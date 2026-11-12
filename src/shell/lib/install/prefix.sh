#!/bin/bash
# Wine prefix lifecycle. Sourced by install.sh after common.sh.

# Seed the runtime's default graphics settings into the folder this title's game
# reads (its UserSubDir). An existing file or link is the user's and stays.
# The file is in the Forever engine's format, so only titles with the
# system-config component get it.
seed_system_config() {
  local config_dir
  title_has system-config || return 0
  test -n "$TMNF_TITLE_USER_SUBDIR" || die "The title registry has no user_subdir."
  config_dir="${TMNF_USERDATA}/${TMNF_TITLE_USER_SUBDIR}/Config"
  mkdir -p "$config_dir"
  if test ! -e "${config_dir}/Default.SystemConfig.Gbx" \
    && test ! -L "${config_dir}/Default.SystemConfig.Gbx"; then
    cp "${TMNF_RUNTIME}/config/Default.SystemConfig.Gbx" "${config_dir}/"
  fi
}

initialize_prefix() {
  local wine_bin
  local wineboot_bin

  validate_managed_layout || die "Unsafe installation layout."
  tmnf_python -m tmnf.guard.managed_paths new-prefix "$TMNF_PREFIX" \
    || die "Choose an empty prefix or a managed TMNF installation."
  mkdir -p "$TMNF_PREFIX"
  if test -f "${TMNF_PREFIX}/system.reg"; then
    test -f "${TMNF_PREFIX}/.tmnf-prefix" || die "This prefix is not managed by TMNF. Choose a new TMNF_PREFIX."
    if test ! -f "${TMNF_PREFIX}/.tmnf-initializing"; then
      WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update
      # Repair seeds only when the game's Documents is this UserData; a legacy
      # Documents folder inside the prefix is left alone.
      if title_has system-config \
        && tmnf_python -m tmnf.guard.managed_paths documents-linked "$TMNF_PREFIX" "$TMNF_USERDATA" 2>/dev/null; then
        seed_system_config
      fi
      say "Prefix is up to date; nothing to repair."
      return
    fi
  fi

  say "Initializing the Wine prefix"
  wine_bin="$(wine_binary)" || die "Wine is not installed."
  wineboot_bin="$(wineboot_binary "$wine_bin")" || die "Cannot locate wineboot."
  touch "${TMNF_PREFIX}/.tmnf-initializing" || die "Cannot mark prefix initialization."
  if test ! -f "${TMNF_PREFIX}/.tmnf-prefix"; then
    if ! printf '%s\n' "$TMNF_TITLE_PREFIX_MARKER" >"${TMNF_PREFIX}/.tmnf-prefix"; then
      rm -f "${TMNF_PREFIX}/.tmnf-prefix" "${TMNF_PREFIX}/.tmnf-initializing"
      die "Cannot mark prefix ownership."
    fi
  fi
  tmnf_python -m tmnf.guard.managed_paths profile-baseline "$TMNF_PREFIX" \
    || die "Cannot record pre-Wineboot profiles; initialization remains incomplete."
  # TMNF is native code. Skip optional Mono/Gecko registration so prefix setup
  # does not stop at download dialogs for unrelated .NET or browser runtimes.
  without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES='mscoree,mshtml=' "$wineboot_bin" --init \
    || die "Wine prefix initialization failed; rerun setup to retry."
  # Redirect fresh Wine default links without touching host data. Preserve older
  # profile arrangements and require explicit backup/repair when seeding is unsafe.
  tmnf_python -m tmnf.guard.managed_paths setup-documents "$TMNF_PREFIX" "$TMNF_USERDATA" \
    || die "Documents setup is incomplete; no external settings were seeded."
  seed_system_config
  rm -f "${TMNF_PREFIX}/.tmnf-profile-baseline" "${TMNF_PREFIX}/.tmnf-initializing"
}
