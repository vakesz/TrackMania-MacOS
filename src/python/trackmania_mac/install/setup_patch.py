"""Write a modified copy of a title's pinned Inno Setup installer.

A registry entry may carry `source_patch`: byte edits to the setup's header, the
header's SHA-256 before them and the SHA-256 of the modified setup. Install then runs
the copy this module writes instead of the download, which stays unchanged in the
cache. Nations ESWC uses one byte: its setup's only [Run] entry, which offers to start
the game on the last page, gets Inno's skipifnotsilent flag, so a normal (clicked
through) install never shows or runs it
("Nations ESWC setup without a launch option" in docs/runtime.md).

Only the layout of an Inno Setup 5.1 setup is read. From the start of the file: the
loader program, which holds "Inno", the offset table's position and its complement at
0x30; the game data (setup.1); the header (setup.0: a 64-byte id, then two blocks,
each a CRC-checked size and compression byte followed by 4096-byte chunks that each
start with their CRC32); the compressed setup program; and the 40-byte offset table
("rDlPtS07", which says where each part starts) as the last bytes. The first header
block is LZMA-compressed and holds the entries, among them [Run]; the edits apply to
it after decompression.

The copy is every byte of the setup before the offset table, then a new setup.0 (the
same id, the edited first block stored without compression, which Inno supports, and
the second block unchanged), then a new offset table pointing at it, with the pointer
at 0x30 updated. The old setup.0 stays in place unused, and nothing else moves.

Anything not recognised is refused, and then no file is left behind: a setup without the
pinned source_sha256, a pointer, offset table id or CRC, setup.0 id, block or chunk CRC
that does not match the layout above, a header without header_sha256, an edit whose
`before` bytes differ, or a result without sha256_after. OUTPUT must not exist; it is
written to a temporary file in its folder and linked into place only once its hash is
right, so it never replaces a file, the download included.

    python -m trackmania_mac.install.setup_patch apply SETUP OUTPUT

The title is TRACKMANIA_MAC_TITLE. Exit status 1 on an error.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import lzma
import os
import pathlib
import stat
import struct
import sys
import tempfile
import typing
import zlib

from trackmania_mac.guard import managed_paths
from trackmania_mac.hub import registry

POINTER_OFFSET = 0x30
TABLE_ID = b"rDlPtS07\x87eVx"
TABLE_SIZE = 40
# setup.0 ids this module reads; each names the Inno Setup data version.
SETUP_IDS = (b"Inno Setup Setup Data (5.1.2)".ljust(64, b"\0"),)
CHUNK = 4096
MAX_HEADER = 64 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
TEMP_PREFIX = ".trackmania-mac-setup."
_BLOCK_HEADER = struct.Struct("<IIB")
_TABLE = struct.Struct("<12s7I")


class SetupPatchError(Exception):
    """The setup or its patch is not what this module recognises; nothing was written."""


class Layout(typing.NamedTuple):
    """Where the parts of a recognised setup are, and the bytes of setup.0."""

    size: int
    table_offset: int
    exe_offset: int
    exe_size: int
    exe_crc: int
    setup0_offset: int
    setup1_offset: int
    setup_id: bytes
    first_block: bytes  # the block as stored: its 9-byte header, then its chunks
    second_block: bytes


def _open_source(path: pathlib.Path) -> typing.BinaryIO:
    """A read-only handle on a regular, non-symlink file."""
    try:
        # O_NONBLOCK: a FIFO fails the regular-file check instead of waiting.
        descriptor = os.open(
            str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as error:
        raise SetupPatchError(f"Cannot open {path} (not a regular file?): {error}") from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise SetupPatchError(f"Not a regular file: {path}")
    return os.fdopen(descriptor, "rb")


def _read_exact(handle: typing.BinaryIO, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise SetupPatchError("The setup ends early")
    return data


def _file_sha256(handle: typing.BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    for chunk in iter(lambda: handle.read(COPY_CHUNK), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _read_block(handle: typing.BinaryIO, offset: int, end: int) -> bytes:
    """One setup.0 block at OFFSET, as stored, after checking its CRCs."""
    if offset + _BLOCK_HEADER.size > end:
        raise SetupPatchError("A setup header block runs past its part of the setup")
    header = _read_exact(handle, offset, _BLOCK_HEADER.size)
    crc, size, compressed = _BLOCK_HEADER.unpack(header)
    if zlib.crc32(header[4:]) != crc or compressed not in (0, 1):
        raise SetupPatchError("A setup header block has an unknown or damaged header")
    if size == 0 or offset + _BLOCK_HEADER.size + size > end:
        raise SetupPatchError("A setup header block runs past its part of the setup")
    chunks = _read_exact(handle, offset + _BLOCK_HEADER.size, size)
    _block_payload(chunks)
    return header + chunks


def _block_payload(chunks: bytes) -> bytes:
    """The bytes of a block's chunks without their CRCs, after checking each CRC."""
    payload = []
    for start in range(0, len(chunks), CHUNK + 4):
        piece = chunks[start : start + CHUNK + 4]
        if len(piece) < 5 or zlib.crc32(piece[4:]) != struct.unpack_from("<I", piece)[0]:
            raise SetupPatchError("A setup header chunk is damaged")
        payload.append(piece[4:])
    return b"".join(payload)


def read_layout(handle: typing.BinaryIO) -> Layout:
    """The layout of an Inno Setup 5.1 setup, or SetupPatchError."""
    size = os.fstat(handle.fileno()).st_size
    if size < POINTER_OFFSET + 12 + TABLE_SIZE:
        raise SetupPatchError("Too small to be an Inno Setup setup")
    magic, table_offset, complement = struct.unpack(
        "<4sII", _read_exact(handle, POINTER_OFFSET, 12)
    )
    if magic != b"Inno" or table_offset ^ complement != 0xFFFFFFFF:
        raise SetupPatchError("No Inno Setup offset table pointer at 0x30")
    if table_offset + TABLE_SIZE != size:
        raise SetupPatchError("The Inno Setup offset table is not at the end of the setup")
    table = _read_exact(handle, table_offset, TABLE_SIZE)
    table_id, total, exe_offset, exe_size, exe_crc, setup0, setup1, table_crc = _TABLE.unpack(table)
    if table_id != TABLE_ID:
        raise SetupPatchError(f"Unknown Inno Setup offset table id {table_id!r}")
    if zlib.crc32(table[:-4]) != table_crc:
        raise SetupPatchError("The Inno Setup offset table CRC does not match")
    if (
        total != size
        or not POINTER_OFFSET + 12 <= setup1 < min(setup0, exe_offset)
        or not max(setup0, exe_offset) < table_offset
        or setup0 == exe_offset
    ):
        raise SetupPatchError("The Inno Setup offset table does not match the setup")
    # setup.0 ends where the setup program starts, or, in a copy this module
    # wrote, where the offset table starts.
    end = exe_offset if setup0 < exe_offset else table_offset
    setup_id = _read_exact(handle, setup0, 64)
    if setup_id not in SETUP_IDS:
        raise SetupPatchError(f"Unknown Inno Setup data version {setup_id.rstrip(bytes(1))!r}")
    first = _read_block(handle, setup0 + 64, end)
    second = _read_block(handle, setup0 + 64 + len(first), end)
    if setup0 + 64 + len(first) + len(second) != end:
        raise SetupPatchError("The setup header does not end where the next part starts")
    return Layout(
        size, table_offset, exe_offset, exe_size, exe_crc, setup0, setup1, setup_id, first, second
    )


def decompress_header(block: bytes) -> bytes:
    """The decompressed bytes of a first header block as read_layout returns it."""
    payload = _block_payload(block[_BLOCK_HEADER.size :])
    if block[8] == 0:
        return payload
    if len(payload) < 5 or payload[0] >= 9 * 5 * 5:
        raise SetupPatchError("The setup header has unknown LZMA properties")
    properties = payload[0]
    lzma_filter = {
        "id": lzma.FILTER_LZMA1,
        "lc": properties % 9,
        "lp": properties // 9 % 5,
        "pb": properties // 45,
        "dict_size": struct.unpack_from("<I", payload, 1)[0],
    }
    try:
        decompressor = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[lzma_filter])
        plain = decompressor.decompress(payload[5:], max_length=MAX_HEADER)
    except lzma.LZMAError as error:
        raise SetupPatchError(f"The setup header does not decompress: {error}") from error
    if len(plain) >= MAX_HEADER:
        raise SetupPatchError("The setup header is too large")
    return plain


def stored_block(data: bytes) -> bytes:
    """DATA as an uncompressed setup.0 block."""
    chunks = b"".join(
        struct.pack("<I", zlib.crc32(data[start : start + CHUNK])) + data[start : start + CHUNK]
        for start in range(0, len(data), CHUNK)
    )
    tail = struct.pack("<IB", len(chunks), 0)
    return struct.pack("<I", zlib.crc32(tail)) + tail + chunks


def apply_edits(header: bytes, edits: typing.Sequence[registry.SetupEdit]) -> bytes:
    edited = bytearray(header)
    for edit in edits:
        end = edit.offset + len(edit.before)
        if end > len(edited) or bytes(edited[edit.offset : end]) != edit.before:
            raise SetupPatchError(
                f"The setup header does not hold {edit.before.hex()} at {edit.offset:#x}"
            )
        edited[edit.offset : end] = edit.after
    return bytes(edited)


def write_patched(
    source: pathlib.Path, output: pathlib.Path, source_sha256: str, patch: registry.SourcePatch
) -> None:
    """Write OUTPUT, the modified copy of SOURCE; SetupPatchError and nothing written otherwise."""
    if os.path.lexists(output):
        raise SetupPatchError(f"Refusing to replace {output}")
    folder = output.parent
    if folder.is_symlink() or not folder.is_dir():
        raise SetupPatchError(f"Not a folder: {folder}")
    with _open_source(source) as handle:
        if _file_sha256(handle) != source_sha256:
            raise SetupPatchError(f"{source} is not the pinned setup")
        layout = read_layout(handle)
        # An unmodified setup: setup.0 before the setup program, its entries compressed.
        if layout.setup0_offset > layout.exe_offset or layout.first_block[8] != 1:
            raise SetupPatchError("The setup header is not laid out as in an unmodified setup")
        header = decompress_header(layout.first_block)
        if hashlib.sha256(header).hexdigest() != patch.header_sha256:
            raise SetupPatchError("The setup header is not the one the patch is for")
        setup0 = layout.setup_id + stored_block(apply_edits(header, patch.edits))
        setup0 += layout.second_block
        new_table = layout.table_offset + len(setup0)
        table = struct.pack(
            "<12s6I",
            TABLE_ID,
            new_table + TABLE_SIZE,
            layout.exe_offset,
            layout.exe_size,
            layout.exe_crc,
            layout.table_offset,
            layout.setup1_offset,
        )
        table += struct.pack("<I", zlib.crc32(table))
        pointer = struct.pack("<4sII", b"Inno", new_table, new_table ^ 0xFFFFFFFF)
        descriptor, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(folder))
        try:
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as target:

                def put(data: bytes) -> None:
                    digest.update(data)
                    target.write(data)

                start = _read_exact(handle, 0, POINTER_OFFSET + len(pointer))
                put(start[:POINTER_OFFSET] + pointer)
                remaining = layout.table_offset - len(start)
                while remaining:
                    chunk = handle.read(min(COPY_CHUNK, remaining))
                    if not chunk:
                        raise SetupPatchError("The setup ends early")
                    put(chunk)
                    remaining -= len(chunk)
                put(setup0)
                put(table)
                if digest.hexdigest() != patch.sha256_after:
                    raise SetupPatchError("Patching the setup did not give the pinned result")
                target.flush()
                os.fsync(target.fileno())
            # link refuses an existing name, so the copy never replaces a file.
            os.link(temporary, str(output))
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def apply(source: str, output: str, title: registry.Title) -> None:
    if title.source_patch is None or not title.source_sha256:
        raise SetupPatchError(f"{title.name} has no setup patch")
    write_patched(
        pathlib.Path(source), pathlib.Path(output), title.source_sha256, title.source_patch
    )


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("apply")
    command.add_argument("source")
    command.add_argument("output")
    args = parser.parse_args(argv)
    try:
        apply(args.source, args.output, registry.get(managed_paths._current_title()))
    except (OSError, SetupPatchError, registry.RegistryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
