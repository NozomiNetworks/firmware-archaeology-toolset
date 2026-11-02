from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fatlib.reports import (
    plugin_settings_lines,
    plugin_status_from_state,
    render_project_report,
    render_search_diagnostics,
    write_project_report,
)
from plugins.base import escape_markdown, render_human_report


class ReportTests(unittest.TestCase):
    def test_escape_markdown_removes_controls_and_neutralizes_markup(self) -> None:
        value = "evil | [click](https://attacker.test) <script>&\nnext\\\x1b"
        escaped = escape_markdown(value)
        self.assertEqual(
            escaped,
            "evil \\| \\[click\\](https://attacker.test) &lt;script&gt;&amp;next\\\\",
        )
        self.assertNotIn("\n", escaped)
        self.assertNotIn("\x1b", escaped)

    def test_project_report_escapes_all_remote_derived_sections(self) -> None:
        attack = "evil | [click](https://attacker.test) <script>\nrow\\"
        plugin_state = {
            "downloads_dir": attack,
            "parser": attack,
            "website": attack,
            "api_base": attack,
            "capture_policy": attack,
            "per_query": attack,
            "min_match_keywords": attack,
            "max_requests": attack,
            "max_enrichments": attack,
            "request_delay": attack,
            "cache_ttl": attack,
            "catalog_path": attack,
            "report_data": {"plugins": [{"request_stats": {"requests": attack}, "errors": [attack]}]},
            "artifacts": [{"artifact_num": attack, "confidence": attack, "matched_keywords": [attack], "name": attack, "url": attack}],
            "snapshots": [{"id": attack, "timestamp": attack, "selected": True, "firmware_links": [attack], "selected_firmware_links": [attack], "original": attack}],
            "downloads": [{"snapshot_timestamp": attack, "status": attack, "local_path": attack, "url": attack}],
            "run_history": [{"completed_at": attack, "status": attack, "result_count": 1, "selected_count": 1}],
        }
        project = {
            "keywords": [attack],
            "output_dir": attack,
            "timeout": 20,
            "leads": [{"id": attack, "status": attack, "source": attack, "url": attack, "title": attack, "note": attack}],
        }

        class Plugin:
            name = attack

            def ensure_state(self, current_project):
                return plugin_state

            def project_summary(self, current_project):
                return attack

        report = render_project_report("demo" + attack, project, Path(attack), [Plugin()])
        self.assertNotIn("<script>", report)
        self.assertNotIn("\nrow", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("\\|", report)
        self.assertIn("\\[click\\]", report)

    def test_human_report_escapes_remote_derived_fields(self) -> None:
        attack = "evil | [click](https://attacker.test) <script>\nrow\\"
        report = render_human_report(
            {
                "project": attack,
                "tool": attack,
                "session": {"id": attack},
                "keywords": [attack],
                "state": {"per_query": attack, "timeout": attack, "min_match_keywords": attack},
                "plugins": [{
                    "status": attack,
                    "searches": [{"query": attack, "mode": attack, "url": attack}],
                    "results": [],
                    "errors": [attack],
                }],
                "artifacts": [{"artifact_num": attack, "confidence": attack, "matched_keywords": [attack], "name": attack, "url": attack}],
            }
        )
        self.assertNotIn("<script>", report)
        self.assertNotIn("\nrow", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("\\|", report)

    def test_plugin_status_prefers_report_then_history_then_content(self) -> None:
        self.assertEqual(plugin_status_from_state({"report_data": {"plugins": [{"status": "partial"}]}}), "partial")
        self.assertEqual(plugin_status_from_state({"run_history": [{"status": "ok"}]}), "ok")
        self.assertEqual(plugin_status_from_state({"snapshots": [{}]}), "ok")
        self.assertEqual(plugin_status_from_state({"artifacts": [{}]}), "ok")
        self.assertEqual(plugin_status_from_state({}), "-")

    def test_settings_and_report_render_all_persisted_sections(self) -> None:
        plugin_state = {
            "downloads_dir": "output/downloads/demo/test",
            "parser": "example",
            "website": "https://example.test",
            "api_base": "https://api.example.test",
            "capture_policy": "nearest",
            "per_query": 10,
            "min_match_keywords": 2,
            "max_requests": 50,
            "max_enrichments": 10,
            "request_delay": 0,
            "cache_ttl": 3600,
            "catalog_path": "catalog.json",
            "report_data": {
                "plugins": [{
                    "request_stats": {
                        "requests": 2,
                        "max_requests": 50,
                        "enrichments": 1,
                        "max_enrichments": 10,
                        "enrichments_skipped": 0,
                        "cache_hits": 1,
                        "request_budget_exhausted": False,
                    },
                    "errors": ["provider unavailable"],
                }]
            },
            "artifacts": [{"artifact_num": 1, "confidence": "high", "matched_keywords": ["sdk"], "name": "vendor/sdk", "url": "https://example.test/sdk"}],
            "snapshots": [{"id": 1, "timestamp": "20240101000000", "selected": True, "firmware_links": ["fw.zip"], "selected_firmware_links": ["fw.zip"], "original": "https://example.test"}],
            "downloads": [{"snapshot_timestamp": "20240101000000", "status": "downloaded", "local_path": "fw.zip", "url": "https://example.test/fw.zip"}],
            "run_history": [{"completed_at": "now", "status": "ok", "result_count": 1, "selected_count": 1}],
        }
        lines = plugin_settings_lines(plugin_state)
        self.assertIn("- Parser: `example`", lines)

        class Plugin:
            name = "test"

            def ensure_state(self, project):
                return plugin_state

            def project_summary(self, project):
                return "one result"

        with tempfile.TemporaryDirectory() as temp_dir:
            project = {"keywords": ["sdk"], "output_dir": temp_dir, "timeout": 20}
            report = render_project_report("demo", project, Path(temp_dir) / ".fat.json", [Plugin()])
            self.assertIn("## test", report)
            self.assertIn("vendor/sdk", report)
            self.assertIn("Recent Runs", report)
        self.assertIn("Search Diagnostics", report)
        self.assertIn("provider unavailable", report)

    def test_search_diagnostics_render_pagination_completion(self) -> None:
        lines: list[str] = []
        render_search_diagnostics(
            lines,
            {
                "report_data": {
                    "plugins": [{
                        "request_stats": {"requests": 3, "max_requests": 50},
                        "pagination": [
                            {"query": "sdk", "pages_fetched": 2, "state": "complete"},
                            {"query": "portal", "pages_fetched": 1, "state": "request-budget"},
                            {"query": "vendor", "pages_fetched": 1, "state": "html-fallback"},
                            {"query": "<bad>", "pages_fetched": 1, "state": "pagination-loop"},
                        ],
                    }]
                }
            },
        )
        report = "\n".join(lines)
        self.assertIn("Pagination", report)
        self.assertIn("sdk: 2 page(s), complete", report)
        self.assertIn("portal: 1 page(s), request-budget", report)
        self.assertIn("vendor: 1 page(s), html-fallback", report)
        self.assertIn("&lt;bad&gt;: 1 page(s), pagination-loop", report)

    def test_write_project_report_updates_project_and_persists_state(self) -> None:
        class Plugin:
            name = "test"

            def ensure_state(self, project):
                return project.setdefault("plugins", {}).setdefault("test", {})

            def project_summary(self, project):
                return "empty"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = {"output_dir": str(root), "keywords": [], "timeout": 20, "plugins": {}}
            state = {"surface_projects": {"demo": project}}
            path = write_project_report("demo", project, state, root / ".fat.json", [Plugin()])
            self.assertTrue(path.exists())
            self.assertEqual(project["report_path"], str(path))
            self.assertIn('"surface_projects"', (root / ".fat.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
