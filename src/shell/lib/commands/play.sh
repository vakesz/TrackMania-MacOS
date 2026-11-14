#!/bin/bash
# Game launching and renderer setup. Sourced by trackmania-mac after common.sh.

# trackmania_mac.session.supervisor's exit status after it redirected the
# settings launcher's game start (REDIRECTED there).
readonly SESSION_REDIRECTED=3
# Switches TmForeverLauncher.exe passes to TmForever.exe for its benchmark and
# benchmark list. Those runs stay in the settings session instead of becoming Play.
readonly LAUNCHER_BENCHMARK_SWITCHES=(/bench /list_benchs)
# Set once this shell has started the diagnostics monitor. It follows this
# shell's PID, which a redirected settings session keeps into Play.
SESSION_MONITOR_STARTED=''

play_target() {
  local executable="$1"
  shift
  local watch="$1"
  shift
  local wine_bin
  local overrides
  local renderer_vars=()
  local helper
  local redirect=false
  local switch

  validate_launch_options
  require_owned_prefix || die "A managed Wine prefix is required."
  test -f "$executable" || die "Required executable is missing. Run src/shell/cli/install.sh first: $executable"
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run src/shell/cli/install.sh first."
  helper="$(session_helper "$wine_bin")"
  case "$TRACKMANIA_MAC_RENDERER" in
    d9vk)
      overrides='d3d9=n'
      # TMNF's startup DirectDraw probe needs no 3D adapter. Its unused OpenGL
      # context raced 32-bit Rosetta startup; keep it separate from D9VK.
      renderer_vars=(
        "DXVK_CONFIG_FILE=${DXVK_CONFIG_FILE:-$DXVK_CONF}"
        "DXVK_STATE_CACHE_PATH=${DXVK_STATE_CACHE_PATH:-$TRACKMANIA_MAC_PREFIX}"
        "DXVK_LOG_LEVEL=${DXVK_LOG_LEVEL:-warn}"
        "DXVK_LOG_PATH=${DXVK_LOG_PATH:-none}"
        "WINE_D3D_CONFIG=renderer=gdi${WINE_D3D_CONFIG:+,$WINE_D3D_CONFIG}")
      ;;
    wined3d-vulkan)
      overrides='d3d9=b'
      renderer_vars=("WINE_D3D_CONFIG=renderer=vulkan${WINE_D3D_CONFIG:+,$WINE_D3D_CONFIG}")
      ;;
    wined3d-gl)
      overrides='d3d9=b'
      renderer_vars=("WINE_D3D_CONFIG=renderer=gl${WINE_D3D_CONFIG:+,$WINE_D3D_CONFIG}")
      ;;
  esac
  # CoreMod redirects D3DX30 compilation into D3DX43, and d3dx9_43 loads
  # d3dx9_31 for the legacy-compiler flag. DXSETUP installs a native d3dx9_31,
  # so override the whole set to the restored Wine copies, not just 30 and 43.
  local d3dx9_overrides
  d3dx9_overrides="$(IFS=,; printf '%s' "${RUNTIME_D3DX9_DLLS[*]}")"
  overrides="${overrides};${d3dx9_overrides},wined3d=b"
  if test -n "${WINEDLLOVERRIDES:-}"; then
    overrides="${overrides};${WINEDLLOVERRIDES}"
  fi
  if test "$TRACKMANIA_MAC_RENDERER" = "d9vk" && ! d9vk_is_installed; then
    die "D9VK is not installed in the prefix. Run src/shell/cli/trackmania-mac d9vk (or set TRACKMANIA_MAC_RENDERER=wined3d-vulkan)."
  fi
  configure_translation
  local session_args=(--watch "$watch" --audio "$TRACKMANIA_MAC_AUDIO")
  if test "$executable" = "$LAUNCHER_EXE"; then
    # Settings' Play/benchmark child remains part of the same session even if
    # the settings process exits. Watch exact paths inside this prefix.
    session_args+=(--watch "$GAME_EXE" --settings)
    if title_has competition-patch; then
      # The launcher's Play starts the plain game. The helper stops that
      # start and this shell runs Play instead, so it gets the Competition
      # Patch and CoreMod as the Game app does (configure_game).
      redirect=true
      session_args+=(--redirect "$GAME_EXE")
      for switch in "${LAUNCHER_BENCHMARK_SWITCHES[@]}"; do
        session_args+=(--redirect-except "$switch")
      done
    fi
  fi
  if test "$TRACKMANIA_MAC_WINDOW_SIZE" != fullscreen; then
    session_args+=(--desktop "$TRACKMANIA_MAC_WINDOW_SIZE")
  fi
  local debug="${WINEDEBUG:--all}"
  # Python runs from a neutral folder: `python -m` puts its working directory
  # first on sys.path, and the game folder is writable by the game and by
  # third-party files laid over it. Only the Wine child starts in the game
  # folder (the supervisor's --cwd), which becomes its Windows current folder.
  cd / || die "Cannot leave the current folder."
  if test "$TRACKMANIA_MAC_DIAG" = on && test -z "$SESSION_MONITOR_STARTED"; then
    # Wine's frame rate and timestamps, read with trackmania_mac.session.monitor's lines by
    # trackmania_mac.session.report. The monitor follows this process ID, which the
    # session supervisor keeps after exec.
    debug="${WINEDEBUG:--all,+fps,+timestamp}"
    say "trackmania-mac-run: version=$TRACKMANIA_MAC_VERSION title=$TRACKMANIA_MAC_TITLE macos=$(sw_vers -productVersion 2>/dev/null) model=$(sysctl -n hw.model 2>/dev/null) x87=$TRACKMANIA_MAC_X87 audio=$TRACKMANIA_MAC_AUDIO renderer=$TRACKMANIA_MAC_RENDERER target=${watch##*/}"
    without_operation_lock trackmania_mac_python -m trackmania_mac.session.monitor --parent "$$" --prefix "$TRACKMANIA_MAC_PREFIX" \
      --wine "$wine_bin" --cache "$TRACKMANIA_MAC_CACHE" --game-exe "$TRACKMANIA_MAC_TITLE_GAME_EXE" &
    SESSION_MONITOR_STARTED=true
  fi
  test -d "$GAME_DIR" || die "Cannot enter game directory: $GAME_DIR"
  local supervisor=(/usr/bin/env
    WINEPREFIX="$TRACKMANIA_MAC_PREFIX"
    WINEDEBUG="$debug"
    WINEDLLOVERRIDES="$overrides"
    "${renderer_vars[@]}"
    PYTHONPATH="$TRACKMANIA_MAC_PYTHONPATH"
    "$TRACKMANIA_MAC_PYTHON" -m trackmania_mac.session.supervisor run
    --prefix "$TRACKMANIA_MAC_PREFIX" --wine "$wine_bin" --helper "$helper" --cwd "$GAME_DIR"
    "${session_args[@]}" -- "$executable" "$@")
  if test "$redirect" = true; then
    # This shell keeps the operation descriptor while the supervisor runs, so
    # the prefix stays locked until Play takes over. The supervisor and Wine
    # still close it for their own children.
    "${supervisor[@]}"
    return
  fi
  # Keep the operation descriptor in the host supervisor. Its child processes
  # close inherited descriptors, so neither Wine nor a detached server owns it.
  exec "${supervisor[@]}"
}

install_d9vk() {
  local wine_bin

  require_owned_prefix || die "A managed Wine prefix is required."
  test -f "$TRACKMANIA_MAC_D9VK_DLL" || die "D9VK is missing. Run make install (expected $TRACKMANIA_MAC_D9VK_DLL)."
  test -d "${TRACKMANIA_MAC_PREFIX}/drive_c/windows/syswow64" || die "The Wine prefix is missing. Run src/shell/cli/install.sh first."
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run src/shell/cli/install.sh first."
  cp "$TRACKMANIA_MAC_D9VK_DLL" "$PREFIX_D9VK_DLL"
  # Register the override in the prefix too, so tools launched outside
  # trackmania-mac (winecfg, the graphics launcher) see the same Direct3D 9.
  without_operation_lock env WINEPREFIX="$TRACKMANIA_MAC_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
    'HKCU\Software\Wine\DllOverrides' /v d3d9 /t REG_SZ /d native /f >/dev/null
  d9vk_is_installed || die "D9VK installation failed."
  say "Installed D9VK into $PREFIX_D9VK_DLL"
}

# Game arguments come from `trackmania-mac play`; configure_game passes none.
# shellcheck disable=SC2120
play_game() {
  if ! title_has competition-patch; then
    play_vanilla "$@"
    return
  fi
  competition_patch_is_ready || die "Competition Patch is missing, outdated, or unconfigured. Run src/shell/cli/install.sh."
  play_target "$TMLOADER_EXE" "$TMLOADER_GAME_EXE" run "$TMLOADER_PROGRAM_ID" "$TMLOADER_PROFILE" "$@"
}

play_vanilla() {
  play_target "$GAME_EXE" "$GAME_EXE" "$@"
}

configure_game() {
  local status=0
  # The host supervisor applies launcher-only GPU defaults under the same
  # startup deadline as audio setup and process creation. play_target returns
  # only when the launcher's game start can be redirected to Play.
  play_target "$LAUNCHER_EXE" "$LAUNCHER_EXE" || status=$?
  test "$status" -eq "$SESSION_REDIRECTED" || exit "$status"
  say "The settings launcher started the game; starting it as Play does."
  play_game
}
