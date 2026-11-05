"""
The language names, the fall back to English (appTranslation.languages_dict, apply_language()) and the right-to-left
interface of a right-to-left language (set_layout_direction()).

    python -m pytest tests/test_language.py
"""

import builtins
import types
import unittest
import unittest.mock

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

import appTranslation as fcTranslate


class TestLanguageNames(unittest.TestCase):
    def test_each_name_is_written_in_its_own_language(self):
        self.assertEqual('العربية', fcTranslate.languages_dict['ar'])
        self.assertEqual('Deutsch', fcTranslate.languages_dict['de'])
        self.assertEqual('Português do Brasil', fcTranslate.languages_dict['pt_BR'])
        self.assertEqual('Türkçe', fcTranslate.languages_dict['tr'])
        # the Russian name is all Cyrillic (it began with a Latin 'P')
        self.assertEqual('Русский', fcTranslate.languages_dict['ru'])
        self.assertTrue(all('Ѐ' <= ch <= 'ӿ' for ch in fcTranslate.languages_dict['ru']))

class TestApplyLanguage(unittest.TestCase):
    def setUp(self):
        self._saved = builtins.__dict__.get('_')
        self.addCleanup(self._restore)

    def _restore(self):
        fcTranslate.apply_language('strings', lang='English')
        if self._saved is not None:
            builtins.__dict__['_'] = self._saved

    def test_an_unknown_name_falls_back_to_english(self):
        # names written by older versions and names of languages without a translation
        self.assertEqual('Deutsch', fcTranslate.apply_language('strings', lang='Deutsch'))
        for name in ('Deutsche', 'Türk', 'Portugues do Brasil', 'Pусский', 'Klingon'):
            with self.subTest(name=name):
                self.assertEqual('English', fcTranslate.apply_language('strings', lang=name))
                self.assertEqual('Preferences', builtins.__dict__['_']('Preferences'))

    def _fake_settings(self, value):
        store = {'language': value}

        class FakeSettings:
            def __init__(self, *args):
                pass

            def contains(self, key):
                return key in store

            def value(self, key, *args, **kwargs):
                return store[key]

            def setValue(self, key, val):
                store[key] = val

        patcher = unittest.mock.patch.object(fcTranslate, 'QSettings', FakeSettings)
        patcher.start()
        self.addCleanup(patcher.stop)
        return store

    def test_an_unknown_name_in_the_settings_is_written_back_as_english(self):
        # else Preferences -> Apply of English compares with the old name and asks for a restart
        store = self._fake_settings('Deutsche')
        self.assertEqual('English', fcTranslate.apply_language('strings'))
        self.assertEqual('English', store['language'])


LTR = Qt.LayoutDirection.LeftToRight
RTL = Qt.LayoutDirection.RightToLeft


class TestRightToLeft(unittest.TestCase):
    """The language is applied at start-up; a change of the language restarts the application."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.addCleanup(QtWidgets.QApplication.setLayoutDirection, Qt.LayoutDirection.LayoutDirectionAuto)

    def test_an_rtl_language_mirrors_the_application(self):
        saved = builtins.__dict__.get('_')
        self.addCleanup(lambda: builtins.__dict__.__setitem__('_', saved) if saved is not None else None)
        self.addCleanup(fcTranslate.apply_language, 'strings', 'English')
        self.assertEqual('العربية', fcTranslate.apply_language('strings', lang='العربية'))
        self.assertEqual('التفضيلات', builtins.__dict__['_']('Preferences'))
        fcTranslate.set_layout_direction('العربية')
        self.assertEqual(RTL, QtWidgets.QApplication.layoutDirection())
        # a new UI is mirrored with nothing extra
        self.assertEqual(RTL, QtWidgets.QWidget().layoutDirection())

    def test_an_ltr_language_keeps_the_application_left_to_right(self):
        for name in [n for code, n in fcTranslate.languages_dict.items() if code != 'ar']:
            with self.subTest(name=name):
                fcTranslate.set_layout_direction(name)
                self.assertEqual(LTR, QtWidgets.QApplication.layoutDirection())

    def test_the_check_box_indicator_goes_after_the_text_in_both_directions(self):
        from appGUI.GUIElements import FCCheckBox, opposite_direction
        for name, app_direction, box_direction in (('English', LTR, RTL), ('العربية', RTL, LTR)):
            with self.subTest(name=name):
                QtWidgets.QApplication.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
                fcTranslate.set_layout_direction(name)
                self.assertEqual(app_direction, QtWidgets.QApplication.layoutDirection())
                box = FCCheckBox('Plot')
                box.setLayoutDirection(opposite_direction())
                self.assertEqual(box_direction, box.layoutDirection())

    def test_code_shell_coordinates_and_canvas_stay_left_to_right(self):
        from appEditors.text_editor.code_edit import CodeEdit
        from appGUI.GUIElements import CoordsToolbar, FCJog
        from appPlugins.ToolShell.ShellUI import TermWidget

        fcTranslate.set_layout_direction('العربية')
        widgets = {
            'code editor': CodeEdit(theme='default'),
            'Tcl shell': TermWidget('test', types.SimpleNamespace(resource_location='assets/resources')),
            'coordinates': CoordsToolbar('Coordinates Toolbar'),
        }
        for what, widget in widgets.items():
            with self.subTest(what=what):
                self.assertEqual(LTR, widget.layoutDirection())

        jog = FCJog(types.SimpleNamespace(resource_location='assets/resources'))
        jog.resize(jog.sizeHint())
        jog.layout().activate()
        self.assertLess(jog.jog_left_button.x(), jog.jog_origin_button.x())
        self.assertLess(jog.jog_origin_button.x(), jog.jog_right_button.x())

        from appGUI.PlotCanvas import PlotCanvas
        from defaults import AppDefaults

        class MainWindow(QtWidgets.QMainWindow):
            def __getattr__(self, name):        # the status bar labels of the canvas
                return unittest.mock.MagicMock()

        defaults = AppDefaults()
        fcapp = unittest.mock.MagicMock(options=dict(defaults.defaults), defaults=defaults, app_units='MM',
                                        ui=MainWindow(), cursor_color_3D='#000000')
        canvas = PlotCanvas(fcapp)
        self.assertEqual(LTR, canvas.native.layoutDirection())


if __name__ == '__main__':
    unittest.main()
