#!/bin/bash
# Installation maintenance: update, doctor, stop, uninstall.
# Sourced by trackmania-mac after common.sh.

update_game() {
  local kind="$TRACKMANIA_MAC_TITLE_INSTALLER" label="$TRACKMANIA_MAC_TITLE_NAME"

  case "$kind" in
    online-retail|online-trial)
      say "Checking the platform and components for $label and repairing what is missing; the installed game files are kept."
      exec "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/install.sh"
      ;;
    steam-copy|disk-image)
      die "$label has no official update channel here; provide the game files again via the hub import flow."
      ;;
  esac

  local needs_patch=false
  if title_has competition-patch; then
    needs_patch=true
  fi
  if test -f "$GAME_EXE"; then
    ensure_title_icon || warn "Could not extract the game icon for the hub."
  fi
  if verify_file "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256" \
    && { test "$needs_patch" = false || competition_patch_is_ready; } \
    && directx_is_current && openal_is_current; then
    if test "$needs_patch" = true; then
      say "$label $TRACKMANIA_MAC_TITLE_GAME_VERSION, DirectX $DIRECTX_VERSION, Competition Patch $TMCOMP_VERSION and OpenAL are current."
    else
      say "$label $TRACKMANIA_MAC_TITLE_GAME_VERSION and DirectX $DIRECTX_VERSION are current."
    fi
    return
  fi

  if verify_file "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256"; then
    exec "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/install.sh"
  fi
  say "Installing the official $label $TRACKMANIA_MAC_TITLE_GAME_VERSION release and Competition Patch."
  TRACKMANIA_MAC_REINSTALL=1 exec "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/install.sh"
}

doctor() {
  local failures=0
  local wine_bin=""
  local wine_version=""
  local wow64_version=""
  local sidecar="${ROSETTA_X87_PATH:-$TRACKMANIA_MAC_SIDECAR}"
  local title_name title_short game_expected_sha

  validate_launch_options
  title_name="$TRACKMANIA_MAC_TITLE_NAME"
  title_short="$TRACKMANIA_MAC_TITLE_SHORT_NAME"
  # Titles without a registry exe pin get presence coverage instead.
  game_expected_sha="$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256"

  if test -f "${TRACKMANIA_MAC_PREFIX}/system.reg" && require_owned_prefix >/dev/null 2>&1; then
    if test "${TRACKMANIA_MAC_OPERATION_FD+x}" = x; then
      # Inherited lock: validates it or dies, then runs the deep checks.
      guard_prefix_operation "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor
    else
      # Direct run: probe by taking and releasing the lock. Only genuine lock
      # contention degrades; any other failure is reported as itself.
      validate_managed_layout || die "Unsafe installation layout."
      local busy_probe
      if busy_probe="$(TRACKMANIA_MAC_LOCK_WAIT=0 trackmania_mac_python -m trackmania_mac.guard.operation_guard run "$TRACKMANIA_MAC_PREFIX" /usr/bin/true 2>&1)"; then
        # A diagnostic Windows process must not race prefix repair or deletion.
        guard_prefix_operation "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor
      elif case "$busy_probe" in *"Another operation is using prefix"*|*"Wine is active for prefix"*) true ;; *) false ;; esac; then
        say "A session owns the prefix right now; deep prefix checks are skipped."
        TRACKMANIA_MAC_DOCTOR_SESSION_ACTIVE=1
      else
        die "Cannot check the prefix: ${busy_probe#error: }"
      fi
    fi
  fi

  say "TrackMania Mac $TRACKMANIA_MAC_VERSION"
  say "Title: $TRACKMANIA_MAC_TITLE ($title_name)"
  say "macOS: $(sw_vers -productVersion 2>/dev/null || echo unknown) ($(uname -m))"
  say "Audio on next launch: $TRACKMANIA_MAC_AUDIO (TRACKMANIA_MAC_AUDIO=off disables sound)"
  if title_has openal && test -f "$GAME_EXE"; then
    case "$(game_openal)" in
      soft) say "OpenAL: OpenAL Soft $OPENAL_SOFT_VERSION" ;;
      creative) say "OpenAL: the game's Creative OpenAL" ;;
      other) say "OpenAL: a replaced OpenAL32.dll ($(sha256_file "$GAME_OPENAL" | cut -c1-12))" ;;
      missing) warn "OpenAL32.dll is missing from the game directory; run update" ;;
    esac
    openal_is_current || warn "TRACKMANIA_MAC_OPENAL=$TRACKMANIA_MAC_OPENAL is not applied yet; run make install"
  fi
  say "Patched x87 translation: $TRACKMANIA_MAC_X87"
  if test "$TRACKMANIA_MAC_X87" = on; then
    if ! test -x "$sidecar"; then
      warn "Patched x87sidecar is missing; run make install or use TRACKMANIA_MAC_X87=off"
      failures=$((failures + 1))
    elif "$sidecar" --probe >/dev/null 2>&1; then
      say "x87sidecar: compatible with the installed Rosetta"
    else
      # Not a failure: the game still runs, unhooked.
      warn "$TRACKMANIA_MAC_X87_UNSUPPORTED_HINT"
    fi
  fi
  if rosetta_is_ready; then
    say "Rosetta 2: ready"
  else
    warn "Rosetta 2 is missing"
    failures=$((failures + 1))
  fi

  if wine_bin="$(wine_binary)" && trackmania_mac_python -m trackmania_mac.install.runtime_manifest verify \
      "$(dirname "$(dirname "$wine_bin")")" "$RUNTIME_VERSION"; then
    if wine_version="$(without_operation_lock "$wine_bin" --version 2>/dev/null)" && test -n "$wine_version"; then
      say "Wine: $wine_version"
    else
      warn "Wine failed its version probe"
      failures=$((failures + 1))
    fi
    say "Wine binary: $wine_bin"
  if test "${TRACKMANIA_MAC_DOCTOR_SESSION_ACTIVE:-}" = 1; then
    : # Wine still reports its version; only prefix probes are skipped below.
  elif ! require_owned_prefix || test ! -f "${TRACKMANIA_MAC_PREFIX}/system.reg"; then
    warn "WoW64 probe skipped: an initialized managed prefix is required"
    failures=$((failures + 1))
  elif wow64_version="$(without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG=-all "$wine_bin" \
    'C:\windows\syswow64\cmd.exe' /c ver 2>/dev/null)"; then
    wow64_version="$(printf '%s' "$wow64_version" | tr -d '\r' | tail -1)"
    say "WoW64: ${wow64_version:-ready}"
  else
    warn "Wine could not execute a 32-bit Windows command in this prefix"
    failures=$((failures + 1))
  fi
  else
    warn "Wine runtime is missing, incomplete, modified, or a different version; build and install the current runtime."
    failures=$((failures + 1))
  fi

  if test -f "$GAME_EXE"; then
    if test -n "$game_expected_sha"; then
      if verify_file "$GAME_EXE" "$game_expected_sha"; then
        say "$title_short: verified official release"
      else
        warn "$title_short: installed executable is not the pinned official build; run update"
      fi
    else
      # Retail and trial installs carry only presence coverage until a hash
      # pin lands in the registry for their exact media.
      say "$title_short: installed (hash pin not available for this title)"
    fi
    say "Game: $GAME_EXE"
  else
    warn "$title_short is not installed at $GAME_EXE"
    failures=$((failures + 1))
  fi

  if title_has competition-patch; then
    if competition_patch_is_ready; then
      say "Competition Patch: $TMCOMP_VERSION"
      say "ModLoader bundle: $TMLOADER_BUNDLE_VERSION"
      say "ModLoader game runtime: $TMLOADER_GAME_VERSION"
    else
      warn "Competition Patch is missing or does not match the pinned release"
      failures=$((failures + 1))
    fi
  fi

  if directx_is_current; then
    say "DirectX End-User Runtimes: $DIRECTX_VERSION"
  else
    warn "DirectX End-User Runtimes $DIRECTX_VERSION are missing; run directx"
    failures=$((failures + 1))
  fi

  say "Renderer: $TRACKMANIA_MAC_RENDERER"
  if test "$TRACKMANIA_MAC_RENDERER" = "d9vk"; then
    if d9vk_is_installed; then
      say "D9VK: installed in the prefix ($(sha256_file "$PREFIX_D9VK_DLL" | cut -c1-12))"
    elif test -f "$TRACKMANIA_MAC_D9VK_DLL"; then
      warn "D9VK is built but not installed in the prefix; run src/shell/cli/trackmania-mac d9vk"
      failures=$((failures + 1))
    else
      warn "D9VK is missing; run make install"
      failures=$((failures + 1))
    fi
  fi

  if test "$failures" -ne 0; then
    die "$failures check(s) failed"
  fi
  say "Result: ready to play"
}

stop_game() {
  local wine_bin
  wine_bin="$(wine_binary)" || die "Wine is not installed."
  require_owned_prefix || die "Refusing to stop an unmanaged prefix."
  trackmania_mac_python -m trackmania_mac.session.supervisor stop --prefix "$TRACKMANIA_MAC_PREFIX" --wine "$wine_bin"
  say "Stopped the Wine prefix of $TRACKMANIA_MAC_TITLE_NAME."
}

uninstall_game() {
  local answer="" option status=0 assume_yes=false
  local -a plan_options=() locations
  for option in "$@"; do
    case "$option" in
      --yes) assume_yes=true ;;
      --include-saves) plan_options=(--include-saves) ;;
      *) die "Unknown uninstall option: $option (use --yes and --include-saves)" ;;
    esac
  done
  # The app wrappers are removed under the same app lock install_app writes them with.
  if test "${TRACKMANIA_MAC_APP_OPERATION_FD+x}" != x; then
    trackmania_mac_python -m trackmania_mac.guard.operation_guard app-run "$TRACKMANIA_MAC_PREFIX" \
      /bin/bash "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" uninstall "$@"
    return $?
  fi
  trackmania_mac_python -m trackmania_mac.guard.operation_guard app-held "$TRACKMANIA_MAC_PREFIX" \
    || die "Invalid app operation lock."
  validate_managed_layout || die "Unsafe installation layout; userdata must be outside the prefix."
  locations=("$TRACKMANIA_MAC_PREFIX" "$TRACKMANIA_MAC_USERDATA" "$TRACKMANIA_MAC_APP" "$TRACKMANIA_MAC_SETTINGS_APP" "$TRACKMANIA_MAC_LAUNCHER" \
    "$TRACKMANIA_MAC_SUPPORT" "$TRACKMANIA_MAC_CACHE")
  # The guard plans and removes in one process; this shell only asks and never
  # reads the plan back, so nothing in a path or an app can become a command.
  if test "$assume_yes" != true; then
    trackmania_mac_python -m trackmania_mac.guard.uninstall plan ${plan_options[@]+"${plan_options[@]}"} "${locations[@]}" \
      || status=$?
    case "$status" in
      0) ;;
      3) return 0 ;;
      *) die "Refusing removal until the reported locations are safe." ;;
    esac
    printf 'Remove these files from %s? [y/N] ' "$TRACKMANIA_MAC_TITLE_NAME"
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES) ;;
      *) say "Cancelled."; return 0 ;;
    esac
  fi
  trackmania_mac_python -m trackmania_mac.guard.uninstall remove ${plan_options[@]+"${plan_options[@]}"} "${locations[@]}" \
    || die "The uninstall did not finish. Run it again to remove what is left."
  say "The Wine runtime and downloads shared with other games were kept."
}
