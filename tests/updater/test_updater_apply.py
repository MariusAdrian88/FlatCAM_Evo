"""Focused, process-free tests for the updater handoff and applier."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.updater.launcher import launch_update
from services.updater.recovery import (
    WORK_DIR_NAME,
    is_release_blocked,
    previous_release_path,
    read_previous_release,
    restore_dir_for_install,
)
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


def _manifest(archive: Path, files: dict[str, bytes], deletions=(), version="2.0", build="build-2") -> dict:
    return {
        "schema_version": 1,
        "channel": "linux",
        "version": version,
        "build": build,
        "version_date": "2026-09-17",
        "minimum_required_version": "1.0",
        "release_notes": "",
        "archive": {
            "filename": archive.name,
            "size": archive.stat().st_size,
            "sha256": _hash(archive.read_bytes()),
        },
        "files": [
            {"path": name, "size": len(data), "sha256": _hash(data), "crc32": zlib.crc32(data)}
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
        "restore_dir": str(update / "restore"),
        "manifest": manifest,
        "previous_version": "1.0",
        "previous_build_string": "build-1",
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


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class TestLauncher(unittest.TestCase):
    def test_frozen_update_and_revert_jobs_pass_the_job_check_of_the_updater(self):
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
        # a frozen build holds library packages named "config" (fontTools.config); only the top-level config/ is protected
        files = {"flatcam.py": b"new", "lib/fontTools/config/__init__.pyc": b"pkg", "lib/config/configuration.txt": b"c",
                 "updater/FlatCAMUpdater.exe": b"new updater"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files)
        rejected = []
        jobs = []

        def frozen_updater(argv, **kwargs):
            job_path = Path(argv[2])
            job = updater_app.load_job(str(job_path))
            jobs.append(job)
            try:
                updater_app._validate_job(job, job_path)
            except ValueError as exc:
                rejected.append(str(exc))  # main() returns 3 without the acknowledgement
                return
            updater_app._write_startup_ack(job)

        exe = str(install / "FlatCAM.exe")
        # the launcher moves the archive into the job: the run and the revert get their own copies
        run_archive = root / "run.zip"
        revert_archive = root / "revert.zip"
        shutil.copy2(archive, run_archive)
        shutil.copy2(archive, revert_archive)
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", exe), patch(
            "services.updater.launcher.subprocess.Popen", frozen_updater
        ), patch.object(launcher_mod, "_wait_for_startup_ack", lambda ack: ack.is_file()):
            self.assertTrue(launch_update(app, manifest, archive, relaunch_argv=[exe]), rejected)
            self.assertFalse(archive.exists())
            restore = restore_dir_for_install(data.resolve(), Path(exe).resolve().parent)
            self.assertEqual(0, _run_job(root, _job(root, install, run_archive, manifest, restore_dir=str(restore))))
            self.assertEqual(b"new updater", (install / "updater" / "FlatCAMUpdater.exe").read_bytes())
            # a revert is a job of the same kind with "revert" set
            self.assertTrue(launch_update(app, manifest, revert_archive, relaunch_argv=[exe], revert=True), rejected)
        self.assertNotIn("revert", jobs[0])
        self.assertIs(True, jobs[1]["revert"])


class TestUpdaterApplication(unittest.TestCase):
    def _root(self, prefix):
        root = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_archive_checksum_failure_and_zip_slip_do_not_mutate_install(self):
        root = self._root("flatcam-updater-archive-")
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files)
        manifest["archive"]["sha256"] = "0" * 64
        job = _job(root, install, archive, manifest)
        events = []
        with patch.object(updater_app, "_PROGRESS", updater_app._Progress(events.append)):
            self.assertEqual(2, _run_job(root, job))
        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())
        # the user sees why the update stopped: in the window and in the log
        self.assertEqual("finish", events[-1][0])
        self.assertEqual(2, events[-1][1])
        self.assertIn("archive hash does not match manifest", events[-1][2])
        self.assertIn("archive hash does not match manifest", Path(job["log_path"]).read_text(encoding="utf-8"))

        evil = root / "evil.zip"
        _write_archive(evil, {"flatcam.py": b"new"}, [(zipfile.ZipInfo("../evil.py"), b"evil")])
        evil_job = _job(root, install, evil, _manifest(evil, files))
        # a member outside the manifest is ignored or refused, but never written
        self.assertIn(_run_job(root, evil_job), (0, 2))
        self.assertFalse((root / "evil.py").exists())
        self.assertFalse((install / ".flatcam-update" / ".." / ".." / "evil.py").resolve().exists())

        symlink_zip = root / "symlink.zip"
        link_info = zipfile.ZipInfo("lib/link.py")
        link_info.create_system = 3
        link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        _write_archive(symlink_zip, {}, [(link_info, b"outside")])
        symlink_manifest = _manifest(symlink_zip, {"lib/link.py": b"outside"})
        symlink_job = _job(root, install, symlink_zip, symlink_manifest)
        self.assertEqual(2, _run_job(root, symlink_job))
        self.assertFalse((install / "lib" / "link.py").exists())

    def test_size_or_crc_mismatch_in_the_archive_stops_before_any_install_file_changes(self):
        root = self._root("flatcam-updater-crc-")
        install = _install(root)
        before = _tree(install)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib/new.py": b"new file"}
        _write_archive(archive, files)
        for field, value in (("crc32", zlib.crc32(b"other")), ("size", 3)):
            with self.subTest(field=field):
                manifest = _manifest(archive, files)
                entry = next(item for item in manifest["files"] if item["path"] == "lib/new.py")
                entry[field] = value
                entry.pop("sha256")  # the check is the CRC-32 or the size alone
                self.assertEqual(2, _run_job(root, _job(root, install, archive, manifest)))
                self.assertEqual(before, _tree(install))

    def test_update_records_the_replaced_release_and_removes_work_and_legacy_folders(self):
        root = self._root("flatcam-updater-record-")
        install = _install(root)
        (install / "lib" / "old.py").write_bytes(b"local edit")
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib/new.py": b"new file"}
        _write_archive(archive, files)
        manifest = _manifest(archive, files, deletions=["lib/old.py"])
        job = _job(root, install, archive, manifest)
        restore = Path(job["restore_dir"])
        # the restore points of older releases (a full copy of the files) are not used any more
        for name in (restore.name, restore.name + ".20260101"):
            (restore.parent / name / "files").mkdir(parents=True)
            (restore.parent / name / "restore.json").write_text("{}", encoding="utf-8")
        launched = []

        with patch.object(updater_app, "relaunch_app", lambda argv, cwd: launched.append(argv)):
            self.assertEqual(0, _run_job(root, job))

        self.assertEqual(b"new entry", (install / "flatcam.py").read_bytes())
        self.assertEqual(b"new file", (install / "lib" / "new.py").read_bytes())
        self.assertFalse((install / "lib" / "old.py").exists())
        self.assertFalse((install / WORK_DIR_NAME).exists())
        self.assertEqual([p.name for p in restore.parent.glob("restore*")], [previous_release_path(restore).name])
        self.assertEqual([], launched)
        record = read_previous_release(restore, install)
        self.assertEqual(("1.0", "build-1", "2.0", "build-2"),
                         (record["previous_version"], record["previous_build_string"], record["version"],
                          record["build_string"]))
        self.assertEqual(["lib/new.py"], record["added"])
        # the record belongs to this installation only
        other = root / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            read_previous_release(restore, other)

    def test_mid_apply_failure_restores_the_old_files_and_relaunches_only_after_full_recovery(self):
        root = self._root("flatcam-updater-apply-")
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib/new-a.py": b"a", "lib/new-b.py": b"b"}
        _write_archive(archive, files)
        job = _job(root, install, archive, _manifest(archive, files), relaunch=True)
        launched = []
        original = updater_app._place_file
        calls = {"count": 0}

        def fail_third(source, target):
            calls["count"] += 1
            if calls["count"] >= 3:
                raise OSError("injected apply failure")
            return original(source, target)

        with patch.object(updater_app, "_place_file", fail_third), patch.object(
            updater_app, "relaunch_app", lambda argv, cwd: launched.append((argv, cwd))
        ), patch.object(updater_app, "RETRY_BACKOFF", ()):
            self.assertEqual(1, _run_job(root, job))

        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())
        self.assertFalse((install / "lib" / "new-a.py").exists())
        self.assertFalse((install / "lib" / "new-b.py").exists())
        self.assertFalse((install / WORK_DIR_NAME).exists())
        self.assertFalse(previous_release_path(Path(job["restore_dir"])).exists())
        self.assertEqual(1, len(launched))

    def test_failed_rollback_refuses_relaunch_and_parent_is_waited_for(self):
        root = self._root("flatcam-updater-failure-")
        install = _install(root)
        archive = root / "release.zip"
        files = {"lib/new.py": b"new"}
        _write_archive(archive, files)
        job = _job(root, install, archive, _manifest(archive, files), relaunch=True, wait_pid=123)
        waited = []
        launched = []

        def parent_wait(pid, timeout):
            waited.append((pid, timeout))

        def fail(source, target):
            raise OSError("apply failure")

        with patch.object(updater_app, "wait_for_pid_exit", parent_wait), patch.object(
            updater_app, "_pid_alive", lambda pid: False
        ), patch.object(updater_app, "rollback", lambda *args: False), patch.object(
            updater_app, "relaunch_app", lambda *args: launched.append(args)
        ), patch.object(updater_app, "_place_file", fail), patch.object(updater_app, "RETRY_BACKOFF", ()):
            self.assertEqual(1, _run_job(root, job))

        self.assertEqual([(123, 60)], waited)
        self.assertEqual([], launched)

    def test_revert_job_installs_the_old_release_blocks_the_new_one_and_clears_the_record(self):
        root = self._root("flatcam-updater-revert-")
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new entry", "lib/new.py": b"new"}
        _write_archive(archive, files)
        update_job = _job(root, install, archive, _manifest(archive, files))
        self.assertEqual(0, _run_job(root, update_job))
        restore = Path(update_job["restore_dir"])
        record = read_previous_release(restore, install)

        # what Revert Update does: install the previous release and delete what the update added
        old_files = {"flatcam.py": b"old entry", "lib/old.py": b"old file"}
        old_archive = root / "old.zip"
        _write_archive(old_archive, old_files)
        old_manifest = _manifest(old_archive, old_files, deletions=record["added"], version="1.0", build="build-1")
        launched = []
        revert_job = _job(
            root, install, old_archive, old_manifest, revert=True, relaunch=True,
            previous_version="2.0", previous_build_string="build-2",
            relaunch_argv=[sys.executable, str(install / "flatcam.py"), "--safe"],
        )
        with patch.object(updater_app, "relaunch_app", lambda argv, cwd: launched.append(argv)):
            self.assertEqual(0, _run_job(root, revert_job))

        self.assertEqual(old_files, _tree(install))
        self.assertTrue(is_release_blocked(restore, "2.0", "build-2"))
        self.assertFalse(is_release_blocked(restore, "1.0", "build-1"))
        self.assertFalse(previous_release_path(restore).exists())
        self.assertFalse((install / WORK_DIR_NAME).exists())
        self.assertEqual([revert_job["relaunch_argv"]], launched)

    def test_old_rollback_jobs_are_refused(self):
        root = self._root("flatcam-updater-oldjob-")
        install = _install(root)
        archive = root / "release.zip"
        files = {"flatcam.py": b"new"}
        _write_archive(archive, files)
        job = _job(root, install, archive, _manifest(archive, files), action="rollback")
        self.assertEqual(3, _run_job(root, job))
        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
