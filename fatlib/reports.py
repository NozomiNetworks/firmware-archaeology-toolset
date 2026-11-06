from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fatlib.paths import project_output_dir, project_timeout
from plugins.base import escape_markdown, save_state


def project_report_path(project: dict[str, Any]) -> Path:
    output_dir = project_output_dir(project)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "report.md"


def plugin_status_from_state(plugin_state: dict[str, Any]) -> str:
    report_data = plugin_state.get("report_data")
    if isinstance(report_data, dict):
        plugins = report_data.get("plugins")
        if isinstance(plugins, list) and plugins:
            return str(plugins[0].get("status") or "-")
    run_history = plugin_state.get("run_history")
    if isinstance(run_history, list) and run_history:
        return str(run_history[-1].get("status") or "-")
    if plugin_state.get("snapshots"):
        return "ok"
    if plugin_state.get("artifacts"):
        return "ok"
    return "-"


def plugin_settings_lines(plugin_state: dict[str, Any]) -> list[str]:
    fields = [
        ("Downloads dir", plugin_state.get("downloads_dir")),
        ("Parser", plugin_state.get("parser")),
        ("Website", plugin_state.get("website")),
        ("API base", plugin_state.get("api_base")),
        ("Capture policy", plugin_state.get("capture_policy")),
        ("Per query", plugin_state.get("per_query")),
        ("Min keyword hits", plugin_state.get("min_match_keywords")),
        ("Max search requests", plugin_state.get("max_requests")),
        ("Max README enrichments", plugin_state.get("max_enrichments")),
        ("Request delay", plugin_state.get("request_delay")),
        ("Search cache TTL", plugin_state.get("cache_ttl")),
        ("Catalog", plugin_state.get("catalog_path")),
    ]
    lines: list[str] = []
    for label, value in fields:
        if value in (None, "", [], {}):
            continue
        lines.append(f"- {label}: `{escape_markdown(value)}`")
    return lines


def render_search_diagnostics(lines: list[str], plugin_state: dict[str, Any]) -> None:
    """Render the latest source-search accounting and provider errors."""

    report_data = plugin_state.get("report_data")
    if not isinstance(report_data, dict):
        return
    reports = report_data.get("plugins")
    if not isinstance(reports, list) or not reports or not isinstance(reports[0], dict):
        return
    plugin_report = reports[0]
    stats = plugin_report.get("request_stats")
    errors = plugin_report.get("errors")
    pagination = plugin_report.get("pagination")
    if not isinstance(stats, dict) and not errors and not isinstance(pagination, list):
        return

    lines.extend(["", "### Search Diagnostics", ""])
    if isinstance(stats, dict):
        lines.extend(
            [
                f"- Requests: `{escape_markdown(stats.get('requests', '-'))}` / `{escape_markdown(stats.get('max_requests', '-'))}`",
                f"- README enrichments: `{escape_markdown(stats.get('enrichments', '-'))}` / `{escape_markdown(stats.get('max_enrichments', '-'))}`",
                f"- Enrichments skipped: `{escape_markdown(stats.get('enrichments_skipped', 0))}`",
                f"- Cache hits: `{escape_markdown(stats.get('cache_hits', 0))}`",
                f"- Request budget exhausted: `{escape_markdown(stats.get('request_budget_exhausted', False))}`",
            ]
        )
    if isinstance(errors, list) and errors:
        lines.append("- Errors:")
        lines.extend(f"  - {escape_markdown(error)}" for error in errors)
    if isinstance(pagination, list) and pagination:
        lines.append("- Pagination:")
        for entry in pagination:
            if not isinstance(entry, dict):
                continue
            query = escape_markdown(entry.get("query", "-"))
            pages = escape_markdown(entry.get("pages_fetched", 0))
            state = escape_markdown(entry.get("state", "unknown"))
            lines.append(f"  - {query}: {pages} page(s), {state}")


def render_artifacts(lines: list[str], plugin_state: dict[str, Any]) -> None:
    artifacts = plugin_state.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        return
    lines.extend(["", "### Artifacts", "", "| ID | Confidence | Hits | Name | URL |", "| --- | --- | ---: | --- | --- |"])
    for artifact in artifacts:
        lines.append(
            f"| {escape_markdown(artifact.get('artifact_num', '-'))} | {escape_markdown(artifact.get('confidence', '-'))} | "
            f"{len(artifact.get('matched_keywords', []))} | {escape_markdown(artifact.get('name', '-'))} | {escape_markdown(artifact.get('url', '-'))} |"
        )


def render_snapshots(lines: list[str], plugin_state: dict[str, Any]) -> None:
    snapshots = plugin_state.get("snapshots", [])
    if not isinstance(snapshots, list) or not snapshots:
        return
    lines.extend(["", "### Snapshots", "", "| ID | Timestamp | Selected | Firmware links | Selected firmware | Original |", "| --- | --- | --- | ---: | ---: | --- |"])
    for snapshot in snapshots:
        lines.append(
            f"| {escape_markdown(snapshot.get('id', '-'))} | {escape_markdown(snapshot.get('timestamp', '-'))} | "
            f"{'yes' if snapshot.get('selected') else 'no'} | {len(snapshot.get('firmware_links', []))} | "
            f"{len(snapshot.get('selected_firmware_links', []))} | {escape_markdown(snapshot.get('original', '-'))} |"
        )


def render_downloads(lines: list[str], plugin_state: dict[str, Any]) -> None:
    downloads = plugin_state.get("downloads", [])
    if not isinstance(downloads, list) or not downloads:
        return
    lines.extend(["", "### Downloads", "", "| When | Status | Path | URL |", "| --- | --- | --- | --- |"])
    for item in downloads[-20:]:
        when = str(item.get("timestamp") or item.get("snapshot_timestamp") or item.get("capture_timestamp") or "-")
        path = item.get("path") or item.get("local_path") or "-"
        url = item.get("url") or "-"
        lines.append(f"| {escape_markdown(when)} | {escape_markdown(item.get('status', '-'))} | {escape_markdown(path)} | {escape_markdown(url)} |")


def render_run_history(lines: list[str], plugin_state: dict[str, Any]) -> None:
    history = plugin_state.get("run_history", [])
    if not isinstance(history, list) or not history:
        return
    lines.extend(["", "### Recent Runs", "", "| When | Status | Results | Selected |", "| --- | --- | ---: | ---: |"])
    for item in reversed(history[-5:]):
        lines.append(
            f"| {escape_markdown(item.get('completed_at', '-'))} | {escape_markdown(item.get('status', '-'))} | "
            f"{item.get('result_count', 0)} | {item.get('selected_count', 0)} |"
        )


def render_leads(lines: list[str], project: dict[str, Any]) -> None:
    leads = project.get("leads", [])
    if not isinstance(leads, list) or not leads:
        return
    lines.extend(["", "## Leads", "", "| ID | Status | Source | URL | Title | Note |", "| --- | --- | --- | --- | --- | --- |"])
    for lead in leads:
        lines.append(
            f"| {escape_markdown(lead['id'])} | {escape_markdown(lead.get('status', '-'))} | {escape_markdown(lead.get('source', '-'))} | "
            f"{escape_markdown(lead.get('url', '-'))} | {escape_markdown(lead.get('title') or '-')} | {escape_markdown(lead.get('note') or '-')} |"
        )


def render_project_report(project_name: str, project: dict[str, Any], state_file: Path, plugins: list[object]) -> str:
    lines = [
        "# Firmware Archaeology Toolset Report",
        "",
        f"- Project: `{escape_markdown(project_name)}`",
        f"- Generated: `{datetime.now(timezone.utc).isoformat()}`",
        f"- State file: `{escape_markdown(state_file)}`",
        f"- Keywords: {', '.join(escape_markdown(keyword) for keyword in project.get('keywords', [])) or '-'}",
        f"- Output dir: `{escape_markdown(project_output_dir(project))}`",
        f"- Timeout: `{escape_markdown(project_timeout(project))}`",
    ]
    render_leads(lines, project)
    lines.extend([
        "",
        "## Plugin Overview",
        "",
        "| Plugin | Status | Artifacts | Selected | Downloads | Runs |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ])

    for plugin in plugins:
        plugin_state = plugin.ensure_state(project)
        lines.append(
            f"| {escape_markdown(plugin.name)} | {escape_markdown(plugin_status_from_state(plugin_state))} | "
            f"{len(plugin_state.get('artifacts', []))} | {len(plugin_state.get('selected_artifact_ids', []))} | "
            f"{len(plugin_state.get('downloads', []))} | {len(plugin_state.get('run_history', []))} |"
        )

    for plugin in plugins:
        plugin_state = plugin.ensure_state(project)
        lines.extend(["", f"## {escape_markdown(plugin.name)}", ""])
        lines.append(f"- Summary: {escape_markdown(plugin.project_summary(project))}")
        settings = plugin_settings_lines(plugin_state)
        if settings:
            lines.extend(["", "### Settings", "", *settings])
        render_search_diagnostics(lines, plugin_state)
        render_artifacts(lines, plugin_state)
        render_snapshots(lines, plugin_state)
        render_downloads(lines, plugin_state)
        render_run_history(lines, plugin_state)

    return "\n".join(lines) + "\n"


def write_project_report(project_name: str, project: dict[str, Any], state: dict[str, Any], state_file: Path, plugins: list[object]) -> Path:
    report_path = project_report_path(project)
    report_path.write_text(render_project_report(project_name, project, state_file, plugins), encoding="utf-8")
    project["report_path"] = str(report_path)
    project["report_generated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state_file, state)
    return report_path
