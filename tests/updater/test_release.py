from __future__ import annotations




# ======================================================================
# merged from tests/updater/test_release_preparation.py
# ======================================================================
"""Focused tests for the local release preparation workflow."""

import hashlib
import os
import zipfile
from pathlib import Path
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


@pytest.mark.parametrize("layout", ("nested_windows",))
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




# ======================================================================
# merged from tests/updater/test_updater_e2e.py
# ======================================================================
"""End-to-end update test: GitHub transport -> checker -> staging -> helper apply/rollback."""


import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import zlib
from types import SimpleNamespace

from services.updater.recovery import (
    WORK_DIR_NAME,
    is_release_blocked,
    previous_release_path,
    read_previous_release,
)
from services.updater.github import checker as checker_module
from services.updater.github import staging as staging_module
from services.updater.github.checker import GitHubUpdateChecker
from services.updater.github.staging import stage_update
from services.updater.github.transport import (
    DELETIONS_ASSET_NAME,
    GITHUB_HOST,
    windows_asset_name,
    GitHubReleaseTransport,
)

ROOT = Path(__file__).resolve().parents[2]
OWNER = "MariusAdrian88"
REPO = "FlatCAM_Evo"
VERSION = "1.2.0"
WINDOWS_ASSET_NAME = windows_asset_name(VERSION)
API_BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"
RELEASES_URL = f"{API_BASE}/releases?per_page=100"
ASSET_URL = (
    f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/v{VERSION}/{WINDOWS_ASSET_NAME}"
)
DELETIONS_URL = (
    f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/"
    f"v{VERSION}/{DELETIONS_ASSET_NAME}"
)


class _Response:
    def __init__(self, body: bytes):
        self._stream = io.BytesIO(body)
        self.status = 200
        self.code = 200
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        pass


class _Opener:
    """Serve fixed bodies for the GitHub URLs used by one scenario."""

    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request.full_url)
        body = self.responses.get(request.full_url)
        if body is None:
            raise AssertionError(f"unexpected GitHub request: {request.full_url}")
        return _Response(body)


def _info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = (0o100644 << 16) if not name.endswith("/") else (0o40755 << 16)
    info.create_system = 3
    return info


def _zip_bytes(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(_info(name), data)
    return buffer.getvalue()


def _release_body(archive: bytes) -> bytes:
    release = {
        "tag_name": f"v{VERSION}",
        "name": f"v{VERSION}",
        "body": "release notes",
        "published_at": "2026-10-01T10:00:00Z",
        "html_url": f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/tag/v{VERSION}",
        "zipball_url": f"{API_BASE}/zipball/v{VERSION}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": WINDOWS_ASSET_NAME,
                "size": len(archive),
                "browser_download_url": ASSET_URL,
                "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
            },
            {
                "name": DELETIONS_ASSET_NAME,
                "size": None,
                "browser_download_url": DELETIONS_URL,
                "digest": None,
            },
        ],
    }
    return json.dumps([release]).encode("utf-8")


def _deletions_body() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "windows": [{"path": "lib/obsolete.pyc", "version": "1.1"}],
            "linux": [],
        }
    ).encode("utf-8")


def _wait_for(path: Path, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _wait_until_missing(path: Path, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return True
        time.sleep(0.05)
    return not path.exists()


def _job_diagnostics(storage: Path) -> str:
    jobs = sorted((storage / "update").glob("flatcam-update-*/update_job.json"))
    if not jobs:
        return "no update job was created"
    log = jobs[0].parent / "updater.log"
    return log.read_text(encoding="utf-8") if log.exists() else f"no log at {log}"


def test_github_transport_checker_stage_launcher_helper_apply_and_rollback(
    monkeypatch, tmp_path
):
    # The Windows release path is simulated on this host: the real GitHub transport,
    # checker, staging, launcher job, and external updater helper code all run, but the
    # process is not the frozen FlatCAM.exe and no Windows file lock is exercised.
    new_exe = b"new frozen executable"
    new_runtime = b"new library.zip runtime"
    new_module = b"new module"
    archive = _zip_bytes(
        [
            ("FlatCAM.exe", new_exe),
            ("lib/library.zip", new_runtime),
            ("lib/new.py", new_module),
        ]
    )

    install = tmp_path / "install"
    (install / "lib").mkdir(parents=True)
    (install / "FlatCAM.exe").write_bytes(b"old frozen executable")
    (install / "lib" / "library.zip").write_bytes(b"old library.zip runtime")
    (install / "lib" / "obsolete.pyc").write_bytes(b"obsolete bytecode")

    release_opener = _Opener({RELEASES_URL: _release_body(archive)})
    app = SimpleNamespace(
        version="1.0",
        options={"global_version_check": True},
        defaults={},
        data_path=tmp_path / "data",
        build_string="source-build",
    )
    monkeypatch.setattr(checker_module, "_is_frozen", lambda: True)
    monkeypatch.setattr(checker_module, "_is_x64", lambda: True)
    monkeypatch.setattr(checker_module, "_is_appimage", lambda: False)

    checker = GitHubUpdateChecker(
        app,
        transport_factory=lambda _app: GitHubReleaseTransport(opener=release_opener),
        platform="win32",
        install_root=install,
    )
    payload = checker.run_check(forced=True)

    assert payload["status"] == "update_available"
    assert payload["channel"] == "windows"
    assert payload["install"] == "windows_asset"
    assert payload["asset"]["name"] == WINDOWS_ASSET_NAME
    assert payload["version"] == VERSION

    monkeypatch.setattr(staging_module, "_is_frozen", lambda: True)
    monkeypatch.setattr(staging_module, "_is_x64", lambda: True)
    monkeypatch.setattr(staging_module, "_is_appimage", lambda: False)
    download_opener = _Opener({ASSET_URL: archive, DELETIONS_URL: _deletions_body()})
    staged = stage_update(
        app,
        payload,
        transport=GitHubReleaseTransport(opener=download_opener),
        staging_root=tmp_path / "staging",
        platform="win32",
        install_root=install,
    )

    files = {entry["path"]: entry for entry in staged.manifest["files"]}
    assert set(files) == {"FlatCAM.exe", "lib/library.zip", "lib/new.py"}
    assert staged.manifest["channel"] == "windows"
    assert [entry["path"] for entry in staged.manifest["deletions"]] == ["lib/obsolete.pyc"]
    assert release_opener.requests == [RELEASES_URL]
    assert download_opener.requests == [ASSET_URL, DELETIONS_URL]
    assert (install / "FlatCAM.exe").read_bytes() == b"old frozen executable"

    storage = tmp_path / "storage"
    storage.mkdir()
    manifest_path = tmp_path / "staged-manifest.json"
    manifest_path.write_text(json.dumps(staged.manifest), encoding="utf-8")
    sentinel = tmp_path / "relaunched.txt"
    sentinel_script = tmp_path / "sentinel.py"
    sentinel_script.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('relaunched', encoding='utf-8')\n",
        encoding="utf-8",
    )
    controller = tmp_path / "launch_controller.py"
    controller.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import services.updater.launcher as launcher\n"
        "install, storage = Path(sys.argv[2]), Path(sys.argv[3])\n"
        "archive, manifest_path, relaunch = (Path(value) for value in sys.argv[4:7])\n"
        "os.environ.pop('PYTHONPATH', None)\n"
        "launcher._install_root = lambda: install\n"
        "app = SimpleNamespace(data_path=storage, version=sys.argv[7], build_string='source-build', options={})\n"
        "manifest = json.loads(manifest_path.read_text(encoding='utf-8'))\n"
        "storage.mkdir(parents=True, exist_ok=True)\n"
        "ok = launcher.launch_update(app, manifest, archive, relaunch_argv=[sys.executable, str(relaunch)],\n"
        "                            revert=sys.argv[8] == 'revert')\n"
        "(storage / 'launch_result.json').write_text(json.dumps({'ok': bool(ok)}), encoding='utf-8')\n"
        "raise SystemExit(0 if ok else 1)\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    def launch(archive_path, manifest_file, app_version, mode):
        return subprocess.run(
            [
                sys.executable,
                str(controller),
                str(ROOT),
                str(install),
                str(storage),
                str(archive_path),
                str(manifest_file),
                str(sentinel_script),
                app_version,
                mode,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    launched = launch(staged.archive_path, manifest_path, "1.0", "update")
    assert launched.returncode == 0, launched.stderr

    assert _wait_for(sentinel), _job_diagnostics(storage)
    assert (install / "FlatCAM.exe").read_bytes() == new_exe
    assert (install / "lib" / "library.zip").read_bytes() == new_runtime
    assert (install / "lib" / "new.py").read_bytes() == new_module
    assert not (install / "lib" / "obsolete.pyc").exists()
    assert json.loads((storage / "launch_result.json").read_text(encoding="utf-8")) == {
        "ok": True
    }

    jobs = sorted((storage / "update").glob("flatcam-update-*/update_job.json"))
    assert len(jobs) == 1
    job = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert job["mode"] == "source"
    assert job["wait_pid"] > 0
    assert "staging_dir" not in job and "revert" not in job
    # the updater leaves no work folder in the installation, and the update recorded the release it replaced
    assert _wait_until_missing(install / WORK_DIR_NAME)
    restore = Path(job["restore_dir"])
    record = read_previous_release(restore, install)
    assert record["previous_version"] == "1.0" and record["version"] == VERSION
    assert record["added"] == ["lib/new.py"]

    # Revert Update: the previous release is installed by a revert job; the added file is deleted
    sentinel.unlink()
    old_files = {
        "FlatCAM.exe": b"old frozen executable",
        "lib/library.zip": b"old library.zip runtime",
        "lib/obsolete.pyc": b"obsolete bytecode",
    }
    old_archive = tmp_path / "old-release.zip"
    old_archive.write_bytes(_zip_bytes(list(old_files.items())))
    revert_manifest = dict(staged.manifest)
    revert_manifest.update(
        version="1.0",
        archive={
            "filename": old_archive.name,
            "size": old_archive.stat().st_size,
            "sha256": hashlib.sha256(old_archive.read_bytes()).hexdigest(),
        },
        files=[
            {"path": name, "size": len(data), "crc32": zlib.crc32(data)} for name, data in old_files.items()
        ],
        deletions=[{"path": "lib/new.py", "version": "1.0"}],
    )
    revert_path = tmp_path / "revert-manifest.json"
    revert_path.write_text(json.dumps(revert_manifest), encoding="utf-8")
    reverted = launch(old_archive, revert_path, VERSION, "revert")
    assert reverted.returncode == 0, reverted.stderr

    assert _wait_for(sentinel), "the revert did not relaunch the application"
    assert _wait_until_missing(install / WORK_DIR_NAME)
    for name, data in old_files.items():
        assert (install / name).read_bytes() == data
    assert not (install / "lib" / "new.py").exists()
    assert is_release_blocked(restore, VERSION, "source-build")
    assert not previous_release_path(restore).exists()
