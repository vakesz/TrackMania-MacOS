import json
import struct
import unittest
from pathlib import Path

from tools.game_profile import generate

ROOT = Path(__file__).resolve().parents[1]
# Captured from the generator before field annotations and validation were added.
DEFAULT_PROFILE = bytes.fromhex('''
4742580600425555520050000b0000000001000000000000002050000b50494b53ac000000
000000000130010b50494b532000000000050000d0020000020000000100000000000000
0000000000000000000000000930010b50494b5310000000000000000000000000000000
000000001030010b50494b53040000000000803f1c30010b50494b530800000001000000
040000002130010b50494b5304000000000000002230010b50494b531c00000001000000
01000000000000000200000002000000010000000000000001decafa3450000b50494b53
1c0000000100000001000000000000000100000006000000000000000000000001decafa
''')


class GameProfile(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / 'config/game-settings.json').read_text())

    def test_default_is_byte_identical(self):
        self.assertEqual(generate(self.settings), DEFAULT_PROFILE)

    def test_header_chunks_lengths_fields_and_terminators(self):
        settings = dict(self.settings, width=1920, height=1080, post_processing=True,
                        water_geometry=True, shader_quality=3, anisotropy=6,
                        thread_count=8, max_opponents=12)
        data = generate(settings)
        self.assertEqual(data[:9], b'GBX\x06\0BUUR')
        self.assertEqual(struct.unpack_from('<IIII', data, 9), (0x0B005000, 0, 1, 0))
        self.assertEqual(struct.unpack_from('<I4sI', data, 25), (0x0B005020, b'PIKS', 172))
        self.assertEqual(struct.unpack_from('<I', data, 37), (0,))  # IsSafeMode.
        offset = 41
        for ident, fmt, expected in (
            (0x0B013001, '<8I', (1920, 1080, 2, 1, 0, 0, 0, 0)),
            (0x0B013009, '<4I', (0, 0, 1, 0)),
            (0x0B013010, '<f', (1.0,)),
            (0x0B01301C, '<2I', (1, 8)),
            (0x0B013021, '<I', (1,)),
            (0x0B013022, '<7I', (1, 1, 0, 2, 3, 1, 6)),
        ):
            size = struct.calcsize(fmt)
            self.assertEqual(struct.unpack_from('<I4sI', data, offset), (ident, b'PIKS', size))
            self.assertEqual(struct.unpack_from(fmt, data, offset + 12), expected)
            offset += 12 + size
        self.assertEqual(struct.unpack_from('<I', data, offset), (0xFACADE01,))
        self.assertEqual(offset + 4, 37 + 172)
        offset += 4
        self.assertEqual(struct.unpack_from('<I4sI', data, offset), (0x0B005034, b'PIKS', 28))
        self.assertEqual(struct.unpack_from('<7I', data, offset + 12), (1, 1, 0, 1, 12, 0, 0))
        self.assertEqual(data[offset + 40:], struct.pack('<I', 0xFACADE01))

    def test_required_fields_and_primitive_types(self):
        for name in self.settings:
            with self.subTest(missing=name), self.assertRaisesRegex(ValueError, name):
                generate({key: value for key, value in self.settings.items() if key != name})
            invalid = (0, 1, 'false', None) if type(self.settings[name]) is bool else (True, False, 1.5, '1', None)
            for value in invalid:
                with self.subTest(field=name, value=value), self.assertRaisesRegex(ValueError, name):
                    generate(dict(self.settings, **{name: value}))

    def test_integer_ranges(self):
        for name, low, high in (('width', 1, 0x7FFFFFFF), ('height', 1, 0x7FFFFFFF),
                               ('thread_count', 1, 0x7FFFFFFF), ('max_opponents', 0, 0x7FFFFFFF),
                               ('shader_quality', 0, 3), ('anisotropy', 0, 6)):
            for value in (low - 1, high + 1):
                with self.subTest(field=name, value=value), self.assertRaisesRegex(ValueError, name):
                    generate(dict(self.settings, **{name: value}))
            for value in (low, high):
                with self.subTest(field=name, value=value):
                    self.assertIsInstance(generate(dict(self.settings, **{name: value})), bytes)


if __name__ == '__main__':
    unittest.main()
