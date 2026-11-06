from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fatlib.plugin_config import resolve_config_file, resolve_plugin_dir, strip_plugin_options
from plugins.scraper.registry import SCRAPER_MAP, configure_external_scrapers, resolve_scraper
from plugins.wayback.registry import PARSER_BY_HOST, configure_external_parsers, infer_parser_name, load_parser


SCRAPER_SOURCE = '''
from plugins.scraper.base import WebsiteScraperBase

class FixtureScraper(WebsiteScraperBase):
    name = "fixture-scraper"
    help_summary = "External fixture scraper."
    def can_handle(self, website_url, entry_html=""):
        return website_url.endswith("/fixture")
    def extract_product_links(self, category_html, website_url):
        return []
    def parse_product(self, product_html, product_url):
        return {}

SCRAPER_CLASS = FixtureScraper
'''
WAYBACK_SOURCE = '''
HOSTS = ("fixture.example",)
def extract_links(snapshot, page_html, archive):
    return ["https://fixture.example/firmware.zip"]
'''


class ExternalPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.empty_plugin_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        configure_external_scrapers(self.empty_plugin_dir)
        configure_external_parsers(self.empty_plugin_dir)
        shutil.rmtree(self.empty_plugin_dir)

    def test_public_defaults_have_only_generic_test_implementations(self) -> None:
        configure_external_scrapers(self.empty_plugin_dir)
        configure_external_parsers(self.empty_plugin_dir)
        self.assertNotIn("fixture-scraper", SCRAPER_MAP)
        self.assertIn("httpbin", load_parser("httpbin").__name__)
        self.assertNotIn("fixture.example", PARSER_BY_HOST)

    def test_external_scraper_and_wayback_parser_are_loaded_from_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            wayback_dir = root / "wayback" / "parsers"
            scraper_dir.mkdir(parents=True)
            wayback_dir.mkdir(parents=True)
            (scraper_dir / "fixture.py").write_text(SCRAPER_SOURCE, encoding="utf-8")
            (wayback_dir / "fixture.py").write_text(WAYBACK_SOURCE, encoding="utf-8")

            configure_external_scrapers(root)
            configure_external_parsers(root)

            self.assertEqual(resolve_scraper("fixture-scraper", "https://fixture.example/fixture").name, "fixture-scraper")
            self.assertEqual(infer_parser_name("https://fixture.example/page", None), "fixture")
            self.assertEqual(load_parser("fixture").extract_links({}, "", None), ["https://fixture.example/firmware.zip"])

    def test_duplicate_external_wayback_host_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            parser_dir = root / "wayback" / "parsers"
            parser_dir.mkdir(parents=True)
            source = WAYBACK_SOURCE.replace("fixture.example", "httpbin.org")
            (parser_dir / "duplicate.py").write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Duplicate Wayback parser host"):
                configure_external_parsers(root)

    def test_external_plugin_package_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            parser_dir = root / "wayback" / "parsers"
            parser_dir.mkdir(parents=True)
            (parser_dir / "nested").mkdir()
            (parser_dir / "nested" / "__init__.py").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "packages are not supported"):
                configure_external_parsers(root)

        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory() as source_directory:
            root = Path(temporary_directory)
            parser_dir = root / "wayback" / "parsers"
            parser_dir.mkdir(parents=True)
            source = Path(source_directory) / "outside.py"
            source.write_text(WAYBACK_SOURCE, encoding="utf-8")
            (parser_dir / "outside.py").symlink_to(source)
            with self.assertRaisesRegex(SystemExit, "Symlinked external plugin"):
                configure_external_parsers(root)

        with tempfile.NamedTemporaryFile() as plugin_file:
            with self.assertRaisesRegex(SystemExit, "External plugin root is not a regular directory"):
                configure_external_parsers(Path(plugin_file.name))

        with tempfile.TemporaryDirectory() as temporary_directory:
            dangling_root = Path(temporary_directory) / "missing-root"
            dangling_root.symlink_to(Path(temporary_directory) / "does-not-exist")
            with self.assertRaisesRegex(SystemExit, "External plugin root is not a regular directory"):
                configure_external_parsers(dangling_root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "wayback").mkdir()
            (root / "wayback" / "parsers").write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "External plugin directory is not a directory"):
                configure_external_parsers(root)

        with tempfile.TemporaryDirectory() as temporary_directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(temporary_directory)
            (root / "wayback").symlink_to(outside_directory, target_is_directory=True)
            with self.assertRaisesRegex(SystemExit, "escapes the configured root"):
                configure_external_parsers(root)

    def test_configured_plugin_directory_precedes_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = Path(temporary_directory) / "config.json"
            configured = Path(temporary_directory) / "configured"
            config.write_text(json.dumps({"plugin_dir": str(configured)}), encoding="utf-8")
            self.assertEqual(resolve_plugin_dir(["--config-file", str(config)]), configured)
            explicit = Path(temporary_directory) / "explicit"
            self.assertEqual(
                resolve_plugin_dir(["--config-file", str(config), "--plugin-dir", str(explicit)]),
                explicit,
            )

    def test_config_errors_and_global_option_helpers_are_explicit(self) -> None:
        self.assertEqual(resolve_config_file([]), Path.home() / ".fat" / "config.json")
        with self.assertRaisesRegex(SystemExit, "--plugin-dir requires a value"):
            resolve_plugin_dir(["--plugin-dir"])
        with self.assertRaisesRegex(SystemExit, "--plugin-dir requires a non-empty path"):
            resolve_plugin_dir(["--plugin-dir", " "])
        with self.assertRaisesRegex(SystemExit, "--config-file requires a value"):
            resolve_config_file(["--config-file"])
        self.assertEqual(strip_plugin_options(["project", "demo", "--plugin-dir", "/tmp/x", "--config-file", "/tmp/c"]), ["project", "demo"])
        with self.assertRaisesRegex(SystemExit, "--config-file requires a value"):
            strip_plugin_options(["--config-file"])

        with tempfile.TemporaryDirectory() as temporary_directory:
            config = Path(temporary_directory) / "config.json"
            config.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Could not read FAT config file"):
                resolve_plugin_dir(["--config-file", str(config)])
            config.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "must contain a JSON object"):
                resolve_plugin_dir(["--config-file", str(config)])
            config.write_text(json.dumps({"plugin_dir": 42}), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "plugin_dir.*non-empty string"):
                resolve_plugin_dir(["--config-file", str(config)])
            config.unlink()
            config.symlink_to(Path(temporary_directory) / "missing-config")
            with self.assertRaisesRegex(SystemExit, "regular, non-symlink"):
                resolve_plugin_dir(["--config-file", str(config)])

    def test_external_import_failures_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            scraper_dir.mkdir(parents=True)
            (scraper_dir / "broken.py").write_text("raise RuntimeError('broken fixture')\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Failed to load external plugin"):
                configure_external_scrapers(root)

    def test_invalid_external_scraper_and_duplicate_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            scraper_dir.mkdir(parents=True)
            (scraper_dir / "invalid.py").write_text("VALUE = 1\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "must export SCRAPER_CLASS"):
                configure_external_scrapers(root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            scraper_dir.mkdir(parents=True)
            source = SCRAPER_SOURCE.replace(
                'name = "fixture-scraper"',
                'name = "invalid-renderer"\n    RENDER_MODE = "sometimes"',
            )
            (scraper_dir / "invalid_render.py").write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "invalid browser rendering settings"):
                configure_external_scrapers(root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            scraper_dir.mkdir(parents=True)
            empty_name = SCRAPER_SOURCE.replace('name = "fixture-scraper"', 'name = ""')
            (scraper_dir / "empty.py").write_text(empty_name, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "empty name"):
                configure_external_scrapers(root)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scraper_dir = root / "scraper" / "websites"
            scraper_dir.mkdir(parents=True)
            duplicate = SCRAPER_SOURCE
            (scraper_dir / "one.py").write_text(duplicate, encoding="utf-8")
            (scraper_dir / "two.py").write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Duplicate scraper parser name"):
                configure_external_scrapers(root)

    def test_invalid_external_wayback_contracts_are_rejected(self) -> None:
        cases = {
            "missing_extract.py": "HOSTS = ('missing.example',)\n",
            "missing_hosts.py": "def extract_links(snapshot, page_html, archive): return []\n",
            "invalid_hosts.py": "HOSTS = ('bad:port',)\ndef extract_links(snapshot, page_html, archive): return []\n",
        }
        expected = {
            "missing_extract.py": "must export extract_links",
            "missing_hosts.py": "must define non-empty HOSTS",
            "invalid_hosts.py": "invalid HOSTS entry",
        }
        for filename, source in cases.items():
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                parser_dir = root / "wayback" / "parsers"
                parser_dir.mkdir(parents=True)
                (parser_dir / filename).write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(SystemExit, expected[filename]):
                    configure_external_parsers(root)

    def test_invalid_external_wayback_render_settings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            parser_dir = root / "wayback" / "parsers"
            parser_dir.mkdir(parents=True)
            source = WAYBACK_SOURCE + '\nRENDER_MODE = "sometimes"\n'
            (parser_dir / "invalid_render.py").write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "invalid browser rendering settings"):
                configure_external_parsers(root)

    def test_unknown_wayback_parser_reports_available_names_and_explicit_inference(self) -> None:
        configure_external_parsers(self.empty_plugin_dir)
        self.assertEqual(infer_parser_name("https://unknown.example/", "httpbin"), "httpbin")
        with self.assertRaisesRegex(SystemExit, "Unknown Wayback parser"):
            load_parser("missing")


if __name__ == "__main__":
    unittest.main()
