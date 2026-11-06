# Plugin API Docs

This directory is the landing page for plugin developer documentation.

Use two layers together:

- The human guide: `plugins/README.md`
- The Wayback parser guide: `plugins/wayback/PARSER_HOWTO.md`
- The generated API reference: `docs/plugin-api/reference/`

## Main page

The generated `reference/index.html` is a main page that describes the project
and links to the individual plugin API pages. Its content is maintained in
`templates/landing.md` and rendered through a pdoc template override in
`templates/index.html.jinja2`, so editing the landing page only requires
editing that markdown file. The template renders the FAT logo from
`templates/fat-logo.txt` and removes pdoc's default logo from the generated
pages.

## Build

Install the optional docs dependency:

```bash
python3 -m pip install -r requirements-docs.txt
```

Or build without installing anything into the host Python:

```bash
uv run --with pdoc ./scripts/build_plugin_api_docs.sh
```

Generate the API reference:

```bash
./scripts/build_plugin_api_docs.sh
```

The generated pages follow the browser or operating system color preference; dark mode is enabled automatically when the user prefers it.

The generated reference is checked against the source API contract with:

```bash
python3 scripts/verify_plugin_api_docs.py
```

By default this writes HTML files to:

```text
docs/plugin-api/reference/
```

You can also provide a custom output directory:

```bash
./scripts/build_plugin_api_docs.sh /tmp/fat-plugin-api
```

## What Gets Documented

The generated reference is intentionally code-first. The main extension points are:

- `plugins.base.PluginContext`
- `plugins.base.SurfacePluginBase`
- `plugins.scraper.base.WebsiteScraperBase`
- `plugins.wayback.parsers.api.ProjectRecord`
- `plugins.wayback.parsers.api.SnapshotRecord`
- `plugins.wayback.parsers.api.Capture`
- `plugins.wayback.parsers.api.ArchiveClient`
- `plugins.wayback.parsers.api.extract_links`
- `plugins.wayback.parsers.api.update_firmware_link`
- `plugins.wayback.parsers.api.post_download`

The documentation check uses each module's `__all__` declaration as the public
API contract. It verifies that every exported symbol has a docstring and a
generated pdoc anchor. It also checks public methods and fields on exported
classes, except members explicitly hidden with `__pdoc__ = False`.

This is intentionally narrower than the full plugin tree. The goal is to show:

- the hooks plugin authors are expected to implement or override
- the shared helper functions that are actually useful when writing plugins

Use the handwritten guides for:

- file/layout conventions
- plugin author checklists
- Wayback parser naming rules and testing flow
- output and state layout conventions

Internal command plumbing and plugin-specific implementation modules stay in the
source tree and the handwritten author guide, but are omitted from the
generated reference to reduce noise.
