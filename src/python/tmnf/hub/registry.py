"""Title registry: one authoritative loader for hub, install and doctor paths.

Every field checked here travels inside config/titles/*.json, which ships
unchanged inside the installed launcher payload. Validation is deliberately
strict: an inconsistent registry entry is an operator error, not a hub quirk.

The shell flows read the same data through one process:
`python -m tmnf.hub.registry export [TITLE]` prints shell-quoted assignments
for the title (TMNF_TITLE_*) and the release pins (config/pins.json), so a
shell process pays a single interpreter start for everything.

Download integrity is guaranteed by the pinned SHA-256, not by the transport:
an http:// source_url is accepted only because the installer is rejected when
its digest differs from source_sha256.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import sys
import typing


ONLINE_INSTALLERS = ('online', 'online-retail', 'online-trial')
INSTALLERS = frozenset(ONLINE_INSTALLERS + ('steam-copy', 'disk-image'))
ENGINES = frozenset({'tmf-classic', 'maniaplanet', 'turbo'})
STATUSES = frozenset({'supported', 'provisional'})

_STRING_FIELDS = (
    'id', 'name', 'short_name', 'engine', 'installer', 'status',
    'prefix_subdir', 'prefix_marker', 'app_name', 'settings_app_name',
    'bundle_id_game', 'bundle_id_settings', 'game_dir_name', 'game_exe',
    'launcher_exe',
)
_SWITCH_FIELDS = ('needs_directx', 'needs_tmloader', 'requires_serial')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_BUNDLE_ID = re.compile(r'^[A-Za-z0-9.-]+$')
_URL = re.compile(r'^https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%\[\]-]+$')
_PIN_TEXT = re.compile(r'^[A-Za-z0-9._ ()-]+$')
_PIN_SHA_KEYS = ('directx_sha256', 'tmloader_sha256', 'tmloader_exe_sha256',
                 'tmloader_game_sha256', 'tmloader_coremod_sha256', 'tmcomp_dll_sha256')
_PIN_TEXT_KEYS = ('directx_version', 'tmloader_bundle_version', 'tmloader_game_version',
                  'tmloader_coremod_version', 'tmcomp_version')
_PIN_URL_KEYS = ('directx_url', 'tmloader_url')


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
        return self.installer in ONLINE_INSTALLERS


def project_dir() -> pathlib.Path:
    # src/python/tmnf/hub/registry.py -> project root, in checkout and in the
    # installed launcher payload alike.
    return pathlib.Path(__file__).resolve().parents[4]


def titles_dir() -> pathlib.Path:
    """The registry directory; TMNF_TITLES_DIR overrides it for every consumer."""
    override = os.environ.get('TMNF_TITLES_DIR')
    return pathlib.Path(override) if override else project_dir() / 'config' / 'titles'


def _plain_name(value: str) -> bool:
    """One path component with no traversal, home expansion or control bytes."""
    return bool(value) and value not in ('.', '..') and not value.startswith('~') \
        and not any(char in '/\\' or ord(char) < 32 or ord(char) == 127 for char in value)


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
        if not _plain_name(data[field]):
            raise RegistryError(f'{path}: unsafe name field {field}')
    if not all(_plain_name(part) for part in data['prefix_subdir'].split('/')):
        raise RegistryError(f'{path}: unsafe prefix_subdir')
    for field in ('bundle_id_game', 'bundle_id_settings'):
        if not _BUNDLE_ID.match(data[field]):
            raise RegistryError(f'{path}: {field} must match [A-Za-z0-9.-]+')
    save_dirs = data.get('save_dirs')
    if not isinstance(save_dirs, list) or not save_dirs \
            or not all(isinstance(item, str) and _plain_name(item) for item in save_dirs):
        raise RegistryError(f'{path}: save_dirs must be a list of plain folder names')

    url = data.get('source_url')
    if isinstance(url, str) and url:
        if not _URL.match(url):
            raise RegistryError(f'{path}: source_url must be an absolute http(s) URL')
    else:
        url = None
    sha = data.get('source_sha256')
    if isinstance(sha, str) and sha and not _SHA256.match(sha):
        raise RegistryError(f'{path}: source_sha256 must be a lowercase hex SHA-256')
    sha = sha or None
    size = data.get('source_size')
    size = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else None
    if url and url.startswith('http://') and not sha:
        raise RegistryError(f'{path}: an http:// source_url requires source_sha256')
    if data['installer'] in ONLINE_INSTALLERS:
        if not url or not sha:
            raise RegistryError(f'{path}: online installer kinds require source_url and source_sha256')
    elif url or sha or size:
        raise RegistryError(f"{path}: installer kind {data['installer']} must not carry pins")

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


def pins_path() -> pathlib.Path:
    return project_dir() / 'config' / 'pins.json'


def pins() -> typing.Dict[str, str]:
    """Release-frozen DirectX and TMLoader pins from config/pins.json."""
    path = pins_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise RegistryError(f'{path}: release pins are missing or not valid JSON') from None
    if not isinstance(data, dict):
        raise RegistryError(f'{path}: pins must be a JSON object')
    for key in _PIN_SHA_KEYS + _PIN_TEXT_KEYS + _PIN_URL_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise RegistryError(f'{path}: missing pin {key}')
        if key in _PIN_SHA_KEYS and not _SHA256.match(value):
            raise RegistryError(f'{path}: {key} must be a lowercase hex SHA-256')
        if key in _PIN_TEXT_KEYS and not _PIN_TEXT.match(value):
            raise RegistryError(f'{path}: unsafe pin text {key}')
        if key in _PIN_URL_KEYS and not (value.startswith('https://') and _URL.match(value)):
            raise RegistryError(f'{path}: {key} must be an https URL')
    return {key: data[key] for key in _PIN_SHA_KEYS + _PIN_TEXT_KEYS + _PIN_URL_KEYS}


def _shell_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return ''
    if isinstance(value, tuple):
        return ' '.join(value)
    return str(value)


def shell_assignments(title: typing.Optional[Title]) -> typing.List[typing.Tuple[str, str]]:
    """(NAME, value) pairs: TMNF_TITLE_* for the title, then the release pins."""
    pairs = []
    if title is not None:
        # save_dirs is omitted: names may contain spaces, so a joined string is
        # ambiguous; the shell only needs the first one.
        pairs = [(f'TMNF_TITLE_{field.upper()}', _shell_value(getattr(title, field)))
                 for field in Title._fields if field != 'save_dirs']
        pairs.append(('TMNF_TITLE_FIRST_SAVE_DIR', title.save_dirs[0]))
    pairs += [(key.upper(), value) for key, value in pins().items()]
    return pairs


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != 'export' or len(arguments) > 2:
        print('usage: python -m tmnf.hub.registry export [TITLE]', file=sys.stderr)
        return 2
    try:
        title = get(arguments[1]) if len(arguments) == 2 else None
        pairs = shell_assignments(title)
    except RegistryError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    sys.stdout.write(''.join(f'{name}={shlex.quote(value)}\n' for name, value in pairs))
    return 0


if __name__ == '__main__':
    sys.exit(main())
