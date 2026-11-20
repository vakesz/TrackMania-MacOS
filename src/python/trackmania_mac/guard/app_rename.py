"""Rename one title's installed Game and Settings apps.

`python -m trackmania_mac.guard.app_rename rename ROOT GAME_APP SETTINGS_APP
(--name=NAME | --reset)` moves both wrappers to the names a base name gives
(trackmania_mac.app.names) and records it in ROOT's `app-name` file; --reset,
or a name equal to the registry's, goes back to the registry names and removes
the file. The shell runs it holding the title's prefix and app locks.

Only our own wrappers of this title move (the check uninstall uses), never onto
anything that exists, and never onto a name another title's apps have or would
have. A move keeps the bundle's inode, so the Dock usually follows it. Any
failure puts back what moved. On success stdout holds shell assignments of the
new paths and whether anything changed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shlex
import sys
import typing
from pathlib import Path

from trackmania_mac.app import names
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import registry

from . import managed_paths
from . import uninstall


class Result(typing.NamedTuple):
    game: Path
    settings: Path
    changed: bool


def _same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _taken(title: registry.Title, titles: typing.Sequence[registry.Title]) -> typing.Dict[str, str]:
    """{key: other title's name} for every app name another title has or would have."""
    taken: typing.Dict[str, str] = {}
    for other in titles:
        if other.id == title.id:
            continue
        found = {other.app_name, other.settings_app_name}
        current = names.effective(other, hub_paths.title_root(other.id))
        found |= {current.game, current.settings}
        for name in found:
            taken.setdefault(names.key(name), other.name)
    return taken


def _move(source: Path, target: Path) -> None:
    """Rename SOURCE to TARGET without ever replacing anything; a target that is
    SOURCE itself (a change of case on APFS) is the one exception."""
    if os.path.lexists(target):
        if not _same(source, target):
            raise FileExistsError(f"{target} already exists")
        os.rename(source, target)
    else:
        managed_paths.rename_exclusive(source, target)


def _write_name(root: Path, base: typing.Optional[str]) -> None:
    """Record BASE in ROOT's app-name file atomically, or remove the file for None."""
    path = root / names.FILE
    if base is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ".{}.{}".format(names.FILE, secrets.token_hex(4))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((base + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def rename(root: str, game_app: str, settings_app: str, name: typing.Optional[str]) -> Result:
    """Give the title's apps the names NAME gives (None: the registry's).

    Raises ValueError/OSError (or RegistryError) and changes nothing when a
    check fails; a failure while moving puts back what moved.
    """
    title, titles = uninstall.load_titles()
    root_path = Path(root)
    game = Path(game_app)
    settings = Path(settings_app)
    for value, kind in ((game_app, "game-app"), (settings_app, "settings-app")):
        entry = uninstall.app_entry(value, title, kind, root_path)
        if entry is None:
            raise ValueError(f"{value} is not installed. Install the apps first.")
        if entry.verb != "remove":
            raise ValueError(f"Refusing to rename {entry.path}: {entry.reason}")
    if not _same(game.parent, settings.parent):
        raise ValueError("The Game and Settings apps must be in the same folder")

    base = None if name is None else names.validate(name)
    if base is not None and names.key(base) == names.key(names.default_base(title)):
        base = None  # The registry's own name: the same as a reset.
    if base is None:
        new_names = (title.app_name, title.settings_app_name)
    else:
        new_names = names.file_names(base)
    targets = (game.parent / new_names[0], settings.parent / new_names[1])

    taken = _taken(title, titles)
    for target in targets:
        other = taken.get(names.key(target.name))
        if other is not None:
            raise ValueError(f"{other} uses the name {target.name}. Choose another name.")
    pairs = [(game, targets[0]), (settings, targets[1])]
    for source, target in pairs:
        if source.name != target.name and os.path.lexists(target) and not _same(source, target):
            raise ValueError(f"{target} already exists. Choose another name.")
    managed_paths.check_layout([str(target) for target in targets], apps=True)

    current = names.effective(title, root_path)
    recorded = current.base if current.custom else None
    file_current = recorded == base and current.problem is None
    if all(source.name == target.name for source, target in pairs) and file_current:
        return Result(targets[0], targets[1], False)

    moved: typing.List[typing.Tuple[Path, Path]] = []
    try:
        for source, target in pairs:
            if source.name == target.name:
                continue
            _move(source, target)
            moved.append((source, target))
        _write_name(root_path, base)
    except BaseException:
        for source, target in reversed(moved):
            try:
                _move(target, source)
            except OSError as error:
                print(f"warning: cannot move {target} back: {error}", file=sys.stderr)
        raise
    return Result(targets[0], targets[1], bool(moved))


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("rename",))
    parser.add_argument("root")
    parser.add_argument("game_app")
    parser.add_argument("settings_app")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--name")
    choice.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = rename(
            args.root, args.game_app, args.settings_app, None if args.reset else args.name
        )
    except (OSError, ValueError, RuntimeError, registry.RegistryError) as error:
        message = str(error)
        shown = message if uninstall._printable(message) else repr(message)
        print(f"error: {shown}", file=sys.stderr)
        return 1
    print(f"_trackmania_mac_renamed_app={shlex.quote(str(result.game))}")
    print(f"_trackmania_mac_renamed_settings_app={shlex.quote(str(result.settings))}")
    print(f"_trackmania_mac_renamed={int(result.changed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
