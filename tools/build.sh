#!/bin/bash
# Homebrew supplies build tools only. Runtime libraries are built from source.
set -euo pipefail
project="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
# Help and packaging need only Python, never Homebrew, Xcode or Rosetta.
readonly_action=0
for argument in "$@"; do
  case "$argument" in
    -h|--help) readonly_action=1 ;;
  esac
done
if { test "$#" = 1 && test "$1" = --only=package; } ||
   { test "$#" = 2 && test "$1" = --only && test "$2" = package; }; then
  readonly_action=1
fi
if test "$readonly_action" = 1; then
  python="$(command -v python3.14 || command -v python3 || true)"
  test -n "$python" || { echo 'Python 3.9 or newer is required for help and packaging.' >&2; exit 1; }
  exec "$python" "${project}/tools/build.py" "$@"
fi
session_only=0
if { test "$#" = 1 && test "$1" = --only=session; } ||
   { test "$#" = 2 && test "$1" = --only && test "$2" = session; }; then
  session_only=1
fi
# Default to every core; Python applies the same default.
if ! [[ "${MACTMNF_JOBS-$(sysctl -n hw.ncpu)}" =~ ^[0-9]*[1-9][0-9]*$ ]]; then
  echo 'MACTMNF_JOBS must be a positive integer' >&2
  exit 1
fi
# Python invalidates completion under the release lock, after bootstrap succeeds.
case "$project" in
  *[[:space:]]*) echo 'Build from a folder without spaces; Wine makefiles require this.' >&2; exit 1 ;;
esac
test "$(uname -s)/$(uname -m)" = Darwin/arm64 || { echo 'An Apple Silicon Mac is required.' >&2; exit 1; }
command -v brew >/dev/null || { echo 'Install Homebrew from https://brew.sh, then run make build again.' >&2; exit 1; }
tools=(python@3.14 mingw-w64)
if test "$session_only" = 0; then
  xcodebuild -version >/dev/null || { echo 'Select a full Xcode installation with xcode-select first.' >&2; exit 1; }
  /usr/bin/arch -x86_64 /usr/bin/true || { echo 'Install Rosetta with: softwareupdate --install-rosetta --agree-to-license' >&2; exit 1; }
  tools+=(cmake ninja meson glslang bison flex pkgconf autoconf automake libtool)
fi
missing=()
for formula in "${tools[@]}"; do
  brew list --versions "$formula" >/dev/null 2>&1 || missing+=("$formula")
done
if test "${#missing[@]}" -gt 0; then brew install "${missing[@]}"; fi
PATH="$(brew --prefix)/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if test "$session_only" = 0; then
  PATH="$(brew --prefix bison)/bin:$(brew --prefix flex)/bin:$PATH"
fi
export PATH
exec "$(brew --prefix python@3.14)/bin/python3.14" "${project}/tools/build.py" "$@"
