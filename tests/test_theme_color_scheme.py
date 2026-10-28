"""
The 'default' and 'light' themes use colors made for a light window and 'dark' colors made for a dark one: Qt's color
scheme has to follow the theme, not the operating system.
"""

import os
import json
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets
from types import SimpleNamespace

import appMain
from appGUI.MainGUI import MainGUI
from defaults import AppDefaults, AppOptions

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

needs_color_scheme = pytest.mark.skipif(not hasattr(QtGui.QStyleHints, 'setColorScheme'),
                                        reason="Qt before 6.8 cannot set the color scheme")


@pytest.fixture(scope='module')
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def restore_scheme(qapp):
    yield
    if hasattr(qapp.styleHints(), 'unsetColorScheme'):
        qapp.styleHints().unsetColorScheme()


class TestApplyColorScheme:
    @needs_color_scheme
    def test_light_themes_give_a_light_window_palette(self, qapp, restore_scheme):
        # the plot canvas uses QPalette().color(Window) as its border color together with black ticks
        appMain.App._apply_color_scheme(SimpleNamespace(qapp=qapp), 'light')
        if qapp.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Unknown:
            pytest.skip("this Qt platform plugin ignores setColorScheme (offscreen)")
        assert QtGui.QPalette().color(QtGui.QPalette.ColorRole.Window).lightness() >= 128

    @needs_color_scheme
    def test_dark_theme_gives_a_dark_window_palette(self, qapp, restore_scheme):
        appMain.App._apply_color_scheme(SimpleNamespace(qapp=qapp), 'dark')
        if qapp.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Unknown:
            pytest.skip("this Qt platform plugin ignores setColorScheme (offscreen)")
        assert QtGui.QPalette().color(QtGui.QPalette.ColorRole.Window).lightness() < 128


class TestPreferencesTabText:
    @staticmethod
    def _gui(qapp, style_sheet):
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(QtWidgets.QWidget(), 'General')
        tabs.addTab(QtWidgets.QWidget(), 'Gerber')
        gui = SimpleNamespace(
            app=SimpleNamespace(qapp=SimpleNamespace(styleSheet=lambda: style_sheet)),
            pref_tab_area=tabs,
            default_pref_tab_area_tab_text_color=tabs.tabBar().tabTextColor(0),
        )
        gui.set_pref_tab_area_tab_default_text_color = lambda: MainGUI.set_pref_tab_area_tab_default_text_color(gui)
        return gui, tabs

    def test_selected_tab_defers_to_stylesheet(self, qapp):
        gui, tabs = self._gui(qapp, 'QWidget { }')
        MainGUI.on_pref_tabbar_clicked(gui, 0)
        assert not tabs.tabBar().tabTextColor(0).isValid()

    def test_selected_tab_defers_to_qt_style_and_clears_explicit_color(self, qapp):
        gui, tabs = self._gui(qapp, '')
        MainGUI.on_pref_tabbar_clicked(gui, 0, QtGui.QColor('red'))
        MainGUI.on_pref_tabbar_clicked(gui, 1)
        assert not tabs.tabBar().tabTextColor(0).isValid()
        assert not tabs.tabBar().tabTextColor(1).isValid()

@pytest.mark.parametrize('legacy,appearance,styling', [
    ('default', 'light', 'default'), ('light', 'light', 'custom'),
    ('dark', 'dark', 'custom'), ('auto', 'auto', 'custom'),
])
def test_legacy_preferences_migrate_and_round_trip(tmp_path, legacy, appearance, styling):
    path = tmp_path / 'defaults.FlatConfig'
    path.write_text(json.dumps({'global_appearance': legacy, 'global_cursor_color': '#123456'}))
    defaults = AppDefaults(beta=False)
    defaults.load(str(path), MagicMock())
    assert defaults['global_appearance'] == appearance
    assert defaults['global_styling'] == styling
    assert defaults.current_defaults['global_styling'] == styling
    assert defaults['global_cursor_color'] == '#123456'
    defaults.write(str(path))
    defaults.load(str(path), MagicMock())
    assert defaults['global_styling'] == styling


@pytest.mark.parametrize('appearance,os_dark,theme', [
    ('light', True, 'light'), ('dark', False, 'dark'),
    ('auto', True, 'dark'), ('auto', False, 'light'),
])
@pytest.mark.parametrize('styling', ['default', 'custom'])
def test_startup_separates_scheme_from_stylesheet(tmp_path, monkeypatch, appearance, os_dark, theme, styling):
    path = tmp_path / 'current_defaults.FlatConfig'
    path.write_text(json.dumps({'global_appearance': appearance, 'global_styling': styling,
                               'first_run': False, 'global_log_verbose': 1, 'global_dark_canvas': True}))
    app = MagicMock()
    app.version = 'test'
    app.defaults_path.return_value = str(path)
    app.data_path = str(tmp_path)
    monkeypatch.setattr(appMain.darkdetect, 'isDark', lambda: os_dark)
    for name in ('AppLifecycle', 'GitHubUpdateChecker', 'Pool'):
        monkeypatch.setattr(appMain, name, MagicMock())
    appMain.App._setup_defaults_and_preferences(app)
    app._apply_color_scheme.assert_called_once_with(theme)
    assert app.options['global_theme'] == theme
    assert app.defaults['global_theme'] == theme
    assert app.options['global_dark_canvas'] is True
    assert app.resource_location == ('assets/resources/dark_resources' if theme == 'dark' else 'assets/resources')
    sheet = app.qapp.setStyleSheet.call_args.args[0]
    assert bool(sheet) == (styling == 'custom')


@pytest.mark.parametrize('key,requested', [
    ('global_appearance', 'dark'), ('global_styling', 'custom'), ('global_dark_canvas', True),
])
@pytest.mark.parametrize('accept', [True])
@pytest.mark.parametrize('has_settings', [True, False])
def test_theme_preferences_confirm_revert_and_save(tmp_path, monkeypatch, qapp, key, requested, accept, has_settings):
    from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
    from appGUI.preferences.general.GeneralGUIPrefGroupUI import GeneralGUIPrefGroupUI
    from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
    import importlib
    module = importlib.import_module('appGUI.preferences.PreferencesUIManager')

    defaults = AppDefaults(beta=False)
    app = MagicMock(decimals=4, resource_location='assets/resources')
    app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    app.options['gerber_bboxmargin'] = 0.123
    monkeypatch.setattr(OptionsGroupUI, 'app', app)
    group = GeneralGUIPrefGroupUI(app)
    ui = MagicMock()
    ui.app = app
    ui.plot_tab_area.count.return_value = 0
    ui.general_pref_form.general_gui_group = group
    for name in ('notebook', 'axis', 'textbox', 'hud'):
        getattr(ui.general_pref_form.general_app_set_group, name + '_font_size_spinner').get_value.return_value = 9
    manager = PreferencesUIManager(defaults, str(tmp_path), ui, MagicMock(), app.options)
    fields = {name: manager.defaults_form_fields[name] for name in
              ('global_appearance', 'global_styling', 'global_dark_canvas', 'global_custom_titlebar')}
    manager.defaults_form_fields = fields
    for name, field in fields.items():
        field.set_value(defaults[name])
    assert group.appearance_label.text() == 'Appearance'
    assert group.style_label.text() == 'Qt Widget Style:'
    assert [group.styling_combo.itemText(i) for i in range(group.styling_combo.count())] == ['Default', 'Custom']
    settings = QtCore.QSettings(str(tmp_path / 'settings.ini'), QtCore.QSettings.Format.IniFormat)
    if has_settings:
        for name in ('appearance', 'styling', 'dark_canvas'):
            settings.setValue(name, defaults['global_' + name])
    monkeypatch.setattr(module.QtCore, 'QSettings', lambda *args: settings)
    monkeypatch.setattr(module, 'QSettings', lambda *args: settings)
    dialog = MagicMock()
    yes, cancel = object(), object()
    dialog.addButton.side_effect = [yes, cancel]
    dialog.clickedButton.return_value = yes if accept else cancel
    monkeypatch.setattr(module, 'FCMessageBox', lambda **kwargs: dialog)
    previous = defaults[key]
    fields[key].set_value(requested)
    manager.on_save_button(save_to_file=False)
    expected = requested if accept else previous
    assert fields[key].get_value() == expected
    assert defaults[key] == app.options[key] == defaults.current_defaults[key] == expected
    assert app.options['gerber_bboxmargin'] == 0.123
    assert settings.value(key.removeprefix('global_'), previous, type=type(previous)) == expected
    assert app.on_app_restart.call_count == int(accept)
    # A subsequent ordinary Save must neither re-prompt nor resurrect a declined change.
    manager.on_save_button(save_to_file=True)
    dialog.exec.assert_called_once()
    saved = AppDefaults(beta=False)
    saved.load(str(tmp_path / 'current_defaults.FlatConfig'), MagicMock())
    assert saved[key] == expected
    manager.deleteLater()
    group.deleteLater()


def _clear_gui_settings(tmp_path, monkeypatch, answer):
    """Preferences -> Clear GUI Settings on an ini file (not the user's settings), answered with the given role."""
    import appGUI.MainGUI as module
    settings = QtCore.QSettings(str(tmp_path / 'gui.ini'), QtCore.QSettings.Format.IniFormat)
    settings.setValue('theme', 'dark')
    settings.setValue('menu_show_text', False)
    monkeypatch.setattr(module.QtCore, 'QSettings', lambda *args: settings)
    monkeypatch.setattr(module, 'QSettings', lambda *args: settings)
    box = MagicMock()
    box.addButton.side_effect = lambda text, role: role
    box.clickedButton.return_value = answer
    monkeypatch.setattr(module, 'FCMessageBox', lambda **kwargs: box)
    gui = MagicMock()
    gui.app.resource_location = 'assets/resources'
    MainGUI.on_gui_clear(gui)
    box.exec.assert_called_once_with()
    return settings


def test_clear_gui_settings_answered_no_keeps_the_theme(qapp, tmp_path, monkeypatch):
    # the panels made later (ObjectUI, PlotCanvas, Distance...) read the theme from QSettings
    settings = _clear_gui_settings(tmp_path, monkeypatch, QtWidgets.QMessageBox.ButtonRole.NoRole)
    assert settings.value('theme') == 'dark'
    assert settings.contains('menu_show_text')


def test_clear_gui_settings_answered_yes_clears_to_the_light_theme(qapp, tmp_path, monkeypatch):
    settings = _clear_gui_settings(tmp_path, monkeypatch, QtWidgets.QMessageBox.ButtonRole.YesRole)
    assert not settings.contains('menu_show_text')
    # the readers use 'light' when the key is missing
    assert settings.value('theme', 'light') == 'light'
