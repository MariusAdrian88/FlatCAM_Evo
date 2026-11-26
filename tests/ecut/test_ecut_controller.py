"""E-Cut controller tests. No hardware: the USB layer is a fake, nothing opens a device."""

import struct
from types import SimpleNamespace

import pytest

from appPlugins.Common.ecut import (
    EcutAPI, EcutBoardConfig, AxisDirection, SignalPolarity,
    SpindleControl, State, EcutError,
    encode_motion_record, decode_rx_report, ecut_crc60, Cmd,
)
from appPlugins.Common.ecut import ecut_controller


class FakeUsb:
    def __init__(self):
        self.is_open = False
        self.cmds = []
        self.motion_packets = []
        self.on_motion = None
        self.positions = [0] * 6
        self.pending = None

    @property
    def writes(self):
        return len(self.cmds) + len(self.motion_packets)

    def cmd(self, opcode, params=b''):
        self.cmds.append((opcode, params))

    def send_motion(self, records):
        self.motion_packets.append(list(records))
        for record in records:
            for i, delta in enumerate(struct.unpack_from('>6h', record, 4)):
                self.positions[i] += delta
        self.pending = len(self.motion_packets)

    def poll(self):
        if self.pending is not None:
            pending, self.pending = self.pending, None
            if self.on_motion:
                self.on_motion(pending)
        from test_ecut_verified import report
        return decode_rx_report(report(tuple(self.positions)))

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False


def make_api(period=0.001, steps_per_mm=100.0, interpolation=3):
    api = EcutAPI()
    for i in range(6):
        api.configure_axis(i, steps_per_mm=steps_per_mm)
    api.set_interpolation(interpolation)  # 16x
    api.config.motion_period_s = period
    api._usb = FakeUsb()
    api._usb.is_open = True
    api._state = State.IDLE
    # Planner tests exercise alternate scaling entirely against the simulated board.
    # Captured hardware calibration/initialization is covered by test_ecut_verified.
    api._initialized = True
    api.encode_config = lambda: b''
    return api


def sent_deltas(api):
    out = []
    for packet in api._usb.motion_packets:
        for rec in packet:
            out.append(struct.unpack('>6h', rec[4:16]))
    return out


def test_mpg_switch_sends_config():
    cfg = EcutBoardConfig()
    cfg.mpg.enabled = True
    cfg.mpg.port_select = 5
    with pytest.raises(EcutError, match='MPG'):
        ecut_controller.encode_config(cfg)
    cfg.unverified.mpg = True
    packets = ecut_controller.encode_config(cfg)
    mpg = next(packets[n:n + 65] for n in range(0, len(packets), 65) if packets[n + 1] == Cmd.MPG_CONFIG)
    assert struct.unpack_from('>I', mpg, 36)[0] == 5   # wire 35..38: the selected counter index


def test_config():
    config = EcutBoardConfig()
    config.axes[0].steps_per_mm = 200.0
    config.axes[0].direction = AxisDirection.FORWARD
    config.inputs['PROBE'].enabled = True
    config.inputs['PROBE'].pin = 5
    config.inputs['X_LIMIT_PLUS'].polarity = SignalPolarity.ACTIVE_LOW
    config.spindle.control = SpindleControl.PWM
    config.motion_period_s = 0.004

    config2 = EcutBoardConfig.from_dict(config.to_dict())
    assert config2.axes[0].steps_per_mm == 200.0
    assert config2.inputs['PROBE'].pin == 5
    assert config2.spindle.control == SpindleControl.PWM
    assert config2.motion_period_s == 0.004
    assert EcutBoardConfig().motion_period_s == 0.001
    assert EcutBoardConfig.from_dict({}).motion_period_s == 0.001


def test_axis_a_is_off_by_default_and_a_saved_value_is_kept():
    assert [a.enabled for a in EcutBoardConfig().axes] == [True, True, True, False, True, True]
    config = EcutBoardConfig()
    config.axes[3].enabled = True
    assert EcutBoardConfig.from_dict(config.to_dict()).axes[3].enabled


def test_step_conversion():
    api = make_api(interpolation=4)  # 32x
    api.configure_axis(0, steps_per_mm=200.0)
    api.configure_axis(1, steps_per_mm=200.0)
    api.configure_axis(2, steps_per_mm=400.0)
    deltas = api._acc.deltas([0.1, 0.05, 0.02, None, None, None])
    assert deltas[0] == 640
    api._acc.advance(deltas)
    assert api._acc.steps[0] == 640
    deltas2 = api._acc.deltas([0.2, 0.1, 0.04, None, None, None])
    assert deltas2[0] == 640


def test_gcode_parsing():
    api = make_api(steps_per_mm=100.0)
    for line in ("G21", "G0 X10 Y10", "G1 X20 Y20 F500", "G0 Z5"):
        api._exec_line(line)
    # 20 mm * 100 steps/mm * 16
    assert api._acc.steps[:3] == [32000, 32000, 8000]
    assert api.position_mm[:3] == [20.0, 20.0, 5.0]




def test_config_persistence(tmp_path):
    api = EcutAPI()
    api.configure_axis(0, steps_per_mm=200.0, direction=AxisDirection.REVERSE)
    api.config.soft_limit_tolerance_mm = 0.02
    api.config.output_labels[0] = 'Vacuum'
    path = tmp_path / 'machine.json'
    api.save_config(path)
    api2 = EcutAPI()
    api2.load_config(path)
    assert api2.config.axes[0].steps_per_mm == 200.0
    assert api2.config.axes[0].direction == AxisDirection.REVERSE
    assert api2.config.soft_limit_tolerance_mm == 0.02
    assert api2.config.output_labels[0] == 'Vacuum'


def test_config_save_refuses_an_invalid_profile(tmp_path):
    api = EcutAPI()
    api.config.motion_period_s = 0.005
    path = tmp_path / 'machine.json'
    with pytest.raises(EcutError):
        api.save_config(path)
    assert not path.exists()


# ----------------------------------------------------------------------
# Audit fixes
# ----------------------------------------------------------------------

def test_long_move_is_not_clipped():
    """100 mm at 100 steps/mm x16 = 160000 steps: used to be clamped to 32767 (20.5 mm)."""
    api = make_api()
    api._exec_line("G1 X100 F6000")
    segs = sent_deltas(api)
    assert len(segs) > 1
    assert all(abs(v) <= 32767 for seg in segs for v in seg)
    assert sum(seg[0] for seg in segs) == 160000
    assert api._acc.steps[0] == 160000
    assert api.position_mm[0] == 100.0


def test_long_jog_step_is_split():
    api = make_api()
    api.jog_step(0, 100.0)
    assert sum(seg[0] for seg in sent_deltas(api)) == 160000
    assert all(abs(seg[0]) <= 32767 for seg in sent_deltas(api))


def test_diagonal_move_keeps_direction():
    api = make_api()
    api.configure_axis(1, steps_per_mm=100.0)
    api._exec_line("G1 X100 Y-50 F6000")
    segs = sent_deltas(api)
    assert sum(s[0] for s in segs) == 160000
    assert sum(s[1] for s in segs) == -80000
    cum_x = cum_y = 0
    for s in segs:
        assert s[0] >= 0 and s[1] <= 0
        cum_x += s[0]
        cum_y += s[1]
        # same fraction on every axis, within one step of rounding
        assert abs(cum_y * 2 - -cum_x) <= 2 or abs(cum_y + cum_x / 2) <= 1


def test_feed_changes_segment_count():
    slow = make_api()
    slow._exec_line("G1 X10 F60")
    fast = make_api()
    fast._exec_line("G1 X10 F10000")
    n_slow = len(sent_deltas(slow))
    n_fast = len(sent_deltas(fast))
    assert n_slow > n_fast
    assert slow._acc.steps[0] == fast._acc.steps[0] == 16000


def test_record_travel_never_exceeds_feed_times_period():
    api = make_api(period=0.001)
    api._exec_line("G1 X10 F600")  # 10 mm/s
    limit_steps = 10.0 * 0.001 * 100 * 16
    for seg in sent_deltas(api):
        assert abs(seg[0]) <= limit_steps + 1


def test_feed_override_scales_timing():
    base = make_api()
    base._exec_line("G1 X10 F600")
    half = make_api()
    half.feed_override = 0.5
    half._exec_line("G1 X10 F600")
    assert len(sent_deltas(half)) > len(sent_deltas(base))


def test_rapid_ignores_feed_but_respects_axis_velocity():
    api = make_api(period=0.001)
    api._exec_line("G0 X10")
    # axis max velocity 50 mm/s, period 0.01 s: at most 0.5 mm = 800 steps per record
    assert all(abs(seg[0]) <= 801 for seg in sent_deltas(api))
    assert api._acc.steps[0] == 16000


def test_g1_without_feed_refused():
    api = make_api()
    with pytest.raises(EcutError):
        api._exec_line("G1 X10")
    assert api._usb.writes == 0


def test_no_motion_period_refuses_with_zero_writes():
    api = make_api(period=None)
    with pytest.raises(EcutError, match='motion timing is not configured/verified'):
        api._exec_line("G1 X10 F500")
    with pytest.raises(EcutError, match='motion timing is not configured/verified'):
        api._exec_line("G0 X10")
    assert api._usb.writes == 0
    assert api._acc.steps == [0] * 6


def test_packets_never_exceed_record_limit():
    api = make_api()
    api._exec_line("G1 X100 F600")
    assert len(api._usb.motion_packets) > 1
    assert all(1 <= len(p) <= ecut_controller.MAX_MOTION_RECORDS_PER_PACKET for p in api._usb.motion_packets)


def test_estop_mid_program_stops_writes_and_stays_estop():
    api = make_api()
    # the E-stop arrives (e.g. from the UI) after the 2nd motion packet of the first move
    api._usb.on_motion = lambda n: api.estop() if n == 2 else None
    program = ["G1 X100 F600", "G1 X200 F600", "G0 Z5"]
    with pytest.raises(EcutError):
        api.run_gcode(program)
    assert api.state == State.ESTOP
    writes_at_estop = api._usb.writes
    # no motion after the E-stop, from any path
    for call in (lambda: api._exec_line("G1 X5 F100"), lambda: api.jog_step(0, 1.0),
                 lambda: api.run_gcode(["G0 X1"])):
        with pytest.raises(EcutError):
            call()
    assert [c for c in api._usb.cmds if c[0] not in (Cmd.ESTOP, Cmd.SET_OUTPUTS)] == []
    assert api._usb.writes == writes_at_estop
    assert api.state == State.ESTOP
    api.stop()
    assert api.state == State.ESTOP
    api.reset()
    assert api.state == State.IDLE
    api._exec_line("G0 X1")
    assert api._usb.writes > writes_at_estop


def test_connect_does_not_push_config(monkeypatch):
    api = EcutAPI()
    fake = FakeUsb()
    api._usb = fake
    assert api.connect() is True
    assert api.state == State.IDLE
    assert fake.cmds == [] and fake.motion_packets == []


def test_unverified_configuration_refused_before_usb_writes():
    api = EcutAPI()
    api._usb = FakeUsb()
    api._usb.is_open = True
    api.configure_axis(0, steps_per_mm=100.0)
    api.config.axes[0].backlash_compensation_enabled = True
    with pytest.raises(EcutError, match='not yet verified'):
        api.push_config()
    assert api._usb.writes == 0


def test_set_position_rounds_like_target_conversion():
    api = make_api(steps_per_mm=100.0, interpolation=3)  # 1600 steps/mm
    api._usb.is_open = False
    api.set_position(0, 0.0009999)  # 1.5998 steps
    assert api.position_steps[0] == 2
    assert api._acc.to_steps([0.0009999, None, None, None, None, None])[0] == 2


def dwell_api(monkeypatch, state=State.RUNNING):
    """Dwell-ready API on a fake clock: every sleep takes 15 ms, status always arrives."""
    api = make_api()
    api._running = True
    api._state = state
    polls = [0]

    def wait_for_rx(timeout=0.5):
        polls[0] += 1
        assert polls[0] < 1000, 'dwell does not end'
        return object()
    api._wait_for_rx = wait_for_rx
    clock = [0.0]

    def sleep(seconds):
        clock[0] += 0.015
    monkeypatch.setattr(ecut_controller, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    return api, clock


@pytest.mark.parametrize('state', [State.RUNNING, State.IDLE])
def test_dwell_uses_deadline(monkeypatch, state):
    api, clock = dwell_api(monkeypatch, state)
    api._dwell(0.1)
    assert 0.100 <= clock[0] <= 0.115


def test_dwell_ends_when_the_stop_is_requested(monkeypatch):
    api, clock = dwell_api(monkeypatch)
    api._stop_requested = True
    api._dwell(5.0)
    assert clock[0] < 0.1
