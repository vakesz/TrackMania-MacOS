#!/usr/bin/env python3
"""Host supervision for src/c/session.c. Wine output is inherited, not captured.

run requires the operation_guard token and FD. stop deliberately takes no
operation lock: it is explicit permission to stop the selected owned prefix.
Only a validated native exit result permits automatic prefix cleanup.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from tmnf.guard import operation_guard
from tmnf.guard.managed_paths import check_prefix, safe_path


POLL_SECONDS = 0.05
EXCEPTION_STATUSES = {
    0x80000003, 0x80000004, 0xC0000005, 0xC000001D, 0xC000008C,
    0xC000008D, 0xC000008E, 0xC000008F, 0xC0000090, 0xC0000091,
    0xC0000092, 0xC0000093, 0xC0000094, 0xC0000095, 0xC0000096,
    0xC00000FD, 0xC0000374, 0xC0000409, 0xE06D7363,
}


def _log(message):
    print('session: ' + message, file=sys.stderr, flush=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate native result field')
        result[key] = value
    return result


def read_result(path):
    """Return None for an unpublished result; reject all non-protocol content."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o022 or info.st_nlink > 1):
            raise ValueError('Unsafe native result file: mode={:o}, links={}'.format(
                stat.S_IMODE(info.st_mode), info.st_nlink))
        # Atomic publication can unlink the old inode after open. This FD still
        # owns a stable, complete record; multiple hard links remain forbidden.
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('Native result exceeds 4096 bytes')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValueError('Malformed native result') from error
    if type(result) is not dict:
        raise ValueError('Invalid native result schema')
    state = result.get('state')
    if state in ('starting', 'running') and set(result) == {'state'}:
        return result
    if (state == 'exited' and set(result) == {'state', 'exit_code', 'cleanup'} and
            result['cleanup'] is True and type(result['exit_code']) is int and
            0 <= result['exit_code'] <= 0xFFFFFFFF):
        return result
    if (state in ('failed', 'uncertain') and set(result) == {'state', 'reason', 'cleanup'} and
            result['cleanup'] is False and type(result['reason']) is str and
            re.fullmatch(r'[a-z][a-z0-9_]*', result['reason'])):
        return result
    raise ValueError('Invalid native result schema')


def _owned_prefix(prefix):
    check_prefix(str(prefix))
    prefix = safe_path(str(prefix))
    info = prefix.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Unsafe Wine prefix: {}'.format(prefix))
    return prefix


def _runtime(wine, prefix):
    wine = Path(wine)
    if not wine.is_absolute():
        raise ValueError('Wine must be an absolute path')
    # Derive from the supplied runtime, not PATH or a wine symlink's target.
    server = wine.with_name('wineserver')
    for binary in (wine, server):
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Runtime executable missing: {}'.format(binary))
    env = dict(os.environ, WINEPREFIX=str(prefix))
    env.pop(operation_guard.TOKEN, None)
    return wine, server, env


def _timeout(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Timeout must be finite and greater than zero')
    return value


@contextmanager
def _signals():
    interrupted = []
    handlers = {}

    def interrupt(signum, frame):
        if not interrupted:
            interrupted.append(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, interrupt)
        yield interrupted
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def _check_interrupt(interrupted):
    if interrupted:
        raise InterruptedError(interrupted[0], 'Interrupted by signal {}'.format(interrupted[0]))


def _wait(process, deadline, interrupted, error_label='Cleanup deadline expired'):
    while True:
        _check_interrupt(interrupted)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(error_label)
        status = process.poll()
        if status is not None:
            return status
        time.sleep(min(POLL_SECONDS, remaining))


def _terminate(process, deadline):
    """Stop only this host PID. Never signal a process group or Windows job."""
    if process.poll() is not None:
        return True
    # Report retained results before the operation guard's two-second SIGKILL.
    deadline = min(deadline, time.monotonic() + 1.0)
    try:
        process.terminate()
        remaining = max(0, deadline - time.monotonic())
        try:
            process.wait(timeout=min(0.5, remaining / 2))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return process.poll() is not None
    except ProcessLookupError:
        return process.poll() is not None
    return True


def _cleanup(server, env, deadline, interrupted):
    for option in ('-k', '-w'):
        _check_interrupt(interrupted)
        if time.monotonic() >= deadline:
            raise TimeoutError('Cleanup deadline expired')
        process = subprocess.Popen([str(server), option], env=env, close_fds=True,
                                   start_new_session=True, stdin=subprocess.DEVNULL)
        try:
            status = _wait(process, deadline, interrupted)
        finally:
            if process.poll() is None:
                # A timed-out command is not permission to kill other host PIDs.
                process.kill()
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    _log('cleanup command PID {} has not ended'.format(process.pid))
        # Wine returns 1 for both -k and -w when no server exists in some
        # lifestyles of the pinned runtime (documented success is 0; the
        # lock probe below still confirms shutdown before counting success).
        if status not in (0, 1):
            raise ValueError('wineserver {} failed with status {}'.format(option, status))


def run(prefix, wine, helper, command, watch=(), desktop=None,
        startup_timeout=300, cleanup_timeout=15, audio=None, settings=False):
    """Return 0/1 for a confirmed target exit; raise on supervision failure.

    Optional audio/settings registry writes share the native startup deadline.
    main() maps failures to CLI dispositions. Call on the main thread because
    run and stop temporarily handle SIGINT, SIGTERM and SIGHUP.
    """
    startup_timeout = _timeout(startup_timeout)
    cleanup_timeout = _timeout(cleanup_timeout)
    if startup_timeout > 0xFFFFFFFE / 1000:
        raise ValueError('Startup timeout exceeds the native millisecond range')
    if not command or not str(command[0]):
        raise ValueError('run requires an executable')
    if audio not in (None, 'on', 'off'):
        raise ValueError('Audio must be on or off')
    if type(settings) is not bool:
        raise ValueError('Settings must be a boolean')
    if settings and PureWindowsPath(command[0]).name in ('', '.', '..'):
        raise ValueError('Settings requires an executable basename')
    if len(watch) > 8:
        raise ValueError('At most eight watch paths are supported')
    if desktop is not None:
        if not re.fullmatch(r'[0-9]{1,5}x[0-9]{1,5}', desktop) or not all(
                0 < int(size) <= 65535 for size in desktop.split('x')):
            raise ValueError('Desktop must be WIDTHxHEIGHT with dimensions 1..65535')
    operation_guard.held(prefix)
    prefix = _owned_prefix(prefix)
    wine, server, env = _runtime(wine, prefix)
    if not Path(helper).is_file():
        raise ValueError('Native session helper is missing: {}'.format(helper))
    home = Path.home().resolve()
    # held() checks the prefix parent even when a temporary prefix needs its
    # private result directory elsewhere. Keep all results beneath home.
    result_parent = prefix.parent if home in prefix.parents else home
    parent = result_parent
    while True:
        info = parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Untrusted session result parent: {}'.format(parent))
        if parent == home:
            break
        parent = parent.parent
    _log('{} prefix={!s} runtime={!s} executable={!r}'.format(
        datetime.now(timezone.utc).isoformat(), prefix, wine, str(command[0])))
    helper_host = None
    deadline = None
    with _signals() as interrupted:
        directory = Path(tempfile.mkdtemp(prefix='.' + prefix.name + '.session-', dir=str(result_parent)))
        result_path = directory / 'result.json'
        try:
            startup_deadline = time.monotonic() + startup_timeout
            preparation = []
            if audio is not None:
                preparation.append(['reg', 'add', r'HKCU\Software\Wine\Drivers', '/v', 'Audio',
                                    '/t', 'REG_SZ', '/d', 'coreaudio' if audio == 'on' else '', '/f'])
            if settings:
                # The Nvidia PCI spoof was qualified only for the TMF launcher
                # family (TMNF and its TMU sibling share the binary). Other
                # engines must not inherit it by coincidence.
                basename = PureWindowsPath(command[0]).name
                if basename in ('TmForeverLauncher.exe', 'TmForever.exe'):
                    key = 'HKCU\\Software\\Wine\\AppDefaults\\{}\\Direct3D'.format(basename)
                    for name, value in (('VideoPciVendorID', '0x10de'),
                                        ('VideoPciDeviceID', '0x0611')):
                        preparation.append(['reg', 'add', key, '/v', name,
                                            '/t', 'REG_DWORD', '/d', value, '/f'])
            for registry_args in preparation:
                _check_interrupt(interrupted)
                if time.monotonic() >= startup_deadline:
                    raise TimeoutError('Wine preparation deadline expired')
                process = subprocess.Popen([str(wine)] + registry_args, env=env, close_fds=True,
                                           start_new_session=True, stdin=subprocess.DEVNULL)
                try:
                    status = _wait(process, startup_deadline, interrupted, 'Wine preparation deadline expired')
                finally:
                    if process.poll() is None:
                        if not _terminate(process, time.monotonic() + cleanup_timeout):
                            # A Wine process that refuses to die cannot be
                            # trusted to stop racing the next registry write
                            # into the same prefix; stop preparation instead
                            # of piling a second client onto it.
                            _log('preparation PID {} has not ended; refusing to continue setup'
                                 .format(process.pid))
                            raise RuntimeError('Wine preparation process could not be stopped')
                if status:
                    raise ValueError('Wine preparation failed with status {}'.format(status))
            _check_interrupt(interrupted)
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Startup deadline expired before native launch')
            args = [str(wine), str(helper), '--result', str(result_path),
                    '--startup-ms', str(math.ceil(remaining * 1000))]
            for path in watch:
                args.extend(['--watch', str(path)])
            if desktop is not None:
                args.extend(['--desktop', desktop])
            args.extend(['--'] + [str(arg) for arg in command])
            _check_interrupt(interrupted)
            helper_host = subprocess.Popen(args, env=env, close_fds=True,
                                           start_new_session=True, stdin=subprocess.DEVNULL, umask=0o077)
            running = False
            seen_result = False
            while True:
                _check_interrupt(interrupted)
                host_status = helper_host.poll()
                result = read_result(result_path)
                if result is not None:
                    seen_result = True
                    state = result['state']
                    if state == 'running':
                        running = True
                    elif state == 'starting' and running:
                        raise ValueError('Native session regressed to starting')
                    elif state == 'exited':
                        code = result['exit_code']
                        label = 'exception status' if code in EXCEPTION_STATUSES else 'target status'
                        _log('{} 0x{:08x} ({})'.format(label, code, code))
                        deadline = time.monotonic() + cleanup_timeout
                        _cleanup(server, env, deadline, interrupted)
                        # Prefix shutdown can change the host exit status. The
                        # native result, not that host status, identifies game exit.
                        _wait(helper_host, deadline, interrupted,
                              'Helper has not ended after cleanup')
                        operation_guard.check_inactive(prefix)
                        _check_interrupt(interrupted)
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Cleanup deadline expired')
                        return 1 if code else 0
                    elif state in ('failed', 'uncertain'):
                        message = '{}: {}; prefix left running; use explicit stop if needed'.format(
                            state, result['reason'])
                        if result['reason'] == 'startup_timeout':
                            raise TimeoutError(message)
                        raise ValueError(message)
                elif seen_result:
                    raise ValueError('Native session result disappeared')
                if not running and time.monotonic() >= startup_deadline:
                    raise TimeoutError('Startup deadline expired; prefix left running; use explicit stop if needed')
                if host_status is not None:
                    raise ValueError('Helper ended with status {} without a final native result'.format(host_status))
                time.sleep(POLL_SECONDS)
        finally:
            if deadline is None:
                deadline = time.monotonic() + cleanup_timeout
            ended = helper_host is None
            try:
                if helper_host is not None:
                    ended = _terminate(helper_host, deadline)
            finally:
                if ended:
                    shutil.rmtree(directory)
                else:
                    _log('helper PID {} has not ended; retained result directory {}'.format(
                        helper_host.pid, directory))
            _check_interrupt(interrupted)


def stop(prefix, wine, cleanup_timeout=15):
    """Explicitly stop an owned prefix, even while another operation holds it."""
    cleanup_timeout = _timeout(cleanup_timeout)
    prefix = _owned_prefix(prefix)
    wine, server, env = _runtime(wine, prefix)
    _log('{} explicit stop prefix={!s} runtime={!s}'.format(
        datetime.now(timezone.utc).isoformat(), prefix, wine))
    with _signals() as interrupted:
        deadline = time.monotonic() + cleanup_timeout
        _cleanup(server, env, deadline, interrupted)
        operation_guard.check_inactive(prefix)
        _check_interrupt(interrupted)
        if time.monotonic() >= deadline:
            raise TimeoutError('Cleanup deadline expired')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    launch = actions.add_parser('run')
    halt = actions.add_parser('stop')
    for action in (launch, halt):
        action.add_argument('--prefix', required=True)
        action.add_argument('--wine', required=True)
        action.add_argument('--cleanup-timeout', type=float, default=15)
    launch.add_argument('--helper', required=True)
    launch.add_argument('--watch', action='append', default=[])
    launch.add_argument('--desktop')
    launch.add_argument('--audio', choices=('on', 'off'))
    launch.add_argument('--settings', action='store_true')
    launch.add_argument('--startup-timeout', type=float, default=300)
    launch.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action == 'stop':
            return stop(args.prefix, args.wine, args.cleanup_timeout)
        if not args.command or args.command[0] != '--':
            raise ValueError('Separate the executable and its arguments with --')
        return run(args.prefix, args.wine, args.helper, args.command[1:], args.watch,
                   args.desktop, args.startup_timeout, args.cleanup_timeout, args.audio, args.settings)
    except InterruptedError as error:
        _log(error.args[1] if len(error.args) > 1 else str(error))
        return 128 + error.errno
    except (TimeoutError, subprocess.TimeoutExpired) as error:
        _log(str(error))
        return 124
    except (OSError, ValueError, RuntimeError) as error:
        _log(str(error))
        return 2


if __name__ == '__main__':
    sys.exit(main())
