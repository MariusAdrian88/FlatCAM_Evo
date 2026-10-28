"""Update provider selection: GitHub by default, Digi as an explicit legacy source."""

from __future__ import annotations

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
    checker_for,
    create_update_checker,
    provider_url,
    run_provider_check,
    update_provider,
)
from tests.updater.test_updater_integration import FakeSignal, _fake_app, _manifest


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


def test_run_check_uses_the_matching_keyword():
    app = _provider_app(global_update_provider="digi")
    checker = MagicMock()
    checker.provider = "digi"
    run_provider_check(app, checker, forced=True)
    checker.run_check.assert_called_once_with(forced=True, share_url=DIGI_URL)

    app = _provider_app()
    checker = MagicMock()
    checker.provider = "github"
    run_provider_check(app, checker)
    checker.run_check.assert_called_once_with(forced=False, repository_url=DEFAULT_REPOSITORY)


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


def test_digi_staged_update_launches_without_a_confirmation_box(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    manifest = _manifest()

    App.on_update_staged(
        app,
        {
            "archive_path": str(archive),
            "staging_dir": str(tmp_path),
            "manifest": manifest,
            "channel": "windows",
            "payload": {},
        },
    )

    launch.assert_called_once_with(app, manifest, str(archive))
    app.quit_application.assert_called_once_with(mode="updater")


def test_no_releases_manual_check_shows_a_warning(tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    app._check_in_progress = True
    app._manual_update_requested = True

    App.on_update_check_result(app, {"status": "up_to_date", "no_releases": True})

    message = app.inform.emissions[0][0]
    assert message.startswith("[WARNING_NOTCL]")
    assert "No releases have been published yet." in message

    app._manual_update_requested = True
    App.on_update_check_result(app, {"status": "up_to_date"})
    assert "up to date" in app.inform.emissions[1][0]


def test_old_digi_url_migrates_but_stays_available_as_legacy_url(tmp_path):
    filename = tmp_path / "current_defaults.FlatConfig"
    filename.write_text(json.dumps({"global_update_url": DIGI_URL}))

    defaults = AppDefaults(beta=False, version="1.0")
    defaults.load(str(filename), MagicMock())

    assert defaults["global_update_url"] == DEFAULT_REPOSITORY
    assert defaults["global_legacy_update_url"] == DIGI_URL
    assert defaults["global_update_provider"] == "github"


def test_settings_with_a_provider_keep_their_urls(tmp_path):
    filename = tmp_path / "current_defaults.FlatConfig"
    saved = {
        "global_update_provider": "github",
        "global_update_url": "https://github.com/example/fork",
        "global_legacy_update_url": "https://s.example.invalid/custom",
    }
    filename.write_text(json.dumps(saved))

    defaults = AppDefaults(beta=False, version="1.0")
    defaults.load(str(filename), MagicMock())

    for key, value in saved.items():
        assert defaults[key] == value


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

