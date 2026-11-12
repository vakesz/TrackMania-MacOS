#!/bin/bash
# shellcheck disable=SC2034 # Read by the files that source common.sh.
# Names of earlier releases that installed files may still carry. Recognising
# them lives here alone; src/python/trackmania_mac/legacy.py holds the Python
# side. tests/test_naming.py allows these names in this file only.

# An installed launcher from an earlier release: its Python package and CLI.
readonly LEGACY_PYTHON_PACKAGE=tmnf
readonly LEGACY_CLI_NAME=tmnf

# Settings of earlier releases were named TMNF_*. Internal names an earlier
# release passed between its own processes are dropped, never adopted.
readonly LEGACY_ENV_PREFIX=TMNF_

# adopt_legacy_settings — for each TMNF_X in the environment, set
# TRACKMANIA_MAC_X unless that is already set (the new name wins), say so once
# on stderr, and unset TMNF_X so children never see or report it again.
# Bash builtins only: the hub runs the CLI with a minimal PATH, and stdout of
# `hub snapshot` is JSON. src/python/trackmania_mac/legacy.py does the same.
adopt_legacy_settings() {
  local old new
  for old in ${!TMNF_*}; do
    case "$old" in
      TMNF_OPERATION_FD|TMNF_APP_OPERATION_FD|TMNF_MIGRATION_CHECKED|TMNF_RELOCATION_MAP|TMNF_TITLE_*)
        unset "$old"
        continue
        ;;
    esac
    new="TRACKMANIA_MAC_${old#"$LEGACY_ENV_PREFIX"}"
    if test -n "${!new+x}"; then
      printf 'warning: %s is ignored because %s is set.\n' "$old" "$new" >&2
    else
      export "$new=${!old}"
      printf 'warning: %s is now %s; the old name still works.\n' "$old" "$new" >&2
    fi
    unset "$old"
  done
}

# Markers of earlier releases (see src/python/trackmania_mac/legacy.py MARKERS).
readonly LEGACY_PREFIX_MARKER=.tmnf-prefix
readonly LEGACY_INITIALIZING_MARKER=.tmnf-initializing
readonly LEGACY_PROFILE_BASELINE=.tmnf-profile-baseline
readonly LEGACY_DIRECTX_MARKER_NAME=.tmnf-directx-jun2010
readonly LEGACY_OPENAL_MARKER_NAME=.tmnf-openal
readonly LEGACY_APP_MARKER=.tmnf-app
readonly LEGACY_LAUNCHER_MARKER=.tmnf-launcher
readonly LEGACY_LAUNCHER_APP_ID=local.tmnf.launcher
# The app wrappers' executable (CFBundleExecutable) before this release.
readonly LEGACY_WRAPPER_EXECUTABLE=TMNF
