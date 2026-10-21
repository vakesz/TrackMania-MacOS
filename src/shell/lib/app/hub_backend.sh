#!/bin/bash
# Machine-facing hub backend. It owns install/state rules; native clients own UI.

hub_list_titles() {
  tmnf_python - "$TMNF_TITLES_DIR" <<'PY'
import json
import sys
from pathlib import Path

for path in sorted(Path(sys.argv[1]).glob('*.json')):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    print('{}: {} [{}]'.format(
        data.get('id', path.stem), data.get('name', path.stem), data.get('status', 'unknown')))
PY
}

hub_snapshot_title() {
  local runtime prefix directx renderer game game_app settings_app
  local title_status

  title_status="$(tmnf_title_status "$TMNF_TITLE")"
  test -n "$title_status" || die "Invalid title registry entry: ${TMNF_TITLES_DIR}/${TMNF_TITLE}.json"

  if wine_binary >/dev/null 2>&1; then runtime=ready; else runtime=missing; fi
  if test -f "${TMNF_PREFIX}/system.reg" && require_owned_prefix >/dev/null 2>&1; then
    prefix=ready
  else
    prefix=missing
  fi
  if directx_is_current; then directx=ready; else directx=missing; fi
  if d9vk_is_installed; then renderer=ready; else renderer=missing; fi
  if test "$TMNF_TITLE" = tmnf; then
    if competition_patch_is_ready; then game=ready; else game=missing; fi
  elif test -f "$GAME_EXE"; then
    game=ready
  else
    game=missing
  fi
  if test -d "$TMNF_APP"; then game_app=ready; else game_app=missing; fi
  if test -d "$TMNF_SETTINGS_APP"; then settings_app=ready; else settings_app=missing; fi

  TMNF_HUB_RUNTIME="$runtime" \
    TMNF_HUB_PREFIX="$prefix" \
    TMNF_HUB_DIRECTX="$directx" \
    TMNF_HUB_RENDERER="$renderer" \
    TMNF_HUB_GAME="$game" \
    TMNF_HUB_GAME_APP="$game_app" \
    TMNF_HUB_SETTINGS_APP="$settings_app" \
    TMNF_HUB_APP_PATH="$TMNF_APP" \
    TMNF_HUB_SETTINGS_APP_PATH="$TMNF_SETTINGS_APP" \
    tmnf_python - "$TMNF_TITLES_DIR/$TMNF_TITLE.json" <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    title = json.loads(Path(sys.argv[1]).read_text())
except (OSError, ValueError) as error:
    raise SystemExit('Invalid title registry entry: ' + str(error))

state = {
    'runtime': os.environ['TMNF_HUB_RUNTIME'],
    'prefix': os.environ['TMNF_HUB_PREFIX'],
    'directx': os.environ['TMNF_HUB_DIRECTX'],
    'renderer': os.environ['TMNF_HUB_RENDERER'],
    'game': os.environ['TMNF_HUB_GAME'],
    'gameApp': os.environ['TMNF_HUB_GAME_APP'],
    'settingsApp': os.environ['TMNF_HUB_SETTINGS_APP'],
}
status = title.get('status')
if status == 'supported':
    actions = [{'id': 'install', 'label': 'Install or repair'}]
    if state['prefix'] == 'ready':
        actions.append({'id': 'update', 'label': 'Check for updates'})
    if state['game'] == 'ready':
        actions.append({'id': 'install-apps', 'label': 'Install Game and Settings apps'})
elif status == 'provisional':
    actions = [{'id': 'install-platform', 'label': 'Install shared platform'}]
    if state['prefix'] == 'ready':
        actions.append({'id': 'import', 'label': 'Import retail installer'})
else:
    raise SystemExit('Unsupported title status: ' + repr(status))

payload = {
    'id': title['id'],
    'name': title['name'],
    'status': status,
    'notes': title.get('notes', ''),
    'state': state,
    'apps': {
        'game': os.environ['TMNF_HUB_APP_PATH'],
        'settings': os.environ['TMNF_HUB_SETTINGS_APP_PATH'],
    },
    'actions': actions,
}
print(json.dumps(payload, separators=(',', ':'), sort_keys=True))
PY
}

hub_snapshot() {
  local title first=1
  printf '{"schema":1,"titles":['
  for title in $(tmnf_title_ids); do
    if test "$first" = 0; then printf ','; fi
    first=0
    TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" hub _state
  done
  printf ']}\n'
}

hub_action_available() {
  local title="$1" action="$2" snapshot
  snapshot="$(TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" hub _state)" || return 1
  TMNF_HUB_SNAPSHOT="$snapshot" tmnf_python -c '
import json
import os
import sys
try:
    title = json.loads(os.environ["TMNF_HUB_SNAPSHOT"])
except (KeyError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if sys.argv[1] in {action["id"] for action in title["actions"]} else 1)
' "$action"
}

hub_run() {
  local title="${1:-}" action="${2:-}" source=""
  shift $(( $# >= 2 ? 2 : $# ))
  test -n "$title" && test -n "$action" || die 'Usage: tmnf hub run TITLE ACTION [--source ABSOLUTE_PATH]'
  tmnf_title_is_known "$title" || die "Unknown title: $title"
  hub_action_available "$title" "$action" \
    || die "Action $action is not currently available for title $title. Refresh the hub and finish prerequisite steps."

  case "$action" in
    install)
      test "$#" = 0 || die 'Install does not accept extra arguments.'
      exec env TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/install.sh"
      ;;
    install-platform)
      test "$#" = 0 || die 'Install platform does not accept extra arguments.'
      exec env TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/install.sh" setup
      ;;
    update)
      test "$#" = 0 || die 'Update does not accept extra arguments.'
      exec env TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" update
      ;;
    install-apps)
      test "$#" = 0 || die 'Install apps does not accept extra arguments.'
      exec env TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" app
      ;;
    import)
      test "$#" = 2 && test "$1" = --source || die 'Import requires --source ABSOLUTE_PATH.'
      source="$2"
      case "$source" in /*) ;; *) die 'Import source must be an absolute path.' ;; esac
      test -f "$source" || die "Import source is not a readable file: $source"
      exec env TMNF_TITLE="$title" "${TMNF_PROJECT_DIR}/src/shell/cli/tmnf" import "$source"
      ;;
    *) die "Unsupported hub action: $action" ;;
  esac
}

hub_command() {
  case "${1:-}" in
    snapshot)
      test "$#" = 1 || die 'Usage: tmnf hub snapshot'
      hub_snapshot
      ;;
    run)
      shift
      hub_run "$@"
      ;;
    _state)
      test "$#" = 1 || die 'Internal hub state command takes no arguments.'
      hub_snapshot_title
      ;;
    *)
      die 'Usage: tmnf hub snapshot | tmnf hub run TITLE ACTION [--source ABSOLUTE_PATH]'
      ;;
  esac
}
