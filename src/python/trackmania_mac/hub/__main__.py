#!/usr/bin/env python3
"""Console entry: python -m trackmania_mac.hub [snapshot|titles|migrate|clean|run ...]"""

from __future__ import annotations

import argparse
import json
import sys

from . import registry, snapshot, run as hub_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trackmania-mac-hub", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("snapshot", help="Print the schema 3 hub snapshot JSON")
    subparsers.add_parser("migrate", help="Move files of an earlier release into the data folder")
    subparsers.add_parser("titles", help='List titles as "id: name [status kind]"')
    subparsers.add_parser("diagnostics", help="Write a diagnostics zip for TRACKMANIA_MAC_TITLE")
    sub_run = subparsers.add_parser("run", help="Run a title action through the shell flows")
    sub_run.add_argument("title")
    sub_run.add_argument("action")
    sub_run.add_argument("--source", default=None)
    sub_run.add_argument("--runtime", default=None)
    sub_run.add_argument("--include-saves", action="store_true")
    for flag in hub_run.LAUNCH_FLAGS:
        sub_run.add_argument(flag, default=None)
    sub_clean = subparsers.add_parser(
        "clean", help="Remove cached downloads, unused runtimes or logs not in use"
    )
    sub_clean.add_argument("kind")

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "snapshot":
            print(json.dumps(snapshot.snapshot(), separators=(",", ":"), sort_keys=True))
            return 0
        if arguments.command == "titles":
            for title in registry.load_all():
                print(f"{title.id}: {title.name} [{title.status} {title.installer}]")
            return 0
        if arguments.command == "migrate":
            hub_run.migrate()
        if arguments.command == "clean":
            hub_run.clean(arguments.kind)
        if arguments.command == "diagnostics":
            from . import diagnostics

            return diagnostics.main()
        hub_run.run(
            arguments.title,
            arguments.action,
            arguments.source,
            arguments.runtime,
            include_saves=arguments.include_saves,
            launch_options={
                "--window-size": arguments.window_size,
                "--audio": arguments.audio,
                "--x87": arguments.x87,
            },
        )
    except registry.RegistryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
