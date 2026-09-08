"""Opt-in numeric Wine proof, never using the default/user prefix.

Set MACTMNF_TEST_RUNTIME to an existing runtime, MACTMNF_TEST_X87_SIDECAR
to the freshly compiled native sidecar, and TMPDIR to an approved directory.
No runtime installation or build is performed. Both Windows architectures
run the integer-encoding reference probe in each translator mode.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MACTMNF_TEST_RUNTIME')
SIDECAR = os.environ.get('MACTMNF_TEST_X87_SIDECAR')


@unittest.skipUnless(RUNTIME and SIDECAR, 'opt-in runtime and freshly built sidecar required')
class X87RuntimeTests(unittest.TestCase):
    def test_windows_precision(self):
        runtime = Path(RUNTIME).resolve(strict=True)
        sidecar = Path(SIDECAR).resolve(strict=True)
        wine, server = runtime / 'bin/wine', runtime / 'bin/wineserver'
        for binary in (wine, server, sidecar):
            self.assertTrue(binary.is_file() and os.access(binary, os.X_OK), str(binary))
        parent = Path(os.environ['TMPDIR']).resolve(strict=True)
        temp = tempfile.TemporaryDirectory(prefix='mactmnf-x87-', dir=parent)
        scratch = Path(temp.name)
        home = scratch / 'home'
        home.mkdir()
        env = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': str(home), 'TMPDIR': str(scratch),
            'XDG_CONFIG_HOME': str(home / '.config'),
            'XDG_CACHE_HOME': str(home / '.cache'),
            'WINEPREFIX': str(scratch / 'prefix'),
            'WINEDEBUG': '-all', 'WINEDLLOVERRIDES': 'mscoree,mshtml=',
            'ROSETTA_X87_PATH': str(sidecar),
            'X87_ENABLE_FMA_CONTRACT': '0', 'X87_FAST_ROUND': '0',
            'X87_ENABLE_IR_SPLIT': '1', 'X87_ENABLE_IR_REMAT': '1',
        }
        def command(args, timeout=60, statuses=(0,)):
            result = subprocess.run([str(a) for a in args], env=env, cwd=scratch,
                                    capture_output=True, text=True, timeout=timeout)
            self.assertIn(result.returncode, statuses, result.stdout + result.stderr)
            return result.stdout
        try:
            print(command([sidecar, '--probe']))
            command([wine, 'wineboot', '-u'], 90)
            # Remove Wine's default link to the host root; expose only this fixture.
            devices = scratch / 'prefix/dosdevices'
            (devices / 'z:').unlink(missing_ok=True)
            (devices / 's:').symlink_to(scratch, target_is_directory=True)
            for arch in ('i686', 'x86_64'):
                compiler = shutil.which(arch + '-w64-mingw32-gcc')
                self.assertIsNotNone(compiler)
                executable = scratch / (arch + '.exe')
                command([compiler, '-O2', '-Wall', '-Wextra', '-Werror',
                         ROOT / 'tests/x87-precision.c', '-o', executable])
                for mode in ('default', 'direct', 'unfused', 'pressure'):
                    with self.subTest(arch=arch, mode=mode):
                        command([server, '-k'], statuses=(0, 1))
                        command([server, '-w'])
                        env.update(X87_DISABLE_X87_IR='1' if mode in ('direct', 'unfused') else '0',
                                   X87_DISABLE_ALL_FUSIONS='1' if mode == 'unfused' else '0')
                        env.pop('X87_FPR_POOL_LIMIT', None)
                        if mode == 'pressure':
                            env['X87_FPR_POOL_LIMIT'] = '5'
                        output = command([wine, 'S:\\' + executable.name])
                        print('{} {}\n{}'.format(arch, mode, output))
                        self.assertIn('88 cases, 0 failures', output)
        finally:
            try:
                command([server, '-k'], 15, statuses=(0, 1))
                command([server, '-w'], 15)
            except Exception:
                temp._finalizer.detach()
                print('Preserved scratch directory after shutdown failure: {}'.format(scratch))
                raise
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
