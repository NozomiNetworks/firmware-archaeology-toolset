from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from plugins.base import print_artifact_table, print_result_table, strip_control_chars
from plugins.wayback import render


class WaybackRenderTests(unittest.TestCase):
    def test_strip_control_chars_preserves_tabs_only(self) -> None:
        self.assertEqual(strip_control_chars("a\tb\n\r\x1b[2J\x7f\x9bZ"), "a\tb")

    def test_generic_result_tables_strip_control_characters(self) -> None:
        attack = "value\x1b[2J\x9b\x07"
        output = io.StringIO()
        with redirect_stdout(output):
            print_result_table("demo", [{"confidence": attack, "matched_keywords": [], "name": attack, "query": attack}])
            print_artifact_table(
                [{"artifact_id": "demo:1", "artifact_num": 1, "plugin": "demo", "confidence": attack, "name": attack}],
                set(),
            )
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x9b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertIn("value", rendered)

    def test_format_helpers_cover_status_and_urls(self) -> None:
        self.assertEqual(render.format_timestamp(None), "-")
        self.assertEqual(render.format_timestamp("invalid"), "invalid")
        self.assertEqual(render.format_timestamp("20240101020304"), "2024-01-01 02:03:04")
        self.assertEqual(render.short_url("https://example.test/a?x=1"), "/a?x=1")
        self.assertEqual(render.short_url("https://example.test/"), "/")
        self.assertEqual(render.format_status("missing"), "missing")

    def test_snapshot_and_download_tables_render_selection_and_matches(self) -> None:
        snapshots = [{
            "id": 1, "timestamp": "20240101000000", "target_type": "page",
            "original": "https://example.test/download", "selected": True,
            "discovery_method": "domain-keyword", "matched_keywords": ["fw"],
            "firmware_links": ["https://example.test/fw/a.zip"],
            "selected_firmware_links": ["https://example.test/fw/a.zip"],
        }]
        output = io.StringIO()
        with redirect_stdout(output):
            render.print_snapshot_table(snapshots)
            render.print_download_table([{"snapshot_id": 1, "status": "downloaded", "capture_timestamp": "20240101000000", "url": "https://example.test/fw/a.zip"}])
            render.show_status("demo", {"parser": "example", "target_url": "https://example.test", "snapshots": snapshots, "downloads": []}, has_update_hook=False, has_post_hook=True)
        rendered = output.getvalue()
        self.assertIn("keyword match: fw", rendered)
        self.assertIn("downloaded", rendered)
        self.assertIn("Wayback project status", rendered)

    def test_post_result_renderer_is_generic(self) -> None:
        self.assertTrue(hasattr(render, "print_post_result"))
    def test_firmware_summary_limits_items_and_truncates_when_needed(self) -> None:
        snapshot = {
            "firmware_links": [
                "https://vendor.example/fw/alpha-super-long-name.zip",
                "https://vendor.example/fw/beta-super-long-name.zip",
                "https://vendor.example/fw/gamma-super-long-name.zip",
            ]
        }

        summary = render.firmware_summary(snapshot, max_items=2, max_length=40)
        self.assertIn("alpha-super-long-name.zip", summary)
        self.assertTrue(summary.endswith("..."))
        self.assertNotIn("gamma-super-long-name.zip", summary)

    def test_print_post_result_renders_package_verdict(self) -> None:
        post_result = {"wrapper_kind": "zip", "verdict": "candidate", "evidence": ["payload present"]}

        output = io.StringIO()
        with redirect_stdout(output):
            render.print_post_result(post_result, verbose=False)

        rendered = output.getvalue()
        self.assertIn("Firmware Scouting", rendered)
        self.assertIn("Verdict", rendered)
        self.assertIn("candidate", rendered)

    def test_print_post_result_verbose_renders_image_and_decryption_details(self) -> None:
        result = {"wrapper_kind": "zip", "verdict": "candidate", "details": {"payloads": 1}}
        output = io.StringIO()
        with redirect_stdout(output):
            render.print_post_result(result, verbose=True)
        rendered = output.getvalue()
        for text in ("Wrapper", "zip", "Verdict", "candidate", "Details"):
            self.assertIn(text, rendered)

    def test_print_post_result_strips_control_characters_from_metadata(self) -> None:
        attack = "value\x1b[2J\x9b\x07"
        result = {
            "wrapper_kind": attack,
            "wrapper_summary": {"img_count": 1, "cap_count": 0},
            "images": [{
                "image_path": attack,
                "outer_unpack_dir": attack,
                "inner_fwimage_path": attack,
                "bridge_assessment": {"label": attack, "score": 0, "max_score": 1, "evidence": [attack]},
                "observations": {"file_size": 10, "encrypted_member_count": 1},
                "decryption_hints": {"matched_password": attack, "decrypted_unpack_dir": attack},
            }],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            render.print_post_result(result, verbose=True)
        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x9b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertIn("value", rendered)


if __name__ == "__main__":
    unittest.main()
