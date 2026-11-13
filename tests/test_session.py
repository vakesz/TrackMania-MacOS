"""Host-only session tests. No Wine installation or user prefix is used."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from trackmania_mac.guard import operation_guard as guard
from trackmania_mac.session import supervisor as session


ROOT = Path(__file__).resolve().parents[1]

WINE = r"""
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(os.environ['HOME'])
args = sys.argv[1:]
scenario = os.environ.get('FAKE_SCENARIO', 'normal')
inherited_umask = os.umask(0)
os.umask(inherited_umask)
if 'FAKE_UMASK' in os.environ: os.umask(int(os.environ['FAKE_UMASK'], 8))
assert 'TRACKMANIA_MAC_OPERATION_FD' not in os.environ
lock = pathlib.Path(os.environ['FIXTURE_LOCK']).stat()
for fd in range(3, 256):
    try: info = os.fstat(fd)
    except OSError: continue
    assert (info.st_dev, info.st_ino) != (lock.st_dev, lock.st_ino), 'inherited operation FD'
invocation = {'args': args, 'prefix': os.environ['WINEPREFIX'], 'pid': os.getpid(),
              'pgid': os.getpgrp(), 'override': os.environ.get('WINEDLLOVERRIDES')}
calls = root / 'wine.jsonl'
with calls.open('a') as stream:
    stream.write(json.dumps(invocation) + '\n')
if args[0] == 'reg':
    index = len(calls.read_text().splitlines())
    (root / 'reg-ready').touch()
    if scenario == 'hungreg': time.sleep(30)
    time.sleep(float(os.environ.get('FAKE_REG_DELAY', '0')))
    if index == int(os.environ.get('FAKE_REG_FAIL_AT', '0')): sys.exit(37)
    sys.exit(0)
result = pathlib.Path(args[args.index('--result') + 1])
(root / 'invocation.json').write_text(json.dumps({
    'args': args, 'prefix': os.environ['WINEPREFIX'], 'pid': os.getpid(),
    'pgid': os.getpgrp(), 'override': os.environ.get('WINEDLLOVERRIDES'),
    'mode': result.parent.stat().st_mode & 0o777,
    'umask': inherited_umask,
}))
def publish(value):
    temporary = result.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(result)
if scenario == 'noresult': sys.exit(0)
if scenario == 'helpererror': sys.exit(37)
if scenario == 'bootstrap': time.sleep(30)
if scenario == 'raw':
    temporary = result.with_suffix('.tmp')
    temporary.write_bytes(bytes.fromhex(os.environ['FAKE_RAW']))
    temporary.chmod(0o600)
    temporary.replace(result)
    time.sleep(30)
if scenario == 'outside':
    child = subprocess.Popen([sys.executable, '-c',
        "import pathlib, sys, time; p=pathlib.Path(sys.argv[1]); "
        "p.write_text(str(__import__('os').getpid())); "
        "exec('while not p.with_suffix(\".release\").exists(): time.sleep(0.01)'); "
        "p.with_suffix('.done').touch()",
        str(root / 'game.pid')], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    while not (root / 'game.pid').exists(): time.sleep(0.01)
    publish({'state': 'running'})
    (root / 'ready').touch()
    time.sleep(30)
if scenario == 'starting':
    publish({'state': 'starting'})
    time.sleep(30)
if scenario in ('failed', 'uncertain', 'native_timeout'):
    publish({'state': 'failed' if scenario == 'failed' else 'uncertain',
             'reason': 'startup_timeout' if scenario == 'native_timeout' else 'fixture_failure',
             'cleanup': False})
    time.sleep(30)
# Terminal-only fixtures publish once; other scenarios exercise state changes.
if scenario in ('interrupt', 'ignoreterm', 'regress', 'disappear', 'longgame'):
    publish({'state': 'running'})
    (root / 'ready').touch()
if scenario in ('interrupt', 'ignoreterm'):
    if scenario == 'ignoreterm': signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / 'signal-ready').touch()
    time.sleep(30)
if scenario == 'regress':
    time.sleep(0.15)
    publish({'state': 'starting'})
    time.sleep(30)
if scenario == 'disappear':
    time.sleep(0.15)
    result.unlink()
    time.sleep(30)
if scenario == 'longgame': time.sleep(2.5)
if scenario == 'redirected':
    publish({'state': 'running'})
    time.sleep(0.15)
    publish({'state': 'redirected', 'cleanup': True})
    sys.exit(0)
publish({'state': 'exited', 'exit_code': int(os.environ.get('FAKE_EXIT', '0')), 'cleanup': True})
if scenario in ('teardown', 'hunghelper'):
    while not (root / 'helper-release').exists():
        assert result.exists(), 'result deleted before helper ended'
        time.sleep(0.01)
"""

SERVER = r"""
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ['HOME'])
assert 'TRACKMANIA_MAC_OPERATION_FD' not in os.environ
lock_path = pathlib.Path(os.environ['FIXTURE_LOCK'])
if lock_path.exists():
    lock = lock_path.stat()
    for fd in range(3, 256):
        try: info = os.fstat(fd)
        except OSError: continue
        assert (info.st_dev, info.st_ino) != (lock.st_dev, lock.st_ino), 'inherited operation FD'
option = sys.argv[1]
with (root / 'server.jsonl').open('a') as stream:
    stream.write(json.dumps({'option': option, 'prefix': os.environ['WINEPREFIX'],
                            'pid': os.getpid()}) + '\n')
scenario = os.environ.get('FAKE_SCENARIO', '')
if scenario == 'hung' + option[1:]: time.sleep(30)
time.sleep(float(os.environ.get('FAKE_' + option[1:].upper() + '_DELAY', '0')))
if option == '-w' and scenario == 'teardown': (root / 'helper-release').touch()
sys.exit(int(os.environ.get('FAKE_' + option[1:].upper() + '_STATUS', '0')))
"""


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-session-host-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.prefix = self.home / "prefix with spaces and 'quotes"
        self.prefix.mkdir(mode=0o700)
        (self.prefix / ".trackmania-mac-prefix").write_text("tmnf:1\n")
        self.runtime = self.home / "runtime with spaces"
        self.runtime.mkdir()
        self.wine = self.runtime / "wine"
        self.server = self.runtime / "wineserver"
        for path, source in ((self.wine, WINE), (self.server, SERVER)):
            path.write_text("#!" + sys.executable + "\n" + source)
            path.chmod(0o700)
        self.helper = self.runtime / "session.exe"
        self.helper.touch()
        self.exe = self.home / "game with spaces.exe"
        self.exe.touch()
        self.env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(self.root),
            "PYTHONPATH": str(ROOT / "src" / "python"),
            "WINEPREFIX": "/never/use/inherited/prefix",
            "WINEDLLOVERRIDES": "fixture-secret-override",
            "FIXTURE_LOCK": str(
                self.prefix.with_name("." + self.prefix.name + ".trackmania-mac-operation.lock")
            ),
        }
        self.addCleanup(self.release_game)

    def release_game(self):
        (self.home / "game.release").touch()
        if (self.home / "game.pid").exists():
            deadline = time.monotonic() + 5
            while not (self.home / "game.done").exists():
                if time.monotonic() >= deadline:
                    self.fail("Fixture game did not acknowledge shutdown")
                time.sleep(0.01)

    def test_result_open_survives_atomic_replacement(self):
        result = self.home / "result.json"
        result.write_text('{"state":"starting"}')
        replacement = self.home / "replacement.json"
        final = dict(state="exited", exit_code=37, cleanup=True)
        replacement.write_text(json.dumps(final))
        original_open = os.open

        def replace_after_open(path, flags):
            fd = original_open(path, flags)
            replacement.replace(result)
            self.assertEqual(os.fstat(fd).st_nlink, 0)
            return fd

        with mock.patch.object(session.os, "open", side_effect=replace_after_open):
            self.assertEqual(session.read_result(result), {"state": "starting"})
        self.assertEqual(session.read_result(result), final)

    def command(self, action="run", options=(), target=None, guarded=True):
        # Every fake wine/wineserver is a fresh Python process, so budgets must
        # leave room for interpreter startup under load. Tests about an expiring
        # budget pass a short one together with a fixture that hangs.
        args = [
            sys.executable,
            "-m",
            "trackmania_mac.session.supervisor",
            action,
            "--prefix",
            str(self.prefix),
            "--wine",
            str(self.wine),
            "--cleanup-timeout",
            "3",
        ]
        if action == "run":
            args += ["--helper", str(self.helper), "--startup-timeout", "1.5"]
        args += list(options)
        if action == "run":
            args += ["--"] + ([str(self.exe)] if target is None else target)
        if action == "run" and guarded:
            args = [
                sys.executable,
                "-m",
                "trackmania_mac.guard.operation_guard",
                "run",
                str(self.prefix),
            ] + args
        return args

    def cli(self, scenario="normal", action="run", options=(), target=None, guarded=True, **env):
        return subprocess.run(
            self.command(action, options, target, guarded),
            env=dict(self.env, FAKE_SCENARIO=scenario, **env),
            text=True,
            capture_output=True,
            timeout=8,
        )

    def spawn(self, command, env=None, **kwargs):
        process = subprocess.Popen(
            command,
            env=self.env if env is None else env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        self.addCleanup(self.reap, process)
        return process

    def reap(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def wait_file(self, name, process):
        path = self.home / name
        deadline = time.monotonic() + 5
        while not path.exists():
            if process.poll() is not None:
                self.fail("Process ended early: {}".format(process.communicate()))
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for " + name)
            time.sleep(0.01)
        return path

    def server_calls(self):
        path = self.home / "server.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def wine_calls(self):
        path = self.home / "wine.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_cleanup(self):
        calls = self.server_calls()
        self.assertEqual([call["option"] for call in calls], ["-k", "-w"])
        self.assertEqual({call["prefix"] for call in calls}, {str(self.prefix)})

    def assert_no_results(self):
        self.assertEqual(list(self.home.glob(".*.session-*")), [])

    def test_normal_exit_and_exact_arguments_environment_and_header(self):
        arguments = [str(self.exe), "", "two words", 'quote"', "trailing\\", "--secret=do-not-log"]
        watches = [str(self.exe), str(self.home / "settings.exe")]
        result = self.cli(
            options=["--watch", watches[0], "--watch", watches[1], "--desktop", "1280x720"],
            target=arguments,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = json.loads((self.home / "invocation.json").read_text())
        args = invocation["args"]
        self.assertEqual(args[0], str(self.helper))
        self.assertEqual(args[args.index("--") + 1 :], arguments)
        self.assertEqual(
            args[args.index("--watch") : args.index("--")],
            ["--watch", watches[0], "--watch", watches[1], "--desktop", "1280x720"],
        )
        self.assertGreater(int(args[args.index("--startup-ms") + 1]), 0)
        self.assertLessEqual(int(args[args.index("--startup-ms") + 1]), 1500)
        self.assertEqual(invocation["prefix"], str(self.prefix))
        self.assertEqual(invocation["override"], "fixture-secret-override")
        self.assertEqual(invocation["pid"], invocation["pgid"])
        self.assertEqual(invocation["mode"], 0o700)
        self.assertEqual(invocation["umask"], 0o077)
        self.assertEqual(Path(args[args.index("--result") + 1]).parent.parent, self.prefix.parent)
        self.assertRegex(result.stderr, r"session: \d{4}-\d\d-\d\dT")
        self.assertIn("runtime=" + str(self.wine), result.stderr)
        self.assertIn("executable=", result.stderr)
        self.assertNotIn("do-not-log", result.stderr)
        self.assertNotIn("fixture-secret-override", result.stderr)
        self.assertEqual(len(self.wine_calls()), 1)
        self.assert_cleanup()
        self.assert_no_results()

    def test_audio_and_settings_prepare_in_order_with_the_same_environment(self):
        for audio, driver in (("on", "coreaudio"), ("off", "")):
            with self.subTest(audio=audio):
                (self.home / "wine.jsonl").unlink(missing_ok=True)
                command = r"C:\Games with spaces\TmForeverLauncher.exe"
                result = self.cli(
                    options=["--audio", audio, "--settings", "--startup-timeout", "3"],
                    target=[command, "--private-argument"],
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.wine_calls()
                gpu_key = r"HKCU\Software\Wine\AppDefaults\TmForeverLauncher.exe\Direct3D"
                self.assertEqual(
                    [call["args"] for call in calls[:3]],
                    [
                        [
                            "reg",
                            "add",
                            r"HKCU\Software\Wine\Drivers",
                            "/v",
                            "Audio",
                            "/t",
                            "REG_SZ",
                            "/d",
                            driver,
                            "/f",
                        ],
                        [
                            "reg",
                            "add",
                            gpu_key,
                            "/v",
                            "VideoPciVendorID",
                            "/t",
                            "REG_DWORD",
                            "/d",
                            "0x10de",
                            "/f",
                        ],
                        [
                            "reg",
                            "add",
                            gpu_key,
                            "/v",
                            "VideoPciDeviceID",
                            "/t",
                            "REG_DWORD",
                            "/d",
                            "0x0611",
                            "/f",
                        ],
                    ],
                )
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[-1]["args"][0], str(self.helper))
                for call in calls:
                    self.assertEqual(call["prefix"], str(self.prefix))
                    self.assertEqual(call["override"], "fixture-secret-override")
                    self.assertEqual(call["pid"], call["pgid"])
                self.assertNotIn("--private-argument", result.stderr)
                self.assertNotIn("fixture-secret-override", result.stderr)

    def test_audio_and_settings_flags_are_independent(self):
        for options, count in ((["--audio", "off"], 1), (["--settings"], 2)):
            with self.subTest(options=options):
                (self.home / "wine.jsonl").unlink(missing_ok=True)
                result = self.cli(
                    options=options + ["--startup-timeout", "3"], target=[r"C:\Games\TmForever.exe"]
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.wine_calls()
                self.assertEqual(len(calls), count + 1)
                if count == 2:
                    self.assertEqual(
                        calls[0]["args"][2],
                        r"HKCU\Software\Wine\AppDefaults\TmForever.exe\Direct3D",
                    )

    GPU_KEY = r"Software\\Wine\\AppDefaults\\TmForever.exe\\Direct3D"
    DRIVERS_KEY = r"Software\\Wine\\Drivers"

    def write_user_reg(self, *keys, header=session.USER_REG_HEADER):
        """keys: (key path with doubled backslashes, [value lines]) in Wine's layout."""
        lines = [header, r";; All keys relative to \\User\\S-1-5-21-0-0-0-1000", "", "#arch=win64"]
        for key, values in keys:
            lines += ["", "[{}] 1700000000".format(key), "#time=1dc0000000000000"] + list(values)
        (self.prefix / "user.reg").write_text("\n".join(lines) + "\n")

    def full_user_reg(self, audio='"coreaudio"', vendor="dword:000010de", device="dword:00000611"):
        self.write_user_reg(
            (self.DRIVERS_KEY, ['"Audio"=' + audio]),
            (self.GPU_KEY, ['"VideoPciDeviceID"=' + device, '"VideoPciVendorID"=' + vendor]),
        )

    def registry_writes(self, options=("--audio", "on", "--settings")):
        (self.home / "wine.jsonl").unlink(missing_ok=True)
        result = self.cli(
            options=list(options) + ["--startup-timeout", "3"], target=[r"C:\Games\TmForever.exe"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.wine_calls()
        self.assertEqual(calls[-1]["args"][0], str(self.helper))
        return [call["args"][4] for call in calls if call["args"][0] == "reg"], result

    def test_values_already_in_user_reg_are_not_written_again(self):
        self.full_user_reg()
        self.assertEqual(self.registry_writes()[0], [])
        self.full_user_reg(audio='""')
        writes, _ = self.registry_writes(("--audio", "off", "--settings"))
        self.assertEqual(writes, [])

    def test_only_the_values_that_differ_are_written(self):
        for name, kwargs, expected in (
            ("audio", dict(audio='""'), ["Audio"]),
            ("vendor", dict(vendor="dword:000010df"), ["VideoPciVendorID"]),
            ("device", dict(device="dword:00000612"), ["VideoPciDeviceID"]),
        ):
            with self.subTest(name):
                self.full_user_reg(**kwargs)
                self.assertEqual(self.registry_writes()[0], expected)

    def test_absent_key_or_value_is_written(self):
        self.write_user_reg((self.DRIVERS_KEY, ['"Audio"="coreaudio"']))
        self.assertEqual(self.registry_writes()[0], ["VideoPciVendorID", "VideoPciDeviceID"])
        self.write_user_reg((self.DRIVERS_KEY, ['"Other"="coreaudio"']), (self.GPU_KEY, []))
        self.assertEqual(
            self.registry_writes()[0], ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        )
        # A different executable's key is not this one's.
        self.write_user_reg(
            (self.DRIVERS_KEY, ['"Audio"="coreaudio"']),
            (
                self.GPU_KEY.replace("TmForever.exe", "Other.exe"),
                ['"VideoPciVendorID"=dword:000010de', '"VideoPciDeviceID"=dword:00000611'],
            ),
        )
        self.assertEqual(self.registry_writes()[0], ["VideoPciVendorID", "VideoPciDeviceID"])

    def test_missing_user_reg_writes_everything(self):
        self.assertFalse((self.prefix / "user.reg").exists())
        self.assertEqual(
            self.registry_writes()[0], ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        )

    def test_malformed_user_reg_writes_everything(self):
        good = ["[{}] 1700000000".format(self.DRIVERS_KEY), '"Audio"="coreaudio"']
        gpu = [
            "[{}] 1700000000".format(self.GPU_KEY),
            '"VideoPciVendorID"=dword:000010de',
            '"VideoPciDeviceID"=dword:00000611',
        ]

        def text(*parts, newline="\n"):
            return newline.join(parts).encode()

        header = session.USER_REG_HEADER
        every = ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        cases = {
            "empty": (b"", every),
            "binary": (b"\xff\xfe\x00garbage\x00", every),
            "wrong header": (text("WINE REGISTRY Version 1", "", *good, "", *gpu, ""), every),
            "no header": (text(*good, "", *gpu, ""), every),
            "crlf": (text(header, "", *good, "", *gpu, "", newline="\r\n"), every),
            "duplicate key": (text(header, "", *good, "", *good, "", *gpu, ""), ["Audio"]),
            "duplicate value": (
                text(header, "", *good, '"Audio"="coreaudio"', "", *gpu, ""),
                ["Audio"],
            ),
            "conflicting duplicate": (
                text(header, "", *good, '"Audio"=""', "", *gpu, ""),
                ["Audio"],
            ),
            "single backslashes": (
                text(
                    header, "", "[Software\\Wine\\Drivers] 1", '"Audio"="coreaudio"', "", *gpu, ""
                ),
                ["Audio"],
            ),
            "wrong dword width": (
                text(
                    header,
                    "",
                    *good,
                    "",
                    gpu[0],
                    '"VideoPciVendorID"=dword:10de',
                    '"VideoPciDeviceID"=dword:00000611',
                    "",
                ),
                ["VideoPciVendorID"],
            ),
        }
        for name, (content, expected) in cases.items():
            with self.subTest(name):
                (self.prefix / "user.reg").write_bytes(content)
                self.assertEqual(self.registry_writes()[0], expected)
        # Control: the same good lines, well formed, are skipped.
        (self.prefix / "user.reg").write_text(
            "\n".join([session.USER_REG_HEADER, "", *good, "", *gpu, ""]) + "\n"
        )
        self.assertEqual(self.registry_writes()[0], [])

    def test_unreadable_or_unexpected_user_reg_files_are_ignored(self):
        path = self.prefix / "user.reg"
        self.full_user_reg()
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        self.assertEqual(
            self.registry_writes()[0], ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        )
        path.chmod(0o600)
        real = self.home / "real-user.reg"
        path.replace(real)
        path.symlink_to(real)
        self.assertEqual(
            self.registry_writes()[0], ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        )
        path.unlink()
        path.mkdir()
        self.assertEqual(
            self.registry_writes()[0], ["Audio", "VideoPciVendorID", "VideoPciDeviceID"]
        )

    def test_a_live_wineserver_means_user_reg_is_not_trusted(self):
        # user.reg may lag the live registry; a matching file proves nothing then.
        self.full_user_reg()
        patcher = mock.patch.dict(os.environ, HOME=str(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        args = [
            [
                "reg",
                "add",
                r"HKCU\Software\Wine\Drivers",
                "/v",
                "Audio",
                "/t",
                "REG_SZ",
                "/d",
                "coreaudio",
                "/f",
            ]
        ]
        self.assertEqual(session._unapplied(self.prefix, args), [])
        for failure in (ValueError("Wine is active for prefix"), OSError("probe failed")):
            with (
                self.subTest(failure=failure),
                mock.patch.object(guard, "check_inactive", side_effect=failure),
            ):
                self.assertEqual(session._unapplied(self.prefix, args), args)
        # A server that appears while user.reg is being read is caught by the second probe.
        with mock.patch.object(
            guard, "check_inactive", side_effect=[None, ValueError("Wine is active")]
        ):
            self.assertEqual(session._unapplied(self.prefix, args), args)

    def test_unrecognized_registry_commands_are_always_written(self):
        self.full_user_reg()
        patcher = mock.patch.dict(os.environ, HOME=str(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        odd = [
            [
                "reg",
                "add",
                r"HKLM\Software\Wine\Drivers",
                "/v",
                "Audio",
                "/t",
                "REG_SZ",
                "/d",
                "coreaudio",
                "/f",
            ],
            [
                "reg",
                "add",
                r"HKCU\Software\Wine\Drivers",
                "/v",
                "Audio",
                "/t",
                "REG_BINARY",
                "/d",
                "00",
                "/f",
            ],
            [
                "reg",
                "add",
                r"HKCU\Software\Wine\Drivers",
                "/v",
                "Audio",
                "/t",
                "REG_SZ",
                "/d",
                'a"b',
                "/f",
            ],
            ["reg", "delete", r"HKCU\Software\Wine\Drivers", "/v", "Audio", "/f"],
        ]
        self.assertEqual(session._unapplied(self.prefix, odd), odd)

    def test_hung_initial_registry_command_never_starts_helper_or_cleanup(self):
        unrelated = self.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        started = time.monotonic()
        result = self.cli(
            "hungreg", options=["--audio", "on", "--settings", "--startup-timeout", "2"]
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("preparation", result.stderr)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(len(self.wine_calls()), 1)
        self.assertFalse((self.home / "invocation.json").exists())
        self.assertEqual(self.server_calls(), [])
        self.assertIsNone(unrelated.poll())
        self.assert_no_results()

    def test_registry_failure_stops_preparation_without_prefix_cleanup(self):
        for index in (1, 2, 3):
            with self.subTest(index=index):
                (self.home / "wine.jsonl").unlink(missing_ok=True)
                result = self.cli(
                    options=["--audio", "on", "--settings", "--startup-timeout", "3"],
                    target=[r"C:\Games\TmForever.exe"],
                    FAKE_REG_FAIL_AT=str(index),
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("preparation", result.stderr)
                self.assertIn("37", result.stderr)
                self.assertEqual(len(self.wine_calls()), index)
                self.assertFalse((self.home / "invocation.json").exists())
                self.assertEqual(self.server_calls(), [])

    def test_all_preparation_and_native_startup_share_one_deadline(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                os.environ[guard.TOKEN] = str(fd)
                process = mock.Mock()
                process.poll.return_value = 0
                with mock.patch.object(session.time, "monotonic", return_value=100) as clock:

                    def prepare_wait(*args):
                        clock.return_value += 0.25
                        return 0

                    def spawn(args, **kwargs):
                        if args[1] != "reg":
                            clock.return_value = 101.6
                        return process

                    with (
                        mock.patch.object(session.subprocess, "Popen", side_effect=spawn) as popen,
                        mock.patch.object(session, "_wait", side_effect=prepare_wait) as wait,
                        mock.patch.object(
                            session, "read_result", return_value={"state": "starting"}
                        ),
                        mock.patch.object(session, "_log"),
                    ):
                        args = self.command(
                            options=["--audio", "on", "--settings"],
                            guarded=False,
                            target=[r"C:\Games\TmForever.exe"],
                        )[3:]
                        self.assertEqual(session.main(args), 124)
                    self.assertEqual([call.args[1] for call in wait.call_args_list], [101.5] * 3)
                    self.assertEqual(len(popen.call_args_list), 4)
                    native = popen.call_args_list[-1].args[0]
                    self.assertEqual(native[1], str(self.helper))
                    self.assertEqual(native[native.index("--startup-ms") + 1], "750")
            finally:
                os.close(fd)

    def test_signal_during_preparation_does_not_launch_or_kill_prefix(self):
        unrelated = self.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
        try:
            process = self.spawn(
                self.command(options=["--audio", "on", "--settings"], guarded=False),
                pass_fds=(fd,),
                env=dict(self.env, FAKE_SCENARIO="hungreg", **{guard.TOKEN: str(fd)}),
            )
            self.wait_file("reg-ready", process)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 143, (stdout, stderr))
            self.assertEqual(len(self.wine_calls()), 1)
            self.assertFalse((self.home / "invocation.json").exists())
            self.assertEqual(self.server_calls(), [])
            self.assertIsNone(unrelated.poll())
            self.assert_no_results()
        finally:
            os.close(fd)

    def test_preparation_validation_happens_before_any_wine_call(self):
        self.helper.unlink()
        result = self.cli(options=["--audio", "on", "--settings"])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("helper is missing", result.stderr)
        self.assertEqual(self.wine_calls(), [])

    def test_published_result_wins_over_an_elapsed_startup_deadline(self):
        read_result = session.read_result
        for state, scenario, expected in (
            ("exited", "normal", 1),
            ("running", "longgame", 1),
            ("failed", "failed", 2),
            ("uncertain", "uncertain", 2),
        ):
            with (
                self.subTest(state=state),
                mock.patch.dict(
                    os.environ,
                    dict(self.env, FAKE_SCENARIO=scenario, FAKE_EXIT=str(0xC0000005)),
                    clear=True,
                ),
            ):
                fd = guard.acquire(self.prefix)
                try:
                    os.environ[guard.TOKEN] = str(fd)
                    with mock.patch.object(session.time, "monotonic", return_value=100) as clock:

                        def delayed_read(path):
                            result = read_result(path)
                            if result and result["state"] == state:  # noqa: B023 (called within this iteration)
                                clock.return_value = 500
                            return result

                        with (
                            mock.patch.object(session, "read_result", side_effect=delayed_read),
                            mock.patch.object(session, "_log") as log,
                        ):
                            self.assertEqual(
                                session.main(self.command(guarded=False)[3:]), expected
                            )
                        self.assertEqual(
                            clock.return_value, 500, "fixture did not publish the requested state"
                        )
                        if expected == 1:
                            self.assertTrue(
                                any(
                                    "exception status 0xc0000005" in call.args[0]
                                    for call in log.call_args_list
                                )
                            )
                finally:
                    os.close(fd)

    def test_target_status_is_unsigned_and_not_a_unix_exit_code(self):
        for code in (37, 0xC0000005, 0xFFFFFFFF, 0xE0424242):
            with self.subTest(code=code):
                result = self.cli(FAKE_EXIT=str(code))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("0x{:08x} ({})".format(code, code), result.stderr)
                self.assertEqual("exception status" in result.stderr, code == 0xC0000005)
                self.assertNotIn("crash", result.stderr.lower())

    def test_redirect_reaches_the_helper_and_returns_redirected_after_cleanup(self):
        game = str(self.home / "TmForever.exe")
        result = self.cli(
            "redirected",
            options=[
                "--settings",
                "--watch",
                str(self.exe),
                "--watch",
                game,
                "--redirect",
                game,
                "--redirect-except",
                "/bench",
                "--redirect-except",
                "/list_benchs",
            ],
        )
        self.assertEqual(result.returncode, session.REDIRECTED, result.stderr)
        self.assertEqual(session.REDIRECTED, 3)
        args = json.loads((self.home / "invocation.json").read_text())["args"]
        self.assertEqual(
            args[args.index("--redirect") : args.index("--")],
            [
                "--redirect",
                game,
                "--redirect-except",
                "/bench",
                "--redirect-except",
                "/list_benchs",
            ],
        )
        self.assertIn("redirected; stopping the prefix", result.stderr)
        self.assertNotIn("target status", result.stderr)
        self.assert_cleanup()
        self.assert_no_results()

    def test_a_session_without_redirect_passes_no_redirect_options(self):
        result = self.cli(options=["--settings"])
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads((self.home / "invocation.json").read_text())["args"]
        self.assertNotIn("--redirect", args)
        self.assertNotIn("--redirect-except", args)

    def test_invalid_redirect_options_never_call_wine(self):
        game = str(self.home / "TmForever.exe")
        for options in (
            ["--redirect-except", "/bench"],
            ["--redirect", ""],
            ["--redirect", game, "--redirect-except", "bench"],
            ["--redirect", game, "--redirect-except", "/bench x"],
            ["--redirect", game, "--redirect-except", "/"],
            ["--redirect", game] + ["--redirect-except", "/a"] * 9,
        ):
            with self.subTest(options=options):
                result = self.cli(options=options)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.wine_calls(), [])
                self.assertEqual(self.server_calls(), [])

    def test_helper_exit_without_result_is_not_success(self):
        for scenario in ("noresult", "helpererror"):
            with self.subTest(scenario=scenario):
                result = self.cli(scenario)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("without a final native result", result.stderr)
                self.assertEqual(self.server_calls(), [])
                self.assert_no_results()

    def test_malformed_and_incomplete_results_never_authorize_cleanup(self):
        for raw in (
            b"{",
            b"",
            b"{}",
            b'{"state":"exited","exit_code":0}',
            b'{"state":"uncertain","reason":"test","cleanup":true}',
            b'{"state":"redirected","cleanup":false}',
            b'{"state":"redirected"}',
            b"x" * 4097,
        ):
            with self.subTest(raw=raw[:80]):
                result = self.cli("raw", FAKE_RAW=raw.hex())
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.server_calls(), [])
                self.assert_no_results()

    def test_failed_and_uncertain_leave_prefix_alone(self):
        for scenario in ("failed", "uncertain", "native_timeout"):
            with self.subTest(scenario=scenario):
                result = self.cli(scenario)
                self.assertEqual(
                    result.returncode, 124 if scenario == "native_timeout" else 2, result.stderr
                )
                self.assertIn(
                    "fixture_failure" if scenario != "native_timeout" else "startup_timeout",
                    result.stderr,
                )
                self.assertEqual(self.server_calls(), [])
                self.assert_no_results()

    def test_bootstrap_and_repeated_starting_do_not_reset_host_deadline(self):
        for scenario in ("bootstrap", "starting"):
            with self.subTest(scenario=scenario):
                started = time.monotonic()
                result = self.cli(scenario, options=["--startup-timeout", "0.25"])
                self.assertEqual(result.returncode, 124, result.stderr)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(self.server_calls(), [])
                self.assert_no_results()

    def test_running_has_no_game_duration_limit(self):
        started = time.monotonic()
        # The fake game runs 2.5s, past the default 1.5s startup budget.
        result = self.cli("longgame")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(time.monotonic() - started, 2.5)
        self.assert_cleanup()

    def test_result_regression_or_disappearance_is_an_error_without_cleanup(self):
        for scenario in ("regress", "disappear"):
            with self.subTest(scenario=scenario):
                result = self.cli(scenario)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.server_calls(), [])

    def test_final_result_triggers_cleanup_before_host_teardown(self):
        result = self.cli("teardown", FAKE_EXIT=str(0xC0000005))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("exception status 0xc0000005", result.stderr)
        self.assertTrue((self.home / "helper-release").exists())
        self.assert_cleanup()
        self.assert_no_results()

    def test_hung_cleanup_commands_and_helper_cannot_report_success(self):
        for scenario, expected in (
            ("hungk", ["-k"]),
            ("hungw", ["-k", "-w"]),
            ("hunghelper", ["-k", "-w"]),
        ):
            with self.subTest(scenario=scenario):
                (self.home / "server.jsonl").unlink(missing_ok=True)
                started = time.monotonic()
                result = self.cli(
                    scenario, options=["--cleanup-timeout", "1"], FAKE_EXIT="37", FAKE_UMASK="0"
                )
                self.assertEqual(result.returncode, 124, result.stderr)
                self.assertIn("target status 0x00000025 (37)", result.stderr)
                self.assertLess(time.monotonic() - started, 2.5)
                self.assertEqual([call["option"] for call in self.server_calls()], expected)

    def test_cleanup_budget_is_shared_between_server_calls(self):
        started = time.monotonic()
        result = self.cli(
            options=["--cleanup-timeout", "1.2"], FAKE_K_DELAY="0.35", FAKE_W_DELAY="1.0"
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - started, 2.2)
        self.assert_cleanup()

    def test_helper_wait_uses_the_same_deadline_as_both_server_calls(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                os.environ[guard.TOKEN] = str(fd)
                host = mock.Mock()
                host.poll.return_value = 0
                with (
                    mock.patch.object(session.subprocess, "Popen", return_value=host),
                    mock.patch.object(
                        session,
                        "read_result",
                        return_value={"state": "exited", "exit_code": 0, "cleanup": True},
                    ),
                    mock.patch.object(session, "_wait", return_value=0) as wait,
                    mock.patch.object(session, "_log"),
                ):
                    self.assertEqual(session.main(self.command(guarded=False)[3:]), 0)
                self.assertEqual(len(wait.call_args_list), 3)
                self.assertEqual(len({call.args[1] for call in wait.call_args_list}), 1)
            finally:
                os.close(fd)

    def test_cleanup_errors_and_absent_server_status(self):
        # -k and -w both return 1 when no server exists in the pinned runtime;
        # check_inactive below is the authority on shutdown, so only statuses
        # beyond (0, 1) count as failures.
        for k, w, expected in (("1", "0", 0), ("2", "0", 2), ("0", "1", 0), ("0", "2", 2)):
            with self.subTest(k=k, w=w):
                result = self.cli(FAKE_K_STATUS=k, FAKE_W_STATUS=w)
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_run_requires_a_real_inherited_guard_token(self):
        for token in (None, "invalid", "99"):
            env = {} if token is None else {guard.TOKEN: token}
            result = self.cli(guarded=False, **env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((self.home / "invocation.json").exists())
        self.assertEqual(self.server_calls(), [])

    def test_run_accepts_actual_passed_fd_and_does_not_leak_it(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                result = subprocess.run(
                    self.command(guarded=False),
                    env=dict(self.env, **{guard.TOKEN: str(fd)}),
                    pass_fds=(fd,),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                with mock.patch.dict(os.environ, {guard.TOKEN: str(fd)}):
                    self.assertEqual(guard.held(self.prefix), fd)
            finally:
                os.close(fd)
        self.assert_cleanup()

    def test_stop_ignores_operation_token_and_does_not_acquire_lock(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                result = self.cli(action="stop", **{guard.TOKEN: "invalid"})
                self.assertEqual(result.returncode, 0, result.stderr)
                with self.assertRaisesRegex(ValueError, "Another operation"):
                    guard.acquire(self.prefix)
            finally:
                os.close(fd)
        self.assert_cleanup()
        self.assertFalse((self.home / "invocation.json").exists())

    def test_stop_timeout_and_error_dispositions(self):
        self.assertEqual(
            self.cli("hungw", action="stop", options=["--cleanup-timeout", "0.2"]).returncode, 124
        )
        self.assertEqual(self.cli(action="stop", FAKE_W_STATUS="3").returncode, 2)

    def test_stop_interruption_ends_only_the_current_server_command(self):
        unrelated = self.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        process = self.spawn(self.command(action="stop"), env=dict(self.env, FAKE_SCENARIO="hungk"))
        self.wait_file("server.jsonl", process)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 130, (stdout, stderr))
        self.assertEqual([call["option"] for call in self.server_calls()], ["-k"])
        self.assertIsNone(unrelated.poll())

    def test_unowned_and_redirected_prefixes_never_invoke_wine_or_server(self):
        (self.prefix / ".trackmania-mac-prefix").unlink()
        for action in ("run", "stop"):
            result = self.cli(action=action)
            self.assertEqual(result.returncode, 2, result.stderr)
        (self.prefix / ".trackmania-mac-prefix").write_text("tmnf:1")
        real = self.home / "real-prefix"
        self.prefix.rename(real)
        self.prefix.symlink_to(real)
        result = self.cli(action="stop")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.server_calls(), [])
        self.assertFalse((self.home / "invocation.json").exists())

    def test_run_rejects_unmanaged_location_or_writable_result_parent(self):
        for mode in ("outside", "writable"):
            with self.subTest(mode=mode):
                if mode == "outside":
                    other_home = self.root / "other-home"
                    other_home.mkdir()
                    env = {"HOME": str(other_home)}
                else:
                    self.home.chmod(0o777)
                    env = {}
                try:
                    result = self.cli(**env)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertFalse((self.home / "invocation.json").exists())
                finally:
                    self.home.chmod(0o700)
        self.assertEqual(self.server_calls(), [])

    def test_owned_temporary_prefix_uses_a_private_result_directory_under_home(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="trackmania-mac-external-prefix-", dir="/private/tmp"
        )
        self.addCleanup(temporary.cleanup)
        prefix = Path(temporary.name) / "prefix"
        prefix.mkdir(mode=0o700)
        (prefix / ".trackmania-mac-prefix").write_text("tmnf:1")
        self.prefix = prefix
        self.env["FIXTURE_LOCK"] = str(prefix.with_name(".prefix.trackmania-mac-operation.lock"))
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = json.loads((self.home / "invocation.json").read_text())
        args = invocation["args"]
        result_path = Path(args[args.index("--result") + 1])
        self.assertEqual(result_path.parent.parent, self.home)
        self.assertEqual(invocation["mode"], 0o700)
        self.assertEqual(invocation["prefix"], str(prefix))
        self.assert_cleanup()
        self.assert_no_results()
        self.assertEqual(list(prefix.parent.glob(".*.session-*")), [])
        for parent in (self.home, prefix.parent):
            with self.subTest(unsafe_parent=parent):
                parent.chmod(0o777)
                try:
                    self.assertNotEqual(self.cli().returncode, 0)
                    self.assertEqual(len(self.wine_calls()), 1)
                finally:
                    parent.chmod(0o700)
        prefix.rename(prefix.with_name("real-prefix"))
        prefix.symlink_to(prefix.with_name("real-prefix"))
        self.assertNotEqual(self.cli().returncode, 0)
        self.assertEqual(len(self.wine_calls()), 1)

    def test_invalid_options_do_not_launch(self):
        for options in (
            ["--startup-timeout", "nan"],
            ["--startup-timeout", "inf"],
            ["--startup-timeout", "1e308"],
            ["--startup-timeout", "4294967.295"],
            ["--cleanup-timeout", "-1"],
            ["--cleanup-timeout", "0"],
            ["--desktop", "0x720"],
            ["--audio", "invalid"],
            ["--desktop", "65536x720"],
            ["--desktop", "1x2/secret"],
            ["--watch", "x"] * 9,
        ):
            with self.subTest(options=options):
                result = self.cli(options=options)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.cli(target=[]).returncode, 2)
        self.assertFalse((self.home / "invocation.json").exists())

    def test_missing_sibling_server_never_falls_back_to_path(self):
        self.server.unlink()
        result = self.cli(action="stop")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Runtime executable missing", result.stderr)
        self.assertEqual(self.server_calls(), [])

    def test_signals_stop_only_helper_and_return_signal_disposition(self):
        unrelated = self.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signum=signum):
                (self.home / "signal-ready").unlink(missing_ok=True)
                with mock.patch.dict(os.environ, self.env, clear=True):
                    fd = guard.acquire(self.prefix)
                try:
                    process = self.spawn(
                        self.command(guarded=False),
                        pass_fds=(fd,),
                        env=dict(self.env, FAKE_SCENARIO="ignoreterm", **{guard.TOKEN: str(fd)}),
                    )
                    self.wait_file("signal-ready", process)
                    started = time.monotonic()
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 128 + signum, (stdout, stderr))
                    self.assertLess(time.monotonic() - started, 1.5)
                    self.assertIsNone(unrelated.poll())
                    self.assertEqual(self.server_calls(), [])
                    self.assert_no_results()
                finally:
                    os.close(fd)

    def test_guard_interruption_does_not_kill_helper_game_group(self):
        # Keep the guarded caller alive through SIGTERM so the guard exercises
        # its final SIGKILL, rather than signalling an already-empty group.
        caller = (
            "import os, signal, subprocess, sys, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            'subprocess.Popen(sys.argv[1:], pass_fds=(int(os.environ["TRACKMANIA_MAC_OPERATION_FD"]),)); '
            "time.sleep(30)"
        )
        command = [
            sys.executable,
            "-m",
            "trackmania_mac.guard.operation_guard",
            "run",
            str(self.prefix),
            sys.executable,
            "-c",
            caller,
        ] + self.command(guarded=False)
        process = self.spawn(command, env=dict(self.env, FAKE_SCENARIO="outside"))
        game = self.wait_file("game.pid", process)
        self.wait_file("ready", process)
        invocation = json.loads((self.home / "invocation.json").read_text())
        self.assertNotEqual(invocation["pgid"], process.pid)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, (stdout, stderr))
        os.kill(int(game.read_text()), 0)
        self.assertEqual(self.server_calls(), [])
        self.assert_no_results()
        self.release_game()

    def test_final_inactivity_check_is_required_for_run_and_stop(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                os.environ[guard.TOKEN] = str(fd)
                for action in ("run", "stop"):
                    with (
                        self.subTest(action=action),
                        mock.patch.object(
                            guard, "check_inactive", side_effect=ValueError("Wine is active")
                        ) as check,
                    ):
                        args = self.command(action, guarded=False)[3:]
                        self.assertEqual(session.main(args), 2)
                        check.assert_called_once_with(self.prefix)
            finally:
                os.close(fd)

    def test_unstoppable_helper_retains_result_directory_and_reports_it(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            fd = guard.acquire(self.prefix)
            try:
                os.environ[guard.TOKEN] = str(fd)
                host = mock.Mock(pid=12345)
                host.poll.return_value = None
                with (
                    mock.patch.object(session.subprocess, "Popen", return_value=host),
                    mock.patch.object(session, "read_result", side_effect=ValueError("malformed")),
                    mock.patch.object(session, "_terminate", return_value=False),
                    mock.patch.object(session, "_log") as log,
                ):
                    self.assertEqual(session.main(self.command(guarded=False)[3:]), 2)
                directories = list(self.home.glob(".*.session-*"))
                self.assertEqual(len(directories), 1)
                self.assertTrue(
                    any(
                        "retained result directory " + str(directories[0]) in call.args[0]
                        for call in log.call_args_list
                    )
                )
                self.assertEqual(self.server_calls(), [])
            finally:
                os.close(fd)

    def test_helper_termination_is_pid_only_and_bounded(self):
        host = mock.Mock(pid=12345)
        host.poll.return_value = None
        host.wait.side_effect = subprocess.TimeoutExpired("fixture helper", 1)
        with mock.patch.object(session.os, "killpg") as group_kill:
            self.assertFalse(session._terminate(host, time.monotonic() + 15))
        host.terminate.assert_called_once_with()
        host.kill.assert_called_once_with()
        group_kill.assert_not_called()
        self.assertTrue(all(0 <= call.kwargs["timeout"] <= 1 for call in host.wait.call_args_list))


class ResultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-result-test-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "result.json"

    def test_missing_result_is_not_an_exit(self):
        self.assertIsNone(session.read_result(self.path))

    def test_exact_native_schemas(self):
        for result in (
            {"state": "starting"},
            {"state": "running"},
            {"state": "exited", "exit_code": 0xFFFFFFFF, "cleanup": True},
            {"state": "redirected", "cleanup": True},
            {"state": "failed", "reason": "create_process_failed", "cleanup": False},
            {"state": "uncertain", "reason": "process_limit", "cleanup": False},
        ):
            with self.subTest(result=result):
                self.path.write_text(json.dumps(result))
                self.assertEqual(session.read_result(self.path), result)

    def test_invalid_schema_types_fields_and_unsigned_range(self):
        invalid = [
            None,
            [],
            True,
            1,
            {},
            {"state": []},
            {"state": "unknown"},
            {"state": "running", "cleanup": True},
            {"state": "starting", "extra": 0},
            {"state": "exited", "cleanup": True},
            {"state": "redirected"},
            {"state": "redirected", "cleanup": False},
            {"state": "redirected", "cleanup": 1},
            {"state": "redirected", "exit_code": 0, "cleanup": True},
            {"state": "redirected", "reason": "test", "cleanup": True},
            {"state": "failed", "reason": "", "cleanup": False},
            {"state": "uncertain", "reason": "test", "cleanup": True},
            {"state": "failed", "reason": 1, "cleanup": False},
            {"state": "failed", "reason": "test\nsecret", "cleanup": False},
        ]
        for code in (-1, 0x100000000, True, 0.0, "0", None):
            invalid.append({"state": "exited", "exit_code": code, "cleanup": True})
        for cleanup in (False, 1, "true", None):
            invalid.append({"state": "exited", "exit_code": 0, "cleanup": cleanup})
        for result in invalid:
            with self.subTest(result=result):
                self.path.write_text(json.dumps(result))
                with self.assertRaises(ValueError):
                    session.read_result(self.path)

    def test_duplicate_fields_invalid_encoding_and_size_limit(self):
        for raw in (
            b'{"state":"failed","state":"exited","exit_code":0,"cleanup":true}',
            b"\xff",
            b"{}garbage",
            b"[]" * 2049,
            b"[" * 2000 + b"]" * 2000,
        ):
            with self.subTest(raw=raw[:80]):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    session.read_result(self.path)
        self.path.write_bytes(b'{"state":"running"}'.ljust(4096))
        self.assertEqual(session.read_result(self.path), {"state": "running"})
        self.path.write_bytes(b'{"state":"running"}'.ljust(4097))
        with self.assertRaises(ValueError):
            session.read_result(self.path)

    def test_symlink_fifo_and_public_result_are_rejected_without_blocking(self):
        target = self.path.with_name("target")
        target.write_text('{"state":"running"}')
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            session.read_result(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            session.read_result(self.path)
        self.path.unlink()
        self.path.write_text('{"state":"running"}')
        self.path.chmod(0o666)
        with self.assertRaises(ValueError):
            session.read_result(self.path)


@unittest.skipUnless(shutil.which("cc"), "a host C compiler is required")
class SettleDecisionTests(unittest.TestCase):
    """The early end of session.c's handoff window, without Wine."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-settle-")
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "session-settle"
        subprocess.run(
            [
                "cc",
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/session-settle.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    # root_known, root_exited, live_targets, live_job, ambiguous, accounted,
    # job_query_ok, active, total, known_job
    SAFE = dict(
        root_known=1,
        root_exited=1,
        live_targets=0,
        live_job=0,
        ambiguous=0,
        accounted=1,
        job_query_ok=1,
        active=0,
        total=3,
        known_job=3,
    )

    def decide(self, **changes):
        facts = dict(self.SAFE, **changes)
        result = subprocess.run(
            [str(self.binary)] + [str(facts[key]) for key in self.SAFE],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def test_finished_root_with_an_empty_matching_job_ends_early(self):
        self.assertEqual(self.decide(), "early")

    def test_every_missing_condition_keeps_the_full_window(self):
        for change in (
            {"root_known": 0},
            {"root_exited": 0},
            {"live_targets": 1},
            {"live_job": 1},
            {"ambiguous": 1},
            {"accounted": 0},
            {"job_query_ok": 0},
            {"active": 1},
            {"total": 4},  # a process was created after the snapshots
            {"total": 2},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.decide(**change), "wait")

    def test_a_failed_job_query_never_ends_early_whatever_it_reported(self):
        self.assertEqual(self.decide(job_query_ok=0, active=0, total=3), "wait")

    def test_live_root_desktop_close_and_live_job_processes_keep_the_window(self):
        # Desktop-close handling needs a live root or a live job process.
        self.assertEqual(self.decide(root_exited=0, live_job=1, active=1), "wait")
        self.assertEqual(self.decide(live_job=1, active=1), "wait")

    def test_session_only_uses_the_helper_after_the_settled_checks(self):
        source = (ROOT / "src/c/session.c").read_text()
        self.assertIn('#include "session_settle.h"', source)
        self.assertEqual(source.count("settle_may_end_early("), 1)
        early = source.index("settle_may_end_early(")
        settled = source.index("if (settled)")
        self.assertLess(early, settled)
        # The early path reuses the settled branch, so state and reason cannot differ.
        self.assertEqual(source.count('state = "exited";'), 1)


@unittest.skipUnless(shutil.which("cc"), "a host C compiler is required")
class RedirectDecisionTests(unittest.TestCase):
    """Which game starts session.c hands to Play, from their command lines."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-redirect-")
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "session-redirect"
        subprocess.run(
            [
                "cc",
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/session-redirect.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    EXCEPT = ("/bench", "/list_benchs")

    def decide(self, command, switches=EXCEPT):
        result = subprocess.run(
            [str(self.binary), command] + list(switches),
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def test_play_starts_are_redirected(self):
        for command in (
            "TmForever.exe",
            r'"C:\Program Files (x86)\TmUnitedForever\TmForever.exe"',
            r'"C:\Games\TmForever.exe" /userdir=C:\Users\me',
            "TmForever.exe   /nolauncher\t",
            "",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), "redirect")

    def test_benchmark_switches_keep_the_process_in_any_case_or_form(self):
        for command in (
            "TmForever.exe /bench",
            "TmForever.exe /BENCH=Bench.Replay.Gbx /out=Bench_1.txt",
            r'"C:\Games\TmForever.exe" /config=Bench.SystemConfig.Gbx /bench=x',
            'TmForever.exe "/bench=with space"',
            "TmForever.exe /list_benchs=x",
            r'"C:\Games\TmForever.exe"/bench',
            "TmForever.exe\t/bench",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), "keep")

    def test_a_switch_inside_another_argument_is_not_a_switch(self):
        for command in (
            'TmForever.exe "a /bench"',
            r'TmForever.exe "a\" /bench"',
            r'TmForever.exe a\\" /bench"',
            "TmForever.exe x/bench",
            "/bench.exe",
            r'"C:\bench dir /bench\TmForever.exe"',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), "redirect")

    def test_without_excepted_switches_every_start_is_redirected(self):
        self.assertEqual(self.decide("TmForever.exe /bench", ()), "redirect")

    def test_session_checks_arguments_before_it_stops_a_process(self):
        source = (ROOT / "src/c/session.c").read_text()
        self.assertIn('#include "session_redirect.h"', source)
        self.assertEqual(source.count("redirect_allowed("), 1)
        self.assertEqual(source.count("TerminateProcess(handle, ERROR_PROCESS_ABORTED)"), 1)
        self.assertLess(
            source.index("redirect_allowed("),
            source.index("TerminateProcess(handle, ERROR_PROCESS_ABORTED)"),
        )
        self.assertEqual(source.count('state = "redirected";'), 1)


@unittest.skipUnless(shutil.which("cc"), "a host C compiler is required")
class InfrastructureRuleTests(unittest.TestCase):
    """Which processes session.c recognizes, and which parents vouch for them."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="trackmania-mac-infrastructure-")
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / "session-infrastructure"
        subprocess.run(
            [
                "cc",
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/session-infrastructure.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    PROCESS = dict(
        watched=0,
        baseline=0,
        root=0,
        in_job=0,
        exited=0,
        exit_code=0,
        desktop_session=0,
        console_verified=0,
        desktop_verified=0,
        service_verified=0,
    )
    PARENT = dict(
        service=1,
        in_job=0,
        root=0,
        watched=0,
        baseline=1,
        service_manager=1,
        created_first=1,
    )

    def run_rule(self, mode, base, changes):
        facts = dict(base, **changes)
        result = subprocess.run(
            [str(self.binary), mode] + [str(facts[key]) for key in base],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def process(self, **changes):
        return self.run_rule("unrecognized", self.PROCESS, changes)

    def parent(self, **changes):
        return self.run_rule("parent", self.PARENT, changes)

    def test_a_verified_service_outside_the_job_is_known_while_alive_or_after_exit_zero(self):
        # lsass.exe, started on demand by the baseline services.exe.
        self.assertEqual(self.process(), "unrecognized")
        self.assertEqual(self.process(service_verified=1), "known")
        self.assertEqual(self.process(service_verified=1, exited=1), "known")

    def test_a_service_in_the_job_stays_unrecognized(self):
        # Only Wine's own services.exe, outside the job, starts a service.
        self.assertEqual(self.process(service_verified=1, in_job=1), "unrecognized")
        self.assertEqual(self.process(service_verified=1, in_job=1, exited=1), "unrecognized")

    def test_rules_from_before_services_are_unchanged(self):
        for changes, expected in (
            ({"watched": 1}, "known"),
            ({"baseline": 1}, "known"),
            ({"exited": 1}, "known"),  # an exited process outside the job
            ({"in_job": 1, "exited": 1}, "unrecognized"),
            ({"in_job": 1, "exited": 1, "console_verified": 1}, "known"),
            ({"in_job": 1, "console_verified": 1}, "unrecognized"),
            ({"in_job": 1, "desktop_verified": 1}, "known"),
            ({"in_job": 1, "desktop_verified": 1, "exited": 1, "exit_code": 1}, "unrecognized"),
            ({"in_job": 1, "root": 1}, "unrecognized"),
            ({"in_job": 1, "root": 1, "exited": 1, "exit_code": 9}, "known"),
            ({"in_job": 1, "root": 1, "desktop_session": 1}, "known"),
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.process(**changes), expected)

    def test_only_the_baseline_services_manager_vouches_for_a_service(self):
        self.assertEqual(self.parent(), "vouches")
        for changes in (
            {"baseline": 0},
            {"service_manager": 0},
            {"in_job": 1},
            {"created_first": 0},
            {"baseline": 0, "root": 1, "in_job": 1},
            {"baseline": 0, "watched": 1, "in_job": 1, "service_manager": 0},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.parent(**changes), "no")

    def test_console_and_desktop_parents_are_unchanged(self):
        job_parent = dict(service=0, baseline=0, service_manager=0, in_job=1)
        self.assertEqual(self.parent(**job_parent, root=1), "vouches")
        self.assertEqual(self.parent(**job_parent, watched=1), "vouches")
        self.assertEqual(self.parent(**job_parent), "no")
        self.assertEqual(self.parent(**dict(job_parent, in_job=0), root=1), "no")
        self.assertEqual(self.parent(**job_parent, root=1, created_first=0), "no")
        # The services manager never vouches for a console host or desktop.
        self.assertEqual(self.parent(service=0), "no")

    def test_session_uses_the_rules_and_needs_a_builtin_image_outside_the_job(self):
        source = (ROOT / "src/c/session.c").read_text()
        self.assertIn('#include "session_infrastructure.h"', source)
        self.assertEqual(source.count("process_is_unrecognized("), 1)
        self.assertEqual(source.count("infrastructure_parent("), 1)
        self.assertEqual(source.count("p->service_verified = 1;"), 1)
        self.assertIn('native_reference(&s, L"services.exe", &s.services)', source)
        candidate = source[source.index("p->service_candidate = ") :]
        candidate = candidate[: candidate.index(";")]
        for condition in ("!baseline", "!root", "!p->watched", "!in_job", "builtin_system_image("):
            self.assertIn(condition, candidate)


if __name__ == "__main__":
    unittest.main()
