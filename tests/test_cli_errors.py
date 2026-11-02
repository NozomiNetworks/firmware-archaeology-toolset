from __future__ import annotations

import io
import urllib.error
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import fat


class CliErrorHandlingTests(unittest.TestCase):
    def test_cli_argument_and_error_helpers(self) -> None:
        self.assertTrue(fat.is_help_flag("--help"))
        self.assertFalse(fat.is_help_flag("status"))
        state_path = Path("/tmp/state.json")
        self.assertEqual(fat.resolve_state_file_from_argv(["status"]), fat.STATE_FILE)
        self.assertEqual(fat.resolve_state_file_from_argv(["--state-file", str(state_path)]), state_path)
        self.assertEqual(fat.strip_global_options(["status", "--state-file", "x"]), ["status"])
        self.assertEqual(
            fat.normalize_global_options(["--state-file", "x", "status"]),
            ["status", "--state-file", "x"],
        )
        self.assertEqual(fat.split_project_and_command(["demo", "status"]), ("demo", ["status"]))
        self.assertEqual(fat.split_project_and_command(["github", "search"]), (None, ["github", "search"]))
        with self.assertRaises(SystemExit):
            fat.resolve_state_file_from_argv(["--state-file"])
        with self.assertRaises(SystemExit):
            fat.strip_global_options(["--state-file"])
        self.assertEqual(fat.format_fatal_error(ValueError()), "ValueError")

    def test_cli_run_main_project_context_status_and_help(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            output = io.StringIO()
            with patch.object(fat.sys, "argv", ["fat.py", "project", "demo", "--keywords", "sdk", "--state-file", str(state_file)]), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(fat.sys, "argv", ["fat.py", "status", "--state-file", str(state_file)]), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(fat.sys, "argv", ["fat.py", "demo", "status", "--state-file", str(state_file)]), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(fat.sys, "argv", ["fat.py", "--help"]), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(
                fat.sys,
                "argv",
                ["fat.py", "--state-file", str(state_file), "status"],
            ), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(fat.sys, "argv", ["fat.py", "project", "--help"]), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(
                fat.sys,
                "argv",
                ["fat.py", "project", "set", "timeout", "30", "--state-file", str(state_file)],
            ), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            with patch.object(
                fat.sys,
                "argv",
                ["fat.py", "set", "keywords", "sdk,firmware", "--state-file", str(state_file)],
            ), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
            self.assertIn("Projects", output.getvalue())

    def test_logo_is_printed_without_ansi_when_output_is_not_a_tty(self) -> None:
        output = io.StringIO()
        with patch.object(fat.sys, "argv", ["fat.py", "--help"]), redirect_stdout(output):
            self.assertEqual(fat.run_main(), 0)
        self.assertIn(fat.load_logo().splitlines()[0].split()[0], output.getvalue())
        self.assertNotIn("\033[", output.getvalue())

    def test_logo_uses_terminal_colors_when_enabled(self) -> None:
        output = io.StringIO()
        with patch.object(fat.sys, "argv", ["fat.py", "--help"]), patch(
            "plugins.base.color_enabled", return_value=True
        ), redirect_stdout(output):
            self.assertEqual(fat.run_main(), 0)
        self.assertIn("\033[1m", output.getvalue())
        self.assertIn("\033[95m", output.getvalue())

    def test_logo_is_loaded_from_the_configured_file(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            logo_path = Path(temp_dir) / "logo.txt"
            logo_path.write_text("custom logo\n", encoding="utf-8")
            with patch.object(fat, "TOOL_LOGO_PATH", logo_path), patch.object(
                fat.sys, "argv", ["fat.py", "--help"]
            ), redirect_stdout(output):
                self.assertEqual(fat.run_main(), 0)
        self.assertIn("custom logo", output.getvalue())
        self.assertNotIn("▄▖", output.getvalue())

    def test_cli_project_parsers_and_project_management_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_file = root / ".fat.json"
            set_args = fat.parse_set_args(["demo", "--keywords", "sdk", "--output-dir", str(root / "out"), "--timeout", "12", "--state-file", str(state_file)])
            self.assertEqual(fat.set_project_context(set_args), 0)
            setting_args = fat.parse_project_setting_args(["keywords", "sdk,portal", "--state-file", str(state_file)])
            self.assertEqual(fat.set_project_setting(setting_args), 0)
            delete_args = fat.parse_project_delete_args(["demo", "--delete-downloads", "no", "--state-file", str(state_file)])
            self.assertEqual(fat.delete_project(delete_args), 0)
            self.assertEqual(fat.parse_project_delete_args(["--delete-downloads", "yes"]).delete_downloads, "yes")

    def test_cli_dispatch_and_fatal_error_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / ".fat.json"
            state = fat.ensure_state_shape({"current_project": "demo", "surface_projects": {"demo": {"name": "demo", "keywords": [], "output_dir": temp_dir, "timeout": 20, "plugins": {}}}})
            self.assertEqual(fat.handle_root_status(state, state_file), 0)
            self.assertEqual(fat.handle_project_status("demo", state, state_file), 0)
            with self.assertRaises(SystemExit):
                fat.dispatch_tool("missing", None, ["missing"], state)
        self.assertIn("Rate limited", fat.format_fatal_error(urllib.error.HTTPError("url", 429, "x", {}, None)))
    def test_main_handles_keyboard_interrupt_cleanly(self) -> None:
        output = io.StringIO()
        with patch.object(fat, "run_main", side_effect=KeyboardInterrupt):
            with redirect_stdout(output):
                result = fat.main()

        self.assertEqual(result, 130)
        self.assertIn("Interrupted.", output.getvalue())

    def test_main_handles_timeout_cleanly(self) -> None:
        output = io.StringIO()
        with patch.object(fat, "run_main", side_effect=socket.timeout("timed out")):
            with redirect_stdout(output):
                result = fat.main()

        self.assertEqual(result, 1)
        self.assertIn("Timed out:", output.getvalue())
        self.assertIn("timed out", output.getvalue())

    def test_main_handles_http_429_cleanly(self) -> None:
        output = io.StringIO()
        error = urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", {}, None)
        with patch.object(fat, "run_main", side_effect=error):
            with redirect_stdout(output):
                result = fat.main()

        self.assertEqual(result, 1)
        self.assertIn("Rate limited", output.getvalue())
        self.assertIn("429", output.getvalue())


if __name__ == "__main__":
    unittest.main()
