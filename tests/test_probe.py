import errno
import json
import os
from pathlib import Path
import runpy
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "src/python/tmnf/probes/probe_runner.py"
# The descendant holds the output pipe open and ignores graceful termination.
TREE = ("import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
        "print(p.pid, flush=True); ")


class ProbeRunner(unittest.TestCase):
    def test_group_cleanup_precedes_reaping(self):
        main = runpy.run_path(str(RUNNER))["main"]
        killpg, waitpid = os.killpg, os.waitpid
        cleanup = []

        def kill_group(pid, sig):
            cleanup.append(pid)
            return killpg(pid, sig)

        def reap(pid, options):
            self.assertEqual(cleanup, [pid], "leader reaped before group cleanup")
            return waitpid(pid, options)

        for handler in (signal.SIG_DFL, signal.SIG_IGN):
            with self.subTest(sigchld=handler):
                cleanup.clear()
                previous = signal.signal(signal.SIGCHLD, handler)
                try:
                    with mock.patch.object(sys, "argv", [str(RUNNER), "5", sys.executable,
                                                        "-c", "raise SystemExit(7)"]), \
                            mock.patch.object(os, "killpg", side_effect=kill_group) as kill, \
                            mock.patch.object(os, "waitpid", side_effect=reap) as wait:
                        self.assertEqual(main(), 7)
                    kill.assert_called_once_with(cleanup[0], signal.SIGKILL)
                    self.assertTrue(wait.called)
                    self.assertEqual(signal.getsignal(signal.SIGCHLD), handler)
                finally:
                    signal.signal(signal.SIGCHLD, previous)

    def test_exit_before_kqueue_registration_still_cleans_up_before_wait(self):
        main = runpy.run_path(str(RUNNER))["main"]
        calls = mock.Mock()
        calls.process.pid = 12345
        calls.process.wait.return_value = 7
        with mock.patch.object(sys, "argv", [str(RUNNER), "5", "fake-probe"]), \
                mock.patch.object(subprocess, "Popen", return_value=calls.process), \
                mock.patch.object(select, "kqueue") as kqueue, \
                mock.patch.object(os, "killpg", calls.killpg):
            kqueue.return_value.control.side_effect = ProcessLookupError(errno.ESRCH, "exited")
            self.assertEqual(main(), 7)
        self.assertEqual(calls.mock_calls, [mock.call.killpg(12345, signal.SIGKILL),
                                           mock.call.process.wait(timeout=1)])

    def test_exit_status_output_and_launch_errors(self):
        result = subprocess.run([sys.executable, str(RUNNER), "5", sys.executable,
                                 "-c", "import sys; print('probe output'); sys.exit(7)"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "probe output\n")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(RUNNER), "5", directory + "/missing"],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 127)
        self.assertIn("cannot run probe", result.stderr)
        for timeout in ("0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout):
                result = subprocess.run([sys.executable, str(RUNNER), timeout, sys.executable],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)

    def test_deadline_kills_descendants_but_not_unrelated_same_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, WINEPREFIX=directory)
            survivor = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=env)
            try:
                result = subprocess.run([sys.executable, str(RUNNER), "1", sys.executable,
                                         "-c", TREE + "time.sleep(60)"], env=env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 124)
                self.assertIn("exceeded 1 seconds", result.stderr)
                self.assertTrue(result.stdout.strip().isdigit())
                # communicate() returning also proves the descendant's pipe closed.
                self.assertIsNone(survivor.poll())
            finally:
                survivor.kill()
                survivor.wait(timeout=5)

    def test_success_also_cleans_up_descendants(self):
        result = subprocess.run([sys.executable, str(RUNNER), "5", sys.executable, "-c", TREE],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().isdigit())

    def test_interruption_cleans_up_descendants(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                process = subprocess.Popen([sys.executable, str(RUNNER), "5", sys.executable,
                                            "-c", TREE + "time.sleep(60)"],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertTrue(process.stdout.readline().strip().isdigit())
                    process.send_signal(sig)
                    _, error = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + sig, error)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=10)


class SmokeScripts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mactmnf-probe-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("src/shell/tools/probes", "src/shell/lib", "src/python/tmnf/probes",
                     "config/titles", "bin", "prefix", "home", "source/bin"):
            (self.root / name).mkdir(parents=True)
        for script in (ROOT / "src/shell/tools/probes").glob("smoke-*.sh"):
            shutil.copy2(script, self.root / "src/shell/tools/probes" / script.name)
        shutil.copy2(RUNNER, self.root / "src/python/tmnf/probes/probe_runner.py")
        shutil.copy2(ROOT / "src/python/tmnf/__init__.py", self.root / "src/python/tmnf/__init__.py")
        shutil.copy2(ROOT / "src/python/tmnf/probes/__init__.py",
                     self.root / "src/python/tmnf/probes/__init__.py")
        shutil.copy2(ROOT / "src/shell/lib/common.sh", self.root / "src/shell/lib/common.sh")
        shutil.copy2(ROOT / "src/shell/lib/titles.sh", self.root / "src/shell/lib/titles.sh")
        shutil.copy2(ROOT / "config/titles/tmnf.json", self.root / "config/titles/tmnf.json")
        shutil.copy2(ROOT / "VERSION", self.root / "VERSION")
        (self.root / "prefix/system.reg").touch()
        self.log = self.root / "calls.jsonl"
        stub = self.root / "bin/stub"
        stub.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "with open(os.environ['PROBE_LOG'], 'a') as log:\n"
            "    log.write(json.dumps({'name': os.path.basename(sys.argv[0]), "
            "'args': sys.argv[1:], 'prefix': os.getenv('WINEPREFIX'), 'pgid': os.getpgrp(), "
            "'x87': os.getenv('TMNF_X87'), 'hook': os.getenv('ROSETTA_X87_PATH'), "
            "'storm_pc24': os.getenv('STORM_PC24'), 'storm_case': os.getenv('STORM_CASE'), "
            "'fma': os.getenv('X87_ENABLE_FMA_CONTRACT'), 'fast_round': os.getenv('X87_FAST_ROUND'), "
            "'ir_disabled': os.getenv('X87_DISABLE_X87_IR'), "
            "'fusions_disabled': os.getenv('X87_DISABLE_ALL_FUSIONS'), "
            "'fpr_limit': os.getenv('X87_FPR_POOL_LIMIT')}) + '\\n')\n"
        )
        stub.chmod(0o755)
        for name in ("wine", "x87sidecar", "i686-w64-mingw32-gcc", "x86_64-w64-mingw32-gcc"):
            (self.root / "bin" / name).symlink_to(stub)
        (self.root / "bin/python3").symlink_to(sys.executable)
        # No inherited installation configuration, live prefix, or Wine binary.
        self.env = {"PATH": str(self.root / "bin") + ":/usr/bin:/bin",
                    "HOME": str(self.root / "home"), "TMPDIR": str(self.root),
                    "PROBE_LOG": str(self.log), "TMNF_ROOT": str(self.root),
                    "TMNF_PREFIX": str(self.root / "prefix"),
                    "TMNF_RUNTIME": str(self.root / "absent-runtime"),
                    "TMNF_WINE": str(self.root / "bin/wine"),
                    "TMNF_SIDECAR": str(self.root / "bin/x87sidecar"),
                    "TMNF_X87_SRC": str(self.root / "source"),
                    "TMNF_X87": "off", "TMNF_AUDIO": "on",
                    "ROSETTA_X87_PATH": "/must-not-use-inherited-hook"}

    def test_all_windows_smokes_with_fake_dependencies(self):
        expected = {"audio": 4, "network": 2, "shaders": 12, "sync": 4, "x87": 2}
        for name, calls in expected.items():
            with self.subTest(smoke=name):
                self.log.write_text("")
                result = subprocess.run(["/bin/bash", str(self.root / "src/shell/tools/probes" / ("smoke-" + name + ".sh"))],
                                        env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                recorded = [json.loads(line) for line in self.log.read_text().splitlines()]
                wines = [call for call in recorded if call["name"] == "wine"]
                self.assertEqual(len(wines), calls)
                self.assertTrue(all(call["prefix"] == self.env["TMNF_PREFIX"] for call in wines))
                self.assertTrue(all(call["pgid"] != os.getpgrp() for call in wines))

    def test_translator_forces_hook_and_reports_source_skip(self):
        self.env['STORM_CASE'] = 'must-not-filter-storm'
        for source_present in (False, True):
            with self.subTest(source_present=source_present):
                source = self.root / "source/bin/x87sidecar"
                if source_present:
                    source.symlink_to(self.root / "bin/stub")
                self.log.write_text("")
                result = subprocess.run(["/bin/bash", str(self.root / "src/shell/tools/probes/smoke-x87sidecar.sh")],
                                        env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("SKIP: source-only" in result.stdout, not source_present)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                probes = [call for call in calls if call["name"] in ("wine", "x87sidecar")]
                self.assertEqual(len(probes), 35 if source_present else 2)
                storms = [call for call in probes if call['args'] and
                          call['args'][-1].endswith('/test_x87_signal_storm')]
                self.assertEqual(len(storms), 4 if source_present else 0)
                for call, settings in zip(storms, [('0', '0', None), ('1', '0', None),
                                                  ('1', '1', None), ('0', '0', '5')]):
                    self.assertEqual(call['args'][0], '--cooperative')
                    self.assertEqual(call['storm_pc24'], '1')
                    self.assertIsNone(call['storm_case'])
                    self.assertEqual((call['fma'], call['fast_round']), ('0', '0'))
                    self.assertEqual((call['ir_disabled'], call['fusions_disabled'],
                                      call['fpr_limit']), settings)
                hook = str(source) if source_present else self.env["TMNF_SIDECAR"]
                self.assertTrue(all(call["x87"] == "on" and call["hook"] == hook for call in probes))
                self.assertTrue(all(call["pgid"] != os.getpgrp() for call in probes))

    def test_missing_compiler_diagnostics_before_wine(self):
        (self.root / "bin/i686-w64-mingw32-gcc").unlink()
        for name in ("audio", "network", "shaders", "sync", "x87"):
            with self.subTest(smoke=name):
                result = subprocess.run(["/bin/bash", str(self.root / "src/shell/tools/probes" / ("smoke-" + name + ".sh"))],
                                        env=self.env, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Missing i686-w64-mingw32-gcc. Install mingw-w64 to build smoke probes.", result.stderr)
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
