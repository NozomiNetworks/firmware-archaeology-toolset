from __future__ import annotations

from pathlib import Path
from typing import Any

from fatlib.paths import (
    DEFAULT_PROJECT_OUTPUT_DIR,
    DEFAULT_PROJECT_TIMEOUT,
    project_output_dir,
    resolve_cwd_path,
)
from plugins.base import normalize_slug, parse_keywords


DEFAULT_KEYWORDS = ""
PROJECT_DEFAULTS: dict[str, dict[str, Any]] = {}
UNSET_TOKENS = {"none", "null", '""', "''"}


def ensure_surface_project(state: dict[str, Any], project_name: str) -> dict[str, Any]:
    state.setdefault("current_project", None)
    projects = state.setdefault("surface_projects", {})
    defaults = PROJECT_DEFAULTS.get(project_name, {})
    if project_name not in projects:
        projects[project_name] = {
            "name": project_name,
            "keywords": parse_keywords(defaults.get("keywords", DEFAULT_KEYWORDS)),
            "output_dir": defaults.get("output_dir", DEFAULT_PROJECT_OUTPUT_DIR),
            "timeout": int(defaults.get("timeout", DEFAULT_PROJECT_TIMEOUT) or DEFAULT_PROJECT_TIMEOUT),
            "plugins": {},
            "leads": [],
            "report_path": "",
            "report_generated_at": "",
        }
    project = projects[project_name]
    project.setdefault("name", project_name)
    project.setdefault("keywords", parse_keywords(defaults.get("keywords", DEFAULT_KEYWORDS)))
    project.setdefault("output_dir", defaults.get("output_dir", DEFAULT_PROJECT_OUTPUT_DIR))
    project.setdefault("timeout", int(defaults.get("timeout", DEFAULT_PROJECT_TIMEOUT) or DEFAULT_PROJECT_TIMEOUT))
    project.setdefault("plugins", {})
    project.setdefault("leads", [])
    project.setdefault("report_path", "")
    project.setdefault("report_generated_at", "")
    return project


def resolve_project_name(state: dict[str, Any], explicit_project: str | None) -> str:
    if explicit_project:
        return explicit_project
    current_project = state.get("current_project")
    if current_project:
        return current_project
    raise SystemExit("No project selected. Use `project <name>` or prefix the command with a project name.")


def set_project_context_values(
    project: dict[str, Any],
    *,
    keywords: str | None = None,
    output_dir: str | None = None,
    timeout: int | None = None,
) -> None:
    if keywords:
        project["keywords"] = parse_keywords(keywords)
    if output_dir:
        project["output_dir"] = output_dir
    if timeout is not None:
        project["timeout"] = max(1, int(timeout))


def update_project_setting(project: dict[str, Any], setting: str, value: str) -> tuple[str, str]:
    normalized = setting.replace("_", "-").lower()
    reset_requested = value.strip().lower() in UNSET_TOKENS
    if normalized == "keywords":
        project["keywords"] = [] if reset_requested else parse_keywords(value)
        return ("Keywords", ", ".join(project["keywords"]) or "-")
    if normalized == "output-dir":
        project["output_dir"] = DEFAULT_PROJECT_OUTPUT_DIR if reset_requested else value
        return ("Output dir", str(project_output_dir(project)))
    if normalized == "timeout":
        project["timeout"] = DEFAULT_PROJECT_TIMEOUT if reset_requested else max(1, int(value))
        return ("Timeout", str(project["timeout"]))
    raise SystemExit("Unsupported project setting. Use `keywords`, `output-dir`, or `timeout`.")
def project_download_roots(project_name: str, project: dict[str, Any], plugins: list[object]) -> list[Path]:
    roots: list[Path] = []
    roots.append(resolve_cwd_path(str(project_output_dir(project) / "downloads" / normalize_slug(project_name))))
    for plugin in plugins:
        plugin_state = plugin.ensure_state(project)
        downloads_dir = str(plugin_state.get("downloads_dir") or "").strip()
        if downloads_dir:
            roots.append(resolve_cwd_path(downloads_dir))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root.absolute())
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique
