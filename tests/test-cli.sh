#!/bin/bash

set -euo pipefail

# Re-enter with no user configuration before creating or invoking any fixtures.
if test "${1:-}" != --isolated; then
  exec env -i PATH="$PATH" /bin/bash "$0" --isolated
fi

PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
TEST_ROOT="$(mktemp -d /private/tmp/trackmania-mac-test.XXXXXX)"
TEST_ROOT="$(CDPATH='' cd -- "$TEST_ROOT" && pwd -P)"
trap 'rm -rf "$TEST_ROOT"' EXIT

FAKE_HOME="${TEST_ROOT}/home"
FAKE_DATA="${FAKE_HOME}/Library/TrackManiaMacOS"
FAKE_ROOT="${FAKE_DATA}/games/tmnf"
FAKE_PREFIX="${FAKE_ROOT}/prefix"
FAKE_GAME="${FAKE_PREFIX}/drive_c/Program Files (x86)/TmNationsForever"
FAKE_RUNTIME="${FAKE_HOME}/runtime"
FAKE_BIN="${FAKE_RUNTIME}/bin"
CALL_LOG="${TEST_ROOT}/wine-call.log"
FAKE_LOADER="${FAKE_PREFIX}/drive_c/Program Files/TMLoader"

FAKE_D9VK="${FAKE_RUNTIME}/lib/d9vk/d3d9.dll"
FAKE_HELPER="${FAKE_RUNTIME}/libexec/trackmania-mac-session.exe"

mkdir -p "$FAKE_HOME" "$FAKE_GAME" "$FAKE_BIN" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3"
mkdir -p "${FAKE_PREFIX}/drive_c/windows/syswow64" "$(dirname "$FAKE_D9VK")"
printf 'fake d9vk' >"$FAKE_D9VK"
touch "${FAKE_GAME}/TmForever.exe" "${FAKE_GAME}/TmForeverLauncher.exe"
touch "${FAKE_LOADER}/TMLoader.exe" "${FAKE_LOADER}/database/TmForever/products/Competition Patch/1.5.3/TMCompPatch.dll"
touch "${FAKE_PREFIX}/.trackmania-mac-prefix" "${FAKE_PREFIX}/system.reg"

cat >"${FAKE_BIN}/wine" <<'EOF'
#!/bin/bash
set -euo pipefail
if test "${1:-}" = "--version"; then
  echo "wine-test"
elif test "${1:-}" = 'C:\windows\syswow64\cmd.exe'; then
  printf '\r\nMicrosoft Windows 10.0.19045\r\n'
else
  if test "${1:-}" = reg && test "${TRACKMANIA_MAC_TEST_REG_STATUS:-0}" -ne 0; then
    exit "$TRACKMANIA_MAC_TEST_REG_STATUS"
  fi
  printf 'cwd=%s\n' "$PWD" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'overrides=%s\n' "${WINEDLLOVERRIDES:-}" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'sidecar=%s\n' "${ROSETTA_X87_PATH:-}" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'rounding=%s,%s\n' "${X87_ENABLE_FMA_CONTRACT:-}" "${X87_FAST_ROUND:-}" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'dxvk_conf=%s\n' "${DXVK_CONFIG_FILE:-}" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'd3d_config=%s\n' "${WINE_D3D_CONFIG:-}" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf 'args=' >>"$TRACKMANIA_MAC_TEST_LOG"
  printf '<%s>' "$@" >>"$TRACKMANIA_MAC_TEST_LOG"
  printf '\n' >>"$TRACKMANIA_MAC_TEST_LOG"
  if test "${1##*/}" = trackmania-mac-session.exe; then
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
    if test "${TRACKMANIA_MAC_TEST_REDIRECT:-0}" = 1; then
      printf '%s\n' '{"state":"redirected","cleanup":true}' >"${result}.tmp"
    else
      printf '%s\n' '{"state":"exited","exit_code":0,"cleanup":true}' >"${result}.tmp"
    fi
    mv "${result}.tmp" "$result"
  fi
fi
EOF
chmod +x "${FAKE_BIN}/wine"

cat >"${FAKE_BIN}/wineserver" <<'EOF'
#!/bin/bash
printf 'server=<%s> prefix=%s\n' "$1" "$WINEPREFIX" >>"$TRACKMANIA_MAC_TEST_LOG"
exit 0
EOF
chmod +x "${FAKE_BIN}/wineserver"
python3 - "$PROJECT_DIR" "$FAKE_RUNTIME" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / 'src' / 'python'))
from trackmania_mac.install import version as project_version
from trackmania_mac.install.runtime_manifest import EXECUTABLES, REQUIRED_FILES, digest, verify_runtime

project, runtime = map(Path, sys.argv[1:])
for name in REQUIRED_FILES + EXECUTABLES + ('licenses/COPYING',):
    path = runtime / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('#!/bin/bash\nexit 0\n' if name in EXECUTABLES else 'fake ' + name)
    if name in EXECUTABLES:
        path.chmod(0o755)
version = project_version.read(project)
(runtime / '.trackmania-mac-runtime').write_text(version + '\n')
(runtime / 'SHA256SUMS').write_text(''.join(
    '{}  {}\n'.format(digest(path), path.relative_to(runtime))
    for path in sorted(runtime.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))
verify_runtime(runtime, version)
PY

export TRACKMANIA_MAC_ROOT="$FAKE_ROOT"
export HOME="$FAKE_HOME"
export TMPDIR="$TEST_ROOT"
export TRACKMANIA_MAC_CACHE="${FAKE_HOME}/cache"
export TRACKMANIA_MAC_USERDATA="${FAKE_DATA}/saves/tmnf"
export TRACKMANIA_MAC_RUNTIME="$FAKE_RUNTIME"
export TRACKMANIA_MAC_SUPPORT="${FAKE_HOME}/Application Support/TMNF"
export TRACKMANIA_MAC_PREFIX="$FAKE_PREFIX"
export TRACKMANIA_MAC_APP="${FAKE_HOME}/Applications/TrackMania Nations Forever.app"
unset TRACKMANIA_MAC_AUDIO
export TRACKMANIA_MAC_TEST_LOG="$CALL_LOG"

: >"$CALL_LOG"
help_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" help)"
printf '%s\n' "$help_output" | grep -q 'directx.*June 2010 DirectX runtime'

doctor_status=0
: >"$CALL_LOG"
doctor_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor 2>&1)" || doctor_status=$?
test "$doctor_status" -ne 0
printf '%s\n' "$doctor_output" | grep -q 'Wine: wine-test'
printf '%s\n' "$doctor_output" | grep -q 'TMNF: installed executable'
printf '%s\n' "$doctor_output" | grep -q 'Competition Patch is missing'
# A direct doctor on an idle prefix takes the lock itself and runs deep checks.
printf '%s\n' "$doctor_output" | grep -q 'WoW64: Microsoft Windows'
if printf '%s\n' "$doctor_output" | grep -q 'A session owns the prefix'; then
  echo 'doctor reported a busy prefix on an idle one' >&2
  exit 1
fi

# A prefix that is really busy degrades with the session message.
OPERATION_LOCK="${FAKE_ROOT}/.prefix.trackmania-mac-operation.lock"
# The guard starts its command only once it holds the lock, so the command
# creating this file proves the prefix is busy. The lock file itself is no
# proof: the idle doctor above already left it behind.
HOLDER_READY="${TEST_ROOT}/holder-ready"
# shellcheck disable=SC2016  # $1 is expanded by the child shell, not here
PYTHONPATH="${PROJECT_DIR}/src/python" /usr/bin/python3 -m trackmania_mac.guard.operation_guard \
  run "$FAKE_PREFIX" /bin/bash -c 'touch "$1" && exec /bin/sleep 60' _ "$HOLDER_READY" >/dev/null 2>&1 &
holder=$!
for _ in $(seq 1 300); do
  test -e "$HOLDER_READY" && break
  sleep 0.1
done
test -e "$HOLDER_READY" || { echo 'the lock holder never took the prefix lock' >&2; exit 1; }
busy_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor 2>&1)" || true
printf '%s\n' "$busy_output" | grep -q 'A session owns the prefix right now'
if printf '%s\n' "$busy_output" | grep -q 'WoW64:'; then
  echo 'doctor ran prefix probes while the prefix was busy' >&2
  exit 1
fi
printf '%s\n' "$busy_output" | grep -q 'Wine: wine-test'
pkill -P "$holder" 2>/dev/null || true
kill "$holder" 2>/dev/null || true
wait "$holder" 2>/dev/null || true

# Any other lock failure is reported as itself, never as a busy session.
chmod 666 "$OPERATION_LOCK"
unsafe_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor 2>&1)" || true
printf '%s\n' "$unsafe_output" | grep -q 'Cannot check the prefix'
if printf '%s\n' "$unsafe_output" | grep -q 'A session owns the prefix'; then
  echo 'doctor mislabelled an unsafe lock as a busy session' >&2
  exit 1
fi
rm -f "$OPERATION_LOCK"

# Diagnostics writes one zip and ends with the machine-readable line.
diagnostics_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" diagnostics 2>&1)"
diagnostics_last="$(printf '%s\n' "$diagnostics_output" | tail -1)"
case "$diagnostics_last" in
  "Diagnostics report: ${FAKE_HOME}/Library/TrackManiaMacOS/logs/diagnostics-tmnf-"*.zip) ;;
  *) echo "unexpected diagnostics output: $diagnostics_last" >&2; exit 1 ;;
esac
test -f "${diagnostics_last#Diagnostics report: }"

# The default renderer is D9VK; playing without it installed must fail clearly.
: >"$CALL_LOG"
if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly succeeded without D9VK installed" >&2
  exit 1
fi

test ! -s "$CALL_LOG"

: >"$CALL_LOG"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" d9vk >/dev/null
cmp -s "$FAKE_D9VK" "${FAKE_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
grep -Fq '<reg><add><HKCU\Software\Wine\DllOverrides></v><d3d9>' "$CALL_LOG"

: >"$CALL_LOG"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla /test-argument 'argument with spaces'
grep -Fq '<reg><add><HKCU\Software\Wine\Drivers></v><Audio></t><REG_SZ></d><coreaudio></f>' "$CALL_LOG"
grep -Fq "cwd=${FAKE_GAME}" "$CALL_LOG"
grep -Fq "overrides=d3d9=n" "$CALL_LOG"
grep -Fq 'overrides=d3d9=n;d3dx9_30,d3dx9_31,d3dx9_43,wined3d=b' "$CALL_LOG"
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
TRACKMANIA_MAC_WINDOW_SIZE=1280x720 "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla /test-argument
grep -Fq "<--watch><${FAKE_GAME}/TmForever.exe><--desktop><1280x720><--><${FAKE_GAME}/TmForever.exe></test-argument>" "$CALL_LOG"
if grep -Fq '<explorer>' "$CALL_LOG"; then
  echo 'The host must not launch an untracked explorer wrapper.' >&2
  exit 1
fi
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"

: >"$CALL_LOG"
TRACKMANIA_MAC_AUDIO=off "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla
grep -Fq '<reg><add><HKCU\Software\Wine\Drivers></v><Audio></t><REG_SZ></d><></f>' "$CALL_LOG"
grep -Fq 'overrides=d3d9=n' "$CALL_LOG"

: >"$CALL_LOG"
if TRACKMANIA_MAC_AUDIO=invalid "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly accepted an invalid audio mode" >&2
  exit 1
fi
test ! -s "$CALL_LOG"

: >"$CALL_LOG"
if TRACKMANIA_MAC_TEST_REG_STATUS=1 "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla >/dev/null 2>&1; then
  echo "play-vanilla unexpectedly launched after audio configuration failed" >&2
  exit 1
fi
test ! -s "$CALL_LOG"

: >"$CALL_LOG"
TRACKMANIA_MAC_RENDERER=wined3d-vulkan "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla
grep -Fq "overrides=d3d9=b" "$CALL_LOG"
grep -Fq "d3d_config=renderer=vulkan" "$CALL_LOG"
: >"$CALL_LOG"
WINE_D3D_CONFIG=csmt=0 "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla
grep -Fq "d3d_config=renderer=gdi,csmt=0" "$CALL_LOG"

# A title without needs_tmloader launches its own executable on play and never
# looks for the Competition Patch (none of its files exist for Canyon here).
CANYON_GAME="${FAKE_PREFIX}/drive_c/Program Files (x86)/ManiaPlanet"
mkdir -p "$CANYON_GAME"
touch "${CANYON_GAME}/ManiaPlanet.exe"
cp "${FAKE_PREFIX}/.trackmania-mac-prefix" "${TEST_ROOT}/trackmania-mac-prefix.saved"
printf 'tm-canyon:1\n' >"${FAKE_PREFIX}/.trackmania-mac-prefix"
: >"$CALL_LOG"
TRACKMANIA_MAC_TITLE=tm-canyon "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play /test-argument
cp "${TEST_ROOT}/trackmania-mac-prefix.saved" "${FAKE_PREFIX}/.trackmania-mac-prefix"
grep -Fq "cwd=${CANYON_GAME}" "$CALL_LOG"
grep -Fq "<--watch><${CANYON_GAME}/ManiaPlanet.exe><--><${CANYON_GAME}/ManiaPlanet.exe></test-argument>" "$CALL_LOG"
if grep -Fq 'TMLoader' "$CALL_LOG"; then
  echo 'play for a title without needs_tmloader must not start ModLoader' >&2
  exit 1
fi
# Its settings launcher's game start is never redirected to Play.
touch "${CANYON_GAME}/ManiaPlanetLauncher.exe"
printf 'tm-canyon:1\n' >"${FAKE_PREFIX}/.trackmania-mac-prefix"
: >"$CALL_LOG"
TRACKMANIA_MAC_TITLE=tm-canyon "${PROJECT_DIR}/src/shell/cli/trackmania-mac" configure
cp "${TEST_ROOT}/trackmania-mac-prefix.saved" "${FAKE_PREFIX}/.trackmania-mac-prefix"
grep -Fq "<--watch><${CANYON_GAME}/ManiaPlanetLauncher.exe><--watch><${CANYON_GAME}/ManiaPlanet.exe><--><${CANYON_GAME}/ManiaPlanetLauncher.exe>" "$CALL_LOG"
if grep -Fq -e '--redirect' "$CALL_LOG"; then
  echo 'configure for a title without needs_tmloader must not redirect its launcher' >&2
  exit 1
fi

: >"$CALL_LOG"
doctor_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor 2>&1)" || true
printf '%s\n' "$doctor_output" | grep -q 'Renderer: d9vk'
printf '%s\n' "$doctor_output" | grep -q 'D9VK: installed in the prefix'

: >"$CALL_LOG"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" configure
grep -Fq '<reg><add><HKCU\Software\Wine\AppDefaults\TmForeverLauncher.exe\Direct3D>' "$CALL_LOG"
# The launcher's Play is redirected; its benchmark switches are not.
CONFIGURE_SESSION="<--watch><${FAKE_GAME}/TmForeverLauncher.exe><--watch><${FAKE_GAME}/TmForever.exe><--redirect><${FAKE_GAME}/TmForever.exe><--redirect-except></bench><--redirect-except></list_benchs><--><${FAKE_GAME}/TmForeverLauncher.exe>"
grep -Fq "$CONFIGURE_SESSION" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"
# A settings session that ends without a redirect does not start Play.
if grep -Fq 'TMLoader' "$CALL_LOG"; then
  echo 'configure started Play without a redirect' >&2
  exit 1
fi
# The launcher and its benchmark run with the game's renderer and translator.
grep -Fq 'overrides=d3d9=n;d3dx9_30,d3dx9_31,d3dx9_43,wined3d=b' "$CALL_LOG"
grep -Fq "sidecar=${FAKE_BIN}/x87sidecar" "$CALL_LOG"
grep -Fq "dxvk_conf=${PROJECT_DIR}/config/dxvk.conf" "$CALL_LOG"
# D9VK reports a listed GPU to the launcher only, so it does not benchmark.
grep -qx '\[TmForeverLauncher.exe\]' "${PROJECT_DIR}/config/dxvk.conf"
grep -Eq '^d3d9.customVendorId *= *10de$' "${PROJECT_DIR}/config/dxvk.conf"
grep -Eq '^d3d9.customDeviceId *= *0611$' "${PROJECT_DIR}/config/dxvk.conf"

# After a redirect the prefix is shut down and Play runs with its own checks,
# here failing as Play does because this fixture's Competition Patch is fake.
: >"$CALL_LOG"
if redirect_output="$(TRACKMANIA_MAC_TEST_REDIRECT=1 "${PROJECT_DIR}/src/shell/cli/trackmania-mac" configure 2>&1)"; then
  echo 'configure succeeded although Play could not start' >&2
  exit 1
fi
grep -Fq "$CONFIGURE_SESSION" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"
printf '%s\n' "$redirect_output" | grep -Fq 'starting it as Play does'
printf '%s\n' "$redirect_output" | grep -Fq 'Competition Patch is missing, outdated, or unconfigured'

: >"$CALL_LOG"
if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" nonsense >/dev/null 2>&1; then
  echo "unknown command unexpectedly succeeded" >&2
  exit 1
fi

: >"$CALL_LOG"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" app >/dev/null 2>&1
test -x "${TRACKMANIA_MAC_APP}/Contents/MacOS/TrackManiaMac"
grep -Fq '<key>CFBundleIconFile</key><string>AppIcon</string>' "${TRACKMANIA_MAC_APP}/Contents/Info.plist"
test ! -e "${TRACKMANIA_MAC_APP}/Contents/Resources/AppIcon.icns"  # the fake executable has no icon
test -x "${FAKE_HOME}/Applications/TrackMania Settings.app/Contents/MacOS/TrackManiaMac"
if grep -Fq "$PROJECT_DIR" "${TRACKMANIA_MAC_APP}/Contents/MacOS/TrackManiaMac"; then
  echo 'Installed app still depends on the checkout' >&2; exit 1
fi
cp -R "${TRACKMANIA_MAC_SUPPORT}/launchers/tmnf" "${TEST_ROOT}/relocated launcher"
: >"$CALL_LOG"
env -i PATH="$PATH" HOME="$FAKE_HOME" TRACKMANIA_MAC_TEST_LOG="$CALL_LOG" \
  "${TEST_ROOT}/relocated launcher/src/shell/cli/trackmania-mac" configure
grep -Fq "args=<${FAKE_HELPER}><--result>" "$CALL_LOG"
grep -Fq "$CONFIGURE_SESSION" "$CALL_LOG"
grep -Fq "sidecar=${FAKE_BIN}/x87sidecar" "$CALL_LOG"
grep -Fq "dxvk_conf=${TEST_ROOT}/relocated launcher/config/dxvk.conf" "$CALL_LOG"
grep -Fxq "server=<-k> prefix=${FAKE_PREFIX}" "$CALL_LOG"
grep -Fxq "server=<-w> prefix=${FAKE_PREFIX}" "$CALL_LOG"

: >"$CALL_LOG"
TRACKMANIA_MAC_X87=off "${PROJECT_DIR}/src/shell/cli/trackmania-mac" play-vanilla
grep -qx 'sidecar=' "$CALL_LOG"
if grep -Eq '^sidecar=.' "$CALL_LOG"; then
  echo 'Translator-off launch inherited a sidecar.' >&2
  exit 1
fi

: >"$CALL_LOG"
# Game defaults: `defaults` rewrites only the launch options the installed launcher records.
LAUNCHER_CONFIG="${TRACKMANIA_MAC_SUPPORT}/launchers/tmnf/config/installation.sh"
config_setting() {
  # shellcheck disable=SC2016 # The literal line install_app writes.
  printf 'if test -z "${%s:-}"; then export %s=%s; fi\n' "$1" "$1" "$2"
}
lines_before="$(wc -l <"$LAUNCHER_CONFIG")"
defaults_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" defaults --window-size 1280x720 --audio off)"
grep -Fq 'Display for TrackMania Nations Forever: 1280x720' <<<"$defaults_output"
grep -Fxq "$(config_setting TRACKMANIA_MAC_WINDOW_SIZE 1280x720)" "$LAUNCHER_CONFIG"
grep -Fxq "$(config_setting TRACKMANIA_MAC_AUDIO off)" "$LAUNCHER_CONFIG"
grep -Fxq "$(config_setting TRACKMANIA_MAC_X87 on)" "$LAUNCHER_CONFIG"
test "$(wc -l <"$LAUNCHER_CONFIG")" = "$lines_before"
cp "$LAUNCHER_CONFIG" "${TEST_ROOT}/installation.saved"
for arguments in '--audio loud' '--window-size 0x720' '--x87' '--bogus on' '' '--audio on --audio off'; do
  # shellcheck disable=SC2086 # Each case is a list of words.
  if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" defaults $arguments >/dev/null 2>&1; then
    echo "defaults accepted: $arguments" >&2; exit 1
  fi
  cmp -s "$LAUNCHER_CONFIG" "${TEST_ROOT}/installation.saved"
done
# Reinstalling the apps keeps the chosen defaults; one given explicitly wins.
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" app >/dev/null 2>&1
grep -Fxq "$(config_setting TRACKMANIA_MAC_WINDOW_SIZE 1280x720)" "$LAUNCHER_CONFIG"
grep -Fxq "$(config_setting TRACKMANIA_MAC_AUDIO off)" "$LAUNCHER_CONFIG"
TRACKMANIA_MAC_AUDIO=on "${PROJECT_DIR}/src/shell/cli/trackmania-mac" app >/dev/null 2>&1
grep -Fxq "$(config_setting TRACKMANIA_MAC_AUDIO on)" "$LAUNCHER_CONFIG"
grep -Fxq "$(config_setting TRACKMANIA_MAC_WINDOW_SIZE 1280x720)" "$LAUNCHER_CONFIG"
# A config not in the form install_app writes is refused and left alone.
printf 'echo surprise\n' >>"$LAUNCHER_CONFIG"
cp "$LAUNCHER_CONFIG" "${TEST_ROOT}/installation.saved"
if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" defaults --x87 off >/dev/null 2>&1; then
  echo 'defaults rewrote a config in an unexpected form' >&2; exit 1
fi
cmp -s "$LAUNCHER_CONFIG" "${TEST_ROOT}/installation.saved"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" app >/dev/null 2>&1
grep -Fxq "$(config_setting TRACKMANIA_MAC_WINDOW_SIZE fullscreen)" "$LAUNCHER_CONFIG"
# A running game (a held prefix) blocks the change with the busy status.
rm -f "$HOLDER_READY"
# shellcheck disable=SC2016  # $1 is expanded by the child shell, not here
PYTHONPATH="${PROJECT_DIR}/src/python" /usr/bin/python3 -m trackmania_mac.guard.operation_guard \
  run "$FAKE_PREFIX" /bin/bash -c 'touch "$1" && exec /bin/sleep 60' _ "$HOLDER_READY" >/dev/null 2>&1 &
holder=$!
for _ in $(seq 1 300); do
  test -e "$HOLDER_READY" && break
  sleep 0.1
done
test -e "$HOLDER_READY" || { echo 'the lock holder never took the prefix lock' >&2; exit 1; }
defaults_status=0
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" defaults --audio off >/dev/null 2>&1 || defaults_status=$?
test "$defaults_status" = 75
pkill -P "$holder" 2>/dev/null || true
kill "$holder" 2>/dev/null || true
wait "$holder" 2>/dev/null || true
grep -Fxq "$(config_setting TRACKMANIA_MAC_AUDIO on)" "$LAUNCHER_CONFIG"

: >"$CALL_LOG"
# Uninstall removes only this title's files, never United Forever's beside them.
OTHER_ROOT="${FAKE_DATA}/games/tmu"
OTHER_SAVES="${FAKE_DATA}/saves/tmu"
FAKE_LOGS="${FAKE_DATA}/logs"
SETTINGS_APP="${FAKE_HOME}/Applications/TrackMania Settings.app"
mkdir -p "${OTHER_ROOT}/prefix" "${OTHER_SAVES}/TmForever" "${TRACKMANIA_MAC_USERDATA}/TmForever" "$FAKE_LOGS" "$TRACKMANIA_MAC_CACHE"
printf 'tmu:1\n' >"${OTHER_ROOT}/prefix/.trackmania-mac-prefix"
printf 'other save' >"${OTHER_SAVES}/TmForever/save"
printf 'save' >"${TRACKMANIA_MAC_USERDATA}/TmForever/save"
printf 'shared' >"${TRACKMANIA_MAC_CACHE}/directx_Jun2010_redist.exe"
printf 'installer' >"${TRACKMANIA_MAC_CACHE}/tmnationsforever_setup.exe"
printf 'log' >"${FAKE_LOGS}/play.log"
printf 'log' >"${FAKE_LOGS}/tmu-play.log"
test -d "${TRACKMANIA_MAC_SUPPORT}/launchers/tmnf"
test -d "$SETTINGS_APP"
: >"$CALL_LOG"
uninstall_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" uninstall --yes)"
for removed in "$FAKE_PREFIX" "$TRACKMANIA_MAC_APP" "$SETTINGS_APP" "${TRACKMANIA_MAC_SUPPORT}/launchers/tmnf" \
  "${TRACKMANIA_MAC_CACHE}/tmnationsforever_setup.exe" "${FAKE_LOGS}/play.log"; do
  if test -e "$removed"; then echo "uninstall left $removed" >&2; exit 1; fi
done
test -f "${TRACKMANIA_MAC_USERDATA}/TmForever/save"
grep -Fq "Kept ${TRACKMANIA_MAC_USERDATA} (saves are kept)" <<<"$uninstall_output"
# With only saves left, --include-saves removes the title's own saves folder.
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" uninstall --yes --include-saves >/dev/null
test ! -e "$TRACKMANIA_MAC_USERDATA"
test -d "$FAKE_ROOT"
test -f "${OTHER_SAVES}/TmForever/save"
test -f "${OTHER_ROOT}/prefix/.trackmania-mac-prefix"
test -f "${TRACKMANIA_MAC_CACHE}/directx_Jun2010_redist.exe"
test -f "${FAKE_LOGS}/tmu-play.log"
test -x "${FAKE_BIN}/wine"
if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" uninstall --bogus >/dev/null 2>&1; then
  echo 'uninstall accepted an unknown option' >&2; exit 1
fi

# clean: only named downloads, runtimes no launcher uses, and logs no launch holds.
mkdir -p "${TRACKMANIA_MAC_CACHE}/build/.clone.abc123" "${TRACKMANIA_MAC_CACHE}/games/tmu"
printf 'x' >"${TRACKMANIA_MAC_CACHE}/trackmania-mac-0.1.0-runtime.tar.xz"
printf 'x' >"${TRACKMANIA_MAC_CACHE}/TMLoader-0.9.zip"
printf 'x' >"${TRACKMANIA_MAC_CACHE}/game-state-0123.exe"
printf 'x' >"${TRACKMANIA_MAC_CACHE}/notes.txt"
printf 'x' >"${TRACKMANIA_MAC_CACHE}/games/tmu/tmunitedforever_setup.exe"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" clean downloads >/dev/null
for removed in directx_Jun2010_redist.exe trackmania-mac-0.1.0-runtime.tar.xz TMLoader-0.9.zip build games/tmu; do
  if test -e "${TRACKMANIA_MAC_CACHE}/${removed}"; then echo "clean downloads left $removed" >&2; exit 1; fi
done
test -f "${TRACKMANIA_MAC_CACHE}/game-state-0123.exe"
test -f "${TRACKMANIA_MAC_CACHE}/notes.txt"
test "$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" clean downloads)" = 'Nothing to remove.'
CURRENT_RUNTIME="${TRACKMANIA_MAC_SUPPORT}/runtime/$(PYTHONPATH="${PROJECT_DIR}/src/python" /usr/bin/python3 -c \
  'import sys; from trackmania_mac.install import version; print(version.read(sys.argv[1]))' "$PROJECT_DIR")"
mkdir -p "${TRACKMANIA_MAC_SUPPORT}/runtime/0.1.0" "${TRACKMANIA_MAC_SUPPORT}/runtime/0.2.0" "$CURRENT_RUNTIME"
touch "${TRACKMANIA_MAC_SUPPORT}/runtime/0.1.0/.trackmania-mac-runtime" "${CURRENT_RUNTIME}/.trackmania-mac-runtime"
"${PROJECT_DIR}/src/shell/cli/trackmania-mac" clean runtimes >/dev/null
test ! -e "${TRACKMANIA_MAC_SUPPORT}/runtime/0.1.0"
test -d "${TRACKMANIA_MAC_SUPPORT}/runtime/0.2.0"  # no runtime marker: not ours
test -d "$CURRENT_RUNTIME"
test -x "${FAKE_BIN}/wine"
printf 'log' >"${FAKE_LOGS}/play.log"
printf 'log' >"${FAKE_LOGS}/play.log.1"
printf 'zip' >"${FAKE_LOGS}/diagnostics-tmnf-20260101-120000.zip"
LOG_HELD="${TEST_ROOT}/log-held"
PYTHONPATH="${PROJECT_DIR}/src/python" /usr/bin/python3 -c '
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(60)' "${FAKE_LOGS}/tmu-play.log.lock" "$LOG_HELD" &
log_holder=$!
for _ in $(seq 1 300); do
  test -e "$LOG_HELD" && break
  sleep 0.1
done
clean_output="$("${PROJECT_DIR}/src/shell/cli/trackmania-mac" clean logs)"
kill "$log_holder" 2>/dev/null || true
wait "$log_holder" 2>/dev/null || true
grep -Fq "Kept ${FAKE_LOGS}/tmu-play.log (a game is writing it)" <<<"$clean_output"
test -f "${FAKE_LOGS}/tmu-play.log"
test -f "${FAKE_LOGS}/tmu-play.log.lock"
test -f "${FAKE_LOGS}/play.log.lock"
for removed in play.log play.log.1 diagnostics-tmnf-20260101-120000.zip; do
  if test -e "${FAKE_LOGS}/${removed}"; then echo "clean logs left $removed" >&2; exit 1; fi
done
if "${PROJECT_DIR}/src/shell/cli/trackmania-mac" clean everything >/dev/null 2>&1; then
  echo 'clean accepted an unknown kind' >&2; exit 1
fi

echo "CLI tests passed"
