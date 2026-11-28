"""The default settings file install seeds into a game folder (game_system_config).

Fixtures live in temporary folders; nothing touches a real prefix.
"""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from trackmania_mac.hub import registry
from trackmania_mac.install import system_config


ROOT = Path(__file__).resolve().parents[1]
NAME = "Default.SystemConfig.Gbx"
SETTINGS = json.loads((ROOT / "config/game-settings.json").read_text())

# The 252-byte seed (windowed 1280x960, 3 Frames synchro, PC3 quality chunk).
# On a scratch copy of an installed prefix the game started with it and kept
# the chunk as written; a fresh install with this seed has not been looked at.
KNOWN_GOOD_1280X960 = (
    b"GBX\x06\x00TUUE"
    b"b005000\r\n0\r\n1\r\n0\r\n"
    b"b005020\r\n1397442896\r\n189\r\nFalse\r\n"
    b"b013001\r\n1397442896\r\n37\r\n1280\r\n960\r\n3\r\n1\r\n0\r\n0\r\nFalse\r\nFalse\r\n"
    b"b013003\r\n1397442896\r\n33\r\nFalse\r\n1\r\nFalse\r\n0\r\nFalse\r\nTrue\r\n"
    b"b01300d\r\n1397442896\r\n27\r\n1\r\nTrue\r\n0\r\n3\r\n3\r\n3\r\n3\r\n3\r\n"
    b"facade01\r\nfacade01\r\n"
)


class TextGbxTests(unittest.TestCase):
    def test_bytes_match_the_expected_seed(self):
        data = system_config.text_2006({"width": 1280, "height": 960})
        self.assertEqual(data, KNOWN_GOOD_1280X960)
        self.assertEqual(len(data), 252)

    def test_other_sizes_change_only_the_size_and_the_lengths_that_cover_it(self):
        data = system_config.text_2006({"width": 1280, "height": 720})
        expected = KNOWN_GOOD_1280X960.replace(b"\r\n960\r\n", b"\r\n720\r\n")
        self.assertEqual(data, expected)
        wide = system_config.text_2006({"width": 10240, "height": 4320})
        tokens = wide[len(system_config.TEXT_2006_HEADER) :].split(b"\r\n")
        # b005020's length covers every token after it up to its own facade01;
        # b013001's covers its eight tokens.
        self.assertEqual(tokens[4:7], [b"b005020", b"1397442896", b"191"])
        self.assertEqual(tokens[8:11], [b"b013001", b"1397442896", b"39"])
        inner = tokens[7 : tokens.index(b"facade01") + 1]
        self.assertEqual(sum(len(token) + 2 for token in inner), 191)
        self.assertEqual(tokens[-2:], [b"facade01", b""])

    def test_windowed_with_3_frames_synchro_and_no_install_key(self):
        tokens = KNOWN_GOOD_1280X960[9:].split(b"\r\n")
        display = tokens[tokens.index(b"b013001") + 3 :][:8]
        self.assertEqual(display[7], b"False")  # fullscreen
        synchro = tokens[tokens.index(b"b013003") + 3 :][:6]
        self.assertEqual(synchro[1], b"1")  # 3 Frames
        self.assertNotIn(b"b005012", KNOWN_GOOD_1280X960)

    def test_seeds_the_pc3_quality_chunk_after_the_synchro_chunk(self):
        tokens = KNOWN_GOOD_1280X960[9:].split(b"\r\n")
        order = [t for t in tokens if t in (b"b013001", b"b013003", b"b01300d", b"facade01")]
        self.assertEqual(order, [b"b013001", b"b013003", b"b01300d", b"facade01", b"facade01"])
        start = tokens.index(b"b01300d")
        body = tokens[start + 3 : start + 11]
        self.assertEqual(body, [b"1", b"True", b"0", b"3", b"3", b"3", b"3", b"3"])
        self.assertEqual(int(tokens[start + 2]), sum(len(t) + 2 for t in body))
        self.assertEqual([t.decode() for t in body], system_config.PC3_QUALITY_TOKENS)

    def test_size_is_validated_like_the_forever_profile(self):
        for settings in (
            {},
            {"width": 1280},
            {"width": 0, "height": 720},
            {"width": 1280, "height": -1},
            {"width": True, "height": 720},
            {"width": "1280", "height": 720},
            {"width": 1280.0, "height": 720},
            {"width": 0x80000000, "height": 720},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                system_config.text_2006(settings)
        system_config.text_2006({"width": 0x7FFFFFFF, "height": 1})

    def test_every_registry_format_has_a_generator(self):
        self.assertEqual(tuple(system_config.GENERATORS), registry.SYSTEM_CONFIG_FORMATS)
        with self.assertRaisesRegex(ValueError, "Unknown settings format"):
            system_config.generate("binary-2008", SETTINGS)


class SeedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="trackmania-mac-sc-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.game = self.base / "TmNationsESWC"
        self.game.mkdir()
        self.data = system_config.text_2006(SETTINGS)

    def leftovers(self):
        return [name for name in os.listdir(self.game) if name.startswith(".")]

    def test_writes_a_complete_0644_file_when_none_exists(self):
        old = os.umask(0o077)
        try:
            self.assertTrue(system_config.seed(str(self.game), NAME, self.data))
        finally:
            os.umask(old)
        path = self.game / NAME
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o644)
        self.assertEqual(os.lstat(path).st_nlink, 1)
        self.assertEqual(self.leftovers(), [])

    def test_never_overwrites_anything_at_that_name(self):
        elsewhere = self.base / "elsewhere"
        elsewhere.write_bytes(b"theirs")
        cases = {
            "file": lambda path: path.write_bytes(b"launcher wrote this"),
            "other case": lambda path: (path.parent / NAME.lower()).write_bytes(b"lower"),
            "folder": lambda path: path.mkdir(),
            "symlink": lambda path: path.symlink_to(elsewhere),
            "dangling symlink": lambda path: path.symlink_to(self.base / "missing"),
        }
        for label, make in cases.items():
            with self.subTest(label):
                for child in os.listdir(self.game):
                    target = self.game / child
                    if target.is_dir() and not target.is_symlink():
                        target.rmdir()
                    else:
                        target.unlink()
                make(self.game / NAME)
                before = {
                    child: (self.game / child).is_symlink() for child in os.listdir(self.game)
                }
                self.assertFalse(system_config.seed(str(self.game), NAME, self.data))
                after = {child: (self.game / child).is_symlink() for child in os.listdir(self.game)}
                self.assertEqual(after, before)
                self.assertEqual(elsewhere.read_bytes(), b"theirs")
                self.assertFalse((self.base / "missing").exists())
        (self.game / NAME).unlink()
        (self.game / NAME).write_bytes(b"launcher wrote this")
        self.assertFalse(system_config.seed(str(self.game), NAME, self.data))
        self.assertEqual((self.game / NAME).read_bytes(), b"launcher wrote this")

    def test_a_file_appearing_during_the_write_wins(self):
        real_link = os.link

        def racing_link(source, target):
            Path(target).write_bytes(b"first")
            return real_link(source, target)

        with mock.patch.object(system_config.os, "link", side_effect=racing_link):
            self.assertFalse(system_config.seed(str(self.game), NAME, self.data))
        self.assertEqual((self.game / NAME).read_bytes(), b"first")
        self.assertEqual(self.leftovers(), [])

    def test_refuses_a_symlinked_or_relative_game_folder_and_unsafe_names(self):
        link = self.base / "link"
        link.symlink_to(self.game)
        with self.assertRaisesRegex(ValueError, "Not a game folder"):
            system_config.seed(str(link), NAME, self.data)
        with self.assertRaisesRegex(ValueError, "absolute"):
            system_config.seed("TmNationsESWC", NAME, self.data)
        for name in ("../x.Gbx", "a/b", ".hidden", "a b"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                system_config.seed(str(self.game), name, self.data)
        self.assertEqual(os.listdir(self.game), [])

    def test_stale_temporary_files_are_swept(self):
        stale = self.game / (system_config.TEMP_PREFIX + "abc123")
        stale.write_bytes(b"partial")
        self.assertTrue(system_config.seed(str(self.game), NAME, self.data))
        self.assertEqual(self.leftovers(), [])

    def run_main(self, title, settings=ROOT / "config/game-settings.json"):
        environment = {"TRACKMANIA_MAC_TITLE": title, "TRACKMANIA_MAC_TITLES_DIR": ""}
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment), redirect_stdout(stdout):
            with redirect_stderr(stderr):
                status = system_config.main(["seed", str(self.game), str(settings)])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_command_line_seeds_from_the_registry_and_game_settings(self):
        status, _, _ = self.run_main("tmnf")
        self.assertEqual(status, 0)
        self.assertEqual(os.listdir(self.game), [])
        status, _, _ = self.run_main("tmn-eswc", self.base / "missing.json")
        self.assertEqual(status, 1)
        self.assertEqual(os.listdir(self.game), [])
        status, stdout, stderr = self.run_main("tmn-eswc")
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Wrote the default settings", stdout)
        self.assertEqual((self.game / NAME).read_bytes(), self.data)
        status, stdout, _ = self.run_main("tmn-eswc")
        self.assertEqual(status, 0)
        self.assertIn("Kept the existing", stdout)
        bad = self.base / "bad.json"
        bad.write_text(json.dumps(dict(SETTINGS, width=0)))
        (self.game / NAME).unlink()
        status, _, stderr = self.run_main("tmn-eswc", bad)
        self.assertEqual(status, 1)
        self.assertIn("width must be an integer", stderr)
        self.assertEqual(os.listdir(self.game), [])


# Synthetic stand-in for a registered key: the shape the launcher writes (23
# characters), never a real key.
FAKE_KEY = "TEST0-FAKE0-KEY00-00000"


def sunrise_config(key: str = FAKE_KEY) -> bytes:
    """A text GBX shaped like the one the Sunrise eXtreme launcher writes.

    Chunk 0x0B005012 is ["", "", key, False, 0]: 16 bytes long when the key is
    empty (before registration) and 39 with a 23-character key. A nested node
    and chunks on both sides make the walk follow the lengths."""
    chunk = system_config._chunk
    tokens = ["b005000", "0", "1", "0"]
    tokens += chunk("b005008", ["en"])
    tokens += chunk("b005009", ["", "", "", "", "", ""])
    tokens += chunk("b005012", ["", "", key, "False", "0"])
    display = chunk("b013001", ["800", "600", "0", "1", "0", "0", "False", "True"])
    tokens += chunk("b005020", ["False"] + display + ["facade01"])
    tokens += chunk("b005031", ["False", "", "", "2350", "0", "False", "", "3", "False"])
    tokens += ["facade01", ""]
    return system_config.TEXT_2006_HEADER + "\r\n".join(tokens).encode("ascii")


class InstallKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / NAME

    def present(self, data: bytes) -> bool:
        self.path.write_bytes(data)
        result = system_config.install_key_present(self.path)
        self.assertIs(type(result), bool)
        return result

    def test_fixture_matches_the_launcher_files(self):
        tokens = sunrise_config("").split(b"\r\n")
        index = tokens.index(b"b005012")
        self.assertEqual(tokens[index + 2], b"16")
        tokens = sunrise_config().split(b"\r\n")
        self.assertEqual(tokens[index + 2], b"39")

    def test_a_registered_key_is_present(self):
        self.assertTrue(self.present(sunrise_config()))

    def test_an_empty_key_token_is_not_a_key(self):
        self.assertFalse(self.present(sunrise_config("")))
        self.assertFalse(self.present(sunrise_config(" ")))

    def test_the_key_chunk_is_found_only_by_walking_the_lengths(self):
        data = sunrise_config("")
        # A value elsewhere that looks like the key chunk id does not count.
        self.assertFalse(self.present(data.replace(b"\r\nen\r\n", b"\r\nb005012\r\n")))
        # Without the key chunk there is no key.
        self.assertFalse(self.present(sunrise_config().replace(b"b005012", b"b005013")))

    def test_malformed_files_have_no_key(self):
        good = sunrise_config()
        for data in (
            b"",
            good.replace(b"TUUE", b"BUCE"),
            good.replace(b"b005000", b"b005001", 1),
            good.replace(b"\r\n39\r\n", b"\r\n38\r\n"),
            good.replace(b"\r\n39\r\n", b"\r\n4x\r\n"),
            good.replace(b"1397442896\r\n39", b"1397442895\r\n39"),
            good[: good.index(FAKE_KEY.encode())],
        ):
            with self.subTest(data=data[:40]):
                self.assertFalse(self.present(data))

    def test_only_a_small_regular_file_is_read(self):
        self.assertFalse(system_config.install_key_present(self.path))
        real = self.path.with_name("real.Gbx")
        real.write_bytes(sunrise_config())
        self.path.symlink_to(real)
        self.assertFalse(system_config.install_key_present(self.path))
        self.path.unlink()
        self.path.mkdir()
        self.assertFalse(system_config.install_key_present(self.path))
        self.path.rmdir()
        padded = sunrise_config().replace(b"\r\nen\r\n", b"\r\n" + b"e" * 70000 + b"\r\n")
        self.assertFalse(self.present(padded))


if __name__ == "__main__":
    unittest.main()
