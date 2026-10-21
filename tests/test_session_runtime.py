"""Opt-in Wine session integration, using only freshly created scratch prefixes.

MACTMNF_TEST_RUNTIME must name an existing runtime containing bin/wine and
bin/wineserver. TMPDIR must name an approved existing temporary directory.
The virtual-desktop test may briefly show a new, test-only Wine desktop.
Run with Python 3.9+: PYTHONPATH=src/python python3 -m unittest discover -s tests \
    -p test_session_runtime.py -v
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MACTMNF_TEST_RUNTIME')


@unittest.skipUnless(RUNTIME, 'opt-in only: explicitly set MACTMNF_TEST_RUNTIME')
class SessionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Path(RUNTIME).resolve(strict=True)
        cls.wine = cls.runtime / 'bin/wine'
        cls.server = cls.runtime / 'bin/wineserver'
        for binary in (cls.wine, cls.server):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise RuntimeError('runtime executable missing: {}'.format(binary))
        compilers = {}
        for arch in ('x86_64', 'i686'):
            compilers[arch] = shutil.which(arch + '-w64-mingw32-gcc')
            if not compilers[arch]:
                raise RuntimeError('opt-in test requires {}-w64-mingw32-gcc'.format(arch))
        if not os.environ.get('TMPDIR'):
            raise RuntimeError('set TMPDIR to an approved existing temporary directory')
        parent = Path(os.environ['TMPDIR']).resolve(strict=True)
        if not parent.is_dir():
            raise RuntimeError('TMPDIR is not a directory: {}'.format(parent))
        cls.temp = tempfile.TemporaryDirectory(prefix='mactmnf-session-', dir=str(parent))
        cls.scratch = Path(cls.temp.name).resolve()
        cls.prefixes = []
        cls.background = []
        cls.serial = 0
        cls.addClassCleanup(cls.cleanup_scratch)
        cls.logs = cls.scratch / 'logs'
        cls.logs.mkdir()
        cls.home = cls.scratch / 'home'
        for directory in (cls.home, cls.home / '.config', cls.home / '.cache', cls.scratch / 'tmp'):
            directory.mkdir(mode=0o700)
        # Do not inherit Wine, Rosetta, DYLD, XDG, or real user profile settings.
        cls.env = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin'),
            'PYTHONPATH': str(ROOT / 'src/python'),
            'HOME': str(cls.home),
            'TMPDIR': str(cls.scratch / 'tmp'),
            'WINEDEBUG': '-all',
            'WINEDLLOVERRIDES': 'mscoree,mshtml=',
            'TMNF_X87': 'off',
            'XDG_CONFIG_HOME': str(cls.home / '.config'),
            'XDG_CACHE_HOME': str(cls.home / '.cache'),
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        cls.bin = cls.scratch / 'fixture binaries with spaces'
        cls.bin.mkdir()
        cls.helper = cls.bin / 'session64.exe'
        cls.child64 = cls.bin / 'child64.exe'
        cls.child32 = cls.bin / 'child32.exe'
        # Match the helper's installed ../lib/wine reference layout, using a
        # private copy so rejection tests cannot alter the selected runtime.
        cls.conhost_reference = cls.scratch / 'lib/wine/x86_64-windows/conhost.exe'
        cls.explorer_reference = cls.scratch / 'lib/wine/x86_64-windows/explorer.exe'
        cls.conhost_reference.parent.mkdir(parents=True)
        for reference in (cls.conhost_reference, cls.explorer_reference):
            shutil.copy2(cls.runtime / 'lib/wine/x86_64-windows' / reference.name, reference)
        cls.gui64 = cls.bin / 'gui64.exe'
        cls.gui32 = cls.bin / 'gui32.exe'
        compile_inputs = [
            ('x86_64', ROOT / 'tests/session-child.c', cls.child64),
            ('i686', ROOT / 'tests/session-child.c', cls.child32),
            ('x86_64', ROOT / 'tests/session-gui.c', cls.gui64),
            ('i686', ROOT / 'tests/session-gui.c', cls.gui32)]
        packaged_helper = cls.runtime / 'libexec/tmnf-session.exe'
        if packaged_helper.is_file():
            shutil.copy2(packaged_helper, cls.helper)
            print('Testing packaged session helper: {}'.format(packaged_helper), flush=True)
        else:
            print('Reference runtime has no helper; testing a freshly compiled source helper', flush=True)
            compile_inputs.insert(0, ('x86_64', ROOT / 'src/c/session.c', cls.helper))
        for arch, source, output in compile_inputs:
            subsystem = ['-mwindows'] if source.name == 'session-gui.c' else []
            cls.command([compilers[arch], '-O2', '-Wall', '-Wextra', '-Werror',
                         '-municode', *subsystem, str(source), '-o', str(output)],
                        cls.env, 'compile-' + output.stem, 60)
        cls.launcher = cls.bin / 'settings launcher.exe'
        cls.game = cls.bin / 'game.exe'
        shutil.copy2(cls.child64, cls.launcher)
        shutil.copy2(cls.child32, cls.game)
        cls.alias = cls.scratch / 'binary alias with spaces'
        cls.alias.symlink_to(cls.bin, target_is_directory=True)
        cls.prefix = cls.new_prefix('primary prefix')
        cls.other_prefix = cls.new_prefix('secondary prefix')
        version = cls.runtime / '.tmnf-runtime'
        print('\nSession integration: existing runtime {}; source {}; not release qualification'.format(
            version.read_text().strip() if version.is_file() else 'unversioned',
            (ROOT / 'VERSION').read_text().strip()), file=sys.stderr, flush=True)

    @classmethod
    def command(cls, args, env, label, timeout, check=True):
        cls.serial += 1
        log = cls.logs / '{:03d}-{}.log'.format(cls.serial, label)
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            stream.write('command: {!r}\nprefix: {}\n'.format(args, env.get('WINEPREFIX')))
            stream.flush()
            try:
                process = subprocess.run([str(arg) for arg in args], env=env,
                                         cwd=str(cls.scratch), stdin=subprocess.DEVNULL,
                                         stdout=stream, stderr=subprocess.STDOUT,
                                         timeout=timeout)
            except subprocess.TimeoutExpired:
                stream.write('\nTIMEOUT after {} seconds\n'.format(timeout))
                stream.flush()
                raise AssertionError(log.read_text(encoding='utf-8', errors='replace')) from None
            stream.write('\nreturncode: {}; elapsed: {:.3f}s\n'.format(
                process.returncode, time.monotonic() - started))
        if check and process.returncode:
            raise AssertionError(log.read_text(encoding='utf-8', errors='replace'))
        return process.returncode, log

    @classmethod
    def prefix_env(cls, prefix):
        # A membership check prevents cleanup from ever targeting a caller's prefix.
        if prefix not in cls.prefixes or prefix.parent != cls.home or prefix.is_symlink():
            raise RuntimeError('refusing unknown or redirected prefix: {}'.format(prefix))
        return dict(cls.env, WINEPREFIX=str(prefix))

    @classmethod
    def stop_prefix(cls, prefix):
        failures = []
        for option in ('-k', '-w'):
            try:
                status, log = cls.command([cls.server, option], cls.prefix_env(prefix),
                                          'stop-' + prefix.name + option, 15, check=False)
                # -k returns 1 when there is no server. The subsequent -w must
                # still succeed; a timeout or any other status preserves scratch.
                if status not in ((0, 1) if option == '-k' else (0,)):
                    raise AssertionError(log.read_text(encoding='utf-8', errors='replace'))
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise AssertionError('scratch wineserver did not stop safely:\n' + '\n'.join(failures))

    @classmethod
    def new_prefix(cls, name):
        prefix = cls.home / name
        prefix.mkdir(mode=0o700)
        cls.prefixes.append(prefix)
        try:
            cls.command([cls.wine, 'wineboot', '--init'], cls.prefix_env(prefix),
                        'wineboot-' + name, 60)
        finally:
            cls.stop_prefix(prefix)
        (prefix / '.tmnf-prefix').write_text('tmnf:1\n', encoding='ascii')
        # An explicit scratch-only drive avoids assumptions about Wine's Z: mapping.
        (prefix / 'dosdevices/s:').symlink_to(cls.scratch, target_is_directory=True)
        return prefix

    @classmethod
    def cleanup_scratch(cls):
        failures = []
        for prefix in cls.prefixes:
            try:
                cls.stop_prefix(prefix)
            except Exception as error:
                failures.append(str(error))
        for process in cls.background:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                failures.append('scratch Wine client still running: pid={}'.format(process.pid))
        if failures:
            # Python 3.9 has no TemporaryDirectory(delete=False). Preserve it even
            # at interpreter exit if server shutdown could not be established.
            cls.temp._finalizer.detach()
            raise AssertionError('Preserved scratch artifacts at {}\n{}'.format(
                cls.scratch, '\n'.join(failures)))
        cls.temp.cleanup()

    @classmethod
    def diagnostics(cls, directory=None):
        parts = ['scratch: {}'.format(cls.scratch)]
        for folder in (cls.logs, directory):
            if folder is None:
                continue
            for path in sorted(folder.iterdir()):
                if folder == cls.logs and directory is not None and directory.name not in path.name:
                    continue
                if path.is_file():
                    parts.append('{}:\n{}'.format(path.name, path.read_text(
                        encoding='utf-8', errors='replace')))
        return '\n'.join(parts)

    def setUp(self):
        self.artifacts = self.scratch / self._testMethodName
        self.artifacts.mkdir()
        self.addCleanup(self.finish_test)
        for prefix in self.prefixes:
            self.stop_prefix(prefix)
        self.deadline = time.monotonic() + 110

    def finish_test(self):
        # Print before deletion, including for assertion errors and timeouts.
        outcome = self._outcome
        result = getattr(outcome, 'result', None)
        failed = result is not None and any(
            test is self or getattr(test, 'test_case', None) is self
            for test, _ in result.failures + result.errors)
        # Python 3.9 stores errors on _Outcome until TestCase.run finishes.
        failed = failed or any(error for _, error in getattr(outcome, 'errors', ()))
        failures = []
        try:
            if failed:
                print('\n' + self.diagnostics(self.artifacts), file=sys.stderr, flush=True)
        finally:
            for prefix in self.prefixes:
                try:
                    self.stop_prefix(prefix)
                except Exception as error:
                    failures.append(str(error))
        if failures:
            raise AssertionError('\n'.join(failures))

    @classmethod
    def windows(cls, path):
        return 'S:\\' + str(path.relative_to(cls.scratch)).replace('/', '\\')

    def run_session(self, executable, args=(), watches=(), startup_ms=10000,
                    desktop=False, prefix=None, label='session'):
        result = self.artifacts / (label + '.json')
        command = [self.wine, self.windows(self.helper), '--result', self.windows(result),
                   '--startup-ms', str(startup_ms)]
        for watch in watches:
            command += ['--watch', str(watch)]
        if desktop:
            command += ['--desktop', '640x480']
        command += ['--', str(executable)] + list(args)
        remaining = self.deadline - time.monotonic()
        self.assertGreater(remaining, 0, 'per-test budget exhausted')
        started = time.monotonic()
        status, log = self.command(command, self.prefix_env(prefix or self.prefix),
                                   self._testMethodName + '-' + label,
                                   min(45, remaining), check=False)
        elapsed = time.monotonic() - started
        self.assertTrue(result.is_file(), self.diagnostics(self.artifacts))
        try:
            data = json.loads(result.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            self.fail(self.diagnostics(self.artifacts))
        return data, status, log.read_text(encoding='utf-8', errors='replace'), elapsed

    def assert_exited(self, observation, code=0):
        data, status, log, _ = observation
        self.assertEqual(data, {'state': 'exited', 'exit_code': code, 'cleanup': True}, log)
        self.assertEqual(status, 0, log)

    def run_host(self, executable, args=(), watches=(), startup_timeout=30):
        command = [sys.executable, '-m', 'tmnf.guard.operation_guard', 'run', self.prefix,
                   sys.executable, '-m', 'tmnf.session.session', 'run', '--prefix', self.prefix,
                   '--wine', self.wine, '--helper', self.helper,
                   '--startup-timeout', str(startup_timeout), '--cleanup-timeout', '15']
        for watch in watches or (executable,):
            command += ['--watch', str(watch)]
        command += ['--', str(executable)] + list(args)
        remaining = self.deadline - time.monotonic()
        self.assertGreater(remaining, 0, 'per-test budget exhausted')
        status, log = self.command(command, self.prefix_env(self.prefix),
                                   self._testMethodName + '-host', min(45, remaining), check=False)
        return status, log

    def assert_prefix_inactive(self, prefix):
        # Neither probe issues -k: teardown must not mask missing host cleanup.
        self.command([self.server, '-w'], self.prefix_env(prefix),
                     self._testMethodName + '-inactive-wait', 5)
        self.command([sys.executable, '-m', 'tmnf.guard.operation_guard', 'run', prefix,
                      '/usr/bin/true'], self.prefix_env(prefix),
                     self._testMethodName + '-inactive-guard', 5)
        self.assertEqual((prefix / '.tmnf-prefix').read_text(), 'tmnf:1\n')
        self.assertFalse(list(self.home.glob('.' + prefix.name + '.session-*')),
                         'host retained a result directory after ending')

    def test_host_zero_cleans_prefix(self):
        status, log = self.run_host(self.child64, ['--exit', '0'])
        text = log.read_text(encoding='utf-8')
        self.assertEqual(status, 0, text)
        self.assertIn('session: exited target_status=0 cleanup=true', text)
        self.assertIn('session: target status 0x00000000 (0)', text)
        self.assert_prefix_inactive(self.prefix)

    def test_fresh_installer_profile_privacy(self):
        from tmnf.install.runtime_manifest import digest

        baseline = {str(path.relative_to(self.runtime)): digest(path)
                    for path in self.runtime.rglob('*') if path.is_file()}
        prefix = self.home / 'fresh installer prefix'
        prefix.mkdir(mode=0o700)
        self.prefixes.append(prefix)  # Register before initialize_prefix invokes Wine.
        staged = self.home / 'installer runtime'
        (staged / 'config').mkdir(parents=True)
        shutil.copy2(self.runtime / 'config/Default.SystemConfig.Gbx', staged / 'config/Default.SystemConfig.Gbx')
        userdata = self.home / 'installer userdata'
        documents = self.home / 'Documents'
        documents.mkdir(exist_ok=True)
        personal = documents / 'personal-fixture.txt'
        personal.write_text('private host fixture')
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Movies', 'Videos'):
            folder = self.home / name
            folder.mkdir(exist_ok=True)
            (folder / 'personal-fixture.txt').write_text('private ' + name)
        env = dict(self.prefix_env(prefix), TMNF_WINE=str(self.wine),
                   TMNF_RUNTIME=str(staged), TMNF_PREFIX=str(prefix),
                   TMNF_ROOT=str(self.home / 'installer root'),
                   TMNF_USERDATA=str(userdata), TMNF_SUPPORT=str(self.home / 'installer support'),
                   TMNF_APPS_DIR=str(self.home / 'installer apps'),
                   TMNF_CACHE=str(self.home / 'installer cache'),
                   TEST_PROFILE_LINKS=str(self.artifacts / 'default-links.json'))
        script = '''. "$1"
python3() {
  if test "${3:-}" = setup-documents; then
    command python3 -c 'import json, os; from pathlib import Path; p = Path(os.environ["TMNF_PREFIX"]); Path(os.environ["TEST_PROFILE_LINKS"]).write_text(json.dumps({str(f.relative_to(p)): str(f.readlink()) if f.is_symlink() else None for u in (p / "drive_c/users").iterdir() if u.is_dir() for f in u.iterdir()}))'
  fi
  command python3 "$@"
}
initialize_prefix'''
        status, log = self.command(['/bin/bash', '-euc', script,
                                    'fixture', ROOT / 'src/shell/cli/install.sh'], env, self._testMethodName, 60, check=False)
        # Read only link strings in the scratch prefix, never host folder contents.
        links = {str(path.relative_to(prefix)): str(path.readlink())
                 for profile in (prefix / 'drive_c/users').iterdir() if profile.is_dir()
                 for path in profile.iterdir() if path.is_symlink()}
        print('\nFresh installer profile links: ' + json.dumps(links, sort_keys=True), file=sys.stderr)
        defaults = json.loads((self.artifacts / 'default-links.json').read_text())
        print('Wine-created profile entries: ' + json.dumps(defaults, sort_keys=True), file=sys.stderr)
        self.assertEqual(status, 0, log.read_text())
        self.assertEqual(personal.read_text(), 'private host fixture')
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Movies', 'Videos'):
            self.assertEqual((self.home / name / 'personal-fixture.txt').read_text(), 'private ' + name)
        self.assertTrue(any(target is not None and Path(path).name == 'Desktop'
                            for path, target in defaults.items()), 'fixture did not exercise a Wine Desktop link')
        profiles = [profile for profile in (prefix / 'drive_c/users').iterdir()
                    if (profile / 'Documents').is_symlink()]
        self.assertTrue(profiles, 'Wine created no redirected Documents profile')
        for profile in profiles:
            self.assertEqual((profile / 'Documents').readlink(), userdata)
            for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
                if str((profile / name).relative_to(prefix)) not in defaults:
                    continue  # Public does not have every user-profile folder.
                self.assertTrue((profile / name).is_dir(), str(profile / name))
                self.assertFalse((profile / name).is_symlink(), str(profile / name))
        self.assertEqual((userdata / 'TmForever/Config/Default.SystemConfig.Gbx').read_bytes(),
                         (staged / 'config/Default.SystemConfig.Gbx').read_bytes())
        for marker in ('.tmnf-initializing', '.tmnf-profile-baseline'):
            self.assertFalse((prefix / marker).exists())
        self.assertEqual(baseline, {str(path.relative_to(self.runtime)): digest(path)
                                    for path in self.runtime.rglob('*') if path.is_file()})

    def test_host_nonzero_cleans_prefix(self):
        status, log = self.run_host(self.windows(self.child32), ['--exit', '37'])
        text = log.read_text(encoding='utf-8')
        self.assertEqual(status, 1, text)
        self.assertIn('session: exited target_status=37 cleanup=true', text)
        self.assertIn('session: target status 0x00000025 (37)', text)
        self.assert_prefix_inactive(self.prefix)

    def test_host_session_with_bundled_translator_enabled(self):
        sidecar = self.runtime / 'bin/x87sidecar'
        self.assertTrue(sidecar.is_file(), 'selected runtime has no bundled translator')
        self.command([sidecar, '--probe'], self.env, self._testMethodName + '-compatibility', 15)
        with mock.patch.dict(self.env, ROSETTA_X87_PATH=str(sidecar), TMNF_X87='on',
                             X87_ENABLE_FMA_CONTRACT='0', X87_FAST_ROUND='0'):
            status, log = self.run_host(self.child32, ['--sleep', '500', '--exit', '0'])
        self.assertEqual(status, 0, log.read_text(encoding='utf-8'))
        self.assertIn('session: exited target_status=0 cleanup=true', log.read_text(encoding='utf-8'))
        self.assert_prefix_inactive(self.prefix)

    def test_host_known_exception_status_cleans_prefix(self):
        # --crash raises the fixture's custom exception, not a host-known status.
        status, log = self.run_host(self.child32, ['--exit', '0xc0000005'])
        text = log.read_text(encoding='utf-8')
        self.assertEqual(status, 1, text)
        self.assertIn('session: exited target_status=3221225477 cleanup=true', text)
        self.assertIn('session: exception status 0xc0000005 (3221225477)', text)
        self.assert_prefix_inactive(self.prefix)

    def test_host_startup_timeout_leaves_launched_fixture_alive(self):
        started = self.artifacts / 'root started.json'
        survived = self.artifacts / 'root survived host timeout.json'
        # Leave cold Wine bootstrap room even under concurrent compiler load.
        # The un-watched root launches a witness only after its 30-second sleep.
        # A live wineserver alone would not prove that this root survived.
        status, log = self.run_host(
            self.launcher,
            ['--log-args', str(started), '--sleep', '30000', '--handoff', str(self.child64),
             '--', '--log-args', str(survived), '--exit', '0'],
            watches=[self.game], startup_timeout=15)
        text = log.read_text(encoding='utf-8')
        self.assertEqual(status, 124, text)
        self.assertIn('session: Startup deadline expired; prefix left running; '
                      'use explicit stop if needed', text)
        self.assertIn('session: running root=', text)
        self.assertRegex(text, r'fixture: pid=\d+ started')
        self.assertTrue(started.is_file(), text)
        self.assertFalse(survived.exists(), text)
        self.assertNotIn('cleanup=true', text)
        active, probe = self.command(
            [sys.executable, '-m', 'tmnf.guard.operation_guard', 'run', self.prefix, '/usr/bin/true'],
            self.prefix_env(self.prefix), self._testMethodName + '-active-guard', 5, check=False)
        self.assertEqual(active, 1, probe.read_text())
        self.assertIn('Wine is active for prefix: {}'.format(self.prefix), probe.read_text())
        deadline = min(self.deadline, time.monotonic() + 40)
        while not survived.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(survived.is_file(), self.diagnostics(self.artifacts))
        self.assertEqual(json.loads(survived.read_text(encoding='utf-8'))[1:],
                         ['--log-args', str(survived), '--exit', '0'])

    def test_host_cleanup_does_not_stop_other_prefix(self):
        process, background_log = self.start_long_fixture(self.other_prefix)
        status, log = self.run_host(self.game, ['--sleep', '500', '--exit', '0'])
        self.assertIsNone(process.poll(), self.diagnostics(self.artifacts))
        self.assertEqual(status, 0, log.read_text())
        self.assertIn('session: target status 0x00000000 (0)', log.read_text())
        self.assert_prefix_inactive(self.prefix)
        self.assertIsNone(process.poll(), self.diagnostics(self.artifacts))
        self.assertNotIn('exited=', background_log.read_text(encoding='utf-8'))

    def test_cli_play_vanilla_uses_host_and_cleans_prefix(self):
        staged = self.home / 'CLI runtime'
        for directory in (staged / 'bin', staged / 'libexec', staged / 'lib/d9vk',
                          staged / 'lib/wine/x86_64-windows'):
            directory.mkdir(parents=True)
        (staged / 'bin/wine').symlink_to(self.wine)
        (staged / 'bin/wineserver').symlink_to(self.server)
        shutil.copy2(self.helper, staged / 'libexec/tmnf-session.exe')
        shutil.copy2(self.conhost_reference, staged / 'lib/wine/x86_64-windows/conhost.exe')
        d9vk = self.runtime / 'lib/d9vk/d3d9.dll'
        self.assertTrue(d9vk.is_file(), 'CLI integration requires the selected runtime D9VK')
        (staged / 'lib/d9vk/d3d9.dll').symlink_to(d9vk)
        prefix_dll = self.prefix / 'drive_c/windows/syswow64/d3d9.dll'
        # Never copy through a Wine-created link into the selected runtime.
        prefix_dll.unlink(missing_ok=True)
        shutil.copy2(d9vk, prefix_dll)
        game_dir = self.prefix / 'drive_c/Program Files (x86)/TmNationsForever'
        game_dir.mkdir()
        shutil.copy2(self.child32, game_dir / 'TmForever.exe')
        argument_log = self.artifacts / 'CLI arguments.json'
        args = ['--log-args', str(argument_log), '--exit', '0', '--', '', 'CLI spaces', 'CLI"quote']
        env = dict(self.prefix_env(self.prefix),
                   TMNF_RUNTIME=str(staged), TMNF_PREFIX=str(self.prefix),
                   TMNF_ROOT=str(self.home / 'CLI root'),
                   TMNF_USERDATA=str(self.home / 'CLI userdata'),
                   TMNF_SUPPORT=str(self.home / 'CLI support'),
                   TMNF_LAUNCHER=str(self.home / 'CLI support/launcher'),
                   TMNF_CACHE=str(self.home / '.cache'),
                   TMNF_APPS_DIR=str(self.home / 'Applications'),
                   TMNF_AUDIO='off', TMNF_RENDERER='d9vk', TMNF_WINDOW_SIZE='fullscreen')
        status, log = self.command(
            ['/bin/bash', ROOT / 'src/shell/cli/tmnf', 'play-vanilla'] + args,
            env, self._testMethodName + '-cli', 45, check=False)
        text = log.read_text(encoding='utf-8')
        self.assertEqual(status, 0, text)
        self.assertIn('runtime={}'.format(staged / 'bin/wine'), text)
        self.assertIn('session: exited target_status=0 cleanup=true', text)
        self.assertIn('session: target status 0x00000000 (0)', text)
        self.assertEqual(json.loads(argument_log.read_text(encoding='utf-8'))[1:], args)
        self.assert_prefix_inactive(self.prefix)

    def test_multimedia_timer_probe_both_architectures(self):
        for architecture in ('i686', 'x86_64'):
            with self.subTest(architecture=architecture):
                output = self.artifacts / ('multimedia-' + architecture + '.exe')
                self.command([architecture + '-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                              ROOT / 'tests/multimedia-timer.c', '-o', output, '-lwinmm'],
                             self.env, self._testMethodName + '-compile-' + architecture, 30)
                self.command([self.wine, self.windows(output)], self.prefix_env(self.prefix),
                             self._testMethodName + '-' + architecture, 50)

    def test_alertable_waits_enabled_and_disabled(self):
        for architecture in ('i686', 'x86_64'):
            output = self.artifacts / ('alertable-' + architecture + '.exe')
            self.command([architecture + '-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                          ROOT / 'tests/alertable-wait.c', '-o', output],
                         self.env, self._testMethodName + '-compile-' + architecture, 30)
            for mode in ('0', '1'):
                with self.subTest(architecture=architecture, fast_wait=mode):
                    env = dict(self.prefix_env(self.prefix), WINEDARWINFASTWAIT=mode)
                    self.command([self.wine, self.windows(output)], env,
                                 self._testMethodName + '-' + architecture + '-' + mode, 60)

    def test_shader_token_self_checks_both_architectures(self):
        for architecture in ('i686', 'x86_64'):
            with self.subTest(architecture=architecture):
                output = self.artifacts / ('shader-tokens-' + architecture + '.exe')
                self.command([architecture + '-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                              ROOT / 'tests/shader-profiles.c', '-o', output],
                             self.env, self._testMethodName + '-compile-' + architecture, 30)
                self.command([self.wine, self.windows(output), '--tokens-only'], self.prefix_env(self.prefix),
                             self._testMethodName + '-' + architecture, 30)

    def test_session_image_identity_both_architectures(self):
        for architecture in ('i686', 'x86_64'):
            with self.subTest(architecture=architecture):
                output = self.artifacts / ('session-identity-' + architecture + '.exe')
                self.command([architecture + '-w64-mingw32-gcc', '-municode', '-O2',
                              '-Wall', '-Wextra', '-Werror', ROOT / 'tests/session-identity.c', '-o', output],
                             self.env, self._testMethodName + '-compile-' + architecture, 30)
                self.command([self.wine, self.windows(output)], self.prefix_env(self.prefix),
                             self._testMethodName + '-' + architecture, 30)

    def test_direct_zero(self):
        self.assert_exited(self.run_session(self.windows(self.child64), ['--exit', '0']))

    def test_direct_nonzero(self):
        self.assert_exited(self.run_session(self.windows(self.child32), ['--exit', '37']), 37)

    def test_unsigned_exception(self):
        self.assert_exited(self.run_session(self.windows(self.child32), ['--crash']), 0xE0424242)

    def test_handoff_retains_long_child32(self):
        observation = self.run_session(
            self.windows(self.launcher),
            ['--handoff', self.windows(self.game), '--', '--sleep', '3500', '--exit', '23'],
            watches=[self.windows(self.game)])
        self.assert_exited(observation, 23)
        log = observation[2]
        root = re.search(r'session: running root=(\d+)', log)
        child = re.search(r'fixture: spawned pid=(\d+)', log)
        self.assertIsNotNone(root, log)
        self.assertIsNotNone(child, log)
        self.assertNotEqual(root[1], child[1], log)
        root_exit = 'session: pid={} exited=0'.format(root[1])
        child_exit = 'session: pid={} exited=23 target'.format(child[1])
        self.assertIn(root_exit, log)
        self.assertIn(child_exit, log)
        self.assertLess(log.index(root_exit), log.index(child_exit), log)
        self.assertGreaterEqual(observation[3], 3.5)

    def test_settings_watches_exact_launcher_and_game_paths(self):
        # Same basename, different directory: watching a basename would also
        # collect the decoy root's status and incorrectly return 41 below.
        other = self.artifacts / 'other directory'
        other.mkdir()
        decoy = other / self.game.name
        shutil.copy2(self.child64, decoy)
        observation = self.run_session(
            self.windows(self.launcher),
            ['--handoff', self.windows(self.game), '--', '--sleep', '2500', '--exit', '19'],
            watches=[self.windows(self.launcher), self.windows(self.game)], label='settings')
        self.assert_exited(observation, 19)
        self.assertIn('observed root target job', observation[2])
        self.assert_exited(self.run_session(
            self.windows(decoy),
            ['--spawn', self.windows(self.game), '100', '41', '0',
             '--', '--sleep', '2500', '--exit', '0'],
            watches=[self.windows(self.launcher), self.windows(self.game)], label='exact-path'))

    def test_windows_alias_and_argument_quoting(self):
        arguments = ['', 'two words', 'embedded"quote', '\u00e1rv\u00edz \u96ea \U0001f3ce',
                     'trailing\\', 'space trailing\\\\', 'slashes\\\\"quote']
        argument_log = self.artifacts / 'arguments with spaces.json'
        observation = self.run_session(
            self.windows(self.child64),
            ['--log-args', self.windows(argument_log), '--'] + arguments,
            watches=[self.windows(self.alias / self.child64.name)])
        self.assert_exited(observation)
        recorded = json.loads(argument_log.read_text(encoding='utf-8'))
        self.assertEqual(recorded[1:4], ['--log-args', self.windows(argument_log), '--'])
        self.assertEqual(recorded[4:], arguments)

    def test_startup_without_watched_target_is_bounded(self):
        data, status, log, elapsed = self.run_session(
            self.windows(self.launcher), ['--exit', '0'],
            watches=[self.windows(self.game)], startup_ms=1200)
        self.assertIn('session: running root=', log)
        self.assertIn('fixture: pid=', log)
        self.assertIn(data.get('state'), ('failed', 'uncertain'), log)
        self.assertIs(data.get('cleanup'), False, log)
        self.assertIn(data.get('reason'), ('command_not_started', 'startup_timeout',
                                         'unobserved_job_process', 'ambiguous_process_activity'), log)
        self.assertNotIn('exit_code', data, log)
        self.assertNotEqual(status, 0, log)
        self.assertLess(elapsed, 40, log)

    def test_unknown_fast_handoff_is_uncertain(self):
        data, status, log, _ = self.run_session(
            self.windows(self.launcher),
            ['--handoff', self.windows(self.game), '--', '--exit', '61'],
            watches=[self.windows(self.launcher)])
        self.assertIn('fixture: spawned pid=', log)
        self.assertEqual(data.get('state'), 'uncertain', log)
        self.assertIs(data.get('cleanup'), False, log)
        self.assertTrue(data.get('reason'), log)
        self.assertNotIn('exit_code', data, log)
        self.assertNotEqual(status, 0, log)

    def test_virtual_desktop_owns_separate_root(self):
        observation = self.run_session(
            self.windows(self.game), ['--sleep', '1000', '--exit', '0'],
            watches=[self.windows(self.game)], desktop=True)
        self.assert_exited(observation)
        log = observation[2]
        root = re.search(r'session: running root=(\d+) desktop', log)
        target = re.search(r'session: pid=(\d+) observed target', log)
        self.assertIsNotNone(root, log)
        self.assertIsNotNone(target, log)
        self.assertNotEqual(root[1], target[1], log)
        self.assertIn('session: pid={} exited='.format(root[1]), log)

    def test_virtual_desktop_conhost_reference_mismatch_is_uncertain(self):
        self.assertFalse(self.conhost_reference.is_symlink())
        original = self.conhost_reference.read_bytes()
        self.assertTrue(original, 'trusted conhost reference is empty')
        witness = self.artifacts / 'desktop target started.json'
        try:
            # Keep the length and PE header intact; only reference bytes differ.
            self.conhost_reference.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            data, status, log, _ = self.run_session(
                self.windows(self.game), ['--log-args', self.windows(witness), '--sleep', '1000', '--exit', '0'],
                watches=[self.windows(self.game)], desktop=True)
            self.assertRegex(log, r'session: running root=\d+ desktop')
            self.assertTrue(witness.is_file(), log)
            self.assertIn('--log-args', json.loads(witness.read_text(encoding='utf-8')))
            self.assertEqual(data.get('state'), 'uncertain', log)
            self.assertIs(data.get('cleanup'), False, log)
            self.assertTrue(data.get('reason'), log)
            self.assertNotIn('exit_code', data, log)
            self.assertEqual(status, 2, log)
        finally:
            self.conhost_reference.write_bytes(original)

    def test_window_owner_without_virtual_desktop_closes_its_explorer(self):
        # The default fullscreen launch has no --desktop: win32u starts
        # explorer.exe /desktop inside the job for the first window owner.
        for fixture in (self.gui64, self.gui32):
            with self.subTest(fixture=fixture.name):
                data, status, log, _ = self.run_session(
                    self.windows(fixture), ['1000'], watches=[self.windows(fixture)],
                    label=fixture.stem)
                self.assertEqual(data, {'state': 'exited', 'exit_code': 0, 'cleanup': True}, log)
                self.assertEqual(status, 0, log)
                explorer = re.search(r'session: pid=(\d+) observed job image=C:\\windows\\system32\\explorer\.exe', log)
                self.assertIsNotNone(explorer, log)
                self.assertIn('session: pid={} verified desktop infrastructure'.format(explorer[1]), log)
                self.assertIn('session: closing desktop pid={}'.format(explorer[1]), log)
                self.assertIn('session: pid={} exited=0'.format(explorer[1]), log)

    def test_launcher_handoff_to_window_owner_cleans_up(self):
        data, status, log, _ = self.run_session(
            self.windows(self.launcher), ['--spawn', self.windows(self.gui64), '100', '0', '0', '--', '1000'],
            watches=[self.windows(self.gui64)])
        self.assertEqual(data, {'state': 'exited', 'exit_code': 0, 'cleanup': True}, log)
        self.assertEqual(status, 0, log)
        self.assertRegex(log, r'session: pid=\d+ verified desktop infrastructure parent=\d+')

    def test_explorer_reference_mismatch_makes_window_owner_uncertain(self):
        self.assertFalse(self.explorer_reference.is_symlink())
        original = self.explorer_reference.read_bytes()
        self.assertTrue(original, 'trusted explorer reference is empty')
        try:
            self.explorer_reference.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            data, status, log, _ = self.run_session(
                self.windows(self.gui64), ['1000'], watches=[self.windows(self.gui64)])
            self.assertIn('session: explorer.exe reference unavailable or different', log)
            self.assertEqual(data, {'state': 'uncertain', 'reason': 'ambiguous_process_activity',
                                    'cleanup': False}, log)
            self.assertEqual(status, 2, log)
        finally:
            self.explorer_reference.write_bytes(original)

    def test_explorer_namesake_outside_system32_is_uncertain(self):
        fixtures = self.artifacts / 'fixtures'
        fixtures.mkdir()
        namesake = fixtures / 'explorer.exe'
        shutil.copy2(self.child64, namesake)
        data, status, log, _ = self.run_session(
            self.windows(self.launcher),
            ['--spawn', self.windows(namesake), '100', '0', '0', '--hold', '--sleep', '250',
             '--', '--sleep', '1500', '--exit', '0'],
            watches=[self.windows(self.launcher)])
        child = re.search(r'fixture: spawned pid=(\d+)', log)
        self.assertIsNotNone(child, log)
        self.assertIn('session: pid={} observed job'.format(child[1]), log)
        self.assertNotIn('verified desktop infrastructure', log)
        self.assertEqual(data, {'state': 'uncertain', 'reason': 'ambiguous_process_activity',
                                'cleanup': False}, log)
        self.assertEqual(status, 2, log)

    def test_conhost_namesake_outside_system32_is_uncertain(self):
        fixtures = self.artifacts / 'fixtures'
        fixtures.mkdir()
        namesake = fixtures / 'conhost.exe'
        shutil.copy2(self.child64, namesake)
        data, status, log, _ = self.run_session(
            self.windows(self.launcher),
            ['--spawn', self.windows(namesake), '100', '0', '0', '--hold', '--sleep', '250',
             '--', '--sleep', '1500', '--exit', '0'],
            watches=[self.windows(self.launcher)])
        child = re.search(r'fixture: spawned pid=(\d+)', log)
        self.assertIsNotNone(child, log)
        self.assertIn('session: pid={} observed job'.format(child[1]), log)
        self.assertIn('fixture: pid={} exited=0'.format(child[1]), log)
        self.assertEqual(data.get('state'), 'uncertain', log)
        self.assertIs(data.get('cleanup'), False, log)
        self.assertTrue(data.get('reason'), log)
        self.assertNotIn('exit_code', data, log)
        self.assertEqual(status, 2, log)

    def start_long_fixture(self, prefix):
        started = self.artifacts / 'long child started.json'
        background_log = self.artifacts / 'long child.log'
        with background_log.open('w', encoding='utf-8') as stream:
            process = subprocess.Popen(
                [str(self.wine), self.windows(self.game), '--log-args', self.windows(started),
                 '--sleep', '60000', '--exit', '0'],
                env=self.prefix_env(prefix), cwd=str(self.scratch),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        self.background.append(process)
        deadline = min(self.deadline, time.monotonic() + 40)
        while not started.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(started.is_file(), self.diagnostics(self.artifacts))
        self.assertIsNone(process.poll(), self.diagnostics(self.artifacts))
        return process, background_log

    def test_two_prefixes_same_basename_do_not_interfere(self):
        secondary = self.other_prefix
        process, background_log = self.start_long_fixture(self.prefix)
        observation = self.run_session(
            self.windows(self.game), ['--sleep', '500', '--exit', '0'], prefix=secondary)
        # Check survival even if the other helper returned an incorrect result.
        self.assertIsNone(process.poll(), self.diagnostics(self.artifacts))
        self.stop_prefix(secondary)
        self.assertIsNone(process.poll(), self.diagnostics(self.artifacts))
        self.assertNotIn('exited=', background_log.read_text(encoding='utf-8'))
        self.assert_exited(observation)


if __name__ == '__main__':
    unittest.main()
