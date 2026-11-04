"""Focused completion-order checks; no rendering or worker barriers required."""

import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtCore, QtGui, QtWidgets

from appObjects.AppObject import ObjectLoadBatch
from appHandlers.appPlotManager import AppPlotManager


class FileOpened(QtCore.QObject):
    opened = QtCore.pyqtSignal(str, str)


class TestResponsiveBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = QtWidgets.QWidget()
        self.tree = QtWidgets.QTreeView()
        self.model = QtGui.QStandardItemModel()
        self.tree.setModel(self.model)
        self.collection = Mock()
        self.collection.view = self.tree
        self.collection.get_active.return_value = None
        self.collection.get_list.return_value = []
        self.window.notebook = Mock()
        self.window.properties_tab = object()
        self.window.set_ui_title = Mock()
        self.app = SimpleNamespace(
            ui=self.window, collection=self.collection, log=Mock(), regFK=Mock(),
            on_notebook_tab_changed=Mock(), on_zoom_fit=Mock(), flush_recent_items=Mock(),
            plotcanvas=Mock())
        self.owner = QtCore.QObject()
        self.owner.app = self.app
        self.owner.load_context = threading.local()
        self.owner.load_batches = set()
        self.owner.on_object_created = Mock()
        self.finished = Mock()
        self.batch = ObjectLoadBatch(self.owner, 1, self.finished)

    def tearDown(self):
        self.batch.timer.stop()
        self.batch.progress.close()
        self.window.close()
        self.tree.close()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def test_worker_completion_does_not_finish_before_insertion_and_plot(self):
        obj = SimpleNamespace(obj_options={'name': 'layer'})

        def integrate(*args):
            self.batch.plots += 1

        self.owner.on_object_created.side_effect = integrate

        def create():
            self.assertIs(self.owner.load_context.batch, self.batch)
            self.batch.batch_event.emit('created', (obj, True, True, None, []))

        self.batch.run(create, [])
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.MetaCall)
        self.finished.assert_not_called()
        self.batch.integrate_next()
        self.finished.assert_not_called()
        self.app.on_zoom_fit.assert_not_called()
        self.batch.on_event('plotted', None)
        self.finished.assert_called_once_with(False)
        self.app.on_zoom_fit.assert_called_once_with()
        self.collection.set_active.assert_called_once_with('layer')
        self.batch.check_finished()
        self.finished.assert_called_once()

    def test_empty_failed_and_insertion_error_batches_finish(self):
        self.batch.on_event('error', 'parse failure')
        self.batch.on_event('done', None)
        self.finished.assert_called_once_with(True)
        self.assertFalse(self.owner.load_batches)

    def test_project_final_state_waits_for_gui_completion_even_when_empty(self):
        from appHandlers.appIO import appIO
        from appMain import App

        # Finish the setUp batch before starting the independent project batch.
        self.batch.on_event('done', None)
        self.app.app_obj = self.owner
        self.app.should_we_save = True
        self.app.project_filename = 'empty.FlatPrj'
        signals = FileOpened()
        self.app.file_opened = signals.opened
        opened = Mock()
        signals.opened.connect(opened)
        self.app.recent = []
        self.app.recent_projects = []
        self.app.options = {'global_recent_limit': 10}
        self.app.defaults = {}
        signals.opened.connect(lambda kind, filename: App.register_recent(self.app, kind, filename))
        self.app.flush_recent_items.reset_mock()
        snapshots = []
        self.app.flush_recent_items.side_effect = lambda: snapshots.append(list(self.app.recent_projects))
        self.app.proc_container = SimpleNamespace(new=lambda text: nullcontext())
        tasks = []
        self.app.worker_task = SimpleNamespace(emit=tasks.append)
        self.app.inform = Mock()
        self.app.splash = None
        self.app.app_units = 'MM'
        self.app.new_project_signal = Mock()
        self.app.history = Mock()
        io = appIO(self.app)
        io.restore_project_objects({'objs': []}, 'empty.FlatPrj', False, False)
        tasks[0]['fcn'](*tasks[0]['params'])
        self.assertTrue(self.app.should_we_save)
        opened.assert_not_called()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.MetaCall)
        self.assertFalse(self.app.should_we_save)
        opened.assert_called_once_with('project', 'empty.FlatPrj')
        self.app.flush_recent_items.assert_called_once_with()
        self.assertEqual(snapshots, [[{'kind': 'project', 'filename': 'empty.FlatPrj'}]])

    def test_plot_all_fits_once_after_out_of_order_tasks_including_failure(self):
        manager = AppPlotManager.__new__(AppPlotManager)
        QtCore.QObject.__init__(manager)
        manager.plot_finished.connect(manager.on_plot_finished)
        manager.log = Mock()
        manager.app_obj = SimpleNamespace(object_plotted=Mock())
        # the collection property reads app.collection
        manager.app = SimpleNamespace(collection=Mock())
        objects = [SimpleNamespace(kind='gerber', obj_options={'plot': True}, plot=Mock()) for i in range(3)]
        hidden = SimpleNamespace(obj_options={'plot': False}, plot=Mock())
        manager.collection.get_list.return_value = objects + [hidden]
        manager.proc_container = SimpleNamespace(new=lambda text: nullcontext())
        tasks = []
        manager.worker_task = SimpleNamespace(emit=tasks.append)
        manager.plot_all(muted=True)
        self.assertEqual(len(tasks), 3)
        for index in (2, 0):
            tasks[index]['fcn'](*tasks[index]['params'])
            manager.app_obj.object_plotted.emit.assert_not_called()
        objects[1].plot.side_effect = RuntimeError('plot failure')
        with self.assertRaises(RuntimeError):
            tasks[1]['fcn'](*tasks[1]['params'])
        manager.app_obj.object_plotted.emit.assert_called_once()
        hidden.plot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
