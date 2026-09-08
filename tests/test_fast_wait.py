"""Compile the patched NtDelayExecution against real pipes, without building Wine.

Uses git objects from the prepared pinned source read-only. Applies every Wine
patch to pristine affected files in scratch space. Full ntdll/server builds and
both-architecture smoke-sync.sh runs remain a release gate, not this unit test.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PIN = '36b6a2cf679fb395f668a917b76537190e212d9c'
SOURCE = Path(os.environ.get('MACTMNF_WINE_SOURCE', ROOT / '.build/release/sources/wine'))


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class FastWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_dir():
            raise unittest.SkipTest('requires prepared pinned Wine source or MACTMNF_WINE_SOURCE')
        cls.scratch = tempfile.TemporaryDirectory(prefix='fast-wait-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.directory = Path(cls.scratch.name)
        pin = json.loads((ROOT / 'config/sources.json').read_text())['wine']['revision']
        assert pin == PIN, f'update source validation for new pin {pin}'
        patches = sorted((ROOT / 'patches/wine').glob('*.patch'))
        paths = set()
        for patch in patches:
            paths.update(re.findall(r'^--- a/(.+)$', patch.read_text(), re.M))
        for name in paths:
            original = subprocess.check_output(['git', '-C', str(SOURCE), 'show', f'{PIN}:{name}'])
            target = cls.directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
        for patch in patches:
            subprocess.run(['git', 'apply', str(patch)],
                           cwd=cls.directory, check=True, timeout=30)
        cls.sync = (cls.directory / 'dlls/ntdll/unix/sync.c').read_text()

    def test_compiled_client(self):
        compiler = shutil.which('cc')
        self.assertIsNotNone(compiler, 'requires a native C compiler')
        header = (self.directory / 'dlls/ntdll/unix/unix_private.h').read_text()
        virtual = (self.directory / 'dlls/ntdll/unix/virtual.c').read_text()
        field = re.search(r'#ifdef __APPLE__\n[^\n]*fast_wait_disabled[^\n]*\n#endif', header)[0]
        initialization = re.search(r'#ifdef __APPLE__\n[^\n]*fast_wait_disabled[^\n]*\n#endif', virtual)[0]
        delay = function(self.sync, 'NTSTATUS WINAPI NtDelayExecution(')
        harness = (ROOT / 'tests/fast-wait.c').read_text()
        harness = harness.replace('/* THREAD_FIELD */', field)
        harness = harness.replace('/* THREAD_INIT */', initialization)
        harness = harness.replace('/* CLIENT_FUNCTION */', delay)
        source = self.directory / 'client.c'
        source.write_text(harness)
        for darwin in (True, False):
            with self.subTest(darwin=darwin):
                executable = self.directory / f'client-{darwin}'
                subprocess.run([compiler, '-std=c11', '-D_DARWIN_C_SOURCE', '-D_POSIX_C_SOURCE=200809L',
                                '-D__APPLE__' if darwin else '-U__APPLE__',
                                '-Wall', '-Wextra', '-Werror', '-O2', str(source),
                                '-o', str(executable)], check=True, timeout=60)
                subprocess.run([str(executable)], check=True, timeout=30)

    def test_ordinary_apc_probe_builds_both_architectures(self):
        for architecture in ('i686', 'x86_64'):
            with self.subTest(architecture=architecture):
                compiler = shutil.which(f'{architecture}-w64-mingw32-gcc')
                self.assertIsNotNone(compiler, f'requires {architecture} MinGW compiler')
                subprocess.run([compiler, '-O2', '-Wall', '-Wextra', '-Werror',
                                str(ROOT / 'tests/alertable-wait.c'), '-o',
                                str(self.directory / f'apc-{architecture}.exe')],
                               check=True, timeout=60)

    def test_ordinary_wait_and_lifecycle_unchanged(self):
        original = subprocess.check_output(
            ['git', '-C', str(SOURCE), 'show', f'{PIN}:dlls/ntdll/unix/sync.c'], text=True)
        before = function(original, 'NTSTATUS WINAPI NtDelayExecution(')
        after = function(self.sync, 'NTSTATUS WINAPI NtDelayExecution(')
        marker = '    /* if alertable, we need to query the server */'
        self.assertEqual(before[before.index(marker):], after[after.index(marker):])
        self.assertEqual(function(original, 'static int get_inproc_alert_fd('),
                         function(self.sync, 'static int get_inproc_alert_fd('))
        # Patch 0005 must not take over descriptor cleanup or alter the protocol.
        patch = (ROOT / 'patches/wine/0005-ntdll-darwin-fast-alertable-yield.patch').read_text()
        self.assertEqual(set(re.findall(r'^--- a/(.+)$', patch, re.M)), {
            'dlls/ntdll/unix/sync.c', 'dlls/ntdll/unix/unix_private.h',
            'dlls/ntdll/unix/virtual.c', 'server/thread.c', 'server/thread.h'})


if __name__ == '__main__':
    unittest.main()
