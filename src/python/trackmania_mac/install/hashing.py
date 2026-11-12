"""Streaming file digests for the shell installer.

CLI: python -m trackmania_mac.install.hashing sha256 PATH [PATH ...]
Prints one lowercase hex digest per path, in order, and nothing else; if any path
cannot be read it prints nothing and fails. hashlib streams several times faster
than shasum, which matters for the multi-hundred-megabyte runtime archive, and
several paths share one interpreter start.
"""

import hashlib
import sys

CHUNK = 1024 * 1024


def sha256_file(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            result.update(chunk)
    return result.hexdigest()


def main(argv):
    if len(argv) < 3 or argv[1] != "sha256":
        print(
            "usage: python -m trackmania_mac.install.hashing sha256 PATH [PATH ...]",
            file=sys.stderr,
        )
        return 2
    try:
        digests = [sha256_file(path) for path in argv[2:]]
    except OSError as error:
        print("hashing: {}".format(error), file=sys.stderr)
        return 1
    print("\n".join(digests))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
