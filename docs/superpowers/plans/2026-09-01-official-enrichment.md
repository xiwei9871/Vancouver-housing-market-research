# Official Direct Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, read-only Phase 2 enrichment layer that directly queries BC Assessment, municipal zoning, school catchments, and official sold-history sources for Stage 2 candidates and preserves auditable evidence.

**Architecture:** Independent source-family adapters share one bounded HTTP/snapshot transport. Each adapter returns a source run plus identity-matched enrichment rows; the pipeline persists the source run and feeds rows through the existing `EnrichmentProvider`, provenance, conflict, confidence, scoring, and report code. Interactive or blocked pages produce explicit verification states and never fall back to fixtures.

**Tech Stack:** Python 3.11, Pydantic 2, httpx, BeautifulSoup, existing SQLite append-only storage, YAML configuration, pytest.

---

## File map

Create:

- `src/northshore_watchdog/research/official_transport.py` — bounded official HTTP transport, response classification, and raw snapshots.
- `src/northshore_watchdog/research/official_enrichment.py` — batch result models, identity matching, and the four official adapters.
- `tests/test_research_official_transport.py` — transport limits, blocking, and snapshot tests.
- `tests/test_research_official_enrichment.py` — adapter/parser/matching tests.
- `tests/fixtures/official/` — deterministic HTML/JSON/ArcGIS-style source responses.

Modify:

- `src/northshore_watchdog/research/models.py` — explicit official enrichment status/scope fields.
- `src/northshore_watchdog/research/provenance.py` — matching and raw-snapshot metadata on `SourceRecord`.
- `src/northshore_watchdog/research/pipeline.py` — build, run, persist, and link official collectors for Stage 2 only.
- `src/northshore_watchdog/research/storage.py` — append-only verification queue persistence and schema migration.
- `src/northshore_watchdog/research/report.py` — official source status, sold-history scope, and verification details.
- `src/northshore_watchdog/cli.py` — expose official-source selection/status without changing the weekly command shape.
- `config/research-mls-first.yaml` — bounded official enrichment configuration and routing.
- `data/research/sources/mls_first_source_registry.yaml` — official source registry entries and trust metadata.
- `README.md` — direct-enrichment commands, source scope, and limitations.

The repository has no `.git` directory. Do not initialize Git or add commit
steps; use the test and report checkpoints below as the change history.

### Task 1: Add explicit enrichment result and provenance types

**Files:**

- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/provenance.py`
- Create: `tests/test_research_official_enrichment.py`

- [ ] **Step 1: Write the failing type tests.**

```python
def test_official_lookup_row_records_identity_scope_and_snapshot():
    row = OfficialLookupRow(
        property_id="property:1",
        status=OfficialQueryStatus.SUCCESS,
        fields={"assessment_total": 1700000},
        source_url="https://official.example/property/1",
        match_method="PID",
        query_scope="last_three_full_calendar_years",
        raw_snapshot_path="data/research/raw/official-enrichment/a.json",
    )
    assert row.status is OfficialQueryStatus.SUCCESS
    assert row.match_method == "PID"


def test_source_record_exposes_direct_query_metadata():
    source = SourceRecord(
        property_id="property:1",
        field_name="zoning",
        source_url="https://city.example/zoning/1",
        source_type=SourceType.OFFICIAL_GOV,
        retrieved_at=OBSERVED_AT,
        match_method="address",
        query_scope="current_bylaw",
        raw_snapshot_path="raw/zoning.json",
    )
    assert source.raw_snapshot_path == "raw/zoning.json"
```

- [ ] **Step 2: Run the focused test and observe the expected import/field failure.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "lookup_row or direct_query_metadata"`

Expected: FAIL because the official result types and metadata fields do not yet exist.

- [ ] **Step 3: Implement the minimal types.**

Add `OfficialQueryStatus` with `SUCCESS`, `NO_MATCH`, `AMBIGUOUS`, `BLOCKED`, `FAILED`, and `SKIPPED_DISABLED`; add `OfficialLookupRow` with `property_id`, `status`, `fields`, `source_url`, `retrieved_at`, `source_version`, `match_method`, `query_scope`, `raw_snapshot_path`, `warnings`; add `OfficialEnrichmentBatch` containing one `SourceRunResult` and lookup rows. Add `match_method`, `query_scope`, and `raw_snapshot_path` optional fields to `SourceRecord`. Add explicit `school_district`, `sold_history_scope`, and `official_enrichment_status` fields to `PropertyRecord`.

- [ ] **Step 4: Run the focused test.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "lookup_row or direct_query_metadata"`

Expected: PASS.

### Task 2: Build shared bounded official transport

**Files:**

- Create: `src/northshore_watchdog/research/official_transport.py`
- Create: `tests/test_research_official_transport.py`

- [ ] **Step 1: Write failing transport tests.**

Test that an injected transport can fetch one allowed official URL and writes a `SourceSnapshot`; a disallowed domain returns `BLOCKED`; a 403/429 or challenge marker returns `BLOCKED`; and the second request is not made after `max_requests_per_run=1`.

```python
result = transport.fetch_all([{"url": "https://official.example/one"},
                              {"url": "https://official.example/two"}])
assert len(result.snapshots) == 1
assert result.status == "PARTIAL"
assert "request cap" in " ".join(result.warnings)
```

- [ ] **Step 2: Run the focused test and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_transport.py`

Expected: FAIL because `OfficialHttpTransport` is absent.

- [ ] **Step 3: Implement transport behavior.**

Implement `OfficialHttpTransport.fetch(url, method="GET", params=None, data=None)` and `fetch_all(requests)`. Validate `https`, allowed host, request cap, timeout, delay, and redirect host. Save every response, including 401/403/429/challenge responses, under the configured raw directory with SHA-256 metadata. Return `SourceRunResult` statuses using the existing `SourceRunStatus` enum. Do not retry policy blocks. Accept an injected callable/`httpx.MockTransport` in tests.

- [ ] **Step 4: Run transport tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_transport.py`

Expected: PASS.

### Task 3: Implement identity matching and BC Assessment

**Files:**

- Modify: `src/northshore_watchdog/research/official_enrichment.py`
- Create/update: `tests/fixtures/official/bcassessment-property.html`
- Modify: `tests/test_research_official_enrichment.py`

- [ ] **Step 1: Write failing BCA and identity tests.**

Cover PID-first matching, normalized address fallback, multiple search results as `AMBIGUOUS`, incompatible PID/legal description as a conflict, one PID with two civic addresses as `SINGLE_TITLE_MULTI_ADDRESS`, assessment field parsing, and sales rows with `query_scope="last_three_full_calendar_years"`.

```python
result = BCAssessmentAdapter(...).collect_for_records([record], OBSERVED_AT)
row = result.rows[0]
assert row.status is OfficialQueryStatus.SUCCESS
assert row.fields["assessment_land"] == 1500000
assert row.fields["previous_sale_prices"] == [1250000]
assert row.query_scope == "last_three_full_calendar_years"
```

- [ ] **Step 2: Run the tests and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "bca or identity or multi_address"`

Expected: FAIL because the adapter and parser are absent.

- [ ] **Step 3: Implement identity helpers and BCA adapter.**

Implement `match_official_identity(record, candidates)` with PID as the first key and normalized address plus municipality as the fallback. Return no match or ambiguous without selecting a fuzzy candidate. Implement `BCAssessmentAdapter` using configured official search requests and a parser that extracts current/previous assessment, land/improvement, PID/legal description, property facts, and available recent registered sales. Emit `OfficialLookupRow` and `SourceRecord`-ready fields; preserve the source scope and snapshot path.

- [ ] **Step 4: Run BCA tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "bca or identity or multi_address"`

Expected: PASS.

### Task 4: Implement municipal zoning routing and parsing

**Files:**

- Modify: `src/northshore_watchdog/research/official_enrichment.py`
- Modify: `tests/test_research_official_enrichment.py`
- Create/update: `tests/fixtures/official/vancouver-zoning.json`, `burnaby-zoning.json`, `north-vancouver-zoning.json`, `west-vancouver-zoning.json`

- [ ] **Step 1: Write failing zoning tests.**

Test all five municipality routes, including separate City/District North Vancouver routing; parse zoning code and bylaw version; apply numeric rules only when the rule response matches the zoning code; and keep `toa`/future policy separate from current entitlement.

```python
assert route_zoning_source("District of North Vancouver").name == "municipal-zoning-dnv"
assert row.fields["zoning"] == "RS-1"
assert row.fields["bylaw_version"] == "3210"
assert row.fields["maximum_units"] is None  # map code without matching bylaw rule
```

- [ ] **Step 2: Run zoning tests and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k zoning`

Expected: FAIL because the routes and parser are absent.

- [ ] **Step 3: Implement zoning adapter.**

Implement municipality routing from config, official parcel/map lookup, and bylaw-rule lookup. Normalize only fields present in the official response. Mark unsupported or interactive sources as `BLOCKED`/`NEED_VERIFICATION`; never infer development rights from listing copy or a zoning label alone.

- [ ] **Step 4: Run zoning tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k zoning`

Expected: PASS.

### Task 5: Implement school catchment and sold-history adapters

**Files:**

- Modify: `src/northshore_watchdog/research/official_enrichment.py`
- Modify: `tests/test_research_official_enrichment.py`
- Create/update: `tests/fixtures/official/school-catchment.json`, `bca-sales.json`

- [ ] **Step 1: Write failing school/history tests.**

Cover SD39, SD41, SD44, and SD45 route selection; elementary and secondary fields; official locator scope; separate BCA sold history from listing prices; and mismatched official/MLS price-date pairs creating a conflict.

```python
assert school.fields["official_elementary_catchment"] == "Example Elementary"
assert school.fields["official_secondary_catchment"] == "Example Secondary"
assert sold.fields["previous_sale_prices"] == [1250000]
assert "asking" not in sold.fields
```

- [ ] **Step 2: Run tests and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "school or sold or history"`

Expected: FAIL because the adapters are absent.

- [ ] **Step 3: Implement school and sold adapters.**

Implement config-driven school-district routing and parsers for official locator/boundary responses. Implement `SoldHistoryAdapter` as the BCA sales projection with explicit scope. Keep MLS/VOW/REALTOR.ca/Zolo observations in separate source records and feed date/price pairs into existing material-conflict detection.

- [ ] **Step 4: Run school/history tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_enrichment.py -k "school or sold or history"`

Expected: PASS.

### Task 6: Integrate official collectors into the enrichment stage

**Files:**

- Modify: `src/northshore_watchdog/research/pipeline.py`
- Modify: `src/northshore_watchdog/research/enrichment.py`
- Modify: `tests/test_research_official_pipeline.py`

- [ ] **Step 1: Write failing integration tests.**

Use injected official transports and a temporary SQLite database. Assert that only the bounded in-range Stage 1 research/verification queue is queried, the configured cap is respected, source runs are persisted before provenance, source-run IDs are attached to provenance, official fields update the property, and a blocked source yields `PARTIAL` plus `NEED_VERIFICATION` without loading `data/research/sources/phase2_*.json`. Downstream zoning, comparables, redevelopment, and scoring remain restricted to the normal Stage 1-included set.

```python
result = run_research_pipeline(config, stages={"enrich"}, dry_run=False)
assert result.stage_status["enrich"] == "PARTIAL"
assert result.records[0].assessment_total == 1700000
assert result.provenance["property:1"][0].source_run_id is not None
assert result.records[0].official_enrichment_status["school-catchment"] == "BLOCKED"
```

- [ ] **Step 2: Run integration tests and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_pipeline.py`

Expected: FAIL because the pipeline builds only JSON mapping providers.

- [ ] **Step 3: Implement pipeline integration.**

Add `build_official_enrichment_collectors()`. In the existing `enrich` stage, compute bounded official-query IDs from the in-range Stage 1 research/verification queue, run only configured collectors against those records, register their trusted source definitions, persist each returned `SourceRunResult` when not dry-running, create mapping providers from returned rows, call existing `enrich_property`, and link provenance with both `weekly_run_id` and `source_run_id`. Merge official status into `PropertyRecord.official_enrichment_status`; set run status to `PARTIAL` when any official source is blocked or failed. Keep `_stage2_property_ids` as the downstream title-gated set.

- [ ] **Step 4: Run integration tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_pipeline.py`

Expected: PASS.

### Task 7: Persist verification items and update reports

**Files:**

- Modify: `src/northshore_watchdog/research/storage.py`
- Modify: `src/northshore_watchdog/research/report.py`
- Modify: `src/northshore_watchdog/cli.py`
- Modify: `tests/test_research_storage.py`, `tests/test_research_report.py`

- [ ] **Step 1: Write failing persistence/report tests.**

Assert that blocked, ambiguous, and unmatched rows append verification items with property/source/status/reason/raw snapshot; prior items remain after a later weekly run; the weekly report prints official source status and sold-history scope; and the conflicts command remains separate from verification items.

- [ ] **Step 2: Run focused tests and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_storage.py tests/test_research_report.py -k "verification or official or scope"`

Expected: FAIL because the verification table and report section are absent.

- [ ] **Step 3: Implement append-only verification storage.**

Add a `verification_items` table with `property_id`, `source_registry_id`, `source_url`, `status`, `reason`, `match_method`, `raw_snapshot_path`, `observed_at`, `weekly_run_id`, and JSON details. Add `save_verification_item()` and `list_open_verification_items()`. Persist one item per source/property/status attempt; do not overwrite earlier records.

- [ ] **Step 4: Implement report and CLI output.**

Add an Official Enrichment section to each candidate and a weekly verification summary. Expose the configured official source statuses in `research-enrich` JSON output and add a read-only `research-verifications` command that returns the append-only queue.

- [ ] **Step 5: Run focused tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_storage.py tests/test_research_report.py -k "verification or official or scope"`

Expected: PASS.

### Task 8: Add configuration, registry, and deterministic end-to-end fixtures

**Files:**

- Modify: `config/research-mls-first.yaml`
- Modify: `data/research/sources/mls_first_source_registry.yaml`
- Modify: `README.md`
- Create: `tests/test_research_official_end_to_end.py`

- [ ] **Step 1: Write the configuration/end-to-end tests.**

Load the operational profile and assert `stage2_only=true`, `max_properties_per_run=30`, all four source names are present, official source types are correct, and local fixture providers are not selected by the official path.

- [ ] **Step 2: Run the test and observe RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_end_to_end.py -k config`

Expected: FAIL because the official section and registry entries are absent.

- [ ] **Step 3: Add bounded source configuration.**

Add official source definitions, allowed official domains, parser versions, raw snapshot directory, timeout/delay/request caps, municipality routes, school-district routes, and the four official registry entries. Keep current MLS-first acquisition definitions unchanged. Do not mark a response as confirmed merely because a configured URL exists; confirmation requires a successful identity-matched response.

- [ ] **Step 4: Add deterministic end-to-end assertions and documentation.**

Use injected transports for BCA, one zoning route, one school route, and a sold-history cross-check. Assert assessment metrics, official zoning evidence, catchments, sold scope, provenance, confidence, and verification states survive a full `research-enrich` run. Document direct commands and the three-year BCA sales scope.

- [ ] **Step 5: Run the end-to-end tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_end_to_end.py`

Expected: PASS.

### Task 9: Run bounded live smoke queries and complete verification

**Files:**

- Modify: `reports/latest-mls-first.md`
- Create: `reports/phase2/official-enrichment-2026-09-01.md`

- [ ] **Step 1: Run focused unit/integration tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_official_transport.py tests/test_research_official_enrichment.py tests/test_research_official_pipeline.py tests/test_research_official_end_to_end.py`

Expected: PASS.

- [ ] **Step 2: Run full regression tests and compile check.**

Run: `rtk .venv/bin/pytest -q` and `rtk .venv/bin/python -m compileall -q src`

Expected: all tests pass and compileall exits successfully.

- [ ] **Step 3: Run a bounded read-only smoke query.**

Run the official enrichment command against existing Stage 2 records with the configured cap of 30. Ensure the smoke set includes BC Assessment, Vancouver, Burnaby, City/District North Vancouver, West Vancouver, SD39, SD41, SD44, and SD45 routes where candidate records exist. Save actual HTTP status, match method, snapshot paths, source scope, and any block/interactive result in the Phase 2 report.

- [ ] **Step 4: Verify the acceptance boundary.**

Confirm the report can explain assessment/land-assessment discount, official zoning status, elementary/secondary catchment, official sold-history scope, title identity, conflicts, and unknowns. Confirm no Phase 3 comparable or redevelopment automation was started.

- [ ] **Step 5: Stop and hand off.**

Report changed files, test evidence, live source statuses, data limitations, and remaining verification items. Pause for user review before any broader acquisition or Phase 3 work.

## Plan self-review

- Spec coverage: source adapters, identity rules, raw snapshots, provenance, bounded configuration, status handling, append-only history, tests, live smoke, reporting, and Phase 2 stop boundary are covered by Tasks 1–9.
- Placeholder scan: no placeholder implementation steps are used; each code change names a file, behavior, test command, and expected result.
- Type consistency: `OfficialLookupRow`, `OfficialQueryStatus`, `OfficialEnrichmentBatch`, `SourceRunResult`, `SourceRecord`, and `PropertyRecord.official_enrichment_status` are introduced before pipeline integration uses them.
- Scope check: the plan contains one cohesive Phase 2 direct-enrichment subsystem; MLS acquisition, comparables, and redevelopment remain outside scope.
