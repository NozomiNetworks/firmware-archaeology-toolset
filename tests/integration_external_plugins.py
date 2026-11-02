from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_external_plugins import SCRAPER_SOURCE, WAYBACK_SOURCE


class ExternalPluginCliIntegrationTests(unittest.TestCase):
    def test_cli_loads_external_scraper_and_wayback_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plugin_root = root / "plugins"
            scraper_dir = plugin_root / "scraper" / "websites"
            wayback_dir = plugin_root / "wayback" / "parsers"
            scraper_dir.mkdir(parents=True)
            wayback_dir.mkdir(parents=True)
            (scraper_dir / "fixture.py").write_text(SCRAPER_SOURCE, encoding="utf-8")
            (wayback_dir / "fixture.py").write_text(WAYBACK_SOURCE, encoding="utf-8")
            state_file = root / ".fat.json"
            config_file = root / "config.json"
            config_file.write_text(json.dumps({"plugin_dir": str(plugin_root)}), encoding="utf-8")
            commands = [
                ["project", "fixture"],
                ["fixture", "scraper", "set", "parser", "fixture-scraper"],
                ["fixture", "scraper", "options"],
                ["fixture", "wayback", "set", "parser", "fixture"],
                ["fixture", "wayback", "options"],
            ]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            for command in commands:
                result = subprocess.run(
                    [sys.executable, "fat.py", "--plugin-dir", str(plugin_root), "--state-file", str(state_file), *command],
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            rendered = result.stdout
            self.assertIn("Available parsers", rendered)
            self.assertIn("fixture", rendered)
            configured = subprocess.run(
                [sys.executable, "fat.py", "--config-file", str(config_file), "--state-file", str(state_file), "fixture", "wayback", "options"],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(configured.returncode, 0, configured.stderr or configured.stdout)
            self.assertIn("fixture", configured.stdout)


if __name__ == "__main__":
    unittest.main()
