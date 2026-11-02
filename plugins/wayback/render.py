"""Terminal-oriented rendering helpers for the Wayback workflow."""

from __future__ import annotations

import os
import sys
import urllib.parse
from datetime import datetime
from typing import Any

from ..base import strip_control_chars
from . import workflow


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_CYAN = "\033[36m"


def color_enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def style(text: str, *codes: str) -> str:
    text = strip_control_chars(text)
    if not color_enabled() or not codes:
        return text
    return f"{''.join(codes)}{text}{ANSI_RESET}"


def print_section(title: str) -> None:
    print(style(title, ANSI_BOLD, ANSI_CYAN))


def print_key_value(label: str, value: str) -> None:
    print(f"{style(label + ':', ANSI_BOLD):<24} {strip_control_chars(value)}")


def print_warn(message: str) -> None:
    print(style(f"[!] {message}", ANSI_YELLOW, ANSI_BOLD))


def print_error(message: str) -> None:
    print(style(f"[x] {message}", ANSI_RED, ANSI_BOLD))


def format_timestamp(timestamp: str | None) -> str:
    if not timestamp:
        return "-"
    try:
        return datetime.strptime(timestamp, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return strip_control_chars(timestamp)


def format_status(status: str) -> str:
    status = strip_control_chars(status)
    palette = {
        "downloaded": (ANSI_GREEN, ANSI_BOLD),
        "exists": (ANSI_BLUE,),
        "missing": (ANSI_YELLOW,),
        "error": (ANSI_RED, ANSI_BOLD),
        "skipped": (ANSI_DIM,),
    }
    return style(status, *palette.get(status, ()))


def firmware_summary(snapshot: dict[str, Any], *, max_items: int = 2, max_length: int = 72) -> str:
    names = [strip_control_chars(workflow.firmware_name(url)) for url in snapshot.get("firmware_links", [])]
    if not names:
        return "-"
    shown = names[:max_items]
    summary = ", ".join(shown)
    if len(names) > max_items:
        summary += f" +{len(names) - max_items} more"
    if len(summary) > max_length:
        summary = summary[: max_length - 3] + "..."
    return summary


def short_url(url: str, *, max_length: int = 96) -> str:
    parsed = urllib.parse.urlsplit(url)
    value = parsed.path or "/"
    if parsed.query:
        value += f"?{parsed.query}"
    if len(value) > max_length:
        value = value[: max_length - 3] + "..."
    return strip_control_chars(value)


def print_snapshot_table(snapshots: list[dict[str, Any]]) -> None:
    print(style("Sel  ID   Timestamp            Type  Links  Firmware names                    Target", ANSI_BOLD))
    print(style("---  ---  -------------------  ----  -----  --------------------------------  ------", ANSI_DIM))
    for snapshot in snapshots:
        links = len(snapshot.get("firmware_links", []))
        marker = style("*", ANSI_GREEN, ANSI_BOLD) if snapshot.get("selected") else " "
        link_text = f"{links:>5}"
        link_count = style(link_text, ANSI_GREEN if links else ANSI_DIM)
        fw_summary = style(firmware_summary(snapshot), ANSI_GREEN if links else ANSI_DIM)
        print(
            f" {marker}  {strip_control_chars(snapshot['id']):>3}  {format_timestamp(snapshot['timestamp'])}  "
            f"{strip_control_chars(snapshot.get('target_type', 'page')):<4}  {link_count}  {fw_summary:<32}  {short_url(snapshot['original'])}"
        )
        if snapshot.get("discovery_method") == "domain-keyword":
            matched = strip_control_chars(", ".join(snapshot.get("matched_keywords", []))) or "-"
            print(f"           keyword match: {matched}")
        for firmware in workflow.firmware_rows(snapshot):
            fw_marker = style("*", ANSI_GREEN, ANSI_BOLD) if firmware["selected"] else " "
            fw_name = style(strip_control_chars(firmware["name"]), ANSI_GREEN if firmware["selected"] else ANSI_DIM)
            print(f"      {fw_marker}  {strip_control_chars(firmware['id']):<6} {'firmware':<19}  {fw_name}")


def print_download_table(downloads: list[dict[str, Any]]) -> None:
    print(style("ID   Status       Capture time          URL", ANSI_BOLD))
    print(style("---  -----------  -------------------  ---", ANSI_DIM))
    for item in downloads:
        status_text = f"{item['status']:<11}"
        print(f"{strip_control_chars(item['snapshot_id']):>3}  {format_status(status_text)}  {format_timestamp(item.get('capture_timestamp')):<19}  {strip_control_chars(item['url'])}")


def show_status(
    project_name: str,
    project: dict[str, Any],
    *,
    has_update_hook: bool,
    has_post_hook: bool,
) -> None:
    snapshots = project.get("snapshots", [])
    downloads = project.get("downloads", [])
    print_section("Wayback project status")
    print_key_value("Project", project_name)
    print_key_value("Parser", str(project.get("parser")))
    print_key_value("Target URL", str(project.get("active_target_url") or project.get("target_url") or "-"))
    print_key_value("Target type", str(project.get("target_type") or "auto"))
    print_key_value("Downloads dir", str(project.get("downloads_dir") or "-"))
    print_key_value("Capture policy", str(project.get("capture_policy") or "nearest"))
    print_key_value("Update hook", "present" if has_update_hook else "-")
    print_key_value("Post hook", "present" if has_post_hook else "-")
    print_key_value("Snapshots", str(len(snapshots)))
    print_key_value("Selected snapshots", str(sum(1 for item in snapshots if item.get("selected"))))
    print_key_value("Selected firmware", str(sum(len(item.get("selected_firmware_links", [])) for item in snapshots)))
    print_key_value("Snapshots with links", str(sum(1 for item in snapshots if item.get("firmware_links"))))
    print_key_value("Downloads logged", str(len(downloads)))
    if snapshots:
        print("")
        print_section("Snapshots")
        print_snapshot_table(snapshots)
    if not downloads:
        return
    print("")
    print_section("Recent downloads")
    print_download_table(downloads[-10:])
    rendered_post_keys: set[tuple[Any, Any]] = set()
    for item in reversed(downloads[-10:]):
        post_result = item.get("post_result")
        post_key = (item.get("snapshot_id"), item.get("url"))
        if item.get("post_status") == "ok" and isinstance(post_result, dict):
            if post_key in rendered_post_keys:
                continue
            rendered_post_keys.add(post_key)
            print("")
            print(style(f"Snapshot {item.get('snapshot_id')} scouting", ANSI_BOLD, ANSI_GREEN))
            print_post_result(post_result, verbose=False)


def print_post_result(post_result: dict[str, Any], *, verbose: bool = False) -> None:
    """Render generic, parser-provided post-download information.

    Vendor-specific interpretation belongs in the external parser. The public
    Wayback renderer only displays compact scalar summaries and, in verbose
    mode, structured values supplied by that parser.
    """

    print_section("Firmware Scouting")
    wrapper = post_result.get("wrapper_kind")
    if wrapper is not None:
        print_key_value("Wrapper", str(wrapper))
    for key in ("status", "verdict", "summary", "evidence", "message"):
        if key in post_result:
            print_key_value(key.replace("_", " ").capitalize(), str(post_result[key]))
    if verbose:
        for key, value in sorted(post_result.items()):
            if key in {"wrapper_kind", "status", "verdict", "summary", "evidence", "message"}:
                continue
            print_key_value(key.replace("_", " ").capitalize(), str(value))
