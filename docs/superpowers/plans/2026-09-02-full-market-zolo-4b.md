# Full-Market Zolo Task 4B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the bounded Zolo Scrapy request graph and append-only artifact/state handling required by Task 4B.

**Architecture:** Keep the optional Scrapy import boundary and existing pure policy/parser helpers. `FullMarketSpider` owns source/context normalization, JSON-safe crawl state, injected artifact writing, and Zolo search/detail callbacks; each callback records an artifact before yielding follow-up requests.

**Tech Stack:** Python 3.11+, Scrapy, scrapy-playwright, BeautifulSoup, existing `parse_public_html`, `parse_zolo_detail`, `ArtifactWriter`, pytest.

---

### Task 1: Add the Zolo request boundary

**Files:**
- Modify: `src/northshore_watchdog/research/crawling/spider.py`
- Test: `tests/test_research_crawling.py` (existing RED test)

- [x] **Step 1: Run the failing start-request test.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo`.
The current failure is `AttributeError: 'FullMarketSpider' object has no attribute 'start_requests'`.

- [ ] **Step 2: Implement constructor normalization and Zolo start requests.**

Read source fields `name`, `source_registry_id`, `allowed_domains`, and
`entrypoints`; read context fields `run_id`, `observed_at`, and `crawl`, with
`max_search_pages` and `max_detail_requests` bounded to positive integers.
Resolve `artifact_writer`, then `writer`, then
`context["artifact_writer"]`. For each same-domain Zolo entrypoint, yield a
Scrapy request with callback `parse_zolo_search`, `playwright=True`, and
`source_name`, `source_registry_id`, `request_kind="SEARCH"`,
`page_number=1`, `run_id`, and `entrypoint_url` in meta.

- [ ] **Step 3: Run the focused test and confirm it passes.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo`.
The start-request test must pass, with only optional-extra tests skipped.

### Task 2: Implement Zolo search parsing, deduplication, and pagination

**Files:**
- Modify: `src/northshore_watchdog/research/crawling/spider.py`
- Test: `tests/test_research_crawling.py` (existing Zolo tests)

- [ ] **Step 1: Verify the callback tests are RED or skipped for the missing extra.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo` and record whether the callback tests execute or skip because Scrapy is unavailable.

- [ ] **Step 2: Implement the minimal search callback.**

Use `response.body`, canonical response URL, and `page_fingerprint()` to
short-circuit repeated canonical URLs or body digests with a SEARCH artifact
having `coverage_state="REPEATED_PAGE"`. Detect visible challenges before
parsing and write `BLOCKED_POLICY` plus `CHALLENGE_DETECTED`. Otherwise call
`parse_public_html()`, write a SEARCH artifact with rows, card count,
extracted-row count, and DOM-only next URL, then yield unique policy-approved
DETAIL requests and at most one unseen SEARCH next request. Mark
`PARTIAL_CAP_REACHED` when search/detail caps prevent more work and
`NATURAL_COMPLETE` when no valid next URL exists.

- [ ] **Step 3: Run the focused Zolo suite.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo`.
Expected: all runnable Zolo tests pass; optional framework tests remain skipped
only when the crawler extra is absent.

### Task 3: Implement Zolo detail artifacts and failure preservation

**Files:**
- Modify: `src/northshore_watchdog/research/crawling/spider.py`
- Test: `tests/test_research_crawling.py` and installed-extra framework tests

- [ ] **Step 1: Add the failing detail behavior test if the installed extra exposes it.**

Construct a detail response carrying a detail request meta record and assert
that the callback invokes `parse_zolo_detail`, writes `request_kind="DETAIL"`,
and returns no search-row deletion side effect; for parser failure assert the
artifact has an error type and rows remain represented by prior SEARCH
artifacts.

- [ ] **Step 2: Implement success and exception paths.**

Call the existing `parse_zolo_detail()` with response body, URL, and spider
`observed_at`. Write a successful DETAIL artifact with one parsed row. Catch
parser exceptions, write a DETAIL artifact with `parse_status="ERROR"` and a
stable error type/message, and return without mutating search rows.

- [ ] **Step 3: Run focused and full tests.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo`
and then `rtk .venv/bin/python -m pytest -q`. Verify exit code zero and report
the RED/GREEN counts, including any optional-extra skips.

### Task 4: Review and verify the bounded scope

**Files:**
- Inspect: `src/northshore_watchdog/research/crawling/spider.py`

- [ ] **Step 1: Inspect the final diff and scope.**

Confirm only the Zolo spider boundary plus process documentation changed; no
Realtor callback, runner, or CLI implementation was added, and no Git commit
was created.

- [ ] **Step 2: Re-run the exact acceptance commands.**

Run `rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k zolo`
and `rtk .venv/bin/python -m pytest -q`; use their fresh output as the final
verification evidence.
