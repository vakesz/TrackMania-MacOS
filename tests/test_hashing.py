"""sha256_file in common.sh: hashlib path, shasum fallback, identical output."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def shell_hash(path, after_source="", **env):
    return subprocess.run(
        [
            "bash",
            "-euc",
            ". src/shell/lib/common.sh; " + after_source + 'sha256_file "$1"',
            "fixture",
            str(path),
        ],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def shell_run(command, **env):
    return subprocess.run(
        ["bash", "-euc", ". src/shell/lib/common.sh; " + command],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def shasum(path):
    return subprocess.run(
        ["shasum", "-a", "256", str(path)], capture_output=True, text=True, check=True
    ).stdout.split()[0]


class Sha256FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sample(self, name, size):
        path = self.root / name
        path.write_bytes(os.urandom(size))
        return path

    def test_matches_shasum_byte_for_byte(self):
        # Sizes straddle the 1 MiB read chunk.
        for size in (1, 1000, 1024 * 1024 - 1, 1024 * 1024, 3 * 1024 * 1024 + 7):
            with self.subTest(size=size):
                path = self.sample("data%d" % size, size)
                result = shell_hash(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, shasum(path) + "\n")

    def test_empty_file(self):
        path = self.root / "empty"
        path.write_bytes(b"")
        result = shell_hash(path)
        self.assertEqual(
            result.stdout, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
        )

    def test_falls_back_to_shasum_without_python(self):
        path = self.sample("data", 4096)
        # common.sh needs Python to load, so make it unusable only afterwards.
        result = shell_hash(path, after_source="have() { return 1; }; ", TMNF_PYTHON="python3")
        self.assertEqual(result.stdout, shasum(path) + "\n")

    def test_module_prints_only_the_digest(self):
        path = self.sample("data", 2048)
        result = subprocess.run(
            ["python3", "-m", "tmnf.install.hashing", "sha256", str(path)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src/python")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.stdout, shasum(path) + "\n")
        self.assertEqual(result.stderr, "")

    def test_module_prints_one_digest_per_path_in_order(self):
        paths = [
            self.sample(name, size) for name, size in (("b", 10), ("a", 2 * 1024 * 1024), ("c", 0))
        ]
        result = subprocess.run(
            ["python3", "-m", "tmnf.install.hashing", "sha256", *map(str, paths)],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src/python")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "".join(shasum(path) + "\n" for path in paths))

    def test_module_prints_nothing_when_any_path_is_unreadable(self):
        path = self.sample("data", 16)
        result = subprocess.run(
            [
                "python3",
                "-m",
                "tmnf.install.hashing",
                "sha256",
                str(path),
                str(self.root / "missing"),
            ],
            env={**os.environ, "PYTHONPATH": str(ROOT / "src/python")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("missing", result.stderr)


class VerifyFilesTests(unittest.TestCase):
    """verify_files: PATH EXPECTED pairs hashed in one Python start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = []
        for name in ("one", "two with space", "three", "four"):
            path = self.root / name
            path.write_text("contents of " + name)
            self.files.append((path, shasum(path)))

    def pairs(self, files=None):
        return " ".join(
            "{!r} {!r}".format(str(path), digest) for path, digest in (files or self.files)
        )

    def verify(self, files=None, after_source="", **env):
        return shell_run(after_source + "verify_files " + self.pairs(files), **env)

    def test_all_matching_succeeds(self):
        self.assertEqual(self.verify().returncode, 0)

    def test_any_mismatch_fails_wherever_it_is(self):
        for index in range(len(self.files)):
            with self.subTest(index=index):
                files = list(self.files)
                files[index] = (files[index][0], "0" * 64)
                self.assertNotEqual(self.verify(files).returncode, 0)

    def test_any_missing_file_fails(self):
        for index in range(len(self.files)):
            with self.subTest(index=index):
                files = list(self.files)
                files[index] = (self.root / "absent", files[index][1])
                self.assertNotEqual(self.verify(files).returncode, 0)

    def test_unreadable_file_is_a_mismatch_not_an_abort(self):
        path, digest = self.files[0]
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        self.assertNotEqual(self.verify().returncode, 0)

    def test_uses_one_python_start_for_every_file(self):
        shim = self.root / "shim"
        starts = self.root / "starts"
        shim.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>{}\nexec python3 "$@"\n'.format(starts))
        shim.chmod(0o755)
        result = self.verify(TMNF_PYTHON=str(shim))
        self.assertEqual(result.returncode, 0, result.stderr)
        hashing = [
            line for line in starts.read_text().splitlines() if "tmnf.install.hashing" in line
        ]
        self.assertEqual(len(hashing), 1, hashing)
        self.assertEqual(hashing[0].count(str(self.root)), len(self.files))

    def test_falls_back_to_shasum_without_python(self):
        fallback = "have() { return 1; }; "
        self.assertEqual(self.verify(after_source=fallback, TMNF_PYTHON="python3").returncode, 0)
        files = list(self.files)
        files[2] = (files[2][0], "0" * 64)
        self.assertNotEqual(
            self.verify(files, after_source=fallback, TMNF_PYTHON="python3").returncode, 0
        )

    def test_falls_back_to_shasum_when_the_hashing_module_fails(self):
        broken = self.root / "broken"
        broken.write_text(
            '#!/bin/bash\nif test "$2" = tmnf.install.hashing; then exit 1; fi\nexec python3 "$@"\n'
        )
        broken.chmod(0o755)
        self.assertEqual(self.verify(TMNF_PYTHON=str(broken)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
