import os
import plistlib
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from trackmania_mac.guard import operation_guard as guard
from trackmania_mac.install import version as project_version


ROOT = Path(__file__).resolve().parents[1]


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-app-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = self.home / "current project"
        self.project.mkdir()
        for name in ("src", "config"):
            shutil.copytree(ROOT / name, self.project / name)
        shutil.copy2(ROOT / "pyproject.toml", self.project / "pyproject.toml")
        tools = self.home / "tools"
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        self.prefix = self.home / "prefix"
        self.launcher = self.home / "launcher"
        self.app = self.home / "apps/Game.app"
        self.settings = self.home / "apps/Settings.app"
        self.env = dict(
            HOME=str(self.home),
            PATH=str(tools) + ":" + os.environ["PATH"],
            PYTHONDONTWRITEBYTECODE="1",
            TRACKMANIA_MAC_ROOT=str(self.home / "game"),
            TRACKMANIA_MAC_PREFIX=str(self.prefix),
            TRACKMANIA_MAC_RUNTIME=str(self.home / "runtime"),
            TRACKMANIA_MAC_SUPPORT=str(self.home / "support"),
            TRACKMANIA_MAC_CACHE=str(self.home / "cache"),
            TRACKMANIA_MAC_USERDATA=str(self.home / "userdata"),
            TRACKMANIA_MAC_LAUNCHER=str(self.launcher),
            TRACKMANIA_MAC_APP=str(self.app),
            TRACKMANIA_MAC_SETTINGS_APP=str(self.settings),
            TRACKMANIA_MAC_APPS_DIR=str(self.home / "apps"),
            TRACKMANIA_MAC_X87="off",
        )
        self.game_exe = self.prefix / "drive_c/Program Files (x86)/TmNationsForever/TmForever.exe"
        self.game_exe.parent.mkdir(parents=True)
        self.game_exe.write_bytes(b"MZ")
        self.cli = ["/bin/bash", str(self.project / "src/shell/cli/trackmania-mac"), "app"]
        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.app / "keep").write_text("old game content")
        (self.settings / "keep").write_text("old settings content")
        self.before = self.snapshot()

    def run_app(self, **overrides):
        return subprocess.run(
            self.cli, env=dict(self.env, **overrides), text=True, capture_output=True, timeout=15
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.home)): path.read_bytes()
            for root in (self.app, self.settings, self.launcher)
            for path in root.rglob("*")
            if path.is_file()
        }

    def blocking_icon(self):
        ready = self.home / "icon-ready"
        (self.project / "src/python/trackmania_mac/app/icon.py").write_text(
            "import os, pathlib, time\n"
            'assert "TRACKMANIA_MAC_OPERATION_FD" not in os.environ\n'
            'assert "TRACKMANIA_MAC_APP_OPERATION_FD" not in os.environ\n'
            f"pathlib.Path({str(ready)!r}).touch()\n"
            "time.sleep(60)\n"
        )
        return ready

    def test_concurrent_refresh_refuses_without_mutating_existing_apps(self):
        ready = self.blocking_icon()
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        env = dict(self.env, **{guard.TOKEN: str(fd)})
        process = subprocess.Popen(
            [
                sys.executable,
                str(self.project / "src/python/trackmania_mac/guard/operation_guard.py"),
                "app-run",
                str(self.prefix),
                *self.cli,
            ],
            env=env,
            pass_fds=(fd,),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    self.fail(str(process.communicate()))
                self.assertLess(time.monotonic(), deadline, "Icon extractor did not start")
                time.sleep(0.01)
            result = self.run_app(TRACKMANIA_MAC_PREFIX=str(self.home / "second-prefix"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another app installation", result.stderr)
            self.assertEqual(self.snapshot(), self.before)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, (stdout, stderr))
            self.assertEqual(self.snapshot(), self.before)
            with self.assertRaisesRegex(ValueError, "Another operation"):
                guard.acquire(self.prefix)
            (self.project / "src/python/trackmania_mac/app/icon.py").write_text(
                "raise SystemExit(1)\n"
            )
            result = self.run_app(TRACKMANIA_MAC_PREFIX=str(self.home / "second-prefix"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.app / "keep").read_text(), "old game content")
            self.assertEqual((self.settings / "keep").read_text(), "old settings content")
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            process.communicate(timeout=8)

    def test_overlap_and_forged_token_preserve_existing_content(self):
        for overrides in (
            {"TRACKMANIA_MAC_APP": str(self.launcher / "Game.app")},
            {"TRACKMANIA_MAC_LAUNCHER": str(self.app / "support")},
            {"TRACKMANIA_MAC_SETTINGS_APP": str(self.app)},
            {guard.APP_TOKEN: "999999"},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_app(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot(), self.before)

    def test_sourced_install_app_returns_and_uses_current_project(self):
        (self.project / "pyproject.toml").write_text('[project]\nversion = "fixture-current"\n')
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                ". ./src/shell/cli/install.sh; . ./src/shell/lib/apps/wrappers.sh; install_app; "
                'PYTHONPATH=src/python python3 -m trackmania_mac.guard.operation_guard held "$TRACKMANIA_MAC_PREFIX"; printf "returned\\n"',
            ],
            cwd=self.project,
            env=dict(self.env, **{guard.TOKEN: str(fd)}),
            pass_fds=(fd,),
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("returned", result.stdout)
        self.assertEqual(project_version.read(self.launcher), "fixture-current")

    def test_refresh_carries_a_bundled_python_into_installed_app_support(self):
        bundled = self.project / "python/bin/python3"
        bundled.parent.mkdir(parents=True)
        bundled.symlink_to(sys.executable)

        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.launcher / "python/bin/python3"
        self.assertTrue(installed.is_file())
        self.assertTrue(os.access(installed, os.X_OK))
        wrapper = (self.app / "Contents/MacOS/TrackManiaMac").read_text()
        self.assertIn(str(installed), wrapper)

    def per_title_env(self, title, **extra):
        """Environment for a title using the default per-title launcher location."""
        env = {k: v for k, v in self.env.items() if k != "TRACKMANIA_MAC_LAUNCHER"}
        env.update(
            TRACKMANIA_MAC_TITLE=title,
            TRACKMANIA_MAC_PREFIX=str(self.home / f"{title}-prefix"),
            TRACKMANIA_MAC_APP=str(self.home / f"apps/{title}-Game.app"),
            TRACKMANIA_MAC_SETTINGS_APP=str(self.home / f"apps/{title}-Settings.app"),
        )
        env.update(extra)
        return env

    def run_env(self, env):
        return subprocess.run(self.cli, env=env, text=True, capture_output=True, timeout=15)

    def test_titles_install_into_separate_launchers(self):
        support = self.home / "support"
        for title in ("tmnf", "tmu"):
            result = self.run_env(self.per_title_env(title))
            self.assertEqual(result.returncode, 0, result.stderr)
        for title in ("tmnf", "tmu"):
            launcher = support / "launchers" / title
            settings = (launcher / "config/installation.sh").read_text()
            self.assertIn(f"export TRACKMANIA_MAC_TITLE={title}; fi\n", settings)
            wrapper = (
                self.home / f"apps/{title}-Game.app/Contents/MacOS/TrackManiaMac"
            ).read_text()
            self.assertIn(str(launcher / "python/bin/python3"), wrapper)
            log_name = "play.log" if title == "tmnf" else f"{title}-play.log"
            self.assertIn(f"Library/TrackManiaMacOS/logs/{log_name}", wrapper)
        self.assertNotIn(
            "TRACKMANIA_MAC_TITLE=tmu",
            (support / "launchers/tmnf/config/installation.sh").read_text(),
        )

    def test_legacy_shared_launcher_is_removed_only_when_it_is_this_titles(self):
        support = self.home / "support"
        legacy = support / "launcher"
        result = self.run_env(self.per_title_env("tmnf", TRACKMANIA_MAC_LAUNCHER=str(legacy)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(legacy.is_dir())
        result = self.run_env(self.per_title_env("tmnf"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        wrapper = (self.home / "apps/tmnf-Game.app/Contents/MacOS/TrackManiaMac").read_text()
        self.assertIn(str(support / "launchers/tmnf/python/bin/python3"), wrapper)

        # A legacy launcher last installed for another title is left alone.
        result = self.run_env(self.per_title_env("tmu", TRACKMANIA_MAC_LAUNCHER=str(legacy)))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_env(self.per_title_env("tmnf"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("another title", result.stderr)
        self.assertTrue((legacy / ".trackmania-mac-launcher").exists())

    def test_symlinked_legacy_launcher_is_never_followed(self):
        support = self.home / "support"
        victim = self.home / "victim"
        victim.mkdir()
        (victim / "keep").write_text("data")
        support.mkdir(exist_ok=True)
        (support / "launcher").symlink_to(victim)
        result = self.run_env(self.per_title_env("tmnf"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((victim / "keep").read_text(), "data")
        self.assertTrue((support / "launcher").is_symlink())

    def test_plist_values_are_xml_escaped(self):
        app = self.home / 'apps/R&D <"it\'s">.app'
        result = self.run_app(TRACKMANIA_MAC_APP=str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        self.assertEqual(plist["CFBundleName"], 'R&D <"it\'s">')

    def test_symlinked_executable_or_icon_is_refused(self):
        for relative in ("Contents/MacOS/TrackManiaMac", "Contents/Resources/AppIcon.icns"):
            with self.subTest(relative=relative):
                target = self.home / "target"
                target.write_text("untouched")
                entry = self.app / relative
                entry.unlink(missing_ok=True)
                entry.symlink_to(target)
                result = self.run_app()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr)
                self.assertEqual(target.read_text(), "untouched")
                entry.unlink()
                self.assertEqual(self.run_app().returncode, 0)

    def test_wrapper_fails_closed_without_launcher_python(self):
        entry = self.app / "Contents/MacOS/TrackManiaMac"
        self.assertEqual(os.stat(entry).st_mode & 0o111, 0o111)
        self.assertFalse((self.launcher / "python/bin/python3").exists())
        alerts = self.home / "alerts"
        alert = self.home / "alert"
        alert.write_text(f'#!/bin/bash\necho "$@" >> {alerts}\n')
        alert.chmod(0o755)
        entry.write_text(entry.read_text().replace("/usr/bin/osascript", str(alert)))
        # self.env's PATH has a python3; it must not be used as a fallback.
        result = subprocess.run(
            ["/bin/bash", str(entry)], env=self.env, text=True, capture_output=True, timeout=15
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("launcher support files are missing", alerts.read_text())

    def run_wrapper_with_status(self, status):
        entry = self.app / "Contents/MacOS/TrackManiaMac"
        python = self.launcher / "python/bin/python3"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text(f"#!/bin/bash\nexit {status}\n")
        python.chmod(0o755)
        alerts = self.home / "alerts"
        alerts.unlink(missing_ok=True)
        alert = self.home / "alert"
        alert.write_text(f'#!/bin/bash\necho "$@" >> {alerts}\n')
        alert.chmod(0o755)
        entry.write_text(entry.read_text().replace("/usr/bin/osascript", str(alert)))
        result = subprocess.run(
            ["/bin/bash", str(entry)], env=self.env, text=True, capture_output=True, timeout=15
        )
        return result, alerts.read_text() if alerts.exists() else ""

    def test_wrapper_maps_busy_status_to_its_own_alert(self):
        result, alerts = self.run_wrapper_with_status(75)
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertIn("Another TrackMania session is still finishing", alerts)
        self.assertNotIn("failed or its state is uncertain", alerts)

    def test_wrapper_keeps_failure_alert_for_other_statuses_and_none_on_success(self):
        result, alerts = self.run_wrapper_with_status(1)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("failed or its state is uncertain", alerts)
        self.assertNotIn("still finishing", alerts)
        result, alerts = self.run_wrapper_with_status(0)
        self.assertEqual((result.returncode, alerts), (0, ""))

    def stub_icon_tool(self):
        (self.project / "src/python/trackmania_mac/app/icon.py").write_text(
            "import pathlib, sys\n"
            'pathlib.Path(sys.argv[-1]).write_bytes(b"icns-converted" if sys.argv[1] == "--from-png" '
            'else b"icns-extracted")\n'
        )

    def test_game_icon_is_extracted_lazily_and_copied_into_both_apps(self):
        self.stub_icon_tool()
        cache = self.home / "support/icons/tmnf.icns"
        self.assertFalse(cache.exists())
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual(cache.read_bytes(), b"icns-extracted")
        for app in (self.app, self.settings):
            self.assertEqual(
                (app / "Contents/Resources/AppIcon.icns").read_bytes(), b"icns-extracted"
            )
        self.assertFalse(list(cache.parent.glob(".extract.*")))

    def test_bundled_artwork_is_the_fallback_until_a_game_icon_exists(self):
        self.stub_icon_tool()
        self.game_exe.unlink()
        art = self.project / "assets/titles"
        art.mkdir(parents=True)
        (art / "tmnf.png").write_bytes(b"png")
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual(
            (self.app / "Contents/Resources/AppIcon.icns").read_bytes(), b"icns-converted"
        )
        self.assertFalse((self.home / "support/icons/tmnf.icns").exists())
        # A cached game icon wins over the bundled artwork.
        (self.home / "support/icons").mkdir(parents=True, exist_ok=True)
        (self.home / "support/icons/tmnf.icns").write_bytes(b"icns-game")
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual((self.app / "Contents/Resources/AppIcon.icns").read_bytes(), b"icns-game")


class AppRenameTests(unittest.TestCase):
    """trackmania-mac rename-apps with the default locations in a scratch HOME."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-rename-", dir="/private/tmp")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.project = self.home / "project"
        self.project.mkdir()
        for name in ("src", "config"):
            shutil.copytree(ROOT / name, self.project / name)
        shutil.copy2(ROOT / "pyproject.toml", self.project / "pyproject.toml")
        tools = self.home / "tools"
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        self.apps = self.home / "apps"
        self.data = self.home / "Library/TrackManiaMacOS"
        self.root = self.data / "games/tmu"
        prefix = self.root / "prefix"
        prefix.mkdir(parents=True)
        (prefix / ".trackmania-mac-prefix").write_text("tmu:1\n")
        self.env = dict(
            HOME=str(self.home),
            PATH=str(tools) + ":" + os.environ["PATH"],
            PYTHONDONTWRITEBYTECODE="1",
            TRACKMANIA_MAC_TITLE="tmu",
            TRACKMANIA_MAC_APPS_DIR=str(self.apps),
            TRACKMANIA_MAC_X87="off",
        )
        self.game = self.apps / "TrackMania United Forever.app"
        self.settings = self.apps / "TrackMania United Settings.app"
        self.config = self.data / "launchers/tmu/config/installation.sh"
        result = self.cli("app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.game.is_dir() and self.settings.is_dir())

    def cli(self, *arguments, **overrides):
        return subprocess.run(
            ["/bin/bash", str(self.project / "src/shell/cli/trackmania-mac"), *arguments],
            env=dict(self.env, **overrides),
            text=True,
            capture_output=True,
            timeout=30,
        )

    @staticmethod
    def plist(app):
        return plistlib.loads((app / "Contents/Info.plist").read_bytes())

    def recorded(self, key):
        prefix = f'if test -z "${{TRACKMANIA_MAC_{key}:-}}"; then export TRACKMANIA_MAC_{key}='
        for line in self.config.read_text().splitlines():
            if line.startswith(prefix):
                return line[len(prefix) : -len("; fi")]
        return None

    def test_rename_keeps_the_bundles_and_survives_a_reinstall(self):
        inodes = (os.stat(self.game).st_ino, os.stat(self.settings).st_ino)
        (self.game / "keep").write_text("game")
        result = self.cli("rename-apps", "TMU")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Renamed the apps to TMU and TMU Settings.", result.stdout)
        game, settings = self.apps / "TMU.app", self.apps / "TMU Settings.app"
        self.assertFalse(self.game.exists() or self.settings.exists())
        self.assertEqual((os.stat(game).st_ino, os.stat(settings).st_ino), inodes)
        self.assertEqual((game / "keep").read_text(), "game")
        self.assertEqual(self.plist(game)["CFBundleName"], "TMU")
        self.assertEqual(self.plist(settings)["CFBundleName"], "TMU Settings")
        self.assertEqual(self.plist(game)["CFBundleIdentifier"], "local.trackmania-mac.tmu.game")
        self.assertEqual(
            self.plist(settings)["CFBundleIdentifier"], "local.trackmania-mac.tmu.settings"
        )
        self.assertEqual((self.root / "app-name").read_text(), "TMU\n")
        self.assertEqual(self.recorded("APP"), str(game).replace(" ", "\\ "))
        self.assertEqual(self.recorded("SETTINGS_APP"), str(settings).replace(" ", "\\ "))

        result = self.cli("rename-apps", "--name=TMU")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already named TMU", result.stdout)

        # Reinstalling the apps writes them at the custom names.
        result = self.cli("app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(path.name for path in self.apps.iterdir()), sorted([game.name, settings.name])
        )
        self.assertEqual(os.stat(game).st_ino, inodes[0])

        result = self.cli("rename-apps", "--reset")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.game.is_dir() and self.settings.is_dir())
        self.assertFalse(game.exists() or (self.root / "app-name").exists())
        self.assertEqual(self.plist(self.game)["CFBundleName"], "TrackMania United Forever")
        self.assertEqual(self.recorded("APP"), str(self.game).replace(" ", "\\ "))

    def test_the_default_name_counts_as_reset(self):
        self.assertEqual(self.cli("rename-apps", "Mine").returncode, 0)
        result = self.cli("rename-apps", "trackmania united forever")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.game.is_dir() and self.settings.is_dir())
        self.assertFalse((self.root / "app-name").exists())

    def test_refusals_change_nothing(self):
        (self.apps / "Taken.app").mkdir()
        cases = [
            (("rename-apps", "Taken"), {}, "already exists"),
            (("rename-apps", "TrackMania"), {}, "TrackMania Nations Forever uses the name"),
            (("rename-apps", "a/b"), {}, "cannot contain"),
            (("rename-apps", "X.app"), {}, "Leave out .app"),
            (("rename-apps",), {}, "Give one new name"),
            (("rename-apps", "A", "B"), {}, "Give one new name"),
            (("rename-apps", "--bogus"), {}, "Unknown option"),
            (("rename-apps", "X"), {"TRACKMANIA_MAC_APP": str(self.game)}, "keep those names"),
        ]
        for arguments, overrides, message in cases:
            with self.subTest(arguments=arguments, overrides=overrides):
                result = self.cli(*arguments, **overrides)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stderr)
                self.assertTrue(self.game.is_dir() and self.settings.is_dir())
                self.assertFalse((self.root / "app-name").exists())

    def test_a_held_prefix_refuses_with_the_busy_status(self):
        fd = guard.acquire(self.root / "prefix")
        self.addCleanup(os.close, fd)
        result = self.cli("rename-apps", "TMU", TRACKMANIA_MAC_LOCK_WAIT="0")
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertTrue(self.game.is_dir())
        self.assertFalse((self.root / "app-name").exists())

    def test_uninstall_removes_renamed_apps_and_then_the_name(self):
        self.assertEqual(self.cli("rename-apps", "TMU").returncode, 0)
        result = self.cli("uninstall", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.apps.iterdir()), [])
        self.assertFalse((self.root / "app-name").exists())
        lines = result.stdout.splitlines()
        removed = [line for line in lines if line.startswith("Removed ")]
        self.assertTrue(removed[-1].endswith("/app-name"), removed)


if __name__ == "__main__":
    unittest.main()
