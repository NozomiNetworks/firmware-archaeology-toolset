from __future__ import annotations

import unittest
import tempfile
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.dockerhub import DockerHubPlugin
from plugins.github import GitHubPlugin
from plugins.gitlab import GitLabPlugin


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def sample_result(plugin: str, query: str, name: str, hits: list[str], *, description: str = "desc", confidence: str = "review") -> dict:
    return {
        "plugin": plugin,
        "kind": "artifact",
        "query": query,
        "name": name,
        "url": f"https://example.test/{name}",
        "description": description,
        "matched_keywords": hits,
        "signals": [f"keyword:{hit}" for hit in hits],
        "confidence": confidence,
    }


class SourcePluginMergeTests(unittest.TestCase):
    def context(
        self,
        *keywords: str,
        min_match_keywords: int = 1,
        verbose: bool = False,
        max_requests: int = 50,
        max_enrichments: int = 10,
        request_delay: float = 0.0,
        cache_ttl: int = 0,
        cache_dir: Path | None = None,
    ):
        return SimpleNamespace(
            keywords=list(keywords), per_query=10, timeout=5,
            min_match_keywords=min_match_keywords, session_id="test",
            verbose=verbose, api_base=None,
            max_requests=max_requests, max_enrichments=max_enrichments,
            request_delay=request_delay, cache_ttl=cache_ttl, cache_dir=cache_dir,
            queries=lambda: list(dict.fromkeys(keywords)),
        )

    def test_github_merge_results_unions_queries_and_prefers_more_specific_match(self) -> None:
        plugin = GitHubPlugin()
        merged = plugin._merge_results(
            [
                sample_result("github", "sdk", "vendor/sdk", ["sdk"], description="generic", confidence="review"),
                sample_result("github", "portal", "vendor/sdk", ["sdk", "portal"], description="better", confidence="high"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_queries"], ["portal", "sdk"])
        self.assertEqual(merged[0]["matched_keywords"], ["portal", "sdk"])
        self.assertEqual(merged[0]["description"], "better")
        self.assertEqual(merged[0]["confidence"], "high")

    def test_gitlab_merge_results_unions_queries_and_prefers_more_specific_match(self) -> None:
        plugin = GitLabPlugin()
        merged = plugin._merge_results(
            [
                sample_result("gitlab", "sdk", "vendor/sdk", ["sdk"], description="generic", confidence="review"),
                sample_result("gitlab", "portal", "vendor/sdk", ["sdk", "portal"], description="better", confidence="high"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_queries"], ["portal", "sdk"])
        self.assertEqual(merged[0]["matched_keywords"], ["portal", "sdk"])
        self.assertEqual(merged[0]["description"], "better")
        self.assertEqual(merged[0]["confidence"], "high")

    def test_dockerhub_merge_results_unions_queries_and_prefers_more_specific_match(self) -> None:
        plugin = DockerHubPlugin()
        merged = plugin._merge_results(
            [
                sample_result("dockerhub", "sdk", "vendor/sdk", ["sdk"], description="generic", confidence="review"),
                sample_result("dockerhub", "portal", "vendor/sdk", ["sdk", "portal"], description="better", confidence="high"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["matched_queries"], ["portal", "sdk"])
        self.assertEqual(merged[0]["matched_keywords"], ["portal", "sdk"])
        self.assertEqual(merged[0]["description"], "better")
        self.assertEqual(merged[0]["confidence"], "high")

    def test_dockerhub_run_filters_results_and_records_errors(self) -> None:
        payloads = [
            {"results": [
                {"repo_name": "vendor/sdk", "short_description": "sdk platform"},
                {"repo_name": "vendor/unrelated", "short_description": "other"},
            ]},
        ]
        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=payloads + [RuntimeError("offline")]):
            result = DockerHubPlugin().run(self.context("sdk", "offline", verbose=True))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["results"][0]["name"], "vendor/sdk")
        self.assertEqual(len(result["errors"]), 1)

    def test_dockerhub_first_page_uses_redirect_blocking_fetcher(self) -> None:
        redirect = urllib.error.HTTPError(
            "https://hub.docker.com/v2/search/repositories/", 302, "Found",
            {"Location": "http://127.0.0.1/private"}, None,
        )
        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=redirect) as safe_fetch, patch(
            "plugins.dockerhub.fetch_json", return_value={"results": []}
        ) as redirect_following_fetch:
            result = DockerHubPlugin().run(self.context("sdk"))

        safe_fetch.assert_called_once()
        redirect_following_fetch.assert_not_called()
        self.assertEqual(result["pagination"][0]["state"], "error")
        self.assertEqual(result["status"], "error")

    def test_search_page_sizes_follow_per_query_limit(self) -> None:
        context = self.context("sdk")
        context.per_query = 100
        for module_name, fetch_name, plugin, expected_parameter, response in (
            ("plugins.github", "fetch_json", GitHubPlugin(), "per_page", {"items": []}),
            ("plugins.gitlab", "fetch_gitlab_page", GitLabPlugin(), "per_page", {"payload": [], "headers": {}}),
            ("plugins.dockerhub", "fetch_dockerhub_page", DockerHubPlugin(), "page_size", {"results": []}),
        ):
            with self.subTest(plugin=plugin.name), patch(f"{module_name}.{fetch_name}", return_value=response) as fetch_json:
                plugin.run(context)
            request_url = fetch_json.call_args.args[0]
            self.assertIn(f"{expected_parameter}=100", request_url)

    def test_github_paginates_queries_breadth_first_and_reports_completion(self) -> None:
        context = self.context("sdk", "portal", max_enrichments=0)
        context.per_query = 1
        calls: list[tuple[str, int]] = []

        def pages(url: str, _timeout: int) -> dict:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            query = params["q"][0]
            page = int(params["page"][0])
            calls.append((query, page))
            return {
                "total_count": 2,
                "incomplete_results": False,
                "items": [{
                    "full_name": f"vendor/{query}-{page}",
                    "description": query,
                    "html_url": f"https://github.com/vendor/{query}-{page}",
                }],
            }

        with patch("plugins.github.fetch_json", side_effect=pages):
            result = GitHubPlugin().run(context)

        self.assertEqual(calls, [("sdk", 1), ("portal", 1), ("sdk", 2), ("portal", 2)])
        self.assertEqual([entry["pages_fetched"] for entry in result["pagination"]], [2, 2])
        self.assertEqual([entry["state"] for entry in result["pagination"]], ["complete", "complete"])

    def test_github_request_budget_preserves_breadth_first_partial_results(self) -> None:
        context = self.context("sdk", "portal", max_requests=3, max_enrichments=0)
        context.per_query = 1
        calls: list[tuple[str, int]] = []

        def pages(url: str, _timeout: int) -> dict:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            query = params["q"][0]
            page = int(params["page"][0])
            calls.append((query, page))
            return {
                "total_count": 2,
                "items": [{
                    "full_name": f"vendor/{query}-{page}",
                    "description": query,
                    "html_url": f"https://github.com/vendor/{query}-{page}",
                }],
            }

        with patch("plugins.github.fetch_json", side_effect=pages):
            result = GitHubPlugin().run(context)

        self.assertEqual(calls, [("sdk", 1), ("portal", 1), ("sdk", 2)])
        states = {entry["query"]: entry["state"] for entry in result["pagination"]}
        self.assertEqual(states["sdk"], "complete")
        self.assertEqual(states["portal"], "request-budget")
        self.assertEqual({item["name"] for item in result["results"]}, {"vendor/sdk-1", "vendor/sdk-2", "vendor/portal-1"})

    def test_github_marks_incomplete_provider_responses_without_looping(self) -> None:
        payload = {
            "total_count": 100,
            "incomplete_results": True,
            "items": [{
                "full_name": "vendor/sdk",
                "description": "sdk",
                "html_url": "https://github.com/vendor/sdk",
            }],
        }
        with patch("plugins.github.fetch_json", return_value=payload) as fetch_json:
            result = GitHubPlugin().run(self.context("sdk", max_enrichments=0))

        fetch_json.assert_called_once()
        self.assertEqual(result["pagination"], [{
            "query": "sdk", "page": 1, "pages_fetched": 1,
            "state": "incomplete-provider-response",
        }])

    def test_later_page_rate_limits_preserve_results_and_mark_partial_queries(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        for module_name, plugin, fetch_name, response, url_query_key in (
            ("plugins.github", GitHubPlugin(), "fetch_json", {"items": [{"full_name": "vendor/sdk", "description": "sdk", "html_url": "https://github.com/vendor/sdk"}], "total_count": 2}, "q"),
            ("plugins.gitlab", GitLabPlugin(), "fetch_gitlab_page", {"payload": [{"path_with_namespace": "vendor/sdk", "description": "sdk", "web_url": "https://gitlab.com/vendor/sdk"}], "headers": {"X-Next-Page": "2"}}, "search"),
            ("plugins.dockerhub", DockerHubPlugin(), "fetch_dockerhub_page", {"results": [{"repo_name": "vendor/sdk", "short_description": "sdk"}], "next": "https://hub.docker.com/v2/search/repositories/?query=sdk&page=2"}, "query"),
        ):
            context = self.context("sdk", max_enrichments=0)
            context.per_query = 1
            def fetch(url, _timeout):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)[url_query_key][0]
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("page", [1])[0])
                if page > 1:
                    raise rate_limited
                return response
            with self.subTest(plugin=plugin.name), patch(f"{module_name}.{fetch_name}", side_effect=fetch):
                result = plugin.run(context)
            self.assertTrue(result["results"])
            self.assertEqual(result["pagination"][0]["pages_fetched"], 1)
            self.assertEqual(result["pagination"][0]["state"], "rate-limited")
            self.assertEqual(result["status"], "partial")

    def test_paginated_cache_reuses_later_pages(self) -> None:
        context = self.context("sdk", cache_ttl=3600, max_enrichments=0)
        context.per_query = 1
        with tempfile.TemporaryDirectory() as temp_dir:
            context.cache_dir = Path(temp_dir)
            def pages(url: str, _timeout: int) -> dict:
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0])
                return {
                    "total_count": 2,
                    "items": [{"full_name": f"vendor/sdk-{page}", "description": "sdk", "html_url": f"https://github.com/vendor/sdk-{page}"}],
                }
            with patch("plugins.github.fetch_json", side_effect=pages) as fetch_json:
                GitHubPlugin().run(context)
                fetch_json.reset_mock()
                result = GitHubPlugin().run(context)
            fetch_json.assert_not_called()
            self.assertEqual(result["request_stats"]["cache_hits"], 2)
            self.assertEqual(result["pagination"][0]["pages_fetched"], 2)

    def test_gitlab_invalid_next_page_header_is_reported_as_partial(self) -> None:
        response = {
            "payload": [{"path_with_namespace": "vendor/sdk", "description": "sdk", "web_url": "https://gitlab.com/vendor/sdk"}],
            "headers": {"X-Next-Page": "abc"},
        }
        with patch("plugins.gitlab.fetch_gitlab_page", return_value=response):
            result = GitLabPlugin().run(self.context("sdk"))
        self.assertEqual(result["pagination"][0]["state"], "invalid-pagination-metadata")
        self.assertTrue(result["results"])
        self.assertEqual(result["status"], "partial")

    def test_github_budget_smaller_than_query_count_preserves_fairness(self) -> None:
        context = self.context("sdk", "portal", max_requests=1, max_enrichments=0)
        context.per_query = 1
        calls: list[str] = []
        def fetch(url: str, _timeout: int) -> dict:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
            calls.append(query)
            return {"total_count": 5, "items": [{"full_name": f"vendor/{query}", "description": query, "html_url": f"https://github.com/vendor/{query}"}]}
        with patch("plugins.github.fetch_json", side_effect=fetch):
            result = GitHubPlugin().run(context)
        self.assertEqual(calls, ["sdk"])
        self.assertEqual([row["state"] for row in result["pagination"]], ["request-budget", "request-budget"])
        self.assertEqual(result["status"], "partial")

    def test_github_exhausted_query_does_not_block_other_queries(self) -> None:
        context = self.context("sdk", "portal", max_enrichments=0)
        context.per_query = 1
        calls: list[tuple[str, int]] = []
        def fetch(url: str, _timeout: int) -> dict:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            query, page = params["q"][0], int(params["page"][0])
            calls.append((query, page))
            total = 1 if query == "sdk" else 2
            return {"total_count": total, "items": [{"full_name": f"vendor/{query}-{page}", "description": query, "html_url": f"https://github.com/vendor/{query}-{page}"}]}
        with patch("plugins.github.fetch_json", side_effect=fetch):
            result = GitHubPlugin().run(context)
        self.assertEqual(calls, [("sdk", 1), ("portal", 1), ("portal", 2)])
        states = {row["query"]: row["state"] for row in result["pagination"]}
        self.assertEqual(states, {"sdk": "complete", "portal": "complete"})

    def test_gitlab_html_fallback_is_not_reported_as_api_pagination_complete(self) -> None:
        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=urllib.error.HTTPError("url", 403, "blocked", {}, None)), patch(
            "plugins.gitlab.fetch_text", return_value='<a href="/vendor/sdk">sdk</a>'
        ):
            result = GitLabPlugin().run(self.context("sdk"))
        self.assertEqual(result["pagination"][0]["pages_fetched"], 0)
        self.assertEqual(result["pagination"][0]["state"], "html-fallback")

    def test_dockerhub_repeated_next_url_stops_pagination(self) -> None:
        repeated = "https://hub.docker.com/v2/search/repositories/?query=sdk&page=2"
        payload = {"results": [{"repo_name": "vendor/sdk", "short_description": "sdk"}], "next": repeated}
        with patch("plugins.dockerhub.fetch_dockerhub_page", return_value=payload) as fetch_json:
            result = DockerHubPlugin().run(self.context("sdk"))
        self.assertEqual(fetch_json.call_count, 2)
        self.assertEqual(result["pagination"][0]["state"], "pagination-loop")
        self.assertEqual(result["status"], "partial")

    def test_dockerhub_partial_error_keeps_prior_results(self) -> None:
        context = self.context("sdk", "offline")
        payload = {"results": [{"repo_name": "vendor/sdk", "short_description": "sdk"}], "next": None}
        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=[payload, RuntimeError("offline")]):
            result = DockerHubPlugin().run(context)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pagination"][1]["state"], "error")

    def test_gitlab_uses_next_page_header_and_keeps_fallback_single_page(self) -> None:
        context = self.context("sdk", "portal", max_enrichments=0)
        context.per_query = 1
        calls: list[tuple[str, int]] = []

        def pages(url: str, _timeout: int) -> dict:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            query = params["search"][0]
            page = int(params["page"][0])
            calls.append((query, page))
            return {
                "payload": [{
                    "path_with_namespace": f"vendor/{query}-{page}",
                    "description": query,
                    "web_url": f"https://gitlab.com/vendor/{query}-{page}",
                }],
                "headers": {"X-Next-Page": "2" if (query, page) == ("sdk", 1) else ""},
            }

        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=pages):
            result = GitLabPlugin().run(context)

        self.assertEqual(calls, [("sdk", 1), ("portal", 1), ("sdk", 2)])
        states = {entry["query"]: entry["state"] for entry in result["pagination"]}
        self.assertEqual(states, {"sdk": "complete", "portal": "complete"})

    def test_dockerhub_follows_next_urls_in_round_robin_order(self) -> None:
        context = self.context("sdk", "portal")
        context.per_query = 1
        calls: list[tuple[str, int]] = []

        def pages(url: str, _timeout: int) -> dict:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            query = params["query"][0]
            page = int(params["page"][0])
            calls.append((query, page))
            next_url = None
            if page == 1:
                next_url = "https://hub.docker.com/v2/search/repositories/?" + urllib.parse.urlencode(
                    {"query": query, "page_size": 1, "page": 2}
                )
            return {
                "results": [{"repo_name": f"vendor/{query}-{page}", "short_description": query}],
                "next": next_url,
            }

        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=pages):
            result = DockerHubPlugin().run(context)

        self.assertEqual(calls, [("sdk", 1), ("portal", 1), ("sdk", 2), ("portal", 2)])
        self.assertEqual([entry["pages_fetched"] for entry in result["pagination"]], [2, 2])
        self.assertTrue(all(entry["state"] == "complete" for entry in result["pagination"]))

    def test_dockerhub_rejects_next_pages_outside_its_api_base(self) -> None:
        payload = {
            "results": [{"repo_name": "vendor/sdk", "short_description": "sdk"}],
            "next": "https://attacker.test/v2/search/repositories/?page=2",
        }
        with patch("plugins.dockerhub.fetch_dockerhub_page", return_value=payload) as fetch_json:
            result = DockerHubPlugin().run(self.context("sdk"))

        fetch_json.assert_called_once()
        self.assertEqual(result["pagination"][0]["state"], "error")
        self.assertIn("outside the configured API base", result["errors"][0])

    def test_dockerhub_rejects_next_urls_outside_search_path_and_malformed_values(self) -> None:
        for next_value, expected_state in (
            ("https://hub.docker.com/other/path", "invalid-pagination-metadata"),
            ({"unexpected": "object"}, "invalid-pagination-metadata"),
        ):
            payload = {"results": [{"repo_name": "vendor/sdk", "short_description": "sdk"}], "next": next_value}
            with self.subTest(next=next_value), patch("plugins.dockerhub.fetch_dockerhub_page", return_value=payload) as fetch_json:
                result = DockerHubPlugin().run(self.context("sdk"))
            fetch_json.assert_called_once()
            self.assertEqual(result["pagination"][0]["state"], expected_state)
            self.assertEqual(result["status"], "partial")

    def test_dockerhub_settings_use_default_and_custom_api_base(self) -> None:
        plugin = DockerHubPlugin()
        project = {"plugins": {}}
        self.assertEqual(plugin.build_api_base(plugin.ensure_state(project)), plugin.default_api_base)
        args = SimpleNamespace(selection="api-base", value="http://registry.test/hub/")
        self.assertEqual(plugin.set("project", project, args), 0)
        self.assertEqual(plugin.build_api_base(project["plugins"]["dockerhub"]), "http://registry.test/hub")
        args.value = "none"
        self.assertEqual(plugin.set("project", project, args), 0)
        self.assertEqual(plugin.build_api_base(project["plugins"]["dockerhub"]), plugin.default_api_base)

    def test_source_search_policy_settings_are_persisted_and_applied(self) -> None:
        plugin = GitHubPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            project = {"name": "demo", "keywords": ["sdk"], "output_dir": temp_dir, "plugins": {}}
            plugin.ensure_state(project)
            for selection, value in (
                ("max-requests", "3"),
                ("max-enrichments", "2"),
                ("request-delay", "0.25"),
                ("cache-ttl", "120"),
            ):
                self.assertEqual(
                    plugin.set(
                        "demo",
                        project,
                        SimpleNamespace(selection=selection, value=value, quiet=True),
                    ),
                    0,
                )
            context = plugin.build_context(
                "demo",
                project,
                SimpleNamespace(quiet=True, state_file=Path(temp_dir) / ".fat.json"),
                project["plugins"]["github"],
            )
        self.assertEqual(context.max_requests, 3)
        self.assertEqual(context.max_enrichments, 2)
        self.assertEqual(context.request_delay, 0.25)
        self.assertEqual(context.cache_ttl, 120)

    def test_rate_limited_search_is_persisted_with_partial_state(self) -> None:
        plugin = GitHubPlugin()
        with tempfile.TemporaryDirectory() as temp_dir:
            project = {"name": "demo", "keywords": ["sdk", "portal"], "output_dir": temp_dir, "plugins": {}}
            plugin.ensure_state(project)
            args = SimpleNamespace(quiet=True, state_file=Path(temp_dir) / ".fat.json")
            rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {"Retry-After": "30"}, None)
            with patch("plugins.github.fetch_json", side_effect=rate_limited) as fetch_json:
                self.assertEqual(plugin.search("demo", project, args), 0)
            fetch_json.assert_called_once()
            persisted = project["plugins"]["github"]
            self.assertEqual(
                persisted["report_data"]["plugins"][0]["errors"][0],
                "sdk: github: rate limited (HTTP 429); retry after 30",
            )
            self.assertTrue(persisted["run_history"])

    def test_dockerhub_run_returns_error_when_all_searches_are_empty(self) -> None:
        with patch("plugins.dockerhub.fetch_dockerhub_page", return_value={"results": []}):
            result = DockerHubPlugin().run(self.context("missing"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["results"], [])

    def test_dockerhub_download_writes_metadata_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "plugins.dockerhub.fetch_json",
            side_effect=[{"name": "vendor/sdk"}, {"results": []}],
        ):
            result = DockerHubPlugin().download(
                self.context("sdk"),
                {"artifact_num": 1, "name": "vendor/sdk"},
                Path(temp_dir),
            )
            self.assertEqual(result["status"], "downloaded")
            self.assertTrue((Path(temp_dir) / "001_vendor-sdk" / "artifact.json").exists())

    def test_github_run_rechecks_matching_readme_and_downloads_archive(self) -> None:
        payload = {"items": [{"full_name": "vendor/sdk", "description": "sdk", "html_url": "https://github.com/vendor/sdk"}]}
        with patch("plugins.github.fetch_json", return_value=payload), patch(
            "plugins.github.fetch_github_readme", return_value="sdk portal documentation"
        ):
            result = GitHubPlugin().run(self.context("sdk", verbose=True))
        self.assertEqual(result["results"][0]["signals"][-1], "readme")

        with tempfile.TemporaryDirectory() as temp_dir, patch("plugins.github.download_file"), patch(
            "plugins.github.fetch_github_readme", return_value="README"
        ):
            downloaded = GitHubPlugin().download(
                self.context("sdk"), {"artifact_num": 2, "name": "vendor/sdk"}, Path(temp_dir)
            )
        self.assertIn("README.md", downloaded["files"])

    def test_github_enriches_duplicate_repository_once(self) -> None:
        payload = {
            "items": [
                {
                    "full_name": "vendor/sdk",
                    "description": "sdk",
                    "html_url": "https://github.com/vendor/sdk",
                }
            ]
        }
        with patch("plugins.github.fetch_json", return_value=payload), patch(
            "plugins.github.fetch_github_readme", return_value="sdk portal"
        ) as readme:
            result = GitHubPlugin().run(self.context("sdk", "portal"))
        self.assertEqual(len(result["results"]), 1)
        readme.assert_called_once_with("vendor/sdk", timeout=5, policy=readme.call_args.kwargs["policy"])
        self.assertEqual(result["request_stats"]["enrichments"], 1)

    def test_github_enrichment_limit_keeps_metadata_candidates_selectable(self) -> None:
        payload = {
            "items": [
                {"full_name": "vendor/one", "description": "sdk", "html_url": "https://github.com/vendor/one"},
                {"full_name": "vendor/two", "description": "sdk", "html_url": "https://github.com/vendor/two"},
            ]
        }
        with patch("plugins.github.fetch_json", return_value=payload), patch(
            "plugins.github.fetch_github_readme", return_value=""
        ) as readme:
            result = GitHubPlugin().run(self.context("sdk", max_enrichments=1))
        self.assertEqual({item["name"] for item in result["results"]}, {"vendor/one", "vendor/two"})
        self.assertEqual(readme.call_count, 1)
        self.assertEqual(result["request_stats"]["enrichments_skipped"], 1)
        self.assertTrue(any("enrichment" in signal for item in result["results"] for signal in item["signals"]))

    def test_github_search_cache_avoids_repeat_upstream_calls(self) -> None:
        payload = {"items": [{"full_name": "vendor/sdk", "description": "sdk", "html_url": "https://github.com/vendor/sdk"}]}
        with tempfile.TemporaryDirectory() as temp_dir:
            context = self.context("sdk", cache_ttl=3600, cache_dir=Path(temp_dir))
            with patch("plugins.github.fetch_json", return_value=payload) as first_api, patch(
                "plugins.github.urllib.request.urlopen", return_value=_Response(b"sdk")
            ) as first_readme:
                GitHubPlugin().run(context)
            with patch("plugins.github.fetch_json", side_effect=AssertionError("API cache miss")) as second_api, patch(
                "plugins.github.urllib.request.urlopen", side_effect=AssertionError("README cache miss")
            ) as second_readme:
                result = GitHubPlugin().run(context)
            self.assertEqual(result["request_stats"]["cache_hits"], 2)
            first_api.assert_called_once()
            first_readme.assert_called_once()
            second_api.assert_not_called()
            second_readme.assert_not_called()

    def test_github_request_budget_reports_partial_results(self) -> None:
        payload = {"items": [{"full_name": "vendor/sdk", "description": "sdk", "html_url": "https://github.com/vendor/sdk"}]}
        with patch("plugins.github.fetch_json", return_value=payload) as fetch_json:
            result = GitHubPlugin().run(self.context("sdk", "portal", max_requests=1))
        self.assertEqual(fetch_json.call_count, 1)
        self.assertTrue(result["results"])
        self.assertTrue(any("request budget" in error for error in result["errors"]))

    def test_github_readme_rate_limit_is_reported_without_losing_metadata(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        payload = {"items": [{"full_name": "vendor/sdk", "description": "sdk", "html_url": "https://github.com/vendor/sdk"}]}
        with patch("plugins.github.fetch_json", return_value=payload), patch(
            "plugins.github.fetch_github_readme", side_effect=rate_limited
        ):
            result = GitHubPlugin().run(self.context("sdk"))
        self.assertTrue(result["results"])
        self.assertTrue(any("rate limited" in error for error in result["errors"]))

    def test_github_policy_readme_rate_limit_is_not_retried(self) -> None:
        from fatlib.search import SearchPolicy

        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {"Retry-After": "0"}, None)
        with patch("plugins.github.urllib.request.urlopen", side_effect=rate_limited) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                from plugins.github import fetch_github_readme
                fetch_github_readme("vendor/sdk", 5, policy=SearchPolicy("github"))
        urlopen.assert_called_once()

    def test_github_rate_limit_stops_following_queries(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        with patch("plugins.github.fetch_json", side_effect=rate_limited) as fetch_json:
            result = GitHubPlugin().run(self.context("sdk", "portal"))
        fetch_json.assert_called_once()
        self.assertEqual(len(result["errors"]), 1)

    def test_github_readme_handles_expected_http_errors_and_propagates_other_errors(self) -> None:
        for code in (403, 404):
            error = urllib.error.HTTPError("url", code, "blocked", {}, None)
            with patch("plugins.github.urllib.request.urlopen", side_effect=error):
                from plugins.github import fetch_github_readme
                self.assertEqual(fetch_github_readme("vendor/sdk", 5), "")

        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        with patch("plugins.github.urllib.request.urlopen", side_effect=rate_limited):
            from plugins.github import fetch_github_readme
            with self.assertRaises(urllib.error.HTTPError):
                fetch_github_readme("vendor/sdk", 5)

        error = urllib.error.HTTPError("url", 500, "server error", {}, None)
        with patch("plugins.github.urllib.request.urlopen", side_effect=error):
            from plugins.github import fetch_github_readme
            with self.assertRaises(urllib.error.HTTPError):
                fetch_github_readme("vendor/sdk", 5)

        with patch("plugins.github.urllib.request.urlopen", return_value=_Response(b"README")):
            from plugins.github import fetch_github_readme
            self.assertEqual(fetch_github_readme("vendor/sdk", 5), "README")

    def test_github_download_omits_unavailable_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("plugins.github.download_file"), patch(
            "plugins.github.fetch_github_readme", return_value=""
        ):
            result = GitHubPlugin().download(
                self.context("sdk"), {"artifact_num": 4, "name": "vendor/sdk"}, Path(temp_dir)
            )
        self.assertNotIn("README.md", result["files"])

    def test_gitlab_run_uses_html_fallback_when_api_is_blocked(self) -> None:
        html = '<a href="/vendor/sdk">sdk platform</a><a href="/assets/app.js">ignored</a>'
        context = self.context("sdk", verbose=True)
        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=urllib.error.HTTPError("url", 403, "blocked", {}, None)), patch(
            "plugins.gitlab.fetch_text", return_value=html
        ), patch("plugins.gitlab.fetch_gitlab_readme", return_value="sdk readme"):
            result = GitLabPlugin().run(context)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results"][0]["name"], "vendor/sdk")
        self.assertIn("html-fallback", result["results"][0]["signals"])

    def test_gitlab_rate_limit_skips_html_fallback(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=rate_limited), patch(
            "plugins.gitlab.fetch_text"
        ) as fetch_text:
            result = GitLabPlugin().run(self.context("sdk"))
        self.assertEqual(result["status"], "error")
        self.assertIn("rate limited", result["errors"][0])
        fetch_text.assert_not_called()

    def test_gitlab_rate_limit_stops_following_queries(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=rate_limited) as fetch_json:
            result = GitLabPlugin().run(self.context("sdk", "portal"))
        fetch_json.assert_called_once()
        self.assertEqual(len(result["errors"]), 1)

    def test_gitlab_readme_rate_limit_is_reported_and_stops_enrichment(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        payload = [
            {"path_with_namespace": "vendor/one", "description": "sdk", "web_url": "https://gitlab.com/vendor/one"},
            {"path_with_namespace": "vendor/two", "description": "sdk", "web_url": "https://gitlab.com/vendor/two"},
        ]
        with patch("plugins.gitlab.fetch_gitlab_page", return_value={"payload": payload, "headers": {}}), patch(
            "plugins.gitlab.fetch_text", side_effect=rate_limited
        ) as fetch_text:
            result = GitLabPlugin().run(self.context("sdk"))
        self.assertEqual(fetch_text.call_count, 1)
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(any("rate limited" in error for error in result["errors"]))
        self.assertTrue(any("enrichment-skipped:stopped" in item["signals"] for item in result["results"]))

    def test_gitlab_enriches_duplicate_project_once(self) -> None:
        payload = [{"path_with_namespace": "vendor/sdk", "description": "sdk", "web_url": "https://gitlab.com/vendor/sdk"}]
        with patch("plugins.gitlab.fetch_gitlab_page", return_value={"payload": payload, "headers": {}}), patch(
            "plugins.gitlab.fetch_gitlab_readme", return_value="sdk portal"
        ) as readme:
            result = GitLabPlugin().run(self.context("sdk", "portal"))
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(readme.call_count, 1)
        self.assertEqual(result["request_stats"]["enrichments"], 1)

    def test_gitlab_download_writes_archive_and_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("plugins.gitlab.download_file"), patch(
            "plugins.gitlab.fetch_gitlab_readme", return_value="README"
        ):
            result = GitLabPlugin().download(
                self.context("sdk"), {"artifact_num": 3, "name": "vendor/sdk"}, Path(temp_dir)
            )
            self.assertEqual(result["status"], "downloaded")
            self.assertTrue((Path(temp_dir) / "003_vendor-sdk" / "README.md").exists())

    def test_gitlab_readme_tries_default_branches_and_strips_html(self) -> None:
        blocked = urllib.error.HTTPError("url", 404, "missing", {}, None)
        with patch("plugins.gitlab.fetch_text", side_effect=[blocked, '<html><h1>SDK</h1><p>docs</p></html>']):
            from plugins.gitlab import fetch_gitlab_readme
            self.assertEqual(fetch_gitlab_readme("vendor/sdk", 5), "SDK docs")

        with patch("plugins.gitlab.fetch_text", side_effect=[blocked, blocked, blocked, blocked]):
            from plugins.gitlab import fetch_gitlab_readme
            self.assertEqual(fetch_gitlab_readme("vendor/sdk", 5), "")

    def test_dockerhub_rate_limit_is_reported_without_retry(self) -> None:
        rate_limited = urllib.error.HTTPError("url", 429, "rate limit exceeded", {}, None)
        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=rate_limited) as fetch_json:
            result = DockerHubPlugin().run(self.context("sdk", "portal"))
        fetch_json.assert_called_once()
        self.assertEqual(result["status"], "error")
        self.assertIn("rate limited", result["errors"][0])

    def test_gitlab_text_rate_limit_stops_following_queries(self) -> None:
        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=RuntimeError("rate limit exceeded")) as fetch_json:
            result = GitLabPlugin().run(self.context("sdk", "portal"))
        fetch_json.assert_called_once()
        self.assertTrue(any("rate limited" in error for error in result["errors"]))

    def test_gitlab_html_fallback_skips_unmatched_projects_and_records_failure(self) -> None:
        context = self.context("sdk")
        body = '<a href="/vendor/unrelated">other</a>' + ("x" * 1000) + '<a href="/vendor/sdk">sdk</a>'
        with patch("plugins.gitlab.fetch_text", return_value=body), patch(
            "plugins.gitlab.fetch_gitlab_readme", return_value=""
        ):
            result = GitLabPlugin()._scrape_explore_page("https://gitlab.com/explore", context, "sdk")
        self.assertEqual([item["name"] for item in result], ["vendor/sdk"])

        with patch("plugins.gitlab.fetch_gitlab_page", side_effect=urllib.error.HTTPError("url", 403, "blocked", {}, None)), patch(
            "plugins.gitlab.fetch_text", side_effect=RuntimeError("html offline")
        ):
            result = GitLabPlugin().run(context)
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("html fallback failed" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
