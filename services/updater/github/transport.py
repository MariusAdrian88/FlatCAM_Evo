"""GitHub Releases transport for the FlatCAM updater."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..common import DownloadCancelled, TransportError, update_archive_name
from ..manifest import parse_version


DEFAULT_REPOSITORY = "https://github.com/MariusAdrian88/FlatCAM_Evo"
GITHUB_HOST = "github.com"
API_HOST = "api.github.com"
PER_PAGE = 100
MAX_PAGES = 100
DELETIONS_ASSET_NAME = "flatcam-evo-update-deletions.json"
_WINDOWS_ASSET_RE = re.compile(r"^flatcam-evo-(\d+(?:\.\d+)*)-win64-update\.zip$")
_TAG_RE = re.compile(r"^v?\d+(?:\.\d+)*$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

_log = logging.getLogger(__name__)


def canonical_version(version: str) -> str:
    """Return the numeric version used in release build identities."""
    parts = list(parse_version(version))
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def windows_asset_name(version) -> str:
    """Return the name of the Windows update asset of a release, made by make_freeze.py from App.version."""
    return update_archive_name(version, "win64")


def is_windows_asset_name(name, version) -> bool:
    """True if name is the Windows update asset of the release version. The version in the name comes from
    App.version of the build and the tag can write it in another form, so the 2 are compared as numbers: the tag
    v1.1.0 takes flatcam-evo-1.1-win64-update.zip."""
    match = _WINDOWS_ASSET_RE.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        return False
    try:
        return canonical_version(match.group(1)) == canonical_version(str(version))
    except ValueError:
        return False


def normalize_repository(repository: str) -> tuple[str, str]:
    """Return the validated owner/name pair for a GitHub repository URL or slug."""
    value = str(repository or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname != GITHUB_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise TransportError("GitHub repository must be an HTTPS github.com URL.")
        value = parsed.path.strip("/")
    if not _SLUG_RE.fullmatch(value):
        raise TransportError("GitHub repository must be an owner/name slug or URL.")
    owner, repo = value.split("/", 1)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise TransportError("GitHub repository must be an owner/name slug or URL.")
    return owner, repo


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class GitHubRelease:
    tag: str
    version: str
    canonical_version: str
    build_string: str
    name: str
    body: str
    published_at: str
    html_url: str
    zipball_url: str
    assets: tuple[ReleaseAsset, ...] = ()

    def asset(self, name: str) -> ReleaseAsset | None:
        """Return the named asset metadata, or None when the release lacks it."""
        for asset in self.assets:
            if asset.name == name:
                return asset
        return None

    def windows_asset(self) -> ReleaseAsset | None:
        """Return the Windows update asset whose version is the version of this release, or None."""
        for asset in self.assets:
            if is_windows_asset_name(asset.name, self.version):
                return asset
        return None

    def package_asset(self, platform: str, arch: str, suffix: str) -> ReleaseAsset | None:
        """Return the package that release-packages.yml attached for one system, or None. The workflow names it
        FlatCAM_EVO-<tag>-<platform>-<arch><suffix>, for example FlatCAM_EVO-v1.1-linux-x86_64.AppImage."""
        ending = f"-{platform}-{arch}{suffix}".lower()
        for asset in self.assets:
            if asset.name.startswith("FlatCAM_EVO-") and asset.name.lower().endswith(ending):
                return asset
        return None


class GitHubReleaseTransport:
    """List stable releases and download release assets from GitHub without credentials."""

    def __init__(
        self,
        repository: str = DEFAULT_REPOSITORY,
        *,
        opener=None,
        timeout: float = 30.0,
        chunk_size: int = 1024 * 1024,
        api_host: str = API_HOST,
    ) -> None:
        self.owner, self.repo = normalize_repository(repository)
        self.repository = f"{self.owner}/{self.repo}"
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.api_host = api_host.lower()

    @staticmethod
    def _close(response) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _status(response) -> int:
        return int(getattr(response, "status", getattr(response, "code", 200)) or 200)

    @staticmethod
    def _http_error(code: int, headers=None) -> TransportError:
        if code == 404:
            return TransportError("The GitHub repository is not accessible (HTTP 404).")
        remaining = headers.get("X-RateLimit-Remaining") if headers is not None else None
        if code == 429 or (code == 403 and str(remaining).strip() == "0"):
            return TransportError(f"GitHub rate limit reached (HTTP {code}).")
        if code == 403:
            return TransportError("GitHub denied access (HTTP 403).")
        return TransportError(f"GitHub request returned HTTP {code}.")

    def _open(self, url: str, headers=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise TransportError("GitHub transport requires HTTPS URLs.")
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            response = self._opener(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc.code, getattr(exc, "headers", None)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"GitHub request failed: {type(exc).__name__}.") from exc
        status = self._status(response)
        if status >= 400:
            self._close(response)
            raise self._http_error(status, getattr(response, "headers", None))
        return response

    @staticmethod
    def _read_json(response):
        try:
            return json.loads(response.read())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError("GitHub response was not valid JSON.") from exc

    @staticmethod
    def _link_next(response) -> str | None:
        headers = getattr(response, "headers", None)
        header = headers.get("Link") if headers is not None else None
        if not header:
            return None
        for part in str(header).split(","):
            pieces = part.split(";")
            if len(pieces) >= 2 and pieces[1].strip() == 'rel="next"':
                return pieces[0].strip().strip("<>")
        return None

    def _releases_url(self) -> str:
        return f"https://{self.api_host}/repos/{self.owner}/{self.repo}/releases?per_page={PER_PAGE}"

    def _validate_pagination(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        expected_path = f"/repos/{self.owner}/{self.repo}/releases"
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.api_host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path.rstrip("/") != expected_path
            or "page" not in urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        ):
            raise TransportError("GitHub pagination did not stay on the repository releases route.")
        return url

    def _release(self, item) -> GitHubRelease | None:
        if not isinstance(item, dict):
            raise TransportError("GitHub releases response contained an invalid entry.")
        if item.get("draft") is True or item.get("prerelease") is True:
            return None
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
            _log.debug("Ignoring GitHub release with malformed tag: %r", tag)
            return None
        try:
            return self._build_release(item, tag)
        except TransportError as exc:
            _log.warning("Ignoring malformed GitHub release %r: %s", tag, exc)
            return None

    def _build_release(self, item, tag: str) -> GitHubRelease:
        html_url = item.get("html_url")
        if not isinstance(html_url, str) or not html_url.startswith(f"https://{GITHUB_HOST}/"):
            raise TransportError("GitHub release metadata has an invalid release URL.")
        zipball_url = item.get("zipball_url")
        zipball_prefix = f"https://{self.api_host}/repos/{self.owner}/{self.repo}/zipball/"
        if not isinstance(zipball_url, str) or not zipball_url.startswith(zipball_prefix):
            raise TransportError("GitHub release metadata has an invalid source archive URL.")
        published_at = item.get("published_at")
        if published_at is not None and not isinstance(published_at, str):
            raise TransportError("GitHub release metadata has an invalid published timestamp.")
        assets = item.get("assets", [])
        if not isinstance(assets, list):
            raise TransportError("GitHub release metadata has an invalid asset list.")
        version = tag[1:] if tag.startswith("v") else tag
        canonical = canonical_version(version)
        name = item.get("name")
        body = item.get("body")
        return GitHubRelease(
            tag=tag,
            version=version,
            canonical_version=canonical,
            build_string=f"github:{self.repository.lower()}:{canonical}",
            name=name if isinstance(name, str) else "",
            body=body if isinstance(body, str) else "",
            published_at=published_at or "",
            html_url=html_url,
            zipball_url=zipball_url,
            assets=tuple(self._asset(raw) for raw in assets),
        )

    @staticmethod
    def _asset(raw) -> ReleaseAsset:
        if not isinstance(raw, dict):
            raise TransportError("GitHub release asset metadata was invalid.")
        name = raw.get("name")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise TransportError("GitHub release asset has an invalid name.")
        url = raw.get("browser_download_url")
        if not isinstance(url, str) or not url.startswith(f"https://{GITHUB_HOST}/"):
            raise TransportError("GitHub release asset has an invalid download URL.")
        size = raw.get("size")
        if size is not None and (type(size) is not int or size < 0):
            raise TransportError("GitHub release asset has an invalid size.")
        digest = raw.get("digest")
        sha256 = None
        if digest is not None:
            if not isinstance(digest, str):
                raise TransportError("GitHub release asset has an invalid digest.")
            match = _DIGEST_RE.fullmatch(digest)
            if match:
                sha256 = match.group(1).lower()
            elif digest.startswith("sha256:"):
                raise TransportError("GitHub release asset has an invalid sha256 digest.")
        return ReleaseAsset(name=name, url=url, size=size, sha256=sha256)

    def list_releases(self) -> tuple[GitHubRelease, ...]:
        """List every published stable release across all release pages."""
        url = self._releases_url()
        visited: set[str] = set()
        releases: list[GitHubRelease] = []
        for _ in range(MAX_PAGES):
            if url in visited:
                raise TransportError("GitHub pagination did not advance.")
            visited.add(url)
            response = self._open(url, headers={"Accept": "application/vnd.github+json"})
            try:
                data = self._read_json(response)
                next_url = self._link_next(response)
            finally:
                self._close(response)
            if not isinstance(data, list):
                raise TransportError("GitHub releases response was not a list.")
            for item in data:
                release = self._release(item)
                if release is not None:
                    releases.append(release)
            if not next_url:
                return tuple(releases)
            url = self._validate_pagination(next_url)
        raise TransportError("GitHub releases pagination exceeded the page limit.")

    def _download_url(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(str(url or ""))
        release_prefix = f"/{self.owner}/{self.repo}/releases/download/"
        zipball_prefix = f"/repos/{self.owner}/{self.repo}/zipball/"
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise TransportError("GitHub asset URL must be an HTTPS github.com URL.")
        if parsed.hostname == GITHUB_HOST and parsed.path.startswith(release_prefix):
            return parsed.geturl()
        if parsed.hostname == self.api_host and parsed.path.startswith(zipball_prefix):
            return parsed.geturl()
        raise TransportError("GitHub asset URL does not belong to the repository releases.")

    @staticmethod
    def _response_total(response, expected_size=None) -> int | None:
        total = expected_size
        if total is None:
            header = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            try:
                total = int(header) if header is not None else None
            except (TypeError, ValueError):
                total = None
        return total

    def _stream_response(
        self,
        response,
        destination: Path,
        expected_size=None,
        expected_sha256=None,
        progress_cb=None,
        cancel_cb=None,
    ) -> None:
        total = self._response_total(response, expected_size)
        digest = hashlib.sha256()
        written = 0
        with Path(destination).open("wb") as output:
            while True:
                if cancel_cb is not None and cancel_cb():
                    raise DownloadCancelled("GitHub download cancelled.")
                chunk = response.read(self.chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress_cb is not None:
                    progress_cb(written, total)
            output.flush()
            os.fsync(output.fileno())
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled("GitHub download cancelled.")
        if expected_size is not None and written != expected_size:
            raise TransportError("GitHub download size did not match the expected size.")
        if expected_sha256 is not None and digest.hexdigest() != str(expected_sha256).lower():
            raise TransportError("GitHub download SHA-256 did not match the expected digest.")

    def download_archive(
        self,
        url: str,
        destination: Path,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        progress_cb=None,
        cancel_cb=None,
    ) -> Path:
        """Stream a release asset to a temporary file and atomically replace destination."""
        url = self._download_url(url)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{destination}.part")
        temporary.unlink(missing_ok=True)
        response = None
        try:
            response = self._open(url)
            self._stream_response(
                response,
                temporary,
                expected_size,
                expected_sha256,
                progress_cb,
                cancel_cb,
            )
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if response is not None:
                self._close(response)
        return destination
