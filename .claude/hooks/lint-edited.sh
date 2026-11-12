#!/usr/bin/env bash
# PostToolUse hook: format/lint the file Claude just wrote or edited.
# Exit 2 feeds lint output back to Claude; tool absence is not an error.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[ -n "$f" ] && [ -f "$f" ] || exit 0
root=${CLAUDE_PROJECT_DIR:-$(git -C "$(dirname "$f")" rev-parse --show-toplevel)}
cd "$root" || exit 0
case "$f" in "$root"/*) ;; *) exit 0 ;; esac
rel=${f#"$root"/}
out=""
case "$rel" in
  *.py)
    uv run --frozen --group lint ruff format -q "$rel" >/dev/null 2>&1
    out=$(uv run --frozen --group lint ruff check -q "$rel" 2>&1); rc=$? ;;
  *.sh|src/shell/cli/trackmania-mac)
    out=$({ bash -n "$rel" && shellcheck -x "$rel"; } 2>&1); rc=$? ;;
  src/swift/*.swift)
    command -v swiftlint >/dev/null || exit 0
    out=$(cd src/swift && swiftlint lint --strict --quiet "${rel#src/swift/}" 2>&1); rc=$? ;;
  *) exit 0 ;;
esac
if [ "$rc" -ne 0 ] && [ -n "$out" ]; then
  printf 'Lint failed for %s:\n%s\n' "$rel" "$out" >&2
  exit 2
fi
exit 0
