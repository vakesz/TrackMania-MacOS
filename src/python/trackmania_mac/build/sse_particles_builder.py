"""Build the Nations ESWC particle patch, config/exe-patches/tmn-eswc/sse-particles.json.

src/c/eswc-sse-particles/billboards.c is compiled to billboards.s with the pinned
MinGW compiler, and billboards.s is committed, so the patch bytes do not depend on
the compiler at hand. entry.s includes it and is assembled without a linker into one
.text section with no relocations, which becomes the section the patch adds to
TmNationsESWC.exe (trackmania_mac.install.exe_patch applies it).

    python -m trackmania_mac.build.sse_particles_builder           # rebuild .s and the patch
    python -m trackmania_mac.build.sse_particles_builder --check   # check, change nothing

Rebuilding needs the pinned compiler and assembler; the committed patch is the
authority. --check needs i686-w64-mingw32-as and -objdump of any version. It checks
the committed code itself: its sha256, no x87 instruction, no absolute address,
jumps only inside it or to the three places in the game, and an entry at each hook
offset. It also assembles billboards.s and entry.s, and with the pinned assembler
compares the result with the patch byte for byte (other versions encode some
instructions differently). With the pinned compiler installed, it also checks that
billboards.c compiles to the committed billboards.s.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import typing

from trackmania_mac.install import exe_patch

ROOT = pathlib.Path(__file__).resolve().parents[4]
SOURCE = ROOT / "src/c/eswc-sse-particles"
PATCH = ROOT / "config/exe-patches/tmn-eswc/sse-particles.json"
COMPILER = "i686-w64-mingw32-gcc"
COMPILER_VERSION = "16.2.0"  # Homebrew mingw-w64 14.0.0
ASSEMBLER = "i686-w64-mingw32-as"
ASSEMBLER_VERSION = "2.47"  # binutils, same package
OBJDUMP = "i686-w64-mingw32-objdump"
CFLAGS = (
    "-S",
    "-masm=intel",
    "-O2",
    "-std=gnu11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-msse2",
    "-mfpmath=sse",
    "-ffp-contract=off",
    "-frounding-math",
    "-ffreestanding",
    "-fno-pic",
    "-fno-asynchronous-unwind-tables",
    "-fno-exceptions",
    "-fno-stack-protector",
    "-mincoming-stack-boundary=2",
    "-fno-tree-vectorize",
    "-fno-ident",
)
IMAGE_BASE = 0x400000
SECTION_NAME = ".sse"
SECTION_RVA = 0x8E8000  # the end of the 1.8.0 image
# Loop heads of function 0x80ff40: address, the 5 bytes the jmp replaces, and the
# offset of its entry in entry.s.
HOOKS = ((0x810685, "8b54247c8b", 0x00), (0x810D17, "8b8c249800", 0x20))
SUMMARY = (
    "SSE2 particle billboard loop for TmNationsESWC.exe 1.8.0 "
    "(function 0x80ff40), entered from its two loop heads."
)
# Where the code may jump into the game: the function's exit and the two loop bodies.
GAME_TARGETS = frozenset({0x811B0F, 0x81068D, 0x810D1E})
_X87 = re.compile(r"^\s*(f[a-z0-9]+|wait)\b", re.IGNORECASE)


def tool_version(tool: str) -> typing.Optional[str]:
    """The last word of TOOL's first --version line, or None when it does not run."""
    try:
        first = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        return None
    return first.split()[-1]


def compiler_version() -> typing.Optional[str]:
    return tool_version(COMPILER)


def assembler_version() -> typing.Optional[str]:
    """binutils major.minor, such as 2.47 for 2.47.20260726."""
    version = tool_version(ASSEMBLER)
    match = re.match(r"\d+\.\d+", version or "")
    return match.group() if match else None


def check_code(code: bytes) -> None:
    """The committed code's policy, from GNU objdump's listing of it."""
    start = IMAGE_BASE + SECTION_RVA
    with tempfile.TemporaryDirectory(prefix="trackmania-mac-sse-") as directory:
        path = pathlib.Path(directory) / "code.bin"
        path.write_bytes(code)
        listing = subprocess.run(
            [
                OBJDUMP,
                "-D",
                "-b",
                "binary",
                "-m",
                "i386",
                "-M",
                "intel",
                f"--adjust-vma={start:#x}",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    instructions = {}
    for line in listing.splitlines():
        match = re.match(r"^\s*([0-9a-f]+):\t[0-9a-f ]+\t(\S.*)$", line)
        if match:
            instructions[int(match.group(1), 16)] = match.group(2)
    exits = set()
    for address, text in instructions.items():
        mnemonic = text.split()[0]
        if _X87.match(mnemonic):
            raise SystemExit(f"x87 instruction at {address:#x}: {text}")
        if re.search(r"ds:0x|\[0x", text):
            raise SystemExit(f"absolute address at {address:#x}: {text}")
        target = re.match(r"(j[a-z]+|call)\s+0x([0-9a-f]+)", text)
        if target:
            destination = int(target.group(2), 16)
            if destination in GAME_TARGETS and address < start + 0x40:
                exits.add(destination)
            elif not start <= destination < start + len(code):
                raise SystemExit(f"jump out of the code at {address:#x}: {text}")
    if exits != GAME_TARGETS:
        raise SystemExit("the entries do not jump to all of the game's three places")
    for _, _, offset in HOOKS:
        if instructions.get(start + offset, "").split() != ["push", "esp"]:
            raise SystemExit(f"no entry at offset {offset:#x}")


def compile_c(output: pathlib.Path) -> None:
    subprocess.run(
        [COMPILER, *CFLAGS, "-o", str(output), str(SOURCE / "billboards.c")],
        check=True,
        cwd=SOURCE,
    )


def check_assembly(path: pathlib.Path) -> None:
    """No x87 instruction and no section besides .text."""
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        code = line.split("#", 1)[0]
        if _X87.match(code):
            raise SystemExit(f"{path.name}:{number}: x87 instruction: {line.strip()}")
        if re.match(r"^\s*\.(section|data|bss|rdata)\b", code):
            raise SystemExit(f"{path.name}:{number}: only .text is allowed: {line.strip()}")


def assemble(work: pathlib.Path, billboards: pathlib.Path) -> bytes:
    """entry.s with BILLBOARDS as billboards.s: the bytes of its .text section."""
    (work / "entry.s").write_bytes((SOURCE / "entry.s").read_bytes())
    if billboards != work / "billboards.s":
        (work / "billboards.s").write_bytes(billboards.read_bytes())
    for name in ("entry.s", "billboards.s"):
        check_assembly(work / name)
    obj = work / "entry.o"
    subprocess.run(
        [
            ASSEMBLER,
            "--defsym",
            f"SECTION_VA={IMAGE_BASE + SECTION_RVA:#x}",
            "-o",
            str(obj),
            "entry.s",
        ],
        check=True,
        cwd=work,
    )
    return text_section(obj.read_bytes())


def text_section(coff: bytes) -> bytes:
    """The raw .text of a COFF object that has nothing else and no relocations."""
    machine, count = struct.unpack_from("<HH", coff, 0)
    optional = struct.unpack_from("<H", coff, 16)[0]
    if machine != 0x14C:
        raise SystemExit("not an i386 object")
    text = None
    for index in range(count):
        header = 20 + optional + 40 * index
        name = coff[header : header + 8].rstrip(b"\0").decode()
        size, pointer, _, _, relocations = struct.unpack_from("<IIIIH", coff, header + 16)
        if name == ".text":
            if relocations:
                raise SystemExit("the code needs relocations; it must be position independent")
            text = coff[pointer : pointer + size]
        elif size:
            raise SystemExit(f"unexpected section {name} in the object")
    if not text:
        raise SystemExit("the object has no code")
    return text


def patch_document(code: bytes) -> str:
    data = base64.b64encode(code).decode()
    document = {
        "format": "trackmania-mac-exe-patch",
        "version": 1,
        "summary": SUMMARY,
        "source": str(SOURCE.relative_to(ROOT)),
        "section": {
            "name": SECTION_NAME,
            "rva": f"{SECTION_RVA:#x}",
            "data": [data[start : start + 76] for start in range(0, len(data), 76)],
            "sha256": hashlib.sha256(code).hexdigest(),
        },
        "hooks": [
            {"va": f"{va:#x}", "replaces": replaced, "target": f"{target:#x}"}
            for va, replaced, target in HOOKS
        ],
    }
    return json.dumps(document, indent=2) + "\n"


def main(argv: typing.Optional[typing.List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="check only; change nothing")
    args = parser.parse_args(argv)
    compiler, assembler = compiler_version(), assembler_version()
    with tempfile.TemporaryDirectory(prefix="trackmania-mac-sse-") as directory:
        work = pathlib.Path(directory)
        if compiler == COMPILER_VERSION:
            compile_c(work / "billboards.s")
        if not args.check:
            if compiler != COMPILER_VERSION or assembler != ASSEMBLER_VERSION:
                raise SystemExit(
                    f"Rebuilding needs {COMPILER} {COMPILER_VERSION} and binutils {ASSEMBLER_VERSION}."
                )
            (SOURCE / "billboards.s").write_bytes((work / "billboards.s").read_bytes())
            code = assemble(work, work / "billboards.s")
            check_code(code)
            PATCH.write_text(patch_document(code))
            print(f"Wrote {PATCH.relative_to(ROOT)} ({len(code)} bytes of code)")
            return 0
        committed = exe_patch.load(PATCH).data
        if patch_document(committed) != PATCH.read_text():
            raise SystemExit(f"{PATCH.relative_to(ROOT)} is not what the builder writes.")
        check_code(committed)
        if compiler == COMPILER_VERSION and (
            (work / "billboards.s").read_bytes() != (SOURCE / "billboards.s").read_bytes()
        ):
            raise SystemExit("billboards.s does not match billboards.c; rebuild the patch.")
        code = assemble(work, SOURCE / "billboards.s")
        name = PATCH.relative_to(ROOT)
        if assembler != ASSEMBLER_VERSION:
            print(
                f"{name}: the code passed its checks; the byte comparison was skipped because "
                f"{ASSEMBLER} is binutils {assembler}, not the pinned {ASSEMBLER_VERSION}."
            )
            return 0
        if code != committed:
            raise SystemExit(f"{name} does not match its source; rebuild it.")
        checked = "billboards.c, billboards.s" if compiler == COMPILER_VERSION else "billboards.s"
        print(f"{name} matches {checked} and entry.s ({len(code)} bytes of code)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
