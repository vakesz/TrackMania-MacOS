import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from lib.managed_paths import check_layout, check_prefix, check_uninstall, record_profile_baseline, safe_path, setup_documents
from lib.runtime_manifest import EXECUTABLES, REQUIRED_FILES, digest, verify_runtime


ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-installation-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.prefix = self.home / 'game/prefix'
        self.runtime = self.home / 'runtime'
        self.log = self.home / 'wine.log'
        tools = self.home / 'tools'
        tools.mkdir()
        (tools / 'python3').symlink_to(sys.executable)
        self.env = dict(PATH=str(tools) + os.pathsep + os.environ['PATH'], HOME=str(self.home), TMPDIR=str(self.home),
                        MACTMNF_ROOT=str(self.home / 'game'), MACTMNF_PREFIX=str(self.prefix),
                        MACTMNF_RUNTIME=str(self.runtime), MACTMNF_SUPPORT=str(self.home / 'support'),
                        MACTMNF_APPS_DIR=str(self.home / 'apps'), MACTMNF_CACHE=str(self.home / 'cache'),
                        MACTMNF_X87='off',
                        TEST_LOG=str(self.log))
        for name in REQUIRED_FILES + EXECUTABLES + ('licenses/COPYING',):
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/bash\nexit 0\n' if name in EXECUTABLES else 'fake ' + name)
            if name in EXECUTABLES:
                path.chmod(0o755)
        wine = self.runtime / 'bin/wine'
        wine.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(json.dumps(dict(binary=Path(sys.argv[0]).name, args=args,
                              prefix=os.environ.get('WINEPREFIX'))) + '\\n')
if args == ['--version']:
    print('wine-test')
elif args and args[0].endswith('cmd.exe'):
    print('Microsoft Windows fixture')
elif args and Path(args[0]).name == 'mactmnf-session.exe':
    options = args[1:args.index('--')]
    result = Path(options[options.index('--result') + 1])
    temporary = result.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(state='exited', exit_code=0, cleanup=True)))
    temporary.chmod(0o600)
    temporary.replace(result)
''')
        shutil.copy2(wine, self.runtime / 'bin/wineserver')
        version = (ROOT / 'VERSION').read_text().strip()
        (self.runtime / '.mactmnf-runtime').write_text(version + '\n')
        (self.runtime / 'SHA256SUMS').write_text(''.join(
            '{}  {}\n'.format(digest(path), path.relative_to(self.runtime))
            for path in sorted(self.runtime.rglob('*')) if path.is_file()))
        verify_runtime(self.runtime, version)
        patcher = mock.patch.dict(os.environ, HOME=str(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)

    def shell(self, command, cwd=ROOT, timeout=15, **overrides):
        self.log.unlink(missing_ok=True)
        return subprocess.run(['/bin/bash', '-euc', command], cwd=cwd,
                              env=dict(self.env, **overrides), text=True, capture_output=True, timeout=timeout)

    def modded_fixture(self):
        project = self.home / 'fixture project'
        project.mkdir()
        for name in ('bin', 'lib', 'config'):
            shutil.copytree(ROOT / name, project / name)
        for name in ('VERSION', 'install.sh'):
            shutil.copy2(ROOT / name, project / name)
        result = self.shell('. ./lib/common.sh; printf "%s\\n" "$GAME_EXE" "$TMLOADER_EXE" '
                            '"$TMCOMP_DLL" "$TMLOADER_GAME_EXE" "$TMLOADER_COREMOD_DLL"', cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        game, loader, patch, target, coremod = map(Path, result.stdout.splitlines())
        common = project / 'lib/common.sh'
        source = common.read_text()
        # Only this copy accepts fake payloads. Pin the base game too so update
        # repairs the profile without downloading the official installer.
        for constant, path in (('TMNF_EXE_SHA256', game), ('TMLOADER_EXE_SHA256', loader),
                               ('TMCOMP_DLL_SHA256', patch), ('TMLOADER_GAME_SHA256', target),
                               ('TMLOADER_COREMOD_SHA256', coremod)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fake pinned ' + constant)
            source, count = re.subn(r'^' + constant + r'="[0-9a-f]{64}"$',
                                    '{}="{}"'.format(constant, digest(path)), source, flags=re.MULTILINE)
            self.assertEqual(count, 1)
        common.write_text(source)
        (game.parent / 'TmForeverLauncher.exe').touch()
        (self.prefix / '.mactmnf-prefix').write_text('tmnf:1\n')
        (self.prefix / 'system.reg').touch()
        for directory in ('syswow64', 'system32'):
            (self.prefix / 'drive_c/windows' / directory).mkdir(parents=True)
        shutil.copy2(self.runtime / 'lib/d9vk/d3d9.dll', self.prefix / 'drive_c/windows/syswow64/d3d9.dll')
        result = self.shell('. ./install.sh; configure_competition_patch; '
                            'printf "%s\\n" "$DIRECTX_SHA256" >"$DIRECTX_MARKER"', cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        return project, game, loader, patch, target, coremod

    def test_protected_and_ambiguous_paths(self):
        for value in ('/', str(self.home), str(self.home) + '///', '/private/tmp/',
                      'relative', str(self.home / 'a/../b'), str(self.home) + '/./b',
                      str(self.home) + '/bad\nname'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_path(value)
        self.assertEqual(safe_path(str(self.prefix) + '//'), self.prefix)

    def test_symlinks_and_physical_overlap(self):
        alias = self.home / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_path(str(alias))
        with self.assertRaises(ValueError):
            check_layout([str(self.prefix), str(alias / 'game/prefix/saves')])
        check_layout([str(self.prefix), str(self.runtime), str(self.home / 'game/saves')])

    def test_home_ancestors_are_protected_even_inside_temporary_root(self):
        nested_home = self.home / 'nested-home'
        nested_home.mkdir()
        with mock.patch.dict(os.environ, HOME=str(nested_home)):
            with self.assertRaises(ValueError):
                safe_path(str(self.home))

    def test_installer_can_be_sourced_after_common(self):
        result = self.shell('. lib/common.sh; . ./install.sh; declare -F initialize_prefix')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.log.exists())

    def test_installed_game_app_runs_without_checkout(self):
        project, _, _, _, _, _ = self.modded_fixture()
        result = self.shell('./bin/mactmnf app', cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.home / 'apps/TrackMania Nations Forever.app/Contents/MacOS/MacTMNF'
        # Keep the generated launcher intact apart from the OS alert adapter.
        # A regression must fail the test, not open a modal dialog on this Mac.
        alert = self.home / 'alert'
        alert.write_text('#!/bin/bash\nexit 0\n')
        alert.chmod(0o755)
        entry.write_text(entry.read_text().replace('/usr/bin/osascript', str(alert)))
        project.rename(self.home / 'checkout moved away')
        env = {'PATH': self.env['PATH'], 'HOME': str(self.home), 'TEST_LOG': str(self.log),
               'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run(['/bin/bash', str(entry)], env=env, text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.home / 'Library/Logs/MacTMNF/play.log'
        self.assertIn('target status 0x00000000', log.read_text())
        self.assertIn('exit status=0', log.read_text())
        self.assertFalse(project.exists())

    def test_existing_unmanaged_directory_is_never_claimed(self):
        self.prefix.mkdir(parents=True)
        personal = self.prefix / 'personal.txt'
        personal.write_text('keep')
        result = self.shell('. ./install.sh; initialize_prefix')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(personal.read_text(), 'keep')
        self.assertFalse((self.prefix / '.mactmnf-prefix').exists())
        self.assertFalse(self.log.exists())

    def test_marker_requires_known_identity_or_legacy_layout(self):
        self.prefix.mkdir(parents=True)
        marker = self.prefix / '.mactmnf-prefix'
        marker.write_text('')
        with self.assertRaises(ValueError):
            check_prefix(str(self.prefix))
        (self.prefix / 'system.reg').touch()
        check_prefix(str(self.prefix))
        marker.write_text('another-game:1\n')
        with self.assertRaises(ValueError):
            check_prefix(str(self.prefix))
        marker.write_text('tmnf:1\n')
        check_prefix(str(self.prefix))
        marker.unlink()
        marker.symlink_to(self.prefix / 'system.reg')
        with self.assertRaises(ValueError):
            check_prefix(str(self.prefix))

    def test_new_prefix_must_be_absent_or_empty(self):
        check_prefix(str(self.prefix), creating=True)
        self.prefix.mkdir(parents=True)
        check_prefix(str(self.prefix), creating=True)
        (self.prefix / '.hidden').touch()
        with self.assertRaises(ValueError):
            check_prefix(str(self.prefix), creating=True)

    def test_doctor_does_not_initialize_missing_prefix(self):
        result = self.shell('./bin/mactmnf doctor')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('WoW64 probe skipped', result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assertEqual(json.loads(self.log.read_text())['args'], ['--version'])

    def test_doctor_rejects_missing_session_helper_without_running_wine(self):
        (self.runtime / 'libexec/mactmnf-session.exe').unlink()
        result = self.shell('./bin/mactmnf doctor')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Wine runtime is missing, incomplete', result.stderr)
        self.assertFalse(self.log.exists())

    def test_doctor_validates_effective_translator(self):
        good = self.runtime / 'bin/sidecar'
        good.write_text('#!/bin/bash\nexit 0\n')
        good.chmod(0o755)
        result = self.shell('./bin/mactmnf doctor', MACTMNF_X87='on',
                            MACTMNF_SIDECAR=str(good), ROSETTA_X87_PATH=str(self.home / 'missing'))
        self.assertIn('x87sidecar is missing or incompatible', result.stderr)
        self.assertNotIn('x87sidecar: compatible', result.stdout)

    def test_invalid_launch_options_fail_before_wine(self):
        cases = [('MACTMNF_WINDOW_SIZE', value) for value in
                 ('0x720', '1280x0', '1280x720junk', ' 1280x720', '-1x720', '1280xx720',
                  '65536x720', '720x65536', '999999999999999999999x720')]
        cases += [('MACTMNF_AUDIO', 'invalid'), ('MACTMNF_X87', 'invalid'),
                  ('MACTMNF_RENDERER', 'invalid')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                result = self.shell('./bin/mactmnf configure', **{key: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_reused_directx_repairs_all_compilers_without_wine(self):
        self.prefix.mkdir(parents=True)
        for arch, directory in (('i386', 'syswow64'), ('x86_64', 'system32')):
            source = self.runtime / ('lib/wine/{}-windows'.format(arch))
            source.mkdir(parents=True, exist_ok=True)
            dest = self.prefix / 'drive_c/windows' / directory
            dest.mkdir(parents=True)
            for dll in ('d3dx9_30', 'd3dx9_43'):
                (source / (dll + '.dll')).write_text(arch + dll)
                (dest / (dll + '.dll')).write_text('native')
        result = self.shell('. ./install.sh; printf "%s\\n" "$DIRECTX_SHA256" >"$DIRECTX_MARKER"; install_directx_runtime')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        for arch, directory in (('i386', 'syswow64'), ('x86_64', 'system32')):
            for dll in ('d3dx9_30', 'd3dx9_43'):
                self.assertEqual((self.prefix / 'drive_c/windows' / directory / (dll + '.dll')).read_text(), arch + dll)

    def test_mod_readiness_is_distinct_from_payload(self):
        project, _, loader, _, target, _ = self.modded_fixture()
        for yaml in (target.parent.parent / 'settings.yaml',
                     loader.parent / 'database/TmForever/profiles/competition.yaml'):
            with self.subTest(yaml=yaml):
                original = yaml.read_text()
                yaml.unlink()
                result = self.shell('. ./lib/common.sh; competition_patch_is_current; '
                                    '! competition_patch_is_ready', cwd=project)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.log.exists())
                result = self.shell('./bin/mactmnf play', cwd=project)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Competition Patch is missing, outdated, or unconfigured', result.stderr)
                self.assertFalse(self.log.exists())
                result = self.shell('./bin/mactmnf update', cwd=project)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(yaml.read_text(), original)
                self.assertNotIn('Downloading', result.stdout)
                result = self.shell('./bin/mactmnf play', cwd=project)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_modded_play_watches_exact_compatibility_executable(self):
        project, game, loader, _, target, _ = self.modded_fixture()
        result = self.shell('./bin/mactmnf play /test-argument "argument with spaces" ""', cwd=project)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        helpers = [call for call in calls if call['args'][0] == str(self.runtime / 'libexec/mactmnf-session.exe')]
        self.assertEqual(len(helpers), 1)
        args = helpers[0]['args']
        options = args[1:args.index('--')]
        self.assertEqual([options[index + 1] for index, option in enumerate(options) if option == '--watch'],
                         [str(target)])
        self.assertNotEqual(target, game)
        self.assertEqual(args[args.index('--') + 1:],
                         [str(loader), 'run', 'TmForever', 'competition', '/test-argument', 'argument with spaces', ''])
        self.assertEqual([call['args'] for call in calls if call['binary'] == 'wineserver'], [['-k'], ['-w']])
        self.assertTrue(all(call['prefix'] == str(self.prefix) for call in calls))

    def test_corrupt_mod_payload_fails_before_wine(self):
        project, _, *payloads = self.modded_fixture()
        for payload in payloads:
            with self.subTest(payload=payload):
                original = payload.read_bytes()
                payload.write_bytes(original + b'corrupt')
                result = self.shell('./bin/mactmnf play', cwd=project)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Competition Patch is missing, outdated, or unconfigured', result.stderr)
                self.assertFalse(self.log.exists())
                payload.write_bytes(original)

    def test_missing_session_helper_fails_before_play_registry_work(self):
        project, *_ = self.modded_fixture()
        (self.runtime / 'libexec/mactmnf-session.exe').unlink()
        for command in ('play', 'play-vanilla'):
            with self.subTest(command=command):
                result = self.shell('./bin/mactmnf ' + command, cwd=project)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('runtime has no session supervisor', result.stderr)
                self.assertFalse(self.log.exists())

    def test_uninstall_refuses_nested_userdata(self):
        self.prefix.mkdir(parents=True)
        (self.prefix / '.mactmnf-prefix').write_text('tmnf:1\n')
        saves = self.prefix / 'saved-data'
        saves.mkdir()
        (saves / 'replay').write_text('keep')
        result = self.shell('./bin/mactmnf uninstall --yes', MACTMNF_USERDATA=str(saves))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((saves / 'replay').read_text(), 'keep')

    def documents_fixture(self, user='player'):
        documents = self.prefix / 'drive_c/users' / user / 'Documents'
        documents.mkdir(parents=True)
        (self.prefix / '.mactmnf-prefix').write_text('tmnf:1\n')
        return documents

    def test_uninstall_checks_all_profiles_and_is_read_only(self):
        self.documents_fixture()
        documents = self.documents_fixture('second')
        config = documents / 'TmForever/Config/settings'
        config.parent.mkdir(parents=True)
        config.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'Back up'):
            check_uninstall(self.prefix, self.home / 'userdata')
        self.assertEqual(config.read_text(), 'keep')
        result = self.shell('python3 lib/managed_paths.py uninstall "$MACTMNF_PREFIX" "$HOME/userdata"')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_uninstall_outside_documents_and_alias_back_inside(self):
        documents = self.documents_fixture()
        documents.rmdir()
        outside = self.home / 'personal-documents'
        (outside / 'TmForever/Replays').mkdir(parents=True)
        replay = outside / 'TmForever/Replays/race'
        replay.write_text('keep')
        documents.symlink_to(outside)
        check_uninstall(self.prefix, self.home / 'userdata')
        internal = self.prefix / 'old-saves'
        internal.mkdir()
        (internal / 'settings').write_text('keep')
        (outside / 'TmForever/Config').symlink_to(internal)
        with self.assertRaisesRegex(ValueError, 'Back up'):
            check_uninstall(self.prefix, self.home / 'userdata')
        self.assertEqual(replay.read_text(), 'keep')
        self.assertEqual(documents.readlink(), outside)

    def test_uninstall_internal_alias_and_my_documents(self):
        documents = self.documents_fixture()
        (documents / 'TmForever').mkdir()
        (documents / 'TmForever/replay').write_text('keep')
        alias = self.home / 'alias'
        alias.symlink_to(documents)
        legacy = self.prefix / 'drive_c/Documents and Settings/legacy'
        legacy.mkdir(parents=True)
        (legacy / 'My Documents').symlink_to(alias)
        with self.assertRaises(ValueError):
            check_uninstall(self.prefix, self.home / 'userdata')

    def test_save_checks_fail_closed_for_broken_and_cyclic_links(self):
        documents = self.documents_fixture()
        documents.rmdir()
        for target in (self.home / 'missing', documents):
            with self.subTest(target=target):
                documents.symlink_to(target)
                for check in (check_uninstall, setup_documents):
                    with self.assertRaises((ValueError, OSError, RuntimeError)):
                        check(self.prefix, self.home / 'userdata')
                self.assertTrue(documents.is_symlink())
                documents.unlink()

    def test_empty_documents_setup_and_retry_preserve_settings(self):
        documents = self.documents_fixture()
        (documents.parent / 'My Documents').symlink_to(documents)
        (documents.parent / 'Desktop').mkdir()
        userdata = self.home / 'userdata'
        boot = self.home / 'fake-wineboot'
        boot.write_text('#!/bin/bash\ntouch "$WINEPREFIX/system.reg"\n')
        boot.chmod(0o755)
        command = ('. ./install.sh; wine_binary() { printf "%s" "$MACTMNF_RUNTIME/bin/wine"; }; '
                   'wineboot_binary() { printf "%s" "$HOME/fake-wineboot"; }; initialize_prefix')
        result = self.shell(command, MACTMNF_USERDATA=str(userdata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(documents.resolve(), userdata)
        settings = userdata / 'TmForever/Config/Default.SystemConfig.Gbx'
        settings.write_text('personal settings')
        (self.prefix / '.mactmnf-initializing').touch()
        result = self.shell(command, MACTMNF_USERDATA=str(userdata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings.read_text(), 'personal settings')
        self.assertFalse((self.prefix / '.mactmnf-initializing').exists())
        self.assertFalse(self.log.exists())

    def fresh_wineboot_fixture(self):
        for name in ('Documents', 'Desktop', 'Downloads', 'Music', 'Pictures', 'Movies'):
            folder = self.home / name
            folder.mkdir()
            (folder / 'personal').write_text('host ' + name)
        boot = self.home / 'fake-wineboot'
        boot.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
prefix = Path(os.environ['WINEPREFIX'])
profile = prefix / 'drive_c/users/player'
profile.mkdir(parents=True, exist_ok=True)
for name in ('Documents', 'Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
    path = profile / name
    if not os.path.lexists(path):
        path.symlink_to(Path.home() / ('Movies' if name == 'Videos' else name))
alias = profile / 'My Documents'
if not os.path.lexists(alias):
    alias.symlink_to(profile / 'Documents')
(prefix / 'system.reg').touch()
raise SystemExit(int(os.environ.get('BOOT_EXIT', '0')))
''')
        boot.chmod(0o755)
        return ('. ./install.sh; wine_binary() { printf "%s" "$MACTMNF_RUNTIME/bin/wine"; }; '
                'wineboot_binary() { printf "%s" "$HOME/fake-wineboot"; }; initialize_prefix')

    def assert_fresh_profile_is_private(self):
        profile = self.prefix / 'drive_c/users/player'
        userdata = self.home / 'userdata'
        self.assertEqual((profile / 'Documents').resolve(), userdata)
        self.assertEqual((profile / 'My Documents').resolve(), userdata)
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
            self.assertTrue((profile / name).is_dir())
            self.assertFalse((profile / name).is_symlink())
        for name in ('Documents', 'Desktop', 'Downloads', 'Music', 'Pictures', 'Movies'):
            self.assertEqual((self.home / name / 'personal').read_text(), 'host ' + name)
        self.assertFalse((self.home / 'Documents/TmForever').exists())
        self.assertFalse((self.prefix / '.mactmnf-initializing').exists())
        self.assertFalse((self.prefix / '.mactmnf-profile-baseline').exists())

    def test_fresh_wine_default_links_redirect_without_touching_host_data(self):
        command = self.fresh_wineboot_fixture()
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_fresh_profile_is_private()
        self.assertEqual((self.home / 'userdata/TmForever/Config/Default.SystemConfig.Gbx').read_bytes(),
                         (self.runtime / 'config/Default.SystemConfig.Gbx').read_bytes())
        self.assertFalse(self.log.exists())

    def test_retry_after_wineboot_preserves_provenance_and_existing_userdata(self):
        command = self.fresh_wineboot_fixture()
        userdata = self.home / 'userdata'
        settings = userdata / 'TmForever/Config/Default.SystemConfig.Gbx'
        settings.parent.mkdir(parents=True)
        settings.write_text('my settings')
        replay = userdata / 'TmForever/replay'
        replay.write_text('my race')
        interrupted = command.replace('; initialize_prefix',
                                      '; python3() { if test "${2:-}" = setup-documents; then return 1; fi; '
                                      'command python3 "$@"; }; initialize_prefix')
        result = self.shell(interrupted, MACTMNF_USERDATA=str(userdata))
        self.assertNotEqual(result.returncode, 0)
        baseline = self.prefix / '.mactmnf-profile-baseline'
        self.assertEqual(json.loads(baseline.read_text())['profiles'], [])
        self.assertTrue((self.prefix / 'drive_c/users/player/Documents').is_symlink())
        result = self.shell(command, MACTMNF_USERDATA=str(userdata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_fresh_profile_is_private()
        self.assertEqual(settings.read_text(), 'my settings')
        self.assertEqual(replay.read_text(), 'my race')

    def test_preexisting_host_default_links_are_not_claimed_by_wineboot(self):
        command = self.fresh_wineboot_fixture()
        documents = self.documents_fixture()
        documents.rmdir()
        documents.symlink_to(self.home / 'Documents')
        desktop = documents.parent / 'Desktop'
        desktop.symlink_to(self.home / 'Desktop')
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Back up', result.stderr)
        self.assertEqual(documents.readlink(), self.home / 'Documents')
        self.assertEqual(desktop.readlink(), self.home / 'Desktop')
        self.assertFalse((self.home / 'userdata').exists())

    def test_custom_links_added_during_interruption_are_preserved(self):
        command = self.fresh_wineboot_fixture()
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'), BOOT_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        profile = self.prefix / 'drive_c/users/player'
        custom = self.home / 'custom'
        custom.mkdir()
        (custom / 'personal').write_text('keep')
        (profile / 'Documents').unlink()
        (profile / 'Documents').symlink_to(custom)
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((profile / 'Documents').readlink(), custom)
        self.assertEqual((custom / 'personal').read_text(), 'keep')
        self.assertFalse((self.home / 'userdata').exists())

    def test_fresh_setup_preserves_custom_and_internal_media_links(self):
        command = self.fresh_wineboot_fixture()
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'), BOOT_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        profile = self.prefix / 'drive_c/users/player'
        for name, target in (('Desktop', self.home / 'custom-desktop'), ('Music', self.prefix / 'music')):
            target.mkdir()
            (target / 'personal').write_text('keep')
            (profile / name).unlink()
            (profile / name).symlink_to(target)
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('Desktop', 'Music'):
            self.assertTrue((profile / name).is_symlink())
            self.assertEqual((profile / name / 'personal').read_text(), 'keep')

    def test_preexisting_media_links_survive_with_configured_userdata(self):
        command = self.fresh_wineboot_fixture()
        documents = self.documents_fixture()
        documents.rmdir()
        userdata = self.home / 'userdata'
        userdata.mkdir()
        documents.symlink_to(userdata)
        desktop = documents.parent / 'Desktop'
        desktop.symlink_to(self.home / 'Desktop')
        result = self.shell(command, MACTMNF_USERDATA=str(userdata))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(desktop.readlink(), self.home / 'Desktop')
        self.assertEqual((desktop / 'personal').read_text(), 'host Desktop')

    def test_fresh_account_home_defaults_can_be_broken_without_opening_targets(self):
        self.prefix.mkdir(parents=True)
        (self.prefix / '.mactmnf-prefix').write_text('tmnf:1\n')
        account_home = self.home / 'account-home-not-created'
        with mock.patch('lib.managed_paths.pwd.getpwuid', return_value=mock.Mock(pw_dir=str(account_home))):
            record_profile_baseline(self.prefix)
        documents = self.documents_fixture()
        documents.rmdir()
        documents.symlink_to(account_home / 'Documents')
        (documents.parent / 'My Documents').symlink_to('Documents')
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
            (documents.parent / name).symlink_to(account_home / name)
        setup_documents(self.prefix, self.home / 'userdata')
        self.assertEqual(documents.readlink(), self.home / 'userdata')
        self.assertEqual((documents.parent / 'My Documents').resolve(), self.home / 'userdata')
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
            self.assertTrue((documents.parent / name).is_dir())
            self.assertFalse((documents.parent / name).is_symlink())
        self.assertFalse(account_home.exists())

    def test_preexisting_broken_default_link_is_not_authorized_by_baseline(self):
        documents = self.documents_fixture()
        documents.rmdir()
        documents.symlink_to(self.home / 'Documents')
        record_profile_baseline(self.prefix)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            setup_documents(self.prefix, self.home / 'userdata')
        self.assertEqual(documents.readlink(), self.home / 'Documents')
        self.assertFalse((self.home / 'userdata').exists())

    def test_invalid_provenance_cannot_authorize_default_link_replacement(self):
        command = self.fresh_wineboot_fixture()
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'), BOOT_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        baseline = self.prefix / '.mactmnf-profile-baseline'
        baseline.write_text('{broken')
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.prefix / 'drive_c/users/player/Documents').readlink(), self.home / 'Documents')
        self.assertFalse((self.home / 'userdata').exists())
        baseline.unlink()
        baseline.symlink_to(self.home / 'Documents/personal')
        result = self.shell(command, MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.home / 'Documents/personal').read_text(), 'host Documents')

    def test_nonempty_documents_leaves_initialization_incomplete(self):
        documents = self.documents_fixture()
        (documents / 'replay').write_text('keep')
        result = self.shell('. ./install.sh; wine_binary() { printf /fake; }; '
                            'wineboot_binary() { printf /usr/bin/true; }; initialize_prefix',
                            MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Back up', result.stderr)
        self.assertTrue((self.prefix / '.mactmnf-initializing').exists())
        self.assertFalse((self.home / 'userdata').exists())
        self.assertEqual((documents / 'replay').read_text(), 'keep')

    def test_uninstall_cli_requires_owned_disjoint_paths_and_warns_about_registry(self):
        self.prefix.mkdir(parents=True)
        with self.assertRaises(ValueError):
            check_uninstall(self.prefix, self.home / 'userdata')
        (self.prefix / '.mactmnf-prefix').write_text('tmnf:1\n')
        with self.assertRaises(ValueError):
            check_uninstall(self.prefix, self.prefix / 'userdata')
        result = self.shell('python3 lib/managed_paths.py uninstall "$MACTMNF_PREFIX" "$HOME/userdata"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('registry', result.stderr)

    def test_nonempty_documents_preflight_does_not_change_other_profiles(self):
        empty = self.documents_fixture('first')
        documents = self.documents_fixture('second')
        (documents / 'personal').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'Back up'):
            setup_documents(self.prefix, self.home / 'userdata')
        self.assertFalse(empty.is_symlink())
        self.assertFalse((self.home / 'userdata').exists())
        self.assertEqual((documents / 'personal').read_text(), 'keep')

    def test_setup_preserves_outside_documents(self):
        documents = self.documents_fixture()
        documents.rmdir()
        outside = self.home / 'personal'
        outside.mkdir()
        documents.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'preserved'):
            setup_documents(self.prefix, self.home / 'userdata')
        self.assertEqual(documents.readlink(), outside)
        self.assertFalse((self.home / 'userdata').exists())

    def test_documents_link_failure_restores_empty_directory(self):
        documents = self.documents_fixture()
        with mock.patch.object(Path, 'symlink_to', side_effect=OSError('fixture failure')):
            with self.assertRaises(OSError):
                setup_documents(self.prefix, self.home / 'userdata')
        self.assertTrue(documents.is_dir())
        self.assertFalse(documents.is_symlink())

    def test_initialization_marker_failure_does_not_claim_prefix(self):
        result = self.shell('. ./install.sh; wine_binary() { printf /fake; }; '
                            'wineboot_binary() { printf /fake; }; touch() { return 1; }; initialize_prefix')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.prefix / '.mactmnf-prefix').exists())
        self.assertFalse(self.log.exists())

    def test_ownership_marker_write_failure_rolls_back_initializing_marker(self):
        result = self.shell('. ./install.sh; wine_binary() { builtin printf /fake; }; '
                            'wineboot_binary() { builtin printf /fake; }; '
                            'printf() { return 1; }; initialize_prefix')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.prefix / '.mactmnf-prefix').exists())
        self.assertFalse((self.prefix / '.mactmnf-initializing').exists())
        self.assertFalse(self.log.exists())

    def test_uninstall_rejects_broken_save_link_in_outside_documents(self):
        documents = self.documents_fixture()
        documents.rmdir()
        outside = self.home / 'outside'
        (outside / 'TmForever').mkdir(parents=True)
        (outside / 'TmForever/Replays').symlink_to(self.home / 'missing')
        documents.symlink_to(outside)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            check_uninstall(self.prefix, self.home / 'userdata')
        self.assertTrue((outside / 'TmForever/Replays').is_symlink())

    def test_failed_wineboot_keeps_retry_marker_without_seeding(self):
        result = self.shell('. ./install.sh; wine_binary() { printf /fake; }; '
                            'wineboot_binary() { printf /usr/bin/false; }; initialize_prefix',
                            MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.prefix / '.mactmnf-initializing').is_file())
        self.assertFalse((self.home / 'userdata').exists())

    def test_initialized_prefix_keeps_legacy_documents_arrangement(self):
        documents = self.documents_fixture()
        (documents / 'personal').write_text('keep')
        (self.prefix / 'system.reg').touch()
        result = self.shell('. ./install.sh; run_wine() { return 0; }; initialize_prefix',
                            MACTMNF_USERDATA=str(self.home / 'userdata'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(documents.is_symlink())
        self.assertFalse((self.home / 'userdata').exists())

    def test_installer_rejects_unknown_commands_before_mutation(self):
        result = self.shell('./install.sh unsupported')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown installation command', result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.log.exists())

    def test_app_identity_is_exact_and_preflight_preserves_launcher(self):
        contents = self.home / 'apps/TrackMania Nations Forever.app/Contents'
        contents.mkdir(parents=True)
        plist = contents / 'Info.plist'
        original = ('<?xml version="1.0"?><plist version="1.0"><dict>'
                    '<key>CFBundleIdentifier</key><string>local-mactmnf-launcher</string>'
                    '</dict></plist>')
        plist.write_text(original)
        support = self.home / 'support/launcher'
        support.mkdir(parents=True)
        (support / '.mactmnf-launcher').touch()
        (support / 'keep').write_text('installed launcher')
        result = self.shell('./bin/mactmnf app')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plist.read_text(), original)
        self.assertEqual((support / 'keep').read_text(), 'installed launcher')

    def test_app_destinations_cannot_alias_or_overlap(self):
        apps = self.home / 'apps'
        apps.mkdir()
        alias = self.home / 'apps-alias'
        alias.symlink_to(apps, target_is_directory=True)
        app = apps / 'Game.app'
        for settings in (str(app) + '/', str(alias / 'Game.app'), str(app / 'Settings.app')):
            with self.subTest(settings=settings):
                result = self.shell('./bin/mactmnf app', MACTMNF_APP=str(app),
                                    MACTMNF_SETTINGS_APP=settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(app.exists())
                self.assertFalse((self.home / 'support/launcher').exists())

    def test_cli_suite_ignores_hostile_inherited_installation_paths(self):
        unrelated = self.home / 'unrelated-launcher'
        unrelated.mkdir()
        (unrelated / '.mactmnf-launcher').touch()
        (unrelated / 'keep').write_text('keep')
        result = self.shell('./tests/test-cli.sh', timeout=45, MACTMNF_LAUNCHER=str(unrelated),
                            MACTMNF_SETTINGS_APP=str(self.home / 'unrelated-settings.app'),
                            MACTMNF_AUDIO='invalid', MACTMNF_WINDOW_SIZE='invalid',
                            ROSETTA_X87_PATH='/not-a-translator')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((unrelated / 'keep').read_text(), 'keep')
        self.assertFalse((self.home / 'unrelated-settings.app').exists())
