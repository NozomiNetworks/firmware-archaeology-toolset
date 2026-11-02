from __future__ import annotations

import tempfile
import unittest
import time
import os
from pathlib import Path
from unittest.mock import Mock, patch

from fatlib.search import SearchCache, SearchRequestBudgetExceeded, SearchPolicy


class SearchPolicyTests(unittest.TestCase):
    def test_request_budget_and_enrichment_budget_are_accounted(self) -> None:
        policy = SearchPolicy("github", max_requests=1, max_enrichments=1)

        self.assertTrue(policy.allow_request())
        with self.assertRaises(SearchRequestBudgetExceeded):
            policy.require_request()
        self.assertTrue(policy.allow_enrichment())
        self.assertFalse(policy.allow_enrichment())

        stats = policy.summary()
        self.assertEqual(stats["requests"], 1)
        self.assertEqual(stats["enrichments"], 1)
        self.assertEqual(stats["enrichments_skipped"], 1)
        self.assertTrue(stats["request_budget_exhausted"])

    def test_request_delay_is_applied_between_requests(self) -> None:
        policy = SearchPolicy("gitlab", request_delay=2.5)
        with patch("fatlib.search.time.monotonic", side_effect=[10.0, 10.25, 12.5]), patch(
            "fatlib.search.time.sleep"
        ) as sleep:
            self.assertTrue(policy.allow_request())
            self.assertTrue(policy.allow_request())
        sleep.assert_called_once_with(2.25)

    def test_policy_cache_hit_avoids_fetcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = SearchPolicy(
                "github",
                cache=SearchCache(Path(temp_dir), ttl=3600),
            )
            fetcher = Mock(return_value={"items": []})
            url = "https://example.test/search"
            self.assertEqual(
                policy.fetch_json(url, timeout=5, fetcher=fetcher),
                {"items": []},
            )
            self.assertEqual(
                policy.fetch_json(url, timeout=5, fetcher=fetcher),
                {"items": []},
            )
            fetcher.assert_called_once_with(url, 5)
            self.assertEqual(policy.summary()["cache_hits"], 1)

    def test_cache_round_trip_is_atomic_and_corrupt_entries_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir), ttl=3600)
            cache.put_text("github", "https://example.test/search?q=sdk", "payload")
            self.assertEqual(cache.get_text("github", "https://example.test/search?q=sdk"), "payload")

            key = cache.key("github", "https://example.test/search?q=sdk")
            (Path(temp_dir) / f"{key}.json").write_text("not json", encoding="utf-8")
            self.assertIsNone(cache.get_text("github", "https://example.test/search?q=sdk"))

    def test_cache_json_round_trip_and_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir), ttl=3600, max_bytes=64)
            payload = {"results": ["sdk"]}
            cache.put_json("dockerhub", "https://example.test/search", payload)
            self.assertEqual(cache.get_json("dockerhub", "https://example.test/search"), payload)

            cache.put_text("dockerhub", "https://example.test/large", "x" * 1000)
            self.assertIsNone(cache.get_text("dockerhub", "https://example.test/large"))

    def test_expired_and_disabled_cache_entries_are_misses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir), ttl=1)
            cache.put_text("github", "https://example.test/expired", "payload")
            old_time = time.time() - 10
            cache_path = Path(temp_dir) / f"{cache.key('github', 'https://example.test/expired')}.json"
            os.utime(cache_path, (old_time, old_time))
            self.assertIsNone(cache.get_text("github", "https://example.test/expired"))
            disabled = SearchCache(Path(temp_dir), ttl=0)
            self.assertIsNone(disabled.get_text("github", "https://example.test/expired"))

    def test_fetch_failure_does_not_populate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = SearchPolicy("github", cache=SearchCache(Path(temp_dir), ttl=3600))
            with self.assertRaisesRegex(RuntimeError, "offline"):
                policy.fetch_text(
                    "https://example.test/readme",
                    timeout=5,
                    fetcher=lambda _url, _timeout: (_ for _ in ()).throw(RuntimeError("offline")),
                )
            self.assertIsNone(policy.cache.get_text("github", "https://example.test/readme"))

    def test_cache_ignores_unserializable_json_and_policy_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir), ttl=3600)
            cache.put_json("github", "https://example.test/object", object())
            self.assertIsNone(cache.get_json("github", "https://example.test/object"))

    def test_cache_write_failures_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "fatlib.search.os.replace", side_effect=OSError("read-only cache")
        ):
            cache = SearchCache(Path(temp_dir), ttl=3600)
            cache.put_text("github", "https://example.test/search", "payload")
            self.assertIsNone(cache.get_text("github", "https://example.test/search"))


if __name__ == "__main__":
    unittest.main()
