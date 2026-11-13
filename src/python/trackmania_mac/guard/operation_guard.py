"""Serialize prefix operations without starting Wine.

The shell must verify `held PREFIX`, then close TRACKMANIA_MAC_OPERATION_FD and
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
    from .. import legacy, markers
except ImportError:
    # Script mode; managed_paths puts the package on the path on import.
    from managed_paths import safe_path  # type: ignore[import-not-found,no-redef]
    from trackmania_mac import legacy, markers  # type: ignore[no-redef]


TOKEN = "TRACKMANIA_MAC_OPERATION_FD"
APP_TOKEN = "TRACKMANIA_MAC_APP_OPERATION_FD"
WINE_SERVER_ROOT = Path("/tmp")
INTERRUPT_GRACE = 2.0
# Opt-in bounded wait, set only by the app launch path. Everything else fails at once.
LOCK_WAIT_ENV = "TRACKMANIA_MAC_LOCK_WAIT"
LOCK_POLL = 0.25
WAIT_MESSAGE = "Waiting for the previous TrackMania session to finish\u2026"
EXIT_BUSY = 75  # EX_TEMPFAIL


# Suffix of the lock beside each prefix: `.<prefix name><suffix>`.
OPERATION_LOCK_SUFFIX = markers.OPERATION_LOCK_SUFFIX


def operation_lock_name(prefix_name, suffix=OPERATION_LOCK_SUFFIX):
    return f".{prefix_name}{suffix}"


def operation_lock_path(prefix):
    prefix = safe_path(str(prefix))
    return prefix.with_name(operation_lock_name(prefix.name))


def legacy_operation_lock_path(prefix):
    """The lock name of earlier releases. A relinked launcher of an earlier release
    still takes only this one, so every operation takes it too, first."""
    prefix = safe_path(str(prefix))
    return prefix.with_name(operation_lock_name(prefix.name, legacy.OPERATION_LOCK_SUFFIX))


def app_lock_path(create=False):
    """One stable lock per canonical HOME, independent of prefix/app locations.

    It sits in the data folder itself, not in its cache, so the migration from
    earlier releases can rename a whole cache folder while holding it.
    """
    value = os.environ.get("HOME", "")
    home = Path(value)
    if (
        not home.is_absolute()
        or any(part in (".", "..") for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("Unsafe app lock HOME")
    home = home.resolve(strict=True)
    fd = _open_directory(home)
    try:
        for name in ("Library", "TrackManiaMacOS"):
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
    return home / "Library/TrackManiaMacOS/.app-install.lock"


def _open_directory(path, dir_fd=None, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if private:
            trusted = info.st_uid == os.getuid() and not info.st_mode & 0o077
        else:
            trusted = (
                info.st_uid in (0, os.getuid())  # type: ignore[assignment]  # only truthiness is used
                and (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
            )
        if not trusted:
            raise ValueError(f"Unsafe lock directory: {path}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _check_file(info, path):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or info.st_nlink != 1
    ):
        raise ValueError(f"Unsafe lock file: {path}")


def _check_identity(fd, path, dir_fd=None):
    opened = os.fstat(fd)
    try:
        named = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError(f"Lock file disappeared: {path}") from error
    _check_file(opened, path)
    _check_file(named, path)
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise ValueError(f"Lock file identity changed: {path}")


def _check_dir_identity(fd, path, dir_fd=None):
    opened = os.fstat(fd)
    try:
        named = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError(f"Lock directory disappeared: {path}") from error
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise ValueError(f"Lock directory identity changed: {path}")


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


class Busy(ValueError):
    """The prefix is held by another session. Maps to EX_TEMPFAIL."""


class _Waiter:
    """One bounded retry budget shared by every busy check of a single run."""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.announced = False

    def retry(self, attempt):
        while True:
            try:
                return attempt()
            except Busy:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise
                if not self.announced:
                    self.announced = True
                    _announce_wait()
                time.sleep(min(LOCK_POLL, remaining))


def _announce_wait():
    try:
        print(WAIT_MESSAGE, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        print(WAIT_MESSAGE.replace("\u2026", "..."), file=sys.stderr, flush=True)


def lock_wait_seconds(environ=None):
    """Seconds an opted-in launch may wait for a busy prefix; 0 means fail at once."""
    value = (os.environ if environ is None else environ).get(LOCK_WAIT_ENV, "")
    if value == "":
        return 0
    if not value.isascii() or not value.isdecimal() or len(value) > 3 or int(value) > 300:
        raise ValueError(f"{LOCK_WAIT_ENV} must be an integer from 0 to 300")
    return int(value)


def acquire_legacy(prefix, waiter=None):
    """The earlier releases' lock for PREFIX, held by the caller and never passed on.

    Always taken before acquire(): one fixed order, so two new operations never
    deadlock, and a session of an earlier release blocks a new one and the reverse.
    """
    path = legacy_operation_lock_path(prefix)
    busy = f"Another operation is using prefix: {prefix}"
    if waiter is not None:
        return waiter.retry(lambda: _acquire(path, busy))
    return _acquire(path, busy)


def acquire(prefix, waiter=None):
    """Return an exclusively locked FD, owned by the caller. Never unlink it."""
    path = operation_lock_path(prefix)
    busy = f"Another operation is using prefix: {prefix}"
    if waiter is not None:
        return waiter.retry(lambda: _acquire(path, busy))
    return _acquire(path, busy)


def _acquire(path, busy):
    parent = _open_directory(path.parent)
    try:
        fd = _open_lock(path.name, parent, create=True)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise Busy(busy) from error
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
    token = os.environ.get(token_name, "")
    if not token or not token.isascii() or not token.isdecimal() or len(token) > 10:
        raise ValueError(f"Invalid {token_name}")
    fd = int(token)
    if fd < 3 or fd > 0x7FFFFFFF:
        raise ValueError(f"Invalid {token_name}")
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
                raise ValueError("Operation token does not hold a lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _check_identity(fd, path)
        finally:
            os.close(probe)
    finally:
        os.close(parent)
    return fd


def wine_server_dir(prefix):
    """Match the pinned Wine server/request.c create_server_dir layout."""
    prefix = safe_path(str(prefix))
    try:
        info = prefix.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError(f"Unsafe Wine prefix: {prefix}")
    return WINE_SERVER_ROOT / f".wine-{os.getuid()}" / f"server-{info.st_dev:x}-{info.st_ino:x}"


def wine_server_lock(prefix):
    directory = wine_server_dir(prefix)
    return None if directory is None else directory / "lock"


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
                    raise Busy(f"Wine is active for prefix: {prefix}") from error
                raise
            else:
                fcntl.lockf(fd, fcntl.LOCK_UN, 1, 0, os.SEEK_SET)
            _check_identity(fd, path)
            # The probe held directory FDs for its whole span, so re-verify
            # the private chain survived the Wine create_server_dir layout,
            # which may legitimately remove and recreate its server directory
            # between our open and the probe.
            _check_dir_identity(directories[-1], path.parent.name, directories[-2])
            _check_dir_identity(directories[-2], path.parents[1].name, directories[-3])
        finally:
            os.close(fd)
    except FileNotFoundError:
        return
    finally:
        for directory in reversed(directories):
            os.close(directory)


def _ancestors():
    """Our pid and every parent up to launchd, so reap never signals its own chain."""
    pids = {os.getpid()}
    pid = os.getppid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        result = subprocess.run(
            ["/bin/ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            pid = int(result.stdout.strip())
        except ValueError:
            break
    return pids


def _server_dir_holders(directory):
    """PIDs of this user's processes holding a Wine server directory or a file in it.

    Matches on the names lsof reports rather than with `+d`: wineserver unlinks
    each tmpmap file as soon as it creates it, so installer leftovers often hold
    only mappings of deleted files that a directory scan cannot see. lsof still
    reports their original paths.
    """
    result = subprocess.run(
        ["/usr/sbin/lsof", "-w", "-n", "-P", "-u", str(os.getuid()), "-F", "pn"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    # lsof exits 1 when some process could not be listed; any other failure is an error.
    if result.returncode not in (0, 1):
        raise RuntimeError(f"lsof failed: {result.stderr.strip() or result.returncode}")
    root = str(directory)
    inside = root.rstrip("/") + "/"
    holders = set()
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdecimal():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            name = line[1:]
            if name == root or name.startswith(inside):
                holders.add(pid)
    return holders


def reap(prefix, grace=INTERRUPT_GRACE):
    """Stop Wine processes still attached to a prefix whose wineserver is gone.

    Wine reparents services.exe, explorer.exe and friends to launchd, so a
    process-tree walk cannot find them. Each still holds the dead server's
    directory open, which names its prefix. Does not touch the operation lock.
    """
    directory = wine_server_dir(prefix)
    if directory is None:
        return 0
    # /tmp is shared: open the chain like check_inactive (no symlinks, and
    # .wine-<uid> and the server dir private to us) so another user cannot point
    # this at a directory our unrelated processes hold open.
    directories = []
    try:
        root = directory.parents[1].resolve()
        directories.append(_open_directory(root))
        directories.append(_open_directory(directory.parent.name, directories[-1], private=True))
        directories.append(_open_directory(directory.name, directories[-1], private=True))
    except FileNotFoundError:
        for fd in reversed(directories):
            os.close(fd)
        return 0
    except BaseException:
        for fd in reversed(directories):
            os.close(fd)
        raise
    try:
        verified = root / directory.parent.name / directory.name
        excluded = _ancestors()
        victims = _server_dir_holders(verified) - excluded
        # Signal only if the path still names the directory we validated.
        _check_dir_identity(directories[-1], directory.name, directories[-2])
        _check_dir_identity(directories[-2], directory.parent.name, directories[-3])
        return _stop(verified, victims, excluded, grace)
    finally:
        for fd in reversed(directories):
            os.close(fd)


def _stop(directory, victims, excluded, grace):
    if not victims:
        return 0
    stopped = len(victims)
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    remaining = victims
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = _server_dir_holders(directory) - excluded
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    print(f"Stopped {stopped} Wine processes left behind by the installer.", flush=True)
    return stopped


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
                event = select.kevent(
                    pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                    fflags=select.KQ_NOTE_EXIT,
                )
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
        except (ValueError, RuntimeError) as error:
            raise signal_error from error


def run(prefix, command, wait=0):
    """Run a command with the operation FD inherited; return shell exit status.

    A positive wait is a budget in seconds for the prefix lock and for Wine to
    finish exiting; a prefix still busy after it raises Busy.
    """
    if not command:
        raise ValueError("run requires a command")
    waiter = _Waiter(wait) if wait > 0 else None
    # This process keeps the earlier releases' lock for the child's whole run; the
    # child never inherits it, so a detached wineserver cannot keep it held.
    legacy_fd = acquire_legacy(prefix, waiter)
    try:
        fd = acquire(prefix, waiter)
        try:
            if waiter is None:
                check_inactive(prefix)
            else:
                waiter.retry(lambda: check_inactive(prefix))
            return _run(command, {TOKEN: fd})
        finally:
            os.close(fd)
    finally:
        os.close(legacy_fd)


def app_run(prefix, command):
    """Borrow the prefix FD; own only the app FD. Installer Wine may be active."""
    if not command:
        raise ValueError("app-run requires a command")
    prefix_fd = held(prefix)
    fd = _acquire(app_lock_path(create=True), "Another app installation is in progress")
    try:
        # Finish nested cleanup before the outer prefix runner's SIGKILL.
        return _run(command, {TOKEN: prefix_fd, APP_TOKEN: fd}, INTERRUPT_GRACE / 2)
    finally:
        os.close(fd)


def _run(command, tokens, grace=INTERRUPT_GRACE):
    handlers: dict = {}
    interrupted: list[int] = []

    def interrupt(signum, frame):
        if not interrupted:
            interrupted.append(signum)

    try:
        # SIG_IGN is inherited across exec and would autoreap the group leader.
        handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, interrupt)
        env = dict(os.environ, **{name: str(fd) for name, fd in tokens.items()})
        child = subprocess.Popen(
            command, env=env, pass_fds=tuple(tokens.values()), start_new_session=True
        )
        while True:
            if interrupted:
                # Keep the leader unreaped until the final group signal, so its
                # process group ID cannot be recycled while we clean up.
                for signum in (interrupted[0], signal.SIGCONT):  # type: ignore[assignment]  # signum is an int at runtime
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
    parser.add_argument(
        "action", choices=("run", "held", "app-run", "app-held", "app-lock-held", "reap")
    )
    parser.add_argument("prefix")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action == "app-lock-held":
            # The migration holds the app lock for every title at once; its
            # relink step checks only that token. PREFIX must be "-".
            if args.prefix != "-" or args.command:
                raise ValueError("app-lock-held takes only -")
            app_held()
            return 0
        if args.action in ("held", "app-held"):
            if args.command:
                raise ValueError("held does not accept a command")
            held(args.prefix)
            if args.action == "app-held":
                app_held()
            return 0
        if args.action == "reap":
            if args.command:
                raise ValueError("reap does not accept a command")
            reap(args.prefix)
            return 0
        if args.action == "app-run":
            return app_run(args.prefix, args.command)
        return run(args.prefix, args.command, lock_wait_seconds())
    except Busy as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_BUSY
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
