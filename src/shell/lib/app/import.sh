#!/bin/bash
# Local imports for retail titles: retail setup programs (.exe) and existing
# Steam game folders (steam-copy titles). Disk images can be mounted for
# inventory, but their setup executable must be copied out before installation.
# Local owned media only. Serial keys are entered inside the game and are
# never stored. Provisional titles stay provisional until user media is
# inventoried; nothing here pretends otherwise.

import_source() {
  local source="$1"
  local mount cleanup

  validate_launch_options
  test -n "${source:-}" || die "Usage: tmnf import <disk-image|.iso|.dmg|setup.exe|steam-game-folder>"
  case "$TMNF_TITLE" in
    tmnf) die "TMNF uses the free download flow (make install); import is for retail titles." ;;
  esac
  require_owned_prefix || die "A managed Wine prefix is required."
  validate_managed_layout || die "Unsafe installation layout."

  if test -d "$source"; then
    import_steam_folder "$source"
    return
  fi

  test -f "$source" || die "Import source not found: $source"
  case "$source" in
    *.iso|*.ISO|*.dmg|*.DMG|*.exe|*.EXE) ;;
    *) die "Unsupported import type (use .iso, .dmg, a retail setup .exe or a Steam game folder): $source" ;;
  esac

  case "$source" in
    *.dmg|*.DMG|*.iso|*.ISO)
      have hdiutil || die "hdiutil is required to mount disk images."
      mount="$(mktemp -d "${TMNF_CACHE}/import-mount.XXXXXX")" || die "Cannot stage mount point."
      rmdir "$mount" || die "Cannot stage mount point."
      printf -v cleanup 'hdiutil detach %q >/dev/null 2>&1 || true; rm -rf -- %q' "$mount" "$mount"
      # shellcheck disable=SC2064
      trap "$cleanup" EXIT
      hdiutil attach -readonly -nobrowse -mountpoint "$mount" "$source" \
        || die "Cannot mount disk image: $source"
      say "Mounted $source at $mount"
      printf 'Top-level contents:\n'
      find "$mount" -mindepth 1 -maxdepth 1 -print 2>/dev/null | head -n 20
      say "While the image is still mounted, copy the retail Setup .exe out of it"
      say "( Finder drag, or: cp \"$mount/<Setup>.exe\" ~/Downloads/ )"
      say "Import paths that point inside a temporary mount vanish after this"
      say "command exits; run the installer import from a stable folder:"
      say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf import <stable>/Setup.exe"
      hdiutil detach "$mount" >/dev/null 2>&1 || true
      trap - EXIT
      rm -rf "$mount"
      ;;
    *.exe|*.EXE)
      say "Running the retail installer in the managed prefix: $source"
      say "Choose its game install location; the hub will verify the expected executable when it exits."
      run_wine "$source" || die "The retail installer failed: $source"
      test -f "$GAME_EXE" || die \
        "The installer exited without creating the expected game executable: $GAME_EXE. Check its install location and retry."
      say "$TMNF_TITLE game files are installed. Use the hub to install its Game and Settings apps."
      ;;
  esac
}

# import_steam_folder FOLDER — steam-copy titles.
import_steam_folder() {
  local source="$1" symlink_files

  case "$(tmnf_title_field "$TMNF_TITLE" installer)" in
    steam-copy) ;;
    *) die "$TMNF_TITLE imports from a disk image or setup program, not a game folder." ;;
  esac

  test -n "$(find "$source" -mindepth 1 -maxdepth 1 -print -quit)" \
    || die "The game folder is empty: $source"
  # The copying walk is literal: no symlink ever leaves the chosen folder.
  symlink_files="$(find "$source" -type l 2>/dev/null | head -n 10)"
  if test -n "$symlink_files"; then
    die "Refusing a source that contains symlinks:\n$symlink_files"
  fi

  if test -L "$GAME_DIR"; then
    die "Refusing a symlink at the managed game destination: $GAME_DIR"
  fi
  if test -e "$GAME_DIR" && test -n "$(find "$GAME_DIR" -mindepth 1 -maxdepth 1 -print -quit)"; then
    die "The managed game destination is not empty: $GAME_DIR. Move it aside before importing."
  fi
  mkdir -p "$GAME_DIR"
  say "Copying $TMNF_TITLE game files into the prefix"
  say "  from: $source"
  say "  into: $GAME_DIR"
  ditto "$source" "$GAME_DIR" || die "Cannot copy the game files."
  if test ! -f "$GAME_EXE"; then
    die "Copied, but the game executable is missing at $GAME_EXE."
  fi
  say "Copied. Finish the Windows-side prerequisites with:"
  say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf directx"
  say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf d9vk"
  say "Then verify with TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf doctor."
}
