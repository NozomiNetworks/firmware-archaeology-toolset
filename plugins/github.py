from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

from .base import (
    PluginContext,
    PluginSetting,
    SurfacePluginBase,
    classify_match,
    download_file,
    fetch_json,
    merge_named_results,
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


def fetch_github_readme(full_name: str, timeout: int, *, policy: SearchPolicy | None = None) -> str:
    """Fetch a repository README from the GitHub API as raw text."""

    url = f"https://api.github.com/repos/{full_name}/readme"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
        },
    )
    def fetch(_url: str, _timeout: int) -> str:
        try:
            with urllib.request.urlopen(request, timeout=_timeout) as response:
                return response.read().decode("utf-8", "ignore")
        except HTTPError as exc:
            if is_rate_limited_error(exc):
                raise
            if exc.code in {403, 404, 429}:
                return ""
            raise

    if policy is None:
        return fetch(url, timeout)
    return policy.fetch_text(
        url,
        timeout=timeout,
        fetcher=fetch,
        headers={"Accept": "application/vnd.github.raw+json"},
    )


class GitHubPlugin(SurfacePluginBase):
    name = "github"
    help_summary = "Search GitHub repositories and download selected repository archives."
    help_examples = (
        "Examples:\n"
        "  fat.py project device-research --keywords \"sdk,portal,connector\"\n"
        "  fat.py project set keywords \"sdk,connector,developer portal\"\n"
        "  fat.py github set downloads-dir output/downloads/lab/github\n"
        "  fat.py github search\n"
        "  fat.py device-research github status\n"
        "  fat.py github select 2,3\n"
        "  fat.py github download selected\n"
    )

    def setting_definitions(self, project: dict, tool_state: dict) -> list[PluginSetting]:
        return [*super().setting_definitions(project, tool_state), *search_setting_definitions()]

    def _merge_results(self, results: list[dict]) -> list[dict]:
        return merge_named_results(results)

    def download(self, context: PluginContext, artifact: dict, downloads_dir: Path) -> dict:
        owner_repo = artifact["name"]
        archive_url = f"https://api.github.com/repos/{owner_repo}/zipball"
        target_dir = downloads_dir / f"{artifact['artifact_num']:03d}_{safe_name(owner_repo)}"
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_path = target_dir / "repository.zip"
        download_file(archive_url, archive_path, timeout=context.timeout)
        readme_text = fetch_github_readme(owner_repo, timeout=context.timeout)
        if readme_text:
            (target_dir / "README.md").write_text(readme_text, encoding="utf-8")
        write_json(target_dir / "artifact.json", artifact)
        return {"status": "downloaded", "path": str(target_dir), "files": ["repository.zip", "artifact.json"] + (["README.md"] if readme_text else [])}

    def run(self, context: PluginContext) -> dict:
        policy = SearchPolicy.from_context(context, self.name)
        metadata_results = []
        searches = []
        errors = []
        pagination = [
            {"query": query, "page": 1, "pages_fetched": 0, "state": "active"}
            for query in context.queries()
        ]
        budget_reported = False

        def report_budget(exc: Exception) -> None:
            nonlocal budget_reported
            if not budget_reported:
                errors.append(str(exc))
                budget_reported = True

        active = list(pagination)
        page_size = max(1, context.per_query)
        while active:
            next_active = []
            stop_state: str | None = None
            for entry in active:
                query = entry["query"]
                page = entry["page"]
                url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
                    {
                        "q": query,
                        "sort": "updated",
                        "order": "desc",
                        "per_page": page_size,
                        "page": page,
                    }
                )
                searches.append({"query": query, "page": page, "url": url})
                if context.verbose:
                    print_step(f"[github] searching {query!r}, page {page}")
                try:
                    payload = policy.fetch_json(url, timeout=context.timeout, fetcher=fetch_json)
                except SearchRequestBudgetExceeded as exc:
                    report_budget(exc)
                    entry["state"] = "request-budget"
                    stop_state = "request-budget"
                    break
                except Exception as exc:
                    if context.verbose:
                        print_warn(f"[github] {query!r} page {page} failed: {exc}")
                    if is_rate_limited_error(exc):
                        errors.append(f"{query}: {format_rate_limit_error(self.name, exc)}")
                        entry["state"] = "rate-limited"
                        stop_state = "rate-limited"
                        break
                    errors.append(f"{query}: {exc}")
                    entry["state"] = "partial-error" if entry["pages_fetched"] else "error"
                    continue

                entry["pages_fetched"] += 1
                items = payload.get("items", []) if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    items = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("full_name", "")
                    description = item.get("description") or ""
                    confidence, hits, signals = classify_match(
                        context.keywords,
                        context.min_match_keywords,
                        name,
                        description,
                    )
                    if not confidence:
                        continue
                    metadata_results.append(
                        {
                            "plugin": self.name,
                            "kind": "repository",
                            "query": query,
                            "name": name,
                            "url": item.get("html_url", ""),
                            "description": description,
                            "matched_keywords": hits,
                            "signals": signals,
                            "confidence": confidence,
                        }
                    )

                total_count = payload.get("total_count") if isinstance(payload, dict) else None
                incomplete = bool(payload.get("incomplete_results")) if isinstance(payload, dict) else False
                if incomplete:
                    entry["state"] = "incomplete-provider-response"
                elif isinstance(total_count, int) and total_count > 1000 and page * page_size >= 1000:
                    entry["state"] = "provider-cap"
                elif len(items) < page_size or (isinstance(total_count, int) and page * page_size >= total_count):
                    entry["state"] = "complete"
                else:
                    entry["page"] = page + 1
                    next_active.append(entry)

            if stop_state:
                for entry in active:
                    if entry["state"] == "active":
                        entry["state"] = stop_state
                for entry in next_active:
                    if entry["state"] == "active":
                        entry["state"] = stop_state
                break
            active = next_active

        deduped = self._merge_results(metadata_results)
        results = []
        enrichment_stopped = False
        for candidate in deduped:
            name = candidate["name"]
            description = candidate.get("description", "")
            readme_text = ""
            if candidate.get("matched_keywords") and not enrichment_stopped:
                if not policy.allow_enrichment():
                    candidate["signals"] = sorted(set(candidate.get("signals", [])) | {"enrichment-skipped:limit"})
                else:
                    try:
                        readme_text = fetch_github_readme(name, timeout=context.timeout, policy=policy)
                    except SearchRequestBudgetExceeded as exc:
                        report_budget(exc)
                        enrichment_stopped = True
                        candidate["signals"] = sorted(set(candidate.get("signals", [])) | {"enrichment-skipped:request-budget"})
                    except Exception as exc:
                        if is_rate_limited_error(exc):
                            errors.append(format_rate_limit_error(self.name, exc))
                            candidate["signals"] = sorted(set(candidate.get("signals", [])) | {"readme-rate-limited"})
                            enrichment_stopped = True
                        else:
                            errors.append(f"{name}: README enrichment failed: {exc}")
                            candidate["signals"] = sorted(set(candidate.get("signals", [])) | {"readme-error"})
            elif candidate.get("matched_keywords") and enrichment_stopped:
                candidate["signals"] = sorted(set(candidate.get("signals", [])) | {"enrichment-skipped:stopped"})
            if readme_text:
                confidence, hits, signals = classify_match(
                    context.keywords,
                    context.min_match_keywords,
                    name,
                    description,
                    readme_text,
                )
                candidate["confidence"] = confidence or candidate["confidence"]
                candidate["matched_keywords"] = sorted(set(candidate.get("matched_keywords", [])) | set(hits))
                candidate["signals"] = sorted(set(candidate.get("signals", [])) | set(signals) | {"readme"})
            results.append(candidate)

        deduped = self._merge_results(results)
        partial_states = {"request-budget", "rate-limited", "error", "partial-error", "provider-cap", "incomplete-provider-response"}
        status = "partial" if deduped and any(entry["state"] in partial_states for entry in pagination) else ("ok" if deduped else "error")

        if context.verbose:
            print_ok(f"[github] kept {len(deduped)} candidate repository hits")
            if deduped:
                print_result_table(self.name, deduped)
                print("")
            print_step(
                f"[github] requests={policy.requests}/{policy.max_requests}, "
                f"enrichments={policy.enrichments}/{policy.max_enrichments}, cache_hits={policy.cache_hits}"
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


PLUGIN_CLASS = GitHubPlugin
