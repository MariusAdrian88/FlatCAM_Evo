"""S1 serial cancellation, physical completion and recovery regressions."""
from unittest.mock import patch

import pytest
import serial

import test_levelling_audit as audit
from test_levelling_audit import FakeGrbl, make_audit_tool, run_worker


class SafetyGrbl(FakeGrbl):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_line = None
        self.fail_hold = False

    def readline(self):
        line = super().readline()
        if self.on_line is not None:
            self.on_line(line)
        return line

    def write(self, data):
        if self.fail_hold and data == b'!':
            raise serial.SerialException('hold write failed')
        return super().write(data)


def probe_tool(fake, units='MM'):
    tool, _ = audit.TestGrblProbeRun()._tool(fake, units=units)
    # Keep worker emission distinct from GUI publication (owned by another task).
    tool.al_result_ready_sig.emit.side_effect = None
    return tool


@pytest.mark.parametrize('action', ['stop', 'reset'])
def test_home_cancel_inside_final_read(action):
    fake = SafetyGrbl()
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool._grbl_recovery = 'needs_home'
    fake.on_line = lambda line: getattr(tool, 'on_grbl_' + action)() if line == b'ok\r\n' else None
    tool.on_grbl_homing()
    run_worker(tool)
    assert tool._grbl_recovery == 'needs_reset'
    assert not any('[success]' in c.args[0] for c in tool.app.inform.emit.call_args_list)


@pytest.mark.parametrize('action', ['stop', 'reset'])
def test_probe_cancel_inside_final_read(action):
    fake = SafetyGrbl()
    tool = probe_tool(fake)

    def cancel(line):
        if fake.probe_count == 2 and fake.commands()[-1] == 'G0 Z2.0000' and line == b'ok\r\n':
            getattr(tool, 'on_grbl_' + action)()

    fake.on_line = cancel
    tool.on_grbl_autolevel()
    run_worker(tool)
    assert not any(c.args[0]['kind'] == 'probe' for c in tool.al_result_ready_sig.emit.call_args_list)


@pytest.mark.parametrize('entry', ['typed', 'jog'])
def test_motion_retains_ownership_until_completion(entry):
    fake = SafetyGrbl(state='Jog' if entry == 'jog' else 'Run')
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool.ui.grbl_command_entry.get_value.return_value = 'G0 X1'
    tool.ui.jog_step_entry.get_value.return_value = 1.0
    tool.ui.jog_fr_entry.get_value.return_value = 100.0
    observed = []

    def completion(line):
        # A status query or G4 synchronization is the physical-completion interval.
        if fake.commands()[-1] in ('?', 'G4 P0'):
            observed.append(tool._grbl_op is not None and tool._grbl_op.motion)
            tool.on_grbl_reset()

    fake.on_line = completion
    if entry == 'typed':
        tool.on_grbl_send_command()
    else:
        tool.on_grbl_jog('xplus')
    run_worker(tool)
    assert observed, 'Acceptance alone must not end motion ownership'
    assert all(observed)
    assert tool._grbl_recovery is not None


@pytest.mark.parametrize('entry,failure', [('typed', 'timeout'), ('jog', 'alarm'), ('home', 'port_error')])
def test_motion_failure_blocks_next_motion(entry, failure):
    prefix = {'typed': 'G0', 'jog': '$J=', 'home': '$H'}[entry]
    fake = SafetyGrbl(replies={prefix: [] if failure == 'timeout' else ['ALARM:9']})
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool.ui.grbl_command_entry.get_value.return_value = 'G0 X1'
    tool.ui.jog_step_entry.get_value.return_value = 1.0
    tool.ui.jog_fr_entry.get_value.return_value = 100.0
    if failure == 'port_error':
        fake.readline = lambda: (_ for _ in ()).throw(serial.SerialException('read failed'))
    if entry == 'typed':
        tool.on_grbl_send_command()
    elif entry == 'jog':
        tool.on_grbl_jog('xplus')
    else:
        tool.on_grbl_homing()
    # Advance real transaction deadlines without waiting 10/30/120 seconds.
    clock = iter(range(0, 10000, 200))
    with patch('appPlugins.Common.grbl.grbl_client.time.monotonic', side_effect=lambda: next(clock)):
        run_worker(tool)
    assert tool._grbl_recovery == 'needs_reset'
    assert tool._grbl_reserve('next motion', motion=True) is None
    if failure != 'alarm':
        assert '!' in fake.commands(), 'Uncertain motion requires feed hold'


def test_failed_stop_write_still_requires_reset():
    fake = SafetyGrbl()
    fake.fail_hold = True
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    op = tool._grbl_reserve('moving', motion=True)
    tool.on_grbl_stop()
    tool._grbl_release(op)
    assert op.cancel.is_set()
    assert tool._grbl_recovery == 'needs_reset'
    assert tool._grbl_reserve('next motion', motion=True) is None


def test_queued_alarm_between_transactions_is_not_flushed():
    fake = SafetyGrbl()
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    op = tool._grbl_reserve('motion', motion=True)
    assert tool._grbl_transact(op, 'G90')[1] == 'ok'
    fake._send('ALARM:1')
    text, status = tool._grbl_transact(op, 'G0 X1')
    assert status != 'ok', 'An asynchronous alarm must not disappear in an input flush'
    assert tool._grbl_recovery == 'needs_reset'


@pytest.mark.parametrize('record', ['[PRB:bad,0,0:1]', '[PRB:nan,0,0:1]'])
def test_uncertain_probe_record_sends_hold(record):
    fake = SafetyGrbl(replies={'G38.2': [record, 'ok']})
    tool = probe_tool(fake)
    tool.on_grbl_autolevel()
    run_worker(tool)
    assert '!' in fake.commands(), 'Invalid numeric records are uncertainty, not confirmed Alarm'
    assert tool._grbl_recovery == 'needs_reset'


def test_duplicate_probe_records_require_recovery():
    fake = SafetyGrbl(replies={'G38.2': ['[PRB:0,0,0:1]', '[PRB:0,0,0:1]', 'ok']})
    tool = probe_tool(fake)
    tool.on_grbl_autolevel()
    run_worker(tool)
    assert '!' in fake.commands()
    assert tool._grbl_recovery == 'needs_reset'
    assert not any(c.args[0]['kind'] == 'probe' for c in tool.al_result_ready_sig.emit.call_args_list)


def test_captured_units_drive_probe_scaling():
    fake = SafetyGrbl(heights={(0.0, 0.0): 2.54, (10.0, 0.0): 2.54})
    tool = probe_tool(fake, units='IN')
    tool.on_grbl_autolevel()
    tool.units = 'MM'
    run_worker(tool)
    payload = tool.al_result_ready_sig.emit.call_args.args[0]
    assert payload['kind'] == 'probe'
    assert payload['heights'][1] == pytest.approx(0.1)


def test_nonzero_wcs_g92_tlo_offsets():
    fake = SafetyGrbl(replies={
        '$#': ['[G54:1,2,3]', '[G92:4,5,6]', '[TLO:7]', 'ok'],
        'G38.2': ['[PRB:5,7,16.25:1]', 'ok'],
    })
    tool = probe_tool(fake)
    # One point makes a fixed controller record unambiguous.
    tool.al_voronoi_geo_storage.pop(2)
    tool.on_grbl_autolevel()
    run_worker(tool)
    payload = tool.al_result_ready_sig.emit.call_args.args[0]
    assert payload['kind'] == 'probe'
    assert payload['heights'][1] == pytest.approx(0.25)


@pytest.mark.parametrize('entry', ['typed', 'jog'])
def test_motion_waits_through_run_until_idle(entry):
    fake = SafetyGrbl(state='Run' if entry == 'typed' else 'Jog')
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool.ui.grbl_command_entry.get_value.return_value = 'G0 X1'
    tool.ui.jog_step_entry.get_value.return_value = 1.0
    tool.ui.jog_fr_entry.get_value.return_value = 100.0
    states = []

    def status_read(line):
        if line.startswith(b'<'):
            assert tool._grbl_op is not None and tool._grbl_op.motion
            assert tool._grbl_reserve('other motion', motion=True) is None
            states.append(line)
            if len(states) == 2:
                fake.state = 'Idle'

    fake.on_line = status_read
    if entry == 'typed':
        tool.on_grbl_send_command()
    else:
        tool.on_grbl_jog('xplus')
    with patch('appPlugins.Common.grbl.grbl_client.time.sleep'):
        run_worker(tool)
    assert len(states) == 3
    assert states[-1].startswith(b'<Idle')
    assert tool._grbl_op is None
    assert tool._grbl_recovery is None


def test_failed_reset_does_not_relax_recovery():
    fake = SafetyGrbl()
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool._grbl_recovery = 'needs_reset'
    before = getattr(tool, '_grbl_safety_generation', 0)
    with patch.object(fake, 'write', side_effect=serial.SerialException('reset failed')):
        tool.on_grbl_reset()
    assert tool._grbl_recovery == 'needs_reset'
    assert tool._grbl_safety_generation > before


def test_alarm_queued_after_final_ok_blocks_home_success():
    fake = SafetyGrbl(replies={'$H': ['ok', 'ALARM:9']})
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool._grbl_recovery = 'needs_home'
    tool.on_grbl_homing()
    run_worker(tool)
    assert tool._grbl_recovery == 'needs_reset'
    assert not any('[success]' in c.args[0] for c in tool.app.inform.emit.call_args_list)


@pytest.mark.parametrize('raises', [False, True])
def test_motion_port_replacement_blocks_without_writing_replacement(raises):
    fake = SafetyGrbl()
    replacement = SafetyGrbl()
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    op = tool._grbl_reserve('motion', motion=True)

    def replace(line):
        tool.grbl_ser_port = replacement
        tool._grbl_connected = True
        if raises:
            raise serial.SerialException('old port failed')

    fake.on_line = replace
    assert tool._grbl_transact(op, 'G0 X1')[1] != 'ok'
    assert tool._grbl_recovery == 'needs_reset'
    assert replacement.commands() == []


class ResetConfirmationGrbl(SafetyGrbl):
    """Reset replies arrive during reads, independently of the subsequent $H reply."""
    BANNER = b"Grbl 1.1h ['$' for help]\r\n"

    def __init__(self, reset_lines=(), home_reply=True):
        super().__init__(replies={'G0': []})
        self.reset_lines = list(reset_lines)
        self.pending_reset_lines = []
        self.home_reply = home_reply
        self.banner_seen = False
        self.read_commands = []
        self.status_count = 0

    def _answer(self, cmd):
        if cmd == '\x18':
            self.pending_reset_lines = list(self.reset_lines)
            return
        if cmd == '$H':
            self.state = 'Run'
            if self.home_reply:
                self._send('ok')
            return
        if cmd == '?':
            self.status_count += 1
            self.state = 'Run' if self.status_count < 3 else 'Idle'
        super()._answer(cmd)

    def readline(self):
        line = self.pending_reset_lines.pop(0) if self.pending_reset_lines else FakeGrbl.readline(self)
        self.read_commands.append((line, list(self.commands())))
        if line == self.BANNER:
            self.banner_seen = True
        if self.on_line is not None:
            self.on_line(line)
        return line


def reset_confirmation_tool(fake):
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    old = tool._grbl_reserve('old motion', motion=True)
    # A real unacknowledged transaction leaves reply_pending and recovery evidence.
    assert tool._grbl_transact(old, 'G0 X1', timeout=0.0)[1] == 'timeout'
    tool.on_grbl_reset()
    tool._grbl_release(old)
    return tool


def run_reset_home(tool):
    ticks = iter(range(10000))
    with patch('appPlugins.Common.grbl.grbl_client.time.monotonic', side_effect=lambda: next(ticks)), \
            patch('appPlugins.Common.grbl.grbl_client.time.sleep'):
        if tool._grbl_op is not None and tool._grbl_op.name == 'reset':
            run_worker(tool)  # the reset worker reads the banner before Home
    tool.on_grbl_homing()
    with patch('appPlugins.Common.grbl.grbl_client.time.monotonic', side_effect=lambda: next(ticks)), \
            patch('appPlugins.Common.grbl.grbl_client.time.sleep'):
        run_worker(tool)


def test_delayed_old_ok_without_banner_cannot_acknowledge_home():
    fake = ResetConfirmationGrbl(reset_lines=[b'ok\r\n'], home_reply=False)
    tool = reset_confirmation_tool(fake)
    run_reset_home(tool)
    assert tool._grbl_recovery == 'needs_reset', 'A delayed old ok must not clear recovery while Run'
    assert '$H' not in fake.commands(), 'Home must not write motion before reset confirmation'
    assert tool._grbl_reply_pending
    assert not any('[success]' in c.args[0] for c in tool.app.inform.emit.call_args_list)


@pytest.mark.parametrize('reset_lines', [[], [b'ALARM:9\r\n']])
def test_alarm_or_missing_banner_sends_no_home(reset_lines):
    fake = ResetConfirmationGrbl(reset_lines=reset_lines, home_reply=False)
    tool = reset_confirmation_tool(fake)
    run_reset_home(tool)
    assert '$H' not in fake.commands()
    assert tool._grbl_recovery == 'needs_reset'
    assert not any('[success]' in c.args[0] for c in tool.app.inform.emit.call_args_list)


def test_home_ack_alone_does_not_clear_recovery_while_run():
    fake = ResetConfirmationGrbl()
    tool = make_audit_tool()
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    tool._grbl_recovery = 'needs_home'
    run_reset_home(tool)
    assert fake.state == 'Idle', 'Home acknowledgement alone is not fresh Idle confirmation'
    assert fake.status_count == 3
    assert tool._grbl_recovery is None


def fake_time(step=1.0):
    """Patches for a clock that advances `step` seconds per monotonic() call and a sleep that waits not."""
    ticks = iter(i * step for i in range(100000))
    return (patch('appPlugins.Common.grbl.grbl_client.time.monotonic', side_effect=lambda: next(ticks)),
            patch('appPlugins.Common.grbl.grbl_client.time.sleep'))


def status_replies(*states):
    return ['<%s|MPos:0.000,0.000,0.000|FS:0,0>' % state for state in states]


def wait_idle_result(tool, op, states, step=10.0):
    monotonic, sleep = fake_time(step)
    with monotonic, sleep, patch.object(tool, '_grbl_query_status', side_effect=status_replies(*states)):
        return tool._grbl_wait_idle(op)


def motion_tool():
    tool = make_audit_tool()
    tool.grbl_ser_port = SafetyGrbl()
    tool._grbl_connected = True
    return tool, tool._grbl_reserve('motion', motion=True)


def test_long_run_past_two_minutes_is_not_a_timeout():
    tool, op = motion_tool()
    assert wait_idle_result(tool, op, ['Run'] * 40 + ['Idle']) is True
    assert tool._grbl_recovery is None


def test_refused_probe_click_in_recovery_keeps_valid_map():
    tool = probe_tool(SafetyGrbl())
    tool.al_heights_valid = True
    tool._grbl_recovery = 'needs_reset'
    generation = tool._al_generation
    tool.on_grbl_autolevel()
    assert tool._al_generation == generation
    assert tool.al_heights_valid
    assert tool._grbl_op is None


