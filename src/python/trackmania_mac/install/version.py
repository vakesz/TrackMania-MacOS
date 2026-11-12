"""Read the release version: [project] version in pyproject.toml.

uv.lock mirrors it. src/shell/lib/version.sh reads it the same way, and
tests/test_project.py checks that both readers agree. Runs on Python 3.9,
which has no tomllib, so this reads only the one key it needs.
"""

from __future__ import annotations

import pathlib
import re
import typing

# The value ends up in runtime paths and release URLs.
_FORMAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_HEADER = re.compile(r"\s*\[")
_KEY = re.compile(r"\s*version\s*=")
_VALUE = re.compile(r'\s*version\s*=\s*"([^"]*)"\s*(?:#.*)?')


class VersionError(ValueError):
    """pyproject.toml has no usable [project] version."""


def parse(text: str) -> typing.Optional[str]:
    """The first version value in the [project] table, if it is a quoted string."""
    in_project = False
    for line in text.splitlines():
        if _HEADER.match(line):
            in_project = line.split("#", 1)[0].strip() == "[project]"
            continue
        if in_project and _KEY.match(line):
            match = _VALUE.fullmatch(line)
            return match.group(1) if match else None
    return None


def read(root: typing.Union[str, pathlib.Path]) -> str:
    """The [project] version from ROOT/pyproject.toml.

    Raises OSError when the file cannot be read and VersionError when the
    value is missing or not a plain token.
    """
    path = pathlib.Path(root) / "pyproject.toml"
    version = parse(path.read_text(encoding="utf-8"))
    if version is None or not _FORMAT.fullmatch(version):
        raise VersionError(f"{path} has no valid [project] version (got {version!r})")
    return version
