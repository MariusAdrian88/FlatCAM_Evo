"""Regression checks for sparse settings and the versionless factory snapshot."""

import json
import os
import stat
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets
import Bookmark as bookmark
from Bookmark import BookmarkManager
from defaults import AppDefaults, AppOptions, Excellon, Gerber, Geometry


@pytest.mark.parametrize("existing", [False, True])
def test_bookmark_rebuild_preserves_defaults_and_runtime_identity(existing):
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    defaults = AppDefaults(beta=False)
    original = deepcopy(defaults.get("global_bookmarks"))
    options = AppOptions(version="1.0")
    runtime = {}
    if existing:
        options["global_bookmarks"] = runtime
    storage = {"1": ["Example", "https://example.com"]}
    manager = BookmarkManager(
        SimpleNamespace(
            defaults=defaults,
            options=options,
            install_bookmarks=lambda **kwargs: None,
        ),
        storage,
    )
    try:
        assert defaults.get("global_bookmarks") == original
        assert options["global_bookmarks"] == storage
        assert options["global_bookmarks"] is not defaults.get("global_bookmarks")
        assert options["global_bookmarks"]["1"] is not manager.bm_dict["1"]
        if existing:
            assert options["global_bookmarks"] is runtime
    finally:
        manager.close()
        manager.deleteLater()
        qapp.processEvents()


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


@pytest.mark.parametrize("target", ["format_lower_in", "excellon_format_lower_in"])
def test_propagation_uses_factory_for_missing_keys(monkeypatch, target):
    for cls in (Excellon, Gerber, Geometry):
        monkeypatch.setattr(cls, "defaults", deepcopy(cls.defaults))
    if target == "format_lower_in":
        del Excellon.defaults["excellon_format_lower_in"]
    Excellon.defaults[target] = -1
    defaults = AppDefaults(beta=False)
    del defaults.defaults["excellon_format_lower_in"]
    defaults.propagate_defaults()
    assert Excellon.defaults[target] == defaults.factory_defaults["excellon_format_lower_in"]


def test_propagation_preserves_falsy_override_and_unmapped_baseline(monkeypatch):
    for cls in (Excellon, Gerber, Geometry):
        monkeypatch.setattr(cls, "defaults", deepcopy(cls.defaults))
    Geometry.defaults["multidepth"] = True
    defaults = AppDefaults(beta=False)
    defaults["excellon_format_lower_in"] = 0
    defaults.propagate_defaults()
    assert Excellon.defaults["excellon_format_lower_in"] == 0
    assert "use_buffer_for_union" not in Gerber.defaults
    assert Geometry.defaults["multidepth"] is True


@pytest.mark.parametrize("change", ["value", "obsolete", "unchanged"])
def test_factory_snapshot_matches_current_defaults(tmp_path, change):
    path = tmp_path / "factory_defaults.FlatConfig"
    saved = deepcopy(AppDefaults.factory_defaults)
    if change == "value":
        saved["global_version_check"] = not saved["global_version_check"]
    elif change == "obsolete":
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
    # the broken factory value, as update_filters() left it after a .gtl export
    ("Gerber File .gtl (.gtl);;Gerber File .gbr (.gbr);;Gerber File .art (.art)All Files (.*);;", False),
    # the default from 2019-11 to 2025-10, as update_filters() left it after a .gtl export
    ("Gerber File .gtl (*.gtl);;Gerber File .gbr (*.gbr);;Gerber File .art (*.art)All Files (*.*)", False),
    ("Gerber File .gtl (*.gtl);;Gerber File .gbr (*.gbr);;All Files (*.*)", True),
], ids=["broken", "legacy", "valid"])
def test_load_replaces_the_broken_gerber_save_filters(tmp_path, saved, kept):
    path = tmp_path / "current_defaults.FlatConfig"
    path.write_text(json.dumps({"gerber_save_filters": saved, "units": "IN"}))
    defaults = AppDefaults(beta=False)
    defaults.load(str(path), SimpleNamespace(emit=lambda *args: None))
    expected = saved if kept else AppDefaults.factory_defaults["gerber_save_filters"]
    assert defaults["gerber_save_filters"] == expected
    assert "All Files (*.*)" in defaults["gerber_save_filters"].split(";;")
    assert defaults["units"] == "IN"
