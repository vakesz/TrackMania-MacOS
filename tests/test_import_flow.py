import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from hang_guard import SUBPROCESS_TIMEOUT


ROOT = Path(__file__).resolve().parents[1]


class ImportFlowTests(unittest.TestCase):
    def run_import(self, title, source, home, wine_script):
        script = f"""
        . {str(ROOT / "src/shell/lib/common.sh")!r}
        . {str(ROOT / "src/shell/lib/commands/import.sh")!r}
        validate_launch_options() {{ :; }}
        require_owned_managed_layout() {{ return 0; }}
        {wine_script}
        import_source "$1"
        """
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "TRACKMANIA_MAC_TITLE": title,
                "TRACKMANIA_MAC_ROOT": str(home / "Games With Spaces" / title),
                "TRACKMANIA_MAC_CACHE": str(home / "Cache"),
                "PATH": "/usr/bin:/bin",
            }
        )
        return subprocess.run(
            ["/bin/bash", "-euc", script, "import-test", str(source)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )

    def test_online_titles_refuse_import(self):
        # Download titles install from their pinned installer; import never runs.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "setup.exe"
            source.write_text("not a real installer")
            for title in ("tmnf", "tmu", "tm-canyon"):
                with self.subTest(title=title):
                    result = self.run_import(
                        title, source, home, 'run_wine() { touch "$HOME/wine-ran"; }'
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("installs from its official download", result.stderr)
                    self.assertFalse((home / "wine-ran").exists())

    def test_retail_setup_runs_interactively_and_checks_expected_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "My Retail Setup.exe"
            source.touch()
            result = self.run_import(
                "tm-original",
                source,
                home,
                'run_wine() { mkdir -p "$(dirname "$GAME_EXE")"; '
                'touch "$GAME_EXE"; printf "wine:%s\\n" "$1"; }',
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"wine:{source}", result.stdout)
            self.assertIn("game files are installed", result.stdout)
            expected = (
                home / "Games With Spaces/tm-original/prefix/drive_c/"
                "Program Files (x86)/TrackMania/TrackMania.exe"
            )
            self.assertTrue(expected.is_file())

    def test_retail_setup_failure_names_expected_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "Setup.exe"
            source.touch()
            result = self.run_import(
                "tm-original",
                source,
                home,
                "run_wine() { return 0; }",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected game executable", result.stderr)
            self.assertIn("TrackMania.exe", result.stderr)

    UPDATE_SHA = "179e8d5d2437ff25d92a5fe840bd798df7b5522f7189a4a48e1b7fda8e097844"
    PATCH_SHA = "9e456a76c39f9e528e831512a94c640e58c266190a5bdb6820e2e2a0d70a0f21"
    # Every step the updates take is recorded in $HOME/steps instead of running.
    UPDATE_STUBS = (
        'step() { printf "%s\\n" "$*" >>"$HOME/steps"; }; '
        'download_verified() { step download "${1##*/}" "$3" "$4"; }; '
        'verify_file() { step verify-file "${1##*/}"; }; '
        'run_wine() { step setup "$1"; mkdir -p "$(dirname "$GAME_EXE")"; touch "$GAME_EXE"; }; '
        "stop_prefix_processes() { step stop; }; "
        'run_released_installer() { step silent "${1##*/}" "$TRACKMANIA_MAC_INNO_DIR"; '
        "TRACKMANIA_MAC_INSTALLER_PID=0; }; "
        'wait_inno_setup() { step wait "$3"; }; '
        "ensure_title_icon() { :; }; "
    )

    def steps(self, home):
        path = home / "steps"
        return path.read_text().splitlines() if path.exists() else []

    def done_log(self, home):
        return (
            home
            / "Games With Spaces/tm-sunrise-extreme/prefix/drive_c"
            / "trackmania-mac-updates.done"
        )

    def test_retail_setup_is_followed_by_the_pinned_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import("tm-sunrise-extreme", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            folder = "C:\\Program Files (x86)\\TrackMania Sunrise"
            self.assertEqual(
                self.steps(home),
                [
                    "download TmSunriseExtreme_update.exe {} 417826835".format(self.UPDATE_SHA),
                    "download TmSunriseExtreme_150_to_151.exe {} 21567628".format(self.PATCH_SHA),
                    "setup {}".format(source),
                    "stop",
                    "verify-file TmSunriseExtreme_update.exe",
                    "silent TmSunriseExtreme_update.exe " + folder,
                    "wait TrackMania Sunrise eXtreme update TmSunriseExtreme_update.exe",
                    "verify-file TmSunriseExtreme_150_to_151.exe",
                    "silent TmSunriseExtreme_150_to_151.exe " + folder,
                    "wait TrackMania Sunrise eXtreme update TmSunriseExtreme_150_to_151.exe",
                ],
            )
            self.assertEqual(
                self.done_log(home).read_text(), self.UPDATE_SHA + "\n" + self.PATCH_SHA + "\n"
            )

    def test_a_failed_update_download_runs_no_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + 'download_verified() { die "download failed"; }; ',
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("download failed", result.stderr)
            self.assertEqual(self.steps(home), [])

    def test_a_failed_update_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + 'wait_inno_setup() { step wait "$3"; '
                'case "$3" in *150_to_151*) die "update failed" ;; esac; }; ',
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("update failed", result.stderr)
            self.assertEqual(self.done_log(home).read_text(), self.UPDATE_SHA + "\n")

    def test_an_installed_game_gets_only_the_remaining_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            game = (
                home / "Games With Spaces/tm-sunrise-extreme/prefix/drive_c/"
                "Program Files (x86)/TrackMania Sunrise/TmSunrise.exe"
            )
            game.parent.mkdir(parents=True)
            game.touch()
            self.done_log(home).write_text(self.UPDATE_SHA + "\n")
            result = self.run_import("tm-sunrise-extreme", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = self.steps(home)
            self.assertNotIn("setup {}".format(source), steps)
            self.assertIn("applying the remaining official updates", result.stdout)
            self.assertIn("already installed", result.stdout)
            self.assertEqual(
                [step for step in steps if step.startswith("silent")],
                [
                    "silent TmSunriseExtreme_150_to_151.exe "
                    "C:\\Program Files (x86)\\TrackMania Sunrise"
                ],
            )
            self.assertEqual(
                self.done_log(home).read_text(), self.UPDATE_SHA + "\n" + self.PATCH_SHA + "\n"
            )

    def test_a_retail_title_without_updates_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import("tm-sunrise", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.steps(home), ["setup {}".format(source)])

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
