import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / "src/shell/lib/common.sh"
PATCH = ROOT / "src/shell/lib/install/patch.sh"
OLD = "202001010000"


def bash(script, *args, home, **extra):
    env = dict(os.environ, HOME=str(home), PATH="/usr/bin:/bin", **extra)
    return subprocess.run(
        ["/bin/bash", "-c", script, "cleanup-test", *map(str, args)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


def age(path):
    subprocess.run(["touch", "-t", OLD, str(path)], check=True)


class SweepStaleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dir = self.root / "dir with spaces"
        self.dir.mkdir()

    def sweep(self, glob=".staging.*", kind=None):
        extra = f" {kind}" if kind else ""
        return bash(
            f'. {str(COMMON)!r}; sweep_stale "$1" "$2"{extra}; echo rc=$?',
            self.dir,
            glob,
            home=self.root,
        )

    def test_removes_old_matching_dirs(self):
        stale = self.dir / ".staging.abc"
        (stale / "inner").mkdir(parents=True)
        age(stale)
        result = self.sweep()
        self.assertIn("rc=0", result.stdout)
        self.assertFalse(stale.exists())

    def test_keeps_fresh_dirs(self):
        fresh = self.dir / ".staging.new"
        fresh.mkdir()
        self.sweep()
        self.assertTrue(fresh.exists())

    def test_keeps_non_matching_names(self):
        other = self.dir / "keepme"
        other.mkdir()
        age(other)
        self.sweep()
        self.assertTrue(other.exists())

    def test_does_not_follow_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        victim = outside / "file"
        victim.touch()
        age(victim)
        age(outside)
        link = self.dir / ".staging.link"
        link.symlink_to(outside)
        subprocess.run(["touch", "-h", "-t", OLD, str(link)], check=True)
        self.sweep()
        self.assertTrue(link.is_symlink())
        self.assertTrue(victim.exists())

    def test_sweeps_old_partial_files_only_when_asked(self):
        partial = self.dir / "a.zip.partial.XYZ"
        partial.touch()
        age(partial)
        self.sweep("*.partial.*")
        self.assertTrue(partial.exists())
        result = self.sweep("*.partial.*", "f")
        self.assertIn("rc=0", result.stdout)
        self.assertFalse(partial.exists())

    def test_missing_directory_is_not_an_error(self):
        result = bash(
            f'. {str(COMMON)!r}; sweep_stale "$1/nope" "*"; echo rc=$?', self.root, home=self.root
        )
        self.assertIn("rc=0", result.stdout)


class SwapTmloaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.prefix = self.root / "prefix"
        self.target = self.prefix / "drive_c/Program Files/TMLoader"
        self.target.mkdir(parents=True)
        (self.target / "old.txt").write_text("old")
        self.new = self.root / "new"
        self.new.mkdir()
        (self.new / "new.txt").write_text("new")

    def swap(self, stub=""):
        script = f"""
        . {str(COMMON)!r}
        . {str(PATCH)!r}
        {stub}
        (swap_tmloader_dir "$1")
        echo rc=$?
        """
        return bash(script, self.new, home=self.root, TMNF_PREFIX=str(self.prefix))

    def test_success_replaces_and_removes_old(self):
        result = self.swap()
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertTrue((self.target / "new.txt").exists())
        self.assertFalse((self.target / "old.txt").exists())
        self.assertEqual(list(self.target.parent.glob(".tmloader-old.*")), [])

    def test_failed_second_move_restores_old(self):
        stub = """mv() {
          case "$1" in *new) return 1 ;; esac
          command mv "$@"
        }"""
        result = self.swap(stub)
        self.assertIn("rc=1", result.stdout, result.stderr)
        self.assertEqual((self.target / "old.txt").read_text(), "old")
        self.assertFalse((self.target / "new.txt").exists())
        self.assertEqual(list(self.target.parent.glob(".tmloader-old.*")), [])


if __name__ == "__main__":
    unittest.main()
