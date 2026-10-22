#!/bin/bash
# Official TrackMania game installers. Sourced by install.sh after common.sh.
#
# Two shapes exist:
#  - Direct Inno Setup installers (TMNF): downloaded, then run silently.
#  - 7z-SFX wrappers around Inno Setup (TMU Forever, ManiaPlanet trial): the
#    outer .exe only unpacks an inner Inno Setup launcher plus its data blob,
#    and the SFX config does not forward switches to the inner setup. The two
#    payload files are unpacked with bsdtar first, then the inner setup runs
#    silently with an explicit /DIR so prefix paths stay predictable.
#
# Every installer path pins its SHA-256; every Inno Setup invocation logs to
# drive_c/tmnf-install.log so the completion watcher can verify on its own.

# _cache_installer_path TITLE — resolved path of the pinned installer download.
_cache_installer_path() {
  local title="$1" url
  if test "$title" = tmnf; then
    # Keep the historical single-title cache location so existing downloads
    # stay valid.
    # shellcheck disable=SC2153
    printf '%s\n' "$INSTALLER_PATH"
  else
    url="$(tmnf_title_field "$title" source_url)"
    if test -z "$url"; then
      die "Title $title has no pinned installer URL in config/titles."
    fi
    printf '%s/games/%s/%s\n' "$TMNF_CACHE" "$title" "$(basename "$url")"
  fi
}

# wait_inno_setup PID LOG LABEL: watch a running Inno Setup in the prefix.
# The legacy Inno Setup versions used by every title can spin during teardown
# under WoW64/Rosetta. Only stop them once their own log documents a
# successful installation and the deinitialization step made it safe.
wait_inno_setup() {
  local installer_pid="$1" installer_log="$2" label="$3"
  local elapsed=0 shutdown_wait=0 max_wait="${TMNF_INSTALL_TIMEOUT:-900}"
  local server_bin

  while kill -0 "$installer_pid" 2>/dev/null; do
    if test -f "$installer_log" \
      && grep -Fq 'Installation process succeeded.' "$installer_log" \
      && grep -Fq 'Deinitializing Setup.' "$installer_log"; then
      shutdown_wait=$((shutdown_wait + 2))
      if test "$shutdown_wait" -ge 10; then
        say "$label installation completed; stopping the legacy installer after its shutdown wait."
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
# background and remember the watcher PID in TMNF_INSTALLER_PID.
# TMNF_INNO_DIR optionally pins the Windows install directory for wrappers
# whose default directory is computed at runtime.
run_released_installer() {
  local installer="$1" label="$2" flags=()
  flags=(/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-)
  flags+=('/LOG=C:\tmnf-install.log')
  if test -n "${TMNF_INNO_DIR:-}"; then
    flags+=("/DIR=$TMNF_INNO_DIR")
  fi
  say "Installing $label"
  run_wine "$installer" "${flags[@]}" "${@:3}" &
  # The Wine invocation runs inside a backgrounded function; the PID watches
  # the whole pipeline, exactly like the installer process did before.
  TMNF_INSTALLER_PID=$!
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

download_pinned_installer() {
  local title="$1" url expected
  url="$(tmnf_title_field "$title" source_url)"
  expected="$(tmnf_title_field "$title" source_sha256)"
  test -n "$url" && test -n "$expected" \
    || die "Title $title has no pinned installer URL and checksum in config/titles."
  download_verified "$url" "$(_cache_installer_path "$title")" "$expected"
}

verify_installed_game() {
  local exe="$1" expected="$2" label="$3"
  test -f "$exe" || die "$label installation finished without creating $exe"
  if test -n "$expected" && ! verify_file "$exe" "$expected"; then
    die "The installed $label executable does not match the pinned official release."
  fi
}

install_game() {
  local title_id="$TMNF_TITLE" installer_path installer_log
  local kind
  kind="$(tmnf_title_field "$title_id" installer)"

  case "$kind" in
    online) ;;
    online-retail|online-trial) install_pinned_inno_game "$kind" ; return ;;
    *) die "Title $title_id has no automatic game installer (kind: $kind); use the hub import flow." ;;
  esac

  installer_path="$(_cache_installer_path "$title_id")"
  installer_log="${TMNF_PREFIX}/drive_c/tmnf-install.log"

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

  download_verified "$TMNF_GAME_URL" "$installer_path" "$TMNF_GAME_SHA256"
  rm -f "$installer_log"
  run_released_installer "$installer_path" "TrackMania Nations Forever"
  wait_inno_setup "$TMNF_INSTALLER_PID" "$installer_log" "TrackMania Nations Forever"
  verify_installed_game "$GAME_EXE" "$TMNF_GAME_EXE_SHA256" "TMNF"
  say "Installed TMNF $TMNF_GAME_VERSION at $GAME_DIR"
}

install_pinned_inno_game() {
  local kind="$1" title_id="$TMNF_TITLE"
  local title_name installer_path installer_log inner

  title_name="$(tmnf_title_field "$title_id" name)"
  installer_log="${TMNF_PREFIX}/drive_c/tmnf-install.log"

  if test -f "$GAME_EXE" && test "${TMNF_REINSTALL:-0}" != "1" \
    && { test ! -f "$installer_log" || grep -Fq 'Installation process succeeded.' "$installer_log"; }; then
    say "$title_name is already installed at $GAME_DIR"
    return
  fi

  download_pinned_installer "$title_id"
  installer_path="$(_cache_installer_path "$title_id")"
  inner="$(unpack_inner_inno "$installer_path")"
  rm -f "$installer_log"
  TMNF_INNO_DIR="C:\Program Files (x86)\${_title_game_dir}" \
    run_released_installer "$inner" "$title_name"
  wait_inno_setup "$TMNF_INSTALLER_PID" "$installer_log" "$title_name"
  verify_installed_game "$GAME_EXE" "" "$title_name"
  say "Installed $title_name at $GAME_DIR"
}
