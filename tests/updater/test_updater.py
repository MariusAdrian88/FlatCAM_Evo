from __future__ import annotations


# ======================================================================
# merged from tests/updater/test_updater_integration.py
# ======================================================================
"""Focused, process-free tests for the updater integration."""


import hashlib
import json
import os
import sys
import types
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from services.updater.manifest import ArchiveInfo, FileEntry, Manifest
from services.updater.github.transport import DEFAULT_REPOSITORY as DEFAULT_UPDATE_URL

CONFIGURED_UPDATE_URL = "https://updates.example.invalid/flatcam"


def _manifest(version="2.0", build="build-2", minimum="0", channel="windows"):
    archive = b"full release archive"
    return Manifest(
        schema_version=1,
        channel=channel,
        version=version,
        build_string=build,
        version_date="2026-09-18",
        minimum_required_version=minimum,
        release_notes="Fixes\n\n* Safer updates",
        archive=ArchiveInfo("flatcam-windows.zip", len(archive), hashlib.sha256(archive).hexdigest()),
        files=(FileEntry("flatcam.py", 3, hashlib.sha256(b"new").hexdigest()),),
        deletions=(),
    )


def _manifest_json(manifest):
    return json.dumps(
        {
            "schema_version": manifest.schema_version,
            "channel": manifest.channel,
            "version": manifest.version,
            "build": manifest.build_string,
            "version_date": manifest.version_date,
            "minimum_required_version": manifest.minimum_required_version,
            "release_notes": manifest.release_notes,
            "archive": {
                "filename": manifest.archive.filename,
                "size": manifest.archive.size,
                "sha256": manifest.archive.sha256,
            },
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in manifest.files
            ],
            "deletions": [],
        }
    ).encode()


class FakeSignal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


class FakeTransport:
    def __init__(self, manifest=None, error=None):
        self.manifest = manifest
        self.error = error
        self.calls = []

    def download_bytes(self, channel, filename, **kwargs):
        self.calls.append(("bytes", channel, filename, kwargs))
        if self.error:
            raise self.error
        return _manifest_json(self.manifest)


def _checker_app(tmp_path, enabled=True):
    return SimpleNamespace(
        options={"global_version_check": enabled},
        defaults={"global_version_check": enabled},
        version="1.0",
        build_string="build-1",
        data_path=str(tmp_path),
        log=MagicMock(),
        inform=FakeSignal(),
        update_available=FakeSignal(),
        update_check_result=FakeSignal(),
    )


def _startup_app(auto_updates, beta=False):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.headless = False
    app.new_launch = None
    app.file_open_filter = None
    app.log = MagicMock()
    app.cmd_line_shellvar = ""
    app.cmd_line_shellfile = ""
    app.trayIcon = SimpleNamespace(show=MagicMock())
    app.splash = None
    app.ui = MagicMock()
    app.options = {
        "global_systray_icon": False,
        "global_version_check": auto_updates,
        "global_welcome": False,  # This fixture tests the direct-startup update check, not a pending user decision.
    }
    app.beta = beta
    app.version = "1.0"
    app.refresh_rollback_action = MagicMock()
    app._queue_version_check = MagicMock()
    # the update check starts when the files of the command line are open (none here)
    app._open_requests = deque()
    app._open_busy = False
    for name in ("_drain_open_requests", "_start_deferred_version_check"):
        setattr(app, name, types.MethodType(getattr(App, name), app))
    app.defaults = AppDefaults(beta=beta, version="1.0")
    app.inform = FakeSignal()
    return app


def test_startup_update_toggle_gates_automatic_queueing(monkeypatch):
    from appMain import App

    monkeypatch.setattr(App, "args", [])
    monkeypatch.setattr("appMain.QSettings", MagicMock())  # the window state is not read from the real settings
    disabled = _startup_app(False)
    App._setup_startup(disabled)
    disabled._queue_version_check.assert_not_called()

    enabled = _startup_app(True)
    App._setup_startup(enabled)
    enabled._queue_version_check.assert_called_once_with()


def _staged_update(tmp_path, **overrides):
    from services.updater.github.staging import StagedUpdate

    directory = tmp_path / "staging"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"full release archive")
    manifest = {
        "version": "2.0",
        "build": "build-2",
        "archive": {"filename": archive.name, "size": archive.stat().st_size, "sha256": "a" * 64},
        "files": [],
        "deletions": [],
    }
    values = dict(
        staging_dir=directory,
        archive_path=archive,
        manifest=manifest,
        channel="windows",
        install="windows_asset",
        trust="Update archive verified against the published GitHub SHA-256 digest.",
        dependencies={"changed": False, "added": [], "removed": []},
    )
    values.update(overrides)
    return StagedUpdate(**values)


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_download_worker_reports_cancellation_and_resets_state(tmp_path):
    from appMain import App
    from services.updater.common import DownloadCancelled

    app = _fake_app(tmp_path)
    app.update_checker = SimpleNamespace(create_transport=lambda app, repository_url=None: object())
    with patch("appMain.stage_update", side_effect=DownloadCancelled("cancelled")):
        App._download_update_worker(
            app,
            {"status": "update_available"},
            SimpleNamespace(is_set=lambda: False),
        )

    assert app.update_staged.emissions[0][0]["canceled"] is True


def _fake_app(tmp_path):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.data_path = str(tmp_path)
    app.options = {}
    app.defaults = AppDefaults(beta=False, version="1.0")
    app.log = MagicMock()
    app.inform = FakeSignal()
    app.update_progress = FakeSignal()
    app.update_staged = FakeSignal()
    app.worker_task = FakeSignal()
    app.ui = SimpleNamespace(hide=MagicMock())
    app._update_dialog = None
    app._update_progress_dialog = None
    app._update_in_progress = False
    app._update_cancel_event = None
    app._shutdown_requested = False
    app.quit_application = MagicMock()
    app._updater_shutdown_preflight = MagicMock(return_value=True)
    return app


def test_update_launches_then_shutdowns_only_after_launcher_ack(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)

    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})

    launch.assert_called_once()
    app.quit_application.assert_called_once_with(mode="updater")


def test_failed_launch_and_cancelled_shutdown_never_quit(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    app.quit_application.assert_not_called()

    app = _fake_app(tmp_path)
    app._updater_shutdown_preflight.return_value = False
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    launch.assert_not_called()
    app.quit_application.assert_not_called()


@pytest.mark.parametrize("active_state", ["operation"])
def test_main_window_close_is_ignored_while_updater_is_active(active_state):
    from appGUI.MainGUI import MainGUI

    class Event:
        def __init__(self):
            self.ignore_calls = 0

        def ignore(self):
            self.ignore_calls += 1

    app = SimpleNamespace(
        save_in_progress=False,
        _check_in_progress=active_state == "checking",
        _check_queued=False,
        _update_operation_active=lambda: active_state == "operation",
        inform=FakeSignal(),
    )
    gui = MainGUI.__new__(MainGUI)
    gui.app = app
    gui.final_save = SimpleNamespace(emit=MagicMock())
    gui.geometry = MagicMock(return_value=SimpleNamespace(x=lambda: 0, y=lambda: 0, width=lambda: 1, height=lambda: 1))
    gui.lock_action = SimpleNamespace(isChecked=lambda: False)
    gui.show_text_action = SimpleNamespace(isChecked=lambda: False)
    gui.isMaximized = lambda: False
    gui.saveState = lambda *_args: b"state"
    gui.splitter = SimpleNamespace(sizes=lambda: [1])
    event = Event()

    with patch("appGUI.MainGUI.QSettings"):
        gui.closeEvent(event)

    assert event.ignore_calls == 1
    gui.final_save.emit.assert_not_called()
    assert "cancel" in app.inform.emissions[-1][0].lower()
    assert "wait" in app.inform.emissions[-1][0].lower()


def test_revert_update_needs_a_previous_release_record_and_installs_it_as_a_revert_job(monkeypatch, tmp_path):
    from appMain import App, QtWidgets

    class Action:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = value

        def setToolTip(self, value):
            self.tooltip = value

    app = _fake_app(tmp_path)
    app.ui.menuhelp_revert_update = Action()
    app._load_previous_release = MagicMock(side_effect=OSError("no previous release is recorded"))
    App.refresh_rollback_action(app)
    assert app.ui.menuhelp_revert_update.enabled is False

    app._load_previous_release = MagicMock(return_value={
        "version": "2.0",
        "build_string": "build-2",
        "previous_version": "1.0",
        "previous_build_string": "build-1",
        "added": ["lib/new.py"],
    })
    App.refresh_rollback_action(app)
    assert app.ui.menuhelp_revert_update.enabled is True

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    app._start_update_download = MagicMock()
    App.on_revert_update(app)
    started = app._start_update_download.call_args.args[0]
    staged_payloads = []
    staged = SimpleNamespace(
        staging_dir=tmp_path / "stage", archive_path=tmp_path / "stage" / "old.zip", manifest=_manifest_json(_manifest()),
        channel="windows", install="windows_asset", trust="", dependencies={},
    )
    checker = SimpleNamespace(
        provider="github",
        create_transport=lambda *args, **kwargs: object(),
        revert_payload=lambda _app, version, transport=None: {"revert": True, "version": version},
    )
    monkeypatch.setattr("appMain.checker_for", lambda _app: checker)
    monkeypatch.setattr("appMain.stage_update", lambda _app, payload, **kwargs: staged_payloads.append(payload) or staged)
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)

    App._download_update_worker(app, started, MagicMock(is_set=lambda: False))
    # the previous release is looked up by its version; the files that the update added are deleted by the revert
    assert staged_payloads == [{"revert": True, "version": "1.0", "revert_delete": ["lib/new.py"]}]
    App.on_update_staged(app, app.update_staged.emissions[0][0])

    launch.assert_called_once()
    assert launch.call_args.kwargs["revert"] is True
    app.quit_application.assert_called_once_with(mode="updater")


# ======================================================================
# merged from tests/updater/test_provider.py
# ======================================================================
"""Update provider selection: GitHub by default, Digi as an explicit legacy source."""


import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from defaults import AppDefaults, AppOptions
from services.updater.digi.checker import UpdateChecker
from services.updater.digi.transport import DEFAULT_UPDATE_URL as DIGI_URL
from services.updater.github.checker import GitHubUpdateChecker
from services.updater.github.transport import DEFAULT_REPOSITORY
from services.updater.provider import (
    create_update_checker,
    provider_url,
    update_provider,
)


def _provider_app(**options):
    return SimpleNamespace(
        options=options,
        defaults={
            "global_update_url": DEFAULT_REPOSITORY,
            "global_legacy_update_url": DIGI_URL,
        },
        update_checker=None,
    )


def test_default_provider_is_github():
    assert AppDefaults.factory_defaults["global_update_provider"] == "github"
    assert AppDefaults.factory_defaults["global_legacy_update_url"] == DIGI_URL
    assert update_provider(_provider_app()) == "github"
    checker = create_update_checker(_provider_app())
    assert isinstance(checker, GitHubUpdateChecker)
    assert checker.repository_url == DEFAULT_REPOSITORY


def test_digi_download_stages_the_archive_and_emits_the_manifest(monkeypatch, tmp_path):
    from appMain import App

    manifest = _manifest()
    seen = {}

    class DigiTransport:
        def download_archive(self, channel, filename, destination, **kwargs):
            seen.update(channel=channel, filename=filename, kwargs=kwargs)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"full release archive")
            return destination

    class Checker:
        provider = "digi"

        def create_transport(self, app, *, share_url=None):
            seen["share_url"] = share_url
            return DigiTransport()

    app = _fake_app(tmp_path)
    app.options = {"global_update_provider": "digi"}
    app.update_checker = Checker()
    payload = {"status": "update_available", "manifest": manifest, "channel": "windows"}

    App._download_update_worker(app, payload, SimpleNamespace(is_set=lambda: False))

    data = app.update_staged.emissions[0][0]
    assert data["canceled"] is False
    assert data["manifest"] is manifest
    assert data["channel"] == "windows"
    assert data["archive_path"].endswith(manifest.archive.filename)
    assert os.path.isfile(data["archive_path"])
    assert data["staging_dir"] == os.path.dirname(data["archive_path"])
    assert os.path.join("update", "staging_") in data["staging_dir"]
    assert seen["share_url"] == DIGI_URL
    assert seen["kwargs"]["expected_size"] == manifest.archive.size
    assert seen["kwargs"]["expected_sha256"] == manifest.archive.sha256


def test_old_digi_url_migrates_but_stays_available_as_legacy_url(tmp_path):
    filename = tmp_path / "current_defaults.FlatConfig"
    filename.write_text(json.dumps({"global_update_url": DIGI_URL}))

    defaults = AppDefaults(beta=False, version="1.0")
    defaults.load(str(filename), MagicMock())

    assert defaults["global_update_url"] == DEFAULT_REPOSITORY
    assert defaults["global_legacy_update_url"] == DIGI_URL
    assert defaults["global_update_provider"] == "github"


def test_imported_preferences_do_not_change_the_update_source(tmp_path):
    # the user chose the Digi source and a share in Preferences
    defaults = AppDefaults(beta=False, version="1.0")
    options = AppOptions(version="1.0", baseline=defaults, defaults=defaults)
    options["global_update_provider"] = "digi"
    options["global_legacy_update_url"] = "https://s.example.invalid/mine"
    # a received preferences file names another source
    filename = tmp_path / "received.FlatConfig"
    filename.write_text(json.dumps({
        "global_update_provider": "github",
        "global_update_url": "https://github.com/example/other",
        "global_legacy_update_url": "https://s.example.invalid/other",
        "global_version_check": False,
    }))

    # Import Preferences (File > Backup)
    options.load(str(filename), MagicMock(), baseline=defaults, include_app_keys=True)

    app = SimpleNamespace(options=options, defaults=defaults)
    assert defaults["global_version_check"] is False
    assert update_provider(app) == "digi"
    assert provider_url(app) == "https://s.example.invalid/mine"
    assert provider_url(app, "github") == DEFAULT_REPOSITORY


def test_download_uses_the_provider_of_the_offer_not_the_current_preference(monkeypatch, tmp_path):
    from appMain import App

    seen = {}

    class GitHubChecker:
        provider = "github"

        def create_transport(self, app, *, repository_url=None):
            seen["repository_url"] = repository_url
            return "github-transport"

    def fake_stage_update(app, payload, *, transport, cancel_cb, progress_cb):
        seen["transport"] = transport
        raise RuntimeError("stop after routing")

    app = _fake_app(tmp_path)
    # The user switched to Digi after the GitHub check produced this offer.
    app.options = {"global_update_provider": "digi"}
    app.update_checker = SimpleNamespace(provider="digi")
    monkeypatch.setattr("appMain.create_update_checker", lambda app, provider=None, url=None: GitHubChecker())
    monkeypatch.setattr("appMain.stage_update", fake_stage_update)
    payload = {
        "status": "update_available",
        "provider": "github",
        "provider_url": "https://github.com/example/fork",
        "channel": "windows",
    }

    App._download_update_worker(app, payload, SimpleNamespace(is_set=lambda: False))

    assert seen == {"repository_url": "https://github.com/example/fork", "transport": "github-transport"}
    data = app.update_staged.emissions[0][0]
    assert data["error"] == "stop after routing"


# ======================================================================
# merged from tests/updater/test_manifest.py
# ======================================================================
"""Focused tests for the updater manifest core, Digi transport and release preparation."""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from services.updater.manifest import (
    ArchiveInfo,
    DeletionEntry,
    FileEntry,
    ManifestError,
    Manifest,
    compare_versions,
    manifest_to_json,
    parse_manifest,
)
from services.updater.digi.transport import (
    DigiPublicShareTransport,
    DownloadCancelled,
)


LINK_ID = "28e6f3bf-0636-46b7-8bdc-d1f7b105f7a0"
DYNAMIC_LINK_ID = "6f1d2a3b-4c5d-4e6f-8a7b-9c0d1e2f3a4b"
SECOND_DYNAMIC_LINK_ID = "7a2e3b4c-5d6f-4a7b-8c9d-0e1f2a3b4c5d"
SHORT_URL = "https://s.go.ro/zihniipc"
CANONICAL_URL = f"https://storage.rcs-rds.ro/links/{LINK_ID}"
ORIGIN_URL = "https://storage.rcs-rds.ro"
METADATA_URL = f"{ORIGIN_URL}/api/v2/public/links/{LINK_ID}"
LISTING_URL = f"{METADATA_URL}/bundle?path=%2Fwindows"
CONTENT_URL = (
    f"{ORIGIN_URL}/content/links/{LINK_ID}/files/get/flatcam-windows.zip"
    "?path=%2Fwindows%2Fflatcam-windows.zip"
)


class FakeResponse:
    def __init__(self, body=b"", url=None, headers=None, status=200):
        self._body = body
        self._position = 0
        self.url = url
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def geturl(self):
        return self.url

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body) - self._position
        end = min(self._position + size, len(self._body))
        result = self._body[self._position:end]
        self._position = end
        return result


class DigiOpener:
    def __init__(self, archive=b"archive-bytes", link_id=LINK_ID, redirect_url=None, metadata_id=None):
        self.archive = archive
        self.link_id = link_id
        self.redirect_url = redirect_url
        self.metadata_id = metadata_id
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        url = request.full_url
        canonical_url = self.redirect_url or f"https://storage.rcs-rds.ro/links/{self.link_id}"
        metadata_url = f"https://storage.rcs-rds.ro/api/v2/public/links/{self.link_id}"
        listing_url = f"{metadata_url}/bundle?path=%2Fwindows"
        content_url = (
            f"https://storage.rcs-rds.ro/content/links/{self.link_id}/files/get/flatcam-windows.zip"
            "?path=%2Fwindows%2Fflatcam-windows.zip"
        )
        if url == SHORT_URL:
            return FakeResponse(url=canonical_url)
        if url == metadata_url:
            body = json.dumps({"id": self.metadata_id or self.link_id, "public": True}).encode()
            return FakeResponse(body=body, url=url)
        if url == listing_url:
            body = json.dumps({
                "files": [{"name": "flatcam-windows.zip", "size": len(self.archive)}]
            }).encode()
            return FakeResponse(body=body, url=url)
        if url == content_url:
            return FakeResponse(
                body=self.archive,
                url=url,
                headers={"Content-Length": str(len(self.archive))},
            )
        raise AssertionError(f"unexpected URL: {url}")


class TestManifestCore(unittest.TestCase):
    def test_version_comparison_orders_release_then_build(self):
        self.assertLess(compare_versions("1.2", "1.3"), 0)
        self.assertGreater(compare_versions("1.3", "1.2"), 0)
        self.assertGreater(compare_versions("1.3", "1.3", "build 12", "build 11"), 0)

    def test_manifest_round_trip_contains_archive_and_deletion_history(self):
        manifest = Manifest(
            schema_version=1,
            channel="linux",
            version="1.2.3",
            build_string="build 12",
            version_date="2026-09-17",
            minimum_required_version="1.0",
            release_notes="Bug fixes",
            archive=ArchiveInfo("flatcam-source.zip", 12, "a" * 64),
            files=(FileEntry("flatcam.py", 4, "b" * 64),),
            deletions=(DeletionEntry("old.py", "1.2.0"),),
        )

        encoded = manifest_to_json(manifest)
        parsed = parse_manifest(encoded)

        self.assertEqual(manifest, parsed)
        self.assertEqual(encoded, manifest_to_json(parsed))

    def test_manifest_rejects_malformed_required_data_and_traversal(self):
        base = {
            "schema_version": 1,
            "channel": "linux",
            "version": "1.0",
            "build": "build 1",
            "version_date": "2026-09-17",
            "minimum_required_version": "0",
            "release_notes": "",
            "archive": {"filename": "flatcam-source.zip", "size": 1, "sha256": "a" * 64},
            "files": [{"path": "flatcam.py", "size": 1, "sha256": "b" * 64}],
            "deletions": [],
        }

        for field in ("archive", "files", "version"):
            malformed = dict(base)
            malformed.pop(field)
            with self.subTest(field=field):
                with self.assertRaises(ManifestError):
                    parse_manifest(malformed)

        malformed = dict(base)
        malformed["files"] = [{"path": "../evil", "size": 1, "sha256": "b" * 64}]
        with self.assertRaises(ManifestError):
            parse_manifest(malformed)

        malformed = dict(base)
        malformed["archive"] = {
            "filename": "C:/evil.zip",
            "size": 1,
            "sha256": "a" * 64,
        }
        with self.assertRaises(ManifestError):
            parse_manifest(malformed)

class TestDigiTransport(unittest.TestCase):
    def _transport(self, archive=b"archive-bytes"):
        opener = DigiOpener(archive)
        transport = DigiPublicShareTransport(
            SHORT_URL,
            expected_link_id=LINK_ID,
            opener=opener,
            timeout=7,
        )
        return transport, opener

    def test_stream_download_cancellation_removes_partial_and_preserves_destination(self):
        transport, _ = self._transport(archive=b"0123456789")
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "flatcam-windows.zip"
            destination.write_bytes(b"old archive")
            calls = []

            def cancel():
                calls.append(True)
                return len(calls) > 1

            with self.assertRaises(DownloadCancelled):
                transport.download_archive(
                    "windows",
                    "flatcam-windows.zip",
                    destination,
                    cancel_cb=cancel,
                )

            self.assertEqual(b"old archive", destination.read_bytes())
            self.assertFalse(Path(str(destination) + ".part").exists())

    def test_stream_download_verifies_hash_and_replaces_atomically(self):
        payload = b"known archive"
        transport, _ = self._transport(archive=payload)
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "flatcam-windows.zip"
            destination.write_bytes(b"old archive")
            observed = []

            def progress(written, total):
                observed.append((written, total, destination.read_bytes()))

            result = transport.download_archive(
                "windows",
                "flatcam-windows.zip",
                destination,
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                progress_cb=progress,
            )

            self.assertEqual(destination, result)
            self.assertEqual(payload, destination.read_bytes())
            self.assertTrue(observed)
            self.assertTrue(all(old == b"old archive" for _, _, old in observed))


# ======================================================================
# merged from tests/updater/test_updater_apply.py
# ======================================================================
"""Focused, process-free tests for the updater handoff and applier."""


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


def _manifest_m4(archive: Path, files: dict[str, bytes], deletions=(), version="2.0", build="build-2") -> dict:
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
        manifest = _manifest_m4(archive, files)
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
        manifest = _manifest_m4(archive, files)
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
        evil_job = _job(root, install, evil, _manifest_m4(evil, files))
        # a member outside the manifest is ignored or refused, but never written
        self.assertIn(_run_job(root, evil_job), (0, 2))
        self.assertFalse((root / "evil.py").exists())
        self.assertFalse((install / ".flatcam-update" / ".." / ".." / "evil.py").resolve().exists())

        symlink_zip = root / "symlink.zip"
        link_info = zipfile.ZipInfo("lib/link.py")
        link_info.create_system = 3
        link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        _write_archive(symlink_zip, {}, [(link_info, b"outside")])
        symlink_manifest = _manifest_m4(symlink_zip, {"lib/link.py": b"outside"})
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
                manifest = _manifest_m4(archive, files)
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
        manifest = _manifest_m4(archive, files, deletions=["lib/old.py"])
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
        job = _job(root, install, archive, _manifest_m4(archive, files), relaunch=True)
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
        job = _job(root, install, archive, _manifest_m4(archive, files), relaunch=True, wait_pid=123)
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
        update_job = _job(root, install, archive, _manifest_m4(archive, files))
        self.assertEqual(0, _run_job(root, update_job))
        restore = Path(update_job["restore_dir"])
        record = read_previous_release(restore, install)

        # what Revert Update does: install the previous release and delete what the update added
        old_files = {"flatcam.py": b"old entry", "lib/old.py": b"old file"}
        old_archive = root / "old.zip"
        _write_archive(old_archive, old_files)
        old_manifest = _manifest_m4(old_archive, old_files, deletions=record["added"], version="1.0", build="build-1")
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
        job = _job(root, install, archive, _manifest_m4(archive, files), action="rollback")
        self.assertEqual(3, _run_job(root, job))
        self.assertEqual(b"old entry", (install / "flatcam.py").read_bytes())


# ======================================================================
# merged from tests/updater/test_updater_permissions.py
# ======================================================================
"""Focused tests for updater installation permissions and the source Git guard."""


import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import updater_app


def _archive(tmp_path, *, name="flatcam.py", payload=b"payload", mode=0o755):
    archive_path = tmp_path / "update.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
        archive.writestr(info, payload)
    return archive_path


def _manifest_m5(archive: Path, files: dict[str, bytes], *, channel="linux") -> dict:
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


def _install_m5(tmp_path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "flatcam.py").write_bytes(b"old entry")
    (install / "requirements.txt").write_text("PyQt6==6.5\n", encoding="utf-8")
    return install


def _job_m5(tmp_path, install: Path, archive: Path, manifest: dict) -> dict:
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


def _run_job_m5(tmp_path, job: dict) -> int:
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
    updater_app._extract_archive(privileged, tmp_path / "staging", _manifest_m5(privileged, files))
    assert calls == [(tmp_path / "staging" / "flatcam.py", 0o755)]

    plain = _archive(tmp_path / "plain", name="readme.md", payload=b"text", mode=0)
    calls.clear()
    updater_app._extract_archive(
        plain, tmp_path / "staging-plain", _manifest_m5(plain, {"readme.md": b"text"})
    )
    assert calls == []


@pytest.mark.parametrize("as_directory", [True])
def test_source_git_markers_are_refused_before_apply(tmp_path, as_directory):
    install = _install_m5(tmp_path)
    marker = install / ".git"
    if as_directory:
        marker.mkdir()
    else:
        marker.write_text("gitdir: /elsewhere/.git/worktrees/flatcam\n", encoding="utf-8")
    archive_path = _archive(tmp_path, payload=b"new entry")
    files = {"flatcam.py": b"new entry"}
    job = _job_m5(tmp_path, install, archive_path, _manifest_m5(archive_path, files))

    assert _run_job_m5(tmp_path, job) == 2
    assert (install / "flatcam.py").read_bytes() == b"old entry"
    assert not (tmp_path / "update" / "staging").exists()
    assert "Git worktree" in (tmp_path / "update" / "updater.log").read_text(encoding="utf-8")


def test_plain_source_installations_still_apply(tmp_path):
    install = _install_m5(tmp_path)
    archive_path = _archive(tmp_path, payload=b"new entry")
    files = {"flatcam.py": b"new entry"}
    job = _job_m5(tmp_path, install, archive_path, _manifest_m5(archive_path, files))

    assert _run_job_m5(tmp_path, job) == 0
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


# ======================================================================
# merged from tests/updater/test_updater_dialogs.py
# ======================================================================
"""Updater dialog tests: GitHub wiring, pre-manifest UI, staged installs."""


import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui, QtTest, QtWidgets


class Signal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


def _fake_app_m6(tmp_path):
    from appMain import App
    from defaults import AppDefaults as Defaults

    app = App.__new__(App)
    app.data_path = str(tmp_path)
    app.options = {}
    app.defaults = Defaults(beta=False, version="1.0")
    app.log = MagicMock()
    app.inform = Signal()
    app.update_progress = Signal()
    app.update_staged = Signal()
    app.worker_task = Signal()
    app.ui = None
    app._update_dialog = None
    app._update_progress_dialog = None
    app._update_in_progress = False
    app._update_cancel_event = None
    app._release_preparation_in_progress = False
    app._manual_update_requested = False
    app._check_in_progress = False
    app._check_queued = False
    app.save_in_progress = False
    app.quit_application = MagicMock()
    app._updater_shutdown_preflight = MagicMock(return_value=True)
    return app


def _payload(**overrides):
    payload = {
        "status": "update_available",
        "channel": "windows",
        "mandatory": False,
        "current": {"version": "1.0", "build": ""},
        "current_version": "1.0",
        "current_build": "",
        "tag": "v1.1",
        "version": "1.1",
        "build_string": "github:mariusadrian88/flatcam_evo:1.1",
        "release_notes": "Plain <b>notes</b>",
        "published_at": "2026-09-17T10:00:00Z",
        "release_page": "https://github.com/MariusAdrian88/FlatCAM_Evo/releases/tag/v1.1",
        "zipball_url": "https://api.github.com/repos/MariusAdrian88/FlatCAM_Evo/zipball/v1.1",
        "asset": {
            "name": "flatcam-evo-windows-x64-update.zip",
            "url": "https://example.invalid/flatcam-evo-windows-x64-update.zip",
            "size": 2048,
            "sha256": "a" * 64,
        },
        "deletions_asset": None,
        "install": "windows_asset",
        "automatic": True,
        "manual": False,
    }
    payload.update(overrides)
    return payload


def _manifest_dict():
    return {
        "schema_version": 1,
        "channel": "windows",
        "version": "1.1",
        "build": "github:mariusadrian88/flatcam_evo:1.1",
        "version_date": "2026-09-17T10:00:00Z",
        "minimum_required_version": "0",
        "release_notes": "notes",
        "archive": {"filename": "flatcam-evo-windows-x64-update.zip", "size": 3, "sha256": "b" * 64},
        "files": [],
        "deletions": [],
    }


@pytest.mark.parametrize("image_markup", [
    '<img width="2944" height="1744" alt="viewer" src="{url}" />',
    '![viewer]({url})',
])
def test_release_images_load_without_waiting_for_the_network(qapp, image_markup):
    from appGUI.UpdateDialog import UpdateDialog

    image = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("red"))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(buffer.data())
    requested = threading.Event()
    send_image = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.set()
            send_image.wait(5)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%s/image.png" % server.server_port
    dialog = None
    try:
        start = time.monotonic()
        dialog = UpdateDialog(None, _payload(release_notes=(
            "# Highlights\n\n" + image_markup.format(url=url) + "\n\nPlain <b>notes</b>"
        )))
        dialog.open()
        assert time.monotonic() - start < 1
        notes = dialog.findChild(QtWidgets.QTextBrowser)
        assert '<img' not in notes.toPlainText()
        assert "<b>notes</b>" in notes.toPlainText()
        deadline = time.monotonic() + 3
        while not requested.is_set() and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        assert requested.is_set()
        assert dialog.isVisible()
        send_image.set()
        loaded = None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
            loaded = notes.document().resource(QtGui.QTextDocument.ResourceType.ImageResource, QtCore.QUrl(url))
            if isinstance(loaded, QtGui.QImage) and not loaded.isNull():
                break
        assert isinstance(loaded, QtGui.QImage) and not loaded.isNull()
        assert loaded.pixelColor(0, 0) == QtGui.QColor("red")
    finally:
        send_image.set()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dependency_warning_cancels_before_preflight_and_launch(qapp, monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app_m6(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-linux-source.zip"
    archive.write_bytes(b"source")
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    observed = {}

    def cancel_dialog(box):
        observed["format"] = box.textFormat()
        observed["text"] = box.text()
        observed["default"] = box.defaultButton().text()
        return QtWidgets.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", cancel_dialog)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        MagicMock(side_effect=AssertionError("untested static prompt")),
    )

    App.on_update_staged(app, {
        "staging_dir": str(staging),
        "archive_path": str(archive),
        "manifest": _manifest_dict() | {"channel": "linux"},
        "channel": "linux",
        "trust": "Source archive downloaded over HTTPS from GitHub; TLS only, no published digest.",
        "dependencies": {"changed": True, "added": ["newdep<2.0"], "removed": []},
    })

    launch.assert_not_called()
    app._updater_shutdown_preflight.assert_not_called()
    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert observed["format"] == QtCore.Qt.TextFormat.PlainText
    assert "newdep<2.0" in observed["text"]
    assert observed["default"] == "Cancel"


def test_cancellation_after_worker_success_prevents_the_gui_launch(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app_m6(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"archive")
    staged = SimpleNamespace(
        staging_dir=staging,
        archive_path=archive,
        manifest=_manifest_dict(),
        channel="windows",
        install="windows_asset",
        trust="Update archive verified against the published GitHub SHA-256 digest.",
        dependencies={"changed": False, "added": [], "removed": []},
    )
    monkeypatch.setattr("appMain.stage_update", lambda *args, **kwargs: staged)
    app.update_checker = SimpleNamespace(create_transport=lambda app, repository_url=None: object())

    cancel = threading.Event()
    App._download_update_worker(app, {"status": "update_available"}, cancel)
    data = app.update_staged.emissions[0][0]
    assert data["canceled"] is False

    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    app._update_cancel_event = cancel
    app._update_in_progress = True
    cancel.set()

    App.on_update_staged(app, data)

    launch.assert_not_called()
    app._updater_shutdown_preflight.assert_not_called()
    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert any("canceled" in emission[0].lower() for emission in app.inform.emissions)


def test_failed_helper_stays_running_and_removes_the_stage(qapp, monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app_m6(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "exec",
        lambda _box: QtWidgets.QMessageBox.StandardButton.Yes,
    )

    App.on_update_staged(app, {
        "staging_dir": str(staging),
        "archive_path": str(archive),
        "manifest": _manifest_dict(),
        "channel": "windows",
        "trust": "Update archive verified against the published GitHub SHA-256 digest.",
        "dependencies": {"changed": False, "added": [], "removed": []},
    })

    app.quit_application.assert_not_called()
    assert not staging.exists()
    assert app._update_in_progress is False
    assert app._update_cancel_event is None
    assert any("updater could not be started" in emission[0].lower() for emission in app.inform.emissions)


def _launcher_fixture(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    archive = staging / "flatcam-evo-windows-x64-update.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("flatcam.py", b"new entry")
    data = archive.read_bytes()
    manifest = {
        "schema_version": 1,
        "channel": "windows",
        "version": "2.0",
        "build": "build-2",
        "version_date": "2026-09-17",
        "minimum_required_version": "0",
        "release_notes": "",
        "archive": {
            "filename": archive.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "files": [{"path": "flatcam.py", "size": 9, "sha256": hashlib.sha256(b"new entry").hexdigest()}],
        "deletions": [],
    }
    return staging, archive, manifest


def test_launcher_owns_a_verified_archive_copy_before_stage_cleanup(monkeypatch, tmp_path):
    import services.updater.launcher as launcher_module
    import updater_app
    from services.updater.launcher import launch_update

    staging, archive, manifest = _launcher_fixture(tmp_path)
    app = SimpleNamespace(data_path=str(tmp_path / "data"), log=MagicMock())
    captured = {}
    original = archive.read_bytes()

    def fake_popen(argv, **kwargs):
        job = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        captured["job"] = job
        Path(job["startup_ack_path"]).write_text("started", encoding="utf-8")

    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)

    assert launch_update(app, manifest, archive, relaunch_argv=["python", "flatcam.py"])

    job_archive = Path(captured["job"]["archive_path"])
    assert job_archive != archive
    assert job_archive.read_bytes() == original
    # the archive is moved into the job: cleaning the stage cannot remove the updater's copy
    assert not archive.exists()
    shutil.rmtree(staging)
    updater_app._validate_archive(job_archive, manifest["archive"])
