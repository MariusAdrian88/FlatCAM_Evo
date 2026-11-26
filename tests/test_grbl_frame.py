#!/usr/bin/env python
"""
Tests of the frame bound height map and of the levelled job of the GRBL Sender:
the frame in the probe result, the hook that binds it, the home WCS and the strict mode of
the levelling, the tool length offset check and the levelled job (T16a); the 2 phases of a
levelled Start, the start request and its invalidation events (T16b, A8.3 and A8.4).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtWidgets

from test_levelling_audit import make_audit_tool
from test_levelling_tool import make_tool
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender, _StartRequest
from appPlugins.ToolGcodeSender import SenderGen
import appPlugins.ToolGcodeSender.Sender  # noqa: F401
from tests.test_levelling_audit import emitted
from tests.test_grbl_job_control import fast_timeouts
from tests.grbl_stream_helpers import (
    FRAME, JOB, make_storage, make_job_target, make_stream_tool, make_levelled_stream_tool, ended_how, CheckFake,
    HookFake, spy_reserve, import_map_b)

# appPlugins/__init__.py shadows the package name with the class, so take the module from sys.modules
sender_module = sys.modules['appPlugins.ToolGcodeSender.Sender']


def make_sender(text, frame=FRAME, method='b', segmented=True):
    target = make_job_target(text, segmented)
    tool = make_audit_tool(units='MM', storage=make_storage(), heights_valid=True, cls=ToolGCodeSender)
    tool._al_map_frame = frame
    tool._al_map_imported = frame is None
    tool.ui.autolevel_cb.get_value.return_value = True
    tool.ui.al_method_radio.get_value.return_value = method
    tool.ui.object_combo.get_value.return_value = 'job'
    tool.app.collection.get_by_name.return_value = target
    tool.app.options = {}
    return tool, target


def messages(tool, tag):
    return [call.args[0] for call in tool.app.inform.emit.call_args_list if tag in call.args[0]]


class TestBoundFrameHook(unittest.TestCase):
    def test_probe_result_binds_the_frame(self):
        tool, target = make_sender(JOB, frame=None)
        frame = ('G55', (1.0, 2.0, 3.0), True)
        tool._al_on_height_map_ready(target, {'kind': 'probe', 'frame': frame})
        self.assertEqual(tool._al_map_frame, frame)
        self.assertFalse(tool._al_map_imported)

    def test_import_clears_the_frame(self):
        for kind in ('import', 'import_points'):
            tool, target = make_sender(JOB)
            tool._al_on_height_map_ready(target, {'kind': kind})
            self.assertIsNone(tool._al_map_frame)
            self.assertTrue(tool._al_map_imported)

    def test_clear_probe_data(self):
        tool, _target = make_sender(JOB)
        tool._al_map_imported = True
        tool._clear_probe_data()
        self.assertIsNone(tool._al_map_frame)
        self.assertFalse(tool._al_map_imported)

    def test_on_al_result_ready_passes_the_result(self):
        # the whole path: the result of the probe reaches the hook with its frame
        tool, _target = make_sender(JOB, frame=None)
        ctx = tool._al_capture_context()
        heights = {key: 0.1 for key in tool.al_voronoi_geo_storage}
        frame = ('G56', (0.5, 0.5, 0.5), False)
        tool._on_al_result_ready({'kind': 'probe', 'context': ctx, 'heights': heights, 'frame': frame})
        self.assertEqual(tool._al_map_frame, frame)
        self.assertFalse(tool._al_map_imported)

    def test_tool_levelling_hook_ignores_the_result(self):
        tool = make_tool(units='MM', storage=make_storage())
        tool.apply_autolevel = MagicMock()
        target = object()
        tool._al_on_height_map_ready(target, {'kind': 'probe', 'frame': FRAME})
        tool.apply_autolevel.assert_called_once_with(target_obj=target)


class TestBuildLevelledJobFrame(unittest.TestCase):
    def test_same_frame_builds(self):
        tool, _target = make_sender("G90 G21 G54\nG0 X0 Y0 Z5\nG1 Z0 F100\nG1 X10 Y0\n")
        self.assertTrue(tool.build_levelled_job())
        self.assertEqual(messages(tool, '[ERROR_NOTCL]'), [])
        self.assertEqual(tool.job_map_generation, tool._al_generation)

    def test_g54_map_with_g55_header_refused(self):
        tool, _target = make_sender("G90 G21 G55\nG0 X0 Y0 Z5\nG1 Z0 F100\nG1 X10 Y0\n")
        self.assertFalse(tool.build_levelled_job())
        self.assertEqual(len(messages(tool, '[ERROR_NOTCL]')), 1)

    def test_g92_in_the_middle_refused(self):
        tool, _target = make_sender(
            "G90 G21 G54\nG0 X0 Y0 Z5\nG1 Z0 F100\nG1 X5 Y0\nG92 X0 Y0\nG1 X10 Y0\nG1 X10 Y5\n")
        self.assertFalse(tool.build_levelled_job())
        self.assertEqual(len(messages(tool, '[ERROR_NOTCL]')), 1)

    def test_unbound_map_does_not_guess_the_wcs(self):
        # an imported map that is not bound yet: the first WCS word of the program declares it
        tool, _target = make_sender("G90 G21 G55\nG0 X0 Y0 Z5\nG1 Z0 F100\nG1 X10 Y0\n", frame=None)
        self.assertTrue(tool.build_levelled_job())
        self.assertEqual(messages(tool, '[ERROR_NOTCL]'), [])


class TestStrictMode(unittest.TestCase):
    def test_unsplit_arc_refused(self):
        tool, _target = make_sender("G90 G21 G54\nG18\nG0 X0 Y0 Z0\nG2 X10 Z0 I5 K0\n")
        self.assertFalse(tool.build_levelled_job())
        self.assertEqual(len(messages(tool, '[ERROR_NOTCL]')), 1)

    def test_relative_move_refused(self):
        tool, _target = make_sender("G90 G21 G54\nG91\nG1 X5 Y0 Z-1 F100\n")
        self.assertFalse(tool.build_levelled_job())
        self.assertEqual(len(messages(tool, '[ERROR_NOTCL]')), 1)
        # an A word: the height map has no rotary axis
        tool, _target = make_sender("G90 G21 G54\nG0 X0 Y0 Z5\nG1 X5 Y0 Z-1 A90 F100\n")
        self.assertFalse(tool.build_levelled_job())
        errors = messages(tool, '[ERROR_NOTCL]')
        self.assertEqual(len(errors), 1)
        self.assertIn('A axis', errors[0])

    def test_tool_levelling_stays_lenient(self):
        # without the strict keyword, the same program only warns (a relative move at an unknown position)
        tool = make_tool(units='MM', storage=make_storage())
        target = make_job_target("G90 G21\nG91\nG1 X5 Y0 Z-1 F100\n")
        self.assertIsNotNone(tool.autolevell_gcode(target, 'b'))
        self.assertEqual(messages(tool, '[ERROR_NOTCL]'), [])
        self.assertEqual(len(messages(tool, '[WARNING_NOTCL]')), 1)


class TestTloChangeRefused(unittest.TestCase):
    def check(self, tlo_line):
        # a map probed with a TLO of 1.5; the program changes the tool length offset
        frame = ('G54', (0.0, 0.0, -1.5), False)
        text = "G90 G21 G54\nG0 X0 Y0 Z5\n%s\nG0 X0 Y0 Z5\nG1 X10 Y0 Z0\n" % tlo_line
        tool, _target = make_sender(text, frame=frame)
        self.assertFalse(tool.build_levelled_job())
        errors = messages(tool, '[ERROR_NOTCL]')
        self.assertEqual(len(errors), 1)
        self.assertIn('tool length offset', errors[0])
        self.assertIn('line 3', errors[0])

    def test_tlo_change_refused(self):
        self.check('G49')
        self.check('G43.1 Z2')

    def test_g43_refused(self):
        self.check('G43 H1')


class TestNoNewKeywords(unittest.TestCase):
    # computed once with the code at commit 7be1b53c (before the new keywords)
    EXPECTED = (
        "(FlatCAM Gcode Generator)\n"
        "G21\n"
        "G90\n"
        "G94\n"
        "F300.0000\n"
        "M03 S10000\n"
        "G00 Z2.0000 (start)\n"
        "G00 X1.0000 Y1.0000\n"
        "G01 Z-0.0680 F300.0000\n"
        "G01 X3.0000 Y1.0000 Z-0.0440\n"
        "G01 X5.0000 Y2.0000 Z0.0100\n"
        "G01 X7.0000 Y4.0000 Z0.0740\n"
        "G01 X9.0000 Y6.0000 Z0.1180\n"
        "G00 Z2.0000\n"
        "G00 X0.0000 Y0.0000\n"
        "M05\n"
        "M2\n"
    )

    def test_autolevell_gcode_without_keywords_unchanged(self):
        tool = make_tool(units='MM', storage=make_storage())
        result = tool.autolevell_gcode(make_job_target(JOB), 'b')
        self.assertEqual(result[1], self.EXPECTED)


class TestSenderMatchesLevellingTool(unittest.TestCase):
    def check(self, method):
        # The sender cleans the comments and the empty lines; ToolLevelling keeps them. After the
        # same cleaning every line (motion and modal) is the same.
        tool_lvl = make_tool(units='MM', storage=make_storage())
        expected_text = tool_lvl.autolevell_gcode(make_job_target(JOB), method)[1]
        expected = SenderGen.clean_lines(expected_text)

        sender, _target = make_sender(JOB, method=method)
        self.assertTrue(sender.build_levelled_job())
        self.assertEqual(sender.job_lines, expected)
        # the levelling really changed the cutting moves
        self.assertNotEqual(sender.job_lines, SenderGen.clean_lines(JOB))
        cutting = [line for line in sender.job_lines if line.startswith('G01 X')]
        self.assertEqual(len(cutting), 4)
        self.assertTrue(all('Z' in line for line in cutting))

    def test_sender_levelled_job_matches_levelling_tool(self):
        self.check('b')
        self.check('v')


class TestStreamJobArcs(unittest.TestCase):
    @staticmethod
    def make(text):
        tool, _target = make_sender(text)
        tool.ui.autolevel_cb.get_value.return_value = False
        return tool

    def test_arc_split_keeps_the_line_numbers(self):
        tool = self.make("(c)\nG21 G90\nG0 X0 Y0 Z0\nG2 X10 Y0 I5 J0 F100\nG0 Z5\n")
        self.assertTrue(tool.build_stream_job())
        self.assertGreater(len(tool.job_lines), 5)
        self.assertEqual(len(tool.job_line_numbers), len(tool.job_lines))
        self.assertEqual(tool.job_line_numbers[0], 2)
        self.assertEqual(tool.job_line_numbers[-1], 5)
        self.assertEqual(tool.job_lines[-1], 'G0 Z5')

    def test_unsplit_arc_refused(self):
        tool = self.make("(c)\nG18\nG0 X0 Y0 Z0\nG2 X10 Z0 I5 K0\n")
        self.assertFalse(tool.build_stream_job())
        errors = messages(tool, '[ERROR_NOTCL]')
        self.assertEqual(len(errors), 1)
        self.assertIn('Line 4', errors[0])


def run_limited(case, fn, limit=2.0):
    """Run `fn` in a thread with a time limit: a deadlock fails the test instead of hanging it."""
    errors = []

    def target():
        try:
            fn()
        except BaseException as err:        # noqa: B036 - reported to the test thread
            errors.append(err)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(limit)
    case.assertFalse(thread.is_alive(), 'deadlock: the event did not end in %s s' % limit)
    if errors:
        raise errors[0]


def job_commands(fake):
    """The job lines that the fake got (the G-code of the job, not the queries)."""
    return [c for c in fake.commands() if c and c[0] in 'GMF' and not c.startswith('$')]


def fresh_target(text=JOB):
    return make_job_target(text)


def patch_object_changed(tool):
    """on_object_changed() with the drawing parts replaced."""
    tool.ui.object_combo.currentText.return_value = 'job'
    tool._new_probing_shapes = MagicMock()
    tool._restore_al_radios = MagicMock()
    tool._restore_al_precision = MagicMock()
    tool._on_show_probing_geo_sig = MagicMock()


class TestLevelledStart(unittest.TestCase):
    def test_same_frame_streams(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake)
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'finished')
        self.assertEqual(names, ['frame', 'stream'])
        commands = fake.commands()
        # the frame is read in phase 1 and again in phase 2, before the first job line
        self.assertEqual(commands.count('$#'), 2)
        self.assertLess(max(i for i, c in enumerate(commands) if c == '$#'),
                        min(i for i, c in enumerate(commands) if c.startswith('G')
                            and not c.startswith('$')))
        cutting = [c for c in commands if c.startswith('G01 X')]
        self.assertEqual(len(cutting), 4)
        self.assertTrue(all('Z' in c for c in cutting))
        self.assertEqual(tool.job_lines[-3:], ['G00 X0.0000 Y0.0000', 'M05', 'M2'])
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_recovery)

    def test_g55_on_the_controller_refused(self):
        # the map was probed in G54; GRBL is in G55 now
        fake = CheckFake(replies={
            '$G': ['[GC:G0 G55 G17 G21 G90 G94 M5 M9 T0 F0 S0]', 'ok'],
            '$#': ['[G54:0.000,0.000,0.000]', '[G55:0.000,0.000,0.000]', '[G92:0.000,0.000,0.000]',
                   '[TLO:0.000]', 'ok']})
        tool, _target = make_levelled_stream_tool(fake)
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(names, ['frame'])
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(tool.job_lines, [])
        self.assertTrue(any('machine frame' in text for text in emitted(tool)))
        self.assertIsNone(tool._grbl_op)
        self.assertFalse(tool._grbl_stream_dirty)

    def test_changed_work_offset_refused(self):
        fake = CheckFake(replies={
            '$#': ['[G54:0.500,0.000,0.000]', '[G92:0.000,0.000,0.000]', '[TLO:0.000]', 'ok']})
        tool, _target = make_levelled_stream_tool(fake)
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(names, ['frame'])
        self.assertEqual(job_commands(fake), [])
        self.assertTrue(any('machine frame' in text for text in emitted(tool)))

    def test_units_of_the_controller_refused(self):
        fake = CheckFake(replies={'$G': ['[GC:G0 G54 G17 G20 G90 G94 M5 M9 T0 F0 S0]', 'ok']})
        tool, _target = make_levelled_stream_tool(fake)
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(names, ['frame'])
        self.assertEqual(job_commands(fake), [])
        self.assertTrue(any('not in the units' in text for text in emitted(tool)))

    def test_not_idle_refused_in_phase_1(self):
        fake = CheckFake(state='Run')
        tool, _target = make_levelled_stream_tool(fake)
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(names, ['frame'])
        self.assertEqual([c for c in fake.commands() if c != '?'], [])
        self.assertTrue(any('must be Idle' in text for text in emitted(tool)))

    def test_imported_map_no(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake, imported=True)
        tool._confirm_imported_frame.return_value = False
        names = spy_reserve(tool)
        tool.on_start()
        tool._confirm_imported_frame.assert_called_once_with()
        self.assertEqual(names, [])
        self.assertEqual(fake.written, [])
        self.assertIsNone(tool._al_map_frame)
        self.assertEqual(tool.job_lines, [])

    def test_imported_map_yes_binds_then_another_offset_is_refused(self):
        fake = CheckFake(replies={
            '$#': ['[G54:1.000,2.000,0.000]', '[G92:0.000,0.000,0.000]', '[TLO:0.000]', 'ok']})
        tool, _target = make_levelled_stream_tool(fake, imported=True)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'finished')
        self.assertEqual(tool._al_map_frame, ('G54', (1.0, 2.0, 0.0), False))
        self.assertEqual(tool._confirm_imported_frame.call_count, 1)

        # the 2nd Start asks again, and the machine has another offset now
        fake.replies['$#'] = ['[G54:3.000,2.000,0.000]', '[G92:0.000,0.000,0.000]', '[TLO:0.000]', 'ok']
        fake.written.clear()
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(tool._confirm_imported_frame.call_count, 2)
        self.assertEqual(names, ['frame'])
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(tool._al_map_frame, ('G54', (1.0, 2.0, 0.0), False))
        self.assertTrue(any('machine frame' in text for text in emitted(tool)))

    def test_not_levelled_start_keeps_one_phase(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake)
        tool.ui.autolevel_cb.get_value.return_value = False
        tool.app.options.update({'tools_grbl_arc_mode': 'keep'})
        names = spy_reserve(tool)
        before = tool._start_seq
        tool.on_start()
        self.assertEqual(names, ['stream'])
        self.assertEqual(tool._start_seq, before)
        self.assertEqual(ended_how(tool), 'finished')
        self.assertNotIn('$#', fake.commands())


class TestRequestDroppedInPhase1(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def check_dropped(self, tool, fake, names):
        self.assertEqual(names, ['frame'])
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(tool.job_lines, [])
        self.assertIsNone(tool._grbl_op)
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_recovery)
        self.assertTrue(any('Start request is old' in text for text in emitted(tool)))

    def test_map_replaced_during_frame(self):
        # the map A was imported and is not bound; the user imports the map B while phase 1 runs
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake, imported=True)
        fake.actions[('$#', 1)] = lambda: import_map_b(tool, self.temp.name)
        names = spy_reserve(tool)
        tool.on_start()
        self.check_dropped(tool, fake, names)
        # the frame of phase 1 was not bound to the new map
        self.assertIsNone(tool._al_map_frame)
        self.assertTrue(tool._al_map_imported)

    def test_target_changed_during_frame(self):
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake)
        patch_object_changed(tool)

        def change_target():
            tool.app.collection.get_by_name.return_value = fresh_target()
            tool.on_object_changed()
        fake.actions[('$#', 1)] = change_target
        names = spy_reserve(tool)
        tool.on_start()
        self.check_dropped(tool, fake, names)

    def test_map_replaced_during_dialog(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake, imported=True)

        def dialog():
            import_map_b(tool, self.temp.name)
            return True
        tool._confirm_imported_frame.side_effect = dialog
        names = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(names, [])
        self.assertEqual(fake.written, [])
        self.assertIsNone(tool._al_map_frame)
        self.assertEqual(tool.job_lines, [])
        self.assertTrue(any('while the dialog was open' in text for text in emitted(tool)))


class TestRequestDroppedInPhase2(unittest.TestCase):
    def check_stale(self, tool, fake):
        self.assertEqual(ended_how(tool), 'stale')
        self.assertEqual(job_commands(fake), [])
        self.assertNotIn(b'!', fake.written)
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertIsNone(tool._grbl_op)
        self.assertFalse(any('job finished' in text or 'found no error' in text for text in emitted(tool)))
        self.assertTrue(any('Start request is old' in text for text in emitted(tool)))

    def test_map_changed_during_final_frame_query(self):
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake)
        with tempfile.TemporaryDirectory() as directory:
            # the 2nd $# is the last frame query of phase 2
            fake.actions[('$#', 2)] = lambda: import_map_b(tool, directory)
            tool.on_start()
        self.assertEqual(fake.counts['$#'], 2)
        self.check_stale(tool, fake)

    def test_check_mode_not_entered_for_an_old_request(self):
        # the request gets old during the last frame query: no $C is sent (A8.3 step 4.3)
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        fake.actions[('$#', 2)] = tool._invalidate_start_requests
        tool.on_start()
        self.assertNotIn('$C', fake.commands())
        self.assertFalse(fake.in_check)
        self.check_stale(tool, fake)

    def test_target_changed_before_first_write(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake)
        patch_object_changed(tool)
        real_io = tool._grbl_stream_io

        def io(op, req=None):
            # after the frame compare and the request check, before the first job write
            tool.app.collection.get_by_name.return_value = fresh_target()
            tool.on_object_changed()
            return real_io(op, req)
        tool._grbl_stream_io = io
        tool.on_start()
        self.check_stale(tool, fake)

    def test_request_old_at_the_start_of_the_worker(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake)
        real_worker = tool._start_stream_worker

        def worker(op, lines, numbers, check_mode, poll_ms, req=None):
            tool._invalidate_start_requests()
            real_worker(op, lines, numbers, check_mode, poll_ms, req)
        tool._start_stream_worker = worker
        tool.on_start()
        self.assertEqual(ended_how(tool), 'stale')
        self.assertEqual([c for c in fake.commands() if c != '?'], ['$$', '$G', '$#'])
        self.assertFalse(tool._grbl_stream_dirty)

    def test_stop_before_the_worker_is_stopped_not_stale(self):
        fake = CheckFake()
        tool, _target = make_levelled_stream_tool(fake)
        real_worker = tool._start_stream_worker

        def worker(op, lines, numbers, check_mode, poll_ms, req=None):
            tool.on_grbl_stop()
            real_worker(op, lines, numbers, check_mode, poll_ms, req)
        tool._start_stream_worker = worker
        tool.on_start()
        self.assertEqual(ended_how(tool), 'stopped')
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(tool._grbl_recovery, 'needs_reset')

    def test_stale_after_check_alarm(self):
        # the request is invalidated after $C was confirmed and before the first job line;
        # GRBL leaves check mode in Alarm
        fake = HookFake()
        fake.after_exit_state = 'Alarm'
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        fake.actions[('$C', 1)] = tool._invalidate_start_requests
        tool.on_start()
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(fake.commands().count('$C'), 2)
        self.assertEqual(ended_how(tool), 'stale_needs_home')
        self.assertEqual(tool._grbl_recovery, 'needs_home')
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertTrue(any('Home' in text and 'request is old' in text for text in emitted(tool)))
        self.assertFalse(any('[success]' in text for text in emitted(tool)))

    def test_stale_after_check_idle(self):
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        fake.actions[('$C', 1)] = tool._invalidate_start_requests
        tool.on_start()
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(ended_how(tool), 'stale')
        self.assertIsNone(tool._grbl_recovery)
        self.assertFalse(tool._grbl_stream_dirty)
        self.assertFalse(fake.in_check)

    def test_stale_check_exit_failed_keeps_the_stream_dirty(self):
        fake = HookFake()
        fake.exit_lines = ['[MSG:Disabled]', 'ok']          # no banner
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        fast_timeouts(tool)
        fake.actions[('$C', 1)] = tool._invalidate_start_requests
        tool.on_start()
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(ended_how(tool), 'check_exit_failed')
        self.assertTrue(tool._grbl_stream_dirty)
        self.assertEqual(tool._grbl_recovery, 'needs_reset')

    def test_stale_cancelled_during_the_cleanup(self):
        fake = HookFake()
        tool, _target = make_levelled_stream_tool(fake, check_mode=True)
        fast_timeouts(tool)
        fake.actions[('$C', 1)] = tool._invalidate_start_requests
        stop = []
        hook_actions = fake.readline

        def readline():
            if fake.exiting and not stop:
                stop.append(True)
                tool.on_grbl_stop()
            return hook_actions()
        fake.readline = readline
        tool.on_start()
        self.assertEqual(job_commands(fake), [])
        self.assertEqual(ended_how(tool), 'stopped')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)


class TestFirstJobWrite(unittest.TestCase):
    @staticmethod
    def make(fake=None):
        fake = fake or CheckFake()
        tool = make_stream_tool(fake)
        op = tool._grbl_reserve('stream', motion=True)
        req = _StartRequest(tool._start_seq, None, None, None, False, op.safety_generation, False)
        return tool, fake, op, req

    def test_invalidation_wins_lock(self):
        tool, fake, op, req = self.make()
        write, _readline = tool._grbl_stream_io(op, req)
        out = []
        thread = threading.Thread(target=lambda: out.append(write(b'G1 X1.000 Y1.000\n')), daemon=True)
        with tool._grbl_op_lock:
            thread.start()
            thread.join(0.05)
            self.assertTrue(thread.is_alive())          # the write waits for the lock
            tool._start_seq += 1                        # an invalidation inside its locked section
        thread.join(2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(out, ['stale'])
        self.assertEqual(fake.written, [])
        self.assertEqual(write.first_job_line_written, [False])
        # the status request is not a job line
        self.assertEqual(write(b'?'), 'ok')
        self.assertEqual(fake.written, [b'?'])

    def test_first_write_wins_lock(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingFake(CheckFake):
            def write(self, data):
                if data.startswith(b'G1'):
                    entered.set()
                    release.wait(2.0)
                return super().write(data)

        tool, fake, op, req = self.make(BlockingFake())
        seq = tool._start_seq
        write, _readline = tool._grbl_stream_io(op, req)
        out = []
        writer = threading.Thread(target=lambda: out.append(write(b'G1 X1.000 Y1.000\n')), daemon=True)
        writer.start()
        self.assertTrue(entered.wait(2.0))              # the writer holds the lock in port.write()
        invalidator = threading.Thread(target=tool._invalidate_start_requests, daemon=True)
        invalidator.start()
        invalidator.join(0.05)
        self.assertTrue(invalidator.is_alive())         # it waits for the lock
        self.assertEqual(tool._start_seq, seq)
        release.set()
        writer.join(2.0)
        invalidator.join(2.0)
        self.assertFalse(writer.is_alive() or invalidator.is_alive())
        self.assertEqual(out, ['ok'])
        self.assertEqual(write.first_job_line_written, [True])
        self.assertEqual(tool._start_seq, seq + 1)
        # the job runs on after its first line
        self.assertEqual(write(b'G1 X2.000 Y1.000\n'), 'ok')
        self.assertEqual(len(job_commands(fake)), 2)

    def test_short_first_write(self):
        class ShortFake(CheckFake):
            def write(self, data):
                super().write(data)
                if data[:1] not in (b'$', b'?'):
                    return len(data) - 1                # a job line: 1 byte less

        tool, fake, op, req = self.make(ShortFake())
        write, _readline = tool._grbl_stream_io(op, req)
        self.assertEqual(write(b'G1 X1.000 Y1.000\n'), 'port_error')
        self.assertEqual(write.first_job_line_written, [False])

        # the whole path: the abort path of A5.2 latches the recovery
        fake = ShortFake()
        tool, _target = make_levelled_stream_tool(fake)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'port_error')
        self.assertEqual(tool._grbl_recovery, 'needs_reset')
        self.assertTrue(tool._grbl_stream_dirty)

    def test_port_error_disconnects(self):
        class LostFake(CheckFake):
            lost = False
            lose_at_job = False

            def write(self, data):
                if self.lose_at_job and data[:1] not in (b'$', b'?'):
                    self.lost = True
                return super().write(data)

            def readline(self):
                if self.lost:
                    raise OSError('device lost')
                return super().readline()

        # (a) in a stream
        fake = LostFake()
        fake.lose_at_job = True
        tool, _target = make_levelled_stream_tool(fake)
        tool.on_start()
        self.assertEqual(ended_how(tool), 'port_error')
        tool.grbl_port_closed_sig.emit.assert_called_once_with()
        self.assertIsNone(tool.grbl_ser_port)
        self.assertFalse(tool._grbl_port_ready())

        # (b) in a status poll (a monitor operation never reaches _on_stream_ended)
        fake = LostFake()
        tool = make_stream_tool(fake)
        fake.lost = True
        op = tool._grbl_reserve('status_poll')
        tool._grbl_query_status(op, timeout=0.5)
        tool._grbl_release(op)
        tool.grbl_port_closed_sig.emit.assert_called_once_with()
        self.assertIsNone(tool.grbl_ser_port)
        self.assertFalse(tool._grbl_port_ready())

    def test_auto_reconnect_never_resumes(self):
        from unittest.mock import MagicMock

        class LostFake(CheckFake):
            lost = False

            def write(self, data):
                if data[:1] not in (b'$', b'?'):
                    self.lost = True
                return super().write(data)

            def readline(self):
                if self.lost:
                    raise OSError('device lost')
                return super().readline()

        for option in (True, False):
            tool, _target = make_levelled_stream_tool(LostFake())
            tool.app.options = {'tools_grbl_auto_reconnect': option}
            tool.on_grbl_connect = MagicMock()
            tool.on_start()
            self.assertEqual(ended_how(tool), 'port_error')
            tool._on_grbl_port_closed()
            tool._reconnect_tick()   # the timer slot: no real wait
            self.assertEqual(tool.on_grbl_connect.call_count, 1 if option else 0)
            if not option:
                continue
            new_port = CheckFake()
            tool.grbl_ser_port = new_port
            tool._reconnect_done()
            self.assertEqual(new_port.written, [])
            self.assertIn('Reconnected. The job was stopped.',
                          ' '.join(c.args[0] for c in tool.app.inform.emit.call_args_list))
            tool._reconnect_tick()
            self.assertEqual(tool.on_grbl_connect.call_count, 1)

    def test_not_levelled_write_has_no_request_check(self):
        tool, fake, op, _req = self.make()
        write, _readline = tool._grbl_stream_io(op)
        tool._start_seq += 5
        self.assertEqual(write(b'G1 X1.000 Y1.000\n'), 'ok')


class TestInvalidationEvents(unittest.TestCase):
    """Each event adds 1 to _start_seq and ends in 2 s (a deadlock fails the test)."""

    @staticmethod
    def make():
        fake = CheckFake()
        tool, target = make_levelled_stream_tool(fake)
        patch_object_changed(tool)
        return tool, fake, target

    def check(self, event, tool=None):
        if tool is None:
            tool = self.make()[0]
        before = tool._start_seq
        run_limited(self, lambda: event(tool))
        self.assertGreater(tool._start_seq, before)
        return tool


    def test_event_import_start_without_publication(self):
        # the start of an import (not its result) is an event
        tool = self.make()[0]
        tool.app.worker_task.emit.side_effect = None
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('map.txt', '')):
            self.check(lambda t: t.on_import_height_map(), tool)


    def test_event_connect(self):
        tool = self.make()[0]
        tool.grbl_ser_port = None
        tool._grbl_unclosed_ports = []
        tool._grbl_connected = False
        tool._grbl_set_ui_connected = MagicMock()
        tool.ui.com_list_combo.currentText.return_value = 'COM9'
        tool.ui.baudrates_list_combo.currentText.return_value = '115200'
        with patch.object(sender_module.serial, 'serial_for_url', return_value=CheckFake()), \
                patch.object(sender_module.QtCore.QTimer, 'singleShot'):
            self.check(lambda t: t.on_grbl_connect(), tool)


    def test_serial_connect_opens_port_off_gui_thread(self):
        opened_in = []

        class BlockingFake(CheckFake):
            def open(self):
                opened_in.append(threading.current_thread())

        tool = self.make()[0]
        tool.grbl_ser_port = None
        tool._grbl_unclosed_ports = []
        tool._grbl_connected = False
        tool._grbl_set_ui_connected = MagicMock()
        tool.ui.com_list_combo.currentText.return_value = 'COM9'
        tool.ui.baudrates_list_combo.currentText.return_value = '115200'
        # the GUI slot must not run on the worker thread (it starts Qt timers): the result is only recorded
        tool.connect_result_sig = MagicMock()
        workers = []

        def run_in_thread(task):
            workers.append(threading.Thread(target=task['fcn'], daemon=True))
            workers[-1].start()

        tool.app.worker_task.emit.side_effect = run_in_thread
        with patch.object(sender_module.serial, 'serial_for_url', return_value=BlockingFake()):
            tool.on_grbl_connect()
            workers[0].join(5.0)
        self.assertEqual(opened_in, [workers[0]])       # the port opens in the worker, not on the GUI thread

    def test_no_connect_while_an_ecut_port_closes(self):
        tool = self.make()[0]
        ecut_port = MagicMock()                         # prepare_program: an E-Cut port
        tool.grbl_ser_port = ecut_port
        tool._grbl_connected = True
        closes = []
        tool.app.worker_task.emit.side_effect = closes.append   # the close waits in the worker queue
        tool._grbl_close_port()
        ecut_port.close.assert_not_called()             # not on the GUI thread
        with patch.object(sender_module.serial, 'serial_for_url') as make_port:
            tool.on_grbl_connect()
            tool.app.worker_task.emit.side_effect = lambda task: task['fcn']()
            closes[0]['fcn']()                          # the worker closes the old port
            ecut_port.close.assert_called_once()
            tool.ui.com_list_combo.currentText.return_value = 'COM9'
            tool.ui.baudrates_list_combo.currentText.return_value = '115200'
            tool.connect_result_sig = MagicMock()
            tool.on_grbl_connect()
        self.assertEqual(make_port.call_count, 1)       # refused while closing, accepted after the close

    def test_event_disconnect_during_an_operation(self):
        tool = self.make()[0]
        op = tool._grbl_reserve('stream', motion=True)
        self.check(lambda t: t._grbl_disconnect(), tool)
        self.assertTrue(op.cancel.is_set())

    def test_request_is_old_after_each_event(self):
        # a request made now is not current after an event
        events = (lambda t: t.on_object_changed(), lambda t: t._clear_probe_data(), lambda t: t.on_grbl_stop(),
                  lambda t: t.on_grbl_reset(), lambda t: t._grbl_close_port())
        for event in events:
            tool, _fake, target = self.make()
            ctx = tool._al_capture_context()
            req = _StartRequest(tool._start_seq, ctx, target, tool._al_map_frame, tool._al_map_imported,
                                tool._grbl_safety_generation, False)
            self.assertTrue(tool._start_request_current(req))
            run_limited(self, lambda: event(tool))
            self.assertFalse(tool._start_request_current(req))


if __name__ == '__main__':
    unittest.main()
