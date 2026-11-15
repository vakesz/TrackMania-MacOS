import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/python"))

from trackmania_mac.install import overlay  # noqa: E402

EXE = "Game.exe"


def _info(name, kind=tarfile.REGTYPE, data=b"", mode=0o644, link=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    info.linkname = link
    return info, (io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def file_entry(name, data=b"x", mode=0o644):
    return _info(name, data=data, mode=mode)


def dir_entry(name, mode=0o755):
    return _info(name, kind=tarfile.DIRTYPE, mode=mode)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-overlay-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.game = self.root / "Program Files" / "Game"
        self.game.mkdir(parents=True)

    def archive(self, entries, name="overlay.tar.gz"):
        path = self.root / name
        with tarfile.open(path, "w:gz") as tar:
            for info, data in entries:
                tar.addfile(info, data)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def standard(self):
        return [
            file_entry(EXE, b"new exe"),
            dir_entry("GameData"),
            file_entry("GameData/Track.Gbx", b"track"),
            file_entry("Resource/Media/a.dds", b"texture"),
        ]

    def apply(self, entries, remove=(), last=EXE, sha=None):
        path, digest = self.archive(entries)
        overlay.apply(str(path), sha or digest, str(self.game), last, remove)

    def staging_left(self):
        return [p.name for p in self.game.parent.iterdir() if p.name.startswith(".trackmania")]

    def test_merges_over_an_existing_game_folder(self):
        (self.game / EXE).write_bytes(b"old exe")
        (self.game / "GameData").mkdir()
        (self.game / "GameData" / "Track.Gbx").write_bytes(b"")
        (self.game / "GameData" / "Keep.Gbx").write_bytes(b"keep")
        (self.game / "tmn.dat").write_bytes(b"loader")
        self.apply(self.standard(), remove=["tmn.dat", "missing.dll"])
        self.assertEqual((self.game / EXE).read_bytes(), b"new exe")
        self.assertEqual((self.game / "GameData" / "Track.Gbx").read_bytes(), b"track")
        self.assertEqual((self.game / "GameData" / "Keep.Gbx").read_bytes(), b"keep")
        self.assertEqual((self.game / "Resource/Media/a.dds").read_bytes(), b"texture")
        self.assertFalse((self.game / "tmn.dat").exists())
        self.assertEqual((self.game / EXE).stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.staging_left(), [])

    def test_replaces_a_symlinked_file_without_following_it(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside")
        (self.game / EXE).symlink_to(outside)
        self.apply(self.standard())
        self.assertFalse((self.game / EXE).is_symlink())
        self.assertEqual((self.game / EXE).read_bytes(), b"new exe")
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_refuses_unsafe_archive_entries(self):
        cases = {
            "symlink": [_info("link", kind=tarfile.SYMTYPE, link="/etc/passwd")],
            "hardlink": [file_entry("a"), _info("b", kind=tarfile.LNKTYPE, link="a")],
            "fifo": [_info("pipe", kind=tarfile.FIFOTYPE)],
            "device": [_info("dev", kind=tarfile.CHRTYPE)],
            "absolute": [file_entry("/abs.txt")],
            "parent": [file_entry("../escape.txt")],
            "nested parent": [file_entry("GameData/../../escape.txt")],
            "backslash": [file_entry("a\\b.txt")],
            "dot": [file_entry("./a.txt")],
            "control": [file_entry("a\nb.txt")],
            "duplicate": [file_entry("a.txt"), file_entry("a.txt")],
            "case collision": [file_entry("GameData/a.txt"), file_entry("gamedata/b.txt")],
            "through a file": [file_entry("a"), file_entry("a/b")],
            "file before its folder entry": [file_entry("a/b"), file_entry("a")],
            "setuid": [file_entry("a", mode=0o4755)],
        }
        for label, entries in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                self.apply(self.standard() + entries)
            self.assertFalse((self.game / EXE).exists(), label)
            self.assertEqual(self.staging_left(), [], label)
        self.assertEqual(list(self.game.iterdir()), [])
        self.assertFalse((self.root / "escape.txt").exists())

    def test_refuses_python_files_in_any_case(self):
        for name in (
            "trackmania_mac/session/supervisor.py",
            "json.py",
            "Lib/x.PYC",
            "a.Pyo",
            "site.pth",
            "deep/x.PTH",
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Python files"):
                self.apply(self.standard() + [file_entry(name)])
        self.assertEqual(list(self.game.iterdir()), [])
        self.apply(self.standard() + [file_entry("Scripts/readme.python.txt")])

    def test_refuses_an_archive_that_unpacks_too_large(self):
        def bomb():
            return [file_entry(EXE, b"\0" * (64 * 1024))]

        with self.assertRaisesRegex(ValueError, "times its own size"):
            self.apply(bomb())
        with mock.patch.object(overlay, "MAX_UNPACK_RATIO", 1_000_000):
            self.apply(bomb())
        self.assertEqual(overlay.MAX_UNPACK_RATIO, 16)

    def test_refuses_an_archive_with_too_many_entries(self):
        self.assertEqual(overlay.MAX_ENTRIES, 1_000_000)
        with (
            mock.patch.object(overlay, "MAX_ENTRIES", 3),
            self.assertRaisesRegex(ValueError, "more than 3 entries"),
        ):
            self.apply(self.standard())
        with mock.patch.object(overlay, "MAX_ENTRIES", 4):
            self.apply(self.standard())

    def test_obsolete_files_are_removed_only_after_the_last_file_is_in(self):
        (self.game / EXE).write_bytes(b"old exe")
        (self.game / "tmn.dat").write_bytes(b"loader")
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == EXE:
                raise OSError("disk full")
            real_replace(source, target)

        with mock.patch.object(overlay.os, "replace", replace), self.assertRaises(OSError):
            self.apply(self.standard(), remove=["tmn.dat"])
        self.assertTrue((self.game / "tmn.dat").exists())
        self.assertEqual((self.game / EXE).read_bytes(), b"old exe")
        self.apply(self.standard(), remove=["tmn.dat"])
        self.assertFalse((self.game / "tmn.dat").exists())
        self.assertEqual((self.game / EXE).read_bytes(), b"new exe")

    def test_refuses_a_digest_mismatch(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.apply(self.standard(), sha="0" * 64)
        with self.assertRaisesRegex(ValueError, "Invalid overlay SHA-256"):
            self.apply(self.standard(), sha="A" * 64)
        self.assertEqual(list(self.game.iterdir()), [])

    def test_refuses_to_merge_through_a_symlinked_folder(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.game / "GameData").symlink_to(elsewhere)
        with self.assertRaisesRegex(ValueError, "Not a plain folder"):
            self.apply(self.standard())
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertFalse((self.game / EXE).exists())

    def test_refuses_a_folder_where_a_file_goes_and_the_reverse(self):
        (self.game / EXE).mkdir()
        with self.assertRaisesRegex(ValueError, "in the way"):
            self.apply(self.standard())
        (self.game / EXE).rmdir()
        (self.game / "GameData").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "Not a plain folder"):
            self.apply(self.standard())

    def test_refuses_a_symlinked_game_folder(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real)
        path, digest = self.archive(self.standard())
        with self.assertRaisesRegex(ValueError, "not a plain folder"):
            overlay.apply(str(path), digest, str(link), EXE)

    def test_the_last_file_must_be_in_the_archive_and_moves_last(self):
        with self.assertRaisesRegex(ValueError, "does not contain Other.exe"):
            self.apply(self.standard(), last="Other.exe")
        with self.assertRaisesRegex(ValueError, "does not contain GameData"):
            self.apply(self.standard(), last="GameData")
        (self.game / EXE).write_bytes(b"old exe")
        moved = []
        real_replace = os.replace

        def replace(source, target):
            moved.append(Path(target).name)
            if len(moved) == 2:
                raise OSError("disk full")
            real_replace(source, target)

        with mock.patch.object(overlay.os, "replace", replace), self.assertRaises(OSError):
            self.apply(self.standard())
        self.assertEqual((self.game / EXE).read_bytes(), b"old exe")
        self.assertNotIn(EXE, moved)
        self.assertEqual(self.staging_left(), [])
        moved.clear()
        with mock.patch.object(overlay.os, "replace", lambda s, t: moved.append(Path(t).name)):
            self.apply(list(reversed(self.standard())))
        self.assertEqual(moved[-1], EXE)

    def test_remove_names_are_checked(self):
        (self.game / "old").mkdir()
        with self.assertRaisesRegex(ValueError, "Refusing to remove a folder"):
            self.apply(self.standard(), remove=["old"])
        # Checked before anything moves, though removal itself comes last.
        self.assertFalse((self.game / EXE).exists())
        with self.assertRaisesRegex(ValueError, "contains GAME.EXE"):
            self.apply(self.standard(), remove=["GAME.EXE"])
        for name in ("a/b", "..", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.apply(self.standard(), remove=[name])
        self.assertTrue((self.game / "old").is_dir())

    def test_sweeps_a_staging_folder_a_killed_run_left(self):
        stale = self.game.parent / (overlay.STAGING_PREFIX + "abc123")
        (stale / "GameData").mkdir(parents=True)
        other = self.game.parent / ".trackmania-mac-other"
        other.mkdir()
        self.apply(self.standard())
        self.assertFalse(stale.exists())
        self.assertTrue(other.is_dir())

    def test_command_line_reports_errors(self):
        path, digest = self.archive(self.standard())
        command = [
            sys.executable,
            "-m",
            "trackmania_mac.install.overlay",
            "apply",
            str(path),
            digest,
            str(self.game),
            "--last",
            EXE,
            "--remove",
            "tmn.dat",
        ]
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src/python")}
        ok = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual((self.game / EXE).read_bytes(), b"new exe")
        bad = subprocess.run(
            command[:5] + ["0" * 64] + command[6:],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("error: Overlay archive SHA-256 mismatch", bad.stderr)


if __name__ == "__main__":
    unittest.main()
