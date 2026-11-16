FAT is a stateful, project-oriented tool for artifact hunting: find likely
firmware images, SDKs, container images, and other downloadable artifacts
across public sources, triage them, and retrieve them, including archived
copies from the Wayback Machine.

Plugins share one command surface (`status`, `search`, `select`, `download`,
`set`, `options`) and persist all state in a single `.fat.json` file, so
discovery, triage, and retrieval stay in one workflow.

## Plugin API reference

This reference documents the three FAT extension surfaces:

- [plugins.base](plugins/base.html) — the core plugin framework:
  `PluginContext` and `SurfacePluginBase`, the standard stateful workflow
  shared by the source plugins (`dockerhub`, `github`, `gitlab`, `scraper`,
  `wayback`).
- [plugins.scraper.base](plugins/scraper/base.html) — `WebsiteScraperBase`,
  the base class implemented by website-specific scraper parsers.
- [plugins.wayback.parsers.api](plugins/wayback/parsers/api.html) — the
  Wayback parser contract: `extract_links`, the optional `update_firmware_link`
  and `post_download` hooks, and the shared record types used by parsers.

Use the search box above to search the reference.

## Guides

- [Plugin author guide](https://github.com/NozomiNetworks/firmware-archaeology-toolset/blob/main/plugins/README.md)
- [Wayback parser guide](https://github.com/NozomiNetworks/firmware-archaeology-toolset/blob/main/plugins/wayback/PARSER_HOWTO.md)

## Rebuilding this reference

```bash
python3 -m pip install -r requirements-docs.txt
./scripts/build_plugin_api_docs.sh
```

The landing page content lives in `docs/plugin-api/templates/landing.md`; the
page layout is customized through `docs/plugin-api/templates/index.html.jinja2`.
