# Firmware Archaeology Toolset

[![CI](https://github.com/NozomiNetworks/firmware-archaeology-toolset/actions/workflows/tests.yml/badge.svg)](https://github.com/NozomiNetworks/firmware-archaeology-toolset/actions/workflows/tests.yml)

FAT is a stateful, project-oriented tool for artifact hunting: find likely
firmware images, SDKs, container images, and other downloadable artifacts across
public sources, triage them, and retrieve them, including archived copies from
the Wayback Machine.

Discovery and retrieval stay in one place. Projects, keywords, findings,
selections, and download history persist in a single `.fat.json` state file, so
you can run one tool at a time and pick up where you left off.

![FAT command-line help](docs/images/fat.png)

## Getting started

The normal FAT runtime has no third-party dependencies. JavaScript-heavy scraper
or Wayback parsers can opt into the Playwright environment described in the
plugin guides.

```bash
git clone https://github.com/NozomiNetworks/firmware-archaeology-toolset.git
cd firmware-archaeology-toolset
python3 fat.py --help
```

Create a project, set keywords, then run any plugin:

```bash
python3 fat.py project device-research --keywords "vendor sdk,vendor platform"
python3 fat.py github search
python3 fat.py github status
python3 fat.py github select 1,3
python3 fat.py github download selected
python3 fat.py report
```
Every plugin shares the same command surface: `status`, `options`, `search`,
`select`, `clear-selection`, `download`, and `set`. 

The `wayback` plugin adds `post` to run the parser post
hook and `clear` to wipe snapshots, selections, history, and downloaded
files. 

All commands accept an optional project name prefix to run commands temporarily
into a specific project, different than the selected one.

```bash
python3 fat.py device-research github status
python3 fat.py device-research report
```

This repository contains the generic Wayback engine
and the `httpbin` test parser. External vendor plugins are 
loaded from `~/.fat/plugins/` by default. Configure
another directory in `~/.fat/config.json`:

```json
{"plugin_dir": "/opt/fat/plugins"}
```

Outputs land in `output/report.md` (human-readable report) and
`output/downloads/` (artifacts).

## Leads

Record investigation leads in project state so they
survive across runs and agent sessions:

```bash
python3 fat.py project lead add https://vendor.example/support/model \
  --source portal --title "support page" --note "lists fw 3.2.1"
python3 fat.py project lead list
python3 fat.py project lead set 1 status acted
python3 fat.py project lead rm 2
```

The `status` field can assume the following values:

- `new`: recorded, not examined
- `checked`: examined, still pending
- `acted`: used as input to a FAT action
- `dead`: ruled out

The `source` field instead is among:
- `portal` for vendor sites
- `web` for searches or dorks
- `writeup` for blogs, talks and forums
- `repo` for code repositories
- `image` for container images 
- `archive` for archived snaphots 
- `user` for other, user-provided sources

Duplicate URLs are rejected.

## Basic workflows

Search a code forge or registry (same flow for `github`, `gitlab`,
`dockerhub`):

```bash
python3 fat.py github set per-query 50
python3 fat.py github set min-match-keywords 2
python3 fat.py github set max-requests 25
python3 fat.py github set max-enrichments 8
python3 fat.py github set cache-ttl 3600
python3 fat.py github search
```

Searches are paginated and budgeted. FAT walks each provider's result pages
round-robin within a per-run request budget, and reports a clearly marked
partial result when the budget ends a query early, so that a big search degrades
gracefully instead of failing. 

Transient errors retry with exponential backoff (honoring Retry-After, up to 4
attempts), hard rate limits stop the affected path cleanly, and every page
fetched, failure, and partial query is recorded in .fat.json and
output/report.md.

Scrape a vendor download site after building the vendor parser:

```bash
python3 fat.py scraper set parser <external-parser>
python3 fat.py scraper set website https://vendor.example/downloads
python3 fat.py scraper search
python3 fat.py scraper select 1,2
python3 fat.py scraper download selected
```

Enumerate and retrieve archived artifacts with the Wayback plugin:

```bash
python3 fat.py wayback set parser <external-parser>
python3 fat.py wayback set target-url https://vendor.example/download
python3 fat.py wayback search snapshot
python3 fat.py wayback select 1,2
python3 fat.py wayback search firmware
python3 fat.py wayback select 2:1,2:2
python3 fat.py wayback download selected
```

## Extending and contributing

- [Contributing guide](CONTRIBUTING.md)
- [Plugin author guide](plugins/README.md)
- [Wayback parser guide](plugins/wayback/PARSER_HOWTO.md)
- [Plugin API reference](docs/plugin-api/README.md)
- Agent playbooks: [investigation](skills/fat-investigation/SKILL.md) and [extension guide](skills/fat-extension/SKILL.md)

Drop a top-level plugin module or package into `plugins/`, expose
`PLUGIN_CLASS`, and implement the expected methods. Plugins are discovered
automatically at runtime.

Vendor scraper and Wayback parser modules are fetched from the configured
external plugin directory. See the [plugin author guide](plugins/README.md) for
the contracts.

## Testing

The regular suite runs offline. The source-plugin integration checks try the
live services first and fall back to small API-shaped fixtures when an upstream
service is unavailable or rate-limited. 

The Wayback integration suite exercises the live archive first and automatically
falls back to a recorded local mirror (`tests/wayback_mirror.py`) when
web.archive.org is unreachable or throttling; the backend used per test is
printed in the run summary. Force a Wayback backend with

`FAT_WAYBACK_INTEGRATION_MODE=live|mirror` (default `auto`)

For source-plugin integration checks, use
`FAT_SURFACE_INTEGRATION_MODE=live|fixture` (default `auto`) to force the live
service or deterministic fixture backend. To make the complete integration run
deterministic, select both fixture backends:

```bash
FAT_SURFACE_INTEGRATION_MODE=fixture \
FAT_WAYBACK_INTEGRATION_MODE=mirror \
  python3 -m unittest discover -s tests -p 'integration_*.py' -v
```

```bash
python3 -m pip install --require-hashes --only-binary=:all: -r requirements-test.txt
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py' -v
python3 -m coverage run --branch -a -m unittest discover -s tests -p 'integration_*.py' -v
python3 -m coverage report --precision=2 --fail-under=80
```

The browser-rendering integration check is separate because it installs the
hash-locked Playwright package and its matching Chromium runtime:

```bash
./scripts/run_browser_tests_container.sh
```

To install all locked development dependencies, including the Playwright browser
runtime, run:

```bash
./scripts/install_dependencies.sh
```

Run it from a virtual environment when the host Python is externally managed:

```bash
python3 -m venv .venv
. .venv/bin/activate
./scripts/install_dependencies.sh
```

Use `./scripts/install_dependencies.sh --browser` for browser support only.
The Chromium version is selected by the pinned Playwright release.

Before opening a pull request, run the local quality gate. It regenerates the
committed API reference with the pinned documentation environment, runs the
containerized checks, and reports whether the branch is ready:

```bash
./scripts/prepare-pr.sh
```

CI runs the unit, integration, browser integration, and docs jobs on every
pull request.


## Repository map

- `fat.py`: CLI entry point
- `fatlib/`: shared project, HTTP, discovery, and report helpers
- `plugins/`: plugin base and implementations (`dockerhub`, `github`, `gitlab`, `scraper`, `wayback`)
- `docs/`: generated plugin API reference
- `skills/`: agent playbooks for operating and extending FAT
- `tests/`: offline unit tests and live integration tests
- `scripts/`: docs build and container test scripts
