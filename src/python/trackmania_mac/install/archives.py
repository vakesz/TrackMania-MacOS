"""Checks shared by the modules that unpack a pinned archive (overlay, track_pack).

`zip_entries` reads a zip's headers and refuses what neither module ever
unpacks: encrypted entries, compression other than stored or deflated, and
symlinks or special files recorded in an entry's Unix attributes.
`member_name` refuses absolute, `..`, backslash and control-character paths.
`label` starts each error message, so each module keeps its own wording.
"""

from __future__ import annotations

import functools
import stat
import typing
import unicodedata
import zipfile

FILE_MODE = 0o644
DIR_MODE = 0o755
ZIP_MAGIC = b"PK\x03\x04"
# Zip compression methods accepted.
ZIP_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# ZipInfo.create_system of an entry whose external attributes hold a Unix mode.
ZIP_UNIX = 3


class Entry(typing.NamedTuple):
    """One archive entry, whatever the format: its raw name, kind, size and mode."""

    name: str
    is_dir: bool
    size: int
    mode: int
    open: typing.Callable[[], typing.Optional[typing.IO[bytes]]]


def zip_entries(archive: zipfile.ZipFile, label: str = "Overlay") -> typing.Iterator[Entry]:
    for info in archive.infolist():
        name = info.filename
        is_dir = name.endswith("/")
        if info.flag_bits & 0x1:
            raise ValueError("{} entries must not be encrypted: {}".format(label, name))
        if info.compress_type not in ZIP_METHODS:
            raise ValueError("Unsupported {} compression: {}".format(label.lower(), name))
        mode = (DIR_MODE if is_dir else FILE_MODE) | (stat.S_IFDIR if is_dir else stat.S_IFREG)
        if info.create_system == ZIP_UNIX and info.external_attr >> 16:
            mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError("{} entries must be files or folders: {}".format(label, name))
        if kind and stat.S_ISDIR(mode) != is_dir:
            raise ValueError("{} entries must be files or folders: {}".format(label, name))
        yield Entry(
            name,
            is_dir,
            0 if is_dir else info.file_size,
            mode,
            functools.partial(archive.open, info),
        )


def member_name(name: str, label: str = "overlay") -> str:
    """A relative archive path with no traversal, backslash or control bytes."""
    name = name[:-1] if name.endswith("/") else name
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        raise ValueError("Unsafe {} path: {!r}".format(label, name))
    return name


def fold(name: str) -> str:
    # macOS compares names case- and normalization-insensitively.
    return unicodedata.normalize("NFD", name).casefold()
