"""Hub snapshot assembly (schema 2).

One defective title registry entry must not truncate the document; the layer
above gets a per-title error field instead. Swift clients render `error` as a
title-card problem, never as a broken window.
"""

from __future__ import annotations

import typing

from . import paths as hub_paths
from . import registry
from . import state

SCHEMA = 2


def _actions(title: registry.Title, items: typing.Dict[str, state.Probe]) -> typing.List[dict]:
    def action(action_id: str, label: str, *, primary: bool = False,
               kind: str = 'build') -> dict:
        return {'id': action_id, 'label': label, 'primary': primary, 'kind': kind}

    prefix = items['prefix'].ready
    game = items['game'].ready
    if title.downloads:
        label = {'online': 'Install or repair',
                 'online-retail': 'Download and install',
                 'online-trial': 'Install trial'}[title.installer]
        actions = [action('install', label, primary=True, kind='install')]
        if prefix and title.installer == 'online':
            actions.append(action('update', 'Check for updates', kind='verify'))
        return actions
    if title.installer in ('steam-copy', 'disk-image'):
        actions = []
        platform_ready = all(items[name].ready for name in ('runtime', 'prefix', 'directx', 'renderer'))
        if not platform_ready:
            actions.append(action('install-platform', 'Install shared platform',
                                  primary=True, kind='install'))
        if game:
            if not (items['gameApp'].ready and items['settingsApp'].ready):
                actions.append(action('install-apps', 'Install Game and Settings apps',
                                      primary=not actions, kind='apps'))
            return actions
        if platform_ready:
            label = ('Copy from an existing Steam install'
                     if title.installer == 'steam-copy' else 'Run a retail installer')
            actions.append(action('import', label, primary=not actions, kind='import'))
        return actions
    raise registry.RegistryError(f'Unhandled installer kind: {title.installer}')


def title_payload(title: registry.Title, paths: typing.Optional[hub_paths.TitlePaths] = None
                  ) -> dict:
    resolved = paths if paths is not None else hub_paths.compute(title)
    items = state.probe(title, resolved)
    icon, icon_source = resolved.resolve_icon()
    return {
        'id': title.id,
        'name': title.name,
        'shortName': title.short_name,
        'status': title.status,
        'engine': title.engine,
        'kind': title.installer,
        'notes': title.notes,
        'requiresSerial': title.requires_serial,
        'error': None,
        'icon': str(icon) if icon else None,
        'iconSource': icon_source,
        'state': {name: probe.payload() for name, probe in items.items()},
        'apps': {'game': str(resolved.app), 'settings': str(resolved.settings_app)},
        'actions': _actions(title, items),
    }


def contained_payload(title: registry.Title) -> dict:
    try:
        return title_payload(title)
    except (registry.RegistryError, OSError) as error:
        return {
            'id': title.id,
            'name': title.name,
            'shortName': title.short_name,
            'status': title.status if title.status in registry.STATUSES else 'provisional',
            'engine': title.engine,
            'kind': title.installer,
            'notes': title.notes,
            'requiresSerial': title.requires_serial,
            'error': f'{error}',
            'icon': None,
            'iconSource': None,
            'state': {},
            'apps': {'game': '', 'settings': ''},
            'actions': [],
        }


def _error_placeholder(title_id: str, message: str) -> dict:
    # Swift decodes id, name, shortName, status and notes as non-optional
    # strings: a placeholder must never emit null for them.
    payload = {key: None for key in ('engine', 'kind', 'icon', 'iconSource')}
    payload.update({
        'id': title_id,
        'name': title_id,
        'shortName': title_id,
        'status': 'provisional',
        'notes': '',
        'requiresSerial': False,
        'error': message,
        'state': {},
        'apps': {'game': '', 'settings': ''},
        'actions': [],
    })
    return payload


def snapshot() -> dict:
    # TMNF_TITLES_DIR (read by the registry) allows probing other title
    # directories; failures stay contained per title so one bad file never
    # buries the rest.
    error = None
    try:
        titles, schema_errors, unreadable = registry.load_registry()
    except registry.RegistryError as failure:
        return {'schema': SCHEMA, 'titles': [], 'error': str(failure)}
    payloads = []
    for title in titles:
        payloads.append(contained_payload(title))
    for stem, message in sorted(schema_errors.items()):
        payloads.append(_error_placeholder(stem, message))
    notes = [f'{stem}: {message}' for stem, message in sorted(schema_errors.items())]
    notes += [f'{path.name}: not valid JSON' for path in unreadable]
    if notes:
        error = '; '.join(notes)
    payload = {'schema': SCHEMA, 'titles': payloads}
    if error is not None:
        payload['error'] = error
    return payload
