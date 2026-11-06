"""sha256_file in common.sh: hashlib path, shasum fallback, identical output."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def shell_hash(path, after_source='', **env):
    return subprocess.run(
        ['bash', '-euc', '. src/shell/lib/common.sh; ' + after_source + 'sha256_file "$1"', 'fixture', str(path)],
        cwd=ROOT, env={**os.environ, **env}, capture_output=True, text=True, check=False)


def shasum(path):
    return subprocess.run(['shasum', '-a', '256', str(path)], capture_output=True, text=True,
                          check=True).stdout.split()[0]


class Sha256FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sample(self, name, size):
        path = self.root / name
        path.write_bytes(os.urandom(size))
        return path

    def test_matches_shasum_byte_for_byte(self):
        # Sizes straddle the 1 MiB read chunk.
        for size in (1, 1000, 1024 * 1024 - 1, 1024 * 1024, 3 * 1024 * 1024 + 7):
            with self.subTest(size=size):
                path = self.sample('data%d' % size, size)
                result = shell_hash(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, shasum(path) + '\n')

    def test_empty_file(self):
        path = self.root / 'empty'
        path.write_bytes(b'')
        result = shell_hash(path)
        self.assertEqual(result.stdout, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n')

    def test_falls_back_to_shasum_without_python(self):
        path = self.sample('data', 4096)
        # common.sh needs Python to load, so make it unusable only afterwards.
        result = shell_hash(path, after_source='have() { return 1; }; ', TMNF_PYTHON='python3')
        self.assertEqual(result.stdout, shasum(path) + '\n')

    def test_module_prints_only_the_digest(self):
        path = self.sample('data', 2048)
        result = subprocess.run(['python3', '-m', 'tmnf.install.hashing', 'sha256', str(path)],
                                env={**os.environ, 'PYTHONPATH': str(ROOT / 'src/python')},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.stdout, shasum(path) + '\n')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
