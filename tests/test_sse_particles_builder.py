"""trackmania_mac.build.sse_particles_builder --check, with the pinned and other assemblers."""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from trackmania_mac.build import sse_particles_builder as builder  # noqa: E402
from trackmania_mac.install import exe_patch  # noqa: E402

COMMITTED = exe_patch.load(builder.PATCH).data
START = builder.IMAGE_BASE + builder.SECTION_RVA


@unittest.skipUnless(
    shutil.which(builder.ASSEMBLER) and shutil.which(builder.OBJDUMP),
    "needs the i686 MinGW binutils",
)
class CheckTests(unittest.TestCase):
    def check(self, assembler, assembled=None):
        out = io.StringIO()
        with (
            mock.patch.object(builder, "compiler_version", return_value=None),
            mock.patch.object(builder, "assembler_version", return_value=assembler),
            contextlib.redirect_stdout(out),
        ):
            if assembled is None:
                return builder.main(["--check"]), out.getvalue()
            with mock.patch.object(builder, "assemble", return_value=assembled):
                return builder.main(["--check"]), out.getvalue()

    def test_the_pinned_assembler_reproduces_the_committed_code(self):
        status, out = self.check(builder.ASSEMBLER_VERSION)
        self.assertEqual(status, 0)
        self.assertIn("matches billboards.s and entry.s (2080 bytes of code)", out)
        with self.assertRaisesRegex(SystemExit, "does not match its source"):
            self.check(builder.ASSEMBLER_VERSION, assembled=COMMITTED[:-1] + b"\xcc")

    def test_another_assembler_checks_the_committed_code_without_comparing_bytes(self):
        status, out = self.check("2.41", assembled=b"encoded differently")
        self.assertEqual(status, 0)
        self.assertIn(
            "the byte comparison was skipped because i686-w64-mingw32-as is binutils 2.41, "
            "not the pinned 2.47",
            out,
        )

    def test_the_committed_code_is_checked_whatever_the_assembler(self):
        broken = COMMITTED[:0x40] + b"\xd9\x05\x00\x90\xce\x00" + COMMITTED[0x46:]
        with (
            mock.patch.object(exe_patch, "load", return_value=mock.Mock(data=broken)),
            mock.patch.object(builder, "patch_document", return_value=builder.PATCH.read_text()),
            self.assertRaisesRegex(SystemExit, "x87 instruction"),
        ):
            self.check("2.41")

    def test_code_policy(self):
        builder.check_code(COMMITTED)

        def at(offset, data):
            return COMMITTED[:offset] + data + COMMITTED[offset + len(data) :]

        jump = (START + 0x500000 - (START + 0x45)).to_bytes(4, "little")
        for code, message in (
            (at(0x40, b"\xd9\x05\x00\x90\xce\x00"), "x87 instruction"),
            (at(0x40, b"\xa1\x00\x90\xce\x00"), "absolute address"),
            (at(0x40, b"\xe9" + jump), "jump out of the code"),
            (at(0x1A, b"\x90" * 5), "all of the game's three"),
            (at(0x00, b"\x90"), "no entry at offset 0x0"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(SystemExit, message):
                builder.check_code(code)


if __name__ == "__main__":
    unittest.main()
