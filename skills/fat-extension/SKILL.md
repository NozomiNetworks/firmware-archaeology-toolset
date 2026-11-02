---
name: fat-extension
description: Use when adding or modifying any FAT extension point — a top-level source plugin under plugins/, an external website parser under the configured FAT plugin directory, the public httpbin Wayback test parser, an external Wayback vendor parser, or a Wayback post-download scout behind a parser hook. Route to the canonical guide for the contract, verify the live code, then follow the shared TDD and validation workflow.
---

# FAT Extension Guide

One skill for all four FAT extension points. The contracts themselves live in
canonical guides; this skill defines the shared workflow, routes each task to
its canonical guide, and records only what is unique per point.

## Choose the extension point

| Extension | Location | Exposes | Canonical guide |
|---|---|---|---|
| Source plugin | `plugins/<name>.py` | `PLUGIN_CLASS`, subclass of `SurfacePluginBase`, implements `run(context)` and `download(context, artifact, downloads_dir)` | `plugins/README.md` |
| External website parser | `<plugin-dir>/scraper/websites/<site>.py` | `SCRAPER_CLASS`, subclass of `WebsiteScraperBase` | `plugins/scraper/base.py`, `plugins/scraper/registry.py` |
| External Wayback vendor parser | `<plugin-dir>/wayback/parsers/<vendor>.py` | plain module exporting `HOSTS` and `extract_links(...)`; optional hooks | `plugins/wayback/PARSER_HOWTO.md` |
| Wayback scout | logic behind the parser's `post_download(...)` hook | JSON-serializable result (or `to_dict()`) rendered from `post_result` | `plugins/wayback/PARSER_HOWTO.md` plus `plugins/wayback/render.py` |

Do not mix the contracts: `PLUGIN_CLASS` is only for top-level FAT plugins,
`SCRAPER_CLASS` only for scraper website parsers, and Wayback parsers are
plain modules with no class at all. Command names are fixed by the dispatch
map in `fat.py`; plugins cannot introduce new command names without editing
the entry point.

## Before coding

1. Read the canonical guide for the chosen extension point.
2. Verify the live contract in code: exact signatures, base class, and
   discovery constant. Docs can lag; the code is the truth.
3. Read one concrete implementation close to the target:
   - `plugins/dockerhub.py` for source plugins
   - `plugins/wayback/parsers/httpbin.py` for the public Wayback parser
   - the corresponding private repository module for external vendor parsers
4. External plugin files are loaded from `~/.fat/plugins/` by default, or from
   `plugin_dir` in `~/.fat/config.json` / `--plugin-dir PATH`.
5. If docs and code disagree, follow the code — then fix the doc in the same
   change.

## Per-point essentials

### Source plugin

- Implement only `run(context)` and `download(context, artifact, downloads_dir)`
  unless the command grammar genuinely differs; do not invent a custom command
  model when the standard surface fits.
- `run(context)` iterates `context.queries()`, classifies candidates with
  `classify_match(...)`, deduplicates with `merge_named_results(...)` where the
  same artifact appears in multiple queries, and returns the plugin report:
  `name`, `status`, `searches`, `errors`, `results`.
- `download(...)` builds a deterministic path under `downloads_dir`, fetches
  only source-specific payloads, optionally writes `artifact.json`, and
  returns `status`, `path`, `files`.
- Reuse `fetch_json`, `fetch_text`, `download_file`, `write_json`, and
  `safe_name` from `plugins/base.py` instead of reimplementing them.
- Bare invocation must mean `status`.

### Scraper website parser

- The top-level `scraper` plugin stays untouched; public test code lives under
  public tree has no vendor implementations; all website parsers live under
  the configured external plugin directory.
- Implement `can_handle(website_url, entry_html="")`,
  `extract_product_links(category_html, website_url)`, and
  `parse_product(product_html, product_url)`; set `name`, `help_summary`, and
  `default_website` when a sensible entrypoint exists.
- Use `extract_hrefs(...)` and `urllib.parse.urljoin(...)`; no brittle regex
  link extraction.
- Normalize product records to the schema implied by `build_catalog(...)` and
  `product_artifact(...)`: `title`, `slug`, `product_url`, `download_urls`,
  optional `download_ids`.
- Keep rendering static by default. For a trusted JavaScript-heavy parser,
  declare `RENDER_MODE`, `RENDER_WAIT_UNTIL`, and optionally `RENDER_WAIT_FOR`
  (and `RENDER_TIMEOUT`). FAT supplies rendered HTML to the same parser
  methods; do not pass Playwright objects into parser code.

### Wayback vendor parser

- Keep the parser small and deterministic: parse candidate links, resolve
  relatives against `snapshot["original"]`, unwrap replay wrappers with
  `ArchiveClient.unwrap_replay_url(...)`, filter to real firmware files, and
  return sorted, deduplicated original vendor URLs.
- Return vendor URLs, never Wayback replay URLs; never build replay URLs by
  hand — let `ArchiveClient` handle replay, capture lookup, and retries.
- Follow at most one archived download-page hop with
  `archive.fetch_snapshot_html_near(...)`.
- Use `archive.log(...)` instead of unconditional prints.
- External Wayback parsers must define a non-empty `HOSTS` sequence. These host
  names register automatic parser inference; explicit parser selection still
  works for targets without an inference mapping.
- Keep rendering static by default. A trusted parser may export the same
  `RENDER_MODE`, `RENDER_WAIT_UNTIL`, `RENDER_WAIT_FOR`, and `RENDER_TIMEOUT`
  declarations. Wayback rendering must use the archived replay URL through
  `archive.fetch_snapshot_html_rendered(...)`; never navigate the live vendor
  URL as a substitute.

### Wayback post-download scout

- Keep the scout vendor-specific; do not push scout logic into the generic
  Wayback plugin unless it is genuinely shared.
- Answer four questions in the result: what wrapper was downloaded, what inner
  payloads exist, which payloads decrypted, and what concise evidence FAT
  should show in status/post output.
- Persist small operator-facing data only: verdict, payload names, decrypt
  parameters, minimal evidence strings, unpacked paths when useful. Convert to
  plain dicts only at the persistence boundary.
- Prefer observational heuristics before vendor-specific assumptions, keep the
  hook idempotent, and return concrete secrets (passwords, commands) as
  structured data rather than log lines.

## TDD workflow

Work in test-driven order:

1. State the exact behavior to add or change and its observable success
   condition (result entries, selection behavior, filenames, state or report
   updates) before coding.
2. Write or update the focused test first; assert the smallest real decision
   (URL recognized or not, record fields, wrapper verdict, rendered summary).
3. Only then patch the implementation.
4. Confirm the same behavior through the real `fat.py` flow for the extension
   point (`<plugin> search` / `scraper search` / `wayback search` +
   `wayback download selected` / `wayback post selected`).

## Validation

Run the focused tests for the touched area, then the real CLI workflow:

- source plugin: `tests/test_base_plugin.py`, the plugin's test file,
  `tests/integration_surface_plugins.py` for live search and download paths
- scraper website parser: public loader tests plus the private repository's
  vendor parser tests
- Wayback parser and scout: `tests/test_wayback_plugin.py`,
  `tests/test_wayback_parser_utils.py`,
  `tests/test_wayback_status_output.py`, `tests/test_wayback_render.py`,
  `tests/test_wayback_support.py`, `tests/integration_wayback.py`
- browser-enabled parser changes: `tests/test_browser.py` plus the separate
  local-fixture check in `tests/browser_integration.py`, run with
  `./scripts/run_browser_tests_container.sh`

Finish with the offline unit gate before handing off:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py' -v
python3 -m coverage report --fail-under=80
```

The Wayback integration suite prefers the live archive and falls back to a
recorded local mirror when web.archive.org throttles, so it stays usable
without network access.

If the change introduces a new capability category — not just another source,
site, or parser of an existing one — check whether
`skills/fat-investigation/SKILL.md` should mention it.

Always verify two things — never stop at "links were found" or "the command
exited 0":

1. the extension works through the real `fat.py` command path, and
2. the recovered files and results are the correct artifacts, verified on
   disk: inspect URL host/path patterns, saved file names and types, and any
   reported decrypted or unpacked paths. Do not trust state alone.
