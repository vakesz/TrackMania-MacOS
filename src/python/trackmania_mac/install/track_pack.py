"""Unpack a title's pinned track pack into the saves folder its game folder links to.

A title's registry entry may name a zip of community tracks
(config/titles/*.json `track_pack`). Import offers to add it after the game's
save folders are linked (trackmania_mac.install.game_saves): the pack's `into`
folder in the game folder must be the link game_saves made, and the tracks are
written into the saves folder it leads to, never through any other symlink.

The archive is hashed and size-checked against the pin and read through one
descriptor. Only zip archives are read, and every entry must be unencrypted,
stored or deflated, and no symlink or special file (trackmania_mac.install.archives).
Only regular files under the pack's `root` folder whose names end with its
`suffix` (any case) are unpacked, at their path relative to `root`; the rest
are counted and skipped, as are names with a part that starts with a dot.

A file is never overwritten: a name the destination already has, in any case,
is skipped. A track whose own path cannot be written (a symlink, file or
case clash where one of its folders goes, something other than a plain file
of its name, a name the volume refuses) is skipped with a warning, so one odd
entry never stops the pack; the destination itself and the archive are still
checked strictly. Each file is written to a temporary name beside it, its size
checked, and then renamed into place with a rename that never replaces
anything. No symlink inside the destination is ever followed.
When every file is in, a marker holding the pack's SHA-256 is written in the
destination, and a later run with the marker present does nothing, so tracks
the player deleted do not come back. A run that stopped is finished by the next
one.

    python -m trackmania_mac.install.track_pack apply ARCHIVE GAME_DIR USERDATA
    python -m trackmania_mac.install.track_pack installed USERDATA

The title is TRACKMANIA_MAC_TITLE, read from the registry. `apply` changes files
and needs the prefix operation lock; it exits 0 when the pack is in, 1 on an
error. `installed` exits 0 when the marker holds this pack's SHA-256, 1 when
not, and changes nothing. Bad usage exits 2.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import re
import secrets
import stat
import sys
import typing
import zipfile
from pathlib import Path

from trackmania_mac import markers
from trackmania_mac.guard import managed_paths
from trackmania_mac.hub import registry
from trackmania_mac.install import archives, game_saves

# Unpacked bytes may be at most this many times the archive's own size.
MAX_UNPACK_RATIO = 16
MAX_ENTRIES = 100_000
TEMP_PREFIX = markers.TRACK_PACK + ".tmp."
_TEMP = re.compile(re.escape(TEMP_PREFIX) + r"[0-9a-f]{8}")
LABEL = "Track pack"
CHUNK = 1024 * 1024
# A marker holds a SHA-256 and a newline; more is never read.
MARKER_MAX = 128
# Errors that concern one entry's own name, not the destination.
_NAME_ERRORS = (errno.ENAMETOOLONG, errno.EILSEQ)


class Result(typing.NamedTuple):
    """Files written, files the destination already had, archive entries skipped
    (not tracks), and tracks whose own path could not be written (blocked)."""

    added: int
    present: int
    skipped: int
    blocked: int = 0


class _Blocked(Exception):
    """One entry's path cannot be written: the entry is skipped, not the pack."""


def _pack(title: registry.Title) -> registry.TrackPack:
    if title.track_pack is None:
        raise ValueError("{} has no track pack".format(title.name))
    return title.track_pack


def _saves_folder(userdata: str, title: registry.Title) -> typing.Optional[Path]:
    """The pack's folder in the saves folder, checked as real folders; None when missing."""
    saves = Path(userdata)
    if not saves.is_absolute():
        raise ValueError("The saves folder must be an absolute path")
    if saves.is_symlink() or not saves.is_dir():
        return None
    parts = [title.user_subdir] + _pack(title).into.split("/")
    try:
        return game_saves._folders(saves.resolve(strict=True), parts, False)
    except game_saves.Skipped as reason:
        raise ValueError(str(reason)) from None


def _marker_holds(folder: Path, expected: str) -> bool:
    """True when FOLDER's marker, a plain file, holds EXPECTED; never follows a symlink."""
    marker = folder / markers.TRACK_PACK
    try:
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("Not a plain file: {}".format(marker)) from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Not a plain file: {}".format(marker))
        data = os.read(descriptor, MARKER_MAX + 1)
    finally:
        os.close(descriptor)
    # A longer file is not one this module wrote: not a match, rewritten later.
    return len(data) <= MARKER_MAX and data.decode("ascii", "replace").strip() == expected


def installed(userdata: str, title: registry.Title) -> bool:
    """True when the saves folder's marker holds this pack's SHA-256."""
    folder = _saves_folder(userdata, title)
    return folder is not None and _marker_holds(folder, _pack(title).sha256)


def _destination(game_dir: str, userdata: str, title: registry.Title) -> Path:
    """The saves folder the game folder's `into` link leads to; that link only."""
    pack = _pack(title)
    try:
        layout = game_saves._layout(game_dir, userdata, title)
        folder = game_saves.Folder(layout, pack.into, create=False)
        if game_saves._state(folder) != "link":
            raise ValueError(
                "{} in the game folder is not linked to the saves folder".format(pack.into)
            )
        target = folder.target_folder(create=False)
    except game_saves.Skipped as reason:
        raise ValueError(str(reason)) from None
    if target is None:
        raise ValueError("The saves folder for {} is missing".format(pack.into))
    return target


def _selected(
    entries: typing.Iterable[archives.Entry], pack: registry.TrackPack, archive_size: int
) -> typing.Tuple[typing.List[typing.Tuple[str, archives.Entry]], int]:
    """The (path relative to root, entry) pairs to unpack, and the count skipped."""
    prefix = pack.root + "/"
    suffix = pack.suffix.casefold()
    chosen: typing.List[typing.Tuple[str, archives.Entry]] = []
    seen: typing.Set[str] = set()
    skipped = 0
    unpacked = 0
    for count, entry in enumerate(entries, start=1):
        if count > MAX_ENTRIES:
            raise ValueError("The track pack has more than {} entries".format(MAX_ENTRIES))
        name = archives.member_name(entry.name, "track pack")
        unpacked += 0 if entry.is_dir else entry.size
        if unpacked > MAX_UNPACK_RATIO * archive_size:
            raise ValueError(
                "The track pack unpacks to more than {} times its own size".format(MAX_UNPACK_RATIO)
            )
        if entry.is_dir:
            continue
        relative = name[len(prefix) :] if name.startswith(prefix) else ""
        folded = archives.fold(relative)
        if (
            not relative
            or not relative.casefold().endswith(suffix)
            or any(part.startswith(".") for part in relative.split("/"))
            or folded in seen
        ):
            skipped += 1
            continue
        seen.add(folded)
        chosen.append((relative, entry))
    return chosen, skipped


class _Writer:
    """Writes files under DEST without replacing anything or passing a symlink."""

    def __init__(self, dest: Path):
        self.dest = dest
        self.names: typing.Dict[Path, typing.Set[str]] = {}

    def _listed(self, folder: Path) -> typing.Set[str]:
        """The folded names in FOLDER; stale temporary files are removed first."""
        if folder not in self.names:
            names = set()
            for child in os.listdir(folder):
                path = folder / child
                if _TEMP.fullmatch(child):
                    info = os.lstat(path)
                    if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                        path.unlink()
                        continue
                names.add(archives.fold(child))
            self.names[folder] = names
        return self.names[folder]

    def _folder(self, parts: typing.Sequence[str]) -> Path:
        """DEST/PARTS as real folders, created as needed; _Blocked when one cannot be."""
        path = self.dest
        for part in parts:
            try:
                spelled = game_saves._spelled(path, part)
            except game_saves.Skipped as reason:
                raise _Blocked(str(reason)) from None
            path = path / spelled
            try:
                try:
                    info = os.lstat(path)
                except FileNotFoundError:
                    os.mkdir(path, archives.DIR_MODE)
                    self._listed(path.parent).add(archives.fold(spelled))
                    info = os.lstat(path)
            except OSError as error:
                if error.errno in _NAME_ERRORS:
                    raise _Blocked("{}: {}".format(path, error.strerror)) from None
                raise
            if not stat.S_ISDIR(info.st_mode):
                raise _Blocked("not a plain folder: {}".format(path))
        return path

    def write(self, relative: str, entry: archives.Entry) -> bool:
        """Write ENTRY at RELATIVE; False when a file of that name is already there.

        Raises _Blocked when the entry's own path cannot be written: a symlink,
        file or name clash where a folder goes, something other than a plain
        file of that name, or a name the volume refuses."""
        parts = relative.split("/")
        folder = self._folder(parts[:-1])
        names = self._listed(folder)
        if archives.fold(parts[-1]) in names:
            try:
                existing = folder / game_saves._spelled(folder, parts[-1])
            except game_saves.Skipped as reason:
                raise _Blocked(str(reason)) from None
            if not stat.S_ISREG(os.lstat(existing).st_mode):
                raise _Blocked("not a plain file: {}".format(existing))
            return False
        target = folder / parts[-1]
        temporary = folder / (TEMP_PREFIX + secrets.token_hex(4))
        try:
            source = entry.open()
            if source is None:
                raise ValueError("Cannot read track pack entry: {}".format(relative))
            with source, temporary.open("xb") as output:
                while True:
                    chunk = source.read(CHUNK)
                    if not chunk:
                        break
                    output.write(chunk)
                    if output.tell() > entry.size:
                        break
                if output.tell() != entry.size:
                    raise ValueError("Track pack entry has the wrong size: {}".format(relative))
            temporary.chmod(archives.FILE_MODE)
            try:
                game_saves._rename_new(temporary, target)
            except FileExistsError:
                names.add(archives.fold(parts[-1]))
                return False
            except OSError as error:
                if error.errno in _NAME_ERRORS:
                    raise _Blocked("{}: {}".format(target, error.strerror)) from None
                raise
        finally:
            if os.path.lexists(temporary):
                temporary.unlink()
        names.add(archives.fold(parts[-1]))
        return True


def _write_marker(dest: Path, sha256: str) -> None:
    marker = dest / markers.TRACK_PACK
    try:
        info = os.lstat(marker)
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise ValueError("Not a plain file: {}".format(marker))
    temporary = dest / (TEMP_PREFIX + secrets.token_hex(4))
    try:
        with temporary.open("x", encoding="ascii") as output:
            output.write(sha256 + "\n")
        temporary.chmod(archives.FILE_MODE)
        os.replace(temporary, marker)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def apply(
    archive: str,
    game_dir: str,
    userdata: str,
    title: registry.Title,
    report: typing.Callable[[str], None] = lambda line: None,
) -> typing.Optional[Result]:
    """Unpack the title's track pack from ARCHIVE; None when it was already added.

    A track whose own path cannot be written is reported and counted as blocked;
    a problem with the destination or the archive raises ValueError or OSError."""
    pack = _pack(title)
    dest = _destination(game_dir, userdata, title)
    if _marker_holds(dest, pack.sha256):
        return None
    with Path(archive).open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size != pack.size:
            raise ValueError("Track pack archive has the wrong size")
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            checksum.update(chunk)
        if checksum.hexdigest() != pack.sha256:
            raise ValueError("Track pack archive SHA-256 mismatch")
        stream.seek(0)
        if stream.read(len(archives.ZIP_MAGIC)) != archives.ZIP_MAGIC:
            raise ValueError("The track pack is not a zip archive")
        stream.seek(0)
        with zipfile.ZipFile(stream) as opened:
            chosen, skipped = _selected(archives.zip_entries(opened, LABEL), pack, size)
            writer = _Writer(dest)
            added = present = blocked = 0
            for relative, entry in chosen:
                try:
                    written = writer.write(relative, entry)
                except _Blocked as reason:
                    blocked += 1
                    report("{}: {}".format(relative, reason))
                    continue
                if written:
                    added += 1
                else:
                    present += 1
    _write_marker(dest, pack.sha256)
    return Result(added, present, skipped, blocked)


def _current_title() -> registry.Title:
    return registry.get(managed_paths._current_title())


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("apply")
    command.add_argument("archive")
    command.add_argument("game_dir")
    command.add_argument("userdata")
    command = commands.add_parser("installed")
    command.add_argument("userdata")
    args = parser.parse_args(argv)
    try:
        title = _current_title()
        if args.command == "installed":
            return 0 if installed(args.userdata, title) else 1
        result = apply(
            args.archive,
            args.game_dir,
            args.userdata,
            title,
            lambda line: print("warning: skipped {}".format(line), file=sys.stderr),
        )
    except (ValueError, OSError, zipfile.BadZipFile, registry.RegistryError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    if result is None:
        print("The track pack is already added.")
    else:
        print(
            "Added {} tracks ({} already there, {} that could not be written, "
            "{} other files skipped).".format(
                result.added, result.present, result.blocked, result.skipped
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
