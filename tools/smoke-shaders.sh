#!/bin/bash
# Check legacy shader profile overloads through the real D3DX entry points.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"

wine_bin="$(wine_binary)" || die "Wine is not installed."
test -f "${MACTMNF_PREFIX}/system.reg" || die "Wine prefix is missing. Run ./install.sh first."
configure_translation
probe_dir="$(mktemp -d /private/tmp/mactmnf-shaders.XXXXXX)"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  have "$compiler" || die "Install mingw-w64 to build the shader probe."
  "$compiler" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/shader-profiles.c" -o "${probe_dir}/shader-profiles-${architecture}.exe"
  for sdk in 30 36 37 43; do
    mode=legacy
    test "$sdk" -le 36 || mode=modern
    say "Testing $architecture D3DX9 SDK $sdk ($mode compiler)"
    env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
      WINEDLLOVERRIDES="d3dx9_${sdk},d3dcompiler_43,wined3d=b" \
      "$wine_bin" "${probe_dir}/shader-profiles-${architecture}.exe" "d3dx9_${sdk}.dll" "$mode"
    if test "$sdk" -gt 36; then
      say "Testing $architecture D3DX9 SDK $sdk legacy compiler flag"
      env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all \
        WINEDLLOVERRIDES="d3dx9_${sdk},d3dcompiler_43,wined3d=b" \
        "$wine_bin" "${probe_dir}/shader-profiles-${architecture}.exe" "d3dx9_${sdk}.dll" legacy-flag
    fi
  done
done
