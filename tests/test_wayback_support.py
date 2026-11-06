from __future__ import annotations

import unittest
from unittest.mock import patch

from plugins.wayback import parser_support


class WaybackSupportTests(unittest.TestCase):
    def test_require_parser_uses_explicit_parser_or_infers_from_snapshot(self) -> None:
        self.assertEqual(parser_support.require_parser({"parser": "explicit"}, []), "explicit")

        snapshots = [{"target_url": "https://httpbin.org/"}]
        self.assertEqual(parser_support.require_parser({"parser": None}, snapshots), "httpbin")

    def test_require_parser_and_hooks_report_missing_or_invalid_extensions(self) -> None:
        with self.assertRaises(SystemExit):
            parser_support.require_parser({"parser": None}, [])

        class EmptyParser:
            pass

        with patch("plugins.wayback.parser_support.load_parser", return_value=EmptyParser()):
            self.assertIsNone(parser_support.load_update_hook({"parser": "empty"}, []))
            self.assertIsNone(parser_support.load_post_hook({"parser": "empty"}, []))

        class InvalidParser:
            update_firmware_link = "invalid"
            post_download = "invalid"

        with patch("plugins.wayback.parser_support.load_parser", return_value=InvalidParser()):
            with self.assertRaises(SystemExit):
                parser_support.load_update_hook({"parser": "invalid"}, [])
            with self.assertRaises(SystemExit):
                parser_support.load_post_hook({"parser": "invalid"}, [])

    def test_load_update_and_post_hooks_return_callable_when_present(self) -> None:
        class FakeParser:
            @staticmethod
            def update_firmware_link(project, snapshot, url, archive):
                return url

            @staticmethod
            def post_download(project, snapshot, url, local_path, archive):
                return {"ok": True}

        with patch("plugins.wayback.parser_support.load_parser", return_value=FakeParser()):
            update_hook = parser_support.load_update_hook({"parser": "fake"}, [])
            post_hook = parser_support.load_post_hook({"parser": "fake"}, [])

        self.assertTrue(callable(update_hook))
        self.assertTrue(callable(post_hook))

    def test_post_hook_is_inferred_from_snapshot_target(self) -> None:
        class FakeParser:
            @staticmethod
            def post_download(project, snapshot, url, local_path, archive):
                return {"ok": True}

        snapshots = [{"target_url": "https://httpbin.org/bytes/32"}]
        with patch("plugins.wayback.parser_support.load_parser", return_value=FakeParser()):
            hook = parser_support.load_post_hook({"parser": None}, snapshots)
        self.assertTrue(callable(hook))


if __name__ == "__main__":
    unittest.main()
