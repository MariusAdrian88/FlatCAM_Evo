"""Focused, process-free tests for the updater integration."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from services.updater.manifest import ArchiveInfo, FileEntry, Manifest
from services.updater.github.transport import DEFAULT_REPOSITORY as DEFAULT_UPDATE_URL

CONFIGURED_UPDATE_URL = "https://updates.example.invalid/flatcam"


def _manifest(version="2.0", build="build-2", minimum="0", channel="windows"):
    archive = b"full release archive"
    return Manifest(
        schema_version=1,
        channel=channel,
        version=version,
        build_string=build,
        version_date="2026-09-18",
        minimum_required_version=minimum,
        release_notes="Fixes\n\n* Safer updates",
        archive=ArchiveInfo("flatcam-windows.zip", len(archive), hashlib.sha256(archive).hexdigest()),
        files=(FileEntry("flatcam.py", 3, hashlib.sha256(b"new").hexdigest()),),
        deletions=(),
    )


def _manifest_json(manifest):
    return json.dumps(
        {
            "schema_version": manifest.schema_version,
            "channel": manifest.channel,
            "version": manifest.version,
            "build": manifest.build_string,
            "version_date": manifest.version_date,
            "minimum_required_version": manifest.minimum_required_version,
            "release_notes": manifest.release_notes,
            "archive": {
                "filename": manifest.archive.filename,
                "size": manifest.archive.size,
                "sha256": manifest.archive.sha256,
            },
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in manifest.files
            ],
            "deletions": [],
        }
    ).encode()


class FakeSignal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


class FakeTransport:
    def __init__(self, manifest=None, error=None):
        self.manifest = manifest
        self.error = error
        self.calls = []

    def download_bytes(self, channel, filename, **kwargs):
        self.calls.append(("bytes", channel, filename, kwargs))
        if self.error:
            raise self.error
        return _manifest_json(self.manifest)


def _checker_app(tmp_path, enabled=True):
    return SimpleNamespace(
        options={"global_version_check": enabled},
        defaults={"global_version_check": enabled},
        version="1.0",
        build_string="build-1",
        data_path=str(tmp_path),
        log=MagicMock(),
        inform=FakeSignal(),
        update_available=FakeSignal(),
        update_check_result=FakeSignal(),
    )


def _startup_app(auto_updates, beta=False):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.headless = False
    app.new_launch = None
    app.file_open_filter = None
    app.log = MagicMock()
    app.cmd_line_shellvar = ""
    app.cmd_line_shellfile = ""
    app.trayIcon = SimpleNamespace(show=MagicMock())
    app.splash = None
    app.ui = MagicMock()
    app.options = {
        "global_systray_icon": False,
        "global_version_check": auto_updates,
    }
    app.beta = beta
    app.version = "1.0"
    app.refresh_rollback_action = MagicMock()
    app._queue_version_check = MagicMock()
    # the update check starts when the files of the command line are open (none here)
    app._open_requests = deque()
    app._open_busy = False
    for name in ("_drain_open_requests", "_start_deferred_version_check"):
        setattr(app, name, types.MethodType(getattr(App, name), app))
    app.defaults = AppDefaults(beta=beta, version="1.0")
    app.inform = FakeSignal()
    return app


def test_startup_update_toggle_gates_automatic_queueing(monkeypatch):
    from appMain import App

    monkeypatch.setattr(App, "args", [])
    monkeypatch.setattr("appMain.QSettings", MagicMock())  # the window state is not read from the real settings
    disabled = _startup_app(False)
    App._setup_startup(disabled)
    disabled._queue_version_check.assert_not_called()

    enabled = _startup_app(True)
    App._setup_startup(enabled)
    enabled._queue_version_check.assert_called_once_with()


def test_startup_queues_updates_for_beta_builds(monkeypatch):
    from appMain import App

    monkeypatch.setattr(App, "args", [])
    monkeypatch.setattr("appMain.QSettings", MagicMock())
    app = _startup_app(True, beta=True)

    App._setup_startup(app)

    app._queue_version_check.assert_called_once_with()


def _staged_update(tmp_path, **overrides):
    from services.updater.github.staging import StagedUpdate

    directory = tmp_path / "staging"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"full release archive")
    manifest = {
        "version": "2.0",
        "build": "build-2",
        "archive": {"filename": archive.name, "size": archive.stat().st_size, "sha256": "a" * 64},
        "files": [],
        "deletions": [],
    }
    values = dict(
        staging_dir=directory,
        archive_path=archive,
        manifest=manifest,
        channel="windows",
        install="windows_asset",
        trust="Update archive verified against the published GitHub SHA-256 digest.",
        dependencies={"changed": False, "added": [], "removed": []},
    )
    values.update(overrides)
    return StagedUpdate(**values)


def test_forced_up_to_date_result_is_reported_to_the_user(tmp_path):
    from services.updater.digi.checker import UpdateChecker

    app = _checker_app(tmp_path)
    transport = FakeTransport(_manifest(version="1.0", build="build-1"))
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    result = checker.run_check(forced=True)

    assert result["status"] == "up_to_date"

    from appMain import App

    app_obj = App.__new__(App)
    app_obj._manual_update_requested = True
    app_obj._check_in_progress = True
    app_obj.inform = app.inform
    App.on_update_check_result(app_obj, result)
    assert "up to date" in app.inform.emissions[-1][0]


@pytest.mark.parametrize("source", [OSError("offline"), ValueError("bad manifest")])
def test_checker_converts_network_and_malformed_failures_to_safe_results(tmp_path, source):
    from services.updater.digi.checker import UpdateChecker

    app = _checker_app(tmp_path)
    transport = FakeTransport(_manifest(), error=source)
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    result = checker.run_check(forced=True)

    assert result["status"] == "error"
    assert result["channel"] == "windows"
    assert result["manifest"] is None
    assert app.inform.emissions


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_download_worker_reports_cancellation_and_resets_state(tmp_path):
    from appMain import App
    from services.updater.common import DownloadCancelled

    app = _fake_app(tmp_path)
    app.update_checker = SimpleNamespace(create_transport=lambda app, repository_url=None: object())
    with patch("appMain.stage_update", side_effect=DownloadCancelled("cancelled")):
        App._download_update_worker(
            app,
            {"status": "update_available"},
            SimpleNamespace(is_set=lambda: False),
        )

    assert app.update_staged.emissions[0][0]["canceled"] is True


def _fake_app(tmp_path):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.data_path = str(tmp_path)
    app.options = {}
    app.defaults = AppDefaults(beta=False, version="1.0")
    app.log = MagicMock()
    app.inform = FakeSignal()
    app.update_progress = FakeSignal()
    app.update_staged = FakeSignal()
    app.rollback_ready = FakeSignal()
    app.worker_task = FakeSignal()
    app.ui = SimpleNamespace(hide=MagicMock())
    app._update_dialog = None
    app._update_progress_dialog = None
    app._update_in_progress = False
    app._update_cancel_event = None
    app._rollback_in_progress = False
    app._shutdown_requested = False
    app.quit_application = MagicMock()
    app._updater_shutdown_preflight = MagicMock(return_value=True)
    return app


def test_update_launches_then_shutdowns_only_after_launcher_ack(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)

    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})

    launch.assert_called_once()
    app.quit_application.assert_called_once_with(mode="updater")


def test_failed_launch_and_cancelled_shutdown_never_quit(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    app.quit_application.assert_not_called()

    app = _fake_app(tmp_path)
    app._updater_shutdown_preflight.return_value = False
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    launch.assert_not_called()
    app.quit_application.assert_not_called()


@pytest.mark.parametrize("active_state", ["checking", "operation"])
def test_main_window_close_is_ignored_while_updater_is_active(active_state):
    from appGUI.MainGUI import MainGUI

    class Event:
        def __init__(self):
            self.ignore_calls = 0

        def ignore(self):
            self.ignore_calls += 1

    app = SimpleNamespace(
        save_in_progress=False,
        _check_in_progress=active_state == "checking",
        _check_queued=False,
        _update_operation_active=lambda: active_state == "operation",
        inform=FakeSignal(),
    )
    gui = MainGUI.__new__(MainGUI)
    gui.app = app
    gui.final_save = SimpleNamespace(emit=MagicMock())
    gui.geometry = MagicMock(return_value=SimpleNamespace(x=lambda: 0, y=lambda: 0, width=lambda: 1, height=lambda: 1))
    gui.lock_action = SimpleNamespace(isChecked=lambda: False)
    gui.show_text_action = SimpleNamespace(isChecked=lambda: False)
    gui.isMaximized = lambda: False
    gui.saveState = lambda *_args: b"state"
    gui.splitter = SimpleNamespace(sizes=lambda: [1])
    event = Event()

    with patch("appGUI.MainGUI.QSettings"):
        gui.closeEvent(event)

    assert event.ignore_calls == 1
    gui.final_save.emit.assert_not_called()
    assert "cancel" in app.inform.emissions[-1][0].lower()
    assert "wait" in app.inform.emissions[-1][0].lower()


def test_rollback_action_requires_valid_restore_point_and_waits_for_ack(monkeypatch, tmp_path):
    from appMain import App, QtWidgets

    class Action:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = value

        def setToolTip(self, value):
            self.tooltip = value

    app = _fake_app(tmp_path)
    app.ui.menuhelp_revert_update = Action()
    app._load_rollback_restore_point = MagicMock(
        return_value=(tmp_path / "restore", {
            "version": "2.0",
            "build_string": "build-2",
            "previous_version": "1.0",
            "previous_build_string": "build-1",
        })
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    App.refresh_rollback_action(app)
    assert app.ui.menuhelp_revert_update.enabled is True

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_rollback", launch)
    App.on_revert_update(app)
    worker = app.worker_task.emissions[0][0]
    worker["fcn"](*worker["params"])
    result = app.rollback_ready.emissions[0][0]
    App.on_rollback_ready(app, result)

    launch.assert_called_once()
    app.quit_application.assert_called_once_with(mode="updater")


def test_download_worker_emits_error_when_staging_fails(tmp_path):
    """Regression: staging failures must stay inside the worker.

    Before the fix transport setup failures escaped unhandled, no
    update_staged was emitted, and _update_in_progress stayed True
    forever (blocking MainGUI.closeEvent).
    """
    from appMain import App
    from services.updater.common import TransportError

    app = _fake_app(tmp_path)
    app.options = {"global_update_url": ""}
    app.update_checker = SimpleNamespace(create_transport=lambda app, repository_url=None: object())

    with patch("appMain.stage_update", side_effect=TransportError("staging failed")):
        # Must not raise — the worker catches staging failures and emits an
        # error payload via update_staged so the caller can reset state.
        App._download_update_worker(
            app,
            {"status": "update_available"},
            SimpleNamespace(is_set=lambda: False),
        )

    assert len(app.update_staged.emissions) == 1
    data = app.update_staged.emissions[0][0]
    assert data["canceled"] is False
    assert "staging failed" in data["error"]

    # Exercise the stuck-state fix: on_update_staged must reset the active
    # flag and close the progress dialog even when the worker failed before
    # downloading anything.
    app._update_in_progress = True
    app._close_update_progress = MagicMock()
    App.on_update_staged(app, data)

    app._close_update_progress.assert_called_once()
    assert app._update_in_progress is False
    app.quit_application.assert_not_called()
    assert any("failed" in emission[0].lower() for emission in app.inform.emissions)
