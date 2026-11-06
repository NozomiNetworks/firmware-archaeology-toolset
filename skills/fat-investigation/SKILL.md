---
name: fat-investigation
description: Use when investigating firmware for a known vendor and device model with FAT — locating a specific version, downloading a usable image, or recovering decrypted contents from an encrypted package. Covers vendor surface recon, public technical research, FAT retrieval, scouting, and reverse engineering.
---

# FAT Investigation

Use this skill when the task starts from a vendor and a device model and aims
at a firmware outcome: locating a specific version, downloading a usable
image, or recovering decrypted contents.

## Inputs and the goal

Mandatory inputs:

- vendor
- device model

The goal must be explicit and testable, for example:

- obtain firmware X.Y for this model, unpacked and decrypted
- recover any downloadable image for this model, version secondary
- assess a user-provided firmware file and recover its contents

Any combination of clues is acceptable — a target version, a user-provided
file, an encrypted or a plain package. The skill does not prescribe one.
Before starting, restate the goal as a success criterion ("model M firmware X
downloaded, verified, and its contents recovered") and confirm what evidence
will count as met. If the goal is ambiguous, ask instead of guessing.

## Operating principles

- The flow below has mandatory evidence gates, while the agent may loop back
  between stages as new leads appear. FAT is a toolbox, not a blind pipeline.
- Evidence over state. A command reporting success is not evidence; inspect
  the actual files on disk.
- Start at the vendor's own surface. Visit the vendor site, product page,
  support area, download area, update portal, and any vendor-provided tools
  before broad scouting. This is mandatory even when the user already has a
  candidate firmware file or URL; surrounding pages often reveal version
  history, packaging, update tools, and the parser contract.
- Record every interesting link as a project lead with `project lead add`
  (fixed `--source` and status vocabularies; see `README.md`). Leads persist
  in project state across runs and sessions, so context survives big scouts
  and project switches.
- Promote a lead into FAT only when acting on it — as an archive target, a
  scraper target, or search keywords — and mark it with
  `project lead set <id> status acted`.
- Dead ends are results. Report when a path needs credentials, or a decision 
  from the user instead of grinding on.

## Investigation flow

### 1. Vendor surface

Intent: learn what the vendor itself exposes and how the product is packaged.

- Browse the vendor portal, product and support pages, and download areas for
  the model.
- Capture product page URLs, firmware links and filenames, version patterns,
  changelogs, and any encryption or packaging hints (wrapper names, paired
  tools, README wording).
- Also capture vendor-provided firmware update and configuration tools:
  their installers often embed the encryption keys, header formats, and
  packaging logic the firmware relies on, and their names make strong
  scouting keywords later.
- Exit when the model's public surface is mapped or clearly dead
  (login-walled, empty, discontinued).

### 2. Public web and technical literature

Intent: use ordinary public web research to find firmware, download pages, and
technical knowledge before launching structured FAT scouts.

- Search with the user keywords plus productive dorks: `"vendor model"
  firmware download`, archive extensions, version strings, chipset names.
- Dork for the vendor's own tooling too: `"vendor model" firmware update
  tool`, `configuration utility download`. An installer is a lead like a
  firmware image — sometimes the more interesting one.
- Capture every firmware or download-page link into the candidate list with
  its origin.
- Search and read ordinary public sources deliberately: vendor mirrors,
  independent websites, blogs, conference talks, forums, mailing lists, and
  technical reports. Trace useful claims back to their source and record the
  URL, author or publisher, date when available, and the specific technique,
  filename, tool, key, or version it contributes.
- Treat public writeups as evidence and leads, not as proof that a method works
  for the target. Confirm the claimed tool, key, or format against an artifact
  from this investigation.
- Exit when searches stop producing new distinct links.

### 3. FAT scouting

Intent: use FAT's structured search surfaces after the vendor surface and
ordinary public research have been exhausted or have produced named leads that
need systematic searching. Do not start with broad GitHub, GitLab, Docker Hub,
or archive searches when the vendor and public web have not yet been checked.

- Repositories: look for vendor/model parsers, decryptors, unpackers, and
  firmware references that reveal packaging or provide working tooling.
- Container images: look for images tied to the vendor, model, or tool names;
  treat them as tooling and metadata leads, confirmed against the firmware
  itself.
- Keep structured searches broad enough to discover useful repositories and
  images, but start with a small number of meaningful queries. FAT filters and
  deduplicates metadata locally, bounds README enrichment, and records cache
  hits and request budgets in `.fat.json` and `output/report.md`. Searches
  request page 1 for every keyword before progressing through later pages
  round-robin. `per-query` controls page size; `max-requests` is the total
  request budget. The diagnostics identify complete and partial queries, and
  a run with results plus interrupted queries is marked `partial`. The
  defaults are 50 requests, 10 enrichments, no delay, and a 3600-second cache
  lifetime for each plugin search run.
- Use `cache-ttl 0` when a fresh provider response is needed, or clear the
  plugin's `output/.fat-cache/<plugin>` directory when stale entries must be
  removed.
- If a surface reports an explicit rate limit, stop retrying that path. Record
  the result, reuse cached candidates, and move to the vendor surface, open
  web research, archives, or another FAT surface. Do not immediately rerun
  overlapping queries against the same provider.
- Follow named tools, keys, filenames, and techniques discovered during public
  research into the relevant FAT search surfaces. A writeup about a different
  model of the same vendor is especially useful because packaging and
  encryption schemes are often shared across a product family.
- Archives: for each promising link — discovered or user-provided — check
  archived versions, and sweep the vendor domain with keyword search when the
  exact page is gone or unhelpful.

Loop back to the vendor surface or open web whenever scouting surfaces new
leads such as older filenames, mirror hosts, or tool names.

If a product or archived page is reachable but its useful links are absent
from the raw HTML, treat it as JavaScript-heavy. Prefer a parser with static
HTML first; then use the opt-in Playwright rendering settings documented by
the extension guide. For Wayback, render only the archived replay page and
keep the raw HTML/debug fixture available when extraction still fails.

### 4. Normalize every useful retrieval link into FAT

Intent: keep the investigation reproducible and keep parser state, downloads,
post-processing, and future searches connected.

When a vendor product page, download page, archive capture, or direct firmware
URL becomes useful to the investigation, implement or update the corresponding
vendor parser in the external plugin directory before relying on it.
Use the parser's `post_download(...)` hook for vendor-specific unpacking,
decryption, metadata extraction, or scouting, and use FAT's own scraper or
Wayback workflow to perform the download. Do not fetch the useful artifact by
an unrelated ad hoc command and leave it outside FAT state.

For a direct file URL, a parser is still selected or inferred when available so
that its post-download hook runs. For a product or download page, implement
the page parser and use the resulting FAT artifact selection. Record the link
with `project lead add`, promote it with the relevant FAT command, and mark the
lead `acted` once the FAT workflow has consumed it.

This requirement applies to links that will be retrieved or analyzed. A blog
post, source repository, or other reference used only as research remains a
lead with provenance; it does not need a vendor page parser unless it also
provides a retrievable vendor artifact.

### 5. Reverse engineer every promising artifact

Intent: extract packaging, encryption, and format knowledge from anything that
can explain the target firmware.

Reverse engineer all interesting artifacts found during the investigation,
including:

- encrypted and unencrypted firmware images;
- older bridge-version images;
- vendor update utilities, configuration tools, installers, and support
  bundles;
- vendor desktop or mobile software;
- container images and archives containing vendor tooling;
- sibling-model firmware using the same packaging family.

Start with safe, repeatable inspection: preserve the original file and hash,
identify its type, inspect strings and metadata, list archive members, compare
headers across versions, and locate embedded scripts, keys, constants, or
format markers. Escalate to disassembly or dynamic analysis when static
inspection cannot answer the packaging or decryption question. Tie every
finding to the exact artifact and tool invocation that produced it.

Feed new filenames, constants, tool names, schema markers, and version
boundaries back into the FAT project as leads and search terms. If analysis
reveals a reusable parser or post-download action, implement it in the
vendor plugin and rerun the FAT workflow so the result is persisted.

## Tips and tricks

Patterns that decide real investigations:

- **The vendor's tools leak the logic.** Firmware update and configuration
  utilities often embed the encryption keys, header formats, and packaging
  code the firmware relies on. Download them like artifacts and mine them
  (strings, embedded scripts, distinctive constants); their names are also
  strong repository and image scouting keywords.
- **The bridge version.** Vendors usually
  introduce encryption once, at one firmware version, and keep the schema
  afterwards. If the latest image is encrypted, walk archived versions back
  to the bridge: the last unencrypted release exposes the plaintext layout,
  and the first encrypted release — or its update tool and release notes —
  often documents or implements the new schema.
- **Decryptors attach to schema eras, not to models.** A secondary source —
  an old writeup, a tool for a sibling model — decrypts every image in the
  same era. Locate the bridge version first, then check which side of it the
  target sits on before declaring a decryptor inapplicable.
- **Record resolved cases.** When an investigation turns on one of these
  patterns, note the specifics (vendor, bridge version, tool, key location)
  in the project report so the next investigation starts warmer. 

## The FAT toolbox

| Investigation need | FAT capability |
|---|---|
| Archived copies of a known page or file | `wayback` exact URL mode |
| Archived URLs across a vendor domain | `wayback` domain keyword mode |
| Code that parses, decrypts, or unpacks | `github` and `gitlab` search |
| Tooling images and metadata leads | `dockerhub` search |
| Download pages behind a supported portal layout | `scraper` |
| Candidate links worth remembering | `project lead` |

Command syntax and workflows live in `README.md`; do not restate them here.
Project state (`.fat.json`) persists everything FAT acted on, across runs and
projects.

## When FAT is not enough

- An archived page has no parser, or an encrypted package needs analysis:
  follow `skills/fat-extension/SKILL.md` to add a vendor parser or a
  post-download scout.
- `wayback post` re-runs a scout over already downloaded files without
  re-downloading.
- If a useful vendor page or file has been found outside FAT, stop and
  normalize it through the vendor parser and FAT download flow before
  continuing the investigation.

## Verify and report

Report the goal met only when the evidence supports it:

- the goal is restated and either met or explicitly reported as not met;
- every artifact is verified on disk — file type, size, hash — and tied to
  the URL it came from;
- the path to each artifact is recorded well enough to reproduce: pages,
  searches, and selections that produced it;
- discovered external tooling is marked as a lead or confirmed against the
  actual firmware file;
- public websites, blogs, talks, forums, and reports used as evidence are
  recorded with provenance and their claims are tied to inspected artifacts;
- reverse-engineering findings identify the artifact, preserved hash, tool or
  command used, and the resulting packaging or decryption evidence;
- decryption claims are backed by extracted payloads, not by a matched
  password, a partially opened archive, or a tool's exit code;
- remaining uncertainty is stated clearly.
