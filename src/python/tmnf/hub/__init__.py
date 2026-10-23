"""Machine-facing hub backend.

Native clients own the presenter; this package owns the title registry, the
per-title state probe, and the run dispatch into the managed install flows.
Shell stays only where Wine/process plumbing is genuinely required.
"""


class HubError(Exception):
    """A hub-level operator error (bad arguments, unknown action)."""
