#!/usr/bin/env python3
"""Build and package the complete runtime without libraries from another Wine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
from game_profile import generate

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.build' / 'release'
SOURCES = BUILD / 'sources'
WORK = BUILD / 'work'
DEPS = WORK / 'deps'
RUNTIME = BUILD / 'runtime'
DIST = ROOT / '.build' / 'dist'
LOCK = json.loads((ROOT / 'config/sources.json').read_text())
VERSION = (ROOT / 'VERSION').read_text().strip()
JOBS = int(os.environ.get('MACTMNF_JOBS', '3'))
ENV = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='14.0')
LOG = BUILD / 'logs' / 'build.log'
if (ROOT / 'upstream').is_dir():
    ENV['MACTMNF_BUNDLED_SOURCES'] = '1'


def run(*args, cwd=ROOT, env=None, capture=False):
    command = [str(a) for a in args]
    if capture:
        return subprocess.run(command, cwd=cwd, env=env or ENV, text=True,
                              stdout=subprocess.PIPE, check=True).stdout.strip()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as log:
        log.write('\n$ ' + shlex.join(command) + '\n')
        log.flush()
        result = subprocess.run(command, cwd=cwd, env=env or ENV, text=True,
                                stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print('\n'.join(LOG.read_text(errors='replace').splitlines()[-35:]), file=sys.stderr)
        raise RuntimeError(f'{command[0]} failed with status {result.returncode}. Full log: {LOG}')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build_inputs():
    files = [ROOT / 'VERSION', ROOT / 'tools/build.py', ROOT / 'tools/build.sh', ROOT / 'tools/game_profile.py']
    files += list((ROOT / 'config').glob('*')) + list((ROOT / 'patches').rglob('*.patch'))
    files += list((ROOT / 'licenses').glob('*'))
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(files) if path.is_file()}


def apply_patches(name, source):
    for patch in sorted((ROOT / 'patches' / name).glob('*.patch')):
        reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                                 cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if reverse.returncode:
            run('git', 'apply', '--check', patch, cwd=source)
            run('git', 'apply', patch, cwd=source)


def fetch(name):
    print(f'Source: {name}', flush=True)
    spec = LOCK[name]
    dest = SOURCES / name
    stamp = dest / '.mactmnf-source.json'
    identity = json.dumps(spec, sort_keys=True)
    if dest.exists():
        if not stamp.exists() or stamp.read_text() != identity:
            raise RuntimeError(f'{dest} does not match sources.json; move it aside before rebuilding.')
        apply_patches(name, dest)
        return dest
    temp = dest.with_suffix('.partial')
    if temp.exists():
        shutil.rmtree(temp)
    shipped = ROOT / 'upstream' / name
    if shipped.is_dir():
        shutil.copytree(shipped, temp, symlinks=True)
    elif 'git' in spec:
        temp.mkdir(parents=True)
        run('git', 'init', '-q', temp)
        run('git', 'fetch', '--depth=1', spec['git'], spec['revision'], cwd=temp)
        run('git', 'checkout', '-q', '--detach', 'FETCH_HEAD', cwd=temp)
        if run('git', 'rev-parse', 'HEAD', cwd=temp, capture=True) != spec['revision']:
            raise RuntimeError(f'Wrong source revision for {name}')
        if spec.get('submodules'):
            run('git', 'submodule', 'update', '--init', '--recursive', '--depth=1', cwd=temp)
    else:
        archive = BUILD / 'downloads' / f'{name}-{spec["sha256"]}.tar'
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists() or digest(archive) != spec['sha256']:
            part = archive.with_suffix('.partial')
            run('curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '60', '--output', part, spec['url'])
            if digest(part) != spec['sha256']:
                part.unlink()
                raise RuntimeError(f'SHA-256 verification failed for {name}')
            part.replace(archive)
        temp.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            tar.extractall(temp, filter='data')
        children = list(temp.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise RuntimeError(f'Unexpected source archive layout: {name}')
        inner = children[0].rename(temp.with_suffix('.unpacked'))
        temp.rmdir()
        inner.rename(temp)
    apply_patches(name, temp)
    (temp / '.mactmnf-source.json').write_text(identity)
    temp.rename(dest)
    return dest


def autotools(name, *options):
    source = fetch(name)
    build = WORK / name
    build.mkdir(parents=True, exist_ok=True)
    env = dict(ENV, CC='/usr/bin/clang -arch x86_64', CXX='/usr/bin/clang++ -arch x86_64',
               CFLAGS='-O2', CXXFLAGS='-O2', CPPFLAGS=f'-I{DEPS}/include',
               LDFLAGS=f'-L{DEPS}/lib -Wl,-headerpad_max_install_names',
               PKG_CONFIG_LIBDIR=f'{DEPS}/lib/pkgconfig', PKG_CONFIG_PATH='')
    stamp = build / '.mactmnf-configure.json'
    identity = json.dumps({'source': LOCK[name], 'options': options,
                           'environment': {key: env[key] for key in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'MACOSX_DEPLOYMENT_TARGET')}}, sort_keys=True)
    if not (build / 'Makefile').exists() or not stamp.exists() or stamp.read_text() != identity:
        run(source / 'configure', '--build=aarch64-apple-darwin', '--host=x86_64-apple-darwin',
            f'--prefix={DEPS}', '--enable-shared', '--disable-static', *options, cwd=build, env=env)
        stamp.write_text(identity)
    run('/usr/bin/make', f'-j{JOBS}', cwd=build, env=env)
    run('/usr/bin/make', f'-j{JOBS}', 'install', cwd=build, env=env)


def dependencies():
    autotools('freetype', '--without-harfbuzz', '--without-brotli', '--without-png', '--without-bzip2')
    autotools('gmp', '--disable-cxx')
    autotools('nettle', '--disable-documentation', '--disable-openssl')
    autotools('libtasn1', '--disable-doc', '--disable-tools')
    autotools('libunistring', '--disable-rpath')
    autotools('gnutls', '--disable-doc', '--disable-tests', '--disable-tools', '--disable-cxx',
              '--without-p11-kit', '--without-idn', '--without-brotli',
              '--without-zstd', '--with-included-unistring=no', '--disable-nls')
    source = fetch('moltenvk')
    run('./fetchDependencies', '--macos', '--keep-cache', cwd=source)
    run('/usr/bin/make', 'macos', cwd=source)
    shutil.copy2(source / 'Package/Release/MoltenVK/dylib/macOS/libMoltenVK.dylib', DEPS / 'lib')
    # Wine dlopens these by the install ID discovered during configure.
    for lib in (DEPS / 'lib').glob('*.dylib'):
        if not lib.is_symlink():
            run('install_name_tool', '-id', '@rpath/' + lib.name, lib)


def wine():
    source = fetch('wine')
    build = WORK / 'wine'
    build.mkdir(parents=True, exist_ok=True)
    headers = SOURCES / 'moltenvk' / 'External' / 'Vulkan-Headers' / 'include'
    if not (headers / 'vulkan/vulkan.h').exists():
        raise RuntimeError(f'Vulkan headers missing: {headers}')
    configuration = ['/usr/bin/arch', '-x86_64', '/bin/bash', source / 'configure',
            '--prefix=/runtime', '--build=x86_64-apple-darwin', '--host=x86_64-apple-darwin',
            '--disable-tests', '--enable-archs=i386,x86_64', '--with-mingw',
            '--without-x', '--without-wayland', '--without-alsa', '--without-pulse', '--without-oss',
            '--without-gstreamer', '--without-sdl', '--without-usb', '--without-udev',
            '--without-cups', '--without-pcap', '--without-dbus',
            'CC=clang -arch x86_64', 'CXX=clang++ -arch x86_64',
            f'BISON={shutil.which("bison")}', f'FLEX={shutil.which("flex")}',
            f'CPPFLAGS=-I{headers}', f'FREETYPE_CFLAGS=-I{DEPS}/include/freetype2',
            f'FREETYPE_LIBS=-L{DEPS}/lib -lfreetype', f'GNUTLS_CFLAGS=-I{DEPS}/include',
            f'GNUTLS_LIBS=-L{DEPS}/lib -lgnutls',
            # Wine's Darwin detection strips install IDs down to basenames.
            # dlopen needs @rpath to find our bundled libraries after relocation.
            'ac_cv_lib_soname_freetype=@rpath/libfreetype.6.dylib',
            'ac_cv_lib_soname_gnutls=@rpath/libgnutls.30.dylib',
            'ac_cv_lib_soname_MoltenVK=@rpath/libMoltenVK.dylib',
            f'LDFLAGS=-L{DEPS}/lib -Wl,-rpath,{DEPS}/lib -Wl,-headerpad_max_install_names']
    stamp = build / '.mactmnf-configure.json'
    identity = json.dumps({'source': LOCK['wine'], 'arguments': list(map(str, configuration)),
                           'deployment_target': ENV['MACOSX_DEPLOYMENT_TARGET']}, sort_keys=True)
    if not (build / 'config.status').exists() or not stamp.exists() or stamp.read_text() != identity:
        run(*configuration, cwd=build,
            env=dict(ENV, PKG_CONFIG_LIBDIR=f'{DEPS}/lib/pkgconfig', PKG_CONFIG_PATH=''))
        stamp.write_text(identity)
    run('/usr/bin/arch', '-x86_64', '/usr/bin/make', f'-j{JOBS}', cwd=build)
    stage = WORK / 'wine-stage'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(exist_ok=True)
    run('/usr/bin/make', f'-j{JOBS}', 'install-lib', f'DESTDIR={stage}', cwd=build)
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    shutil.copytree(stage / 'runtime', RUNTIME, symlinks=True)


def d9vk():
    source = fetch('d9vk')
    build = WORK / 'd9vk'
    if not (build / 'build.ninja').exists():
        run('meson', 'setup', '--cross-file', source / 'build-win32.txt', '--buildtype=release',
            '-Doptimization=3', build, source)
    run('ninja', '-C', build, f'-j{JOBS}', 'src/d3d9/d3d9.dll')
    (RUNTIME / 'lib/d9vk').mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / 'src/d3d9/d3d9.dll', RUNTIME / 'lib/d9vk/d3d9.dll')


def sidecar():
    source = fetch('x87sidecar')
    build = WORK / 'x87sidecar'
    run('cmake', '-S', source, '-B', build, '-DCMAKE_BUILD_TYPE=Release')
    run('cmake', '--build', build, '--parallel', JOBS)
    run(build / 'bin/x87sidecar', '--probe')
    (RUNTIME / 'bin').mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / 'bin/x87sidecar', RUNTIME / 'bin/x87sidecar')


def relocate():
    for lib in (DEPS / 'lib').glob('*.dylib'):
        target = RUNTIME / 'lib' / lib.name
        if target.exists() or target.is_symlink():
            target.unlink()
        if lib.is_symlink():
            target.symlink_to(os.readlink(lib))
        else:
            shutil.copy2(lib, target)
    for binary in RUNTIME.rglob('*'):
        if binary.is_symlink() or not binary.is_file():
            continue
        kind = run('file', '-b', binary, capture=True)
        if 'PE32' in kind:
            compiler = 'x86_64' if 'PE32+' in kind else 'i686'
            run(f'{compiler}-w64-mingw32-strip', '--strip-debug', binary)
        if 'Mach-O' not in kind:
            continue
        # Keep framework paths intact, including those containing spaces.
        deps = run('otool', '-L', binary, capture=True).splitlines()[1:]
        for line in deps:
            if not line[:1].isspace():
                continue  # otool emits another header for each universal-binary slice.
            dep = line.strip().split(' (compatibility version')[0]
            if dep.startswith(('/System/', '/usr/lib/', '@loader_path/', '@executable_path/')):
                continue
            name = Path(dep).name
            if (RUNTIME / 'lib' / name).exists():
                if dep != '@rpath/' + name:
                    run('install_name_tool', '-change', dep, '@rpath/' + name, binary)
            elif dep.startswith('@rpath/') and (binary.parent / name).exists():
                continue
            else:
                raise RuntimeError(f'Unbundled library in {binary}: {dep}')
        load = run('otool', '-l', binary, capture=True)
        rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', load)
        desired = '@loader_path/' + os.path.relpath(RUNTIME / 'lib', binary.parent)
        for old in set(rpaths):
            if old.startswith('/'):
                run('install_name_tool', '-delete_rpath', old, binary)
        if desired not in rpaths:
            run('install_name_tool', '-add_rpath', desired, binary)
        run('strip', '-x', binary)
        run('codesign', '--force', '--sign', '-', binary)
    (RUNTIME / 'config').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'config/dxvk.conf', RUNTIME / 'config/dxvk.conf')
    (RUNTIME / 'config/Default.SystemConfig.Gbx').write_bytes(
        generate(json.loads((ROOT / 'config/game-settings.json').read_text())))
    shutil.copy2(ROOT / 'config/sources.json', RUNTIME / 'sources.json')
    info = {
        'version': VERSION,
        'macos': run('sw_vers', '-productVersion', capture=True),
        'xcode': run('xcodebuild', '-version', capture=True),
        'deployment_target': ENV['MACOSX_DEPLOYMENT_TARGET'],
        'tools': {name: run(name, '--version', capture=True).splitlines()[0]
                  for name in ('clang', 'i686-w64-mingw32-gcc', 'x86_64-w64-mingw32-gcc', 'cmake', 'meson', 'ninja')},
        'patches': {str(path.relative_to(ROOT)): digest(path) for path in sorted((ROOT / 'patches').rglob('*.patch'))},
    }
    (RUNTIME / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    (RUNTIME / '.mactmnf-runtime').write_text(VERSION + '\n')
    licenses = RUNTIME / 'licenses'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', licenses / 'MacTMNF.txt')
    shutil.copytree(ROOT / 'licenses', licenses / 'toolchain', dirs_exist_ok=True)
    for name in LOCK:
        dest = licenses / name
        dest.mkdir(exist_ok=True)
        for path in (SOURCES / name).rglob('*'):
            if '.git' in path.parts or not path.is_file():
                continue
            if path.name.upper().startswith(('COPYING', 'LICENSE', 'LICENCE', 'COPYRIGHT', 'NOTICE', 'FTL.TXT', 'README')):
                relative = path.relative_to(SOURCES / name)
                (dest / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest / relative)
    run(RUNTIME / 'bin/wine', '--version')
    run(RUNTIME / 'bin/x87sidecar', '--probe')
    manifest = ''.join(f'{digest(path)}  {path.relative_to(RUNTIME)}\n'
                       for path in sorted(RUNTIME.rglob('*'))
                       if path.is_file() and not path.is_symlink() and path.name != 'SHA256SUMS')
    (RUNTIME / 'SHA256SUMS').write_text(manifest)


def package():
    if not (RUNTIME / '.mactmnf-runtime').exists():
        raise RuntimeError('Run make build first.')
    complete = BUILD / 'build-inputs.json'
    if not complete.exists() or json.loads(complete.read_text()) != build_inputs():
        raise RuntimeError('Build inputs changed or the full build did not complete. Run make build before packaging.')
    DIST.mkdir(parents=True, exist_ok=True)
    for kind in ('runtime', 'source'):
        archive = DIST / f'mactmnf-{VERSION}-{kind}.tar.xz'
        with tarfile.open(archive, 'w:xz', preset=3) as tar:
            if kind == 'runtime':
                tar.add(RUNTIME, arcname='runtime')
            else:
                def project_filter(info):
                    return None if '__pycache__' in Path(info.name).parts else info
                for entry in ('VERSION', 'Makefile', 'README.md', 'LICENSE', '.gitignore', 'bin', 'lib',
                              'config', 'patches', 'tools', 'tests', 'docs', 'licenses', 'install.sh'):
                    tar.add(ROOT / entry, arcname=f'mactmnf/{entry}', filter=project_filter)
                def source_filter(info):
                    # Omit generated binaries and Git metadata, retain all corresponding source.
                    parts = Path(info.name).parts
                    if '.git' in parts:
                        return None
                    mvk = 'mactmnf/upstream/moltenvk/'
                    if any(info.name == mvk + p or info.name.startswith(mvk + p + '/')
                           for p in ('build', 'Package', 'External/build', 'DerivedData', 'xcodebuild.log')):
                        return None
                    return info
                for name in LOCK:
                    tar.add(SOURCES / name, arcname=f'mactmnf/upstream/{name}', filter=source_filter)
        (DIST / f'{archive.name}.sha256').write_text(f'{digest(archive)}  {archive.name}\n')
        print(archive, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['fetch', 'deps', 'wine', 'd9vk', 'sidecar', 'bundle', 'package'])
    args = parser.parse_args()
    if JOBS < 1:
        parser.error('MACTMNF_JOBS must be a positive integer')
    SOURCES.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(exist_ok=True)
    if args.only == 'fetch':
        for name in LOCK:
            fetch(name)
        return
    actions = [('deps', dependencies), ('wine', wine), ('d9vk', d9vk), ('sidecar', sidecar), ('bundle', relocate)]
    if args.only == 'package':
        package()
        return
    for name, action in actions:
        if not args.only or args.only == name:
            print(f'== {name} ==', flush=True)
            action()
    if args.only:
        print(f'Finished {args.only}.', flush=True)
    else:
        (BUILD / 'build-inputs.json').write_text(json.dumps(build_inputs(), indent=2) + '\n')
        print(f'Runtime ready: {RUNTIME}\nNext: make install', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
