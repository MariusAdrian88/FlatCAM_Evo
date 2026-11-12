"""Tests of the app Undo / Redo history (appHandlers/appHistory.py)."""

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shapely import LineString
from shapely.geometry import Point
from appHandlers.appHistory import (COORD_BYTES, HistoryError, HistoryManager, HistoryStep, SnapshotStep,
                                    invert_matrix, object_state)
from appHandlers.appProjects import ProjectManager
from appObjects.ProjectContext import ProjectContext
from tests.helpers_objects import make_app, make_gerber  # noqa: F401,E402


class FakeStep(HistoryStep):
    def __init__(self, label, log, size=0, uids=(), fail=False):
        self.label = label
        self.size = size
        self._uids = set(uids)
        self.log = log
        self.fail = fail

    def uids(self):
        return self._uids

    def undo(self, history):
        if self.fail:
            raise HistoryError('gone')
        self.log.append(('undo', self.label))

    def redo(self, history):
        self.log.append(('redo', self.label))


def fake_app(**options):
    app = SimpleNamespace(headless=False, call_source='app', inform=Mock(), history_changed=Mock(), log=Mock(),
                          options=dict(options), projects=SimpleNamespace(is_busy=lambda: False))
    return app


def manager(**options):
    app = fake_app(**options)
    history = HistoryManager(app, collection=SimpleNamespace(get_list=lambda: []))
    app.history = history
    return history, app


class TestManager(unittest.TestCase):

    def test_undo_redo_order_and_redo_cleared(self):
        h, _app = manager()
        log = []
        for name in 'abc':
            h.push(FakeStep(name, log))
        self.assertTrue(h.undo())
        self.assertTrue(h.undo())
        self.assertTrue(h.redo())
        self.assertEqual(log, [('undo', 'c'), ('undo', 'b'), ('redo', 'b')])
        self.assertEqual((h.undo_label(), h.redo_label()), ('b', 'c'))
        h.push(FakeStep('d', log))
        self.assertFalse(h.can_redo())
        self.assertFalse(h.redo())

    def test_memory_limit(self):
        h, _app = manager(global_history_memory_mb=1)
        log = []
        for i in range(5):
            h.push(FakeStep(str(i), log, size=400 * 1024))
        # 1 MB holds 2 steps of 400 KB
        self.assertEqual([s.label for s in h.undo_stack], ['3', '4'])
        # a single step bigger than the limit is kept, so the last action can always be undone
        h.push(FakeStep('big', log, size=5 * 1024 * 1024))
        self.assertEqual([s.label for s in h.undo_stack], ['big'])

    def test_group(self):
        h, _app = manager()
        log = []
        with h.group('all'):
            h.push(FakeStep('a', log))
            with h.group('inner'):
                h.push(FakeStep('b', log))
        self.assertEqual(len(h.undo_stack), 1)
        self.assertEqual(h.undo_label(), 'all')
        h.undo()
        self.assertEqual(log, [('undo', 'b'), ('undo', 'a')])
        h.redo()
        self.assertEqual(log[2:], [('redo', 'a'), ('redo', 'b')])

    def test_suspended_and_headless(self):
        h, app = manager()
        log = []
        with h.suspended():
            h.push(FakeStep('a', log))
        self.assertFalse(h.can_undo())
        app.headless = True
        h.push(FakeStep('b', log))
        self.assertFalse(h.can_undo())

    def test_blocked_in_editor_and_busy(self):
        h, app = manager()
        log = []
        h.push(FakeStep('a', log))
        app.call_source = 'geo_editor'
        self.assertFalse(h.undo())
        app.call_source = 'app'
        app.projects.is_busy = lambda: True
        self.assertFalse(h.undo())
        self.assertEqual(log, [])
        self.assertTrue(h.can_undo())

    def test_failing_step_is_retained_for_retry(self):
        h, app = manager()
        log = []
        h.push(FakeStep('ok', log))
        h.push(FakeStep('bad', log, fail=True))
        self.assertFalse(h.undo())
        self.assertEqual(h.undo_label(), 'bad')
        self.assertFalse(h.can_redo())
        self.assertIn('[ERROR_NOTCL]', app.inform.emit.call_args[0][0])
        h.undo_stack[-1].fail = False
        self.assertTrue(h.undo())


    def test_invert_matrix(self):
        m = (0.0, -2.0, 2.0, 0.0, 5.0, -3.0)
        a, b, d, e, xoff, yoff = invert_matrix(m)
        # m then its inverse on the point (1, 1)
        x, y = 0.0 * 1 - 2.0 * 1 + 5.0, 2.0 * 1 + 0.0 * 1 - 3.0
        self.assertAlmostEqual(a * x + b * y + xoff, 1.0)
        self.assertAlmostEqual(d * x + e * y + yoff, 1.0)
        with self.assertRaises(HistoryError):
            invert_matrix((1.0, 2.0, 2.0, 4.0, 0.0, 0.0))


class TestIdentityAndProjects(unittest.TestCase):

    def test_load_preserves_louddict_callback_and_defaults(self):
        for key in ('obj_options', 'options'):
            obj = make_gerber(make_app())
            options = obj.obj_options
            callback = Mock()
            options.set_change_callback(callback)
            options['project_default'] = 7
            obj.from_dict({key: {'name': 'loaded', 'plot': False}})
            self.assertIs(obj.obj_options, options)
            self.assertEqual(options['name'], 'loaded')
            self.assertEqual(options['project_default'], 7)
            options['plot'] = True
            callback.assert_called_with('plot')


class TestMerge(unittest.TestCase):

    def test_snapshots_with_the_same_key_merge(self):
        h, _app = manager()
        h.push(SnapshotStep('Cut Z', [('u1', {'v': 1}, {'v': 2})], merge_key=('cutz', 'u1', 3)))
        h.push(SnapshotStep('Cut Z', [('u1', {'v': 2}, {'v': 3})], merge_key=('cutz', 'u1', 3)))
        self.assertEqual(len(h.undo_stack), 1)
        self.assertEqual(h.undo_stack[0].items, [('u1', {'v': 1}, {'v': 3})])

    def test_other_key_other_objects_or_after_undo_do_not_merge(self):
        h, _app = manager()
        h.push(SnapshotStep('a', [('u1', {}, {})], merge_key='k'))
        h.push(SnapshotStep('b', [('u1', {}, {})], merge_key='other'))
        h.push(SnapshotStep('c', [('u2', {}, {})], merge_key='other'))
        h.push(SnapshotStep('d', [('u2', {}, {})]))
        h.push(SnapshotStep('e', [('u2', {}, {})]))
        self.assertEqual(len(h.undo_stack), 5)

class TestRebuildSaveRename(unittest.TestCase):

    def test_rename_to_a_taken_name_raises(self):
        a = SimpleNamespace(obj_options={'name': 'a'})
        b = SimpleNamespace(obj_options={'name': 'b'})
        collection = SimpleNamespace(get_list=lambda: [a, b], rename_object=Mock())
        h = HistoryManager(fake_app(), collection=collection)
        with self.assertRaises(HistoryError):
            h.rename(a, 'b')
        collection.rename_object.assert_not_called()
        h.rename(a, 'free')
        collection.rename_object.assert_called_once_with(a, 'free')


# ======================================================================
# merged from tests/test_app_history_app.py
# ======================================================================
"""App Undo / Redo history on real objects in a headless App: matrix, snapshot, delete + rebuild, create, rename."""

import os
import re
import sys
import time

import numpy as np
import pytest
from PyQt6 import QtWidgets

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appGUI.GUIElements as gui
import appHandlers.appHistory as appHistory
from appHandlers.appHistory import apply_matrix_to_object
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'unify')

pytestmark = pytest.mark.slow


@pytest.fixture(scope='module')
def fc():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app = App(qapp=qapp, headless=True)
    # the history records nothing in headless mode; these tests need it
    appHistory.RECORD_IN_HEADLESS = True
    yield app
    appHistory.RECORD_IN_HEADLESS = False


@pytest.fixture
def no_plot(monkeypatch):
    monkeypatch.setattr(FlatCAMObj, 'plot', lambda *a, **k: None)
    monkeypatch.setattr(FlatCAMObj, 'plot_transformed', lambda *a, **k: True)
    monkeypatch.setattr(ExcellonObject, 'plot_transformed', lambda *a, **k: True)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()


def _load(fc, kind, filename, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, filename))
        if kind == 'excellon':
            obj.create_geometry()
    return fc.app_obj.new_object(kind, name, init, plot=False)


def _settle(fc, timeout=5.0):
    """Wait for the tasks that an object rebuild starts (the history refuses Undo / Redo while the app is busy)."""
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _bounds(obj):
    return np.array(obj.bounds())


def test_matrix_undo_redo(fc, no_plot):
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_matrix')
    b0 = _bounds(grb)
    m = (0.0, -1.0, 1.0, 0.0, 10.0, 0.0)
    apply_matrix_to_object(grb, m)
    fc.history.record_matrix('rot', [(grb, m)])
    b1 = _bounds(grb)
    assert fc.history.undo() and np.allclose(_bounds(grb), b0)
    assert fc.history.redo() and np.allclose(_bounds(grb), b1)


def test_delete_undo_rebuilds_same_uid_and_later_steps_work(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_del')
    uid, b0 = exc.history_uid, _bounds(exc)
    m = (1.0, 0.0, 0.0, 1.0, 5.0, 5.0)
    apply_matrix_to_object(exc, m)
    fc.history.record_matrix('move', [(exc, m)])
    fc.history.record_delete('del', [exc])
    fc.object_ops.delete_objects_now([exc])
    assert fc.history.find(uid) is None
    assert fc.history.undo()                    # rebuild
    _settle(fc)
    rebuilt = fc.history.find(uid)
    assert rebuilt is not None and rebuilt is not exc
    assert fc.history.undo()                    # the move, on the rebuilt object
    assert np.allclose(_bounds(rebuilt), b0)


def test_create_undo_redo(fc, no_plot):
    count = len(fc.collection.get_list())
    grb = _load(fc, 'gerber', 'unify_mixed.gbr', 'g_create')
    assert fc.history.undo_label().endswith('g_create')
    uid = grb.history_uid
    assert fc.history.undo() and fc.history.find(uid) is None
    assert len(fc.collection.get_list()) == count
    assert fc.history.redo() and fc.history.find(uid) is not None


def _select(fc, monkeypatch, objs):
    monkeypatch.setattr(fc.collection, 'get_selected', lambda: list(objs))


def _fake_spinner(value):
    class _Spinner:
        def __init__(self, *a, **k):
            pass

        def setWindowIcon(self, *a):
            pass

        def get_value(self):
            return value, True
    return _Spinner


@pytest.mark.parametrize('kind,filename', [('gerber', 'unify_mixed.gbr'), ('excellon', 'unify_mixed.drl')])
def test_rotate_30_undo_restores_bounds(fc, no_plot, monkeypatch, kind, filename):
    obj = _load(fc, kind, filename, 'rot30_' + kind)
    b0 = _bounds(obj)
    _select(fc, monkeypatch, [obj])
    fc.object_ops.on_rotate(silent=True, preset=30)
    b1 = _bounds(obj)
    assert not np.allclose(b1, b0)
    # a hole polygon is rotated twice (not rebuilt), so its circle approximation differs by a tiny amount
    assert fc.history.undo() and np.allclose(_bounds(obj), b0, atol=1e-3)
    assert fc.history.redo() and np.allclose(_bounds(obj), b1, atol=1e-3)


def test_transform_tool_group_undo(fc, no_plot, monkeypatch):
    g1 = _load(fc, 'gerber', 'unify_mixed.gbr', 'tt1')
    g2 = _load(fc, 'gerber', 'unify_mixed.gbr', 'tt2')
    _select(fc, monkeypatch, [g1, g2])
    b1, b2 = _bounds(g1), _bounds(g2)
    n = len(fc.history.undo_stack)
    fc.transform_tool.on_rotate_action(30.0, (1.0, 2.0))
    assert len(fc.history.undo_stack) == n + 1
    assert not np.allclose(_bounds(g1), b1)
    assert fc.history.undo()
    assert np.allclose(_bounds(g1), b1, atol=1e-6) and np.allclose(_bounds(g2), b2, atol=1e-6)


def _source_without_time(text):
    return re.sub(r'^(?:G04 |;)Created on :.*$', '', text or '', flags=re.M)


def test_move_tool_undo_redo_regenerates_source(fc, no_plot):
    kind, filename = 'gerber', 'unify_mixed.gbr'
    obj = _load(fc, kind, filename, 'move_source_' + kind)
    assert obj.regenerate_source()
    source0, b0 = _source_without_time(obj.source_file), _bounds(obj)
    fc.move_tool.move_handler((4.0, -2.5), objects=[obj])
    _settle(fc)
    source1 = _source_without_time(obj.source_file)
    assert source1 != source0
    assert fc.history.undo()
    assert np.allclose(_bounds(obj), b0)
    assert _source_without_time(obj.source_file) == source0
    assert fc.history.redo()
    assert _source_without_time(obj.source_file) == source1


def test_failed_source_regeneration_keeps_the_undo_step(fc, no_plot, monkeypatch):
    failure = 'raise'
    obj = _load(fc, 'gerber', 'unify_mixed.gbr', 'move_recovery')
    assert obj.regenerate_source()
    source0, b0 = _source_without_time(obj.source_file), _bounds(obj)
    fc.move_tool.move_handler((4.0, -2.5), objects=[obj])
    _settle(fc)
    source1, b1 = _source_without_time(obj.source_file), _bounds(obj)
    undo_len, redo_len, label = len(fc.history.undo_stack), len(fc.history.redo_stack), fc.history.undo_label()

    original = type(obj).regenerate_source

    def broken(self, *args, **kwargs):
        if failure == 'raise':
            raise RuntimeError('source export failed')
        return False

    monkeypatch.setattr(type(obj), 'regenerate_source', broken)
    try:
        assert fc.history.undo() is False
    finally:
        monkeypatch.setattr(type(obj), 'regenerate_source', original)

    assert np.allclose(_bounds(obj), b1)
    assert _source_without_time(obj.source_file) == source1
    assert len(fc.history.undo_stack) == undo_len
    assert len(fc.history.redo_stack) == redo_len
    assert fc.history.undo_label() == label

    assert fc.history.undo()
    assert np.allclose(_bounds(obj), b0)
    assert _source_without_time(obj.source_file) == source0


# ======================================================================
# merged from tests/test_app_history_keys.py
# ======================================================================
"""Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y drive the app history from MainGUI.keyPressEvent; the view and editor keys."""
import os
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets

from appGUI.MainGUI import MainGUI

CTRL = QtCore.Qt.KeyboardModifier.ControlModifier
SHIFT = QtCore.Qt.KeyboardModifier.ShiftModifier
NO_MODIFIER = QtCore.Qt.KeyboardModifier.NoModifier


def press(key, modifiers, source='app', in_text=False, options=None):
    gui = MagicMock()
    gui.app.call_source = source
    gui.app.history = Mock()
    if options is not None:
        gui.app.options = options
    gui.app_focus_in_text = lambda: in_text
    event = Mock()
    event.native.isAutoRepeat.return_value = False
    event.isAutoRepeat.return_value = False
    event.key = key
    with patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=modifiers):
        MainGUI.keyPressEvent(gui, event)
    return gui


class TestKeysWithoutPlotArea(unittest.TestCase):
    """View -> Toggle Plot Area (Ctrl+F10) can close the last tab: the keys of the Project tab still work."""

    @staticmethod
    def press(key, modifiers, tab_name=None):
        gui = MagicMock()
        gui.app.call_source = 'app'
        if tab_name is None:
            gui.plot_tab_area.currentWidget.return_value = None
        else:
            gui.plot_tab_area.currentWidget.return_value.objectName.return_value = tab_name
        with patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=modifiers):
            # the Project tab sends the key as an int
            MainGUI.keyPressEvent(gui, int(key))
        return gui


class TestEscapeLeavesFullScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        class Window(QtWidgets.QMainWindow):
            on_full_screen_toggled = MainGUI.on_full_screen_toggled

        self.window = Window()
        self.window.app = MagicMock()
        self.window.app.call_source = 'app'
        self.window.app.options = {}
        self.window.app.ui = self.window
        # as MainGUI.__init__ leaves them
        self.window.toggle_f_screen = False
        self.window.x_pos = self.window.y_pos = self.window.titlebar_height = None
        for name in ('coords_toolbar', 'delta_coords_toolbar', 'grid_toolbar', 'status_toolbar'):
            setattr(self.window, name, MagicMock())
        self.toolbar = self.window.addToolBar('File')
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.show()
        self.qapp.processEvents()

    def press_escape(self):
        no_modifier = QtCore.Qt.KeyboardModifier.NoModifier
        with patch.object(QtWidgets.QApplication, 'keyboardModifiers', return_value=no_modifier):
            MainGUI.keyPressEvent(self.window, int(QtCore.Qt.Key.Key_Escape))
        self.qapp.processEvents()

    def test_escape_after_alt_f10(self):
        self.window.on_full_screen_toggled()    # View -> Toggle FullScreen, Alt+F10
        self.assertTrue(self.window.isFullScreen())
        self.assertFalse(self.toolbar.isVisible())
        self.press_escape()
        self.assertFalse(self.window.isFullScreen())
        self.assertTrue(self.toolbar.isVisible())


class TestViewKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        class Window(QtWidgets.QMainWindow):
            eventFilter = MainGUI.eventFilter
            app_focus_in_text = staticmethod(MainGUI.app_focus_in_text)

        self.window = Window()
        self.window.app = MagicMock()
        self.window.app.options = {'global_toggle_tooltips': True}
        self.target = QtWidgets.QPushButton('Canvas / editor / plugin')
        self.window.setCentralWidget(self.target)
        self.actions = {}
        for key, name in (('G', 'grid_lines'), ('A', 'axis'), ('H', 'hud'), ('W', 'workspace')):
            action = QtGui.QAction(name, self.window)
            action.setCheckable(True)
            setattr(self.window, 'menuview_toggle_' + name, action)
            self.actions[key] = action
        self.qapp.installEventFilter(self.window)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.qapp.removeEventFilter, self.window)
        self.window.show()
        self.window.activateWindow()
        self.target.setFocus()
        self.qapp.processEvents()

    def key(self, key, modifiers=SHIFT):
        event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, modifiers)
        return self.window.eventFilter(self.target, event)

    def test_shift_keys_toggle_the_view_in_every_context(self):
        for key, letter in ((QtCore.Qt.Key.Key_G, 'G'), (QtCore.Qt.Key.Key_A, 'A'), (QtCore.Qt.Key.Key_H, 'H'),
                            (QtCore.Qt.Key.Key_W, 'W')):
            for source in ('app', 'geo_editor', 'measurement'):
                self.window.app.call_source = source
                before = self.actions[letter].isChecked()
                self.assertTrue(self.key(key))
                self.assertNotEqual(before, self.actions[letter].isChecked(), letter)
        self.assertFalse(self.key(QtCore.Qt.Key.Key_G, NO_MODIFIER))


# ---------------------------------------------------------------------------------------------------------------------
# Characterization of MainGUI.keyPressEvent: every key with every modifier, from the plot area (a vispy key) and from
# the Project tab (an int), in each context and state, and the calls that it makes. The table was recorded from the
# handler before the keys moved to the registry of appGUI/shortcuts.py (python tests/test_app_history_keys.py --record).
# ---------------------------------------------------------------------------------------------------------------------
import json
import re

from vispy.util import keys as vispy_keys

K = QtCore.Qt.Key
M = QtCore.Qt.KeyboardModifier
CHARACTERIZATION_FILE = os.path.join(os.path.dirname(__file__), 'fixtures', 'shortcut_keys.json')

KEYS = ([getattr(K, 'Key_%s' % c) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'] +
        [getattr(K, 'Key_F%d' % i) for i in range(1, 13)] +
        [K.Key_Escape, K.Key_Delete, K.Key_Space, K.Key_Return, K.Key_Enter, K.Key_Backspace, K.Key_Up, K.Key_Down,
         K.Key_Minus, K.Key_Equal, K.Key_QuoteLeft, K.Key_Tab, K.Key_Plus])
MODIFIERS = {
    'none': M.NoModifier, 'ctrl': M.ControlModifier, 'shift': M.ShiftModifier, 'alt': M.AltModifier,
    'ctrl+shift': M.ControlModifier | M.ShiftModifier, 'ctrl+alt': M.ControlModifier | M.AltModifier,
    'keypad': M.KeypadModifier,
}
# the vispy keys of the plot area, as vispy's Qt backend makes them
_VISPY_SPECIAL = {
    K.Key_Escape: vispy_keys.ESCAPE, K.Key_Delete: vispy_keys.DELETE, K.Key_Space: vispy_keys.SPACE,
    K.Key_Return: vispy_keys.ENTER, K.Key_Enter: vispy_keys.ENTER, K.Key_Backspace: vispy_keys.BACKSPACE,
    K.Key_Up: vispy_keys.UP, K.Key_Down: vispy_keys.DOWN, K.Key_Tab: vispy_keys.TAB,
}
_VISPY_SPECIAL.update({getattr(K, 'Key_F%d' % i): getattr(vispy_keys, 'F%d' % i) for i in range(1, 13)})

# Shift+A, Shift+G, Shift+H and Shift+W never reach keyPressEvent: MainGUI.eventFilter takes them first in every
# context (the 'global' context of the registry; TestViewKeys)
UNREACHABLE = {('app', 'shift', 'A'), ('app', 'shift', 'G'), ('app', 'shift', 'H'), ('app', 'shift', 'W')}

# the calls that only read the state; the record keeps the calls that do something
_READS = ('isChecked', 'currentWidget', 'objectName', 'get_selected', 'get_active', 'get_names', 'isFullScreen',
          'app_focus_in_text', 'currentIndex', 'get_value', 'options.get', 'snap')


def vispy_key(key):
    if key in _VISPY_SPECIAL:
        return _VISPY_SPECIAL[key]
    return vispy_keys.Key(chr(int(key))) if 32 <= int(key) <= 127 else None


class _VispyEvent:
    def __init__(self, key):
        self.key = key
        self.native = Mock()
        self.native.isAutoRepeat.return_value = False

    @staticmethod
    def isAutoRepeat():
        return False


def _key_name(key):
    return K(key).name[4:]


def _states(context):
    """The states that change what a key does in the context: name -> function that sets up the mocked MainGUI."""
    from appEditors.appGeoEditor import FCShapeTool

    def plain(gui):
        pass

    def tab(name):
        def setup(gui):
            gui.plot_tab_area.currentWidget.return_value.objectName.return_value = name
        return setup

    def in_text(gui):
        gui.app_focus_in_text = lambda: True
        gui._focus_in_text = True

    def no_active(gui):
        gui.app.collection.get_active.return_value = None
        gui.app.collection.get_selected.return_value = []

    def tool_idle(editor, button):
        def setup(gui):
            getattr(gui, button).isChecked.return_value = True
        return setup

    def tool_active(editor, button):
        def setup(gui):
            getattr(gui, button).isChecked.return_value = False
        return setup

    def drawing(editor, button, name):
        def setup(gui):
            tool = MagicMock(spec=FCShapeTool)
            tool.name = name
            tool.points = [(0, 0)]
            tool.origin = (0, 0)
            tool.complete = True
            tool.clean_up = MagicMock()
            for attr in ('path_tool', 'polygon_tool', 'drill_tool', 'slot_tool', 'copy_tool'):
                setattr(tool, attr, MagicMock(length=1.0))
            getattr(gui.app, editor).active_tool = tool
            getattr(gui, button).isChecked.return_value = False
        return setup

    def nothing_selected(editor, button):
        def setup(gui):
            getattr(gui.app, editor).get_selected.return_value = None
            getattr(gui.app, editor).selected = []
            getattr(gui, button).isChecked.return_value = True
        return setup

    def list_focus(gui):
        gui._list_focus = True

    if context == 'app':
        return {'plain': plain, 'database_tab': tab('database_tab'), 'text_editor_tab': tab('text_editor_tab'),
                'in_text': in_text, 'no_active': no_active, 'list_focus': list_focus}
    editors = {'geo_editor': ('geo_select_btn', 'path'), 'grb_editor': ('grb_select_btn', 'track'),
               'exc_editor': ('select_drill_btn', 'drill_add')}
    if context in editors:
        button, tool = editors[context]
        return {'select': tool_idle(context, button), 'tool': tool_active(context, button),
                'drawing': drawing(context, button, tool), 'nothing_selected': nothing_selected(context, button),
                'in_text': in_text}
    if context == 'measurement':
        return {'plain': plain, 'in_text': in_text}
    return {'plain': plain}


def _clean(text):
    return re.sub(r" (id='\d+'|at 0x[0-9a-fA-F]+)", '', text)


class _Recorder:
    """A mocked MainGUI in 1 context and state, with the patches that keep a key press inside the mock."""

    def __init__(self, context, state):
        from appGUI import MainGUI as main_module
        self.keyPressEvent = main_module.MainGUI.keyPressEvent
        gui = self.gui = MagicMock()
        gui.app.call_source = context
        gui._focus_in_text = False
        gui._list_focus = False
        gui.app_focus_in_text = lambda: False
        gui.app.resource_location = 'assets/resources'
        gui.decimals = 4
        for editor in ('geo_editor', 'grb_editor'):
            getattr(gui.app, editor).vertex_grips = None      # no Selection Tool drag to cancel
        _states(context)[state](gui)
        for name in ('webbrowser_open', 'FCMessageBox', 'FCInputDoubleSpinner'):
            gui.attach_mock(MagicMock(name=name), 'EXT_' + name)
        gui.EXT_FCInputDoubleSpinner.return_value.get_value.return_value = (1.5, True)
        self.modifiers = M.NoModifier
        from appObjects.ObjectCollection import EventSensitiveListView
        list_view = MagicMock(spec=EventSensitiveListView)
        self.patches = [
            patch.object(QtWidgets.QApplication, 'keyboardModifiers', side_effect=lambda: self.modifiers),
            patch.object(QtWidgets.QApplication, 'focusWidget',
                         side_effect=lambda: (self.line_edit if gui._focus_in_text else
                                              list_view if gui._list_focus else None)),
            patch('webbrowser.open', gui.EXT_webbrowser_open),
        ]
        for module in _modules_with_names():
            for name in ('FCMessageBox', 'FCInputDoubleSpinner'):
                if hasattr(module, name):
                    self.patches.append(patch.object(module, name, getattr(gui, 'EXT_' + name)))

    def __enter__(self):
        self.line_edit = QtWidgets.QLineEdit()
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in reversed(self.patches):
            p.stop()
        self.line_edit.deleteLater()

    def press(self, key, modifiers, from_list):
        """Press 1 key; returns the calls that it made, as text."""
        gui = self.gui
        gui.mock_calls.clear()      # only mock_calls are read; reset_mock() walks every child and is slow
        self.modifiers = modifiers
        event = int(key) if from_list else _VispyEvent(vispy_key(key))
        try:
            result = self.keyPressEvent(gui, event)
        except Exception as err:
            result = 'raised %s' % type(err).__name__
        calls = []
        for c in gui.mock_calls:
            name = c[0]
            if not name or '__' in name or name.endswith(_READS) or name.startswith('EXT_FCMessageBox().set'):
                continue
            calls.append(_clean('%s%s' % (name, c[1:] if (c[1] or c[2]) else '')))
        if result is not None:
            calls.append(_clean('-> %r' % (result,)))
        return calls


def _modules_with_names():
    import importlib
    modules = []
    for name in ('appGUI.MainGUI', 'appGUI.shortcuts', 'appGUI.shortcut_actions'):
        try:
            modules.append(importlib.import_module(name))
        except ImportError:
            pass
    return modules


CONTEXTS = ('app', 'geo_editor', 'grb_editor', 'exc_editor', 'gcode_editor', 'measurement', 'qrcode_tool',
            'copper_thieving_tool', '2_sided_tool', 'geometry', 'fiducials_tool', 'markers_tool')


def record(context):
    """{state: {'modifiers|key|source': [calls]}} of the keys that do something in the context."""
    table = {}
    for state in _states(context):
        rows = {}
        with _Recorder(context, state) as recorder:
            _record_rows(context, recorder, rows)
        table[state] = rows
    return table


def _record_rows(context, recorder, rows):
    for mod_name, modifiers in MODIFIERS.items():
        for key in KEYS:
            if (context, mod_name, _key_name(key)) in UNREACHABLE:
                continue
            for source in ('list', 'canvas'):
                calls = recorder.press(key, modifiers, source == 'list')
                if calls:
                    rows['%s|%s|%s' % (mod_name, _key_name(key), source)] = calls


class TestChangedShortcut(unittest.TestCase):
    def test_a_changed_key_runs_its_action_and_the_old_key_does_not(self):
        from appGUI import shortcuts
        registry = shortcuts.registry()
        registry.apply_overrides({'app.new_gerber': 'Ctrl+Shift+B', 'geo_editor.circle': 'Alt+O'})
        try:
            gui = press(QtCore.Qt.Key.Key_B, CTRL | SHIFT)
            gui.app.app_obj.new_gerber_object.assert_called_once()
            gui = press(QtCore.Qt.Key.Key_B, NO_MODIFIER)
            gui.app.app_obj.new_gerber_object.assert_not_called()
            gui = press('O', M.AltModifier, source='geo_editor')
            gui.app.geo_editor.select_tool.assert_called_once_with('circle')
            gui = press('O', NO_MODIFIER, source='geo_editor')
            gui.app.geo_editor.select_tool.assert_not_called()
        finally:
            registry.reset_all()


class TestShortcutCharacterization(unittest.TestCase):
    """Each key of each context runs what it ran before the keys moved to the registry (with the factory keys)."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with open(CHARACTERIZATION_FILE, encoding='utf-8') as f:
            cls.expected = json.load(f)

    def check(self, context):
        actual = record(context)
        expected = self.expected[context]
        for state in expected:
            diff = {k: (expected[state].get(k), actual[state].get(k))
                    for k in set(expected[state]) | set(actual[state])
                    if expected[state].get(k) != actual[state].get(k)}
            self.assertEqual({}, diff, '%s / %s' % (context, state))

    def test_every_context(self):
        for context in CONTEXTS:
            self.check(context)


# ======================================================================
# merged from tests/test_app_history_plugins.py
# ======================================================================
"""App Undo / Redo history on the in-place changes of the plugins and of the object handlers (headless App)."""

import os
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6 import QtWidgets
from shapely import LineString, Point, box
from shapely.ops import unary_union

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject
from appPlugins.ToolCutOut import CutOut
from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolIsolation import ToolIsolation










def _settle_m3(fc, timeout=5.0):
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)




def _area(obj):
    geo = obj.solid_geometry
    return unary_union(geo if isinstance(geo, list) else [geo]).area


def _geometry(fc, name, multigeo=False):
    """A Geometry object with a closed contour (a 20 x 10 rectangle) and 1 tool."""
    contour = LineString(list(box(0, 0, 20, 10).exterior.coords))

    def init(obj, _app):
        obj.solid_geometry = [contour]
        obj.tools = {1: {'tooldia': 1.0, 'data': {'name': name}, 'solid_geometry': [contour] if multigeo else []}}
        obj.multigeo = multigeo
    return fc.app_obj.new_object('geometry', name, init, plot=False)


def _one_step(fc, before, label_part):
    """The history got exactly 1 new step since `before` (the undo stack length), with `label_part` in its label."""
    assert len(fc.history.undo_stack) == before + 1
    assert label_part in fc.history.undo_label()


def _stack(fc):
    return len(fc.history.undo_stack)


# ---------------------------------------------------------------------------------------------------------------------
# 1. Double-Sided: Mirror
# ---------------------------------------------------------------------------------------------------------------------
def _hole_xy(points):
    return np.asarray([p.coords[0] for p in points])


def _nearest(points, others):
    return np.linalg.norm(points[:, None] - others[None, :], axis=2).min(axis=1)


def _canonical(holes, turn):
    """Holes of a T-B board seen as an L-R board (x and y exchanged)."""
    return holes[:, ::-1] if turn == 'T-B' else holes


def test_dblsided_generated_holes_preserve_coincident_user_drills(fc, no_plot):
    from appPlugins.ToolDblSided import DblSidedGen as gen

    def init(obj, app):
        obj.add_tool(1, drills=[Point(-2, 5), Point(4, 4)], slots=[(Point(1, 1), Point(2, 1))])
        obj.create_geometry()
    exc = fc.app_obj.new_object('excellon', 'dbl_append', init, plot=False)
    original = _bounds(exc)
    with fc.history.snapshot('Alignment Drills', [exc]):
        gen.replace_generated(exc, 1.00001, [Point(-2, 5), Point(22, 5)], fc.decimals)
    assert len(exc.tools) == 1
    assert len(exc.tools[1]['drills']) == 4
    assert len(exc.tools[1]['slots']) == 1
    assert gen.source_view(exc).bounds() == pytest.approx(original)
    matrix = (-1, 0, 0, 1, 20, 0)
    with fc.history.snapshot('Mirror', [exc]):
        appHistory.apply_matrix_to_object(exc, matrix)
        gen.replace_generated(exc, 1, [Point(-3, 5), Point(23, 5)], fc.decimals)
    assert len(exc.tools[1]['drills']) == 4
    assert exc.tools[1]['drills'][0].equals(Point(22, 5))
    assert fc.history.undo()
    assert exc.tools[1]['drills'][0].equals(Point(-2, 5))
    assert fc.history.redo()
    assert exc.tools[1]['drills'][0].equals(Point(22, 5))
    # Project/history round-trip retains ownership independently of coordinates.
    state = appHistory.object_state(exc)
    appHistory.restore_object_state(exc, state)
    gen.replace_generated(exc, 2, [Point(50, 50)], fc.decimals)
    assert len(exc.tools[1]['drills']) == 2
    assert len(exc.tools[1]['slots']) == 1
    assert sum(len(t['drills']) for t in exc.tools.values()) == 3
    assert exc.source_file


def _drill_count(obj):
    return sum(len(tool.get('drills') or ()) for tool in obj.tools.values())


def _dbl_editor_save(drills, prepare=None):
    """An Excellon editor round trip, committed with the tool reconstruction of the real save worker."""
    from unittest.mock import Mock
    from test_exc_editor import qt_editor
    from appEditors.appExcEditor import tools_with_data

    _, editor = qt_editor(drills.units)
    editor.edit_fcexcellon(drills)
    if prepare is not None:
        prepare(editor)
    editor._save_excellon_in_place = Mock()
    editor.update_fcexcellon(drills, in_place=True)
    drills.tools = tools_with_data(drills.tools, editor.new_tools, editor.olddia_newdia,
                                   editor.data_defaults, editor.decimals)
    drills.create_geometry()
    drills.regenerate_source()
    return editor


def test_dblsided_editor_round_trip_keeps_generated_holes_owned(fc, no_plot):
    from appPlugins.ToolDblSided import DblSidedGen as gen

    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_editor')
    tool.ui.auto_align_cb.set_value(False)
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    assert _drill_count(drills) == 43
    user = pads + (7, -3)
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    generated = [p for p in holes if _nearest(np.asarray([p]), user)[0] > 1e-6]
    assert len(generated) == 3

    # A user drill exactly on one of our holes is a separate feature; a save must keep both.
    next(iter(drills.tools.values()))['drills'].append(Point(*generated[0]))
    drills.create_geometry()
    assert drills.regenerate_source()
    assert _drill_count(drills) == 44
    user = np.vstack([user, generated[0]])

    _dbl_editor_save(drills)
    tool.on_process()
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    assert len(holes) == 44
    # only our 3 holes are regenerated; the 41 user drills (1 of them coincident) are kept
    assert _drill_count(gen.source_view(drills)) == 41
    assert _nearest(user, holes) == pytest.approx(np.zeros(len(user)))
    generated = [p for p in holes if _nearest(np.asarray([p]), user)[0] > 1e-6]
    assert len(generated) == 3

    # Deleting 1 generated hole renumbers the others and moving another must not slide the ownership
    # onto a user drill.
    def remove_and_move(editor):
        storage = next(iter(editor.storage_dict.values()))
        removed = next(shape for shape in storage.get_objects()
                       if np.allclose(shape.geo.centroid.coords[0], generated[1]))
        storage.replace(removed=[removed], inserted=[])
        moved_shape = next(shape for shape in storage.get_objects()
                           if np.allclose(shape.geo.centroid.coords[0], generated[2]))
        editor.selected = [moved_shape]
        assert editor.transform_features('rotate', 90) != 'fail'

    editor = _dbl_editor_save(drills, remove_and_move)
    moved = editor.selected[0].geo.centroid.coords[0]
    tool.on_process()
    holes = _hole_xy([p for tool_dict in drills.tools.values() for p in tool_dict['drills']])
    assert len(holes) == 44
    assert not any(np.allclose(hole, moved) for hole in holes)
    assert _nearest(user, holes) == pytest.approx(np.zeros(len(user)))
    assert _drill_count(gen.source_view(drills)) == 41


def test_dblsided_regeneration_keeps_the_object_attribute_ids_of_the_user_holes():
    from types import SimpleNamespace
    from appPlugins.ToolDblSided import DblSidedGen as gen
    tool = {'tooldia': 0.8, 'drills': [Point(0, 0), Point(1, 1), Point(2, 2)], 'slots': [],
            'drills_to': [4, 0, 5], gen.GENERATED: [1]}
    plain = {'tooldia': 1.0, 'drills': [Point(9, 9)], 'slots': []}
    obj = SimpleNamespace(tools={1: tool, 2: plain}, obj_options={}, bounds=lambda: (0, 0, 1, 1),
                          create_geometry=lambda: None, regenerate_source=lambda: True)
    gen.replace_generated(obj, 0.8, [Point(7, 7), Point(8, 8)], 4)
    assert [p.x for p in tool['drills']] == [0, 2, 7, 8]
    # the removed generated hole took its id, the added holes have none, a tool without ids gets no key
    assert tool['drills_to'] == [4, 5, 0, 0]
    assert 'drills_to' not in plain


def test_dblsided_alignment_stages_all_sources_and_rejects_ambiguity():
    from appPlugins.ToolDblSided import DblSidedGen as gen
    from appPlugins.ToolAlignObjects.AlignGen import SnapIndex, AlignError, apply_matrix
    rng = np.random.default_rng(42)
    pads = rng.uniform((0, 0), (50, 30), (40, 2))

    def index(points):
        return SnapIndex([Point(p).buffer(0.8) for p in points], points, [])

    angle = 0.3
    matrix = (np.cos(angle), -np.sin(angle), np.sin(angle), np.cos(angle), 7, -3)
    holes = apply_matrix(matrix, pads)
    bottom = pads + (12, 5)
    plans = gen.alignment_matrices([index(pads), index(bottom), index(holes)], [0, 1], 2)
    assert apply_matrix(plans[2], holes) == pytest.approx(pads, abs=0.01)
    assert apply_matrix(plans[1], bottom) == pytest.approx(pads, abs=0.01)
    assert 0 not in plans
    with pytest.raises(AlignError):
        gen.alignment_matrices([index(pads), index([(0, 0)])], [0], 1)
    square = index([(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(AlignError, match='ambiguous'):
        gen.alignment_matrices([square, square], [0], 1)


def _dbl_sources(fc, name, reflected=False):
    pads = np.random.default_rng(42).uniform((0, 0), (50, 30), (40, 2))

    def gerber(points, suffix):
        def init(obj, app):
            records = [{'solid': Point(p).buffer(0.8), 'follow': Point(p)} for p in points]
            obj.tools = {10: {'type': 'C', 'size': 1.6, 'geometry': records}}
            obj.solid_geometry = [r['solid'] for r in records]
            obj.follow_geometry = [r['follow'] for r in records]
        return fc.app_obj.new_object('gerber', name + suffix, init, plot=False)

    top = gerber(pads, '_top')
    if reflected:
        from appPlugins.ToolAlignObjects.AlignGen import apply_matrix
        angle = 0.3
        bottom_points = apply_matrix((np.cos(angle), -np.sin(angle), np.sin(angle), np.cos(angle), 60, 8),
                                     pads * (-1, 1))
    else:
        bottom_points = pads + (12, 5)
    bottom = gerber(bottom_points, '_bottom')

    def init_drills(obj, app):
        obj.add_tool(0.8, drills=[Point(p) for p in pads + (7, -3)])
        obj.create_geometry()
    drills = fc.app_obj.new_object('excellon', name + '_drills', init_drills, plot=False)
    tool = DblSidedTool(fc)
    tool.set_tool_ui()
    tool.change_level('b')
    for combo, obj in zip(tool.ui.basic_combos, (top, bottom, drills)):
        combo.set_value(obj.obj_options['name'])
    return tool, top, bottom, drills, pads


def _wait_dbl(tool):
    end = time.monotonic() + 10
    while tool.pending_alignment is not None and time.monotonic() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert tool.pending_alignment is None


@pytest.mark.parametrize('composed', [False, True])
def test_dblsided_registered_bounds_ignore_cleared_copper(fc, no_plot, composed):
    from appPlugins.ToolDblSided.DblSidedGen import bounds_drills
    from appPlugins.ToolAlignObjects.AlignGen import apply_matrix
    fc.history.clear()
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_clear_' + str(composed))
    removed = box(100, 5, 110, 15)
    records = bottom.tools[10]['geometry']
    records.extend([{'solid': removed, 'follow': removed.centroid}, {'clear': removed}])
    copper = unary_union([r['solid'] for r in records if 'solid' in r]).difference(removed)
    bottom.solid_geometry = copper if composed else list(copper.geoms)
    bounds = top.bounds()
    flip = (-1, 0, 0, 1, bounds[0] + bounds[2], 0)
    mirrored = apply_matrix(flip, pads)
    holes = _hole_xy(bounds_drills(bounds, 0.8, 2))
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    for _ in range(2):
        tool.on_process()
        _wait_dbl(tool)
        for obj, expected in ((top, pads), (bottom, mirrored)):
            assert np.asarray([r['follow'].coords[0] for r in obj.tools[10]['geometry'][:40]]) == \
                pytest.approx(expected)
            assert obj.bounds() == pytest.approx(bounds)
        actual = np.asarray([p.coords[0] for t in drills.tools.values() for p in t['drills']])
        assert actual.shape == (43, 2)
        assert actual[:40] == pytest.approx(pads)
        assert actual[40:] == pytest.approx(holes)
    tool.deleteLater()


def test_dblsided_process_is_one_undo_and_survives_reload(fc, no_plot):
    tool, top, bottom, drills, pads = _dbl_sources(fc, 'dbl_flip', reflected=True)
    xmin, ymin, xmax, ymax = drills.bounds()
    appHistory.apply_matrix_to_object(drills, (-1, 0, 0, 1, xmin + xmax, 0))
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    originals = [_bounds(obj) for obj in (top, bottom, drills)]
    expected = pads.copy()
    expected[:, 0] = _bounds(top)[0] + _bounds(top)[2] - expected[:, 0]
    n = _stack(fc)
    tool.on_process()
    _wait_dbl(tool)
    _one_step(fc, n, '2-Sided')
    assert np.asarray([rec['follow'].coords[0] for rec in top.tools[10]['geometry']]) == pytest.approx(pads)
    assert np.asarray([p.coords[0] for p in drills.tools[1]['drills'][:40]]) == pytest.approx(pads)
    assert np.asarray([rec['follow'].coords[0] for rec in bottom.tools[10]['geometry']]) == pytest.approx(expected)
    assert len(drills.tools[1]['drills']) == 43
    assert fc.history.undo()
    assert len(drills.tools[1]['drills']) == 40
    for obj, original in zip((top, bottom, drills), originals):
        assert _bounds(obj) == pytest.approx(original)
    assert fc.history.redo()
    assert np.asarray([rec['follow'].coords[0] for rec in bottom.tools[10]['geometry']]) == pytest.approx(expected)
    holes = np.asarray([p.coords[0] for p in drills.tools[1]['drills'][40:]])
    sources = [obj.source_file for obj in (top, bottom, drills)]
    import json
    from camlib import to_dict, dict2obj
    for obj in (top, bottom, drills):
        state = json.loads(json.dumps(appHistory.object_state(obj), default=to_dict), object_hook=dict2obj)
        appHistory.restore_object_state(obj, state)
    tool.set_tool_ui()
    for combo, obj in zip(tool.ui.basic_combos, (top, bottom, drills)):
        combo.set_value(obj.obj_options['name'])
    tool.ui.generate_cb.set_value(True)
    tool.ui.append_drills_cb.set_value(True)
    tool.ui.basic_drill_dia.set_value(0.8)
    tool.on_process()
    _wait_dbl(tool)
    assert np.asarray([rec['follow'].coords[0] for t in bottom.tools.values() for rec in t['geometry']]) == \
        pytest.approx(expected)
    restored_drills = [p.coords[0] for t in drills.tools.values() for p in t['drills']]
    assert len(restored_drills) == 43
    assert np.asarray(restored_drills[40:]) == pytest.approx(holes)
    # the export header holds the time of the export ("Created on ... at HH:MM"): compare without it
    def undated(text):
        return [line for line in (text or '').splitlines() if 'Created on' not in line]

    assert [undated(obj.source_file) for obj in (top, bottom, drills)] == [undated(s) for s in sources]
    bottom.offset((4, -7))
    tool.on_process()
    _wait_dbl(tool)
    # External edits invalidate the manufacturing marker: re-register the actual input, then mirror it once.
    assert np.asarray([rec['follow'].coords[0] for t in bottom.tools.values() for rec in t['geometry']]) == \
        pytest.approx(expected)
    tool.deleteLater()


# ---------------------------------------------------------------------------------------------------------------------
# 3. Cutout: manual gap, 1 step per click
# ---------------------------------------------------------------------------------------------------------------------
def _cutout_stub(fc, geo):
    ui = SimpleNamespace(
        dia=SimpleNamespace(get_value=lambda: 2.0),
        gapsize=SimpleNamespace(get_value=lambda: 1.0),
        gaptype_combo=SimpleNamespace(get_value=lambda: 1),          # thin gaps -> tool 99
        cutz_entry=SimpleNamespace(get_value=lambda: -1.0),
        thin_depth_entry=SimpleNamespace(get_value=lambda: -0.5),
        mpass_cb=SimpleNamespace(get_value=lambda: False),
        maxdepth_entry=SimpleNamespace(get_value=lambda: 0.5))
    fc.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y))
    tool_dict = {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}
    stub = SimpleNamespace(app=fc, ui=ui, man_cutout_obj=geo, cut_tool_dict=tool_dict, mb_manual_solid_geo=None,
                           mb_manual_cuts=[])
    return CutOut, stub


def test_manual_cutout_click_is_a_snapshot_step(fc, no_plot):
    geo = _geometry(fc, 'geo_cut')
    geo.plot = lambda *a, **k: None
    CutOut, stub = _cutout_stub(fc, geo)
    keys0 = sorted(geo.tools)
    n = _stack(fc)
    CutOut.on_manual_cutout(stub, (10.0, 0.0))
    _one_step(fc, n, 'Manual Cutout')
    assert geo.multigeo is True and 99 in geo.tools
    solid1 = geo.tools[1]['solid_geometry']
    assert fc.history.undo()
    assert geo.multigeo is False and sorted(geo.tools) == keys0 and geo.tools[1]['solid_geometry'] == []
    assert fc.history.redo()
    assert geo.multigeo is True and 99 in geo.tools
    assert geo.tools[1]['solid_geometry'].equals(solid1) if hasattr(solid1, 'equals') else True


def test_manual_cutout_session_cancel_and_finish_preserve_history(fc, no_plot):
    from unittest.mock import Mock
    from test_cutout_inner_contours import make_tool

    geo = _geometry(fc, 'geo_cut_session')
    tool = make_tool()
    app = tool.test_app
    app.history = fc.history
    app.collection.objs[geo.obj_options['name']] = geo
    app.ui.keyPressEvent = Mock()
    app.ui.notebook = Mock()
    app.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y), tool_shape=Mock())
    app.on_mouse_click_over_plot = Mock()
    app.on_mouse_click_release_over_plot = Mock()
    app.on_mouse_move_over_plot = Mock()
    app.plotcanvas.translate_coords = lambda pos: pos
    app.plotcanvas.pixel_size = lambda: 0.1
    app.grid_status = lambda: False
    tool.ui.man_object_combo = SimpleNamespace(currentText=lambda: geo.obj_options['name'])
    tool.ui.big_cursor_cb.set_value(False)
    tool.ui.dia.set_value(2.0)
    tool.ui.gapsize.set_value(1.0)
    tool.ui.cutz_entry.set_value(-1.0)
    tool.ui.thin_depth_entry.set_value(-0.5)
    tool.ui.gaptype_combo.set_value(1)
    original = appHistory.object_state(geo)
    n = _stack(fc)
    tool.on_manual_gap_click()
    tool.on_manual_cutout((10, 0))
    tool.on_manual_cutout((20, 5))
    tool.on_mouse_click_release(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_click_press(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_move(SimpleNamespace(pos=(5, 10), button=1, is_dragging=True))
    tool.on_mouse_click_release(SimpleNamespace(pos=(5, 10), button=1))
    tool.on_mouse_click_release(SimpleNamespace(pos=(20, 5), button=1))
    tool.on_key_press(SimpleNamespace(key='Delete'))
    tool.on_key_press(SimpleNamespace(key='Escape'))
    assert appHistory.states_equal(original, appHistory.object_state(geo))
    assert _stack(fc) == n
    # Cancelling must leave the preceding action undoable, not redo cancelled gaps.
    assert fc.history.undo()
    assert fc.collection.get_by_name('geo_cut_session') is None
    assert fc.history.redo()
    _settle_m3(fc)
    geo = fc.collection.get_by_name('geo_cut_session')
    app.collection.objs[geo.obj_options['name']] = geo
    tool.on_manual_gap_click()
    tool.on_manual_cutout((10, 0))
    tool.on_manual_cutout((20, 5))
    tool.on_mouse_click_release(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_click_press(SimpleNamespace(pos=(10, 0), button=1))
    tool.on_mouse_move(SimpleNamespace(pos=(5, 10), button=1, is_dragging=True))
    tool.on_mouse_click_release(SimpleNamespace(pos=(5, 10), button=1))
    tool.on_mouse_click_release(SimpleNamespace(pos=(20, 5), button=1))
    tool.on_key_press(SimpleNamespace(key='Delete'))
    geo.obj_options['session_note'] = 'keep'
    tool.on_mouse_click_release(SimpleNamespace(pos=(0, 0), button=2))
    _one_step(fc, n, 'Manual Cutout')
    finished = unary_union(geo.solid_geometry)
    assert 99 in geo.tools
    assert finished.distance(Point(10, 0)) < 1e-7
    assert finished.distance(Point(5, 10)) > 1.0
    thin = unary_union(geo.tools[99]['solid_geometry'])
    assert thin.length == pytest.approx(3.0)
    assert fc.history.undo(), fc.history.blocked_reason()
    assert 99 not in geo.tools and not geo.multigeo
    assert unary_union(geo.solid_geometry).length == pytest.approx(60)
    assert geo.obj_options['session_note'] == 'keep'
    assert fc.history.redo()
    assert unary_union(geo.solid_geometry).equals(finished) and 99 in geo.tools
    assert unary_union(geo.tools[99]['solid_geometry']).equals(thin)


# ---------------------------------------------------------------------------------------------------------------------
# 4 / 5. Single-geo <-> Multi-geo
# ---------------------------------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------------------------------
# 6. Simplify (runs in the worker)
# ---------------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------------
# 7. Gerber buffer, 8. Isolation buffer
# ---------------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------------
# 9. CNCJob source file, 10 / 11. Rename
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def cncjob(fc, no_plot):
    _load(fc, 'gerber', 'unify_mixed.gbr', 'g_job')
    fc.shell.tcl.eval('isolate g_job -dia 0.2 -passes 1 -outname g_job_iso')
    _settle_m3(fc)
    fc.shell.tcl.eval('cncjob g_job_iso -dia 0.2 -z_cut -0.1 -z_move 2 -feedrate 100 -outname job_hist')
    _settle_m3(fc)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()
    job = fc.collection.get_by_name('job_hist')
    assert job is not None
    return job


def test_cncjob_update_source_file_is_a_snapshot_step(fc, cncjob):
    src0 = cncjob.source_file
    cncjob.prepend_snippet = '(SNIPPET-START)\n'
    cncjob.append_snippet = '(SNIPPET-END)\n'
    cncjob.ui.snippets_cb.blockSignals(True)
    cncjob.ui.snippets_cb.set_value(True)
    cncjob.ui.snippets_cb.blockSignals(False)
    n = _stack(fc)
    cncjob.on_update_source_file()
    _one_step(fc, n, 'Source')
    src1 = cncjob.source_file
    assert src1 != src0 and '(SNIPPET-START)' in src1
    assert fc.history.undo() and cncjob.source_file == src0
    assert fc.history.redo() and cncjob.source_file == src1


# ======================================================================
# merged from tests/test_app_history_milling.py
# ======================================================================
"""App Undo / Redo history of the Milling plugin: the tool table of an existing Geometry / Excellon target."""

import os
import sys
import time
from copy import deepcopy

import pytest
from PyQt6 import QtCore, QtWidgets
from shapely.geometry import box

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject








def _settle_m4(fc, timeout=5.0):
    end = time.time() + timeout
    while fc.projects.is_busy() and time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def _pump(n=10):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _select_row(table, row):
    """Select only this row. selectRow() reads the keyboard modifiers of the application: a Shift or Ctrl state
    that an earlier test left behind extends the selection, and a later delete removes more than 1 tool."""
    table.clearSelection()
    table.selectionModel().select(
        table.model().index(row, 0),
        QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect | QtCore.QItemSelectionModel.SelectionFlag.Rows)


def _load_exc(fc, name):
    def init(obj, _app):
        obj.parse_file(os.path.join(FIX, 'unify_mixed.drl'))
        obj.create_geometry()
    return fc.app_obj.new_object('excellon', name, init, plot=False)


def _new_geo(fc, name):

    def init(obj, _app):
        obj.solid_geometry = box(0, 0, 10, 10)
    return fc.app_obj.new_object('geometry', name, init, plot=False)


def _tools(obj):
    """tools without the geometries (keys, tooldia, data), comparable."""
    return {int(k): {'tooldia': float(v['tooldia']), 'data': deepcopy(v['data'])} for k, v in obj.tools.items()}


def _open(fc, kind, obj):
    """Open the plugin on the target kind and select the object (what the user does)."""
    mt = fc.milling_tool
    if not getattr(fc, "_milling_test_open", False):   # run(toggle=True) a second time would close the plugin
        mt.run(toggle=True)
        fc._milling_test_open = True
    _pump()
    mt.ui.target_radio.set_value(kind)
    _pump()
    mt.ui.object_combo.setCurrentIndex(mt.ui.object_combo.findText(obj.obj_options['name']))
    mt.on_object_changed()
    _pump()
    return mt


def _steps(fc):
    return len(fc.history.undo_stack)


def test_geo_tool_actions_undo_redo(fc, no_plot):
    geo = _new_geo(fc, 'm_geo_actions')
    _settle_m4(fc)
    mt = _open(fc, 'geo', geo)
    assert mt.target_obj is geo

    # add
    s0 = _tools(geo)
    n = _steps(fc)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    assert _steps(fc) == n + 1
    s1 = _tools(geo)
    assert len(s1) == len(s0) + 1
    assert fc.history.undo() and _tools(geo) == s0
    assert fc.history.redo() and _tools(geo) == s1

    # a second tool through the Add button path
    n = _steps(fc)
    mt.on_tool_add(dia=0.8)
    _pump()
    assert _steps(fc) == n + 1
    s2 = _tools(geo)
    assert len(s2) == len(s1) + 1

    # copy
    _select_row(mt.ui.tools_table_mill_geo, 0)
    n = _steps(fc)
    mt.on_tool_copy()
    _pump()
    assert _steps(fc) == n + 1
    s3 = _tools(geo)
    assert len(s3) > len(s2)
    assert fc.history.undo() and _tools(geo) == s2
    assert fc.history.redo() and _tools(geo) == s3

    # edit diameter
    n = _steps(fc)
    item = mt.ui.tools_table_mill_geo.item(0, 1)
    item.setText('%.4f' % 1.25)
    _pump()
    assert _steps(fc) == n + 1
    s4 = _tools(geo)
    assert s4 != s3
    assert fc.history.undo() and _tools(geo) == s3
    assert fc.history.redo() and _tools(geo) == s4

    # apply to all
    _select_row(mt.ui.tools_table_mill_geo, 0)
    first_uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    geo.tools[first_uid]['data']['tools_mill_cutz'] = -0.77
    s4 = _tools(geo)
    n = _steps(fc)
    mt.on_apply_param_to_all_clicked()
    _pump()
    assert _steps(fc) == n + 1
    s5 = _tools(geo)
    assert all(v['data']['tools_mill_cutz'] == -0.77 for v in s5.values())
    assert fc.history.undo() and _tools(geo) == s4
    assert fc.history.redo() and _tools(geo) == s5

    # delete one, then all
    _select_row(mt.ui.tools_table_mill_geo, 0)
    n = _steps(fc)
    mt.on_tool_delete()
    _pump()
    _settle_m4(fc)
    assert not fc.projects.is_busy()
    assert _steps(fc) == n + 1
    s6 = _tools(geo)
    assert len(s6) == len(s5) - 1
    assert fc.history.undo() and _tools(geo) == s5
    assert fc.history.redo() and _tools(geo) == s6

    n = _steps(fc)
    mt.on_tool_delete(all_tools=True)
    _pump()
    _settle_m4(fc)
    assert not fc.projects.is_busy()
    assert _steps(fc) == n + 1
    assert not geo.tools
    assert fc.history.undo() and _tools(geo) == s6


def test_geo_field_edit_is_one_undo_step(fc, no_plot):
    geo = _new_geo(fc, 'm_geo_edits')
    _settle_m4(fc)
    mt = _open(fc, 'geo', geo)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    _select_row(mt.ui.tools_table_mill_geo, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    n = _steps(fc)
    for k, v in enumerate((-0.1, -0.2, -0.3), 1):
        mt.ui.cutz_entry.set_value(v)
        mt.ui.cutz_entry.returnPressed.emit()
        _pump()
        assert _steps(fc) == n + k
    assert geo.tools[uid]['data']['tools_mill_cutz'] == -0.3
    assert fc.history.undo() and geo.tools[uid]['data']['tools_mill_cutz'] == -0.2
    assert fc.history.redo() and geo.tools[uid]['data']['tools_mill_cutz'] == -0.3


def test_v_shape_cut_z_follows_undo_redo(fc, no_plot):
    """The Cut Z that a V shape (or a V-tip edit) calculates is in the same Undo step as the edit."""
    geo = _new_geo(fc, 'm_geo_vshape')
    _settle_m4(fc)
    mt = _open(fc, 'geo', geo)
    mt.on_tool_default_add(dia=0.5)
    _pump()
    _select_row(mt.ui.tools_table_mill_geo, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_geo.item(0, 3).text())
    geo.tools[uid]['data']['tools_mill_cutz'] = -2.4
    before = _tools(geo)

    mt.ui.tool_shape_combo.setCurrentIndex(mt.ui.tool_shape_combo.findText('V'))
    _pump()
    v_shape = _tools(geo)
    assert v_shape[uid]['data']['tools_mill_cutz'] != -2.4
    assert fc.history.undo() and _tools(geo) == before
    assert fc.history.redo() and _tools(geo) == v_shape

    mt.ui.tipdia_entry.set_value(float(mt.ui.tipdia_entry.get_value()) + 0.05)
    _pump()
    v_tip = _tools(geo)
    assert v_tip[uid]['data']['tools_mill_cutz'] != v_shape[uid]['data']['tools_mill_cutz']
    assert fc.history.undo() and _tools(geo) == v_shape
    assert fc.history.redo() and _tools(geo) == v_tip


def test_exc_field_edits_and_order(fc, no_plot):
    exc = _load_exc(fc, 'm_exc_edits')
    _settle_m4(fc)
    mt = _open(fc, 'exc', exc)
    assert mt.target_obj is exc and len(exc.tools) >= 2
    _select_row(mt.ui.tools_table_mill_exc, 0)
    _pump()
    uid = int(mt.ui.tools_table_mill_exc.item(0, 3).text())
    n = _steps(fc)
    mt.ui.cutz_entry.set_value(-0.6)
    mt.ui.cutz_entry.returnPressed.emit()
    _pump()
    assert exc.tools[uid]['data']['tools_mill_cutz'] == -0.6
    assert _steps(fc) == n + 1

    # the Order combo only orders the table rows: the tools of the object and the history stay as they are
    before = _tools(exc)
    n = _steps(fc)
    mt.ui.order_combo.set_value(2)
    _pump()
    mt.ui.order_combo.set_value(0)
    _pump()
    assert _tools(exc) == before
    assert _steps(fc) == n


# ======================================================================
# merged from tests/test_app_history_drilling.py
# ======================================================================
"""Undo / Redo history of the Drilling plugin: a user edit of excellon_obj.tools is 1 step, a UI rebuild adds none."""

import os
import sys
import time
from copy import deepcopy

import pytest
from PyQt6 import QtWidgets

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import appHandlers.appHistory as appHistory
from appMain import App
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.ExcellonObject import ExcellonObject














def _open_m5(fc, name):
    """Open the plugin on the object the way the user does: run() then a target change."""
    tool = fc.drilling_tool
    tool.run(toggle=False)
    tool.ui.object_combo.setCurrentText(name)
    tool.on_object_changed()
    tool.ui.tools_table.selectAll()
    return tool


def _field(tool):
    """A numeric per-tool option of the form and its widget."""
    key = 'tools_drill_cutz'
    assert key in tool.tool_form_fields
    return key, tool.tool_form_fields[key]


def _edit(tool, widget, value):
    widget.set_value(value)
    widget.editingFinished.emit()


def test_field_edit_and_tool_type_change_add_no_step(fc, no_plot, monkeypatch):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_field')
    tool = _open_m5(fc, 'e_drl_field')
    assert tool.excellon_tools is exc.tools
    key, widget = _field(tool)
    uid = next(iter(exc.tools))
    n = _steps(fc)
    for v in (-1.5, -1.6, -1.7):
        _edit(tool, widget, v)
    assert exc.tools[uid]['data'][key] == -1.7      # the edit reaches the object
    assert _steps(fc) == n
    # the handler is not connected to a widget now; call it the way a cell combo would
    cw = QtWidgets.QComboBox()
    cw.addItems(['C', 'V'])
    cw.setCurrentText('V')
    tool.ui.tools_table.setCellWidget(0, 2, cw)
    monkeypatch.setattr(tool, 'sender', lambda: cw)
    tool.on_tooltable_cellwidget_change()
    assert _steps(fc) == n


def test_apply_to_all_is_one_step_with_exact_undo_redo(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_all')
    tool = _open_m5(fc, 'e_drl_all')
    assert len(exc.tools) > 1
    key, widget = _field(tool)
    tool.ui.tools_table.selectRow(0)
    _edit(tool, widget, -1.1)
    first_uid = tool.ui.tools_table.item(0, 3).text()
    before = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    n = _steps(fc)
    tool.ui.tools_table.setCurrentCell(0, 0)
    tool.on_apply_param_to_all_clicked()
    assert _steps(fc) == n + 1
    after = deepcopy({k: v['data'] for k, v in exc.tools.items()})
    assert all(d[key] == -1.1 for d in after.values()), first_uid
    assert fc.history.undo()
    assert {k: v['data'] for k, v in exc.tools.items()} == before
    assert fc.history.redo()
    assert {k: v['data'] for k, v in exc.tools.items()} == after
    # the plugin follows the restored dict: an edit after Undo still reaches the object
    assert fc.history.undo()
    assert tool.excellon_tools is exc.tools
    uid = next(iter(exc.tools))
    _edit(tool, widget, -2.5)
    assert exc.tools[uid]['data'][key] == -2.5


def test_edit_after_a_tools_db_load_does_not_reach_the_object_and_adds_no_step(fc, no_plot):
    exc = _load(fc, 'excellon', 'unify_mixed.drl', 'e_drl_detached')
    tool = _open_m5(fc, 'e_drl_detached')
    key, widget = _field(tool)
    uid = next(iter(exc.tools))
    first = exc.tools[uid]['data'][key]
    tool.rebuild_ui()                           # a row drag and drop: the plugin keeps a copy
    assert tool.excellon_tools is not exc.tools
    n = _steps(fc)
    tool.ui.tools_table.selectAll()
    _edit(tool, widget, -3.3)
    assert _steps(fc) == n
    assert exc.tools[uid]['data'][key] == first


# ======================================================================
# merged from tests/test_app_history_editors.py
# ======================================================================
"""Tests of the history step that App.on_editing_finished records for an editor save (Geometry, Gerber, Excellon,
CNCJob)."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from shapely.geometry import box

from appHandlers.appHistory import HistoryManager, HistoryStep
from appMain import App
from appObjects.GeometryObject import GeometryObject

from tests.helpers_objects import app, _headless_qt  # noqa: F401,E402


class FakeObj:
    """The attributes that object_state() and HistoryManager.restore_state() need."""
    ser_attrs = ['solid_geometry', 'obj_options']

    def __init__(self, uid, kind, geometry):
        self.history_uid = uid
        self.kind = kind
        self.solid_geometry = geometry
        self.obj_options = {'name': uid}
        self.plot = Mock()
        self.set_offset_values = Mock()

    def from_dict(self, data):
        self.solid_geometry = data['solid_geometry']
        self.obj_options = data['obj_options']

    def bounds(self):
        return self.solid_geometry.bounds


class OtherStep(HistoryStep):
    label = 'other'
    size = 0

    def __init__(self, uid):
        self._uid = uid

    def uids(self):
        return {self._uid}

    def undo(self, history):
        pass

    def redo(self, history):
        pass


def make_app_objs(objs):
    app = SimpleNamespace(headless=False, call_source='app', inform=Mock(), history_changed=Mock(), log=Mock(),
                          options={}, projects=SimpleNamespace(is_busy=lambda: False), _history_edit=None,
                          app_obj=SimpleNamespace(object_changed=Mock()))
    app.history = HistoryManager(app, collection=SimpleNamespace(get_list=lambda: objs))
    return app


def begin(app, obj):
    App._history_edit_begin(app, obj)


def saved(app, obj):
    App._history_edit_saved(app, obj)


class TestEditorHistory(unittest.TestCase):

    def test_gerber_and_excellon_edit_is_one_step(self):
        # Save updates a Gerber / Excellon / Geometry object in place: 1 Edit step
        for kind in ('geometry', 'gerber', 'excellon'):
            old, new = box(0, 0, 1, 1), box(0, 0, 3, 3)
            obj = FakeObj(kind[:1] + '1', kind, old)
            app = make_app_objs([obj])
            begin(app, obj)
            self.assertIsNotNone(app._history_edit)
            obj.solid_geometry = new
            saved(app, obj)
            self.assertEqual([s.label for s in app.history.undo_stack], ['Edit'], kind)
            self.assertTrue(app.history.undo())
            self.assertEqual(obj.solid_geometry, old)
            self.assertTrue(app.history.redo())
            self.assertEqual(obj.solid_geometry, new)


def test_real_multigeo_geometry_edit_is_one_step_and_undo_gives_the_old_tool_geometry(app):
    # what App.on_editing_finished and update_editor_geometry() do for a Save in place, with a real GeometryObject

    old, new = box(0, 0, 1, 1), box(0, 0, 5, 5)
    obj = GeometryObject('geo', app)
    obj.history_uid = 'g1'
    obj.multigeo = True
    obj.tools = {1: {'tooldia': 1.0, 'data': {'tools_mill_tooldia': 1.0}, 'solid_geometry': [old]}}
    obj.solid_geometry = [old]
    obj.plot = Mock()
    obj.set_offset_values = Mock()
    history_app = make_app_objs([obj])
    begin(history_app, obj)
    obj.tools[1]['solid_geometry'] = [new]
    obj.solid_geometry = [new]
    saved(history_app, obj)
    assert [s.label for s in history_app.history.undo_stack] == ['Edit']
    assert history_app.history.undo()
    assert obj.tools[1]['solid_geometry'] == [old] and obj.solid_geometry == [old]
    assert history_app.history.redo()
    assert obj.tools[1]['solid_geometry'] == [new] and obj.solid_geometry == [new]


# ======================================================================
# merged from tests/test_history_recovery.py
# ======================================================================
"""Transactional replay and exact lightweight transform metadata regressions."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from shapely.geometry import Point

from appCommon.Common import LoudDict
from appHandlers.appHistory import (HistoryManager, MatrixStep, SnapshotStep, GroupStep, CreateStep,
                                    DeleteStep, RenameStep, object_state, transform_metadata, retained_sizes,
                                    states_equal)
from appHandlers.appObjectOps import AppObjectOps


class Object:
    kind = 'geometry'
    ser_attrs = ['obj_options', 'solid_geometry']

    def __init__(self, uid):
        self.history_uid = uid
        self.obj_options = LoudDict(name=uid, plot=True)
        self.solid_geometry = [Point(1, 2)]
        self.multigeo = False
        self.plot = Mock()
        self.set_offset_values = Mock()

    def from_dict(self, state):
        self.obj_options.update(state['obj_options'])
        self.solid_geometry = state['solid_geometry']

    def bounds(self):
        return self.solid_geometry[0].bounds


def setup_history():
    objects = [Object('a'), Object('b')]
    app = fake_app()
    collection = SimpleNamespace(get_list=lambda: list(objects))
    collection.rename_object = lambda obj, name: obj.obj_options.__setitem__('name', name)
    h = HistoryManager(app, collection)
    app.history = h
    app.app_obj = SimpleNamespace(object_changed=Mock())
    h.delete_objects = lambda objs: [objects.remove(obj) for obj in objs]

    def rebuild(state):
        obj = Object(state['history_uid'])
        objects.append(obj)
        h.restore_state(obj, state)
    h.rebuild = rebuild
    return h, objects


@pytest.mark.parametrize('matrix', [(np.nan, 0, 0, 1, 0, 0), (1, 0, 0, 1)])
def test_invalid_matrix_prevalidated_before_any_mutation(matrix):
    h, objects = setup_history()
    original = objects[0].solid_geometry[0]
    h.push(MatrixStep('bad', [('a', (1, 0, 0, 1, 3, 0)), ('b', matrix)]))
    assert not h.undo()
    assert objects[0].solid_geometry[0] is original
    assert h.can_undo() and not h.can_redo()


def test_partial_group_rolls_back_and_can_retry():
    h, objects = setup_history()
    a, b = objects
    before = object_state(a)
    a.solid_geometry = [Point(8, 9)]
    after = object_state(a)
    # Undo runs the snapshot before the matrix; plotting b then fails once.
    b.plot.side_effect = [RuntimeError('plot'), None, None]
    h.push(GroupStep('group', [MatrixStep('move', [('b', (1, 0, 0, 1, 2, 0))]),
                               SnapshotStep('edit', [('a', before, after)])]))
    assert not h.undo()
    assert states_equal(object_state(a), after)
    assert b.solid_geometry[0].equals_exact(Point(1, 2), 0)
    assert h.undo_label() == 'group'
    assert h.undo()
    assert a.solid_geometry[0].equals_exact(Point(1, 2), 0)


def test_partial_create_undo_restores_membership_and_bookkeeping():
    h, objects = setup_history()
    original_delete = h.delete_objects

    def fail(objs):
        original_delete(objs[:1])
        raise RuntimeError('partial delete')
    h.delete_objects = fail
    step = CreateStep('create', ['a', 'b'])
    h.push(step)
    assert not h.undo()
    assert {obj.history_uid for obj in objects} == {'a', 'b'}
    assert step.states == {} and step.geometry_sizes() == {}
    h.delete_objects = original_delete
    assert h.undo() and not objects
    assert h.redo() and len(objects) == 2


def test_matrix_metadata_options_presence_roundtrip():
    h, objects = setup_history()
    obj = objects[0]
    before = transform_metadata(obj)
    obj.solid_geometry = [Point(3, 2)]
    obj.obj_options['offset_x'] = 2
    h.record_matrix('move', [(obj, (1, 0, 0, 1, 2, 0), before)])
    assert h.undo() and 'offset_x' not in obj.obj_options
    assert h.redo() and obj.obj_options['offset_x'] == 2


@pytest.mark.parametrize('bulk', [True])
def test_delete_inactive_keeps_surviving_properties(bulk):
    active, gone, other = Object('active'), Object('gone'), Object('other')
    objects = [active, gone, other]
    collection = SimpleNamespace(get_list=lambda: objects, get_active=lambda: active,
                                 delete_objects=lambda selected: [objects.remove(obj) for obj in selected])
    app = SimpleNamespace(collection=collection, setup_default_properties_tab=Mock(),
                          delete_selection_shape=Mock(), exc_areas=Mock())
    holder = SimpleNamespace(app=app, inform=Mock(), log=Mock())
    AppObjectOps.delete_objects_now(holder, [gone, other] if bulk else [gone])
    app.setup_default_properties_tab.assert_not_called()
    assert active in objects
