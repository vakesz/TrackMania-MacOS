"""Computed per-title installation paths.

Mirrors the environment contract from src/shell/lib/common.sh exactly: explicit
environment overrides win, then title-specific defaults, then user-level
defaults. Both entry points (shell first, hub second) must agree so the hub's
state view describes the same installation the shell manages.
"""

from __future__ import annotations

import os
import pathlib
import typing

from trackmania_mac.install import version as project_version

from . import registry


# Title-derived values. Hub actions run with these scrubbed from the
# environment (see run.py), so the state view must ignore them too; otherwise
# the hub would describe a different installation than the one an action
# manages. Only the shared TRACKMANIA_MAC_APPS_DIR, TRACKMANIA_MAC_SUPPORT and TRACKMANIA_MAC_CACHE apply.
# TRACKMANIA_MAC_TITLES_DIR is deliberately absent: the registry directory is honored by
# every consumer (shell, snapshot, run).
TITLE_ENV = (
    "TRACKMANIA_MAC_ROOT",
    "TRACKMANIA_MAC_PREFIX",
    "TRACKMANIA_MAC_APP",
    "TRACKMANIA_MAC_SETTINGS_APP",
    "TRACKMANIA_MAC_USERDATA",
    "TRACKMANIA_MAC_RUNTIME",
    "TRACKMANIA_MAC_LAUNCHER",
    "TRACKMANIA_MAC_WINE",
)


# The command-line entry point, src/shell/cli/<CLI_NAME> (TRACKMANIA_MAC_CLI_NAME in common.sh).
CLI_NAME = "trackmania-mac"


def data_dir(home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    """The one folder that holds every default location (see common.sh)."""
    return (home if home is not None else pathlib.Path.home()) / "Library" / "TrackManiaMacOS"


def title_root(title_id: str, home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    """A title's default root: its prefix and its operation lock."""
    return data_dir(home) / "games" / title_id


def saves_dir(home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    return data_dir(home) / "saves"


def default_userdata(title_id: str, home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    """A title's default saves folder, the prefix's Windows Documents folder."""
    return saves_dir(home) / title_id


def default_support(home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    """Holds runtime/, launchers/ and icons/; TRACKMANIA_MAC_SUPPORT overrides it."""
    return data_dir(home)


def default_cache(home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    return data_dir(home) / "cache"


def log_dir(home: typing.Optional[pathlib.Path] = None) -> pathlib.Path:
    return data_dir(home) / "logs"


def support_dir() -> pathlib.Path:
    """runtime/, launchers/ and icons/: TRACKMANIA_MAC_SUPPORT, else the data folder."""
    return pathlib.Path(os.environ.get("TRACKMANIA_MAC_SUPPORT") or default_support())


def cache_dir() -> pathlib.Path:
    """Downloads and build workspaces: TRACKMANIA_MAC_CACHE, else cache/ in the data folder."""
    return pathlib.Path(os.environ.get("TRACKMANIA_MAC_CACHE") or default_cache())


class TitlePaths(typing.NamedTuple):
    title_id: str
    root: pathlib.Path
    prefix: pathlib.Path
    userdata: pathlib.Path
    support: pathlib.Path
    runtime: pathlib.Path
    cache: pathlib.Path
    apps_dir: pathlib.Path
    app: pathlib.Path
    settings_app: pathlib.Path
    launcher: pathlib.Path
    game_dir: pathlib.Path
    game_exe: pathlib.Path
    launcher_exe: pathlib.Path

    @property
    def icon(self) -> pathlib.Path:
        """Per-title cached .icns extracted from the official game executable."""
        return self.support / "icons" / f"{self.title_id}.icns"

    def bundled_icon(self, suffix: str) -> pathlib.Path:
        """Original per-title artwork shipped with the project (PNG in packaged builds)."""
        return registry.project_dir() / "assets" / "titles" / f"{self.title_id}.{suffix}"

    def resolve_icon(self) -> typing.Tuple[typing.Optional[pathlib.Path], typing.Optional[str]]:
        """Game icon, else bundled PNG, else bundled SVG, as (path, source)."""
        if self.icon.is_file():
            return self.icon, "game"
        for suffix in ("png", "svg"):
            candidate = self.bundled_icon(suffix)
            if candidate.is_file():
                return candidate, "bundled"
        return None, None

    @property
    def runtime_wine(self) -> pathlib.Path:
        return self.runtime / "bin" / "wine"


def runtime_version() -> str:
    """The release version; OSError or VersionError when it cannot be read."""
    return project_version.read(registry.project_dir())


def compute(title: registry.Title) -> TitlePaths:
    version = runtime_version()
    root = title_root(title.id)
    user_data = default_userdata(title.id)
    prefix = root / "prefix"

    support = support_dir()
    runtime = support / "runtime" / version
    cache = cache_dir()

    apps_env = os.environ.get("TRACKMANIA_MAC_APPS_DIR")
    if apps_env:
        apps_dir = pathlib.Path(apps_env)
    elif os.access("/Applications", os.W_OK):
        apps_dir = pathlib.Path("/Applications")
    else:
        apps_dir = pathlib.Path.home() / "Applications"

    app = apps_dir / title.app_name
    settings_app = app.parent / title.settings_app_name
    launcher = support / "launchers" / title.id

    game_dir = prefix / "drive_c" / "Program Files (x86)" / title.game_dir_name
    return TitlePaths(
        title_id=title.id,
        root=root,
        prefix=prefix,
        userdata=user_data,
        support=support,
        runtime=runtime,
        cache=cache,
        apps_dir=apps_dir,
        app=app,
        settings_app=settings_app,
        launcher=launcher,
        game_dir=game_dir,
        game_exe=game_dir / title.game_exe,
        launcher_exe=game_dir / title.launcher_exe,
    )


def local_runtime_fallback() -> typing.Optional[pathlib.Path]:
    candidate = registry.project_dir() / ".build" / "release" / "runtime"
    if (candidate / "bin" / "wine").is_file():
        return candidate
    return None


def wine_binary(paths: TitlePaths) -> typing.Optional[pathlib.Path]:
    if (paths.runtime / "bin" / "wine").is_file():
        return paths.runtime / "bin" / "wine"
    fallback = local_runtime_fallback()
    if fallback:
        return fallback / "bin" / "wine"
    return None
