"""Seed a title's default settings file into its game folder, never replacing one.

Some titles read their graphics settings from a file in the game folder rather
than from Windows Documents, and no installer ships it: the title's settings
launcher writes it the first time. The registry's `game_system_config` names
the file and its format, and install writes a minimal one when none exists, so
the first start already uses the runtime's defaults.

`text-2006` is the text GBX TrackMania Nations ESWC reads as
Default.SystemConfig.Gbx: the 9-byte header `GBX`, version 6, `TUUE`, then
tokens each followed by CRLF. The body is class CSystemConfig (0x0B005000) with
its three header fields, then chunks: a chunk id, the skip marker 1397442896
("PIKS" read as a little-endian integer), the byte length of the chunk's tokens
with their CRLFs, then the tokens. A nested node ends with `facade01`, and the
file ends with `facade01` and one empty token. The only chunk written is the
display node 0x0B005020 (IsSafeMode False), holding:

- 0x0B013001: width, height, then `3 1 0 0 False` as the launcher writes them
  (meaning unknown), then fullscreen, written False (windowed);
- 0x0B013003: `False`, the Cpu/Gpu synchro index, `False`, `0`, optimize
  dynamic geometry (`False`), `True`. The index is the launcher's dropdown
  position (None 0, 3 Frames 1, 2.5 2, 2 3, 1.5 4, 1 Frame 5, Half 6,
  Immediate 7); it is written as 1, 3 Frames.

The per-install key (chunk 0x0B005012) is never written. The game fills
everything else with its own defaults. Width and height come from
config/game-settings.json, the values the Forever titles' seeded settings use.

    python -m trackmania_mac.install.system_config seed GAME_DIR SETTINGS_JSON

The title is TRACKMANIA_MAC_TITLE, read from the registry; a title without the
field gets nothing. The file is written to a temporary file in the game folder
and hard-linked into place, so it appears complete or not at all, and an
existing file, folder or symlink of that name (in any case) is left as it is.
Exit status 0 when the file was written or already there, 1 on an error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import stat
import sys
import tempfile
import typing

from trackmania_mac.guard import managed_paths
from trackmania_mac.hub import registry

TEMP_PREFIX = ".trackmania-mac-system-config."
FILE_MODE = 0o644
# "PIKS" read as a little-endian 32-bit integer: the chunk may be skipped.
SKIP = "1397442896"
END = "facade01"
TEXT_2006_HEADER = b"GBX\x06\x00TUUE"
# The launcher's Cpu/Gpu synchro dropdown position for "3 Frames".
SYNCHRO_3_FRAMES = 1
_INT_MAX = 0x7FFFFFFF


def _chunk(ident: str, tokens: typing.List[str]) -> typing.List[str]:
    """A text-GBX chunk: id, skip marker, byte length of its tokens with CRLFs, tokens."""
    length = sum(len(token.encode("ascii")) + 2 for token in tokens)
    return [ident, SKIP, str(length)] + tokens


def _size(settings: typing.Mapping[str, object]) -> typing.Tuple[int, int]:
    # Signed 32-bit GBX integers, the limits game_profile.generate checks too:
    # an encoding limit, not a claim about resolutions the game supports.
    values = []
    for name in ("width", "height"):
        value = settings.get(name)
        if type(value) is not int or not 1 <= value <= _INT_MAX:
            raise ValueError(f"{name} must be an integer in 1..{_INT_MAX}")
        values.append(value)
    return values[0], values[1]


def text_2006(settings: typing.Mapping[str, object]) -> bytes:
    """The minimal Nations ESWC Default.SystemConfig.Gbx: windowed, 3 Frames synchro."""
    width, height = _size(settings)
    display = _chunk(
        "b013001", [str(width), str(height), "3", "1", "0", "0", "False", "False"]
    ) + _chunk("b013003", ["False", str(SYNCHRO_3_FRAMES), "False", "0", "False", "True"])
    tokens = ["b005000", "0", "1", "0"]
    tokens += _chunk("b005020", ["False"] + display + [END])
    tokens += [END, ""]
    return TEXT_2006_HEADER + "\r\n".join(tokens).encode("ascii")


GENERATORS: typing.Dict[str, typing.Callable[[typing.Mapping[str, object]], bytes]] = {
    "text-2006": text_2006,
}


def generate(format_name: str, settings: typing.Mapping[str, object]) -> bytes:
    if format_name not in GENERATORS:
        raise ValueError(f"Unknown settings format: {format_name}")
    return GENERATORS[format_name](settings)


def _game_folder(game_dir: str) -> pathlib.Path:
    folder = pathlib.Path(game_dir)
    if not folder.is_absolute():
        raise ValueError(f"The game folder must be an absolute path: {game_dir}")
    info = os.lstat(folder)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Not a game folder: {folder}")
    return folder


def _sweep(folder: pathlib.Path) -> None:
    """Remove temporary files a killed run left; the prefix lock rules out a live one."""
    for child in os.listdir(folder):
        if not child.startswith(TEMP_PREFIX):
            continue
        info = os.lstat(folder / child)
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
            os.unlink(folder / child)


def _taken(folder: pathlib.Path, name: str) -> bool:
    # APFS compares names ignoring case; listing also covers a case-sensitive volume.
    wanted = name.casefold()
    return os.path.lexists(folder / name) or any(
        child.casefold() == wanted for child in os.listdir(folder)
    )


def seed(game_dir: str, name: str, data: bytes) -> bool:
    """Write DATA to GAME_DIR/NAME unless that name exists; True when written."""
    folder = _game_folder(game_dir)
    # The registry's rule for a plain file name in the game folder.
    if not registry._FILE_NAME.fullmatch(name):
        raise ValueError(f"Unsafe file name: {name!r}")
    _sweep(folder)
    if _taken(folder, name):
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(folder))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), FILE_MODE)
            os.fsync(handle.fileno())
        try:
            # link() never replaces or follows anything at the new name.
            os.link(temporary, folder / name)
        except FileExistsError:
            return False
        return True
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("seed")
    command.add_argument("game_dir")
    command.add_argument("settings")
    args = parser.parse_args(argv)
    try:
        title = registry.get(managed_paths._current_title())
        config = title.game_system_config
        if config is None:
            return 0
        settings = json.loads(pathlib.Path(args.settings).read_text())
        if not isinstance(settings, dict):
            raise ValueError(f"{args.settings} must hold a JSON object")
        data = generate(config.format, settings)
        target = pathlib.Path(args.game_dir) / config.file
        if seed(args.game_dir, config.file, data):
            print(f"Wrote the default settings to {target}")
        else:
            print(f"Kept the existing {target}")
    except (OSError, ValueError, registry.RegistryError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
