#!/bin/bash
# Exercise alertable waits and WinMM timers through 32-bit and 64-bit Wine.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"

have python3 || die "Missing python3. Install Python 3.9 or newer to run smoke probes."
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  have "$compiler" || die "Missing $compiler. Install mingw-w64 to build smoke probes."
done
wine_bin="$(wine_binary)" || die "Wine is not installed."
test -f "${MACTMNF_PREFIX}/system.reg" || die "Wine prefix is missing. Run ./install.sh first."
configure_translation
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/mactmnf-sync.XXXXXX")"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  "$compiler" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/alertable-wait.c" -o "${probe_dir}/alertable-wait-${architecture}.exe"
  say "Testing $architecture (WINEDARWINFASTWAIT=${WINEDARWINFASTWAIT:-default})"
  python3 "${SCRIPT_DIR}/run-probe.py" 60 env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
    "$wine_bin" "${probe_dir}/alertable-wait-${architecture}.exe"
  "$compiler" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/multimedia-timer.c" -o "${probe_dir}/multimedia-timer-${architecture}.exe" -lwinmm
  say "Testing $architecture WinMM multimedia timers"
  python3 "${SCRIPT_DIR}/run-probe.py" 60 env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
    "$wine_bin" "${probe_dir}/multimedia-timer-${architecture}.exe"
done
