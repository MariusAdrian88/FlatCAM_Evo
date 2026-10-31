#!/usr/bin/env python
"""
The GRBL Sender plugin must construct with the real __init__ and fill its UI.

Run: .venv/Scripts/python.exe -m pytest tests/test_grbl_sender_construct.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6 import QtWidgets, QtGui, QtCore, QtTest
from shapely import Point, box

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from defaults import AppDefaults
from appPlugins.ToolGcodeSender.Sender import ToolGCodeSender


class FakeCollection(QtGui.QStandardItemModel):
    """The object combo needs a real item model; the sender asks it for objects by name."""

    def __init__(self):
        super().__init__()
        self.insertRows(0, 5)

    def get_by_name(self, name):
        return None

    def get_active(self):
        return None


def make_app():
    app = MagicMock()
    app.collection = FakeCollection()
    app.decimals = 4
    app.options = dict(AppDefaults.factory_defaults)
    app.defaults = app.options
    app.resource_location = 'assets/resources'
    app.app_units = 'MM'
    return app


class TestGrblSenderConstruct(unittest.TestCase):

    def test_avoidance_combo_lists_only_excellon_and_follows_checkbox(self):
        app = make_app()
        app.collection.setItem(1, QtGui.QStandardItem('Excellon'))
        app.collection.setItem(3, QtGui.QStandardItem('CNCJob'))
        app.collection.item(1).appendRow(QtGui.QStandardItem('drills'))
        app.collection.item(3).appendRow(QtGui.QStandardItem('job'))
        tool = ToolGCodeSender(app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            tool._populate_tool_ui()
        combo = tool.ui.avoid_exc_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], ['drills'])
        for checked in (True, False):
            tool.ui.avoid_exc_holes_cb.setChecked(checked)
            for widget in (combo, tool.ui.avoid_exc_label, tool.ui.avoid_exc_holes_size_entry):
                self.assertEqual(widget.isHidden(), not checked)
        app.collection.item(1).appendRow(QtGui.QStandardItem('other drills'))
        self.assertEqual(combo.count(), 2)

    def test_fit_view_state_restores_in_a_new_sender(self):
        app = make_app()
        app.defaults = dict(app.options)
        tool = ToolGCodeSender(app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            tool._populate_tool_ui()
        self.assertTrue(tool.ui.fit_view_cb.isChecked())
        tool.ui.fit_view_cb.setChecked(False)
        self.assertFalse(app.defaults['tools_gcode_fit_view'])
        restarted_app = make_app()
        restarted_app.options.update(app.defaults)
        restarted = ToolGCodeSender(restarted_app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            restarted._populate_tool_ui()
        self.assertFalse(restarted.ui.fit_view_cb.isChecked())
        layout = next(restarted.ui.tools_box.itemAt(i).layout()
                      for i in range(restarted.ui.tools_box.count())
                      if restarted.ui.tools_box.itemAt(i).layout() is not None and
                      restarted.ui.tools_box.itemAt(i).layout().indexOf(restarted.ui.obj_combo_label) >= 0)
        self.assertIs(layout.itemAt(0).widget(), restarted.ui.obj_combo_label)
        self.assertIsNotNone(layout.itemAt(1).spacerItem())
        self.assertIs(layout.itemAt(2).widget(), restarted.ui.fit_view_cb)
        self.assertIs(layout.itemAt(3).widget(), restarted.ui.console_cb)


class TestSelectedExcellonAvoidance(unittest.TestCase):

    def make_tool(self):
        from tests.test_levelling_audit import make_audit_tool

        tool = make_audit_tool()
        selected = SimpleNamespace(kind='excellon', obj_options={'plot': False},
                                   solid_geometry=Point(5, 5).buffer(0.5))
        other = SimpleNamespace(kind='excellon', obj_options={'plot': True},
                                solid_geometry=Point(8, 8).buffer(0.5))
        tool.app.collection.get_list.return_value = [selected, other]
        tool.app.collection.get_by_name.side_effect = {'drills': selected, 'other': other}.get
        tool.ui.avoid_exc_combo.get_value.return_value = 'drills'
        tool.ui.avoid_exc_holes_cb.get_value.return_value = True
        tool.ui.probe_tip_dia_entry.get_value.return_value = 0.2
        tool.solid_geo = box(0, 0, 10, 10)
        tool.mouse_events_connected = True
        tool.plot_probing_geo = MagicMock()
        return tool

    def test_only_selected_object_is_checked_even_when_not_plotted(self):
        tool = self.make_tool()
        self.assertFalse(tool.check_point_over_excellon(Point(5, 5).buffer(0.1), True).is_empty)
        self.assertTrue(tool.check_point_over_excellon(Point(8, 8).buffer(0.1), True).is_empty)
        tool.ui.avoid_exc_combo.get_value.return_value = 'other'
        self.assertFalse(tool.check_point_over_excellon(Point(8, 8).buffer(0.1), True).is_empty)
        self.assertTrue(tool.check_point_over_excellon(Point(5, 5).buffer(0.1), True).is_empty)
        self.assertTrue(tool.check_point_over_excellon(Point(8, 8).buffer(0.1), False).is_empty)

    def test_manual_points_use_selected_object(self):
        tool = self.make_tool()
        for xy, expected_count in (((5, 5), 0), ((8, 8), 1)):
            tool.app.plotcanvas.translate_coords.return_value = xy
            tool.app.geo_editor.snap.return_value = xy
            tool.on_mouse_click_release(SimpleNamespace(pos=xy, button=1))
            self.assertEqual(len(tool.al_voronoi_geo_storage), expected_count)

    def test_grid_points_use_selected_object(self):
        tool = self.make_tool()
        tool.ui.avoid_exc_holes_size_entry.get_value.return_value = 0.5
        tool.ui.al_columns_entry.get_value.return_value = 11
        tool.ui.al_rows_entry.get_value.return_value = 11
        tool.ui.al_method_radio.get_value.return_value = 'b'
        tool.app.dec_format.side_effect = lambda value, decimals: round(value, decimals)
        tool.generate_bilinear_geometry = MagicMock()
        tool.probing_gcode = MagicMock(return_value='')
        tool.on_add_grid_points()
        points = [(v['point'].x, v['point'].y) for v in tool.al_voronoi_geo_storage.values()]
        self.assertNotIn((5, 5), points)
        self.assertIn((8, 8), points)

    def test_missing_or_wrong_object_blocks_generation_and_manual_clicks(self):
        for name in ('', 'deleted', 'job'):
            with self.subTest(name=name):
                tool = self.make_tool()
                tool.ui.avoid_exc_combo.get_value.return_value = name
                tool.ui.object_combo.get_value.return_value = 'job'
                job = SimpleNamespace(kind='cncjob', gcode_parsed=[{'kind': ['C'], 'geom': tool.solid_geo}])
                tool.app.collection.get_by_name.side_effect = lambda n: job if n == 'job' else None
                tool.al_voronoi_geo_storage = {1: {'point': Point(1, 1), 'geo': None, 'height': 0.0}}
                tool.on_add_grid_points = MagicMock()
                tool.on_add_manual_points = MagicMock()
                self.assertEqual(tool.on_add_al_probepoints(), 'fail')
                tool.on_add_grid_points.assert_not_called()
                tool.on_add_manual_points.assert_not_called()
                tool.app.plotcanvas.translate_coords.return_value = (5, 5)
                tool.app.geo_editor.snap.return_value = (5, 5)
                tool.mouse_events_connected = True
                tool.on_mouse_click_release(SimpleNamespace(pos=(5, 5), button=1))
                self.assertEqual(len(tool.al_voronoi_geo_storage), 1)
                self.assertIn('Excellon', str(tool.app.inform.emit.call_args))


class TestBaudratePersists(unittest.TestCase):

    def populate(self, app):
        tool = ToolGCodeSender(app)
        with patch('appPlugins.Common.levelling_core.ShapeCollection'):
            tool._populate_tool_ui()
        return tool

    def test_custom_rate_is_added_and_selected(self):
        app = make_app()
        app.options["tools_grbl_baudrate"] = '256000'
        tool = self.populate(app)
        combo = tool.ui.baudrates_list_combo
        self.assertEqual(combo.currentText(), '256000')
        self.assertEqual([combo.itemText(i) for i in range(combo.count())].count('256000'), 1)

    def test_selection_is_saved(self):
        app = make_app()
        tool = self.populate(app)
        tool.ui.baudrates_list_combo.setCurrentText('9600')
        self.assertEqual(app.options["tools_grbl_baudrate"], '9600')

    def test_added_rate_is_saved(self):
        app = make_app()
        tool = self.populate(app)
        # 250000 is not in the default list, so the button adds it
        tool.ui.new_baudrate_entry.set_value(250000)
        tool.on_grbl_add_baudrate()
        self.assertEqual(app.options["tools_grbl_baudrate"], '250000')


if __name__ == '__main__':
    unittest.main()
