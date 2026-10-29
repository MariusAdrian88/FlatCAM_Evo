"""The Preferences form shows and saves the project keys of the active project, and the values it keeps valid."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    def test_change_of_options_writes_the_project_value(self):
        manager, defaults, options = make_manager()
        defaults['gerber_bboxmargin'] = 0.1
        options['gerber_bboxmargin'] = 0.5
        manager.defaults_write_form_field('gerber_bboxmargin')
        self.assertEqual(manager.defaults_form_fields['gerber_bboxmargin'].value, 0.5)

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

    def test_show_writes_the_form_when_there_are_no_edits(self):
        manager, _defaults, options = make_manager()
        options['gerber_bboxmargin'] = 0.5
        with patch.object(PreferencesUIManager, 'init_preferences_gui'), \
                patch.object(PreferencesUIManager, 'pref_connect'), \
                patch.object(PreferencesUIManager, '_PreferencesUIManager__init_color_pickers'):
            manager.show_preferences_gui()
            self.assertEqual(manager.defaults_form_fields['gerber_bboxmargin'].value, 0.5)
            manager.defaults_form_fields['gerber_bboxmargin'].value = 0.9
            manager.preferences_changed_flag = True
            manager.show_preferences_gui()
        self.assertEqual(manager.defaults_form_fields['gerber_bboxmargin'].value, 0.9)


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

if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
