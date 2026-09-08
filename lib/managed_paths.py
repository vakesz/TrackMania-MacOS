#!/usr/bin/env python3
"""Check installation ownership without creating directories or invoking Wine."""

import argparse
import json
import os
import pwd
from pathlib import Path
import sys


def safe_path(value):
    path = Path(value)
    if (not path.is_absolute() or any(part in ('.', '..') for part in value.split('/'))
            or any(ord(char) < 32 for char in value) or path.is_symlink()):
        raise ValueError('Unsafe managed path: {!r}'.format(value))
    resolved = path.resolve()
    home = Path.home().resolve()
    roots = (home, Path('/private/tmp'), Path('/tmp').resolve())
    if resolved in roots or resolved in home.parents or not any(root in resolved.parents for root in roots):
        raise ValueError('Managed path must be below home or the temporary directory: {}'.format(value))
    return resolved


def check_layout(values, apps=False):
    if apps and any(not Path(value).is_absolute() for value in values):
        raise ValueError('App destinations must be absolute paths')
    # App bundles may live in /Applications or a user-selected apps volume.
    # Their existing-content ownership is checked separately before publication.
    paths = [Path(value).resolve() if apps else safe_path(value) for value in values]
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('Managed paths overlap: {} and {}'.format(left, right))


def check_prefix(value, creating=False):
    path = safe_path(value)
    if not path.exists():
        if creating:
            return
        raise ValueError('Wine prefix is missing: {}'.format(path))
    if not path.is_dir():
        raise ValueError('Wine prefix is not a directory: {}'.format(path))
    marker = path / '.mactmnf-prefix'
    initializing = path / '.mactmnf-initializing'
    if initializing.is_symlink() or (initializing.exists() and not initializing.is_file()):
        raise ValueError('Invalid prefix initialization marker')
    if marker.is_symlink():
        raise ValueError('Prefix ownership marker must not be a symlink')
    if marker.is_file():
        identity = marker.read_text().strip()
        if identity == 'tmnf:1':
            return
        # Shipped TMNF installations used an empty marker. Require Wine layout
        # or the existing interrupted-initialization marker before accepting it.
        if not identity and any((path / name).is_file() and not (path / name).is_symlink()
                                for name in ('system.reg', '.mactmnf-initializing')):
            return
        raise ValueError('Unrecognized prefix ownership: {}'.format(path))
    if creating and not any(path.iterdir()):
        return
    raise ValueError('Refusing a nonempty unmanaged prefix: {}'.format(path))


def existing_directory(path):
    """Allow absent paths, but never mistake a broken link for an absent path."""
    if not os.path.lexists(path):
        for parent in path.parents:
            if os.path.lexists(parent):
                if not parent.resolve(strict=True).is_dir():
                    raise ValueError('Expected a directory: {}'.format(parent))
                break
        return False
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError('Expected a directory: {}'.format(path))
    return True


def profile_documents(prefix, links=False):
    for root in (prefix / 'drive_c/users', prefix / 'drive_c/Documents and Settings'):
        if not existing_directory(root):
            continue
        for profile in root.iterdir():
            if not existing_directory(profile):
                continue
            for name in ('Documents', 'My Documents'):
                documents = profile / name
                if (links and documents.is_symlink()) or existing_directory(documents):
                    yield documents


def check_uninstall(prefix, userdata):
    """Read-only guard for known profile folders, not custom Shell Folders registry paths.

    Raises ValueError/OSError/RuntimeError if ownership or save safety is uncertain.
    The caller must hold the prefix operation lock through removal. Custom registry
    save locations require an explicit user backup; this is not a registry parser.
    """
    check_layout([str(prefix), str(userdata)])
    check_prefix(str(prefix))
    prefix = Path(prefix).resolve()

    def inspect(path, ancestors=()):
        resolved = path.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError('Cyclic save location: {}'.format(path))
        if resolved.is_dir():
            for child in path.iterdir():
                inspect(child, ancestors + (resolved,))
        elif prefix in resolved.parents:
            raise ValueError('Back up user files before uninstalling: {}'.format(path))

    for documents in profile_documents(prefix):
        if prefix in documents.resolve(strict=True).parents:
            inspect(documents)
        else:
            # Outside Documents may contain a save-folder alias back into Wine.
            for name in ('TmForever', 'Config', 'Replays', 'Tracks'):
                child = documents / name
                if os.path.lexists(child):
                    inspect(child)


def setup_documents(prefix, userdata):
    """Redirect empty folders and proven fresh Wine defaults, never host data."""
    check_layout([str(prefix), str(userdata)])
    check_prefix(str(prefix))
    prefix, userdata = Path(prefix).resolve(), Path(userdata).resolve()
    baseline = prefix / '.mactmnf-profile-baseline'
    provenance = None
    if os.path.lexists(baseline):
        if baseline.is_symlink() or not baseline.is_file():
            raise ValueError('Invalid profile initialization provenance')
        provenance = json.loads(baseline.read_text())
        if (not isinstance(provenance, dict) or provenance.get('version') != 1
                or not isinstance(provenance.get('home'), str)
                or not isinstance(provenance.get('account_home', provenance['home']), str)
                or not isinstance(provenance.get('profiles'), list)
                or not all(isinstance(item, str) for item in provenance['profiles'])):
            raise ValueError('Invalid profile initialization provenance')

    def fresh_default(path, name):
        return (provenance is not None and path.is_symlink()
                and path.parent.resolve(strict=True) == path.parent
                and str(path.parent.relative_to(prefix)) not in provenance['profiles']
                and any(path.readlink() == Path(home) / name
                        for home in (provenance['home'], provenance.get('account_home', provenance['home']))))

    documents = list(profile_documents(prefix, links=True))
    if not documents:
        raise ValueError('Wine created no known Documents folder; back up custom save locations and repair the profile')
    replacements = []
    for path in documents:
        if fresh_default(path, 'Documents'):
            replacements.append(path)
            continue
        if path.is_symlink() and any(path.readlink() in (candidate, Path(candidate.name))
                                     and candidate.parent == path.parent and fresh_default(candidate, 'Documents')
                                     for candidate in documents if candidate != path):
            continue
        resolved = path.resolve(strict=True)
        if resolved == userdata:
            continue
        # Wine commonly exposes My Documents as an alias of Documents.
        if path.is_symlink() and any(candidate != path and candidate.resolve(strict=True) == resolved
                                     and (not candidate.is_symlink() or fresh_default(candidate, 'Documents'))
                                     for candidate in documents):
            continue
        if path.is_symlink() or prefix not in resolved.parents or any(path.iterdir()):
            raise ValueError('Existing Documents arrangement preserved: {}. Back up user files and explicitly link Documents to {} before retrying'.format(path, userdata))
        replacements.append(path)
    userdata.mkdir(parents=True, exist_ok=True)
    for path in replacements:
        target = path.readlink() if path.is_symlink() else None
        if target is not None:
            path.unlink()  # Only the Wine-created link, never its host target.
        else:
            path.rmdir()  # Refuses if files appeared after preflight.
        try:
            path.symlink_to(userdata, target_is_directory=True)
        except OSError:
            if target is None:
                path.mkdir()
            else:
                path.symlink_to(target, target_is_directory=True)
            raise
    # Shield fresh Wine defaults from macOS privacy prompts. Preexisting and
    # custom profile links remain untouched, including links into the prefix.
    for profile in {path.parent for path in documents}:
        for name in ('Desktop', 'Downloads', 'Music', 'Pictures', 'Videos'):
            path = profile / name
            if fresh_default(path, name) or (name == 'Videos' and fresh_default(path, 'Movies')):
                target = path.readlink()
                path.unlink()
                try:
                    path.mkdir()
                except OSError:
                    path.symlink_to(target, target_is_directory=True)
                    raise


def record_profile_baseline(prefix):
    """Persist pre-Wineboot profiles once so retries recognize our default links."""
    check_prefix(str(prefix))
    prefix = Path(prefix).resolve()
    baseline = prefix / '.mactmnf-profile-baseline'
    if os.path.lexists(baseline):
        if baseline.is_symlink() or not baseline.is_file():
            raise ValueError('Invalid profile initialization provenance')
        return  # Never recapture Wine-created profiles on an interrupted retry.
    profiles = []
    for root in (prefix / 'drive_c/users', prefix / 'drive_c/Documents and Settings'):
        if existing_directory(root):
            profiles.extend(str(path.relative_to(prefix)) for path in root.iterdir())
    with baseline.open('x') as stream:
        # Wine on macOS may use the OS account home rather than environment HOME.
        # Record names only; replacing our new links never requires opening targets.
        json.dump(dict(version=1, home=str(Path.home().resolve()),
                       account_home=pwd.getpwuid(os.getuid()).pw_dir, profiles=profiles), stream)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('path', 'layout', 'app-layout', 'prefix', 'new-prefix', 'uninstall', 'setup-documents', 'profile-baseline'))
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args()
    try:
        if args.command in ('uninstall', 'setup-documents'):
            if len(args.paths) != 2:
                raise ValueError('{} requires PREFIX USERDATA'.format(args.command))
            if args.command == 'uninstall':
                check_uninstall(*args.paths)
                print('warning: Custom Windows Shell Folders registry save locations are not checked; back them up explicitly before removal.', file=sys.stderr)
            else:
                setup_documents(*args.paths)
        elif args.command == 'profile-baseline':
            if len(args.paths) != 1:
                raise ValueError('profile-baseline requires PREFIX')
            record_profile_baseline(args.paths[0])
        elif args.command in ('layout', 'app-layout'):
            check_layout(args.paths, apps=args.command == 'app-layout')
        elif args.command == 'path':
            for path in args.paths:
                safe_path(path)
        else:
            for path in args.paths:
                check_prefix(path, creating=args.command == 'new-prefix')
    except (OSError, ValueError, RuntimeError) as error:
        print('error: {}'.format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
