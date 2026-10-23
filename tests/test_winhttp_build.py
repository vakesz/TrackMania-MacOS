import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'winhttp_build', Path(__file__).resolve().parents[1] / 'src/python/tmnf/build/winhttp_probe_builder.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class WinhttpBuildTests(unittest.TestCase):
    def test_output_with_spaces_is_one_argument(self):
        target = 'dlls/winhttp/i386-windows/net.o'
        command = build.recipe(f'{target}:\n\ti686-w64-mingw32-gcc -c -o $@ /source/dlls/winhttp/net.c\n',
                               target, Path('/work'), Path('/source'), Path('/scratch with spaces'))
        self.assertEqual(command[command.index('-o') + 1], '/scratch with spaces/' + target)

    def test_compile_reuses_flags_and_headers_but_redirects_source_and_output(self):
        for arch, cc in (('i386', 'i686'), ('x86_64', 'x86_64')):
            with self.subTest(arch=arch):
                target = f'dlls/winhttp/{arch}-windows/net.o'
                makefile = (f'{arch}_CC = {cc}-w64-mingw32-gcc\n{arch}_CFLAGS = -O2 -g\n'
                            f'{target}: /source/dlls/winhttp/net.c\n'
                            f'\t$({arch}_CC) -c -o $@ /source/dlls/winhttp/net.c \\\n'
                            f'  -Idlls/winhttp -Iinclude -I/source/include -Wall $({arch}_CFLAGS)\n')
                command = build.recipe(makefile, target, Path('/work'), Path('/source'), Path('/scratch'))
                self.assertEqual(command, [f'{cc}-w64-mingw32-gcc', '-c', '-o', f'/scratch/{target}',
                                           '/scratch/source/dlls/winhttp/net.c', '-I/work/dlls/winhttp',
                                           '-I/work/include', '-I/source/include', '-Wall', '-O2', '-g'])

    def test_link_rebuilds_all_winhttp_objects_and_keeps_imports_read_only(self):
        target = 'dlls/winhttp/i386-windows/winhttp.dll'
        command = build.recipe(
            f'i386_LDFLAGS = -Wl,--build-id\n{target}: dependency\n'
            '\ttools/winegcc/winegcc -o $@ --wine-objdir . -Wl,--wine-builtin -shared '
            'dlls/winhttp/i386-windows/net.o dlls/winhttp/i386-windows/request.o '
            'dlls/winhttp/i386-windows/session.o dlls/ntdll/i386-windows/libntdll.a $(i386_LDFLAGS)\n',
            target, Path('/work'), Path('/source'), Path('/scratch'))
        self.assertEqual(command[0], '/work/tools/winegcc/winegcc')
        self.assertEqual(command[command.index('--wine-objdir') + 1], '/work')
        self.assertIn('/scratch/dlls/winhttp/i386-windows/net.o', command)
        self.assertIn('/scratch/dlls/winhttp/i386-windows/request.o', command)
        self.assertIn('/scratch/dlls/winhttp/i386-windows/session.o', command)
        self.assertIn('/work/dlls/ntdll/i386-windows/libntdll.a', command)
        self.assertIn('-Wl,--wine-builtin', command)

    def test_unknown_or_missing_recipes_fail_closed(self):
        target = 'dlls/winhttp/i386-windows/net.o'
        for makefile in ('', f'{target}:\n\t$(shell touch /bad) -o $@\n',
                         f'{target}:\n\t$(OTHER_CC) -o $@\n',
                         f'{target}:\n\ti686-w64-mingw32-gcc -o /bad\n',
                         f'{target}:\n\ti686-w64-mingw32-gcc -o $@ -MF /bad\n'):
            with self.subTest(makefile=makefile), self.assertRaises(ValueError):
                build.recipe(makefile, target, Path('/work'), Path('/source'), Path('/scratch'))

    def test_every_source_uses_the_scratch_private_header(self):
        self.assertEqual(set(build.CHANGED), {'cookie.c', 'handle.c', 'main.c', 'net.c', 'request.c',
                                             'session.c', 'socket.c', 'url.c'})
        for name in build.CHANGED:
            target = f'dlls/winhttp/i386-windows/{Path(name).stem}.o'
            command = build.recipe(f'{target}:\n\ti686-w64-mingw32-gcc -c -o $@ '
                                   f'/source/dlls/winhttp/{name} -I/source/dlls/winhttp\n',
                                   target, Path('/work'), Path('/source'), Path('/scratch'))
            self.assertIn('/scratch/source/dlls/winhttp/' + name, command)
            self.assertIn('-I/scratch/source/dlls/winhttp', command)
        target = 'dlls/winhttp/i386-windows/winhttp.dll'
        with self.assertRaisesRegex(ValueError, 'Unrebuilt WinHTTP object'):
            build.recipe(f'{target}:\n\ttools/winegcc/winegcc -o $@ dlls/winhttp/i386-windows/unknown.o\n',
                         target, Path('/work'), Path('/source'), Path('/scratch'))

    def test_second_patch_scope_rejected_before_output_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root = base / 'checkout'
            source = root / '.build/release/sources/wine'
            work = root / '.build/release/work/wine'
            for directory in (source, work, root / 'config', root / 'patches/wine'):
                directory.mkdir(parents=True)
            pin = {'git': 'fixture', 'revision': build.PIN}
            (root / 'config/sources.json').write_text(json.dumps({'wine': pin}))
            (source / '.mactmnf-source.json').write_text(json.dumps(pin))
            (work / '.mactmnf-configure.json').write_text(json.dumps({'source': pin}))
            for patch in build.PATCHES:
                (root / patch).write_bytes((build.ROOT / patch).read_bytes())
            for wrong in (b'1\t1\tdlls/crypt32/chain.c\n', b'1\t1\tdlls/winhttp/net.c\n',
                          b'1\t1\tdlls/winhttp/net.c\n1\t1\tdlls/winhttp/request.c\n1\t1\tdlls/winhttp/session.c\n'):
                with self.subTest(scope=wrong), mock.patch.object(build.subprocess, 'check_output', side_effect=[
                        build.PIN + '\n', b'1\t1\tdlls/winhttp/net.c\n1\t1\tdlls/winhttp/request.c\n', wrong]), \
                        mock.patch.object(build.subprocess, 'run'), \
                        self.assertRaisesRegex(ValueError, '0011.*must change only'):
                    build.build(base / 'output', root=root)
                self.assertFalse((base / 'output').exists())

    def test_existing_and_checkout_output_rejected_before_reading_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for output in (root, root / 'new', root.parent / root.name):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    build.build(output, root=root)
            outside = root / 'existing'
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, 'already exist'):
                build.build(outside, root=root / 'checkout')

    def test_wrong_pin_and_patch_scope_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root = base / 'checkout'
            source = root / '.build/release/sources/wine'
            work = root / '.build/release/work/wine'
            for directory in (source, work, root / 'config', root / 'patches/wine'):
                directory.mkdir(parents=True)
            pin = {'git': 'fixture', 'revision': build.PIN}
            (root / 'config/sources.json').write_text(json.dumps({'wine': pin}))
            (source / '.mactmnf-source.json').write_text(json.dumps(pin))
            (work / '.mactmnf-configure.json').write_text(json.dumps({'source': pin}))
            (root / build.PATCH).write_text('fixture patch')
            with mock.patch.object(build.subprocess, 'check_output', return_value='wrong HEAD\n'), \
                    self.assertRaisesRegex(ValueError, 'HEAD'):
                build.build(base / 'output', root=root)
            with mock.patch.dict(os.environ, GIT_DIR='/unrelated/git', GIT_WORK_TREE='/unrelated/tree',
                                 GIT_INDEX_FILE='/unrelated/index'), \
                    mock.patch.object(build.subprocess, 'check_output', side_effect=[
                    build.PIN + '\n', b'1\t1\tdlls/winhttp/winhttp_private.h\n']), \
                    mock.patch.object(build.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'only net.c, request.c'):
                    build.build(base / 'output', root=root)
                self.assertEqual(run.call_args.kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
                for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
                    self.assertNotIn(name, run.call_args.kwargs['env'])
            self.assertFalse((base / 'output').exists())

    def test_fault_overlay_refuses_existing_output_and_invalid_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            checkout = root / 'checkout'
            work = checkout / '.build/release/work/wine'
            work.mkdir(parents=True)
            (work / 'Makefile').write_text('')
            base = root / 'base'
            base.mkdir()
            manifest = {'test_only': True, 'revision': build.PIN}
            with mock.patch.object(build, 'ROOT', checkout), mock.patch.object(build.subprocess, 'run') as run:
                for output in (base, base / 'output', checkout / 'output'):
                    with self.assertRaisesRegex(ValueError, 'new directory outside'):
                        build.build_fault_overlay(output, base)
                for wrong in ({}, dict(manifest, revision='wrong'), dict(manifest, fault_injection=True),
                              dict(manifest, test_only=False)):
                    (base / 'build.json').write_text(json.dumps(wrong))
                    with self.subTest(manifest=wrong), self.assertRaisesRegex(ValueError, 'uninstrumented'):
                        build.build_fault_overlay(root / 'output', base)
                manifest['makefile_sha256'] = 'wrong'
                (base / 'build.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'Makefile hash changed'):
                    build.build_fault_overlay(root / 'output', base)
                manifest['makefile_sha256'] = build.hashlib.sha256(b'').hexdigest()
                dll = base / 'native/i386-windows/winhttp.dll'
                dll.parent.mkdir(parents=True)
                dll.write_bytes(b'test input')
                manifest['artifacts'] = {'native/i386-windows/winhttp.dll': 'wrong'}
                (base / 'build.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'Base DLL hash mismatch'):
                    build.build_fault_overlay(root / 'output', base)
                self.assertFalse((root / 'output').exists())
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
