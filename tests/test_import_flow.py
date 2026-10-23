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
        . {str(ROOT / 'src/shell/lib/commands/import.sh')!r}
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

    def steam_fixture(self, home):
        source = home / "Steam Game"
        source.mkdir()
        (source / "TrackmaniaTurbo.exe").write_text("exe")
        prefix = home / "Games With Spaces/tm-turbo/prefix"
        (prefix / "drive_c").mkdir(parents=True)
        return source, prefix

    def test_steam_copy_is_staged_and_moved_into_place(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source, prefix = self.steam_fixture(home)
            result = self.run_import("tm-turbo", source, home, "")
            self.assertEqual(result.returncode, 0, result.stderr)
            game = prefix / "drive_c/Program Files (x86)/TrackMania Turbo"
            self.assertEqual((game / "TrackmaniaTurbo.exe").read_text(), "exe")
            self.assertEqual(list(prefix.glob(".import-staging.*")), [])

    def test_steam_copy_refuses_symlink_in_source_or_destination_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source, prefix = self.steam_fixture(home)
            (source / "link").symlink_to("/etc/hosts")
            result = self.run_import("tm-turbo", source, home, "")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlinks", result.stderr)
            (source / "link").unlink()

            outside = home / "outside"
            outside.mkdir()
            (prefix / "drive_c/Program Files (x86)").symlink_to(outside)
            result = self.run_import("tm-turbo", source, home, "")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(list(prefix.glob(".import-staging.*")), [])


if __name__ == "__main__":
    unittest.main()
