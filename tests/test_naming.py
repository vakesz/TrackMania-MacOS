"""No name of an earlier release outside the places that recognise one.

The project was renamed from `tmnf` to `trackmania-mac` (package trackmania_mac,
CLI trackmania-mac, settings TRACKMANIA_MAC_*). Only the title id `tmnf` and what
derives from it keep that name, and old names may appear only where an explicit,
labelled entry of ALLOWED says why. Markdown, LICENSE, website/, .claude/ and the
issue templates are maintained separately and not scanned.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tempfile
import typing
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# What an old name looks like, by kind.
PATTERNS = {
    "python package": r"\btmnf\.(?:app|build|guard|hub|install|probes|session)\b|src/python/tmnf\b",
    "cli": r"\bcli/tmnf\b|[\"']cli[\"']\s*[/,]\s*[\"']tmnf[\"']|\b(?:by|outside) tmnf\b",
    "env prefix": r"(?<![A-Za-z0-9_])TMNF_[A-Z*]",
    "marker or tombstone": r"\.tmnf-[a-z]|\btmnf-(?:removing|relink|operation|session)",
    "log prefix": r"\btmnf-(?:state|mon|map|run):",
    "bundle id": r"\blocal\.tmnf\.",
    "repository name": r"(?i:mactmnf)",
    "file prefix map": r"=/tmnf\b",
    "wrapper executable": r"MacOS/TMNF\b|<string>TMNF</string>|\.TMNF\.XXXXXX",
}

# Each entry: why it may keep an old name, and (file glob, line regex or None for
# the whole file). Drop an entry and the test names every line it covered.
ALLOWED: typing.Dict[str, typing.List[typing.Tuple[str, typing.Optional[str]]]] = {
    "legacy-names modules: the one place each layer recognises earlier names": [
        ("src/shell/lib/legacy.sh", None),
        ("src/python/trackmania_mac/legacy.py", None),
        ("src/swift/Sources/TrackManiaHub/HubLegacy.swift", None),
    ],
    "test fixtures that build an earlier release's install, settings or log": [
        ("tests/test_relocate.py", None),
        ("tests/test_markers.py", None),
        ("tests/test_legacy_env.py", None),
        ("tests/test_naming.py", None),
        ("tests/test_session_report.py", r"tmnf-(?:state|mon|map|run):"),
        ("tests/test_operations.py", r"\.tmnf-operation\.lock"),
    ],
    "registry: the bundle ids earlier releases gave each title's apps": [
        ("config/titles/*.json", r'"legacy_bundle_id_(?:game|settings)"'),
    ],
}
EXCLUDED = ("*.md", "LICENSE", "website/*", ".claude/*", ".github/ISSUE_TEMPLATE/*")


def allowed(path: str, line: str) -> bool:
    for entries in ALLOWED.values():
        for glob, pattern in entries:
            if fnmatch.fnmatch(path, glob) and (pattern is None or re.search(pattern, line)):
                return True
    return False


def scan(root: Path, paths: typing.Iterable[str]) -> typing.List[str]:
    """`path:line: kind` for every old name outside ALLOWED."""
    found = []
    rules = {kind: re.compile(pattern) for kind, pattern in PATTERNS.items()}
    for path in sorted(paths):
        if any(fnmatch.fnmatch(path, glob) for glob in EXCLUDED):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for kind, rule in rules.items():
                if rule.search(line) and not allowed(path, line):
                    found.append("{}:{}: {}".format(path, number, kind))
    return found


def project_files() -> typing.List[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class NamingTests(unittest.TestCase):
    def test_no_old_name_outside_the_allowed_places(self):
        self.assertEqual(scan(ROOT, project_files()), [])

    def test_a_planted_old_name_is_reported(self):
        with tempfile.TemporaryDirectory(prefix="trackmania-mac-naming-") as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/tool.sh").write_text('echo "$TMNF_FOO"\ntouch .tmnf-bar\nok\n')
            (root / "src/module.py").write_text(
                "import tmnf.hub\nlocal.tmnf.game\nMACTMNF_X\nContents/MacOS/TMNF\n"
            )
            (root / "src/legacy.sh").write_text("TMNF_FOO\n")
            (root / "notes.md").write_text("TMNF_FOO\n")
            found = scan(root, ["src/tool.sh", "src/module.py", "notes.md"])
        self.assertEqual(
            found,
            [
                "src/module.py:1: python package",
                "src/module.py:2: bundle id",
                "src/module.py:3: repository name",
                "src/module.py:4: wrapper executable",
                "src/tool.sh:1: env prefix",
                "src/tool.sh:2: marker or tombstone",
            ],
        )

    def test_the_title_id_itself_is_not_an_old_name(self):
        with tempfile.TemporaryDirectory(prefix="trackmania-mac-naming-") as directory:
            root = Path(directory)
            (root / "a.py").write_text('title = "tmnf"\npath = "games/tmnf/prefix"\nTMNF\n')
            self.assertEqual(scan(root, ["a.py"]), [])


if __name__ == "__main__":
    unittest.main()
