#!/bin/bash
# Official TrackMania game installers. Sourced by install.sh after common.sh.
#
# Two shapes exist:
#  - Direct Inno Setup installers (TMNF, ESWC): downloaded, then run with an
#    explicit /DIR. Silently, unless the registry marks the setup interactive
#    (source_interactive): then the user clicks through its wizard. A setup
#    with source_patch runs from a modified copy next to the download
#    (trackmania_mac.install.setup_patch), removed when the setup ends. Pinned
#    official updates may follow, run silently, and then an overlay archive
#    unpacked over the game folder (trackmania_mac.install.overlay), and
#    the NX_COMPAT flag set on the registry's nx_compat files
#    (trackmania_mac.install.pe_flags), the game executable patched
#    (trackmania_mac.install.exe_patch), then the registry's default settings
#    file written into the game folder when none exists
#    (trackmania_mac.install.system_config); apply_title_layers runs these
#    steps, and import runs them after a retail setup too. Each finished update is recorded in
#    drive_c/trackmania-mac-updates.done and not run again, because an official
#    updater refuses to run twice; an install that only lacks the NX_COMPAT
#    flag or the patch gets just the steps after the overlay.
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
  say "The $label setup opens in its own window. Click through its pages and keep the suggested folder. The remaining files are installed after setup closes."
  run_wine "$installer" "${flags[@]}" &
  TRACKMANIA_MAC_INSTALLER_PID=$!
}

# write_patched_setup INSTALLER STAGING — the source-patch component: write the
# modified copy of the pinned setup into STAGING, a fresh *.inner folder that
# remove_inno_staging deletes, and print its path. The download is only read.
write_patched_setup() {
  local installer="$1" staging="$2" copy
  copy="${staging}/${installer##*/}"
  rm -rf -- "$staging"
  mkdir -p "$staging" || die "Cannot stage the modified setup."
  TRACKMANIA_MAC_TITLE="$TRACKMANIA_MAC_TITLE" TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" \
    trackmania_mac_python -m trackmania_mac.install.setup_patch apply "$installer" "$copy" \
    || die "Could not prepare the $TRACKMANIA_MAC_TITLE_NAME setup. Run install again to download it again."
  printf '%s\n' "$copy"
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

# track_pack_wanted — the user asked for the title's track pack
# (TRACKMANIA_MAC_TRACK_PACK=on; the hub passes the choice from its prompt) and
# the saves folder does not already record it (trackmania_mac.install.track_pack).
track_pack_wanted() {
  test "${TRACKMANIA_MAC_TRACK_PACK:-off}" = on || return 1
  title_has track-pack || return 1
  ! TRACKMANIA_MAC_TITLE="$TRACKMANIA_MAC_TITLE" TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" \
    trackmania_mac_python -m trackmania_mac.install.track_pack installed "$TRACKMANIA_MAC_USERDATA"
}

# download_track_pack — fetch and verify the title's pinned track pack.
download_track_pack() {
  download_verified "$TRACKMANIA_MAC_TITLE_TRACK_PACK_URL" \
    "$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$TRACKMANIA_MAC_TITLE_TRACK_PACK_URL")" \
    "$TRACKMANIA_MAC_TITLE_TRACK_PACK_SHA256" "$TRACKMANIA_MAC_TITLE_TRACK_PACK_SIZE"
}

# apply_track_pack — unpack the downloaded track pack into the saves folder the
# game folder links to. Run after sync_game_save_links; never overwrites a file.
apply_track_pack() {
  say "Adding the community tracks"
  TRACKMANIA_MAC_TITLE="$TRACKMANIA_MAC_TITLE" TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" \
    trackmania_mac_python -m trackmania_mac.install.track_pack apply \
    "$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$TRACKMANIA_MAC_TITLE_TRACK_PACK_URL")" \
    "$GAME_DIR" "$TRACKMANIA_MAC_USERDATA"
}

# apply_official_updates — run each pinned update installer silently, in order.
# An update that finished is recorded by its SHA-256 in trackmania-mac-updates.done
# and skipped next time: an official updater refuses to run twice.
apply_official_updates() {
  local index count="${TRACKMANIA_MAC_TITLE_UPDATE_COUNT:-0}" path label sha
  local installer_log="${TRACKMANIA_MAC_PREFIX}/drive_c/trackmania-mac-install.log"
  local done_log="${TRACKMANIA_MAC_PREFIX}/drive_c/trackmania-mac-updates.done"
  for ((index = 1; index <= count; index++)); do
    path="$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$(_title_update_field "$index" URL)")"
    label="$TRACKMANIA_MAC_TITLE_NAME update ${path##*/}"
    sha="$(_title_update_field "$index" SHA256)"
    if test -f "$done_log" && grep -Fxq "$sha" "$done_log"; then
      say "The $label is already installed"
      continue
    fi
    # The download was verified before the setup, which may have taken an hour.
    verify_file "$path" "$sha" \
      || die "The downloaded $label no longer matches its pinned checksum. Run install again to download it again."
    rm -f "$installer_log"
    TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
      run_released_installer "$path" "$label"
    wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$label"
    printf '%s\n' "$sha" >>"$done_log"
  done
}

# apply_overlay — unpack the pinned overlay over the game folder, remove the
# files it makes obsolete, and write the game executable last.
apply_overlay() {
  local archive options=() name names=()
  archive="$(_cache_download_path "$TRACKMANIA_MAC_TITLE" "$TRACKMANIA_MAC_TITLE_OVERLAY_URL")"
  # The registry allows only plain file names in remove and skip paths, so
  # splitting on spaces is exact. The root is one value and may hold spaces.
  read -r -a names <<<"${TRACKMANIA_MAC_TITLE_OVERLAY_REMOVE:-}"
  for name in ${names[@]+"${names[@]}"}; do
    options+=(--remove "$name")
  done
  read -r -a names <<<"${TRACKMANIA_MAC_TITLE_OVERLAY_SKIP:-}"
  for name in ${names[@]+"${names[@]}"}; do
    options+=(--skip "$name")
  done
  if test -n "${TRACKMANIA_MAC_TITLE_OVERLAY_ROOT:-}"; then
    options+=(--root "$TRACKMANIA_MAC_TITLE_OVERLAY_ROOT")
  fi
  say "Adding the $TRACKMANIA_MAC_TITLE_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION game files"
  trackmania_mac_python -m trackmania_mac.install.overlay apply "$archive" \
    "$TRACKMANIA_MAC_TITLE_OVERLAY_SHA256" "$GAME_DIR" --last "$TRACKMANIA_MAC_TITLE_GAME_EXE" \
    ${options[@]+"${options[@]}"} \
    || die "Could not add the $TRACKMANIA_MAC_TITLE_NAME game files."
}

# apply_nx_compat — set the NX_COMPAT flag on the registry's nx_compat files, so
# Wine does not treat the game as needing executable memory everywhere.
apply_nx_compat() {
  local names=()
  # The registry allows only plain file names here, so splitting on spaces is exact.
  read -r -a names <<<"${TRACKMANIA_MAC_TITLE_NX_COMPAT:-}"
  say "Marking the game files as compatible with memory protection"
  trackmania_mac_python -m trackmania_mac.install.pe_flags nx-compat "$GAME_DIR" \
    ${names[@]+"${names[@]}"} \
    || die "Could not update the $TRACKMANIA_MAC_TITLE_NAME game files."
}

# apply_game_exe_patch — the game-exe-patch component: patch the game executable
# with the registry's game_exe_patch (trackmania_mac.install.exe_patch).
apply_game_exe_patch() {
  say "Adding the faster particle code to the game"
  TRACKMANIA_MAC_TITLE="$TRACKMANIA_MAC_TITLE" TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" \
    trackmania_mac_python -m trackmania_mac.install.exe_patch apply "$GAME_DIR" \
    || die "Could not patch the $TRACKMANIA_MAC_TITLE_NAME game executable."
}

# seed_game_system_config — the game-system-config component: write the
# registry's default settings file into the game folder unless one exists
# (trackmania_mac.install.system_config). Not fatal: without it the game starts
# with its own defaults.
seed_game_system_config() {
  title_has game-system-config || return 0
  TRACKMANIA_MAC_TITLE="$TRACKMANIA_MAC_TITLE" TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" \
    trackmania_mac_python -m trackmania_mac.install.system_config seed "$GAME_DIR" \
    "${TRACKMANIA_MAC_PROJECT_DIR}/config/game-settings.json" \
    || warn "Could not write the default settings for $TRACKMANIA_MAC_TITLE_NAME; the game starts with its own."
}

verify_installed_game() {
  local exe="$1" expected="$2" label="$3"
  test -f "$exe" || die "$label installation finished without creating $exe"
  if test -n "$expected" && ! verify_file "$exe" "$expected"; then
    die "The installed $label executable does not match the pinned official release."
  fi
}

# title_is_layered — true when steps follow the title's base setup: pinned
# updates, an overlay or the NX_COMPAT flag. Each is safe to repeat, so an
# install or import that stopped after its setup finishes without it.
title_is_layered() {
  title_has updates || title_has overlay || title_has nx-compat
}

# finish_after_overlay — the steps after the overlay (the NX_COMPAT flag, the
# executable patch, the default settings file), then the pinned-executable check.
# The flag step runs whenever the title has it: an earlier run may have stopped
# after the executable but before the other files, and files that have the flag
# are left as they are.
finish_after_overlay() {
  if title_has nx-compat; then
    apply_nx_compat
  fi
  if title_has game-exe-patch; then
    apply_game_exe_patch
  fi
  seed_game_system_config
  verify_installed_game "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256" "$TRACKMANIA_MAC_TITLE_SHORT_NAME"
}

# apply_title_layers — every step after the base setup: the pinned updates not
# yet recorded, the overlay, then finish_after_overlay. The downloads must be
# verified first (download_title_extras).
apply_title_layers() {
  if title_has updates; then
    apply_official_updates
  fi
  if title_has overlay; then
    apply_overlay
  fi
  finish_after_overlay
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
    elif game_exe_finish_pending; then
      # The executable is the pinned build without the NX_COMPAT flag or the
      # patch (an install from before those steps, or one that stopped between
      # them), so the steps after the overlay finish it. The flag step runs
      # either way: it may have stopped after the executable but before the
      # other files, and it leaves files that have the flag as they are.
      say "Finishing the $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION installation at $GAME_DIR"
      finish_after_overlay
      say "Installed $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION at $GAME_DIR"
    elif title_is_layered; then
      # A layered install stopped after its setup: repeat the steps after it,
      # which are safe to repeat and need no clicks, instead of the setup.
      say "Finishing the $TRACKMANIA_MAC_TITLE_SHORT_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION installation at $GAME_DIR"
      download_title_extras
      apply_title_layers
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
  # The subshell's EXIT trap removes a modified copy of the setup however the
  # subshell ends; the trap does not outlive it.
  (
    setup_path="$installer_path"
    if title_has source-patch; then
      staging="${installer_path%.exe}.inner"
      printf -v cleanup 'remove_inno_staging %q' "$staging"
      # shellcheck disable=SC2064
      trap "$cleanup" EXIT
      setup_path="$(write_patched_setup "$installer_path" "$staging")" || exit "$?"
    fi
    # Pin /DIR so the title installs where GAME_DIR points, whatever the
    # installer's own default directory is.
    if test "${TRACKMANIA_MAC_TITLE_SOURCE_INTERACTIVE:-}" = true; then
      TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
        run_interactive_installer "$setup_path" "$TRACKMANIA_MAC_TITLE_NAME"
      # A person drives this setup, so it gets longer than a silent one.
      wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$TRACKMANIA_MAC_TITLE_NAME" \
        "${TRACKMANIA_MAC_SETUP_WIZARD_TIMEOUT:-3600}"
      # Stops anything started from the setup's last page, should it offer one.
      stop_prefix_processes
    else
      TRACKMANIA_MAC_INNO_DIR="C:\\Program Files (x86)\\${TRACKMANIA_MAC_TITLE_GAME_DIR_NAME}" \
        run_released_installer "$setup_path" "$TRACKMANIA_MAC_TITLE_NAME"
      wait_inno_setup "$TRACKMANIA_MAC_INSTALLER_PID" "$installer_log" "$TRACKMANIA_MAC_TITLE_NAME"
    fi
  ) || exit "$?"
  if test "${TRACKMANIA_MAC_TITLE_SOURCE_INTERACTIVE:-}" = true; then
    test -f "$GAME_EXE" \
      || die "$TRACKMANIA_MAC_TITLE_NAME setup closed without installing into $GAME_DIR. Run install again and keep the suggested folder."
  fi
  apply_title_layers
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
