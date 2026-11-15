"""Hub snapshot assembly (schema 3).

One defective title registry entry must not truncate the document; the layer
above gets a per-title error field instead. Swift clients render `error` as a
title-card problem, never as a broken window.

`migration` says whether files of an earlier release still wait to move into
the data folder (trackmania_mac.guard.relocate). Until they have, no title offers an
action: a fresh install would otherwise start beside the old one.

`storage` (trackmania_mac.guard.storage) sizes what the Settings window can clean,
and each title's `launchOptions` are the game defaults its installed launcher
records; both are read without running anything.
"""

from __future__ import annotations

import os
import pathlib
import typing

from trackmania_mac.app import launcher_config
from trackmania_mac.guard import relocate
from trackmania_mac.guard import storage
from trackmania_mac.guard import uninstall
from trackmania_mac.install import version as project_version

from . import paths as hub_paths
from . import registry
from . import state

SCHEMA = 3


def _actions(
    title: registry.Title,
    items: typing.Dict[str, state.Probe],
    session_active: bool = False,
    removable: bool = False,
) -> typing.List[dict]:
    actions = _base_actions(title, items)
    if items["prefix"].ready:
        actions.append({"id": "doctor", "label": "Run checks", "primary": False, "kind": "verify"})
        actions.append(
            {
                "id": "diagnostics",
                "label": "Create diagnostics report",
                "primary": False,
                "kind": "maintenance",
            }
        )
        if session_active:
            actions.append(
                {"id": "stop", "label": "Stop game", "primary": False, "kind": "maintenance"}
            )
    # Offered whenever the title left anything behind: a prefix, its apps or
    # launcher, or only its saves after an uninstall that kept them.
    if removable or items["prefix"].ready:
        actions.append(
            {"id": "uninstall", "label": "Uninstall\u2026", "primary": False, "kind": "destructive"}
        )
    return actions


def _base_actions(title: registry.Title, items: typing.Dict[str, state.Probe]) -> typing.List[dict]:
    def action(action_id: str, label: str, *, primary: bool = False, kind: str = "build") -> dict:
        return {"id": action_id, "label": label, "primary": primary, "kind": kind}

    prefix = items["prefix"].ready
    game = items["game"].ready
    if title.downloads:
        label = {
            "online": "Install or repair",
            "online-retail": "Download and install",
            "online-trial": "Install trial",
        }[title.installer]
        actions = [action("install", label, primary=True, kind="install")]
        if prefix and title.installer == "online":
            actions.append(action("update", "Check for updates", kind="verify"))
        return actions
    if title.installer in ("steam-copy", "disk-image"):
        actions = []
        platform_ready = all(
            items[name].ready for name in ("runtime", "prefix", "directx", "renderer")
        )
        if not platform_ready:
            actions.append(
                action("install-platform", "Install shared platform", primary=True, kind="install")
            )
        if game:
            if not (items["gameApp"].ready and items["settingsApp"].ready):
                actions.append(
                    action(
                        "install-apps",
                        "Install Game and Settings apps",
                        primary=not actions,
                        kind="apps",
                    )
                )
            return actions
        if platform_ready:
            label = (
                "Copy from an existing Steam install"
                if title.installer == "steam-copy"
                else "Run a retail installer"
            )
            actions.append(action("import", label, primary=not actions, kind="import"))
        return actions
    raise registry.RegistryError(f"Unhandled installer kind: {title.installer}")


def log_dir() -> pathlib.Path:
    return hub_paths.log_dir()


def _locations(paths: hub_paths.TitlePaths) -> dict:
    return {
        "root": str(paths.root),
        "prefix": str(paths.prefix),
        "gameDir": str(paths.game_dir),
        "userdata": str(paths.userdata),
        "logs": str(log_dir()),
    }


LAUNCH_FIELDS = (
    ("WINDOW_SIZE", "windowSize"),
    ("AUDIO", "audio"),
    ("X87", "x87"),
    ("RENDERER", "renderer"),
)
REINSTALL = "Reinstall the Game and Settings apps from the hub to change them here."


def launch_options(launcher: pathlib.Path) -> typing.Optional[dict]:
    """The game defaults an installed launcher records, or None without one.

    A value that is missing reads as its default (common.sh). The hub may
    change them (`set-defaults`) only when every line is in the form install_app
    writes; an invalid recorded value is shown as its default and replaced by
    the next change.
    """
    config = launcher / launcher_config.CONFIG
    if not os.path.lexists(config):
        return None
    options: dict = {field: launcher_config.LAUNCH_DEFAULTS[key] for key, field in LAUNCH_FIELDS}
    options.update(editable=False, reason=None)
    if launcher.is_symlink() or not uninstall.has_marker(launcher, "LAUNCHER"):
        options["reason"] = "These apps were not installed by this hub. " + REINSTALL
        return options
    try:
        settings = launcher_config.read(config)
    except (OSError, ValueError):
        options["reason"] = "The game's launch settings are not in the expected form. " + REINSTALL
        return options
    invalid = []
    for key, field in LAUNCH_FIELDS:
        value = settings.get(key)
        if value is None:
            continue
        if launcher_config.valid_launch_option(key, value):
            options[field] = value
        else:
            invalid.append(field)
    options["editable"] = True
    if invalid:
        options["reason"] = (
            "A recorded setting is not valid ({}); the game will not start until "
            "it is changed here.".format(", ".join(invalid))
        )
    return options


def _link(title: registry.Title) -> typing.Optional[dict]:
    """The title's related page, or None; the registry sets both fields or neither."""
    if not title.link_url or not title.link_label:
        return None
    return {"label": title.link_label, "url": title.link_url}


def title_payload(
    title: registry.Title, paths: typing.Optional[hub_paths.TitlePaths] = None
) -> dict:
    resolved = paths if paths is not None else hub_paths.compute(title)
    items = state.probe(title, resolved)
    icon, icon_source = resolved.resolve_icon()
    # Fails safe: any probe error reads as inactive, which hides Stop game.
    active = state.session_active(resolved.prefix)
    removal = uninstall.preview(title, resolved)
    return {
        "id": title.id,
        "name": title.name,
        "shortName": title.short_name,
        "status": title.status,
        "releaseDate": title.release_date,
        "engine": title.engine,
        "kind": title.installer,
        "notes": title.notes,
        "link": _link(title),
        "requiresSerial": title.requires_serial,
        "sourceSize": title.download_size,
        "locations": _locations(resolved),
        "sessionActive": active,
        "error": None,
        "icon": str(icon) if icon else None,
        "iconSource": icon_source,
        "state": {name: probe.payload() for name, probe in items.items()},
        "apps": {"game": str(resolved.app), "settings": str(resolved.settings_app)},
        "removal": removal,
        "launchOptions": launch_options(resolved.launcher),
        "actions": _actions(title, items, active, uninstall.removable(removal)),
    }


def contained_payload(title: registry.Title) -> dict:
    try:
        return title_payload(title)
    except (registry.RegistryError, OSError, project_version.VersionError) as error:
        return {
            "id": title.id,
            "name": title.name,
            "shortName": title.short_name,
            "status": title.status if title.status in registry.STATUSES else "unsupported",
            "releaseDate": title.release_date,
            "engine": title.engine,
            "kind": title.installer,
            "notes": title.notes,
            "link": _link(title),
            "requiresSerial": title.requires_serial,
            "sourceSize": title.download_size,
            "locations": {},
            "sessionActive": False,
            "error": f"{error}",
            "icon": None,
            "iconSource": None,
            "state": {},
            "apps": {"game": "", "settings": ""},
            "removal": None,
            "launchOptions": None,
            "actions": [],
        }


def _error_placeholder(title_id: str, message: str) -> dict:
    # Swift decodes id, name, shortName, status and notes as non-optional
    # strings: a placeholder must never emit null for them.
    payload: dict[str, object] = {
        key: None for key in ("engine", "kind", "icon", "iconSource", "releaseDate", "link")
    }
    payload.update(
        {
            "id": title_id,
            "name": title_id,
            "shortName": title_id,
            "status": "unsupported",
            "notes": "",
            "requiresSerial": False,
            "sourceSize": None,
            "locations": {},
            "sessionActive": False,
            "error": message,
            "state": {},
            "apps": {"game": "", "settings": ""},
            "removal": None,
            "launchOptions": None,
            "actions": [],
        }
    )
    return payload


def _hub_version() -> str:
    try:
        return hub_paths.runtime_version()
    except (OSError, project_version.VersionError):
        return ""


def migration() -> dict:
    """The relocate status; never raises, never changes a file."""
    try:
        return relocate.status()
    except Exception as error:  # noqa: BLE001 - one probe must not break the snapshot
        return {
            "state": "blocked",
            "reason": f"Cannot check for files of an earlier release: {error}",
            "from": [],
            "to": str(hub_paths.data_dir()),
        }


def snapshot() -> dict:
    # TRACKMANIA_MAC_TITLES_DIR (read by the registry) allows probing other title
    # directories; failures stay contained per title so one bad file never
    # buries the rest.
    error = None
    moving = migration()
    try:
        titles, schema_errors, unreadable = registry.load_registry()
    except registry.RegistryError as failure:
        return {
            "schema": SCHEMA,
            "hubVersion": _hub_version(),
            "migration": moving,
            "storage": storage.preview(),
            "titles": [],
            "error": str(failure),
        }
    payloads = []
    for title in titles:
        payload = contained_payload(title)
        if moving["state"] != "none":
            payload["actions"] = []
        payloads.append(payload)
    for stem, message in sorted(schema_errors.items()):
        payloads.append(_error_placeholder(stem, message))
    notes = [f"{stem}: {message}" for stem, message in sorted(schema_errors.items())]
    notes += [f"{path.name}: not valid JSON" for path in unreadable]
    if notes:
        error = "; ".join(notes)
    payload = {
        "schema": SCHEMA,
        "hubVersion": _hub_version(),
        "migration": moving,
        "storage": storage.preview(),
        "titles": payloads,
    }
    if error is not None:
        payload["error"] = error
    return payload
