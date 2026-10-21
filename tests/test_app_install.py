import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tmnf.guard import operation_guard as guard


ROOT = Path(__file__).resolve().parents[1]


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tmnf-app-', dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.project = self.home / 'current project'
        self.project.mkdir()
        for name in ('src', 'config'):
            shutil.copytree(ROOT / name, self.project / name)
        shutil.copy2(ROOT / 'VERSION', self.project / 'VERSION')
        tools = self.home / 'tools'
        tools.mkdir()
        (tools / 'python3').symlink_to(sys.executable)
        self.prefix = self.home / 'prefix'
        self.launcher = self.home / 'launcher'
        self.app = self.home / 'apps/Game.app'
        self.settings = self.home / 'apps/Settings.app'
        self.env = dict(HOME=str(self.home), PATH=str(tools) + ':' + os.environ['PATH'],
                        PYTHONDONTWRITEBYTECODE='1', TMNF_ROOT=str(self.home / 'game'),
                        TMNF_PREFIX=str(self.prefix), TMNF_RUNTIME=str(self.home / 'runtime'),
                        TMNF_SUPPORT=str(self.home / 'support'), TMNF_CACHE=str(self.home / 'cache'),
                        TMNF_USERDATA=str(self.home / 'userdata'), TMNF_LAUNCHER=str(self.launcher),
                        TMNF_APP=str(self.app), TMNF_SETTINGS_APP=str(self.settings),
                        TMNF_APPS_DIR=str(self.home / 'apps'), TMNF_X87='off')
        self.cli = ['/bin/bash', str(self.project / 'src/shell/cli/tmnf'), 'app']
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
        (self.project / 'src/python/tmnf/app/app_icon.py').write_text(
            'import os, pathlib, time\n'
            'assert "TMNF_OPERATION_FD" not in os.environ\n'
            'assert "TMNF_APP_OPERATION_FD" not in os.environ\n'
            f'pathlib.Path({str(ready)!r}).touch()\n'
            'time.sleep(60)\n')
        return ready

    def test_concurrent_refresh_refuses_without_mutating_existing_apps(self):
        ready = self.blocking_icon()
        fd = guard.acquire(self.prefix)
        self.addCleanup(os.close, fd)
        env = dict(self.env, **{guard.TOKEN: str(fd)})
        process = subprocess.Popen(
            [sys.executable, str(self.project / 'src/python/tmnf/guard/operation_guard.py'), 'app-run',
             str(self.prefix), *self.cli], env=env, pass_fds=(fd,),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    self.fail(str(process.communicate()))
                self.assertLess(time.monotonic(), deadline, 'Icon extractor did not start')
                time.sleep(0.01)
            result = self.run_app(TMNF_PREFIX=str(self.home / 'second-prefix'))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Another app installation', result.stderr)
            self.assertEqual(self.snapshot(), self.before)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 143, (stdout, stderr))
            self.assertEqual(self.snapshot(), self.before)
            with self.assertRaisesRegex(ValueError, 'Another operation'):
                guard.acquire(self.prefix)
            (self.project / 'src/python/tmnf/app/app_icon.py').write_text('raise SystemExit(1)\n')
            result = self.run_app(TMNF_PREFIX=str(self.home / 'second-prefix'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.app / 'keep').read_text(), 'old game content')
            self.assertEqual((self.settings / 'keep').read_text(), 'old settings content')
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            process.communicate(timeout=8)

    def test_overlap_and_forged_token_preserve_existing_content(self):
        for overrides in ({'TMNF_APP': str(self.launcher / 'Game.app')},
                          {'TMNF_LAUNCHER': str(self.app / 'support')},
                          {'TMNF_SETTINGS_APP': str(self.app)},
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
            ['/bin/bash', '-euc', '. ./src/shell/cli/install.sh; . ./src/shell/lib/app/apps.sh; install_app; '
             'PYTHONPATH=src/python python3 -m tmnf.guard.operation_guard held "$TMNF_PREFIX"; printf "returned\\n"'],
            cwd=self.project, env=dict(self.env, **{guard.TOKEN: str(fd)}), pass_fds=(fd,),
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('returned', result.stdout)
        self.assertEqual((self.launcher / 'VERSION').read_text(), 'fixture-current\n')

    def test_refresh_carries_a_bundled_python_into_installed_app_support(self):
        bundled = self.project / 'python/bin/python3'
        bundled.parent.mkdir(parents=True)
        bundled.symlink_to(sys.executable)

        result = self.run_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.launcher / 'python/bin/python3'
        self.assertTrue(installed.is_file())
        self.assertTrue(os.access(installed, os.X_OK))
        wrapper = (self.app / 'Contents/MacOS/TMNF').read_text()
        self.assertIn(str(installed), wrapper)


if __name__ == '__main__':
    unittest.main()
