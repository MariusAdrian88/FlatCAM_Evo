"""The Preferences form shows and saves the project keys of the active project, and the values it keeps valid."""
import os
import sys
import importlib
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PyQt6 import QtCore, QtWidgets

from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
from appGUI.preferences.PreferencesUIManager import PreferencesUIManager
from appGUI.preferences.cncjob.CNCJobGenPrefGroupUI import CNCJobGenPrefGroupUI
from appGUI.preferences.excellon.ExcellonGenPrefGroupUI import ExcellonGenPrefGroupUI
from appGUI.preferences.general.GeneralGUIPrefGroupUI import GeneralGUIPrefGroupUI
from appGUI.preferences.gerber.GerberGenPrefGroupUI import GerberGenPrefGroupUI
from defaults import AppDefaults, AppOptions


class Field:
    def __init__(self, value=None):
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


def make_manager():
    defaults = AppDefaults(beta=False)
    options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    manager = PreferencesUIManager.__new__(PreferencesUIManager)
    QtCore.QObject.__init__(manager)
    manager.defaults = defaults
    manager.ui = SimpleNamespace(app=SimpleNamespace(options=options, log=Mock()))
    manager.preferences_changed_flag = False
    manager.defaults_form_fields = {
        'gerber_bboxmargin': Field(), 'gerber_bboxrounded': Field(), 'global_gridx': Field()}
    return manager, defaults, options


def make_group(group_class):
    """A real Preferences group on a mock App; the options write the application keys to the defaults."""
    defaults = AppDefaults(beta=False)
    app = MagicMock(decimals=4, resource_location='assets/resources')
    app.defaults = defaults
    app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    with patch.object(OptionsGroupUI, 'app', app):
        group = group_class(app)
    group.app = app     # the handlers read OptionsGroupUI.app, which the App sets
    return group, app, defaults


class TestPreferencesProjectKeys(unittest.TestCase):
    def test_form_shows_project_value_for_project_key(self):
        manager, defaults, options = make_manager()
        defaults['gerber_bboxmargin'] = 0.1
        options['gerber_bboxmargin'] = 0.5
        defaults['global_gridx'] = 2.0
        manager.defaults_write_form()
        self.assertEqual(manager.defaults_form_fields['gerber_bboxmargin'].value, 0.5)
        self.assertEqual(manager.defaults_form_fields['global_gridx'].value, 2.0)


    def test_read_form_writes_only_changed_project_keys(self):
        manager, defaults, options = make_manager()
        defaults['gerber_bboxmargin'] = 0.1
        defaults['gerber_bboxrounded'] = False
        options['gerber_bboxmargin'] = 0.5
        options['gerber_bboxrounded'] = True
        fields = manager.defaults_form_fields
        fields['gerber_bboxmargin'].value = 0.7
        fields['gerber_bboxrounded'].value = True
        fields['global_gridx'].value = 2.5
        manager.defaults_read_form()
        self.assertEqual(options['gerber_bboxmargin'], 0.7)
        self.assertEqual(defaults['gerber_bboxmargin'], 0.7)
        self.assertIs(options['gerber_bboxrounded'], True)
        self.assertIs(defaults['gerber_bboxrounded'], False)
        self.assertEqual(defaults['global_gridx'], 2.5)


class TestPreferencesColors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_save_keeps_the_alpha_of_a_colour(self):
        manager, defaults, _options = make_manager()
        defaults['gerber_plot_fill'] = '#BBF26880'
        manager.defaults_form_fields = {
            'gerber_plot_fill': Field('#FF0000'), 'excellon_plot_fill': Field('#FF00'),
            'global_sel_line': Field('#00FF0040'), 'global_draw_color': Field('#FF0000')}
        manager.defaults_read_form()
        self.assertEqual(defaults['gerber_plot_fill'], '#FF000080')
        self.assertEqual(defaults['excellon_plot_fill'], defaults.factory_defaults['excellon_plot_fill'])
        self.assertEqual(defaults['global_sel_line'], '#00FF0040')
        self.assertEqual(defaults['global_draw_color'], '#FF0000')

    def test_alpha_slider_gives_a_valid_colour_that_save_keeps(self):
        for group_class, slider, fields in (
                (CNCJobGenPrefGroupUI, 'cncjob_alpha_entry',
                 {'cncjob_travel_fill': 'tfill_color_entry', 'cncjob_travel_line': 'tline_color_entry'}),
                (GerberGenPrefGroupUI, 'gerber_alpha_entry',
                 {'gerber_plot_fill': 'fill_color_entry', 'gerber_plot_line': 'line_color_entry'}),
                (ExcellonGenPrefGroupUI, 'excellon_alpha_entry',
                 {'excellon_plot_fill': 'fill_color_entry', 'excellon_plot_line': 'line_color_entry'}),
                (GeneralGUIPrefGroupUI, 'left_right_alpha_entry',
                 {'global_sel_fill': 'sf_color_entry', 'global_sel_line': 'sl_color_entry'}),
                (GeneralGUIPrefGroupUI, 'right_left_alpha_entry',
                 {'global_alt_sel_fill': 'alt_sf_color_entry', 'global_alt_sel_line': 'alt_sl_color_entry'})):
            with self.subTest(slider=slider):
                group, app, defaults = make_group(group_class)
                self.addCleanup(group.deleteLater)
                manager = make_manager()[0]
                manager.defaults = defaults
                manager.ui = SimpleNamespace(app=app)
                manager.defaults_form_fields = {key: getattr(group, name) for key, name in fields.items()}
                manager.defaults_write_form()
                getattr(group, slider).set_value(10)
                expected = {key: defaults.factory_defaults[key][:7] + '0a' for key in fields}
                self.assertEqual({key: app.options[key] for key in fields}, expected)    # the canvas colours
                manager.defaults_read_form()    # Save
                self.assertEqual({key: defaults[key] for key in fields}, expected)


class TestExcellonPreferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_restore_defaults_button_sets_the_factory_format(self):
        group, app, defaults = make_group(ExcellonGenPrefGroupUI)
        self.addCleanup(group.deleteLater)
        fields = {key: getattr(group, key + '_entry') for key in (
            'excellon_format_upper_in', 'excellon_format_lower_in', 'excellon_format_upper_mm',
            'excellon_format_lower_mm')}
        fields.update(excellon_zeros=group.excellon_zeros_radio, excellon_units=group.excellon_units_radio)
        app.preferencesUiManager.defaults_form_fields = fields
        group.excellon_zeros_radio.set_value('L')
        group.excellon_units_radio.set_value('METRIC')
        group.excellon_format_lower_in_entry.set_value(3)
        group.excellon_defaults_button.click()
        self.assertEqual({key: field.get_value() for key, field in fields.items()},
                         {key: defaults.factory_defaults[key] for key in fields})    # zeros: 'T'


# ------------------------------------------------------------------------------------------------------------------
# The Preferences window: the real forms of MainGUI, the real PreferencesUIManager, opened as the menu opens it
# ------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def pref_app(tmp_path, monkeypatch):
    """An App mock with a real MainGUI and PreferencesUIManager; yields it with the Preferences window open."""
    from appGUI.MainGUI import MainGUI
    from appHandlers.appUIActions import AppUIActions
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    defaults = AppDefaults(beta=False)
    app = MagicMock(decimals=4, resource_location='assets/resources', data_path=str(tmp_path))
    app.defaults = defaults
    app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    monkeypatch.setattr(OptionsGroupUI, 'app', app)
    ui = MainGUI(app)
    app.ui = ui
    app.preferencesUiManager = PreferencesUIManager(defaults, str(tmp_path), ui, MagicMock(), app.options)
    app.preferencesUiManager.defaults_write_form()
    app.ui_actions = AppUIActions(app)
    app.on_preferences = app.ui_actions.on_preferences
    app.on_toggle_preferences = app.ui_actions.on_toggle_preferences
    app.on_preferences()
    yield app
    if ui.preferences_window is not None:
        ui.preferences_window.close_quietly()
    # a full MainGUI left to the interpreter exit crashes the process
    ui.close()
    ui.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
    qapp.processEvents()


def answer_save_question(monkeypatch, answer, module='appGUI.preferences.PreferencesUIManager'):
    """Answer the question of the module (default: 'Save Preferences') with 'Yes' or 'No'; returns the mock dialog."""
    module = importlib.import_module(module)
    dialog = MagicMock()
    yes, no = object(), object()
    dialog.addButton.side_effect = [yes, no]
    dialog.clickedButton.return_value = yes if answer == 'Yes' else no
    monkeypatch.setattr(module, 'FCMessageBox', lambda **kwargs: dialog)
    return dialog


def test_every_key_writes_and_reads_its_widget_through_the_window(pref_app):
    window = pref_app.ui.preferences_window
    manager = pref_app.preferencesUiManager
    for page in window.pages:
        window.show_page(page)
        assert window.stack.currentWidget().isAncestorOf(page.group)
    fields = manager.defaults_form_fields
    assert len(fields) > 600
    assert [key for key, field in fields.items() if window.page_of(field) is None] == []
    # the form normalizes some factory values (a tuple written as text, a combo index): write, read, then the next
    # round trip must keep every value
    manager.defaults_write_form()
    manager.defaults_read_form()
    shown = {key: deepcopy(pref_app.defaults.get(key)) for key in fields}
    for key, field in fields.items():     # the form is written by key, not by tab
        manager.defaults_write_form_field(key)
    manager.defaults_read_form()
    assert {key: pref_app.defaults.get(key) for key in fields} == shown


def test_close_asks_to_save_an_edit_and_cancel_discards_it(pref_app, monkeypatch):
    window = pref_app.ui.preferences_window
    manager = pref_app.preferencesUiManager
    cutz = pref_app.ui.plugin_pref_form.tools_drill_group.cutz_entry
    factory = pref_app.defaults['tools_drill_cutz']

    cutz.set_value(-2.5)        # an edit: valueChanged marks the Preferences as edited
    assert manager.preferences_changed_flag
    dialog = answer_save_question(monkeypatch, 'No')
    window.close()
    dialog.exec.assert_called_once()
    assert not window.isVisible()
    assert pref_app.defaults['tools_drill_cutz'] == factory

    pref_app.on_preferences()
    assert cutz.get_value() == factory
    cutz.set_value(-2.5)
    window.cancel_button.click()
    assert not window.isVisible()
    assert pref_app.defaults['tools_drill_cutz'] == factory
    pref_app.on_preferences()
    assert cutz.get_value() == factory


def test_ctrl_s_in_the_window_applies_the_preferences(pref_app):
    from PyQt6.QtTest import QTest
    window = pref_app.ui.preferences_window
    window.show_page(window.pages[0])
    cutz = pref_app.ui.plugin_pref_form.tools_drill_group.cutz_entry
    cutz.set_value(-2.5)
    QTest.keyClick(window.tree, QtCore.Qt.Key.Key_S, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert pref_app.defaults['tools_drill_cutz'] == -2.5
    assert not pref_app.preferencesUiManager.preferences_changed_flag
    assert window.isVisible()

    window.save_button.click()
    assert not window.isVisible()
    # A project switch fills the hidden form. Reopening it must not turn that update into an unsaved edit.
    pref_app.options['tools_drill_cutz'] = -3.5
    pref_app.preferencesUiManager.defaults_write_form()
    pref_app.on_preferences()
    assert cutz.get_value() == -3.5
    assert not pref_app.preferencesUiManager.preferences_changed_flag
    cutz.set_value(-4.5)
    assert pref_app.preferencesUiManager.preferences_changed_flag


def test_search_filters_the_pages_by_label_and_tooltip(pref_app):
    window = pref_app.ui.preferences_window
    drill = window.ui.plugin_pref_form.tools_drill_group
    window.search('FEEDRATE')
    visible = [page for page in window.pages if not page.item.isHidden()]
    assert window.page_of(drill) in visible and len(visible) < len(window.pages) / 2
    assert all(int(page.item.text(1)) > 0 for page in visible)
    assert window.current_page() is visible[0]          # the first page with a match is shown
    assert all(window.tree.topLevelItem(i).isHidden() == (window.tree.topLevelItem(i).text(0) == 'Application')
               for i in range(window.tree.topLevelItemCount()) if window.tree.topLevelItem(i).text(0) in
               ('Application', 'Plugins'))
    feedrate_label = window.label_of(drill.feedrate_z_entry)
    assert 'Feedrate Z' in feedrate_label.text() and feedrate_label.styleSheet()     # highlighted
    # words of a tooltip only: the tooltip of Cut Z
    window.search('below the copper surface')
    assert window.page_of(drill) in [page for page in window.pages if not page.item.isHidden()]
    window.search('')
    assert not any(page.item.isHidden() for page in window.pages)
    assert not feedrate_label.styleSheet()


def test_changed_settings_are_marked_and_reset_page_puts_the_factory_values(pref_app):
    from PyQt6.QtTest import QTest
    window = pref_app.ui.preferences_window
    manager = pref_app.preferencesUiManager
    drill = window.ui.plugin_pref_form.tools_drill_group
    page = window.page_of(drill)
    window.show_page(page)
    # the factory values in the form: no setting is marked on any page
    window.refresh_markers()
    assert {p.name: window.changed_keys(p) for p in window.pages if window.changed_keys(p)} == {}
    assert len(window.unmarked_keys) < 15

    # a saved value that differs from the factory value is marked, on its row and on its page in the tree
    drill.feedrate_z_entry.set_value(123.0)
    drill.cutz_entry.set_value(-2.5)
    manager.on_save_button(save_to_file=False)
    for _ in range(200):        # the marks follow the edit by themselves
        if len(window.marker_rows(page)) == 2:
            break
        QTest.qWait(10)
    assert window.changed_keys(page) == ['tools_drill_cutz', 'tools_drill_feedrate_z']
    assert not page.item.icon(1).isNull() and not page.item.parent().icon(1).isNull()     # the dots in the tree
    assert len(page.content.bars) == 2 and page.content.buttons[1].isVisibleTo(page.content)

    # Reset page: the factory values in the form, kept only by Save or Apply
    window.reset_page_button.click()
    assert drill.cutz_entry.get_value() == pref_app.defaults.factory_defaults['tools_drill_cutz']
    assert pref_app.defaults['tools_drill_cutz'] == -2.5
    window.refresh_markers()
    assert window.changed_keys(page) == [] and page.item.icon(1).isNull()

    # Cancel throws the reset away
    window.cancel_button.click()
    pref_app.on_preferences()
    assert drill.cutz_entry.get_value() == -2.5

    # the arrow of a row resets only that setting
    window.show_page(page)
    window.refresh_markers()
    window.reset_key('tools_drill_feedrate_z')
    assert drill.feedrate_z_entry.get_value() == pref_app.defaults.factory_defaults['tools_drill_feedrate_z']
    assert drill.cutz_entry.get_value() == -2.5


@pytest.mark.parametrize('direction', [QtCore.Qt.LayoutDirection.LeftToRight])
def test_reset_arrows_on_the_same_row_reset_only_their_setting(pref_app, direction):
    from PyQt6.QtTest import QTest
    qapp = QtWidgets.QApplication.instance()
    previous = qapp.layoutDirection()
    qapp.setLayoutDirection(direction)
    try:
        window = pref_app.ui.preferences_window
        fields = pref_app.preferencesUiManager.defaults_form_fields
        keys = ('gerber_plot', 'gerber_solid', 'gerber_multicolored')
        expected = {key: not pref_app.defaults.factory_defaults[key] for key in keys}
        for key, value in expected.items():
            fields[key].set_value(value)
        page = window.page_of(fields[keys[0]])
        window.show_page(page)
        for key in keys:
            qapp.processEvents()
            window.refresh_markers()
            button = next(b for b in page.content.buttons if b.isVisible() and b.property('option_key') == key)
            page.widget.ensureWidgetVisible(button)
            target = page.content.childAt(button.geometry().center())
            QTest.mouseClick(target, QtCore.Qt.MouseButton.LeftButton)
            expected[key] = pref_app.defaults.factory_defaults[key]
            assert {key: fields[key].get_value() for key in keys} == expected
    finally:
        qapp.setLayoutDirection(previous)


def test_keyboard_shortcuts_page_reports_a_key_in_use_before_it_is_assigned(pref_app, tmp_path):
    from PyQt6.QtTest import QTest
    from appGUI import shortcuts
    registry = shortcuts.registry()
    window = pref_app.ui.preferences_window
    editor = pref_app.ui.general_pref_form.general_shortcuts_group.shortcuts_editor
    page = window.page_of(editor)
    assert (page.section, page.name) == ('Application', 'Keyboard Shortcuts')
    window.show_page(page)
    try:
        # search by name and by key
        editor.search_entry.setText('new gerber')
        assert editor.visible_ids() == ['app.new_gerber']
        editor.search_entry.clear()
        QTest.keyClick(editor.find_key_entry, QtCore.Qt.Key.Key_S, QtCore.Qt.KeyboardModifier.ControlModifier)
        assert {'app.save_project', 'geo_editor.exit', 'grb_editor.exit'} <= set(editor.visible_ids())
        assert all(registry.key(aid) == 'Ctrl+S' for aid in editor.visible_ids())
        editor.find_key_entry.clear()

        # Ctrl+S for New Gerber: the modifier combo box and the key field; it is reported, not assigned
        editor.select('app.new_gerber')
        editor.modifier_combo.setCurrentIndex(editor.modifier_combo.findData('Ctrl'))
        QTest.keyClick(editor.key_entry, QtCore.Qt.Key.Key_S)
        assert not editor.assign()
        assert 'Ctrl+S' in editor.conflict_text() and 'Save Project' in editor.conflict_text()
        assert editor.get_value() == {}
        editor.cancel()

        # a free key is assigned; the page and its row are marked, the factory key is shown
        QTest.keyClick(editor.key_entry, QtCore.Qt.Key.Key_B,
                       QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier)
        assert editor.modifier_combo.currentData() == 'Ctrl+Shift'
        assert editor.assign()
        window.refresh_markers()
        assert window.changed_keys(page) == ['global_shortcuts'] and not page.item.icon(1).isNull()
        assert window.marker_rows(page) == []       # the row of the table is marked, not the whole page
        row = editor.rows['app.new_gerber']
        assert not editor.table.item(row, editor.COLUMN_COMMAND).icon().isNull()

        # "Assign it here and clear the other"
        editor.select('app.jump')
        assert not editor.request('app.jump', 'Ctrl+Shift+B')
        editor.confirm()
        assert editor.get_value() == {'app.jump': 'Ctrl+Shift+B', 'app.new_gerber': ''}

        # reset one, export, reset all, import
        editor.reset('app.new_gerber')
        assert editor.get_value() == {'app.jump': 'Ctrl+Shift+B'}
        path = str(tmp_path / 'keys.json')
        assert editor.export_file(path)
        editor.reset_all()
        assert editor.get_value() == {}
        assert editor.import_file(path)
        assert editor.get_value() == {'app.jump': 'Ctrl+Shift+B'}

        # the keys work after Apply; the F3 list and the menu show the new key
        assert registry.key('app.jump') == 'J'
        pref_app.preferencesUiManager.on_save_button(save_to_file=False)
        assert registry.key('app.jump') == 'Ctrl+Shift+B'
        assert 'Ctrl+Shift+B' in pref_app.ui.shortcuts_tab.sh_app.toPlainText()
        assert pref_app.ui.menueditjump.text().endswith('\tCtrl+Shift+B')

        # Reset must ask before taking a factory key that was assigned to another command.
        editor.reset_all()
        assert not editor.request('app.jump', 'B')
        editor.confirm()
        editor.reset('app.new_gerber')
        assert editor.get_value() == {'app.jump': 'B', 'app.new_gerber': ''}
        assert editor.conflict_text()
        editor.confirm()
        assert editor.get_value() == {'app.jump': ''}

        # A main-phase key must not take a fixed key used while drawing.
        assert not editor.request('geo_editor.undo', 'T')
        assert 'Corner Modes' in editor.conflict_text()
        assert not editor.replace_button.isVisibleTo(editor)
    finally:
        registry.reset_all()


@pytest.mark.parametrize('overrides', [
    {'app.jump': 'B'},
    {'app.jump': 'not a key'},
])
def test_shortcut_import_rejects_conflicts_and_invalid_keys_without_losing_edits(pref_app, tmp_path, monkeypatch,
                                                                              overrides):
    import json
    editor = pref_app.ui.general_pref_form.general_shortcuts_group.shortcuts_editor
    assert editor.request('app.jump', 'Ctrl+Shift+B')
    saved = editor.get_value()
    path = tmp_path / 'keys.json'
    path.write_text(json.dumps({'shortcuts': overrides}), encoding='utf-8')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', Mock())
    assert not editor.import_file(str(path))
    assert editor.get_value() == saved


def test_changed_shortcuts_survive_a_restart(tmp_path):
    from appGUI import shortcuts
    registry = shortcuts.registry()
    defaults = AppDefaults(beta=False)
    assert defaults['global_shortcuts'] == {}       # factory: every action has its factory key
    # only the changed keys are saved; an id that no longer exists is ignored
    defaults['global_shortcuts'] = {'app.new_gerber': 'Ctrl+Shift+B', 'app.no_such_action': 'F9'}
    path = str(tmp_path / 'current_defaults.FlatConfig')
    defaults.write(filename=path)
    restarted = AppDefaults(beta=False)
    restarted.load(filename=path, inform=Mock())
    try:
        shortcuts.apply_options(SimpleNamespace(defaults=restarted))
        assert registry.key('app.new_gerber') == 'Ctrl+Shift+B'
        assert registry.overrides() == {'app.new_gerber': 'Ctrl+Shift+B'}
    finally:
        registry.reset_all()


if __name__ == '__main__':
    unittest.main()
