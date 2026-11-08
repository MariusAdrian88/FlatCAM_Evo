"""Path rules and release records of the FlatCAM updater (standard library only)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath


_RECORD_FIELDS = ("install_dir", "previous_version", "previous_build_string", "version", "build_string")

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
# the work folder of the updater inside the installation: a rename from it into place stays on one volume, and the
# extracted files get the access rights of the installation folder (a move from the user profile keeps its rights)
WORK_DIR_NAME = ".flatcam-update"
PROTECTED_DIR_NAMES = PROTECTED_DIR_NAMES | {WORK_DIR_NAME}


def restore_dir_for_install(data_path, install_dir) -> Path:
    """Return the stable base path of the update records of an installation.

    The records (``_previous.json``, ``_blocked.json``) are siblings of this path. Releases up to 1.05 kept a
    restore point with a copy of all files in this directory; the updater removes such a directory.
    """
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


def previous_release_path(restore_dir) -> Path:
    """Return the sibling record of the release that the last update replaced (the target of Revert Update)."""
    restore_path = Path(restore_dir)
    return restore_path.with_name(restore_path.name + "_previous.json")


def read_previous_release(restore_dir, install_dir) -> dict:
    """Read the record of the previous release for this installation; raise when there is no usable record.

    The record holds no files: Revert Update downloads the previous release from GitHub and installs it.
    """
    path = previous_release_path(restore_dir)
    if path.is_symlink() or not path.is_file():
        raise OSError(f"no previous release is recorded: {path}")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid previous release record: {exc}") from exc
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError("previous release record schema must be integer 1")
    for field in _RECORD_FIELDS:
        if not isinstance(record.get(field), str):
            raise ValueError(f"previous release record {field} must be a string")
    if not record["previous_version"]:
        raise ValueError("previous release record has no version")
    added = record.get("added", [])
    if not isinstance(added, list):
        raise ValueError("previous release record added must be a list")
    record["added"] = [validate_relative_path(item) for item in added]
    if _path_key(_resolve(Path(record["install_dir"]), "install directory")) != _path_key(
            _resolve(Path(install_dir), "install_dir argument")):
        raise ValueError("previous release record installation mismatch")
    return record


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
