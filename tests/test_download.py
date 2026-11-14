from pathlib import Path
import hashlib
import os
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BODY = b"installer bytes"


class DownloadVerified(unittest.TestCase):
    """download_verified with a stub curl that records its arguments."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-download-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "bin").mkdir()
        curl = self.root / "bin/curl"
        curl.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$@" >"$ARGS_FILE"\n'
            'while test "$#" -gt 0; do\n'
            '  if test "$1" = --output; then printf "%s" "$BODY" >"$2"; fi\n'
            "  shift\ndone\n"
        )
        curl.chmod(0o755)

    def download(self, *extra):
        destination = self.root / "cache" / "installer.exe"
        result = subprocess.run(
            [
                "/bin/bash",
                "-euc",
                '. src/shell/lib/common.sh; download_verified "$@"',
                "fixture",
                "https://example.invalid/installer.exe",
                str(destination),
                hashlib.sha256(BODY).hexdigest(),
                *extra,
            ],
            cwd=ROOT,
            env={
                "PATH": "{}:/usr/bin:/bin".format(self.root / "bin"),
                "HOME": str(self.root),
                "ARGS_FILE": str(self.root / "args"),
                "BODY": BODY.decode(),
            },
            text=True,
            capture_output=True,
        )
        arguments = (
            (self.root / "args").read_text().splitlines() if (self.root / "args").exists() else []
        )
        return result, arguments

    def test_size_limit_is_passed_to_curl_and_q_stays_first(self):
        result, arguments = self.download("1211926014")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments[0], "-q")
        self.assertEqual(arguments[arguments.index("--max-filesize") + 1], "1211926014")

    def test_no_size_limit_when_the_size_is_unknown(self):
        for extra in ((), ("",)):
            with self.subTest(extra=extra):
                result, arguments = self.download(*extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(arguments[0], "-q")
                self.assertNotIn("--max-filesize", arguments)

    def test_dropped_connections_are_retried(self):
        # --retry alone skips errors such as a reset connection (curl exit 56).
        result, arguments = self.download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--retry-all-errors", arguments)

    def test_invalid_size_limit_is_rejected_before_downloading(self):
        result, arguments = self.download("12abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid download size limit", result.stderr)
        self.assertEqual(arguments, [])


class InnoStaging(unittest.TestCase):
    """The unpacked inner installer must not outlive the install, success or not."""

    def run_install(self, wait_stub):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-inno-")
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        staging = home / "cache" / "setup.inner"
        script = (
            ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
            "download_pinned_installer() { :; }; "
            '_cache_installer_path() { printf "%s\\n" "$HOME/cache/setup.exe"; }; '
            'unpack_inner_inno() { mkdir -p "$HOME/cache/setup.inner"; '
            'printf "%s\\n" "$HOME/cache/setup.inner/setup.exe"; }; '
            "run_released_installer() { TRACKMANIA_MAC_INSTALLER_PID=0; }; "
            + wait_stub
            + " verify_installed_game() { :; }; say() { :; }; "
            + ""
            + 'install_pinned_inno_game online-retail; echo "after install"'
        )
        result = subprocess.run(
            ["/bin/bash", "-euc", script],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": str(home), "TRACKMANIA_MAC_TITLE": "tmu"},
            text=True,
            capture_output=True,
            timeout=30,
        )
        return result, staging

    def test_staging_is_removed_after_a_successful_install(self):
        result, staging = self.run_install("wait_inno_setup() { :; };")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after install", result.stdout)
        self.assertFalse(staging.exists())

    def test_staging_is_removed_when_setup_fails(self):
        result, staging = self.run_install('wait_inno_setup() { die "setup failed"; };')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("setup failed", result.stderr)
        self.assertNotIn("after install", result.stdout)
        self.assertFalse(staging.exists())

    def test_only_inner_directories_are_removed(self):
        with tempfile.TemporaryDirectory(prefix="trackmania-mac-inno-") as directory:
            keep = Path(directory).resolve() / "cache"
            keep.mkdir()
            subprocess.run(
                [
                    "/bin/bash",
                    "-euc",
                    ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
                    'remove_inno_staging "$1"',
                    "fixture",
                    str(keep),
                ],
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin", "HOME": directory, "TRACKMANIA_MAC_TITLE": "tmu"},
                check=True,
            )
            self.assertTrue(keep.is_dir())


class StopInstallerTree(unittest.TestCase):
    """wait_inno_setup must not block on an installer that survives wineserver -k."""

    def run_wait(self, installer_body, max_wait=""):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-wait-")
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        log = home / "install.log"
        stop = home / "stop"
        server = home / "wineserver"
        server.write_text(f"#!/bin/sh\ntouch '{stop}'\n")
        server.chmod(0o755)
        pidfile = home / "pids"
        script = (
            ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
            "wine_binary() { echo wine; }; "
            f'wineserver_binary() {{ echo "{server}"; }}; '
            'without_operation_lock() { "$@"; }; say() { :; }; '
            f'LOG="{log}"; PIDS="{pidfile}"; STOP="{stop}"; '
            "( " + installer_body + " ) & "
            f'pid=$!; wait_inno_setup "$pid" "$LOG" Test {max_wait}; echo "returned $?"'
        )
        self.addCleanup(self.reap, pidfile)
        started = time.monotonic()
        result = subprocess.run(
            ["/bin/bash", "-euc", script],
            cwd=ROOT,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "TRACKMANIA_MAC_TITLE": "tmu",
                "TRACKMANIA_MAC_INSTALL_KILL_GRACE": "1",
            },
            text=True,
            capture_output=True,
            timeout=60,
        )
        return result, time.monotonic() - started, pidfile

    @staticmethod
    def reap(pidfile):
        if not pidfile.exists():
            return
        for pid in pidfile.read_text().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

    @staticmethod
    def alive(pid):
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        return True

    def test_stubborn_installer_tree_is_killed_after_the_grace_period(self):
        body = (
            'printf "Installation process succeeded.\\nDeinitializing Setup.\\n" > "$LOG"; '
            "( trap '' TERM; exec sleep 120 ) & "
            'echo "$(sh -c "echo \\$PPID") $!" > "$PIDS"; '
            "trap '' TERM; while :; do sleep 1; done"
        )
        result, elapsed, pidfile = self.run_wait(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("returned 0", result.stdout)
        self.assertIn("Stopping the installer processes that did not exit", result.stderr)
        self.assertLess(elapsed, 30)
        pids = pidfile.read_text().split()
        self.assertEqual(len(pids), 2)
        for pid in pids:
            self.assertFalse(self.alive(pid), f"process {pid} survived")

    def test_installer_that_exits_after_wineserver_kill_needs_no_sigkill(self):
        body = (
            'printf "Installation process succeeded.\\nDeinitializing Setup.\\n" > "$LOG"; '
            'while [ ! -e "$STOP" ]; do sleep 0.2; done'
        )
        result, _, _ = self.run_wait(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("returned 0", result.stdout)
        self.assertNotIn("Stopping the installer processes", result.stderr)

    def test_cancelled_setup_that_lingers_is_stopped_and_reported(self):
        body = (
            'printf "Got EAbort exception.\\nDeinitializing Setup.\\nLog closed.\\n" > "$LOG"; '
            'while [ ! -e "$STOP" ]; do sleep 0.2; done'
        )
        result, elapsed, _ = self.run_wait(body)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("returned", result.stdout)
        self.assertIn("Test setup was cancelled or stopped early", result.stderr)
        self.assertLess(elapsed, 30)

    def test_a_longer_limit_can_be_given(self):
        body = 'while [ ! -e "$STOP" ]; do sleep 0.2; done'
        result, elapsed, _ = self.run_wait(body, max_wait="2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Test installation timed out", result.stderr)
        self.assertLess(elapsed, 30)


class InteractiveSetup(unittest.TestCase):
    """A setup the user clicks through keeps /DIR and /LOG but drops the silent switches."""

    def test_interactive_flags(self):
        script = (
            ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
            'run_wine() { printf "%s\\n" "$@"; }; '
            'TRACKMANIA_MAC_INNO_DIR="C:\\Games\\Here" run_interactive_installer /x/setup.exe Test; '
            'wait "$TRACKMANIA_MAC_INSTALLER_PID"'
        )
        result = subprocess.run(
            ["/bin/bash", "-euc", script],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "TRACKMANIA_MAC_TITLE": "tmn-eswc"},
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        notice, *arguments = result.stdout.splitlines()
        self.assertTrue(notice.startswith("The Test setup opens in its own window."), notice)
        self.assertEqual(
            arguments,
            [
                "/x/setup.exe",
                "/SP-",
                "/NORESTART",
                "/LOG=C:\\trackmania-mac-install.log",
                "/DIR=C:\\Games\\Here",
            ],
        )


class LayeredInstall(unittest.TestCase):
    """tmn-eswc: downloads first, the guided setup, its updates, then the overlay."""

    def run_install(self, extra_stubs=""):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-layered-")
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve()
        script = (
            ". src/shell/lib/common.sh; . src/shell/lib/install/game.sh; "
            'step() { printf "%s\\n" "$*" >>"$HOME/steps"; }; '
            "download_pinned_installer() { step download-installer; }; "
            'download_verified() { step download "${2##*/}" "$3" "$4"; }; '
            'run_interactive_installer() { step interactive "${1##*/}" "$TRACKMANIA_MAC_INNO_DIR"; '
            'TRACKMANIA_MAC_INSTALLER_PID=0; mkdir -p "${GAME_EXE%/*}"; : >"$GAME_EXE"; }; '
            'run_released_installer() { step silent "${1##*/}"; TRACKMANIA_MAC_INSTALLER_PID=0; }; '
            'wait_inno_setup() { step wait "$3" "${4:-default}"; }; '
            "stop_prefix_wine() { step stop-installer-tree; }; "
            "stop_prefix_processes() { step stop; }; "
            'verify_file() { step verify-file "${1##*/}"; }; '
            'trackmania_mac_python() { step overlay "${@:3}"; }; '
            'verify_installed_game() { step verify "$2"; }; ' + extra_stubs + "install_game"
        )
        result = subprocess.run(
            ["/bin/bash", "-euc", script],
            cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": str(home), "TRACKMANIA_MAC_TITLE": "tmn-eswc"},
            text=True,
            capture_output=True,
            timeout=30,
        )
        steps = home / "steps"
        return result, (steps.read_text().splitlines() if steps.exists() else []), home

    def test_steps_run_in_order(self):
        result, steps, home = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        cache = home / "Library/TrackManiaMacOS/cache/games/tmn-eswc"
        game_dir = (
            home / "Library/TrackManiaMacOS/games/tmn-eswc/prefix/drive_c/Program Files (x86)"
        ) / "TmNationsESWC"
        self.assertEqual(
            steps,
            [
                "download-installer",
                "download tmnationseswc_175_to_179_setup.exe "
                "8f42e6b0f9058360ce497363cbcbc022fd9d4385a84606db9b0d65a2abcdb5f7 19194902",
                "download Win10Patch.tar.gz "
                "b2ec4f34a0fc1009175792f9f2e3130bdf242379235a1ad4c50019117ebb7460 67468269",
                "interactive TmNationsESWC_Setup.exe C:\\Program Files (x86)\\TmNationsESWC",
                "wait TrackMania Nations ESWC 3600",
                "stop",
                "verify-file tmnationseswc_175_to_179_setup.exe",
                "silent tmnationseswc_175_to_179_setup.exe",
                "wait TrackMania Nations ESWC update tmnationseswc_175_to_179_setup.exe default",
                "overlay apply {} b2ec4f34a0fc1009175792f9f2e3130bdf242379235a1ad4c50019117ebb7460 "
                "{} --last TmNationsESWC.exe --remove tmn.exe --remove tmn.dll --remove tmn.dat".format(
                    cache / "Win10Patch.tar.gz", game_dir
                ),
                "verify 381213323695ba9e3c3e19beafd6e063f3a155c1034838b317c40187915b2560",
            ],
        )
        self.assertIn("Adding the TrackMania Nations ESWC 1.8.0 game files", result.stdout)

    def test_a_setup_that_installed_elsewhere_stops_before_the_updates(self):
        result, steps, _ = self.run_install(
            "run_interactive_installer() { step interactive; TRACKMANIA_MAC_INSTALLER_PID=0; }; "
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("setup closed without installing into", result.stderr)
        self.assertIn("keep the suggested folder", result.stderr)
        self.assertEqual(steps[-1], "stop")

    def test_a_changed_update_download_stops_before_it_runs(self):
        result, steps, _ = self.run_install(
            'verify_file() { step verify-file "${1##*/}"; return 1; }; '
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "The downloaded TrackMania Nations ESWC update tmnationseswc_175_to_179_setup.exe "
            "no longer matches its pinned checksum. Run install again to download it again.",
            result.stderr,
        )
        self.assertNotIn("silent tmnationseswc_175_to_179_setup.exe", steps)

    def test_a_partial_install_is_finished_without_the_setup(self):
        prepare = 'mkdir -p "${GAME_EXE%/*}"; : >"$GAME_EXE"; '
        result, steps, _ = self.run_install(
            prepare + 'verify_file() { step verify-file "${1##*/}"; test "$1" != "$GAME_EXE"; }; '
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Finishing the TMN 1.8.0 installation at", result.stdout)
        self.assertEqual(
            [step.split(" ", 1)[0] for step in steps],
            [
                "verify-file",
                "download",
                "download",
                "verify-file",
                "silent",
                "wait",
                "overlay",
                "verify",
            ],
        )
        self.assertNotIn("download-installer", steps)
        self.assertFalse(any(step.startswith("interactive") for step in steps))

    def test_a_partial_install_after_a_failed_setup_runs_the_setup_again(self):
        # The setup's log does not record success, so the base install is redone.
        prepare = (
            'mkdir -p "${GAME_EXE%/*}"; : >"$GAME_EXE"; '
            'printf "Got EAbort exception.\\n" '
            '>"$TRACKMANIA_MAC_PREFIX/drive_c/trackmania-mac-install.log"; '
        )
        result, steps, _ = self.run_install('mkdir -p "$TRACKMANIA_MAC_PREFIX/drive_c"; ' + prepare)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("download-installer", steps)
        self.assertTrue(any(step.startswith("interactive") for step in steps))

    def test_a_failed_overlay_stops_the_install(self):
        result, steps, _ = self.run_install("trackmania_mac_python() { return 1; }; ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not add the TrackMania Nations ESWC game files.", result.stderr)
        self.assertNotIn("verify", [step.split(" ", 1)[0] for step in steps])


if __name__ == "__main__":
    unittest.main()
