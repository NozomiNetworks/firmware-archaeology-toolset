#!/usr/bin/env python3
"""List archived page versions, extract firmware links with parser plugins, and optionally download them."""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fatlib.browser import BrowserError, BrowserOptions, BrowserSession
from fatlib.http import USER_AGENT, build_request
from .registry import infer_parser_name as registry_infer_parser_name
from .registry import load_parser as registry_load_parser
CDX_API = "https://web.archive.org/cdx/search/cdx"
AVAILABILITY_API = "https://archive.org/wayback/available"
REPLAY_API_BASE = "https://web.archive.org"
DEFAULT_TIMEOUT = 90
DEFAULT_RETRIES = 4
PAGE_COLLAPSE = "timestamp:8"
ARCHIVE_URL_RE = re.compile(
    r"^https?://web\.archive\.org/web/(?P<timestamp>\d{14})(?P<modifier>[a-z_]{0,4})?/(?P<original>https?://.+)$",
    re.IGNORECASE,
)
CAPTURE_TIMESTAMP_RE = re.compile(r"\d{4,14}")


def validate_capture_timestamp(value: object) -> str:
    """Validate one capture timestamp received from an archive API.

    Archive timestamps are later used in local download paths, so anything
    other than a bounded plain digit string must be rejected.
    """

    text = str(value)
    if not CAPTURE_TIMESTAMP_RE.fullmatch(text):
        raise ValueError(f"invalid archive capture timestamp: {text!r}")
    return text

@dataclass(frozen=True)
class Capture:
    """Archived capture metadata returned by CDX and availability lookups."""

    timestamp: str
    original: str
    statuscode: str
    mimetype: str | None = None
    length: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", validate_capture_timestamp(self.timestamp))


class ArchiveClient:
    """Helper for querying and replaying Wayback or mirror captures.

    The Wayback plugin uses this client internally, and parser modules receive
    the active instance in `extract_links(...)`, `update_firmware_link(...)`,
    and `post_download(...)`.

    Parser authors usually care about:

    - `fetch_snapshot_html(...)`
    - `fetch_snapshot_html_for_url(...)`
    - `fetch_snapshot_html_near(...)`
    - `closest_page_capture(...)`
    - `http_get(...)`
    - `http_stream(...)`
    - `log(...)`
    """

    def __init__(
        self,
        *,
        sleep: float = 1.0,
        verbose: bool = False,
        verbose_http: bool = False,
        api_base: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.sleep = sleep
        self.verbose = verbose
        self.timeout = timeout
        self.retries = max(1, int(retries))
        base = str(api_base or "").rstrip("/")
        if not base:
            self.cdx_api = CDX_API
            self.availability_api = AVAILABILITY_API
            self.replay_api_base = REPLAY_API_BASE
        elif urllib.parse.urlsplit(base).netloc == "web.archive.org":
            # The webarchive service splits replay/CDX and availability across two hosts.
            self.cdx_api = f"{base}/cdx/search/cdx"
            self.availability_api = AVAILABILITY_API
            self.replay_api_base = base
        else:
            # Other mirrors can expose all endpoints below one base prefix.
            self.cdx_api = f"{base}/cdx/search/cdx"
            self.availability_api = f"{base}/wayback/available"
            self.replay_api_base = base
        if verbose_http:
            http.client.HTTPConnection.debuglevel = 1
            http.client.HTTPSConnection.debuglevel = 1
        self._snapshot_render_options = BrowserOptions(mode="static", timeout=self.timeout)

    def log(self, message: str) -> None:
        """Emit one verbose log line when the client is in verbose mode."""

        if self.verbose:
            print(message, flush=True)

    @staticmethod
    def unwrap_replay_url(url: str) -> str:
        """Return the original URL when given a Wayback replay URL.

        If the input is not a replay URL, it is returned unchanged.
        Parsers can use this to normalize candidate links before filtering.
        """

        match = ARCHIVE_URL_RE.match(url)
        if not match:
            return url
        return match.group("original")

    def http_get(
        self,
        url: str,
        *,
        timeout: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Fetch one URL with retries and return raw response bytes."""

        timeout = self.timeout if timeout is None else timeout
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            request = build_request(url, extra_headers=headers)
            try:
                self.log(f"[http] GET attempt {attempt}/{self.retries}: {url}")
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = response.read()
                    self.log(f"[http] GET ok ({len(payload)} bytes): {url}")
                    return payload
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                self.log(f"[http] GET failed: {url} -> {exc}")
                if attempt == self.retries:
                    break
                time.sleep(attempt)
        assert last_error is not None
        raise last_error

    def http_stream(self, url: str, destination: Path, *, timeout: int | None = None) -> None:
        """Stream one URL into a local file with retries."""

        timeout = self.timeout if timeout is None else timeout
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(destination.suffix + ".part")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            request = build_request(url)
            try:
                self.log(f"[http] STREAM attempt {attempt}/{self.retries}: {url} -> {destination}")
                bytes_written = 0
                next_progress = 25 * 1024 * 1024
                with urllib.request.urlopen(request, timeout=timeout) as response, temp_path.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        bytes_written += len(chunk)
                        if self.verbose and bytes_written >= next_progress:
                            print(f"[http] STREAM progress {bytes_written} bytes: {destination}", flush=True)
                            next_progress += 25 * 1024 * 1024
                temp_path.replace(destination)
                self.log(f"[http] STREAM ok ({bytes_written} bytes): {destination}")
                return
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                self.log(f"[http] STREAM failed: {url} -> {exc}")
                if temp_path.exists():
                    temp_path.unlink()
                if attempt == self.retries:
                    break
                time.sleep(attempt * 2)
        assert last_error is not None
        raise last_error

    def fetch_json(self, url: str) -> object:
        """Fetch and decode one JSON document."""

        return json.loads(self.http_get(url).decode("utf-8"))

    def cdx_query(
        self,
        *,
        url: str,
        fl: tuple[str, ...],
        match_type: str | None = None,
        filters: tuple[str, ...] = (),
        collapse: str | None = None,
        limit: int | None = None,
    ) -> list[list[str]]:
        """Execute one CDX query and return the raw JSON rows."""

        params: list[tuple[str, str]] = [("url", url), ("output", "json"), ("fl", ",".join(fl))]
        if match_type:
            params.append(("matchType", match_type))
        if collapse:
            params.append(("collapse", collapse))
        if limit:
            params.append(("limit", str(limit)))
        for item in filters:
            params.append(("filter", item))
        query = f"{self.cdx_api}?{urllib.parse.urlencode(params)}"
        return self.fetch_json(query)  # type: ignore[return-value]

    def domain_keyword_captures(self, domain: str, *, limit: int | None = None) -> list[Capture]:
        """Return distinct successful captures discovered across one domain."""

        rows = self.cdx_query(
            url=domain,
            match_type="domain",
            fl=("timestamp", "original", "statuscode", "mimetype"),
            filters=("statuscode:200",),
            collapse="urlkey",
            limit=limit,
        )
        return [Capture(timestamp=row[0], original=row[1], statuscode=row[2], mimetype=row[3]) for row in rows[1:]]

    def list_page_captures(self, url: str, *, max_snapshots: int) -> list[Capture]:
        """List archived page captures for one target page URL."""

        rows = self.cdx_query(
            url=url,
            fl=("timestamp", "original", "statuscode", "mimetype"),
            filters=("statuscode:200",),
            collapse=PAGE_COLLAPSE,
            limit=max_snapshots or None,
        )
        return [Capture(timestamp=row[0], original=row[1], statuscode=row[2], mimetype=row[3]) for row in rows[1:]]

    def exact_file_captures(self, url: str, *, collapse: str | None = PAGE_COLLAPSE) -> list[Capture]:
        """List archived captures for one exact file URL."""

        rows = self.cdx_query(
            url=url,
            fl=("timestamp", "original", "statuscode", "mimetype", "length"),
            filters=("statuscode:200",),
            collapse=collapse,
        )
        return [
            Capture(
                timestamp=row[0],
                original=row[1],
                statuscode=row[2],
                mimetype=row[3],
                length=row[4] if len(row) > 4 else None,
            )
            for row in rows[1:]
        ]

    @staticmethod
    def nearest_capture(captures: list[Capture], timestamp: str | None = None) -> Capture | None:
        """Choose the nearest capture in time from an existing capture list."""

        if not captures:
            return None
        if not timestamp:
            return captures[-1]
        return min(captures, key=lambda capture: (abs(int(capture.timestamp) - int(timestamp)), capture.timestamp))

    def strict_file_capture(self, url: str, timestamp: str | None) -> Capture | None:
        """Return the exact-timestamp capture for one file URL when present."""

        if not timestamp:
            return None
        captures = self.exact_file_captures(url, collapse=None)
        for capture in captures:
            if capture.timestamp == timestamp:
                return capture
        return None

    def closest_page_capture(self, url: str, timestamp: str | None = None, *, policy: str = "nearest") -> Capture | None:
        """Resolve the archived capture FAT should use for one binary or page URL."""

        if policy == "strict":
            return self.strict_file_capture(url, timestamp)
        params = {"url": url}
        if timestamp:
            params["timestamp"] = timestamp
        query = f"{self.availability_api}?{urllib.parse.urlencode(params)}"
        payload = self.fetch_json(query)
        archived = payload.get("archived_snapshots", {})  # type: ignore[assignment]
        closest = archived.get("closest") if isinstance(archived, dict) else None
        if not isinstance(closest, dict):
            return self.nearest_capture(self.exact_file_captures(url), timestamp)
        if str(closest.get("available")).lower() != "true":
            return self.nearest_capture(self.exact_file_captures(url), timestamp)
        return Capture(timestamp=str(closest["timestamp"]), original=url, statuscode=str(closest["status"]))

    def replay_page_url(self, capture: Capture) -> str:
        """Return the replay URL for viewing one archived page capture."""

        return f"{self.replay_api_base}/web/{capture.timestamp}/{capture.original}"

    def replay_file_url(self, capture: Capture) -> str:
        """Return the replay URL for downloading one archived file capture."""

        return f"{self.replay_api_base}/web/{capture.timestamp}if_/{capture.original}"

    def fetch_snapshot_html(self, capture: Capture) -> str:
        """Fetch the HTML or text body for one archived page capture."""

        replay_url = self.replay_page_url(capture)
        mimetype = str(capture.mimetype or "").lower()
        if "json" in mimetype:
            replay_url = self.replay_file_url(capture)
        return self.http_get(replay_url).decode("utf-8", "replace")

    def fetch_snapshot_html_rendered(
        self,
        capture: Capture,
        *,
        mode: str = "required",
        wait_until: str = "domcontentloaded",
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Fetch one archived HTML page through an isolated browser session.

        The browser always navigates to the archived replay URL. JSON and
        other non-page captures retain the direct HTTP behavior because they
        are data resources rather than browser documents.
        """

        mimetype = str(capture.mimetype or "").lower()
        if "json" in mimetype or mode == "static":
            return self.fetch_snapshot_html(capture)
        options = BrowserOptions(
            mode=mode,
            wait_until=wait_until,
            wait_for=wait_for,
            timeout=int(timeout or self.timeout),
        )
        replay_url = self.replay_page_url(capture)
        try:
            with BrowserSession(options) as browser:
                return browser.render(replay_url, timeout=options.timeout)
        except BrowserError:
            if mode != "preferred":
                raise
            self.log(f"[browser] rendering failed; falling back to static HTML: {replay_url}")
            return self.fetch_snapshot_html(capture)

    def set_snapshot_render_options(self, options: BrowserOptions) -> None:
        """Set the rendering policy used by parser follow-up page fetches."""

        self._snapshot_render_options = options

    def fetch_snapshot_html_for_url(self, url: str, timestamp: str) -> str:
        """Fetch archived page HTML for one explicit URL/timestamp pair."""

        capture = Capture(timestamp=timestamp, original=url, statuscode="200")
        return self.fetch_snapshot_html(capture)

    def fetch_snapshot_html_near(
        self,
        url: str,
        timestamp: str,
        *,
        render_mode: str | None = None,
        wait_until: str | None = None,
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, Capture | None]:
        """Fetch archived page HTML at or near one timestamp.

        This tries the exact timestamp first, then falls back to the nearest
        available capture. Parsers commonly use this when a page links to a
        second-level archived download page.
        """

        active = self._snapshot_render_options
        mode = active.mode if render_mode is None else render_mode
        wait_strategy = active.wait_until if wait_until is None else wait_until
        selector = active.wait_for if wait_for is None else wait_for
        render_timeout = active.timeout if timeout is None else timeout

        def fetch(capture: Capture) -> str:
            if mode == "static":
                return self.fetch_snapshot_html(capture)
            return self.fetch_snapshot_html_rendered(
                capture,
                mode=mode,
                wait_until=wait_strategy,
                wait_for=selector,
                timeout=render_timeout,
            )

        try:
            capture = Capture(timestamp=timestamp, original=url, statuscode="200")
            return fetch(capture), capture
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            pass

        capture = self.closest_page_capture(url, timestamp)
        if capture is None:
            return (None, None)
        try:
            return fetch(capture), capture
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            return (None, None)

    def download_capture(self, capture: Capture, destination: Path) -> Path:
        """Download one archived file capture into a local path."""

        self.http_stream(self.replay_file_url(capture), destination)
        return destination


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    if not parsed.scheme:
        raise SystemExit(f"URL must include a scheme: {url}")
    path = parsed.path or "/"
    if parsed.query:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
    if path != "/" and not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "artifact"


def contained_download_path(downloads_dir: Path, name: str) -> Path:
    """Return one path under downloads_dir, refusing names that escape it."""

    local_path = downloads_dir / name
    if not local_path.resolve().is_relative_to(downloads_dir.resolve()):
        raise ValueError(f"Download path escapes downloads directory: {name!r}")
    return local_path


def infer_parser_name(target_url: str, explicit_parser: str | None) -> str:
    return registry_infer_parser_name(target_url, explicit_parser)


def load_parser(parser_name: str) -> Any:
    return registry_load_parser(parser_name)
