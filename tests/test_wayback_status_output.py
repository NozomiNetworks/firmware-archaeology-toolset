from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import plugins.wayback as wayback


class WaybackStatusOutputTests(unittest.TestCase):
    def test_show_status_renders_core_fields(self) -> None:
        project = {
            "parser": "examplevendor",
            "downloads_dir": "output/downloads/demo/wayback",
            "capture_policy": "nearest",
            "targets": ["https://vendor.example/downloads"],
            "snapshots": [
                {
                    "id": 1,
                    "target_url": "https://vendor.example/downloads",
                    "timestamp": "20240101010101",
                    "original": "https://vendor.example/downloads",
                    "selected": True,
                    "selected_firmware_links": ["https://vendor.example/fw/app.zip"],
                    "firmware_links": ["https://vendor.example/fw/app.zip"],
                }
            ],
            "downloads": [],
        }

        output = io.StringIO()
        with redirect_stdout(output), patch("plugins.wayback.load_update_hook", return_value=None), patch(
            "plugins.wayback.load_post_hook", return_value=None
        ):
            wayback.show_status("demo", project)

        rendered = output.getvalue()
        self.assertIn("Wayback project status", rendered)
        self.assertIn("Project", rendered)
        self.assertIn("demo", rendered)
        self.assertIn("examplevendor", rendered)
        self.assertIn("Selected firmware", rendered)
        self.assertIn("2024-01-01 01:01:01", rendered)
        self.assertIn("app.zip", rendered)


if __name__ == "__main__":
    unittest.main()
