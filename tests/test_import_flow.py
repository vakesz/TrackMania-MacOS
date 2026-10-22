import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ImportFlowTests(unittest.TestCase):
    def run_import(self, title, source, home, wine_script):
        script = f"""
        . {str(ROOT / 'src/shell/lib/common.sh')!r}
        . {str(ROOT / 'src/shell/lib/app/import.sh')!r}
        validate_launch_options() {{ :; }}
        require_owned_prefix() {{ return 0; }}
        validate_managed_layout() {{ return 0; }}
        {wine_script}
        import_source "$1"
        """
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "TMNF_TITLE": title,
            "TMNF_ROOT": str(home / "Games With Spaces" / title),
            "TMNF_CACHE": str(home / "Cache"),
            "PATH": "/usr/bin:/bin",
        })
        return subprocess.run(
            ["/bin/bash", "-euc", script, "import-test", str(source)],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=20,
        )

    def test_retail_setup_runs_interactively_and_checks_expected_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "My Retail Setup.exe"
            source.touch()
            result = self.run_import(
                "tm-original", source, home,
                'run_wine() { mkdir -p "$(dirname "$GAME_EXE")"; '
                'touch "$GAME_EXE"; printf "wine:%s\\n" "$1"; }',
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"wine:{source}", result.stdout)
            self.assertIn("game files are installed", result.stdout)
            expected = (home / "Games With Spaces/tm-original/prefix/drive_c/"
                        "Program Files (x86)/TrackMania/TrackMania.exe")
            self.assertTrue(expected.is_file())

    def test_retail_setup_failure_names_expected_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "Setup.exe"
            source.touch()
            result = self.run_import(
                "tm-original", source, home, "run_wine() { return 0; }",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected game executable", result.stderr)
            self.assertIn("TrackMania.exe", result.stderr)


if __name__ == "__main__":
    unittest.main()
