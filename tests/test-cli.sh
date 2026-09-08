#!/bin/bash

set -euo pipefail

# Re-enter with no user configuration before creating or invoking any fixtures.
if test "${1:-}" != --isolated; then
  exec env -i PATH="$PATH" /bin/bash "$0" --isolated
fi

PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
TEST_ROOT="$(mktemp -d /private/tmp/mactmnf-test.XXXXXX)"
TEST_ROOT="$(CDPATH='' cd -- "$TEST_ROOT" && pwd -P)"
trap 'rm -rf "$TEST_ROOT"' EXIT

FAKE_HOME="${TEST_ROOT}/home"
FAKE_ROOT="${FAKE_HOME}/Games/TMNF"
FAKE_PREFIX="${FAKE_ROOT}/prefix"
FAKE_GAME="${FAKE_PREFIX}/drive_c/Program Files (x86)/TmNationsForever"
FAKE_RUNTIME="${FAKE_HOME}/runtime"
FAKE_BIN="${FAKE_RUNTIME}/bin"
CALL_LOG="${TEST_ROOT}/wine-call.log"
FAKE_LOADER="${FAKE_PREFIX}/drive_c/Program Files/TMLoader"

FAKE_D9VK="${FAKE_RUNTIME}/lib/d9vk/d3d9.dll"
FAKE_HELPER="${FAKE_RUNTIME}/libexec/mactmnf-session.exe"

mkdir -p "$FAKE_HOME" "$FAKE_GAME" "$FAKE_BIN" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3"
mkdir -p "${FAKE_PREFIX}/drive_c/windows/syswow64" "$(dirname "$FAKE_D9VK")"
printf 'fake d9vk' >"$FAKE_D9VK"
touch "${FAKE_GAME}/TmForever.exe" "${FAKE_GAME}/TmForeverLauncher.exe"
touch "${FAKE_LOADER}/TMLoader.exe" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3/TMCompPatch.dll"
touch "${FAKE_PREFIX}/.mactmnf-prefix" "${FAKE_PREFIX}/system.reg"

cat >"${FAKE_BIN}/wine" <<'EOF'
#!/bin/bash
set -euo pipefail
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
  if test "${1##*/}" = mactmnf-session.exe; then
    shift
    result=''
    while test "$#" -gt 0; do
      case "$1" in
        --result) result="$2"; shift 2 ;;
        --) break ;;
        *) shift ;;
      esac
    done
    test -n "$result"
    umask 077
    printf '%s\n' '{"state":"exited","exit_code":0,"cleanup":true}' >"${result}.tmp"
    mv "${result}.tmp" "$result"
  fi
fi
EOF
chmod +x "${FAKE_BIN}/wine"

cat >"${FAKE_BIN}/wineserver" <<'EOF'
#!/bin/bash
printf 'server=<%s> prefix=%s\n' "$1" "$WINEPREFIX" >>"$MACTMNF_TEST_LOG"
exit 0
EOF
chmod +x "${FAKE_BIN}/wineserver"
python3 - "$PROJECT_DIR" "$FAKE_RUNTIME" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from lib.runtime_manifest import EXECUTABLES, REQUIRED_FILES, digest, verify_runtime

project, runtime = map(Path, sys.argv[1:])
for name in REQUIRED_FILES + EXECUTABLES + ('licenses/COPYING',):
    path = runtime / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('#!/bin/bash\nexit 0\n' if name in EXECUTABLES else 'fake ' + name)
    if name in EXECUTABLES:
        path.chmod(0o755)
version = (project / 'VERSION').read_text().strip()
(runtime / '.mactmnf-runtime').write_text(version + '\n')
(runtime / 'SHA256SUMS').write_text(''.join(
    '{}  {}\n'.format(digest(path), path.relative_to(runtime))
    for path in sorted(runtime.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))
verify_runtime(runtime, version)
PY

export MACTMNF_ROOT="$FAKE_ROOT"
export HOME="$FAKE_HOME"
export TMPDIR="$TEST_ROOT"
export MACTMNF_CACHE="${FAKE_HOME}/cache"
export MACTMNF_USERDATA="${FAKE_ROOT}/UserData"
export MACTMNF_RUNTIME="$FAKE_RUNTIME"
export MACTMNF_SUPPORT="${FAKE_HOME}/Application Support/MacTMNF"
export MACTMNF_PREFIX="$FAKE_PREFIX"
export MACTMNF_APP="${FAKE_HOME}/Applications/TrackMania Nations Forever.app"
unset MACTMNF_AUDIO
export MACTMNF_TEST_LOG="$CALL_LOG"

: >"$CALL_LOG"
help_output="$("${PROJECT_DIR}/bin/mactmnf" help)"
printf '%s\n' "$help_output" | grep -q 'directx.*June 2010 DirectX runtime'

doctor_status=0
: >"$CALL_LOG"
doctor_output="$("${PROJECT_DIR}/bin/mactmnf" doctor 2>&1)" || doctor_status=$?
test "$doctor_status" -ne 0
printf '%s\n' "$doctor_output" | grep -q 'Wine: wine-test'
printf '%s\n' "$doctor_output" | grep -q 'TMNF: installed executable'
printf '%s\n' "$doctor_output" | grep -q 'Competition Patch is missing'

# The default renderer is D9VK; playing without it installed must fail clearly.
: >"$CALL_LOG"
if "${PROJECT_DIR}/bin/mactmnf" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly succeeded without D9VK installed" >&2
  exit 1
fi

test ! -s "$CALL_LOG"

: >"$CALL_LOG"
"${PROJECT_DIR}/bin/mactmnf" d9vk >/dev/null
cmp -s "$FAKE_D9VK" "${FAKE_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
grep -Fq '<reg><add><HKCU\Software\Wine\DllOverrides></v><d3d9>' "$CALL_LOG"

: >"$CALL_LOG"
"${PROJECT_DIR}/bin/mactmnf" play-vanilla /test-argument 'argument with spaces'
grep -Fq '<reg><add><HKCU\Software\Wine\Drivers></v><Audio></t><REG_SZ></d><coreaudio></f>' "$CALL_LOG"
grep -Fq "cwd=${FAKE_GAME}" "$CALL_LOG"
grep -Fq "overrides=d3d9=n" "$CALL_LOG"
grep -Fq 'overrides=d3d9=n;d3dx9_30,d3dx9_43,wined3d=b' "$CALL_LOG"
grep -Fq "sidecar=${FAKE_BIN}/x87sidecar" "$CALL_LOG"
grep -Fq 'rounding=0,0' "$CALL_LOG"
grep -Fq "dxvk_conf=${PROJECT_DIR}/config/dxvk.conf" "$CALL_LOG"
# ddraw's wined3d adapter stays off OpenGL while D9VK renders.
grep -Fq 'd3d_config=renderer=gdi' "$CALL_LOG"
# Fullscreen uses the native helper without a virtual desktop.
grep -Fq "args=<${FAKE_HELPER}><--result>" "$CALL_LOG"
grep -Fq "<--watch><${FAKE_GAME}/TmForever.exe><--><${FAKE_GAME}/TmForever.exe></test-argument><argument with spaces>" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"
if grep -Eq '<explorer>|<--desktop>' "$CALL_LOG"; then
  echo 'fullscreen must not wrap the game in an explorer desktop' >&2
  exit 1
fi

# The native helper creates the virtual desktop, not a host explorer command.
: >"$CALL_LOG"
MACTMNF_WINDOW_SIZE=1280x720 "${PROJECT_DIR}/bin/mactmnf" play-vanilla /test-argument
grep -Fq "<--watch><${FAKE_GAME}/TmForever.exe><--desktop><1280x720><--><${FAKE_GAME}/TmForever.exe></test-argument>" "$CALL_LOG"
if grep -Fq '<explorer>' "$CALL_LOG"; then
  echo 'The host must not launch an untracked explorer wrapper.' >&2
  exit 1
fi
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"

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

: >"$CALL_LOG"
if MACTMNF_TEST_REG_STATUS=1 "${PROJECT_DIR}/bin/mactmnf" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly launched after audio configuration failed" >&2
  exit 1
fi
test ! -s "$CALL_LOG"

: >"$CALL_LOG"
MACTMNF_RENDERER=wined3d-vulkan "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -Fq "overrides=d3d9=b" "$CALL_LOG"
grep -Fq "d3d_config=renderer=vulkan" "$CALL_LOG"
: >"$CALL_LOG"
WINE_D3D_CONFIG=csmt=0 "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -Fq "d3d_config=renderer=gdi,csmt=0" "$CALL_LOG"

: >"$CALL_LOG"
doctor_output="$("${PROJECT_DIR}/bin/mactmnf" doctor 2>&1)" || true
printf '%s\n' "$doctor_output" | grep -q 'Renderer: d9vk'
printf '%s\n' "$doctor_output" | grep -q 'D9VK: installed in the prefix'

: >"$CALL_LOG"
"${PROJECT_DIR}/bin/mactmnf" configure
grep -Fq '<reg><add><HKCU\Software\Wine\AppDefaults\TmForeverLauncher.exe\Direct3D>' "$CALL_LOG"
grep -Fq "<--watch><${FAKE_GAME}/TmForeverLauncher.exe><--watch><${FAKE_GAME}/TmForever.exe><--><${FAKE_GAME}/TmForeverLauncher.exe>" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"
# The launcher and its benchmark run with the game's renderer and translator.
grep -Fq 'overrides=d3d9=n;d3dx9_30,d3dx9_43,wined3d=b' "$CALL_LOG"
grep -Fq "sidecar=${FAKE_BIN}/x87sidecar" "$CALL_LOG"
grep -Fq "dxvk_conf=${PROJECT_DIR}/config/dxvk.conf" "$CALL_LOG"
# D9VK reports a listed GPU to the launcher only, so it does not benchmark.
grep -qx '\[TmForeverLauncher.exe\]' "${PROJECT_DIR}/config/dxvk.conf"
grep -Eq '^d3d9.customVendorId *= *10de$' "${PROJECT_DIR}/config/dxvk.conf"
grep -Eq '^d3d9.customDeviceId *= *0611$' "${PROJECT_DIR}/config/dxvk.conf"

: >"$CALL_LOG"
if "${PROJECT_DIR}/bin/mactmnf" nonsense >/dev/null 2>&1; then
  echo "unknown command unexpectedly succeeded" >&2
  exit 1
fi

: >"$CALL_LOG"
"${PROJECT_DIR}/bin/mactmnf" app >/dev/null 2>&1
test -x "${MACTMNF_APP}/Contents/MacOS/MacTMNF"
grep -Fq '<key>CFBundleIconFile</key><string>AppIcon</string>' "${MACTMNF_APP}/Contents/Info.plist"
test ! -e "${MACTMNF_APP}/Contents/Resources/AppIcon.icns"  # the fake executable has no icon
test -x "${FAKE_HOME}/Applications/TrackMania Settings.app/Contents/MacOS/MacTMNF"
if grep -Fq "$PROJECT_DIR" "${MACTMNF_APP}/Contents/MacOS/MacTMNF"; then
  echo 'Installed app still depends on the checkout' >&2; exit 1
fi
cp -R "${MACTMNF_SUPPORT}/launcher" "${TEST_ROOT}/relocated launcher"
: >"$CALL_LOG"
env -i PATH="$PATH" HOME="$FAKE_HOME" MACTMNF_TEST_LOG="$CALL_LOG" \
  "${TEST_ROOT}/relocated launcher/bin/mactmnf" configure
grep -Fq "args=<${FAKE_HELPER}><--result>" "$CALL_LOG"
grep -Fq "<--watch><${FAKE_GAME}/TmForeverLauncher.exe><--watch><${FAKE_GAME}/TmForever.exe><--><${FAKE_GAME}/TmForeverLauncher.exe>" "$CALL_LOG"
grep -Fq "sidecar=${FAKE_BIN}/x87sidecar" "$CALL_LOG"
grep -Fq "dxvk_conf=${TEST_ROOT}/relocated launcher/config/dxvk.conf" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"

: >"$CALL_LOG"
MACTMNF_X87=off "${PROJECT_DIR}/bin/mactmnf" play-vanilla
grep -qx 'sidecar=' "$CALL_LOG"
if grep -Eq '^sidecar=.' "$CALL_LOG"; then
  echo 'Translator-off launch inherited a sidecar.' >&2
  exit 1
fi

: >"$CALL_LOG"
"${PROJECT_DIR}/bin/mactmnf" uninstall --yes >/dev/null
test ! -e "$FAKE_PREFIX"

echo "CLI tests passed"
