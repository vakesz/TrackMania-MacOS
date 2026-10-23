#!/bin/bash
# Complete runtime installation. Sourced by install.sh after common.sh.

runtime_is_complete() {
  tmnf_python -m tmnf.install.runtime_manifest verify "$1" "$RUNTIME_VERSION"
}

# auto installs a finished local build when present, else downloads; download
# always fetches the release runtime; build compiles it and installs the result.
validate_runtime_source() {
  # Unset means auto; an empty value is a mistake, not a default.
  case "${TMNF_RUNTIME_SOURCE-auto}" in
    auto|download|build) ;;
    *) die "Invalid TMNF_RUNTIME_SOURCE: use auto, download or build." ;;
  esac
  if test "${TMNF_RUNTIME_SOURCE-auto}" = build && test -n "${TMNF_RUNTIME_ARCHIVE:-}"; then
    die "TMNF_RUNTIME_ARCHIVE installs a downloaded runtime; it cannot be combined with TMNF_RUNTIME_SOURCE=build."
  fi
}

runtime_download_hint() {
  if test "${TMNF_RUNTIME_SOURCE-auto}" = auto; then
    printf 'Run make build, or supply TMNF_RUNTIME_ARCHIVE and TMNF_RUNTIME_SHA256.'
  else
    printf 'Check the connection, or supply TMNF_RUNTIME_ARCHIVE and TMNF_RUNTIME_SHA256.'
  fi
}

# Fetch, verify and safely unpack the release source archive into the cache.
# Runs inside install_wine_runtime's subshell: it uses its staging directory and
# cleanup trap, and sets the caller's tree.
extract_source_archive() {
  local name="$SOURCE_ARCHIVE_NAME"
  local archive="${TMNF_CACHE}/${name}"
  local destination="${TMNF_CACHE}/source/${RUNTIME_VERSION}"
  local expected stage marker

  case "$destination" in
    *[[:space:]]*) die "Build from a cache folder without spaces; Wine makefiles require this. Set TMNF_CACHE to another path (now: $TMNF_CACHE)." ;;
  esac
  curl --fail --location --retry 3 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
    --output "${staging}/source.sha256" "${RUNTIME_RELEASE_URL}/${name}.sha256" \
    || die "Cannot download the source archive checksum for release $RUNTIME_VERSION."
  expected="$(awk 'NR==1 {print $1}' "${staging}/source.sha256")"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid source archive checksum."
  expected="$(printf '%s' "$expected" | tr 'A-F' 'a-f')"
  tree="$destination"
  # Reuse trusts a marker file, not a re-hash of the tree, so that an interrupted
  # multi-hour build resumes. Forging it takes write access to this user's cache.
  # Upstream trees hold links the build materialises, so a missing tail is allowed
  # but escapes are not. The tree's links are re-checked, and a symlinked or non-regular marker is refused.
  if test -d "$destination" && test ! -L "$destination"; then
    marker="${destination}/.tmnf-source-sha256"
    test -f "$marker" && test ! -L "$marker" && test "$(cat "$marker")" = "$expected" \
      || die "The extracted source differs from release $RUNTIME_VERSION. Remove it to rebuild: $destination"
    tmnf_python -m tmnf.install.runtime_manifest links "$destination" --skip .build --allow-dangling \
      || die "The extracted source has unsafe links. Remove it to rebuild: $destination"
    say "Using extracted source $destination"
    return
  fi
  test ! -e "$destination" && test ! -L "$destination" \
    || die "Unexpected source path, remove it first: $destination"
  download_verified "${RUNTIME_RELEASE_URL}/${name}" "$archive" "$expected" \
    || die "Cannot download the source archive."
  mkdir -p "${TMNF_CACHE}/source" || die "Cannot create the source cache."
  stage="$(mktemp -d "${TMNF_CACHE}/source/.extract.XXXXXX")" || die "Cannot create source staging directory."
  printf -v cleanup '%s; rm -rf -- %q' "$cleanup" "$stage"
  # shellcheck disable=SC2064
  trap "$cleanup" EXIT
  tmnf_python -m tmnf.install.runtime_manifest extract "$archive" "$stage" "$expected" --root "$RELEASE_NAME" \
    || die "Cannot extract the source archive safely."
  tmnf_python -m tmnf.install.runtime_manifest links "${stage}/${RELEASE_NAME}" --allow-dangling \
    || die "The source archive has broken or escaping links."
  test -f "${stage}/${RELEASE_NAME}/src/shell/tools/build-runtime.sh" || die "The source archive has no runtime builder."
  marker="${stage}/${RELEASE_NAME}/.tmnf-source-sha256"
  rm -f -- "$marker"
  printf '%s\n' "$expected" >"$marker"
  # BSD mv would nest into an existing directory, so refuse one first.
  test ! -e "$destination" && test ! -L "$destination" || die "Unexpected source path, remove it first: $destination"
  mv "${stage}/${RELEASE_NAME}" "$destination" || die "Cannot publish the extracted source."
}

homebrew_bin_dirs() {
  printf '%s\n' /usr/local/bin /opt/homebrew/bin
}

# Compile the runtime, from this checkout or the release source archive, and
# set the caller's built to the result. Also runs inside the install subshell.
build_runtime_locally() {
  local script="${TMNF_PROJECT_DIR}/src/shell/tools/build-runtime.sh"
  local tree="$TMNF_PROJECT_DIR" dir build_path="$PATH"

  if test ! -f "$script"; then
    extract_source_archive
    script="${tree}/src/shell/tools/build-runtime.sh"
  fi
  # A GUI-launched hub has a minimal PATH that hides Homebrew. Each hit is
  # prepended, so the later entry in homebrew_bin_dirs wins.
  if ! command -v brew >/dev/null 2>&1; then
    while IFS= read -r dir; do
      if test -x "${dir}/brew"; then build_path="${dir}:${build_path}"; fi
    done < <(homebrew_bin_dirs)
  fi
  say "Building the Wine runtime from source (this takes a long time)"
  without_operation_lock env PATH="$build_path" /bin/bash "$script" \
    || die "The runtime build failed. It resumes when you run the installer again."
  built="${tree}/.build/release/runtime"
  test -e "$built" || die "The runtime build produced no runtime at $built"
}

install_wine_runtime() {
  local built="${TMNF_PROJECT_DIR}/.build/release/runtime"
  local archive="${TMNF_RUNTIME_ARCHIVE:-}"
  local expected="${TMNF_RUNTIME_SHA256:-}"
  local mode="${TMNF_RUNTIME_SOURCE-auto}"
  local staging source cleanup status

  validate_runtime_source
  is_safe_managed_path "$TMNF_RUNTIME" || die "Unsafe runtime path: $TMNF_RUNTIME"
  if test -e "$TMNF_RUNTIME" || test -L "$TMNF_RUNTIME"; then
    runtime_is_complete "$TMNF_RUNTIME" \
      || die "Existing runtime is damaged or not owned. Move it aside while the game is closed: $TMNF_RUNTIME"
    if test -z "$archive"; then
      if test "$mode" = auto; then
        say "Runtime $RUNTIME_VERSION is installed at $TMNF_RUNTIME"
      else
        say "Runtime $RUNTIME_VERSION is already installed at $TMNF_RUNTIME; remove it while the game is closed to reinstall."
      fi
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
    if test -z "$archive" && test "$mode" = build; then
      build_runtime_locally
    fi
    if test -z "$archive" && test "$mode" != download && { test -e "$built" || test -L "$built"; }; then
      runtime_is_complete "$built" || die "The local source build is incomplete or damaged."
      say "Installing the local source build"
      cp -pRP "$built" "${staging}/runtime" || die "Cannot copy the local runtime."
    else
      if test -z "$archive"; then
        archive="${TMNF_CACHE}/${RUNTIME_ARCHIVE_NAME}"
        curl --fail --location --retry 3 --connect-timeout 20 --speed-limit 1024 --speed-time 60 \
          --output "${staging}/release.sha256" \
          "${RUNTIME_RELEASE_URL}/${RUNTIME_ARCHIVE_NAME}.sha256" \
          || die "Cannot download runtime release $RUNTIME_VERSION. $(runtime_download_hint)"
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
