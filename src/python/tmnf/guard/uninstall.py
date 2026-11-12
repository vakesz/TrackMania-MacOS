"""Per-title uninstall: plan every path one title owns, then remove exactly those.

`plan` validates every path and describes it for the confirmation prompt;
`remove` builds the same plan in this process and deletes it. No other layer
parses a plan, so no path or app metadata can be read back as an instruction.
`preview` is the cheap read-only view the hub snapshot shows in its dialog.

What a title owns:
- its prefix (`check_prefix` accepts it) and, only with include_saves, its
  default UserData folder, which is the prefix's Windows Documents folder;
- its two app wrappers, only when they carry our `.tmnf-app` marker and this
  title's bundle identifier;
- its launcher support directory, cached installer, logs and extracted icon;
- tombstones a previous, interrupted uninstall left beside any of these.

Never removed: a title root (tmnf's root `~/Games/TMNF` holds the other
titles' roots and every root holds its operation lock), the runtime, shared
downloads, another title's paths, or anything outside the managed roots.

Removal renames each folder to a tombstone in its own parent first, so a
removal that stops halfway never leaves a folder that has lost its ownership
marker under its real name; the next run finds the tombstone and finishes it.
"""

from __future__ import annotations

import argparse
import errno
import os
import plistlib
import re
import secrets
import shutil
import stat
import sys
import typing
import unicodedata
from pathlib import Path

from tmnf.hub import registry

from . import managed_paths

if typing.TYPE_CHECKING:
    from tmnf.hub.paths import TitlePaths

LEGACY_APP_ID = "local.tmnf.launcher"
LOG_SUFFIXES = ("", ".1", ".2", ".3")
# Always matched with fullmatch: "$" would also accept a trailing newline.
TOMBSTONE = re.compile(r"\.(?P<name>.+)\.tmnf-removing\.[0-9a-f]{8}")
MOUNTED = "is a mounted volume; unmount it first"
UNFINISHED_SAVES = "saves from an unfinished uninstall"
# plan exits with this status when nothing is left to remove.
NOTHING_TO_REMOVE = 3

Identity = typing.Tuple[int, int]


class Entry(typing.NamedTuple):
    # "remove", "prune" (remove only if empty), "restore" (rename back to
    # target) or "keep"
    verb: str
    kind: str
    path: Path
    reason: str = ""
    # (st_dev, st_ino) of the path and of its parent when planned; removal
    # refuses to act on anything that no longer matches.
    identity: typing.Optional[Identity] = None
    parent: typing.Optional[Identity] = None
    target: typing.Optional[Path] = None

    def describe(self) -> str:
        if self.verb == "keep":
            return "{} ({})".format(self.path, self.reason)
        if self.verb == "restore":
            return "{} ({}; moved back to {})".format(self.path, self.reason, self.target)
        return str(self.path)


def _identity(info: os.stat_result) -> Identity:
    return (info.st_dev, info.st_ino)


def _planned(verb: str, kind: str, path: Path) -> Entry:
    """A removal of PATH (resolved, not a symlink) with its identity recorded.

    A path on another device than its parent is a mount point: removing it
    would empty that whole volume, so it is kept instead.
    """
    info, parent = os.lstat(path), os.lstat(path.parent)
    if info.st_dev != parent.st_dev:
        return _keep(kind, path, MOUNTED)
    return Entry(verb, kind, path, identity=_identity(info), parent=_identity(parent))


def _mount_point(path: Path) -> bool:
    try:
        return os.lstat(path).st_dev != os.lstat(path.parent).st_dev
    except OSError:
        return False


def _remove(kind: str, path: Path) -> Entry:
    return _planned("remove", kind, path)


def _keep(kind: str, path: Path, reason: str) -> Entry:
    return Entry("keep", kind, path, reason)


# Control (C0 and C1), format (bidi overrides, zero-width) and line or
# paragraph separators can all disguise or break a line of output.
_UNPRINTABLE = frozenset(("Cc", "Cf", "Zl", "Zp"))


def _printable(text: str) -> bool:
    return not any(unicodedata.category(char) in _UNPRINTABLE for char in text)


def _plain_absolute(value: str, what: str) -> Path:
    if (
        not value.startswith("/")
        or any(part in (".", "..") for part in value.split("/"))
        or not _printable(value)
    ):
        raise ValueError("Unsafe {} path: {!r}".format(what, value))
    return Path(value.rstrip("/") or "/")


def _owned(path: Path) -> bool:
    return os.lstat(path).st_uid == os.getuid()


def _same(left: Path, right: Path) -> bool:
    """Same file by device and inode: APFS compares names case-insensitively."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def load_titles() -> typing.Tuple[registry.Title, typing.List[registry.Title]]:
    """(this title, every registry entry), from TMNF_TITLES_DIR and the shipped registry.

    Fails closed: without every title's prefix_subdir the nesting rule below
    cannot prove that a removal stays out of another title's files.
    """
    title_id = managed_paths._current_title()
    directories = [registry.titles_dir()]
    shipped = registry.project_dir() / "config" / "titles"
    if shipped.resolve() != directories[0].resolve():
        directories.append(shipped)
    current = None
    titles: typing.List[registry.Title] = []
    for index, directory in enumerate(directories):
        loaded, schema_errors, unreadable = registry.load_registry(directory)
        problems = [f"{stem}: {message}" for stem, message in sorted(schema_errors.items())]
        problems += [f"{path.name}: not valid JSON" for path in unreadable]
        if problems:
            raise ValueError(
                "Fix the title registry before uninstalling: {}".format("; ".join(problems))
            )
        titles.extend(loaded)
        if index == 0:
            current = next((title for title in loaded if title.id == title_id), None)
    # The conflict check keys titles by id, so an override may not quietly
    # redefine a shipped title's root: every (directory, id) must agree.
    roots: typing.Dict[str, str] = {}
    for loaded_title in titles:
        known = roots.setdefault(loaded_title.id, loaded_title.prefix_subdir)
        if known.casefold() != loaded_title.prefix_subdir.casefold():
            raise ValueError(
                "Fix the title registry before uninstalling: {} has prefix_subdir {} in "
                "TMNF_TITLES_DIR but {} in the shipped registry".format(
                    loaded_title.id, known, loaded_title.prefix_subdir
                )
            )
    conflicts = registry.subdir_conflicts(titles)
    if conflicts:
        raise ValueError(
            "Fix the title registry before uninstalling: {}".format(
                "; ".join(f"{key}: {message}" for key, message in sorted(conflicts.items()))
            )
        )
    if current is None:
        raise ValueError("Unknown title: {}".format(title_id))
    return current, titles


def title_root(title: registry.Title, home: Path) -> Path:
    return home / "Games" / title.prefix_subdir


def default_userdata(title: registry.Title, home: Path) -> Path:
    return title_root(title, home) / "UserData"


def installer_cache_path(title: registry.Title, cache: Path) -> typing.Optional[Path]:
    """Mirror of _cache_installer_path in src/shell/lib/titles.sh (parity-tested)."""
    if not title.source_url:
        return None
    name = title.source_url.rsplit("/", 1)[-1]
    if title.id == "tmnf":
        # The historical single-title location, directly in the shared cache.
        return cache / name
    return cache / "games" / title.id / name


def shared_cache_names() -> typing.Set[str]:
    """Files directly in the cache that every title uses (see common.sh)."""
    pins = registry.pins()
    return {
        "directx_Jun2010_redist.exe",
        pins["directx_url"].rsplit("/", 1)[-1],
        "TMLoader-{}.zip".format(pins["tmloader_bundle_version"]),
        "openal-soft-{}-bin.zip".format(pins["openal_soft_version"]),
        ".app-install.lock",
    }


def log_names(title_id: str) -> typing.Set[str]:
    """tmnf keeps play.log/configure.log; other titles use `<id>-<action>.log`."""
    bases: typing.Tuple[str, ...] = ("play.log", "configure.log")
    if title_id != "tmnf":
        bases = tuple("{}-{}".format(title_id, base) for base in bases)
    return {base + suffix for base in bases for suffix in LOG_SUFFIXES}


def _chain(path: Path) -> typing.Set[Identity]:
    """Identities of PATH (if it exists) and of every existing ancestor, symlinks followed."""
    found = set()
    for candidate in (path, *path.parents):
        try:
            found.add(_identity(os.stat(candidate)))
        except OSError:
            continue
    return found


class Limits:
    """Paths a removal must never equal or contain, and areas it must stay out of.

    Compared by (st_dev, st_ino), never by spelling: APFS is case-insensitive,
    so "games/tmnf" and "Games/TMNF" are the same folder.
    """

    def __init__(self, protected: typing.Sequence[Path], foreign: typing.Sequence[Path]):
        # A removal equals or contains a protected path exactly when its identity
        # is that path's own or one of its existing ancestors'.
        self.containing: typing.Dict[Identity, Path] = {}
        for path in protected:
            for identity in _chain(path):
                self.containing.setdefault(identity, path)
        self.foreign: typing.Dict[Identity, Path] = {}
        for area in foreign:
            try:
                self.foreign[_identity(os.stat(area))] = area
            except OSError:
                continue  # Nothing can be inside an area that does not exist.

    def check(self, entry: Entry) -> None:
        identity = entry.identity or _identity(os.lstat(entry.path))
        if identity in self.containing:
            raise ValueError(
                "Refusing to remove {}: it is or contains {}, which is kept".format(
                    entry.path, self.containing[identity]
                )
            )
        for ancestor in _chain(entry.path.parent):
            if ancestor in self.foreign:
                raise ValueError(
                    "Refusing to remove {}: it belongs to another title ({})".format(
                        entry.path, self.foreign[ancestor]
                    )
                )


def limits(
    title: registry.Title,
    titles: typing.Sequence[registry.Title],
    home: Path,
    support: Path,
    cache: Path,
    apps_dir: Path,
) -> Limits:
    protected = [
        home,
        home / "Games",
        title_root(title, home),
        support,
        support / "runtime",
        support / "launchers",
        support / "icons",
        cache,
        cache / "games",
        logs_dir(home),
        apps_dir,
    ]
    foreign = []
    own_chain = _chain(title_root(title, home))
    for other in titles:
        if other.id == title.id:
            continue
        root = title_root(other, home)
        protected += [root, root / "prefix", root / "UserData"]
        foreign += [root / "prefix", root / "UserData"]
        # Another title's root is foreign too, unless it holds this title's root
        # (tmnf's ~/Games/TMNF holds every other title's root).
        try:
            holds_ours = _identity(os.stat(root)) in own_chain
        except OSError:
            holds_ours = False
        if not holds_ours:
            foreign.append(root)
    return Limits(protected, foreign)


def logs_dir(home: Path) -> Path:
    return home / "Library" / "Logs" / "TMNF"


def tombstone_name(name: str) -> str:
    return ".{}.tmnf-removing.{}".format(name, secrets.token_hex(4))


def leftover_entries(directory: Path, name: str, kind: str) -> typing.List[Entry]:
    """Tombstones of NAME that an interrupted uninstall left in DIRECTORY.

    Names compare casefolded: a tombstone keeps the spelling the folder was
    reached by, and APFS treats "userdata" and "UserData" as one name.
    """
    if not directory.is_dir():
        return []
    entries = []
    for child in sorted(os.listdir(directory)):
        match = TOMBSTONE.fullmatch(child)
        if not match or match.group("name").casefold() != name.casefold():
            continue
        path = directory.resolve(strict=True) / child
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            entries.append(_remove(kind, path))
        else:
            entries.append(_keep(kind, path, "not a folder this hub left behind"))
    return entries


def app_entry(value: str, title: registry.Title, kind: str) -> typing.Optional[Entry]:
    """Remove only our complete wrapper for this title; anything else is kept."""
    path = _plain_absolute(value, "app")
    expected = title.app_name if kind == "game-app" else title.settings_app_name
    if path.name != expected:
        raise ValueError("App path {} does not end in {}".format(path, expected))
    if not os.path.lexists(path):
        return None
    contents = path / "Contents"
    marker = contents / ".tmnf-app"
    info = contents / "Info.plist"
    if path.is_symlink():
        return _keep(kind, path, "a symlink, not an app this hub installed")
    if not path.is_dir() or contents.is_symlink() or not contents.is_dir():
        return _keep(kind, path, "not an app this hub installed")
    if info.is_symlink() or not info.is_file():
        return _keep(kind, path, "not an app this hub installed")
    try:
        with info.open("rb") as stream:
            info_data = plistlib.load(stream)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return _keep(kind, path, "its Info.plist cannot be read")
    if not isinstance(info_data, dict):
        return _keep(kind, path, "its Info.plist cannot be read")
    identifier = info_data.get("CFBundleIdentifier")
    if marker.is_symlink() or not marker.is_file():
        if identifier == LEGACY_APP_ID:
            return _keep(kind, path, "an older launcher app; remove it yourself")
        return _keep(kind, path, "not an app this hub installed")
    wanted = title.bundle_id_game if kind == "game-app" else title.bundle_id_settings
    if identifier != wanted:
        # The identifier comes from a file anyone could have written: repr() it.
        return _keep(kind, path, "belongs to another app ({!r})".format(identifier))
    if not _owned(path):
        return _keep(kind, path, "owned by another user")
    return _remove(kind, path.parent.resolve(strict=True) / path.name)


def launcher_entry(value: str, support: Path, title: registry.Title) -> typing.Optional[Entry]:
    path = _plain_absolute(value, "launcher")
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        return _keep("launcher", path, "a symlink")
    resolved = managed_paths.safe_path(value)
    marker = resolved / ".tmnf-launcher"
    if resolved.name != title.id or not _same(resolved.parent, support / "launchers"):
        return _keep("launcher", path, "not this game's launcher folder")
    if marker.is_symlink() or not marker.is_file() or not resolved.is_dir():
        return _keep("launcher", path, "not a launcher folder this hub installed")
    if not _owned(resolved):
        return _keep("launcher", path, "owned by another user")
    return _remove("launcher", resolved)


def _regular_file(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def _installer_files(directory: Path, name: str) -> typing.List[Entry]:
    """NAME and its `.partial.XXXXXX` downloads in DIRECTORY (see download_verified)."""
    partial = re.compile(r"{}\.partial\.[A-Za-z0-9]{{6}}".format(re.escape(name)))
    entries = []
    for child in sorted(os.listdir(directory)):
        if child != name and not partial.fullmatch(child):
            continue
        path = directory / child
        if _regular_file(path):
            entries.append(_remove("cache", path))
        else:
            entries.append(_keep("cache", path, "not a regular file"))
    return entries


def cache_entries(title: registry.Title, cache: Path) -> typing.List[Entry]:
    """The title's cached installer, its partial downloads and its unpacked staging.

    tmnf's installer sits directly in the shared cache. Other titles download
    to `games/<id>`, which carries no ownership marker, so only the known
    files in it are removed and the folder itself only once it is empty.
    """
    target = installer_cache_path(title, cache)
    if target is None or not os.path.lexists(cache) or cache.is_symlink():
        return []
    name = target.name
    if title.id == "tmnf":
        if not name or name in shared_cache_names():
            raise ValueError("The cached installer name {!r} is shared by every title".format(name))
        resolved_cache = managed_paths.safe_path(str(cache))
        return _installer_files(resolved_cache, name) if resolved_cache.is_dir() else []
    folder = cache / "games" / title.id
    if not os.path.lexists(folder):
        return []
    if folder.is_symlink():
        return [_keep("cache", folder, "a symlink")]
    resolved = managed_paths.safe_path(str(folder))
    if not resolved.is_dir() or not _same(resolved.parent, cache / "games"):
        return [_keep("cache", folder, "not this game's cache folder")]
    entries = _installer_files(resolved, name)
    # unpack_inner_inno stages `${installer%.exe}.inner` next to the installer.
    staging = (name[: -len(".exe")] if name.endswith(".exe") else name) + ".inner"
    inner = resolved / staging
    if os.path.lexists(inner):
        info = os.lstat(inner)
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            entries.append(_remove("cache", inner))
        else:
            entries.append(_keep("cache", inner, "not a folder this hub unpacked"))
    entries += leftover_entries(resolved, staging, "cache")
    if _owned(resolved):
        entries.append(_planned("prune", "cache", resolved))
    return entries


def log_entries(title: registry.Title, home: Path) -> typing.List[Entry]:
    """Exact per-title log names and diagnostics reports; launch locks stay."""
    directory = logs_dir(home)
    if not os.path.lexists(directory):
        return []
    if directory.is_symlink() or not directory.is_dir():
        return [_keep("logs", directory, "the logs folder is not a plain folder")]
    names = log_names(title.id)
    report = re.compile(r"diagnostics-{}-\d{{8}}-\d{{6}}\.zip".format(re.escape(title.id)))
    resolved = managed_paths.safe_path(str(directory))
    entries = []
    for child in sorted(os.listdir(resolved)):
        if child not in names and not report.fullmatch(child):
            continue
        path = resolved / child
        if _regular_file(path):
            entries.append(_remove("logs", path))
        else:
            entries.append(_keep("logs", path, "not a regular file"))
    return entries


def icon_entry(title: registry.Title, support: Path) -> typing.Optional[Entry]:
    path = support / "icons" / "{}.icns".format(title.id)
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not _regular_file(path):
        return _keep("icon", path, "not a regular file")
    return _remove("icon", managed_paths.safe_path(str(path)))


def _external_saves(prefix: Path, userdata: Path, warnings: typing.List[str]) -> typing.List[Entry]:
    """Documents locations outside the prefix and UserData: reported, never removed."""
    locations: typing.List[Path] = []
    try:
        locations += list(managed_paths.profile_documents(prefix))
    except (OSError, ValueError) as error:
        warnings.append("Could not list the prefix's Documents folders ({}).".format(error))
    try:
        locations += managed_paths.redirected_documents(prefix)
    except (OSError, ValueError) as error:
        warnings.append(
            "Could not read where the prefix keeps Documents ({}). Files outside the prefix "
            "and the saves folder are never removed.".format(error)
        )
    own = set()
    for path in (prefix, userdata):
        try:
            own.add(_identity(os.stat(path)))
        except OSError:
            continue
    entries: typing.List[Entry] = []
    seen: typing.Set[Identity] = set()
    for location in locations:
        try:
            resolved = location.resolve(strict=True)
            identity = _identity(os.stat(resolved))
        except OSError:
            continue
        # Inside the prefix or the saves folder (by identity, at any depth): not elsewhere.
        if identity in seen or own & _chain(resolved):
            continue
        seen.add(identity)
        entries.append(_keep("saves-elsewhere", resolved, "outside the managed folders"))
    return entries


def prefix_entries(
    prefix_value: str,
    userdata_value: str,
    title: registry.Title,
    include_saves: bool,
    warnings: typing.List[str],
) -> typing.List[Entry]:
    if not os.path.lexists(prefix_value.rstrip("/") or "/"):
        return []
    if not include_saves:
        # Saves inside the prefix block this removal, exactly as before.
        prefix = managed_paths.check_uninstall(prefix_value, userdata_value, title=title.id)
        return [_remove("prefix", prefix)]
    managed_paths.check_layout([prefix_value, userdata_value])
    managed_paths.check_prefix(prefix_value, title=title.id)
    prefix = managed_paths.safe_path(prefix_value)
    userdata = Path(userdata_value).resolve()
    return [_remove("prefix", prefix)] + _external_saves(prefix, userdata, warnings)


def userdata_entries(
    value: str,
    title: registry.Title,
    titles: typing.Sequence[registry.Title],
    home: Path,
    include_saves: bool,
) -> typing.List[Entry]:
    """The whole default UserData folder: it is this title's Windows Documents folder.

    save_dirs overlap between titles (tmu also lists TmForever), so removing
    save folders from a shared or custom folder could delete another title's
    saves. Only the title's own default folder is ever removed.
    """
    path = _plain_absolute(value, "saves")
    if not os.path.lexists(path):
        return []
    if not include_saves:
        return [_keep("saves", path, "saves are kept")]
    if path.is_symlink():
        raise ValueError("The saves folder is a symlink: {}".format(path))
    resolved = managed_paths.safe_path(value)
    if not _same(resolved, default_userdata(title, home)):
        raise ValueError(
            "Your saves are in a custom folder ({}) that this uninstall does not remove. "
            "Remove it yourself, or uninstall without removing saves.".format(path)
        )
    if not resolved.is_dir() or not _owned(resolved):
        raise ValueError("The saves folder is not a folder you own: {}".format(path))
    ours = _identity(os.stat(resolved))
    for other in titles:
        if other.id == title.id:
            continue
        other_prefix = title_root(other, home) / "prefix"
        if not other_prefix.is_dir():
            continue
        try:
            # Both the profile folders and any user.reg redirection of Documents.
            documents = list(managed_paths.profile_documents(other_prefix))
            documents += managed_paths.redirected_documents(other_prefix)
            chains = [_chain(location.resolve(strict=True)) for location in documents]
        except (OSError, ValueError) as error:
            raise ValueError(
                "Cannot check whether {} uses {}: {}".format(other.name, path, error)
            ) from error
        if any(ours in chain for chain in chains):
            raise ValueError("{} also keeps its saves in {}".format(other.name, path))
    return [_remove("saves", resolved)]


def _saves_kept(found: typing.List[Entry], userdata: Path) -> typing.List[Entry]:
    """Keeping saves never deletes them: an unfinished uninstall's saves go back.

    The first saves tombstone is renamed back to the saves folder when that
    name is free; any other is reported and kept.
    """
    entries: typing.List[Entry] = []
    restoring = os.path.lexists(userdata)  # True once the name is taken
    for entry in found:
        if entry.verb == "remove" and not restoring:
            restoring = True
            entries.append(entry._replace(verb="restore", reason=UNFINISHED_SAVES, target=userdata))
        elif entry.verb == "remove":
            entries.append(_keep("saves", entry.path, UNFINISHED_SAVES))
        else:
            entries.append(entry)
    return entries


def _leftovers(
    title: registry.Title,
    home: Path,
    prefix: Path,
    userdata: Path,
    apps: typing.Sequence[Path],
    launcher: Path,
    support: Path,
    include_saves: bool,
) -> typing.List[Entry]:
    """Tombstones of every folder this title's uninstall renames before deleting.

    Each kind is looked for only where its live folder would pass that
    folder's own ownership rules: prefix and saves tombstones only in this
    title's root, launcher tombstones only as `launchers/<id>`, app
    tombstones only under this title's registry app names.
    """
    entries = []
    root = title_root(title, home)
    for path, kind in ((prefix, "prefix"), (userdata, "saves")):
        if not _same(path.parent, root):
            continue
        found = leftover_entries(path.parent, path.name, kind)
        if kind == "saves" and not include_saves:
            found = _saves_kept(found, userdata)
        entries += found
    for path, kind in ((apps[0], "game-app"), (apps[1], "settings-app")):
        parent = path.parent
        if os.path.lexists(parent) and not parent.is_symlink():
            entries += leftover_entries(parent, path.name, kind)
    if launcher.name == title.id and _same(launcher.parent, support / "launchers"):
        entries += leftover_entries(launcher.parent, title.id, "launcher")
    return entries


def _other_titles_documents(
    entries: typing.Sequence[Entry],
    title: registry.Title,
    titles: typing.Sequence[registry.Title],
    home: Path,
) -> None:
    """Refuse removing a folder another title keeps its Documents (saves) in.

    Applies to the prefix as well as the saves folder: a profile link or a
    user.reg redirection in another prefix may point into either.
    """
    removals = [e for e in entries if e.verb != "keep" and e.kind in ("prefix", "saves")]
    if not removals:
        return
    for other in titles:
        if other.id == title.id:
            continue
        other_prefix = title_root(other, home) / "prefix"
        if not other_prefix.is_dir():
            continue
        try:
            documents = list(managed_paths.profile_documents(other_prefix))
            documents += managed_paths.redirected_documents(other_prefix)
            chains = [_chain(location.resolve(strict=True)) for location in documents]
        except (OSError, ValueError) as error:
            raise ValueError(
                "Cannot check where {} keeps its saves: {}".format(other.name, error)
            ) from error
        for entry in removals:
            if any(entry.identity in chain for chain in chains):
                raise ValueError("{} keeps its saves inside {}".format(other.name, entry.path))


def plan(
    prefix: str,
    userdata: str,
    app: str,
    settings_app: str,
    launcher: str,
    support: str,
    cache: str,
    include_saves: bool = False,
    warnings: typing.Optional[typing.List[str]] = None,
) -> typing.List[Entry]:
    """Validate every path and return what to remove and what is kept, in removal order.

    Raises ValueError/OSError/RuntimeError (or RegistryError) when anything is
    uncertain. The caller must hold the prefix and app operation locks.
    """
    warnings = warnings if warnings is not None else []
    title, titles = load_titles()
    home = Path.home().resolve()
    app_path = _plain_absolute(app, "app")
    settings_path = _plain_absolute(settings_app, "app")
    if not _same(app_path.parent, settings_path.parent) and (
        app_path.parent.resolve() != settings_path.parent.resolve()
    ):
        raise ValueError("The game and settings apps must be in the same folder")
    support_path = _plain_absolute(support, "support").resolve()
    cache_path = _plain_absolute(cache, "cache")
    bounds = limits(
        title, titles, home, support_path, cache_path.resolve(), app_path.parent.resolve()
    )
    entries: typing.List[Entry] = []
    for value, kind in ((app, "game-app"), (settings_app, "settings-app")):
        entry = app_entry(value, title, kind)
        if entry is not None:
            entries.append(entry)
    entry = launcher_entry(launcher, support_path, title)
    if entry is not None:
        entries.append(entry)
    entries += prefix_entries(prefix, userdata, title, include_saves, warnings)
    entries += userdata_entries(userdata, title, titles, home, include_saves)
    entries += _leftovers(
        title,
        home,
        _plain_absolute(prefix, "prefix"),
        _plain_absolute(userdata, "saves"),
        (app_path, settings_path),
        _plain_absolute(launcher, "launcher"),
        support_path,
        include_saves,
    )
    entries += cache_entries(title, cache_path)
    entries += log_entries(title, home)
    entry = icon_entry(title, support_path)
    if entry is not None:
        entries.append(entry)
    for entry in entries:
        # Fail closed: a resolved path or reason that could break a line of
        # output is never shown, let alone removed.
        if not _printable(str(entry.path)) or not _printable(entry.reason):
            raise ValueError("Unsafe path in the uninstall plan: {!r}".format(str(entry.path)))
        if entry.verb != "keep":
            bounds.check(entry)
    _other_titles_documents(entries, title, titles, home)
    return entries


def _same_volume(directory: int, name: str, device: int) -> None:
    """Refuse a tree with another volume mounted inside it; nothing is crossed.

    Walks from the parent's descriptor without following symlinks.
    """
    for root, directories, files, root_fd in os.fwalk(name, dir_fd=directory):
        for child in directories + files:
            if os.stat(child, dir_fd=root_fd, follow_symlinks=False).st_dev != device:
                raise ValueError(
                    "{!r} has another volume mounted inside it ({!r}); unmount it and "
                    "uninstall again".format(name, os.path.join(root, child))
                )


def _delete_tree(directory: int, parent: Path, name: str, expected: Identity) -> None:
    """Delete folder NAME inside the open DIRECTORY, which holds it under that name."""
    if sys.version_info >= (3, 11):
        # Fully descriptor-relative: no path is resolved again after the checks.
        if _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != expected:
            raise ValueError("{} changed after it was checked".format(parent / name))
        _same_volume(directory, name, expected[0])
        shutil.rmtree(name, dir_fd=directory)
        return
    # Python 3.9 and 3.10 have no rmtree(dir_fd=). The identity is checked by path
    # immediately before; rmtree itself never follows symlinks inside the tree.
    # Residual risk: a same-user process swapping an ancestor of PARENT between
    # this check and rmtree's own open. The bundled Python (3.12) takes the
    # branch above.
    _same_volume(directory, name, expected[0])
    if _identity(os.lstat(parent / name)) != expected:
        raise ValueError("{} changed after it was checked".format(parent / name))
    shutil.rmtree(parent / name)


def _apply(entry: Entry) -> bool:
    """Carry out one planned entry; False when it was already gone or not empty."""
    assert entry.identity is not None and entry.parent is not None
    parent = entry.path.parent
    # O_NOFOLLOW refuses a parent swapped for a symlink; the identity check
    # refuses one swapped for another folder anywhere along its path.
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_info = os.fstat(descriptor)
        if _identity(parent_info) != entry.parent:
            raise ValueError("{} changed after it was checked".format(parent))
        try:
            info = os.stat(entry.path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if _identity(info) != entry.identity:
            raise ValueError("{} changed after it was checked".format(entry.path))
        if info.st_dev != parent_info.st_dev:
            # A mount point: deleting its contents would empty the whole volume.
            raise ValueError("{} {}".format(entry.path, MOUNTED))
        if entry.verb == "restore":
            assert entry.target is not None and entry.target.parent == parent
            try:
                os.stat(entry.target.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return False  # The saves folder exists again; the tombstone stays.
            os.rename(
                entry.path.name, entry.target.name, src_dir_fd=descriptor, dst_dir_fd=descriptor
            )
            return True
        if entry.verb == "prune":
            try:
                os.rmdir(entry.path.name, dir_fd=descriptor)
            except OSError as error:
                if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    return False
                raise
            return True
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(entry.path.name, dir_fd=descriptor)
            return True
        name = entry.path.name
        if not TOMBSTONE.fullmatch(name):
            name = tombstone_name(name)
            os.rename(entry.path.name, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        _delete_tree(descriptor, parent, name, entry.identity)
        return True
    finally:
        os.close(descriptor)


def remove(
    entries: typing.Sequence[Entry],
    report: typing.Optional[typing.Callable[[str], None]] = None,
) -> int:
    """Carry out every planned entry in order and report each; returns how many acted."""
    report = report or print
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError(
            "This Python cannot remove folders without following symlinks; nothing was removed"
        )
    done = 0
    for entry in entries:
        if entry.verb == "keep":
            report("Kept {}".format(entry.describe()))
        elif _apply(entry):
            done += 1
            if entry.verb == "restore":
                report("Restored {} from an unfinished uninstall".format(entry.target))
            else:
                report("Removed {}".format(entry.path))
    return done


PREVIEW_LABELS = {
    "prefix": "Wine prefix and game files",
    "game-app": "Game app",
    "settings-app": "Settings app",
    "launcher": "Launcher files",
    "leftover": "Files left by an unfinished uninstall",
    "cache": "Cached installer",
    "logs": "Logs for this game",
    "icon": "Game icon",
}
# Any of these makes the title uninstallable; cache, logs and icon alone do not.
REMOVABLE_KINDS = ("prefix", "game-app", "settings-app", "launcher", "leftover")


def preview(title: registry.Title, paths: "TitlePaths") -> dict:
    """What an uninstall would remove, for the hub dialog. Never raises.

    Existence and marker checks only; `plan` performs the full validation
    (save-tree walk, nesting, registry completeness) when the action runs.
    """
    items: typing.List[dict] = []

    def add(kind: str, path: Path) -> None:
        items.append({"kind": kind, "label": PREVIEW_LABELS[kind], "path": str(path)})

    failures = (OSError, ValueError, RuntimeError, registry.RegistryError)

    def attempt(function, *arguments):
        try:
            return function(*arguments)
        except failures:
            return None

    def owned_prefix() -> bool:
        try:
            managed_paths.check_prefix(str(paths.prefix), title=title.id)
        except failures:
            return False
        return True

    # A mount point is kept by plan, so the dialog does not list it either.
    if os.path.lexists(paths.prefix) and owned_prefix() and not _mount_point(paths.prefix):
        add("prefix", paths.prefix)
    for value, kind in ((paths.app, "game-app"), (paths.settings_app, "settings-app")):
        entry = attempt(app_entry, str(value), title, kind)
        if entry is not None and entry.verb == "remove":
            add(kind, value)
    entry = attempt(launcher_entry, str(paths.launcher), paths.support.resolve(), title)
    if entry is not None and entry.verb == "remove":
        add("launcher", paths.launcher)
    leftovers = attempt(
        _leftovers,
        title,
        Path.home(),
        paths.prefix,
        paths.userdata,
        (paths.app, paths.settings_app),
        paths.launcher,
        paths.support,
        True,
    )
    unfinished_saves = None
    for entry in leftovers or ():
        if entry.verb != "remove":
            continue
        if entry.kind == "saves":
            # Saves still: the dialog offers keeping (restoring) or removing them.
            unfinished_saves = unfinished_saves or str(entry.path)
        else:
            add("leftover", entry.path)
    cache = [e for e in attempt(cache_entries, title, paths.cache) or () if e.verb == "remove"]
    if cache:
        add("cache", cache[0].path)
    logs = [e for e in attempt(log_entries, title, Path.home()) or () if e.verb == "remove"]
    if logs:
        add("logs", logs_dir(Path.home()))
    entry = attempt(icon_entry, title, paths.support)
    if entry is not None and entry.verb == "remove":
        add("icon", entry.path)
    saves = unfinished_saves
    userdata = paths.userdata
    if userdata.is_dir() and not userdata.is_symlink() and not _mount_point(userdata):
        saves = str(userdata)
    return {"items": items, "saves": saves}


def removable(removal: dict) -> bool:
    return removal.get("saves") is not None or any(
        item.get("kind") in REMOVABLE_KINDS for item in removal.get("items", ())
    )


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan", "remove"))
    parser.add_argument("--include-saves", action="store_true")
    for name in ("prefix", "userdata", "app", "settings_app", "launcher", "support", "cache"):
        parser.add_argument(name)
    args = parser.parse_args(argv)
    warnings: typing.List[str] = []
    try:
        entries = plan(
            args.prefix,
            args.userdata,
            args.app,
            args.settings_app,
            args.launcher,
            args.support,
            args.cache,
            include_saves=args.include_saves,
            warnings=warnings,
        )
        for warning in warnings:
            print("warning: {}".format(warning), file=sys.stderr)
        if args.command == "remove":
            if not remove(entries):
                print("Nothing was left to remove.")
            return 0
    except (OSError, ValueError, RuntimeError, registry.RegistryError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    removals = [entry for entry in entries if entry.verb == "remove"]
    restores = [entry for entry in entries if entry.verb == "restore"]
    kept = [entry for entry in entries if entry.verb == "keep"]
    if not removals and not restores:
        print("Nothing is left to remove.")
        return NOTHING_TO_REMOVE
    if removals:
        print("This removes:")
        for entry in removals:
            print("  {}".format(entry.describe()))
    if restores:
        print("This restores:")
        for entry in restores:
            print("  {}".format(entry.describe()))
    if kept:
        print("This keeps:")
        for entry in kept:
            print("  {}".format(entry.describe()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
