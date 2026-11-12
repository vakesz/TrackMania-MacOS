"""Helpers for building disposable-prefix WinHTTP TLS fixtures.

Prefixes and HOME directories are always owned scratch directories, never a
caller prefix, and no root is installed in macOS Keychain.
"""

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/tls"
ARCHITECTURES = ("i686", "x86_64")
REQUEST_MODES = ("sync", "async")
TLS_SERVER = runpy.run_path(str(ROOT / "src/python/tmnf/probes/tls_test_server.py"))
TLS_CASES = TLS_SERVER["CASES"]


def local_probe_arguments(mode, control_url, case_url, case):
    if mode not in REQUEST_MODES:
        raise ValueError("unknown WinHTTP request mode: " + mode)
    return (["--async"] if mode == "async" else []) + ["--local", control_url, case_url, case]


def fixture_environment(runtime, home, prefix, nonce, winhttp_overlay=False):
    # Construct, do not filter, the environment. In particular no inherited Wine
    # overrides, prefix, Rosetta hook, DYLD settings, proxies, or real HOME.
    return {
        "PATH": str(runtime / "bin") + ":/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "USER": "mactmnf-tls-test",
        "LOGNAME": "mactmnf-tls-test",
        "WINEPREFIX": str(prefix),
        "WINESERVER": str(runtime / "bin/wineserver"),
        "WINEARCH": "win64",
        "WINEDEBUG": "-all,warn+winhttp",
        "WINEDLLOVERRIDES": "mscoree,mshtml=" + (";winhttp=n" if winhttp_overlay else ""),
        "MACTMNF_DISPOSABLE_TEST": nonce,
    }


def copy_winhttp_overlay(overlay, appdir, arch):
    """Read the isolated builder's native output; write only a new app-local copy."""
    directory, machine = {"i686": ("i386-windows", 0x14C), "x86_64": ("x86_64-windows", 0x8664)}[
        arch
    ]
    source = overlay / directory / "winhttp.dll"
    manifest = json.loads((overlay.parent / "build.json").read_text())
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (
        manifest.get("test_only") is not True
        or manifest["artifacts"]["native/" + directory + "/winhttp.dll"] != digest
    ):
        raise ValueError("test-only WinHTTP overlay manifest/hash mismatch: " + str(source))
    # Minimal PE header checks; no PE parser.
    offset = int.from_bytes(data[60:64], "little")
    if (
        len(data) < 64
        or data[:2] != b"MZ"
        or offset < 64
        or data[offset : offset + 4] != b"PE\0\0"
        or int.from_bytes(data[offset + 4 : offset + 6], "little") != machine
        or data[64:96].startswith(b"Wine builtin DLL")
    ):
        raise ValueError("expected matching-architecture native WinHTTP DLL: " + str(source))
    with (appdir / "winhttp.dll").open("xb") as destination:
        destination.write(data)
    print(
        "TLS overlay %s: source=%s sha256=%s; recorded Wine revision=%s patches=%s; test-only"
        % (
            arch,
            source,
            digest,
            manifest["revision"],
            manifest.get("patches", manifest.get("patch_sha256")),
        ),
        flush=True,
    )


def assert_overlay_module(output, appdir):
    paths = [
        line[len("INFO: winhttp module=") :]
        for line in output.splitlines()
        if line.startswith("INFO: winhttp module=")
    ]
    suffix = (appdir.parent.name.lower(), appdir.name.lower(), "winhttp.dll")
    if len(paths) != 1:
        raise AssertionError("missing or ambiguous WinHTTP module proof:\n" + output)
    path = PureWindowsPath(paths[0])
    if (
        not path.is_absolute()
        or ".." in path.parts
        or tuple(part.lower() for part in path.parts[-3:]) != suffix
    ):
        raise AssertionError(
            "WinHTTP overlay was not loaded from the owned application directory:\n" + output
        )


def run(command, env, timeout=60):
    # Files avoid inherited pipes keeping communicate() alive after a Wine child
    # exits. Detached processes are stopped by prefix-scoped wineserver cleanup.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", dir=env["HOME"]) as output:
        try:
            result = subprocess.run(
                list(map(str, command)),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                start_new_session=True,
                cwd=env["HOME"],
            )
        except subprocess.TimeoutExpired as error:
            output.seek(0)
            raise AssertionError(
                "command exceeded %ss: %s\n%s" % (timeout, command, output.read())
            ) from error
        output.seek(0)
        return result.returncode, output.read()


def check_owned_prefix(root, prefix, identity):
    if (
        root.resolve() != root
        or prefix.parent != root
        or prefix.resolve() != prefix
        or prefix.name not in {"prefix-" + arch for arch in ARCHITECTURES}
        or (prefix.stat().st_dev, prefix.stat().st_ino) != identity
    ):
        raise AssertionError("refusing to operate on unowned prefix: " + str(prefix))


def cleanup_prefixes(runtime, scratch, root_identity, owned):
    errors = []
    if (
        scratch.resolve() != scratch
        or (scratch.stat().st_dev, scratch.stat().st_ino) != root_identity
    ):
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
        raise AssertionError(
            "cleanup failed; scratch retained at %s: %s" % (scratch, "; ".join(errors))
        )
    shutil.rmtree(scratch)
    print("TLS cleanup: owned wineservers stopped; scratch removed", flush=True)


class LegacyTLS10Account(unittest.TestCase):
    """Account creation in TrackMania Forever needs WinINet to speak TLS 1.0.

    Opt in with MACTMNF_TEST_RUNTIME=/absolute/path/to/runtime. The probe runs
    in a disposable prefix and never touches the game prefix.
    """

    @unittest.skipUnless(os.environ.get("MACTMNF_TEST_RUNTIME"), "MACTMNF_TEST_RUNTIME is not set")
    def test_wininet_request_over_tls10_with_game_retry(self):
        runtime = Path(os.environ["MACTMNF_TEST_RUNTIME"]).resolve()
        compiler = shutil.which("i686-w64-mingw32-gcc")
        if compiler is None:
            self.skipTest("i686-w64-mingw32-gcc not found (brew install mingw-w64)")
        try:
            server = TLS_SERVER["serve_legacy_tls10"]()
            url, handshakes = server.__enter__()
        except TLS_SERVER["TLS10Unavailable"] as error:
            self.skipTest(str(error))
        scratch = Path(tempfile.mkdtemp(prefix="mactmnf-tls10-")).resolve()
        owned = []
        try:
            prefix, home = scratch / "prefix-i686", scratch / "home-i686"
            (home / "tmp").mkdir(parents=True)
            prefix.mkdir()
            env = fixture_environment(runtime, home, prefix, "a" * 64)
            env["WINEDEBUG"] = "-all"
            owned.append((prefix, (prefix.stat().st_dev, prefix.stat().st_ino), env))
            probe = scratch / "inet_legacy.exe"
            subprocess.run(
                [compiler, "-O2", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/inet_legacy.c")]
                + ["-o", str(probe), "-lwininet"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            code, output = run([runtime / "bin/wine", "wineboot", "--init"], env, timeout=180)
            self.assertEqual(code, 0, output)
            port = url.rsplit(":", 1)[1].rstrip("/")
            code, output = run([runtime / "bin/wine", probe, "127.0.0.1", port], env, timeout=60)
            self.assertEqual(code, 0, output)
            self.assertIn("HTTP 200", output)
            # The self-signed fixture must go through the game's retry path.
            self.assertIn("attempt 0 error 12045", output)
            self.assertTrue(handshakes, "server saw no completed TLS handshake")
            for version, cipher in handshakes:
                self.assertEqual(version, "TLSv1", handshakes)
                self.assertIn(cipher, TLS_SERVER["LEGACY_TLS10_CIPHERS"].split(":"))
        finally:
            server.__exit__(None, None, None)
            if owned:
                root_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
                cleanup_prefixes(runtime, scratch, root_identity, owned)
            else:
                shutil.rmtree(scratch)


if __name__ == "__main__":
    unittest.main()
