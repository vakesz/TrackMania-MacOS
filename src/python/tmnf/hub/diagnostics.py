"""Diagnostics report: one redacted zip a user can attach to a bug report.

The report never needs the operation lock beyond what `tmnf doctor` takes
itself, and a failing doctor never aborts it.
"""

from __future__ import annotations

import datetime
import getpass
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import typing
import zipfile

from . import paths as hub_paths
from . import registry
from . import snapshot

LOG_COUNT = 5
LOG_TAIL_BYTES = 2 * 1024 * 1024
DOCTOR_TIMEOUT = 120
ROSETTA = pathlib.Path('/Library/Apple/usr/share/rosetta/rosetta')


def redact(text: str, home: str, user: str) -> str:
    """Replace the home directory with ~ and the username with <user>."""
    if home and home != '/':
        text = text.replace(home.rstrip('/'), '~')
    if user:
        text = re.sub(r'(?<![A-Za-z0-9_.-])' + re.escape(user) + r'(?![A-Za-z0-9_-])',
                      '<user>', text)
    return text


def _run(command: typing.List[str], timeout: int = 20) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return f'(failed to run {command[0]}: {error})\n'
    return result.stdout + result.stderr


def _doctor(title_id: str) -> str:
    cli = registry.project_dir() / 'src' / 'shell' / 'cli' / 'tmnf'
    env = dict(os.environ, TMNF_TITLE=title_id)
    try:
        result = subprocess.run(['/bin/bash', str(cli), 'doctor'], capture_output=True,
                                text=True, errors='replace', timeout=DOCTOR_TIMEOUT,
                                env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        return f'doctor could not run: {error}\n'
    return f'{result.stdout}{result.stderr}\n(doctor exit status {result.returncode})\n'


def _summary(title_id: str) -> str:
    try:
        version = hub_paths.runtime_version()
    except OSError:
        version = 'unknown'
    return (f'VERSION: {version}\nTitle: {title_id}\n'
            f'sw_vers:\n{_run(["/usr/bin/sw_vers"])}'
            f'uname -m: {_run(["/usr/bin/uname", "-m"]).strip()}\n'
            f'Rosetta present: {"yes" if ROSETTA.exists() else "no"}\n')


def _tail(path: pathlib.Path) -> str:
    with path.open('rb') as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - LOG_TAIL_BYTES))
        return stream.read().decode('utf-8', 'replace')


def _recent_logs(directory: pathlib.Path) -> typing.List[pathlib.Path]:
    try:
        logs = [path for path in directory.glob('*.log') if path.is_file()]
        return sorted(logs, key=lambda path: path.stat().st_mtime, reverse=True)[:LOG_COUNT]
    except OSError:
        return []


def build(title_id: str, doctor_output: typing.Optional[str] = None) -> pathlib.Path:
    title = registry.get(title_id)
    home = str(pathlib.Path.home())
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = os.path.basename(home)
    directory = snapshot.log_dir()
    directory.mkdir(parents=True, exist_ok=True)

    members: typing.Dict[str, str] = {
        'summary.txt': _summary(title_id),
        'doctor.txt': doctor_output if doctor_output is not None else _doctor(title_id),
        'snapshot.json': json.dumps(snapshot.contained_payload(title), indent=2, sort_keys=True),
    }
    for log in _recent_logs(directory):
        try:
            members[f'logs/{log.name}'] = _tail(log)
        except OSError as error:
            members[f'logs/{log.name}'] = f'(unreadable: {error})\n'

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    target = directory / f'diagnostics-{title_id}-{stamp}.zip'
    handle, temporary = tempfile.mkstemp(dir=directory, suffix='.tmp')
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, text in members.items():
                archive.writestr(name, redact(text, home, user))
        os.replace(temporary, target)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return target


def main() -> int:
    title_id = os.environ.get('TMNF_TITLE', 'tmnf')
    try:
        target = build(title_id)
    except (registry.RegistryError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print(f'Diagnostics report: {target}')
    return 0
