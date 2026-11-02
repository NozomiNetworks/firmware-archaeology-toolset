from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from ..base import (
    PluginSetting,
    PluginContext,
    SurfacePluginBase,
    print_key_value,
    print_ok,
    print_result_table,
    print_section,
    print_step,
    project_downloads_dir,
    project_output_dir,
    safe_name,
)
from .registry import SCRAPER_MAP, resolve_scraper


class ScraperPlugin(SurfacePluginBase):
    """Top-level FAT plugin for website-specific artifact cataloging.

    This plugin reuses the standard FAT project state and command model, but
    delegates all site-specific HTML parsing to implementations of
    `WebsiteScraperBase`.
    """

    name = "scraper"
    help_summary = "Catalog and download artifacts from website-specific marketplaces."
    help_examples = (
        "Examples:\n"
        "  fat.py scraper set parser <external-parser>\n"
        "  fat.py scraper set website https://vendor.example/downloads\n"
        "  fat.py project set output-dir output\n"
        "  fat.py scraper set downloads-dir output/downloads/lab/scraper\n"
        "  fat.py scraper search\n"
        "  fat.py scraper status\n"
        "  fat.py scraper select 1,2\n"
        "  fat.py scraper download selected\n"
    )

    def require_inputs(self, project: dict) -> None:
        return None

    def ensure_state(self, project: dict):
        tool_state = super().ensure_state(project)
        tool_state.setdefault("parser", "")
        tool_state.setdefault("website", "")
        tool_state.setdefault("catalog_path", "")
        tool_state.setdefault("catalog_data", {})
        return tool_state

    def setting_definitions(self, project: dict, tool_state: dict) -> list[PluginSetting]:
        return [
            PluginSetting(
                name="parser",
                state_key="parser",
                label="Parser",
                default_factory=lambda _project, _tool_state: "",
                parse_value=lambda raw, _project, _tool_state: self.parse_parser_value(raw),
                render_value=lambda value: value or "-",
            ),
            PluginSetting(
                name="website",
                state_key="website",
                label="Website",
                default_factory=lambda _project, _tool_state: "",
                parse_value=lambda raw, _project, _tool_state: raw.strip(),
                render_value=lambda value: value or "-",
            ),
            *super().setting_definitions(project, tool_state),
        ]

    def option_rows(self, project_name: str, project: dict, tool_state: dict) -> list[tuple[str, str]]:
        rows = super().option_rows(project_name, project, tool_state)
        return rows + [
            ("Catalog", str(tool_state.get("catalog_path") or "-")),
            ("Available parsers", ", ".join(sorted(SCRAPER_MAP)) or "-"),
        ]

    def parse_parser_value(self, raw_value: str) -> str:
        value = raw_value.strip()
        if value not in SCRAPER_MAP:
            raise SystemExit(f"Unknown scraper parser: {value}")
        return value

    def options(self, project_name: str, project: dict, args) -> int:
        tool_state = self.ensure_state(project)
        print_section("Plugin Options")
        for label, value in self.option_rows(project_name, project, tool_state):
            print_key_value(label, value)
        return 0

    def set(self, project_name: str, project: dict, args) -> int:
        return super().set(project_name, project, args)

    def resolve_target(self, tool_state: dict, context: PluginContext) -> tuple[object, str]:
        parser_name = str(tool_state.get("parser") or "").strip() or None
        configured_website = str(tool_state.get("website") or "").strip()
        if parser_name and not configured_website:
            configured_website = SCRAPER_MAP[parser_name].default_website
        if not configured_website:
            raise SystemExit(
                "No website configured for this scraper project.\n"
                "Set it with `scraper set website <url>` or choose a parser with a default website."
            )
        entry_html = ""
        if parser_name is None:
            request_scraper = next(iter(SCRAPER_MAP.values()), None)
            if request_scraper is None:
                raise SystemExit("No scraper parsers are available.")
            try:
                entry_html = request_scraper.fetch_text(configured_website, context.timeout)
            except HTTPError as exc:
                raise SystemExit(
                    "Failed to fetch the configured website while trying to auto-detect the scraper parser.\n"
                    f"Website: {configured_website}\n"
                    f"HTTP error: {exc.code} {exc.reason}\n"
                    "If you are using the public-surface mirror on port 8031, note that it only serves `/hub` and `/archive`, not the marketplace clone.\n"
                    "Use the actual marketplace mirror URL, for example `http://127.0.0.1:8000/product-category/open-platform/`, or set the parser explicitly with `scraper set parser <name>`."
                ) from exc
            except URLError as exc:
                raise SystemExit(
                    "Failed to fetch the configured website while trying to auto-detect the scraper parser.\n"
                    f"Website: {configured_website}\n"
                    f"Network error: {exc}\n"
                    "Make sure the target website is reachable, or set the parser explicitly with `scraper set parser <name>`."
                ) from exc
        scraper = resolve_scraper(parser_name, configured_website, entry_html)
        return scraper, configured_website

    def search(self, project_name: str, project: dict, args) -> int:
        tool_state = self.ensure_state(project)
        output_dir = project_output_dir(project)
        plugin_output_dir = output_dir / self.name / safe_name(project_name)
        plugin_output_dir.mkdir(parents=True, exist_ok=True)
        selected_ids = set(tool_state.get("selected_artifact_ids", []))
        context = self.build_context(project_name, project, args, tool_state)
        scraper, website = self.resolve_target(tool_state, context)
        self.print_search_header(
            context,
            project_name,
            extra_fields=[("Parser", scraper.name), ("Website", website)],
        )
        if context.verbose:
            print_step("starting website catalog session")
        catalog = scraper.crawl(website, context.timeout, verbose=context.verbose)
        catalog_path = plugin_output_dir / "catalog.json"
        catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")

        results = [scraper.product_artifact(product, website) for product in catalog["applications"]]
        if context.verbose and results:
            print_result_table(self.name, results)
            print("")
        plugin_report = {
            "name": self.name,
            "status": "ok" if results else "error",
            "searches": [{"query": website, "url": website, "mode": "crawl"}],
            "errors": [],
            "results": results,
        }
        self.finalize_search(
            project_name,
            project,
            tool_state,
            context,
            selected_ids,
            plugin_report,
            keywords=[],
            per_query=0,
            min_match_keywords=0,
            extra_updates={
                "parser": scraper.name,
                "website": website,
                "downloads_dir": tool_state.get("downloads_dir", project_downloads_dir(project, self.name)),
                "catalog_path": str(catalog_path),
                "catalog_data": catalog,
            },
            extra_record={
                "parser": scraper.name,
                "website": website,
                "catalog_path": str(catalog_path),
            },
        )
        print_ok(f"Wrote {catalog_path}")
        print_ok(f"Updated state for {self.name}")
        return 0

    def download(self, context: PluginContext, artifact: dict, downloads_dir: Path) -> dict:
        scraper = resolve_scraper(str(artifact.get("scraper_name") or ""), str(artifact.get("website") or ""))
        product_slug = str(artifact["slug"])
        resolved_downloads_dir = downloads_dir.resolve()
        target_dir = downloads_dir / safe_name(product_slug)
        if not target_dir.resolve().is_relative_to(resolved_downloads_dir):
            raise ValueError(f"unsafe product slug: {product_slug}")
        target_dir.mkdir(parents=True, exist_ok=True)
        saved_files: list[str] = []
        for download_url in artifact.get("download_urls", []):
            relative_path = scraper.relative_download_path(product_slug, download_url)
            destination = downloads_dir / relative_path
            if not destination.resolve().is_relative_to(resolved_downloads_dir):
                raise ValueError(f"unsafe download path: {relative_path}")
            scraper.download_file(download_url, destination, context.timeout)
            saved_files.append(str(relative_path))
        (target_dir / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
        return {
            "status": "downloaded",
            "path": str(target_dir),
            "files": saved_files + ["artifact.json"],
        }

    def project_summary(self, project: dict) -> str:
        tool_state = self.ensure_state(project)
        parser_name = tool_state.get("parser") or "-"
        return (
            f"artifacts={len(tool_state.get('artifacts', []))} "
            f"selected={len(tool_state.get('selected_artifact_ids', []))} "
            f"downloads={len(tool_state.get('downloads', []))} "
            f"parser={parser_name}"
        )


PLUGIN_CLASS = ScraperPlugin
