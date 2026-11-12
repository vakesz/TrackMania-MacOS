#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=src/shell/lib/common.sh
. "${SCRIPT_DIR}/../lib/common.sh"

install_rosetta() {
  if rosetta_is_ready; then
    say "Rosetta 2 is ready"
    return
  fi

  say "Installing Rosetta 2"
  /usr/sbin/softwareupdate --install-rosetta --agree-to-license
  rosetta_is_ready || die "Rosetta installation did not complete."
}

# shellcheck source=src/shell/lib/install/runtime.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/runtime.sh"
# shellcheck source=src/shell/lib/install/prefix.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/prefix.sh"
# shellcheck source=src/shell/lib/install/directx.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/directx.sh"
# shellcheck source=src/shell/lib/install/game.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/game.sh"
# shellcheck source=src/shell/lib/install/patch.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/patch.sh"
# shellcheck source=src/shell/lib/install/openal.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/openal.sh"
# shellcheck source=src/shell/lib/apps/icons.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/apps/icons.sh"

usage() {
  say 'Usage: install.sh [--runtime=auto|download|build] [directx|setup]'
}

main() {
  local runtime_flag=0
  # The flag wins over TRACKMANIA_MAC_RUNTIME_SOURCE. It reaches the guarded re-exec through the environment.
  while test "$#" -gt 0; do
    case "$1" in
      --runtime=*|--runtime)
        test "$runtime_flag" = 0 || die "Give --runtime only once."
        runtime_flag=1
        if test "$1" = --runtime; then
          test "$#" -ge 2 || die "--runtime needs auto, download or build."
          TRACKMANIA_MAC_RUNTIME_SOURCE="$2"
          shift
        else
          TRACKMANIA_MAC_RUNTIME_SOURCE="${1#--runtime=}"
        fi
        shift
        ;;
      *) break ;;
    esac
  done
  export TRACKMANIA_MAC_RUNTIME_SOURCE="${TRACKMANIA_MAC_RUNTIME_SOURCE-auto}"
  validate_runtime_source
  case "${1:-}" in
    ''|directx|setup) test "$#" -le 1 || { usage >&2; die "Too many arguments."; } ;;
    -h|--help) usage; return ;;
    *) die "Unknown installation command: $1" ;;
  esac
  validate_launch_options
  migrate_legacy_install
  validate_managed_layout || die "Unsafe installation layout."
  trackmania_mac_python -m trackmania_mac.guard.managed_paths new-prefix "$TRACKMANIA_MAC_PREFIX" \
    || die "Choose an empty prefix or a managed TrackMania Mac installation."
  guard_prefix_operation "${SCRIPT_DIR}/install.sh" "$@"
  require_macos
  install_rosetta
  install_wine_runtime
  export TRACKMANIA_MAC_D9VK_DLL="${TRACKMANIA_MAC_RUNTIME}/lib/d9vk/d3d9.dll"
  export TRACKMANIA_MAC_SIDECAR="${TRACKMANIA_MAC_RUNTIME}/bin/x87sidecar"
  initialize_prefix

  if test "${1:-}" = "directx"; then
    install_directx_runtime
    return
  fi

  if test "${1:-}" != "setup"; then
    case "$TRACKMANIA_MAC_TITLE_INSTALLER" in
      online|online-retail|online-trial)
        install_game
        ensure_title_icon || warn "Could not extract the game icon for the hub."
        install_directx_runtime
        if title_has competition-patch; then
          install_competition_patch
        fi
        if title_has openal; then
          install_openal
        fi
        ;;
      steam-copy|disk-image)
        die "Title $TRACKMANIA_MAC_TITLE joins an existing copy:
run the shared platform setup, then use the hub's import action.
  make setup
  TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac import /path/to/existing-game-files"
        ;;
    esac
  else
    install_directx_runtime
  fi

  "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" d9vk
  if test "${1:-}" = "setup"; then
    say "Platform setup complete (runtime, prefix, DirectX, D9VK)."
    say "Provide the retail game image, then run src/shell/cli/trackmania-mac doctor to verify."
    return
  fi

  case "$TRACKMANIA_MAC_TITLE_INSTALLER" in
    online|online-retail|online-trial)
      "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" app
      "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" doctor
      ;;
    *)
      say "Platform setup complete (runtime, prefix, DirectX, D9VK)."
      say "Next, provide the existing game files:"
      if test "$TRACKMANIA_MAC_TITLE_INSTALLER" = steam-copy; then
        say "  TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac import /path/to/steamapps/common/game-folder"
      else
        say "  TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac import /path/to/retail-setup.dmg"
      fi
      say "Imported installs then finish with: TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac app"
      return
      ;;
  esac
  say "Run src/shell/cli/trackmania-mac play to start the game."
}

if test "${BASH_SOURCE[0]}" = "$0"; then
  main "$@"
fi
