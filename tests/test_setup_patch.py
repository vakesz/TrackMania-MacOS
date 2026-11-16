"""Tests for trackmania_mac.install.setup_patch, on a small synthetic Inno Setup file."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import lzma
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from trackmania_mac.hub import registry  # noqa: E402
from trackmania_mac.install import setup_patch  # noqa: E402

SETUP_ID = b"Inno Setup Setup Data (5.1.2)".ljust(64, b"\0")
FLAG_OFFSET = 0x1234
# Longer than one 4096-byte chunk, with the [Run] flags byte at FLAG_OFFSET.
HEADER = bytearray(bytes(range(256)) * 24)
HEADER[FLAG_OFFSET] = 0x14
HEADER = bytes(HEADER)
SECOND = b"second header block " * 300
LOADER_SIZE = 0x200
DATA = b"game data " * 100
SETUP_EXE = b"setup program " * 20


def chunked(payload):
    return b"".join(
        struct.pack("<I", zlib.crc32(payload[start : start + 4096])) + payload[start : start + 4096]
        for start in range(0, len(payload), 4096)
    )


def block(payload, compressed):
    chunks = chunked(payload)
    tail = struct.pack("<IB", len(chunks), compressed)
    return struct.pack("<I", zlib.crc32(tail)) + tail + chunks


def lzma_block(plain):
    lc, lp, pb, dict_size = 3, 0, 2, 1 << 16
    lzma_filter = {"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dict_size}
    properties = bytes([(pb * 5 + lp) * 9 + lc]) + struct.pack("<I", dict_size)
    return block(properties + lzma.compress(plain, lzma.FORMAT_RAW, filters=[lzma_filter]), 1)


def offset_table(table_id, total, exe_offset, setup0, setup1):
    body = struct.pack(
        "<12s6I", table_id, total, exe_offset, len(SETUP_EXE), 0xDEADBEEF, setup0, setup1
    )
    return body + struct.pack("<I", zlib.crc32(body))


def build_setup(
    header=HEADER, table_id=setup_patch.TABLE_ID, setup_id=SETUP_ID, first=None, trailing=b""
):
    """Loader, game data, setup.0, setup program and offset table, like a 5.1 setup."""
    setup0 = setup_id + (first if first is not None else lzma_block(header)) + block(SECOND, 0)
    setup1 = LOADER_SIZE
    setup0_offset = setup1 + len(DATA)
    exe_offset = setup0_offset + len(setup0)
    table_offset = exe_offset + len(SETUP_EXE)
    loader = bytearray(LOADER_SIZE)
    loader[:2] = b"MZ"
    struct.pack_into("<4sII", loader, 0x30, b"Inno", table_offset, table_offset ^ 0xFFFFFFFF)
    total = table_offset + 40
    return (
        bytes(loader)
        + DATA
        + setup0
        + SETUP_EXE
        + offset_table(table_id, total, exe_offset, setup0_offset, setup1)
        + trailing
    )


def expected_result(source):
    """The copy the module should write, built independently from its parts."""
    table_offset = len(source) - 40
    edited = bytearray(HEADER)
    edited[FLAG_OFFSET] = 0x34
    setup0 = SETUP_ID + block(bytes(edited), 0) + block(SECOND, 0)
    new_table = table_offset + len(setup0)
    head = bytearray(source[:table_offset])
    struct.pack_into("<4sII", head, 0x30, b"Inno", new_table, new_table ^ 0xFFFFFFFF)
    exe_offset = table_offset - len(SETUP_EXE)
    return (
        bytes(head)
        + setup0
        + offset_table(setup_patch.TABLE_ID, new_table + 40, exe_offset, table_offset, LOADER_SIZE)
    )


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_patch(source, header_sha=None, before=b"\x14", offset=FLAG_OFFSET, after_sha=None):
    edit = registry.SetupEdit(offset, before, bytes([before[0] ^ 0x20]) + before[1:])
    return registry.SourcePatch(
        header_sha or sha(HEADER), (edit,), after_sha or sha(expected_result(source))
    )


class SetupPatchTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-setup-patch-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "Setup.exe"
        self.output = self.folder / "copy" / "Setup.exe"
        self.output.parent.mkdir()

    def write_source(self, data):
        self.source.write_bytes(data)
        return data

    def run_patch(self, source_bytes, patch=None, source_sha=None, output=None):
        output = output or self.output
        setup_patch.write_patched(
            self.source,
            output,
            source_sha or sha(source_bytes),
            patch or make_patch(source_bytes),
        )

    def assert_refused(self, source_bytes, message, **kwargs):
        with self.assertRaisesRegex(setup_patch.SetupPatchError, message):
            self.run_patch(source_bytes, **kwargs)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), source_bytes)

    def test_writes_the_copy_and_leaves_the_setup_unchanged(self):
        source = self.write_source(build_setup())
        self.run_patch(source)
        result = self.output.read_bytes()
        self.assertEqual(result, expected_result(source))
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])
        # The copy is a setup the module itself reads back: the new table and
        # pointer agree, and the first block now holds the edited header, stored.
        with open(self.output, "rb") as handle:
            layout = setup_patch.read_layout(handle)
        self.assertEqual(layout.setup0_offset, len(source) - 40)
        self.assertEqual(layout.first_block[8], 0)
        header = setup_patch.decompress_header(layout.first_block)
        self.assertEqual(header[FLAG_OFFSET], 0x34)
        self.assertEqual(
            header[:FLAG_OFFSET] + header[FLAG_OFFSET + 1 :],
            HEADER[:FLAG_OFFSET] + HEADER[FLAG_OFFSET + 1 :],
        )
        self.assertEqual(layout.second_block, block(SECOND, 0))

    def test_refuses_a_setup_without_the_pinned_hash(self):
        source = self.write_source(build_setup())
        self.assert_refused(source, "not the pinned setup", source_sha="0" * 64)

    def test_refuses_a_file_without_the_offset_table_pointer(self):
        source = bytearray(build_setup())
        source[0x30:0x34] = b"Unno"
        self.assert_refused(self.write_source(bytes(source)), "pointer at 0x30")
        source[0x30:0x34] = b"Inno"
        source[0x38] ^= 1
        self.assert_refused(self.write_source(bytes(source)), "pointer at 0x30")

    def test_refuses_an_unknown_offset_table_id_or_version(self):
        for table_id in (b"rDlPtS06\x87eVx", b"rDlPtS\xcd\xe6\xd7\x7b\x0b\x2a"):
            with self.subTest(table_id=table_id):
                source = self.write_source(build_setup(table_id=table_id))
                self.assert_refused(source, "Unknown Inno Setup offset table id")

    def test_refuses_a_damaged_offset_table(self):
        source = bytearray(build_setup())
        source[-1] ^= 1
        self.assert_refused(self.write_source(bytes(source)), "offset table CRC")

    def test_refuses_an_offset_table_that_is_not_last(self):
        source = self.write_source(build_setup(trailing=b"x"))
        self.assert_refused(source, "not at the end")

    def test_refuses_an_unknown_setup_data_version(self):
        setup_id = b"Inno Setup Setup Data (5.5.0)".ljust(64, b"\0")
        source = self.write_source(build_setup(setup_id=setup_id))
        self.assert_refused(source, "Unknown Inno Setup data version")

    def test_refuses_damaged_header_blocks(self):
        good = lzma_block(HEADER)
        damaged_chunk = bytearray(good)
        damaged_chunk[20] ^= 1
        damaged_header = bytearray(good)
        damaged_header[4] ^= 1
        for first, message in (
            (bytes(damaged_chunk), "chunk is damaged"),
            (bytes(damaged_header), "unknown or damaged header|runs past"),
            (block(HEADER, 0), "not laid out as in an unmodified setup"),
        ):
            with self.subTest(message=message):
                source = self.write_source(build_setup(first=first))
                self.assert_refused(source, message)

    def test_refuses_a_copy_it_wrote(self):
        self.run_patch(self.write_source(build_setup()))
        copy = self.write_source(self.output.read_bytes())
        self.output.unlink()
        self.assert_refused(copy, "not laid out as in an unmodified setup")

    def test_refuses_a_header_without_the_pinned_hash(self):
        other = bytearray(HEADER)
        other[0] ^= 1
        source = self.write_source(build_setup(header=bytes(other)))
        self.assert_refused(source, "not the one the patch is for", patch=make_patch(source))

    def test_refuses_an_edit_whose_bytes_differ(self):
        source = self.write_source(build_setup())
        for patch in (
            make_patch(source, before=b"\x15"),
            make_patch(source, offset=len(HEADER)),
        ):
            with self.subTest(patch=patch):
                self.assert_refused(source, "does not hold", patch=patch)

    def test_refuses_a_result_without_the_pinned_hash(self):
        source = self.write_source(build_setup())
        patch = make_patch(source, after_sha="0" * 64)
        self.assert_refused(source, "did not give the pinned result", patch=patch)

    def test_never_replaces_a_file(self):
        source = self.write_source(build_setup())
        self.output.write_bytes(b"kept")
        with self.assertRaisesRegex(setup_patch.SetupPatchError, "Refusing to replace"):
            self.run_patch(source)
        self.assertEqual(self.output.read_bytes(), b"kept")
        # Asked to write over the setup itself, it refuses and leaves it alone.
        with self.assertRaisesRegex(setup_patch.SetupPatchError, "Refusing to replace"):
            self.run_patch(source, output=self.source)
        self.assertEqual(self.source.read_bytes(), source)

    def test_refuses_a_symlinked_setup(self):
        source = self.write_source(build_setup())
        link = self.folder / "Link.exe"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(setup_patch.SetupPatchError, "not a regular file"):
            setup_patch.write_patched(link, self.output, sha(source), make_patch(source))
        self.assertFalse(self.output.exists())


class SetupPatchCommandTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-setup-patch-cli-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "Setup.exe"
        self.source_bytes = build_setup()
        self.source.write_bytes(self.source_bytes)
        titles = self.folder / "titles"
        titles.mkdir()
        entry = json.loads((ROOT / "config/titles/tmn-eswc.json").read_text())
        entry["source_sha256"] = sha(self.source_bytes)
        entry["source_patch"] = {
            "header_sha256": sha(HEADER),
            "edits": [{"offset": hex(FLAG_OFFSET), "before": "14", "after": "34"}],
            "sha256_after": sha(expected_result(self.source_bytes)),
        }
        (titles / "tmn-eswc.json").write_text(json.dumps(entry))
        self.environment = {
            "TRACKMANIA_MAC_TITLE": "tmn-eswc",
            "TRACKMANIA_MAC_TITLES_DIR": str(titles),
        }

    def run_main(self, *arguments):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, self.environment), contextlib.redirect_stderr(stderr):
            status = setup_patch.main(["apply", *arguments])
        return status, stderr.getvalue()

    def test_apply_writes_the_copy_from_the_registry_entry(self):
        output = self.folder / "copy.exe"
        status, stderr = self.run_main(str(self.source), str(output))
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(output.read_bytes(), expected_result(self.source_bytes))

    def test_apply_reports_a_refusal(self):
        output = self.folder / "copy.exe"
        self.source.write_bytes(self.source_bytes + b"x")
        status, stderr = self.run_main(str(self.source), str(output))
        self.assertEqual(status, 1)
        self.assertIn("error: ", stderr)
        self.assertFalse(output.exists())

    def test_apply_needs_a_title_with_a_setup_patch(self):
        self.environment["TRACKMANIA_MAC_TITLES_DIR"] = str(ROOT / "config/titles")
        self.environment["TRACKMANIA_MAC_TITLE"] = "tmnf"
        status, stderr = self.run_main(str(self.source), str(self.folder / "copy.exe"))
        self.assertEqual(status, 1)
        self.assertIn("has no setup patch", stderr)


if __name__ == "__main__":
    unittest.main()
