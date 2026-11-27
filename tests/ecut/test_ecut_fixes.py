"""E-Cut defect-fix tests: pause, G-code validation, unverified features refused, config and transport checks.

No hardware: the USB layer is a fake, nothing opens a device.
"""

import threading

import pytest

from appPlugins.Common.ecut import (
    EcutAPI, EcutBoardConfig, SignalPolarity, State, EcutError, EcutConfigError, EcutRxFrame,
    EcutUsb, EcutUsbError, Cmd,
)
from test_ecut_controller import make_api, sent_deltas


def _later(delay, fn):
    t = threading.Timer(delay, fn)
    t.start()
    return t


# ----- 1. pause ---------------------------------------------------------

def test_pause_blocks_submission_until_resume():
    api = make_api()
    at_resume = []

    def on_motion(n):
        if n == 1:
            api.pause()

            def resume():
                at_resume.append(len(api._usb.motion_packets))
                api.resume()
            _later(0.15, resume)

    api._usb.on_motion = on_motion
    api.run_gcode(["G0 X1", "G0 X2"])
    # the hold sends 1 braking packet along the path, then nothing until resume
    assert at_resume == [2]
    assert len(api._usb.motion_packets) > 1
    assert api._acc.steps[0] == 2 * 100 * 16
    assert api.state == State.IDLE


def test_stop_while_paused_ends_run():
    api = make_api()

    def on_motion(n):
        if n == 1:
            api.pause()
            _later(0.1, api.stop)

    api._usb.on_motion = on_motion
    api.run_gcode(["G0 X1", "G0 X2"])
    assert len(api._usb.motion_packets) == 2  # the packet in flight and its braking tail
    assert api.state == State.IDLE


def test_estop_while_paused_stays_estop():
    api = make_api()

    def on_motion(n):
        if n == 1:
            api.pause()
            _later(0.1, api.estop)

    api._usb.on_motion = on_motion
    with pytest.raises(EcutError):
        api.run_gcode(["G0 X1", "G0 X2"])
    assert api.state == State.ESTOP
    assert len(api._usb.motion_packets) == 2  # the packet in flight and its braking tail


# ----- 2. G-code --------------------------------------------------------

def test_comments_and_blank_lines_are_skipped():
    api = make_api()
    api.run_gcode(["", "   ", "; comment", "(comment)", "G1 X1 (inline) F600 ; tail", "G0 Y1"])
    assert api._acc.steps[:2] == [1600, 1600]


def test_g20_inches_and_g91_incremental():
    api = make_api()
    api.run_gcode(["G20", "G0 X1", "G91", "G0 X1", "G21", "G0 X10", "G90", "G0 X0"])
    assert api._acc.steps[0] == 0
    api2 = make_api()
    api2.run_gcode(["G20", "G0 X1"])
    assert api2.position_mm[0] == pytest.approx(25.4, abs=1e-3)
    api3 = make_api()
    api3.run_gcode(["G0 X5", "G91", "G0 X2", "G0 X-1"])
    assert api3.position_mm[0] == pytest.approx(6.0, abs=1e-3)


def _planned_records(program):
    api = make_api(steps_per_mm=100, interpolation=0)
    api.config.axes[3].enabled = True
    api.run_gcode(program)
    return len(sent_deltas(api))


def test_inch_mode_keeps_the_rotary_feed_in_degrees_per_minute():
    assert _planned_records(["G20", "G1 A10 F60"]) == pytest.approx(_planned_records(["G21", "G1 A10 F60"]), rel=0.01)
    # an X word without an X displacement (at X0) is still a rotary move; relative moves too
    assert _planned_records(["G20", "G1 X0 A10 F60"]) == pytest.approx(_planned_records(["G21", "G1 X0 A10 F60"]), rel=0.01)
    assert _planned_records(["G20", "G91", "G1 X0 A10 F60"]) == pytest.approx(
        _planned_records(["G21", "G91", "G1 X0 A10 F60"]), rel=0.01)
    # a move with X displacement still converts the inch feed
    assert _planned_records(["G20", "G1 X1 F60"]) == pytest.approx(_planned_records(["G21", "G1 X25.4 F1524"]), rel=0.01)


@pytest.mark.parametrize('bad, word', [
    ("G2 X1 Y1 I1", 'G2'), ("S1000", 'S1000'), ("M3", 'M3'), ("T1.5", 'T1.5'), ("G99", 'G99'),
    ("G28", 'G28'), ("N10 G0 X1", 'N10'),
])
def test_unsupported_word_refused_before_anything_is_sent(bad, word):
    api = make_api()
    with pytest.raises(EcutError) as e:
        api.run_gcode(["G0 X1", "G0 X2", bad])
    assert 'line 3' in str(e.value) and word in str(e.value)
    assert api._usb.writes == 0
    assert api._acc.steps == [0] * 6
    assert api.state == State.IDLE


@pytest.mark.parametrize('program, msg', [
    (["G1 X1"], 'line 1'), (["X5"], 'motion mode'), (["G0 X1 X2"], 'duplicate'),
    (["G0 G1 X1"], 'conflicting'), (["G20 G21"], 'conflicting'),
    (["G0 X1 $"], 'cannot parse'), (["(open"], 'cannot parse'),
])
def test_program_validation_errors(program, msg):
    api = make_api()
    with pytest.raises(EcutError, match=msg):
        api.run_gcode(program)
    assert api._usb.writes == 0


def test_m2_stops():
    api = make_api()
    api.run_gcode(["G0 X1", "M2", "G0 X2"])
    assert api._acc.steps[0] == 1600
    assert (Cmd.STOP, b'') in api._usb.cmds


# ----- 3. homing / probing ---------------------------------------------

def test_homing_and_probing_refused_without_writes():
    api = make_api()
    for call in (api.home_all, lambda: api.home_axis(0), lambda: api.home_axes([0, 1]), api.probe):
        with pytest.raises(EcutError):
            call()
    with pytest.raises(ValueError):
        api.home_axes([9])
    assert api._usb.writes == 0


# ----- 4. unverified spindle / coolant / outputs ------------------------

def test_unverified_dro_access_refused_without_writes():
    api = make_api()
    calls = (lambda: api.get_dro(0), lambda: api.set_dro(0, 1.0))
    for call in calls:
        with pytest.raises(EcutError):
            call()
    assert api._usb.writes == 0


def test_read_inputs_still_decodes_confirmed_input_bank():
    api = make_api()
    api.configure_input('PROBE', enabled=True, pin=3, polarity=SignalPolarity.ACTIVE_HIGH)
    frame = EcutRxFrame(0, (0,) * 6, 0, 0, 0b1000, 0, 0, 0, 0, (), 0, True)
    api._usb.poll = lambda: frame
    assert api.read_inputs()['PROBE'] is True


# ----- 5. interpolation index -------------------------------------------

@pytest.mark.parametrize('bad', [10, -1, 4.0, True, None, '3'])
def test_interpolation_index_validated(bad):
    with pytest.raises(EcutConfigError):
        EcutBoardConfig(interpolation_index=bad)
    with pytest.raises(EcutConfigError):
        EcutBoardConfig.from_dict({'interpolation_index': bad})


def test_interpolation_index_direct_assignment_caught_on_use():
    cfg = EcutBoardConfig()
    cfg.interpolation_index = 99
    with pytest.raises(EcutConfigError):
        cfg.interpolation_factor
    assert EcutBoardConfig.from_dict({'interpolation_index': 9}).interpolation_factor == 1024


def test_bad_interpolation_in_json_file_refused(tmp_path):
    path = tmp_path / 'm.json'
    path.write_text('{"interpolation_index": 12}')
    with pytest.raises(EcutConfigError, match='interpolation_index'):
        EcutAPI().load_config(path)


# ----- 6. USB write check -----------------------------------------------

class _Dev:
    def __init__(self, result):
        self.result = result

    def write(self, data):
        return self.result


@pytest.mark.parametrize('result', [-1, 10, 0, None])
def test_usb_send_checks_write_result(result):
    usb = EcutUsb()
    usb._dev = _Dev(result)
    with pytest.raises(EcutUsbError):
        usb.cmd(Cmd.STOP)
    assert issubclass(EcutUsbError, EcutError)


def test_usb_send_accepts_full_write():
    usb = EcutUsb()
    usb._dev = _Dev(65)
    usb.cmd(Cmd.STOP)


# ----- 7. removed unused config fields ----------------------------------

def test_old_json_with_removed_fields_still_loads():
    cfg = EcutBoardConfig.from_dict({'cycle_time_ms': 25.0, 'rapid_feedrate_mm_min': 1.0,
                                     'default_feedrate_mm_min': 2.0, 'interpolation_index': 2})
    assert cfg.interpolation_index == 2
    assert 'cycle_time_ms' not in cfg.to_dict()
    assert 'Cycle time' not in cfg.summary()
