#!/bin/bash
# Game launching and renderer setup. Sourced by tmnf after common.sh.

play_target() {
  local executable="$1"
  shift
  local watch="$1"
  shift
  local wine_bin
  local overrides
  local renderer_vars=()
  local helper

  validate_launch_options
  require_owned_prefix || die "A managed Wine prefix is required."
  test -f "$executable" || die "Required executable is missing. Run src/shell/cli/install.sh first: $executable"
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run src/shell/cli/install.sh first."
  helper="$(session_helper "$wine_bin")"
  case "$TMNF_RENDERER" in
    d9vk)
      overrides='d3d9=n'
      # TMNF's startup DirectDraw probe needs no 3D adapter. Its unused OpenGL
      # context raced 32-bit Rosetta startup; keep it separate from D9VK.
      renderer_vars=(
        "DXVK_CONFIG_FILE=${DXVK_CONFIG_FILE:-$DXVK_CONF}"
        "DXVK_STATE_CACHE_PATH=${DXVK_STATE_CACHE_PATH:-$TMNF_PREFIX}"
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
  # CoreMod redirects D3DX30 compilation into D3DX43. Keep both patched compilers.
  overrides="${overrides};d3dx9_30,d3dx9_43,wined3d=b"
  if test -n "${WINEDLLOVERRIDES:-}"; then
    overrides="${overrides};${WINEDLLOVERRIDES}"
  fi
  if test "$TMNF_RENDERER" = "d9vk" && ! d9vk_is_installed; then
    die "D9VK is not installed in the prefix. Run src/shell/cli/tmnf d9vk (or set TMNF_RENDERER=wined3d-vulkan)."
  fi
  configure_translation
  local session_args=(--watch "$watch" --audio "$TMNF_AUDIO")
  if test "$executable" = "$LAUNCHER_EXE"; then
    # Settings' Play/benchmark child remains part of the same session even if
    # the settings process exits. Watch exact paths inside this prefix.
    session_args+=(--watch "$GAME_EXE" --settings)
  fi
  if test "$TMNF_WINDOW_SIZE" != fullscreen; then
    session_args+=(--desktop "$TMNF_WINDOW_SIZE")
  fi
  cd "$GAME_DIR" || die "Cannot enter game directory: $GAME_DIR"
  # Keep the operation descriptor in the host supervisor. Its child processes
  # close inherited descriptors, so neither Wine nor a detached server owns it.
  exec /usr/bin/env \
    WINEPREFIX="$TMNF_PREFIX" \
    WINEDEBUG="${WINEDEBUG:--all}" \
    WINEDLLOVERRIDES="$overrides" \
    "${renderer_vars[@]}" \
    PYTHONPATH="$TMNF_PYTHONPATH" \
    "$TMNF_PYTHON" -m tmnf.session.session run \
    --prefix "$TMNF_PREFIX" --wine "$wine_bin" --helper "$helper" \
    "${session_args[@]}" -- "$executable" "$@"
}

install_d9vk() {
  local wine_bin

  require_owned_prefix || die "A managed Wine prefix is required."
  test -f "$TMNF_D9VK_DLL" || die "D9VK is missing. Run make install (expected $TMNF_D9VK_DLL)."
  test -d "${TMNF_PREFIX}/drive_c/windows/syswow64" || die "The Wine prefix is missing. Run src/shell/cli/install.sh first."
  wine_bin="$(wine_binary)" || die "Wine is not installed. Run src/shell/cli/install.sh first."
  cp "$TMNF_D9VK_DLL" "$PREFIX_D9VK_DLL"
  # Register the override in the prefix too, so tools launched outside
  # tmnf (winecfg, the graphics launcher) see the same Direct3D 9.
  without_operation_lock env WINEPREFIX="$TMNF_PREFIX" WINEDEBUG=-all "$wine_bin" reg add \
    'HKCU\Software\Wine\DllOverrides' /v d3d9 /t REG_SZ /d native /f >/dev/null
  d9vk_is_installed || die "D9VK installation failed."
  say "Installed D9VK into $PREFIX_D9VK_DLL"
}

play_game() {
  competition_patch_is_ready || die "Competition Patch is missing, outdated, or unconfigured. Run src/shell/cli/install.sh."
  play_target "$TMLOADER_EXE" "$TMLOADER_GAME_EXE" run TmForever competition "$@"
}

play_vanilla() {
  play_target "$GAME_EXE" "$GAME_EXE" "$@"
}

configure_game() {
  # The host supervisor applies launcher-only GPU defaults under the same
  # startup deadline as audio setup and process creation.
  play_target "$LAUNCHER_EXE" "$LAUNCHER_EXE"
}
