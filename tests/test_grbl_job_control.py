#!/usr/bin/env python
"""Check mode for 1 job, and the exit from it (A5.4, A5.5)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.test_levelling_audit import emitted
from tests.grbl_stream_helpers import make_stream_tool, make_levelled_stream_tool, ended_how, has_success, CheckFake


def fast_timeouts(tool):
    """The exit from check mode waits for the banner and the status: shorten the waits (< 1 s)."""
    real_confirm = tool._grbl_confirm_reset
    real_state = tool._grbl_post_reset_state
    tool._grbl_confirm_reset = lambda op: real_confirm(op, timeout=0.3)
    tool._grbl_post_reset_state = lambda op: real_state(op, timeout=0.3)


def record_exit(tool):
    """Record the results of _grbl_exit_check_mode()."""
    results = []
    real_exit = tool._grbl_exit_check_mode

    def exit_check_mode(op):
        results.append(real_exit(op))
        return results[-1]
    tool._grbl_exit_check_mode = exit_check_mode
    return results


def exit_tool(fake):
    """A tool with an operation that has just finished a validation in check mode."""
    tool = make_stream_tool(fake)
    fast_timeouts(tool)
    op = tool._grbl_reserve('stream', motion=True)
    tool._grbl_stream_dirty = True
    return tool, op


class TestExitCheckMode(unittest.TestCase):
    def test_exit_separate_replies(self):
        fake = CheckFake(in_check=True)
        tool, op = exit_tool(fake)
        self.assertEqual(tool._grbl_exit_check_mode(op), 'idle')
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(op.failed)
        tool._grbl_release(op)
        self.assertIsNotNone(tool._grbl_reserve('stream', motion=True))

    def test_exit_one_chunk(self):
        fake = CheckFake(in_check=True)
        fake.one_chunk = True
        tool, op = exit_tool(fake)
        self.assertEqual(tool._grbl_exit_check_mode(op), 'idle')
        self.assertIsNone(tool._grbl_recovery)
        tool._grbl_release(op)
        self.assertIsNotNone(tool._grbl_reserve('stream', motion=True))

    def test_exit_alarm(self):
        fake = CheckFake(in_check=True)
        fake.after_exit_state = 'Alarm'      # the real _grbl_post_reset_state reads <Alarm|...>
        tool, op = exit_tool(fake)
        self.assertEqual(tool._grbl_exit_check_mode(op), 'alarm')
        self.assertEqual(tool._grbl_recovery, 'needs_home')
        self.assertFalse(op.failed)
        tool._grbl_release(op)
        self.assertIsNone(tool._grbl_reserve('stream', motion=True))
        self.assertIsNotNone(tool._grbl_reserve('home', motion=True, allow_in_recovery=True))

    def test_exit_real_alarm_line(self):
        fake = CheckFake(in_check=True, replies={'?': ['ALARM:1']})
        tool, op = exit_tool(fake)
        self.assertEqual(tool._grbl_exit_check_mode(op), 'failed')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(op.failed)

    def test_exit_no_banner(self):
        fake = CheckFake()
        fake.exit_lines = ['[MSG:Disabled]', 'ok']
        tool = make_stream_tool(fake, check_mode=True)
        fast_timeouts(tool)
        results = record_exit(tool)
        tool.on_start()
        self.assertEqual(results, ['failed'])
        self.assertEqual(ended_how(tool), 'check_exit_failed')
        self.assertFalse(has_success(tool))
        self.assertIn('needs_reset', (tool._grbl_recovery,))

    def test_exit_no_status(self):
        fake = CheckFake()
        fake.mute_status_after_exit = True
        tool = make_stream_tool(fake, check_mode=True)
        fast_timeouts(tool)
        results = record_exit(tool)
        tool.on_start()
        self.assertEqual(results, ['failed'])
        self.assertEqual(ended_how(tool), 'check_exit_failed')
        self.assertFalse(has_success(tool))
        self.assertIsNotNone(tool._grbl_recovery)

    def test_exit_cancelled(self):
        fake = CheckFake()
        tool = make_stream_tool(fake, check_mode=True)
        fast_timeouts(tool)
        results = record_exit(tool)
        stop = {}

        def hook():
            # the user presses Stop while the worker waits for the banner
            if fake.exiting and 'generation' not in stop:
                stop['generation'] = tool._grbl_safety_generation + 1
                tool.on_grbl_stop()
        fake.read_hook = hook
        tool.on_start()
        self.assertEqual(results, ['failed'])
        self.assertNotEqual(ended_how(tool), 'check_passed')
        self.assertFalse(has_success(tool))
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertEqual(tool._grbl_safety_generation, stop['generation'])
        self.assertFalse(tool._grbl_reset_pending)
        self.assertTrue(tool._grbl_stream_dirty)


class TestCheckModeJob(unittest.TestCase):
    def test_check_passed(self):
        fake = CheckFake()
        tool = make_stream_tool(fake, check_mode=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'check_passed')
        self.assertTrue(any('[success]' in t and 'no error' in t for t in emitted(tool)))
        self.assertFalse(fake.in_check)
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertEqual(fake.commands().count('$C'), 2)         # enter and exit

    def test_check_passed_needs_home(self):
        fake = CheckFake()
        fake.after_exit_state = 'Alarm'
        tool = make_stream_tool(fake, check_mode=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'check_passed_needs_home')
        self.assertFalse(has_success(tool))
        self.assertTrue(any('[WARNING_NOTCL]' in t and 'Press Home' in t for t in emitted(tool)))
        self.assertEqual(tool._grbl_recovery, 'needs_home')

    def test_stop_during_check_stream(self):
        fake = CheckFake()
        tool = make_stream_tool(fake, check_mode=True)
        stop = {}

        def hook():
            if 'done' not in stop and sum(1 for t in fake.commands() if t.startswith('G1')) >= 3:
                stop['done'] = True
                tool.on_grbl_stop()
        fake.read_hook = hook
        tool.on_start()
        self.assertTrue(stop)
        self.assertEqual(ended_how(tool), 'stopped')
        commands = fake.commands()
        self.assertEqual(commands.count('$C'), 1)                 # no $C after the Stop
        self.assertIn(b'!', fake.written)
        self.assertTrue(fake.in_check)
        self.assertFalse(has_success(tool))
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)

    def test_check_errors_no_success(self):
        fake = CheckFake(replies={'G1 X01': ['error:20']})
        tool = make_stream_tool(fake, source='G1 X00.000 Y0.000 F100\nG1 X01.000 Y0.000 F100\nG1 X02.000 Y0.000 F100\n',
                                check_mode=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'check_failed')
        self.assertFalse(has_success(tool))
        self.assertEqual(tool._stream_result['errors'], [2])
        self.assertTrue(any('[ERROR_NOTCL]' in t and '2' in t for t in emitted(tool)))
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(fake.in_check)

    def test_enter_rejected_in_check(self):
        fake = CheckFake(in_check=True)
        tool = make_stream_tool(fake, check_mode=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'rejected')
        self.assertFalse([t for t in fake.commands() if t.startswith('G1')])
        self.assertNotIn('$C', fake.commands())
        self.assertTrue(any('GRBL is in check mode' in t for t in emitted(tool)))
        self.assertFalse(has_success(tool))
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_recovery)
        self.assertIsNone(tool._grbl_op)

    def test_levelled_check_order(self):
        # GRBL refuses $# in Check: a levelled check-mode Start must read the frame before $C
        class NoHashInCheck(CheckFake):
            def _answer(self, cmd):
                if cmd == '$#' and self.in_check:
                    self._send('error:8')
                    return
                super()._answer(cmd)

        fake = NoHashInCheck()
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        tool.on_start()
        commands = fake.commands()
        first_check = commands.index('$C')
        self.assertTrue([i for i, c in enumerate(commands) if c == '$#'])
        self.assertTrue(all(i < first_check for i, c in enumerate(commands) if c == '$#'))
        self.assertEqual(commands.count('$#'), 2)               # phase 1 and phase 2
        self.assertEqual(commands.count('$C'), 2)               # enter and exit
        self.assertEqual(len([c for c in commands if c.startswith('G01 X')]), 4)   # the job streamed
        self.assertEqual(ended_how(tool), 'check_passed')
        self.assertFalse(fake.in_check)


# ---- merged from test_grbl_stream_abort.py ----
"""The abort of a GRBL stream and the stream epoch (A5.2, A5.3)."""
import sys
import os

import unittest

import serial

from tests.test_levelling_audit import FakeGrbl, emitted
from tests.grbl_stream_helpers import make_stream_tool, ended_how, has_success, gcode


class RaisingGrbl(FakeGrbl):
    """The port raises a SerialException from the `fail_at`-th write on."""

    def __init__(self, fail_at, **kwargs):
        super().__init__(**kwargs)
        self.fail_at = fail_at
        self.count = 0

    def write(self, data):
        self.count += 1
        if self.count >= self.fail_at:
            raise serial.SerialException('gone')
        super().write(data)


def job_lines_written(fake):
    return [text for text in fake.commands() if text.startswith('G1')]


class TestStreamAbort(unittest.TestCase):
    def _error_run(self):
        # line 0 of 10 gets error:20; 5 lines (23 bytes each) are in flight
        fake = FakeGrbl(replies={'G1 X00': ['error:20']})
        tool = make_stream_tool(fake)
        tool.on_start()
        return tool, fake

    def test_normal_job_finishes(self):
        fake = FakeGrbl()
        tool = make_stream_tool(fake)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'finished')
        self.assertEqual(len(job_lines_written(fake)), 10)
        self.assertTrue(has_success(tool))
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_recovery)
        self.assertIsNone(tool._grbl_op)
        self.assertIsNotNone(tool._grbl_reserve('stream', motion=True))

    def test_error_latches(self):
        tool, fake = self._error_run()
        self.assertEqual(ended_how(tool), 'error')
        self.assertEqual(len(job_lines_written(fake)), 5)
        self.assertIn(b'!', fake.written)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_op)
        self.assertIsNone(tool._grbl_reserve('stream', motion=True))
        self.assertFalse(has_success(tool))

    def test_port_exception_latches(self):
        # write 1 is the status query, writes 2 and 3 are job lines, write 4 raises
        fake = RaisingGrbl(4)
        tool = make_stream_tool(fake)
        at_release = {}
        real_release = tool._grbl_release

        def release(op):
            at_release['recovery'] = tool._grbl_recovery
            at_release['failed'] = op.failed
            real_release(op)
        tool._grbl_release = release
        tool.on_start()
        self.assertEqual(ended_how(tool), 'port_error')
        self.assertEqual(at_release, {'recovery': 'needs_reset', 'failed': True})
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertIsNone(tool._grbl_reserve('stream', motion=True))
        self.assertFalse(has_success(tool))

    def test_old_ok_not_consumed(self):
        tool, fake = self._error_run()
        self.assertGreaterEqual(len(fake.rx), 3)       # the ok of the lines in flight are still there
        before = list(fake.written)
        self.assertIsNone(tool._grbl_reserve('param'))
        self.assertEqual(fake.written, before)
        self.assertTrue(any('lines of the stopped job' in text for text in emitted(tool)))

    def test_reset_clears_dirty(self):
        tool, fake = self._error_run()
        self.assertTrue(tool._grbl_stream_dirty)
        tool.on_grbl_reset()
        # the reset worker (inline here) reads the banner that the fake sends for the reset byte
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertFalse(tool._grbl_reset_pending)
        self.assertEqual(tool._grbl_recovery, 'needs_home')

    def test_reset_during_final_wait_kept(self):
        fake = FakeGrbl()
        tool = make_stream_tool(fake)
        state = {'all_acknowledged': False, 'reset': False}
        tool.stream_progress_sig.emit.side_effect = lambda done, total: state.update(
            all_acknowledged=(done == total))
        real_query = tool._grbl_query_status

        def query(op, *args, **kwargs):
            # the final wait for Idle: the user presses Reset here
            if state['all_acknowledged'] and not state['reset']:
                state['reset'] = True
                state['generation'] = tool._grbl_safety_generation + 1
                tool.on_grbl_reset()
            return real_query(op, *args, **kwargs)
        tool._grbl_query_status = query
        tool.on_start()
        self.assertTrue(state['reset'])
        self.assertEqual(ended_how(tool), 'stopped')
        self.assertTrue(tool._grbl_reset_pending)
        self.assertEqual(tool._grbl_safety_generation, state['generation'])
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertFalse(has_success(tool))


# ---- merged from test_grbl_restart.py ----
"""Restart uses the preceding program position, never the selected move's endpoint."""
import os
import sys

import unittest
from unittest.mock import MagicMock

from appPlugins.ToolGcodeSender import SenderGen


PROGRAM = ['G21', 'G90', 'G17', 'G54', 'F100', 'M3 S12000', 'G0 Z3',
           'G0 X1 Y2', 'G1 Z-0.2', 'G2 X5 Y2 I2 J0', 'M5', 'M2']


class TestRestartPreamble(unittest.TestCase):

    def preamble(self, lines=PROGRAM, start=10, travel=3, units='MM'):
        return SenderGen.restart_preamble(lines, start, travel, units)[0]

    def test_clearance_xy_spindle_plunge_then_restore_arc_mode(self):
        result = self.preamble()
        lift = result.index('G0 Z3.0000')
        xy = result.index('G0 X1.0000 Y2.0000')
        spindle = result.index('M3 S12000.0000')
        plunge = result.index('G1 Z-0.2000 F100.0000')
        self.assertLess(lift, xy)
        self.assertLess(xy, spindle)
        self.assertLess(spindle, plunge)
        self.assertEqual(result[-1], 'G1')
        self.assertNotIn(PROGRAM[9], result)
        self.assertNotIn('M2', result)

    def test_incremental_and_implicit_moves_reconstruct_the_preceding_position(self):
        lines = PROGRAM[:9] + ['G91', 'G1 X2 Y3 Z-0.1', 'X1', 'X2']
        result = self.preamble(lines, len(lines))
        self.assertIn('G0 X4.0000 Y5.0000', result)
        self.assertIn('G1 Z-0.3000 F100.0000', result)
        self.assertEqual(result[-2:], ['G91', 'G1'])

    def test_modal_arc_is_restored_after_the_synthetic_linear_plunge(self):
        result = self.preamble(PROGRAM[:10] + ['X1 Y2 I-2 J0'], 11)
        self.assertIn('G0 X5.0000 Y2.0000', result)
        self.assertNotIn('G2', result)

    def test_beginning_does_not_require_a_position_or_travel_height(self):
        self.assertEqual(self.preamble(['G1 X1'], 1, None), [])

    def test_unknown_position_and_side_effects_are_refused(self):
        cases = [(['G21', 'G90', 'G1 X1', 'G1 X2'], 4),
                 (PROGRAM[:9] + ['G92 X0', 'G1 X2'], 11),
                 (PROGRAM[:9] + ['G38.2 Z-3 F50', 'G1 X2'], 11),
                 (PROGRAM[:9] + ['M6 T2', 'G1 X2'], 11),
                 (PROGRAM[:9] + ['M2', 'G1 X2'], 11),
                 (PROGRAM[:9] + ['$H', 'G1 X2'], 11)]
        for lines, start in cases:
            with self.subTest(lines=lines):
                with self.assertRaises(ValueError):
                    self.preamble(lines, start)

    def test_invalid_range_height_and_nonfinite_values_are_refused(self):
        for start, travel in ((0, 3), (13, 3), (10, float('nan')), (10, -1)):
            with self.subTest(start=start, travel=travel):
                with self.assertRaises(ValueError):
                    self.preamble(start=start, travel=travel)

    def test_spindle_and_coolant_use_final_state_without_replaying_stops(self):
        lines = PROGRAM[:9] + ['M5', 'M3 S9000', 'M8', 'M9', 'M7', 'G1 X2']
        result = self.preamble(lines, len(lines))
        self.assertEqual([s for s in result if s.startswith('M3')], ['M3 S9000.0000'])
        self.assertNotIn('M8', result)
        self.assertIn('M7', result)

    def test_units_change_converts_travel_height_and_modal_feed(self):
        lines = PROGRAM[:9] + ['G20', 'G0 X1 Y2 Z0.1', 'G1 X2']
        result = self.preamble(lines, len(lines), 25.4)
        self.assertIn('G20', result)
        self.assertIn('G0 Z1.0000', result)
        self.assertIn('G1 Z0.1000 F3.9370', result)

    def test_undeclared_work_frame_and_tool_offset_are_left_intact(self):
        lines = [s for s in PROGRAM if s != 'G54']
        result = self.preamble(lines, 9)
        self.assertNotIn('G54', result)
        self.assertNotIn('G49', result)

    def test_tool_length_offset_is_converted_to_the_active_units(self):
        lines = PROGRAM[:9] + ['G43.1 Z25.4', 'G20', 'G0 X1 Y2 Z0.1', 'G1 X2']
        result = self.preamble(lines, len(lines), 25.4)
        self.assertIn('G43.1 Z1.0000', result)

    def test_first_cut_can_supply_its_own_feed_when_no_reentry_plunge_is_needed(self):
        lines = ['G21', 'G90', 'M3 S12000', 'G0 X1 Y2 Z3', 'G1 Z-0.2 F100']
        result = self.preamble(lines, len(lines))
        self.assertIn('G0 X1.0000 Y2.0000', result)
        self.assertFalse(any(s.startswith('G1 Z') for s in result))
        self.assertEqual(result[-1], 'G0')


class TestStartDialog(unittest.TestCase):


    def test_ok_and_cancel_return_the_selected_line_or_none(self):
        from PyQt6 import QtCore, QtWidgets
        from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender
        from tests.grbl_stream_helpers import CheckFake, make_stream_tool
        tool = make_stream_tool(CheckFake())
        tool.app.ui = QtWidgets.QMainWindow()
        self.addCleanup(tool.app.ui.deleteLater)
        for choice, expected in (('beginning', 1), ('line', 7), ('cancel', None)):
            def finish(choice=choice):
                dialog = QtWidgets.QApplication.activeModalWidget()
                if choice == 'cancel':
                    dialog.reject()
                else:
                    dialog.start_radio.set_value(choice)
                    dialog.line_entry.set_value(7)
                    dialog.accept()

            QtCore.QTimer.singleShot(0, finish)
            self.assertEqual(ToolGCodeSender._choose_start_line(tool, 10), expected)


class TestSenderRestart(unittest.TestCase):

    def make_tool(self, start=10, check_mode=False):
        from tests.grbl_stream_helpers import CheckFake, make_stream_tool
        fake = CheckFake()
        tool = make_stream_tool(fake, '\n'.join(PROGRAM), check_mode=check_mode)
        tool.app.options['tools_grbl_arc_mode'] = 'send'
        tool._target().z_move = 3
        tool._choose_start_line = MagicMock(return_value=start)
        return tool, fake

    def test_restart_streams_preamble_and_selected_arc_with_original_console_numbering(self):
        from PyQt6 import QtWidgets
        from appPlugins.ToolGcodeSender.Sender import GrblConsole
        tool, fake = self.make_tool()
        dock = QtWidgets.QDockWidget()
        console = GrblConsole(tool)
        dock.setWidget(console)
        self.addCleanup(dock.deleteLater)
        tool.app.ui.grbl_console_dock = dock
        tool.stream_progress_sig.emit.side_effect = tool._on_stream_progress
        tool.on_start()
        commands = fake.commands()
        self.assertNotIn('G0 X1 Y2', commands)
        self.assertIn('G0 X1.0000 Y2.0000', commands)
        self.assertLess(commands.index('G1 Z-0.2000 F100.0000'), commands.index(PROGRAM[9]))
        self.assertEqual(commands.count(PROGRAM[9]), 1)
        self.assertEqual(console.program_view.toPlainText().splitlines(), PROGRAM)
        self.assertEqual(tool._stream_result['done'], len(PROGRAM))
        self.assertEqual(console._acknowledged, len(PROGRAM))

    def test_cancel_releases_the_reserved_operation_without_writing(self):
        tool, fake = self.make_tool(None)
        tool.on_start()
        self.assertEqual(fake.written, [])
        self.assertIsNone(tool._grbl_op)
        tool.app.worker_task.emit.assert_not_called()

    def test_source_edit_while_dialog_is_open_drops_the_request(self):
        tool, fake = self.make_tool()

        def choose(count):
            tool._target().source_file += '\nG0 X99'
            return 10

        tool._choose_start_line.side_effect = choose
        tool.on_start()
        self.assertEqual(fake.written, [])
        self.assertIsNone(tool._grbl_op)

    def test_unknown_position_refuses_restart_without_writing(self):
        tool, fake = self.make_tool(3)
        tool.on_start()
        self.assertEqual(fake.written, [])
        self.assertIsNone(tool._grbl_op)

    def test_cancel_precedes_automatic_probing(self):
        tool, fake = self.make_tool(None)
        tool.ui.autolevel_cb.get_value.return_value = True
        tool.al_heights_valid = False
        tool._queue_probe = MagicMock()
        tool.on_start()
        tool._queue_probe.assert_not_called()
        self.assertEqual(fake.written, [])

    def test_restart_error_highlights_the_selected_console_row(self):
        tool, fake = self.make_tool()
        fake.replies['G2 X'] = ['error:20']
        tool.on_start()
        self.assertEqual(tool._stream_result['error_indices'], [9])
        self.assertEqual(tool._stream_result['line'], 10)

    def test_arc_mode_is_attached_to_the_first_inherited_move_not_sent_alone(self):
        from tests.grbl_stream_helpers import ended_how
        for tail, expected in ((['X1 Y2 I-2 J0'], 'G2 X1 Y2 I-2 J0'),
                               (['F80', 'X1 Y2 I-2 J0'], 'G2 X1 Y2 I-2 J0'),
                               (['F80', 'G1 X8', 'X9'], 'G1 X8')):
            with self.subTest(tail=tail):
                tool, fake = self.make_tool(11)
                tool._target().source_file = '\n'.join(PROGRAM[:10] + tail + ['M2'])
                answer = fake._answer

                def grbl_answer(command, answer=answer):
                    if command in ('G2', 'G3'):
                        fake._send('error:26')
                    else:
                        answer(command)

                fake._answer = grbl_answer
                tool.on_start()
                self.assertEqual(ended_how(tool), 'finished')
                self.assertIn(expected, fake.commands())
                self.assertNotIn('G2', fake.commands())

    def test_check_mode_checks_the_restart_sequence(self):
        from tests.grbl_stream_helpers import ended_how
        tool, fake = self.make_tool(check_mode=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'check_passed')
        commands = fake.commands()
        self.assertIn('G0 X1.0000 Y2.0000', commands)
        self.assertLess(commands.index('$C'), commands.index('G0 Z3.0000'))

    def test_levelled_restart_uses_the_preceding_compensated_z(self):
        from tests.grbl_stream_helpers import CheckFake, make_levelled_stream_tool, ended_how
        tool, target = make_levelled_stream_tool(CheckFake())
        target.z_move = 3
        tool._choose_start_line = MagicMock(return_value=10)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'finished')
        expected, _resumed = SenderGen.restart_preamble(tool.job_lines, 10, 3, target.units)
        commands = tool.grbl_ser_port.commands()
        self.assertIn(next(s for s in expected if s.startswith('G1 Z')), commands)
        self.assertNotIn(tool.job_lines[7], commands)


class ToolChangeFake(CheckFake):
    """A grblHAL at M6: the M6 waits in the Tool state for the 0xA3 acknowledge, then for a Cycle Start (~)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.acknowledged = False
        self.tool_waiting = False

    def write(self, data):
        self.written.append(data)
        if data == b'\xa3':
            self.acknowledged = True
            return
        self._answer(data.decode('utf-8', errors='replace').strip())

    def _answer(self, cmd):
        if cmd == 'M6':
            # the M6 line is not acknowledged until the tool change is done
            self.tool_waiting = True
            self.state = 'Tool'
            return
        if cmd == '~' and self.tool_waiting:
            self.tool_waiting = False
            self.state = 'Idle'
            self._send('ok')
            return
        super()._answer(cmd)


class HighBitRealtimeFake(CheckFake):
    """An RT- grblHAL: the printable real-time commands are ignored, only the top-bit-set forms answer."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.unknown = []

    def write(self, data):
        self.written.append(data)
        if data == b'\x80':
            self._send('<Idle|MPos:0.000,0.000,0.000|FS:0,0>')
            return
        if data in (b'?', b'!', b'~'):
            self.unknown.append(data)
            return
        self._answer(data.decode('utf-8', errors='replace').strip())


class TestGrblHalController(unittest.TestCase):
    def test_automatic_tool_wait_refuses_operator_commands_without_claiming_touch_off(self):
        fake = CheckFake()
        tool = make_stream_tool(fake, source='M6\n')
        tool._grbl_caps = {'automatic_tool_change': True}
        answer = fake._answer
        polls = []
        attempted = []

        def automatic(command):
            if command == 'M6':
                fake.state = 'Tool'
            elif command == '?' and fake.state == 'Tool':
                polls.append(True)
                if len(polls) < 4:
                    fake._send('<Tool|MPos:0,0,0>')
                else:
                    fake.state = 'Idle'
                    fake._send('<Idle|MPos:0,0,0>')
                    fake._send('ok')
            else:
                answer(command)

        def operator():
            if tool.__dict__.get('_grbl_tool_change_pending') and not attempted:
                attempted.append(True)
                tool.ui.grbl_command_entry.get_value.return_value = '$J=G91 X1 F100'
                tool.on_grbl_send_command()

        fake._answer = automatic
        fake.read_hook = operator
        tool.on_start()
        self.assertTrue(attempted)
        self.assertTrue(any('unavailable' in text.lower() for text in emitted(tool)))
        self.assertEqual(ended_how(tool), 'finished')
        self.assertNotIn(b'$J=G91 X1 F100\n', fake.written)
        self.assertNotIn(b'\xa3', fake.written)
        self.assertFalse(any('jog/touch off' in text for text in emitted(tool)))

    def test_external_sd_and_mpg_preflight_refuse_even_when_idle(self):
        for field in ('SD:Pending', 'SD:10.0,job.nc', 'MPG:1'):
            with self.subTest(field=field):
                fake = CheckFake(replies={'?': ['<Idle|MPos:0,0,0|%s>' % field]})
                tool = make_stream_tool(fake, source='G1 X1\n')
                tool.on_start()
                self.assertNotIn(b'G1 X1\n', fake.written)
                self.assertTrue(any('SD' in text or 'MPG' in text for text in emitted(tool)))

    def test_m6_waits_for_the_acknowledge_and_a_cycle_start(self):
        fake = ToolChangeFake()
        tool = make_stream_tool(fake, source='G21\nM6\nG1 X1.000 Y1.000\n')

        def operator():
            if (fake.tool_waiting and tool.__dict__.get('_grbl_tool_change_pending') and
                    not tool.__dict__.get('_grbl_tool_command_busy')):
                tool._on_resume_button()
        fake.read_hook = operator
        tool.on_start()

        self.assertEqual(ended_how(tool), 'finished')
        written = fake.written
        ack, start, motion = written.index(b'\xa3'), written.index(b'~'), written.index(b'G1 X1.000 Y1.000\n')
        self.assertLess(written.index(b'M6\n'), ack)
        # no job line is written while the controller waits for the new tool
        self.assertTrue(all(line in (b'?', b'$G\n') for line in written[ack + 1:start]), written)
        self.assertEqual(written[ack + 1:start].count(b'$G\n'), 1)
        self.assertLess(written.index(b'$$\n'), written.index(b'M6\n'))
        self.assertLess(start, motion)
        self.assertTrue(any('Cycle Start' in text for text in emitted(tool)))

    def test_adjacent_manual_m6_requires_a_fresh_second_reply_fence(self):
        for fail_second in (False, True):
            with self.subTest(fail_second=fail_second):
                fake = ToolChangeFake()
                tool = make_stream_tool(fake, source='M6\nM6\nG1 X1\n')
                answer = fake._answer
                premature = []
                stages = []

                def controller(command):
                    if command == '$G' and fake.written.count(b'\xa3') == 2 and fail_second:
                        fake._send('error:20')
                    else:
                        answer(command)

                def try_resume():
                    before = len(fake.written)
                    tool._on_resume_button()
                    tool.on_grbl_pause_resume(False)
                    premature.extend(fake.written[before:])

                def operator():
                    if fake.commands().count('M6') == 2 and fake.written.count(b'\xa3') < 2:
                        return  # let the second Tool report reach the stream reader first
                    if (fake.tool_waiting and fake.written.count(b'\xa3') == 2 and
                            len(stages) < (1 if fail_second else 2)):
                        # Pause before the second fence's [GC:] marker and terminal ok.
                        stages.append(len(stages))
                        try_resume()
                    elif fake.tool_waiting and tool.__dict__.get('_grbl_tool_change_pending'):
                        tool._on_resume_button()

                def reply(text):
                    if text == 'error:20':
                        try_resume()  # the caller hasn't latched recovery yet

                fake._answer = controller
                fake.read_hook = operator
                tool.console_line_sig.emit.side_effect = reply
                tool.on_start()
                self.assertNotIn(b'~', premature)
                self.assertEqual(len(stages), 1 if fail_second else 2)
                self.assertEqual(fake.written.count(b'\xa3'), 2)
                self.assertEqual(fake.written.count(b'~'), 1 if fail_second else 2)
                if fail_second:
                    self.assertEqual(ended_how(tool), 'error')
                    self.assertEqual(tool._grbl_recovery, 'needs_reset')
                    self.assertNotIn(b'G1 X1\n', fake.written)
                else:
                    self.assertEqual(ended_how(tool), 'finished')
                    second_start = len(fake.written) - 1 - fake.written[::-1].index(b'~')
                    self.assertLess(second_start, fake.written.index(b'G1 X1\n'))

    def test_manual_m6_fence_and_temporary_errors_never_release_resume(self):
        for command in ('$G', 'G49'):
            with self.subTest(command=command):
                fake = ToolChangeFake(replies={command: ['error:20']})
                tool = make_stream_tool(fake, source='M6\nG1 X1\n')
                attempted = []
                errors = []

                def reply(text):
                    if text == 'error:20':
                        errors.append(text)
                        # The reader has the error, but its caller hasn't latched recovery yet.
                        tool._on_resume_button()
                        tool.on_grbl_pause_resume(False)

                def operator():
                    if fake.acknowledged:
                        if tool.__dict__.get('_grbl_tool_change_pending') and not attempted:
                            attempted.append(tool._grbl_queue_tool_command('G49'))
                        tool._on_resume_button()
                        tool.on_grbl_pause_resume(False)

                fake.read_hook = operator
                tool.console_line_sig.emit.side_effect = reply
                tool.on_start()
                tool._on_resume_button()
                self.assertNotIn(b'~', fake.written)
                self.assertNotIn(b'G1 X1\n', fake.written)
                self.assertEqual(ended_how(tool), 'error')
                self.assertEqual(tool._grbl_recovery, 'needs_reset')
                self.assertEqual(b'G49\n' in fake.written, command == 'G49')
                self.assertTrue(errors)

    def test_healthy_hold_resumes_through_both_sender_controls(self):
        fake = CheckFake()
        tool = make_stream_tool(fake)
        op = tool._grbl_reserve('stream', motion=True)
        self.assertIsNotNone(op)
        tool._grbl_stream_dirty = True
        fake.state = 'Hold'
        tool._on_resume_button()
        tool._grbl_release(op)
        tool._grbl_stream_dirty = False
        tool.on_grbl_pause_resume(False)
        self.assertEqual(fake.written, [b'~', b'~'])

    def test_rt_minus_uses_the_top_bit_realtime_commands(self):
        fake = HighBitRealtimeFake()
        tool = make_stream_tool(fake)
        tool._grbl_top_bit_realtime = True
        op = tool._grbl_reserve('frame')
        self.assertIsNotNone(op)
        line = tool._grbl_query_status(op, timeout=0.2)
        tool._grbl_realtime(b'!')
        tool._grbl_release(op)

        self.assertTrue(line and line.startswith('<Idle'))
        self.assertIn(b'\x80', fake.written)
        self.assertIn(b'\x82', fake.written)
        self.assertEqual(fake.unknown, [])

    def test_a_four_axis_grblhal_is_refused(self):
        fake = CheckFake(replies={'$I+': ['[OPT:VN,35,128,4,0]', '[AXS:4:XYZU]', '[NEWOPT:RT+,TC]',
                                         '[FIRMWARE:grblHAL]', 'ok']})
        tool = make_stream_tool(fake)
        tool._grbl_grblhal = True
        op = tool._grbl_reserve('units')
        tool._grbl_read_after_connect(op)
        tool._grbl_release(op)

        self.assertTrue(tool._grbl_motion_blocked)
        self.assertTrue(any('X, Y and Z' in text for text in emitted(tool)))
        self.assertIsNone(tool._grbl_reserve('stream', motion=True))
        self.assertIsNotNone(tool._grbl_reserve('status', motion=False))


if __name__ == '__main__':
    unittest.main()
