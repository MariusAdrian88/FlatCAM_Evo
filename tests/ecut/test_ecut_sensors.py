"""Firmware-command sequences with synthetic switch status; no hardware opens."""
import struct

import pytest

from appPlugins.Common.ecut import EcutAPI, EcutError, SignalPolarity, AxisDirection, State, decode_rx_report, ecut_crc60
from test_ecut_capabilities import IoBoard
from test_ecut_verified import report


class SensorBoard(IoBoard):
    def __init__(self, contact=True, contact_at=-128):
        super().__init__()
        self.positions = [0] * 6
        self.contact = contact
        self.contact_at = contact_at
        self.active = False
    def cmd(self, opcode, params=b''):
        super().cmd(opcode, params)
        if int(opcode) in (0xae, 0xa6, 0xb4):
            offsets = (4, 12, 20, 38, 46, 54)
            targets = []
            for offset in offsets:
                low, high = struct.unpack_from('>II', params, offset)
                q = (high << 32) | low
                if q >= 1 << 63:
                    q -= 1 << 64
                targets.append(q // 65536)
            axis = 2 if int(opcode) == 0xae else int.from_bytes(params[32:34], 'big')
            desired = targets[axis]
            if self.contact and desired < self.positions[axis]:
                self.positions[axis] = self.contact_at
                self.active = True
            else:
                self.positions[axis] = desired
                self.active = False
    def poll(self):
        data = bytearray(report(tuple(self.positions), outputs=self.outputs))
        struct.pack_into('>H', data, 31, 1 << 5 if self.active else 0)
        struct.pack_into('>I', data, 61, ecut_crc60(data[1:61]))
        return decode_rx_report(data)


def sensor_api(contact=True, unverified=True):
    api = EcutAPI()
    api._usb = SensorBoard(contact)
    api.config.unverified.homing = api.config.unverified.probing = unverified
    api.configure_input('PROBE', pin=5, polarity=SignalPolarity.ACTIVE_HIGH, debounce_ms=0)
    api.configure_input('Z_HOME', pin=5, polarity=SignalPolarity.ACTIVE_HIGH, debounce_ms=0)
    api.configure_input('Z_LIMIT_MINUS', pin=5, polarity=SignalPolarity.ACTIVE_HIGH, debounce_ms=0)
    api.config.probe.max_travel_mm = .005
    api.config.probe.retract_mm = .005
    api.config.probe.fast_velocity_mm_s = .1
    api.config.probe.slow_velocity_mm_s = .05
    api.config.homing[2].enabled = True
    api.config.homing[2].direction = AxisDirection.REVERSE
    api.config.homing[2].hw_limit_when_homing = False
    api.config.homing[2].max_travel_mm = .005
    api.config.homing[2].retract_mm = .005
    # The probe input is separate in production. Disable the limit role for probe tests.
    api.config.inputs['Z_LIMIT_MINUS'].enabled = False
    api.push_config()
    return api


def test_probe_returns_measured_contact_and_retracts_not_planned_endpoint():
    api = sensor_api()
    point = api.probe(2, -.005)
    assert point[2] == -.0025
    assert point[2] != -.005
    assert api.last_probe_result['position_source'] == 'stationary_controller_report'
    assert api.last_probe_result['firmware_latched'] is False
    assert not api.read_inputs()['PROBE']
    assert any(op == 0xae for op, _ in api._usb.commands)
    assert any(op == 0xac for op, _ in api._usb.commands)


def test_probe_initializes_native_bounds_and_stops_before_reading_contact_position():
    api = sensor_api()
    api.probe(2, -.005)
    commands = [op for op, _ in api._usb.commands]
    first_probe = commands.index(0xae)
    assert 0xb5 in commands[:first_probe]
    second_probe = commands.index(0xae, first_probe + 1)
    assert 0xc9 in commands[first_probe + 1:second_probe]


def test_sensor_wait_detects_finite_endpoint_while_native_busy_remains_set(monkeypatch):
    api = sensor_api(False)
    api._running = True
    api._set_state(State.RUNNING)
    frame = decode_rx_report(report(status=0x81))
    monkeypatch.setattr(api, 'poll', lambda: frame)
    with pytest.raises(EcutError, match='No contact'):
        api._wait_switch('PROBE', True, [0] * 6, .01)


def test_probe_no_hit_raises_and_never_creates_a_valid_sample():
    api = sensor_api(contact=False)
    with pytest.raises(EcutError, match='[Nn]o contact'):
        api.probe(2, -.005)
    assert api.last_probe_result is None
    assert api.state == State.ERROR
    assert api._usb.outputs == 0


def test_homing_shared_limit_sensor_stops_backoffs_and_sets_offset():
    api = sensor_api()
    api.config.inputs['PROBE'].enabled = False
    api.config.inputs['Z_LIMIT_MINUS'].enabled = True
    api.config.homing[2].home_offset_mm = 1
    api.push_config()
    api.home_axis(2)
    assert api.position_steps[2] == 51200
    assert api.homed_axes == (2,)
    assert not api.read_inputs()['Z_HOME']
    commands = [op for op, _ in api._usb.commands]
    assert 0xa6 in commands and 0xb4 in commands and 0xc9 in commands


def test_homing_without_switch_hit_does_not_mark_axis_homed():
    api = sensor_api(contact=False)
    with pytest.raises(EcutError, match='[Nn]o contact|not reached'):
        api.home_axis(2)
    assert api.homed_axes == ()
    assert api.state == State.ERROR


@pytest.mark.parametrize('code', ['G38.2', 'G31'])
def test_gcode_probe_uses_feed_and_returns_to_program_after_retract(code):
    api = sensor_api()
    api.run_gcode([f'G21 G91 {code} Z-.005 F6', 'G90 G1 Z0 F6'])
    assert api.last_probe_result['position_mm'][2] == -.0025
    assert api.position_steps[2] == 0
    assert api.state == State.IDLE


def test_gcode_probe_unconfigured_refused_before_prior_move():
    api = sensor_api()
    api.config.inputs['PROBE'].enabled = False
    api.push_config()
    api._usb.commands.clear()
    with pytest.raises(EcutError, match='[Pp]rob'):
        api.run_gcode(['G91 G1 X.001 F6', 'G38.2 Z-.005 F6'])
    assert api._usb.commands == []


def test_gcode_missing_probe_contact_aborts_remaining_program():
    api = sensor_api(False)
    with pytest.raises(EcutError, match='No contact'):
        api.run_gcode(['G91 G38.2 Z-.005 F6', 'G1 X.001 F6'])
    assert api._usb.positions[0] == 0
    assert api.last_probe_result is None
    assert api.state == State.ERROR


def test_rejected_new_probe_invalidates_previous_contact_sample():
    api = sensor_api()
    api.probe(2, -.005)
    api._usb.active = True
    with pytest.raises(EcutError, match='already active'):
        api.probe(2, -.005)
    assert api.last_probe_result is None


def test_gcode_slow_probe_pass_does_not_exceed_program_feed():
    api = sensor_api()
    api.run_gcode(['G91 G38.2 Z-.005 F.6'])
    # First seek, retract, second seek, retract. Seek F.6 = .01 mm/s.
    limits = [p for op, p in api._usb.commands if op == 0x4b]
    maximum = int(.01 * abs(api._axis_scale(2)) * 65536 / 1000)
    assert struct.unpack_from('>I', limits[0], 46)[0] <= maximum
    assert struct.unpack_from('>I', limits[2], 46)[0] <= maximum


def test_probe_preparation_interlock_never_restarts_motion(monkeypatch):
    api = sensor_api()
    api._usb.commands.clear()
    def interrupted_position():
        api.estop()
        return [0] * 6
    monkeypatch.setattr(api, '_measured_counts', interrupted_position)
    with pytest.raises(EcutError):
        api.probe(2, -.005)
    assert api.state == State.ESTOP
    assert not any(op in (0xae, 0xa6, 0xb4) for op, _ in api._usb.commands)


def test_homing_preparation_interlock_never_sends_target(monkeypatch):
    api = sensor_api()
    api._usb.commands.clear()
    def interrupted_position():
        api.estop()
        return [0] * 6
    monkeypatch.setattr(api, '_measured_counts', interrupted_position)
    with pytest.raises(EcutError):
        api.home_axis(2)
    assert api.state == State.ESTOP
    assert not any(op in (0xae, 0xa6, 0xb4) for op, _ in api._usb.commands)


def test_failed_rehome_invalidates_existing_reference():
    api = sensor_api()
    api.home_axis(2)
    assert api.homed_axes == (2,)
    api._usb.contact = False
    with pytest.raises(EcutError):
        api.home_axis(2)
    assert api.homed_axes == ()


def test_pause_stops_native_motion_and_invalidates_sensor_cycle():
    from appPlugins.Common.ecut import Cmd
    api = sensor_api()
    api._running = True
    api._set_state(State.RUNNING)
    api._native_move(Cmd.PROBE, [0, 0, -256, 0, 0, 0], 2, .1, 0)
    api.pause()
    assert api.state == State.ERROR
    assert not api._running
    assert any(op == 0xc8 for op, _ in api._usb.commands[-2:])


def test_reinitialization_invalidates_cached_probe_and_home_results():
    api = sensor_api()
    api.home_axis(2)
    api.probe(2, -.005)
    api.reset()
    # the reset keeps the counts and the axis scale, so the machine home stays valid
    assert api.homed_axes == (2,)
    assert api.last_probe_result is None


def test_probe_grid_collects_samples_and_finishes_at_safe_z():
    api = sensor_api()
    samples = api.probe_grid([(.001, .002), (.002, .001)], .001)
    assert samples == [(.001, .002, -.0025), (.002, .001, -.0025)]
    assert api.position_steps[2] == round(.001 * api._axis_scale(2))
    assert api.state == State.IDLE


def test_probe_grid_without_probe_does_not_move_to_first_point():
    api = sensor_api()
    api.config.inputs['PROBE'].enabled = False
    api.push_config()
    api._usb.commands.clear()
    with pytest.raises(EcutError, match='[Pp]rob'):
        api.probe_grid([(.001, .002)], .001)
    assert api._usb.commands == []


def test_empty_probe_grid_does_not_move():
    api = sensor_api()
    api._usb.commands.clear()
    assert api.probe_grid([], .001) == []
    assert api._usb.commands == []


def test_raw_active_probe_cannot_be_hidden_by_pending_debounce():
    api = sensor_api()
    api.config.inputs['PROBE'].debounce_ms = 20
    api.push_config()
    assert api.read_inputs()['PROBE'] is False
    api._usb.active = True
    api._usb.commands.clear()
    with pytest.raises(EcutError, match='active or unavailable'):
        api.probe(2, -.005)
    assert not any(op == 0xae for op, _ in api._usb.commands)


def test_probe_release_at_endpoint_waits_for_configured_debounce():
    api = sensor_api()
    api.config.inputs['PROBE'].debounce_ms = 20
    api.push_config()
    assert api.probe(2, -.005)[2] == -.0025


@pytest.mark.parametrize('operation', ['home', 'probe'])
@pytest.mark.parametrize('interrupt', ['stop', 'reset'])
def test_final_sensor_completion_cannot_publish_result_after_stop_or_reset(operation, interrupt):
    from appPlugins.Common.ecut import Cmd
    api = sensor_api()
    api._usb.commands.clear()
    triggered = []

    def interrupt_final_status(frame):
        if triggered:
            return
        commands = [int(op) for op, _ in api._usb.commands]
        final_home_stop = operation == 'home' and commands and commands[-1] == int(Cmd.SET_POSITION)
        final_probe_finish = operation == 'probe' and commands.count(0xac) == 2
        if final_home_stop or final_probe_finish:
            triggered.append(len(commands))
            getattr(api, interrupt)()

    api.on_rx_frame(interrupt_final_status)
    with pytest.raises(EcutError, match='Sensor operation stopped'):
        if operation == 'home':
            api.home_axis(2)
        else:
            api.probe(2, -.005)
    assert triggered
    assert api.homed_axes == ()
    assert api.last_probe_result is None
    assert api.state == State.ERROR
    if operation == 'home':
        assert not any(int(op) in (Cmd.PROBE, Cmd.HOME, Cmd.JOG_ON) for op, _ in api._usb.commands[triggered[0]:])


@pytest.mark.parametrize('operation', ['home', 'probe'])
@pytest.mark.parametrize('interrupt', ['stop', 'estop'])
@pytest.mark.parametrize('boundary', ['search', 'retract'])
def test_sensor_search_and_retract_cancellation_never_sends_later_target(operation, interrupt, boundary):
    from appPlugins.Common.ecut import Cmd
    api = sensor_api()
    api._usb.commands.clear()
    triggered = []
    after_interrupt = []

    def interrupt_status(frame):
        if triggered:
            return
        commands = [int(op) for op, _ in api._usb.commands]
        if not commands:
            return
        if operation == 'home':
            reached = commands[-1] == (Cmd.HOME if boundary == 'search' else Cmd.JOG_ON)
        else:
            reached = commands.count(Cmd.PROBE) == (1 if boundary == 'search' else 2)
        if reached:
            triggered.append(True)
            getattr(api, interrupt)()
            after_interrupt.append(len(api._usb.commands))

    api.on_rx_frame(interrupt_status)
    with pytest.raises(EcutError):
        if operation == 'home':
            api.home_axis(2)
        else:
            api.probe(2, -.005)
    assert triggered
    assert api.homed_axes == ()
    assert api.last_probe_result is None
    assert api.state == (State.ESTOP if interrupt == 'estop' else State.ERROR)
    assert not any(int(op) in (Cmd.PROBE, Cmd.HOME, Cmd.JOG_ON, Cmd.SET_POSITION)
                   for op, _ in api._usb.commands[after_interrupt[0]:])


@pytest.mark.parametrize('operation', ['home', 'probe'])
@pytest.mark.parametrize('status', ['missing', 'unchanged'])
def test_sensor_missing_contact_times_out_without_reusing_old_status(monkeypatch, operation, status):
    from types import SimpleNamespace
    from appPlugins.Common.ecut import ecut_controller, Cmd
    api = sensor_api(contact=False)
    previous = api.last_rx_frame
    original_poll = api._usb.poll
    ticks = iter(range(1000))
    monkeypatch.setattr(ecut_controller, 'time', SimpleNamespace(
        monotonic=lambda: next(ticks) / 10, sleep=lambda seconds: None))

    def no_contact_status():
        if any(int(op) in (Cmd.PROBE, Cmd.HOME) for op, _ in api._usb.commands):
            return None if status == 'missing' else previous
        return original_poll()

    monkeypatch.setattr(api._usb, 'poll', no_contact_status)
    with pytest.raises(EcutError, match='timeout waiting for'):
        if operation == 'home':
            api.home_axis(2)
        else:
            api.probe(2, -.005)
    assert api.last_probe_result is None
    assert api.homed_axes == ()
    assert api.state == State.ERROR
    assert api._usb.outputs == 0
    assert sum(int(op) in (Cmd.PROBE, Cmd.HOME, Cmd.JOG_ON) for op, _ in api._usb.commands) == 1


@pytest.mark.parametrize('operation', ['home', 'probe'])
@pytest.mark.parametrize('failure', ['removed_read', 'ambiguous_write'])
def test_sensor_usb_fault_never_replays_target_or_publishes_sample(monkeypatch, operation, failure):
    from appPlugins.Common.ecut import Cmd, EcutUsbError
    api = sensor_api()
    api._usb.commands.clear()
    original_poll = api._usb.poll
    original_cmd = api._usb.cmd

    def failing_poll():
        if any(int(op) in (Cmd.PROBE, Cmd.HOME) for op, _ in api._usb.commands):
            raise EcutUsbError('device removed during sensor search')
        return original_poll()

    def uncertain_cmd(opcode, params=b''):
        original_cmd(opcode, params)
        if int(opcode) in (Cmd.PROBE, Cmd.HOME):
            raise EcutUsbError('ambiguous target write')

    if failure == 'removed_read':
        monkeypatch.setattr(api._usb, 'poll', failing_poll)
    else:
        monkeypatch.setattr(api._usb, 'cmd', uncertain_cmd)
    with pytest.raises(EcutUsbError):
        if operation == 'home':
            api.home_axis(2)
        else:
            api.probe(2, -.005)
    assert api.state == State.ERROR
    assert api.last_probe_result is None
    assert api.homed_axes == ()
    assert api._usb.outputs == 0
    assert sum(int(op) in (Cmd.PROBE, Cmd.HOME, Cmd.JOG_ON) for op, _ in api._usb.commands) == 1


def test_shared_home_limit_stays_an_interlock_when_homing_exemption_is_disabled():
    from appPlugins.Common.ecut import Cmd
    api = sensor_api()
    api.config.inputs['PROBE'].enabled = False
    api.config.inputs['Z_LIMIT_MINUS'].enabled = True
    api.config.homing[2].hw_limit_when_homing = True
    api.push_config()
    api._usb.commands.clear()
    with pytest.raises(EcutError):
        api.home_axis(2)
    assert api.state == State.ESTOP
    assert api.homed_axes == ()
    assert 'Z_LIMIT_MINUS' in api.fault_reason
    assert not any(int(op) in (Cmd.JOG_ON, Cmd.SET_POSITION) for op, _ in api._usb.commands)


def home_switch_api(contact=True, unverified=True):
    api = sensor_api(contact, unverified)
    api.config.inputs['PROBE'].enabled = False
    for axis in (0, 1):
        api.configure_input('XY'[axis] + '_HOME', pin=5, polarity=SignalPolarity.ACTIVE_HIGH, debounce_ms=0)
        api.config.axes[axis].enabled = True
        home = api.config.homing[axis]
        home.enabled = True
        home.direction = AxisDirection.REVERSE
        home.max_travel_mm = home.retract_mm = .005
    api.push_config()
    return api


def test_home_command_runs_sensor_cycle(monkeypatch):
    import time
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine, GcodeError
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = home_switch_api()
    engine = EcutGcodeEngine(api)
    assert engine.execute_line('$H') == ['ok']
    assert api.state == State.IDLE
    assert api.homed_axes == (0, 1, 2)
    # a single axis
    api._homed_axes.clear()
    assert engine.execute_line('$HZ') == ['ok']
    assert api.homed_axes == (2,)
    # the switch is off: the refusal stays and nothing moves
    off = home_switch_api(unverified=False)
    off.config.unverified.probing = True
    with pytest.raises(GcodeError, match='[Hh]oming refused'):
        EcutGcodeEngine(off).execute_line('$H')
    assert off.homed_axes == ()
    # a switch that never closes: one ALARM:9 line, no axis homed
    bad = home_switch_api(contact=False)
    monkeypatch.setattr(bad, 'connect', lambda: True)
    bad._usb.close = lambda: None
    port = EcutSenderPort(api=bad, engine=EcutGcodeEngine(bad), timeout=0.03)
    try:
        port.write(b'$H\n')
        lines = []
        deadline = time.monotonic() + 20
        while not any(x.startswith(b'ALARM') for x in lines) and time.monotonic() < deadline:
            lines.extend(port.readlines())
            time.sleep(0.01)
        time.sleep(0.2)
        lines.extend(port.readlines())
    finally:
        port.close()
    assert [x for x in lines if x.startswith(b'ALARM')] == [b'ALARM:9\r\n']
    assert bad.homed_axes == ()


def test_g38_reports_contact(monkeypatch):
    import time
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine, GcodeError
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = sensor_api()
    api._usb.contact_at = -61440          # Z -1.200 mm at 51200 counts/mm
    api.config.probe.max_travel_mm = 5
    api.push_config()
    engine = EcutGcodeEngine(api)
    assert engine.execute_line('$#')[-2] == '[PRB:0.000,0.000,0.000:0]'
    assert engine.execute_line('G91 G38.2 Z-2 F6') == ['[PRB:0.000,0.000,-1.200:1]', 'ok']
    assert '[PRB:0.000,0.000,-1.200:1]' in engine.execute_line('$#')
    assert api.state == State.IDLE
    # G31 answers the same way, probing from the start again (the retract stays on the free path)
    engine.execute_line('G90 G1 Z0 F600')
    api._usb.contact_at = -30720
    assert engine.execute_line('G31 Z-1 F6') == ['[PRB:0.000,0.000,-0.600:1]', 'ok']
    # the switch is off: the refusal stays and nothing moves
    off = sensor_api()
    off.config.unverified.probing = False
    with pytest.raises(GcodeError, match='refused'):
        EcutGcodeEngine(off).execute_line('G38.2 Z-.004 F6')
    assert off._usb.positions[2] == 0
    # no contact: one ALARM:5 line, no ok
    bad = sensor_api(contact=False)
    monkeypatch.setattr(bad, 'connect', lambda: True)
    bad._usb.close = lambda: None
    port = EcutSenderPort(api=bad, engine=EcutGcodeEngine(bad), timeout=0.03)
    try:
        port.write(b'G91 G38.2 Z-.004 F6\n')
        lines = []
        deadline = time.monotonic() + 20
        while not any(x.startswith(b'ALARM') for x in lines) and time.monotonic() < deadline:
            lines.extend(port.readlines())
            time.sleep(0.01)
        time.sleep(0.2)
        lines.extend(port.readlines())
    finally:
        port.close()
    assert [x for x in lines if x.startswith(b'ALARM')] == [b'ALARM:5\r\n']
    assert b'ok\r\n' not in lines


@pytest.mark.parametrize('reinitialize', [False, True])
def test_probe_record_is_cleared_when_machine_coordinates_are_reestablished(reinitialize):
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine

    api = sensor_api()
    api.config.probe.max_travel_mm = 5
    api.push_config()
    engine = EcutGcodeEngine(api)
    assert engine.execute_line('G91 G38.2 Z-.004 F6')[0].endswith(':1]')
    if reinitialize:
        api.push_config()
    else:
        engine.reference_axes([2])
    assert '[PRB:0.000,0.000,0.000:0]' in engine.execute_line('$#')


def test_probe_record_is_cleared_by_a_home_cycle():
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine

    api = home_switch_api()
    engine = EcutGcodeEngine(api)
    # the record of an earlier contact, in the machine frame before the home cycle
    api._last_probe_result = {'position_mm': (1.0, 2.0, -3.0, 0.0, 0.0, 0.0)}
    assert '[PRB:1.000,2.000,-3.000:1]' in engine.execute_line('$#')
    assert engine.execute_line('$HZ') == ['ok']
    assert '[PRB:0.000,0.000,0.000:0]' in engine.execute_line('$#')


def test_probe_record_after_a_failed_probe_and_after_a_reset_reports_no_hit():
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine
    api = sensor_api()
    api.config.probe.max_travel_mm = 5
    api.push_config()
    engine = EcutGcodeEngine(api)
    assert engine.execute_line('G91 G38.2 Z-.004 F6')[0].endswith(':1]')
    api._usb.contact = False
    with pytest.raises(EcutError):
        engine.execute_line('G91 G38.2 Z-.004 F6')
    assert [x for x in engine.execute_line('$#') if x.startswith('[PRB')][0].endswith(':0]')
    api = sensor_api()
    api.config.probe.max_travel_mm = 5
    api.push_config()
    engine = EcutGcodeEngine(api)
    engine.execute_line('G91 G38.2 Z-.004 F6')
    engine.realtime(0x18)
    assert '[PRB:0.000,0.000,0.000:0]' in engine.execute_line('$#')


class ContactBoard(SensorBoard):
    """One probed axis: the switch closes at contact_at and opens again only at release_at."""
    def __init__(self, axis, sign, contact_at, release_at=None):
        super().__init__()
        self.axis, self.sign, self.contact_at = axis, sign, contact_at
        self.release_at = contact_at if release_at is None else release_at
        self.reached = []
    def cmd(self, opcode, params=b''):
        IoBoard.cmd(self, opcode, params)
        if int(opcode) in (0xae, 0xa6, 0xb4):
            low, high = struct.unpack_from('>II', params, (4, 12, 20)[self.axis])
            desired = (((high << 32) | low) - (1 << 64 if high >> 31 else 0)) // 65536
            s, pos = self.sign, self.positions[self.axis]
            new = self.contact_at if s * pos < s * self.contact_at <= s * desired else desired
            self.positions[self.axis] = new
            self.reached.append(new)
            self.active = s * new >= s * self.release_at


def contact_api(axis, sign, contact_mm, release_mm=None, start_mm=0.0, backlash_mm=0.0):
    api = sensor_api()
    scale = api._axis_scale(axis)
    api.config.probe.max_travel_mm = .05
    api.config.probe.retract_mm = .005
    ax = api.config.axes[axis]
    if backlash_mm:
        ax.backlash_mm, ax.backlash_compensation_enabled = backlash_mm, True
        api.config.unverified.backlash = True
    api.push_config()
    api._usb = ContactBoard(axis, sign, round(contact_mm * scale),
                            None if release_mm is None else round(release_mm * scale))
    api._usb.positions[axis] = round(start_mm * scale)
    return api, scale


@pytest.mark.parametrize('axis, sign, contact_mm', [(2, -1, -.0025), (0, 1, .0025)])
def test_probe_retract_never_passes_the_probe_start(axis, sign, contact_mm):
    api, scale = contact_api(axis, sign, contact_mm)
    api.probe(axis, sign * .005)
    assert api.last_probe_result['position_mm'][axis] == pytest.approx(contact_mm)
    assert all(sign * counts >= 0 for counts in api._usb.reached)   # nothing behind the start (0)
    assert sign * api._usb.positions[axis] >= 0
    assert not api.read_inputs()['PROBE']


def test_probe_retract_with_backlash_ends_at_contact_plus_retract():
    api, scale = contact_api(2, -1, -.0025, start_mm=.02, backlash_mm=.010)
    api.probe(2, -.03)
    assert api.logical_position_mm()[2] == pytest.approx(-.0025 + .005, abs=1 / scale)


def probe_targets(api, axis):
    """Logical target (mm) of each native probe move, with the offset that is set when it is sent."""
    logical = []
    original = api._native_move

    def spy(opcode, targets, move_axis, speed, flags):
        logical.append((targets[move_axis] - api._backlash_offset[move_axis]) / api._axis_scale(axis))
        original(opcode, targets, move_axis, speed, flags)
    api._native_move = spy
    return logical


def test_probe_with_a_reversal_targets_the_requested_logical_position():
    api, scale = contact_api(2, -1, -.5, start_mm=.02, backlash_mm=.010)   # contact out of reach
    api._backlash_dir[2] = 1                                               # the last move went up
    logical = probe_targets(api, 2)
    with pytest.raises(EcutError, match='[Nn]o contact'):
        api.probe(2, -.03)
    assert logical[0] == pytest.approx(-.01, abs=2 / scale)


def test_probe_to_the_soft_limit_with_backlash_is_accepted():
    api, scale = contact_api(2, -1, -.5, start_mm=.02, backlash_mm=.010)
    ax = api.config.axes[2]
    ax.soft_limits_enabled, ax.soft_limit_negative_mm, ax.soft_limit_positive_mm = True, 0, 1
    api.push_config()
    api._backlash_dir[2] = 1
    logical = probe_targets(api, 2)
    with pytest.raises(EcutError, match='[Nn]o contact'):
        api.probe(2, -.02)
    assert logical[0] == pytest.approx(0.0, abs=2 / scale)


def test_probe_that_stays_active_at_the_start_bound_fails_with_alarm_5():
    api, scale = contact_api(2, -1, -.0025, release_mm=.00125)
    with pytest.raises(EcutError):
        api.probe(2, -.005)
    assert api._fault_alarm == 5
    assert api.last_probe_result is None
    assert max(api._usb.reached) <= 0   # no move behind the start
