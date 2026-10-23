#!/bin/bash
# Competition Patch and CoreMod via TMLoader. Sourced by install.sh after common.sh.

configure_competition_patch() {
  local product_dir
  local profile_dir

  product_dir="${TMLOADER_DIR}/database/TmForever/products/TmForever"
  profile_dir="${TMLOADER_DIR}/database/TmForever/profiles"
  mkdir -p "$product_dir" "$profile_dir"
  printf '%s\n' 'install: "C:/Program Files (x86)/TmNationsForever"' >"${product_dir}/settings.yaml"
  printf '%s\n' \
    'program:' \
    '  id: TmForever' \
    "  version: ${TMLOADER_GAME_VERSION}" \
    'mods:' \
    '  - id: Competition Patch' \
    '  - id: CoreMod' >"${profile_dir}/competition.yaml"
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

  extract_dir="$(mktemp -d "${TMNF_CACHE}/tmloader-extract.XXXXXX")"
  printf -v cleanup 'rm -rf -- %q' "$extract_dir"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  unzip -q "$TMLOADER_ARCHIVE" -d "$extract_dir"
  verify_file "${extract_dir}/TMLoader.exe" "$TMLOADER_EXE_SHA256" \
    || die "ModLoader executable verification failed."
  verify_file "${extract_dir}/database/TmForever/products/Competition Patch/${TMCOMP_VERSION}/TMCompPatch.dll" "$TMCOMP_DLL_SHA256" \
    || die "Competition Patch verification failed."
  verify_file "${extract_dir}/database/TmForever/products/TmForever/${TMLOADER_GAME_VERSION}/TmForever.exe" "$TMLOADER_GAME_SHA256" \
    || die "ModLoader compatibility runtime verification failed."
  verify_file "${extract_dir}/database/TmForever/products/CoreMod/${TMLOADER_COREMOD_VERSION}/CoreMod.dll" "$TMLOADER_COREMOD_SHA256" \
    || die "CoreMod verification failed."

  rm -rf "$TMLOADER_DIR"
  mkdir -p "$(dirname "$TMLOADER_DIR")"
  mv "$extract_dir" "$TMLOADER_DIR"

  configure_competition_patch

  competition_patch_is_current || die "Competition Patch installation failed."
  say "Installed Competition Patch $TMCOMP_VERSION with ModLoader bundle $TMLOADER_BUNDLE_VERSION"
)
