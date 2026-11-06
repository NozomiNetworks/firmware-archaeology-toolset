"""Website-scraper extension surface used by the FAT `scraper` plugin.

The top-level FAT plugin remains `scraper`, but site-specific implementations
live under `plugins/scraper/websites/`. Contributors extending marketplace
support should normally subclass `WebsiteScraperBase`.
"""

from __future__ import annotations

import urllib.parse
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fatlib.browser import BrowserError, BrowserOptions, BrowserSession
from fatlib.http import ALLOWED_URL_SCHEMES, download_file as shared_download_file, fetch_text as shared_fetch_text
from ..base import safe_name


__all__ = [
    "WebsiteScraperBase",
]

__pdoc__ = {
    "HrefCollector": False,
}


class HrefCollector(HTMLParser):
    """@private

    Minimal HTML parser that extracts `href` attribute values.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value)


class WebsiteScraperBase(ABC):
    """Base class for one website-specific scraper implementation.

    A website scraper is not a top-level FAT plugin. Instead, it is selected by
    the `scraper` plugin to handle one marketplace or download surface.

    Subclasses should provide:

    - `name`
    - `help_summary`
    - `default_website` when there is a sensible default entrypoint
    - `can_handle()` for parser auto-detection
    - `extract_product_links()`
    - `parse_product()`
    """

    name = ""
    help_summary = ""
    default_website = ""
    # Rendering is deliberately opt-in.  Existing scrapers continue to use
    # the inexpensive static HTTP path unless they declare otherwise.
    RENDER_MODE = "static"
    RENDER_WAIT_UNTIL = "domcontentloaded"
    RENDER_WAIT_FOR: str | None = None
    RENDER_TIMEOUT: int | None = None

    def can_handle(self, website_url: str, entry_html: str = "") -> bool:
        """Return `True` when this scraper can parse the target website."""

        return False

    def fetch_text(self, url: str, timeout: int) -> str:
        """Fetch one HTML page for scraper-side parsing."""

        return shared_fetch_text(url, timeout=timeout, accept="text/html,application/xhtml+xml")

    def browser_options(self, timeout: int) -> BrowserOptions:
        """Return the validated browser settings declared by this scraper."""

        return BrowserOptions(
            mode=str(self.RENDER_MODE),
            wait_until=str(self.RENDER_WAIT_UNTIL),
            wait_for=self.RENDER_WAIT_FOR,
            timeout=int(self.RENDER_TIMEOUT or timeout),
        )

    def fetch_rendered_text(
        self,
        url: str,
        timeout: int,
        *,
        session: BrowserSession | None = None,
    ) -> str:
        """Fetch browser-rendered HTML, optionally reusing a crawl session."""

        options = self.browser_options(timeout)
        if session is not None:
            return session.render(url, timeout=options.timeout)
        with BrowserSession(options) as browser:
            return browser.render(url, timeout=options.timeout)

    def _fetch_page_text(
        self,
        url: str,
        timeout: int,
        *,
        session: BrowserSession | None = None,
        verbose: bool = False,
        browser_disabled: bool = False,
    ) -> str:
        """Fetch one parser input according to the scraper's render mode."""

        mode = str(self.RENDER_MODE)
        if mode == "static" or browser_disabled:
            return self.fetch_text(url, timeout)
        try:
            return self.fetch_rendered_text(url, timeout, session=session)
        except BrowserError:
            if mode != "preferred":
                raise
            if verbose:
                print(f"[!] [scraper:{self.name}] browser rendering failed; falling back to static HTML")
            return self.fetch_text(url, timeout)

    def download_file(self, url: str, destination: Path, timeout: int) -> None:
        """Download one remote file into a local destination path."""

        shared_download_file(url, destination, timeout=timeout)

    def extract_hrefs(self, html: str) -> list[str]:
        """Extract raw `href` values from an HTML document."""

        collector = HrefCollector()
        collector.feed(html)
        return collector.hrefs

    def same_origin(self, url: str, base_url: str) -> bool:
        """Return whether two URLs have the same HTTP(S) scheme and network location."""

        try:
            parsed = urllib.parse.urlsplit(url)
            base = urllib.parse.urlsplit(base_url)
            parsed_scheme = parsed.scheme.lower()
            base_scheme = base.scheme.lower()
            return (
                parsed_scheme in ALLOWED_URL_SCHEMES
                and base_scheme in ALLOWED_URL_SCHEMES
                and parsed_scheme == base_scheme
                and parsed.netloc.lower() == base.netloc.lower()
            )
        except ValueError:
            return False

    def crawl(self, website_url: str, timeout: int, *, verbose: bool = False) -> dict[str, Any]:
        """Crawl the configured category page and return a normalized catalog."""

        session: BrowserSession | None = None
        mode = str(self.RENDER_MODE)
        browser_disabled = False
        if mode != "static":
            try:
                session = BrowserSession(self.browser_options(timeout))
                session.__enter__()
            except BrowserError:
                if mode != "preferred":
                    raise
                if verbose:
                    print(f"[!] [scraper:{self.name}] browser unavailable; falling back to static HTML")
                session = None
                browser_disabled = True
        try:
            entry_html = self._fetch_page_text(
                website_url,
                timeout,
                session=session,
                verbose=verbose,
                browser_disabled=browser_disabled,
            )
            product_urls = self.extract_product_links(entry_html, website_url)
            applications: list[dict[str, Any]] = []
            for product_url in product_urls:
                if verbose:
                    print(f"[*] [scraper:{self.name}] fetching {product_url}")
                product_html = self._fetch_page_text(
                    product_url,
                    timeout,
                    session=session,
                    verbose=verbose,
                    browser_disabled=browser_disabled,
                )
                product = self.parse_product(product_html, product_url)
                applications.append(product)
            return self.build_catalog(website_url, applications)
        finally:
            if session is not None:
                session.close()

    def build_catalog(self, website_url: str, applications: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the persisted catalog document for one scraper run."""

        total_download_links = sum(len(app.get("download_urls", [])) for app in applications)
        unique_download_ids = len(
            {
                download_id
                for app in applications
                for download_id in app.get("download_ids", [])
            }
        )
        return {
            "source": website_url,
            "scraper": self.name,
            "applications": applications,
            "summary": {
                "application_count": len(applications),
                "applications_with_downloads": sum(1 for app in applications if app.get("download_urls")),
                "total_download_links": total_download_links,
                "unique_download_ids": unique_download_ids,
            },
        }

    def product_artifact(self, product: dict[str, Any], website_url: str) -> dict[str, Any]:
        """Convert one parsed product record into a FAT artifact entry."""

        slug = str(product["slug"])
        title = str(product["title"])
        download_urls = list(product.get("download_urls", []))
        return {
            "plugin": "scraper",
            "kind": "website_artifact_bundle",
            "query": website_url,
            "name": slug,
            "url": str(product.get("product_url", website_url)),
            "description": f"{title} ({len(download_urls)} download(s))",
            "matched_keywords": [],
            "signals": ["scraped", f"scraper:{self.name}"],
            "confidence": "catalog",
            "title": title,
            "slug": slug,
            "download_urls": download_urls,
            "download_ids": list(product.get("download_ids", [])),
            "saved_files": list(product.get("saved_files", [])),
            "website": website_url,
            "scraper_name": self.name,
        }

    def filename_from_download_url(self, download_url: str) -> str:
        """Derive a local filename from one download URL."""

        parsed = urllib.parse.urlsplit(download_url)
        name = Path(parsed.path).name
        if not name.strip("."):
            download_id_from_url = getattr(self, "download_id_from_url", None)
            if callable(download_id_from_url):
                try:
                    return f"download-{download_id_from_url(download_url)}.zip"
                except (TypeError, ValueError):
                    pass
            return "download.bin"
        return name

    def relative_download_path(self, product_slug: str, download_url: str) -> Path:
        """Return the relative on-disk path used for one downloaded file."""

        return Path(safe_name(product_slug)) / self.filename_from_download_url(download_url)

    @abstractmethod
    def extract_product_links(self, category_html: str, website_url: str) -> list[str]:
        """Return product detail URLs discovered on the category page."""

        raise NotImplementedError

    @abstractmethod
    def parse_product(self, product_html: str, product_url: str) -> dict[str, Any]:
        """Parse one product page into the normalized product record schema."""

        raise NotImplementedError
