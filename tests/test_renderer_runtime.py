"""Opt-in, 32-bit D9VK 2D/volume and 16-bit RT readback GPU regression.

May briefly show a small test window.

MACTMNF_TEST_RUNTIME selects the read-only runtime. MACTMNF_TEST_D9VK_DLL
optionally selects a candidate DLL instead of runtime/lib/d9vk/d3d9.dll.
Run each selection separately; baseline failures are not expectedFailure/skip.
No game, downloads, caller prefix, user HOME, or x87 sidecar is used.
"""

import hashlib
import json
import os
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path, PureWindowsPath

from test_network_runtime import check_owned_prefix, cleanup_prefixes, run

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("MACTMNF_TEST_RUNTIME"),
                     "set MACTMNF_TEST_RUNTIME to opt into disposable-prefix D9VK GPU tests")
class RendererRuntime(unittest.TestCase):
    def test_managed_texture_formats(self):
        runtime = Path(os.environ["MACTMNF_TEST_RUNTIME"]).resolve(strict=True)
        for name in ("wine", "wineserver"):
            self.assertTrue(os.access(runtime / "bin" / name, os.X_OK), "runtime missing " + name)
        compiler = shutil.which("i686-w64-mingw32-gcc")
        self.assertIsNotNone(compiler, "i686 mingw-w64 compiler required")
        source = Path(os.environ.get("MACTMNF_TEST_D9VK_DLL", runtime / "lib/d9vk/d3d9.dll")).resolve(strict=True)
        data = source.read_bytes()
        offset = int.from_bytes(data[60:64], "little")
        self.assertTrue(len(data) >= 64 and data[:2] == b"MZ" and offset >= 64 and
                        data[offset:offset + 4] == b"PE\0\0" and
                        int.from_bytes(data[offset + 4:offset + 6], "little") == 0x14c and
                        not data[64:96].startswith(b"Wine builtin DLL"), "expected native i386 D9VK DLL")
        digest = hashlib.sha256(data).hexdigest()
        version = (runtime / ".tmnf-runtime").read_text().strip()
        revision = json.loads((runtime / "sources.json").read_text())["d9vk"]["revision"]
        print(f"\nRenderer runtime={runtime} version={version} recorded baseline D9VK={revision}", flush=True)
        overlay = bool(os.environ.get("MACTMNF_TEST_D9VK_DLL"))
        print(f"Renderer DLL={source} sha256={digest} overlay={overlay}", flush=True)

        # Never accept caller TMPDIR/HOME/WINEPREFIX. All writable state is owned.
        scratch = Path(tempfile.mkdtemp(prefix="mactmnf-renderer-", dir="/tmp")).resolve()
        root_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
        owned = []
        start = time.monotonic()

        def interrupt(signum, frame):
            raise KeyboardInterrupt(f"renderer test interrupted by signal {signum}")

        previous = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, interrupt)
            prefix = scratch / "prefix-i686"
            prefix.mkdir(mode=0o700)
            identity = (prefix.stat().st_dev, prefix.stat().st_ino)
            home = scratch / "home-i686"
            for directory in ("tmp", "cache", "config", "data", "logs", "shaders"):
                (home / directory).mkdir(parents=True)
            appdir = scratch / "app"
            appdir.mkdir()
            # Exclusive app-local copy cannot follow Wine's prefix DLL symlinks.
            with (appdir / "d3d9.dll").open("xb") as destination:
                destination.write(data)
            config = home / "dxvk.conf"
            config.write_text("dxvk.enableAsync = False\n", encoding="ascii")
            def winpath(path):
                return "Z:" + str(path).replace("/", "\\")

            env = {"PATH": str(runtime / "bin") + ":/usr/bin:/bin", "HOME": str(home),
                   "CFFIXED_USER_HOME": str(home), "TMPDIR": str(home / "tmp"),
                   "TMP": winpath(home / "tmp"), "TEMP": winpath(home / "tmp"),
                   "USER": "mactmnf-renderer-test", "LOGNAME": "mactmnf-renderer-test",
                   "XDG_CACHE_HOME": str(home / "cache"), "XDG_CONFIG_HOME": str(home / "config"),
                   "XDG_DATA_HOME": str(home / "data"), "WINEPREFIX": str(prefix),
                   "WINESERVER": str(runtime / "bin/wineserver"), "WINEARCH": "win64",
                   "WINEDLLOVERRIDES": "mscoree,mshtml=;d3d9=n", "WINEDEBUG": "-all",
                   "WINE_D3D_CONFIG": "renderer=gdi", "MACTMNF_X87": "off",
                   "DXVK_CONFIG_FILE": winpath(config), "DXVK_ASYNC": "0",
                   "DXVK_LOG_LEVEL": "info", "DXVK_LOG_PATH": winpath(home / "logs"),
                   "DXVK_SHADER_DUMP_PATH": winpath(home / "shaders"),
                   "DXVK_STATE_CACHE_PATH": winpath(home / "cache")}
            exe = appdir / "d3d9-formats.exe"
            code, output = run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                                "-msse2", "-mfpmath=sse", ROOT / "tests/d3d9-formats.c",
                                "-o", exe, "-ld3d9", "-luser32"],
                               {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(home / "tmp")})
            self.assertEqual(code, 0, output)
            # Before any Wine call, failures need only identity-checked scratch removal.
            owned.append((prefix, identity, env))
            code, output = run([runtime / "bin/wine", "--version"], env)
            self.assertEqual(code, 0, output)
            print("Renderer Wine: " + output.strip(), flush=True)
            boot_start = time.monotonic()
            check_owned_prefix(scratch, prefix, identity)
            code, output = run([runtime / "bin/wine", "wineboot.exe", "--init"], env, timeout=90)
            self.assertEqual(code, 0, "wineboot failed: " + output)
            code, output = run([runtime / "bin/wineserver", "-w"], env, timeout=30)
            self.assertEqual(code, 0, "wineboot server did not finish: " + output)
            self.assertTrue((prefix / "system.reg").is_file())
            self.assertEqual((prefix / "dosdevices/z:").resolve(), Path("/"))
            print(f"Renderer prefix init {time.monotonic() - boot_start:.2f}s", flush=True)
            check_owned_prefix(scratch, prefix, identity)
            probe_start = time.monotonic()
            try:
                code, output = run([runtime / "bin/wine", exe, winpath(appdir / "d3d9.dll")], env, timeout=90)
                print(output, flush=True)
            finally:
                print(f"Renderer probe elapsed {time.monotonic() - probe_start:.2f}s", flush=True)
                for log in sorted((home / "logs").glob("*.log")):
                    print(f"Renderer log {log.name}:\n{log.read_text(errors='replace')}", flush=True)
            renderer_log = (home / "logs/d3d9-formats_d3d9.log").read_text()
            self.assertIn("Found config file: " + winpath(config), renderer_log)
            self.assertIn("dxvk.enableAsync = False", renderer_log)
            paths = [line.removeprefix("INFO: d3d9 module=") for line in output.splitlines()
                     if line.startswith("INFO: d3d9 module=")]
            self.assertEqual(len(paths), 1, "missing/ambiguous loaded module proof")
            self.assertEqual(PureWindowsPath(paths[0]), PureWindowsPath(winpath(appdir / "d3d9.dll")))
            self.assertEqual(code, 0, output)
            for name in ("A8R8G8B8", "A4R4G4B4", "A1R5G5B5-alpha0", "A1R5G5B5-alpha1", "R5G6B5"):
                self.assertIn("PASS: " + name + " RGBA=", output)
            for index in range(3):
                self.assertIn(f"PASS: volume-A4R4G4B4 slice={index} ", output)
            for name in ("A4R4G4B4", "A1R5G5B5-alpha0", "A1R5G5B5-alpha1", "R5G6B5"):
                self.assertIn("PASS: readback-" + name + " rows_packed16=", output)
            self.assertIn("RESULT: cases=10 failures=0", output)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest, "source DLL changed during test")
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                cleanup_prefixes(runtime, scratch, root_identity, owned)
                print(f"Renderer cleanup complete; total {time.monotonic() - start:.2f}s", flush=True)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


if __name__ == "__main__":
    unittest.main()
