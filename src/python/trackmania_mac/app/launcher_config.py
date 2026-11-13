"""Read an installed launcher's config/installation.sh without running it.

install_app (src/shell/lib/apps/wrappers.sh) writes one line per setting,
`if test -z "${NAME:-}"; then export NAME=VALUE; fi`, with VALUE quoted by
`printf %q`. `word_value` decodes such a word the way `shell_word_value` in
wrappers.sh does (tests/test_launcher_config.py checks that they agree), and
`read` accepts a file only when every line has that form, so nothing in it is
ever run or guessed at.

The launch options the hub shows are checked against the same values
`validate_launch_options` in src/shell/lib/common.sh accepts; the shell stays
the authority when they are written.
"""

from __future__ import annotations

import os
import re
import stat
import typing
from pathlib import Path

from trackmania_mac import legacy

CONFIG = Path("config") / "installation.sh"
LINE = re.compile(r'if test -z "\$\{([A-Z0-9_]+):-\}"; then export ([A-Z0-9_]+)=(.+); fi')
# Settings are named in the dialect of the release that wrote the launcher.
DIALECTS = (legacy.NEW_ENV_PREFIX, legacy.ENV_PREFIX)
MAX_BYTES = 64 * 1024
# Outside quotes `printf %q` escapes each of these, so none stands bare.
_SPECIAL = frozenset("][\\'\"$`;&|<>(){}*?!#~")
_OCTAL = re.compile(r"\\([0-7]{1,3})")

# The launch options, by setting name without its dialect prefix, and their
# defaults in src/shell/lib/common.sh.
LAUNCH_DEFAULTS = {"WINDOW_SIZE": "fullscreen", "AUDIO": "on", "X87": "on", "RENDERER": "d9vk"}
RENDERERS = ("d9vk", "wined3d-vulkan", "wined3d-gl")
_WINDOW_SIZE = re.compile(r"([1-9][0-9]{0,4})x([1-9][0-9]{0,4})")


class LauncherConfigError(ValueError):
    pass


def _control(char: str) -> bool:
    return ord(char) < 32 or ord(char) == 127


def word_value(word: str) -> str:
    """The value of one shell word as `printf %q` writes it; ValueError otherwise."""
    if word == "''":
        return ""
    if not word:
        raise LauncherConfigError("empty word")
    out = bytearray()
    index = 0
    while index < len(word):
        char = word[index]
        if char == "\\":
            if index + 1 >= len(word):
                raise LauncherConfigError("trailing backslash")
            out += word[index + 1].encode("utf-8")
            index += 2
        elif word.startswith("$'", index):
            index += 2
            while True:
                if index >= len(word):
                    raise LauncherConfigError("unterminated $'...'")
                char = word[index]
                if char == "'":
                    index += 1
                    break
                if char == "\\":
                    octal = _OCTAL.match(word, index)
                    if octal:
                        code = int(octal.group(1), 8)
                        if code < 32 or code > 255:
                            raise LauncherConfigError("control character")
                        out.append(code)
                        index = octal.end()
                    elif word[index + 1 : index + 2] in ("\\", "'"):
                        out += word[index + 1].encode("utf-8")
                        index += 2
                    else:
                        raise LauncherConfigError("unknown escape")
                elif _control(char):
                    raise LauncherConfigError("control character")
                else:
                    out += char.encode("utf-8")
                    index += 1
        elif char in _SPECIAL or char in " \t\n\v\f\r" or _control(char):
            raise LauncherConfigError("unquoted special character")
        else:
            out += char.encode("utf-8")
            index += 1
    try:
        value = out.decode("utf-8")
    except UnicodeDecodeError:
        raise LauncherConfigError("not UTF-8") from None
    if any(_control(char) for char in value):
        raise LauncherConfigError("control character")
    return value


def read(path: Path) -> typing.Dict[str, str]:
    """Settings by name without their dialect prefix (TITLE, RUNTIME, AUDIO, ...).

    Raises LauncherConfigError when the file is not a plain file of ours, a
    line is not in the form install_app writes, or the dialect is unknown;
    OSError when it cannot be read.
    """
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise LauncherConfigError("{} is not a plain file of this user".format(path))
    if info.st_size > MAX_BYTES:
        raise LauncherConfigError("{} is too large".format(path))
    names: typing.Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if not match or match.group(1) != match.group(2):
            raise LauncherConfigError("{} has a line not in the expected form".format(path))
        names[match.group(1)] = word_value(match.group(3))
    dialect = next((prefix for prefix in DIALECTS if prefix + "TITLE" in names), None)
    if dialect is None:
        raise LauncherConfigError("{} does not name its game".format(path))
    if any(not name.startswith(dialect) for name in names):
        raise LauncherConfigError("{} mixes setting names of two releases".format(path))
    return {name[len(dialect) :]: value for name, value in names.items()}


def valid_launch_option(key: str, value: str) -> bool:
    """What validate_launch_options in common.sh accepts for KEY (no dialect prefix)."""
    if key in ("AUDIO", "X87"):
        return value in ("on", "off")
    if key == "RENDERER":
        return value in RENDERERS
    if key == "WINDOW_SIZE":
        if value == "fullscreen":
            return True
        match = _WINDOW_SIZE.fullmatch(value)
        return match is not None and all(int(part) <= 65535 for part in match.groups())
    raise KeyError(key)
