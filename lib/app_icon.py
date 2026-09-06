"""Build a macOS .icns from the icon group embedded in a Windows executable.

Usage: app_icon.py <program.exe> <output.icns>

Nadeo's icon stays inside the installed game; nothing is redistributed. The
PE resource tree is walked directly so no third-party tools are needed. Each
icon size is converted with sips and assembled with iconutil, both part of
macOS. Missing sizes are scaled from the largest image.
"""
import os
import struct
import subprocess
import sys
import tempfile

RT_ICON, RT_GROUP_ICON = 3, 14
ICONSET = [('icon_16x16.png', 16), ('icon_16x16@2x.png', 32), ('icon_32x32.png', 32),
           ('icon_32x32@2x.png', 64), ('icon_128x128.png', 128), ('icon_128x128@2x.png', 256),
           ('icon_256x256.png', 256), ('icon_256x256@2x.png', 512), ('icon_512x512.png', 512)]


def resources(data):
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    optional = struct.unpack_from('<H', data, pe + 20)[0]
    table = pe + 24 + optional
    for i in range(count):
        name, _, va, _, raw = struct.unpack_from('<8sIIII', data, table + i * 40)[:5]
        if name.rstrip(b'\0') == b'.rsrc':
            return va, raw
    raise ValueError('no resource section')


def entries(data, raw, offset):
    named, ids = struct.unpack_from('<HH', data, raw + offset + 12)
    return [struct.unpack_from('<II', data, raw + offset + 16 + k * 8) for k in range(named + ids)]


def icon_groups(data):
    va, raw = resources(data)
    images, groups = {}, {}
    for kind, offset in entries(data, raw, 0):
        if kind not in (RT_ICON, RT_GROUP_ICON):
            continue
        for name, offset2 in entries(data, raw, offset & 0x7FFFFFFF):
            for _, leaf in entries(data, raw, offset2 & 0x7FFFFFFF):
                rva, size = struct.unpack_from('<II', data, raw + leaf)[:2]
                start = raw + rva - va
                (images if kind == RT_ICON else groups)[name & 0xFFFF] = data[start:start + size]
    return images, groups


def best_images(data):
    """Return {size: ico bytes} for the deepest image of each size in the richest group."""
    images, groups = icon_groups(data)
    chosen = {}
    for group in groups.values():
        count = struct.unpack_from('<H', group, 4)[0]
        candidates = {}
        for k in range(count):
            w, h, colors, _, planes, bits, size, ident = struct.unpack_from('<BBBBHHIH', group, 6 + k * 14)
            side = w or 256
            if ident in images and (side not in candidates or bits > candidates[side][0]):
                header = struct.pack('<HHH', 0, 1, 1)
                entry = struct.pack('<BBBBHHII', w, h, colors, 0, planes, bits, len(images[ident]), 22)
                candidates[side] = (bits, header + entry + images[ident])
        if len(candidates) > len(chosen):
            chosen = candidates
    return {side: ico for side, (bits, ico) in chosen.items()}


def run(*command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(exe, output):
    icons = best_images(open(exe, 'rb').read())
    if not icons:
        raise SystemExit(f'{exe}: no icons found')
    largest = max(icons)
    with tempfile.TemporaryDirectory() as work:
        pngs = {}
        for side, ico in icons.items():
            path = os.path.join(work, f'{side}.ico')
            open(path, 'wb').write(ico)
            pngs[side] = os.path.join(work, f'{side}.png')
            run('sips', '-s', 'format', 'png', path, '--out', pngs[side])
        iconset = os.path.join(work, 'App.iconset')
        os.mkdir(iconset)
        for name, side in ICONSET:
            target = os.path.join(iconset, name)
            if side in pngs:
                run('cp', pngs[side], target)
            else:
                run('sips', '-z', str(side), str(side), pngs[largest], '--out', target)
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        run('iconutil', '-c', 'icns', iconset, '-o', output)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
