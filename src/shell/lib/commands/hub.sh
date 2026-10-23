#!/bin/bash
# Machine-facing hub backend. Native clients own the presenter; the title
# registry, per-title state probe and action dispatch live in
# src/python/tmnf/hub. Shell remains only where Wine plumbing requires it.

hub_command() {
  exec /usr/bin/env PYTHONPATH="${TMNF_PYTHONPATH}" "${TMNF_PYTHON}" -m tmnf.hub "$@"
}
