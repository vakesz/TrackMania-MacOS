import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RuntimeInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mactmnf-release-test-', dir='/private/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / 'runtime'
        for directory in ('bin', 'lib/d9vk', 'lib/wine/i386-windows', 'config', 'licenses'):
            (self.bundle / directory).mkdir(parents=True)
        for name in ('wine', 'wineserver', 'x87sidecar'):
            executable = self.bundle / 'bin' / name
            executable.write_text('#!/bin/bash\nexit 0\n')
            executable.chmod(0o755)
        (self.bundle / '.mactmnf-runtime').write_text((ROOT / 'VERSION').read_text())
        (self.bundle / 'lib/d9vk/d3d9.dll').write_bytes(b'fixture')
        (self.bundle / 'config/dxvk.conf').write_text('fixture')
        (self.bundle / 'config/Default.SystemConfig.Gbx').write_bytes(b'fixture')
        for name in ('d3dx9_30.dll', 'd3dx9_43.dll'):
            (self.bundle / 'lib/wine/i386-windows' / name).write_bytes(b'fixture')
        manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.bundle)}\n'
                           for p in sorted(self.bundle.rglob('*')) if p.is_file())
        (self.bundle / 'SHA256SUMS').write_text(manifest)
        self.archive = self.root / 'runtime.tar.xz'
        self.destination = self.root / "support with spaces and 'quotes/runtime"

    def package(self):
        with tarfile.open(self.archive, 'w:xz') as tar:
            tar.add(self.bundle, arcname='runtime')

    def install(self, checksum=None):
        env = dict(os.environ, MACTMNF_RUNTIME=str(self.destination),
                   MACTMNF_CACHE=str(self.root / 'cache'), MACTMNF_RUNTIME_ARCHIVE=str(self.archive),
                   MACTMNF_RUNTIME_SHA256=checksum or hashlib.sha256(self.archive.read_bytes()).hexdigest())
        return subprocess.run(['bash', '-euc', '. lib/common.sh; . lib/runtime.sh; install_wine_runtime'],
                              cwd=ROOT, env=env, text=True, capture_output=True, check=False)

    def test_verified_archive_installs_outside_checkout(self):
        self.package()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.destination / 'lib/d9vk/d3d9.dll').read_bytes(), b'fixture')
        # An explicit install must never silently erase an existing runtime.
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.destination / 'bin/wine').exists())

    def test_wrong_archive_checksum_leaves_no_install(self):
        self.package()
        result = self.install('0' * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_changed_member_fails_file_manifest(self):
        (self.bundle / 'lib/d9vk/d3d9.dll').write_bytes(b'corrupt')
        self.package()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_archive_traversal_is_rejected(self):
        with tarfile.open(self.archive, 'w:xz') as tar:
            member = tarfile.TarInfo('runtime/../../escape')
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'escape').exists())

    def test_archive_cannot_write_through_an_external_symlink(self):
        target = self.root / 'unrelated'
        target.mkdir()
        (target / 'keep').write_bytes(b'original')
        with tarfile.open(self.archive, 'w:xz') as tar:
            link = tarfile.TarInfo('runtime/link')
            link.type = tarfile.SYMTYPE
            link.linkname = str(target)
            tar.addfile(link)
            member = tarfile.TarInfo('runtime/link/keep')
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / 'keep').read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
