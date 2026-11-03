"""Updater dialog tests: GitHub wiring, pre-manifest UI, staged installs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets



@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Signal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


def _fake_app(tmp_path):
    from appMain import App
    from defaults import AppDefaults as Defaults

    app = App.__new__(App)
    app.data_path = str(tmp_path)
    app.options = {}
    app.defaults = Defaults(beta=False, version="1.0")
    app.log = MagicMock()
    app.inform = Signal()
    app.update_progress = Signal()
    app.update_staged = Signal()
    app.worker_task = Signal()
    app.ui = None
    app._update_dialog = None
    app._update_progress_dialog = None
    app._update_in_progress = False
    app._update_cancel_event = None
    app._rollback_in_progress = False
    app._release_preparation_in_progress = False
    app._manual_update_requested = False
    app._check_in_progress = False
    app._check_queued = False
    app.save_in_progress = False
    app.quit_application = MagicMock()
    app._updater_shutdown_preflight = MagicMock(return_value=True)
    return app


def _payload(**overrides):
    payload = {
        "status": "update_available",
        "channel": "windows",
        "mandatory": False,
        "current": {"version": "1.0", "build": ""},
        "current_version": "1.0",
        "current_build": "",
        "tag": "v1.1",
        "version": "1.1",
        "build_string": "github:mariusadrian88/flatcam_evo:1.1",
        "release_notes": "Plain <b>notes</b>",
        "published_at": "2026-09-17T10:00:00Z",
        "release_page": "https://github.com/MariusAdrian88/FlatCAM_Evo/releases/tag/v1.1",
        "zipball_url": "https://api.github.com/repos/MariusAdrian88/FlatCAM_Evo/zipball/v1.1",
        "asset": {
            "name": "flatcam-evo-windows-x64-update.zip",
            "url": "https://example.invalid/flatcam-evo-windows-x64-update.zip",
            "size": 2048,
            "sha256": "a" * 64,
        },
        "deletions_asset": None,
        "install": "windows_asset",
        "automatic": True,
        "manual": False,
    }
    payload.update(overrides)
    return payload


def _manifest_dict():
    return {
        "schema_version": 1,
        "channel": "windows",
        "version": "1.1",
        "build": "github:mariusadrian88/flatcam_evo:1.1",
        "version_date": "2026-09-17T10:00:00Z",
        "minimum_required_version": "0",
        "release_notes": "notes",
        "archive": {"filename": "flatcam-evo-windows-x64-update.zip", "size": 3, "sha256": "b" * 64},
        "files": [],
        "deletions": [],
    }


def test_dependency_warning_cancels_before_preflight_and_launch(qapp, monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-linux-source.zip"
    archive.write_bytes(b"source")
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    observed = {}

    def cancel_dialog(box):
        observed["format"] = box.textFormat()
        observed["text"] = box.text()
        observed["default"] = box.defaultButton().text()
        return QtWidgets.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", cancel_dialog)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        MagicMock(side_effect=AssertionError("untested static prompt")),
    )

    App.on_update_staged(app, {
        "staging_dir": str(staging),
        "archive_path": str(archive),
        "manifest": _manifest_dict() | {"channel": "linux"},
        "channel": "linux",
        "trust": "Source archive downloaded over HTTPS from GitHub; TLS only, no published digest.",
        "dependencies": {"changed": True, "added": ["newdep<2.0"], "removed": []},
    })

    launch.assert_not_called()
    app._updater_shutdown_preflight.assert_not_called()
    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert observed["format"] == QtCore.Qt.TextFormat.PlainText
    assert "newdep<2.0" in observed["text"]
    assert observed["default"] == "Cancel"


def test_cancellation_after_worker_success_prevents_the_gui_launch(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"archive")
    staged = SimpleNamespace(
        staging_dir=staging,
        archive_path=archive,
        manifest=_manifest_dict(),
        channel="windows",
        install="windows_asset",
        trust="Update archive verified against the published GitHub SHA-256 digest.",
        dependencies={"changed": False, "added": [], "removed": []},
    )
    monkeypatch.setattr("appMain.stage_update", lambda *args, **kwargs: staged)
    app.update_checker = SimpleNamespace(create_transport=lambda app, repository_url=None: object())

    cancel = threading.Event()
    App._download_update_worker(app, {"status": "update_available"}, cancel)
    data = app.update_staged.emissions[0][0]
    assert data["canceled"] is False

    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    app._update_cancel_event = cancel
    app._update_in_progress = True
    cancel.set()

    App.on_update_staged(app, data)

    launch.assert_not_called()
    app._updater_shutdown_preflight.assert_not_called()
    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert any("canceled" in emission[0].lower() for emission in app.inform.emissions)



def test_failed_helper_stays_running_and_removes_the_stage(qapp, monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "exec",
        lambda _box: QtWidgets.QMessageBox.StandardButton.Yes,
    )

    App.on_update_staged(app, {
        "staging_dir": str(staging),
        "archive_path": str(archive),
        "manifest": _manifest_dict(),
        "channel": "windows",
        "trust": "Update archive verified against the published GitHub SHA-256 digest.",
        "dependencies": {"changed": False, "added": [], "removed": []},
    })

    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert any("updater could not be started" in emission[0].lower() for emission in app.inform.emissions)


def _launcher_fixture(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("flatcam.py", b"new entry")
    data = archive.read_bytes()
    manifest = {
        "schema_version": 1,
        "channel": "windows",
        "version": "2.0",
        "build": "build-2",
        "version_date": "2026-09-17",
        "minimum_required_version": "0",
        "release_notes": "",
        "archive": {
            "filename": archive.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "files": [{"path": "flatcam.py", "size": 9, "sha256": hashlib.sha256(b"new entry").hexdigest()}],
        "deletions": [],
    }
    return staging, archive, manifest


def test_launcher_owns_a_verified_archive_copy_before_stage_cleanup(monkeypatch, tmp_path):
    import services.updater.launcher as launcher_module
    import updater_app
    from services.updater.launcher import launch_update

    staging, archive, manifest = _launcher_fixture(tmp_path)
    app = SimpleNamespace(data_path=str(tmp_path / "data"), log=MagicMock())
    captured = {}

    def fake_popen(argv, **kwargs):
        job = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        captured["job"] = job
        Path(job["startup_ack_path"]).write_text("started", encoding="utf-8")

    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)

    assert launch_update(app, manifest, archive, relaunch_argv=["python", "flatcam.py"])

    job_archive = Path(captured["job"]["archive_path"])
    assert job_archive != archive
    assert job_archive.read_bytes() == archive.read_bytes()
    shutil.rmtree(staging)
    assert not archive.exists()
    updater_app._validate_archive(job_archive, manifest["archive"])

