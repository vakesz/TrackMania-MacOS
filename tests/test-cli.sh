#!/bin/bash

set -euo pipefail

PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
TEST_ROOT="$(mktemp -d /private/tmp/mactmnf-test.XXXXXX)"
TEST_ROOT="$(CDPATH='' cd -- "$TEST_ROOT" && pwd -P)"
trap 'rm -rf "$TEST_ROOT"' EXIT

FAKE_HOME="${TEST_ROOT}/home"
FAKE_ROOT="${FAKE_HOME}/Games/TMNF"
FAKE_PREFIX="${FAKE_ROOT}/prefix"
FAKE_GAME="${FAKE_PREFIX}/drive_c/Program Files (x86)/TmNationsForever"
FAKE_BIN="${TEST_ROOT}/bin"
CALL_LOG="${TEST_ROOT}/wine-call.log"
FAKE_LOADER="${FAKE_PREFIX}/drive_c/Program Files/TMLoader"

FAKE_D9VK="${TEST_ROOT}/d9vk/d3d9.dll"

mkdir -p "$FAKE_HOME" "$FAKE_GAME" "$FAKE_BIN" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3"
mkdir -p "${FAKE_PREFIX}/drive_c/windows/syswow64" "$(dirname "$FAKE_D9VK")"
printf 'fake d9vk' >"$FAKE_D9VK"
touch "${FAKE_GAME}/TmForever.exe" "${FAKE_GAME}/TmForeverLauncher.exe"
touch "${FAKE_LOADER}/TMLoader.exe" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3/TMCompPatch.dll"
touch "${FAKE_PREFIX}/.mactmnf-prefix"

cat >"${FAKE_BIN}/wine" <<'EOF'
#!/bin/bash
if test "${1:-}" = "--version"; then
  echo "wine-test"
elif test "${1:-}" = 'C:\windows\syswow64\cmd.exe'; then
  printf '\r\nMicrosoft Windows 10.0.19045\r\n'
else
  if test "${1:-}" = reg && test "${MACTMNF_TEST_REG_STATUS:-0}" -ne 0; then
    exit "$MACTMNF_TEST_REG_STATUS"
  fi
  printf 'cwd=%s\n' "$PWD" >>"$MACTMNF_TEST_LOG"
  printf 'overrides=%s\n' "${WINEDLLOVERRIDES:-}" >>"$MACTMNF_TEST_LOG"
  printf 'sidecar=%s\n' "${ROSETTA_X87_PATH:-}" >>"$MACTMNF_TEST_LOG"
  printf 'rounding=%s,%s\n' "${X87_ENABLE_FMA_CONTRACT:-}" "${X87_FAST_ROUND:-}" >>"$MACTMNF_TEST_LOG"
  printf 'dxvk_conf=%s\n' "${DXVK_CONFIG_FILE:-}" >>"$MACTMNF_TEST_LOG"
  printf 'd3d_config=%s\n' "${WINE_D3D_CONFIG:-}" >>"$MACTMNF_TEST_LOG"
  printf 'args=' >>"$MACTMNF_TEST_LOG"
  printf '<%s>' "$@" >>"$MACTMNF_TEST_LOG"
  printf '\n' >>"$MACTMNF_TEST_LOG"
fi
EOF
chmod +x "${FAKE_BIN}/wine"

export MACTMNF_ROOT="$FAKE_ROOT"
export MACTMNF_SUPPORT="${FAKE_HOME}/Application Support/MacTMNF"
export MACTMNF_PREFIX="$FAKE_PREFIX"
export MACTMNF_APP="${FAKE_HOME}/Applications/TrackMania Nations Forever.app"
unset MACTMNF_AUDIO
export MACTMNF_WINE="${FAKE_BIN}/wine"
export MACTMNF_TEST_LOG="$CALL_LOG"
export MACTMNF_D9VK_DLL="$FAKE_D9VK"
export MACTMNF_SIDECAR="${FAKE_BIN}/wine"

help_output="$("${PROJECT_DIR}/bin/mactmnf" help)"
printf '%s\n' "$help_output" | grep -q 'directx.*June 2010 DirectX runtime'

doctor_status=0
doctor_output="$("${PROJECT_DIR}/bin/mactmnf" doctor 2>&1)" || doctor_status=$?
test "$doctor_status" -ne 0
printf '%s\n' "$doctor_output" | grep -q 'Wine: wine-test'
printf '%s\n' "$doctor_output" | grep -q 'TMNF: installed executable'
printf '%s\n' "$doctor_output" | grep -q 'Competition Patch is missing'

# The default renderer is D9VK; playing without it installed must fail clearly.
if "${PROJECT_DIR}/bin/mactmnf" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly succeeded without D9VK installed" >&2
  exit 1
fi

"${PROJECT_DIR}/bin/mactmnf" d9vk >/dev/null
cmp -s "$FAKE_D9VK" "${FAKE_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
grep -Fq '<reg><add><HKCU\Software\Wine\DllOverrides><\/v><d3d9>' "$CALL_LOG" \
  || grep -Fq '<reg><add><HKCU\Software\Wine\DllOverrides></v><d3d9>' "$CALL_LOG"

"${PROJECT_DIR}/bin/mactmnf" play-vanilla /test-argument
grep -Fq '<reg><add><HKCU\Software\Wine\Drivers></v><Audio></t><REG_SZ></d><coreaudio></f>' "$CALL_LOG"
grep -Fq "cwd=${FAKE_GAME}" "$CALL_LOG"
grep -Fq "overrides=d3d9=n" "$CALL_LOG"
grep -Fq 'overrides=d3d9=n;d3dx9_30,d3dx9_43,wined3d=b' "$CALL_LOG"
grep -Fq "sidecar=${MACTMNF_SIDECAR}" "$CALL_LOG"
grep -Fq 'rounding=0,0' "$CALL_LOG"
grep -Fq "dxvk_conf=${PROJECT_DIR}/config/dxvk.conf" "$CALL_LOG"
grep -Fq "<explorer></desktop=TMNF,1280x720><${FAKE_GAME}/TmForever.exe></test-argument>" "$CALL_LOG"

: >"$CALL_LOG"
MACTMNF_AUDIO=off "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -Fq '<reg><add><HKCU\Software\Wine\Drivers></v><Audio></t><REG_SZ></d><></f>' "$CALL_LOG"
grep -Fq 'overrides=d3d9=n' "$CALL_LOG"

: >"$CALL_LOG"
if MACTMNF_AUDIO=invalid "${PROJECT_DIR}/bin/mactmnf" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly accepted an invalid audio mode" >&2
  exit 1
fi
test ! -s "$CALL_LOG"

if MACTMNF_TEST_REG_STATUS=1 "${PROJECT_DIR}/bin/mactmnf" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly launched after audio configuration failed" >&2
  exit 1
fi
test ! -s "$CALL_LOG"

MACTMNF_RENDERER=wined3d-vulkan "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -Fq "overrides=d3d9=b" "$CALL_LOG"
grep -Fq "d3d_config=renderer=vulkan" "$CALL_LOG"

doctor_output="$("${PROJECT_DIR}/bin/mactmnf" doctor 2>&1)" || true
printf '%s\n' "$doctor_output" | grep -q 'Renderer: d9vk'
printf '%s\n' "$doctor_output" | grep -q 'D9VK: installed in the prefix'

"${PROJECT_DIR}/bin/mactmnf" configure
grep -Fq '<reg><add><HKCU\Software\Wine\AppDefaults\TmForeverLauncher.exe\Direct3D>' "$CALL_LOG"
grep -Fq "<${FAKE_GAME}/TmForeverLauncher.exe>" "$CALL_LOG"

if "${PROJECT_DIR}/bin/mactmnf" nonsense >/dev/null 2>&1; then
  echo "unknown command unexpectedly succeeded" >&2
  exit 1
fi

"${PROJECT_DIR}/bin/mactmnf" app >/dev/null
test -x "${MACTMNF_APP}/Contents/MacOS/MacTMNF"
test -x "${FAKE_HOME}/Applications/TrackMania Settings.app/Contents/MacOS/MacTMNF"
if grep -Fq "$PROJECT_DIR" "${MACTMNF_APP}/Contents/MacOS/MacTMNF"; then
  echo 'Installed app still depends on the checkout' >&2; exit 1
fi
cp -R "${MACTMNF_SUPPORT}/launcher" "${TEST_ROOT}/relocated launcher"
: >"$CALL_LOG"
env -u MACTMNF_ROOT -u MACTMNF_PREFIX -u MACTMNF_SUPPORT \
  "${TEST_ROOT}/relocated launcher/bin/mactmnf" configure
grep -Fq "<${FAKE_GAME}/TmForeverLauncher.exe>" "$CALL_LOG"

: >"$CALL_LOG"
MACTMNF_X87=off "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -qx 'sidecar=' "$CALL_LOG"

"${PROJECT_DIR}/bin/mactmnf" uninstall --yes >/dev/null
test ! -e "$FAKE_PREFIX"

echo "CLI tests passed"
