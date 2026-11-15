"""Game-folder saves: kept in the saves folder and linked from the game folder.

Every fixture lives in a temporary folder under /private/tmp; nothing here
touches a real prefix or saves folder.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from trackmania_mac.hub import registry
from trackmania_mac.install import game_saves


ROOT = Path(__file__).resolve().parents[1]
ESWC = registry.get("tmn-eswc", ROOT / "config/titles")
TWO = ("GameData/Profiles", "GameData/Tracks/Replays/Autosaves")


def tree(folder: Path) -> dict:
    """Every path under FOLDER with what it is: a folder, a link's target or a file's text."""
    found = {}
    for root, directories, files in os.walk(folder):
        for name in directories + files:
            path = Path(root) / name
            key = str(path.relative_to(folder))
            if path.is_symlink():
                found[key] = "-> " + os.readlink(path)
            elif path.is_dir():
                found[key] = "dir"
            else:
                found[key] = path.read_text()
    return found


class GameSavesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="trackmania-mac-saves-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.game = self.base / "prefix/drive_c/Program Files (x86)" / ESWC.game_dir_name
        self.saves = self.base / "saves"
        self.game.mkdir(parents=True)
        self.saves.mkdir()
        self.title = ESWC._replace(game_save_dirs=TWO)
        self.reports: list = []

    def kept(self, relative: str) -> Path:
        return self.saves / ESWC.user_subdir / relative

    def recovered(self, relative: str) -> Path:
        return self.saves / ESWC.user_subdir / game_saves.RECOVERED / relative

    def file(self, path: Path, text: str = "x") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def link(self, title=None) -> list:
        return game_saves.run(
            "link", str(self.game), str(self.saves), title or self.title, self.reports.append
        )

    def check(self, title=None) -> list:
        return game_saves.run("check", str(self.game), str(self.saves), title or self.title)

    def assertLinked(self, relative: str) -> None:
        link = self.game / relative
        self.assertTrue(link.is_symlink(), link)
        self.assertEqual(os.readlink(link), str(self.kept(relative)))
        self.assertTrue(self.kept(relative).is_dir())
        self.assertFalse(self.kept(relative).is_symlink())

    # --- linking -----------------------------------------------------------

    def test_missing_folders_are_created_in_the_saves_folder_and_linked(self):
        self.assertEqual(self.link(ESWC), [])
        for relative in ESWC.game_save_dirs:
            self.assertLinked(relative)
        self.assertEqual(self.check(ESWC), [])
        before = tree(self.base)
        self.assertEqual(self.link(ESWC), [])
        self.assertEqual(tree(self.base), before)
        self.assertEqual(self.reports, [])

    def test_saves_in_a_real_folder_move_to_the_saves_folder(self):
        self.file(self.game / "GameData/Profiles/Player/profile.gbx", "mine")
        self.file(self.game / "GameData/Profiles/.hidden", "dot")
        self.assertEqual(self.link(), [])
        self.assertLinked("GameData/Profiles")
        self.assertEqual(
            (self.kept("GameData/Profiles") / "Player/profile.gbx").read_text(), "mine"
        )
        self.assertEqual((self.kept("GameData/Profiles") / ".hidden").read_text(), "dot")
        # Reads through the link see the moved files.
        self.assertEqual((self.game / "GameData/Profiles/Player/profile.gbx").read_text(), "mine")
        self.assertTrue(any("Moved the saves" in line for line in self.reports))
        self.assertFalse(self.recovered("GameData/Profiles").exists())

    def test_on_a_clash_the_saves_folder_wins_and_nothing_is_overwritten(self):
        relative = "GameData/Tracks/Replays/Autosaves"
        self.file(self.kept(relative) / "Race.Replay.Gbx", "kept")
        self.file(self.recovered(relative) / "Race.Replay.Gbx", "earlier")
        self.file(self.game / relative / "Race.Replay.Gbx", "game")
        self.file(self.game / relative / "New.Replay.Gbx", "new")
        self.assertEqual(self.link(), [])
        self.assertLinked(relative)
        self.assertEqual((self.kept(relative) / "Race.Replay.Gbx").read_text(), "kept")
        self.assertEqual((self.kept(relative) / "New.Replay.Gbx").read_text(), "new")
        self.assertEqual((self.recovered(relative) / "Race.Replay.Gbx").read_text(), "earlier")
        self.assertEqual((self.recovered(relative) / "Race (2).Replay.Gbx").read_text(), "game")
        self.assertTrue(any("already has Race.Replay.Gbx" in line for line in self.reports))

    def test_a_clashing_folder_moves_whole_and_a_name_without_extension_is_numbered(self):
        self.file(self.kept("GameData/Profiles/Player/a"), "kept")
        self.file(self.recovered("GameData/Profiles/Player/b"), "earlier")
        self.file(self.game / "GameData/Profiles/Player/c", "game")
        self.assertEqual(self.link(), [])
        self.assertEqual(
            sorted(os.listdir(self.recovered("GameData/Profiles"))), ["Player", "Player (2)"]
        )
        self.assertEqual((self.recovered("GameData/Profiles") / "Player (2)/c").read_text(), "game")
        self.assertFalse((self.kept("GameData/Profiles") / "Player/c").exists())

    def test_names_are_compared_ignoring_case_and_existing_spellings_are_reused(self):
        self.file(self.game / "gamedata/profiles/Player/p.gbx", "mine")
        self.file(self.saves / ESWC.user_subdir.lower() / "GAMEDATA/Profiles/player/q.gbx", "kept")
        self.assertEqual(self.link(), [])
        self.assertEqual(os.listdir(self.game), ["gamedata"])
        self.assertIn("profiles", os.listdir(self.game / "gamedata"))
        self.assertTrue((self.game / "gamedata/profiles").is_symlink())
        self.assertEqual(os.listdir(self.saves), [ESWC.user_subdir.lower()])
        target = self.saves / ESWC.user_subdir.lower() / "GAMEDATA/Profiles"
        self.assertEqual(os.readlink(self.game / "gamedata/profiles"), str(target))
        # "Player" clashes with "player": the saves folder's copy stays.
        self.assertEqual(os.listdir(target), ["player"])
        recovered = self.saves / ESWC.user_subdir.lower() / game_saves.RECOVERED
        self.assertEqual(
            (recovered / "GameData/Profiles/Player/p.gbx").read_text(),
            "mine",
        )
        self.assertEqual(self.check(), [])

    def test_a_link_to_the_saves_folder_gets_its_folder_back(self):
        self.assertEqual(self.link(), [])
        self.kept("GameData/Profiles").rmdir()
        # Doctor says so (the game could not write there); link repairs it quietly.
        (warning,) = self.check()
        self.assertIn("links to the missing", warning)
        self.assertEqual(self.link(), [])
        self.assertLinked("GameData/Profiles")
        self.assertEqual(self.check(), [])

    def test_check_inspects_the_saves_end_of_a_correct_link(self):
        self.assertEqual(self.link(), [])
        # A symlink planted in the saves folder (say through the Documents link).
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        self.kept("GameData/Profiles").rmdir()
        self.kept("GameData/Profiles").symlink_to(elsewhere)
        (warning,) = self.check()
        self.assertIn("is a symlink", warning)
        (warning,) = self.link()
        self.assertIn("is a symlink", warning)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_names_matching_in_several_cases_are_refused(self):
        self.file(self.game / "GameData/Profiles/p.gbx", "mine")
        real_listdir = os.listdir
        game_data = str(self.game / "GameData")

        def listdir(path="."):
            names = real_listdir(path)
            # What a case-sensitive volume can hold: two spellings side by side.
            return names + ["profiles"] if str(path) == game_data else names

        self.assertEqual(game_saves._matches(self.game / "GameData", "Profiles"), ["Profiles"])
        with mock.patch.object(game_saves.os, "listdir", side_effect=listdir):
            self.assertEqual(
                game_saves._matches(self.game / "GameData", "PROFILES"), ["Profiles", "profiles"]
            )
            self.assertEqual(
                game_saves._matches(self.game / "GameData", "profiles"), ["profiles", "Profiles"]
            )
            warnings = self.link()
            checked = self.check()
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 entries named Profiles in different cases", warnings[0])
        self.assertIn("different cases", checked[0])
        self.assertEqual((self.game / "GameData/Profiles/p.gbx").read_text(), "mine")
        self.assertFalse((self.game / "GameData/Profiles").is_symlink())

    def test_a_link_spelled_another_way_to_the_same_folder_is_kept(self):
        self.kept("GameData/Profiles").mkdir(parents=True)
        (self.game / "GameData").mkdir()
        spelled = str(self.kept("GameData/Profiles")).replace("/private/tmp/", "/tmp/", 1)
        (self.game / "GameData/Profiles").symlink_to(spelled)
        self.assertEqual(self.link(), [])
        self.assertEqual(os.readlink(self.game / "GameData/Profiles"), spelled)
        self.assertEqual(self.check(), [])

    def test_a_link_elsewhere_is_kept_unless_it_leads_nowhere(self):
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.game / "GameData").mkdir()
        link = self.game / "GameData/Profiles"
        link.symlink_to(elsewhere)
        (warning,) = self.link(self.title._replace(game_save_dirs=("GameData/Profiles",)))
        self.assertIn("links to {}".format(elsewhere), warning)
        self.assertEqual(os.readlink(link), str(elsewhere))
        self.assertEqual(len(self.check()), 2)
        # Dangling, and the saves folder is missing too: kept, with a warning.
        elsewhere.rmdir()
        (warning,) = self.link(self.title._replace(game_save_dirs=("GameData/Profiles",)))
        self.assertIn("missing", warning)
        self.assertEqual(os.readlink(link), str(elsewhere))
        # Dangling with the saves folder present (it moved): relinked.
        self.file(self.kept("GameData/Profiles/Player/p.gbx"))
        self.assertEqual(self.link(), [])
        self.assertLinked("GameData/Profiles")
        self.assertEqual(
            [name for name in os.listdir(self.game / "GameData") if name.startswith(".")], []
        )

    def test_a_file_or_symlinked_folder_on_the_way_is_refused(self):
        self.file(self.game / "GameData/Profiles", "a file")
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.game / "GameData/Tracks").symlink_to(elsewhere)
        warnings = self.link()
        self.assertEqual(len(warnings), 2)
        self.assertIn("GameData/Profiles: ", warnings[0])
        self.assertIn("is not a folder", warnings[0])
        self.assertIn("is a symlink", warnings[1])
        self.assertEqual((self.game / "GameData/Profiles").read_text(), "a file")
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertEqual(len(self.check()), 2)

    def test_a_symlink_in_the_saves_folder_is_refused(self):
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.saves / ESWC.user_subdir).symlink_to(elsewhere)
        self.file(self.game / "GameData/Profiles/p.gbx", "mine")
        warnings = self.link()
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all("is a symlink" in warning for warning in warnings))
        self.assertEqual((self.game / "GameData/Profiles/p.gbx").read_text(), "mine")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_saves_folder_on_another_volume_is_skipped(self):
        self.file(self.game / "GameData/Profiles/p.gbx", "mine")
        real_lstat = os.lstat
        target = str(self.kept("GameData/Profiles"))

        def lstat(path, *arguments, **keywords):
            info = real_lstat(path, *arguments, **keywords)
            if str(path) == target:
                values = list(info)
                values[2] = info.st_dev + 1
                return os.stat_result(values)
            return info

        with mock.patch.object(game_saves.os, "lstat", side_effect=lstat):
            warnings = self.link()
        (warning,) = warnings
        self.assertIn("another volume", warning)
        self.assertEqual((self.game / "GameData/Profiles/p.gbx").read_text(), "mine")
        self.assertFalse((self.game / "GameData/Profiles").is_symlink())
        self.assertLinked("GameData/Tracks/Replays/Autosaves")

    def test_an_interrupted_move_is_finished_by_the_next_run(self):
        for name in ("a", "b", "c"):
            self.file(self.game / "GameData/Profiles" / name, name)
        real = game_saves._rename_new
        calls = []

        def stop_after_one(source, target):
            if calls:
                raise OSError(5, "Input/output error", str(source))
            calls.append(source)
            real(source, target)

        with mock.patch.object(game_saves, "_rename_new", side_effect=stop_after_one):
            (warning,) = self.link()
        self.assertIn("Input/output error", warning)
        self.assertFalse((self.game / "GameData/Profiles").is_symlink())
        self.assertEqual(os.listdir(self.kept("GameData/Profiles")), ["a"])
        self.assertEqual(self.link(), [])
        self.assertLinked("GameData/Profiles")
        self.assertEqual(sorted(os.listdir(self.kept("GameData/Profiles"))), ["a", "b", "c"])
        self.assertFalse(self.recovered("GameData/Profiles").exists())

    def test_a_run_stopped_between_emptying_and_linking_links_next_time(self):
        self.file(self.game / "GameData/Profiles/p.gbx", "mine")
        with mock.patch.object(game_saves.os, "symlink", side_effect=OSError(28, "No space")):
            warnings = self.link()
        self.assertEqual(len(warnings), 2)
        self.assertFalse(os.path.lexists(self.game / "GameData/Profiles"))
        self.assertEqual((self.kept("GameData/Profiles") / "p.gbx").read_text(), "mine")
        self.assertEqual(self.link(), [])
        self.assertLinked("GameData/Profiles")

    def test_check_never_changes_anything(self):
        self.file(self.game / "GameData/Profiles/p.gbx", "mine")
        (self.game / "GameData/Tracks").mkdir()
        before = tree(self.base)
        warnings = self.check()
        self.assertEqual(tree(self.base), before)
        self.assertIn("saves are in the game folder", warnings[0])
        self.assertIn("not linked", warnings[1])

    def test_a_title_without_game_save_dirs_does_nothing(self):
        title = registry.get("tmnf", ROOT / "config/titles")
        self.assertEqual(title.game_save_dirs, ())
        self.assertNotIn("game-saves", title.components)
        self.assertEqual(game_saves.run("link", "/nonexistent", "/nonexistent", title), [])

    def test_the_game_and_saves_folders_must_be_real_folders(self):
        link = self.base / "game-link"
        link.symlink_to(self.game)
        with self.assertRaisesRegex(ValueError, "Not a game folder"):
            game_saves.run("link", str(link), str(self.saves), self.title)
        with self.assertRaisesRegex(ValueError, "saves folder is missing"):
            game_saves.run("link", str(self.game), str(self.base / "none"), self.title)
        with self.assertRaisesRegex(ValueError, "absolute"):
            game_saves.run("link", "game", str(self.saves), self.title)

    def test_command_line_reports_warnings_and_exit_status(self):
        environment = {"TRACKMANIA_MAC_TITLE": "tmn-eswc", "TRACKMANIA_MAC_TITLES_DIR": ""}
        arguments = [str(self.game), str(self.saves)]
        with mock.patch.dict(os.environ, environment):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(game_saves.main(["check", *arguments]), 1)
            self.assertEqual(stderr.getvalue().count("warning: "), len(ESWC.game_save_dirs))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(game_saves.main(["link", *arguments]), 0)
                self.assertEqual(game_saves.main(["check", *arguments]), 0)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(
                    game_saves.main(["link", str(self.base / "none"), *arguments[1:]]), 1
                )
            self.assertIn("error: ", stderr.getvalue())

    # --- the uninstall check -----------------------------------------------

    def test_saves_left_finds_only_real_folders_holding_files(self):
        prefix = self.base / "prefix"
        self.assertIsNone(game_saves.saves_left(prefix, self.title))
        (self.game / "GameData/Profiles/Empty/Deeper").mkdir(parents=True)
        self.assertIsNone(game_saves.saves_left(prefix, self.title))
        save = self.file(self.game / "GameData/Profiles/Empty/Deeper/p.gbx")
        self.assertEqual(game_saves.saves_left(prefix, self.title), self.game / "GameData/Profiles")
        with self.assertRaisesRegex(ValueError, "Saves are still in the game folder"):
            game_saves.check_prefix(prefix, self.title)
        save.unlink()
        self.assertEqual(self.link(), [])
        self.file(self.kept("GameData/Profiles/p.gbx"))
        self.assertIsNone(game_saves.saves_left(prefix, self.title))
        game_saves.check_prefix(prefix, self.title)

    def test_saves_left_follows_links_that_stay_inside_the_prefix(self):
        prefix = self.base / "prefix"
        hidden = prefix / "drive_c/users/player/AppData/TMN"
        self.file(hidden / "Profiles/Player/p.gbx", "mine")
        (self.game / "GameData").mkdir()
        link = self.game / "GameData/Profiles"
        link.symlink_to(hidden / "Profiles")
        self.assertEqual(game_saves.saves_left(prefix, self.title), hidden / "Profiles")
        with self.assertRaisesRegex(ValueError, "Saves are still in the game folder"):
            game_saves.check_prefix(prefix, self.title)
        # A symlinked ancestor inside the prefix is followed too.
        link.unlink()
        (self.game / "GameData").rmdir()
        (self.game / "GameData").symlink_to(hidden)
        self.assertEqual(game_saves.saves_left(prefix, self.title), hidden / "Profiles")
        # A relative link into the prefix counts the same.
        (self.game / "GameData").unlink()
        (self.game / "GameData").symlink_to("../../users/player/AppData/TMN")
        self.assertEqual(game_saves.saves_left(prefix, self.title), hidden / "Profiles")

    def test_saves_left_skips_links_out_of_the_prefix_and_nowhere(self):
        prefix = self.base / "prefix"
        self.assertEqual(self.link(), [])  # links into the saves folder, outside
        self.file(self.kept("GameData/Profiles/p.gbx"))
        self.assertIsNone(game_saves.saves_left(prefix, self.title))
        (self.game / "GameData/Profiles").unlink()
        (self.game / "GameData/Profiles").symlink_to(self.base / "missing")
        self.assertIsNone(game_saves.saves_left(prefix, self.title))
        (self.game / "GameData/Profiles").unlink()
        (self.game / "GameData/Profiles").symlink_to("Profiles")  # a loop
        self.assertIsNone(game_saves.saves_left(prefix, self.title))

    def test_saves_left_checks_every_spelling_of_a_name(self):
        prefix = self.base / "prefix"
        self.assertEqual(self.link(), [])
        # The link and, beside it, a real folder holding files under another name
        # that the matcher reports as the same (a case-sensitive volume).
        self.file(self.game / "GameData/Profiles2/p.gbx", "mine")
        real_matches = game_saves._matches

        def matches(folder, name):
            if name == "Profiles":
                return ["Profiles", "Profiles2"]
            return real_matches(folder, name)

        with mock.patch.object(game_saves, "_matches", side_effect=matches):
            self.assertEqual(
                game_saves.saves_found(prefix, self.title), [self.game / "GameData/Profiles2"]
            )
        self.assertIsNone(game_saves.saves_left(prefix, self.title))

    def test_numbered_names_keep_their_extensions(self):
        self.assertEqual(game_saves._numbered("Race.Replay.Gbx", 2), "Race (2).Replay.Gbx")
        self.assertEqual(game_saves._numbered("Player", 3), "Player (3)")
        self.assertEqual(game_saves._numbered(".hidden", 2), ".hidden (2)")


if __name__ == "__main__":
    unittest.main()
