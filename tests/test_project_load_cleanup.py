"""Project parse failures must clean up on the GUI thread."""

import json
import os
import tempfile
import threading
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from PyQt6 import QtCore, QtGui, QtWidgets, QtTest

from appHandlers.appIO import appIO
from defaults import AppOptions


class ProjectSignals(QtCore.QObject):
    new_project = QtCore.pyqtSignal()
    restore = QtCore.pyqtSignal(object, str, bool, bool, bool, bool)
    objects = QtCore.pyqtSignal(object, str, bool, bool)
    opened = QtCore.pyqtSignal(str, str)


class TestProjectLoadCleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.window = QtWidgets.QWidget()
        self.addCleanup(self.window.close)
        self.window.set_ui_title = Mock()
        self.signals = ProjectSignals()
        self.tasks = []
        self.app = SimpleNamespace(
            log=Mock(), inform=Mock(), splash=Mock(), app_units='MM', preferencesUiManager=Mock(),
            options=AppOptions(version='test', baseline={}, defaults={}), defaults={},
            ui=self.window, new_project_signal=self.signals.new_project,
            restore_project=self.signals.restore, restore_project_objects_sig=self.signals.objects,
            worker_task=SimpleNamespace(emit=self.tasks.append),
            proc_container=SimpleNamespace(new=lambda text: nullcontext()),
            project_filename='previous.FlatPrj', resource_location='assets/resources',
            app_obj=SimpleNamespace(load_batches=set()), history=Mock())
        self.io = appIO(self.app)
        self.signals.restore.connect(self.io.restore_project_handler)
        self.io.on_file_new_project = Mock()
        # an interactive load shows a message box on a failure
        critical = patch.object(QtWidgets.QMessageBox, 'critical')
        self.critical = critical.start()
        self.addCleanup(critical.stop)
        warning = patch.object(QtWidgets.QMessageBox, 'warning')
        self.warning = warning.start()
        self.addCleanup(warning.stop)
        self.done = []
        self.io.project_load_done.connect(lambda ok, name, failed: self.done.append((ok, name, failed)))

    def wait_for(self, predicate):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not predicate() and timer.elapsed() < 2000:
            QtTest.QTest.qWait(1)
        self.assertTrue(predicate(), 'GUI completion timed out')

    def parse(self, text, unreadable=False, request_from_worker=False):
        filename = os.path.join(self.directory.name, 'project.FlatPrj')
        with open(filename, 'w') as stream:
            stream.write(text)
        if request_from_worker:
            request = threading.Thread(target=lambda: self.io.open_project(filename, cli=None))
            request.start()
            request.join(2)
            self.assertFalse(request.is_alive())
            self.assertFalse(self.tasks)
        else:
            self.io.open_project(filename)
        self.wait_for(lambda: bool(self.tasks))
        task = self.tasks.pop()
        errors = []

        def run():
            try:
                task['fcn'](*task['params'])
            except Exception as error:
                errors.append(error)

        if unreadable:
            with patch('builtins.open', side_effect=PermissionError('denied')):
                worker = threading.Thread(target=run)
                worker.start()
                worker.join(2)
        else:
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)

    def test_worker_request_starts_and_cleans_up_on_gui_thread(self):
        gui_thread = QtCore.QThread.currentThread()
        title_threads = []
        self.window.set_ui_title.side_effect = lambda **kwargs: title_threads.append(QtCore.QThread.currentThread())
        self.parse('bad project', request_from_worker=True)
        self.wait_for(lambda: len(title_threads) >= 2)
        self.assertEqual(title_threads, [gui_thread, gui_thread])

    def test_bad_project_keeps_the_current_project_and_leaves_no_batch(self):
        cases = (('not JSON or LZMA', False), ('{}', True), ('[]', False), ('{}', False),
                 ('{"objs":null,"options":{}}', False), ('{"objs":[null],"options":{}}', False),
                 ('{"objs":[],"options":[]}', False))
        for text, unreadable in cases:
            with self.subTest(text=text, unreadable=unreadable):
                self.window.set_ui_title.reset_mock()
                self.parse(text, unreadable=unreadable)
                # the failure restores the title of the current project on the GUI thread
                self.wait_for(lambda: self.window.set_ui_title.call_args_list[-1:] == [call(name='previous.FlatPrj')])
                self.assertEqual(self.app.project_filename, 'previous.FlatPrj')
                self.assertFalse(self.app.app_obj.load_batches)
                # one result for each request, with the reason shown to the user
                self.assertEqual([item[0] for item in self.done], [False])
                self.assertEqual(self.critical.call_count, 1)
                self.done.clear()
                self.critical.reset_mock()


    def restore_objects(self, objs):
        """Restore objects with a factory that builds simple objects. Non-interactive: no dialogs."""
        class Obj:
            def __init__(self, kind, name):
                self.kind = kind
                self.obj_options = {}
                if kind != 'script':
                    self.tools = {}

            def from_dict(self, d):
                if d.get('boom'):
                    raise ValueError('bad data')
                self.obj_options = dict(d['obj_options'])
                if hasattr(self, 'tools'):
                    self.tools = d.get('tools', {})

            def on_options_change(self, *args):
                pass

        created = []

        def new_object(kind, name, initialize, **kwargs):
            obj = Obj(kind, name)
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            created.append((name, obj))
            return obj

        self.app.app_obj = SimpleNamespace(load_batches=set(), new_object=new_object)
        self.app.file_opened = self.signals.opened
        self.app.should_we_save = False
        self.app.project_filename = None
        self.io.restore_project_objects({'objs': objs, 'options': {}}, 'board.FlatPrj', True, False)
        task = self.tasks.pop()
        task['fcn'](*task['params'])
        return created

    def test_script_and_bad_objects_do_not_stop_the_load(self):
        opened = Mock()
        self.signals.opened.connect(opened)
        created = self.restore_objects([
            {'kind': 'script', 'obj_options': {'name': 'run'}},
            {'kind': 'gerber', 'boom': True, 'obj_options': {'name': 'broken'}},
            {'kind': 'gerber'},
            {'kind': 'document', 'obj_options': {'name': 'notes'}},
            {'kind': 'gerber', 'obj_options': {'name': 'top'}, 'tools': {'10': {}}},
        ])
        self.assertEqual([name for name, _obj in created], ['run', 'notes', 'top'])
        self.assertEqual(list(created[2][1].tools), [10])
        self.assertEqual(self.done, [(True, 'board.FlatPrj', ['broken', '#3'])])
        # the file is not the project file: a Save must ask for a path; the recent list still gets it
        self.assertIsNone(self.app.project_filename)
        self.assertTrue(self.app.should_we_save)
        self.assertEqual(opened.call_count, 1)


    def test_empty_project_parses_and_finishes_batch(self):
        from appObjects.AppObject import AppObject

        tree = QtWidgets.QTreeView()
        # An empty real selection model is enough; no object UI is constructed.
        model = QtGui.QStandardItemModel()
        tree.setModel(model)
        self.addCleanup(tree.close)
        self.app.collection = Mock()
        self.app.collection.view = tree
        self.app.collection.get_list.return_value = []
        self.app.collection.get_active.return_value = None
        self.app.regFK = Mock()
        self.app.flush_recent_items = Mock()
        self.app.on_notebook_tab_changed = Mock()
        self.app.on_zoom_fit = Mock()
        self.app.on_plots_updated = Mock()
        self.app.file_opened = self.signals.opened
        self.app.app_obj = AppObject(self.app)
        self.window.notebook = Mock()
        self.window.properties_tab = object()
        self.signals.objects.connect(self.io.restore_project_objects)
        opened = Mock()
        self.signals.opened.connect(opened)
        dialog = Mock()
        no = object()
        dialog.clickedButton.return_value = no
        with patch('appHandlers.appIO.FCMessageBox', return_value=dialog):
            dialog.addButton.side_effect = [object(), no]
            self.app.should_we_save = True
            self.parse(json.dumps({'objs': [], 'options': {}}))
            self.wait_for(lambda: bool(self.tasks))
            self.assertTrue(self.app.should_we_save)
            task = self.tasks.pop()
            worker = threading.Thread(target=lambda: task['fcn'](*task['params']))
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.wait_for(lambda: not self.app.app_obj.load_batches)
            self.assertFalse(self.app.should_we_save)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.app.flush_recent_items.call_count, 1)

if __name__ == '__main__':
    unittest.main()
