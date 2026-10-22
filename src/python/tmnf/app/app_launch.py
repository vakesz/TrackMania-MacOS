#!/usr/bin/env python3
"""Launch an app with direct-to-file output and three numbered log backups.

Rotate before launch at 2 MiB. Retention bounds the number of completed logs,
not their bytes: inherited FDs let active or surviving writers exceed 2 MiB.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile

ROTATE_BYTES = 2 * 1024 * 1024


def _check_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022 or info.st_nlink != 1):
        raise ValueError('Unsafe log or lock file')
    return info


@contextmanager
def _open_file(path):
    _check_file(path)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'ab', buffering=0) as stream:
        opened, named = os.fstat(fd), _check_file(path)
        if named is None or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise ValueError('Log or lock file changed')
        yield stream


def run(launcher, action, log_path):
    if action not in ('play', 'configure'):
        raise ValueError('Action must be play or configure')
    path = Path(log_path)
    if (not path.is_absolute() or any(part in ('.', '..') for part in str(log_path).split('/'))
            or any(ord(char) < 32 for char in str(log_path))):
        raise ValueError('Unsafe log path')
    parent = path.parent.resolve()
    roots = (Path.home().resolve(), Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve())
    if not any(parent == root or root in parent.parents for root in roots):
        raise ValueError('Log must be below home or the temporary directory')
    for directory in (parent, *parent.parents):
        if not directory.exists():
            continue
        info = directory.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
            raise ValueError('Unsafe log directory')
    path = parent / path.name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Log directory must be owned and writable only by the user')
    # Never unlink or rotate the lock; descendants inherit only the log FD.
    with _open_file(Path(str(path) + '.lock')) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another launch is using this log') from None
        logs = [path] + [Path(str(path) + '.' + str(index)) for index in range(1, 4)]
        infos = [_check_file(log) for log in logs]
        if infos[0] is not None and infos[0].st_size >= ROTATE_BYTES:
            for index in range(3, 0, -1):
                if infos[index - 1] is not None:
                    logs[index - 1].replace(logs[index])
        with _open_file(path) as output:
            # The blank separator separates entries in an appended log; a
            # fresh log (or a rotated one) starts directly with its header.
            def record(message):
                stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                output.write((('\n' if output.tell() else '') + stamp + ' ' + message + '\n').encode('ascii'))

            interrupted, handlers = [], {}

            def interrupt(signum, frame):
                if not interrupted:
                    interrupted.append(signum)

            status = 1
            record('start action=' + action)
            try:
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    handlers[signum] = signal.signal(signum, interrupt)
                child = subprocess.Popen([launcher, action], stdout=output, stderr=output)
                forwarded = False
                while True:
                    if interrupted and not forwarded:
                        child.send_signal(interrupted[0])
                        forwarded = True
                    try:
                        status = child.wait(timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                # The launcher's operation guard owns group cleanup and its grace.
                status = 128 + interrupted[0] if interrupted else (status if status >= 0 else 128 - status)
            finally:
                record('exit status=' + str(status))
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
            return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('launcher')
    parser.add_argument('action', choices=('play', 'configure'))
    parser.add_argument('log_path')
    args = parser.parse_args(argv)
    try:
        return run(args.launcher, args.action, args.log_path)
    except (OSError, ValueError, RuntimeError) as error:
        # Do not echo launcher paths, arguments, environment, or OS error filenames.
        message = str(error) if isinstance(error, ValueError) else 'Cannot launch or write app log'
        print('error: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
