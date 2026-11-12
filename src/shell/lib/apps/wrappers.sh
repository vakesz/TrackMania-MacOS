#!/bin/bash
# Installed apps contain a small shell entry point; support files live outside the checkout.

# shellcheck source=src/shell/lib/apps/payload.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/apps/payload.sh"
# shellcheck source=src/shell/lib/apps/icons.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/apps/icons.sh"
# shellcheck source=src/shell/lib/plist.sh
. "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/lib/plist.sh"

# Ownership markers: an app wrapper's Contents and a launcher folder carry one.
readonly APP_MARKER=.trackmania-mac-app
# The wrapper's executable (CFBundleExecutable), and so its process name.
readonly WRAPPER_EXECUTABLE=TrackManiaMac
readonly LAUNCHER_MARKER=.trackmania-mac-launcher
# Settings an installed launcher records in config/installation.sh, in order.
readonly LAUNCHER_SETTINGS=(TRACKMANIA_MAC_TITLE TRACKMANIA_MAC_ROOT TRACKMANIA_MAC_PREFIX TRACKMANIA_MAC_USERDATA TRACKMANIA_MAC_SUPPORT TRACKMANIA_MAC_RUNTIME
  TRACKMANIA_MAC_LAUNCHER TRACKMANIA_MAC_APP TRACKMANIA_MAC_SETTINGS_APP TRACKMANIA_MAC_WINDOW_SIZE TRACKMANIA_MAC_AUDIO TRACKMANIA_MAC_X87 TRACKMANIA_MAC_RENDERER)

# publish_file TEMP DEST — atomic rename of a file staged next to DEST.
publish_file() {
  mv -f "$1" "$2" || { rm -f "$1"; die "Cannot write $2"; }
}

# resolve_app_icon — print a temporary .icns for the wrappers: the cached game
# icon, else the bundled artwork converted once, else nothing.
resolve_app_icon() {
  local cache="${TRACKMANIA_MAC_SUPPORT}/icons/${TRACKMANIA_MAC_TITLE}.icns"
  local bundled="${TRACKMANIA_MAC_PROJECT_DIR}/assets/titles/${TRACKMANIA_MAC_TITLE}.png" out
  out="$(mktemp "${TMPDIR:-/tmp}/trackmania-mac-icon.XXXXXX")" || return 0
  if test -f "$cache" && test ! -L "$cache" && cp "$cache" "$out"; then
    printf '%s\n' "$out"
  elif test -f "$bundled" && icon_tool --from-png "$bundled" "$out" 2>/dev/null && test -s "$out"; then
    printf '%s\n' "$out"
  else
    rm -f "$out"
  fi
}

# has_marker DIR MARKER LEGACY — DIR carries MARKER or an earlier release's
# LEGACY name as a plain file; a symlink under either name means not ours.
has_marker() {
  test ! -L "$1/$2" && test ! -L "$1/$3" && { test -f "$1/$2" || test -f "$1/$3"; }
}

check_app_target() {
  local path="$1" entry
  local contents="${path}/Contents"
  for entry in "$path" "$contents" "${contents}/MacOS" "${contents}/Resources" "${contents}/Info.plist" "${contents}/${APP_MARKER}" \
    "${contents}/${LEGACY_APP_MARKER}" "${contents}/MacOS/${WRAPPER_EXECUTABLE}" \
    "${contents}/MacOS/${LEGACY_WRAPPER_EXECUTABLE}" "${contents}/Resources/AppIcon.icns"; do
    test ! -L "$entry" || die "Refusing a symlink in the managed app: $entry"
  done
  if test -e "$path" && ! has_marker "$contents" "$APP_MARKER" "$LEGACY_APP_MARKER"; then
    # Recognize the previous TMNF launcher when upgrading.
    if ! test -f "${contents}/Info.plist" || ! /usr/libexec/PlistBuddy \
      -c 'Print CFBundleIdentifier' "${contents}/Info.plist" 2>/dev/null | grep -Fxq "$LEGACY_LAUNCHER_APP_ID"; then
      die "An unrelated app already exists: $path"
    fi
  fi
}

# write_app PATH NAME ACTION IDENTIFIER [ICON] [MODULE] [CLI] — MODULE and CLI
# (relative to the launcher) default to this release's; a relinked launcher of an
# earlier release passes its own.
write_app() {
  local path="$1" name="$2" action="$3" identifier="$4" icon="${5:-}"
  local module="${6:-${TRACKMANIA_MAC_PYTHON_PACKAGE}.app.launch}" cli="${7:-src/shell/cli/${TRACKMANIA_MAC_CLI_NAME}}"
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
  # Order matters for a wrapper being rewritten: the new executable first, then the
  # Info.plist naming it, then the old executable goes. Stopped anywhere, the plist
  # names an executable that exists.
  tmp="$(mktemp "${contents}/MacOS/.${WRAPPER_EXECUTABLE}.XXXXXX")" || die "Cannot stage the app executable."
  # tmnf keeps play.log/configure.log; other titles get their own so a second title's app
  # neither waits on the first's log lock nor mixes output with it.
  local log_name="${action}.log"
  test "$TRACKMANIA_MAC_TITLE" = tmnf || log_name="${TRACKMANIA_MAC_TITLE}-${action}.log"
  # shellcheck disable=SC2016 # Generate shell variables for the app's later invocation.
  {
    printf '#!/bin/bash\n'
    printf '%s\n' 'alert() { /usr/bin/osascript -e "display alert \"TrackMania needs attention\" message \"$1\" as critical"; }'
    printf 'python_bin=%q\n' "${TRACKMANIA_MAC_LAUNCHER}/python/bin/python3"
    # Fail closed: never fall back to a python3 found on PATH.
    printf 'if test ! -x "$python_bin"; then\n'
    printf '  alert "The launcher support files are missing. Reinstall the apps from the TrackMania hub."\n'
    printf '  exit 1\nfi\n'
    printf 'PYTHONPATH=%q "$python_bin" -m %s %q %q %q\n' "${TRACKMANIA_MAC_LAUNCHER}/src/python" "$module" \
      "${TRACKMANIA_MAC_LAUNCHER}/${cli}" "$action" "${TRACKMANIA_MAC_LOG_DIR}/${log_name}"
    printf 'status=$?\nif test "$status" -eq 75; then\n'
    printf '  alert "Another TrackMania session is still finishing. Try again in a moment."\n'
    printf 'elif test "$status" -ne 0; then\n'
    printf '  alert "The session failed or its state is uncertain. The game may still be running. Open ~/Library/TrackManiaMacOS/logs in Finder for the launch log."\n'
    printf 'fi\nexit "$status"\n'
  } >"$tmp"
  chmod +x "$tmp"
  publish_file "$tmp" "${contents}/MacOS/${WRAPPER_EXECUTABLE}"
  tmp="$(mktemp "${contents}/.Info.plist.XXXXXX")" || die "Cannot stage Info.plist."
  cat >"$tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>${WRAPPER_EXECUTABLE}</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>$(xml_escape "$identifier")</string>
  <!-- The wrapper only launches and supervises Wine; Wine shows the game's own
       Dock tile. Hiding the wrapper's tile leaves a single icon in the Dock. -->
  <key>LSUIElement</key><true/>
  <key>CFBundleName</key><string>$(xml_escape "$name")</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(xml_escape "$TRACKMANIA_MAC_VERSION")</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
EOF
  publish_file "$tmp" "${contents}/Info.plist"
  rm -f "${contents}/MacOS/${LEGACY_WRAPPER_EXECUTABLE}"
  touch "${contents}/${APP_MARKER}" "$path"  # Finder refreshes a touched bundle's icon.
  # Only once this release's marker is in place: the app is ours at every instant.
  rm -f "${contents}/${LEGACY_APP_MARKER}"
  say "Installed $path"
}

# remove_legacy_launcher — the shared pre-multi-title launcher is only deleted
# once the Nations Forever wrappers point at the per-title one, it was
# installed by this title, and it passes the managed-path checks.
remove_legacy_launcher() {
  local legacy="${TRACKMANIA_MAC_SUPPORT}/launcher"
  test "$TRACKMANIA_MAC_TITLE" = tmnf || return 0
  test "$TRACKMANIA_MAC_LAUNCHER" = "${TRACKMANIA_MAC_SUPPORT}/launchers/tmnf" || return 0
  test -d "$legacy" || return 0
  if test -L "$legacy" || ! has_marker "$legacy" "$LAUNCHER_MARKER" "$LEGACY_LAUNCHER_MARKER"; then
    warn "Leaving the old launcher in place (not a managed directory): $legacy"
    return 0
  fi
  # shellcheck disable=SC2016 # Literal line written by install_app.
  if ! grep -Fxq 'if test -z "${TRACKMANIA_MAC_TITLE:-}"; then export TRACKMANIA_MAC_TITLE=tmnf; fi' "${legacy}/config/installation.sh" 2>/dev/null; then
    warn "Leaving the old launcher in place (it belongs to another title): $legacy"
    return 0
  fi
  is_safe_managed_path "$legacy" || { warn "Leaving the old launcher in place (unsafe path): $legacy"; return 0; }
  rm -rf -- "$legacy" || warn "Could not remove the old launcher: $legacy"
}

install_app() {
  local staging setting cleanup
  local game_name settings_name game_id settings_id app_icon
  if test "${TRACKMANIA_MAC_APP_OPERATION_FD+x}" != x; then
    trackmania_mac_python -m trackmania_mac.guard.operation_guard app-run "$TRACKMANIA_MAC_PREFIX" \
      /bin/bash "${TRACKMANIA_MAC_PROJECT_DIR}/src/shell/cli/trackmania-mac" app
    return $?
  fi
  trackmania_mac_python -m trackmania_mac.guard.operation_guard app-held "$TRACKMANIA_MAC_PREFIX" \
    || die "Invalid app operation lock."
  validate_managed_layout || die "Unsafe installation layout."
  trackmania_mac_python -m trackmania_mac.guard.managed_paths app-layout \
    "$TRACKMANIA_MAC_APP" "$TRACKMANIA_MAC_SETTINGS_APP" "$TRACKMANIA_MAC_LAUNCHER" \
    || die "Game, Settings and launcher need separate, non-overlapping destinations."
  check_app_target "$TRACKMANIA_MAC_APP"
  check_app_target "$TRACKMANIA_MAC_SETTINGS_APP"
  # Refresh the cached game icon before anything is replaced.
  ensure_title_icon || warn "Could not extract the game icon for $TRACKMANIA_MAC_TITLE; using the bundled artwork."
  is_safe_managed_path "$TRACKMANIA_MAC_LAUNCHER" || die "Unsafe launcher path: $TRACKMANIA_MAC_LAUNCHER"
  mkdir -p "$(dirname "$TRACKMANIA_MAC_LAUNCHER")"
  staging="$(mktemp -d "$(dirname "$TRACKMANIA_MAC_LAUNCHER")/.launcher-install.XXXXXX")"
  (
    printf -v cleanup 'rm -rf -- %q' "$staging"
    # shellcheck disable=SC2064
    trap "$cleanup" EXIT
    stage_launcher_payload "$TRACKMANIA_MAC_PROJECT_DIR" "$staging"
    # printf %q keeps spaces, apostrophes and shell metacharacters literal.
    for setting in "${LAUNCHER_SETTINGS[@]}"; do
      # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
      printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$setting" "$setting" "${!setting}"
    done >"${staging}/config/installation.sh"
    touch "${staging}/${LAUNCHER_MARKER}"
    if test -e "$TRACKMANIA_MAC_LAUNCHER"; then
      has_marker "$TRACKMANIA_MAC_LAUNCHER" "$LAUNCHER_MARKER" "$LEGACY_LAUNCHER_MARKER" \
        || die "Refusing to replace an unmanaged launcher directory."
      # Swap atomically: the old launcher stays roll-backable until the new
      # one is fully in place, so a failed move never leaves the machine
      # without an installed launcher.
      sweep_stale "$(dirname "$TRACKMANIA_MAC_LAUNCHER")" '.launcher-previous.*'
      previous="$(mktemp -d "$(dirname "$TRACKMANIA_MAC_LAUNCHER")/.launcher-previous.XXXXXX")"
      mv "$TRACKMANIA_MAC_LAUNCHER" "$previous/launcher"
    fi
    if ! mv "$staging" "$TRACKMANIA_MAC_LAUNCHER"; then
      if test -n "${previous:-}" && test -d "$previous/launcher"; then
        mv "$previous/launcher" "$TRACKMANIA_MAC_LAUNCHER"
        rm -rf -- "$previous"
        die "Cannot install the launcher support directory."
      fi
      rm -rf -- "$TRACKMANIA_MAC_LAUNCHER"
      die "Cannot install the launcher support directory."
    fi
    rm -rf -- "${previous:-/nonexistent}"
  )
  game_name="$(basename "$TRACKMANIA_MAC_APP" .app)"
  settings_name="$(basename "$TRACKMANIA_MAC_SETTINGS_APP" .app)"
  # Bundle identifiers live in the title registry (via trackmania_mac_load_title).
  game_id="$TRACKMANIA_MAC_TITLE_BUNDLE_ID_GAME"
  settings_id="$TRACKMANIA_MAC_TITLE_BUNDLE_ID_SETTINGS"
  test -n "$game_id" && test -n "$settings_id" \
    || die "Title $TRACKMANIA_MAC_TITLE has no bundle identifiers in ${TRACKMANIA_MAC_TITLES_DIR}/${TRACKMANIA_MAC_TITLE}.json"
  app_icon="$(resolve_app_icon)"
  write_app "$TRACKMANIA_MAC_APP" "$game_name" play "$game_id" "$app_icon"
  write_app "$TRACKMANIA_MAC_SETTINGS_APP" "$settings_name" configure "$settings_id" "$app_icon"
  test -z "$app_icon" || rm -f "$app_icon"
  remove_legacy_launcher
}

# --- Relinking after trackmania-mac migrate -------------------------------------------
# trackmania-mac migrate (src/python/trackmania_mac/guard/relocate.py) moved this title's files and
# runs relink-apps once per title, holding the app lock, with
# TRACKMANIA_MAC_RELOCATION_MAP: tab-separated lines `prefix OLD NEW` (a moved folder),
# `exact OLD NEW` (a folder that maps only as a whole) and `set NAME VALUE` (a
# setting to add when a launcher does not record it). The launcher keeps its own
# code and runtime version until the next Update; only recorded paths change.

# relocated_path VALUE — VALUE's new spelling: an exact entry first, then the
# longest moved folder VALUE is or lies in; VALUE itself when nothing moved.
relocated_path() {
  local value="$1" kind old new best="" best_new=""
  while IFS=$'\t' read -r kind old new; do
    case "$kind" in
      exact)
        if test "$value" = "$old"; then
          printf '%s\n' "$new"
          return 0
        fi
        ;;
      prefix)
        if { test "$value" = "$old" || [[ "$value" == "$old"/* ]]; } && test "${#old}" -gt "${#best}"; then
          best="$old"
          best_new="$new"
        fi
        ;;
    esac
  done <<<"$TRACKMANIA_MAC_RELOCATION_MAP"
  if test -n "$best"; then
    printf '%s%s\n' "$best_new" "${value#"$best"}"
  else
    printf '%s\n' "$value"
  fi
}

# relocated_from PATH — every old spelling of PATH, one per line.
relocated_from() {
  local value="$1" kind old new
  while IFS=$'\t' read -r kind old new; do
    test "$kind" = prefix || continue
    if test "$value" = "$new" || [[ "$value" == "$new"/* ]]; then
      printf '%s%s\n' "$old" "${value#"$new"}"
    fi
  done <<<"$TRACKMANIA_MAC_RELOCATION_MAP"
}

# shell_word_value WORD — print the value of one word as `printf %q` writes it
# (plain characters, backslash escapes, '' and $'...' with octal escapes),
# decoded without running anything. Fails on anything else: unquoted ; $( `
# spaces or quotes, and any control character in the value.
shell_word_value() {
  local word="$1" out="" char code LC_ALL=C backslash="\\" quote="'"
  # Outside quotes %q escapes every shell metacharacter, so only these stand bare.
  local plain="^[^][[:space:][:cntrl:]${backslash}${backslash}${quote}\"\$\`;&|<>(){}*?!#~]"
  if test "$word" = "''"; then
    printf '\n'
    return 0
  fi
  test -n "$word" || return 1
  while test -n "$word"; do
    char="${word:0:1}"
    if test "$char" = "$backslash"; then
      test "${#word}" -ge 2 || return 1
      out="${out}${word:1:1}"
      word="${word:2}"
    elif test "${word:0:2}" = "\$'"; then
      word="${word:2}"
      while :; do
        test -n "$word" || return 1
        char="${word:0:1}"
        if test "$char" = "$quote"; then
          word="${word:1}"
          break
        elif test "$char" = "$backslash"; then
          if [[ "$word" =~ ^\\([0-7]{1,3}) ]]; then
            code="${BASH_REMATCH[1]}"
            test "$((8#$code))" -ge 32 || return 1
            printf -v char '%b' "\\0${code}"
            word="${word:$((1 + ${#code}))}"
          elif test "${word:1:1}" = "$backslash" || test "${word:1:1}" = "$quote"; then
            char="${word:1:1}"
            word="${word:2}"
          else
            return 1
          fi
          out="${out}${char}"
        else
          # Inside $'...' everything but a control character is literal.
          [[ "$char" != [[:cntrl:]] ]] || return 1
          out="${out}${char}"
          word="${word:1}"
        fi
      done
    elif [[ "$char" =~ $plain ]]; then
      out="${out}${char}"
      word="${word:1}"
    else
      return 1
    fi
  done
  [[ "$out" != *[[:cntrl:]]* ]] || return 1
  printf '%s\n' "$out"
}

# relink_launcher_config FILE — rewrite the recorded paths that moved. Every line
# must be blank, a comment, or a setting exactly as install_app writes it, with a
# value that is one %q word; the value is decoded, never run. Anything else and
# the launcher is left alone for a reinstall (status 1).
relink_launcher_config() {
  local file="$1" line name value mapped kind key extra tmp changed=0 dialect=""
  local pattern='^if test -z "\$\{([A-Z0-9_]+):-\}"; then export ([A-Z0-9_]+)=(.+); fi$'
  local recorded=" "
  test -f "$file" && test ! -L "$file" || return 0
  tmp="$(mktemp "$(dirname "$file")/.installation.XXXXXX")" || die "Cannot stage $file"
  # shellcheck disable=SC2094 # $tmp is a new file beside $file, published afterwards.
  while IFS= read -r line || test -n "$line"; do
    case "$line" in
      ''|'#'*)
        printf '%s\n' "$line"
        continue
        ;;
    esac
    if ! [[ "$line" =~ $pattern ]] || test "${BASH_REMATCH[1]}" != "${BASH_REMATCH[2]}" \
      || ! value="$(shell_word_value "${BASH_REMATCH[3]}")"; then
      rm -f "$tmp"
      warn "Leaving the launcher in $(dirname "$(dirname "$file")") unchanged: its settings are not in the expected form. Reinstall the apps from the hub."
      return 1
    fi
    name="${BASH_REMATCH[1]}"
    recorded="${recorded}${name} "
    case "$name" in *_TITLE) dialect="${name%TITLE}" ;; esac
    mapped="$(relocated_path "$value")"
    if test "$mapped" != "$value"; then
      changed=1
      # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
      printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "$name" "$name" "$mapped"
    else
      printf '%s\n' "$line"
    fi
  done <"$file" >"$tmp"
  # Settings the launcher must gain, named in its own release's dialect.
  while IFS=$'\t' read -r kind key extra; do
    test "$kind" = set && test -n "$dialect" || continue
    [[ "$key" =~ ^[A-Z0-9_]+$ ]] || continue
    case "$recorded" in *" ${dialect}${key} "*) continue ;; esac
    changed=1
    # shellcheck disable=SC2016 # This text executes later, in the installed launcher.
    printf 'if test -z "${%s:-}"; then export %s=%q; fi\n' "${dialect}${key}" "${dialect}${key}" "$extra" >>"$tmp"
  done <<<"$TRACKMANIA_MAC_RELOCATION_MAP"
  if test "$changed" = 1; then
    publish_file "$tmp" "$file"
    say "Updated $file"
  else
    rm -f "$tmp"
  fi
}

# launcher_version DIR — the release an installed launcher came from.
launcher_version() {
  local version=""
  if test -f "$1/VERSION"; then
    IFS= read -r version <"$1/VERSION" || true
  elif test -f "$1/pyproject.toml"; then
    version="$(trackmania_mac_project_version "$1" 2>/dev/null)" || version=""
  fi
  printf '%s\n' "${version:-$TRACKMANIA_MAC_VERSION}"
}

# launcher_entry_points DIR — "MODULE<tab>CLI" of the release layout DIR contains:
# a launcher keeps its own code until the next Update, whichever release wrote it.
launcher_entry_points() {
  local dir="$1" package cli
  for package in "$TRACKMANIA_MAC_PYTHON_PACKAGE" "$LEGACY_PYTHON_PACKAGE"; do
    for cli in "$TRACKMANIA_MAC_CLI_NAME" "$LEGACY_CLI_NAME"; do
      if test -f "${dir}/src/python/${package}/app/launch.py" && test -f "${dir}/src/shell/cli/${cli}"; then
        printf '%s.app.launch\tsrc/shell/cli/%s\n' "$package" "$cli"
        return 0
      fi
    done
  done
  return 1
}

# relink_wrapper APP ACTION IDENTIFIER MODULE CLI — point one of our wrappers that
# uses this title's (moved) launcher at its new folder and the new log folder.
relink_wrapper() {
  local app="$1" action="$2" identifier="$3" module="$4" cli="$5" legacy_identifier="${6:-}"
  local contents entry candidate found="" icon="" line python current executable
  contents="${app}/Contents"
  test -e "$app" || test -L "$app" || return 0
  current="$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "${contents}/Info.plist" 2>/dev/null)" || current=""
  # The executable the plist names: this release's or an earlier one's, never another.
  executable="$(/usr/libexec/PlistBuddy -c 'Print CFBundleExecutable' "${contents}/Info.plist" 2>/dev/null)" \
    || executable=""
  case "$executable" in
    "$WRAPPER_EXECUTABLE"|"$LEGACY_WRAPPER_EXECUTABLE") ;;
    *) executable="" ;;
  esac
  entry="${contents}/MacOS/${executable:-${LEGACY_WRAPPER_EXECUTABLE}}"
  if test -L "$app" || ! has_marker "$contents" "$APP_MARKER" "$LEGACY_APP_MARKER" \
    || test ! -f "$entry" || test -L "$entry" \
    || { test "$current" != "$identifier" && { test -z "$legacy_identifier" || test "$current" != "$legacy_identifier"; }; }; then
    warn "Leaving $app unchanged: it is not an app this hub installed for $TRACKMANIA_MAC_TITLE_NAME."
    return 0
  fi
  # The Python the wrapper runs, decoded: the locale that wrote it chose the quoting.
  line="$(grep -m 1 '^python_bin=' "$entry")" || line=""
  python="$(shell_word_value "${line#python_bin=}")" || python=""
  while IFS= read -r candidate; do
    if test -n "$python" && test "$python" = "${candidate}/python/bin/python3"; then
      found="$candidate"
      break
    fi
  done < <(relocated_from "$TRACKMANIA_MAC_LAUNCHER"; printf '%s\n' "$TRACKMANIA_MAC_LAUNCHER")
  if test -z "$found"; then
    warn "Leaving $app unchanged: it uses another launcher. Reinstall the apps from the hub."
    return 0
  fi
  # Nothing to do once it already names this launcher, the new log folder, this
  # release's bundle identifier, marker and executable; otherwise write_app converts them.
  if test "$found" = "$TRACKMANIA_MAC_LAUNCHER" && grep -Fq "$(printf '%q' "$TRACKMANIA_MAC_LOG_DIR")/" "$entry" \
    && test "$current" = "$identifier" && test ! -e "${contents}/${LEGACY_APP_MARKER}" \
    && test "$executable" = "$WRAPPER_EXECUTABLE" && test ! -e "${contents}/MacOS/${LEGACY_WRAPPER_EXECUTABLE}"; then
    return 0
  fi
  if test -f "${contents}/Resources/AppIcon.icns" && test ! -L "${contents}/Resources/AppIcon.icns"; then
    icon="$(mktemp "${TMPDIR:-/tmp}/trackmania-mac-icon.XXXXXX")" || icon=""
    test -z "$icon" || cp "${contents}/Resources/AppIcon.icns" "$icon" || icon=""
  fi
  TRACKMANIA_MAC_VERSION="$(launcher_version "$TRACKMANIA_MAC_LAUNCHER")" \
    write_app "$app" "$(basename "$app" .app)" "$action" "$identifier" "$icon" "$module" "$cli"
  test -z "$icon" || rm -f "$icon"
}

# relink_apps — the relink-apps command. Per title: the launcher's recorded
# paths, then its two wrappers. A later per-title conversion step belongs here.
relink_apps() {
  local entry_points module cli
  test -n "${TRACKMANIA_MAC_RELOCATION_MAP:-}" || die "relink-apps only runs inside trackmania-mac migrate."
  trackmania_mac_python -m trackmania_mac.guard.operation_guard app-lock-held - || die "Invalid app operation lock."
  test -d "$TRACKMANIA_MAC_LAUNCHER" && test ! -L "$TRACKMANIA_MAC_LAUNCHER" \
    && has_marker "$TRACKMANIA_MAC_LAUNCHER" "$LAUNCHER_MARKER" "$LEGACY_LAUNCHER_MARKER" || return 0
  relink_launcher_config "${TRACKMANIA_MAC_LAUNCHER}/config/installation.sh" || return 0
  if ! entry_points="$(launcher_entry_points "$TRACKMANIA_MAC_LAUNCHER")"; then
    warn "The $TRACKMANIA_MAC_TITLE_NAME apps are from a release too old to update in place; reinstall them from the hub."
    return 0
  fi
  IFS=$'\t' read -r module cli <<<"$entry_points"
  # Old wrappers become this release's: bundle identifier and marker included.
  relink_wrapper "$TRACKMANIA_MAC_APP" play "$TRACKMANIA_MAC_TITLE_BUNDLE_ID_GAME" "$module" "$cli" \
    "$TRACKMANIA_MAC_TITLE_LEGACY_BUNDLE_ID_GAME"
  relink_wrapper "$TRACKMANIA_MAC_SETTINGS_APP" configure "$TRACKMANIA_MAC_TITLE_BUNDLE_ID_SETTINGS" "$module" "$cli" \
    "$TRACKMANIA_MAC_TITLE_LEGACY_BUNDLE_ID_SETTINGS"
}
