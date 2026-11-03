"""Focused tests for updater installation permissions and the source Git guard."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import updater_app


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(tmp_path, *, name="flatcam.py", payload=b"payload", mode=0o755):
    archive_path = tmp_path / "update.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
        archive.writestr(info, payload)
    return archive_path


def _manifest(archive: Path, files: dict[str, bytes], *, channel="linux") -> dict:
    return {
        "schema_version": 1,
        "channel": channel,
        "version": "2.0",
        "build": "github:mariusadrian88/flatcam_evo:2",
        "version_date": "2026-10-01",
        "minimum_required_version": "0",
        "release_notes": "",
        "archive": {
            "filename": archive.name,
            "size": archive.stat().st_size,
            "sha256": _hash(archive.read_bytes()),
        },
        "files": [
            {"path": name, "size": len(data), "sha256": _hash(data)} for name, data in files.items()
        ],
        "deletions": [],
    }


def _install(tmp_path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "flatcam.py").write_bytes(b"old entry")
    (install / "requirements.txt").write_text("PyQt6==6.5\n", encoding="utf-8")
    return install


def _job(tmp_path, install: Path, archive: Path, manifest: dict) -> dict:
    update = tmp_path / "update"
    return {
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


def _run_job(tmp_path, job: dict) -> int:
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return updater_app.main(["--job", str(path)])


def test_final_extraction_strips_privilege_bits_and_skips_unknown_modes(monkeypatch, tmp_path):
    privileged = _archive(tmp_path / "privileged", mode=0o4755)
    files = {"flatcam.py": b"payload"}
    calls = []
    monkeypatch.setattr(
        updater_app.os, "chmod", lambda path, mode: calls.append((Path(path), mode))
    )
    updater_app._extract_archive(privileged, tmp_path / "staging", _manifest(privileged, files))
    assert calls == [(tmp_path / "staging" / "flatcam.py", 0o755)]

    plain = _archive(tmp_path / "plain", name="readme.md", payload=b"text", mode=0)
    calls.clear()
    updater_app._extract_archive(
        plain, tmp_path / "staging-plain", _manifest(plain, {"readme.md": b"text"})
    )
    assert calls == []


@pytest.mark.parametrize("as_directory", [True])
def test_source_git_markers_are_refused_before_apply(tmp_path, as_directory):
    install = _install(tmp_path)
    marker = install / ".git"
    if as_directory:
        marker.mkdir()
    else:
        marker.write_text("gitdir: /elsewhere/.git/worktrees/flatcam\n", encoding="utf-8")
    archive_path = _archive(tmp_path, payload=b"new entry")
    files = {"flatcam.py": b"new entry"}
    job = _job(tmp_path, install, archive_path, _manifest(archive_path, files))

    assert _run_job(tmp_path, job) == 2
    assert (install / "flatcam.py").read_bytes() == b"old entry"
    assert not (tmp_path / "update" / "staging").exists()
    assert "Git worktree" in (tmp_path / "update" / "updater.log").read_text(encoding="utf-8")


def test_plain_source_installations_still_apply(tmp_path):
    install = _install(tmp_path)
    archive_path = _archive(tmp_path, payload=b"new entry")
    files = {"flatcam.py": b"new entry"}
    job = _job(tmp_path, install, archive_path, _manifest(archive_path, files))

    assert _run_job(tmp_path, job) == 0
    assert (install / "flatcam.py").read_bytes() == b"new entry"


@pytest.mark.skipif(os.name != "posix", reason="requires UNIX permission bits")
def test_apply_and_rollback_preserve_execute_bits(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    target = install / "flatcam.py"
    target.write_bytes(b"old")
    os.chmod(target, 0o644)
    staging = tmp_path / "staging"
    staged = staging / "flatcam.py"
    staged.parent.mkdir()
    staged.write_bytes(b"new")
    os.chmod(staged, 0o755)

    completed = updater_app.apply_update(install, staging, tmp_path / "backup", [])
    assert stat.S_IMODE(target.stat().st_mode) == 0o755

    assert updater_app.rollback(install, tmp_path / "backup", completed)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
