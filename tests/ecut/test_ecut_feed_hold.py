"""Feed hold brakes along the planned path and resumes it; no board is needed."""
import importlib
import math
import struct
import threading
import time
import types

import pytest

from appPlugins.Common.ecut import Cmd, State, decode_rx_report
from appPlugins.Common.ecut import ecut_controller
from appPlugins.Common.ecut.ecut_config import EcutBoardConfig
from appPlugins.Common.ecut.ecut_controller import _HoldableRecords
from test_ecut_verified import Board, report
from appPlugins.Common.ecut.ecut_planner import LinearMove, iter_path_segments, plan_linear_path


def make_path(config, target=(20.0, 10.0), feed=1200.0):
    blocks = plan_linear_path(config, (0,) * 6, [LinearMove((target[0], target[1], None, None, None, None), False, feed)])
    return iter_path_segments(blocks, junction_deviation_mm=config.junction_deviation_mm)


def speeds(records, config):
    scale = config.axes[0].steps_per_mm * config.interpolation_factor
    return [math.hypot(r[0] / scale, r[1] / scale) * 1000 for r in records]


def test_without_hold_the_planned_records_pass_unchanged():
    config = EcutBoardConfig()
    path = make_path(config)
    records = _HoldableRecords(path, config, lambda: False)
    out = []
    while not records.done:
        out.extend(records.take(4))
    assert out == list(path)


def test_hold_brakes_at_the_deceleration_limit_and_resume_completes_the_path():
    config = EcutBoardConfig()
    for axis in config.axes:
        axis.acceleration_mm_s2 = 200.0
    path = make_path(config)
    total = [sum(r[i] for r in path) for i in range(6)]
    state = {'held': False}
    records = _HoldableRecords(path, config, lambda: state['held'])
    out = []
    out.extend(records.take(len(path) // 2))
    hold_at = len(out)
    state['held'] = True
    while not records.stopped:
        chunk = records.take(4)
        assert chunk or records.stopped
        out.extend(chunk)
    braking = len(out) - hold_at
    assert records.take(4) == []  # held and stopped: nothing to send
    state['held'] = False
    while not records.done:
        out.extend(records.take(4))
    assert [sum(r[i] for r in out) for i in range(6)] == total
    v = speeds(out, config)
    cruise = v[hold_at - 1]
    # about cruise / deceleration of braking time; the limit along the path is the axis limit / direction
    limit = min(200.0 * math.hypot(2, 1) / 2, 200.0 * math.hypot(2, 1) / 1)
    assert abs(braking - cruise / limit * 1000) <= 3
    changes = [abs(b - a) * 1000 for a, b in zip(v, v[1:])]
    # rounding to whole counts adds about 1 count per record of noise
    noise = 2 / (config.axes[0].steps_per_mm * config.interpolation_factor) * 1000 * 1000
    assert max(changes[hold_at - 1:]) <= max(changes[:hold_at - 1]) + noise
    assert all(max(abs(c) for c in r) <= 32767 for r in out)


def test_hold_at_a_stopped_point_stops_at_once():
    config = EcutBoardConfig()
    path = make_path(config)
    records = _HoldableRecords(path, config, lambda: True)
    # the path starts at rest: a hold before the first record sends nothing
    assert records.take(4) == []
    assert records.stopped


class Clock:
    """Fake time of the E-Cut controller: sleeping moves it, so a 5 s deadline costs no real time."""

    def __init__(self):
        self.now = 0.0
        self.module = types.ModuleType('time')
        self.module.__dict__.update(vars(time))
        self.module.monotonic = lambda: self.now
        self.waiting = None  # while it returns True the sleep also takes real time: a port thread is working
        self.module.sleep = self.sleep

    def sleep(self, seconds):
        if self.waiting is not None and self.waiting():
            time.sleep(0.001)
        self.now += max(seconds, 0.001)


class StreamBoard(Board):
    """FIFO model: every poll runs 1 record; busy while records wait or until `busy_until`."""

    def __init__(self, clock):
        super().__init__()
        self.positions = [0] * 6
        self.is_open = False
        self.clock = clock
        self.queue, self.sent, self.events = [], [], []
        self.busy_until = 0.0
        self.trigger = None
        self.hold = None  # while it returns True the board runs no record
        self.stream_end = self.stop_at = None

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def cmd(self, opcode, params=b''):
        super().cmd(opcode, params)
        if int(opcode) == int(Cmd.END_STREAM):
            self.stream_end = self.clock.now
        if int(opcode) == int(Cmd.STOP):
            self.stop_at = self.clock.now
            self.queue.clear()
            self.busy_until = 0.0
        self.events.append(int(opcode))

    def send_motion(self, records):
        for record in records:
            deltas = struct.unpack_from('>6h', record, 4)
            self.queue.append(deltas)
            self.sent.append(deltas)
            self.events.append('record')

    def poll(self):
        if self.trigger is not None and self.trigger(self):
            self.trigger = None
        if self.clock.now >= self.busy_until and not (self.hold is not None and self.hold()):
            for _ in range(min(1, len(self.queue))):
                self.positions = [p + d for p, d in zip(self.positions, self.queue.pop(0))]
        busy = bool(self.queue) or self.clock.now < self.busy_until
        return decode_rx_report(report(tuple(self.positions), status=0x81 if busy else 1,
                                       fifo=max(0, 12000 - 20 * len(self.queue))))


def run_stop(monkeypatch, moment):
    """Run one 10 mm move through the real port and send 0x85 at `moment`; return the evidence."""
    sender_port = importlib.import_module('appPlugins.Common.ecut.ecut_sender_port')
    clock = Clock()
    monkeypatch.setattr(ecut_controller, 'time', clock.module)
    raised = []

    class Counted(sender_port._Interrupted):
        def __init__(self):
            super().__init__()
            raised.append(1)
    monkeypatch.setattr(sender_port, '_Interrupted', Counted)
    api = ecut_controller.EcutAPI()
    board = api._usb = StreamBoard(clock)
    states = []
    api.on_state_change(states.append)
    port = sender_port.EcutSenderPort(api=api, timeout=0.2)
    try:
        assert b'E-Cut host adapter' in port.readline()
        api._homed_axes.update({0, 1})
        requested = []

        def fire(board):
            if moment == 'records':
                due = len(board.sent) >= 40
            elif moment == 'fifo':
                due = len(board.queue) >= 250  # fifo_free <= 7000: the loop waits for space
            elif moment == 'drain':
                due = board.stream_end is not None
            elif moment == 'busy':  # stays busy until the board gets Cmd.STOP
                if board.stream_end is not None:
                    board.busy_until = float('inf')
                due = board.stream_end is not None and clock.now >= board.stream_end + 0.5
            else:  # 'late': 0.1 s before the drain wait ends; the board is busy 1 s more
                if board.stream_end is not None and board.busy_until == 0.0:
                    board.busy_until = board.stream_end + 2.9
                due = board.stream_end is not None and clock.now >= board.stream_end + 1.9
            if due:
                requested.append(clock.now)
                # the port's own thread takes the byte; until the program is stopping the board must not
                # drain, and the fake time must not run out meanwhile (a loaded PC makes that thread slow)
                if moment in ('records', 'fifo', 'drain'):
                    taken = []

                    def in_flight():
                        if api.stop_pending:
                            taken.append(True)
                        return not taken
                    board.hold = clock.waiting = in_flight
                port.write(b'\x85')
            return due
        board.trigger = fire
        port.write(b'G91G1X10F600\n')
        deadline = time.monotonic() + 60  # real time, only a guard against a hang
        while (not raised or api.state != State.IDLE or port._active) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert raised and api.state == State.IDLE, 'the program did not end'
        first = board.events.index('record')
        events = board.events[first:]  # after the start-up; the close of the port is not part of it
        homed, stop_at = api.homed_axes, board.stop_at
    finally:
        port.close()
    return types.SimpleNamespace(api=api, board=board, states=states, raised=raised, requested=requested[0],
                                 events=events, homed=homed, stop_at=stop_at)


@pytest.mark.parametrize('moment', ['records', 'fifo', 'drain'])
def test_stop_decelerates_and_keeps_reference(monkeypatch, moment):
    run = run_stop(monkeypatch, moment)
    board, config = run.board, run.api.config
    stops = [i for i, event in enumerate(run.events) if event == int(Cmd.STOP)]
    assert len(stops) == 1 and stops[0] > max(i for i, event in enumerate(run.events) if event == 'record')
    assert len(run.raised) == 1
    assert run.homed == (0, 1)
    # no resume: the state goes PAUSED and then IDLE, never back to RUNNING
    assert State.PAUSED in run.states and run.states[run.states.index(State.PAUSED):].count(State.RUNNING) == 0
    v = speeds(board.sent, config)
    assert v[-1] <= max(v) / 3  # the stream ends with falling speed
    full = 10 * config.axes[0].steps_per_mm * config.interpolation_factor
    moved = sum(r[0] for r in board.sent)
    assert (moved < full * 0.9) if moment != 'drain' else abs(moved - full) <= 2


def test_stop_at_the_deadline_clears_the_reference(monkeypatch):
    run = run_stop(monkeypatch, 'busy')
    assert run.stop_at - run.requested == pytest.approx(5.0, abs=0.1)
    assert run.homed == ()
    assert State.ERROR not in run.states


def test_stop_requested_near_the_drain_end_does_not_raise_the_timeout(monkeypatch):
    run = run_stop(monkeypatch, 'late')
    assert State.ERROR not in run.states
    assert run.stop_at - run.requested < 5.0  # the board went idle: the normal end of the stop
    assert run.homed == (0, 1)
