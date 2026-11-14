"""Tests for trackmania_mac.install.pe_flags, on small synthetic PE files."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from trackmania_mac.install import pe_flags  # noqa: E402

LFANEW = 0x80
FLAGS_AT = LFANEW + 24 + 70


def make_pe(
    flags=0x8140, machine=0x014C, magic=0x10B, signature=b"PE\0\0", size=0x200, optional_size=0xE0
):
    data = bytearray(b"\xaa" * size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, LFANEW)
    data[LFANEW : LFANEW + 4] = signature
    struct.pack_into("<H", data, LFANEW + 4, machine)
    struct.pack_into("<H", data, LFANEW + 20, optional_size)
    struct.pack_into("<H", data, LFANEW + 24, magic)
    struct.pack_into("<H", data, FLAGS_AT, flags)
    return bytes(data)


class PeFlagsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-pe-")
        self.addCleanup(temporary.cleanup)
        self.game = Path(temporary.name) / "game"
        self.game.mkdir()

    def put(self, name, data, mode=0o644):
        path = self.game / name
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def flags(self, name):
        return struct.unpack_from("<H", (self.game / name).read_bytes(), FLAGS_AT)[0]

    def test_sets_the_flag_and_changes_nothing_else(self):
        original = make_pe(flags=0x8000)
        self.put("a.exe", original)
        self.assertTrue(pe_flags.set_nx_compat(str(self.game), "a.exe"))
        patched = (self.game / "a.exe").read_bytes()
        self.assertEqual(self.flags("a.exe"), 0x8100)
        self.assertEqual(len(patched), len(original))
        changed = [i for i in range(len(original)) if original[i] != patched[i]]
        self.assertEqual(changed, [FLAGS_AT + 1])
        self.assertEqual(os.listdir(self.game), ["a.exe"])

    def test_an_already_flagged_file_is_not_touched(self):
        path = self.put("a.dll", make_pe(flags=0x0140))
        before = path.stat()
        self.assertFalse(pe_flags.set_nx_compat(str(self.game), "a.dll"))
        after = path.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(os.listdir(self.game), ["a.dll"])

    def test_a_read_only_file_is_replaced_and_keeps_its_mode(self):
        self.put("a.dll", make_pe(flags=0), mode=0o444)
        pe_flags.set_nx_compat(str(self.game), "a.dll")
        self.assertEqual(self.flags("a.dll"), 0x0100)
        self.assertEqual(stat.S_IMODE((self.game / "a.dll").stat().st_mode), 0o444)

    def rejects(self, name, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            pe_flags.set_nx_compat(str(self.game), name)

    def test_refuses_files_that_are_not_pe32_x86(self):
        cases = {
            "text.dll": (b"hello" * 100, "Not a PE file"),
            "tiny.dll": (b"MZ", "Not a PE file"),
            "sig.dll": (make_pe(signature=b"NE\0\0"), "Not a PE file"),
            "plus.dll": (make_pe(magic=0x20B), "Not a PE32 image"),
            "x64.dll": (make_pe(machine=0x8664), "Not a 32-bit x86"),
        }
        for name, (data, pattern) in cases.items():
            with self.subTest(name=name):
                path = self.put(name, data)
                self.rejects(name, pattern)
                self.assertEqual(path.read_bytes(), data)
        data = bytearray(make_pe())
        struct.pack_into("<I", data, 0x3C, 0xFFFFFFF0)
        self.put("far.dll", bytes(data))
        self.rejects("far.dll", "Not a PE file")

    def test_refuses_symlinks_directories_and_missing_files(self):
        outside = Path(self.game.parent) / "outside.dll"
        outside.write_bytes(make_pe(flags=0))
        (self.game / "link.dll").symlink_to(outside)
        self.rejects("link.dll", "Cannot open")
        self.assertEqual(outside.read_bytes(), make_pe(flags=0))
        (self.game / "folder.dll").mkdir()
        self.rejects("folder.dll", "regular file|Cannot open")
        self.rejects("missing.dll", "Cannot open")

    def test_refuses_names_outside_the_game_folder(self):
        outside = Path(self.game.parent) / "outside.dll"
        outside.write_bytes(make_pe(flags=0))
        for name in ("../outside.dll", "sub/a.dll", "..", ".", "", "a\\b.dll", str(outside)):
            with self.subTest(name=name):
                self.rejects(name, "Unsafe file name")
        self.assertEqual(outside.read_bytes(), make_pe(flags=0))

    def test_refuses_a_symlinked_game_folder(self):
        link = self.game.parent / "link"
        link.symlink_to(self.game)
        self.put("a.dll", make_pe(flags=0))
        with self.assertRaisesRegex(ValueError, "Not a game folder"):
            pe_flags.set_nx_compat(str(link), "a.dll")

    def test_a_failed_replace_leaves_the_original_and_no_temporary_file(self):
        original = make_pe(flags=0)
        self.put("a.dll", original)
        with mock.patch.object(pe_flags.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                pe_flags.set_nx_compat(str(self.game), "a.dll")
        self.assertEqual((self.game / "a.dll").read_bytes(), original)
        self.assertEqual(os.listdir(self.game), ["a.dll"])

    def test_refuses_a_fifo_without_hanging(self):
        os.mkfifo(self.game / "pipe.dll")
        self.rejects("pipe.dll", "Not a regular file")

    def test_drops_setuid_and_keeps_permission_bits(self):
        self.put("a.dll", make_pe(flags=0), mode=0o4755)
        pe_flags.set_nx_compat(str(self.game), "a.dll")
        self.assertEqual(stat.S_IMODE((self.game / "a.dll").stat().st_mode), 0o755)

    def test_refuses_a_file_over_the_size_limit(self):
        path = self.put("big.dll", make_pe(flags=0))
        with open(path, "r+b") as handle:
            handle.truncate(pe_flags.MAX_SIZE + 1)
        self.rejects("big.dll", "over 64 MiB")
        self.assertEqual(path.stat().st_size, pe_flags.MAX_SIZE + 1)

    def test_refuses_a_too_small_optional_header(self):
        data = make_pe(flags=0, optional_size=71)
        path = self.put("small.dll", data)
        self.rejects("small.dll", "optional header is too small")
        self.assertEqual(path.read_bytes(), data)
        self.put("ok.dll", make_pe(flags=0, optional_size=72))
        self.assertTrue(pe_flags.set_nx_compat(str(self.game), "ok.dll"))

    def test_main_sweeps_stale_temporary_files_but_nothing_else(self):
        self.put("a.exe", make_pe(flags=0))
        stale = self.put(pe_flags.TEMP_PREFIX + "abc", b"junk")
        keep = self.put("other.tmp", b"keep")
        outside = self.game.parent / "outside"
        outside.write_bytes(b"outside")
        (self.game / (pe_flags.TEMP_PREFIX + "link")).symlink_to(outside)
        (self.game / (pe_flags.TEMP_PREFIX + "dir")).mkdir()
        self.assertEqual(self.run_main("a.exe")[0], 0)
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists())
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertTrue((self.game / (pe_flags.TEMP_PREFIX + "dir")).is_dir())

    def run_pending(self, sha, name):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pe_flags.main(["pending-nx", sha, str(self.game / name)])
        return code, out.getvalue(), err.getvalue()

    def test_pending_nx_is_a_pure_check(self):
        data = make_pe(flags=0x8000)
        flagged = make_pe(flags=0x8100)
        path = self.put("a.exe", data)
        before = path.stat()
        good = hashlib.sha256(flagged).hexdigest()
        self.assertEqual(self.run_pending(good, "a.exe"), (0, "", ""))
        self.assertEqual(self.run_pending(good.upper(), "a.exe")[0], 0)
        self.assertEqual(self.run_pending("0" * 64, "a.exe"), (1, "", ""))
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(os.listdir(self.game), ["a.exe"])

    def test_pending_nx_is_false_when_the_flag_is_already_set(self):
        flagged = make_pe(flags=0x8100)
        self.put("a.exe", flagged)
        self.assertEqual(
            self.run_pending(hashlib.sha256(flagged).hexdigest(), "a.exe"), (1, "", "")
        )

    def test_pending_nx_reports_files_that_are_not_pe(self):
        self.put("a.exe", b"nope" * 64)
        code, out, err = self.run_pending("0" * 64, "a.exe")
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"^error: Not a PE file")
        code, _, err = self.run_pending("0" * 64, "missing.exe")
        self.assertEqual(code, 1)
        self.assertRegex(err, "^error: Cannot open")

    def run_main(self, *names):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pe_flags.main(["nx-compat", str(self.game), *names])
        return code, out.getvalue(), err.getvalue()

    def test_main_patches_several_files_quietly(self):
        self.put("a.exe", make_pe(flags=0))
        self.put("b.dll", make_pe(flags=0))
        self.assertEqual(self.run_main("a.exe", "b.dll"), (0, "", ""))
        self.assertEqual((self.flags("a.exe"), self.flags("b.dll")), (0x0100, 0x0100))

    def test_main_reports_an_error_and_exits_nonzero(self):
        self.put("a.exe", make_pe(flags=0))
        self.put("bad.dll", b"nope" * 64)
        code, out, err = self.run_main("a.exe", "bad.dll", "c.dll")
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"^error: Not a PE file")
        self.assertEqual(self.flags("a.exe"), 0x0100)


if __name__ == "__main__":
    unittest.main()
