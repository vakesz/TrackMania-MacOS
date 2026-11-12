"""ModLoader (TMLoader) layout for the Competition Patch: the one definition.

Install, verification and Play in the shell, and the hub's readiness probe,
all read these paths. The shell gets them through
`python -m tmnf.hub.registry export` (the same single interpreter start that
loads the title), so it only hashes the files and never restates the layout.
Paths are POSIX and relative: to the prefix for PREFIX_DIR, and to the
ModLoader directory for everything else.
"""

from __future__ import annotations

import typing

# ModLoader's program for the Forever engine and the profile Play starts.
PROGRAM_ID = "TmForever"
PROFILE = "competition"
PREFIX_DIR = "drive_c/Program Files/TMLoader"


class PinnedFile(typing.NamedTuple):
    shell_name: str
    relpath: str
    sha256: str
    label: str


def _product(name: str, version: str, filename: str) -> str:
    return f"database/{PROGRAM_ID}/products/{name}/{version}/{filename}"


def pinned_files(pins: typing.Mapping[str, str]) -> typing.List[PinnedFile]:
    """Every file the pinned ModLoader bundle must provide, in verification order."""
    return [
        PinnedFile(
            "TMLOADER_EXE_REL", "TMLoader.exe", pins["tmloader_exe_sha256"], "ModLoader executable"
        ),
        PinnedFile(
            "TMCOMP_DLL_REL",
            _product("Competition Patch", pins["tmcomp_version"], "TMCompPatch.dll"),
            pins["tmcomp_dll_sha256"],
            f"Competition Patch {pins['tmcomp_version']}",
        ),
        PinnedFile(
            "TMLOADER_GAME_EXE_REL",
            _product(PROGRAM_ID, pins["tmloader_game_version"], f"{PROGRAM_ID}.exe"),
            pins["tmloader_game_sha256"],
            "ModLoader game runtime",
        ),
        PinnedFile(
            "TMLOADER_COREMOD_DLL_REL",
            _product("CoreMod", pins["tmloader_coremod_version"], "CoreMod.dll"),
            pins["tmloader_coremod_sha256"],
            "CoreMod",
        ),
    ]


def config_files() -> typing.Tuple[str, str]:
    """The product settings and the Play profile that configuration writes."""
    return (
        f"database/{PROGRAM_ID}/products/{PROGRAM_ID}/settings.yaml",
        f"database/{PROGRAM_ID}/profiles/{PROFILE}.yaml",
    )


def shell_assignments(pins: typing.Mapping[str, str]) -> typing.List[typing.Tuple[str, str]]:
    """(NAME, value) pairs for the shell: program, profile and relative paths."""
    settings, profile = config_files()
    pairs = [
        ("TMLOADER_PROGRAM_ID", PROGRAM_ID),
        ("TMLOADER_PROFILE", PROFILE),
        ("TMLOADER_PREFIX_DIR", PREFIX_DIR),
    ]
    pairs += [(item.shell_name, item.relpath) for item in pinned_files(pins)]
    pairs += [("TMLOADER_SETTINGS_REL", settings), ("TMLOADER_PROFILE_REL", profile)]
    return pairs
