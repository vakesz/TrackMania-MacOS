#!/bin/bash
# Exercise alertable waits through the real 32-bit and 64-bit Wine runtime.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"

wine_bin="$(wine_binary)" || die "Wine is not installed."
test -f "${MACTMNF_PREFIX}/system.reg" || die "Wine prefix is missing. Run ./install.sh first."
configure_translation
probe_dir="$(mktemp -d /private/tmp/mactmnf-sync.XXXXXX)"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  have "$compiler" || die "Install mingw-w64 to build the wait probe."
  "$compiler" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/alertable-wait.c" -o "${probe_dir}/alertable-wait-${architecture}.exe"
  say "Testing $architecture (WINEDARWINFASTWAIT=${WINEDARWINFASTWAIT:-default})"
  env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
    "$wine_bin" "${probe_dir}/alertable-wait-${architecture}.exe"
done
