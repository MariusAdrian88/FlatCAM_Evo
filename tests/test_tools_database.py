"""Focused Tools Database regression checks using temporary databases only."""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6 import QtCore, QtWidgets
from appDatabase import ToolsDB2
from appGUI.GUIElements import FCComboBox2, FCDoubleSpinner, FCDetachableTab2
from defaults import AppDefaults


def record(target=0, diameter=2.4):
    options = AppDefaults().defaults
    data = {key: deepcopy(value) for key, value in options.items()
            if key.startswith('tools_')}
    data.update(tool_target=target, tol_min=0.0, tol_max=0.0)
    return {'name': 'fixture', 'tooldia': diameter, 'data': data}


class Tabs(QtWidgets.QTabWidget):
    def __init__(self):
        super().__init__()
        self.tabBar = super().tabBar()


class DecisionMessageBox:
    response = 'yes'

    def __init__(self, parent=None):
        self.buttons = {}

    def addButton(self, label, role):
        self.buttons[str(label)] = object()
        return self.buttons[str(label)]

    def clickedButton(self):
        return self.buttons[{'yes': 'Yes', 'no': 'No', 'cancel': 'Cancel'}[self.response]]

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class DatabaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tools.FlatDB'
        self.path.write_text('{}', encoding='utf-8')
        self.tabs = Tabs()
        self.addCleanup(self.tabs.deleteLater)
        self.app = SimpleNamespace(
            decimals=4, app_units='MM', resource_location=str(ROOT / 'assets/resources'),
            options=AppDefaults().defaults, defaults=MagicMock(), inform=MagicMock(),
            log=MagicMock(), tools_db_changed_flag=False,
            tools_database_path=lambda: str(self.path),
            dec_format=lambda value, decimals: round(value, decimals),
            ui=SimpleNamespace(plot_tab_area=self.tabs))
        self.slot_errors = []
        hook = patch.object(sys, 'excepthook', lambda *error: self.slot_errors.append(error))
        hook.start()
        self.addCleanup(hook.stop)

    def database(self, contents=None):
        if contents is not None:
            self.path.write_text(json.dumps(contents), encoding='utf-8')
        db = ToolsDB2(self.app, callback_on_tool_request=MagicMock())
        self.addCleanup(db.deleteLater)
        self.tabs.addTab(db, 'Tools Database')
        self.app.tools_db_tab = db
        return db

    def detached_database(self, contents=None):
        tabs = FCDetachableTab2()
        self.tabs = tabs
        self.app.ui.plot_tab_area = tabs
        db = self.database(contents)
        tabs.detachTab(0, QtCore.QPoint(1, 1))
        self.addCleanup(tabs.deleteLater)
        self.addCleanup(tabs.closeDetachedTabs)
        return db, tabs, next(iter(tabs.detachedTabs.values()))

    def actions_handler(self, tabs=None):
        tabs = tabs or self.tabs
        tabs.plot_tab_area = tabs
        tabs.coords_toolbar = MagicMock()
        tabs.delta_coords_toolbar = MagicMock()
        return SimpleNamespace(app=self.app, ui=tabs, log=self.app.log,
                               inform=self.app.inform,
                               on_geometry_tool_add_from_db_executed=MagicMock())


class LifecycleChecks(DatabaseCase):
    def shutdown_lifecycle(self, database):
        from appHandlers.appLifecycle import AppLifecycle

        self.app.tools_db_tab = database
        self.app.preferencesUiManager = MagicMock()
        self.app.save_in_progress = False
        self.app.should_we_save = False
        self.app.defaults = MagicMock()
        self.app.ui.hide = MagicMock()
        # quit_application saves the GUI state; these stand-ins keep it away from the real QSettings
        self.app.ui.saveState = MagicMock(return_value=b'')
        self.app.ui.isMaximized = MagicMock(return_value=False)
        self.app.ui.geometry = MagicMock(return_value=QtCore.QRect(10, 20, 800, 600))
        self.app.ui.general_pref_form = MagicMock()
        self.app.new_launch = None
        self.app.geo_editor = None
        self.app.exc_editor = None
        self.app.grb_editor = None
        self.app.gcode_editor = None
        # quit_application looks the editors up through App._editor_for (Stage 5); no editor exists here
        self.app._editor_for = lambda kind: None
        self.app.plotcanvas = MagicMock()
        self.app.on_mouse_move_over_plot = MagicMock(name='on_mouse_move_over_plot')
        self.app.on_mouse_click_over_plot = MagicMock(name='on_mouse_click_over_plot')
        self.app.on_mouse_click_release_over_plot = MagicMock(name='on_mouse_click_release_over_plot')
        self.app.on_mouse_double_click_over_plot = MagicMock(name='on_mouse_double_click_over_plot')
        self.app.ui.keyPressEvent = MagicMock(name='keyPressEvent')
        self.app.pool = MagicMock()
        self.app.workers = MagicMock()
        return AppLifecycle(self.app)

    def test_shutdown_honors_database_cancel_save_failure_and_success(self):
        from PyQt6 import QtWidgets

        for outcome, decision, expected in (
                ('cancel', False, False), ('save failure', False, False),
                ('save success', True, True), ('discard', True, True)):
            with self.subTest(outcome=outcome):
                database = MagicMock()
                database.confirm_close.return_value = decision
                lifecycle = self.shutdown_lifecycle(database)
                with patch('appHandlers.appLifecycle.sys.platform', 'linux'), \
                        patch('PyQt6.QtCore.QSettings'), \
                        patch.object(QtWidgets.QApplication, 'quit') as quit_app:
                    result = lifecycle.quit_application(silent=True)
                self.assertEqual(expected, result)
                database.confirm_close.assert_called_once_with()
                if expected:
                    self.app.ui.hide.assert_called_once_with()
                    self.assertEqual(self.app.plotcanvas.graph_event_disconnect.call_args_list, [
                        call('mouse_move', self.app.on_mouse_move_over_plot),
                        call('mouse_press', self.app.on_mouse_click_over_plot),
                        call('mouse_release', self.app.on_mouse_click_release_over_plot),
                        call('mouse_double_click', self.app.on_mouse_double_click_over_plot),
                        call('key_press', self.app.ui.keyPressEvent)])
                    self.app.plotcanvas.close.assert_called_once_with()
                    self.app.pool.terminate.assert_called_once_with()
                    self.app.pool.join.assert_called_once_with()
                    self.app.workers.quit.assert_called_once_with()
                    quit_app.assert_called_once_with()
                else:
                    self.app.ui.hide.assert_not_called()
                    self.app.preferencesUiManager.save_defaults.assert_not_called()
                    self.app.plotcanvas.graph_event_disconnect.assert_not_called()
                    self.app.plotcanvas.close.assert_not_called()
                    self.app.pool.terminate.assert_not_called()
                    self.app.pool.join.assert_not_called()
                    self.app.workers.quit.assert_not_called()
                    quit_app.assert_not_called()


class EditorChecks(DatabaseCase):
    def test_typed_cutting_parameters_with_units_are_saved_without_changing_values(self):
        import appDatabase
        from PyQt6.QtTest import QTest
        db = self.database({'7': record()})
        db.resize(1100, 800)
        db.show()
        cases = (
            (1, 'tools_mill_cutz', '-0.6', -0.6),
            (1, 'tools_mill_travelz', '3.2', 3.2),
            (1, 'tools_mill_feedrate', '150', 150.0),
            (1, 'tools_mill_spindlespeed', '12000', 12000.0),
            (2, 'tools_drill_spindlespeed', '14000', 14000),
            (4, 'tools_paint_overlap', '32.5', 32.5))
        for target, key, text, expected in cases:
            with self.subTest(parameter=key):
                db.ui.properties_combo.setCurrentIndex(db.ui.properties_combo.findData(target))
                self.qapp.processEvents()
                widget = db.form_fields[key]
                widget.setFocus()
                widget.selectAll()
                QTest.keyClicks(widget, text)
                # the spinners commit the typed text on Enter or focus out; the test window never gets the focus
                QTest.keyClick(widget, QtCore.Qt.Key.Key_Return)
                self.assertEqual(expected, db.db_tool_dict['7']['data'][key])
        for target, key, expected in ((1, 'tools_mill_feedrate', 150.0),
                                      (2, 'tools_drill_spindlespeed', 14000)):
            with self.subTest(invalid_parameter=key):
                db.ui.properties_combo.setCurrentIndex(db.ui.properties_combo.findData(target))
                widget = db.form_fields[key]
                widget.setFocus()
                widget.selectAll()
                QTest.keyClicks(widget, 'invalid')
                QTest.keyClick(widget, QtCore.Qt.Key.Key_Tab)
                self.assertEqual(expected, db.db_tool_dict['7']['data'][key])
        self.assertTrue(db.on_save_tools_db())
        saved = appDatabase.load_tools_database(self.path, self.app.options)
        for target, key, text, expected in cases:
            self.assertEqual(expected, saved['7']['data'][key])
        self.assertEqual([], self.slot_errors)

    def test_library_search_operation_filter_and_numeric_sort(self):
        tools = {'7': record(1, 10.0), '8': record(4, 2.0), '9': record(0, 0.8)}
        tools['8']['name'] = 'Paint cutter'
        db = self.database(tools)
        db.ui.search_entry.setText('paint')
        db.apply_filters()
        self.assertEqual(1, db.ui.tree_widget.model().rowCount())
        self.assertEqual('Paint cutter', db.ui.tree_widget.model().index(0, 1).data())
        db.ui.search_entry.clear()
        db.ui.filter_combo.setCurrentIndex(db.ui.filter_combo.findData(1))
        db.apply_filters()
        self.assertEqual(2, db.ui.tree_widget.model().rowCount())
        db.ui.filter_combo.setCurrentIndex(0)
        db.apply_filters()
        db.ui.tree_widget.sortByColumn(3, QtCore.Qt.SortOrder.AscendingOrder)
        self.assertEqual([0.8, 2.0, 10.0], [db.ui.tree_widget.model().index(row, 3).data(
            QtCore.Qt.ItemDataRole.UserRole) for row in range(3)])
        self.assertFalse(self.app.tools_db_changed_flag)

    def test_picker_shows_only_compatible_tools_and_operation_properties(self):
        db = self.database({'7': record(4), '8': record(2), '9': record(0)})
        db.set_context('paint')
        self.assertEqual(2, db.ui.tree_widget.model().rowCount())
        db.select_tool('9')
        self.assertFalse(db.ui.paint_box.isHidden())
        self.assertTrue(db.ui.drill_box.isHidden())
        self.assertTrue(db.ui.iso_box.isHidden())
        db.on_tool_request = MagicMock(return_value=True)
        db._close_database_container = MagicMock(return_value=True)
        self.assertTrue(db.on_tool_requested_from_app())
        imported = db.on_tool_request.call_args.kwargs['tool']
        self.assertIn('tools_paint_overlap', imported['data'])
        self.assertNotIn('tools_drill_cutz', imported['data'])
        self.assertEqual(0, db.db_tool_dict['9']['data']['tool_target'])
        db.set_context('app')
        self.assertEqual(3, db.ui.tree_widget.model().rowCount())

    def test_add_uses_current_operation_and_invalid_edit_preserves_saved_library(self):
        db = self.database()
        db.ui.filter_combo.setCurrentIndex(db.ui.filter_combo.findData(2))
        db.on_tool_add()
        self.assertEqual(2, db.db_tool_dict['1']['data']['tool_target'])
        db.ui.name_entry.set_value('New drill')
        db.ui.dia_entry.set_value(0.8)
        self.assertTrue(db.on_save_tools_db())
        before = self.path.read_bytes()
        db.ui.tol_min_entry.set_value(1.0)
        db.ui.tol_max_entry.set_value(0.5)
        self.assertFalse(db.on_save_tools_db())
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertTrue(db.ui.status_label.text())

    def test_sqlite_migration_preserves_legacy_and_unknown_fields(self):
        import appDatabase
        source = {'7': record(4), '42': record(2)}
        source['7']['vendor_note'] = {'keep': True}
        self.path.write_text(json.dumps(source), encoding='utf-8')
        original = self.path.read_bytes()
        destination = self.path.with_suffix('.sqlite3')
        appDatabase.initialize_tools_database(destination, self.path, self.app.options)
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(b'SQLite format 3\x00', destination.read_bytes()[:16])
        loaded = appDatabase.load_tools_database(destination, self.app.options, units='MM')
        self.assertEqual(source, loaded)
        # Reopening must never re-import the legacy file over subsequent edits.
        loaded['7']['name'] = 'saved'
        appDatabase.write_tools_database(destination, loaded, units='MM')
        appDatabase.initialize_tools_database(destination, self.path, self.app.options)
        self.assertEqual('saved', appDatabase.load_tools_database(destination, self.app.options)['7']['name'])
        self.path = destination
        db = self.database()
        db.select_tool('7')
        db.ui.name_entry.set_value('edited in library')
        self.assertTrue(db.on_save_tools_db())
        saved = appDatabase.load_tools_database(destination, self.app.options)
        self.assertEqual(source['42'], saved['42'])
        self.assertEqual('edited in library', saved['7']['name'])

    def test_sqlite_rolls_back_failed_save_and_rejects_stale_editor(self):
        import sqlite3
        import appDatabase
        destination = self.path.with_suffix('.sqlite3')
        source = {'7': record(4)}
        appDatabase.write_tools_database(destination, source, units='MM')
        with closing(sqlite3.connect(destination)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON tools "
                               "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        changed = deepcopy(source)
        changed['7']['name'] = 'changed'
        with self.assertRaises(OSError):
            appDatabase.write_tools_database(destination, changed, units='MM')
        self.assertEqual(source, appDatabase.load_tools_database(destination, self.app.options))
        with closing(sqlite3.connect(destination)) as connection, connection:
            connection.execute('DROP TRIGGER reject_update')
        appDatabase.write_tools_database(destination, changed, units='MM', expected_revision=1)
        with self.assertRaises(ValueError):
            appDatabase.write_tools_database(destination, source, units='MM', expected_revision=1)
        with self.assertRaises(ValueError):
            appDatabase.load_tools_database(destination, self.app.options, units='IN')
        self.assertEqual('changed', appDatabase.load_tools_database(destination, self.app.options)['7']['name'])

        self.path = destination
        db = self.database()
        changed['7']['name'] = 'external edit'
        appDatabase.write_tools_database(destination, changed, units='MM')
        db.ui.name_entry.set_value('local edit')
        self.assertFalse(db.on_save_tools_db())
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual('external edit', appDatabase.load_tools_database(destination, self.app.options)['7']['name'])

    def test_failed_first_write_leaves_no_file_and_migration_retries(self):
        import appDatabase
        from appCommon.ToolDatabase import write_database
        destination = self.path.with_suffix('.sqlite3')
        with self.assertRaises(OSError):
            write_database(destination, {'7': {'name': 'x', 'tooldia': -1.0, 'data': {'tool_target': 1}}},
                           units='MM')
        self.assertFalse(destination.exists())
        # An empty file from an older failed start must not block the migration.
        destination.write_bytes(b'')
        self.path.write_text(json.dumps({'7': record(4)}), encoding='utf-8')
        appDatabase.initialize_tools_database(destination, self.path, self.app.options)
        self.assertEqual(['7'], list(appDatabase.load_tools_database(destination, self.app.options)))

    def test_migration_skips_bad_records_and_uses_newest_legacy_library(self):
        import appDatabase
        bad = record(4)
        bad['name'] = ''
        current = self.path.parent / 'tools_db_1.1.FlatDB'
        current.write_text('{}', encoding='utf-8')
        self.path.write_text(json.dumps({'7': record(4), '8': bad}), encoding='utf-8')
        destination = self.path.with_suffix('.sqlite3')
        appDatabase.initialize_tools_database(destination, [current, self.path], self.app.options)
        self.assertEqual(['7'], list(appDatabase.load_tools_database(destination, self.app.options)))

    def test_editor_skips_bad_record_and_saves_the_rest(self):
        import sqlite3
        import appDatabase
        destination = self.path.with_suffix('.sqlite3')
        appDatabase.write_tools_database(destination, {'7': record(4), '8': record(2)}, units='MM')
        bad = record(2)
        bad['tooldia'] = 'wide'
        with closing(sqlite3.connect(destination)) as connection, connection:
            connection.execute('UPDATE tools SET record=? WHERE id=8', (json.dumps(bad),))
        self.path = destination
        db = self.database()
        db.select_tool('8')
        self.assertTrue(db.ui.status_label.text())
        self.assertFalse(db.ui.param_area.isEnabled())
        self.assertTrue(db.ui.save_db_btn.isEnabled())
        db.select_tool('7')
        db.ui.name_entry.set_value('kept edit')
        self.assertTrue(db.on_save_tools_db())
        self.assertEqual('kept edit', appDatabase.load_tools_database(destination, self.app.options)['7']['name'])
        with closing(sqlite3.connect(destination)) as connection:
            stored = connection.execute('SELECT record FROM tools WHERE id=8').fetchone()[0]
        self.assertEqual(bad, json.loads(stored))

    def test_lookup_exact_precedes_tolerance_and_supports_general_tools(self):
        import appDatabase
        source = {'7': record(4), '8': record(4, 2.5), '9': record(2)}
        source['8']['data'].update(tol_min=2.0, tol_max=3.0)
        self.path.write_text(json.dumps(source), encoding='utf-8')
        for path in (self.path, self.path.with_suffix('.sqlite3')):
            with self.subTest(path=path.suffix):
                if path.suffix == '.sqlite3':
                    appDatabase.write_tools_database(path, source, units='MM')
                found = appDatabase.load_tools_database(path, self.app.options, target=4, diameter=2.4)
                self.assertEqual(['7'], list(found))
                found = appDatabase.load_tools_database(path, self.app.options, target=4, diameter=2.6)
                self.assertEqual(['8'], list(found))
        source = {'7': record(0), '8': record(2)}
        self.path.write_text(json.dumps(source), encoding='utf-8')
        self.assertEqual(['7'], list(appDatabase.load_tools_database(
            self.path, self.app.options, target=4, diameter=2.4)))

    def test_normalization_preserves_input_and_repairs_aliases(self):
        import appDatabase
        normalize = getattr(appDatabase, 'normalize_tools_database', None)
        self.assertTrue(callable(normalize))
        if not callable(normalize):
            return

        source = {'7': record(4)}
        source['7']['data']['tool_target'] = 'Paint'
        source['7']['data']['job'] = 'Finishing'
        source['7']['tools_mill_offset_value'] = 0.75
        source['7']['data']['tools_cutout_gap_type'] = 'mb'
        source['7']['vendor_note'] = {'keep': True}
        before = deepcopy(source)

        result = normalize(source, self.app.options)

        self.assertEqual(before, source)
        self.assertEqual(4, result['7']['data']['tool_target'])
        self.assertEqual(1, result['7']['data']['tools_mill_job_type'])
        self.assertEqual(0.75, result['7']['data']['tools_mill_offset_value'])
        self.assertEqual(2, result['7']['data']['tools_cutout_gap_type'])
        self.assertEqual({'keep': True}, result['7']['vendor_note'])

    def test_normalization_rejects_invalid_records(self):
        import appDatabase
        normalize = getattr(appDatabase, 'normalize_tools_database', None)
        self.assertTrue(callable(normalize))
        if not callable(normalize):
            return

        with self.assertRaises(ValueError):
            normalize([], self.app.options)

        invalid = {'01': record()}
        with self.assertRaises(ValueError):
            normalize(invalid, self.app.options)

        invalid = {'7': record()}
        invalid['7']['data']['tool_target'] = 'Unknown target'
        before = deepcopy(invalid)
        with self.assertRaises(ValueError):
            normalize(invalid, self.app.options)
        self.assertEqual(before, invalid)

        for field, value in (('name', ''), ('tooldia', 0), ('tooldia', float('nan'))):
            with self.subTest(field=field, value=value):
                invalid = {'7': record()}
                invalid['7'][field] = value
                with self.assertRaises(ValueError):
                    normalize(invalid, self.app.options)

    def preferences_after_apply(self):
        # an Apply evaluates these fields: '0.1' -> 0.1, '0.0, 0.0' -> (0.0, 0.0), 'no' -> 0, None -> (10.0, 10.0)
        applied = dict(self.app.options)
        applied.update(tools_iso_tooldia=0.1, tools_mill_toolchangexy=(0.0, 0.0), tools_drill_toolchangexy=(0.0, 0.0),
                       tools_drill_tool_order=0, tools_mill_endxy=(10.0, 10.0), tools_mill_startz=5.0)
        return applied

    def test_records_load_after_a_preferences_apply_changes_the_option_types(self):
        import appDatabase
        applied = self.preferences_after_apply()
        records = {'1': record(1), '2': record(2), '3': record(3)}  # saved before the Apply
        for tool_id in ('4', '5', '6'):  # saved after the Apply (the tuples are lists in the file)
            records[tool_id] = record(int(tool_id) - 3)
            records[tool_id]['data'].update((key, applied[key]) for key in applied
                                            if applied[key] != self.app.options[key])
        records['7'] = record(2)
        records['7']['data']['tools_drill_tool_order'] = 'sideways'
        destination = self.path.with_suffix('.sqlite3')
        appDatabase.write_tools_database(destination, records, units='MM')

        for options in (applied, self.app.options):
            with self.subTest(applied=options is applied):
                skipped = {}
                loaded = appDatabase.load_tools_database(destination, options, skipped=skipped)
                self.assertEqual(['1', '2', '3', '4', '5', '6'], sorted(loaded))
                self.assertEqual(['7'], list(skipped))
                self.assertEqual('0.1', loaded['3']['data']['tools_iso_tooldia'])
                self.assertEqual([10.0, 10.0], loaded['6']['data']['tools_mill_endxy'])

    def test_records_with_wrong_option_types_are_still_skipped(self):
        import appDatabase
        wrong = {'7': ('tools_mill_feedrate', '120'), '8': ('tools_mill_toolchangexy', [float('nan'), 0.0]),
                 '9': ('tools_mill_endxy', {'x': 1.0}), '10': ('tools_mill_multidepth', 1),
                 '11': ('tools_mill_startz', True)}
        records = {}
        for tool_id, (key, value) in wrong.items():
            records[tool_id] = record(1)
            records[tool_id]['data'][key] = value

        for options in (self.preferences_after_apply(), self.app.options):
            skipped = {}
            self.assertEqual({}, appDatabase.normalize_tools_database(records, options, skipped=skipped))
            self.assertEqual(sorted(wrong), sorted(skipped))

    def test_load_accepts_utf8_bom_and_rejects_import_transactionally(self):
        import appDatabase

        valid = {'7': record(4)}
        self.path.write_bytes(b'\xef\xbb\xbf' + json.dumps(valid).encode('utf-8'))
        loaded = appDatabase.load_tools_database(self.path, self.app.options)
        self.assertEqual(4, loaded['7']['data']['tool_target'])

        db = self.database({'1': record()})
        before_state = deepcopy(db.db_tool_dict)
        before_bytes = self.path.read_bytes()
        self.app.tools_db_changed_flag = True
        invalid_path = self.path.parent / 'invalid.FlatDB'
        invalid_path.write_text('[]', encoding='utf-8')
        with patch('appDatabase.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=(str(invalid_path), '')):
            self.assertFalse(db.on_import_tools_db_file())
        self.assertEqual(before_state, db.db_tool_dict)
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(before_bytes, self.path.read_bytes())

        incoming = record(2)
        incoming['name'] = 'Imported drill'
        bad = record(2)
        bad['tooldia'] = 0
        invalid_path.write_text(json.dumps({'1': incoming, '5': bad}), encoding='utf-8')
        for _attempt in range(2):
            # A second import of the same file adds no duplicates.
            with patch('appDatabase.QtWidgets.QFileDialog.getOpenFileName',
                       return_value=(str(invalid_path), '')):
                self.assertTrue(db.on_import_tools_db_file())
            self.assertEqual({'1', '2'}, set(db.db_tool_dict))
        self.assertEqual('Imported drill', db.db_tool_dict['2']['name'])
        self.assertEqual(before_bytes, self.path.read_bytes())
        before_state = deepcopy(db.db_tool_dict)
        with patch('appDatabase.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=(str(invalid_path), '')), patch.object(db, 'confirm_close', return_value=False):
            self.assertFalse(db.on_import_tools_db_file(replace=True))
        self.assertEqual(before_state, db.db_tool_dict)

    def test_cutout_gap_type_roundtrip(self):
        db = self.database()
        db.ui.cutout_gaptype_radio.blockSignals(True)
        for value in (0, 1, 2):
            with self.subTest(value=value):
                db.ui.cutout_gaptype_radio.set_value(value)
                self.assertEqual(value, db.ui.cutout_gaptype_radio.get_value())

    def test_v_tool_diameter_calculation_uses_numeric_shape_id(self):
        db = self.database()
        db.ui.mill_shape_combo.set_value(5)
        db.ui.mill_vdia_entry.set_value(0.2)
        db.ui.mill_vangle_entry.set_value(60)
        for cut_z in (-0.1, 0.1):
            with self.subTest(cut_z=cut_z):
                db.ui.mill_cutz_entry.set_value(cut_z)
                db.on_calculate_tooldia()
                self.assertAlmostEqual(0.3155, db.ui.dia_entry.get_value(), places=4)

        db.ui.mill_shape_combo.set_value(0)
        diameter = db.ui.dia_entry.get_value()
        db.ui.mill_cutz_entry.set_value(-4.0)
        db.on_calculate_tooldia()
        self.assertEqual(diameter, db.ui.dia_entry.get_value())

    def test_job_and_offset_write_canonical_keys(self):
        db = self.database()
        for name, value, key, widget in (
                ('gdb_job', 2, 'tools_mill_job_type', FCComboBox2()),
                ('gdb_custom_offset', 0.75, 'tools_mill_offset_value', FCDoubleSpinner())):
            with self.subTest(name=name):
                if isinstance(widget, FCComboBox2):
                    widget.addItems(['a', 'b', 'c'])
                widget.setObjectName(name)
                widget.set_value(value)
                target = record()
                item = MagicMock()
                item.data.return_value = '1'
                stub = SimpleNamespace(
                    current_toolid=1, sender=lambda: widget, app=self.app,
                    db_tool_dict={'1': target}, name2option=db.name2option,
                    selected_tool_ids=lambda: ['1'], model=MagicMock(),
                    on_tools_db_edited=MagicMock())
                ToolsDB2.update_storage(stub)
                self.assertEqual(value, target['data'][key])
                self.assertNotIn('job', target['data'])
                self.assertNotIn('tools_mill_offset_value', target)

    def test_sparse_ids_survive_copy_sort_delete_and_request(self):
        db = self.database({'7': record(), '42': record(3, 0.8)})
        db.select_tool('7')
        db.on_tool_copy()
        self.assertEqual({'7', '42', '43'}, set(db.db_tool_dict))

        db.on_sort_dia()
        db.on_sort_target()
        self.assertEqual({'7', '42', '43'}, set(db.db_tool_dict))

        db.select_tool('42')
        db.on_tool_delete()
        self.assertEqual({'7', '43'}, set(db.db_tool_dict))

        db.select_tool('43')
        db.confirm_close = MagicMock(return_value=True)
        db.on_tool_requested_from_app()
        self.assertEqual('fixture', db.on_tool_request.call_args.kwargs['tool']['name'])

    def test_empty_database_can_add_delete_last_and_add_again(self):
        db = self.database()
        db.on_tool_add()
        self.assertEqual({'1'}, set(db.db_tool_dict))
        db.select_tool('1')
        db.on_tool_delete()
        self.assertEqual({}, db.db_tool_dict)
        db.on_tool_add()
        self.assertEqual({'1'}, set(db.db_tool_dict))

        db.db_tool_dict['1']['name'] = 'new_tool_custom'
        db.on_tool_add()
        self.assertEqual({'1', '2'}, set(db.db_tool_dict))

    def test_database_request_delivers_an_independent_copy(self):
        db = self.database({'1': record()})
        db.select_tool('1')
        original = deepcopy(db.db_tool_dict['1'])

        def mutate(tool):
            tool['data']['tools_mill_feedrate'] = 321.0

        db.on_tool_request = mutate
        db.on_tool_requested_from_app()
        self.assertEqual(original, db.db_tool_dict['1'])

    def test_failed_save_keeps_dirty_flag_and_bytes(self):
        db = self.database({'1': record()})
        before = self.path.read_bytes()
        db.db_tool_dict['1']['name'] = 'edited'
        db.on_tools_db_edited(silent=True)
        with patch('appDatabase.write_tools_database',
                   side_effect=PermissionError('injected failure'), create=True):
            db.on_save_db_btn_click()
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotEqual(db.ui.save_db_btn.default_stylesheet, db.ui.save_db_btn.styleSheet())

    def test_save_without_visible_tab_writes_database(self):
        db = self.database({'1': record()})
        db.db_tool_dict['1']['name'] = 'saved after tab removal'
        db.on_tools_db_edited(silent=True)
        self.tabs.removeTab(0)
        self.assertTrue(db.on_save_tools_db())
        self.assertEqual('saved after tab removal', json.loads(self.path.read_text())['1']['name'])
        self.assertEqual(db.ui.save_db_btn.default_stylesheet, db.ui.save_db_btn.styleSheet())

    def test_json_export_preserves_units_and_failed_export_preserves_file(self):
        import appDatabase
        path = self.path.with_suffix('.json')
        source = {'7': record(4)}
        appDatabase.write_tools_database(path, source, units='MM')
        self.assertEqual(source, appDatabase.load_tools_database(path, self.app.options, units='MM'))
        with self.assertRaises(ValueError):
            appDatabase.load_tools_database(path, self.app.options, units='IN')
        before = path.read_bytes()
        source['7']['vendor_note'] = float('nan')
        with self.assertRaises(ValueError):
            appDatabase.write_tools_database(path, source, units='MM')
        self.assertEqual(before, path.read_bytes())
        db = self.database({'7': record(4)})
        self.app.app_units = 'IN'
        self.app.get_last_save_folder = lambda: str(self.path.parent)
        with patch('appDatabase.FCFileSaveDialog.get_saved_filename', return_value=(str(path), '')):
            self.assertTrue(db.on_export_tools_db_file())
        self.assertEqual({'7': record(4)}, appDatabase.load_tools_database(path, self.app.options, units='MM'))

    def test_save_preserves_unknown_fields_and_clears_dirty_after_commit(self):
        db_record = record(4)
        db_record['vendor_note'] = {'keep': True}
        db_record['data']['future_option'] = ['keep']
        db = self.database({'7': db_record})
        db.db_tool_dict['7']['data']['tools_paint_overlap'] = 23.0
        self.app.tools_db_changed_flag = True

        self.assertTrue(db.on_save_tools_db(silent=True))
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual({'keep': True}, saved['7']['vendor_note'])
        self.assertEqual(['keep'], saved['7']['data']['future_option'])
        self.assertEqual(23.0, saved['7']['data']['tools_paint_overlap'])
        self.assertFalse(self.app.tools_db_changed_flag)

    def test_discard_successful_request_closes_and_clears_dirty_state(self):
        import appDatabase

        db = self.database({'1': record()})
        db.select_tool('1')
        db.on_tool_request = MagicMock(return_value=True)
        self.app.tools_db_changed_flag = True
        self.tabs.closeTab = MagicMock(
            side_effect=lambda index: setattr(self.app, 'tools_db_changed_flag', False) or True)
        DecisionMessageBox.response = 'no'

        with patch.object(appDatabase, 'FCMessageBox', DecisionMessageBox):
            self.assertTrue(db.on_tool_requested_from_app())

        self.assertFalse(self.app.tools_db_changed_flag)
        self.tabs.closeTab.assert_called_once_with(0)

    def test_confirm_close_save_discard_cancel(self):
        import appDatabase

        class FakeMessageBox:
            response = 'yes'

            def __init__(self, parent=None):
                self.buttons = {}

            def addButton(self, label, role):
                self.buttons[str(label)] = object()
                return self.buttons[str(label)]

            def clickedButton(self):
                return self.buttons[{'yes': 'Yes', 'no': 'No', 'cancel': 'Cancel'}[self.response]]

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        for response, expected, dirty in (('yes', True, True), ('no', True, False),
                                          ('cancel', False, True)):
            with self.subTest(response=response), patch.object(appDatabase, 'FCMessageBox', FakeMessageBox):
                FakeMessageBox.response = response
                db = self.database({'1': record()})
                db.app.tools_db_changed_flag = True
                db.on_save_tools_db = MagicMock(return_value=True)
                self.assertEqual(expected, db.confirm_close())
                self.assertEqual(dirty, db.app.tools_db_changed_flag)

        db = self.database({'1': record()})
        db._close_preapproved = True
        self.assertTrue(db.confirm_close())
        self.assertFalse(db._close_preapproved)

    def test_confirm_close_keeps_dirty_state_when_save_fails(self):
        import appDatabase

        class SaveFailureMessageBox:
            def __init__(self, parent=None):
                self.yes = object()

            def addButton(self, label, role):
                return self.yes if str(label) == 'Yes' else object()

            def clickedButton(self):
                return self.yes

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        with patch.object(appDatabase, 'FCMessageBox', SaveFailureMessageBox):
            db = self.database({'1': record()})
            db.app.tools_db_changed_flag = True
            db.on_save_tools_db = MagicMock(return_value=False)
            self.assertFalse(db.confirm_close())
            self.assertTrue(db.app.tools_db_changed_flag)

class ConsumerChecks(DatabaseCase):
    def test_missing_database_opens_recovery_editor_without_creating_file(self):
        from appHandlers.appUIActions import AppUIActions

        self.path.unlink()
        handler = self.actions_handler()

        self.assertEqual('fail', AppUIActions.on_tools_database(handler, source='app'))
        self.assertIsInstance(self.app.tools_db_tab, ToolsDB2)
        self.assertFalse(self.app.tools_db_tab._db_load_valid)
        self.assertTrue(self.app.tools_db_tab.ui.import_db_btn.isEnabled())
        self.assertFalse(self.path.exists())

    def test_unreadable_database_opens_recovery_editor_without_overwriting(self):
        from appHandlers.appUIActions import AppUIActions

        self.path.unlink()
        self.path.mkdir()
        handler = self.actions_handler()

        self.assertEqual('fail', AppUIActions.on_tools_database(handler, source='app'))
        self.assertIsInstance(self.app.tools_db_tab, ToolsDB2)
        self.assertFalse(self.app.tools_db_tab._db_load_valid)
        self.assertTrue(self.app.tools_db_tab.ui.import_db_btn.isEnabled())
        self.assertTrue(self.path.is_dir())

    def test_numeric_targets_automatic_lookup(self):
        cases = [
            ('appPlugins.ToolPaint.Paint', 'ToolPaint', 4, 'paint_tools', 'tools_paint_overlap'),
            ('appPlugins.ToolNCC.Ncc', 'ToolNcc', 5, 'ncc_tools', 'tools_ncc_overlap'),
            ('appPlugins.ToolIsolation', 'ToolIsolation', 3, 'iso_tools', 'tools_iso_overlap'),
            ('appPlugins.ToolCutOut', 'CutOut', 6, 'cut_tool_dict', 'tools_cutout_margin')]
        for module, class_name, target, storage, parameter in cases:
            with self.subTest(tool=class_name):
                tool = record(target)
                tool['data'][parameter] = 23.0
                tool['data']['tools_mill_feedrate'] = 321.0
                self.path.write_text(json.dumps({'1': tool}), encoding='utf-8')
                cls = getattr(importlib.import_module(module), class_name)
                table = MagicMock()
                table.rowCount.return_value = 0
                stub = SimpleNamespace(
                    app=self.app, decimals=4, default_data=deepcopy(record()['data']),
                    blockSignals=MagicMock(), ui_disconnect=MagicMock(), ui_connect=MagicMock(),
                    build_ui=MagicMock(), update_ui=MagicMock(), on_tool_default_add=MagicMock(),
                    ui=SimpleNamespace(tools_table=table))
                setattr(stub, storage, {})
                cls.on_tool_add(stub, custom_dia=2.4)
                stub.on_tool_default_add.assert_not_called()
                result = getattr(stub, storage)
                data = result['data'] if target == 6 else result[1]['data']
                self.assertEqual(23.0, data[parameter])
                self.assertEqual(321.0, data['tools_mill_feedrate'])

                # A library in other units adds a default tool and says why on the status bar.
                self.path.write_text(json.dumps({'format': 'flatcam-tools', 'version': 1, 'units': 'IN',
                                                 'tools': {'1': tool}}), encoding='utf-8')
                self.app.inform.reset_mock()
                setattr(stub, storage, {})
                cls.on_tool_add(stub, custom_dia=2.4)
                stub.on_tool_default_add.assert_called_once()
                stub.on_tool_default_add.reset_mock()
                self.assertIn('IN', self.app.inform.emit.call_args[0][0])

    def test_paint_offset_comes_from_the_matching_record(self):
        from appPlugins.ToolPaint.Paint import ToolPaint

        matching = record(4)
        matching['data']['tools_mill_offset_type'] = 3
        matching['data']['tools_mill_offset_value'] = 0.75
        trailing = record(1)
        trailing['data']['tools_mill_offset_type'] = 0
        trailing['data']['tools_mill_offset_value'] = 0.0
        self.path.write_text(json.dumps({'1': matching, '2': trailing}), encoding='utf-8')
        table = MagicMock()
        table.rowCount.return_value = 0
        stub = SimpleNamespace(
            app=self.app, decimals=4, default_data=deepcopy(record()['data']), paint_tools={},
            blockSignals=MagicMock(), build_ui=MagicMock(), update_ui=MagicMock(),
            on_tool_default_add=MagicMock(), ui=SimpleNamespace(
                new_tooldia_entry=MagicMock(), tools_table=table),
            on_row_selection_change=MagicMock())
        stub.ui.new_tooldia_entry.get_value.return_value = 2.4

        ToolPaint.on_tool_add(stub)

        self.assertEqual(3, stub.paint_tools[1]['offset'])
        self.assertEqual(0.75, stub.paint_tools[1]['offset_value'])

    def test_paint_tolerance_boundaries_no_match_and_ambiguity(self):
        from appPlugins.ToolPaint.Paint import ToolPaint

        def run(records):
            self.path.write_text(json.dumps(records), encoding='utf-8')
            table = MagicMock()
            table.rowCount.return_value = 0
            stub = SimpleNamespace(
                app=self.app, decimals=4, default_data=deepcopy(record()['data']), paint_tools={},
                blockSignals=MagicMock(), build_ui=MagicMock(), update_ui=MagicMock(),
                on_tool_default_add=MagicMock(), ui=SimpleNamespace(
                    new_tooldia_entry=MagicMock(), tools_table=table),
                on_row_selection_change=MagicMock())
            stub.ui.new_tooldia_entry.get_value.return_value = 2.4
            ToolPaint.on_tool_add(stub, custom_dia=2.4)
            return stub

        for bounds in ((2.4, 2.6), (2.2, 2.4)):
            with self.subTest(bounds=bounds):
                tool = record(4, 2.5)
                tool['data']['tol_min'], tool['data']['tol_max'] = bounds
                result = run({'1': tool})
                result.on_tool_default_add.assert_not_called()
                self.assertIn(1, result.paint_tools)

        tool = record(4, 2.5)
        tool['data']['tol_min'], tool['data']['tol_max'] = (2.5, 2.6)
        result = run({'1': tool})
        result.on_tool_default_add.assert_called_once()
        self.assertEqual({}, result.paint_tools)

        first = record(4, 2.5)
        second = record(4, 2.6)
        for tool in (first, second):
            tool['data']['tol_min'], tool['data']['tol_max'] = (2.2, 2.6)
        result = run({'1': first, '2': second})
        result.on_tool_default_add.assert_not_called()
        self.assertEqual({}, result.paint_tools)

    def test_drilling_numeric_target_updates_parameters(self):
        from appPlugins.ToolDrilling import ToolDrilling
        tool = record(2)
        tool['data']['tools_drill_feedrate_z'] = 321.0
        stub = SimpleNamespace(
            app=self.app, excellon_obj=object(),
            excellon_tools={1: {'tooldia': 2.4, 'data': {'tools_drill_feedrate_z': 99.0}}},
            tools_db_dict={'1': tool}, build_tool_ui=MagicMock(), blockSignals=MagicMock())
        ToolDrilling.replace_tools(stub)
        self.assertEqual(321.0, stub.excellon_tools[1]['data']['tools_drill_feedrate_z'])
        stub.excellon_tools[1]['data']['tools_drill_feedrate_z'] = 99.0
        self.assertEqual(321.0, tool['data']['tools_drill_feedrate_z'])

    def test_drilling_tolerance_merges_tools_with_the_same_db_diameter(self):
        from shapely import Point
        from appPlugins.ToolDrilling import ToolDrilling
        tool = record(2, 0.8)
        tool['data']['tol_min'], tool['data']['tol_max'] = (0.75, 0.85)
        stub = SimpleNamespace(
            app=self.app, excellon_obj=object(),
            excellon_tools={
                1: {'tooldia': 0.8, 'drills': [Point(0, 0)], 'slots': [],
                    'solid_geometry': [Point(0, 0).buffer(0.4)], 'data': {}},
                2: {'tooldia': 1.0, 'drills': [Point(5, 5)], 'slots': [],
                    'solid_geometry': [Point(5, 5).buffer(0.5)], 'data': {}},
                3: {'tooldia': 0.82, 'drills': [Point(1, 1), Point(2, 2)], 'slots': [((3, 3), (4, 3))],
                    'solid_geometry': [Point(1, 1).buffer(0.41), Point(2, 2).buffer(0.41)], 'data': {}},
            },
            tools_db_dict={'1': tool}, build_tool_ui=MagicMock(), blockSignals=MagicMock())
        object_tools = stub.excellon_tools

        ToolDrilling.replace_tools(stub)

        self.assertEqual([1, 2], sorted(stub.excellon_tools))
        merged, other = stub.excellon_tools[1], stub.excellon_tools[2]
        self.assertEqual(0.8, merged['tooldia'])
        self.assertEqual([(0, 0), (1, 1), (2, 2)], [(p.x, p.y) for p in merged['drills']])
        self.assertEqual([((3, 3), (4, 3))], merged['slots'])
        self.assertEqual(3, len(merged['solid_geometry']))
        self.assertEqual(tool['data']['tools_drill_feedrate_z'], merged['data']['tools_drill_feedrate_z'])
        self.assertEqual(1.0, other['tooldia'])
        self.assertEqual(1, len(other['drills']))
        stub.build_tool_ui.assert_called_once()
        # the tools of the Excellon object must not change
        self.assertEqual([0.8, 1.0, 0.82], [t['tooldia'] for t in object_tools.values()])
        self.assertEqual([1, 1, 2], [len(t['drills']) for t in object_tools.values()])

    def test_cutout_picker_does_not_mutate_database_record(self):
        from appPlugins.ToolCutOut import CutOut
        tool = record(6)
        before = deepcopy(tool)
        stub = SimpleNamespace(app=self.app, default_data=deepcopy(tool['data']),
                               cut_tool_dict={}, update_ui=MagicMock(),
                               ui=SimpleNamespace(dia=MagicMock()))
        CutOut.on_cutout_tool_add_from_db_executed(stub, tool)
        self.assertEqual(before, tool)


if __name__ == '__main__':
    unittest.main(verbosity=2)
