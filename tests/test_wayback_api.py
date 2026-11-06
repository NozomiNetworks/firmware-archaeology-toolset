from __future__ import annotations

import unittest
from pathlib import Path

from plugins.wayback.parsers import api


class WaybackParserApiTests(unittest.TestCase):
    def test_documented_parser_contract_exports_and_types(self) -> None:
        self.assertEqual(
            set(api.__all__),
            {"ArchiveClient", "Capture", "ProjectRecord", "SnapshotRecord", "extract_links", "update_firmware_link", "post_download"},
        )
        self.assertEqual(api.ProjectRecord.__origin__, dict)
        self.assertEqual(api.SnapshotRecord.__origin__, dict)
        self.assertIsInstance(api.Capture("20240101000000", "https://example.test/fw.zip", "200"), api.Capture)
        self.assertIn("fetch_snapshot_html", api.ArchiveClient.__doc__ or "")

    def test_required_and_optional_contract_hooks_are_explicit_stubs(self) -> None:
        snapshot = {"id": 1, "original": "https://example.test/page", "timestamp": "20240101000000"}
        project = {"name": "demo"}
        with self.assertRaises(NotImplementedError):
            api.extract_links(snapshot, "<html></html>", api.ArchiveClient(sleep=0))
        with self.assertRaises(NotImplementedError):
            api.post_download(project, snapshot, "https://example.test/fw.zip", Path("fw.zip"), api.ArchiveClient(sleep=0))
        with self.assertRaises(NotImplementedError):
            api.update_firmware_link(project, snapshot, "https://example.test/fw.zip", api.ArchiveClient(sleep=0))


if __name__ == "__main__":
    unittest.main()
