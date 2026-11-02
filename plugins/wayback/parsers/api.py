"""Documented extension contract for Wayback parser modules.

Wayback parser modules are lightweight adapters that teach the `wayback` plugin
how to extract firmware download links from archived pages for one vendor or
site family.

The implementation model is a plain module exporting:

- `extract_links(...)` as the required parser function
- `update_firmware_link(...)` as an optional pre-download hook
- `post_download(...)` as an optional follow-on hook

Wayback parser authors usually need to understand four things:

- the persisted Wayback project state shape
- the per-snapshot record shape
- the `Capture` record used by the archive client
- the small subset of `ArchiveClient` methods that parsers can call safely

For replay-URL normalization, parsers should reuse:

- `ArchiveClient.unwrap_replay_url(...)`

For parser setup and naming conventions, see:

- `plugins/wayback/PARSER_HOWTO.md`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..retrieve_archived_firmware import ArchiveClient, Capture

__all__ = [
    "ArchiveClient",
    "Capture",
    "ProjectRecord",
    "SnapshotRecord",
    "extract_links",
    "update_firmware_link",
    "post_download",
]

ProjectRecord = dict[str, Any]
"""Persisted Wayback plugin state for one FAT project.

This is the dictionary stored under:

`surface_projects.<project>.plugins.wayback`

Relevant keys for parser authors:

- `parser`: selected parser name, for example `examplevendor`
- `api_base`: optional archive/mirror base URL override used by FAT
- `downloads_dir`: local directory where retrieved firmware files are written
- `capture_policy`: `nearest` or `strict`
- `target_url`: exact URL selected for snapshot enumeration
- `target_type`: `auto`, `page`, or `file`
- `active_target_url`: URL promoted from a Domain keyword result
- `search_domain`: domain used for Domain keyword search
- `search_keywords`: URL terms used for Domain keyword search
- `search_filetypes`: MIME include and exclude rules for Domain keyword search
- `snapshots`: list of `SnapshotRecord` dictionaries
- `downloads`: download log entries produced by `wayback download ...`

Parsers usually read this structure only in optional hooks such as
`update_firmware_link(...)` and `post_download(...)`. They should avoid
mutating it directly unless they are intentionally adding parser-local metadata
to the download log or post-processing results.
"""

SnapshotRecord = dict[str, Any]
"""Persisted snapshot metadata passed into Wayback parser functions.

Each snapshot represents one archived capture of the selected target URL.

Fields:

- `id`: integer snapshot identifier used by `wayback select`
- `target_url`: the exact URL used to find this capture
- `target_type`: `page` or `file`
- `timestamp`: 14-digit Wayback capture timestamp, for example `20240102030405`
- `original`: original page URL for this capture
- `statuscode`: HTTP status code recorded by Wayback, typically `200`
- `mimetype`: archived content type when available
- `selected`: whether this snapshot is currently selected in the workflow
- `firmware_links`: parser-extracted firmware URLs for this snapshot
- `selected_firmware_links`: selected subset of `firmware_links`
- `discovery_method`: `snapshot` or `domain-keyword`
- `matched_keywords`: keyword terms matched in the candidate URL
- `debug_html_path`: saved archived HTML when page extraction needs review
- `parser_error`: parser exception text when extraction fails

Usage guidance:

- use `target_url` when you need to know which page family this snapshot came
  from
- in parser code, prefer a more explicit local variable name such as
  `snapshot_target_url`
- use `original` when resolving relative links found in the archived HTML
- use `timestamp` when fetching adjacent archived pages near the same time
- treat `firmware_links` and `selected_firmware_links` as FAT-managed workflow
  state, not as parser inputs to recompute from scratch
"""

Capture.__doc__ = """Immutable archived capture record used by `ArchiveClient`.

Fields:

- `timestamp`: 14-digit capture timestamp
- `original`: original URL for the captured resource
- `statuscode`: archived HTTP status code
- `mimetype`: archived content type when known
- `length`: archived content length when known

Parsers usually do not construct `Capture` objects directly, but they may
receive them indirectly from `ArchiveClient.fetch_snapshot_html_near(...)`.
"""

ArchiveClient.__doc__ = """Archive and Wayback access helper used by the Wayback plugin.

Parser authors do not need to know the full client surface. The methods that
matter for parser implementation are:

- `fetch_snapshot_html(capture)`: fetch one archived page body
- `fetch_snapshot_html_for_url(url, timestamp)`: fetch one archived page body
  for an explicit URL/timestamp pair
- `fetch_snapshot_html_near(url, timestamp)`: try the exact timestamp first,
  then fall back to the nearest available capture; returns `(html, capture)`
- `closest_page_capture(url, timestamp, policy=...)`: resolve the archived
  binary/page capture FAT would use for a given URL
- `http_get(url)`: raw GET helper when a parser needs to fetch additional data
- `http_stream(url, destination)`: streaming download helper used by FAT when a
  parser rewrites a firmware URL to a live vendor location
- `unwrap_replay_url(url)`: strip a Wayback replay wrapper and recover the real
  underlying URL
- `log(message)`: verbose logger that prints only when FAT is in verbose mode

Parsers should prefer the HTML-oriented helpers over raw replay URL
construction. Let `ArchiveClient` handle mirror layout, retry logic, and
Wayback endpoint differences.
"""


def extract_links(
    snapshot: SnapshotRecord,
    page_html: str,
    archive: ArchiveClient,
) -> list[str]:
    """Required parser export for archived download-page parsing.

    Parameters:
    - `snapshot`: persisted metadata for the archived page currently being parsed
    - `page_html`: HTML body of that archived page
    - `archive`: the active `ArchiveClient`, useful for fetching adjacent
      archived pages when the main page links to secondary download pages

    Returns:
    - a list of normalized firmware URLs

    Notes:
    - return only the real firmware URLs, not Wayback replay URLs
    - prefer deterministic ordering, for example `sorted(...)`
    - parsers should be vendor-specific, but this function should stay generic
      enough to work across multiple versions of the same vendor site
    - use `snapshot["original"]` to resolve relative links from `page_html`
    - use `snapshot["timestamp"]` with `archive.fetch_snapshot_html_near(...)`
      when the page links to second-level archived download pages
    """

    raise NotImplementedError


def post_download(
    project: ProjectRecord,
    snapshot: SnapshotRecord,
    url: str,
    local_path: str | Path,
    archive: ArchiveClient,
) -> Any:
    """Optional parser hook invoked after one firmware file is downloaded.

    Use this for follow-on processing that should happen after retrieval, such
    as firmware scouting, metadata extraction, or bridge-version detection.

    Parser modules do not need to implement this function. FAT only calls it
    when the selected parser exports it.

    Parameters:
    - `project`: the persisted Wayback project state
    - `snapshot`: the `SnapshotRecord` that produced this firmware URL
    - `url`: the original firmware URL selected from the archived page
    - `local_path`: local file path FAT downloaded for that firmware
    - `archive`: the active `ArchiveClient`

    Return value:
    - any JSON-serializable object, or an object exposing `to_dict()`
    - FAT stores the normalized value under the download log entry as
      `post_result`
    """

    raise NotImplementedError


def update_firmware_link(
    project: ProjectRecord,
    snapshot: SnapshotRecord,
    url: str,
    archive: ArchiveClient,
) -> str | None:
    """Optional parser hook invoked before one firmware file is downloaded.

    Use this when an archived page reveals the historical firmware filename,
    but the actual binary should be downloaded from a newer live vendor storage
    location instead of from the archived replay URL.

    Returns:
    - a replacement direct-download URL when the parser wants FAT to fetch the
      firmware from a live vendor location
    - `None` to keep the original archived URL and let FAT use the normal
      Wayback capture download flow

    Typical use:
    - historical page exposes `old-fw-name.zip`
    - vendor later moved binaries to a new live storage path
    - parser rewrites the URL to that live location so FAT downloads the file
      from the vendor instead of from the archived replay URL
    """

    raise NotImplementedError
