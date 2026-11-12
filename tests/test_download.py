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

    def run_wait(self, installer_body):
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
            'pid=$!; wait_inno_setup "$pid" "$LOG" Test; echo "returned $?"'
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


if __name__ == "__main__":
    unittest.main()
