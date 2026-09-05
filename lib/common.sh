#!/bin/bash
# shellcheck disable=SC2034

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
: "${MACTMNF_WINDOW_SIZE:=1280x720}"
: "${MACTMNF_AUDIO:=on}"
: "${MACTMNF_X87:=on}"
: "${MACTMNF_PREFIX:=${MACTMNF_ROOT}/prefix}"
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

wine_builtin_d3dx9_30() {
  local wine_bin="$1"
  local wine_root
  local candidate

  wine_root="$(CDPATH='' cd -- "$(dirname -- "$wine_bin")/.." && pwd)"
  for candidate in \
    "${wine_root}/lib/wine/i386-windows/d3dx9_30.dll" \
    "${wine_root}/dlls/d3dx9_30/i386-windows/d3dx9_30.dll"; do
    if test -f "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

d9vk_is_installed() {
  test -f "$MACTMNF_D9VK_DLL" || return 1
  test -f "$PREFIX_D9VK_DLL" || return 1
  test "$(sha256_file "$MACTMNF_D9VK_DLL")" = "$(sha256_file "$PREFIX_D9VK_DLL")"
}

# Print one environment assignment per line; callers read these into an array.
renderer_overrides() {
  case "$MACTMNF_RENDERER" in
    d9vk) printf '%s' 'd3d9=n' ;;
    wined3d-vulkan|wined3d-gl) printf '%s' 'd3d9=b' ;;
    *) die "Unknown MACTMNF_RENDERER: $MACTMNF_RENDERER (use d9vk, wined3d-vulkan or wined3d-gl)" ;;
  esac
}

renderer_env() {
  case "$MACTMNF_RENDERER" in
    d9vk)
      printf '%s\n' \
        "DXVK_CONFIG_FILE=${DXVK_CONFIG_FILE:-$DXVK_CONF}" \
        "DXVK_STATE_CACHE_PATH=${DXVK_STATE_CACHE_PATH:-$MACTMNF_PREFIX}" \
        "DXVK_LOG_LEVEL=${DXVK_LOG_LEVEL:-warn}" \
        "DXVK_LOG_PATH=${DXVK_LOG_PATH:-none}"
      ;;
    wined3d-vulkan) printf '%s\n' "WINE_D3D_CONFIG=renderer=vulkan${WINE_D3D_CONFIG:+,$WINE_D3D_CONFIG}" ;;
    wined3d-gl) printf '%s\n' "WINE_D3D_CONFIG=renderer=gl${WINE_D3D_CONFIG:+,$WINE_D3D_CONFIG}" ;;
  esac
}

download_verified() {
  local url="$1"
  local destination="$2"
  local expected="$3"
  local partial="${destination}.partial"

  mkdir -p "$(dirname "$destination")"
  if verify_file "$destination" "$expected"; then
    say "Using cached $(basename "$destination")"
    return 0
  fi

  if test -e "$destination"; then
    warn "Discarding cached file with the wrong SHA-256: $destination"
    rm -f "$destination"
  fi

  say "Downloading $(basename "$destination")"
  rm -f "$partial"
  curl --fail --location --retry 3 --progress-bar --output "$partial" "$url"
  verify_file "$partial" "$expected" || {
    rm -f "$partial"
    die "SHA-256 verification failed for $url"
  }
  mv "$partial" "$destination"
}

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
  env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG="${WINEDEBUG:--all}" "$wine_bin" "$@"
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
  env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
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
  local path="$1"

  test -n "$path" || return 1
  test "$path" != "/" || return 1
  test "$path" != "$HOME" || return 1
  case "$path" in
    */../*|*/..|*/./*|*/.) return 1 ;;
  esac
  case "$path" in
    "$HOME"/*|/private/tmp/*|/tmp/*) return 0 ;;
    *) return 1 ;;
  esac
}
