"""GCode sender startup, preview workers and probe-before-stream workflow."""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtGui, QtWidgets
from shapely import Point

from tests.grbl_stream_helpers import make_levelled_stream_tool, spy_reserve, JOB
from tests.test_grbl_sender_construct import make_app
from tests.test_grbl_view3d import show_tool, fake_canvas, SPLIT_ARC_JOB
from tests.test_levelling_audit import FakeGrbl
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender


class SenderSelectionTest(unittest.TestCase):
    def test_excellon_holes_follow_checkbox_selection_and_loading(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                app = make_app()
                app.plotcanvas3d = fake_canvas()
                app.collection.setItem(1, QtGui.QStandardItem('Excellon'))
                root = app.collection.item(1)
                holes = Point(5, 5).buffer(0.5)
                other_holes = Point(8, 8).buffer(1.0)
                objects = {
                    'drills': SimpleNamespace(kind='excellon', solid_geometry=[holes]),
                    'other': SimpleNamespace(kind='excellon', solid_geometry=other_holes)
                }
                app.collection.get_by_name = objects.get
                if existing:
                    root.appendRow(QtGui.QStandardItem('drills'))
                tool = ToolGCodeSender(app)
                tool.build_ui()
                tool.ui.avoid_exc_holes_cb.setChecked(True)
                tool.on_show_3d()
                shapes = tool.view3d.excellon_shapes

                def assert_holes(geometry):
                    self.assertEqual(len(shapes.data), 1)
                    points = next(iter(shapes.data.values()))['line_pts']
                    self.assertEqual(tuple(points.min(axis=0)), geometry.bounds[:2])
                    self.assertEqual(tuple(points.max(axis=0)), geometry.bounds[2:])

                if not existing:
                    self.assertFalse(shapes.data)
                    root.appendRow(QtGui.QStandardItem('drills'))
                assert_holes(holes)
                tool.ui.avoid_exc_holes_cb.setChecked(False)
                self.assertFalse(shapes.data)
                tool.ui.avoid_exc_holes_cb.setChecked(True)
                assert_holes(holes)
                root.appendRow(QtGui.QStandardItem('other'))
                tool.ui.avoid_exc_combo.setCurrentIndex(1)
                assert_holes(other_holes)
                root.removeRow(1)
                assert_holes(holes)
                root.removeRow(0)
                self.assertFalse(shapes.data)
                root.appendRow(QtGui.QStandardItem('drills'))
                self.assertTrue(shapes.data)
                app.ui.plot_tab_area.indexOf.return_value = -1
                tool.on_plugin_cleanup()
                self.assertFalse(shapes.data)
                self.assertIsNone(shapes.parent)

    def test_existing_and_later_loaded_excellon_are_selected(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                app = make_app()
                app.collection.setItem(1, QtGui.QStandardItem('Excellon'))
                root = app.collection.item(1)
                if existing:
                    root.appendRow(QtGui.QStandardItem('drills'))
                tool = ToolGCodeSender(app)
                tool.build_ui()
                combo = tool.ui.avoid_exc_combo
                if not existing:
                    root.appendRow(QtGui.QStandardItem('drills'))
                self.assertEqual(combo.currentText(), 'drills')
                root.appendRow(QtGui.QStandardItem('other drills'))
                self.assertEqual(combo.currentText(), 'drills')
                root.removeRow(0)
                self.assertEqual(combo.currentText(), 'other drills')
                app.collection.clear()
                app.collection.insertRows(0, 5)
                app.collection.setItem(1, QtGui.QStandardItem('Excellon'))
                app.collection.item(1).appendRow(QtGui.QStandardItem('new project drills'))
                self.assertEqual(combo.count(), 1)
                self.assertEqual(combo.currentText(), 'new project drills')

    def test_reset_and_close_invalidate_map(self):
        tool = ToolGCodeSender(make_app())
        tool.build_ui()
        for action in ('reset', 'close'):
            with self.subTest(action=action):
                tool.al_heights_valid = True
                tool._al_map_imported = True
                tool._al_map_frame = ('G54', (0, 0, 0), False)
                with patch('appPlugins.Common.levelling_core.ShapeCollection'):
                    if action == 'reset':
                        tool.ui.reset_button.click()
                    else:
                        tool.on_plugin_cleanup()
                self.assertFalse(tool.al_heights_valid)
                self.assertIsNone(tool._al_map_frame)
                self.assertFalse(tool._al_map_imported)


class SenderStartWorkflowTest(unittest.TestCase):
    def make_tool(self, **fake_options):
        fake = FakeGrbl(**fake_options)
        tool, target = make_levelled_stream_tool(fake)
        tool._choose_height_map = MagicMock(return_value='existing')
        tool.ui.ptravelz_entry.get_value.return_value = 2.0
        tool.ui.pdepth_entry.get_value.return_value = -1.0
        tool.ui.feedrate_probe_entry.get_value.return_value = 100.0
        tool.ui.precision_probe_cb.get_value.return_value = False
        return tool, target, fake

    def test_missing_map_probes_then_levels_and_streams(self):
        tool, target, fake = self.make_tool()
        tool.al_heights_valid = False
        operations = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(operations, ['probe', 'frame', 'stream'])
        self.assertEqual(fake.probe_count, len(tool.al_voronoi_geo_storage))
        self.assertTrue(tool.al_heights_valid)
        self.assertEqual(tool.job_map_generation, tool._al_generation)
        commands = fake.commands()
        self.assertLess(max(i for i, cmd in enumerate(commands) if cmd.startswith('G38.2')),
                        next(i for i, cmd in enumerate(commands) if cmd.startswith('M03')))
        self.assertEqual(target.source_file, JOB)
        tool._choose_height_map.assert_not_called()

    def test_existing_map_choice_can_reuse_regenerate_or_cancel(self):
        for choice, expected in (('existing', ['frame', 'stream']),
                                 ('new', ['probe', 'frame', 'stream']), ('cancel', [])):
            with self.subTest(choice=choice):
                tool, _target, fake = self.make_tool()
                tool._choose_height_map.return_value = choice
                operations = spy_reserve(tool)
                tool.on_start()
                self.assertEqual(operations, expected)
                self.assertEqual(fake.probe_count > 0, choice == 'new')

    def test_failed_probe_never_streams(self):
        tool, _target, fake = self.make_tool(fail_probe_at=2)
        tool.al_heights_valid = False
        operations = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(operations, ['probe'])
        self.assertFalse(tool.al_heights_valid)
        self.assertFalse(any(cmd.startswith('M03') for cmd in fake.commands()))

    def test_queued_probe_results_are_published_before_continuing(self):
        tool, _target, _fake = self.make_tool()
        tool.al_heights_valid = False
        events = []
        tool.al_result_ready_sig.emit.side_effect = lambda result: events.append(('result', result))
        tool.probe_finished_sig.emit.side_effect = lambda op, ctx: events.append(('finished', op, ctx))
        operations = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(operations, ['probe'])
        self.assertIsNone(tool._grbl_op)
        self.assertEqual([event[0] for event in events], ['result', 'finished'])
        tool._on_al_result_ready(events[0][1])
        tool._on_probe_finished(events[1][1], events[1][2])
        self.assertEqual(operations, ['probe', 'frame', 'stream'])

    def test_height_map_dialog_has_explicit_choices(self):
        tool, _target, _fake = self.make_tool()
        for choice in ('new', 'existing', 'cancel'):
            with self.subTest(choice=choice):
                buttons = [object(), object(), object()]
                box = MagicMock()
                box.addButton.side_effect = buttons
                box.clickedButton.return_value = buttons[('new', 'existing', 'cancel').index(choice)]
                with patch('appPlugins.ToolGcodeSender.Sender.FCMessageBox', return_value=box):
                    self.assertEqual(ToolGCodeSender._choose_height_map(tool), choice)
                box.setDefaultButton.assert_called_once_with(buttons[2])

    def test_source_change_while_choosing_map_cancels_start(self):
        tool, _target, fake = self.make_tool()
        tool._choose_height_map.side_effect = lambda: (tool._clear_probe_data(), 'existing')[1]
        tool.on_start()
        self.assertEqual(fake.commands(), [])

    def test_check_mode_without_a_map_never_probes(self):
        tool, _target, fake = self.make_tool()
        tool.al_heights_valid = False
        tool.ui.check_mode_cb.get_value.return_value = True
        tool.on_start()
        self.assertEqual(fake.commands(), [])

    def test_close_during_probe_prevents_stream(self):
        tool, _target, fake = self.make_tool()
        tool.al_heights_valid = False
        def close_after_probe():
            if fake.probe_count:
                fake.read_hook = None
                tool.on_plugin_cleanup()

        fake.read_hook = close_after_probe
        operations = spy_reserve(tool)
        tool.on_start()
        self.assertEqual(operations, ['probe'])
        self.assertFalse(tool.al_heights_valid)


class SenderPreviewWorkerTest(unittest.TestCase):
    def test_levelled_preview_uses_private_interpolation_state(self):
        tool, _target = make_levelled_stream_tool(FakeGrbl())
        tool.app.plotcanvas3d = fake_canvas()
        queued, results = [], []
        tool.app.worker_task.emit.side_effect = queued.append
        tool.preview_ready_sig.emit.side_effect = results.append
        tool._al_points = 'unchanged'
        tool.on_show_3d()
        ui = tool.ui
        tool.ui = None
        queued[0]['fcn']()
        tool.ui = ui
        self.assertNotIn('error', results[0])
        self.assertEqual(tool._al_points, 'unchanged')
        tool._on_preview_ready(results[0])
        self.assertTrue(tool.view3d._segments)

    def test_preparation_is_queued_and_worker_reads_no_widgets(self):
        tool = show_tool(SPLIT_ARC_JOB, 'send')
        queued = []
        tool.app.worker_task.emit.side_effect = queued.append
        tool.on_show_3d()
        self.assertEqual(len(queued), 1)
        self.assertEqual(tool.view3d._segments, [])
        ui = tool.ui
        tool.ui = None
        # Delay the GUI slot until after the worker has returned.
        results = []
        tool.preview_ready_sig.emit.side_effect = results.append
        queued[0]['fcn']()
        tool.ui = ui
        self.assertEqual(len(results), 1)
        tool._on_preview_ready(results[0])
        self.assertTrue(tool.view3d._segments)

    def test_closed_plugin_discards_queued_preview(self):
        tool = show_tool(SPLIT_ARC_JOB, 'send')
        queued = []
        tool.app.worker_task.emit.side_effect = queued.append
        tool.on_show_3d()
        view = tool.view3d
        tool.app.ui.plot_tab_area.indexOf.return_value = -1
        tool.on_plugin_cleanup()
        queued[0]['fcn']()
        self.assertIsNone(tool.view3d)
        self.assertEqual(view._segments, [])

    def test_changed_source_discards_preview(self):
        tool = show_tool(SPLIT_ARC_JOB, 'send')
        queued = []
        tool.app.worker_task.emit.side_effect = queued.append
        tool.on_show_3d()
        tool.app.collection.get_by_name.return_value.source_file = 'G0 X99'
        queued[0]['fcn']()
        self.assertEqual(tool.view3d._segments, [])


class SenderMachineProfileBlockTest(unittest.TestCase):
    """With an active profile the Sender checks (and, with the calibration on, corrects) the stream, the restart and
    Save levelled G-code; the live probe commands the points through the correction."""

    def make_tool(self, folder, correction, levelled=False, source=None, fake_cls=FakeGrbl, bed=300.0, heights=None):
        from appCommon.machine_profile.output import calibration_hash
        from appCommon.machine_profile.profile import MachineProfile, save_profile
        from tests.grbl_stream_helpers import make_stream_tool
        calibration = {'matrix': [[1.0, 0.01], [0.0, 1.0]], 'enabled': correction}
        profile = MachineProfile(name='Cal', bed_x=bed, bed_y=300.0, z_travel=40.0,
                                 work_offset=(0.0, 0.0, -20.0), calibration=calibration)
        save_profile(profile, folder)
        fake = fake_cls(heights=heights, replies={
            '$G': ['[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]', 'ok'],
            '$#': ['[G54:0.000,0.000,-20.000]', '[G55:0.000,0.000,0.000]', '[G56:0.000,0.000,0.000]',
                   '[G57:0.000,0.000,0.000]', '[G58:0.000,0.000,0.000]', '[G59:0.000,0.000,0.000]',
                   '[G28:0.000,0.000,0.000]', '[G30:0.000,0.000,0.000]', '[G92:0.000,0.000,0.000]',
                   '[TLO:0.000]', 'ok']})
        if levelled:
            tool = make_levelled_stream_tool(fake, **({} if source is None else {'text': source}))[0]
        else:
            tool = make_stream_tool(fake)
        tool.app.options.update({'global_machine_profile': 'Cal', 'global_machine_profiles_folder': folder})
        if levelled:
            tool._al_map_calibration = calibration_hash(profile)       # the map was probed under this correction
        tool._grbl_report_inches = False        # $13 was read: the positions of the controller are in mm
        tool.ui.ptravelz_entry.get_value.return_value = 2.0
        tool.ui.pdepth_entry.get_value.return_value = -1.0
        tool.ui.feedrate_probe_entry.get_value.return_value = 100.0
        tool.ui.precision_probe_cb.get_value.return_value = False
        return tool, fake

    def refused(self, tool):
        return [m for m in tool.app.inform.emit.call_args_list if 'corrects XY' in str(m)]

    MARKED = ('G21\nG90\nG0 X10 Y10\n(MP:FIXED BEGIN toolchange)\nG0 X0 Y0\nM6\n(MP:FIXED END)\n'
              'G1 X20 Y10 F100\n')
    RESTART = 'G21\nG90\nG94\nF100\nM3 S1000\nG0 Z5\nG0 X10 Y10\nG1 Z-1\nG1 X20 Y10\nG1 X30 Y10\n'

    OUT_OF_BED = 'G21\nG90\nG0 Z-5\nG0 X10 Y10\nG0 X500 Y10\n'

    @staticmethod
    def streamed(fake):
        """The lines of the job that the fake controller got (not the status polls and the state queries)."""
        return [cmd for cmd in fake.commands() if cmd != '?' and not cmd.startswith('$')]

    def make_stream(self, folder, correction, source, fake_cls=FakeGrbl):
        from tests.grbl_stream_helpers import make_stream_tool
        tool, fake = self.make_tool(folder, correction, fake_cls=fake_cls)
        stream = make_stream_tool(fake, source=source)
        stream.app.options.update(tool.app.options)
        stream._grbl_report_inches = False
        stream._confirm_profile_stream = MagicMock(return_value=True)
        return stream, fake

    def test_stream_is_corrected_with_the_calibration_on_and_a_marked_station_stays(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_stream(folder, True, self.MARKED)
            tool.on_start()
            self.assertEqual(tool.job_lines, ['G21', 'G90', 'G0 X10.1000 Y10.0000', 'G0 X0 Y0', 'M6',
                                              'G1 X20.1000 Y10.0000 F100'])
            self.assertIn('G0 X10.1000 Y10.0000', fake.commands())
            self.assertIn('G0 X0 Y0', fake.commands())
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_stream(folder, False, self.MARKED)
            tool.on_start()
            self.assertEqual(tool.job_lines, ['G21', 'G90', 'G0 X10 Y10', 'G0 X0 Y0', 'M6', 'G1 X20 Y10 F100'])

    def test_rejected_stream_does_not_start_and_start_is_free(self):
        import tempfile
        for correction in (True, False):
            with self.subTest(correction=correction), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, correction, self.OUT_OF_BED)
                tool.on_start()
                self.assertEqual(self.streamed(fake), [])
                self.assertIsNone(tool._grbl_op)
                self.assertTrue([m for m in tool.app.inform.emit.call_args_list if 'not started' in str(m)])

    def test_declined_confirmation_does_not_start_the_stream(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_stream(folder, True, self.MARKED)
            tool._confirm_profile_stream.return_value = False
            tool.on_start()
            self.assertTrue(tool._confirm_profile_stream.called)
            self.assertEqual(self.streamed(fake), [])
            self.assertIsNone(tool._grbl_op)

    def test_restart_keeps_the_prepared_program_and_is_not_corrected_again(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_stream(folder, True, self.RESTART)
            tool.app.collection.get_by_name.return_value.z_move = 5.0
            tool._choose_start_line = MagicMock(return_value=10)
            tool.on_start()
            commands = self.streamed(fake)
            self.assertIn('G0 X20.1000 Y10.0000', commands)       # the preamble position: corrected once
            self.assertNotIn('G0 X20.2010 Y10.0000', commands)
            self.assertEqual(commands[-1], 'G1 X30.1000 Y10.0000')
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_stream(folder, True, self.RESTART.replace('G1 X30 Y10', 'G1 X900 Y10'))
            tool.app.collection.get_by_name.return_value.z_move = 5.0
            tool._choose_start_line = MagicMock(return_value=10)
            tool.on_start()
            self.assertEqual(self.streamed(fake), [])           # the resumed part is checked too

    def test_levelled_stream_is_corrected_after_the_levelling(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            plain = make_levelled_stream_tool(FakeGrbl())[0]
            plain.on_start()
            tool, fake = self.make_tool(folder, True, levelled=True)
            tool._al_map_frame = ('G54', (0.0, 0.0, -20.0), False)      # the work offset of the fake controller
            tool._confirm_profile_stream = MagicMock(return_value=True)
            tool.on_start()
            self.assertTrue(tool.job_lines, tool.app.inform.emit.call_args_list)
            self.assertEqual(len(tool.job_lines), len(plain.job_lines))
            self.assertNotEqual(tool.job_lines, plain.job_lines)
            self.assertIn('G00 X1.0100 Y1.0000', tool.job_lines)
            self.assertTrue(fake.commands())

    LEVELLED_MARKED = ('G21\nG90\nG94\nF300\nM03 S10000\nG00 Z2.0000\nG00 X1.0000 Y1.0000\n'
                       '(MP:FIXED BEGIN toolchange)\nG00 X4.0000 Y4.0000\nM6\n(MP:FIXED END)\n'
                       'G01 Z-0.1000 F300\nG01 X3.0000 Y1.0000\nG00 Z2.0000\nM05\nM2\n')

    def test_levelled_stream_keeps_the_fixed_station_and_corrects_the_cut(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, _fake = self.make_tool(folder, True, levelled=True, source=self.LEVELLED_MARKED)
            tool._al_map_frame = ('G54', (0.0, 0.0, -20.0), False)
            tool._confirm_profile_stream = MagicMock(return_value=True)
            tool.on_start()
            self.assertTrue(tool.job_lines, tool.app.inform.emit.call_args_list)
            self.assertIn('G00 X4.0000 Y4.0000', tool.job_lines)         # the station keeps its machine position
            self.assertFalse([line for line in tool.job_lines if 'X4.04' in line])
            self.assertTrue([line for line in tool.job_lines if line.startswith('G01 X3.0100 Y1.0000')])
            self.assertFalse([line for line in tool.job_lines if 'MP:' in line])

    def test_corrected_plain_job_keeps_the_line_map_and_arc_chords_map_to_the_arc(self):
        import tempfile
        source = 'G21\nG90\nG91.1\nG17\nG0 X20 Y10\nG2 X10 Y20 I-10 J0 F100\nG1 X15 Y15\n'
        with tempfile.TemporaryDirectory() as folder:
            tool, _fake = self.make_stream(folder, True, source)
            tool.on_start()
            numbers = tool.job_line_numbers
            self.assertEqual(len(numbers), len(tool.job_lines))
            self.assertGreater(numbers.count(6), 2, (numbers, tool.job_lines, tool.app.inform.emit.call_args_list))
            self.assertEqual(numbers[:5], [1, 2, 3, 4, 5])
            self.assertEqual(set(numbers[4:-1]) - {5}, {6})
            self.assertEqual(numbers[-1], 7)

    # 30 mm below the work zero of the profile (Z -20): machine Z -30 without a tool length offset
    TOOL_CHANGE = 'G21\nG90\nG0 Z5\nM6\nG0 X10 Y10\nG0 Z-10\n'

    @staticmethod
    def run_tool_change(tool, fake, command, route, reply=None):
        """Stream TOOL_CHANGE: at the M6 queue the touch-off `command`, then press Resume through `route`. A refused
        Resume is followed by Stop (the fake controller would wait for ever). `reply`: the lines that the controller
        answers to `$#` from then on. A route that returns a thread (the GUI thread of the user, which waits for
        the stream worker) is waited for before the Stop."""
        state = {'queued': False, 'tried': False, 'thread': None}

        def operator():
            if (not (fake.tool_waiting and tool.__dict__.get('_grbl_tool_change_pending')) or
                    tool.__dict__.get('_grbl_tool_command_busy')):
                return
            if state['thread'] is not None and state['thread'].is_alive():
                return
            if not state['queued']:
                state['queued'] = True
                if reply is not None:
                    fake.replies['$#'] = reply
                tool._grbl_queue_tool_command(command)
            elif not state['tried']:
                state['tried'] = True
                state['thread'] = route(tool)
            elif b'~' not in fake.written:
                tool.on_grbl_stop()
        fake.read_hook = operator
        tool.on_start()

    RESUME_ROUTES = (('Resume button', lambda tool: tool._on_resume_button()),)

    def test_tool_change_with_a_tlo_below_the_z_travel_refuses_every_resume_route(self):
        import tempfile
        from tests.test_grbl_job_control import ToolChangeFake
        for name, route in self.RESUME_ROUTES:
            with self.subTest(route=name), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, False, self.TOOL_CHANGE, ToolChangeFake)
                tool._confirm_profile_resume = MagicMock(return_value=True)
                self.run_tool_change(tool, fake, 'G43.1 Z-15', route)       # machine Z -45, travel is 40
                self.assertNotIn(b'~', fake.written)
                self.assertNotIn(b'G0 Z-10\n', fake.written)
                texts = [str(m) for m in tool.app.inform.emit.call_args_list]
                self.assertTrue([t for t in texts if 'resume refused' in t and 'Line 6' in t and 'z_travel' in t], texts)
                tool.ui.resume_button.setDisabled.assert_called_with(True)
                tool._confirm_profile_resume.assert_not_called()

    def test_tool_change_with_a_safe_tlo_resumes_through_every_route(self):
        import tempfile
        from tests.test_grbl_job_control import ToolChangeFake
        from tests.grbl_stream_helpers import ended_how
        for name, route in self.RESUME_ROUTES:
            with self.subTest(route=name), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, False, self.TOOL_CHANGE, ToolChangeFake)
                tool._confirm_profile_resume = MagicMock(return_value=True)
                self.run_tool_change(tool, fake, 'G43.1 Z-2', route)
                self.assertEqual(fake.written.count(b'~'), 1)
                self.assertIn(b'G0 Z-10\n', fake.written)
                self.assertEqual(ended_how(tool), 'finished')

    def test_tool_change_resume_asks_when_the_recheck_has_warnings(self):
        import tempfile
        from tests.test_grbl_job_control import ToolChangeFake
        for answer in (True, False):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, False, self.TOOL_CHANGE, ToolChangeFake)
                tool._confirm_profile_resume = MagicMock(return_value=answer)
                # $TPW measures a tool length, and the controller does not report it: the TLO is unverified
                self.run_tool_change(tool, fake, '$TPW', lambda t: self.gui_thread(t._on_resume_button),
                                     reply=['[G54:0.000,0.000,0.000]', '[G92:0.000,0.000,0.000]', 'ok'])
                tool._confirm_profile_resume.assert_called_once()
                self.assertEqual(fake.written.count(b'~'), 1 if answer else 0)

    @staticmethod
    def gui_thread(function):
        """The Resume of the user: the GUI thread waits for the stream worker, which owns the port."""
        thread = threading.Thread(target=function)
        thread.start()
        return thread

    TPW_REPLY = '[G54:0.000,0.000,0.000]', '[G92:0.000,0.000,0.000]', '[TLO:%.3f]', 'ok'

    def test_resume_after_a_probed_tool_change_uses_the_tlo_of_the_controller(self):
        import tempfile
        from tests.test_grbl_job_control import ToolChangeFake
        from tests.grbl_stream_helpers import ended_how
        for command in ('$TPW', '$TLR'):
            with self.subTest(command=command, tlo=-15.0), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, False, self.TOOL_CHANGE, ToolChangeFake)
                tool._confirm_profile_resume = MagicMock(return_value=True)
                reply = [line % -15.0 if '%' in line else line for line in self.TPW_REPLY]
                self.run_tool_change(tool, fake, command, lambda t: self.gui_thread(t._on_resume_button), reply)
                self.assertNotIn(b'~', fake.written)
                self.assertNotIn(b'G0 Z-10\n', fake.written)
                texts = [str(m) for m in tool.app.inform.emit.call_args_list]
                self.assertTrue([t for t in texts if 'resume refused' in t and 'Line 6' in t and 'z_travel' in t], texts)
                tool._confirm_profile_resume.assert_not_called()
            with self.subTest(command=command, tlo=-2.0), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_stream(folder, False, self.TOOL_CHANGE, ToolChangeFake)
                tool._confirm_profile_resume = MagicMock(return_value=True)
                reply = [line % -2.0 if '%' in line else line for line in self.TPW_REPLY]
                self.run_tool_change(tool, fake, command, lambda t: self.gui_thread(t._on_resume_button), reply)
                self.assertEqual(fake.written.count(b'~'), 1)
                self.assertIn(b'G0 Z-10\n', fake.written)
                self.assertEqual(ended_how(tool), 'finished')
                tool._confirm_profile_resume.assert_not_called()

    def test_tool_change_without_a_profile_resumes_without_a_check(self):
        from tests.test_grbl_job_control import ToolChangeFake
        from tests.grbl_stream_helpers import make_stream_tool, ended_how
        fake = ToolChangeFake()
        tool = make_stream_tool(fake, source=self.TOOL_CHANGE)
        tool._confirm_profile_resume = MagicMock()
        self.run_tool_change(tool, fake, 'G43.1 Z-15', lambda t: t._on_resume_button())
        self.assertEqual(fake.written.count(b'~'), 1)
        self.assertEqual(ended_how(tool), 'finished')
        tool._confirm_profile_resume.assert_not_called()

    def test_no_profile_leaves_the_job_lines_unchanged(self):
        from tests.grbl_stream_helpers import make_stream_tool
        tool = make_stream_tool(FakeGrbl(), source=self.MARKED)
        tool._confirm_profile_stream = MagicMock()
        tool.on_start()
        self.assertEqual(tool.job_lines, ['G21', 'G90', 'G0 X10 Y10', 'G0 X0 Y0', 'M6', 'G1 X20 Y10 F100'])
        tool._confirm_profile_stream.assert_not_called()

    def test_sender_label_names_the_profile(self):
        import tempfile
        from appGUI.machine_profile_ui import update_sender_label
        for correction, text in ((True, 'Machine profile: Cal (checked / corrected)'),
                                 (False, 'Machine profile: Cal (checked)')):
            with self.subTest(correction=correction), tempfile.TemporaryDirectory() as folder:
                tool, _fake = self.make_tool(folder, correction)
                label = MagicMock()
                tool.ui.machine_profile_label = label
                tool.app.grbl_sender_tool = tool
                update_sender_label(tool.app)
                label.setText.assert_called_with(text)

    # the controller of make_tool has the work Z zero at machine Z -20: PRB reports the machine Z
    Z_OFFSET = 20.0

    def test_probe_commands_the_mapped_points_and_stores_the_heights_under_the_grid_points(self):
        import tempfile
        from appCommon.machine_profile.facts import active_profile_of
        from appCommon.machine_profile.output import calibration_hash
        for correction in (True, False):
            with self.subTest(correction=correction), tempfile.TemporaryDirectory() as folder:
                shift = 0.05 if correction else 0.0
                heights = {(round(5.0 + shift, 3), 5.0): 0.3, (round(10.0 + 2 * shift, 3), 10.0): 0.4}
                tool, fake = self.make_tool(folder, correction, levelled=True, heights=heights)
                tool.on_grbl_autolevel()
                self.assertEqual(fake.probe_count, 9)
                moves = [cmd for cmd in fake.commands() if cmd.startswith('G0 X')]
                self.assertIn('G0 X%.4f Y5.0000' % (5.0 + shift), moves)
                self.assertEqual('G0 X5.0000 Y5.0000' in moves, not correction)
                by_xy = {(v['point'].x, v['point'].y): v['height'] for v in tool.al_voronoi_geo_storage.values()}
                self.assertAlmostEqual(by_xy[(5.0, 5.0)], 0.3 + self.Z_OFFSET)       # under the grid point
                self.assertAlmostEqual(by_xy[(10.0, 10.0)], 0.4 + self.Z_OFFSET)
                self.assertTrue(tool.al_heights_valid)
                self.assertEqual(tool._al_map_calibration, calibration_hash(active_profile_of(tool.app)))
                self.assertEqual(tool._al_map_calibration is not None, correction)

    def test_probe_outside_the_bed_after_the_mapping_moves_nothing(self):
        import tempfile
        for correction in (True, False):
            with self.subTest(correction=correction), tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_tool(folder, correction, levelled=True, bed=8.0)
                tool.on_grbl_autolevel()
                self.assertEqual(fake.probe_count, 0)
                self.assertFalse([cmd for cmd in fake.commands() if cmd.startswith('G0')])
                self.assertTrue([m for m in tool.app.inform.emit.call_args_list if 'probe not started' in str(m)])
                self.assertIsNone(tool._grbl_op)

    def test_levelled_stream_refuses_a_map_of_another_calibration(self):
        import tempfile
        for correction, map_calibration, refused in ((True, 'aaaaaaaaaaaa', True), (False, 'aaaaaaaaaaaa', True),
                                                     (True, None, True), (False, None, False)):
            with self.subTest(correction=correction, map_calibration=map_calibration), \
                    tempfile.TemporaryDirectory() as folder:
                tool, fake = self.make_tool(folder, correction, levelled=True)
                tool._al_map_frame = ('G54', (0.0, 0.0, -20.0), False)
                tool._al_map_calibration = map_calibration
                tool._confirm_profile_stream = MagicMock(return_value=True)
                tool.on_start()
                self.assertEqual(self.streamed(fake) == [], refused)
                texts = [str(m) for m in tool.app.inform.emit.call_args_list]
                self.assertEqual(bool([t for t in texts if 'calibration' in t and 'Probe again' in t]), refused)

    def test_levelled_move_past_the_z_travel_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_tool(folder, False, levelled=True)
            tool._al_map_frame = ('G54', (0.0, 0.0, -20.0), False)
            for value in tool.al_voronoi_geo_storage.values():
                if (value['point'].x, value['point'].y) == (10.0, 10.0):
                    value['height'] = -200.0
            tool._confirm_profile_stream = MagicMock(return_value=True)
            tool.on_start()
            self.assertEqual(self.streamed(fake), [])
            texts = [str(m) for m in tool.app.inform.emit.call_args_list]
            self.assertTrue([t for t in texts if 'not started' in t and 'outside the travel' in t], texts)

    def test_save_levelled_gcode_writes_the_corrected_checked_text(self):
        import tempfile
        for correction in (True, False):
            with self.subTest(correction=correction), tempfile.TemporaryDirectory() as folder:
                tool, _fake = self.make_stream(folder, correction, self.MARKED)
                target = os.path.join(folder, 'levelled.nc')
                with patch('appPlugins.ToolGcodeSender.Sender.FCFileSaveDialog.get_saved_filename',
                           return_value=(target, '')):
                    tool.on_save_levelled_gcode()
                with open(target) as f:
                    written = f.read()
                self.assertEqual('X10.1000' in written, correction)
                self.assertIn('M6', written)
        with tempfile.TemporaryDirectory() as folder:
            tool, _fake = self.make_stream(folder, True, self.OUT_OF_BED)
            target = os.path.join(folder, 'levelled.nc')
            with patch('appPlugins.ToolGcodeSender.Sender.FCFileSaveDialog.get_saved_filename',
                       return_value=(target, '')):
                tool.on_save_levelled_gcode()
            self.assertFalse(os.path.exists(target))

    def test_homing_is_not_blocked_by_the_correction(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_tool(folder, True)
            tool.on_grbl_homing()
            self.assertIn('$H', fake.commands())
            self.assertFalse(self.refused(tool))

    def test_jog_is_not_blocked_by_the_correction(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            tool, fake = self.make_tool(folder, True)
            tool.ui.jog_step_entry.get_value.return_value = 1.0
            tool.ui.jog_fr_entry.get_value.return_value = 100.0
            tool.on_grbl_jog('xplus')
            self.assertTrue(any(cmd.startswith('$J=') for cmd in fake.commands()), fake.commands())
            self.assertFalse(self.refused(tool))


if __name__ == '__main__':
    unittest.main()
