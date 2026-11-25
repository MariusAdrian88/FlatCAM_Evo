


# ======================================================================
# merged from tests/test_batch_properties_qt.py
# ======================================================================
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


# ======================================================================
# merged from tests/test_plugin_object_creation.py
# ======================================================================
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


class PluginMenuReinstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_reinstall_keeps_one_entry_in_a_menu_other_than_plugins(self):
        from appMain import App
        app = SimpleNamespace(decimals=4, plugin_menu_actions=[], app_plugins=[], kept_sender=None,
                              ui=SimpleNamespace(menu_plugins=QtWidgets.QMenu(), menuoptions=QtWidgets.QMenu()))
        for _round in range(3):     # start + 2 new projects: init_tools() = remove_tools() + install_tools()
            App.remove_tools(app)
            plugin = AppTool(app)
            plugin.pluginName = 'Transform'
            plugin.install(pos=app.ui.menuoptions, separator=True)
            AppTool(app).install()
        options = app.ui.menuoptions.actions()
        self.assertEqual([act.text() for act in options if not act.isSeparator()], ['Transform'])
        self.assertEqual(len(options), 2)
        self.assertEqual(len(app.ui.menu_plugins.actions()), 1)


class PluginReinstallReleaseTests(unittest.TestCase):
    """New Project / open / Close All call init_tools(): the replaced plugins must not stay alive."""

    @classmethod
    def setUpClass(cls):
        from appMain import App
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = App(qapp=cls.qt_app, headless=True)

    def test_init_tools_releases_the_replaced_plugins(self):
        import gc
        import weakref
        app = self.app
        old = [weakref.ref(plugin) for plugin in app.app_plugins]
        self.assertIsNone(app.init_tools(init_tcl=False))
        for _round in range(3):
            self.qt_app.processEvents()
            gc.collect()
        # also covers the machine-stop registry: a Sender still registered there would be alive
        self.assertEqual([ref() for ref in old if ref() is not None], [])


class SenderKeptOnResetTests(unittest.TestCase):
    """A connected GCode sender survives New Project / open / Close All when the preference or the user says so."""

    @classmethod
    def setUpClass(cls):
        from appMain import App
        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = App(qapp=cls.qt_app, headless=True)

    def setUp(self):
        from tests.test_levelling_audit import FakeGrbl
        self.fake = FakeGrbl()
        sender = self.app.grbl_sender_tool
        sender.grbl_ser_port = self.fake
        sender._grbl_connected = True
        sender._grbl_identity_pending = False       # a stopped machine of an earlier test left it set
        self.old_ui = sender.ui
        self.app.options['tools_grbl_keep_on_reset'] = True

    def tearDown(self):
        sender = self.app.grbl_sender_tool
        sender.ui = self.old_ui
        sender._grbl_connected = False
        sender.grbl_ser_port = None
        self.app.options['tools_grbl_keep_on_reset'] = True

    def _reset(self):
        self.assertIsNone(self.app.init_tools(init_tcl=False))

    def test_not_connected_sender_is_replaced(self):
        old = self.app.grbl_sender_tool
        old._grbl_connected = False
        self.assertEqual(self.app.decide_machine_on_reset(), 'replace')
        self._reset()
        self.assertIsNot(self.app.grbl_sender_tool, old)

    def test_connected_sender_is_kept_with_preference_on(self):
        old = self.app.grbl_sender_tool
        self.assertEqual(self.app.decide_machine_on_reset(), 'keep')
        self._reset()
        self.assertIs(self.app.grbl_sender_tool, old)
        self.assertIn(old, self.app.app_plugins)
        self.assertTrue(old.machine_connected())
        self.assertNotIn(b'', self.fake.written)      # no soft reset
        self.assertEqual(len([a for _m, a in self.app.plugin_menu_actions
                              if a.text().startswith(old.pluginName)]), 1)

    def test_kept_sender_still_streams(self):
        old = self.app.grbl_sender_tool
        self.app.decide_machine_on_reset()
        self._reset()
        old.ui = MagicMock()        # the panel is closed in a headless run
        lines = ['G1 X1.000 Y0.000 F100', 'G1 X2.000 Y0.000 F100']
        old.job_lines = lines
        old.job_line_numbers = [1, 2]
        op = old._grbl_reserve('stream', motion=True)
        self.assertIsNotNone(op)
        how = old._grbl_stream_run(op, lines, [1, 2], False, 0.02, None)
        old._grbl_release(op)
        self.assertEqual(how, 'finished')
        self.assertTrue(any(b'X2.000' in item for item in self.fake.written if isinstance(item, bytes)) or
                        any('X2.000' in str(item) for item in self.fake.written))

    def test_off_cancel_resets_nothing(self):
        self.app.options['tools_grbl_keep_on_reset'] = False
        old = self.app.grbl_sender_tool
        with patch.object(self.app, 'headless', False),                 patch.object(self.app, '_ask_machine_on_reset', return_value='cancel'):
            self.assertEqual(self.app.decide_machine_on_reset(), 'cancel')
        self.assertTrue(old.machine_connected())
        self.assertIs(self.app.grbl_sender_tool, old)

    def test_off_stop_disconnects_and_replaces(self):
        self.app.options['tools_grbl_keep_on_reset'] = False
        old = self.app.grbl_sender_tool
        with patch.object(self.app, 'headless', False),                 patch.object(self.app, '_ask_machine_on_reset', return_value='stop'):
            self.assertEqual(self.app.decide_machine_on_reset(), 'stop')
        self._reset()
        self.assertFalse(old.machine_connected())
        self.assertIsNot(self.app.grbl_sender_tool, old)

    def test_headless_off_stops_without_a_popup(self):
        self.app.options['tools_grbl_keep_on_reset'] = False
        with patch.object(self.app, '_ask_machine_on_reset') as ask:
            self.assertEqual(self.app.decide_machine_on_reset(), 'stop')
        ask.assert_not_called()

    def _panel_replaced(self, answer=None):
        """Another plugin takes the Plugin tab while the Sender shows in it; the Sender hides afterwards."""
        from appTool import AppTool
        sender = self.app.grbl_sender_tool
        sender.ui = MagicMock()         # the panel is not built in a headless run
        sender.ui.al_toolbar.indexOf.return_value = -1      # the real toolbar answers a tab index, not a mock
        scroll = self.app.ui.plugin_scroll_area
        scroll.setWidget(sender)
        try:
            with patch.object(self.app, 'headless', answer is None), \
                    patch.object(self.app, '_ask_machine_on_reset', return_value=answer):
                allowed = AppTool.confirm_panel_change(self.app)
            if allowed:
                scroll.takeWidget()
                sender.on_plugin_cleanup()
            return allowed
        finally:
            scroll.takeWidget()

    def test_replaced_panel_keeps_a_connected_machine(self):
        sender = self.app.grbl_sender_tool
        self.assertTrue(self._panel_replaced())
        self.assertTrue(sender.machine_connected())
        with patch.object(self.app, 'inform') as inform:
            op = sender._grbl_reserve('stream', motion=True)     # the job can still run
        self.assertIsNotNone(op, inform.emit.call_args_list)

    def test_off_cancel_keeps_the_panel_and_the_machine(self):
        self.app.options['tools_grbl_keep_on_reset'] = False
        sender = self.app.grbl_sender_tool
        self.assertFalse(self._panel_replaced('cancel'))
        self.assertTrue(sender.machine_connected())

    def test_off_stop_stops_the_machine_when_the_panel_goes(self):
        self.app.options['tools_grbl_keep_on_reset'] = False
        sender = self.app.grbl_sender_tool
        self.assertTrue(self._panel_replaced('stop'))
        self.assertFalse(sender.machine_connected())

    def test_project_switch_with_caching_keeps_a_connected_machine(self):
        projects = self.app.projects
        sender = self.app.grbl_sender_tool
        sender.ui = MagicMock()
        self.app.options['global_project_caching'] = True
        other = projects.add_context('Board_B')
        try:
            with patch.object(projects, 'activate'), patch.object(projects, 'deactivate'), \
                    patch.object(projects, 'before_switch'):
                self.assertTrue(projects.switch(other))
            sender.on_plugin_cleanup()          # the hide that follows the switch
            self.assertTrue(sender.machine_connected())
        finally:
            self.app.options['global_project_caching'] = False


# ======================================================================
# merged from tests/test_responsive_batch_executor.py
# ======================================================================
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


# ======================================================================
# merged from tests/test_custom_origin_responsiveness.py
# ======================================================================
"""Custom Origin must keep dialogs/widgets/camera on the GUI thread."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely import affinity
from shapely.geometry import box

from appCommon.Common import LoudDict
from appHandlers.appEdit import appEditor


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Layer_m4:
    def __init__(self, x, y, events, threads, block=None):
        self.geometry = box(x, y, x + 2.0, y + 3.0)
        self.events = events
        self.threads = threads
        self.block = block
        self.gui_thread = QtCore.QThread.currentThread()
        self.offset_entry = QtWidgets.QLineEdit()
        self.obj_options = LoudDict()
        self.obj_options['name'] = 'layer'
        self.obj_options.set_change_callback(lambda key: self.record('metadata'))
        self.source_file = 'original'

    def record(self, phase):
        self.events.append(phase)
        self.threads.append((phase, QtCore.QThread.currentThread()))

    def bounds(self):
        self.record('bounds')
        return self.geometry.bounds

    def offset(self, vector):
        self.record('offset')
        self.geometry = affinity.translate(self.geometry, xoff=vector[0], yoff=vector[1])

    def plot(self):
        self.record('plot')
        self.plotted_geometry = self.geometry

    def regenerate_source(self):
        self.record('export_start')
        if self.block is not None:
            entered, release = self.block
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test did not release exporter')
        self.source_file = self.geometry.wkt
        self.record('export_done')

    def set_offset_values(self, bounds=None):
        self.record('ui')
        # Record the old unsafe call without making a native cross-thread Qt
        # widget write in the failing regression run.
        if QtCore.QThread.currentThread() is self.gui_thread:
            self.offset_entry.setText(str(bounds if bounds is not None else self.bounds()))


class Worker(QtCore.QThread):
    def __init__(self, task):
        super().__init__()
        self.task = task
        self.errors = []

    def run(self):
        try:
            self.task['fcn'](*self.task['params'])
        except Exception as error:
            self.errors.append(error)


def setup_operation():
    app = make_app()
    app.resource_location = 'assets/resources'
    app.should_we_save = False
    events, threads, tasks = [], [], []
    app.inform.emit.side_effect = events.append
    app.worker_task.emit.side_effect = tasks.append
    controller = appEditor(app)
    app.edit_class = controller
    return app, controller, events, threads, tasks


@pytest.mark.parametrize('mode, origin', [
    ('bl', (10.0, 5.0)), ('c', (18.5, 19.0)),
])
def test_corner_and_center_modes_keep_group_offsets_and_explicit_inline_execution(qt_app, mode, origin):
    app, controller, events, threads, tasks = setup_operation()
    objects = [Layer_m4(10.0, 30.0, events, threads), Layer_m4(25.0, 5.0, events, threads)]
    before = [obj.geometry for obj in objects]
    app.collection.get_selected.return_value = objects
    with patch('appHandlers.appEdit.DialogBoxChoice', return_value=SimpleNamespace(ok=True, location_point=mode)):
        controller.on_custom_origin(use_thread=False)
    assert tasks == []
    for obj, original in zip(objects, before):
        expected = affinity.translate(original, xoff=-origin[0], yoff=-origin[1])
        assert obj.geometry.equals_exact(expected, 1e-9)
        assert obj.plotted_geometry.equals_exact(expected, 1e-9)
        assert obj.source_file == expected.wkt
        assert tuple(obj.obj_options[key] for key in ('xmin', 'ymin', 'xmax', 'ymax')) == expected.bounds
        assert obj.offset_entry.text() == str(expected.bounds)
    assert all(thread is QtCore.QThread.currentThread() for phase, thread in threads)
    app.plotcanvas.fit_view.assert_called_once_with()
    app.delete_selection_shape.assert_called_once_with()
    app.app_obj.object_changed.emit.assert_not_called()
    assert app.should_we_save is True


def test_regeneration_failure_refreshes_bounds_but_does_not_report_success(qt_app):
    app, controller, events, threads, tasks = setup_operation()
    obj = Layer_m4(10.0, 30.0, events, threads)
    obj.regenerate_source = Mock(side_effect=ValueError('export failed'))
    app.collection.get_selected.return_value = [obj]
    with patch('appHandlers.appEdit.DialogBoxChoice', return_value=SimpleNamespace(ok=True, location_point='bl')):
        controller.on_custom_origin(use_thread=False)
    assert 'export failed' in app.inform.emit.call_args.args[0]
    assert not any('Origin set' in event for event in events)
    assert obj.obj_options['xmin'] == 0.0
    app.plotcanvas.fit_view.assert_not_called()


# ======================================================================
# merged from tests/test_move2origin_gerbers.py
# ======================================================================
"""Exercise Move2Origin geometry, plot payloads and actual Gerber regeneration."""

from unittest.mock import Mock, patch

import pytest
from shapely import affinity
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appHandlers.appObjectOps import AppObjectOps
from appObjects.GerberObject import GerberObject
from tests.helpers_objects import make_app as make_app_h, make_gerber, NATIVE_GBR  # noqa: F401,E402


@pytest.mark.parametrize('threaded', [False, True])
@pytest.mark.parametrize('positions', [
    pytest.param([(10.0, 30.0), (25.0, 5.0)], id='positions1'),
])
def test_selected_gerbers_move_as_group_with_matching_plot_and_source(positions, threaded, tmp_path):
    from PyQt6 import QtWidgets

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = make_app_h()
    app.delete_selection_shape = Mock()
    app.f_handlers = appIO(app)
    plotted = {}
    objects = []
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        for index, (x, y) in enumerate(positions):
            obj = make_gerber(app, path=NATIVE_GBR, name='layer_%d' % index)
            xmin, ymin, xmax, ymax = obj.bounds()
            obj.offset((x - xmin, y - ymin))
            obj.obj_options.update(plot=True, follow=False, solid=True, multicolored=False)
            obj.source_file = 'original source'
            objects.append(obj)
            plotted[obj] = []

            def add_batch(batch, target=obj, **kwargs):
                plotted[target].extend(entry['shape'] for entry in batch)

            def add_shape(shape, target=obj, **kwargs):
                plotted[target].append(shape)

            obj.add_shapes_batch = add_batch
            obj.add_shape = add_shape

        before = [unary_union(obj.solid_geometry) for obj in objects]
        before_bounds = [obj.bounds() for obj in objects]
        x = min(bounds[0] for bounds in before_bounds)
        y = min(bounds[1] for bounds in before_bounds)
        app.collection.get_selected.return_value = objects
        ops = AppObjectOps(app)
        ops.on_move2origin(use_thread=threaded)
        if threaded:
            task = app.worker_task.emit.call_args.args[0]
            assert all(obj.bounds() == bounds for obj, bounds in zip(objects, before_bounds))
            task['fcn'](*task['params'])

        for index, obj in enumerate(objects):
            expected = affinity.translate(before[index], xoff=-x, yoff=-y)
            moved = unary_union(obj.solid_geometry)
            # Union may reorder rings after floating-point translation.
            assert moved.hausdorff_distance(expected) < 1e-7
            assert moved.symmetric_difference(expected).area < 1e-7
            displayed = unary_union(plotted[obj])
            assert displayed.hausdorff_distance(expected) < 1e-7
            assert displayed.symmetric_difference(expected).area < 1e-7
            assert tuple(obj.obj_options[key] for key in ('xmin', 'ymin', 'xmax', 'ymax')) == pytest.approx(moved.bounds)
            assert obj.source_file != 'original source'
            filename = tmp_path / ('moved_%d.gbr' % index)
            filename.write_text(obj.source_file)
            parsed = GerberObject('parsed_%d' % index, app)
            assert parsed.parse_file(str(filename)) is None
            assert parsed.bounds() == pytest.approx(moved.bounds, abs=2e-5)

        after_bounds = [obj.bounds() for obj in objects]
        assert min(bounds[0] for bounds in after_bounds) == pytest.approx(0.0)
        assert min(bounds[1] for bounds in after_bounds) == pytest.approx(0.0)
        if len(objects) == 2:
            assert after_bounds[1][0] - after_bounds[0][0] == pytest.approx(before_bounds[1][0] - before_bounds[0][0])
            assert after_bounds[1][1] - after_bounds[0][1] == pytest.approx(before_bounds[1][1] - before_bounds[0][1])
        if threaded:
            app.plotcanvas.fit_view.assert_called_once_with(blocking=False)
        else:
            app.plotcanvas.fit_view.assert_called_once_with()
        app.delete_selection_shape.assert_called_once_with()
        assert app.should_we_save


# ======================================================================
# merged from tests/test_move2origin_responsiveness.py
# ======================================================================
"""Real Qt action dispatch and worker/GUI completion boundaries for group moves."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PyQt6 import QtCore, QtWidgets, QtTest

from appHandlers.appObjectOps import AppObjectOps




def make_app():
    app = Mock()
    app.options = {}
    app.defaults = {}
    app.ui = Mock()
    app.collection.get_selected.return_value = []
    app.proc_container.new.side_effect = lambda text: nullcontext()
    app.ui.pop_menucolor.actions.return_value = []
    return app


class Layer_m6:
    def __init__(self, x, events, threads, entered=None, release=None):
        self.x = x
        self.obj_options = {}
        self.events = events
        self.threads = threads
        self.entered = entered
        self.release = release
        self.offset_entry = QtWidgets.QLineEdit()

    def bounds(self):
        return self.x, 5.0, self.x + 1.0, 6.0

    def offset(self, vector):
        self.threads.append(('offset', QtCore.QThread.currentThread()))
        self.x += vector[0]

    def plot(self):
        self.threads.append(('plot', QtCore.QThread.currentThread()))
        self.events.append('plot')

    def regenerate_source(self):
        self.threads.append(('export', QtCore.QThread.currentThread()))
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError('test did not release exporter')
        self.events.append('export')

    def set_offset_values(self, bounds=None):
        self.threads.append(('ui', QtCore.QThread.currentThread()))
        self.offset_entry.setText(str(bounds if bounds is not None else self.bounds()))
        self.events.append('ui')


class ClickCanvas:
    """The mouse_release handlers of the canvas (graph_event_connect / graph_event_disconnect) and a click."""

    def __init__(self):
        self.handlers = []

    def graph_event_connect(self, name, callback):
        self.handlers.append((name, callback))

    def graph_event_disconnect(self, name, callback=None):
        self.handlers = [handler for handler in self.handlers if handler != (name, callback)]

    def translate_coords(self, pos):
        return pos

    def pixel_size(self):
        return 0.1

    def click(self, button, pos):
        for name, callback in list(self.handlers):
            if name == 'mouse_release':
                callback(SimpleNamespace(button=button, pos=pos))


def test_set_origin_moves_the_objects_once_for_a_double_click(qt_app):
    app = make_app()
    app.defaults = Mock()
    app.replot_signal = MagicMock()
    app.grid_status.return_value = False
    app.plotcanvas = ClickCanvas()
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    layer = Layer_m6(10.0, [], [])
    app.collection.get_list.return_value = [layer]
    AppObjectOps(app).on_set_origin()
    # the second click comes before the worker task of the first click has run
    app.plotcanvas.click(1, (3.0, 4.0))
    app.plotcanvas.click(1, (3.0, 4.0))
    assert len(tasks) == 1
    tasks[0]['fcn'](*tasks[0]['params'])
    assert layer.x == 7.0


class ReplotSignal(QtCore.QObject):
    replot_signal = QtCore.pyqtSignal(list)


def test_set_origin_started_again_before_the_replot_ends_still_takes_the_click(qt_app):
    app = make_app()
    app.defaults = Mock()
    signals = ReplotSignal()
    app.replot_signal = signals.replot_signal
    app.grid_status.return_value = False
    app.plotcanvas = ClickCanvas()
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    layer = Layer_m6(10.0, [], [])
    app.collection.get_list.return_value = [layer]
    ops = AppObjectOps(app)

    ops.on_set_origin()
    app.plotcanvas.click(1, (3.0, 4.0))
    task = tasks.pop(0)
    task['fcn'](*task['params'])    # the offset; it queues the replot
    ops.on_set_origin()             # the user starts Set Origin again before the replot ends
    task = tasks.pop(0)
    task['fcn'](*task['params'])    # the replot of the first Set Origin
    app.plotcanvas.click(1, (1.0, 0.0))
    assert len(tasks) == 1
    tasks[0]['fcn'](*tasks[0]['params'])
    assert layer.x == 6.0


def test_set_origin_click_near_an_object_corner_takes_the_exact_corner(qt_app):
    app = make_app()
    app.defaults = Mock()
    app.replot_signal = MagicMock()
    app.grid_status.return_value = True
    app.geo_editor.snap.return_value = (12.0, 6.0)
    app.plotcanvas = ClickCanvas()
    tasks = []
    app.worker_task.emit.side_effect = tasks.append
    layer = Layer_m6(10.0, [], [])    # box (10, 5) - (11, 6)
    app.collection.get_list.return_value = [layer]
    AppObjectOps(app).on_set_origin()
    app.plotcanvas.click(1, (11.3, 6.2))    # 10 px of 0.1 = 1.0 snap distance
    tasks[0]['fcn'](*tasks[0]['params'])
    assert layer.x == -1.0


# ======================================================================
# merged from tests/test_worker_stack_shutdown.py
# ======================================================================
import unittest
from types import SimpleNamespace

from appWorkerStack import WorkerStack


class FakeThread:
    def __init__(self, name, events, wait_results, running=True):
        self.name = name
        self.events = events
        self.wait_results = list(wait_results)
        self.running = running

    def requestInterruption(self):
        self.events.append((self.name, 'interrupt'))

    def quit(self):
        self.events.append((self.name, 'quit'))

    def wait(self, timeout=None):
        self.events.append((self.name, 'wait', timeout))
        result = self.wait_results.pop(0) if self.wait_results else not self.running
        if result:
            self.running = False
        return result

    def isRunning(self):
        self.events.append((self.name, 'is_running'))
        return self.running

    def terminate(self):
        self.events.append((self.name, 'terminate'))


class TestWorkerStackShutdown(unittest.TestCase):
    @staticmethod
    def make_stack(*threads):
        stack = WorkerStack(workers_number=0)
        stack.threads = list(threads)
        return stack

    def test_forces_thread_that_exceeds_grace_period(self):
        events = []
        thread = FakeThread('blocked', events, wait_results=[False, True])
        stack = self.make_stack(thread)

        stack.quit(timeout_ms=0)

        self.assertIn(('blocked', 'terminate'), events)
        self.assertIn(('blocked', 'wait', 1000), events)


class FakeWorker:
    def __init__(self, name):
        self.name = name
        self.started = []
        self.worker_task_signal = SimpleNamespace(emit=self.started.append)


class TestWorkerStackDispatch(unittest.TestCase):
    @staticmethod
    def make_stack(count):
        stack = WorkerStack(workers_number=0)
        for i in range(count):
            worker = FakeWorker('w%d' % i)
            stack.load[worker.name] = 0
            stack.worker_map[worker.name] = worker
        return stack

    @staticmethod
    def task(name, priority=None):
        task = {'fcn': lambda: None, 'params': [], 'name': name}
        if priority:
            task['priority'] = priority
        return task

    @staticmethod
    def started(stack):
        return [(w.name, [t['fcn'] for t in w.started]) for w in stack.worker_map.values()]

    def test_task_does_not_wait_behind_a_long_task_while_a_worker_is_idle(self):
        stack = self.make_stack(2)
        long_task, short_task, third = (self.task(n) for n in 'abc')
        for task in (long_task, short_task, third):
            stack.add_task(task)
        w0, w1 = stack.worker_map['w0'], stack.worker_map['w1']
        self.assertEqual([t['fcn'] for t in w0.started], [long_task['fcn']])
        self.assertEqual([t['fcn'] for t in w1.started], [short_task['fcn']])
        stack.on_task_completed('w1')  # the long task of w0 still runs
        self.assertEqual([t['fcn'] for t in w1.started], [short_task['fcn'], third['fcn']])
        self.assertEqual(len(w0.started), 1)

    def test_high_task_goes_before_queued_normal_tasks(self):
        stack = self.make_stack(1)
        first, normal, high = self.task('a'), self.task('b'), self.task('c', 'high')
        for task in (first, normal, high):
            stack.add_task(task)
        worker = stack.worker_map['w0']
        stack.on_task_completed('w0')
        self.assertEqual([t['fcn'] for t in worker.started], [first['fcn'], high['fcn']])
