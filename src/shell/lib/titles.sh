#!/bin/bash
# Title registry access. config/titles/*.json and src/python/trackmania_mac/hub/registry.py
# are the single source of truth; this file only loads them. Inert policy only:
# no plugins or arbitrary hooks.

: "${TRACKMANIA_MAC_TITLES_DIR:=${TRACKMANIA_MAC_PROJECT_DIR}/config/titles}"

trackmania_mac_title_is_known() {
  case "$1" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
  test -f "${TRACKMANIA_MAC_TITLES_DIR}/$1.json"
}

# trackmania_mac_load_title [TITLE] — one Python start defines every TRACKMANIA_MAC_TITLE_* field
# of TITLE plus the release pins (DIRECTX_*, TMLOADER_*, TMCOMP_*, OPENAL_SOFT_*). Without a
# title only the pins are defined. The registry validates every value and
# shell-quotes it, so evaluating the output is safe.
# title_has COMPONENT — true when the loaded title has this install component.
# The registry derives the list (Title.components); an unknown name is a bug.
title_has() {
  case "$1" in
    competition-patch|openal|system-config|updates|overlay) ;;
    *) die "Unknown title component: $1" ;;
  esac
  case " ${TRACKMANIA_MAC_TITLE_COMPONENTS-} " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

trackmania_mac_load_title() {
  local assignments
  assignments="$(TRACKMANIA_MAC_TITLES_DIR="$TRACKMANIA_MAC_TITLES_DIR" trackmania_mac_python -m trackmania_mac.hub.registry export "$@")" \
    || return 1
  eval "$assignments"
}

# _cache_installer_path TITLE — resolved path of the pinned installer download.
# src/python/trackmania_mac/guard/uninstall.py mirrors it (installer_cache_path); a test keeps them equal.
_cache_installer_path() {
  local title="$1" url="$TRACKMANIA_MAC_TITLE_SOURCE_URL"
  test "$title" = "$TRACKMANIA_MAC_TITLE" || die "Installer cache requested for $title, not $TRACKMANIA_MAC_TITLE."
  if test -z "$url"; then
    die "Title $title has no pinned installer URL in config/titles."
  fi
  if test "$title" = tmnf; then
    # Keep the historical single-title cache location so existing downloads
    # stay valid.
    printf '%s/%s\n' "$TRACKMANIA_MAC_CACHE" "${url##*/}"
  else
    printf '%s/games/%s/%s\n' "$TRACKMANIA_MAC_CACHE" "$title" "${url##*/}"
  fi
}

# _cache_download_path TITLE URL — resolved path of one of the title's extra
# downloads (updates, overlay), always under games/<id>.
# src/python/trackmania_mac/guard/uninstall.py mirrors it (download_cache_paths); a test keeps them equal.
_cache_download_path() {
  local title="$1" url="$2"
  test "$title" = "$TRACKMANIA_MAC_TITLE" || die "Download cache requested for $title, not $TRACKMANIA_MAC_TITLE."
  test -n "$url" || die "Title $title has no pinned download URL in config/titles."
  printf '%s/games/%s/%s\n' "$TRACKMANIA_MAC_CACHE" "$title" "${url##*/}"
}
