#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck source=src/shell/lib/common.sh
. "${PROJECT_DIR}/src/shell/lib/common.sh"

have python3 || die "Missing python3. Install Python 3.9 or newer to run smoke probes."
for architecture in i686 x86_64; do
  test "$architecture" = i686 || test "$TMNF_AUDIO" = on || continue
  compiler="${architecture}-w64-mingw32-gcc"
  have "$compiler" || die "Missing $compiler. Install mingw-w64 to build smoke probes."
done
test -f "${TMNF_PREFIX}/system.reg" || die "Wine prefix is missing. Run make install first."
wine_bin="$(wine_binary)" || die "Wine is not installed."
configure_translation
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/mactmnf-audio.XXXXXX")"
trap 'rm -rf "$probe_dir"' EXIT

i686-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
  "${PROJECT_DIR}/tests/audio-devices.c" -o "${probe_dir}/audio-devices.exe" -lole32
# Expand positional arguments in the bounded child shell, not here.
# shellcheck disable=SC2016
tmnf_python -m tmnf.probes.probe_runner 60 \
  env TMNF_PREFIX="$TMNF_PREFIX" TMNF_AUDIO="$TMNF_AUDIO" \
  bash -c '. "$1"; configure_wine_audio "$2"' _ "${PROJECT_DIR}/src/shell/lib/common.sh" "$wine_bin"
tmnf_python -m tmnf.probes.probe_runner 60 env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all \
  "$wine_bin" "${probe_dir}/audio-devices.exe" "$TMNF_AUDIO"
say "PASS: Wine's playback devices match TMNF_AUDIO=$TMNF_AUDIO."
if test "$TMNF_AUDIO" = on; then
  for architecture in i686 x86_64; do
    compiler="${architecture}-w64-mingw32-gcc"
    "$compiler" -O2 -Wall -Wextra -Werror \
      "${PROJECT_DIR}/tests/audio-playback.c" -o "${probe_dir}/playback-${architecture}.exe" \
      -ldsound -ldxguid -lole32
    say "Testing $architecture playback clocks and 3D API state (audibility/spatialization not verified)"
    tmnf_python -m tmnf.probes.probe_runner 60 env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all MVK_CONFIG_LOG_LEVEL=1 \
      "$wine_bin" "${probe_dir}/playback-${architecture}.exe"
  done
fi
