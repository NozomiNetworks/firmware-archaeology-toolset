from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import verify_plugin_api_docs


class PluginApiDocumentationTests(unittest.TestCase):
    def test_committed_reference_satisfies_source_contract(self):
        self.assertEqual([], verify_plugin_api_docs.verify())

    def test_missing_generated_anchor_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            reference_root = Path(temporary_directory)
            source_root = verify_plugin_api_docs.DEFAULT_REFERENCE_ROOT
            for relative_page in verify_plugin_api_docs.DOCUMENTED_MODULES.values():
                destination = reference_root / relative_page
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_root / relative_page, destination)

            page = reference_root / "plugins/base.html"
            page_html = page.read_text(encoding="utf-8")
            page.write_text(
                page_html.replace('href="#PluginContext"', 'href="#MissingPluginContext"')
                .replace('id="PluginContext"', 'id="MissingPluginContext"'),
                encoding="utf-8",
            )

            errors = verify_plugin_api_docs.verify(reference_root)

        self.assertIn(
            "plugins.base.PluginContext: missing generated documentation anchor",
            errors,
        )

    def test_missing_and_empty_pages_are_reported(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            reference_root = Path(temporary_directory)
            empty_page = reference_root / "plugins/base.html"
            empty_page.parent.mkdir(parents=True)
            empty_page.write_text("", encoding="utf-8")

            errors = verify_plugin_api_docs.verify(reference_root)

        self.assertTrue(any("plugins.base: generated page is empty" in error for error in errors))
        self.assertTrue(any("plugins.scraper.base: generated page is missing" in error for error in errors))

    def test_command_line_reports_success_usage_and_failure(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, verify_plugin_api_docs.main([]))
        self.assertIn("Verified documented plugin API", output.getvalue())

        error_output = StringIO()
        with redirect_stderr(error_output):
            self.assertEqual(2, verify_plugin_api_docs.main(["one", "two"]))
        self.assertIn("usage:", error_output.getvalue())

        with tempfile.TemporaryDirectory() as temporary_directory:
            error_output = StringIO()
            with redirect_stderr(error_output):
                result = verify_plugin_api_docs.main([temporary_directory])
        self.assertEqual(1, result)
        self.assertIn("documentation check failed", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
