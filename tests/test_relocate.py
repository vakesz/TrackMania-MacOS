"""Moving an install from the folders of earlier releases into ~/Library/TrackManiaMacOS.

Every fixture lives under a temporary HOME in /private/tmp; nothing here touches
the real ~/Games or ~/Library. The saves invariant comes first: whatever fails,
and wherever a run stops, every save file exists exactly once, unchanged and
never copied (same inode), under its old or its new folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from unittest import mock

from trackmania_mac import legacy, markers
from trackmania_mac.guard import managed_paths, operation_guard, relocate, uninstall
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import registry, state

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/shell/cli/trackmania-mac"
TITLES = {title.id: title for title in registry.load_all(ROOT / "config/titles")}
LEGACY_IDS = sorted(title.id for title in TITLES.values() if title.legacy_prefix_subdir)


class Crash(BaseException):
    """The process dies here: nothing after it runs, not even a rollback."""


def quoted(path) -> str:
    """PATH as the wrapper writes it: printf %q in this locale."""
    return subprocess.run(
        ["/bin/bash", "-c", 'printf "%q" "$1"', "_", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def failing_at(original, step, error):
    """ORIGINAL, except that call number STEP (1-based) raises ERROR instead."""
    calls = []

    def wrapper(*arguments):
        calls.append(arguments)
        if len(calls) == step:
            raise error
        return original(*arguments)

    return wrapper


class Legacy:
    """A HOME laid out the way earlier releases left it."""

    def __init__(self, home: Path):
        self.home = home
        self.support = home / "Library/Application Support/TMNF"
        self.cache = home / "Library/Caches/TMNF"
        self.logs = home / "Library/Logs/TMNF"
        self.data = home / "Library/TrackManiaMacOS"

    def root(self, title_id):
        return self.home / "Games" / TITLES[title_id].legacy_prefix_subdir

    def userdata(self, title_id):
        return self.root(title_id) / "UserData"

    def install(self, title_id, saves=True, documents=None):
        title = TITLES[title_id]
        prefix = self.root(title_id) / "prefix"
        profile = prefix / "drive_c/users/player"
        profile.mkdir(parents=True)
        (prefix / ".tmnf-prefix").write_text(title.prefix_marker + "\n")
        (prefix / "system.reg").write_text("WINE REGISTRY Version 2\n")
        dosdevices = prefix / "dosdevices"
        dosdevices.mkdir()
        (dosdevices / "c:").symlink_to("../drive_c")
        (dosdevices / "z:").symlink_to("/")
        userdata = self.userdata(title_id)
        userdata.mkdir(parents=True)
        # setup_documents links the resolved UserData path, absolute.
        target = documents if documents is not None else userdata.resolve()
        (profile / "Documents").symlink_to(target, target_is_directory=True)
        (profile / "My Documents").symlink_to(target, target_is_directory=True)
        game = prefix / "drive_c/Program Files (x86)" / title.game_dir_name / title.game_exe
        game.parent.mkdir(parents=True)
        game.write_bytes(b"MZ")
        if saves:
            for name in title.save_dirs[:2]:
                replay = userdata / name / "Replays/race.Gbx"
                replay.parent.mkdir(parents=True, exist_ok=True)
                replay.write_bytes(os.urandom(64))
            (userdata / title.user_subdir / "Config").mkdir(parents=True, exist_ok=True)
            (userdata / title.user_subdir / "Config/Default.SystemConfig.Gbx").write_text("cfg")
        launcher = self.support / "launchers" / title_id
        (launcher / "config").mkdir(parents=True)
        (launcher / ".tmnf-launcher").touch()
        (launcher / "config/installation.sh").write_text("# settings\n")
        return prefix

    def shared(self):
        (self.support / "runtime/0.4.3/bin").mkdir(parents=True)
        (self.support / "runtime/0.4.3/bin/wine").write_text("wine")
        (self.support / "icons").mkdir()
        (self.support / "icons/tmnf.icns").write_text("icon")
        (self.cache / "games/tmu").mkdir(parents=True)
        (self.cache / "directx_Jun2010_redist.exe").write_text("dx")
        (self.cache / "build/0123/.git").mkdir(parents=True)
        self.logs.mkdir(parents=True)
        (self.logs / "play.log").write_text("log")

    def save_trees(self):
        """Where each title's saves are, old or new, as {title: path}."""
        found = {}
        for title_id in LEGACY_IDS:
            places = [
                path
                for path in (self.userdata(title_id), self.data / "saves" / title_id)
                if os.path.lexists(path)
            ]
            assert len(places) <= 1, places
            if places:
                found[title_id] = places[0]
        return found


def fingerprint(folder: Path) -> dict:
    """Every file below FOLDER: content digest, inode, size and modification time."""
    result = {}
    for directory, _directories, files in os.walk(folder):
        for name in files:
            path = Path(directory) / name
            info = os.lstat(path)
            result[str(path.relative_to(folder))] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            )
    return result


def tree(folder: Path) -> dict:
    """Name-for-name and byte-for-byte picture of FOLDER, links not followed."""
    result = {}
    for directory, directories, files in os.walk(folder):
        for name in directories + files:
            path = Path(directory) / name
            info = os.lstat(path)
            relative = str(path.relative_to(folder))
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ("dir", stat.S_IMODE(info.st_mode))
            else:
                result[relative] = ("file", path.read_bytes(), stat.S_IMODE(info.st_mode))
    return result


class Base(unittest.TestCase):
    # A home folder name to test with; None uses the temporary folder itself.
    HOME_NAME: typing.Optional[str] = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            dir="/private/tmp", prefix="trackmania-mac-relocate-"
        )
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        if self.HOME_NAME:
            self.home = self.home / self.HOME_NAME
            self.home.mkdir()
        self.legacy = Legacy(self.home)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TRACKMANIA_MAC_", "TMNF_")) and key != "HOME"
        }
        environment["HOME"] = str(self.home)
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.messages = []

    def migrate(self, **kwargs):
        return relocate.migrate(home=self.home, report=self.messages.append, **kwargs)

    def install_everything(self):
        for title_id in LEGACY_IDS:
            self.legacy.install(title_id)
        self.legacy.shared()


class SavesAreNeverLostTests(Base):
    """The invariant behind the whole design, through every failure point."""

    def setUp(self):
        super().setUp()
        self.install_everything()
        self.before = {
            title_id: fingerprint(path) for title_id, path in self.legacy.save_trees().items()
        }
        self.assertEqual(sorted(self.before), LEGACY_IDS)
        self.steps = len(relocate.plan(self.home, os.environ, deep=True).moves)
        self.assertGreater(self.steps, len(LEGACY_IDS) * 2)

    def assert_saves_intact(self):
        found = self.legacy.save_trees()
        self.assertEqual(sorted(found), LEGACY_IDS)
        for title_id, path in found.items():
            self.assertEqual(fingerprint(path), self.before[title_id], title_id)

    def assert_old_layout(self):
        for title_id in LEGACY_IDS:
            self.assertTrue(self.legacy.userdata(title_id).is_dir(), title_id)
            self.assertTrue((self.legacy.root(title_id) / "prefix/.tmnf-prefix").is_file())
        self.assertFalse(os.path.lexists(self.legacy.data / relocate.JOURNAL))
        self.assertFalse(os.path.lexists(self.legacy.data / "games"))
        self.assertFalse(os.path.lexists(self.legacy.data / "saves"))

    def assert_new_layout(self):
        for title_id in LEGACY_IDS:
            saves = self.legacy.data / "saves" / title_id
            prefix = self.legacy.data / "games" / title_id / "prefix"
            self.assertTrue(saves.is_dir(), title_id)
            self.assertTrue((prefix / ".tmnf-prefix").is_file(), title_id)
            # Adopted at the move; the earlier name stays for that release's launcher.
            self.assertTrue((prefix / markers.PREFIX).is_file(), title_id)
            documents = prefix / "drive_c/users/player/Documents"
            self.assertEqual(documents.resolve(strict=True), saves.resolve())
        self.assertFalse(os.path.lexists(self.legacy.data / relocate.JOURNAL))
        self.assertFalse(os.path.lexists(self.home / "Games"))

    def test_a_clean_run_moves_every_save_without_copying(self):
        self.assertEqual(self.migrate(), self.steps)
        self.assert_saves_intact()
        self.assert_new_layout()

    def test_a_failure_at_any_step_puts_everything_back(self):
        original = relocate._move
        for failing in range(self.steps):
            with self.subTest(step=failing):
                flaky = failing_at(original, failing + 1, OSError(5, "injected failure"))
                with mock.patch.object(relocate, "_move", flaky):
                    with self.assertRaisesRegex(OSError, "put back"):
                        self.migrate()
                self.assert_saves_intact()
                self.assert_old_layout()

    def test_a_failing_rename_inside_the_kernel_call_is_rolled_back(self):
        original = managed_paths.rename_exclusive
        for failing in range(self.steps):
            with self.subTest(step=failing):
                # Call 1 publishes the journal; rollback renames after the failure pass.
                flaky = failing_at(original, failing + 2, OSError(28, "No space left"))

                with mock.patch.object(managed_paths, "rename_exclusive", flaky):
                    with self.assertRaises(OSError):
                        self.migrate()
                self.assert_saves_intact()
                self.assert_old_layout()

    def test_a_crash_at_any_step_resumes_to_the_new_layout(self):
        original = relocate._move
        for failing in range(self.steps):
            with self.subTest(step=failing):
                # Each round starts from the old layout again.
                self.assertTrue(
                    self.legacy.userdata(LEGACY_IDS[0]).exists(),
                    "the previous round left no old layout",
                )
                dies = failing_at(original, failing + 1, Crash())
                with (
                    mock.patch.object(relocate, "_move", dies),
                    mock.patch.object(relocate, "_rollback", side_effect=Crash()),
                ):
                    with self.assertRaises(Crash):
                        self.migrate()
                self.assertTrue((self.legacy.data / relocate.JOURNAL).is_file())
                self.assert_saves_intact()
                self.assertEqual(relocate.status(self.home)["state"], "pending")
                self.migrate()
                self.assert_saves_intact()
                self.assert_new_layout()
                self.reset()

    def test_a_crash_after_the_moves_finishes_on_the_next_run(self):
        with mock.patch.object(managed_paths, "remap_links", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.migrate()
        self.assert_saves_intact()
        self.assertTrue((self.legacy.data / relocate.JOURNAL).is_file())
        self.migrate()
        self.assert_saves_intact()
        self.assert_new_layout()

    def test_a_resume_that_fails_again_keeps_its_record(self):
        original = relocate._move
        calls = []

        def dies(move, created):
            calls.append(move)
            if len(calls) == 2:
                raise Crash()
            return original(move, created)

        with (
            mock.patch.object(relocate, "_move", dies),
            mock.patch.object(relocate, "_rollback", side_effect=Crash()),
        ):
            with self.assertRaises(Crash):
                self.migrate()
        with mock.patch.object(relocate, "_move", side_effect=OSError(5, "again")):
            with self.assertRaisesRegex(OSError, "stopped again"):
                self.migrate()
        self.assertTrue((self.legacy.data / relocate.JOURNAL).is_file())
        self.assert_saves_intact()
        self.migrate()
        self.assert_saves_intact()
        self.assert_new_layout()

    def test_a_resume_never_acts_on_a_folder_it_did_not_record(self):
        original = relocate._move
        calls = []

        def dies(move, created):
            calls.append(move)
            if len(calls) == 3:
                raise Crash()
            return original(move, created)

        with (
            mock.patch.object(relocate, "_move", dies),
            mock.patch.object(relocate, "_rollback", side_effect=Crash()),
        ):
            with self.assertRaises(Crash):
                self.migrate()
        # Someone replaces a moved saves folder with another folder of the same name.
        journal = json.loads((self.legacy.data / relocate.JOURNAL).read_text())
        moved = next(item for item in journal["moves"][:2] if item["kind"] == "saves")
        target = Path(moved["target"])
        target.rename(target.with_name("elsewhere"))
        target.mkdir()
        (target / "other").write_text("not ours")
        picture = tree(self.home)
        with self.assertRaisesRegex(relocate.Blocked, "no longer at"):
            self.migrate()
        self.assertEqual(tree(self.home), picture)
        self.assertEqual(relocate.status(self.home)["state"], "blocked")

    def test_a_target_created_after_the_check_is_never_replaced(self):
        original = managed_paths.rename_exclusive
        intruder = self.legacy.data / "saves" / LEGACY_IDS[-1]

        def race(source, target):
            # Another process creates the folder between the check and the rename.
            if Path(target) == intruder:
                intruder.mkdir(parents=True, exist_ok=True)
            return original(source, target)

        with mock.patch.object(managed_paths, "rename_exclusive", race):
            with self.assertRaisesRegex(OSError, "put back"):
                self.migrate()
        self.assertTrue(intruder.is_dir())
        # The rollback could not remove saves/ while the intruder was in it.
        intruder.rmdir()
        intruder.parent.rmdir()
        self.assert_saves_intact()
        self.assert_old_layout()

    def test_rename_exclusive_refuses_an_existing_empty_folder(self):
        source = self.home / "source"
        source.mkdir()
        (source / "save").write_text("keep")
        target = self.home / "target"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            managed_paths.rename_exclusive(source, target)
        self.assertEqual((source / "save").read_text(), "keep")

    def test_a_crash_partway_through_a_rollback_resumes_forward(self):
        original = managed_paths.rename_exclusive
        state = {"moves": 0, "back": 0}

        def flaky(source, target):
            if "TrackManiaMacOS" in str(target) and not str(target).endswith(relocate.JOURNAL):
                state["moves"] += 1
                if state["moves"] == 4:
                    raise OSError(5, "injected failure")
            elif "TrackManiaMacOS" in str(source):
                state["back"] += 1
                if state["back"] == 2:
                    raise Crash()
            return original(source, target)

        with mock.patch.object(managed_paths, "rename_exclusive", flaky):
            with self.assertRaises(Crash):
                self.migrate()
        self.assertTrue((self.legacy.data / relocate.JOURNAL).is_file())
        self.assert_saves_intact()
        self.migrate()
        self.assert_saves_intact()
        self.assert_new_layout()

    def test_a_rollback_that_finds_another_folder_keeps_the_record(self):
        original = relocate._move
        calls = []

        def swap_then_fail(move, created):
            calls.append(move)
            if len(calls) == 2:
                # The first moved folder is replaced by another one of the same name.
                first = calls[0].target
                first.rename(first.with_name(first.name + "-elsewhere"))
                first.mkdir()
                raise OSError(5, "injected failure")
            return original(move, created)

        with mock.patch.object(relocate, "_move", swap_then_fail):
            with self.assertRaisesRegex(OSError, "could not be moved back"):
                self.migrate()
        self.assertTrue((self.legacy.data / relocate.JOURNAL).is_file())

    def test_cleanup_and_remap_failures_never_leave_the_move_pending(self):
        failures = (
            mock.patch.object(
                relocate, "_remove_if_empty", side_effect=PermissionError(1, "locked")
            ),
            mock.patch.object(managed_paths, "remap_links", side_effect=ValueError("bad link")),
        )
        for failure in failures:
            with self.subTest(failure=failure.attribute):
                with failure:
                    self.migrate()
                self.assert_saves_intact()
                self.assertFalse(os.path.lexists(self.legacy.data / relocate.JOURNAL))
                self.assertEqual(relocate.status(self.home)["state"], "none")
                self.assertTrue(any("locked" in m or "bad link" in m for m in self.messages))
                self.reset()

    def test_a_tampered_record_is_refused(self):
        tamper = {
            "target outside the data folder": lambda item: item.update(
                target=str(self.home / "Documents/elsewhere")
            ),
            "target of another kind": lambda item: item.update(
                target=str(self.legacy.data / "logs/x")
            ),
            "identity as text": lambda item: item.update(identity=["1", "2"]),
            "control character": lambda item: item.update(source=item["source"] + "\x01"),
        }
        for name, change in tamper.items():
            with self.subTest(name=name):
                with (
                    mock.patch.object(relocate, "_move", side_effect=Crash()),
                    mock.patch.object(relocate, "_rollback", side_effect=Crash()),
                ):
                    with self.assertRaises(Crash):
                        self.migrate()
                path = self.legacy.data / relocate.JOURNAL
                journal = json.loads(path.read_text())
                saves = next(item for item in journal["moves"] if item["kind"] == "saves")
                change(saves)
                path.write_text(json.dumps(journal))
                picture = tree(self.home)
                with self.assertRaisesRegex(relocate.Blocked, "cannot be used"):
                    self.migrate()
                self.assertEqual(tree(self.home), picture)
                self.assertEqual(relocate.status(self.home)["state"], "blocked")
                self.reset()

    def reset(self):
        """Back to a fresh old layout for the next subtest."""
        import shutil

        for child in list(self.home.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.install_everything()
        self.before = {
            title_id: fingerprint(path) for title_id, path in self.legacy.save_trees().items()
        }


class MigrationTests(Base):
    def test_nothing_to_move_creates_nothing(self):
        picture = tree(self.home)
        self.assertEqual(relocate.status(self.home)["state"], "none")
        self.assertEqual(self.migrate(), 0)
        self.assertEqual(relocate.main(["pending"]), relocate.NOTHING_TO_MOVE)
        self.assertEqual(tree(self.home), picture)
        self.assertFalse(os.path.lexists(self.legacy.data))

    def test_layout_after_a_full_move(self):
        self.legacy.install("tmnf")
        self.legacy.install("tmu")
        self.legacy.shared()
        (self.home / "Games/TMNF/.prefix.tmnf-operation.lock").write_text("")
        (self.home / "Games/.DS_Store").write_text("")
        self.migrate()
        data = self.legacy.data
        self.assertTrue((data / "games/tmnf/prefix/.tmnf-prefix").is_file())
        self.assertTrue((data / "games/tmu/prefix/.tmnf-prefix").is_file())
        self.assertTrue((data / "saves/tmnf/TmForever/Replays/race.Gbx").is_file())
        self.assertTrue((data / "runtime/0.4.3/bin/wine").is_file())
        self.assertTrue((data / "launchers/tmnf/.tmnf-launcher").is_file())
        self.assertTrue((data / "icons/tmnf.icns").is_file())
        self.assertTrue((data / "cache/directx_Jun2010_redist.exe").is_file())
        self.assertTrue((data / "cache/games/tmu").is_dir())
        self.assertFalse(os.path.lexists(data / "cache/build"))
        self.assertTrue((data / "logs/play.log").is_file())
        for gone in (
            self.home / "Games",
            self.legacy.support,
            self.legacy.cache,
            self.legacy.logs,
        ):
            self.assertFalse(os.path.lexists(gone), gone)
        self.assertTrue((self.home / "Library/Application Support").is_dir())
        # Wine's own links are relative or point at the host root: unchanged.
        dosdevices = data / "games/tmnf/prefix/dosdevices"
        self.assertEqual(os.readlink(dosdevices / "c:"), "../drive_c")
        self.assertEqual(os.readlink(dosdevices / "z:"), "/")
        profile = data / "games/tmnf/prefix/drive_c/users/player"
        self.assertEqual(os.readlink(profile / "Documents"), str(data / "saves/tmnf"))
        self.assertEqual(os.readlink(profile / "My Documents"), str(data / "saves/tmnf"))
        self.assertEqual(relocate.status(self.home)["state"], "none")

    def test_documents_kept_elsewhere_are_left_alone(self):
        documents = self.home / "Documents"
        (documents / "TmForever").mkdir(parents=True)
        (documents / "TmForever/save").write_text("keep")
        self.legacy.install("tmnf", documents=documents)
        self.migrate()
        profile = self.legacy.data / "games/tmnf/prefix/drive_c/users/player"
        self.assertEqual(os.readlink(profile / "Documents"), str(documents))
        self.assertEqual((documents / "TmForever/save").read_text(), "keep")

    def test_other_files_in_an_old_folder_stay_and_keep_it(self):
        self.legacy.install("tmnf")
        (self.home / "Games/TMNF/notes.txt").write_text("mine")
        (self.home / "Games/other").mkdir()
        self.migrate()
        self.assertEqual((self.home / "Games/TMNF/notes.txt").read_text(), "mine")
        self.assertTrue((self.home / "Games/other").is_dir())
        self.assertTrue(any("notes" in m or "Games/TMNF" in m for m in self.messages))

    def test_uninstall_leftovers_move_where_uninstall_looks(self):
        self.legacy.install("tmnf")
        root = self.legacy.root("tmnf")
        (root / ".UserData.tmnf-removing.0123abcd/TmForever").mkdir(parents=True)
        (root / ".UserData.tmnf-removing.0123abcd/TmForever/save").write_text("keep")
        (root / ".prefix.tmnf-removing.89abcdef").mkdir()
        self.migrate()
        data = self.legacy.data
        self.assertEqual(
            (data / "saves/.tmnf.tmnf-removing.0123abcd/TmForever/save").read_text(), "keep"
        )
        self.assertTrue((data / "games/tmnf/.prefix.tmnf-removing.89abcdef").is_dir())

    def test_a_prefix_that_is_not_ours_stays_but_saves_move(self):
        self.legacy.install("tmnf")
        (self.legacy.root("tmnf") / "prefix/.tmnf-prefix").unlink()
        self.migrate()
        self.assertTrue((self.legacy.root("tmnf") / "prefix/system.reg").is_file())
        self.assertTrue((self.legacy.data / "saves/tmnf/TmForever").is_dir())
        self.assertEqual(relocate.status(self.home)["state"], "none")

    def test_a_symlinked_old_root_is_followed_and_the_link_kept(self):
        elsewhere = self.home / "Elsewhere/TMNF"
        self.legacy.install("tmnf")
        elsewhere.parent.mkdir()
        (self.home / "Games/TMNF").rename(elsewhere)
        (self.home / "Games/TMNF").symlink_to(elsewhere, target_is_directory=True)
        self.migrate()
        self.assertTrue((self.legacy.data / "saves/tmnf/TmForever/Replays/race.Gbx").is_file())
        self.assertTrue((self.home / "Games/TMNF").is_symlink())
        profile = self.legacy.data / "games/tmnf/prefix/drive_c/users/player"
        self.assertEqual(
            (profile / "Documents").resolve(), (self.legacy.data / "saves/tmnf").resolve()
        )

    def test_custom_support_and_cache_stay_put(self):
        self.legacy.install("tmnf")
        self.legacy.shared()
        environ = dict(
            os.environ, TRACKMANIA_MAC_SUPPORT="/elsewhere", TRACKMANIA_MAC_CACHE="/cache"
        )
        relocate.migrate(home=self.home, environ=environ, report=self.messages.append)
        self.assertTrue((self.legacy.support / "runtime/0.4.3/bin/wine").is_file())
        self.assertTrue((self.legacy.cache / "directx_Jun2010_redist.exe").is_file())
        self.assertTrue((self.legacy.data / "saves/tmnf").is_dir())
        self.assertTrue((self.legacy.data / "logs/play.log").is_file())

    def test_only_the_new_caches_own_build_folder_is_removed(self):
        self.legacy.install("tmnf")
        self.legacy.shared()
        (self.legacy.cache / "games/build/keep").mkdir(parents=True)
        # A merged cache: each child moves on its own, games/ among them.
        (self.legacy.data / "cache").mkdir(parents=True)
        (self.legacy.data / "cache/other").write_text("new")
        self.migrate()
        cache = self.legacy.data / "cache"
        self.assertTrue((cache / "games/build/keep").is_dir())
        self.assertFalse(os.path.lexists(cache / "build"))
        self.assertEqual((cache / "other").read_text(), "new")

    def test_a_build_folder_only_in_the_new_cache_is_left_alone(self):
        self.legacy.install("tmnf")
        (self.legacy.data / "cache/build/0123/.git").mkdir(parents=True)
        self.migrate()
        self.assertTrue((self.legacy.data / "cache/build/0123/.git").is_dir())

    def test_shared_folders_merge_without_overwriting(self):
        self.legacy.install("tmnf")
        self.legacy.shared()
        (self.legacy.data / "logs").mkdir(parents=True)
        (self.legacy.data / "logs/play.log").write_text("new")
        (self.legacy.data / "runtime/0.5.0").mkdir(parents=True)
        self.migrate()
        self.assertEqual((self.legacy.data / "logs/play.log").read_text(), "new")
        self.assertEqual((self.legacy.logs / "play.log").read_text(), "log")
        self.assertTrue((self.legacy.data / "runtime/0.4.3/bin/wine").is_file())
        self.assertEqual(relocate.status(self.home)["state"], "none")


class BlockedTests(Base):
    def setUp(self):
        super().setUp()
        self.legacy.install("tmnf")
        self.legacy.install("tmu")
        self.legacy.shared()

    def assert_blocked(self, pattern, exception=relocate.Blocked):
        picture = tree(self.home)
        with self.assertRaisesRegex(exception, pattern):
            self.migrate()
        self.assertEqual(tree(self.home), picture)
        moving = relocate.status(self.home)
        self.assertEqual(moving["state"], "blocked")
        self.assertRegex(moving["reason"], pattern)
        self.assertEqual(relocate.main(["pending"]), 0)

    def test_both_old_and_new_saves(self):
        (self.legacy.data / "saves/tmu/TrackMania").mkdir(parents=True)
        self.assert_blocked("Both .* exist")

    def test_an_empty_new_folder_is_not_in_the_way(self):
        (self.legacy.data / "saves/tmu").mkdir(parents=True)
        self.migrate()
        self.assertTrue((self.legacy.data / "saves/tmu/TrackMania").is_dir())

    def test_case_variant_target_counts_as_existing(self):
        probe = self.home / "CaseProbe"
        probe.mkdir()
        if not os.path.exists(self.home / "caseprobe"):
            self.skipTest("case-sensitive volume")
        (self.home / "Library/trackmaniamacos/SAVES/TMNF/x").mkdir(parents=True)
        self.assert_blocked("Both .* exist")

    def test_another_volume(self):
        original = relocate._device

        def device(path):
            return original(path) + (1 if "TrackManiaMacOS" in str(path) else 0)

        with mock.patch.object(relocate, "_device", device):
            picture = tree(self.home)
            with self.assertRaisesRegex(relocate.Blocked, "different disk.*never copies"):
                self.migrate()
            self.assertEqual(tree(self.home), picture)

    def test_a_symlinked_saves_folder(self):
        userdata = self.legacy.userdata("tmu")
        userdata.rename(self.home / "MySaves")
        userdata.symlink_to(self.home / "MySaves", target_is_directory=True)
        self.assert_blocked("link or not a folder")

    def test_a_running_game(self):
        with mock.patch.object(
            operation_guard, "check_inactive", side_effect=operation_guard.Busy("Wine is active")
        ):
            picture = tree(self.home)
            with self.assertRaisesRegex(relocate.Busy, "running"):
                self.migrate()
            self.assertEqual(tree(self.home) | {}, picture | {})
            self.assertEqual(relocate.status(self.home)["state"], "blocked")
        self.assertTrue(self.legacy.userdata("tmnf").is_dir())

    def test_a_held_operation_lock(self):
        fd = operation_guard.acquire(self.legacy.root("tmu") / "prefix")
        self.addCleanup(os.close, fd)
        with self.assertRaisesRegex(relocate.Busy, "running"):
            self.migrate()
        self.assertTrue(self.legacy.userdata("tmu").is_dir())
        self.assertFalse(os.path.lexists(self.legacy.data / relocate.JOURNAL))

    def test_control_characters(self):
        (self.legacy.root("tmnf") / ".UserData.tmnf-removing.0123abcd").mkdir()
        bad = self.legacy.data / "saves"
        bad.mkdir(parents=True)
        os.rename(self.legacy.root("tmnf") / ".UserData.tmnf-removing.0123abcd", bad / "x")
        home = self.home / "bad\x01home"
        home.mkdir()
        with mock.patch.dict(os.environ, HOME=str(home)):
            moving = relocate.status(home)
        # Nothing old exists there; a control character in HOME itself blocks.
        self.assertEqual(moving["state"], "blocked")
        self.assertIn("control characters", moving["reason"])


class RelinkCommandTests(Base):
    def test_each_title_gets_the_map_and_the_app_lock(self):
        self.legacy.install("tmnf")
        self.legacy.shared()
        out = self.home / "relink.jsonl"
        script = (
            "import json, os, sys\n"
            "from trackmania_mac.guard import operation_guard\n"
            "operation_guard.app_held()\n"
            "with open(sys.argv[1], 'a') as f:\n"
            "    f.write(json.dumps([os.environ['TRACKMANIA_MAC_TITLE'], "
            "os.environ['TRACKMANIA_MAC_RELOCATION_MAP']]) + '\\n')\n"
        )
        os.environ["PYTHONPATH"] = str(ROOT / "src/python")
        self.migrate(relink=[sys.executable, "-c", script, str(out)])
        calls = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(sorted(call[0] for call in calls), sorted(TITLES))
        mapping = calls[0][1].splitlines()
        old_launchers = str(self.legacy.support / "launchers")
        self.assertIn(
            "prefix\t{}\t{}".format(old_launchers, self.legacy.data / "launchers"), mapping
        )
        self.assertIn(
            "exact\t{}\t{}".format(self.home / "Games/TMNF", self.legacy.data / "games/tmnf"),
            mapping,
        )
        self.assertIn("exact\t{}\t{}".format(self.legacy.support, self.legacy.data), mapping)
        self.assertIn("set\tCACHE\t{}".format(self.legacy.data / "cache"), mapping)


class RemapLinksTests(Base):
    def test_only_links_into_moved_folders_change(self):
        prefix = self.home / "prefix"
        profile = prefix / "drive_c/users/player"
        profile.mkdir(parents=True)
        (prefix / "dosdevices").mkdir()
        (prefix / "dosdevices/d:").symlink_to("/Volumes/CD")
        (profile / "Documents").symlink_to("/old/UserData")
        (profile / "Desktop").symlink_to("/old/userdata/Desktop")
        (profile / "Music").mkdir()
        (profile / ".Videos.tmnf-relink.0123abcd").symlink_to("/stale")
        changed = managed_paths.remap_links(prefix, [("/old/UserData", "/new/saves")])
        self.assertEqual(sorted(path.name for path in changed), ["Desktop", "Documents"])
        self.assertEqual(os.readlink(profile / "Documents"), "/new/saves")
        self.assertEqual(os.readlink(profile / "Desktop"), "/new/saves/Desktop")
        self.assertEqual(os.readlink(prefix / "dosdevices/d:"), "/Volumes/CD")
        self.assertTrue((profile / "Music").is_dir())
        self.assertFalse(os.path.lexists(profile / ".Videos.tmnf-relink.0123abcd"))


class SnapshotReadOnlyTests(Base):
    """`trackmania-mac hub snapshot` changes nothing under HOME, whatever the migration state."""

    def snapshot(self):
        environment = dict(os.environ, HOME=str(self.home), PATH="/usr/bin:/bin")
        result = subprocess.run(
            ["/bin/bash", str(CLI), "hub", "snapshot"],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def assert_read_only(self, state):
        picture = tree(self.home)
        payload = self.snapshot()
        self.assertEqual(tree(self.home), picture)
        self.assertEqual(payload["schema"], 3)
        self.assertEqual(payload["migration"]["state"], state)
        self.assertTrue(all(title["actions"] == [] for title in payload["titles"]))
        return payload

    def test_pending(self):
        self.legacy.install("tmnf")
        self.legacy.shared()
        payload = self.assert_read_only("pending")
        self.assertEqual(payload["migration"]["to"], str(self.legacy.data))
        self.assertFalse(payload["migration"]["partial"])
        self.assertIn(str(self.home / "Games/TMNF"), payload["migration"]["from"])
        self.assertIn(str(self.legacy.support), payload["migration"]["from"])

    def test_blocked(self):
        self.legacy.install("tmnf")
        (self.legacy.data / "saves/tmnf/TmForever").mkdir(parents=True)
        payload = self.assert_read_only("blocked")
        self.assertIn("Both", payload["migration"]["reason"])

    def test_interrupted(self):
        self.legacy.install("tmnf")
        with (
            mock.patch.object(relocate, "_rollback", side_effect=Crash()),
            mock.patch.object(relocate, "_move", side_effect=Crash()),
        ):
            with self.assertRaises(Crash):
                self.migrate()
        self.assert_read_only("pending")


class ShellAutoMigrationTests(Base):
    """Title commands move old files first, unless a location was given explicitly."""

    def run_cli(self, *arguments, **extra):
        environment = dict(os.environ, HOME=str(self.home), PATH="/usr/bin:/bin", **extra)
        return subprocess.run(
            ["/bin/bash", str(CLI), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_title_locations_skip_it(self):
        self.legacy.install("tmnf")
        for name in ("TRACKMANIA_MAC_ROOT", "TRACKMANIA_MAC_PREFIX", "TRACKMANIA_MAC_USERDATA"):
            with self.subTest(name=name):
                result = self.run_cli("doctor", **{name: str(self.home / "custom")})
                self.assertNotIn("Moving your TrackMania files", result.stdout)
                self.assertTrue(self.legacy.userdata("tmnf").is_dir())
                self.assertFalse(os.path.lexists(self.legacy.data / "saves"))

    def test_a_custom_cache_or_support_still_moves_the_titles_first(self):
        for name in ("TRACKMANIA_MAC_CACHE", "TRACKMANIA_MAC_SUPPORT"):
            with self.subTest(name=name):
                self.legacy.install("tmnf")
                self.legacy.shared()
                custom = self.home / "custom"
                result = self.run_cli("doctor", **{name: str(custom)})
                self.assertIn("Moving your TrackMania files", result.stdout, result.stderr)
                self.assertTrue((self.legacy.data / "saves/tmnf/TmForever").is_dir())
                # The overridden shared folder is the user's and stays where it is.
                stays = self.legacy.cache if name == "TRACKMANIA_MAC_CACHE" else self.legacy.support
                self.assertTrue(stays.is_dir())
                import shutil

                for child in list(self.home.iterdir()):
                    shutil.rmtree(child) if child.is_dir() else child.unlink()

    def test_a_blocked_move_refuses_the_command(self):
        self.legacy.install("tmnf")
        (self.legacy.data / "saves/tmnf/TmForever").mkdir(parents=True)
        (self.legacy.data / "saves/tmnf/TmForever/fresh").write_text("new")
        picture = tree(self.home)
        for extra in ({}, {"TRACKMANIA_MAC_CACHE": str(self.home / "custom")}):
            with self.subTest(extra=extra):
                result = self.run_cli("setup", **extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(relocate.NOT_MOVED, result.stderr)
                self.assertIn("nothing was removed", result.stderr)
                self.assertEqual(tree(self.home), picture)
        self.assertFalse(os.path.lexists(self.legacy.data / "games/tmnf/prefix"))

    def test_a_partly_done_move_says_the_apps_may_not_work(self):
        self.legacy.install("tmnf")
        self.legacy.install("tmu")
        original = relocate._move
        dies = failing_at(original, 3, Crash())
        with (
            mock.patch.object(relocate, "_move", dies),
            mock.patch.object(relocate, "_rollback", side_effect=Crash()),
        ):
            with self.assertRaises(Crash):
                relocate.migrate(home=self.home, report=lambda line: None)
        # A moved folder goes missing: the unfinished move is blocked.
        journal = json.loads((self.legacy.data / relocate.JOURNAL).read_text())
        moved = Path(journal["moves"][0]["target"])
        moved.rename(moved.with_name(moved.name + "-elsewhere"))
        self.assertTrue(relocate.status(self.home)["partial"])
        result = self.run_cli("doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(relocate.PARTLY_MOVED, result.stderr)
        self.assertNotIn("keep working", result.stderr)

    def test_no_old_files_creates_nothing(self):
        result = self.run_cli("doctor")
        self.assertNotIn("Moving your TrackMania files", result.stdout)
        self.assertFalse(os.path.lexists(self.legacy.data / "saves"))
        self.assertFalse(os.path.lexists(self.legacy.data / relocate.JOURNAL))

    def test_old_files_move_before_the_command(self):
        self.legacy.install("tmnf")
        result = self.run_cli("doctor")
        self.assertIn("Moving your TrackMania files", result.stdout, result.stderr)
        self.assertTrue((self.legacy.data / "saves/tmnf/TmForever").is_dir())
        self.assertFalse(os.path.lexists(self.home / "Games"))

    def test_hub_run_refuses_while_files_wait(self):
        self.legacy.install("tmnf")
        result = self.run_cli("hub", "run", "tmnf", "doctor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("earlier release", result.stderr)
        self.assertTrue(self.legacy.userdata("tmnf").is_dir())

    def test_hub_migrate_moves_everything(self):
        self.legacy.install("tmnf")
        result = self.run_cli("hub", "migrate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.legacy.data / "saves/tmnf/TmForever").is_dir())
        self.assertEqual(relocate.status(self.home)["state"], "none")


class PathsTests(Base):
    def test_defaults_live_in_one_folder(self):
        title = TITLES["tmu"]
        paths = hub_paths.compute(title)
        data = self.home / "Library/TrackManiaMacOS"
        self.assertEqual(paths.root, data / "games/tmu")
        self.assertEqual(paths.prefix, data / "games/tmu/prefix")
        self.assertEqual(paths.userdata, data / "saves/tmu")
        self.assertEqual(paths.support, data)
        self.assertEqual(paths.cache, data / "cache")
        self.assertEqual(hub_paths.log_dir(), data / "logs")
        self.assertEqual(paths.launcher, data / "launchers/tmu")
        self.assertEqual(paths.icon, data / "icons/tmu.icns")


if __name__ == "__main__":
    unittest.main()


OLD_RELEASE = r"""
set -eu
home="$1" apps="$2"
support="${home}/Library/Application Support/TMNF"
launcher="${support}/launchers/tmnf"
package="${3:-tmnf}" cli="${4:-tmnf}" dialect="${5:-TMNF_}"
mkdir -p "${launcher}/config" "${launcher}/src/python/${package}/app" "${launcher}/src/shell/cli"
touch "${launcher}/.tmnf-launcher" "${launcher}/src/python/${package}/app/launch.py" "${launcher}/src/shell/cli/${cli}"
printf '0.4.3\n' >"${launcher}/VERSION"
TRACKMANIA_MAC_TITLE=tmnf TRACKMANIA_MAC_ROOT="${home}/Games/TMNF" TRACKMANIA_MAC_PREFIX="${home}/Games/TMNF/prefix"
TRACKMANIA_MAC_USERDATA="${home}/Games/TMNF/UserData" TRACKMANIA_MAC_SUPPORT="$support"
TRACKMANIA_MAC_RUNTIME="${support}/runtime/0.4.3" TRACKMANIA_MAC_LAUNCHER="$launcher"
TRACKMANIA_MAC_APP="${apps}/TrackMania Nations Forever.app" TRACKMANIA_MAC_SETTINGS_APP="${apps}/TrackMania Settings.app"
TRACKMANIA_MAC_WINDOW_SIZE=fullscreen TRACKMANIA_MAC_AUDIO=on TRACKMANIA_MAC_X87=on TRACKMANIA_MAC_RENDERER=d9vk
for setting in TRACKMANIA_MAC_TITLE TRACKMANIA_MAC_ROOT TRACKMANIA_MAC_PREFIX TRACKMANIA_MAC_USERDATA TRACKMANIA_MAC_SUPPORT TRACKMANIA_MAC_RUNTIME TRACKMANIA_MAC_LAUNCHER \
  TRACKMANIA_MAC_APP TRACKMANIA_MAC_SETTINGS_APP TRACKMANIA_MAC_WINDOW_SIZE TRACKMANIA_MAC_AUDIO TRACKMANIA_MAC_X87 TRACKMANIA_MAC_RENDERER; do
  name="${dialect}${setting#TRACKMANIA_MAC_}"
  printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$name" "$name" "${!setting}"
done >"${launcher}/config/installation.sh"
for pair in "TrackMania Nations Forever.app:play:local.tmnf.game" "TrackMania Settings.app:configure:local.tmnf.settings"; do
  IFS=: read -r name action identifier <<<"$pair"
  contents="${apps}/${name}/Contents"
  mkdir -p "${contents}/MacOS" "${contents}/Resources"
  printf '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleExecutable</key><string>TMNF</string><key>CFBundleIdentifier</key><string>%s</string></dict></plist>\n' \
    "$identifier" >"${contents}/Info.plist"
  printf 'icon' >"${contents}/Resources/AppIcon.icns"
  touch "${contents}/.tmnf-app"
  {
    printf '#!/bin/bash\n'
    printf 'python_bin=%q\n' "${launcher}/python/bin/python3"
    printf 'PYTHONPATH=%q "$python_bin" -m %s.app.launch %q %q %q\n' "${launcher}/src/python" "$package" \
      "${launcher}/src/shell/cli/${cli}" "$action" "${home}/Library/Logs/TMNF/${action}.log"
  } >"${contents}/MacOS/TMNF"
  chmod +x "${contents}/MacOS/TMNF"
done
"""


class ShellRelinkTests(Base):
    """trackmania-mac migrate points apps and a launcher from an earlier release at the new folders."""

    # The Python package, CLI and setting prefix an installed launcher carries:
    # v0.4.x's by default.
    LAYOUT = ("tmnf", "tmnf", "TMNF_")
    # The locale the earlier release wrote its settings in (C quotes non-ASCII as $'...').
    OLD_LOCALE: typing.Optional[str] = None

    def setUp(self):
        super().setUp()
        self.apps = self.home / "Applications"
        self.apps.mkdir()
        self.legacy.install("tmnf")
        self.legacy.shared()
        environment = dict(os.environ, LC_ALL=self.OLD_LOCALE) if self.OLD_LOCALE else None
        subprocess.run(
            ["/bin/bash", "-c", OLD_RELEASE, "_", str(self.home), str(self.apps), *self.LAYOUT],
            check=True,
            env=environment,
        )

    def run_migrate(self, path="/usr/bin:/bin"):
        environment = dict(
            os.environ,
            HOME=str(self.home),
            PATH=path,
            TRACKMANIA_MAC_APPS_DIR=str(self.apps),
        )
        return subprocess.run(
            ["/bin/bash", str(CLI), "migrate"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    SETTINGS = ("ROOT", "PREFIX", "USERDATA", "SUPPORT", "RUNTIME", "LAUNCHER", "APP")
    SETTINGS += ("WINDOW_SIZE", "CACHE")

    def recorded(self):
        config = self.legacy.data / "launchers/tmnf/config/installation.sh"
        script = '. "$1"; for n in "${@:2}"; do printf "%s=%s\\n" "$n" "${!n-}"; done'
        names = [self.LAYOUT[2] + name for name in self.SETTINGS]
        result = subprocess.run(
            ["/usr/bin/env", "-i", "/bin/bash", "-c", script, "_", str(config), *names],
            capture_output=True,
            text=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def test_launcher_config_and_wrappers_follow_the_move(self):
        result = self.run_migrate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.legacy.data
        self.assertEqual(
            self.recorded(),
            {
                self.LAYOUT[2] + "ROOT": str(data / "games/tmnf"),
                self.LAYOUT[2] + "PREFIX": str(data / "games/tmnf/prefix"),
                self.LAYOUT[2] + "USERDATA": str(data / "saves/tmnf"),
                self.LAYOUT[2] + "SUPPORT": str(data),
                self.LAYOUT[2] + "RUNTIME": str(data / "runtime/0.4.3"),
                self.LAYOUT[2] + "LAUNCHER": str(data / "launchers/tmnf"),
                self.LAYOUT[2] + "APP": str(self.apps / "TrackMania Nations Forever.app"),
                self.LAYOUT[2] + "WINDOW_SIZE": "fullscreen",
                self.LAYOUT[2] + "CACHE": str(data / "cache"),
            },
        )
        for name, log in (
            ("TrackMania Nations Forever.app", "play.log"),
            ("TrackMania Settings.app", "configure.log"),
        ):
            contents = self.apps / name / "Contents"
            # The executable is renamed and the plist names it; the old one is gone.
            wrapper = (contents / "MacOS" / markers.WRAPPER_EXECUTABLE).read_text()
            self.assertFalse(os.path.lexists(contents / "MacOS" / legacy.WRAPPER_EXECUTABLE))
            self.assertIn(
                "<string>{}</string>".format(markers.WRAPPER_EXECUTABLE),
                (contents / "Info.plist").read_text(),
            )
            self.assertIn(quoted(str(data / "launchers/tmnf/python/bin/python3")), wrapper)
            self.assertIn(quoted(str(data / "logs" / log)), wrapper)
            self.assertNotIn("Application Support", wrapper)
            self.assertNotIn("Library/Logs", wrapper)
            self.assertEqual((contents / "Resources/AppIcon.icns").read_text(), "icon")
            package, cli, _dialect = self.LAYOUT
            # The launcher keeps its own code until Update: its own module and CLI.
            self.assertIn("-m {}.app.launch ".format(package), wrapper)
            self.assertIn(quoted(str(data / "launchers/tmnf/src/shell/cli" / cli)), wrapper)
            self.assertIn("<string>0.4.3</string>", (contents / "Info.plist").read_text())
            # Converted at the relink: this release's bundle identifier and marker only.
            title = TITLES["tmnf"]
            wanted = title.bundle_id_game if log == "play.log" else title.bundle_id_settings
            self.assertIn(
                "<string>{}</string>".format(wanted), (contents / "Info.plist").read_text()
            )
            self.assertTrue((contents / markers.APP).is_file())
            self.assertFalse(os.path.lexists(contents / legacy.MARKERS["APP"]))
        # A second run has nothing left to do.
        self.assertEqual(relocate.status(self.home)["state"], "none")

    def test_an_interrupted_marker_conversion_leaves_the_app_ours(self):
        # The relink stops right after writing the new marker, before removing the old.
        fake = self.home / "fake-bin"
        fake.mkdir()
        (fake / "rm").write_text(
            '#!/bin/bash\nfor a in "$@"; do case "$a" in */{}) exit 1 ;; esac; done\n'
            'exec /bin/rm "$@"\n'.format(legacy.MARKERS["APP"])
        )
        (fake / "rm").chmod(0o755)
        result = self.run_migrate(path="{}:/usr/bin:/bin".format(fake))
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = self.apps / "TrackMania Nations Forever.app/Contents"
        self.assertTrue((contents / markers.APP).is_file())
        self.assertTrue((contents / legacy.MARKERS["APP"]).is_file())
        title = TITLES["tmnf"]
        for name, kind in (
            ("TrackMania Nations Forever.app", "game-app"),
            ("TrackMania Settings.app", "settings-app"),
        ):
            entry = uninstall.app_entry(str(self.apps / name), title, kind)
            self.assertEqual(entry.verb, "remove", (name, entry))
            self.assertTrue(state._app_ready(self.apps / name), name)

    def interrupted(self, command, match):
        """Run the migration with COMMAND failing on an argument ending in MATCH."""
        fake = self.home / "fake-bin"
        fake.mkdir()
        (fake / command).write_text(
            '#!/bin/bash\nfor a in "$@"; do case "$a" in */{}) exit 1 ;; esac; done\n'
            'exec /bin/{} "$@"\n'.format(match, command)
        )
        (fake / command).chmod(0o755)
        result = self.run_migrate(path="{}:/usr/bin:/bin".format(fake))
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.apps / "TrackMania Nations Forever.app/Contents"

    def assert_launchable(self, contents):
        """The plist names an executable that exists, and the app is still ours."""
        named = plistlib.loads((contents / "Info.plist").read_bytes())["CFBundleExecutable"]
        self.assertTrue((contents / "MacOS" / named).is_file(), named)
        self.assertEqual(uninstall.wrapper_executable(contents), contents / "MacOS" / named)
        self.assertTrue(state._app_ready(contents.parent))
        return named

    def test_stopped_before_the_plist_is_replaced_the_old_executable_still_runs(self):
        contents = self.interrupted("mv", "Info.plist")
        self.assertEqual(self.assert_launchable(contents), legacy.WRAPPER_EXECUTABLE)
        self.assertTrue((contents / "MacOS" / markers.WRAPPER_EXECUTABLE).is_file())

    def test_stopped_before_the_old_executable_is_removed_the_new_one_runs(self):
        contents = self.interrupted("rm", "MacOS/" + legacy.WRAPPER_EXECUTABLE)
        self.assertEqual(self.assert_launchable(contents), markers.WRAPPER_EXECUTABLE)
        self.assertTrue((contents / "MacOS" / legacy.WRAPPER_EXECUTABLE).is_file())

    def test_a_wrapper_using_another_launcher_is_left_alone(self):
        wrapper = self.apps / "TrackMania Settings.app/Contents/MacOS" / legacy.WRAPPER_EXECUTABLE
        wrapper.write_text("#!/bin/bash\npython_bin=/elsewhere/python3\n")
        result = self.run_migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wrapper.read_text(), "#!/bin/bash\npython_bin=/elsewhere/python3\n")
        self.assertIn("uses another launcher", result.stderr)


class InjectedSettingsTests(ShellRelinkTests):
    def test_a_setting_line_carrying_a_command_is_never_run(self):
        config = self.legacy.support / "launchers/tmnf/config/installation.sh"
        marker = self.home / "injected"
        line = 'if test -z "${{TMNF_ROOT:-}}"; then export TMNF_ROOT=/x; touch {}; fi\n'
        with config.open("a") as stream:
            stream.write(line.format(marker))
        before = config.read_text()
        result = self.run_migrate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(marker))
        moved = self.legacy.data / "launchers/tmnf/config/installation.sh"
        self.assertEqual(moved.read_text(), before)
        self.assertIn("not in the expected form", result.stderr)

    test_launcher_config_and_wrappers_follow_the_move = None  # type: ignore[assignment]
    test_a_wrapper_using_another_launcher_is_left_alone = None  # type: ignore[assignment]
    test_an_interrupted_marker_conversion_leaves_the_app_ours = None  # type: ignore[assignment]
    test_stopped_before_the_plist_is_replaced_the_old_executable_still_runs = None  # type: ignore[assignment]
    test_stopped_before_the_old_executable_is_removed_the_new_one_runs = None  # type: ignore[assignment]


class NonAsciiHomeRelinkTests(ShellRelinkTests):
    """A home folder name outside ASCII, with settings quoted as $'...' by a C locale."""

    HOME_NAME = "zo\u00eb caf\u00e9"
    OLD_LOCALE = "C"


class NewLayoutRelinkTests(ShellRelinkTests):
    """A launcher that already carries this release's package and CLI."""

    LAYOUT = ("trackmania_mac", CLI.name, "TRACKMANIA_MAC_")
