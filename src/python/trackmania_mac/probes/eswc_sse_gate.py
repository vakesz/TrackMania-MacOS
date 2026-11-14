#!/usr/bin/env python3
"""Particle patch gate: compare the Nations ESWC particle patch with the game's x87 loop.

Builds tests/eswc-sse-particles.c, which maps the installed, patched
TmNationsESWC.exe without running it, and runs it once with the original x87 loop
and once with the patch, under stock Rosetta (exact x87, the reference) and under
each translator. Each run makes the same calls: game-like and edge-case particles,
under the game's 24-bit precision and other control words. The gate passes when, in
every call:

- the patch under stock Rosetta gives the same vertices and loop state as the x87
  loop under stock Rosetta;
- under each translator, the vertices of the particles the patch computes itself
  are those of stock Rosetta (particles it hands to the x87 loop follow the
  translator, like the unpatched game);
- the patch leaves MXCSR as it found it.

--time reports nanoseconds per particle instead. Runs in the selected prefix, as make
smoke does, and needs the installed game; close the game first. From the
repository root, with PYTHONPATH=src/python:

  python3 -m trackmania_mac.probes.eswc_sse_gate                    # installed translator
  python3 -m trackmania_mac.probes.eswc_sse_gate --sidecar PATH --calls 30000
  python3 -m trackmania_mac.probes.eswc_sse_gate --time
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import subprocess
import sys
import tempfile
import typing
from pathlib import Path

from . import x87_wine
from trackmania_mac.hub import registry

SOURCE = x87_wine.ROOT / "tests/eswc-sse-particles.c"
TITLE_ID = "tmn-eswc"  # the patch and the addresses in the harness are this build's


def build(work: Path) -> Path:
    exe = work / "eswc-sse-particles.exe"
    subprocess.run(
        [
            x87_wine.compiler(),
            "-O1",
            "-std=gnu11",
            "-Wall",
            "-msse2",
            "-mfpmath=sse",
            "-ffp-contract=off",
            "-frounding-math",
            "-Wl,--image-base=0x10000000",
            "-Wl,--disable-dynamicbase",
            "-o",
            str(exe),
            str(SOURCE),
        ],
        check=True,
    )
    return exe


def run(wine, prefix, harness, game_exe, translator, args) -> typing.List[str]:
    """The harness's result lines; SystemExit when it does not finish."""
    env = x87_wine.environment(prefix, translator)
    status, output = x87_wine.run(env, [str(wine), str(harness), game_exe, *args], 3600)
    lines = [line for line in output.splitlines() if line[:2] in ("H ", "T ")]
    if status or "DONE" not in output.splitlines():
        raise SystemExit("The harness did not finish ({}):\n{}".format(status, output[-2000:]))
    return lines


def calls(lines: typing.List[str]) -> typing.Dict[str, typing.Dict[str, str]]:
    """H lines by call number: group, count, fallbacks, sse, x87, state, MXCSR before and after."""
    result = {}
    for line in lines:
        if line.startswith("H "):
            _, call, group, count, handed, _, sse, _, x87, _, state, _, before, after = line.split()
            result[call] = dict(
                group=group,
                count=count,
                handed=handed,
                sse=sse,
                x87=x87,
                state=state,
                before=before,
                after=after,
            )
    return result


def compare(results: typing.Dict[typing.Tuple[str, str], typing.Dict[str, dict]]) -> int:
    """Print the comparison of check runs keyed by (orig|stub, translator); 1 on failure."""
    reference = results[("orig", x87_wine.STOCK)]
    particles = sum(int(call["count"]) for call in reference.values())
    handed = sum(int(call["handed"]) for call in reference.values())
    groups = collections.Counter(call["group"] for call in reference.values())
    print(
        f"{len(reference)} calls, {particles} particles, {handed} handed to the x87 loop; "
        f"groups {dict(sorted(groups.items()))}"
    )
    status = 0
    for (impl, translator), got in sorted(results.items()):
        if set(got) != set(reference):
            print(f"FAIL {impl}/{translator}: the runs made different calls")
            status = 1
            continue
        differ: typing.Counter[str] = collections.Counter()
        for call, line in got.items():
            expected = reference[call]
            if line["sse"] != expected["sse"]:
                differ["particles the patch computes"] += 1
            if line["x87"] != expected["x87"]:
                differ["particles handed to the x87 loop"] += 1
            if line["state"] != expected["state"]:
                differ["loop state"] += 1
            if impl == "stub" and line["handed"] == "0" and line["after"] != line["before"]:
                differ["MXCSR not restored"] += 1
        # The x87 loop follows the translator; the patch must match stock Rosetta.
        if impl == "orig":
            failures: typing.Dict[str, int] = {}
        elif translator == x87_wine.STOCK:
            failures = dict(differ)
        else:
            failures = {
                name: differ[name]
                for name in ("particles the patch computes", "MXCSR not restored")
                if name in differ
            }
        if failures:
            status = 1
        verdict = "FAIL" if failures else "ok  "
        print(
            f"{verdict} {impl}/{translator}: differences from orig/stock {dict(differ) or 'none'}"
        )
    print("PASS" if status == 0 else "FAIL")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sidecar",
        action="append",
        default=[],
        metavar="[NAME=]PATH",
        help="x87sidecar binary (repeatable; default: installed)",
    )
    parser.add_argument("--calls", type=int, default=3000, help="calls per run")
    parser.add_argument("--time", action="store_true", help="report ns per particle")
    args = parser.parse_args()
    wine, prefix, installed, game = x87_wine.settings()
    title = registry.get(TITLE_ID)
    if not (game / title.game_exe).is_file():
        raise SystemExit(f"The game is not installed in {game}. Run make install first.")
    # The harness calls fixed addresses in this exact build.
    if hashlib.sha256((game / title.game_exe).read_bytes()).hexdigest() != title.game_exe_sha256:
        raise SystemExit(f"{game / title.game_exe} is not the pinned, patched build. Run install.")
    game_exe = "C:\\Program Files (x86)\\{}\\{}".format(title.game_dir_name, title.game_exe)
    translators = [(x87_wine.STOCK, x87_wine.STOCK)]
    translators += [x87_wine.parse_sidecar(arg) for arg in args.sidecar] or [
        ("installed", installed)
    ]
    with tempfile.TemporaryDirectory(prefix="trackmania-mac-eswc-sse-") as work:
        harness = build(Path(work))
        mode = ["time", "1024", "1000"] if args.time else ["check", str(args.calls)]
        results = {}
        for name, path in translators:
            for impl in ("orig", "stub"):
                lines = run(wine, prefix, harness, game_exe, path, [impl, *mode])
                if args.time:
                    for line in lines:
                        _, _, order, ns = line.split()
                        print(f"{impl}/{name} {order}: {ns} ns per particle")
                else:
                    results[(impl, name)] = calls(lines)
    return 0 if args.time else compare(results)


if __name__ == "__main__":
    sys.exit(main())
