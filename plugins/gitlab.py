from __future__ import annotations

import html
import re
import urllib.parse
from pathlib import Path
from urllib.error import HTTPError

from .base import (
    PluginContext,
    PluginSetting,
    SurfacePluginBase,
    classify_match,
    download_file,
    fetch_json_with_headers,
    fetch_text,
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


PROJECT_LINK_RE = re.compile(r'href="(/[^"/\s]+/[^"\s?#]+)"')
TAG_RE = re.compile(r"<[^>]+>")


def fetch_gitlab_page(url: str, timeout: int) -> dict:
    """Fetch one GitLab API page while retaining its pagination headers."""

    return fetch_json_with_headers(url, timeout)


def fetch_gitlab_readme(
    path_with_namespace: str,
    timeout: int,
    *,
    policy: SearchPolicy | None = None,
) -> str:
    """Fetch a GitLab project README from common default-branch locations."""

    candidates = [
        f"https://gitlab.com/{path_with_namespace}/-/raw/main/README.md",
        f"https://gitlab.com/{path_with_namespace}/-/raw/master/README.md",
        f"https://gitlab.com/{path_with_namespace}/-/blob/main/README.md",
        f"https://gitlab.com/{path_with_namespace}/-/blob/master/README.md",
    ]
    for url in candidates:
        try:
            if policy is None:
                body = fetch_text(url, timeout=timeout, accept="text/plain,text/html")
            else:
                body = policy.fetch_text(
                    url,
                    timeout=timeout,
                    fetcher=lambda candidate_url, candidate_timeout: fetch_text(
                        candidate_url,
                        timeout=candidate_timeout,
                        accept="text/plain,text/html",
                    ),
                    headers={"Accept": "text/plain,text/html"},
                )
        except HTTPError as exc:
            if is_rate_limited_error(exc):
                raise
            if exc.code in {403, 404, 429}:
                continue
            raise
        if "<html" in body.lower():
            body = " ".join(TAG_RE.sub(" ", body).split())
        if body:
            return body
    return ""


class GitLabPlugin(SurfacePluginBase):
    name = "gitlab"
    help_summary = "Search GitLab projects and download selected repository archives."
    help_examples = (
        "Examples:\n"
        "  fat.py project device-research --keywords \"sdk,platform,archive\"\n"
        "  fat.py gitlab set min-match-keywords 3\n"
        "  fat.py gitlab set downloads-dir output/downloads/lab/gitlab\n"
        "  fat.py gitlab search\n"
        "  fat.py device-research gitlab status\n"
        "  fat.py gitlab select 1\n"
        "  fat.py gitlab download selected\n"
    )

    def setting_definitions(self, project: dict, tool_state: dict) -> list[PluginSetting]:
        return [*super().setting_definitions(project, tool_state), *search_setting_definitions()]

    def _merge_results(self, results: list[dict]) -> list[dict]:
        return merge_named_results(results)

    def download(self, context: PluginContext, artifact: dict, downloads_dir: Path) -> dict:
        target_dir = downloads_dir / f"{artifact['artifact_num']:03d}_{safe_name(artifact['name'])}"
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_url = f"https://gitlab.com/{artifact['name']}/-/archive/main/{artifact['name'].split('/')[-1]}-main.zip"
        archive_path = target_dir / "repository.zip"
        download_file(archive_url, archive_path, timeout=context.timeout)
        readme_text = fetch_gitlab_readme(artifact["name"], timeout=context.timeout)
        if readme_text:
            (target_dir / "README.md").write_text(readme_text, encoding="utf-8")
        write_json(target_dir / "artifact.json", artifact)
        return {"status": "downloaded", "path": str(target_dir), "files": ["repository.zip", "artifact.json"] + (["README.md"] if readme_text else [])}

    def _scrape_explore_page(
        self,
        url: str,
        context: PluginContext,
        query: str,
        *,
        policy: SearchPolicy | None = None,
    ) -> list[dict]:
        if policy is None:
            body = fetch_text(url, timeout=context.timeout, accept="text/html,application/xhtml+xml")
        else:
            body = policy.fetch_text(
                url,
                timeout=context.timeout,
                fetcher=lambda candidate_url, candidate_timeout: fetch_text(
                    candidate_url,
                    timeout=candidate_timeout,
                    accept="text/html,application/xhtml+xml",
                ),
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
        results = []
        seen = set()
        for match in PROJECT_LINK_RE.finditer(body):
            path = html.unescape(match.group(1))
            if path.startswith(("/assets/", "/users/", "/explore/", "/help/", "/search", "/-/")):
                continue
            if path in seen:
                continue
            seen.add(path)
            snippet = body[max(0, match.start() - 400) : min(len(body), match.end() + 400)]
            text = " ".join(TAG_RE.sub(" ", snippet).split())
            confidence, hits, signals = classify_match(
                context.keywords,
                context.min_match_keywords,
                path.strip("/"),
                text,
            )
            if not confidence:
                continue
            results.append(
                {
                    "plugin": self.name,
                    "kind": "project",
                    "query": query,
                    "name": path.strip("/"),
                    "url": f"https://gitlab.com{path}",
                    "description": text[:220],
                    "matched_keywords": hits,
                    "signals": signals + ["html-fallback"],
                    "confidence": confidence,
                }
            )
        return results

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
                api_url = "https://gitlab.com/api/v4/projects?" + urllib.parse.urlencode(
                    {"search": query, "simple": "true", "per_page": page_size, "page": page}
                )
                searches.append({"query": query, "page": page, "url": api_url, "mode": "api"})
                if context.verbose:
                    print_step(f"[gitlab] searching {query!r}, page {page} via api")
                try:
                    response = policy.fetch_json(api_url, timeout=context.timeout, fetcher=fetch_gitlab_page)
                except HTTPError as exc:
                    if is_rate_limited_error(exc):
                        errors.append(f"{query}: {format_rate_limit_error(self.name, exc)}")
                        entry["state"] = "rate-limited"
                        stop_state = "rate-limited"
                        break
                    if context.verbose:
                        print_warn(f"[gitlab] api blocked for {query!r} with {exc.code}, switching to html")
                    errors.append(f"{query}: api blocked with {exc.code}")
                    html_url = "https://gitlab.com/explore/projects?" + urllib.parse.urlencode({"name": query})
                    searches.append({"query": query, "url": html_url, "mode": "html-fallback"})
                    try:
                        metadata_results.extend(self._scrape_explore_page(html_url, context, query, policy=policy))
                        entry["fallback"] = "html"
                        entry["state"] = "html-fallback"
                    except SearchRequestBudgetExceeded as fallback_exc:
                        report_budget(fallback_exc)
                        entry["state"] = "request-budget"
                        stop_state = "request-budget"
                        break
                    except Exception as fallback_exc:
                        if context.verbose:
                            print_warn(f"[gitlab] html fallback failed for {query!r}: {fallback_exc}")
                        if is_rate_limited_error(fallback_exc):
                            errors.append(f"{query}: {format_rate_limit_error(self.name, fallback_exc)}")
                            entry["state"] = "rate-limited"
                            stop_state = "rate-limited"
                            break
                        errors.append(f"{query}: html fallback failed: {fallback_exc}")
                        entry["state"] = "error"
                    continue
                except SearchRequestBudgetExceeded as exc:
                    report_budget(exc)
                    entry["state"] = "request-budget"
                    stop_state = "request-budget"
                    break
                except Exception as exc:
                    if context.verbose:
                        print_warn(f"[gitlab] {query!r} page {page} failed: {exc}")
                    if is_rate_limited_error(exc):
                        errors.append(f"{query}: {format_rate_limit_error(self.name, exc)}")
                        entry["state"] = "rate-limited"
                        stop_state = "rate-limited"
                        break
                    errors.append(f"{query}: {exc}")
                    entry["state"] = "partial-error" if entry["pages_fetched"] else "error"
                    continue

                payload = response.get("payload", []) if isinstance(response, dict) else []
                headers = response.get("headers", {}) if isinstance(response, dict) else {}
                if not isinstance(payload, list):
                    payload = []
                if not isinstance(headers, dict):
                    headers = {}
                entry["pages_fetched"] += 1
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("path_with_namespace", "")
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
                            "kind": "project",
                            "query": query,
                            "name": name,
                            "url": item.get("web_url", ""),
                            "description": description,
                            "matched_keywords": hits,
                            "signals": signals,
                            "confidence": confidence,
                        }
                    )

                raw_next_page = headers.get("X-Next-Page", headers.get("x-next-page", ""))
                next_page = str(raw_next_page).strip()
                if next_page and not next_page.isdigit():
                    entry["state"] = "invalid-pagination-metadata"
                    errors.append(f"{query}: invalid GitLab X-Next-Page value")
                elif next_page and int(next_page) > page:
                    entry["page"] = int(next_page)
                    next_active.append(entry)
                elif next_page and int(next_page) != 0:
                    entry["state"] = "invalid-pagination-metadata"
                    errors.append(f"{query}: GitLab X-Next-Page did not advance")
                else:
                    entry["state"] = "complete"

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
                        readme_text = fetch_gitlab_readme(name, timeout=context.timeout, policy=policy)
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
        partial_states = {"request-budget", "rate-limited", "error", "partial-error", "invalid-pagination-metadata"}
        status = "partial" if deduped and any(entry["state"] in partial_states for entry in pagination) else ("ok" if deduped else "error")

        if context.verbose:
            print_ok(f"[gitlab] kept {len(deduped)} candidate project hits")
            if deduped:
                print_result_table(self.name, deduped)
                print("")
            print_step(
                f"[gitlab] requests={policy.requests}/{policy.max_requests}, "
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


PLUGIN_CLASS = GitLabPlugin
