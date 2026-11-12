"""Diagnostics report: one redacted zip a user can attach to a bug report.

The report never needs the operation lock beyond what `trackmania-mac doctor` takes
itself, and a failing doctor never aborts it.
"""

from __future__ import annotations

import datetime
import getpass
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import typing
import zipfile

from trackmania_mac.guard import operation_guard
from trackmania_mac.install import version as project_version
from . import paths as hub_paths
from . import registry
from . import snapshot

LOG_COUNT = 5
LOG_TAIL_BYTES = 2 * 1024 * 1024
DOCTOR_TIMEOUT = 120
# Slack on top of operation_guard.INTERRUPT_GRACE for the guard to finish and exit.
TERMINATE_MARGIN = 1.0
ROSETTA = pathlib.Path("/Library/Apple/usr/share/rosetta/rosetta")


def redact(text: str, home: str, user: str) -> str:
    """Replace the home directory with ~ and the username with <user>."""
    if home and home != "/":
        text = text.replace(home.rstrip("/"), "~")
    if user:
        text = re.sub(
            r"(?<![A-Za-z0-9_.-])" + re.escape(user) + r"(?![A-Za-z0-9_-])", "<user>", text
        )
    return text


def _run(command: typing.List[str], timeout: int = 20) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"(failed to run {command[0]}: {error})\n"
    return result.stdout + result.stderr


def _stop_group(child: "subprocess.Popen[str]") -> None:
    """Stop a timed-out doctor and everything it started.

    The direct child is operation_guard, which forwards SIGTERM to the doctor's own
    group and SIGKILLs it after INTERRUPT_GRACE. Killing only the guard would leave the
    doctor holding the prefix lock, so signal our group first and escalate only after
    the guard's grace has passed.
    """
    for signum, wait in (
        (signal.SIGTERM, operation_guard.INTERRUPT_GRACE + TERMINATE_MARGIN),
        (signal.SIGKILL, TERMINATE_MARGIN),
    ):
        try:
            os.killpg(child.pid, signum)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            child.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _doctor(title_id: str) -> str:
    cli = registry.project_dir() / "src" / "shell" / "cli" / hub_paths.CLI_NAME
    env = dict(os.environ, TRACKMANIA_MAC_TITLE=title_id)
    try:
        child = subprocess.Popen(
            ["/bin/bash", str(cli), "doctor"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        return f"doctor could not run: {error}\n"
    try:
        stdout, stderr = child.communicate(timeout=DOCTOR_TIMEOUT)
    except subprocess.TimeoutExpired:
        _stop_group(child)
        try:
            stdout, stderr = child.communicate(timeout=TERMINATE_MARGIN)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return (
            f"{stdout}{stderr}\n(doctor timed out after {DOCTOR_TIMEOUT} seconds and was stopped)\n"
        )
    return f"{stdout}{stderr}\n(doctor exit status {child.returncode})\n"


def _summary(title_id: str) -> str:
    try:
        version = hub_paths.runtime_version()
    except (OSError, project_version.VersionError):
        version = "unknown"
    return (
        f"Version: {version}\nTitle: {title_id}\n"
        f"sw_vers:\n{_run(['/usr/bin/sw_vers'])}"
        f"uname -m: {_run(['/usr/bin/uname', '-m']).strip()}\n"
        f"Rosetta present: {'yes' if ROSETTA.exists() else 'no'}\n"
    )


def _tail(path: pathlib.Path) -> str:
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - LOG_TAIL_BYTES))
        return stream.read().decode("utf-8", "replace")


def _is_title_log(name: str, title_id: str) -> bool:
    """tmnf keeps the unprefixed play/configure logs; other titles use `<id>-<action>.log`."""
    if title_id == "tmnf":
        return name in ("play.log", "configure.log")
    return name.startswith(f"{title_id}-") and name.endswith(".log")


def _recent_logs(directory: pathlib.Path, title_id: str) -> typing.List[pathlib.Path]:
    try:
        logs = [
            path
            for path in directory.glob("*.log")
            if path.is_file() and _is_title_log(path.name, title_id)
        ]
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
        "summary.txt": _summary(title_id),
        "doctor.txt": doctor_output if doctor_output is not None else _doctor(title_id),
        "snapshot.json": json.dumps(snapshot.contained_payload(title), indent=2, sort_keys=True),
    }
    for log in _recent_logs(directory, title_id):
        try:
            members[f"logs/{log.name}"] = _tail(log)
        except OSError as error:
            members[f"logs/{log.name}"] = f"(unreadable: {error})\n"

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = directory / f"diagnostics-{title_id}-{stamp}.zip"
    handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in members.items():
                archive.writestr(name, redact(text, home, user))
        os.replace(temporary, target)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return target


def main() -> int:
    title_id = os.environ.get("TRACKMANIA_MAC_TITLE", "tmnf")
    try:
        target = build(title_id)
    except (registry.RegistryError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Diagnostics report: {target}")
    return 0
