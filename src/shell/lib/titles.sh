#!/bin/bash
# Title registry access. config/titles/*.json and src/python/tmnf/hub/registry.py
# are the single source of truth; this file only loads them. Inert policy only:
# no plugins or arbitrary hooks.

: "${TMNF_TITLES_DIR:=${TMNF_PROJECT_DIR}/config/titles}"

tmnf_title_is_known() {
  case "$1" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
  test -f "${TMNF_TITLES_DIR}/$1.json"
}

# tmnf_load_title [TITLE] — one Python start defines every TMNF_TITLE_* field
# of TITLE plus the release pins (DIRECTX_*, TMLOADER_*, TMCOMP_*). Without a
# title only the pins are defined. The registry validates every value and
# shell-quotes it, so evaluating the output is safe.
tmnf_load_title() {
  local assignments
  assignments="$(TMNF_TITLES_DIR="$TMNF_TITLES_DIR" tmnf_python -m tmnf.hub.registry export "$@")" \
    || return 1
  eval "$assignments"
}
