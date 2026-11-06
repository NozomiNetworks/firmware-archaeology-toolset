"""Offline checks for the recorded-fixture Wayback mirror used by integration tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wayback_mirror

from plugins.wayback.retrieve_archived_firmware import ArchiveClient


class WaybackMirrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mirror = wayback_mirror.start_mirror()
        self.addCleanup(self.mirror.stop)

    def get(self, path: str) -> bytes:
        url = f"{self.mirror.base_url}{path}"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read()

    def test_rate_limit_errors_are_detected(self) -> None:
        rate_limited = urllib.error.HTTPError(
            "https://web.archive.org/cdx/search/cdx",
            403,
            "rate limit exceeded",
            {},
            None,
        )
        too_many = urllib.error.HTTPError(
            "https://example.test",
            429,
            "too many requests",
            {},
            None,
        )
        forbidden = urllib.error.HTTPError("https://example.test", 403, "forbidden", {}, None)
        self.assertTrue(wayback_mirror.is_rate_limited(rate_limited))
        self.assertTrue(wayback_mirror.is_rate_limited(too_many))
        self.assertFalse(wayback_mirror.is_rate_limited(forbidden))

    def test_cdx_query_serves_recorded_rows(self) -> None:
        body = self.get("/cdx/search/cdx?url=https%3A%2F%2Fhttpbin.org%2Fanything&output=json")
        rows = json.loads(body)
        self.assertEqual(rows[0][0], "timestamp")
        self.assertTrue(rows[1:])
        self.assertTrue(all(row[1].endswith("/anything") for row in rows[1:]))

    def test_cdx_query_with_unknown_url_returns_empty_rows(self) -> None:
        body = self.get("/cdx/search/cdx?url=https%3A%2F%2Fexample.com%2Fnope&output=json")
        rows = json.loads(body)
        self.assertEqual(len(rows), 1)

    def test_replay_serves_recorded_body_regardless_of_timestamp(self) -> None:
        body = self.get("/web/20260818183333if_/https://httpbin.org/anything")
        payload = json.loads(body)
        self.assertIn("httpbin.org", payload["url"])

    def test_replay_matches_scheme_and_port_variants(self) -> None:
        body = self.get("/web/20190330100925if_/http://httpbin.org:80/bytes/32")
        self.assertEqual(len(body), 32)

    def test_replay_without_fixture_returns_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.get("/web/20260823152213/https://httpbin.org/")
        self.assertEqual(raised.exception.code, 404)

    def test_availability_returns_no_match_shape(self) -> None:
        payload = json.loads(self.get("/wayback/available?url=https%3A%2F%2Fhttpbin.org%2Fbytes%2F32"))
        self.assertEqual(payload, {"archived_snapshots": {}})

    def test_archive_client_end_to_end_against_mirror(self) -> None:
        archive = ArchiveClient(api_base=self.mirror.base_url, timeout=5, sleep=0)
        captures = archive.exact_file_captures("https://httpbin.org/bytes/32")
        self.assertTrue(captures)

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "payload.bin"
            archive.download_capture(captures[0], destination)
            self.assertEqual(destination.stat().st_size, 32)

        pages = archive.list_page_captures("https://httpbin.org/", max_snapshots=0)
        self.assertTrue(pages)


if __name__ == "__main__":
    unittest.main()
