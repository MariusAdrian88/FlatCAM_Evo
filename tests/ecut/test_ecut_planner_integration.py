"""Lookahead integration against the existing simulated board, without hardware."""

import pytest

from appPlugins.Common.ecut import Cmd, EcutError, State
from appPlugins.Common.ecut.ecut_controller import _MoveAction
from appPlugins.Common.ecut.ecut_planner import LinearMove, plan_linear_path, iter_path_segments
from test_ecut_controller import make_api, sent_deltas
from test_ecut_spindle import pwm_api


def end_stream_count(api):
    commands = getattr(api._usb, 'cmds', getattr(api._usb, 'commands', []))
    return sum(opcode == Cmd.END_STREAM for opcode, _params in commands)


def test_program_groups_adjacent_moves_into_one_planned_stream():
    api = make_api()
    api.run_gcode(['G1 X1 F600', 'X2', 'X3'])
    assert end_stream_count(api) == 1
    expected = iter_path_segments(plan_linear_path(api.config, (0,) * 6, [
        LinearMove((x, None, None, None, None, None), False, 600) for x in (1, 2, 3)]))
    assert len(sent_deltas(api)) == len(expected)
    assert sent_deltas(api) == list(expected)
    assert api.position_steps == [4800, 0, 0, 0, 0, 0]
    assert api.state == State.IDLE


def test_relative_targets_follow_rounded_actual_counts():
    api = make_api()
    api.run_gcode(['G91 G1 X0.0004 F60'] + ['X0.0004'] * 49 + ['G90 X0.1', 'G91 X-0.01'])
    assert api.position_steps == [144, 0, 0, 0, 0, 0]
    assert end_stream_count(api) == 1


@pytest.mark.parametrize('barrier', ['G4 P0', 'M5', 'M3 S6000', 'M0', 'T1 M6'])
def test_control_actions_are_stopped_path_boundaries(barrier):
    api = pwm_api()

    def continue_program(state):
        if state == State.PAUSED:
            if api.pending_tool_change is not None:
                api.confirm_tool_change(api.pending_tool_change)
            else:
                api.resume()

    api.on_state_change(continue_program)
    api.run_gcode(['G1 X0.001 F60', barrier, 'G1 X0.002'])
    assert end_stream_count(api) == 2
    assert api.position_mm[0] == pytest.approx(0.002, abs=0.00002)


def test_rotary_axis_words_isolate_their_path():
    api = make_api()
    api.config.axes[3].enabled = True
    api.run_gcode(['G1 X0.001 F60', 'A0.001', 'X0.002'])
    assert end_stream_count(api) == 3
    assert api.position_steps[:4] == [3, 0, 0, 2]


def test_later_soft_limit_failure_precedes_spindle_and_any_motion():
    api = pwm_api()
    api.config.axes[0].soft_limits_enabled = True
    api.config.axes[0].soft_limit_positive_mm = 2
    api.push_config()
    api._usb.events.clear()
    with pytest.raises(EcutError):
        api.run_gcode(['M3 S6000', 'G1 X1 F600', 'G4 P0', 'X3'])
    assert api._usb.events == []
    assert api.position_steps == [0] * 6
    assert api.state == State.IDLE


def test_later_position_overflow_precedes_any_motion():
    api = make_api()
    with pytest.raises(EcutError):
        api.run_gcode(['G1 X0.001 F600', 'X999999999999'])
    assert api._usb.writes == 0
    assert api.state == State.IDLE


def test_later_disabled_axis_precedes_any_motion():
    api = make_api()
    api.config.axes[1].enabled = False
    with pytest.raises(EcutError, match='disabled'):
        api.run_gcode(['G1 X0.001 F600', 'Y0'])
    assert api._usb.writes == 0


def test_move_linear_validates_zero_distance_feed_before_submission():
    api = make_api()
    with pytest.raises(EcutError):
        api._move_linear(_MoveAction(False, True, {0: 0}, None))
    assert api._usb.writes == 0


def test_stop_during_group_aborts_remaining_records():
    api = make_api()
    api._usb.on_motion = lambda _packet: api.stop()
    api.run_gcode(['G1 X10 F600', 'X20', 'X30'])
    assert 0 <= api.position_steps[0] < 48000
    assert len(api._usb.motion_packets) == 1
    assert api.state == State.IDLE


def test_program_feed_override_applies_to_preplanned_path():
    normal, slow = make_api(), make_api()
    slow._feed_override = 0.5
    normal.run_gcode(['G1 X1 F600', 'X2'])
    slow.run_gcode(['G1 X1 F600', 'X2'])
    assert slow.position_steps == normal.position_steps
    assert len(sent_deltas(slow)) > len(sent_deltas(normal))

def test_synthetic_probe_defers_following_motion_until_measured_retract():
    from appPlugins.Common.ecut.ecut_controller import compile_program, _ProbeAction, _PlannedPathAction
    from test_ecut_sensors import sensor_api

    api = sensor_api()
    program = ['G91 G1 X.001 F6', 'G38.2 Z-.005 F6', 'G1 Z.001 F6']
    prepared = api._plan_program_actions(compile_program(program))
    assert isinstance(prepared[0][1], _PlannedPathAction)
    assert isinstance(prepared[1][1], _ProbeAction)
    assert type(prepared[2][1]) is _MoveAction
    api.run_gcode(program)
    assert api.last_probe_result['position_mm'][2] == -.0025
    # SensorBoard reports -128 at contact; the retract (256 counts) stops at the probe start, 0, the only
    # path known to be free. The following relative move rounds +.001 mm from there to +51 counts.
    assert api.position_steps[2] == 51
    assert api.state == State.IDLE
