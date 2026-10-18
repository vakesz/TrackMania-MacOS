#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"

install_rosetta() {
  if rosetta_is_ready; then
    say "Rosetta 2 is ready"
    return
  fi

  say "Installing Rosetta 2"
  /usr/sbin/softwareupdate --install-rosetta --agree-to-license
  rosetta_is_ready || die "Rosetta installation did not complete."
}

. "${SCRIPT_DIR}/lib/runtime.sh"

initialize_prefix() {
  local wine_bin
  local wineboot_bin

  validate_managed_layout || die "Unsafe installation layout."
  python3 "${SCRIPT_DIR}/lib/managed_paths.py" new-prefix "$MACTMNF_PREFIX" \
    || die "Choose an empty prefix or a managed TMNF installation."
  mkdir -p "$MACTMNF_PREFIX"
  if test -f "${MACTMNF_PREFIX}/system.reg"; then
    test -f "${MACTMNF_PREFIX}/.mactmnf-prefix" || die "This prefix is not managed by MacTMNF. Choose a new MACTMNF_PREFIX."
    if test ! -f "${MACTMNF_PREFIX}/.mactmnf-initializing"; then
      WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update
      return
    fi
  fi

  say "Initializing the Wine prefix"
  wine_bin="$(wine_binary)" || die "Wine is not installed."
  wineboot_bin="$(wineboot_binary "$wine_bin")" || die "Cannot locate wineboot."
  touch "${MACTMNF_PREFIX}/.mactmnf-initializing" || die "Cannot mark prefix initialization."
  if test ! -f "${MACTMNF_PREFIX}/.mactmnf-prefix"; then
    if ! printf 'tmnf:1\n' >"${MACTMNF_PREFIX}/.mactmnf-prefix"; then
      rm -f "${MACTMNF_PREFIX}/.mactmnf-prefix" "${MACTMNF_PREFIX}/.mactmnf-initializing"
      die "Cannot mark prefix ownership."
    fi
  fi
  python3 "${SCRIPT_DIR}/lib/managed_paths.py" profile-baseline "$MACTMNF_PREFIX" \
    || die "Cannot record pre-Wineboot profiles; initialization remains incomplete."
  # TMNF is native code. Skip optional Mono/Gecko registration so prefix setup
  # does not stop at download dialogs for unrelated .NET or browser runtimes.
  without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES='mscoree,mshtml=' "$wineboot_bin" --init \
    || die "Wine prefix initialization failed; rerun setup to retry."
  # Redirect fresh Wine default links without touching host data. Preserve older
  # profile arrangements and require explicit backup/repair when seeding is unsafe.
  python3 "${SCRIPT_DIR}/lib/managed_paths.py" setup-documents "$MACTMNF_PREFIX" "$MACTMNF_USERDATA" \
    || die "Documents setup is incomplete; no external settings were seeded."
  mkdir -p "${MACTMNF_USERDATA}/TmForever/Config"
  if test ! -e "${MACTMNF_USERDATA}/TmForever/Config/Default.SystemConfig.Gbx" \
    && test ! -L "${MACTMNF_USERDATA}/TmForever/Config/Default.SystemConfig.Gbx"; then
    cp "${MACTMNF_RUNTIME}/config/Default.SystemConfig.Gbx" "${MACTMNF_USERDATA}/TmForever/Config/"
  fi
  rm -f "${MACTMNF_PREFIX}/.mactmnf-profile-baseline" "${MACTMNF_PREFIX}/.mactmnf-initializing"
}

install_directx_runtime() (
  local extract_dir cleanup

  if directx_is_current; then
    repair_runtime_dlls
    say "DirectX End-User Runtimes $DIRECTX_VERSION are already installed"
    return
  fi

  download_verified "$DIRECTX_URL" "$DIRECTX_ARCHIVE" "$DIRECTX_SHA256"
  extract_dir="$(mktemp -d "${MACTMNF_CACHE}/directx-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  bsdtar -xf "$DIRECTX_ARCHIVE" -C "$extract_dir"
  test -f "${extract_dir}/DXSETUP.exe" || die "DirectX archive did not contain DXSETUP.exe."

  say "Installing DirectX End-User Runtimes $DIRECTX_VERSION"
  # Managed DirectX probes Wine's .NET shim even when Mono is absent. TMNF
  # needs the native components; treat .NET as unavailable for this setup.
  WINEDLLOVERRIDES='mscoree=' run_wine "${extract_dir}/DXSETUP.exe" /silent

  # Restore Wine-owned system DLLs which DXSETUP replaces with native copies.
  # DirectX-only side-by-side components and COM registrations remain present.
  WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update

  repair_runtime_dlls

  printf '%s\n' "$DIRECTX_SHA256" >"$DIRECTX_MARKER"
  rm -rf "$extract_dir"
  directx_is_current || die "DirectX runtime installation failed."
  say "Installed DirectX End-User Runtimes $DIRECTX_VERSION"
)

repair_runtime_dlls() {
  local wine_root arch dll source
  wine_root="$(dirname "$(dirname "$(wine_binary)")")"
  for arch in i386 x86_64; do
    for dll in d3dx9_30 d3dx9_43; do
      source="${wine_root}/lib/wine/${arch}-windows/${dll}.dll"
      test -f "$source" || die "The runtime is missing $source"
      if test "$arch" = i386; then
        cp "$source" "${MACTMNF_PREFIX}/drive_c/windows/syswow64/${dll}.dll"
      else
        cp "$source" "${MACTMNF_PREFIX}/drive_c/windows/system32/${dll}.dll"
      fi
    done
  done
}

install_game() {
  local installer_pid
  local elapsed=0
  local shutdown_wait=0
  local max_wait="${MACTMNF_INSTALL_TIMEOUT:-900}"
  local server_bin
  local installer_log="${MACTMNF_PREFIX}/drive_c/mactmnf-install.log"

  if test -f "$GAME_EXE" && test "${MACTMNF_REINSTALL:-0}" != "1" \
    && { test ! -f "$installer_log" || grep -Fq 'Installation process succeeded.' "$installer_log"; }; then
    if verify_file "$GAME_EXE" "$TMNF_EXE_SHA256"; then
      say "TMNF $TMNF_GAME_VERSION is already installed at $GAME_DIR"
    else
      say "TMNF is already installed at $GAME_DIR"
      warn "The executable differs from the official $TMNF_GAME_VERSION build; run ./bin/mactmnf update to replace it."
    fi
    return
  fi

  download_verified "$TMNF_URL" "$INSTALLER_PATH" "$TMNF_SHA256"
  say "Installing TrackMania Nations Forever"
  rm -f "$installer_log"
  run_wine "$INSTALLER_PATH" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- '/LOG=C:\mactmnf-install.log' &
  installer_pid=$!

  while kill -0 "$installer_pid" 2>/dev/null; do
    # This old Inno Setup can spin in process teardown under WoW64/Rosetta.
    # Only stop it after its own log confirms success and deinitialization.
    if test -f "$installer_log" \
      && grep -Fq 'Installation process succeeded.' "$installer_log" \
      && grep -Fq 'Deinitializing Setup.' "$installer_log"; then
      shutdown_wait=$((shutdown_wait + 2))
      if test "$shutdown_wait" -ge 10; then
        say 'Game installation completed; stopping the legacy installer after its shutdown wait.'
        server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
        without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" "$server_bin" -k || true
        wait "$installer_pid" 2>/dev/null || true
        break
      fi
    fi
    if test "$elapsed" -ge "$max_wait"; then
      if test -f "$installer_log" && grep -Fq 'Installation process succeeded.' "$installer_log"; then
        warn "The installer completed its file installation but did not exit after ${max_wait}s; stopping this prefix."
        server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
        without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" "$server_bin" -k || true
        wait "$installer_pid" 2>/dev/null || true
        break
      fi
      server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
      without_operation_lock env WINEPREFIX="$MACTMNF_PREFIX" "$server_bin" -k || true
      die "TMNF installation timed out. See $installer_log; rerun make install to retry."
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  wait "$installer_pid" 2>/dev/null || true
  grep -Fq 'Installation process succeeded.' "$installer_log" || die "Game installation did not complete. See $installer_log."
  test -f "$GAME_EXE" || die "The installer exited without creating $GAME_EXE"
  verify_file "$GAME_EXE" "$TMNF_EXE_SHA256" || die "The installed TmForever.exe does not match official version $TMNF_GAME_VERSION."
  say "Installed TMNF $TMNF_GAME_VERSION at $GAME_DIR"
}

configure_competition_patch() {
  local product_dir
  local profile_dir

  product_dir="${TMLOADER_DIR}/database/TmForever/products/TmForever"
  profile_dir="${TMLOADER_DIR}/database/TmForever/profiles"
  mkdir -p "$product_dir" "$profile_dir"
  printf '%s\n' 'install: "C:/Program Files (x86)/TmNationsForever"' >"${product_dir}/settings.yaml"
  printf '%s\n' \
    'program:' \
    '  id: TmForever' \
    "  version: ${TMLOADER_GAME_VERSION}" \
    'mods:' \
    '  - id: Competition Patch' \
    '  - id: CoreMod' >"${profile_dir}/competition.yaml"
}

install_competition_patch() (
  local extract_dir entry cleanup

  if competition_patch_is_current; then
    configure_competition_patch
    say "Competition Patch $TMCOMP_VERSION is already installed"
    return
  fi

  download_verified "$TMLOADER_URL" "$TMLOADER_ARCHIVE" "$TMLOADER_SHA256"
  is_safe_managed_path "$TMLOADER_DIR" || die "Unsafe ModLoader path: $TMLOADER_DIR"

  while IFS= read -r entry; do
    case "$entry" in
      /*|../*|*/../*|*/..) die "Unsafe path in ModLoader archive: $entry" ;;
    esac
  done < <(unzip -Z1 "$TMLOADER_ARCHIVE")

  extract_dir="$(mktemp -d "${MACTMNF_CACHE}/tmloader-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  unzip -q "$TMLOADER_ARCHIVE" -d "$extract_dir"
  verify_file "${extract_dir}/TMLoader.exe" "$TMLOADER_EXE_SHA256" \
    || die "ModLoader executable verification failed."
  verify_file "${extract_dir}/database/TmForever/products/Competition Patch/${TMCOMP_VERSION}/TMCompPatch.dll" "$TMCOMP_DLL_SHA256" \
    || die "Competition Patch verification failed."
  verify_file "${extract_dir}/database/TmForever/products/TmForever/${TMLOADER_GAME_VERSION}/TmForever.exe" "$TMLOADER_GAME_SHA256" \
    || die "ModLoader compatibility runtime verification failed."
  verify_file "${extract_dir}/database/TmForever/products/CoreMod/${TMLOADER_COREMOD_VERSION}/CoreMod.dll" "$TMLOADER_COREMOD_SHA256" \
    || die "CoreMod verification failed."

  rm -rf "$TMLOADER_DIR"
  mkdir -p "$(dirname "$TMLOADER_DIR")"
  mv "$extract_dir" "$TMLOADER_DIR"

  configure_competition_patch

  competition_patch_is_current || die "Competition Patch installation failed."
  say "Installed Competition Patch $TMCOMP_VERSION with ModLoader bundle $TMLOADER_BUNDLE_VERSION"
)

install_openal() (
  local extract_dir entry cleanup current

  current="$(game_openal)"
  case "$current" in
    missing)
      warn "The game has no OpenAL32.dll; skipping the OpenAL step. Reinstall the game to restore it."
      return ;;
    other)
      warn "Leaving $GAME_OPENAL unchanged: it is neither the game's Creative OpenAL nor OpenAL Soft $OPENAL_SOFT_VERSION"
      return ;;
  esac
  if test "$MACTMNF_OPENAL" = creative; then
    if test "$current" = soft; then
      verify_file "$GAME_OPENAL_CREATIVE" "$CREATIVE_OPENAL_SHA256" \
        || die "Cannot restore Creative OpenAL: $GAME_OPENAL_CREATIVE is missing. Reinstall the game with update."
      cp "$GAME_OPENAL_CREATIVE" "$GAME_OPENAL"
      rm -f "$GAME_OPENAL_COPYING"
      say "Restored the game's Creative OpenAL"
    else
      say "Using the game's Creative OpenAL"
    fi
    printf 'creative\n' >"$OPENAL_MARKER"
    return
  fi

  if test "$current" = soft; then
    printf 'soft\n' >"$OPENAL_MARKER"
    say "OpenAL Soft $OPENAL_SOFT_VERSION is already installed"
    return
  fi

  download_verified "$OPENAL_SOFT_URL" "$OPENAL_SOFT_ARCHIVE" "$OPENAL_SOFT_SHA256"
  while IFS= read -r entry; do
    case "$entry" in
      /*|../*|*/../*|*/..) die "Unsafe path in OpenAL Soft archive: $entry" ;;
    esac
  done < <(unzip -Z1 "$OPENAL_SOFT_ARCHIVE")

  extract_dir="$(mktemp -d "${MACTMNF_CACHE}/openal-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  unzip -q "$OPENAL_SOFT_ARCHIVE" "openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" \
    "openal-soft-${OPENAL_SOFT_VERSION}-bin/COPYING" -d "$extract_dir"
  verify_file "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" "$OPENAL_SOFT_DLL_SHA256" \
    || die "OpenAL Soft library verification failed."

  # Keep the original, so MACTMNF_OPENAL=creative restores it without a game reinstall.
  cp "$GAME_OPENAL" "$GAME_OPENAL_CREATIVE"
  cp "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" "$GAME_OPENAL"
  cp "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/COPYING" "$GAME_OPENAL_COPYING"

  openal_soft_is_installed || die "OpenAL Soft installation failed."
  printf 'soft\n' >"$OPENAL_MARKER"
  say "Installed OpenAL Soft $OPENAL_SOFT_VERSION as the game's OpenAL32.dll (MACTMNF_OPENAL=creative restores the original)"
)

main() {
  case "${1:-}" in
    ''|directx) test "$#" -le 1 || die "Usage: install.sh [directx]" ;;
    -h|--help) say 'Usage: install.sh [directx]'; return ;;
    *) die "Unknown installation command: $1" ;;
  esac
  validate_launch_options
  validate_managed_layout || die "Unsafe installation layout."
  python3 "${SCRIPT_DIR}/lib/managed_paths.py" new-prefix "$MACTMNF_PREFIX" \
    || die "Choose an empty prefix or a managed TMNF installation."
  guard_prefix_operation "${SCRIPT_DIR}/install.sh" "$@"
  require_macos
  install_rosetta
  install_wine_runtime
  export MACTMNF_D9VK_DLL="${MACTMNF_RUNTIME}/lib/d9vk/d3d9.dll"
  export MACTMNF_SIDECAR="${MACTMNF_RUNTIME}/bin/x87sidecar"
  initialize_prefix

  if test "${1:-}" = "directx"; then
    install_directx_runtime
    return
  fi

  install_game
  install_directx_runtime
  install_competition_patch
  install_openal
  "${SCRIPT_DIR}/bin/mactmnf" d9vk
  "${SCRIPT_DIR}/bin/mactmnf" app
  "${SCRIPT_DIR}/bin/mactmnf" doctor
  say "Run ./bin/mactmnf play to start the game."
}

if test "${BASH_SOURCE[0]}" = "$0"; then
  main "$@"
fi
