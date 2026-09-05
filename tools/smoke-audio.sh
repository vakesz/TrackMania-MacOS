#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"

have i686-w64-mingw32-gcc || die "Install mingw-w64 to build the audio probe."
test -f "${MACTMNF_PREFIX}/system.reg" || die "Wine prefix is missing. Run ./install.sh first."
wine_bin="$(wine_binary)" || die "Wine is not installed."
configure_translation
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/mactmnf-audio.XXXXXX")"
trap 'rm -rf "$probe_dir"' EXIT

i686-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
  "${PROJECT_DIR}/tests/audio-devices.c" -o "${probe_dir}/audio-devices.exe" -lole32
configure_wine_audio "$wine_bin"
env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
  "$wine_bin" "${probe_dir}/audio-devices.exe" "$MACTMNF_AUDIO"
say "PASS: Wine's playback devices match MACTMNF_AUDIO=$MACTMNF_AUDIO."
if test "$MACTMNF_AUDIO" = on; then
  for architecture in i686 x86_64; do
    compiler="${architecture}-w64-mingw32-gcc"
    have "$compiler" || die "Install mingw-w64 to build the playback probe."
    "$compiler" -O2 -Wall -Wextra -Werror \
      "${PROJECT_DIR}/tests/audio-playback.c" -o "${probe_dir}/playback-${architecture}.exe" \
      -ldsound -ldxguid -lole32
    say "Testing $architecture stereo and positional audio (two quiet tones)"
    env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all MVK_CONFIG_LOG_LEVEL=1 \
      "$wine_bin" "${probe_dir}/playback-${architecture}.exe"
  done
fi
