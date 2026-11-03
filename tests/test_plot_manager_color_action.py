import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appHandlers.appPlotManager import AppPlotManager


class ColorActionApp(QtCore.QObject):
    def __init__(self, selected):
        super().__init__()
        for name in ('log', 'inform', 'worker_task', 'plotcanvas', 'clear_pool',
                     'app_obj', 'proc_container'):
            setattr(self, name, Mock())
        self.options = {}
        self.defaults = {'gerber_plot_fill': '#00FF00BF', 'gerber_plot_line': '#006600BF'}
        self.collection = Mock()
        self.collection.get_selected.return_value = [selected]
        self.collection.get_list.return_value = [selected]
        self.collection.group_items = {'gerber': Mock()}
        self.plot_manager = AppPlotManager(self)
        self.errors = []

    def on_set_color_action_triggered(self):
        try:
            self.plot_manager.on_set_color_action_triggered()
        except Exception as error:
            self.errors.append(error)


class PlotManagerColorActionTests(unittest.TestCase):
    def test_action_through_app_facade_updates_selected_object(self):
        qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        selected = SimpleNamespace(kind='excellon', alpha_level='BF', shapes=Mock())
        app = ColorActionApp(selected)
        action = QtGui.QAction('Red', app)
        action.triggered.connect(app.on_set_color_action_triggered)

        action.trigger()

        self.assertEqual(app.errors, [])
        self.assertEqual(selected.fill_color, '#FF0000BF')
        selected.shapes.redraw.assert_called_once_with(
            update_colors=(selected.fill_color, selected.outline_color))
        self.assertEqual(app.options['excellon_color'],
                         (selected.outline_color, selected.fill_color))


if __name__ == '__main__':
    unittest.main()
