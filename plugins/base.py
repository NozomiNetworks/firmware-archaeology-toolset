"""Shared plugin framework for the Firmware Archaeology Toolset.

This module defines the core extension surface used by FAT plugins:

- `PluginContext` carries normalized runtime settings into plugin code.
- `SurfacePluginBase` implements the standard stateful workflow used by most
  source plugins.

Plugin authors should usually start with `SurfacePluginBase` and override only
`run()` and `download()`. More complex plugins may also override parser,
validation, or command handlers, but the default path is intentionally small.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fatlib.http import (
    USER_AGENT,
    download_file as shared_download_file,
    fetch_json_no_redirect as shared_fetch_json_no_redirect,
    fetch_json_with_headers as shared_fetch_json_with_headers,
    fetch_text as shared_fetch_text,
)
from fatlib.search import (
    DEFAULT_CACHE_TTL,
    DEFAULT_MAX_ENRICHMENTS,
    DEFAULT_MAX_REQUESTS,
    DEFAULT_REQUEST_DELAY,
)
from fatlib.paths import (
    DEFAULT_PROJECT_OUTPUT_DIR,
    DEFAULT_PROJECT_TIMEOUT,
    project_downloads_dir,
    project_output_dir,
    project_timeout,
)

STATE_FILE = Path(".fat.json")
RUN_HISTORY_LIMIT = 5
RUN_HISTORY_TOP_ARTIFACTS = 5
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BLUE = "\033[34m"
ANSI_CYAN = "\033[36m"
UNSET_VALUE_TOKENS = {"none", "null", '""', "''"}
_TERMINAL_ESCAPE_SEQUENCES = re.compile(
    r"(?:\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])|\x9b[0-?]*[ -/]*[@-~])"
)
_TERMINAL_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")

__all__ = [
    "PluginContext",
    "SurfacePluginBase",
    "classify_match",
    "download_file",
    "fetch_json",
    "fetch_json_no_redirect",
    "fetch_json_with_headers",
    "fetch_text",
    "is_unset_value",
    "merge_named_results",
    "parse_keywords",
    "safe_name",
    "write_json",
]

__pdoc__ = {
    "SurfacePluginBase.analyze": False,
    "SurfacePluginBase.build_context": False,
    "SurfacePluginBase.clear_selection": False,
    "SurfacePluginBase.download_command": False,
    "SurfacePluginBase.options": False,
    "SurfacePluginBase.project_summary": False,
    "SurfacePluginBase.root_summary": False,
    "SurfacePluginBase.search": False,
    "SurfacePluginBase.select": False,
    "SurfacePluginBase.status": False,
}


@dataclass
class PluginContext:
    """Normalized runtime settings passed to plugin logic.

    The context is created by `SurfacePluginBase.build_context()` after FAT has
    merged project defaults, persisted plugin options, and command-line flags.
    Plugins should treat this object as read-only for the duration of a run.
    """

    keywords: list[str]
    per_query: int
    timeout: int
    min_match_keywords: int
    session_id: str
    verbose: bool
    state_file: Path
    api_base: str | None = None
    max_requests: int = DEFAULT_MAX_REQUESTS
    max_enrichments: int = DEFAULT_MAX_ENRICHMENTS
    request_delay: float = DEFAULT_REQUEST_DELAY
    cache_ttl: int = DEFAULT_CACHE_TTL
    cache_dir: Path | None = None

    def queries(self) -> list[str]:
        """Return a stable, deduplicated list of query strings.

        Standard surface plugins search once per keyword. This helper removes
        blanks and preserves the first occurrence ordering so that reports and
        run history stay deterministic.
        """

        queries: list[str] = []
        for keyword in self.keywords:
            if keyword and keyword not in queries:
                queries.append(keyword)
        deduped: list[str] = []
        for query in queries:
            if query not in deduped:
                deduped.append(query)
        return deduped


@dataclass(frozen=True)
class PluginSetting:
    """Declarative definition for one persisted plugin setting."""

    name: str
    state_key: str
    label: str
    default_factory: Any
    parse_value: Any = None
    render_value: Any = None


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {"version": 1}
    return json.loads(raw)


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def ensure_state_shape(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("version", 1)
    state.setdefault("current", {})
    state["current"].setdefault("artifacts", [])
    state["current"].setdefault("selected_artifact_ids", [])
    state["current"].setdefault("downloads", [])
    return state


def color_enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def strip_control_chars(text: Any) -> str:
    """Remove terminal control characters and escape sequences except tabs."""

    without_escapes = _TERMINAL_ESCAPE_SEQUENCES.sub("", str(text))
    return _TERMINAL_CONTROL_CHARS.sub("", without_escapes)


def style(text: str, *codes: str) -> str:
    text = strip_control_chars(text)
    if not color_enabled() or not codes:
        return text
    return f"{''.join(codes)}{text}{ANSI_RESET}"


def print_section(title: str) -> None:
    print(style(title, ANSI_BOLD, ANSI_CYAN))


def print_key_value(label: str, value: str) -> None:
    print(f"{style(label + ':', ANSI_BOLD):<24} {strip_control_chars(value)}")


def print_step(message: str) -> None:
    print(style(f"[*] {message}", ANSI_BLUE))


def print_ok(message: str) -> None:
    print(style(f"[+] {message}", ANSI_GREEN))


def print_warn(message: str) -> None:
    print(style(f"[!] {message}", ANSI_YELLOW))


def print_error(message: str) -> None:
    print(style(f"[x] {message}", ANSI_RED, ANSI_BOLD))


def default_downloads_dir(plugin_name: str, project_name: str) -> str:
    """Return the default downloads directory for one plugin/project pair."""

    return f"{DEFAULT_PROJECT_OUTPUT_DIR}/downloads/{safe_name(project_name)}/{plugin_name}"


def new_session_id(project_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{normalize_slug(project_name)}-{stamp}"


def normalize(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def keyword_variants(keyword: str) -> list[str]:
    base = normalize(keyword)
    compact = base.replace(" ", "")
    variants = {base, compact}
    for suffix in ("sdk", "api", "ai"):
        if compact.endswith(suffix) and len(compact) > len(suffix) + 1:
            variants.add(f"{compact[:-len(suffix)]} {suffix}")
    return [variant for variant in variants if variant]


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    haystack = normalize(text)
    token_set = set(haystack.split())
    compact_haystack = haystack.replace(" ", "")
    matched = []
    for keyword in keywords:
        for variant in keyword_variants(keyword):
            if " " in variant:
                variant_tokens = variant.split()
                if variant in haystack or all(token in token_set for token in variant_tokens):
                    matched.append(keyword)
                    break
            else:
                if variant in token_set or variant in compact_haystack:
                    matched.append(keyword)
                    break
    return matched


def classify_match(
    keywords: list[str],
    min_match_keywords: int,
    name: str,
    description: str,
    extra_text: str = "",
) -> tuple[str | None, list[str], list[str]]:
    """Classify one candidate artifact against the project's keyword set.

    Returns a tuple containing:

    - a confidence label such as `high` or `review`
    - the matched project keywords
    - normalized signals explaining why the candidate matched
    """

    haystack = f"{name} {description} {extra_text}"
    hits = matched_keywords(haystack, keywords)
    signals: list[str] = []

    if hits:
        signals.extend(f"keyword:{keyword}" for keyword in hits)
    if extra_text.strip():
        signals.append("extra-text")

    if len(hits) >= min_match_keywords:
        return "high", hits, signals
    if hits:
        return "review", hits, signals
    return None, hits, signals


def fetch_text(url: str, timeout: int, accept: str) -> str:
    """Fetch a text resource with the shared FAT user agent."""

    return shared_fetch_text(url, timeout=timeout, accept=accept)


def fetch_json(url: str, timeout: int) -> Any:
    """Fetch and decode a JSON resource with the shared FAT user agent."""

    return json.loads(fetch_text(url, timeout=timeout, accept="application/json"))


def fetch_json_with_headers(url: str, timeout: int) -> dict[str, Any]:
    """Fetch JSON and normalized response headers.

    Returns a mapping with ``payload`` containing the decoded JSON value and
    ``headers`` containing response header names and values. Source plugins
    can inspect provider pagination headers such as GitLab's ``X-Next-Page``.
    """

    payload, headers = shared_fetch_json_with_headers(url, timeout=timeout)
    return {"payload": payload, "headers": headers}


def fetch_json_no_redirect(url: str, timeout: int) -> Any:
    """Fetch decoded JSON without following HTTP redirects."""

    return shared_fetch_json_no_redirect(url, timeout=timeout)


def print_result_table(plugin_name: str, results: list[dict[str, Any]], *, max_rows: int = 12) -> None:
    print(style(f"{plugin_name.upper()} results", ANSI_BOLD, ANSI_CYAN))
    print(style("Conf  Hits  Name                                      Queries", ANSI_BOLD))
    print(style("----  ----  ----------------------------------------  -------------------------", ANSI_DIM))
    for result in results[:max_rows]:
        hits = len(result.get("matched_keywords", []))
        queries = strip_control_chars(
            ", ".join(result.get("matched_queries", [result.get("query", "")]))
        )[:25]
        name = strip_control_chars(result.get("name", ""))[:40]
        confidence = strip_control_chars(result.get("confidence", ""))
        print(f"{confidence:<4}  {hits:>4}  {name:<40}  {queries}")
    if len(results) > max_rows:
        print(style(f"... {len(results) - max_rows} more result(s) only in generated reports and state", ANSI_DIM))


def print_artifact_table(artifacts: list[dict[str, Any]], selected_ids: set[str]) -> None:
    print(style("Sel  ID   Plugin     Conf  Hits  Name", ANSI_BOLD))
    print(style("---  ---  ---------  ----  ----  ----------------------------------------", ANSI_DIM))
    for artifact in artifacts:
        marker = "*" if artifact["artifact_id"] in selected_ids else " "
        print(
            f" {marker}  {artifact['artifact_num']:>3}  {strip_control_chars(artifact['plugin']):<9}  "
            f"{strip_control_chars(artifact['confidence']):<4}  {len(artifact.get('matched_keywords', [])):>4}  "
            f"{strip_control_chars(artifact['name'])[:40]}"
        )


def safe_name(text: str) -> str:
    """Convert arbitrary text into a filesystem-safe slug."""

    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower()
    return slug if slug.strip(".") else "artifact"


def parse_keywords(raw_keywords: str) -> list[str]:
    """Parse a comma-separated keyword string into normalized unique values."""

    values: list[str] = []
    for part in raw_keywords.split(","):
        keyword = " ".join(part.strip().split()).lower()
        if keyword and keyword not in values:
            values.append(keyword)
    return values


def is_unset_value(value: object) -> bool:
    """Return `True` when a CLI value should reset a setting."""

    if value is None:
        return False
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in UNSET_VALUE_TOKENS or value == ""


def effective_min_match_keywords(requested: int, keywords: list[str]) -> int:
    distinct = len({keyword for keyword in keywords if keyword})
    if distinct == 0:
        return max(1, requested)
    return max(1, min(requested, distinct))


def annotate_artifacts(plugin_report: dict[str, Any], selected_ids: set[str]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for next_num, result in enumerate(plugin_report["results"], start=1):
        artifact = dict(result)
        artifact_id = f"{artifact['plugin']}:{artifact['name']}"
        artifact["artifact_id"] = artifact_id
        artifact["artifact_num"] = next_num
        artifact["selected"] = artifact_id in selected_ids
        artifacts.append(artifact)
    return artifacts


def artifact_index(artifacts: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    by_num = {artifact["artifact_num"]: artifact for artifact in artifacts}
    return by_id, by_num


def merge_named_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate artifact candidates by `name`.

    Queries, matched keywords, and signals are unioned. The descriptive fields
    from the strongest individual match are kept, where strength is determined
    first by keyword-hit count and then by confidence label.
    """

    confidence_rank = {"high": 3, "review": 2, "catalog": 1, "error": 0}
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        current = merged.get(result["name"])
        if current is None:
            result["matched_queries"] = [result["query"]]
            merged[result["name"]] = result
            continue
        current_hit_count = len(current.get("matched_keywords", []))
        result_hit_count = len(result.get("matched_keywords", []))
        result_is_stronger = (
            result_hit_count > current_hit_count
            or (
                result_hit_count == current_hit_count
                and confidence_rank.get(str(result.get("confidence")), -1) > confidence_rank.get(str(current.get("confidence")), -1)
            )
        )
        current["matched_keywords"] = sorted(set(current["matched_keywords"]) | set(result["matched_keywords"]))
        current["signals"] = sorted(set(current["signals"]) | set(result["signals"]))
        current["matched_queries"] = sorted(set(current["matched_queries"]) | {result["query"]})
        if result_is_stronger:
            current["description"] = result["description"]
            current["confidence"] = result["confidence"]
            current["url"] = result["url"]
    return sorted(merged.values(), key=lambda item: (-len(item["matched_keywords"]), item["name"]))


def resolve_selection(selection: str, artifacts: list[dict[str, Any]], selected_ids: set[str]) -> list[dict[str, Any]]:
    by_id, by_num = artifact_index(artifacts)
    if selection == "selected":
        return [by_id[artifact_id] for artifact_id in selected_ids if artifact_id in by_id]
    if selection == "all":
        return artifacts
    chosen: list[dict[str, Any]] = []
    for piece in selection.split(","):
        token = piece.strip()
        if not token:
            continue
        if token.isdigit() and int(token) in by_num:
            chosen.append(by_num[int(token)])
        elif token in by_id:
            chosen.append(by_id[token])
        else:
            raise SystemExit(f"Unknown artifact selection: {token}")
    return chosen


def require_no_extra_args(plugin_name: str, command: str, selection: str, value: Any) -> None:
    """Validate commands that do not accept extra positional arguments."""

    if selection != "selected" or value:
        raise SystemExit(f"`{plugin_name} {command}` does not accept extra positional arguments.")


def require_selection_only(plugin_name: str, command: str, selection: str, value: Any, *, message: str) -> None:
    """Validate commands that require one selector and no trailing value."""

    if selection == "selected" or value:
        raise SystemExit(message)


def require_optional_selection(plugin_name: str, command: str, value: Any, *, message: str) -> None:
    """Validate commands that accept one optional selector but no trailing value."""

    if value:
        raise SystemExit(message)


def require_setting_assignment(plugin_name: str, selection: str, value: Any) -> None:
    """Validate `set <name> <value>` style commands."""

    if selection == "selected" or value is None:
        raise SystemExit(f"`{plugin_name} set` requires `set <attribute> <value>`.")


def search_setting_definitions() -> list[PluginSetting]:
    """Return shared bounded-search settings for source plugins."""

    return [
        PluginSetting(
            name="max-requests",
            state_key="max_requests",
            label="Max search requests",
            default_factory=lambda _project, _tool_state: DEFAULT_MAX_REQUESTS,
            parse_value=lambda raw, _project, _tool_state: max(1, int(raw)),
        ),
        PluginSetting(
            name="max-enrichments",
            state_key="max_enrichments",
            label="Max README enrichments",
            default_factory=lambda _project, _tool_state: DEFAULT_MAX_ENRICHMENTS,
            parse_value=lambda raw, _project, _tool_state: max(0, int(raw)),
        ),
        PluginSetting(
            name="request-delay",
            state_key="request_delay",
            label="Request delay",
            default_factory=lambda _project, _tool_state: DEFAULT_REQUEST_DELAY,
            parse_value=lambda raw, _project, _tool_state: max(0.0, float(raw)),
            render_value=lambda value: f"{float(value):g}s",
        ),
        PluginSetting(
            name="cache-ttl",
            state_key="cache_ttl",
            label="Search cache TTL",
            default_factory=lambda _project, _tool_state: DEFAULT_CACHE_TTL,
            parse_value=lambda raw, _project, _tool_state: max(0, int(raw)),
            render_value=lambda value: "disabled" if int(value) == 0 else f"{int(value)}s",
        ),
    ]


class SurfacePluginBase:
    """Default implementation for FAT's stateful plugin workflow.

    Most plugins should inherit from this class. The base provides:

    - standard command parsing for `status`, `options`, `search`, `select`,
      `clear-selection`, `download`, and `set`
    - per-project plugin state initialization
    - structured state persistence and run history tracking
    - selection bookkeeping and download orchestration

    Subclasses typically override only:

    - `name`
    - `help_summary`
    - `help_examples`
    - `run()`
    - `download()`

    Plugins with substantially different workflows may override command
    handlers or argument parsing, as the `wayback` and `scraper` package
    plugins do.
    """

    name = ""
    help_summary = ""
    help_examples = ""
    default_per_query = 10
    default_min_match_keywords = 2

    def setting_definitions(self, project: dict[str, Any], tool_state: dict[str, Any]) -> list[PluginSetting]:
        """Return the persisted setting definitions supported by this plugin."""

        return [
            PluginSetting(
                name="downloads-dir",
                state_key="downloads_dir",
                label="Downloads dir",
                default_factory=lambda _project, _tool_state: project_downloads_dir(_project, self.name),
            ),
            PluginSetting(
                name="per-query",
                state_key="per_query",
                label="Per query",
                default_factory=lambda _project, _tool_state: self.default_per_query,
                parse_value=lambda raw, _project, _tool_state: max(1, int(raw)),
            ),
            PluginSetting(
                name="min-match-keywords",
                state_key="min_match_keywords",
                label="Min keyword hits",
                default_factory=lambda _project, _tool_state: self.default_min_match_keywords,
                parse_value=lambda raw, _project, _tool_state: max(1, int(raw)),
            ),
        ]

    def setting_map(self, project: dict[str, Any], tool_state: dict[str, Any]) -> dict[str, PluginSetting]:
        """Return a lookup map for this plugin's setting definitions."""

        return {setting.name: setting for setting in self.setting_definitions(project, tool_state)}

    def resolve_setting_default(self, setting: PluginSetting, project: dict[str, Any], tool_state: dict[str, Any]) -> Any:
        """Resolve the default value for one setting definition."""

        return setting.default_factory(project, tool_state)

    def parse_setting_value(self, setting: PluginSetting, raw_value: str, project: dict[str, Any], tool_state: dict[str, Any]) -> Any:
        """Parse one CLI value according to the setting definition."""

        parser = setting.parse_value
        if parser is None:
            return raw_value
        return parser(raw_value, project, tool_state)

    def render_setting_value(self, setting: PluginSetting, value: Any) -> str:
        """Render one persisted setting value for terminal output."""

        renderer = setting.render_value
        if renderer is None:
            return str(value)
        return str(renderer(value))

    def option_rows(self, project_name: str, project: dict[str, Any], tool_state: dict[str, Any]) -> list[tuple[str, str]]:
        """Return the standard option rows shown by `options()`."""

        rows = [
            ("Project", project_name),
            ("Tool", self.name),
            ("Keywords", ", ".join(project.get("keywords", [])) or "-"),
            ("Output dir", str(project_output_dir(project))),
        ]
        for setting in self.setting_definitions(project, tool_state):
            value = tool_state.get(setting.state_key, self.resolve_setting_default(setting, project, tool_state))
            rows.append((setting.label, self.render_setting_value(setting, value)))
        rows.extend(
            [
                ("Timeout", str(project_timeout(project))),
                ("Run history limit", str(RUN_HISTORY_LIMIT)),
            ]
        )
        return rows

    def build_arg_parser(self) -> argparse.ArgumentParser:
        """Build the default per-plugin argument parser.

        The default command model supports one optional positional selector and
        one optional value, which are interpreted differently depending on the
        selected command.
        """

        parser = argparse.ArgumentParser(
            description=f"Stateful {self.name} workflow runner.",
            formatter_class=argparse.RawTextHelpFormatter,
            epilog=self.help_examples,
        )
        parser.add_argument("selection", nargs="?", default="selected", help="Command-specific selector or setting name.")
        parser.add_argument("value", nargs="?", default=None, help="Command-specific value.")
        parser.add_argument("--quiet", action="store_true", help="Suppress progress and table output.")
        return parser

    def parse_args(self, argv: list[str], commands: AbstractSet[str]):
        """Parse plugin-local arguments after FAT resolves the command name.

        Override this only when the plugin needs a custom command grammar.
        """

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
        """Validate the positional shape required by the selected command.

        Override this together with `parse_args()` for non-standard workflows.
        """

        command = args.command
        selection = args.selection
        value = args.value

        if command in {"search", "status", "options", "clear-selection"}:
            require_no_extra_args(self.name, command, selection, value)
            return

        if command == "select":
            require_selection_only(
                self.name,
                command,
                selection,
                value,
                message=f"`{self.name} select` requires an artifact selection like `1,2` and no extra trailing value.",
            )
            return

        if command == "download":
            require_optional_selection(
                self.name,
                command,
                value,
                message=f"`{self.name} download` accepts at most one selector such as `selected`, `all`, or `1,2`.",
            )
            return

        if command == "set":
            require_setting_assignment(self.name, selection, value)
            return

    def ensure_state(self, project: dict[str, Any]) -> dict[str, Any]:
        """Ensure the plugin's persisted state block exists for one project.

        The returned dictionary lives under
        `surface_projects.<project>.plugins.<plugin_name>` in the state file.
        Subclasses may extend this shape, but should keep custom keys scoped to
        their own plugin namespace.
        """

        plugins = project.setdefault("plugins", {})
        defaults = {
            "tool": self.name,
            "report_data": {},
            "run_history": [],
            "artifacts": [],
            "selected_artifact_ids": [],
            "downloads": [],
        }
        for setting in self.setting_definitions(project, {}):
            defaults[setting.state_key] = self.resolve_setting_default(setting, project, {})
        plugins.setdefault(self.name, defaults)
        tool_state = plugins[self.name]
        tool_state.setdefault("tool", self.name)
        tool_state.setdefault("report_data", {})
        tool_state.setdefault("run_history", [])
        tool_state.setdefault("artifacts", [])
        tool_state.setdefault("selected_artifact_ids", [])
        tool_state.setdefault("downloads", [])
        for setting in self.setting_definitions(project, tool_state):
            tool_state.setdefault(setting.state_key, self.resolve_setting_default(setting, project, tool_state))
        return tool_state

    def require_inputs(self, project: dict[str, Any]) -> None:
        """Validate that the project has the inputs needed for `search()`.

        Standard surface plugins require project keywords. Plugins with a
        different model, such as the website-oriented scraper, can override
        this method.
        """

        if not project.get("keywords"):
            raise SystemExit(
                "No keywords configured for this project. "
                "Set them with `project <name> --keywords \"k1,k2\"` "
                "or `project set keywords \"k1,k2\"`."
            )

    def build_context(self, project_name: str, project: dict[str, Any], args, tool_state: dict[str, Any]) -> PluginContext:
        """@private

        Construct the runtime context passed to plugin implementation code.
        """

        keywords = project.get("keywords", [])
        return PluginContext(
            keywords=keywords,
            per_query=int(tool_state.get("per_query", self.default_per_query) or self.default_per_query),
            timeout=project_timeout(project),
            min_match_keywords=effective_min_match_keywords(
                int(tool_state.get("min_match_keywords", self.default_min_match_keywords) or self.default_min_match_keywords),
                keywords,
            ),
            session_id=new_session_id(project_name),
            verbose=not args.quiet,
            state_file=args.state_file,
            api_base=str(tool_state.get("api_base") or "").rstrip("/") or None,
            max_requests=int(tool_state.get("max_requests", DEFAULT_MAX_REQUESTS) or DEFAULT_MAX_REQUESTS),
            max_enrichments=max(0, int(tool_state.get("max_enrichments", DEFAULT_MAX_ENRICHMENTS) or 0)),
            request_delay=max(0.0, float(tool_state.get("request_delay", DEFAULT_REQUEST_DELAY) or 0)),
            cache_ttl=max(0, int(tool_state.get("cache_ttl", DEFAULT_CACHE_TTL) or 0)),
            cache_dir=project_output_dir(project) / ".fat-cache" / self.name,
        )

    def run(self, context: PluginContext) -> dict[str, Any]:
        """Collect and normalize artifacts for one search session.

        Subclasses must return a plugin report with this shape:

        - `name`
        - `status`
        - `searches`
        - `errors`
        - `results`

        Each item in `results` should use the normalized artifact schema
        described in `plugins/README.md`.
        """

        raise NotImplementedError(f"{self.__class__.__name__}.run() must be implemented by the plugin")

    def download(self, context: PluginContext, artifact: dict[str, Any], downloads_dir: Path) -> dict[str, Any]:
        """Retrieve one selected artifact into the plugin's downloads directory.

        The return value is persisted into download history. Implementations
        should return at least a `path` field and may include additional
        metadata such as file names or extracted paths.
        """

        raise NotImplementedError(f"{self.__class__.__name__}.download() must be implemented by the plugin")

    def analyze(self, context: PluginContext, project_name: str, selected_ids: AbstractSet[str]) -> dict[str, Any]:
        """@private

        Run collection and wrap the plugin report in the shared structured envelope.
        """

        self.print_search_header(context, project_name)
        plugin_report = self.run(context)
        return self.build_persisted_report(context, project_name, selected_ids, plugin_report)

    def print_search_header(
        self,
        context: PluginContext,
        project_name: str,
        *,
        extra_fields: list[tuple[str, str]] | None = None,
    ) -> None:
        """@private

        Print the standard search session header when verbose output is enabled.
        """

        if not context.verbose:
            return
        print_section("Firmware Archaeology Toolset")
        print_key_value("Run", context.session_id)
        print_key_value("Project", project_name)
        print_key_value("Tool", self.name)
        print_key_value("Keywords", ", ".join(context.keywords))
        print_key_value("Per query", str(context.per_query))
        print_key_value("Min keyword hits", str(context.min_match_keywords))
        for label, value in extra_fields or []:
            print_key_value(label, value)
        print_key_value("State file", str(context.state_file))
        print("")

    def build_persisted_report(
        self,
        context: PluginContext,
        project_name: str,
        selected_ids: AbstractSet[str],
        plugin_report: dict[str, Any],
        *,
        keywords: list[str] | None = None,
        per_query: int | None = None,
        min_match_keywords: int | None = None,
    ) -> dict[str, Any]:
        """@private

        Build the persisted report envelope stored in plugin state.
        """

        report_keywords = list(context.keywords if keywords is None else keywords)
        report_per_query = context.per_query if per_query is None else per_query
        report_min_hits = context.min_match_keywords if min_match_keywords is None else min_match_keywords
        artifacts = annotate_artifacts(plugin_report, selected_ids)
        return {
            "project": project_name,
            "tool": self.name,
            "session": {
                "id": context.session_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "state_file": str(context.state_file),
            },
            "state": {
                "project": project_name,
                "tool": self.name,
                "keywords": report_keywords,
                "per_query": report_per_query,
                "timeout": context.timeout,
                "min_match_keywords": report_min_hits,
                "selected_artifact_ids": sorted(selected_ids),
            },
            "keywords": report_keywords,
            "plugins": [plugin_report],
            "artifacts": artifacts,
        }

    def persist_search_state(
        self,
        project: dict[str, Any],
        tool_state: dict[str, Any],
        context: PluginContext,
        report: dict[str, Any],
        selected_ids: AbstractSet[str],
        *,
        extra_updates: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """@private

        Persist a search report into plugin state and retain valid selections.
        """

        artifact_ids = {artifact["artifact_id"] for artifact in report["artifacts"]}
        retained_selected = sorted(artifact_id for artifact_id in selected_ids if artifact_id in artifact_ids)
        updated_state = {
            "tool": self.name,
            "min_match_keywords": context.min_match_keywords,
            "per_query": context.per_query,
            "downloads_dir": tool_state.get("downloads_dir", project_downloads_dir(project, self.name)),
            "report_data": report,
            "artifacts": report["artifacts"],
            "selected_artifact_ids": retained_selected,
            "downloads": tool_state.get("downloads", []),
        }
        if extra_updates:
            updated_state.update(extra_updates)
        tool_state.update(updated_state)
        return report["plugins"][0], retained_selected

    def append_search_history(
        self,
        project: dict[str, Any],
        tool_state: dict[str, Any],
        context: PluginContext,
        plugin_report: dict[str, Any],
        retained_selected: list[str],
        artifacts: list[dict[str, Any]],
        *,
        extra_record: dict[str, Any] | None = None,
    ) -> None:
        """@private

        Append the standard persisted run-history record for a search session.
        """

        record = {
            "id": context.session_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "tool": self.name,
            "keywords": project["keywords"],
            "per_query": context.per_query,
            "timeout": context.timeout,
            "min_match_keywords": context.min_match_keywords,
            "output_dir": str(project_output_dir(project)),
            "status": plugin_report["status"],
            "result_count": len(plugin_report["results"]),
            "error_count": len(plugin_report["errors"]),
            "selected_count": len(retained_selected),
            "top_artifacts": summarize_artifacts_for_history(artifacts),
        }
        if plugin_report.get("request_stats"):
            record["request_stats"] = plugin_report["request_stats"]
        if extra_record:
            record.update(extra_record)
        append_run_history(tool_state, record)

    def print_search_summary(self, context: PluginContext, plugin_report: dict[str, Any], artifacts: list[dict[str, Any]], retained_selected: list[str], state_file: Path) -> None:
        """@private

        Print the standard verbose summary after a search session completes.
        """

        if not context.verbose:
            return
        print("")
        print_section("Artifacts")
        print_artifact_table(artifacts, set(retained_selected))
        print("")
        print_section("Summary")
        print_key_value(plugin_report["name"], f"status={plugin_report['status']} results={len(plugin_report['results'])} errors={len(plugin_report['errors'])}")
        print_key_value("Artifacts", str(len(artifacts)))
        print_key_value("Selected", str(len(retained_selected)))
        print_key_value("State", str(state_file))
        print("")

    def finalize_search(
        self,
        project_name: str,
        project: dict[str, Any],
        tool_state: dict[str, Any],
        context: PluginContext,
        selected_ids: AbstractSet[str],
        plugin_report: dict[str, Any],
        *,
        keywords: list[str] | None = None,
        per_query: int | None = None,
        min_match_keywords: int | None = None,
        extra_updates: dict[str, Any] | None = None,
        extra_record: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        """@private

        Persist one completed search session and render the shared summary.
        """

        report = self.build_persisted_report(
            context,
            project_name,
            selected_ids,
            plugin_report,
            keywords=keywords,
            per_query=per_query,
            min_match_keywords=min_match_keywords,
        )
        plugin_report, retained_selected = self.persist_search_state(
            project,
            tool_state,
            context,
            report,
            selected_ids,
            extra_updates=extra_updates,
        )
        self.append_search_history(
            project,
            tool_state,
            context,
            plugin_report,
            retained_selected,
            report["artifacts"],
            extra_record=extra_record,
        )
        self.print_search_summary(context, plugin_report, report["artifacts"], retained_selected, context.state_file)
        return plugin_report, report["artifacts"], retained_selected

    def search(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Execute the standard search workflow and persist the resulting state.
        """

        self.require_inputs(project)
        tool_state = self.ensure_state(project)
        project_output_dir(project).mkdir(parents=True, exist_ok=True)
        selected_ids = set(tool_state.get("selected_artifact_ids", []))
        context = self.build_context(project_name, project, args, tool_state)
        if context.verbose:
            print_step("starting collection session")
        plugin_report = self.run(context)
        self.finalize_search(
            project_name,
            project,
            tool_state,
            context,
            selected_ids,
            plugin_report,
        )
        print_ok(f"Updated state for {self.name}")
        return 0

    def status(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Render the persisted status view for this plugin.
        """

        tool_state = self.ensure_state(project)
        artifacts = tool_state.get("artifacts", [])
        selected_ids = set(tool_state.get("selected_artifact_ids", []))
        run_history = tool_state.get("run_history", [])
        print_section("Surface State")
        print_key_value("Project", project.get("name", "-"))
        print_key_value("Tool", tool_state.get("tool", "-"))
        print_key_value("Keywords", ", ".join(project.get("keywords", [])) or "-")
        print_key_value("Artifacts", str(len(artifacts)))
        print_key_value("Selected", str(len(selected_ids)))
        print_key_value("Recent runs", str(len(run_history)))
        print("")
        if artifacts:
            print_artifact_table(artifacts, selected_ids)
        else:
            print_warn("No artifacts in state. Run `search` first.")
        if run_history:
            print("")
            print_section("Recent Runs")
            print(style("When                 Status  Results  Selected  Top artifacts", ANSI_BOLD))
            print(style("-------------------  ------  -------  --------  ----------------------------------------", ANSI_DIM))
            for item in reversed(run_history[-3:]):
                top_names = ", ".join(artifact["name"] for artifact in item.get("top_artifacts", [])[:2]) or "-"
                print(
                    f"{item.get('completed_at', '-')[:19]:<19}  "
                    f"{item.get('status', '-'):<6}  "
                    f"{item.get('result_count', 0):>7}  "
                    f"{item.get('selected_count', 0):>8}  "
                    f"{top_names[:40]}"
                )
        return 0

    def options(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Render persisted plugin options for the current project.
        """

        tool_state = self.ensure_state(project)
        print_section("Plugin Options")
        for label, value in self.option_rows(project_name, project, tool_state):
            print_key_value(label, value)
        return 0

    def select(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Update the selected artifact set using artifact numbers or ids.
        """

        tool_state = self.ensure_state(project)
        artifacts = tool_state.get("artifacts", [])
        if not artifacts:
            raise SystemExit("No artifacts in state. Run `search` first.")
        chosen = resolve_selection(args.selection, artifacts, set(tool_state.get("selected_artifact_ids", [])))
        selected_ids = sorted({artifact["artifact_id"] for artifact in chosen})
        tool_state["selected_artifact_ids"] = selected_ids
        for artifact in tool_state["artifacts"]:
            artifact["selected"] = artifact["artifact_id"] in selected_ids
        print_ok(f"Selected {len(selected_ids)} artifact(s)")
        print_artifact_table(tool_state["artifacts"], set(selected_ids))
        return 0

    def clear_selection(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Clear the current artifact selection for this plugin.
        """

        tool_state = self.ensure_state(project)
        tool_state["selected_artifact_ids"] = []
        for artifact in tool_state.get("artifacts", []):
            artifact["selected"] = False
        print_ok("Cleared artifact selection")
        return 0

    def download_command(self, project_name: str, project: dict[str, Any], args) -> int:
        """@private

        Resolve the requested selection and invoke `download()` per artifact.
        """

        tool_state = self.ensure_state(project)
        artifacts = tool_state.get("artifacts", [])
        selected_ids = set(tool_state.get("selected_artifact_ids", []))
        if not artifacts:
            raise SystemExit("No artifacts in state. Run `search` first.")
        chosen = resolve_selection(args.selection, artifacts, selected_ids)
        if not chosen:
            raise SystemExit("No artifacts selected for download.")
        downloads_dir = Path(tool_state.get("downloads_dir", project_downloads_dir(project, self.name)))
        downloads_dir.mkdir(parents=True, exist_ok=True)
        context = self.build_context(project_name, project, args, tool_state)
        downloads = tool_state.get("downloads", [])
        for artifact in chosen:
            if context.verbose:
                print_step(f"downloading {artifact['artifact_id']}")
            try:
                result = self.download(context, artifact, downloads_dir)
            except Exception as exc:
                downloads.append({"artifact_id": artifact["artifact_id"], "status": "error", "error": str(exc), "timestamp": datetime.now(timezone.utc).isoformat()})
                print_warn(f"download failed for {artifact['artifact_id']}: {exc}")
                continue
            downloads.append({"artifact_id": artifact["artifact_id"], **result, "timestamp": datetime.now(timezone.utc).isoformat()})
            print_ok(f"downloaded {artifact['artifact_id']} -> {result['path']}")
        tool_state["downloads"] = downloads
        return 0

    def set(self, project_name: str, project: dict[str, Any], args) -> int:
        """Persist one plugin setting using the shared option model.

        Supported settings in the base implementation are:

        - `downloads-dir`
        - `per-query`
        - `min-match-keywords`

        Subclasses may extend this command for plugin-specific settings and may
        honor shared unset tokens such as `None`, `null`, `""`, and `''`.
        """

        if not args.selection:
            raise SystemExit(f"Missing setting name. Use `{self.name} set <name> <value>`.")
        if args.value is None:
            raise SystemExit(f"Missing setting value. Use `{self.name} set {args.selection} <value>`.")
        tool_state = self.ensure_state(project)
        setting = args.selection.replace("_", "-").lower()
        if setting == "keywords":
            raise SystemExit(
                f"`{self.name} set keywords` is not supported because keywords are project-level. "
                "Use `fat.py set keywords \"k1,k2\"` or `fat.py project set keywords \"k1,k2\"`."
            )
        unset_requested = is_unset_value(args.value)
        definitions = self.setting_map(project, tool_state)
        spec = definitions.get(setting)
        if spec is None:
            raise SystemExit(
                f"Unsupported setting for {self.name}: {args.selection}. "
                f"Use one of `{ '`, `'.join(sorted(definitions)) }`."
            )
        if unset_requested:
            tool_state[spec.state_key] = self.resolve_setting_default(spec, project, tool_state)
        else:
            tool_state[spec.state_key] = self.parse_setting_value(spec, args.value, project, tool_state)
        print_ok(f"{'Reset' if unset_requested else 'Set'} {setting} for {self.name}")
        print_key_value("Value", self.render_setting_value(spec, tool_state[spec.state_key]))
        return 0

    def project_summary(self, project: dict[str, Any]) -> str:
        tool_state = self.ensure_state(project)
        return (
            f"artifacts={len(tool_state.get('artifacts', []))} "
            f"selected={len(tool_state.get('selected_artifact_ids', []))} "
            f"downloads={len(tool_state.get('downloads', []))} "
            f"runs={len(tool_state.get('run_history', []))} "
            f"min_hits={tool_state.get('min_match_keywords', 0)}"
        )

    def root_summary(self, project: dict[str, Any]) -> str:
        tool_state = self.ensure_state(project)
        return f"{self.name}:a={len(tool_state.get('artifacts', []))}"


def download_file(url: str, destination: Path, timeout: int) -> None:
    """Download a file with the shared FAT user agent."""

    shared_download_file(url, destination, timeout=timeout)


def write_json(path: Path, payload: Any) -> None:
    """Write JSON to disk using stable formatting."""

    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


_MARKDOWN_ESCAPES = str.maketrans(
    {
        "\\": "\\\\",
        "|": "\\|",
        "`": "&#96;",
        "[": "\\[",
        "]": "\\]",
        "<": "&lt;",
        ">": "&gt;",
        "&": "&amp;",
    }
)


def escape_markdown(value: Any) -> str:
    """Escape untrusted text so it renders inertly in Markdown reports."""

    text = "".join(ch for ch in str(value) if ch == "\t" or (ch >= " " and not "\x7f" <= ch <= "\x9f"))
    return text.translate(_MARKDOWN_ESCAPES)


def render_human_report(report: dict[str, Any]) -> str:
    plugin = report["plugins"][0]
    lines = [
        f"# Firmware Archaeology Toolset Report",
        "",
        f"- Project: `{escape_markdown(report['project'])}`",
        f"- Tool: `{escape_markdown(report['tool'])}`",
        f"- Session: `{escape_markdown(report['session']['id'])}`",
        f"- Keywords: {', '.join(escape_markdown(keyword) for keyword in report.get('keywords', [])) or '-'}",
        f"- Per query: {escape_markdown(report['state']['per_query'])}",
        f"- Timeout: {escape_markdown(report['state']['timeout'])}",
        f"- Min keyword hits: {escape_markdown(report['state']['min_match_keywords'])}",
        "",
        "## Summary",
        "",
        f"- Plugin status: `{escape_markdown(plugin['status'])}`",
        f"- Searches: {len(plugin.get('searches', []))}",
        f"- Results: {len(plugin.get('results', []))}",
        f"- Errors: {len(plugin.get('errors', []))}",
        "",
        "## Searches",
        "",
    ]

    for search in plugin.get("searches", []):
        mode = f" ({escape_markdown(search['mode'])})" if search.get("mode") else ""
        lines.append(
            f"- `{escape_markdown(search.get('query', '-'))}`{mode}: "
            f"{escape_markdown(search.get('url', '-'))}"
        )

    lines.extend(["", "## Artifacts", ""])
    if report.get("artifacts"):
        lines.append("| ID | Confidence | Hits | Name | URL |")
        lines.append("| --- | --- | ---: | --- | --- |")
        for artifact in report["artifacts"]:
            lines.append(
                f"| {escape_markdown(artifact['artifact_num'])} | {escape_markdown(artifact['confidence'])} | "
                f"{len(artifact.get('matched_keywords', []))} | "
                f"{escape_markdown(artifact['name'])} | {escape_markdown(artifact['url'])} |"
            )
    else:
        lines.append("_No artifacts kept._")

    if plugin.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in plugin["errors"]:
            lines.append(f"- {escape_markdown(error)}")

    return "\n".join(lines) + "\n"


def summarize_artifacts_for_history(artifacts: list[dict[str, Any]], *, limit: int = RUN_HISTORY_TOP_ARTIFACTS) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for artifact in artifacts[:limit]:
        summary.append(
            {
                "artifact_num": artifact["artifact_num"],
                "name": artifact["name"],
                "url": artifact["url"],
                "confidence": artifact["confidence"],
                "keyword_hits": len(artifact.get("matched_keywords", [])),
            }
        )
    return summary


def append_run_history(tool_state: dict[str, Any], record: dict[str, Any], *, limit: int = RUN_HISTORY_LIMIT) -> None:
    history = tool_state.setdefault("run_history", [])
    history.append(record)
    tool_state["run_history"] = history[-limit:]
