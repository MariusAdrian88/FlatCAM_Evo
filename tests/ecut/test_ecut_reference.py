"""Manual machine home (reference) and soft limits with a tolerance; no board is needed."""
from types import SimpleNamespace

import pytest

from appPlugins.Common.ecut import Cmd, EcutError, State
from appPlugins.Common.ecut.ecut_controller import _MoveAction
from appPlugins.Common.ecut.ecut_gcode_engine import GcodeError
from test_ecut_gcode_engine import engine_api


def limited_engine(tolerance=0.05):
    engine, api = engine_api()
    for axis in api.config.axes[:3]:
        axis.soft_limits_enabled = True
        axis.soft_limit_negative_mm = 0
        axis.soft_limit_positive_mm = 2
        axis.max_velocity_mm_s = 2
        axis.acceleration_mm_s2 = 10
    api.config.soft_limit_tolerance_mm = tolerance
    api.push_config()
    return engine, api


def test_reference_sets_the_home_counts_and_enables_soft_limits():
    engine, api = limited_engine()
    api._usb.positions[0] = 123456
    api._acc.steps[0] = 123456
    api.config.homing[2].home_offset_mm = 0  # Z homes at the top: Z = 0 there
    api.reference_axes([0, 2])
    assert api._usb.positions[0] == 0 and api._usb.positions[2] == 0
    assert api.homed_axes == (0, 2)
    assert api.position_steps[0] == 0


def test_reference_uses_the_home_offset():
    engine, api = limited_engine()
    api.config.homing[1].home_offset_mm = 1.5
    api.push_config()
    api.reference_axes([1])
    assert api.position_mm[1] == pytest.approx(1.5)


def test_reference_refused_when_the_board_does_not_confirm():
    engine, api = limited_engine()
    original = api._usb.cmd

    def ignore_a0(opcode, params=b''):
        if int(opcode) == 0xa0:
            return
        original(opcode, params)
    api._usb.cmd = ignore_a0
    api._usb.positions[0] = api._acc.steps[0] = 5000
    with pytest.raises(EcutError, match='did not confirm'):
        api.reference_axes([0])
    assert api.homed_axes == ()


def test_reference_waits_for_the_board_to_apply_the_counts():
    # the real board applies a0 about 10 ms later: the first idle frames still show the old counts
    engine, api = limited_engine()
    original_cmd, original_poll = api._usb.cmd, api._usb.poll
    held = []

    def late_a0(opcode, params=b''):
        if int(opcode) == 0xa0:
            held.append([3, opcode, params])
            return
        original_cmd(opcode, params)

    def poll():
        if held:
            held[0][0] -= 1
            if held[0][0] == 0:
                _, opcode, params = held.pop()
                original_cmd(opcode, params)
        return original_poll()
    api._usb.cmd, api._usb.poll = late_a0, poll
    api.config.homing[0].home_offset_mm = 1.0
    api.push_config()
    api.reference_axes([0])
    assert api.homed_axes == (0,)
    assert api.position_mm[0] == pytest.approx(1.0)


def test_soft_limits_apply_without_a_machine_home():
    engine, api = limited_engine()
    assert api.homed_axes == ()
    with pytest.raises(GcodeError, match='Soft limit') as error:
        engine.execute_line('G53 G0 X3')
    assert error.value.code == 15
    with pytest.raises(GcodeError, match='Soft limit'):
        engine.execute_line('$J=G21 G90 X-1 F120')
    assert engine.execute_line('G53 G0 X1') == ['ok']


def test_tolerance_allows_a_target_just_outside_the_limit():
    engine, api = limited_engine(tolerance=0.05)
    api.reference_axes([0])
    assert engine.execute_line('G53 G0 X2.04') == ['ok']
    with pytest.raises(GcodeError, match='Soft limit'):
        engine.execute_line('G53 G0 X2.06')
    assert engine.execute_line('G53 G0 X-0.04') == ['ok']


def test_an_axis_outside_the_limits_can_move_back_in():
    engine, api = limited_engine()
    api.reference_axes([0])
    engine.execute_line('G53 G0 X1.5')
    # the limit changes while the axis is at 1.5: the axis is now outside
    api.config.axes[0].soft_limit_positive_mm = 1
    api.push_config()
    assert api.homed_axes == (0,)
    assert engine.execute_line('G53 G0 X1.2') == ['ok']  # closer to the window
    with pytest.raises(GcodeError, match='Soft limit'):
        engine.execute_line('G53 G0 X1.3')  # farther out
    assert engine.execute_line('G53 G0 X0.5') == ['ok']
    assert api.state == State.IDLE


@pytest.mark.parametrize('frame', [
    SimpleNamespace(motion_busy=True, fifo_free=12000),
    SimpleNamespace(motion_busy=False, fifo_free=11000),
    None])
def test_stop_of_a_moving_board_removes_the_reference(frame):
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api._last_rx = frame   # busy, a FIFO that is not empty, or no frame: the board can have moved
    api.stop()
    assert api.homed_axes == ()


@pytest.mark.parametrize('flag', ['_native_motion_active', '_jog_active'])
def test_stop_during_a_native_move_or_a_jog_removes_the_reference(flag):
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    setattr(api, flag, True)   # the last frame still shows the board idle
    api.stop()
    assert api.homed_axes == ()


@pytest.mark.parametrize('busy, homed', [(True, ()), (False, (0, 1))])
def test_reset_in_motion_forgets_reference(busy, homed):
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api._last_rx = SimpleNamespace(motion_busy=busy, fifo_free=12000)
    api.push_config()
    assert api.homed_axes == homed


def referenced_xyz():
    engine, api = reversed_x_engine()
    api.reference_axes([1, 2])
    assert tuple(sorted(api.homed_axes)) == (0, 1, 2) and any(api._backlash_offset)
    return engine, api


def reference_of(api):
    return tuple(sorted(api.homed_axes)), tuple(api._backlash_offset), tuple(api._backlash_dir)


@pytest.mark.parametrize('end', ['M30', 'M2'])
def test_a_program_that_ends_normally_keeps_the_reference(end):
    engine, api = referenced_xyz()
    before, x = reference_of(api), api.position_mm[0]
    api.run_gcode(['G0 X20', 'G0 X10', end])   # a reversal in the program, the path drained before the end
    assert reference_of(api) == before
    assert api.position_mm[0] == pytest.approx(x)


def test_a_stop_request_during_a_dwell_keeps_the_reference():
    engine, api = referenced_xyz()
    before = reference_of(api)
    original, fired = api._usb.poll, []

    def poll():
        if api._running and not fired:
            fired.append(1)
            api.request_stop()   # the 0x85 of the host: nothing streams, the board is stationary
        return original()
    api._usb.poll = poll
    api.run_gcode(['G4 P1', 'M30'])
    assert fired and reference_of(api) == before


def test_a_stop_request_during_an_m0_pause_keeps_the_reference():
    engine, api = referenced_xyz()
    before, seen = reference_of(api), []

    def operator(state):
        if state == State.PAUSED and not seen:
            seen.append(1)
            api.request_stop()
    api.on_state_change(operator)
    api.run_gcode(['M0', 'G0 X20'])
    assert seen and reference_of(api) == before


def test_a_stop_while_records_are_submitted_removes_the_reference_without_a_program():
    # a move outside a program: no _running; the last frame is the one from before the first record
    engine, api = referenced_xyz()
    api._poll()
    send, seen = api._usb.send_motion, []

    def send_then_stop(records):
        send(records)
        if not seen:
            api._last_rx = SimpleNamespace(motion_busy=False, fifo_free=12000)   # stale: from before the record
            api.stop()
            seen.append(tuple(sorted(api.homed_axes)))
    api._usb.send_motion = send_then_stop
    path = api._plan_linear_actions([(1, _MoveAction(True, True, {0: 20.0}, None))], tuple(api._acc.steps))
    assert not api._running
    try:
        api._run_action(path)
    except EcutError:
        pass
    assert seen == [()]


def test_estop_polls_nothing_before_the_estop_command():
    engine, api = referenced_xyz()
    polls, opcodes = [], []
    original, send = api._usb.poll, api._usb.cmd
    api._usb.poll = lambda: polls.append(1) or original()
    api._usb.cmd = lambda opcode, params=b'': opcodes.append(opcode) or send(opcode, params)
    api._last_rx = SimpleNamespace(motion_busy=True, fifo_free=12000)
    api.estop()
    assert polls == [] and api.homed_axes == ()
    assert Cmd.ESTOP in opcodes


def reversed_x_engine():
    engine, api = engine_api()
    ax = api.config.axes[0]
    ax.steps_per_mm = 25600 // api.config.interpolation_factor
    ax.max_velocity_mm_s, ax.acceleration_mm_s2 = 100, 1000
    ax.backlash_mm, ax.backlash_compensation_enabled = 0.05, True
    api.config.unverified.backlash = True
    api.push_config()
    api.reference_axes([0])
    for line in ('G0 X10', 'G0 X20', 'G0 X10'):   # X+ X+ X-: one reversal
        engine.execute_line(line)
    return engine, api


def test_backlash_offset_survives_an_idle_reset():
    engine, api = reversed_x_engine()
    assert api._usb.positions[0] == 10 * 25600 - 1280   # the board went 0.05 mm further down
    assert api.position_mm[0] == pytest.approx(10.0)
    api.push_config()
    assert api.position_mm[0] == pytest.approx(10.0) and api.homed_axes == (0,)
    api.config.axes[0].backlash_mm = 0.1
    api.push_config()
    assert api.homed_axes == ()


@pytest.mark.parametrize('programs,intervals', [
    ([['G1 X10 F600', 'G1 X0']], (7, 50)),
    ([['G1 X5 F1200', 'G1 X0', 'G1 X5', 'G1 X0']], (7, 50)),
    ([['G1 X10 F600'], ['G1 X0 F600']], (7, 20, 50))])   # the 2nd path starts with a reversal
def test_reported_position_with_backlash_is_right_for_frames_at_any_interval(programs, intervals):
    # A frame within the compensation counts of a reversal can show a position that the board has on the way
    # to the reversal and on the way back: only the intervals above never hit that.
    import struct
    from test_ecut_controller import FakeUsb, make_api

    class SampledUsb(FakeUsb):
        def __init__(self, api, every):
            super().__init__()
            self.api, self.every, self.count, self.samples = api, every, 0, []

        def send_motion(self, records):
            self.motion_packets.append(list(records))
            for record in records:
                for i, delta in enumerate(struct.unpack_from('>6h', record, 4)):
                    self.positions[i] += delta
                self.count += 1
                if self.count % self.every == 0:
                    frame = SimpleNamespace(axis_positions=tuple(self.positions))
                    scale = self.api.config.axes[0].steps_per_mm * self.api.config.interpolation_factor
                    self.samples.append((self.count, self.positions[0] / scale, self.api.logical_position_mm(frame)[0]))
            self.pending = len(self.motion_packets)

    def run(every):
        api = make_api()
        api.config.axes[0].backlash_mm = 0.05
        api.config.axes[0].backlash_compensation_enabled = True
        api.config.unverified.backlash = True
        api.reference_axes([0])
        usb = SampledUsb(api, every)
        usb.is_open = True
        api._usb = usb
        for program in programs:
            api.run_gcode(program)
        return api, usb.samples

    api, dense = run(1)
    # the reported X never moves more than the board does between 2 frames, and ends at the program end
    assert all(abs(b[2] - a[2]) <= abs(b[1] - a[1]) + 1e-9 for a, b in zip(dense, dense[1:]))
    assert dense[-1][2] == pytest.approx(api.position_mm[0], abs=1e-4)
    truth = {count: logical for count, _board, logical in dense}
    for every in intervals:
        _api, sparse = run(every)
        assert sparse and all(logical == pytest.approx(truth[count], abs=1e-6) for count, _b, logical in sparse)


def test_reported_x_does_not_move_before_the_board_reaches_the_reversal():
    from test_ecut_controller import make_api
    api = make_api()
    scale = api.config.axes[0].steps_per_mm * api.config.interpolation_factor
    api.config.axes[0].backlash_mm = 0.05
    api.config.axes[0].backlash_compensation_enabled = True
    api.config.unverified.backlash = True
    api.reference_axes([0])
    reports = []

    def on_motion(_packet):   # a frame of the board after each packet
        frame = api._usb.poll()
        reports.append((frame.axis_positions[0] / scale, api.logical_position_mm(frame)[0]))

    api._usb.on_motion = on_motion
    api.run_gcode(['G1 X10 F600', 'G1 X0'])
    assert len(reports) > 2
    # the reported X is the board X until the board is at the reversal (X 10): the offset is not applied early
    outward = []
    for board, reported in reports:   # the frames until the board turns back
        if outward and board < outward[-1][0]:
            break
        outward.append((board, reported))
    assert len(outward) > 2 and outward[-1][0] > 9
    assert all(reported == pytest.approx(board, abs=1e-4) for board, reported in outward)
    assert api.position_mm[0] == pytest.approx(0, abs=1e-4)


def test_unchanged_config_push_keeps_the_logical_x_of_an_unhomed_axis():
    engine, api = engine_api()
    ax = api.config.axes[0]
    ax.steps_per_mm = 25600 // api.config.interpolation_factor
    ax.max_velocity_mm_s, ax.acceleration_mm_s2 = 100, 1000
    ax.backlash_mm, ax.backlash_compensation_enabled = 0.05, True
    api.config.unverified.backlash = True
    api.push_config()
    for line in ('G0 X10', 'G0 X0'):   # X+ X-: one reversal, no home
        engine.execute_line(line)
    assert api.homed_axes == ()
    before = api.position_mm[0]
    api.push_config()
    assert api.position_mm[0] == pytest.approx(before)
    assert api.logical_position_mm()[0] == pytest.approx(before)


def test_a_stop_between_two_reversals_removes_the_reference():
    # X20 -> X10 -> X20: the offsets of the group end where they started, but the stop falls in between
    engine, api = reversed_x_engine()
    engine.execute_line('G0 X20')
    assert api.homed_axes == (0,)
    path = api._plan_linear_actions(
        [(1, _MoveAction(True, True, {0: 10.0}, None)), (2, _MoveAction(True, True, {0: 20.0}, None))],
        tuple(api._acc.steps))
    send = api._usb.send_motion

    def send_then_fail(records):
        send(records)
        if api._acc.steps[0] < 15 * 25600:
            raise EcutError('stop in the negative leg')
    api._usb.send_motion = send_then_fail
    with pytest.raises(EcutError, match='negative leg'):
        api._run_action(path)
    assert api.homed_axes == ()


def test_status_and_probe_position_are_logical_after_a_reversal():
    engine, api = reversed_x_engine()
    api._poll()
    report = engine.status_report()
    assert 'Pos:10.000,' in report
    with pytest.raises(GcodeError, match='backlash'):   # a calibration move is not planned with offsets
        engine.calibration_move(0, 1, 60, engine.get_motion_settings()['revision'])


def test_a_scale_change_removes_the_reference():
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api.config.axes[0].steps_per_mm = 400
    api.push_config()
    assert api.homed_axes == (1,)
