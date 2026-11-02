"""Core Wayback workflow operations shared by the plugin layers."""

from __future__ import annotations

import fnmatch
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

from fatlib.paths import resolve_cwd_path

from .retrieve_archived_firmware import ArchiveClient, Capture, contained_download_path, load_parser, safe_name
from .parser_support import load_post_hook, load_update_hook, render_options, require_parser


def parse_selection(selection: str, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if selection == "selected":
        selected = [snapshot for snapshot in snapshots if snapshot.get("selected")]
        if selected:
            return selected
        firmware_selected = [snapshot for snapshot in snapshots if snapshot.get("selected_firmware_links")]
        return firmware_selected or snapshots
    if selection == "all":
        return snapshots
    wanted = {int(piece) for piece in selection.split(",") if piece.strip()}
    return [snapshot for snapshot in snapshots if snapshot["id"] in wanted]


def clear_wayback_state(project: dict[str, Any], *, remove_files: bool = True) -> list[Path]:
    removed_paths: list[Path] = []
    if remove_files:
        directories = [
            resolve_cwd_path(project.get("downloads_dir", "output/downloads")),
            resolve_cwd_path(project.get("debug_dir") or f"{project.get('output_dir', 'output')}/debug/{safe_name(str(project.get('name', 'project')))}/wayback"),
        ]
        for directory in directories:
            if not directory.exists():
                continue
            for child in directory.iterdir():
                removed_paths.append(child)
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    project["active_target_url"] = None
    project["snapshots"] = []
    project["downloads"] = []
    return removed_paths


def firmware_name(url: str) -> str:
    path = Path(urllib.parse.urlsplit(url).path)
    return path.name or url


def firmware_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    selected_links = set(snapshot.get("selected_firmware_links", []))
    rows: list[dict[str, Any]] = []
    for index, url in enumerate(snapshot.get("firmware_links", []), start=1):
        rows.append({"index": index, "id": f"{snapshot['id']}:{index}", "url": url, "name": firmware_name(url), "selected": url in selected_links})
    return rows


def normalize_post_result(post_result: Any) -> dict[str, Any]:
    if isinstance(post_result, dict):
        return post_result
    to_dict = getattr(post_result, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"Unsupported post result type: {type(post_result)!r}")


def _matching_download_entry(download_log: list[dict[str, Any]], snapshot_id: int, url: str) -> dict[str, Any] | None:
    for entry in reversed(download_log):
        if entry.get("snapshot_id") == snapshot_id and entry.get("url") == url and entry.get("local_path"):
            return entry
    return None


def resolve_existing_local_path(project: dict[str, Any], entry: dict[str, Any]) -> Path | None:
    local_path_text = str(entry.get("local_path") or "")
    if not local_path_text:
        return None
    local_path = Path(local_path_text)
    if local_path.exists():
        return local_path

    downloads_dir = resolve_cwd_path(project.get("downloads_dir", "output/downloads"))
    candidate = downloads_dir / local_path.name
    if candidate.exists():
        entry["local_path"] = str(candidate)
        return candidate

    matches = list(downloads_dir.glob(f"*{local_path.name}"))
    if len(matches) == 1 and matches[0].exists():
        entry["local_path"] = str(matches[0])
        return matches[0]
    return None


def invoke_post_hook(project: dict[str, Any], snapshot: dict[str, Any], url: str, local_path: Path, archive: ArchiveClient) -> dict[str, Any] | None:
    post_hook = load_post_hook(project, project.get("snapshots", []))
    if post_hook is None:
        return None
    print("[*] invoking parser post hook for {}".format(local_path.name))
    return post_hook(project=project, snapshot=snapshot, url=url, local_path=local_path, archive=archive)


def infer_target_type(url: str, mimetype: str | None, configured: str) -> str:
    if configured in {"page", "file"}:
        return configured
    mime = (mimetype or "").lower()
    if mime.startswith("text/html") or "xhtml" in mime:
        return "page"
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith(("/", ".html", ".htm", ".php", ".aspx")):
        return "page"
    return "file"


def enum_snapshots(project: dict[str, Any], archive: ArchiveClient) -> None:
    target_url = project.get("active_target_url") or project.get("target_url")
    if not target_url:
        if project.get("search_domain") and project.get("search_keywords"):
            enum_domain_keyword_search(project, archive)
            return
        raise SystemExit("Configure either `set target-url <url>` or both `set domain <hostname>` and `set keywords <terms>`.")
    configured_type = str(project.get("target_type", "auto") or "auto")
    print(f"[*] Enumerating archived versions for {target_url}")
    captures = archive.exact_file_captures(target_url) if configured_type == "file" else archive.list_page_captures(target_url, max_snapshots=0)
    print(f"    -> {len(captures)} snapshot(s)")
    snapshots: list[dict[str, Any]] = []
    for next_id, capture in enumerate(captures, start=1):
        target_type = infer_target_type(target_url, capture.mimetype, configured_type)
        snapshots.append(
            {
                "id": next_id,
                "target_url": target_url,
                "target_type": target_type,
                "discovery_method": "snapshot",
                "timestamp": capture.timestamp,
                "original": capture.original,
                "statuscode": capture.statuscode,
                "mimetype": capture.mimetype or "",
                "selected": False,
                "selected_firmware_links": [],
                "firmware_links": [capture.original] if target_type == "file" else [],
            }
        )
    project["snapshots"] = snapshots
    project["downloads"] = []


def enum_domain_keyword_search(project: dict[str, Any], archive: ArchiveClient) -> None:
    """Find candidate archived pages across the configured domain."""

    keywords = [str(item).strip() for item in project.get("search_keywords", []) if str(item).strip()]
    if not keywords:
        raise SystemExit("No keywords configured. Set them with `set keywords keyword1,keyword2`.")
    domain = str(project.get("search_domain") or "").strip()
    if not domain:
        raise SystemExit("No domain configured. Set it with `set domain <hostname>`.")
    filetypes = project.get("search_filetypes") or {"include": [], "exclude": []}

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    next_id = 1
    print(f"[*] Searching archived pages across {domain} for: {', '.join(keywords)}")
    captures = archive.domain_keyword_captures(domain)
    for capture in captures:
        matched = [keyword for keyword in keywords if keyword.casefold() in capture.original.casefold()]
        mimetype = (capture.mimetype or "").lower()
        included = not filetypes.get("include") or any(fnmatch.fnmatchcase(mimetype, rule) for rule in filetypes["include"])
        excluded = any(fnmatch.fnmatchcase(mimetype, rule) for rule in filetypes.get("exclude", []))
        if not matched or not included or excluded or (capture.original, capture.timestamp) in seen:
            continue
        seen.add((capture.original, capture.timestamp))
        target_type = infer_target_type(capture.original, capture.mimetype, "auto")
        direct_links = [capture.original] if target_type == "file" else []
        candidates.append(
            {
                "id": next_id,
                "target_url": capture.original,
                "target_type": target_type,
                "timestamp": capture.timestamp,
                "original": capture.original,
                "statuscode": capture.statuscode,
                "mimetype": capture.mimetype or "",
                "discovery_method": "domain-keyword",
                "matched_keywords": matched,
                "selected": False,
                "selected_firmware_links": direct_links,
                "firmware_links": direct_links,
            }
        )
        next_id += 1

    project["active_target_url"] = None
    project["snapshots"] = candidates
    project["downloads"] = []
    if not candidates:
        print(
            f"[!] No URLs matched the configured keywords and file types "
            f"({len(captures)} archived capture(s) checked)."
        )


def select_snapshots(project: dict[str, Any], selection: str) -> list[int]:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    selected_ids = {snapshot["id"] for snapshot in parse_selection(selection, snapshots)}
    for snapshot in snapshots:
        snapshot["selected"] = snapshot["id"] in selected_ids
    promoted = [snapshot for snapshot in snapshots if snapshot["selected"] and snapshot.get("discovery_method") == "domain-keyword"]
    if promoted:
        project["active_target_url"] = promoted[0]["original"]
        project["target_type"] = promoted[0].get("target_type", "auto")
    return sorted(selected_ids)


def clear_selection(project: dict[str, Any]) -> None:
    for snapshot in project.get("snapshots", []):
        snapshot["selected"] = False


def parse_firmware_selection(selection: str, snapshots: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    selected_pairs: list[tuple[dict[str, Any], str]] = []
    wanted = {piece.strip() for piece in selection.split(",") if piece.strip()}
    for snapshot in snapshots:
        for firmware in firmware_rows(snapshot):
            if firmware["id"] in wanted:
                selected_pairs.append((snapshot, firmware["url"]))
    return selected_pairs


def select_firmware(project: dict[str, Any], selection: str) -> list[str]:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    selected_pairs = parse_firmware_selection(selection, snapshots)
    selected_map: dict[int, list[str]] = {}
    for snapshot, url in selected_pairs:
        selected_map.setdefault(snapshot["id"], []).append(url)
    for snapshot in snapshots:
        snapshot["selected_firmware_links"] = selected_map.get(snapshot["id"], [])
        snapshot["selected"] = bool(snapshot["selected_firmware_links"])
    return [
        f"{snapshot['id']}:{idx}"
        for snapshot in snapshots
        for idx, url in enumerate(snapshot.get("firmware_links", []), start=1)
        if url in set(snapshot.get("selected_firmware_links", []))
    ]


def clear_firmware_selection(project: dict[str, Any]) -> None:
    for snapshot in project.get("snapshots", []):
        snapshot["selected_firmware_links"] = []


def enum_firmware(project: dict[str, Any], selection: str, archive: ArchiveClient) -> None:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    for snapshot in parse_selection(selection, snapshots):
        if snapshot.get("target_type") == "file":
            snapshot["firmware_links"] = [snapshot["original"]]
            continue
        parser_name = require_parser(project, [snapshot])
        parser = load_parser(parser_name)
        browser_options = render_options(parser, int(project.get("timeout", 90) or 90))
        set_render_options = getattr(archive, "set_snapshot_render_options", None)
        if callable(set_render_options):
            set_render_options(browser_options)
        capture = Capture(timestamp=snapshot["timestamp"], original=snapshot["original"], statuscode=snapshot["statuscode"], mimetype=snapshot.get("mimetype") or None)
        archive.log(f"[extract] snapshot {capture.timestamp} {capture.original}")
        html: str | None = None
        try:
            if browser_options.mode == "static":
                html = archive.fetch_snapshot_html(capture)
            else:
                html = archive.fetch_snapshot_html_rendered(
                    capture,
                    mode=browser_options.mode,
                    wait_until=browser_options.wait_until,
                    wait_for=browser_options.wait_for,
                    timeout=browser_options.timeout,
                )
            links = sorted(dict.fromkeys(parser.extract_links(snapshot, html, archive)))
            snapshot.pop("parser_error", None)
        except Exception as exc:  # noqa: BLE001
            snapshot["parser_error"] = str(exc)
            links = []
        if not links and html is not None:
            snapshot["debug_html_path"] = save_debug_html(project, snapshot, html)
        snapshot["firmware_links"] = links
        snapshot["selected_firmware_links"] = [url for url in snapshot.get("selected_firmware_links", []) if url in set(links)]
        archive.log(f"[extract] {len(links)} link(s) from {capture.timestamp} {capture.original}")


def save_debug_html(project: dict[str, Any], snapshot: dict[str, Any], html: str) -> str:
    debug_dir = resolve_cwd_path(project.get("debug_dir") or f"{project.get('output_dir', 'output')}/debug/{safe_name(str(project.get('name', 'project')))}/wayback")
    debug_dir.mkdir(parents=True, exist_ok=True)
    filename = f"snapshot-{snapshot['id']}-{snapshot['timestamp']}-{safe_name(snapshot['original'])[:80]}.html"
    path = debug_dir / filename
    path.write_text(html, encoding="utf-8")
    return str(path)


def download_firmware(project: dict[str, Any], selection: str, archive: ArchiveClient) -> None:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")

    downloads_dir = resolve_cwd_path(project.get("downloads_dir", "output/downloads"))
    capture_policy = str(project.get("capture_policy", "nearest") or "nearest")
    downloads_dir.mkdir(parents=True, exist_ok=True)
    download_log: list[dict[str, Any]] = project.setdefault("downloads", [])
    update_hook = load_update_hook(project, snapshots)

    for snapshot in parse_selection(selection, snapshots):
        selected_links = snapshot.get("selected_firmware_links", [])
        links = selected_links or snapshot.get("firmware_links", [])
        if not links:
            print(f"{snapshot['id']:>3}  skipped              no firmware links")
            continue
        for url in links:
            archive.log(f"[download] resolving {url}")
            entry = {"snapshot_id": snapshot["id"], "snapshot_timestamp": snapshot["timestamp"], "url": url, "capture_policy": capture_policy}
            replacement_url = update_hook(project, snapshot, url, archive) if update_hook is not None and snapshot.get("target_type") != "file" else None
            if replacement_url:
                archive.log(f"[download] parser rewrote {url} -> {replacement_url}")
                filename = Path(urllib.parse.urlsplit(replacement_url).path).name or Path(url).name
                local_path = contained_download_path(downloads_dir, f"{snapshot['timestamp']}_{safe_name(filename)}")
                entry["resolved_url"] = replacement_url
                entry["download_source"] = "live"
                entry["local_path"] = str(local_path)
                if local_path.exists():
                    entry["status"] = "exists"
                else:
                    try:
                        archive.http_stream(replacement_url, local_path)
                    except Exception as exc:  # noqa: BLE001
                        entry["status"] = "error"
                        entry["error"] = str(exc)
                        download_log.append(entry)
                        print(f"{snapshot['id']:>3}  error                live                 {url} -> {exc}")
                        continue
                    entry["status"] = "downloaded"
            else:
                if snapshot.get("target_type") == "file" and url == snapshot.get("original"):
                    capture = Capture(
                        timestamp=snapshot["timestamp"],
                        original=snapshot["original"],
                        statuscode=snapshot["statuscode"],
                        mimetype=snapshot.get("mimetype") or None,
                    )
                else:
                    capture = archive.closest_page_capture(url, snapshot["timestamp"], policy=capture_policy)
                if capture is None:
                    entry["status"] = "missing"
                    download_log.append(entry)
                    print(f"{snapshot['id']:>3}  missing              -                    {url}")
                    continue

                filename = Path(capture.original).name or Path(url).name
                local_path = contained_download_path(downloads_dir, f"{capture.timestamp}_{safe_name(filename)}")
                entry["capture_timestamp"] = capture.timestamp
                entry["download_source"] = "archive"
                entry["local_path"] = str(local_path)
                if local_path.exists():
                    entry["status"] = "exists"
                else:
                    try:
                        archive.download_capture(capture, local_path)
                    except Exception as exc:  # noqa: BLE001
                        entry["status"] = "error"
                        entry["error"] = str(exc)
                        download_log.append(entry)
                        print(f"{snapshot['id']:>3}  error                {capture.timestamp}  {url} -> {exc}")
                        continue
                    entry["status"] = "downloaded"

            if load_post_hook(project, snapshots) is not None:
                try:
                    post_result = invoke_post_hook(project, snapshot, url, local_path, archive)
                except Exception as exc:  # noqa: BLE001
                    entry["post_status"] = "error"
                    entry["post_error"] = str(exc)
                    archive.log(f"[post] error for {local_path}: {exc}")
                else:
                    entry["post_status"] = "ok"
                    if post_result is not None:
                        entry["post_result"] = normalize_post_result(post_result)

            download_log.append(entry)


def run_post_actions(project: dict[str, Any], selection: str, archive: ArchiveClient) -> bool:
    snapshots = project.get("snapshots", [])
    if not snapshots:
        raise SystemExit("No snapshots in state. Run enum_snapshots first.")
    if load_post_hook(project, snapshots) is None:
        return False

    download_log: list[dict[str, Any]] = project.setdefault("downloads", [])
    any_ran = False
    for snapshot in parse_selection(selection, snapshots):
        links = snapshot.get("selected_firmware_links", []) or snapshot.get("firmware_links", [])
        for url in links:
            entry = _matching_download_entry(download_log, snapshot["id"], url)
            if entry is None:
                continue
            local_path = resolve_existing_local_path(project, entry)
            if local_path is None:
                continue
            try:
                post_result = invoke_post_hook(project, snapshot, url, local_path, archive)
            except Exception as exc:  # noqa: BLE001
                entry["post_status"] = "error"
                entry["post_error"] = str(exc)
                continue
            entry["post_status"] = "ok"
            if post_result is not None:
                entry["post_result"] = normalize_post_result(post_result)
            any_ran = True
    return any_ran
