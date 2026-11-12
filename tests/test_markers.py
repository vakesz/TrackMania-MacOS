"""Marker files of this release and of earlier ones.

New names are written before anything old is removed, so a prefix or an app is
ours at every instant; two names that disagree make it not ours. Every fixture
lives under a temporary folder in /private/tmp.
"""

from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trackmania_mac import legacy, markers
from trackmania_mac.guard import managed_paths, uninstall
from trackmania_mac.hub import registry, state

ROOT = Path(__file__).resolve().parents[1]
TITLE = registry.get("tmnf", ROOT / "config/titles")


class Crash(BaseException):
    """The process dies here."""


class PrefixMarkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-markers-"
        )
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name).resolve() / "prefix"
        self.prefix.mkdir()
        (self.prefix / "system.reg").write_text("WINE REGISTRY Version 2\n")
        (self.prefix / legacy.MARKERS["PREFIX"]).write_text(TITLE.prefix_marker + "\n")
        (self.prefix / legacy.MARKERS["DIRECTX"]).write_text("digest\n")

    def check(self):
        managed_paths.check_prefix(str(self.prefix), title=TITLE.id)

    def test_an_interrupted_adopt_keeps_the_prefix_ours_and_finishes_next_time(self):
        with mock.patch.object(managed_paths, "rename_exclusive", side_effect=Crash()):
            with self.assertRaises(Crash):
                managed_paths.adopt_prefix_markers(self.prefix, title=TITLE.id)
        self.assertFalse((self.prefix / markers.PREFIX).exists())
        self.assertTrue((self.prefix / legacy.MARKERS["PREFIX"]).is_file())
        self.check()  # Still ours, through the earlier name.
        self.assertTrue(state._prefix_ready(TITLE, self.prefix).ready)
        adopted = managed_paths.adopt_prefix_markers(self.prefix, title=TITLE.id)
        self.assertEqual(adopted, ["PREFIX", "DIRECTX"])
        self.assertEqual((self.prefix / markers.PREFIX).read_text(), TITLE.prefix_marker + "\n")
        # The earlier names stay for a launcher of that release.
        self.assertTrue((self.prefix / legacy.MARKERS["PREFIX"]).is_file())
        self.assertTrue((self.prefix / legacy.MARKERS["DIRECTX"]).is_file())
        self.check()
        self.assertEqual(managed_paths.adopt_prefix_markers(self.prefix, title=TITLE.id), [])

    def test_names_that_disagree_make_the_prefix_not_ours(self):
        (self.prefix / markers.PREFIX).write_text("tmu:1\n")
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.check()
        with self.assertRaisesRegex(ValueError, "disagree"):
            managed_paths.adopt_prefix_markers(self.prefix, title=TITLE.id)
        self.assertFalse(state._prefix_ready(TITLE, self.prefix).ready)

    def test_a_symlink_under_either_name_is_refused(self):
        for name in (markers.PREFIX, legacy.MARKERS["PREFIX"]):
            with self.subTest(name=name):
                path = self.prefix / name
                if path.exists():
                    path.unlink()
                path.symlink_to(self.prefix / "system.reg")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.check()
                path.unlink()
                (self.prefix / legacy.MARKERS["PREFIX"]).write_text(TITLE.prefix_marker + "\n")

    def test_only_the_new_name_is_enough(self):
        (self.prefix / legacy.MARKERS["PREFIX"]).rename(self.prefix / markers.PREFIX)
        self.check()


class AppMarkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-markers-"
        )
        self.addCleanup(temporary.cleanup)
        self.app = Path(temporary.name).resolve() / TITLE.app_name
        contents = self.app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        executable = contents / "MacOS/TMNF"
        executable.write_text("#!/bin/bash\n")
        executable.chmod(0o755)
        self.contents = contents

    def write(self, identifier, *names):
        with (self.contents / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleIdentifier": identifier}, stream)
        for name in names:
            (self.contents / name).touch()

    def test_a_wrapper_with_either_or_both_markers_and_either_id_is_ours(self):
        cases = (
            (TITLE.legacy_bundle_id_game, (legacy.MARKERS["APP"],)),
            # Interrupted between writing the new marker and removing the old one.
            (TITLE.bundle_id_game, (markers.APP, legacy.MARKERS["APP"])),
            (TITLE.bundle_id_game, (markers.APP,)),
        )
        for identifier, names in cases:
            with self.subTest(identifier=identifier, names=names):
                for name in (markers.APP, legacy.MARKERS["APP"]):
                    if (self.contents / name).exists():
                        (self.contents / name).unlink()
                self.write(identifier, *names)
                entry = uninstall.app_entry(str(self.app), TITLE, "game-app")
                self.assertEqual(entry.verb, "remove", entry)
                self.assertTrue(state._app_ready(self.app))

    def test_the_executable_the_plist_names_must_exist_and_be_ours(self):
        old = self.contents / "MacOS" / legacy.WRAPPER_EXECUTABLE  # written by setUp
        new = self.contents / "MacOS" / markers.WRAPPER_EXECUTABLE
        cases = (
            ({"CFBundleExecutable": markers.WRAPPER_EXECUTABLE}, False),  # named but missing
            ({"CFBundleExecutable": legacy.WRAPPER_EXECUTABLE}, True),
            ({}, True),  # no key: whichever of the two exists
            ({"CFBundleExecutable": "Other"}, False),
        )
        for extra, ready in cases:
            with self.subTest(extra=extra):
                with (self.contents / "Info.plist").open("wb") as stream:
                    plistlib.dump({"CFBundleIdentifier": TITLE.bundle_id_game, **extra}, stream)
                (self.contents / markers.APP).touch()
                self.assertEqual(state._app_ready(self.app), ready)
        new.write_text("#!/bin/bash\n")
        new.chmod(0o755)
        old.unlink()
        with (self.contents / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": markers.WRAPPER_EXECUTABLE}, stream)
        self.assertEqual(uninstall.wrapper_executable(self.contents), new)

    def test_another_titles_id_or_no_marker_is_not_ours(self):
        self.write("local.tmnf.tmu.game", legacy.MARKERS["APP"])
        self.assertEqual(uninstall.app_entry(str(self.app), TITLE, "game-app").verb, "keep")
        (self.contents / legacy.MARKERS["APP"]).unlink()
        self.write(TITLE.bundle_id_game)
        self.assertEqual(uninstall.app_entry(str(self.app), TITLE, "game-app").verb, "keep")
        self.assertFalse(state._app_ready(self.app))
        (self.contents / markers.APP).symlink_to(self.contents / "Info.plist")
        self.assertEqual(uninstall.app_entry(str(self.app), TITLE, "game-app").verb, "keep")

    def test_an_earlier_releases_tombstone_is_still_recognised(self):
        for name in (".prefix.tmnf-removing.0123abcd", ".prefix.trackmania-mac-removing.0123abcd"):
            self.assertTrue(uninstall.TOMBSTONE.fullmatch(name), name)
        self.assertIn(markers.TOMBSTONE_INFIX, uninstall.tombstone_name("prefix"))


if __name__ == "__main__":
    unittest.main()
