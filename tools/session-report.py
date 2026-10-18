#!/usr/bin/env python3
"""Report frame rate and CPU by game state for sessions run with make diag.

Reads the play log: the session header and lib/monitor.py lines, the game-state
helper's lines (src/game-state.c, state and map name) and Wine's +fps lines, which
share one clock. Phases come only from the game's own state; time without a known
state is reported as "unknown". Frame rates count only time when the game had focus.

  python3 tools/session-report.py            # the latest session
  python3 tools/session-report.py --list     # one line per session in the log and its backups
"""

import argparse
from pathlib import Path
import re
import shlex
import statistics

LOG = Path.home() / 'Library/Logs/MacTMNF/play.log'
RECORD = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (start action=\w+|exit status=\d+)$')
FPS = re.compile(r'^(\d+\.\d+):[0-9a-f]+:trace:fps:\S+ \S+ @ approx ([\d.]+)fps')
STATE = re.compile(r'^mactmnf-state: (\d+\.\d+) (\S+)')
MAP = re.compile(r'^mactmnf-map: (\d+\.\d+) (.+)$')
MONITOR = re.compile(r'^mactmnf-mon: (.*)$')
# TMNF's EGameState values (CTrackMania::GetCurrentState).
STATES = {0x0: 'none', 0x1: 'online menu', 0x2: 'online round prepare', 0x4: 'online round',
          0x8: 'online round exit', 0x10: 'startup', 0x20: 'menu', 0x40: 'quit', 0x80: 'race init',
          0x100: 'editor', 0x200: 'race', 0x400: 'finish', 0x1000: 'replay', 0x2000: 'race end',
          0x4000: 'online sync', 0x8000: 'online race', 0x10000: 'online exit'}


def fields(text):
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    return dict(part.split('=', 1) for part in parts if '=' in part)


def state_name(value):
    if value.startswith('0x'):
        number = int(value, 16)
        return STATES.get(number, value)
    return 'unknown'


class Session:
    def __init__(self, header, started):
        self.header, self.started, self.status = header, started, None
        self.fps, self.states, self.focus, self.samples, self.notes = [], [], [], [], []
        self.maps = []

    def add(self, line):
        match = FPS.match(line)
        if match:
            self.fps.append((float(match.group(1)), float(match.group(2))))
            return
        match = STATE.match(line)
        if match:
            self.states.append((float(match.group(1)), match.group(2)))
            return
        match = MAP.match(line)
        if match:
            self.maps.append((float(match.group(1)), match.group(2)))
            return
        match = MONITOR.match(line)
        if match:
            event = fields(match.group(1))
            tick = float(event.get('tick', 0))
            if event.get('event') == 'focus':
                self.focus.append((tick, event.get('state')))
            elif event.get('event') == 'sample':
                self.samples.append((tick, float(event.get('cpu', '0%').rstrip('%')), event.get('top', '')))
            elif event.get('event') in ('state_helper', 'error'):
                self.notes.append(' '.join(f'{k}={v}' for k, v in event.items() if k != 'tick'))

    def at(self, series, tick, default):
        value = default
        for time, item in series:
            if time > tick:
                break
            value = item
        return value

    def phases(self):
        """[(start, end, state)] from the game's own state; unknown before the first line."""
        ticks = [t for t, _ in self.fps] + [t for t, _ in self.states] + [t for t, _, _ in self.samples]
        if not ticks:
            return []
        begin, end = min(ticks), max(ticks)
        changes = [(t, state_name(v)) for t, v in self.states if v != 'exited']
        bounds = [(begin, 'unknown')] + [(t, name) for t, name in changes if t >= begin]
        result = []
        for (start, name), following in zip(bounds, bounds[1:] + [(end, None)]):
            if following[0] > start:
                result.append((start, following[0], name))
        merged = []
        for phase in result:
            if merged and merged[-1][2] == phase[2]:
                merged[-1] = (merged[-1][0], phase[1], phase[2])
            else:
                merged.append(phase)
        return merged


def summary(values):
    if not values:
        return 'no frame-rate samples'
    ordered = sorted(values)
    return (f'fps median {statistics.median(ordered):6.1f}  slowest 10% {ordered[len(ordered) // 10]:6.1f}  '
            f'lowest {ordered[0]:6.1f}  (n={len(ordered)})')


def by_state(session):
    phases = session.phases()
    totals = {}
    for start, end, name in phases:
        entry = totals.setdefault(name, {'seconds': 0.0, 'fps': [], 'cpu': []})
        entry['seconds'] += end - start
    unfocused = []
    for tick, fps in session.fps:
        name = next((n for s, e, n in phases if s <= tick <= e), 'unknown')
        if session.at(session.focus, tick, 'game') == 'game':
            totals.setdefault(name, {'seconds': 0.0, 'fps': [], 'cpu': []})['fps'].append(fps)
        else:
            unfocused.append(fps)
    for tick, cpu, _ in session.samples:
        name = next((n for s, e, n in phases if s <= tick <= e), 'unknown')
        totals.setdefault(name, {'seconds': 0.0, 'fps': [], 'cpu': []})['cpu'].append(cpu)
    return phases, totals, unfocused


def by_map(session):
    """{map: {'races', 'seconds', 'fps'}} over race phases, named by the map loaded then."""
    maps = {}
    for start, end, name in session.phases():
        if name != 'race':
            continue
        map_name = session.at(session.maps, start, 'unknown')
        entry = maps.setdefault(map_name if map_name != 'none' else 'unknown',
                                {'races': 0, 'seconds': 0.0, 'fps': []})
        entry['races'] += 1
        entry['seconds'] += end - start
        entry['fps'] += [fps for tick, fps in session.fps
                         if start <= tick <= end and session.at(session.focus, tick, 'game') == 'game']
    return maps


def report(session):
    header = fields(session.header)
    print(f"Session {session.started or '?'}  {' '.join(f'{k}={v}' for k, v in header.items())}"
          + (f'  [exit {session.status}]' if session.status is not None else ''))
    for note in session.notes:
        print(f'  note: {note}')
    phases, totals, unfocused = by_state(session)
    print('  by game state (frame rate while the game had focus):')
    for name, entry in sorted(totals.items(), key=lambda item: -item[1]['seconds']):
        cpu = f"  cpu avg {statistics.mean(entry['cpu']):4.0f}%" if entry['cpu'] else ''
        print(f"    {name:<14} {entry['seconds']:6.0f} s  {summary(entry['fps'])}{cpu}")
    if unfocused:
        print(f'    {"(not focused)":<14} {"":>8}  {summary(unfocused)}')
    maps = by_map(session) if session.maps else {}
    if maps:
        print('  races by map (frame rate while the game had focus):')
        for name, entry in sorted(maps.items(), key=lambda item: -item[1]['seconds']):
            print(f"    {name:<24} {entry['races']:2d} x {entry['seconds']:5.0f} s  {summary(entry['fps'])}")
    if phases:
        begin = phases[0][0]
        print('  timeline (seconds from the first sample):')
        for start, end, name in phases:
            values = [fps for tick, fps in session.fps if start <= tick <= end]
            median = f'median {statistics.median(values):6.1f} fps' if values else 'no frame-rate samples'
            where = f'  {session.at(session.maps, start, "unknown")}' if name == 'race' and session.maps else ''
            print(f'    {start - begin:7.1f} - {end - begin:7.1f}  {name:<14} {median}{where}')
    hot = [top for _, _, top in session.samples if top]
    if hot:
        print(f'  busiest threads, last sample: {hot[-1]}')


def sessions(paths):
    found, started, current = [], None, None
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(errors='replace').splitlines():
            record = RECORD.match(line)
            if record:
                if record.group(2).startswith('start'):
                    started, current = record.group(1), None
                elif current is not None:
                    current.status = int(record.group(2).split('=')[1])
                continue
            if line.startswith('mactmnf-run: '):
                current = Session(line[len('mactmnf-run: '):], started)
                found.append(current)
            elif current is not None:
                current.add(line)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('log', nargs='?', type=Path, default=LOG)
    parser.add_argument('--list', action='store_true', help='one line per session, oldest first')
    args = parser.parse_args()
    paths = [Path(f'{args.log}.{i}') for i in (3, 2, 1)] + [args.log] if args.list else [args.log]
    found = sessions(paths)
    if not found:
        raise SystemExit(f'No diagnostic session in {args.log}. Run make diag first.')
    if not args.list:
        report(found[-1])
        return
    for session in found:
        _, totals, _ = by_state(session)
        race = totals.get('race', {}).get('fps', [])
        print(f"{session.started or '?':<21} {fields(session.header).get('x87', '?'):>3} x87  "
              f"race {summary(race)}")


if __name__ == '__main__':
    main()
