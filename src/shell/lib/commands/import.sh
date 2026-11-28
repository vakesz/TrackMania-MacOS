#!/bin/bash
# Local imports for retail titles and existing Steam game folders (steam-copy
# titles). A disk-image title takes its retail setup program (.exe), a disc
# image (.iso or .dmg, mounted read-only for the whole import) or a folder that
# holds the disc's files; for the last two, find_disc_setup picks the setup
# program at the top of the disc. Local owned media only. Serial keys are
# entered inside the game and are never stored. An import does not change a
# title's registry status; it stays provisional or unsupported until user media
# is inventoried.
#
# A retail title with steps after its setup (title_is_layered: pinned official
# updates, an overlay, the NX_COMPAT flag) gets them from apply_title_layers in
# install/game.sh: the downloads are verified before the setup, so a failed
# download costs no clicks, the updates run silently, and each finished update
# is recorded in drive_c/trackmania-mac-updates.done. When the game is already
# installed, an import of such a title skips the setup: it does nothing when the
# executable is the pinned build, sets only the NX_COMPAT flag when that is all
# that is left, and otherwise repeats the steps (the updates not yet recorded,
# the overlay, the flag), which finishes an import that stopped.

# shellcheck source=src/shell/lib/apps/icons.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/apps/icons.sh"
# shellcheck source=src/shell/lib/install/game.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/install/game.sh"

import_source() {
  local source="$1" setup

  validate_launch_options
  test -n "${source:-}" || die "Usage: trackmania-mac import <.iso|.dmg|setup.exe|disc-folder|steam-game-folder>"
  case "$TRACKMANIA_MAC_TITLE_INSTALLER" in
    online|online-retail|online-trial)
      die "$TRACKMANIA_MAC_TITLE_NAME installs from its official download (src/shell/cli/install.sh); import is for Steam copies and retail discs." ;;
  esac
  require_owned_managed_layout

  if test "$TRACKMANIA_MAC_TITLE_INSTALLER" = steam-copy; then
    test -d "$source" || die "$TRACKMANIA_MAC_TITLE imports from its Steam game folder: $source"
    import_steam_folder "$source"
    return
  fi

  if test -d "$source"; then
    # die inside the substitution ends only the subshell.
    setup="$(find_disc_setup "$source")" || exit 1
    import_setup "$setup"
    return
  fi
  test -f "$source" || die "Import source not found: $source"
  case "$source" in
    *.dmg|*.DMG|*.iso|*.ISO) import_disc_image "$source" ;;
    *.exe|*.EXE) import_setup "$source" ;;
    *) die "Unsupported import type (use the disc image, its setup .exe or a folder with the disc's files): $source" ;;
  esac
}

# find_disc_setup FOLDER — prints the one setup program (*setup*.exe, any
# case) at the top of a disc folder. Symlinks are not followed.
find_disc_setup() {
  local folder="$1" found
  local -a setups=()
  while IFS= read -r -d '' found; do
    setups+=("$found")
  done < <(find "$folder" -mindepth 1 -maxdepth 1 -type f -iname '*setup*.exe' -print0 2>/dev/null)
  case "${#setups[@]}" in
    1) printf '%s\n' "${setups[0]}" ;;
    0) die "No setup program (a Setup .exe) at the top of $folder. Choose the disc image, the disc's folder or its setup .exe." ;;
    *) die "More than one setup program in $folder; choose the one to run:$(printf '\n  %s' "${setups[@]}")" ;;
  esac
}

# import_disc_image IMAGE — mounts a .iso or .dmg read-only and runs its setup
# program; the image stays mounted until the import ends.
import_disc_image() {
  local image="$1" mount cleanup setup
  have hdiutil || die "hdiutil is required to mount disk images."
  mkdir -p "$TRACKMANIA_MAC_CACHE" || die "Cannot create $TRACKMANIA_MAC_CACHE"
  mount="$(mktemp -d "${TRACKMANIA_MAC_CACHE}/import-mount.XXXXXX")" || die "Cannot stage mount point."
  rmdir "$mount" || die "Cannot stage mount point."
  printf -v cleanup 'hdiutil detach %q >/dev/null 2>&1 || true; rm -rf -- %q' "$mount" "$mount"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  hdiutil attach -readonly -nobrowse -mountpoint "$mount" "$image" >/dev/null \
    || die "Cannot mount disk image: $image"
  say "Mounted $image read-only."
  setup="$(find_disc_setup "$mount")" || exit 1
  import_setup "$setup"
  hdiutil detach "$mount" >/dev/null 2>&1 || warn "Could not unmount $mount; eject it in Finder."
  trap - EXIT
  rm -rf -- "$mount"
}

# import_setup SETUP — runs a retail setup program, then the title's layers.
import_setup() {
  local source="$1"
  if title_has updates || title_has overlay; then
    download_title_extras
  fi
  if title_is_layered && test -f "$GAME_EXE"; then
    if test -n "${TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256:-}" \
      && verify_file "$GAME_EXE" "$TRACKMANIA_MAC_TITLE_GAME_EXE_SHA256"; then
      say "$TRACKMANIA_MAC_TITLE_NAME $TRACKMANIA_MAC_TITLE_GAME_VERSION is already installed at $GAME_DIR."
    elif game_exe_finish_pending; then
      say "Finishing the $TRACKMANIA_MAC_TITLE_NAME installation at $GAME_DIR."
      finish_after_overlay
    else
      say "$TRACKMANIA_MAC_TITLE_NAME is already installed at $GAME_DIR; applying the remaining install steps."
      stop_prefix_processes
      apply_title_layers
    fi
  else
    say "Running the retail installer in the managed prefix: $source"
    say "Choose its game install location; the hub will verify the expected executable when it exits."
    run_wine "$source" || die "The retail installer failed: $source"
    test -f "$GAME_EXE" || die \
      "The installer exited without creating the expected game executable: $GAME_EXE. Check its install location and retry."
    if title_is_layered; then
      # Stops anything started from the setup's last page before the next steps run.
      stop_prefix_processes
      apply_title_layers
    fi
  fi
  # The import holds the prefix lock; Play links what is left.
  sync_game_save_links || warn "Some save folders in the game folder are not linked to the saves folder yet."
  ensure_title_icon || warn "Could not extract the game icon for the hub."
  say "$TRACKMANIA_MAC_TITLE game files are installed. Use the hub to install its Game and Settings apps."
}

# check_game_dir_chain — no component between the prefix and the game folder
# may be a symlink, so the copy cannot be redirected outside the prefix.
check_game_dir_chain() {
  local drive_c="${TRACKMANIA_MAC_PREFIX}/drive_c" path part
  local -a parts
  test ! -L "$TRACKMANIA_MAC_PREFIX" || die "Refusing a symlink at the prefix: $TRACKMANIA_MAC_PREFIX"
  test ! -L "$drive_c" || die "Refusing a symlink at the managed destination: $drive_c"
  case "$GAME_DIR" in
    "${drive_c}/"*) ;;
    *) die "The game folder is outside the prefix: $GAME_DIR" ;;
  esac
  IFS=/ read -r -a parts <<<"${GAME_DIR#"${drive_c}"/}"
  path="$drive_c"
  for part in "${parts[@]}"; do
    path="${path}/${part}"
    test ! -L "$path" || die "Refusing a symlink at the managed game destination: $path"
  done
}

# import_steam_folder FOLDER — steam-copy titles.
import_steam_folder() {
  local source="$1" symlink_files staging cleanup

  test -n "$(find "$source" -mindepth 1 -maxdepth 1 -print -quit)" \
    || die "The game folder is empty: $source"
  # The copying walk is literal: no symlink ever leaves the chosen folder.
  symlink_files="$(find "$source" -type l 2>/dev/null | head -n 10)"
  if test -n "$symlink_files"; then
    die "Refusing a source that contains symlinks:\n$symlink_files"
  fi

  check_game_dir_chain
  if test -e "$GAME_DIR" && test -n "$(find "$GAME_DIR" -mindepth 1 -maxdepth 1 -print -quit)"; then
    die "The managed game destination is not empty: $GAME_DIR. Move it aside before importing."
  fi
  mkdir -p "$(dirname "$GAME_DIR")"
  check_game_dir_chain
  # Copy into a staging directory inside the prefix, re-scan the copy, and only
  # then move it into place, so a source that changes mid-copy never reaches
  # the managed game destination with a symlink in it.
  sweep_stale "$TRACKMANIA_MAC_PREFIX" '.import-staging.*'
  staging="$(mktemp -d "${TRACKMANIA_MAC_PREFIX}/.import-staging.XXXXXX")" || die "Cannot stage the import."
  printf -v cleanup 'rm -rf -- %q' "$staging"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  say "Copying $TRACKMANIA_MAC_TITLE game files into the prefix"
  say "  from: $source"
  say "  into: $GAME_DIR"
  ditto "$source" "$staging" || die "Cannot copy the game files."
  symlink_files="$(find "$staging" -type l 2>/dev/null | head -n 10)"
  if test -n "$symlink_files"; then
    die "Refusing a copy that contains symlinks:\n$symlink_files"
  fi
  test -f "${staging}/${GAME_EXE#"${GAME_DIR}"/}" \
    || die "Copied, but the game executable is missing at $GAME_EXE."
  chmod 755 "$staging"
  check_game_dir_chain
  if test -d "$GAME_DIR"; then
    rmdir "$GAME_DIR" || die "The managed game destination is not empty: $GAME_DIR."
  fi
  mv "$staging" "$GAME_DIR" || die "Cannot move the game files into place."
  trap - EXIT
  ensure_title_icon || warn "Could not extract the game icon for the hub."
  say "Copied. Finish the Windows-side prerequisites with:"
  say "  TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac directx"
  say "  TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac d9vk"
  say "Then verify with TRACKMANIA_MAC_TITLE=$TRACKMANIA_MAC_TITLE src/shell/cli/trackmania-mac doctor."
}
