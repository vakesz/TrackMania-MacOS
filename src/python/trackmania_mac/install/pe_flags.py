"""Set the NX_COMPAT flag on 32-bit PE files in an installed game folder.

Wine's PE loader enables executable-everywhere memory (`force_exec_prot`) as
soon as any loaded module lacks IMAGE_DLLCHARACTERISTICS_NX_COMPAT. Under
Rosetta every write to executable memory is then very slow, so a title whose
files lack the flag (config/titles/*.json `nx_compat`) runs a fraction of its
speed. This sets bit 0x0100 of DllCharacteristics and changes nothing else; the
PE checksum is left alone because Windows does not check it for these files.

Each name must be a plain file name of a regular file (not a symlink) inside the
game folder, and a 32-bit x86 PE (PE32). The file is read through one
descriptor and replaced atomically, keeping its permission bits. A file that already has
the flag is not touched. A file over 64 MiB is refused, only permission bits
(`0o777`) are kept, and stale temporary files from a killed run are swept first.

    python -m trackmania_mac.install.pe_flags nx-compat GAME_DIR NAME [NAME...]
    python -m trackmania_mac.install.pe_flags pending-nx SHA256 FILE

`pending-nx` only checks: it exits 0 when FILE is a PE32 x86 file without the
flag whose bytes, with the flag set, hash to SHA256 (so setting the flag is all
that is left to install it), and 1 otherwise. It prints an error only when FILE
cannot be read or is not such a PE file, and never writes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import pathlib
import stat
import struct
import sys
import tempfile
import typing

NX_COMPAT = 0x0100
MACHINE_I386 = 0x014C
PE32_MAGIC = 0x10B
TEMP_PREFIX = ".trackmania-mac-pe."
MAX_SIZE = 64 * 1024 * 1024
# Offsets from the "PE\0\0" signature: the COFF Machine field, the optional
# header (24 bytes in), and DllCharacteristics inside it (70 bytes in).
_MACHINE = 4
_OPTIONAL_SIZE = 20
_OPTIONAL = 24
_DLL_CHARACTERISTICS = _OPTIONAL + 70


def _plain_name(name: str) -> str:
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError("Unsafe file name: {!r}".format(name))
    return name


def _read(path: pathlib.Path) -> typing.Tuple[bytes, int]:
    """The file's bytes and mode, from one descriptor on a regular, non-symlink file."""
    try:
        # O_NONBLOCK: opening a FIFO must not wait for a writer; it fails the
        # regular-file check below instead.
        descriptor = os.open(
            str(path),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        raise ValueError("Cannot open {} (not a regular file?): {}".format(path, error)) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Not a regular file: {}".format(path))
        if info.st_size > MAX_SIZE:
            raise ValueError("Refusing a file over {} MiB: {}".format(MAX_SIZE >> 20, path))
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(), stat.S_IMODE(info.st_mode)


def _flags_offset(data: bytes, path: pathlib.Path) -> int:
    """Offset of DllCharacteristics in a PE32 x86 image, or ValueError."""
    if data[:2] != b"MZ" or len(data) < 0x40:
        raise ValueError("Not a PE file: {}".format(path))
    (lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if lfanew > len(data) - (_DLL_CHARACTERISTICS + 2) or data[lfanew : lfanew + 4] != b"PE\0\0":
        raise ValueError("Not a PE file: {}".format(path))
    (optional_size,) = struct.unpack_from("<H", data, lfanew + _OPTIONAL_SIZE)
    if optional_size < _DLL_CHARACTERISTICS - _OPTIONAL + 2:
        raise ValueError("PE optional header is too small: {}".format(path))
    (machine,) = struct.unpack_from("<H", data, lfanew + _MACHINE)
    if machine != MACHINE_I386:
        raise ValueError("Not a 32-bit x86 PE file: {}".format(path))
    (magic,) = struct.unpack_from("<H", data, lfanew + _OPTIONAL)
    if magic != PE32_MAGIC:
        raise ValueError("Not a PE32 image: {}".format(path))
    return lfanew + _DLL_CHARACTERISTICS


def sweep_temporaries(game_dir: str) -> None:
    """Remove temporary files a killed run left; the prefix lock rules out a live one."""
    folder = pathlib.Path(game_dir)
    for child in os.listdir(folder):
        if not child.startswith(TEMP_PREFIX):
            continue
        info = os.lstat(folder / child)
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
            os.unlink(folder / child)


def set_nx_compat(game_dir: str, name: str) -> bool:
    """Set the flag on GAME_DIR/NAME; True when the file was changed."""
    folder = pathlib.Path(game_dir)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Not a game folder: {}".format(folder))
    path = folder / _plain_name(name)
    data, mode = _read(path)
    offset = _flags_offset(data, path)
    (flags,) = struct.unpack_from("<H", data, offset)
    if flags & NX_COMPAT:
        return False
    patched = bytearray(data)
    struct.pack_into("<H", patched, offset, flags | NX_COMPAT)
    descriptor, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(folder))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(patched)
            handle.flush()
            os.fchmod(handle.fileno(), mode & 0o777)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return True


def pending_nx(sha256: str, file: str) -> bool:
    """True when setting the flag on FILE would give a file with this SHA-256."""
    path = pathlib.Path(file)
    data, _ = _read(path)
    offset = _flags_offset(data, path)
    (flags,) = struct.unpack_from("<H", data, offset)
    if flags & NX_COMPAT:
        return False
    patched = bytearray(data)
    struct.pack_into("<H", patched, offset, flags | NX_COMPAT)
    return hashlib.sha256(patched).hexdigest() == sha256.lower()


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Set PE flags on installed game files.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("nx-compat")
    command.add_argument("game_dir")
    command.add_argument("names", nargs="+")
    check = commands.add_parser("pending-nx")
    check.add_argument("sha256")
    check.add_argument("file")
    args = parser.parse_args(argv)
    try:
        if args.command == "pending-nx":
            return 0 if pending_nx(args.sha256, args.file) else 1
        folder = pathlib.Path(args.game_dir)
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Not a game folder: {}".format(folder))
        sweep_temporaries(args.game_dir)
        for name in args.names:
            set_nx_compat(args.game_dir, name)
    except (ValueError, OSError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
