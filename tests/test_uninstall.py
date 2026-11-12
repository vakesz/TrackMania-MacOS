"""Per-title uninstall: the guard plan, the shell removal and the hub preview.

Every fixture lives under a temporary HOME in /private/tmp (a managed root);
nothing here touches the real ~/Games, ~/Library or /Applications.
"""

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tmnf.guard import operation_guard, uninstall
from tmnf.hub import registry


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/tmnf"

TITLES = {title.id: title for title in registry.load_all(ROOT / "config/titles")}


def make_app(path, bundle_id, marker=True):
    contents = path / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, stream)
    executable = contents / "MacOS/TMNF"
    executable.write_text("#!/bin/bash\n")
    executable.chmod(0o755)
    if marker:
        (contents / ".tmnf-app").touch()
    return path


class Fixture:
    """A HOME with an installed tmnf whose root also holds other titles' roots."""

    def __init__(self, home):
        self.home = home
        self.support = home / "Library/Application Support/TMNF"
        self.cache = home / "Library/Caches/TMNF"
        self.logs = home / "Library/Logs/TMNF"
        self.apps = home / "Applications"
        for directory in (self.support / "runtime/1", self.cache, self.logs, self.apps):
            directory.mkdir(parents=True)

    def root(self, title_id):
        return self.home / "Games" / TITLES[title_id].prefix_subdir

    def prefix(self, title_id):
        return self.root(title_id) / "prefix"

    def userdata(self, title_id):
        return self.root(title_id) / "UserData"

    def install(self, title_id, saves=True, apps=True):
        title = TITLES[title_id]
        prefix = self.prefix(title_id)
        documents = prefix / "drive_c/users/player/Documents"
        documents.parent.mkdir(parents=True)
        (prefix / ".tmnf-prefix").write_text(title.prefix_marker + "\n")
        (prefix / "system.reg").write_text("WINE REGISTRY Version 2\n")
        userdata = self.userdata(title_id)
        userdata.mkdir(parents=True)
        documents.symlink_to(userdata, target_is_directory=True)
        if saves:
            save = userdata / title.save_dirs[0] / "Replays/race.Gbx"
            save.parent.mkdir(parents=True)
            save.write_text("keep")
        if apps:
            make_app(self.apps / title.app_name, title.bundle_id_game)
            make_app(self.apps / title.settings_app_name, title.bundle_id_settings)
            launcher = self.support / "launchers" / title_id
            launcher.mkdir(parents=True)
            (launcher / ".tmnf-launcher").touch()
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
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="tmnf-uninstall-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)
        environment = {key: value for key, value in os.environ.items() if key != "TMNF_TITLES_DIR"}
        environment.update(HOME=str(self.home), TMNF_TITLE="tmnf")
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, title_id="tmnf", include_saves=False, warnings=None, **overrides):
        os.environ["TMNF_TITLE"] = title_id
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

    def test_tmnf_never_reaches_into_titles_nested_under_its_root(self):
        self.fx.install("tmnf")
        for other in ("tmu", "tm-canyon"):
            self.fx.install(other)
        removed = self.removed(self.plan(include_saves=True))
        root = self.fx.root("tmnf")
        self.assertIn(root / "prefix", removed)
        self.assertIn(root / "UserData", removed)
        self.assertNotIn(root, removed)
        for path in removed:
            for other in ("tmu", "tm-canyon"):
                other_root = self.fx.root(other)
                self.assertFalse(
                    path == other_root or other_root in path.parents or path in other_root.parents,
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
            self.assertNotIn(self.fx.root("tmnf") / "UserData", [path, *path.parents])
        self.assertIn(root / "prefix", removed)
        self.assertIn(root / "UserData", removed)

    def test_prefix_at_a_shared_root_is_refused(self):
        self.fx.install("tmu", apps=False)
        root = self.fx.root("tmnf")
        (root / ".tmnf-prefix").write_text("tmnf:1\n")
        (root / "system.reg").write_text("x")
        elsewhere = self.home / "saves"
        with self.assertRaisesRegex(ValueError, "Refusing to remove .*Games/TMNF:"):
            self.plan(prefix=str(root), userdata=str(elsewhere))

    def test_registry_nesting_and_broken_entries_fail_closed(self):
        self.fx.install("tmnf")
        titles = self.home / "titles"
        shutil.copytree(ROOT / "config/titles", titles)
        data = json.loads((titles / "tm-turbo.json").read_text())
        data["prefix_subdir"] = "TMNF/UserData/evil"
        (titles / "tm-turbo.json").write_text(json.dumps(data))
        os.environ["TMNF_TITLES_DIR"] = str(titles)
        with self.assertRaisesRegex(ValueError, "contains .*UserData/evil"):
            self.plan(include_saves=True)
        (titles / "tm-turbo.json").write_text("{not json")
        with self.assertRaisesRegex(ValueError, "Fix the title registry"):
            self.plan()

    def test_symlinked_prefix_or_saves_folder_is_refused(self):
        real = self.home / "real-prefix"
        real.mkdir()
        (real / ".tmnf-prefix").write_text("tmnf:1\n")
        (real / "system.reg").write_text("x")
        self.fx.root("tmnf").mkdir(parents=True)
        self.fx.prefix("tmnf").symlink_to(real)
        with self.assertRaises(ValueError):
            self.plan()
        self.fx.prefix("tmnf").unlink()
        outside = self.home / "outside-saves"
        outside.mkdir()
        self.fx.userdata("tmnf").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.plan(include_saves=True)

    def test_saves_folder_reached_through_a_link_out_of_home_is_refused(self):
        with tempfile.TemporaryDirectory() as foreign:  # $TMPDIR is not a managed root
            target = Path(foreign).resolve()
            (target / "UserData").mkdir()
            (self.home / "Games").mkdir()
            (self.home / "Games/TMNF").symlink_to(target)
            with self.assertRaises(ValueError):
                self.plan(include_saves=True)
            self.assertTrue((target / "UserData").is_dir())

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
        self.assertTrue((settings_target / "Contents/.tmnf-app").exists())

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
        (game / "Contents/.tmnf-app").symlink_to(marker / "Contents/.tmnf-app")
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
        (outside / ".tmnf-launcher").touch()
        (self.fx.support / "launchers").mkdir()
        (self.fx.support / "launchers/tmu").symlink_to(outside)
        (self.fx.cache / "games").mkdir()
        (self.fx.cache / "games/tmu").symlink_to(outside)
        (self.fx.support / "icons").mkdir()
        (self.fx.support / "icons/tmu.icns").symlink_to(outside / ".tmnf-launcher")
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
        self.assertNotIn(tmu, removed)
        self.fx.install("tmu", apps=False)
        removed = self.removed(self.plan("tmu"))
        self.assertIn(tmu, removed)
        self.assertNotIn(canyon, removed)
        self.assertNotIn(self.fx.cache / "games", removed)

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
        (self.fx.prefix("tmnf") / ".tmnf-prefix").write_text("tmu:1\n")
        with self.assertRaisesRegex(ValueError, "another TrackMania title"):
            self.plan()

    def test_plan_command_prints_tab_separated_lines(self):
        self.fx.install("tmnf")
        arguments = self.fx.arguments("tmnf")
        output = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: output.append(a[0])):
            status = uninstall.main(["plan", "--include-saves", *arguments.values()])
        self.assertEqual(status, 0)
        verbs = {line.split("\t")[0] for line in output}
        self.assertEqual(verbs, {"remove"})
        self.assertIn("remove\tsaves\t{}".format(self.fx.userdata("tmnf")), output)


class ShellUninstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="tmnf-uninstall-cli-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)

    def tmnf(self, *arguments, title="tmnf"):
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.home),
            "TMPDIR": str(self.home),
            "TMNF_TITLE": title,
            "TMNF_APPS_DIR": str(self.fx.apps),
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
        result = self.tmnf("uninstall", "--yes", "--include-saves")
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
            self.fx.prefix("tmu") / ".tmnf-prefix",
            self.fx.userdata("tmu") / "TmForever/Replays/race.Gbx",
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
            result = self.tmnf("uninstall", "--yes", "--include-saves")
        finally:
            os.close(fd)
        # The guard exits 1, or 75 where it reports a busy prefix on its own.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another operation is using prefix", result.stderr)
        self.assertTrue((prefix / ".tmnf-prefix").exists())
        self.assertTrue(self.fx.userdata("tmnf").is_dir())
        self.assertTrue((self.fx.apps / TITLES["tmnf"].app_name).is_dir())

    def test_apps_only_title_uninstalls_without_a_prefix(self):
        self.fx.install("tmu")
        shutil.rmtree(self.fx.prefix("tmu"))
        result = self.tmnf("uninstall", "--yes", title="tmu")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.fx.apps / TITLES["tmu"].app_name).exists())
        self.assertTrue(self.fx.userdata("tmu").is_dir())

    def test_unanswered_prompt_cancels(self):
        prefix = self.fx.install("tmnf")
        result = self.tmnf("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cancelled.", result.stdout)
        self.assertIn(str(prefix), result.stdout)
        self.assertTrue(prefix.is_dir())

    def test_shell_and_python_agree_on_the_cached_installer(self):
        for title in TITLES.values():
            if not title.source_url:
                continue
            with self.subTest(title=title.id):
                script = (
                    'die() { echo "$*" >&2; exit 1; }; . "$1/src/shell/lib/titles.sh"; '
                    '_cache_installer_path "$TMNF_TITLE"'
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", script, "bash", str(ROOT)],
                    env={
                        "PATH": "/usr/bin:/bin",
                        "TMNF_PROJECT_DIR": str(ROOT),
                        "TMNF_TITLE": title.id,
                        "TMNF_TITLE_SOURCE_URL": title.source_url,
                        "TMNF_CACHE": str(self.fx.cache),
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


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="tmnf-preview-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.fx = Fixture(self.home)
        environment = {key: value for key, value in os.environ.items() if key != "TMNF_TITLES_DIR"}
        environment.update(HOME=str(self.home), TMNF_APPS_DIR=str(self.fx.apps))
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def preview(self, title_id):
        from tmnf.hub import paths as hub_paths

        title = TITLES[title_id]
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
        (self.fx.prefix("tmnf") / ".tmnf-prefix").write_text("tmu:1\n")
        make_app(self.fx.apps / TITLES["tmnf"].app_name, "com.example.other", marker=False)
        kinds = [item["kind"] for item in self.preview("tmnf")["items"]]
        self.assertNotIn("prefix", kinds)
        self.assertNotIn("game-app", kinds)


if __name__ == "__main__":
    unittest.main()
