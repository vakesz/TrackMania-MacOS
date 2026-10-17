"""Offline checks for tools/physics-gate-x87.py: TmForever.map parsing and call tables.

Needs no game, Wine or compiler; the names are taken from the game's linker map.
"""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('physics_gate_x87', ROOT / 'tools/physics-gate-x87.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

MAP = '''
 0001:004dfca0       ?RotateX@GmMat3@@QAEXM@Z   008e0ca0 f   geom:GmMat3.obj
 0001:004e1970       ?Mult@GmIso4@@QAEXABV1@@Z  008e2970 f   geom:GmIso4.obj
 0001:004e21c0       ?SetRotation@GmQuat@@QAEXMABVGmVec3@@@Z 008e31c0 f   geom:GmQuat.obj
 0001:0038b540       ?SetGmIso3FromSimi2@GmSimi2@@SAXABV1@AAVGmIso3@@@Z 0078c540 f i control:ControlEffectSimi.obj
 0001:0003fc60       ?ClampReal@GmFunc@@SAMMMM@Z 00440c60 f i systemengine:SystemEngine_Gpu.obj
 0001:004e10d0       ?ConvertMethodGet@GmUnit@@SAHABVCFastString@@AAW4EConvertMethod@1@AAVCFastStringInt@@@Z 008e20d0 f   geom:GmUnit.obj
 0002:00001000       ?s_Zero@GmVec3@@2V1@B     00b31000     geom:GmVec3.obj
'''


class ParseTests(unittest.TestCase):
    def test_member_functions(self):
        self.assertEqual(gate.parse('?RotateX@GmMat3@@QAEXM@Z'),
                         ('RotateX', 'GmMat3', 'thiscall', 'void', ['float']))
        # V1 refers back to the class name: GmIso4::Mult(const GmIso4&).
        self.assertEqual(gate.parse('?Mult@GmIso4@@QAEXABV1@@Z'),
                         ('Mult', 'GmIso4', 'thiscall', 'void', ['buf']))
        self.assertEqual(gate.parse('?SetRotation@GmQuat@@QAEXMABVGmVec3@@@Z'),
                         ('SetRotation', 'GmQuat', 'thiscall', 'void', ['float', 'buf']))

    def test_static_function(self):
        self.assertEqual(gate.parse('?SetGmIso3FromSimi2@GmSimi2@@SAXABV1@AAVGmIso3@@@Z'),
                         ('SetGmIso3FromSimi2', 'GmSimi2', 'cdecl', 'void', ['buf', 'buf']))

    def test_unsupported(self):
        for name in ('?ClampReal@GmFunc@@SAMMMM@Z',  # excluded class
                     '?ConvertMethodGet@GmUnit@@SAHABVCFastString@@AAW4EConvertMethod@1@AAVCFastStringInt@@@Z'):
            with self.subTest(name=name), self.assertRaises(gate.Unsupported):
                gate.parse(name)


class TableTests(unittest.TestCase):
    def test_functions_from_map(self):
        funcs = gate.functions(MAP, skip={'?RotateX@GmMat3@@QAEXM@Z'})
        self.assertEqual([f[0] for f in funcs], ['?SetGmIso3FromSimi2@GmSimi2@@SAXABV1@AAVGmIso3@@@Z',
                                                 '?Mult@GmIso4@@QAEXABV1@@Z',
                                                 '?SetRotation@GmQuat@@QAEXMABVGmVec3@@@Z'])
        self.assertEqual(funcs[1][1], 0x4e2970)  # address minus the 0x400000 image base

    def test_buffers_start_after_this(self):
        source = gate.generate(gate.functions(MAP))
        # Members pass "this" in buffer 0; static functions start their arguments at 0.
        self.assertIn('((fn)(c->base + 0x4e2970))(c->buf[0], c->buf[1]);', source)
        self.assertIn('((fn)(c->base + 0x4e31c0))(c->buf[0], c->f, c->buf[1]);', source)
        self.assertIn('((fn)(c->base + 0x38c540))(c->buf[0], c->buf[1]);', source)
        self.assertIn('{"?SetGmIso3FromSimi2@GmSimi2@@SAXABV1@AAVGmIso3@@@Z", call_0, 2},', source)
        self.assertIn('const int FUNCTION_COUNT = 4;', source)


if __name__ == '__main__':
    unittest.main()
