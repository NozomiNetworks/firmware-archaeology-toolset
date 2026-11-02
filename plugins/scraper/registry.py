from __future__ import annotations

from pathlib import Path

from fatlib.discovery import discover_external_modules, discover_modules, instantiate_subclass
from .base import WebsiteScraperBase


BUILTIN_SCRAPERS = discover_modules(
    f"{__package__}.websites",
    Path(__file__).resolve().parent / "websites",
    instantiate=lambda module: instantiate_subclass(module, "SCRAPER_CLASS", WebsiteScraperBase),
)
SCRAPERS = list(BUILTIN_SCRAPERS)
SCRAPER_MAP = {scraper.name: scraper for scraper in SCRAPERS}


def _validate_render_settings(scraper: WebsiteScraperBase) -> None:
    try:
        scraper.browser_options(90)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"Scraper {scraper.name or scraper.__class__.__name__} has invalid browser rendering settings: {exc}"
        ) from exc


for _scraper in SCRAPERS:
    _validate_render_settings(_scraper)


def configure_external_scrapers(plugin_dir: Path) -> None:
    """Add scraper implementations from the configured external root."""

    external: list[WebsiteScraperBase] = []
    for module in discover_external_modules(plugin_dir, "scraper/websites", "scraper_websites"):
        scraper = instantiate_subclass(module, "SCRAPER_CLASS", WebsiteScraperBase)
        if scraper is None:
            raise SystemExit(
                f"External scraper {module.__file__} must export SCRAPER_CLASS "
                "as a WebsiteScraperBase subclass."
            )
        external.append(scraper)

    names = [scraper.name for scraper in BUILTIN_SCRAPERS]
    for scraper in external:
        if not scraper.name:
            raise SystemExit(f"External scraper {scraper.__module__} has an empty name")
        _validate_render_settings(scraper)
        if scraper.name in names:
            raise SystemExit(f"Duplicate scraper parser name: {scraper.name}")
        names.append(scraper.name)

    SCRAPERS.clear()
    SCRAPERS.extend(BUILTIN_SCRAPERS)
    SCRAPERS.extend(sorted(external, key=lambda item: item.name))
    SCRAPER_MAP.clear()
    SCRAPER_MAP.update({scraper.name: scraper for scraper in SCRAPERS})


def resolve_scraper(scraper_name: str | None, website_url: str, entry_html: str = "") -> WebsiteScraperBase:
    if scraper_name:
        try:
            return SCRAPER_MAP[scraper_name]
        except KeyError as exc:
            raise SystemExit(f"Unknown scraper parser: {scraper_name}") from exc
    for scraper in SCRAPERS:
        if scraper.can_handle(website_url, entry_html):
            return scraper
    available = ", ".join(sorted(SCRAPER_MAP)) or "-"
    raise SystemExit(
        "Could not infer scraper parser for the target website.\n"
        f"Set it explicitly with `scraper set parser <name>`.\n"
        f"Available parsers: {available}"
    )
