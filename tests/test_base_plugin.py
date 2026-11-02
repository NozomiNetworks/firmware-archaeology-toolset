from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace

from plugins.base import (
    PluginContext,
    annotate_artifacts,
    artifact_index,
    classify_match,
    effective_min_match_keywords,
    ensure_state_shape,
    keyword_variants,
    load_state,
    matched_keywords,
    merge_named_results,
    normalize,
    parse_keywords,
    resolve_selection,
    safe_name,
    save_state,
    SurfacePluginBase,
)


class DummyPlugin(SurfacePluginBase):
    name = "dummy"
    help_summary = "dummy"

    def run(self, context):
        return {
            "name": self.name,
            "status": "ok",
            "searches": [{"query": "sdk", "url": "https://example.test/search?q=sdk"}],
            "errors": [],
            "results": [
                {
                    "plugin": self.name,
                    "kind": "repository",
                    "query": "sdk",
                    "name": "vendor/sdk",
                    "url": "https://example.test/vendor/sdk",
                    "description": "sdk repo",
                    "matched_keywords": ["sdk"],
                    "signals": ["keyword:sdk"],
                    "confidence": "high",
                }
            ],
        }

    def download(self, context, artifact, downloads_dir: Path):
        target_dir = downloads_dir / "downloaded-artifact"
        target_dir.mkdir(parents=True, exist_ok=True)
        output_file = target_dir / "artifact.txt"
        output_file.write_text(artifact["name"], encoding="utf-8")
        return {"status": "downloaded", "path": str(target_dir), "files": ["artifact.txt"]}


class SurfacePluginBaseTests(unittest.TestCase):
    def make_project(self, root: Path) -> dict:
        return {
            "name": "demo",
            "keywords": ["sdk"],
            "output_dir": str(root / "output"),
            "timeout": 9,
            "plugins": {},
            "report_path": "",
            "report_generated_at": "",
        }

    def test_search_select_download_and_reset_setting(self) -> None:
        plugin = DummyPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)

            search_args = SimpleNamespace(quiet=True, state_file=state_file, selection="selected", value=None)
            self.assertEqual(plugin.search("demo", project, search_args), 0)

            tool_state = plugin.ensure_state(project)
            self.assertEqual(len(tool_state["artifacts"]), 1)
            self.assertEqual(len(tool_state["run_history"]), 1)
            self.assertEqual(tool_state["artifacts"][0]["artifact_num"], 1)

            select_args = SimpleNamespace(selection="1", value=None, quiet=True, state_file=state_file)
            self.assertEqual(plugin.select("demo", project, select_args), 0)
            self.assertEqual(tool_state["selected_artifact_ids"], ["dummy:vendor/sdk"])

            download_args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=state_file)
            self.assertEqual(plugin.download_command("demo", project, download_args), 0)
            self.assertEqual(len(tool_state["downloads"]), 1)
            download_path = Path(tool_state["downloads"][0]["path"])
            self.assertTrue((download_path / "artifact.txt").exists())

            set_args = SimpleNamespace(selection="downloads-dir", value="None", quiet=True, state_file=state_file)
            self.assertEqual(plugin.set("demo", project, set_args), 0)
            self.assertEqual(
                tool_state["downloads_dir"],
                str(Path(project["output_dir"]) / "downloads" / "demo" / "dummy"),
            )

    def test_safe_name_rejects_dot_only_paths(self) -> None:
        self.assertEqual(safe_name("."), "artifact")
        self.assertEqual(safe_name(".."), "artifact")
        self.assertEqual(safe_name("..."), "artifact")
        self.assertEqual(safe_name("app-one"), "app-one")

    def test_set_keywords_rejects_project_level_setting(self) -> None:
        plugin = DummyPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)

            set_args = SimpleNamespace(selection="keywords", value="sdk, portal , sdk", quiet=True, state_file=state_file)
            with self.assertRaises(SystemExit) as error:
                plugin.set("demo", project, set_args)
            self.assertIn("project-level", str(error.exception))

    def test_options_renders_current_setting_values(self) -> None:
        plugin = DummyPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)
            tool_state = plugin.ensure_state(project)
            tool_state["per_query"] = 42
            tool_state["min_match_keywords"] = 1
            tool_state["downloads_dir"] = str(root / "custom-downloads")
            args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=state_file)

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(plugin.options("demo", project, args), 0)

            rendered = output.getvalue()
            self.assertIn("Plugin Options", rendered)
            self.assertIn("dummy", rendered)
            self.assertIn("custom-downloads", rendered)
            self.assertIn("42", rendered)
            self.assertIn("1", rendered)

    def test_validate_args_enforces_standard_command_shapes(self) -> None:
        plugin = DummyPlugin()

        plugin.validate_args(SimpleNamespace(command="search", selection="selected", value=None))
        plugin.validate_args(SimpleNamespace(command="download", selection="all", value=None))
        plugin.validate_args(SimpleNamespace(command="set", selection="downloads-dir", value="output/downloads/demo/dummy"))

        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="status", selection="1", value=None))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="select", selection="selected", value=None))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="download", selection="selected", value="extra"))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="set", selection="selected", value=None))

    def test_base_matching_helpers_cover_normalization_and_confidence(self) -> None:
        self.assertEqual(normalize("VendorSDK_API"), "vendor sdk api")
        self.assertIn("vendor sdk", keyword_variants("VendorSDK"))
        self.assertEqual(parse_keywords("sdk, portal, sdk"), ["sdk", "portal"])
        self.assertEqual(matched_keywords("Vendor SDK portal", ["sdk", "portal", "missing"]), ["sdk", "portal"])
        self.assertEqual(classify_match(["sdk"], 1, "vendor/sdk", "repo" )[0], "high")
        partial = classify_match(["sdk", "portal"], 2, "vendor/sdk", "repo")
        self.assertEqual(partial[0], "review")
        self.assertEqual(partial[1], ["sdk"])
        self.assertEqual(effective_min_match_keywords(4, ["sdk", "portal"]), 2)
        self.assertEqual(effective_min_match_keywords(2, []), 2)

    def test_base_state_and_artifact_helpers_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            self.assertEqual(load_state(path), {"version": 1})
            state = ensure_state_shape({})
            state["current"]["artifacts"] = [{"artifact_id": "x:one", "artifact_num": 1}]
            save_state(path, state)
            self.assertEqual(load_state(path)["version"], 1)

        report = {"results": [{"plugin": "x", "query": "one", "name": "one", "confidence": "review", "matched_keywords": []}]}
        artifacts = annotate_artifacts(report, {"x:one"})
        self.assertTrue(artifacts[0]["selected"])
        self.assertEqual(artifact_index(artifacts)[1][1]["name"], "one")
        self.assertEqual(resolve_selection("1", artifacts, set())[0]["name"], "one")
        with self.assertRaises(SystemExit):
            resolve_selection("missing", artifacts, set())

    def test_base_merge_and_context_helpers(self) -> None:
        merged = merge_named_results([
            {"name": "one", "query": "a", "matched_keywords": ["a"], "signals": [], "confidence": "review", "description": "old", "url": "old"},
            {"name": "one", "query": "b", "matched_keywords": ["a", "b"], "signals": ["readme"], "confidence": "high", "description": "new", "url": "new"},
        ])
        self.assertEqual(merged[0]["description"], "new")
        context = PluginContext(["sdk", "sdk", ""], 10, 5, 1, "id", False, Path("state"))
        self.assertEqual(context.queries(), ["sdk"])

    def test_base_workflow_errors_and_clear_selection(self) -> None:
        plugin = DummyPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=root / ".fat.json")
            with self.assertRaises(SystemExit):
                plugin.search("demo", {**project, "keywords": []}, args)
            with self.assertRaises(SystemExit):
                plugin.select("demo", project, SimpleNamespace(selection="1", value=None, quiet=True, state_file=root / ".fat.json"))
            with self.assertRaises(SystemExit):
                plugin.download_command("demo", project, args)

            plugin.search("demo", project, args)
            with self.assertRaises(SystemExit):
                plugin.select("demo", project, SimpleNamespace(selection="unknown", value=None, quiet=True, state_file=root / ".fat.json"))
            plugin.clear_selection("demo", project, args)
            self.assertEqual(plugin.ensure_state(project)["selected_artifact_ids"], [])

    def test_base_setting_parser_and_download_error_are_persisted(self) -> None:
        class FailingPlugin(DummyPlugin):
            def download(self, context, artifact, downloads_dir):
                raise RuntimeError("download failed")

        plugin = FailingPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            args = SimpleNamespace(selection="selected", value=None, quiet=True, state_file=root / ".fat.json")
            plugin.search("demo", project, args)
            plugin.select("demo", project, SimpleNamespace(selection="1", value=None, quiet=True, state_file=root / ".fat.json"))
            plugin.download_command("demo", project, args)
            self.assertEqual(plugin.ensure_state(project)["downloads"][0]["status"], "error")
            with self.assertRaises(SystemExit):
                plugin.set("demo", project, SimpleNamespace(selection="unknown", value="x", quiet=True, state_file=root / ".fat.json"))
            with self.assertRaises(ValueError):
                plugin.set("demo", project, SimpleNamespace(selection="per-query", value="invalid", quiet=True, state_file=root / ".fat.json"))


if __name__ == "__main__":
    unittest.main()
