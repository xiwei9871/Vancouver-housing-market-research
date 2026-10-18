# Greater Vancouver Detached House Research System V1

## Phase 1 Playwright acquisition design

**Date:** 2026-09-02  
**Status:** Approved by the project owner in conversation

## Objective

Trial browser-rendered acquisition for the existing public listing sources,
without replacing the current research pipeline and without starting a broad
unattended market crawl. The trial must distinguish three outcomes:

1. a rendered page is accessible and produces normalized listing observations;
2. the page is reachable but its markup cannot be parsed reliably; or
3. the source blocks or limits automated access.

The result must be auditable and must never turn a blocked or incomplete page
into a false full-market scan.

## Decision

Add a direct Playwright source adapter to the existing `SourceAdapter`
interface. Keep `Scrapy` and `scrapy-playwright` out of this phase. Keep
`Crawl4AI` out of the acquisition path.

The existing `PublicFeedSource` remains the owner of URL allowlists, robots
policy, status/challenge detection, parsers, normalized source envelopes, raw
snapshots, and append-only source runs. `PlaywrightPublicSource` reuses those
contracts while replacing the page transport with a browser-rendered response.

The adapter kind is `PLAYWRIGHT_PUBLIC`; the evidence `source_type` remains
`MLS` for REALTOR.ca and `AGGREGATOR` for Zolo. This keeps access mechanism and
evidence classification separate.

## Browser boundary

The runtime uses the standard Playwright Python library and Chromium in
headless mode. A source run creates one isolated browser context with the
configured user agent, opens only configured same-domain URLs, captures
rendered `page.content()`, and closes the context at the end of the run.

The following are deliberately out of scope:

- login, saved cookies, session replay, or private MLS/VOW access;
- stealth patches, fingerprint manipulation, proxy rotation, CAPTCHA solving,
  or challenge bypass;
- unbounded link discovery or uncontrolled pagination;
- treating a public page claim as official title, assessment, zoning, school,
  or sold-price evidence.

`GET` page navigation is the Phase 1 contract. Existing public HTTP POST/API
definitions are not silently converted to browser actions; they remain
available through the existing adapter until a separately authorized source
contract exists.

## Flow

```text
configured PLAYWRIGHT_PUBLIC requests
                  |
                  v
        PlaywrightPublicSource
        - host/scheme allowlist
        - fail-closed robots check
        - bounded page budget and delay
        - browser navigation and rendered HTML
        - status/challenge/redirect checks
        - HTML snapshot + optional screenshot
                  |
                  v
       existing PublicFeedSource parser
                  |
                  v
 SourceRunResult -> existing acquisition merge/title gate/
                    Stage 1/SQLite/history/report pipeline
```

The browser transport is injectable. Tests use deterministic fake responses;
the real smoke test uses Playwright only when the optional dependency and
Chromium executable are installed.

## Evidence and storage

The existing raw snapshot is the rendered HTML body and remains append-only.
Browser responses carry the final URL, status, content type, method, observed
time, retrieval time, source version, SHA-256, and raw path. When configured,
the same page also writes a PNG screenshot beside the HTML snapshot and stores
its path in snapshot metadata. A snapshot is written even for a received
403/429/challenge response.

The existing `source_runs` and `source_snapshots` tables are reused. A small
backward-compatible `screenshot_path` column is added for browser evidence;
older databases are upgraded with the existing column-migration mechanism.

## Phase 1 configuration

Add `config/research-playwright.yaml`, inherited from the current MLS-first
profile but with the public listing definitions changed to `PLAYWRIGHT_PUBLIC`.
The trial profile has:

- one bounded search URL per source for the initial smoke run;
- `max_requests_per_run` and `max_pages_per_request` caps;
- `respect_robots: true` and explicit allowed domains;
- `coverage_complete: false`;
- an explicit raw snapshot directory;
- no credentials and no persisted browser profile;
- `wait_until`, optional `wait_for_selector`, timeout, delay, and screenshot
  settings.

The profile is a browser trial, not a claim of complete Greater Vancouver
coverage. A later full scan can expand configured URLs only after the trial
has passed and the source behavior has been reviewed.

## Parser and pagination policy

The existing conservative HTML parser is reused. A browser-rendered page is
accepted only when it exposes explicit listing-card, JSON-LD, embedded JSON,
or link/text fields already supported by that parser. Missing fields remain
`UNKNOWN`/`None`; no address, price, tenure, lot, or sold history is guessed.

Phase 1 supports a configured next-page selector only with a hard page cap and
page-fingerprint deduplication. If the selector is absent, the adapter captures
the configured URL once. If pagination loops, changes domains, or reaches the
cap, the run records a warning and remains `PARTIAL` with
`coverage_complete: false`.

## Failure semantics

- unavailable/denied robots, disallowed host, unsafe redirect, 401/403/429, or
  challenge page: `BLOCKED_POLICY` when no rows were obtained for that source;
- navigation timeout or Playwright runtime failure: `FAILED`;
- valid rows plus parse/pagination issues: `PARTIAL`;
- valid bounded capture with no parse errors: `SUCCESS`, still with
  `coverage_complete: false`;
- no browser package/executable: explicit setup error, never an HTTP fallback
  hidden behind the browser source.

A single 403 or challenge is a source-access limitation, not by itself a
structural reason to add Scrapy. The source must first demonstrate a scale or
orchestration defect.

## Phase 2 trigger

Only move to `Scrapy + scrapy-playwright` if the controlled Playwright trial
shows a repeatable need for one or more of:

- resumable jobs over many search/detail pages;
- scheduler-level deduplication and retries;
- per-domain concurrency/throttling across a large queue;
- persistent crawl state after interruption;
- browser-page/resource limits that are difficult to manage inside one source
  adapter;
- measurable pipeline throughput or SQLite contention at the intended scan
  size.

If the only observed defect is an external 403, challenge, robots denial, or
terms-of-use restriction, the correct result is `BLOCKED_POLICY` and a source
strategy decision—not a crawler-framework escalation.

## Acceptance criteria

1. Fixture tests verify rendered-response parsing, injected browser transport,
   domain/robots/status/challenge policy, page caps, pagination deduplication,
   and append-only HTML/screenshot evidence.
2. The pipeline can build a `PLAYWRIGHT_PUBLIC` adapter without changing
   existing local, authorized, public HTTP, or official enrichment behavior.
3. The trial profile runs at most the configured bounded page budget and keeps
   `coverage_complete: false`.
4. A real one-page Realtor.ca and one-page Zolo smoke test records listings,
   a parse failure, or an explicit block status with evidence—never fabricated
   success.
5. Existing tests remain green. No Scrapy dependency is introduced in Phase 1.
