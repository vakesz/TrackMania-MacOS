"""Hub action dispatch.

Actions run through the managed shell flows with a scrubbed environment so one
title's overrides cannot leak into another's installation. Only genuinely
shared preferences (TRACKMANIA_MAC_APPS_DIR, TRACKMANIA_MAC_SUPPORT and TRACKMANIA_MAC_CACHE) survive, and
so does TRACKMANIA_MAC_TITLES_DIR: the registry directory applies to every consumer.
TRACKMANIA_MAC_RUNTIME_SOURCE is the exception: only the explicit --runtime flag sets it.
TRACKMANIA_MAC_RUNTIME_ARCHIVE and TRACKMANIA_MAC_RUNTIME_SHA256 are never inherited either.
Only uninstall accepts --include-saves, which also removes the title's saves.
Only set-defaults accepts --window-size, --audio and --x87; it records them in
the title's installed launcher (trackmania-mac defaults), which checks them.
No action runs while files of an earlier release wait to move; `migrate` moves
them (trackmania-mac migrate), and it and `clean` (trackmania-mac clean) are the
only commands that are not per title.
"""

from __future__ import annotations

import os
import pathlib
import sys
import typing

from trackmania_mac.guard import relocate

from . import paths as hub_paths
from . import registry
from . import snapshot as hub_snapshot


RUNTIME_SOURCES = ("download", "build")
# set-defaults flags, as `trackmania-mac defaults` takes them.
LAUNCH_FLAGS = ("--window-size", "--audio", "--x87")
CLEAN_KINDS = ("downloads", "runtimes", "logs")
RUNTIME_ACTIONS = ("install", "install-platform")
# Runtime inputs the hub never takes from its own environment.
RUNTIME_ENV = (
    "TRACKMANIA_MAC_RUNTIME_SOURCE",
    "TRACKMANIA_MAC_RUNTIME_ARCHIVE",
    "TRACKMANIA_MAC_RUNTIME_SHA256",
)


def _scrubbed_env(title_id: str, runtime: typing.Optional[str] = None) -> typing.Dict[str, str]:
    # The same allowlist paths.compute ignores, so hub state and actions agree.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in hub_paths.TITLE_ENV and key not in RUNTIME_ENV
    }
    env["TRACKMANIA_MAC_TITLE"] = title_id
    if runtime is not None:
        env["TRACKMANIA_MAC_RUNTIME_SOURCE"] = runtime
    return env


def die(message: str) -> typing.NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _exec(
    script: pathlib.Path,
    argv: typing.List[str],
    title_id: str,
    runtime: typing.Optional[str] = None,
) -> typing.NoReturn:
    command = ["/bin/bash", str(script), *argv]
    os.execve("/bin/bash", command, _scrubbed_env(title_id, runtime))


def _shared_env() -> typing.Dict[str, str]:
    """The environment of a command that is not per title."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in hub_paths.TITLE_ENV
        and key not in RUNTIME_ENV
        and key != "TRACKMANIA_MAC_TITLE"
    }


def migrate() -> typing.NoReturn:
    """Move every title's files out of the folders of earlier releases."""
    cli = _project_script("src", "shell", "cli", hub_paths.CLI_NAME)
    os.execve("/bin/bash", ["/bin/bash", str(cli), "migrate"], _shared_env())


def _require_moved() -> None:
    moving = relocate.status()
    if moving["state"] != "none":
        die(
            "Your files are still in the folders of an earlier release"
            + (f": {moving['reason']}" if moving["reason"] else "; the hub moves them first.")
        )


def clean(kind: str) -> typing.NoReturn:
    """Remove cached downloads, unused runtimes or logs (trackmania-mac clean)."""
    if kind not in CLEAN_KINDS:
        die(f"Unknown cleanup '{kind}': use downloads, runtimes or logs.")
    _require_moved()
    cli = _project_script("src", "shell", "cli", hub_paths.CLI_NAME)
    os.execve("/bin/bash", ["/bin/bash", str(cli), "clean", kind], _shared_env())


def _project_script(*parts: str) -> pathlib.Path:
    return registry.project_dir().joinpath(*parts)


def available_action_ids(title: registry.Title) -> set:
    # Only the target title's payload: an unrelated broken entry must not block it.
    payload = hub_snapshot.contained_payload(title)
    if payload.get("error"):
        die(f"Title {title.id} has a problem: {payload['error']}")
    return {action["id"] for action in payload["actions"]}


def run(
    title_id: str,
    action_id: str,
    source: typing.Optional[str],
    runtime: typing.Optional[str] = None,
    include_saves: bool = False,
    launch_options: typing.Optional[typing.Dict[str, str]] = None,
) -> typing.NoReturn:
    title = registry.get(title_id)  # Registry-level validation with a precise message.
    options = {flag: value for flag, value in (launch_options or {}).items() if value is not None}
    if set(options) - set(LAUNCH_FLAGS):
        die(f"Unknown launch option: {sorted(set(options) - set(LAUNCH_FLAGS))[0]}")
    if action_id == "import":
        if not source:
            die(
                "Import requires --source ABSOLUTE_PATH (a folder for Steam copies, "
                "a disk image for retail imports)"
            )
        if not source.startswith("/"):
            die(f"Import source must be an absolute path: {source}")
        if not os.path.exists(source):
            die(f"Import source does not exist: {source}")
    elif source is not None:
        die(f"Action {action_id} does not accept --source.")
    if runtime is not None:
        if action_id not in RUNTIME_ACTIONS:
            die(f"Action {action_id} does not accept --runtime.")
        if runtime not in RUNTIME_SOURCES:
            die(f"Invalid --runtime '{runtime}': expected download or build.")
    if include_saves and action_id != "uninstall":
        die(f"Action {action_id} does not accept --include-saves.")
    if options and action_id != "set-defaults":
        die(f"Action {action_id} does not accept --window-size, --audio or --x87.")
    _require_moved()
    if action_id == "set-defaults":
        _set_defaults(title, options)
    availability = available_action_ids(title)
    if action_id not in availability:
        die(
            f"Action {action_id} is not currently available for title {title_id}. "
            "Refresh the hub and finish prerequisite steps."
        )

    cli = _project_script("src", "shell", "cli", hub_paths.CLI_NAME)
    if action_id == "install":
        _exec(_project_script("src", "shell", "cli", "install.sh"), [], title_id, runtime)
    if action_id == "install-platform":
        _exec(_project_script("src", "shell", "cli", "install.sh"), ["setup"], title_id, runtime)
    if action_id == "update":
        _exec(cli, ["update"], title_id)
    if action_id == "install-apps":
        _exec(cli, ["app"], title_id)
    if action_id == "import":
        _exec(cli, ["import", source], title_id)  # type: ignore[list-item]  # validated non-empty above
    if action_id == "doctor":
        _exec(cli, ["doctor"], title_id)
    if action_id == "diagnostics":
        _exec(cli, ["diagnostics"], title_id)
    if action_id == "stop":
        _exec(cli, ["stop"], title_id)
    if action_id == "uninstall":
        saves = ["--include-saves"] if include_saves else []
        _exec(cli, ["uninstall", "--yes", *saves], title_id)
    die(f"Unsupported hub action: {action_id}")


def _set_defaults(title: registry.Title, options: typing.Dict[str, str]) -> typing.NoReturn:
    if not options:
        die("Choose at least one of --window-size, --audio and --x87.")
    payload = hub_snapshot.contained_payload(title)
    if payload.get("error"):
        die(f"Title {title.id} has a problem: {payload['error']}")
    current = payload.get("launchOptions")
    if current is None:
        die(f"{title.name} has no installed apps yet. Install them first.")
    if not current["editable"]:
        die(current["reason"])
    argv = ["defaults"]
    for flag in LAUNCH_FLAGS:
        if flag in options:
            argv += [flag, options[flag]]
    _exec(_project_script("src", "shell", "cli", hub_paths.CLI_NAME), argv, title.id)
