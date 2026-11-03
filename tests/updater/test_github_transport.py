"""Focused tests for the GitHub Releases transport."""

from __future__ import annotations

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


WINDOWS_ASSET_NAME = windows_asset_name("1.1")
OWNER = "MariusAdrian88"
REPO = "FlatCAM_Evo"
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


def _asset_json(name=WINDOWS_ASSET_NAME, *, size=2048, digest="sha256:" + "a" * 64):
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
    return f"https://{GITHUB_HOST}/{OWNER}/{REPO}/releases/download/v1.1/{WINDOWS_ASSET_NAME}"


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

    offline = urllib.error.URLError("offline")
    with pytest.raises(TransportError):
        GitHubReleaseTransport(opener=FakeOpener([offline])).list_releases()

    invalid_json = FakeResponse(b"<html>not json</html>")
    with pytest.raises(TransportError, match="valid JSON"):
        GitHubReleaseTransport(opener=FakeOpener([invalid_json])).list_releases()

    not_a_list = FakeResponse(b'{"message": "bad"}')
    with pytest.raises(TransportError, match="not a list"):
        GitHubReleaseTransport(opener=FakeOpener([not_a_list])).list_releases()


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

