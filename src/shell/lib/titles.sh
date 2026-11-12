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
# of TITLE plus the release pins (DIRECTX_*, TMLOADER_*, TMCOMP_*, OPENAL_SOFT_*). Without a
# title only the pins are defined. The registry validates every value and
# shell-quotes it, so evaluating the output is safe.
# title_has COMPONENT — true when the loaded title has this install component.
# The registry derives the list (Title.components); an unknown name is a bug.
title_has() {
  case "$1" in
    competition-patch|openal|system-config) ;;
    *) die "Unknown title component: $1" ;;
  esac
  case " ${TMNF_TITLE_COMPONENTS-} " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

tmnf_load_title() {
  local assignments
  assignments="$(TMNF_TITLES_DIR="$TMNF_TITLES_DIR" tmnf_python -m tmnf.hub.registry export "$@")" \
    || return 1
  eval "$assignments"
}

# _cache_installer_path TITLE — resolved path of the pinned installer download.
# src/python/tmnf/guard/uninstall.py mirrors it (installer_cache_path); a test keeps them equal.
_cache_installer_path() {
  local title="$1" url="$TMNF_TITLE_SOURCE_URL"
  test "$title" = "$TMNF_TITLE" || die "Installer cache requested for $title, not $TMNF_TITLE."
  if test -z "$url"; then
    die "Title $title has no pinned installer URL in config/titles."
  fi
  if test "$title" = tmnf; then
    # Keep the historical single-title cache location so existing downloads
    # stay valid.
    printf '%s/%s\n' "$TMNF_CACHE" "${url##*/}"
  else
    printf '%s/games/%s/%s\n' "$TMNF_CACHE" "$title" "${url##*/}"
  fi
}
