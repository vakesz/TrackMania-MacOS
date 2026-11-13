"""Names of earlier releases that installed files or a user's environment may still carry.

Recognising them lives here alone; src/shell/lib/legacy.sh holds the shell side.
tests/test_naming.py allows these names in the two files only.
"""

from __future__ import annotations

import os
import sys
import typing

# Settings of earlier releases were named TMNF_*.
ENV_PREFIX = "TMNF_"
NEW_ENV_PREFIX = "TRACKMANIA_MAC_"
# Internal names an earlier release passed between its own processes: dropped.
INTERNAL_ENV = ("OPERATION_FD", "APP_OPERATION_FD", "MIGRATION_CHECKED", "RELOCATION_MAP")
INTERNAL_ENV_PREFIXES = ("TITLE_",)
# An installed launcher of an earlier release: its Python package and CLI.
PYTHON_PACKAGE = "tmnf"
CLI_NAME = "tmnf"


def adopt_settings(
    environ: typing.Optional[typing.MutableMapping[str, str]] = None,
    stream: typing.Optional[typing.TextIO] = None,
) -> None:
    """Map each TMNF_X to TRACKMANIA_MAC_X (the new name wins), say so once, unset TMNF_X.

    The same rule as adopt_legacy_settings in src/shell/lib/legacy.sh, for Python
    entry points started without the shell. Notices go to stderr only.
    """
    environ = os.environ if environ is None else environ
    stream = sys.stderr if stream is None else stream
    for old in sorted(name for name in environ if name.startswith(ENV_PREFIX)):
        rest = old[len(ENV_PREFIX) :]
        value = environ.pop(old)
        if rest in INTERNAL_ENV or rest.startswith(INTERNAL_ENV_PREFIXES):
            continue
        new = NEW_ENV_PREFIX + rest
        if new in environ:
            print("warning: {} is ignored because {} is set.".format(old, new), file=stream)
        else:
            environ[new] = value
            print("warning: {} is now {}; the old name still works.".format(old, new), file=stream)


# Markers of earlier releases, by the name of this release's constant in
# markers.py. Readers accept them; prefix markers are kept beside the new ones,
# because a relinked launcher of an earlier release reads only these.
MARKERS = {
    "PREFIX": ".tmnf-prefix",
    "INITIALIZING": ".tmnf-initializing",
    "PROFILE_BASELINE": ".tmnf-profile-baseline",
    "DIRECTX": ".tmnf-directx-jun2010",
    "OPENAL": ".tmnf-openal",
    "APP": ".tmnf-app",
    "LAUNCHER": ".tmnf-launcher",
    # A runtime an earlier release installed; read only when deciding whether
    # an old runtime may be removed (guard/storage.py).
    "RUNTIME": ".tmnf-runtime",
}
TOMBSTONE_INFIX = ".tmnf-removing"
RELINK_INFIX = ".tmnf-relink"
# A relinked launcher of an earlier release locks its prefix under this name,
# so every operation also takes it (operation_guard.acquire_legacy).
OPERATION_LOCK_SUFFIX = ".tmnf-operation.lock"
# Bundle identifier of the shared launcher app of the first releases.
LAUNCHER_APP_ID = "local.tmnf.launcher"
# The hub app's own bundle identifier before this release.
HUB_BUNDLE_ID = "local.tmnf.hub"
# Session line prefixes earlier releases wrote; logs and their backups still hold them.
LOG_STATE = "tmnf-state:"
LOG_MAP = "tmnf-map:"
LOG_MONITOR = "tmnf-mon:"
LOG_RUN = "tmnf-run:"
# The app wrappers' executable (CFBundleExecutable) before this release.
WRAPPER_EXECUTABLE = "TMNF"
