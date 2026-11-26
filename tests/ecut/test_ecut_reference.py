"""Manual machine home (reference) and soft limits with a tolerance; no board is needed."""
from types import SimpleNamespace

import pytest

from appPlugins.Common.ecut import EcutError, State
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


def test_stop_during_motion_removes_the_reference():
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api._running = True
    api.stop()
    assert api.homed_axes == ()


@pytest.mark.parametrize('busy, homed', [(True, ()), (False, (0, 1))])
def test_reset_in_motion_forgets_reference(busy, homed):
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api._last_rx = SimpleNamespace(motion_busy=busy)
    api.push_config()
    assert api.homed_axes == homed


def test_a_scale_change_removes_the_reference():
    engine, api = limited_engine()
    api.reference_axes([0, 1])
    api.config.axes[0].steps_per_mm = 400
    api.push_config()
    assert api.homed_axes == (1,)
