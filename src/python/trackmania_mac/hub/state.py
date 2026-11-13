"""Per-title installation state probe.

The verdicts mirror what the shell flows enforce (src/shell/lib/common.sh,
src/shell/lib/install/*.sh), so the hub never tells a different story than an
actual run through the installer. No Wine process is started here.
"""

from __future__ import annotations

import hashlib
import pathlib
import stat
import typing

from . import paths as hub_paths
from . import registry
from trackmania_mac.guard import managed_paths, operation_guard, uninstall
from trackmania_mac.install import modloader

__all__ = ["install_key", "probe", "session_active"]


class Probe(typing.NamedTuple):
    ready: bool
    note: str

    def payload(self) -> dict:
        value: dict[str, object] = {"ready": self.ready}
        if self.note:
            value["note"] = self.note
        return value


def _digest(path: pathlib.Path) -> str:
    result = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _same_bytes(left: pathlib.Path, right: pathlib.Path) -> bool:
    if not (left.is_file() and right.is_file()):
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as one, right.open("rb") as two:
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
    return prefix / modloader.PREFIX_DIR


def _runtime_ready(wine: typing.Optional[pathlib.Path]) -> Probe:
    if wine is None:
        return Probe(False, "Wine runtime not installed")
    return Probe(True, "Wine runtime present")


def _prefix_ready(title: registry.Title, prefix: pathlib.Path) -> Probe:
    expected = title.prefix_marker
    try:
        text = managed_paths.read_marker(prefix, "PREFIX")
    except ValueError as error:
        return Probe(False, str(error))
    except (OSError, UnicodeDecodeError):
        return Probe(False, "Wine prefix not created")
    if text is None:
        return Probe(False, "Wine prefix not created")
    identity = text.strip()
    if managed_paths.marker_present(prefix, "INITIALIZING"):
        return Probe(False, "Initialization is incomplete; run install again")
    if identity == expected and (prefix / "system.reg").is_file():
        return Probe(True, "Managed wine prefix initialized")
    if not identity and title.id == "tmnf" and (prefix / "system.reg").is_file():
        return Probe(True, "Prefix from an early TrackMania Mac release preserved")
    if not identity and title.id == "tmnf":
        return Probe(False, "Legacy marker present but the prefix is not initialized")
    if identity == expected:
        return Probe(False, "Managed prefix exists but is not initialized")
    if identity and identity != expected:
        return Probe(
            False,
            f"Prefix belongs to another registered title ({identity}), "
            "clean it up or choose another TRACKMANIA_MAC_PREFIX",
        )
    return Probe(False, "Wine prefix not created")


def _directx_ready(prefix: pathlib.Path) -> Probe:
    try:
        text = managed_paths.read_marker(prefix, "DIRECTX")
        ok = text is not None and text.strip() == registry.pins()["directx_sha256"]
    except (OSError, UnicodeDecodeError, ValueError):
        ok = False
    if ok:
        return Probe(True, "DirectX June 2010 runtimes present")
    return Probe(False, "DirectX June 2010 runtimes missing")


def _renderer_ready(prefix: pathlib.Path, wine: typing.Optional[pathlib.Path]) -> Probe:
    if wine is None:
        return Probe(False, "Renderer needs the runtime installed first")
    source = wine.parent.parent / "lib" / "d9vk" / "d3d9.dll"
    installed = prefix / "drive_c" / "windows" / "syswow64" / "d3d9.dll"
    if _same_bytes(source, installed):
        return Probe(True, "D9VK (Direct3D 9 over Metal) ready")
    if installed.is_file():
        return Probe(False, "Prefix D9VK does not match the runtime; rerun the renderer install")
    return Probe(False, "D9VK not installed in the prefix")


def _competition_patch_ready(prefix: pathlib.Path) -> Probe:
    loader = _loader_dir(prefix)
    pin = registry.pins()
    for item in modloader.pinned_files(pin):
        if not _pin_ok(loader / item.relpath, item.sha256):
            return Probe(False, f"{item.label} is missing or does not match the pinned release")
    settings, profile = (loader / relpath for relpath in modloader.config_files())
    if not (
        settings.is_file()
        and settings.stat().st_size
        and profile.is_file()
        and profile.stat().st_size
    ):
        return Probe(False, "Competition Patch configuration files are missing")
    return Probe(
        True,
        (
            f"Competition Patch {pin['tmcomp_version']} "
            f"with ModLoader {pin['tmloader_bundle_version']}"
        ),
    )


def _game_ready(title: registry.Title, paths: hub_paths.TitlePaths, prefix: pathlib.Path) -> Probe:
    if not paths.game_exe.is_file():
        return Probe(False, f"{title.name} is not installed yet")
    if "competition-patch" in title.components:
        return _competition_patch_ready(prefix)
    if title.requires_serial:
        return Probe(True, "Installed; register your key with Register player key")
    return Probe(True, "Installed")


# Placeholders the game ships before a real key replaces them: Steam's
# STEAM_Nadeo.ini carries the template token, and "Ask" means no key yet.
_CD_KEY_PLACEHOLDERS = frozenset({"", "%cdkey%", "ask"})


def install_key(ini: pathlib.Path) -> typing.Optional[str]:
    """The InstallKey value from the [TmForever] section of Nadeo.ini, or None."""
    try:
        text = ini.read_bytes().decode("latin-1")
    except OSError:
        return None
    section = ""
    found: typing.Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            header = line.split(";", 1)[0].strip()
            if header.endswith("]"):
                section = header[1:-1].strip().lower()
                continue
        name, sep, value = line.partition("=")
        if sep and section == "tmforever" and name.strip().lower() == "installkey":
            # The last line wins, as in most INI readers.
            found = value.strip()
    return found


def _launcher_key_registered(title: registry.Title, paths: hub_paths.TitlePaths) -> bool:
    """Whether the game launcher stored a registered key in the player's Config.

    The retail launcher's Register key writes Config/pkey.dat under the title's
    user folder and leaves Nadeo.ini at Key=Ask. Only its presence is checked.
    """
    pkey = paths.userdata / title.user_subdir / "Config" / "pkey.dat"
    try:
        info = pkey.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0


def _cd_key_ready(title: registry.Title, paths: hub_paths.TitlePaths) -> Probe:
    """Whether the game has a CD key: a real InstallKey in Nadeo.ini (Steam
    copies) or a key registered in the game launcher. The key is never reported."""
    if _launcher_key_registered(title, paths):
        return Probe(True, "CD key present")
    ini = paths.game_dir / "Nadeo.ini"
    if not ini.is_file():
        return Probe(False, "Nadeo.ini not found in the game folder")
    key = install_key(ini)
    if key is None or key.lower() in _CD_KEY_PLACEHOLDERS:
        return Probe(False, "CD key required")
    return Probe(True, "CD key present")


def _app_ready(path: pathlib.Path) -> bool:
    """A launchable app must be one of our complete, non-symlinked wrappers."""
    contents = path / "Contents"
    info = contents / "Info.plist"
    try:
        return (
            path.is_dir()
            and not path.is_symlink()
            and contents.is_dir()
            and not contents.is_symlink()
            and uninstall.has_marker(contents, "APP")
            and info.is_file()
            and not info.is_symlink()
            and uninstall.wrapper_executable(contents) is not None
        )
    except OSError:
        return False


def probe(
    title: registry.Title, paths: typing.Optional[hub_paths.TitlePaths] = None
) -> typing.Dict[str, Probe]:
    """State items, exactly the ones the shell flows gate on, plus cdKey.

    cdKey gates only the hub's Play button; it is added once the game files
    are in place, so a title that is not installed never shows it.
    """
    resolved = paths if paths is not None else hub_paths.compute(title)
    wine = hub_paths.wine_binary(resolved)
    items = {
        "runtime": _runtime_ready(wine),
        "prefix": _prefix_ready(title, resolved.prefix),
        "directx": _directx_ready(resolved.prefix),
        "renderer": _renderer_ready(resolved.prefix, wine),
        "game": _game_ready(title, resolved, resolved.prefix),
        "gameApp": Probe(_app_ready(resolved.app), ""),
        "settingsApp": Probe(_app_ready(resolved.settings_app), ""),
    }
    if title.cd_key_in_nadeo_ini and resolved.game_exe.is_file():
        items["cdKey"] = _cd_key_ready(title, resolved)
    return items


def session_active(prefix: pathlib.Path) -> bool:
    """True only when Wine's server lock for this prefix is provably held.

    One stat plus a momentary non-blocking lockf; never starts Wine. Anything
    unexpected counts as inactive, so the answer never raises.
    """
    try:
        operation_guard.check_inactive(prefix)
    except ValueError as error:
        return str(error).startswith("Wine is active")
    except Exception:  # noqa: BLE001 - a probe must never break the snapshot
        return False
    return False
