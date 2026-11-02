from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from .base import (
    PluginContext,
    PluginSetting,
    SurfacePluginBase,
    classify_match,
    fetch_json_no_redirect,
    fetch_json,
    is_unset_value,
    merge_named_results,
    print_key_value,
    print_ok,
    print_result_table,
    print_step,
    print_warn,
    safe_name,
    write_json,
    search_setting_definitions,
)
from fatlib.http import format_rate_limit_error, is_rate_limited_error
from fatlib.search import SearchPolicy, SearchRequestBudgetExceeded


def _valid_dockerhub_next_url(next_url: str, api_base: str) -> tuple[str | None, str | None]:
    """Resolve and validate Docker Hub's continuation URL origin and path."""

    resolved = urllib.parse.urljoin(f"{api_base.rstrip('/')}/", next_url)
    parts = urllib.parse.urlsplit(resolved)
    base = urllib.parse.urlsplit(api_base)
    expected_path = f"{base.path.rstrip('/')}/v2/search/repositories/"
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.fragment:
        return None, "invalid"
    if (parts.scheme.lower(), parts.netloc.lower()) != (base.scheme.lower(), base.netloc.lower()):
        return None, "origin"
    if parts.path != expected_path:
        return None, "invalid"
    return resolved, None


def fetch_dockerhub_page(url: str, timeout: int) -> object:
    """Fetch a Docker Hub search page without following redirects."""

    return fetch_json_no_redirect(url, timeout)


class DockerHubPlugin(SurfacePluginBase):
    name = "dockerhub"
    default_api_base = "https://hub.docker.com"
    help_summary = "Search Docker Hub repositories and download selected image metadata."
    help_examples = (
        "Examples:\n"
        "  fat.py project device-research --keywords \"sdk,platform,connector\"\n"
        "  fat.py dockerhub set min-match-keywords 3\n"
        "  fat.py dockerhub set api-base http://127.0.0.1:8031/hub\n"
        "  fat.py dockerhub set downloads-dir output/downloads/lab/dockerhub\n"
        "  fat.py dockerhub search\n"
        "  fat.py device-research dockerhub status\n"
        "  fat.py dockerhub select 1\n"
        "  fat.py dockerhub download selected\n"
    )

    def setting_definitions(self, project: dict, tool_state: dict) -> list[PluginSetting]:
        return [*super().setting_definitions(project, tool_state), *search_setting_definitions()]

    def ensure_state(self, project: dict) -> dict:
        tool_state = super().ensure_state(project)
        tool_state.setdefault("api_base", self.default_api_base)
        return tool_state

    def build_api_base(self, tool_state: dict) -> str:
        return str(tool_state.get("api_base") or self.default_api_base).rstrip("/")

    def options(self, project_name: str, project: dict, args) -> int:
        result = super().options(project_name, project, args)
        tool_state = self.ensure_state(project)
        print_key_value("API base", self.build_api_base(tool_state))
        return result

    def set(self, project_name: str, project: dict, args) -> int:
        setting = args.selection.replace("_", "-").lower()
        if setting == "api-base":
            tool_state = self.ensure_state(project)
            tool_state["api_base"] = self.default_api_base if is_unset_value(args.value) else args.value.rstrip("/")
            print_ok(f"{'Reset' if is_unset_value(args.value) else 'Set'} api-base for {self.name}")
            print_key_value("Value", tool_state["api_base"])
            return 0
        return super().set(project_name, project, args)

    def _merge_results(self, results: list[dict]) -> list[dict]:
        return merge_named_results(results)

    def download(self, context: PluginContext, artifact: dict, downloads_dir: Path) -> dict:
        target_dir = downloads_dir / f"{artifact['artifact_num']:03d}_{safe_name(artifact['name'])}"
        target_dir.mkdir(parents=True, exist_ok=True)
        api_base = str(artifact.get("api_base") or context.api_base or self.default_api_base).rstrip("/")
        metadata_url = f"{api_base}/v2/repositories/{artifact['name']}/"
        tags_url = f"{api_base}/v2/repositories/{artifact['name']}/tags/?page_size=25"
        metadata = fetch_json(metadata_url, timeout=context.timeout)
        tags = fetch_json(tags_url, timeout=context.timeout)
        write_json(target_dir / "metadata.json", metadata)
        write_json(target_dir / "tags.json", tags)
        write_json(target_dir / "artifact.json", artifact)
        return {"status": "downloaded", "path": str(target_dir), "files": ["metadata.json", "tags.json", "artifact.json"]}

    def run(self, context: PluginContext) -> dict:
        policy = SearchPolicy.from_context(context, self.name)
        api_base = str(context.api_base or self.default_api_base).rstrip("/")
        results = []
        searches = []
        errors = []
        pagination = [
            {"query": query, "url": None, "pages_fetched": 0, "state": "active"}
            for query in context.queries()
        ]

        for entry in pagination:
            entry["url"] = f"{api_base}/v2/search/repositories/?" + urllib.parse.urlencode(
                {"query": entry["query"], "page_size": max(1, context.per_query), "page": 1}
            )
            entry["visited_urls"] = []

        active = list(pagination)
        while active:
            next_active = []
            stop_state: str | None = None
            for entry in active:
                query = entry["query"]
                url = entry["url"]
                searches.append({"query": query, "page": entry["pages_fetched"] + 1, "url": url})
                if context.verbose:
                    print_step(f"[dockerhub] searching {query!r}, page {entry['pages_fetched'] + 1}")
                try:
                    payload = policy.fetch_json(url, timeout=context.timeout, fetcher=fetch_dockerhub_page)
                except SearchRequestBudgetExceeded as exc:
                    errors.append(str(exc))
                    entry["state"] = "request-budget"
                    stop_state = "request-budget"
                    break
                except Exception as exc:
                    if context.verbose:
                        print_warn(f"[dockerhub] {query!r} failed: {exc}")
                    if is_rate_limited_error(exc):
                        errors.append(f"{query}: {format_rate_limit_error(self.name, exc)}")
                        entry["state"] = "rate-limited"
                        stop_state = "rate-limited"
                        break
                    errors.append(f"{query}: {exc}")
                    entry["state"] = "error"
                    continue

                entry["pages_fetched"] += 1
                entry["visited_urls"].append(url)
                items = payload.get("results", []) if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    items = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("repo_name", "")
                    description = item.get("short_description") or ""
                    confidence, hits, signals = classify_match(
                        context.keywords,
                        context.min_match_keywords,
                        name,
                        description,
                    )
                    if not confidence:
                        continue
                    results.append(
                        {
                            "plugin": self.name,
                            "kind": "container_image",
                            "query": query,
                            "name": name,
                            "url": f"{api_base}/r/{name}",
                            "description": description,
                            "matched_keywords": hits,
                            "signals": signals,
                            "confidence": confidence,
                            "api_base": api_base,
                        }
                    )

                next_present = isinstance(payload, dict) and "next" in payload
                next_url = payload.get("next") if isinstance(payload, dict) else None
                if next_url is None and next_present:
                    entry["state"] = "complete"
                elif isinstance(next_url, str) and next_url:
                    resolved_next, invalid_reason = _valid_dockerhub_next_url(next_url, api_base)
                    if invalid_reason == "origin":
                        errors.append(f"{query}: dockerhub returned a next page outside the configured API base")
                        entry["state"] = "error"
                    elif invalid_reason or resolved_next is None:
                        errors.append(f"{query}: dockerhub returned invalid pagination metadata")
                        entry["state"] = "invalid-pagination-metadata"
                    elif resolved_next in entry["visited_urls"]:
                        errors.append(f"{query}: dockerhub returned a repeated next page URL")
                        entry["state"] = "pagination-loop"
                    else:
                        entry["url"] = resolved_next
                        next_active.append(entry)
                elif not next_present or next_url == "":
                    entry["state"] = "complete"
                else:
                    errors.append(f"{query}: dockerhub returned invalid pagination metadata")
                    entry["state"] = "invalid-pagination-metadata"

            if stop_state:
                for entry in active:
                    if entry["state"] == "active":
                        entry["state"] = stop_state
                for entry in next_active:
                    if entry["state"] == "active":
                        entry["state"] = stop_state
                break
            active = next_active

        deduped = self._merge_results(results)
        partial_states = {"request-budget", "rate-limited", "error", "partial-error", "pagination-loop", "invalid-pagination-metadata"}
        status = "partial" if deduped and any(entry["state"] in partial_states for entry in pagination) else ("ok" if deduped else "error")

        if context.verbose:
            print_ok(f"[dockerhub] kept {len(deduped)} candidate image hits")
            if deduped:
                print_result_table(self.name, deduped)
                print("")
            print_step(
                f"[dockerhub] requests={policy.requests}/{policy.max_requests}, "
                f"cache_hits={policy.cache_hits}"
            )

        return {
            "name": self.name,
            "status": status,
            "searches": searches,
            "errors": errors,
            "results": deduped,
            "pagination": pagination,
            "request_stats": policy.summary(),
        }


PLUGIN_CLASS = DockerHubPlugin
