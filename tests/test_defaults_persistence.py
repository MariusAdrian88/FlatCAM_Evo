"""Tests for version-independent defaults persistence and safe .get() fallbacks.

Ensure defaults dict is no longer versioned, preferences survive app
version changes, and all reads use .get() with factory-default fallbacks.
"""

from __future__ import annotations

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
