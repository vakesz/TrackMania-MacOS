#!/bin/bash
# shellcheck disable=SC2034

if test "${_TRACKMANIA_MAC_COMMON_SOURCED:-0}" = 1; then return 0; fi
_TRACKMANIA_MAC_COMMON_SOURCED=1

set -u

# The hub and its installed launchers execute Python directly from signed app
# resources. Writing __pycache__ files there after codesign would invalidate
# the bundle seal, so all project entry points run Python without bytecode.
export PYTHONDONTWRITEBYTECODE=1

TRACKMANIA_MAC_PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=src/shell/lib/legacy.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/legacy.sh"
# Before anything reads a setting: earlier releases' names become this release's.
adopt_legacy_settings
# Installed launchers carry their paths with them; a checkout has no such file.
if test -f "${TRACKMANIA_MAC_PROJECT_DIR}/config/installation.sh"; then
  # shellcheck source=/dev/null
  . "${TRACKMANIA_MAC_PROJECT_DIR}/config/installation.sh"
fi

# Every default location lives in this one folder. src/python/trackmania_mac/hub/paths.py
# computes the same defaults for the hub.
readonly TRACKMANIA_MAC_DATA_DIR="${HOME}/Library/TrackManiaMacOS"
readonly TRACKMANIA_MAC_LOG_DIR="${TRACKMANIA_MAC_DATA_DIR}/logs"
# Marker files in a Wine prefix (src/python/trackmania_mac/markers.py names them
# all; an earlier release's names, in legacy.sh, are read as well).
readonly PREFIX_MARKER_NAME=.trackmania-mac-prefix
readonly INITIALIZING_MARKER_NAME=.trackmania-mac-initializing
readonly PROFILE_BASELINE_NAME=.trackmania-mac-profile-baseline
# This release's Python package and CLI, as an installed launcher carries them.
readonly TRACKMANIA_MAC_PYTHON_PACKAGE=trackmania_mac
readonly TRACKMANIA_MAC_CLI_NAME=trackmania-mac
# A title location given explicitly, by the environment or an installed
# launcher's config, is the user's choice: the CLI then never moves files from
# the folders of earlier releases (see migrate_legacy_install). A custom
# TRACKMANIA_MAC_SUPPORT or TRACKMANIA_MAC_CACHE does not count: the move leaves those folders alone
# but still has to move the title's prefix and saves before anything uses them.
readonly TRACKMANIA_MAC_LOCATION_SETTINGS=(TRACKMANIA_MAC_ROOT TRACKMANIA_MAC_PREFIX TRACKMANIA_MAC_USERDATA)
TRACKMANIA_MAC_EXPLICIT_PATHS=""
for _trackmania_mac_name in "${TRACKMANIA_MAC_LOCATION_SETTINGS[@]}"; do
  if test -n "${!_trackmania_mac_name+x}"; then TRACKMANIA_MAC_EXPLICIT_PATHS=1; fi
done
unset _trackmania_mac_name
readonly TRACKMANIA_MAC_EXPLICIT_PATHS
# Launch options an installed launcher records (install_app). One given by the
# environment (or this launcher's own config) is the user's choice; for the others
# install_app keeps what the launcher it replaces recorded, so reinstalling the
# apps never resets the game defaults chosen in the hub.
readonly TRACKMANIA_MAC_LAUNCH_SETTINGS=(TRACKMANIA_MAC_WINDOW_SIZE TRACKMANIA_MAC_AUDIO TRACKMANIA_MAC_X87 TRACKMANIA_MAC_RENDERER)
TRACKMANIA_MAC_EXPLICIT_LAUNCH_OPTIONS=" "
for _trackmania_mac_name in "${TRACKMANIA_MAC_LAUNCH_SETTINGS[@]}"; do
  if test -n "${!_trackmania_mac_name:-}"; then
    TRACKMANIA_MAC_EXPLICIT_LAUNCH_OPTIONS="${TRACKMANIA_MAC_EXPLICIT_LAUNCH_OPTIONS}${_trackmania_mac_name} "
  fi
done
unset _trackmania_mac_name
readonly TRACKMANIA_MAC_EXPLICIT_LAUNCH_OPTIONS
# A custom TRACKMANIA_MAC_ROOT keeps its saves beside the prefix, as it always has.
if test -n "${TRACKMANIA_MAC_ROOT+x}"; then _trackmania_mac_userdata_default=root; else _trackmania_mac_userdata_default=data; fi

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

# Remove abandoned staging leftovers: direct children of DIR named GLOB, of TYPE
# (d by default, f for files), owned by this user, not symlinks, and idle for
# more than an hour. Never fails the caller.
sweep_stale() {
  local dir="$1" glob="$2" type="${3:-d}" entry
  test -d "$dir" || return 0
  while IFS= read -r entry; do
    test -n "$entry" || continue
    rm -rf -- "$entry" || warn "Cannot remove stale $entry"
  done < <(find "$dir" -mindepth 1 -maxdepth 1 -type "$type" -name "$glob" \
    -user "$(id -u)" -mmin +60 2>/dev/null || true)
  return 0
}

have() {
  command -v "$1" >/dev/null 2>&1
}

# Run the trackmania_mac Python package. The package root travels with the project
# (checkout or installed launcher), so one helper serves every caller.
# Exec sites inline PYTHONPATH directly to preserve process replacement.
readonly TRACKMANIA_MAC_PYTHONPATH="${TRACKMANIA_MAC_PROJECT_DIR}/src/python${PYTHONPATH:+:$PYTHONPATH}"
if test -z "${TRACKMANIA_MAC_PYTHON:-}" && test -x "${TRACKMANIA_MAC_PROJECT_DIR}/python/bin/python3"; then
  TRACKMANIA_MAC_PYTHON="${TRACKMANIA_MAC_PROJECT_DIR}/python/bin/python3"
fi
: "${TRACKMANIA_MAC_PYTHON:=python3}"
readonly TRACKMANIA_MAC_PYTHON

trackmania_mac_python() {
  if [[ "$TRACKMANIA_MAC_PYTHON" = */* ]]; then
    test -x "$TRACKMANIA_MAC_PYTHON" || die "Bundled Python is missing or not executable: $TRACKMANIA_MAC_PYTHON"
  else
    have "$TRACKMANIA_MAC_PYTHON" || die "Python 3 is required (missing command: $TRACKMANIA_MAC_PYTHON)"
  fi
  PYTHONPATH="$TRACKMANIA_MAC_PYTHONPATH" "$TRACKMANIA_MAC_PYTHON" "$@"
}

: "${TRACKMANIA_MAC_TITLE:=tmnf}"
# shellcheck source=src/shell/lib/titles.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/titles.sh"

# Per-title paths and names live in config/titles/*.json and the release pins in
# config/pins.json; one Python start (trackmania_mac_load_title) defines all of them.
case "${1:-}" in
  hub|titles)
    # The Python hub backend contains broken registry files per title and
    # computes its paths from the registry itself; a strict shell-side gate
    # here would break that containment. No Python is started here: the hub
    # reads the pins itself, so the title and pin fields are only defined
    # (empty) to keep the guarded defaults below from tripping `set -u`.
    TRACKMANIA_MAC_TITLE_LEGACY_PREFIX_SUBDIR=""
    TRACKMANIA_MAC_TITLE_GAME_DIR_NAME=""
    TRACKMANIA_MAC_TITLE_GAME_EXE=""
    TRACKMANIA_MAC_TITLE_LAUNCHER_EXE=""
    TRACKMANIA_MAC_TITLE_USER_SUBDIR=""
    TRACKMANIA_MAC_TITLE_APP_NAME=""
    TRACKMANIA_MAC_TITLE_SETTINGS_APP_NAME=""
    DIRECTX_VERSION="" DIRECTX_URL="" DIRECTX_SHA256=""
    TMLOADER_URL="" TMLOADER_BUNDLE_VERSION="" TMLOADER_SHA256="" TMLOADER_EXE_SHA256=""
    TMLOADER_GAME_VERSION="" TMLOADER_GAME_SHA256=""
    TMLOADER_COREMOD_VERSION="" TMLOADER_COREMOD_SHA256=""
    TMCOMP_VERSION="" TMCOMP_DLL_SHA256=""
    TMLOADER_PREFIX_DIR="" TMLOADER_EXE_REL="" TMCOMP_DLL_REL="" TMLOADER_GAME_EXE_REL=""
    TMLOADER_COREMOD_DLL_REL="" TMLOADER_SETTINGS_REL="" TMLOADER_PROFILE_REL=""
    OPENAL_SOFT_VERSION="" OPENAL_SOFT_URL="" OPENAL_SOFT_SHA256="" OPENAL_SOFT_DLL_SHA256=""
    ;;
  *)
    trackmania_mac_title_is_known "$TRACKMANIA_MAC_TITLE" \
      || die "Unknown TRACKMANIA_MAC_TITLE: $TRACKMANIA_MAC_TITLE (see ${TRACKMANIA_MAC_TITLES_DIR}/*.json)"
    trackmania_mac_load_title "$TRACKMANIA_MAC_TITLE" \
      || die "Invalid title registry entry: ${TRACKMANIA_MAC_TITLES_DIR}/${TRACKMANIA_MAC_TITLE}.json"
    : "${TRACKMANIA_MAC_ROOT:=${TRACKMANIA_MAC_DATA_DIR}/games/${TRACKMANIA_MAC_TITLE}}"
    ;;
esac

# Wine-owned Direct3D 9 shader compilers that installation restores over the
# native DXSETUP copies and Play forces to builtin. tests/test_installation.py
# checks that runtime_manifest.py lists the same names.
readonly RUNTIME_D3DX9_DLLS=(d3dx9_30 d3dx9_31 d3dx9_43)
# shellcheck source=src/shell/lib/version.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/version.sh"
TRACKMANIA_MAC_VERSION="$(trackmania_mac_project_version "$TRACKMANIA_MAC_PROJECT_DIR")" \
  || die "Cannot read the release version from ${TRACKMANIA_MAC_PROJECT_DIR}/pyproject.toml"
RUNTIME_VERSION="$TRACKMANIA_MAC_VERSION"
RUNTIME_RELEASE_URL="https://github.com/vakesz/TrackMania-MacOS/releases/download/v${RUNTIME_VERSION}"
RUNTIME_REPOSITORY_URL="https://github.com/vakesz/TrackMania-MacOS.git"
RELEASE_NAME="trackmania-mac"
RUNTIME_ARCHIVE_NAME="${RELEASE_NAME}-${RUNTIME_VERSION}-runtime.tar.xz"

: "${TRACKMANIA_MAC_CACHE:=${TRACKMANIA_MAC_DATA_DIR}/cache}"
: "${TRACKMANIA_MAC_SUPPORT:=${TRACKMANIA_MAC_DATA_DIR}}"
: "${TRACKMANIA_MAC_RUNTIME:=${TRACKMANIA_MAC_SUPPORT}/runtime/${RUNTIME_VERSION}}"
if test -w /Applications; then
  : "${TRACKMANIA_MAC_APPS_DIR:=/Applications}"
else
  : "${TRACKMANIA_MAC_APPS_DIR:=${HOME}/Applications}"
fi
: "${TRACKMANIA_MAC_APP:=${TRACKMANIA_MAC_APPS_DIR}/${TRACKMANIA_MAC_TITLE_APP_NAME}}"
: "${TRACKMANIA_MAC_SETTINGS_APP:=$(dirname "$TRACKMANIA_MAC_APP")/${TRACKMANIA_MAC_TITLE_SETTINGS_APP_NAME}}"
: "${TRACKMANIA_MAC_LAUNCHER:=${TRACKMANIA_MAC_SUPPORT}/launchers/${TRACKMANIA_MAC_TITLE}}"
# "fullscreen" (the default) launches the game directly so it uses its own
# display mode, set in TrackMania Settings. A WxH value instead runs it inside
# an explorer virtual desktop of that size, whose title bar shrinks the drawable
# and clips the game's fixed-size menu, so use it only when a fixed window is
# wanted.
: "${TRACKMANIA_MAC_WINDOW_SIZE:=fullscreen}"
: "${TRACKMANIA_MAC_AUDIO:=on}"
# Session diagnostics for make diag: game state, frame rate and CPU in the play log
: "${TRACKMANIA_MAC_DIAG:=off}"
: "${TRACKMANIA_MAC_X87:=on}"
# x87sidecar hooks Rosetta builds from macOS 15.4 on. Wine starts it in
# cooperative mode, which runs the game unhooked on any other Rosetta.
TRACKMANIA_MAC_X87_UNSUPPORTED_HINT="x87sidecar does not support this Rosetta, so the game runs on stock Rosetta and is slower. x87 acceleration needs macOS 15.4 or later; set TRACKMANIA_MAC_X87=off to skip it."
# Title-agnostic fallback: the hub commands never consume TRACKMANIA_MAC_ROOT themselves,
# and title-aware commands set their own per-title default above.
: "${TRACKMANIA_MAC_ROOT:=${TRACKMANIA_MAC_DATA_DIR}/games/${TRACKMANIA_MAC_TITLE}}"
: "${TRACKMANIA_MAC_PREFIX:=${TRACKMANIA_MAC_ROOT}/prefix}"
# Game's OpenAL implementation, applied by make install: soft or creative. The
# last applied choice stays until TRACKMANIA_MAC_OPENAL selects another one.
readonly OPENAL_MARKER="${TRACKMANIA_MAC_PREFIX}/.trackmania-mac-openal"
readonly LEGACY_OPENAL_MARKER="${TRACKMANIA_MAC_PREFIX}/${LEGACY_OPENAL_MARKER_NAME}"
if test -z "${TRACKMANIA_MAC_OPENAL:-}"; then
  for _trackmania_mac_marker in "$OPENAL_MARKER" "$LEGACY_OPENAL_MARKER"; do
    if test -f "$_trackmania_mac_marker"; then
      TRACKMANIA_MAC_OPENAL="$(tr -d '\r\n' <"$_trackmania_mac_marker")"
      break
    fi
  done
  unset _trackmania_mac_marker
fi
: "${TRACKMANIA_MAC_OPENAL:=soft}"
if test "$_trackmania_mac_userdata_default" = root; then
  : "${TRACKMANIA_MAC_USERDATA:=${TRACKMANIA_MAC_ROOT}/UserData}"
else
  : "${TRACKMANIA_MAC_USERDATA:=${TRACKMANIA_MAC_DATA_DIR}/saves/${TRACKMANIA_MAC_TITLE}}"
fi
unset _trackmania_mac_userdata_default
# Direct3D 9 renderer: d9vk (DXVK on MoltenVK/Metal), wined3d-vulkan, wined3d-gl
: "${TRACKMANIA_MAC_RENDERER:=d9vk}"
if test -x "${TRACKMANIA_MAC_RUNTIME}/bin/wine"; then
  runtime_dir="$TRACKMANIA_MAC_RUNTIME"
else
  runtime_dir="${TRACKMANIA_MAC_PROJECT_DIR}/.build/release/runtime"
fi
: "${TRACKMANIA_MAC_D9VK_DLL:=${runtime_dir}/lib/d9vk/d3d9.dll}"
: "${TRACKMANIA_MAC_SIDECAR:=${runtime_dir}/bin/x87sidecar}"

readonly GAME_DIR="${TRACKMANIA_MAC_PREFIX}/drive_c/Program Files (x86)/${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}"
readonly GAME_EXE="${GAME_DIR}/${TRACKMANIA_MAC_TITLE_GAME_EXE}"
readonly LAUNCHER_EXE="${GAME_DIR}/${TRACKMANIA_MAC_TITLE_LAUNCHER_EXE}"
readonly DIRECTX_ARCHIVE="${TRACKMANIA_MAC_CACHE}/directx_Jun2010_redist.exe"
readonly DIRECTX_MARKER="${TRACKMANIA_MAC_PREFIX}/.trackmania-mac-directx-jun2010"
readonly LEGACY_DIRECTX_MARKER="${TRACKMANIA_MAC_PREFIX}/${LEGACY_DIRECTX_MARKER_NAME}"
readonly TMLOADER_ARCHIVE="${TRACKMANIA_MAC_CACHE}/TMLoader-${TMLOADER_BUNDLE_VERSION}.zip"
# The ModLoader layout comes from trackmania_mac.install.modloader through the registry
# export; only the prefix-specific base is joined here.
readonly TMLOADER_DIR="${TRACKMANIA_MAC_PREFIX}/${TMLOADER_PREFIX_DIR}"
readonly TMLOADER_EXE="${TMLOADER_DIR}/${TMLOADER_EXE_REL}"
readonly TMCOMP_DLL="${TMLOADER_DIR}/${TMCOMP_DLL_REL}"
readonly TMLOADER_GAME_EXE="${TMLOADER_DIR}/${TMLOADER_GAME_EXE_REL}"
readonly PREFIX_D9VK_DLL="${TRACKMANIA_MAC_PREFIX}/drive_c/windows/syswow64/d3d9.dll"
readonly DXVK_CONF="${TRACKMANIA_MAC_PROJECT_DIR}/config/dxvk.conf"
readonly TMLOADER_COREMOD_DLL="${TMLOADER_DIR}/${TMLOADER_COREMOD_DLL_REL}"
readonly TMLOADER_SETTINGS="${TMLOADER_DIR}/${TMLOADER_SETTINGS_REL}"
readonly TMLOADER_PROFILE_FILE="${TMLOADER_DIR}/${TMLOADER_PROFILE_REL}"
readonly OPENAL_SOFT_ARCHIVE="${TRACKMANIA_MAC_CACHE}/openal-soft-${OPENAL_SOFT_VERSION}-bin.zip"
readonly GAME_OPENAL="${GAME_DIR}/OpenAL32.dll"
readonly GAME_OPENAL_CREATIVE="${GAME_DIR}/OpenAL32.creative.dll"
readonly GAME_OPENAL_COPYING="${GAME_DIR}/OpenAL32.COPYING.txt"

python_is_usable() {
  { [[ "$TRACKMANIA_MAC_PYTHON" = */* ]] && test -x "$TRACKMANIA_MAC_PYTHON"; } \
    || { [[ "$TRACKMANIA_MAC_PYTHON" != */* ]] && have "$TRACKMANIA_MAC_PYTHON"; }
}

# Lowercase hex digest only. hashlib streams several times faster than shasum
# on large archives; shasum remains the fallback when no Python is usable here.
# -I is not an option: it would drop the PYTHONPATH that locates the package.
sha256_file() {
  local digest
  if python_is_usable; then
    if digest="$(PYTHONPATH="$TRACKMANIA_MAC_PYTHONPATH" "$TRACKMANIA_MAC_PYTHON" -m trackmania_mac.install.hashing sha256 "$1" 2>/dev/null)" \
      && [[ "$digest" =~ ^[0-9a-f]{64}$ ]]; then
      printf '%s\n' "$digest"
      return 0
    fi
  fi
  shasum -a 256 "$1" | awk '{print $1}'
}

# One lowercase digest per line for each PATH, from a single Python start. If that
# fails for any reason, every path gets the shasum fallback; a path shasum cannot
# read yields an empty line, which matches no expected digest.
sha256_files() {
  local digests digest path count
  if python_is_usable; then
    if digests="$(PYTHONPATH="$TRACKMANIA_MAC_PYTHONPATH" "$TRACKMANIA_MAC_PYTHON" -m trackmania_mac.install.hashing sha256 "$@" 2>/dev/null)"; then
      count=0
      while IFS= read -r digest; do
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || { count=-1; break; }
        count=$((count + 1))
      done <<<"$digests"
      if test "$count" -eq "$#"; then
        printf '%s\n' "$digests"
        return 0
      fi
    fi
  fi
  for path in "$@"; do
    printf '%s\n' "$(shasum -a 256 "$path" 2>/dev/null | awk '{print $1}')"
  done
}

verify_file() {
  local path="$1"
  local expected="$2"
  local actual

  test -f "$path" || return 1
  actual="$(sha256_file "$path")"
  test "$actual" = "$expected"
}

# Arguments are PATH EXPECTED_SHA256 pairs. Any missing file means no match; the
# rest are hashed together, so a mismatch no longer skips the later files.
verify_files() {
  local -a paths=() wanted=()
  local digests digest index=0
  while test "$#" -ge 2; do
    test -f "$1" || return 1
    paths+=("$1")
    wanted+=("$2")
    shift 2
  done
  test "${#paths[@]}" -gt 0 || return 0
  digests="$(sha256_files "${paths[@]}")"
  while IFS= read -r digest; do
    test "$digest" = "${wanted[index]}" || return 1
    index=$((index + 1))
  done <<<"$digests"
  test "$index" -eq "${#paths[@]}"
}

competition_patch_is_current() {
  verify_files \
    "$TMLOADER_EXE" "$TMLOADER_EXE_SHA256" \
    "$TMCOMP_DLL" "$TMCOMP_DLL_SHA256" \
    "$TMLOADER_GAME_EXE" "$TMLOADER_GAME_SHA256" \
    "$TMLOADER_COREMOD_DLL" "$TMLOADER_COREMOD_SHA256"
}

directx_is_current() {
  local marker
  for marker in "$DIRECTX_MARKER" "$LEGACY_DIRECTX_MARKER"; do
    if test -f "$marker"; then
      test "$(tr -d '\r\n' <"$marker")" = "$DIRECTX_SHA256"
      return
    fi
  done
  return 1
}

# prefix_marker_present NAME_VARIABLE — the prefix carries that marker under
# this release's name or (for the matching LEGACY_ name) an earlier one's.
prefix_marker_present() {
  local legacy
  case "$1" in
    PREFIX_MARKER_NAME) legacy="$LEGACY_PREFIX_MARKER" ;;
    INITIALIZING_MARKER_NAME) legacy="$LEGACY_INITIALIZING_MARKER" ;;
    *) die "Unknown prefix marker: $1" ;;
  esac
  test -f "${TRACKMANIA_MAC_PREFIX}/${!1}" || test -f "${TRACKMANIA_MAC_PREFIX}/${legacy}"
}

d9vk_is_installed() {
  test -f "$TRACKMANIA_MAC_D9VK_DLL" || return 1
  test -f "$PREFIX_D9VK_DLL" || return 1
  cmp -s "$TRACKMANIA_MAC_D9VK_DLL" "$PREFIX_D9VK_DLL"
}

# The openal component: a title whose release ships Creative's OpenAL router
# pins that DLL in the registry (creative_openal_sha256). OpenAL Soft's official
# Windows binaries (config/pins.json) replace it, because the router's software
# mixer costs much more CPU under Wine on macOS 27. Other titles keep theirs.
openal_soft_is_installed() {
  verify_file "$GAME_OPENAL" "$OPENAL_SOFT_DLL_SHA256"
}

# soft, creative, other or missing: the OpenAL32.dll the game loads from its directory.
game_openal() {
  if test ! -f "$GAME_OPENAL"; then
    printf 'missing\n'
  elif openal_soft_is_installed; then
    printf 'soft\n'
  elif verify_file "$GAME_OPENAL" "$TRACKMANIA_MAC_TITLE_CREATIVE_OPENAL_SHA256"; then
    printf 'creative\n'
  else
    printf 'other\n'
  fi
}

# make install applied TRACKMANIA_MAC_OPENAL. It leaves a missing or unrecognised DLL alone.
openal_is_current() {
  title_has openal || return 0
  case "$(game_openal)" in
    "$TRACKMANIA_MAC_OPENAL"|other|missing) return 0 ;;
    *) return 1 ;;
  esac
}

competition_patch_is_ready() {
  competition_patch_is_current \
    && test -s "$TMLOADER_SETTINGS" \
    && test -s "$TMLOADER_PROFILE_FILE"
}

# launch_option_valid NAME VALUE — VALUE is one NAME accepts, for the launch
# options an installed launcher records (TRACKMANIA_MAC_LAUNCH_SETTINGS).
# src/python/trackmania_mac/app/launcher_config.py repeats this for the hub's view.
launch_option_valid() {
  case "$1" in
    TRACKMANIA_MAC_AUDIO|TRACKMANIA_MAC_X87) case "$2" in on|off) return 0 ;; esac ;;
    TRACKMANIA_MAC_RENDERER) case "$2" in d9vk|wined3d-vulkan|wined3d-gl) return 0 ;; esac ;;
    TRACKMANIA_MAC_WINDOW_SIZE)
      test "$2" = fullscreen && return 0
      [[ "$2" =~ ^[1-9][0-9]{0,4}x[1-9][0-9]{0,4}$ ]] && test "${2%x*}" -le 65535 && test "${2#*x}" -le 65535 && return 0
      ;;
  esac
  return 1
}

validate_launch_options() {
  trackmania_mac_title_is_known "$TRACKMANIA_MAC_TITLE" \
    || die "Unknown TRACKMANIA_MAC_TITLE: $TRACKMANIA_MAC_TITLE (see ${TRACKMANIA_MAC_TITLES_DIR}/*.json)"
  launch_option_valid TRACKMANIA_MAC_AUDIO "$TRACKMANIA_MAC_AUDIO" || die "Unknown TRACKMANIA_MAC_AUDIO: $TRACKMANIA_MAC_AUDIO (use on or off)"
  launch_option_valid TRACKMANIA_MAC_X87 "$TRACKMANIA_MAC_X87" || die "Unknown TRACKMANIA_MAC_X87: $TRACKMANIA_MAC_X87 (use on or off)"
  case "$TRACKMANIA_MAC_OPENAL" in soft|creative) ;; *) die "Unknown TRACKMANIA_MAC_OPENAL: $TRACKMANIA_MAC_OPENAL (use soft or creative)" ;; esac
  case "$TRACKMANIA_MAC_DIAG" in on|off) ;; *) die "Unknown TRACKMANIA_MAC_DIAG: $TRACKMANIA_MAC_DIAG (use on or off)" ;; esac
  launch_option_valid TRACKMANIA_MAC_RENDERER "$TRACKMANIA_MAC_RENDERER" \
    || die "Unknown TRACKMANIA_MAC_RENDERER: $TRACKMANIA_MAC_RENDERER (use d9vk, wined3d-vulkan or wined3d-gl)"
  launch_option_valid TRACKMANIA_MAC_WINDOW_SIZE "$TRACKMANIA_MAC_WINDOW_SIZE" \
    || die "Invalid TRACKMANIA_MAC_WINDOW_SIZE: use fullscreen or WIDTHxHEIGHT, with dimensions 1..65535."
}

download_verified() (
  local url="$1"
  local destination="$2"
  local expected="$3"
  local max_bytes="${4:-}"
  local partial cleanup limit=()

  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid download SHA-256."
  expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
  mkdir -p "$(dirname "$destination")"
  if verify_file "$destination" "$expected"; then
    say "Using cached $(basename "$destination")"
    return 0
  fi

  say "Downloading $(basename "$destination")"
  sweep_stale "$(dirname "$destination")" '*.partial.*' f
  partial="$(mktemp "${destination}.partial.XXXXXX")" || die "Cannot stage download."
  printf -v cleanup 'rm -f -- %q' "$partial"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  trap 'exit 1' HUP INT TERM
  if test -n "$max_bytes"; then
    [[ "$max_bytes" =~ ^[1-9][0-9]{0,15}$ ]] || die "Invalid download size limit: $max_bytes"
    limit=(--max-filesize "$max_bytes")
  fi
  curl -q --fail --location --retry 3 --retry-all-errors --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
    ${limit[@]+"${limit[@]}"} --progress-bar --output "$partial" "$url" || die "Download failed: $url"
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
  if test -n "${TRACKMANIA_MAC_WINE:-}"; then
    printf '%s\n' "$TRACKMANIA_MAC_WINE"
  elif test -x "${TRACKMANIA_MAC_RUNTIME}/bin/wine"; then
    printf '%s\n' "${TRACKMANIA_MAC_RUNTIME}/bin/wine"
  elif test -x "${TRACKMANIA_MAC_PROJECT_DIR}/.build/release/runtime/bin/wine"; then
    printf '%s\n' "${TRACKMANIA_MAC_PROJECT_DIR}/.build/release/runtime/bin/wine"
  else
    return 1
  fi
}

session_helper() {
  local wine_bin="$1" helper
  helper="$(dirname "$(dirname "$wine_bin")")/libexec/trackmania-mac-session.exe"
  test -f "$helper" || die "The runtime has no session supervisor. Build and install the current runtime."
  test -x "$(dirname "$wine_bin")/wineserver" || die "The runtime is missing wineserver."
  printf '%s\n' "$helper"
}

configure_translation() {
  case "$TRACKMANIA_MAC_X87" in
    on)
      export ROSETTA_X87_PATH="${ROSETTA_X87_PATH:-$TRACKMANIA_MAC_SIDECAR}"
      test -x "$ROSETTA_X87_PATH" || die "Patched x87sidecar is missing. Run make install."
      export X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0
      ;;
    off) unset ROSETTA_X87_PATH ;;
    *) die "Unknown TRACKMANIA_MAC_X87: $TRACKMANIA_MAC_X87 (use on or off)" ;;
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
  without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG="${WINEDEBUG:--all}" "$wine_bin" "$@"
}

configure_wine_audio() {
  local wine_bin="$1"
  local driver

  case "$TRACKMANIA_MAC_AUDIO" in
    off) driver="" ;;
    on) driver="coreaudio" ;;
    *) die "Unknown TRACKMANIA_MAC_AUDIO: $TRACKMANIA_MAC_AUDIO (use off or on)" ;;
  esac

  # Current mmdevapi loads the Unix audio library directly, bypassing DLL
  # overrides. An empty driver list is Wine's supported no-audio setting.
  without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
    'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d "$driver" /f >/dev/null
}

require_macos() {
  test "$(uname -s)" = "Darwin" || die "TrackMania Mac supports macOS only."
  test "$(uname -m)" = "arm64" || die "This installer currently supports Apple Silicon Macs only."
}

rosetta_is_ready() {
  /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1
}

is_safe_managed_path() {
  trackmania_mac_python -m trackmania_mac.guard.managed_paths path "$1"
}

validate_managed_layout() {
  trackmania_mac_python -m trackmania_mac.guard.managed_paths layout \
    "$TRACKMANIA_MAC_PREFIX" "$TRACKMANIA_MAC_RUNTIME" "$TRACKMANIA_MAC_LAUNCHER" "$TRACKMANIA_MAC_USERDATA"
}

require_owned_prefix() {
  trackmania_mac_python -m trackmania_mac.guard.managed_paths prefix "$TRACKMANIA_MAC_PREFIX"
}

# require_owned_prefix then validate_managed_layout, in one Python start. The
# prefix is checked first and keeps its own message; exit status 3 means the
# prefix passed and the layout did not.
require_owned_managed_layout() {
  local status=0
  trackmania_mac_python -m trackmania_mac.guard.managed_paths prefix-layout \
    "$TRACKMANIA_MAC_PREFIX" "$TRACKMANIA_MAC_RUNTIME" "$TRACKMANIA_MAC_LAUNCHER" "$TRACKMANIA_MAC_USERDATA" || status=$?
  case "$status" in
    0) ;;
    3) die "Unsafe installation layout." ;;
    *) die "A managed Wine prefix is required." ;;
  esac
}

guard_prefix_operation() {
  validate_managed_layout || die "Unsafe installation layout."
  hold_prefix_operation "$@"
}

# Takes the operation lock, or validates the inherited one. For callers that
# have already checked the layout.
hold_prefix_operation() {
  if test "${TRACKMANIA_MAC_OPERATION_FD+x}" != x; then
    mkdir -p "$(dirname "$TRACKMANIA_MAC_PREFIX")"
    # Process replacement (not a helper call) preserves lock FD inheritance.
    exec /usr/bin/env PYTHONPATH="$TRACKMANIA_MAC_PYTHONPATH" "$TRACKMANIA_MAC_PYTHON" -m trackmania_mac.guard.operation_guard run "$TRACKMANIA_MAC_PREFIX" /bin/bash "$@"
  fi
  trackmania_mac_python -m trackmania_mac.guard.operation_guard held "$TRACKMANIA_MAC_PREFIX" \
    || die "Invalid inherited installation lock."
}

without_operation_lock() (
  if test "${TRACKMANIA_MAC_OPERATION_FD+x}" = x; then
    case "$TRACKMANIA_MAC_OPERATION_FD" in ''|*[!0-9]*) die "Invalid operation descriptor." ;; esac
    # Bash 3.2 has no dynamic-FD redirection. The descriptor is digits only and
    # the guarded entry point verified its identity before any Wine invocation.
    eval "exec ${TRACKMANIA_MAC_OPERATION_FD}>&-"
  fi
  unset TRACKMANIA_MAC_OPERATION_FD
  # No exec here: "$@" may be a shell function (exec only replaces external
  # commands). The descriptor above is already closed in this subshell.
  "$@"
)

# Move an install from the folders of earlier releases into TRACKMANIA_MAC_DATA_DIR before
# a title command uses the new locations. Does nothing when a location was given
# explicitly or nothing is left to move; src/python/trackmania_mac/guard/relocate.py owns
# every rule and creates no folder unless something moves.
migrate_legacy_install() {
  local status=0
  test -z "$TRACKMANIA_MAC_EXPLICIT_PATHS" || return 0
  # Once per command: guarded re-executions and install.sh inherit the answer.
  test -z "${TRACKMANIA_MAC_MIGRATION_CHECKED:-}" || return 0
  export TRACKMANIA_MAC_MIGRATION_CHECKED=1
  # Exit status 3: nothing to move. 0: files of an earlier release are left.
  trackmania_mac_python -m trackmania_mac.guard.relocate pending || status=$?
  case "$status" in
    0) ;;
    3) return 0 ;;
    *) die "Cannot check for files of an earlier release." ;;
  esac
  say "Moving your TrackMania files into ${TRACKMANIA_MAC_DATA_DIR}"
  if ! /bin/bash "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" migrate; then
    # Whether anything moved decides what is still safe to use; relocate.py says.
    die "$(trackmania_mac_python -m trackmania_mac.guard.relocate advice 2>/dev/null \
      || printf 'Your files were not moved completely, so this command did not run.')"
  fi
}
