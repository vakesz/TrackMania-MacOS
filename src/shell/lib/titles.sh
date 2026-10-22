#!/bin/bash
# Title registry helpers. Inert policy only: no plugins or arbitrary hooks.
# TMNF behavior is unchanged; other titles are provisional until installer
# inventory lands (user-supplied disk images, no keys stored).

: "${TMNF_TITLES_DIR:=${TMNF_PROJECT_DIR}/config/titles}"

tmnf_title_ids() {
  tmnf_python - "$TMNF_TITLES_DIR" <<'PY'
import json, sys
from pathlib import Path
directory = Path(sys.argv[1])
ids = sorted(path.stem for path in directory.glob('*.json'))
print(' '.join(ids))
PY
}

tmnf_title_field() {
  local title="$1" field="$2"
  tmnf_python - "$TMNF_TITLES_DIR/${title}.json" "$field" <<'PY'
import json, sys
from pathlib import Path
path, field = Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(path.read_text())
except (OSError, ValueError):
    sys.exit(1)
value = data.get(field, '')
if isinstance(value, bool):
    print('true' if value else 'false')
elif isinstance(value, list):
    print(' '.join(str(item) for item in value))
elif value is None:
    print('')
else:
    print(str(value))
PY
}

tmnf_title_is_known() {
  case "$1" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
  test -f "${TMNF_TITLES_DIR}/$1.json"
}

tmnf_title_status() {
  tmnf_title_field "$1" status
}

# First declared save directory of a title (plain folder name, no slashes).
# Used to seed per-title userdata under the prefix's redirected Documents.
tmnf_title_first_save_dir() {
  tmnf_title_field "$1" save_dirs | cut -d ' ' -f 1
}
