"""Shared helpers for the x87 benchmark and physics gate: settings, Wine runs.

Paths come from lib/common.sh, so MACTMNF_ROOT, MACTMNF_PREFIX, MACTMNF_RUNTIME
and MACTMNF_WINE select the installation exactly as for make smoke.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STOCK = 'stock'  # stock Rosetta x87: exact, and the reference for every comparison


def settings():
    """(wine, prefix, installed sidecar, game directory) as lib/common.sh resolves them."""
    script = ('. "$1/lib/common.sh"; wine_binary || exit 1; '
              'printf "%s\\n" "$MACTMNF_PREFIX" "$MACTMNF_SIDECAR" "$GAME_DIR"')
    result = subprocess.run(['/bin/bash', '-c', script, 'settings', str(ROOT)],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Wine is not installed. Run make install first.')
    wine, prefix, sidecar, game = result.stdout.splitlines()[:4]
    if not Path(prefix, 'system.reg').is_file():
        raise SystemExit(f'Wine prefix is missing: {prefix}. Run make install first.')
    return Path(wine), Path(prefix), Path(sidecar), Path(game)


def environment(prefix, sidecar):
    """Wine environment with the given translator, or stock Rosetta for STOCK."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('X87_', 'ROSETTA_X87'))}
    env.update(WINEPREFIX=str(prefix), WINEDEBUG='-all')
    if sidecar != STOCK:
        # The translator settings the game runs with (lib/common.sh configure_translation).
        env.update(ROSETTA_X87_PATH=str(sidecar), X87_ENABLE_FMA_CONTRACT='0', X87_FAST_ROUND='0')
    return env


def parse_sidecar(argument):
    """'NAME=PATH' or 'PATH' -> (name, resolved path); the name defaults to the file name."""
    name, _, path = argument.rpartition('=')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f'No such translator: {path}')
    return name or path.name, path


def run(env, command, timeout):
    """Run under tools/run-probe.py. Returns (exit status, output).

    Output goes to a file, never a pipe: Wine's background services inherit and keep
    pipes open. The translator writes its own log lines to the same output; they can
    land in the middle of the program's buffered lines, so they are cut out."""
    with tempfile.TemporaryFile('w+') as out:
        status = subprocess.run(['python3', str(ROOT / 'tools/run-probe.py'), str(timeout), *command],
                                env=env, stdout=out, stderr=subprocess.STDOUT).returncode
        out.seek(0)
        return status, re.sub(r'\[rosettax87\][^\n]*\n', '', out.read())


def compiler():
    gcc = shutil.which('i686-w64-mingw32-gcc')
    if gcc is None:
        raise SystemExit('Missing i686-w64-mingw32-gcc. Install mingw-w64 (brew install mingw-w64).')
    return gcc
