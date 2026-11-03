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

if __name__ == "__main__":
    unittest.main()
