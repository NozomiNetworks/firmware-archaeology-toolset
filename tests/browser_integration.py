"""Deterministic browser-rendering integration checks.

This module is intentionally separate from the ordinary integration suite:
the job that runs it installs Playwright and Chromium.
"""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

from plugins.scraper.base import WebsiteScraperBase


class _DynamicPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            body = """
            <html><body><main id="results"></main>
            <script>
            setTimeout(() => {
              document.body.dataset.rendered = 'true';
              document.querySelector('#results').innerHTML =
                '<a class="product" href="/product">Product</a>';
            }, 10);
            </script></body></html>
            """
        elif self.path == "/product":
            body = """
            <html><body><main id="product"></main>
            <script>
            setTimeout(() => {
              document.body.dataset.rendered = 'true';
              document.querySelector('#product').innerHTML =
                '<a class="download" href="/artifact.bin">Download</a>';
            }, 10);
            </script></body></html>
            """
        elif self.path == "/artifact.bin":
            body = "artifact"
        else:
            self.send_error(404)
            return
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return None


class _RenderedFixtureScraper(WebsiteScraperBase):
    name = "browser-fixture"
    RENDER_MODE = "required"
    RENDER_WAIT_FOR = 'body[data-rendered="true"]'

    def extract_product_links(self, category_html: str, website_url: str) -> list[str]:
        return [urljoin(website_url, href) for href in self.extract_hrefs(category_html)]

    def parse_product(self, product_html: str, product_url: str) -> dict:
        links = [urljoin(product_url, href) for href in self.extract_hrefs(product_html)]
        return {
            "slug": "browser-fixture",
            "title": "Browser fixture product",
            "product_url": product_url,
            "download_urls": links,
            "download_ids": [],
        }


class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _DynamicPageHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.website_url = f"http://127.0.0.1:{cls.server.server_port}/"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_rendered_javascript_links_reach_the_existing_scraper_contract(self) -> None:
        catalog = _RenderedFixtureScraper().crawl(self.website_url, timeout=10)
        self.assertEqual(catalog["summary"]["application_count"], 1)
        self.assertEqual(catalog["applications"][0]["download_urls"], [self.website_url + "artifact.bin"])


if __name__ == "__main__":
    unittest.main()
