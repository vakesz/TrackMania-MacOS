#!/usr/bin/env python3
"""Run a host-side probe with a deadline, cleaning up only its process group."""

import argparse
from contextlib import closing
import math
import os
import select
import signal
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeout", type=float, help="deadline in seconds")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or not args.command:
        parser.error("a finite positive timeout and a command are required")

    interrupted = []
    previous = {sig: signal.signal(sig, lambda signum, frame: interrupted.append(signum))
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    # An inherited SIG_IGN would let the kernel reap the child automatically.
    previous[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    process = None
    result = 127
    deadline = time.monotonic() + args.timeout
    try:
        with closing(select.kqueue()) as queue:
            process = subprocess.Popen(args.command, start_new_session=True)
            exited = False
            try:
                queue.control([select.kevent(process.pid, filter=select.KQ_FILTER_PROC,
                                            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                            fflags=select.KQ_NOTE_EXIT)], 0, 0)
            except ProcessLookupError:
                # Registration can return ESRCH for an already-exited child.
                # It is still our unreaped zombie, so its PID cannot be reused.
                exited = True
            while not exited:
                if interrupted:
                    print("FAIL: probe interrupted: " + " ".join(args.command), file=sys.stderr)
                    return 128 + interrupted[0]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print("FAIL: probe exceeded %g seconds: %s" %
                          (args.timeout, " ".join(args.command)), file=sys.stderr)
                    return 124
                exited = bool(queue.control(None, 1, min(remaining, 0.1)))
    except OSError as error:
        print("FAIL: cannot run probe: %s" % error, file=sys.stderr)
        return 127
    finally:
        if process is not None:
            # Keep the leader unreaped until killpg, including on success, so
            # its PID/group ID cannot be recycled before we signal the group.
            # Never use wineserver -k or search by process name/prefix. A Wine
            # server that detaches from this group is deliberately left alone.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                # Darwin killpg reports EPERM for a zombie-only group.
                if not exited:
                    print("FAIL: cannot signal probe group: %s" % error, file=sys.stderr)
            try:
                result = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                print("FAIL: probe did not exit after SIGKILL", file=sys.stderr)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    sys.exit(main())
