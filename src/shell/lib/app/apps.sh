#!/bin/bash
# Installed apps contain a small shell entry point; support files live outside the checkout.

check_app_target() {
  local path="$1" entry
  local contents="${path}/Contents"
  for entry in "$path" "$contents" "${contents}/MacOS" "${contents}/Resources" "${contents}/Info.plist" "${contents}/.mactmnf-app"; do
    test ! -L "$entry" || die "Refusing a symlink in the managed app: $entry"
  done
  if test -e "$path" && test ! -f "${contents}/.mactmnf-app"; then
    # Recognize the previous MacTMNF launcher when upgrading.
    if ! test -f "${contents}/Info.plist" || ! /usr/libexec/PlistBuddy \
      -c 'Print CFBundleIdentifier' "${contents}/Info.plist" 2>/dev/null | grep -Fxq local.mactmnf.launcher; then
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
  if test -f "${MACTMNF_LAUNCHER}/config/AppIcon.icns"; then
    cp "${MACTMNF_LAUNCHER}/config/AppIcon.icns" "${contents}/Resources/AppIcon.icns"
  else
    rm -f "${contents}/Resources/AppIcon.icns"
    warn "Could not extract the game icon for $name; using the generic app icon."
  fi
  cat >"${contents}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>MacTMNF</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>${identifier}</string>
  <!-- The wrapper only launches and supervises Wine; Wine shows the game's own
       Dock tile. Hiding the wrapper's tile leaves a single icon in the Dock. -->
  <key>LSUIElement</key><true/>
  <key>CFBundleName</key><string>${name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${MACTMNF_VERSION}</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  # shellcheck disable=SC2016 # Generate shell variables for the app's later invocation.
  {
    printf '#!/bin/bash\n'
    printf 'python3 %q %q %q %q\n' "${MACTMNF_LAUNCHER}/lib/app_launch.py" \
      "${MACTMNF_LAUNCHER}/bin/mactmnf" "$action" "${HOME}/Library/Logs/MacTMNF/${action}.log"
    printf 'status=$?\nif test "$status" -ne 0; then\n'
    printf '  /usr/bin/osascript -e %q\n' 'display alert "TrackMania needs attention" message "The session failed or its state is uncertain. The game may still be running. Open ~/Library/Logs/MacTMNF in Finder for the launch log." as critical'
    printf 'fi\nexit "$status"\n'
  } >"${contents}/MacOS/MacTMNF"
  chmod +x "${contents}/MacOS/MacTMNF"
  touch "${contents}/.mactmnf-app" "$path"  # Finder refreshes a touched bundle's icon.
  say "Installed $path"
}

install_app() {
  local staging setting cleanup
  if test "${MACTMNF_APP_OPERATION_FD+x}" != x; then
    python3 "${MACTMNF_PROJECT_DIR}/lib/operation_guard.py" app-run "$MACTMNF_PREFIX" \
      /bin/bash "${MACTMNF_PROJECT_DIR}/bin/mactmnf" app
    return $?
  fi
  python3 "${MACTMNF_PROJECT_DIR}/lib/operation_guard.py" app-held "$MACTMNF_PREFIX" \
    || die "Invalid app operation lock."
  validate_managed_layout || die "Unsafe installation layout."
  python3 "${MACTMNF_PROJECT_DIR}/lib/managed_paths.py" app-layout \
    "$MACTMNF_APP" "$MACTMNF_SETTINGS_APP" "$MACTMNF_LAUNCHER" \
    || die "Game, Settings and launcher need separate, non-overlapping destinations."
  check_app_target "$MACTMNF_APP"
  check_app_target "$MACTMNF_SETTINGS_APP"
  is_safe_managed_path "$MACTMNF_LAUNCHER" || die "Unsafe launcher path: $MACTMNF_LAUNCHER"
  mkdir -p "$(dirname "$MACTMNF_LAUNCHER")"
  staging="$(mktemp -d "$(dirname "$MACTMNF_LAUNCHER")/.launcher-install.XXXXXX")"
  (
    printf -v cleanup 'rm -rf -- %q' "$staging"
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    mkdir -p "${staging}/bin" "${staging}/lib" "${staging}/config"
    cp "${MACTMNF_PROJECT_DIR}/bin/mactmnf" "${staging}/bin/"
    cp "${MACTMNF_PROJECT_DIR}/lib/"*.sh "${MACTMNF_PROJECT_DIR}/lib/"*.py "${staging}/lib/"
    cp "${MACTMNF_PROJECT_DIR}/config/dxvk.conf" "${staging}/config/"
    cp "${MACTMNF_PROJECT_DIR}/install.sh" "$staging"
    cp "${MACTMNF_PROJECT_DIR}/VERSION" "$staging"
    if ! (
      # Neither descriptor belongs in the icon extractor's subprocesses.
      eval "exec ${MACTMNF_APP_OPERATION_FD}>&-"
      unset MACTMNF_APP_OPERATION_FD
      without_operation_lock python3 "${MACTMNF_PROJECT_DIR}/lib/app_icon.py" "$GAME_EXE" "${staging}/config/AppIcon.icns"
    ) 2>/dev/null; then
      rm -f "${staging}/config/AppIcon.icns"
    fi
    # printf %q keeps spaces, apostrophes and shell metacharacters literal.
    for setting in MACTMNF_ROOT MACTMNF_PREFIX MACTMNF_USERDATA MACTMNF_SUPPORT MACTMNF_RUNTIME MACTMNF_LAUNCHER \
      MACTMNF_APP MACTMNF_SETTINGS_APP MACTMNF_WINDOW_SIZE MACTMNF_AUDIO MACTMNF_X87 MACTMNF_RENDERER; do
      # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
      printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$setting" "$setting" "${!setting}"
    done >"${staging}/config/installation.sh"
    touch "${staging}/.mactmnf-launcher"
    if test -e "$MACTMNF_LAUNCHER"; then
      test -f "${MACTMNF_LAUNCHER}/.mactmnf-launcher" || die "Refusing to replace an unmanaged launcher directory."
      rm -rf "$MACTMNF_LAUNCHER"
    fi
    mv "$staging" "$MACTMNF_LAUNCHER"
  )
  write_app "$MACTMNF_APP" 'TrackMania Nations Forever' play 'local.mactmnf.game'
  write_app "$MACTMNF_SETTINGS_APP" 'TrackMania Settings' configure 'local.mactmnf.settings'
}
