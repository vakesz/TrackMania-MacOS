import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLES = ROOT / 'config/titles'
REQUIRED = {'id', 'name', 'engine', 'installer', 'status', 'prefix_subdir',
            'prefix_marker', 'app_name', 'settings_app_name', 'game_exe',
            'launcher_exe', 'save_dirs'}
ENGINES = {'tmf-classic', 'maniaplanet', 'turbo'}
INSTALLERS = {'free-download', 'disk-image'}


class TitleRegistryTests(unittest.TestCase):
    def test_allTitle_json_valid(self):
        ids = set()
        for path in sorted(TITLES.glob('*.json')):
            with self.subTest(title=path.name):
                data = json.loads(path.read_text())
                self.assertTrue(REQUIRED <= set(data), data)
                self.assertEqual(path.stem, data['id'])
                self.assertIn(data['engine'], ENGINES)
                self.assertIn(data['installer'], INSTALLERS)
                self.assertIn(data['status'], ('supported', 'provisional'))
                self.assertTrue(data['prefix_marker'].endswith(':1'))
                self.assertTrue(data['app_name'].endswith('.app'))
                ids.add(data['id'])
        self.assertEqual(ids, {'tmnf', 'tmu', 'tm-original', 'tm-canyon', 'tm-turbo'})

    def test_tmnf_registry_matches_common_sh(self):
        data = json.loads((TITLES / 'tmnf.json').read_text())
        common = (ROOT / 'src/shell/lib/common.sh').read_text()
        self.assertIn('TMNF_GAME_URL="https://nadeo-download.cdn.ubi.com/trackmaniaforever/tmnationsforever_setup.exe"', common)
        self.assertEqual(data['game_exe'], 'TmForever.exe')
        self.assertEqual(data['prefix_marker'], 'tmnf:1')

    def test_common_sh_title_defaults(self):
        result = subprocess.run(
            ['/bin/bash', '-euc', '. src/shell/lib/common.sh; printf "%s\\n" "$TMNF_TITLE" "$GAME_EXE" "$TMNF_ROOT"'],
            cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        title, game_exe, root = result.stdout.splitlines()
        self.assertEqual(title, 'tmnf')
        self.assertTrue(game_exe.endswith('TmNationsForever/TmForever.exe'))
        self.assertTrue(root.endswith('Games/TMNF'))

    def test_shell_matches_title_registry(self):
        for path in sorted(TITLES.glob('*.json')):
            data = json.loads(path.read_text())
            with self.subTest(title=data['id']):
                result = subprocess.run(
                    ['/bin/bash', '-euc', '. src/shell/lib/common.sh; printf "%s\\n" "$GAME_EXE" '
                     '"$LAUNCHER_EXE" "$TMNF_ROOT" "$TMNF_APP" "$TMNF_SETTINGS_APP"'],
                    cwd=ROOT, text=True, capture_output=True, timeout=15,
                    env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMNF_TITLE': data['id']})
                self.assertEqual(result.returncode, 0, result.stderr)
                game_exe, launcher_exe, root, app, settings_app = result.stdout.splitlines()
                game_dir = 'drive_c/Program Files (x86)/{}'.format(data['game_dir_name'])
                self.assertTrue(game_exe.endswith(game_dir + '/' + data['game_exe']), game_exe)
                self.assertTrue(launcher_exe.endswith(game_dir + '/' + data['launcher_exe']),
                                launcher_exe)
                self.assertTrue(root.endswith('Games/' + data['prefix_subdir']), root)
                self.assertTrue(app.endswith('/' + data['app_name']), app)
                self.assertTrue(settings_app.endswith('/' + data['settings_app_name']),
                                settings_app)

    def test_cli_titles_lists_all(self):
        result = subprocess.run(
            ['/bin/bash', str(ROOT / 'src/shell/cli/tmnf'), 'titles'],
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        for title_id in ('tmnf', 'tmu', 'tm-original', 'tm-canyon', 'tm-turbo'):
            self.assertIn(title_id, result.stdout)

    def test_unknown_title_rejected(self):
        result = subprocess.run(
            ['/bin/bash', '-euc', '. src/shell/lib/common.sh; validate_launch_options'],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMNF_TITLE': 'nope'})
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
