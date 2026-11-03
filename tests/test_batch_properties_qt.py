"""Real collection/Qt event-loop coverage without a rendering canvas."""

import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6 import QtCore, QtWidgets, QtTest

from appGUI.MainGUI import MainGUI
from appHandlers.appUIActions import AppUIActions
from appHandlers.appObjectOps import AppObjectOps
from appObjects.AppObject import AppObject, ObjectLoadBatch
from appObjects.ObjectCollection import ObjectCollection


class Defaults(dict):
    @property
    def factory_defaults(self):
        return self


class Signals(QtCore.QObject):
    status = QtCore.pyqtSignal(object, str, str)
    selection = QtCore.pyqtSignal(object, object)


class Window(QtWidgets.QWidget):
    def keyPressEvent(self, event):
        MainGUI.keyPressEvent(self, event)


class Layer(QtCore.QObject):
    kind = 'gerber'
    status_color = None
    has_plot_colors = False
    tool_option_prefix = None
    inherits_mill_options = False

    def __init__(self, name, app):
        super().__init__()
        self.app = app
        self.obj_options = {'name': name, 'plot': True}
        self.builds = 0
        self.build_threads = []

    def from_dict(self, data):
        self.obj_options = dict(data['obj_options'])
        self.tools = {}

    def on_options_change(self, key):
        pass

    def bounds(self):
        return 0.0, 0.0, 1.0, 1.0

    def ui_type(self, app):
        return QtWidgets.QLabel(self.obj_options['name'])

    def set_ui(self, ui):
        self.ui = ui
        # GerberObject.set_ui intentionally builds once; append builds again.
        self.build_ui()

    def build_ui(self):
        self.builds += 1
        self.build_threads.append(QtCore.QThread.currentThread())
        self.app.ui.properties_scroll_area.takeWidget()
        self.app.ui.properties_scroll_area.setWidget(self.ui)


class TestBatchPropertiesQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = Window()
        self.window.resize(700, 400)
        self.window.notebook = QtWidgets.QTabWidget()
        self.window.project_tab = QtWidgets.QWidget()
        self.window.properties_tab = QtWidgets.QWidget()
        self.window.notebook.addTab(self.window.project_tab, 'Project')
        self.window.notebook.addTab(self.window.properties_tab, 'Properties')
        self.window.properties_scroll_area = QtWidgets.QScrollArea()
        QtWidgets.QVBoxLayout(self.window.properties_tab).addWidget(self.window.properties_scroll_area)
        self.window.menuobjects = QtWidgets.QMenu()
        self.window.splitter = Mock()
        self.signals = Signals()
        defaults = Defaults(
            global_allow_edit_in_project_tab=False, global_project_autohide=False,
            gerber_plot_on_select=False,
            global_proj_item_color_light='#000000FF', global_proj_item_dis_color_light='#808080FF',
            global_proj_item_color_dark='#FFFFFFFF', global_proj_item_dis_color_dark='#808080FF')
        self.app = SimpleNamespace(
            ui=self.window, options=defaults, defaults=defaults, resource_location='assets/resources',
            log=Mock(), inform=Mock(), regFK=Mock(), worker_task=Mock(),
            object_status_changed=self.signals.status, proj_selection_changed=self.signals.selection,
            on_plots_updated=Mock(), on_zoom_fit=Mock(), flush_recent_items=Mock(), history=Mock(),
            call_source='app')
        self.window.app = self.app
        self.app.collection = ObjectCollection(self.app)
        self.app.app_obj = AppObject(self.app)
        self.actions = SimpleNamespace(app=self.app, ui=self.window)
        self.app.on_notebook_tab_changed = lambda: AppUIActions.on_notebook_tab_changed(self.actions)
        self.app.setup_default_properties_tab = self.show_default
        self.window.notebook.currentChanged.connect(self.app.on_notebook_tab_changed)
        layout = QtWidgets.QHBoxLayout(self.window)
        layout.addWidget(self.app.collection.view)
        layout.addWidget(self.window.notebook)
        self.window.show()
        self.show_default()
        self.batches = []

    def tearDown(self):
        for batch in self.batches:
            if not batch.completed:
                batch.timer.stop()
                batch.progress.close()
        self.window.close()

    def show_default(self):
        self.window.properties_scroll_area.takeWidget()
        label = QtWidgets.QLabel('default')
        label.setObjectName('default_properties')
        self.window.properties_scroll_area.setWidget(label)

    def wait_for(self, predicate):
        timer = QtCore.QElapsedTimer()
        timer.start()
        # the limit is long for the parallel gate on a busy machine; a pass returns at once
        while not predicate() and timer.elapsed() < 10000:
            QtTest.QTest.qWait(1)
        self.assertTrue(predicate(), 'Qt integration timed out')

    def layer(self, name):
        obj = Layer(name, self.app)
        self.app.collection.append(obj)
        self.assertEqual(obj.builds, 2)
        return obj

    def select(self, obj):
        self.app.collection.set_exclusive_active(obj.obj_options['name'])

    def assert_properties(self, obj):
        self.assertIs(self.app.collection.get_active(), obj)
        self.assertIs(self.window.properties_scroll_area.widget(), obj.ui)
        self.assertEqual(obj.ui.text(), obj.obj_options['name'])


    def test_bulk_delete_detaches_rows_before_cleanup_and_redraws_once(self):
        collection = self.app.collection
        objects = [self.layer(str(i)) for i in range(12)]
        survivor = objects[5]
        selected = [obj for obj in objects if obj is not survivor]
        self.window.plot_tab_area = QtWidgets.QTabWidget()
        self.app.plotcanvas = Mock()
        self.app.tool_shapes = Mock()
        self.app.delete_selection_shape = Mock()
        self.app.exc_areas = Mock()
        self.app.defaults.report_usage = Mock()
        self.app.setup_default_properties_tab = Mock(wraps=self.show_default)
        removed = QtTest.QSignalSpy(collection.rowsRemoved)
        statuses = QtTest.QSignalSpy(self.signals.status)
        persistent = QtCore.QPersistentModelIndex(collection.index(
            5, 0, collection.index(0, 0, QtCore.QModelIndex())))
        for obj in selected:
            obj.mark_shapes_storage = Mock()
            obj.mark_shapes = Mock()
            obj.clear = Mock(side_effect=lambda update=False: self.assertEqual(collection.get_list(), [survivor]))
            obj.delete = Mock()
            tab = QtWidgets.QWidget()
            tab.setObjectName(obj.obj_options['name'] + '_editor_tab')
            self.window.plot_tab_area.addTab(tab, obj.obj_options['name'])
            collection.set_active(obj.obj_options['name'])

        AppObjectOps(self.app).on_delete(force_deletion=True)

        self.assertEqual(collection.get_list(), [survivor])
        self.assertEqual(collection.get_selected(), [])
        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.row(), 0)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(statuses), len(selected))
        self.assertEqual(self.window.plot_tab_area.count(), 0)
        self.app.plotcanvas.redraw.assert_called_once_with()
        self.app.tool_shapes.clear.assert_called_once_with(update=True)
        self.app.setup_default_properties_tab.assert_called_once_with()
        self.app.regFK.register_keywords.assert_called_once_with(self.app.regFK.myKeywords)
        self.assertEqual(self.app.all_objects_list, [survivor])
        self.assertTrue(self.app.should_we_save)
        for obj in selected:
            obj.clear.assert_called_once_with()
            obj.delete.assert_called_once_with()
            obj.mark_shapes.clear.assert_called_once_with(update=False)
            self.assertFalse(obj.mark_shapes.enabled)


    def test_delete_confirmation_cancel_does_not_remove_objects(self):
        first, second = self.layer('first'), self.layer('second')
        self.app.collection.set_all_active()
        self.app.defaults.report_usage = Mock()
        with patch('appGUI.GUIElements.FCMessageBox') as message_box:
            AppObjectOps(self.app).on_delete()
        message_box.return_value.exec.assert_called_once_with()
        self.assertEqual(self.app.collection.get_list(), [first, second])
        self.assertEqual(self.app.collection.get_selected(), [first, second])


    def test_object_with_the_history_uid_of_another_object_gets_a_new_one(self):
        first, second = Layer('first', self.app), Layer('second', self.app)
        first.history_uid = second.history_uid = 'same'
        self.app.collection.append(first)
        self.app.collection.append(second)
        self.assertEqual(first.history_uid, 'same')
        self.assertNotEqual(second.history_uid, 'same')

    def test_cli_project_waits_for_real_appobject_gui_insertion_without_progress(self):
        from appHandlers.appIO import appIO

        self.app.options['units'] = 'MM'
        self.app.main_thread = QtCore.QThread.currentThread()
        self.app.inform_shell = Mock()
        self.app.splash = None
        self.app.app_units = 'MM'
        self.app.new_project_signal = Mock()
        self.app.proc_container = SimpleNamespace(new=lambda text: nullcontext())
        self.app.file_opened = Mock()
        self.app.should_we_save = True
        tasks = []
        self.app.worker_task = SimpleNamespace(emit=tasks.append)
        io = appIO(self.app)
        project = {'objs': [{'kind': 'gerber', 'obj_options': {'name': 'cli_layer', 'plot': False}}]}
        with patch('appObjects.AppObject.GerberObject', Layer):
            io.restore_project_objects(project, 'cli.FlatPrj', True, False)
            batch = io.findChildren(ObjectLoadBatch)[0]
            self.assertIs(batch.parent(), io)
            self.assertIsNone(batch.progress)
            self.assertEqual(tasks[0]['params'], [])
            worker = threading.Thread(target=tasks[0]['fcn'])
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.app.collection.get_list(), [])
        self.app.file_opened.emit.assert_not_called()
        self.wait_for(lambda: batch.completed)
        self.assertFalse(batch.failed)
        obj = self.app.collection.get_by_name('cli_layer')
        self.assertIsInstance(obj, Layer)
        self.assertEqual(obj.builds, 2)
        self.assertTrue(all(thread is QtCore.QThread.currentThread() for thread in obj.build_threads))
        self.assertEqual(self.app.all_objects_list, [obj])
        self.app.file_opened.emit.assert_called_once_with('project', 'cli.FlatPrj')
        self.assertFalse(self.app.should_we_save)
        self.app.on_zoom_fit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
