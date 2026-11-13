"""Move an install from the folders of earlier releases into the shared data folder.

Earlier releases kept each title's prefix and saves in
~/Games/<legacy_prefix_subdir> (from the title registry), the runtime,
launchers and icons in ~/Library/Application Support/TMNF, downloads in
~/Library/Caches/TMNF and logs in ~/Library/Logs/TMNF. This module is the only
place that knows those folders; everything now lives in ~/Library/TrackManiaMacOS
(trackmania_mac.hub.paths).

Rules that keep saves safe:
- Folders are only ever renamed, whole, with renamex_np(RENAME_EXCL). Nothing is
  copied, opened for writing, truncated or deleted inside a saves folder, and no
  rename can replace an existing path. At every instant each save exists under
  exactly one name, old or new.
- Everything is checked before the first rename (same volume, free targets, no
  running game, our ownership markers); a failed check changes nothing.
- A journal (.migration.json in the data folder) records every planned rename
  with the folder's (device, inode) before the first one. A failure renames
  everything back; a crash leaves the journal, and the next run resumes by
  identity, never acting on a folder it did not record.

Locations given in TRACKMANIA_MAC_SUPPORT or TRACKMANIA_MAC_CACHE are the user's and stay where they
are; so does a prefix without our ownership marker.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import typing
from pathlib import Path

from trackmania_mac import legacy
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import registry

from . import managed_paths
from . import operation_guard
from . import uninstall

JOURNAL = ".migration.json"
JOURNAL_VERSION = 1
LEGACY_SUPPORT = ("Library", "Application Support", "TMNF")
LEGACY_CACHE = ("Library", "Caches", "TMNF")
LEGACY_LOGS = ("Library", "Logs", "TMNF")
LEGACY_GAMES = "Games"
# What earlier releases kept in the support folder (launcher: the shared
# launcher of the first releases, which install_app later removes).
SUPPORT_CHILDREN = ("runtime", "launchers", "icons", "launcher")
# Files we may delete from an emptied old folder: Finder's metadata and our lock.
DISPOSABLE = (
    ".DS_Store",
    operation_guard.operation_lock_name("prefix"),
    operation_guard.operation_lock_name("prefix", legacy.OPERATION_LOCK_SUFFIX),
)
LEGACY_APP_LOCK = ".app-install.lock"
# `pending` exit status when nothing is left to move.
NOTHING_TO_MOVE = 3
BUSY = "A game or another TrackMania task is running. Quit it, then try again."

KINDS = ("prefix", "saves", "leftover", "support", "cache", "logs")

Identity = typing.Tuple[int, int]


class Blocked(ValueError):
    """The migration cannot start; nothing was moved."""


class Busy(Blocked):
    """A game or another operation holds a prefix or the app lock."""


class Move(typing.NamedTuple):
    kind: str  # prefix, saves, leftover, support, cache, logs
    source: Path  # resolved: the folder renamed
    target: Path
    nominal: Path  # the default spelling earlier releases recorded
    title: typing.Optional[str] = None
    identity: typing.Optional[Identity] = None

    def record(self) -> dict:
        return {
            "kind": self.kind,
            "source": str(self.source),
            "target": str(self.target),
            "nominal": str(self.nominal),
            "title": self.title,
            "identity": list(self.identity) if self.identity else None,
        }


class Plan(typing.NamedTuple):
    data: Path
    moves: typing.List[Move]
    kept: typing.List[str]
    blocked: typing.List[str]
    prefixes: typing.List[Path]  # existing old prefixes: locked, never active
    roots: typing.List[typing.Tuple[str, Path, Path]]  # (title id, nominal, resolved) old roots
    support: typing.Optional[typing.Tuple[Path, Path]]
    cache: typing.Optional[typing.Tuple[Path, Path]]
    logs: typing.Optional[typing.Tuple[Path, Path]]


def _identity(path: Path) -> Identity:
    info = os.lstat(path)
    return (info.st_dev, info.st_ino)


def _device(path: Path) -> int:
    """Device of PATH, or of its nearest existing ancestor (the folder it would join)."""
    for candidate in (path, *path.parents):
        try:
            return os.stat(candidate).st_dev
        except FileNotFoundError:
            continue
    raise Blocked("Cannot find a folder for {}".format(path))


def _check_text(path: Path) -> None:
    if not uninstall._printable(str(path)):
        raise Blocked("A folder name contains control characters: {!r}".format(str(path)))


def _old_folder(nominal: Path) -> typing.Optional[Path]:
    """The resolved old folder NOMINAL names, or None when there is none.

    A symlinked old folder is followed, but it must lead to a real folder
    under the home folder, as every managed path must.
    """
    _check_text(nominal)
    if not os.path.lexists(nominal):
        return None
    try:
        resolved = nominal.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise Blocked(
            "{} is a link to a folder that no longer exists ({}). Reconnect the disk or "
            "folder it points to, then try again.".format(nominal, error)
        ) from error
    _check_text(resolved)
    if not resolved.is_dir():
        raise Blocked(
            "{} is not a folder. Move it out of the way, for example to your Desktop, then "
            "try again.".format(nominal)
        )
    try:
        managed_paths.safe_path(str(resolved))
    except ValueError as error:
        raise Blocked(str(error)) from error
    return resolved


def _empty_dir(path: Path) -> bool:
    return not path.is_symlink() and path.is_dir() and not any(path.iterdir())


def _holds_nothing(path: Path) -> bool:
    """A folder with nothing in it but Finder metadata: nothing to move."""
    return not path.is_symlink() and all(name == ".DS_Store" for name in os.listdir(path))


COUNT_LIMIT = 10_000


def _describe(path: Path) -> str:
    """How much PATH holds, so a message can say which folder has the data."""
    count = 0
    for _root, _directories, files in os.walk(path):
        count += sum(1 for name in files if name != ".DS_Store")
        if count >= COUNT_LIMIT:
            return "more than {} files".format(COUNT_LIMIT)
    return "no files" if count == 0 else "1 file" if count == 1 else "{} files".format(count)


class _Planner:
    def __init__(self, home: Path, environ: typing.Mapping[str, str], deep: bool):
        self.home = home
        self.environ = environ
        self.deep = deep
        self.data = hub_paths.data_dir(home)
        self.moves: typing.List[Move] = []
        self.kept: typing.List[str] = []
        self.blocked: typing.List[str] = []
        self.prefixes: typing.List[Path] = []
        self.roots: typing.List[typing.Tuple[str, Path, Path]] = []
        self.support: typing.Optional[typing.Tuple[Path, Path]] = None
        self.cache: typing.Optional[typing.Tuple[Path, Path]] = None
        self.logs: typing.Optional[typing.Tuple[Path, Path]] = None

    def add(self, kind: str, source: Path, target: Path, nominal: Path, title=None) -> None:
        """One whole-folder rename; the target must be free (or an empty folder)."""
        _check_text(source)
        _check_text(target)
        info = os.lstat(source)
        if stat.S_ISLNK(info.st_mode):
            raise Blocked("{} is a link; move the folder it points to yourself".format(source))
        if info.st_uid != os.getuid():
            raise Blocked("{} belongs to another user".format(source))
        if info.st_dev != os.lstat(source.parent).st_dev:
            raise Blocked("{} {}".format(source, uninstall.MOUNTED))
        if info.st_dev != _device(target.parent):
            raise Blocked(
                "{} is on a different disk than {}. TrackMania Mac moves folders but never "
                "copies them: move {} to {} yourself in Finder, then try again.".format(
                    source, self.data, source, target
                )
            )
        if os.path.lexists(target) and not _empty_dir(target):
            raise Blocked(
                "Both {} ({}) and {} ({}) exist, so nothing was moved and nothing was "
                "removed. TrackMania Mac uses {} from now on: move anything you want to keep "
                "from the first folder into it, then move the first folder somewhere outside "
                "{}, for example to your Desktop, and try again.".format(
                    source,
                    _describe(source),
                    target,
                    _describe(target),
                    target,
                    source.parent,
                )
            )
        # The target must not lie inside what moves (a link loop through HOME).
        if (info.st_dev, info.st_ino) in uninstall._chain(target.parent):
            raise Blocked("{} lies inside {}".format(target, source))
        if self.deep and kind in ("prefix", "saves", "leftover"):
            inside = uninstall.mounted_inside(source)
            if inside is not None:
                raise Blocked(
                    "{} contains a mounted volume at {}; unmount it first".format(source, inside)
                )
        self.moves.append(Move(kind, source, target, nominal, title, (info.st_dev, info.st_ino)))

    def merge(self, kind: str, source: Path, target: Path, nominal: Path) -> None:
        """A shared folder: whole when the target is free, else child by child.

        A child whose name is already taken stays where it is and is reported;
        nothing is overwritten and nothing is deleted.
        """
        if not os.path.lexists(target) or _empty_dir(target):
            self.add(kind, source, target, nominal)
            return
        if target.is_symlink() or not target.is_dir():
            raise Blocked("{} exists and is not a folder".format(target))
        for name in sorted(os.listdir(source)):
            if name == ".DS_Store" or (kind == "cache" and name == LEGACY_APP_LOCK):
                continue
            if os.path.lexists(target / name):
                self.kept.append(
                    "{} (kept: {} already exists)".format(source / name, target / name)
                )
                continue
            self.add(kind, source / name, target / name, nominal / name)

    def title(self, title: registry.Title) -> None:
        if not title.legacy_prefix_subdir:
            return
        nominal = self.home / LEGACY_GAMES / title.legacy_prefix_subdir
        root = _old_folder(nominal)
        if root is None:
            return
        self.roots.append((title.id, nominal, root))
        new_root = hub_paths.title_root(title.id, self.home)
        new_saves = hub_paths.default_userdata(title.id, self.home)
        prefix = root / "prefix"
        if os.path.lexists(prefix):
            try:
                managed_paths.check_prefix(str(prefix), title=title.id)
            except (OSError, ValueError, RuntimeError) as error:
                self.kept.append(
                    "{} (kept: not a prefix this hub manages: {})".format(prefix, error)
                )
            else:
                self.prefixes.append(prefix)
                self.add("prefix", prefix, new_root / "prefix", nominal / "prefix", title.id)
        userdata = root / "UserData"
        if os.path.lexists(userdata):
            if userdata.is_symlink() or not userdata.is_dir():
                raise Blocked(
                    "{} is a link or not a folder, and TrackMania Mac never moves saves "
                    "through a link. Move the folder holding your saves to {} yourself in "
                    "Finder, then try again.".format(userdata, new_saves)
                )
            if _holds_nothing(userdata):
                self.kept.append("{} (kept: it holds no saves)".format(userdata))
            else:
                self.add("saves", userdata, new_saves, nominal / "UserData", title.id)
        # An unfinished uninstall's tombstones go where the new uninstall looks.
        for name in sorted(os.listdir(root)):
            match = uninstall.TOMBSTONE.fullmatch(name)
            if not match:
                continue
            folded = match.group("name").casefold()
            suffix = name[len(match.group("name")) + 1 :]
            if folded == "prefix":
                target = new_root / name
            elif folded == "userdata":
                target = new_saves.parent / ".{}{}".format(title.id, suffix)
            else:
                continue
            if (root / name).is_symlink() or not (root / name).is_dir():
                self.kept.append("{} (kept: not a folder this hub left behind)".format(root / name))
                continue
            self.add("leftover", root / name, target, nominal / name, title.id)

    def shared(self) -> None:
        if not self.environ.get("TRACKMANIA_MAC_SUPPORT"):
            nominal = self.home.joinpath(*LEGACY_SUPPORT)
            folder = _old_folder(nominal)
            if folder is not None:
                self.support = (nominal, folder)
                support = hub_paths.default_support(self.home)
                for name in SUPPORT_CHILDREN:
                    if os.path.lexists(folder / name):
                        self.merge("support", folder / name, support / name, nominal / name)
        if not self.environ.get("TRACKMANIA_MAC_CACHE"):
            nominal = self.home.joinpath(*LEGACY_CACHE)
            folder = _old_folder(nominal)
            if folder is not None:
                self.cache = (nominal, folder)
                self.merge("cache", folder, hub_paths.default_cache(self.home), nominal)
        nominal = self.home.joinpath(*LEGACY_LOGS)
        folder = _old_folder(nominal)
        if folder is not None:
            self.logs = (nominal, folder)
            self.merge("logs", folder, hub_paths.log_dir(self.home), nominal)


def plan(
    home: typing.Optional[Path] = None,
    environ: typing.Optional[typing.Mapping[str, str]] = None,
    deep: bool = False,
) -> Plan:
    """What would move, read-only. Every problem is collected in `blocked`."""
    home = home if home is not None else Path.home()
    environ = environ if environ is not None else os.environ
    planner = _Planner(home, environ, deep)
    try:
        titles, schema_errors, unreadable = registry.load_registry()
        problems = [f"{stem}: {message}" for stem, message in sorted(schema_errors.items())]
        problems += [f"{path.name}: not valid JSON" for path in unreadable]
        if problems:
            raise Blocked("Fix the title registry first: {}".format("; ".join(problems)))
        _check_text(home)
        for title in titles:
            try:
                planner.title(title)
            except Blocked as error:
                planner.blocked.append(str(error))
        try:
            planner.shared()
        except Blocked as error:
            planner.blocked.append(str(error))
        targets: typing.Dict[str, Move] = {}
        for move in planner.moves:
            other = targets.setdefault(str(move.target).casefold(), move)
            if other is not move:
                planner.blocked.append(
                    "{} and {} would both move to {}".format(other.source, move.source, move.target)
                )
    except (Blocked, OSError, registry.RegistryError) as error:
        planner.blocked.append(str(error))
    return Plan(
        planner.data,
        planner.moves,
        planner.kept,
        planner.blocked,
        planner.prefixes,
        planner.roots,
        planner.support,
        planner.cache,
        planner.logs,
    )


def _journal_path(home: Path) -> Path:
    return hub_paths.data_dir(home) / JOURNAL


def _journal_text(value: object, what: str) -> Path:
    """One absolute, printable path from the journal, checked as a managed path."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("bad {}".format(what))
    path = Path(value)
    _check_text(path)
    managed_paths.safe_path(value)
    return path


def _expected_target(home: Path, kind: str, title: typing.Optional[str], target: Path) -> bool:
    """Whether TARGET is a place this module itself would move a KIND folder to."""
    titles = {item.id for item in registry.load_all() if item.legacy_prefix_subdir}
    if kind in ("prefix", "saves", "leftover"):
        if title is None or title not in titles:
            return False
        root = hub_paths.title_root(title, home)
        saves = hub_paths.default_userdata(title, home)
        if kind == "prefix":
            return target == root / "prefix"
        if kind == "saves":
            return target == saves
        return target.parent in (root, saves.parent) and bool(
            uninstall.TOMBSTONE.fullmatch(target.name)
        )
    if title is not None:
        return False
    if kind == "support":
        support = hub_paths.default_support(home)
        return any(
            target == support / name or target.parent == support / name for name in SUPPORT_CHILDREN
        )
    base = hub_paths.default_cache(home) if kind == "cache" else hub_paths.log_dir(home)
    return target == base or target.parent == base


def _read_journal(path: Path, home: typing.Optional[Path] = None) -> dict:
    """The journal, refused unless every entry is one this module would have written.

    Each move's target is derived again from its kind and title; every path is a
    printable managed path; identities are integers. `exact` and `set` are never
    read from the file: they are derived from the checked moves (see _derived).
    """
    home = home if home is not None else Path.home()
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular file")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != JOURNAL_VERSION:
            raise ValueError("unknown format")
        if not isinstance(data.get("moves"), list):
            raise ValueError("no moves")
        for item in data["moves"]:
            if not isinstance(item, dict) or item.get("kind") not in KINDS:
                raise ValueError("bad kind")
            for key in ("source", "target", "nominal"):
                _journal_text(item.get(key), key)
            title = item.get("title")
            if title is not None and not isinstance(title, str):
                raise ValueError("bad title")
            if not _expected_target(home, item["kind"], title, Path(item["target"])):
                raise ValueError("unexpected target {}".format(item["target"]))
            identity = item.get("identity")
            if not (
                isinstance(identity, list)
                and len(identity) == 2
                and all(isinstance(part, int) and not isinstance(part, bool) for part in identity)
            ):
                raise ValueError("bad identity")
        roots = data.get("roots", [])
        legacy = {
            str(home / LEGACY_GAMES / title.legacy_prefix_subdir): title.id
            for title in registry.load_all()
            if title.legacy_prefix_subdir
        }
        if not isinstance(roots, list):
            raise ValueError("bad roots")
        for pair in roots:
            if not (isinstance(pair, list) and len(pair) == 2) or pair[0] not in legacy:
                raise ValueError("bad roots")
            _journal_text(pair[1], "roots")
        for key in ("support", "cache", "logs"):
            if key in data and data[key] is not None:
                if not (isinstance(data[key], list) and len(data[key]) == 2):
                    raise ValueError("bad {}".format(key))
                _journal_text(data[key][1], key)
        return data
    except (OSError, ValueError, KeyError, TypeError, registry.RegistryError) as error:
        raise Blocked(
            "The record of an unfinished move ({}) cannot be used: {}. Nothing was changed; "
            "check the folders it names before moving it out of the way.".format(path, error)
        ) from error


def _journal_moves(data: dict) -> typing.List[Move]:
    return [
        Move(
            item["kind"],
            Path(item["source"]),
            Path(item["target"]),
            Path(item["nominal"]),
            item.get("title"),
            (int(item["identity"][0]), int(item["identity"][1])),
        )
        for item in data["moves"]
    ]


def _state(move: Move) -> str:
    """ "todo" (still at its source), "done" (at its target) or "lost" (neither)."""
    for path, state in ((move.source, "todo"), (move.target, "done")):
        try:
            if _identity(path) == move.identity:
                return state
        except OSError:
            continue
    return "lost"


def legacy_paths(home: Path, result: Plan) -> typing.List[str]:
    """The old folders that still hold something to move, for the hub."""
    found: typing.List[str] = []
    for move in result.moves:
        if move.kind in ("prefix", "saves", "leftover"):
            entry = str(move.nominal.parent)
        elif move.kind == "support":
            entry = str(home.joinpath(*LEGACY_SUPPORT))
        else:
            entry = str(home.joinpath(*(LEGACY_CACHE if move.kind == "cache" else LEGACY_LOGS)))
        if entry not in found:
            found.append(entry)
    return found


def status(
    home: typing.Optional[Path] = None, environ: typing.Optional[typing.Mapping[str, str]] = None
) -> dict:
    """The hub's view: read-only, never creates or changes anything.

    state is "none" (nothing to move), "pending" (the hub moves it now) or
    "blocked" (reason says why). partial is True once part of the move has
    happened (an unfinished move's record exists): only then may the apps of the
    earlier release stop working. The hub and the CLI both decide on it.
    """
    home = home if home is not None else Path.home()
    target = str(hub_paths.data_dir(home))
    journal = _journal_path(home)
    if os.path.lexists(journal):
        try:
            moves = _journal_moves(_read_journal(journal, home))
            lost = [move for move in moves if _state(move) == "lost"]
            if lost:
                raise Blocked(_lost_message(lost))
        except Blocked as error:
            return {
                "state": "blocked",
                "reason": str(error),
                "partial": True,
                "from": [],
                "to": target,
            }
        return {"state": "pending", "reason": None, "partial": True, "from": [], "to": target}
    result = plan(home, environ)
    if not result.moves and not result.blocked:
        return {"state": "none", "reason": None, "partial": False, "from": [], "to": target}
    reason = "; ".join(result.blocked) if result.blocked else None
    if reason is None:
        for prefix in result.prefixes:
            try:
                operation_guard.check_inactive(prefix)
            except operation_guard.Busy:
                reason = BUSY
                break
            except (OSError, ValueError):
                continue
    return {
        "state": "blocked" if reason else "pending",
        "reason": reason,
        "partial": False,
        "from": legacy_paths(home, result),
        "to": target,
    }


# What the CLI says when the move did not finish, by status()["partial"]; the hub
# shows the same two cases from the same field.
NOT_MOVED = (
    "Your files were not moved, so this command did not run. Your Game and Settings apps in "
    "Applications keep working with your files where they are. Fix the problem above, then run "
    "the command again or open the hub."
)
PARTLY_MOVED = (
    "Part of the move has already happened, so this command did not run, and your Game and "
    "Settings apps may not work until the move finishes. Fix the problem above, then run the "
    "command again or open the hub to finish it."
)


def advice(moving: dict) -> str:
    return PARTLY_MOVED if moving.get("partial") else NOT_MOVED


def _lost_message(lost: typing.Sequence[Move]) -> str:
    return (
        "{} is no longer at {} or {}. Nothing else was changed; put it back in one of "
        "those places, then try again.".format(lost[0].kind, lost[0].source, lost[0].target)
    )


def _derived(
    home: Path, moves: typing.Sequence[Move], journal: dict
) -> typing.Tuple[typing.List[typing.Tuple[str, str]], typing.List[typing.Tuple[str, str]]]:
    """(exact, set) for the relink, derived from checked moves and roots, never read back.

    `exact`: an old title root or shared folder maps only as a whole (an old
    root also held other titles' roots). `set`: settings a launcher must gain.
    """
    exact: typing.List[typing.Tuple[str, str]] = []
    legacy = {
        str(home / LEGACY_GAMES / title.legacy_prefix_subdir): title.id
        for title in registry.load_all()
        if title.legacy_prefix_subdir
    }
    for nominal, resolved in journal.get("roots", ()):
        new = str(hub_paths.title_root(legacy[nominal], home))
        for old in {nominal, resolved}:
            exact.append((old, new))
    kinds = {move.kind for move in moves}
    for key, parts, default in (
        ("support", LEGACY_SUPPORT, hub_paths.default_support(home)),
        ("cache", LEGACY_CACHE, hub_paths.default_cache(home)),
        ("logs", LEGACY_LOGS, hub_paths.log_dir(home)),
    ):
        if key in kinds:
            nominal_path = home.joinpath(*parts)
            for old in {str(nominal_path), str(nominal_path.resolve())}:
                exact.append((old, str(default)))
    settings = [("CACHE", str(hub_paths.default_cache(home)))] if "cache" in kinds else []
    return exact, settings


def _relocation_map(home: Path, moves: typing.Sequence[Move], journal: dict) -> typing.List[str]:
    """Lines for the shell's relink: `prefix OLD NEW`, `exact OLD NEW`, `set NAME VALUE`.

    Paths with control characters never get here, so tab and newline separate.
    A `set` NAME has no prefix; the shell adds the launcher's own.
    """
    lines = []
    for move in moves:
        for old in {str(move.nominal), str(move.source)}:
            lines.append("prefix\t{}\t{}".format(old, move.target))
    exact, settings = _derived(home, moves, journal)
    for old, new in exact:
        lines.append("exact\t{}\t{}".format(old, new))
    for name, value in settings:
        lines.append("set\t{}\t{}".format(name, value))
    return lines


def _link_mapping(moves: typing.Sequence[Move]) -> typing.List[typing.Tuple[str, str]]:
    pairs = []
    for move in moves:
        for old in {str(move.nominal), str(move.source)}:
            pairs.append((old, str(move.target)))
    return pairs


def _move(move: Move, created: typing.List[Path]) -> None:
    """Rename one recorded folder; refuses anything that is not the folder recorded.

    Folders this creates for the target are added to CREATED, outermost first.
    """
    if _identity(move.source) != move.identity:
        raise Blocked("{} changed after it was checked".format(move.source))
    if os.path.lexists(move.target):
        # An empty folder in the way is removed, never renamed over; rmdir
        # itself refuses a folder that gained files since.
        if not _empty_dir(move.target):
            raise Blocked("{} appeared while moving".format(move.target))
        os.rmdir(move.target)
    for parent in reversed(move.target.parents):
        if not os.path.lexists(parent):
            parent.mkdir()
            created.append(parent)
    managed_paths.rename_exclusive(move.source, move.target)


def _rollback(done: typing.Sequence[Move]) -> typing.List[str]:
    """Rename moved folders back, newest first; returns what could not go back."""
    failures = []
    for move in reversed(done):
        try:
            if _identity(move.target) != move.identity:
                failures.append("{} is no longer the folder moved there".format(move.target))
                continue
            managed_paths.rename_exclusive(move.target, move.source)
        except OSError as error:
            failures.append("{}: {}".format(move.target, error))
    return failures


def _write_journal(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}".format(path.name, os.urandom(4).hex()))
    with open(temporary, "x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    managed_paths.rename_exclusive(temporary, path)


def _remove_if_empty(folder: Path, kept: typing.List[str]) -> None:
    """Delete FOLDER when nothing but Finder metadata or our lock file is left in it."""
    if folder.is_symlink() or not folder.is_dir():
        return
    names = os.listdir(folder)
    if any(name not in DISPOSABLE for name in names):
        kept.append("{} (kept: it still holds other files)".format(folder))
        return
    for name in names:
        path = folder / name
        if path.is_symlink() or not path.is_file():
            kept.append("{} (kept: it still holds other files)".format(folder))
            return
    for name in names:
        (folder / name).unlink()
    folder.rmdir()


def _cleanup(home: Path, journal: dict, kept: typing.List[str]) -> None:
    """Remove the emptied old folders, deepest first, then ~/Games itself. Best effort.

    Only folders this module derives itself: each title's old root (from the
    registry) and the old support, cache and logs folders, followed through a
    link only to the folder the link names now. A failure is reported, never fatal.
    """
    games = home / LEGACY_GAMES
    folders: typing.Set[Path] = set()
    recorded = {pair[0] for pair in journal.get("roots", ())}
    for title in registry.load_all():
        if not title.legacy_prefix_subdir:
            continue
        nominal = games / title.legacy_prefix_subdir
        if str(nominal) not in recorded:
            continue
        if nominal.is_symlink():
            kept.append("{} (kept: a link you made)".format(nominal))
        try:
            folders.add(nominal.resolve(strict=True))
        except (OSError, RuntimeError):
            continue
        for parent in nominal.parents:
            if parent == games or games not in parent.parents:
                break
            folders.add(parent)
    shared = []
    for key, parts in (("support", LEGACY_SUPPORT), ("cache", LEGACY_CACHE), ("logs", LEGACY_LOGS)):
        if journal.get(key):
            nominal = home.joinpath(*parts)
            if nominal.is_symlink():
                kept.append("{} (kept: a link you made)".format(nominal))
            shared.append(nominal)
    ordered = sorted(folders, key=lambda path: len(path.parts), reverse=True)
    if journal.get("roots"):
        ordered.append(games)
    for folder in ordered + shared:
        try:
            if folder == games:
                _remove_if_empty(folder, [])
            else:
                _remove_if_empty(folder.resolve() if folder in shared else folder, kept)
        except OSError as error:
            kept.append("{} (kept: {})".format(folder, error.strerror or error))


def _remove_build_workspace(
    home: Path, journal: dict, report: typing.Callable[[str], None]
) -> None:
    """A moved runtime build workspace records its old folder in its build files; drop it.

    Only the new cache's own `build` folder, and only when this move brought it
    there (the whole cache, or `build` itself in a merge); never a folder that
    merely has that name deeper in the cache.
    """
    cache = hub_paths.default_cache(home)
    build = cache / "build"
    moved = {Path(item["target"]) for item in journal["moves"] if item["kind"] == "cache"}
    if not moved & {cache, build}:
        return
    if build.is_symlink() or not build.is_dir() or not shutil.rmtree.avoids_symlink_attacks:
        return
    try:
        shutil.rmtree(build)
        report("Removed the unfinished runtime build in {}".format(build))
    except OSError as error:
        report("Could not remove {}: {}".format(build, error))


def _locks(prefixes: typing.Sequence[Path], home: Path) -> typing.List[int]:
    """Every old prefix's lock with Wine inactive, then the app locks (old and new).

    Wine is probed first, which changes nothing; the data folder only comes
    into being with the app lock, once every prefix is known to be free.
    """
    fds: typing.List[int] = []
    try:
        fds = operation_guard.acquire_prefixes(prefixes)
        legacy = home.joinpath(*LEGACY_CACHE) / LEGACY_APP_LOCK
        if legacy.is_file() and not legacy.is_symlink():
            fds.append(operation_guard._acquire(legacy, BUSY))
        fds.insert(0, operation_guard._acquire(operation_guard.app_lock_path(create=True), BUSY))
    except operation_guard.Busy as error:
        for fd in fds:
            os.close(fd)
        raise Busy(BUSY) from error
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    return fds


def _relink(
    home: Path,
    command: typing.Optional[typing.Sequence[str]],
    moves: typing.Sequence[Move],
    journal: dict,
    app_fd: int,
    report: typing.Callable[[str], None],
) -> None:
    """Point every title's installed apps and launcher at the moved folders (shell owns their format)."""
    if not command:
        return
    mapping = "\n".join(_relocation_map(home, moves, journal))
    try:
        titles = registry.load_all()
    except registry.RegistryError as error:
        report("warning: could not update the apps ({}); reinstall them from the hub".format(error))
        return
    for title in titles:
        env = dict(os.environ)
        for name in hub_paths.TITLE_ENV:
            env.pop(name, None)
        env.update(
            TRACKMANIA_MAC_TITLE=title.id,
            TRACKMANIA_MAC_RELOCATION_MAP=mapping,
            **{operation_guard.APP_TOKEN: str(app_fd)},
        )
        result = subprocess.run(list(command), env=env, pass_fds=(app_fd,), check=False)
        if result.returncode != 0:
            report(
                "warning: the {} apps still point at the old folders; reinstall them from "
                "the hub".format(title.name)
            )


def migrate(
    home: typing.Optional[Path] = None,
    environ: typing.Optional[typing.Mapping[str, str]] = None,
    relink: typing.Optional[typing.Sequence[str]] = None,
    report: typing.Optional[typing.Callable[[str], None]] = None,
) -> int:
    """Move everything (or resume an interrupted move). Returns how many folders moved.

    Raises Blocked (nothing moved), Busy, or OSError after a failure that was
    rolled back (or, if even that failed, left for the next run to resume).
    """
    home = home if home is not None else Path.home()
    environ = environ if environ is not None else os.environ
    report = report or print
    journal_path = _journal_path(home)
    resuming = os.path.lexists(journal_path)
    if resuming:
        journal = _read_journal(journal_path, home)
        moves = _journal_moves(journal)
        states = [_state(move) for move in moves]
        lost = [move for move, state in zip(moves, states) if state == "lost"]
        if lost:
            raise Blocked(_lost_message(lost))
        prefixes = [
            move.source if state == "todo" else move.target
            for move, state in zip(moves, states)
            if move.kind == "prefix"
        ]
        todo = [move for move, state in zip(moves, states) if state == "todo"]
        report("Finishing an unfinished move into {}".format(hub_paths.data_dir(home)))
        kept: typing.List[str] = []
    else:
        result = plan(home, environ, deep=True)
        if result.blocked:
            raise Blocked("; ".join(result.blocked))
        if not result.moves:
            return 0
        moves = todo = result.moves
        prefixes = result.prefixes
        kept = list(result.kept)
        journal = {
            "version": JOURNAL_VERSION,
            "moves": [move.record() for move in moves],
            "roots": [[str(nominal), str(resolved)] for _, nominal, resolved in result.roots],
        }
        for key, pair in (
            ("support", result.support),
            ("cache", result.cache),
            ("logs", result.logs),
        ):
            if pair and any(move.kind == key for move in moves):
                journal[key] = [str(pair[0]), str(pair[1])]
    fds = _locks(prefixes, home)
    try:
        if not resuming:
            _write_journal(journal_path, journal)
        done: typing.List[Move] = []
        created: typing.List[Path] = []
        try:
            for move in todo:
                _move(move, created)
                done.append(move)
                report("Moved {} to {}".format(move.source, move.target))
        except BaseException as error:
            if resuming:
                # Earlier runs moved the rest: going forward is the only way back
                # to one complete layout, so the record stays for the next run.
                raise OSError(
                    "The move stopped again ({}). Open the hub again to finish it.".format(error)
                ) from error
            failures = _rollback(done)
            if failures:
                raise OSError(
                    "The move stopped ({}) and some folders could not be moved back ({}). "
                    "Open the hub again to finish the move.".format(error, "; ".join(failures))
                ) from error
            journal_path.unlink()
            for folder in reversed(created):
                try:
                    folder.rmdir()
                except OSError:
                    pass
            if not isinstance(error, Exception):
                raise
            raise OSError(
                "The move stopped and everything was put back where it was: {}".format(error)
            ) from error
        # Every folder has moved: from here on nothing may leave the move pending.
        # A failure is reported and the record removed, so the hub never loops.
        for move in moves:
            if move.kind == "prefix":
                try:
                    for link in managed_paths.remap_links(move.target, _link_mapping(moves)):
                        report("Updated {}".format(link))
                    # This release's marker names beside the earlier ones, which stay
                    # for the launcher of that release until the next Update.
                    managed_paths.adopt_prefix_markers(move.target, title=move.title)
                except (OSError, ValueError, RuntimeError) as error:
                    report(
                        "warning: could not update the links in {} ({}). The game may not find "
                        "its saves until they point at the new saves folder; write a diagnostics "
                        "report from the hub and report it.".format(move.target, error)
                    )
        _relink(home, relink, moves, journal, fds[0], report)
        _remove_build_workspace(home, journal, report)
        _cleanup(home, journal, kept)
        journal_path.unlink()
    finally:
        for fd in fds:
            os.close(fd)
    for line in kept:
        report("Kept {}".format(line))
    report("Your TrackMania files are now in {}".format(hub_paths.data_dir(home)))
    return len(todo)


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="exit 0 when files of an earlier release are left, 3 if not")
    sub.add_parser("status", help="print the hub's migration object as JSON")
    sub.add_parser("advice", help="what the CLI says when the move did not finish")
    run = sub.add_parser("run", help="move everything; RELINK runs once per title")
    run.add_argument("relink", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "pending":
            return 0 if status()["state"] != "none" else NOTHING_TO_MOVE
        if args.command == "status":
            print(json.dumps(status(), sort_keys=True))
            return 0
        if args.command == "advice":
            print(advice(status()))
            return 0
        migrate(relink=args.relink or None)
        return 0
    except Busy as error:
        print("error: {}".format(error), file=sys.stderr)
        return operation_guard.EXIT_BUSY
    except (Blocked, OSError, ValueError, RuntimeError, registry.RegistryError) as error:
        message = str(error)
        print(
            "error: {}".format(message if uninstall._printable(message) else repr(message)),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
