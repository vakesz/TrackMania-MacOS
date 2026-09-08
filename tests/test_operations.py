import errno
import fcntl
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lib import operation_guard as guard

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'lib/operation_guard.py'


class Operations(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-operation-test-', dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prefix = self.root / "prefix with spaces and 'quotes"
        self.server_root = self.root / 'servers'
        self.server_root.mkdir(mode=0o700)
        patch = mock.patch.object(guard, 'WINE_SERVER_ROOT', self.server_root)
        patch.start()
        self.addCleanup(patch.stop)
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        self.env.pop(guard.TOKEN, None)
        self.env.pop(guard.APP_TOKEN, None)
        self.home = self.root / 'app-home'
        self.home.mkdir()
        self.env['HOME'] = str(self.home)

    def cli(self, *args, env=None, pass_fds=()):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              env=self.env if env is None else env, pass_fds=pass_fds,
                              text=True, capture_output=True, timeout=10, check=False)

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def spawn(self, command, **kwargs):
        process = subprocess.Popen(command, env=self.env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        self.addCleanup(self.stop, process)
        return process

    def wait_file(self, path, process):
        deadline = time.monotonic() + 5
        while not path.exists():
            if process.poll() is not None:
                self.fail(f'Process exited early: {process.communicate()}')
            if time.monotonic() > deadline:
                self.fail(f'Timed out waiting for {path}')
            time.sleep(0.01)

    def server_lock(self, prefix=None):
        prefix = prefix or self.prefix
        prefix.mkdir(parents=True, exist_ok=True)
        path = guard.wine_server_lock(prefix)
        path.parent.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(b'untouched server lock\n')
        path.chmod(0o600)
        return path

    def hold_record_lock(self, path, start=0):
        ready = self.root / 'record-ready'
        ready.unlink(missing_ok=True)
        process = self.spawn([
            sys.executable, '-c',
            ('import fcntl, os, pathlib, sys; '
             'fd = os.open(sys.argv[1], os.O_RDWR); '
             'fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, int(sys.argv[3])); '
             'pathlib.Path(sys.argv[2]).touch(); sys.stdin.read()'),
            str(path), str(ready), str(start)], stdin=subprocess.PIPE)
        self.wait_file(ready, process)
        return process

    def test_missing_prefix_and_server_lock_are_inactive_without_creation(self):
        guard.check_inactive(self.prefix)
        self.assertFalse(self.prefix.exists())
        self.assertEqual(list(self.server_root.iterdir()), [])
        self.prefix.mkdir()
        guard.check_inactive(self.prefix)
        self.assertEqual(list(self.server_root.iterdir()), [])
        path = self.server_lock()
        path.unlink()
        guard.check_inactive(self.prefix)
        self.assertFalse(path.exists())

    def test_layout_uses_prefix_device_and_inode(self):
        self.prefix.mkdir()
        info = self.prefix.stat()
        with mock.patch.object(guard, 'WINE_SERVER_ROOT', Path('/tmp')):
            self.assertEqual(guard.wine_server_lock(self.prefix),
                             Path(f'/tmp/.wine-{os.getuid()}/server-{info.st_dev:x}-{info.st_ino:x}/lock'))

    def test_record_lock_refuses_only_the_selected_prefix(self):
        path = self.server_lock()
        before = path.read_bytes(), path.stat().st_ino
        holder = self.hold_record_lock(path)
        with self.assertRaisesRegex(ValueError, 'Wine is active'):
            guard.check_inactive(self.prefix)
        other = self.root / 'other' / self.prefix.name
        self.server_lock(other)
        guard.check_inactive(other)
        self.assertIsNone(holder.poll())
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)
        holder.communicate('', timeout=5)
        guard.check_inactive(self.prefix)
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)

    def test_probe_locks_only_byte_zero(self):
        path = self.server_lock()
        holder = self.hold_record_lock(path, start=1)
        guard.check_inactive(self.prefix)
        self.assertIsNone(holder.poll())

    def test_active_prefix_prevents_child_and_releases_operation_fd(self):
        self.hold_record_lock(self.server_lock())
        with mock.patch.object(guard.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'Wine is active'):
                guard.run(self.prefix, [sys.executable, '-c', 'pass'])
            spawn.assert_not_called()
        fd = guard.acquire(self.prefix)
        os.close(fd)

    def test_server_lock_symlink_fifo_and_writable_file_fail_closed(self):
        path = self.server_lock()
        target = self.root / 'target'
        target.write_bytes(b'do not overwrite')
        for kind in ('symlink', 'fifo', 'writable', 'directory', 'hardlink'):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == 'symlink':
                    path.symlink_to(target)
                elif kind == 'fifo':
                    os.mkfifo(path)
                elif kind == 'directory':
                    path.mkdir()
                elif kind == 'hardlink':
                    os.link(target, path)
                else:
                    path.write_bytes(b'unsafe')
                    path.chmod(0o666)
                with self.assertRaises((ValueError, OSError)):
                    guard.check_inactive(self.prefix)
                if kind == 'directory':
                    path.rmdir()
                    path.touch()
        self.assertEqual(target.read_bytes(), b'do not overwrite')

    def test_server_directories_cannot_be_symlinks_or_public(self):
        path = self.server_lock()
        for directory in (path.parent, path.parent.parent):
            with self.subTest(directory=directory):
                directory.chmod(0o755)
                with self.assertRaisesRegex(ValueError, 'Unsafe lock directory'):
                    guard.check_inactive(self.prefix)
                directory.chmod(0o700)
                moved = directory.with_name(directory.name + '-moved')
                directory.rename(moved)
                directory.symlink_to(moved, target_is_directory=True)
                with self.assertRaises(OSError):
                    guard.check_inactive(self.prefix)
                directory.unlink()
                moved.rename(directory)

    def test_wrong_owner_and_unexpected_record_lock_errors_fail_closed(self):
        self.server_lock()
        with mock.patch.object(guard.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, 'Unsafe Wine prefix'):
                guard.check_inactive(self.prefix)
            with self.assertRaises(ValueError):
                guard.acquire(self.prefix)
        with mock.patch.object(guard.fcntl, 'lockf', side_effect=OSError(errno.ENOLCK, 'no locks')), \
                self.assertRaises(OSError):
            guard.check_inactive(self.prefix)

    def test_operation_lock_is_stable_and_never_truncated(self):
        path = guard.operation_lock_path(self.prefix)
        path.write_bytes(b'preserve this')
        path.chmod(0o600)
        inode = path.stat().st_ino
        for _ in range(2):
            result = self.cli('run', self.prefix, sys.executable, '-c', 'pass')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.read_bytes(), b'preserve this')
        self.assertFalse(self.prefix.exists())

    def test_unsafe_operation_lock_and_parent_are_rejected(self):
        path = guard.operation_lock_path(self.prefix)
        target = self.root / 'target'
        target.write_bytes(b'preserve this')
        for kind in ('symlink', 'fifo', 'writable', 'hardlink', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    path.symlink_to(target)
                elif kind == 'fifo':
                    os.mkfifo(path)
                elif kind == 'hardlink':
                    os.link(target, path)
                elif kind == 'directory':
                    path.mkdir()
                else:
                    path.write_bytes(b'unsafe')
                    path.chmod(0o666)
                result = self.cli('run', self.prefix, sys.executable, '-c', 'pass')
                self.assertNotEqual(result.returncode, 0)
                if kind == 'directory':
                    path.rmdir()
                else:
                    path.unlink()
        self.root.chmod(0o777)
        try:
            with self.assertRaisesRegex(ValueError, 'Unsafe lock directory'):
                guard.acquire(self.prefix)
        finally:
            self.root.chmod(0o700)
        self.assertEqual(target.read_bytes(), b'preserve this')

    def test_concurrent_commands_refuse_busy_prefix_but_allow_other_root(self):
        ready = self.root / 'operation-ready'
        first = self.spawn([
            sys.executable, str(SCRIPT), 'run', str(self.prefix), sys.executable, '-c',
            ('import os, pathlib, sys; os.close(int(os.environ.pop("MACTMNF_OPERATION_FD"))); '
             'pathlib.Path(sys.argv[1]).touch(); sys.stdin.read()'),
            str(ready)], stdin=subprocess.PIPE)
        self.wait_file(ready, first)
        result = self.cli('run', self.prefix, sys.executable, '-c', 'print("must not run")')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another operation', result.stderr)
        self.assertEqual(result.stdout, '')
        other = self.root / 'other' / self.prefix.name
        other.parent.mkdir()
        result = self.cli('run', other, sys.executable, '-c', 'pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        first.communicate('', timeout=5)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.cli('run', self.prefix, sys.executable, '-c', 'pass').returncode, 0)

    def test_nested_shell_held_checks_keep_parent_lock(self):
        result = self.cli(
            'run', self.prefix, '/bin/bash', '-eu', '-c',
            '"$1" "$2" held "$3"; "$1" "$2" held "$3"; '
            'if "$1" "$2" run "$3" /usr/bin/true; then exit 99; fi',
            'fixture', sys.executable, SCRIPT, self.prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Another operation', result.stderr)

    def test_app_run_borrows_prefix_fd_without_wine_probe(self):
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        with mock.patch.dict(os.environ, {guard.TOKEN: str(fd), 'HOME': str(self.home)}), \
                mock.patch.object(guard, 'check_inactive', side_effect=AssertionError('Wine probe')):
            result = guard.app_run(self.prefix, [sys.executable, str(SCRIPT), 'app-held', str(self.prefix)])
            self.assertEqual(result, 0)
            self.assertEqual(guard.held(self.prefix), fd)
            with self.assertRaisesRegex(ValueError, 'Another operation'):
                guard.acquire(self.prefix)

    def test_app_lock_serializes_prefixes_but_not_other_homes(self):
        ready = self.root / 'app-ready'
        first = self.spawn([
            sys.executable, str(SCRIPT), 'run', str(self.prefix),
            sys.executable, str(SCRIPT), 'app-run', str(self.prefix),
            sys.executable, '-c',
            'import pathlib, sys; pathlib.Path(sys.argv[1]).touch(); sys.stdin.read()',
            str(ready)], stdin=subprocess.PIPE)
        self.wait_file(ready, first)
        other = self.root / 'other-prefix'
        command = ('run', other, sys.executable, SCRIPT, 'app-run', other,
                   sys.executable, '-c', 'print("mutation")')
        result = self.cli(*command)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another app installation', result.stderr)
        self.assertEqual(result.stdout, '')
        other_home = self.root / 'other-home'
        other_home.mkdir()
        result = self.cli(*command, env=dict(self.env, HOME=str(other_home)))
        self.assertEqual(result.returncode, 0, result.stderr)
        first.communicate('', timeout=5)
        self.assertEqual(first.returncode, 0)
        path = self.home / 'Library/Caches/MacTMNF/.app-install.lock'
        path.write_bytes(b'keep lock contents')
        inode = path.stat().st_ino
        self.assertEqual(self.cli(*command).returncode, 0)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(path.read_bytes(), b'keep lock contents')

    def test_app_forged_tokens_and_unsafe_paths_fail_closed(self):
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        env = dict(self.env, **{guard.TOKEN: str(fd)})
        command = ('app-run', self.prefix, sys.executable, '-c', 'print("mutation")')
        self.assertNotEqual(self.cli(*command).returncode, 0)
        self.assertFalse((self.home / 'Library').exists())
        self.assertEqual(self.cli(*command, env=env, pass_fds=(fd,)).returncode, 0)
        path = self.home / 'Library/Caches/MacTMNF/.app-install.lock'
        unlocked = os.open(path, os.O_RDWR)
        try:
            result = self.cli('app-held', self.prefix,
                              env=dict(env, **{guard.APP_TOKEN: str(unlocked)}), pass_fds=(fd, unlocked))
            self.assertNotEqual(result.returncode, 0)
        finally:
            os.close(unlocked)
        target = self.root / 'app-lock-target'
        target.write_text('untouched')
        for kind in ('symlink', 'hardlink', 'writable', 'fifo'):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == 'symlink':
                    path.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(target, path)
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.touch(mode=0o666)
                    path.chmod(0o666)
                result = self.cli(*command, env=env, pass_fds=(fd,))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
        path.unlink()
        for directory in (path.parent, path.parent.parent, self.home / 'Library'):
            directory.chmod(0o777)
            result = self.cli(*command, env=env, pass_fds=(fd,))
            self.assertNotEqual(result.returncode, 0)
            directory.chmod(0o700)
            moved = directory.with_name(directory.name + '-moved')
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
            result = self.cli(*command, env=env, pass_fds=(fd,))
            self.assertNotEqual(result.returncode, 0)
            directory.unlink()
            moved.rename(directory)
        self.assertEqual(target.read_text(), 'untouched')

    def test_interrupted_app_run_releases_only_app_lock(self):
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        self.env[guard.TOKEN] = str(fd)
        ready = self.root / 'app-interrupt-ready'
        process = self.spawn([
            sys.executable, str(SCRIPT), 'app-run', str(self.prefix), sys.executable, '-c',
            'import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)',
            str(ready)], pass_fds=(fd,))
        self.wait_file(ready, process)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 143, (stdout, stderr))
        self.assertEqual(self.cli('held', self.prefix, pass_fds=(fd,)).returncode, 0)
        with self.assertRaisesRegex(ValueError, 'Another operation'):
            guard.acquire(self.prefix)
        result = self.cli('app-run', self.prefix, sys.executable, str(SCRIPT),
                          'app-held', self.prefix, pass_fds=(fd,))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_outer_interruption_cleans_nested_app_group(self):
        ready = self.root / 'nested-app-ready'
        process = self.spawn([
            sys.executable, str(SCRIPT), 'run', str(self.prefix),
            sys.executable, str(SCRIPT), 'app-run', str(self.prefix),
            sys.executable, '-c',
            'import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            'pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready)])
        self.wait_file(ready, process)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 143, (stdout, stderr))
        result = self.cli('run', self.prefix, sys.executable, SCRIPT, 'app-run', self.prefix,
                          sys.executable, SCRIPT, 'app-held', self.prefix)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_tokens_are_rejected(self):
        for token in (None, '', 'no', '-1', ' 3', '3\n', '0', '1', '2', '999999',
                      '99999999999999999999', '\u0663'):
            with self.subTest(token=token):
                env = dict(self.env)
                if token is not None:
                    env[guard.TOKEN] = token
                result = self.cli('held', self.prefix, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Traceback', result.stderr)

    def test_token_must_be_locked_matching_open_file_description(self):
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        other = self.root / 'other-prefix'
        self.assertNotEqual(self.cli('held', other, env=dict(self.env, **{guard.TOKEN: str(fd)}),
                                     pass_fds=(fd,)).returncode, 0)
        separate = os.open(guard.operation_lock_path(self.prefix), os.O_RDWR)
        try:
            result = self.cli('held', self.prefix,
                              env=dict(self.env, **{guard.TOKEN: str(separate)}), pass_fds=(separate,))
            self.assertNotEqual(result.returncode, 0)
        finally:
            os.close(separate)
        fcntl.flock(fd, fcntl.LOCK_UN)
        result = self.cli('held', self.prefix, env=dict(self.env, **{guard.TOKEN: str(fd)}),
                          pass_fds=(fd,))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not hold a lock', result.stderr)

    def test_replaced_lock_file_invalidates_token(self):
        fd = guard.acquire(self.prefix)
        try:
            path = guard.operation_lock_path(self.prefix)
            path.rename(path.with_name('old-lock'))
            path.touch(mode=0o600)
            result = self.cli('held', self.prefix, env=dict(self.env, **{guard.TOKEN: str(fd)}),
                              pass_fds=(fd,))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('identity changed', result.stderr)
        finally:
            os.close(fd)

    def test_held_matches_canonical_path_through_parent_alias(self):
        parent = self.root / 'real'
        parent.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(parent, target_is_directory=True)
        prefix = parent / self.prefix.name
        fd = guard.acquire(prefix)
        try:
            result = self.cli('held', alias / prefix.name,
                              env=dict(self.env, **{guard.TOKEN: str(fd)}), pass_fds=(fd,))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(guard.operation_lock_path(alias / prefix.name),
                             guard.operation_lock_path(prefix))
        finally:
            os.close(fd)

    def test_unsafe_prefix_and_cli_arguments_are_rejected(self):
        target = self.root / 'target-prefix'
        target.mkdir()
        self.prefix.symlink_to(target, target_is_directory=True)
        for prefix in (self.prefix, 'relative', str(self.root) + '/../prefix', '/tmp'):
            with self.subTest(prefix=prefix):
                result = self.cli('run', prefix, sys.executable, '-c', 'pass')
                self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.cli('run', target).returncode, 0)
        self.assertNotEqual(self.cli('held', target, 'extra').returncode, 0)

    def test_file_disappearing_after_open_fails_closed(self):
        path = self.server_lock()
        original = guard._check_identity

        def disappear(fd, name, dir_fd=None):
            if dir_fd is not None:
                path.rename(path.with_name('moved-lock'))
            return original(fd, name, dir_fd)

        with mock.patch.object(guard, '_check_identity', side_effect=disappear), \
                self.assertRaisesRegex(ValueError, 'Lock file disappeared'):
            guard.check_inactive(self.prefix)

    def test_normal_and_signal_exit_status_and_spawn_failure(self):
        for code, expected in [('raise SystemExit(37)', 37),
                               ('import os, signal; os.kill(os.getpid(), signal.SIGTERM)', 143)]:
            result = self.cli('run', self.prefix, sys.executable, '-c', code)
            self.assertEqual(result.returncode, expected, result.stderr)
        result = self.cli('run', self.prefix, self.root / 'nonexistent-command')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.cli('run', self.prefix, sys.executable, '-c', 'pass').returncode, 0)

    def test_interruption_forwards_signal_and_kills_only_own_group(self):
        unrelated = self.spawn([sys.executable, '-c', 'import time; time.sleep(60)'])
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signum=signum):
                ready = self.root / f'ready-{signum}'
                received = self.root / f'received-{signum}'
                descendant_ready = self.root / f'descendant-{signum}'
                code = (
                    'import os, pathlib, signal, subprocess, sys, time\n'
                    'def received(sig, frame):\n'
                    '    pathlib.Path(sys.argv[2]).write_text(str(sig))\n'
                    '    if sig == signal.SIGTERM: sys.exit(0)\n'
                    'for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n'
                    '    signal.signal(sig, received)\n'
                    'subprocess.Popen([sys.executable, "-c", '
                    '"import pathlib, signal, sys, time; "'
                    '"[signal.signal(s, signal.SIG_IGN) for s in "'
                    '"(signal.SIGINT, signal.SIGTERM, signal.SIGHUP)]; "'
                    '"pathlib.Path(sys.argv[1]).touch(); time.sleep(60)", sys.argv[3]])\n'
                    'pathlib.Path(sys.argv[1]).write_text(str(os.getpgrp()))\n'
                    'time.sleep(60)\n')
                process = self.spawn([sys.executable, str(SCRIPT), 'run', str(self.prefix),
                                      sys.executable, '-c', code, str(ready), str(received),
                                      str(descendant_ready)])
                self.wait_file(ready, process)
                self.wait_file(descendant_ready, process)
                pgid = int(ready.read_text())
                self.assertNotEqual(pgid, os.getpgrp())
                started = time.monotonic()
                process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 128 + signum, (stdout, stderr))
                self.assertLess(time.monotonic() - started, 7)
                self.assertEqual(received.read_text(), str(int(signum)))
                self.assertIsNone(unrelated.poll())
                deadline = time.monotonic() + 3
                while True:
                    try:
                        os.killpg(pgid, 0)
                    except ProcessLookupError:
                        break
                    if time.monotonic() > deadline:
                        self.fail('Guarded process group survived interruption')
                    time.sleep(0.01)
                self.assertEqual(self.cli('run', self.prefix, sys.executable, '-c', 'pass').returncode, 0)

    def test_interruption_after_graceful_leader_exit_returns_signal_status(self):
        ready = self.root / 'graceful-ready'
        received = self.root / 'graceful-term'
        code = (
            'import pathlib, signal, sys, time\n'
            'def terminate(sig, frame):\n'
            '    pathlib.Path(sys.argv[2]).touch()\n'
            '    sys.exit(0)\n'
            'signal.signal(signal.SIGTERM, terminate)\n'
            'pathlib.Path(sys.argv[1]).touch()\n'
            'time.sleep(60)\n')
        for ignore_sigchld in (False, True):
            with self.subTest(ignore_sigchld=ignore_sigchld):
                ready.unlink(missing_ok=True)
                received.unlink(missing_ok=True)
                command = [sys.executable, str(SCRIPT), 'run', str(self.prefix),
                           sys.executable, '-c', code, str(ready), str(received)]
                if ignore_sigchld:
                    command = [sys.executable, '-c',
                               ('import os, signal, sys; '
                                'signal.signal(signal.SIGCHLD, signal.SIG_IGN); '
                                'os.execv(sys.argv[1], sys.argv[1:])')] + command
                process = self.spawn(command)
                self.wait_file(ready, process)
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 143, (stdout, stderr))
                self.assertTrue(received.exists())
                self.assertEqual(self.cli('run', self.prefix, sys.executable, '-c', 'pass').returncode, 0)

    def test_sigchld_disposition_is_restored_after_exit_and_spawn_failure(self):
        original = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        try:
            self.assertEqual(guard.run(self.prefix, [sys.executable, '-c', 'raise SystemExit(37)']), 37)
            self.assertEqual(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
            with self.assertRaises(FileNotFoundError):
                guard.run(self.prefix, [str(self.root / 'missing-command')])
            self.assertEqual(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGCHLD, original)

    def test_group_permission_error_is_not_ignored_for_live_leader(self):
        process = self.spawn([sys.executable, '-c', 'import time; time.sleep(60)'],
                             start_new_session=True)
        with mock.patch.object(guard.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'denied')), \
                self.assertRaises(PermissionError):
            guard._signal_group(process.pid, signal.SIGTERM)
        self.assertIsNone(process.poll())

    def test_group_permission_error_uses_nonreaping_exit_observation(self):
        pid = 12345
        exited = select.kevent(pid, filter=select.KQ_FILTER_PROC, fflags=select.KQ_NOTE_EXIT)
        missing = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                flags=select.KQ_EV_ERROR, data=errno.ESRCH)
        for outcome in ([exited], [missing], ProcessLookupError(errno.ESRCH, 'exited')):
            with self.subTest(outcome=outcome), \
                    mock.patch.object(guard.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'denied')), \
                    mock.patch.object(guard.select, 'kqueue') as kqueue:
                queue = kqueue.return_value
                if isinstance(outcome, OSError):
                    queue.control.side_effect = outcome
                else:
                    queue.control.return_value = outcome
                guard._signal_group(pid, signal.SIGKILL)
                changes, max_events, timeout = queue.control.call_args[0]
                self.assertEqual((max_events, timeout), (1, 0))
                self.assertEqual(changes[0].ident, pid)
                self.assertEqual(changes[0].filter, select.KQ_FILTER_PROC)
                self.assertEqual(changes[0].fflags, select.KQ_NOTE_EXIT)
                queue.close.assert_called_once_with()

    def test_shell_closes_fd_before_detached_command(self):
        home = self.root / 'home'
        home.mkdir()
        env = {
            'HOME': str(home),
            'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
            'LC_ALL': 'C',
            'TMPDIR': str(self.root),
            'PYTHONDONTWRITEBYTECODE': '1',
            'MACTMNF_ROOT': str(home / 'game'),
            'MACTMNF_PREFIX': str(self.prefix),
            'MACTMNF_CACHE': str(home / 'cache'),
            'MACTMNF_SUPPORT': str(home / 'support'),
            'MACTMNF_RUNTIME': str(home / 'runtime'),
            'MACTMNF_LAUNCHER': str(home / 'launcher'),
            'MACTMNF_USERDATA': str(home / 'userdata'),
            'MACTMNF_APPS_DIR': str(home / 'Applications'),
            'MACTMNF_APP': str(home / 'Applications/TrackMania Nations Forever.app'),
            'MACTMNF_SETTINGS_APP': str(home / 'Applications/TrackMania Settings.app'),
            'MACTMNF_D9VK_DLL': str(home / 'runtime/lib/d9vk/d3d9.dll'),
            'MACTMNF_SIDECAR': str(home / 'runtime/bin/x87sidecar'),
        }
        ready = self.root / 'detached-ready'
        release = self.root / 'detached-release'
        code = ('import os, pathlib, sys, time\n'
                'assert "MACTMNF_OPERATION_FD" not in os.environ\n'
                'try: os.fstat(int(sys.argv[3]))\n'
                'except OSError: pass\n'
                'else: raise AssertionError("operation FD is still open")\n'
                'if os.fork(): sys.exit(0)\n'
                'os.setsid()\n'
                'pathlib.Path(sys.argv[1]).touch()\n'
                'while not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.01)')
        self.addCleanup(release.touch)
        result = self.cli(
            'run', self.prefix, '/bin/bash', '-eu', '-c',
            '. "$7"; "$1" "$2" held "$MACTMNF_PREFIX"; '
            'without_operation_lock "$1" -c "$4" "$5" "$6" "$MACTMNF_OPERATION_FD" '
            '</dev/null >/dev/null 2>&1; '
            'while [ ! -f "$5" ]; do sleep 0.01; done',
            'fixture', sys.executable, SCRIPT, self.prefix, code, ready, release,
            ROOT / 'lib/common.sh', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(ready.exists())
        self.assertFalse(release.exists())
        result = self.cli('run', self.prefix, sys.executable, '-c', 'pass', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        release.touch()


if __name__ == '__main__':
    unittest.main()
