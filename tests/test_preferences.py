from __future__ import annotations




# ======================================================================
# merged from tests/test_defaults_persistence.py
# ======================================================================
"""Tests for version-independent defaults persistence and safe .get() fallbacks.

Ensure defaults dict is no longer versioned, preferences survive app
version changes, and all reads use .get() with factory-default fallbacks.
"""


import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from unittest.mock import Mock

from PyQt6 import QtWidgets
import Bookmark as bookmark
from Bookmark import BookmarkManager
from defaults import AppDefaults, AppOptions, Excellon, Gerber, Geometry


class FakeSignal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


class TestVersionIndependentPreferences:
    """Preferences must survive app version changes without orphaning or reset."""

    def test_saved_preferences_survive_version_change(self, tmp_path):
        """User preferences saved in version 1.0 load correctly in version 2.0."""
        defaults_v1 = AppDefaults(beta=False, version="1.0")
        defaults_v1["units"] = "IN"
        defaults_v1["global_version_check"] = False
        defaults_v1["global_update_url"] = "https://custom.example.com"

        filename = tmp_path / "current_defaults.FlatConfig"
        defaults_v1.write(str(filename))

        defaults_v2 = AppDefaults(beta=False, version="2.0")
        inform = FakeSignal()
        defaults_v2.load(str(filename), inform)

        assert defaults_v2["units"] == "IN"
        assert defaults_v2["global_version_check"] is False
        assert defaults_v2["global_update_url"] == "https://custom.example.com"

    def test_user_overrides_win_and_missing_keys_fall_back_to_factory_defaults(self, tmp_path):
        """Falsy user overrides (False, 0, '') are kept; keys missing from an old file get factory values."""
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps({"global_version_check": False, "global_worker_number": 0,
                                        "global_last_folder": "", "units": "IN"}))

        defaults = AppDefaults(beta=False, version="2.0")
        defaults.load(str(filename), FakeSignal())

        assert defaults["global_version_check"] is False
        assert defaults["global_worker_number"] == 0
        assert defaults["global_last_folder"] == ""
        assert defaults["units"] == "IN"
        assert defaults["global_update_url"] == AppDefaults.factory_defaults["global_update_url"]

    def test_old_preference_choices_are_migrated(self, tmp_path):
        """Old 'delete sources' becomes 'keep target' off and 'delete subtractors' on; the old PNG DPI 96 becomes 300."""
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps({"tools_sub_delete_sources": True, "tools_film_png_dpi": 96}))

        defaults = AppDefaults(beta=False, version="2.0")
        defaults.load(str(filename), FakeSignal())

        assert defaults["tools_sub_keep_target"] is False
        assert defaults["tools_sub_delete_subtractors"] is True
        assert defaults["tools_film_png_dpi"] == 300


class TestPreferencesFileWrite:
    """A save that fails keeps the old preferences file; a cut file would reset all preferences at the next start."""

    @staticmethod
    def _saved_preferences(tmp_path):
        filename = tmp_path / "current_defaults.FlatConfig"
        defaults = AppDefaults(beta=False, version="1.0")
        defaults["units"] = "IN"
        defaults.write(str(filename))
        defaults["units"] = "MM"
        return defaults, filename

    def test_value_that_cannot_be_saved_keeps_the_old_file(self, tmp_path):
        defaults, filename = self._saved_preferences(tmp_path)
        before = filename.read_bytes()
        defaults["zz_not_saveable"] = {1, 2}  # sorted last, so the save fails at the end of the file

        with pytest.raises((TypeError, ValueError)):
            defaults.write(str(filename))

        assert filename.read_bytes() == before
        assert [path.name for path in tmp_path.iterdir()] == [filename.name]
        loaded = AppDefaults(beta=False, version="1.0")
        inform = FakeSignal()
        loaded.load(str(filename), inform)
        assert inform.emissions == []
        assert loaded["units"] == "IN"


class TestSafeGetFallbacks:
    """All defaults/options reads must use .get() with factory-default fallbacks."""

    def test_nested_defaults_are_independent_from_factory_and_snapshot(self):
        """Runtime and restore snapshots must not share mutable factory values."""
        defaults = AppDefaults(beta=False, version="1.0")
        other_defaults = AppDefaults(beta=False, version="1.0")

        defaults["global_bookmarks"]["custom"] = {
            "title": "Custom",
            "url": "https://example.com",
        }
        defaults["document_font_sizes"].append("999")

        assert "custom" not in AppDefaults.factory_defaults["global_bookmarks"]
        assert "custom" not in other_defaults["global_bookmarks"]
        assert "999" not in AppDefaults.factory_defaults["document_font_sizes"]
        assert "999" not in other_defaults["document_font_sizes"]
        assert "custom" not in defaults.current_defaults["global_bookmarks"]


class TestLegacyCompatibility:
    """Legacy versioned files must be discovered and migrated."""

    def test_most_recent_legacy_selected_when_no_exact_match(self, tmp_path):
        """Most recent legacy file is selected when no exact version match."""
        legacy_10 = tmp_path / "current_defaults_1.0.FlatConfig"
        legacy_10.write_text(json.dumps({"units": "IN"}))
        
        legacy_20 = tmp_path / "current_defaults_2.0.FlatConfig"
        legacy_20.write_text(json.dumps({"units": "MM"}))

        found = AppDefaults.find_legacy_defaults_file(str(tmp_path), "3.0")
        # Should pick 2.0 as most recent
        assert found == str(legacy_20)

    @pytest.mark.parametrize("stable,units", [("{}", "IN")], ids=["empty"])
    def test_startup_loads_legacy_file_over_empty_stable_file(self, tmp_path, monkeypatch, stable, units):
        """_setup_paths_and_config() writes an empty stable file ('{}') before the discovery; saved ones still win."""
        import appMain

        (tmp_path / "current_defaults_8.995.FlatConfig").write_text(json.dumps({
            "units": "IN", "global_log_verbose": 1, "global_version_check": False}))
        (tmp_path / "current_defaults.FlatConfig").write_text(stable)
        app = MagicMock()
        app.version = "1.0"
        app.beta = False
        app.data_path = str(tmp_path)
        app.defaults_path.return_value = str(tmp_path / "current_defaults.FlatConfig")
        for name in ("AppLifecycle", "AppLogging", "GitHubUpdateChecker", "Pool"):
            monkeypatch.setattr(appMain, name, MagicMock())
        monkeypatch.setattr(appMain.darkdetect, "isDark", lambda: False)

        appMain.App._setup_defaults_and_preferences(app)
        assert app.defaults["units"] == units
        assert app.defaults["global_version_check"] is (units != "IN")


class TestExportPreferences:
    """File -> Export Preferences writes the preferences in effect, as the preferences file has them."""

    def test_export_writes_the_preferences(self, tmp_path, monkeypatch):
        from appHandlers.appIO import appIO

        defaults = AppDefaults(beta=False, version="1.0")
        defaults["global_gridx"] = 0.25
        defaults["tools_drill_cutz"] = -2.5
        target = tmp_path / "exported.FlatConfig"
        app = MagicMock(defaults=defaults, data_path=str(tmp_path))
        app.options = AppOptions(version="1.0", baseline=defaults, defaults=defaults)
        handler = SimpleNamespace(app=app, options=app.options, log=MagicMock(), inform=FakeSignal())
        monkeypatch.setattr("appHandlers.appIO.FCFileSaveDialog.get_saved_filename",
                            lambda **kwargs: (str(target), ""))

        appIO.on_export_preferences(handler)

        assert handler.inform.emissions[-1][0].startswith("[success]")
        app.file_saved.emit.assert_called_once_with("preferences", str(target))
        app.preferencesUiManager.defaults_read_form.assert_not_called()
        exported = AppDefaults(beta=False, version="1.0")
        exported.load(str(target), FakeSignal())
        assert exported["global_gridx"] == 0.25
        assert exported["tools_drill_cutz"] == -2.5


def test_bookmark_export_then_import_gives_the_same_bookmarks(tmp_path, monkeypatch):
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    menu = QtWidgets.QMenu()
    app = SimpleNamespace(
        defaults=AppDefaults(beta=False), options=AppOptions(version="1.0"), install_bookmarks=lambda **kwargs: None,
        inform=Mock(), log=Mock(), resource_location="assets/resources", get_last_save_folder=lambda: str(tmp_path),
        ui=SimpleNamespace(menuhelp_bookmarks=menu, menuhelp_bookmarks_manager=menu.addAction("Bookmark Manager")))
    path = tmp_path / "bookmarks.txt"
    monkeypatch.setattr(bookmark.FCFileSaveDialog, "get_saved_filename", lambda **kwargs: (str(path), ""))
    monkeypatch.setattr(bookmark.QtWidgets.QFileDialog, "getOpenFileName", lambda **kwargs: (str(path), ""))

    exporter = BookmarkManager(app, {"1": ["FlatCAM", "http://flatcam.org"], "2": ["PCB Shop", "https://ex.com/pcb"]})
    importer = BookmarkManager(app, {"1": ["FlatCAM", "http://flatcam.org"]})
    try:
        exporter.on_export_bookmarks()
        assert path.read_text() == "FlatCAM:http://flatcam.org\nPCB Shop:https://ex.com/pcb\n"
        # a blank line (a list written by hand)
        path.write_text(path.read_text() + "\n")
        importer.on_import_bookmarks()
        assert importer.bm_dict == {"1": ["FlatCAM", "http://flatcam.org"], "2": ["PCB Shop", "https://ex.com/pcb"]}
    finally:
        for manager in (exporter, importer):
            manager.deleteLater()
        qapp.processEvents()


def test_propagation_uses_factory_for_missing_keys_and_keeps_falsy_override(monkeypatch):
    for cls in (Excellon, Gerber, Geometry):
        monkeypatch.setattr(cls, "defaults", deepcopy(cls.defaults))
    Excellon.defaults["excellon_format_lower_in"] = -1
    defaults = AppDefaults(beta=False)
    del defaults.defaults["excellon_format_lower_in"]
    defaults.propagate_defaults()
    assert Excellon.defaults["excellon_format_lower_in"] == defaults.factory_defaults["excellon_format_lower_in"]

    Geometry.defaults["multidepth"] = True
    defaults = AppDefaults(beta=False)
    defaults["excellon_format_lower_in"] = 0
    defaults.propagate_defaults()
    assert Excellon.defaults["excellon_format_lower_in"] == 0
    assert "use_buffer_for_union" not in Gerber.defaults
    assert Geometry.defaults["multidepth"] is True


@pytest.mark.parametrize("change", ["value", "unchanged"])
def test_factory_snapshot_matches_current_defaults(tmp_path, change):
    path = tmp_path / "factory_defaults.FlatConfig"
    saved = deepcopy(AppDefaults.factory_defaults)
    if change == "value":
        saved["global_version_check"] = not saved["global_version_check"]
        saved["obsolete_preference"] = True
    path.write_text(json.dumps(saved))
    os.utime(path, (1_700_000_000, 1_700_000_000))
    original_mtime = path.stat().st_mtime_ns
    path.chmod(stat.S_IREAD)
    try:
        AppDefaults.save_factory_defaults(str(path), "2.0")
        assert json.loads(path.read_text()) == AppDefaults.factory_defaults
        if change == "unchanged":
            assert path.stat().st_mtime_ns == original_mtime
    finally:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_load_drops_the_removed_autosave_keys(tmp_path):
    path = tmp_path / "current_defaults.FlatConfig"
    path.write_text(json.dumps({"global_autosave": True, "global_autosave_timeout": 1000, "units": "IN"}))
    defaults = AppDefaults(beta=False)
    defaults.load(str(path), SimpleNamespace(emit=lambda *args: None))
    assert "global_autosave" not in defaults
    assert "global_autosave_timeout" not in defaults
    assert defaults["units"] == "IN"


@pytest.mark.parametrize("saved, kept", [
    # the default from 2019-11 to 2025-10, as update_filters() left it after a .gtl export
    ("Gerber File .gtl (*.gtl);;Gerber File .gbr (*.gbr);;Gerber File .art (*.art)All Files (*.*)", False),
    ("Gerber File .gtl (*.gtl);;Gerber File .gbr (*.gbr);;All Files (*.*)", True),
], ids=["legacy", "valid"])
def test_load_replaces_the_broken_gerber_save_filters(tmp_path, saved, kept):
    path = tmp_path / "current_defaults.FlatConfig"
    path.write_text(json.dumps({"gerber_save_filters": saved, "units": "IN"}))
    defaults = AppDefaults(beta=False)
    defaults.load(str(path), SimpleNamespace(emit=lambda *args: None))
    expected = saved if kept else AppDefaults.factory_defaults["gerber_save_filters"]
    assert defaults["gerber_save_filters"] == expected
    assert "All Files (*.*)" in defaults["gerber_save_filters"].split(";;")
    assert defaults["units"] == "IN"


# ======================================================================
# merged from tests/test_app_option_fallback.py
# ======================================================================
"""Stage 1 (reduce unnecessary inheritance): the shared app_option function and the ser_attrs duplicates.

Everything is headless: objects are built with a SimpleNamespace app and the VisPy ShapeCollection patched out.
"""

import os
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import camlib  # noqa: E402
from appObjects.AppObjectTemplate import FlatCAMObj  # noqa: E402
from appObjects.CNCJobObject import CNCJobObject  # noqa: E402
from appObjects.DocumentObject import DocumentObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appObjects.ScriptObject import ScriptObject  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from appParsers.ParseGerber import Gerber  # noqa: E402
from camlib import CNCjob, Geometry, app_option  # noqa: E402
# fixtures and helpers of the characterization tests (a fixture imported here is collected as ours)
from tests.helpers_objects import (  # noqa: F401,E402
    app, assert_same_tools, _headless_qt, make_excellon, make_gerber, save_and_load,
)


class Defaults(dict):
    def __init__(self, data=None, factory=None):
        super().__init__(data or {})
        self.factory_defaults = factory or {}


def fake_app(options=None, defaults=None):
    return SimpleNamespace(options=options or {}, defaults=defaults)


class TestAppOptionFallbackOrder:
    def test_options_win_over_defaults_and_factory(self):
        a = fake_app({'k': 1}, Defaults({'k': 2}, {'k': 3}))
        assert app_option(a, 'k', 4) == 1


    def test_fallback_last(self):
        a = fake_app({}, Defaults())
        assert app_option(a, 'k', 4) == 4
        assert app_option(a, 'k') is None


    def test_falsy_option_value_is_kept(self):
        a = fake_app({'k': 0}, Defaults({'k': 2}))
        assert app_option(a, 'k', 4) == 0


def build_objects(app):
    app.preprocessors = defaultdict(Mock)
    return {
        'Geometry': Geometry(app), 'Gerber': Gerber(app), 'Excellon': Excellon(app), 'CNCjob': CNCjob(app),
        'GerberObject': make_gerber(app), 'ExcellonObject': make_excellon(app),
        'GeometryObject': GeometryObject('geo', app), 'CNCJobObject': CNCJobObject('job', app),
    }


class TestSerAttrsHaveNoDuplicates:

    def test_old_layout_dict_loads_into_new_layout(self, app):
        """A project saved with the old (duplicated) ser_attrs has the same keys: it must load."""
        grb = make_gerber(app)
        new_keys = set(grb.to_dict())
        old_layout = list(grb.ser_attrs) + ['tools', 'solid_geometry']
        grb.ser_attrs = old_layout
        old_dict = grb.to_dict()
        assert set(old_dict) == new_keys

        other = make_gerber(app, name='other')
        other.from_dict(old_dict)
        assert other.units == grb.units
        assert sorted(other.tools) == sorted(grb.tools)

        app.preprocessors = defaultdict(Mock)
        job = CNCJobObject('job', app)
        job_keys = set(job.to_dict())
        job.ser_attrs = list(job.ser_attrs) + ['kind', 'tools']
        assert set(job.to_dict()) == job_keys


# ======================================================================
# merged from tests/test_database_option_fallback.py
# ======================================================================
"""Regression tests for mutable factory fallbacks in the tools database."""

from types import SimpleNamespace
import unittest

from appDatabase import _database_option


class Defaults_m3:
    def __init__(self, values, factory_defaults):
        self.values = values
        self.factory_defaults = factory_defaults

    def get(self, key, default=None):
        return self.values.get(key, default)


class TestDatabaseOptionFallback(unittest.TestCase):
    def test_missing_mutable_values_are_independent_factory_copies(self):
        factory_value = {"colors": ["red"]}
        app = SimpleNamespace(
            defaults=Defaults_m3({}, {"mutable": factory_value}),
            options={},
        )

        first = _database_option(app, "mutable")
        second = _database_option(app, "mutable")

        self.assertEqual(first, factory_value)
        self.assertIsNot(first, factory_value)
        self.assertIsNot(first, second)
        first["colors"].append("blue")
        self.assertEqual(factory_value, {"colors": ["red"]})
        self.assertEqual(second, {"colors": ["red"]})


    def test_present_falsy_default_overrides_factory_value(self):
        factory_value = ["factory"]
        default_value = []
        app = SimpleNamespace(
            defaults=Defaults_m3({"mutable": default_value}, {"mutable": factory_value}),
            options={},
        )

        result = _database_option(app, "mutable")

        self.assertIs(result, default_value)
        self.assertIsNot(result, factory_value)


# ======================================================================
# merged from tests/test_options_split.py
# ======================================================================
"""Tests for the split of application settings and project options."""
import json
import os
import sys
import tempfile
import unittest
import threading
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appObjects.AppObject import AppObject
from defaults import AppDefaults, AppOptions, APP_TOOL_KEYS, PROJECT_KEYS, is_project_key, project_options


class TestIsProjectKey(unittest.TestCase):
    def test_tools_keys_are_project_keys(self):
        for key in ('tools_iso_tooldia', 'tools_drill_ppname_e', 'tools_mill_ppname_g', 'tools_ncc_operation'):
            self.assertTrue(is_project_key(key), key)
        for key in ('global_gridx', 'global_workspace', 'global_appearance', 'cncjob_tooldia',
                    'excellon_optimization_type', 'gerber_plot', 'units_precision', 'first_run',
                    'root_folder_path', None, 5):
            self.assertFalse(is_project_key(key), key)

class TestAppOptionsMergedView(unittest.TestCase):
    def make(self):
        defaults = AppDefaults(beta=False)
        calls = []
        options = AppOptions(version='test', callback=calls.append, baseline=defaults, defaults=defaults)
        return defaults, options, calls

    def test_application_key_reads_and_writes_defaults(self):
        defaults, options, calls = self.make()
        options['global_gridx'] = 7.5
        self.assertEqual(defaults['global_gridx'], 7.5)
        self.assertEqual(options['global_gridx'], 7.5)
        self.assertEqual(options.get('global_gridx'), 7.5)
        self.assertIn('global_gridx', options)
        self.assertEqual(calls, ['global_gridx'])
        defaults['global_gridx'] = 3.0
        self.assertEqual(options['global_gridx'], 3.0)

    def test_project_key_stays_in_own_storage(self):
        defaults, options, calls = self.make()
        before = defaults['gerber_bboxmargin']
        options['gerber_bboxmargin'] = before + 1.0
        self.assertEqual(options['gerber_bboxmargin'], before + 1.0)
        self.assertEqual(defaults['gerber_bboxmargin'], before)
        self.assertEqual(calls, ['gerber_bboxmargin'])

    def test_load_project_ignores_application_keys(self):
        defaults, options, _calls = self.make()
        appearance = defaults['global_appearance']
        options['gerber_bboxrounded'] = not defaults['gerber_bboxrounded']
        options.load_project({'global_appearance': 'not-a-theme', 'gerber_bboxmargin': 0.75,
                              'global_graphic_engine': '2D'})
        self.assertEqual(defaults['global_appearance'], appearance)
        self.assertEqual(options['gerber_bboxmargin'], 0.75)
        self.assertEqual(options['gerber_bboxrounded'], defaults['gerber_bboxrounded'])
        self.assertNotIn('global_graphic_engine', options)

    def test_load_project_converts_default_lengths_the_file_lacks(self):
        defaults, options, _calls = self.make()
        defaults['units'] = 'MM'
        options.load_project({'units': 'IN', 'gerber_bboxmargin': 0.5})
        self.assertAlmostEqual(options['excellon_drill_tooldia'], 0.8 / 25.4)
        self.assertEqual(options['gerber_bboxmargin'], 0.5)
        options.load_project({'units': 'MM'})
        self.assertEqual(options['excellon_drill_tooldia'], 0.8)

    def test_load_keeps_only_project_keys_unless_asked(self):
        defaults, options, _calls = self.make()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'prefs.FlatConfig')
            with open(path, 'w') as f:
                json.dump({'global_gridx': 42.0, 'gerber_bboxmargin': 0.5}, f)
            options.load(path, Mock())
            self.assertEqual(options['gerber_bboxmargin'], 0.5)
            self.assertNotEqual(defaults['global_gridx'], 42.0)
            options.load(path, Mock(), include_app_keys=True)
            self.assertEqual(defaults['global_gridx'], 42.0)
        self.assertTrue(all(is_project_key(key) for key in options.options))


class TestNewObjectOptionFill(unittest.TestCase):
    def fill(self, project_units, provided=None):
        defaults = AppDefaults(beta=False)
        options = AppOptions(version='test', baseline=defaults, defaults=defaults)
        options['units'] = project_units
        app = SimpleNamespace(options=options, defaults=defaults, log=Mock(), inform_shell=Mock(), inform=Mock())
        owner = SimpleNamespace(app=app, load_context=threading.local())
        owner._app_option = MethodType(AppObject._app_option, owner)
        made = []

        class FakeObject:
            tool_option_prefix = ''
            inherits_mill_options = False

            def __init__(self, name, app=None):
                self.obj_options = {}
                self.units = None
                made.append(self)

        def initialize(obj, _app):
            return False
        if provided is not None:
            initialize.provided_options = provided
        with patch('appObjects.AppObject.GerberObject', FakeObject), patch('appObjects.AppObject.CNCJobObject', FakeObject):
            for kind in ('gerber', 'cncjob'):
                AppObject._new_object(owner, kind, 'name', initialize)
        return defaults, made

    def test_application_lengths_follow_the_units_of_the_project(self):
        defaults, made = self.fill('IN')
        self.assertAlmostEqual(made[1].obj_options['tooldia'], defaults['cncjob_tooldia'] / 25.4)
        defaults, made = self.fill('MM')
        self.assertEqual(made[1].obj_options['tooldia'], defaults['cncjob_tooldia'])


# ======================================================================
# merged from tests/test_preferences_project_keys.py
# ======================================================================
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

@pytest.fixture(scope='module')
def _pref_env(tmp_path_factory):
    """One real MainGUI (1.7 s to build) for the module; pref_app puts it back to the factory state for each test."""
    from appGUI.MainGUI import MainGUI
    from appHandlers.appUIActions import AppUIActions
    tmp_path = tmp_path_factory.mktemp('prefs')
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    defaults = AppDefaults(beta=False)
    app = MagicMock(decimals=4, resource_location='assets/resources', data_path=str(tmp_path))
    app.defaults = defaults
    app.options = AppOptions(version='test', baseline=defaults, defaults=defaults)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(OptionsGroupUI, 'app', app)
    ui = MainGUI(app)
    app.ui = ui
    app.preferencesUiManager = PreferencesUIManager(defaults, str(tmp_path), ui, MagicMock(), app.options)
    app.ui_actions = AppUIActions(app)
    app.on_preferences = app.ui_actions.on_preferences
    app.on_toggle_preferences = app.ui_actions.on_toggle_preferences
    factory = deepcopy(dict(defaults))
    yield app, factory
    if ui.preferences_window is not None:
        ui.preferences_window.close_quietly()
    # a full MainGUI left to the interpreter exit crashes the process
    ui.close()
    ui.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
    qapp.processEvents()
    monkeypatch.undo()


@pytest.fixture
def pref_app(_pref_env):
    """An App mock with a real MainGUI and PreferencesUIManager; yields it with the Preferences window open."""
    from appGUI import shortcuts
    app, factory = _pref_env
    ui = app.ui
    if ui.preferences_window is not None:
        ui.preferences_window.close_quietly()
    shortcuts.registry().reset_all()
    app.defaults.update(deepcopy(factory))
    app.options.update(deepcopy(factory))
    manager = app.preferencesUiManager
    manager.defaults_write_form()
    manager.preferences_changed_flag = False
    app.on_preferences()
    yield app
    if ui.preferences_window is not None:
        ui.preferences_window.close_quietly()
    shortcuts.registry().reset_all()


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


# ======================================================================
# merged from tests/test_factory_snapshot_atomic.py
# ======================================================================
"""Transactional-write guarantees for AppDefaults.save_factory_defaults.

Verifies that the factory snapshot is written atomically: existing bytes
survive serialization or replacement failure, temp artifacts are cleaned,
and Windows read-only mode is restored on failure.
"""

import json
import os
import stat
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from defaults import AppDefaults


OLD_VALUES = {"global_version_check": False, "obsolete_preference": True}


def _write_read_only_snapshot(path, data):
    """Write *data* as a read-only factory snapshot."""
    path.write_text(json.dumps(data))
    path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


# -- dump failure with existing read-only snapshot --------------------------

class TestDumpFailurePreservesExisting:
    """If json.dump raises, the existing read-only snapshot survives intact."""

    def test_existing_bytes_survive_dump_failure(self, tmp_path):
        dest = tmp_path / "factory_defaults.FlatConfig"
        _write_read_only_snapshot(dest, OLD_VALUES)
        original_bytes = dest.read_bytes()

        with patch("defaults.json.dump", side_effect=RuntimeError("disk full")):
            AppDefaults.save_factory_defaults(str(dest), "2.0")

        assert dest.read_bytes() == original_bytes
        assert json.loads(dest.read_text()) == OLD_VALUES

# -- missing destination failure --------------------------------------------

class TestMissingDestinationFailure:
    """If no file exists and writing fails, no truncated destination is left."""

    def test_no_destination_after_dump_failure(self, tmp_path):
        dest = tmp_path / "factory_defaults.FlatConfig"

        with patch("defaults.json.dump", side_effect=RuntimeError("disk full")):
            AppDefaults.save_factory_defaults(str(dest), "2.0")

        assert not dest.exists()

# -- successful write to read-only existing ---------------------------------

class TestSuccessfulReadOnlyUpdate:
    """Successful update of a read-only snapshot produces correct content and mode."""

    def test_read_only_snapshot_updated_successfully(self, tmp_path):
        dest = tmp_path / "factory_defaults.FlatConfig"
        _write_read_only_snapshot(dest, OLD_VALUES)

        AppDefaults.save_factory_defaults(str(dest), "2.0")

        assert json.loads(dest.read_text()) == AppDefaults.factory_defaults
        mode = dest.stat().st_mode
        assert mode & stat.S_IWRITE == 0  # read-only again
