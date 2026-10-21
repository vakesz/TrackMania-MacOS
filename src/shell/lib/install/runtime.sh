#!/bin/bash
# Complete runtime installation. Sourced by install.sh after common.sh.

runtime_is_complete() {
  tmnf_python -m tmnf.install.runtime_manifest verify "$1" "$RUNTIME_VERSION"
}

install_wine_runtime() {
  local built="${TMNF_PROJECT_DIR}/.build/release/runtime"
  local archive="${TMNF_RUNTIME_ARCHIVE:-}"
  local expected="${TMNF_RUNTIME_SHA256:-}"
  local staging source cleanup status

  is_safe_managed_path "$TMNF_RUNTIME" || die "Unsafe runtime path: $TMNF_RUNTIME"
  if test -e "$TMNF_RUNTIME" || test -L "$TMNF_RUNTIME"; then
    runtime_is_complete "$TMNF_RUNTIME" \
      || die "Existing runtime is damaged or not owned. Move it aside while the game is closed: $TMNF_RUNTIME"
    if test -z "$archive"; then
      say "Runtime $RUNTIME_VERSION is installed at $TMNF_RUNTIME"
      return
    fi
  fi
  mkdir -p "$(dirname "$TMNF_RUNTIME")" "$TMNF_CACHE" || die "Cannot create runtime directories."
  # Keep cleanup local to this operation, including a failed checksum or extraction.
  (
    set -e
    staging="$(mktemp -d "$(dirname "$TMNF_RUNTIME")/.runtime-install.XXXXXX")" \
      || die "Cannot create runtime staging directory."
    # Bash 3.2 unwinds function locals before EXIT on errexit. Capture the path.
    printf -v cleanup 'rm -rf -- %q' "$staging"
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    trap 'exit 1' HUP INT TERM
    if test -z "$archive" && { test -e "$built" || test -L "$built"; }; then
      runtime_is_complete "$built" || die "The local source build is incomplete or damaged."
      say "Installing the local source build"
      cp -pRP "$built" "${staging}/runtime" || die "Cannot copy the local runtime."
    else
      if test -z "$archive"; then
        archive="${TMNF_CACHE}/${RUNTIME_ARCHIVE_NAME}"
        curl --fail --location --retry 3 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
          --output "${staging}/release.sha256" \
          "${RUNTIME_RELEASE_URL}/${RUNTIME_ARCHIVE_NAME}.sha256" \
          || die "Cannot download runtime release $RUNTIME_VERSION. Run make build, or supply TMNF_RUNTIME_ARCHIVE and TMNF_RUNTIME_SHA256."
        expected="$(awk 'NR==1 {print $1}' "${staging}/release.sha256")"
        [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid release checksum."
        expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
        download_verified "${RUNTIME_RELEASE_URL}/${RUNTIME_ARCHIVE_NAME}" "$archive" "$expected" \
          || die "Cannot download the runtime archive."
      else
        [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Set TMNF_RUNTIME_SHA256 to the downloaded archive's published checksum."
        expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
        verify_file "$archive" "$expected" || die "Runtime archive SHA-256 mismatch."
      fi
      tmnf_python -m tmnf.install.runtime_manifest extract "$archive" "$staging" "$expected" \
        || die "Cannot extract the runtime archive safely."
    fi
    source="${staging}/runtime"
    runtime_is_complete "$source" || die "The runtime is incomplete or has the wrong version."
    if test ! -e "$TMNF_RUNTIME" && test ! -L "$TMNF_RUNTIME"; then
      without_operation_lock "${source}/bin/wine" --version || die "Wine runtime probe failed."
      without_operation_lock "${source}/bin/x87sidecar" --probe || die "x87sidecar runtime probe failed."
    fi
    tmnf_python -m tmnf.install.runtime_manifest publish "$source" "$TMNF_RUNTIME" "$RUNTIME_VERSION" \
      || die "Cannot publish runtime. Existing destinations are never replaced."
  )
  status=$?
  test "$status" -eq 0 || return "$status"
  say "Installed Wine, D9VK, MoltenVK and patched x87sidecar."
}
