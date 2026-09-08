"""Opt-in WinHTTP TLS tests, with an optional app-local test DLL overlay.

Every run creates its own prefixes and HOME directories, never uses a caller
prefix, and never installs a root in macOS Keychain. No CDN requests are made.
"""

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import runpy
import select
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/tls"
ARCHITECTURES = ("i686", "x86_64")
REQUEST_MODES = ("sync", "async")
TLS_CASES = runpy.run_path(str(ROOT / "tools/tls-test-server.py"))["CASES"]


def local_probe_arguments(mode, control_url, case_url, case):
    if mode not in REQUEST_MODES:
        raise ValueError("unknown WinHTTP request mode: " + mode)
    return (["--async"] if mode == "async" else []) + ["--local", control_url, case_url, case]


def fixture_environment(runtime, home, prefix, nonce, winhttp_overlay=False):
    # Construct, do not filter, the environment. In particular no inherited Wine
    # overrides, prefix, Rosetta hook, DYLD settings, proxies, or real HOME.
    return {"PATH": str(runtime / "bin") + ":/usr/bin:/bin", "HOME": str(home),
            "TMPDIR": str(home / "tmp"), "USER": "mactmnf-tls-test", "LOGNAME": "mactmnf-tls-test",
            "WINEPREFIX": str(prefix), "WINESERVER": str(runtime / "bin/wineserver"),
            "WINEARCH": "win64", "WINEDEBUG": "-all,warn+winhttp",
            "WINEDLLOVERRIDES": "mscoree,mshtml=" + (";winhttp=n" if winhttp_overlay else ""),
            "MACTMNF_DISPOSABLE_TEST": nonce}


def copy_winhttp_overlay(overlay, appdir, arch):
    """Read the isolated builder's native output; write only a new app-local copy."""
    directory, machine = {"i686": ("i386-windows", 0x14c), "x86_64": ("x86_64-windows", 0x8664)}[arch]
    source = overlay / directory / "winhttp.dll"
    manifest = json.loads((overlay.parent / "build.json").read_text())
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (manifest.get("test_only") is not True or
            manifest["artifacts"]["native/" + directory + "/winhttp.dll"] != digest):
        raise ValueError("test-only WinHTTP overlay manifest/hash mismatch: " + str(source))
    # Same small header checks as test_winhttp_build.py; no new PE parser.
    offset = int.from_bytes(data[60:64], "little")
    if (len(data) < 64 or data[:2] != b"MZ" or offset < 64 or
            data[offset:offset + 4] != b"PE\0\0" or
            int.from_bytes(data[offset + 4:offset + 6], "little") != machine or
            data[64:96].startswith(b"Wine builtin DLL")):
        raise ValueError("expected matching-architecture native WinHTTP DLL: " + str(source))
    with (appdir / "winhttp.dll").open("xb") as destination:
        destination.write(data)
    print("TLS overlay %s: source=%s sha256=%s; recorded Wine revision=%s patches=%s; test-only" %
          (arch, source, digest, manifest["revision"], manifest.get("patches", manifest.get("patch_sha256"))), flush=True)


def assert_overlay_module(output, appdir):
    paths = [line[len("INFO: winhttp module="):] for line in output.splitlines()
             if line.startswith("INFO: winhttp module=")]
    suffix = (appdir.parent.name.lower(), appdir.name.lower(), "winhttp.dll")
    if len(paths) != 1:
        raise AssertionError("missing or ambiguous WinHTTP module proof:\n" + output)
    path = PureWindowsPath(paths[0])
    if (not path.is_absolute() or ".." in path.parts or
            tuple(part.lower() for part in path.parts[-3:]) != suffix):
        raise AssertionError("WinHTTP overlay was not loaded from the owned application directory:\n" + output)


def run(command, env, timeout=60):
    # Files avoid inherited pipes keeping communicate() alive after a Wine child
    # exits. Detached processes are stopped by prefix-scoped wineserver cleanup.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", dir=env["HOME"]) as output:
        try:
            result = subprocess.run(list(map(str, command)), env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
                                    start_new_session=True, cwd=env["HOME"])
        except subprocess.TimeoutExpired as error:
            output.seek(0)
            raise AssertionError("command exceeded %ss: %s\n%s" %
                                 (timeout, command, output.read())) from error
        output.seek(0)
        return result.returncode, output.read()


def check_owned_prefix(root, prefix, identity):
    if (root.resolve() != root or prefix.parent != root or prefix.resolve() != prefix or
            prefix.name not in {"prefix-" + arch for arch in ARCHITECTURES} or
            (prefix.stat().st_dev, prefix.stat().st_ino) != identity):
        raise AssertionError("refusing to operate on unowned prefix: " + str(prefix))


def cleanup_prefixes(runtime, scratch, root_identity, owned):
    errors = []
    if scratch.resolve() != scratch or (scratch.stat().st_dev, scratch.stat().st_ino) != root_identity:
        raise AssertionError("scratch directory identity changed; refusing cleanup")
    for prefix, identity, env in owned:
        try:
            check_owned_prefix(scratch, prefix, identity)
            if env["WINEPREFIX"] != str(prefix) or Path(env["HOME"]).parent != scratch:
                raise AssertionError("cleanup environment does not belong to scratch")
        except (OSError, AssertionError) as error:
            errors.append(str(error))
            continue
        for option in ("-k", "-w"):
            try:
                code, output = run([runtime / "bin/wineserver", option], env, timeout=15)
                # -k returns 1 when no server is running. Always require -w to
                # succeed, even if -k failed, before considering deletion.
                if code not in ((0, 1) if option == "-k" else (0,)):
                    raise AssertionError("wineserver %s exited %s: %s" % (option, code, output))
            except (OSError, AssertionError) as error:
                errors.append(str(error))
    if errors:
        raise AssertionError("cleanup failed; scratch retained at %s: %s" % (scratch, "; ".join(errors)))
    shutil.rmtree(scratch)
    print("TLS cleanup: owned wineservers stopped; scratch removed", flush=True)


@unittest.skipUnless(os.environ.get("MACTMNF_TEST_RUNTIME"),
                     "set MACTMNF_TEST_RUNTIME to opt into disposable-prefix Wine TLS tests")
class NetworkRuntime(unittest.TestCase):
    def test_termination_after_import_cleans_up(self):
        environment = {"MACTMNF_TEST_RUNTIME": os.environ["MACTMNF_TEST_RUNTIME"],
                       "PATH": os.environ.get("PATH", os.defpath)}
        if os.environ.get("MACTMNF_TEST_WINHTTP_DLLS"):
            environment["MACTMNF_TEST_WINHTTP_DLLS"] = os.environ["MACTMNF_TEST_WINHTTP_DLLS"]
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "NetworkRuntime.test_winhttp_rejections_in_owned_prefixes"],
            env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        output = bytearray()
        deadline = time.monotonic() + 150
        try:
            while b"pinned test CA imported into disposable CurrentUser ROOT" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, output.decode(errors="replace"))
                self.assertTrue(select.select([process.stdout], [], [], remaining)[0], "import readiness timed out")
                chunk = os.read(process.stdout.fileno(), 4096)
                self.assertTrue(chunk, output.decode(errors="replace"))
                output.extend(chunk)
            process.send_signal(signal.SIGTERM)
            remaining, _ = process.communicate(timeout=45)
            output.extend(remaining)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b"TLS cleanup: owned wineservers stopped; scratch removed", output)
            print("SIGTERM after actual CA import: owned prefix cleaned up", flush=True)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_winhttp_rejections_in_owned_prefixes(self):
        runtime = Path(os.environ["MACTMNF_TEST_RUNTIME"]).resolve(strict=True)
        for name in ("wine", "wineserver"):
            self.assertTrue(os.access(runtime / "bin" / name, os.X_OK), "runtime missing " + name)
        compilers = {arch: shutil.which(arch + "-w64-mingw32-gcc") for arch in ARCHITECTURES}
        self.assertTrue(all(compilers.values()), "both mingw-w64 compilers are required")
        version = (runtime / ".mactmnf-runtime").read_text().strip()
        print("\nTLS runtime: %s, version %s; not qualification of current source" % (runtime, version), flush=True)
        overlay = os.environ.get("MACTMNF_TEST_WINHTTP_DLLS")
        overlay = Path(overlay).resolve(strict=True) if overlay else None
        fault_overlay = os.environ.get("MACTMNF_TEST_WINHTTP_FAULT_DLLS")
        fault_overlay = Path(fault_overlay).resolve(strict=True) if fault_overlay else None
        fault_only = os.environ.get("MACTMNF_TEST_WINHTTP_FAULT_ONLY") == "1"
        self.assertFalse(fault_only and fault_overlay is None, "fault-only requires a fault overlay")
        if fault_only:
            print("TLS fault-only run: importer controls and lookup cleanup; certificate/pool matrix omitted", flush=True)
        if fault_overlay:
            self.assertIs(json.loads((fault_overlay.parent / "build.json").read_text()).get("fault_injection"), True)

        # Ignore caller TMPDIR as well as caller prefix and installation settings.
        scratch = Path(tempfile.mkdtemp(prefix="mactmnf-tls-runtime-", dir="/tmp")).resolve()
        root_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
        owned = []
        server = None

        def interrupt(signum, frame):
            raise KeyboardInterrupt("TLS test interrupted by signal %d" % signum)

        previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            for arch in ARCHITECTURES:
                appdir = scratch / ("bin-" + arch)
                appdir.mkdir()
                if overlay is not None:
                    copy_winhttp_overlay(overlay, appdir, arch)
                for source, library in (("network", "winhttp"), ("tls-trust", "crypt32")):
                    command = [compilers[arch], "-O2", "-Wall", "-Wextra", "-Werror",
                               ROOT / ("tests/" + source + ".c"), "-o", appdir / (source + ".exe"),
                               "-l" + library]
                    code, output = run(command, {"PATH": "/usr/bin:/bin", "HOME": str(scratch)})
                    self.assertEqual(code, 0, output)

                if fault_overlay:
                    fault_appdir = scratch / ("fault-bin-" + arch)
                    fault_appdir.mkdir()
                    copy_winhttp_overlay(fault_overlay, fault_appdir, arch)
                    shutil.copyfile(appdir / "network.exe", fault_appdir / "network.exe")

            server = subprocess.Popen([sys.executable, str(ROOT / "tools/tls-test-server.py")],
                                      env={"PATH": "/usr/bin:/bin", "HOME": str(scratch)},
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, start_new_session=True, cwd=scratch)
            self.assertTrue(select.select([server.stdout], [], [], 10)[0], "TLS server readiness timed out")
            urls = json.loads(server.stdout.readline())
            self.assertEqual(set(urls), set(TLS_CASES))
            for url in urls.values():
                parsed = urlsplit(url)
                self.assertEqual((parsed.scheme, parsed.hostname, parsed.path), ("https", "127.0.0.1", "/"))
                self.assertIsNone(parsed.username)
                self.assertFalse(parsed.query or parsed.fragment)
                self.assertTrue(parsed.port and 0 < parsed.port < 65536)
            self.assertEqual(len(set(urls.values())), len(TLS_CASES))
            pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / "ca.pem").read_text())).hexdigest()

            for arch in ARCHITECTURES:
                with self.subTest(architecture=arch):
                    prefix = scratch / ("prefix-" + arch)
                    prefix.mkdir(mode=0o700)
                    identity = (prefix.stat().st_dev, prefix.stat().st_ino)
                    home = scratch / ("home-" + arch)
                    (home / "tmp").mkdir(parents=True)
                    nonce = secrets.token_hex(32)
                    env = fixture_environment(runtime, home, prefix, nonce, winhttp_overlay=overlay is not None)
                    owned.append((prefix, identity, env))
                    appdir = scratch / ("bin-" + arch)
                    trust_exe = appdir / "tls-trust.exe"
                    network_exe = appdir / "network.exe"

                    def wine(*args, environment=None):
                        check_owned_prefix(scratch, prefix, identity)
                        child_env = env if environment is None else environment
                        self.assertEqual(child_env["WINEPREFIX"], str(prefix))
                        self.assertEqual(child_env["HOME"], str(home))
                        code, output = run([runtime / "bin/wine", *args], child_env)
                        if overlay is not None and args[0] == network_exe:
                            assert_overlay_module(output, appdir)
                        if fault_overlay and args[0] == scratch / ("fault-bin-" + arch) / "network.exe":
                            assert_overlay_module(output, args[0].parent)
                        return code, output

                    code, output = wine("wineboot.exe", "--init")
                    self.assertEqual(code, 0, "wineboot failed: " + output)
                    code, output = run([runtime / "bin/wineserver", "-w"], env)
                    self.assertEqual(code, 0, "wineboot server did not finish: " + output)
                    self.assertTrue((prefix / "system.reg").is_file())
                    drive = prefix / "drive_c"
                    self.assertEqual(drive.resolve(), drive)
                    self.assertEqual((prefix / "dosdevices/c:").resolve(), drive)
                    marker = drive / "mactmnf-test-prefix.txt"
                    self.assertFalse(marker.exists())
                    for name in ("ca.pem", "untrusted-ca.pem", "valid.pem"):
                        shutil.copyfile(FIXTURES / name, drive / name)
                    (drive / "oversized.pem").write_bytes(b"X" * 65537)
                    def absent_root():
                        for mode in REQUEST_MODES:
                            code, output = wine(network_exe, *local_probe_arguments(mode, urls["valid"], urls["valid"], "valid"))
                            self.assertEqual(code, 1, output)
                            self.assertIn("trusted positive control failed; rejection case was not run", output)
                            # Only paired post-import success makes this an absent-root
                            # control. This baseline is not rejection conformance.
                            self.assertRegex(output, r"status=0 error=(12045|12157|12175) ")
                            if overlay is not None:
                                self.assertIn("status=0 error=12175 secure_flags=0x00000008 expected=0x00000000 callbacks=1", output)
                            print("%s %s missing-root control: %s" % (arch, mode, output.strip()), flush=True)

                    absent_root()
                    for proof in ("missing-marker", "wrong-marker", "missing-env", "wrong-env", "short-nonce", "nonhex-nonce"):
                        with self.subTest(architecture=arch, proof=proof):
                            environment = dict(env)
                            argument = nonce
                            if proof != "missing-marker":
                                marker.write_text("0" * 64 if proof == "wrong-marker" else nonce, encoding="ascii")
                            if proof == "missing-env":
                                environment.pop("MACTMNF_DISPOSABLE_TEST")
                            if proof == "wrong-env":
                                environment["MACTMNF_DISPOSABLE_TEST"] = "0" * 64
                            if proof in ("short-nonce", "nonhex-nonce"):
                                argument = "abc" if proof == "short-nonce" else "g" * 64
                                environment["MACTMNF_DISPOSABLE_TEST"] = argument
                                marker.write_text(argument, encoding="ascii")
                            code, output = wine(trust_exe, r"C:\ca.pem", argument, pin, environment=environment)
                            self.assertEqual(code, 2, output)
                            self.assertIn("REFUSE: disposable-prefix proof", output)
                            self.assertNotIn("opening CurrentUser ROOT", output)
                            print("%s importer %s: refused before store open" % (arch, proof), flush=True)

                    marker.write_text(nonce, encoding="ascii")
                    leaf_pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / "valid.pem").read_text())).hexdigest()
                    other_pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / "untrusted-ca.pem").read_text())).hexdigest()
                    for name, digest in (("ca.pem", "0" * 64), ("untrusted-ca.pem", pin),
                                         ("untrusted-ca.pem", other_pin),
                                         ("valid.pem", leaf_pin), ("oversized.pem", pin)):
                        with self.subTest(architecture=arch, certificate=name, pin=digest):
                            code, output = wine(trust_exe, "C:\\" + name, nonce, digest)
                            self.assertEqual(code, 3, output)
                            self.assertIn("REFUSE: pinned test CA", output)
                            self.assertNotIn("opening CurrentUser ROOT", output)

                    absent_root()
                    code, output = wine(trust_exe, r"C:\ca.pem", nonce, pin)
                    self.assertEqual(code, 0, output)
                    self.assertIn("pinned test CA imported into disposable CurrentUser ROOT", output)
                    print("%s importer: %s" % (arch, output.strip()), flush=True)
                    for mode in REQUEST_MODES:
                        with self.subTest(architecture=arch, mode=mode):
                            if fault_overlay:
                                with self.subTest(architecture=arch, mode=mode, fault="certificate-lookup"):
                                    fault_env = dict(env, WINEDLLOVERRIDES="mscoree,mshtml=;winhttp=n")
                                    # With no subscription, this contract checks 12175, not an invalid-CA callback.
                                    code, output = wine(scratch / ("fault-bin-" + arch) / "network.exe",
                                        *local_probe_arguments(mode, urls["valid"], urls["valid"], "untrusted"),
                                        "0", "0", "no-subscription", environment=fault_env)
                                    print("%s %s certificate-lookup fault: %s" % (arch, mode, output.strip()), flush=True)
                                    self.assertEqual(code, 0, output)
                                    self.assertEqual(output.count("FAULT: final certificate lookup returned NULL"), 1, output)
                                    self.assertEqual(output.count("FAULT: rejected connection release=1"), 1, output)
                                    self.assertNotIn("release=2", output)
                                    self.assertIn("ERROR_WINHTTP_SECURE_FAILURE, secure_flags=0x00000000", output)
                            if fault_only:
                                continue
                            code, output = wine(network_exe, *local_probe_arguments(mode, urls["valid"], urls["valid"], "valid"))
                            self.assertEqual(code, 0, "post-import positive control failed: " + output)
                            self.assertEqual(output.count("PASS: local HTTPS returned HTTP 200"), 2, output)
                            if mode == "async":
                                self.assertEqual(output.count("INFO: async callbacks secure=0 send=1 headers=1 errors=0 "
                                                              "closing=1 bad=0 order=0/1/2/3/0/4 async_error=0 api=0"), 2, output)
                            print("%s %s valid: %s" % (arch, mode, output.strip()), flush=True)
                            for case, flag in (("untrusted", "00000008"), ("expired", "00000020"), ("hostname", "00000010")):
                                with self.subTest(architecture=arch, mode=mode, case=case):
                                    code, output = wine(network_exe, *local_probe_arguments(mode, urls["valid"], urls[case], case))
                                    print("%s %s %s: %s" % (arch, mode, case, output.strip()), flush=True)
                                    self.assertEqual(code, 0, output)
                                    self.assertIn("PASS: local HTTPS returned HTTP 200", output)
                                    self.assertIn("ERROR_WINHTTP_SECURE_FAILURE, secure_flags=0x" + flag, output)
                                    if mode == "async":
                                        self.assertIn("INFO: async callbacks secure=1 send=0 headers=0 errors=1 "
                                                      "closing=1 bad=0 order=1/0/0/0/2/3 async_error=12175 api=5", output)
                            # Every subset of date/CA/name ignores, unrelated usage, replacement and reset.
                            for case in TLS_CASES:
                                for flags in (0, 0x100, 0x1000, 0x2000, 0x1100, 0x2100, 0x3000, 0x3100, 0x200):
                                    expected = ("expired" if "expired" in case and not flags & 0x2000 else
                                                "untrusted" if "untrusted" in case and not flags & 0x100 else
                                                "hostname" if "hostname" in case and not flags & 0x1000 else "valid")
                                    with self.subTest(architecture=arch, mode=mode, case=case, flags=hex(flags)):
                                        arguments = local_probe_arguments(mode, urls["valid"], urls[case], expected)
                                        code, output = wine(network_exe, *arguments, "0x3300", hex(flags))
                                        print("%s %s %s flags=%s: %s" % (arch, mode, case, hex(flags), output.strip()), flush=True)
                                        self.assertEqual(code, 0, output)
                            for case, initial, final, expected in (
                                    ("valid", 0, 0x100, "valid"),
                                    ("untrusted", 0x100, 0, "untrusted"),
                                    ("expired-untrusted", 0x2100, 0x2000, "untrusted"),
                                    ("hostname", 0x1000, 0x2000, "hostname")):
                                with self.subTest(architecture=arch, mode=mode, pool=case):
                                    code, output = wine(network_exe,
                                        *local_probe_arguments(mode, urls["valid"], urls[case], expected),
                                        hex(initial), hex(final), "pool")
                                    print("%s %s pool %s: %s" % (arch, mode, case, output.strip()), flush=True)
                                    self.assertEqual(code, 0, output)
                            with self.subTest(architecture=arch, mode=mode, pool="revocation"):
                                code, output = wine(network_exe,
                                    *local_probe_arguments(mode, urls["valid"], urls["valid"], "revocation"),
                                    "0", "0", "pool-revocation")
                                print("%s %s pool revocation: %s" % (arch, mode, output.strip()), flush=True)
                                self.assertEqual(code, 0, output)
                            for case in ("untrusted", "expired", "hostname"):
                                with self.subTest(architecture=arch, mode=mode, no_subscription=case):
                                    code, output = wine(network_exe,
                                        *local_probe_arguments(mode, urls["valid"], urls[case], case),
                                        "0", "0", "no-subscription")
                                    print("%s %s no-subscription %s: %s" % (arch, mode, case, output.strip()), flush=True)
                                    self.assertEqual(code, 0, output)
                                    self.assertIn("ERROR_WINHTTP_SECURE_FAILURE, secure_flags=0x00000000", output)
        finally:
            # A second graceful signal must not interrupt owned-prefix cleanup.
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                try:
                    if server is not None:
                        server.terminate()
                        try:
                            server.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)
                        server.stdout.close()
                finally:
                    cleanup_prefixes(runtime, scratch, root_identity, owned)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


if __name__ == "__main__":
    unittest.main()
