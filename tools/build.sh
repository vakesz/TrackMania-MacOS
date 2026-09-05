#!/bin/bash
# Homebrew supplies build tools only. Runtime libraries are built from source.
set -euo pipefail
project="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
case "$project" in
  *[[:space:]]*) echo 'Build from a folder without spaces; Wine makefiles require this.' >&2; exit 1 ;;
esac
test "$(uname -s)/$(uname -m)" = Darwin/arm64 || { echo 'An Apple Silicon Mac is required.' >&2; exit 1; }
command -v brew >/dev/null || { echo 'Install Homebrew from https://brew.sh, then run make build again.' >&2; exit 1; }
xcodebuild -version >/dev/null || { echo 'Select a full Xcode installation with xcode-select first.' >&2; exit 1; }
/usr/bin/arch -x86_64 /usr/bin/true || { echo 'Install Rosetta with: softwareupdate --install-rosetta --agree-to-license' >&2; exit 1; }
tools=(python@3.14 cmake ninja meson mingw-w64 glslang bison flex pkgconf autoconf automake libtool)
missing=()
for formula in "${tools[@]}"; do
  brew list --versions "$formula" >/dev/null 2>&1 || missing+=("$formula")
done
if test "${#missing[@]}" -gt 0; then brew install "${missing[@]}"; fi
PATH="$(brew --prefix bison)/bin:$(brew --prefix flex)/bin:$(brew --prefix)/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
exec "$(brew --prefix python@3.14)/bin/python3.14" "${project}/tools/build.py" "$@"
