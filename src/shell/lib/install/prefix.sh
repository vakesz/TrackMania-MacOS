#!/bin/bash
# Wine prefix lifecycle. Sourced by install.sh after common.sh.

# Seed the runtime's default graphics settings into the folder this title's game
# reads (its UserSubDir). An existing file or link is the user's and stays.
# The file is in the Forever engine's format, so only titles with the
# system-config component get it.
seed_system_config() {
  local config_dir
  title_has system-config || return 0
  test -n "$TRACKMANIA_MAC_TITLE_USER_SUBDIR" || die "The title registry has no user_subdir."
  config_dir="${TRACKMANIA_MAC_USERDATA}/${TRACKMANIA_MAC_TITLE_USER_SUBDIR}/Config"
  mkdir -p "$config_dir"
  if test ! -e "${config_dir}/Default.SystemConfig.Gbx" \
    && test ! -L "${config_dir}/Default.SystemConfig.Gbx"; then
    cp "${TRACKMANIA_MAC_RUNTIME}/config/Default.SystemConfig.Gbx" "${config_dir}/"
  fi
}

initialize_prefix() {
  local wine_bin
  local wineboot_bin

  validate_managed_layout || die "Unsafe installation layout."
  trackmania_mac_python -m trackmania_mac.guard.managed_paths new-prefix "$TRACKMANIA_MAC_PREFIX" \
    || die "Choose an empty prefix or a managed TrackMania Mac installation."
  mkdir -p "$TRACKMANIA_MAC_PREFIX"
  if test -f "${TRACKMANIA_MAC_PREFIX}/system.reg"; then
    prefix_marker_present PREFIX_MARKER_NAME \
      || die "This prefix is not managed by TrackMania Mac. Choose a new TRACKMANIA_MAC_PREFIX."
    if ! prefix_marker_present INITIALIZING_MARKER_NAME; then
      # An earlier release's markers gain this release's names; theirs stay.
      trackmania_mac_python -m trackmania_mac.guard.managed_paths adopt-markers "$TRACKMANIA_MAC_PREFIX" \
        || die "Cannot read this prefix's ownership markers."
      WINEDLLOVERRIDES='mscoree,mshtml=' run_wine wineboot --update
      # Repair seeds only when the game's Documents is this UserData; a legacy
      # Documents folder inside the prefix is left alone.
      if title_has system-config \
        && trackmania_mac_python -m trackmania_mac.guard.managed_paths documents-linked "$TRACKMANIA_MAC_PREFIX" "$TRACKMANIA_MAC_USERDATA" 2>/dev/null; then
        seed_system_config
      fi
      say "Prefix is up to date; nothing to repair."
      return
    fi
  fi

  say "Initializing the Wine prefix"
  wine_bin="$(wine_binary)" || die "Wine is not installed."
  wineboot_bin="$(wineboot_binary "$wine_bin")" || die "Cannot locate wineboot."
  touch "${TRACKMANIA_MAC_PREFIX}/${INITIALIZING_MARKER_NAME}" || die "Cannot mark prefix initialization."
  if ! prefix_marker_present PREFIX_MARKER_NAME; then
    if ! printf '%s\n' "$TRACKMANIA_MAC_TITLE_PREFIX_MARKER" >"${TRACKMANIA_MAC_PREFIX}/${PREFIX_MARKER_NAME}"; then
      rm -f "${TRACKMANIA_MAC_PREFIX}/${PREFIX_MARKER_NAME}" "${TRACKMANIA_MAC_PREFIX}/${INITIALIZING_MARKER_NAME}"
      die "Cannot mark prefix ownership."
    fi
  else
    trackmania_mac_python -m trackmania_mac.guard.managed_paths adopt-markers "$TRACKMANIA_MAC_PREFIX" \
      || die "Cannot read this prefix's ownership markers."
  fi
  trackmania_mac_python -m trackmania_mac.guard.managed_paths profile-baseline "$TRACKMANIA_MAC_PREFIX" \
    || die "Cannot record pre-Wineboot profiles; initialization remains incomplete."
  # TMNF is native code. Skip optional Mono/Gecko registration so prefix setup
  # does not stop at download dialogs for unrelated .NET or browser runtimes.
  without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG=-all WINEDLLOVERRIDES='mscoree,mshtml=' "$wineboot_bin" --init \
    || die "Wine prefix initialization failed; rerun setup to retry."
  # Redirect fresh Wine default links without touching host data. Preserve older
  # profile arrangements and require explicit backup/repair when seeding is unsafe.
  trackmania_mac_python -m trackmania_mac.guard.managed_paths setup-documents "$TRACKMANIA_MAC_PREFIX" "$TRACKMANIA_MAC_USERDATA" \
    || die "Documents setup is incomplete; no external settings were seeded."
  seed_system_config
  rm -f "${TRACKMANIA_MAC_PREFIX}/${PROFILE_BASELINE_NAME}" "${TRACKMANIA_MAC_PREFIX}/${LEGACY_PROFILE_BASELINE}" \
    "${TRACKMANIA_MAC_PREFIX}/${INITIALIZING_MARKER_NAME}" "${TRACKMANIA_MAC_PREFIX}/${LEGACY_INITIALIZING_MARKER}"
}
