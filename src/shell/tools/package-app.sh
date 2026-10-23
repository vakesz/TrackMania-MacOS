#!/bin/bash
# Native hub .app packager. No Homebrew, Wine build, or Rosetta is required.
# Signing: set TMNF_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"
# for distribution; otherwise ad-hoc signs. Notarization of the zip is opt-in
# via TMNF_NOTARIZE=1 with TMNF_NOTARY_PROFILE naming a notarytool keychain
# profile (xcrun notarytool store-credentials); no password is passed on argv.
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

# shellcheck source=src/shell/lib/apps/payload.sh
. "${PROJECT_DIR}/src/shell/lib/apps/payload.sh"
# shellcheck source=src/shell/lib/plist.sh
. "${PROJECT_DIR}/src/shell/lib/plist.sh"

# Scratch directories removed on any exit, including die().
CLEANUP_DIRS=()
cleanup() {
  local dir
  for dir in "${CLEANUP_DIRS[@]}"; do rm -rf "$dir"; done
}
trap cleanup EXIT

TITLES_ART_DIR="${PROJECT_DIR}/assets/titles"
TITLE_ART_PX=512

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
  prune_bundled_python "${launcher}/python"
  test -x "${launcher}/python/bin/python3" \
    || die "Bundled Python was not staged into the hub."
  PYTHONDONTWRITEBYTECODE=1 "${launcher}/python/bin/python3" \
    -c 'import hashlib, ssl, sys, urllib.request, zipfile; assert sys.version_info >= (3, 9)' \
    || die "Bundled Python cannot run after staging."
}

prune_bundled_python() {
  # The native hub invokes only python3 and the standard library. Keep that
  # runtime intact, but do not ship Python's compiler headers, pip bootstrap,
  # Tk/IDLE, developer metadata, or precompiled bytecode. The launcher sets
  # PYTHONDONTWRITEBYTECODE, so a hub action cannot later alter the app seal.
  local root="$1" version stdlib leftover
  version="${BUNDLED_PYTHON_VERSION%.*}"
  stdlib="${root}/lib/python${version}"
  test -d "$stdlib" || die "Bundled Python standard library is missing: $stdlib"

  rm -rf "${root}/include" "${root}/share" "${root}/lib/pkgconfig" \
    "${stdlib}/config-${version}-darwin" "${stdlib}/ensurepip" \
    "${stdlib}/idlelib" "${stdlib}/lib2to3" "${stdlib}/pydoc_data" \
    "${stdlib}/site-packages" "${stdlib}/tkinter"
  # Tcl/Tk and its packages carry version numbers; glob so a Python bump
  # cannot silently ship them.
  (
    shopt -s nullglob
    rm -rf "${root}/lib/"{itcl,tcl,tdbc,thread,tk,Tix}* \
      "${root}/lib/"lib{tcl,tk}*.dylib "${stdlib}/lib-dynload/"_tkinter*.so
  )
  leftover="$(find "${root}/lib" -maxdepth 1 \( -iname '*tcl*' -o -iname '*tk*' \) ; \
    find "${stdlib}/lib-dynload" -iname '*tkinter*')"
  test -z "$leftover" || die "Tcl/Tk files survived pruning: $leftover"
  find "$root" -type d -name '__pycache__' -prune -exec rm -rf {} +
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

svg_to_png() {
  local source="$1" side="$2" out="$3"
  sips -s format png -z "$side" "$side" "$source" --out "$out" >/dev/null \
    || die "Cannot rasterize $source"
}

build_icns() {
  local source="$1" out="$2" work iconset master name side
  test -f "$source" || die "Icon source is missing: $source"
  work="$(mktemp -d)" || die "Cannot stage the icon."
  CLEANUP_DIRS+=("$work")
  iconset="${work}/AppIcon.iconset"
  mkdir -p "$iconset"
  master="${iconset}/icon_512x512@2x.png"
  svg_to_png "$source" 1024 "$master"
  while IFS=' ' read -r name side; do
    test "$name" = "icon_512x512@2x.png" && continue
    sips -z "$side" "$side" "$master" --out "${iconset}/${name}" >/dev/null \
      || die "Cannot generate icon size: $name"
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
  iconutil -c icns "$iconset" -o "$out" || die "Cannot compile icon: $out"
  test -s "$out" || die "Icon output is empty: $out"
}

stage_hub_icon() {
  build_icns "$HUB_ICON_SOURCE" "${1}/Resources/AppIcon.icns"
}

# Rasterize assets/titles/<id>.svg into <launcher>/assets/titles/<id>.png.
# The SVG set must match config/titles/*.json exactly.
stage_title_art() {
  local launcher="$1" config svg id out
  out="${launcher}/assets/titles"
  mkdir -p "$out"
  for config in "${PROJECT_DIR}/config/titles/"*.json; do
    id="$(basename "$config" .json)"
    test -f "${TITLES_ART_DIR}/${id}.svg" \
      || die "Title '${id}' has no art: ${TITLES_ART_DIR}/${id}.svg"
  done
  for svg in "${TITLES_ART_DIR}/"*.svg; do
    test -f "$svg" || die "No title art found in ${TITLES_ART_DIR}"
    id="$(basename "$svg" .svg)"
    [[ "$id" =~ ^[a-z0-9_-]+$ ]] || die "Invalid title art name: $id"
    test -f "${PROJECT_DIR}/config/titles/${id}.json" \
      || die "Title art ${id}.svg has no config/titles/${id}.json"
    svg_to_png "$svg" "$TITLE_ART_PX" "${out}/${id}.png"
  done
}

build_appbundle() {
  local contents launcher binary bundle_id name version
  contents="${APP_DIR}/Contents"
  rm -rf "$APP_DIR"
  rm -rf "${DIST_DIR}/${HUB_NAME}.app"
  mkdir -p "${contents}/MacOS" "${contents}/Resources"
  launcher="${contents}/Resources/launcher"
  stage_launcher_payload "$PROJECT_DIR" "$launcher"
  stage_bundled_python "$launcher"
  stage_title_art "$launcher"
  stage_hub_icon "$contents"
  binary="$(build_hub_binary)"
  cp "$binary" "${contents}/MacOS/${HUB_NAME}"
  [[ "$BUNDLE_ID" =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid bundle identifier: $BUNDLE_ID"
  bundle_id="$(xml_escape "$BUNDLE_ID")"
  name="$(xml_escape "$HUB_NAME")"
  version="$(xml_escape "$VERSION")"
  cat >"${contents}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>${name}</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>${bundle_id}</string>
  <key>CFBundleName</key><string>${name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${version}</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.games</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  touch "$APP_DIR"
  say "Built ${APP_DIR}"
}

sign_one() {
  local target="$1"
  if test -z "${TMNF_SIGN_IDENTITY:-}"; then
    codesign --force --sign - "$target" || die "Ad-hoc signing failed: $target"
  else
    codesign --force --options runtime --timestamp --sign "$TMNF_SIGN_IDENTITY" "$target" \
      || die "Developer ID signing failed: $target"
  fi
  codesign --verify --strict "$target" || die "Signature verification failed: $target"
}

# Sign every nested Mach-O (deepest and libraries first) before the bundle;
# the bundled python3 needs no entitlements since all code shares our identity.
sign_bundled_machos() {
  local root="$1" path
  while IFS= read -r path; do
    file -b "$path" | grep -q 'Mach-O' && sign_one "$path"
  done < <(find "$root" -type f \( -name '*.so' -o -name '*.dylib' \) \
             | awk -F/ '{print NF, $0}' | sort -rn | cut -d' ' -f2-)
  while IFS= read -r path; do
    file -b "$path" | grep -q 'Mach-O' && sign_one "$path"
  done < <(find "$root" -type f -perm -u+x ! -name '*.so' ! -name '*.dylib')
  return 0
}

sign_appbundle() {
  local identity="${TMNF_SIGN_IDENTITY:-}"
  sign_bundled_machos "${APP_DIR}/Contents/Resources/launcher/python"
  sign_one "${APP_DIR}/Contents/MacOS/${HUB_NAME}"
  sign_one "$APP_DIR"
  if test -z "$identity"; then
    say "Ad-hoc signed ${APP_DIR} (no Developer ID; Gatekeeper will warn on download)."
  else
    say "Signed ${APP_DIR} with ${identity}"
  fi
  codesign --verify --deep --strict "$APP_DIR" || die "Signature verification failed."
}

build_zip() {
  local zip="${DIST_DIR}/${ZIP_NAME}"
  mkdir -p "$DIST_DIR"
  rm -f "$zip"
  test -d "$APP_DIR" || die "Build the app bundle first."
  # ditto preserves symlinks; --norsrc omits AppleDouble Finder metadata that
  # normal unzip(1) would materialize inside the sealed app and invalidate its
  # signature. --keepParent keeps the top-level .app folder inside the archive.
  ditto --norsrc -c -k --keepParent "$APP_DIR" "$zip" || die "App archive creation failed."
  say "Wrote $zip"
  if test "${TMNF_NOTARIZE:-0}" = 1; then
    local result
    test -n "${TMNF_NOTARY_PROFILE:-}" || die "Set TMNF_NOTARY_PROFILE for notarization."
    test -n "${TMNF_SIGN_IDENTITY:-}" || die "Notarization requires TMNF_SIGN_IDENTITY."
    result="$(xcrun notarytool submit "$zip" --keychain-profile "$TMNF_NOTARY_PROFILE" --wait 2>&1)" \
      || { printf '%s\n' "$result" >&2; die "Notarization failed."; }
    printf '%s\n' "$result"
    grep -q 'status: Accepted' <<<"$result" || die "Notarization was not accepted."
    # Stapling modifies the bundle, so re-archive after stapling.
    xcrun stapler staple "$APP_DIR" || die "Stapling failed."
    xcrun stapler validate "$APP_DIR" || die "Staple validation failed."
    codesign --verify --deep --strict "$APP_DIR" || die "Signature broken by stapling."
    ditto --norsrc -c -k --keepParent "$APP_DIR" "$zip" || die "App re-archive failed."
    say "Notarized, stapled and re-archived $zip"
  else
    say "Skipped notarization (TMNF_NOTARIZE=1 to enable)."
  fi
  (cd "$DIST_DIR" && shasum -a 256 "$ZIP_NAME" >"${ZIP_NAME}.sha256")
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  command="${1:-all}"
  case "$command" in
    appbundle) build_appbundle; sign_appbundle ;;
    zip) build_appbundle; sign_appbundle; build_zip ;;
    all) build_appbundle; sign_appbundle; build_zip ;;
    -h|--help) say 'Usage: package-app.sh [appbundle|zip|all]' ;;
    *) die "Unknown package-app command: $command" ;;
  esac
fi
