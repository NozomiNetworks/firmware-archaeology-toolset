# Plugin Author Guide

This directory contains the public runtime-discovered plugins used by `fat.py`.
Vendor scraper and Wayback parser implementations are loaded from the
configured external plugin directory instead.

Generated API reference for the extension surface can be built with `pdoc`.
See `docs/plugin-api/README.md`.

The loader supports both:

- single-file plugins such as `plugins/dockerhub.py`
- package plugins such as `plugins/wayback/`

## Discovery Rules

The entry point scans `plugins/` under the FAT root directory and imports every module/package except `base`.

A plugin is accepted if it exposes:

- `PLUGIN_CLASS = YourPluginClass`

and that class has a `name` attribute. The loader instantiates the class with
no arguments. `help_summary` and `help_examples` are not required for
discovery, but set them so `fat.py --help` and plugin help render properly.

Shared import/discovery helpers live in `fatlib/discovery.py`. They are used by:

- top-level FAT plugin discovery
- scraper website parser discovery
- Wayback parser module loading

The default external plugin directory is `~/.fat/plugins/`. Configure it with
`plugin_dir` in `~/.fat/config.json`, or override it for one command with
`--plugin-dir PATH`. The private vendor repository's `install.sh` copies files
into this directory.

There are three separate extension contracts in this tree:

- top-level FAT plugins under `plugins/`
  - must expose `PLUGIN_CLASS`
- external vendor scrapers under the configured plugin directory; the public tree has no vendor scraper implementations
  - must expose `SCRAPER_CLASS`
- the public Wayback engine and `httpbin` test parser; vendor parsers are loaded from the configured plugin directory
  - are plain modules
  - must export `extract_links(...)`
  - may export `update_firmware_link(...)`
  - may export `post_download(...)`

Do not mix these contracts. `PLUGIN_CLASS` is only for top-level FAT plugins.

## Base Class

Most plugins should inherit from `SurfacePluginBase` in `plugins/base.py`.

That base already provides:

- project/plugin state wiring
- project-derived default path handling
- shared one-shot HTTP header/request helpers via `fatlib/http.py`
- default command parsing
- shared command-shape validation helpers
- validation for the standard commands
- search/status/options/select/clear-selection/download/set flow
- shared search finalization and run-history persistence
- output rendering

## Plugin Author Checklist

For a normal plugin, use this order:

1. Create `plugins/<name>.py`.
2. Subclass `SurfacePluginBase`.
3. Set:
   - `name`
   - `help_summary`
   - `help_examples`
4. Implement:
   - `run(context)`
   - `download(context, artifact, downloads_dir)`
5. Reuse shared helpers instead of reimplementing them:
   - HTTP: `fatlib/http.py`
   - bounded search requests and cache: `fatlib/search.py`
   - project/output paths: `fatlib/paths.py`
   - discovery: `fatlib/discovery.py`
6. Only override `parse_args()`, `validate_args()`, `set()`, or `options()` if
   the default workflow is genuinely insufficient.
7. Add tests under `tests/` before refactoring or extending the plugin.

## Standard Plugin Shape

A normal surface plugin usually needs to define:

- `name`
- `help_summary`
- `help_examples`
- `run(context)`
- `download(context, artifact, downloads_dir)`

The base class handles the rest.

### Minimal skeleton

This compact skeleton illustrates the plugin contract; its request loop is
intentionally page-one-only. Production source plugins must follow the bounded
pagination guidance below.

```python
from __future__ import annotations

from pathlib import Path

from .base import PluginContext, SurfacePluginBase, download_file, fetch_json
from fatlib.search import SearchPolicy


class ExamplePlugin(SurfacePluginBase):
    name = "example"
    help_summary = "One-line summary for top-level help."
    help_examples = (
        "Examples:\n"
        "  fat.py example options\n"
        "  fat.py example search\n"
    )

    def run(self, context: PluginContext) -> dict:
        results = []
        searches = []
        errors = []

        policy = SearchPolicy.from_context(context, self.name)
        for query in context.queries():
            url = f"https://example.invalid/api/search?q={query}&page=1&per_page={context.per_query}"
            searches.append({"query": query, "page": 1, "url": url})
            try:
                payload = policy.fetch_json(url, timeout=context.timeout, fetcher=fetch_json)
            except Exception as exc:
                errors.append(f"{query}: {exc}")
                continue

            for item in payload.get("results", []):
                results.append(
                    {
                        "plugin": self.name,
                        "kind": "repository",
                        "query": query,
                        "name": item["name"],
                        "url": item["html_url"],
                        "description": item.get("description", ""),
                        "matched_keywords": [query],
                        "signals": ["public_api"],
                        "confidence": "medium",
                    }
                )

        return {
            "name": self.name,
            "status": "ok" if results else "error",
            "searches": searches,
            "errors": errors,
            "results": results,
        }

    def download(self, context: PluginContext, artifact: dict, downloads_dir: Path) -> dict:
        filename = artifact["name"].replace("/", "-") + ".zip"
        destination = downloads_dir / filename
        download_file(artifact["url"], destination, context.timeout)
        return {"status": "downloaded", "path": str(destination), "files": [str(destination)]}


PLUGIN_CLASS = ExamplePlugin
```

Source plugins should paginate through `SearchPolicy` so every query gets its
first page before later pages are requested round-robin. Keep `per_query` as
the page size and `max-requests` as the run-wide request limit. Stop when the
provider indicates exhaustion; retain collected results if a later page
fails. Return per-query diagnostics (`pages_fetched` and a completion or
partial `state`) with the plugin report. Treat malformed pagination metadata
as partial and validate provider-supplied continuation URLs before requesting
them. For APIs that return pagination headers, use `fetch_json_with_headers()`;
it returns `{"payload": ..., "headers": ...}`.

## Normalized Result Schema

Each `run()` implementation must return plugin results using the shared shape:

- `plugin`
- `kind`
- `query`
- `name`
- `url`
- `description`
- `matched_keywords`
- `signals`
- `confidence`

The plugin report itself must contain:

- `name`
- `status`
- `searches`
- `errors`
- `results`

## Standard Commands

If you inherit from `SurfacePluginBase`, your plugin gets these commands:

- `status`
- `options`
- `search`
- `select`
- `clear-selection`
- `download`
- `set`

Bare invocation defaults to `status`.

The `wayback` plugin extends this surface with two extra commands:

- `post`: re-run the parser `post_download` hook for already downloaded artifacts
- `clear`: wipe the plugin's snapshots, selections, download history, and downloaded files

Command names are fixed by the top-level entry point (`SURFACE_COMMANDS` and
the dispatch map in `fat.py`). A plugin can override `parse_args()` to reshape
the grammar inside those commands, as `wayback` does with
`search snapshot` / `search firmware`, but it cannot introduce new command
names without editing `fat.py`.

The default parser also supports:

- `--quiet`

The global `--state-file` option is handled by the main entry point, not by plugins.

## Which Base Hooks To Override

Most plugins only need:

- `run(context)` to produce normalized artifact results
- `download(context, artifact, downloads_dir)` to retrieve selected artifacts

Override these only when needed:

- `setting_definitions()` when the plugin needs extra plugin-local settings
- `parse_args()` when the command grammar is not the standard
  `status/options/search/select/clear-selection/download/set` flow
- `validate_args()` when custom commands or selectors need extra checks
- `status()` or `options()` only when the default tabular output is not enough

If your plugin still fits the standard workflow, prefer extending the base
behavior instead of replacing it.

## Persisted Settings

For normal surface plugins, the base class already supports these `set` keys:

- `downloads-dir`
- `per-query`
- `min-match-keywords`

The GitHub, GitLab, and Docker Hub source plugins also support bounded search
controls:

- `max-requests`: maximum upstream search and enrichment requests in one run
- `max-enrichments`: maximum README enrichments in one run for GitHub and GitLab
- `request-delay`: minimum delay between upstream requests, in seconds
- `cache-ttl`: successful response cache lifetime, in seconds; `0` disables it

`per-query` remains the upstream page size. It does not cap the total number of
results; pagination continues within the configured request budget. Source
plugins request page 1 for every query first, then continue round-robin through
later pages until results are exhausted or a limit/error stops the run. Search
results are collected as metadata first, deduplicated locally, and enriched
only within the configured enrichment budget. A candidate skipped during
search enrichment remains selectable using its metadata. Defaults are 50
requests, 10 enrichments, no delay, and a 3600-second cache lifetime per
plugin search run.
The cache is stored under `output/.fat-cache/<plugin>`; set `cache-ttl` to `0`
for a fresh run or remove that directory to clear stored responses. Request
statistics and provider errors are persisted in the structured plugin report
and rendered in `output/report.md`.

The source plugins perform bounded paginated discovery. Per-query completion
and partial states are recorded in search diagnostics and `output/report.md`.
`max-enrichments` applies to GitHub and GitLab README lookups; Docker Hub has
no README enrichment stage, so that setting has no effect there.

Project-wide settings such as `keywords`, `output-dir`, and `timeout` are managed by `fat.py project ...`, not by plugins.

These settings are now defined declaratively through `setting_definitions()`.
For many plugins, that means:

- no custom `set()` implementation is needed
- `options()` can render from the same setting definitions
- reset/default behavior stays in one place

If the remaining plugin-level settings are sufficient, you do not need to
override `set()` or `options()`.

Declarative example for a simple extra setting:

```python
from .base import PluginSetting, SurfacePluginBase


def default_api_base(project, tool_state):
    return "https://hub.docker.com"


def parse_api_base(raw, project, tool_state):
    return raw.rstrip("/")


def render_optional_value(value):
    return value or "-"


class ExamplePlugin(SurfacePluginBase):
    ...

    def setting_definitions(self, project, tool_state):
        return [
            PluginSetting(
                name="api-base",
                state_key="api_base",
                label="API base",
                default_factory=default_api_base,
                parse_value=parse_api_base,
                render_value=render_optional_value,
            ),
            *super().setting_definitions(project, tool_state),
        ]
```

How this works:

- `default_factory(project, tool_state)` returns the default value when the
  setting is not present in persisted state
- `parse_value(raw, project, tool_state)` converts the CLI string into the
  stored value
- `render_value(value)` controls how the value is shown in `options()`

If the setting is just a normal persisted key, prefer `setting_definitions()`
over a custom `set()` implementation.

If you really need custom `set()` behavior, keep it small and delegate the
standard keys back to the base class:

```python
def set(self, project_name, project, args):
    tool_state = self.ensure_state(project)

    if args.selection == "website":
        tool_state["website"] = args.value.strip()
        print_ok("Set website for scraper")
        print_key_value("Value", tool_state["website"])
        return 0

    return super().set(project_name, project, args)
```

Use that pattern only when the setting logic does not fit the declarative
model cleanly.

## When To Override Base Behavior

Override parser/validation/commands only if your plugin genuinely needs a different workflow.

Wayback is the main example:

- it is a plugin package, not a single file
- it overrides `parse_args()`
- it overrides `validate_args()`
- it has custom settings such as `parser` and `capture-policy`
- it has custom `status()` and `options()` output

Its internal package is intentionally split by concern:

- `plugins/wayback/__init__.py`
  The FAT-facing plugin wrapper and orchestration layer.
- `plugins/wayback/parser_support.py`
  Parser/target resolution helpers and hook loading.
- `plugins/wayback/workflow.py`
  Stateful snapshot/firmware workflow operations.
- `plugins/wayback/render.py`
  Terminal rendering helpers for status, snapshot tables, and scouting output.
- `plugins/wayback/retrieve_archived_firmware.py`
  Archive transport and replay client logic.

Wayback parser modules may also expose an optional post-download hook:

- `post_download(project, snapshot, url, local_path, archive)`

FAT calls it after a firmware file is available locally, including direct file
targets when the parser is selected or inferred from the target host. That hook
is the intended place for follow-on steps such as firmware scouting, triage, or
metadata extraction.

Wayback parser modules may also expose an optional pre-download rewrite hook:

- `update_firmware_link(project, snapshot, url, archive)`

FAT calls it before downloading the firmware. When the hook returns a
replacement URL, FAT downloads that live vendor URL directly instead of
fetching the binary through Wayback.

The bundled `plugins/wayback/parsers/httpbin.py` provides a small end to end
example. It parses the archived httpbin documentation page as a product page,
selects `/bytes/32` as the firmware-like link, and leaves the actual archived
binary download to the shared Wayback workflow.

The documented parser interface for that module-level contract lives in:

- `plugins/wayback/parsers/api.py`
- `plugins/wayback/PARSER_HOWTO.md`

Shared parser-side helpers that should be reused instead of copied live in:

- `plugins/wayback/retrieve_archived_firmware.py`

In particular, Wayback parsers should reuse:

- `ArchiveClient.unwrap_replay_url(...)`

If your plugin is similarly complex, prefer a package plugin:

```text
plugins/yourplugin/
  __init__.py
  helper_a.py
  helper_b.py
  parsers/
```

The `scraper` plugin follows the same package-plugin pattern, but hosts a second layer of website-specific parser modules under:

```text
plugins/scraper/
  __init__.py
  base.py
  registry.py
  websites/
```

Use that pattern when one FAT plugin needs multiple site-specific implementations without introducing a second top-level FAT command per website.

For scraper website parsers specifically:

- the module exports `SCRAPER_CLASS`
- that class must subclass `WebsiteScraperBase`
- this is separate from the top-level plugin `PLUGIN_CLASS` contract

JavaScript rendering is opt-in for trusted parsers. A scraper can declare:

```python
RENDER_MODE = "required"          # static, preferred, or required
RENDER_WAIT_UNTIL = "domcontentloaded"
RENDER_WAIT_FOR = ".download-list" # optional CSS selector
# RENDER_TIMEOUT = 60              # optional seconds override
```

`static` is the default. `preferred` falls back to the existing HTTP fetch if
Playwright or Chromium cannot render a page. `required` reports that failure.
FAT passes rendered HTML strings to the existing `extract_product_links` and
`parse_product` methods, reusing one isolated browser session for a crawl.
The optional browser environment is installed with
`./scripts/install_dependencies.sh --browser`, which installs the
hash-locked `requirements-browser.txt` packages and the Chromium runtime
selected by the pinned Playwright release; it is not required for normal FAT
use.

## State, Output, And Paths

Project-wide settings are stored under:

```text
surface_projects.<project>
```

Plugin-local state lives under:

```text
surface_projects.<project>.plugins.<plugin_name>
```

The default state file is:

```text
.fat.json
```

Project-wide output defaults to:

```text
output/
```

Derived defaults should be reused instead of rebuilt manually:

- project report: `output/report.md`
- plugin downloads: `output/downloads/<project>/<plugin>/...`

Use `fatlib/paths.py` for these derived paths instead of hardcoding directory
layouts inside plugins.

## State Contract

The base class stores per-plugin state under:

```text
surface_projects.<project>.plugins.<plugin_name>
```

For standard surface plugins this includes:

- current artifacts
- selected artifact ids
- download history
- `report_data`
- bounded `run_history`
- persisted plugin options

If you extend the state shape, keep it local to your plugin namespace.

## Testing Expectations

Before refactoring or extending a plugin:

- add or update tests under `tests/`
- keep `python3 -m unittest discover -s tests -v` green
- keep `python3 -m py_compile ...` green for touched modules

That is especially important for plugins with custom command grammars or
non-standard download workflows.

## Output Contract

The tool generates:

- structured `report_data` in state
- an on-demand project report when the user runs `fat.py report`

Do not treat the markdown report as the machine API, and do not make plugins write markdown themselves.

## Practical Guidance

- Keep `run()` side-effect free apart from network I/O.
- Put persistence in the shared flow, not inside `run()`.
- Deduplicate unstable result sets before returning them.
- Use keyword matching consistently across title, description, and optional extra text.
- Keep downloads idempotent where possible.
- Keep plugin-specific state compact.

## Testing

Minimum checks when adding or modifying a plugin:

```bash
python3 -m py_compile fat.py plugins/*.py
python3 fat.py <plugin> -h
python3 fat.py <plugin> options
python3 fat.py <plugin> search
```

The unit suite uses fakes and patched network calls. The Wayback integration
checks in `tests/integration_wayback.py` prefer the live archive and fall back
to a recorded local mirror (`tests/wayback_mirror.py`) when web.archive.org is
unreachable or throttling. `FAT_WAYBACK_INTEGRATION_MODE=live|mirror` forces a
backend. Run them with:

```bash
python3 -m unittest discover -s tests -p 'integration_*.py' -v
```

## Generated Reference

The handwritten guide in this file explains the design and conventions.
The generated API reference is intended to answer "what methods and types do I
actually implement?" without duplicating that contract by hand.

Build it with:

```bash
python3 -m pip install -r requirements-docs.txt
./scripts/build_plugin_api_docs.sh
```

Or, with an ephemeral tool environment:

```bash
uv run --with pdoc ./scripts/build_plugin_api_docs.sh
```
