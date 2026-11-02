from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import plugins.wayback as wayback
from plugins.wayback import WaybackPlugin
from plugins.wayback.parsers import httpbin


class WaybackPluginTests(unittest.TestCase):
    def make_project(self, root: Path) -> dict:
        return {
            "name": "demo",
            "keywords": [],
            "output_dir": str(root / "output"),
            "timeout": 15,
            "plugins": {},
            "report_path": "",
            "report_generated_at": "",
        }

    def test_httpbin_parser_extracts_archived_bytes_endpoint(self) -> None:
        html = """
        <html><body>
          <a href="/anything">JSON</a>
          <a href="https://httpbin.org/bytes/32">bytes</a>
          <a href="https://httpbin.org/bytes/64">other bytes</a>
          <a href="https://example.test/bytes/32">other host</a>
        </body></html>
        """

        links = httpbin.extract_links(
            {"original": "https://httpbin.org/"},
            html,
            archive=None,
        )

        self.assertEqual(links, ["https://httpbin.org/bytes/32"])

    def test_set_downloads_dir_reset_and_capture_policy(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)

            custom_args = SimpleNamespace(selection="downloads-dir", value=str(root / "custom"), quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, custom_args), 0)
            self.assertEqual(plugin.ensure_state(project)["downloads_dir"], str(root / "custom"))

            reset_args = SimpleNamespace(selection="downloads-dir", value="None", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, reset_args), 0)
            self.assertEqual(
                plugin.ensure_state(project)["downloads_dir"],
                str(Path(project["output_dir"]) / "downloads" / "demo" / "wayback"),
            )

            policy_args = SimpleNamespace(selection="capture-policy", value="strict", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, policy_args), 0)
            self.assertEqual(plugin.ensure_state(project)["capture_policy"], "strict")

            keywords_args = SimpleNamespace(selection="keywords", value="firmware, product-slug", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, keywords_args), 0)
            self.assertEqual(plugin.ensure_state(project)["search_keywords"], ["firmware", "product-slug"])

            target_args = SimpleNamespace(selection="target-url", value="https://vendor.example/download", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, target_args), 0)
            self.assertEqual(plugin.ensure_state(project)["target_url"], "https://vendor.example/download")
            self.assertEqual(plugin.ensure_state(project)["search_keywords"], [])

            type_args = SimpleNamespace(selection="target-type", value="file", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, type_args), 0)
            self.assertEqual(plugin.ensure_state(project)["target_type"], "file")

            domain_args = SimpleNamespace(selection="domain", value="www.vendor.example", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, domain_args), 0)
            self.assertEqual(plugin.ensure_state(project)["search_domain"], "www.vendor.example")
            self.assertIsNone(plugin.ensure_state(project)["target_url"])

            filetypes_args = SimpleNamespace(selection="filetypes", value="include=text/html;exclude=image/*", quiet=True, state_file=root / ".fat.json")
            self.assertEqual(plugin.set("demo", project, filetypes_args), 0)
            self.assertEqual(plugin.ensure_state(project)["search_filetypes"], {"include": ["text/html"], "exclude": ["image/*"]})

    def test_clear_wayback_state_removes_downloaded_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads_dir = root / "downloads"
            child_dir = downloads_dir / "snapshot-1"
            child_file = downloads_dir / "note.txt"
            child_dir.mkdir(parents=True, exist_ok=True)
            child_file.write_text("note", encoding="utf-8")
            (child_dir / "firmware.zip").write_text("fw", encoding="utf-8")

            state = {
                "downloads_dir": str(downloads_dir),
                "debug_dir": str(root / "debug"),
                "targets": ["https://example.test/download"],
                "snapshots": [{"id": 1}],
                "downloads": [{"path": "x"}],
            }
            removed = wayback.clear_wayback_state(state, remove_files=True)

            self.assertEqual(state["snapshots"], [])
            self.assertEqual(state["downloads"], [])
            self.assertFalse(child_dir.exists())
            self.assertFalse(child_file.exists())
            self.assertEqual({path.name for path in removed}, {"snapshot-1", "note.txt"})

    def test_domain_keyword_selection_promotes_candidate_url(self) -> None:
        project = {
            "parser": "fake", "search_domain": "vendor.example", "search_keywords": ["firmware"],
            "search_filetypes": {"include": ["text/html"], "exclude": []}, "snapshots": [], "downloads": [],
        }

        class FakeArchive:
            def domain_keyword_captures(self, domain):
                from plugins.wayback.retrieve_archived_firmware import Capture
                return [Capture("20240101010101", "https://vendor.example/new/firmware", "200", "text/html")]

        wayback.workflow.enum_snapshots(project, FakeArchive())

        self.assertEqual(wayback.workflow.select_snapshots(project, "1"), [1])
        self.assertEqual(project["active_target_url"], "https://vendor.example/new/firmware")

    def test_domain_keyword_filetype_filter_excludes_images(self) -> None:
        project = {
            "search_domain": "vendor.example", "search_keywords": ["firmware"],
            "search_filetypes": {"include": ["text/html"], "exclude": ["image/*"]},
            "snapshots": [], "downloads": [],
        }

        class FakeArchive:
            def domain_keyword_captures(self, domain):
                from plugins.wayback.retrieve_archived_firmware import Capture
                return [
                    Capture("20240101010101", "https://vendor.example/firmware", "200", "text/html"),
                    Capture("20240101010102", "https://vendor.example/firmware.png", "200", "image/png"),
                ]

        wayback.workflow.enum_snapshots(project, FakeArchive())
        self.assertEqual([item["original"] for item in project["snapshots"]], ["https://vendor.example/firmware"])

    def test_direct_file_target_bypasses_parser(self) -> None:
        class FakeArchive:
            def exact_file_captures(self, url):
                from plugins.wayback.retrieve_archived_firmware import Capture
                return [Capture("20240101010101", url, "200", "application/zip")]

        project = {"target_url": "https://vendor.example/fw.zip", "target_type": "file", "snapshots": [], "downloads": []}
        wayback.workflow.enum_snapshots(project, FakeArchive())
        self.assertEqual(project["snapshots"][0]["target_type"], "file")
        self.assertEqual(project["snapshots"][0]["firmware_links"], ["https://vendor.example/fw.zip"])

    def test_empty_parser_result_saves_debug_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            class FakeArchive:
                def log(self, message: str) -> None:
                    return None

                def fetch_snapshot_html(self, capture) -> str:
                    return "<html>historical layout</html>"

            class EmptyParser:
                @staticmethod
                def extract_links(snapshot, html, archive):
                    return []

            project = {
                "name": "demo", "output_dir": temp_dir, "parser": "fake",
                "snapshots": [{"id": 7, "target_type": "page", "timestamp": "20240101010101",
                               "original": "https://vendor.example/download", "statuscode": "200",
                               "mimetype": "text/html", "selected": True, "firmware_links": [],
                               "selected_firmware_links": []}],
            }
            with patch("plugins.wayback.workflow.require_parser", return_value="fake"), patch(
                "plugins.wayback.workflow.load_parser", return_value=EmptyParser()
            ):
                wayback.workflow.enum_firmware(project, "selected", FakeArchive())

            debug_path = Path(project["snapshots"][0]["debug_html_path"])
            self.assertTrue(debug_path.exists())
            self.assertEqual(debug_path.read_text(encoding="utf-8"), "<html>historical layout</html>")

    def test_enum_firmware_populates_links_from_parser(self) -> None:
        class FakeArchive:
            def log(self, message: str) -> None:
                return None

            def fetch_snapshot_html(self, capture) -> str:
                return "<html>snapshot</html>"

        class FakeParser:
            @staticmethod
            def extract_links(snapshot, html, archive):
                return ["https://vendor.example/fw/one.zip", "https://vendor.example/fw/two.zip"]

        project = {
            "parser": "fake",
            "downloads_dir": "output/downloads/demo/wayback",
            "targets": ["https://vendor.example/download"],
            "snapshots": [
                {
                    "id": 1,
                    "target_url": "https://vendor.example/download",
                    "timestamp": "20240101010101",
                    "original": "https://vendor.example/download",
                    "statuscode": "200",
                    "mimetype": "text/html",
                    "selected": True,
                    "selected_firmware_links": [],
                    "firmware_links": [],
                }
            ],
            "downloads": [],
        }

        with patch("plugins.wayback.workflow.require_parser", return_value="fake"), patch(
            "plugins.wayback.workflow.load_parser", return_value=FakeParser()
        ):
            wayback.enum_firmware(project, "selected", FakeArchive())

        self.assertEqual(
            project["snapshots"][0]["firmware_links"],
            ["https://vendor.example/fw/one.zip", "https://vendor.example/fw/two.zip"],
        )

    def test_enum_firmware_uses_parser_render_settings_for_archived_html(self) -> None:
        class FakeArchive:
            def __init__(self) -> None:
                self.render_calls = []

            def log(self, message: str) -> None:
                return None

            def fetch_snapshot_html_rendered(self, capture, **kwargs) -> str:
                self.render_calls.append((capture, kwargs))
                return "<html>rendered snapshot</html>"

        class RenderedParser:
            RENDER_MODE = "required"
            RENDER_WAIT_UNTIL = "load"
            RENDER_WAIT_FOR = ".download"
            RENDER_TIMEOUT = 11

            @staticmethod
            def extract_links(snapshot, html, archive):
                self.assertEqual(html, "<html>rendered snapshot</html>")
                return ["https://vendor.example/fw/rendered.zip"]

        archive = FakeArchive()
        project = {
            "parser": "rendered",
            "timeout": 5,
            "snapshots": [{
                "id": 4, "target_url": "https://vendor.example/download",
                "timestamp": "20240101010101", "original": "https://vendor.example/download",
                "statuscode": "200", "mimetype": "text/html", "selected": True,
                "selected_firmware_links": [], "firmware_links": [],
            }],
        }
        with patch("plugins.wayback.workflow.require_parser", return_value="rendered"), patch(
            "plugins.wayback.workflow.load_parser", return_value=RenderedParser()
        ):
            wayback.enum_firmware(project, "selected", archive)

        self.assertEqual(project["snapshots"][0]["firmware_links"], ["https://vendor.example/fw/rendered.zip"])
        self.assertEqual(archive.render_calls[0][1], {
            "mode": "required", "wait_until": "load", "wait_for": ".download", "timeout": 11,
        })

    def test_enum_firmware_warns_when_parser_finds_no_links(self) -> None:
        class FakeArchive:
            def log(self, message: str) -> None:
                return None

            def fetch_snapshot_html(self, capture) -> str:
                return "<html>old layout</html>"

        class EmptyParser:
            @staticmethod
            def extract_links(snapshot, html, archive):
                return []

        project = {
            "parser": "fake",
            "snapshots": [{
                "id": 3, "target_url": "https://vendor.example/new/download",
                "timestamp": "20240101010101", "original": "https://vendor.example/new/download",
                "statuscode": "200", "mimetype": "text/html", "selected": True,
                "selected_firmware_links": [], "firmware_links": [],
            }],
        }
        with patch("plugins.wayback.workflow.require_parser", return_value="fake"), patch(
            "plugins.wayback.workflow.load_parser", return_value=EmptyParser()
        ), redirect_stdout(io.StringIO()) as output:
            wayback.enum_firmware(project, "selected", FakeArchive())

        self.assertIn("review the vendor parser", output.getvalue())

    def test_download_firmware_prefers_live_rewritten_url_when_update_hook_exists(self) -> None:
        class FakeArchive:
            def __init__(self) -> None:
                self.streamed: list[tuple[str, Path]] = []
                self.downloaded: list[tuple[str, Path]] = []

            def log(self, message: str) -> None:
                return None

            def http_stream(self, url: str, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("live", encoding="utf-8")
                self.streamed.append((url, destination))

            def closest_page_capture(self, url: str, timestamp: str, *, policy: str = "nearest"):
                raise AssertionError("archive capture path should not be used when update hook returns a live URL")

            def download_capture(self, capture, destination: Path) -> Path:
                self.downloaded.append((capture.timestamp, destination))
                raise AssertionError("download_capture should not be called for rewritten live URLs")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = FakeArchive()
            project = {
                "parser": "fake",
                "downloads_dir": str(root / "downloads"),
                "capture_policy": "nearest",
                "targets": ["https://vendor.example/download"],
                "snapshots": [
                    {
                        "id": 1,
                        "target_url": "https://vendor.example/download",
                        "timestamp": "20240101010101",
                        "original": "https://vendor.example/download",
                        "statuscode": "200",
                        "mimetype": "text/html",
                        "selected": True,
                        "selected_firmware_links": ["https://old.vendor.example/fw/app.zip"],
                        "firmware_links": ["https://old.vendor.example/fw/app.zip"],
                    }
                ],
                "downloads": [],
            }

            with patch("plugins.wayback.workflow.load_update_hook", return_value=lambda project, snapshot, url, archive: "https://live.vendor.example/fw/app.zip"), patch(
                "plugins.wayback.workflow.load_post_hook", return_value=None
            ):
                wayback.download_firmware(project, "selected", archive)

            self.assertEqual(len(archive.streamed), 1)
            self.assertEqual(archive.streamed[0][0], "https://live.vendor.example/fw/app.zip")
            self.assertEqual(len(project["downloads"]), 1)
            self.assertEqual(project["downloads"][0]["download_source"], "live")
            self.assertEqual(project["downloads"][0]["resolved_url"], "https://live.vendor.example/fw/app.zip")
            self.assertEqual(project["downloads"][0]["status"], "downloaded")
            self.assertTrue(Path(project["downloads"][0]["local_path"]).exists())

    def test_wayback_plugin_builds_archive_with_project_timeout_and_api_base(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            tool_state = plugin.ensure_state(project)
            tool_state["api_base"] = "https://archive.example.test"
            args = SimpleNamespace(selection="snapshot", value="", quiet=True, state_file=root / ".fat.json")

            with patch("plugins.wayback.ArchiveClient") as archive_cls, patch(
                "plugins.wayback.enum_snapshots"
            ) as enum_snapshots:
                plugin.search("demo", project, args)

            archive_cls.assert_called_once_with(
                sleep=1.0,
                verbose=False,
                verbose_http=False,
                api_base="https://archive.example.test",
                timeout=15,
            )
            enum_snapshots.assert_called_once()

    def test_download_firmware_rejects_untrusted_capture_timestamp_path(self) -> None:
        class FakeArchive:
            def log(self, message: str) -> None:
                return None

            def closest_page_capture(self, url: str, timestamp: str, *, policy: str = "nearest"):
                return SimpleNamespace(timestamp="../pwned", original="https://vendor.example/fw/app.zip")

            def download_capture(self, capture, destination: Path) -> Path:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("firmware", encoding="utf-8")
                return destination

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads_dir = root / "downloads"
            project = {
                "parser": "fake",
                "downloads_dir": str(downloads_dir),
                "capture_policy": "nearest",
                "snapshots": [
                    {
                        "id": 1,
                        "timestamp": "20240101000000",
                        "target_type": "page",
                        "original": "https://vendor.example/download",
                        "statuscode": "200",
                        "selected": True,
                        "selected_firmware_links": [],
                        "firmware_links": ["https://vendor.example/fw/app.zip"],
                    }
                ],
                "downloads": [],
            }
            with patch("plugins.wayback.workflow.load_update_hook", return_value=None), patch(
                "plugins.wayback.workflow.load_post_hook", return_value=None
            ):
                with self.assertRaises(ValueError):
                    wayback.download_firmware(project, "selected", FakeArchive())

            self.assertFalse((root / "pwned_app.zip").exists())
            self.assertEqual(project["downloads"], [])

    def test_download_firmware_rejects_untrusted_snapshot_timestamp_for_live_download(self) -> None:
        class FakeArchive:
            def log(self, message: str) -> None:
                return None

            def http_stream(self, url: str, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("firmware", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = {
                "parser": "fake",
                "downloads_dir": str(root / "downloads"),
                "snapshots": [
                    {
                        "id": 1,
                        "timestamp": "../pwned",
                        "target_type": "page",
                        "original": "https://vendor.example/download",
                        "statuscode": "200",
                        "selected": True,
                        "selected_firmware_links": [],
                        "firmware_links": ["https://vendor.example/fw/app.zip"],
                    }
                ],
                "downloads": [],
            }
            with patch(
                "plugins.wayback.workflow.load_update_hook",
                return_value=lambda project, snapshot, url, archive: "https://vendor.example/live/app.zip",
            ), patch("plugins.wayback.workflow.load_post_hook", return_value=None):
                with self.assertRaises(ValueError):
                    wayback.download_firmware(project, "selected", FakeArchive())

            self.assertFalse((root / "pwned_app.zip").exists())
            self.assertEqual(project["downloads"], [])

    def test_wayback_plugin_download_command_dispatches_with_built_archive(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            args = SimpleNamespace(selection="selected", value="", quiet=True, state_file=root / ".fat.json")

            with patch("plugins.wayback.ArchiveClient", return_value="archive") as archive_cls, patch(
                "plugins.wayback.download_firmware"
            ) as download_firmware:
                plugin.download_command("demo", project, args)

            archive_cls.assert_called_once()
            download_firmware.assert_called_once_with(plugin.ensure_state(project), "selected", "archive")

    def test_wayback_plugin_status_renders_core_fields(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            state = plugin.ensure_state(project)
            state["parser"] = "examplevendor"
            state["targets"] = ["https://vendor.example/downloads"]
            state["snapshots"] = [
                {
                    "id": 1,
                    "target_url": "https://vendor.example/downloads",
                    "timestamp": "20240101010101",
                    "original": "https://vendor.example/downloads",
                    "selected": True,
                    "selected_firmware_links": ["https://vendor.example/fw/app.zip"],
                    "firmware_links": ["https://vendor.example/fw/app.zip"],
                }
            ]
            args = SimpleNamespace(selection="selected", value="", quiet=True, state_file=root / ".fat.json")

            output = io.StringIO()
            with redirect_stdout(output), patch("plugins.wayback.load_update_hook", return_value=None), patch(
                "plugins.wayback.load_post_hook", return_value=None
            ):
                self.assertEqual(plugin.status("demo", project, args), 0)

            rendered = output.getvalue()
            self.assertIn("Wayback project status", rendered)
            self.assertIn("examplevendor", rendered)
            self.assertIn("Selected firmware", rendered)
            self.assertIn("app.zip", rendered)

    def test_wayback_options_reflect_custom_settings(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            project = self.make_project(root)
            state = plugin.ensure_state(project)
            state["parser"] = "examplevendor"
            state["api_base"] = "https://archive.example.test"
            state["capture_policy"] = "strict"
            args = SimpleNamespace(selection="selected", value="", quiet=True, state_file=state_file)

            output = io.StringIO()
            with redirect_stdout(output), patch("plugins.wayback.load_post_hook", return_value=None):
                self.assertEqual(plugin.options("demo", project, args), 0)

            rendered = output.getvalue()
            self.assertIn("examplevendor", rendered)
            self.assertIn("https://archive.example.test", rendered)
            self.assertIn("strict", rendered)
            self.assertIn("Target URL", rendered)

    def test_wayback_validate_args_enforces_custom_command_shapes(self) -> None:
        plugin = WaybackPlugin()

        plugin.validate_args(SimpleNamespace(command="status", selection="selected", value=""))
        plugin.validate_args(SimpleNamespace(command="search", selection="snapshot", value=""))
        plugin.validate_args(SimpleNamespace(command="search", selection="firmware", value=""))
        plugin.validate_args(SimpleNamespace(command="select", selection="1,2", value=""))
        plugin.validate_args(SimpleNamespace(command="select", selection="2:1,2:2", value=""))
        plugin.validate_args(SimpleNamespace(command="download", selection="all", value=""))
        plugin.validate_args(SimpleNamespace(command="post", selection="selected", value=""))
        plugin.validate_args(SimpleNamespace(command="set", selection="parser", value="examplevendor"))

        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="status", selection="1", value=""))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="search", selection="selected", value=""))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="search", selection="snapshot", value="extra"))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="select", selection="selected", value=""))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="clear-selection", selection="1", value=""))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="download", selection="selected", value="extra"))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="post", selection="selected", value="extra"))
        with self.assertRaises(SystemExit):
            plugin.validate_args(SimpleNamespace(command="set", selection="selected", value=""))

    def test_wayback_setting_parsers_cover_valid_and_invalid_values(self) -> None:
        plugin = WaybackPlugin()
        self.assertEqual(plugin.parse_capture_policy(" STRICT "), "strict")
        self.assertEqual(plugin.parse_target_url(" https://example.test/page "), "https://example.test/page")
        self.assertEqual(plugin.parse_target_type("PAGE"), "page")
        self.assertEqual(plugin.parse_domain("https://example.test/"), "example.test")
        self.assertEqual(plugin.parse_filetypes("include=text/html;exclude=image/*"), {"include": ["text/html"], "exclude": ["image/*"]})
        self.assertEqual(plugin.render_filetypes({"include": [], "exclude": []}), "include=*;exclude=-")
        self.assertEqual(plugin.parse_parser_value("httpbin"), "httpbin")
        for parser, value in [
            (plugin.parse_capture_policy, "bad"),
            (plugin.parse_target_url, "relative/path"),
            (plugin.parse_target_type, "binary"),
            (plugin.parse_domain, "https://example.test/path"),
            (plugin.parse_filetypes, "image/*"),
            (plugin.parse_filetypes, "include=;exclude="),
        ]:
            with self.assertRaises(SystemExit):
                parser(value)

    def test_wayback_search_and_dispatch_commands(self) -> None:
        plugin = WaybackPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = self.make_project(root)
            state = plugin.ensure_state(project)
            state["target_url"] = "https://example.test/page"
            args = SimpleNamespace(selection="snapshot", value="", quiet=True, state_file=root / ".fat.json")
            with patch("plugins.wayback.enum_snapshots") as enum_mock:
                self.assertEqual(plugin.search("demo", project, args), 0)
            enum_mock.assert_called_once()

            args.selection = "firmware"
            with patch("plugins.wayback.enum_firmware") as firmware_mock:
                self.assertEqual(plugin.search("demo", project, args), 0)
            firmware_mock.assert_called_once()

            args.selection = "bad"
            with self.assertRaises(SystemExit):
                plugin.search("demo", project, args)

    def test_wayback_top_level_selection_and_clear_wrappers(self) -> None:
        project = {
            "snapshots": [{"id": 1, "timestamp": "20240101000000", "original": "https://x/page", "target_type": "page", "selected": True, "firmware_links": ["https://x/fw.zip"], "selected_firmware_links": []}],
            "downloads": [],
        }
        with redirect_stdout(io.StringIO()):
            wayback.select_snapshots(project, "1")
            wayback.select_firmware(project, "1:1")
            wayback.clear_firmware_selection(project)
            wayback.clear_selection(project)
        self.assertFalse(project["snapshots"][0]["selected"])
        self.assertEqual(project["snapshots"][0]["selected_firmware_links"], [])

    def test_wayback_workflow_selection_and_normalization_helpers(self) -> None:
        snapshots = [
            {"id": 1, "selected": True, "firmware_links": ["https://x/a.zip"], "selected_firmware_links": []},
            {"id": 2, "selected": False, "firmware_links": ["https://x/b.zip"], "selected_firmware_links": ["https://x/b.zip"]},
        ]
        self.assertEqual([item["id"] for item in wayback.workflow.parse_selection("selected", snapshots)], [1])
        snapshots[0]["selected"] = False
        self.assertEqual([item["id"] for item in wayback.workflow.parse_selection("selected", snapshots)], [2])
        self.assertEqual([item["id"] for item in wayback.workflow.parse_selection("all", snapshots)], [1, 2])
        self.assertEqual(wayback.workflow.firmware_name("https://x/a.zip"), "a.zip")
        self.assertEqual(wayback.workflow.normalize_post_result({"ok": True}), {"ok": True})

        class Result:
            def to_dict(self):
                return {"ok": True}

        self.assertEqual(wayback.workflow.normalize_post_result(Result()), {"ok": True})
        with self.assertRaises(TypeError):
            wayback.workflow.normalize_post_result(object())

    def test_wayback_resolves_existing_download_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            downloads = root / "downloads"
            downloads.mkdir()
            direct = root / "direct.bin"
            direct.write_bytes(b"x")
            project = {"downloads_dir": str(downloads)}
            entry = {"local_path": str(direct)}
            self.assertEqual(wayback.workflow.resolve_existing_local_path(project, entry), direct)
            entry = {"local_path": "old.bin"}
            (downloads / "old.bin").write_bytes(b"x")
            self.assertEqual(wayback.workflow.resolve_existing_local_path(project, entry), downloads / "old.bin")
            entry = {"local_path": "prefix.bin"}
            (downloads / "one-prefix.bin").write_bytes(b"x")
            self.assertEqual(wayback.workflow.resolve_existing_local_path(project, entry), downloads / "one-prefix.bin")
            self.assertIsNone(wayback.workflow.resolve_existing_local_path(project, {"local_path": "missing.bin"}))

    def test_wayback_run_post_actions_handles_success_missing_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = Path(temp_dir) / "firmware.bin"
            local_path.write_bytes(b"x")
            project = {
                "downloads_dir": temp_dir,
                "snapshots": [
                    {"id": 1, "target_type": "page", "firmware_links": ["https://x/fw.bin"], "selected_firmware_links": []},
                    {"id": 2, "target_type": "file", "firmware_links": ["https://x/direct.bin"], "selected_firmware_links": []},
                ],
                "downloads": [
                    {"snapshot_id": 1, "url": "https://x/fw.bin", "local_path": str(local_path)},
                    {"snapshot_id": 1, "url": "https://x/missing.bin", "local_path": "missing.bin"},
                    {"snapshot_id": 2, "url": "https://x/direct.bin", "local_path": str(local_path)},
                ],
            }
            class Archive:
                def log(self, message):
                    pass

            with patch("plugins.wayback.workflow.load_post_hook", return_value=lambda **kwargs: {"ok": True}), patch(
                "plugins.wayback.workflow.invoke_post_hook", return_value={"ok": True}
            ):
                self.assertTrue(wayback.workflow.run_post_actions(project, "all", Archive()))
                self.assertEqual(project["downloads"][0]["post_status"], "ok")
                self.assertEqual(project["downloads"][2]["post_status"], "ok")

            with patch("plugins.wayback.workflow.load_post_hook", return_value=lambda **kwargs: {"ok": True}), patch(
                "plugins.wayback.workflow.invoke_post_hook", side_effect=RuntimeError("post failed")
            ):
                project["downloads"][0].pop("post_status", None)
                self.assertFalse(wayback.workflow.run_post_actions(project, "1", Archive()))
            self.assertEqual(project["downloads"][0]["post_status"], "error")
            self.assertIsNone(wayback.workflow.resolve_existing_local_path(project, {}))

    def test_direct_file_download_runs_post_hook(self) -> None:
        class Archive:
            def log(self, message):
                pass

            def download_capture(self, capture, destination):
                destination.write_bytes(b"firmware")

        with tempfile.TemporaryDirectory() as temp_dir:
            project = {
                "name": "demo", "downloads_dir": temp_dir, "downloads": [], "parser": "fake",
                "snapshots": [{"id": 1, "timestamp": "20240101000000", "target_type": "file",
                               "original": "https://x/direct.zip", "statuscode": "200", "mimetype": "application/zip",
                               "selected": True, "selected_firmware_links": [],
                               "firmware_links": ["https://x/direct.zip"]}],
            }
            hook = lambda **kwargs: {"checked": True}
            with patch("plugins.wayback.workflow.load_update_hook", return_value=None), patch(
                "plugins.wayback.workflow.load_post_hook", return_value=hook
            ) as load_hook, patch("plugins.wayback.workflow.invoke_post_hook", return_value={"checked": True}) as invoke:
                wayback.workflow.download_firmware(project, "selected", Archive())
            load_hook.assert_called()
            invoke.assert_called_once()
            self.assertEqual(project["downloads"][0]["post_status"], "ok")
            self.assertEqual(project["downloads"][0]["post_result"], {"checked": True})

    def test_wayback_download_records_missing_and_archive_errors(self) -> None:
        class Archive:
            def log(self, message):
                pass

            def closest_page_capture(self, url, timestamp, *, policy):
                return None if url.endswith("missing.zip") else type("Capture", (), {"timestamp": "20240101000000", "original": url})()

            def download_capture(self, capture, destination):
                raise RuntimeError("archive unavailable")

        project = {
            "name": "demo", "downloads_dir": tempfile.mkdtemp(), "downloads": [],
            "snapshots": [{"id": 1, "timestamp": "20240101000000", "target_type": "page",
                           "selected": True, "selected_firmware_links": [],
                           "firmware_links": ["https://x/missing.zip", "https://x/error.zip"]}],
        }
        with patch("plugins.wayback.workflow.load_update_hook", return_value=None), patch(
            "plugins.wayback.workflow.load_post_hook", return_value=None
        ):
            wayback.workflow.download_firmware(project, "selected", Archive())
        self.assertEqual([entry["status"] for entry in project["downloads"]], ["missing", "error"])

    def test_wayback_post_hook_and_existing_download_are_handled(self) -> None:
        class Archive:
            def log(self, message):
                pass

            def closest_page_capture(self, url, timestamp, *, policy):
                return type("Capture", (), {"timestamp": "20240101000000", "original": url})()

            def download_capture(self, capture, destination):
                destination.write_bytes(b"firmware")

        with tempfile.TemporaryDirectory() as temp_dir:
            project = {
                "name": "demo", "downloads_dir": temp_dir, "downloads": [], "parser": "fake",
                "snapshots": [{"id": 1, "timestamp": "20240101000000", "target_type": "page",
                               "selected": True, "selected_firmware_links": ["https://x/fw.zip"],
                               "firmware_links": ["https://x/fw.zip"]}],
            }
            hook = lambda **kwargs: type("Result", (), {"to_dict": lambda self: {"checked": True}})()
            with patch("plugins.wayback.workflow.load_update_hook", return_value=None), patch(
                "plugins.wayback.workflow.load_post_hook", return_value=hook
            ):
                wayback.workflow.download_firmware(project, "selected", Archive())
                wayback.workflow.download_firmware(project, "selected", Archive())
            self.assertEqual(project["downloads"][0]["post_result"], {"checked": True})
            self.assertEqual(project["downloads"][1]["status"], "exists")


if __name__ == "__main__":
    unittest.main()
