"""Background GitHub release update checks for FlatCAM."""

from __future__ import annotations

import json
import os
import platform as platform_module
import sys
import traceback
from pathlib import Path

from ..common import (
    app_log as _app_log,
    emit as _emit,
    inform as _inform,
    install_root as _install_root,
    local_build as _local_build,
)
from ..manifest import ManifestError, is_newer, parse_version
from ..recovery import blocked_release_path, is_release_blocked, restore_dir_for_install
from .transport import (
    DEFAULT_REPOSITORY,
    DELETIONS_ASSET_NAME,
    GitHubReleaseTransport,
    TransportError,
    canonical_version,
)


def _platform_name(value) -> str:
    """Normalize a platform identifier to windows, linux, macos, or other."""
    name = str(value or "").lower()
    if name.startswith("win"):
        return "windows"
    if name.startswith("linux"):
        return "linux"
    if name.startswith("darwin") or name.startswith("mac"):
        return "macos"
    return "other"


def _is_frozen() -> bool:
    """Return whether the application runs from a frozen build."""
    return bool(getattr(sys, "frozen", False))


def _is_x64() -> bool:
    """Return whether this is a 64-bit process on a 64-bit x86 target."""
    if sys.maxsize <= 2**32:
        return False
    return platform_module.machine().lower() in ("amd64", "x86_64", "x64")


def _is_appimage() -> bool:
    """Return whether the process runs from an AppImage bundle."""
    return bool(os.environ.get("APPIMAGE") or os.environ.get("APPDIR"))


def _numeric(value) -> tuple[int, ...]:
    """Return comparable version parts with trailing zeros removed."""
    return parse_version(canonical_version(value))


def select_release(releases):
    """Return the stable release with the highest numeric version, if any."""
    best = None
    best_parts: tuple[int, ...] = ()
    for release in releases:
        try:
            parts = _numeric(release.version)
        except ManifestError:
            continue
        if best is None or parts > best_parts:
            best = release
            best_parts = parts
    return best


def _machine() -> str:
    """Return the processor name in the form of the release package names: x86_64 or arm64."""
    name = platform_module.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(name, name)


# the manual reasons that have a package of their own in the release, and how to find it
_PACKAGE_KINDS = {"appimage": ("linux", ".AppImage"), "macos": ("macos", ".dmg")}


def _asset_payload(asset):
    if asset is None:
        return None
    return {
        "name": asset.name,
        "url": asset.url,
        "size": asset.size,
        "sha256": asset.sha256,
    }


class GitHubUpdateChecker:
    """Fetch, validate, and compare the latest stable GitHub release off the GUI thread."""

    def __init__(
        self,
        app=None,
        *,
        transport_factory=None,
        platform=None,
        install_root=None,
        repository_url=None,
    ):
        self.app = app
        self.transport_factory = transport_factory
        self.platform = platform
        self.install_root = install_root
        self.repository_url = repository_url

    def create_transport(self, app=None, *, repository_url=None):
        """Create the configured release transport for checks and downloads."""
        app = app or self.app
        if self.transport_factory is not None:
            return self.transport_factory(app)
        repository_url = self.repository_url if repository_url is None else repository_url
        repository = DEFAULT_REPOSITORY if not repository_url else repository_url
        return GitHubReleaseTransport(repository=repository)

    def _channel(self, app) -> str:
        name = _platform_name(self.platform or getattr(app, "os", sys.platform))
        if name in ("windows", "linux"):
            return name
        return "unsupported"

    def _payload(self, status, channel, app, release=None, *, mandatory=False, error="", install=None, reason=""):
        current = {
            "version": str(getattr(app, "version", "")),
            "build": _local_build(app),
        }
        payload = {
            "status": status,
            "channel": channel,
            "mandatory": bool(mandatory),
            "current": current,
            "current_version": current["version"],
            "current_build": current["build"],
            # The offer carries its source so the download uses the same provider and address.
            "provider": "github",
            "provider_url": getattr(self, "_offer_url", None) or self.repository_url or DEFAULT_REPOSITORY,
        }
        if release is not None:
            payload.update(
                {
                    "tag": release.tag,
                    "version": release.version,
                    "build_string": release.build_string,
                    "release_notes": release.body,
                    "published_at": release.published_at,
                    "release_page": release.html_url,
                    "zipball_url": release.zipball_url,
                    "asset": _asset_payload(release.windows_asset()),
                    "deletions_asset": _asset_payload(release.asset(DELETIONS_ASSET_NAME)),
                }
            )
        if install is not None:
            automatic = install in ("windows_asset", "linux_zipball")
            payload.update(
                {
                    "install": install,
                    "automatic": automatic,
                    "manual": not automatic,
                }
            )
            if reason:
                payload["manual_reason"] = reason
            if release is not None and reason in _PACKAGE_KINDS:
                package_platform, suffix = _PACKAGE_KINDS[reason]
                payload["package_asset"] = _asset_payload(release.package_asset(package_platform, _machine(), suffix))
        if error:
            payload["error"] = error
        return payload

    @staticmethod
    def _release_blocked(restore_dir, version, build_string) -> bool:
        """Return whether a revert marker blocks this release, including equivalent tags."""
        if is_release_blocked(restore_dir, version, build_string):
            return True
        marker = blocked_release_path(restore_dir)
        try:
            if marker.is_symlink() or not marker.is_file():
                return False
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        recorded_version = data.get("version")
        recorded_build = data.get("build_string")
        if not isinstance(recorded_version, str) or not isinstance(recorded_build, str):
            return False
        if recorded_build != str(build_string):
            return False
        try:
            return _numeric(recorded_version) == _numeric(version)
        except ManifestError:
            return False

    def _install_plan(self, app) -> tuple[str, str]:
        """Return the (install mode, manual reason) pair for this installation."""
        install_root = Path(self.install_root) if self.install_root else Path(_install_root(app))
        if _is_appimage():
            return "manual", "appimage"
        name = _platform_name(self.platform or getattr(app, "os", sys.platform))
        if name == "windows":
            if not _is_frozen():
                return "manual", "windows-source"
            if not _is_x64():
                return "manual", "unsupported-architecture"
            return "windows_asset", ""
        if name == "linux":
            if _is_frozen():
                return "manual", "frozen-linux"
            if (install_root / ".git").exists():
                return "manual", "git-checkout"
            if not os.access(install_root, os.W_OK):
                return "manual", "read-only"
            return "linux_zipball", ""
        if name == "macos":
            return "manual", "macos"
        return "manual", "unsupported-platform"

    def revert_payload(self, app, version, *, repository_url=None, transport=None):
        """Return the offer of the release with this version, to install it as a revert; raise when it is missing."""
        channel = self._channel(app)
        install, reason = self._install_plan(app)
        if install not in ("windows_asset", "linux_zipball"):
            raise TransportError(f"This installation cannot be reverted automatically: {reason}.")
        if transport is None:
            transport = self.create_transport(app, repository_url=repository_url)
        wanted = _numeric(version)
        release = None
        for candidate in transport.list_releases():
            try:
                if _numeric(candidate.version) == wanted:
                    release = candidate
                    break
            except ManifestError:
                continue
        if release is None:
            raise TransportError(f"The release {version} is not published on GitHub.")
        if install == "windows_asset" and release.windows_asset() is None:
            raise TransportError(f"The release {version} has no Windows update archive.")
        payload = self._payload("update_available", channel, app, release=release, install=install, reason=reason)
        payload["revert"] = True
        return payload

    def run_check(self, app=None, *, forced=False, repository_url=None):
        """Run one check and return a result safe for a worker task."""
        app = app or self.app
        self._offer_url = self.repository_url if repository_url is None else repository_url
        try:
            return self._check(app, forced=forced, repository_url=repository_url)
        except Exception as exc:
            _app_log(app, "error", f"GitHubUpdateChecker failed:\n{traceback.format_exc()}")
            result = self._payload("error", self._channel(app), app, error=str(exc))
            _inform(app, "Failed checking for the latest version safely.")
            _emit(app, "update_check_result", result)
            return result

    def _check(self, app, *, forced=False, repository_url=None):
        preference = getattr(app, "options", None)
        if preference is None:
            preference = getattr(app, "defaults", {})
        defaults = getattr(app, "defaults", {})
        if not forced and not preference.get("global_version_check", defaults.get("global_version_check")):
            result = self._payload("disabled", self._channel(app), app)
            _emit(app, "update_check_result", result)
            return result

        channel = self._channel(app)
        try:
            transport = self.create_transport(app, repository_url=repository_url)
        except Exception as exc:
            result = self._payload("error", channel, app, error=str(exc))
            if isinstance(exc, TransportError):
                url = repository_url if repository_url is not None else self.repository_url
                url = url or DEFAULT_REPOSITORY
                _inform(
                    app,
                    f"The update URL is not a valid GitHub repository: {url}. "
                    "Set an https://github.com/<owner>/<repo> URL in Preferences.",
                )
            else:
                _inform(app, "Failed checking for the latest version. No update transport is available.")
            _emit(app, "update_check_result", result)
            return result

        try:
            releases = transport.list_releases()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            result = self._payload("error", channel, app, error=str(exc))
            _inform(app, "Failed checking for the latest version. Could not retrieve a valid release list.")
            _emit(app, "update_check_result", result)
            return result

        release = select_release(releases)
        if release is None:
            result = self._payload("up_to_date", channel, app)
            result["no_releases"] = True
            _app_log(app, "debug", "No published stable GitHub releases were found.")
            _emit(app, "update_check_result", result)
            return result

        if not is_newer(release.version, getattr(app, "version", "0")):
            result = self._payload("up_to_date", channel, app, release=release)
            _app_log(app, "debug", "The application is up to date.")
            _emit(app, "update_check_result", result)
            return result

        restore_dir = restore_dir_for_install(
            getattr(app, "data_path", Path.home() / ".FlatCAM"),
            self.install_root or _install_root(app),
        )
        if self._release_blocked(restore_dir, release.version, release.build_string):
            result = self._payload("blocked", channel, app, release=release)
            _inform(app, "The available release was previously reverted and remains blocked.")
            _emit(app, "update_check_result", result)
            return result

        install, reason = self._install_plan(app)
        if install == "windows_asset" and release.windows_asset() is None:
            result = self._payload(
                "error",
                channel,
                app,
                release=release,
                error="The newest release does not provide the Windows x64 update asset.",
            )
            _inform(app, "Failed checking for the latest version. The Windows update asset is missing.")
            _emit(app, "update_check_result", result)
            return result

        result = self._payload("update_available", channel, app, release=release, install=install, reason=reason)
        _emit(app, "update_check_result", result)
        _emit(app, "update_available", result)
        return result
