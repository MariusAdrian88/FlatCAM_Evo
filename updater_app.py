"""Standalone stdlib-only FlatCAM update applier.

The launcher copies this file and ``recovery.py`` (or, frozen, the updater/
runtime) to a temporary directory before starting it, so the apply step can
replace the installed copy.  No application imports are allowed here.  The
only GUI is the progress window of the standard library (tkinter); without
tkinter or a display the updater runs with no window.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path

try:
    from services.updater.recovery import (
        WORK_DIR_NAME,
        blocked_release_path,
        is_protected_deletion,
        previous_release_path,
        safe_join,
        validate_relative_path,
    )
except ImportError:  # source helper copied beside recovery.py
    from recovery import (  # type: ignore[no-redef]
        WORK_DIR_NAME,
        blocked_release_path,
        is_protected_deletion,
        previous_release_path,
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
        "revert",
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


class _Progress:
    """Receives the steps of the job and writes them to the log.

    The sink gets ("job", action, version, log_path, install_dir), ("stage", text, total),
    ("advance", count) and ("finish", code, message). The progress window reads these events.
    """

    def __init__(self, sink=None):
        self._sink = sink
        self._action = "update"
        self._version = ""
        self._log_path = ""
        self.error = ""

    def _post(self, *event) -> None:
        if self._sink is not None:
            self._sink(event)

    def job(self, job: dict) -> None:
        self._action = "revert" if job.get("revert") else "update"
        manifest = job.get("manifest")
        self._version = str(manifest.get("version", "")) if isinstance(manifest, dict) else ""
        self._log_path = str(job.get("log_path", ""))
        self._post("job", self._action, self._version, self._log_path, str(job.get("install_dir", "")))

    def stage(self, text: str, total: int = 0) -> None:
        _log("info", "%s%s", text, f" ({total})" if total else "")
        self._post("stage", text, total)

    def advance(self, count: int = 1) -> None:
        self._post("advance", count)

    def fail(self, text: str) -> None:
        """Collect the reasons of a failure: the later steps (rollback, relaunch) add their result."""
        _log("error", "%s", text)
        self.error = f"{self.error}\n{text}" if self.error else text

    def finish(self, code: int) -> None:
        if code == 0:
            if self._action == "revert":
                version = f" {self._version}" if self._version else ""
                message = f"FlatCAM is reverted to version{version}. FlatCAM starts again."
            else:
                version = f" {self._version}" if self._version else ""
                message = f"FlatCAM is updated to version{version}. FlatCAM starts again."
            _log("info", "%s", message)
        else:
            message = self.error or f"The updater stopped with code {code}."
            if self._log_path:
                message += f"\n\nLog file: {self._log_path}"
        self._post("finish", code, message)


_PROGRESS = _Progress()


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

    prefix = raw.get("archive_prefix", "")
    if not isinstance(prefix, str):
        raise ValueError("manifest archive_prefix must be a string")
    if prefix:
        prefix = validate_relative_path(prefix, reject_protected=False)

    files = []
    seen: set[str] = set()
    for entry in raw["files"]:
        if not isinstance(entry, dict) or any(field not in entry for field in ("path", "size")):
            raise ValueError("manifest file entry is malformed")
        path = validate_relative_path(entry["path"])
        if path.casefold() in seen:
            raise ValueError("manifest contains duplicate file paths")
        seen.add(path.casefold())
        if len(Path(path).parts) == 1 and path in _CONTROL_NAMES:
            raise ValueError(f"manifest uses a reserved control path: {path}")
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError("manifest file contract is malformed")
        files.append({"path": path, "size": entry["size"], **_file_check(entry)})

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
        "archive_prefix": prefix,
        "files": files,
        "deletions": deletions,
    }


def _file_check(entry: dict) -> dict:
    """Return the integrity fields of a file entry: a SHA-256 (older launchers, Digi), a CRC-32 (GitHub), or both."""
    check = {}
    if entry.get("sha256"):
        if not _hash_valid(entry["sha256"]):
            raise ValueError("manifest file sha256 is malformed")
        check["sha256"] = entry["sha256"].lower()
    if entry.get("crc32") is not None:
        if type(entry["crc32"]) is not int or not 0 <= entry["crc32"] <= 0xFFFFFFFF:
            raise ValueError("manifest file crc32 is malformed")
        check["crc32"] = entry["crc32"]
    if not check:
        raise ValueError("manifest file entry has no sha256 or crc32")
    return check


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
    # a revert is an update to the previous release; the restore points (action "rollback") of releases up to 1.05
    # are not used now, and the installed updater of such a release runs its own rollback jobs
    if job.get("action", "update") != "update":
        raise ValueError("job action is invalid")
    if not isinstance(job.get("revert", False), bool):
        raise ValueError("revert must be a boolean")
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

    if "manifest" not in job and "manifest_path" not in job:
        raise ValueError("update job is missing manifest or manifest_path")
    if "archive_path" not in job:
        raise ValueError("update job is missing archive_path")
    archive = _validate_absolute_path(job["archive_path"], "archive_path", allow_missing=False)
    if not archive.is_file():
        raise ValueError("archive_path must be a regular file")
    if _paths_overlap(archive, install):
        raise ValueError("archive_path must be outside install_dir")
    # older launchers name a staging and a backup folder in the user data; the updater works in its folder inside
    # the installation, so these fields are only checked
    for field in ("staging_dir", "backup_dir"):
        if field in job and _paths_overlap(_validate_absolute_path(job[field], field), install):
            raise ValueError(f"{field} must be outside install_dir")
    work_paths = [restore]
    if "runtime_dir" in paths:
        work_paths.append(paths["runtime_dir"])
    if any(_paths_overlap(archive, path) for path in work_paths):
        raise ValueError("archive and updater work paths overlap")
    if len(work_paths) == 2 and _paths_overlap(*work_paths):
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
    work = install / WORK_DIR_NAME
    _reject_symlink_components(work, install)
    if _is_link(work) or (work.exists() and not work.is_dir()):
        raise ValueError(f"updater work folder is not a directory: {work}")
    # a backup folder of its own for each job: a backup that an earlier failed cleanup left must not stop this update
    paths.update({"archive_path": archive, "work_dir": work, "staging_dir": work / "staging",
                  "backup_dir": work / f"backup-{uuid.uuid4().hex[:12]}"})
    return {"job": job, "install": install, "paths": paths, "manifest": manifest, "argv": relaunch_argv}


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


class _PathGuard:
    """Join managed relative paths below one root.

    A relative path is checked as text (no absolute path, no ``..``); each directory below the root is checked one
    time for a symlink or a junction, not again for each file of a release with thousands of files.
    """

    def __init__(self, root: Path, label: str):
        root = Path(root)
        _reject_symlink_components(root)
        self.root = root
        self.label = label
        self._checked: set[str] = set()

    def join(self, relative: str) -> Path:
        normalized = validate_relative_path(relative)
        parts = normalized.split("/")
        cursor = self.root
        key = ""
        for part in parts[:-1]:
            cursor = cursor / part
            key = f"{key}/{part.casefold()}"
            if key in self._checked:
                continue
            if _is_link(cursor):
                raise ValueError(f"{self.label} must not contain symlink components: {normalized}")
            self._checked.add(key)
        target = cursor / parts[-1]
        if _is_link(target):
            raise ValueError(f"{self.label} must not be a symlink: {normalized}")
        return target


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _archive_members(archive_path: Path, manifest: dict) -> dict[str, zipfile.ZipInfo]:
    """Map each managed file to its archive member; the members that the manifest does not list stay unused."""
    expected = {entry["path"].casefold(): entry for entry in manifest["files"]}
    prefix = manifest.get("archive_prefix", "")
    lead = f"{prefix}/".casefold() if prefix else ""
    members = {}
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                raw_name = info.filename
                if info.is_dir() or raw_name.endswith("/"):
                    continue
                name = raw_name.replace("\\", "/")
                if lead:
                    if not name.casefold().startswith(lead):
                        continue
                    name = name[len(lead):]
                key = name.casefold()
                if key not in expected:
                    continue
                if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                    raise ValueError(f"ZIP symlink is not allowed: {raw_name}")
                if key in members:
                    raise ValueError(f"ZIP contains duplicate path: {name}")
                members[key] = info
    except zipfile.BadZipFile as exc:
        raise ValueError(f"archive is not a valid ZIP: {exc}") from exc
    if set(members) != set(expected):
        missing = sorted(expected[key]["path"] for key in set(expected) - set(members))
        raise ValueError(f"ZIP is missing managed files: {missing!r}")
    return members


def _reset_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if path.exists():
        if _is_link(path) or not path.is_dir():
            raise ValueError(f"work path is not a directory: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _extract_archive(archive_path: Path, staging_dir: Path, manifest: dict) -> None:
    """Extract each managed file one time and check its size and its CRC-32 or SHA-256 while it is written."""
    members = _archive_members(archive_path, manifest)
    _reset_directory(staging_dir)
    guard = _PathGuard(staging_dir, "staged file path")
    entries = {entry["path"].casefold(): entry for entry in manifest["files"]}
    made: set[Path] = set()
    _PROGRESS.stage("Extracting files", len(entries))
    with zipfile.ZipFile(archive_path, "r") as archive:
        for key, entry in sorted(entries.items()):
            destination = guard.join(entry["path"])
            if destination.parent not in made:
                destination.parent.mkdir(parents=True, exist_ok=True)
                made.add(destination.parent)
            info = members[key]
            digest = hashlib.sha256() if entry.get("sha256") else None
            written = 0
            try:
                # the member stream checks the CRC-32 of the data at its end
                with archive.open(info, "r") as source, destination.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        if digest is not None:
                            digest.update(chunk)
                        written += len(chunk)
            except zipfile.BadZipFile as exc:
                raise ValueError(f"staged file checksum mismatch: {entry['path']}: {exc}") from exc
            if (
                written != entry["size"]
                or (entry.get("crc32") is not None and info.CRC != entry["crc32"])
                or (digest is not None and digest.hexdigest() != entry["sha256"])
            ):
                raise ValueError(f"staged file checksum mismatch: {entry['path']}")
            permissions = (info.external_attr >> 16) & 0o777
            if permissions:
                os.chmod(destination, permissions)
            _PROGRESS.advance()


def _is_staging_metadata(path: Path) -> bool:
    return len(path.parts) == 1 and path.name in _CONTROL_NAMES


def _contract_files(files) -> dict[str, dict]:
    if files is None:
        return {}
    if not isinstance(files, list):
        raise OSError("files contract must be a list")
    result = {}
    for entry in files:
        if not isinstance(entry, dict) or any(field not in entry for field in ("path", "size")):
            raise OSError("malformed file contract entry")
        try:
            path = validate_relative_path(entry["path"])
            check = _file_check(entry)
        except (TypeError, ValueError) as exc:
            raise OSError(f"malformed file contract entry: {exc}") from exc
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise OSError(f"malformed file contract entry: {path}")
        if path.casefold() in result:
            raise OSError(f"duplicate file contract entry: {path}")
        result[path.casefold()] = {"path": path, "size": entry["size"], **check}
    return result


def _staged_files(staging_dir: Path, expected: dict[str, dict] | None, job_file_path: Path | None) -> dict[str, Path]:
    """Return the staged files; the extraction checked their data, so here only their set and sizes are checked."""
    result = {}
    _reject_symlink_components(staging_dir)
    # a missing staging folder gives no files: the contract check below then fails for each expected file
    for root, dirs, filenames in os.walk(staging_dir, followlinks=False):
        root_path = Path(root)
        if any(_is_link(root_path / name) for name in dirs):
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
            if source.stat().st_size != entry["size"]:
                raise OSError(f"staged file size mismatch: {entry['path']}")
    return result


def _attach_completed(exc: BaseException, completed: list[dict]) -> None:
    try:
        exc.completed_ops = completed  # type: ignore[attr-defined]
    except Exception:
        pass


def _place_file(source: Path, target: Path) -> None:
    """Rename a staged file into place; copy it when the rename is not possible (another volume)."""
    try:
        os.replace(source, target)
    except OSError:
        if target.exists():
            raise
        shutil.copy2(source, target)


def _copy_or_move_target(install: _PathGuard, backup_guard: _PathGuard, rel: str, source: Path | None,
                         completed: list[dict], made: set[Path]) -> None:
    target = install.join(rel)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise OSError(f"install target is not a regular file: {target}")
        backup = backup_guard.join(rel)
        if backup.parent not in made:
            backup.parent.mkdir(parents=True, exist_ok=True)
            made.add(backup.parent)
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
            if target.parent not in made:
                target.parent.mkdir(parents=True, exist_ok=True)
                made.add(target.parent)
            _retry_op("staged move", _place_file, source, target)
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
    """Move the staged files into place and do the deletions; record each step for a reverse rollback."""
    completed: list[dict] = []
    try:
        install_dir = _validate_absolute_path(str(install_dir), "install_dir", allow_missing=False)
        staging_dir = _validate_absolute_path(str(staging_dir), "staging_dir")
        backup_dir = _validate_absolute_path(str(backup_dir), "backup_dir")
        if _paths_overlap(staging_dir, backup_dir):
            raise OSError("update staging and backup paths overlap")
        work = (install_dir / WORK_DIR_NAME).resolve()
        for path in (staging_dir, backup_dir):
            if _paths_overlap(path, install_dir) and work not in path.resolve().parents:
                raise OSError("update work path overlaps install_dir outside the updater work folder")
        expected = _contract_files(files) if files is not None else None
        staged = _staged_files(staging_dir, expected, job_file_path)
        deletes = {}
        for raw in delete_list or []:
            rel = validate_relative_path(raw)
            deletes[rel.casefold()] = rel
        if backup_dir.exists():
            if _is_link(backup_dir) or not backup_dir.is_dir():
                raise OSError("backup_dir is not a directory")
            if any(backup_dir.iterdir()):
                raise OSError("backup_dir must be empty")
        else:
            backup_dir.mkdir(parents=True, exist_ok=True)

        install = _PathGuard(install_dir, "install path")
        backup_guard = _PathGuard(backup_dir, "backup path")
        made: set[Path] = set()
        all_keys = set(deletes) | set(staged)
        _PROGRESS.stage("Replacing files", len(all_keys))
        for key in sorted(all_keys):
            rel = expected[key]["path"] if expected and key in expected else staged[key].relative_to(staging_dir).as_posix() if key in staged else deletes[key]
            _copy_or_move_target(install, backup_guard, rel, staged.get(key), completed, made)
            _PROGRESS.advance()
        return completed
    except Exception as exc:
        _attach_completed(exc, completed)
        raise


def _verify_final(install_dir: Path, files: list[dict], delete_list: list[str]) -> None:
    """Check that each managed file is in place with its size and that each deleted file is gone."""
    install = _PathGuard(install_dir, "installed path")
    for entry in files:
        target = install.join(entry["path"])
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise OSError(f"installed file is missing: {entry['path']}") from exc
        if not target.is_file() or size != entry["size"]:
            raise OSError(f"installed file size mismatch: {entry['path']}")
    for raw in delete_list:
        target = install.join(raw)
        if target.exists() or target.is_symlink():
            raise OSError(f"obsolete file remains: {raw}")


def rollback(install_dir: Path, backup_dir: Path, completed_ops: list[dict]) -> bool:
    """Reverse completed operations in reverse order; false means unsafe to relaunch."""
    success = True
    _PROGRESS.stage("Restoring the previous files", len(completed_ops))
    for operation in reversed(completed_ops):
        _PROGRESS.advance()
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


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_release(job: dict, completed_ops: list[dict]) -> None:
    """Write the release records after a committed update; they hold no files.

    An update records the release that it replaced: Revert Update downloads that release and installs it, and it
    deletes the files that this update added. A revert blocks the release that it removed and clears the record.
    """
    restore = Path(job["restore_dir"])
    record_path = previous_release_path(restore)
    if job.get("revert"):
        _write_json_atomic(blocked_release_path(restore), {
            "version": str(job.get("previous_version", "")),
            "build_string": str(job.get("previous_build_string", "")),
        })
        record_path.unlink(missing_ok=True)
        return
    added = sorted({operation["rel"] for operation in completed_ops if operation["type"] == "add"})
    _write_json_atomic(record_path, {
        "schema": 1,
        "install_dir": str(Path(job["install_dir"]).resolve()),
        "mode": job["mode"],
        "exe_name": job["exe_name"],
        "previous_version": str(job.get("previous_version", "")),
        "previous_build_string": str(job.get("previous_build_string", "")),
        "version": str(job["manifest"]["version"]),
        "build_string": str(job["manifest"]["build"]),
        "added": added,
    })


def _legacy_restore_points(job: dict) -> list[Path]:
    """Return the restore point folders of releases up to 1.05 (a copy of all files): they are not used now."""
    restore = Path(job["restore_dir"])
    found = []
    try:
        for path in restore.parent.iterdir():
            name = path.name
            if (name == restore.name or name.startswith(restore.name + ".")) and path.is_dir() and not _is_link(path):
                found.append(path)
    except OSError:
        pass
    return found


def _default_relaunch_argv() -> list[str]:
    if _IS_WINDOWS:
        return [str(sys.executable), *sys.argv[1:]]
    return [str(sys.executable), *sys.argv]


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


def _apply_job(job: dict, job_path: Path) -> int:
    try:
        validated = _validate_job(job, job_path)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        _PROGRESS.fail(f"The update job is not valid: {exc}")
        return 2
    install = validated["install"]
    paths = validated["paths"]
    manifest = validated["manifest"]
    try:
        _PROGRESS.stage("Checking the update archive")
        _validate_archive(paths["archive_path"], manifest["archive"])
        _PROGRESS.stage("Waiting for FlatCAM to close")
        wait_for_pid_exit(job["wait_pid"], timeout=60)
        if job["wait_pid"] > 0 and _pid_alive(job["wait_pid"]):
            _PROGRESS.fail("FlatCAM did not close in 60 seconds. Nothing was changed.")
            _cleanup_tree(paths.get("runtime_dir"))
            return 2
        _remove_update_lock(job)
        if job["mode"] == "frozen":
            executable = safe_join(install, job["exe_name"], label="application executable")
            if not wait_for_file_unlock(executable):
                _PROGRESS.fail(f"{executable} stays locked by another process. Nothing was changed.")
                _cleanup_tree(paths.get("runtime_dir"))
                return 2
        if job["mode"] == "source" and manifest["channel"] == "linux":
            git_marker = install / ".git"
            if git_marker.exists() or git_marker.is_symlink():
                _PROGRESS.fail(f"Refusing to update a Linux source installation that is a Git worktree: {git_marker}")
                _cleanup_tree(paths.get("runtime_dir"))
                return 2
        _extract_archive(paths["archive_path"], paths["staging_dir"], manifest)
        files = manifest["files"]
        deletes = [entry["path"] for entry in manifest["deletions"]]
    except (OSError, TypeError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        _PROGRESS.fail(f"Could not prepare the update: {exc}. Nothing was changed.")
        _cleanup_tree(paths.get("work_dir"))
        _cleanup_tree(paths.get("runtime_dir"))
        return 2

    completed: list[dict] = []
    try:
        completed = apply_update(install, paths["staging_dir"], paths["backup_dir"], deletes, job_path, files)
        _verify_final(install, files, deletes)
    except Exception as exc:
        _PROGRESS.fail(f"The update failed: {exc}")
        completed = getattr(exc, "completed_ops", completed)
        rollback_ok = rollback(install, paths["backup_dir"], completed)
        if rollback_ok:
            _PROGRESS.fail("The previous version is restored.")
        else:
            _PROGRESS.fail("The previous version could not be restored. "
                           "FlatCAM is not started: install it again with the installer.")
        if rollback_ok and job["relaunch"]:
            try:
                relaunch_app(job["relaunch_argv"], install)
            except OSError:
                pass
        _cleanup_tree(paths.get("staging_dir"))
        if rollback_ok:
            _cleanup_tree(paths.get("work_dir"))
        return 1

    try:
        _record_release(job, completed)
    except Exception as exc:
        _log("error", "Update committed but the release record could not be written: %s", exc)

    relaunch_ok = True
    if job["relaunch"]:
        _PROGRESS.stage("Starting FlatCAM")
        try:
            relaunch_app(job["relaunch_argv"], install)
        except OSError as exc:
            _PROGRESS.fail(f"FlatCAM is updated, but it could not start: {exc}")
            relaunch_ok = False
    # FlatCAM starts while the updater removes the replaced files
    _PROGRESS.stage("Removing the replaced files")
    _cleanup_tree(paths.get("work_dir"))
    for legacy in _legacy_restore_points(job):
        _cleanup_tree(legacy)
    _cleanup_tree(paths.get("runtime_dir"))
    return 0 if relaunch_ok else 1


def _run(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="updater_app")
    parser.add_argument("--job", metavar="PATH", required=True)
    args = parser.parse_args(argv)
    try:
        job_path = _validate_absolute_path(args.job, "job_path", allow_missing=False)
        job = load_job(str(job_path))
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[updater] Failed to load job: {exc}", file=sys.stderr)
        _PROGRESS.fail(f"Could not load the update job: {exc}")
        return 3
    try:
        _validate_job(job, job_path)
        _write_startup_ack(job)
        setup_logging(job["log_path"])
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"[updater] Invalid job: {exc}", file=sys.stderr)
        _PROGRESS.fail(f"The update job is not valid: {exc}")
        return 3
    _PROGRESS.job(job)
    return _apply_job(job, job_path)


def main(argv=None) -> int:
    code = 3
    try:
        code = _run(argv)
    except SystemExit as exc:  # argparse
        code = exc.code if isinstance(exc.code, int) else 3
        raise
    except BaseException as exc:
        _PROGRESS.fail(f"Unexpected updater error: {exc!r}")
        _LOG.exception("Unexpected updater error")
        raise
    finally:
        _PROGRESS.finish(code)
    return code


_SUCCESS_CLOSE_MS = 4000
_POLL_MS = 100

# colors of the dark theme of the progress window
_DARK = {
    "bg": "#202020",
    "fg": "#e6e6e6",
    "muted": "#7a7a7a",
    "button": "#333333",
    "button_active": "#3d3d3d",
    "border": "#4a4a4a",
    "trough": "#2d2d2d",
    "bar": "#3d8ee6",
}


def _os_dark_mode() -> bool:
    """True when the OS uses a dark theme for applications; False when it is light or not known."""
    if _IS_WINDOWS:
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return winreg.QueryValueEx(handle, "AppsUseLightTheme")[0] == 0
        except (OSError, ImportError):
            return False
    if sys.platform == "darwin":
        # Tk on macOS (Aqua) follows the system appearance by itself
        return False
    gtk_theme = os.environ.get("GTK_THEME", "")
    if gtk_theme:
        return "dark" in gtk_theme.lower()
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=1,
        ).stdout
        if "dark" in out:
            return True
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
            capture_output=True, text=True, timeout=1,
        ).stdout
        return "dark" in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


class _ProgressWindow:
    """Progress window of the updater: the update has no other visible part while FlatCAM is closed."""

    def __init__(self, tk, ttk):
        self.tk = tk
        self.events: queue.Queue = queue.Queue()
        self.finished = False
        root = self.root = tk.Tk()
        root.title("FlatCAM Updater")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if getattr(sys, "frozen", False):
            self._set_icon(sys.executable)
        self.dark = _os_dark_mode()
        if self.dark:
            self._apply_dark_style(ttk)
        frame = ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")
        self.title = ttk.Label(frame, text="Updating FlatCAM", font=("TkDefaultFont", 11, "bold"))
        self.title.grid(row=0, column=0, sticky="w")
        self.stage = ttk.Label(frame, text="Starting", width=60)
        self.stage.grid(row=1, column=0, sticky="w", pady=(8, 4))
        self.bar = ttk.Progressbar(frame, length=440, mode="indeterminate")
        self.bar.grid(row=2, column=0, sticky="ew")
        self.count = ttk.Label(frame, text="")
        self.count.grid(row=3, column=0, sticky="e", pady=(2, 0))
        self.message = ttk.Label(frame, text="Do not start FlatCAM until the update is complete.",
                                 wraplength=440, justify="left")
        self.message.grid(row=4, column=0, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, sticky="e", pady=(12, 0))
        self.open_log = ttk.Button(buttons, text="Open Log", command=self._open_log)
        self.close = ttk.Button(buttons, text="Close", command=self._on_close, state="disabled")
        self.close.grid(row=0, column=1, padx=(8, 0))
        self.log_path = ""
        self.total = 0
        self.done = 0
        self.bar.start(15)
        root.update_idletasks()
        if self.dark:
            self._dark_title_bar()
        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
        y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
        root.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        root.lift()
        root.after(_POLL_MS, self._poll)

    def _apply_dark_style(self, ttk) -> None:
        # the native themes (vista, aqua) ignore custom colors; clam takes them
        c = _DARK
        self.root.configure(background=c["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            return
        style.configure(".", background=c["bg"], foreground=c["fg"], bordercolor=c["border"],
                        lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["trough"],
                        focuscolor=c["border"])
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("TButton", background=c["button"], foreground=c["fg"], bordercolor=c["border"],
                        lightcolor=c["button"], darkcolor=c["button"], padding=(10, 3))
        style.map("TButton",
                  background=[("disabled", c["bg"]), ("pressed", c["border"]), ("active", c["button_active"])],
                  foreground=[("disabled", c["muted"])],
                  lightcolor=[("disabled", c["bg"]), ("active", c["button_active"])],
                  darkcolor=[("disabled", c["bg"]), ("active", c["button_active"])])
        style.configure("Horizontal.TProgressbar", background=c["bar"], troughcolor=c["trough"],
                        bordercolor=c["border"], lightcolor=c["bar"], darkcolor=c["bar"])

    def _dark_title_bar(self) -> None:
        # Windows 10 1809+ and Windows 11 draw a dark title bar on request; other systems ignore this
        if not _IS_WINDOWS:
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE; 19 on Windows 10 builds before 20H1
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def _set_icon(self, path) -> None:
        try:
            self.root.iconbitmap(default=str(path))
        except Exception:
            pass

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.total:
            self.bar.configure(value=self.done)
            self.count.configure(text=f"{self.done} / {self.total}")
        self.root.after(_POLL_MS, self._poll)

    def _handle(self, event) -> None:
        kind = event[0]
        if kind == "job":
            _, action, version, self.log_path, install_dir = event
            if action == "revert":
                self.title.configure(text=f"Reverting FlatCAM to version {version}")
            else:
                self.title.configure(text=f"Updating FlatCAM to version {version}" if version else "Updating FlatCAM")
            if not getattr(sys, "frozen", False) and install_dir:
                icon = Path(install_dir) / "assets" / "resources" / "flatcam_icon32.ico"
                if icon.is_file():
                    self._set_icon(icon)
        elif kind == "stage":
            _, text, total = event
            self.stage.configure(text=text)
            self.total, self.done = total, 0
            if total:
                self.bar.stop()
                self.bar.configure(mode="determinate", maximum=total, value=0)
            else:
                self.count.configure(text="")
                self.bar.configure(mode="indeterminate")
                self.bar.start(15)
        elif kind == "advance":
            self.done = min(self.done + event[1], self.total) if self.total else 0
        elif kind == "finish":
            self._finish(event[1], event[2])

    def _finish(self, code: int, message: str) -> None:
        self.finished = True
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=1, value=1 if code == 0 else 0)
        self.total = 0
        self.count.configure(text="")
        self.message.configure(text=message)
        self.close.configure(state="normal")
        if code == 0:
            self.stage.configure(text="Complete")
            self.root.after(_SUCCESS_CLOSE_MS, self.root.destroy)
        else:
            self.stage.configure(text="Stopped")
            if self.log_path and Path(self.log_path).is_file():
                self.open_log.grid(row=0, column=0)
            self.root.deiconify()
            self.root.lift()
            self.root.bell()

    def _open_log(self) -> None:
        try:
            if _IS_WINDOWS:
                os.startfile(self.log_path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", self.log_path], start_new_session=True)
        except OSError:
            pass

    def _on_close(self) -> None:
        # the files must not stay half replaced: the window closes only after the job ends
        if self.finished:
            self.root.destroy()


def _window_disabled() -> bool:
    return bool(os.environ.get("FLATCAM_UPDATER_NO_WINDOW"))


def run_with_window(argv=None) -> int:
    """Run the job in a worker thread and show its progress; without tkinter or a display, run it with no window."""
    global _PROGRESS
    if _window_disabled():
        return main(argv)
    try:
        import tkinter
        from tkinter import ttk

        window = _ProgressWindow(tkinter, ttk)
    except Exception:
        return main(argv)
    _PROGRESS = _Progress(window.events.put)
    result = {"code": 3}

    def work() -> None:
        try:
            result["code"] = main(argv)
        except SystemExit as exc:
            result["code"] = exc.code if isinstance(exc.code, int) else 3
        except BaseException:
            result["code"] = 3

    worker = threading.Thread(target=work, name="flatcam-updater-job", daemon=True)
    worker.start()
    window.root.mainloop()
    worker.join()
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(run_with_window())
