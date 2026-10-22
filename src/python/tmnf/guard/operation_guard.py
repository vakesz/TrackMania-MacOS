"""Serialize prefix operations without starting Wine.

The shell must verify `held PREFIX`, then close TMNF_OPERATION_FD and
unset the token in each subprocess that execs wine or wineserver. Otherwise
a detached server can retain the operation lock after the shell exits.
"""

import argparse
from contextlib import closing
import errno
import fcntl
import os
import select
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

try:
    from .managed_paths import safe_path
except ImportError:
    from managed_paths import safe_path


TOKEN = 'TMNF_OPERATION_FD'
APP_TOKEN = 'TMNF_APP_OPERATION_FD'
WINE_SERVER_ROOT = Path('/tmp')
INTERRUPT_GRACE = 2.0


def operation_lock_path(prefix):
    prefix = safe_path(str(prefix))
    return prefix.with_name(f'.{prefix.name}.tmnf-operation.lock')


def app_lock_path(create=False):
    """One stable lock per canonical HOME, independent of prefix/app locations."""
    value = os.environ.get('HOME', '')
    home = Path(value)
    if (not home.is_absolute() or any(part in ('.', '..') for part in value.split('/')) or
            any(ord(char) < 32 for char in value)):
        raise ValueError('Unsafe app lock HOME')
    home = home.resolve(strict=True)
    fd = _open_directory(home)
    try:
        for name in ('Library', 'Caches', 'TMNF'):
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = _open_directory(name, fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)
    return home / 'Library/Caches/TMNF/.app-install.lock'


def _open_directory(path, dir_fd=None, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if private:
            trusted = info.st_uid == os.getuid() and not info.st_mode & 0o077
        else:
            trusted = (info.st_uid in (0, os.getuid()) and
                       (not info.st_mode & 0o022 or
                        (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)))
        if not trusted:
            raise ValueError(f'Unsafe lock directory: {path}')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _check_file(info, path):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o022 or info.st_nlink != 1):
        raise ValueError(f'Unsafe lock file: {path}')


def _check_identity(fd, path, dir_fd=None):
    opened = os.fstat(fd)
    try:
        named = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError(f'Lock file disappeared: {path}') from error
    _check_file(opened, path)
    _check_file(named, path)
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise ValueError(f'Lock file identity changed: {path}')


def _check_dir_identity(fd, path, dir_fd=None):
    opened = os.fstat(fd)
    try:
        named = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError(f'Lock directory disappeared: {path}') from error
    if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode) or
            (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise ValueError(f'Lock directory identity changed: {path}')


def _open_lock(path, dir_fd, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        info = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            return _open_lock(path, dir_fd)
    else:
        _check_file(info, path)
        fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        _check_identity(fd, path, dir_fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def acquire(prefix):
    """Return an exclusively locked FD, owned by the caller. Never unlink it."""
    return _acquire(operation_lock_path(prefix), f'Another operation is using prefix: {prefix}')


def _acquire(path, busy):
    parent = _open_directory(path.parent)
    try:
        fd = _open_lock(path.name, parent, create=True)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise ValueError(busy) from error
                raise
            _check_identity(fd, path)
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


def held(prefix):
    """Validate the inherited token without unlocking or closing its FD."""
    return _held(operation_lock_path(prefix), TOKEN)


def app_held():
    return _held(app_lock_path(), APP_TOKEN)


def _held(path, token_name):
    token = os.environ.get(token_name, '')
    if not token or not token.isascii() or not token.isdecimal() or len(token) > 10:
        raise ValueError(f'Invalid {token_name}')
    fd = int(token)
    if fd < 3 or fd > 0x7fffffff:
        raise ValueError(f'Invalid {token_name}')
    parent = _open_directory(path.parent)
    try:
        _check_identity(fd, path.name, parent)
        probe = _open_lock(path.name, parent)
        try:
            # An unlocked FD is not a token. A separate open must conflict, but
            # flock on the inherited open-file-description must still succeed.
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
            else:
                raise ValueError('Operation token does not hold a lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _check_identity(fd, path)
        finally:
            os.close(probe)
    finally:
        os.close(parent)
    return fd


def wine_server_lock(prefix):
    """Match the pinned Wine server/request.c create_server_dir layout."""
    prefix = safe_path(str(prefix))
    try:
        info = prefix.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o022):
        raise ValueError(f'Unsafe Wine prefix: {prefix}')
    return (WINE_SERVER_ROOT / f'.wine-{os.getuid()}' /
            f'server-{info.st_dev:x}-{info.st_ino:x}' / 'lock')


def check_inactive(prefix):
    """Probe only an existing server lock. Missing prefix/lock means inactive."""
    path = wine_server_lock(prefix)
    if path is None:
        return
    directories = []
    try:
        directories.append(_open_directory(path.parents[2].resolve()))
        directories.append(_open_directory(path.parents[1].name, directories[-1], private=True))
        directories.append(_open_directory(path.parent.name, directories[-1], private=True))
        fd = _open_lock(path.name, directories[-1])
        try:
            try:
                # Wine's acquire_lock uses F_WRLCK, SEEK_SET, start=0, len=1.
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, os.SEEK_SET)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise ValueError(f'Wine is active for prefix: {prefix}') from error
                raise
            else:
                fcntl.lockf(fd, fcntl.LOCK_UN, 1, 0, os.SEEK_SET)
            _check_identity(fd, path)
            # The probe held directory FDs for its whole span, so re-verify
            # the private chain survived the Wine create_server_dir layout,
            # which may legitimately remove and recreate its server directory
            # between our open and the probe.
            _check_dir_identity(directories[-1], path.parent.name,
                                directories[-2])
            _check_dir_identity(directories[-2], path.parents[1].name,
                                directories[-3])
        finally:
            os.close(fd)
    except FileNotFoundError:
        return
    finally:
        for directory in reversed(directories):
            os.close(directory)


def _signal_group(pid, signum):
    """Signal only a group whose leader is our still-unreaped child."""
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
    except PermissionError as signal_error:
        # Darwin can return EPERM for a zombie-only group. Cleanup must never
        # die here: observe exit without wait/poll (reaping here would let
        # the PGID be reused before SIGKILL). A live leader is not safe to
        # ignore; propagate the original signal failure if exit is unproven.
        try:
            with closing(select.kqueue()) as queue:
                event = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                      flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                      fflags=select.KQ_NOTE_EXIT)
                try:
                    events = queue.control([event], 1, 0)
                except ProcessLookupError:
                    return
                except OSError as error:
                    if error.errno == errno.ESRCH:
                        return
                    raise signal_error from error
                for kevent in events:
                    if kevent.flags & select.KQ_EV_ERROR:
                        if kevent.data == errno.ESRCH:
                            return
                        raise signal_error
                    if kevent.fflags & select.KQ_NOTE_EXIT:
                        return
                raise signal_error
        except (ValueError, RuntimeError):
            raise signal_error


def run(prefix, command):
    """Run a command with the operation FD inherited; return shell exit status."""
    if not command:
        raise ValueError('run requires a command')
    fd = acquire(prefix)
    try:
        check_inactive(prefix)
        return _run(command, {TOKEN: fd})
    finally:
        os.close(fd)


def app_run(prefix, command):
    """Borrow the prefix FD; own only the app FD. Installer Wine may be active."""
    if not command:
        raise ValueError('app-run requires a command')
    prefix_fd = held(prefix)
    fd = _acquire(app_lock_path(create=True), 'Another app installation is in progress')
    try:
        # Finish nested cleanup before the outer prefix runner's SIGKILL.
        return _run(command, {TOKEN: prefix_fd, APP_TOKEN: fd}, INTERRUPT_GRACE / 2)
    finally:
        os.close(fd)


def _run(command, tokens, grace=INTERRUPT_GRACE):
    handlers = {}
    interrupted = []

    def interrupt(signum, frame):
        if not interrupted:
            interrupted.append(signum)

    try:
        # SIG_IGN is inherited across exec and would autoreap the group leader.
        handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, interrupt)
        env = dict(os.environ, **{name: str(fd) for name, fd in tokens.items()})
        child = subprocess.Popen(command, env=env, pass_fds=tuple(tokens.values()), start_new_session=True)
        while True:
            if interrupted:
                # Keep the leader unreaped until the final group signal, so its
                # process group ID cannot be recycled while we clean up.
                for signum in (interrupted[0], signal.SIGCONT):
                    _signal_group(child.pid, signum)
                deadline = time.monotonic() + grace
                while time.monotonic() < deadline:
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                _signal_group(child.pid, signal.SIGKILL)
                try:
                    child.wait(timeout=INTERRUPT_GRACE)
                except subprocess.TimeoutExpired:
                    # Best effort: the group was SIGKILLed and the interrupted
                    # status is authoritative even if reaping is still pending.
                    pass
                return 128 + interrupted[0]
            # Do not reap between noticing an interruption and signalling the
            # group. If exit wins this race, there is no group cleanup to do.
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, handlers)
            try:
                status = None if interrupted else child.poll()
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if status is not None:
                if interrupted:
                    return 128 + interrupted[0]
                return status if status >= 0 else 128 - status
            time.sleep(0.05)
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'held', 'app-run', 'app-held'))
    parser.add_argument('prefix')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action in ('held', 'app-held'):
            if args.command:
                raise ValueError('held does not accept a command')
            held(args.prefix)
            if args.action == 'app-held':
                app_held()
            return 0
        if args.action == 'app-run':
            return app_run(args.prefix, args.command)
        return run(args.prefix, args.command)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
