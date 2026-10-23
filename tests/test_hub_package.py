import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
APP = ROOT / ".build/app/TrackMania Mac.app"
ZIP = ROOT / ".build/dist" / f"TrackMania-Mac-{VERSION}.app.zip"
ICON_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


class HubPackageTests(unittest.TestCase):
    def test_native_hub_bundle_and_zip(self):
        result = subprocess.run(["/bin/bash", "src/shell/tools/package-app.sh", "all"], cwd=ROOT,
                                text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        executable = APP / "Contents/MacOS/TrackMania Mac"
        launcher = APP / "Contents/Resources/launcher"
        icon = APP / "Contents/Resources/AppIcon.icns"
        self.assertTrue(executable.is_file())
        self.assertTrue((ROOT / "assets/branding/trackmania-mac-icon.svg").is_file())
        self.assertGreater(icon.stat().st_size, 1024)
        self.assertEqual(icon.read_bytes()[:4], b"icns")
        self.assertTrue((launcher / "src/shell/cli/tmnf").is_file())
        self.assertTrue((launcher / "config/titles/tmnf.json").is_file())
        bundled_python = launcher / "python/bin/python3"
        self.assertTrue(bundled_python.is_file())
        self.assertFalse((launcher / "python/include").exists())
        self.assertFalse((launcher / "python/lib/python3.12/site-packages").exists())
        self.assertFalse(list((launcher / "python").rglob("__pycache__")))
        python_root = launcher / "python"
        self.assertFalse([p for p in (python_root / "lib").iterdir()
                          if any(t in p.name.lower() for t in ("tcl", "tk"))])
        self.assertFalse(list(python_root.rglob("_tkinter*")))
        titles = {p.stem for p in (ROOT / "config/titles").glob("*.json")}
        self.assertEqual({p.stem for p in (ROOT / "assets/titles").glob("*.svg")}, titles)
        self.assertEqual({p.stem for p in (launcher / "assets/titles").glob("*.png")}, titles)
        self.assertFalse(list((launcher / "assets/titles").glob("*.svg")))
        for png in (launcher / "assets/titles").glob("*.png"):
            dims = subprocess.check_output(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(png)], text=True)
            self.assertIn("pixelWidth: 512", dims)
            self.assertIn("pixelHeight: 512", dims)
        for path in python_root.rglob("*"):
            if path.is_file() and not path.is_symlink() and (
                    path.suffix in (".so", ".dylib") or path.name.startswith("python3")):
                if "Mach-O" in subprocess.check_output(["file", "-b", str(path)], text=True):
                    subprocess.run(["codesign", "--verify", "--strict", str(path)], check=True)
        self.assertFalse((launcher / "src/python/tmnf/config.py").exists())

        file_type = subprocess.check_output(["file", str(executable)], text=True)
        self.assertIn("Mach-O", file_type)
        self.assertNotIn("shell script", file_type)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)

        with (APP / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        self.assertEqual(info["CFBundleExecutable"], "TrackMania Mac")
        self.assertEqual(info["CFBundleIconFile"], "AppIcon")
        self.assertEqual(info["LSApplicationCategoryType"], "public.app-category.games")
        self.assertEqual(info["CFBundleShortVersionString"], VERSION)
        self.assertEqual(info["CFBundleVersion"], VERSION)
        lint = subprocess.run(["plutil", "-lint", str(APP / "Contents/Info.plist")],
                              text=True, capture_output=True, timeout=20)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

        with tempfile.TemporaryDirectory() as directory:
            iconset = Path(directory) / "AppIcon.iconset"
            result = subprocess.run(["iconutil", "-c", "iconset", "-o", str(iconset), str(icon)],
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual({path.name for path in iconset.iterdir()}, set(ICON_SIZES))
            for name, side in ICON_SIZES.items():
                dimensions = subprocess.check_output(
                    ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(iconset / name)],
                    text=True)
                self.assertIn(f"pixelWidth: {side}", dimensions)
                self.assertIn(f"pixelHeight: {side}", dimensions)

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            tools = Path(directory) / "tools"
            installed_support = Path(directory) / "installed-support"
            home.mkdir()
            tools.mkdir()
            for command, target in {"cat": "/bin/cat", "dirname": "/usr/bin/dirname"}.items():
                (tools / command).symlink_to(target)
            fake_python = tools / "python3"
            fake_python.write_text("#!/bin/sh\nexit 97\n")
            fake_python.chmod(0o755)
            bundled_check = subprocess.run(
                [str(bundled_python), "-c", "import ssl, urllib.request, zipfile"],
                text=True, capture_output=True, timeout=20,
                env={"HOME": str(home), "PATH": str(tools),
                     "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(bundled_check.returncode, 0,
                             bundled_check.stdout + bundled_check.stderr)
            result = subprocess.run([str(launcher / "src/shell/cli/tmnf"), "hub", "snapshot"],
                                    cwd=launcher, text=True, capture_output=True, timeout=20,
                                    env={"HOME": str(home), "PATH": str(tools)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema"], 2)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
            self.assertFalse(list((launcher / "src").rglob("__pycache__")))

            staged = subprocess.run(
                ["/bin/bash", "-euc", '. "$1"; stage_launcher_payload "$2" "$3"; '
                 '"$3/python/bin/python3" -c "import ssl"', "fixture",
                 str(launcher / "src/shell/lib/apps/payload.sh"), str(launcher),
                 str(installed_support)],
                text=True, capture_output=True, timeout=30,
                env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            installed = subprocess.run([str(installed_support / "src/shell/cli/tmnf"), "hub", "snapshot"],
                                       cwd=installed_support, text=True, capture_output=True, timeout=20,
                                       env={"HOME": str(home), "PATH": str(tools)})
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            self.assertEqual(json.loads(installed.stdout)["schema"], 2)

        self.assertTrue(ZIP.is_file())
        with zipfile.ZipFile(ZIP) as archive:
            names = archive.namelist()
        top_level = {name.split("/", 1)[0] for name in names if name and not name.startswith("__MACOSX/")}
        self.assertEqual(top_level, {"TrackMania Mac.app"})
        self.assertFalse(any(part.startswith("._") for name in names for part in Path(name).parts))

        # A user may extract the download with standard unzip, not Archive Utility.
        # The archive must not materialize AppleDouble files within the signed bundle.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["unzip", "-q", str(ZIP), "-d", directory],
                                    text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run(["codesign", "--verify", "--deep", "--strict",
                            str(Path(directory) / "TrackMania Mac.app")], check=True)

    def test_xml_escape(self):
        result = subprocess.run(
            ["/bin/bash", "-c", '. "$1"; xml_escape "$2"', str(ROOT / "src/shell/tools/_t"),
             str(ROOT / "src/shell/lib/plist.sh"), "a&b<c>\"d'e"],
            text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "a&amp;b&lt;c&gt;&quot;d&apos;e")


if __name__ == "__main__":
    unittest.main()
