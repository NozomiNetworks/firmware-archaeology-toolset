from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fatlib.browser import BrowserError
from plugins.scraper import ScraperPlugin
from plugins.scraper.base import WebsiteScraperBase
from plugins.scraper.registry import resolve_scraper


class FakeScraper:
    name = "fake-scraper"

    def crawl(self, website: str, timeout: int, *, verbose: bool = False) -> dict:
        return {
            "source": website,
            "scraper": self.name,
            "applications": [
                {
                    "slug": "app-one",
                    "title": "App One",
                    "product_url": f"{website}/product/app-one/",
                    "download_count": 1,
                    "download_ids": ["1"],
                    "download_urls": [f"{website}/files/app-one.zip"],
                    "saved_files": ["app-one/app-one.zip"],
                }
            ],
            "summary": {"application_count": 1},
        }

    def product_artifact(self, product: dict, website: str) -> dict:
        return {
            "plugin": "scraper",
            "kind": "website_artifact_bundle",
            "query": website,
            "name": product["slug"],
            "url": product["product_url"],
            "description": product["title"],
            "matched_keywords": [],
            "signals": ["scraped"],
            "confidence": "catalog",
            "title": product["title"],
            "slug": product["slug"],
            "download_urls": product["download_urls"],
            "download_ids": product["download_ids"],
            "saved_files": product["saved_files"],
            "website": website,
            "scraper_name": self.name,
        }

    def relative_download_path(self, product_slug: str, download_url: str) -> Path:
        return Path(product_slug) / "artifact.zip"

    def download_file(self, url: str, destination: Path, timeout: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(url, encoding="utf-8")


class ScraperPluginTests(unittest.TestCase):
    def make_project(self, root: Path) -> dict:
        return {
            "name": "demo",
            "keywords": [],
            "output_dir": str(root / "output"),
            "timeout": 12,
            "plugins": {},
            "report_path": "",
            "report_generated_at": "",
        }

    def test_search_persists_catalog_and_artifacts(self) -> None:
        plugin = ScraperPlugin()
        fake_scraper = FakeScraper()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)
            tool_state = plugin.ensure_state(project)
            tool_state["parser"] = "fake"
            tool_state["website"] = "https://example.test/store"
            args = SimpleNamespace(quiet=True, state_file=state_file, selection="selected", value=None)

            with patch("plugins.scraper.resolve_scraper", return_value=fake_scraper):
                self.assertEqual(plugin.search("demo", project, args), 0)

            updated_state = plugin.ensure_state(project)
            self.assertEqual(updated_state["parser"], fake_scraper.name)
            self.assertEqual(len(updated_state["artifacts"]), 1)
            self.assertEqual(len(updated_state["run_history"]), 1)
            self.assertTrue(Path(updated_state["catalog_path"]).exists())

    def test_set_parser_website_and_options(self) -> None:
        plugin = ScraperPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)

            with patch.dict("plugins.scraper.SCRAPER_MAP", {"fake": object()}, clear=False):
                parser_args = SimpleNamespace(selection="parser", value="fake", quiet=True, state_file=state_file)
                self.assertEqual(plugin.set("demo", project, parser_args), 0)

            website_args = SimpleNamespace(selection="website", value="https://example.test/store", quiet=True, state_file=state_file)
            self.assertEqual(plugin.set("demo", project, website_args), 0)

            output = io.StringIO()
            options_args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=state_file)
            with redirect_stdout(output):
                self.assertEqual(plugin.options("demo", project, options_args), 0)

            rendered = output.getvalue()
            self.assertIn("fake", rendered)
            self.assertIn("https://example.test/store", rendered)
            self.assertIn("Available parsers", rendered)

            reset_args = SimpleNamespace(selection="website", value="None", quiet=True, state_file=state_file)
            self.assertEqual(plugin.set("demo", project, reset_args), 0)
            self.assertEqual(plugin.ensure_state(project)["website"], "")

    def test_download_command_uses_scraper_download_hook(self) -> None:
        plugin = ScraperPlugin()
        fake_scraper = FakeScraper()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)
            tool_state = plugin.ensure_state(project)
            tool_state["artifacts"] = [
                {
                    "artifact_id": "scraper:app-one",
                    "artifact_num": 1,
                    "plugin": "scraper",
                    "kind": "website_artifact_bundle",
                    "query": "https://example.test/store",
                    "name": "app-one",
                    "url": "https://example.test/store/product/app-one/",
                    "description": "App One",
                    "matched_keywords": [],
                    "signals": ["scraped"],
                    "confidence": "catalog",
                    "title": "App One",
                    "slug": "app-one",
                    "download_urls": ["https://example.test/store/files/app-one.zip"],
                    "download_ids": ["1"],
                    "saved_files": ["app-one/artifact.zip"],
                    "website": "https://example.test/store",
                    "scraper_name": fake_scraper.name,
                    "selected": True,
                }
            ]
            tool_state["selected_artifact_ids"] = ["scraper:app-one"]
            args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=state_file)

            with patch("plugins.scraper.resolve_scraper", return_value=fake_scraper):
                self.assertEqual(plugin.download_command("demo", project, args), 0)

            self.assertEqual(len(tool_state["downloads"]), 1)
            download_dir = Path(tool_state["downloads"][0]["path"])
            self.assertTrue((download_dir / "artifact.json").exists())

    def test_download_rejects_path_traversal_slug(self) -> None:
        plugin = ScraperPlugin()
        fake_scraper = FakeScraper()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads_dir = root / "downloads"
            downloads_dir.mkdir(parents=True)
            artifact = {
                "artifact_id": "scraper:..",
                "slug": "..",
                "download_urls": ["https://example.test/store/files/app-one.zip"],
                "website": "https://example.test/store",
                "scraper_name": fake_scraper.name,
            }
            with patch("plugins.scraper.resolve_scraper", return_value=fake_scraper):
                with self.assertRaises(ValueError):
                    plugin.download(SimpleNamespace(timeout=12, verbose=False), artifact, downloads_dir)
            self.assertFalse((root / "artifact.zip").exists())
            self.assertFalse((root / "artifact.json").exists())

    def test_download_rejects_unsafe_overridden_relative_path(self) -> None:
        plugin = ScraperPlugin()
        fake_scraper = FakeScraper()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads_dir = root / "downloads"
            downloads_dir.mkdir(parents=True)
            artifact = {
                "artifact_id": "scraper:app-one",
                "slug": "app-one",
                "download_urls": ["https://example.test/store/files/app-one.zip"],
                "website": "https://example.test/store",
                "scraper_name": fake_scraper.name,
            }
            for unsafe_path in (
                Path("../escape.zip"),
                Path("nested/../../escape.zip"),
                Path("/tmp/example-escape.zip"),
            ):
                with self.subTest(unsafe_path=unsafe_path):
                    with patch.object(fake_scraper, "relative_download_path", return_value=unsafe_path):
                        with patch("plugins.scraper.resolve_scraper", return_value=fake_scraper):
                            with self.assertRaises(ValueError):
                                plugin.download(SimpleNamespace(timeout=12, verbose=False), artifact, downloads_dir)
            self.assertFalse((root / "escape.zip").exists())

    def test_website_scraper_base_crawl_and_catalog_helpers(self) -> None:
        class FakeWebsite(WebsiteScraperBase):
            name = "fake"

            def extract_product_links(self, category_html, website_url):
                return [website_url + "/product/a/"]

            def parse_product(self, product_html, product_url):
                return {"slug": "a", "title": "A", "product_url": product_url, "download_urls": ["https://x/download/1/a.zip"], "download_ids": ["1"]}

        scraper = FakeWebsite()
        scraper.fetch_text = lambda url, timeout: "html"
        catalog = scraper.crawl("https://example.test", 5, verbose=True)
        self.assertEqual(catalog["summary"]["application_count"], 1)
        artifact = scraper.product_artifact(catalog["applications"][0], "https://example.test")
        self.assertEqual(artifact["name"], "a")
        self.assertEqual(scraper.filename_from_download_url("https://x/download/1/a.zip"), "a.zip")
        self.assertEqual(scraper.filename_from_download_url("https://x"), "download.bin")
        self.assertEqual(scraper.filename_from_download_url("https://x/.."), "download.bin")
        self.assertEqual(scraper.relative_download_path("a", "https://x/download/1/a.zip").name, "a.zip")

    def test_rendering_scraper_reuses_one_browser_session_for_category_and_products(self) -> None:
        class RenderedWebsite(WebsiteScraperBase):
            name = "rendered"
            RENDER_MODE = "required"
            RENDER_WAIT_FOR = ".product"

            def extract_product_links(self, category_html, website_url):
                self.category_html = category_html
                return [website_url + "/product/a/", website_url + "/product/b/"]

            def parse_product(self, product_html, product_url):
                return {"slug": product_html, "title": product_url, "product_url": product_url, "download_urls": []}

        class FakeBrowserSession:
            instances = []

            def __init__(self, options):
                self.options = options
                self.rendered = []
                self.closed = False
                self.__class__.instances.append(self)

            def __enter__(self):
                return self

            def render(self, url, *, timeout):
                self.rendered.append((url, timeout))
                return "category" if url.endswith("example.test") else url.rsplit("/", 2)[-2]

            def close(self):
                self.closed = True

        with patch("plugins.scraper.base.BrowserSession", FakeBrowserSession):
            scraper = RenderedWebsite()
            catalog = scraper.crawl("https://example.test", 5)

        self.assertEqual(catalog["summary"]["application_count"], 2)
        self.assertEqual(len(FakeBrowserSession.instances), 1)
        session = FakeBrowserSession.instances[0]
        self.assertEqual(
            session.rendered,
            [
                ("https://example.test", 5),
                ("https://example.test/product/a/", 5),
                ("https://example.test/product/b/", 5),
            ],
        )
        self.assertEqual(session.options.wait_for, ".product")
        self.assertTrue(session.closed)

    def test_preferred_rendering_falls_back_to_static_html(self) -> None:
        class PreferredWebsite(WebsiteScraperBase):
            name = "preferred"
            RENDER_MODE = "preferred"

            def extract_product_links(self, category_html, website_url):
                self.seen_category = category_html
                return []

            def parse_product(self, product_html, product_url):
                return {}

        class FailingBrowserSession:
            def __init__(self, options):
                pass

            def __enter__(self):
                raise BrowserError("browser unavailable")

            def close(self):
                pass

        scraper = PreferredWebsite()
        scraper.fetch_text = lambda url, timeout: "static category"
        with patch("plugins.scraper.base.BrowserSession", FailingBrowserSession):
            catalog = scraper.crawl("https://example.test", 5, verbose=True)
        self.assertEqual(scraper.seen_category, "static category")
        self.assertEqual(catalog["summary"]["application_count"], 0)

    def test_required_rendering_failure_is_not_hidden(self) -> None:
        class RequiredWebsite(WebsiteScraperBase):
            name = "required"
            RENDER_MODE = "required"

            def extract_product_links(self, category_html, website_url):
                return []

            def parse_product(self, product_html, product_url):
                return {}

        class FailingBrowserSession:
            def __init__(self, options):
                pass

            def __enter__(self):
                raise BrowserError("browser unavailable")

            def close(self):
                pass

        with patch("plugins.scraper.base.BrowserSession", FailingBrowserSession):
            with self.assertRaisesRegex(BrowserError, "browser unavailable"):
                RequiredWebsite().crawl("https://example.test", 5)

    def test_scraper_registry_resolves_explicit_and_auto_parsers(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_scraper("example-parser", "https://example.test")
        with self.assertRaises(SystemExit):
            resolve_scraper(None, "https://example.test/catalog/")
        with self.assertRaises(SystemExit):
            resolve_scraper("missing", "https://example.test")


if __name__ == "__main__":
    unittest.main()
