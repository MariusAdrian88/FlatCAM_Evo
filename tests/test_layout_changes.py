"""Layout activation must pass names, not Qt combobox indices."""

import os
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

from appMain import App
from appHandlers.appLifecycle import AppLifecycle
from appGUI.MainGUI import MainGUI
from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appGUI.preferences.general import GeneralGUIPrefGroupUI as gui_module
from defaults import AppDefaults


@pytest.fixture
def layout_app(tmp_path, monkeypatch):
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    settings = QtCore.QSettings(str(tmp_path / 'layout.ini'), QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr(QtCore, 'QSettings', lambda *args: settings)
    monkeypatch.setattr(gui_module, 'QSettings', lambda *args: settings)
    monkeypatch.setattr(importlib.import_module('appGUI.MainGUI'), 'QSettings', lambda *args: settings)
    app = MagicMock()
    app.decimals = 4
    app.resource_location = 'assets/resources'
    app.defaults = AppDefaults(beta=False)
    app.options = dict(app.defaults)
    monkeypatch.setattr(OptionsGroupUI, 'app', app)
    app.on_layout = lambda lay=None, connect_signals=True: App.on_layout(app, lay, connect_signals)
    app.lifecycle = AppLifecycle(app)
    window = QtWidgets.QMainWindow()
    window.lock_toolbar = MagicMock(side_effect=lambda lock=False: MainGUI.lock_toolbar(window, lock))
    window.show_text_under_action = lambda show_text=True: MainGUI.show_text_under_action(window, show_text)
    window.lock_action = QtGui.QAction(window)
    window.show_text_action = QtGui.QAction(window)
    for action in (window.lock_action, window.show_text_action):
        action.setCheckable(True)
    window.lock_action.triggered.connect(window.lock_toolbar)
    window.show_text_action.triggered.connect(window.show_text_under_action)
    window.populate_toolbars = lambda: MainGUI.populate_toolbars(window)
    window.app = app
    for name in ('grid_snap_btn', 'grid_gap_link_cb'):
        setattr(window, name, QtWidgets.QCheckBox())
    for name in ('corner_snap_btn', 'snap_magnet'):
        setattr(window, name, QtWidgets.QWidget())
    for name in ('grid_gap_x_entry', 'grid_gap_y_entry', 'snap_max_dist_entry'):
        setattr(window, name, QtWidgets.QLineEdit())
    group = gui_module.GeneralGUIPrefGroupUI(app)
    window.general_pref_form = SimpleNamespace(general_gui_group=group)
    app.ui = window
    yield app, group, settings
    group.close()
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize('resources', ['assets/resources', 'assets/resources/dark_resources'])
def test_view_restore_layout_restores_hidden_toolbars(layout_app, resources):
    app, group, settings = layout_app
    app.resource_location = resources
    window = MainGUI(app)
    try:
        app.ui = window
        app.on_layout('standard')
        window.toolbaredit.hide()
        window.toolbarview.hide()
        action = next(action for action in window.menuview.actions() if action.text() == 'Restore Layout')
        assert not action.icon().isNull()
        action.trigger()
        assert not window.toolbaredit.isHidden()
        assert not window.toolbarview.isHidden()
    finally:
        # a full MainGUI left to the interpreter exit crashes the process (0xC0000409)
        window.close()
        window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)


@pytest.mark.parametrize('index,layout', [(0, 'standard'), (1, 'compact'), (2, 'minimal')])
def test_ui_layout_activation_and_restoration(layout_app, index, layout):
    app, group, settings = layout_app
    group.layout_combo.setCurrentIndex(index)
    group.layout_combo.activated.emit(index)
    assert settings.value('layout') == layout
    assert app.options['global_layout'] == layout
    assert app.ui.toolBarArea(app.ui.toolbarfile) == (
        QtCore.Qt.ToolBarArea.LeftToolBarArea if layout == 'compact'
        else QtCore.Qt.ToolBarArea.TopToolBarArea)
    assert app.ui.toolbarfile.isHidden()
    assert app.ui.toolbarview.isHidden() == (layout == 'minimal')
    assert app.ui.geo_edit_toolbar.isHidden() == (layout == 'minimal')
    assert app.ui.file_open_gerber_btn in app.ui.toolbarfile.actions()
    app.connect_toolbar_signals.assert_called_once_with()
    app.connect_editors_toolbar_signals.assert_called_once_with()
    app.log.error.assert_not_called()
    restored = gui_module.GeneralGUIPrefGroupUI(app)
    try:
        assert restored.layout_combo.get_value() == layout
    finally:
        restored.close()


@pytest.mark.parametrize('lock', [False])
@pytest.mark.parametrize('show_text', [False, True])
def test_layout_transitions_keep_actions_and_toolbar_state(layout_app, lock, show_text):
    app, group, settings = layout_app
    app.ui.lock_toolbar(lock)
    app.ui.show_text_under_action(show_text)
    for layout in ('standard', 'standard', 'compact', 'compact', 'standard',
                   'minimal', 'minimal', 'compact', 'minimal', 'standard'):
        app.on_layout(layout)
        lock = True if layout == 'minimal' else lock
        for toggle in range(3):
            assert app.ui.lock_action.isChecked() == lock
            assert app.ui.show_text_action.isChecked() == show_text
            for toolbar in app.ui.findChildren(QtWidgets.QToolBar, options=QtCore.Qt.FindChildOption.FindDirectChildrenOnly):
                assert toolbar.isMovable() == (not lock)
                assert toolbar.toolButtonStyle() == (
                    QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon if show_text
                    else QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
            assert settings.value('toolbar_lock', type=bool) == lock
            assert settings.value('menu_show_text', type=bool) == show_text
            app.ui.lock_action.trigger()
            app.ui.show_text_action.trigger()
            lock, show_text = not lock, not show_text


@pytest.mark.parametrize('layout', ['standard', 'minimal'])
@pytest.mark.parametrize('saved_state', ['missing', 'invalid', 'custom', 'preset'])
@pytest.mark.parametrize('show_text', [False])
def test_normal_startup_restores_layout_and_custom_toolbar_state(
        layout_app, monkeypatch, layout, saved_state, show_text):
    app, group, settings = layout_app
    monkeypatch.setattr(importlib.import_module('appMain'), 'QSettings', lambda *args: settings)
    # MainGUI initially constructs standard toolbars, even when another preset is saved.
    app.on_layout('standard', connect_signals=False)
    app.ui.grbl_console_dock = QtWidgets.QDockWidget('Console', app.ui)
    app.ui.grbl_console_dock.setObjectName('grbl_console_dock')
    app.ui.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, app.ui.grbl_console_dock)
    if saved_state == 'preset':
        saved_window = QtWidgets.QMainWindow()
        for name in ('toolbarfile', 'toolbaredit', 'toolbarview', 'toolbarshell', 'toolbarplugins',
                     'exc_edit_toolbar', 'geo_edit_toolbar', 'grb_edit_toolbar'):
            toolbar = QtWidgets.QToolBar(saved_window)
            toolbar.setObjectName(getattr(app.ui, name).objectName())
            saved_window.addToolBar(
                QtCore.Qt.ToolBarArea.LeftToolBarArea if layout == 'compact'
                else QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
            toolbar.setVisible(layout != 'minimal' or name in ('toolbarfile', 'toolbaredit', 'toolbarplugins'))
        settings.setValue('saved_gui_state', saved_window.saveState(0))
        saved_window.close()
    elif saved_state == 'custom':
        app.ui.addToolBar(QtCore.Qt.ToolBarArea.RightToolBarArea, app.ui.toolbarfile)
        app.ui.toolbaredit.hide()
        app.ui.toolbarview.show()
        settings.setValue('saved_gui_state', app.ui.saveState(0))
    elif saved_state == 'invalid':
        settings.setValue('saved_gui_state', QtCore.QByteArray(b'invalid'))
    app.ui.grbl_console_dock.hide()
    settings.setValue('layout', layout)
    app.options['first_run'] = False
    app.ui.lock_toolbar(saved_state == 'preset')
    app.ui.show_text_under_action(show_text)
    for toolbar in (app.ui.exc_edit_toolbar, app.ui.geo_edit_toolbar, app.ui.grb_edit_toolbar):
        toolbar.setDisabled(True)
    app.connect_toolbar_signals.reset_mock()
    app.connect_editors_toolbar_signals.reset_mock()

    App._setup_tools_and_editors(app)

    assert app.options['global_layout'] == layout
    assert app.ui.toolBarArea(app.ui.toolbarfile) == (
        QtCore.Qt.ToolBarArea.RightToolBarArea if saved_state == 'custom'
        else QtCore.Qt.ToolBarArea.LeftToolBarArea if layout == 'compact'
        else QtCore.Qt.ToolBarArea.TopToolBarArea)
    assert app.ui.toolbarview.isHidden() == (layout == 'minimal' and saved_state != 'custom')
    assert app.ui.toolbaredit.isHidden() == (saved_state == 'custom')
    assert app.ui.grbl_console_dock.isHidden()
    for toolbar in (app.ui.exc_edit_toolbar, app.ui.geo_edit_toolbar, app.ui.grb_edit_toolbar):
        assert not toolbar.isEnabled()
    lock = saved_state == 'preset' or (layout == 'minimal' and saved_state != 'custom')
    for toggle in range(2):
        assert app.ui.lock_action.isChecked() == lock
        assert app.ui.show_text_action.isChecked() == show_text
        for toolbar in (app.ui.toolbarfile, app.ui.toolbarview, app.ui.geo_edit_toolbar):
            assert toolbar.isMovable() == (not lock)
            assert toolbar.toolButtonStyle() == (
                QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon if show_text
                else QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        assert settings.value('toolbar_lock', type=bool) == lock
        assert settings.value('menu_show_text', type=bool) == show_text
        app.ui.lock_action.trigger()
        app.ui.show_text_action.trigger()
        lock, show_text = not lock, not show_text
    app.connect_toolbar_signals.assert_not_called()
    app.preferencesUiManager.save_defaults.assert_not_called()
