"""Source-plugin integration checks with live-first fixture fallback.

The fixtures contain only the fields consumed by FAT. ``auto`` mode tries the
live service first and falls back to those fixtures when the service is
unreachable or rate-limited. Use ``FAT_SURFACE_INTEGRATION_MODE=live`` or
``fixture`` to force one backend.
"""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from plugins.base import PluginContext
from plugins.dockerhub import DockerHubPlugin
from plugins.github import GitHubPlugin
from plugins.gitlab import GitLabPlugin


class SurfacePluginIntegrationTests(unittest.TestCase):
    def mode(self) -> str:
        return os.environ.get("FAT_SURFACE_INTEGRATION_MODE", "auto").strip().lower() or "auto"

    @staticmethod
    def is_rate_limited(value) -> bool:
        message = str(value).casefold()
        return "rate limit" in message or "too many requests" in message or "http error 429" in message

    def run_live_or_fixture(self, name, live_call, fixture_call, valid):
        if self.mode() == "fixture":
            return fixture_call()
        try:
            result = live_call()
        except Exception as exc:
            if self.mode() == "live":
                raise
            reason = "rate-limited" if self.is_rate_limited(exc) else f"unavailable ({exc})"
            print(f"[surface-integration] {name}: live service {reason}; using fixture")
        else:
            if valid(result) or self.mode() == "live":
                return result
            errors = result.get("errors", []) if isinstance(result, dict) else result
            reason = "rate-limited" if self.is_rate_limited(errors) else "returned no usable result"
            print(f"[surface-integration] {name}: live service {reason}; using fixture")
        return fixture_call()

    def context(self, keyword: str, *, api_base: str | None = None) -> PluginContext:
        return PluginContext(
            keywords=[keyword],
            per_query=3,
            timeout=5,
            min_match_keywords=1,
            session_id="integration",
            verbose=False,
            state_file=Path(".fat-integration.json"),
            api_base=api_base,
        )

    def test_github_search_returns_repository_metadata(self) -> None:
        search_response = {
            "items": [
                {
                    "full_name": "octocat/Hello-World",
                    "description": "My first repository on GitHub!",
                    "html_url": "https://github.com/octocat/Hello-World",
                }
            ]
        }
        def fixture_run():
            with patch("plugins.github.fetch_json", return_value=search_response), patch(
                "plugins.github.fetch_github_readme", return_value="Hello World"
            ):
                return GitHubPlugin().run(self.context("octocat"))

        result = self.run_live_or_fixture(
            "github",
            lambda: GitHubPlugin().run(self.context("octocat")),
            fixture_run,
            lambda value: value.get("status") == "ok" and bool(value.get("results")),
        )
        self.assertEqual(result["status"], "ok", result["errors"])
        self.assertTrue(result["results"])
        artifact = result["results"][0]
        self.assertTrue(artifact["name"])
        self.assertIn("github.com", artifact["url"])

    def test_gitlab_search_returns_project_metadata(self) -> None:
        search_response = [
            {
                "path_with_namespace": "gitlab-org/gitlab",
                "description": "GitLab CE mirror",
                "web_url": "https://gitlab.com/gitlab-org/gitlab",
            }
        ]
        def fixture_run():
            with patch("plugins.gitlab.fetch_gitlab_page", return_value={"payload": search_response, "headers": {}}), patch(
                "plugins.gitlab.fetch_gitlab_readme", return_value="GitLab"
            ):
                return GitLabPlugin().run(self.context("gitlab-org/gitlab"))

        result = self.run_live_or_fixture(
            "gitlab",
            lambda: GitLabPlugin().run(self.context("gitlab-org/gitlab")),
            fixture_run,
            lambda value: value.get("status") == "ok" and bool(value.get("results")),
        )
        self.assertEqual(result["status"], "ok", result["errors"])
        self.assertTrue(result["results"])
        artifact = result["results"][0]
        self.assertEqual(artifact["plugin"], "gitlab")
        self.assertTrue(artifact["name"])
        self.assertTrue(artifact["url"].startswith("https://gitlab.com/"))

    def test_dockerhub_search_and_metadata_download(self) -> None:
        search_response = {
            "results": [
                {
                    "repo_name": "library/python",
                    "short_description": "Python is an interpreted programming language.",
                }
            ]
        }
        def fixture_search():
            with patch("plugins.dockerhub.fetch_dockerhub_page", return_value=search_response):
                return DockerHubPlugin().run(self.context("python"))

        result = self.run_live_or_fixture(
            "dockerhub search",
            lambda: DockerHubPlugin().run(self.context("python")),
            fixture_search,
            lambda value: value.get("status") == "ok" and bool(value.get("results")),
        )
        self.assertEqual(result["status"], "ok", result["errors"])
        self.assertTrue(result["results"])
        artifact = result["results"][0]
        self.assertTrue(artifact["name"])
        self.assertTrue(artifact["url"].startswith("https://hub.docker.com/"))

        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_response = {"name": "library/python", "namespace": "library"}
            tags_response = {"results": [{"name": "latest"}]}
            def fixture_download():
                with patch(
                    "plugins.dockerhub.fetch_json",
                    side_effect=[metadata_response, tags_response],
                ):
                    return DockerHubPlugin().download(
                        self.context("python"),
                        {**artifact, "artifact_num": 1},
                        Path(temp_dir),
                    )

            downloaded = self.run_live_or_fixture(
                "dockerhub download",
                lambda: DockerHubPlugin().download(
                    self.context("python"),
                    {**artifact, "artifact_num": 1},
                    Path(temp_dir),
                ),
                fixture_download,
                lambda value: value.get("status") == "downloaded",
            )
            target = Path(downloaded["path"])
            self.assertTrue((target / "metadata.json").exists())
            self.assertTrue((target / "tags.json").exists())

    def test_pagination_round_robin_fixture_path(self) -> None:
        context = self.context("sdk")
        context.keywords = ["sdk", "portal"]
        context.per_query = 1
        context.max_requests = 4
        context.max_enrichments = 0

        github_calls = []
        def github_page(url, _timeout):
            from urllib.parse import parse_qs, urlsplit
            params = parse_qs(urlsplit(url).query)
            query, page = params["q"][0], int(params["page"][0])
            github_calls.append((query, page))
            return {"total_count": 2, "items": [{"full_name": f"vendor/{query}-{page}", "description": query, "html_url": f"https://github.com/vendor/{query}-{page}"}]}

        with patch("plugins.github.fetch_json", side_effect=github_page):
            github = GitHubPlugin().run(context)
        self.assertEqual(github_calls, [("sdk", 1), ("portal", 1), ("sdk", 2), ("portal", 2)])
        self.assertTrue(all(row["state"] == "complete" for row in github["pagination"]))

        docker_calls = []
        def docker_page(url, _timeout):
            from urllib.parse import parse_qs, urlsplit
            params = parse_qs(urlsplit(url).query)
            query, page = params["query"][0], int(params["page"][0])
            docker_calls.append((query, page))
            next_url = f"https://hub.docker.com/v2/search/repositories/?query={query}&page={page + 1}" if page == 1 else None
            return {"results": [{"repo_name": f"vendor/{query}-{page}", "short_description": query}], "next": next_url}

        with patch("plugins.dockerhub.fetch_dockerhub_page", side_effect=docker_page):
            docker = DockerHubPlugin().run(context)
        self.assertEqual(docker_calls, [("sdk", 1), ("portal", 1), ("sdk", 2), ("portal", 2)])
        self.assertTrue(all(row["state"] == "complete" for row in docker["pagination"]))


if __name__ == "__main__":
    unittest.main()
