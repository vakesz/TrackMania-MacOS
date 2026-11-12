import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from trackmania_mac.install import version as project_version

ROOT = Path(__file__).resolve().parents[1]
SHELL_READER = ROOT / "src/shell/lib/version.sh"

# pyproject.toml fixtures and the version both readers must agree on (None: rejected).
FIXTURES = {
    "plain": ('[project]\nname = "x"\nversion = "1.2.3"\n', "1.2.3"),
    "key in an earlier table": (
        '[tool.other]\nversion = "wrong"\n\n[project]\nversion = "1.2.3"\n',
        "1.2.3",
    ),
    "spacing, comments and CRLF": (
        '# version = "wrong"\r\n[project]  # metadata\r\n  version="2.0rc1"  # bump\r\n',
        "2.0rc1",
    ),
    "array table before": ('[[tool.x]]\nversion = "no"\n[project]\nversion="4"\n', "4"),
    "no trailing newline": ('[project]\nversion = "3.1"', "3.1"),
    "missing key": ('[project]\nname = "x"\n\n[tool.other]\nversion = "1.0"\n', None),
    "key only in a subtable": ('[project]\nname = "x"\n[project.urls]\nversion = "1.0"\n', None),
    "no project table": ('version = "1.0"\n', None),
    "unquoted value": ("[project]\nversion = 1.0\n", None),
    "empty value": ('[project]\nversion = ""\n', None),
    "space in value": ('[project]\nversion = "1.0 beta"\n', None),
    "path in value": ('[project]\nversion = "../1.0"\n', None),
    "slash in value": ('[project]\nversion = "1.0/x"\n', None),
    "quote in value": ('[project]\nversion = "1"2"\n', None),
}


def shell_version(directory, env=None):
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            '. "$1"; trackmania_mac_project_version "$2"',
            "test",
            SHELL_READER,
            directory,
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        assert result.stderr.strip(), "the shell reader failed without a message"
        assert not result.stdout, result.stdout
        return None
    return result.stdout.rstrip("\n")


def python_version(directory):
    try:
        return project_version.read(directory)
    except project_version.VersionError:
        return None


class ProjectMetadataTests(unittest.TestCase):
    def test_pyproject_version_matches_uv_lock(self):
        lock = (ROOT / "uv.lock").read_text()
        match = re.search(
            r'^\[\[package\]\]\nname = "trackmania-mac"\nversion = "([^"]+)"$', lock, re.M
        )
        self.assertIsNotNone(match, "uv.lock has no trackmania-mac package entry")
        self.assertEqual(match.group(1), project_version.read(ROOT))

    def test_there_is_no_version_file(self):
        self.assertFalse((ROOT / "VERSION").exists())

    def test_shell_and_python_readers_agree_on_the_project(self):
        version = project_version.read(ROOT)
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+")
        self.assertEqual(shell_version(ROOT), version)

    def test_the_shell_reader_runs_no_external_command(self):
        # The installed hub calls the CLI with a minimal PATH.
        self.assertEqual(
            shell_version(ROOT, env={"PATH": "/nonexistent"}), project_version.read(ROOT)
        )

    def test_shell_and_python_readers_agree_on_fixtures(self):
        for name, (text, expected) in FIXTURES.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "pyproject.toml").write_bytes(text.encode())
                self.assertEqual(python_version(directory), expected)
                self.assertEqual(shell_version(directory), expected)

    def test_a_missing_pyproject_fails_both_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                project_version.read(directory)
            self.assertIsNone(shell_version(directory))


if __name__ == "__main__":
    unittest.main()
