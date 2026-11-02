"""Small demonstration parser for the httpbin API documentation page.

The httpbin landing page documents several endpoints.  For the Wayback
workflow, ``/bytes/32`` is treated as a firmware-like binary resource so the
page can exercise normal parser discovery and archived download handling.
"""

from __future__ import annotations

HOSTS = ("httpbin.org",)

import html
import urllib.parse
from html.parser import HTMLParser

from ..retrieve_archived_firmware import ArchiveClient


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.links.append(value)


def extract_links(snapshot: dict[str, str], page_html: str, archive) -> list[str]:
    """Return the archived httpbin byte endpoint documented by the page."""

    collector = _LinkCollector()
    collector.feed(page_html)
    page_url = snapshot["original"]
    links: set[str] = set()
    for raw_link in collector.links:
        candidate = urllib.parse.urljoin(page_url, html.unescape(raw_link.strip()))
        candidate = ArchiveClient.unwrap_replay_url(candidate)
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.netloc.casefold() == "httpbin.org" and parsed.path == "/bytes/32":
            links.add(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")))
    return sorted(links)
