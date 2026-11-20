"""Custom app names: the app-name file (trackmania_mac.app.names) and the rename guard.

Every fixture lives under a temporary HOME in /private/tmp; nothing here
touches the real ~/Library or /Applications.
"""

import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from trackmania_mac.app import names
from trackmania_mac.guard import app_rename
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import registry
from hang_guard import SUBPROCESS_TIMEOUT


ROOT = Path(__file__).resolve().parents[1]
TITLES = {title.id: title for title in registry.load_all(ROOT / "config/titles")}


def make_app(path, bundle_id, marker=True):
    contents = path / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, stream)
    executable = contents / "MacOS/TrackManiaMac"
    executable.write_text("#!/bin/bash\n")
    executable.chmod(0o755)
    if marker:
        (contents / ".trackmania-mac-app").touch()
    return path


class ValidateTests(unittest.TestCase):
    def test_accepted_names_are_trimmed_and_normalised(self):
        cases = {
            "TMU": "TMU",
            "  My game\t": "My game",
            "Café": "Café",
            'R&D <"it\'s">': 'R&D <"it\'s">',
            "x" * 64: "x" * 64,
            "Trackmania (2020)": "Trackmania (2020)",
            "Game.App2": "Game.App2",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(names.validate(raw), expected)

    def test_refused_names(self):
        cases = {
            "": "Enter a name",
            "   ": "Enter a name",
            "x" * 65: "at most 64",
            "é" * 64 + "一" * 0: None,  # 64 characters, 128 bytes: accepted
            "一" * 64 + "": None,  # 64 characters, 192 bytes: accepted
            "\U0001f600" * 51: "shorter",  # 51 characters, 204 bytes
            "a/b": "cannot contain",
            "a:b": "cannot contain",
            ".hidden": "start with a dot",
            "a\nb": "control or invisible",
            "a‮b": "control or invisible",
            "a​b": "control or invisible",
            "a b": "control or invisible",
            "Game.app": "Leave out .app",
            "Game.APP": "Leave out .app",
        }
        for raw, message in cases.items():
            with self.subTest(raw=raw):
                if message is None:
                    names.validate(raw)
                    continue
                with self.assertRaisesRegex(ValueError, message):
                    names.validate(raw)

    def test_key_compares_as_apfs_does(self):
        self.assertEqual(names.key("TMU"), names.key("tmu"))
        self.assertEqual(names.key("Café"), names.key("CAFÉ"))
        self.assertNotEqual(names.key("TMU"), names.key("TMU2"))


class NameFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="trackmania-mac-names-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.title = TITLES["tmu"]

    def test_effective_names(self):
        self.assertEqual(
            names.effective(self.title, self.root),
            (
                "TrackMania United Forever.app",
                "TrackMania United Settings.app",
                "TrackMania United Forever",
                False,
                None,
            ),
        )
        (self.root / "app-name").write_text("TMU\n")
        self.assertEqual(
            names.effective(self.title, self.root),
            ("TMU.app", "TMU Settings.app", "TMU", True, None),
        )

    def test_a_bad_file_leaves_the_registry_names(self):
        path = self.root / "app-name"
        cases = [b"a/b\n", b"\xff\n", b"two\nlines\n", b"x" * 2000, b"\n"]
        for data in cases:
            with self.subTest(data=data[:20]):
                path.write_bytes(data)
                current = names.effective(self.title, self.root)
                self.assertEqual(current[:4], names.defaults(self.title)[:4])
                self.assertTrue(current.problem)
        path.unlink()
        target = self.root / "target"
        target.write_text("TMU\n")
        path.symlink_to(target)
        self.assertIn("not a regular file", names.effective(self.title, self.root).problem)
        path.unlink()
        path.mkdir()
        self.assertIn("not a regular file", names.effective(self.title, self.root).problem)

    def export(self, title="tmu"):
        return subprocess.run(
            [sys.executable, "-m", "trackmania_mac.app.names", "export", str(self.root)],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src/python"), TRACKMANIA_MAC_TITLE=title),
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )

    def test_export_prints_shell_assignments(self):
        (self.root / "app-name").write_text("It's mine\n")
        result = self.export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "_trackmania_mac_app_file={}\n_trackmania_mac_settings_app_file={}\n".format(
                shlex.quote("It's mine.app"), shlex.quote("It's mine Settings.app")
            ),
        )
        (self.root / "app-name").write_text("bad/name\n")
        result = self.export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TrackMania United Forever.app", result.stdout)
        self.assertIn("warning: ", result.stderr)
        self.assertEqual(self.export("nonexistent").returncode, 1)


class RenameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="trackmania-mac-rename-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        environment = {
            key: value for key, value in os.environ.items() if key != "TRACKMANIA_MAC_TITLES_DIR"
        }
        environment.update(HOME=str(self.home), TRACKMANIA_MAC_TITLE="tmu")
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.title = TITLES["tmu"]
        self.root = hub_paths.title_root("tmu")
        self.root.mkdir(parents=True)
        self.apps = self.home / "Applications"
        self.game = make_app(self.apps / self.title.app_name, self.title.bundle_id_game)
        self.settings = make_app(
            self.apps / self.title.settings_app_name, self.title.bundle_id_settings
        )

    def rename(self, name, game=None, settings=None):
        return app_rename.rename(
            str(self.root), str(game or self.game), str(settings or self.settings), name
        )

    def listing(self):
        return sorted(path.name for path in self.apps.iterdir())

    def test_rename_moves_both_apps_and_records_the_name(self):
        inodes = (os.stat(self.game).st_ino, os.stat(self.settings).st_ino)
        result = self.rename("TMU")
        self.assertEqual(result, (self.apps / "TMU.app", self.apps / "TMU Settings.app", True))
        self.assertEqual(self.listing(), ["TMU Settings.app", "TMU.app"])
        self.assertEqual((os.stat(result.game).st_ino, os.stat(result.settings).st_ino), inodes)
        self.assertEqual((self.root / "app-name").read_text(), "TMU\n")
        self.assertEqual(self.rename("TMU", result.game, result.settings).changed, False)
        result = self.rename(None, result.game, result.settings)
        self.assertEqual((result.game, result.settings), (self.game, self.settings))
        self.assertFalse((self.root / "app-name").exists())

    def test_the_default_name_is_a_reset(self):
        result = self.rename("TMU")
        result = self.rename("TRACKMANIA UNITED FOREVER", result.game, result.settings)
        self.assertEqual((result.game, result.settings), (self.game, self.settings))
        self.assertFalse((self.root / "app-name").exists())

    def test_a_change_of_case_renames_in_place(self):
        probe = self.apps / "probe"
        probe.write_text("")
        if not (self.apps / "PROBE").exists():
            self.skipTest("case-sensitive volume")
        result = self.rename("TMU")
        result = self.rename("tmu", result.game, result.settings)
        self.assertEqual(result.changed, True)
        probe.unlink()
        self.assertEqual(self.listing(), ["tmu Settings.app", "tmu.app"])
        self.assertEqual((self.root / "app-name").read_text(), "tmu\n")

    def test_a_target_that_exists_is_refused(self):
        (self.apps / "Taken Settings.app").mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.rename("Taken")
        self.assertEqual(
            self.listing(), sorted([self.game.name, self.settings.name, "Taken Settings.app"])
        )
        self.assertFalse((self.root / "app-name").exists())

    def test_another_titles_names_are_refused(self):
        # TrackMania Settings.app is Nations Forever's Settings app.
        with self.assertRaisesRegex(ValueError, "TrackMania Nations Forever uses the name"):
            self.rename("TrackMania")
        other = hub_paths.title_root("tmnf")
        other.mkdir(parents=True)
        (other / "app-name").write_text("Café\n")
        for name in ("CAFÉ", "café"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "uses the name"):
                    self.rename(name)
        self.assertEqual(self.listing(), sorted([self.game.name, self.settings.name]))

    def test_a_failed_second_move_puts_the_first_back(self):
        real = app_rename._move

        def failing(source, target):
            if target.name == "TMU Settings.app":
                raise OSError("disk full")
            real(source, target)

        with mock.patch.object(app_rename, "_move", side_effect=failing):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.rename("TMU")
        self.assertEqual(self.listing(), sorted([self.game.name, self.settings.name]))
        self.assertFalse((self.root / "app-name").exists())

    def test_a_failed_name_write_puts_both_apps_back(self):
        inode = os.stat(self.game).st_ino
        with mock.patch.object(app_rename, "_write_name", side_effect=OSError("read-only")):
            with self.assertRaisesRegex(OSError, "read-only"):
                self.rename("TMU")
        self.assertEqual(self.listing(), sorted([self.game.name, self.settings.name]))
        self.assertEqual(os.stat(self.game).st_ino, inode)
        self.assertFalse((self.root / "app-name").exists())

    def test_apps_that_are_not_ours_are_refused(self):
        foreign = self.apps / "foreign"
        make_app(foreign / self.title.app_name, "com.example.other")
        unmarked = self.apps / "unmarked"
        make_app(unmarked / self.title.app_name, self.title.bundle_id_game, marker=False)
        linked = self.apps / "linked"
        linked.mkdir()
        (linked / self.title.app_name).symlink_to(self.game)
        for folder, message in (
            (foreign, "another app"),
            (unmarked, "not an app"),
            (linked, "symlink"),
        ):
            with self.subTest(folder=folder.name):
                with self.assertRaisesRegex(ValueError, message):
                    self.rename(
                        "TMU",
                        game=folder / self.title.app_name,
                        settings=folder / self.title.settings_app_name,
                    )
        with self.assertRaisesRegex(ValueError, "does not end in"):
            self.rename("TMU", game=self.apps / "Other.app")
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.rename("TMU", game=self.home / self.title.app_name)
        self.assertFalse((self.root / "app-name").exists())
        self.assertTrue(self.game.is_dir() and not self.game.is_symlink())

    def test_command_line(self):
        def run(*arguments):
            return subprocess.run(
                [sys.executable, "-m", "trackmania_mac.guard.app_rename", "rename", *arguments],
                env=dict(os.environ, PYTHONPATH=str(ROOT / "src/python")),
                text=True,
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT,
            )

        paths = (str(self.root), str(self.game), str(self.settings))
        result = run(*paths, "--name=-My game")
        self.assertEqual(result.returncode, 0, result.stderr)
        game = self.apps / "-My game.app"
        self.assertIn(f"_trackmania_mac_renamed_app={shlex.quote(str(game))}\n", result.stdout)
        self.assertIn("_trackmania_mac_renamed=1\n", result.stdout)
        result = run(*paths, "--name=X")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        self.assertNotEqual(run(*paths).returncode, 0)
        self.assertNotEqual(run(*paths, "--name=X", "--reset").returncode, 0)


if __name__ == "__main__":
    unittest.main()
