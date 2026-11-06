"""Tests for project lead commands and the fatlib.leads store."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from fatlib.leads import (
    add_lead,
    iter_leads,
    remove_leads,
    set_lead_field,
)


ROOT = Path(__file__).resolve().parents[1]


class LeadStoreUnitTests(unittest.TestCase):
    def make_project(self) -> dict:
        return {"name": "demo", "leads": []}

    def test_add_assigns_sequential_and_stable_ids(self) -> None:
        project = self.make_project()
        first = add_lead(project, "https://vendor.example/a", source="portal")
        second = add_lead(project, "https://vendor.example/b", source="web")
        self.assertEqual((first["id"], second["id"]), (1, 2))

        remove_leads(project, first["id"])
        third = add_lead(project, "https://vendor.example/c", source="user")
        self.assertEqual(third["id"], 3)

    def test_add_rejects_duplicate_url_with_explicit_error(self) -> None:
        project = self.make_project()
        add_lead(project, "https://vendor.example/page", source="portal")
        with self.assertRaises(SystemExit) as raised:
            add_lead(project, "https://vendor.example/page", source="web")
        self.assertIn("already records this URL", str(raised.exception))
        self.assertIn("lead set 1", str(raised.exception))

    def test_duplicate_detection_ignores_casing_and_trailing_slash(self) -> None:
        project = self.make_project()
        add_lead(project, "https://Vendor.example/Page/", source="portal")
        with self.assertRaises(SystemExit):
            add_lead(project, "https://vendor.example/page", source="web")

    def test_add_validates_url_source_and_status(self) -> None:
        project = self.make_project()
        with self.assertRaises(SystemExit) as raised:
            add_lead(project, "vendor.example/page", source="web")
        self.assertIn("absolute http(s) URL", str(raised.exception))

        with self.assertRaises(SystemExit) as raised:
            add_lead(project, "https://vendor.example/a", source="bogus")
        self.assertIn("Unknown lead source", str(raised.exception))

        with self.assertRaises(SystemExit) as raised:
            add_lead(project, "https://vendor.example/a", source="web", status="bogus")
        self.assertIn("Unknown lead status", str(raised.exception))

    def test_set_lead_field_updates_and_unsets(self) -> None:
        project = self.make_project()
        lead = add_lead(project, "https://vendor.example/a", source="portal", title="t", note="n")

        updated = set_lead_field(project, lead["id"], "status", "acted")
        self.assertEqual(updated["status"], "acted")

        updated = set_lead_field(project, lead["id"], "note", "none")
        self.assertEqual(updated["note"], "")

        with self.assertRaises(SystemExit):
            set_lead_field(project, lead["id"], "status", "none")
        with self.assertRaises(SystemExit):
            set_lead_field(project, lead["id"], "bogus", "x")
        with self.assertRaises(SystemExit):
            set_lead_field(project, 99, "status", "dead")

    def test_remove_leads_is_all_or_nothing(self) -> None:
        project = self.make_project()
        lead = add_lead(project, "https://vendor.example/a", source="portal")

        with self.assertRaises(SystemExit) as raised:
            remove_leads(project, f"{lead['id']},7")
        self.assertIn("No lead with id 7", str(raised.exception))
        self.assertEqual(len(project["leads"]), 1)

        removed = remove_leads(project, str(lead["id"]))
        self.assertEqual([item["id"] for item in removed], [lead["id"]])
        self.assertEqual(project["leads"], [])

        with self.assertRaises(SystemExit):
            remove_leads(project, "x")

    def test_iter_leads_filters_by_status(self) -> None:
        project = self.make_project()
        add_lead(project, "https://vendor.example/a", source="portal", status="new")
        add_lead(project, "https://vendor.example/b", source="web", status="dead")

        self.assertEqual(len(iter_leads(project)), 2)
        self.assertEqual([lead["id"] for lead in iter_leads(project, "dead")], [2])
        with self.assertRaises(SystemExit):
            iter_leads(project, "bogus")


class ProjectLeadFlowTests(unittest.TestCase):
    def run_fat(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "fat.py", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=check,
        )

    def test_lead_add_list_set_rm_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            output_dir = Path(temp_dir) / "out"

            self.run_fat("project", "demo", "--keywords", "fw", "--state-file", str(state_file))
            self.run_fat("project", "set", "output-dir", str(output_dir), "--state-file", str(state_file))
            self.run_fat(
                "project", "lead", "add", "https://vendor.example/support/model",
                "--source", "portal", "--title", "support page", "--note", "lists fw 3.2.1",
                "--state-file", str(state_file),
            )
            self.run_fat(
                "project", "lead", "add", "https://mirror.example/fw.zip",
                "--source", "web", "--state-file", str(state_file),
            )

            listing = self.run_fat("project", "lead", "list", "--state-file", str(state_file))
            self.assertIn("support/model", listing.stdout)
            self.assertIn("mirror.example", listing.stdout)
            self.assertIn("Leads", listing.stdout)
            self.assertIn("2", listing.stdout)

            self.run_fat("project", "lead", "set", "1", "status", "acted", "--state-file", str(state_file))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            leads = state["surface_projects"]["demo"]["leads"]
            self.assertEqual([lead["status"] for lead in leads], ["acted", "new"])

            self.run_fat("project", "lead", "set", "2", "note", "none", "--state-file", str(state_file))
            self.run_fat("project", "lead", "rm", "2", "--state-file", str(state_file))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            leads = state["surface_projects"]["demo"]["leads"]
            self.assertEqual([lead["id"] for lead in leads], [1])

            filtered = self.run_fat("project", "lead", "list", "dead", "--state-file", str(state_file))
            self.assertIn("No leads recorded", filtered.stdout)

            status = self.run_fat("demo", "status", "--state-file", str(state_file))
            self.assertIn("Leads", status.stdout)

            self.run_fat("report", "--state-file", str(state_file))
            report_body = (output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Leads", report_body)
            self.assertIn("support/model", report_body)

    def test_lead_add_rejects_duplicate_url_at_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            self.run_fat("project", "demo", "--state-file", str(state_file))
            self.run_fat(
                "project", "lead", "add", "https://vendor.example/page",
                "--source", "portal", "--state-file", str(state_file),
            )
            result = self.run_fat(
                "project", "lead", "add", "https://vendor.example/page",
                "--source", "web", "--state-file", str(state_file), check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already records this URL", result.stderr)

    def test_lead_add_rejects_bad_url_and_source_at_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            self.run_fat("project", "demo", "--state-file", str(state_file))

            result = self.run_fat(
                "project", "lead", "add", "vendor.example/page",
                "--source", "web", "--state-file", str(state_file), check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absolute http(s) URL", result.stderr)

            result = self.run_fat(
                "project", "lead", "add", "https://vendor.example/page",
                "--source", "bogus", "--state-file", str(state_file), check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Unknown lead source", result.stderr)
            self.assertIn("portal", result.stderr)

    def test_lead_commands_require_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            result = self.run_fat(
                "project", "lead", "add", "https://vendor.example/page",
                "--source", "web", "--state-file", str(state_file), check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No project selected", result.stderr)

    def test_project_prefixed_and_bare_lead_forms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat-test.json"
            self.run_fat("project", "demo", "--state-file", str(state_file))
            self.run_fat(
                "demo", "lead", "add", "https://vendor.example/page",
                "--source", "portal", "--state-file", str(state_file),
            )
            result = self.run_fat("demo", "lead", "list", "--state-file", str(state_file))
            self.assertIn("vendor.example", result.stdout)

            self.run_fat("project", "other", "--state-file", str(state_file))
            result = self.run_fat("lead", "list", "--state-file", str(state_file))
            self.assertIn("No leads recorded", result.stdout)

            self.run_fat("project", "demo", "--state-file", str(state_file))
            result = self.run_fat("lead", "list", "--state-file", str(state_file))
            self.assertIn("vendor.example", result.stdout)


if __name__ == "__main__":
    unittest.main()
