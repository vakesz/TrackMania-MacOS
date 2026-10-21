import concurrent.futures
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_manifest', ROOT / 'src/python/tmnf/install/runtime_manifest.py')
runtime_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_manifest)


class RuntimeInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tmnf-release-test-',
                                                dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bundle = self.root / 'runtime'
        for directory in ('bin', 'lib/d9vk', 'lib/wine/i386-windows',
                          'lib/wine/x86_64-windows', 'libexec', 'config', 'licenses'):
            (self.bundle / directory).mkdir(parents=True)
        for name in ('wine', 'wineserver', 'x87sidecar'):
            executable = self.bundle / 'bin' / name
            executable.write_text('#!/bin/bash\nexit 0\n')
            executable.chmod(0o755)
        (self.bundle / 'bin/wineboot').symlink_to('wine')
        self.version = (ROOT / 'VERSION').read_text().strip()
        (self.bundle / '.tmnf-runtime').write_text(self.version + '\n')
        for name in runtime_manifest.REQUIRED_FILES + ('licenses/fixture.txt',):
            if name == '.tmnf-runtime':
                continue
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        (self.bundle / 'lib/wine/current').symlink_to('i386-windows', target_is_directory=True)
        (self.bundle / 'bin/wine-link').symlink_to('wine')
        self.write_manifest()
        self.archive = self.root / 'runtime.tar.xz'
        self.destination = self.home / "support with spaces and 'quotes/runtime"
        commands = self.root / 'commands'
        commands.mkdir()
        (commands / 'python3').symlink_to(sys.executable)
        curl = commands / 'curl'
        curl.write_text('#!/bin/bash\nprintf "Unexpected fixture network request\\n" >&2\nexit 97\n')
        curl.chmod(0o755)
        # Deliberately do not inherit HOME, TMNF_*, Wine, Python, or shell settings.
        self.env = {'HOME': str(self.home), 'PATH': str(commands) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                    'LC_ALL': 'C', 'TMPDIR': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1',
                    'TMNF_RUNTIME': str(self.destination),
                    'TMNF_CACHE': str(self.home / 'cache'),
                    'TMNF_APPS_DIR': str(self.home / 'Applications')}
        self.project = self.root / 'project'
        (self.project / 'src/shell/lib/install').mkdir(parents=True)
        (self.project / 'src/python/tmnf/install').mkdir(parents=True)
        (self.project / 'src/python/tmnf/guard').mkdir(parents=True)
        for package in ('src/python/tmnf', 'src/python/tmnf/install', 'src/python/tmnf/guard'):
            (self.project / package / '__init__.py').write_text('')
        shutil.copy2(ROOT / 'src/shell/lib/install/runtime.sh',
                     self.project / 'src/shell/lib/install/runtime.sh')
        shutil.copy2(ROOT / 'src/python/tmnf/install/runtime_manifest.py',
                     self.project / 'src/python/tmnf/install/runtime_manifest.py')
        shutil.copy2(ROOT / 'src/python/tmnf/guard/managed_paths.py',
                     self.project / 'src/python/tmnf/guard/managed_paths.py')

    def write_manifest(self, root=None):
        root = root or self.bundle
        manifest = ''.join('{}  {}\n'.format(hashlib.sha256(p.read_bytes()).hexdigest(),
                                            p.relative_to(root))
                           for p in sorted(root.rglob('*'))
                           if p.is_file() and not p.is_symlink() and p.name != 'SHA256SUMS')
        (root / 'SHA256SUMS').write_text(manifest)

    def package(self, extras=()):
        with tarfile.open(self.archive, 'w:xz') as tar:
            tar.add(self.bundle, arcname='runtime')
            for member in extras:
                tar.addfile(member, io.BytesIO(b'x' * member.size) if member.isreg() else None)

    def install(self, checksum=None, explicit=True, conditional=False, umask='022'):
        env = dict(self.env)
        if explicit:
            env.update(TMNF_RUNTIME_ARCHIVE=str(self.archive),
                       TMNF_RUNTIME_SHA256=checksum if checksum is not None else
                       hashlib.sha256(self.archive.read_bytes()).hexdigest())
        return subprocess.run(
            ['bash', '-euc', 'umask "$2"; . src/shell/lib/common.sh; TMNF_PROJECT_DIR="$1"; '
             '. "$TMNF_PROJECT_DIR/src/shell/lib/install/runtime.sh"; ' +
             ('if install_wine_runtime; then exit 0; else exit 1; fi' if conditional else
              'install_wine_runtime'), 'fixture', str(self.project), umask],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False)

    def assert_clean(self):
        self.assertEqual(list(self.destination.parent.glob('.runtime-install.*')), [])
        self.assertEqual(list(self.home.rglob('*.partial*')), [])

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.lexists(self.destination))
        self.assert_clean()

    def test_verified_archive_installs_and_reuses_identical_runtime(self):
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.destination.stat().st_ino
        self.assertEqual((self.destination / 'lib/d9vk/d3d9.dll').read_bytes(), b'fixture')
        self.assertTrue((self.destination / 'bin/wine-link').is_symlink())
        self.assertEqual(os.readlink(self.destination / 'bin/wineboot'), 'wine')
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.destination.stat().st_ino, before)
        self.assertFalse((self.destination / 'runtime').exists())
        self.assert_clean()

    def test_wrong_archive_checksum_leaves_no_install(self):
        self.package()
        self.assert_rejected(self.install('0' * 64))

    def test_archive_digest_must_be_hex(self):
        self.package()
        for digest in ('g' * 64, 'a' * 63, '', 'a' * 64 + '\n', '-' * 64):
            with self.subTest(digest=digest):
                self.assert_rejected(self.install(digest))

    def test_uppercase_archive_digest_is_accepted(self):
        self.package()
        result = self.install(hashlib.sha256(self.archive.read_bytes()).hexdigest().upper())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_member_fails_file_manifest(self):
        (self.bundle / 'lib/d9vk/d3d9.dll').write_bytes(b'corrupt')
        self.package()
        self.assert_rejected(self.install())

    def test_required_manifest_entry_cannot_be_omitted(self):
        path = self.bundle / 'SHA256SUMS'
        path.write_text(''.join(line for line in path.read_text().splitlines(True)
                                if not line.endswith('  lib/wine/x86_64-windows/wineboot.exe\n')))
        self.package()
        self.assert_rejected(self.install())

    def test_wineboot_loader_symlink_does_not_replace_required_pe_program(self):
        (self.bundle / 'lib/wine/x86_64-windows/wineboot.exe').unlink()
        self.write_manifest()
        self.assertEqual(os.readlink(self.bundle / 'bin/wineboot'), 'wine')
        self.assertTrue(os.access(self.bundle / 'bin/wineboot', os.X_OK))
        self.package()
        result = self.install()
        self.assert_rejected(result)
        self.assertIn('Missing required runtime file: lib/wine/x86_64-windows/wineboot.exe', result.stderr)

    def test_unlisted_extra_file_is_rejected(self):
        (self.bundle / 'extra').write_text('not in manifest')
        self.package()
        self.assert_rejected(self.install())

    def test_required_files_cannot_be_omitted_even_from_complete_manifest(self):
        for name in runtime_manifest.REQUIRED_FILES + runtime_manifest.EXECUTABLES:
            with self.subTest(name=name):
                path = self.bundle / name
                target = os.readlink(path) if path.is_symlink() else None
                content, mode = path.read_bytes(), path.stat().st_mode
                path.unlink()
                try:
                    self.write_manifest()
                    self.package()
                    self.assert_rejected(self.install())
                finally:
                    if target is not None:
                        path.symlink_to(target)
                    else:
                        path.write_bytes(content)
                        path.chmod(mode)
        self.write_manifest()

    def test_binary_must_be_executable(self):
        (self.bundle / 'bin/wineboot').chmod(0o644)
        self.package()
        self.assert_rejected(self.install())

    def test_session_pe_helper_does_not_need_unix_execute_permissions(self):
        helper = self.bundle / 'libexec/tmnf-session.exe'
        helper.chmod(0o644)
        self.assertFalse(os.access(helper, os.X_OK))
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.destination / 'libexec/tmnf-session.exe'
        self.assertEqual(installed.read_bytes(), b'fixture')
        self.assertFalse(os.access(installed, os.X_OK))

    def test_manifest_digest_names_and_duplicates_are_strict(self):
        path = self.bundle / 'SHA256SUMS'
        original = path.read_text()
        for manifest in ('z' * 64 + original[64:], original + original.splitlines(True)[0],
                         original + '0' * 64 + '  ../escape\n',
                         original + '0' * 64 + '  /absolute\n',
                         original + '0' * 64 + '  bin/../wine\n',
                         original + '0' * 64 + '  missing\n'):
            with self.subTest(manifest=manifest[-100:]):
                path.write_text(manifest)
                self.package()
                self.assert_rejected(self.install())

    def test_shipped_manifest_and_shasum_binary_format_are_accepted(self):
        (self.bundle / 'licenses/name with spaces.txt').write_text('fixture')
        self.write_manifest()
        path = self.bundle / 'SHA256SUMS'
        path.write_text(''.join(line[:64].upper() + ' *./' + line[66:]
                                for line in path.read_text().splitlines(True)))
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_archive_bad_names_are_rejected(self):
        for name in ('runtime/../../escape', '/runtime/escape', '../escape', 'other/file',
                     'runtime/./extra', 'runtime//extra', 'runtime/extra\nfile',
                     'runtime/extra\\file', 'runtime/bin/wine', 'runtime/BIN/extra'):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.size = 1
                self.package([member])
                self.assert_rejected(self.install())
                self.assertFalse((self.root / 'escape').exists())

    def test_archive_unsafe_links_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            for target in ('/tmp/outside', '../../escape', '../runtime/bin/wine',
                           'runtime/../../escape', 'bad\nname', 'bad\\name', 'missing'):
                with self.subTest(kind=kind, target=target):
                    link = tarfile.TarInfo('runtime/link')
                    link.type, link.linkname = kind, target
                    self.package([link])
                    self.assert_rejected(self.install())

    def test_archive_cannot_write_through_a_symlink(self):
        target = self.root / 'unrelated'
        target.mkdir()
        (target / 'keep').write_bytes(b'original')
        for link_target in (str(target), 'licenses'):
            with self.subTest(target=link_target):
                link = tarfile.TarInfo('runtime/link')
                link.type, link.linkname = tarfile.SYMTYPE, link_target
                member = tarfile.TarInfo('runtime/link/keep')
                member.size = 1
                self.package([link, member])
                self.assert_rejected(self.install())
                self.assertEqual((target / 'keep').read_bytes(), b'original')

    def test_archive_special_files_and_root_links_are_rejected(self):
        for kind, name in ((tarfile.FIFOTYPE, 'runtime/fifo'),
                           (tarfile.CHRTYPE, 'runtime/device'),
                           (tarfile.SYMTYPE, 'runtime')):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, 'licenses'
                self.package([member])
                self.assert_rejected(self.install())

    def test_internal_hard_links_are_supported(self):
        os.link(self.bundle / 'bin/wine', self.bundle / 'bin/wine-hardlink')
        self.write_manifest()
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.destination / 'bin/wine').stat().st_ino,
                         (self.destination / 'bin/wine-hardlink').stat().st_ino)

    def test_symlink_cycles_are_rejected(self):
        (self.bundle / 'cycle-a').symlink_to('cycle-b')
        (self.bundle / 'cycle-b').symlink_to('cycle-a')
        self.package()
        self.assert_rejected(self.install())

    def test_symlink_chain_cannot_escape_and_reenter_root(self):
        (self.bundle / 'licenses/up').symlink_to('..')
        (self.bundle / 'licenses/roundtrip').symlink_to('up/../runtime/bin/wine')
        self.package()
        self.assert_rejected(self.install())

    def test_safe_relative_symlink_chain_is_supported(self):
        (self.bundle / 'licenses/wine').symlink_to('../bin/wine-link')
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_archive_inaccessible_directories_are_rejected_and_cleaned(self):
        member = tarfile.TarInfo('runtime/inaccessible')
        member.type, member.mode = tarfile.DIRTYPE, 0o000
        self.package([member])
        self.assert_rejected(self.install())

    def test_invalid_archive_does_not_run_probes_in_a_shell_conditional(self):
        (self.bundle / 'bin/wine').write_text('#!/bin/bash\nexit 98\n')
        self.package()
        result = self.install(conditional=True)
        self.assert_rejected(result)
        self.assertIn('SHA-256 mismatch', result.stderr)
        self.assertNotIn('probe failed', result.stderr)

    def test_damaged_existing_runtime_is_never_repaired_or_removed(self):
        self.package()
        self.assertEqual(self.install().returncode, 0)
        path = self.destination / 'lib/d9vk/d3d9.dll'
        path.write_bytes(b'damaged')
        for explicit in (True, False):
            with self.subTest(explicit=explicit):
                self.assertNotEqual(self.install(explicit=explicit).returncode, 0)
                self.assertEqual(path.read_bytes(), b'damaged')
                self.assert_clean()

    def test_different_verified_archive_cannot_replace_runtime(self):
        self.package()
        self.assertEqual(self.install().returncode, 0)
        (self.bundle / 'lib/d9vk/d3d9.dll').write_bytes(b'new version')
        self.write_manifest()
        self.package()
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual((self.destination / 'lib/d9vk/d3d9.dll').read_bytes(), b'fixture')
        self.assert_clean()

    def test_explicit_archive_detects_changed_internal_link(self):
        self.package()
        self.assertEqual(self.install().returncode, 0)
        path = self.destination / 'bin/wine-link'
        path.unlink()
        path.symlink_to('wineserver')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(os.readlink(path), 'wineserver')
        self.assert_clean()

    def test_installed_external_link_is_rejected_without_archive(self):
        self.package()
        self.assertEqual(self.install().returncode, 0)
        path = self.destination / 'bin/wine-link'
        path.unlink()
        path.symlink_to(self.bundle / 'bin/wine')
        self.assertNotEqual(self.install(explicit=False).returncode, 0)
        self.assert_clean()

    def test_existing_nonowned_directory_is_preserved(self):
        self.package()
        self.destination.mkdir(parents=True)
        (self.destination / 'keep').write_text('mine')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual((self.destination / 'keep').read_text(), 'mine')
        self.assert_clean()

    def test_existing_root_symlink_is_rejected(self):
        self.package()
        self.destination.parent.mkdir(parents=True)
        for target in (self.bundle, self.root / 'missing'):
            with self.subTest(target=target):
                self.destination.symlink_to(target)
                self.assertNotEqual(self.install().returncode, 0)
                self.assertTrue(self.destination.is_symlink())
                self.assert_clean()
                self.destination.unlink()

    def test_existing_runtime_is_verified_without_archive(self):
        self.package()
        self.assertEqual(self.install().returncode, 0)
        self.archive.unlink()
        result = self.install(explicit=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_clean()

    def test_local_build_is_verified_and_copied_with_links(self):
        built = self.project / '.build/release/runtime'
        shutil.copytree(self.bundle, built, symlinks=True)
        result = self.install(explicit=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.destination / 'bin/wine-link').is_symlink())
        self.assert_clean()

    def test_local_install_preserves_modes_for_archive_reuse_under_restrictive_umask(self):
        built = self.project / '.build/release/runtime'
        shutil.copytree(self.bundle, built, symlinks=True)
        self.package()
        result = self.install(explicit=False, umask='077')
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.destination.stat().st_ino
        result = self.install(umask='077')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.destination.stat().st_ino, before)
        self.assertEqual(runtime_manifest.verify_runtime(self.destination, self.version),
                         runtime_manifest.verify_runtime(self.bundle, self.version))
        self.assert_clean()

    def test_release_checksum_fetch_has_connection_and_stall_timeouts(self):
        curl = self.root / 'commands/curl'
        curl.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >"$HOME/curl-arguments"\nexit 97\n')
        self.assert_rejected(self.install(explicit=False))
        arguments = (self.home / 'curl-arguments').read_text().splitlines()
        for option, value in (('--connect-timeout', '20'), ('--speed-limit', '1024'),
                              ('--speed-time', '60')):
            with self.subTest(option=option):
                self.assertIn(option, arguments)
                self.assertEqual(arguments[arguments.index(option) + 1], value)
        self.assertTrue(arguments[-1].endswith('.tar.xz.sha256'))

    def test_damaged_local_build_is_rejected_without_downloading(self):
        built = self.project / '.build/release/runtime'
        shutil.copytree(self.bundle, built, symlinks=True)
        (built / 'lib/d9vk/d3d9.dll').write_bytes(b'damaged')
        self.assert_rejected(self.install(explicit=False))

    def test_probe_failure_cleans_only_its_own_staging_directory(self):
        (self.bundle / 'bin/x87sidecar').write_text('#!/bin/bash\nexit 1\n')
        self.write_manifest()
        self.package()
        other = self.destination.parent / '.runtime-install.other'
        other.mkdir(parents=True)
        (other / 'keep').write_text('another install')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob('.runtime-install.*')), [other])
        self.assertEqual((other / 'keep').read_text(), 'another install')

    def test_concurrent_identical_installs_do_not_nest_or_replace(self):
        self.package()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.install(), range(4)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.destination / 'runtime').exists())
        runtime_manifest.verify_runtime(self.destination, self.version)
        self.assert_clean()

    def test_publication_race_preserves_even_an_empty_destination(self):
        self.destination.parent.mkdir(parents=True)

        def race(path):
            self.destination.mkdir()
            return False

        with mock.patch.object(runtime_manifest.os.path, 'lexists', side_effect=race):
            with self.assertRaises(ValueError):
                runtime_manifest.publish_runtime(self.bundle, self.destination, self.version)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertTrue((self.bundle / 'bin/wine').exists())


if __name__ == '__main__':
    unittest.main()
