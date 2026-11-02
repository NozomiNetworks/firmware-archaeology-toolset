from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliProjectFlowTests(unittest.TestCase):
    def run_fat(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "fat.py", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_project_set_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            output_dir = Path(temp_dir) / "out"

            self.run_fat("project", "demo", "--keywords", "sdk,portal", "--state-file", str(state_file))
            self.run_fat("project", "set", "output-dir", str(output_dir), "--state-file", str(state_file))
            self.run_fat("project", "set", "timeout", "47", "--state-file", str(state_file))
            result = self.run_fat("report", "--state-file", str(state_file))

            self.assertIn("Wrote", result.stdout)
            report_path = output_dir / "report.md"
            self.assertTrue(report_path.exists())
            report_body = report_path.read_text(encoding="utf-8")
            self.assertIn("Project: `demo`", report_body)
            self.assertIn("Output dir:", report_body)
            self.assertIn("Timeout: `47`", report_body)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            project = state["surface_projects"]["demo"]
            self.assertEqual(project["output_dir"], str(output_dir))
            self.assertEqual(project["timeout"], 47)
            self.assertEqual(project["keywords"], ["sdk", "portal"])

    def test_source_search_policy_settings_persist_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            self.run_fat("project", "demo", "--keywords", "sdk", "--state-file", str(state_file))
            for setting, value in (
                ("max-requests", "3"),
                ("max-enrichments", "2"),
                ("request-delay", "0.25"),
                ("cache-ttl", "120"),
            ):
                self.run_fat("demo", "github", "set", setting, value, "--state-file", str(state_file))
            options = self.run_fat("demo", "github", "options", "--state-file", str(state_file))
            self.assertIn("Max search requests:     3", options.stdout)
            self.assertIn("Max README enrichments:  2", options.stdout)
            self.assertIn("Request delay:           0.25s", options.stdout)
            self.assertIn("Search cache TTL:        120s", options.stdout)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            settings = state["surface_projects"]["demo"]["plugins"]["github"]
            self.assertEqual(settings["max_requests"], 3)
            self.assertEqual(settings["max_enrichments"], 2)
            self.assertEqual(settings["request_delay"], 0.25)
            self.assertEqual(settings["cache_ttl"], 120)

    def test_project_delete_removes_default_and_overridden_download_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            output_dir = Path(temp_dir) / "out"
            custom_dir = Path(temp_dir) / "custom-downloads"

            self.run_fat("project", "demo", "--state-file", str(state_file))
            self.run_fat("project", "set", "output-dir", str(output_dir), "--state-file", str(state_file))
            self.run_fat("demo", "github", "set", "downloads-dir", str(custom_dir), "--state-file", str(state_file))

            default_dir = output_dir / "downloads" / "demo" / "github"
            default_dir.mkdir(parents=True, exist_ok=True)
            custom_dir.mkdir(parents=True, exist_ok=True)
            (default_dir / "default.bin").write_text("default", encoding="utf-8")
            (custom_dir / "custom.bin").write_text("custom", encoding="utf-8")

            result = self.run_fat(
                "project",
                "delete",
                "demo",
                "--delete-downloads",
                "yes",
                "--state-file",
                str(state_file),
            )

            self.assertIn("Removed paths", result.stdout)
            self.assertIn(str(output_dir / "downloads" / "demo"), result.stdout)
            self.assertIn(str(custom_dir), result.stdout)
            self.assertFalse((output_dir / "downloads" / "demo").exists())
            self.assertFalse(custom_dir.exists())

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("demo", state.get("surface_projects", {}))

    def test_bare_set_updates_active_project_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"

            self.run_fat("project", "demo", "--state-file", str(state_file))
            self.run_fat("set", "keywords", "sdk,portal", "--state-file", str(state_file))

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["surface_projects"]["demo"]["keywords"], ["sdk", "portal"])

    def test_project_help_lists_all_subcommands(self) -> None:
        result = self.run_fat("project", "--help")
        for expected in ("project set", "project delete", "--delete-downloads", "project lead"):
            self.assertIn(expected, result.stdout)

    def test_plugin_set_keywords_fails_with_project_level_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"

            self.run_fat("project", "demo", "--state-file", str(state_file))
            result = subprocess.run(
                ["python3", "fat.py", "demo", "dockerhub", "set", "keywords", "sdk,portal", "--state-file", str(state_file)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("project-level", result.stderr)
            self.assertIn("fat.py set keywords", result.stderr)


if __name__ == "__main__":
    unittest.main()
