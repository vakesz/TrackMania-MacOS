"""Optional native Windows integration test of the compiled WinMM probe.

Requires native Windows and MinGW-w64 compilers on PATH. Never launches Wine.
Run with: python3 -m unittest discover -s tests -p test_multimedia_timer.py -v
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'requires native Windows, not Wine')
class MultimediaTimerTests(unittest.TestCase):
    def compile_and_run(self, architecture):
        compiler = shutil.which(f'{architecture}-w64-mingw32-gcc')
        if compiler is None:
            self.skipTest(f'requires {architecture}-w64-mingw32-gcc')
        with tempfile.TemporaryDirectory(prefix='multimedia-timer-') as directory:
            executable = Path(directory) / f'multimedia-timer-{architecture}.exe'
            subprocess.run(
                [compiler, '-O2', '-Wall', '-Wextra', '-Werror',
                 str(ROOT / 'tests/multimedia-timer.c'), '-o', str(executable), '-lwinmm'],
                check=True, timeout=60)
            subprocess.run([str(executable)], check=True, timeout=60)

    def test_i686(self):
        self.compile_and_run('i686')

    def test_x86_64(self):
        self.compile_and_run('x86_64')


if __name__ == '__main__':
    unittest.main()
