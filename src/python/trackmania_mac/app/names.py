"""The names of a title's installed apps.

A title's Game and Settings apps take their names from the registry
(`app_name`, `settings_app_name`) unless its root (`games/<id>` in the data
folder) holds an `app-name` file. That file is one line, a base name B, and
the apps are then `B.app` and `B Settings.app`. This module is the only place
that reads or checks the file; the shell takes the effective names from
`python -m trackmania_mac.app.names export ROOT`, and `guard.app_rename`
changes them.

Names compare as APFS compares them: case-insensitively and regardless of
Unicode normalisation (`key`).
"""

from __future__ import annotations

import os
import shlex
import stat
import sys
import typing
import unicodedata
from pathlib import Path

from trackmania_mac.hub import registry

FILE = "app-name"
MAX_CHARACTERS = 64
# "<name> Settings.app" must stay under the 255-byte file name limit.
MAX_BYTES = 200
MAX_FILE_BYTES = 1024
SETTINGS_SUFFIX = " Settings"
# Control, format (bidi overrides, zero-width) and line or paragraph separators.
_UNPRINTABLE = frozenset(("Cc", "Cf", "Zl", "Zp"))


class Names(typing.NamedTuple):
    game: str  # the Game app's file name, "<base>.app"
    settings: str  # the Settings app's file name
    base: str  # the Game app's name without ".app"
    custom: bool  # an app-name file sets the names
    problem: typing.Optional[str]  # why a present app-name file is ignored


def validate(raw: str) -> str:
    """RAW trimmed and NFC-normalised; ValueError with a sentence for the user."""
    name = unicodedata.normalize("NFC", raw.strip())
    if not name:
        raise ValueError("Enter a name for the apps.")
    if len(name) > MAX_CHARACTERS:
        raise ValueError(f"Use a name of at most {MAX_CHARACTERS} characters.")
    if len(name.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Use a shorter name.")
    if "/" in name or ":" in name:
        raise ValueError("A name cannot contain / or :.")
    if name.startswith("."):
        raise ValueError("A name cannot start with a dot.")
    if any(unicodedata.category(char) in _UNPRINTABLE for char in name):
        raise ValueError("A name cannot contain control or invisible characters.")
    if name.casefold().endswith(".app"):
        raise ValueError("Leave out .app; it is added for you.")
    return name


def key(name: str) -> str:
    """NAME as APFS compares it: case-folded, normalisation-insensitive."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())


def default_base(title: registry.Title) -> str:
    """The registry's Game app name without ".app"."""
    return title.app_name[: -len(".app")]


def file_names(base: str) -> typing.Tuple[str, str]:
    """(Game, Settings) file names for a custom BASE."""
    return f"{base}.app", f"{base}{SETTINGS_SUFFIX}.app"


def read(root: typing.Union[str, Path]) -> typing.Optional[str]:
    """The base name ROOT's app-name file records, or None without one.

    ValueError when the file is a symlink, not a regular file, too large, or
    does not hold a valid name.
    """
    path = Path(root) / FILE
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path} is not a regular file")
    if info.st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path} is too large")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{path} is too large")
    text = data.decode("utf-8")  # UnicodeDecodeError is a ValueError
    if text.endswith("\n"):
        text = text[:-1]
    return validate(text)


def defaults(title: registry.Title) -> Names:
    return Names(title.app_name, title.settings_app_name, default_base(title), False, None)


def effective(title: registry.Title, root: typing.Union[str, Path]) -> Names:
    """The names the title's apps have now. Never raises for a bad app-name file:
    the registry names apply and `problem` says why."""
    try:
        base = read(root)
    except (OSError, ValueError) as error:
        return defaults(title)._replace(problem=str(error))
    if base is None:
        return defaults(title)
    game, settings = file_names(base)
    return Names(game, settings, base, True, None)


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[0] != "export":
        print("usage: python -m trackmania_mac.app.names export ROOT", file=sys.stderr)
        return 2
    try:
        title = registry.get(os.environ.get("TRACKMANIA_MAC_TITLE") or "")
    except registry.RegistryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    names = effective(title, arguments[1])
    if names.problem is not None:
        print(
            f"warning: the default app names apply: {names.problem}. "
            "Reset the app name in the hub to fix it.",
            file=sys.stderr,
        )
    print(f"_trackmania_mac_app_file={shlex.quote(names.game)}")
    print(f"_trackmania_mac_settings_app_file={shlex.quote(names.settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
