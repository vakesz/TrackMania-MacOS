"""Tests for trackmania_mac.install.exe_patch, on a small synthetic PE file."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from trackmania_mac.hub import registry  # noqa: E402
from trackmania_mac.install import exe_patch, pe_flags  # noqa: E402

LFANEW = 0x40
OPTIONAL = LFANEW + 24
TABLE = OPTIONAL + 0xE0
HOOK_VA = 0x401010
REPLACED = bytes.fromhex("8b54247c8b")
CODE = b"\xcc" * 0x30


def make_pe(dll_characteristics=0x8000):
    """One .text section at RVA 0x1000 (file 0x200..0x400) holding the hooked bytes."""
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, LFANEW)
    data[LFANEW : LFANEW + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, LFANEW + 4, 0x14C, 1)
    struct.pack_into("<H", data, LFANEW + 20, 0xE0)
    struct.pack_into("<H", data, OPTIONAL, 0x10B)
    struct.pack_into("<III", data, OPTIONAL + 28, 0x400000, 0x1000, 0x200)
    struct.pack_into("<II", data, OPTIONAL + 56, 0x2000, 0x200)
    struct.pack_into("<H", data, OPTIONAL + 70, dll_characteristics)
    struct.pack_into(
        "<8sIIIIIIHHI", data, TABLE, b".text", 0x200, 0x1000, 0x200, 0x200, 0, 0, 0, 0, 0x60000020
    )
    data[0x200:0x400] = b"\x90" * 0x200
    data[0x210:0x215] = REPLACED
    return bytes(data)


def patch_document(**changes):
    data = base64.b64encode(CODE).decode()
    document = {
        "format": "trackmania-mac-exe-patch",
        "version": 1,
        "summary": "test",
        "source": "src/c/test",
        "section": {
            "name": ".sse",
            "rva": "0x2000",
            "data": [data[:20], data[20:]],
            "sha256": hashlib.sha256(CODE).hexdigest(),
        },
        "hooks": [{"va": f"{HOOK_VA:#x}", "replaces": REPLACED.hex(), "target": "0x10"}],
    }
    document.update(changes)
    return document


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PatchedImageTests(unittest.TestCase):
    def patch(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patch.json"
            path.write_text(json.dumps(patch_document(**changes)))
            return exe_patch.load(path)

    def test_adds_the_section_and_jumps_into_it(self):
        result = exe_patch.patched(make_pe(), self.patch())
        self.assertEqual(len(result), 0x600)
        self.assertEqual(struct.unpack_from("<H", result, LFANEW + 6)[0], 2)
        self.assertEqual(struct.unpack_from("<I", result, OPTIONAL + 56)[0], 0x3000)
        self.assertEqual(
            struct.unpack_from("<8sIIIIIIHHI", result, TABLE + 40),
            (b".sse\0\0\0\0", 0x30, 0x2000, 0x200, 0x400, 0, 0, 0, 0, 0x60000020),
        )
        self.assertEqual(result[0x400:0x430], CODE)
        self.assertEqual(result[0x430:], bytes(0x1D0))
        jump = b"\xe9" + struct.pack("<i", (0x402000 + 0x10) - (HOOK_VA + 5))
        self.assertEqual(result[0x210:0x215], jump)
        changed = [i for i in range(0x400) if result[i] != make_pe()[i]]
        self.assertEqual(changed[0], LFANEW + 6)
        self.assertTrue(all(i < 0x188 or 0x210 <= i < 0x215 for i in changed))

    def test_refuses_an_image_it_does_not_fit(self):
        image = make_pe()
        no_slot = bytearray(image)
        no_slot[TABLE + 40] = 1
        overlay = image + b"signature"
        wrong_bytes = bytearray(image)
        wrong_bytes[0x212] = 0
        not_code = bytearray(image)
        struct.pack_into("<I", not_code, TABLE + 36, 0x40000040)
        for data, message in (
            (bytes(no_slot), "no room"),
            (overlay, "follows the last"),
            (bytes(wrong_bytes), "does not hold"),
            (bytes(not_code), "not in code"),
            (b"MZ" + bytes(10), "malformed PE file"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                exe_patch.patched(data, self.patch())
        moved = patch_document()
        moved["section"]["rva"] = "0x3000"
        with self.assertRaisesRegex(ValueError, "end of the image"):
            exe_patch.patched(image, self.patch(section=moved["section"]))

    def test_refuses_malformed_headers_with_a_value_error(self):
        def changed(offset, fmt, value):
            data = bytearray(make_pe())
            struct.pack_into(fmt, data, offset, value)
            return bytes(data)

        for data, message in (
            (changed(OPTIONAL + 32, "<I", 0), "not a power of two"),
            (changed(OPTIONAL + 36, "<I", 0), "not a power of two"),
            (changed(OPTIONAL + 36, "<I", 0x300), "not a power of two"),
            # The new header would reach the first section's data, whatever SizeOfHeaders says.
            (changed(TABLE + 20, "<I", 0x170), "no room"),
            (changed(LFANEW + 6, "<H", 0x400), "malformed PE file"),
            (changed(LFANEW + 6, "<H", 0), "no sections"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                exe_patch.patched(data, self.patch())

    def test_maps_hooks_only_inside_the_section_data(self):
        short = bytearray(make_pe())
        struct.pack_into("<I", short, TABLE + 8, 0x12)  # virtual size ends inside the hook
        with self.assertRaisesRegex(ValueError, "not in code"):
            exe_patch.patched(bytes(short), self.patch())
        hook = patch_document()["hooks"][0]
        overlapping = [hook, dict(hook, va=f"{HOOK_VA + 4:#x}")]
        with self.assertRaisesRegex(ValueError, "overlap"):
            exe_patch.patched(make_pe(), self.patch(hooks=overlapping))

    def test_refuses_a_jump_out_of_rel32_range(self):
        far = bytearray(make_pe())
        struct.pack_into("<I", far, OPTIONAL + 56, 0x90000000)
        section = dict(patch_document()["section"], rva="0x90000000")
        with self.assertRaisesRegex(ValueError, "too far"):
            exe_patch.patched(bytes(far), self.patch(section=section))

    def test_refuses_a_patch_file_over_the_size_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patch.json"
            path.write_text(json.dumps(patch_document(summary="x" * exe_patch.MAX_PATCH_FILE)))
            with self.assertRaisesRegex(ValueError, "is over 1024 KiB"):
                exe_patch.load(path)

    def test_refuses_a_malformed_patch_file(self):
        good = patch_document()
        bad_sha = dict(good["section"], sha256="0" * 64)
        bad_data = dict(good["section"], data=["not base64!"])
        bad_name = dict(good["section"], name="sse")
        for changes in (
            {"extra": 1},
            {"format": "other"},
            {"version": 2},
            {"version": True},
            {"version": "1"},
            {"section": bad_sha},
            {"section": bad_data},
            {"section": bad_name},
            {"hooks": []},
            {"hooks": [dict(good["hooks"][0], target="0x30")]},
            {"hooks": [dict(good["hooks"][0], target="16")]},
            {"hooks": [dict(good["hooks"][0], replaces="8b54")]},
            {"hooks": [dict(good["hooks"][0], other=1)]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.patch(**changes)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-exe-patch-")
        self.addCleanup(temporary.cleanup)
        patches = Path(temporary.name) / "exe-patches"
        (patches / "tmn-eswc").mkdir(parents=True)
        (patches / "tmn-eswc/test.json").write_text(json.dumps(patch_document()))
        folder = mock.patch.object(registry, "exe_patches_dir", return_value=patches)
        folder.start()
        self.addCleanup(folder.stop)
        self.game = Path(temporary.name) / "game"
        self.game.mkdir()
        self.flagged = make_pe(0x8100)
        self.final = exe_patch.patched(self.flagged, exe_patch.load(patches / "tmn-eswc/test.json"))
        self.title = registry.get("tmn-eswc", ROOT / "config/titles")._replace(
            game_exe="Game.exe",
            nx_compat=("Game.exe",),
            game_exe_sha256=sha(self.final),
            game_exe_patch=registry.GameExePatch("tmn-eswc/test.json", sha(self.flagged)),
        )

    def put(self, data, mode=0o751):
        path = self.game / "Game.exe"
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def test_patches_once_and_keeps_the_permission_bits(self):
        path = self.put(self.flagged)
        stale = self.game / (pe_flags.TEMP_PREFIX + "stale")
        stale.write_bytes(b"left by a killed run")
        self.assertTrue(exe_patch.apply(str(self.game), self.title))
        self.assertEqual(path.read_bytes(), self.final)
        self.assertEqual(path.stat().st_mode & 0o777, 0o751)
        self.assertEqual(os.listdir(self.game), ["Game.exe"])
        self.assertFalse(exe_patch.apply(str(self.game), self.title))

    def test_writes_nothing_unless_both_hashes_match(self):
        path = self.put(make_pe(0x8000))
        with self.assertRaisesRegex(ValueError, "not the build the patch is for"):
            exe_patch.apply(str(self.game), self.title)
        self.assertEqual(path.read_bytes(), make_pe(0x8000))
        path = self.put(self.flagged)
        wrong = self.title._replace(game_exe_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "did not give the pinned build"):
            exe_patch.apply(str(self.game), wrong)
        self.assertEqual(path.read_bytes(), self.flagged)
        self.assertEqual(os.listdir(self.game), ["Game.exe"])

    def test_refuses_links_and_other_files(self):
        target = self.game.parent / "elsewhere.exe"
        target.write_bytes(self.flagged)
        (self.game / "Game.exe").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Cannot open"):
            exe_patch.apply(str(self.game), self.title)
        self.assertEqual(target.read_bytes(), self.flagged)
        os.unlink(self.game / "Game.exe")
        os.mkfifo(self.game / "Game.exe")
        with self.assertRaisesRegex(ValueError, "Not a regular file"):
            exe_patch.apply(str(self.game), self.title)

    def test_stage_tells_which_steps_are_left(self):
        for data, expected in (
            (self.final, "final"),
            (self.flagged, "patch"),
            (make_pe(0x8000), "nx"),
            (make_pe(0x8001), "unknown"),
        ):
            with self.subTest(expected=expected):
                self.put(data)
                self.assertEqual(exe_patch.stage(str(self.game), self.title), expected)
        self.put(make_pe(0x8000))
        unflagged = self.title._replace(nx_compat=())
        self.assertEqual(exe_patch.stage(str(self.game), unflagged), "unknown")

    def test_command_line_reads_the_title_from_the_environment(self):
        self.put(self.flagged)
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"TRACKMANIA_MAC_TITLE": "tmn-eswc"}),
            mock.patch.object(registry, "get", return_value=self.title) as get,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(exe_patch.main(["stage", str(self.game)]), 0)
            self.assertEqual(exe_patch.main(["apply", str(self.game)]), 0)
        get.assert_called_with("tmn-eswc")
        self.assertEqual(out.getvalue(), "patch\n")
        self.assertEqual((self.game / "Game.exe").read_bytes(), self.final)

    def test_command_line_reports_a_malformed_executable(self):
        broken = bytearray(self.flagged)
        struct.pack_into("<I", broken, OPTIONAL + 36, 0)
        self.put(bytes(broken))
        title = self.title._replace(
            game_exe_patch=self.title.game_exe_patch._replace(sha256_before=sha(bytes(broken)))
        )
        err = io.StringIO()
        with (
            mock.patch.object(registry, "get", return_value=title),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(exe_patch.main(["apply", str(self.game)]), 1)
        self.assertRegex(err.getvalue(), "^error: PE alignment 0x0 is not a power of two")
        self.assertEqual((self.game / "Game.exe").read_bytes(), bytes(broken))


class CommittedPatchTests(unittest.TestCase):
    def test_patch_files_never_follow_a_registry_override(self):
        with mock.patch.dict(os.environ, {"TRACKMANIA_MAC_TITLES_DIR": "/tmp/elsewhere/titles"}):
            self.assertEqual(registry.exe_patches_dir(), ROOT / "config/exe-patches")

    def test_the_eswc_patch_loads_and_jumps_to_its_entries(self):
        title = registry.get("tmn-eswc", ROOT / "config/titles")
        patch = exe_patch.load(ROOT / "config/exe-patches" / title.game_exe_patch.file)
        self.assertEqual((patch.name, patch.rva), (".sse", 0x8E8000))
        self.assertEqual(
            [(hook.va, hook.replaces.hex(), hook.target) for hook in patch.hooks],
            [(0x810685, "8b54247c8b", 0x00), (0x810D17, "8b8c249800", 0x20)],
        )


if __name__ == "__main__":
    unittest.main()
