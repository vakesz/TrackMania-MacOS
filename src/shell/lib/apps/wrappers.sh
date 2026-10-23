#!/bin/bash
# Installed apps contain a small shell entry point; support files live outside the checkout.

# shellcheck source=src/shell/lib/apps/payload.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/apps/payload.sh"
# shellcheck source=src/shell/lib/apps/icons.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/apps/icons.sh"
# shellcheck source=src/shell/lib/plist.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/plist.sh"

# publish_file TEMP DEST — atomic rename of a file staged next to DEST.
publish_file() {
  mv -f "$1" "$2" || { rm -f "$1"; die "Cannot write $2"; }
}

# resolve_app_icon — print a temporary .icns for the wrappers: the cached game
# icon, else the bundled artwork converted once, else nothing.
resolve_app_icon() {
  local cache="${TMNF_SUPPORT}/icons/${TMNF_TITLE}.icns"
  local bundled="${TMNF_PROJECT_DIR}/assets/titles/${TMNF_TITLE}.png" out
  out="$(mktemp "${TMPDIR:-/tmp}/tmnf-icon.XXXXXX")" || return 0
  if test -f "$cache" && test ! -L "$cache" && cp "$cache" "$out"; then
    printf '%s\n' "$out"
  elif test -f "$bundled" && icon_tool --from-png "$bundled" "$out" 2>/dev/null && test -s "$out"; then
    printf '%s\n' "$out"
  else
    rm -f "$out"
  fi
}

check_app_target() {
  local path="$1" entry
  local contents="${path}/Contents"
  for entry in "$path" "$contents" "${contents}/MacOS" "${contents}/Resources" "${contents}/Info.plist" "${contents}/.tmnf-app" \
    "${contents}/MacOS/TMNF" "${contents}/Resources/AppIcon.icns"; do
    test ! -L "$entry" || die "Refusing a symlink in the managed app: $entry"
  done
  if test -e "$path" && test ! -f "${contents}/.tmnf-app"; then
    # Recognize the previous TMNF launcher when upgrading.
    if ! test -f "${contents}/Info.plist" || ! /usr/libexec/PlistBuddy \
      -c 'Print CFBundleIdentifier' "${contents}/Info.plist" 2>/dev/null | grep -Fxq local.tmnf.launcher; then
      die "An unrelated app already exists: $path"
    fi
  fi
}

write_app() {
  local path="$1" name="$2" action="$3" identifier="$4" icon="${5:-}"
  local contents="${path}/Contents" tmp
  check_app_target "$path"
  mkdir -p "${contents}/MacOS" "${contents}/Resources"
  # Re-check what mkdir may have raced with: no managed directory is a symlink.
  for tmp in "$path" "$contents" "${contents}/MacOS" "${contents}/Resources"; do
    test ! -L "$tmp" || die "Refusing a symlink in the managed app: $tmp"
  done
  # A missing icon only costs the Finder artwork.
  if test -n "$icon"; then
    tmp="$(mktemp "${contents}/Resources/.AppIcon.XXXXXX")" || die "Cannot stage the app icon."
    cp "$icon" "$tmp" || { rm -f "$tmp"; die "Cannot stage the app icon."; }
    publish_file "$tmp" "${contents}/Resources/AppIcon.icns"
  else
    rm -f "${contents}/Resources/AppIcon.icns"
    warn "No icon available for $name; using the generic app icon."
  fi
  tmp="$(mktemp "${contents}/.Info.plist.XXXXXX")" || die "Cannot stage Info.plist."
  cat >"$tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>TMNF</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>$(xml_escape "$identifier")</string>
  <!-- The wrapper only launches and supervises Wine; Wine shows the game's own
       Dock tile. Hiding the wrapper's tile leaves a single icon in the Dock. -->
  <key>LSUIElement</key><true/>
  <key>CFBundleName</key><string>$(xml_escape "$name")</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(xml_escape "$TMNF_VERSION")</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  publish_file "$tmp" "${contents}/Info.plist"
  tmp="$(mktemp "${contents}/MacOS/.TMNF.XXXXXX")" || die "Cannot stage the app executable."
  # shellcheck disable=SC2016 # Generate shell variables for the app's later invocation.
  {
    printf '#!/bin/bash\n'
    printf '%s\n' 'alert() { /usr/bin/osascript -e "display alert \"TrackMania needs attention\" message \"$1\" as critical"; }'
    printf 'python_bin=%q\n' "${TMNF_LAUNCHER}/python/bin/python3"
    # Fail closed: never fall back to a python3 found on PATH.
    printf 'if test ! -x "$python_bin"; then\n'
    printf '  alert "The launcher support files are missing. Reinstall the apps from the TrackMania hub."\n'
    printf '  exit 1\nfi\n'
    printf 'PYTHONPATH=%q "$python_bin" -m tmnf.app.launch %q %q %q\n' "${TMNF_LAUNCHER}/src/python" \
      "${TMNF_LAUNCHER}/src/shell/cli/tmnf" "$action" "${HOME}/Library/Logs/TMNF/${action}.log"
    printf 'status=$?\nif test "$status" -ne 0; then\n'
    printf '  alert "The session failed or its state is uncertain. The game may still be running. Open ~/Library/Logs/TMNF in Finder for the launch log."\n'
    printf 'fi\nexit "$status"\n'
  } >"$tmp"
  chmod +x "$tmp"
  publish_file "$tmp" "${contents}/MacOS/TMNF"
  touch "${contents}/.tmnf-app" "$path"  # Finder refreshes a touched bundle's icon.
  say "Installed $path"
}

# remove_legacy_launcher — the shared pre-multi-title launcher is only deleted
# once the Nations Forever wrappers point at the per-title one, it was
# installed by this title, and it passes the managed-path checks.
remove_legacy_launcher() {
  local legacy="${TMNF_SUPPORT}/launcher"
  test "$TMNF_TITLE" = tmnf || return 0
  test "$TMNF_LAUNCHER" = "${TMNF_SUPPORT}/launchers/tmnf" || return 0
  test -d "$legacy" || return 0
  if test -L "$legacy" || test ! -f "${legacy}/.tmnf-launcher" || test -L "${legacy}/.tmnf-launcher"; then
    warn "Leaving the old launcher in place (not a managed directory): $legacy"
    return 0
  fi
  # shellcheck disable=SC2016 # Literal line written by install_app.
  if ! grep -Fxq 'if test -z "${TMNF_TITLE:-}"; then export TMNF_TITLE=tmnf; fi' "${legacy}/config/installation.sh" 2>/dev/null; then
    warn "Leaving the old launcher in place (it belongs to another title): $legacy"
    return 0
  fi
  is_safe_managed_path "$legacy" || { warn "Leaving the old launcher in place (unsafe path): $legacy"; return 0; }
  rm -rf -- "$legacy" || warn "Could not remove the old launcher: $legacy"
}

install_app() {
  local staging setting cleanup
  local game_name settings_name game_id settings_id app_icon
  if test "${TMNF_APP_OPERATION_FD+x}" != x; then
    tmnf_python -m tmnf.guard.operation_guard app-run "$TMNF_PREFIX" \
      /bin/bash "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" app
    return $?
  fi
  tmnf_python -m tmnf.guard.operation_guard app-held "$TMNF_PREFIX" \
    || die "Invalid app operation lock."
  validate_managed_layout || die "Unsafe installation layout."
  tmnf_python -m tmnf.guard.managed_paths app-layout \
    "$TMNF_APP" "$TMNF_SETTINGS_APP" "$TMNF_LAUNCHER" \
    || die "Game, Settings and launcher need separate, non-overlapping destinations."
  check_app_target "$TMNF_APP"
  check_app_target "$TMNF_SETTINGS_APP"
  # Refresh the cached game icon before anything is replaced.
  ensure_title_icon || warn "Could not extract the game icon for $TMNF_TITLE; using the bundled artwork."
  is_safe_managed_path "$TMNF_LAUNCHER" || die "Unsafe launcher path: $TMNF_LAUNCHER"
  mkdir -p "$(dirname "$TMNF_LAUNCHER")"
  staging="$(mktemp -d "$(dirname "$TMNF_LAUNCHER")/.launcher-install.XXXXXX")"
  (
    printf -v cleanup 'rm -rf -- %q' "$staging"
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    stage_launcher_payload "$TMNF_PROJECT_DIR" "$staging"
    # printf %q keeps spaces, apostrophes and shell metacharacters literal.
    for setting in TMNF_TITLE TMNF_ROOT TMNF_PREFIX TMNF_USERDATA TMNF_SUPPORT TMNF_RUNTIME TMNF_LAUNCHER \
      TMNF_APP TMNF_SETTINGS_APP TMNF_WINDOW_SIZE TMNF_AUDIO TMNF_X87 TMNF_RENDERER; do
      # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
      printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$setting" "$setting" "${!setting}"
    done >"${staging}/config/installation.sh"
    touch "${staging}/.tmnf-launcher"
    if test -e "$TMNF_LAUNCHER"; then
      test -f "${TMNF_LAUNCHER}/.tmnf-launcher" || die "Refusing to replace an unmanaged launcher directory."
      # Swap atomically: the old launcher stays roll-backable until the new
      # one is fully in place, so a failed move never leaves the machine
      # without an installed launcher.
      previous="$(mktemp -d "$(dirname "$TMNF_LAUNCHER")/.launcher-previous.XXXXXX")"
      mv "$TMNF_LAUNCHER" "$previous/launcher"
    fi
    if ! mv "$staging" "$TMNF_LAUNCHER"; then
      if test -n "${previous:-}" && test -d "$previous/launcher"; then
        mv "$previous/launcher" "$TMNF_LAUNCHER"
        rm -rf -- "$previous"
        die "Cannot install the launcher support directory."
      fi
      rm -rf -- "$TMNF_LAUNCHER"
      die "Cannot install the launcher support directory."
    fi
    rm -rf -- "${previous:-/nonexistent}"
  )
  game_name="$(basename "$TMNF_APP" .app)"
  settings_name="$(basename "$TMNF_SETTINGS_APP" .app)"
  # Bundle identifiers live in the title registry (via tmnf_load_title).
  game_id="$TMNF_TITLE_BUNDLE_ID_GAME"
  settings_id="$TMNF_TITLE_BUNDLE_ID_SETTINGS"
  test -n "$game_id" && test -n "$settings_id" \
    || die "Title $TMNF_TITLE has no bundle identifiers in ${TMNF_TITLES_DIR}/${TMNF_TITLE}.json"
  app_icon="$(resolve_app_icon)"
  write_app "$TMNF_APP" "$game_name" play "$game_id" "$app_icon"
  write_app "$TMNF_SETTINGS_APP" "$settings_name" configure "$settings_id" "$app_icon"
  test -z "$app_icon" || rm -f "$app_icon"
  remove_legacy_launcher
}
