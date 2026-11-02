"""Project lead store: persist investigation candidate links in project state.

A lead is one interesting URL discovered during an investigation, kept with
fixed-vocabulary provenance so context survives across runs, sessions, and
project switches.

Lead records live under:

```text
surface_projects.<project>.leads
```

Fixed vocabularies:

- `status`: why the lead is currently parked
  - `new`: recorded, not yet examined
  - `checked`: examined, no conclusion yet
  - `acted`: used as input to a FAT action (archive target, keywords, download)
  - `dead`: examined and ruled out
- `source`: where the lead came from
  - `portal`: the vendor's own site (product, support, or download pages)
  - `web`: open-web search or dorks
  - `writeup`: public writeups such as blogs, talks, or forum threads
  - `repo`: code repositories (GitHub, GitLab)
  - `image`: container images (Docker Hub)
  - `archive`: web archive discovery (wayback CDX or replay)
  - `user`: provided directly by the user

Duplicate URLs are rejected, not silently updated: callers should decide
explicitly whether to update an existing lead with `set_lead_field`.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any

LEAD_STATUSES = ("new", "checked", "acted", "dead")
LEAD_SOURCES = ("portal", "web", "writeup", "repo", "image", "archive", "user")
LEAD_FIELDS = ("status", "title", "source", "note")
UNSET_TOKENS = {"none", "null", '""', "''"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_status(status: str) -> str:
    cleaned = str(status).strip().lower()
    if cleaned not in LEAD_STATUSES:
        raise SystemExit(f"Unknown lead status {status!r}. Use one of: {', '.join(LEAD_STATUSES)}.")
    return cleaned


def _validate_source(source: str) -> str:
    cleaned = str(source).strip().lower()
    if cleaned not in LEAD_SOURCES:
        raise SystemExit(f"Unknown lead source {source!r}. Use one of: {', '.join(LEAD_SOURCES)}.")
    return cleaned


def _validate_url(url: str) -> str:
    cleaned = str(url).strip()
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SystemExit(
            "Lead URL must be an absolute http(s) URL, for example https://vendor.example/download"
        )
    return cleaned


def _url_key(url: str) -> tuple[str, str, str, str]:
    """Comparison key that ignores casing and trailing slashes.

    Only used for duplicate detection; the lead stores the URL as given.
    """

    parsed = urllib.parse.urlsplit(str(url).strip())
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        (parsed.path.rstrip("/") or "/").lower(),
        parsed.query.lower(),
    )


def find_lead(project: dict[str, Any], lead_id: Any) -> dict[str, Any]:
    """Return one lead by id or raise SystemExit."""

    try:
        wanted = int(lead_id)
    except (TypeError, ValueError):
        raise SystemExit(f"Invalid lead id {lead_id!r}.")
    for lead in project.get("leads", []):
        if lead["id"] == wanted:
            return lead
    raise SystemExit(f"No lead with id {wanted} in this project.")


def iter_leads(project: dict[str, Any], status: str | None = None) -> list[dict[str, Any]]:
    """Return leads, optionally filtered by one validated status."""

    leads = project.get("leads", [])
    if status:
        validated = _validate_status(status)
        return [lead for lead in leads if lead["status"] == validated]
    return list(leads)


def add_lead(
    project: dict[str, Any],
    url: str,
    *,
    title: str = "",
    source: str = "",
    note: str = "",
    status: str = "new",
) -> dict[str, Any]:
    """Append one lead to the project; reject duplicate URLs explicitly."""

    cleaned_url = _validate_url(url)
    cleaned_status = _validate_status(status)
    cleaned_source = _validate_source(source)
    leads = project.setdefault("leads", [])
    key = _url_key(cleaned_url)
    for lead in leads:
        if _url_key(lead["url"]) == key:
            raise SystemExit(
                f"Lead {lead['id']} already records this URL: {lead['url']}\n"
                f"Update it with `project lead set {lead['id']} <field> <value>` instead."
            )
    lead = {
        "id": max((item["id"] for item in leads), default=0) + 1,
        "url": cleaned_url,
        "title": str(title).strip(),
        "source": cleaned_source,
        "note": str(note).strip(),
        "status": cleaned_status,
        "created_at": _now(),
        "updated_at": _now(),
    }
    leads.append(lead)
    return lead


def set_lead_field(project: dict[str, Any], lead_id: Any, field: str, value: Any) -> dict[str, Any]:
    """Update one lead field; `none` clears optional text fields."""

    normalized = str(field).strip().lower()
    if normalized not in LEAD_FIELDS:
        raise SystemExit(f"Unsupported lead field {field!r}. Use one of: {', '.join(LEAD_FIELDS)}.")
    lead = find_lead(project, lead_id)
    cleaned = str(value).strip()
    if cleaned.lower() in UNSET_TOKENS:
        if normalized == "status":
            raise SystemExit("Lead status cannot be unset; set it to one of: " + ", ".join(LEAD_STATUSES) + ".")
        lead[normalized] = ""
    elif normalized == "status":
        lead["status"] = _validate_status(cleaned)
    elif normalized == "source":
        lead["source"] = _validate_source(cleaned)
    else:
        lead[normalized] = cleaned
    lead["updated_at"] = _now()
    return lead


def remove_leads(project: dict[str, Any], ids: Any) -> list[dict[str, Any]]:
    """Remove the given lead ids and return the removed records."""

    wanted: set[int] = set()
    for piece in str(ids).split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            wanted.add(int(piece))
        except ValueError:
            raise SystemExit(f"Invalid lead id {piece!r}.")
    if not wanted:
        raise SystemExit("No lead ids given.")
    leads = project.setdefault("leads", [])
    removed = [lead for lead in leads if lead["id"] in wanted]
    missing = sorted(wanted - {lead["id"] for lead in removed})
    if missing:
        raise SystemExit(f"No lead with id {missing[0]} in this project.")
    project["leads"] = [lead for lead in leads if lead["id"] not in wanted]
    return removed
