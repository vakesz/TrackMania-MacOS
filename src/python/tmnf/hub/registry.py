"""Title registry: one authoritative loader for hub, install and doctor paths.

Every field checked here travels inside config/titles/*.json, which ships
unchanged inside the installed launcher payload. Validation is deliberately
strict: an inconsistent registry entry is an operator error, not a hub quirk.
"""

from __future__ import annotations

import json
import pathlib
import re
import typing


INSTALLERS = frozenset({'online', 'online-retail', 'online-trial', 'steam-copy', 'disk-image'})
ENGINES = frozenset({'tmf-classic', 'maniaplanet', 'turbo', 'tm-original'})
STATUSES = frozenset({'supported', 'provisional'})

_STRING_FIELDS = (
    'id', 'name', 'short_name', 'engine', 'installer', 'status',
    'prefix_subdir', 'prefix_marker', 'app_name', 'settings_app_name',
    'bundle_id_game', 'bundle_id_settings', 'game_dir_name', 'game_exe',
    'launcher_exe',
)
_SWITCH_FIELDS = ('needs_directx', 'needs_tmloader', 'requires_serial')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class RegistryError(Exception):
    """A title registry entry is missing, malformed or inconsistent."""


class Title(typing.NamedTuple):
    id: str
    name: str
    short_name: str
    engine: str
    installer: str
    status: str
    prefix_subdir: str
    prefix_marker: str
    app_name: str
    settings_app_name: str
    bundle_id_game: str
    bundle_id_settings: str
    game_dir_name: str
    game_exe: str
    launcher_exe: str
    save_dirs: typing.Tuple[str, ...]
    needs_directx: bool
    needs_tmloader: bool
    source_url: typing.Optional[str]
    source_sha256: typing.Optional[str]
    source_size: typing.Optional[int]
    requires_serial: bool
    notes: str

    @property
    def downloads(self) -> bool:
        """True when the title downloads an official installer from its URL."""
        return self.installer in ('online', 'online-retail', 'online-trial')


def project_dir() -> pathlib.Path:
    # src/python/tmnf/hub/registry.py -> project root, in checkout and in the
    # installed launcher payload alike.
    return pathlib.Path(__file__).resolve().parents[4]


def titles_dir() -> pathlib.Path:
    return project_dir() / 'config' / 'titles'


def _validate(data: object, path: pathlib.Path) -> Title:
    if not isinstance(data, dict):
        raise RegistryError(f'{path}: registry entry must be a JSON object')
    missing = [field for field in _STRING_FIELDS if not isinstance(data.get(field), str)
               or not data.get(field)]
    if missing:
        raise RegistryError(f'{path}: missing or empty text fields: {", ".join(missing)}')
    for field in _SWITCH_FIELDS:
        if not isinstance(data.get(field), bool):
            raise RegistryError(f'{path}: {field} must be true or false')
    if data['id'] != path.stem:
        raise RegistryError(f'{path}: id does not match the file name')
    if data['engine'] not in ENGINES:
        raise RegistryError(f"{path}: unknown engine {data['engine']}")
    if data['installer'] not in INSTALLERS:
        raise RegistryError(f"{path}: unknown installer kind {data['installer']}")
    if data['status'] not in STATUSES:
        raise RegistryError(f"{path}: unknown status {data['status']}")
    if not str(data['prefix_marker']).endswith(':1'):
        raise RegistryError(f"{path}: prefix_marker must end with ':1'")
    if not str(data['app_name']).endswith('.app'):
        raise RegistryError(f"{path}: app_name must end with '.app'")
    for field in ('short_name', 'game_exe', 'launcher_exe', 'game_dir_name',
                  'app_name', 'settings_app_name'):
        value = data[field]
        if any(char in '\n\r\x00/\\' for char in value) or '\\' in value:
            raise RegistryError(f'{path}: unsafe name field {field}')
    parts = data['prefix_subdir'].split('/')
    if parts[0].startswith('/') or any(part in ('', '.', '..') for part in parts):
        raise RegistryError(f'{path}: unsafe prefix_subdir')
    save_dirs = data.get('save_dirs')
    if not isinstance(save_dirs, list) or not save_dirs \
            or not all(isinstance(item, str) and item and '/' not in item
                       and '\\' not in item for item in save_dirs):
        raise RegistryError(f'{path}: save_dirs must be a list of plain folder names')

    url = data.get('source_url')
    if isinstance(url, str) and url:
        if not url.startswith(('https://', 'http://')):
            raise RegistryError(f'{path}: source_url must be an absolute http(s) URL')
    else:
        url = None
    sha = data.get('source_sha256')
    if isinstance(sha, str) and _SHA256.match(sha):
        pass
    elif isinstance(sha, str) and sha:
        raise RegistryError(f'{path}: source_sha256 must be a lowercase hex SHA-256')
    else:
        sha = None
    size = data.get('source_size')
    if isinstance(size, int) and size > 0:
        pass
    else:
        size = None
    if data['installer'] in ('online', 'online-retail', 'online-trial'):
        if not url or not sha:
            raise RegistryError(f'{path}: online installer kinds require source_url and source_sha256')
    elif url or sha or size:
        raise RegistryError(f"{path}: installer kind {data['installer']} must not carry pins")
    if data['installer'] in ('online-retail', 'disk-image') and data['status'] == 'provisional':
        pass  # provisional is the expected state for retail and disk installs

    return Title(
        save_dirs=tuple(save_dirs),
        source_url=url,
        source_sha256=sha,
        source_size=size,
        requires_serial=bool(data.get('requires_serial')),
        notes=str(data.get('notes', '')),
        **{field: data[field] for field in _STRING_FIELDS},
        needs_directx=data['needs_directx'],
        needs_tmloader=data['needs_tmloader'],
    )


def load_registry(titles_dir_path=None):
    """Load every entry with per-title containment.

    Returns (titles, schema_errors, unreadable) where schema_errors maps a
    file stem to its validation message and unreadable is a list of paths
    whose content could not even be parsed as JSON.
    """
    directory = titles_dir_path if titles_dir_path is not None else titles_dir()
    titles, schema_errors, unreadable = [], {}, []
    for path in sorted(directory.glob('*.json')):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            unreadable.append(path)
            continue
        try:
            titles.append(_validate(data, path))
        except RegistryError as error:
            schema_errors[path.stem] = str(error)
    if not titles and not schema_errors and not unreadable:
        raise RegistryError(f'{directory}: the title registry is empty')
    return titles, schema_errors, unreadable


def load_all(titles_dir_path=None):
    titles, schema_errors, unreadable = load_registry(titles_dir_path)
    if schema_errors or unreadable:
        raise RegistryError('; '.join(
            [f'{stem}: {message}' for stem, message in sorted(schema_errors.items())] +
            [f'{path.name}: not valid JSON' for path in unreadable]))
    return titles


def get(title_id: str, titles_dir_path: typing.Optional[pathlib.Path] = None) -> Title:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', title_id or ''):
        raise RegistryError(f'Unknown title: {title_id!r}')
    path = (titles_dir_path if titles_dir_path is not None else titles_dir()) / f'{title_id}.json'
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise RegistryError(f'Unknown title: {title_id}. '
                            f'See the registry at {path.parent}') from None
    return _validate(data, path)
