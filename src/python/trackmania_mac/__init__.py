# Settings of earlier releases (TRACKMANIA_MAC_*) become TRACKMANIA_MAC_* before any module
# reads the environment; the shell does the same in common.sh.
from . import legacy as _legacy  # noqa: E402

_legacy.adopt_settings()
