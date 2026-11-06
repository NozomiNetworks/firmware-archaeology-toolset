# Wayback Parser How-To

This guide is for developers adding a vendor parser to the external plugin
directory. The generic Wayback implementation and parser contract stay
in the FAT repository.

External parsers are copied under the configured plugin root:

```text
<plugin-dir>/wayback/parsers/
```

Use it together with:

- `plugins/wayback/parsers/api.py`
- `plugins/wayback/retrieve_archived_firmware.py`
- `plugins/README.md`

## What A Wayback Parser Does

The `wayback` plugin has two stages inside `fat.py`:

1. enumerate archived versions of an exact target URL or discovered candidate
2. extract firmware URLs from those archived pages and optionally download them

A parser teaches FAT how to do stage 2 for one vendor or site family.

The parser itself is a plain Python module, not a class.

For a small complete example, see `parsers/httpbin.py`. It treats the
archived `https://httpbin.org/` documentation page as a product page and
extracts its `/bytes/32` endpoint as a firmware-like candidate. The candidate
then follows the ordinary `search firmware`, `select`, and `download` steps;
the parser does not download it itself.

## Files You Add

For a parser named `examplevendor`, add:

```text
<plugin-dir>/wayback/parsers/examplevendor.py
```

When the user runs:

```bash
python3 fat.py <project> wayback set parser examplevendor
python3 fat.py <project> wayback set target-url https://vendor.example/downloads/device
python3 fat.py <project> wayback search snapshot
```

FAT queries Wayback for archived captures of the exact target URL and stores
the results in project state. The parser is only needed when the target is an
HTML page and firmware links must be extracted from its content.

When a vendor changes its URL layout, use Domain keyword search to scout
the target domain for archived URLs containing user supplied terms:

```bash
python3 fat.py <project> wayback set domain www.vendor.example
python3 fat.py <project> wayback set keywords firmware,product-slug
python3 fat.py <project> wayback set filetypes 'include=text/html;exclude=image/*'
python3 fat.py <project> wayback search snapshot
python3 fat.py <project> wayback select 3
python3 fat.py <project> wayback search snapshot
python3 fat.py <project> wayback search firmware
```

Domain keyword results use the same snapshot state and IDs as the normal search.
Selecting a result promotes its URL as the active target for the next
`search snapshot`. HTML candidates use the parser; file candidates can be
downloaded directly without a parser. If a parser is selected or can be
inferred from the target host, its `post_download` hook still runs after a
direct file download.

Domain keyword results accept archived MIME types by default while excluding
common page assets. Change the filter when a narrower result set is needed:

```bash
python3 fat.py <project> wayback set filetypes \
  'include=text/html,application/zip;exclude=image/*,text/css'
```

Rules use the CDX MIME type. `include` limits candidates and `exclude` removes
matches; wildcards such as `image/*` are supported.

## Parser Module Contract

Required export:

- `HOSTS`: non-empty sequence of hostnames used for automatic parser inference
- `extract_links(snapshot, page_html, archive) -> list[str]`

Optional exports:

- `update_firmware_link(project, snapshot, url, archive) -> str | None`
- `post_download(project, snapshot, url, local_path, archive) -> Any`
- `RENDER_MODE = "preferred" | "required"` (default: `"static"`)
- `RENDER_WAIT_UNTIL = "domcontentloaded" | "load" | "networkidle"`
- `RENDER_WAIT_FOR = "<css selector>"` (optional)
- `RENDER_TIMEOUT = <seconds>` (optional)

When a parser declares a non-static render mode, FAT navigates Playwright to
the archived replay URL and passes the resulting HTML string to the unchanged
`extract_links(...)` function. It never renders the live vendor URL for this
path. `preferred` falls back to direct archived HTTP retrieval; `required`
returns an actionable browser error. JSON and direct file captures continue to
use direct retrieval. Browser rendering is an explicit trusted-plugin feature,
not a default for public parsers.

## State And Data Structures

### Project Record

The `project` object passed to optional hooks is the persisted Wayback plugin
state for the current FAT project. Relevant keys:

- `parser`: selected parser name
- `api_base`: optional archive mirror base
- `downloads_dir`: local output directory for downloaded firmware
- `capture_policy`: `nearest` or `strict`
- `target_url`: exact URL selected for normal snapshot enumeration
- `target_type`: `auto`, `page`, or `file`
- `active_target_url`: URL promoted from a Domain keyword result
- `search_domain`: domain searched in Domain keyword search mode
- `search_keywords`: user supplied URL terms
- `search_filetypes`: MIME include and exclude rules
- `snapshots`: list of snapshot records
- `downloads`: download log entries created by `wayback download ...`

Parsers usually inspect this state, not mutate it.

Related FAT-managed files and directories:

- state file: `.fat.json` by default
- project report: `output/report.md`
- default Wayback downloads: `output/downloads/<project>/wayback/`
- parser debug HTML: `output/debug/<project>/wayback/`

If a parser reads `project["downloads_dir"]`, that value is the effective
Wayback download directory for the current project, after any explicit
`wayback set downloads-dir ...` override.

### Snapshot Record

The `snapshot` dictionary passed into parser hooks describes one archived target
page. Fields:

- `id`: integer snapshot id used by `wayback select`
- `target_url`: exact URL used to find this snapshot
- `target_type`: `page` or `file`
- `timestamp`: 14-digit Wayback timestamp
- `original`: original URL for this archived page
- `statuscode`: archived HTTP status code
- `mimetype`: archived content type when known
- `selected`: whether the snapshot is currently selected
- `firmware_links`: firmware URLs extracted for this snapshot
- `selected_firmware_links`: selected subset of `firmware_links`
- `discovery_method`: `snapshot` or `domain-keyword`
- `matched_keywords`: keyword terms matched in the candidate URL
- `debug_html_path`: saved archived HTML when page extraction returns no links
- `parser_error`: parser exception text when extraction fails

How to use it:

- use `snapshot["original"]` to resolve relative links found in `page_html`
- use `snapshot["timestamp"]` to fetch adjacent archived pages near the same
  moment
- use `snapshot["target_url"]` when logic depends on the original target family
  and consider assigning it to a clearer local name such as
  `snapshot_target_url`
- use `snapshot["firmware_links"]` only as FAT-managed extracted results, not
  as a place to stash parser-side temporary data

### Capture

`Capture` is the immutable record used by `ArchiveClient`. Fields:

- `timestamp`
- `original`
- `statuscode`
- `mimetype`
- `length`

Parsers usually do not need to build captures manually, but they may receive
them from `archive.fetch_snapshot_html_near(...)`.

## ArchiveClient Methods Parsers Can Use

The parser receives the active `archive` client. Useful methods:

- `archive.fetch_snapshot_html(capture)`
  Fetch HTML/text for a known archived page capture.

- `archive.fetch_snapshot_html_rendered(capture, ...)`
  Fetch a JavaScript-rendered archived page when the parser's render settings
  require it. The replay URL and browser lifecycle are handled by FAT.

- `archive.fetch_snapshot_html_for_url(url, timestamp)`
  Fetch archived HTML for one explicit URL/timestamp pair.

- `archive.fetch_snapshot_html_near(url, timestamp)`
  Try the exact timestamp first, then fall back to the nearest available
  capture. Returns `(html, capture)`. When the parser declares browser
  rendering, the same settings are applied automatically to this follow-up
  page fetch.

- `archive.closest_page_capture(url, timestamp, policy="nearest")`
  Resolve the archived capture FAT would use for a page or binary URL.

- `archive.http_get(url)`
  Raw GET helper when you need additional non-HTML retrieval.

- `archive.http_stream(url, destination)`
  Streaming helper used by FAT when a parser rewrites a firmware URL to a live
  vendor location.

- `archive.log(message)`
  Verbose logger. Use this instead of unconditional `print(...)` in parser code.

Rule of thumb:

- use `fetch_snapshot_html_near(...)` when an archived page links to a second
  archived HTML page
- use `closest_page_capture(...)` when you need to know which capture FAT would
  use for a page or binary URL under the current capture policy
- use `http_stream(...)` only when the parser intentionally rewrites a firmware
  download to a live vendor URL

Let `ArchiveClient` handle mirror layout, replay URL details, and retry logic.
Do not build Wayback replay URLs by hand unless you have a very specific reason.

## Implementing `extract_links(...)`

This is the core parser function.

Input:

- `snapshot`: one archived target page record
- `page_html`: HTML body of that archived page
- `archive`: active archive client

Output:

- a list of real firmware URLs

Rules:

- return the original firmware URLs, not Wayback replay URLs
- deduplicate them
- keep ordering deterministic, usually `sorted(...)`
- keep the logic vendor-specific but tolerant of historical layout drift

Typical pattern:

1. parse links from `page_html`
2. resolve relatives against `snapshot["original"]`
3. strip Wayback wrappers if needed
4. keep only URLs that match the vendor’s firmware pattern
5. if the page links to second-level download pages, fetch those with
   `archive.fetch_snapshot_html_near(...)`

When stripping wrappers, reuse the shared helper:

```python
from plugins.wayback.retrieve_archived_firmware import ArchiveClient
```

Then call:

```python
ArchiveClient.unwrap_replay_url(url)
```

Do not reimplement the same Wayback replay regex in each parser unless you genuinely need different behavior.

Minimal skeleton:

```python
from __future__ import annotations

import urllib.parse

from plugins.wayback.retrieve_archived_firmware import ArchiveClient


def extract_links(snapshot, page_html, archive):
    snapshot_target_url = snapshot["target_url"]
    candidate_urls = set()

    for href in extract_candidate_links(page_html):
        candidate_url = urllib.parse.urljoin(snapshot["original"], href)
        candidate_url = ArchiveClient.unwrap_replay_url(candidate_url)
        if is_firmware_file(candidate_url):
            candidate_urls.add(candidate_url)

    return sorted(candidate_urls)
```

## Implementing `update_firmware_link(...)`

Use this only when the firmware file should be downloaded from a newer live
vendor storage path instead of from the archived replay URL.

Common case:

- archived page exposes historical filename `old-fw.zip`
- vendor moved binaries to a new storage path
- Wayback never indexed the old file there
- parser rewrites to the vendor’s live storage URL

Return:

- replacement live URL as `str`
- `None` to keep the normal archived download path

Keep this hook small:

- derive the new URL from the historical filename or vendor naming rules
- do not download anything inside this hook
- let FAT perform the actual transfer

Example:

```python
from pathlib import Path
import urllib.parse


def update_firmware_link(project, snapshot, url, archive):
    filename = Path(urllib.parse.urlsplit(url).path).name
    if not filename:
        return None
    return f"https://vendor.example/firmware/{filename}"
```

## Implementing `post_download(...)`

Use this for follow-on processing after FAT has already downloaded the firmware.
It runs for both parser-extracted links and direct file targets when the parser
is selected or can be inferred from the target host.

Good uses:

- extract metadata
- run firmware scouting
- unpack and triage images
- detect bridge versions
- stage parser-specific follow-on analysis

Avoid:

- mutating unrelated project state
- re-downloading the same file
- printing large amounts of output unconditionally

Parameters:

- `project`: Wayback project state
- `snapshot`: source snapshot record
- `url`: original firmware URL
- `local_path`: downloaded local file path
- `archive`: active archive client

Return:

- any JSON-serializable object
- or an object exposing `to_dict()`

FAT stores the normalized result under the download log entry as
`post_result`.

## Parser Author Checklist

1. Add `<plugin-dir>/wayback/parsers/<name>.py` in the external plugin directory.
2. Implement `extract_links(snapshot, page_html, archive)`.
3. Reuse:
   - `ArchiveClient.unwrap_replay_url(...)`
   - `archive.fetch_snapshot_html_near(...)`
   - `archive.log(...)`
4. Add `update_firmware_link(...)` only if the vendor moved binaries to a new
   live storage path.
5. Add `post_download(...)` only if follow-on processing is actually needed.
6. Test with:
   - `python3 fat.py <project> wayback set parser <name>`
   - `python3 fat.py <project> wayback search snapshot`
   - `python3 fat.py <project> wayback select 1`
   - `python3 fat.py <project> wayback search firmware`
   - `python3 fat.py <project> wayback select 1:1`
   - `python3 fat.py <project> wayback download selected`

## Testing And Debugging Tips

- start with one exact `target-url`
- set `target-type file` when the URL is a direct firmware file
- use `archive.log(...)` for parser diagnostics
- keep extracted URL ordering deterministic with `sorted(...)`
- prefer resilient vendor-specific matching rules over page-layout-perfect
  scrapers
- when a vendor page links to another HTML page, fetch that page through the
  archive client instead of following the live site directly

## Practical Guidelines

- Keep the parser module small and vendor-focused.
- Prefer helper functions in the parser module over deeply nested logic in
  `extract_links(...)`.
- Always normalize or strip Wayback-wrapped URLs before filtering.
- Be tolerant of historical HTML drift across snapshots.
- Use `archive.log(...)` for parser diagnostics.
- Avoid mutating `snapshot["firmware_links"]` directly; just return the links.
- Avoid network calls outside `ArchiveClient`.

## Quick Manual Test Flow

```bash
python3 fat.py project demo
python3 fat.py demo wayback set parser examplevendor
python3 fat.py demo wayback set target-url https://vendor.example/download
python3 fat.py demo wayback search snapshot
python3 fat.py demo wayback select 1
python3 fat.py demo wayback search firmware
python3 fat.py demo wayback select 1:1
python3 fat.py demo wayback download selected
python3 fat.py demo wayback post selected
```

Inspect:

- `wayback status`
- project state in `.fat.json`
- downloaded files under `output/downloads/<project>/wayback/`
- failed page HTML under `output/debug/<project>/wayback/`
