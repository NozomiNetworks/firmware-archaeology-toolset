from __future__ import annotations

import json
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from plugins.wayback.retrieve_archived_firmware import (
    ArchiveClient,
    Capture,
    contained_download_path,
    infer_parser_name,
    normalize_url,
    safe_name,
    validate_capture_timestamp,
)
from fatlib.browser import BrowserOptions


class ArchiveClientTests(unittest.TestCase):
    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                payload, self.payload = self.payload, b""
                return payload
            payload, self.payload = self.payload[:size], self.payload[size:]
            return payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def test_http_get_and_stream_retry_transport(self) -> None:
        client = ArchiveClient(sleep=0, timeout=3)
        attempts = {"get": 0, "stream": 0}

        def fake_get(request, timeout):
            attempts["get"] += 1
            if attempts["get"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "0"}, io.BytesIO())
            return self.Response(b"payload")

        def fake_stream(request, timeout):
            attempts["stream"] += 1
            if attempts["stream"] == 1:
                raise urllib.error.URLError("temporary")
            return self.Response(b"streamed")

        with tempfile.TemporaryDirectory() as temp_dir, patch("plugins.wayback.retrieve_archived_firmware.urllib.request.urlopen", side_effect=fake_get), patch(
            "plugins.wayback.retrieve_archived_firmware.time.sleep"
        ):
            self.assertEqual(client.http_get("https://example.test"), b"payload")
            destination = Path(temp_dir) / "nested" / "file.bin"
            with patch("plugins.wayback.retrieve_archived_firmware.urllib.request.urlopen", side_effect=fake_stream):
                client.http_stream("https://example.test/file", destination)
            self.assertEqual(destination.read_bytes(), b"streamed")
        self.assertEqual(attempts, {"get": 2, "stream": 2})

    def test_http_get_raises_after_non_retryable_error(self) -> None:
        client = ArchiveClient(sleep=0)
        with patch(
            "plugins.wayback.retrieve_archived_firmware.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("https://example.test", 404, "missing", {}, io.BytesIO()),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                client.http_get("https://example.test")

    def test_http_get_can_disable_retries_for_integration_checks(self) -> None:
        client = ArchiveClient(sleep=0, retries=1)
        with patch(
            "plugins.wayback.retrieve_archived_firmware.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("https://example.test", 429, "rate limited", {}, io.BytesIO()),
        ) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                client.http_get("https://example.test")
        urlopen.assert_called_once()

    def test_url_helpers_and_capture_selection(self) -> None:
        self.assertEqual(normalize_url("https://example.test/path"), "https://example.test/path/")
        self.assertEqual(safe_name("a/b c"), "a_b_c")
        self.assertEqual(infer_parser_name("https://httpbin.org/", None), "httpbin")
        captures = [Capture("20240101000000", "https://x/a", "200"), Capture("20240201000000", "https://x/a", "200")]
        self.assertEqual(ArchiveClient.nearest_capture(captures, "20240115000000"), captures[0])
        self.assertIsNone(ArchiveClient.nearest_capture([], None))

    def test_capture_timestamps_and_download_paths_are_validated(self) -> None:
        self.assertEqual(validate_capture_timestamp("2024"), "2024")
        self.assertEqual(validate_capture_timestamp("20240101000000"), "20240101000000")
        for value in ("../outside", "202401010000000", "", None):
            with self.assertRaises(ValueError):
                validate_capture_timestamp(value)
        with self.assertRaises(ValueError):
            Capture("../outside", "https://x/file.zip", "200")

        client = ArchiveClient(sleep=0)
        client.fetch_json = lambda url: [
            ["timestamp", "original", "statuscode", "mimetype"],
            ["../outside", "https://x/page", "200", "text/html"],
        ]
        with self.assertRaises(ValueError):
            client.list_page_captures("https://x/page", max_snapshots=1)

        client.fetch_json = lambda url: {
            "archived_snapshots": {
                "closest": {"available": "true", "timestamp": "../outside", "status": "200"}
            }
        }
        with self.assertRaises(ValueError):
            client.closest_page_capture("https://x/file.zip")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloads_dir = Path(temp_dir) / "downloads"
            downloads_dir.mkdir()
            self.assertEqual(
                contained_download_path(downloads_dir, "20240101000000_file.zip"),
                downloads_dir / "20240101000000_file.zip",
            )
            for name in ("../outside.bin", "/tmp/outside.bin"):
                with self.assertRaises(ValueError):
                    contained_download_path(downloads_dir, name)

    def test_cdx_lists_and_replay_helpers_parse_rows(self) -> None:
        client = ArchiveClient(sleep=0)
        client.fetch_json = lambda url: [
            ["timestamp", "original", "statuscode", "mimetype", "length"],
            ["20240101000000", "https://x/page", "200", "text/html", "10"],
        ]
        self.assertEqual(client.list_page_captures("https://x/page", max_snapshots=1)[0].mimetype, "text/html")
        self.assertEqual(client.exact_file_captures("https://x/file.zip")[0].length, "10")
        capture = Capture("20240101000000", "https://x/file.zip", "200")
        self.assertIn("if_", client.replay_file_url(capture))
        self.assertIn("/web/20240101000000/", client.replay_page_url(capture))

    def test_fetch_snapshot_html_uses_replay_modifier_for_json(self) -> None:
        client = ArchiveClient(sleep=0)
        seen: list[str] = []
        client.http_get = lambda url: seen.append(url) or b'{"ok": true}'
        body = client.fetch_snapshot_html(Capture("20240101000000", "https://x/api", "200", "application/json"))
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertIn("if_", seen[0])

    def test_fetch_snapshot_html_rendered_uses_archived_replay_url(self) -> None:
        client = ArchiveClient(sleep=0, timeout=4, api_base="https://mirror.example")
        capture = Capture("20240101000000", "https://vendor.example/products", "200", "text/html")

        class FakeBrowserSession:
            instances = []

            def __init__(self, options):
                self.options = options
                self.closed = False
                self.__class__.instances.append(self)

            def __enter__(self):
                return self

            def render(self, url, *, timeout):
                self.url = url
                self.render_timeout = timeout
                return "<html>rendered</html>"

            def __exit__(self, exc_type, exc, tb):
                self.closed = True

        with patch("plugins.wayback.retrieve_archived_firmware.BrowserSession", FakeBrowserSession):
            self.assertEqual(
                client.fetch_snapshot_html_rendered(
                    capture,
                    mode="required",
                    wait_until="load",
                    wait_for=".download",
                    timeout=3,
                ),
                "<html>rendered</html>",
            )

        session = FakeBrowserSession.instances[0]
        self.assertEqual(session.url, "https://mirror.example/web/20240101000000/https://vendor.example/products")
        self.assertEqual(session.options.wait_until, "load")
        self.assertEqual(session.options.wait_for, ".download")
        self.assertEqual(session.render_timeout, 3)
        self.assertTrue(session.closed)

    def test_near_page_fetch_inherits_parser_render_settings(self) -> None:
        client = ArchiveClient(sleep=0, timeout=4)
        client.set_snapshot_render_options(
            BrowserOptions(mode="required", wait_until="load", wait_for=".download", timeout=6)
        )
        capture = Capture("20240101000000", "https://vendor.example/next", "200", "text/html")
        with patch.object(client, "fetch_snapshot_html_rendered", return_value="<html>rendered</html>") as rendered:
            html, selected = client.fetch_snapshot_html_near(capture.original, capture.timestamp)
        self.assertEqual(html, "<html>rendered</html>")
        self.assertEqual((selected.timestamp, selected.original, selected.statuscode), (capture.timestamp, capture.original, capture.statuscode))
        call = rendered.call_args
        self.assertEqual((call.args[0].timestamp, call.args[0].original), (capture.timestamp, capture.original))
        self.assertEqual(call.kwargs, {
            "mode": "required", "wait_until": "load", "wait_for": ".download", "timeout": 6,
        })

    def test_closest_capture_falls_back_to_exact_captures(self) -> None:
        client = ArchiveClient(sleep=0)
        capture = Capture("20240101000000", "https://x/file.zip", "200")
        client.fetch_json = lambda url: {"archived_snapshots": {"closest": {"available": "false"}}}
        client.exact_file_captures = lambda url, collapse="timestamp:8": [capture]
        self.assertEqual(client.closest_page_capture(capture.original, capture.timestamp), capture)
        self.assertIsNone(client.strict_file_capture(capture.original, "20240201000000"))

    def test_download_capture_streams_to_destination(self) -> None:
        client = ArchiveClient(sleep=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "file.bin"
            with patch.object(client, "http_stream") as stream:
                client.download_capture(Capture("20240101000000", "https://x/file.bin", "200"), destination)
                stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
