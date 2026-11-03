"""The release that the user skipped: the startup check does not show it again; a manual check still does."""

from __future__ import annotations

import json
import logging
from pathlib import Path

SKIP_FILE_NAME = "update_skipped_release.json"

_log = logging.getLogger(__name__)


def _identity(payload: dict) -> dict:
    """The version and build of the offer. The GitHub payload has them at the top; the other provider has them in
    its manifest, as a dict or as an object."""
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        version, build = manifest.get("version"), manifest.get("build") or manifest.get("build_string")
    else:
        version, build = getattr(manifest, "version", None), getattr(manifest, "build_string", None)
    return {"version": str(payload.get("version") or version or ""),
            "build_string": str(payload.get("build_string") or build or "")}


def skip_release(data_path, payload: dict) -> None:
    """Keep the version and build of the offered release as the skipped release."""
    try:
        path = Path(data_path) / SKIP_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_identity(payload)), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        _log.warning("The skipped release could not be saved: %s", exc)


def is_release_skipped(data_path, payload: dict) -> bool:
    """True if the offered release is the release that the user skipped. A new version or build is not skipped."""
    try:
        saved = json.loads((Path(data_path) / SKIP_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    identity = _identity(payload)
    return isinstance(saved, dict) and bool(identity["version"]) and saved == identity
