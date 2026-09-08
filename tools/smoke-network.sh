#!/bin/bash
set -euo pipefail
arguments=("$@")
if test "${1:-}" = --async; then shift; fi
if test "$#" -ne 0; then
  if test "$#" -ne 4 || test "$1" != --local; then
    printf '%s\n' 'Usage: smoke-network.sh [--async] [--local CONTROL_URL CASE_URL valid|untrusted|expired|hostname]' >&2
    exit 2
  fi
  case "$4" in valid|untrusted|expired|hostname) ;; *) printf '%s\n' 'Unknown TLS expectation.' >&2; exit 2 ;; esac
  for url in "$2" "$3"; do
    if ! [[ "$url" =~ ^https://127\.0\.0\.1:[0-9]+/[^\?\#@]*$ ]]; then
      printf '%s\n' 'Local TLS tests require https://127.0.0.1:PORT/path URLs without query or fragment.' >&2
      exit 2
    fi
  done
fi
PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"
have python3 || die "Missing python3. Install Python 3.9 or newer to run smoke probes."
for architecture in i686 x86_64; do
  compiler="${architecture}-w64-mingw32-gcc"
  have "$compiler" || die "Missing $compiler. Install mingw-w64 to build smoke probes."
done
wine_bin="$(wine_binary)" || die 'Run make install first.'
test -f "${MACTMNF_PREFIX}/system.reg" || die 'Wine prefix is missing.'
configure_translation
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/mactmnf-network.XXXXXX")"
trap 'rm -rf "$probe_dir"' EXIT
for architecture in i686 x86_64; do
  "${architecture}-w64-mingw32-gcc" -O2 -Wall -Wextra -Werror \
    "${PROJECT_DIR}/tests/network.c" -o "${probe_dir}/${architecture}.exe" -lwinhttp
  python3 "${PROJECT_DIR}/tools/run-probe.py" 90 \
    env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all "$wine_bin" "${probe_dir}/${architecture}.exe" ${arguments[@]+"${arguments[@]}"}
done
