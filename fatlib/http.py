from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timezone


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0"
ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_ATTEMPTS = 4
BASE_RETRY_DELAY_SECONDS = 1.0


def _rewrite_resolution_error(url: str, exc: Exception) -> Exception:
    """Return a clearer error for unresolved workshop mirror hostnames."""

    if not isinstance(exc, urllib.error.URLError):
        return exc
    reason = getattr(exc, "reason", None)
    if not isinstance(reason, socket.gaierror):
        return exc

    host = urllib.parse.urlsplit(url).hostname or ""
    if not host.endswith(".nnlabs"):
        return exc

    return RuntimeError(
        f"Could not resolve workshop host '{host}'. "
        "Make sure you are using the workshop DNS setup (or a hosts override) "
        f"before targeting {url!r}."
    )


def build_headers(*, accept: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the shared FAT request headers."""

    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if extra:
        headers.update(extra)
    return headers


def build_request(url: str, *, accept: str | None = None, extra_headers: dict[str, str] | None = None) -> urllib.request.Request:
    """Build one urllib request with the shared FAT headers."""

    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"unsupported URL scheme {scheme!r}: only http(s) URLs are allowed")
    return urllib.request.Request(url, headers=build_headers(accept=accept, extra=extra_headers))


def _parse_retry_after_seconds(raw_value: str | None) -> float | None:
    if not raw_value:
        return None
    stripped = raw_value.strip()
    if not stripped:
        return None
    try:
        return max(0.0, float(int(stripped)))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (target - now).total_seconds())


def _retry_delay_seconds(exc: urllib.error.HTTPError, attempt: int) -> float | None:
    if exc.code != 429:
        return None
    retry_after = _parse_retry_after_seconds(exc.headers.get("Retry-After"))
    if retry_after is not None:
        return retry_after
    return BASE_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))


def is_rate_limited_error(exc: BaseException) -> bool:
    """Return whether an upstream response clearly indicates rate limiting."""

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return True
        if exc.code == 403:
            headers = exc.headers or {}
            retry_after = headers.get("Retry-After")
            remaining = headers.get("X-RateLimit-Remaining")
            return bool(retry_after) or str(remaining).strip() == "0" or "rate" in str(exc).casefold()
    message = str(exc).casefold()
    return "rate limit" in message or "too many requests" in message


def format_rate_limit_error(service: str, exc: BaseException) -> str:
    """Format a provider rate-limit error consistently for persisted reports."""

    code = getattr(exc, "code", None)
    headers = getattr(exc, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    code_text = f"HTTP {code}" if code is not None else "upstream response"
    retry_text = str(retry_after).strip() if retry_after else "unknown"
    return f"{service}: rate limited ({code_text}); retry after {retry_text}"


def _open_with_retries(request: urllib.request.Request, *, timeout: int):
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # Explicit rate limits are terminal. The Retry-After value is
            # reported to the caller; it is not used for a blind retry.
            if is_rate_limited_error(exc):
                raise
            delay = _retry_delay_seconds(exc, attempt)
            if delay is None or attempt >= MAX_ATTEMPTS:
                raise
            time.sleep(delay)
        except Exception as exc:
            last_exc = exc
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable retry state")


def fetch_text(url: str, *, timeout: int, accept: str = "text/plain") -> str:
    """Fetch one text resource using the shared FAT headers."""

    request = build_request(url, accept=accept)
    try:
        with _open_with_retries(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "ignore")
    except Exception as exc:
        raise _rewrite_resolution_error(url, exc) from exc


def fetch_json_with_headers(
    url: str,
    *,
    timeout: int,
    accept: str = "application/json",
) -> tuple[object, dict[str, str]]:
    """Fetch JSON and retain response headers needed for pagination."""

    request = build_request(url, accept=accept)
    try:
        with _open_with_retries(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "ignore")
            headers = {str(name): str(value) for name, value in response.headers.items()}
    except Exception as exc:
        raise _rewrite_resolution_error(url, exc) from exc
    return json.loads(body), headers


def fetch_json_no_redirect(url: str, *, timeout: int) -> object:
    """Fetch JSON without following redirects.

    This is intended for provider-supplied continuation URLs whose destination
    has already been validated by the caller.
    """

    request = build_request(url, accept="application/json")
    opener = urllib.request.build_opener(urllib.request.HTTPHandler(), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))
    except Exception as exc:
        raise _rewrite_resolution_error(url, exc) from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return redirects to urllib as HTTP errors instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_file(url: str, destination: Path, *, timeout: int, accept: str = "*/*") -> None:
    """Download one URL into a local file using the shared FAT headers."""

    request = build_request(url, accept=accept)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _open_with_retries(request, timeout=timeout) as response:
            destination.write_bytes(response.read())
    except Exception as exc:
        raise _rewrite_resolution_error(url, exc) from exc
