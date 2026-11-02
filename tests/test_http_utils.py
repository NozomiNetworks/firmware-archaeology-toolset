from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error
import socket
from datetime import datetime, timezone, timedelta

from fatlib import http


class FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def read(self, _size: int = -1) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class HttpUtilsTests(unittest.TestCase):
    def test_build_request_rejects_non_http_schemes(self) -> None:
        for url in (
            "file:///tmp/secret",
            "ftp://example.test/file",
            "data:text/plain,secret",
            "//example.test/file",
        ):
            with self.assertRaisesRegex(ValueError, "unsupported URL scheme"):
                http.build_request(url)

        request = http.build_request("HTTPS://example.test/file")
        self.assertEqual(request.full_url, "HTTPS://example.test/file")

    def test_request_headers_and_retry_parsing(self) -> None:
        request = http.build_request("https://example.test", accept="text/html", extra_headers={"X-Test": "yes"})
        self.assertEqual(request.headers["Accept"], "text/html")
        self.assertEqual(request.headers["X-test"], "yes")
        self.assertEqual(http._parse_retry_after_seconds("3"), 3.0)
        self.assertIsNone(http._parse_retry_after_seconds("invalid"))
        future = (datetime.now(timezone.utc) + timedelta(seconds=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertGreaterEqual(http._parse_retry_after_seconds(future), 0.0)
        error = urllib.error.HTTPError("url", 500, "error", {}, io.BytesIO())
        self.assertIsNone(http._retry_delay_seconds(error, 1))

    def test_rate_limit_error_format_includes_status_and_retry_after(self) -> None:
        error = urllib.error.HTTPError(
            "url",
            429,
            "Too Many Requests",
            {"Retry-After": "17"},
            io.BytesIO(),
        )
        self.assertEqual(
            http.format_rate_limit_error("github", error),
            "github: rate limited (HTTP 429); retry after 17",
        )
        self.assertIn(
            "retry after unknown",
            http.format_rate_limit_error("gitlab", RuntimeError("rate limit exceeded")),
        )

    def test_403_with_zero_remaining_quota_is_rate_limited(self) -> None:
        error = urllib.error.HTTPError(
            "url", 403, "forbidden", {"X-RateLimit-Remaining": "0"}, io.BytesIO()
        )
        self.assertTrue(http.is_rate_limited_error(error))

    def test_workshop_dns_errors_are_rewritten(self) -> None:
        error = urllib.error.URLError(socket.gaierror(-2, "name or service not known"))
        rewritten = http._rewrite_resolution_error("https://mirror.nnlabs/file", error)
        self.assertIsInstance(rewritten, RuntimeError)
        self.assertIn("mirror.nnlabs", str(rewritten))
        self.assertIs(http._rewrite_resolution_error("https://example.test", error), error)
    def test_fetch_text_uses_shared_headers_and_timeout(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            seen["timeout"] = timeout
            seen["user_agent"] = request.headers.get("User-agent")
            seen["accept"] = request.headers.get("Accept")
            return FakeResponse(b"hello")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            body = http.fetch_text("https://example.test", timeout=17, accept="text/html")

        self.assertEqual(body, "hello")
        self.assertEqual(seen["timeout"], 17)
        self.assertEqual(seen["user_agent"], http.USER_AGENT)
        self.assertEqual(seen["accept"], "text/html")

    def test_fetch_json_with_headers_retains_provider_pagination_metadata(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=FakeResponse(b'{"items": []}', {"X-Next-Page": "2"}),
        ):
            payload, headers = http.fetch_json_with_headers("https://example.test/api", timeout=7)

        self.assertEqual(payload, {"items": []})
        self.assertEqual(headers, {"X-Next-Page": "2"})

    def test_fetch_json_no_redirect_rejects_redirect_responses(self) -> None:
        error = urllib.error.HTTPError(
            "https://hub.example.test/search", 302, "Found", {"Location": "https://outside.test/"}, io.BytesIO()
        )
        with patch("urllib.request.OpenerDirector.open", side_effect=error) as open_request:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                http.fetch_json_no_redirect("https://hub.example.test/search", timeout=5)
        self.assertEqual(raised.exception.code, 302)
        open_request.assert_called_once()

    def test_download_file_uses_shared_headers_and_writes_destination(self) -> None:
        seen: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            seen["timeout"] = timeout
            seen["user_agent"] = request.headers.get("User-agent")
            seen["accept"] = request.headers.get("Accept")
            return FakeResponse(b"payload")

        with tempfile.TemporaryDirectory() as temp_dir, patch("urllib.request.urlopen", side_effect=fake_urlopen):
            destination = Path(temp_dir) / "downloads" / "artifact.bin"
            http.download_file("https://example.test/file.bin", destination, timeout=23)

            self.assertTrue(destination.exists())
            self.assertEqual(destination.read_bytes(), b"payload")

        self.assertEqual(seen["timeout"], 23)
        self.assertEqual(seen["user_agent"], http.USER_AGENT)
        self.assertEqual(seen["accept"], "*/*")

    def test_fetch_text_raises_http_429_without_retry(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(request, timeout):
            attempts["count"] += 1
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "0"},
                io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep") as sleep_mock:
            with self.assertRaises(urllib.error.HTTPError):
                http.fetch_text("https://example.test", timeout=17, accept="text/html")

        self.assertEqual(attempts["count"], 1)
        sleep_mock.assert_not_called()

    def test_download_file_raises_http_429_without_retry(self) -> None:
        attempts = {"count": 0}

        def fake_urlopen(request, timeout):
            attempts["count"] += 1
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "0"},
                io.BytesIO(b""),
            )

        with tempfile.TemporaryDirectory() as temp_dir, patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep") as sleep_mock:
            destination = Path(temp_dir) / "downloads" / "artifact.bin"
            with self.assertRaises(urllib.error.HTTPError):
                http.download_file("https://example.test/file.bin", destination, timeout=23)

        self.assertEqual(attempts["count"], 1)
        sleep_mock.assert_not_called()

    def test_fetch_text_raises_http_429_immediately(self) -> None:
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {"Retry-After": "0"},
                io.BytesIO(b""),
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep") as sleep_mock:
            with self.assertRaises(urllib.error.HTTPError) as error:
                http.fetch_text("https://example.test", timeout=17, accept="text/html")

        self.assertEqual(error.exception.code, 429)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
