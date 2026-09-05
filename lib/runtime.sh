#!/bin/bash
# Complete runtime installation. Sourced by install.sh after common.sh.

runtime_is_complete() {
  local root="$1"
  test -f "${root}/.mactmnf-runtime" \
    && test "$(cat "${root}/.mactmnf-runtime")" = "$RUNTIME_VERSION" \
    && test -x "${root}/bin/wine" && test -x "${root}/bin/wineserver" \
    && test -x "${root}/bin/x87sidecar" && test -f "${root}/lib/d9vk/d3d9.dll" \
    && test -f "${root}/config/dxvk.conf" && test -f "${root}/config/Default.SystemConfig.Gbx" \
    && test -f "${root}/lib/wine/i386-windows/d3dx9_30.dll" \
    && test -f "${root}/lib/wine/i386-windows/d3dx9_43.dll" && test -d "${root}/licenses"
}

install_wine_runtime() {
  local built="${MACTMNF_PROJECT_DIR}/.build/release/runtime"
  local archive="${MACTMNF_RUNTIME_ARCHIVE:-}"
  local expected="${MACTMNF_RUNTIME_SHA256:-}"
  local staging entry source

  if test -z "$archive" && runtime_is_complete "$MACTMNF_RUNTIME"; then
    say "Runtime $RUNTIME_VERSION is installed at $MACTMNF_RUNTIME"
    return
  fi
  is_safe_managed_path "$MACTMNF_RUNTIME" || die "Unsafe runtime path: $MACTMNF_RUNTIME"
  if test -e "$MACTMNF_RUNTIME"; then
    die "Runtime destination already exists. Move it aside while the game is closed: $MACTMNF_RUNTIME"
  fi
  mkdir -p "$(dirname "$MACTMNF_RUNTIME")" "$MACTMNF_CACHE"
  staging="$(mktemp -d "$(dirname "$MACTMNF_RUNTIME")/.runtime-install.XXXXXX")"
  # Keep cleanup local to this operation, including a failed checksum or extraction.
  (
    trap 'rm -rf "$staging"' EXIT
    if test -z "$archive" && runtime_is_complete "$built"; then
      say "Installing the local source build"
      cp -R "$built" "${staging}/runtime"
    else
      if test -z "$archive"; then
        archive="${MACTMNF_CACHE}/${RUNTIME_ARCHIVE_NAME}"
        curl --fail --location --retry 3 --output "${staging}/release.sha256" \
          "${RUNTIME_RELEASE_URL}/${RUNTIME_ARCHIVE_NAME}.sha256" \
          || die "Cannot download runtime release $RUNTIME_VERSION. Run make build, or supply MACTMNF_RUNTIME_ARCHIVE and MACTMNF_RUNTIME_SHA256."
        expected="$(awk 'NR==1 {print $1}' "${staging}/release.sha256")"
        test "${#expected}" -eq 64 || die "Invalid release checksum."
        download_verified "${RUNTIME_RELEASE_URL}/${RUNTIME_ARCHIVE_NAME}" "$archive" "$expected"
      else
        test "${#expected}" -eq 64 || die "Set MACTMNF_RUNTIME_SHA256 to the downloaded archive's published checksum."
        verify_file "$archive" "$expected" || die "Runtime archive SHA-256 mismatch."
      fi
      tar -tf "$archive" >"${staging}/entries"
      while IFS= read -r entry; do
        case "$entry" in
          /*|../*|*/../*|*/..) die "Unsafe runtime archive path: $entry" ;;
          runtime|runtime/*) ;;
          *) die "Unexpected runtime archive entry: $entry" ;;
        esac
      done <"${staging}/entries"
      tar -xf "$archive" -C "$staging"
    fi
    source="${staging}/runtime"
    runtime_is_complete "$source" || die "The runtime is incomplete or has the wrong version."
    test -f "${source}/SHA256SUMS" || die "The runtime has no file manifest."
    (cd "$source" && shasum -a 256 -c SHA256SUMS >/dev/null)
    "${source}/bin/wine" --version
    "${source}/bin/x87sidecar" --probe
    mv "$source" "$MACTMNF_RUNTIME"
  )
  say "Installed Wine, D9VK, MoltenVK and patched x87sidecar."
}
