"""Focused tests for the local release preparation workflow."""

import hashlib
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from services.updater.digi import release as release_module
from services.updater.manifest import ManifestError, parse_manifest
from services.updater.digi.release import prepare_releases


class Signal:
    def __init__(self):
        self.emissions = []
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, *args):
        if args:
            self._slots.remove(args[0])
        else:
            self._slots.clear()

    def emit(self, *args):
        self.emissions.append(args)
        for slot in tuple(self._slots):
            slot(*args)


class Progress:
    def __init__(self, *args):
        self.canceled = Signal()
        self.closed = False
        self.visible = False

    def setWindowTitle(self, _title):
        pass

    def setWindowModality(self, _modality):
        pass

    def setAutoClose(self, _value):
        pass

    def setAutoReset(self, _value):
        pass

    def setCancelButton(self, _button):
        pass

    def show(self):
        self.visible = True

    def close(self):
        self.closed = True
        self.visible = False

    def isVisible(self):
        return self.visible


@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_source(root: Path) -> Path:
    (root / "services" / "updater").mkdir(parents=True)
    (root / "flatcam.py").write_text("print('source')", encoding="utf-8")
    (root / "updater_app.py").write_text("print('updater')", encoding="utf-8")
    (root / "services" / "updater" / "manifest.py").write_text("manifest", encoding="utf-8")
    return root


def _make_windows(root: Path) -> Path:
    (root / "updater").mkdir(parents=True)
    (root / "FlatCAM.exe").write_bytes(b"frozen-build")
    (root / "updater" / "FlatCAMUpdater.exe").write_bytes(b"stable-updater")
    (root / "payload.py").write_text("payload", encoding="utf-8")
    return root


def _workflow_app(source_root: Path):
    from appMain import App

    app = App.__new__(App)
    app.ui = object()
    app.app_home = str(source_root)
    app.version = "3.4.5"
    app.version_date = "2026/5/01"
    app.log = MagicMock()
    app.inform = Signal()
    app.worker_task = Signal()
    app.update_progress = Signal()
    app.release_prepared = Signal()
    app._update_progress_dialog = None
    app._update_dialog = None
    app._release_preparation_in_progress = False
    app._update_in_progress = False
    app._rollback_in_progress = False
    return app


def _patch_dialogs(monkeypatch, windows_root, output_root, notes=("", True), answer=None):
    directories = iter((str(windows_root), str(output_root)))
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: next(directories),
    )
    monkeypatch.setattr(QtWidgets.QInputDialog, "getMultiLineText", lambda *args, **kwargs: notes)
    if answer is not None:
        monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: answer)


def test_prepare_workflow_rejects_incomplete_inputs_before_worker_dispatch(monkeypatch, tmp_path):
    from appMain import App

    source = tmp_path / "invalid-source"
    source.mkdir()
    windows = tmp_path / "invalid-windows"
    windows.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    app = _workflow_app(source)
    _patch_dialogs(
        monkeypatch,
        windows,
        output,
        notes=("notes", True),
        answer=QtWidgets.QMessageBox.StandardButton.Yes,
    )

    App.prepare_update_files(app)

    assert app.worker_task.emissions == []
    assert app._release_preparation_in_progress is False
    assert "required" in app.inform.emissions[-1][0].lower()


def test_release_core_failure_leaves_no_partial_final_pair(monkeypatch, tmp_path):
    windows = _make_windows(tmp_path / "windows")
    linux = _make_source(tmp_path / "linux")
    output = tmp_path / "output"
    calls = []
    original_zip = release_module.build_deterministic_zip

    def fail_linux(root, destination, policy=release_module.DEFAULT_EXCLUSION_POLICY):
        if Path(root) == linux.resolve():
            raise OSError("linux archive failed")
        calls.append(destination)
        return original_zip(root, destination, policy)

    monkeypatch.setattr(release_module, "build_deterministic_zip", fail_linux)

    with pytest.raises(OSError, match="linux archive failed"):
        prepare_releases(
            {"windows": windows, "linux": linux},
            output,
            version="3.4.5",
            build_string="2026/5/01",
            version_date="2026/5/01",
        )

    assert calls
    assert not list(output.rglob("*.zip"))
    assert not (output / "windows" / "manifest.json").exists()
    assert not (output / "linux" / "manifest.json").exists()
    assert not list(output.rglob("*.part"))


@pytest.mark.parametrize("layout", ("equal_windows", "nested_windows", "nested_linux", "roots_inside_output"))
def test_release_rejects_overlapping_output_before_staging_and_preserves_files(
    monkeypatch, tmp_path, layout
):
    if layout == "roots_inside_output":
        output = tmp_path / "publish"
        output.mkdir()
        windows = _make_windows(output / "windows")
        linux = _make_source(output / "linux")
    else:
        windows = _make_windows(tmp_path / "windows")
        linux = _make_source(tmp_path / "linux")
        if layout == "equal_windows":
            output = windows
        elif layout == "nested_windows":
            output = windows / "publish"
            output.mkdir()
        else:
            output = linux / "publish"
            output.mkdir()

    sentinels = []
    for root in (windows, linux):
        sentinel = root / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        sentinels.append(sentinel)
    output_sentinel = output / "existing.txt"
    output_sentinel.write_text("existing", encoding="utf-8")

    staging_calls = []
    archive_calls = []
    original_mkdtemp = release_module.tempfile.mkdtemp

    def record_staging(*args, **kwargs):
        path = Path(original_mkdtemp(*args, **kwargs))
        staging_calls.append(path)
        return str(path)

    def unexpected_archive(*args, **kwargs):
        archive_calls.append(args)
        raise AssertionError("archive generation must not run")

    monkeypatch.setattr(release_module.tempfile, "mkdtemp", record_staging)
    monkeypatch.setattr(release_module, "build_deterministic_zip", unexpected_archive)

    with pytest.raises(Exception) as error:
        prepare_releases(
            {"windows": windows, "linux": linux},
            output,
            version="3.4.5",
            build_string="2026/5/01",
            version_date="2026/5/01",
        )

    assert isinstance(error.value, ManifestError)
    assert not staging_calls
    assert not archive_calls
    assert all(path.read_text(encoding="utf-8") == "keep" for path in sentinels)
    assert output_sentinel.read_text(encoding="utf-8") == "existing"


def test_end_to_end_preparation_has_four_verified_platform_upload_files(monkeypatch, tmp_path):
    windows = _make_windows(tmp_path / "windows")
    linux = _make_source(tmp_path / "linux")
    for root in (windows, linux):
        (root / "tests").mkdir()
        (root / "tests" / "test_not_payload.py").write_text("no", encoding="utf-8")
        (root / "docs").mkdir()
        (root / "docs" / "README.md").write_text("no", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("no", encoding="utf-8")
        (root / ".venv").mkdir()
        (root / ".venv" / "pyvenv.cfg").write_text("no", encoding="utf-8")
        (root / "user_config").mkdir()
        (root / "user_config" / "settings.json").write_text("no", encoding="utf-8")
        (root / "make_freeze.py").write_text("no", encoding="utf-8")
    output = tmp_path / "upload"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network call"))

    preparation = prepare_releases(
        {"windows": windows, "linux": linux},
        output,
        version="3.4.5",
        build_string="2026/5/01",
        version_date="2026/5/01",
        minimum_required_version="0",
        release_notes="Ticket 5",
    )

    # the archives have the version in their names; the client finds them through manifest.json
    expected_paths = (
        output / "windows" / "flatcam-evo-3.4.5-win64-update.zip",
        output / "windows" / "manifest.json",
        output / "linux" / "flatcam-evo-3.4.5-source-update.zip",
        output / "linux" / "manifest.json",
    )
    assert preparation.upload_paths == expected_paths
    for channel, archive_name in (("windows", "flatcam-evo-3.4.5-win64-update.zip"),
                                  ("linux", "flatcam-evo-3.4.5-source-update.zip")):
        manifest_path = output / channel / "manifest.json"
        manifest = parse_manifest(manifest_path.read_bytes())
        archive_path = output / channel / manifest.archive.filename
        assert manifest.channel == channel
        assert manifest.version == "3.4.5"
        assert manifest.build_string == "2026/5/01"
        assert manifest.version_date == "2026/5/01"
        assert manifest.minimum_required_version == "0"
        assert manifest.release_notes == "Ticket 5"
        assert manifest.archive.filename == archive_name
        archive_bytes = archive_path.read_bytes()
        assert manifest.archive.size == len(archive_bytes)
        assert manifest.archive.sha256 == hashlib.sha256(archive_bytes).hexdigest()
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        assert "make_freeze.py" not in names
        assert not any(name.startswith(("tests/", "docs/", ".git/", ".venv/", "user_config/")) for name in names)
        assert names == {entry.path for entry in manifest.files}


def test_successful_result_resets_state_reports_paths_and_opens_output(monkeypatch, tmp_path):
    from appMain import App

    source = _make_source(tmp_path / "source")
    app = _workflow_app(source)
    progress = Progress()
    progress.show()
    app._update_progress_dialog = progress
    app._release_preparation_in_progress = True
    paths = tuple(
        tmp_path / name
        for name in (
            "windows/manifest.json",
            "windows/flatcam-windows.zip",
            "linux/manifest.json",
            "linux/flatcam-source.zip",
        )
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"file")
    opened = []
    monkeypatch.setattr(
        "appMain.QtGui.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )

    App.on_release_prepared(
        app,
        {"success": True, "output_dir": str(tmp_path), "upload_paths": [str(path) for path in paths]},
    )

    assert app._release_preparation_in_progress is False
    assert progress.closed is True
    assert all(str(path) in app.inform.emissions[-1][0] for path in paths)
    assert opened
