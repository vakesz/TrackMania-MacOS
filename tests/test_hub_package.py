import json
import os
import plistlib
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from trackmania_mac.install import version as project_version


ROOT = Path(__file__).resolve().parents[1]
VERSION = project_version.read(ROOT)
APP = ROOT / ".build/app/TrackMania Mac.app"
ZIP = ROOT / ".build/dist" / f"TrackMania-Mac-{VERSION}.app.zip"
PACKAGER = ROOT / "src/shell/tools/package-app.sh"
RELEASE_ASSETS = ROOT / "config/release-assets.sha256"
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
        pin = "ABCDEF0123456789" * 4
        # The release workflow builds unsigned in one job and signs a tar of
        # that app in another, so exercise the same two separate processes.
        ZIP.unlink(missing_ok=True)
        env = {**os.environ, "TRACKMANIA_MAC_RUNTIME_PIN_SHA256": pin}
        result = subprocess.run(
            ["/bin/bash", str(PACKAGER), "build-unsigned"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=180,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(APP.is_dir())
        self.assertFalse(ZIP.exists(), "build-unsigned must not write the zip")

        # Carry the app the way CI does: upload-artifact loses symlinks and exec
        # bits, so the workflow ships a tar; sign must work on its extraction.
        with tempfile.TemporaryDirectory() as directory:
            tarball = Path(directory) / "hub-app.tar"
            tar_env = {**os.environ, "COPYFILE_DISABLE": "1"}
            subprocess.run(
                ["tar", "-C", str(APP.parent), "-cf", str(tarball), APP.name],
                check=True,
                env=tar_env,
            )
            shutil.rmtree(APP)
            subprocess.run(
                ["tar", "-C", str(APP.parent), "-xf", str(tarball)], check=True, env=tar_env
            )

        result = subprocess.run(
            ["/bin/bash", str(PACKAGER), "sign"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        executable = APP / "Contents/MacOS/TrackMania Mac"
        launcher = APP / "Contents/Resources/launcher"
        icon = APP / "Contents/Resources/AppIcon.icns"
        self.assertTrue(executable.is_file())
        self.assertTrue((ROOT / "assets/branding/trackmania-mac-icon.svg").is_file())
        self.assertGreater(icon.stat().st_size, 1024)
        self.assertEqual(icon.read_bytes()[:4], b"icns")
        self.assertTrue((launcher / "src/shell/cli/trackmania-mac").is_file())
        self.assertTrue((launcher / "config/titles/tmnf.json").is_file())
        # The runtime pin sits inside the codesigned launcher payload.
        self.assertEqual(
            (launcher / "config/runtime-pin").read_text(), f"{VERSION} {pin.lower()}\n"
        )
        bundled_python = launcher / "python/bin/python3"
        self.assertTrue(bundled_python.is_file())
        self.assertFalse((launcher / "python/include").exists())
        self.assertFalse((launcher / "python/lib/python3.12/site-packages").exists())
        self.assertFalse(list((launcher / "python").rglob("__pycache__")))
        python_root = launcher / "python"
        self.assertFalse(
            [
                p
                for p in (python_root / "lib").iterdir()
                if any(t in p.name.lower() for t in ("tcl", "tk"))
            ]
        )
        self.assertFalse(list(python_root.rglob("_tkinter*")))
        titles = {p.stem for p in (ROOT / "config/titles").glob("*.json")}
        self.assertEqual({p.stem for p in (launcher / "assets/titles").glob("*.png")}, titles)
        self.assertFalse(list((launcher / "assets/titles").glob("*.svg")))
        for png in (launcher / "assets/titles").glob("*.png"):
            dims = subprocess.check_output(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(png)], text=True
            )
            self.assertIn("pixelWidth: 512", dims)
            self.assertIn("pixelHeight: 512", dims)
        for path in python_root.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and (path.suffix in (".so", ".dylib") or path.name.startswith("python3"))
            ):
                if "Mach-O" in subprocess.check_output(["file", "-b", str(path)], text=True):
                    subprocess.run(["codesign", "--verify", "--strict", str(path)], check=True)
        self.assertFalse((launcher / "src/python/trackmania_mac/config.py").exists())

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
        lint = subprocess.run(
            ["plutil", "-lint", str(APP / "Contents/Info.plist")],
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

        with tempfile.TemporaryDirectory() as directory:
            iconset = Path(directory) / "AppIcon.iconset"
            result = subprocess.run(
                ["iconutil", "-c", "iconset", "-o", str(iconset), str(icon)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual({path.name for path in iconset.iterdir()}, set(ICON_SIZES))
            for name, side in ICON_SIZES.items():
                dimensions = subprocess.check_output(
                    ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(iconset / name)],
                    text=True,
                )
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
                text=True,
                capture_output=True,
                timeout=20,
                env={"HOME": str(home), "PATH": str(tools), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(
                bundled_check.returncode, 0, bundled_check.stdout + bundled_check.stderr
            )
            result = subprocess.run(
                [str(launcher / "src/shell/cli/trackmania-mac"), "hub", "snapshot"],
                cwd=launcher,
                text=True,
                capture_output=True,
                timeout=20,
                env={"HOME": str(home), "PATH": str(tools)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["schema"], 3)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
            self.assertFalse(list((launcher / "src").rglob("__pycache__")))

            staged = subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    '. "$1"; stage_launcher_payload "$2" "$3"; '
                    '"$3/python/bin/python3" -c "import ssl"',
                    "fixture",
                    str(launcher / "src/shell/lib/apps/payload.sh"),
                    str(launcher),
                    str(installed_support),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            self.assertEqual(
                (installed_support / "config/runtime-pin").read_text(),
                (launcher / "config/runtime-pin").read_text(),
            )
            installed = subprocess.run(
                [str(installed_support / "src/shell/cli/trackmania-mac"), "hub", "snapshot"],
                cwd=installed_support,
                text=True,
                capture_output=True,
                timeout=20,
                env={"HOME": str(home), "PATH": str(tools)},
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            self.assertEqual(json.loads(installed.stdout)["schema"], 3)

        self.assertTrue(ZIP.is_file())
        with zipfile.ZipFile(ZIP) as archive:
            names = archive.namelist()
        top_level = {
            name.split("/", 1)[0] for name in names if name and not name.startswith("__MACOSX/")
        }
        self.assertEqual(top_level, {"TrackMania Mac.app"})
        self.assertFalse(any(part.startswith("._") for name in names for part in Path(name).parts))

        # A user may extract the download with standard unzip, not Archive Utility.
        # The archive must not materialize AppleDouble files within the signed bundle.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["unzip", "-q", str(ZIP), "-d", directory],
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            subprocess.run(
                [
                    "codesign",
                    "--verify",
                    "--deep",
                    "--strict",
                    str(Path(directory) / "TrackMania Mac.app"),
                ],
                check=True,
            )


class PackagerCommands(unittest.TestCase):
    """Subcommand wiring, with the build, sign and zip steps stubbed out."""

    def dispatch(self, command):
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                '. "$1"; build_appbundle() { echo build; }; sign_appbundle() { echo sign; }; '
                'build_zip() { echo zip; }; main "$2"',
                "fixture",
                str(PACKAGER),
                command,
            ],
            text=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin"},
        )
        return result, result.stdout.split()

    def test_each_command_runs_its_steps_in_order(self):
        expected = {
            "build-unsigned": ["build"],
            "sign": ["sign", "zip"],
            "appbundle": ["build", "sign"],
            "zip": ["build", "sign", "zip"],
            "all": ["build", "sign", "zip"],
        }
        for command, steps in expected.items():
            with self.subTest(command=command):
                result, ran = self.dispatch(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(ran, steps)

    def test_unknown_command_is_rejected_and_help_lists_the_new_ones(self):
        result, ran = self.dispatch("bogus")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(ran, [])
        result, _ = self.dispatch("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build-unsigned", result.stdout)
        self.assertIn("sign", result.stdout)

    def check_staged_app(self, app_dir):
        return subprocess.run(
            [
                "/bin/bash",
                "-euc",
                '. "$1"; APP_DIR="$2"; check_staged_app',
                "fixture",
                str(PACKAGER),
                str(app_dir),
            ],
            text=True,
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin"},
        )

    def test_sign_refuses_a_missing_or_mangled_app(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "TrackMania Mac.app"
            self.assertNotEqual(self.check_staged_app(app).returncode, 0)

            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources/launcher/python/bin").mkdir(parents=True)
            (contents / "Info.plist").write_text("<plist/>")
            executable = contents / "MacOS/TrackMania Mac"
            python = contents / "Resources/launcher/python/bin/python3"
            for path in (executable, python):
                path.write_text("#!/bin/sh\n")
            executable.chmod(0o755)
            python.chmod(0o644)
            # Exec bits lost in transit would make signing silently skip Python.
            result = self.check_staged_app(app)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bundled Python", result.stderr)

            python.chmod(0o755)
            result = self.check_staged_app(app)
            self.assertEqual(result.returncode, 0, result.stderr)

            executable.chmod(0o644)
            self.assertNotEqual(self.check_staged_app(app).returncode, 0)


@unittest.skipUnless((ROOT / ".git").exists(), "source archives omit the release hash file")
class ReleaseAssetsFile(unittest.TestCase):
    """config/release-assets.sha256 is the committed trust anchor for tag builds."""

    def test_names_exactly_the_current_runtime_and_source_archives(self):
        text = RELEASE_ASSETS.read_text()
        self.assertTrue(text.endswith("\n"))
        lines = text.splitlines()
        entries = []
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (trackmania-mac-[^ /]+\.tar\.xz)", line)
            self.assertIsNotNone(match, f"malformed line: {line!r}")
            entries.append(match.groups())
        self.assertEqual(
            [name for _, name in entries],
            [f"trackmania-mac-{VERSION}-runtime.tar.xz", f"trackmania-mac-{VERSION}-source.tar.xz"],
        )
        self.assertEqual(len({digest for digest, _ in entries}), 2)

    def test_is_in_shasum_format(self):
        # shasum -c must accept the file as committed (it reads files that are
        # absent here, so only parsing is checked: every line is "properly
        # formatted" and nothing is reported as a format error).
        result = subprocess.run(
            ["shasum", "-a", "256", "--strict", "-c", str(RELEASE_ASSETS)],
            cwd=Path(tempfile.gettempdir()),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertNotIn("improperly formatted", result.stderr)
        self.assertNotIn("no properly formatted", result.stderr)


class RuntimePinWriter(unittest.TestCase):
    def write(self, **env):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory)
            (launcher / "config").mkdir()
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    '. "$1"; write_runtime_pin "$2"',
                    "fixture",
                    str(ROOT / "src/shell/tools/package-app.sh"),
                    str(launcher),
                ],
                text=True,
                capture_output=True,
                timeout=30,
                env={"PATH": "/usr/bin:/bin", **env},
            )
            pin = launcher / "config/runtime-pin"
            return result, pin.read_text() if pin.exists() else None

    def test_valid_hash_is_lowercased_and_paired_with_version(self):
        result, pin = self.write(TRACKMANIA_MAC_RUNTIME_PIN_SHA256="AB" * 32)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pin, f"{VERSION} {'ab' * 32}\n")

    def test_invalid_hash_is_rejected(self):
        for value in ("abc", "g" * 64, "a" * 63, "a" * 65):
            with self.subTest(value=value):
                result, pin = self.write(TRACKMANIA_MAC_RUNTIME_PIN_SHA256=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(pin)

    def test_without_a_hash_no_pin_is_written_and_a_note_is_printed(self):
        result, pin = self.write()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(pin)
        self.assertIn("no runtime checksum pin", result.stdout)

    def test_required_pin_fails_the_build_when_missing(self):
        result, pin = self.write(TRACKMANIA_MAC_REQUIRE_RUNTIME_PIN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(pin)


if __name__ == "__main__":
    unittest.main()
