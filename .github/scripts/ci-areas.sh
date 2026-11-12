#!/bin/bash
# Decide which check.yml jobs a change needs. Reads changed paths, one per
# line, on stdin and prints `job=true|false` lines for $GITHUB_OUTPUT.
#
# Rules go from specific to general. A path no rule names runs the scripts
# check, so a new kind of file is never silently untested. Only files that
# nothing in CI reads (docs, the website, GitHub forms) run nothing.
set -euo pipefail

scripts=false swift=false c_helpers=false x87=false

every_job() {
  scripts=true swift=true c_helpers=true x87=true
}

while IFS= read -r path; do
  test -n "$path" || continue
  case "$path" in
    .github/workflows/check.yml | .github/scripts/ci-areas.sh | Makefile)
      every_job ;;
    src/swift/*)
      swift=true ;;
    # The MinGW flags in check.yml mirror these two files, and the Python
    # tests read src/c too.
    src/c/* | src/python/tmnf/build/runtime_builder.py | src/python/tmnf/session/monitor.py)
      scripts=true c_helpers=true ;;
    # What the native x87sidecar test reads or runs under.
    tests/test_x87_patch.py | patches/x87sidecar/* | config/sources.json | pyproject.toml | uv.lock)
      scripts=true x87=true ;;
    src/* | tests/* | config/* | patches/* | assets/*)
      scripts=true ;;
    website/* | docs/* | *.md | LICENSE | licenses/* | .claude/* | t3.json \
      | .github/ISSUE_TEMPLATE/* | .github/workflows/pages.yml)
      ;;
    *)
      scripts=true ;;
  esac
done

printf 'scripts=%s\nswift=%s\nc-helpers=%s\nx87=%s\n' "$scripts" "$swift" "$c_helpers" "$x87"
