from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_OUTPUT_DIR = "output"
DEFAULT_PROJECT_TIMEOUT = 20


def project_output_dir(project: dict[str, Any]) -> Path:
    """Return the effective output directory for one project."""

    return Path(str(project.get("output_dir") or DEFAULT_PROJECT_OUTPUT_DIR))


def project_timeout(project: dict[str, Any]) -> int:
    """Return the effective timeout for one project."""

    return max(1, int(project.get("timeout", DEFAULT_PROJECT_TIMEOUT) or DEFAULT_PROJECT_TIMEOUT))


def project_downloads_dir(project: dict[str, Any], plugin_name: str) -> str:
    """Return the default downloads directory for one plugin within one project."""

    project_name = re.sub(r"[^a-z0-9]+", "-", str(project.get("name", "project")).lower()).strip("-") or "project"
    return str(project_output_dir(project) / "downloads" / project_name / plugin_name)


def resolve_cwd_path(path_text: str) -> Path:
    """Resolve one path relative to the current working directory."""

    path = Path(path_text)
    if path.is_absolute():
        return path
    return Path.cwd() / path
