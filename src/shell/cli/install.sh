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

TMNF_TITLE_INSTALL_KIND="$(tmnf_title_field "$TMNF_TITLE" installer)"
TMNF_TITLE_NEEDS_PATCH="$(tmnf_title_field "$TMNF_TITLE" needs_tmloader)"

main() {
  case "${1:-}" in
    ''|directx|setup) test "$#" -le 1 || die "Usage: install.sh [directx|setup]" ;;
    -h|--help) say 'Usage: install.sh [directx|setup]'; return ;;
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
    case "$TMNF_TITLE_INSTALL_KIND" in
      online|online-retail|online-trial)
        install_game
        install_directx_runtime
        if test "$TMNF_TITLE_NEEDS_PATCH" = true; then
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

  case "$TMNF_TITLE_INSTALL_KIND" in
    online|online-retail|online-trial)
      "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" app
      "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" doctor
      ;;
    *)
      say "Platform setup complete (runtime, prefix, DirectX, D9VK)."
      say "Next, provide the existing game files:"
      if test "$TMNF_TITLE_INSTALL_KIND" = steam-copy; then
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
