"""Tests for version-independent defaults persistence and safe .get() fallbacks.

Ensure defaults dict is no longer versioned, preferences survive app
version changes, and all reads use .get() with factory-default fallbacks.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from defaults import AppDefaults, AppOptions


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

    def test_missing_keys_in_old_file_fall_back_to_factory_defaults(self, tmp_path):
        """New keys added in later versions fall back to factory defaults without KeyError."""
        old_data = {
            "units": "MM",
            "global_version_check": True,
        }
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps(old_data))

        defaults = AppDefaults(beta=False, version="2.0")
        inform = FakeSignal()
        defaults.load(str(filename), inform)

        assert defaults["units"] == "MM"
        assert defaults["global_version_check"] is True
        assert defaults["global_update_url"] == AppDefaults.factory_defaults["global_update_url"]
        assert defaults["global_worker_number"] == AppDefaults.factory_defaults["global_worker_number"]

    def test_old_subtract_delete_sources_choice_is_kept(self, tmp_path):
        """The old Subtract 'delete sources' choice becomes 'keep target' off and 'delete subtractors' on."""
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps({"tools_sub_delete_sources": True}))

        defaults = AppDefaults(beta=False, version="2.0")
        defaults.load(str(filename), FakeSignal())

        assert defaults["tools_sub_keep_target"] is False
        assert defaults["tools_sub_delete_subtractors"] is True
        assert "tools_sub_delete_sources" not in defaults.defaults

    def test_user_overrides_win_over_factory_defaults_including_falsy_values(self, tmp_path):
        """User overrides (including False, 0, empty strings) must not be replaced by defaults."""
        saved_data = {
            "global_version_check": False,
            "global_worker_number": 0,
            "global_last_folder": "",
            "units": "IN",
        }
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps(saved_data))

        defaults = AppDefaults(beta=False, version="2.0")
        inform = FakeSignal()
        defaults.load(str(filename), inform)

        assert defaults["global_version_check"] is False
        assert defaults["global_worker_number"] == 0
        assert defaults["global_last_folder"] == ""
        assert defaults["units"] == "IN"


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

    def test_full_disk_keeps_the_old_file(self, tmp_path):
        defaults, filename = self._saved_preferences(tmp_path)
        before = filename.read_bytes()

        with patch("defaults.os.fsync", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with pytest.raises(OSError):
                defaults.write(str(filename))

        assert filename.read_bytes() == before
        assert [path.name for path in tmp_path.iterdir()] == [filename.name]


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

    def test_appoptions_load_restores_baseline_before_sparse_overlay(self, tmp_path):
        """Sparse imports restore missing keys while preserving falsy overrides."""
        filename = tmp_path / "preferences.FlatConfig"
        filename.write_text(json.dumps({
            "global_version_check": False,
            "global_worker_number": 0,
            "global_last_folder": "",
        }))

        baseline = AppDefaults(beta=False, version="1.0")
        options = AppOptions(version="1.0", baseline=baseline)
        options["global_update_url"] = "https://stale.example.com"
        options.load(str(filename), FakeSignal())

        assert options["global_version_check"] is False
        assert options["global_worker_number"] == 0
        assert options["global_last_folder"] == ""
        assert options["global_update_url"] == AppDefaults.factory_defaults["global_update_url"]

class TestMigrationBehavior:
    """Migration should merge missing keys, not reset on version mismatch."""

    def test_no_migration_reset_on_version_mismatch(self, tmp_path):
        """Version mismatch in saved file should not trigger reset to factory defaults."""
        old_data = {
            "version": "1.0",
            "units": "IN",
            "global_version_check": False,
        }
        filename = tmp_path / "current_defaults.FlatConfig"
        filename.write_text(json.dumps(old_data))

        defaults = AppDefaults(beta=False, version="2.0")
        inform = FakeSignal()
        defaults.load(str(filename), inform)

        assert defaults.old_defaults_found is False
        assert defaults["units"] == "IN"
        assert defaults["global_version_check"] is False

class TestLegacyCompatibility:
    """Legacy versioned files must be discovered and migrated."""

    def test_legacy_file_discovered_when_stable_file_missing(self, tmp_path):
        """Legacy versioned file is found when stable file doesn't exist."""
        legacy_file = tmp_path / "current_defaults_1.0.FlatConfig"
        legacy_file.write_text(json.dumps({
            "units": "IN",
            "global_version_check": False,
        }))

        found = AppDefaults.find_legacy_defaults_file(str(tmp_path), "1.0")
        assert found == str(legacy_file)

    def test_most_recent_legacy_selected_when_no_exact_match(self, tmp_path):
        """Most recent legacy file is selected when no exact version match."""
        legacy_10 = tmp_path / "current_defaults_1.0.FlatConfig"
        legacy_10.write_text(json.dumps({"units": "IN"}))
        
        legacy_20 = tmp_path / "current_defaults_2.0.FlatConfig"
        legacy_20.write_text(json.dumps({"units": "MM"}))

        found = AppDefaults.find_legacy_defaults_file(str(tmp_path), "3.0")
        # Should pick 2.0 as most recent
        assert found == str(legacy_20)

    def test_startup_discovery_loads_legacy_preferences(self, tmp_path):
        """Startup flow discovers and loads legacy file when stable file missing."""
        from appMain import App
        
        # Create legacy file with user preferences
        legacy_file = tmp_path / "current_defaults_1.0.FlatConfig"
        legacy_file.write_text(json.dumps({
            "units": "IN",
            "global_version_check": False,
            "global_update_url": "https://custom.example.com",
        }))
        
        # Create app instance and call startup discovery
        app = App.__new__(App)
        app.data_path = str(tmp_path)
        app.version = "2.0"
        app.beta = False
        app.log = MagicMock()
        app.inform = FakeSignal()
        
        # Simulate startup discovery
        current_defaults_path = app.defaults_path()
        assert not os.path.isfile(current_defaults_path)
        
        legacy_path = AppDefaults.find_legacy_defaults_file(app.data_path, str(app.version))
        assert legacy_path == str(legacy_file)
        
        # Load from legacy file
        defaults = AppDefaults(beta=False, version="2.0")
        inform = FakeSignal()
        defaults.load(legacy_path, inform)
        
        # Verify user preferences preserved
        assert defaults["units"] == "IN"
        assert defaults["global_version_check"] is False
        # A custom share saved before the provider choice is kept for the legacy Digi provider.
        assert defaults["global_legacy_update_url"] == "https://custom.example.com"

    def test_factory_defaults_file_updated_with_new_keys(self, tmp_path):
        """Factory defaults file is rewritten if missing new keys."""
        factory_file = tmp_path / "factory_defaults.FlatConfig"
        
        # Write incomplete factory defaults (missing some keys)
        incomplete = {"units": "MM", "first_run": True}
        factory_file.write_text(json.dumps(incomplete))
        os.chmod(str(factory_file), stat.S_IWRITE)
        
        # Save factory defaults - should rewrite with all keys
        AppDefaults.save_factory_defaults(str(factory_file), "2.0")
        
        saved_data = json.loads(factory_file.read_text())
        # Should have all factory keys
        assert "global_version_check" in saved_data
        assert "global_update_url" in saved_data
        assert len(saved_data) >= len(AppDefaults.factory_defaults)
