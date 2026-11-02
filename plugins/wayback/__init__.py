"""Wayback workflow plugin package."""

from __future__ import annotations

import argparse
from collections.abc import Set as AbstractSet
from pathlib import Path
import urllib.parse

from ..base import (
    PluginSetting,
    SurfacePluginBase,
    is_unset_value,
    print_ok,
    parse_keywords,
    project_timeout,
    require_no_extra_args,
    require_optional_selection,
    require_selection_only,
    require_setting_assignment,
)
from .retrieve_archived_firmware import ArchiveClient, load_parser
from .registry import available_parser_names
from .parser_support import load_post_hook, load_update_hook
from . import workflow
from .render import (
    format_status,
    format_timestamp,
    firmware_summary,
    print_download_table,
    print_error,
    print_key_value,
    print_post_result,
    print_section,
    print_snapshot_table,
    print_warn,
    show_status as render_show_status,
)


def selection_is_firmware(selection: str) -> bool:
    return ":" in selection


def clear_wayback_state(project: dict[str, object], *, remove_files: bool = True) -> list[Path]:
    return workflow.clear_wayback_state(project, remove_files=remove_files)


def enum_snapshots(project: dict[str, object], archive: ArchiveClient) -> None:
    workflow.enum_snapshots(project, archive)
    print("")
    print_snapshot_table(project.get("snapshots", []))


def select_snapshots(project: dict[str, object], selection: str) -> None:
    selected_ids = workflow.select_snapshots(project, selection)
    print(f"Selected snapshots: {','.join(str(item) for item in selected_ids) or '(none)'}")
    print("")
    print_snapshot_table(project.get("snapshots", []))


def clear_selection(project: dict[str, object]) -> None:
    workflow.clear_selection(project)
    snapshots = project.get("snapshots", [])
    print("Selected snapshots cleared.")
    if snapshots:
        print("")
        print_snapshot_table(snapshots)


def select_firmware(project: dict[str, object], selection: str) -> None:
    labels = workflow.select_firmware(project, selection)
    print(f"Selected firmware: {','.join(labels) or '(none)'}")
    print("")
    print_snapshot_table(project.get("snapshots", []))


def clear_firmware_selection(project: dict[str, object]) -> None:
    workflow.clear_firmware_selection(project)
    snapshots = project.get("snapshots", [])
    print("Selected firmware cleared.")
    if snapshots:
        print("")
        print_snapshot_table(snapshots)


def enum_firmware(project: dict[str, object], selection: str, archive: ArchiveClient) -> None:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    workflow.enum_firmware(project, selection, archive)
    for snapshot in workflow.parse_selection(selection, snapshots):
        links = snapshot.get("firmware_links", [])
        print(
            f"{snapshot['id']:>3}  {format_timestamp(snapshot['timestamp'])}  "
            f"firmware_links={len(links)}  {firmware_summary(snapshot, max_items=3, max_length=96)}"
        )
        if not links:
            if snapshot.get("parser_error"):
                print_warn(f"Parser failed for snapshot {snapshot['id']}: {snapshot['parser_error']}")
            else:
                print_warn(
                    f"No firmware links found for snapshot {snapshot['id']} ({snapshot['original']}). "
                    "The archived page may use an older URL or HTML schema; review the vendor parser."
                )
            if snapshot.get("debug_html_path"):
                print_warn(f"Archived HTML saved for review: {snapshot['debug_html_path']}")
    print("")
    print_snapshot_table(snapshots)


def download_firmware(project: dict[str, object], selection: str, archive: ArchiveClient) -> None:
    before = len(project.setdefault("downloads", []))
    workflow.download_firmware(project, selection, archive)
    for entry in project.get("downloads", [])[before:]:
        post_result = entry.get("post_result")
        if isinstance(post_result, dict):
            print_post_result(post_result, verbose=True)
        when = "live" if entry.get("download_source") == "live" else format_timestamp(entry.get("capture_timestamp"))
        print(f"{entry['snapshot_id']:>3}  {format_status(entry['status']):<19}  {when:<19}  {entry['url']}")


def run_post_actions(project: dict[str, object], selection: str, archive: ArchiveClient) -> None:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    if load_post_hook(project, snapshots) is None:
        print_warn("Selected parser does not define a post hook.")
        return
    previous = {
        (entry.get("snapshot_id"), entry.get("url")): entry.get("post_result")
        for entry in project.setdefault("downloads", [])
    }
    any_ran = workflow.run_post_actions(project, selection, archive)
    for entry in project.get("downloads", []):
        key = (entry.get("snapshot_id"), entry.get("url"))
        if entry.get("post_status") == "error":
            local_name = Path(str(entry.get("local_path") or "firmware")).name
            print_error(f"post hook failed for {local_name}: {entry.get('post_error')}")
        post_result = entry.get("post_result")
        if isinstance(post_result, dict) and previous.get(key) is not post_result:
            print_post_result(post_result, verbose=True)
    if not any_ran:
        print_warn("No post actions were executed.")


def show_status(project_name: str, project: dict[str, object]) -> None:
    snapshots = project.get("snapshots", [])
    render_show_status(
        project_name,
        project,
        has_update_hook=load_update_hook(project, snapshots) is not None,
        has_post_hook=load_post_hook(project, snapshots) is not None,
    )


class WaybackPlugin(SurfacePluginBase):
    """Top-level FAT plugin for archived firmware enumeration and retrieval.

    Unlike standard surface plugins, Wayback uses a two-phase workflow:

    - enumerate archived snapshots of target download pages
    - enumerate and retrieve firmware links from selected snapshots

    It therefore overrides command parsing and several command handlers instead
    of relying entirely on `SurfacePluginBase`.
    """

    name = "wayback"
    default_capture_policy = "nearest"
    help_summary = "Enumerate archived download pages and firmware files."
    help_examples = (
        "Examples:\n"
        "  fat.py wayback set parser examplevendor\n"
        "  fat.py wayback set api-base http://127.0.0.1:8031/archive\n"
        "  fat.py wayback set downloads-dir output/downloads/lab/wayback\n"
        "  fat.py wayback set capture-policy strict\n"
        "  fat.py wayback set target-url https://vendor.example/download\n"
        "  fat.py wayback set target-type auto|page|file\n"
        "  fat.py wayback set domain www.vendor.example\n"
        "  fat.py wayback set keywords firmware,product\n"
        "  fat.py wayback set filetypes 'include=text/html;exclude=image/*'\n"
        "  fat.py wayback search snapshot\n"
        "  fat.py wayback select 1,2\n"
        "  fat.py wayback search firmware\n"
        "  fat.py wayback select 2:1,2:2\n"
        "  fat.py wayback download selected\n"
        "  fat.py wayback post selected\n"
        "  fat.py wayback clear\n"
    )

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description="Stateful wayback workflow runner.",
            formatter_class=argparse.RawTextHelpFormatter,
            epilog=self.help_examples,
        )
        parser.add_argument("selection", nargs="?", default="selected", help="`snapshot` or `firmware` for search, ids for select/download, or setting name for set.")
        parser.add_argument("value", nargs="?", default="", help="Value used by `set`, for example the parser name.")
        parser.add_argument("--quiet", action="store_true", help="Suppress progress and table output.")
        return parser

    def parse_args(self, argv: list[str], commands: AbstractSet[str]):
        command = "status"
        if argv and argv[0] in commands:
            command = argv[0]
            argv = argv[1:]
        parser = self.build_arg_parser()
        args = parser.parse_args(argv)
        args.command = command
        self.validate_args(args)
        return args

    def validate_args(self, args) -> None:
        command = args.command
        selection = args.selection
        value = args.value

        if command == "search":
            if selection not in {"snapshot", "firmware"} or value:
                raise SystemExit("`wayback search` requires `snapshot` or `firmware` and no extra trailing value.")
            return

        if command == "select":
            require_selection_only(
                self.name,
                command,
                selection,
                value,
                message="`wayback select` requires snapshot ids like `1,2` or firmware ids like `2:1,2:2`.",
            )
            return

        if command in {"post"}:
            require_optional_selection(
                self.name,
                command,
                value,
                message=f"`wayback {command}` accepts at most one selector such as `selected`, `all`, or `1,2`.",
            )
            return

        if command in {"clear"}:
            require_no_extra_args(self.name, command, selection, value)
            return

        super().validate_args(args)

    def ensure_state(self, project: dict):
        defaults = {
            "snapshots": [],
            "downloads": [],
            "target_url": None,
            "target_type": "auto",
            "search_domain": None,
            "search_keywords": [],
            "search_filetypes": {"include": [], "exclude": ["image/*", "text/css", "application/javascript", "text/javascript"]},
        }
        wayback = super().ensure_state(project)
        for key, value in defaults.items():
            wayback.setdefault(key, value)
        return wayback

    def setting_definitions(self, project: dict, tool_state: dict) -> list[PluginSetting]:
        return [
            PluginSetting(
                name="parser",
                state_key="parser",
                label="Parser",
                default_factory=lambda _project, _tool_state: None,
                parse_value=lambda raw, _project, _tool_state: self.parse_parser_value(raw),
                render_value=lambda value: value or "-",
            ),
            PluginSetting(
                name="api-base",
                state_key="api_base",
                label="API base",
                default_factory=lambda _project, _tool_state: None,
                parse_value=lambda raw, _project, _tool_state: raw.rstrip("/"),
                render_value=lambda value: value or "-",
            ),
            PluginSetting(
                name="capture-policy",
                state_key="capture_policy",
                label="Capture policy",
                default_factory=lambda _project, _tool_state: self.default_capture_policy,
                parse_value=lambda raw, _project, _tool_state: self.parse_capture_policy(raw),
            ),
            PluginSetting(
                name="target-url",
                state_key="target_url",
                label="Target URL",
                default_factory=lambda _project, _tool_state: None,
                parse_value=lambda raw, _project, _tool_state: self.parse_target_url(raw),
                render_value=lambda value: value or "-",
            ),
            PluginSetting(
                name="target-type",
                state_key="target_type",
                label="Target type",
                default_factory=lambda _project, _tool_state: "auto",
                parse_value=lambda raw, _project, _tool_state: self.parse_target_type(raw),
            ),
            PluginSetting(
                name="domain",
                state_key="search_domain",
                label="Domain",
                default_factory=lambda _project, _tool_state: None,
                parse_value=lambda raw, _project, _tool_state: self.parse_domain(raw),
                render_value=lambda value: value or "-",
            ),
            PluginSetting(
                name="keywords",
                state_key="search_keywords",
                label="Keywords",
                default_factory=lambda _project, _tool_state: [],
                parse_value=lambda raw, _project, _tool_state: parse_keywords(raw),
                render_value=lambda value: ", ".join(value or []) or "-",
            ),
            PluginSetting(
                name="filetypes",
                state_key="search_filetypes",
                label="File types",
                default_factory=lambda _project, _tool_state: {"include": [], "exclude": ["image/*", "text/css", "application/javascript", "text/javascript"]},
                parse_value=lambda raw, _project, _tool_state: self.parse_filetypes(raw),
                render_value=lambda value: self.render_filetypes(value),
            ),
            *super().setting_definitions(project, tool_state),
        ]

    def option_rows(self, project_name: str, project: dict, tool_state: dict) -> list[tuple[str, str]]:
        rows = super().option_rows(project_name, project, tool_state)
        snapshots = tool_state.get("snapshots", [])
        return rows + [
            ("Active target", str(tool_state.get("active_target_url") or tool_state.get("target_url") or "-")),
            ("Post hook", "present" if load_post_hook(tool_state, snapshots) is not None else "-"),
            ("Available parsers", ", ".join(available_parser_names()) or "-"),
        ]

    def parse_parser_value(self, raw_value: str) -> str:
        value = raw_value.strip()
        load_parser(value)
        return value

    def parse_capture_policy(self, raw_value: str) -> str:
        value = raw_value.strip().lower()
        if value not in {"nearest", "strict"}:
            raise SystemExit("Wayback capture-policy must be `nearest` or `strict`.")
        return value

    def parse_target_url(self, raw_value: str) -> str:
        value = raw_value.strip()
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SystemExit("Wayback target-url must be an absolute http(s) URL.")
        return value

    def parse_target_type(self, raw_value: str) -> str:
        value = raw_value.strip().lower()
        if value not in {"auto", "page", "file"}:
            raise SystemExit("Wayback target-type must be `auto`, `page`, or `file`.")
        return value

    def parse_domain(self, raw_value: str) -> str:
        value = raw_value.strip()
        parsed = urllib.parse.urlsplit(value if "://" in value else f"https://{value}")
        if not parsed.hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise SystemExit("Wayback domain must be a hostname without a path.")
        return parsed.hostname

    def parse_filetypes(self, raw_value: str) -> dict[str, list[str]]:
        rules = {"include": [], "exclude": []}
        for clause in raw_value.split(";"):
            clause = clause.strip()
            if not clause:
                continue
            if "=" not in clause:
                raise SystemExit("Wayback filetypes must use `include=...;exclude=...`.")
            name, values = clause.split("=", 1)
            name = name.strip().lower()
            if name not in rules:
                raise SystemExit("Wayback filetypes supports only `include` and `exclude`.")
            rules[name] = [item.strip().lower() for item in values.split(",") if item.strip()]
        if not rules["include"] and not rules["exclude"]:
            raise SystemExit("Wayback filetypes needs at least one file type rule.")
        return rules

    def render_filetypes(self, value) -> str:
        include = ",".join(value.get("include", []))
        exclude = ",".join(value.get("exclude", []))
        return f"include={include or '*'};exclude={exclude or '-'}"

    def build_archive(self, project: dict, args, plugin_state: dict):
        return ArchiveClient(
            sleep=1.0,
            verbose=not args.quiet,
            verbose_http=False,
            api_base=plugin_state.get("api_base"),
            timeout=project_timeout(project),
        )

    def search(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        archive = self.build_archive(project, args, plugin_state)
        mode = args.selection
        if mode == "snapshot":
            enum_snapshots(plugin_state, archive)
            return 0
        if mode == "firmware":
            enum_firmware(plugin_state, "selected", archive)
            return 0
        raise SystemExit("Wayback search requires `snapshot` or `firmware`.")

    def status(self, project_name: str, project: dict, args) -> int:
        show_status(project_name, self.ensure_state(project))
        return 0

    def options(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        print_section("Plugin Options")
        for label, value in self.option_rows(project_name, project, plugin_state):
            print_key_value(label, value)
        return 0

    def select(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        if args.selection == "selected":
            raise SystemExit("Wayback select requires ids like `1,2` or firmware ids like `2:1,2:2`.")
        if selection_is_firmware(args.selection):
            select_firmware(plugin_state, args.selection)
        else:
            select_snapshots(plugin_state, args.selection)
        return 0

    def clear_selection(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        clear_firmware_selection(plugin_state)
        clear_selection(plugin_state)
        return 0

    def clear(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        removed_paths = clear_wayback_state(plugin_state, remove_files=True)
        print_ok("Cleared wayback snapshots, firmware selections, and download history")
        print_key_value("Removed paths", str(len(removed_paths)))
        print_key_value("Downloads dir", str(plugin_state.get("downloads_dir") or "-"))
        return 0

    def download_command(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        archive = self.build_archive(project, args, plugin_state)
        download_firmware(plugin_state, args.selection, archive)
        return 0

    def post_command(self, project_name: str, project: dict, args) -> int:
        plugin_state = self.ensure_state(project)
        archive = self.build_archive(project, args, plugin_state)
        run_post_actions(plugin_state, args.selection, archive)
        return 0

    def set(self, project_name: str, project: dict, args) -> int:
        setting_name = args.selection.replace("_", "-").lower()
        tool_state = self.ensure_state(project)
        if setting_name in {"domain", "keywords", "filetypes"}:
            setting = self.setting_map(project, tool_state)[setting_name]
            if not is_unset_value(args.value) and setting_name in {"domain", "keywords"}:
                if tool_state.get("target_url") or tool_state.get("active_target_url"):
                    tool_state["target_url"] = None
                    tool_state["active_target_url"] = None
                    tool_state["snapshots"] = []
                    tool_state["downloads"] = []
                    print_warn("Switched to Domain keyword search mode; cleared target-url and previous results.")
            if is_unset_value(args.value):
                tool_state[setting.state_key] = self.resolve_setting_default(setting, project, tool_state)
            else:
                tool_state[setting.state_key] = self.parse_setting_value(setting, args.value, project, tool_state)
            print_ok(f"{'Reset' if is_unset_value(args.value) else 'Set'} {setting_name} for {self.name}")
            print_key_value("Value", self.render_setting_value(setting, tool_state[setting.state_key]))
            return 0
        if setting_name == "target-url" and not is_unset_value(args.value):
            if tool_state.get("search_domain") or tool_state.get("search_keywords"):
                tool_state["search_domain"] = None
                tool_state["search_keywords"] = []
                tool_state["active_target_url"] = None
                tool_state["snapshots"] = []
                tool_state["downloads"] = []
                print_warn("Switched to Exact URL mode; cleared domain keyword search settings and previous results.")
        return super().set(project_name, project, args)

    def project_summary(self, project: dict) -> str:
        state = self.ensure_state(project)
        return (
            f"parser={state.get('parser') or '-'} "
            f"policy={state.get('capture_policy') or self.default_capture_policy} "
            f"snapshots={len(state.get('snapshots', []))} "
            f"selected_fw={sum(len(item.get('selected_firmware_links', [])) for item in state.get('snapshots', []))} "
            f"downloads={len(state.get('downloads', []))}"
        )

    def root_summary(self, project: dict) -> str:
        state = self.ensure_state(project)
        return f"{self.name}:s={len(state.get('snapshots', []))}"


PLUGIN_CLASS = WaybackPlugin
