"""Launch the external FlatCAM updater without stopping the main app."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .manifest import manifest_to_json, parse_manifest
from .recovery import is_protected_path, load_restore_point, restore_dir_for_install


UPDATER_DIR_NAME = "updater"
UPDATER_EXE_NAME = "FlatCAMUpdater.exe"
_STARTUP_ACK_TIMEOUT = 5.0
_log = logging.getLogger(__name__)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _install_root() -> Path:
    """Return the frozen install directory or the source repository root."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _install_dir() -> Path:
    """Compatibility alias for callers that use the older helper name."""
    return _install_root()


def _updater_dir(install_root: Path) -> Path:
    return Path(install_root) / UPDATER_DIR_NAME


def _updater_exe(install_root: Path) -> Path:
    return _updater_dir(install_root) / UPDATER_EXE_NAME


def _validate_runtime(app=None, operation: str = "update"):
    """Return the protected frozen updater runtime or ``None``."""
    if not _is_frozen():
        return None
    install_root = _install_root()
    updater_dir = _updater_dir(install_root)
    updater_exe = _updater_exe(install_root)
    if (
        updater_dir.is_symlink()
        or not updater_dir.is_dir()
        or updater_exe.is_symlink()
        or not updater_exe.is_file()
    ):
        _app_log(app, "error", f"{operation}: protected updater executable is missing")
        return None
    return install_root, updater_dir, updater_exe


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]


def _app_log(app, level: str, message: str) -> None:
    logger = getattr(app, "log", None)
    method = getattr(logger, level, None)
    if callable(method):
        try:
            method(message)
            return
        except Exception:
            pass
    method = getattr(_log, level, None)
    if callable(method):
        method(message)


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


def _has_symlink_component(path: Path) -> bool:
    path = Path(path)
    if not path.is_absolute():
        path = Path(os.path.abspath(path))
    cursor = Path(path.anchor or os.sep)
    for component in path.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            return True
    return False


def _storage_root(app, install_root: Path) -> Path:
    raw_path = getattr(app, "data_path", "")
    requested = Path(raw_path) if raw_path else Path(tempfile.gettempdir()) / "FlatCAM"
    if not requested:
        requested = Path(tempfile.gettempdir()) / "FlatCAM"
    try:
        if _has_symlink_component(requested):
            raise ValueError("updater storage path must not contain symlinks")
        if _paths_overlap(requested, install_root):
            requested = Path(tempfile.gettempdir()) / "FlatCAM"
    except (OSError, RuntimeError):
        requested = Path(tempfile.gettempdir()) / "FlatCAM"
    return requested.resolve()


def _manifest_dict(manifest) -> dict:
    if isinstance(manifest, dict):
        data = json.loads(json.dumps(manifest))
    elif dataclasses.is_dataclass(manifest):
        data = dataclasses.asdict(manifest)
    else:
        archive = getattr(manifest, "archive")
        if dataclasses.is_dataclass(archive):
            archive = dataclasses.asdict(archive)
        else:
            archive = {
                "filename": archive.filename,
                "size": archive.size,
                "sha256": archive.sha256,
            }
        data = {
            "schema_version": getattr(manifest, "schema_version", 1),
            "channel": getattr(manifest, "channel", "linux"),
            "version": str(manifest.version),
            "build": str(getattr(manifest, "build_string", getattr(manifest, "build", ""))),
            "version_date": str(getattr(manifest, "version_date", "")),
            "minimum_required_version": str(getattr(manifest, "minimum_required_version", "0")),
            "release_notes": str(getattr(manifest, "release_notes", "")),
            "archive": archive,
            "files": [],
            "deletions": [],
        }
        for entry in getattr(manifest, "files", ()):
            data["files"].append(
                {
                    "path": entry.path,
                    "size": entry.size,
                    "sha256": entry.sha256,
                }
            )
        for entry in getattr(manifest, "deletions", ()):
            data["deletions"].append(
                {"path": entry.path, "version": getattr(entry, "version", "0")}
            )
    if "build" not in data and "build_string" in data:
        data["build"] = data["build_string"]
    if "deletions" not in data:
        data["deletions"] = []
    return data


def _validate_archive_reference(archive_path: Path, manifest: dict) -> None:
    if not archive_path.is_absolute() or archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("archive_path must be an existing regular file")
    archive = manifest.get("archive")
    if not isinstance(archive, dict):
        raise ValueError("manifest archive must be an object")
    if type(archive.get("size")) is not int or archive["size"] < 0:
        raise ValueError("manifest archive size is invalid")
    digest = archive.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("manifest archive hash is invalid")
    if archive_path.stat().st_size != archive["size"]:
        raise ValueError("archive size does not match manifest")
    hasher = hashlib.sha256()
    with archive_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != digest.lower():
        raise ValueError("archive hash does not match manifest")


def _copy_job_archive(job_root: Path, archive_path: Path) -> Path:
    """Copy the verified archive into the job so the caller may delete its staging."""
    source = Path(archive_path).resolve()
    destination = (Path(job_root) / "archive" / source.name).resolve()
    if _path_key(source) == _path_key(destination) or _paths_overlap(source, job_root):
        return source
    destination.parent.mkdir(parents=True)
    shutil.copy2(source, destination)
    return destination


def _default_relaunch_argv() -> list[str]:
    if _is_frozen():
        return [str(sys.executable), *sys.argv[1:]]
    return [str(sys.executable), *sys.argv]


def _validate_relaunch_argv(argv) -> list[str]:
    if not isinstance(argv, list) or not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        raise ValueError("relaunch_argv must be a non-empty list of strings")
    return list(argv)


def _new_job_root(storage_root: Path, install_root: Path, prefix: str) -> Path:
    update_root = storage_root / "update"
    if _paths_overlap(update_root, install_root):
        raise ValueError("updater work directory overlaps install root")
    if update_root.is_symlink() or storage_root.is_symlink():
        raise ValueError("updater work directory must not use symlinks")
    update_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(update_root)))


def _copy_source_runtime(job_root: Path) -> tuple[Path, Path]:
    runtime = job_root / "runtime"
    runtime.mkdir()
    source_root = Path(__file__).resolve().parents[2]
    helper_source = source_root / "updater_app.py"
    recovery_source = Path(__file__).resolve().with_name("recovery.py")
    if (
        helper_source.is_symlink()
        or recovery_source.is_symlink()
        or not helper_source.is_file()
        or not recovery_source.is_file()
    ):
        raise FileNotFoundError("source updater helper or recovery runtime is missing")
    helper = runtime / "updater_app.py"
    shutil.copy2(helper_source, helper)
    shutil.copy2(recovery_source, runtime / "recovery.py")
    return runtime, helper


def _runtime(app, install_root: Path) -> tuple[Path, Path, Path | None, Path | None]:
    """Return command cwd, command executable, runtime directory, helper."""
    if _is_frozen():
        runtime = _validate_runtime(app, "launch")
        if runtime is None:
            raise FileNotFoundError("protected updater executable is missing")
        _, updater_dir, updater_exe = runtime
        return updater_dir, updater_exe, None, None
    raise RuntimeError("source runtime is created per job")


def _detached_kwargs() -> dict:
    kwargs = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _creation_flags() -> int:
    return 0x00000008 | 0x00000200 if sys.platform == "win32" else 0


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _wait_for_startup_ack(ack_path: Path, timeout: float = _STARTUP_ACK_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ack_path.is_file() and not ack_path.is_symlink():
            return True
        time.sleep(0.05)
    return ack_path.is_file() and not ack_path.is_symlink()


def _launch_process(command: list[str], cwd: Path, ack_path: Path) -> bool:
    try:
        subprocess.Popen(command, cwd=str(cwd), **_detached_kwargs())
    except OSError:
        return False
    return _wait_for_startup_ack(ack_path)


def _prepare_job_paths(app, install_root: Path, prefix: str) -> tuple[Path, Path, Path, Path, Path]:
    storage = _storage_root(app, install_root)
    job_root = _new_job_root(storage, install_root, prefix)
    staging = job_root / "staging"
    backup = job_root / "backup"
    restore = restore_dir_for_install(storage, install_root)
    ack = job_root / ".updater_started"
    return job_root, staging, backup, restore, ack


def launch_update(app, manifest, archive_path, *, relaunch_argv=None) -> bool:
    """Write and launch a full-archive update job."""
    job_root = None
    try:
        install_root = _install_root()
        archive_path = Path(archive_path)
        manifest_data = json.loads(manifest_to_json(parse_manifest(_manifest_dict(manifest))))
        if any(is_protected_path(entry["path"]) for entry in manifest_data["files"]):
            raise ValueError("manifest contains a protected managed path")
        if any(is_protected_path(entry["path"]) for entry in manifest_data["deletions"]):
            raise ValueError("manifest contains a protected deletion path")
        _validate_archive_reference(archive_path, manifest_data)
        if _paths_overlap(archive_path, install_root):
            raise ValueError("archive_path must be outside install root")
        relaunch = _validate_relaunch_argv(
            _default_relaunch_argv() if relaunch_argv is None else relaunch_argv
        )
        job_root, staging, backup, restore, ack = _prepare_job_paths(
            app, install_root, "flatcam-update-"
        )
        job_archive = _copy_job_archive(job_root, archive_path)
        _validate_archive_reference(job_archive, manifest_data)
        mode = "frozen" if _is_frozen() else "source"
        if mode == "frozen":
            cwd, executable, runtime, helper = _runtime(app, install_root)
            exe_name = Path(sys.executable).name
            if exe_name.casefold() != "flatcam.exe":
                raise ValueError("frozen application executable must be FlatCAM.exe")
        else:
            runtime, helper = _copy_source_runtime(job_root)
            cwd, executable = runtime, Path(sys.executable)
            exe_name = ""
        job = {
            "schema": 1,
            "action": "update",
            "mode": mode,
            "install_dir": str(install_root),
            "archive_path": str(job_archive),
            "staging_dir": str(staging),
            "backup_dir": str(backup),
            "restore_dir": str(restore),
            "manifest": manifest_data,
            "previous_version": str(getattr(app, "version", "")),
            "previous_build_string": str(getattr(app, "build_string", "")),
            "exe_name": exe_name,
            "wait_pid": os.getpid(),
            "relaunch": True,
            "relaunch_argv": relaunch,
            "startup_ack_path": str(ack),
            "log_path": str(job_root / "updater.log"),
        }
        # a frozen build has no runtime copy per job (runtime None): the updater rejects runtime_dir "None"
        if runtime is not None:
            job["runtime_dir"] = str(runtime)
        job_path = job_root / "update_job.json"
        _write_json(job_path, job)
        ack.unlink(missing_ok=True)
        command = (
            [str(executable), "--job", str(job_path)]
            if mode == "frozen"
            else [str(executable), str(helper), "--job", str(job_path)]
        )
        if not _launch_process(command, cwd, ack):
            shutil.rmtree(job_root, ignore_errors=True)
            _app_log(app, "error", "launch_update: updater did not acknowledge startup")
            return False
        _app_log(app, "info", f"launch_update: updater started for {install_root}")
        return True
    except Exception as exc:
        if job_root is not None:
            shutil.rmtree(job_root, ignore_errors=True)
        _app_log(app, "error", f"launch_update: {exc}")
        return False


def launch_updater(app, manifest, delta_or_archive, staging_dir=None) -> bool:
    """Compatibility spelling; Ticket 2 uses a full archive, not a delta."""
    if staging_dir is not None:
        archive_path = delta_or_archive
    else:
        archive_path = delta_or_archive
    return launch_update(app, manifest, archive_path)


def launch_rollback(app, *, relaunch_argv=None) -> bool:
    """Write and launch a rollback job for the retained restore point."""
    try:
        install_root = _install_root()
        storage = _storage_root(app, install_root)
        restore = restore_dir_for_install(storage, install_root)
        load_restore_point(restore, install_root, verify_files=False)
        relaunch = _validate_relaunch_argv(
            _default_relaunch_argv() if relaunch_argv is None else relaunch_argv
        )
        job_root, staging, backup, _restore, ack = _prepare_job_paths(
            app, install_root, "flatcam-rollback-"
        )
        mode = "frozen" if _is_frozen() else "source"
        if mode == "frozen":
            cwd, executable, runtime, helper = _runtime(app, install_root)
            if Path(sys.executable).name.casefold() != "flatcam.exe":
                raise ValueError("frozen application executable must be FlatCAM.exe")
            exe_name = Path(sys.executable).name
        else:
            runtime, helper = _copy_source_runtime(job_root)
            cwd, executable = runtime, Path(sys.executable)
            exe_name = ""
        job = {
            "schema": 1,
            "action": "rollback",
            "mode": mode,
            "install_dir": str(install_root),
            "restore_dir": str(restore),
            "staging_dir": str(staging),
            "backup_dir": str(backup),
            "exe_name": exe_name,
            "wait_pid": os.getpid(),
            "relaunch": True,
            "relaunch_argv": relaunch,
            "startup_ack_path": str(ack),
            "log_path": str(job_root / "updater.log"),
        }
        # a frozen build has no runtime copy per job (runtime None): the updater rejects runtime_dir "None"
        if runtime is not None:
            job["runtime_dir"] = str(runtime)
        job_path = job_root / "rollback_job.json"
        _write_json(job_path, job)
        ack.unlink(missing_ok=True)
        command = (
            [str(executable), "--job", str(job_path)]
            if mode == "frozen"
            else [str(executable), str(helper), "--job", str(job_path)]
        )
        if not _launch_process(command, cwd, ack):
            _app_log(app, "error", "launch_rollback: updater did not acknowledge startup")
            return False
        _app_log(app, "info", f"launch_rollback: updater started for {install_root}")
        return True
    except Exception as exc:
        _app_log(app, "error", f"launch_rollback: {exc}")
        return False
