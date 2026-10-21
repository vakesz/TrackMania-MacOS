#!/bin/bash
# Native hub .app packager. No Homebrew, Wine build, or Rosetta is required.
# Signing: set TMNF_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"
# for distribution; otherwise ad-hoc signs. Notarization of the zip is opt-in
# via TMNF_NOTARIZE=1 with APPLE_ID/TMNF_TEAM_ID/TMNF_APP_PASSWORD set.
set -euo pipefail

PROJECT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)"
VERSION="$(cat "${PROJECT_DIR}/VERSION")"
HUB_NAME="TrackMania Mac"
APP_DIR="${PROJECT_DIR}/.build/app/${HUB_NAME}.app"
DIST_DIR="${PROJECT_DIR}/.build/dist"
ZIP_NAME="TrackMania-Mac-${VERSION}.app.zip"
BUNDLE_ID="${TMNF_HUB_BUNDLE_ID:-local.tmnf.hub}"
BUNDLED_PYTHON_VERSION="${TMNF_BUNDLED_PYTHON_VERSION:-3.12.14}"
HUB_ICON_SOURCE="${PROJECT_DIR}/assets/branding/trackmania-mac-icon.svg"

# shellcheck source=src/shell/lib/app/launcher_payload.sh
. "${PROJECT_DIR}/src/shell/lib/app/launcher_payload.sh"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

bundled_python_root() {
  local python root
  if test -n "${TMNF_BUNDLED_PYTHON_ROOT:-}"; then
    root="${TMNF_BUNDLED_PYTHON_ROOT}"
  else
    command -v uv >/dev/null 2>&1 \
      || die "uv is required to bundle Python (or set TMNF_BUNDLED_PYTHON_ROOT)."
    uv python install --no-progress "$BUNDLED_PYTHON_VERSION" >&2
    python="$(uv python find --no-project --managed-python --resolve-links "$BUNDLED_PYTHON_VERSION")" \
      || die "Cannot find the managed Python $BUNDLED_PYTHON_VERSION installed by uv."
    root="$("$python" -c 'import sys; print(sys.base_prefix)')" \
      || die "Cannot resolve the managed Python runtime root."
  fi
  test -x "${root}/bin/python3" \
    || die "Bundled Python root must contain bin/python3: $root"
  "${root}/bin/python3" -c 'import hashlib, ssl, sys; assert sys.version_info >= (3, 9)' \
    || die "Bundled Python is not a usable Python 3.9+ runtime: $root"
  printf '%s\n' "$root"
}

stage_bundled_python() {
  local launcher root
  launcher="$1"
  root="$(bundled_python_root)"
  ditto "$root" "${launcher}/python"
  test -x "${launcher}/python/bin/python3" \
    || die "Bundled Python was not staged into the hub."
  "${launcher}/python/bin/python3" -c 'import hashlib, ssl, sys; assert sys.version_info >= (3, 9)' \
    || die "Bundled Python cannot run after staging."
}

build_hub_binary() {
  local bin_dir
  swift build --package-path "${PROJECT_DIR}/src/swift" -c release \
    --scratch-path "${PROJECT_DIR}/.build/swift" >&2
  bin_dir="$(swift build --package-path "${PROJECT_DIR}/src/swift" -c release \
    --scratch-path "${PROJECT_DIR}/.build/swift" --show-bin-path)"
  test -x "${bin_dir}/TrackManiaHub" \
    || die "Swift build did not produce TrackManiaHub: ${bin_dir}/TrackManiaHub"
  printf '%s\n' "${bin_dir}/TrackManiaHub"
}

stage_hub_icon() {
  local contents="$1" iconset master name side

  test -f "$HUB_ICON_SOURCE" || die "Hub icon source is missing: $HUB_ICON_SOURCE"
  iconset="${contents}/Resources/AppIcon.iconset"
  master="${iconset}/icon_512x512@2x.png"
  mkdir -p "$iconset"
  sips -s format png -z 1024 1024 "$HUB_ICON_SOURCE" --out "$master" >/dev/null \
    || die "Cannot rasterize the hub icon source."
  while IFS=' ' read -r name side; do
    test "$name" = "icon_512x512@2x.png" && continue
    sips -z "$side" "$side" "$master" --out "${iconset}/${name}" >/dev/null \
      || die "Cannot generate hub icon size: $name"
  done <<'EOF'
icon_16x16.png 16
icon_16x16@2x.png 32
icon_32x32.png 32
icon_32x32@2x.png 64
icon_128x128.png 128
icon_128x128@2x.png 256
icon_256x256.png 256
icon_256x256@2x.png 512
icon_512x512.png 512
icon_512x512@2x.png 1024
EOF
  iconutil -c icns "$iconset" -o "${contents}/Resources/AppIcon.icns" \
    || die "Cannot compile the hub icon."
  rm -rf "$iconset"
  test -s "${contents}/Resources/AppIcon.icns" || die "Hub icon output is empty."
}

build_appbundle() {
  local contents launcher binary
  contents="${APP_DIR}/Contents"
  rm -rf "$APP_DIR"
  rm -rf "${DIST_DIR}/${HUB_NAME}.app"
  mkdir -p "${contents}/MacOS" "${contents}/Resources"
  launcher="${contents}/Resources/launcher"
  stage_launcher_payload "$PROJECT_DIR" "$launcher"
  stage_bundled_python "$launcher"
  stage_hub_icon "$contents"
  binary="$(build_hub_binary)"
  cp "$binary" "${contents}/MacOS/${HUB_NAME}"
  cat >"${contents}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>TrackMania Mac</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleName</key><string>${HUB_NAME}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  touch "$APP_DIR"
  say "Built ${APP_DIR}"
}

sign_appbundle() {
  local identity="${TMNF_SIGN_IDENTITY:-}"
  if test -z "$identity"; then
    codesign --force --sign - "$APP_DIR" || die "Ad-hoc signing failed."
    say "Ad-hoc signed ${APP_DIR} (no Developer ID; Gatekeeper will warn on download)."
  else
    codesign --force --options runtime --timestamp \
      --sign "$identity" "$APP_DIR" \
      || die "Developer ID signing failed."
    say "Signed ${APP_DIR} with ${identity}"
  fi
  codesign --verify --deep --strict "$APP_DIR" || die "Signature verification failed."
}

build_zip() {
  local zip="${DIST_DIR}/${ZIP_NAME}"
  mkdir -p "$DIST_DIR"
  rm -f "$zip"
  test -d "$APP_DIR" || die "Build the app bundle first."
  # ditto preserves symlinks and the code signature; plain zip(1) does not.
  # --keepParent keeps the top-level .app folder inside the archive.
  ditto -c -k --keepParent "$APP_DIR" "$zip" || die "App archive creation failed."
  say "Wrote $zip"
  if test "${TMNF_NOTARIZE:-0}" = 1; then
    test -n "${APPLE_ID:-}" || die "Set APPLE_ID for notarization."
    test -n "${TMNF_TEAM_ID:-}" || die "Set TMNF_TEAM_ID for notarization."
    test -n "${TMNF_APP_PASSWORD:-}" || die "Set TMNF_APP_PASSWORD for notarization."
    xcrun notarytool submit "$zip" --apple-id "$APPLE_ID" --team-id "$TMNF_TEAM_ID" \
      --password "$TMNF_APP_PASSWORD" --wait || die "Notarization failed."
    # Stapling modifies the bundle, so re-archive after stapling.
    xcrun stapler staple "$APP_DIR" || die "Stapling failed."
    ditto -c -k --keepParent "$APP_DIR" "$zip" || die "App re-archive failed."
    say "Notarized, stapled and re-archived $zip"
  else
    say "Skipped notarization (TMNF_NOTARIZE=1 to enable)."
  fi
  (cd "$DIST_DIR" && shasum -a 256 "$ZIP_NAME" >"${ZIP_NAME}.sha256")
}

command="${1:-all}"
case "$command" in
  appbundle) build_appbundle; sign_appbundle ;;
  zip) build_appbundle; sign_appbundle; build_zip ;;
  all) build_appbundle; sign_appbundle; build_zip ;;
  -h|--help) say 'Usage: package-app.sh [appbundle|zip|all]' ;;
  *) die "Unknown package-app command: $command" ;;
esac
