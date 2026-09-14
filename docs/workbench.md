# M5 read-only workbench

Start from the repository with `python3 -m evalnoise workbench --root runs`.
Open **http://127.0.0.1:4178**. Stop with Ctrl+C. `--port` changes the port,
not the loopback-only bind address. No Docker daemon or model connection is
required. Installation needs no additional runtime packages or frontend build.

## Implemented local workbench

- Search run names, statuses, contracts, and directory IDs.
- Open a run and filter recorded trials by task, profile, outcome, and text.
- Keep full recorded/pass/other/missing counts separate from the visible filter count.
- Inspect complete trial JSON, agent traces, verifier decisions, resource audits,
  logs, and telemetry metadata through expandable evidence sections.
- Inspect resource profiles and engine provenance without rewriting reports.
- Navigate on desktop or mobile with native links, labels, and disclosure controls.
- Compare two manifests side by side, with unknown fields distinct from equal fields.
- Plot raw Engine memory, cumulative CPU and cumulative throttling samples. Separate
  tool steps retain separate receipt clocks. Missing samples are not zeros or peaks.
- Inspect the full unfiltered task/profile/outcome matrix while filtering trial rows.
- Download the selected trials as JSON, including filter values, full/selected/planned
  counts, and canonical source hashes. Downloads do not modify files on the server.
- Enable five-second live refresh, preserving filters, disclosure state and scroll.
  Manual refresh works independently; an error keeps the previously displayed snapshot.

Live progress uses opt-in polling, not a push stream. Evidence can
change between reads during active runs; there is no snapshot transaction or
cryptographic authenticity guarantee. Counts describe recorded files and do not
replace the stricter analytical validation in `compare` or `block-analyze`.
The library includes immediate child directories with `manifest.json`, not
comparison-only, subscription-check, or recursively nested output directories.

## Boundary

Only allowlisted viewing routes and two built-in static assets are served.
There are no run, delete, cleanup, shell, upload, or credential endpoints.
Host, Origin, and Fetch Metadata checks reject cross-origin browser access;
the listener binds only to 127.0.0.1. CSP forbids external resources, framing,
inline script, forms, and base tags. Same-origin fetch is allowed for refresh;
external connections remain forbidden. All artifact text is HTML-escaped.
Responses are not cached and contain no outgoing third-party references.

Artifact names are single components. Descriptor-relative opens refuse symlinks
and non-regular files. Viewing limits are 1000 root entries, 16 MiB per JSON,
10000 trials and 64 MiB aggregate trial data per run. This is a trusted-local
viewer, not a hardened multi-user hosting service. Local processes/users with
access to the listener can read the selected root's evidence; logs may contain
private content. Never reverse-proxy or port-forward it to an untrusted network.

## Validation and remaining scope

Four automated tests cover real HTTP requests, host/origin rejection, no mutation
routes, traversal/symlink refusal, JSON bounds, escaped payloads, and byte-identical
source files before/after viewing. Browser inspection used the actual 144-trial
block study: outcome filtering exposed exactly 24 `oom_killed` records and opening
a record exposed its `OOMKilled` evidence. Desktop 1440px and mobile 390px were
checked; the mobile evidence page had no horizontal overflow or page errors.

`tests/test_workbench_browser.py` runs a persisted Chromium regression covering
comparison, live evidence changes, preserved filters, filtered downloads, escaping,
plots, desktop/mobile widths, and navigation without stale cross-run filters.
Run with `EVALNOISE_BROWSER_TESTS=1`; Playwright is a test-only dependency, installed
in a separate CI browser job. This is not a formal screen-reader accessibility audit
or cross-browser certification. The M3 live-provider and broader M4 methodological
gates remain open.
