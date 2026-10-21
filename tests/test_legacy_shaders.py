"""Opt-in disposable-prefix shader regressions. No game or caller prefix is used.

MACTMNF_TEST_RUNTIME is read-only. MACTMNF_TEST_D9VK_DLL selects the DXSO
candidate. MACTMNF_TEST_SHADER_DLL_DIR optionally supplies app-local compiler
DLLs, including native SDK references. Neither rendering here nor HLSL effect
compilation proves Wine's separate D3DBC lowering/render path.

MACTMNF_TEST_SHADER_MODE selects render (default), translate, effect,
effect-wine, or profiles. translate requires MACTMNF_TEST_VKD3D_INCLUDE and
an explicit scratch wined3d.dll overlay; it checks SPIR-V, not GPU output.
effect uses the public SDK entry point. effect-wine calls the legacy compiler
export with compiler_version=30; the current FX2 emitter blocks full success.
"""
import os
import hashlib
from pathlib import Path, PureWindowsPath
import signal
import shutil
import tempfile
import unittest

from test_network_runtime import check_owned_prefix, cleanup_prefixes, run

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("MACTMNF_TEST_RUNTIME"), "set MACTMNF_TEST_RUNTIME for GPU tests")
class LegacyShaders(unittest.TestCase):
    def test_shaders(self):
        runtime = Path(os.environ["MACTMNF_TEST_RUNTIME"]).resolve(strict=True)
        scratch = Path(tempfile.mkdtemp(prefix="mactmnf-legacy-shaders-", dir="/tmp")).resolve()
        root_identity = (scratch.stat().st_dev, scratch.stat().st_ino)
        owned = []
        previous = {}
        def interrupt(signum, frame):
            raise KeyboardInterrupt(f"shader test interrupted by signal {signum}")
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, interrupt)
            prefix = scratch / "prefix-i686"
            prefix.mkdir()
            identity = (prefix.stat().st_dev, prefix.stat().st_ino)
            home = scratch / "home"
            app = scratch / "app"
            app.mkdir()
            for directory in ("tmp", "cache", "config", "data", "logs"):
                (home / directory).mkdir(parents=True)
            def winpath(path):
                return "Z:" + str(path).replace("/", "\\")
            dll = Path(os.environ.get("MACTMNF_TEST_D9VK_DLL", runtime / "lib/d9vk/d3d9.dll")).resolve(strict=True)
            shutil.copyfile(dll, app / "d3d9.dll")
            print(f"D9VK DLL={dll} sha256={hashlib.sha256(dll.read_bytes()).hexdigest()}", flush=True)
            overrides = "mscoree,mshtml=;d3d9=n"
            if os.environ.get("MACTMNF_TEST_SHADER_DLL_DIR"):
                for source in Path(os.environ["MACTMNF_TEST_SHADER_DLL_DIR"]).glob("*.dll"):
                    self.assertNotEqual(source.name.lower(), "d3d9.dll")
                    shutil.copyfile(source, app / source.name)
                    print(f"Shader DLL={source} sha256={hashlib.sha256(source.read_bytes()).hexdigest()}", flush=True)
                    overrides += ";" + source.stem + "=n"
            config = home / "dxvk.conf"
            config.write_text("dxvk.enableAsync = False\n")
            env = {"PATH": str(runtime / "bin") + ":/usr/bin:/bin", "HOME": str(home),
                   "CFFIXED_USER_HOME": str(home), "TMPDIR": str(home / "tmp"),
                   "TMP": winpath(home / "tmp"), "TEMP": winpath(home / "tmp"),
                   "USER": "shader-test", "LOGNAME": "shader-test", "WINEPREFIX": str(prefix),
                   "WINESERVER": str(runtime / "bin/wineserver"), "WINEARCH": "win64",
                   "WINEDLLOVERRIDES": overrides, "WINEDEBUG": "-all", "MACTMNF_X87": "off",
                   "WINE_D3D_CONFIG": "renderer=gdi", "DXVK_ASYNC": "0", "MVK_CONFIG_LOG_LEVEL": "1",
                   "DXVK_LOG_LEVEL": "warn",
                   "DXVK_CONFIG_FILE": winpath(config), "DXVK_LOG_PATH": winpath(home / "logs"),
                   "DXVK_STATE_CACHE_PATH": winpath(home / "cache"),
                   "XDG_CACHE_HOME": str(home / "cache"), "XDG_CONFIG_HOME": str(home / "config"),
                   "XDG_DATA_HOME": str(home / "data")}
            compiler = shutil.which("i686-w64-mingw32-gcc")
            self.assertIsNotNone(compiler)
            for name in ("shader-profiles", "legacy-shader-render"):
                extra = []
                if name == "legacy-shader-render" and os.environ.get("MACTMNF_TEST_VKD3D_INCLUDE"):
                    extra = ["-DVKD3D_TRANSLATION_PROBE", "-I" + os.environ["MACTMNF_TEST_VKD3D_INCLUDE"]]
                code, output = run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                                    ROOT / "tests" / (name + ".c"), "-o", app / (name + ".exe"),
                                    "-ld3d9", "-luser32", *extra], dict(os.environ, TMPDIR=str(home / "tmp")))
                self.assertEqual(code, 0, output)
            owned.append((prefix, identity, env))
            check_owned_prefix(scratch, prefix, identity)
            code, output = run([runtime / "bin/wine", "wineboot.exe", "--init"], env, timeout=90)
            self.assertEqual(code, 0, output)
            code, output = run([runtime / "bin/wineserver", "-w"], env, timeout=30)
            self.assertEqual(code, 0, output)
            commands = [("shader-profiles", "--tokens-only")]
            mode = os.environ.get("MACTMNF_TEST_SHADER_MODE", "render")
            if mode in ("effect", "effect-wine"):
                commands.append(("shader-profiles", os.environ.get("MACTMNF_TEST_SHADER_SDK", "d3dx9_30.dll"),
                                 "effect-only" if mode == "effect" else mode))
            elif mode == "translate":
                self.assertTrue(os.environ.get("MACTMNF_TEST_VKD3D_INCLUDE"))
                commands.append(("legacy-shader-render", "--translate"))
            elif mode == "profiles":
                commands.append(("shader-profiles", os.environ.get("MACTMNF_TEST_SHADER_SDK", "d3dx9_30.dll")))
            else:
                self.assertEqual(mode, "render")
                commands.append(("legacy-shader-render",))
            for name, *args in commands:
                code, output = run([runtime / "bin/wine", app / (name + ".exe"), *args], env, timeout=90)
                print(output, flush=True)
                if name == "legacy-shader-render" or "effect-wine" in args:
                    module = "wined3d" if mode in ("translate", "effect-wine") else "d3d9"
                    paths = [line.removeprefix(f"INFO: {module} module=") for line in output.splitlines()
                             if line.startswith(f"INFO: {module} module=")]
                    self.assertEqual(len(paths), 1, "missing loaded module evidence")
                    self.assertEqual(PureWindowsPath(paths[0]), PureWindowsPath(winpath(app / (module + ".dll"))))
                self.assertEqual(code, 0, output)
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                cleanup_prefixes(runtime, scratch, root_identity, owned)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


if __name__ == "__main__":
    unittest.main()
