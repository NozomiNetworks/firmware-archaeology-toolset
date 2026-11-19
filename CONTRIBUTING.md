# Contributing

Thank you for contributing to the Firmware Archaeology Toolset (FAT). Keep
changes focused, preserve the reproducible security checks, and include tests
and documentation when behavior changes.

## Pull requests

Pull requests are welcome. CI runs on every PR; the same containerized quality
gate that runs locally must be green before a change is merged. Accepted PRs
are merged here by the maintainer, and signed release commits periodically
mark published states of the project.

## Before you start

FAT has no third-party runtime dependencies. Optional JavaScript rendering uses
the separately locked `requirements-browser.txt` environment. The CI
environment uses Python 3.14.7 in the pinned Debian Bookworm container. Docker
is required to run the same containerized quality gate locally. Live
integration checks also need outbound network access, although deterministic
fixture and Wayback mirror modes are available.

Create a branch from the current main branch and keep generated or local
investigation data out of commits. In particular, do not commit `.fat.json`,
coverage data, test logs, downloaded artifacts, or files under `output/` unless
the change specifically requires a fixture or documentation artifact there.

## Make a change

Start by reading the relevant guide:

- [Plugin Author Guide](plugins/README.md) for top-level plugins.
- [Wayback Parser How-To](plugins/wayback/PARSER_HOWTO.md) for Wayback vendor
  parsers.
- [FAT extension skill](skills/fat-extension/SKILL.md) for the shared extension
  workflow.

This repository contains the generic Wayback implementation and the
`httpbin` test parser. Real vendor scrapers and Wayback parsers belong in the
external plugin directory and must not be added to this tree.

Add or update tests with the implementation. Use the existing result schemas,
HTTP helpers, state handling, and command validation unless the change is
intended to alter those contracts. Keep external-service tests small and
bounded; use local fixtures for deterministic behavior and rate-limit fallback
paths.

## Run the checks

The recommended pre-pull-request command regenerates the committed API
reference, checks whitespace, runs the complete unit, integration, browser,
and documentation checks, enforces the 80% coverage threshold, and verifies
that generated documentation is current:

```bash
./scripts/prepare-pr.sh
```

The script requires Docker. It uses the same pinned Python image and hash-
locked dependencies as CI. It leaves phase logs in `output/test-logs/` when a
check needs investigation. The browser phase is included automatically; run
it separately only when changing rendering support or a parser that declares
browser settings:

```bash
./scripts/run_browser_tests_container.sh
```

That command installs the hash-locked Playwright package, installs its matching
Chromium runtime, and tests a local JavaScript fixture. It does not
contact a vendor website.

For a quick offline unit run, install the locked test tooling and run:

```bash
python3 -m pip install --require-hashes --only-binary=:all: -r requirements-test.txt
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py' -v
python3 -m coverage report --fail-under=80
```

Integration tests default to live services and fall back to local fixtures or
the Wayback mirror when an upstream service is unavailable or rate-limited.
Use these environment variables when you need a deterministic run:

```bash
FAT_SURFACE_INTEGRATION_MODE=fixture \
FAT_WAYBACK_INTEGRATION_MODE=mirror \
  python3 -m unittest discover -s tests -p 'integration_*.py' -v
```

Source searches use per-run request and README-enrichment budgets. The defaults
are 50 requests and 10 enrichments, with no pacing delay and a 3600-second
successful-response cache. Adjust them with the plugin settings when testing a
provider, and use `cache-ttl 0` for a fresh response. Search diagnostics are
persisted in the plugin report and in `output/report.md`. Pagination is
bounded by `max-requests`: page 1 is fetched for each query before later pages
are requested round-robin. Tests for source plugins must mock every page and
assert the final pagination state, including request-budget and rate-limit
partial results, malformed continuation metadata, and repeated continuation
URLs. A provider API failure after results have been collected should preserve
those results and report the run as `partial`.

## Documentation

The API reference under `docs/plugin-api/reference/` is generated and
committed. Do not hand-edit generated HTML. Change the source docstrings or
documentation templates, then run `./scripts/prepare-pr.sh` so the reference
is regenerated with the pinned documentation environment. The documentation
check uses each documented module's `__all__` declaration and verifies that
exported symbols have source docstrings and generated pdoc anchors; public
members deliberately marked private remain outside that contract.

Update `README.md` and the relevant guide when a user-facing command, plugin
contract, workflow, or configuration option changes. Keep examples runnable
and make state transitions explicit where a command changes the active
project or target.

## Dependencies and CI

Test and documentation dependencies are compiled lock files with hashes.
Update the corresponding `.in` file first, regenerate its lock file with the
project's pinned tooling, and keep `--require-hashes` installation working.
Browser support follows the same process with `requirements-browser.in` and
`requirements-browser.txt`. Run `./scripts/install_dependencies.sh --browser`
to install the package and the Chromium runtime selected by that Playwright
release. CI calls the same installer.
Do not remove hashes or weaken the binary-only installation checks to make a
build pass.

GitHub Actions and the CI container image are pinned to immutable references.
When updating them, preserve the version comment alongside the commit SHA or
image digest and run the local quality gate before opening a pull request.

## Pull requests

A pull request should explain the problem, the resulting behavior, and the
validation performed. Keep commits focused and use a clear subject line. Call
out changes to security-sensitive behavior, external requests, state-file
formats, generated documentation, or plugin contracts so reviewers can assess
their impact.

Contributions are accepted under the repository license in [LICENSE](LICENSE).
