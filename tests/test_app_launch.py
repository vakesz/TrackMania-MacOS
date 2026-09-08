import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from lib import app_launch

SCRIPT = Path(__file__).resolve().parents[1] / 'lib/app_launch.py'


class AppLaunch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-app-launch-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.log = self.home / 'Library/Logs/MacTMNF/play.log'
        self.launcher = self.root / "launcher with 'quotes;$(false)"
        self.marker = self.root / 'launched'
        self.env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1',
                        SECRET_TOKEN='must-not-be-logged')
        self.stub()

    def stub(self, code='os.write(1, b"stdout\\x00\\xff\\n"); os.write(2, b"stderr\\n")'):
        self.launcher.write_text(
            '#!' + sys.executable + '\nimport os, pathlib, signal, sys, time\n'
            + 'pathlib.Path(' + repr(str(self.marker)) + ').write_text(repr(sys.argv[1:]))\n'
            + code + '\n')
        self.launcher.chmod(0o700)

    def command(self, action='play', log=None):
        return [sys.executable, str(SCRIPT), str(self.launcher), action,
                str(self.log if log is None else log)]

    def cli(self, action='play', log=None):
        return subprocess.run(self.command(action, log), env=self.env,
                              capture_output=True, timeout=8, check=False)

    def spawn(self):
        process = subprocess.Popen(self.command(), env=self.env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    def wait_file(self, path, process=None):
        deadline = time.monotonic() + 5
        while not path.exists():
            if process is not None and process.poll() is not None:
                self.fail('Launcher exited before fixture was ready')
            if time.monotonic() > deadline:
                self.fail('Timed out waiting for ' + str(path))
            time.sleep(0.01)

    def write_log(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)

    def backup(self, number):
        return Path(str(self.log) + '.' + str(number))

    def test_bytes_metadata_actions_and_private_creation(self):
        for action in ('play', 'configure'):
            with self.subTest(action=action):
                result = self.cli(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((result.stdout, result.stderr), (b'', b''))
                self.assertEqual(self.marker.read_text(), repr([action]))
        data = self.log.read_bytes()
        self.assertEqual(data.count(b'stdout\x00\xff\nstderr\n'), 2)
        self.assertRegex(data, rb'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ start action=play\n')
        self.assertRegex(data, rb'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ exit status=0\n$')
        self.assertNotIn(str(self.launcher).encode(), data)
        self.assertNotIn(self.env['SECRET_TOKEN'].encode(), data)
        for path in (self.log, Path(str(self.log) + '.lock')):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_statuses_are_preserved_and_signals_normalized(self):
        for status in (0, 1, 2, 124):
            with self.subTest(status=status):
                self.stub('sys.exit(' + str(status) + ')')
                self.assertEqual(self.cli().returncode, status)
                self.assertTrue(self.log.read_bytes().endswith(('exit status=' + str(status) + '\n').encode()))
        self.stub('os.kill(os.getpid(), signal.SIGTERM)')
        self.assertEqual(self.cli().returncode, 143)
        self.assertTrue(self.log.read_bytes().endswith(b'exit status=143\n'))

    def test_rotation_threshold_order_and_three_backups(self):
        self.write_log(self.log, b'x' * (app_launch.ROTATE_BYTES - 1))
        self.assertEqual(self.cli().returncode, 0)
        self.assertFalse(self.backup(1).exists())
        for number in range(1, 4):
            self.write_log(self.backup(number), str(number).encode())
        completed = self.log.read_bytes()
        self.assertGreater(len(completed), app_launch.ROTATE_BYTES)
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(self.backup(1).read_bytes(), completed)
        self.assertEqual(self.backup(2).read_bytes(), b'1')
        self.assertEqual(self.backup(3).read_bytes(), b'2')
        self.assertFalse(self.backup(4).exists())
        self.write_log(self.log, b'e' * app_launch.ROTATE_BYTES)
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(self.backup(1).read_bytes(), b'e' * app_launch.ROTATE_BYTES)

    def test_unrelated_files_prefix_and_symlinks_are_untouched(self):
        prefix = self.root / 'prefix'
        prefix.mkdir()
        sentinel = prefix / 'system.reg'
        sentinel.write_bytes(b'prefix stays intact')
        self.write_log(self.log, b'x' * app_launch.ROTATE_BYTES)
        unrelated = self.log.parent / 'play.log.other'
        unrelated.symlink_to(sentinel)
        self.write_log(self.backup(4), b'not ours')
        self.assertEqual(self.cli().returncode, 0)
        self.assertTrue(unrelated.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'prefix stays intact')
        self.assertEqual(self.backup(4).read_bytes(), b'not ours')
        self.assertEqual(list(prefix.iterdir()), [sentinel])

    def test_unsafe_log_lock_and_backups_fail_before_launch_or_rotation(self):
        target = self.root / 'target'
        target.write_bytes(b'leave alone')
        paths = [self.log, Path(str(self.log) + '.lock')] + [self.backup(i) for i in range(1, 4)]
        for path in paths:
            for kind in ('symlink', 'dangling', 'fifo', 'directory', 'writable', 'hardlink'):
                with self.subTest(path=path.name, kind=kind):
                    self.write_log(self.log, b'x' * app_launch.ROTATE_BYTES)
                    path.unlink(missing_ok=True)
                    if kind in ('symlink', 'dangling'):
                        path.symlink_to(target if kind == 'symlink' else self.root / 'missing')
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    elif kind == 'directory':
                        path.mkdir()
                    elif kind == 'hardlink':
                        os.link(target, path)
                    else:
                        path.write_bytes(b'unsafe')
                        path.chmod(0o666)
                    result = self.cli()
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertFalse(self.marker.exists())
                    if path != self.log:
                        self.assertEqual(self.log.stat().st_size, app_launch.ROTATE_BYTES)
                    if kind == 'directory':
                        path.rmdir()
                    else:
                        path.unlink()
        self.assertEqual(target.read_bytes(), b'leave alone')
        self.assertFalse((self.root / 'missing').exists())

    def test_wrong_file_owner_is_rejected(self):
        self.write_log(self.log, b'preserve')
        with mock.patch.object(app_launch.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                with app_launch._open_file(self.log):
                    self.fail('Opened another user\'s log')
        self.assertEqual(self.log.read_bytes(), b'preserve')

    def test_unsafe_paths_and_parents_create_nothing(self):
        outside = '/Users/mactmnf-app-launch-forbidden/logs/play.log'
        for path in ('relative/play.log', str(self.root) + '/../escape/play.log', outside):
            with self.subTest(path=path):
                self.assertEqual(self.cli(log=path).returncode, 1)
        self.assertFalse(Path(outside).parent.exists())
        alias = self.home / 'alias'
        alias.symlink_to('/Users')
        self.assertEqual(self.cli(log=alias / 'mactmnf-app-launch-forbidden/play.log').returncode, 1)
        public = self.home / 'public'
        public.mkdir()
        public.chmod(0o777)
        self.assertEqual(self.cli(log=public / 'new/play.log').returncode, 1)
        self.assertFalse((public / 'new').exists())
        self.assertFalse(self.log.parent.exists())
        self.assertFalse(self.marker.exists())

    def test_invalid_actions_missing_and_extra_arguments_do_not_launch(self):
        for args in ([], [str(self.launcher)], self.command()[2:] + ['extra'],
                     [str(self.launcher), 'stop', str(self.log)],
                     [str(self.launcher), 'play; touch bad', str(self.log)]):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env,
                                        capture_output=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 2)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.log.parent.exists())

    def test_spawn_failure_records_final_status_without_path_or_credentials(self):
        self.launcher.unlink()
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.log.read_bytes().endswith(b'exit status=1\n'))
        self.assertNotIn(str(self.launcher).encode(), result.stderr + self.log.read_bytes())
        self.assertNotIn(b'Traceback', result.stderr)
        self.stub()
        self.assertEqual(self.cli().returncode, 0)

    def test_concurrent_attempt_is_refused_and_lock_inode_is_stable(self):
        ready, release = self.root / 'ready', self.root / 'release'
        self.stub('os.write(1, b"x" * ' + str(app_launch.ROTATE_BYTES) + ')\n'
                  + 'pathlib.Path(' + repr(str(ready)) + ').touch()\n'
                  + 'while not pathlib.Path(' + repr(str(release)) + ').exists(): time.sleep(0.01)')
        first = self.spawn()
        self.addCleanup(release.touch)
        self.wait_file(ready, first)
        lock = Path(str(self.log) + '.lock')
        inode, before = lock.stat().st_ino, self.log.read_bytes()
        self.marker.unlink()
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'Another launch', result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.log.read_bytes(), before)
        self.assertFalse(self.backup(1).exists())
        release.touch()
        self.assertEqual(first.wait(timeout=5), 0)
        self.stub()
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_bytes(), b'')
        self.assertTrue(self.backup(1).exists())

    def test_wrapper_signals_forward_to_launcher_only_and_wait_for_cleanup(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(self.stop, unrelated)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signum=signum):
                ready = self.root / ('ready-' + str(signum))
                received = self.root / ('received-' + str(signum))
                self.stub('def handle(sig, frame):\n'
                          + '    time.sleep(0.15)\n'
                          + '    pathlib.Path(' + repr(str(received)) + ').write_text(str(sig))\n'
                          + '    sys.exit(0)\n'
                          + 'for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, handle)\n'
                          + 'pathlib.Path(' + repr(str(ready)) + ').touch()\n'
                          + 'time.sleep(60)')
                process = self.spawn()
                self.wait_file(ready, process)
                process.send_signal(signum)
                self.assertEqual(process.wait(timeout=5), 128 + signum)
                self.assertEqual(received.read_text(), str(int(signum)))
                self.assertIsNone(unrelated.poll())
                self.assertTrue(self.log.read_bytes().endswith(
                    ('exit status=' + str(128 + signum) + '\n').encode()))

    def test_surviving_writer_keeps_regular_fd_without_holding_lock(self):
        ready, release, done = (self.root / name for name in ('ready', 'release', 'done'))
        self.stub('assert __import__("stat").S_ISREG(os.fstat(1).st_mode)\n'
                  + 'assert __import__("stat").S_ISREG(os.fstat(2).st_mode)\n'
                  + 'os.write(1, b"x" * ' + str(app_launch.ROTATE_BYTES) + ')\n'
                  + 'if os.fork(): sys.exit(124)\n'
                  + 'signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n'
                  + 'pathlib.Path(' + repr(str(ready)) + ').touch()\n'
                  + 'while not pathlib.Path(' + repr(str(release)) + ').exists(): time.sleep(0.01)\n'
                  + 'os.write(1, b"survived\\n")\n'
                  + 'pathlib.Path(' + repr(str(done)) + ').touch()')
        self.addCleanup(release.touch)
        self.assertEqual(self.cli().returncode, 124)
        self.wait_file(ready)
        self.stub()
        self.assertEqual(self.cli().returncode, 0)
        self.assertGreater(self.backup(1).stat().st_size, app_launch.ROTATE_BYTES)
        release.touch()
        self.wait_file(done)
        self.assertIn(b'survived\n', self.backup(1).read_bytes())
        self.assertNotIn(b'survived\n', self.log.read_bytes())


if __name__ == '__main__':
    unittest.main()
