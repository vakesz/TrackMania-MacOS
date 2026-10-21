#!/bin/bash
# shellcheck disable=SC2034

if test "${_TMNF_COMMON_SOURCED:-0}" = 1; then return 0; fi
_TMNF_COMMON_SOURCED=1

set -u

TMNF_PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
# Installed launchers carry their paths with them; a checkout has no such file.
if test -f "${TMNF_PROJECT_DIR}/config/installation.sh"; then
  # shellcheck source=/dev/null
  . "${TMNF_PROJECT_DIR}/config/installation.sh"
fi

say() {
  printf '%s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

have() {
  command -v "$1" >/dev/null 2>&1
}

# Run the tmnf Python package. The package root travels with the project
# (checkout or installed launcher), so one helper serves every caller.
# Exec sites inline PYTHONPATH directly to preserve process replacement.
readonly TMNF_PYTHONPATH="${TMNF_PROJECT_DIR}/src/python${PYTHONPATH:+:$PYTHONPATH}"
if test -z "${TMNF_PYTHON:-}" && test -x "${TMNF_PROJECT_DIR}/python/bin/python3"; then
  TMNF_PYTHON="${TMNF_PROJECT_DIR}/python/bin/python3"
fi
: "${TMNF_PYTHON:=python3}"
readonly TMNF_PYTHON

tmnf_python() {
  if [[ "$TMNF_PYTHON" = */* ]]; then
    test -x "$TMNF_PYTHON" || die "Bundled Python is missing or not executable: $TMNF_PYTHON"
  else
    have "$TMNF_PYTHON" || die "Python 3 is required (missing command: $TMNF_PYTHON)"
  fi
  PYTHONPATH="$TMNF_PYTHONPATH" "$TMNF_PYTHON" "$@"
}

: "${TMNF_TITLE:=tmnf}"
# shellcheck source=src/shell/lib/titles.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/titles.sh"

# Per-title paths and names live in config/titles/*.json; this only loads them.
# Prints prefix_subdir, game_dir_name, game_exe, launcher_exe, app_name and
# settings_app_name, one per line. Anything else is rejected.
_tmnf_load_title() {
  tmnf_python - "$1" <<'PY'
import json
import sys
fields = ('prefix_subdir', 'game_dir_name', 'game_exe', 'launcher_exe',
          'app_name', 'settings_app_name')
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(1)
values = []
for key in fields:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        sys.exit(1)
    if any(char in value for char in '\n\r\x00'):
        sys.exit(1)
    values.append(value)
if values[0].startswith('/') or values[0].startswith('~') \
        or any(part in ('', '.', '..') for part in values[0].split('/')):
    sys.exit(1)
for value in values[1:]:
    if '/' in value or value in ('.', '..'):
        sys.exit(1)
sys.stdout.write(''.join(value + '\n' for value in values))
PY
}

tmnf_title_is_known "$TMNF_TITLE" \
  || die "Unknown TMNF_TITLE: $TMNF_TITLE (see ${TMNF_TITLES_DIR}/*.json)"
_TMNF_TITLE_SPEC="$(_tmnf_load_title "${TMNF_TITLES_DIR}/${TMNF_TITLE}.json")" \
  || die "Invalid title registry entry: ${TMNF_TITLES_DIR}/${TMNF_TITLE}.json"
{
  IFS= read -r _title_root_leaf &&
  IFS= read -r _title_game_dir &&
  IFS= read -r _title_game_exe &&
  IFS= read -r _title_launcher_exe &&
  IFS= read -r _title_app &&
  IFS= read -r _title_settings
} <<<"$_TMNF_TITLE_SPEC" \
  || die "Cannot parse title registry entry: ${TMNF_TITLES_DIR}/${TMNF_TITLE}.json"
unset _TMNF_TITLE_SPEC

: "${TMNF_ROOT:=${HOME}/Games/${_title_root_leaf}}"
TMNF_VERSION="$(cat "${TMNF_PROJECT_DIR}/VERSION")"
TMNF_GAME_URL="https://nadeo-download.cdn.ubi.com/trackmaniaforever/tmnationsforever_setup.exe"
TMNF_GAME_SHA256="2f659138ed4409da404970841e18f03d29921beaf6a424824c8312ddb20f6355"
TMNF_GAME_VERSION="2.11.26"
TMNF_GAME_EXE_SHA256="3847cf9f20bfc63914450060ed528c12104f743d96ad23d6e76abd178de8c84f"
TMLOADER_URL="https://tomashu.pages.dev/modloader/modloader/TMLoader-1.0.3-win32.zip"
TMLOADER_BUNDLE_VERSION="1.0.3"
TMLOADER_SHA256="85d758e75f23084f24ef948836a0fa4e5fe4c77bb1e8897df9ee01f8bd245eb9"
TMLOADER_EXE_SHA256="0cf4c92ad1cb2dc7b92f95b2ec63d9058fd930c200632aaa2aab3a5a3a65e2c3"
TMLOADER_GAME_VERSION="2.12.0-compat"
TMLOADER_GAME_SHA256="0a885e194131063be9f78d9e5cf81e0f3017dfc4331d176812109b6aefc633bf"
TMLOADER_COREMOD_VERSION="1.0.9"
TMLOADER_COREMOD_SHA256="f3ffdcaf2b15c040823d60bcc559e7f287f767b483c1b42588212cdf714d08cf"
TMCOMP_VERSION="1.5.3"
TMCOMP_DLL_SHA256="eef7200a2f6d5a8b31ae43534752e807c3c146248bd941fdf54b21b61225a135"
RUNTIME_VERSION="$TMNF_VERSION"
RUNTIME_RELEASE_URL="https://github.com/vakesz/TrackMania-MacOS/releases/download/v${RUNTIME_VERSION}"
RUNTIME_ARCHIVE_NAME="tmnf-${RUNTIME_VERSION}-runtime.tar.xz"
DIRECTX_VERSION="9.29.1974.1 (June 2010)"
DIRECTX_URL="https://download.microsoft.com/download/8/4/a/84a35bf1-dafe-4ae8-82af-ad2ae20b6b14/directx_Jun2010_redist.exe"
DIRECTX_SHA256="053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b"

: "${TMNF_CACHE:=${HOME}/Library/Caches/TMNF}"
: "${TMNF_SUPPORT:=${HOME}/Library/Application Support/TMNF}"
: "${TMNF_RUNTIME:=${TMNF_SUPPORT}/runtime/${RUNTIME_VERSION}}"
if test -w /Applications; then
  : "${TMNF_APPS_DIR:=/Applications}"
else
  : "${TMNF_APPS_DIR:=${HOME}/Applications}"
fi
: "${TMNF_APP:=${TMNF_APPS_DIR}/${_title_app}}"
: "${TMNF_SETTINGS_APP:=$(dirname "$TMNF_APP")/${_title_settings}}"
: "${TMNF_LAUNCHER:=${TMNF_SUPPORT}/launcher}"
# "fullscreen" (the default) launches the game directly so it uses its own
# display mode, set in TrackMania Settings. A WxH value instead runs it inside
# an explorer virtual desktop of that size, whose title bar shrinks the drawable
# and clips the game's fixed-size menu, so use it only when a fixed window is
# wanted.
: "${TMNF_WINDOW_SIZE:=fullscreen}"
: "${TMNF_AUDIO:=on}"
: "${TMNF_X87:=on}"
: "${TMNF_PREFIX:=${TMNF_ROOT}/prefix}"
: "${TMNF_USERDATA:=${TMNF_ROOT}/UserData}"
# Direct3D 9 renderer: d9vk (DXVK on MoltenVK/Metal), wined3d-vulkan, wined3d-gl
: "${TMNF_RENDERER:=d9vk}"
if test -x "${TMNF_RUNTIME}/bin/wine"; then
  runtime_dir="$TMNF_RUNTIME"
else
  runtime_dir="${TMNF_PROJECT_DIR}/.build/release/runtime"
fi
: "${TMNF_D9VK_DLL:=${runtime_dir}/lib/d9vk/d3d9.dll}"
: "${TMNF_SIDECAR:=${runtime_dir}/bin/x87sidecar}"

readonly GAME_DIR="${TMNF_PREFIX}/drive_c/Program Files (x86)/${_title_game_dir}"
readonly GAME_EXE="${GAME_DIR}/${_title_game_exe}"
readonly LAUNCHER_EXE="${GAME_DIR}/${_title_launcher_exe}"
readonly INSTALLER_PATH="${TMNF_CACHE}/tmnationsforever_setup.exe"
readonly DIRECTX_ARCHIVE="${TMNF_CACHE}/directx_Jun2010_redist.exe"
readonly DIRECTX_MARKER="${TMNF_PREFIX}/.tmnf-directx-jun2010"
readonly TMLOADER_ARCHIVE="${TMNF_CACHE}/TMLoader-${TMLOADER_BUNDLE_VERSION}.zip"
readonly TMLOADER_DIR="${TMNF_PREFIX}/drive_c/Program Files/TMLoader"
readonly TMLOADER_EXE="${TMLOADER_DIR}/TMLoader.exe"
readonly TMCOMP_DLL="${TMLOADER_DIR}/database/TmForever/products/Competition Patch/${TMCOMP_VERSION}/TMCompPatch.dll"
readonly TMLOADER_GAME_EXE="${TMLOADER_DIR}/database/TmForever/products/TmForever/${TMLOADER_GAME_VERSION}/TmForever.exe"
readonly PREFIX_D9VK_DLL="${TMNF_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
readonly DXVK_CONF="${TMNF_PROJECT_DIR}/config/dxvk.conf"
readonly TMLOADER_COREMOD_DLL="${TMLOADER_DIR}/database/TmForever/products/CoreMod/${TMLOADER_COREMOD_VERSION}/CoreMod.dll"

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

verify_file() {
  local path="$1"
  local expected="$2"
  local actual

  test -f "$path" || return 1
  actual="$(sha256_file "$path")"
  test "$actual" = "$expected"
}

competition_patch_is_current() {
  verify_file "$TMLOADER_EXE" "$TMLOADER_EXE_SHA256" \
    && verify_file "$TMCOMP_DLL" "$TMCOMP_DLL_SHA256" \
    && verify_file "$TMLOADER_GAME_EXE" "$TMLOADER_GAME_SHA256" \
    && verify_file "$TMLOADER_COREMOD_DLL" "$TMLOADER_COREMOD_SHA256"
}

directx_is_current() {
  test -f "$DIRECTX_MARKER" \
    && test "$(tr -d '\r\n' <"$DIRECTX_MARKER")" = "$DIRECTX_SHA256"
}

d9vk_is_installed() {
  test -f "$TMNF_D9VK_DLL" || return 1
  test -f "$PREFIX_D9VK_DLL" || return 1
  cmp -s "$TMNF_D9VK_DLL" "$PREFIX_D9VK_DLL"
}

competition_patch_is_ready() {
  competition_patch_is_current \
    && test -s "${TMLOADER_DIR}/database/TmForever/products/TmForever/settings.yaml" \
    && test -s "${TMLOADER_DIR}/database/TmForever/profiles/competition.yaml"
}

validate_launch_options() {
  tmnf_title_is_known "$TMNF_TITLE" \
    || die "Unknown TMNF_TITLE: $TMNF_TITLE (see ${TMNF_TITLES_DIR}/*.json)"
  case "$TMNF_AUDIO" in on|off) ;; *) die "Unknown TMNF_AUDIO: $TMNF_AUDIO (use on or off)" ;; esac
  case "$TMNF_X87" in on|off) ;; *) die "Unknown TMNF_X87: $TMNF_X87 (use on or off)" ;; esac
  case "$TMNF_RENDERER" in
    d9vk|wined3d-vulkan|wined3d-gl) ;;
    *) die "Unknown TMNF_RENDERER: $TMNF_RENDERER (use d9vk, wined3d-vulkan or wined3d-gl)" ;;
  esac
  if test "$TMNF_WINDOW_SIZE" != fullscreen; then
    if ! [[ "$TMNF_WINDOW_SIZE" =~ ^[1-9][0-9]{0,4}x[1-9][0-9]{0,4}$ ]] \
      || test "${TMNF_WINDOW_SIZE%x*}" -gt 65535 \
      || test "${TMNF_WINDOW_SIZE#*x}" -gt 65535; then
      die "Invalid TMNF_WINDOW_SIZE: use fullscreen or WIDTHxHEIGHT, with dimensions 1..65535."
    fi
  fi
}

download_verified() (
  local url="$1"
  local destination="$2"
  local expected="$3"
  local partial cleanup

  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid download SHA-256."
  expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
  mkdir -p "$(dirname "$destination")"
  if verify_file "$destination" "$expected"; then
    say "Using cached $(basename "$destination")"
    return 0
  fi

  say "Downloading $(basename "$destination")"
  partial="$(mktemp "${destination}.partial.XXXXXX")" || die "Cannot stage download."
  printf -v cleanup 'rm -f -- %q' "$partial"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  trap 'exit 1' HUP INT TERM
  curl --fail --location --retry 3 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
    --progress-bar --output "$partial" "$url" || die "Download failed: $url"
  verify_file "$partial" "$expected" || {
    rm -f "$partial"
    die "SHA-256 verification failed for $url"
  }
  # Concurrent callers download the same pinned artifact to independent files.
  # Rename replaces a cache file, never its contents through a symlink.
  test ! -d "$destination" || die "Download destination is a directory: $destination"
  mv -f "$partial" "$destination"
)

wine_binary() {
  if test -n "${TMNF_WINE:-}"; then
    printf '%s\n' "$TMNF_WINE"
  elif test -x "${TMNF_RUNTIME}/bin/wine"; then
    printf '%s\n' "${TMNF_RUNTIME}/bin/wine"
  elif test -x "${TMNF_PROJECT_DIR}/.build/release/runtime/bin/wine"; then
    printf '%s\n' "${TMNF_PROJECT_DIR}/.build/release/runtime/bin/wine"
  else
    return 1
  fi
}

session_helper() {
  local wine_bin="$1" helper
  helper="$(dirname "$(dirname "$wine_bin")")/libexec/tmnf-session.exe"
  test -f "$helper" || die "The runtime has no session supervisor. Build and install the current runtime."
  test -x "$(dirname "$wine_bin")/wineserver" || die "The runtime is missing wineserver."
  printf '%s\n' "$helper"
}

configure_translation() {
  case "$TMNF_X87" in
    on)
      export ROSETTA_X87_PATH="${ROSETTA_X87_PATH:-$TMNF_SIDECAR}"
      test -x "$ROSETTA_X87_PATH" || die "Patched x87sidecar is missing. Run make install."
      export X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0
      ;;
    off) unset ROSETTA_X87_PATH ;;
    *) die "Unknown TMNF_X87: $TMNF_X87 (use on or off)" ;;
  esac
}

wineserver_binary() {
  local wine_bin="$1"
  local sibling
  sibling="$(dirname "$wine_bin")/wineserver"

  if test -x "$sibling"; then
    printf '%s\n' "$sibling"
  elif test -x "$(CDPATH='' cd -- "$(dirname -- "$wine_bin")/.." && pwd)/server/wineserver"; then
    printf '%s\n' "$(CDPATH='' cd -- "$(dirname -- "$wine_bin")/.." && pwd)/server/wineserver"
  else
    return 1
  fi
}

wineboot_binary() {
  local wine_bin="$1"
  local sibling
  sibling="$(dirname "$wine_bin")/wineboot"

  if test -x "$sibling"; then
    printf '%s\n' "$sibling"
  else
    return 1
  fi
}

run_wine() {
  local wine_bin
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run src/shell/cli/install.sh first."
  without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG="${WINEDEBUG:--all}" "$wine_bin" "$@"
}

configure_wine_audio() {
  local wine_bin="$1"
  local driver

  case "$TMNF_AUDIO" in
    off) driver="" ;;
    on) driver="coreaudio" ;;
    *) die "Unknown TMNF_AUDIO: $TMNF_AUDIO (use off or on)" ;;
  esac

  # Current mmdevapi loads the Unix audio library directly, bypassing DLL
  # overrides. An empty driver list is Wine's supported no-audio setting.
  without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
    'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d "$driver" /f >/dev/null
}

require_macos() {
  test "$(uname -s)" = "Darwin" || die "TMNF supports macOS only."
  test "$(uname -m)" = "arm64" || die "This installer currently supports Apple Silicon Macs only."
}

rosetta_is_ready() {
  /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1
}

is_safe_managed_path() {
  tmnf_python -m tmnf.guard.managed_paths path "$1"
}

validate_managed_layout() {
  tmnf_python -m tmnf.guard.managed_paths layout \
    "$TMNF_PREFIX" "$TMNF_RUNTIME" "$TMNF_LAUNCHER" "$TMNF_USERDATA"
}

require_owned_prefix() {
  tmnf_python -m tmnf.guard.managed_paths prefix "$TMNF_PREFIX"
}

guard_prefix_operation() {
  validate_managed_layout || die "Unsafe installation layout."
  if test "${TMNF_OPERATION_FD+x}" != x; then
    mkdir -p "$(dirname "$TMNF_PREFIX")"
    # Process replacement (not a helper call) preserves lock FD inheritance.
    exec env PYTHONPATH="$TMNF_PYTHONPATH" "$TMNF_PYTHON" -m tmnf.guard.operation_guard run "$TMNF_PREFIX" /bin/bash "$@"
  fi
  tmnf_python -m tmnf.guard.operation_guard held "$TMNF_PREFIX" \
    || die "Invalid inherited installation lock."
}

without_operation_lock() (
  if test "${TMNF_OPERATION_FD+x}" = x; then
    case "$TMNF_OPERATION_FD" in ''|*[!0-9]*) die "Invalid operation descriptor." ;; esac
    # Bash 3.2 has no dynamic-FD redirection. The descriptor is digits only and
    # the guarded entry point verified its identity before any Wine invocation.
    eval "exec ${TMNF_OPERATION_FD}>&-"
  fi
  unset TMNF_OPERATION_FD
  # No exec here: "$@" may be a shell function (exec only replaces external
  # commands). The descriptor above is already closed in this subshell.
  "$@"
)
