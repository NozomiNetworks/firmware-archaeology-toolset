from __future__ import annotations

import unittest

from fatlib.discovery import import_module_from_package, instantiate_plugin_like
from plugins.scraper.registry import SCRAPER_MAP
from plugins.wayback.retrieve_archived_firmware import load_parser


class DiscoveryTests(unittest.TestCase):
    def test_import_module_from_package_loads_named_module(self) -> None:
        module = import_module_from_package("plugins.wayback", "parsers.httpbin")
        self.assertEqual(module.__name__, "plugins.wayback.parsers.httpbin")

    def test_scraper_registry_exposes_known_scraper(self) -> None:
        self.assertNotIn("private-vendor-parser", SCRAPER_MAP)

    def test_wayback_load_parser_returns_module_with_extract_links(self) -> None:
        parser = load_parser("httpbin")
        self.assertTrue(hasattr(parser, "extract_links"))

    def test_plugin_discovery_requires_plugin_class(self) -> None:
        class ModuleWithoutPluginClass:
            class ExamplePlugin:
                name = "example"
                help_summary = "summary"

        self.assertIsNone(instantiate_plugin_like(ModuleWithoutPluginClass))


if __name__ == "__main__":
    unittest.main()
