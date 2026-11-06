"""Local Wayback Machine mirror backed by recorded fixtures.

The integration suite prefers the live archive but falls back to this mirror
when web.archive.org throttles or is unreachable. The fixture data was
recorded from the live CDX API and replay endpoints on 2026-10-06 using the
project's own ArchiveClient, so query and response shapes match exactly.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fatlib.http import build_request

# Exceptions that mean "the live archive is unreachable or throttling", not a
# code defect. HTTPError is a URLError subclass, so archived 429/5xx responses
# count as transport failures too.
TRANSPORT_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)

# Replay paths look like /web/<timestamp>[modifier]/<original-url>; the
# modifier (if_, id_, ...) selects replay rendering and is ignored here.
REPLAY_PATH_RE = r"^/web/\d{14}[a-z_]{0,4}/(?P<original>.+)$"


def is_rate_limited(error: BaseException) -> bool:
    """Return whether an upstream error clearly indicates rate limiting."""

    if isinstance(error, urllib.error.HTTPError):
        if error.code == 429:
            return True
        if error.code == 403:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            return bool(retry_after) or "rate" in str(error).casefold()
    message = str(error).casefold()
    return "rate limit" in message or "too many requests" in message

CDX_ROWS: dict[str, list[list[str]]] = {
    'https://httpbin.org/anything': [
        ['timestamp', 'original', 'statuscode', 'mimetype', 'length'],
        ['20260818183333', 'https://httpbin.org/anything', '200', 'application/json', '859'],
        ['20260821172123', 'https://httpbin.org/anything', '200', 'application/json', '773'],
        ['20260828065718', 'https://httpbin.org/anything', '200', 'application/json', '983'],
        ['20260904172552', 'http://httpbin.org/anything', '200', 'application/json', '769'],
        ['20260907164136', 'https://httpbin.org/anything', '200', 'application/json', '980'],
        ['20260915113347', 'http://httpbin.org/anything', '200', 'application/json', '768'],
    ],
    'https://httpbin.org/bytes/32': [
        ['timestamp', 'original', 'statuscode', 'mimetype', 'length'],
        ['20190330100925', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '306'],
        ['20190331215909', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '302'],
        ['20190508205720', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '381'],
        ['20190615050504', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '380'],
        ['20190812062122', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '380'],
        ['20190914231046', 'http://httpbin.org:80/bytes/32', '200', 'application/octet-stream', '378'],
    ],
    'https://httpbin.org/': [
        ['timestamp', 'original', 'statuscode', 'mimetype', 'length'],
        ['20260823152213', 'https://httpbin.org/', '200', 'text/html', '3621'],
        ['20260825234115', 'https://httpbin.org/', '200', 'text/html', '3604'],
        ['20260907052455', 'https://httpbin.org/', '200', 'text/html', '3646'],
        ['20260908221855', 'https://httpbin.org/', '200', 'text/html', '3641'],
        ['20260927051035', 'https://httpbin.org/', '200', 'text/html', '3538'],
        ['20260928072636', 'http://httpbin.org/', '200', 'text/html', '3644'],
    ],
}

# One recorded body per original URL, keyed by the canonical form produced by
# canonical_url(). CDX rows may spell the same URL with http/https or explicit
# default ports, so replay lookups normalize before matching.
REPLAY_BODIES: dict[str, tuple[bytes, str]] = {
    # https://httpbin.org/anything (510 bytes, application/json)
    '//httpbin.org/anything': (b'{\n  "args": {}, \n  "data": "", \n  "files": {}, \n  "form": {}, \n  "headers": {\n    "Accept-Encoding": "gzip, deflate, zstd", \n    "Host": "httpbin.org", \n    "User-Agent": "Mozilla/5.0 (compatible; archive.org_bot +http://archive.org/details/archive.org_bot) Zeno/7d76126 warc/v0.8.102", \n    "X-Amzn-Trace-Id": "Root=1-6aa92d1b-771620ec29ae03f87828c1a9"\n  }, \n  "json": null, \n  "method": "GET", \n  "origin": "204.62.249.7", \n  "url": "https://web.archive.org/web/20260915113347/http://httpbin.org/anything"\n}\n', 'application/json'),
    # https://httpbin.org/bytes/32 (32 bytes, application/octet-stream)
    '//httpbin.org/bytes/32': (b'\xce\xf3Zg\xf4\x95wb\x00j\xef\x84\x8d:\xe1\xbc\xb3:\x13\xa7\xdf\xeb!\xc40d\x1dZ9\x90\xceR', 'application/octet-stream'),
}


def canonical_url(url: str) -> str:
    """Normalize scheme and default-port spelling so recorded URLs match."""

    parsed = urllib.parse.urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None or (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        netloc = host
    else:
        netloc = f"{host}:{port}"
    return urllib.parse.urlunsplit(("", netloc, parsed.path or "/", parsed.query, ""))


class _WaybackMirrorHandler(BaseHTTPRequestHandler):
    server_version = "FATWaybackMirror/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep test output quiet

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/cdx/search/cdx":
            params = urllib.parse.parse_qs(parsed.query)
            url = (params.get("url") or [""])[0]
            rows = CDX_ROWS.get(url, [["timestamp", "original", "statuscode", "mimetype", "length"]])
            self._send(200, json.dumps(rows).encode("utf-8"), "application/json")
            return
        match = re.match(REPLAY_PATH_RE, parsed.path)
        if match:
            original = match.group("original")
            entry = REPLAY_BODIES.get(canonical_url(original))
            if entry is None:
                self._send(404, f"no replay fixture for {original}".encode("utf-8"), "text/plain")
                return
            self._send(200, entry[0], entry[1])
            return
        if parsed.path == "/wayback/available":
            # No recorded availability payload; the plugin's closest_page_capture
            # falls back to CDX rows, which this mirror serves.
            self._send(200, json.dumps({"archived_snapshots": {}}).encode("utf-8"), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WaybackMirror:
    """A thread-local HTTP server that mirrors the recorded Wayback endpoints."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _WaybackMirrorHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> WaybackMirror:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def start_mirror() -> WaybackMirror:
    """Start the recorded-fixture mirror and return the handle."""

    return WaybackMirror().start()


def probe_live_archive(timeout: float = 5.0) -> Exception | None:
    """Send one cheap request to the live CDX API.

    Returns None when the live archive is reachable and polite; returns the
    failure exception otherwise (timeouts and 429s both count as unreachable).
    """

    query = urllib.parse.urlencode({"url": "https://httpbin.org/bytes/32", "output": "json", "limit": "1"})
    try:
        with urllib.request.urlopen(build_request(f"https://web.archive.org/cdx/search/cdx?{query}"), timeout=timeout) as response:
            response.read()
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
