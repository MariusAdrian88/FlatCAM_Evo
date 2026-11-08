"""End-to-end update test: GitHub transport -> checker -> staging -> helper apply/rollback."""

from __future__ import annotations

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
