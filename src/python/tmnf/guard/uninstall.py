"""Per-title uninstall plan: every path one title's uninstall may remove.

`plan` validates and prints each path to remove, resolved exactly once; the
shell removes those paths and nothing else. `preview` is the cheap read-only
view the hub snapshot shows in the confirmation dialog. Both use the same rules.

What a title owns:
- its prefix (`check_prefix` accepts it) and, only with include_saves, its
  default UserData folder, which is the prefix's Windows Documents folder;
- its two app wrappers, only when they carry our `.tmnf-app` marker and this
  title's bundle identifier;
- its launcher support directory, cached installer, logs and extracted icon.

Never removed: a title root (tmnf's root `~/Games/TMNF` holds the other
titles' roots and every root holds its operation lock), the runtime, shared
downloads, another title's paths, or anything outside the managed roots.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import stat
import sys
import typing
from pathlib import Path

from tmnf.hub import registry

from . import managed_paths

if typing.TYPE_CHECKING:
    from tmnf.hub.paths import TitlePaths

LEGACY_APP_ID = "local.tmnf.launcher"
LOG_SUFFIXES = ("", ".1", ".2", ".3")


class Entry(typing.NamedTuple):
    verb: str  # "remove" or "keep"
    kind: str
    path: Path
    reason: str = ""

    def line(self) -> str:
        if self.verb == "remove":
            return "remove\t{}\t{}".format(self.kind, self.path)
        return "keep\t{}\t{}\t{}".format(self.kind, self.reason, self.path)


def _keep(kind: str, path: Path, reason: str) -> Entry:
    return Entry("keep", kind, path, reason)


def _plain_absolute(value: str, what: str) -> Path:
    if (
        not value.startswith("/")
        or any(part in (".", "..") for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Unsafe {} path: {!r}".format(what, value))
    return Path(value.rstrip("/") or "/")


def _owned(path: Path) -> bool:
    return os.lstat(path).st_uid == os.getuid()


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


class Limits(typing.NamedTuple):
    """Paths a removal must never equal or contain, and areas it must stay out of."""

    protected: typing.Tuple[Path, ...]
    foreign: typing.Tuple[Path, ...]

    def check(self, path: Path) -> None:
        for other in self.protected:
            if path == other or path in other.parents:
                raise ValueError(
                    "Refusing to remove {}: it is or contains {}, which is kept".format(path, other)
                )
        for area in self.foreign:
            if path == area or area in path.parents:
                raise ValueError(
                    "Refusing to remove {}: it belongs to another title ({})".format(path, area)
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
    for other in titles:
        if other.id == title.id:
            continue
        root = title_root(other, home)
        protected += [root, root / "prefix", root / "UserData"]
        foreign += [root / "prefix", root / "UserData"]
    return Limits(
        tuple(path.resolve() for path in protected), tuple(path.resolve() for path in foreign)
    )


def logs_dir(home: Path) -> Path:
    return home / "Library" / "Logs" / "TMNF"


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
            identifier = plistlib.load(stream).get("CFBundleIdentifier")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return _keep(kind, path, "its Info.plist cannot be read")
    if marker.is_symlink() or not marker.is_file():
        if identifier == LEGACY_APP_ID:
            return _keep(kind, path, "an older launcher app; remove it yourself")
        return _keep(kind, path, "not an app this hub installed")
    wanted = title.bundle_id_game if kind == "game-app" else title.bundle_id_settings
    if identifier != wanted:
        return _keep(kind, path, "belongs to another app ({})".format(identifier))
    if not _owned(path):
        return _keep(kind, path, "owned by another user")
    return Entry("remove", kind, path.parent.resolve(strict=True) / path.name)


def launcher_entry(value: str, support: Path, title: registry.Title) -> typing.Optional[Entry]:
    path = _plain_absolute(value, "launcher")
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        return _keep("launcher", path, "a symlink")
    resolved = managed_paths.safe_path(value)
    marker = resolved / ".tmnf-launcher"
    if resolved.name != title.id or resolved.parent != support / "launchers":
        return _keep("launcher", path, "not this game's launcher folder")
    if marker.is_symlink() or not marker.is_file() or not resolved.is_dir():
        return _keep("launcher", path, "not a launcher folder this hub installed")
    if not _owned(resolved):
        return _keep("launcher", path, "owned by another user")
    return Entry("remove", "launcher", resolved)


def _regular_file(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


def cache_entries(title: registry.Title, cache: Path) -> typing.List[Entry]:
    """The title's cached installer: a file in the shared cache for tmnf, else a folder."""
    target = installer_cache_path(title, cache)
    if target is None or not os.path.lexists(cache):
        return []
    if title.id != "tmnf":
        folder = cache / "games" / title.id
        if not os.path.lexists(folder):
            return []
        if folder.is_symlink():
            return [_keep("cache", folder, "a symlink")]
        resolved = managed_paths.safe_path(str(folder))
        if resolved != cache.resolve() / "games" / title.id or not resolved.is_dir():
            return [_keep("cache", folder, "not this game's cache folder")]
        if not _owned(resolved):
            return [_keep("cache", folder, "owned by another user")]
        return [Entry("remove", "cache", resolved)]
    name = target.name
    if not name or name in shared_cache_names():
        raise ValueError("The cached installer name {!r} is shared by every title".format(name))
    resolved_cache = managed_paths.safe_path(str(cache)) if not cache.is_symlink() else None
    if resolved_cache is None or not resolved_cache.is_dir():
        return []
    partial = re.compile(r"^{}\.partial\.[A-Za-z0-9]{{6}}$".format(re.escape(name)))
    entries = []
    for child in sorted(os.listdir(resolved_cache)):
        if child != name and not partial.match(child):
            continue
        path = resolved_cache / child
        if _regular_file(path):
            entries.append(Entry("remove", "cache", path))
        else:
            entries.append(_keep("cache", path, "not a regular file"))
    return entries


def log_entries(title: registry.Title, home: Path) -> typing.List[Entry]:
    """Exact per-title log names and diagnostics reports; launch locks stay."""
    directory = logs_dir(home)
    if not os.path.lexists(directory):
        return []
    if directory.is_symlink() or not directory.is_dir():
        return [_keep("logs", directory, "the logs folder is not a plain folder")]
    names = log_names(title.id)
    report = re.compile(r"^diagnostics-{}-\d{{8}}-\d{{6}}\.zip$".format(re.escape(title.id)))
    resolved = managed_paths.safe_path(str(directory))
    entries = []
    for child in sorted(os.listdir(resolved)):
        if child not in names and not report.match(child):
            continue
        path = resolved / child
        if _regular_file(path):
            entries.append(Entry("remove", "logs", path))
        else:
            entries.append(_keep("logs", path, "not a regular file"))
    return entries


def icon_entry(title: registry.Title, support: Path) -> typing.Optional[Entry]:
    path = support / "icons" / "{}.icns".format(title.id)
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not _regular_file(path):
        return _keep("icon", path, "not a regular file")
    return Entry("remove", "icon", managed_paths.safe_path(str(path)))


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
    entries: typing.List[Entry] = []
    seen = set()
    for location in locations:
        try:
            resolved = location.resolve(strict=True)
        except OSError:
            continue
        if resolved == prefix or prefix in resolved.parents:
            continue
        if resolved == userdata or userdata in resolved.parents or resolved in seen:
            continue
        seen.add(resolved)
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
        return [Entry("remove", "prefix", prefix)]
    managed_paths.check_layout([prefix_value, userdata_value])
    managed_paths.check_prefix(prefix_value, title=title.id)
    prefix = managed_paths.safe_path(prefix_value)
    userdata = Path(userdata_value).resolve()
    return [Entry("remove", "prefix", prefix)] + _external_saves(prefix, userdata, warnings)


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
    if resolved != default_userdata(title, home).resolve():
        raise ValueError(
            "Your saves are in a custom folder ({}) that this uninstall does not remove. "
            "Remove it yourself, or uninstall without removing saves.".format(path)
        )
    if not resolved.is_dir() or not _owned(resolved):
        raise ValueError("The saves folder is not a folder you own: {}".format(path))
    for other in titles:
        if other.id == title.id:
            continue
        other_prefix = title_root(other, home) / "prefix"
        if not other_prefix.is_dir():
            continue
        try:
            documents = list(managed_paths.profile_documents(other_prefix))
            targets = [location.resolve(strict=True) for location in documents]
        except (OSError, ValueError) as error:
            raise ValueError(
                "Cannot check whether {} uses {}: {}".format(other.name, path, error)
            ) from error
        if any(target == resolved or resolved in target.parents for target in targets):
            raise ValueError("{} also keeps its saves in {}".format(other.name, path))
    return [Entry("remove", "saves", resolved)]


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
    if app_path.parent.resolve() != settings_path.parent.resolve():
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
    entries += cache_entries(title, cache_path)
    entries += log_entries(title, home)
    entry = icon_entry(title, support_path)
    if entry is not None:
        entries.append(entry)
    for entry in entries:
        if entry.verb == "remove":
            bounds.check(entry.path)
    return entries


PREVIEW_LABELS = {
    "prefix": "Wine prefix and game files",
    "game-app": "Game app",
    "settings-app": "Settings app",
    "launcher": "Launcher files",
    "cache": "Cached installer",
    "logs": "Logs for this game",
    "icon": "Game icon",
}
# Any of these makes the title uninstallable; cache, logs and icon alone do not.
REMOVABLE_KINDS = ("prefix", "game-app", "settings-app", "launcher")


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

    if os.path.lexists(paths.prefix) and owned_prefix():
        add("prefix", paths.prefix)
    for value, kind in ((paths.app, "game-app"), (paths.settings_app, "settings-app")):
        entry = attempt(app_entry, str(value), title, kind)
        if entry is not None and entry.verb == "remove":
            add(kind, value)
    entry = attempt(launcher_entry, str(paths.launcher), paths.support.resolve(), title)
    if entry is not None and entry.verb == "remove":
        add("launcher", paths.launcher)
    cache = [e for e in attempt(cache_entries, title, paths.cache) or () if e.verb == "remove"]
    if cache:
        # tmnf's installer is one file in the shared cache; other titles own a folder.
        shown = installer_cache_path(title, paths.cache) if title.id == "tmnf" else None
        add("cache", shown or cache[0].path)
    logs = [e for e in attempt(log_entries, title, Path.home()) or () if e.verb == "remove"]
    if logs:
        add("logs", logs_dir(Path.home()))
    entry = attempt(icon_entry, title, paths.support)
    if entry is not None and entry.verb == "remove":
        add("icon", entry.path)
    saves = None
    userdata = paths.userdata
    if userdata.is_dir() and not userdata.is_symlink():
        saves = str(userdata)
    return {"items": items, "saves": saves}


def removable(removal: dict) -> bool:
    return removal.get("saves") is not None or any(
        item.get("kind") in REMOVABLE_KINDS for item in removal.get("items", ())
    )


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("plan",))
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
    except (OSError, ValueError, RuntimeError, registry.RegistryError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    for warning in warnings:
        print("warning: {}".format(warning), file=sys.stderr)
    for entry in entries:
        print(entry.line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
