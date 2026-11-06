#!/usr/bin/env python3
"""Diagnostic session monitor for TMNF_DIAG=on.

src/shell/lib/commands/play.sh starts it beside the session supervisor; it exits with that process.
It writes to the same log as the game, so Wine's +fps lines and these lines can be
read together by tmnf.session.report:

  tmnf-state: <tick> 0x<state>|unknown|exited    game state, from src/c/game-state.c
  tmnf-map: <tick> <name>|none|unknown           current map, from the same helper
  tmnf-mon: tick=<tick> event=<name> key=value   focus, power and CPU samples

<tick> is CLOCK_MONOTONIC_RAW in seconds, the clock of Wine's +timestamp prefix and
of QueryPerformanceCounter under Wine. The game-state helper is built with MinGW
when first needed and cached; without MinGW the state stays unknown.
"""

import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / 'src/c/game-state.c'
PROC_PIDLISTTHREADS, PROC_PIDTHREADINFO, PROC_PIDTBSDINFO = 6, 5, 3
RESCAN_SECONDS = 5.0  # a ps scan costs ~50 ms of CPU; never run it more often than this


class ThreadInfo(ctypes.Structure):
    _fields_ = [('user', ctypes.c_uint64), ('system', ctypes.c_uint64), ('cpu_usage', ctypes.c_int32),
                ('policy', ctypes.c_int32), ('run_state', ctypes.c_int32), ('flags', ctypes.c_int32),
                ('sleep_time', ctypes.c_int32), ('curpri', ctypes.c_int32), ('priority', ctypes.c_int32),
                ('maxpriority', ctypes.c_int32), ('name', ctypes.c_char * 64)]


class BsdInfo(ctypes.Structure):
    """struct proc_bsdinfo from <sys/proc_info.h>; only the start time is read."""
    _fields_ = [('flags', ctypes.c_uint32), ('status', ctypes.c_uint32), ('xstatus', ctypes.c_uint32),
                ('pid', ctypes.c_uint32), ('ppid', ctypes.c_uint32), ('uid', ctypes.c_uint32),
                ('gid', ctypes.c_uint32), ('ruid', ctypes.c_uint32), ('rgid', ctypes.c_uint32),
                ('svuid', ctypes.c_uint32), ('svgid', ctypes.c_uint32), ('rfu_1', ctypes.c_uint32),
                ('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32), ('nfiles', ctypes.c_uint32),
                ('pgid', ctypes.c_uint32), ('pjobc', ctypes.c_uint32), ('tdev', ctypes.c_uint32),
                ('tpgid', ctypes.c_uint32), ('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


def start_time(pid, libproc):
    """(seconds, microseconds) the process started, or None; tells a reused pid from the game."""
    if libproc is None:
        return None
    info = BsdInfo()
    if libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)) <= 0:
        return None
    return info.start_sec, info.start_usec


def emit(event, **fields):
    parts = ['tmnf-mon:', 'tick=%.3f' % time.clock_gettime(time.CLOCK_MONOTONIC_RAW), 'event=' + event]
    for key, value in fields.items():
        if value is not None and value != '':
            text = str(value)
            parts.append(key + '=' + (shlex.quote(text) if re.search(r'[\s\'"=]', text) else text))
    print(' '.join(parts), flush=True)


def output(*command, timeout=5):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def game_pid():
    """Host process ID of TmForever.exe; Wine shows the Windows path as its command line."""
    for line in output('ps', '-axo', 'pid=,command=').splitlines():
        pid, _, command = line.strip().partition(' ')
        if re.match(r'[A-Za-z]:\\(?:.*\\)?TmForever\.exe(?:\s|$)', command.strip(), re.I):
            return int(pid)
    return None


class GameTracker:
    """Follows the game process without scanning ps every tick.

    The pid found by one scan is checked each tick with kill(pid, 0) and its
    start time, which is cheap and catches both exit and pid reuse. ps runs only
    while no game is known, and at most every RESCAN_SECONDS.
    """

    def __init__(self, libproc=None, scan=game_pid, clock=time.monotonic):
        self.libproc, self.scan, self.clock = libproc, scan, clock
        self.pid = self.started = None
        self.last_scan = float('-inf')

    def poll(self):
        if self.pid is not None and not (alive(self.pid) and start_time(self.pid, self.libproc) == self.started):
            self.pid = self.started = None
        if self.pid is None and self.clock() - self.last_scan >= RESCAN_SECONDS:
            self.last_scan = self.clock()
            self.pid = self.scan()
            if self.pid is not None:
                self.started = start_time(self.pid, self.libproc)
        return self.pid


class FrontTracker:
    """Foreground process ID. lsappinfo is asked who is in front once per tick; the pid
    behind an application serial number never changes, so it is looked up once per ASN."""

    def __init__(self):
        self.pids = {}

    def pid(self):
        asn = output('lsappinfo', 'front').strip()
        if not asn:
            return None
        if asn not in self.pids:
            match = re.search(r'\bpid\s*=\s*(\d+)', output('lsappinfo', 'info', '-only', 'pid', asn))
            if not match:
                return None  # not cached: the app may still be registering
            self.pids = {asn: int(match.group(1))}  # only the current front app is kept
        return self.pids[asn]


def power_state():
    source = output('pmset', '-g', 'ps')
    state = {'source': 'ac' if "'AC Power'" in source else 'battery' if "'Battery Power'" in source else 'unknown'}
    low = re.search(r'^\s*lowpowermode\s+(\d)', output('pmset', '-g'), re.M)
    if low:
        state['low_power'] = 'on' if low.group(1) == '1' else 'off'
    return state


def threads(pid, libproc):
    """{thread: (cpu ns, priority, name)} for one process."""
    handles = (ctypes.c_uint64 * 1024)()
    count = libproc.proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, handles, ctypes.sizeof(handles)) // 8
    result = {}
    for handle in handles[:max(count, 0)]:
        info = ThreadInfo()
        if libproc.proc_pidinfo(pid, PROC_PIDTHREADINFO, handle, ctypes.byref(info), ctypes.sizeof(info)) > 0:
            name = info.name.decode(errors='replace').strip() or 'thread-%x' % (handle & 0xffffffff)
            result[handle] = (info.user + info.system, info.curpri, name)
    return result


def start_state_helper(cache, prefix, wine):
    """Build src/c/game-state.c once per source version and run it in the game's prefix."""
    compiler = shutil.which('x86_64-w64-mingw32-gcc')
    if compiler is None:
        emit('state_helper', status='unavailable', reason='x86_64-w64-mingw32-gcc not found (brew install mingw-w64)')
        return None
    if not SOURCE.is_file():  # installed launchers carry no C sources
        emit('state_helper', status='unavailable', reason='no game-state.c (run make diag from a checkout)')
        return None
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]
    exe = Path(cache) / ('game-state-' + digest + '.exe')
    if not exe.is_file():
        exe.parent.mkdir(parents=True, exist_ok=True)
        partial = exe.with_suffix('.partial.exe')
        built = subprocess.run([compiler, '-O2', '-Wall', '-Wextra', '-Werror', str(SOURCE), '-o', str(partial)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not built:
            emit('state_helper', status='unavailable', reason='build failed')
            return None
        partial.replace(exe)
        for old in Path(cache).glob('game-state-*.exe'):  # builds of earlier source versions
            if old != exe:
                old.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('X87_', 'ROSETTA_X87'))}
    env.update(WINEPREFIX=prefix, WINEDEBUG='-all')
    # Its lines go straight to the shared log file, never through a pipe.
    return subprocess.Popen([wine, str(exe)], env=env, stdin=subprocess.DEVNULL, stdout=sys.stdout,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--parent', type=int, required=True, help='session supervisor to follow')
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--wine', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--interval', type=float, default=5.0, help='seconds between CPU samples')
    args = parser.parse_args()
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        libproc = ctypes.CDLL('/usr/lib/libproc.dylib')
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    except OSError:
        libproc = None

    helper = start_state_helper(args.cache, args.prefix, args.wine)
    tracker, front = GameTracker(libproc), FrontTracker()
    focus = power = None
    last_power = last_sample = float('-inf')
    previous, previous_time = {}, None
    while alive(args.parent):
        now = time.monotonic()
        try:
            game = tracker.poll()
            state = 'game' if game is not None and front.pid() == game else 'other'
            if state != focus:
                focus = state
                emit('focus', state=state)
            if now - last_power >= 15:
                last_power = now
                current_power = power_state()
                if current_power != power:
                    power = current_power
                    emit('power', **current_power)
            if game is not None and libproc is not None and now - last_sample >= args.interval:
                last_sample = now
                current = threads(game, libproc)
                if previous and previous_time is not None:
                    elapsed = (now - previous_time) * 1e9
                    usage = sorted(((cpu - previous[handle][0]) / elapsed * 100, priority, name)
                                   for handle, (cpu, priority, name) in current.items() if handle in previous)
                    usage.reverse()
                    emit('sample', cpu='%.0f%%' % sum(busy for busy, _, _ in usage), threads=len(current),
                         top=','.join('%s:%.0f%%@%d' % (name, busy, priority)
                                      for busy, priority, name in usage[:4] if busy >= 1))
                previous, previous_time = current, now
        except Exception as error:  # diagnostics must never disturb the session
            emit('error', where=type(error).__name__)
        time.sleep(1.0)
    if helper is not None and helper.poll() is None:
        helper.terminate()


if __name__ == '__main__':
    main()
