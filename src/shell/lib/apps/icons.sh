#!/bin/bash
# Per-title icon cache: ${TMNF_SUPPORT}/icons/<id>.icns is extracted from the
# locally installed game executable, so nothing of Nadeo's is redistributed.
# Until it exists, app wrappers fall back to the project's own bundled artwork.

# icon_tool ARGS... — run tmnf.app.icon without any operation-lock descriptor.
# A leaked copy of either token would keep the prefix or app lock alive after
# the caller exits, and sips/iconutil never need them.
icon_tool() (
  if test "${TMNF_APP_OPERATION_FD+x}" = x; then
    case "$TMNF_APP_OPERATION_FD" in ''|*[!0-9]*) : ;; *) eval "exec ${TMNF_APP_OPERATION_FD}>&-" ;; esac
    unset TMNF_APP_OPERATION_FD
  fi
  if test "${TMNF_OPERATION_FD+x}" = x; then
    case "$TMNF_OPERATION_FD" in ''|*[!0-9]*) : ;; *) eval "exec ${TMNF_OPERATION_FD}>&-" ;; esac
    unset TMNF_OPERATION_FD
  fi
  without_operation_lock tmnf_python -m tmnf.app.icon "$@"
)

# ensure_title_icon — refresh the cached game icon when the game exists and the
# cache is missing or older than the executable. Returns nonzero when no icon
# could be produced; callers treat that as cosmetic.
ensure_title_icon() {
  local icons="${TMNF_SUPPORT}/icons" cache work
  cache="${icons}/${TMNF_TITLE}.icns"
  test -f "$GAME_EXE" || return 0
  if test -f "$cache" && test ! -L "$cache" && test ! "$GAME_EXE" -nt "$cache"; then
    return 0
  fi
  is_safe_managed_path "$icons" 2>/dev/null || return 1
  test ! -L "$cache" || return 1
  mkdir -p "$icons" || return 1
  work="$(mktemp -d "${icons}/.extract.XXXXXX")" || return 1
  if icon_tool "$GAME_EXE" "${work}/icon.icns" 2>/dev/null && test -s "${work}/icon.icns" \
    && mv -f "${work}/icon.icns" "$cache"; then
    rm -rf -- "$work"
    return 0
  fi
  rm -rf -- "$work"
  return 1
}
