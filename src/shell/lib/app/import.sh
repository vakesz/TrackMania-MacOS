#!/bin/bash
# Disk-image import for retail TrackMania titles (United, Original, Canyon, Turbo).
# Local owned installers only. Never stores serial keys. Provisional: full
# installer flags and patch chains land once user images are inventoried.

import_disk_image() {
  local image="$1"
  local mount cleanup
  local top_entries

  validate_launch_options
  test -n "${image:-}" || die "Usage: tmnf import <disk-image.iso|.dmg|setup.exe>"
  test -f "$image" || die "Disk image not found: $image"
  case "$image" in
    *.iso|*.ISO|*.dmg|*.DMG|*.exe|*.EXE) ;;
    *) die "Unsupported image type (use .iso, .dmg or a retail setup .exe): $image" ;;
  esac
  if test "$TMNF_TITLE" = tmnf; then
    die "TMNF uses the free download flow (make install); import is for United, Original, Canyon and Turbo."
  fi
  require_owned_prefix || die "A managed Wine prefix is required."
  validate_managed_layout || die "Unsafe installation layout."

  case "$image" in
    *.dmg|*.DMG|*.iso|*.ISO)
      have hdiutil || die "hdiutil is required to mount disk images."
      mount="$(mktemp -d "${TMNF_CACHE}/import-mount.XXXXXX")" || die "Cannot stage mount point."
      rmdir "$mount" || die "Cannot stage mount point."
      printf -v cleanup 'hdiutil detach %q >/dev/null 2>&1 || true; rm -rf -- %q' "$mount" "$mount"
      # shellcheck disable=SC2064
      trap "$cleanup" EXIT
      hdiutil attach -readonly -nobrowse -mountpoint "$mount" "$image" \
        || die "Cannot mount disk image: $image"
      say "Mounted $image at $mount"
      top_entries="$(find "$mount" -mindepth 1 -maxdepth 1 -print 2>/dev/null | head -n 20)"
      say "Top-level contents:"
      printf '%s\n' "$top_entries"
      say "Provisional importer: installer inventory for $TMNF_TITLE is pending user images."
      say "Next: identify the retail setup .exe above, then run it with:"
      say "  TMNF_TITLE=$TMNF_TITLE src/shell/cli/tmnf import \"$mount/<Setup>.exe\""
      say "Serial keys are entered interactively and never stored."
      hdiutil detach "$mount" >/dev/null 2>&1 || true
      trap - EXIT
      rm -rf "$mount"
      ;;
    *.exe|*.EXE)
      say "Retail installer selected: $image"
      say "Provisional importer: automated flags for $TMNF_TITLE are not pinned yet."
      say "Run the installer interactively in the managed prefix with:"
      say "  TMNF_TITLE=$TMNF_TITLE WINEPREFIX=\"$TMNF_PREFIX\" \"\$(src/shell/cli/tmnf doctor >/dev/null 2>&1; echo wine)\" \"$image\""
      say "Then run src/shell/cli/tmnf doctor to verify. Report the installer name, flags and"
      say "installed exe path so the title registry can be pinned."
      ;;
  esac
}
