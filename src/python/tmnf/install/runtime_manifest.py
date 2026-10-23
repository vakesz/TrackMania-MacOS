#!/usr/bin/env python3
"""Validate shipped runtimes without executing them. Compatible with Python 3.9.

CLI: verify ROOT VERSION; extract ARCHIVE STAGING SHA256; publish SOURCE DEST VERSION.
verify_runtime(root, version) returns a comparable inventory, or raises ValueError
or OSError. SHA256SUMS covers every regular file except itself; shipped manifests
omit symlinks, so verification checks their containment, and publication compares
their exact targets against the verified source. No new artifact metadata is used.
"""

import argparse
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import unicodedata


EXECUTABLES = ('bin/wine', 'bin/wineserver', 'bin/wineboot', 'bin/x87sidecar')
REQUIRED_FILES = (
    '.tmnf-runtime', 'lib/d9vk/d3d9.dll', 'lib/libMoltenVK.dylib',
    'config/dxvk.conf', 'config/Default.SystemConfig.Gbx',
    'lib/wine/x86_64-windows/wineboot.exe', 'libexec/tmnf-session.exe',
    'lib/wine/x86_64-windows/conhost.exe',
) + tuple('lib/wine/{}/{}'.format(arch, name)
          for arch in ('i386-windows', 'x86_64-windows')
          for name in ('d3dx9_30.dll', 'd3dx9_43.dll', 'd3dcompiler_43.dll'))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def safe_name(name):
    if (not name or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsafe runtime path: {!r}'.format(name))
    return name


def safe_link(name, target):
    if (not target or target.startswith('/') or '\\' in target
            or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        raise ValueError('Unsafe runtime link: {} -> {!r}'.format(name, target))
    parts = name.split('/')[:-1]
    for part in target.split('/'):
        if part == '..':
            if not parts:
                raise ValueError('Runtime link escapes root: {}'.format(name))
            parts.pop()
        elif part not in ('', '.'):
            parts.append(part)


def check_link(root, path, name, allow_dangling=False):
    """Return the target of a link that stays inside root and resolves to an existing path."""
    target = os.readlink(path)
    safe_link(name, target)
    try:
        if allow_dangling:
            if not link_stays_inside(root, path.parent, target):
                raise ValueError('escapes')
        else:
            prefix = path.parent
            for part in target.split('/'):
                prefix = prefix / part
                prefix.resolve(strict=True).relative_to(root)
            path.stat()  # Also reject invalid kernel paths such as regular-file/..
    except (ValueError, RuntimeError, OSError) as exc:
        raise ValueError('Broken or escaping runtime link: {}'.format(name)) from exc
    return target


def link_stays_inside(root, directory, target):
    """Walk target from directory, following existing links, and tolerate a missing tail.

    Upstream trees ship links that a build later materialises. A '..' after a missing
    component is refused, since that component could become a link out of root.
    """
    current = directory
    missing = False
    parts = [part for part in target.split('/') if part not in ('', '.')]
    for index, part in enumerate(parts):
        if not missing and not current.is_dir():
            return False
        if part == '..':
            if missing:
                return False
            current = current.parent
        else:
            candidate = current / part
            if missing or not os.path.lexists(candidate):
                missing = True
                current = candidate
            else:
                current = Path(os.path.realpath(candidate))
                missing = not os.path.lexists(current)
        try:
            current.relative_to(root)
        except ValueError:
            return False
    return True


def check_tree_links(root, skip=(), allow_dangling=False):
    """Reject links under root that escape it (or dangle); skip names directly under root."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Tree root must be a real directory: {}'.format(root))
    root = root.resolve()
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if directory == root and path.name in skip:
                continue
            name = safe_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                check_link(root, path, name, allow_dangling)
            elif stat.S_ISDIR(mode):
                pending.append(path)


def safe_root_name(name):
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name) or name in ('.', '..'):
        raise ValueError('Unsafe archive root name: {!r}'.format(name))
    return name


def verify_runtime(root, version):
    """Check ownership, required payload, all file hashes, and contained links."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Runtime root must be a real directory: {}'.format(root))
    root = root.resolve()
    inventory = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            name = safe_name(path.relative_to(root).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = check_link(root, path, name)
                inventory[name] = ('link', target)
            elif stat.S_ISDIR(mode):
                inventory[name] = ('directory', stat.S_IMODE(mode))
                pending.append(path)
            elif stat.S_ISREG(mode):
                if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise ValueError('Unsafe runtime file permissions: {}'.format(name))
                inventory[name] = ('file', stat.S_IMODE(mode), digest(path))
            else:
                raise ValueError('Unsupported runtime file type: {}'.format(name))

    for name in ('.tmnf-runtime', 'SHA256SUMS'):
        if inventory.get(name, (None,))[0] != 'file':
            raise ValueError('Missing regular runtime metadata: {}'.format(name))
    if (root / '.tmnf-runtime').read_text().rstrip('\n') != version:
        raise ValueError('Runtime has the wrong version')
    for name in REQUIRED_FILES + EXECUTABLES:
        path = root / name
        if not path.is_file():
            raise ValueError('Missing required runtime file: {}'.format(name))
        if name in EXECUTABLES and not os.access(path, os.X_OK):
            raise ValueError('Runtime binary is not executable: {}'.format(name))
    if not (root / 'licenses').is_dir():
        raise ValueError('Runtime licenses directory is missing')

    manifest = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *](.+)', line)
        if not match:
            raise ValueError('Malformed SHA256SUMS entry: {!r}'.format(line))
        checksum, name = match.groups()
        # shasum's common ./ prefix and binary marker are shipped-format compatible.
        name = safe_name(name[2:] if name.startswith('./') else name)
        if name in manifest:
            raise ValueError('Duplicate SHA256SUMS entry: {}'.format(name))
        manifest[name] = checksum.lower()
    files = {name: item[2] for name, item in inventory.items()
             if item[0] == 'file' and name != 'SHA256SUMS'}
    if manifest.keys() != files.keys():
        raise ValueError('SHA256SUMS inventory differs: missing={}, unexpected={}'.format(
            sorted(files.keys() - manifest.keys()), sorted(manifest.keys() - files.keys())))
    for name, checksum in manifest.items():
        if files[name] != checksum:
            raise ValueError('Runtime file SHA-256 mismatch: {}'.format(name))
    return inventory


def extract_archive(archive, staging, expected, root_name='runtime'):
    """Validate all headers before writing; never extract through archive links."""
    safe_root_name(root_name)
    prefix_name = root_name + '/'
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
        raise ValueError('Invalid archive SHA-256')
    # Hash and extract through the same descriptor, not a replaceable cache pathname.
    with Path(archive).open('rb') as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        if checksum.hexdigest() != expected.lower():
            raise ValueError('Runtime archive SHA-256 mismatch')
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:*') as tar:
            members = {}
            spellings = {}
            for member in tar.getmembers():
                name = safe_name(member.name)
                if name != root_name and not name.startswith(prefix_name):
                    raise ValueError('Unexpected runtime archive entry: {}'.format(name))
                if name in members:
                    raise ValueError('Duplicate runtime archive entry: {}'.format(name))
                if not (member.isdir() or member.isreg() or member.issym() or member.islnk()):
                    raise ValueError('Unsupported archive file type: {}'.format(name))
                if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise ValueError('Unsafe archive permissions: {}'.format(name))
                if member.isdir() and member.mode & 0o700 != 0o700:
                    raise ValueError('Runtime directories must be owner-accessible: {}'.format(name))
                if name == root_name and not member.isdir():
                    raise ValueError('Archive root must be a directory')
                # macOS normally has a case-insensitive, normalization-insensitive FS.
                parts = name.split('/')
                for index in range(1, len(parts) + 1):
                    prefix = '/'.join(parts[:index])
                    folded = unicodedata.normalize('NFD', prefix).casefold()
                    if spellings.setdefault(folded, prefix) != prefix:
                        raise ValueError('Colliding archive paths: {}'.format(name))
                if member.issym():
                    safe_link(name[len(prefix_name):], member.linkname)
                elif member.islnk():
                    target = safe_name(member.linkname)
                    if not target.startswith(prefix_name):
                        raise ValueError('Hard link escapes runtime: {}'.format(name))
                members[name] = member

            hard_targets = {}
            for name, member in members.items():
                for parent in Path(name).parents:
                    ancestor = members.get(parent.as_posix())
                    if ancestor is not None and not ancestor.isdir():
                        raise ValueError('Archive writes through a non-directory: {}'.format(name))
                if member.islnk():
                    target = member
                    seen = {name}
                    while target.islnk():
                        if target.linkname in seen or target.linkname not in members:
                            raise ValueError('Invalid archive hard link: {}'.format(name))
                        seen.add(target.linkname)
                        target = members[target.linkname]
                    if not target.isreg():
                        raise ValueError('Hard link must target a regular file: {}'.format(name))
                    hard_targets[name] = target.name

            staging = Path(staging)
            root = staging / root_name
            root.mkdir()  # The caller owns a unique staging directory; never merge trees.
            directories = {}
            for name, member in members.items():
                path = staging / name
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    directories[path] = member.mode & 0o777
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if member.isreg():
                        with tar.extractfile(member) as source, path.open('xb') as dest:
                            shutil.copyfileobj(source, dest)
                        path.chmod(member.mode & 0o777)
            for name, target in hard_targets.items():
                os.link(staging / target, staging / name)
            for name, member in members.items():
                if member.issym():
                    (staging / name).symlink_to(member.linkname)
            for path, mode in sorted(directories.items(), key=lambda item: len(item[0].parts), reverse=True):
                path.chmod(mode)


def publish_runtime(source, destination, version):
    """Publish without replacing even an empty destination; reuse only identical trees."""
    source, destination = Path(source), Path(destination)
    inventory = verify_runtime(source, version)
    if os.path.lexists(destination):
        if verify_runtime(destination, version) != inventory:
            raise ValueError('Existing runtime differs from the verified source')
        return
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:  # RENAME_EXCL
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            if verify_runtime(destination, version) != inventory:
                raise ValueError('Concurrently installed runtime differs from the verified source')
        else:
            raise OSError(error, os.strerror(error), str(destination))


def cli_root_name(value):
    try:
        return safe_root_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('root')
    verify.add_argument('version')
    extract = commands.add_parser('extract')
    extract.add_argument('archive')
    extract.add_argument('staging')
    extract.add_argument('sha256')
    extract.add_argument('--root', default='runtime', type=cli_root_name)
    links = commands.add_parser('links')
    links.add_argument('root')
    links.add_argument('--skip', action='append', default=[])
    links.add_argument('--allow-dangling', action='store_true')
    publish = commands.add_parser('publish')
    publish.add_argument('source')
    publish.add_argument('destination')
    publish.add_argument('version')
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            verify_runtime(args.root, args.version)
        elif args.command == 'links':
            check_tree_links(args.root, args.skip, args.allow_dangling)
        elif args.command == 'extract':
            extract_archive(args.archive, args.staging, args.sha256, args.root)
        else:
            publish_runtime(args.source, args.destination, args.version)
    except (ValueError, OSError, tarfile.TarError, UnicodeError) as exc:
        print('error: {}'.format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
