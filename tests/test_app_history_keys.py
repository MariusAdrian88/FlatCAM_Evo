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

    def test_ctrl_s_saves_the_project(self):
        gui = self.press(QtCore.Qt.Key.Key_S, CTRL)
        gui.app.f_handlers.on_file_save_project.assert_called_once_with()

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

    def test_escape_in_a_normal_window_only_deselects(self):
        self.press_escape()
        self.window.app.on_deselect_all.assert_called_once_with()
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

    def test_view_toggles_work_once_outside_text_inputs(self):
        self.window.app.call_source = 'geo_editor'
        for key, action in self.actions.items():
            with self.subTest(key=key):
                before = action.isChecked()
                QtTest.QTest.keyClick(self.target, getattr(QtCore.Qt.Key, 'Key_' + key), SHIFT)
                self.assertEqual(action.isChecked(), not before)
                event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress,
                                       getattr(QtCore.Qt.Key, 'Key_' + key), SHIFT, key, True)
                self.qapp.sendEvent(self.target, event)
                self.assertEqual(action.isChecked(), not before)


if __name__ == '__main__':
    unittest.main()
