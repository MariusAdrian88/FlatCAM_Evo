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


class TestAppHistoryKeys(unittest.TestCase):
    def test_ctrl_z_undo(self):
        gui = press(QtCore.Qt.Key.Key_Z, CTRL)
        gui.app.history.undo.assert_called_once()
        gui.app.history.redo.assert_not_called()

    def test_ctrl_shift_z_redo(self):
        gui = press(QtCore.Qt.Key.Key_Z, CTRL | SHIFT)
        gui.app.history.redo.assert_called_once()
        gui.app.history.undo.assert_not_called()

    def test_text_focus_blocks(self):
        gui = press(QtCore.Qt.Key.Key_Z, CTRL, in_text=True)
        gui.app.history.undo.assert_not_called()
        gui.app.history.redo.assert_not_called()


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
    return re.sub(r" (id='\d+'|at 0x[0-9a-f]+)", '', text)


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
        list_view = MagicMock(spec=main_module.EventSensitiveListView)
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
        gui.reset_mock()
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

    def test_app(self):
        self.check('app')

    def test_geometry_editor(self):
        self.check('geo_editor')

    def test_gerber_editor(self):
        self.check('grb_editor')

    def test_excellon_editor(self):
        self.check('exc_editor')

    def test_plugin_contexts(self):
        for context in CONTEXTS[4:]:
            self.check(context)


if __name__ == '__main__':
    import sys as _sys
    if '--record' in _sys.argv:
        _qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        data = {context: record(context) for context in CONTEXTS}
        with open(CHARACTERIZATION_FILE, 'w', encoding='utf-8') as out:
            json.dump(data, out, indent=1, sort_keys=True)
        print({c: {s: len(r) for s, r in t.items()} for c, t in data.items()})
    else:
        unittest.main()
