import os
import plistlib
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
        self.game_exe = (self.prefix / 'drive_c/Program Files (x86)/TmNationsForever/TmForever.exe')
        self.game_exe.parent.mkdir(parents=True)
        self.game_exe.write_bytes(b'MZ')
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
        (self.project / 'src/python/tmnf/app/icon.py').write_text(
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
            (self.project / 'src/python/tmnf/app/icon.py').write_text('raise SystemExit(1)\n')
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
            ['/bin/bash', '-euc', '. ./src/shell/cli/install.sh; . ./src/shell/lib/apps/wrappers.sh; install_app; '
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

    def per_title_env(self, title, **extra):
        """Environment for a title using the default per-title launcher location."""
        env = {k: v for k, v in self.env.items() if k != 'TMNF_LAUNCHER'}
        env.update(TMNF_TITLE=title, TMNF_PREFIX=str(self.home / f'{title}-prefix'),
                   TMNF_APP=str(self.home / f'apps/{title}-Game.app'),
                   TMNF_SETTINGS_APP=str(self.home / f'apps/{title}-Settings.app'))
        env.update(extra)
        return env

    def run_env(self, env):
        return subprocess.run(self.cli, env=env, text=True, capture_output=True, timeout=15)

    def test_titles_install_into_separate_launchers(self):
        support = self.home / 'support'
        for title in ('tmnf', 'tmu'):
            result = self.run_env(self.per_title_env(title))
            self.assertEqual(result.returncode, 0, result.stderr)
        for title in ('tmnf', 'tmu'):
            launcher = support / 'launchers' / title
            settings = (launcher / 'config/installation.sh').read_text()
            self.assertIn(f'export TMNF_TITLE={title}; fi\n', settings)
            wrapper = (self.home / f'apps/{title}-Game.app/Contents/MacOS/TMNF').read_text()
            self.assertIn(str(launcher / 'python/bin/python3'), wrapper)
        self.assertNotIn('TMNF_TITLE=tmu', (support / 'launchers/tmnf/config/installation.sh').read_text())

    def test_legacy_shared_launcher_is_removed_only_when_it_is_this_titles(self):
        support = self.home / 'support'
        legacy = support / 'launcher'
        result = self.run_env(self.per_title_env('tmnf', TMNF_LAUNCHER=str(legacy)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(legacy.is_dir())
        result = self.run_env(self.per_title_env('tmnf'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        wrapper = (self.home / 'apps/tmnf-Game.app/Contents/MacOS/TMNF').read_text()
        self.assertIn(str(support / 'launchers/tmnf/python/bin/python3'), wrapper)

        # A legacy launcher last installed for another title is left alone.
        result = self.run_env(self.per_title_env('tmu', TMNF_LAUNCHER=str(legacy)))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_env(self.per_title_env('tmnf'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('another title', result.stderr)
        self.assertTrue((legacy / '.tmnf-launcher').exists())

    def test_symlinked_legacy_launcher_is_never_followed(self):
        support = self.home / 'support'
        victim = self.home / 'victim'
        victim.mkdir()
        (victim / 'keep').write_text('data')
        support.mkdir(exist_ok=True)
        (support / 'launcher').symlink_to(victim)
        result = self.run_env(self.per_title_env('tmnf'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((victim / 'keep').read_text(), 'data')
        self.assertTrue((support / 'launcher').is_symlink())

    def test_plist_values_are_xml_escaped(self):
        app = self.home / 'apps/R&D <"it\'s">.app'
        result = self.run_app(TMNF_APP=str(app))
        self.assertEqual(result.returncode, 0, result.stderr)
        plist = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        self.assertEqual(plist['CFBundleName'], 'R&D <"it\'s">')

    def test_symlinked_executable_or_icon_is_refused(self):
        for relative in ('Contents/MacOS/TMNF', 'Contents/Resources/AppIcon.icns'):
            with self.subTest(relative=relative):
                target = self.home / 'target'
                target.write_text('untouched')
                entry = self.app / relative
                entry.unlink(missing_ok=True)
                entry.symlink_to(target)
                result = self.run_app()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('symlink', result.stderr)
                self.assertEqual(target.read_text(), 'untouched')
                entry.unlink()
                self.assertEqual(self.run_app().returncode, 0)

    def test_wrapper_fails_closed_without_launcher_python(self):
        entry = self.app / 'Contents/MacOS/TMNF'
        self.assertEqual(os.stat(entry).st_mode & 0o111, 0o111)
        self.assertFalse((self.launcher / 'python/bin/python3').exists())
        alerts = self.home / 'alerts'
        alert = self.home / 'alert'
        alert.write_text(f'#!/bin/bash\necho "$@" >> {alerts}\n')
        alert.chmod(0o755)
        entry.write_text(entry.read_text().replace('/usr/bin/osascript', str(alert)))
        # self.env's PATH has a python3; it must not be used as a fallback.
        result = subprocess.run(['/bin/bash', str(entry)], env=self.env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('launcher support files are missing', alerts.read_text())

    def stub_icon_tool(self):
        (self.project / 'src/python/tmnf/app/icon.py').write_text(
            'import pathlib, sys\n'
            'pathlib.Path(sys.argv[-1]).write_bytes(b"icns-converted" if sys.argv[1] == "--from-png" '
            'else b"icns-extracted")\n')

    def test_game_icon_is_extracted_lazily_and_copied_into_both_apps(self):
        self.stub_icon_tool()
        cache = self.home / 'support/icons/tmnf.icns'
        self.assertFalse(cache.exists())
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual(cache.read_bytes(), b'icns-extracted')
        for app in (self.app, self.settings):
            self.assertEqual((app / 'Contents/Resources/AppIcon.icns').read_bytes(), b'icns-extracted')
        self.assertFalse(list(cache.parent.glob('.extract.*')))

    def test_bundled_artwork_is_the_fallback_until_a_game_icon_exists(self):
        self.stub_icon_tool()
        self.game_exe.unlink()
        art = self.project / 'assets/titles'
        art.mkdir(parents=True)
        (art / 'tmnf.png').write_bytes(b'png')
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual((self.app / 'Contents/Resources/AppIcon.icns').read_bytes(), b'icns-converted')
        self.assertFalse((self.home / 'support/icons/tmnf.icns').exists())
        # A cached game icon wins over the bundled artwork.
        (self.home / 'support/icons').mkdir(parents=True, exist_ok=True)
        (self.home / 'support/icons/tmnf.icns').write_bytes(b'icns-game')
        self.assertEqual(self.run_app().returncode, 0)
        self.assertEqual((self.app / 'Contents/Resources/AppIcon.icns').read_bytes(), b'icns-game')


if __name__ == '__main__':
    unittest.main()
