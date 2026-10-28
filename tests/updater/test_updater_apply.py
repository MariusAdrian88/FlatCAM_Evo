"""Focused, process-free tests for the updater handoff and applier."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.updater.launcher import (
    _creation_flags,
    _install_root,
    _is_frozen,
    launch_rollback,
    launch_update,
)
from services.updater.recovery import (
    blocked_release_path,
    is_release_blocked,
    load_restore_point,
    restore_dir_for_install,
)
from services.updater.archive import DEFAULT_EXCLUSION_POLICY
import services.updater.launcher as launcher_mod
import updater_app


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_archive(path: Path, files: dict[str, bytes], extra=None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        for info, data in extra or ():
            archive.writestr(info, data)


def _manifest(archive: Path, files: dict[str, bytes], deletions=()) -> dict:
    return {
        "schema_version": 1,
        "channel": "linux",
        "version": "2.0",
        "build": "build-2",
        "version_date": "2026-09-17",
        "minimum_required_version": "1.0",
        "release_notes": "",
        "archive": {
            "filename": archive.name,
            "size": archive.stat().st_size,
            "sha256": _hash(archive.read_bytes()),
        },
        "files": [
            {"path": name, "size": len(data), "sha256": _hash(data)}
            for name, data in files.items()
        ],
        "deletions": [
            {"path": name, "version": "1.0"} for name in deletions
        ],
    }


def _install(tmp_path: Path, *, frozen=False) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "flatcam.py").write_bytes(b"old entry")
    (install / "lib").mkdir()
    (install / "lib" / "old.py").write_bytes(b"old file")
    if frozen:
        (install / "FlatCAM.exe").write_bytes(b"old exe")
        (install / "updater").mkdir()
        (install / "updater" / "FlatCAMUpdater.exe").write_bytes(b"updater")
    return install


def _job(tmp_path: Path, install: Path, archive: Path, manifest: dict, **extra) -> dict:
    update = tmp_path / "update"
    job = {
        "schema": 1,
        "action": "update",
        "mode": "source",
        "install_dir": str(install),
        "archive_path": str(archive),
        "staging_dir": str(update / "staging"),
        "backup_dir": str(update / "backup"),
        "restore_dir": str(update / "restore"),
        "manifest": manifest,
        "exe_name": "",
        "wait_pid": 0,
        "relaunch": False,
        "relaunch_argv": [sys.executable, str(install / "flatcam.py")],
        "startup_ack_path": str(update / ".updater_started"),
        "log_path": str(update / "updater.log"),
        "runtime_dir": str(update / "runtime"),
    }
    job.update(extra)
    return job


def _run_job(tmp_path: Path, job: dict) -> int:
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return updater_app.main(["--job", str(path)])


def _rollback_job(root: Path, install: Path, restore: Path, **extra) -> dict:
    update = root / "rollback"
    job = {
        "schema": 1,
        "action": "rollback",
        "mode": "source",
        "install_dir": str(install),
        "restore_dir": str(restore),
        "exe_name": "",
        "staging_dir": str(update / "staging"),
        "backup_dir": str(update / "backup"),
        "wait_pid": 0,
        "relaunch": False,
        "relaunch_argv": [sys.executable, str(install / "flatcam.py")],
        "startup_ack_path": str(update / ".updater_started"),
        "log_path": str(update / "updater.log"),
        "runtime_dir": str(update / "runtime"),
    }
    job.update(extra)
    return job


class TestRecovery(unittest.TestCase):
    def test_restore_metadata_and_payload_are_strictly_validated(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-recovery-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with self.subTest("valid"):
            install = root / "install"
            install.mkdir(exist_ok=True)
            (install / "FlatCAM.exe").write_bytes(b"current")
            payload = b"previous"
            restore = root / "restore"
            (restore / "files").mkdir(parents=True, exist_ok=True)
            (restore / "files" / "lib.py").write_bytes(payload)
            metadata = {
                "schema": 1,
                "install_dir": str(install.resolve()),
                "exe_name": "FlatCAM.exe",
                "previous_version": "1.0",
                "previous_build_string": "build-1",
                "version": "2.0",
                "build_string": "build-2",
                "files": [{"path": "lib.py", "size": len(payload), "sha256": _hash(payload)}],
                "delete": [],
            }
            (restore / "restore.json").write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(metadata, load_restore_point(restore, install))

            metadata["files"][0]["path"] = "../escape"
            (restore / "restore.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_restore_point(restore, install, verify_files=False)

class TestLauncher(unittest.TestCase):
    def test_frozen_launch_requires_protected_updater_and_waits_for_ack(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-frozen-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root, frozen=True)
        archive = root / "release.zip"
        _write_archive(archive, {"flatcam.py": b"new"})
        manifest = _manifest(archive, {"flatcam.py": b"new"})
        app = SimpleNamespace(data_path=str(root / "data"))
        (root / "data").mkdir(exist_ok=True)
        popen_calls = []

        def fake_popen(argv, **kwargs):
            popen_calls.append((argv, kwargs))
            job = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
            ack = Path(job["startup_ack_path"])
            ack.parent.mkdir(parents=True, exist_ok=True)
            ack.write_text("started", encoding="utf-8")

        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(install / "FlatCAM.exe")), patch(
            "services.updater.launcher.subprocess.Popen", fake_popen
        ):
            self.assertTrue(launch_update(app, manifest, archive, relaunch_argv=["FlatCAM.exe"]))

        self.assertEqual(str(install / "updater" / "FlatCAMUpdater.exe"), popen_calls[0][0][0])
        self.assertEqual(0x208 if sys.platform == "win32" else 0, popen_calls[0][1]["creationflags"])

    def test_frozen_update_and_rollback_jobs_pass_the_job_check_of_the_updater(self):
        """A frozen build has no runtime copy per job: the launcher wrote runtime_dir "None", which the updater
        rejects ("runtime_dir must be absolute") before it acknowledges the start, so the app said "The updater could
        not be started." The fake updater checks the job as updater_app.main() does before the acknowledgement."""
        root = Path(tempfile.mkdtemp(prefix="flatcam-frozen-job-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root, frozen=True)
        data = root / "data"
        data.mkdir()
        app = SimpleNamespace(data_path=str(data))
        archive = root / "release.zip"
        _write_archive(archive, {"flatcam.py": b"new"})
        manifest = _manifest(archive, {"flatcam.py": b"new"})
        rejected = []

        def frozen_updater(argv, **kwargs):
            job_path = Path(argv[2])
            job = updater_app.load_job(str(job_path))
            try:
                updater_app._validate_job(job, job_path)
            except ValueError as exc:
                rejected.append(str(exc))  # main() returns 3 without the acknowledgement
                return
            updater_app._write_startup_ack(job)

        exe = str(install / "FlatCAM.exe")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", exe), patch(
            "services.updater.launcher.subprocess.Popen", frozen_updater
        ), patch.object(launcher_mod, "_wait_for_startup_ack", lambda ack: ack.is_file()):
            self.assertTrue(launch_update(app, manifest, archive, relaunch_argv=[exe]), rejected)
            # a restore point of this installation, as an applied update leaves it
            restore = restore_dir_for_install(data.resolve(), Path(exe).resolve().parent)
            self.assertEqual(0, _run_job(root, _job(root, install, archive, manifest, restore_dir=str(restore))))
            self.assertTrue(launch_rollback(app, relaunch_argv=[exe]), rejected)

    def test_source_launch_copies_helper_outside_install(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-source-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = root / "install"
        (install / "services" / "updater").mkdir(parents=True)
        (install / "updater_app.py").write_text("# helper", encoding="utf-8")
        (install / "services" / "updater" / "launcher.py").write_text("# launcher", encoding="utf-8")
        (install / "services" / "updater" / "recovery.py").write_text("# recovery", encoding="utf-8")
        archive = root / "release.zip"
        _write_archive(archive, {"flatcam.py": b"new"})
        manifest = _manifest(archive, {"flatcam.py": b"new"})
        data = root / "data"
        data.mkdir()
        app = SimpleNamespace(data_path=str(data))
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            job = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
            ack = Path(job["startup_ack_path"])
            ack.parent.mkdir(parents=True, exist_ok=True)
            ack.write_text("started", encoding="utf-8")

        with patch("services.updater.launcher.__file__", str(install / "services" / "updater" / "launcher.py")), patch(
            "services.updater.launcher.subprocess.Popen", fake_popen
        ):
            self.assertTrue(launch_update(app, manifest, archive, relaunch_argv=["python", "flatcam.py"]))

        command = calls[0][0]
        helper = Path(command[1] if command[1] != "-m" else command[3])
        self.assertEqual(sys.executable, command[0])
        self.assertNotIn(install.resolve(), helper.resolve().parents)
        self.assertTrue((helper.parent / "recovery.py").exists())

class TestUpdaterApplication(unittest.TestCase):
    def test_archive_checksum_failure_and_zip_slip_do_not_mutate_install(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-archive-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files)
        manifest["archive"]["sha256"] = "0" * 64
        job = _job(root, install, archive, manifest)
        self.assertEqual(2, _run_job(root, job))
        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())

        evil = root / "evil.zip"
        _write_archive(evil, {"flatcam.py": b"new"}, [(zipfile.ZipInfo("../evil.py"), b"evil")])
        evil_manifest = _manifest(evil, files)
        evil_manifest["archive"]["size"] = evil.stat().st_size
        evil_manifest["archive"]["sha256"] = _hash(evil.read_bytes())
        evil_job = _job(root, install, evil, evil_manifest)
        self.assertEqual(2, _run_job(root, evil_job))
        self.assertFalse((root / "evil.py").exists())

        symlink_zip = root / "symlink.zip"
        link_info = zipfile.ZipInfo("lib/link.py")
        link_info.create_system = 3
        link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        _write_archive(symlink_zip, {}, [(link_info, b"outside")])
        symlink_manifest = _manifest(symlink_zip, {"lib/link.py": b"outside"})
        symlink_job = _job(root, install, symlink_zip, symlink_manifest)
        self.assertEqual(2, _run_job(root, symlink_job))
        self.assertFalse((install / "lib" / "link.py").exists())

    def test_source_edits_are_backed_up_obsolete_files_removed_and_restore_is_retained(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-retain-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root)
        (install / "lib" / "old.py").write_bytes(b"local edit")
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib" + "/new.py": b"new file"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files, deletions=["lib/old.py"])
        job = _job(root, install, archive, manifest)

        with patch.object(updater_app.subprocess, "Popen") as popen:
            self.assertEqual(0, _run_job(root, job))
            popen.assert_not_called()

        self.assertEqual(b"new entry", (install / "flatcam.py").read_bytes())
        self.assertEqual(b"new file", (install / "lib" / "new.py").read_bytes())
        self.assertFalse((install / "lib" / "old.py").exists())
        restore = Path(job["restore_dir"])
        metadata = load_restore_point(restore, install)
        self.assertEqual({"flatcam.py", "lib/old.py"}, {item["path"] for item in metadata["files"]})
        self.assertEqual(["lib/new.py"], metadata["delete"])

    def test_mid_apply_failure_rolls_back_and_relaunches_only_after_full_recovery(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-apply-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root)
        archive = root / "release.zip"
        files = {"lib/new-a.py": b"a", "lib/new-b.py": b"b"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files)
        job = _job(root, install, archive, manifest, relaunch=True)
        launched = []
        original_copy = updater_app.shutil.copy2
        calls = {"count": 0}

        def fail_second(source, target, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] >= 2:
                raise OSError("injected apply failure")
            return original_copy(source, target, *args, **kwargs)

        with patch.object(updater_app.shutil, "copy2", fail_second), patch.object(
            updater_app, "relaunch_app", lambda argv, cwd: launched.append((argv, cwd))
        ):
            self.assertEqual(1, _run_job(root, job))

        self.assertFalse((install / "lib" / "new-a.py").exists())
        self.assertFalse((install / "lib" / "new-b.py").exists())
        self.assertEqual(1, len(launched))

    def test_failed_rollback_refuses_relaunch_and_parent_is_waited_for(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-failure-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root)
        archive = root / "release.zip"
        files = {"lib/new.py": b"new"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files)
        job = _job(root, install, archive, manifest, relaunch=True, wait_pid=123)
        waited = []
        launched = []

        def parent_wait(pid, timeout):
            waited.append((pid, timeout))

        with patch.object(updater_app, "wait_for_pid_exit", parent_wait), patch.object(
            updater_app, "_pid_alive", lambda pid: False
        ), patch.object(updater_app, "rollback", lambda *args: False), patch.object(
            updater_app, "relaunch_app", lambda *args: launched.append(args)
        ), patch.object(updater_app.shutil, "copy2", side_effect=OSError("apply failure")):
            self.assertEqual(1, _run_job(root, job))

        self.assertEqual([(123, 60)], waited)
        self.assertEqual([], launched)

    def test_explicit_rollback_marks_blocked_release_consumes_restore_and_uses_argv(self):
        root = Path(tempfile.mkdtemp(prefix="flatcam-ticket2-rollback-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib/new.py": b"new"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files, deletions=["lib/old.py"])
        update_job = _job(root, install, archive, manifest)
        self.assertEqual(0, _run_job(root, update_job))
        restore = Path(update_job["restore_dir"])
        rollback_job = {
            "schema": 1,
            "action": "rollback",
            "mode": "source",
            "install_dir": str(install),
            "restore_dir": str(restore),
            "exe_name": "",
            "wait_pid": 0,
            "relaunch": True,
            "relaunch_argv": [sys.executable, str(install / "flatcam.py"), "--safe"],
            "startup_ack_path": str(root / "rollback-started"),
            "log_path": str(root / "rollback.log"),
        }
        launched = []
        with patch.object(updater_app, "relaunch_app", lambda argv, cwd: launched.append((argv, cwd))):
            self.assertEqual(0, _run_job(root, rollback_job))

        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())
        self.assertEqual(b"old file", (install / "lib" / "old.py").read_bytes())
        self.assertFalse((install / "lib" / "new.py").exists())
        self.assertFalse(restore.exists())
        self.assertTrue(is_release_blocked(restore, "2.0", "build-2"))
        self.assertEqual([rollback_job["relaunch_argv"]], [item[0] for item in launched])


if __name__ == "__main__":
    unittest.main()
