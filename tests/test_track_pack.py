"""Track packs: community tracks unpacked into the saves folder a game folder links to.

Every archive here is a small synthetic zip with made-up names, and every
fixture lives in a temporary folder under /private/tmp; nothing here touches a
real prefix, saves folder or the pinned download.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from trackmania_mac import markers
from trackmania_mac.hub import registry
from trackmania_mac.install import game_saves, track_pack


ROOT = Path(__file__).resolve().parents[1]
TMSX = registry.get("tm-sunrise-extreme", ROOT / "config/titles")
INTO = "GameData/Tracks/Challenges/Downloaded"


class Cp437Info(zipfile.ZipInfo):
    """An entry whose name is stored as cp437, without the UTF-8 flag."""

    __slots__ = ()

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp437"), self.flag_bits & ~0x800


def encrypted(raw):
    """RAW with the encryption flag set on every central directory entry."""
    data = bytearray(raw)
    start = data.find(b"PK\x01\x02")
    while start != -1:
        data[start + 8] |= 0x1
        start = data.find(b"PK\x01\x02", start + 4)
    return bytes(data)


def entry(name, data=b"track", info_class=zipfile.ZipInfo, **info):
    """(ZipInfo for NAME with INFO set, DATA)."""
    zinfo = info_class(name)
    zinfo.compress_type = zipfile.ZIP_DEFLATED
    for key, value in info.items():
        setattr(zinfo, key, value)
    return zinfo, data


class TrackPackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="trackmania-mac-pack-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.game = self.base / "prefix/drive_c/Program Files (x86)" / TMSX.game_dir_name
        self.saves = self.base / "saves"
        self.game.mkdir(parents=True)
        self.saves.mkdir()
        self.dest = self.saves / TMSX.user_subdir / INTO
        self.archive = self.base / "pack.zip"
        self.title = TMSX._replace(game_save_dirs=("GameData/Profiles", INTO))

    def pack(self, entries, *, raw=None, encrypt=False):
        """Write ENTRIES as the archive and pin the title's pack to it."""
        if raw is None:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for zinfo, data in entries:
                    archive.writestr(zinfo, data)
            raw = buffer.getvalue()
        if encrypt:
            raw = encrypted(raw)
        self.archive.write_bytes(raw)
        pinned = TMSX.track_pack._replace(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
        self.title = self.title._replace(track_pack=pinned)

    def link(self):
        warnings = game_saves.run(
            "link", str(self.game), str(self.saves), self.title, report=lambda line: None
        )
        self.assertEqual(warnings, [])

    def apply(self):
        return track_pack.apply(str(self.archive), str(self.game), str(self.saves), self.title)

    def files(self):
        return sorted(
            str(path.relative_to(self.dest))
            for path in self.dest.rglob("*")
            if path.is_file() and path.name != markers.TRACK_PACK
        )

    def test_unpacks_only_tracks_under_the_root_into_the_saves_folder(self):
        cp437 = entry("Downloaded/RACE/Café $o$s.Challenge.Gbx", b"cafe", Cp437Info)
        self.pack(
            [
                entry("Downloaded/"),
                entry("Downloaded/RACE/Island/TOP500/", b""),
                entry("Downloaded/RACE/Island/TOP500/It's $fff fast.Challenge.Gbx", b"a"),
                entry("Downloaded/PLATFORM/b.challenge.gbx", b"bb"),
                cp437,
                entry("Downloaded/RACE/page.aspx"),
                entry("Downloaded/intro.bik"),
                entry("Downloaded/-_'~'_"),
                entry("Downloaded/.hidden.Challenge.Gbx"),
                entry("www.trackmania-design.de/c.Challenge.Gbx"),
                entry("other.Challenge.Gbx"),
            ]
        )
        self.link()
        result = self.apply()
        self.assertEqual(result, track_pack.Result(3, 0, 6))
        self.assertEqual(
            self.files(),
            [
                "PLATFORM/b.challenge.gbx",
                "RACE/Café $o$s.Challenge.Gbx",
                "RACE/Island/TOP500/It's $fff fast.Challenge.Gbx",
            ],
        )
        self.assertEqual((self.dest / "PLATFORM/b.challenge.gbx").read_bytes(), b"bb")
        self.assertEqual(
            stat.S_IMODE(os.stat(self.dest / "PLATFORM/b.challenge.gbx").st_mode), 0o644
        )
        # Reached through the game folder's link, and nothing else in the prefix.
        self.assertTrue((self.game / INTO).is_symlink())
        self.assertTrue((self.game / INTO / "PLATFORM/b.challenge.gbx").is_file())
        self.assertEqual(
            (self.dest / markers.TRACK_PACK).read_text(), self.title.track_pack.sha256 + "\n"
        )
        self.assertTrue(track_pack.installed(str(self.saves), self.title))

    def test_never_overwrites_and_skips_names_in_any_case(self):
        self.pack(
            [
                entry("Downloaded/a.Challenge.Gbx", b"new"),
                entry("Downloaded/Sub/B.Challenge.Gbx", b"new"),
                entry("Downloaded/c.Challenge.Gbx", b"new"),
            ]
        )
        self.link()
        (self.dest / "sub").mkdir()
        (self.dest / "a.Challenge.Gbx").write_bytes(b"mine")
        (self.dest / "sub/b.CHALLENGE.gbx").write_bytes(b"mine")
        self.assertEqual(self.apply(), track_pack.Result(1, 2, 0))
        self.assertEqual((self.dest / "a.Challenge.Gbx").read_bytes(), b"mine")
        self.assertEqual((self.dest / "sub/b.CHALLENGE.gbx").read_bytes(), b"mine")
        self.assertEqual((self.dest / "c.Challenge.Gbx").read_bytes(), b"new")
        # The existing folder is reused as it is spelled.
        self.assertEqual(sorted(os.listdir(self.dest / "sub")), ["b.CHALLENGE.gbx"])
        self.assertIn("sub", os.listdir(self.dest))
        self.assertNotIn("Sub", os.listdir(self.dest))

    def test_a_marked_pack_is_not_added_again(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx"), entry("Downloaded/b.Challenge.Gbx")])
        self.link()
        self.assertEqual(self.apply(), track_pack.Result(2, 0, 0))
        (self.dest / "a.Challenge.Gbx").unlink()
        self.assertIsNone(self.apply())
        self.assertEqual(self.files(), ["b.Challenge.Gbx"])

    def test_another_pack_hash_installs_again(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        self.link()
        (self.dest / markers.TRACK_PACK).write_text("0" * 64 + "\n")
        self.assertFalse(track_pack.installed(str(self.saves), self.title))
        self.assertEqual(self.apply(), track_pack.Result(1, 0, 0))
        self.assertTrue(track_pack.installed(str(self.saves), self.title))

    def test_a_stopped_run_is_finished_and_its_temporary_files_removed(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx"), entry("Downloaded/b.Challenge.Gbx")])
        self.link()
        stale = self.dest / (track_pack.TEMP_PREFIX + "0123abcd")
        stale.write_bytes(b"half")
        (self.dest / "a.Challenge.Gbx").write_bytes(b"track")
        self.assertEqual(self.apply(), track_pack.Result(1, 1, 0))
        self.assertFalse(stale.exists())

    def test_installed_is_false_without_a_saves_folder(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        self.assertFalse(track_pack.installed(str(self.saves), self.title))
        self.assertFalse(track_pack.installed(str(self.base / "missing"), self.title))

    def assertRefused(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.apply()
        self.assertEqual(self.files() if self.dest.is_dir() else [], [])
        self.assertFalse((self.dest / markers.TRACK_PACK).exists())

    def test_refuses_an_archive_that_does_not_match_its_pin(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        self.link()
        pinned = self.title.track_pack
        self.title = self.title._replace(track_pack=pinned._replace(sha256="0" * 64))
        self.assertRefused("SHA-256 mismatch")
        self.title = self.title._replace(track_pack=pinned._replace(size=pinned.size + 1))
        self.assertRefused("wrong size")

    def test_refuses_unsafe_entries_before_writing_anything(self):
        for entries, pattern in (
            (
                [entry("Downloaded/a.Challenge.Gbx", compress_type=zipfile.ZIP_BZIP2)],
                "Unsupported track pack compression",
            ),
            (
                [
                    entry(
                        "Downloaded/a.Challenge.Gbx",
                        b"/etc",
                        create_system=3,
                        external_attr=(stat.S_IFLNK | 0o777) << 16,
                    )
                ],
                "must be files or folders",
            ),
            ([entry("Downloaded/../a.Challenge.Gbx")], "Unsafe track pack path"),
            ([entry("/Downloaded/a.Challenge.Gbx")], "Unsafe track pack path"),
            ([entry("Downloaded\\a.Challenge.Gbx")], "Unsafe track pack path"),
        ):
            with self.subTest(pattern=pattern, name=entries[0][0].filename):
                self.pack([entry("Downloaded/first.Challenge.Gbx")] + entries)
                if not (self.game / INTO).is_symlink():
                    self.link()
                self.assertRefused(pattern)

    def test_refuses_an_encrypted_entry(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")], encrypt=True)
        self.link()
        self.assertRefused("must not be encrypted")

    def test_refuses_a_tar_and_an_oversized_pack(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("Downloaded/a.Challenge.Gbx")
            archive.addfile(info, io.BytesIO(b""))
        self.pack([], raw=buffer.getvalue())
        self.link()
        self.assertRefused("not a zip")
        self.pack([entry("Downloaded/a.Challenge.Gbx", b"x" * 4096)])
        with mock.patch.object(track_pack, "MAX_UNPACK_RATIO", 1):
            self.assertRefused("more than 1 times")

    def test_writes_only_through_the_save_link(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        # Not linked yet: a real folder in the game folder.
        (self.game / INTO).mkdir(parents=True)
        self.assertRefused("not linked")
        (self.game / INTO).rmdir()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.game / INTO).symlink_to(elsewhere)
        self.assertRefused("not linked")
        self.assertEqual(os.listdir(elsewhere), [])

    def apply_reporting(self):
        lines = []
        result = track_pack.apply(
            str(self.archive), str(self.game), str(self.saves), self.title, lines.append
        )
        return result, lines

    def assertBlocked(self, blocked, reason):
        """The pack finished: good.Challenge.Gbx is in, BLOCKED entries were skipped."""
        result, lines = self.apply_reporting()
        self.assertEqual(result.blocked, len(blocked), lines)
        self.assertEqual(result.added, 1, lines)
        self.assertEqual([line.split(": ", 1)[0] for line in lines], blocked)
        for line in lines:
            self.assertIn(reason, line)
        self.assertTrue((self.dest / "good.Challenge.Gbx").is_file())
        self.assertTrue(track_pack.installed(str(self.saves), self.title))

    def test_skips_a_track_behind_a_symlinked_folder(self):
        self.pack(
            [entry("Downloaded/RACE/a.Challenge.Gbx"), entry("Downloaded/good.Challenge.Gbx")]
        )
        self.link()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.dest / "RACE").symlink_to(elsewhere)
        self.assertBlocked(["RACE/a.Challenge.Gbx"], "not a plain folder")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_skips_a_track_behind_a_file_or_dangling_link_where_a_folder_goes(self):
        self.pack(
            [
                entry("Downloaded/RACE/a.Challenge.Gbx"),
                entry("Downloaded/STUNTS/b.Challenge.Gbx"),
                entry("Downloaded/good.Challenge.Gbx"),
            ]
        )
        self.link()
        (self.dest / "RACE").write_bytes(b"mine")
        (self.dest / "STUNTS").symlink_to(self.base / "nowhere")
        self.assertBlocked(["RACE/a.Challenge.Gbx", "STUNTS/b.Challenge.Gbx"], "not a plain folder")
        self.assertEqual((self.dest / "RACE").read_bytes(), b"mine")
        self.assertFalse((self.base / "nowhere").exists())

    def test_skips_a_track_whose_name_is_a_dangling_link_or_a_folder(self):
        self.pack(
            [
                entry("Downloaded/a.Challenge.Gbx"),
                entry("Downloaded/b.Challenge.Gbx"),
                entry("Downloaded/good.Challenge.Gbx"),
            ]
        )
        self.link()
        (self.dest / "a.Challenge.Gbx").symlink_to(self.base / "nowhere")
        (self.dest / "B.challenge.gbx").mkdir()
        self.assertBlocked(["a.Challenge.Gbx", "b.Challenge.Gbx"], "not a plain file")
        self.assertFalse((self.base / "nowhere").exists())

    def test_skips_a_file_and_a_folder_of_the_same_name_in_the_archive(self):
        self.pack(
            [
                entry("Downloaded/a.Challenge.Gbx"),
                entry("Downloaded/a.Challenge.Gbx/b.Challenge.Gbx"),
                entry("Downloaded/x.Challenge.Gbx/c.Challenge.Gbx"),
                entry("Downloaded/x.Challenge.Gbx"),
                entry("Downloaded/good.Challenge.Gbx"),
            ]
        )
        self.link()
        result, lines = self.apply_reporting()
        self.assertEqual(result, track_pack.Result(3, 0, 0, 2))
        self.assertEqual(
            [line.split(": ", 1)[0] for line in lines],
            ["a.Challenge.Gbx/b.Challenge.Gbx", "x.Challenge.Gbx"],
        )
        self.assertTrue((self.dest / "a.Challenge.Gbx").is_file())
        self.assertTrue((self.dest / "x.Challenge.Gbx/c.Challenge.Gbx").is_file())

    def test_skips_a_track_whose_folder_names_clash_in_case(self):
        self.pack(
            [entry("Downloaded/RACE/a.Challenge.Gbx"), entry("Downloaded/good.Challenge.Gbx")]
        )
        self.link()
        spelled = game_saves._spelled

        def clash(folder, name):
            if name == "RACE":
                raise game_saves.Skipped("{} holds 2 entries named RACE".format(folder))
            return spelled(folder, name)

        with mock.patch.object(game_saves, "_spelled", side_effect=clash):
            self.assertBlocked(["RACE/a.Challenge.Gbx"], "holds 2 entries named RACE")

    def test_skips_a_name_too_long_for_the_volume(self):
        long_file = "x" * 300 + ".Challenge.Gbx"
        long_folder = "y" * 300
        self.pack(
            [
                entry("Downloaded/" + long_file),
                entry("Downloaded/{}/a.Challenge.Gbx".format(long_folder)),
                entry("Downloaded/good.Challenge.Gbx"),
            ]
        )
        self.link()
        self.assertBlocked([long_file, long_folder + "/a.Challenge.Gbx"], "too long")
        self.assertEqual(
            [name for name in os.listdir(self.dest) if name.startswith(track_pack.TEMP_PREFIX)],
            [],
        )

    def test_a_symlinked_or_oversized_marker(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        self.link()
        marker = self.dest / markers.TRACK_PACK
        real = self.base / "marker"
        real.write_text(self.title.track_pack.sha256 + "\n")
        marker.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "Not a plain file"):
            track_pack.installed(str(self.saves), self.title)
        with self.assertRaisesRegex(ValueError, "Not a plain file"):
            self.apply()
        self.assertEqual(self.files(), [])
        marker.unlink()
        marker.write_text(self.title.track_pack.sha256 + " " * 200 + "x\n")
        self.assertFalse(track_pack.installed(str(self.saves), self.title))
        self.assertEqual(self.apply(), track_pack.Result(1, 0, 0))
        self.assertEqual(marker.read_text(), self.title.track_pack.sha256 + "\n")

    def test_refuses_a_symlinked_saves_folder_on_the_way(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx")])
        self.link()
        tracks = self.saves / TMSX.user_subdir / "GameData/Tracks/Challenges"
        moved = self.base / "moved"
        tracks.rename(moved)
        tracks.symlink_to(moved)
        with self.assertRaises(ValueError):
            self.apply()
        with self.assertRaises(ValueError):
            track_pack.installed(str(self.saves), self.title)

    def test_a_title_without_a_pack_is_refused(self):
        title = TMSX._replace(track_pack=None)
        with self.assertRaisesRegex(ValueError, "no track pack"):
            track_pack.installed(str(self.saves), title)

    def run_main(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(track_pack, "_current_title", return_value=self.title),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = track_pack.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_command_line(self):
        self.pack([entry("Downloaded/a.Challenge.Gbx"), entry("Downloaded/x.aspx")])
        self.link()
        self.assertEqual(self.run_main("installed", str(self.saves))[0], 1)
        code, out, _ = self.run_main("apply", str(self.archive), str(self.game), str(self.saves))
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "Added 1 tracks (0 already there, 0 that could not be written, 1 other files skipped).\n",
        )
        self.assertEqual(self.run_main("installed", str(self.saves))[0], 0)
        code, out, _ = self.run_main("apply", str(self.archive), str(self.game), str(self.saves))
        self.assertEqual((code, out), (0, "The track pack is already added.\n"))
        code, _, err = self.run_main(
            "apply", str(self.base / "none.zip"), str(self.game), str(self.saves)
        )
        self.assertEqual(code, 0)  # Marked already: the archive is not read.
        (self.dest / markers.TRACK_PACK).unlink()
        code, _, err = self.run_main(
            "apply", str(self.base / "none.zip"), str(self.game), str(self.saves)
        )
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "), err)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            track_pack.main(["apply"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
