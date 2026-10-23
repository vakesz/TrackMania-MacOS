#!/bin/bash
# Exercise TMNF's precision mode across the optional translator's code paths.
set -euo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH='' cd -- "${SCRIPT_DIR}/../../../.." && pwd)"
# shellcheck source=src/shell/lib/common.sh
. "${PROJECT_DIR}/src/shell/lib/common.sh"

have python3 || die "Missing python3. Install Python 3.9 or newer to run smoke probes."
export TMNF_X87=on
source_dir="${TMNF_X87_SRC:-${PROJECT_DIR}/.build/release/work/x87sidecar}"
sidecar="${source_dir}/bin/x87sidecar"
if test ! -x "$sidecar"; then
  if test "${TMNF_X87_SOURCE_ONLY:-0}" = 1; then
    die "Source-only probe requires an executable sidecar: $sidecar"
  fi
  say 'SKIP: source-only (no source-built x87sidecar); falling back to the installed-runtime Wine probe series (smoke-x87.sh, several minutes).'
  export ROSETTA_X87_PATH="$TMNF_SIDECAR"
  say 'Testing installed translator Wine integration (TMNF_X87=on)'
  exec "${SCRIPT_DIR}/smoke-x87.sh"
fi
export ROSETTA_X87_PATH="$sidecar"
tmnf_python -m tmnf.probes.probe_runner 60 "$sidecar" --probe
for mode in default direct unfused pressure; do
  mode_vars=(X87_DISABLE_X87_IR=0 X87_DISABLE_ALL_FUSIONS=0)
  case "$mode" in
    direct) mode_vars=(X87_DISABLE_X87_IR=1 X87_DISABLE_ALL_FUSIONS=0) ;;
    unfused) mode_vars=(X87_DISABLE_X87_IR=1 X87_DISABLE_ALL_FUSIONS=1) ;;
    pressure) mode_vars=(X87_DISABLE_X87_IR=0 X87_DISABLE_ALL_FUSIONS=0 X87_FPR_POOL_LIMIT=5) ;;
  esac
  # Storm checks interruption stability, not an independent numeric RC oracle.
  # STORM_PC24 sets its input CW; the sample does not load it before every case.
  for probe in test_precision_control test_pc24_arithmetic test_pc24_host_state test_rc_rounding \
    test_arith test_flags_across_x87 test_fxch test_x87_signal_storm; do
    say "Testing $probe ($mode)"
    tmnf_python -m tmnf.probes.probe_runner 60 \
      env -u X87_FPR_POOL_LIMIT -u STORM_CASE STORM_PC24=1 TEST_PC24=1 X87_ENABLE_IR_SPLIT=1 X87_ENABLE_IR_REMAT=1 X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0 \
      "${mode_vars[@]}" "$sidecar" --cooperative "${source_dir}/bin/tests/$probe"
  done
done
if test "${TMNF_X87_SOURCE_ONLY:-0}" = 1; then
  exit 0
fi
say "Testing automatic Wine integration (32-bit and 64-bit)"
ROSETTA_X87_PATH="$sidecar" X87_ENABLE_FMA_CONTRACT=0 X87_FAST_ROUND=0 \
  "${SCRIPT_DIR}/smoke-x87.sh"
