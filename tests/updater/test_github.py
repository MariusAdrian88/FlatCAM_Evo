from __future__ import annotations




# ======================================================================
# merged from tests/updater/test_github_staging.py
# ======================================================================
"""Focused tests for GitHub update staging, validation, and normalization."""


import hashlib
import io
import stat
import zipfile
import zlib
from pathlib import Path

import pytest

from services.updater.common import DownloadCancelled, TransportError
from services.updater.github import staging as staging_module
from services.updater.github.staging import (
    SOURCE_ARCHIVE_NAME,
    StagedUpdate,
    StagingError,
    stage_update,
)
from services.updater.github.transport import GITHUB_HOST, windows_asset_name
from services.updater.manifest import parse_manifest, sha256_file


OWNER = "MariusAdrian88"
REPO = "FlatCAM_Evo"
VERSION = "1.2.0"
WINDOWS_ASSET_NAME = windows_asset_name(VERSION)
BUILD = f"github:{OWNER.lower()}/{REPO.lower()}:1.2"
WRAPPER = f"{OWNER}-{REPO}-6f1a2b3c"
APP_MAIN = b"class App:\n    version = 1.2\n"


def _info(name, kind="file", mode=0o644):
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    if kind == "dir":
        info.external_attr = (stat.S_IFDIR | (mode & 0o777)) << 16
    elif kind == "file":
        info.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
    elif kind == "symlink":
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
    else:
        file_type = {
            "fifo": stat.S_IFIFO,
            "chr": stat.S_IFCHR,
            "blk": stat.S_IFBLK,
            "sock": stat.S_IFSOCK,
        }[kind]
        info.external_attr = (file_type | (mode & 0o777)) << 16
    info.create_system = 3
    return info


def _zip_bytes(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(info, data)
    return buffer.getvalue()


def _source_entries(prefix=WRAPPER + "/", app_main=APP_MAIN, requirements: bytes | None = b"PyQt6==6.5\n", extra=()):
    entries = [
        (_info(prefix + "flatcam.py", mode=0o755), b"entry"),
        (_info(prefix + "appMain.py"), app_main),
    ]
    if requirements is not None:
        entries.append((_info(prefix + "requirements.txt"), requirements))
    entries.append((_info(prefix + "docs/readme.md"), b"excluded"))
    if prefix:
        entries.insert(0, (_info(prefix, kind="dir"), b""))
    entries.extend(extra)
    return entries


def _windows_entries(extra=()):
    entries = [
        (_info("FlatCAM.exe", mode=0o755), b"exe"),
        (_info("lib/", kind="dir"), b""),
        (_info("lib/library.zip"), b"runtime"),
        (_info("flatcam.py"), b"payload"),
        (_info("docs/manual.md"), b"excluded"),
    ]
    entries.extend(extra)
    return entries


def _linux_payload(**overrides):
    payload = {
        "status": "update_available",
        "channel": "linux",
        "install": "linux_zipball",
        "version": VERSION,
        "build_string": BUILD,
        "release_notes": "release notes",
        "published_at": "2026-10-01T10:00:00Z",
        "zipball_url": f"https://api.github.com/repos/{OWNER}/{REPO}/zipball/v{VERSION}",
    }
    payload.update(overrides)
    return payload


def _windows_payload(body, *, digest=True, **overrides):
    payload = {
        "status": "update_available",
        "channel": "windows",
        "install": "windows_asset",
        "version": VERSION,
        "build_string": BUILD,
        "release_notes": "release notes",
        "published_at": "2026-10-01T10:00:00Z",
        "asset": {
            "name": WINDOWS_ASSET_NAME,
            "url": f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/v{VERSION}/{WINDOWS_ASSET_NAME}",
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest() if digest else None,
        },
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    def __init__(self, body=b"", error=None):
        self.body = body
        self.error = error
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
        destination = Path(destination)
        self.calls.append(
            {
                "url": url,
                "destination": destination,
                "expected_size": expected_size,
                "expected_sha256": expected_sha256,
            }
        )
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled("cancelled")
        if self.error is not None:
            raise self.error
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.body)
        if progress_cb is not None:
            progress_cb(len(self.body), expected_size)
        return destination


def _force_platform(monkeypatch, *, frozen, x64=True, appimage=False):
    monkeypatch.setattr(staging_module, "_is_frozen", lambda: frozen)
    monkeypatch.setattr(staging_module, "_is_x64", lambda: x64)
    monkeypatch.setattr(staging_module, "_is_appimage", lambda: appimage)


def _install_root(tmp_path, requirements="PyQt6==6.5\n") -> Path:
    root = tmp_path / "install"
    root.mkdir(parents=True, exist_ok=True)
    (root / "requirements.txt").write_text(requirements, encoding="utf-8")
    return root


def _stage(
    monkeypatch,
    tmp_path,
    payload,
    body,
    *,
    platform,
    frozen,
    x64=True,
    appimage=False,
    install_root=None,
    transport=None,
    **kwargs,
):
    _force_platform(monkeypatch, frozen=frozen, x64=x64, appimage=appimage)
    root = install_root if install_root is not None else _install_root(tmp_path)
    return stage_update(
        None,
        payload,
        transport=transport if transport is not None else FakeTransport(body),
        staging_root=tmp_path / "stagebase",
        platform=platform,
        install_root=root,
        **kwargs,
    )


def test_linux_staging_returns_full_manifest_with_release_identity(monkeypatch, tmp_path):
    body = _zip_bytes(_source_entries())
    transport = FakeTransport(body)
    result = _stage(
        monkeypatch,
        tmp_path,
        _linux_payload(),
        body,
        platform="linux",
        frozen=False,
        transport=transport,
    )

    assert isinstance(result, StagedUpdate)
    assert result.channel == "linux"
    assert result.install == "linux_zipball"
    assert result.staging_dir.is_dir()
    assert result.archive_path.parent.parent == result.staging_dir
    assert result.archive_path.is_file()
    assert not result.staging_dir.is_relative_to(tmp_path / "install")

    manifest = parse_manifest(result.manifest)
    assert manifest.version == VERSION
    assert manifest.build_string == BUILD
    assert manifest.channel == "linux"
    assert manifest.minimum_required_version == "0"
    assert manifest.version_date == "2026-10-01T10:00:00Z"
    assert manifest.release_notes == "release notes"
    assert "mandatory" not in result.manifest
    assert manifest.archive.filename == SOURCE_ARCHIVE_NAME
    assert manifest.archive.size == result.archive_path.stat().st_size
    assert manifest.archive.sha256 == sha256_file(result.archive_path)
    # the downloaded zip is the job archive; the manifest names its wrapper folder and each file's size and CRC-32
    assert result.archive_path.read_bytes() == body
    assert manifest.archive_prefix == WRAPPER
    flatcam = next(entry for entry in manifest.files if entry.path == "flatcam.py")
    assert (flatcam.size, flatcam.crc32) == (len(b"entry"), zlib.crc32(b"entry"))

    paths = [entry.path for entry in manifest.files]
    assert "flatcam.py" in paths
    assert "appMain.py" in paths
    assert "requirements.txt" in paths
    assert "docs/readme.md" not in paths
    assert not any(path.startswith(WRAPPER) for path in paths)
    assert result.trust

    assert transport.calls[0]["expected_size"] is None
    assert transport.calls[0]["expected_sha256"] is None
    assert transport.calls[0]["url"] == _linux_payload()["zipball_url"]


def test_windows_staging_verifies_asset_digest_and_reports_trust(monkeypatch, tmp_path):
    body = _zip_bytes(_windows_entries())
    transport = FakeTransport(body)
    result = _stage(
        monkeypatch,
        tmp_path,
        _windows_payload(body),
        body,
        platform="win32",
        frozen=True,
        x64=True,
        transport=transport,
    )

    assert result.channel == "windows"
    assert result.install == "windows_asset"
    assert transport.calls[0]["expected_size"] == len(body)
    assert transport.calls[0]["expected_sha256"] == hashlib.sha256(body).hexdigest()

    manifest = parse_manifest(result.manifest)
    assert manifest.channel == "windows"
    paths = [entry.path for entry in manifest.files]
    assert "FlatCAM.exe" in paths
    assert "lib/library.zip" in paths
    assert "docs/manual.md" not in paths
    assert "SHA-256" in result.trust


@pytest.mark.parametrize(
    "platform, frozen, x64, appimage, reason",
    [("win32", False, True, False, "windows-source"), ("linux", False, True, True, "appimage")],
)
def test_install_matrix_refuses_unsupported_targets_before_download(
    monkeypatch, tmp_path, platform, frozen, x64, appimage, reason
):
    transport = FakeTransport(b"")
    with pytest.raises(StagingError, match=reason):
        _stage(
            monkeypatch,
            tmp_path,
            {},
            b"",
            platform=platform,
            frozen=frozen,
            x64=x64,
            appimage=appimage,
            transport=transport,
        )

    assert transport.calls == []
    assert not (tmp_path / "stagebase").exists()


def test_payload_install_and_channel_must_match_the_local_machine(monkeypatch, tmp_path):
    transport = FakeTransport(b"")
    linux_root = _install_root(tmp_path)

    with pytest.raises(StagingError, match="channel"):
        _stage(
            monkeypatch,
            tmp_path,
            _windows_payload(b""),
            b"",
            platform="linux",
            frozen=False,
            install_root=linux_root,
            transport=transport,
        )
    with pytest.raises(StagingError, match="install mode"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(install="manual"),
            b"",
            platform="linux",
            frozen=False,
            install_root=linux_root,
            transport=transport,
        )
    windows_root = _install_root(tmp_path / "windows")
    with pytest.raises(StagingError, match="channel"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            b"",
            platform="win32",
            frozen=True,
            install_root=windows_root,
            transport=transport,
        )
    assert transport.calls == []


def test_staging_directory_must_stay_outside_the_installation(monkeypatch, tmp_path):
    root = _install_root(tmp_path)
    transport = FakeTransport(b"")
    _force_platform(monkeypatch, frozen=False)
    with pytest.raises(StagingError, match="outside the installation"):
        stage_update(
            None,
            _linux_payload(),
            transport=transport,
            staging_root=root,
            platform="linux",
            install_root=root,
        )
    assert transport.calls == []


@pytest.mark.parametrize(
    "name",
    ["../evil.py", "/absolute.py"],
)
def test_unsafe_zip_member_paths_are_rejected_even_when_excluded(monkeypatch, tmp_path, name):
    entries = _source_entries(extra=[(_info(WRAPPER + "/" + name), b"evil")])
    with pytest.raises(StagingError, match="unsafe|backslashes|invalid name|drive prefix"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            _zip_bytes(entries),
            platform="linux",
            frozen=False,
        )

    assert list((tmp_path / "stagebase").iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink"])
def test_zip_symlinks_and_special_members_are_rejected(monkeypatch, tmp_path, kind):
    entries = _source_entries(extra=[(_info(WRAPPER + "/special", kind=kind), b"target")])
    with pytest.raises(StagingError, match="symbolic link|unsupported file type"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            _zip_bytes(entries),
            platform="linux",
            frozen=False,
        )


def test_protected_and_excluded_members_never_reach_the_payload(monkeypatch, tmp_path):
    extra = [
        (_info(WRAPPER + "/config/settings.json"), b"config"),
        (_info(WRAPPER + "/updater/FlatCAMUpdater.exe"), b"updater"),
        (_info(WRAPPER + "/.git/config"), b"git"),
        (_info(WRAPPER + "/tests/test_x.py"), b"test"),
        (_info(WRAPPER + "/user_data/project.flatprj"), b"data"),
        (_info(WRAPPER + "/cache/data.bin"), b"cache"),
    ]
    result = _stage(
        monkeypatch,
        tmp_path,
        _linux_payload(),
        _zip_bytes(_source_entries(extra=extra)),
        platform="linux",
        frozen=False,
    )

    paths = [entry.path for entry in parse_manifest(result.manifest).files]
    # the updater runtime is managed: the launcher runs it from a copy, so the apply step can replace it
    assert "updater/FlatCAMUpdater.exe" in paths
    for forbidden in (
        "config/settings.json",
        ".git/config",
        "tests/test_x.py",
        "user_data/project.flatprj",
        "cache/data.bin",
    ):
        assert forbidden not in paths
    assert "flatcam.py" in paths


def test_source_archive_requires_exactly_one_enclosing_directory(monkeypatch, tmp_path):
    no_wrapper = _zip_bytes(_source_entries(prefix=""))
    with pytest.raises(StagingError, match="enclosing directory"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            no_wrapper,
            platform="linux",
            frozen=False,
        )

    two_roots = _zip_bytes(
        [
            (_info("alpha/flatcam.py"), b"entry"),
            (_info("beta/appMain.py"), APP_MAIN),
        ]
    )
    with pytest.raises(StagingError, match="enclosing directory"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            two_roots,
            platform="linux",
            frozen=False,
        )


def test_windows_archive_requires_root_flatcam_executable_and_lib_package(monkeypatch, tmp_path):
    wrapped = _zip_bytes(
        [
            (_info("pkg/", kind="dir"), b""),
            (_info("pkg/FlatCAM.exe"), b"exe"),
            (_info("pkg/lib/", kind="dir"), b""),
            (_info("pkg/lib/library.zip"), b"runtime"),
        ]
    )
    with pytest.raises(StagingError, match="expected"):
        _stage(
            monkeypatch,
            tmp_path,
            _windows_payload(wrapped),
            wrapped,
            platform="win32",
            frozen=True,
            x64=True,
        )

    missing_lib = _zip_bytes([(_info("FlatCAM.exe"), b"exe")])
    with pytest.raises(StagingError, match="lib/library.zip"):
        _stage(
            monkeypatch,
            tmp_path,
            _windows_payload(missing_lib),
            missing_lib,
            platform="win32",
            frozen=True,
            x64=True,
        )


def test_non_zip_and_empty_archives_are_rejected(monkeypatch, tmp_path):
    with pytest.raises(StagingError, match="valid ZIP"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            b"not a zip",
            platform="linux",
            frozen=False,
        )

    empty = _zip_bytes([])
    with pytest.raises(StagingError, match="empty"):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            empty,
            platform="linux",
            frozen=False,
        )


@pytest.mark.parametrize(
    "app_main, error",
    [(b"class App:\n    version = 1.3\n", "does not match")],
)
def test_source_app_version_must_match_release_numerically(monkeypatch, tmp_path, app_main, error):
    entries = _source_entries(app_main=app_main)
    with pytest.raises(StagingError, match=error):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            _zip_bytes(entries),
            platform="linux",
            frozen=False,
        )


def test_cancellation_and_failure_clean_up_the_staging_directory(monkeypatch, tmp_path):
    body = _zip_bytes(_source_entries())
    with pytest.raises(DownloadCancelled):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            body,
            platform="linux",
            frozen=False,
            cancel_cb=lambda: True,
        )
    assert list((tmp_path / "stagebase").iterdir()) == []

    with pytest.raises(TransportError):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            body,
            platform="linux",
            frozen=False,
            transport=FakeTransport(error=TransportError("offline")),
        )
    assert list((tmp_path / "stagebase").iterdir()) == []

    state = {"cancel": False}
    with pytest.raises(DownloadCancelled):
        _stage(
            monkeypatch,
            tmp_path,
            _linux_payload(),
            body,
            platform="linux",
            frozen=False,
            cancel_cb=lambda: state["cancel"],
            progress_cb=lambda written, total: state.__setitem__("cancel", True),
        )
    assert list((tmp_path / "stagebase").iterdir()) == []


@pytest.mark.parametrize(
    "name",
    ["lib/file:stream"],
)
def test_windows_archives_reject_windows_incompatible_components(monkeypatch, tmp_path, name):
    entries = _windows_entries(extra=[(_info(name), b"x")])
    with pytest.raises(StagingError, match="not valid on Windows"):
        _stage(
            monkeypatch,
            tmp_path,
            _windows_payload(_zip_bytes(entries)),
            _zip_bytes(entries),
            platform="win32",
            frozen=True,
            x64=True,
        )


@pytest.mark.parametrize(
    "asset_overrides",
    [{"name": "other.zip"}, {"sha256": "nothex"}],
)
def test_windows_payload_metadata_is_strict_before_download(
    monkeypatch, tmp_path, asset_overrides
):
    body = _zip_bytes(_windows_entries())
    payload = _windows_payload(body)
    payload["asset"].update(asset_overrides)
    transport = FakeTransport(body)
    with pytest.raises(StagingError):
        _stage(
            monkeypatch,
            tmp_path,
            payload,
            body,
            platform="win32",
            frozen=True,
            x64=True,
            transport=transport,
        )
    assert transport.calls == []
    assert not (tmp_path / "stagebase").exists()


# ======================================================================
# merged from tests/updater/test_github_staging_deletions.py
# ======================================================================
"""Focused tests for cumulative deletion assets in GitHub update staging."""


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




def _info_m2(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = (0o100644 << 16) if not name.endswith("/") else (0o40755 << 16)
    info.create_system = 3
    return info


def _zip_bytes_m2(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(_info_m2(name), data)
    return buffer.getvalue()


def _source_archive(extra=()) -> bytes:
    entries = [
        (f"{WRAPPER}/", b""),
        (f"{WRAPPER}/flatcam.py", b"entry"),
        (f"{WRAPPER}/appMain.py", APP_MAIN),
    ]
    entries.extend((f"{WRAPPER}/{name}", data) for name, data in extra)
    return _zip_bytes_m2(entries)


def _windows_archive(extra=()) -> bytes:
    return _zip_bytes_m2(
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


def _linux_payload_m2(asset=None, *, current_version="1.0", include_asset=True, **overrides) -> dict:
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


def _windows_payload_m2(body: bytes, asset=None, *, current_version="1.0", **overrides) -> dict:
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


class FakeTransport_m2:
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


def _stage_m2(
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
        transport=transport if transport is not None else FakeTransport_m2(archive, deletions_body),
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

    result = _stage_m2(
        monkeypatch,
        tmp_path,
        _linux_payload_m2(asset),
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
        _stage_m2(
            monkeypatch,
            tmp_path,
            _linux_payload_m2(_asset()),
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
        _stage_m2(
            monkeypatch,
            tmp_path,
            _linux_payload_m2(asset),
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
        _stage_m2(
            monkeypatch,
            tmp_path,
            _linux_payload_m2(asset),
            _source_archive(),
            deletions_body=json.dumps(asset).encode(),
        )
    _assert_no_staging(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [{"name": "other.json"}],
)
def test_malformed_deletion_advertisement_aborts_before_download(monkeypatch, tmp_path, overrides):
    payload = _linux_payload_m2(_asset())
    payload["deletions_asset"].update(overrides)
    transport = FakeTransport_m2(_source_archive(), b"{}")

    with pytest.raises(StagingError):
        _stage_m2(
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
    result = _stage_m2(
        monkeypatch,
        tmp_path,
        _windows_payload_m2(archive, asset),
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


# ======================================================================
# merged from tests/updater/test_github_transport.py
# ======================================================================
"""Focused tests for the GitHub Releases transport."""


import datetime
import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from services.updater.digi.transport import DownloadCancelled
from services.updater.github.transport import (
    API_HOST,
    DEFAULT_REPOSITORY,
    GITHUB_HOST,
    windows_asset_name,
    GitHubReleaseTransport,
    TransportError,
    canonical_version,
    normalize_repository,
)


WINDOWS_ASSET_NAME_m3 = windows_asset_name("1.1")
API_BASE = f"https://{API_HOST}/repos/{OWNER}/{REPO}"
RELEASES_URL = f"{API_BASE}/releases?per_page=100"


class FakeResponse:
    def __init__(self, body=b"", *, status=200, headers=None, url=""):
        self._stream = io.BytesIO(body)
        self.status = status
        self.code = status
        self.headers = dict(headers or {})
        self.url = url
        self.closed = False

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self.url

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _release_json(tag, *, draft=False, prerelease=False, assets=(), zipball=None):
    return {
        "tag_name": tag,
        "name": tag,
        "body": "release notes",
        "published_at": "2026-09-17T10:00:00Z",
        "html_url": f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/tag/{tag}",
        "zipball_url": zipball or f"https://{API_HOST}/repos/{OWNER}/{REPO}/zipball/{tag}",
        "draft": draft,
        "prerelease": prerelease,
        "assets": list(assets),
    }


def _asset_json(name=WINDOWS_ASSET_NAME_m3, *, size=2048, digest="sha256:" + "a" * 64):
    asset = {
        "name": name,
        "size": size,
        "browser_download_url": f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/v1.1/{name}",
    }
    if digest is not None:
        asset["digest"] = digest
    return asset


def _releases_response(releases, *, next_url=None):
    headers = {}
    if next_url:
        headers["Link"] = f'<{next_url}>; rel="next"'
    return FakeResponse(json.dumps(releases).encode("utf-8"), headers=headers)


def _archive_url():
    return f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/v1.1/{WINDOWS_ASSET_NAME_m3}"


def test_repository_urls_are_validated():
    assert normalize_repository(DEFAULT_REPOSITORY) == (OWNER, REPO)
    assert normalize_repository("owner/repo") == ("owner", "repo")
    assert GitHubReleaseTransport().repository == f"{OWNER}/{REPO}"
    for bad in (
        "http://github.com/owner/repo",
        "https://example.com/owner/repo",
        "https://github.com/owner",
        "https://github.com/owner/repo/extra",
        "https://user:secret@github.com/owner/repo",
        "https://github.com:8443/owner/repo",
    ):
        with pytest.raises(TransportError):
            GitHubReleaseTransport(bad)


def test_listing_uses_repo_releases_route_with_per_page_100_and_follows_next():
    page_two = f"{API_BASE}/releases?per_page=100&page=2"
    opener = FakeOpener(
        [
            _releases_response([_release_json("v1.2.0")], next_url=page_two),
            _releases_response([_release_json("v1.1")]),
        ]
    )
    releases = GitHubReleaseTransport(opener=opener).list_releases()

    assert [request.full_url for request in opener.requests] == [RELEASES_URL, page_two]
    assert [release.tag for release in releases] == ["v1.2.0", "v1.1"]
    assert opener.requests[0].get_header("Accept") == "application/vnd.github+json"


def test_drafts_prereleases_and_malformed_tags_are_ignored():
    entries = [
        _release_json("v9.0", draft=True),
        _release_json("v8.0", prerelease=True),
        _release_json("v1.0.0-beta"),
        _release_json("V7.0"),
        _release_json("release-1.0"),
        _release_json("1.0.0+build"),
        _release_json("1.0.0"),
        _release_json("v2.0"),
    ]
    releases = GitHubReleaseTransport(opener=FakeOpener([_releases_response(entries)])).list_releases()

    assert [release.tag for release in releases] == ["1.0.0", "v2.0"]
    assert releases[0].version == "1.0.0"
    assert releases[0].canonical_version == "1"
    assert releases[0].build_string == f"github:{OWNER.lower()}/{REPO.lower()}:1"
    assert canonical_version("1") == "1"
    assert canonical_version("1.0") == "1"
    assert canonical_version("1.0.0") == "1"
    assert canonical_version("1.2.0.0") == "1.2"


def test_http_rate_limit_network_and_json_errors_fail_safely():
    not_found = urllib.error.HTTPError(RELEASES_URL, 404, "Not Found", {}, None)
    with pytest.raises(TransportError, match="not accessible"):
        GitHubReleaseTransport(opener=FakeOpener([not_found])).list_releases()

    rate_limited = urllib.error.HTTPError(RELEASES_URL, 429, "Too Many Requests", {}, None)
    with pytest.raises(TransportError, match="rate limit"):
        GitHubReleaseTransport(opener=FakeOpener([rate_limited])).list_releases()

    # the message tells the local time when GitHub resets the limit
    reset = 1794330433
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}
    exhausted = urllib.error.HTTPError(RELEASES_URL, 403, "Forbidden", headers, None)
    expected = datetime.datetime.fromtimestamp(reset).strftime("%H:%M")
    with pytest.raises(TransportError, match=f"rate limit.*after {expected}"):
        GitHubReleaseTransport(opener=FakeOpener([exhausted])).list_releases()

    offline = urllib.error.URLError("offline")
    with pytest.raises(TransportError):
        GitHubReleaseTransport(opener=FakeOpener([offline])).list_releases()

    invalid_json = FakeResponse(b"<html>not json</html>")
    with pytest.raises(TransportError, match="valid JSON"):
        GitHubReleaseTransport(opener=FakeOpener([invalid_json])).list_releases()

    not_a_list = FakeResponse(b'{"message": "bad"}')
    with pytest.raises(TransportError, match="not a list"):
        GitHubReleaseTransport(opener=FakeOpener([not_a_list])).list_releases()


def test_release_list_cache_saves_requests_for_its_lifetime(tmp_path, monkeypatch):
    cache = tmp_path / "github_releases_cache.json"
    clock = [1000.0]
    monkeypatch.setattr("services.updater.github.transport.time.time", lambda: clock[0])

    def transport(responses, seconds=600, repository=DEFAULT_REPOSITORY):
        opener = FakeOpener(responses)
        return GitHubReleaseTransport(repository, opener=opener, cache_path=cache, cache_seconds=seconds), opener

    first, opener = transport([_releases_response([_release_json("v1.1")])])
    assert [r.tag for r in first.list_releases()] == ["v1.1"]
    assert len(opener.requests) == 1

    # an app restart inside the lifetime does not ask GitHub
    clock[0] += 599
    again, opener = transport([])
    assert [r.tag for r in again.list_releases()] == ["v1.1"]
    assert opener.requests == []

    # a forced check (lifetime 0) asks GitHub and refreshes the cache
    forced, opener = transport([_releases_response([_release_json("v1.2")])], seconds=0)
    assert [r.tag for r in forced.list_releases()] == ["v1.2"]
    assert len(opener.requests) == 1

    # another repository does not use the cached list
    other, opener = transport([_releases_response([])], repository="owner/repo")
    assert other.list_releases() == ()
    assert len(opener.requests) == 1

    # after the lifetime the transport asks GitHub again
    clock[0] += 601
    late, opener = transport([_releases_response([_release_json("v1.3")])])
    assert [r.tag for r in late.list_releases()] == ["v1.3"]
    assert len(opener.requests) == 1

    # a corrupt cache file is ignored
    cache.write_text("{not json", encoding="utf-8")
    broken, opener = transport([_releases_response([_release_json("v1.4")])])
    assert [r.tag for r in broken.list_releases()] == ["v1.4"]


def _malformed_entries():
    asset = _asset_json()
    return {
        "bad_digest": _release_json("v1.1", assets=[_asset_json(digest="sha256:nothex")]),
        "bad_asset_url": _release_json("v1.1", assets=[{**asset, "browser_download_url": "http://example.com/x.zip"}]),
        "bad_zipball": _release_json("v1.1", zipball="https://example.com/zip"),
    }


@pytest.mark.parametrize("key", sorted(_malformed_entries()))
def test_one_malformed_release_is_skipped_not_fatal(key):
    entries = [_malformed_entries()[key], _release_json("v2.0")]
    releases = GitHubReleaseTransport(opener=FakeOpener([_releases_response(entries)])).list_releases()

    assert [release.tag for release in releases] == ["v2.0"]


def test_download_archive_streams_verifies_and_replaces(tmp_path):
    body = b"release archive bytes"
    digest = hashlib.sha256(body).hexdigest()
    opener = FakeOpener([FakeResponse(body, headers={"Content-Length": str(len(body))})])
    transport = GitHubReleaseTransport(opener=opener, chunk_size=4)
    destination = tmp_path / "staging" / "update.zip"
    progress = []

    result = transport.download_archive(
        _archive_url(),
        destination,
        expected_size=len(body),
        expected_sha256=digest,
        progress_cb=lambda written, total: progress.append((written, total)),
    )

    assert result == destination
    assert destination.read_bytes() == body
    assert progress[-1] == (len(body), len(body))
    assert not Path(f"{destination}.part").exists()


def test_download_archive_cancellation_and_digest_failure_clean_up(tmp_path):
    body = b"0123456789"
    destination = tmp_path / "update.zip"

    cancelled = {"value": False}

    def progress(written, total):
        cancelled["value"] = True

    transport = GitHubReleaseTransport(opener=FakeOpener([FakeResponse(body)]), chunk_size=4)
    with pytest.raises(DownloadCancelled):
        transport.download_archive(_archive_url(), destination, progress_cb=progress, cancel_cb=lambda: cancelled["value"])
    assert not destination.exists()
    assert not Path(f"{destination}.part").exists()

    transport = GitHubReleaseTransport(opener=FakeOpener([FakeResponse(body)]), chunk_size=4)
    with pytest.raises(TransportError):
        transport.download_archive(_archive_url(), destination, expected_sha256="0" * 64)
    assert not destination.exists()
    assert not Path(f"{destination}.part").exists()


def test_download_urls_must_belong_to_the_repository(tmp_path):
    transport = GitHubReleaseTransport()
    destination = tmp_path / "update.zip"
    for bad in (
        "http://github.com/MariusAdrian88/FlatCAM_Evo/releases/download/v1/f.zip",
        "https://github.com/other/repo/releases/download/v1/f.zip",
        "https://example.com/f.zip",
    ):
        with pytest.raises(TransportError):
            transport.download_archive(bad, destination)
    assert not destination.exists()


# ======================================================================
# merged from tests/updater/test_github_checker.py
# ======================================================================
"""Focused tests for the GitHub release update checker."""


import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.updater.github import checker as checker_module
from services.updater.github.checker import GitHubUpdateChecker, select_release
from services.updater.github.transport import (
    API_HOST,
    GITHUB_HOST,
    GitHubRelease,
    GitHubReleaseTransport,
    ReleaseAsset,
    TransportError,
    canonical_version,
    windows_asset_name,
)
from services.updater.recovery import blocked_release_path, restore_dir_for_install




class FakeTransport_m4:
    def __init__(self, releases=(), error=None):
        self.releases = tuple(releases)
        self.error = error
        self.calls = 0

    def list_releases(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.releases


class Signal:
    def __init__(self):
        self.payloads = []

    def emit(self, payload):
        self.payloads.append(payload)


def _release(tag, *, assets=()):
    version = tag[1:] if tag.startswith("v") else tag
    canonical = canonical_version(version)
    return GitHubRelease(
        tag=tag,
        version=version,
        canonical_version=canonical,
        build_string=f"github:{OWNER.lower()}/{REPO.lower()}:{canonical}",
        name=tag,
        body="release notes",
        published_at="2026-09-17T10:00:00Z",
        html_url=f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/tag/{tag}",
        zipball_url=f"https://{API_HOST}/repos/{OWNER}/{REPO}/zipball/{tag}",
        assets=tuple(assets),
    )


def _windows_asset(version="1.1", tag="v1.1"):
    name = windows_asset_name(version)
    return ReleaseAsset(
        name=name,
        url=f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/{tag}/{name}",
        size=2048,
        sha256="a" * 64,
    )


def _app(tmp_path, **overrides):
    app = SimpleNamespace(
        version=1.0,
        os="win32",
        options={"global_version_check": True},
        defaults={},
        data_path=str(tmp_path / "data"),
        inform=Signal(),
        update_check_result=Signal(),
        update_available=Signal(),
        build_string="",
    )
    app.__dict__.update(overrides)
    return app


def _checker(app, transport, install_root, *, platform="win32"):
    return GitHubUpdateChecker(
        app,
        transport_factory=lambda _app: transport,
        platform=platform,
        install_root=install_root,
    )


def _freeze(monkeypatch, *, frozen, appimage=False, machine="AMD64"):
    monkeypatch.setattr(checker_module, "_is_frozen", lambda: frozen)
    monkeypatch.setattr(checker_module, "_is_x64", lambda: machine.lower() in ("amd64", "x86_64", "x64"))
    if appimage:
        monkeypatch.setenv("APPIMAGE", "/opt/flatcam.AppImage")
    else:
        monkeypatch.delenv("APPIMAGE", raising=False)
        monkeypatch.delenv("APPDIR", raising=False)


def test_highest_numeric_release_wins_and_trailing_zeros_are_equal(monkeypatch, tmp_path):
    assert select_release([_release("v1.9.9"), _release("1.10"), _release("v1.2.0")]).tag == "1.10"
    assert select_release([_release("v1.0.0"), _release("1.0")]).tag == "v1.0.0"
    assert select_release([]) is None

    _freeze(monkeypatch, frozen=True)
    app = _app(tmp_path, version=1.0)
    payload = _checker(app, FakeTransport_m4([_release("v1.0.0")]), tmp_path).run_check(app)
    assert payload["status"] == "up_to_date"
    assert payload["current_version"] == "1.0"


def test_windows_frozen_x64_selects_the_exact_update_asset(monkeypatch, tmp_path):
    # the version in the asset name is App.version of the build; it must be the version of the release tag
    _freeze(monkeypatch, frozen=True)
    app = _app(tmp_path, version=1.0)
    assets = [_windows_asset("1.0", "v1.1.0"), _windows_asset("1.1", "v1.1.0")]
    payload = _checker(app, FakeTransport_m4([_release("v1.1.0", assets=assets)]), tmp_path).run_check(app)

    assert payload["status"] == "update_available"
    assert payload["channel"] == "windows"
    assert payload["tag"] == "v1.1.0"
    assert payload["version"] == "1.1.0"
    assert payload["build_string"] == f"github:{OWNER.lower()}/{REPO.lower()}:1.1"
    assert payload["mandatory"] is False
    assert payload["automatic"] is True
    assert payload["manual"] is False
    assert payload["install"] == "windows_asset"
    assert payload["asset"] == {
        "name": "flatcam-evo-1.1-win64-update.zip",
        "url": _windows_asset("1.1", "v1.1.0").url,
        "size": 2048,
        "sha256": "a" * 64,
    }
    assert payload["release_notes"] == "release notes"
    assert payload["published_at"] == "2026-09-17T10:00:00Z"
    assert payload["release_page"] == f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/tag/v1.1.0"
    assert payload["zipball_url"].endswith("/zipball/v1.1.0")
    assert "manifest" not in payload
    assert len(app.update_available.payloads) == 1
    assert len(app.update_check_result.payloads) == 1


def test_missing_windows_asset_is_an_error_without_fallback(monkeypatch, tmp_path):
    _freeze(monkeypatch, frozen=True)
    app = _app(tmp_path, version=1.0)
    # the newest release has only the archive of another version: it is not used
    transport = FakeTransport_m4([_release("v1.1", assets=[_windows_asset("1.0")]),
                               _release("v1.0", assets=[_windows_asset("1.0", "v1.0")])])
    payload = _checker(app, transport, tmp_path).run_check(app)

    assert payload["status"] == "error"
    assert "asset" in payload["error"]
    assert "install" not in payload
    assert app.update_available.payloads == []


@pytest.mark.parametrize(
    "platform, frozen, machine, appimage, reason",
    [
        ("darwin", False, "ARM64", False, "macos"),
        ("win32", False, "AMD64", False, "windows-source"),
        ("linux", False, "x86_64", True, "appimage"),
    ],
)
def test_unsupported_installs_report_manual_replacement_without_intrusion(
    monkeypatch, tmp_path, platform, frozen, machine, appimage, reason
):
    _freeze(monkeypatch, frozen=frozen, appimage=appimage, machine=machine)
    install = tmp_path / "install"
    install.mkdir()
    app = _app(tmp_path, os=platform, version=1.0)
    payload = _checker(
        app,
        FakeTransport_m4([_release("v1.1", assets=[_windows_asset()])]),
        install,
        platform=platform,
    ).run_check(app)

    assert payload["status"] == "update_available"
    assert payload["channel"] == ("windows" if platform == "win32" else "linux" if platform == "linux" else "unsupported")
    assert payload["automatic"] is False
    assert payload["manual"] is True
    assert payload["install"] == "manual"
    assert payload["manual_reason"] == reason
    assert payload["release_page"].startswith(f"https://{GITHUB_HOST}/")


@pytest.mark.parametrize("machine", ["x86_64"])
def test_linux_source_install_uses_the_release_zipball(monkeypatch, tmp_path, machine):
    _freeze(monkeypatch, frozen=False, machine=machine)
    install = tmp_path / "install"
    install.mkdir()
    app = _app(tmp_path, os="linux", version=1.0)
    payload = _checker(app, FakeTransport_m4([_release("v1.1")]), install, platform="linux").run_check(app)

    assert payload["automatic"] is True
    assert payload["install"] == "linux_zipball"
    assert payload["zipball_url"] == f"https://{API_HOST}/repos/{OWNER}/{REPO}/zipball/v1.1"
    assert "manual_reason" not in payload


def test_blocked_release_is_matched_across_equivalent_numeric_tags(tmp_path):
    app = _app(tmp_path, version=0.9)
    install = tmp_path / "install"
    install.mkdir()
    marker = blocked_release_path(restore_dir_for_install(app.data_path, install))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"version": "1.0.0", "build_string": f"github:{OWNER.lower()}/{REPO.lower()}:1"}),
        encoding="utf-8",
    )

    blocked = _checker(app, FakeTransport_m4([_release("v1.0")]), install).run_check(app)
    assert blocked["status"] == "blocked"

    newer = _checker(app, FakeTransport_m4([_release("v1.1")]), install).run_check(app)
    assert newer["status"] == "update_available"


def test_transport_failures_become_safe_error_payloads(tmp_path):
    app = _app(tmp_path)
    transport = FakeTransport_m4(error=TransportError("The GitHub repository is not accessible (HTTP 404)."))
    payload = _checker(app, transport, tmp_path).run_check(app)

    assert payload["status"] == "error"
    assert "404" in payload["error"]
    assert app.update_check_result.payloads == [payload]
    # the status bar message tells the reason, not only that the check failed
    assert "404" in app.inform.payloads[-1]

    malformed = _app(tmp_path)
    payload = _checker(malformed, FakeTransport_m4(error=ValueError("bad release metadata")), tmp_path).run_check(malformed)
    assert payload["status"] == "error"
    assert payload["error"] == "bad release metadata"
