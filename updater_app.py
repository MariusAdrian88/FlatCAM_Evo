"""Standalone stdlib-only FlatCAM update applier.

The launcher copies this file and ``recovery.py`` (or, frozen, the updater/
runtime) to a temporary directory before starting it, so the apply step can
replace the installed copy.  No GUI or application imports are allowed here.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

try:
    from services.updater.recovery import (
        blocked_release_path,
        is_protected_deletion,
        is_release_blocked,
        load_restore_point,
        safe_join,
        validate_relative_path,
    )
except ImportError:  # source helper copied beside recovery.py
    from recovery import (  # type: ignore[no-redef]
        blocked_release_path,
        is_protected_deletion,
        is_release_blocked,
        load_restore_point,
        safe_join,
        validate_relative_path,
    )


RETRY_BACKOFF = (0.1, 0.2, 0.4)
FILE_UNLOCK_BACKOFF = (0.1, 0.2, 0.4, 0.8)
_IS_WINDOWS = sys.platform == "win32"
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259
_LOG = logging.getLogger("flatcam-updater")
_JOB_FIELDS = frozenset(
    {
        "schema",
        "action",
        "mode",
        "install_dir",
        "archive_path",
        "staging_dir",
        "backup_dir",
        "restore_dir",
        "manifest",
        "manifest_path",
        "exe_name",
        "previous_version",
        "previous_build_string",
        "wait_pid",
        "relaunch",
        "relaunch_argv",
        "startup_ack_path",
        "log_path",
        "runtime_dir",
        "lock_file_path",
        "interactive",
    }
)
_CONTROL_NAMES = frozenset(
    {
        "update_job.json",
        "rollback_job.json",
        ".updater_started",
        ".rollback_ready.json",
        ".rollback_cancelled",
    }
)


def _log(level: str, message: str, *args) -> None:
    method = getattr(_LOG, level)
    method(message, *args)


def setup_logging(log_path: str) -> None:
    """Configure one file logger after its destination has been validated."""
    for handler in list(_LOG.handlers):
        _LOG.removeHandler(handler)
        handler.close()
    _LOG.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _LOG.addHandler(handler)


def load_job(job_path: str) -> dict:
    with Path(job_path).open("r", encoding="utf-8") as stream:
        job = json.load(stream)
    if not isinstance(job, dict):
        raise ValueError("job must be a JSON object")
    return job


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_valid(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _paths_overlap(first: Path, second: Path) -> bool:
    first = Path(first).resolve()
    second = Path(second).resolve()
    return (
        _path_key(first) == _path_key(second)
        or first in second.parents
        or second in first.parents
    )


def _reject_symlink_components(path: Path, parent: Path | None = None) -> None:
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
    if parent.is_symlink():
        raise ValueError("path parent must not be a symlink")
    cursor = parent
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError("path must not contain symlink components")


def _validate_absolute_path(value, label: str, *, allow_missing=True) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be a path string")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")
    _reject_symlink_components(path)
    if path.exists() and path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    if not allow_missing and not path.exists():
        raise OSError(f"{label} does not exist: {path}")
    return path


def _validate_manifest(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("manifest must be a JSON object")
    required = (
        "schema_version",
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
    if any(field not in raw for field in required):
        raise ValueError("manifest is missing a required field")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("unsupported manifest schema")
    if raw["channel"] not in ("windows", "linux"):
        raise ValueError("unsupported manifest channel")
    for field in ("version", "build", "version_date", "minimum_required_version", "release_notes"):
        if not isinstance(raw[field], str):
            raise ValueError(f"manifest {field} must be a string")

    archive = raw["archive"]
    if not isinstance(archive, dict) or any(
        field not in archive for field in ("filename", "size", "sha256")
    ):
        raise ValueError("manifest archive is malformed")
    archive_name = validate_relative_path(archive["filename"])
    if type(archive["size"]) is not int or archive["size"] < 0 or not _hash_valid(archive["sha256"]):
        raise ValueError("manifest archive contract is malformed")

    files = []
    seen: set[str] = set()
    for entry in raw["files"]:
        if not isinstance(entry, dict) or any(
            field not in entry for field in ("path", "size", "sha256")
        ):
            raise ValueError("manifest file entry is malformed")
        path = validate_relative_path(entry["path"])
        if path.casefold() in seen:
            raise ValueError("manifest contains duplicate file paths")
        seen.add(path.casefold())
        if type(entry["size"]) is not int or entry["size"] < 0 or not _hash_valid(entry["sha256"]):
            raise ValueError("manifest file contract is malformed")
        files.append(
            {"path": path, "size": entry["size"], "sha256": entry["sha256"].lower()}
        )

    deletions = []
    for entry in raw["deletions"]:
        if not isinstance(entry, dict) or "path" not in entry or "version" not in entry:
            raise ValueError("manifest deletion entry is malformed")
        path = validate_relative_path(entry["path"])
        if is_protected_deletion(path):
            raise ValueError("path is protected")
        if path.casefold() in seen:
            raise ValueError("manifest file and deletion paths overlap")
        seen.add(path.casefold())
        if not isinstance(entry["version"], str):
            raise ValueError("manifest deletion version must be a string")
        deletions.append({"path": path, "version": entry["version"]})
    return {
        "schema_version": 1,
        "channel": raw["channel"],
        "version": raw["version"],
        "build": raw["build"],
        "version_date": raw["version_date"],
        "minimum_required_version": raw["minimum_required_version"],
        "release_notes": raw["release_notes"],
        "archive": {"filename": archive_name, "size": archive["size"], "sha256": archive["sha256"].lower()},
        "files": files,
        "deletions": deletions,
    }


def _manifest_from_job(job: dict) -> dict:
    if "manifest" in job:
        return _validate_manifest(job["manifest"])
    manifest_path = _validate_absolute_path(job.get("manifest_path"), "manifest_path", allow_missing=False)
    try:
        return _validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest file: {exc}") from exc


def _validate_relaunch_argv(value) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("relaunch_argv must be a non-empty list")
    if any(not isinstance(item, str) or not item or "\x00" in item for item in value):
        raise ValueError("relaunch_argv must contain non-empty strings")
    return list(value)


def _validate_job(job: dict, job_path: Path | None = None) -> dict:
    if not isinstance(job, dict):
        raise ValueError("job must be a JSON object")
    unknown = set(job) - _JOB_FIELDS
    if unknown:
        raise ValueError(f"job contains unknown fields: {sorted(unknown)!r}")
    if type(job.get("schema")) is not int or job["schema"] != 1:
        raise ValueError("job schema must be integer 1")
    action = job.get("action", "update")
    if action not in ("update", "rollback"):
        raise ValueError("job action is invalid")
    if job.get("mode") not in ("source", "frozen"):
        raise ValueError("job mode is invalid")
    common = (
        "install_dir",
        "restore_dir",
        "exe_name",
        "wait_pid",
        "relaunch",
        "relaunch_argv",
        "startup_ack_path",
        "log_path",
    )
    for field in common:
        if field not in job:
            raise ValueError(f"job is missing {field}")
    install = _validate_absolute_path(job["install_dir"], "install_dir", allow_missing=False)
    if not install.is_dir():
        raise ValueError("install_dir must be a directory")
    restore = _validate_absolute_path(job["restore_dir"], "restore_dir")
    if _paths_overlap(restore, install):
        raise ValueError("restore_dir must be outside install_dir")
    if not isinstance(job["exe_name"], str):
        raise ValueError("exe_name must be a string")
    for field in ("previous_version", "previous_build_string"):
        if field in job and not isinstance(job[field], str):
            raise ValueError(f"{field} must be a string")
    if job["exe_name"]:
        if Path(job["exe_name"]).name != job["exe_name"]:
            raise ValueError("exe_name must be a file name")
        if job["mode"] == "frozen" and job["exe_name"].casefold() != "flatcam.exe":
            raise ValueError("frozen jobs require FlatCAM.exe")
    if type(job["wait_pid"]) is not int or job["wait_pid"] < 0:
        raise ValueError("wait_pid must be a non-negative integer")
    if not isinstance(job["relaunch"], bool):
        raise ValueError("relaunch must be a boolean")
    relaunch_argv = _validate_relaunch_argv(job["relaunch_argv"])

    paths = {"restore_dir": restore}
    for field in ("startup_ack_path", "log_path", "runtime_dir"):
        if field not in job:
            if field == "runtime_dir":
                continue
            raise ValueError(f"job is missing {field}")
        paths[field] = _validate_absolute_path(job[field], field)
        if _paths_overlap(paths[field], install):
            raise ValueError(f"{field} must be outside install_dir")
    if "lock_file_path" in job:
        lock = _validate_absolute_path(job["lock_file_path"], "lock_file_path")
        if _paths_overlap(lock, install):
            raise ValueError("lock_file_path must be outside install_dir")
        paths["lock_file_path"] = lock
    if job_path is not None:
        job_path = _validate_absolute_path(str(job_path), "job_path", allow_missing=False)
        if _paths_overlap(job_path, install):
            raise ValueError("job file must be outside install_dir")

    if action == "update":
        for field in ("archive_path", "staging_dir", "backup_dir"):
            if field not in job:
                raise ValueError(f"update job is missing {field}")
        if "manifest" not in job and "manifest_path" not in job:
            raise ValueError("update job is missing manifest or manifest_path")
        archive = _validate_absolute_path(job["archive_path"], "archive_path", allow_missing=False)
        if not archive.is_file():
            raise ValueError("archive_path must be a regular file")
        if _paths_overlap(archive, install):
            raise ValueError("archive_path must be outside install_dir")
        for field in ("staging_dir", "backup_dir"):
            path = _validate_absolute_path(job[field], field)
            if _paths_overlap(path, install):
                raise ValueError(f"{field} must be outside install_dir")
            paths[field] = path
        work_paths = [paths[field] for field in ("staging_dir", "backup_dir", "restore_dir")]
        if "runtime_dir" in paths:
            work_paths.append(paths["runtime_dir"])
        if any(_paths_overlap(archive, path) for path in work_paths):
            raise ValueError("archive and updater work paths overlap")
        for index, first in enumerate(work_paths):
            if any(_paths_overlap(first, second) for second in work_paths[index + 1:]):
                raise ValueError("updater work paths overlap")
        if "manifest_path" in job:
            manifest_path = _validate_absolute_path(job["manifest_path"], "manifest_path", allow_missing=False)
            if _paths_overlap(manifest_path, install) or any(_paths_overlap(manifest_path, path) for path in work_paths):
                raise ValueError("manifest_path overlaps a protected path")
        for path in (paths["startup_ack_path"], paths["log_path"]):
            if any(_paths_overlap(path, work) for work in work_paths):
                raise ValueError("job protocol path overlaps update work")
        manifest = _manifest_from_job(job)
        if job["mode"] == "frozen":
            executable = install / job["exe_name"]
            if not executable.is_file() or executable.is_symlink():
                raise ValueError("frozen application executable is missing")
        paths["archive_path"] = archive
        return {"job": job, "install": install, "paths": paths, "manifest": manifest, "argv": relaunch_argv}

    for field in ("staging_dir", "backup_dir"):
        if field in job:
            path = _validate_absolute_path(job[field], field)
            if _paths_overlap(path, install) or _paths_overlap(path, restore):
                raise ValueError(f"{field} overlaps a protected path")
            paths[field] = path
    if "runtime_dir" in paths and _paths_overlap(paths["runtime_dir"], restore):
        raise ValueError("runtime_dir overlaps restore_dir")
    for field in ("startup_ack_path", "log_path"):
        if _paths_overlap(paths[field], restore):
            raise ValueError(f"{field} overlaps restore_dir")
    if not restore.is_dir():
        raise OSError("restore_dir does not exist")
    return {"job": job, "install": install, "paths": paths, "manifest": None, "argv": relaunch_argv}


def _validate_archive(path: Path, contract: dict) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("archive is not a regular file")
    if path.stat().st_size != contract["size"]:
        raise ValueError("archive size does not match manifest")
    if _hash_file(path) != contract["sha256"]:
        raise ValueError("archive hash does not match manifest")


def validate_install_dir(job: dict) -> tuple[bool, str]:
    """Return a validation result without writing files or starting processes."""
    try:
        _validate_job(job)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return False, str(exc)
    return True, ""


def _archive_members(archive_path: Path, manifest: dict) -> dict[str, zipfile.ZipInfo]:
    expected = {entry["path"].casefold(): entry for entry in manifest["files"]}
    members = {}
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                raw_name = info.filename
                is_directory = info.is_dir() or raw_name.endswith("/")
                name = raw_name.rstrip("/") if is_directory else raw_name
                if not name:
                    continue
                normalized = validate_relative_path(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError(f"ZIP symlink is not allowed: {raw_name}")
                if is_directory:
                    continue
                if len(Path(normalized).parts) == 1 and normalized in _CONTROL_NAMES:
                    raise ValueError(f"ZIP uses a reserved control path: {normalized}")
                key = normalized.casefold()
                if key in members:
                    raise ValueError(f"ZIP contains duplicate path: {normalized}")
                if key not in expected:
                    raise ValueError(f"ZIP contains unmanaged path: {normalized}")
                members[key] = info
            if set(members) != set(expected):
                missing = sorted(set(expected) - set(members))
                raise ValueError(f"ZIP is missing managed files: {missing!r}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"archive is not a valid ZIP: {exc}") from exc
    return members


def _reset_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"work path is not a directory: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _extract_archive(archive_path: Path, staging_dir: Path, manifest: dict) -> None:
    members = _archive_members(archive_path, manifest)
    _reset_directory(staging_dir)
    entries = {entry["path"].casefold(): entry for entry in manifest["files"]}
    with zipfile.ZipFile(archive_path, "r") as archive:
        for key, entry in sorted(entries.items()):
            destination = safe_join(staging_dir, entry["path"], label="staged file path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            info = members[key]
            digest = hashlib.sha256()
            written = 0
            with archive.open(info, "r") as source, destination.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                target.flush()
                os.fsync(target.fileno())
            if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError(f"staged file checksum mismatch: {entry['path']}")
            permissions = (info.external_attr >> 16) & 0o777
            if permissions:
                os.chmod(destination, permissions)


def _is_staging_metadata(path: Path) -> bool:
    return len(path.parts) == 1 and path.name in _CONTROL_NAMES


def _contract_files(files) -> dict[str, dict]:
    if files is None:
        return {}
    if not isinstance(files, list):
        raise OSError("files contract must be a list")
    result = {}
    for entry in files:
        if not isinstance(entry, dict) or any(field not in entry for field in ("path", "size", "sha256")):
            raise OSError("malformed file contract entry")
        try:
            path = validate_relative_path(entry["path"])
        except (TypeError, ValueError) as exc:
            raise OSError(f"malformed file contract entry: {exc}") from exc
        if type(entry["size"]) is not int or entry["size"] < 0 or not _hash_valid(entry["sha256"]):
            raise OSError(f"malformed file contract entry: {path}")
        if path.casefold() in result:
            raise OSError(f"duplicate file contract entry: {path}")
        result[path.casefold()] = {"path": path, "size": entry["size"], "sha256": entry["sha256"].lower()}
    return result


def _staged_files(staging_dir: Path, expected: dict[str, dict] | None, job_file_path: Path | None) -> dict[str, Path]:
    result = {}
    if not staging_dir.exists():
        return result
    _reject_symlink_components(staging_dir)
    for root, dirs, filenames in os.walk(staging_dir, followlinks=False):
        root_path = Path(root)
        symlink_dirs = [name for name in dirs if (root_path / name).is_symlink()]
        if symlink_dirs:
            raise OSError("staging directory contains a symlink")
        for filename in filenames:
            source = root_path / filename
            if source.is_symlink():
                raise OSError("staging file is a symlink")
            rel = source.relative_to(staging_dir)
            if _is_staging_metadata(rel) or (job_file_path and source.resolve() == job_file_path.resolve()):
                continue
            normalized = validate_relative_path(rel.as_posix())
            key = normalized.casefold()
            if expected is not None and key not in expected:
                raise OSError(f"staged path is not in update contract: {normalized}")
            if key in result:
                raise OSError(f"duplicate staged path: {normalized}")
            result[key] = source
    if expected is not None:
        for key, entry in expected.items():
            source = result.get(key)
            if source is None:
                raise OSError(f"expected staged file is missing: {entry['path']}")
            if source.stat().st_size != entry["size"] or _hash_file(source) != entry["sha256"]:
                raise OSError(f"staged file checksum mismatch: {entry['path']}")
    return result


def _attach_completed(exc: BaseException, completed: list[dict]) -> None:
    try:
        exc.completed_ops = completed  # type: ignore[attr-defined]
    except Exception:
        pass


def _copy_or_move_target(install_dir: Path, backup_dir: Path, rel: str, source: Path | None, completed: list[dict]) -> None:
    target = safe_join(install_dir, rel, label="install path")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise OSError(f"install target is not a regular file: {target}")
        backup = safe_join(backup_dir, rel, label="backup path")
        backup.parent.mkdir(parents=True, exist_ok=True)
        op = {"type": "replace" if source is not None else "delete", "rel": rel, "dst": str(target), "backup": str(backup)}
        try:
            if backup.exists() or backup.is_symlink():
                raise OSError(f"backup target already exists: {backup}")
            _retry_op("backup move", shutil.move, str(target), str(backup))
        except Exception as exc:
            _attach_completed(exc, completed)
            raise
        completed.append(op)
    elif source is not None:
        op = {"type": "add", "rel": rel, "dst": str(target)}
        completed.append(op)

    if source is not None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _retry_op("staged copy", shutil.copy2, str(source), str(target))
        except Exception as exc:
            _attach_completed(exc, completed)
            raise


def _retry_op(name: str, function, *args) -> None:
    last = None
    for delay in (*RETRY_BACKOFF, None):
        try:
            function(*args)
            return
        except OSError as exc:
            last = exc
            if delay is not None:
                time.sleep(delay)
    raise last  # type: ignore[misc]


def apply_update(
    install_dir: Path,
    staging_dir: Path,
    backup_dir: Path,
    delete_list: list[str],
    job_file_path: Path | None = None,
    files: list[dict] | None = None,
) -> list[dict]:
    """Apply staged files and deletions, recording enough for reverse rollback."""
    completed: list[dict] = []
    try:
        install_dir = _validate_absolute_path(str(install_dir), "install_dir", allow_missing=False)
        staging_dir = _validate_absolute_path(str(staging_dir), "staging_dir")
        backup_dir = _validate_absolute_path(str(backup_dir), "backup_dir")
        if _paths_overlap(install_dir, staging_dir) or _paths_overlap(install_dir, backup_dir):
            raise OSError("update work path overlaps install_dir")
        expected = _contract_files(files) if files is not None else None
        staged = _staged_files(staging_dir, expected, job_file_path)
        deletes = {}
        for raw in delete_list or []:
            rel = validate_relative_path(raw)
            deletes[rel.casefold()] = rel
        if backup_dir.exists():
            if backup_dir.is_symlink() or not backup_dir.is_dir():
                raise OSError("backup_dir is not a directory")
            if any(backup_dir.iterdir()):
                raise OSError("backup_dir must be empty")
        else:
            backup_dir.mkdir(parents=True, exist_ok=True)

        all_keys = set(deletes) | set(staged)
        for key in sorted(all_keys):
            rel = expected[key]["path"] if expected and key in expected else staged[key].relative_to(staging_dir).as_posix() if key in staged else deletes[key]
            _copy_or_move_target(
                install_dir,
                backup_dir,
                rel,
                staged.get(key),
                completed,
            )
        return completed
    except Exception as exc:
        _attach_completed(exc, completed)
        raise


def _verify_final(install_dir: Path, files: list[dict], delete_list: list[str]) -> None:
    for entry in files:
        rel = validate_relative_path(entry["path"])
        target = safe_join(install_dir, rel, label="installed path")
        if target.is_symlink() or not target.is_file():
            raise OSError(f"installed file is missing: {rel}")
        if target.stat().st_size != entry["size"] or _hash_file(target) != entry["sha256"].lower():
            raise OSError(f"installed file checksum mismatch: {rel}")
    for raw in delete_list:
        rel = validate_relative_path(raw)
        target = safe_join(install_dir, rel, label="deleted path")
        if target.exists() or target.is_symlink():
            raise OSError(f"obsolete file remains: {rel}")


def rollback(install_dir: Path, backup_dir: Path, completed_ops: list[dict]) -> bool:
    """Reverse completed operations in reverse order; false means unsafe to relaunch."""
    success = True
    for operation in reversed(completed_ops):
        target = Path(operation["dst"])
        try:
            if "backup" in operation:
                backup = Path(operation["backup"])
                if backup.is_symlink() or not backup.is_file():
                    raise OSError(f"rollback backup is missing: {backup}")
                if target.exists() or target.is_symlink():
                    if target.is_dir() and not target.is_symlink():
                        raise OSError(f"rollback target is a directory: {target}")
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(target))
            elif target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    raise OSError(f"rollback target is a directory: {target}")
                target.unlink()
        except OSError as exc:
            _log("error", "Rollback failed for %s: %s", target, exc)
            success = False
    return success


def _restore_operation_records(completed_ops: list[dict]) -> tuple[list[dict], list[str]]:
    records: dict[str, dict] = {}
    for operation in completed_ops:
        rel = validate_relative_path(str(operation["rel"]))
        record = records.setdefault(rel.casefold(), {"path": rel})
        if "backup" in operation:
            record["backup"] = operation["backup"]
        elif "backup" not in record:
            record["new"] = True
    files = []
    deletes = []
    for record in records.values():
        if "backup" in record:
            backup = Path(record["backup"])
            files.append({"path": record["path"], "source": backup})
        elif record.get("new"):
            deletes.append(record["path"])
    return files, sorted(deletes)


def _write_restore_metadata(path: Path, metadata: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def retain_restore_point(job: dict, completed_ops: list[dict]) -> None:
    """Atomically publish the one restore point representing the prior release."""
    install = Path(job["install_dir"]).resolve()
    restore = Path(job["restore_dir"]).resolve()
    parent = restore.parent
    _reject_symlink_components(parent)
    parent.mkdir(parents=True, exist_ok=True)
    records, delete_paths = _restore_operation_records(completed_ops)
    candidate = Path(tempfile.mkdtemp(prefix=restore.name + ".pending-", dir=str(parent)))
    published = False
    previous = None
    try:
        files_root = candidate / "files"
        files_root.mkdir()
        entries = []
        for record in sorted(records, key=lambda item: item["path"]):
            source = Path(record["source"])
            if source.is_symlink() or not source.is_file():
                raise OSError(f"restore backup is missing: {source}")
            destination = safe_join(files_root, record["path"], label="restore payload path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            entries.append({"path": record["path"], "size": destination.stat().st_size, "sha256": _hash_file(destination)})
        metadata = {
            "schema": 1,
            "install_dir": str(install),
            "mode": job["mode"],
            "exe_name": job["exe_name"],
            "previous_version": str(job.get("previous_version", "")),
            "previous_build_string": str(job.get("previous_build_string", "")),
            "version": str(job["manifest"]["version"]),
            "build_string": str(job["manifest"]["build"]),
            "files": entries,
            "delete": delete_paths,
            "relaunch_argv": list(job["relaunch_argv"]),
        }
        _write_restore_metadata(candidate / "restore.json", metadata)
        load_restore_point(candidate, install, verify_files=True)
        if restore.exists():
            if restore.is_symlink() or not restore.is_dir():
                raise OSError("restore_dir is not a real directory")
            previous = parent / f"{restore.name}.previous-{uuid.uuid4().hex}"
            os.replace(restore, previous)
        try:
            os.replace(candidate, restore)
            published = True
        except Exception as exc:
            if previous is not None:
                os.replace(previous, restore)
                previous = None
            raise exc
        if previous is not None:
            try:
                shutil.rmtree(previous)
            except OSError:
                _log("warning", "Could not remove superseded restore point: %s", previous)
    finally:
        if not published and candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)


def _default_relaunch_argv() -> list[str]:
    if _IS_WINDOWS:
        return [str(sys.executable), *sys.argv[1:]]
    return [str(sys.executable), *sys.argv]


def _write_blocked_release_marker(restore_dir: Path, metadata: dict) -> None:
    marker = blocked_release_path(restore_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": metadata["version"], "build_string": metadata["build_string"]}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_marker(marker: Path, previous: bytes | None) -> bool:
    try:
        if previous is None:
            marker.unlink(missing_ok=True)
        else:
            marker.write_bytes(previous)
        return True
    except OSError:
        return False


def _consume_restore_point(restore: Path) -> Path:
    consumed = restore.parent / f"{restore.name}.consumed-{uuid.uuid4().hex}"
    os.replace(restore, consumed)
    return consumed


def _cleanup_tree(path: Path | None) -> None:
    if path is None:
        return
    # a frozen updater runs from its runtime copy: a partial delete can remove modules it still loads.
    # The launcher removes the old job folder at the next update.
    if getattr(sys, "frozen", False) and _paths_overlap(Path(sys.executable), path):
        return
    try:
        if path.exists() and not path.is_symlink():
            shutil.rmtree(path)
    except OSError as exc:
        _log("warning", "Cleanup failed for %s: %s", path, exc)


def _validate_rollback_job(job: dict) -> dict:
    return _validate_job(job)


def _rollback_job_from_restore(restore_arg: str) -> dict:
    restore = _validate_absolute_path(restore_arg, "restore_dir", allow_missing=False)
    metadata = load_restore_point(restore, verify_files=False)
    install = Path(metadata["install_dir"]).resolve()
    job_root = restore.parent / f"rollback_job_{uuid.uuid4().hex}"
    job_root.mkdir(parents=True, exist_ok=False)
    argv = metadata.get("relaunch_argv")
    if not isinstance(argv, list) or not argv:
        argv = _default_relaunch_argv()
    return {
        "schema": 1,
        "action": "rollback",
        "mode": metadata.get("mode", "source"),
        "install_dir": str(install),
        "restore_dir": str(restore),
        "staging_dir": str(job_root / "staging"),
        "backup_dir": str(job_root / "backup"),
        "exe_name": metadata["exe_name"],
        "wait_pid": 0,
        "relaunch": True,
        "relaunch_argv": argv,
        "startup_ack_path": str(job_root / ".updater_started"),
        "log_path": str(job_root / "updater.log"),
        "runtime_dir": str(job_root),
    }


def _write_startup_ack(job: dict) -> None:
    path = Path(job["startup_ack_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("started", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_startup_ack(job: dict) -> None:
    """Public compatibility wrapper used by process-handoff tests."""
    _write_startup_ack(job)


def _pid_alive(pid: int) -> bool:
    if _IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = kernel32.GetLastError()
            if error == 87:
                return False
            raise OSError(error, f"could not query PID {pid}")
        code = ctypes.c_ulong(0)
        try:
            if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                raise OSError(kernel32.GetLastError(), f"could not query PID {pid}")
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == _STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_pid_exit(pid: int, timeout: float = 60) -> None:
    if pid <= 0:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)


def wait_for_file_unlock(exe_path: Path, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    delays = iter(FILE_UNLOCK_BACKOFF)
    while time.monotonic() < deadline:
        try:
            os.rename(exe_path, exe_path)
            with exe_path.open("r+b"):
                pass
            return True
        except OSError:
            time.sleep(next(delays, 0.1))
    return False


def _remove_update_lock(job: dict) -> None:
    path_value = job.get("lock_file_path")
    if not path_value or job.get("wait_pid", 0) <= 0:
        return
    path = _validate_absolute_path(path_value, "lock_file_path")
    if path.name != "FlatCAM.lock":
        raise ValueError("invalid application lock path")
    path.unlink(missing_ok=True)


def relaunch_app(argv, install_dir: Path) -> None:
    if isinstance(argv, (str, Path)):
        argv = [str(argv)]
    kwargs = {"cwd": str(install_dir), "close_fds": True}
    if _IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(list(argv), **kwargs)


def restore_previous_version(job: dict) -> int:
    """Restore and consume a validated retained point."""
    try:
        validated = _validate_rollback_job(job)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return 3
    install = validated["install"]
    paths = validated["paths"]
    restore = paths["restore_dir"] if "restore_dir" in paths else Path(job["restore_dir"])
    try:
        metadata = load_restore_point(restore, install, verify_files=True)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        _log("error", "Could not load restore point: %s", exc)
        return 3
    if job["exe_name"] != metadata["exe_name"]:
        return 3
    if _rollback_cancelled(job):
        return 2
    try:
        wait_for_pid_exit(job["wait_pid"], timeout=60)
        if job["wait_pid"] > 0 and _pid_alive(job["wait_pid"]):
            return 2
        _remove_update_lock(job)
        if job["exe_name"]:
            exe = safe_join(install, job["exe_name"], label="application executable")
            if exe.exists() and not wait_for_file_unlock(exe):
                return 2
    except (OSError, TypeError, ValueError, RuntimeError):
        return 2

    backup = paths.get("backup_dir") or restore.parent / f"{restore.name}.rollback-{uuid.uuid4().hex}"
    completed: list[dict] = []
    marker = blocked_release_path(restore)
    previous_marker = None
    try:
        if marker.exists():
            if marker.is_symlink():
                return 3
            previous_marker = marker.read_bytes()
        files = [{"path": entry["path"], "size": entry["size"], "sha256": entry["sha256"]} for entry in metadata["files"]]
        completed = apply_update(install, restore / "files", backup, metadata["delete"], None, files)
        _verify_final(install, files, metadata["delete"])
        _write_blocked_release_marker(restore, metadata)
        consumed = _consume_restore_point(restore)
    except Exception as exc:
        completed = getattr(exc, "completed_ops", completed)
        marker_ok = _restore_marker(marker, previous_marker)
        recovery_ok = rollback(install, backup, completed)
        if not (marker_ok and recovery_ok):
            _log("critical", "Rollback recovery failed; refusing relaunch")
            return 1
        if job["relaunch"]:
            try:
                relaunch_app(job["relaunch_argv"], install)
            except OSError:
                pass
        return 1

    _cleanup_tree(backup)
    _cleanup_tree(consumed)
    if job["relaunch"]:
        try:
            relaunch_app(job["relaunch_argv"], install)
        except OSError:
            return 1
    return 0


def _rollback_cancelled(job: dict) -> bool:
    cancel = job.get("cancel_path")
    return bool(cancel and Path(cancel).exists())


def _apply_job(job: dict, job_path: Path) -> int:
    try:
        validated = _validate_job(job, job_path)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return 2
    install = validated["install"]
    paths = validated["paths"]
    manifest = validated["manifest"]
    try:
        _validate_archive(paths["archive_path"], manifest["archive"])
        wait_for_pid_exit(job["wait_pid"], timeout=60)
        if job["wait_pid"] > 0 and _pid_alive(job["wait_pid"]):
            _cleanup_tree(paths.get("runtime_dir"))
            return 2
        _remove_update_lock(job)
        if job["mode"] == "frozen":
            executable = safe_join(install, job["exe_name"], label="application executable")
            if not wait_for_file_unlock(executable):
                _cleanup_tree(paths.get("runtime_dir"))
                return 2
        if job["mode"] == "source" and manifest["channel"] == "linux":
            git_marker = install / ".git"
            if git_marker.exists() or git_marker.is_symlink():
                _log("error", "Refusing to update a Linux source installation that is a Git worktree: %s", git_marker)
                _cleanup_tree(paths.get("runtime_dir"))
                return 2
        _extract_archive(paths["archive_path"], paths["staging_dir"], manifest)
        files = manifest["files"]
        deletes = [entry["path"] for entry in manifest["deletions"]]
    except (OSError, TypeError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        _cleanup_tree(paths.get("staging_dir"))
        _cleanup_tree(paths.get("runtime_dir"))
        return 2

    completed: list[dict] = []
    try:
        completed = apply_update(install, paths["staging_dir"], paths["backup_dir"], deletes, job_path, files)
        _verify_final(install, files, deletes)
    except Exception as exc:
        completed = getattr(exc, "completed_ops", completed)
        rollback_ok = rollback(install, paths["backup_dir"], completed)
        if rollback_ok and job["relaunch"]:
            try:
                relaunch_app(job["relaunch_argv"], install)
            except OSError:
                pass
        _cleanup_tree(paths.get("staging_dir"))
        if rollback_ok:
            _cleanup_tree(paths.get("backup_dir"))
        return 1

    try:
        retain_restore_point(job, completed)
    except Exception as exc:
        _log("error", "Update committed but restore point retention failed: %s", exc)

    relaunch_ok = True
    if job["relaunch"]:
        try:
            relaunch_app(job["relaunch_argv"], install)
        except OSError:
            relaunch_ok = False
    _cleanup_tree(paths.get("staging_dir"))
    _cleanup_tree(paths.get("backup_dir"))
    _cleanup_tree(paths.get("runtime_dir"))
    return 0 if relaunch_ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="updater_app")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job", metavar="PATH")
    group.add_argument("--rollback", metavar="RESTORE_DIR")
    args = parser.parse_args(argv)
    job_path = None
    try:
        if args.rollback:
            job = _rollback_job_from_restore(args.rollback)
        else:
            job_path = _validate_absolute_path(args.job, "job_path", allow_missing=False)
            job = load_job(str(job_path))
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[updater] Failed to load job: {exc}", file=sys.stderr)
        return 3
    action = job.get("action", "update")
    try:
        validated = _validate_job(job, job_path)
        if action == "rollback":
            validated = _validate_rollback_job(job)
        _write_startup_ack(job)
        setup_logging(job["log_path"])
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"[updater] Invalid job: {exc}", file=sys.stderr)
        return 3
    if action == "rollback":
        try:
            return restore_previous_version(job)
        finally:
            _cleanup_tree(Path(job.get("runtime_dir")) if job.get("runtime_dir") else None)
    return _apply_job(job, job_path)


if __name__ == "__main__":
    raise SystemExit(main())
