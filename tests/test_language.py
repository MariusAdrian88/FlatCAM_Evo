"""
The language names, the fall back to English (appTranslation.languages_dict, apply_language()) and the right-to-left
interface of a right-to-left language (set_layout_direction()).

    python -m pytest tests/test_language.py
"""

import builtins
import os
import runpy
import shutil
import tempfile
import types
import unittest
import unittest.mock

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

import appTranslation as fcTranslate


class TestRuntimeMessages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import i18n_tool
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.strings = i18n_tool.extract()[0]

    def translate(self, text):
        # Only messages that reach extraction can be present in a translator's catalog.
        return 'translated: ' + text if text in self.strings else text

    def test_calculator_validation_is_translated_before_display(self):
        from appPlugins.ToolCalculators import Calculator, CalculatorGen
        tool = types.SimpleNamespace(
            ui=types.SimpleNamespace(tipDia_entry=unittest.mock.Mock(get_value=lambda: 1),
                                     tipAngle_entry=unittest.mock.Mock(get_value=lambda: 180),
                                     cutDepth_entry=unittest.mock.Mock(get_value=lambda: -1)),
            ui_disconnect=lambda: None, ui_connect=lambda: None,
            app=types.SimpleNamespace(inform=unittest.mock.Mock()))
        with unittest.mock.patch.object(builtins, '_', self.translate, create=True):
            del builtins._
            model = runpy.run_path(CalculatorGen.__file__)
            with self.assertRaises(ValueError) as error:
                model['v_shape_diameter'](1, 180, -1)
            self.assertEqual('Tip angle must be between 0 and 180 degrees.', str(error.exception))
            builtins._ = self.translate
            with self.subTest('translator installed after model import'):
                with self.assertRaises(ValueError) as error:
                    model['v_shape_diameter'](1, 180, -1)
                self.assertEqual('translated: Tip angle must be between 0 and 180 degrees.', str(error.exception))
            with unittest.mock.patch.object(Calculator, 'v_shape_diameter', model['v_shape_diameter']):
                Calculator.ToolCalculator.on_calculate_tool_dia(tool)
        self.assertEqual('[ERROR_NOTCL] translated: Tip angle must be between 0 and 180 degrees.',
                         tool.app.inform.emit.call_args.args[0])

    def test_extract_errors_and_skip_counts_use_extractable_translations(self):
        import tclCommands.TclCommandExtract
        from appPlugins.ToolExtract import ExtractGen
        with unittest.mock.patch.object(fcTranslate, 'apply_language'), \
                unittest.mock.patch.object(builtins, '_', self.translate):
            command_class = runpy.run_path('tclCommands/TclCommandExtract.py')['TclCommandExtract']
            command = command_class.__new__(command_class)
            result = ExtractGen.ExtractResult('drills', error=ExtractGen.ERR_NO_APERTURES,
                                             skipped={ExtractGen.SKIP_EMPTY: 3})
            self.assertEqual('translated: There are no apertures to use. Check the apertures and the pad kinds.',
                             command._fail_text(result))
            self.assertEqual(' (3 translated: pads skipped: empty shape)', command._skipped_text(result))
            result.error, result.skipped = 'unknown', {'unknown reason': 2, ExtractGen.SKIP_EMPTY: 0}
            self.assertEqual('translated: Extract failed: unknown', command._fail_text(result))
            self.assertEqual(' (2 unknown reason)', command._skipped_text(result))

    def test_invert_error_is_translated_in_the_tcl_output(self):
        import tclCommands.TclCommandInvertGerber
        from test_tcl_small_commands import make_invert, invert_source
        from test_tcl_audit_regressions import FCShell
        from shapely import box
        _command, app = make_invert({'board': invert_source('board', box(0, 0, 1, 1), None)})
        with unittest.mock.patch.object(fcTranslate, 'apply_language'), \
                unittest.mock.patch.object(builtins, '_', self.translate):
            command_class = runpy.run_path('tclCommands/TclCommandInvertGerber.py')['TclCommandInvertGerber']
            command = command_class(app)
            with self.assertRaises(FCShell.TclErrorException) as error:
                command.execute({'name': 'board'}, [])
        self.assertIn('translated: The object has no bounds to invert.', str(error.exception))
        app.app_obj.new_object.assert_not_called()

    def test_rules_status_is_translated_in_the_result_row(self):
        from appPlugins.ToolRulesCheck import RulesCheckUI
        ui = RulesCheckUI.RulesUI.__new__(RulesCheckUI.RulesUI)
        ui.results_table = QtWidgets.QTableWidget(0, 6)
        ui.summary_label = QtWidgets.QLabel()
        ui.decimals = 4
        with unittest.mock.patch.object(RulesCheckUI, '_', self.translate, create=True):
            ui.fill_results([{'rule': 'clearance', 'error': 'invalid geometry'},
                             {'rule': 'holes', 'checked': False}])
        rows = {ui.results_table.item(row, 0).text(): ui.results_table.item(row, 1).text()
                for row in range(ui.results_table.rowCount())}
        self.assertEqual({'clearance': 'translated: ERROR', 'holes': 'translated: NOTHING TO CHECK'}, rows)
        self.assertEqual('translated: 0 violations in 0 rules', ui.summary_label.text())


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

    def _fake_locale(self, *language_dirs):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for lang in language_dirs:
            os.makedirs(os.path.join(folder, lang, 'LC_MESSAGES'))
        fcTranslate.translations.clear()
        self.addCleanup(fcTranslate.translations.clear)
        return folder

    def test_a_missing_or_corrupt_catalog_shows_the_source_strings(self):
        # the language is listed, but its catalog cannot be used; the previous language must not stay installed
        for damage in ('missing', 'corrupt'):
            with self.subTest(damage=damage):
                self.assertEqual('العربية', fcTranslate.apply_language('strings', lang='العربية'))
                self.assertEqual('التفضيلات', builtins.__dict__['_']('Preferences'))
                folder = self._fake_locale('de')
                if damage == 'corrupt':
                    with open(os.path.join(folder, 'de', 'LC_MESSAGES', 'strings.mo'), 'wb') as catalog:
                        catalog.write(b'not a catalog')
                with unittest.mock.patch.object(fcTranslate, 'locale_dir', lambda: folder):
                    self.assertEqual('Deutsch', fcTranslate.apply_language('strings', lang='Deutsch'))
                    self.assertEqual('Preferences', builtins.__dict__['_']('Preferences'))


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
