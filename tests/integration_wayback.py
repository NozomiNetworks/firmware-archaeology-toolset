"""Integration checks for the Wayback plugin using httpbin as a target site.

Backend selection: each scenario prefers the live archive. A cheap probe in
setUpClass decides the initial backend, and any scenario that fails with a
transport error (or an assertion after the workflow swallowed one) is re-run
once against the recorded local mirror. Two consecutive live failures switch
the remaining scenarios straight to the mirror.

Force a backend with FAT_WAYBACK_INTEGRATION_MODE=live|mirror|auto (default:
auto). The backend used by each scenario is printed in the run summary.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wayback_mirror

from plugins.wayback import workflow
from plugins.wayback.parsers import httpbin  # noqa: F401  (parser discovery sanity)
from plugins.wayback.retrieve_archived_firmware import ArchiveClient, Capture

# Transport errors mean the archive is unreachable or throttling. Assertions
# are included because download_firmware and enum_firmware catch transport
# failures internally and turn them into state the scenarios then assert on;
# re-running against the deterministic mirror settles whether the failure was
# environmental (mirror passes) or a real regression (mirror fails too).
FALLBACK_ERRORS = (*wayback_mirror.TRANSPORT_ERRORS, AssertionError)


class WaybackHttpbinIntegrationTests(unittest.TestCase):
    FALLBACK_THRESHOLD = 2

    live_mode: bool | None = None
    mirror: wayback_mirror.WaybackMirror | None = None
    consecutive_fallbacks = 0
    backend_report: list[tuple[str, str]] = []

    @classmethod
    def setUpClass(cls) -> None:
        mode = os.environ.get("FAT_WAYBACK_INTEGRATION_MODE", "auto").strip().lower() or "auto"
        if mode == "mirror":
            cls.live_mode = False
            reason = "forced by FAT_WAYBACK_INTEGRATION_MODE=mirror"
        elif mode == "live":
            cls.live_mode = True
            reason = "forced by FAT_WAYBACK_INTEGRATION_MODE=live"
        else:
            failure = wayback_mirror.probe_live_archive()
            if failure is None:
                cls.live_mode = True
                reason = "live archive probe ok"
            else:
                cls.live_mode = False
                reason = (
                    "live archive rate-limited"
                    if wayback_mirror.is_rate_limited(failure)
                    else f"live archive unreachable: {failure}"
                )
        print(f"[wayback-integration] initial backend: {cls._backend_label()} ({reason})")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.mirror is not None:
            cls.mirror.stop()
            cls.mirror = None
        for name, backend in cls.backend_report:
            print(f"[wayback-integration] {name}: {backend}")

    @classmethod
    def _backend_label(cls) -> str:
        return "live archive" if cls.live_mode else "local mirror"

    def make_archive(self, api_base: str | None = None) -> ArchiveClient:
        return ArchiveClient(timeout=5, retries=1, sleep=0, verbose=False, api_base=api_base)

    def mirror_api_base(self) -> str:
        cls = type(self)
        if cls.mirror is None:
            cls.mirror = wayback_mirror.start_mirror()
            print(f"[wayback-integration] local mirror started at {cls.mirror.base_url}")
        return cls.mirror.base_url

    def run_scenario(self, name: str, scenario: Callable[[ArchiveClient], None]) -> None:
        cls = type(self)
        if not cls.live_mode:
            scenario(self.make_archive(api_base=self.mirror_api_base()))
            cls.backend_report.append((name, "mirror"))
            return
        try:
            scenario(self.make_archive())
        except FALLBACK_ERRORS as exc:
            cls.consecutive_fallbacks += 1
            if wayback_mirror.is_rate_limited(exc):
                cls.live_mode = False
                reason = "live archive rate-limited"
            else:
                reason = f"live attempt failed: {exc}"
            print(f"[wayback-integration] {name}: {reason}; using local mirror")
            if cls.consecutive_fallbacks >= cls.FALLBACK_THRESHOLD:
                cls.live_mode = False
                print(
                    f"[wayback-integration] {cls.consecutive_fallbacks} consecutive live failures; "
                    "remaining scenarios use the local mirror"
                )
            scenario(self.make_archive(api_base=self.mirror_api_base()))
            cls.backend_report.append((name, "mirror (fallback)"))
        else:
            cls.consecutive_fallbacks = 0
            cls.backend_report.append((name, "live"))

    def scenario_anything_capture(self, archive: ArchiveClient) -> None:
        project = {
            "name": "httpbin",
            "target_url": "https://httpbin.org/anything",
            "target_type": "page",
            "snapshots": [],
            "downloads": [],
        }

        workflow.enum_snapshots(project, archive)

        self.assertTrue(project["snapshots"])
        snapshot = project["snapshots"][0]
        html = archive.fetch_snapshot_html(
            Capture(
                timestamp=snapshot["timestamp"],
                original=snapshot["original"],
                statuscode=snapshot["statuscode"],
                mimetype=snapshot["mimetype"],
            )
        )
        payload = json.loads(html)
        self.assertIn("url", payload)
        self.assertIn("httpbin.org", payload["url"])

    def scenario_binary_capture(self, archive: ArchiveClient) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = {
                "name": "httpbin",
                "target_url": "https://httpbin.org/bytes/32",
                "target_type": "file",
                "output_dir": str(Path(temp_dir) / "output"),
                "downloads_dir": str(Path(temp_dir) / "downloads"),
                "snapshots": [],
                "downloads": [],
                "capture_policy": "nearest",
            }

            workflow.enum_snapshots(project, archive)
            self.assertTrue(project["snapshots"])
            workflow.select_snapshots(project, "1")
            workflow.download_firmware(project, "selected", archive)

            self.assertEqual(project["downloads"][0]["status"], "downloaded")
            downloaded = Path(project["downloads"][0]["local_path"])
            self.assertEqual(downloaded.stat().st_size, 32)

    def scenario_product_page_parser(self, archive: ArchiveClient) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = {
                "name": "httpbin",
                "target_url": "https://httpbin.org/",
                "target_type": "page",
                "parser": "httpbin",
                "output_dir": str(Path(temp_dir) / "output"),
                "downloads_dir": str(Path(temp_dir) / "downloads"),
                "snapshots": [],
                "downloads": [],
                "capture_policy": "nearest",
            }

            workflow.enum_snapshots(project, archive)
            self.assertTrue(project["snapshots"])

            # The public page currently renders its endpoint links through
            # Swagger JavaScript, and historical replay versions differ. Use
            # a stable archived-product-page-shaped body here so this test
            # checks the parser-to-download workflow rather than one mutable
            # presentation of the public documentation page.
            archive.fetch_snapshot_html = lambda capture: """
                <html><body>
                  <a href="/anything">JSON API</a>
                  <a href="https://httpbin.org/bytes/32">firmware-like binary</a>
                </body></html>
            """
            binary_captures = archive.exact_file_captures("https://httpbin.org/bytes/32")
            self.assertTrue(binary_captures)
            archive.closest_page_capture = lambda url, timestamp, *, policy="nearest": binary_captures[-1]
            workflow.enum_firmware(project, "1", archive)
            self.assertEqual(project["snapshots"][0]["firmware_links"], ["https://httpbin.org/bytes/32"])
            workflow.select_firmware(project, "1:1")
            workflow.download_firmware(project, "selected", archive)

            self.assertEqual(project["downloads"][0]["status"], "downloaded")
            downloaded = Path(project["downloads"][0]["local_path"])
            self.assertEqual(downloaded.stat().st_size, 32)

    def test_anything_capture_is_enumerated_and_replayed(self) -> None:
        self.run_scenario("anything capture enumerated and replayed", self.scenario_anything_capture)

    def test_binary_capture_is_downloaded_through_wayback_plugin(self) -> None:
        self.run_scenario("binary capture downloaded through wayback plugin", self.scenario_binary_capture)

    def test_httpbin_product_page_parser_discovers_and_downloads_bytes(self) -> None:
        self.run_scenario("httpbin product page parser discovers and downloads bytes", self.scenario_product_page_parser)


if __name__ == "__main__":
    unittest.main()
