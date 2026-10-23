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

from . import registry


# Title-derived values. Hub actions run with these scrubbed from the
# environment (see run.py), so the state view must ignore them too; otherwise
# the hub would describe a different installation than the one an action
# manages. Only the shared TMNF_APPS_DIR, TMNF_SUPPORT and TMNF_CACHE apply.
# TMNF_TITLES_DIR is deliberately absent: the registry directory is honored by
# every consumer (shell, snapshot, run).
TITLE_ENV = ('TMNF_ROOT', 'TMNF_PREFIX', 'TMNF_APP', 'TMNF_SETTINGS_APP',
             'TMNF_USERDATA', 'TMNF_RUNTIME', 'TMNF_LAUNCHER', 'TMNF_WINE')


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
        return self.support / 'icons' / f'{self.title_id}.icns'

    def bundled_icon(self, suffix: str) -> pathlib.Path:
        """Original per-title artwork shipped with the project (PNG in packaged builds)."""
        return registry.project_dir() / 'assets' / 'titles' / f'{self.title_id}.{suffix}'

    def resolve_icon(self) -> typing.Tuple[typing.Optional[pathlib.Path], typing.Optional[str]]:
        """Game icon, else bundled PNG, else bundled SVG, as (path, source)."""
        if self.icon.is_file():
            return self.icon, 'game'
        for suffix in ('png', 'svg'):
            candidate = self.bundled_icon(suffix)
            if candidate.is_file():
                return candidate, 'bundled'
        return None, None

    @property
    def runtime_wine(self) -> pathlib.Path:
        return self.runtime / 'bin' / 'wine'


def runtime_version() -> str:
    return (registry.project_dir() / 'VERSION').read_text().strip()


def compute(title: registry.Title) -> TitlePaths:
    version = runtime_version()
    root = pathlib.Path.home() / 'Games' / title.prefix_subdir
    user_data = root / 'UserData'
    prefix = root / 'prefix'

    support = pathlib.Path(os.environ.get('TMNF_SUPPORT')
                           or pathlib.Path.home() / 'Library' / 'Application Support' / 'TMNF')
    runtime = support / 'runtime' / version
    cache = pathlib.Path(os.environ.get('TMNF_CACHE')
                         or pathlib.Path.home() / 'Library' / 'Caches' / 'TMNF')

    apps_env = os.environ.get('TMNF_APPS_DIR')
    if apps_env:
        apps_dir = pathlib.Path(apps_env)
    elif os.access('/Applications', os.W_OK):
        apps_dir = pathlib.Path('/Applications')
    else:
        apps_dir = pathlib.Path.home() / 'Applications'

    app = apps_dir / title.app_name
    settings_app = app.parent / title.settings_app_name
    launcher = support / 'launchers' / title.id

    game_dir = prefix / 'drive_c' / 'Program Files (x86)' / title.game_dir_name
    return TitlePaths(
        title_id=title.id, root=root, prefix=prefix, userdata=user_data, support=support,
        runtime=runtime, cache=cache, apps_dir=apps_dir, app=app,
        settings_app=settings_app, launcher=launcher, game_dir=game_dir,
        game_exe=game_dir / title.game_exe, launcher_exe=game_dir / title.launcher_exe,
    )


def local_runtime_fallback() -> typing.Optional[pathlib.Path]:
    candidate = registry.project_dir() / '.build' / 'release' / 'runtime'
    if (candidate / 'bin' / 'wine').is_file():
        return candidate
    return None


def wine_binary(paths: TitlePaths) -> typing.Optional[pathlib.Path]:
    if (paths.runtime / 'bin' / 'wine').is_file():
        return paths.runtime / 'bin' / 'wine'
    fallback = local_runtime_fallback()
    if fallback:
        return fallback / 'bin' / 'wine'
    return None
