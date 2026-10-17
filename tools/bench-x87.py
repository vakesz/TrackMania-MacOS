#!/usr/bin/env python3
"""Time x87 workloads under stock Rosetta and under x87sidecar builds.

Builds tests/x87-bench.c as a 32-bit x87 program and runs it in the selected
prefix (as make smoke does), once with stock Rosetta and once per translator.
Prints nanoseconds per iteration, the median of several runs. No game files are
used. Close the game first.

  python3 tools/bench-x87.py                           # installed translator
  python3 tools/bench-x87.py --sidecar old=PATH --sidecar new=PATH   # compare builds
"""

import argparse
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x87_wine  # noqa: E402

# (workload, x87 precision control): TMNF's racing thread runs in PC24.
CASES = [('mat4', '24'), ('sprite', '24'), ('mat4', '53'), ('int', '')]
SECONDS_PER_RUN = 0.4


def measure(env, exe, workload, precision, repeats):
    extra = [precision] if precision else []

    def once(iterations):
        status, output = x87_wine.run(env, ['wine', str(exe), workload, str(iterations), *extra], 120)
        for line in output.splitlines():
            parts = line.split()
            if status == 0 and len(parts) == 3 and parts[0] == workload:
                return float(parts[1])
        raise RuntimeError(f'{workload}: no result (exit {status}): {output[-300:]}')

    probe = once(20000)  # calibrate: about SECONDS_PER_RUN per timed run
    iterations = int(min(max(SECONDS_PER_RUN * 1e9 / max(probe, 0.01), 20000), 50_000_000))
    return statistics.median(once(iterations) for _ in range(repeats))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sidecar', action='append', default=[], metavar='[NAME=]PATH',
                        help='x87sidecar binary to time (repeatable; default: the installed one)')
    parser.add_argument('--repeats', type=int, default=3, help='timed runs per case (median)')
    args = parser.parse_args()
    wine, prefix, installed, _ = x87_wine.settings()
    named = [x87_wine.parse_sidecar(arg) for arg in args.sidecar] or [('installed', installed)]
    translators = [x87_wine.STOCK] + [path for _, path in named]
    labels = [x87_wine.STOCK] + [name for name, _ in named]

    with tempfile.TemporaryDirectory(prefix='mactmnf-x87-bench-') as work:
        exe = Path(work) / 'x87-bench.exe'
        x87_wine.subprocess.run([x87_wine.compiler(), '-O2', '-Wall', '-Wextra', '-Werror',
                                 '-mfpmath=387', '-march=pentium4', '-mno-sse',
                                 str(x87_wine.ROOT / 'tests/x87-bench.c'), '-o', str(exe)], check=True)
        width = max(12, *(len(name) + 2 for name in labels))
        print(f'ns per iteration, median of {args.repeats}; lower is faster', flush=True)
        print(f"{'case':<12}" + ''.join(f'{name:>{width}}' for name in labels), flush=True)
        for workload, precision in CASES:
            line = f"{workload + ('/pc' + precision if precision else ''):<12}"
            for sidecar in translators:
                env = x87_wine.environment(prefix, sidecar)
                env['PATH'] = f'{wine.parent}:{env.get("PATH", "")}'
                try:
                    line += f'{measure(env, exe, workload, precision, args.repeats):{width}.1f}'
                except RuntimeError as error:
                    line += f"{'error':>{width}}"
                    print(f'  {error}', file=sys.stderr)
            print(line, flush=True)


if __name__ == '__main__':
    main()
