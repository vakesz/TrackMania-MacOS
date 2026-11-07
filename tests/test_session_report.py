"""Offline checks for tmnf.session.report and tmnf.session.monitor (no Wine, game or MinGW)."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tmnf.session import monitor, report

# One session: unknown until the helper reports, then menu, race (focus lost for a
# while), finish. Wine's +fps lines, helper lines and monitor lines share one clock.
LOG = '''
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
'''


class SessionReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tmnf-report-')
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / 'play.log'
        self.log.write_text(LOG)

    def test_phases_come_only_from_game_state(self):
        session = report.sessions([self.log])[-1]
        self.assertEqual(session.started, '2026-10-18T10:00:00Z')
        self.assertEqual(session.status, 0)
        names = [name for _, _, name in session.phases()]
        self.assertEqual(names, ['unknown', 'menu', 'race', 'finish'])

    def test_frame_rate_by_state_counts_focused_time_only(self):
        _, totals, unfocused = report.by_state(report.sessions([self.log])[-1])
        self.assertEqual(totals['race']['fps'], [110.0, 100.0, 90.0])
        self.assertEqual(totals['race']['cpu'], [95.0])
        self.assertEqual(totals['unknown']['fps'], [30.0])
        self.assertEqual(unfocused, [20.0])

    def test_races_are_grouped_by_the_map_loaded(self):
        maps = report.by_map(report.sessions([self.log])[-1])
        self.assertEqual(list(maps), ['A01-Race'])
        self.assertEqual(maps['A01-Race']['races'], 1)
        self.assertEqual(maps['A01-Race']['fps'], [110.0, 100.0, 90.0])

    def test_report_and_list_print(self):
        for argv in ([str(self.log)], [str(self.log), '--list']):
            with self.subTest(argv=argv), mock.patch('sys.argv', ['session-report.py', *argv]), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                report.main()
                self.assertIn('race', out.getvalue())
                self.assertIn('fps median  100.0', out.getvalue())

    def test_log_without_session_is_an_error(self):
        self.log.write_text('2026-10-18T10:00:00Z start action=play\nplain game output\n')
        with mock.patch('sys.argv', ['session-report.py', str(self.log)]), self.assertRaises(SystemExit):
            report.main()


class MonitorTests(unittest.TestCase):
    def test_missing_compiler_leaves_state_unknown(self):
        with mock.patch.object(monitor.shutil, 'which', return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(monitor.start_state_helper('/nonexistent', '/nonexistent', 'wine'))
        self.assertRegex(out.getvalue(), r'^tmnf-mon: tick=\d+\.\d{3} event=state_helper status=unavailable')


class TrackerTests(unittest.TestCase):
    """Spawn counts of the per-second monitor loop, with subprocess stubbed."""

    GAME_PS = '  100 /sbin/launchd\n 4242 Z:\\Games\\TmForever.exe -nologo\n'
    NO_GAME_PS = '  100 /sbin/launchd\n'

    def setUp(self):
        self.calls = []
        self.now = 0.0
        self.alive = True

    def stub_run(self, ps):
        def run(command, **_):
            self.calls.append(list(command[:2] if command[0] == 'lsappinfo' else command[:1]))
            if command[0] == 'ps':
                return mock.Mock(stdout=ps)
            if command[1] == 'front':
                return mock.Mock(stdout='ASN:0x0-0x1:\n')
            return mock.Mock(stdout='    pid = 4242 !cgsConnection\n')
        return mock.patch.object(monitor.subprocess, 'run', side_effect=run)

    def ticks(self, count, ps):
        game = monitor.GameTracker(libproc=None, clock=lambda: self.now)
        front = monitor.FrontTracker()
        seen = []
        with self.stub_run(ps), mock.patch.object(monitor, 'alive', side_effect=lambda _: self.alive):
            for tick in range(count):
                self.now = float(tick)
                pid = game.poll()
                seen.append((pid, front.pid() if pid is not None else None))
        return seen

    def test_running_game_costs_one_ps_spawn_over_many_ticks(self):
        seen = self.ticks(30, self.GAME_PS)
        self.assertEqual(set(seen), {(4242, 4242)})
        self.assertEqual(self.calls.count(['ps']), 1)
        # lsappinfo front once per tick; the pid behind the ASN is looked up once.
        self.assertEqual(self.calls.count(['lsappinfo', 'front']), 30)
        self.assertEqual(self.calls.count(['lsappinfo', 'info']), 1)

    def test_missing_game_rescans_at_most_every_five_seconds(self):
        seen = self.ticks(30, self.NO_GAME_PS)
        self.assertEqual({pid for pid, _ in seen}, {None})
        self.assertEqual(self.calls.count(['ps']), 6)
        self.assertNotIn(['lsappinfo', 'front'], self.calls)  # no game: foreground is irrelevant

    def test_exited_game_is_noticed_at_once_and_rescan_is_throttled(self):
        game = monitor.GameTracker(libproc=None, clock=lambda: self.now)
        with self.stub_run(self.GAME_PS), mock.patch.object(monitor, 'alive', side_effect=lambda _: self.alive):
            self.assertEqual(game.poll(), 4242)
            self.alive, self.now = False, 1.0
            self.assertIsNone(game.poll())
            self.assertEqual(self.calls.count(['ps']), 1)
            self.alive, self.now = True, 6.0
            self.assertEqual(game.poll(), 4242)
        self.assertEqual(self.calls.count(['ps']), 2)

    def test_reused_pid_is_not_mistaken_for_the_game(self):
        starts = iter([(1, 0), (2, 0)])
        game = monitor.GameTracker(libproc=object(), clock=lambda: self.now)
        with self.stub_run(self.GAME_PS), mock.patch.object(monitor, 'alive', return_value=True), \
                mock.patch.object(monitor, 'start_time', side_effect=lambda *_: next(starts)):
            self.assertEqual(game.poll(), 4242)  # scan records start (1, 0)
            self.now = 1.0
            self.assertIsNone(game.poll())  # same pid, new start time: dropped, scan throttled


if __name__ == '__main__':
    unittest.main()
