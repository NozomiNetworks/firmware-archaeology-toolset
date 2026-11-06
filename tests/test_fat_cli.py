"""In-process CLI coverage for the fat.py surfaces the subprocess tests miss.

Subprocess invocations of fat.py do not count toward coverage, so the lead
management, project deletion internals, report and plugin dispatch paths are
exercised here through fat.run_main() directly.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import fat


def run_cli(*args: str) -> tuple[int, str]:
    output = io.StringIO()
    with patch.object(fat.sys, "argv", ["fat.py", *args]), redirect_stdout(output):
        code = fat.run_main()
    return code, output.getvalue()


class LeadCliTests(unittest.TestCase):
    def test_lead_add_list_set_rm_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            run_cli("project", "vendor", "--state-file", str(state_file))
            code, out = run_cli(
                "project", "lead", "add", "https://vendor.example/support",
                "--source", "portal", "--title", "support page",
                "--note", "lists fw 3.2.1", "--state-file", str(state_file),
            )
            self.assertEqual(code, 0)
            self.assertIn("Added lead 1 to vendor", out)
            self.assertIn("support page", out)

            code, out = run_cli("project", "lead", "list", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("ID   Status   Source   URL", out)
            self.assertIn("title: support page", out)
            self.assertIn("note: lists fw 3.2.1", out)

            code, out = run_cli(
                "project", "lead", "set", "1", "status", "checked",
                "--state-file", str(state_file),
            )
            self.assertEqual(code, 0)
            self.assertIn("Updated lead 1", out)
            self.assertIn("Status", out)

            code, out = run_cli("project", "lead", "rm", "1", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("Removed 1 lead(s)", out)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["surface_projects"]["vendor"]["leads"], [])

    def test_bare_lead_command_uses_active_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            run_cli("project", "demo", "--state-file", str(state_file))
            code, out = run_cli(
                "lead", "add", "https://mirror.example/fw.zip",
                "--source", "web", "--state-file", str(state_file),
            )
            self.assertEqual(code, 0)
            self.assertIn("Added lead 1 to demo", out)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["current_project"], "demo")

    def test_lead_list_empty_project_prints_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            run_cli("project", "empty", "--state-file", str(state_file))
            code, out = run_cli("empty", "lead", "list", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("No leads recorded", out)

    def test_lead_unknown_subcommand_prints_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            with self.assertRaises(SystemExit) as caught:
                run_cli("project", "lead", "bogus", "--state-file", str(state_file))
            self.assertIn("fat.py project lead add", str(caught.exception))


class ProjectDeleteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_file = self.root / ".fat.json"

    def test_delete_with_downloads_removes_roots_and_prints_paths(self) -> None:
        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / "fw.zip").write_text("data", encoding="utf-8")
        run_cli("project", "demo", "--state-file", str(self.state_file))
        run_cli(
            "demo", "github", "set", "downloads-dir", str(downloads),
            "--state-file", str(self.state_file),
        )
        code, out = run_cli(
            "project", "delete", "demo", "--delete-downloads", "yes",
            "--state-file", str(self.state_file),
        )
        self.assertEqual(code, 0)
        self.assertIn("Downloads deleted", out)
        self.assertIn("Removed paths", out)
        self.assertFalse(downloads.exists())
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertNotIn("demo", state["surface_projects"])

    def test_delete_resets_current_project_only_when_it_matches(self) -> None:
        run_cli("project", "demo", "--state-file", str(self.state_file))
        run_cli("project", "other", "--state-file", str(self.state_file))
        code, _ = run_cli(
            "project", "delete", "demo", "--delete-downloads", "no",
            "--state-file", str(self.state_file),
        )
        self.assertEqual(code, 0)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertNotIn("demo", state["surface_projects"])
        self.assertEqual(state["current_project"], "other")

    def test_delete_prompts_interactively(self) -> None:
        run_cli(
            "project", "demo", "--output-dir", str(self.root / "out"),
            "--state-file", str(self.state_file),
        )
        with patch("builtins.input", side_effect=["maybe", "y"]):
            code, out = run_cli(
                "project", "delete", "demo", "--state-file", str(self.state_file),
            )
        self.assertEqual(code, 0)
        self.assertIn("Delete Project", out)
        self.assertIn("Please answer `y` or `n`.", out)
        self.assertIn("Downloads deleted", out)

    def test_delete_unknown_project_fails(self) -> None:
        run_cli("project", "demo", "--state-file", str(self.state_file))
        with self.assertRaises(SystemExit) as caught:
            run_cli(
                "project", "delete", "nope", "--delete-downloads", "no",
                "--state-file", str(self.state_file),
            )
        self.assertIn("Unknown project", str(caught.exception))

    def test_delete_prompt_answering_no_keeps_downloads(self) -> None:
        downloads = self.root / "downloads"
        downloads.mkdir()
        (downloads / "fw.zip").write_text("data", encoding="utf-8")
        run_cli("project", "demo", "--state-file", str(self.state_file))
        run_cli(
            "demo", "github", "set", "downloads-dir", str(downloads),
            "--state-file", str(self.state_file),
        )
        with patch("builtins.input", side_effect=["n"]):
            code, out = run_cli(
                "project", "delete", "demo", "--state-file", str(self.state_file),
            )
        self.assertEqual(code, 0)
        self.assertTrue(downloads.exists())
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertNotIn("demo", state["surface_projects"])


class ReportAndDispatchTests(unittest.TestCase):
    def test_bare_and_project_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            output_dir = Path(temp_dir) / "out"
            run_cli(
                "project", "demo", "--keywords", "sdk",
                "--output-dir", str(output_dir), "--state-file", str(state_file),
            )
            code, out = run_cli("report", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("Wrote", out)
            code, out = run_cli("demo", "report", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertTrue((output_dir / "report.md").exists())

    def test_project_without_tool_name_fails(self) -> None:
        # "Missing tool name" is only reachable with no global options: a
        # trailing --state-file would be re-appended and parsed as the tool
        # name. The default state file is only read here; the command exits
        # before any write.
        with self.assertRaises(SystemExit) as caught:
            run_cli("demo")
        self.assertIn("Missing tool name", str(caught.exception))

    def test_plugin_dispatch_runs_offline_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            run_cli("project", "demo", "--state-file", str(state_file))
            code, out = run_cli("demo", "github", "options", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("Max search requests", out)
            code, out = run_cli("demo", "github", "status", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("Surface State", out)

    def test_dispatch_tool_prints_plugin_help(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            run_cli("project", "demo", "--state-file", str(state_file))
            output = io.StringIO()
            with patch.object(
                fat.sys, "argv",
                ["fat.py", "demo", "github", "--help", "--state-file", str(state_file)],
            ), redirect_stdout(output):
                with self.assertRaises(SystemExit):
                    fat.run_main()
            self.assertIn("usage", output.getvalue())

    def test_run_plugin_tool_rejects_unsupported_commands(self) -> None:
        stub = argparse.Namespace(command="post")
        fake = type("FakePlugin", (), {
            "name": "fake",
            "parse_args": staticmethod(lambda argv, commands: stub),
        })()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            with patch.object(fat, "PLUGIN_MAP", {"fake": fake}):
                with self.assertRaises(SystemExit) as caught:
                    fat.run_plugin_tool("demo", "fake", ["post", "--state-file", str(state_file)])
        self.assertIn("is not supported", str(caught.exception))


class SmallGapTests(unittest.TestCase):
    def test_root_status_without_projects_prints_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            code, out = run_cli("status", "--state-file", str(state_file))
            self.assertEqual(code, 0)
            self.assertIn("No projects in state", out)

    def test_missing_logo_file_returns_empty_art(self) -> None:
        self.assertEqual(fat.load_logo(Path("/nonexistent/fat-logo.txt")), "")

    def test_split_project_and_command_empty_argv(self) -> None:
        self.assertEqual(fat.split_project_and_command([]), (None, []))

    def test_format_fatal_error_uses_exception_message(self) -> None:
        self.assertEqual(fat.format_fatal_error(ValueError("boom")), "boom")

    def test_main_reraises_system_exit(self) -> None:
        with patch.object(fat, "run_main", side_effect=SystemExit(3)):
            with self.assertRaises(SystemExit) as caught:
                fat.main()
        self.assertEqual(caught.exception.code, 3)

    def test_main_reraises_when_fat_debug_is_set(self) -> None:
        with patch.dict("os.environ", {"FAT_DEBUG": "1"}), patch.object(
            fat, "run_main", side_effect=ValueError("debug me")
        ):
            with self.assertRaises(ValueError):
                fat.main()


if __name__ == "__main__":
    unittest.main()
