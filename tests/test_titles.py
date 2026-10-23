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
ENGINES = {'tmf-classic', 'maniaplanet', 'turbo'}
INSTALLERS = {'online', 'online-retail', 'online-trial', 'steam-copy', 'disk-image'}
DOWNLOADERS = {'online', 'online-retail', 'online-trial'}
SHA256 = re.compile(r'^[0-9a-f]{64}$')


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

    def test_shell_and_python_read_the_same_release_pins(self):
        """config/pins.json is the only home: shell and Python must agree on it."""
        import sys
        sys.path.insert(0, str(ROOT / 'src/python'))
        from tmnf.hub import registry
        pins = registry.pins()
        self.assertEqual(pins, {key: value for key, value in json.loads(
            (ROOT / 'config/pins.json').read_text()).items()})
        names = ' '.join(f'"${key.upper()}"' for key in pins)
        result = subprocess.run(
            ['/bin/bash', '-euc', f'. src/shell/lib/common.sh; printf "%s\\n" {names}'],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), list(pins.values()))
        for literal in pins.values():
            for source in ('src/shell/lib/common.sh', 'src/python/tmnf/hub/state.py',
                           'src/python/tmnf/hub/paths.py'):
                self.assertNotIn(literal, (ROOT / source).read_text(), (source, literal))

    def test_tmnf_installer_cache_path_is_historical(self):
        result = subprocess.run(
            ['/bin/bash', '-euc', '. src/shell/lib/common.sh; . src/shell/lib/install/game.sh; '
             '_cache_installer_path tmnf'],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMNF_CACHE': '/tmp/c'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '/tmp/c/tmnationsforever_setup.exe')

    def test_staged_payload_contains_pins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ['/bin/bash', '-euc', '. src/shell/lib/common.sh; '
                 '. src/shell/lib/apps/payload.sh; '
                 f'stage_launcher_payload "$PWD" "{directory}/p"'],
                cwd=ROOT, text=True, capture_output=True, timeout=60,
                env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(directory) / 'p/config/pins.json').read_text(),
                             (ROOT / 'config/pins.json').read_text())

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



class RegistryValidationTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(ROOT / 'src/python'))
        from tmnf.hub import registry
        self.registry = registry
        self.data = json.loads((TITLES / 'tmu.json').read_text())

    def validate(self, **changes):
        data = dict(self.data, **changes)
        return self.registry._validate(data, Path('/x/tmu.json'))

    def test_shipped_entry_is_valid(self):
        self.validate()

    def test_unsafe_names_are_rejected(self):
        bad = ['.', '..', '~root', 'a\\b', 'a\nb', 'a\x00b', 'a\x7fb', 'a/b']
        for field in ('short_name', 'game_exe', 'launcher_exe', 'game_dir_name'):
            for value in bad:
                with self.subTest(field=field, value=value), \
                        self.assertRaises(self.registry.RegistryError):
                    self.validate(**{field: value})
        for field, value in (('app_name', '~x.app'), ('settings_app_name', '..'),
                             ('app_name', 'a\\b.app')):
            with self.subTest(field=field, value=value), \
                    self.assertRaises(self.registry.RegistryError):
                self.validate(**{field: value})
        for value in ('.', '..', '~x', 'a\\b', 'a\tb'):
            with self.subTest(save_dir=value), self.assertRaises(self.registry.RegistryError):
                self.validate(save_dirs=[value])
        for value in ('TMNF/..', '/abs', 'a//b', '~/x', 'a/./b', 'a\\b', 'TMNF/a\nb'):
            with self.subTest(prefix_subdir=value), \
                    self.assertRaises(self.registry.RegistryError):
                self.validate(prefix_subdir=value)

    def test_bundle_ids_and_urls(self):
        for value in ('a b', 'a;b', 'a$b', 'a/b', 'a_b'):
            with self.subTest(bundle=value), self.assertRaises(self.registry.RegistryError):
                self.validate(bundle_id_game=value)
        for value in ('ftp://x/y', 'file:///etc/passwd', 'https://x/a b', "https://x/'"):
            with self.subTest(url=value), self.assertRaises(self.registry.RegistryError):
                self.validate(source_url=value)
        self.validate(source_url='https://example.test/x.exe')
        with self.assertRaises(self.registry.RegistryError):
            self.validate(source_url='http://example.test/x.exe', source_sha256=None)

    def test_export_round_trips_hostile_but_valid_values(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            hostile = dict(self.data, id='evil', name="$(touch pwned) `id` ' \" ; &",
                           notes='line1\nline2 $HOME \\ *', short_name='a b$c',
                           prefix_marker='evil:1')
            titles = Path(directory) / 'titles'
            titles.mkdir()
            (titles / 'evil.json').write_text(json.dumps(hostile))
            result = subprocess.run(
                ['/bin/bash', '-euc', '. src/shell/lib/common.sh; '
                 'printf "%s\\0" "$TMNF_TITLE_NAME" "$TMNF_TITLE_NOTES" "$TMNF_TITLE_SHORT_NAME"'],
                cwd=ROOT, capture_output=True, timeout=15,
                env={'PATH': '/usr/bin:/bin', 'HOME': directory, 'TMNF_TITLE': 'evil',
                     'TMNF_TITLES_DIR': str(titles)})
            self.assertEqual(result.returncode, 0, result.stderr)
            name, notes, short = result.stdout.decode().split('\0')[:3]
            self.assertEqual((name, notes, short), (hostile['name'], hostile['notes'],
                                                    hostile['short_name']))
            self.assertFalse((ROOT / 'pwned').exists())
            self.assertFalse((Path(directory) / 'pwned').exists())

    def test_shell_honors_titles_dir_like_python(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            titles = Path(directory) / 'titles'
            titles.mkdir()
            (titles / 'tmu.json').write_text(json.dumps(dict(self.data, name='Custom')))
            result = subprocess.run(
                ['/bin/bash', '-euc', '. src/shell/lib/common.sh; printf "%s" "$TMNF_TITLE_NAME"'],
                cwd=ROOT, text=True, capture_output=True, timeout=15,
                env={'PATH': '/usr/bin:/bin', 'HOME': directory, 'TMNF_TITLE': 'tmu',
                     'TMNF_TITLES_DIR': str(titles)})
            self.assertEqual(result.stdout, 'Custom', result.stderr)


if __name__ == '__main__':
    unittest.main()
