#!/bin/bash
# Official TrackMania game installers. Sourced by install.sh after common.sh.
#
# Two shapes exist:
#  - Direct Inno Setup installers (TMNF, ESWC): downloaded, then run with an
#    explicit /DIR. Silently, unless the registry marks the setup interactive
#    (source_interactive): then the user clicks through its wizard. Pinned
#    official updates may follow, run silently, and then an overlay archive
#    unpacked over the game folder (trackmania_mac.install.overlay).
#  - 7z-SFX wrappers around Inno Setup (TMU Forever, ManiaPlanet trial): the
#    outer .exe only unpacks an inner Inno Setup launcher plus its data blob,
#    and the SFX config does not forward switches to the inner setup. The two
#    payload files are unpacked with bsdtar first, then the inner setup runs
#    silently with an explicit /DIR so prefix paths stay predictable.
#
# Every installer path pins its SHA-256; every Inno Setup invocation logs to
# drive_c/trackmania-mac-install.log so the completion watcher can verify on its own.

# _installer_descendants PID — print every descendant of PID, deepest first.
_installer_descendants() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do
    _installer_descendants "$child"
    printf '%s\n' "$child"
  done
}

# stop_installer_tree PID — after `wineserver -k`, give the installer a short
# grace period to exit, then SIGKILL it and its descendants. A Wine client
# (start.exe) has been seen ignoring both the wineserver shutdown and SIGTERM,
# which left a bare `wait` blocked forever. Only the tree below PID is touched.
stop_installer_tree() {
  local pid="$1" grace="${TRACKMANIA_MAC_INSTALL_KILL_GRACE:-15}" waited=0 victim victims
  while kill -0 "$pid" 2>/dev/null && test "$waited" -lt "$grace"; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "Stopping the installer processes that did not exit after Wine shut down."
    victims="$(_installer_descendants "$pid")"
    for victim in $victims "$pid"; do
      kill -KILL "$victim" 2>/dev/null || true
    done
  fi
  wait "$pid" 2>/dev/null || true
}

# reap_prefix_leftovers — after `wineserver -k` and stop_installer_tree, stop
# Wine processes (services.exe, explorer.exe, ...) that were reparented to
# launchd and still hold the dead server's directory open. Setup steps only.
reap_prefix_leftovers() {
  without_operation_lock trackmania_mac_python -m trackmania_mac.guard.operation_guard reap "$TRACKMANIA_MAC_PREFIX" || true
}

# _kill_prefix_server — ask this prefix's wineserver to end every Wine process.
_kill_prefix_server() {
  local server_bin
  server_bin="$(wineserver_binary "$(wine_binary)")" || die "Cannot locate wineserver."
  without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" "$server_bin" -k || true
}

# stop_prefix_wine PID — shut down this prefix's wineserver, then stop the
# installer tree below PID (still running, not yet reaped) and the Wine
# processes left behind.
stop_prefix_wine() {
  _kill_prefix_server
  stop_installer_tree "$1"
  reap_prefix_leftovers
}

# stop_prefix_processes — the same for the prefix only, once the installer has
# been reaped: its PID may already belong to another process.
stop_prefix_processes() {
  _kill_prefix_server
  reap_prefix_leftovers
}

# wait_inno_setup PID LOG LABEL [MAX_WAIT]: watch a running Inno Setup in the
# prefix. The legacy Inno Setup versions used by every title can spin during
# teardown under WoW64/Rosetta. Only stop them once their own log documents
# the end of Setup (deinitialization): after a successful installation the
# watcher continues, after a cancelled or failed one it reports that.
# MAX_WAIT defaults to TRACKMANIA_MAC_INSTALL_TIMEOUT (900 seconds).
wait_inno_setup() {
  local installer_pid="$1" installer_log="$2" label="$3"
  local elapsed=0 shutdown_wait=0 max_wait="${4:-${TRACKMANIA_MAC_INSTALL_TIMEOUT:-900}}"

  while kill -0 "$installer_pid" 2>/dev/null; do
    if test -f "$installer_log" && grep -Fq 'Deinitializing Setup.' "$installer_log"; then
      shutdown_wait=$((shutdown_wait + 2))
      if test "$shutdown_wait" -ge 10; then
        if ! grep -Fq 'Installation process succeeded.' "$installer_log"; then
          stop_prefix_wine "$installer_pid"
          die "$label setup was cancelled or stopped early. See $installer_log."
        fi
        say "$label installation completed; stopping the legacy installer after its shutdown wait."
        stop_prefix_wine "$installer_pid"
        break
      fi
    fi
    if test "$elapsed" -ge "$max_wait"; then
      if test -f "$installer_log" && grep -Fq 'Installation process succeeded.' "$installer_log"; then
        warn "The installer completed its file installation but did not exit after ${max_wait}s; stopping this prefix."
        stop_prefix_wine "$installer_pid"
        break
      fi
      stop_prefix_wine "$installer_pid"
      die "$label installation timed out. See $installer_log; rerun make install to retry."
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  wait "$installer_pid" 2>/dev/null || true
  grep -Fq 'Installation process succeeded.' "$installer_log" \
    || die "$label installation did not complete. See $installer_log."
}

# run_released_installer PATH LABEL — start one silent Inno Setup run in the
# background and remember the watcher PID in TRACKMANIA_MAC_INSTALLER_PID.
# TRACKMANIA_MAC_INNO_DIR optionally pins the Windows install directory for wrappers
# whose default directory is computed at runtime.
run_released_installer() {
  local installer="$1" label="$2" flags=()
  flags=(/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-)
  flags+=('/LOG=C:\trackmania-mac-install.log')
  if test -n "${TRACKMANIA_MAC_INNO_DIR:-}"; then
    flags+=("/DIR=$TRACKMANIA_MAC_INNO_DIR")
  fi
  say "Installing $label"
  run_wine "$installer" "${flags[@]}" "${@:3}" &
  # The Wine invocation runs inside a backgrounded function; the PID watches
  # the whole pipeline, exactly like the installer process did before.
  TRACKMANIA_MAC_INSTALLER_PID=$!
}

# run_interactive_installer PATH LABEL — start an Inno Setup the user clicks
# through, in the background, like run_released_installer: no silent switches,
# the same log, and /DIR from TRACKMANIA_MAC_INNO_DIR as the suggested folder.
run_interactive_installer() {
  local installer="$1" label="$2" flags=()
  flags=(/SP- /NORESTART '/LOG=C:\trackmania-mac-install.log')
  if test -n "${TRACKMANIA_MAC_INNO_DIR:-}"; then
    flags+=("/DIR=$TRACKMANIA_MAC_INNO_DIR")
  fi
  say "The $label setup opens in its own window. Click through its pages and keep the suggested folder. On the last page, don't start the game; the remaining files are installed after setup closes."
  run_wine "$installer" "${flags[@]}" &
  TRACKMANIA_MAC_INSTALLER_PID=$!
}

# unpack_inner_inno INSTALLER — prints the path of the extracted Inno Setup
# launcher next to its data blob. bsdtar reads the store-method 7z payload
# Nadeo used for TMU Forever and the ManiaPlanet trial.
unpack_inner_inno() {
  local installer="$1" staging entries count exe_name

  entries="$(bsdtar -tf "$installer")" || die "Not a supported installer bundle: $installer"
  count="$(printf '%s\n' "$entries" | grep -cE '\.exe$|\.bin$')"
  test "$count" = 2 \
    || die "Unexpected installer layout: expected one Inno Setup launcher plus its data blob, saw:
$entries"
  exe_name="$(printf '%s\n' "$entries" | grep -E '\.exe$' | head -n 1)"
  test -n "$exe_name" || die "No Inno Setup launcher inside the installer bundle."

  staging="${installer%.exe}.inner"
  rm -rf -- "$staging"
  mkdir -p "$staging" || die "Cannot stage the installer contents."
  bsdtar -xf "$installer" -C "$staging" || die "Cannot unpack the installer bundle."
  test -f "$staging/$exe_name" || die "The extracted setup launcher is missing: $exe_name"
  printf '%s\n' "$staging/$exe_name"
}

# remove_inno_staging DIR — delete the unpacked inner installer (about 1.2 GB for TMU)
# once Setup no longer needs it. Only `*.inner` directories under a managed root.
remove_inno_staging() {
  local dir="$1"
  case "$dir" in
    *.inner) ;;
    *) return 0 ;;
  esac
  test -d "$dir" || return 0
  if is_safe_managed_path "$dir" 2>/dev/null; then
    rm -rf -- "$dir"
  else
    warn "Leaving the unpacked installer in place (unsafe path): $dir"
  fi
}

download_pinned_installer() {
  local title="$1"
  test -n "$TRACKMANIA_MAC_TITLE_SOURCE_URL" && test -n "$TRACKMANIA_MAC_TITLE_SOURCE_SHA256" \
    || die "Title $title has no pinned installer URL and checksum in config/titles."
  # The registry pins the installer size (empty when unknown); cap the download at it.
  download_verified "$TRACKMANIA_MAC_TITLE_SOURCE_URL" "$(_cache_installer_path "$title")" \
    "$TRACKMANIA_MAC_TITLE_SOURCE_SHA256" "${TRACKMANIA_MAC_TITLE_SOURCE_SIZE:-}"
}

# _title_update_field INDEX FIELD — one exported value of update INDEX (URL, SHA256, SIZE).
_title_update_field() {
  local name="TRACKMANIA_MAC_TITLE_UPDATE_${1}_${2}"
  printf '%s\n' "${!name}"
}

# download_title_extras — fetch and verify the title's pinned updates and
# overlay before any setup runs, so a failed download costs no clicks.
download_title_extras() {
  local index count="${TRACKMANIA_MAC_TITLE_UPDATE_COUNT:-0}" url
  for ((index = 1; index <= count; index++)); do
    url="$(_title_update_field "$index" URL)"
    download_verified "$url" "$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$url")" \
      "$(_title_update_field "$index" SHA256)" "$(_title_update_field "$index" SIZE)"
  done
  if title_has overlay; then
    download_verified "$TRACKMANIA_MAC_TITLE_OVERLAY_URL" \
      "$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$TRACKMANIA_MAC_TITLE_OVERLAY_URL")" \
      "$TRACKMANIA_MAC_TITLE_OVERLAY_SHA256" "$TRACKMANIA_MAC_TITLE_OVERLAY_SIZE"
  fi
}

# apply_official_updates — run each pinned update installer silently, in order.
apply_official_updates() {
  local index count="${TRACKMANIA_MAC_TITLE_UPDATE_COUNT:-0}" path label
  local installer_log="${TRACKMANIA_MAC_PREFIX}/drive_c/trackmania-mac-install.log"
  for ((index = 1; index <= count; index++)); do
    path="$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$(_title_update_field "$index" URL)")"
    label="$TRACKMANIA_MAC_TITLE_NAME update ${path##*/}"
    # The download was verified before the setup, which may have taken an hour.
    verify_file "$path" "$(_title_update_field "$index" SHA256)" \
      || die "The downloaded $label no longer matches its pinned checksum. Run install again to download it again."
    rm -f "$installer_log"
    TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
      run_released_installer "$path" "$label"
    wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$label"
  done
}

# apply_overlay — unpack the pinned overlay over the game folder, remove the
# files it makes obsolete, and write the game executable last.
apply_overlay() {
  local archive remove=() name names=()
  archive="$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$TRACKMANIA_MAC_TITLE_OVERLAY_URL")"
  # The registry allows only plain file names here, so splitting on spaces is exact.
  read -r -a names <<<"${TRACKMANIA_MAC_TITLE_OVERLAY_REMOVE:-}"
  for name in ${names[@]+"${names[@]}"}; do
    remove+=(--remove "$name")
  done
  say "Adding the $TRACKMANIA_MAC_TITLE_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION game files"
  trackmania_mac_python -m trackmania_mac.install.overlay apply "$archive" \
    "$TRACKMANIA_MAC_TITLE_OVERLAY_SHA256" "$GAME_DIR" --last "$TRACKMANIA_MAC_TITLE_GAME_EXE" \
    ${remove[@]+"${remove[@]}"} \
    || die "Could not add the $TRACKMANIA_MAC_TITLE_NAME game files."
}

verify_installed_game() {
  local exe="$1" expected="$2" label="$3"
  test -f "$exe" || die "$label installation finished without creating $exe"
  if test -n "$expected" && ! verify_file "$exe" "$expected"; then
    die "The installed $label executable does not match the pinned official release."
  fi
}

install_game() {
  local title_id="$TRACKMANIA_MAC_TITLE" installer_path installer_log
  local kind="$TRACKMANIA_MAC_TITLE_INSTALLER"

  case "$kind" in
    online) ;;
    online-retail|online-trial) install_pinned_inno_game "$kind" ; return ;;
    *) die "Title $title_id has no automatic game installer (kind: $kind); use the hub import flow." ;;
  esac

  installer_path="$(_cache_installer_path "$title_id")"
  installer_log="${TRACKMANIA_MAC_PREFIX}/drive_c/trackmania-mac-install.log"

  if test -f "$GAME_EXE" && test "${TRACKMANIA_MAC_REINSTALL:-0}" != "1" \
    && { test ! -f "$installer_log" || grep -Fq 'Installation process succeeded.' "$installer_log"; }; then
    if verify_file "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256"; then
      say "$TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION is already installed at $GAME_DIR"
    elif title_has updates || title_has overlay; then
      # A layered install stopped after its setup: repeat the steps after it,
      # which are safe to repeat and need no clicks, instead of the setup.
      say "Finishing the $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION installation at $GAME_DIR"
      download_title_extras
      if title_has updates; then
        apply_official_updates
      fi
      if title_has overlay; then
        apply_overlay
      fi
      verify_installed_game "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256" "$TRACKMANIA_MAC_TITLE_SHORT_NAME"
      say "Installed $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION at $GAME_DIR"
    else
      say "$TRACKMANIA_MAC_TITLE_SHORT_NAME is already installed at $GAME_DIR"
      warn "The executable differs from the pinned $TRACKMANIA_MAC_TITLE_GAME_VERSION build; run src/shell/cli/trackmania-mac update to replace it."
    fi
    return
  fi

  download_pinned_installer "$title_id"
  download_title_extras
  rm -f "$installer_log"
  # Pin /DIR so the title installs where GAME_DIR points, whatever the
  # installer's own default directory is.
  if test "${TRACKMANIA_MAC_TITLE_SOURCE_INTERACTIVE:-}" = true; then
    TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
      run_interactive_installer "$installer_path" "$TRACKMANIA_MAC_TITLE_NAME"
    # A person drives this setup, so it gets longer than a silent one.
    wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$TRACKMANIA_MAC_TITLE_NAME" \
      "${TRACKMANIA_MAC_SETUP_WIZARD_TIMEOUT:-3600}"
    # Stops anything started from the setup's last page, such as the game.
    stop_prefix_processes
    test -f "$GAME_EXE" \
      || die "$TRACKMANIA_MAC_TITLE_NAME setup closed without installing into $GAME_DIR. Run install again and keep the suggested folder."
  else
    TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
      run_released_installer "$installer_path" "$TRACKMANIA_MAC_TITLE_NAME"
    wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$TRACKMANIA_MAC_TITLE_NAME"
  fi
  if title_has updates; then
    apply_official_updates
  fi
  if title_has overlay; then
    apply_overlay
  fi
  verify_installed_game "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256" "$TRACKMANIA_MAC_TITLE_SHORT_NAME"
  say "Installed $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION at $GAME_DIR"
}

install_pinned_inno_game() {
  local kind="$1" title_id="$TRACKMANIA_MAC_TITLE"
  local title_name installer_path installer_log inner cleanup

  title_name="$TRACKMANIA_MAC_TITLE_NAME"
  installer_log="${TRACKMANIA_MAC_PREFIX}/drive_c/trackmania-mac-install.log"

  if test -f "$GAME_EXE" && test "${TRACKMANIA_MAC_REINSTALL:-0}" != "1" \
    && { test ! -f "$installer_log" || grep -Fq 'Installation process succeeded.' "$installer_log"; }; then
    say "$title_name is already installed at $GAME_DIR"
    return
  fi

  download_pinned_installer "$title_id"
  installer_path="$(_cache_installer_path "$title_id")"
  inner="$(unpack_inner_inno "$installer_path")"
  rm -f "$installer_log"
  # Inside double quotes "\$" is a literal dollar sign, so the separator
  # before the expansion must be an escaped backslash.
  # The subshell's EXIT trap removes the unpacked installer when die exits it; the trap
  # does not outlive the subshell.
  printf -v cleanup 'remove_inno_staging %q' "$(dirname "$inner")"
  (
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
      run_released_installer "$inner" "$title_name"
    wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$title_name"
  ) || exit "$?"
  verify_installed_game "$GAME_EXE" "" "$title_name"
  say "Installed $title_name at $GAME_DIR"
}
