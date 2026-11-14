"""Per-title uninstall: the guard plan, the shell removal and the hub preview.

Every fixture lives under a temporary HOME in /private/tmp (a managed root);
nothing here touches the real ~/Library or /Applications.
"""

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from trackmania_mac.guard import operation_guard, uninstall
from trackmania_mac.hub import registry


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/trackmania-mac"

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


def faked_device(info, device):
    """INFO with another st_dev, as a mount point would report."""
    values = list(info)
    values[2] = device
    return os.stat_result(values)


class Fixture:
    """A HOME with the data folder layout: games/<id>, saves/<id>, cache and logs."""

    def __init__(self, home):
        self.home = home
        self.data = home / "Library/TrackManiaMacOS"
        self.support = self.data
        self.cache = self.data / "cache"
        self.logs = self.data / "logs"
        self.apps = home / "Applications"
        for directory in (self.support / "runtime/1", self.cache, self.logs, self.apps):
            directory.mkdir(parents=True)

    def root(self, title_id):
        return self.data / "games" / title_id

    def prefix(self, title_id):
        return self.root(title_id) / "prefix"

    def userdata(self, title_id):
        return self.data / "saves" / title_id

    def save(self, title_id):
        """The replay install(saves=True) writes, in the title's first save folder."""
        return self.userdata(title_id) / TITLES[title_id].save_dirs[0] / "Replays/race.Gbx"

    def install(self, title_id, saves=True, apps=True):
        title = TITLES[title_id]
        prefix = self.prefix(title_id)
        documents = prefix / "drive_c/users/player/Documents"
        documents.parent.mkdir(parents=True)
        (prefix / ".trackmania-mac-prefix").write_text(title.prefix_marker + "\n")
        (prefix / "system.reg").write_text("WINE REGISTRY Version 2\n")
        userdata = self.userdata(title_id)
        userdata.mkdir(parents=True)
        documents.symlink_to(userdata, target_is_directory=True)
        if saves:
            save = self.save(title_id)
            save.parent.mkdir(parents=True)
            save.write_text("keep")
        if apps:
            make_app(self.apps / title.app_name, title.bundle_id_game)
            make_app(self.apps / title.settings_app_name, title.bundle_id_settings)
            launcher = self.support / "launchers" / title_id
            launcher.mkdir(parents=True)
            (launcher / ".trackmania-mac-launcher").touch()
        return prefix

    def arguments(self, title_id, **overrides):
        title = TITLES[title_id]
        values = dict(
            prefix=str(self.prefix(title_id)),
            userdata=str(self.userdata(title_id)),
            app=str(self.apps / title.app_name),
            settings_app=str(self.apps / title.settings_app_name),
            launcher=str(self.support / "launchers" / title_id),
            support=str(self.support),
            cache=str(self.cache),
        )
        values.update(overrides)
        return values


class GuardPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-uninstall-"
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)
        environment = {
            key: value for key, value in os.environ.items() if key != "TRACKMANIA_MAC_TITLES_DIR"
        }
        environment.update(HOME=str(self.home), TRACKMANIA_MAC_TITLE="tmnf")
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, title_id="tmnf", include_saves=False, warnings=None, **overrides):
        os.environ["TRACKMANIA_MAC_TITLE"] = title_id
        return uninstall.plan(
            **self.fx.arguments(title_id, **overrides),
            include_saves=include_saves,
            warnings=warnings,
        )

    @staticmethod
    def removed(entries):
        return [entry.path for entry in entries if entry.verb == "remove"]

    @staticmethod
    def kept(entries):
        return {entry.path: entry for entry in entries if entry.verb == "keep"}

    def test_removal_never_reaches_another_titles_folders(self):
        self.fx.install("tmnf")
        for other in ("tmu", "tm-canyon"):
            self.fx.install(other)
        removed = self.removed(self.plan(include_saves=True))
        root = self.fx.root("tmnf")
        self.assertIn(root / "prefix", removed)
        self.assertIn(self.fx.userdata("tmnf"), removed)
        self.assertNotIn(root, removed)
        for path in removed:
            for other in ("tmu", "tm-canyon"):
                for folder in (self.fx.root(other), self.fx.userdata(other)):
                    self.assertFalse(
                        path == folder or folder in path.parents or path in folder.parents,
                        path,
                    )

    def test_other_titles_stay_inside_their_own_root(self):
        self.fx.install("tmnf")
        self.fx.install("tmu")
        removed = self.removed(self.plan("tmu", include_saves=True))
        root = self.fx.root("tmu")
        for path in removed:
            if root in path.parents:
                continue
            self.assertNotIn(self.fx.root("tmnf") / "prefix", [path, *path.parents])
            self.assertNotIn(self.fx.userdata("tmnf"), [path, *path.parents])
        self.assertIn(root / "prefix", removed)
        self.assertIn(self.fx.userdata("tmu"), removed)

    def test_prefix_at_a_shared_root_is_refused(self):
        self.fx.install("tmu", apps=False)
        root = self.fx.root("tmnf")
        root.mkdir(parents=True)
        (root / ".trackmania-mac-prefix").write_text("tmnf:1\n")
        (root / "system.reg").write_text("x")
        elsewhere = self.home / "saves"
        with self.assertRaisesRegex(ValueError, "Refusing to remove .*games/tmnf:"):
            self.plan(prefix=str(root), userdata=str(elsewhere))

    def test_registry_nesting_and_broken_entries_fail_closed(self):
        self.fx.install("tmnf")
        titles = self.home / "titles"
        shutil.copytree(ROOT / "config/titles", titles)
        data = json.loads((titles / "tm-turbo.json").read_text())
        os.environ["TRACKMANIA_MAC_TITLES_DIR"] = str(titles)
        # Case differs: APFS would put this old root inside tmnf's old saves folder.
        for subdir in ("TMNF/UserData/evil", "tmnf/userdata/evil", "tmnf/PREFIX/x", "tmnf"):
            with self.subTest(subdir=subdir):
                data["legacy_prefix_subdir"] = subdir
                (titles / "tm-turbo.json").write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "Fix the title registry.*tm-turbo"):
                    self.plan(include_saves=True)
        (titles / "tm-turbo.json").write_text("{not json")
        with self.assertRaisesRegex(ValueError, "Fix the title registry"):
            self.plan()

    def test_symlinked_prefix_or_saves_folder_is_refused(self):
        real = self.home / "real-prefix"
        real.mkdir()
        (real / ".trackmania-mac-prefix").write_text("tmnf:1\n")
        (real / "system.reg").write_text("x")
        self.fx.root("tmnf").mkdir(parents=True)
        self.fx.prefix("tmnf").symlink_to(real)
        with self.assertRaises(ValueError):
            self.plan()
        self.fx.prefix("tmnf").unlink()
        outside = self.home / "outside-saves"
        outside.mkdir()
        self.fx.userdata("tmnf").parent.mkdir(parents=True, exist_ok=True)
        self.fx.userdata("tmnf").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.plan(include_saves=True)

    def test_saves_folder_reached_through_a_link_out_of_home_is_refused(self):
        with tempfile.TemporaryDirectory() as foreign:  # $TMPDIR is not a managed root
            target = Path(foreign).resolve()
            (target / "tmnf").mkdir()
            self.fx.data.mkdir(parents=True, exist_ok=True)
            (self.fx.data / "saves").symlink_to(target)
            with self.assertRaises(ValueError):
                self.plan(include_saves=True)
            self.assertTrue((target / "tmnf").is_dir())

    def test_apps_that_are_not_ours_are_kept(self):
        self.fx.install("tmnf", apps=False)
        title = TITLES["tmnf"]
        game = self.fx.apps / title.app_name
        make_app(game, "com.example.other", marker=False)
        settings_target = make_app(self.home / "elsewhere.app", title.bundle_id_settings)
        (self.fx.apps / title.settings_app_name).symlink_to(settings_target)
        kept = self.kept(self.plan())
        self.assertIn("not an app this hub installed", kept[game].reason)
        self.assertIn("symlink", kept[self.fx.apps / title.settings_app_name].reason)
        self.assertTrue((settings_target / "Contents/.trackmania-mac-app").exists())

    def test_other_titles_wrapper_renamed_or_legacy_launcher_is_kept(self):
        self.fx.install("tmnf", apps=False)
        title = TITLES["tmnf"]
        game = make_app(self.fx.apps / title.app_name, TITLES["tmu"].bundle_id_game)
        settings = make_app(
            self.fx.apps / title.settings_app_name, uninstall.LEGACY_APP_ID, marker=False
        )
        kept = self.kept(self.plan())
        self.assertIn("another app", kept[game].reason)
        self.assertIn("older launcher", kept[settings].reason)
        marker = make_app(self.home / "x.app", title.bundle_id_game)
        shutil.rmtree(game)
        make_app(game, title.bundle_id_game, marker=False)
        (game / "Contents/.trackmania-mac-app").symlink_to(marker / "Contents/.trackmania-mac-app")
        self.assertIn(game, self.kept(self.plan()))

    def test_managed_apps_launcher_icon_and_logs_are_removed(self):
        self.fx.install("tmnf")
        icon = self.fx.support / "icons/tmnf.icns"
        icon.parent.mkdir()
        icon.write_bytes(b"icns")
        for name in ("play.log", "play.log.1", "configure.log", "play.log.lock"):
            (self.fx.logs / name).write_text("x")
        (self.fx.logs / "tmu-play.log").write_text("x")
        (self.fx.logs / "diagnostics-tmnf-20260101-120000.zip").write_text("x")
        (self.fx.logs / "diagnostics-tmu-20260101-120000.zip").write_text("x")
        removed = self.removed(self.plan())
        title = TITLES["tmnf"]
        for path in (
            self.fx.apps / title.app_name,
            self.fx.apps / title.settings_app_name,
            self.fx.support / "launchers/tmnf",
            icon,
            self.fx.logs / "play.log",
            self.fx.logs / "play.log.1",
            self.fx.logs / "configure.log",
            self.fx.logs / "diagnostics-tmnf-20260101-120000.zip",
        ):
            self.assertIn(path, removed)
        for path in (
            self.fx.logs / "play.log.lock",
            self.fx.logs / "tmu-play.log",
            self.fx.logs / "diagnostics-tmu-20260101-120000.zip",
            self.fx.support / "runtime",
            self.fx.support,
            self.fx.logs,
        ):
            self.assertNotIn(path, removed)
        # Removal order: apps first, so a half-removed game cannot be launched.
        self.assertEqual(removed[0], self.fx.apps / title.app_name)

    def test_logs_match_exact_names_only(self):
        self.fx.install("tm-canyon", saves=False, apps=False)
        for name in ("tm-canyon-play.log", "tm-canyon-play.log.3", "tm-play.log", "play.log"):
            (self.fx.logs / name).write_text("x")
        target = self.home / "outside.log"
        target.write_text("keep")
        (self.fx.logs / "tm-canyon-configure.log").symlink_to(target)
        entries = self.plan("tm-canyon")
        removed = self.removed(entries)
        self.assertEqual(
            sorted(path.name for path in removed if path.parent == self.fx.logs),
            ["tm-canyon-play.log", "tm-canyon-play.log.3"],
        )
        self.assertIn(self.fx.logs / "tm-canyon-configure.log", self.kept(entries))

    def test_launcher_cache_and_icon_symlinks_are_kept(self):
        self.fx.install("tmu", apps=False)
        outside = self.home / "outside"
        outside.mkdir()
        (outside / ".trackmania-mac-launcher").touch()
        (self.fx.support / "launchers").mkdir()
        (self.fx.support / "launchers/tmu").symlink_to(outside)
        (self.fx.cache / "games").mkdir()
        (self.fx.cache / "games/tmu").symlink_to(outside)
        (self.fx.support / "icons").mkdir()
        (self.fx.support / "icons/tmu.icns").symlink_to(outside / ".trackmania-mac-launcher")
        entries = self.plan("tmu")
        kept = self.kept(entries)
        for path in ("launchers/tmu", "icons/tmu.icns"):
            self.assertIn(self.fx.support / path, kept)
        self.assertIn(self.fx.cache / "games/tmu", kept)
        self.assertNotIn(outside, self.removed(entries))

    def test_cached_installers_never_include_shared_downloads(self):
        self.fx.install("tmnf", apps=False)
        shared = sorted(uninstall.shared_cache_names() - {".app-install.lock"})
        for name in shared + [
            "tmnationsforever_setup.exe",
            "tmnationsforever_setup.exe.partial.Ab12Cd",
        ]:
            (self.fx.cache / name).write_text("x")
        tmu = self.fx.cache / "games/tmu"
        tmu.mkdir(parents=True)
        (tmu / "tmunitedforever_setup.exe").write_text("x")
        canyon = self.fx.cache / "games/tm-canyon"
        canyon.mkdir()
        removed = self.removed(self.plan())
        self.assertIn(self.fx.cache / "tmnationsforever_setup.exe", removed)
        self.assertIn(self.fx.cache / "tmnationsforever_setup.exe.partial.Ab12Cd", removed)
        for name in shared:
            self.assertNotIn(self.fx.cache / name, removed)
        self.assertFalse(any(tmu in path.parents or path == tmu for path in removed))
        self.fx.install("tmu", apps=False)
        removed = self.removed(self.plan("tmu"))
        self.assertIn(tmu / "tmunitedforever_setup.exe", removed)
        # The folder has no ownership marker: it is never removed wholesale.
        self.assertNotIn(tmu, removed)
        self.assertNotIn(canyon, removed)
        self.assertNotIn(self.fx.cache / "games", removed)

    def test_title_cache_folder_keeps_unknown_files(self):
        self.fx.install("tmu", apps=False)
        tmu = self.fx.cache / "games/tmu"
        (tmu / "tmunitedforever_setup.inner").mkdir(parents=True)
        (tmu / "tmunitedforever_setup.inner/setup.bin").write_text("x")
        (tmu / "tmunitedforever_setup.exe").write_text("x")
        (tmu / "tmunitedforever_setup.exe.partial.Zz9Yy8").write_text("x")
        (tmu / "notes.txt").write_text("keep")
        uninstall.remove(self.plan("tmu"), report=lambda line: None)
        self.assertEqual(sorted(os.listdir(tmu)), ["notes.txt"])
        (tmu / "notes.txt").unlink()
        (tmu / "tmunitedforever_setup.exe").write_text("x")
        uninstall.remove(self.plan("tmu"), report=lambda line: None)
        self.assertFalse(tmu.exists())
        self.assertTrue((self.fx.cache / "games").is_dir())

    def test_title_cache_folder_includes_updates_and_overlay(self):
        self.fx.install("tmn-eswc", apps=False)
        eswc = self.fx.cache / "games/tmn-eswc"
        eswc.mkdir(parents=True)
        names = [
            "TmNationsESWC_Setup.exe",
            "TmNationsESWC_Setup.exe.partial.Ab12Cd",
            "tmnationseswc_175_to_179_setup.exe",
            "tmnationseswc_175_to_179_setup.exe.partial.Zz9Yy8",
            "Win10Patch.tar.gz",
            "Win10Patch.tar.gz.partial.Qq1Ww2",
            "notes.txt",
        ]
        for name in names:
            (eswc / name).write_text("x")
        uninstall.remove(self.plan("tmn-eswc"), report=lambda line: None)
        self.assertEqual(sorted(os.listdir(eswc)), ["notes.txt"])
        (eswc / "notes.txt").unlink()
        (eswc / "Win10Patch.tar.gz").write_text("x")
        uninstall.remove(self.plan("tmn-eswc"), report=lambda line: None)
        self.assertFalse(eswc.exists())

    def test_extra_downloads_of_a_title_whose_installer_is_shared_sit_in_its_folder(self):
        update = registry.Download("https://example.com/u/update.exe", "1" * 64, 5)
        title = TITLES["tmnf"]._replace(updates=(update,))
        (self.fx.cache / "tmnationsforever_setup.exe").write_text("x")
        folder = self.fx.cache / "games/tmnf"
        folder.mkdir(parents=True)
        (folder / "update.exe").write_text("x")
        self.assertEqual(
            uninstall.download_cache_paths(title, self.fx.cache),
            [
                (self.fx.cache / "tmnationsforever_setup.exe", "installer"),
                (folder / "update.exe", "update"),
            ],
        )
        removed = self.removed(uninstall.cache_entries(title, self.fx.cache))
        self.assertIn(self.fx.cache / "tmnationsforever_setup.exe", removed)
        self.assertIn(folder.resolve() / "update.exe", removed)

    def test_keeping_saves_still_refuses_saves_inside_the_prefix(self):
        prefix = self.fx.install("tmnf", saves=False)
        documents = prefix / "drive_c/users/player/Documents"
        documents.unlink()
        (documents / "TmForever").mkdir(parents=True)
        (documents / "TmForever/settings").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Back up"):
            self.plan()
        removed = self.removed(self.plan(include_saves=True))
        self.assertIn(prefix, removed)

    def test_keeping_saves_reports_the_saves_folder(self):
        self.fx.install("tmnf")
        entries = self.plan()
        self.assertNotIn(self.fx.userdata("tmnf"), self.removed(entries))
        self.assertEqual(self.kept(entries)[self.fx.userdata("tmnf")].kind, "saves")

    def test_saves_outside_the_managed_folders_are_reported_not_removed(self):
        prefix = self.fx.install("tmnf", saves=False)
        documents = prefix / "drive_c/users/player/Documents"
        documents.unlink()
        outside = self.home / "Documents"
        (outside / "TmForever").mkdir(parents=True)
        documents.symlink_to(outside)
        entries = self.plan(include_saves=True)
        self.assertEqual(self.kept(entries)[outside].kind, "saves-elsewhere")
        self.assertNotIn(outside, self.removed(entries))

    def test_unreadable_user_reg_warns_only_when_removing_saves(self):
        prefix = self.fx.install("tmnf")
        (prefix / "user.reg").write_bytes(
            b"[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Shell Folders]\n"
            b'"Personal"=dword:00000001\n'
        )
        with self.assertRaisesRegex(ValueError, "Documents location"):
            self.plan()
        warnings = []
        self.assertIn(prefix, self.removed(self.plan(include_saves=True, warnings=warnings)))
        self.assertTrue(any("Documents" in warning for warning in warnings))

    def test_custom_saves_folder_is_never_removed(self):
        self.fx.install("tmnf")
        custom = self.home / "MySaves"
        custom.mkdir()
        with self.assertRaisesRegex(ValueError, "custom folder"):
            self.plan(include_saves=True, userdata=str(custom))
        self.assertNotIn(custom, self.removed(self.plan(userdata=str(custom))))

    def test_saves_folder_shared_with_another_titles_prefix_is_refused(self):
        self.fx.install("tmnf")
        other = self.fx.install("tmu", apps=False)
        documents = other / "drive_c/users/player/Documents"
        documents.unlink()
        documents.symlink_to(self.fx.userdata("tmnf"))
        with self.assertRaisesRegex(ValueError, "United"):
            self.plan(include_saves=True)

    def test_missing_prefix_still_plans_the_apps(self):
        self.fx.install("tmnf")
        shutil.rmtree(self.fx.prefix("tmnf"))
        removed = self.removed(self.plan())
        self.assertIn(self.fx.apps / TITLES["tmnf"].app_name, removed)
        self.assertNotIn(self.fx.prefix("tmnf"), removed)

    def test_prefix_of_another_title_is_refused(self):
        self.fx.install("tmnf")
        (self.fx.prefix("tmnf") / ".trackmania-mac-prefix").write_text("tmu:1\n")
        with self.assertRaisesRegex(ValueError, "another TrackMania title"):
            self.plan()

    def run_main(self, *arguments):
        output = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: output.append(a[0])):
            status = uninstall.main(list(arguments))
        return status, output

    def test_plan_command_describes_and_removes_nothing(self):
        self.fx.install("tmnf")
        arguments = self.fx.arguments("tmnf").values()
        status, output = self.run_main("plan", "--include-saves", *arguments)
        self.assertEqual(status, 0)
        self.assertEqual(output[0], "This removes:")
        self.assertIn("  {}".format(self.fx.userdata("tmnf")), output)
        self.assertTrue(self.fx.prefix("tmnf").is_dir())
        status, output = self.run_main("remove", "--include-saves", *arguments)
        self.assertEqual(status, 0)
        self.assertIn("Removed {}".format(self.fx.userdata("tmnf")), output)
        self.assertFalse(self.fx.prefix("tmnf").exists())
        status, output = self.run_main("plan", *arguments)
        self.assertEqual(
            (status, output), (uninstall.NOTHING_TO_REMOVE, ["Nothing is left to remove."])
        )

    def test_hostile_bundle_identifier_is_quoted_and_never_a_removal(self):
        self.fx.install("tmnf", apps=False)
        hostile = "x\nremove\tz\t{}\tq".format(self.home)
        make_app(self.fx.apps / TITLES["tmnf"].app_name, hostile)
        entries = self.plan()
        kept = self.kept(entries)[self.fx.apps / TITLES["tmnf"].app_name]
        self.assertIn(repr(hostile), kept.reason)
        self.assertNotIn("\n", kept.describe())
        self.assertNotIn(self.home, self.removed(entries))

    def test_control_characters_in_resolved_paths_fail_closed(self):
        self.fx.install("tmnf")
        hostile = self.home / "evil\nremove\tz\tq"
        for linked in (self.fx.apps, self.fx.support / "launchers"):
            with self.subTest(linked=linked.name):
                hostile.mkdir()
                for child in os.listdir(linked):
                    os.rename(linked / child, hostile / child)
                linked.rmdir()
                linked.symlink_to(hostile)
                try:
                    with self.assertRaisesRegex(ValueError, "Unsafe path"):
                        self.plan()
                finally:
                    linked.unlink()
                    hostile.rename(linked)

    def test_interrupted_removal_is_finished_next_time(self):
        self.fx.install("tmnf")
        root = self.fx.root("tmnf")
        # A removal that stopped after deleting the marker, before the rest.
        tomb = root / ".prefix.trackmania-mac-removing.0123abcd"
        self.fx.prefix("tmnf").rename(tomb)
        (tomb / ".trackmania-mac-prefix").unlink()
        app_tomb = self.fx.apps / ".TrackMania Settings.app.trackmania-mac-removing.89abcdef"
        (self.fx.apps / TITLES["tmnf"].settings_app_name).rename(app_tomb)
        foreign = root / ".prefix.trackmania-mac-removing.notours"
        foreign.mkdir()
        entries = self.plan()
        self.assertIn(tomb, self.removed(entries))
        self.assertIn(app_tomb, self.removed(entries))
        uninstall.remove(entries, report=lambda line: None)
        self.assertFalse(tomb.exists())
        self.assertFalse(app_tomb.exists())
        self.assertTrue(foreign.is_dir())
        self.assertEqual(self.removed(self.plan()), [])

    def test_removal_renames_to_a_tombstone_before_deleting(self):
        prefix = self.fx.install("tmnf")
        entries = [entry for entry in self.plan() if entry.kind == "prefix"]

        def stop(*args, **kwargs):
            raise OSError("interrupted")

        with mock.patch.object(uninstall.shutil, "rmtree", side_effect=stop):
            with self.assertRaises(OSError):
                uninstall.remove(entries, report=lambda line: None)
        self.assertFalse(prefix.exists())
        tombs = [n for n in os.listdir(self.fx.root("tmnf")) if "-removing." in n]
        self.assertEqual(len(tombs), 1)
        self.assertRegex(tombs[0], r"^\.prefix\.trackmania-mac-removing\.[0-9a-f]{8}$")
        shutil.rmtree(self.fx.root("tmnf") / tombs[0])

    def test_removal_refuses_a_parent_or_entry_changed_after_planning(self):
        self.fx.install("tmnf")
        entries = self.plan()
        decoy = self.home / "decoy"
        self.fx.apps.rename(decoy)
        self.fx.apps.symlink_to(decoy)
        with self.assertRaises(OSError):  # O_NOFOLLOW: the parent is now a symlink
            uninstall.remove(entries, report=lambda line: None)
        self.assertTrue((decoy / TITLES["tmnf"].app_name).is_dir())
        self.fx.apps.unlink()
        decoy.rename(self.fx.apps)
        entries = self.plan()
        app = self.fx.apps / TITLES["tmnf"].app_name
        shutil.rmtree(app)
        make_app(app, TITLES["tmnf"].bundle_id_game)
        with self.assertRaisesRegex(ValueError, "changed after it was checked"):
            uninstall.remove(entries, report=lambda line: None)
        self.assertTrue(app.is_dir())

    def test_removal_needs_a_symlink_safe_rmtree_and_one_volume(self):
        self.fx.install("tmnf")
        entries = self.plan()
        with mock.patch.object(uninstall.shutil.rmtree, "avoids_symlink_attacks", False):
            with self.assertRaisesRegex(RuntimeError, "nothing was removed"):
                uninstall.remove(entries, report=lambda line: None)
        self.assertTrue(self.fx.prefix("tmnf").is_dir())
        root = os.open(self.fx.root("tmnf"), os.O_RDONLY | os.O_DIRECTORY)
        try:
            device = os.lstat(self.fx.prefix("tmnf")).st_dev
            uninstall._same_volume(root, self.fx.root("tmnf"), "prefix", device)
            with self.assertRaisesRegex(ValueError, "contains a mounted volume at /"):
                uninstall._same_volume(root, self.fx.root("tmnf"), "prefix", device + 1)
        finally:
            os.close(root)

    def test_paths_are_compared_by_identity_not_spelling(self):
        probe = self.home / "CaseProbe"
        probe.mkdir()
        if not (self.home / "caseprobe").is_dir():
            self.skipTest("this volume is case-sensitive")
        self.fx.install("tmu", apps=False)
        root = self.fx.root("tmnf")
        root.mkdir(parents=True)
        (root / ".trackmania-mac-prefix").write_text("tmnf:1\n")
        (root / "system.reg").write_text("x")
        case_changed = str(root).replace("TrackManiaMacOS/games/tmnf", "trackmaniamacos/GAMES/TMNF")
        with self.assertRaisesRegex(ValueError, "Refusing to remove"):
            self.plan(prefix=case_changed, userdata=str(self.home / "saves"))
        (root / ".trackmania-mac-prefix").unlink()
        (root / "system.reg").unlink()
        self.fx.install("tmnf")
        userdata = str(self.fx.userdata("tmnf")).replace("saves/tmnf", "SAVES/TMNF")
        removed = self.removed(self.plan(include_saves=True, userdata=userdata))
        self.assertTrue(any(os.path.samefile(path, self.fx.userdata("tmnf")) for path in removed))

    def test_mount_point_at_a_planned_path_is_kept_not_emptied(self):
        self.fx.install("tmnf")
        real_lstat = os.lstat
        mounted = {str(self.fx.userdata("tmnf")), str(self.fx.prefix("tmnf"))}

        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            return faked_device(info, info.st_dev + 1) if str(path) in mounted else info

        with mock.patch.object(uninstall.os, "lstat", side_effect=lstat):
            entries = self.plan(include_saves=True)
            removal = PreviewTests.preview_for(self.fx, "tmnf")
        kept = self.kept(entries)
        for path in mounted:
            self.assertEqual(kept[Path(path)].reason, uninstall.MOUNTED)
            self.assertNotIn(Path(path), self.removed(entries))
        self.assertIsNone(removal["saves"])
        self.assertNotIn("prefix", [item["kind"] for item in removal["items"]])
        # At removal time too: an entry on another device than its parent is refused.
        userdata = self.fx.userdata("tmnf")
        info = real_lstat(userdata)
        entry = uninstall.Entry(
            "remove",
            "saves",
            userdata,
            identity=(info.st_dev + 1, info.st_ino),
            parent=uninstall._identity(real_lstat(userdata.parent)),
        )
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return faked_device(info, info.st_dev + 1) if path == userdata.name else info

        with mock.patch.object(uninstall.os, "stat", side_effect=stat):
            with self.assertRaisesRegex(ValueError, "mounted volume"):
                uninstall.remove([entry], report=lambda line: None)
        self.assertTrue(self.fx.save("tmnf").exists())

    def interrupt(self, entries, kind):
        """Remove everything before KIND, then stop while deleting KIND (real order)."""
        index = next(i for i, entry in enumerate(entries) if entry.kind == kind)
        uninstall.remove(entries[:index], report=lambda line: None)

        def stop(*args, **kwargs):
            raise OSError("interrupted")

        with mock.patch.object(uninstall.shutil, "rmtree", side_effect=stop):
            with self.assertRaises(OSError):
                uninstall.remove([e for e in entries if e.kind == kind], report=lambda line: None)
        return [
            folder / name
            for folder in (self.fx.root("tmnf"), self.fx.userdata("tmnf").parent)
            for name in os.listdir(folder)
            if "removing" in name
        ]

    def test_keeping_saves_restores_an_unfinished_uninstalls_saves(self):
        self.fx.install("tmnf")
        (tomb,) = self.interrupt(self.plan(include_saves=True), "saves")
        self.assertFalse(self.fx.userdata("tmnf").exists())
        self.assertEqual(PreviewTests.preview_for(self.fx, "tmnf")["saves"], str(tomb))
        entries = self.plan()
        self.assertNotIn(tomb, self.removed(entries))
        uninstall.remove(entries, report=lambda line: None)
        self.assertTrue((self.fx.save("tmnf")).exists())
        self.assertFalse(tomb.exists())
        # With the saves folder back in place, a second tombstone is kept, not deleted.
        second = self.fx.userdata("tmnf").parent / ".tmnf.trackmania-mac-removing.0123abcd"
        second.mkdir()
        kept = self.kept(self.plan())
        self.assertEqual(kept[second].reason, uninstall.UNFINISHED_SAVES)
        self.assertIn(second, self.removed(self.plan(include_saves=True)))

    def test_tombstones_match_any_spelling_of_the_folder(self):
        probe = self.home / "CaseProbe"
        probe.mkdir()
        if not (self.home / "caseprobe").is_dir():
            self.skipTest("this volume is case-sensitive")
        self.fx.install("tmnf")
        upper = str(self.fx.userdata("tmnf").parent / "TMNF")
        (tomb,) = self.interrupt(self.plan(include_saves=True, userdata=upper), "saves")
        self.assertTrue(tomb.name.startswith(".TMNF.trackmania-mac-removing."))
        self.assertIn(tomb, self.removed(self.plan(include_saves=True)))
        restore = [e for e in self.plan() if e.verb == "restore"]
        self.assertEqual([e.path for e in restore], [tomb])

    def test_paths_pointed_at_another_titles_root_never_touch_it(self):
        canyon = self.fx.install("tm-canyon")
        root = self.fx.root("tm-canyon")
        prefix_tomb = root / ".prefix.trackmania-mac-removing.0123abcd"
        canyon.rename(prefix_tomb)
        saves_tomb = (
            self.fx.userdata("tm-canyon").parent / ".tm-canyon.trackmania-mac-removing.89abcdef"
        )
        saves_tomb.mkdir()
        launcher_tomb = self.fx.support / "launchers/.tm-canyon.trackmania-mac-removing.11112222"
        launcher_tomb.mkdir()
        overrides = dict(
            prefix=str(canyon),
            userdata=str(self.fx.userdata("tm-canyon")),
            launcher=str(self.fx.support / "launchers/tm-canyon"),
        )
        removed = self.removed(self.plan(**overrides))
        for path in removed:
            self.assertFalse(root == path or root in path.parents, path)
        self.assertNotIn(saves_tomb, removed)
        self.assertNotIn(launcher_tomb, removed)
        self.assertNotIn(self.fx.support / "launchers/tm-canyon", removed)
        with self.assertRaisesRegex(ValueError, "custom folder"):
            self.plan(include_saves=True, **overrides)
        # Limits treats another title's root and saves folder as foreign.
        title, titles = uninstall.load_titles()
        bounds = uninstall.limits(
            title, titles, self.home, self.fx.support, self.fx.cache, self.fx.apps
        )
        with self.assertRaisesRegex(ValueError, "belongs to another title"):
            bounds.check(uninstall.Entry("remove", "prefix", prefix_tomb, identity=(0, 0)))
        os.environ["TRACKMANIA_MAC_TITLE"] = "tmu"
        self.fx.install("tmu", apps=False)
        title, titles = uninstall.load_titles()
        bounds = uninstall.limits(
            title, titles, self.home, self.fx.support, self.fx.cache, self.fx.apps
        )
        bounds.check(uninstall._remove("prefix", self.fx.prefix("tmu")))
        # Saves folders share one parent: another title's saves tombstone there
        # is neither removed nor restored, whichever saves folder is passed.
        os.environ["TRACKMANIA_MAC_TITLE"] = "tmnf"
        for include_saves in (False, True):
            entries = self.plan(include_saves=include_saves, prefix=str(canyon))
            self.assertNotIn(saves_tomb, [entry.path for entry in entries])
        self.assertTrue(saves_tomb.is_dir())

    @unittest.skipIf(sys.version_info < (3, 11), "rmtree(dir_fd=) needs Python 3.11")
    def test_folders_are_deleted_through_the_parent_descriptor(self):
        self.fx.install("tmnf")
        calls = []
        real = uninstall.shutil.rmtree

        def record(path, *args, **kwargs):
            calls.append((path, kwargs))
            return real(path, *args, **kwargs)

        with mock.patch.object(uninstall.shutil, "rmtree", side_effect=record):
            uninstall.remove(self.plan(), report=lambda line: None)
        self.assertTrue(calls)
        for path, kwargs in calls:
            self.assertIn("dir_fd", kwargs)
            self.assertRegex(path, r"^\.[^/]+\.trackmania-mac-removing\.[0-9a-f]{8}$")

    def test_names_with_a_trailing_newline_are_ignored(self):
        self.fx.install("tmnf")
        odd = [
            self.fx.root("tmnf") / ".prefix.trackmania-mac-removing.0123abcd\n",
            self.fx.logs / "diagnostics-tmnf-20260101-120000.zip\n",
            self.fx.cache / "tmnationsforever_setup.exe.partial.Ab12Cd\n",
        ]
        odd[0].mkdir()
        for path in odd[1:]:
            path.write_text("x")
        (self.fx.logs / "play.log").write_text("x")
        entries = self.plan()
        for path in odd:
            self.assertNotIn(path, [entry.path for entry in entries])
        self.assertIn(self.fx.logs / "play.log", self.removed(entries))

    def test_unicode_controls_fail_closed(self):
        for text in ("a\x85b", "a\u2028b", "a\u2029b", "a\u202eb", "a\u200bb", "a\x7fb"):
            self.assertFalse(uninstall._printable(text), repr(text))
        self.assertTrue(uninstall._printable("TrackMania Nations Forever.app é"))
        self.fx.install("tmnf")
        hostile = self.home / "apps\u202eppa"
        self.fx.apps.rename(hostile)
        self.fx.apps.symlink_to(hostile)
        with self.assertRaisesRegex(ValueError, "Unsafe path"):
            self.plan()

    def test_registry_override_cannot_move_a_title_root(self):
        # Roots come from the id; an override's old root only matters to the migration.
        self.fx.install("tmnf")
        titles = self.home / "titles"
        shutil.copytree(ROOT / "config/titles", titles)
        data = json.loads((titles / "tmnf.json").read_text())
        data["legacy_prefix_subdir"] = "Elsewhere/TMNF"
        (titles / "tmnf.json").write_text(json.dumps(data))
        os.environ["TRACKMANIA_MAC_TITLES_DIR"] = str(titles)
        removed = self.removed(self.plan())
        self.assertIn(self.fx.prefix("tmnf"), removed)

    def test_another_titles_documents_inside_the_prefix_block_its_removal(self):
        prefix = self.fx.install("tmnf")
        shared = prefix / "drive_c/shared"
        shared.mkdir()
        other = self.fx.install("tm-canyon", apps=False)
        documents = other / "drive_c/users/player/Documents"
        documents.unlink()
        documents.symlink_to(shared)
        for include_saves in (False, True):
            with self.subTest(include_saves=include_saves):
                with self.assertRaisesRegex(ValueError, "Canyon keeps its saves inside"):
                    self.plan(include_saves=include_saves)
        self.assertTrue(prefix.is_dir())

    def unfinished_saves(self):
        """The state an include-saves run leaves when it stops on the saves folder."""
        self.fx.install("tmnf")
        shutil.rmtree(self.fx.prefix("tmnf"))
        tomb = self.fx.userdata("tmnf").parent / ".tmnf.trackmania-mac-removing.0123abcd"
        self.fx.userdata("tmnf").rename(tomb)
        return tomb

    def test_restore_works_with_home_reached_through_a_symlink(self):
        if not Path("/tmp").is_symlink() or Path("/tmp").resolve() != Path("/private/tmp"):
            self.skipTest("/tmp is not a symlink to /private/tmp here")
        tomb = self.unfinished_saves()
        linked = "/tmp/" + self.home.name
        os.environ["HOME"] = linked
        arguments = {
            key: value.replace(str(self.home), linked)
            for key, value in self.fx.arguments("tmnf").items()
        }
        os.environ["TRACKMANIA_MAC_TITLE"] = "tmnf"
        entries = uninstall.plan(**arguments)
        uninstall.remove(entries, report=lambda line: None)
        self.assertFalse(tomb.exists())
        self.assertTrue((self.fx.save("tmnf")).exists())

    def test_restore_works_with_a_case_variant_root(self):
        probe = self.home / "CaseProbe"
        probe.mkdir()
        if not (self.home / "caseprobe").is_dir():
            self.skipTest("this volume is case-sensitive")
        tomb = self.unfinished_saves()
        variant = str(self.fx.data).replace("TrackManiaMacOS", "trackmaniamacos")
        entries = self.plan(prefix=variant + "/GAMES/TMNF/prefix", userdata=variant + "/SAVES/TMNF")
        uninstall.remove(entries, report=lambda line: None)
        self.assertFalse(tomb.exists())
        # Restored under the canonical spelling.
        self.assertIn("tmnf", os.listdir(self.fx.userdata("tmnf").parent))
        self.assertTrue((self.fx.save("tmnf")).exists())

    def test_volume_mounted_inside_is_reported_before_any_rename(self):
        prefix = self.fx.install("tmnf")
        inner = prefix / "drive_c"
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            return faked_device(info, info.st_dev + 1) if str(path) == str(inner) else info

        with mock.patch.object(uninstall.os, "lstat", side_effect=lstat):
            entries = self.plan()
        self.assertEqual(
            self.kept(entries)[prefix].reason,
            "contains a mounted volume at {}; unmount it first".format(inner),
        )
        entries = self.plan()
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return faked_device(info, info.st_dev + 1) if path == "drive_c" else info

        with mock.patch.object(uninstall.os, "stat", side_effect=stat):
            with self.assertRaisesRegex(ValueError, "mounted volume at {}".format(inner)):
                uninstall.remove(
                    [e for e in entries if e.kind == "prefix"], report=lambda line: None
                )
        self.assertTrue(prefix.is_dir())  # never renamed
        self.assertEqual([n for n in os.listdir(self.fx.root("tmnf")) if "removing" in n], [])

    def test_symlinked_root_does_not_make_another_root_ours(self):
        turbo = self.fx.root("tm-turbo")
        (turbo / "x").mkdir(parents=True)
        self.fx.root("tm-canyon").symlink_to(turbo)
        os.environ["TRACKMANIA_MAC_TITLE"] = "tm-canyon"
        title, titles = uninstall.load_titles()
        bounds = uninstall.limits(
            title, titles, self.home, self.fx.support, self.fx.cache, self.fx.apps
        )
        with self.assertRaisesRegex(ValueError, "belongs to another title"):
            bounds.check(uninstall._remove("prefix", turbo / "x"))

    def test_keep_saves_checks_a_prefix_tombstone_for_saves(self):
        prefix = self.fx.install("tmnf", saves=False)
        documents = prefix / "drive_c/users/player/Documents"
        documents.unlink()
        (documents / "TmForever").mkdir(parents=True)
        (documents / "TmForever/settings").write_text("keep")
        tomb = self.fx.root("tmnf") / ".prefix.trackmania-mac-removing.0123abcd"
        prefix.rename(tomb)
        with self.assertRaisesRegex(ValueError, "Back up"):
            self.plan()
        self.assertTrue((tomb / "drive_c/users/player/Documents/TmForever/settings").exists())
        self.assertIn(tomb, self.removed(self.plan(include_saves=True)))

    def test_error_messages_never_print_raw_controls(self):
        prefix = self.fx.install("tmnf", saves=False)
        documents = prefix / "drive_c/users/player/Documents"
        documents.unlink()
        (documents / "TmForever").mkdir(parents=True)
        (documents / "TmForever/\u202eevil").write_text("x")
        printed = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(a[0])):
            status = uninstall.main(["plan", *self.fx.arguments("tmnf").values()])
        self.assertEqual(status, 1)
        (line,) = printed
        self.assertTrue(uninstall._printable(line), repr(line))
        self.assertIn("\\u202e", line)

    def test_saves_folder_used_through_another_titles_user_reg_is_refused(self):
        self.fx.install("tmnf")
        other = self.fx.install("tmu", apps=False)
        (other / "dosdevices").mkdir()
        (other / "dosdevices/z:").symlink_to("/")
        target = str(self.fx.userdata("tmnf")).replace("/", "\\\\")
        (other / "user.reg").write_text(
            "WINE REGISTRY Version 2\n"
            "[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Shell Folders] 1\n"
            '"Personal"="Z:{}"\n'.format(target)
        )
        self.assertTrue(
            any(
                os.path.samefile(path, self.fx.userdata("tmnf"))
                for path in uninstall.managed_paths.redirected_documents(other)
            )
        )
        with self.assertRaisesRegex(ValueError, "United"):
            self.plan(include_saves=True)


class ShellUninstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-uninstall-cli-"
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)

    def run_cli(self, *arguments, title="tmnf"):
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.home),
            "TMPDIR": str(self.home),
            "TRACKMANIA_MAC_TITLE": title,
            "TRACKMANIA_MAC_APPS_DIR": str(self.fx.apps),
        }
        return subprocess.run(
            ["/bin/bash", str(CLI), *arguments],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def test_include_saves_removes_one_title_and_nothing_nested_beside_it(self):
        self.fx.install("tmnf")
        self.fx.install("tmu")
        shared = self.fx.cache / "directx_Jun2010_redist.exe"
        shared.write_text("shared")
        result = self.run_cli("uninstall", "--yes", "--include-saves")
        self.assertEqual(result.returncode, 0, result.stderr)
        title = TITLES["tmnf"]
        for path in (
            self.fx.prefix("tmnf"),
            self.fx.userdata("tmnf"),
            self.fx.apps / title.app_name,
            self.fx.apps / title.settings_app_name,
            self.fx.support / "launchers/tmnf",
        ):
            self.assertFalse(os.path.lexists(path), path)
        tmu = TITLES["tmu"]
        for path in (
            self.fx.prefix("tmu") / ".trackmania-mac-prefix",
            self.fx.save("tmu"),
            self.fx.apps / tmu.app_name,
            self.fx.support / "launchers/tmu",
            self.fx.support / "runtime/1",
            shared,
        ):
            self.assertTrue(path.exists(), path)
        self.assertTrue(self.fx.root("tmnf").is_dir())
        self.assertIn("Removed {}".format(self.fx.prefix("tmnf")), result.stdout)

    def test_busy_prefix_is_refused_immediately(self):
        prefix = self.fx.install("tmnf")
        fd = operation_guard.acquire(prefix)
        try:
            result = self.run_cli("uninstall", "--yes", "--include-saves")
        finally:
            os.close(fd)
        # The guard exits 1, or 75 where it reports a busy prefix on its own.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another operation is using prefix", result.stderr)
        self.assertTrue((prefix / ".trackmania-mac-prefix").exists())
        self.assertTrue(self.fx.userdata("tmnf").is_dir())
        self.assertTrue((self.fx.apps / TITLES["tmnf"].app_name).is_dir())

    def test_apps_only_title_uninstalls_without_a_prefix(self):
        self.fx.install("tmu")
        shutil.rmtree(self.fx.prefix("tmu"))
        result = self.run_cli("uninstall", "--yes", title="tmu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.fx.apps / TITLES["tmu"].app_name).exists())
        self.assertTrue(self.fx.userdata("tmu").is_dir())

    def test_unanswered_prompt_cancels(self):
        prefix = self.fx.install("tmnf")
        result = self.run_cli("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cancelled.", result.stdout)
        self.assertIn(str(prefix), result.stdout)
        self.assertTrue(prefix.is_dir())

    def test_hostile_app_metadata_removes_nothing_extra_through_the_cli(self):
        self.fx.install("tmnf", apps=False)
        sentinel = self.home / "precious.txt"
        sentinel.write_text("keep")
        hostile = "x\nremove\tz\t{}\tq".format(self.home)
        app = make_app(self.fx.apps / TITLES["tmnf"].app_name, hostile)
        result = self.run_cli("uninstall", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertTrue(app.is_dir())
        self.assertFalse(self.fx.prefix("tmnf").exists())
        self.assertIn(repr(hostile), result.stdout)

    def test_cli_finishes_an_interrupted_uninstall_without_a_prefix(self):
        self.fx.install("tmu")
        root = self.fx.root("tmu")
        tomb = root / ".prefix.trackmania-mac-removing.0123abcd"
        self.fx.prefix("tmu").rename(tomb)
        (tomb / ".trackmania-mac-prefix").unlink()
        result = self.run_cli("uninstall", "--yes", title="tmu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(tomb.exists())
        self.assertIn("Removed {}".format(tomb), result.stdout)

    def test_cancel_then_keep_saves_never_deletes_unfinished_saves(self):
        self.fx.install("tmnf")
        save = self.fx.save("tmnf")
        tomb = self.fx.userdata("tmnf").parent / ".tmnf.trackmania-mac-removing.0123abcd"
        # An include-saves run that removed the prefix and stopped on the saves.
        shutil.rmtree(self.fx.prefix("tmnf"))
        self.fx.userdata("tmnf").rename(tomb)
        cancelled = self.run_cli("uninstall")
        self.assertIn("Cancelled.", cancelled.stdout)
        self.assertIn("moved back to", cancelled.stdout)
        result = self.run_cli("uninstall", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(save.exists())
        self.assertFalse(tomb.exists())
        self.assertIn("Restored {}".format(self.fx.userdata("tmnf")), result.stdout)

    def test_shell_and_python_agree_on_the_cached_installer(self):
        for title in TITLES.values():
            if not title.source_url:
                continue
            with self.subTest(title=title.id):
                script = (
                    'die() { echo "$*" >&2; exit 1; }; . "$1/src/shell/lib/titles.sh"; '
                    '_cache_installer_path "$TRACKMANIA_MAC_TITLE"'
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", script, "bash", str(ROOT)],
                    env={
                        "PATH": "/usr/bin:/bin",
                        "TRACKMANIA_MAC_PROJECT_DIR": str(ROOT),
                        "TRACKMANIA_MAC_TITLE": title.id,
                        "TRACKMANIA_MAC_TITLE_SOURCE_URL": title.source_url,
                        "TRACKMANIA_MAC_CACHE": str(self.fx.cache),
                    },
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.strip(),
                    str(uninstall.installer_cache_path(title, self.fx.cache)),
                )

    def test_shell_and_python_agree_on_extra_downloads(self):
        for title in TITLES.values():
            extras = uninstall.download_cache_paths(title, self.fx.cache)[1:]
            if not extras:
                continue
            with self.subTest(title=title.id):
                urls = [update.url for update in title.updates]
                urls += [title.overlay.url] if title.overlay else []
                script = (
                    'die() { echo "$*" >&2; exit 1; }; . "$1/src/shell/lib/titles.sh"; '
                    'shift; for url in "$@"; do _cache_download_path "$TRACKMANIA_MAC_TITLE" "$url"; done'
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", script, "bash", str(ROOT), *urls],
                    env={
                        "PATH": "/usr/bin:/bin",
                        "TRACKMANIA_MAC_PROJECT_DIR": str(ROOT),
                        "TRACKMANIA_MAC_TITLE": title.id,
                        "TRACKMANIA_MAC_CACHE": str(self.fx.cache),
                    },
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [str(path) for path, _ in extras])


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-preview-"
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)
        environment = {
            key: value for key, value in os.environ.items() if key != "TRACKMANIA_MAC_TITLES_DIR"
        }
        environment.update(HOME=str(self.home), TRACKMANIA_MAC_APPS_DIR=str(self.fx.apps))
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def preview(self, title_id):
        return self.preview_for(self.fx, title_id)

    @staticmethod
    def preview_for(fixture, title_id):
        from trackmania_mac.hub import paths as hub_paths

        title = TITLES[title_id]
        with mock.patch.dict(os.environ, {"TRACKMANIA_MAC_APPS_DIR": str(fixture.apps)}):
            return uninstall.preview(title, hub_paths.compute(title))

    def test_preview_lists_what_exists(self):
        self.assertEqual(self.preview("tmnf"), {"items": [], "saves": None})
        self.assertFalse(uninstall.removable(self.preview("tmnf")))
        self.fx.install("tmnf")
        (self.fx.cache / "tmnationsforever_setup.exe").write_text("x")
        (self.fx.logs / "play.log").write_text("x")
        removal = self.preview("tmnf")
        self.assertEqual(
            [item["kind"] for item in removal["items"]],
            ["prefix", "game-app", "settings-app", "launcher", "cache", "logs"],
        )
        self.assertEqual(removal["saves"], str(self.fx.userdata("tmnf")))
        self.assertTrue(uninstall.removable(removal))
        self.assertEqual(self.preview("tmu"), {"items": [], "saves": None})

    def test_saves_alone_are_removable_but_logs_alone_are_not(self):
        (self.fx.logs / "play.log").write_text("x")
        self.assertFalse(uninstall.removable(self.preview("tmnf")))
        self.fx.userdata("tmnf").mkdir(parents=True)
        self.assertTrue(uninstall.removable(self.preview("tmnf")))

    def test_preview_skips_other_titles_prefix_and_foreign_apps(self):
        self.fx.install("tmnf", apps=False)
        (self.fx.prefix("tmnf") / ".trackmania-mac-prefix").write_text("tmu:1\n")
        make_app(self.fx.apps / TITLES["tmnf"].app_name, "com.example.other", marker=False)
        kinds = [item["kind"] for item in self.preview("tmnf")["items"]]
        self.assertNotIn("prefix", kinds)
        self.assertNotIn("game-app", kinds)


if __name__ == "__main__":
    unittest.main()
