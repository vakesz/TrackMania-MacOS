"""Build a macOS .icns from the icon group embedded in a Windows executable.

Usage: app_icon.py <program.exe> <output.icns>

Nadeo's icon stays inside the installed game; nothing is redistributed. The
PE resource tree is walked directly so no third-party tools are needed. Each
icon size is converted with sips and assembled with iconutil, both part of
macOS. Missing sizes are scaled from the largest image.
"""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

RT_ICON, RT_GROUP_ICON = 3, 14
ICONSET = [('icon_16x16.png', 16), ('icon_16x16@2x.png', 32), ('icon_32x32.png', 32),
           ('icon_32x32@2x.png', 64), ('icon_128x128.png', 128), ('icon_128x128@2x.png', 256),
           ('icon_256x256.png', 256), ('icon_256x256@2x.png', 512), ('icon_512x512.png', 512)]


def check_bounds(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError('truncated or out-of-bounds icon resource')


def resources(data):
    """Return the resource RVA and its file-backed bytes, not section padding."""
    check_bounds(data, 0, 64)
    if data[:2] != b'MZ':
        raise ValueError('not a DOS executable')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    check_bounds(data, pe, 24)
    if pe < 64 or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('not a PE executable')
    count = struct.unpack_from('<H', data, pe + 6)[0]
    optional = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    check_bounds(data, opt, optional)
    if optional < 2:
        raise ValueError('missing PE optional header')
    magic = struct.unpack_from('<H', data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise ValueError('unsupported PE optional header')
    directories = 96 if magic == 0x10B else 112
    if optional < directories + 24:
        raise ValueError('missing PE resource directory')
    directory_count = struct.unpack_from('<I', data, opt + directories - 4)[0]
    if not 3 <= directory_count <= (optional - directories) // 8:
        raise ValueError('invalid PE data directory count')
    rva, size = struct.unpack_from('<II', data, opt + directories + 16)
    if not rva or size < 16:
        raise ValueError('no PE resources')
    table = opt + optional
    check_bounds(data, table, count * 40)
    for i in range(count):
        va, raw_size, raw = struct.unpack_from('<III', data, table + i * 40 + 12)
        if va <= rva < va + raw_size:
            if rva - va + size > raw_size:
                raise ValueError('resources extend beyond their section')
            check_bounds(data, raw, raw_size)
            start = raw + rva - va
            return rva, data[start:start + size]
    raise ValueError('resource directory is not file-backed')


def entries(data, offset):
    check_bounds(data, offset, 16)
    named, ids = struct.unpack_from('<HH', data, offset + 12)
    check_bounds(data, offset + 16, (named + ids) * 8)
    return [struct.unpack_from('<II', data, offset + 16 + k * 8) for k in range(named + ids)]


def icon_groups(data):
    va, resource = resources(data)
    images, groups = {}, {}
    for kind, offset in entries(resource, 0):
        if kind not in (RT_ICON, RT_GROUP_ICON):
            continue
        if not offset & 0x80000000:
            raise ValueError('expected icon type directory')
        for name, offset2 in entries(resource, offset & 0x7FFFFFFF):
            if name > 0xFFFF:  # Named resources are not supported by this extractor.
                continue
            if not offset2 & 0x80000000:
                raise ValueError('expected icon language directory')
            for _, leaf in entries(resource, offset2 & 0x7FFFFFFF):
                if leaf & 0x80000000:
                    raise ValueError('expected icon data entry')
                check_bounds(resource, leaf, 16)
                rva, size = struct.unpack_from('<II', resource, leaf)
                start = rva - va
                check_bounds(resource, start, size)
                (images if kind == RT_ICON else groups)[name] = resource[start:start + size]
    return images, groups


def best_images(data):
    """Return {size: ico bytes} for the deepest image of each size in the richest group."""
    images, groups = icon_groups(data)
    chosen = {}
    for group in groups.values():
        check_bounds(group, 0, 6)
        reserved, kind, count = struct.unpack_from('<HHH', group)
        if reserved or kind != 1:
            raise ValueError('unsupported icon group')
        check_bounds(group, 6, count * 14)
        candidates = {}
        for k in range(count):
            w, h, colors, reserved, planes, bits, size, ident = struct.unpack_from('<BBBBHHIH', group, 6 + k * 14)
            if reserved or w != h or ident not in images:
                continue
            if not size or size != len(images[ident]):
                raise ValueError('icon image size does not match its group')
            side = w or 256
            if side not in candidates or bits > candidates[side][0]:
                header = struct.pack('<HHH', 0, 1, 1)
                entry = struct.pack('<BBBBHHII', w, h, colors, 0, planes, bits, len(images[ident]), 22)
                candidates[side] = (bits, header + entry + images[ident])
        if len(candidates) > len(chosen):
            chosen = candidates
    return {side: ico for side, (bits, ico) in chosen.items()}


def run(*command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(exe, output):
    icons = best_images(pathlib.Path(exe).read_bytes())
    if not icons:
        raise SystemExit(f'{exe}: no icons found')
    largest = max(icons)
    with tempfile.TemporaryDirectory() as work:
        pngs = {}
        for side, ico in icons.items():
            path = os.path.join(work, f'{side}.ico')
            pathlib.Path(path).write_bytes(ico)
            pngs[side] = os.path.join(work, f'{side}.png')
            run('sips', '-s', 'format', 'png', path, '--out', pngs[side])
        iconset = os.path.join(work, 'App.iconset')
        os.mkdir(iconset)
        for name, side in ICONSET:
            target = os.path.join(iconset, name)
            if side not in pngs:
                pngs[side] = os.path.join(work, f'{side}.png')
                run('sips', '-z', str(side), str(side), pngs[largest], '--out', pngs[side])
            shutil.copyfile(pngs[side], target)
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        run('iconutil', '-c', 'icns', iconset, '-o', output)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    try:
        main(sys.argv[1], sys.argv[2])
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        # The app installer treats a nonzero status as a request for its generic icon.
        raise SystemExit(f'{sys.argv[1]}: could not build icon: {error}') from None
