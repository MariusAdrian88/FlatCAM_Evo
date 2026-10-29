"""Standalone restore-point validation for the FlatCAM updater."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


_REQUIRED_FIELDS = (
    "schema",
    "install_dir",
    "exe_name",
    "previous_version",
    "previous_build_string",
    "version",
    "build_string",
    "files",
    "delete",
)
_FILE_FIELDS = ("path", "size", "sha256")
_HASH_CHUNK_SIZE = 1024 * 1024
_HASH_RE = set("0123456789abcdefABCDEF")

# These names are excluded by Ticket 1's release policy or are updater/user
# state.  They must never become a restore payload or a delete target.
PROTECTED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "cache",
        "config",
        "dist",
        "doc",
        "docs",
        "freeze",
        "settings",
        "tests",
        "user_data",
        "user-settings",
        "user_settings",
        "userdata",
        "venv",
    }
)
PROTECTED_FILE_NAMES = frozenset(
    {
        "build.py",
        "build_freeze.py",
        "freeze.py",
        "make_freeze.py",
        "make_freezed.py",
        "setup_freeze.py",
    }
)
PROTECTED_SUFFIXES = (".flatconfig", ".flatprj")
UPDATER_RUNTIME_DIR = "updater"


def restore_dir_for_install(data_path, install_dir) -> Path:
    """Return the stable restore-point directory for an installation."""
    install_hash8 = hashlib.sha1(str(Path(install_dir)).encode()).hexdigest()[:8]
    return Path(data_path) / "update" / f"restore_{install_hash8}"


def blocked_release_path(restore_dir) -> Path:
    """Return the sibling marker used to block an explicitly reverted release."""
    restore_path = Path(restore_dir)
    return restore_path.with_name(restore_path.name + "_blocked.json")


def validate_relative_path(value: str, *, reject_protected: bool = True) -> str:
    """Return a canonical safe relative path or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.endswith("/")
        or PurePosixPath(normalized).is_absolute()
    ):
        raise ValueError("path must be a safe relative file path")

    windows_path = PureWindowsPath(normalized)
    if windows_path.is_absolute() or windows_path.anchor or windows_path.drive:
        raise ValueError("path must not be a Windows absolute path")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("path must not contain dot or empty segments")
    if reject_protected and is_protected_path(normalized):
        raise ValueError("path is protected")
    return "/".join(parts)


def is_protected_path(value: str) -> bool:
    """Return whether a managed-relative path is excluded or protected."""
    normalized = str(value).replace("\\", "/").casefold()
    parts = normalized.split("/")
    if not parts or not parts[0]:
        return True
    protected_names = {name.casefold() for name in PROTECTED_DIR_NAMES}
    for index, part in enumerate(parts[:-1]):
        # The user ``config`` is top-level; ``lib/fontTools/config`` is a
        # library package of the frozen build.
        if part == "config" and index > 0:
            continue
        if part in protected_names:
            return True
    filename = parts[-1]
    if filename in {name.casefold() for name in PROTECTED_DIR_NAMES | PROTECTED_FILE_NAMES}:
        return True
    return filename.endswith(PROTECTED_SUFFIXES)


def is_protected_deletion(value: str) -> bool:
    """Return whether a deletion path is protected.

    An update replaces the files of the frozen updater runtime (the top-level
    ``updater`` directory beside FlatCAM.exe), but it never deletes them.
    """
    normalized = str(value).replace("\\", "/").casefold()
    return is_protected_path(normalized) or normalized.split("/")[0] == UPDATER_RUNTIME_DIR


def _resolve(path: Path, label: str) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((_path_key(path), _path_key(parent))) == _path_key(parent)
    except ValueError:
        return False


def _reject_symlink_components(path: Path, parent: Path | None = None) -> None:
    """Reject symlink components instead of ever following them."""
    path = Path(path)
    if not path.is_absolute():
        path = Path(os.path.abspath(path))
    if parent is None:
        parent = Path(path.anchor or os.sep)
    parent = Path(parent)
    if not parent.is_absolute():
        parent = Path(os.path.abspath(parent))
    try:
        relative = path.relative_to(parent)
    except ValueError as exc:
        raise ValueError("path is outside its parent") from exc

    cursor = parent
    if cursor.is_symlink():
        raise ValueError("path parent must not be a symlink")
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError("path must not contain symlink components")


def safe_join(parent: Path, relative: str, *, label: str = "path") -> Path:
    """Join a managed-relative path while enforcing containment and no symlinks."""
    normalized = validate_relative_path(relative)
    parent = Path(parent)
    _reject_symlink_components(parent)
    candidate = parent.joinpath(*normalized.split("/"))
    _reject_symlink_components(candidate, parent)
    resolved_parent = _resolve(parent, f"{label} parent")
    resolved_candidate = _resolve(candidate, label)
    if not _is_within(resolved_candidate, resolved_parent):
        raise ValueError(f"{label} escapes its parent")
    return candidate


def _verify_backup_file(path: Path, expected_size: int, expected_hash: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"restore payload is missing or not a file: {path}")
    if path.stat().st_size != expected_size:
        raise ValueError(f"restore payload size mismatch: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as payload:
        while chunk := payload.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    if digest.hexdigest().casefold() != expected_hash.casefold():
        raise ValueError(f"restore payload hash mismatch: {path}")


def _check_unique_path(path: str, seen_paths: set[str]) -> None:
    key = path.casefold()
    if key in seen_paths:
        raise ValueError("restore file and delete paths must be unique")
    seen_paths.add(key)


def _validate_metadata_fields(metadata: dict) -> None:
    missing = [field for field in _REQUIRED_FIELDS if field not in metadata]
    if missing:
        raise ValueError("restore metadata is missing a required field")
    if type(metadata["schema"]) is not int or metadata["schema"] != 1:
        raise ValueError("restore metadata schema must be integer 1")
    if not isinstance(metadata["install_dir"], str) or not Path(metadata["install_dir"]).is_absolute():
        raise ValueError("restore metadata install_dir must be absolute")
    if not isinstance(metadata["exe_name"], str):
        raise ValueError("restore metadata exe_name must be a string")
    if metadata["exe_name"]:
        exe_name = Path(metadata["exe_name"])
        if exe_name.name != metadata["exe_name"] or metadata["exe_name"] in (".", ".."):
            raise ValueError("restore metadata exe_name must be a file name")
    for field in ("previous_version", "previous_build_string", "version", "build_string"):
        if not isinstance(metadata[field], str):
            raise ValueError(f"restore metadata {field} must be a string")
    if metadata.get("mode") is not None and metadata["mode"] not in ("source", "frozen"):
        raise ValueError("restore metadata mode is invalid")
    if not isinstance(metadata["files"], list):
        raise ValueError("restore metadata files must be a list")
    if not isinstance(metadata["delete"], list):
        raise ValueError("restore metadata delete must be a list")


def _validate_entries(metadata: dict) -> tuple[list[tuple], list[tuple]]:
    file_entries = []
    for entry in metadata["files"]:
        if not isinstance(entry, dict) or any(field not in entry for field in _FILE_FIELDS):
            raise ValueError("restore file entry is malformed")
        path = validate_relative_path(entry["path"])
        size = entry["size"]
        if type(size) is not int or size < 0:
            raise ValueError("restore file size must be a non-negative integer")
        expected_hash = entry["sha256"]
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or not set(expected_hash) <= _HASH_RE
        ):
            raise ValueError("restore file sha256 must be 64 hexadecimal characters")
        file_entries.append((path, size, expected_hash))

    delete_paths = []
    for entry in metadata["delete"]:
        path = validate_relative_path(entry)
        delete_paths.append(path)
    return file_entries, delete_paths


def load_restore_point(restore_dir, install_dir=None, verify_files=True) -> dict:
    """Load and validate a restore point without changing any files."""
    restore_path = Path(restore_dir)
    if not restore_path.is_absolute():
        raise ValueError("restore directory must be absolute")
    _reject_symlink_components(restore_path)
    if not restore_path.is_dir():
        raise OSError(f"restore directory is missing: {restore_path}")
    metadata_path = restore_path / "restore.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise OSError(f"restore metadata is missing: {metadata_path}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid restore metadata: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("restore metadata must be a JSON object")
    _validate_metadata_fields(metadata)

    record_install = Path(metadata["install_dir"])
    _reject_symlink_components(record_install)
    install_root = _resolve(record_install, "install directory")
    if not install_root.is_dir() or record_install.is_symlink():
        raise ValueError("restore metadata install_dir must be an existing directory")
    if install_dir is not None:
        supplied = Path(install_dir)
        _reject_symlink_components(supplied)
        if _path_key(_resolve(supplied, "install_dir argument")) != _path_key(install_root):
            raise ValueError("restore metadata installation mismatch")

    restore_root = _resolve(restore_path, "restore directory")
    if _is_within(restore_root, install_root) or _is_within(install_root, restore_root):
        raise ValueError("restore directory and installation must not contain each other")

    files_root = restore_path / "files"
    if files_root.is_symlink() or not files_root.is_dir():
        raise ValueError("restore files directory must be a real directory")
    _reject_symlink_components(files_root, restore_path)
    file_entries, delete_paths = _validate_entries(metadata)
    seen_paths: set[str] = set()
    for path, size, expected_hash in file_entries:
        _check_unique_path(path, seen_paths)
        safe_join(install_root, path, label="restore file path")
        backup_path = safe_join(files_root, path, label="restore payload path")
        if verify_files:
            _verify_backup_file(backup_path, size, expected_hash)
    for path in delete_paths:
        _check_unique_path(path, seen_paths)
        safe_join(install_root, path, label="restore delete path")

    exe_name = metadata["exe_name"]
    if exe_name:
        exe_path = safe_join(install_root, exe_name, label="installation executable")
        if exe_path.exists():
            if exe_path.is_symlink() or not exe_path.is_file():
                raise ValueError("installation executable must be a file")
        elif not any(path.casefold() == exe_name.casefold() for path, _, _ in file_entries):
            if metadata.get("mode") != "source":
                raise ValueError("installation executable is missing from restore contract")

    return metadata


def is_release_blocked(restore_dir, version, build_string) -> bool:
    """Return whether a sibling marker exactly blocks a release."""
    marker = blocked_release_path(restore_dir)
    try:
        if marker.is_symlink() or not marker.is_file():
            return False
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and isinstance(data.get("version"), str)
        and isinstance(data.get("build_string"), str)
        and data["version"] == str(version)
        and data["build_string"] == str(build_string)
    )
