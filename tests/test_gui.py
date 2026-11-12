


# ======================================================================
# merged from tests/test_gui_elements_input.py
# ======================================================================
"""Editing in the FlatCAM input widgets (GUIElements): the number fields and the tool tables."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtTest import QTest

from appGUI.GUIElements import FCDoubleSpinner, FCCheckBox, FCLabel, FCMicronSpinner, FCSpinner, FCTable, \
    checkbox_column_width


@pytest.fixture(scope='module', autouse=True)
def qt_application():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def spinner_with_800(cls):
    """A spinner with the range 0 to 1000 and the value 800, its text selected for typing."""
    spinner = cls()
    spinner.set_range(0, 1000)
    spinner.set_value(800)
    spinner.lineEdit().selectAll()
    return spinner


@pytest.mark.parametrize('cls', [FCDoubleSpinner])
def test_typed_number_over_the_maximum_keeps_the_previous_value(cls):
    spinner = spinner_with_800(cls)
    values = []
    spinner.valueChanged.connect(values.append)

    QTest.keyClicks(spinner.lineEdit(), '1500')
    QTest.keyClick(spinner.lineEdit(), QtCore.Qt.Key.Key_Return)

    assert spinner.get_value() == 800  # not 150, the last prefix in the range
    assert values == []  # the prefixes 1, 15 and 150 were not committed


@pytest.mark.parametrize('cls', [FCDoubleSpinner])
def test_typed_number_reaches_the_slots_of_the_keypad_enter(cls):
    spinner = spinner_with_800(cls)
    seen = []
    spinner.returnPressed.connect(lambda: seen.append(spinner.get_value()))

    QTest.keyClicks(spinner.lineEdit(), '950')
    QTest.keyClick(spinner, QtCore.Qt.Key.Key_Enter)

    assert spinner.get_value() == 950
    assert seen and set(seen) == {950}


class DragFromTable:
    """A drag enter event of a drag that started in the table (a real event has a source only during a drag)."""

    def __init__(self, table):
        self.table = table

    def source(self):
        return self.table

    def accept(self):
        pass


def tool_table():
    table = FCTable(drag_drop=True)
    table.setColumnCount(2)
    table.setRowCount(2)
    for row in range(2):
        for column in range(2):
            table.setItem(row, column, QtWidgets.QTableWidgetItem('%d' % (row + 1)))
    return table


def test_drag_that_leaves_the_tool_table_unblocks_its_signals():
    table = tool_table()
    edits = []
    table.itemChanged.connect(edits.append)

    table.dragEnterEvent(DragFromTable(table))
    assert table.signalsBlocked()
    table.dragLeaveEvent(QtGui.QDragLeaveEvent())  # the mouse is released outside the table, or Esc
    table.item(0, 1).setText('0.15')

    assert not table.signalsBlocked()
    assert edits == [table.item(0, 1)]


def test_micron_field_shows_the_units_of_the_project_and_keeps_micrometers():
    units = ['MM']
    label = FCLabel('')
    field = FCMicronSpinner(units=lambda: units[0], units_label=label)

    field.set_value(2.0)
    assert (field.value(), label.text(), field.get_value()) == (2.0, 'µm', 2.0)

    units[0] = 'IN'
    # the shown number keeps its units until the field is set again
    assert field.get_value() == 2.0
    field.set_value(2.0)
    # 2 um is 0.0787 mils; the value that the user does not change keeps its micrometers
    assert (round(field.value(), 3), label.text(), field.get_value()) == (0.079, 'mils', 2.0)

    field.lineEdit().selectAll()
    QTest.keyClicks(field.lineEdit(), '1')
    QTest.keyClick(field.lineEdit(), QtCore.Qt.Key.Key_Return)
    assert field.get_value() == pytest.approx(25.4)


# ======================================================================
# merged from tests/test_theme_color_scheme.py
# ======================================================================
"""
The 'default' and 'light' themes use colors made for a light window and 'dark' colors made for a dark one: Qt's color
scheme has to follow the theme, not the operating system.
"""

import os
import json
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

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


@pytest.mark.parametrize('legacy,appearance,styling', [
    ('dark', 'dark', 'custom'),
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


@pytest.mark.parametrize('appearance,os_dark,theme,styling', [
    ('light', True, 'light', 'default'), ('auto', True, 'dark', 'custom'),
])
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


@pytest.mark.parametrize('key,requested,has_settings', [
    ('global_appearance', 'dark', True),
])
@pytest.mark.parametrize('accept', [True])
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


# ======================================================================
# merged from tests/test_qt_style.py
# ======================================================================
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from PyQt6 import QtCore, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_isolation  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


class TestQtStyle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.native_style = cls.app.style().objectName()
        cls.subprocess_result = None
        # every test of this class changes the application style: 1 new process for the whole file
        if qt_isolation.crowded():
            cls.subprocess_result = qt_isolation.run_pytest(__file__)

    def run_in_subprocess(self):
        """True when the tests ran in a new process; then this test passes only if that run passed."""
        if self.subprocess_result is None:
            return False
        returncode, output = self.subprocess_result
        self.assertEqual(returncode, 0, output)
        return True

    def setUp(self):
        if self.subprocess_result is not None:
            return
        self.directory = tempfile.TemporaryDirectory()
        self.settings = QtCore.QSettings(
            str(Path(self.directory.name) / 'style.ini'), QtCore.QSettings.Format.IniFormat)
        self.app.setStyleSheet('')
        self.app.setStyle(self.native_style)
        self.app.setProperty('flatcam_style', None)

    def tearDown(self):
        if self.subprocess_result is not None:
            return
        self.app.setStyleSheet('')
        self.app.setStyle(self.native_style)
        self.app.setProperty('flatcam_style', None)
        self.settings.sync()
        del self.settings
        self.directory.cleanup()

    def startup(self):
        # Run the actual startup style block without launching the CAM application.
        source = (ROOT / 'flatcam.py').read_text(encoding='utf-8')
        block = source.split('    # apply style\n', 1)[1].split('    if settings.contains("font_size"):', 1)[0]
        exec(compile(textwrap.dedent(block), 'flatcam.py', 'exec'), {
            'app': self.app, 'QtWidgets': QtWidgets,
            'QSettings': lambda *args: self.settings})

    def test_startup_restores_names_and_handles_legacy_or_invalid_values(self):
        if self.run_in_subprocess():
            return
        keys = QtWidgets.QStyleFactory.keys()
        for saved, expected in [('Fusion', 'Fusion'), ('fUsIoN', 'Fusion'),
                                (str(keys.index('Fusion')), 'Fusion'),
                                ('999', self.native_style), ('-1', self.native_style),
                                ('missing-style', self.native_style), (None, self.native_style)]:
            with self.subTest(saved=saved):
                self.app.setStyle(self.native_style)
                self.settings.clear()
                if saved is not None:
                    self.settings.setValue('style', saved)
                self.startup()
                self.assertEqual(self.app.style().objectName().lower(), expected.lower())
                self.assertEqual(self.app.property('flatcam_style').lower(), expected.lower())
                if saved is not None:
                    self.assertEqual(self.settings.value('style').lower(), expected.lower())


# ======================================================================
# merged from tests/test_icon_colors.py
# ======================================================================
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets


def write_icon(path, colors):
    image = QtGui.QImage(len(colors), 1, QtGui.QImage.Format.Format_RGBA8888)
    for x, color in enumerate(colors):
        image.setPixelColor(x, 0, QtGui.QColor(*color))
    assert image.save(str(path))


class TestIconColors(unittest.TestCase):
    def test_only_single_color_icons_change_and_remain_recolorable(self):
        from appCommon.IconColors import recolor_icons

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mono = [(102, 173, 199, 255), (51, 87, 100, 128), (0, 0, 0, 0)]
            for name in ('tool.png', 'tool.ico', 'drill16_project.png', 'axis18.png',
                         'settings18.png', 'hud18.png', 'graylight12.png', 'active_static.png', 'red32.png',
                         'grid18_status.png'):
                write_icon(root / name, mono)
            write_icon(root / 'neutral.png', [(227, 233, 237, 255), (110, 114, 116, 64)])
            write_icon(root / 'white.png', [(255, 255, 255, 255)])
            write_icon(root / 'mixed.png', [(255, 0, 0, 255), (0, 255, 0, 255)])
            write_icon(root / 'logo.png', [(255, 0, 0, 255), (255, 255, 255, 255)])
            (root / 'dark_resources').mkdir()
            write_icon(root / 'dark_resources' / 'tool.png', mono)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}

            self.assertEqual(recolor_icons(root, '#aabbcc'), 4)
            for name in ('tool.png', 'tool.ico', 'neutral.png', 'white.png'):
                image = QtGui.QImage(str(root / name))
                self.assertEqual(image.pixelColor(0, 0).name(), '#aabbcc')
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(1, 0).alpha(), 128)
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(2, 0).alpha(), 0)
            for path, original in before.items():
                if path.name not in ('tool.png', 'tool.ico', 'neutral.png', 'white.png') or path.parent != root:
                    self.assertEqual(path.read_bytes(), original, path.name)
            self.assertEqual(recolor_icons(root, '#223344'), 4)
            self.assertEqual(QtGui.QImage(str(root / 'tool.png')).pixelColor(0, 0).name(), '#223344')
            self.assertEqual(recolor_icons(root, '#223344'), 0)
            with self.assertRaises(ValueError):
                recolor_icons(root, '#123')

    def test_invalid_image_does_not_change_any_files(self):
        from appCommon.IconColors import recolor_icons

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_icon(root / 'a.png', [(255, 255, 255, 255)])
            (root / 'z.png').write_bytes(b'invalid image')
            original = (root / 'a.png').read_bytes()
            with self.assertRaises(OSError):
                recolor_icons(root, '#66adc7')
            self.assertEqual((root / 'a.png').read_bytes(), original)

    def test_failed_write_restores_previously_changed_icons(self):
        from appCommon.IconColors import recolor_icons, _write_icon

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a.png', 'b.png'):
                write_icon(root / name, [(255, 255, 255, 255)])
            before = {p: p.read_bytes() for p in root.iterdir()}

            def fail_second(path, data):
                if path.name == 'b.png':
                    raise OSError('Test write failure')
                _write_icon(path, data)

            with patch('appCommon.IconColors._write_icon', side_effect=fail_second):
                with self.assertRaisesRegex(OSError, 'Test write failure'):
                    recolor_icons(root, '#66adc7')
            for path, original in before.items():
                self.assertEqual(path.read_bytes(), original)


# ======================================================================
# merged from tests/test_rename_completer.py
# ======================================================================
"""Rename keeps the shell auto-completer in sync, also when the old name was never registered."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore

from appCommon.RegisterFileKeywords import RegisterFK
from appObjects.AppObjectTemplate import FlatCAMObj


def make_reg(keywords):
    reg = RegisterFK.__new__(RegisterFK)
    QtCore.QObject.__init__(reg)
    reg.myKeywords = list(keywords)
    reg.object_names = []
    cmd_line = mock.MagicMock()
    reg.shell = mock.MagicMock()
    reg.shell.command_line.return_value = cmd_line
    return reg, cmd_line


class TestRenameCompleter(unittest.TestCase):
    def test_rename_with_unregistered_old_name(self):
        reg, cmd_line = make_reg(["other"])
        code_editor = mock.MagicMock()
        stub = SimpleNamespace(
            obj_options={"name": "old"},
            default_data={"name": "old"},
            ui=mock.MagicMock(),
            app=SimpleNamespace(
                regFK=reg,
                ui=SimpleNamespace(code_editor=code_editor),
                collection=mock.MagicMock(),
                inform=mock.MagicMock(),
                log=mock.MagicMock(),
            ),
        )
        FlatCAMObj.on_name_activate(stub, new_name="new")

        expected = ["new", "other"]
        self.assertEqual(reg.myKeywords, expected)
        cmd_line.set_model_data.assert_called_with(expected)
        code_editor.set_model_data.assert_called_once_with(expected)
        self.assertNotIn("old", reg.myKeywords)
        self.assertEqual(stub.obj_options["name"], "new")


def named(name):
    return SimpleNamespace(obj_options={"name": name})


class TestRenameKeepsNamesUnique(unittest.TestCase):
    def test_name_entry_and_gcode_export_number_the_name_of_another_object(self):
        reg, _ = make_reg(["top", "top_cnc"])
        stub = SimpleNamespace(
            obj_options={"name": "top_cnc"},
            default_data={"name": "top_cnc"},
            ui=mock.MagicMock(),
            app=SimpleNamespace(regFK=reg, ui=SimpleNamespace(code_editor=mock.MagicMock()),
                                collection=mock.MagicMock(), inform=mock.MagicMock(), log=mock.MagicMock()),
        )
        stub.app.collection.get_list.return_value = [named("top"), stub]
        # the G-code export renames the CNC job to the base name of the file (top.nc)
        FlatCAMObj.on_name_activate(stub, silent=True, new_name="top")
        self.assertEqual(stub.obj_options["name"], "top_1")
        self.assertEqual(reg.myKeywords, ["top_1", "top"])
        FlatCAMObj.on_name_activate(stub, new_name="")
        self.assertEqual(stub.obj_options["name"], "top_1")

    def test_project_tree_and_rename_object_number_the_name_of_another_object(self):
        from appObjects.ObjectCollection import ObjectCollection
        other, renamed = named("top"), named("top_cnc")
        collection = SimpleNamespace(app=mock.MagicMock(), get_list=lambda: [other, renamed],
                                     dataChanged=mock.MagicMock(), update_view=mock.MagicMock())
        index = mock.MagicMock()
        index.internalPointer.return_value.obj = renamed
        ObjectCollection.setData(collection, index, "top")
        self.assertEqual(renamed.obj_options["name"], "top_1")
        renamed.obj_options["name"] = "top_cnc"
        self.assertEqual(ObjectCollection.rename_object(collection, renamed, "top"), "top_1")
        self.assertEqual((other.obj_options["name"], renamed.obj_options["name"]), ("top", "top_1"))


# ======================================================================
# merged from tests/test_file_filters.py
# ======================================================================
#!/usr/bin/env python
"""
Tests for the open-file dialog filters. On Linux the Qt file dialog matches the filter patterns case-sensitive,
so each pattern must also be there in upper case (EasyEDA writes e.g. 'board.GTL', 'board.DRL').
Also the Gerber save dialog: each file type needs a '*.ext' pattern and 'All Files (*.*)' must be the last group.
And the name that the save dialog gives: the extension of the chosen filter is added only to a name that has none
of the extensions of that filter, in any case.

Run: python tests/test_file_filters.py
"""

import os
import sys
import unittest
from fnmatch import fnmatchcase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets


def patterns_of(ext_filter):
    """Return the patterns of each filter group, keyed by the group name."""
    groups = {}
    for group in ext_filter.split(';;'):
        name, _sep, patterns = group.rpartition('(')
        groups[name.strip()] = patterns.rstrip(')').split()
    return groups


def matches(ext_filter, filename):
    """True if a file type group, not the 'All Files (*.*)' group, shows the file."""
    return any(fnmatchcase(filename, p)
               for group in patterns_of(ext_filter).values() if '*.*' not in group
               for p in group)


class DialogFilterTests(unittest.TestCase):
    """Run the real dialog callbacks with a patched QFileDialog and check the filter they pass to Qt."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def captured_filter(self, call):
        stub = SimpleNamespace(log=MagicMock(), inform=MagicMock(), worker_task=MagicMock(),
                               app=MagicMock(), splash=MagicMock())
        stub.app.get_last_folder.return_value = ''
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames', return_value=([], '')) as dialog:
            call(stub)
        return dialog.call_args.kwargs['filter']

    def test_gerber_dialog_shows_upper_and_lower_case_files(self):
        from appHandlers.appIO import appIO
        ext_filter = self.captured_filter(lambda stub: appIO.on_file_open_gerber(stub))
        for filename in ('board.gtl', 'board.GTL', 'board.GBR', 'board.gme', 'board.GME', 'board.GKO'):
            self.assertTrue(matches(ext_filter, filename), filename)
        # every group gets the upper case, not only the first one
        self.assertIn('*.ART', patterns_of(ext_filter)['Allegro Files'])

    def test_excellon_gcode_and_hpgl2_dialogs_show_upper_case_files(self):
        from appHandlers.appIO import appIO
        cases = ((appIO.on_file_open_excellon, 'board.DRL'),
                 (appIO.on_file_open_gcode, 'job.NC'),
                 (appIO.on_file_open_hpgl2, 'plot.PLT'))
        for callback, filename in cases:
            with self.subTest(callback=callback.__name__):
                ext_filter = self.captured_filter(lambda stub: callback(stub))
                self.assertTrue(matches(ext_filter, filename))
                self.assertTrue(matches(ext_filter, filename.lower()))

class GerberSaveFilterTests(unittest.TestCase):
    """Run the real Gerber export callback with a patched save dialog and check the filter it passes to Qt."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def make_stub():
        from appObjects.GerberObject import GerberObject
        from defaults import AppDefaults
        stub = SimpleNamespace(log=MagicMock(), inform=MagicMock(), export_gerber=MagicMock(), app=MagicMock())
        stub.app.options = {}
        stub.app.defaults = dict(AppDefaults.factory_defaults)
        stub.app.get_last_save_folder.return_value = ''
        obj = GerberObject.__new__(GerberObject)
        obj.obj_options = {'name': 'board'}
        obj.app = stub.app
        stub.app.collection.get_active.return_value = obj
        return stub

    @staticmethod
    def export(stub, file_type):
        """The user types 'board' and picks 'file_type'. Return the filter shown and the file name exported."""
        from appHandlers.appIO import appIO

        def save_dialog(**kwargs):
            return 'board', next(g for g in kwargs['filter'].split(';;') if g.startswith(file_type))

        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', side_effect=save_dialog) as dialog:
            appIO.on_file_export_gerber(stub)
        return dialog.call_args.kwargs['filter'], stub.export_gerber.call_args.args[1]

    def test_each_file_type_shows_its_files_and_all_files_is_last(self):
        ext_filter, _filename = self.export(self.make_stub(), 'Gerber File .gbr ')
        groups = ext_filter.split(';;')
        self.assertEqual('All Files (*.*)', groups[-1])
        for group in groups[:-1]:
            # 'Gerber File .gtl (*.gtl)' must show 'board.gtl'
            filename = 'board' + group.split()[2]
            self.assertTrue(matches(group, filename), group)

    def test_the_chosen_file_type_adds_its_extension(self):
        # .art is the last file type; it was joined to 'All Files' and saved 'board' without an extension
        for file_type, filename in (('Gerber File .gtl ', 'board.gtl'), ('Gerber File .art ', 'board.art')):
            self.assertEqual(filename, self.export(self.make_stub(), file_type)[1])


class SaveDialogExtensionTests(unittest.TestCase):
    """FCFileSaveDialog adds the extension of the chosen filter only to a name that has none of its extensions."""

    @staticmethod
    def saved_name(picked, chosen_filter):
        from appGUI.GUIElements import FCFileSaveDialog
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(picked, chosen_filter)):
            return FCFileSaveDialog.get_saved_filename(caption='Export Code ...', ext_filter=chosen_filter)[0]

    def test_a_name_with_an_extension_of_the_filter_in_any_case_is_kept(self):
        # View Source of a CNC job saves with "(*.NC)": over 'board_cnc.nc' it wrote 'board_cnc.nc.NC'
        for picked, chosen_filter in (('C:/w/board_cnc.nc', 'GCode Files .nc (*.NC)'),
                                      ('C:/w/TOP.NC', 'G-Code Files .nc (*.nc)'),
                                      ('C:/w/board.gbr', 'Gerber Files (*.gbr *.GBR)')):
            self.assertEqual(picked, self.saved_name(picked, chosen_filter))
        for picked, chosen_filter, expected in (('C:/w/board', 'GCode Files .nc (*.NC)', 'C:/w/board.NC'),
                                                ('C:/w/top.gcode', 'G-Code Files .nc (*.nc)', 'C:/w/top.gcode.nc'),
                                                ('C:/w/board.txt', 'All Files (*.*)', 'C:/w/board.txt'),
                                                ('', 'G-Code Files .nc (*.nc)', '')):
            self.assertEqual(expected, self.saved_name(picked, chosen_filter))


# ======================================================================
# merged from tests/test_qt_command_connections.py
# ======================================================================
"""Guard bool-emitting Qt signals from silently changing command options."""

from unittest.mock import Mock

import pytest
from PyQt6 import QtGui, QtWidgets

from appHandlers.appSignalConnector import AppSignalConnector


@pytest.fixture(scope='module')
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize('registration, action_name', [
    ('connect_editmenu_signals', 'menuedit_center_in_origin'),
    ('connect_toolbar_signals', 'replot_btn'),
])
def test_real_registered_actions_keep_command_defaults(qt_app, registration, action_name):
    app = Mock()
    action = QtGui.QAction('Command')
    setattr(app.ui, action_name, action)
    app.ui.pop_menucolor.actions.return_value = []
    calls = []

    def plot_all(fit_view=True, muted=False, use_thread=True):
        calls.append((fit_view, use_thread))

    def on_custom_origin(use_thread=True):
        calls.append((True, use_thread))

    app.plot_all = plot_all
    app.edit_class.on_custom_origin = on_custom_origin
    connector = AppSignalConnector(app)
    getattr(connector, registration)()
    action.trigger()
    assert calls == [(True, True)]


# ======================================================================
# merged from tests/test_jump_to.py
# ======================================================================
"""Run: .venv/Scripts/python.exe tests/test_jump_to.py"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if sys.platform != 'win32':
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets
from vispy import app
from vispy.geometry import Rect

app.use_app('pyqt6')

from appGUI.VisPyCanvas import VisPyCanvas
from appGUI.PlotCanvas import PlotCanvas
from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appCanvasEvents import AppCanvasEvents


class TestJumpTo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_handler(self):
        canvas = VisPyCanvas()
        self.addCleanup(canvas.close)
        canvas.size = (800, 600)
        canvas.unfreeze()
        canvas.shape_collection = Mock()
        canvas.fit_center = PlotCanvas.fit_center.__get__(canvas)
        canvas.on_update_text_hud = Mock()
        ui = QtWidgets.QWidget()
        self.addCleanup(ui.deleteLater)
        setattr(ui, 'update_location_labels', Mock())
        fcapp = SimpleNamespace(
            log=Mock(), inform=Mock(), defaults=Mock(), options={}, ui=ui,
            plotcanvas=canvas, clipboard=SimpleNamespace(text=lambda: ''), resource_location='',
            mouse_pos=[12.3456789, -8.7654321], rel_point1=(1, 2),
            jump_signal=Mock(), locate_signal=Mock(), grid_status=lambda: False,
        )
        return fcapp, AppObjectOps(fcapp)

    def test_relative_reference_is_captured_before_dialog(self):
        fcapp, handler = self.make_handler()
        start = tuple(fcapp.mouse_pos)

        def dialog(**kwargs):
            # Moving to the dialog must not move the relative reference.
            fcapp.mouse_pos[:] = [90, 80]
            return SimpleNamespace(ok=True, location='0.125, -0.25', reference='rel')

        with patch('appGUI.GUIElements.DialogBoxRadio', side_effect=dialog), \
                patch.object(QtGui.QCursor, 'setPos'):
            target = handler.on_jump_to()
        self.assertEqual(target, (start[0] + 0.125, start[1] - 0.25))

    def test_jump_click_matches_target_and_canvas(self):
        fcapp, handler = self.make_handler()
        canvas = fcapp.plotcanvas
        events = AppCanvasEvents(fcapp)
        events.on_plugin_mouse_move = Mock()
        fcapp.options['global_selection_shape'] = False
        fcapp.ui.shell_dock = Mock()
        fcapp.ui.popMenu = SimpleNamespace(mouse_is_panning=False)
        for target in ((3.4, 4.5), (15.2, 13.4)):
            with self.subTest(target=target), patch.object(QtGui.QCursor, 'setPos'):
                handler.on_jump_to(custom_location=target)
                pixel = np.round(canvas.translate_coords_2(target)[:2])
                canvas.events.mouse_move(pos=pixel)
                event = SimpleNamespace(pos=pixel, button=1)
                events.on_mouse_click_over_plot(event)
                # 0.00001 mm is 100 times finer than the requested one-micron precision.
                np.testing.assert_allclose(fcapp.mouse_click_pos, target, rtol=0, atol=1e-5)
                np.testing.assert_allclose(fcapp.mouse_pos, target, rtol=0, atol=1e-5)
                np.testing.assert_allclose(fcapp.ui.update_location_labels.call_args.args,
                                           (0.0, 0.0, *target), rtol=0, atol=1e-5)
                np.testing.assert_allclose(canvas.on_update_text_hud.call_args.args,
                                           (0.0, 0.0, *target), rtol=0, atol=1e-5)
                actual = canvas.grid.get_transform('canvas', 'visual').map(pixel)
                np.testing.assert_array_equal(canvas.translate_coords(pixel), actual)

    def test_jump_centers_target_without_changing_zoom(self):
        fcapp, handler = self.make_handler()
        canvas = fcapp.plotcanvas
        canvas.view.camera.rect = Rect(100, -80, 0.04, 0.03)
        before = canvas.view.camera.rect.size
        target = (3.2, 4.3)
        with patch.object(QtGui.QCursor, 'setPos'):
            handler.on_jump_to(custom_location=target)
        np.testing.assert_allclose(canvas.view.camera.rect.size, before, rtol=0, atol=1e-12)
        # Centering may shift less than a pixel to align the integer OS cursor.
        np.testing.assert_allclose(canvas.view.camera.rect.center, target, rtol=0,
                                   atol=canvas.pixel_size())

    def test_canvas_pixel_roundtrip_after_pan_zoom_and_resize(self):
        fcapp, _ = self.make_handler()
        canvas = fcapp.plotcanvas
        for size, rect, target in (
                ((800, 600), (-20, -20, 40, 40), (3.123456, -8.765432)),
                ((1200, 400), (120, -80, 0.4, 0.4), (120.123456, -79.765432)),
                ((400, 900), (-900, 200, 600, 600), (-743.23456, 359.12345))):
            with self.subTest(size=size, rect=rect):
                canvas.size = size
                canvas.view.camera.rect = Rect(*rect)
                screen = canvas.translate_coords_2(target)
                np.testing.assert_allclose(canvas.translate_coords(screen[:2])[:2], target, atol=1e-8)
                # The OS cursor has integer logical pixels: half a pixel is the unavoidable limit.
                pixel = np.round(screen[:2])
                np.testing.assert_array_less(np.abs(screen[:2] - pixel), 0.500001)
                np.testing.assert_allclose(canvas.translate_coords_2(canvas.translate_coords(pixel)[:2])[:2],
                                           pixel, atol=1e-5)
