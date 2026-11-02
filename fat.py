#!/usr/bin/env python3
"""Firmware Archaeology Toolset CLI."""

from __future__ import annotations

import os
import socket
import shutil
import sys
import urllib.error
from pathlib import Path

from fatlib.discovery import discover_modules, instantiate_plugin_like
from fatlib.plugin_config import resolve_plugin_dir, strip_plugin_options
from fatlib.leads import LEAD_SOURCES, LEAD_STATUSES, add_lead, iter_leads, remove_leads, set_lead_field
from fatlib.paths import project_output_dir, project_timeout
from fatlib.projects import (
    ensure_surface_project,
    project_download_roots,
    resolve_project_name,
    set_project_context_values,
    update_project_setting,
)
from fatlib.reports import write_project_report
from plugins.base import (
    ANSI_BOLD,
    STATE_FILE,
    ensure_state_shape,
    load_state,
    print_error,
    print_key_value,
    print_ok,
    print_section,
    print_warn,
    save_state,
    style,
)
from plugins.scraper.registry import configure_external_scrapers
from plugins.wayback.registry import configure_external_parsers


SURFACE_COMMANDS = {"search", "status", "options", "select", "clear-selection", "clear", "download", "post", "set"}
PLUGINS = discover_modules(
    "plugins",
    Path(__file__).resolve().parent / "plugins",
    skip={"base"},
    instantiate=instantiate_plugin_like,
)
PLUGIN_MAP = {plugin.name: plugin for plugin in PLUGINS}
PLUGIN_NAMES = tuple(PLUGIN_MAP)
ANSI_NEON_PINK = "\033[95m"
TOOL_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "fat-logo.txt"


def load_logo(path: Path | None = None) -> str:
    """Load the optional CLI logo from disk."""

    path = TOOL_LOGO_PATH if path is None else path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def render_pink_logo_art(art: str) -> str:
    """Render logo artwork in the default neon-pink style."""

    return style(art, ANSI_BOLD, ANSI_NEON_PINK)


def print_logo() -> None:
    """Print the FAT banner using the CLI's terminal color policy."""

    for line in load_logo().splitlines():
        print(render_pink_logo_art(line))


def is_help_flag(value: str) -> bool:
    return value in {"-h", "--help"}


def resolve_state_file_from_argv(argv: list[str], default: Path = STATE_FILE) -> Path:
    if "--state-file" not in argv:
        return default
    index = argv.index("--state-file")
    if index + 1 >= len(argv):
        raise SystemExit("--state-file requires a value")
    return Path(argv[index + 1])


def strip_global_options(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token == "--state-file":
            if index + 1 >= len(argv):
                raise SystemExit("--state-file requires a value")
            skip_next = True
            continue
        cleaned.append(token)
    return cleaned


def normalize_global_options(argv: list[str]) -> list[str]:
    """Accept global options before or after the command arguments."""

    if "--state-file" not in argv:
        return argv
    cleaned = strip_global_options(argv)
    if "--state-file" not in argv:
        return cleaned
    state_file = resolve_state_file_from_argv(argv)
    return cleaned + ["--state-file", str(state_file)]


def build_top_level_parser():
    import argparse

    tool_lines = "\n".join(
        f"  {plugin.name:<10} {getattr(plugin, 'help_summary', '')}"
        for plugin in PLUGINS
    )
    parser = argparse.ArgumentParser(
        prog="fat.py",
        description="Firmware Archaeology Toolset: artifact-focused recon and archive retrieval.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Tools:\n"
            f"{tool_lines}\n"
        ),
    )
    parser.add_argument("project_or_tool", nargs="?", help="`project`, a project name, or a tool name.")
    parser.add_argument("tool", nargs="?", help="Tool name when a project name is provided first.")
    parser.add_argument("rest", nargs="*", help="Remaining tool-specific arguments.")
    parser.add_argument("--config-file", type=Path, help="FAT configuration file.")
    parser.add_argument("--plugin-dir", type=Path, help="External plugin directory.")
    return parser


def parse_set_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="Set the active project context.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  fat.py project myproject\n"
            "  fat.py project myproject --keywords \"sdk,platform,connector\"\n"
            "  fat.py project myproject --output-dir output\n"
            "  fat.py project myproject --timeout 30\n"
        ),
    )
    parser.add_argument("project", help="Project name to persist as the current project.")
    parser.add_argument("--keywords", help="Default comma-separated keywords to persist for this project.")
    parser.add_argument("--output-dir", help="Project-wide output directory.")
    parser.add_argument("--timeout", type=int, help="Project-wide timeout in seconds.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_project_setting_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="Update settings on the active project.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  fat.py project set keywords \"sdk,platform,connector\"\n"
            "  fat.py project set output-dir output\n"
            "  fat.py project set timeout 30\n"
        ),
    )
    parser.add_argument("setting", help="Project setting name.")
    parser.add_argument("value", help="Project setting value.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_project_delete_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="Delete a persisted project.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  fat.py project delete\n"
            "  fat.py project delete myproject\n"
            "  fat.py project delete myproject --delete-downloads yes\n"
        ),
    )
    parser.add_argument("project", nargs="?", help="Project name to delete. Defaults to the active project.")
    parser.add_argument(
        "--delete-downloads",
        choices=("yes", "no"),
        help="Whether to also delete downloaded artifacts for this project.",
    )
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_lead_add_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="Add one investigation lead to the project.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Sources:\n"
            "  portal   vendor's own site (product, support, download pages)\n"
            "  web      open-web search or dorks\n"
            "  writeup  public writeups: blogs, talks, forum threads\n"
            "  repo     code repositories (GitHub, GitLab)\n"
            "  image    container images (Docker Hub)\n"
            "  archive  web archive discovery (wayback CDX or replay)\n"
            "  user     provided directly by the user\n"
            "\n"
            "Statuses: new (recorded, not examined), checked (examined, pending),\n"
            "acted (used as input to a FAT action), dead (ruled out).\n"
            "\n"
            "Examples:\n"
            '  fat.py project lead add https://vendor.example/support/model \\\n'
            '      --source portal --title "support page" --note "lists fw 3.2.1"\n'
            '  fat.py project lead add https://mirror.example/fw-2.1.zip --source web\n'
        ),
    )
    parser.add_argument("url", help="Absolute http(s) URL of the lead.")
    parser.add_argument("--title", default="", help="Short display label.")
    parser.add_argument("--source", required=True, help=f"One of: {', '.join(LEAD_SOURCES)}.")
    parser.add_argument("--note", default="", help="Why this lead is interesting.")
    parser.add_argument("--status", default="new", help=f"One of: {', '.join(LEAD_STATUSES)}.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_lead_list_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="List recorded leads, optionally filtered by status.",
        epilog=f"Statuses: {', '.join(LEAD_STATUSES)}.",
    )
    parser.add_argument("status", nargs="?", default="", help=f"Optional filter: {', '.join(LEAD_STATUSES)}.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_lead_set_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(
        description="Update one lead field.",
        epilog=(
            f"Fields: status, title, source, note.\n"
            f"Statuses: {', '.join(LEAD_STATUSES)}.\n"
            f"Sources: {', '.join(LEAD_SOURCES)}.\n"
            "Use `none` to clear title, source, or note."
        ),
    )
    parser.add_argument("lead", help="Lead id.")
    parser.add_argument("field", help="One of: status, title, source, note.")
    parser.add_argument("value", help="New value; `none` clears title, source, or note.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


def parse_lead_rm_args(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(description="Remove one or more leads.")
    parser.add_argument("leads", help="Comma-separated lead ids, for example 1 or 1,3.")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="State file path.")
    return parser.parse_args(argv)


LEAD_COMMANDS = {"add", "list", "set", "rm"}


def lead_usage() -> str:
    return (
        "Usage:\n"
        "  fat.py project lead add <url> --source <source> [--title T] [--note N] [--status S]\n"
        "  fat.py project lead list [status]\n"
        "  fat.py project lead set <id> <field> <value>\n"
        "  fat.py project lead rm <id>[,<id>...]\n"
        "\n"
        f"Sources: {', '.join(LEAD_SOURCES)}\n"
        f"Statuses: {', '.join(LEAD_STATUSES)}"
    )


def print_lead_table(leads: list[dict]) -> None:
    print("ID   Status   Source   URL")
    print("---  -------  -------  --------------------------------------------------")
    for lead in leads:
        print(f"{lead['id']:<4} {lead['status']:<8} {lead['source']:<8} {lead['url']}")
        if lead.get("title"):
            print(f"{'':<4} {'':<8} {'':<8} title: {lead['title']}")
        if lead.get("note"):
            print(f"{'':<4} {'':<8} {'':<8} note: {lead['note']}")


def handle_project_leads(rest: list[str], explicit_project: str | None) -> int:
    if not rest or rest[0] in ("-h", "--help") or rest[0] not in LEAD_COMMANDS:
        raise SystemExit(lead_usage())
    command, lead_argv = rest[0], rest[1:]
    if command == "add":
        args = parse_lead_add_args(lead_argv)
    elif command == "list":
        args = parse_lead_list_args(lead_argv)
    elif command == "set":
        args = parse_lead_set_args(lead_argv)
    else:
        args = parse_lead_rm_args(lead_argv)

    state_file = args.state_file
    state = ensure_state_shape(load_state(state_file))
    project_name = resolve_project_name(state, explicit_project)
    project = ensure_surface_project(state, project_name)

    if command == "add":
        lead = add_lead(project, args.url, title=args.title, source=args.source, note=args.note, status=args.status)
        print_ok(f"Added lead {lead['id']} to {project_name}")
        print_key_value("URL", lead["url"])
        print_key_value("Status", lead["status"])
        print_key_value("Source", lead["source"])
        if lead["title"]:
            print_key_value("Title", lead["title"])
        if lead["note"]:
            print_key_value("Note", lead["note"])
    elif command == "list":
        leads = iter_leads(project, args.status)
        print_section("Leads")
        print_key_value("Project", project_name)
        print("")
        if not leads:
            print_warn("No leads recorded. Use `project lead add <url> --source <source>`.")
        else:
            print_lead_table(leads)
        print("")
        print_key_value("Leads", str(len(leads)))
    elif command == "set":
        lead = set_lead_field(project, args.lead, args.field, args.value)
        print_ok(f"Updated lead {lead['id']} in {project_name}")
        print_key_value(args.field.replace("_", " ").capitalize(), lead[args.field] or "-")
    else:
        removed = remove_leads(project, args.leads)
        print_ok(f"Removed {len(removed)} lead(s) from {project_name}")
        print_key_value("Removed ids", ", ".join(str(lead["id"]) for lead in removed))

    state["current_project"] = project_name
    save_state(state_file, state)
    return 0


def set_project_context(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    state = ensure_state_shape(state)
    project = ensure_surface_project(state, args.project)
    set_project_context_values(
        project,
        keywords=args.keywords,
        output_dir=args.output_dir,
        timeout=args.timeout,
    )
    state["current_project"] = args.project
    save_state(args.state_file, state)
    print_section("Current Project")
    print_key_value("Project", args.project)
    print_key_value("Keywords", ", ".join(project["keywords"]) or "-")
    print_key_value("Output dir", str(project_output_dir(project)))
    print_key_value("Timeout", str(project_timeout(project)))
    print_key_value("State", str(args.state_file))
    return 0


def set_project_setting(args) -> int:
    state = load_state(args.state_file)
    state = ensure_state_shape(state)
    project_name = resolve_project_name(state, None)
    project = ensure_surface_project(state, project_name)
    label, rendered = update_project_setting(project, args.setting, args.value)
    print_ok(f"Updated project {args.setting.replace('_', '-').lower()} for {project_name}")
    print_key_value(label, rendered)
    save_state(args.state_file, state)
    return 0


def prompt_delete_downloads(project_name: str, roots: list[Path]) -> bool:
    print_section("Delete Project")
    print_key_value("Project", project_name)
    if roots:
        print_key_value("Download roots", str(len(roots)))
        for root in roots:
            print(f"  - {root}")
    else:
        print_key_value("Download roots", "0")
    while True:
        answer = input("Delete downloaded artifacts for this project as well? [y/N]: ").strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print_warn("Please answer `y` or `n`.")


def delete_project(args) -> int:
    state = load_state(args.state_file)
    project_name = resolve_project_name(state, args.project)
    projects = state.get("surface_projects", {})
    if project_name not in projects:
        raise SystemExit(f"Unknown project {project_name!r}.")
    project = ensure_surface_project(state, project_name)
    roots = project_download_roots(project_name, project, PLUGINS)

    if args.delete_downloads is None:
        delete_downloads = prompt_delete_downloads(project_name, roots)
    else:
        delete_downloads = args.delete_downloads == "yes"

    removed_paths: list[Path] = []
    if delete_downloads:
        for root in roots:
            if not root.exists():
                continue
            removed_paths.append(root)
            if root.is_dir():
                shutil.rmtree(root)
            else:
                root.unlink()

    del projects[project_name]
    if state.get("current_project") == project_name:
        state["current_project"] = None
    save_state(args.state_file, state)

    print_ok(f"Deleted project {project_name}")
    print_key_value("State", str(args.state_file))
    print_key_value("Downloads deleted", "yes" if delete_downloads else "no")
    print_key_value("Paths removed", str(len(removed_paths)))
    if removed_paths:
        print("")
        print("Removed paths")
        for path in removed_paths:
            print(f"- {path}")
    return 0


def print_root_status(state: dict, state_file: Path) -> None:
    state = ensure_state_shape(state)
    projects = state.get("surface_projects", {})
    current_project = state.get("current_project")
    print_section("Projects")
    print_key_value("Active project", current_project or "-")
    print_key_value("Surface state", str(state_file))
    print_key_value("Projects", str(len(projects)))
    print("")
    if not projects:
        print_warn("No projects in state. Use `project <name>` first.")
        return
    print("Act  Project                Keys  Plugin Summaries")
    print("---  ---------------------  ----  -----------------------------------------------")
    for project_name in sorted(projects):
        project = projects[project_name]
        summaries = ", ".join(plugin.root_summary(project) for plugin in PLUGINS)
        marker = "*" if project_name == current_project else " "
        print(
            f" {marker}   {project_name[:21]:<21}  {len(project.get('keywords', [])):>4}  {summaries}"
        )


def print_project_status(project_name: str, project: dict, state_file: Path) -> None:
    print_section("Project Status")
    print_key_value("Project", project_name)
    print_key_value("Keywords", ", ".join(project.get("keywords", [])) or "-")
    print_key_value("Output dir", str(project_output_dir(project)))
    print_key_value("Timeout", str(project_timeout(project)))
    print_key_value("Surface state", str(state_file))
    print_key_value("Report", project.get("report_path") or "-")
    print_key_value("Leads", str(len(project.get("leads", []))))
    print("")

    print_section("Plugins")
    for plugin in PLUGINS:
        print_key_value(plugin.name, plugin.project_summary(project))


def handle_project_report(project_name: str, state: dict, state_file: Path) -> int:
    project = ensure_surface_project(state, project_name)
    state["current_project"] = project_name
    report_path = write_project_report(project_name, project, state, state_file, PLUGINS)
    print_ok(f"Wrote {report_path}")
    return 0


def run_plugin_tool(project_name: str, tool_name: str, argv: list[str]) -> int:
    plugin = PLUGIN_MAP[tool_name]
    state_file = resolve_state_file_from_argv(argv)
    plugin_argv = strip_global_options(argv)
    args = plugin.parse_args(plugin_argv, SURFACE_COMMANDS)
    args.state_file = state_file
    state = ensure_state_shape(load_state(state_file))
    project = ensure_surface_project(state, project_name)
    state["current_project"] = project_name
    command_method = {
        "search": "search",
        "status": "status",
        "options": "options",
        "select": "select",
        "clear-selection": "clear_selection",
        "clear": "clear",
        "download": "download_command",
        "post": "post_command",
        "set": "set",
    }[args.command]
    if not hasattr(plugin, command_method):
        raise SystemExit(f"`{tool_name} {args.command}` is not supported.")
    result = getattr(plugin, command_method)(project_name, project, args)
    save_state(state_file, state)
    return result


def split_project_and_command(argv: list[str]) -> tuple[str | None, list[str]]:
    if not argv:
        return None, []
    first = argv[0].lower()
    if first in (*PLUGIN_NAMES, "status"):
        return None, argv
    return argv[0], argv[1:]


def handle_root_status(state: dict, state_file: Path) -> int:
    print_root_status(state, state_file)
    return 0


def handle_project_status(project_name: str, state: dict, state_file: Path) -> int:
    project = ensure_surface_project(state, project_name)
    state["current_project"] = project_name
    save_state(state_file, state)
    print_project_status(project_name, project, state_file)
    return 0


def dispatch_tool(tool_name: str, explicit_project: str | None, argv: list[str], state: dict) -> int:
    plugin_argv = strip_global_options(argv[1:])
    if tool_name in PLUGIN_NAMES and any(is_help_flag(flag) for flag in plugin_argv):
        PLUGIN_MAP[tool_name].parse_args(plugin_argv, SURFACE_COMMANDS)
        return 0
    project_name = resolve_project_name(state, explicit_project)
    if tool_name not in PLUGIN_MAP:
        raise SystemExit(f"Unknown tool {tool_name!r}. Valid choices: {', '.join(PLUGIN_NAMES)}")
    return run_plugin_tool(project_name, tool_name, argv[1:])


PROJECT_USAGE = (
    "Usage:\n"
    "  fat.py project <name> [--keywords K] [--output-dir D] [--timeout S]\n"
    "      Create or activate a project.\n"
    "  fat.py project set <setting> <value>\n"
    "      Update the active project (keywords, output-dir, timeout).\n"
    "  fat.py project lead add|list|set|rm ...\n"
    "      Manage recorded investigation leads.\n"
    "  fat.py project delete [name] [--delete-downloads yes|no]\n"
    "      Delete a project (defaults to the active one). Without the flag,\n"
    "      FAT asks whether to also delete downloaded artifacts.\n"
)


def run_main() -> int:
    print_logo()
    argv = normalize_global_options(sys.argv[1:])
    plugin_dir = resolve_plugin_dir(argv)
    configure_external_scrapers(plugin_dir)
    configure_external_parsers(plugin_dir)
    argv = strip_plugin_options(argv)
    top_parser = build_top_level_parser()

    if not argv or is_help_flag(argv[0]):
        top_parser.print_help()
        return 0

    if argv[0] == "project":
        if len(argv) > 1 and argv[1] in ("-h", "--help", "help"):
            print(PROJECT_USAGE)
            return 0
        if len(argv) > 1 and argv[1] == "set":
            return set_project_setting(parse_project_setting_args(argv[2:]))
        if len(argv) > 1 and argv[1] == "delete":
            return delete_project(parse_project_delete_args(argv[2:]))
        if len(argv) > 1 and argv[1] == "lead":
            return handle_project_leads(argv[2:], None)
        return set_project_context(parse_set_args(argv[1:]))
    if argv[0] == "set":
        return set_project_setting(parse_project_setting_args(argv[1:]))
    if argv[0] == "lead":
        return handle_project_leads(argv[1:], None)

    state_file = resolve_state_file_from_argv(argv)
    state = ensure_state_shape(load_state(state_file))

    if argv[0] == "status":
        return handle_root_status(state, state_file)
    if argv[0] == "report":
        return handle_project_report(resolve_project_name(state, None), state, state_file)

    explicit_project, argv = split_project_and_command(argv)
    if explicit_project and not argv:
        raise SystemExit("Missing tool name after project.")

    if argv and argv[0] == "status":
        if not explicit_project:
            raise SystemExit("Project status requires `<project> status`.")
        return handle_project_status(explicit_project, state, state_file)
    if argv and argv[0] == "report":
        if not explicit_project:
            raise SystemExit("Project report requires `<project> report` or `report` with an active project.")
        return handle_project_report(explicit_project, state, state_file)
    if argv and argv[0] == "lead":
        if not explicit_project:
            raise SystemExit("Project leads require an active project or `<project> lead ...`.")
        return handle_project_leads(argv[1:], explicit_project)

    if not argv:
        top_parser.print_help()
        return 0
    tool_name = argv[0].lower()
    return dispatch_tool(tool_name, explicit_project, argv, state)


def is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return is_timeout_exception(getattr(exc, "reason", None))
    return False


def format_fatal_error(exc: BaseException) -> str:
    if is_timeout_exception(exc):
        return f"Timed out: {exc}"
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "Rate limited (HTTP 429 Too Many Requests). Wait a moment and retry."
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def main() -> int:
    try:
        return run_main()
    except KeyboardInterrupt:
        print_warn("Interrupted.")
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        if os.environ.get("FAT_DEBUG"):
            raise
        print_error(format_fatal_error(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
