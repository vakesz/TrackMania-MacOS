#!/usr/bin/env python3
"""Check installation ownership without creating directories or invoking Wine."""

import argparse
import ctypes
import errno
import json
import os
import pwd
import re
from pathlib import Path
import sys
import unicodedata

try:
    from trackmania_mac import legacy, markers
except ImportError:  # Run as a script: the package is two folders up.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from trackmania_mac import legacy, markers


def safe_path(value):
    """Validate a managed path and return it resolved exactly once.

    Trailing slashes are normalized away (so a symlink cannot hide behind
    "link/"), the final component must not be a symlink, and the RESOLVED path
    must lie below an allowed root. Ancestor symlinks are allowed as long as
    they resolve inside those roots. Destructive callers must act on the
    returned path, never on the original string.
    """
    value = value.rstrip("/") or "/"
    path = Path(value)
    if (
        not path.is_absolute()
        or any(part in (".", "..") for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
        or path.is_symlink()
    ):
        raise ValueError("Unsafe managed path: {!r}".format(value))
    resolved = path.resolve()
    home = Path.home().resolve()
    roots = (home, Path("/private/tmp"), Path("/tmp").resolve())
    if (
        resolved in roots
        or resolved in home.parents
        or not any(root in resolved.parents for root in roots)
    ):
        raise ValueError(
            "Managed path must be below home or the temporary directory: {}".format(value)
        )
    return resolved


_TITLE_MARKERS = None


def title_markers():
    """Prefix ownership markers from config/titles/*.json, next to this file."""
    global _TITLE_MARKERS
    if _TITLE_MARKERS is None:
        markers = set()
        titles = Path(__file__).resolve().parents[4] / "config" / "titles"
        if not titles.is_dir():
            # Never an empty-set fallback that would accept everything; the
            # operator learns why instead of a mystery "Unrecognized prefix".
            raise RuntimeError(
                "The title registry is missing at {}. The installed launcher "
                "payload or checkout is incomplete.".format(titles)
            )
        # Each readable entry's marker is derived from its id (the file stem),
        # exactly as the registry's Title.prefix_marker does.
        for path in sorted(titles.glob("*.json")):
            try:
                json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            markers.add("{}:1".format(path.stem))
        _TITLE_MARKERS = markers
    return _TITLE_MARKERS


def _current_title(explicit=None):
    """The title whose prefix is checked; configuration travels with the entry."""
    title = explicit if explicit is not None else os.environ.get("TRACKMANIA_MAC_TITLE") or "tmnf"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", title or ""):
        raise ValueError("Unsafe title identifier: {!r}".format(title))
    return title


def _check_owned_directory(path):
    """A prefix another account controls is not managed by this one."""
    stat = path.stat()
    if stat.st_uid != os.getuid():
        raise ValueError(
            "Prefix {} is owned by uid {}; refusing to treat it as managed".format(
                path, stat.st_uid
            )
        )


def check_layout(values, apps=False):
    if apps and any(not Path(value).is_absolute() for value in values):
        raise ValueError("App destinations must be absolute paths")
    # App bundles may live in /Applications or a user-selected apps volume.
    # Their existing-content ownership is checked separately before publication.
    paths = [Path(value).resolve() if apps else safe_path(value) for value in values]
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError("Managed paths overlap: {} and {}".format(left, right))


def _marker_names(key):
    """(this release's name, an earlier release's name) of marker KEY (see markers.py)."""
    return getattr(markers, key), legacy.MARKERS[key]


def read_marker(folder, key):
    """Text of marker KEY in FOLDER, under this release's name or an earlier one.

    None when neither exists. A symlink or non-file under either name, or both
    names holding different text, raise ValueError: whose folder it is would
    then be uncertain, so nothing may treat it as ours.
    """
    texts = []
    for name in _marker_names(key):
        path = Path(folder) / name
        if path.is_symlink():
            raise ValueError("Marker {} must not be a symlink".format(path))
        if path.is_file():
            texts.append(path.read_text())
        elif os.path.lexists(path):
            raise ValueError("Marker {} is not a file".format(path))
    if len(texts) == 2 and texts[0].strip() != texts[1].strip():
        raise ValueError(
            "The markers {} and {} in {} disagree; whose folder it is is uncertain".format(
                *_marker_names(key), folder
            )
        )
    return texts[0] if texts else None


def marker_present(folder, key):
    return any(os.path.lexists(Path(folder) / name) for name in _marker_names(key))


def adopt_marker(folder, key):
    """Give FOLDER this release's marker KEY when only an earlier release's exists.

    The new file is written beside it and renamed into place without replacing
    anything; the earlier one stays, so the folder is ours at every instant. A
    run stopped halfway leaves the earlier marker and a temporary file, and the
    next run finishes. Returns True when it wrote the new marker.
    """
    folder = Path(folder)
    new = folder / _marker_names(key)[0]
    text = read_marker(folder, key)
    if text is None or os.path.lexists(new):
        return False
    temporary = folder / ".{}.{}".format(new.name.lstrip("."), os.urandom(4).hex())
    with open(temporary, "x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        rename_exclusive(temporary, new)
    except FileExistsError:
        temporary.unlink()
        return False
    return True


# Prefix markers an earlier release's launcher reads: adopted, never removed.
PREFIX_MARKERS = ("PREFIX", "DIRECTX", "OPENAL")


def adopt_prefix_markers(prefix, title=None):
    """Write this release's prefix markers beside an earlier release's, for a prefix of ours."""
    check_prefix(str(prefix), title=title)
    prefix = safe_path(str(prefix))
    return [key for key in PREFIX_MARKERS if adopt_marker(prefix, key)]


def check_prefix(value, creating=False, title=None):
    """Accept exactly this title's marker; cross-title markers never pass."""
    path = safe_path(value)
    if not path.exists():
        if creating:
            return
        raise ValueError("Wine prefix is missing: {}".format(path))

    if not path.is_dir():
        raise ValueError("Wine prefix is not a directory: {}".format(path))
    _check_owned_directory(path)
    title = _current_title(title)
    for name in _marker_names("INITIALIZING"):
        initializing = path / name
        if initializing.is_symlink() or (initializing.exists() and not initializing.is_file()):
            raise ValueError("Invalid prefix initialization marker")
    for name in _marker_names("PREFIX"):
        if (path / name).is_symlink():
            raise ValueError("Prefix ownership marker must not be a symlink")
    text = read_marker(path, "PREFIX")
    if text is not None:
        identity = text.strip()
        expected = "{}:1".format(title)
        if identity == expected:
            return
        if identity in title_markers():
            raise ValueError(
                "Prefix {} belongs to another TrackMania title ({}, not {}); "
                "each title keeps its own prefix".format(path, identity, expected)
            )
        # Shipped TMNF installations used an empty marker. Require Wine layout
        # or the interrupted-initialization marker before accepting it, and only
        # for the reference title whose installed base that compatibility keeps.
        if not identity:
            if title != "tmnf":
                raise ValueError(
                    "Prefix {} has no ownership marker; title {} requires {}".format(
                        path, title, expected
                    )
                )
            if any(
                (path / name).is_file() and not (path / name).is_symlink()
                for name in ("system.reg", *_marker_names("INITIALIZING"))
            ):
                return
            raise ValueError(
                "Prefix {} carries a legacy empty marker and no Wine layout; "
                "choose a fresh TRACKMANIA_MAC_PREFIX".format(path)
            )
        raise ValueError("Unrecognized prefix ownership: {}".format(path))
    if creating and not any(path.iterdir()):
        return
    raise ValueError("Refusing a nonempty unmanaged prefix: {}".format(path))


def existing_directory(path):
    """Allow absent paths, but never mistake a broken link for an absent path."""
    if not os.path.lexists(path):
        for parent in path.parents:
            if os.path.lexists(parent):
                if not parent.resolve(strict=True).is_dir():
                    raise ValueError("Expected a directory: {}".format(parent))
                break
        return False
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Expected a directory: {}".format(path))
    return True


def profile_documents(prefix, links=False):
    for root in (prefix / "drive_c/users", prefix / "drive_c/Documents and Settings"):
        if not existing_directory(root):
            continue
        for profile in root.iterdir():
            if not existing_directory(profile):
                continue
            for name in ("Documents", "My Documents"):
                documents = profile / name
                if (links and documents.is_symlink()) or existing_directory(documents):
                    yield documents


def save_dir_names():
    """Save-directory names, joined from the authoritative title registry.

    The registry's save_dirs entries are the source of truth; config drift in
    either direction is a test failure, not a silent desync of the guards.
    """
    names = set()
    titles = Path(__file__).resolve().parents[4] / "config" / "titles"
    if titles.is_dir():
        for path in sorted(titles.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            for item in data.get("save_dirs") or ():
                if isinstance(item, str) and item and "/" not in item:
                    names.add(item)
    # Hard minimum for any prefix whose registry is unreadable: a legacy
    # uninstall must never quietly skip a known save family.
    names.update(
        (
            "TmForever",
            "TmUnited",
            "TrackMania",
            "TrackMania United",
            "ManiaPlanet",
            "Canyon",
            "Turbo",
            "Config",
            "Replays",
            "Tracks",
        )
    )
    return tuple(sorted(names))


_SHELL_FOLDER_SECTIONS = (
    "software\\microsoft\\windows\\currentversion\\explorer\\shell folders",
    "software\\microsoft\\windows\\currentversion\\explorer\\user shell folders",
)
_REG_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
}
_HEX_ESCAPE = re.compile("[0-9A-Fa-f]{1,4}")
_OCTAL_ESCAPE = re.compile("[0-7]{1,3}")
_USERPROFILE = "%USERPROFILE%"


def _unreadable_registry(registry, detail):
    return ValueError(
        "Cannot verify the Documents location in {} ({}); back up your saves manually "
        "before removing the prefix yourself".format(registry, detail)
    )


def _read_reg_string(text, index, terminator):
    """Read a Wine-escaped string from text[index:]; return (value, index past terminator).

    Wine's dump_strW writes backslash, the terminator, C escapes (\\n), octal and \\xHHHH
    (UTF-16 code unit) escapes. A surrogate pair stays two escapes and is joined here.
    """
    out: list[str] = []
    while index < len(text):
        char = text[index]
        index += 1
        if char == terminator:
            value = "".join(out)
            try:
                value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
            except UnicodeError as error:
                raise ValueError("lone surrogate escape") from error
            return value, index
        if char != "\\":
            out.append(char)
            continue
        if index >= len(text):
            break
        char = text[index]
        if char in _REG_ESCAPES:
            out.append(_REG_ESCAPES[char])
            index += 1
        elif char == "x":
            digits = _HEX_ESCAPE.match(text, index + 1)
            if not digits:
                raise ValueError("malformed \\x escape")
            out.append(chr(int(digits.group(), 16)))
            index = digits.end()
        elif char in "01234567":
            digits = _OCTAL_ESCAPE.match(text, index)
            # char is an octal digit, so the pattern always matches here.
            out.append(chr(int(digits.group(), 8)))  # type: ignore[union-attr]
            index = digits.end()  # type: ignore[union-attr]
        else:
            out.append(char)
            index += 1
    raise ValueError("unterminated string")


def _parse_reg_value(rest):
    """The string held by a Wine REG_SZ/REG_EXPAND_SZ value body (the text after '=')."""
    for tag in ('"', 'str:"', 'str(2):"'):
        if rest.startswith(tag):
            value, end = _read_reg_string(rest, len(tag), '"')
            if rest[end:].strip():
                raise ValueError("trailing text after the value")
            return value
    match = re.match(r"hex\([12]\):", rest, re.IGNORECASE)
    if not match:
        raise ValueError("not a string value")
    items = [item.strip() for item in rest[match.end() :].split(",")]
    if not all(re.fullmatch("[0-9A-Fa-f]{2}", item) for item in items):
        raise ValueError("malformed hex value")
    try:
        value = bytes(int(item, 16) for item in items).decode("utf-16-le")
    except UnicodeError as error:
        raise ValueError("malformed hex string") from error
    return value.rstrip("\0")


def _registry_text(registry):
    if registry.is_symlink() or not registry.is_file():
        raise _unreadable_registry(registry, "not a regular file")
    data = registry.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Wine escapes non-ASCII as \x, so this is a legacy or hand-edited file.
        return data.decode("latin-1")


def _personal_values(registry):
    """Raw 'Personal' values from the Shell Folders sections of a Wine user.reg.

    Real Wine format (server/registry.c): `WINE REGISTRY Version 2`, sections like
    `[Software\\\\Microsoft\\\\...\\\\Shell Folders] <time>`, values like
    `"Personal"="C:\\\\users\\\\name\\\\Documents"` and, for REG_EXPAND_SZ,
    `"Personal"=str(2):"%USERPROFILE%\\\\Documents"`; hex values wrap with a trailing
    backslash. Lines outside the two sections are never interpreted.
    """
    try:
        text = _registry_text(registry)
    except UnicodeError as error:
        raise _unreadable_registry(registry, "undecodable file") from error
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if lines and lines[-1].endswith("\\") and not lines[-1].startswith(("[", "#")):
            lines[-1] = lines[-1][:-1] + line.strip()
        else:
            lines.append(line)
    values = []
    section = None
    for number, line in enumerate(lines, 1):
        line = line.strip()
        try:
            if line.startswith("["):
                name, _ = _read_reg_string(line, 1, "]")
                section = name.casefold() in _SHELL_FOLDER_SECTIONS
            elif section and line and not line.startswith(("#", ";", "@")):
                if not line.startswith('"'):
                    raise ValueError("unrecognized line")
                name, end = _read_reg_string(line, 1, '"')
                if not line.startswith("=", end):
                    raise ValueError('missing "="')
                if name.casefold() == "personal":
                    values.append(_parse_reg_value(line[end + 1 :].strip()))
        except ValueError as error:
            raise _unreadable_registry(registry, "line {}: {}".format(number, error)) from error
    return values


def _profile_windows_paths(prefix):
    """Windows paths of each profile folder in the prefix, for %USERPROFILE%."""
    for root in ("users", "Documents and Settings"):
        folder = prefix / "drive_c" / root
        if existing_directory(folder):
            for profile in sorted(folder.iterdir()):
                if existing_directory(profile):
                    yield "C:\\{}\\{}".format(root, profile.name)


def _windows_location(prefix, value):
    """Map a Windows path to its location in the prefix, or None if it does not exist."""
    if not value or not re.match(r"[A-Za-z]:[\\/]", value):
        raise ValueError("unsupported path {!r}".format(value))
    parts = [part for part in re.split(r"[\\/]+", value[3:]) if part not in ("", ".")]
    for part in parts:
        if (
            part == ".."
            or ":" in part
            or part.endswith((" ", "."))
            or any(ord(char) < 32 for char in part)
        ):
            raise ValueError("unsupported path {!r}".format(value))
    letter = value[0].lower()
    if letter == "c":
        current = prefix / "drive_c"
    else:
        drive = prefix / "dosdevices" / (letter + ":")
        if not os.path.lexists(drive):
            return None
        current = drive.resolve(strict=True)
    if not os.path.lexists(current):
        return None
    for part in parts:
        candidate = current / part
        if not os.path.lexists(candidate):
            # Windows names are case-insensitive, and so is Wine's lookup.
            try:
                names = [name for name in os.listdir(current) if name.casefold() == part.casefold()]
            except (FileNotFoundError, NotADirectoryError):
                return None
            if not names:
                return None
            if len(names) > 1:
                raise ValueError("ambiguous path {!r}".format(value))
            candidate = current / names[0]
        current = candidate
    return current


def redirected_documents(prefix):
    """Existing locations the prefix's registry gives the Windows Personal shell folder.

    A missing user.reg is a fresh prefix and yields nothing. Anything else that cannot
    be read or mapped raises ValueError, so callers fail closed.
    """
    registry = prefix / "user.reg"
    if not os.path.lexists(registry):
        return []
    locations = []
    for value in _personal_values(registry):
        try:
            if "%" in value:
                if (
                    value[: len(_USERPROFILE)].upper() != _USERPROFILE
                    or "%" in value[len(_USERPROFILE) :]
                ):
                    raise ValueError("unsupported variable in {!r}".format(value))
                candidates = [
                    profile + value[len(_USERPROFILE) :]
                    for profile in _profile_windows_paths(prefix)
                ]
            else:
                candidates = [value]
            for candidate in candidates:
                location = _windows_location(prefix, candidate)
                if location is not None:
                    locations.append(location)
        except ValueError as error:
            raise _unreadable_registry(registry, error) from error
    return locations


def check_uninstall(prefix, userdata, title=None):
    """Read-only guard for every known save location in the prefix.

    Returns the resolved prefix path, the only path a caller may remove.
    Raises ValueError/OSError/RuntimeError if ownership or save safety is uncertain.
    The caller must hold the prefix operation lock through removal. Locations are the
    default profile Documents folders plus the Personal shell folder redirections in
    the prefix's user.reg, which is read only for that value and fails closed when it
    exists but cannot be understood (a missing user.reg is a fresh prefix).
    """
    check_layout([str(prefix), str(userdata)])
    check_prefix(str(prefix), title=title)
    prefix = safe_path(str(prefix))
    check_prefix_saves(prefix)
    return prefix


def check_prefix_saves(prefix):
    """Raise unless no user file in PREFIX's Documents would go with it.

    PREFIX is an already resolved folder; ownership is the caller's concern
    (check_uninstall checks the marker, an uninstall tombstone has none).
    """
    prefix = Path(prefix)
    max_depth = 32
    max_entries = 200_000
    state = {"entries": 0}

    def inspect(path, ancestors=(), depth=0):
        state["entries"] += 1
        if state["entries"] > max_entries:
            raise RuntimeError(
                "The save tree is too large to verify safely (>{}) before removal; "
                "back up user files manually and remove the prefix yourself".format(max_entries)
            )
        if depth > max_depth:
            raise RuntimeError(
                "The save tree nests deeper than {} levels; back up user files "
                "manually before removal".format(max_depth)
            )
        resolved = path.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError("Cyclic save location: {!r}".format(str(path)))
        if resolved.is_dir():
            for child in path.iterdir():
                inspect(child, ancestors + (resolved,), depth + 1)
        elif prefix in resolved.parents:
            raise ValueError("Back up user files before uninstalling: {!r}".format(str(path)))

    names = save_dir_names()
    checked = set()

    def check_location(documents):
        resolved = documents.resolve(strict=True)
        if resolved in checked:
            return
        checked.add(resolved)
        if resolved == prefix or prefix in resolved.parents:
            inspect(documents)
        else:
            # Outside Documents may contain a save-folder alias back into Wine.
            for name in names:
                child = documents / name
                if os.path.lexists(child):
                    inspect(child)

    for documents in profile_documents(prefix):
        check_location(documents)
    for documents in redirected_documents(prefix):
        check_location(documents)


def _links_to_userdata(path, userdata):
    """True when a Documents entry already resolves to the userdata folder."""
    try:
        return path.resolve(strict=True) == userdata
    except OSError:
        return False


def documents_linked(prefix, userdata):
    """Read-only: True when every Documents entry of the prefix is already userdata.

    This is the arrangement setup_documents leaves behind; a prefix with a
    legacy or custom Documents folder is not linked and nothing here changes it.
    """
    check_layout([str(prefix), str(userdata)])
    check_prefix(str(prefix))
    prefix, userdata = Path(prefix).resolve(), Path(userdata).resolve()
    documents = list(profile_documents(prefix, links=True))
    return bool(documents) and all(_links_to_userdata(path, userdata) for path in documents)


def setup_documents(prefix, userdata):
    """Redirect empty folders and proven fresh Wine defaults, never host data."""
    check_layout([str(prefix), str(userdata)])
    check_prefix(str(prefix))
    prefix, userdata = Path(prefix).resolve(), Path(userdata).resolve()
    # An initialization an earlier release began recorded it under its own name.
    baseline = next(
        (
            prefix / name
            for name in _marker_names("PROFILE_BASELINE")
            if os.path.lexists(prefix / name)
        ),
        prefix / markers.PROFILE_BASELINE,
    )
    provenance = None
    if os.path.lexists(baseline):
        if baseline.is_symlink() or not baseline.is_file():
            raise ValueError("Invalid profile initialization provenance")
        provenance = json.loads(baseline.read_text())
        if (
            not isinstance(provenance, dict)
            or provenance.get("version") != 1
            or not isinstance(provenance.get("home"), str)
            or not isinstance(provenance.get("account_home", provenance["home"]), str)
            or not isinstance(provenance.get("profiles"), list)
            or not all(isinstance(item, str) for item in provenance["profiles"])
        ):
            raise ValueError("Invalid profile initialization provenance")

    def fresh_default(path, name):
        return (
            provenance is not None
            and path.is_symlink()
            and path.parent.resolve(strict=True) == path.parent
            and str(path.parent.relative_to(prefix)) not in provenance["profiles"]
            and any(
                path.readlink() == Path(home) / name
                for home in (provenance["home"], provenance.get("account_home", provenance["home"]))
            )
        )

    documents = list(profile_documents(prefix, links=True))
    if not documents:
        raise ValueError(
            "Wine created no known Documents folder; back up custom save locations and repair the profile"
        )
    replacements = []
    for path in documents:
        if fresh_default(path, "Documents"):
            replacements.append(path)
            continue
        if path.is_symlink() and any(
            path.readlink() in (candidate, Path(candidate.name))
            and candidate.parent == path.parent
            and fresh_default(candidate, "Documents")
            for candidate in documents
            if candidate != path
        ):
            continue
        resolved = path.resolve(strict=True)
        if _links_to_userdata(path, userdata):
            continue
        # Wine commonly exposes My Documents as an alias of Documents.
        if path.is_symlink() and any(
            candidate != path
            and candidate.resolve(strict=True) == resolved
            and (not candidate.is_symlink() or fresh_default(candidate, "Documents"))
            for candidate in documents
        ):
            continue
        if path.is_symlink() or prefix not in resolved.parents or any(path.iterdir()):
            raise ValueError(
                "Existing Documents arrangement preserved: {}. Back up user files and explicitly link Documents to {} before retrying".format(
                    path, userdata
                )
            )
        replacements.append(path)
    userdata.mkdir(parents=True, exist_ok=True)
    for path in replacements:
        target = path.readlink() if path.is_symlink() else None
        if target is not None:
            path.unlink()  # Only the Wine-created link, never its host target.
        else:
            path.rmdir()  # Refuses if files appeared after preflight.
        try:
            path.symlink_to(userdata, target_is_directory=True)
        except OSError:
            if target is None:
                path.mkdir()
            else:
                path.symlink_to(target, target_is_directory=True)
            raise
    # Shield fresh Wine defaults from macOS privacy prompts. Preexisting and
    # custom profile links remain untouched, including links into the prefix.
    for profile in {path.parent for path in documents}:
        for name in ("Desktop", "Downloads", "Music", "Pictures", "Videos"):
            path = profile / name
            if fresh_default(path, name) or (name == "Videos" and fresh_default(path, "Movies")):
                target = path.readlink()
                path.unlink()
                try:
                    path.mkdir()
                except OSError:
                    path.symlink_to(target, target_is_directory=True)
                    raise


def record_profile_baseline(prefix):
    """Persist pre-Wineboot profiles once so retries recognize our default links."""
    check_prefix(str(prefix))
    prefix = Path(prefix).resolve()
    for name in _marker_names("PROFILE_BASELINE")[1:]:
        if os.path.lexists(prefix / name):
            return  # An earlier release recorded it; never recapture.
    baseline = prefix / markers.PROFILE_BASELINE
    if os.path.lexists(baseline):
        if baseline.is_symlink() or not baseline.is_file():
            raise ValueError("Invalid profile initialization provenance")
        return  # Never recapture Wine-created profiles on an interrupted retry.
    profiles: list[str] = []
    for root in (prefix / "drive_c/users", prefix / "drive_c/Documents and Settings"):
        if existing_directory(root):
            profiles.extend(str(path.relative_to(prefix)) for path in root.iterdir())
    with baseline.open("x") as stream:
        # Wine on macOS may use the OS account home rather than environment HOME.
        # Record names only; replacing our new links never requires opening targets.
        json.dump(
            dict(
                version=1,
                home=str(Path.home().resolve()),
                account_home=pwd.getpwuid(os.getuid()).pw_dir,
                profiles=profiles,
            ),
            stream,
        )
        stream.flush()
        os.fsync(stream.fileno())


# renamex_np flags from <stdio.h> on macOS.
RENAME_SWAP = 0x2
RENAME_EXCL = 0x4
# A link being swapped by remap_links; a leftover is removed on the next run.
RELINK_TEMP = re.compile(
    r"\.(?P<name>.+)(?:{}|{})\.[0-9a-f]{{8}}".format(
        re.escape(markers.RELINK_INFIX), re.escape(legacy.RELINK_INFIX)
    )
)


def _renamex(source, target, flags):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renamex_np", None)
    if function is None:
        raise OSError(errno.ENOTSUP, "renamex_np is not available", str(source))
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(os.fsencode(str(source)), os.fsencode(str(target)), flags) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source), None, str(target))


def rename_exclusive(source, target):
    """Rename SOURCE to TARGET atomically; fail if TARGET exists in any form.

    The kernel refuses an existing target (EEXIST), so no rename here can ever
    replace a file or folder, not even an empty one.
    """
    _renamex(source, target, RENAME_EXCL)


def swap(left, right):
    """Exchange two existing paths atomically; neither is ever missing."""
    _renamex(left, right, RENAME_SWAP)


def _profile_children(prefix):
    for root in (prefix / "drive_c/users", prefix / "drive_c/Documents and Settings"):
        if root.is_symlink() or not root.is_dir():
            continue
        for profile in sorted(root.iterdir()):
            if profile.is_symlink() or not profile.is_dir():
                continue
            yield from sorted(profile.iterdir())


def _link_candidates(prefix):
    """Symlinks Wine and setup_documents create to host folders: drives and profile folders."""
    dosdevices = prefix / "dosdevices"
    if not dosdevices.is_symlink() and dosdevices.is_dir():
        yield from sorted(dosdevices.iterdir())
    yield from _profile_children(prefix)


def _folded_parts(text):
    """Path components as APFS compares them: Unicode NFC, case folded."""
    return [unicodedata.normalize("NFC", part).casefold() for part in text.split("/") if part]


def _moved(text, mapping):
    """TEXT's new spelling when it is, or lies below, an old path of MAPPING.

    Compared and cut by whole components, so a case or NFC/NFD difference in the
    old path never shifts where the rest of TEXT starts.
    """
    if not text.startswith("/"):
        return None
    parts = [part for part in text.split("/") if part]
    folded = _folded_parts(text)
    # Longest old path first: a moved folder inside another moved folder wins.
    for old, new in sorted(
        mapping, key=lambda pair: len(_folded_parts(str(pair[0]))), reverse=True
    ):
        old_folded = _folded_parts(str(old))
        if folded[: len(old_folded)] == old_folded:
            rest = parts[len(old_folded) :]
            return "/".join([str(new).rstrip("/")] + rest)
    return None


def remap_links(prefix, mapping):
    """Point the prefix's host links that lead into a moved folder at its new place.

    MAPPING holds (old, new) absolute paths. Only symlinks among the prefix's
    drives (dosdevices) and profile folders (setup_documents' Documents links)
    whose text is an absolute path at or below an old path change; names
    compare casefolded, as on APFS. A link is never followed and nothing that
    is not a symlink is touched. Each link is replaced by swapping in a new one
    (renamex_np RENAME_SWAP), so the name never goes missing, and the swapped
    out link is then removed. Returns the links that changed.
    """
    prefix = safe_path(str(prefix))
    changed = []
    for path in list(_link_candidates(prefix)):
        if RELINK_TEMP.fullmatch(path.name):
            if path.is_symlink():
                path.unlink()  # Left by an interrupted swap; its partner is in place.
            continue
        if not path.is_symlink():
            continue
        target = _moved(os.readlink(path), mapping)
        if target is None:
            continue
        temporary = path.with_name(
            ".{}{}.{}".format(path.name, markers.RELINK_INFIX, os.urandom(4).hex())
        )
        os.symlink(target, temporary)
        try:
            swap(temporary, path)
        except BaseException:
            temporary.unlink()
            raise
        temporary.unlink()
        changed.append(path)
    return changed


# Exit status of prefix-layout when the prefix is fine but the layout is not.
# Any other failure (status 1) is a prefix failure, as for the `prefix` command.
LAYOUT_FAILED = 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "path",
            "layout",
            "app-layout",
            "prefix",
            "new-prefix",
            "uninstall",
            "setup-documents",
            "documents-linked",
            "profile-baseline",
            "prefix-layout",
            "adopt-markers",
        ),
    )
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    try:
        if args.command in ("uninstall", "setup-documents", "documents-linked"):
            if len(args.paths) != 2:
                raise ValueError("{} requires PREFIX USERDATA".format(args.command))
            if args.command == "uninstall":
                resolved_prefix = check_uninstall(*args.paths)
                # The one path the caller may remove: resolved once, already verified.
                print(resolved_prefix)
            elif args.command == "documents-linked":
                # A query, not a failure: exit 1 without a message when unlinked.
                return 0 if documents_linked(*args.paths) else 1
            else:
                setup_documents(*args.paths)
        elif args.command == "profile-baseline":
            if len(args.paths) != 1:
                raise ValueError("profile-baseline requires PREFIX")
            record_profile_baseline(args.paths[0])
        elif args.command == "adopt-markers":
            if len(args.paths) != 1:
                raise ValueError("adopt-markers requires PREFIX")
            adopt_prefix_markers(args.paths[0])
        elif args.command == "prefix-layout":
            # `prefix PATHS[0]` then `layout PATHS`, in one process. The prefix is
            # checked first, so its error wins exactly as it does for two calls.
            check_prefix(args.paths[0])
            try:
                check_layout(args.paths)
            except (OSError, ValueError, RuntimeError) as error:
                print("error: {}".format(error), file=sys.stderr)
                return LAYOUT_FAILED
        elif args.command in ("layout", "app-layout"):
            check_layout(args.paths, apps=args.command == "app-layout")
        elif args.command == "path":
            for path in args.paths:
                safe_path(path)
        else:
            for path in args.paths:
                check_prefix(path, creating=args.command == "new-prefix")
    except (OSError, ValueError, RuntimeError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
