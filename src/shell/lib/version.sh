#!/bin/bash
# The release version is [project] version in pyproject.toml; uv.lock mirrors
# it. src/python/trackmania_mac/install/version.py reads it the same way, and
# tests/test_project.py checks that both readers agree.
#
# Sourced by common.sh, package-app.sh and the release workflow, none of which
# can rely on the others, so this file defines nothing else. It runs no
# external command: the installed hub calls the CLI with a minimal PATH.

# Print the [project] version from DIR/pyproject.toml. Fails when the key is
# missing or the value is not a plain token: it ends up in runtime paths and
# release URLs.
trackmania_mac_project_version() {
  local file="${1:?trackmania_mac_project_version needs a project directory}/pyproject.toml"
  local line header version="" in_project=0
  local header_re='^[[:space:]]*\[' key_re='^[[:space:]]*version[[:space:]]*='
  local value_re='^[[:space:]]*version[[:space:]]*=[[:space:]]*"([^"]*)"[[:space:]]*(#.*)?$'
  local format_re='^[A-Za-z0-9][A-Za-z0-9._+-]*$'
  if ! test -f "$file"; then
    printf 'error: cannot read the version: %s is missing\n' "$file" >&2
    return 1
  fi
  while IFS= read -r line || test -n "$line"; do
    line="${line%$'\r'}"
    if [[ "$line" =~ $header_re ]]; then
      header="${line%%#*}"
      header="${header#"${header%%[![:space:]]*}"}"
      header="${header%"${header##*[![:space:]]}"}"
      if test "$header" = "[project]"; then in_project=1; else in_project=0; fi
      continue
    fi
    if test "$in_project" = 1 && [[ "$line" =~ $key_re ]]; then
      if [[ "$line" =~ $value_re ]]; then version="${BASH_REMATCH[1]}"; fi
      break
    fi
  done <"$file"
  if ! [[ "$version" =~ $format_re ]]; then
    printf 'error: %s has no valid [project] version (got "%s")\n' "$file" "$version" >&2
    return 1
  fi
  printf '%s\n' "$version"
}
