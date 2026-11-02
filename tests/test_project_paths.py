from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fatlib.projects import ensure_surface_project, project_download_roots, update_project_setting
from fatlib.paths import project_downloads_dir, project_output_dir, project_timeout


class DummyPlugin:
    name = "dummy"

    def ensure_state(self, project: dict):
        plugins = project.setdefault("plugins", {})
        plugins.setdefault(
            self.name,
            {
                "downloads_dir": project_downloads_dir(project, self.name),
            },
        )
        return plugins[self.name]


class ProjectPathTests(unittest.TestCase):
    def test_default_project_helpers_follow_project_state(self) -> None:
        state: dict = {}
        project = ensure_surface_project(state, "demo")

        self.assertEqual(str(project_output_dir(project)), "output")
        self.assertEqual(project_timeout(project), 20)
        self.assertEqual(project_downloads_dir(project, "github"), "output/downloads/demo/github")

    def test_project_setting_reset_restores_default_path_values(self) -> None:
        state: dict = {}
        project = ensure_surface_project(state, "demo")
        update_project_setting(project, "output-dir", "/tmp/custom-output")
        update_project_setting(project, "timeout", "47")

        self.assertEqual(str(project_output_dir(project)), "/tmp/custom-output")
        self.assertEqual(project_timeout(project), 47)

        update_project_setting(project, "output-dir", "None")
        update_project_setting(project, "timeout", "None")

        self.assertEqual(str(project_output_dir(project)), "output")
        self.assertEqual(project_timeout(project), 20)
        self.assertEqual(project_downloads_dir(project, "github"), "output/downloads/demo/github")

    def test_project_download_roots_include_default_and_overrides_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state: dict = {}
            project = ensure_surface_project(state, "demo")
            update_project_setting(project, "output-dir", str(root / "out"))
            plugin = DummyPlugin()
            plugin_state = plugin.ensure_state(project)
            plugin_state["downloads_dir"] = str(root / "custom-downloads")

            roots = project_download_roots("demo", project, [plugin])

            self.assertEqual(
                roots,
                [
                    root / "out" / "downloads" / "demo",
                    root / "custom-downloads",
                ],
            )


if __name__ == "__main__":
    unittest.main()
