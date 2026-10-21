"""Local TLS fixture checks. No Wine, installed roots, or external endpoints."""

from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import runpy
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src/python/tmnf/probes/tls_test_server.py"
TLS = runpy.run_path(str(SERVER))
FIXTURES = TLS["FIXTURES"]


@contextmanager
def connect(url, context):
    address = urlsplit(url)
    assert address.hostname == "127.0.0.1"
    with socket.create_connection((address.hostname, address.port), timeout=3) as connection:
        with context.wrap_socket(connection, server_hostname=address.hostname) as secured:
            yield secured


def trust(certificate=None):
    # Do not inherit system roots, SSL_CERT_FILE, or SSL_CERT_DIR.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if certificate is not None:
        context.load_verify_locations(cafile=str(certificate))
    return context


class TLSFixtures(unittest.TestCase):
    def test_combined_leaf_metadata_and_signatures(self):
        self.assertEqual(set(TLS["CASES"]), {"valid", "untrusted", "expired", "hostname",
                         "expired-untrusted", "hostname-untrusted", "expired-hostname", "expired-hostname-untrusted"})
        self.assertEqual({path.name for path in FIXTURES.glob("*.key")}, {"server.key"})
        self.assertEqual({path.stem for path in FIXTURES.glob("*.pem")}, {*TLS["CASES"], "ca", "untrusted-ca"})
        for case in TLS["CASES"]:
            with self.subTest(case=case):
                leaf = FIXTURES / (case + ".pem")
                # Metadata is checked separately from OpenSSL's first reported error.
                metadata = ssl._ssl._test_decode_cert(str(leaf))
                self.assertEqual(metadata["notBefore"], "Jan  1 00:00:00 2020 GMT")
                self.assertEqual(metadata["notAfter"], "Jan  1 00:00:00 %s GMT" %
                                 ("2021" if "expired" in case else "2040"))
                self.assertEqual(metadata["subjectAltName"], (("DNS", "wrong-host.invalid"),) if "hostname" in case else
                                 (("IP Address", "127.0.0.1"), ("DNS", "127.0.0.1")))
                ca = "untrusted-ca" if "untrusted" in case else "ca"
                self.assertEqual(metadata["issuer"], ((("commonName", "MacTMNF PUBLIC TEST ONLY " + ca),),))
                result = subprocess.run(["openssl", "verify", "-CAfile", str(FIXTURES / (ca + ".pem")),
                                         "-attime", "1590969600", "-purpose", "sslserver", str(leaf)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                text = subprocess.check_output(["openssl", "x509", "-in", str(leaf), "-text", "-noout"], text=True)
                for extension in ("Authority Information Access", "CRL Distribution Points", "OCSP"):
                    self.assertNotIn(extension, text)

    def test_keepalive_connection_ids_prove_reuse_and_new_connection(self):
        with TLS["serve"]() as urls:
            parsed = urlsplit(urls["valid"])
            client = http.client.HTTPSConnection(parsed.hostname, parsed.port, context=trust(FIXTURES / "ca.pem"), timeout=3)
            try:
                ids = []
                for _ in range(2):
                    client.request("HEAD", "/")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    ids.append(response.getheader("X-Test-Connection"))
                    self.assertEqual(response.read(), b"")
                self.assertIsNotNone(ids[0])
                self.assertEqual(ids[0], ids[1])
                client.close()
                client.request("HEAD", "/")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertNotEqual(response.getheader("X-Test-Connection"), ids[0])
                response.read()
            finally:
                client.close()

    def test_independent_rejections_with_valid_chain_control(self):
        context = trust(FIXTURES / "ca.pem")
        self.assertEqual(context.cert_store_stats()["x509_ca"], 1)
        with TLS["serve"]() as urls:
            # The trusted control must work before any negative counts as evidence.
            with connect(urls["valid"], context) as connection:
                self.assertEqual(connection.getpeercert()["subjectAltName"],
                                 (("IP Address", "127.0.0.1"), ("DNS", "127.0.0.1")))
                connection.sendall(b"HEAD / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                self.assertIn(b" 200 ", connection.recv(4096).split(b"\r\n")[0])
            for case, code in (("untrusted", 20), ("expired", 10), ("hostname", 64)):
                with self.subTest(case=case):
                    with self.assertRaises(ssl.SSLCertVerificationError) as caught:
                        with connect(urls[case], context):
                            self.fail("invalid certificate accepted")
                    self.assertEqual(caught.exception.verify_code, code, caught.exception.verify_message)

    def test_absent_root_breaks_control_and_untrusted_leaf_is_otherwise_valid(self):
        with TLS["serve"]() as urls:
            with self.assertRaises(ssl.SSLCertVerificationError) as caught:
                with connect(urls["valid"], trust()):
                    self.fail("control must fail without the private test CA")
            self.assertEqual(caught.exception.verify_code, 20)
            with connect(urls["untrusted"], trust(FIXTURES / "untrusted-ca.pem")) as connection:
                connection.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
                self.assertIn(b" 200 ", connection.recv(4096).split(b"\r\n")[0])

    def test_loopback_only_and_ports_closed_on_normal_or_exceptional_exit(self):
        for fail in (False, True):
            with self.subTest(exception=fail):
                try:
                    with TLS["serve"]() as urls:
                        self.assertEqual(set(urls), set(TLS["CASES"]))
                        self.assertEqual(len({urlsplit(url).port for url in urls.values()}), len(TLS["CASES"]))
                        self.assertTrue(all(urlsplit(url).hostname == "127.0.0.1" for url in urls.values()))
                        if fail:
                            raise RuntimeError("test interruption")
                except RuntimeError:
                    if not fail:
                        raise
                self.assert_ports_closed(urls)

    def assert_ports_closed(self, urls):
        for url in urls.values():
            with self.assertRaises(ConnectionRefusedError):
                with socket.create_connection(("127.0.0.1", urlsplit(url).port), timeout=1):
                    self.fail("server still listening")

    def test_partial_startup_failure_closes_existing_server(self):
        servers = []
        server_class = TLS["Server"]

        def record(*args):
            server = server_class(*args)
            servers.append(server)
            return server

        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory)
            for name in ("server.key", "valid.pem"):
                shutil.copyfile(FIXTURES / name, fixtures / name)
            with mock.patch.dict(TLS["serve"].__wrapped__.__globals__, Server=record):
                with self.assertRaises(FileNotFoundError):
                    with TLS["serve"](fixtures):
                        self.fail("missing fixture accepted")
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].socket.fileno(), -1)

    def test_server_subprocess_readiness_and_signal_cleanup(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                process = subprocess.Popen([sys.executable, str(SERVER)], text=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    self.assertTrue(select.select([process.stdout], [], [], 5)[0], "no readiness line")
                    urls = json.loads(process.stdout.readline())
                    with connect(urls["valid"], trust(FIXTURES / "ca.pem")):
                        pass
                    process.send_signal(sig)
                    output, error = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, error)
                    self.assertEqual(output, "")
                    self.assert_ports_closed(urls)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)


class NetworkProbe(unittest.TestCase):
    def test_local_probe_mode_arguments_preserve_urls_and_expectations(self):
        runtime = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))
        self.assertEqual(runtime["REQUEST_MODES"], ("sync", "async"))
        arguments = runtime["local_probe_arguments"]
        control, case_url = "https://127.0.0.1:12345/", "https://127.0.0.1:12346/"
        for case in ("valid", "untrusted", "expired", "hostname"):
            with self.subTest(case=case):
                sync = arguments("sync", control, case_url, case)
                self.assertEqual(sync, ["--local", control, case_url, case])
                self.assertEqual(arguments("async", control, case_url, case), ["--async", *sync])
        with self.assertRaisesRegex(ValueError, "unknown WinHTTP request mode"):
            arguments("invalid", control, case_url, "valid")

    def test_both_pe_architectures_compile_without_running_wine(self):
        with tempfile.TemporaryDirectory() as directory:
            for architecture in ("i686", "x86_64"):
                compiler = shutil.which(architecture + "-w64-mingw32-gcc")
                if compiler is None:
                    self.skipTest("mingw-w64 not installed; C compilation not checked")
                for source, library in (("network", "winhttp"), ("tls-trust", "crypt32")):
                    with self.subTest(architecture=architecture, source=source):
                        result = subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror",
                                                 str(ROOT / ("tests/" + source + ".c")), "-o",
                                                 str(Path(directory) / (architecture + "-" + source + ".exe")),
                                                 "-l" + library], capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_environment_does_not_inherit_caller_settings(self):
        runtime = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))
        with mock.patch.dict(os.environ, {"WINEPREFIX": "/real-prefix", "HOME": "/real-home",
                                          "MACTMNF_PREFIX": "/game", "ROSETTA_X87_PATH": "/hook",
                                          "DYLD_LIBRARY_PATH": "/libraries", "https_proxy": "https://external"}):
            env = runtime["fixture_environment"](Path("/runtime"), Path("/scratch/home"),
                                                   Path("/scratch/prefix"), "a" * 64)
        self.assertEqual(env["WINEPREFIX"], "/scratch/prefix")
        self.assertEqual(env["HOME"], "/scratch/home")
        self.assertEqual(env["WINESERVER"], "/runtime/bin/wineserver")
        self.assertEqual(env["WINEDLLOVERRIDES"], "mscoree,mshtml=")
        for name in ("MACTMNF_PREFIX", "ROSETTA_X87_PATH", "DYLD_LIBRARY_PATH", "https_proxy"):
            self.assertNotIn(name, env)
        overlay_env = runtime["fixture_environment"](Path("/runtime"), Path("/scratch/home"),
                                                       Path("/scratch/prefix"), "a" * 64, winhttp_overlay=True)
        self.assertEqual(overlay_env["WINEDLLOVERRIDES"], "mscoree,mshtml=;winhttp=n")
        self.assertEqual(dict(overlay_env, WINEDLLOVERRIDES=env["WINEDLLOVERRIDES"]), env)

    def test_overlay_copy_checks_hash_architecture_and_native_marker(self):
        copy = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))["copy_winhttp_overlay"]
        for arch, directory, machine in (("i686", "i386-windows", 0x14c), ("x86_64", "x86_64-windows", 0x8664)):
            for failure in (None, "hash", "architecture", "builtin", "signature", "truncated"):
                with self.subTest(architecture=arch, failure=failure), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    source = root / "native" / directory / "winhttp.dll"
                    source.parent.mkdir(parents=True)
                    appdir = root / ("bin-" + arch)
                    appdir.mkdir()
                    data = bytearray(160)
                    data[:2] = b"MZ"
                    data[60:64] = (128).to_bytes(4, "little")
                    data[128:132] = b"PE\0\0"
                    data[132:134] = (0 if failure == "architecture" else machine).to_bytes(2, "little")
                    if failure == "builtin":
                        data[64:80] = b"Wine builtin DLL"
                    if failure == "signature":
                        data[128] = 0
                    if failure == "truncated":
                        data = data[:63]
                    source.write_bytes(data)
                    source.chmod(0o444)
                    digest = hashlib.sha256(data).hexdigest()
                    manifest = {"test_only": True, "revision": "a" * 40, "patch_sha256": "b" * 64,
                                "artifacts": {"native/" + directory + "/winhttp.dll":
                                              "0" * 64 if failure == "hash" else digest}}
                    (root / "build.json").write_text(json.dumps(manifest))
                    if failure is None:
                        copy(root / "native", appdir, arch)
                        self.assertEqual((appdir / "winhttp.dll").read_bytes(), data)
                        with self.assertRaises(FileExistsError):
                            copy(root / "native", appdir, arch)
                    else:
                        with self.assertRaises(ValueError):
                            copy(root / "native", appdir, arch)
                        self.assertFalse((appdir / "winhttp.dll").exists())
                    self.assertEqual(source.read_bytes(), data)
                    self.assertEqual(source.stat().st_mode & 0o777, 0o444)

    def test_overlay_module_proof_rejects_missing_builtin_and_wrong_appdir(self):
        check = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))["assert_overlay_module"]
        appdir = Path("/private/tmp/mactmnf-tls-runtime-owned/bin-i686")
        good = "INFO: winhttp module=Z:\\private\\tmp\\mactmnf-tls-runtime-owned\\bin-i686\\winhttp.dll\n"
        check(good, appdir)
        check(good.replace("winhttp.dll", "WINHTTP.DLL"), appdir)
        for output in ("", good + good, good.replace("bin-i686", "bin-x86_64"),
                       good.replace("runtime-owned", "runtime-other"), good.replace("Z:\\", ""),
                       "INFO: winhttp module=C:\\windows\\system32\\winhttp.dll\n"):
            with self.subTest(output=output), self.assertRaises(AssertionError):
                check(output, appdir)

    def test_runtime_prefix_ownership_checks(self):
        check = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))["check_owned_prefix"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prefix = root / "prefix-i686"
            prefix.mkdir()
            identity = (prefix.stat().st_dev, prefix.stat().st_ino)
            check(root, prefix, identity)
            for parent, path, owner in ((root, prefix, (0, 0)), (root.parent, prefix, identity),
                                         (root, root / "game-prefix", identity)):
                with self.assertRaises(AssertionError):
                    check(parent, path, owner)
            moved = root / "moved"
            prefix.rename(moved)
            prefix.symlink_to(moved, target_is_directory=True)
            with self.assertRaises(AssertionError):
                check(root, prefix, identity)

    def test_runtime_cleanup_is_prefix_scoped_and_retains_failed_cleanup(self):
        runtime = runpy.run_path(str(ROOT / "tests/test_network_runtime.py"))
        cleanup = runtime["cleanup_prefixes"]
        for failure in (None, "wait", "ownership", "kill"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                prefix = root / "prefix-i686"
                prefix.mkdir()
                home = root / "home-i686"
                home.mkdir()
                env = runtime["fixture_environment"](Path("/runtime"), home, prefix, "a" * 64)
                identity = (prefix.stat().st_dev, prefix.stat().st_ino)
                owned = [(prefix, (0, 0) if failure == "ownership" else identity, env)]
                root_identity = (root.stat().st_dev, root.stat().st_ino)
                with mock.patch.dict(cleanup.__globals__, run=mock.Mock()) as globals_:
                    run = globals_["run"]
                    run.side_effect = ([AssertionError("kill timed out"), (0, "")] if failure == "kill" else
                                       [(1, ""), (1 if failure == "wait" else 0, "")])
                    if failure is None:
                        cleanup(Path("/runtime"), root, root_identity, owned)
                        self.assertFalse(root.exists())
                    else:
                        with self.assertRaisesRegex(AssertionError, "scratch retained"):
                            cleanup(Path("/runtime"), root, root_identity, owned)
                        self.assertTrue(prefix.is_dir())
                    self.assertEqual(run.call_count, 0 if failure == "ownership" else 2)
                    if run.called:
                        self.assertEqual(run.call_args_list,
                                         [mock.call([Path("/runtime/bin/wineserver"), option], env, timeout=15)
                                          for option in ("-k", "-w")])

    def test_smoke_forwards_local_arguments_without_trust_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("src/shell/tools/probes", "src/shell/lib", "src/python/tmnf/probes", "bin", "prefix"):
                (root / name).mkdir(parents=True)
            shutil.copyfile(ROOT / "src/shell/tools/probes/smoke-network.sh", root / "src/shell/tools/probes/smoke-network.sh")
            (root / "prefix/system.reg").touch()
            (root / "src/shell/lib/common.sh").write_text(
                'have() { command -v "$1" >/dev/null; }\n'
                'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                'wine_binary() { printf "%s\\n" "$FAKE_WINE"; }\n'
                'configure_translation() { :; }\n'
                'tmnf_python() { PYTHONPATH="' + str(root / 'src/python') + '" python3 "$@"; }\n',
                encoding="ascii")
            stub = root / "bin/stub"
            stub.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, sys\n"
                "with open(os.environ['CALLS'], 'a') as log:\n"
                "    log.write(json.dumps([os.path.basename(sys.argv[0]), sys.argv[1:], "
                "os.getenv('WINEPREFIX')]) + '\\n')\n", encoding="utf-8")
            stub.chmod(0o755)
            for name in ("wine", "i686-w64-mingw32-gcc", "x86_64-w64-mingw32-gcc"):
                (root / "bin" / name).symlink_to(stub)
            # Stub the process runner, not Wine itself in a real prefix.
            (root / "src/python/tmnf/probes/probe_runner.py").write_text(
                "import subprocess, sys\nraise SystemExit(subprocess.call(sys.argv[2:]))\n", encoding="ascii")
            (root / "src/python/tmnf/__init__.py").write_text("", encoding="ascii")
            (root / "src/python/tmnf/probes/__init__.py").write_text("", encoding="ascii")
            (root / "bin/python3").symlink_to(sys.executable)
            log = root / "calls.jsonl"
            env = {"PATH": str(root / "bin") + ":/usr/bin:/bin", "HOME": directory,
                   "TMPDIR": directory, "CALLS": str(log), "FAKE_WINE": str(root / "bin/wine"),
                   "TMNF_PREFIX": str(root / "prefix"), "WINEPREFIX": "/must-not-use"}
            local = ["--local", "https://127.0.0.1:12345/", "https://127.0.0.1:12346/", "expired"]
            for args in ([], ["--async"], local, ["--async", *local]):
                with self.subTest(args=args):
                    log.write_text("")
                    result = subprocess.run(["/bin/bash", str(root / "src/shell/tools/probes/smoke-network.sh"), *args],
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual([call[0] for call in calls], ["i686-w64-mingw32-gcc", "wine",
                                                                 "x86_64-w64-mingw32-gcc", "wine"])
                    for _, argv, prefix in (call for call in calls if call[0] == "wine"):
                        self.assertEqual(argv[1:], args)
                        self.assertEqual(prefix, str(root / "prefix"))
                    self.assertEqual(list((root / "prefix").iterdir()), [root / "prefix/system.reg"])
                    self.assertEqual(list(root.glob("mactmnf-network.*")), [])

    def test_invalid_arguments_fail_before_any_runtime_dependency(self):
        good = "https://127.0.0.1:12345/"
        cases = [["--unknown"], ["--local", good, good, "invalid"]]
        for bad in ("https://example.com/", "http://127.0.0.1:12345/", "https://localhost:12345/",
                    good + "?query", good + "#fragment", "https://user@127.0.0.1:12345/"):
            cases.extend((["--local", bad, good, "expired"], ["--local", good, bad, "expired"]))
        cases += [["--async", *args] for args in cases]
        with tempfile.TemporaryDirectory() as directory:
            for args in cases:
                with self.subTest(args=args):
                    result = subprocess.run(["/bin/bash", str(ROOT / "src/shell/tools/probes/smoke-network.sh"), *args],
                                            env={"PATH": directory, "HOME": directory},
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn("Missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
