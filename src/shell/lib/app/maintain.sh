#!/bin/bash
# Installation maintenance: update, doctor, stop, uninstall.
# Sourced by tmnf after common.sh.

update_game() {
  local kind label
  kind="$(tmnf_title_field "$TMNF_TITLE" installer)"
  label="$(tmnf_title_field "$TMNF_TITLE" name)"

  case "$kind" in
    online-retail|online-trial)
      say "Re-running the pinned $label installer (repair)."
      exec "${TMNF_PROJECT_DIR}/src/shell/cli/install.sh"
      ;;
    steam-copy|disk-image)
      die "$label has no official update channel here; provide the game files again via the hub import flow."
      ;;
  esac

  local needs_patch=false
  if test "$(tmnf_title_field "$TMNF_TITLE" needs_tmloader)" = true; then
    needs_patch=true
  fi
  if verify_file "$GAME_EXE" "$TMNF_GAME_EXE_SHA256" \
    && { test "$needs_patch" = false || competition_patch_is_ready; } \
    && directx_is_current; then
    if test "$needs_patch" = true; then
      say "$label $TMNF_GAME_VERSION, DirectX $DIRECTX_VERSION, and Competition Patch $TMCOMP_VERSION are current."
    else
      say "$label $TMNF_GAME_VERSION and DirectX $DIRECTX_VERSION are current."
    fi
    return
  fi

  if verify_file "$GAME_EXE" "$TMNF_GAME_EXE_SHA256"; then
    exec "${TMNF_PROJECT_DIR}/src/shell/cli/install.sh"
  fi
  say "Installing the official $label $TMNF_GAME_VERSION release and Competition Patch."
  TMNF_REINSTALL=1 exec "${TMNF_PROJECT_DIR}/src/shell/cli/install.sh"
}

doctor() {
  local failures=0
  local wine_bin=""
  local wine_version=""
  local wow64_version=""
  local sidecar="${ROSETTA_X87_PATH:-$TMNF_SIDECAR}"
  local needs_patch title_name title_short game_expected_sha

  validate_launch_options
  title_name="$(tmnf_title_field "$TMNF_TITLE" name)"
  title_short="$(tmnf_title_field "$TMNF_TITLE" short_name)"
  game_expected_sha="${TMNF_GAME_EXE_SHA256:-}"
  needs_patch="$(tmnf_title_field "$TMNF_TITLE" needs_tmloader)"

  if test -f "${TMNF_PREFIX}/system.reg" && require_owned_prefix >/dev/null 2>&1; then
    if tmnf_python -m tmnf.guard.operation_guard held "$TMNF_PREFIX" >/dev/null 2>&1; then
      # A diagnostic Windows process must not race prefix repair or deletion,
      # and an already-busy prefix must degrade instead of dying cryptically.
      guard_prefix_operation "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" doctor
    else
      say "A session owns the prefix right now; deep prefix checks are skipped."
      TMNF_DOCTOR_SESSION_ACTIVE=1
    fi
  fi

  say "TMNF $TMNF_VERSION"
  say "Title: $TMNF_TITLE ($title_name)"
  say "macOS: $(sw_vers -productVersion 2>/dev/null || echo unknown) ($(uname -m))"
  say "Audio on next launch: $TMNF_AUDIO (TMNF_AUDIO=off disables sound)"
  say "Patched x87 translation: $TMNF_X87"
  if test "$TMNF_X87" = on; then
    if test -x "$sidecar" && "$sidecar" --probe >/dev/null 2>&1; then
      say "x87sidecar: compatible with the installed Rosetta"
    else
      warn "Patched x87sidecar is missing or incompatible with Rosetta; rebuild or use TMNF_X87=off"
      failures=$((failures + 1))
    fi
  fi
  if rosetta_is_ready; then
    say "Rosetta 2: ready"
  else
    warn "Rosetta 2 is missing"
    failures=$((failures + 1))
  fi

  if wine_bin="$(wine_binary)" && tmnf_python -m tmnf.install.runtime_manifest verify \
      "$(dirname "$(dirname "$wine_bin")")" "$RUNTIME_VERSION"; then
    if wine_version="$(without_operation_lock "$wine_bin" --version 2>/dev/null)" && test -n "$wine_version"; then
      say "Wine: $wine_version"
    else
      warn "Wine failed its version probe"
      failures=$((failures + 1))
    fi
    say "Wine binary: $wine_bin"
  if test "${TMNF_DOCTOR_SESSION_ACTIVE:-}" = 1; then
    : # Wine still reports its version; only prefix probes are skipped below.
  elif ! require_owned_prefix || test ! -f "${TMNF_PREFIX}/system.reg"; then
    warn "WoW64 probe skipped: an initialized managed prefix is required"
    failures=$((failures + 1))
  elif wow64_version="$(without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all "$wine_bin" \
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

  if test "$needs_patch" = true; then
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

  say "Renderer: $TMNF_RENDERER"
  if test "$TMNF_RENDERER" = "d9vk"; then
    if d9vk_is_installed; then
      say "D9VK: installed in the prefix ($(sha256_file "$PREFIX_D9VK_DLL" | cut -c1-12))"
    elif test -f "$TMNF_D9VK_DLL"; then
      warn "D9VK is built but not installed in the prefix; run src/shell/cli/tmnf d9vk"
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
  tmnf_python -m tmnf.session.session stop --prefix "$TMNF_PREFIX" --wine "$wine_bin"
  say "Stopped the TMNF Wine prefix."
}

uninstall_game() {
  local answer=""
  validate_managed_layout || die "Unsafe installation layout; userdata must be outside the prefix."
  tmnf_python -m tmnf.guard.managed_paths uninstall "$TMNF_PREFIX" "$TMNF_USERDATA" \
    || die "Refusing removal until the reported user-data locations are safe."
  if test "${1:-}" != "--yes"; then
    printf 'Remove the TMNF prefix at %s? [y/N] ' "$TMNF_PREFIX"
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES) ;;
      *) say "Cancelled."; return 0 ;;
    esac
  fi
  rm -rf "$TMNF_PREFIX"
  say "Removed $TMNF_PREFIX"
  say "The cached installers and private Wine runtime were kept."
}
