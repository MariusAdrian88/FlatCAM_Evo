"""Run: .venv/Scripts/python.exe tests/test_titlebar.py"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_isolation  # noqa: E402

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets
from appGUI.TitleBar import TitleBar
from appGUI.MainGUI import MainGUI
from defaults import AppDefaults, AppOptions


class TitleWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.close_requests = 0
        self.setMinimumSize(450, 200)
        self.resize(900, 400)
        self.menu = QtWidgets.QMenuBar(self)
        self.file_menu = self.menu.addMenu('File')
        self.open_action = self.file_menu.addAction('Open')
        self.submenu = self.file_menu.addMenu('Recent')
        self.recent_action = self.submenu.addAction('Board')
        self.menu.addMenu('Edit')
        self.notebook_action = QtGui.QAction('Toggle Panel', self)
        self.notebook_action.setCheckable(True)
        self.menu.addAction(self.notebook_action)
        self.title_bar = TitleBar(self, self.menu, self.notebook_action)
        self.setMenuWidget(self.title_bar)
        self.setCentralWidget(QtWidgets.QPushButton('Canvas'))
        self.setWindowTitle('board.FlatPrj')

    def closeEvent(self, event):
        self.close_requests += 1
        # Alt+F4 or the taskbar still close it, so a window left by a stopped run does not block the screen
        if not event.spontaneous():
            event.ignore()

    def nativeEvent(self, event_type, message):
        return MainGUI.nativeEvent(self, event_type, message)

    def normalGeometry(self):
        return MainGUI.normalGeometry(self)

    def place_on_cursor_screen(self):
        return MainGUI.place_on_cursor_screen(self)


class TestTitleBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = TitleWindow()
        self.bar = self.window.title_bar
        self.window.show()
        self.qapp.processEvents()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.hide)

    def expand(self):
        QtTest.QTest.mouseClick(self.bar.menu_button, QtCore.Qt.MouseButton.LeftButton)
        self.qapp.processEvents()

    def test_window_created_by_the_splash_keeps_its_saved_geometry(self):
        window = TitleWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        if window.title_bar.native_filter is None:
            self.skipTest('native Windows frame only')
        window.ensurePolished()
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        window.setGeometry(300, 200, 900, 500)
        # The app start: QSplashScreen.finish() creates the native window before the first show.
        splash = QtWidgets.QSplashScreen(QtGui.QPixmap(20, 20))
        splash.show()
        splash.finish(window)
        window.show()
        QtTest.QTest.qWait(100)
        self.assertEqual(QtCore.QRect(300, 200, 900, 500), window.geometry())


    def test_maximized_startup_preserves_restore_geometry(self):
        from appMain import App

        window = TitleWindow()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.hide)
        # Native maximize/restore must run on screen, not Qt's simulated window state.
        window.ensurePolished()
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        window.setGeometry(115, 23, 900, 400)
        original = window.geometry()
        app = MagicMock()
        app.ui = window
        app.headless = False
        app.splash = None
        app.options = {'global_systray_icon': False, 'global_version_check': False}
        app.cmd_line_shellvar = app.cmd_line_shellfile = app.cmd_line_command = None
        app.defaults.old_defaults_found = False
        with tempfile.TemporaryDirectory() as path:
            settings = QtCore.QSettings(os.path.join(path, 'startup.ini'), QtCore.QSettings.Format.IniFormat)
            settings.setValue('maximized_gui', True)
            # the mouse cursor of the test run can be on any screen
            with patch('appMain.QSettings', return_value=settings), patch.object(App, 'args', []), \
                    patch('appGUI.MainGUI.cursor_screen', return_value=window.screen()):
                App._setup_startup(app)
            self.qapp.processEvents()
            self.assertTrue(window.isMaximized())
            window.showNormal()
            self.qapp.processEvents()
            self.assertEqual(window.geometry(), original)

    def test_main_window_fullscreen_preserves_geometry_and_maximized_state(self):
        for custom_titlebar in (True, False):
            window = self.window if custom_titlebar else QtWidgets.QMainWindow()
            window.app = SimpleNamespace(qapp=self.qapp, ui=window, options={
                'global_custom_titlebar': custom_titlebar,
                'global_coords_bar_show': True, 'global_delta_coords_bar_show': True,
                'global_grid_bar_show': True, 'global_statusbar_show': True,
            })
            if not custom_titlebar:
                self.addCleanup(window.deleteLater)
                self.addCleanup(window.hide)
                window.menu = QtWidgets.QMenuBar(window)
                window.menu_toggle_nb = window.menu.addAction('Notebook')
                MainGUI.build_titlebar(window)
                window.resize(900, 400)
                window.show()
            if sys.platform == 'win32' and self.qapp.platformName() == 'windows':
                from PyQt6 import QtOpenGLWidgets
                window.hide()
                window.setCentralWidget(QtOpenGLWidgets.QOpenGLWidget(window))
                window.ensurePolished()
                window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, False)
                window.show()
            window.hidden_toolbars = []
            window.original_window_flags = window.windowFlags()
            for name in ('coords_toolbar', 'delta_coords_toolbar', 'grid_toolbar', 'status_toolbar'):
                toolbar = QtWidgets.QToolBar(name)
                setattr(window, name, toolbar)
                window.statusBar().addWidget(toolbar)
            self.qapp.processEvents()
            original = window.geometry()
            for maximized in (False, True):
                with self.subTest(custom_titlebar=custom_titlebar, maximized=maximized):
                    if maximized:
                        window.showMaximized()
                        self.qapp.processEvents()
                        if custom_titlebar and self.bar.native_filter is not None:
                            self.assertEqual(window.geometry(), window.screen().availableGeometry())
                            self.assertEqual(self.bar.height(), 30)
                        self.assertEqual(window.normalGeometry(), original)
                        window.showNormal()
                        self.qapp.processEvents()
                        self.assertFalse(window.isMaximized())
                        self.assertEqual(window.geometry(), original)
                        window.showMaximized()
                        self.qapp.processEvents()
                        window.showMinimized()
                        self.qapp.processEvents()
                        window.showMaximized()
                        self.qapp.processEvents()
                        self.assertEqual(window.normalGeometry(), original)
                    MainGUI.on_full_screen_toggled(window)
                    self.qapp.processEvents()
                    self.assertTrue(window.isFullScreen())
                    if custom_titlebar:
                        self.assertFalse(self.bar.isVisible())
                    MainGUI.on_full_screen_toggled(window)
                    self.qapp.processEvents()
                    self.assertFalse(window.isFullScreen())
                    self.assertEqual(window.windowFlags(), window.original_window_flags)
                    self.assertEqual(window.isMaximized(), maximized)
                    if not maximized:
                        self.assertEqual(window.geometry(), original)
                    else:
                        window.showNormal()
                        self.qapp.processEvents()
                        self.assertEqual(window.geometry(), original)


class TestTitleBarPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_preferences(self, path):
        from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
        from appGUI.preferences.general.GeneralGUIPrefGroupUI import GeneralGUIPrefGroupUI
        from appGUI.preferences.PreferencesUIManager import PreferencesUIManager

        defaults = AppDefaults(beta=False)
        self.assertIs(defaults.get('global_custom_titlebar'), True)
        app = MagicMock()
        app.decimals = 4
        app.resource_location = 'assets/resources'
        app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
        with patch.object(OptionsGroupUI, 'app', app):
            group = GeneralGUIPrefGroupUI(app)
        self.addCleanup(group.deleteLater)
        ui = MagicMock()
        ui.app = app
        ui.plot_tab_area.count.return_value = 0
        ui.general_pref_form.general_gui_group = group
        for name in ('notebook', 'axis', 'textbox', 'hud'):
            spinner = getattr(ui.general_pref_form.general_app_set_group, name + '_font_size_spinner')
            spinner.get_value.return_value = 9
        manager = PreferencesUIManager(defaults, path, ui, MagicMock(), app.options)
        self.addCleanup(manager.deleteLater)
        self.assertIs(manager.defaults_form_fields.get('global_custom_titlebar'), group.custom_titlebar_cb)
        manager.defaults_write_form_field('global_custom_titlebar')
        self.assertTrue(group.custom_titlebar_cb.get_value())
        self.assertIn('restart', group.custom_titlebar_cb.toolTip().lower())
        manager.defaults_form_fields = {'global_custom_titlebar': group.custom_titlebar_cb}
        group.appearance_radio.set_value(defaults['global_appearance'])
        group.dark_canvas_cb.set_value(False)
        return manager

    def test_custom_titlebar_changes_persist_and_restart_only_after_confirmation(self):
        for current, requested in ((True, False), (False, True)):
            for accept in (True, False):
                with self.subTest(current=current, requested=requested, accept=accept), \
                        tempfile.TemporaryDirectory() as path:
                    manager = self.make_preferences(path)
                    manager.defaults['global_custom_titlebar'] = current
                    manager.ui.app.options['global_custom_titlebar'] = current
                    checkbox = manager.ui.general_pref_form.general_gui_group.custom_titlebar_cb
                    checkbox.set_value(requested)
                    settings = QtCore.QSettings(os.path.join(path, 'settings.ini'), QtCore.QSettings.Format.IniFormat)
                    settings.setValue('appearance', manager.defaults['global_appearance'])
                    settings.setValue('dark_canvas', False)
                    dialog = MagicMock()
                    yes, cancel = object(), object()
                    dialog.addButton.side_effect = [yes, cancel]
                    dialog.clickedButton.return_value = yes if accept else cancel
                    with patch('appGUI.preferences.PreferencesUIManager.QtCore.QSettings', return_value=settings), \
                            patch('appGUI.preferences.PreferencesUIManager.QSettings', return_value=settings), \
                            patch('appGUI.preferences.PreferencesUIManager.FCMessageBox', return_value=dialog):
                        manager.on_save_button(save_to_file=False)
                    expected = requested if accept else current
                    dialog.exec.assert_called_once()
                    self.assertEqual(checkbox.get_value(), expected)
                    self.assertEqual(manager.defaults['global_custom_titlebar'], expected)
                    self.assertEqual(manager.ui.app.options['global_custom_titlebar'], expected)
                    if accept:
                        manager.ui.app.on_app_restart.assert_called_once()
                        saved = AppDefaults(beta=False)
                        saved.load(os.path.join(path, 'current_defaults.FlatConfig'), inform=MagicMock())
                        self.assertEqual(saved['global_custom_titlebar'], expected)
                    else:
                        manager.ui.app.on_app_restart.assert_not_called()
                    settings.sync()


if __name__ == '__main__':
    unittest.main()
