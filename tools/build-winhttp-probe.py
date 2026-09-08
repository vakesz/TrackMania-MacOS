#!/usr/bin/env python3
"""Relink test-only winhttp DLLs using existing Wine objects, without running make.

Requires the prepared, unmodified winhttp sources and completed Wine work tree at
the pinned revision. All WinHTTP C sources are rebuilt for private-header changes.
Patches 0010 and 0011 are applied in order. No runtime is installed or
launched. --output must name a new directory outside this checkout.

The normal outputs retain Wine's builtin marker. native/{i386,x86_64}-windows
contains a second link without that marker. Put each native DLL beside its
matching test executable in separate scratch directories, then run with an
explicit SCRATCH WINEPREFIX and WINEDLLOVERRIDES=winhttp=n. Do not overwrite
prefix DLL symlinks, which can point into the installed runtime.
The pinned loader rejects builtin-marked DLLs with that override; winhttp=b and
WINEDLLPATH do not override DLLs already present in the installed runtime.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = '36b6a2cf679fb395f668a917b76537190e212d9c'
PATCH = 'patches/wine/0010-winhttp-report-certificate-errors.patch'
PATCHES = (PATCH, 'patches/wine/0011-winhttp-preserve-unignored-certificate-errors.patch')
PATCH_SCOPES = (('net.c', 'request.c'), ('net.c', 'request.c', 'winhttp_private.h'))
ARCHES = ('i386', 'x86_64')
CHANGED = ('cookie.c', 'handle.c', 'main.c', 'net.c', 'request.c', 'session.c', 'socket.c', 'url.c')


def recipe(makefile, target, work, source, output):
    """Read only the recorded recipe; never ask make to evaluate its dependency graph."""
    text = makefile.replace('\\\n', '')
    match = re.search(r'^' + re.escape(target) + r':[^\n]*\n\t([^\n]+)', text, re.MULTILINE)
    if not match:
        raise ValueError(f'Missing recorded recipe: {target}')
    command = match[1].replace('$@', shlex.quote(str(output / target)))
    for name in re.findall(r'\$\((\w+)\)', command):
        if name not in {f'{arch}_{kind}' for arch in ARCHES for kind in ('CC', 'CFLAGS', 'LDFLAGS')}:
            raise ValueError(f'Unsupported recipe variable: {name}')
        value = re.search(r'^' + name + r' = (.*)$', text, re.MULTILINE)
        if not value:
            raise ValueError(f'Missing recipe variable: {name}')
        command = command.replace(f'$({name})', value[1])
    if '$' in command:
        raise ValueError('Unsupported make expansion')

    args = shlex.split(command)
    for index, arg in enumerate(args):
        if arg == '.':
            args[index] = str(work)
        elif arg.startswith('-I') and not Path(arg[2:]).is_absolute():
            args[index] = '-I' + str(work / arg[2:])
        elif arg == '-I' + str(source / 'dlls/winhttp'):
            args[index] = '-I' + str(output / 'source/dlls/winhttp')
        elif arg.startswith(('dlls/', 'libs/', 'tools/')):
            replacement = output / arg
            if arg in {f'dlls/winhttp/{arch}-windows/{Path(name).stem}.o'
                       for arch in ARCHES for name in CHANGED}:
                args[index] = str(replacement)
            else:
                if arg.startswith('dlls/winhttp/') and arg.endswith('.o'):
                    raise ValueError(f'Unrebuilt WinHTTP object: {arg}')
                args[index] = str(work / arg)
        elif arg in {str(source / 'dlls/winhttp' / name) for name in CHANGED}:
            args[index] = str(output / 'source/dlls/winhttp' / Path(arg).name)
    expected = 'tools/winegcc/winegcc' if target.endswith('.dll') else None
    compiler = 'i686-w64-mingw32-gcc' if '/i386-windows/' in target else 'x86_64-w64-mingw32-gcc'
    if args[0] != (str(work / expected) if expected else compiler):
        raise ValueError(f'Unexpected compiler in {target}')
    if args.count('-o') != 1 or args[args.index('-o') + 1] != str(output / target):
        raise ValueError(f'Unexpected output in {target}')
    if not expected and str(output / 'source/dlls/winhttp' / (Path(target).stem + '.c')) not in args:
        raise ValueError(f'Recipe does not compile the patched source: {target}')
    # These recipes must not contain hidden dependency, import-library, or debug outputs.
    if any(arg.startswith(('-M', '-save-temps', '-Wl,--out-implib', '-Wl,--debug-file'))
           for arg in args):
        raise ValueError(f'Unsupported additional output in {target}')
    return args


def build(output, root=ROOT):
    root = root.resolve()
    output = output.parent.resolve(strict=True) / output.name
    if output == root or root in output.parents:
        raise ValueError('Output must be outside the checkout, not in .build or the runtime')
    if output.exists() or output.is_symlink():
        raise ValueError('Output directory must not already exist')
    release = root / '.build/release'
    work = release / 'work/wine'
    source = release / 'sources/wine'
    pin = json.loads((root / 'config/sources.json').read_text())['wine']
    stamp = json.loads((source / '.mactmnf-source.json').read_text())
    configured = json.loads((work / '.mactmnf-configure.json').read_text())
    # Existing work trees use the old flat source stamp; newer ones nest upstream.
    if pin['revision'] != PIN or stamp.get('upstream', stamp) != pin or configured['source'] != pin:
        raise ValueError('Source/configure pin mismatch; this recipe requires the existing pinned build')
    git_env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        git_env.pop(name, None)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, env=git_env, text=True).strip()
    if head != PIN:
        raise ValueError('Prepared source HEAD does not match the pin')
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', 'dlls/winhttp'],
                   cwd=source, env=git_env, check=True)
    patches = []
    for patch, scope in zip(PATCHES, PATCH_SCOPES):
        patch_bytes = (root / patch).read_bytes()
        changed = subprocess.check_output(['git', 'apply', '--numstat', '-'], input=patch_bytes,
                                          cwd=source, env=git_env)
        paths = [line.split(b'\t')[-1].decode() for line in changed.splitlines()]
        if sorted(paths) != sorted(f'dlls/winhttp/{name}' for name in scope):
            raise ValueError(f'{patch} must change only ' + ', '.join(scope))
        patches.append((patch, patch_bytes))
    if set(re.findall(r'\b(\w+\.c)\b', (source / 'dlls/winhttp/Makefile.in').read_text())) != set(CHANGED):
        raise ValueError('WinHTTP source list changed; refusing partial private-header rebuild')
    makefile = (work / 'Makefile').read_text()
    commands = []
    for arch in ARCHES:
        for name in (*(Path(name).stem + '.o' for name in CHANGED), 'winhttp.dll'):
            target = f'dlls/winhttp/{arch}-windows/{name}'
            commands.append(recipe(makefile, target, work, source, output))
        native = commands[-1].copy()
        native.remove('-Wl,--wine-builtin')
        native[native.index('-o') + 1] = str(output / f'native/{arch}-windows/winhttp.dll')
        commands.append(native)
    for command in commands:
        for arg in command:
            if arg.startswith(str(work) + '/') and not Path(arg).is_file():
                raise ValueError(f'Missing existing build input: {arg}; no rebuild attempted')

    output.mkdir(mode=0o700)
    (output / 'tmp').mkdir()
    for arch in ARCHES:
        (output / f'dlls/winhttp/{arch}-windows').mkdir(parents=True)
        (output / f'native/{arch}-windows').mkdir(parents=True)
    shutil.copytree(source / 'dlls/winhttp', output / 'source/dlls/winhttp')
    env = dict(git_env, TMPDIR=str(output / 'tmp'), TMP=str(output / 'tmp'),
               TEMP=str(output / 'tmp'), MACOSX_DEPLOYMENT_TARGET='14.0')
    for patch, patch_bytes in patches:
        local_patch = output / Path(patch).name
        local_patch.write_bytes(patch_bytes)
        subprocess.run(['git', 'apply', '--check', str(local_patch)], cwd=output / 'source', env=env, check=True)
        subprocess.run(['git', 'apply', str(local_patch)], cwd=output / 'source', env=env, check=True)
    manifest = {'test_only': True, 'revision': PIN,
                'patches': [{'path': patch, 'sha256': hashlib.sha256(data).hexdigest()} for patch, data in patches],
                'rebuilt_sources': list(CHANGED),
                'source_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (output / 'source/dlls/winhttp').iterdir() if path.is_file()},
                'makefile_sha256': hashlib.sha256(makefile.encode()).hexdigest(),
                'commands': commands, 'cwd': str(output)}
    (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (output / 'build.log').open('w') as log:
        for command in commands:
            print(f'Building {command[command.index("-o") + 1]}', flush=True)
            log.write('$ ' + shlex.join(command) + '\n')
            log.flush()
            subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    artifacts = [output / f'{kind}/{arch}-windows/winhttp.dll'
                 for kind in ('dlls/winhttp', 'native') for arch in ARCHES]
    manifest['artifacts'] = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in artifacts}
    (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True, type=Path, help='New directory in caller-owned scratch space')
    parser.add_argument('--fault-base', type=Path,
                        help='Relink an existing isolated build with test-only certificate-lookup/release wrappers')
    args = parser.parse_args()
    try:
        for artifact in (build_fault_overlay(args.output, args.fault_base) if args.fault_base else build(args.output)):
            print(artifact)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\nNo full build attempted. See output/build.log if compilation started.\n')


# Only the explicit --fault-base link includes these wrappers. Normal DLLs and
# Wine sources contain no fault hook. The first lookup is the trusted control.
FAULT_WRAPPER = r'''
#include <windows.h>
#include <stdio.h>
struct netconn;
const void *__real_netconn_get_certificate(struct netconn *);
void __real_netconn_release(struct netconn *);
static struct netconn *rejected;
static LONG lookups, releases;
const void *__wrap_netconn_get_certificate(struct netconn *conn)
{
    if (InterlockedIncrement(&lookups) != 2) return __real_netconn_get_certificate(conn);
    rejected = conn;
    fputs("FAULT: final certificate lookup returned NULL\n", stderr);
    fflush(stderr);
    return NULL;
}
void __wrap_netconn_release(struct netconn *conn)
{
    if (conn == rejected)
    {
        LONG count = InterlockedIncrement(&releases);
        fprintf(stderr, "FAULT: rejected connection release=%ld\n", count);
        fflush(stderr);
        /* Do not dereference the freed pointer when detecting the regression. */
        if (count != 1) ExitProcess(86);
    }
    __real_netconn_release(conn);
}
'''


def build_fault_overlay(output, base):
    base = base.resolve(strict=True)
    output = output.parent.resolve(strict=True) / output.name
    if output == ROOT or ROOT in output.parents or base in output.parents or output.exists() or output.is_symlink():
        raise ValueError('Fault output must be a new directory outside the checkout and base build')
    manifest_bytes = (base / 'build.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('test_only') is not True or manifest.get('revision') != PIN or manifest.get('fault_injection'):
        raise ValueError('Expected an uninstrumented isolated build at the pinned revision')
    work = ROOT / '.build/release/work/wine'
    source = ROOT / '.build/release/sources/wine'
    makefile = (work / 'Makefile').read_text()
    if manifest['makefile_sha256'] != hashlib.sha256(makefile.encode()).hexdigest():
        raise ValueError('Recorded Makefile hash changed')
    commands = []
    for arch in ARCHES:
        dll = f'native/{arch}-windows/winhttp.dll'
        if manifest['artifacts'][dll] != hashlib.sha256((base / dll).read_bytes()).hexdigest():
            raise ValueError('Base DLL hash mismatch')
        command = recipe(makefile, f'dlls/winhttp/{arch}-windows/winhttp.dll', work, source, base)
        command.remove('-Wl,--wine-builtin')
        command[command.index('-o') + 1] = str(base / dll)
        if command not in manifest['commands']:
            raise ValueError('Base native link does not match the recorded recipe')
        obj = output / f'native/{arch}-windows/fault.o'
        compile_command = recipe(makefile, f'dlls/winhttp/{arch}-windows/net.o', work, source, base)
        if compile_command not in manifest['commands']:
            raise ValueError('Base compilation does not match the recorded recipe')
        compile_command[compile_command.index(str(base / 'source/dlls/winhttp/net.c'))] = str(output / 'fault.c')
        compile_command[compile_command.index('-o') + 1] = str(obj)
        commands.append(compile_command)
        command[command.index('-o') + 1] = str(output / dll)
        command.insert(command.index('-shared') + 1, str(obj))
        command.extend(['-Wl,--wrap=netconn_get_certificate', '-Wl,--wrap=netconn_release'])
        commands.append(command)
    output.mkdir(mode=0o700)
    (output / 'tmp').mkdir()
    for arch in ARCHES:
        (output / f'native/{arch}-windows').mkdir(parents=True)
    (output / 'fault.c').write_text(FAULT_WRAPPER, encoding='ascii')
    env = dict(os.environ, TMPDIR=str(output / 'tmp'), TMP=str(output / 'tmp'), TEMP=str(output / 'tmp'))
    with (output / 'build.log').open('w') as log:
        for command in commands:
            log.write('$ ' + shlex.join(command) + '\n')
            log.flush()
            subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    artifacts = [output / f'native/{arch}-windows/winhttp.dll' for arch in ARCHES]
    result = {'test_only': True, 'fault_injection': True, 'revision': PIN, 'patches': manifest['patches'],
              'base': str(base), 'base_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'wrapper_sha256': hashlib.sha256(FAULT_WRAPPER.encode()).hexdigest(), 'commands': commands,
              'artifacts': {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}}
    (output / 'build.json').write_text(json.dumps(result, indent=2) + '\n')
    return artifacts


if __name__ == '__main__':
    main()
