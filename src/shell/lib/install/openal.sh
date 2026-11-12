#!/bin/bash
# OpenAL Soft in place of TMNF's Creative OpenAL. Sourced by install.sh after common.sh.

install_openal() (
  local extract_dir entry cleanup current

  current="$(game_openal)"
  case "$current" in
    missing)
      warn "The game has no OpenAL32.dll; skipping the OpenAL step. Reinstall the game to restore it."
      return ;;
    other)
      warn "Leaving $GAME_OPENAL unchanged: it is neither the game's Creative OpenAL nor OpenAL Soft $OPENAL_SOFT_VERSION"
      return ;;
  esac
  if test "$TRACKMANIA_MAC_OPENAL" = creative; then
    if test "$current" = soft; then
      verify_file "$GAME_OPENAL_CREATIVE" "$TRACKMANIA_MAC_TITLE_CREATIVE_OPENAL_SHA256" \
        || die "Cannot restore Creative OpenAL: $GAME_OPENAL_CREATIVE is missing. Reinstall the game with update."
      cp "$GAME_OPENAL_CREATIVE" "$GAME_OPENAL"
      rm -f "$GAME_OPENAL_COPYING"
      say "Restored the game's Creative OpenAL"
    else
      say "Using the game's Creative OpenAL"
    fi
    printf 'creative\n' >"$OPENAL_MARKER"
    return
  fi

  if test "$current" = soft; then
    printf 'soft\n' >"$OPENAL_MARKER"
    say "OpenAL Soft $OPENAL_SOFT_VERSION is already installed"
    return
  fi

  download_verified "$OPENAL_SOFT_URL" "$OPENAL_SOFT_ARCHIVE" "$OPENAL_SOFT_SHA256"
  while IFS= read -r entry; do
    case "$entry" in
      /*|../*|*/../*|*/..) die "Unsafe path in OpenAL Soft archive: $entry" ;;
    esac
  done < <(unzip -Z1 "$OPENAL_SOFT_ARCHIVE")

  sweep_stale "$TRACKMANIA_MAC_CACHE" '*-extract.*'
  extract_dir="$(mktemp -d "${TRACKMANIA_MAC_CACHE}/openal-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  unzip -q "$OPENAL_SOFT_ARCHIVE" "openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" \
    "openal-soft-${OPENAL_SOFT_VERSION}-bin/COPYING" -d "$extract_dir"
  verify_file "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" "$OPENAL_SOFT_DLL_SHA256" \
    || die "OpenAL Soft library verification failed."

  # Keep the original, so TRACKMANIA_MAC_OPENAL=creative restores it without a game reinstall.
  cp "$GAME_OPENAL" "$GAME_OPENAL_CREATIVE"
  cp "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/bin/Win32/soft_oal.dll" "$GAME_OPENAL"
  cp "${extract_dir}/openal-soft-${OPENAL_SOFT_VERSION}-bin/COPYING" "$GAME_OPENAL_COPYING"

  openal_soft_is_installed || die "OpenAL Soft installation failed."
  printf 'soft\n' >"$OPENAL_MARKER"
  say "Installed OpenAL Soft $OPENAL_SOFT_VERSION as the game's OpenAL32.dll (TRACKMANIA_MAC_OPENAL=creative restores the original)"
)
