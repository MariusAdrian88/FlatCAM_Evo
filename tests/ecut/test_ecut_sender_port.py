"""Serial contract and worker ownership, without a physical E-Cut board."""

import importlib
import threading
import time

import pytest
import serial

from appPlugins.Common.ecut import EcutError, State
from appPlugins.Common.ecut.ecut_protocol import EcutUsbError


class FakeGcodeError(EcutError):
    code = 3


class FakeAPI:
    def __init__(self):
        self.connected = False
        self.state = State.IDLE
        self.calls = []
        self.callback = None
        self.fail_poll = False
        self.stop_pending = False
        self.stop_completed = False
        self.fault_alarm = 1
        self.fault_reason = None

    def is_stationary(self, frame):
        return False

    def record(self, name):
        self.calls.append((name, threading.get_ident()))

    def connect(self):
        self.record('connect')
        self.connected = True
        return True

    def push_config(self):
        self.record('config')

    def on_rx_frame(self, callback):
        self.callback = callback

    def poll(self):
        self.record('poll')
        if self.fail_poll:
            raise EcutError('USB disconnected')
        if self.callback:
            self.callback(None)

    def stop(self):
        self.record('stop')
        self.state = State.IDLE

    def disconnect(self):
        self.record('disconnect')
        self.connected = False


class FakeEngine:
    def __init__(self, api):
        self.api = api
        self.lines = []
        self.controls = []
        self.pending_tool_change = None
        self.pause_reason = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.check_mode = False
        self.programs = []

    def run_startup(self):
        return []

    def prepare_program(self, lines):
        self.api.record('prepare')
        if 'SLOW' in lines:
            # a planner that never ends by itself; it only leaves through the checkpoint
            while not self.release.wait(0.002):
                self.api._planning_checkpoint()
        return tuple(lines)

    def execute_program(self, job):
        self.programs.append(job)
        for line in job:
            self.execute_line(line)

    def execute_line(self, line):
        self.api.record('execute')
        self.lines.append(line)
        if line == 'WAIT':
            self.api.state = State.RUNNING
            self.entered.set()
            while not self.release.is_set():
                self.api.poll()
                time.sleep(0.002)
            self.api.state = State.IDLE
        if line == 'T7M6':
            self.pending_tool_change = 7
            self.pause_reason = 'M6'
            while self.pending_tool_change is not None:
                self.api.poll()
                time.sleep(0.002)
            self.pause_reason = None
        if line == 'FAIL':
            raise EcutUsbError('lost board')
        if line == 'LIMIT':
            self.api.state = State.ESTOP
            self.api.fault_alarm = 10
            self.api.fault_reason = 'Emergency input'
            raise EcutError('stopped')
        if line == 'REFUSE':
            raise EcutError('refused by the controller')
        if line == 'M3 S2000':
            raise FakeGcodeError('Spindle run output SPINDLE_CW is not configured')
        return ['ok']

    def status_report(self, state=None):
        return '<%s|MPos:1.000,2.000,3.000|WCO:0.000,0.000,0.000>' % (state or 'Idle')

    def realtime(self, code):
        self.api.record('realtime')
        self.controls.append(code)
        if code == ord('!'):
            self.api.state = State.PAUSED
        elif code == ord('~'):
            if self.pending_tool_change is not None:
                raise FakeGcodeError('Tool confirmation required')
        elif code == 0xa0:
            raise EcutError('USB failed during control')
            self.api.state = State.RUNNING
        elif code == 0x85:
            self.release.set()
            self.pending_tool_change = None
            self.api.stop()
        return []

    def reset(self):
        self.api.record('reset')
        self.release.set()
        self.pending_tool_change = None
        self.pause_reason = None
        return []

    def confirm_tool_change(self, tool):
        self.api.record('confirm')
        if tool != self.pending_tool_change:
            raise FakeGcodeError('Wrong tool')
        self.pending_tool_change = None




@pytest.fixture
def connection():
    module = importlib.import_module('appPlugins.Common.ecut.ecut_sender_port')
    api = FakeAPI()
    engine = FakeEngine(api)
    port = module.EcutSenderPort(api=api, engine=engine, timeout=0.03)
    assert b'E-Cut host adapter' in port.readline()
    yield port, api, engine
    engine.release.set()
    port.close()


def wait_for(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.002)


def test_partial_crlf_and_realtime_are_independent(connection):
    port, api, engine = connection
    assert port.write(b'G1 X?1\r') == 7
    assert port.write(b'\nG21\nG9') == 7
    wait_for(lambda: len(engine.lines) == 2)
    assert engine.lines == ['G1 X1', 'G21']
    port.write(b'0\n')
    wait_for(lambda: len(engine.lines) == 3)
    assert engine.lines[-1] == 'G90'
    assert b'MPos:1.000' in b''.join(port.readlines())
    assert {ident for _, ident in api.calls} == {port._worker.ident}


def test_ack_waits_for_execution_and_status_never_idle_with_work(connection):
    port, api, engine = connection
    port.write(b'WAIT\nG21\n')
    assert engine.entered.wait(1)
    assert port.readline() == b''
    port.write(b'?')
    assert port.readline().startswith(b'<Run|MPos:')
    engine.release.set()
    assert port.readline() == b'ok\r\n'
    assert port.readline() == b'ok\r\n'


def test_read_waits_to_deadline_and_buffer_can_be_cleared(connection):
    port, api, engine = connection
    started = time.monotonic()
    assert port.read(1) == b''
    assert time.monotonic() - started >= 0.02
    port.write(b'G21\n')
    wait_for(lambda: port.in_waiting == 4)
    assert port.read(2) == b'ok'
    port.reset_input_buffer()
    assert port.in_waiting == 0
    port.dtr = False
    port.dtr = True
    assert not any(name == 'reset' for name, _ in api.calls)


def test_read_timeout_none_wakes_when_closed(connection):
    port, api, engine = connection
    port.timeout = None
    result = []
    reader = threading.Thread(target=lambda: result.append(port.read(1)))
    reader.start()
    port.close()
    reader.join(1)
    assert result == [b'']


def test_close_cancels_worker_and_queued_commands(connection):
    port, api, engine = connection
    port.write(b'WAIT\nG1 X99\n')
    assert engine.entered.wait(1)
    port.close()
    assert not port.is_open
    assert not port._worker.is_alive()
    assert engine.lines == ['WAIT']
    assert [name for name, _ in api.calls][-1] == 'disconnect'
    with pytest.raises(serial.SerialException):
        port.write(b'G21\n')


def test_reset_cancels_old_epoch_and_banner_follows_reset(connection):
    port, api, engine = connection
    port.write(b'WAIT\nG1 X99\n')
    assert engine.entered.wait(1)
    port.write(b'\x18')
    assert b'E-Cut host adapter' in port.readline()
    wait_for(lambda: any(name == 'reset' for name, _ in api.calls))
    assert engine.lines == ['WAIT']
    assert port.readline() == b''
    port.write(b'G21\n')
    assert port.readline() == b'ok\r\n'


def test_tool_change_uses_confirmation_queue_and_announces_once(connection):
    port, api, engine = connection
    requested = []
    port.tool_change_callback = requested.append
    port.write(b'T7M6\n')
    assert port.readline() == b'[ECUT:TOOL:7]\r\n'
    wait_for(lambda: port.pending_tool_change == 7)
    assert port.pause_reason == 'M6'
    port.write(b'~')
    assert port.readline().startswith(b'[MSG:E-Cut real-time command refused')
    assert port.pending_tool_change == 7
    port.confirm_tool_change(7)
    assert port.readline() == b'ok\r\n'
    assert requested == [7]
    assert {ident for _, ident in api.calls} == {port._worker.ident}


def test_failed_command_discards_host_queue_and_surfaces_alarm(connection):
    port, api, engine = connection
    port.write(b'FAIL\nG1 X99\n')
    assert port.readline().startswith(b'ALARM:')
    wait_for(lambda: not port.is_open)
    assert engine.lines == ['FAIL']


def test_idle_poll_error_is_visible_to_reader(connection):
    port, api, engine = connection
    api.fail_poll = True
    assert port.readline().startswith(b'ALARM:')
    wait_for(lambda: not port.is_open)


def test_coded_engine_error_tells_the_reason(connection):
    port, api, engine = connection
    port.write(b'M3 S2000\n')
    assert port.readline() == b'[MSG:E-Cut: Spindle run output SPINDLE_CW is not configured]\r\n'
    assert port.readline() == b'error:3\r\n'
    assert port.is_open


def test_oversize_write_is_atomic(connection):
    port, api, engine = connection
    with pytest.raises(serial.SerialException):
        port.write(b'G' * 70000)
    port.write(b'G21\n')
    assert port.readline() == b'ok\r\n'
    assert engine.lines == ['G21']


def test_startup_failure_is_raised_and_disconnected():
    module = importlib.import_module('appPlugins.Common.ecut.ecut_sender_port')
    api = FakeAPI()
    def fail():
        raise EcutError('Bad initialization readback')
    api.push_config = fail
    with pytest.raises(serial.SerialException, match='Bad initialization readback'):
        module.EcutSenderPort(api=api, engine=FakeEngine(api))
    assert [name for name, _ in api.calls][-1] == 'disconnect'


def test_crlf_never_inserts_banner_after_a_completed_command(connection):
    port, api, engine = connection
    port.write(b'G21\r')
    assert port.readline() == b'ok\r\n'
    port.write(b'\n')
    assert port.readline() == b''


def test_wake_after_initial_banner_is_cleared(connection):
    port, api, engine = connection
    port.reset_input_buffer()
    port.write(b'\r\n\r\n')
    assert b'E-Cut host adapter' in port.readline()
    assert port.readline() == b''


def test_realtime_usb_error_is_fatal(connection):
    port, api, engine = connection
    port.write(b'\xa0')
    assert port.readline().startswith(b'ALARM:')
    wait_for(lambda: not port.is_open)


def test_stale_tool_confirmation_cannot_confirm_later_change(connection):
    port, api, engine = connection
    port.write(b'T7M6\n')
    assert port.readline() == b'[ECUT:TOOL:7]\r\n'
    token = port.tool_change_token
    port.confirm_tool_change(7, token=token)
    assert port.readline() == b'ok\r\n'
    port.write(b'T7M6\n')
    assert port.readline() == b'[ECUT:TOOL:7]\r\n'
    assert port.tool_change_token != token
    port.confirm_tool_change(7, token=token)
    assert port.readline().startswith(b'error:')
    assert port.pending_tool_change == 7
    port.confirm_tool_change(7, token=port.tool_change_token)
    assert port.readline() == b'ok\r\n'


def make_board_api():
    from appPlugins.Common.ecut import EcutAPI
    from test_ecut_verified import Board

    class SerialBoard(Board):
        def __init__(self):
            super().__init__()
            self.is_open = False
            self.threads = []

        def open(self):
            self.threads.append(threading.get_ident())
            self.is_open = True

        def close(self):
            self.threads.append(threading.get_ident())
            self.is_open = False

        def poll(self):
            self.threads.append(threading.get_ident())
            return super().poll()

        def cmd(self, opcode, params=b''):
            self.threads.append(threading.get_ident())
            return super().cmd(opcode, params)

        def send_motion(self, records):
            self.threads.append(threading.get_ident())
            return super().send_motion(records)

    api = EcutAPI()
    api._usb = SerialBoard()
    return api


def test_real_api_engine_motion_status_and_manual_tool_cycle():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    board = api._usb
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        assert b'E-Cut host adapter' in port.readline()
        before = api.position_steps
        port.write(b'G91G1X0.01F60\n')
        assert port.readline() == b'ok\r\n'
        assert api.position_steps[0] > before[0]
        port.write(b'T7M6\n')
        assert port.readline() == b'[ECUT:TOOL:7]\r\n'
        assert port.pause_reason == 'M6'
        port.write(b'~')
        assert port.readline().startswith(b'[MSG:E-Cut real-time command refused')
        assert port.pending_tool_change == 7
        port.confirm_tool_change(7, token=port.tool_change_token)
        assert port.readline() == b'ok\r\n'
        port.write(b'M0\n')
        wait_for(lambda: port.pause_reason == 'M0')
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:0|MPos:')
        port.write(b'~')
        assert port.readline() == b'ok\r\n'
    finally:
        port.close()
    assert set(board.threads) == {port._worker.ident}


def test_limit_input_reports_pin_and_alarm():
    import struct
    from appPlugins.Common.ecut import SignalPolarity, decode_rx_report, ecut_crc60
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from test_ecut_verified import report
    api = make_board_api()
    api.configure_input('X_LIMIT_PLUS', pin=2, polarity=SignalPolarity.ACTIVE_HIGH)
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        assert b'E-Cut host adapter' in port.readline()
        data = bytearray(report(tuple(api.position_steps)))
        struct.pack_into('>H', data, 31, 4)
        struct.pack_into('>I', data, 61, ecut_crc60(data[1:61]))
        api._usb.poll = lambda: decode_rx_report(data)
        wait_for(lambda: api.state == State.ESTOP)
        port.write(b'?')
        lines = []
        wait_for(lambda: lines.extend(port.readlines()) or any(x.startswith(b'<') for x in lines))
        assert lines.count(b'ALARM:1\r\n') == 1
        assert any(x.startswith(b'[MSG:E-Cut: Limit input') for x in lines)
        assert b'|Pn:X|' in next(x for x in lines if x.startswith(b'<'))
        port.write(b'?')
        time.sleep(0.2)
        assert b'ALARM:1\r\n' not in port.readlines()
    finally:
        port.close()


def test_real_api_reset_unwinds_dwell_without_old_ack():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        port.readline()
        port.write(b'G4P5\nG91G1X10F60\n')
        wait_for(lambda: api.state == State.RUNNING)
        before = list(api._usb.positions)
        port.write(b'\x18')
        assert b'E-Cut host adapter' in port.readline()
        assert port.readline() == b''
        assert api._usb.positions == before
        port.write(b'G21\n')
        assert port.readline() == b'ok\r\n'
    finally:
        port.close()


def test_default_profile_has_only_sh9001_axes_pwm_and_no_output_roles(monkeypatch):
    from appPlugins.Common.ecut import SpindleControl
    from appPlugins.Common.ecut import ecut_sender_port
    api = make_board_api()
    monkeypatch.setattr(ecut_sender_port, 'EcutAPI', lambda: api)
    port = ecut_sender_port.EcutSenderPort()
    try:
        assert [axis.enabled for axis in api.config.axes] == [True] * 3 + [False] * 3
        assert api.config.spindle.control == SpindleControl.PWM
        assert api.config.spindle.pwm_period_counts == 1000
        assert not api.config.outputs['SPINDLE_CW'].enabled
        assert not api.config.outputs['SPINDLE_CCW'].enabled
    finally:
        port.close()


def test_spindle_stop_override_is_intercepted_without_line_terminator(connection):
    port, api, engine = connection
    port.write(b'\x9e')
    wait_for(lambda: 0x9e in engine.controls)
    assert not engine.lines


def test_reset_failure_never_emits_success_banner(connection):
    port, api, engine = connection
    def fail():
        raise EcutError('Reset readback failed')
    engine.reset = fail
    port.write(b'\x18')
    assert port.readline().startswith(b'ALARM:')
    assert b'E-Cut host adapter' not in b''.join(port.readlines())
    wait_for(lambda: not port.is_open)


def blocked_shutdown_port():
    """A real port whose worker cannot finish the shutdown until the gate opens."""
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    gate = threading.Event()
    api.disconnect = lambda: gate.wait(10)
    port = EcutSenderPort(api=api, engine=FakeEngine(api))
    port.JOIN_TIMEOUT = 0.3
    return port, gate


def test_second_close_of_a_stuck_port_is_fast_and_raises():
    port, gate = blocked_shutdown_port()
    try:
        with pytest.raises(serial.SerialException, match='did not stop'):
            port.close()
        start = time.monotonic()
        with pytest.raises(serial.SerialException, match='did not stop'):
            port.close()
        assert time.monotonic() - start < port.JOIN_TIMEOUT / 2
    finally:
        gate.set()
        port._worker.join(2)
    port.close()


def test_shutdown_error_is_reported_once():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    port = EcutSenderPort(api=api, engine=FakeEngine(api))
    def fail():
        raise EcutError('Stop readback failed')
    api.stop = fail
    with pytest.raises(serial.SerialException, match='Stop readback failed'):
        port.close()
    port.close()  # the dead worker's old error must not block a reconnection forever


def test_failed_verified_shutdown_is_reported():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = FakeAPI()
    engine = FakeEngine(api)
    port = EcutSenderPort(api=api, engine=engine)
    def fail():
        raise EcutError('Stop readback failed')
    api.stop = fail
    with pytest.raises(serial.SerialException, match='Stop readback failed'):
        port.close()
    assert not port._worker.is_alive()
    assert not api.connected


def test_reset_bypasses_a_full_realtime_queue(connection):
    port, api, engine = connection
    port.MAX_CONTROLS = 2
    with port._condition:
        port.write(b'??')
        assert port.write(b'\x18') == 1
    assert b'E-Cut host adapter' in port.readline()


def test_reset_discards_a_long_partial_line_in_same_write(connection):
    port, api, engine = connection
    port.write(b'G' * 4095)
    port.write(b'\x18G21\n')
    assert b'E-Cut host adapter' in port.readline()
    assert port.readline() == b'ok\r\n'
    assert engine.lines == ['G21']


def test_stored_startup_runs_on_worker_after_banner(tmp_path):
    from appPlugins.Common.ecut.ecut_gcode_engine import EcutGcodeEngine
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    path = tmp_path / 'sender.json'
    engine = EcutGcodeEngine(api, settings_path=path)
    assert engine.execute_line('$N0=G91G1X0.01F60') == ['ok']
    before = list(api._usb.positions)
    port = EcutSenderPort(api=api, settings_path=path, timeout=0.2)
    try:
        assert b'E-Cut host adapter' in port.readline()
        assert port.readline() == b'>G91G1X0.01F60:ok\r\n'
        assert api._usb.positions[0] > before[0]
        assert set(api._usb.threads) == {port._worker.ident}
    finally:
        port.close()


@pytest.mark.parametrize('tool_change', [False, True])
def test_sender_stream_lines_conforms_to_real_ecut_connection(tool_change):
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from appPlugins.Common.grbl.grbl_protocol import parse_grbl_status_report
    from appPlugins.Common.grbl.grbl_client import stream_lines

    api = make_board_api()
    before = list(api._usb.positions)
    port = EcutSenderPort(api=api, timeout=0.01)
    reports, traffic, progress, tools = [], [], [], []

    def write(data):
        assert port.write(data) == len(data)
        return 'ok'

    def confirm(tool):
        tools.append(tool)
        port.confirm_tool_change(tool, token=port.tool_change_token)

    lines = ['G21', 'G91', 'G1X0.02Y-0.01F60', 'G4P0.03']
    if tool_change:
        lines += ['T7M6']
    lines += ['G1X-0.01Y0.01', 'G90']
    try:
        assert b'E-Cut host adapter' in port.readline()
        port.tool_change_callback = confirm
        result = stream_lines(
            lines, write, port.readline, rx_size=48,
            poll_status=True, poll_interval=0.001, stall_timeout=1.0,
            on_status=reports.append, on_line=traffic.append,
            on_progress=lambda done, total: progress.append((done, total)))

        assert result['status'] == 'finished'
        assert result['done'] == len(lines)
        assert result['in_flight'] == 0
        assert result['errors'] == []
        assert traffic.count('ok') == len(lines)
        assert progress == [(done, len(lines)) for done in range(1, len(lines) + 1)]
        assert reports and all(parse_grbl_status_report(text)['mpos'] is not None for text in reports)
        assert tools == ([7] if tool_change else [])
        assert ('[ECUT:TOOL:7]' in traffic) == tool_change
        scale = api.config.interpolation_factor
        assert api._usb.positions[0] == before[0] + round(0.01 * api.config.axes[0].steps_per_mm * scale)
        assert api._usb.positions[1] == before[1]

        while port.in_waiting:
            port.readline()
        port.write(b'?')
        final = parse_grbl_status_report(port.readline().decode('ascii').strip())
        assert final['state'] == 'Idle'
        measured = [steps / (axis.steps_per_mm * scale)
                    for steps, axis in zip(api._usb.positions[:3], api.config.axes[:3])]
        assert final['mpos'] == pytest.approx(measured, abs=0.0005)
        assert set(api._usb.threads) == {port._worker.ident}
    finally:
        port.close()


def test_fault_during_program_gives_one_alarm_line(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21', 'LIMIT'])
    for line in (b'G21\n', b'LIMIT\n'):
        port.write(line)
        assert port.readline() == b'ok\r\n'
    lines = []
    wait_for(lambda: lines.extend(port.readlines()) or any(x.startswith(b'[MSG:E-Cut program') for x in lines))
    time.sleep(0.2)
    lines.extend(port.readlines())
    alarms = [x for x in lines if x.startswith(b'ALARM')]
    assert alarms == [b'ALARM:10\r\n']


def test_prepared_upload_acks_acceptance_before_single_execution(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21', 'WAIT'])
    port.write(b'G21\n')
    assert port.readline() == b'ok\r\n'
    assert engine.lines == []
    port.write(b'?')
    assert port.readline().startswith(b'<Run|')
    port.write(b'WAIT\n')
    assert port.readline() == b'ok\r\n'
    assert engine.entered.wait(1)
    assert engine.programs == [('G21', 'WAIT')]
    assert port.readline() == b''
    engine.release.set()
    wait_for(lambda: not port._active and port._prepared_job is None)
    port.write(b'?')
    assert port.readline().startswith(b'<Idle|')
    assert {ident for _, ident in api.calls} == {port._worker.ident}


def test_prepared_upload_mismatch_discards_job_without_execution(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21', 'G90'])
    port.write(b'G91\nG90\n')
    assert port.readline().startswith(b'error:')
    assert engine.lines == []
    assert engine.programs == []
    with pytest.raises(serial.SerialException):
        port.write(b'G1X1\n')


@pytest.mark.parametrize('lines', [[], ['G21\n'], ['G1X?1'], ['M3!'], ['G21\ud800'],['G' * 4097]])
def test_prepare_rejects_invalid_source_framing(connection, lines):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    with pytest.raises((ValueError, serial.SerialException)):
        port.prepare_program(lines)
    assert not any(name == 'prepare' for name, _ in api.calls)


def test_prepare_rejects_check_mode_and_busy_connection(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    engine.check_mode = True
    with pytest.raises(serial.SerialException):
        port.prepare_program(['G21'])
    engine.check_mode = False
    port.write(b'WAIT\n')
    assert engine.entered.wait(1)
    with pytest.raises(serial.SerialException):
        port.prepare_program(['G21'])


def test_prepare_timeout_frees_the_worker(connection, monkeypatch):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    monkeypatch.setattr(port, 'PREPARE_TIMEOUT', 0.05)
    with pytest.raises(serial.SerialException):
        port.prepare_program(['SLOW'])
    wait_for(lambda: not port._active)
    port.prepare_program(['G21'])


def test_reset_cancels_incomplete_prepared_upload(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21', 'WAIT'])
    port.write(b'G21\n')
    assert port.readline() == b'ok\r\n'
    port.write(b'\x18')
    assert b'E-Cut host adapter' in port.readline()
    assert engine.programs == []
    port.write(b'G90\n')
    assert port.readline() == b'ok\r\n'
    assert engine.lines == ['G90']


def test_reset_resolves_a_blocked_program_preparation(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def blocked(lines):
        entered.set()
        assert release.wait(1)
        return tuple(lines)
    engine.prepare_program = blocked
    def prepare():
        try:
            port.prepare_program(['G21'])
        except serial.SerialException as error:
            errors.append(str(error))
    caller = threading.Thread(target=prepare)
    caller.start()
    try:
        assert entered.wait(1)
        port.write(b'\x18')
        caller.join(0.2)
        assert not caller.is_alive()
        assert errors
    finally:
        release.set()
        caller.join(1)
    assert b'E-Cut host adapter' in port.readline()
    assert engine.programs == []


def test_prepared_sender_batches_real_engine_linear_motion():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from appPlugins.Common.grbl.grbl_client import stream_lines
    api = make_board_api()
    calls = []
    run_gcode = api.run_gcode
    def run(lines):
        calls.append(tuple(lines))
        return run_gcode(lines)
    api.run_gcode = run
    port = EcutSenderPort(api=api, timeout=0.01)
    lines = ['G21', 'G91', 'G1X0.01F60', 'G1X0.02', 'G1X-0.01']
    try:
        port.readline()
        wait_for(lambda: not port._startup_pending and not port._active)
        before = list(api._usb.positions)
        port.prepare_program(lines)
        assert not calls
        def write(data):
            assert port.write(data) == len(data)
            return 'ok'
        result = stream_lines(lines, write, port.readline, rx_size=32,
                              poll_status=True, poll_interval=0.001, stall_timeout=1)
        assert result['status'] == 'finished'
        assert result['done'] == len(lines)
        wait_for(lambda: port._prepared_job is None and not port._active)
        assert len(calls) == 1
        expected = round(0.02 * api.config.axes[0].steps_per_mm * api.config.interpolation_factor)
        assert api._usb.positions[0] == before[0] + expected
        assert b'ok' not in b''.join(port.readlines())
    finally:
        port.close()


def test_prepared_real_job_preserves_m6_confirmation_and_reset():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from appPlugins.Common.grbl.grbl_client import stream_lines
    api = make_board_api()
    port = EcutSenderPort(api=api, timeout=0.03)
    lines = ['G91', 'G1X0.01F60', 'T7M6', 'G4P5', 'G1X10']
    requested = []
    port.tool_change_callback = requested.append
    try:
        port.readline()
        wait_for(lambda: not port._startup_pending and not port._active)
        port.prepare_program(lines)
        def write(data):
            assert port.write(data) == len(data)
            return 'ok'
        result = stream_lines(lines, write, port.readline, rx_size=32,
                              poll_status=True, poll_interval=0.001, stall_timeout=1)
        assert result['status'] == 'finished'
        wait_for(lambda: port.pending_tool_change == 7)
        assert requested == [7]
        assert port.pause_reason == 'M6'
        port.reset_input_buffer()
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:0|MPos:')
        port.confirm_tool_change(7, token=port.tool_change_token)
        wait_for(lambda: port.pending_tool_change is None and api.state == State.RUNNING)
        before = list(api._usb.positions)
        port.write(b'\x18')
        assert b'E-Cut host adapter' in port.readline()
        wait_for(lambda: not port._active and port._prepared_job is None)
        assert api._usb.positions == before
        assert b'ok' not in b''.join(port.readlines())
    finally:
        port.close()


def test_extra_partial_source_cancels_prepared_job_without_execution(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21'])
    port.write(b'G21\nG')
    assert port.readline() == b'ok\r\n'
    assert port.readline().startswith(b'error:')
    assert not engine.programs


def test_prepared_runtime_failure_reports_alarm_after_source_ack(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['FAIL'])
    port.write(b'FAIL\n')
    assert port.readline() == b'ok\r\n'
    assert port.readline().startswith(b'ALARM:')
    wait_for(lambda: not port.is_open)


def test_preparation_timeout_discards_late_worker_result(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.PREPARE_TIMEOUT = 0.02
    release = threading.Event()
    def blocked(lines):
        assert release.wait(1)
        return tuple(lines)
    engine.prepare_program = blocked
    try:
        with pytest.raises(serial.SerialException, match='timed out'):
            port.prepare_program(['G21'])
    finally:
        release.set()
    wait_for(lambda: not port._active)
    assert port._prepared_job is None
    assert not engine.programs


@pytest.mark.parametrize('state', [State.PAUSED, State.RUNNING, State.ESTOP, State.ERROR])
def test_program_preparation_requires_worker_verified_idle_state(connection, state):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    api.state = state
    with pytest.raises(serial.SerialException):
        port.prepare_program(['G21'])
    assert not any(name == 'prepare' for name, _ in api.calls)


def test_hold_reports_draining_until_fresh_fifo_and_measured_target_are_idle():
    from appPlugins.Common.ecut import decode_rx_report
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from test_ecut_verified import report
    api = make_board_api()
    board = api._usb
    status = {'busy': False, 'fifo': 12000, 'offset': 0, 'silent': False}
    def poll():
        board.threads.append(threading.get_ident())
        if status['silent']:
            return None
        positions = list(board.positions)
        positions[0] += status['offset']
        return decode_rx_report(report(tuple(positions), status=0x81 if status['busy'] else 1,
                                       fifo=status['fifo']))
    board.poll = poll
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        port.readline()
        port.write(b'G4P5\n')
        wait_for(lambda: api.state == State.RUNNING)
        status.update(busy=True, fifo=11900)
        port.write(b'!')
        wait_for(lambda: api.state == State.PAUSED)
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:1|MPos:')
        status['busy'] = False
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:1|MPos:')
        status.update(fifo=12000, offset=1)
        time.sleep(0.02)
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:1|MPos:')
        status['offset'] = 0
        time.sleep(0.02)
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:0|MPos:')
    finally:
        status.update(busy=False, fifo=12000, offset=0, silent=False)
        port.close()


def test_hold_without_any_rx_frame_is_conservatively_draining(connection):
    port, api, engine = connection
    port.write(b'!')
    port.write(b'?')
    assert port.readline().startswith(b'<Hold:1|MPos:')


def test_hold_with_stale_rx_frame_is_conservatively_draining():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    poll = api._usb.poll
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        port.readline()
        wait_for(lambda: not port._active and not port._startup_pending)
        port.HOLD_STATUS_MAX_AGE = 0.01
        api._usb.poll = lambda: None
        port.write(b'!')
        time.sleep(0.03)
        port.write(b'?')
        assert port.readline().startswith(b'<Hold:1|MPos:')
    finally:
        api._usb.poll = poll
        port.close()


@pytest.mark.parametrize('control', [0x18, 0x85, 'close'])
def test_planning_checkpoint_cancels_preparation_before_it_completes(connection, control):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    entered, finished = threading.Event(), threading.Event()
    errors = []
    def preflight(lines):
        entered.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            api._planning_checkpoint()
            time.sleep(0.001)
        finished.set()
        return tuple(lines)
    engine.prepare_program = preflight
    def prepare():
        try:
            port.prepare_program(['G21'])
        except serial.SerialException as error:
            errors.append(str(error))
    caller = threading.Thread(target=prepare)
    caller.start()
    assert entered.wait(1)
    if control == 'close':
        port.close()
    else:
        port.write(bytes([control]))
    caller.join(1)
    assert not caller.is_alive()
    assert errors
    assert not finished.is_set()
    wait_for(lambda: not port._active)
    assert not finished.is_set()
    assert port._prepared_job is None
    assert not engine.programs
    expected = 'reset' if control == 0x18 else 'stop'
    assert any(name == expected for name, _ in api.calls)


@pytest.mark.parametrize('control', [0x91, 0xa0])
def test_partial_real_upload_refuses_overrides_and_output_toggles(control):
    import struct
    from appPlugins.Common.ecut import Cmd, decode_rx_report
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    from test_ecut_verified import report
    api = make_board_api()
    api.configure_output('FLOOD', enabled=True, pin=2)
    board = api._usb
    outputs = [0]
    command = board.cmd
    def cmd(opcode, params=b''):
        command(opcode, params)
        if opcode == Cmd.SET_OUTPUTS:
            outputs[0] = struct.unpack_from('>I', params, 8)[0]
    def poll():
        board.threads.append(threading.get_ident())
        return decode_rx_report(report(tuple(board.positions), outputs=outputs[0]))
    board.cmd, board.poll = cmd, poll
    port = EcutSenderPort(api=api, timeout=0.1)
    try:
        port.readline()
        wait_for(lambda: not port._startup_pending and not port._active)
        port.prepare_program(['G91', 'G1X0.01F60'])
        port.write(b'G91\n')
        assert port.readline() == b'ok\r\n'
        before_commands = list(board.commands)
        before_position = list(board.positions)
        before_override = api.feed_override
        port.write(bytes([control]))
        assert port.readline().startswith(b'[MSG:E-Cut real-time command refused')
        assert api.feed_override == before_override
        assert board.commands == before_commands
        assert outputs[0] == 0
        assert board.positions == before_position
        port.write(b'G1X0.01F60\n')
        assert port.readline() == b'ok\r\n'
        wait_for(lambda: not port._active and port._prepared_job is None)
        assert board.positions[0] > before_position[0]
        assert b'ALARM:' not in b''.join(port.readlines())
        assert outputs[0] == 0
    finally:
        port.close()


def test_worker_preflight_refuses_realtime_override_without_invalidating_job(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def preflight(lines):
        entered.set()
        while not release.is_set():
            api._planning_checkpoint()
            time.sleep(0.001)
        return tuple(lines)
    engine.prepare_program = preflight
    def prepare():
        try:
            port.prepare_program(['G21'])
        except serial.SerialException as error:
            errors.append(str(error))
    caller = threading.Thread(target=prepare)
    caller.start()
    try:
        assert entered.wait(1)
        port.write(b'\x91\xa0')
        assert port.readline().startswith(b'[MSG:E-Cut real-time command refused')
        assert port.readline().startswith(b'[MSG:E-Cut real-time command refused')
        assert engine.controls == []
    finally:
        release.set()
        caller.join(1)
    assert not errors
    assert not caller.is_alive()
    port.write(b'G21\n')
    assert port.readline() == b'ok\r\n'
    wait_for(lambda: engine.programs == [('G21',)])


def test_utf8_comment_bytes_are_text_not_realtime_commands(connection):
    port, api, engine = connection
    # 'ș' = c8 99 (spindle override), 'à' = c3 a0 (flood toggle), 'Å' = c3 85 (jog cancel)
    line = '(Plăcuță ș à Å)G21\n'.encode('utf-8')
    port.write(line[:3])
    port.write(line[3:])
    assert port.readline() == b'ok\r\n'
    assert engine.controls == []
    assert engine.lines == ['(Plăcuță ș à Å)G21']


def test_soft_reset_wins_over_jog_cancel_in_one_write(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.write(b'\x18\x85')
    wait_for(lambda: any(name == 'reset' for name, _ in api.calls))


def test_refused_line_keeps_connection_open(connection):
    port, api, engine = connection
    port.write(b'REFUSE\nG21\n')
    assert port.readline().startswith(b'[MSG:E-Cut: refused by the controller')
    assert port.readline() == b'error:20\r\n'
    assert port.readline() == b'ok\r\n'
    assert port.is_open
    assert engine.lines == ['REFUSE', 'G21']


def test_prepare_accepts_utf8_comment():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        port.readline()
        wait_for(lambda: not port._startup_pending and not port._active)
        port.prepare_program(['(Plăcuță ș)', 'G21'])
        with pytest.raises((ValueError, serial.SerialException)):
            port.prepare_program(['G21 X1é'])
    finally:
        port.close()


def test_reference_axes_runs_on_the_worker_and_reports_homed_axes():
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    port = EcutSenderPort(api=api, timeout=0.2)
    try:
        port.readline()
        wait_for(lambda: not port._startup_pending and not port._active)
        assert port.reference_axes([0, 2]) == {'homed_axes': [0, 2]}
        assert api._usb.positions[0] == 0 and api._usb.positions[2] == 0
        assert port.get_io_state()['homed_axes'] == [0, 2]
        with pytest.raises(serial.SerialException):
            port.reference_axes([5])
    finally:
        port.close()


class GatedBoard:
    """Blocks the first motion packet so a test can read the queue state in the middle of a stream."""

    def __init__(self, api, fifo=12000):
        from test_ecut_verified import report
        from appPlugins.Common.ecut import decode_rx_report
        self.reached = threading.Event()
        self.proceed = threading.Event()
        board = api._usb
        send = board.send_motion

        def gated(records):
            if not self.reached.is_set():
                self.reached.set()
                assert self.proceed.wait(5)
            return send(records)
        board.send_motion = gated
        self.fifo = fifo
        board.poll = lambda: decode_rx_report(report(tuple(board.positions), fifo=self.fifo))


QUEUE_PROGRAM = ['G21', 'G91', 'G1X0.5F600', 'G4P0.001', 'G1Y0.5', 'G4P0.001', 'G1X-0.5']


def test_api_queue_state_counts_down_to_idle_and_resets_on_abort():
    api = make_board_api()
    gate = GatedBoard(api)
    api.connect()
    api.push_config()
    assert api.queue_state() == {'blocks_waiting': 0, 'blocks_active': 0, 'records_unsent': 0}
    runner = threading.Thread(target=api.run_gcode, args=(QUEUE_PROGRAM,))
    runner.start()
    try:
        assert gate.reached.wait(2)
        first = api.queue_state()
        assert (first['blocks_active'], first['blocks_waiting']) == (1, 2)
        assert first['records_unsent'] > 0
        seen = []
        gate.proceed.set()
        while runner.is_alive():
            seen.append(api.queue_state())
            time.sleep(0.0005)
        seen.append(api.queue_state())
        remaining = [s['records_unsent'] for s in seen]
        assert remaining == sorted(remaining, reverse=True) and remaining[0] <= first['records_unsent']
        assert api.queue_state() == {'blocks_waiting': 0, 'blocks_active': 0, 'records_unsent': 0}
    finally:
        gate.proceed.set()
        runner.join(5)
    # abort in the middle: nothing stays queued
    api = make_board_api()
    gate = GatedBoard(api)
    api.connect()
    api.push_config()
    runner = threading.Thread(target=api.run_gcode, args=(QUEUE_PROGRAM,))
    runner.start()
    try:
        assert gate.reached.wait(2)
        assert api.queue_state()['blocks_active'] == 1
        api.stop()
        gate.proceed.set()
        runner.join(5)
        assert api.queue_state() == {'blocks_waiting': 0, 'blocks_active': 0, 'records_unsent': 0}
    finally:
        gate.proceed.set()


def test_api_queue_state_is_idle_for_empty_and_refused_programs():
    api = make_board_api()
    api.connect()
    api.push_config()
    api.run_gcode([])
    assert api.queue_state()['blocks_waiting'] == 0
    with pytest.raises(EcutError):
        api.run_gcode(['G1X1', 'G999'])
    assert api.queue_state() == {'blocks_waiting': 0, 'blocks_active': 0, 'records_unsent': 0}


def _queue_port(fifo=12000):
    from appPlugins.Common.ecut.ecut_sender_port import EcutSenderPort
    api = make_board_api()
    gate = GatedBoard(api, fifo)
    port = EcutSenderPort(api=api, timeout=0.05)
    port.readline()
    wait_for(lambda: not port._startup_pending and not port._active)
    return port, api, gate


def _stream_program(port, lines):
    from appPlugins.Common.grbl.grbl_client import stream_lines
    port.prepare_program(lines)

    def write(data):
        assert port.write(data) == len(data)
        return 'ok'
    threading.Thread(target=stream_lines, args=(lines, write, port.readline),
                     kwargs=dict(rx_size=32, poll_status=True, poll_interval=0.001, stall_timeout=2),
                     daemon=True).start()


def test_port_queue_state_shows_work_then_idle_and_clears_on_soft_reset():
    port, api, gate = _queue_port()
    try:
        assert port.get_queue_state()['idle'] is True
        _stream_program(port, QUEUE_PROGRAM)
        assert gate.reached.wait(3)
        state = port.get_queue_state()
        assert state['idle'] is False and state['blocks_active'] == 1 and state['records_unsent'] > 0
        port.write(b'\x18')
        gate.proceed.set()
        wait_for(lambda: port.get_queue_state()['idle'], 3)
        state = port.get_queue_state()
        assert (state['lines_waiting'], state['blocks_waiting'], state['blocks_active']) == (0, 0, 0)
        # a second run after the reset counts again
        gate2 = GatedBoard(api)
        port.write(b'G91G1X0.5F600\n')
        assert gate2.reached.wait(3)
        assert port.get_queue_state()['idle'] is False
        gate2.proceed.set()
        wait_for(lambda: port.get_queue_state()['idle'], 3)
    finally:
        gate.proceed.set()
        port.close()


def test_port_queue_state_stays_busy_during_feed_hold_and_ends_idle():
    port, api, gate = _queue_port()
    try:
        port.write(b'G91G1X0.5F600\nG1Y0.5F600\n')
        assert gate.reached.wait(3)
        port.write(b'!')
        gate.proceed.set()
        wait_for(lambda: api.state == State.PAUSED)
        held = port.get_queue_state()
        assert held['idle'] is False and held['blocks_active'] == 1
        port.write(b'~')
        wait_for(lambda: port.get_queue_state()['idle'], 5)
    finally:
        gate.proceed.set()
        port.close()


def test_port_queue_state_queued_lines_and_board_fifo():
    port, api, gate = _queue_port()
    gate.fifo = 9000
    try:
        port.write(b'G91G1X0.5F600\nG1Y0.5F600\nG1X-0.5\n')
        assert gate.reached.wait(3)
        wait_for(lambda: port.get_queue_state()['lines_waiting'] == 2)
        state = port.get_queue_state()
        assert state['board_queued'] is True
        gate.fifo = 12000
        gate.proceed.set()
        wait_for(lambda: port.get_queue_state()['lines_waiting'] == 0, 5)
        wait_for(lambda: port.get_queue_state()['idle'], 5)
        assert port.get_queue_state()['board_queued'] is False
    finally:
        gate.fifo = 12000
        gate.proceed.set()
        port.close()


def test_port_queue_state_after_close_does_not_raise():
    port, api, gate = _queue_port()
    port.close()
    assert port.get_queue_state()['idle'] is True


def _io_config():
    from appPlugins.Common.ecut.ecut_config import EcutBoardConfig, InputSignal, OutputSignal, SignalPolarity
    cfg = EcutBoardConfig()
    cfg.inputs = {'PROBE': InputSignal(True, 3, SignalPolarity.ACTIVE_HIGH, 0),
                  'DOOR': InputSignal(True, 4, SignalPolarity.ACTIVE_LOW, 0),
                  'FEED_HOLD': InputSignal(True, 5, SignalPolarity.ACTIVE_HIGH, 0, port=2),
                  'CYCLE_START': InputSignal(True, 20, SignalPolarity.ACTIVE_HIGH, 0)}
    cfg.outputs = {'FLOOD': OutputSignal(True, 1, False), 'MIST': OutputSignal(True, 1, False),
                   'LASER': OutputSignal(True, 2, True), 'STEP_X': OutputSignal(True, 3, False),
                   'SPINDLE_PWM': OutputSignal(True, 4, False), 'OFF': OutputSignal(False, 5, False),
                   'HIGH': OutputSignal(True, 9, False)}
    return cfg


def test_ecut_io_decode_inputs_outputs_and_gray_cases():
    from appPlugins.Common.ecut.ecut_controller import decode_ecut_io, effective_inputs, effective_outputs
    cfg = _io_config()
    cfg.safety.estop_input_enabled = True
    cfg.safety.estop_input_pin = 7
    ins, outs = effective_inputs(cfg), effective_outputs(cfg)
    assert [e['names'] for e in outs] == [['FLOOD', 'MIST'], ['LASER']]
    state = {'fresh': True, 'configured': True, 'inputs': 1 << 3 | 1 << 7, 'outputs': 1 << 1}
    got = decode_ecut_io(state, ins, outs)
    assert got['PROBE'] == (True, '')
    assert got['DOOR'][0] is True          # active low, pin low
    assert got['ESTOP'][0] is not None    # safety override shows the E-stop
    assert got['FEED_HOLD'][0] is None and 'port/pin' in got['FEED_HOLD'][1]
    assert got['CYCLE_START'][0] is None
    assert got['FLOOD'] == got['MIST'] == (True, '')
    assert got['LASER'] == (True, '')       # inverted output, pin low
    assert 'STEP_X' not in got and 'HIGH' not in got
    for bad in ({**state, 'fresh': False}, {**state, 'configured': False}):
        assert all(v[0] is None for v in decode_ecut_io(bad, ins, outs).values())


def test_io_tab_reads_only_the_cached_state_and_writes_through_the_port_worker():
    import os
    import sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from PyQt6 import QtWidgets
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from appPlugins.Common.ecut.ecut_controller import effective_inputs, effective_outputs
    from tests.grbl_stream_helpers import make_stream_tool
    from tests.test_levelling_audit import FakeGrbl
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    cfg = _io_config()
    cfg.safety.estop_input_enabled = True
    cfg.safety.estop_input_pin = 7

    class Port(FakeGrbl):
        @staticmethod
        def effective_inputs():
            return effective_inputs(cfg)

        @staticmethod
        def effective_outputs():
            return effective_outputs(cfg)

        def __init__(self):
            super().__init__()
            self.calls = []
            self.io = {'fresh': True, 'configured': True, 'connected': True, 'busy': False, 'inputs': 1 << 3 | 1 << 7,
                       'outputs': 0}

        def get_motion_settings(self):
            return {'revision': 7}

        save_motion_settings = calibration_move = lambda self, *args: None

        def get_io_state(self):
            self.calls.append('io')
            return dict(self.io)

        def set_manual_output(self, pin, active, revision):
            self.calls.append(('set', pin, active, revision))
            self.io['outputs'] = self.io['outputs'] | (1 << pin) if active else self.io['outputs'] & ~(1 << pin)
            return dict(self.io)

    port = Port()
    tool = make_stream_tool(port)
    tool._status_poll_timer = MagicMock()
    tool.io_discovery_sig = MagicMock()
    tool.ecut_motion_result_sig.emit.side_effect = tool._on_ecut_motion_result
    io = tool.sender_io
    io._page_shown = lambda: True
    page = io.new_page()
    io.discover(port)
    try:
        assert port.calls == []
        io.tick()
        assert port.calls == ['io']                              # only the cached state, no other request
        assert page.inputs['PROBE'].state is True
        assert page.inputs['DOOR'].state is True                 # active low, pin low
        assert page.inputs['ESTOP'].state is not None           # the safety E-stop shows
        assert page.inputs['FEED_HOLD'].state is None            # port 2: not readable
        assert page.outputs['FLOOD'].state is False and page.outputs['LASER'].state is True
        assert 'FLOOD / MIST' in page.outputs['FLOOD'].toolTip()
        io.on_output_requested('LASER', False)                   # inverted: Off is the physical high level
        assert port.calls[-1] == ('set', 2, True, 7)
        assert io.pending is None and page.outputs['LASER'].state is False
        io.on_output_requested('FLOOD', True)
        assert port.calls[-1] == ('set', 1, True, 7)
        assert page.outputs['FLOOD'].state is True               # the readback of the write
        port.io['busy'] = True                                   # a busy board takes no write
        io.tick()
        io.on_output_requested('FLOOD', False)
        assert port.calls[-1] == 'io'
    finally:
        page.deleteLater()
        if io._timer is not None:
            io._timer.stop()


def test_io_tab_follows_a_saved_settings_config_pins_and_revision_together():
    import os
    import sys
    from unittest.mock import MagicMock
    from PyQt6 import QtWidgets
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from appPlugins.Common.ecut.ecut_controller import effective_inputs, effective_outputs
    from appPlugins.Common.ecut.ecut_config import InputSignal, OutputSignal, SignalPolarity
    from tests.grbl_stream_helpers import make_stream_tool
    from tests.test_levelling_audit import FakeGrbl
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    cfg = _io_config()

    class Port(FakeGrbl):
        effective_inputs = staticmethod(lambda: effective_inputs(cfg))
        effective_outputs = staticmethod(lambda: effective_outputs(cfg))
        save_motion_settings = calibration_move = lambda self, *args: None

        def __init__(self):
            super().__init__()
            self.calls = []
            self.io = {'fresh': True, 'configured': True, 'connected': True, 'busy': False, 'inputs': 0, 'outputs': 0}

        def get_motion_settings(self):
            return {'revision': 7}

        def get_io_state(self):
            return dict(self.io)

        def set_manual_output(self, pin, active, revision):
            self.calls.append(('set', pin, active, revision))
            return dict(self.io)

    port = Port()
    tool = make_stream_tool(port)
    tool._status_poll_timer = MagicMock()
    tool.io_discovery_sig = MagicMock()
    tool.ecut_motion_result_sig.emit.side_effect = tool._on_ecut_motion_result
    io = tool.sender_io
    io._page_shown = lambda: True
    page = io.new_page()
    io.discover(port)
    try:
        io.revision = 7
        assert 'O1' in page.outputs['FLOOD'].text()
        cfg.outputs['FLOOD'] = OutputSignal(True, 6, False)
        cfg.outputs['MIST'] = OutputSignal(True, 6, False)
        cfg.inputs['PROBE'] = InputSignal(True, 8, SignalPolarity.ACTIVE_HIGH, 0)
        port.io['inputs'] = 1 << 8
        dialog = MagicMock()
        tool._ecut_motion_dialog = dialog
        tool._refresh_ecut_motion_io = MagicMock()
        tool._on_ecut_motion_result((port, dialog, 'save', {'revision': 8}, None))
        assert 'O6' in page.outputs['FLOOD'].text()
        io.tick()
        assert page.inputs['PROBE'].state is True              # the new pin, not the old one
        io.on_output_requested('FLOOD', True)
        assert port.calls[-1] == ('set', 6, True, 8)
    finally:
        page.deleteLater()
        if io._timer is not None:
            io._timer.stop()


def test_feed_override_reaches_the_engine_while_a_prepared_program_runs(connection):
    port, api, engine = connection
    wait_for(lambda: not port._startup_pending and not port._active)
    port.prepare_program(['G21', 'WAIT'])
    port.write(b'G21\nWAIT\n')
    wait_for(engine.entered.is_set)
    port.write(bytes([0x91]))
    wait_for(lambda: 0x91 in engine.controls)
    port.write(bytes([0x95]))
    lines = []
    wait_for(lambda: lines.extend(port.readlines()) or any(x.startswith(b'[MSG:E-Cut real-time command refused') for x in lines))
    assert 0x95 not in engine.controls


