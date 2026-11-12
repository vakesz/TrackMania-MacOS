#!/bin/bash
# Competition Patch and CoreMod via TMLoader. Sourced by install.sh after common.sh.

configure_competition_patch() {
  test -n "$TMNF_TITLE_GAME_DIR_NAME" || die "The title registry has no game_dir_name."
  mkdir -p "$(dirname "$TMLOADER_SETTINGS")" "$(dirname "$TMLOADER_PROFILE_FILE")"
  printf 'install: "C:/Program Files (x86)/%s"\n' "$TMNF_TITLE_GAME_DIR_NAME" >"$TMLOADER_SETTINGS"
  printf '%s\n' \
    'program:' \
    "  id: ${TMLOADER_PROGRAM_ID}" \
    "  version: ${TMLOADER_GAME_VERSION}" \
    'mods:' \
    '  - id: Competition Patch' \
    '  - id: CoreMod' >"$TMLOADER_PROFILE_FILE"
}

# Put NEW in place of TMLOADER_DIR. The old directory is moved aside first and
# restored if the new one cannot be moved in, so a failure never loses it.
swap_tmloader_dir() {
  local new="$1" old=""
  # Without the registry's ModLoader layout TMLOADER_DIR would be the prefix itself.
  test -n "$TMLOADER_PREFIX_DIR" || die "The ModLoader layout is not loaded."
  mkdir -p "$(dirname "$TMLOADER_DIR")" || die "Cannot create $(dirname "$TMLOADER_DIR")"
  sweep_stale "$(dirname "$TMLOADER_DIR")" '.tmloader-old.*'
  if test -e "$TMLOADER_DIR"; then
    old="$(mktemp -d "$(dirname "$TMLOADER_DIR")/.tmloader-old.XXXXXX")" \
      || die "Cannot stage the previous ModLoader."
    mv "$TMLOADER_DIR" "$old/TMLoader" || die "Cannot move the previous ModLoader aside."
  fi
  if ! mv "$new" "$TMLOADER_DIR"; then
    if test -n "$old"; then
      mv "$old/TMLoader" "$TMLOADER_DIR" && rm -rf -- "$old"
    fi
    die "Cannot install ModLoader into $TMLOADER_DIR"
  fi
  test -z "$old" || rm -rf -- "$old"
}

install_competition_patch() (
  local extract_dir entry cleanup

  if competition_patch_is_current; then
    configure_competition_patch
    say "Competition Patch $TMCOMP_VERSION is already installed"
    return
  fi

  download_verified "$TMLOADER_URL" "$TMLOADER_ARCHIVE" "$TMLOADER_SHA256"
  is_safe_managed_path "$TMLOADER_DIR" || die "Unsafe ModLoader path: $TMLOADER_DIR"

  while IFS= read -r entry; do
    case "$entry" in
      /*|../*|*/../*|*/..) die "Unsafe path in ModLoader archive: $entry" ;;
    esac
  done < <(unzip -Z1 "$TMLOADER_ARCHIVE")

  sweep_stale "$TMNF_CACHE" '*-extract.*'
  extract_dir="$(mktemp -d "${TMNF_CACHE}/tmloader-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  unzip -q "$TMLOADER_ARCHIVE" -d "$extract_dir"
  verify_file "${extract_dir}/${TMLOADER_EXE_REL}" "$TMLOADER_EXE_SHA256" \
    || die "ModLoader executable verification failed."
  verify_file "${extract_dir}/${TMCOMP_DLL_REL}" "$TMCOMP_DLL_SHA256" \
    || die "Competition Patch verification failed."
  verify_file "${extract_dir}/${TMLOADER_GAME_EXE_REL}" "$TMLOADER_GAME_SHA256" \
    || die "ModLoader compatibility runtime verification failed."
  verify_file "${extract_dir}/${TMLOADER_COREMOD_DLL_REL}" "$TMLOADER_COREMOD_SHA256" \
    || die "CoreMod verification failed."

  swap_tmloader_dir "$extract_dir"

  configure_competition_patch

  competition_patch_is_current || die "Competition Patch installation failed."
  say "Installed Competition Patch $TMCOMP_VERSION with ModLoader bundle $TMLOADER_BUNDLE_VERSION"
)
