#!/bin/bash
# Copy the launcher payload used by the hub and app wrappers. A packaged hub
# adds a relocatable CPython tree at project/python; installed app wrappers
# inherit it from there and never depend on the user's developer tools.

# Pin the commit a hub was built from so its build mode can fetch that exact
# source. Only the project's own .git counts; a parent repository is never
# consulted. A tree without .git (a staged payload being staged again) passes
# its existing record on, and one with neither records nothing.
record_build_revision() {
  local project="$1" file="$2" revision state

  rm -f -- "$file"
  if test -e "${project}/.git"; then
    revision="$(git -C "$project" rev-parse HEAD 2>/dev/null)" || return 0
    state=clean
    if test -n "$(git -C "$project" status --porcelain 2>/dev/null)"; then state=dirty; fi
    printf '%s\n%s\n' "$revision" "$state" >"$file"
  elif test -f "${project}/config/build-revision"; then
    cp "${project}/config/build-revision" "$file"
  fi
}

stage_launcher_payload() {
  local project="$1" destination="$2" module art

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
  cp "${project}/config/dxvk.conf" "${project}/config/pins.json" "${destination}/config/"
  mkdir -p "${destination}/config/titles"
  cp "${project}/config/titles/"*.json "${destination}/config/titles/"
  cp "${project}/VERSION" "${destination}/"
  record_build_revision "$project" "${destination}/config/build-revision"
  # Original per-title artwork, rasterized to PNG at package time. A checkout
  # has no PNGs; the hub then falls back to the SVGs in assets/titles.
  if test -d "${project}/assets/titles"; then
    mkdir -p "${destination}/assets/titles"
    for art in "${project}/assets/titles/"*.png ; do
      test -f "$art" || continue
      cp "$art" "${destination}/assets/titles/"
    done
  fi
  if test -d "${project}/python"; then
    test -x "${project}/python/bin/python3" \
      || die "Bundled Python is invalid: ${project}/python/bin/python3"
    cp -R "${project}/python" "${destination}/"
  fi
}
