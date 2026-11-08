"""Manifest models and validation for full-archive releases."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
SUPPORTED_CHANNELS = frozenset({"windows", "linux"})
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ManifestError(ValueError):
    """Raised when manifest data or a managed path is invalid."""


@dataclass(frozen=True)
class ArchiveInfo:
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True)
class FileEntry:
    """One managed file: a SHA-256 (a manifest built from files) or the CRC-32 of its archive member.

    A GitHub release archive is checked as a whole against its SHA-256; the updater checks the CRC-32 of each member
    while it extracts it, so the app does not extract and pack the archive again only to hash each file.
    """

    path: str
    size: int
    sha256: str = ""
    crc32: int | None = None


@dataclass(frozen=True)
class DeletionEntry:
    path: str
    version: str


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    channel: str
    version: str
    build_string: str
    version_date: str
    minimum_required_version: str
    release_notes: str
    archive: ArchiveInfo
    files: tuple[FileEntry, ...]
    deletions: tuple[DeletionEntry, ...] = ()
    # the enclosing directory of the archive members (a GitHub source archive); empty for a flat archive
    archive_prefix: str = ""

    @property
    def build(self) -> str:
        """Return the build metadata using the short public name."""
        return self.build_string


def parse_version(value) -> tuple[int, ...]:
    """Parse a dotted numeric version into comparable integer parts."""
    if isinstance(value, bool):
        raise ManifestError(f"Unsupported version type {type(value).__name__!r}.")
    if isinstance(value, int):
        if value < 0:
            raise ManifestError(f"Version must not be negative: {value!r}")
        return (value,)
    if isinstance(value, float):
        value = repr(value)
    if not isinstance(value, str):
        raise ManifestError(f"Unsupported version type {type(value).__name__!r}: {value!r}")
    value = value.strip()
    if not value:
        raise ManifestError("Version string is empty.")
    parts = value.split(".")
    if any(not part.isdigit() for part in parts):
        raise ManifestError(f"Cannot parse version {value!r}: all parts must be integers.")
    return tuple(int(part) for part in parts)


def _normal_version(value) -> tuple[int, ...]:
    parts = list(parse_version(value))
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _build_tuple(value) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(number) for number in numbers)


def compare_versions(left, right, left_build="", right_build="") -> int:
    """Compare versions, using numeric build metadata only when versions tie."""
    left_version = _normal_version(left)
    right_version = _normal_version(right)
    if left_version != right_version:
        return 1 if left_version > right_version else -1
    left_build_tuple = _build_tuple(left_build)
    right_build_tuple = _build_tuple(right_build)
    if left_build_tuple == right_build_tuple:
        return 0
    if not left_build_tuple:
        return -1
    if not right_build_tuple:
        return 1
    return 1 if left_build_tuple > right_build_tuple else -1


def is_newer(remote_version, local_version, remote_build="", local_build="") -> bool:
    """Return whether a remote release is newer than the local release."""
    return compare_versions(remote_version, local_version, remote_build, local_build) > 0


def select_channel(system_name: str | None = None) -> str:
    """Map Windows to its channel and Linux/Darwin to the source channel."""
    value = system_name if system_name is not None else sys.platform
    value = str(value).lower()
    if value.startswith("win"):
        return "windows"
    if value.startswith("linux"):
        return "linux"
    if value.startswith("darwin") or value.startswith("mac"):
        return "linux"
    raise ManifestError(f"Unsupported platform: {system_name or sys.platform!r}")


def validate_relative_path(value: str) -> str:
    """Return a safe forward-slash path or raise for traversal/absolute paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ManifestError(f"Invalid relative path: {value!r}")
    path = value.replace("\\", "/")
    if path.startswith("/") or _DRIVE_RE.match(path):
        raise ManifestError(f"Security: path {value!r} must be relative.")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"Security: path {value!r} contains an unsafe component.")
    return "/".join(parts)


def _validate_hash(value, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ManifestError(f"{label} must be a 64-character SHA-256 hex digest.")
    return value.lower()


def _validate_crc32(value, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ManifestError(f"{label} must be a 32-bit unsigned integer.")
    return value


def _file_check(entry: FileEntry, index) -> dict:
    """Return the integrity fields of a file entry: its SHA-256, its CRC-32, or both."""
    check = {}
    if entry.sha256:
        check["sha256"] = _validate_hash(entry.sha256, f"files[{index}].sha256")
    if entry.crc32 is not None:
        check["crc32"] = _validate_crc32(entry.crc32, f"files[{index}].crc32")
    if not check:
        raise ManifestError(f"files[{index}] needs a sha256 or a crc32 field.")
    return check


def _validate_size(value, label: str) -> int:
    if isinstance(value, bool):
        raise ManifestError(f"{label} must be a non-negative integer.")
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{label} must be a non-negative integer.") from exc
    if size < 0:
        raise ManifestError(f"{label} must be a non-negative integer.")
    return size


def _validate_channel(value: str) -> str:
    if value not in SUPPORTED_CHANNELS:
        raise ManifestError(f"Unsupported release channel: {value!r}")
    return value


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the lowercase SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_archive(raw) -> ArchiveInfo:
    if not isinstance(raw, dict):
        raise ManifestError("Manifest 'archive' must be an object.")
    for key in ("filename", "size", "sha256"):
        if key not in raw:
            raise ManifestError(f"Manifest archive is missing required field {key!r}.")
    return ArchiveInfo(
        filename=validate_relative_path(raw["filename"]),
        size=_validate_size(raw["size"], "archive.size"),
        sha256=_validate_hash(raw["sha256"], "archive.sha256"),
    )


def parse_manifest(data) -> Manifest:
    """Parse and validate a manifest dict, JSON string, or JSON bytes."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Invalid JSON in manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("Manifest must be a JSON object.")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(
            f"Unsupported schema_version {data.get('schema_version')!r}; expected {SCHEMA_VERSION}."
        )
    required = (
        "channel",
        "version",
        "build",
        "version_date",
        "minimum_required_version",
        "release_notes",
        "archive",
        "files",
        "deletions",
    )
    missing = [key for key in required if key not in data]
    if missing:
        raise ManifestError(f"Manifest missing required field {missing[0]!r}.")
    version = data["version"]
    parse_version(version)
    files_data = data["files"]
    if not isinstance(files_data, list):
        raise ManifestError("Manifest 'files' must be a list.")
    files = []
    seen = set()
    for index, raw in enumerate(files_data):
        if not isinstance(raw, dict):
            raise ManifestError(f"files[{index}] must be an object.")
        for key in ("path", "size"):
            if key not in raw:
                raise ManifestError(f"files[{index}] is missing required field {key!r}.")
        path = validate_relative_path(raw["path"])
        if path in seen:
            raise ManifestError(f"Manifest contains duplicate file path {path!r}.")
        seen.add(path)
        entry = FileEntry(
            path=path,
            size=_validate_size(raw["size"], f"files[{index}].size"),
            sha256=raw.get("sha256", ""),
            crc32=raw.get("crc32"),
        )
        check = _file_check(entry, index)
        files.append(FileEntry(path=path, size=entry.size, sha256=check.get("sha256", ""), crc32=check.get("crc32")))
    deletion_data = data["deletions"]
    if not isinstance(deletion_data, list):
        raise ManifestError("Manifest 'deletions' must be a list.")
    deletions = []
    seen = set()
    for index, raw in enumerate(deletion_data):
        if not isinstance(raw, dict) or "path" not in raw or "version" not in raw:
            raise ManifestError(f"deletions[{index}] must contain path and version.")
        path = validate_relative_path(raw["path"])
        if path in seen:
            raise ManifestError(f"Manifest contains duplicate deletion path {path!r}.")
        seen.add(path)
        parse_version(raw["version"])
        deletions.append(DeletionEntry(path=path, version=str(raw["version"])))
    prefix = data.get("archive_prefix", "")
    if not isinstance(prefix, str):
        raise ManifestError("Manifest 'archive_prefix' must be a string.")
    if prefix:
        prefix = validate_relative_path(prefix)
    return Manifest(
        schema_version=SCHEMA_VERSION,
        channel=_validate_channel(data["channel"]),
        version=str(version),
        build_string=str(data["build"]),
        version_date=str(data["version_date"]),
        minimum_required_version=str(data["minimum_required_version"]),
        release_notes=str(data["release_notes"]),
        archive=_parse_archive(data["archive"]),
        files=tuple(files),
        deletions=tuple(deletions),
        archive_prefix=prefix,
    )


def manifest_to_json(manifest: Manifest) -> str:
    """Serialize a manifest deterministically as compact JSON."""
    data = {
        "archive": {
            "filename": validate_relative_path(manifest.archive.filename),
            "sha256": _validate_hash(manifest.archive.sha256, "archive.sha256"),
            "size": _validate_size(manifest.archive.size, "archive.size"),
        },
        "build": manifest.build_string,
        "channel": _validate_channel(manifest.channel),
        "deletions": [
            {"path": validate_relative_path(entry.path), "version": entry.version}
            for entry in sorted(manifest.deletions, key=lambda entry: entry.path)
        ],
        "files": [
            {
                "path": validate_relative_path(entry.path),
                "size": _validate_size(entry.size, "file.size"),
                **_file_check(entry, entry.path),
            }
            for entry in sorted(manifest.files, key=lambda entry: entry.path)
        ],
        "minimum_required_version": manifest.minimum_required_version,
        "release_notes": manifest.release_notes,
        "schema_version": manifest.schema_version,
        "version": manifest.version,
        "version_date": manifest.version_date,
    }
    if manifest.archive_prefix:
        data["archive_prefix"] = validate_relative_path(manifest.archive_prefix)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_manifest(
    build_dir,
    version,
    *,
    channel: str,
    archive: ArchiveInfo,
    build_string: str = "",
    version_date: str = "",
    minimum_required_version: str = "0",
    release_notes: str = "",
    deletions: Iterable[DeletionEntry] = (),
    exclusions=(),
) -> Manifest:
    """Build a manifest from managed files under *build_dir*."""
    build_dir = Path(build_dir).resolve()
    if not build_dir.is_dir():
        raise ManifestError(f"build_dir {str(build_dir)!r} is not a directory.")
    exclude = exclusions

    def is_excluded(relative_path: str) -> bool:
        if hasattr(exclude, "excludes"):
            return bool(exclude.excludes(relative_path))
        return any(relative_path.startswith(str(prefix).replace("\\", "/")) for prefix in exclude)

    entries = []
    for path in sorted(build_dir.rglob("*"), key=lambda item: item.relative_to(build_dir).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(build_dir).as_posix()
        if is_excluded(relative_path):
            continue
        entries.append(
            FileEntry(
                path=relative_path,
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return Manifest(
        schema_version=SCHEMA_VERSION,
        channel=_validate_channel(channel),
        version=str(version),
        build_string=str(build_string),
        version_date=str(version_date),
        minimum_required_version=str(minimum_required_version),
        release_notes=str(release_notes),
        archive=archive,
        files=tuple(entries),
        deletions=tuple(deletions),
    )
