#!/bin/bash
# Copy the launcher payload used by the hub and app wrappers. A packaged hub
# adds a relocatable CPython tree at project/python; installed app wrappers
# inherit it from there and never depend on the user's developer tools.

stage_launcher_payload() {
  local project="$1" destination="$2" module

  mkdir -p "${destination}/src/shell" "${destination}/src/python/tmnf" "${destination}/config"
  cp -R "${project}/src/shell/cli" "${project}/src/shell/lib" "${destination}/src/shell/"
  cp "${project}/src/python/tmnf/__init__.py" "${destination}/src/python/tmnf/"
  for module in app guard hub install session; do
    mkdir -p "${destination}/src/python/tmnf/${module}"
    cp "${project}/src/python/tmnf/${module}/__init__.py" \
      "${project}/src/python/tmnf/${module}/"*.py \
      "${destination}/src/python/tmnf/${module}/"
  done
  # Bytecode never ships: the launcher must run installed sources as-is.
  find "${destination}/src" \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
  cp "${project}/config/dxvk.conf" "${destination}/config/"
  mkdir -p "${destination}/config/titles"
  cp "${project}/config/titles/"*.json "${destination}/config/titles/"
  cp "${project}/VERSION" "${destination}/"
  if test -d "${project}/python"; then
    test -x "${project}/python/bin/python3" \
      || die "Bundled Python is invalid: ${project}/python/bin/python3"
    cp -R "${project}/python" "${destination}/"
  fi
}
