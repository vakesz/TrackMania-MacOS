"""Hub action dispatch.

Actions run through the managed shell flows with a scrubbed environment so one
title's overrides cannot leak into another's installation. Only genuinely
shared preferences (TMNF_APPS_DIR, TMNF_SUPPORT and TMNF_CACHE) survive, and
so does TMNF_TITLES_DIR: the registry directory applies to every consumer.
TMNF_RUNTIME_SOURCE is the exception: only the explicit --runtime flag sets it.
TMNF_RUNTIME_ARCHIVE and TMNF_RUNTIME_SHA256 are never inherited either.
"""

from __future__ import annotations

import os
import pathlib
import sys
import typing

from . import paths as hub_paths
from . import registry
from . import snapshot as hub_snapshot


RUNTIME_SOURCES = ('download', 'build')
RUNTIME_ACTIONS = ('install', 'install-platform')
# Runtime inputs the hub never takes from its own environment.
RUNTIME_ENV = ('TMNF_RUNTIME_SOURCE', 'TMNF_RUNTIME_ARCHIVE', 'TMNF_RUNTIME_SHA256')


def _scrubbed_env(title_id: str, runtime: typing.Optional[str] = None) -> typing.Dict[str, str]:
    # The same allowlist paths.compute ignores, so hub state and actions agree.
    env = {key: value for key, value in os.environ.items()
           if key not in hub_paths.TITLE_ENV and key not in RUNTIME_ENV}
    env['TMNF_TITLE'] = title_id
    if runtime is not None:
        env['TMNF_RUNTIME_SOURCE'] = runtime
    return env


def die(message: str) -> typing.NoReturn:
    print(f'error: {message}', file=sys.stderr)
    raise SystemExit(1)


def _exec(script: pathlib.Path, argv: typing.List[str], title_id: str,
          runtime: typing.Optional[str] = None) -> typing.NoReturn:
    command = ['/bin/bash', str(script), *argv]
    os.execve('/bin/bash', command, _scrubbed_env(title_id, runtime))


def _project_script(*parts: str) -> pathlib.Path:
    return registry.project_dir().joinpath(*parts)


def available_action_ids(title: registry.Title) -> set:
    # Only the target title's payload: an unrelated broken entry must not block it.
    payload = hub_snapshot.contained_payload(title)
    if payload.get('error'):
        die(f'Title {title.id} has a problem: {payload["error"]}')
    return {action['id'] for action in payload['actions']}


def run(title_id: str, action_id: str, source: typing.Optional[str],
        runtime: typing.Optional[str] = None) -> typing.NoReturn:
    title = registry.get(title_id)  # Registry-level validation with a precise message.
    if action_id == 'import':
        if not source:
            die('Import requires --source ABSOLUTE_PATH (a folder for Steam copies, '
                'a disk image for retail imports)')
        if not source.startswith('/'):
            die(f'Import source must be an absolute path: {source}')
        if not os.path.exists(source):
            die(f'Import source does not exist: {source}')
    elif source is not None:
        die(f'Action {action_id} does not accept --source.')
    if runtime is not None:
        if action_id not in RUNTIME_ACTIONS:
            die(f'Action {action_id} does not accept --runtime.')
        if runtime not in RUNTIME_SOURCES:
            die(f"Invalid --runtime '{runtime}': expected download or build.")
    availability = available_action_ids(title)
    if action_id not in availability:
        die(f'Action {action_id} is not currently available for title {title_id}. '
            'Refresh the hub and finish prerequisite steps.')

    cli = _project_script('src', 'shell', 'cli', 'tmnf')
    if action_id == 'install':
        _exec(_project_script('src', 'shell', 'cli', 'install.sh'), [], title_id, runtime)
    if action_id == 'install-platform':
        _exec(_project_script('src', 'shell', 'cli', 'install.sh'), ['setup'], title_id, runtime)
    if action_id == 'update':
        _exec(cli, ['update'], title_id)
    if action_id == 'install-apps':
        _exec(cli, ['app'], title_id)
    if action_id == 'import':
        _exec(cli, ['import', source], title_id)
    die(f'Unsupported hub action: {action_id}')
