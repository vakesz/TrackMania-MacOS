"""Unpack a pinned overlay archive over an installed game folder.

Some titles finish their install by laying a replacement build over the files
the official installers left (config/titles/*.json `overlay`). The archive is
a tar (any compression tarfile reads) or a zip, told apart by its first bytes.
It is hashed and read through one descriptor, and every header is checked
before anything is written: only regular files and folders (no symlinks, also
none recorded in a zip's Unix attributes), no absolute, `..` or colliding
paths, no Python files (a game folder must never be able to supply code to this
project's Python), no setuid, setgid or sticky bits, and a bounded unpacked size
and entry count. A zip entry must also be unencrypted and stored or deflated.

`--root` names the archive folder the game files are in: every entry must lie in
it, and paths are taken relative to it. `--skip` names a file (relative to the
root) that is checked like the others but never unpacked; each must be in the
archive. `--last` and `--remove` are relative to the root as well.

It is unpacked into a staging folder beside the game folder, the merge is
checked against what is already there (no symlinked folders, no folder where
a file goes), and the staged files are moved in. The `--last` file (the game
executable) moves last, so a verified executable means every file is in;
the files the overlay makes obsolete are removed after it.

    python -m trackmania_mac.install.overlay apply ARCHIVE SHA256 GAME_DIR \
        --last GAME_EXE [--remove NAME]... [--root FOLDER] [--skip PATH]...
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import os
import pathlib
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import typing
import zipfile

from trackmania_mac.install import archives

STAGING_PREFIX = ".trackmania-mac-overlay."
# Unpacked bytes may be at most this many times the archive's own size.
MAX_UNPACK_RATIO = 16
MAX_ENTRIES = 1_000_000
# Names Python could import or run from a folder on sys.path.
PYTHON_SUFFIXES = (".py", ".pyc", ".pyo", ".pth")
FILE_MODE = archives.FILE_MODE
DIR_MODE = archives.DIR_MODE
ZIP_MAGIC = archives.ZIP_MAGIC
ZIP_UNIX = archives.ZIP_UNIX
_Entry = archives.Entry
_zip_entries = archives.zip_entries
_member_name = archives.member_name
_fold = archives.fold


def _tar_entries(tar: tarfile.TarFile) -> typing.Iterator[_Entry]:
    for member in tar:
        if not (member.isreg() or member.isdir()):
            raise ValueError("Overlay entries must be files or folders: {}".format(member.name))
        yield _Entry(
            member.name,
            member.isdir(),
            member.size if member.isreg() else 0,
            member.mode,
            functools.partial(tar.extractfile, member),
        )


def _members(
    entries: typing.Iterable[_Entry],
    last: str,
    remove: typing.Sequence[str],
    archive_size: int,
    root: str = "",
    skip: typing.Sequence[str] = (),
) -> typing.Dict[str, _Entry]:
    """Every entry to unpack by its checked name relative to root, in archive order."""
    checked: typing.Dict[str, _Entry] = {}
    spellings: typing.Dict[str, str] = {}
    unpacked = 0
    for count, entry in enumerate(entries, start=1):
        if count > MAX_ENTRIES:
            raise ValueError("The overlay has more than {} entries".format(MAX_ENTRIES))
        name = _member_name(entry.name)
        if name.lower().endswith(PYTHON_SUFFIXES):
            raise ValueError("Overlay entries must not be Python files: {}".format(name))
        unpacked += 0 if entry.is_dir else entry.size
        if unpacked > MAX_UNPACK_RATIO * archive_size:
            raise ValueError(
                "The overlay unpacks to more than {} times its own size".format(MAX_UNPACK_RATIO)
            )
        if entry.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise ValueError("Unsafe overlay permissions: {}".format(name))
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            if spellings.setdefault(_fold(prefix), prefix) != prefix:
                raise ValueError("Colliding overlay paths: {}".format(name))
        if name in checked:
            raise ValueError("Duplicate overlay entry: {}".format(name))
        checked[name] = entry
    members: typing.Dict[str, _Entry] = {}
    for name, entry in checked.items():
        if root and name == root:
            if not entry.is_dir:
                raise ValueError("The overlay folder {} is a file".format(root))
            continue
        if root and not name.startswith(root + "/"):
            raise ValueError("Overlay entry outside {}: {}".format(root, name))
        members[name[len(root) + 1 :] if root else name] = entry
    for name in members:
        for parent in pathlib.PurePosixPath(name).parents:
            ancestor = members.get(parent.as_posix())
            if ancestor is not None and not ancestor.is_dir:
                raise ValueError("Overlay writes through a file: {}".format(name))
    for name in skip:
        skipped = members.get(name)
        if skipped is None or skipped.is_dir:
            raise ValueError("The overlay does not contain {}, which it should skip".format(name))
        del members[name]
    final = members.get(last)
    if final is None or final.is_dir:
        raise ValueError("The overlay does not contain {}".format(last))
    folded = {_fold(name) for name in members}
    for name in remove:
        if _fold(name) in folded:
            raise ValueError("The overlay itself contains {}, which it should remove".format(name))
    return members


def _check_target(dest: pathlib.Path, name: str, is_dir: bool) -> None:
    """The merge may not pass through a symlink or replace a folder with a file."""
    path = dest
    for part in name.split("/")[:-1]:
        path = path / part
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Not a plain folder in the game folder: {}".format(path))
    target = dest / name
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return
    if is_dir and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Not a plain folder in the game folder: {}".format(target))
    if not is_dir and not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ValueError("A folder or special file is in the way: {}".format(target))


def _remove(dest: pathlib.Path, name: str) -> None:
    path = dest / name
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        raise ValueError("Refusing to remove a folder: {}".format(path))
    path.unlink()


def _sweep_staging(parent: pathlib.Path) -> None:
    """Remove staging folders a killed run left; the prefix lock rules out a live one."""
    pattern = re.compile(re.escape(STAGING_PREFIX) + r"[A-Za-z0-9_]+")
    for child in os.listdir(parent):
        path = parent / child
        if not pattern.fullmatch(child):
            continue
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            shutil.rmtree(path)


def apply(
    archive: str,
    expected: str,
    game_dir: str,
    last: str,
    remove: typing.Sequence[str] = (),
    root: str = "",
    skip: typing.Sequence[str] = (),
) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Invalid overlay SHA-256")
    last = _member_name(last)
    root = _member_name(root) if root else ""
    skip = [_member_name(name) for name in skip]
    if last in skip:
        raise ValueError("The overlay cannot skip {}".format(last))
    for name in remove:
        if "/" in name or _member_name(name) != name:
            raise ValueError("Unsafe file to remove: {!r}".format(name))
    dest = pathlib.Path(game_dir)
    info = os.lstat(dest)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("The game folder is not a plain folder: {}".format(dest))
    with pathlib.Path(archive).open("rb") as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
        if checksum.hexdigest() != expected:
            raise ValueError("Overlay archive SHA-256 mismatch")
        archive_size = os.fstat(stream.fileno()).st_size
        stream.seek(0)
        is_zip = stream.read(len(ZIP_MAGIC)) == ZIP_MAGIC
        stream.seek(0)
        with contextlib.ExitStack() as stack:
            if is_zip:
                entries = _zip_entries(stack.enter_context(zipfile.ZipFile(stream)))
            else:
                entries = _tar_entries(
                    stack.enter_context(tarfile.open(fileobj=stream, mode="r:*"))
                )
            members = _members(entries, last, remove, archive_size, root, skip)
            _merge(dest, members, last, remove)


def _merge(
    dest: pathlib.Path, members: typing.Dict[str, _Entry], last: str, remove: typing.Sequence[str]
) -> None:
    """Check the merge against the game folder, stage every file, then move them in."""
    for name, entry in members.items():
        _check_target(dest, name, entry.is_dir)
    # Removal runs after the merge, so refuse a folder before anything moves.
    for name in remove:
        if (dest / name).is_dir() and not (dest / name).is_symlink():
            raise ValueError("Refusing to remove a folder: {}".format(dest / name))
    _sweep_staging(dest.parent)
    staging = pathlib.Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=str(dest.parent)))
    try:
        for name, entry in members.items():
            path = staging / name
            if entry.is_dir:
                path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
                continue
            path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            source = entry.open()
            if source is None:  # Only non-regular members have no data.
                raise ValueError("Cannot read overlay entry: {}".format(name))
            with source, path.open("xb") as target:
                shutil.copyfileobj(source, target)
                if target.tell() != entry.size:
                    raise ValueError("Overlay entry has the wrong size: {}".format(name))
            path.chmod(FILE_MODE)
        files = [name for name, entry in members.items() if not entry.is_dir]
        files.sort(key=lambda name: name == last)
        for name, entry in members.items():
            if entry.is_dir:
                (dest / name).mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        for name in files:
            (dest / name).parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            os.replace(staging / name, dest / name)
        for name in remove:
            _remove(dest, name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Unpack a pinned overlay over a game folder.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("apply")
    command.add_argument("archive")
    command.add_argument("sha256")
    command.add_argument("game_dir")
    command.add_argument("--last", required=True)
    command.add_argument("--remove", action="append", default=[])
    command.add_argument("--root", default="")
    command.add_argument("--skip", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        apply(
            args.archive, args.sha256, args.game_dir, args.last, args.remove, args.root, args.skip
        )
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile, UnicodeError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
