#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"
wine_bin="$(wine_binary)" || die 'Run make install first.'
test -f "${MACTMNF_PREFIX}/system.reg" || die 'Wine prefix is missing.'
configure_translation
probe_dir="$(mktemp -d /private/tmp/mactmnf-network.XXXXXX)"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  "${architecture}-w64-mingw32-gcc" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/network.c" -o "${probe_dir}/${architecture}.exe" -lwinhttp
  env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all "$wine_bin" "${probe_dir}/${architecture}.exe"
done
