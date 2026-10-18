# Phase 1 Playwright public acquisition implementation plan

> **For agentic workers:** implement task-by-task with TDD. Do not add
> Scrapy/scrapy-playwright unless the real Phase 1 smoke test establishes one
> of the structural Phase 2 triggers in the design document.

**Goal:** Add a bounded direct Playwright adapter for rendered public listing
pages and validate it through the existing acquisition, snapshot, SQLite, and
report pipeline.

**Architecture:** `PlaywrightPublicSource` subclasses/reuses the existing
`PublicFeedSource` policy and parser contracts. An injectable browser transport
keeps unit tests deterministic. The runtime transport owns one isolated
Playwright browser context per source run, captures rendered HTML, and can
write a screenshot. `build_source_adapters` recognizes `PLAYWRIGHT_PUBLIC`.

**Tech stack:** Python 3.11+, existing Pydantic/httpx/BeautifulSoup/SQLite
stack, optional `playwright` dependency, Chromium, pytest. No stealth,
proxy-rotation, CAPTCHA bypass, login, or private feed.

## Task 1 — Lock the browser response contract with failing tests

**Files:** create `tests/test_research_playwright_feeds.py`.

- [x] Add tests for an injected rendered HTML response producing a normalized
  listing row and an HTML snapshot.
- [x] Add tests for optional screenshot bytes producing a PNG artifact and
  snapshot metadata.
- [x] Add tests for disabled source, `coverage_complete=False`, request/page
  caps, and GET-only browser requests.
- [x] Add tests for robots denial/unavailability, 403/429, challenge HTML, and
  unsafe redirects.
- [x] Add tests for next-page selector traversal with a hard cap and repeated
  page fingerprint stopping the loop.
- [x] Add a test that `build_source_adapters` constructs the browser adapter.
- [x] Run only the new module and confirm the intended RED state because the
  new adapter and config hook do not exist yet:

```bash
rtk .venv/bin/pytest -q tests/test_research_playwright_feeds.py
```

## Task 2 — Implement the browser adapter and transport

**Files:** create `src/northshore_watchdog/research/browser_feeds.py`; modify
`src/northshore_watchdog/research/public_feeds.py` only where shared snapshot
metadata needs the optional screenshot path.

- [x] Add `BrowserPageResponse`/capture metadata using the existing
  `PublicFeedResponse` shape, with optional screenshot bytes.
- [x] Implement `PlaywrightPublicSource` as a `PublicFeedSource`-compatible
  adapter with injectable page and robots transports.
- [x] Implement the runtime transport with standard Playwright Chromium,
  isolated context, configured user agent/timeouts, rendered `page.content()`,
  and optional `page.screenshot()`.
- [x] Implement browser robots retrieval using the same isolated context and
  preserve the existing fail-closed policy.
- [x] Reject non-GET browser requests, off-domain redirects, challenge pages,
  and requests beyond the configured cap.
- [x] Add selector-based pagination only with URL/domain checks, fingerprint
  deduplication, and `max_pages_per_request`.
- [x] Convert browser navigation/runtime errors into visible `FAILED` results;
  do not silently fall back to httpx.
- [x] Run focused tests until green:

```bash
rtk .venv/bin/pytest -q tests/test_research_playwright_feeds.py
```

## Task 3 — Persist browser evidence and connect the pipeline

**Files:** modify `src/northshore_watchdog/research/sources.py`,
`src/northshore_watchdog/research/storage.py`, and
`src/northshore_watchdog/research/pipeline.py`; extend focused tests.

- [x] Add optional `screenshot_path` to `SourceSnapshot`.
- [x] Add/migrate the SQLite `source_snapshots.screenshot_path` column and
  persist it append-only with each source run.
- [x] Import the browser adapter and construct it for `type:
  PLAYWRIGHT_PUBLIC`, passing trusted registry metadata and resolved raw paths.
- [x] Preserve all existing adapter branches and source ordering.
- [x] Verify a browser source run is persisted with source run ID, rendered
  snapshot URL/status/hash/path, and optional screenshot path.
- [x] Run the focused regression set:

```bash
rtk .venv/bin/pytest -q tests/test_research_playwright_feeds.py tests/test_research_public_feeds.py tests/test_research_storage.py tests/test_research_pipeline.py
```

## Task 4 — Add the controlled Playwright profile

**Files:** create `config/research-playwright.yaml`; modify the source
registry/README only as needed to document the new access method.

- [x] Inherit the MLS-first market ranges and acquisition policy.
- [x] Keep the MLS primary slot disabled and define bounded browser sources for
  Realtor.ca and Zolo with one initial search URL each.
- [x] Set `type: PLAYWRIGHT_PUBLIC`, explicit allowed domains, `respect_robots:
  true`, `coverage_complete: false`, small page/request caps, delay, timeout,
  and raw snapshot directories.
- [x] Set `capture_screenshot` explicitly for the smoke test.
- [x] Keep official enrichment disabled in this acquisition-only trial unless
  the inherited profile requires a clearly disabled provider.
- [x] Add a config assertion test and run YAML/config loading tests.

## Task 5 — Install optional runtime and run the real smoke test

**Environment and artifacts:** optional virtualenv install; raw browser
snapshots under the configured `data/research/raw/browser-feeds` directory;
report under a new browser-trial report path.

- [x] Install the pinned optional Playwright package in the project virtualenv
  and install only Chromium using the official Playwright installer.
- [x] Run one bounded source request for Realtor.ca and one for Zolo through the
  new profile. Do not expand to full market acquisition yet.
- [x] Capture stdout/status, source runs, listing count, blocked/challenge
  reasons, snapshots, screenshots, and parser warnings.
- [x] If a source is blocked, preserve the response and report
  `BLOCKED_POLICY`; do not retry with bypass techniques.
- [x] If the browser runtime cannot be installed or launched, report the exact
  setup blocker and keep the code/tests valid.

## Task 6 — Full verification and Phase 1 acceptance report

- [x] Run all tests and compile checks:

```bash
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m compileall -q src tests
```

- [x] Run a dry-run with the new profile to verify no accidental broad crawl.
- [x] Write a concise acceptance report documenting implementation files,
  source results, evidence paths, data limitations, and whether the result is
  `PASS`, `PARTIAL`, or `BLOCKED`.
- [x] Explicitly assess whether any structural Phase 2 trigger occurred. Do
  not add Scrapy in this plan unless the evidence meets the design criteria.

## Completion boundary

Stop after the bounded Playwright trial and acceptance report. Do not claim a
complete MLS or Greater Vancouver market scan. Do not implement Scrapy,
scrapy-playwright, comparable expansion, or unattended full-market crawling in
Phase 1.
