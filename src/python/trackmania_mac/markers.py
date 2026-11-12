"""Names of the marker files, locks, tombstones and runtime files this release writes.

Each has one constant here; src/shell/lib/common.sh repeats the few the shell
writes. The names of earlier releases are in legacy.py, and readers accept both
(see managed_paths.read_marker).
"""

from __future__ import annotations

# In a Wine prefix: ownership (text "<title id>:1"), an interrupted
# initialization, the profiles before wineboot, and applied components.
PREFIX = ".trackmania-mac-prefix"
INITIALIZING = ".trackmania-mac-initializing"
PROFILE_BASELINE = ".trackmania-mac-profile-baseline"
DIRECTX = ".trackmania-mac-directx-jun2010"
OPENAL = ".trackmania-mac-openal"
# In an app wrapper's Contents and in a launcher folder.
APP = ".trackmania-mac-app"
LAUNCHER = ".trackmania-mac-launcher"
# In a runtime built by this release (no earlier name is read: a release only
# ever checks a runtime of its own version).
RUNTIME = ".trackmania-mac-runtime"
SESSION_EXE = "trackmania-mac-session.exe"
LICENSE = "trackmania-mac.txt"
# `.<name><infix>.<8 hex>`: an uninstall's tombstone, a link being swapped.
TOMBSTONE_INFIX = ".trackmania-mac-removing"
RELINK_INFIX = ".trackmania-mac-relink"
# Beside a prefix: `.<prefix name><suffix>`.
OPERATION_LOCK_SUFFIX = ".trackmania-mac-operation.lock"
# Prefixes of the session lines a diagnostic run adds to the play log
# (src/c/game-state.c writes the same spellings for state and map).
LOG_STATE = "trackmania-mac-state:"
LOG_MAP = "trackmania-mac-map:"
LOG_MONITOR = "trackmania-mac-mon:"
LOG_RUN = "trackmania-mac-run:"
# The app wrappers' executable (CFBundleExecutable), and so their process name.
WRAPPER_EXECUTABLE = "TrackManiaMac"
