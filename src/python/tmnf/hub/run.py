"""Hub action dispatch.

Actions run through the managed shell flows with a scrubbed environment so one
title's overrides cannot leak into another's installation. Only genuinely
shared preferences (TMNF_APPS_DIR, TMNF_SUPPORT and TMNF_CACHE) survive.
"""

from __future__ import annotations

import os
import pathlib
import sys
import typing

from . import registry
from . import snapshot as hub_snapshot

# Title-derived values: never inherited across hub-rendered actions.
_SCRUBBED = ('TMNF_ROOT', 'TMNF_PREFIX', 'TMNF_APP', 'TMNF_SETTINGS_APP',
             'TMNF_USERDATA', 'TMNF_RUNTIME', 'TMNF_LAUNCHER', 'TMNF_WINE')


def _scrubbed_env(title_id: str) -> typing.Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SCRUBBED}
    env['TMNF_TITLE'] = title_id
    return env


def die(message: str) -> typing.NoReturn:
    print(f'error: {message}', file=sys.stderr)
    raise SystemExit(1)


def _exec(script: pathlib.Path, argv: typing.List[str], title_id: str) -> typing.NoReturn:
    command = ['/bin/bash', str(script), *argv]
    os.execve('/bin/bash', command, _scrubbed_env(title_id))


def _project_script(*parts: str) -> pathlib.Path:
    return registry.project_dir().joinpath(*parts)


def available_action_ids(title_id: str) -> set:
    payloads = hub_snapshot.snapshot()
    if payloads.get('error'):
        die(payloads['error'])
    for candidate in payloads['titles']:
        if candidate['id'] == title_id:
            if candidate.get('error'):
                die(f'Title {title_id} has a registry problem: {candidate["error"]}')
            return {action['id'] for action in candidate['actions']}
    die(f'Unknown title: {title_id}')


def run(title_id: str, action_id: str, source: typing.Optional[str]) -> typing.NoReturn:
    registry.get(title_id)  # Registry-level validation with a precise message.
    if action_id == 'import':
        if not source:
            die('Import requires --source ABSOLUTE_PATH (a folder for Steam copies, '
                'a disk image for retail imports)')
        if not source.startswith('/'):
            die(f'Import source must be an absolute path: {source}')
        if not os.path.lexists(source) and not os.path.isdir(source):
            die(f'Import source does not exist: {source}')
    elif source is not None:
        die(f'Action {action_id} does not accept --source.')
    availability = available_action_ids(title_id)
    if action_id not in availability:
        die(f'Action {action_id} is not currently available for title {title_id}. '
            'Refresh the hub and finish prerequisite steps.')

    cli = _project_script('src', 'shell', 'cli', 'tmnf')
    if action_id == 'install':
        _exec(_project_script('src', 'shell', 'cli', 'install.sh'), [], title_id)
    if action_id == 'install-platform':
        _exec(_project_script('src', 'shell', 'cli', 'install.sh'), ['setup'], title_id)
    if action_id == 'update':
        _exec(cli, ['update'], title_id)
    if action_id == 'install-apps':
        _exec(cli, ['app'], title_id)
    if action_id == 'import':
        _exec(cli, ['import', source], title_id)
    die(f'Unsupported hub action: {action_id}')
