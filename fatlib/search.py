"""Shared request budgeting and response caching for source searches."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


DEFAULT_MAX_REQUESTS = 50
DEFAULT_MAX_ENRICHMENTS = 10
DEFAULT_REQUEST_DELAY = 0.0
DEFAULT_CACHE_TTL = 3600
DEFAULT_CACHE_MAX_BYTES = 2_000_000


class SearchRequestBudgetExceeded(RuntimeError):
    """Raised when a search would exceed its upstream request budget."""


class SearchCache:
    """Small filesystem cache for successful, read-only search responses."""

    def __init__(
        self,
        directory: Path,
        *,
        ttl: int = DEFAULT_CACHE_TTL,
        max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
    ) -> None:
        self.directory = Path(directory)
        self.ttl = max(0, int(ttl))
        self.max_bytes = max(1, int(max_bytes))

    def key(self, service: str, url: str, headers: dict[str, str] | None = None) -> str:
        material = json.dumps(
            {"service": service, "url": url, "headers": headers or {}},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _path(self, service: str, url: str, headers: dict[str, str] | None = None) -> Path:
        return self.directory / f"{self.key(service, url, headers)}.json"

    def get_text(self, service: str, url: str, headers: dict[str, str] | None = None) -> str | None:
        if self.ttl <= 0:
            return None
        path = self._path(service, url, headers)
        try:
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            body = payload.get("body")
            return body if isinstance(body, str) else None
        except (OSError, ValueError, TypeError):
            return None

    def put_text(self, service: str, url: str, body: str, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps({"body": body}, ensure_ascii=False).encode("utf-8")
        if len(payload) > self.max_bytes:
            return
        path = self._path(service, url, headers)
        temporary: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=self.directory
            )
            temporary = Path(temporary_name)
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def get_json(self, service: str, url: str, headers: dict[str, str] | None = None) -> Any | None:
        body = self.get_text(service, url, headers)
        if body is None:
            return None
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return None

    def put_json(self, service: str, url: str, payload: Any, headers: dict[str, str] | None = None) -> None:
        try:
            body = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError):
            return
        self.put_text(service, url, body, headers)


class SearchPolicy:
    """Run-local request budget, pacing, enrichment limit, and cache policy."""

    def __init__(
        self,
        service: str,
        *,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        max_enrichments: int = DEFAULT_MAX_ENRICHMENTS,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        cache: SearchCache | None = None,
        verbose: bool = False,
    ) -> None:
        self.service = service
        self.max_requests = max(1, int(max_requests))
        self.max_enrichments = max(0, int(max_enrichments))
        self.request_delay = max(0.0, float(request_delay))
        self.cache = cache
        self.verbose = verbose
        self.requests = 0
        self.enrichments = 0
        self.enrichments_skipped = 0
        self.cache_hits = 0
        self.request_budget_exhausted = False
        self._last_request_at: float | None = None

    @classmethod
    def from_context(cls, context: object, service: str) -> "SearchPolicy":
        cache_dir = getattr(context, "cache_dir", None)
        cache_ttl = int(getattr(context, "cache_ttl", 0) or 0)
        cache = SearchCache(Path(cache_dir), ttl=cache_ttl) if cache_dir and cache_ttl > 0 else None
        return cls(
            service,
            max_requests=int(getattr(context, "max_requests", DEFAULT_MAX_REQUESTS) or DEFAULT_MAX_REQUESTS),
            max_enrichments=int(getattr(context, "max_enrichments", DEFAULT_MAX_ENRICHMENTS) or 0),
            request_delay=float(getattr(context, "request_delay", DEFAULT_REQUEST_DELAY) or 0),
            cache=cache,
            verbose=bool(getattr(context, "verbose", False)),
        )

    def allow_request(self) -> bool:
        """Consume one request slot, pacing after the previous request."""

        if self.requests >= self.max_requests:
            self.request_budget_exhausted = True
            return False
        now = time.monotonic()
        if self._last_request_at is not None and self.request_delay:
            remaining = self.request_delay - (now - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
                now = time.monotonic()
        self._last_request_at = now
        self.requests += 1
        return True

    def require_request(self) -> None:
        if not self.allow_request():
            raise SearchRequestBudgetExceeded(
                f"{self.service}: request budget exhausted after {self.requests} request(s)"
            )

    def allow_enrichment(self) -> bool:
        """Reserve one candidate enrichment slot."""

        if self.enrichments >= self.max_enrichments:
            self.enrichments_skipped += 1
            return False
        self.enrichments += 1
        return True

    def fetch_json(
        self,
        url: str,
        *,
        timeout: int,
        fetcher: Callable[[str, int], Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        if self.cache is not None:
            cached = self.cache.get_json(self.service, url, headers)
            if cached is not None:
                self.cache_hits += 1
                return cached
        self.require_request()
        payload = fetcher(url, timeout)
        if self.cache is not None:
            self.cache.put_json(self.service, url, payload, headers)
        return payload

    def fetch_text(
        self,
        url: str,
        *,
        timeout: int,
        fetcher: Callable[[str, int], str],
        headers: dict[str, str] | None = None,
    ) -> str:
        if self.cache is not None:
            cached = self.cache.get_text(self.service, url, headers)
            if cached is not None:
                self.cache_hits += 1
                return cached
        self.require_request()
        body = fetcher(url, timeout)
        if self.cache is not None:
            self.cache.put_text(self.service, url, body, headers)
        return body

    def summary(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "requests": self.requests,
            "max_requests": self.max_requests,
            "enrichments": self.enrichments,
            "max_enrichments": self.max_enrichments,
            "enrichments_skipped": self.enrichments_skipped,
            "cache_hits": self.cache_hits,
            "cache_enabled": self.cache is not None,
            "request_delay": self.request_delay,
            "request_budget_exhausted": self.request_budget_exhausted,
        }
