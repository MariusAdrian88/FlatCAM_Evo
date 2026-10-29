"""Focused tests for GitHub update staging, validation, and normalization."""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest

from services.updater.archive import build_deterministic_zip
from services.updater.common import DownloadCancelled, TransportError
from services.updater.github import staging as staging_module
from services.updater.github.staging import (
    SOURCE_ARCHIVE_NAME,
    StagedUpdate,
    StagingError,
    stage_update,
    validate_windows_relative_path,
)
from services.updater.github.transport import GITHUB_HOST, windows_asset_name
from services.updater.manifest import ManifestError, parse_manifest, sha256_file


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
    assert result.staging_dir == result.archive_path.parent
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
    [("win32", False, True, False, "windows-source"), ("win32", True, False, False, "unsupported-architecture"), ("linux", False, True, True, "appimage")],
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
    ["../evil.py", "/absolute.py", "a/../b.py"],
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


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
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
    with zipfile.ZipFile(result.archive_path) as archive:
        members = archive.namelist()
    assert "flatcam.py" in members
    assert "config/settings.json" not in members


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
    [(b"class App:\n    version = 1.3\n", "does not match"), (b"class App:\n    version = getattr(object, 'v')\n", "literal"), (b"class Other:\n    version = 1.2\n", "does not declare")],
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


def test_requirement_changes_are_reported_for_gui_confirmation(monkeypatch, tmp_path):
    root = _install_root(tmp_path, "PyQt6==6.5\n")
    same = _stage(
        monkeypatch,
        tmp_path,
        _linux_payload(),
        _zip_bytes(_source_entries()),
        platform="linux",
        frozen=False,
        install_root=root,
    )
    assert same.dependencies == {"changed": False, "added": [], "removed": []}

    changed = _stage(
        monkeypatch,
        tmp_path / "second",
        _linux_payload(),
        _zip_bytes(
            _source_entries(requirements=b"PyQt6==6.6\nnumpy==1.26\nshapely==2.0\n")
        ),
        platform="linux",
        frozen=False,
        install_root=root,
    )
    assert changed.dependencies == {
        "changed": True,
        "added": ["PyQt6==6.6", "numpy==1.26", "shapely==2.0"],
        "removed": ["PyQt6==6.5"],
    }


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
    ["lib/file:stream", "AUX", "trailing."],
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
    [{"name": "other.zip"}, {"sha256": "nothex"}, {"size": "100"}, {"size": -1}],
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

