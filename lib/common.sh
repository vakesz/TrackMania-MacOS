#!/bin/bash
# shellcheck disable=SC2034

if test "${_MACTMNF_COMMON_SOURCED:-0}" = 1; then return 0; fi
_MACTMNF_COMMON_SOURCED=1

set -u

MACTMNF_PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Installed launchers carry their paths with them; a checkout has no such file.
if test -f "${MACTMNF_PROJECT_DIR}/config/installation.sh"; then
  # shellcheck source=/dev/null
  . "${MACTMNF_PROJECT_DIR}/config/installation.sh"
fi

MACTMNF_VERSION="$(cat "${MACTMNF_PROJECT_DIR}/VERSION")"
TMNF_URL="https://nadeo-download.cdn.ubi.com/trackmaniaforever/tmnationsforever_setup.exe"
TMNF_SHA256="2f659138ed4409da404970841e18f03d29921beaf6a424824c8312ddb20f6355"
TMNF_GAME_VERSION="2.11.26"
TMNF_EXE_SHA256="3847cf9f20bfc63914450060ed528c12104f743d96ad23d6e76abd178de8c84f"
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
RUNTIME_VERSION="$MACTMNF_VERSION"
RUNTIME_RELEASE_URL="https://github.com/vakesz/TrackMania-MacOS/releases/download/v${RUNTIME_VERSION}"
RUNTIME_ARCHIVE_NAME="mactmnf-${RUNTIME_VERSION}-runtime.tar.xz"
DIRECTX_VERSION="9.29.1974.1 (June 2010)"
DIRECTX_URL="https://download.microsoft.com/download/8/4/a/84a35bf1-dafe-4ae8-82af-ad2ae20b6b14/directx_Jun2010_redist.exe"
DIRECTX_SHA256="053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b"
# OpenAL Soft's official Windows binaries replace the Creative OpenAL router that
# TMNF ships; its software mixer costs much more CPU under Wine on macOS 27.
OPENAL_SOFT_VERSION="1.25.2"
OPENAL_SOFT_URL="https://openal-soft.org/openal-binaries/openal-soft-${OPENAL_SOFT_VERSION}-bin.zip"
OPENAL_SOFT_SHA256="67a0c4b800bd860c93c04f38caf8cbe4875f9c84700ac430efc451f70e265434"
OPENAL_SOFT_DLL_SHA256="606cf8b4c22c7ae00813585279ac045115907bc66d1483eb26355907021ea4b0"
# OpenAL32.dll as installed by the pinned TMNF release.
CREATIVE_OPENAL_SHA256="26ba695d73fdf78ff133ab9c0e12300c26076441627a25ffba80c8b8aedb10ec"

: "${MACTMNF_ROOT:=${HOME}/Games/TMNF}"
: "${MACTMNF_CACHE:=${HOME}/Library/Caches/MacTMNF}"
: "${MACTMNF_SUPPORT:=${HOME}/Library/Application Support/MacTMNF}"
: "${MACTMNF_RUNTIME:=${MACTMNF_SUPPORT}/runtime/${RUNTIME_VERSION}}"
if test -w /Applications; then
  : "${MACTMNF_APPS_DIR:=/Applications}"
else
  : "${MACTMNF_APPS_DIR:=${HOME}/Applications}"
fi
: "${MACTMNF_APP:=${MACTMNF_APPS_DIR}/TrackMania Nations Forever.app}"
: "${MACTMNF_SETTINGS_APP:=$(dirname "$MACTMNF_APP")/TrackMania Settings.app}"
: "${MACTMNF_LAUNCHER:=${MACTMNF_SUPPORT}/launcher}"
# "fullscreen" (the default) launches the game directly so it uses its own
# display mode, set in TrackMania Settings. A WxH value instead runs it inside
# an explorer virtual desktop of that size, whose title bar shrinks the drawable
# and clips the game's fixed-size menu, so use it only when a fixed window is
# wanted.
: "${MACTMNF_WINDOW_SIZE:=fullscreen}"
: "${MACTMNF_AUDIO:=on}"
: "${MACTMNF_X87:=on}"
: "${MACTMNF_PREFIX:=${MACTMNF_ROOT}/prefix}"
# Game's OpenAL implementation, applied by make install: soft or creative. The
# last applied choice stays until MACTMNF_OPENAL selects another one.
readonly OPENAL_MARKER="${MACTMNF_PREFIX}/.mactmnf-openal"
if test -z "${MACTMNF_OPENAL:-}" && test -f "$OPENAL_MARKER"; then
  MACTMNF_OPENAL="$(tr -d '\r\n' <"$OPENAL_MARKER")"
fi
: "${MACTMNF_OPENAL:=soft}"
: "${MACTMNF_USERDATA:=${MACTMNF_ROOT}/UserData}"
# Direct3D 9 renderer: d9vk (DXVK on MoltenVK/Metal), wined3d-vulkan, wined3d-gl
: "${MACTMNF_RENDERER:=d9vk}"
if test -x "${MACTMNF_RUNTIME}/bin/wine"; then
  runtime_dir="$MACTMNF_RUNTIME"
else
  runtime_dir="${MACTMNF_PROJECT_DIR}/.build/release/runtime"
fi
: "${MACTMNF_D9VK_DLL:=${runtime_dir}/lib/d9vk/d3d9.dll}"
: "${MACTMNF_SIDECAR:=${runtime_dir}/bin/x87sidecar}"

readonly GAME_DIR="${MACTMNF_PREFIX}/drive_c/Program Files (x86)/TmNationsForever"
readonly GAME_EXE="${GAME_DIR}/TmForever.exe"
readonly LAUNCHER_EXE="${GAME_DIR}/TmForeverLauncher.exe"
readonly INSTALLER_PATH="${MACTMNF_CACHE}/tmnationsforever_setup.exe"
readonly DIRECTX_ARCHIVE="${MACTMNF_CACHE}/directx_Jun2010_redist.exe"
readonly DIRECTX_MARKER="${MACTMNF_PREFIX}/.mactmnf-directx-jun2010"
readonly TMLOADER_ARCHIVE="${MACTMNF_CACHE}/TMLoader-${TMLOADER_BUNDLE_VERSION}.zip"
readonly TMLOADER_DIR="${MACTMNF_PREFIX}/drive_c/Program Files/TMLoader"
readonly TMLOADER_EXE="${TMLOADER_DIR}/TMLoader.exe"
readonly TMCOMP_DLL="${TMLOADER_DIR}/database/TmForever/products/Competition Patch/${TMCOMP_VERSION}/TMCompPatch.dll"
readonly TMLOADER_GAME_EXE="${TMLOADER_DIR}/database/TmForever/products/TmForever/${TMLOADER_GAME_VERSION}/TmForever.exe"
readonly PREFIX_D9VK_DLL="${MACTMNF_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
readonly DXVK_CONF="${MACTMNF_PROJECT_DIR}/config/dxvk.conf"
readonly TMLOADER_COREMOD_DLL="${TMLOADER_DIR}/database/TmForever/products/CoreMod/${TMLOADER_COREMOD_VERSION}/CoreMod.dll"
readonly OPENAL_SOFT_ARCHIVE="${MACTMNF_CACHE}/openal-soft-${OPENAL_SOFT_VERSION}-bin.zip"
readonly GAME_OPENAL="${GAME_DIR}/OpenAL32.dll"
readonly GAME_OPENAL_CREATIVE="${GAME_DIR}/OpenAL32.creative.dll"
readonly GAME_OPENAL_COPYING="${GAME_DIR}/OpenAL32.COPYING.txt"

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
  test -f "$MACTMNF_D9VK_DLL" || return 1
  test -f "$PREFIX_D9VK_DLL" || return 1
  cmp -s "$MACTMNF_D9VK_DLL" "$PREFIX_D9VK_DLL"
}

openal_soft_is_installed() {
  verify_file "$GAME_OPENAL" "$OPENAL_SOFT_DLL_SHA256"
}

# soft, creative, other or missing: the OpenAL32.dll the game loads from its directory.
game_openal() {
  if test ! -f "$GAME_OPENAL"; then
    printf 'missing\n'
  elif openal_soft_is_installed; then
    printf 'soft\n'
  elif verify_file "$GAME_OPENAL" "$CREATIVE_OPENAL_SHA256"; then
    printf 'creative\n'
  else
    printf 'other\n'
  fi
}

# make install applied MACTMNF_OPENAL. It leaves a missing or unrecognised DLL alone.
openal_is_current() {
  case "$(game_openal)" in
    "$MACTMNF_OPENAL"|other|missing) return 0 ;;
    *) return 1 ;;
  esac
}

competition_patch_is_ready() {
  competition_patch_is_current \
    && test -s "${TMLOADER_DIR}/database/TmForever/products/TmForever/settings.yaml" \
    && test -s "${TMLOADER_DIR}/database/TmForever/profiles/competition.yaml"
}

validate_launch_options() {
  case "$MACTMNF_AUDIO" in on|off) ;; *) die "Unknown MACTMNF_AUDIO: $MACTMNF_AUDIO (use on or off)" ;; esac
  case "$MACTMNF_X87" in on|off) ;; *) die "Unknown MACTMNF_X87: $MACTMNF_X87 (use on or off)" ;; esac
  case "$MACTMNF_OPENAL" in soft|creative) ;; *) die "Unknown MACTMNF_OPENAL: $MACTMNF_OPENAL (use soft or creative)" ;; esac
  case "$MACTMNF_RENDERER" in
    d9vk|wined3d-vulkan|wined3d-gl) ;;
    *) die "Unknown MACTMNF_RENDERER: $MACTMNF_RENDERER (use d9vk, wined3d-vulkan or wined3d-gl)" ;;
  esac
  if test "$MACTMNF_WINDOW_SIZE" != fullscreen; then
    if ! [[ "$MACTMNF_WINDOW_SIZE" =~ ^[1-9][0-9]{0,4}x[1-9][0-9]{0,4}$ ]] \
      || test "${MACTMNF_WINDOW_SIZE%x*}" -gt 65535 \
      || test "${MACTMNF_WINDOW_SIZE#*x}" -gt 65535; then
      die "Invalid MACTMNF_WINDOW_SIZE: use fullscreen or WIDTHxHEIGHT, with dimensions 1..65535."
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
  if test -n "${MACTMNF_WINE:-}"; then
    printf '%s\n' "$MACTMNF_WINE"
  elif test -x "${MACTMNF_RUNTIME}/bin/wine"; then
    printf '%s\n' "${MACTMNF_RUNTIME}/bin/wine"
  elif test -x "${MACTMNF_PROJECT_DIR}/.build/release/runtime/bin/wine"; then
    printf '%s\n' "${MACTMNF_PROJECT_DIR}/.build/release/runtime/bin/wine"
  else
    return 1
  fi
}

session_helper() {
  local wine_bin="$1" helper
  helper="$(dirname "$(dirname "$wine_bin")")/libexec/mactmnf-session.exe"
  test -f "$helper" || die "The runtime has no session supervisor. Build and install the current runtime."
  test -x "$(dirname "$wine_bin")/wineserver" || die "The runtime is missing wineserver."
  printf '%s\n' "$helper"
}

configure_translation() {
  case "$MACTMNF_X87" in
    on)
      export ROSETTA_X87_PATH="${ROSETTA_X87_PATH:-$MACTMNF_SIDECAR}"
      test -x "$ROSETTA_X87_PATH" || die "Patched x87sidecar is missing. Run make install."
      export X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0
      ;;
    off) unset ROSETTA_X87_PATH ;;
    *) die "Unknown MACTMNF_X87: $MACTMNF_X87 (use on or off)" ;;
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
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run ./install.sh first."
  without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG="${WINEDEBUG:--all}" "$wine_bin" "$@"
}

configure_wine_audio() {
  local wine_bin="$1"
  local driver

  case "$MACTMNF_AUDIO" in
    off) driver="" ;;
    on) driver="coreaudio" ;;
    *) die "Unknown MACTMNF_AUDIO: $MACTMNF_AUDIO (use off or on)" ;;
  esac

  # Current mmdevapi loads the Unix audio library directly, bypassing DLL
  # overrides. An empty driver list is Wine's supported no-audio setting.
  without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
    'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d "$driver" /f >/dev/null
}

require_macos() {
  test "$(uname -s)" = "Darwin" || die "MacTMNF supports macOS only."
  test "$(uname -m)" = "arm64" || die "This installer currently supports Apple Silicon Macs only."
}

rosetta_is_ready() {
  /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1
}

is_safe_managed_path() {
  python3 "${MACTMNF_PROJECT_DIR}/lib/managed_paths.py" path "$1"
}

validate_managed_layout() {
  python3 "${MACTMNF_PROJECT_DIR}/lib/managed_paths.py" layout \
    "$MACTMNF_PREFIX" "$MACTMNF_RUNTIME" "$MACTMNF_LAUNCHER" "$MACTMNF_USERDATA"
}

require_owned_prefix() {
  python3 "${MACTMNF_PROJECT_DIR}/lib/managed_paths.py" prefix "$MACTMNF_PREFIX"
}

guard_prefix_operation() {
  validate_managed_layout || die "Unsafe installation layout."
  if test "${MACTMNF_OPERATION_FD+x}" != x; then
    mkdir -p "$(dirname "$MACTMNF_PREFIX")"
    exec python3 "${MACTMNF_PROJECT_DIR}/lib/operation_guard.py" run "$MACTMNF_PREFIX" /bin/bash "$@"
  fi
  python3 "${MACTMNF_PROJECT_DIR}/lib/operation_guard.py" held "$MACTMNF_PREFIX" \
    || die "Invalid inherited installation lock."
}

without_operation_lock() (
  if test "${MACTMNF_OPERATION_FD+x}" = x; then
    case "$MACTMNF_OPERATION_FD" in ''|*[!0-9]*) die "Invalid operation descriptor." ;; esac
    # Bash 3.2 has no dynamic-FD redirection. The descriptor is digits only and
    # the guarded entry point verified its identity before any Wine invocation.
    eval "exec ${MACTMNF_OPERATION_FD}>&-"
  fi
  unset MACTMNF_OPERATION_FD
  exec "$@"
)
