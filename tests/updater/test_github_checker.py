"""Focused tests for the GitHub release update checker."""

from __future__ import annotations

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


OWNER = "MariusAdrian88"
REPO = "FlatCAM_Evo"


class FakeTransport:
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
    payload = _checker(app, FakeTransport([_release("v1.0.0")]), tmp_path).run_check(app)
    assert payload["status"] == "up_to_date"
    assert payload["current_version"] == "1.0"


def test_windows_frozen_x64_selects_the_exact_update_asset(monkeypatch, tmp_path):
    # the version in the asset name is App.version of the build; it must be the version of the release tag
    _freeze(monkeypatch, frozen=True)
    app = _app(tmp_path, version=1.0)
    assets = [_windows_asset("1.0", "v1.1.0"), _windows_asset("1.1", "v1.1.0")]
    payload = _checker(app, FakeTransport([_release("v1.1.0", assets=assets)]), tmp_path).run_check(app)

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
    transport = FakeTransport([_release("v1.1", assets=[_windows_asset("1.0")]),
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
        FakeTransport([_release("v1.1", assets=[_windows_asset()])]),
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
    payload = _checker(app, FakeTransport([_release("v1.1")]), install, platform="linux").run_check(app)

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

    blocked = _checker(app, FakeTransport([_release("v1.0")]), install).run_check(app)
    assert blocked["status"] == "blocked"

    newer = _checker(app, FakeTransport([_release("v1.1")]), install).run_check(app)
    assert newer["status"] == "update_available"


def test_transport_failures_become_safe_error_payloads(tmp_path):
    app = _app(tmp_path)
    transport = FakeTransport(error=TransportError("The GitHub repository is not accessible (HTTP 404)."))
    payload = _checker(app, transport, tmp_path).run_check(app)

    assert payload["status"] == "error"
    assert "404" in payload["error"]
    assert app.update_check_result.payloads == [payload]
    # the status bar message tells the reason, not only that the check failed
    assert "404" in app.inform.payloads[-1]

    malformed = _app(tmp_path)
    payload = _checker(malformed, FakeTransport(error=ValueError("bad release metadata")), tmp_path).run_check(malformed)
    assert payload["status"] == "error"
    assert payload["error"] == "bad release metadata"

