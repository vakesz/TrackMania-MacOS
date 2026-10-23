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
        ok = marker.is_file() and marker.read_text().strip() == registry.pins()['directx_sha256']
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
    pin = registry.pins()
    patches = (
        (loader / 'TMLoader.exe', pin['tmloader_exe_sha256'], 'ModLoader executable'),
        (loader / 'database' / 'TmForever' / 'products' / 'Competition Patch'
         / pin['tmcomp_version'] / 'TMCompPatch.dll', pin['tmcomp_dll_sha256'],
         f"Competition Patch {pin['tmcomp_version']}"),
        (loader / 'database' / 'TmForever' / 'products' / 'TmForever'
         / pin['tmloader_game_version'] / 'TmForever.exe', pin['tmloader_game_sha256'],
         'ModLoader game runtime'),
        (loader / 'database' / 'TmForever' / 'products' / 'CoreMod'
         / pin['tmloader_coremod_version'] / 'CoreMod.dll', pin['tmloader_coremod_sha256'], 'CoreMod'),
    )
    for path, expected, label in patches:
        if not _pin_ok(path, expected):
            return Probe(False, f'{label} is missing or does not match the pinned release')
    settings = loader / 'database' / 'TmForever' / 'products' / 'TmForever' / 'settings.yaml'
    profile = loader / 'database' / 'TmForever' / 'profiles' / 'competition.yaml'
    if not (settings.is_file() and settings.stat().st_size
            and profile.is_file() and profile.stat().st_size):
        return Probe(False, 'Competition Patch configuration files are missing')
    return Probe(True, (f"Competition Patch {pin['tmcomp_version']} "
                       f"with ModLoader {pin['tmloader_bundle_version']}"))


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
