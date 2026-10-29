"""Download and normalize GitHub release archives into an external staging area."""

from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..archive import DEFAULT_EXCLUSION_POLICY, build_deterministic_zip
from ..common import (
    DownloadCancelled,
    TransportError,
    app_log as _app_log,
    install_root as _install_root,
)
from ..manifest import (
    ArchiveInfo,
    DeletionEntry,
    ManifestError,
    build_manifest,
    compare_versions,
    manifest_to_json,
    parse_version,
    sha256_file,
    validate_relative_path,
)
from ..recovery import is_protected_deletion, validate_relative_path as validate_installer_path
from .checker import _is_appimage, _is_frozen, _is_x64, _platform_name
from .transport import DELETIONS_ASSET_NAME, GitHubReleaseTransport, is_windows_asset_name


SOURCE_ARCHIVE_NAME = "flatcam-evo-linux-source.zip"
SOURCE_VERSION_MEMBER = "appMain.py"
SOURCE_ROOT_ENTRIES = ("flatcam.py", "appMain.py")
WINDOWS_ROOT_ENTRIES = ("FlatCAM.exe", "lib/library.zip")
REQUIREMENTS_MEMBER = "requirements.txt"
_STAGING_PREFIX = "flatcam-evo-update-"

_TRUST_DIGEST = "Update archive verified against the published GitHub SHA-256 digest."
_TRUST_ASSET = "GitHub release asset downloaded over HTTPS; no SHA-256 digest was published."
_TRUST_TLS = "Source archive downloaded over HTTPS from GitHub; TLS only, no published digest."

_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')

_DELETIONS_SCHEMA_VERSION = 1
_CONTROL_METADATA_NAMES = frozenset(
    {
        "update_job.json",
        "rollback_job.json",
        ".updater_started",
        ".rollback_ready.json",
        ".rollback_cancelled",
    }
)


def validate_windows_relative_path(value: str) -> str:
    """Return a canonical path or reject Windows-incompatible payload components."""
    path = validate_relative_path(value)
    for part in path.split("/"):
        if any(character in _WINDOWS_INVALID_CHARACTERS or ord(character) < 32 for character in part):
            raise ManifestError(f"Windows payload path contains an invalid character: {value!r}.")
        if part != part.rstrip(" ."):
            raise ManifestError(f"Windows payload path component ends with a space or dot: {value!r}.")
        if part.split(".", 1)[0].rstrip(" .").upper() in _WINDOWS_RESERVED_NAMES:
            raise ManifestError(f"Windows payload path uses a reserved device name: {value!r}.")
    return path


class StagingError(TransportError):
    """Raised when a downloaded release cannot be staged safely."""


@dataclass(frozen=True)
class StagedUpdate:
    """One normalized update, ready for the download worker and its helper."""

    staging_dir: Path
    archive_path: Path
    manifest: dict
    channel: str
    install: str
    trust: str
    dependencies: dict


@dataclass(frozen=True)
class _Download:
    install: str
    channel: str
    version: str
    build_string: str
    release_notes: str
    published_at: str
    archive_name: str
    url: str
    size: int | None
    sha256: str | None
    deletions_url: str | None
    deletions_size: int | None
    deletions_sha256: str | None


def _installation_platform(app, platform):
    """Return the platform identifier used to enforce the install matrix."""
    if platform is not None:
        return platform
    return getattr(app, "os", None) or sys.platform


def _is_git_worktree(root: Path) -> bool:
    """Return whether an installation directory is a Git worktree marker or checkout."""
    marker = Path(root) / ".git"
    return marker.exists() or marker.is_symlink()


def _install_mode(platform, install_root: Path) -> tuple[str, str]:
    """Return the (install mode, manual reason) pair recomputed on this machine."""
    name = _platform_name(platform)
    if _is_appimage():
        return "manual", "appimage"
    if name == "windows":
        if not _is_frozen():
            return "manual", "windows-source"
        if not _is_x64():
            return "manual", "unsupported-architecture"
        return "windows_asset", ""
    if name == "linux":
        if _is_frozen():
            return "manual", "frozen-linux"
        if _is_git_worktree(install_root):
            return "manual", "git-checkout"
        if not os.access(install_root, os.W_OK):
            return "manual", "read-only"
        return "linux_zipball", ""
    if name == "macos":
        return "manual", "macos"
    return "manual", "unsupported-platform"


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _deletions_advertisement(payload) -> tuple[str | None, int | None, str | None]:
    """Return the optional deletion asset fields, validating an advertised asset."""
    raw = payload.get("deletions_asset")
    if raw is None:
        return None, None, None
    if not isinstance(raw, dict) or raw.get("name") != DELETIONS_ASSET_NAME:
        raise StagingError("Release deletion asset is malformed.")
    url = raw.get("url")
    if not isinstance(url, str) or not url:
        raise StagingError("Release deletion asset URL is missing.")
    size = raw.get("size")
    if size is not None and (type(size) is not int or size <= 0):
        raise StagingError("Release deletion asset size is invalid.")
    sha256 = None
    digest = raw.get("sha256")
    if digest is not None:
        if not isinstance(digest, str) or not _is_sha256(digest):
            raise StagingError("Release deletion asset SHA-256 digest is invalid.")
        sha256 = digest.lower()
    return url, size, sha256


def _release_download(payload, platform, install_root: Path) -> _Download:
    """Validate the payload against this installation before anything is downloaded."""
    if not isinstance(payload, dict):
        raise StagingError("Update payload must be a mapping.")
    status = payload.get("status")
    if status is not None and status != "update_available":
        raise StagingError(f"Update payload is not an available release: {status!r}.")
    install, reason = _install_mode(platform, install_root)
    if install == "manual":
        raise StagingError(f"This installation cannot be updated automatically: {reason}.")
    channel = "windows" if install == "windows_asset" else "linux"
    if payload.get("channel") != channel:
        raise StagingError(f"Release channel {payload.get('channel')!r} does not match this installation.")
    declared = payload.get("install")
    if declared is not None and declared != install:
        raise StagingError(f"Release install mode {declared!r} is not supported for {channel!r}.")
    version = payload.get("version")
    try:
        parse_version(version)
    except ManifestError as exc:
        raise StagingError(f"Release version is invalid: {version!r}.") from exc
    build_string = payload.get("build_string")
    if not isinstance(build_string, str) or not build_string:
        raise StagingError("Release build identity is missing.")
    release_notes = payload.get("release_notes")
    if not isinstance(release_notes, str):
        raise StagingError("Release notes are invalid.")
    published_at = payload.get("published_at")
    if not isinstance(published_at, str):
        raise StagingError("Release timestamp is invalid.")
    deletions_url, deletions_size, deletions_sha256 = _deletions_advertisement(payload)
    common = {
        "install": install,
        "channel": channel,
        "version": str(version),
        "build_string": build_string,
        "release_notes": release_notes,
        "published_at": published_at,
    }
    if install == "windows_asset":
        asset = payload.get("asset")
        if not isinstance(asset, dict) or not is_windows_asset_name(asset.get("name"), version):
            raise StagingError("Release does not provide the Windows x64 update asset.")
        url = asset.get("url")
        if not isinstance(url, str) or not url:
            raise StagingError("Release Windows asset URL is missing.")
        size = asset.get("size")
        if size is not None and (type(size) is not int or size <= 0):
            raise StagingError("Release Windows asset size is invalid.")
        sha256 = None
        digest = asset.get("sha256")
        if digest is not None:
            if not isinstance(digest, str) or not _is_sha256(digest):
                raise StagingError("Release Windows asset SHA-256 digest is invalid.")
            sha256 = digest.lower()
        return _Download(
            **common,
            archive_name=asset["name"],
            url=url,
            size=size,
            sha256=sha256,
            deletions_url=deletions_url,
            deletions_size=deletions_size,
            deletions_sha256=deletions_sha256,
        )
    url = payload.get("zipball_url")
    if not isinstance(url, str) or not url:
        raise StagingError("Release source archive URL is missing.")
    return _Download(
        **common,
        archive_name=SOURCE_ARCHIVE_NAME,
        url=url,
        size=None,
        sha256=None,
        deletions_url=deletions_url,
        deletions_size=deletions_size,
        deletions_sha256=deletions_sha256,
    )


def _inspect_members(archive_path: Path) -> list[tuple[zipfile.ZipInfo, str, bool]]:
    """Return validated (info, normalized path, is directory) tuples for every member."""
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise StagingError("Downloaded update is not a valid ZIP archive.") from exc
    if not infos:
        raise StagingError("Downloaded update archive is empty.")
    members = []
    for info in infos:
        raw = info.filename
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise StagingError("ZIP contains a member with an invalid name.")
        if "\\" in raw:
            raise StagingError(f"ZIP member path uses backslashes: {raw!r}.")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise StagingError(f"ZIP member is a symbolic link: {raw!r}.")
        file_type = stat.S_IFMT(mode)
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise StagingError(f"ZIP member has an unsupported file type: {raw!r}.")
        is_dir = info.is_dir() or raw.endswith("/") or stat.S_ISDIR(mode)
        if is_dir and file_type == stat.S_IFREG:
            raise StagingError(f"ZIP member type conflicts with its path: {raw!r}.")
        name = raw[:-1] if raw.endswith("/") else raw
        if not name:
            raise StagingError("ZIP contains an empty member path.")
        try:
            normalized = validate_relative_path(name)
        except ManifestError as exc:
            raise StagingError(f"ZIP member path is unsafe: {raw!r}.") from exc
        if any(
            len(part) > 1 and part[1] == ":" and part[0].isalpha()
            for part in normalized.split("/")
        ):
            raise StagingError(f"ZIP member path contains a drive prefix: {raw!r}.")
        members.append((info, normalized, is_dir))
    return members


def _source_wrapper_prefix(members) -> str | None:
    """Return the casefolded single enclosing directory of a GitHub source archive."""
    prefixes = {path.split("/", 1)[0].casefold() for _, path, _ in members}
    if len(prefixes) != 1:
        return None
    prefix = next(iter(prefixes))
    for _, path, is_dir in members:
        if path.casefold() == prefix and not is_dir:
            return None
    return prefix


def _strip_wrapper(members, prefix: str) -> list[tuple[zipfile.ZipInfo, str, bool]]:
    """Strip exactly one enclosing directory from source archive member paths."""
    stripped = []
    for info, path, is_dir in members:
        _, separator, tail = path.partition("/")
        if separator:
            stripped.append((info, tail, is_dir))
    return stripped


def _check_structure(members) -> dict[str, tuple[zipfile.ZipInfo, str]]:
    """Reject duplicate, case-colliding, or parent-file member paths."""
    files: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    directories: set[str] = set()
    for info, path, is_dir in members:
        key = path.casefold()
        if key in files or key in directories:
            raise StagingError(f"ZIP contains a duplicate path: {path!r}.")
        if is_dir:
            directories.add(key)
        else:
            files[key] = (info, path)
    for key, (_, path) in files.items():
        parts = key.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in files:
                raise StagingError(f"ZIP member conflicts with a parent file: {path!r}.")
    for key in directories:
        parts = key.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in files:
                raise StagingError(f"ZIP directory conflicts with a parent file: {key!r}.")
    return files


def _validate_windows_members(members) -> None:
    """Reject Windows-incompatible member components before any extraction."""
    for _, path, _ in members:
        try:
            validate_windows_relative_path(path)
        except ManifestError as exc:
            raise StagingError(f"ZIP member path is not valid on Windows: {path!r}.") from exc


def _select_members(members, channel: str) -> list[tuple[zipfile.ZipInfo, str]]:
    """Return payload members after the shared protection policy and surface checks."""
    files = _check_structure(members)
    selected = [
        (info, path)
        for info, path in files.values()
        if not DEFAULT_EXCLUSION_POLICY.excludes(path)
    ]
    if not selected:
        raise StagingError("Update archive contains no manageable files.")
    required = WINDOWS_ROOT_ENTRIES if channel == "windows" else SOURCE_ROOT_ENTRIES
    present = {path.casefold() for _, path in selected}
    for entry in required:
        if entry.casefold() not in present:
            raise StagingError(f"Update archive is missing the expected {entry!r} entry.")
    return selected


def _check_cancelled(cancel_cb) -> None:
    if cancel_cb is not None and cancel_cb():
        raise DownloadCancelled("Update staging cancelled.")


def _extract_members(archive_path: Path, selected, payload_dir: Path, cancel_cb=None) -> None:
    """Write selected members below the payload root, preserving ordinary mode bits."""
    payload_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for info, path in selected:
            _check_cancelled(cancel_cb)
            destination = payload_dir.joinpath(*path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            permissions = (info.external_attr >> 16) & 0o777
            if permissions:
                os.chmod(destination, permissions)


def _staged_source_version(path: Path) -> tuple[str, ...]:
    """Return literal App.version values from a staged appMain.py without executing it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise StagingError(f"Staged source application cannot be parsed: {exc}") from exc
    values = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "App":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "version"
                for target in statement.targets
            ):
                continue
            value = statement.value
            if (
                not isinstance(value, ast.Constant)
                or isinstance(value.value, bool)
                or not isinstance(value.value, (str, int, float))
            ):
                raise StagingError("Staged App.version is not a literal value.")
            values.append(str(value.value))
    if not values:
        raise StagingError("Staged application does not declare App.version.")
    return tuple(values)


def _validate_source_version(payload_dir: Path, version: str) -> None:
    """Refuse staged sources whose literal App.version differs from the release."""
    for value in _staged_source_version(payload_dir / SOURCE_VERSION_MEMBER):
        try:
            if compare_versions(value, version) != 0:
                raise StagingError(
                    f"Staged source version {value!r} does not match the release version {version!r}."
                )
        except ManifestError as exc:
            raise StagingError(f"Staged source version {value!r} cannot be compared.") from exc


def _read_requirements(path: Path) -> str | None:
    """Return newline-normalized requirements text, None when absent, or raise safely."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StagingError(f"Could not read requirements file {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StagingError(f"Requirements file is not valid UTF-8: {path}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _requirement_lines(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(
        line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    )


def _dependency_changes(install_root: Path, payload_dir: Path) -> dict:
    """Return requirement differences for the later explicit GUI confirmation."""
    staged_text = _read_requirements(payload_dir / REQUIREMENTS_MEMBER)
    installed_text = _read_requirements(Path(install_root) / REQUIREMENTS_MEMBER)
    staged = _requirement_lines(staged_text)
    installed = _requirement_lines(installed_text)
    added = sorted(staged - installed)
    removed = sorted(installed - staged)
    if staged_text is None and installed_text is None:
        changed = False
    elif staged_text is None or installed_text is None:
        changed = True
    else:
        changed = bool(added or removed) or staged_text != installed_text
    return {"changed": changed, "added": added, "removed": removed}


def _trust_text(channel: str, digest_verified: bool) -> str:
    if channel == "windows":
        return _TRUST_DIGEST if digest_verified else _TRUST_ASSET
    return _TRUST_TLS


def _overlaps(first: Path, second: Path) -> bool:
    first = Path(first).resolve()
    second = Path(second).resolve()
    return first == second or first in second.parents or second in first.parents


def _deletion_path(value, platform: str) -> str:
    """Return the installer-canonical deletion path or reject it."""
    try:
        normalized = validate_installer_path(value)
    except ValueError as exc:
        raise StagingError(f"Deletion path is unsafe: {value!r}.") from exc
    if platform == "windows":
        try:
            normalized = validate_windows_relative_path(normalized)
        except ManifestError as exc:
            raise StagingError(f"Deletion path is not valid on Windows: {value!r}.") from exc
    if normalized.rsplit("/", 1)[-1].casefold() in _CONTROL_METADATA_NAMES or is_protected_deletion(normalized):
        raise StagingError(f"Deletion path is protected: {value!r}.")
    return normalized


def _deletion_version(value) -> str:
    """Return a deletion version string, rejecting booleans and non-numeric values."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise StagingError(f"Deletion version must be numeric: {value!r}.")
    try:
        parse_version(value)
    except ManifestError as exc:
        raise StagingError(f"Deletion version is invalid: {value!r}.") from exc
    return str(value)


def _casefold_candidates(root: Path, relative: str) -> tuple[Path, ...]:
    """Return existing installation entries matching a path using casefold semantics."""
    current = [Path(root)]
    for part in relative.split("/"):
        matches = []
        for base in current:
            if not base.is_dir():
                continue
            try:
                matches.extend(
                    path for path in base.iterdir() if path.name.casefold() == part.casefold()
                )
            except OSError:
                continue
        current = matches
        if not current:
            return ()
    return tuple(current)


def _validate_deletion_entries(
    entries, platform: str, install_root: Path, check_local: bool = True
) -> list[tuple[str, str]]:
    """Validate one platform's complete cumulative deletion list."""
    validated = []
    seen = set()
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict) or "path" not in raw or "version" not in raw:
            raise StagingError(f"Deletion entry {index} must contain path and version.")
        path = _deletion_path(raw["path"], platform)
        key = path.casefold()
        if key in seen:
            raise StagingError(f"Deletion asset contains duplicate path {path!r}.")
        seen.add(key)
        version = _deletion_version(raw["version"])
        if check_local and any(candidate.is_dir() for candidate in _casefold_candidates(install_root, path)):
            raise StagingError(f"Deletion path is a directory: {path!r}.")
        validated.append((path, version))
    for path, _ in validated:
        parts = path.casefold().split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in seen:
                raise StagingError(f"Deletion path refers to a parent directory: {path!r}.")
    return validated


def _parse_deletions_asset(
    raw,
    *,
    platform: str,
    install_root: Path,
    target_version: str,
    installed_version,
) -> tuple[DeletionEntry, ...]:
    """Validate the full deletion asset and select entries for this update."""
    if not isinstance(raw, dict):
        raise StagingError("Deletion asset must be a JSON object.")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != _DELETIONS_SCHEMA_VERSION:
        raise StagingError("Deletion asset schema_version must be 1.")
    validated = {}
    for name in ("windows", "linux"):
        entries = raw.get(name)
        if not isinstance(entries, list):
            raise StagingError(f"Deletion asset {name!r} must be a list.")
        validated[name] = _validate_deletion_entries(
            entries, name, install_root, check_local=name == platform
        )
    selected = []
    for path, version in validated[platform]:
        if (
            compare_versions(installed_version, version) < 0
            and compare_versions(version, target_version) <= 0
        ):
            selected.append(DeletionEntry(path=path, version=version))
    return tuple(selected)


def _installed_version(app, payload):
    """Return a trustworthy installed version for deletion filtering."""
    candidates = []
    if app is not None:
        candidates.append(getattr(app, "version", None))
    candidates.append(payload.get("current_version"))
    current = payload.get("current")
    if isinstance(current, dict):
        candidates.append(current.get("version"))
    for value in candidates:
        if value is None or value == "":
            continue
        try:
            parse_version(value)
        except ManifestError:
            continue
        return value
    raise StagingError("Deletion asset requires a valid installed version.")


def _reject_payload_deletion_overlap(deletions, payload_paths) -> None:
    """Reject deletion/payload collisions, including parent directories."""
    payload_keys = {path.casefold() for path in payload_paths}
    for entry in deletions:
        key = entry.path.casefold()
        if key in payload_keys:
            raise StagingError(f"Deletion path overlaps a payload file: {entry.path!r}.")
        parts = key.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in payload_keys:
                raise StagingError(f"Deletion path is below a payload file: {entry.path!r}.")
        if any(path.startswith(key + "/") for path in payload_keys):
            raise StagingError(f"Deletion path is a payload parent directory: {entry.path!r}.")


def stage_update(
    app=None,
    payload=None,
    *,
    transport=None,
    staging_root=None,
    platform=None,
    install_root=None,
    cancel_cb=None,
    progress_cb=None,
) -> StagedUpdate:
    """Download, validate, and normalize one release archive outside the installation."""
    root = Path(install_root) if install_root is not None else Path(_install_root(app))
    request = _release_download(payload, _installation_platform(app, platform), root)
    base = Path(staging_root) if staging_root is not None else Path(tempfile.gettempdir())
    if _overlaps(base, root):
        raise StagingError("The staging directory must live outside the installation.")
    base.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=str(base)))
    try:
        download = staging / "download" / request.archive_name
        if transport is None:
            transport = GitHubReleaseTransport()
        transport.download_archive(
            request.url,
            download,
            expected_size=request.size,
            expected_sha256=request.sha256,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        if cancel_cb is not None and cancel_cb():
            raise DownloadCancelled("Update staging cancelled.")
        members = _inspect_members(download)
        _check_structure(members)
        if request.channel == "windows":
            _validate_windows_members(members)
        if request.channel == "linux":
            prefix = _source_wrapper_prefix(members)
            if prefix is None:
                raise StagingError("Source archive must contain exactly one enclosing directory.")
            members = _strip_wrapper(members, prefix)
        selected = _select_members(members, request.channel)
        deletions = ()
        if request.deletions_url is not None:
            deletions_path = download.parent / DELETIONS_ASSET_NAME
            transport.download_archive(
                request.deletions_url,
                deletions_path,
                expected_size=request.deletions_size,
                expected_sha256=request.deletions_sha256,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
            _check_cancelled(cancel_cb)
            try:
                raw_deletions = json.loads(deletions_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise StagingError(f"Deletion asset is not valid JSON: {exc}") from exc
            deletions = _parse_deletions_asset(
                raw_deletions,
                platform=request.channel,
                install_root=root,
                target_version=request.version,
                installed_version=_installed_version(app, payload),
            )
            _reject_payload_deletion_overlap(deletions, [path for _, path in selected])
        payload_dir = staging / "payload"
        _extract_members(download, selected, payload_dir, cancel_cb)
        if request.channel == "linux":
            _validate_source_version(payload_dir, request.version)
        archive_path = staging / request.archive_name
        build_deterministic_zip(payload_dir, archive_path, DEFAULT_EXCLUSION_POLICY)
        _check_cancelled(cancel_cb)
        manifest = build_manifest(
            payload_dir,
            request.version,
            channel=request.channel,
            archive=ArchiveInfo(
                filename=archive_path.name,
                size=archive_path.stat().st_size,
                sha256=sha256_file(archive_path),
            ),
            build_string=request.build_string,
            version_date=request.published_at,
            minimum_required_version="0",
            release_notes=request.release_notes,
            deletions=deletions,
            exclusions=DEFAULT_EXCLUSION_POLICY,
        )
        dependencies = _dependency_changes(root, payload_dir)
        _check_cancelled(cancel_cb)
        shutil.rmtree(download.parent, ignore_errors=True)
        _app_log(app, "debug", f"Staged GitHub update {request.version} in {staging}")
        return StagedUpdate(
            staging_dir=staging,
            archive_path=archive_path,
            manifest=json.loads(manifest_to_json(manifest)),
            channel=request.channel,
            install=request.install,
            trust=_trust_text(request.channel, request.sha256 is not None),
            dependencies=dependencies,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
