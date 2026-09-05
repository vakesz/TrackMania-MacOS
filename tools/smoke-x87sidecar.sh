#!/bin/bash
# Exercise TMNF's precision mode across the optional translator's code paths.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
. "${PROJECT_DIR}/lib/common.sh"

source_dir="${MACTMNF_X87_SRC:-${PROJECT_DIR}/.build/release/work/x87sidecar}"
sidecar="${source_dir}/bin/x87sidecar"
if test ! -x "$sidecar"; then
  say 'Source-only translator probes require make build; testing the installed Wine integration.'
  exec "${SCRIPT_DIR}/smoke-x87.sh"
fi
"$sidecar" --probe
for mode in default direct unfused pressure; do
  mode_vars=(X87_DISABLE_X87_IR=0 X87_DISABLE_ALL_FUSIONS=0)
  case "$mode" in
    direct) mode_vars=(X87_DISABLE_X87_IR=1 X87_DISABLE_ALL_FUSIONS=0) ;;
    unfused) mode_vars=(X87_DISABLE_X87_IR=1 X87_DISABLE_ALL_FUSIONS=1) ;;
    pressure) mode_vars=(X87_DISABLE_X87_IR=0 X87_DISABLE_ALL_FUSIONS=0 X87_FPR_POOL_LIMIT=5) ;;
  esac
  for probe in test_precision_control test_pc24_arithmetic test_pc24_host_state \
    test_arith test_flags_across_x87 test_fxch; do
    say "Testing $probe ($mode)"
    env -u X87_FPR_POOL_LIMIT TEST_PC24=1 X87_ENABLE_IR_SPLIT=1 X87_ENABLE_IR_REMAT=1 X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0 \
      "${mode_vars[@]}" "$sidecar" --cooperative "${source_dir}/bin/tests/$probe"
  done
done
say "Testing automatic Wine integration (32-bit and 64-bit)"
ROSETTA_X87_PATH="$sidecar" X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0 \
  "${SCRIPT_DIR}/smoke-x87.sh"
