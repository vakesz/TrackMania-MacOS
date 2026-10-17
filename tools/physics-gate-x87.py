#!/usr/bin/env python3
"""Physics gate: run TMNF's own x87 math under x87sidecar builds and compare results.

Generates a call table from the installed TmForever.map: the member and static
functions of the game's Gm* math classes (vectors, matrices, quaternions,
isometries, frustums, collision shapes) whose arguments are references to Gm
structures, floats, doubles, bools or small integers. tests/x87-gamecode.c maps
TmForever.exe without running it and calls each function with deterministic random
and edge-case inputs (huge, tiny, subnormal, signed zero, infinity), in PC24 with all
four rounding modes and in PC53, and prints one hash of all outputs per function
and mode.

Stock Rosetta (exact x87) is the reference for every build. The first translator is
the baseline: every later one must produce identical hashes, or the gate fails. Put
the current translator first to check that a change keeps every result.

Runs in the selected prefix, as make smoke does, and needs the installed game. Close
the game first. Functions that crash or hang on random inputs (some need game state,
such as the C runtime's math dispatch) are listed in tests/x87-gamecode-skip.txt.

  python3 tools/physics-gate-x87.py                                # installed vs stock
  python3 tools/physics-gate-x87.py --sidecar old=PATH --sidecar new=PATH
  python3 tools/physics-gate-x87.py --sidecar PATH --explain NAME  # values that differ
"""

import argparse
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x87_wine  # noqa: E402

ROOT = x87_wine.ROOT
SOURCE = ROOT / 'tests/x87-gamecode.c'
SKIP = ROOT / 'tests/x87-gamecode-skip.txt'
GAME_EXE = r'C:\Program Files (x86)\TmNationsForever\TmForever.exe'
# Classes whose functions need allocated game objects or archives, not plain structs.
EXCLUDED_CLASSES = ('GmArchive', 'GmSurfMesh', 'GmField2', 'GmFunc', 'GmField2Base', 'GmField2Compressed')
IMAGE_BASE = 0x400000

SIMPLE = {'X': 'void', 'M': 'float', 'N': 'double', '_N': 'bool', 'H': 'int', 'I': 'unsigned',
          'K': 'unsigned', 'J': 'long', 'D': 'char', 'E': 'unsigned char', 'F': 'short', 'G': 'unsigned short'}


class Unsupported(Exception):
    pass


def parse(mangled):
    """Decode the subset of MSVC names used by Gm* functions.

    Returns (method, cls, convention, ret, args); each arg is 'buf', 'float', 'double',
    'bool' or 'small' (an integer argument, passed values 0..2)."""
    m = re.match(r'\?([A-Za-z0-9_]+)@(Gm[A-Za-z0-9]+)@@(.*)$', mangled)
    if not m:
        raise Unsupported('name')
    method, cls, rest = m.groups()
    if cls.startswith(EXCLUDED_CLASSES):
        raise Unsupported(f'class {cls}')
    names = [method, cls]  # name back-references 0 and 1
    if rest[:3] in ('QAE', 'QBE', 'AAE', 'ABE', 'IAE', 'IBE', 'UAE', 'UBE'):
        convention, pos = 'thiscall', 3
    elif rest[:2] == 'SA':
        convention, pos = 'cdecl', 2
    else:
        raise Unsupported('convention')

    def read_name(p):
        """Qualified class name: fragments 'Name@' or a name back-reference digit, ended by '@'."""
        parts = []
        while rest[p] != '@':
            if rest[p].isdigit():
                parts.append(names[int(rest[p])])
                p += 1
            else:
                end = rest.index('@', p)
                parts.append(rest[p:end])
                names.append(rest[p:end])
                p = end + 1
        return parts[0], p + 1

    arg_types = []  # for argument back-references

    def read_type(p, top=False):
        if rest[p].isdigit():
            return arg_types[int(rest[p])], p + 1
        start = p
        if rest.startswith('_N', p):
            kind, p = 'bool', p + 2
        elif rest[p] in SIMPLE:
            kind, p = {'M': 'float', 'N': 'double', 'X': 'void'}.get(rest[p], 'small'), p + 1
        elif rest[p] in 'AP' and p + 1 < len(rest) and rest[p + 1] in 'AB':
            p += 2
            if rest[p] in 'VU':
                name, p = read_name(p + 1)
                if not name.startswith('Gm') or name.startswith(EXCLUDED_CLASSES):
                    raise Unsupported(f'class {name}')
                kind = 'buf'
            elif rest[p] == 'M':
                kind, p = 'buf', p + 1  # float pointer: point at a float buffer
            else:
                raise Unsupported('pointer')
        elif rest[p] in 'VU':
            raise Unsupported('class by value')
        else:
            raise Unsupported(f'type {rest[p]}')
        if top and p - start > 1:
            arg_types.append(kind)
        return kind, p

    if rest[pos] == '?':
        raise Unsupported('returns class')
    ret, pos = read_type(pos)
    if ret == 'buf':
        raise Unsupported('returns reference')
    args = []
    if rest[pos:] in ('XZ', 'XZ@'):
        return method, cls, convention, ret, args
    while rest[pos] not in '@Z':
        kind, pos = read_type(pos, top=True)
        args.append(kind)
    if len(args) > 6:
        raise Unsupported('too many arguments')
    return method, cls, convention, ret, args

C_TYPES = {'buf': 'void*', 'float': 'float', 'double': 'double', 'bool': 'unsigned char', 'small': 'unsigned'}
C_RET = {'void': 'void', 'float': 'float', 'double': 'double', 'bool': 'unsigned char', 'small': 'unsigned'}


def functions(map_text, skip=()):
    """[(name, rva, convention, ret, args)] for the supported Gm* functions of a linker map."""
    found = []
    for line in map_text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[3] != 'f' or '@Gm' not in parts[1] or parts[1] in skip:
            continue
        try:
            method, cls, convention, ret, args = parse(parts[1])
        except (Unsupported, IndexError, ValueError):
            continue
        found.append((parts[1], int(parts[2], 16) - IMAGE_BASE, convention, ret, tuple(args)))
    return sorted(set(found), key=lambda f: f[1])


def generate(funcs):
    """C source of the call table: one typed wrapper per function."""
    out = ['/* Generated by tools/physics-gate-x87.py from TmForever.map; do not edit. */',
           '#include "x87-gamecode.h"', '']
    for i, (name, rva, convention, ret, args) in enumerate(funcs):
        attr = '__attribute__((thiscall))' if convention == 'thiscall' else '__attribute__((cdecl))'
        params = (['void*'] if convention == 'thiscall' else []) + [C_TYPES[a] for a in args]
        call_args = ['c->buf[0]'] if convention == 'thiscall' else []
        b = 1 if convention == 'thiscall' else 0
        for a in args:
            if a == 'buf':
                call_args.append(f'c->buf[{b}]')
                b += 1
            else:
                call_args.append({'float': 'c->f', 'double': 'c->d', 'bool': 'c->flag'}.get(a, 'c->small'))
        rtype = C_RET[ret]
        out.append(f'static void call_{i}(struct call *c) {{  /* {name} */')
        out.append(f'    typedef {rtype} ({attr} *fn)({", ".join(params) or "void"});')
        call = f'((fn)(c->base + 0x{rva:x}))({", ".join(call_args)})'
        if rtype == 'void':
            out.append(f'    {call};')
        else:
            out.append(f'    {rtype} r = {call};')
            out.append('    memcpy(c->ret, &r, sizeof(r));')
        out.append('}')
    out += ['', 'const struct function FUNCTIONS[] = {']
    for i, (name, rva, convention, ret, args) in enumerate(funcs):
        bufs = (1 if convention == 'thiscall' else 0) + args.count('buf')
        out.append(f'    {{"{name}", call_{i}, {bufs}}},')
    out += ['};', f'const int FUNCTION_COUNT = {len(funcs)};']
    return '\n'.join(out) + '\n'


class Gate:
    def __init__(self, work, map_path, calls):
        self.work, self.map_path, self.calls = Path(work), map_path, calls
        self.wine, self.prefix, self.installed, _ = x87_wine.settings()
        self.build()

    def build(self):
        skip = set(SKIP.read_text().split()) if SKIP.exists() else set()
        self.funcs = functions(self.map_path.read_text(errors='replace'), skip)
        (self.work / 'table.c').write_text(generate(self.funcs))
        self.exe = self.work / 'x87-gamecode.exe'
        subprocess.run([x87_wine.compiler(), '-O1', '-std=gnu11', '-Wall', '-Wno-unused-function',
                        f'-I{SOURCE.parent}', '-Wl,--image-base=0x10000000', '-Wl,--disable-dynamicbase',
                        '-o', str(self.exe), str(SOURCE), str(self.work / 'table.c')], check=True)

    def run(self, sidecar, args, timeout):
        env = x87_wine.environment(self.prefix, sidecar)
        return x87_wine.run(env, [str(self.wine), str(self.exe), GAME_EXE, *args], timeout)

    def hashes(self, sidecar, learn=False):
        """{(function, mode): hash} over all functions, resuming after a crash.

        With learn=True (stock Rosetta), a crash marks a function that needs game state
        the harness does not set up: it is added to the skip list and the table rebuilt."""
        got, start = {}, 0
        while True:
            status, text = self.run(sidecar, [str(self.calls), str(start)], 3600)
            got.update(((m[1], m[2]), m[3]) for m in
                       re.finditer(r'^HASH (\S+) (\S+) ([0-9a-f]{16})$', text, re.M))
            if re.search(r'^DONE$', text, re.M):
                return got, []
            crash = re.search(r'^CRASH (\d+)', text, re.M)
            if not crash:
                raise SystemExit(f'harness failed under {x87_wine.label(sidecar)} (exit {status}):\n{text[-600:]}')
            name = self.funcs[int(crash[1])][0]
            if not learn:
                return got, [name]
            print(f'  skip {name}: crashes under stock Rosetta too', flush=True)
            with SKIP.open('a') as f:
                f.write(name + '\n')
            self.build()
            return self.hashes(sidecar, learn)

    def dump(self, sidecar, name):
        index = next((i for i, f in enumerate(self.funcs) if f[0] == name), None)
        if index is None:
            raise SystemExit(f'{name} is not in the call table (skipped, or not a supported Gm function)')
        _, text = self.run(sidecar, [str(self.calls), str(index), 'dump'], 600)
        rows = [line.split() for line in text.splitlines()]
        outputs = {tuple(r[1:5]): (r[5], r[6]) for r in rows if len(r) == 7 and r[0] == 'W'}
        return outputs


def as_float(word):
    return struct.unpack('<f', struct.pack('<I', int(word, 16)))[0]


def explain(gate, sidecar, name):
    """Print the output words of one function that differ from stock Rosetta."""
    ref, got = gate.dump(x87_wine.STOCK, name), gate.dump(sidecar, name)
    shown = 0
    print(f'{name}: differing output words (mode call buffer word: input -> stock | translator)')
    for key in sorted(set(ref) | set(got), key=lambda k: (k[0], int(k[1]), int(k[2]), int(k[3]))):
        a, b = ref.get(key), got.get(key)
        out_a = a[1] if a else b[0]  # an unchanged word keeps its input value
        out_b = b[1] if b else a[0]
        if out_a == out_b:
            continue
        shown += 1
        if shown <= 40:
            source = (a or b)[0]
            print(f'  {key[0]:<9} {key[1]:>4} {key[2]:>2} {key[3]:>3}: {source} ({as_float(source):.9g}) -> '
                  f'{out_a} ({as_float(out_a):.9g}) | {out_b} ({as_float(out_b):.9g})')
    print(f'  {shown} differing words')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sidecar', action='append', default=[], metavar='[NAME=]PATH',
                        help='x87sidecar binary (repeatable; the first is the baseline; default: installed)')
    parser.add_argument('--calls', type=int, default=200, help='inputs per function and mode')
    parser.add_argument('--explain', metavar='NAME', help='show the values one function computes differently')
    parser.add_argument('--json', type=Path, help='save every hash to this file')
    args = parser.parse_args()
    _, _, installed, game = x87_wine.settings()
    map_path = game / 'TmForever.map'
    if not (game / 'TmForever.exe').is_file() or not map_path.is_file():
        raise SystemExit(f'The game is not installed in {game}. Run make install first.')
    named = [x87_wine.parse_sidecar(arg) for arg in args.sidecar] or [('installed', installed)]

    with tempfile.TemporaryDirectory(prefix='mactmnf-x87-gate-') as work:
        gate = Gate(work, map_path, args.calls)
        if args.explain:
            explain(gate, named[0][1], args.explain)
            return 0
        reference, _ = gate.hashes(x87_wine.STOCK, learn=True)
        print(f'{len(gate.funcs)} game functions; {len(reference)} function/mode results; '
              f'reference: stock Rosetta', flush=True)
        results, status = {x87_wine.STOCK: reference}, 0
        for name, path in named:
            got, crashed = gate.hashes(path)
            results[name] = got
            if crashed:
                print(f'FAIL {name}: crashed in {crashed[0]}, which runs under stock Rosetta')
                status = 1
                continue
            differ = sum(got.get(k) != v for k, v in reference.items())
            print(f'{name}: {differ} of {len(reference)} results differ from stock Rosetta', flush=True)
        base_name = named[0][0]
        for name, _ in named[1:]:
            if name not in results or base_name not in results:
                continue
            differ = sorted(k for k in reference if results[name].get(k) != results[base_name].get(k))
            if differ:
                status = 1
                print(f'FAIL {name}: {len(differ)} results differ from {base_name}')
                for function, mode in differ[:15]:
                    print(f'       {mode:<10} {function}')
            else:
                print(f'PASS {name}: all {len(reference)} results identical to {base_name}')
        if args.json:
            args.json.write_text(json.dumps({n: {f'{f} {m}': h for (f, m), h in r.items()}
                                             for n, r in results.items()}, indent=1))
        return status


if __name__ == '__main__':
    sys.exit(main())
