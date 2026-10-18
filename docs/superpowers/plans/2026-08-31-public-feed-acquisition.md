# Public Feed Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, read-only public HTTP/JSON acquisition adapter for REALTOR.ca and Zolo and connect it to the existing MLS-first Phase 1 research pipeline.

**Architecture:** Add a focused `public_feeds.py` module for HTTP policy and source-specific parsing. Extend `SourceRunResult` and SQLite with append-only raw snapshot metadata, then let the existing pipeline instantiate `PUBLIC_HTTP` adapters and merge their rows through the unchanged identity, provenance, conflict, Stage 1, and report contracts.

**Tech Stack:** Python 3.11+, Pydantic 2, httpx, BeautifulSoup, urllib.robotparser-compatible robots parsing, SQLite, PyYAML, pytest. No browser automation, proxy, challenge solver, or new crawler dependency.

---

## Scope and file map

Create:

- `src/northshore_watchdog/research/public_feeds.py` — public HTTP response model, robots/request policy, raw snapshot writer, REALTOR.ca JSON parser, and conservative HTML/embedded-JSON parsers.
- `tests/test_research_public_feeds.py` — red/green tests for parsing, limits, robots, challenges, and raw snapshots.

Modify:

- `src/northshore_watchdog/research/sources.py` — carry `SourceSnapshot` metadata in source runs.
- `src/northshore_watchdog/research/pipeline.py` — build `PUBLIC_HTTP` adapters and preserve registry metadata.
- `src/northshore_watchdog/research/storage.py` — create and append `source_snapshots`; persist snapshot metadata with source runs.
- `config/research-mls-first.yaml` — use bounded live public-feed definitions before optional local snapshot fallback.
- `data/research/sources/mls_first_source_registry.yaml` — register public HTTP source roles and domains.
- `reports/latest-mls-first.md` — regenerate from the live/blocked run.
- `README.md`, `data/research/README.md` — document live public-feed setup, safety, and limitations.

Do not modify comparable, redevelopment, school, official-record, or scoring formulas in this Phase 2 increment.

## Task 1: Lock the public source contract with failing tests

**Files:** Create `tests/test_research_public_feeds.py`.

- [x] **Step 1: Write the parser and policy tests.** Add tests that instantiate a wished-for `PublicFeedSource` with injected responses and assert:

```python
def test_public_json_feed_maps_realtor_results_and_writes_snapshot(tmp_path):
    response = PublicFeedResponse(
        url="https://api2.realtor.ca/Listing.svc/PropertySearch_Post",
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps({"Paging": {"TotalRecords": 1}, "Results": [{
            "Id": 3001,
            "MlsNumber": "R3000001",
            "Property": {"PriceUnformattedValue": 1_500_000,
                          "Address": {"AddressText": "100 TEST STREET|Vancouver, BC"},
                          "Type": "Single Family",
                          "OwnershipType": "Freehold"},
            "Building": {"Bedrooms": "3", "BathroomTotal": "2",
                          "SizeInterior": "1800 sqft"},
            "RelativeURLEn": "/real-estate/3001/100-test-street-vancouver",
        }]}).encode(),
    )
    source = PublicFeedSource(..., transport=lambda request: response,
                              raw_snapshot_dir=tmp_path)

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["mls_number"] == "R3000001"
    assert result.records[0]["asking"] == 1_500_000
    assert result.snapshots[0].sha256
    assert Path(result.snapshots[0].raw_path).read_bytes()
```

Also add tests for REALTOR/Zolo HTML cards, malformed rows, missing robots, robots denial, 403/429, challenge HTML, domain/request limits, unsafe redirects, and `coverage_complete=False`.

- [x] **Step 2: Run only the new tests and verify the expected RED state.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_public_feeds.py
```

Expected: collection/import failure because `public_feeds.py`, `PublicFeedResponse`, and `PublicFeedSource` are not implemented.

## Task 2: Implement the public feed adapter and parsers

**Files:** Create `src/northshore_watchdog/research/public_feeds.py`.

- [x] **Step 1: Add the minimal response/snapshot contracts.** Implement:

```python
@dataclass(frozen=True)
class PublicFeedResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    method: str = "GET"

class PublicFeedSource(_BaseSource):
    def collect(self, observed_at: datetime) -> SourceRunResult: ...
```

`collect` must call only the injected transport in tests or the standard `httpx.Client` in runtime, never a browser/stealth/proxy client. Keep `coverage_complete=False` unless explicitly supplied otherwise.

- [x] **Step 2: Implement fail-closed robots, domain, status, and rate policy.** Before each target request, validate URL scheme/host against `allowed_domains`, fetch/cache `/robots.txt`, and require `can_fetch(user_agent, target_url)`. Stop the affected host on unavailable robots, 401/403/429, or challenge markers; reject off-domain redirects and honor the request cap. Use `min_delay_seconds` and `max_requests_per_run`; tests inject a no-op sleeper.

- [x] **Step 3: Implement parser functions.** Map only explicit fields:

```python
def parse_realtor_ca_json(content: bytes, requested_url: str, observed_at: datetime) -> list[dict[str, Any]]: ...
def parse_public_html(content: bytes, requested_url: str, source_name: str, observed_at: datetime) -> list[dict[str, Any]]: ...
```

Support nested REALTOR.ca `Results`, explicit `data-*`/listing-card fields, JSON-LD or embedded JSON objects with an MLS number, and conservative link/text extraction. Preserve `source_url`, `source_type`, source registry, observed timestamp, and `source_version` via the adapter envelope.

- [x] **Step 4: Run focused tests and refactor only after green.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_public_feeds.py
```

Expected: all new adapter/parser/policy tests pass.

## Task 3: Add append-only snapshots and pipeline integration

**Files:** Modify `sources.py`, `pipeline.py`, `storage.py`; add integration tests to `tests/test_research_public_feeds.py`.

- [x] **Step 1: Add a failing storage integration assertion.** Assert that `ResearchStorage.save_source_run(result)` creates one `source_snapshots` row with URL, status, hash, raw path, and source version; a second save creates a second row and leaves the first row unchanged.

- [x] **Step 2: Extend `SourceRunResult` and SQLite minimally.** Add `snapshots` to the result, create the append-only table in `ResearchStorage.init`, and insert snapshot metadata from `save_source_run` in the same transaction. Do not store the body in the current projection; the immutable raw file path/hash is the body evidence.

- [x] **Step 3: Add `PUBLIC_HTTP` construction.** In `build_source_adapters`, pass a `PublicFeedSource` the trusted registry definition, URL requests, raw directory, and optional injected transport. Preserve the configured priority order and source role.

- [x] **Step 4: Run focused storage/pipeline tests.**

```bash
rtk .venv/bin/pytest -q tests/test_research_public_feeds.py tests/test_research_storage.py tests/test_research_pipeline.py
```

Expected: all pass, including existing append-only and MLS-first identity tests.

## Task 4: Enable the bounded public profile and verify Phase 1 + Phase 2

**Files:** Modify `config/research-mls-first.yaml`, `data/research/sources/mls_first_source_registry.yaml`, `README.md`, `data/research/README.md`; generate report artifacts.

- [x] **Step 1: Add a failing profile assertion.** Assert the profile contains disabled `MLS_PRIMARY`, then `REALTOR_CA_PUBLIC` and `ZOLO_PUBLIC` `PUBLIC_HTTP` definitions with `coverage_complete: false`, request caps, robots policy, and raw snapshot directory.

- [x] **Step 2: Configure conservative regional requests.** Use a small explicit URL list for Greater Vancouver detached/single-family searches and a public REALTOR.ca JSON request definition. Do not enable a private API, add credentials, or infer official fields. Keep local JSON snapshots as an explicitly named deterministic fallback profile rather than silently mixing them into the live source run.

- [x] **Step 3: Run the live read-only smoke test.** Execute the MLS-first CLI against the profile. Record whether the environment returns listings, 403, robots denial, timeout, or challenge. Save all responses that were actually received and expose the result in `reports/latest-mls-first.md`.

- [x] **Step 4: Run complete verification.**

```bash
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m compileall -q src tests
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-mls-first.yaml --dry-run
rtk .venv/bin/northshore-watchdog research-run-weekly --config config/research-mls-first.yaml --dry-run
```

Expected: no fabricated candidates; source status and coverage are explicit; Phase 1 and existing Phase 2–5 regressions pass.

## Completion boundary

Stop after this plan. Do not implement dynamic sold comparables, redevelopment economics, or broad unattended crawling in this increment. Report any public-source block as a data-source limitation, not as a successful market scan.
