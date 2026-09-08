#!/usr/bin/env python3
"""Build and package the complete runtime without libraries from another Wine."""
import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Capture before local imports and lock waits: old code must not stamp newer sources.
LOADED_PYTHON_INPUTS = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                      for name in ('tools/build.py', 'tools/game_profile.py', 'lib/runtime_manifest.py')}

from game_profile import generate

sys.path.insert(0, str(ROOT / 'lib'))
from runtime_manifest import verify_runtime

BUILD = ROOT / '.build' / 'release'
SOURCES = BUILD / 'sources'
WORK = BUILD / 'work'
DEPS = WORK / 'deps'
RUNTIME = BUILD / 'runtime'
DIST = ROOT / '.build' / 'dist'
LOCK = json.loads((ROOT / 'config/sources.json').read_text())
VERSION = (ROOT / 'VERSION').read_text().strip()
JOBS = os.cpu_count() or 1  # MACTMNF_JOBS overrides; see main().
ENV = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='14.0')
LOG = BUILD / 'logs' / 'build.log'
if (ROOT / 'upstream').is_dir():
    ENV['MACTMNF_BUNDLED_SOURCES'] = '1'
# Remap the build path out of __FILE__ and debug strings, so shipped binaries
# never carry the maintainer's home directory. verify_no_build_paths() enforces
# that nothing slips through.
FILE_PREFIX_MAP = f'-ffile-prefix-map={ROOT}=/mactmnf'
PROJECT_ENTRIES = ('VERSION', 'Makefile', 'README.md', 'LICENSE', '.gitignore', 'bin', 'lib',
                   'config', 'patches', 'src', 'tools', 'tests', 'docs', 'licenses', 'install.sh')


def elapsed_text(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f'{seconds // 3600}h{seconds % 3600 // 60:02d}m'
    if seconds >= 60:
        return f'{seconds // 60}m{seconds % 60:02d}s'
    return f'{seconds}s'


STATUS = None  # The Progress line currently on screen, if any.


def status(detail):
    """Update the detail shown on the active progress line, if there is one."""
    if STATUS is not None:
        STATUS.detail = detail


class Progress:
    """One build step on screen: its title, elapsed time and latest detail.

    On a terminal the line redraws in place twice a second. Elsewhere (a pipe,
    a CI log) it prints when the step starts, once a minute while it runs, and
    when it finishes, so long compiles never look hung."""

    def __init__(self, title, detail=None):
        self.title = title
        self.detail = detail  # A string, or a callable returning the current detail.
        self.started = time.monotonic()
        self.tty = sys.stdout.isatty()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.refresh, daemon=True)

    def __enter__(self):
        global STATUS
        STATUS = self
        if self.tty:
            self.draw()
        else:
            print(f'  {self.title} ...', flush=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        global STATUS
        self.stopped.set()
        self.thread.join()
        STATUS = None
        outcome = 'done' if kind is None else 'stopped' if kind is KeyboardInterrupt else 'failed'
        line = f'  {self.title}: {outcome} in {self.elapsed()}'
        sys.stdout.write(('\r' + line + '\x1b[K' if self.tty else line) + '\n')
        sys.stdout.flush()

    def elapsed(self):
        return elapsed_text(time.monotonic() - self.started)

    def current_detail(self):
        detail = self.detail() if callable(self.detail) else self.detail
        return ' '.join((detail or '').split())

    def refresh(self):
        while not self.stopped.wait(0.5 if self.tty else 60):
            if self.tty:
                self.draw()
            else:
                print(f'  {self.title}: still running after {self.elapsed()}: {self.current_detail()}',
                      flush=True)

    def draw(self):
        width = max(shutil.get_terminal_size().columns - 1, 20)
        line = f'  {self.title}  {self.elapsed()}  {self.current_detail()}'
        sys.stdout.write('\r' + line[:width] + '\x1b[K')
        sys.stdout.flush()


def log_tail():
    """The last non-empty line the running command wrote to the build log."""
    try:
        with LOG.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(stream.tell() - 4096, 0))
            text = stream.read().decode(errors='replace')
    except OSError:
        return ''
    lines = [line.strip() for line in re.split(r'[\r\n]', text) if line.strip()]
    return lines[-1] if lines else ''


def run(*args, cwd=ROOT, env=None, capture=False, title=None, quiet=False):
    """Run a command, logging its output. Without capture or quiet, show a progress line."""
    command = [str(a) for a in args]
    if capture:
        return subprocess.run(command, cwd=cwd, env=env or ENV, text=True,
                              stdout=subprocess.PIPE, check=True).stdout.strip()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if title is None:
        title = ' '.join([Path(command[0]).name, *command[1:2]])
    with LOG.open('a') as log:
        log.write('\n$ ' + shlex.join(command) + '\n')
        log.flush()
        with nullcontext() if quiet else Progress(title, log_tail):
            process = subprocess.Popen(command, cwd=cwd, env=env or ENV, text=True,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                returncode = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
            if returncode:
                raise RuntimeError(f'{command[0]} failed with status {returncode}. Full log: {LOG}')


def digest(path):
    with Path(path).open('rb') as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        return checksum.hexdigest()


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


@contextmanager
def release_lock():
    BUILD.mkdir(parents=True, exist_ok=True)
    # Keep this inode outside WORK, and never unlink it while another process waits.
    with (BUILD / '.mactmnf.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'Another build or package run holds the lock{lock_holder(lock)}.\n'
                  'Waiting for it to finish; press Ctrl-C to stop waiting.', flush=True)
            with Progress('Waiting for the build lock'):
                fcntl.flock(lock, fcntl.LOCK_EX)
        for name, expected in LOADED_PYTHON_INPUTS.items():
            if not (ROOT / name).is_file() or digest(ROOT / name) != expected:
                raise RuntimeError(f'Loaded Python input changed since startup: {name}. '
                                   'Restart tools/build.sh before building or packaging.')
        # Record the holder for the message above; the file is never unlinked.
        lock.seek(0)
        lock.truncate()
        lock.write(f'{os.getpid()} {shlex.join(sys.argv)} started {time.strftime("%H:%M:%S")}\n')
        lock.flush()
        yield


def lock_holder(lock):
    """Describe the recorded lock holder when that process is still alive."""
    lock.seek(0)
    pid, _, description = lock.read().strip().partition(' ')
    try:
        os.kill(int(pid), 0)
    except (ValueError, OSError):
        return ''
    return f' (pid {pid}: {description})'


def inventory(root, exclude=lambda path: False):
    """Record files, modes and link targets without traversing symlinks."""
    mode = root.lstat().st_mode
    if not stat.S_ISDIR(mode):
        raise RuntimeError(f'Inventory root must be a real directory: {root}')
    result = {'.': ['directory', stat.S_IMODE(mode)]}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if exclude(relative):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                result[str(relative)] = ['link', os.readlink(path)]
            elif stat.S_ISREG(mode):
                result[str(relative)] = ['file', stat.S_IMODE(mode), digest(path)]
            elif stat.S_ISDIR(mode):
                result[str(relative)] = ['directory', stat.S_IMODE(mode)]
                visit(path)
            else:
                raise RuntimeError(f'Unsupported archive member: {path}')

    visit(root)
    return result


def source_excluded(name, path):
    if any(part in ('.git', '__pycache__') for part in path.parts) or path == Path('.mactmnf-source.json'):
        return True
    # libunistring 1.4.2 leaves this scratch file when declared.sh lacks GNU sed.
    # Keep the distributed libunistring.sym itself covered by the source gate.
    if name == 'libunistring' and path == Path('lib/libunistring.sym-t1'):
        return True
    if name != 'moltenvk':
        return False
    # xcodebuild creates empty *.xcodeproj/project.xcworkspace trees; the tarball ships none.
    if any(part.endswith('.xcodeproj') and path.parts[i + 1:i + 2] == ('project.xcworkspace',)
           for i, part in enumerate(path.parts)):
        return True
    return any(path == Path(p) or Path(p) in path.parents
        for p in ('build', 'Package', 'External/build', 'DerivedData',
                  'xcodebuild.log', 'dependenciesbuild.log'))


def project_excluded(path):
    return path.parts[0] not in PROJECT_ENTRIES or '__pycache__' in path.parts


def source_inventory(required=True):
    result = {}
    for name in LOCK:
        if required or (SOURCES / name).exists():
            status(f'upstream/{name}')
            result[name] = inventory(SOURCES / name, lambda path: source_excluded(name, path))
    return result


def source_snapshot(required=True):
    for entry in PROJECT_ENTRIES:
        if not (ROOT / entry).exists():
            raise RuntimeError(f'Missing source archive input: {ROOT / entry}')
    status('project')
    return {'project': inventory(ROOT, project_excluded), 'upstream': source_inventory(required)}


def hashed_snapshot(title, required=True):
    """source_snapshot() behind a progress line: hashing every source tree takes a while."""
    with Progress(title):
        return source_snapshot(required)


def inventory_changes(before, after, prefix=''):
    """Describe nested snapshot drift, including modes, hashes and link targets."""
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        path = f'{prefix}/{name}' if prefix else name
        if old == new:
            continue
        if isinstance(old, dict) or isinstance(new, dict):
            changes.extend(inventory_changes(old or {}, new or {}, path))
        else:
            kind = 'added' if old is None else 'removed' if new is None else 'changed'
            changes.append(f'{path}: {kind}: {old!r} -> {new!r}')
    return changes


def prepared_identity(name):
    shipped = ROOT / 'upstream' / name
    return {'schema': 2, 'upstream': LOCK[name],
            'patches': [[str(path.relative_to(ROOT)), digest(path)]
                        for path in sorted((ROOT / 'patches' / name).glob('*.patch'))],
            'bundled': hashlib.sha256(json.dumps(
                inventory(shipped, lambda path: source_excluded(name, path)),
                sort_keys=True).encode()).hexdigest() if shipped.is_dir() else None}


def toolchain_identity(only=None):
    tools = ('/usr/bin/clang', '/usr/bin/clang++', 'clang', 'clang++',
             'i686-w64-mingw32-gcc', 'i686-w64-mingw32-g++',
             'x86_64-w64-mingw32-gcc', 'x86_64-w64-mingw32-g++',
             '/usr/bin/make', 'cmake', 'meson', 'ninja', 'bison', 'flex',
             'pkg-config', 'autoconf', 'automake', 'glslangValidator')
    if only == 'session':
        tools = ('x86_64-w64-mingw32-gcc',)
    identity = {'tools': {name: {'path': str(Path(shutil.which(name) or name).resolve()),
                                'version': run(name, '--version', capture=True)} for name in tools}}
    if only == 'session':
        return identity
    return dict(identity,
            xcode=run('xcodebuild', '-version', capture=True),
            sdk=run('xcrun', '--sdk', 'macosx', '--show-sdk-path', capture=True),
            sdk_version=run('xcrun', '--sdk', 'macosx', '--show-sdk-version', capture=True),
            macos=run('sw_vers', '-productVersion', capture=True),
            environment={key: ENV.get(key) for key in
                             ('MACOSX_DEPLOYMENT_TARGET', 'SDKROOT', 'DEVELOPER_DIR',
                              'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS')})


def build_inputs():
    files = [ROOT / 'VERSION', ROOT / 'LICENSE', ROOT / 'tools/build.py', ROOT / 'tools/build.sh',
             ROOT / 'tools/game_profile.py', ROOT / 'lib/runtime_manifest.py']
    files += list((ROOT / 'config').rglob('*')) + list((ROOT / 'patches').rglob('*.patch'))
    files += list((ROOT / 'licenses').rglob('*')) + list((ROOT / 'src').rglob('*'))
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(files) if path.is_file()}


def apply_patches(name, source):
    for patch in sorted((ROOT / 'patches' / name).glob('*.patch')):
        reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)],
                                 cwd=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if reverse.returncode:
            run('git', 'apply', '--check', patch, cwd=source, quiet=True)
            run('git', 'apply', patch, cwd=source, title=f'{name}: applying {patch.name}')


def fetch(name):
    spec = LOCK[name]
    dest = SOURCES / name
    stamp = dest / '.mactmnf-source.json'
    identity = json.dumps(prepared_identity(name), sort_keys=True)
    if dest.exists():
        if stamp.exists() and stamp.read_text() == identity:
            print(f'Source: {name}: prepared', flush=True)
            return dest
        # Prepared trees are build products; patches/ is the edit surface.
        print(f'Re-preparing {name}: the source stamp is missing or its upstream/patch inputs changed.', flush=True)
        shutil.rmtree(dest)
    else:
        print(f'Source: {name}: preparing', flush=True)
    temp = dest.with_suffix('.partial')
    for leftover in (temp, temp.with_suffix('.unpacked')):
        if leftover.exists():
            shutil.rmtree(leftover)
    shipped = ROOT / 'upstream' / name
    if shipped.is_dir():
        with Progress(f'{name}: copying bundled sources'):
            shutil.copytree(shipped, temp, symlinks=True)
    elif 'git' in spec:
        temp.mkdir(parents=True)
        run('git', 'init', '-q', temp, quiet=True)
        run('git', 'fetch', '--depth=1', spec['git'], spec['revision'], cwd=temp,
            title=f'{name}: fetching {spec["git"]} at {spec["revision"][:12]}')
        run('git', 'checkout', '-q', '--detach', 'FETCH_HEAD', cwd=temp, quiet=True)
        if run('git', 'rev-parse', 'HEAD', cwd=temp, capture=True) != spec['revision']:
            raise RuntimeError(f'Wrong source revision for {name}')
        if spec.get('submodules'):
            run('git', 'submodule', 'update', '--init', '--recursive', '--depth=1', cwd=temp,
                title=f'{name}: fetching submodules')
    else:
        archive = BUILD / 'downloads' / f'{name}-{spec["sha256"]}.tar'
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists() or digest(archive) != spec['sha256']:
            part = archive.with_suffix('.partial')
            run('curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '60', '--output', part, spec['url'],
                title=f'{name}: downloading {spec["url"]}')
            if digest(part) != spec['sha256']:
                part.unlink()
                raise RuntimeError(f'SHA-256 verification failed for {name}')
            part.replace(archive)
        temp.mkdir(parents=True)
        with tarfile.open(archive) as tar, Progress(f'{name}: unpacking {archive.name}'):
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
               CFLAGS=f'-O2 {FILE_PREFIX_MAP}', CXXFLAGS=f'-O2 {FILE_PREFIX_MAP}', CPPFLAGS=f'-I{DEPS}/include',
               LDFLAGS=f'-L{DEPS}/lib -Wl,-headerpad_max_install_names',
               PKG_CONFIG_LIBDIR=f'{DEPS}/lib/pkgconfig', PKG_CONFIG_PATH='')
    stamp = build / '.mactmnf-configure.json'
    identity = json.dumps({'source': LOCK[name], 'options': options,
                           'environment': {key: env[key] for key in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'MACOSX_DEPLOYMENT_TARGET')}}, sort_keys=True)
    if not (build / 'Makefile').exists() or not stamp.exists() or stamp.read_text() != identity:
        run(source / 'configure', '--build=aarch64-apple-darwin', '--host=x86_64-apple-darwin',
            f'--prefix={DEPS}', '--enable-shared', '--disable-static', *options, cwd=build, env=env,
            title=f'{name}: configure')
        stamp.write_text(identity)
    run('/usr/bin/make', f'-j{JOBS}', cwd=build, env=env, title=f'{name}: make -j{JOBS}')
    run('/usr/bin/make', f'-j{JOBS}', 'install', cwd=build, env=env, title=f'{name}: make install')


def dependencies():
    autotools('freetype', '--without-harfbuzz', '--without-brotli', '--without-png', '--without-bzip2')
    autotools('gmp', '--disable-cxx')
    autotools('nettle', '--disable-documentation', '--disable-openssl')
    autotools('libtasn1', '--disable-doc', '--disable-tools')
    autotools('libunistring', '--disable-rpath')
    # A neutral sysconfdir keeps the build's deps path out of the shipped
    # library; TMNF does not use a system gnutls config file. -ffile-prefix-map
    # only remaps __FILE__ and debug paths, not this configure constant.
    autotools('gnutls', '--disable-doc', '--disable-tests', '--disable-tools', '--disable-cxx',
              '--without-p11-kit', '--without-idn', '--without-brotli',
              '--without-zstd', '--with-included-unistring=no', '--disable-nls',
              '--sysconfdir=/etc')
    source = fetch('moltenvk')
    # This cache lives outside WORK. fetchDependencies also cleans MoltenVK itself.
    cache = source / 'External/build'
    if cache.exists():
        shutil.rmtree(cache)
    run('./fetchDependencies', '--macos', '--keep-cache', cwd=source, title='moltenvk: fetchDependencies --macos')
    with Progress('moltenvk: hashing the prepared tree'):
        prepared = inventory(source, lambda path: source_excluded('moltenvk', path))
    run('/usr/bin/make', 'macos', cwd=source, title='moltenvk: make macos (xcodebuild)')
    with Progress('moltenvk: checking the tree for drift'):
        changes = inventory_changes(prepared, inventory(source, lambda path: source_excluded('moltenvk', path)),
                                    'upstream/moltenvk')
    if changes:
        raise RuntimeError('MoltenVK sources changed during compilation. Run make build again.\n  '
                           + '\n  '.join(changes))
    shutil.copy2(source / 'Package/Release/MoltenVK/dylib/macOS/libMoltenVK.dylib', DEPS / 'lib')
    # Wine dlopens these by the install ID discovered during configure.
    for lib in (DEPS / 'lib').glob('*.dylib'):
        if not lib.is_symlink():
            run('install_name_tool', '-id', '@rpath/' + lib.name, lib, quiet=True)


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
            f'CC=clang -arch x86_64 {FILE_PREFIX_MAP}', f'CXX=clang++ -arch x86_64 {FILE_PREFIX_MAP}',
            # The PE (mingw) side is compiled separately; map its paths too.
            f'CROSSCFLAGS=-O2 -g {FILE_PREFIX_MAP}',
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
            env=dict(ENV, PKG_CONFIG_LIBDIR=f'{DEPS}/lib/pkgconfig', PKG_CONFIG_PATH=''),
            title='wine: configure')
        stamp.write_text(identity)
    run('/usr/bin/arch', '-x86_64', '/usr/bin/make', f'-j{JOBS}', cwd=build, title=f'wine: make -j{JOBS}')
    stage = WORK / 'wine-stage'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(exist_ok=True)
    run('/usr/bin/make', f'-j{JOBS}', 'install-lib', f'DESTDIR={stage}', cwd=build, title='wine: make install-lib')
    with Progress('wine: copying the staged tree to runtime/'):
        if RUNTIME.exists():
            shutil.rmtree(RUNTIME)
        shutil.copytree(stage / 'runtime', RUNTIME, symlinks=True)


def d9vk():
    source = fetch('d9vk')
    build = WORK / 'd9vk'
    if not (build / 'build.ninja').exists():
        run('meson', 'setup', '--cross-file', source / 'build-win32.txt', '--buildtype=release',
            '-Doptimization=3', build, source, title='d9vk: meson setup')
    run('ninja', '-C', build, f'-j{JOBS}', 'src/d3d9/d3d9.dll', title=f'd9vk: ninja d3d9.dll -j{JOBS}')
    (RUNTIME / 'lib/d9vk').mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / 'src/d3d9/d3d9.dll', RUNTIME / 'lib/d9vk/d3d9.dll')


def sidecar():
    source = fetch('x87sidecar')
    build = WORK / 'x87sidecar'
    run('cmake', '-S', source, '-B', build, '-DCMAKE_BUILD_TYPE=Release', title='x87sidecar: cmake configure')
    run('cmake', '--build', build, '--parallel', JOBS, title=f'x87sidecar: cmake build -j{JOBS}')
    run(build / 'bin/x87sidecar', '--probe', title='x87sidecar: probe')
    (RUNTIME / 'bin').mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / 'bin/x87sidecar', RUNTIME / 'bin/x87sidecar')


def session():
    build = WORK / 'session'
    build.mkdir(parents=True, exist_ok=True)
    binary = build / 'mactmnf-session.exe'
    run('x86_64-w64-mingw32-gcc', '-municode', '-O2', '-Wall', '-Wextra', '-Werror',
        FILE_PREFIX_MAP, ROOT / 'src/session.c', '-o', binary, cwd=build,
        title='session: x86_64-w64-mingw32-gcc session.c')
    (RUNTIME / 'libexec').mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, RUNTIME / 'libexec/mactmnf-session.exe')


def relocate():
    for lib in (DEPS / 'lib').glob('*.dylib'):
        target = RUNTIME / 'lib' / lib.name
        if target.exists() or target.is_symlink():
            target.unlink()
        if lib.is_symlink():
            target.symlink_to(os.readlink(lib))
        else:
            shutil.copy2(lib, target)
    binaries = [path for path in RUNTIME.rglob('*') if not path.is_symlink() and path.is_file()]
    with Progress('bundle: stripping, relinking and signing runtime binaries'):
        for index, binary in enumerate(binaries, 1):
            status(f'{index}/{len(binaries)} {binary.relative_to(RUNTIME)}')
            relocate_binary(binary)
    with Progress('bundle: writing config, build info and licenses'):
        bundle_metadata()
    run(RUNTIME / 'bin/wine', '--version', title='bundle: wine --version')
    run(RUNTIME / 'bin/x87sidecar', '--probe', title='bundle: x87sidecar --probe')
    with Progress('bundle: hashing the runtime manifest'):
        (RUNTIME / 'SHA256SUMS').write_text(runtime_manifest(inventory(RUNTIME)))
    with Progress('bundle: scanning the runtime for leaked build paths'):
        verify_no_build_paths()


def relocate_binary(binary):
    kind = run('file', '-b', binary, capture=True)
    if 'PE32' in kind:
        compiler = 'x86_64' if 'PE32+' in kind else 'i686'
        run(f'{compiler}-w64-mingw32-strip', '--strip-debug', binary, quiet=True)
    if 'Mach-O' in kind:
        relocate_macho(binary)


def relocate_macho(binary):
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
                run('install_name_tool', '-change', dep, '@rpath/' + name, binary, quiet=True)
        elif dep.startswith('@rpath/') and (binary.parent / name).exists():
            continue
        else:
            raise RuntimeError(f'Unbundled library in {binary}: {dep}')
    load = run('otool', '-l', binary, capture=True)
    rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', load)
    desired = '@loader_path/' + os.path.relpath(RUNTIME / 'lib', binary.parent)
    for old in set(rpaths):
        if old.startswith('/'):
            run('install_name_tool', '-delete_rpath', old, binary, quiet=True)
    if desired not in rpaths:
        run('install_name_tool', '-add_rpath', desired, binary, quiet=True)
    run('strip', '-x', binary, quiet=True)
    run('codesign', '--force', '--sign', '-', binary, quiet=True)


def bundle_metadata():
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


def runtime_manifest(contents):
    return ''.join(f'{entry[2]}  {name}\n' for name, entry in sorted(contents.items())
                   if entry[0] == 'file' and name != 'SHA256SUMS')


def verified_runtime():
    try:
        contents = {name: list(entry) for name, entry in verify_runtime(RUNTIME, VERSION).items()}
    except (OSError, ValueError) as error:
        raise RuntimeError(f'Runtime verification failed: {error}. Run make build.') from error
    # Preserve the completion stamp's JSON representation and root mode coverage.
    contents['.'] = ['directory', stat.S_IMODE(RUNTIME.stat().st_mode)]
    return contents


def verify_no_build_paths():
    # No shipped file may carry the absolute build path (it would leak the
    # maintainer's home directory). FILE_PREFIX_MAP keeps it out of compiled
    # binaries; this catches anything that still slips through, from any source.
    found = subprocess.run(['grep', '-rlF', str(ROOT), str(RUNTIME)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if found.returncode not in (0, 1):
        raise RuntimeError(f'Cannot verify runtime build paths (grep status {found.returncode}): '
                           f'{found.stderr.strip()}')
    leaked = [line for line in found.stdout.splitlines() if line]
    if found.returncode == 0 or leaked:
        listing = '\n  '.join(leaked[:20])
        more = '' if len(leaked) <= 20 else f'\n  ... and {len(leaked) - 20} more'
        raise RuntimeError(f'Build path {ROOT} leaked into the runtime:\n  {listing}{more}')


def archive_metadata(info):
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info


def source_archive(archive):
    def project_filter(info):
        path = Path(info.name).relative_to('mactmnf')
        return None if project_excluded(path) else archive_metadata(info)

    def source_filter(info):
        path = Path(info.name).relative_to('mactmnf/upstream')
        return None if source_excluded(path.parts[0], Path(*path.parts[1:])) else archive_metadata(info)

    with tarfile.open(archive, 'w:xz', preset=3) as tar:
        for entry in PROJECT_ENTRIES:
            tar.add(ROOT / entry, arcname=f'mactmnf/{entry}', filter=project_filter)
        for name in LOCK:
            tar.add(SOURCES / name, arcname=f'mactmnf/upstream/{name}', filter=source_filter)


def verify_completed():
    try:
        complete = json.loads((BUILD / 'build-inputs.json').read_text())
        if not isinstance(complete, dict) or complete.get('schema') != 2:
            raise RuntimeError('Old completion stamp. Run make build before packaging.')
        if complete.get('inputs') != build_inputs():
            raise RuntimeError('Build inputs changed. Run make build before packaging.')
        if complete.get('sources') != hashed_snapshot('Checking that the sources still match the completed build'):
            raise RuntimeError('Corresponding source changed. Run make build before packaging.')
        with Progress('Verifying the runtime tree'):
            runtime = verified_runtime()
        if complete.get('runtime') != runtime:
            raise RuntimeError('Runtime inventory changed. Run make build before packaging.')
    except (OSError, ValueError) as error:
        raise RuntimeError(f'Cannot verify the completed build: {error}. Run make build before packaging.') from error
    with Progress('Scanning the runtime for leaked build paths'):
        verify_no_build_paths()
    return complete


@release_lock()
def package():
    global LOCK, VERSION
    LOCK = json.loads((ROOT / 'config/sources.json').read_text())
    VERSION = (ROOT / 'VERSION').read_text().strip()
    complete = verify_completed()
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.package-', dir=DIST) as directory:
        staged = []
        for kind in ('runtime', 'source'):
            archive = Path(directory) / f'mactmnf-{VERSION}-{kind}.tar.xz'
            with Progress(f'Writing {archive.name}'):
                if kind == 'runtime':
                    with tarfile.open(archive, 'w:xz', preset=3) as tar:
                        tar.add(RUNTIME, arcname='runtime', filter=archive_metadata)
                else:
                    source_archive(archive)
            checksum = archive.with_name(archive.name + '.sha256')
            checksum.write_text(f'{digest(archive)}  {archive.name}\n')
            staged.append((archive, checksum))
        # Refuse publication if inputs were edited while the archives were written.
        if verify_completed() != complete:
            raise RuntimeError('Completed build changed during packaging. Retry with no build running.')
        for archive, checksum in staged:
            destination = DIST / archive.name
            checksum_destination = DIST / checksum.name
            # Interruption may leave a missing checksum, never an old one for new bytes.
            checksum_destination.unlink(missing_ok=True)
            archive.replace(destination)
            checksum.replace(checksum_destination)
            print(destination, flush=True)


def main():
    global JOBS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=['fetch', 'deps', 'wine', 'd9vk', 'sidecar', 'session', 'bundle', 'package'])
    args = parser.parse_args()
    if args.only == 'package':
        package()
        return
    try:
        jobs = os.environ.get('MACTMNF_JOBS', str(os.cpu_count() or 1))
        if not re.fullmatch(r'[0-9]+', jobs):
            raise ValueError
        JOBS = int(jobs)
        if JOBS < 1:
            raise ValueError
    except ValueError:
        parser.error('MACTMNF_JOBS must be a positive integer')
    print(f'Using {JOBS} compiler jobs (set MACTMNF_JOBS to change that).', flush=True)
    build_runtime(args.only)


@release_lock()
def build_runtime(only):
    global LOCK, VERSION
    complete = BUILD / 'build-inputs.json'
    complete.unlink(missing_ok=True)
    LOCK = json.loads((ROOT / 'config/sources.json').read_text())
    VERSION = (ROOT / 'VERSION').read_text().strip()
    SOURCES.mkdir(parents=True, exist_ok=True)
    if only == 'fetch':
        for name in LOCK:
            fetch(name)
        return
    actions = [('deps', dependencies, 'freetype, gmp, nettle, libtasn1, libunistring, gnutls and MoltenVK'),
               ('wine', wine, 'Wine, Unix side plus i386 and x86_64 PE DLLs'),
               ('d9vk', d9vk, 'D9VK d3d9.dll'), ('sidecar', sidecar, 'x87 sidecar'),
               ('session', session, 'session helper'), ('bundle', relocate, 'relocatable runtime tree')]
    # Prepare sources before discarding compiler output so a failed fetch keeps the cache.
    for name in (() if only == 'session' else LOCK):
        if only in (None, 'deps') or name == {'sidecar': 'x87sidecar'}.get(only, only) or (SOURCES / name).exists():
            fetch(name)
    inputs = build_inputs()
    # Keep completion tied to loaded code even if files change during source preparation.
    inputs.update(LOADED_PYTHON_INPUTS)
    with Progress('Checking the toolchain'):
        toolchain = toolchain_identity(only)
    prepared = hashed_snapshot('Hashing the project and prepared source trees', required=False)
    identity = {'inputs': inputs, 'toolchain': toolchain, 'sources': prepared['upstream']}
    # The helper-only toolchain must not replace or invalidate the full-build cache.
    work = WORK / 'session' if only == 'session' else WORK
    stamp = work / '.mactmnf-work.json'
    try:
        previous = json.loads(stamp.read_text())
    except (OSError, ValueError):
        previous = None
    if previous != identity and work.exists():
        print(f'Discarding compiler output in {work}: {discard_reason(previous, identity)}', flush=True)
        with Progress('Removing the old compiler output'):
            shutil.rmtree(work)
    elif previous == identity:
        print(f'Reusing compiler output in {work}: build inputs, toolchain and sources are unchanged.',
              flush=True)
    work.mkdir(parents=True, exist_ok=True)
    if inputs != build_inputs():
        raise RuntimeError('Build inputs changed during the build. Run make build again.')
    # This is cache input identity, not completion. Keep it across interrupted compilers;
    # Compiler input drift leaves this old identity in place for retry invalidation.
    # Other project files belong to the current-run and release source gates only.
    write_json(stamp, identity)
    if not only or only == 'deps':
        # Keep this absolute prefix stable for cached configure/Makefile paths.
        if DEPS.exists():
            shutil.rmtree(DEPS)
        (DEPS / 'lib').mkdir(parents=True)
    selected = [entry for entry in actions if not only or only == entry[0]]
    for index, (name, action, description) in enumerate(selected, 1):
        print(f'== [{index}/{len(selected)}] {name}: {description} ==', flush=True)
        action()
        if name == 'deps':
            # fetchDependencies materializes MoltenVK's nested source before Wine builds.
            after_deps = hashed_snapshot('Hashing the source trees after dependency compilation',
                                         required=False)
            changes = inventory_changes(prepared['project'], after_deps['project'], 'project')
            for component in sorted(prepared['upstream'].keys() | after_deps['upstream'].keys()):
                before = prepared['upstream'].get(component, {})
                after = after_deps['upstream'].get(component, {})
                if component == 'moltenvk':
                    # fetchDependencies materializes External itself on a fresh tree.
                    before = {key: value for key, value in before.items()
                              if key != 'External' and not key.startswith('External/')}
                    after = {key: value for key, value in after.items()
                              if key != 'External' and not key.startswith('External/')}
                changes.extend(inventory_changes(before, after, f'upstream/{component}'))
            if changes:
                raise RuntimeError('Sources changed during dependency compilation. Run make build again.\n  '
                                   + '\n  '.join(changes))
            prepared = after_deps
            if inputs != build_inputs():
                raise RuntimeError('Build inputs changed during the build. Run make build again.')
            identity['sources'] = prepared['upstream']
            write_json(stamp, identity)
    runtime = None
    if not only:
        with Progress('Verifying the runtime tree'):
            runtime = verified_runtime()
    if inputs != build_inputs():
        raise RuntimeError('Build inputs changed during the build. Run make build again.')
    changes = inventory_changes(prepared, hashed_snapshot('Checking the source trees for drift', required=False))
    if changes:
        raise RuntimeError('Sources changed during compilation. Run make build again.\n  '
                           + '\n  '.join(changes))
    if only:
        print(f'Finished {only}.', flush=True)
    else:
        write_json(complete, {'schema': 2, 'inputs': inputs, 'toolchain': toolchain,
                              'sources': prepared, 'runtime': runtime})
        print(f'Runtime ready: {RUNTIME}\nNext: make install', flush=True)


def discard_reason(previous, identity):
    """Explain, briefly, why cached compiler output no longer matches the build."""
    if not isinstance(previous, dict):
        return 'no valid cache identity was recorded.'
    reasons = []
    old_inputs, new_inputs = previous.get('inputs') or {}, identity['inputs']
    changed = sorted(name for name in old_inputs.keys() | new_inputs.keys()
                     if old_inputs.get(name) != new_inputs.get(name))
    if changed:
        listed = ', '.join(changed[:8]) + ('' if len(changed) <= 8 else f' and {len(changed) - 8} more')
        reasons.append(f'build inputs changed ({listed})')
    if previous.get('toolchain') != identity['toolchain']:
        reasons.append('the toolchain changed')
    if previous.get('sources') != identity['sources']:
        reasons.append('prepared sources changed')
    return '; '.join(reasons) + '.' if reasons else 'the recorded cache identity is unreadable.'


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        if f'Full log: {LOG}' in str(error) and LOG.exists():
            print('\n'.join(LOG.read_text(errors='replace').splitlines()[-35:]), file=sys.stderr)
        sys.exit(str(error))
    except KeyboardInterrupt:
        sys.exit('\nBuild interrupted. Run make build again to resume from the cached compiler output.')
