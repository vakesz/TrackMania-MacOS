"""Machine-wide cleanup for the hub's Settings: cached downloads, runtimes no
installed launcher uses, and logs no game is writing.

`preview` is the read-only view the hub snapshot reports as `storage`; it
takes no lock and never raises. `clean` takes the locks, plans again and
removes exactly what it planned with uninstall.remove, which renames a folder
to a tombstone and deletes it only while its device and inode still match.

- downloads: only named files, never "everything in the cache": each title's
  pinned installer with its partial downloads and unpacked staging
  (uninstall.cache_entries), the shared DirectX, TMLoader and OpenAL Soft
  downloads in any version, runtime archives, runtime source-build workspaces
  and unpacked downloads older than an hour. Lock files, the session helper
  (`game-state-*.exe`), import mount points and anything unknown stay.
- runtimes: `runtime/<X.Y.Z>` folders carrying a runtime marker that neither
  this release nor any installed launcher uses. A launcher uses the runtime its
  config/installation.sh records, else the one of its own version; one whose
  settings cannot be read blocks every removal.
- logs: `<name>.log` and its rotated copies while their `<name>.log.lock` can
  be locked (a launch holds it for the whole session), and diagnostics
  reports. Lock files are never removed.

Downloads and runtimes are cleaned holding the app lock and every prefix lock
with Wine inactive, so no install, update or game is running at the time.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import re
import stat
import sys
import time
import typing
from pathlib import Path

from trackmania_mac.app import launcher_config
from trackmania_mac.hub import paths as hub_paths
from trackmania_mac.hub import registry
from trackmania_mac.install import version as project_version

from . import managed_paths, operation_guard, uninstall
from .uninstall import Entry

KINDS = ("downloads", "runtimes", "logs")
BUSY = "A game or another TrackMania task is running. Try again once it has finished."
# Files and folders a snapshot may visit to size everything; past it a size reads as unknown.
SIZE_BUDGET = 200_000
# The age sweep_stale (common.sh) gives an unpacked download before it counts as left over.
STALE_SECONDS = 60 * 60
RUNTIME_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
BUILD_REVISION = re.compile(r"[0-9a-f]{40}")
CLONE = re.compile(r"\.clone\.[A-Za-z0-9]{6}")
EXTRACT = re.compile(r"(?:directx|tmloader|openal)-extract\.[A-Za-z0-9]{6}")
PARTIAL = re.compile(r"(?P<base>.+)\.partial\.[A-Za-z0-9]{6}")
SHARED_DOWNLOADS = (
    (re.compile(r"directx_Jun2010_redist\.exe"), "DirectX June 2010 redistributable"),
    (re.compile(r"TMLoader-(?P<version>[0-9A-Za-z._-]+)\.zip"), "TMLoader {version}"),
    (re.compile(r"openal-soft-(?P<version>[0-9A-Za-z._-]+)-bin\.zip"), "OpenAL Soft {version}"),
    (
        re.compile(r"trackmania-mac-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-runtime\.tar\.xz"),
        "Runtime {version} download",
    ),
)
LOG = re.compile(r"(?P<base>[a-z0-9][a-z0-9-]*\.log)(?:\.[1-3])?")
REPORT = re.compile(r"diagnostics-[a-z0-9-]+-[0-9]{8}-[0-9]{6}\.zip")
LEFTOVER = "Files left by an unfinished cleanup"
UNFINISHED = "Unfinished download"


class Locations(typing.NamedTuple):
    home: Path
    data: Path
    support: Path
    cache: Path
    logs: Path


def locations() -> Locations:
    """The same folders hub/paths.py and common.sh compute."""
    home = Path.home().resolve()
    return Locations(
        home,
        hub_paths.data_dir(home),
        hub_paths.support_dir(),
        hub_paths.cache_dir(),
        hub_paths.log_dir(home),
    )


def titles() -> typing.List[registry.Title]:
    """Every readable registry entry; a broken one only keeps its installer."""
    loaded, _errors, _unreadable = registry.load_registry()
    return loaded


class _Sizer:
    """Apparent sizes, visiting at most `budget` entries across every call."""

    def __init__(self, budget: typing.Optional[int] = None):
        self.left = SIZE_BUDGET if budget is None else budget

    def size(self, path: Path) -> typing.Optional[int]:
        try:
            info = os.lstat(path)
        except OSError:
            return None
        self.left -= 1
        if self.left < 0:
            return None
        if not stat.S_ISDIR(info.st_mode):
            return info.st_size
        total, pending = 0, [path]
        while pending:
            try:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        self.left -= 1
                        if self.left < 0:
                            return None
                        try:
                            child = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISDIR(child.st_mode):
                            if child.st_dev == info.st_dev:  # never into another volume
                                pending.append(Path(entry.path))
                        else:
                            total += child.st_size
            except OSError:
                continue
        return total


def _total(sizes: typing.Iterable[typing.Optional[int]]) -> typing.Optional[int]:
    total = 0
    for size in sizes:
        if size is None:
            return None
        total += size
    return total


# --- Downloads -------------------------------------------------------------


def _installer_label(entry: Entry, installer: str, title: registry.Title) -> str:
    name = entry.path.name
    if entry.verb == "prune":
        return ""
    if name == installer:
        return "{} installer".format(title.name)
    if PARTIAL.fullmatch(name):
        return UNFINISHED
    if uninstall.TOMBSTONE.fullmatch(name):
        return LEFTOVER
    return "Unpacked {} installer".format(title.name)


def _shared_label(name: str) -> typing.Optional[str]:
    partial = PARTIAL.fullmatch(name)
    base = partial.group("base") if partial else name
    for pattern, label in SHARED_DOWNLOADS:
        match = pattern.fullmatch(base)
        if match:
            return UNFINISHED if partial else label.format(**match.groupdict())
    return None


def _build_entries(cache: Path) -> typing.List[typing.Tuple[str, Entry]]:
    """Source-build workspaces (install_wine_runtime with RUNTIME=build) in cache/build."""
    build = cache / "build"
    if not os.path.lexists(build):
        return []
    if build.is_symlink() or not build.is_dir() or not uninstall._owned(build):
        return [("", uninstall._keep("cache", build, "not a build folder this hub made"))]
    planned = []
    for child in sorted(os.listdir(build)):
        if BUILD_REVISION.fullmatch(child) or CLONE.fullmatch(child):
            label = "Runtime source build"
        elif uninstall.TOMBSTONE.fullmatch(child):
            label = LEFTOVER
        else:
            continue
        path = build / child
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            planned.append((label, uninstall._remove("cache", path)))
        else:
            planned.append((label, uninstall._keep("cache", path, "not a folder this hub made")))
    planned.append(("", uninstall._planned("prune", "cache", build)))
    return planned


def plan_downloads(
    cache: Path, entries: typing.Sequence[registry.Title]
) -> typing.List[typing.Tuple[str, Entry]]:
    """(label, entry) pairs in removal order; a prune or keep may carry an empty label."""
    if not os.path.lexists(cache):
        return []
    if cache.is_symlink() or not cache.is_dir():
        raise ValueError("The downloads folder is not a plain folder: {}".format(cache))
    root = managed_paths.safe_path(str(cache))
    planned: typing.List[typing.Tuple[str, Entry]] = []
    for title in entries:
        target = uninstall.installer_cache_path(title, root)
        if target is None:
            continue
        for entry in uninstall.cache_entries(title, root):
            planned.append((_installer_label(entry, target.name, title), entry))
    now = time.time()
    for child in sorted(os.listdir(root)):
        path = root / child
        label = _shared_label(child)
        if label is not None:
            if uninstall._regular_file(path):
                planned.append((label, uninstall._remove("cache", path)))
            else:
                planned.append((label, uninstall._keep("cache", path, "not a regular file")))
            continue
        tombstone = uninstall.TOMBSTONE.fullmatch(child)
        if EXTRACT.fullmatch(child) or (tombstone and EXTRACT.fullmatch(tombstone.group("name"))):
            info = os.lstat(path)
            if (
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == os.getuid()
                and (tombstone or now - info.st_mtime > STALE_SECONDS)
            ):
                planned.append(
                    (
                        LEFTOVER if tombstone else "Unpacked download",
                        uninstall._remove("cache", path),
                    )
                )
    return planned + _build_entries(root)


# --- Runtimes --------------------------------------------------------------


class Launcher(typing.NamedTuple):
    path: Path
    settings: typing.Optional[typing.Dict[str, str]]
    problem: typing.Optional[str]


def launchers(support: Path) -> typing.List[Launcher]:
    """Every launcher folder this hub installed under SUPPORT, with its recorded settings."""
    candidates = []
    folder = support / "launchers"
    if folder.is_dir() and not folder.is_symlink():
        candidates += [folder / name for name in sorted(os.listdir(folder)) if name[:1] != "."]
    # The shared launcher of the releases before one launcher per title.
    candidates.append(support / "launcher")
    found = []
    for path in candidates:
        if path.is_symlink() or not path.is_dir() or not uninstall.has_marker(path, "LAUNCHER"):
            continue
        config = path / launcher_config.CONFIG
        try:
            settings = launcher_config.read(config) if os.path.lexists(config) else {}
        except (OSError, ValueError) as error:
            found.append(Launcher(path, None, str(error)))
            continue
        found.append(Launcher(path, settings, None))
    return found


def _launcher_version(path: Path) -> str:
    version_file = path / "VERSION"
    if version_file.is_file():
        lines = version_file.read_text().splitlines()
        version = lines[0].strip() if lines else ""
        if version:
            return version
    return project_version.read(path)


def runtimes_in_use(
    support: Path, current: str, found: typing.Sequence[Launcher]
) -> typing.Tuple[typing.List[Path], typing.Optional[str]]:
    """(runtime folders in use, why nothing may be removed or None)."""
    used = [support / "runtime" / current]
    for launcher in found:
        reason = launcher.problem
        if launcher.settings is not None:
            recorded = launcher.settings.get("RUNTIME")
            if recorded and recorded.startswith("/"):
                used.append(Path(recorded))
                continue
            if recorded:
                reason = "it records a relative runtime path"
            else:
                try:
                    used.append(support / "runtime" / _launcher_version(launcher.path))
                    continue
                except (OSError, project_version.VersionError) as error:
                    reason = str(error)
        return used, (
            "Cannot tell which runtime the launcher in {} uses ({}). "
            "Reinstall its apps from the hub first.".format(launcher.path, reason)
        )
    return used, None


class RuntimePlan(typing.NamedTuple):
    entries: typing.List[Entry]
    in_use: typing.List[str]
    blocked: typing.Optional[str]
    used: typing.List[Path]


def _identity(path: Path) -> typing.Optional[typing.Tuple[int, int]]:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def runtime_version_name(path: Path) -> str:
    tombstone = uninstall.TOMBSTONE.fullmatch(path.name)
    return tombstone.group("name") if tombstone else path.name


def plan_runtimes(support: Path, current: str, found: typing.Sequence[Launcher]) -> RuntimePlan:
    directory = support / "runtime"
    used, blocked = runtimes_in_use(support, current, found)
    in_use = []
    for path in used:
        name = path.name if path.parent == directory else str(path)
        if name not in in_use:
            in_use.append(name)
    if blocked is not None or not os.path.lexists(directory):
        return RuntimePlan([], in_use, blocked, used)
    if directory.is_symlink() or not directory.is_dir():
        reason = "The runtime folder is not a plain folder: {}".format(directory)
        return RuntimePlan([], in_use, reason, used)
    root = managed_paths.safe_path(str(directory))
    used_ids = {identity for identity in map(_identity, used) if identity is not None}
    entries = []
    for child in sorted(os.listdir(root)):
        tombstone = uninstall.TOMBSTONE.fullmatch(child)
        name = tombstone.group("name") if tombstone else child
        if not RUNTIME_VERSION.fullmatch(name) or (not tombstone and name == current):
            continue
        path = root / child
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            continue
        if (info.st_dev, info.st_ino) in used_ids:
            continue
        # A tombstone is our own unfinished removal; a runtime must carry its marker.
        if tombstone or uninstall.has_marker(path, "RUNTIME"):
            entries.append(uninstall._remove("runtime", path))
    return RuntimePlan(entries, in_use, None, used)


# --- Logs ------------------------------------------------------------------


def _log_files(
    directory: Path,
) -> typing.Optional[typing.Tuple[Path, typing.Dict[str, typing.List[str]], typing.List[str]]]:
    if not os.path.lexists(directory):
        return None
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("The logs folder is not a plain folder: {}".format(directory))
    resolved = managed_paths.safe_path(str(directory))
    groups: typing.Dict[str, typing.List[str]] = {}
    reports = []
    for child in sorted(os.listdir(resolved)):
        match = LOG.fullmatch(child)
        if match:
            groups.setdefault(match.group("base"), []).append(child)
        elif REPORT.fullmatch(child):
            reports.append(child)
    return resolved, groups, reports


def _log_lock(directory: Path, base: str) -> typing.Optional[int]:
    """The lock a launch holds on BASE (app/launch.py), or None while one does.

    It is created when missing, as a launch would, so a launch that starts
    now waits for this cleanup instead of writing to a removed file.
    """
    path = directory / (base + ".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Unsafe lock file: {}".format(path))
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                return None
            raise
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return fd


def _file_entry(path: Path) -> Entry:
    if uninstall._regular_file(path):
        return uninstall._remove("logs", path)
    return uninstall._keep("logs", path, "not a regular file")


def clean_logs(directory: Path, report: typing.Callable[[str], None]) -> int:
    found = _log_files(directory)
    if found is None:
        return 0
    resolved, groups, reports = found
    done = 0
    for base, names in sorted(groups.items()):
        fd = _log_lock(resolved, base)
        if fd is None:
            report("Kept {} (a game is writing it)".format(resolved / base))
            continue
        try:
            done += uninstall.remove([_file_entry(resolved / name) for name in names], report)
        finally:
            os.close(fd)
    done += uninstall.remove([_file_entry(resolved / name) for name in reports], report)
    return done


def logs_size(directory: Path, sizer: _Sizer) -> typing.Optional[int]:
    found = _log_files(directory)
    if found is None:
        return 0
    resolved, groups, reports = found
    names = [name for group in groups.values() for name in group] + reports
    return _total(sizer.size(resolved / name) for name in names)


# --- Locks and limits ------------------------------------------------------


def _prefixes(
    where: Locations, entries: typing.Sequence[registry.Title], found: typing.Sequence[Launcher]
) -> typing.List[Path]:
    """Every prefix an operation could hold: each title's, each folder in games/,
    and each prefix a launcher records. Only those whose folder exists can be locked."""
    candidates = [hub_paths.title_root(title.id) / "prefix" for title in entries]
    games = where.data / "games"
    if games.is_dir() and not games.is_symlink():
        candidates += [
            games / name / "prefix" for name in sorted(os.listdir(games)) if name[:1] != "."
        ]
    for launcher in found:
        recorded = (launcher.settings or {}).get("PREFIX")
        if recorded and recorded.startswith("/"):
            candidates.append(Path(recorded))
    prefixes, seen = [], set()
    for prefix in candidates:
        parent = _identity(prefix.parent)
        if parent is None or not prefix.parent.is_dir():
            continue
        key = (parent, prefix.name.casefold())
        if key not in seen:
            seen.add(key)
            prefixes.append(prefix)
    return prefixes


def _lock(where: Locations, entries, found) -> typing.List[int]:
    fds = operation_guard.acquire_prefixes(_prefixes(where, entries, found))
    try:
        app = operation_guard.app_lock_path(create=True)
        fds.insert(0, operation_guard._acquire(app, BUSY))
    except BaseException:
        for fd in fds:
            os.close(fd)
        raise
    return fds


def _limits(
    where: Locations, entries: typing.Sequence[registry.Title], kept: typing.Sequence[Path]
) -> uninstall.Limits:
    protected = [
        where.home,
        where.data,
        where.data / "games",
        hub_paths.saves_dir(where.home),
        where.support,
        where.support / "runtime",
        where.support / "launchers",
        where.support / "icons",
        where.cache,
        where.cache / "games",
        where.logs,
        *kept,
    ]
    for title in entries:
        root = hub_paths.title_root(title.id, where.home)
        protected += [root, root / "prefix", hub_paths.default_userdata(title.id, where.home)]
    return uninstall.Limits(protected, [])


def clean(kind: str, report: typing.Callable[[str], None] = print) -> int:
    """Remove what KIND names; returns how many paths were removed.

    Raises operation_guard.Busy while anything runs, ValueError when a check
    refuses, OSError or RuntimeError when removal fails.
    """
    if kind not in KINDS:
        raise ValueError("Unknown cleanup {!r}: use downloads, runtimes or logs.".format(kind))
    where = locations()
    if kind == "logs":
        return clean_logs(where.logs, report)
    current = hub_paths.runtime_version()
    entries = titles()
    fds = _lock(where, entries, launchers(where.support))
    try:
        if kind == "downloads":
            planned = [entry for _label, entry in plan_downloads(where.cache, entries)]
            bounds = _limits(where, entries, [])
        else:
            # Read again under the app lock: no install can change a launcher now.
            result = plan_runtimes(where.support, current, launchers(where.support))
            if result.blocked is not None:
                raise ValueError(result.blocked)
            planned, bounds = result.entries, _limits(where, entries, result.used)
        for entry in planned:
            if not uninstall._printable(str(entry.path)) or not uninstall._printable(entry.reason):
                raise ValueError("Unsafe path in the cleanup plan: {!r}".format(str(entry.path)))
            if entry.verb != "keep":
                bounds.check(entry)
        return uninstall.remove(planned, report)
    finally:
        for fd in fds:
            os.close(fd)


# --- Snapshot --------------------------------------------------------------


def preview() -> typing.Optional[dict]:
    """The snapshot's `storage` object, or None when it cannot be computed. Never raises."""
    try:
        where = locations()
        current = hub_paths.runtime_version()
    except Exception:  # noqa: BLE001 - one probe must not break the snapshot
        return None
    sizer = _Sizer()
    problems = []
    downloads: dict = {"bytes": None, "items": []}
    try:
        removed = [
            (label, entry.path, sizer.size(entry.path))
            for label, entry in plan_downloads(where.cache, titles())
            if entry.verb == "remove" and label
        ]
        downloads = {
            "bytes": _total(size for _label, _path, size in removed),
            "items": [
                {"label": label, "path": str(path), "bytes": size} for label, path, size in removed
            ],
        }
    except Exception as error:  # noqa: BLE001
        problems.append("Cannot list the downloads: {}".format(error))
    runtimes: dict = {"current": current, "items": [], "inUse": [current], "blocked": None}
    try:
        plan = plan_runtimes(where.support, current, launchers(where.support))
        runtimes.update(
            items=[
                {
                    "version": runtime_version_name(entry.path),
                    "path": str(entry.path),
                    "bytes": sizer.size(entry.path),
                }
                for entry in plan.entries
            ],
            inUse=plan.in_use,
            blocked=plan.blocked,
        )
    except Exception as error:  # noqa: BLE001
        runtimes["blocked"] = "Cannot check the runtimes: {}".format(error)
        problems.append(runtimes["blocked"])
    logs: dict = {"bytes": None, "path": str(where.logs)}
    try:
        logs["bytes"] = logs_size(where.logs, sizer)
    except Exception as error:  # noqa: BLE001
        problems.append("Cannot list the logs: {}".format(error))
    return {
        "downloads": downloads,
        "runtimes": runtimes,
        "logs": logs,
        "error": "; ".join(problems) if problems else None,
    }


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("clean",))
    parser.add_argument("kind")
    args = parser.parse_args(argv)
    try:
        if not clean(args.kind):
            print("Nothing to remove.")
        return 0
    except operation_guard.Busy:
        print("error: {}".format(BUSY), file=sys.stderr)
        return operation_guard.EXIT_BUSY
    except (
        OSError,
        ValueError,
        RuntimeError,
        registry.RegistryError,
        project_version.VersionError,
    ) as error:
        message = str(error)
        shown = message if uninstall._printable(message) else repr(message)
        print("error: {}".format(shown), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
