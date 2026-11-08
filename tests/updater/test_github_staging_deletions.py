"""Focused tests for cumulative deletion assets in GitHub update staging."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

import updater_app
from services.updater.common import DownloadCancelled, TransportError
from services.updater.github import staging as staging_module
from services.updater.github.staging import StagingError, stage_update
from services.updater.github.transport import DELETIONS_ASSET_NAME, GITHUB_HOST, windows_asset_name


OWNER = "MariusAdrian88"
REPO = "FlatCAM_Evo"
VERSION = "1.2.0"
WINDOWS_ASSET_NAME = windows_asset_name(VERSION)
BUILD = f"github:{OWNER.lower()}/{REPO.lower()}:1.2"
WRAPPER = f"{OWNER}-{REPO}-6f1a2b3c"
APP_MAIN = b"class App:\n    version = 1.2\n"


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


def _source_archive(extra=()) -> bytes:
    entries = [
        (f"{WRAPPER}/", b""),
        (f"{WRAPPER}/flatcam.py", b"entry"),
        (f"{WRAPPER}/appMain.py", APP_MAIN),
    ]
    entries.extend((f"{WRAPPER}/{name}", data) for name, data in extra)
    return _zip_bytes(entries)


def _windows_archive(extra=()) -> bytes:
    return _zip_bytes(
        [
            ("FlatCAM.exe", b"new exe"),
            ("lib/library.zip", b"new runtime"),
            *extra,
        ]
    )


def _deletion(path: str, version) -> dict:
    return {"path": path, "version": version}


def _asset(windows=(), linux=(), schema=1) -> dict:
    return {"schema_version": schema, "windows": list(windows), "linux": list(linux)}


def _deletions_advertisement() -> dict:
    return {
        "name": DELETIONS_ASSET_NAME,
        "url": (
            f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/"
            f"v{VERSION}/{DELETIONS_ASSET_NAME}"
        ),
        "size": None,
        "sha256": None,
    }


def _linux_payload(asset=None, *, current_version="1.0", include_asset=True, **overrides) -> dict:
    payload: dict = {
        "status": "update_available",
        "channel": "linux",
        "install": "linux_zipball",
        "version": VERSION,
        "build_string": BUILD,
        "release_notes": "release notes",
        "published_at": "2026-10-01T10:00:00Z",
        "zipball_url": f"https://api.github.com/repos/{OWNER}/{REPO}/zipball/v{VERSION}",
        "current_version": current_version,
    }
    if include_asset:
        payload["deletions_asset"] = _deletions_advertisement() if asset is not None else None
    payload.update(overrides)
    return payload


def _windows_payload(body: bytes, asset=None, *, current_version="1.0", **overrides) -> dict:
    payload = {
        "status": "update_available",
        "channel": "windows",
        "install": "windows_asset",
        "version": VERSION,
        "build_string": BUILD,
        "release_notes": "release notes",
        "published_at": "2026-10-01T10:00:00Z",
        "current_version": current_version,
        "asset": {
            "name": WINDOWS_ASSET_NAME,
            "url": (
                f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/"
                f"v{VERSION}/{WINDOWS_ASSET_NAME}"
            ),
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        },
        "deletions_asset": _deletions_advertisement() if asset is not None else None,
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    """Write ZIP and JSON bodies based on the advertised URL, like the real transport."""

    def __init__(
        self,
        archive: bytes,
        deletions: bytes | None = None,
        *,
        error: Exception | None = None,
        error_on: str | None = None,
        after=None,
    ):
        self.archive = archive
        self.deletions = deletions
        self.error = error
        self.error_on = error_on
        self.after = after
        self.calls = []

    def download_archive(
        self,
        url,
        destination,
        *,
        expected_size=None,
        expected_sha256=None,
        progress_cb=None,
        cancel_cb=None,
    ):
        kind = "json" if url.endswith(DELETIONS_ASSET_NAME) else "archive"
        destination = Path(destination)
        self.calls.append(kind)
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled("cancelled")
        if self.error is not None and self.error_on in (None, kind):
            raise self.error
        body = self.deletions if kind == "json" else self.archive
        if body is None:
            raise TransportError("missing body")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        if progress_cb is not None:
            progress_cb(len(body), expected_size if expected_size is not None else len(body))
        if self.after is not None:
            self.after(kind)
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled("cancelled")
        if expected_size is not None and len(body) != expected_size:
            raise TransportError("size mismatch")
        if expected_sha256 is not None and hashlib.sha256(body).hexdigest() != expected_sha256:
            raise TransportError("digest mismatch")
        return destination


def _stage(
    monkeypatch,
    tmp_path,
    payload,
    archive,
    *,
    deletions_body=None,
    transport=None,
    platform="linux",
    frozen=False,
    x64=True,
    install_root=None,
    **kwargs,
):
    monkeypatch.setattr(staging_module, "_is_frozen", lambda: frozen)
    monkeypatch.setattr(staging_module, "_is_x64", lambda: x64)
    monkeypatch.setattr(staging_module, "_is_appimage", lambda: False)
    root = Path(install_root) if install_root is not None else tmp_path / "install"
    root.mkdir(parents=True, exist_ok=True)
    return stage_update(
        None,
        payload,
        transport=transport if transport is not None else FakeTransport(archive, deletions_body),
        staging_root=tmp_path / "stagebase",
        platform=platform,
        install_root=root,
        **kwargs,
    )


def _assert_no_staging(tmp_path):
    base = tmp_path / "stagebase"
    assert not base.exists() or not list(base.iterdir())


def test_cumulative_deletions_select_only_the_installed_target_range(monkeypatch, tmp_path):
    asset = _asset(
        windows=[_deletion("lib/windows-only.pyc", "1.1")],
        linux=[
            _deletion("lib/old-090.py", "0.9"),
            _deletion("lib/old-100.py", "1.0"),
            _deletion("lib/old-110.py", "1.1"),
            _deletion("lib/old-120.py", "1.2.0"),
            _deletion("lib/old-130.py", "1.3"),
            _deletion("lib/old-1100.py", "1.10"),
        ],
    )
    body = json.dumps(asset).encode()

    result = _stage(
        monkeypatch,
        tmp_path,
        _linux_payload(asset),
        _source_archive(),
        deletions_body=body,
    )

    assert [entry["path"] for entry in result.manifest["deletions"]] == [
        "lib/old-110.py",
        "lib/old-120.py",
    ]
    assert all(not entry["path"].startswith("lib/windows") for entry in result.manifest["deletions"])


@pytest.mark.parametrize(
    "asset_body",
    [pytest.param(b"{", id="bad-json")],
)
def test_invalid_advertised_asset_aborts_and_cleans(monkeypatch, tmp_path, asset_body):
    with pytest.raises(StagingError):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(_asset()),
            _source_archive(),
            deletions_body=asset_body,
        )
    _assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "path",
    ["config/settings.json"],
)
def test_protected_deletion_paths_are_rejected_even_when_out_of_range(monkeypatch, tmp_path, path):
    asset = _asset(windows=[_deletion(path, "0.1")], linux=[_deletion(path, "0.1")])
    with pytest.raises(StagingError, match="unsafe|protected"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(asset),
            _source_archive(),
            deletions_body=json.dumps(asset).encode(),
        )
    _assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "path",
    ["lib/file:stream"],
)
def test_windows_unsafe_deletion_paths_are_validated_on_every_channel(monkeypatch, tmp_path, path):
    asset = _asset(windows=[_deletion(path, "1.1")], linux=[])
    with pytest.raises(StagingError, match="Windows"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(asset),
            _source_archive(),
            deletions_body=json.dumps(asset).encode(),
        )
    _assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [{"name": "other.json"}],
)
def test_malformed_deletion_advertisement_aborts_before_download(monkeypatch, tmp_path, overrides):
    payload = _linux_payload(_asset())
    payload["deletions_asset"].update(overrides)
    transport = FakeTransport(_source_archive(), b"{}")

    with pytest.raises(StagingError):
        _stage(
            monkeypatch,
            tmp_path,
            payload,
            _source_archive(),
            transport=transport,
        )

    assert transport.calls == []
    _assert_no_staging(tmp_path)


def test_windows_deletions_apply_and_rollback_including_absent_target(monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "FlatCAM.exe").write_bytes(b"old exe")
    (install / "lib").mkdir()
    (install / "lib" / "library.zip").write_bytes(b"old runtime")
    (install / "lib" / "obsolete.pyc").write_bytes(b"obsolete")

    asset = _asset(
        windows=[
            _deletion("lib/obsolete.pyc", "1.1"),
            _deletion("lib/already-gone.pyc", "1.1"),
        ],
        linux=[],
    )
    archive = _windows_archive()
    result = _stage(
        monkeypatch,
        tmp_path,
        _windows_payload(archive, asset),
        archive,
        deletions_body=json.dumps(asset).encode(),
        platform="win32",
        frozen=True,
        install_root=install,
    )

    assert [entry["path"] for entry in result.manifest["deletions"]] == [
        "lib/already-gone.pyc",
        "lib/obsolete.pyc",
    ]
    # the downloaded zip is the job archive: the updater extracts it, then moves the files in
    manifest = updater_app._validate_manifest(result.manifest)
    work = install / ".flatcam-update"
    updater_app._extract_archive(result.archive_path, work / "staging", manifest)
    completed = updater_app.apply_update(
        install,
        work / "staging",
        work / "backup",
        [entry["path"] for entry in result.manifest["deletions"]],
        files=manifest["files"],
    )
    assert not (install / "lib" / "obsolete.pyc").exists()
    assert (install / "FlatCAM.exe").read_bytes() == b"new exe"

    assert updater_app.rollback(install, work / "backup", completed)
    assert (install / "lib" / "obsolete.pyc").read_bytes() == b"obsolete"
    assert (install / "FlatCAM.exe").read_bytes() == b"old exe"
