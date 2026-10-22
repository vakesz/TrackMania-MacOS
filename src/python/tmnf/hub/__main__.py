#!/usr/bin/env python3
"""Console entry: python -m tmnf.hub [snapshot|titles|run ...]"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from . import registry, snapshot, run as hub_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='tmnf-hub', description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('snapshot', help='Print the schema 2 hub snapshot JSON')
    subparsers.add_parser('titles', help='List titles as "id: name [status kind]"')
    sub_run = subparsers.add_parser('run', help='Run a title action through the shell flows')
    sub_run.add_argument('title')
    sub_run.add_argument('action')
    sub_run.add_argument('--source', default=None)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == 'snapshot':
            print(json.dumps(snapshot.snapshot(), separators=(',', ':'), sort_keys=True))
            return 0
        if arguments.command == 'titles':
            override = os.environ.get('TMNF_TITLES_DIR')
            directory = pathlib.Path(override) if override else None
            for title in registry.load_all(directory):
                print(f'{title.id}: {title.name} [{title.status} {title.installer}]')
            return 0
        hub_run.run(arguments.title, arguments.action, arguments.source)
    except registry.RegistryError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
