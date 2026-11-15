"""Keep the save folders a title writes inside its game folder in its saves folder.

Some titles write profiles, replays and tracks under their game folder in the
Wine prefix instead of under Windows Documents (config/titles/*.json
`game_save_dirs`), so removing the prefix would delete them. Each listed folder
lives at the same relative path in the title's saves folder,
USERDATA/<user_subdir>/<path>, and the game folder holds an absolute symlink to
it.

    python -m trackmania_mac.install.game_saves link GAME_DIR USERDATA
    python -m trackmania_mac.install.game_saves check GAME_DIR USERDATA

The title is TRACKMANIA_MAC_TITLE, read from the registry. `link` changes files
and needs the prefix operation lock; for each listed folder it acts on what the
game folder holds there:

- nothing: the saves folder is created and linked;
- a link to the saves folder: the saves folder is created again if it is gone;
- another link: replaced only when it leads nowhere and the saves folder
  exists, otherwise kept with a warning;
- a real folder: each entry in it moves into the saves folder with a rename
  that never replaces anything. An entry the saves folder already has stays
  there, and the game folder's copy moves under a free name to
  <user_subdir>/Recovered from game folder/<path>. The emptied folder is then
  replaced by the link. A run that stops halfway is finished by the next one.
  A saves folder on another volume is skipped with a warning (v1: no copies);
- a file or anything else: kept with a warning.

Every folder on the way, in the game folder and in the saves folder, must be a
real folder: a symlink there is refused. Existing names are reused whatever
their case; a folder holding several entries whose names differ only in case
(a case-sensitive volume) is refused. `check` only reads: it warns about each
listed folder that `link` would still change or warn about, including a link
whose saves folder is missing or not a real folder.

`saves_found` serves an uninstall that keeps saves: the listed folders that
removing a prefix would delete with files in them, under every spelling of
each name and through symlinks that resolve inside the prefix.

Both print one `warning:` line per folder they leave as it is and then exit 1;
they exit 0 when every folder is (or already was) linked, and 2 on bad usage.
"""

from __future__ import annotations

import argparse
import errno
import os
import secrets
import stat
import sys
import typing
import unicodedata
from pathlib import Path

from trackmania_mac.guard import managed_paths
from trackmania_mac.hub import registry

RECOVERED = "Recovered from game folder"
LINK_PREFIX = ".trackmania-mac-link."
# The uninstall check walks at most this many entries of a save folder.
MAX_ENTRIES = 200_000
# Free names tried for a recovered copy before giving up.
MAX_SUFFIX = 1000
WINDOWS_GAME_DIR = ("drive_c", "Program Files (x86)")


class Skipped(Exception):
    """A save folder left as it is, with the reason."""


def _fold(name: str) -> str:
    # macOS compares names case- and normalization-insensitively.
    return unicodedata.normalize("NFD", name).casefold()


def _matches(folder: Path, name: str) -> typing.List[str]:
    """Every entry of FOLDER named NAME ignoring case (more than one only on a
    case-sensitive volume), exact spelling first."""
    try:
        children = os.listdir(folder)
    except OSError:
        return []
    wanted = _fold(name)
    found = sorted(child for child in children if _fold(child) == wanted)
    return sorted(found, key=lambda child: child != name)


def _spelled(folder: Path, name: str) -> str:
    """NAME as FOLDER already spells it, ignoring case; NAME when it is absent.

    Refuses (Skipped) a name that several entries match in different cases:
    which one the game uses is unknown, so none is linked or moved.
    """
    found = _matches(folder, name)
    if len(found) > 1:
        raise Skipped(
            "{} holds {} entries named {} in different cases ({}); keep one".format(
                folder, len(found), name, ", ".join(found)
            )
        )
    return found[0] if found else name


def _folders(base: Path, parts: typing.Sequence[str], create: bool) -> typing.Optional[Path]:
    """BASE/PARTS as real folders, spelled as found, created when CREATE.

    Returns None for a missing folder when not creating. A symlink, file or
    anything else on the way is refused.
    """
    path = base
    for part in parts:
        path = path / _spelled(path, part)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if not create:
                return None
            os.mkdir(path)
            info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            raise Skipped("{} is a symlink".format(path))
        if not stat.S_ISDIR(info.st_mode):
            raise Skipped("{} is not a folder".format(path))
    return path


def _rename_new(source: Path, target: Path) -> None:
    """Rename SOURCE to TARGET; never replace anything at TARGET."""
    try:
        managed_paths.rename_exclusive(source, target)
    except OSError as error:
        if error.errno not in (errno.ENOTSUP, errno.EINVAL):
            raise
        # A volume without exclusive renames. The prefix lock keeps the game
        # from creating TARGET between this check and the rename.
        if os.path.lexists(target):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target)) from None
        os.rename(source, target)


def _numbered(name: str, number: int) -> str:
    """NAME with " (NUMBER)" before its extensions: "Race.Replay.Gbx" -> "Race (2).Replay.Gbx"."""
    dot = name.find(".", 1)
    if dot == -1:
        return "{} ({})".format(name, number)
    return "{} ({}){}".format(name[:dot], number, name[dot:])


def _recover(source: Path, folder: Path) -> Path:
    """Move SOURCE into FOLDER under its own name or the first free numbered one."""
    for number in range(1, MAX_SUFFIX + 1):
        name = source.name if number == 1 else _numbered(source.name, number)
        target = folder / name
        if os.path.lexists(folder / _spelled(folder, name)):
            continue
        try:
            _rename_new(source, target)
        except FileExistsError:
            continue
        return target
    raise Skipped("no free name for {} in {}".format(source.name, folder))


class Layout(typing.NamedTuple):
    title: registry.Title
    game_dir: Path
    saves: Path  # the resolved saves folder (USERDATA)


def _layout(game_dir: str, userdata: str, title: registry.Title) -> Layout:
    game = Path(game_dir)
    saves = Path(userdata)
    if not game.is_absolute() or not saves.is_absolute():
        raise ValueError("The game and saves folders must be absolute paths")
    info = os.lstat(game)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("Not a game folder: {}".format(game))
    if saves.is_symlink() or not saves.is_dir():
        raise ValueError("The saves folder is missing or not a folder: {}".format(saves))
    return Layout(title, game, saves.resolve(strict=True))


class Folder:
    """One listed save folder: where the game finds it and where it is kept."""

    def __init__(self, layout: Layout, relative: str, create: bool):
        self.layout = layout
        parts = relative.split("/")
        self.leaf = parts[-1]
        self.parts = parts
        parent = _folders(layout.game_dir, parts[:-1], create)
        self.link = None if parent is None else parent / _spelled(parent, self.leaf)
        kept = [layout.title.user_subdir] + parts[:-1]
        target_parent = _folders(layout.saves, kept, False)
        if target_parent is None:
            self.target = layout.saves.joinpath(*kept, self.leaf)
        else:
            self.target = target_parent / _spelled(target_parent, self.leaf)

    def target_folder(self, create: bool) -> typing.Optional[Path]:
        """The saves folder for it, checked (and created when CREATE)."""
        kept = [self.layout.title.user_subdir] + self.parts
        found = _folders(self.layout.saves, kept, create)
        if found is not None:
            self.target = found
        return found

    def points_home(self) -> bool:
        """True when the game folder's link already leads to the saves folder."""
        assert self.link is not None
        if os.readlink(self.link) == str(self.target):
            return True
        try:
            return os.path.samefile(self.link, self.target)
        except OSError:
            return False


def _state(folder: Folder) -> str:
    """missing, link, foreign, folder or other: what the game folder holds."""
    if folder.link is None:
        return "missing"
    try:
        info = os.lstat(folder.link)
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "link" if folder.points_home() else "foreign"
    if stat.S_ISDIR(info.st_mode):
        return "folder"
    return "other"


def _replace_link(link: Path, target: Path) -> None:
    """Point the symlink LINK at TARGET in one rename."""
    stale = "{}{}.".format(LINK_PREFIX, link.name)
    for child in os.listdir(link.parent):
        # Temporary links a killed run left; the prefix lock rules out a live one.
        if child.startswith(stale) and (link.parent / child).is_symlink():
            os.unlink(link.parent / child)
    temporary = link.parent / "{}{}".format(stale, secrets.token_hex(4))
    os.symlink(str(target), temporary)
    try:
        os.rename(temporary, link)
    except BaseException:
        os.unlink(temporary)
        raise


def _move_into(folder: Folder, target: Path, report: typing.Callable[[str], None]) -> None:
    """Move every entry of the game folder's real folder into TARGET."""
    assert folder.link is not None
    source = folder.link
    if os.lstat(source).st_dev != os.lstat(target).st_dev:
        raise Skipped(
            "the saves folder {} is on another volume; move the files in {} there yourself".format(
                target, source
            )
        )
    recovered: typing.Optional[Path] = None
    moved = 0
    present = {_fold(name) for name in os.listdir(target)}
    for name in sorted(os.listdir(source)):
        entry = source / name
        if _fold(name) not in present:
            present.add(_fold(name))
            try:
                _rename_new(entry, target / name)
                moved += 1
                continue
            except FileExistsError:
                pass
        if recovered is None:
            kept = [folder.layout.title.user_subdir, RECOVERED] + folder.parts
            recovered = _folders(folder.layout.saves, kept, True)
            assert recovered is not None
        copy = _recover(entry, recovered)
        report("{} already has {}; the game folder's copy is now {}".format(target, name, copy))
    if moved:
        report("Moved the saves in {} to {}".format(source, target))
    try:
        os.rmdir(source)
    except OSError as error:
        if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
            raise Skipped("new files appeared in {} while it was moved".format(source)) from None
        raise


def link_folder(folder: Folder, report: typing.Callable[[str], None]) -> None:
    """Make the game folder's entry a link to the saves folder; Skipped when it cannot."""
    state = _state(folder)
    if state == "link":
        folder.target_folder(create=True)
        return
    if state == "foreign":
        assert folder.link is not None
        destination = os.readlink(folder.link)
        if os.path.exists(folder.link):
            raise Skipped("{} links to {}; it is left as it is".format(folder.link, destination))
        target = folder.target_folder(create=False)
        if target is None:
            raise Skipped(
                "{} links to the missing {}, and {} does not exist; it is left as it is".format(
                    folder.link, destination, folder.target
                )
            )
        _replace_link(folder.link, target)
        return
    if state == "other":
        raise Skipped("{} is not a folder; it is left as it is".format(folder.link))
    target = folder.target_folder(create=True)
    assert target is not None
    # Linking creates the game folder's parents (Folder with create=True).
    assert folder.link is not None
    if state == "folder":
        _move_into(folder, target, report)
    os.symlink(str(target), folder.link)


def check_folder(folder: Folder) -> None:
    """Skipped, with what to expect, unless the entry already links to the saves folder."""
    state = _state(folder)
    if state == "link":
        # The saves end must pass the same checks link applies to it.
        if folder.target_folder(create=False) is None:
            raise Skipped(
                "{} links to the missing {}; Play creates it".format(folder.link, folder.target)
            )
        return
    where = folder.link or folder.layout.game_dir.joinpath(*folder.parts)
    if state == "missing":
        raise Skipped("{} is not linked to {} yet; Play links it".format(where, folder.target))
    if state == "folder":
        raise Skipped(
            "saves are in the game folder at {}; Play moves them to {}".format(where, folder.target)
        )
    if state == "foreign":
        raise Skipped("{} links to {}, not to {}".format(where, os.readlink(where), folder.target))
    raise Skipped("{} is not a folder".format(where))


def run(
    command: str,
    game_dir: str,
    userdata: str,
    title: registry.Title,
    report: typing.Callable[[str], None] = print,
) -> typing.List[str]:
    """Link or check every listed folder; returns one warning per folder left as it is."""
    if not title.game_save_dirs:
        return []
    layout = _layout(game_dir, userdata, title)
    warnings = []
    for relative in title.game_save_dirs:
        try:
            folder = Folder(layout, relative, create=command == "link")
            if command == "link":
                link_folder(folder, report)
            else:
                check_folder(folder)
        except Skipped as reason:
            warnings.append("{}: {}".format(relative, reason))
        except OSError as error:
            warnings.append("{}: {}".format(relative, error))
    return warnings


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _reached(path: Path, root: Path, parts: typing.Sequence[str]) -> typing.Iterator[Path]:
    """Every folder inside ROOT that PATH/PARTS names, whatever the case of each part.

    A symlink is followed when it resolves inside ROOT, because its target goes
    with ROOT; one that resolves outside it (the link to the saves folder) or
    nowhere is not, because removing ROOT leaves its target alone.
    """
    if not parts:
        yield path
        return
    for name in _matches(path, parts[0]):
        child = path / name
        info = os.lstat(child)
        if stat.S_ISLNK(info.st_mode):
            try:
                child = child.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not _inside(child, root) or not child.is_dir():
                continue
        elif not stat.S_ISDIR(info.st_mode):
            continue
        yield from _reached(child, root, parts[1:])


def saves_found(prefix: Path, title: registry.Title) -> typing.List[Path]:
    """Every listed save folder that removing PREFIX would delete with files in it.

    Read-only, for an uninstall that keeps saves. PREFIX is a resolved folder
    (the live prefix or an uninstall's tombstone of it). Each name is matched
    in every case, and a symlink on the way is followed when it leads to
    another folder inside PREFIX.
    """
    root = prefix.resolve(strict=True)
    found: typing.List[Path] = []
    for relative in title.game_save_dirs:
        parts = WINDOWS_GAME_DIR + (title.game_dir_name,) + tuple(relative.split("/"))
        for path in _reached(root, root, parts):
            if path not in found and _holds_files(path):
                found.append(path)
    return found


def saves_left(prefix: Path, title: registry.Title) -> typing.Optional[Path]:
    """The first folder saves_found reports, or None."""
    found = saves_found(prefix, title)
    return found[0] if found else None


def _holds_files(folder: Path) -> bool:
    """True when anything but folders lies inside FOLDER, at any depth."""
    pending = [folder]
    seen = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES:
                    raise RuntimeError(
                        "The save folder {} is too large to check (>{} entries); move it "
                        "yourself before uninstalling".format(folder, MAX_ENTRIES)
                    )
                if not entry.is_dir(follow_symlinks=False):
                    return True
                pending.append(Path(entry.path))
    return False


def check_prefix(prefix: Path, title: registry.Title) -> None:
    """Raise ValueError when removing PREFIX would delete saves left in its game folder."""
    left = saves_left(prefix, title)
    if left is not None:
        raise ValueError(
            "Saves are still in the game folder at {}, which the uninstall would remove. "
            "Run the uninstall again once they are in the saves folder (see the warnings "
            "above), or uninstall with --include-saves to remove them too.".format(left)
        )


def _current_title() -> registry.Title:
    return registry.get(managed_paths._current_title())


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("link", "check"))
    parser.add_argument("game_dir")
    parser.add_argument("userdata")
    args = parser.parse_args(argv)
    try:
        warnings = run(args.command, args.game_dir, args.userdata, _current_title())
    except (OSError, ValueError, registry.RegistryError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    for warning in warnings:
        print("warning: {}".format(warning), file=sys.stderr)
    return 1 if warnings else 0


if __name__ == "__main__":
    sys.exit(main())
