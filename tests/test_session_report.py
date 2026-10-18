"""Offline checks for tools/session-report.py and lib/monitor.py (no Wine, game or MinGW)."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lib import monitor

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session_report', ROOT / 'tools/session-report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

# One session: unknown until the helper reports, then menu, race (focus lost for a
# while), finish. Wine's +fps lines, helper lines and monitor lines share one clock.
LOG = '''
2026-10-18T10:00:00Z start action=play
mactmnf-run: version=0.3.0 macos=27.0.1 model=Mac17,9 x87=on audio=on renderer=d9vk target=TmForever.exe
mactmnf-mon: tick=100.000 event=focus state=game
100.500:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 30.00fps, total 30.00fps
mactmnf-state: 101.000 0x20
mactmnf-map: 101.050 none
102.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 120.00fps, total 100.00fps
mactmnf-map: 102.900 A01-Race
mactmnf-state: 103.000 0x200
104.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 110.00fps, total 105.00fps
mactmnf-mon: tick=104.500 event=sample cpu=95% threads=40 top=main:90%@36
105.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 100.00fps, total 104.00fps
mactmnf-mon: tick=105.500 event=focus state=other
106.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 20.00fps, total 90.00fps
mactmnf-mon: tick=106.500 event=focus state=game
107.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 90.00fps, total 90.00fps
mactmnf-state: 108.000 0x400
109.000:0120:trace:fps:win32u_vkQueuePresentKHR 0x1 @ approx 115.00fps, total 95.00fps
mactmnf-state: 110.000 exited

2026-10-18T10:00:30Z exit status=0
'''


class SessionReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mactmnf-report-')
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
        self.assertRegex(out.getvalue(), r'^mactmnf-mon: tick=\d+\.\d{3} event=state_helper status=unavailable')


if __name__ == '__main__':
    unittest.main()
