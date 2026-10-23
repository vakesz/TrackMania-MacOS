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
. "${TMNF_PROJECT_DIR}/src/shell/lib/install/runtime.sh"
# shellcheck source=src/shell/lib/install/prefix.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/install/prefix.sh"
# shellcheck source=src/shell/lib/install/directx.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/install/directx.sh"
# shellcheck source=src/shell/lib/install/game.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/install/game.sh"
# shellcheck source=src/shell/lib/install/patch.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/install/patch.sh"
# shellcheck source=src/shell/lib/apps/icons.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/apps/icons.sh"

usage() {
  say 'Usage: install.sh [--runtime=auto|download|build] [directx|setup]'
}

main() {
  local runtime_flag=0
  # The flag wins over TMNF_RUNTIME_SOURCE. It reaches the guarded re-exec through the environment.
  while test "$#" -gt 0; do
    case "$1" in
      --runtime=*|--runtime)
        test "$runtime_flag" = 0 || die "Give --runtime only once."
        runtime_flag=1
        if test "$1" = --runtime; then
          test "$#" -ge 2 || die "--runtime needs auto, download or build."
          TMNF_RUNTIME_SOURCE="$2"
          shift
        else
          TMNF_RUNTIME_SOURCE="${1#--runtime=}"
        fi
        shift
        ;;
      *) break ;;
    esac
  done
  export TMNF_RUNTIME_SOURCE="${TMNF_RUNTIME_SOURCE-auto}"
  validate_runtime_source
  case "${1:-}" in
    ''|directx|setup) test "$#" -le 1 || { usage >&2; die "Too many arguments."; } ;;
    -h|--help) usage; return ;;
    *) die "Unknown installation command: $1" ;;
  esac
  validate_launch_options
  validate_managed_layout || die "Unsafe installation layout."
  tmnf_python -m tmnf.guard.managed_paths new-prefix "$TMNF_PREFIX" \
    || die "Choose an empty prefix or a managed TMNF installation."
  guard_prefix_operation "${SCRIPT_DIR}/install.sh" "$@"
  require_macos
  install_rosetta
  install_wine_runtime
  export TMNF_D9VK_DLL="${TMNF_RUNTIME}/lib/d9vk/d3d9.dll"
  export TMNF_SIDECAR="${TMNF_RUNTIME}/bin/x87sidecar"
  initialize_prefix

  if test "${1:-}" = "directx"; then
    install_directx_runtime
    return
  fi

  if test "${1:-}" != "setup"; then
    case "$TMNF_TITLE_INSTALLER" in
      online|online-retail|online-trial)
        install_game
        ensure_title_icon || warn "Could not extract the game icon for the hub."
        install_directx_runtime
        if test "$TMNF_TITLE_NEEDS_TMLOADER" = true; then
          install_competition_patch
        fi
        ;;
      steam-copy|disk-image)
        die "Title $TMNF_TITLE joins an existing copy:
run the shared platform setup, then use the hub's import action.
  make setup
  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf import /path/to/existing-game-files"
        ;;
    esac
  else
    install_directx_runtime
  fi

  "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" d9vk
  if test "${1:-}" = "setup"; then
    say "Platform setup complete (runtime, prefix, DirectX, D9VK)."
    say "Provide the retail game image, then run src/shell/cli/tmnf doctor to verify."
    return
  fi

  case "$TMNF_TITLE_INSTALLER" in
    online|online-retail|online-trial)
      "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" app
      "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" doctor
      ;;
    *)
      say "Platform setup complete (runtime, prefix, DirectX, D9VK)."
      say "Next, provide the existing game files:"
      if test "$TMNF_TITLE_INSTALLER" = steam-copy; then
        say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf import /path/to/steamapps/common/game-folder"
      else
        say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf import /path/to/retail-setup.dmg"
      fi
      say "Imported installs then finish with: TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf app"
      return
      ;;
  esac
  say "Run src/shell/cli/tmnf play to start the game."
}

if test "${BASH_SOURCE[0]}" = "$0"; then
  main "$@"
fi
