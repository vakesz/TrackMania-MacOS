import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from hang_guard import SUBPROCESS_TIMEOUT


ROOT = Path(__file__).resolve().parents[1]


class ImportFlowTests(unittest.TestCase):
    def run_import(self, title, source, home, wine_script, track_pack=None):
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
        env.pop("TRACKMANIA_MAC_TRACK_PACK", None)
        if track_pack is not None:
            env["TRACKMANIA_MAC_TRACK_PACK"] = track_pack
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
    OVERLAY_SHA = "de3ec4cce962ba2d1283bb37fb7458c53be684355fadaf79bcab597d3411f883"
    # Every step the import takes is recorded in $HOME/steps instead of running.
    # The game executable has the pinned hash only when $HOME/pinned exists, and
    # only the NX_COMPAT flag is missing when $HOME/nx-pending exists.
    UPDATE_STUBS = (
        'step() { printf "%s\\n" "$*" >>"$HOME/steps"; }; '
        'download_verified() { step download "${1##*/}" "$3" "$4"; }; '
        'verify_file() { step verify-file "${1##*/}"; '
        'if test "$1" = "$GAME_EXE"; then test -f "$HOME/pinned"; fi; }; '
        "apply_overlay() { step overlay; }; "
        "apply_nx_compat() { step nx; }; "
        "verify_installed_game() { step verify-game; }; "
        'game_exe_finish_pending() { test -f "$HOME/nx-pending"; }; '
        "sync_game_save_links() { if title_has game-saves; then step save-links; fi; }; "
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
                    "download TrackmaniaSunriseExtreme_Win10Patch.zip {} 135814066".format(
                        self.OVERLAY_SHA
                    ),
                    "setup {}".format(source),
                    "stop",
                    "verify-file TmSunriseExtreme_update.exe",
                    "silent TmSunriseExtreme_update.exe " + folder,
                    "wait TrackMania Sunrise eXtreme update TmSunriseExtreme_update.exe",
                    "verify-file TmSunriseExtreme_150_to_151.exe",
                    "silent TmSunriseExtreme_150_to_151.exe " + folder,
                    "wait TrackMania Sunrise eXtreme update TmSunriseExtreme_150_to_151.exe",
                    "overlay",
                    "nx",
                    "verify-game",
                    "save-links",
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

    def installed_game(self, home):
        game = (
            home / "Games With Spaces/tm-sunrise-extreme/prefix/drive_c/"
            "Program Files (x86)/TrackMania Sunrise/TmSunrise.exe"
        )
        game.parent.mkdir(parents=True)
        game.touch()
        return game

    def test_an_installed_game_gets_only_the_remaining_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            self.installed_game(home)
            self.done_log(home).write_text(self.UPDATE_SHA + "\n")
            result = self.run_import("tm-sunrise-extreme", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = self.steps(home)
            self.assertNotIn("setup {}".format(source), steps)
            self.assertIn("applying the remaining install steps", result.stdout)
            self.assertIn("already installed", result.stdout)
            self.assertEqual(
                [step for step in steps if step.startswith("silent")],
                [
                    "silent TmSunriseExtreme_150_to_151.exe "
                    "C:\\Program Files (x86)\\TrackMania Sunrise"
                ],
            )
            self.assertEqual(steps[-4:], ["overlay", "nx", "verify-game", "save-links"])
            self.assertEqual(
                self.done_log(home).read_text(), self.UPDATE_SHA + "\n" + self.PATCH_SHA + "\n"
            )

    def test_an_import_that_lacks_only_the_nx_flag_sets_only_the_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            self.installed_game(home)
            (home / "nx-pending").touch()
            result = self.run_import("tm-sunrise-extreme", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = [step for step in self.steps(home) if not step.startswith("download")]
            self.assertEqual(
                steps, ["verify-file TmSunrise.exe", "nx", "verify-game", "save-links"]
            )
            self.assertIn("Finishing", result.stdout)

    def test_a_finished_import_runs_nothing_again(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            self.installed_game(home)
            (home / "pinned").touch()
            result = self.run_import("tm-sunrise-extreme", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = [step for step in self.steps(home) if not step.startswith("download")]
            self.assertEqual(steps, ["verify-file TmSunrise.exe", "save-links"])
            self.assertIn("1.5.1 is already installed", result.stdout)

    PACK_SHA = "3c34c28b4dd195064663b85d40457880164fa354dde02691e20334a92bab3abf"
    PACK_DOWNLOAD = "download CustomChallengesTMSX.zip {} 70062938".format(PACK_SHA)
    PACK_STUBS = "apply_track_pack() { step pack; }; "

    def pack_marker(self, home):
        return (
            home
            / "Games With Spaces/tm-sunrise-extreme/UserData/TrackMania"
            / "GameData/Tracks/Challenges/Downloaded/.trackmania-mac-track-pack"
        )

    def test_the_track_pack_downloads_before_the_setup_and_unpacks_after_the_links(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + self.PACK_STUBS,
                track_pack="on",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = self.steps(home)
            self.assertEqual(steps[3], self.PACK_DOWNLOAD)
            self.assertEqual(steps[4], "setup {}".format(source))
            self.assertEqual(steps[-2:], ["save-links", "pack"])

    def test_the_track_pack_is_off_unless_asked_for(self):
        for value in (None, "off"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                source = home / "TmSunriseSetup.exe"
                source.touch()
                result = self.run_import(
                    "tm-sunrise-extreme",
                    source,
                    home,
                    self.UPDATE_STUBS + self.PACK_STUBS,
                    track_pack=value,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(self.PACK_DOWNLOAD, self.steps(home))
                self.assertNotIn("pack", self.steps(home))

    def test_an_unknown_track_pack_value_stops_the_import(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import(
                "tm-sunrise-extreme", source, home, self.UPDATE_STUBS, track_pack="yes"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unknown TRACKMANIA_MAC_TRACK_PACK", result.stderr)
            self.assertEqual(self.steps(home), [])

    def test_a_failed_track_pack_only_warns(self):
        for stubs, message in (
            (
                'download_verified() { case "$1" in *CustomChallenges*) return 1 ;; esac; '
                'step download "${1##*/}"; }; ',
                "Could not download the community tracks",
            ),
            ("apply_track_pack() { step pack; return 1; }; ", "Could not add the community tracks"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                source = home / "TmSunriseSetup.exe"
                source.touch()
                result = self.run_import(
                    "tm-sunrise-extreme",
                    source,
                    home,
                    self.UPDATE_STUBS + self.PACK_STUBS + stubs,
                    track_pack="on",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertIn("game files are installed", result.stdout)
                self.assertIn("setup {}".format(source), self.steps(home))

    def test_a_finished_import_with_the_pack_on_adds_only_the_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            self.installed_game(home)
            (home / "pinned").touch()
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + self.PACK_STUBS,
                track_pack="on",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(self.PACK_DOWNLOAD, self.steps(home))
            steps = [step for step in self.steps(home) if not step.startswith("download")]
            self.assertEqual(steps, ["verify-file TmSunrise.exe", "save-links", "pack"])

    def test_a_recorded_pack_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            self.installed_game(home)
            (home / "pinned").touch()
            marker = self.pack_marker(home)
            marker.parent.mkdir(parents=True)
            marker.write_text(self.PACK_SHA + "\n")
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + self.PACK_STUBS,
                track_pack="on",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(self.PACK_DOWNLOAD, self.steps(home))
            self.assertNotIn("pack", self.steps(home))

    def test_a_title_without_a_pack_ignores_the_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "Setup.exe"
            source.touch()
            result = self.run_import(
                "tm-original", source, home, self.UPDATE_STUBS + self.PACK_STUBS, track_pack="on"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.steps(home), ["setup {}".format(source)])

    def test_a_failed_overlay_stops_the_import(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "TmSunriseSetup.exe"
            source.touch()
            result = self.run_import(
                "tm-sunrise-extreme",
                source,
                home,
                self.UPDATE_STUBS + 'apply_overlay() { die "overlay failed"; }; ',
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("overlay failed", result.stderr)
            self.assertNotIn("nx", self.steps(home))
            self.assertNotIn("game files are installed", result.stdout)

    def test_a_retail_title_without_updates_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "Setup.exe"
            source.touch()
            result = self.run_import("tm-original", source, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.steps(home), ["setup {}".format(source)])

    def disc_folder(self, home, *names):
        disc = home / "Disc Copy"
        disc.mkdir()
        for name in names:
            (disc / name).touch()
        return disc

    def test_a_disc_folder_runs_its_setup_program(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            disc = self.disc_folder(home, "CdAutoRun.exe", "Setup.exe", "TrackMania.ico")
            result = self.run_import("tm-original", disc, home, self.UPDATE_STUBS)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.steps(home), ["setup {}".format(disc / "Setup.exe")])

    def test_a_disc_folder_needs_exactly_one_setup_program(self):
        for names, message in (
            (("CdAutoRun.exe",), "No setup program"),
            (("Setup.exe", "TmSetup.EXE"), "More than one setup program"),
        ):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                disc = self.disc_folder(home, *names)
                result = self.run_import("tm-original", disc, home, self.UPDATE_STUBS)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.steps(home), [])

    def test_a_disc_folder_ignores_a_symlinked_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            disc = self.disc_folder(home)
            (home / "elsewhere.exe").touch()
            (disc / "Setup.exe").symlink_to(home / "elsewhere.exe")
            result = self.run_import("tm-original", disc, home, self.UPDATE_STUBS)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No setup program", result.stderr)

    # attach makes the mount point hold a setup program; detach records itself.
    HDIUTIL_STUB = (
        'hdiutil() { case "$1" in '
        'attach) step attach "${@: -1}"; mkdir -p "${@: -2:1}"; '
        'touch "${@: -2:1}/Setup.exe";; '
        'detach) step detach; rm -rf -- "$2";; esac; }; '
    )

    def test_a_disc_image_is_mounted_for_the_whole_import(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            image = home / "TM-Original.iso"
            image.touch()
            result = self.run_import(
                "tm-original", image, home, self.UPDATE_STUBS + self.HDIUTIL_STUB
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = self.steps(home)
            self.assertEqual(steps[0], "attach {}".format(image))
            self.assertRegex(steps[1], r"^setup .*/import-mount\.\w+/Setup\.exe$")
            self.assertEqual(steps[2:], ["detach"])
            self.assertEqual(list((home / "Cache").glob("import-mount.*")), [])

    def test_a_failed_setup_still_unmounts_the_disc_image(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            image = home / "TM-Original.iso"
            image.touch()
            result = self.run_import(
                "tm-original",
                image,
                home,
                self.UPDATE_STUBS + self.HDIUTIL_STUB + "run_wine() { return 1; }; ",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("retail installer failed", result.stderr)
            self.assertEqual(self.steps(home)[-1], "detach")
            self.assertEqual(list((home / "Cache").glob("import-mount.*")), [])

    def test_steam_copy_titles_take_only_a_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "setup.exe"
            source.touch()
            result = self.run_import(
                "tm-turbo", source, home, 'run_wine() { touch "$HOME/wine-ran"; }'
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("imports from its Steam game folder", result.stderr)
            self.assertFalse((home / "wine-ran").exists())

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
