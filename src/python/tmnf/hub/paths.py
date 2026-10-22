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


def _bool_env(variable: str, default: bool) -> bool:
    value = os.environ.get(variable)
    if value is None:
        return default
    return value.lower() in ('1', 'true', 'yes', 'on')


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

    @property
    def runtime_wine(self) -> pathlib.Path:
        return self.runtime / 'bin' / 'wine'


def runtime_version() -> str:
    return (registry.project_dir() / 'VERSION').read_text().strip()


def directxpins() -> typing.Tuple[str, str]:
    """(version, sha256) of the DirectX End-User Runtimes June 2010 pin."""
    version = '9.29.1974.1 (June 2010)'
    sha256 = ('053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89'
              'c73b')
    return version, sha256


def compute(title: registry.Title) -> TitlePaths:
    version = runtime_version()
    root = pathlib.Path(os.environ.get('TMNF_ROOT')
                        or pathlib.Path.home() / 'Games' / title.prefix_subdir)
    user_data = pathlib.Path(root) / 'UserData'
    prefix = pathlib.Path(os.environ.get('TMNF_PREFIX') or root / 'prefix')

    support = pathlib.Path(os.environ.get('TMNF_SUPPORT')
                           or pathlib.Path.home() / 'Library' / 'Application Support' / 'TMNF')
    runtime = pathlib.Path(os.environ.get('TMNF_RUNTIME') or support / 'runtime' / version)
    cache = pathlib.Path(os.environ.get('TMNF_CACHE')
                         or pathlib.Path.home() / 'Library' / 'Caches' / 'TMNF')

    apps_env = os.environ.get('TMNF_APPS_DIR')
    if apps_env:
        apps_dir = pathlib.Path(apps_env)
    elif _bool_env('TMNF_APPS_WRITABLE_SYSTEM', os.access('/Applications', os.W_OK)):
        apps_dir = pathlib.Path('/Applications')
    else:
        apps_dir = pathlib.Path.home() / 'Applications'

    app = pathlib.Path(os.environ.get('TMNF_APP') or apps_dir / title.app_name)
    settings_app = pathlib.Path(
        os.environ.get('TMNF_SETTINGS_APP') or app.parent / title.settings_app_name)
    launcher = pathlib.Path(os.environ.get('TMNF_LAUNCHER') or support / 'launcher')

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
    override = os.environ.get('TMNF_WINE')
    if override:
        candidate = pathlib.Path(override)
        return candidate if candidate.is_file() else None
    if (paths.runtime / 'bin' / 'wine').is_file():
        return paths.runtime / 'bin' / 'wine'
    fallback = local_runtime_fallback()
    if fallback:
        return fallback / 'bin' / 'wine'
    return None
