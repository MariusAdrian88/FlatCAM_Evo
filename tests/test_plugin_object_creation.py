"""Plugin creation options are applied on successful GUI-thread completion."""

import os
import importlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets

from appTool import AppTool
from appObjects.AppObject import AppObject
from defaults import AppDefaults


class PluginObjectCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app = SimpleNamespace(
            decimals=4, options={}, defaults=AppDefaults.factory_defaults,
            collection=Mock(), ui=Mock(), log=Mock(), inform=Mock(), worker_task=Mock(),
            all_objects_list=[]
        )
        self.app.ui.notebook.currentWidget.return_value = self.app.ui.plugin_tab
        self.source = SimpleNamespace(obj_options={'name': 'source', 'plot': True}, visible=True)
        self.obj = SimpleNamespace(
            obj_options={'name': 'result'}, kind='geometry', has_plot_colors=False, status_color=None
        )

    def complete(self, hide, switch, source=True):
        self.obj._plugin_creation_context = (self.source if source else None, hide, switch)
        holder = SimpleNamespace(app=self.app)
        AppObject.on_object_created(holder, self.obj, False, False, None, [])

    def test_success_hides_only_primary_source_and_switches_after_selection(self):
        reference = SimpleNamespace(obj_options={'plot': True}, visible=True)
        self.complete(True, True)
        self.assertFalse(self.source.obj_options['plot'])
        self.assertFalse(self.source.visible)
        self.assertTrue(reference.visible)
        self.app.collection.set_active.assert_called_once_with('result')
        self.app.ui.notebook.setCurrentWidget.assert_called_once_with(self.app.ui.properties_tab)


    def test_failed_initialization_does_not_hide_or_switch(self):
        tool = AppTool(self.app)
        tool.ui = SimpleNamespace()
        tool.add_object_creation_controls()
        self.app.app_obj = Mock()
        self.app.app_obj.new_object.side_effect = lambda kind, name, init, **kw: init(self.obj, self.app)
        self.assertEqual(tool.new_object('geometry', 'bad', lambda obj, app: 'fail',
                                         source=self.source), 'fail')
        self.assertTrue(self.source.visible)
        self.app.ui.notebook.setCurrentWidget.assert_not_called()


    def test_worker_creation_uses_captured_options_on_gui_thread_only_after_success(self):
        class Result(QtCore.QObject):
            tool_option_prefix = None
            inherits_mill_options = False
            has_plot_colors = False
            status_color = None
            kind = 'geometry'

            def __init__(self, name, app):
                super().__init__()
                self.obj_options = {'name': name}

            def bounds(self):
                return 0, 0, 1, 1

        self.app.options['units'] = 'MM'
        self.app.main_thread = QtCore.QThread.currentThread()
        self.app.on_plots_updated = Mock()
        self.app.inform_shell = Mock()
        self.app.app_obj = AppObject(self.app)
        self.app.collection.append.side_effect = lambda obj: self.assertIs(
            QtCore.QThread.currentThread(), self.app.main_thread)
        tool = AppTool(self.app)
        tool.ui = SimpleNamespace()
        tool.add_object_creation_controls()
        results = []
        with patch('appObjects.AppObject.GeometryObject', Result):
            for failed in (True, False):
                worker = threading.Thread(target=lambda: results.append(tool.new_object(
                    'geometry', 'result', lambda obj, app: 'fail' if failed else None,
                    source=self.source, plot=False, autoselected=False)))
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertTrue(self.source.visible)
                if failed:
                    self.assertEqual(results[-1], 'fail')
                    self.qt_app.processEvents()
                    self.app.collection.append.assert_not_called()
                    self.app.ui.notebook.setCurrentWidget.assert_not_called()
            tool.ui.object_autohide_cb.set_value(False)
            tool.ui.object_autoswitch_cb.set_value(False)
            self.qt_app.processEvents()
        self.assertFalse(self.source.visible)
        self.app.collection.set_active.assert_called_once_with('result')
        self.app.ui.notebook.setCurrentWidget.assert_called_once_with(self.app.ui.properties_tab)

if __name__ == '__main__':
    unittest.main()
