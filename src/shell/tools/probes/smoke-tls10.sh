#!/bin/bash
# Account creation in TrackMania Forever needs WinINet to speak TLS 1.0 to a
# server with an untrusted, expired certificate. Serve exactly that on loopback.
set -euo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck source=src/shell/lib/common.sh
. "${PROJECT_DIR}/src/shell/lib/common.sh"

have python3 || die "Missing python3. Install Python 3.9 or newer to run smoke probes."
have i686-w64-mingw32-gcc || die "Missing i686-w64-mingw32-gcc. Install mingw-w64 to build smoke probes."
test -f "${TRACKMANIA_MAC_PREFIX}/system.reg" || die "Wine prefix is missing. Run make install first."
wine_bin="$(wine_binary)" || die "Wine is not installed."
configure_translation
probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/trackmania-mac-tls10.XXXXXX")"
server_pid=''
cleanup() {
  if test -n "$server_pid"; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$probe_dir"
}
trap cleanup EXIT

i686-w64-mingw32-gcc -O2 -Wall -Wextra -Werror \
  "${PROJECT_DIR}/tests/inet_legacy.c" -o "${probe_dir}/inet_legacy.exe" -lwininet
trackmania_mac_python -m trackmania_mac.probes.tls_test_server --legacy-tls10 >"${probe_dir}/server.json" 2>"${probe_dir}/server.err" &
server_pid=$!
port=''
for _ in $(seq 1 100); do
  port="$(sed -n 's|.*127\.0\.0\.1:\([0-9][0-9]*\)/.*|\1|p' "${probe_dir}/server.json")"
  test -n "$port" && break
  if ! kill -0 "$server_pid" 2>/dev/null; then
    server_pid=''
    if grep -q '^skip:' "${probe_dir}/server.err"; then
      say "SKIP: $(cat "${probe_dir}/server.err")"
      exit 0
    fi
    cat "${probe_dir}/server.err" >&2
    die "The TLS 1.0 test server exited early."
  fi
  sleep 0.1
done
test -n "$port" || die "The TLS 1.0 test server did not report a port."
output="$(trackmania_mac_python -m trackmania_mac.probes.probe_runner 90 \
  env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG=-all "$wine_bin" "${probe_dir}/inet_legacy.exe" 127.0.0.1 "$port")" ||
  { printf '%s\n' "$output" >&2; die "WinINet could not complete a TLS 1.0 request."; }
printf '%s\n' "$output"
case "$output" in
  *'HTTP 200'*) say "PASS: WinINet completed the game's account request over TLS 1.0." ;;
  *) die "Unexpected WinINet result." ;;
esac
