"""Offline checks for tmnf.session.report and tmnf.session.monitor (no Wine, game or MinGW)."""

import contextlib
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tmnf.session import monitor, report

# One session: unknown until the helper reports, then menu, race (focus lost for a
# while), finish. Wine's +fps lines, helper lines and monitor lines share one clock.
LOG = """
2026-10-18T10:00:00Z start action=play
tmnf-run: version=0.3.0 macos=27.0.1 model=Mac17,9 x87=on audio=on renderer=d9vk target=TmForever.exe
tmnf-mon: tick=100.000 event=focus state=game
100.500:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 30.00fps, total 30.00fps
tmnf-state: 101.000 0x20
tmnf-map: 101.050 none
102.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 120.00fps, total 100.00fps
tmnf-map: 102.900 A01-Race
tmnf-state: 103.000 0x200
104.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 110.00fps, total 105.00fps
tmnf-mon: tick=104.500 event=sample cpu=95% threads=40 top=main:90%@36
105.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 100.00fps, total 104.00fps
tmnf-mon: tick=105.500 event=focus state=other
106.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 20.00fps, total 90.00fps
tmnf-mon: tick=106.500 event=focus state=game
107.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 90.00fps, total 90.00fps
tmnf-state: 108.000 0x400
109.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 115.00fps, total 95.00fps
tmnf-state: 110.000 exited

2026-10-18T10:00:30Z exit status=0
"""


class SessionReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tmnf-report-")
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / "play.log"
        self.log.write_text(LOG)

    def test_phases_come_only_from_game_state(self):
        session = report.sessions([self.log])[-1]
        self.assertEqual(session.started, "2026-10-18T10:00:00Z")
        self.assertEqual(session.status, 0)
        names = [name for _, _, name in session.phases()]
        self.assertEqual(names, ["unknown", "menu", "race", "finish"])

    def test_frame_rate_by_state_counts_focused_time_only(self):
        _, totals, unfocused = report.by_state(report.sessions([self.log])[-1])
        self.assertEqual(totals["race"]["fps"], [110.0, 100.0, 90.0])
        self.assertEqual(totals["race"]["cpu"], [95.0])
        self.assertEqual(totals["unknown"]["fps"], [30.0])
        self.assertEqual(unfocused, [20.0])

    def test_races_are_grouped_by_the_map_loaded(self):
        maps = report.by_map(report.sessions([self.log])[-1])
        self.assertEqual(list(maps), ["A01-Race"])
        self.assertEqual(maps["A01-Race"]["races"], 1)
        self.assertEqual(maps["A01-Race"]["fps"], [110.0, 100.0, 90.0])

    def test_report_and_list_print(self):
        for argv in ([str(self.log)], [str(self.log), "--list"]):
            with (
                self.subTest(argv=argv),
                mock.patch("sys.argv", ["session-report.py", *argv]),
                contextlib.redirect_stdout(io.StringIO()) as out,
            ):
                report.main()
                self.assertIn("race", out.getvalue())
                self.assertIn("fps median  100.0", out.getvalue())

    def test_log_without_session_is_an_error(self):
        self.log.write_text("2026-10-18T10:00:00Z start action=play\nplain game output\n")
        with (
            mock.patch("sys.argv", ["session-report.py", str(self.log)]),
            self.assertRaises(SystemExit),
        ):
            report.main()


class MonitorTests(unittest.TestCase):
    def test_missing_compiler_leaves_state_unknown(self):
        with (
            mock.patch.object(monitor.shutil, "which", return_value=None),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertIsNone(monitor.start_state_helper("/nonexistent", "/nonexistent", "wine"))
        self.assertRegex(
            out.getvalue(), r"^tmnf-mon: tick=\d+\.\d{3} event=state_helper status=unavailable"
        )


FAKE_COMPILER = """#!/bin/bash
# Stands in for x86_64-w64-mingw32-gcc: records argv, then behaves like gcc by
# writing the -o file only when the build succeeds.
printf '%s\\n' call "$@" >>"$FAKE_CC_LOG"
out=
while test "$#" -gt 0; do
  test "$1" != -o || out="$2"
  shift
done
test -z "${FAKE_CC_FAIL:-}" || exit 1
printf 'fake exe' >"$out"
"""

FAKE_WINE = """#!/bin/bash
printf '%s|%s\\n' "$WINEPREFIX" "$*" >>"$FAKE_WINE_LOG"
"""


class StateHelperBuildTests(unittest.TestCase):
    """start_state_helper against a fake MinGW compiler and fake Wine on PATH; nothing real runs."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tmnf-state-helper-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.compiler = bin_dir / "x86_64-w64-mingw32-gcc"
        self.wine = bin_dir / "wine"
        for tool, body in ((self.compiler, FAKE_COMPILER), (self.wine, FAKE_WINE)):
            tool.write_text(body)
            tool.chmod(0o755)
        self.cc_log, self.wine_log = self.root / "cc.log", self.root / "wine.log"
        self.source = self.root / "game-state.c"
        self.source.write_text("int version_one;\n")
        self.cache = self.root / "cache"
        # A real file: the helper's stdout is inherited by the child, and emit() writes here too.
        self.output = open(self.root / "output.log", "w+")
        self.addCleanup(self.output.close)
        env = mock.patch.dict(
            os.environ,
            PATH=str(bin_dir),
            FAKE_CC_LOG=str(self.cc_log),
            FAKE_WINE_LOG=str(self.wine_log),
        )
        for patcher in (
            env,
            mock.patch.object(monitor, "SOURCE", self.source),
            mock.patch.object(monitor.sys, "stdout", self.output),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self):
        process = monitor.start_state_helper(self.cache, "the-prefix", str(self.wine))
        if process is not None:
            self.assertEqual(process.wait(timeout=10), 0)
        return process

    def exe(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()[:16]
        return self.cache / ("game-state-" + digest + ".exe")

    def compiles(self):
        if not self.cc_log.exists():
            return []
        return [call.splitlines() for call in self.cc_log.read_text().split("call\n")[1:]]

    def events(self):
        self.output.flush()
        return (self.root / "output.log").read_text()

    def test_builds_with_strict_flags_and_runs_the_helper_in_the_game_prefix(self):
        process = self.start()
        self.assertIsNotNone(process)
        partial = self.exe().with_suffix(".partial.exe")
        self.assertEqual(
            self.compiles(),
            [["-O2", "-Wall", "-Wextra", "-Werror", str(self.source), "-o", str(partial)]],
        )
        self.assertEqual(self.exe().read_text(), "fake exe")
        self.assertFalse(partial.exists())
        self.assertEqual(self.wine_log.read_text(), "the-prefix|{}\n".format(self.exe()))

    def test_unchanged_source_reuses_the_cached_build(self):
        self.start()
        self.start()
        self.assertEqual(len(self.compiles()), 1)
        self.assertEqual(len(self.wine_log.read_text().splitlines()), 2)

    def test_changed_source_rebuilds_and_sweeps_old_digests(self):
        self.cache.mkdir()
        stale = self.cache / "game-state-0123456789abcdef.exe"
        stale.write_text("old build")
        unrelated = [self.cache / "notes.txt", self.cache / "game-state.log"]
        for path in unrelated:
            path.write_text("keep")
        self.start()
        first = self.exe()
        self.assertTrue(first.is_file())
        self.assertFalse(stale.exists())
        self.source.write_text("int version_two;\n")
        self.start()
        second = self.exe()
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.compiles()), 2)
        self.assertEqual(sorted(self.cache.glob("game-state-*.exe")), [second])
        for path in unrelated:
            self.assertEqual(path.read_text(), "keep")

    def test_compile_failure_reports_unavailable_and_leaves_no_executable(self):
        with mock.patch.dict(os.environ, FAKE_CC_FAIL="1"):
            self.assertIsNone(self.start())
        self.assertRegex(
            self.events(), r"event=state_helper status=unavailable reason='build failed'"
        )
        self.assertEqual(len(self.compiles()), 1)
        self.assertEqual(list(self.cache.glob("game-state-*.exe")), [])
        self.assertFalse(self.wine_log.exists())
        # Nothing is remembered: the next start, with a working compiler, builds.
        self.assertIsNotNone(self.start())
        self.assertTrue(self.exe().is_file())
        self.assertEqual(len(self.compiles()), 2)


class TrackerTests(unittest.TestCase):
    """Spawn counts of the per-second monitor loop, with subprocess stubbed."""

    GAME_PS = "  100 /sbin/launchd\n 4242 Z:\\Games\\TmForever.exe -nologo\n"
    NO_GAME_PS = "  100 /sbin/launchd\n"

    def setUp(self):
        self.calls = []
        self.now = 0.0
        self.alive = True

    def stub_run(self, ps):
        def run(command, **_):
            self.calls.append(list(command[:2] if command[0] == "lsappinfo" else command[:1]))
            if command[0] == "ps":
                return mock.Mock(stdout=ps)
            if command[1] == "front":
                return mock.Mock(stdout="ASN:0x0-0x1:\n")
            return mock.Mock(stdout="    pid = 4242 !cgsConnection\n")

        return mock.patch.object(monitor.subprocess, "run", side_effect=run)

    def ticks(self, count, ps):
        game = monitor.GameTracker("TmForever.exe", libproc=None, clock=lambda: self.now)
        front = monitor.FrontTracker()
        seen = []
        with (
            self.stub_run(ps),
            mock.patch.object(monitor, "alive", side_effect=lambda _: self.alive),
        ):
            for tick in range(count):
                self.now = float(tick)
                pid = game.poll()
                seen.append((pid, front.pid() if pid is not None else None))
        return seen

    def test_game_pid_matches_only_the_given_exe(self):
        ps = "  100 /sbin/launchd\n 4242 Z:\\Games\\TmForever.exe\n 4343 C:\\MP\\ManiaPlanet.exe -x\n"
        with self.stub_run(ps):
            self.assertEqual(monitor.game_pid("ManiaPlanet.exe"), 4343)
            self.assertEqual(monitor.game_pid("TmForever.exe"), 4242)
            self.assertIsNone(monitor.game_pid("Tm.orever.exe"))

    def test_running_game_costs_one_ps_spawn_over_many_ticks(self):
        seen = self.ticks(30, self.GAME_PS)
        self.assertEqual(set(seen), {(4242, 4242)})
        self.assertEqual(self.calls.count(["ps"]), 1)
        # lsappinfo front once per tick; the pid behind the ASN is looked up once.
        self.assertEqual(self.calls.count(["lsappinfo", "front"]), 30)
        self.assertEqual(self.calls.count(["lsappinfo", "info"]), 1)

    def test_missing_game_rescans_at_most_every_five_seconds(self):
        seen = self.ticks(30, self.NO_GAME_PS)
        self.assertEqual({pid for pid, _ in seen}, {None})
        self.assertEqual(self.calls.count(["ps"]), 6)
        self.assertNotIn(["lsappinfo", "front"], self.calls)  # no game: foreground is irrelevant

    def test_exited_game_is_noticed_at_once_and_rescan_is_throttled(self):
        game = monitor.GameTracker("TmForever.exe", libproc=None, clock=lambda: self.now)
        with (
            self.stub_run(self.GAME_PS),
            mock.patch.object(monitor, "alive", side_effect=lambda _: self.alive),
        ):
            self.assertEqual(game.poll(), 4242)
            self.alive, self.now = False, 1.0
            self.assertIsNone(game.poll())
            self.assertEqual(self.calls.count(["ps"]), 1)
            self.alive, self.now = True, 6.0
            self.assertEqual(game.poll(), 4242)
        self.assertEqual(self.calls.count(["ps"]), 2)

    def test_reused_pid_is_not_mistaken_for_the_game(self):
        starts = iter([(1, 0), (2, 0)])
        game = monitor.GameTracker("TmForever.exe", libproc=object(), clock=lambda: self.now)
        with (
            self.stub_run(self.GAME_PS),
            mock.patch.object(monitor, "alive", return_value=True),
            mock.patch.object(monitor, "start_time", side_effect=lambda *_: next(starts)),
        ):
            self.assertEqual(game.poll(), 4242)  # scan records start (1, 0)
            self.now = 1.0
            self.assertIsNone(game.poll())  # same pid, new start time: dropped, scan throttled


if __name__ == "__main__":
    unittest.main()
