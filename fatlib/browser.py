"""Optional Playwright support for pages whose useful HTML is rendered by JavaScript."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .http import USER_AGENT

RENDER_MODES = {"static", "preferred", "required"}
WAIT_UNTIL_VALUES = {"domcontentloaded", "load", "networkidle"}


class BrowserError(RuntimeError):
    """Base error for optional browser rendering failures."""


class BrowserUnavailableError(BrowserError):
    """Playwright or its browser binary is unavailable."""


class BrowserRenderError(BrowserError):
    """A browser could not render the requested page."""


@dataclass(frozen=True)
class BrowserOptions:
    """Validated settings for one browser rendering session."""

    mode: str = "static"
    wait_until: str = "domcontentloaded"
    wait_for: str | None = None
    timeout: int = 90

    def __post_init__(self) -> None:
        if self.mode not in RENDER_MODES:
            raise ValueError(f"unknown browser render mode: {self.mode!r}")
        if self.wait_until not in WAIT_UNTIL_VALUES:
            raise ValueError(f"unknown browser wait strategy: {self.wait_until!r}")
        if self.wait_for is not None and not self.wait_for.strip():
            raise ValueError("browser wait_for selector must be non-empty")
        if int(self.timeout) <= 0:
            raise ValueError("browser timeout must be positive")


def _load_playwright_factory() -> Callable[[], Any]:
    """Import Playwright only when rendering is explicitly requested."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised through the seam below
        raise BrowserUnavailableError(
            "Playwright is not installed. Install the optional browser dependencies "
            "with `./scripts/install_dependencies.sh --browser`."
        ) from exc
    return sync_playwright


class BrowserSession:
    """Own one isolated headless browser session and render several pages."""

    def __init__(
        self,
        options: BrowserOptions,
        *,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.options = options
        self._playwright_factory = playwright_factory
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def __enter__(self) -> "BrowserSession":
        try:
            factory = self._playwright_factory or _load_playwright_factory()
            self._playwright = factory().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=USER_AGENT,
                accept_downloads=False,
            )
            return self
        except BrowserError:
            self.close()
            raise
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise BrowserUnavailableError(
                "Could not start Playwright Chromium. Install the browser binary with "
                "`./scripts/install_dependencies.sh --browser` and retry."
            ) from exc

    def render(self, url: str, *, timeout: int | None = None) -> str:
        """Navigate to *url* and return the browser's serialized HTML."""

        if self._context is None:
            raise BrowserRenderError("browser session is not open")
        page = None
        timeout_ms = int((timeout or self.options.timeout) * 1000)
        try:
            page = self._context.new_page()
            page.goto(url, wait_until=self.options.wait_until, timeout=timeout_ms)
            if self.options.wait_for:
                page.wait_for_selector(self.options.wait_for, timeout=timeout_ms)
            return str(page.content())
        except Exception as exc:  # noqa: BLE001
            raise BrowserRenderError(f"Could not render {url}: {exc}") from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        """Close all browser resources, tolerating partial startup."""

        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, method)()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._browser = None
        self._playwright = None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def render_html(
    url: str,
    *,
    timeout: int = 90,
    wait_until: str = "domcontentloaded",
    wait_for: str | None = None,
) -> str:
    """Render one page using a short-lived browser session."""

    options = BrowserOptions(mode="required", wait_until=wait_until, wait_for=wait_for, timeout=timeout)
    with BrowserSession(options) as session:
        return session.render(url)
