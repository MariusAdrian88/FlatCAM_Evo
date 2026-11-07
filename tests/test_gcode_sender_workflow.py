"""GCode sender startup, preview workers and probe-before-stream workflow."""
import os
import sys
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


if __name__ == '__main__':
    unittest.main()
