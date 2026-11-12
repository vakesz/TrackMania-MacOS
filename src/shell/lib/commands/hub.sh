#!/bin/bash
# Machine-facing hub backend. Native clients own the presenter; the title
# registry, per-title state probe and action dispatch live in
# src/python/trackmania_mac/hub. Shell remains only where Wine plumbing requires it.

hub_command() {
  exec /usr/bin/env PYTHONPATH="${TRACKMANIA_MAC_PYTHONPATH}" "${TRACKMANIA_MAC_PYTHON}" -m trackmania_mac.hub "$@"
}
