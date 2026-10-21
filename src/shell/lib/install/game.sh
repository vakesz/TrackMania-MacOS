#!/bin/bash
# Official TMNF release download and install. Sourced by install.sh after common.sh.

install_game() {
  local installer_pid
  local elapsed=0
  local shutdown_wait=0
  local max_wait="${TMNF_INSTALL_TIMEOUT:-900}"
  local server_bin
  local installer_log="${TMNF_PREFIX}/drive_c/tmnf-install.log"

  if test -f "$GAME_EXE" && test "${TMNF_REINSTALL:-0}" != "1" \
    && { test ! -f "$installer_log" || grep -Fq 'Installation process succeeded.' "$installer_log"; }; then
    if verify_file "$GAME_EXE" "$TMNF_GAME_EXE_SHA256"; then
      say "TMNF $TMNF_GAME_VERSION is already installed at $GAME_DIR"
    else
      say "TMNF is already installed at $GAME_DIR"
      warn "The executable differs from the official $TMNF_GAME_VERSION build; run src/shell/cli/tmnf update to replace it."
    fi
    return
  fi

  download_verified "$TMNF_GAME_URL" "$INSTALLER_PATH" "$TMNF_GAME_SHA256"
  say "Installing TrackMania Nations Forever"
  rm -f "$installer_log"
  run_wine "$INSTALLER_PATH" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- '/LOG=C:\tmnf-install.log' &
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
        without_operation_lock env WINEPREFIX="$TMNF_PREFIX" "$server_bin" -k || true
        wait "$installer_pid" 2>/dev/null || true
        break
      fi
    fi
    if test "$elapsed" -ge "$max_wait"; then
      if test -f "$installer_log" && grep -Fq 'Installation process succeeded.' "$installer_log"; then
        warn "The installer completed its file installation but did not exit after ${max_wait}s; stopping this prefix."
        server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
        without_operation_lock env WINEPREFIX="$TMNF_PREFIX" "$server_bin" -k || true
        wait "$installer_pid" 2>/dev/null || true
        break
      fi
      server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
      without_operation_lock env WINEPREFIX="$TMNF_PREFIX" "$server_bin" -k || true
      die "TMNF installation timed out. See $installer_log; rerun make install to retry."
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  wait "$installer_pid" 2>/dev/null || true
  grep -Fq 'Installation process succeeded.' "$installer_log" || die "Game installation did not complete. See $installer_log."
  test -f "$GAME_EXE" || die "The installer exited without creating $GAME_EXE"
  verify_file "$GAME_EXE" "$TMNF_GAME_EXE_SHA256" || die "The installed TmForever.exe does not match official version $TMNF_GAME_VERSION."
  say "Installed TMNF $TMNF_GAME_VERSION at $GAME_DIR"
}
