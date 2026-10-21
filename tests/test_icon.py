import runpy
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmnf.app import app_icon

ROOT = Path(__file__).resolve().parents[1]
RAW, RVA = 0x200, 0x1000


def executable(pe_plus=False, root_offset=0):
    """Synthetic PE with three opaque RT_ICON payloads and two icon groups."""
    data = bytearray(RAW + root_offset + 512)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3C, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    optional = 240 if pe_plus else 224
    struct.pack_into('<HH', data, 0x84, 0x8664 if pe_plus else 0x14C, 1)
    struct.pack_into('<H', data, 0x94, optional)
    struct.pack_into('<H', data, 0x98, 0x20B if pe_plus else 0x10B)
    directories = 0x98 + (112 if pe_plus else 96)
    struct.pack_into('<I', data, directories - 4, 16)
    struct.pack_into('<II', data, directories + 16, RVA + root_offset, 512)
    struct.pack_into('<8sIIII', data, 0x98 + optional,
                     b'.icons\0\0', 512 + root_offset, RVA, 512 + root_offset, RAW)
    raw = RAW + root_offset
    # Fixed type -> numeric name -> language tree; all offsets are root-relative.
    for offset, items in (
        (0, [(3, 32), (14, 72)]),
        (32, [(1, 104), (2, 128), (3, 152)]),
        (72, [(1, 176), (2, 200)]),
    ):
        struct.pack_into('<HH', data, raw + offset + 12, 0, len(items))
        for index, (ident, target) in enumerate(items):
            struct.pack_into('<II', data, raw + offset + 16 + index * 8,
                             ident, 0x80000000 | target)
    for directory, leaf, payload, size in (
        (104, 224, 320, 4), (128, 240, 324, 4), (152, 256, 328, 4),
        (176, 272, 384, 20), (200, 288, 416, 48),
    ):
        struct.pack_into('<HHII', data, raw + directory + 12, 0, 1, 1033, leaf)
        struct.pack_into('<IIII', data, raw + leaf, RVA + root_offset + payload, size, 0, 0)
    data[raw + 320:raw + 332] = b'low!deepwide'
    for offset, images in ((384, [(0, 32, 3)]), (416, [(16, 8, 1), (16, 32, 2), (0, 32, 3)])):
        struct.pack_into('<HHH', data, raw + offset, 0, 1, len(images))
        for index, (side, bits, ident) in enumerate(images):
            struct.pack_into('<BBBBHHIH', data, raw + offset + 6 + index * 14,
                             side, side, 0, 0, 1, bits, 4, ident)
    return data


class IconResources(unittest.TestCase):
    def test_best_images_uses_richest_group_and_deepest_per_size(self):
        for pe_plus, offset in ((False, 0), (True, 0), (False, 16)):
            with self.subTest(pe_plus=pe_plus, root_offset=offset):
                icons = app_icon.best_images(executable(pe_plus, offset))
                self.assertEqual(set(icons), {16, 256})
                for side, payload in ((16, b'deep'), (256, b'wide')):
                    ico = icons[side]
                    self.assertEqual(struct.unpack_from('<HHH', ico), (0, 1, 1))
                    self.assertEqual(struct.unpack_from('<BBBBHHII', ico, 6),
                                     (side % 256, side % 256, 0, 0, 1, 32, 4, 22))
                    self.assertEqual(ico[22:], payload)

    def test_truncated_files_are_rejected(self):
        data = executable()
        for length in range(len(data)):
            with self.subTest(length=length), self.assertRaises(ValueError):
                app_icon.best_images(data[:length])

    def test_malformed_headers_and_resources_are_rejected(self):
        for name, offset, fmt, value in (
            ('DOS signature', 0, '<H', 0),
            ('PE offset', 0x3C, '<I', 0xFFFFFFFF),
            ('PE signature', 0x80, '<I', 0),
            ('optional header size', 0x94, '<H', 1),
            ('optional magic', 0x98, '<H', 0),
            ('directory count', 0xF4, '<I', 2),
            ('directory table extent', 0xF4, '<I', 17),
            ('resource RVA', 0x108, '<I', 0xFFFFFFFF),
            ('resource extent', 0x10C, '<I', 513),
            ('section count', 0x86, '<H', 0xFFFF),
            ('section raw offset', 0x18C, '<I', 0xFFFFFFFF),
            ('directory extent', RAW + 14, '<H', 0xFFFF),
            ('type not directory', RAW + 20, '<I', 32),
            ('type offset', RAW + 20, '<I', 0xFFFFFFFF),
            ('name not directory', RAW + 52, '<I', 104),
            ('name offset', RAW + 52, '<I', 0xFFFFFFFF),
            ('leaf is directory', RAW + 124, '<I', 0x80000000),
            ('leaf offset', RAW + 124, '<I', 505),
            ('payload before root', RAW + 224, '<I', RVA - 1),
            ('payload after root', RAW + 224, '<I', RVA + 513),
            ('payload crosses resource end', RAW + 224, '<I', RVA + 510),
            ('payload size', RAW + 228, '<I', 0xFFFFFFFF),
            ('short group header', RAW + 276, '<I', 5),
            ('group type', RAW + 386, '<H', 2),
            ('group count', RAW + 388, '<H', 0xFFFF),
            ('group image size', RAW + 398, '<I', 5),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                data = executable()
                struct.pack_into(fmt, data, offset, value)
                app_icon.best_images(data)

    def test_missing_and_nonsquare_images_are_skipped(self):
        data = executable()
        # Remove the small group; make the rich group's 16px entries unusable.
        struct.pack_into('<H', data, RAW + 388, 0)
        struct.pack_into('<H', data, RAW + 434, 99)
        data[RAW + 437] = 32
        self.assertEqual(set(app_icon.best_images(data)), {256})

    def test_cli_failure_allows_callers_generic_icon_fallback(self):
        no_icons = executable()
        struct.pack_into('<I', no_icons, RAW + 16, 1)
        struct.pack_into('<I', no_icons, RAW + 24, 2)
        with tempfile.TemporaryDirectory() as work:
            exe, output = Path(work) / 'bad.exe', Path(work) / 'App.icns'
            for data, message in ((b'not a PE', 'could not build icon'), (no_icons, 'no icons found')):
                with self.subTest(message=message):
                    exe.write_bytes(data)
                    result = subprocess.run([sys.executable, str(ROOT / 'src/python/tmnf/app/app_icon.py'), str(exe), str(output)],
                                            capture_output=True, text=True, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(output.exists())

    def test_unsupported_image_conversion_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as work:
            exe, output = Path(work) / 'game.exe', Path(work) / 'App.icns'
            exe.write_bytes(executable())
            with patch.object(sys, 'argv', ['app_icon.py', str(exe), str(output)]), \
                    patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'sips')), \
                    self.assertRaisesRegex(SystemExit, 'could not build icon'):
                runpy.run_path(str(ROOT / 'src/python/tmnf/app/app_icon.py'), run_name='__main__')
            self.assertFalse(output.exists())

    def test_converts_once_and_resizes_once_per_missing_size(self):
        with tempfile.TemporaryDirectory(prefix="icon with 'quotes ") as work:
            exe, output = Path(work) / 'game.exe', Path(work) / 'App.icns'
            exe.write_bytes(executable())

            def convert(*command):
                if command[0] == 'sips':
                    Path(command[-1]).write_bytes(Path(command[-3]).read_bytes())
                else:
                    self.assertEqual(command[0], 'iconutil')
                    iconset = Path(command[3])
                    self.assertEqual({path.name for path in iconset.iterdir()},
                                     {name for name, _ in app_icon.ICONSET})
                    for name, side in app_icon.ICONSET:
                        payload = b'deep' if side == 16 else b'wide'
                        self.assertEqual((iconset / name).read_bytes()[22:], payload)
                    Path(command[-1]).write_bytes(b'icns')

            with patch.object(app_icon, 'run', side_effect=convert) as run:
                app_icon.main(exe, output)
            commands = [call.args for call in run.call_args_list]
            self.assertEqual(sum(cmd[:2] == ('sips', '-s') for cmd in commands), 2)
            self.assertCountEqual([int(cmd[2]) for cmd in commands if cmd[:2] == ('sips', '-z')],
                                  [32, 64, 128, 512])
            self.assertEqual(len(commands), 7)
            self.assertEqual(output.read_bytes(), b'icns')


if __name__ == '__main__':
    unittest.main()
