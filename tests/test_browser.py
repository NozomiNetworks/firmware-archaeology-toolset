from __future__ import annotations

import unittest
from unittest.mock import patch

from fatlib.browser import (
    BrowserOptions,
    BrowserRenderError,
    BrowserSession,
    BrowserUnavailableError,
    render_html,
)


class FakePage:
    def __init__(self, html: str = "<html></html>") -> None:
        self.html = html
        self.goto_calls: list[tuple[str, str, int]] = []
        self.wait_calls: list[tuple[str, int]] = []
        self.closed = False

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append((url, wait_until, timeout))

    def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.wait_calls.append((selector, timeout))

    def content(self) -> str:
        return self.html

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage("<html><body><a class='download'>artifact</a></body></html>")
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeContext()
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self.context

    def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = self
        self.browser = FakeBrowser()
        self.stopped = False

    def start(self):
        return self

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser

    def stop(self) -> None:
        self.stopped = True


class BrowserTests(unittest.TestCase):
    def test_render_waits_for_selector_and_closes_page_and_session(self) -> None:
        playwright = FakePlaywright()
        options = BrowserOptions(mode="required", wait_until="load", wait_for=".download", timeout=7)

        with BrowserSession(options, playwright_factory=lambda: playwright) as session:
            self.assertIn("download", session.render("https://example.test", timeout=3))

        page = playwright.browser.context.pages[0]
        self.assertEqual(page.goto_calls, [("https://example.test", "load", 3000)])
        self.assertEqual(page.wait_calls, [(".download", 3000)])
        self.assertTrue(page.closed)
        self.assertTrue(playwright.browser.context.closed)
        self.assertTrue(playwright.browser.closed)
        self.assertTrue(playwright.stopped)

    def test_render_html_uses_required_browser_mode(self) -> None:
        playwright = FakePlaywright()
        with patch("fatlib.browser.BrowserSession") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.render.return_value = "<html>rendered</html>"
            self.assertEqual(render_html("https://example.test", timeout=4), "<html>rendered</html>")
            options = session_class.call_args.args[0]
            self.assertEqual(options.mode, "required")
            session.render.assert_called_once_with("https://example.test")

    def test_missing_playwright_has_actionable_error(self) -> None:
        options = BrowserOptions(mode="required")
        with patch(
            "fatlib.browser._load_playwright_factory",
            side_effect=BrowserUnavailableError("install Playwright"),
        ):
            with self.assertRaisesRegex(BrowserUnavailableError, "install Playwright"):
                with BrowserSession(options):
                    pass

    def test_render_failures_are_wrapped_and_resources_are_closed(self) -> None:
        playwright = FakePlaywright()
        page = playwright.browser.context.new_page()

        def fail(*args, **kwargs):
            raise RuntimeError("selector timeout")

        page.goto = fail
        playwright.browser.context.pages.clear()
        playwright.browser.context.new_page = lambda: page
        with self.assertRaisesRegex(BrowserRenderError, "selector timeout"):
            with BrowserSession(BrowserOptions(mode="required"), playwright_factory=lambda: playwright) as session:
                session.render("https://example.test")
        self.assertTrue(page.closed)
        self.assertTrue(playwright.stopped)

    def test_options_reject_invalid_values(self) -> None:
        for kwargs in (
            {"mode": "sometimes"},
            {"wait_until": "soon"},
            {"wait_for": " "},
            {"timeout": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BrowserOptions(**kwargs)


if __name__ == "__main__":
    unittest.main()
