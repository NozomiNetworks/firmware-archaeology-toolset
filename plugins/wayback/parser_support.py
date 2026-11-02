"""Shared support helpers for the Wayback workflow and parser hooks."""

from __future__ import annotations

from typing import Any

from fatlib.browser import BrowserOptions

from .retrieve_archived_firmware import infer_parser_name, load_parser


def require_parser(project: dict[str, Any], snapshots: list[dict[str, Any]]) -> str:
    parser_name = project.get("parser")
    if parser_name:
        return str(parser_name)
    if snapshots:
        return infer_parser_name(snapshots[0]["target_url"], None)
    raise SystemExit(
        "No parser configured for this Wayback project.\n"
        "Set it with `wayback set parser <name>` before searching firmware on a page."
    )


def load_post_hook(project: dict[str, Any], snapshots: list[dict[str, Any]]):
    parser_name = project.get("parser")
    if not parser_name and snapshots:
        parser_name = infer_parser_name(snapshots[0]["target_url"], None)
    if not parser_name:
        return None
    parser = load_parser(parser_name)
    hook = getattr(parser, "post_download", None)
    if hook is None:
        return None
    if not callable(hook):
        raise SystemExit(f"Parser {parser_name} exports `post_download`, but it is not callable.")
    return hook


def load_update_hook(project: dict[str, Any], snapshots: list[dict[str, Any]]):
    parser_name = project.get("parser")
    if not parser_name:
        return None
    parser = load_parser(parser_name)
    hook = getattr(parser, "update_firmware_link", None)
    if hook is None:
        return None
    if not callable(hook):
        raise SystemExit(f"Parser {parser_name} exports `update_firmware_link`, but it is not callable.")
    return hook


def render_options(parser: Any, timeout: int) -> BrowserOptions:
    """Validate optional browser settings exported by a Wayback parser."""

    return BrowserOptions(
        mode=str(getattr(parser, "RENDER_MODE", "static")),
        wait_until=str(getattr(parser, "RENDER_WAIT_UNTIL", "domcontentloaded")),
        wait_for=getattr(parser, "RENDER_WAIT_FOR", None),
        timeout=int(getattr(parser, "RENDER_TIMEOUT", None) or timeout),
    )
