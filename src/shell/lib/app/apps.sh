#!/bin/bash
# Installed apps contain a small shell entry point; support files live outside the checkout.

# shellcheck source=src/shell/lib/app/launcher_payload.sh
. "${TMNF_PROJECT_DIR}/src/shell/lib/app/launcher_payload.sh"

check_app_target() {
  local path="$1" entry
  local contents="${path}/Contents"
  for entry in "$path" "$contents" "${contents}/MacOS" "${contents}/Resources" "${contents}/Info.plist" "${contents}/.tmnf-app"; do
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
  local path="$1" name="$2" action="$3" identifier="$4"
  local contents="${path}/Contents"
  check_app_target "$path"
  mkdir -p "${contents}/MacOS" "${contents}/Resources"
  # The icon comes from the installed game executable, so nothing of Nadeo's is
  # redistributed. A missing icon only costs the Finder artwork.
  if test -f "${TMNF_LAUNCHER}/config/AppIcon.icns"; then
    cp "${TMNF_LAUNCHER}/config/AppIcon.icns" "${contents}/Resources/AppIcon.icns"
  else
    rm -f "${contents}/Resources/AppIcon.icns"
    warn "Could not extract the game icon for $name; using the generic app icon."
  fi
  cat >"${contents}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>TMNF</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>${identifier}</string>
  <!-- The wrapper only launches and supervises Wine; Wine shows the game's own
       Dock tile. Hiding the wrapper's tile leaves a single icon in the Dock. -->
  <key>LSUIElement</key><true/>
  <key>CFBundleName</key><string>${name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${TMNF_VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  # shellcheck disable=SC2016 # Generate shell variables for the app's later invocation.
  {
    printf '#!/bin/bash\n'
    printf 'python_bin=%q\n' "${TMNF_LAUNCHER}/python/bin/python3"
    printf 'if test ! -x "$python_bin"; then python_bin=python3; fi\n'
    printf 'PYTHONPATH=%q "$python_bin" -m tmnf.app.app_launch %q %q %q\n' "${TMNF_LAUNCHER}/src/python" \
      "${TMNF_LAUNCHER}/src/shell/cli/tmnf" "$action" "${HOME}/Library/Logs/TMNF/${action}.log"
    printf 'status=$?\nif test "$status" -ne 0; then\n'
    printf '  /usr/bin/osascript -e %q\n' 'display alert "TrackMania needs attention" message "The session failed or its state is uncertain. The game may still be running. Open ~/Library/Logs/TMNF in Finder for the launch log." as critical'
    printf 'fi\nexit "$status"\n'
  } >"${contents}/MacOS/TMNF"
  chmod +x "${contents}/MacOS/TMNF"
  touch "${contents}/.tmnf-app" "$path"  # Finder refreshes a touched bundle's icon.
  say "Installed $path"
}

install_app() {
  local staging setting cleanup
  local game_name settings_name game_id settings_id
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
  is_safe_managed_path "$TMNF_LAUNCHER" || die "Unsafe launcher path: $TMNF_LAUNCHER"
  mkdir -p "$(dirname "$TMNF_LAUNCHER")"
  staging="$(mktemp -d "$(dirname "$TMNF_LAUNCHER")/.launcher-install.XXXXXX")"
  (
    printf -v cleanup 'rm -rf -- %q' "$staging"
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    stage_launcher_payload "$TMNF_PROJECT_DIR" "$staging"
    if ! (
      # Neither descriptor belongs in the icon extractor's subprocesses.
      eval "exec ${TMNF_APP_OPERATION_FD}>&-"
      unset TMNF_APP_OPERATION_FD
      without_operation_lock tmnf_python -m tmnf.app.app_icon "$GAME_EXE" "${staging}/config/AppIcon.icns"
    ) 2>/dev/null; then
      rm -f "${staging}/config/AppIcon.icns"
    fi
    # printf %q keeps spaces, apostrophes and shell metacharacters literal.
    for setting in TMNF_TITLE TMNF_ROOT TMNF_PREFIX TMNF_USERDATA TMNF_SUPPORT TMNF_RUNTIME TMNF_LAUNCHER \
      TMNF_APP TMNF_SETTINGS_APP TMNF_WINDOW_SIZE TMNF_AUDIO TMNF_X87 TMNF_RENDERER; do
      # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
      printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$setting" "$setting" "${!setting}"
    done >"${staging}/config/installation.sh"
    touch "${staging}/.tmnf-launcher"
    if test -e "$TMNF_LAUNCHER"; then
      test -f "${TMNF_LAUNCHER}/.tmnf-launcher" || die "Refusing to replace an unmanaged launcher directory."
      rm -rf "$TMNF_LAUNCHER"
    fi
    mv "$staging" "$TMNF_LAUNCHER"
  )
  game_name="$(basename "$TMNF_APP" .app)"
  settings_name="$(basename "$TMNF_SETTINGS_APP" .app)"
  # Bundle identifiers live in the title registry (via lib/titles.sh).
  game_id="$(tmnf_title_field "$TMNF_TITLE" bundle_id_game)"
  settings_id="$(tmnf_title_field "$TMNF_TITLE" bundle_id_settings)"
  test -n "$game_id" && test -n "$settings_id" \
    || die "Title $TMNF_TITLE has no bundle identifiers in ${TMNF_TITLES_DIR}/${TMNF_TITLE}.json"
  write_app "$TMNF_APP" "$game_name" play "$game_id"
  write_app "$TMNF_SETTINGS_APP" "$settings_name" configure "$settings_id"
}
