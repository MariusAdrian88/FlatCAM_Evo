"""Program controls and real FlatCAM files, using the simulated USB board."""
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from appPlugins.Common.ecut import EcutBoardConfig, EcutError, State, SignalPolarity
from appPlugins.Common.ecut import ecut_controller
from appPlugins.Common.ecut.ecut_controller import compile_program
from test_ecut_feed_hold import Clock, StreamBoard
from test_ecut_spindle import pwm_api, pwm_values


def test_m0_restore_policy_roundtrips_and_defaults_on():
    config = EcutBoardConfig()
    assert getattr(config.safety, 'restore_outputs_after_m0', None) is True
    config.safety.restore_outputs_after_m0 = False
    restored = EcutBoardConfig.from_dict(config.to_dict())
    assert restored.safety.restore_outputs_after_m0 is False


def test_g94_retains_feed_per_minute_in_inches():
    api = pwm_api()
    api.run_gcode(['G94 G20 G91', 'G1 X0.001 F1', 'G21'])
    assert api.position_mm[0] == pytest.approx(0.0254, abs=0.00002)
    assert api._feed_mm_min == pytest.approx(25.4)


def test_g4_seconds_waits_without_axis_motion_or_scaling_in_inches():
    api = pwm_api()
    start = time.monotonic()
    api.run_gcode(['G20', 'G4 P0.025'])
    assert time.monotonic() - start >= .025
    assert not any(event[0] == 'motion' for event in api._usb.events)


@pytest.mark.parametrize('line', ['G4', 'G4 P-1', 'P1', 'G4 P1 P2', 'G4 X1 P1',
                                 'G4 G1 P1', 'G93', 'G95', 'T-1', 'T1.5',
                                 'T1 T2', 'M6', 'M0 M6', 'T1 M6 G0 X1'])
def test_invalid_program_controls_refuse_before_earlier_writes(line):
    api = pwm_api()
    with pytest.raises(EcutError):
        api.run_gcode(['G0 X1', line])
    assert api._usb.events == []


def test_t_selects_without_prompt_or_motion():
    api = pwm_api()
    api.run_gcode(['T2'])
    assert api.selected_tool == 2
    assert api.current_tool is None
    assert api._usb.events == []


def test_m6_requires_matching_confirmation_and_does_not_restart_spindle():
    api = pwm_api()
    pauses = []

    def operator(state):
        if state != State.PAUSED:
            return
        pauses.append((api.pause_reason, api.pending_tool_change, api._usb.outputs,
                       api.position_steps, pwm_values(api)[-1]))
        with pytest.raises(EcutError):
            api.resume()
        with pytest.raises(EcutError):
            api.confirm_tool_change(3)
        assert api.state == State.PAUSED and api.current_tool is None
        api.confirm_tool_change(2)

    api.on_state_change(operator)
    api.run_gcode(['M3 S6000', 'T2 M6', 'G0 X0.001'])
    assert pauses == [('M6', 2, 0, [0]*6, 0)]
    assert api.current_tool == api.selected_tool == 2
    assert api._usb.outputs == 0
    assert api.pause_reason is None and api.pending_tool_change is None


@pytest.mark.parametrize('restore', [True, False])
def test_m0_clears_outputs_and_restore_is_configurable(restore):
    api = pwm_api()
    api.config.safety.restore_outputs_after_m0 = restore
    api.configure_output('FLOOD', pin=2)
    api.push_config()
    api.flood_on()
    observations = []

    def operator(state):
        if state == State.PAUSED:
            observations.append((api.pause_reason, api._usb.outputs, pwm_values(api)[-1]))
            api.resume()

    api.on_state_change(operator)
    api.run_gcode(['M3 S6000', 'M0'])
    assert observations == [('M0', 0, 0)]
    assert api._usb.outputs == (5 if restore else 0)
    assert pwm_values(api)[-1] == (250 if restore else 0)


@pytest.mark.parametrize('operation', ['stop', 'estop', 'reset'])
def test_manual_wait_cancel_does_not_submit_following_motion(operation):
    api = pwm_api()

    def operator(state):
        if state == State.PAUSED:
            getattr(api, operation)()

    api.on_state_change(operator)
    api.run_gcode(['T1 M6', 'G0 X1'])
    assert not any(event[0] == 'motion' for event in api._usb.events)
    assert api.pending_tool_change is None
    assert api._usb.outputs == 0


def test_resume_refuses_a_still_asserted_feed_hold():
    api = pwm_api()
    api._state = State.PAUSED
    api._input_values['FEED_HOLD'] = True
    with pytest.raises(EcutError):
        api.resume()
    assert api.state == State.PAUSED


def test_g4_pause_does_not_consume_dwell_time():
    api = pwm_api()
    original = api._usb.poll
    paused = False

    def pause_during_status():
        nonlocal paused
        frame = original()
        if not paused:
            paused = True
            api.pause()
            time.sleep(.04)
            api.resume()
        return frame

    api._usb.poll = pause_during_status
    start = time.monotonic()
    api.run_gcode(['G4 P0.025'])
    assert time.monotonic() - start >= .065


@pytest.mark.parametrize('operation', ['stop', 'estop'])
def test_g4_stop_aborts_before_following_motion(operation):
    api = pwm_api()
    original = api._usb.poll
    fired = False

    def stop_during_status():
        nonlocal fired
        frame = original()
        if not fired:
            fired = True
            getattr(api, operation)()
        return frame

    api._usb.poll = stop_during_status
    try:
        api.run_gcode(['G4 P1', 'G0 X1'])
    except EcutError:
        assert operation == 'estop'
    assert not any(event[0] == 'motion' for event in api._usb.events)


def test_manual_pause_status_loss_clears_pending_state():
    api = pwm_api()

    def lose_status(state):
        if state == State.PAUSED:
            api._wait_for_rx = lambda timeout: None

    api.on_state_change(lose_status)
    with pytest.raises(EcutError):
        api.run_gcode(['T1 M6', 'G0 X1'])
    assert api.state == State.ERROR
    assert api.pending_tool_change is None and api._usb.outputs == 0


def test_m0_continue_cannot_restart_after_estop():
    api = pwm_api()

    def cancel(state):
        if state == State.PAUSED:
            api.estop()
            api.resume()

    api.on_state_change(cancel)
    api.run_gcode(['M3 S6000', 'M0', 'G0 X1'])
    assert api.state == State.ESTOP
    assert api._usb.outputs == 0 and pwm_values(api)[-1] == 0
    assert not any(event[0] == 'motion' for event in api._usb.events)


def test_stop_from_continue_state_callback_cannot_restore_outputs():
    api = pwm_api()
    paused = False

    def cancel_resume(state):
        nonlocal paused
        if state == State.PAUSED:
            paused = True
            api.resume()
        elif state == State.RUNNING and paused:
            api.stop()

    api.on_state_change(cancel_resume)
    api.run_gcode(['M3 S6000', 'M0', 'G0 X1'])
    assert api._usb.outputs == 0 and pwm_values(api)[-1] == 0
    assert not any(event[0] == 'motion' for event in api._usb.events)


def test_stop_during_continue_status_cannot_restore_spindle():
    api = pwm_api()
    paused = False
    cancelled = False
    original = api._usb.poll

    def cancel_status():
        nonlocal cancelled
        frame = original()
        if paused and not cancelled and api.state == State.RUNNING:
            cancelled = True
            api.stop()
        return frame

    def resume(state):
        nonlocal paused
        if state == State.PAUSED:
            paused = True
            api.resume()

    api._usb.poll = cancel_status
    api.on_state_change(resume)
    try:
        api.run_gcode(['M3 S6000', 'M0', 'G0 X1'])
    except EcutError:
        pass
    assert api._usb.outputs == 0 and pwm_values(api)[-1] == 0


@pytest.mark.parametrize('command', ['M0', 'T1 M6'])
def test_manual_pause_clears_physical_coolant_despite_generic_alias(command):
    api = pwm_api()
    api.configure_output('FLOOD', pin=2)
    api.configure_output('OUTPUT_0', pin=2)
    api.push_config()
    api.set_output('OUTPUT_0', True)

    def confirm(state):
        if state == State.PAUSED:
            assert api._usb.outputs == 0
            if api.pending_tool_change is not None:
                api.confirm_tool_change(api.pending_tool_change)
            else:
                api.resume()

    api.on_state_change(confirm)
    api.run_gcode([command])
    assert api._usb.outputs == (4 if command == 'M0' else 0)


def test_last_move_with_slow_fifo_ends_idle(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ecut_controller, 'time', clock.module)
    api = ecut_controller.EcutAPI()

    class SlowBoard(StreamBoard):
        def cmd(self, opcode, params=b''):
            super().cmd(opcode, params)
            if self.stream_end is not None:  # with the queue run after it: over the plain 2 s drain wait
                self.busy_until = self.stream_end + 1.8

    board = api._usb = SlowBoard(clock)
    board.is_open = True
    api.push_config()
    states = []
    api.on_state_change(states.append)
    api.run_gcode(['G1 X50 F3000', 'M30'])
    assert api.state == State.IDLE
    assert State.ERROR not in states


def test_unmodified_supplied_file_executes_with_explicit_test_operator(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'docs/mach3/gcode' / 'cutout.txt'
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    assert compile_program(lines)
    api = pwm_api()
    # The config is static here; re-encoding it for every motion packet is what made this slow.
    api.encode_config = lambda encoded=api.encode_config(): encoded
    clock = [1000.0]

    def advance(seconds):
        clock[0] += seconds
        return clock[0]

    # Fake clock inside the controller only: the file's 1 s dwell and the pacing sleeps cost no real time.
    monkeypatch.setattr(ecut_controller, 'time', SimpleNamespace(monotonic=lambda: advance(1e-4), sleep=advance))
    pauses = []
    api.on_state_change(lambda state: pauses.append(state) if state == State.PAUSED else None)
    api.run_gcode(lines)
    assert not pauses
    assert api.position_mm[:3] == pytest.approx([0, 0, 2])
    assert api._usb.outputs == 0 and pwm_values(api)[-1] == 0
