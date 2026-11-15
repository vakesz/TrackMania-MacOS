"""Patch a title's game executable, and tell how far an install got with it.

A registry entry may name a patch for its game executable (`game_exe_patch`: a file
under config/exe-patches and the executable's SHA-256 before the patch;
game_exe_sha256 is the hash after it). A patch file adds one section of code to the
32-bit PE image and writes a 5-byte relative jmp into that code at each hook. Nations
ESWC uses one to run its particle loop as SSE2 code instead of x87 ("Nations ESWC
particle speed patch" in docs/runtime.md); the code is built from the folder its
`source` names.

The patch is applied only when the executable has exactly the `before` hash, every
hook holds the bytes the patch replaces, the section table has room for one more
entry, nothing follows the last section, and the result has game_exe_sha256.
Otherwise nothing is written. The executable is read and replaced as in
trackmania_mac.install.pe_flags: one descriptor on a regular file, a temporary file
renamed over it, permission bits kept.

    python -m trackmania_mac.install.exe_patch apply GAME_DIR
    python -m trackmania_mac.install.exe_patch stage GAME_DIR

`apply` patches the executable unless it already has game_exe_sha256. `stage` prints
how far the executable got through the steps after the overlay: `final` (it has
game_exe_sha256), `patch` (it has the patch's before hash), `nx` (setting NX_COMPAT
gives the hash the next step needs) or `unknown`. The title is TRACKMANIA_MAC_TITLE.
Exit status 1 on an error.

A patch file is JSON:

    {"format": "trackmania-mac-exe-patch", "version": 1, "summary": TEXT, "source": PATH,
     "section": {"name": ".sse", "rva": "0x8e8000", "data": [BASE64 LINES], "sha256": HEX},
     "hooks": [{"va": "0x810685", "replaces": "8b54247c8b", "target": "0x0"}, ...]}

The section starts at `rva`, which must be the image's current end; `target` is an
offset into its data. The section is code that can be executed and read, never written.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import pathlib
import re
import struct
import sys
import typing

from trackmania_mac.guard import managed_paths
from trackmania_mac.hub import registry
from trackmania_mac.install import pe_flags

FORMAT = "trackmania-mac-exe-patch"
VERSION = 1
MAX_PATCH_FILE = 1 << 20
SECTION_CHARACTERISTICS = 0x60000020  # code, execute, read
_EXECUTE = 0x20000000
_HEX = re.compile(r"^0x[0-9a-f]+$")
_SECTION_NAME = re.compile(r"^\.[a-z0-9]{1,7}$")


class Hook(typing.NamedTuple):
    va: int
    replaces: bytes
    target: int


class Patch(typing.NamedTuple):
    name: str
    rva: int
    data: bytes
    hooks: typing.Tuple[Hook, ...]


def _keys(value: object, keys: typing.Set[str], what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("{} needs exactly {}".format(what, ", ".join(sorted(keys))))
    return value


def _number(value: object, what: str) -> int:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("{} must be a lowercase hex number like 0x1f".format(what))
    return int(value, 16)


def load(path: pathlib.Path) -> Patch:
    """The patch file at PATH, checked."""
    with open(path, "rb") as handle:
        text = handle.read(MAX_PATCH_FILE + 1)
    if len(text) > MAX_PATCH_FILE:
        raise ValueError("{} is over {} KiB".format(path, MAX_PATCH_FILE >> 10))
    document = _keys(
        json.loads(text),
        {"format", "version", "summary", "source", "section", "hooks"},
        "a patch file",
    )
    version = document["version"]
    if document["format"] != FORMAT or type(version) is not int or version != VERSION:
        raise ValueError("{} is not a version {} {} file".format(path, VERSION, FORMAT))
    section = _keys(document["section"], {"name", "rva", "data", "sha256"}, "section")
    name, lines = section["name"], section["data"]
    if not isinstance(name, str) or not _SECTION_NAME.fullmatch(name):
        raise ValueError("section name must be a dot and up to 7 lowercase letters or digits")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("section data must be a list of base64 lines")
    try:
        data = base64.b64decode("".join(lines), validate=True)
    except binascii.Error as error:
        raise ValueError("section data is not base64: {}".format(error)) from error
    if not data or hashlib.sha256(data).hexdigest() != section["sha256"]:
        raise ValueError("section data does not match its sha256")
    hooks = document["hooks"]
    if not isinstance(hooks, list) or not hooks:
        raise ValueError("hooks must be a non-empty list")
    parsed = []
    for hook in hooks:
        hook = _keys(hook, {"va", "replaces", "target"}, "a hook")
        replaces = hook["replaces"]
        if not isinstance(replaces, str) or not re.fullmatch(r"[0-9a-f]{10}", replaces):
            raise ValueError("a hook must replace 5 bytes, written as 10 lowercase hex digits")
        target = _number(hook["target"], "hook target")
        if target >= len(data):
            raise ValueError("hook target {:#x} is outside the section".format(target))
        parsed.append(Hook(_number(hook["va"], "hook va"), bytes.fromhex(replaces), target))
    return Patch(name, _number(section["rva"], "section rva"), data, tuple(parsed))


def _align(value: int, alignment: int) -> int:
    return -(-value // alignment) * alignment


def patched(image: bytes, patch: Patch) -> bytes:
    """IMAGE with PATCH's section added and its hooks written; ValueError if it does not fit."""
    try:
        return _patched(image, patch)
    except (struct.error, OverflowError) as error:
        raise ValueError("malformed PE file: {}".format(error)) from error


def _patched(image: bytes, patch: Patch) -> bytes:
    (pe,) = struct.unpack_from("<I", image, 0x3C)
    if image[:2] != b"MZ" or image[pe : pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    (count,) = struct.unpack_from("<H", image, pe + 6)
    (optional_size,) = struct.unpack_from("<H", image, pe + 20)
    optional = pe + 24
    image_base, section_alignment, file_alignment = struct.unpack_from("<III", image, optional + 28)
    size_of_image, size_of_headers = struct.unpack_from("<II", image, optional + 56)
    for alignment in (section_alignment, file_alignment):
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("PE alignment {:#x} is not a power of two".format(alignment))
    table = optional + optional_size
    # name, virtual size, rva, raw size, raw offset, characteristics
    sections = [
        struct.unpack_from("<8sIIII", image, table + 40 * i)
        + struct.unpack_from("<I", image, table + 40 * i + 36)
        for i in range(count)
    ]
    if not sections:
        raise ValueError("the PE file has no sections")
    slot = table + 40 * count
    first_data = min([size_of_headers] + [raw for _, _, _, size, raw, _ in sections if size])
    if slot + 40 > first_data or any(image[slot : slot + 40]):
        raise ValueError("the PE section table has no room for another section")
    if max(raw + size for _, _, _, size, raw, _ in sections) != len(image):
        raise ValueError("data follows the last PE section")
    if len(image) % file_alignment or patch.rva != size_of_image:
        raise ValueError("the patch section does not start at the end of the image")

    result = bytearray(image)
    hooks = sorted(patch.hooks)
    if any(later.va < earlier.va + 5 for earlier, later in zip(hooks, hooks[1:])):
        raise ValueError("the patch's hooks overlap")
    for hook in hooks:
        rva = hook.va - image_base
        offset = next(
            (
                raw + rva - start
                for _, virtual, start, size, raw, characteristics in sections
                if characteristics & _EXECUTE and start <= rva <= start + min(virtual, size) - 5
            ),
            None,
        )
        if offset is None:
            raise ValueError("hook {:#x} is not in code".format(hook.va))
        if result[offset : offset + 5] != hook.replaces:
            raise ValueError(
                "hook {:#x} does not hold the bytes the patch replaces".format(hook.va)
            )
        distance = image_base + patch.rva + hook.target - (hook.va + 5)
        if not -(1 << 31) <= distance < 1 << 31:
            raise ValueError("hook {:#x} is too far from the patch section".format(hook.va))
        result[offset : offset + 5] = b"\xe9" + struct.pack("<i", distance)

    raw_size = _align(len(patch.data), file_alignment)
    struct.pack_into(
        "<8sIIIIIIHHI",
        result,
        slot,
        patch.name.encode(),
        len(patch.data),
        patch.rva,
        raw_size,
        len(image),
        0,
        0,
        0,
        0,
        SECTION_CHARACTERISTICS,
    )
    struct.pack_into("<H", result, pe + 6, count + 1)
    struct.pack_into(
        "<I", result, optional + 56, patch.rva + _align(len(patch.data), section_alignment)
    )
    result += patch.data + bytes(raw_size - len(patch.data))
    return bytes(result)


def _game_exe(game_dir: str, title: registry.Title) -> pathlib.Path:
    folder = pathlib.Path(game_dir)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("Not a game folder: {}".format(folder))
    return folder / pe_flags.plain_name(title.game_exe)


def apply(game_dir: str, title: registry.Title) -> bool:
    """Patch the title's executable in GAME_DIR; True when the file was changed."""
    if title.game_exe_patch is None:
        return False
    path = _game_exe(game_dir, title)
    pe_flags.sweep_temporaries(game_dir)
    data, mode = pe_flags.read_file(path)
    digest = hashlib.sha256(data).hexdigest()
    if digest == title.game_exe_sha256:
        return False
    if digest != title.game_exe_patch.sha256_before:
        raise ValueError("{} is not the build the patch is for".format(path))
    result = patched(data, load(registry.exe_patches_dir() / title.game_exe_patch.file))
    if hashlib.sha256(result).hexdigest() != title.game_exe_sha256:
        raise ValueError("Patching {} did not give the pinned build".format(path))
    pe_flags.replace(path, result, mode)
    return True


def stage(game_dir: str, title: registry.Title) -> str:
    """final, patch, nx or unknown: see the module documentation."""
    path = _game_exe(game_dir, title)
    data, _ = pe_flags.read_file(path)
    digest = hashlib.sha256(data).hexdigest()
    if digest == title.game_exe_sha256:
        return "final"
    patch = title.game_exe_patch
    if patch is not None and digest == patch.sha256_before:
        return "patch"
    after_nx = patch.sha256_before if patch is not None else title.game_exe_sha256
    flagged = {name.casefold() for name in title.nx_compat}
    if (
        after_nx
        and title.game_exe.casefold() in flagged
        and pe_flags.pending_nx(after_nx, str(path))
    ):
        return "nx"
    return "unknown"


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("apply", "stage"):
        commands.add_parser(command).add_argument("game_dir")
    args = parser.parse_args(argv)
    try:
        title = registry.get(managed_paths._current_title())
        if args.command == "stage":
            print(stage(args.game_dir, title))
        else:
            apply(args.game_dir, title)
    except (OSError, ValueError, registry.RegistryError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
