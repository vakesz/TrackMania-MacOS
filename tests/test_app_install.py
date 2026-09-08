import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from lib import operation_guard as guard


ROOT = Path(__file__).resolve().parents[1]


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-app-', dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = self.home / 'current project'
        self.project.mkdir()
        for name in ('bin', 'lib', 'config'):
            shutil.copytree(ROOT / name, self.project / name)
        for name in ('VERSION', 'install.sh'):
            shutil.copy2(ROOT / name, self.project / name)
        tools = self.home / 'tools'
        tools.mkdir()
        (tools / 'python3').symlink_to(sys.executable)
        self.prefix = self.home / 'prefix'
        self.launcher = self.home / 'launcher'
        self.app = self.home / 'apps/Game.app'
        self.settings = self.home / 'apps/Settings.app'
        self.env = dict(HOME=str(self.home), PATH=str(tools) + ':' + os.environ['PATH'],
                        PYTHONDONTWRITEBYTECODE='1', MACTMNF_ROOT=str(self.home / 'game'),
                        MACTMNF_PREFIX=str(self.prefix), MACTMNF_RUNTIME=str(self.home / 'runtime'),
                        MACTMNF_SUPPORT=str(self.home / 'support'), MACTMNF_CACHE=str(self.home / 'cache'),
                        MACTMNF_USERDATA=str(self.home / 'userdata'), MACTMNF_LAUNCHER=str(self.launcher),
                        MACTMNF_APP=str(self.app), MACTMNF_SETTINGS_APP=str(self.settings),
                        MACTMNF_APPS_DIR=str(self.home / 'apps'), MACTMNF_X87='off')
        self.cli = ['/bin/bash', str(self.project / 'bin/mactmnf'), 'app']
        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.app / 'keep').write_text('old game content')
        (self.settings / 'keep').write_text('old settings content')
        self.before = self.snapshot()

    def run_app(self, **overrides):
        return subprocess.run(self.cli, env=dict(self.env, **overrides),
                              text=True, capture_output=True, timeout=15)

    def snapshot(self):
        return {str(path.relative_to(self.home)): path.read_bytes()
                for root in (self.app, self.settings, self.launcher)
                for path in root.rglob('*') if path.is_file()}

    def blocking_icon(self):
        ready = self.home / 'icon-ready'
        (self.project / 'lib/app_icon.py').write_text(
            'import os, pathlib, time\n'
            'assert "MACTMNF_OPERATION_FD" not in os.environ\n'
            'assert "MACTMNF_APP_OPERATION_FD" not in os.environ\n'
            f'pathlib.Path({str(ready)!r}).touch()\n'
            'time.sleep(60)\n')
        return ready

    def test_concurrent_refresh_refuses_without_mutating_existing_apps(self):
        ready = self.blocking_icon()
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        env = dict(self.env, **{guard.TOKEN: str(fd)})
        process = subprocess.Popen(
            [sys.executable, str(self.project / 'lib/operation_guard.py'), 'app-run',
             str(self.prefix), *self.cli], env=env, pass_fds=(fd,),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    self.fail(str(process.communicate()))
                self.assertLess(time.monotonic(), deadline, 'Icon extractor did not start')
                time.sleep(0.01)
            result = self.run_app(MACTMNF_PREFIX=str(self.home / 'second-prefix'))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Another app installation', result.stderr)
            self.assertEqual(self.snapshot(), self.before)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, (stdout, stderr))
            self.assertEqual(self.snapshot(), self.before)
            with self.assertRaisesRegex(ValueError, 'Another operation'):
                guard.acquire(self.prefix)
            (self.project / 'lib/app_icon.py').write_text('raise SystemExit(1)\n')
            result = self.run_app(MACTMNF_PREFIX=str(self.home / 'second-prefix'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.app / 'keep').read_text(), 'old game content')
            self.assertEqual((self.settings / 'keep').read_text(), 'old settings content')
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            process.communicate(timeout=8)

    def test_overlap_and_forged_token_preserve_existing_content(self):
        for overrides in ({'MACTMNF_APP': str(self.launcher / 'Game.app')},
                          {'MACTMNF_LAUNCHER': str(self.app / 'support')},
                          {'MACTMNF_SETTINGS_APP': str(self.app)},
                          {guard.APP_TOKEN: '999999'}):
            with self.subTest(overrides=overrides):
                result = self.run_app(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.snapshot(), self.before)

    def test_sourced_install_app_returns_and_uses_current_project(self):
        (self.project / 'VERSION').write_text('fixture-current\n')
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        result = subprocess.run(
            ['/bin/bash', '-euc', '. ./install.sh; . ./lib/apps.sh; install_app; '
             'python3 lib/operation_guard.py held "$MACTMNF_PREFIX"; printf "returned\\n"'],
            cwd=self.project, env=dict(self.env, **{guard.TOKEN: str(fd)}), pass_fds=(fd,),
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('returned', result.stdout)
        self.assertEqual((self.launcher / 'VERSION').read_text(), 'fixture-current\n')


if __name__ == '__main__':
    unittest.main()
