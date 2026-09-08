#!/bin/bash
# Check the precision mode used by TMNF before testing a CPU translator.
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
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/mactmnf-x87.XXXXXX")"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  "$compiler" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/x87-precision.c" -o "${probe_dir}/x87-${architecture}.exe"
  say "Testing $architecture x87 precision and rounding"
  python3 "${SCRIPT_DIR}/run-probe.py" 60 env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
    "$wine_bin" "${probe_dir}/x87-${architecture}.exe"
done
