import concurrent.futures
import contextlib
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tmnf.build import runtime_builder as build
from tmnf.install.runtime_manifest import REQUIRED_FILES


ROOT = Path(__file__).resolve().parents[1]


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-build-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        release = self.root / '.build/release'
        overrides = dict(ROOT=self.root, BUILD=release, SOURCES=release / 'sources',
                         WORK=release / 'work', DEPS=release / 'work/deps',
                         RUNTIME=release / 'runtime', DIST=self.root / '.build/dist',
                         LOG=release / 'logs/build.log', VERSION='test', JOBS=3,
                         LOCK={'wine': {'git': 'fixture', 'revision': 'a'},
                               'moltenvk': {'url': 'fixture', 'sha256': 'b'}},
                         ENV={'MACOSX_DEPLOYMENT_TARGET': '14.0'},
                         FILE_PREFIX_MAP=f'-ffile-prefix-map={self.root}=/tmnf')
        patcher = mock.patch.multiple(build, **overrides)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.no_commands = mock.patch.object(build, 'run', side_effect=AssertionError('Unexpected external command'))
        self.no_commands.start()
        self.addCleanup(self.no_commands.stop)
        self.original_path_check = build.verify_no_build_paths
        patcher = mock.patch.object(build, 'verify_no_build_paths')
        patcher.start()
        self.addCleanup(patcher.stop)
        for entry in build.PROJECT_ENTRIES:
            path = self.root / entry
            if entry in ('config', 'patches', 'src', 'tests', 'docs', 'licenses'):
                path.mkdir()
            else:
                path.write_text('fixture\n')
        for name in build.LOADED_PYTHON_INPUTS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        (self.root / 'src/c').mkdir(exist_ok=True)
        (self.root / 'src/c/session.c').write_text('fixture supervisor\n')
        (self.root / 'src/shell/cli').mkdir(parents=True)
        (self.root / 'src/shell/cli/install.sh').write_text('fixture installer\n')
        (self.root / 'docs/building.md').write_text('fixture documentation\n')
        (self.root / 'VERSION').write_text('test\n')
        (self.root / 'config/sources.json').write_text(json.dumps(build.LOCK))
        (self.root / 'config/settings.json').write_text('{}\n')
        patcher = mock.patch.object(build, 'LOADED_PYTHON_INPUTS',
                                    {name: build.digest(self.root / name) for name in build.LOADED_PYTHON_INPUTS})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.make_sources()
        (build.RUNTIME / 'bin').mkdir(parents=True)
        for name in ('wine', 'wineserver', 'x87sidecar'):
            (build.RUNTIME / 'bin' / name).write_bytes(b'fixture executable')
            (build.RUNTIME / 'bin' / name).chmod(0o755)
        (build.RUNTIME / 'bin/wineboot').symlink_to('wine')
        (build.RUNTIME / 'bin/wine-link').symlink_to('wine')
        for name in REQUIRED_FILES + ('licenses/fixture.txt',):
            path = build.RUNTIME / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture payload')
        (build.RUNTIME / '.tmnf-runtime').write_text('test\n')
        self.manifest()
        self.complete = build.BUILD / 'build-inputs.json'

    def make_sources(self):
        for name in build.LOCK:
            source = build.SOURCES / name
            source.mkdir(parents=True, exist_ok=True)
            if name == 'moltenvk':
                (source / 'External').mkdir(exist_ok=True)
            (source / 'source.c').write_text('original\n')
            (source / '.mactmnf-source.json').write_text(json.dumps(build.prepared_identity(name), sort_keys=True))

    def manifest(self):
        (build.RUNTIME / 'SHA256SUMS').write_text(build.runtime_manifest(build.inventory(build.RUNTIME)))

    def complete_build(self):
        build.write_json(self.complete, {'schema': 2, 'inputs': build.build_inputs(),
                                        'toolchain': {'fixture': 'old'},
                                        'sources': build.source_snapshot(),
                                        'runtime': build.verified_runtime()})

    def main(self, *args, jobs='3'):
        with mock.patch.object(sys, 'argv', ['build.py', *args]), \
                mock.patch.dict(os.environ, TMNF_JOBS=jobs), \
                contextlib.redirect_stdout(io.StringIO()):
            build.main()

    def actions(self):
        return mock.patch.multiple(build, dependencies=mock.DEFAULT, wine=mock.DEFAULT,
                                   d9vk=mock.DEFAULT, sidecar=mock.DEFAULT, session=mock.DEFAULT,
                                   relocate=mock.DEFAULT,
                                   toolchain_identity=mock.Mock(return_value={'fixture': 'new'}))

    def test_inputs_include_root_license_and_recursive_config(self):
        original = build.build_inputs()
        self.assertIn('src/python/tmnf/install/runtime_manifest.py', original)
        (self.root / 'src/python/tmnf/install/runtime_manifest.py').write_text('changed verifier')
        self.assertNotEqual(original, build.build_inputs())
        original = build.build_inputs()
        (self.root / 'LICENSE').write_text('new license')
        self.assertNotEqual(original, build.build_inputs())
        (self.root / 'config/nested').mkdir()
        nested = self.root / 'config/nested/input.json'
        nested.write_text('one')
        before = build.build_inputs()
        self.assertIn('config/nested/input.json', before)
        nested.write_text('two')
        self.assertNotEqual(before, build.build_inputs())
        nested.unlink()
        self.assertNotIn('config/nested/input.json', build.build_inputs())

    def test_prepared_identity_binds_ordered_patch_paths_and_hashes(self):
        patches = self.root / 'patches/wine'
        patches.mkdir()
        for name in ('0002.patch', '0001.patch'):
            (patches / name).write_text(name)
        identity = build.prepared_identity('wine')
        self.assertEqual(identity['patches'], [[f'patches/wine/{name}', build.digest(patches / name)]
                                               for name in ('0001.patch', '0002.patch')])
        shipped = self.root / 'upstream/wine'
        shipped.mkdir(parents=True)
        (shipped / 'source.c').write_text('bundled')
        for change in ('edit', 'remove', 'rename', 'add', 'upstream'):
            with self.subTest(change=change):
                shutil.rmtree(patches)
                patches.mkdir()
                (patches / '0001.patch').write_text('original patch')
                self.make_sources()
                source = build.SOURCES / 'wine'
                (source / 'source.c').write_text('local edit')
                patch = patches / '0001.patch'
                if change == 'edit':
                    patch.write_text('edited')
                elif change == 'remove':
                    patch.unlink()
                elif change == 'rename':
                    patch.rename(patches / '0003.patch')
                elif change == 'add':
                    (patches / '0002.patch').write_text('added')
                else:
                    build.LOCK['wine']['revision'] = 'changed'
                with mock.patch.object(build, 'apply_patches') as apply, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(build.fetch('wine'), source)
                apply.assert_called_once()
                self.assertEqual((source / 'source.c').read_text(), 'bundled')
                self.assertEqual(json.loads((source / '.mactmnf-source.json').read_text()),
                                 build.prepared_identity('wine'))

    def test_recursive_native_sources_bind_inputs_and_completed_build(self):
        nested = self.root / 'src/nested/protocol.h'
        nested.parent.mkdir()
        nested.write_text('original header')
        for path in (self.root / 'src/c/session.c', nested):
            with self.subTest(path=path):
                before = build.build_inputs()
                self.assertIn(str(path.relative_to(self.root)), before)
                self.complete_build()
                old_time = path.stat().st_mtime_ns
                path.write_text('edited native source')
                os.utime(path, ns=(old_time, old_time))
                self.assertNotEqual(before, build.build_inputs())
                with self.assertRaisesRegex(RuntimeError, 'Build inputs changed'):
                    build.package()
        before = build.build_inputs()
        nested.unlink()
        self.assertNotEqual(before, build.build_inputs())
        self.assertNotIn('src/nested/protocol.h', build.build_inputs())

    def test_old_and_missing_source_stamps_reprepare_the_tree(self):
        source = build.SOURCES / 'wine'
        stamp = source / '.mactmnf-source.json'
        shipped = self.root / 'upstream/wine'
        shipped.mkdir(parents=True)
        (shipped / 'source.c').write_text('bundled')
        for text in (json.dumps(build.LOCK['wine'], sort_keys=True), 'broken', None):
            with self.subTest(stamp=text):
                (source / 'source.c').write_text('stale')
                (source / 'leftover.o').write_text('stale output')
                if text is None:
                    stamp.unlink(missing_ok=True)
                else:
                    stamp.write_text(text)
                with mock.patch.object(build, 'apply_patches') as apply, \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(build.fetch('wine'), source)
                apply.assert_called_once_with('wine', mock.ANY)
                self.assertIn('Re-preparing wine', output.getvalue())
                self.assertEqual((source / 'source.c').read_text(), 'bundled')
                self.assertFalse((source / 'leftover.o').exists())
                self.assertEqual(stamp.read_text(), json.dumps(build.prepared_identity('wine'), sort_keys=True))

    def test_matching_source_stamp_preserves_edits_without_reapplying(self):
        source = build.SOURCES / 'wine'
        (source / 'source.c').write_text('local edit')
        with mock.patch.object(build, 'apply_patches') as apply:
            self.assertEqual(build.fetch('wine'), source)
        apply.assert_not_called()
        self.assertEqual((source / 'source.c').read_text(), 'local edit')

    def test_interrupted_archive_unpack_leftovers_are_cleared_before_repreparing(self):
        source = build.SOURCES / 'wine'
        shutil.rmtree(source)
        shipped = self.root / 'upstream/wine'
        shipped.mkdir(parents=True)
        (shipped / 'source.c').write_text('bundled')
        for suffix in ('.partial', '.unpacked'):
            leftover = build.SOURCES / ('wine' + suffix)
            leftover.mkdir()
            (leftover / 'stale.c').write_text('interrupted extraction')
        with mock.patch.object(build, 'apply_patches'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.fetch('wine'), source)
        self.assertEqual((source / 'source.c').read_text(), 'bundled')
        self.assertEqual(sorted(path.name for path in build.SOURCES.iterdir()), ['moltenvk', 'wine'])

    def test_bundled_source_changes_reprepare_the_tree(self):
        source = build.SOURCES / 'wine'
        shutil.rmtree(source)
        shipped = self.root / 'upstream/wine'
        shipped.mkdir(parents=True)
        (shipped / 'source.c').write_text('bundled')
        with mock.patch.object(build, 'apply_patches') as apply:
            build.fetch('wine')
        apply.assert_called_once()
        stamp = json.loads((source / '.mactmnf-source.json').read_text())
        self.assertEqual(stamp, build.prepared_identity('wine'))
        (source / 'source.c').write_text('local edit')
        (shipped / 'source.c').write_text('new upstream')
        with mock.patch.object(build, 'apply_patches') as apply, contextlib.redirect_stdout(io.StringIO()):
            build.fetch('wine')
        apply.assert_called_once()
        self.assertEqual((source / 'source.c').read_text(), 'new upstream')

    def test_every_mutating_action_invalidates_completion_before_failure(self):
        names = {'deps': 'dependencies', 'wine': 'wine', 'd9vk': 'd9vk',
                 'sidecar': 'sidecar', 'session': 'session', 'bundle': 'relocate'}
        for only in ('fetch', *names, None):
            with self.subTest(only=only), self.actions() as actions:
                self.complete_build()

                def fail(*args):
                    self.assertFalse(self.complete.exists())
                    with (build.BUILD / '.mactmnf.lock').open() as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    raise RuntimeError('fixture failure')

                if only == 'fetch':
                    with mock.patch.object(build, 'fetch', side_effect=fail), \
                            self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                        self.main('--only', only)
                else:
                    actions[names[only] if only else 'dependencies'].side_effect = fail
                    with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                        self.main(*(['--only', only] if only else []))
                self.assertFalse(self.complete.exists())
                with (build.BUILD / '.mactmnf.lock').open() as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_full_build_cleans_deps_but_keeps_configure_paths_and_work(self):
        with self.actions() as actions:
            self.main()
            self.assertTrue(self.complete.exists())
            cached = build.WORK / 'wine/Makefile'
            cached.parent.mkdir()
            cached.write_text(f'prefix={build.DEPS}')
            stale = build.DEPS / 'lib/stale.dylib'
            stale.write_text('must not ship')

            def deps():
                self.assertFalse(self.complete.exists())
                self.assertFalse(stale.exists())
                self.assertTrue((build.DEPS / 'lib').is_dir())
                self.assertEqual(cached.read_text(), f'prefix={build.DEPS}')

            actions['dependencies'].side_effect = deps
            self.main()
            build.verify_completed()

    def test_toolchain_and_source_changes_discard_compiler_work(self):
        with self.actions():
            self.main()
            for change in ('toolchain', 'source', 'inputs', 'session'):
                with self.subTest(change=change):
                    stale = build.WORK / 'stale-object'
                    stale.write_text('old compiler output')
                    if change == 'toolchain':
                        build.toolchain_identity.return_value = {'fixture': 'different'}
                    elif change == 'source':
                        source = build.SOURCES / 'wine/source.c'
                        old_time = source.stat().st_mtime_ns
                        source.write_text('new code with old timestamp')
                        os.utime(source, ns=(old_time, old_time))
                    elif change == 'session':
                        (self.root / 'src/c/session.c').write_text('changed supervisor')
                    else:
                        (self.root / 'LICENSE').write_text('changed')
                    self.main()
                    self.assertFalse(stale.exists())
                    build.verify_completed()

    def test_toolchain_identity_detects_versions_paths_and_sdk(self):
        with mock.patch.object(build, 'run', return_value='version 1') as run, \
                mock.patch.object(build.shutil, 'which', return_value='/fixture/tool') as which:
            original = build.toolchain_identity()
            run.return_value = 'version 2'
            self.assertNotEqual(original, build.toolchain_identity())
            run.return_value = 'version 1'
            which.return_value = '/other/tool'
            self.assertNotEqual(original, build.toolchain_identity())
            self.assertIn(mock.call('xcrun', '--sdk', 'macosx', '--show-sdk-version', capture=True), run.call_args_list)

    def test_failed_wine_retry_preserves_partial_work_only_for_matching_inputs(self):
        for change in ('none', 'toolchain', 'source', 'inputs', 'project'):
            for error in (RuntimeError('compiler failed'), subprocess.TimeoutExpired('make', 1800)):
                with self.subTest(change=change, error=type(error).__name__), self.actions() as actions:
                    partial = build.WORK / 'wine/partial-object'
                    stamp = build.WORK / '.mactmnf-work.json'

                    def fail():
                        self.assertTrue(stamp.is_file())
                        self.assertFalse(self.complete.exists())
                        partial.parent.mkdir(exist_ok=True)
                        partial.write_text('valid partial compiler work')
                        raise error

                    actions['wine'].side_effect = fail
                    with self.assertRaises(type(error)):
                        self.main()
                    identity = stamp.read_bytes()
                    self.assertFalse(self.complete.exists())
                    with self.assertRaisesRegex(RuntimeError, 'completed build'):
                        build.package()
                    if change == 'toolchain':
                        build.toolchain_identity.return_value = {'fixture': 'changed'}
                    elif change != 'none':
                        path = {'source': build.SOURCES / 'wine/source.c',
                                'inputs': self.root / 'src/c/session.c',
                                'project': self.root / 'README.md'}[change]
                        path.write_text(path.read_text() + 'changed\n')

                    def resume():
                        self.assertEqual(partial.exists(), change in ('none', 'project'))
                        if change in ('none', 'project'):
                            self.assertEqual(partial.read_text(), 'valid partial compiler work')
                            self.assertEqual(stamp.read_bytes(), identity)

                    actions['wine'].side_effect = resume
                    self.main()
                    build.verify_completed()
                    if change == 'project':
                        build.package()
                        with tarfile.open(build.DIST / 'tmnf-test-source.tar.xz') as archive:
                            self.assertEqual(archive.extractfile('tmnf/README.md').read(), path.read_bytes())

    def test_project_edit_during_build_blocks_completion_but_preserves_compiler_cache(self):
        for action in ('dependencies', 'wine'):
            for relative in ('README.md', 'docs/building.md'):
                with self.subTest(action=action, relative=relative), self.actions() as actions:
                    path = self.root / relative
                    partial = build.WORK / 'partial-object'
                    stamp = build.WORK / '.mactmnf-work.json'
                    checkpoint = []

                    def edit():
                        checkpoint.append(stamp.read_bytes())
                        partial.write_text('valid compiler output')
                        path.write_text(path.read_text() + 'concurrent project edit\n')

                    actions[action].side_effect = edit
                    with self.assertRaisesRegex(RuntimeError, 'Sources changed during'):
                        self.main()
                    self.assertFalse(self.complete.exists())
                    self.assertEqual(stamp.read_bytes(), checkpoint[0])
                    self.assertEqual(set(json.loads(stamp.read_text())), {'inputs', 'toolchain', 'sources'})
                    with self.assertRaisesRegex(RuntimeError, 'completed build'):
                        build.package()
                    actions[action].side_effect = lambda: self.assertEqual(partial.read_text(), 'valid compiler output')
                    self.main()
                    self.assertEqual(stamp.read_bytes(), checkpoint[0])
                    complete = build.verify_completed()
                    self.assertEqual(complete['sources']['project'][relative][2], build.digest(path))
                    build.package()
                    with tarfile.open(build.DIST / 'tmnf-test-source.tar.xz') as archive:
                        self.assertEqual(archive.extractfile('tmnf/' + relative).read(), path.read_bytes())

    def test_dependency_materialization_checkpoint_survives_wine_timeout(self):
        header = build.SOURCES / 'moltenvk/External/dependency/header.h'
        stamp = build.WORK / '.mactmnf-work.json'
        partial = build.WORK / 'partial-object'

        def materialize():
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text('materialized source')

        def fail():
            self.assertEqual(json.loads(stamp.read_text())['sources'], build.source_inventory())
            partial.write_text('partial Wine output')
            raise subprocess.TimeoutExpired('make', 1800)

        with self.actions() as actions:
            actions['dependencies'].side_effect = materialize
            actions['wine'].side_effect = fail
            with self.assertRaises(subprocess.TimeoutExpired):
                self.main()
            identity = stamp.read_bytes()
            self.assertFalse(self.complete.exists())
            with self.assertRaisesRegex(RuntimeError, 'completed build'):
                build.package()
            actions['wine'].side_effect = lambda: self.assertEqual(partial.read_text(), 'partial Wine output')
            self.main()
            self.assertEqual(stamp.read_bytes(), identity)
            build.verify_completed()

    def test_failed_actions_cannot_checkpoint_concurrent_edits(self):
        for action, path in (('wine', build.SOURCES / 'wine/source.c'),
                             ('session', self.root / 'src/c/session.c'),
                             ('dependencies', build.SOURCES / 'moltenvk/External/header.h')):
            with self.subTest(action=action), self.actions() as actions:
                only = 'session' if action == 'session' else None
                args = ['--only', only] if only else []
                work = build.WORK / 'session' if only else build.WORK
                stamp = work / '.mactmnf-work.json'
                partial = work / 'partial-object'
                checkpoint = []

                def fail():
                    checkpoint.append(stamp.read_bytes())
                    partial.write_text('output from changing inputs')
                    path.write_text('concurrent edit')
                    raise RuntimeError('interrupted')

                actions[action].side_effect = fail
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    self.main(*args)
                self.assertEqual(stamp.read_bytes(), checkpoint[0])
                self.assertFalse(self.complete.exists())
                with self.assertRaisesRegex(RuntimeError, 'completed build'):
                    build.package()
                actions[action].side_effect = lambda: self.assertFalse(partial.exists())
                self.main(*args)
                self.assertNotEqual(stamp.read_bytes(), checkpoint[0])

    def test_unexpected_dependency_drift_is_not_checkpointed_and_retry_clears_work(self):
        for relative in ('wine/source.c', 'moltenvk/source.c'):
            with self.subTest(relative=relative), self.actions() as actions:
                source = build.SOURCES / relative
                partial = build.WORK / 'partial-object'
                before = build.source_inventory()

                def drift():
                    partial.write_text('untrusted output')
                    source.write_text(source.read_text() + 'unexpected edit\n')

                actions['dependencies'].side_effect = drift
                with self.assertRaisesRegex(RuntimeError, 'Sources changed during dependency compilation'):
                    self.main()
                self.assertEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'], before)
                self.assertFalse(self.complete.exists())
                actions['dependencies'].side_effect = lambda: self.assertFalse(partial.exists())
                self.main()
                build.verify_completed()

    def test_session_toolchain_only_probes_64_bit_mingw(self):
        with mock.patch.object(build, 'run', return_value='gcc fixture') as run, \
                mock.patch.object(build.shutil, 'which', return_value='/fixture/gcc'):
            identity = build.toolchain_identity('session')
        run.assert_called_once_with('x86_64-w64-mingw32-gcc', '--version', capture=True)
        self.assertEqual(identity, {'tools': {'x86_64-w64-mingw32-gcc':
                                             {'path': '/fixture/gcc', 'version': 'gcc fixture'}}})

    def test_session_compiles_unicode_64_bit_helper_and_copies_to_runtime(self):
        binary = build.WORK / 'session/tmnf-session.exe'

        def compile(*args, **kwargs):
            self.assertTrue(binary.parent.is_dir())
            binary.write_bytes(b'MZ fixture supervisor')

        with mock.patch.object(build, 'run', side_effect=compile) as run:
            build.session()
        run.assert_called_once_with(
            'x86_64-w64-mingw32-gcc', '-municode', '-O2', '-Wall', '-Wextra', '-Werror',
            build.FILE_PREFIX_MAP, self.root / 'src/c/session.c', '-o', binary, cwd=binary.parent,
            title='session: x86_64-w64-mingw32-gcc session.c')
        self.assertEqual((build.RUNTIME / 'libexec/tmnf-session.exe').read_bytes(), binary.read_bytes())

    def test_failed_session_compile_does_not_copy_stale_output(self):
        binary = build.WORK / 'session/tmnf-session.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'stale compiler output')
        installed = build.RUNTIME / 'libexec/tmnf-session.exe'
        original = installed.read_bytes()
        with mock.patch.object(build, 'run', side_effect=RuntimeError('compiler failed')):
            with self.assertRaisesRegex(RuntimeError, 'compiler failed'):
                build.session()
        self.assertEqual(installed.read_bytes(), original)

    def test_session_diagnostic_needs_no_upstream_sources_or_other_actions(self):
        shutil.rmtree(build.SOURCES)
        with self.actions() as actions, mock.patch.object(build, 'fetch') as fetch:
            for args in (('--only', 'session'), ('--only=session',)):
                with self.subTest(args=args):
                    self.main(*args)
                    actions['session'].assert_called_once_with()
                    actions['session'].reset_mock()
            fetch.assert_not_called()
            for name in ('dependencies', 'wine', 'd9vk', 'sidecar', 'relocate'):
                actions[name].assert_not_called()
            build.toolchain_identity.assert_called_with('session')
        self.assertFalse(self.complete.exists())
        self.assertFalse(build.DEPS.exists())
        self.assertEqual(list(build.SOURCES.iterdir()), [])

    def test_full_pipeline_builds_session_before_bundling(self):
        with self.actions() as actions:
            ordered = mock.Mock()
            for name, action in actions.items():
                ordered.attach_mock(action, name)
            self.main()
        self.assertEqual(ordered.mock_calls, [mock.call.dependencies(), mock.call.wine(),
                         mock.call.d9vk(), mock.call.sidecar(), mock.call.session(), mock.call.relocate()])

    def test_session_diagnostic_preserves_full_work_and_next_full_build_identity(self):
        toolchain_identity = build.toolchain_identity
        with self.actions(), mock.patch.object(build, 'run', return_value='fixture version'), \
                mock.patch.object(build.shutil, 'which', return_value='/fixture/compiler'):
            build.toolchain_identity.side_effect = toolchain_identity
            self.main()
            full_stamp = build.WORK / '.mactmnf-work.json'
            original_stamp = full_stamp.read_bytes()
            cached = build.WORK / 'wine/Makefile'
            cached.parent.mkdir()
            cached.write_text(f'prefix={build.DEPS}')
            dependency = build.DEPS / 'lib/cached.dylib'
            dependency.write_text('cached dependency')
            helper = build.WORK / 'session/stale-object'
            helper.parent.mkdir()
            helper.write_text('unstamped helper output')
            before = build.inventory(build.WORK, lambda path: path.parts[0] == 'session')

            self.main('--only', 'session')

            self.assertFalse(self.complete.exists())
            self.assertFalse(helper.exists())
            self.assertEqual(build.inventory(build.WORK, lambda path: path.parts[0] == 'session'), before)
            self.assertEqual(full_stamp.read_bytes(), original_stamp)
            with mock.patch.object(build.shutil, 'rmtree', wraps=shutil.rmtree) as remove:
                self.main()
            # Full builds still clean the installed dependency prefix, not compiler work.
            remove.assert_called_once_with(build.DEPS)
            self.assertEqual(cached.read_text(), f'prefix={build.DEPS}')
            self.assertEqual(full_stamp.read_bytes(), original_stamp)
            build.verify_completed()

    def test_session_identity_changes_only_discard_session_work(self):
        with self.actions():
            self.main()
            full_stamp = build.WORK / '.mactmnf-work.json'
            original_stamp = full_stamp.read_bytes()
            self.main('--only', 'session')
            stamp = build.WORK / 'session/.mactmnf-work.json'
            cached = build.WORK / 'session/cached-object'
            cached.write_text('cached helper output')
            self.main('--only', 'session')
            self.assertTrue(cached.exists())
            before = build.inventory(build.WORK, lambda path: path.parts[0] == 'session')
            for change in ('source', 'compiler'):
                with self.subTest(change=change):
                    original_identity = json.loads(stamp.read_text())
                    cached.write_text('stale helper output')
                    if change == 'source':
                        (self.root / 'src/c/session.c').write_text('changed supervisor')
                    else:
                        build.toolchain_identity.return_value = {'fixture': 'changed compiler'}
                    self.main('--only', 'session')
                    identity = json.loads(stamp.read_text())
                    self.assertNotEqual(identity, original_identity)
                    self.assertEqual(identity['inputs']['src/c/session.c'],
                                     build.digest(self.root / 'src/c/session.c'))
                    self.assertEqual(identity['toolchain'], build.toolchain_identity.return_value)
                    self.assertFalse(cached.exists())
                    self.assertFalse(self.complete.exists())
                    self.assertEqual(full_stamp.read_bytes(), original_stamp)
                    self.assertEqual(build.inventory(build.WORK, lambda path: path.parts[0] == 'session'), before)

    def test_failed_session_diagnostic_preserves_cache_not_completion(self):
        with self.actions() as actions:
            self.main()
            self.main('--only', 'session')
            before = build.inventory(build.WORK, lambda path: path.parts[0] == 'session')
            self.complete_build()
            stamp = build.WORK / 'session/.mactmnf-work.json'
            identity = stamp.read_bytes()
            partial = build.WORK / 'session/partial-object'

            def fail():
                self.assertEqual(stamp.read_bytes(), identity)
                partial.write_text('partial helper')
                raise RuntimeError('compiler failed')

            actions['session'].side_effect = fail
            with self.assertRaisesRegex(RuntimeError, 'compiler failed'):
                self.main('--only', 'session')
            self.assertFalse(self.complete.exists())
            self.assertEqual(stamp.read_bytes(), identity)
            self.assertEqual(build.inventory(build.WORK, lambda path: path.parts[0] == 'session'), before)
            with self.assertRaisesRegex(RuntimeError, 'completed build'):
                build.package()
            actions['session'].side_effect = lambda: self.assertEqual(partial.read_text(), 'partial helper')
            self.main('--only', 'session')
            self.assertFalse(self.complete.exists())

    def test_session_source_edit_during_compilation_prevents_completion(self):
        with self.actions() as actions:
            actions['session'].side_effect = lambda: (self.root / 'src/c/session.c').write_text('concurrent edit')
            with self.assertRaisesRegex(RuntimeError, 'Build inputs changed during the build'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertNotEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['inputs'],
                            build.build_inputs())

    def test_dependencies_clear_external_cache_without_removing_sources(self):
        source = build.SOURCES / 'moltenvk'
        cache = source / 'External/build'
        cache.mkdir()
        (cache / 'old-object').write_text('stale')
        (build.DEPS / 'lib').mkdir(parents=True)
        library = source / 'Package/Release/MoltenVK/dylib/macOS/libMoltenVK.dylib'
        library.parent.mkdir(parents=True)
        library.write_text('fixture library')

        def run(*args, **kwargs):
            self.assertFalse(cache.exists())
            self.assertEqual((source / 'source.c').read_text(), 'original\n')

        with mock.patch.object(build, 'autotools'), mock.patch.object(build, 'run', side_effect=run):
            build.dependencies()
        self.assertTrue((build.DEPS / 'lib/libMoltenVK.dylib').is_file())

    def test_successful_partial_build_does_not_restore_completion(self):
        self.complete_build()
        with self.actions():
            self.main('--only', 'wine')
        self.assertFalse(self.complete.exists())

    def test_source_edit_after_wine_prevents_completion(self):
        source = build.SOURCES / 'wine/source.c'
        old_time = source.stat().st_mtime_ns

        def edit():
            source.write_text('edited after Wine compiled')
            os.utime(source, ns=(old_time, old_time))

        with self.actions() as actions:
            actions['d9vk'].side_effect = edit
            with self.assertRaisesRegex(RuntimeError, 'Sources changed during compilation'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertNotEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'],
                            build.source_inventory())

    def test_source_edit_during_final_verification_prevents_completion(self):
        verify = build.verified_runtime

        def edit():
            (build.SOURCES / 'wine/source.c').write_text('edited during final verification')
            return verify()

        with self.actions(), mock.patch.object(build, 'verified_runtime', side_effect=edit):
            with self.assertRaisesRegex(RuntimeError, 'Sources changed during compilation'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertNotEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'],
                            build.source_inventory())

    def test_full_build_requires_shared_runtime_contract_before_completion(self):
        with self.actions() as actions:
            actions['sidecar'].side_effect = lambda: (build.RUNTIME / 'lib/wine/x86_64-windows/wineboot.exe').unlink()
            with self.assertRaisesRegex(RuntimeError, 'Missing required runtime file'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'],
                         build.source_inventory())
        with self.assertRaisesRegex(RuntimeError, 'completed build'):
            build.package()

    def test_materialized_moltenvk_sources_are_bound_before_wine(self):
        header = build.SOURCES / 'moltenvk/External/dependency/header.h'

        def materialize():
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text('materialized source')

        def generate():
            output = build.SOURCES / 'moltenvk/Package/library'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'generated')

        with self.actions() as actions:
            actions['dependencies'].side_effect = materialize
            actions['wine'].side_effect = generate
            self.main()
            complete = build.verify_completed()
            self.assertEqual(complete['sources']['upstream']['moltenvk']['External/dependency/header.h'][2],
                             build.digest(header))
            actions['sidecar'].side_effect = lambda: header.write_text('edited after Wine compiled')
            with self.assertRaisesRegex(RuntimeError, 'Sources changed during compilation'):
                self.main()
        self.assertFalse(self.complete.exists())

    def test_fresh_moltenvk_tree_materializes_external_and_workspace_dirs(self):
        # A fresh tarball extraction has no External yet, and xcodebuild adds workspace dirs.
        source = build.SOURCES / 'moltenvk'
        shutil.rmtree(source / 'External')
        (source / 'MoltenVKPackaging.xcodeproj').mkdir()
        (source / 'Demos/Demos.xcworkspace').mkdir(parents=True)
        (source / 'Demos/Demos.xcworkspace/contents.xcworkspacedata').write_text('shipped')
        (source / '.mactmnf-source.json').write_text(json.dumps(build.prepared_identity('moltenvk'), sort_keys=True))

        def materialize():
            (source / 'External/Vulkan-Headers').mkdir(parents=True)
            (source / 'External/Vulkan-Headers/vulkan.h').write_text('materialized')
            (source / 'MoltenVKPackaging.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/configuration').mkdir(parents=True)

        with self.actions() as actions:
            actions['dependencies'].side_effect = materialize
            self.main()
            complete = build.verify_completed()
        upstream = complete['sources']['upstream']['moltenvk']
        self.assertIn('External/Vulkan-Headers/vulkan.h', upstream)
        self.assertIn('Demos/Demos.xcworkspace/contents.xcworkspacedata', upstream)
        self.assertFalse(any(key.startswith('MoltenVKPackaging.xcodeproj/project.xcworkspace') for key in upstream))
        self.assertIn('MoltenVKPackaging.xcodeproj', upstream)


    def test_libunistring_symbol_scratch_is_not_a_source_input(self):
        build.LOCK['libunistring'] = {'url': 'fixture', 'sha256': 'c'}
        (self.root / 'config/sources.json').write_text(json.dumps(build.LOCK))
        self.make_sources()
        library = build.SOURCES / 'libunistring/lib'
        library.mkdir()
        symbols = library / 'libunistring.sym'
        symbols.write_text('original symbols\n')
        for component, path in (('wine', 'lib/libunistring.sym-t1'),
                                ('libunistring', 'other/libunistring.sym-t1'),
                                ('libunistring', 'lib/libunistring.sym-t2'),
                                ('libunistring', 'lib/Makefile.in')):
            self.assertFalse(build.source_excluded(component, Path(path)))
        with self.actions() as actions:
            actions['dependencies'].side_effect = lambda: (library / 'libunistring.sym-t1').touch()
            self.main()
            build.verify_completed()
            archive = self.root / 'source.tar.xz'
            build.source_archive(archive)
            with tarfile.open(archive) as tar:
                self.assertNotIn('tmnf/upstream/libunistring/lib/libunistring.sym-t1', tar.getnames())
                self.assertIn('tmnf/upstream/libunistring/lib/libunistring.sym', tar.getnames())
            actions['dependencies'].side_effect = lambda: symbols.write_text('changed symbols\n')
            with self.assertRaisesRegex(RuntimeError, 'upstream/libunistring/lib/libunistring.sym'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertNotEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'],
                            build.source_inventory())

    def test_project_snapshot_ignores_build_outputs_and_timestamps_not_root_mode(self):
        before = build.source_snapshot()
        (build.BUILD / 'new-output').write_text('generated')
        os.utime(self.root / 'README.md', (1, 1))
        os.utime(self.root, (1, 1))
        self.assertEqual(before, build.source_snapshot())
        self.root.chmod(0o755)
        changes = build.inventory_changes(before, build.source_snapshot())
        self.assertEqual(len(changes), 1)
        self.assertIn('project/.: changed', changes[0])

    def test_dependency_drift_reports_paths_and_inventory_values(self):
        with self.actions() as actions:
            def drift():
                (build.SOURCES / 'wine/source.c').unlink()
                (build.SOURCES / 'wine/new.c').write_text('new')
                (self.root / 'README.md').chmod(0o600)
                self.root.chmod(0o755)

            actions['dependencies'].side_effect = drift
            with self.assertRaises(RuntimeError) as error:
                self.main()
        message = str(error.exception)
        for path in ('project/.', 'project/README.md', 'upstream/wine/source.c', 'upstream/wine/new.c'):
            self.assertIn(path, message)
        self.assertIn('added', message)
        self.assertIn('removed', message)
        self.assertIn('changed', message)
        self.assertFalse(self.complete.exists())
        self.assertNotEqual(json.loads((build.WORK / '.mactmnf-work.json').read_text())['sources'],
                            build.source_inventory())

    def test_invalid_jobs_fail_cleanly_without_mutation(self):
        self.complete_build()
        for jobs in ('', '0', '-1', '1.5', 'abc', '+3', ' 3', '3\n'):
            with self.subTest(jobs=jobs[:20]), contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as exit:
                    self.main(jobs=jobs)
                self.assertEqual(exit.exception.code, 2)
                self.assertIn('TMNF_JOBS must be a positive integer', error.getvalue())
                self.assertNotIn('Traceback', error.getvalue())
                self.assertTrue(self.complete.exists())
        with self.actions():
            self.main('--only', 'wine', jobs='003')
        self.assertEqual(build.JOBS, 3)

    def test_jobs_default_to_every_core(self):
        with self.actions(), mock.patch.dict(os.environ), contextlib.redirect_stdout(io.StringIO()) as output:
            os.environ.pop('TMNF_JOBS', None)
            with mock.patch.object(sys, 'argv', ['build.py', '--only', 'wine']):
                build.main()
        self.assertEqual(build.JOBS, os.cpu_count())
        self.assertIn(f'Using {os.cpu_count()} compiler jobs', output.getvalue())

    def test_help_and_package_dispatch_without_build_setup_or_jobs(self):
        self.complete_build()
        with mock.patch.object(build, 'package') as package, \
                mock.patch.object(build, 'toolchain_identity', side_effect=AssertionError('toolchain probe')):
            self.main('--only', 'package', jobs='invalid')
            package.assert_called_once_with()
            with self.assertRaises(SystemExit) as exit:
                self.main('--help', jobs='invalid')
            self.assertEqual(exit.exception.code, 0)
        self.assertTrue(self.complete.exists())
        self.assertFalse(build.WORK.exists())

    def test_help_does_not_create_lock_or_build_directories(self):
        shutil.rmtree(build.BUILD)
        with self.assertRaises(SystemExit) as exit:
            self.main('--help')
        self.assertEqual(exit.exception.code, 0)
        self.assertFalse(build.BUILD.exists())

    def test_missing_legacy_and_malformed_completion_fail_actionably(self):
        for value in (None, json.dumps(build.build_inputs()), '{', '[]'):
            with self.subTest(value=value):
                if value is not None:
                    self.complete.write_text(value)
                with self.assertRaisesRegex(RuntimeError, 'make build'):
                    build.package()
                self.assertFalse(build.DIST.exists())

    def test_runtime_drift_fails_even_with_regenerated_manifest(self):
        for change in ('content', 'extra', 'missing', 'mode', 'link', 'manifest', 'version'):
            with self.subTest(change=change):
                (build.RUNTIME / 'bin/wine').chmod(0o755)
                self.manifest()
                self.complete_build()
                binary = build.RUNTIME / 'bin/wine'
                if change == 'content':
                    binary.write_bytes(b'changed')
                elif change == 'extra':
                    (build.RUNTIME / 'extra').write_bytes(b'unexpected')
                elif change == 'missing':
                    (build.RUNTIME / 'extra').unlink()
                elif change == 'mode':
                    binary.chmod(0o644)
                elif change == 'link':
                    link = build.RUNTIME / 'bin/wine-link'
                    link.unlink()
                    link.symlink_to('../.tmnf-runtime')
                elif change == 'manifest':
                    (build.RUNTIME / 'SHA256SUMS').write_text('invalid')
                else:
                    (build.RUNTIME / '.tmnf-runtime').write_text('wrong\n')
                if change not in ('manifest', 'version'):
                    self.manifest()
                with self.assertRaisesRegex(RuntimeError, 'Runtime'):
                    build.package()
                self.assertFalse(build.DIST.exists())

    def test_manifest_must_cover_all_regular_files_including_nested_sums(self):
        self.complete_build()
        (build.RUNTIME / 'bin/SHA256SUMS').write_text('nested file')
        with self.assertRaisesRegex(RuntimeError, 'SHA256SUMS'):
            build.verified_runtime()
        self.manifest()
        self.assertIn('bin/SHA256SUMS', (build.RUNTIME / 'SHA256SUMS').read_text())
        build.verified_runtime()
        (build.RUNTIME / 'bin/wine').write_text('tampered')
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            build.verified_runtime()

    def test_runtime_links_cannot_escape_or_dangle(self):
        link = build.RUNTIME / 'bin/wine-link'
        for target in ('missing', str(self.root / 'LICENSE'), str(build.RUNTIME / 'bin/wine')):
            with self.subTest(target=target):
                link.unlink()
                link.symlink_to(target)
                with self.assertRaisesRegex(RuntimeError, 'link'):
                    build.verified_runtime()

    def test_shared_verifier_preserves_completion_inventory_format(self):
        with mock.patch.object(build, 'verify_runtime', wraps=build.verify_runtime) as verify:
            self.assertEqual(build.verified_runtime(), build.inventory(build.RUNTIME))
        verify.assert_called_once_with(build.RUNTIME, build.VERSION)

    def test_matching_hashes_and_stamp_cannot_approve_incomplete_runtime(self):
        for name in ('bin/wineserver', 'bin/wineboot', 'bin/x87sidecar',
                     'lib/wine/x86_64-windows/wineboot.exe', 'lib/d9vk/d3d9.dll',
                     'lib/libMoltenVK.dylib', 'lib/wine/i386-windows/d3dcompiler_43.dll',
                     'libexec/tmnf-session.exe'):
            with self.subTest(missing=name):
                self.complete_build()
                path = build.RUNTIME / name
                saved = path.with_name(path.name + '.saved')
                path.rename(saved)
                try:
                    self.manifest()
                    complete = json.loads(self.complete.read_text())
                    complete['runtime'] = build.inventory(build.RUNTIME)
                    build.write_json(self.complete, complete)
                    with self.assertRaisesRegex(RuntimeError, 'Missing required runtime file'):
                        build.package()
                finally:
                    saved.rename(path)
                    self.manifest()

    def test_nonexecutable_runtime_is_rejected_with_matching_hashes(self):
        (build.RUNTIME / 'bin/wineserver').chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'not executable'):
            build.verified_runtime()

    def test_symlinked_inventory_roots_are_rejected(self):
        self.complete_build()
        for root in (build.RUNTIME, build.SOURCES / 'wine'):
            with self.subTest(root=root):
                moved = root.with_name(root.name + '-moved')
                root.rename(moved)
                root.symlink_to(moved, target_is_directory=True)
                with self.assertRaisesRegex(RuntimeError, 'real directory'):
                    build.package()
                root.unlink()
                moved.rename(root)

    def test_source_and_project_edits_invalidate_correspondence(self):
        for relative in ('wine/source.c', 'moltenvk/External/dependency/source.c'):
            path = build.SOURCES / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original')
            self.complete_build()
            path.write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'Corresponding source'):
                build.package()
        self.complete_build()
        (self.root / 'README.md').write_text('new documentation')
        with self.assertRaisesRegex(RuntimeError, 'Corresponding source'):
            build.package()

    def test_source_archive_uses_the_snapshot_exclusions(self):
        nested = self.root / 'src/nested/protocol.h'
        nested.parent.mkdir()
        nested.write_text('fixture header')
        self.complete_build()
        excluded = ('wine/.git/config', 'moltenvk/build/object', 'moltenvk/Package/library',
                    'moltenvk/External/build/cache', 'moltenvk/DerivedData/object',
                    'moltenvk/xcodebuild.log', 'moltenvk/dependenciesbuild.log',
                    'wine/__pycache__/script.pyc')
        for relative in excluded:
            path = build.SOURCES / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('generated')
        cache = self.root / 'src/python/tmnf/build/__pycache__'
        cache.mkdir()
        (cache / 'build.pyc').write_bytes(b'cache')
        build.verify_completed()
        archive = self.root / 'source.tar.xz'
        build.source_archive(archive)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            for relative in excluded:
                self.assertNotIn('tmnf/upstream/' + relative, names)
            self.assertNotIn('tmnf/src/python/tmnf/build/__pycache__', names)
            self.assertNotIn('tmnf/upstream/wine/.mactmnf-source.json', names)
            for relative in ('src/c/session.c', 'src/nested/protocol.h'):
                self.assertEqual(tar.extractfile('tmnf/' + relative).read(),
                                 (self.root / relative).read_bytes())
            for name, contents in build.source_inventory().items():
                for relative in contents:
                    self.assertIn(str(Path('tmnf/upstream') / name / relative), names)

    def test_packaging_needs_no_toolchain_and_publishes_valid_checksums(self):
        self.complete_build()
        original_complete = self.complete.read_bytes()
        stamps = {path: path.read_bytes() for path in build.SOURCES.glob('*/.mactmnf-source.json')}
        with mock.patch.object(build, 'toolchain_identity', side_effect=AssertionError('toolchain probe')), \
                mock.patch.object(build, 'fetch', side_effect=AssertionError('source mutation')):
            build.package()
        self.assertEqual(len(list(build.DIST.iterdir())), 4)
        for kind in ('source', 'runtime'):
            archive = build.DIST / f'tmnf-test-{kind}.tar.xz'
            self.assertEqual(archive.with_name(archive.name + '.sha256').read_text(),
                             f'{build.digest(archive)}  {archive.name}\n')
            with tarfile.open(archive) as tar:
                self.assertTrue(tar.getmembers())
        self.assertTrue(self.complete.exists())
        self.assertEqual(original_complete, self.complete.read_bytes())
        self.assertEqual(stamps, {path: path.read_bytes() for path in stamps})

    def test_build_waits_for_packaging_before_invalidating_completion(self):
        self.complete_build()
        original = self.complete.read_bytes()
        packaging = threading.Event()
        waiting = threading.Event()
        proceed = threading.Event()
        archive = build.source_archive
        flock = fcntl.flock

        def pause_archive(path):
            packaging.set()
            if not proceed.wait(5):
                raise RuntimeError('test packaging timed out')
            archive(path)

        def acquire(fd, operation):
            if packaging.is_set():
                waiting.set()
            flock(fd, operation)

        with mock.patch.object(build, 'source_archive', side_effect=pause_archive), \
                mock.patch.object(build.fcntl, 'flock', side_effect=acquire), \
                mock.patch.object(build, 'fetch', wraps=build.fetch) as fetch, \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            package = pool.submit(build.package)
            try:
                self.assertTrue(packaging.wait(5))
                pending_build = pool.submit(self.main, '--only', 'fetch')
                self.assertTrue(waiting.wait(5))
                self.assertEqual(self.complete.read_bytes(), original)
                fetch.assert_not_called()
                self.assertFalse(pending_build.done())
            finally:
                proceed.set()
            package.result(timeout=5)
            pending_build.result(timeout=5)
        self.assertFalse(self.complete.exists())

    def test_locked_actions_reload_metadata_that_may_change_while_waiting(self):
        expected = json.loads((self.root / 'config/sources.json').read_text())
        with self.actions():
            build.LOCK = {'stale': {}}
            build.VERSION = 'stale'
            self.main()
        self.assertEqual(build.LOCK, expected)
        self.assertEqual(build.VERSION, 'test')
        build.LOCK = {'stale': {}}
        build.VERSION = 'stale'
        build.package()
        self.assertEqual(build.LOCK, expected)
        self.assertEqual(build.VERSION, 'test')

    def test_queued_actions_reject_changed_python_inputs_before_mutation(self):
        self.complete_build()
        flock = fcntl.flock
        for only in (None, 'wine', 'session', 'fetch', 'package'):
            for name in build.LOADED_PYTHON_INPUTS:
                with self.subTest(only=only, changed=name):
                    path = self.root / name
                    original = path.read_bytes()
                    waiting = threading.Event()

                    def acquire(fd, operation):
                        waiting.set()
                        flock(fd, operation)

                    with (build.BUILD / '.mactmnf.lock').open('a') as owner:
                        flock(owner, fcntl.LOCK_EX)
                        before = build.inventory(build.BUILD)
                        with self.actions(), mock.patch.object(build.fcntl, 'flock', side_effect=acquire), \
                                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            pending = pool.submit(self.main, *(['--only', only] if only else []))
                            try:
                                self.assertTrue(waiting.wait(5))
                                self.assertFalse(pending.done())
                                path.write_bytes(original + b'# edited while queued\n')
                            finally:
                                flock(owner, fcntl.LOCK_UN)
                            try:
                                with self.assertRaisesRegex(RuntimeError, 'Loaded Python input changed') as error:
                                    pending.result(timeout=5)
                                self.assertIn(name, str(error.exception))
                                self.assertIn('Restart src/shell/tools/build-runtime.sh', str(error.exception))
                            finally:
                                path.write_bytes(original)
                    self.assertEqual(build.inventory(build.BUILD), before)
                    self.assertFalse(build.DIST.exists())

    def test_python_edit_during_source_preparation_cannot_be_recorded_as_loaded_code(self):
        fetch = build.fetch

        def edit(name):
            source = fetch(name)
            (self.root / 'src/python/tmnf/build/game_profile.py').write_text('edited after acquiring the lock')
            return source

        with self.actions(), mock.patch.object(build, 'fetch', side_effect=edit):
            with self.assertRaisesRegex(RuntimeError, 'Build inputs changed during the build'):
                self.main()
        self.assertFalse(self.complete.exists())
        self.assertFalse((build.WORK / '.mactmnf-work.json').exists())

    def test_packages_cannot_interleave_archive_and_checksum_publication(self):
        self.complete_build()
        publishing = threading.Event()
        waiting = threading.Event()
        proceed = threading.Event()
        publications = []
        replace = Path.replace
        flock = fcntl.flock

        def publish(path, target):
            publications.append(threading.get_ident())
            if path.name == 'tmnf-test-runtime.tar.xz.sha256' and not publishing.is_set():
                publishing.set()
                if not proceed.wait(5):
                    raise RuntimeError('test publication timed out')
            return replace(path, target)

        def acquire(fd, operation):
            if publishing.is_set():
                waiting.set()
            flock(fd, operation)

        with mock.patch.object(Path, 'replace', publish), \
                mock.patch.object(build.fcntl, 'flock', side_effect=acquire), \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(build.package)
            try:
                self.assertTrue(publishing.wait(5))
                second = pool.submit(build.package)
                self.assertTrue(waiting.wait(5))
                self.assertEqual(len(publications), 2)
                self.assertFalse(second.done())
            finally:
                proceed.set()
            first.result(timeout=5)
            second.result(timeout=5)
        self.assertEqual(len(publications), 8)
        self.assertEqual(publications[:4], [publications[0]] * 4)
        self.assertEqual(publications[4:], [publications[4]] * 4)
        self.assertNotEqual(publications[0], publications[4])
        for archive in build.DIST.glob('*.tar.xz'):
            self.assertEqual(archive.with_name(archive.name + '.sha256').read_text(),
                             f'{build.digest(archive)}  {archive.name}\n')

    def test_failed_archive_keeps_previous_release_and_cleans_staging(self):
        self.complete_build()
        build.package()
        original = build.inventory(build.DIST)
        with mock.patch.object(build, 'source_archive', side_effect=RuntimeError('archive failed')):
            with self.assertRaisesRegex(RuntimeError, 'archive failed'):
                build.package()
        self.assertEqual(original, build.inventory(build.DIST))

    def test_edits_during_packaging_prevent_publication(self):
        self.complete_build()
        archive = build.source_archive

        def edit_during_archive(path):
            archive(path)
            (build.SOURCES / 'wine/source.c').write_text('concurrent edit')

        with mock.patch.object(build, 'source_archive', side_effect=edit_during_archive):
            with self.assertRaisesRegex(RuntimeError, 'Corresponding source'):
                build.package()
        self.assertEqual(list(build.DIST.iterdir()), [])

    def test_publication_failure_cannot_leave_an_old_checksum_for_new_bytes(self):
        self.complete_build()
        build.package()
        checksum = build.DIST / 'tmnf-test-runtime.tar.xz.sha256'
        checksum.write_text('stale checksum')
        replace = Path.replace

        def fail_checksum(path, target):
            if path.name.endswith('.sha256'):
                raise OSError('checksum publication failed')
            return replace(path, target)

        with mock.patch.object(Path, 'replace', fail_checksum):
            with self.assertRaisesRegex(OSError, 'checksum publication failed'):
                build.package()
        self.assertFalse(checksum.exists())
        self.assertFalse(any(path.is_dir() for path in build.DIST.iterdir()))

    def test_new_completion_during_packaging_prevents_publication(self):
        self.complete_build()
        archive = build.source_archive

        def complete_during_archive(path):
            archive(path)
            (build.SOURCES / 'wine/source.c').write_text('new completed source')
            self.complete_build()

        with mock.patch.object(build, 'source_archive', side_effect=complete_during_archive):
            with self.assertRaisesRegex(RuntimeError, 'Completed build changed'):
                build.package()
        self.assertEqual(list(build.DIST.iterdir()), [])

    def test_json_write_failure_preserves_previous_stamp_and_removes_temp(self):
        self.complete_build()
        original = self.complete.read_bytes()
        entries = sorted(build.BUILD.iterdir())
        with mock.patch.object(Path, 'replace', side_effect=OSError('rename failed')):
            with self.assertRaisesRegex(OSError, 'rename failed'):
                build.write_json(self.complete, {'new': True})
        self.assertEqual(self.complete.read_bytes(), original)
        self.assertEqual(sorted(build.BUILD.iterdir()), entries)

    def test_build_path_scan_fails_closed(self):
        # Call the real function while keeping grep itself mocked.
        verify = self.original_path_check
        for code, output, succeeds in ((1, '', True), (0, '/runtime/file\n', False),
                                       (0, '', False), (2, '', False), (-9, '', False)):
            with self.subTest(code=code, output=output):
                result = subprocess.CompletedProcess([], code, output, 'fixture error')
                with mock.patch.object(build.subprocess, 'run', return_value=result):
                    if succeeds:
                        verify()
                    else:
                        with self.assertRaises(RuntimeError):
                            verify()


class BuildShellTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-build-shell-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'src/shell/tools').mkdir(parents=True)
        (self.root / 'src/python/tmnf').mkdir(parents=True)
        shutil.copy2(ROOT / 'src/shell/tools/build-runtime.sh', self.root / 'src/shell/tools/build-runtime.sh')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('brew', 'xcodebuild', 'uname'):
            path = self.bin / name
            path.write_text('#!/bin/bash\necho "forbidden bootstrap: $0" >&2\nexit 90\n')
            path.chmod(0o755)
        python = self.bin / 'python3.14'
        python.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        python.chmod(0o755)
        self.complete = self.root / '.build/release/build-inputs.json'
        self.complete.parent.mkdir(parents=True)
        self.complete.write_text('complete')

    def run_wrapper(self, *args, jobs='3'):
        return subprocess.run(['/bin/bash', str(self.root / 'src/shell/tools/build-runtime.sh'), *args],
                              env=dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin', TMNF_JOBS=jobs),
                              text=True, capture_output=True, check=False)

    def test_help_and_package_skip_bootstrap_and_keep_completion(self):
        for args in (('--help',), ('-h',), ('--only', 'package'), ('--only=package',),
                     ('--only', 'session', '--help'), ('--only=session', '-h')):
            with self.subTest(args=args):
                result = self.run_wrapper(*args, jobs='invalid')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('tmnf.build.runtime_builder', result.stdout)
                self.assertNotIn('forbidden bootstrap', result.stderr)
                self.assertTrue(self.complete.exists())

    def test_session_bootstrap_only_requires_python_and_mingw(self):
        (self.bin / 'uname').write_text('#!/bin/bash\ncase "$1" in -s) echo Darwin;; -m) echo arm64;; esac\n')
        (self.bin / 'brew').write_text(
            '#!/bin/bash\n'
            f'printf "%s\\n" "$*" >>"{self.root}/bootstrap"\n'
            'case "$*" in\n'
            '  "list --versions python@3.14"|"list --versions mingw-w64") exit 0;;\n'
            f'  "--prefix"|"--prefix python@3.14") echo "{self.root}";;\n'
            '  *) echo "unexpected dependency: $*" >&2; exit 91;;\n'
            'esac\n')
        for args in (('--only', 'session'), ('--only=session',)):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ['-m', 'tmnf.build.runtime_builder', *args])
                self.assertEqual(self.complete.read_text(), 'complete')
        self.assertEqual((self.root / 'bootstrap').read_text().splitlines(),
                         ['list --versions python@3.14', 'list --versions mingw-w64',
                          '--prefix', '--prefix python@3.14'] * 2)

    def test_bootstrap_failure_does_not_mutate_completion_outside_lock(self):
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('forbidden bootstrap', result.stderr)
        self.assertEqual(self.complete.read_text(), 'complete')

    def test_invalid_jobs_never_reach_bootstrap(self):
        for jobs in ('', '0', '000', '-1', '1.5', 'text', ' 3', '+3'):
            with self.subTest(jobs=jobs):
                result = self.run_wrapper(jobs=jobs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('TMNF_JOBS must be a positive integer', result.stderr)
                self.assertNotIn('forbidden bootstrap', result.stderr)
                self.assertTrue(self.complete.exists())


if __name__ == '__main__':
    unittest.main()
