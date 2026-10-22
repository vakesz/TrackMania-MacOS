import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLES = ROOT / 'config/titles'
REQUIRED = {'id', 'name', 'engine', 'installer', 'status', 'prefix_subdir',
            'prefix_marker', 'app_name', 'settings_app_name', 'game_exe',
            'launcher_exe', 'save_dirs', 'needs_directx', 'needs_tmloader'}
ENGINES = {'tmf-classic', 'maniaplanet', 'turbo', 'tm-original'}
INSTALLERS = {'online', 'online-retail', 'online-trial', 'steam-copy', 'disk-image'}
DOWNLOADERS = {'online', 'online-retail', 'online-trial'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')


def load_states_module():
    import sys
    sys.path.insert(0, str(ROOT / 'src/python'))
    from tmnf.hub import state
    return state


def load_paths_module():
    import sys
    sys.path.insert(0, str(ROOT / 'src/python'))
    from tmnf.hub import paths
    return paths


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
                self.assertEqual(data['prefix_marker'], data['id'] + ':1')
                self.assertTrue(data['app_name'].endswith('.app'))
                self.assertTrue(data['save_dirs'], data)
                self.assertTrue(all(isinstance(value, bool) for value in (
                    data['needs_directx'], data['needs_tmloader'])))
                if data['installer'] in DOWNLOADERS:
                    self.assertTrue(data['source_url'].startswith('http'), data)
                    self.assertRegex(data['source_sha256'], SHA256)
                    size = data.get('source_size')
                    self.assertTrue(size is None or size > 0, size)
                else:
                    for pin in ('source_url', 'source_sha256', 'source_size'):
                        self.assertFalse(data.get(pin), (pin, data.get(pin)))
                ids.add(data['id'])
        self.assertEqual(ids, {'tmnf', 'tmu', 'tm-original', 'tm-canyon', 'tm-turbo'})

    def test_prefix_subdirs_are_unique(self):
        subdirs = {}
        for path in sorted(TITLES.glob('*.json')):
            data = json.loads(path.read_text())
            self.assertNotIn(data['prefix_subdir'], subdirs, data['prefix_subdir'])
            subdirs[data['prefix_subdir']] = data['id']

    def test_downloader_pins_match_common_sh(self):
        """The registry and common.sh must state the same official TMNF pin."""
        data = json.loads((TITLES / 'tmnf.json').read_text())
        common = (ROOT / 'src/shell/lib/common.sh').read_text()
        self.assertIn(f'TMNF_GAME_URL="{data["source_url"]}"', common)
        self.assertIn(data['source_sha256'], common)
        self.assertEqual(data['game_exe'], 'TmForever.exe')
        self.assertEqual(data['prefix_marker'], 'tmnf:1')

    def test_python_state_pins_match_common_sh(self):
        state = load_states_module()
        common = (ROOT / 'src/shell/lib/common.sh').read_text()
        for constant in ('_DIRECTX_SHA256', '_TMLOADER_EXE_SHA256', '_TMLOADER_GAME_SHA256',
                         '_TMCOMP_DLL_SHA256', '_COREMOD_DLL_SHA256'):
            value = getattr(state, constant)
            self.assertIn(value, common, constant)

    def test_directx_version_pin_matches_common_sh(self):
        paths = load_paths_module()
        common = (ROOT / 'src/shell/lib/common.sh').read_text()
        version, sha = paths.directxpins()
        self.assertIn(f'DIRECTX_VERSION="{version}"', common)
        self.assertIn(sha, common)

    def test_installer_kinds_match_hub_registry(self):
        sys_path_added = ROOT / 'src/python'
        import sys
        sys.path.insert(0, str(sys_path_added))
        from tmnf.hub import registry
        self.assertEqual(registry.INSTALLERS, frozenset(INSTALLERS))

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

    def test_tmnf_source_override_does_not_change_other_title_cache_paths(self):
        result = subprocess.run(
            ['/bin/bash', '-euc',
             '. src/shell/lib/common.sh; . src/shell/lib/install/game.sh; '
             '_cache_installer_path tmu'],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMNF_TITLE': 'tmu',
                 'TMNF_CACHE': '/tmp/tmnf-cache',
                 'TMNF_SOURCE_URL': 'https://example.invalid/override.exe'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            '/tmp/tmnf-cache/games/tmu/tmunitedforever_setup.exe')

    def test_save_dirs_are_declared_for_every_guarded_name(self):
        """managed_paths.save_dir_names() must stay a superset of the registry."""
        import sys
        sys.path.insert(0, str(ROOT / 'src/python'))
        from tmnf.guard.managed_paths import save_dir_names
        guarded = set(save_dir_names())
        for path in sorted(TITLES.glob('*.json')):
            data = json.loads(path.read_text())
            with self.subTest(title=data['id']):
                for name in data['save_dirs']:
                    self.assertIn(name, guarded, name)

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
