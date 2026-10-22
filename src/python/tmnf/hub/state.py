"""Per-title installation state probe.

The verdicts mirror what the shell flows enforce (src/shell/lib/common.sh,
src/shell/lib/install/*.sh), so the hub never tells a different story than an
actual run through the installer. No Wine process is started here.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import typing

from . import paths as hub_paths
from . import registry

__all__ = ['probe']

# Release-frozen pins, shared with src/shell/lib/common.sh by the registry
# pin-sync tests; each language keeps one literal source of truth.
_DIRECTX_SHA256 = '053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b'
_TMLOADER_EXE_SHA256 = '0cf4c92ad1cb2dc7b92f95b2ec63d9058fd930c200632aaa2aab3a5a3a65e2c3'
_TMLOADER_GAME_SHA256 = '0a885e194131063be9f78d9e5cf81e0f3017dfc4331d176812109b6aefc633bf'
_TMCOMP_DLL_SHA256 = 'eef7200a2f6d5a8b31ae43534752e807c3c146248bd941fdf54b21b61225a135'
_COREMOD_DLL_SHA256 = 'f3ffdcaf2b15c040823d60bcc559e7f287f767b483c1b42588212cdf714d08cf'

_TMLOADER_GAME_VERSION = '2.12.0-compat'
_TMCOMP_VERSION = '1.5.3'
_COREMOD_VERSION = '1.0.9'
_TMLOADER_BUNDLE_VERSION = '1.0.3'


class Probe(typing.NamedTuple):
    ready: bool
    note: str

    def payload(self) -> dict:
        value = {'ready': self.ready}
        if self.note:
            value['note'] = self.note
        return value


def _digest(path: pathlib.Path) -> str:
    result = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _same_bytes(left: pathlib.Path, right: pathlib.Path) -> bool:
    if not (left.is_file() and right.is_file()):
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open('rb') as one, right.open('rb') as two:
        while True:
            block_one = one.read(1024 * 1024)
            block_two = two.read(1024 * 1024)
            if block_one != block_two:
                return False
            if not block_one:
                return True


def _pin_ok(path: pathlib.Path, expected: str) -> bool:
    try:
        return path.is_file() and _digest(path) == expected
    except OSError:
        return False


def _loader_dir(prefix: pathlib.Path) -> pathlib.Path:
    return prefix / 'drive_c' / 'Program Files' / 'TMLoader'


def _runtime_ready(wine: typing.Optional[pathlib.Path]) -> Probe:
    if wine is None:
        return Probe(False, 'Wine runtime not installed')
    return Probe(True, 'Wine runtime present')


def _prefix_ready(title: registry.Title, prefix: pathlib.Path) -> Probe:
    expected = title.prefix_marker
    marker = prefix / '.tmnf-prefix'
    try:
        identity = marker.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return Probe(False, 'Wine prefix not created')
    if identity == expected and (prefix / 'system.reg').is_file():
        return Probe(True, 'Managed wine prefix initialized')
    if not identity and title.id == 'tmnf' and (prefix / 'system.reg').is_file():
        return Probe(True, 'Legacy TMNF prefix preserved')
    if not identity and title.id == 'tmnf':
        return Probe(False, 'Legacy marker present but the prefix is not initialized')
    if identity == expected:
        return Probe(False, 'Managed prefix exists but is not initialized')
    if identity and identity != expected:
        return Probe(False, f'Prefix belongs to another registered title ({identity}), '
                            'clean it up or choose another TMNF_PREFIX')
    return Probe(False, 'Wine prefix not created')


def _directx_ready(prefix: pathlib.Path) -> Probe:
    marker = prefix / '.tmnf-directx-jun2010'
    try:
        ok = marker.is_file() and marker.read_text().strip() == _DIRECTX_SHA256
    except (OSError, UnicodeDecodeError):
        ok = False
    if ok:
        return Probe(True, 'DirectX June 2010 runtimes present')
    return Probe(False, 'DirectX June 2010 runtimes missing')


def _renderer_ready(prefix: pathlib.Path, wine: typing.Optional[pathlib.Path]) -> Probe:
    if wine is None:
        return Probe(False, 'Renderer needs the runtime installed first')
    source = wine.parent.parent / 'lib' / 'd9vk' / 'd3d9.dll'
    installed = prefix / 'drive_c' / 'windows' / 'syswow64' / 'd3d9.dll'
    if _same_bytes(source, installed):
        return Probe(True, 'D9VK (Direct3D 9 over Metal) ready')
    if installed.is_file():
        return Probe(False, 'Prefix D9VK does not match the runtime; rerun the renderer install')
    return Probe(False, 'D9VK not installed in the prefix')


def _competition_patch_ready(prefix: pathlib.Path) -> Probe:
    loader = _loader_dir(prefix)
    patches = (
        (loader / 'TMLoader.exe', _TMLOADER_EXE_SHA256, 'ModLoader executable'),
        (loader / 'database' / 'TmForever' / 'products' / 'Competition Patch'
         / _TMCOMP_VERSION / 'TMCompPatch.dll', _TMCOMP_DLL_SHA256,
         f'Competition Patch {_TMCOMP_VERSION}'),
        (loader / 'database' / 'TmForever' / 'products' / 'TmForever'
         / _TMLOADER_GAME_VERSION / 'TmForever.exe', _TMLOADER_GAME_SHA256,
         'ModLoader game runtime'),
        (loader / 'database' / 'TmForever' / 'products' / 'CoreMod'
         / _COREMOD_VERSION / 'CoreMod.dll', _COREMOD_DLL_SHA256, 'CoreMod'),
    )
    for path, expected, label in patches:
        if not _pin_ok(path, expected):
            return Probe(False, f'{label} is missing or does not match the pinned release')
    settings = loader / 'database' / 'TmForever' / 'products' / 'TmForever' / 'settings.yaml'
    profile = loader / 'database' / 'TmForever' / 'profiles' / 'competition.yaml'
    if not (settings.is_file() and settings.stat().st_size
            and profile.is_file() and profile.stat().st_size):
        return Probe(False, 'Competition Patch configuration files are missing')
    return Probe(True, f'Competition Patch {_TMCOMP_VERSION} with ModLoader {_TMLOADER_BUNDLE_VERSION}')


def _game_ready(title: registry.Title, paths: hub_paths.TitlePaths,
                prefix: pathlib.Path) -> Probe:
    if not paths.game_exe.is_file():
        return Probe(False, f'{title.name} is not installed yet')
    if title.installer == 'online' and title.needs_tmloader:
        return _competition_patch_ready(prefix)
    if title.requires_serial:
        return Probe(True, 'Installed; the game asks for the serial key at first launch')
    return Probe(True, 'Installed')


def _app_ready(path: pathlib.Path) -> bool:
    """A launchable app must be one of our complete, non-symlinked wrappers."""
    contents = path / 'Contents'
    marker = contents / '.tmnf-app'
    info = contents / 'Info.plist'
    executable = contents / 'MacOS' / 'TMNF'
    try:
        return (path.is_dir() and not path.is_symlink()
                and contents.is_dir() and not contents.is_symlink()
                and marker.is_file() and not marker.is_symlink()
                and info.is_file() and not info.is_symlink()
                and executable.is_file() and os.access(executable, os.X_OK)
                and not executable.is_symlink())
    except OSError:
        return False


def probe(title: registry.Title, paths: typing.Optional[hub_paths.TitlePaths] = None
          ) -> typing.Dict[str, Probe]:
    """State items, exactly the ones the shell flows gate on."""
    resolved = paths if paths is not None else hub_paths.compute(title)
    wine = hub_paths.wine_binary(resolved)
    items = {
        'runtime': _runtime_ready(wine),
        'prefix': _prefix_ready(title, resolved.prefix),
        'directx': _directx_ready(resolved.prefix),
        'renderer': _renderer_ready(resolved.prefix, wine),
        'game': _game_ready(title, resolved, resolved.prefix),
        'gameApp': Probe(_app_ready(resolved.app), ''),
        'settingsApp': Probe(_app_ready(resolved.settings_app), ''),
    }
    return items
