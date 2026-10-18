# Greater Vancouver Detached House Research System V1 — Phases 2–5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax and every implementation step follows TDD.

**Goal:** Turn the accepted Phase 1 research baseline into a complete, auditable Phase 2–5 pipeline for local or explicitly authorized property research, dynamic comparables, redevelopment economics, and repeatable weekly execution.

**Architecture:** Add focused modules under `src/northshore_watchdog/research/` for source policy, acquisition, enrichment, comparables, redevelopment, deltas, orchestration, and scheduling. Reuse the Phase 1 `PropertyRecord`, provenance, scoring, state, storage, and report contracts; preserve legacy purchase/rental commands and databases.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, SQLite, existing Typer CLI, existing local collectors, `httpx` only through an explicitly authorized/injected read-only adapter, pytest. No new crawler dependency.

---

## Working rules

- Work only in `/Users/xiwei/northshore_property_watchdog`.
- Prefix every shell command with `rtk`.
- Use `apply_patch` for all source, test, fixture, config, and documentation edits.
- Follow RED → verify failure → GREEN → verify pass → refactor for every new behavior.
- Keep the research database separate from `outputs/watchdog.sqlite` and `outputs/rental_watchdog.sqlite`.
- Do not initialize Git; the repository has no Git metadata.
- Do not bypass login, CAPTCHA, robots, paywalls, 403/429 responses, anti-bot challenges, or API limits.
- Disabled or unauthorized sources must not make a network request.
- Missing official evidence stays `UNKNOWN`; conflicts are not scored as favorable values.
- Do not replace a sale price with a listing price in comparable calculations.
- Run the full legacy and research suite after each integration batch.

## File map

Create:

- `src/northshore_watchdog/research/sources.py` — source policy, source-run results, local file adapters, and authorized/injected source adapters.
- `src/northshore_watchdog/research/acquisition.py` — source aggregation, stable identity, Stage 1 market-bucket scan, title gate, and trigger explanations.
- `src/northshore_watchdog/research/enrichment.py` — local/authorized field enrichment providers and conservative merge logic.
- `src/northshore_watchdog/research/comparables.py` — typed comparable records, window fallback, reliability filtering, medians, affordability, dislocation, and land proxy.
- `src/northshore_watchdog/research/redevelopment.py` — zoning-rule registry, scenario economics, product comparison, TOA/assembly, and future opportunity cost.
- `src/northshore_watchdog/research/delta.py` — property observation and score/state change detection.
- `src/northshore_watchdog/research/pipeline.py` — end-to-end stage orchestration and structured run result.
- `src/northshore_watchdog/research/scheduler.py` — local lock, dry-run, cron-compatible weekly execution, and failure handling.
- `data/research/sources/phase2_listings.json` — clearly labelled local authorized/mock listing input.
- `data/research/sources/phase2_assessments.json` — local assessment enrichment fixture.
- `data/research/sources/phase2_zoning.json` — official-style zoning/use enrichment fixture with evidence status.
- `data/research/sources/phase2_schools.json` — school catchment/outlook fixture.
- `data/research/sources/phase2_roads.json` — road class, distance, buffer, and noise fixture.
- `data/research/sources/phase2_history.json` — listing/sold history fixture.
- `data/research/sources/phase2_comparables.json` — sold comparable fixture with more than five reliable records and conflict/low-confidence rows.
- `data/research/sources/phase2_source_registry.yaml` — source registry examples and authorization states.
- `tests/test_research_sources.py`
- `tests/test_research_acquisition.py`
- `tests/test_research_enrichment.py`
- `tests/test_research_comparables.py`
- `tests/test_research_redevelopment.py`
- `tests/test_research_delta.py`
- `tests/test_research_pipeline.py`
- `tests/test_research_scheduler.py`

Modify:

- `src/northshore_watchdog/research/models.py` — source-run, comparable, enrichment, delta, and economic fields while preserving Phase 1 compatibility.
- `src/northshore_watchdog/research/provenance.py` — source status and unresolved-source helpers if needed.
- `src/northshore_watchdog/research/normalization.py` — source-agnostic aliases and non-destructive property merge helpers.
- `src/northshore_watchdog/research/scoring.py` — use Phase 3/4 derived inputs without changing the approved eight maxima or independent-index formulas.
- `src/northshore_watchdog/research/storage.py` — source runs, enrichment payloads, typed comparables, current-property listing, open-conflict listing, and append-only delta persistence.
- `src/northshore_watchdog/research/report.py` — source-run status, comparable/economic summaries, delta sections, and conflict queue.
- `src/northshore_watchdog/research/validation.py` — share pipeline helpers where appropriate without making Phase 1 call acquisition.
- `src/northshore_watchdog/cli.py` — register seven operational research commands; do not alter legacy/rental command semantics.
- `config/research.yaml` — acquisition, enrichment, comparable, redevelopment, scheduler, and report configuration.
- `data/research/README.md` — Phase 2–5 source file and authorization conventions.
- `README.md` — new commands and explicit source limitations.

Generated/ignored local artifacts:

- `reports/weekly/YYYY-MM-DD.md`
- `reports/latest.md`
- `outputs/research_watchdog.sqlite`

## Task 1: Add source policy and typed operational models

**Files:**

- Create: `src/northshore_watchdog/research/sources.py`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/provenance.py`
- Modify: `config/research.yaml`
- Test: `tests/test_research_sources.py`

- [x] **Step 1: Write failing tests for source policy and run results**

Add tests with the following behaviors:

```python
def test_disabled_source_does_not_invoke_transport():
    calls = []
    source = AuthorizedJsonSource(
        name="disabled-api",
        source_type=SourceType.MLS,
        config={"enabled": False, "authorization_status": "AUTHORIZED"},
        transport=lambda: calls.append("called"),
    )

    result = source.collect(datetime(2026, 8, 30, tzinfo=timezone.utc))

    assert result.status is SourceRunStatus.SKIPPED_DISABLED
    assert calls == []
    assert result.records == []
```

Also test unauthorized sources, local JSON success, malformed-row partial status, and an authorized injected transport. Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_sources.py
```

Expected: FAIL because the source classes and statuses do not exist.

- [x] **Step 2: Implement the source contract**

Add these exact public models and interfaces:

```python
class SourceRunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    SKIPPED_UNAUTHORIZED = "SKIPPED_UNAUTHORIZED"
    BLOCKED_POLICY = "BLOCKED_POLICY"

class SourceRunResult(BaseModel):
    source_registry_id: str
    source_name: str
    status: SourceRunStatus
    started_at: datetime
    finished_at: datetime
    records: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

class SourceAdapter(Protocol):
    source_name: str
    source_type: SourceType
    def collect(self, observed_at: datetime) -> SourceRunResult: ...
```

Implement `LocalJsonSource`, `LocalCsvSource`, `LegacyCollectorSource`, and `AuthorizedJsonSource`. The authorized adapter must require `enabled=true` and `authorization_status=AUTHORIZED` before invoking its injected transport; the default config must instantiate no remote transport. Every record must carry source URL/type/retrieval timestamp when converted to `SourceRecord`.

Extend `SourceRegistryEntry` with access method/authorization and run metadata only as optional fields so existing Phase 1 JSON remains valid. Add configuration blocks for `acquisition.sources`, `enrichment.providers`, and `source_policy.remote_default_enabled: false`.

- [x] **Step 3: Run source tests and legacy regressions**

```bash
rtk .venv/bin/pytest -q tests/test_research_sources.py tests/test_collectors.py tests/test_scoring.py tests/test_risk_rules.py
```

Expected: all pass; no legacy collector is called by the research source tests unless explicitly wrapped.

## Task 2: Implement Phase 2 local/authorized acquisition and Stage 1 scan

**Files:**

- Create: `src/northshore_watchdog/research/acquisition.py`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/normalization.py`
- Create: `data/research/sources/phase2_listings.json`
- Create: `data/research/sources/phase2_source_registry.yaml`
- Test: `tests/test_research_acquisition.py`

- [x] **Step 1: Write failing Stage 1 tests**

Test that:

- records are merged by PID/address identity, not listing ID;
- freehold detached records in named market buckets are included;
- core and extended ranges are bucket-specific;
- a large lot, assessment discount, price reduction, long DOM, threshold frontage, or redevelopment signal records a trigger;
- a leasehold or partial-interest record is gated out of normal candidates;
- unknown ownership becomes `NEED_VERIFICATION` rather than a favorable candidate;
- 100 records are not fabricated when only fixture records exist.

Use a wished-for API:

```python
result = scan_stage1(
    listings=[listing],
    config=config,
    observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
)
assert result.candidates[0].market_bucket == "vancouver_east"
assert "PRICE_DISLOCATION" in result.candidates[0].trigger_tags
```

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_acquisition.py
```

Expected: FAIL because `scan_stage1` and result models do not exist.

- [x] **Step 2: Implement acquisition aggregation and Stage 1**

Expose:

```python
def acquire_from_sources(
    adapters: Iterable[SourceAdapter],
    *,
    observed_at: datetime,
) -> AcquisitionResult: ...

def scan_stage1(
    listings: Iterable[Listing | PropertyRecord | dict[str, Any]],
    config: dict[str, Any],
    *,
    observed_at: datetime,
) -> Stage1ScanResult: ...
```

`AcquisitionResult` must contain normalized records, source-run results, provenance, parse errors, and counters. `Stage1Candidate` must contain property ID, market bucket, inclusion decision, gate state, reason codes, trigger tags, and search-range classification. Use `normalize_property` for identity/metrics, map municipality/neighbourhood to a named bucket, and never use absolute price across buckets. Include an extended/special pool even when a price is outside the core range; retain explicit exclusion reasons.

Add the fixture as a local/mock source with 6–10 detached records including one freehold, one assessment-discount record, one major-road record, one unknown title, one leasehold boundary, and one large-lot/TOA hypothesis. Every row must have `source_url`, `source_type`, and `observed_at` metadata in the source registry or row envelope.

- [x] **Step 3: Verify the red-green cycle**

```bash
rtk .venv/bin/pytest -q tests/test_research_acquisition.py
```

Expected: PASS with trigger reasons visible and no source or property identity duplication.

## Task 3: Implement Phase 2 enrichment providers and conservative merge

**Files:**

- Create: `src/northshore_watchdog/research/enrichment.py`
- Create: `data/research/sources/phase2_assessments.json`
- Create: `data/research/sources/phase2_zoning.json`
- Create: `data/research/sources/phase2_schools.json`
- Create: `data/research/sources/phase2_roads.json`
- Create: `data/research/sources/phase2_history.json`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/normalization.py`
- Test: `tests/test_research_enrichment.py`

- [x] **Step 1: Write failing enrichment tests**

Test:

```python
def test_enrichment_updates_only_evidenced_fields_and_keeps_unknowns():
    result = enrich_property(record, providers=[assessment_provider, zoning_provider])
    assert result.property.assessment_total == 1_700_000
    assert result.property.official_zoning_confirmed is True
    assert result.property.official_elementary_catchment is None
    assert {item.field_name for item in result.provenance} >= {"assessment_total", "zoning"}
```

Also test official approved-use evidence, one-PID multi-address behavior, conflicting assessment/frontage values, source failures, road buffer fields, history fields, and that listing text cannot set official zoning or approved use. Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_enrichment.py
```

Expected: FAIL because provider and merge APIs do not exist.

- [x] **Step 2: Implement provider and merge contracts**

Add:

```python
class EnrichmentResult(BaseModel):
    property_id: str
    provider_name: str
    status: str
    field_updates: dict[str, Any] = Field(default_factory=dict)
    source_records: list[SourceRecord] = Field(default_factory=list)
    conflicts: list[DataConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

def enrich_property(
    record: PropertyRecord,
    *,
    providers: Iterable[EnrichmentProvider],
    observed_at: datetime,
) -> EnrichedPropertyResult: ...
```

Implement `JsonMappingProvider` keyed by property ID, PID, or normalized address. It must parse the five fixture types, create one `SourceRecord` per supplied field, detect material mismatches with `detect_material_conflicts_from_provenance`, and merge only non-empty/non-conflicting values. Official zoning and approved use require `OFFICIAL_GOV` source type; assessment requires `BC_ASSESSMENT`; school/road status remains separate from listing copy. Apply assessment/listing/road/lot derived metrics after merge.

Add explicit `source_status`/warnings to enrichment output. A missing file or disabled provider returns a non-fatal skip and leaves fields unknown.

- [x] **Step 3: Run focused and baseline tests**

```bash
rtk .venv/bin/pytest -q tests/test_research_enrichment.py tests/test_research_normalization.py tests/test_research_provenance.py
```

Expected: PASS; conflicts are retained and affected fields do not get used as reliable score inputs.

## Task 4: Extend append-only storage and implement weekly deltas

**Files:**

- Create: `src/northshore_watchdog/research/delta.py`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/storage.py`
- Test: `tests/test_research_delta.py`
- Test: `tests/test_research_storage.py`

- [x] **Step 1: Write failing delta/storage tests**

Test that two observations of one property produce events for price reduction, DOM/relist, sold/status, assessment, zoning, score, confidence, and conflict changes; identical observations produce no duplicate delta. Test source-run and enrichment payload persistence, current-property listing, open conflict listing, and historical row preservation.

Use:

```python
events = diff_property_records(previous, current)
assert {event.event_type for event in events} >= {"price_change", "status_change", "score_change"}
```

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_delta.py tests/test_research_storage.py
```

Expected: FAIL for missing delta events/source-run methods.

- [x] **Step 2: Implement delta and storage methods**

Expose:

```python
def diff_property_records(
    previous: PropertyRecord | None,
    current: PropertyRecord,
    *,
    observed_at: datetime,
) -> list[WatchlistEvent]: ...
```

Compare only meaningful normalized fields and emit structured old/new payloads. Do not emit a reduction for zero/unknown change. Use event types `first_seen`, `price_change`, `price_reduction`, `status_change`, `dom_change`, `relist_change`, `assessment_change`, `zoning_change`, `score_change`, `confidence_change`, `state_change`, `conflict_opened`, `conflict_resolved`, and `source_run_status`.

Extend the SQLite schema with `source_runs` and `enrichment_observations`; retain existing append-only tables. Add `list_current_properties`, `list_open_conflicts`, `save_source_run`, `save_enrichment_observation`, and `record_watchlist_events`. `save_observation` must preserve previous raw/normalized payloads and update only the current projection. Use transactions for each property/run.

- [x] **Step 3: Verify storage history**

```bash
rtk .venv/bin/pytest -q tests/test_research_delta.py tests/test_research_storage.py
```

Expected: PASS and all earlier Phase 1 storage tests remain green.

## Task 5: Implement Phase 3 comparable engine

**Files:**

- Create: `src/northshore_watchdog/research/comparables.py`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/storage.py`
- Modify: `config/research.yaml`
- Create: `data/research/sources/phase2_comparables.json`
- Test: `tests/test_research_comparables.py`

- [x] **Step 1: Write failing comparable tests**

Test:

```python
result = evaluate_comparables(subject, comps, config, observed_at=observed_at)
assert result.comp_window_days == 180
assert result.comp_count >= 5
assert result.expected_market_value == result.median_sold_price
assert result.relative_affordability_index == subject.asking / result.median_sold_price
assert result.price_dislocation_pct == subject.asking / result.expected_market_value - 1
```

Also test fallback to 365 days, fewer-than-five `LOW_COMP_CONFIDENCE`, exclusion of listing-only rows, ±20% lot preference, road/zoning mismatch exclusion, sold-history conflict exclusion, assessment ratio, adjusted land price, and missing building value leaving land proxy unknown. Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_comparables.py
```

Expected: FAIL because the typed comparable engine does not exist.

- [x] **Step 2: Implement typed selection and calculations**

Add:

```python
class ComparableRecord(BaseModel):
    comparable_id: str
    subject_property_id: str
    property_id: str | None = None
    municipality: str
    neighbourhood: str | None = None
    property_type: str | None = None
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    sold_date: datetime | None = None
    sold_price: int | None = None
    asking_price: int | None = None
    lot_area_sqft: float | None = None
    frontage_ft: float | None = None
    depth_ft: float | None = None
    house_size_sqft: float | None = None
    zoning: str | None = None
    road_exposure_level: int | None = None
    current_use_building_value: int | None = None
    reliable: bool = True
    conflict: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    source_records: list[SourceRecord] = Field(default_factory=list)

def evaluate_comparables(
    subject: PropertyRecord,
    comparables: Iterable[ComparableRecord | dict[str, Any]],
    config: dict[str, Any],
    *,
    observed_at: datetime,
) -> ComparableResult: ...
```

Filter by municipality/neighbourhood/detached/title, sold date, sold price, lot preference, frontage/depth/zoning/road/condition similarity, reliability, and conflicts. Use 180 days first; expand to 365 only when fewer than five valid comps remain. Compute medians only from sold prices. Set `expected_market_value` only when at least five reliable records exist. Compute subject `relative_affordability_index`, `price_dislocation_pct`, and land proxy only with an explicit current-use building value. Update the subject record without overwriting provenance.

- [x] **Step 3: Run focused comparable tests and legacy suite**

```bash
rtk .venv/bin/pytest -q tests/test_research_comparables.py tests/test_research_scoring.py tests/test_research_end_to_end.py
```

Expected: PASS; assessment discount alone never supplies expected market value.

## Task 6: Implement Phase 4 redevelopment and product economics

**Files:**

- Create: `src/northshore_watchdog/research/redevelopment.py`
- Modify: `src/northshore_watchdog/research/models.py`
- Modify: `src/northshore_watchdog/research/scoring.py`
- Modify: `config/research.yaml`
- Test: `tests/test_research_redevelopment.py`

- [x] **Step 1: Write failing redevelopment tests**

Test that:

- every detached record gets Keep/Duplex/Max Multiplex;
- a listing phrase cannot mark a scenario legally permitted;
- official zoning rules can mark duplex/multiplex permitted;
- theoretical area and realistic area are distinct;
- costs are split into hard, soft, developer-specific, and contingency;
- 2–6 unit options are compared only within official max units;
- a larger-unit duplex can outrank a six-unit option;
- front/rear and side-by-side/noise-buffer fields are retained;
- TOA/assembly is option value only;
- future uplift uses redevelopment-date existing value.

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_redevelopment.py
```

Expected: FAIL for the new registry/economic API.

- [x] **Step 2: Implement zoning registry and economic analysis**

Expose:

```python
def analyze_redevelopment(
    record: PropertyRecord,
    config: dict[str, Any],
    *,
    comparable_result: ComparableResult | None = None,
    observed_at: datetime,
) -> RedevelopmentAnalysis: ...

def future_uplift(
    existing_property_market_value: int | None,
    new_finished_market_value: int | None,
    total_redevelopment_cost: int | None,
) -> int | None: ...
```

Load `redevelopment.rules` by municipality/zoning. Require `official_zoning_confirmed is True` and an official source reference for entitlement. Use Phase 1 baseline scenario construction as the common scenario shape, then populate official limits, area, cost, saleable area, buyer, fit, execution, product options, resale estimates, and uplift. Select `best_economic_product` with legality, unit-size fit, buyer pool, execution, and margin; do not select solely by unit count. Add `unit_size`, resale and cost assumptions to each option so the report is auditable.

- [x] **Step 3: Verify Phase 4 behavior**

```bash
rtk .venv/bin/pytest -q tests/test_research_redevelopment.py tests/test_research_scoring.py
```

Expected: PASS with official and unverified cases visibly different.

## Task 7: Build the end-to-end Phase 2–4 pipeline

**Files:**

- Create: `src/northshore_watchdog/research/pipeline.py`
- Modify: `src/northshore_watchdog/research/validation.py`
- Modify: `src/northshore_watchdog/research/storage.py`
- Modify: `src/northshore_watchdog/research/report.py`
- Modify: `config/research.yaml`
- Test: `tests/test_research_pipeline.py`

- [x] **Step 1: Write failing pipeline tests**

Test that `run_research_pipeline`:

- uses local fixture sources under default config without network;
- executes acquisition → enrichment → comparable → redevelopment → scoring/state;
- persists source runs, observations, provenance, conflicts, comps, scenarios, snapshots, and deltas;
- on a second run preserves prior observations and emits only real deltas;
- supports stage filters for acquire/enrich/comps/redevelop;
- supports dry-run without adding observations or snapshots;
- returns source, stage, candidate, confidence, conflict, and report counters.

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_pipeline.py
```

Expected: FAIL because the pipeline contract is absent.

- [x] **Step 2: Implement pipeline orchestration**

Add:

```python
def run_research_pipeline(
    config: dict[str, Any],
    *,
    run_type: str = "weekly",
    observed_at: datetime | None = None,
    stages: set[str] | None = None,
    source_names: set[str] | None = None,
    dry_run: bool = False,
) -> ResearchRunResult: ...
```

Use a deterministic observed timestamp when provided. For a full run, load configured source adapters, perform Stage 1, enrich Stage 1/selected properties, evaluate configured comps, run redevelopment analysis, compute Phase 1 score/state, and write all append-only artifacts unless dry-run. On source/provider failure, continue independent providers and mark the run `PARTIAL`. Use hard veto and confidence gate after scores. Feed source statuses, deltas, comps, scenarios, and conflicts to the report writer.

Do not make `research-validate-phase1` call this acquisition pipeline; keep it as a local static baseline command.

- [x] **Step 3: Run pipeline tests and all existing research tests**

```bash
rtk .venv/bin/pytest -q tests/test_research_pipeline.py tests/test_research_validation.py tests/test_research_cli.py tests/test_research_end_to_end.py
```

Expected: PASS; Phase 1 output remains stable.

## Task 8: Implement Phase 5 scheduler, CLI commands, and conflict queue

**Files:**

- Create: `src/northshore_watchdog/research/scheduler.py`
- Modify: `src/northshore_watchdog/cli.py`
- Modify: `src/northshore_watchdog/research/report.py`
- Modify: `config/research.yaml`
- Modify: `README.md`
- Modify: `data/research/README.md`
- Test: `tests/test_research_scheduler.py`
- Test: `tests/test_research_report.py`

- [x] **Step 1: Write failing scheduler/CLI/report tests**

Test that the command registration includes:

```text
research-acquire
research-enrich
research-comps
research-redevelop
research-run-weekly
research-conflicts
research-validate-phase1
```

Test that `--dry-run` does not mutate counts, a held lock returns `LOCKED`, source filters work, partial source failure is reported, open conflicts render with source values, and the weekly report includes the required eight sections plus source/stage/delta/conflict summaries. Test that static Phase 1 wording is not used for a Phase 5 run.

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_scheduler.py tests/test_research_report.py tests/test_research_cli.py
```

Expected: FAIL for missing commands and scheduler.

- [x] **Step 2: Implement scheduler and CLI**

Expose:

```python
def run_weekly_scheduled(
    config: dict[str, Any],
    *,
    observed_at: datetime | None = None,
    dry_run: bool = False,
    source_names: set[str] | None = None,
) -> ResearchRunResult: ...
```

Use an atomic local lock file under the configured research history/run directory, remove it in `finally`, and never hold it across unrelated processes. Register the seven commands with Typer. `research-conflicts` reads open conflicts only and prints JSON/Markdown-safe output. Add cron examples for `research-run-weekly`; do not start a resident daemon or notifications by default.

Extend reports with:

- source run statuses/errors;
- Stage 1 and Stage 2 counts;
- comparable window/count/confidence;
- best product and economic assumptions;
- delta event list;
- open conflict queue with conflicting sources;
- `UNKNOWN`/`NOT_AVAILABLE` for missing evidence.

Update README with command usage, source authorization rules, and the fact that no remote request occurs under default config.

- [x] **Step 3: Verify Phase 5 focused tests**

```bash
rtk .venv/bin/pytest -q tests/test_research_scheduler.py tests/test_research_report.py tests/test_research_cli.py
```

Expected: PASS; legacy CLI command registration remains intact.

## Task 9: Add full local fixtures and acceptance/integration assertions

**Files:**

- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_research_end_to_end.py`
- Modify: `tests/test_research_storage.py`
- Create: `tests/test_research_phase2_to5_acceptance.py`
- Modify: `data/research/README.md`

- [x] **Step 1: Write acceptance assertions before final integration**

Cover:

- one local full-run property has source provenance through report;
- one assessment-discount property has expected value only after five sold comps;
- one property with six-unit legal max selects the best product rather than automatically six units;
- one unknown zoning property remains legally unknown;
- one leasehold/partial/title-conflict property never becomes normal A/B;
- one repeated run keeps observation/score/scenario/history row counts increasing without deleting prior payloads;
- one price/status/zoning/confidence delta appears exactly once per changed observation;
- no network call happens under `config/research.yaml` default;
- Phase 1 CLI/report behavior and all legacy purchase/rental tests remain green.

Run:

```bash
rtk .venv/bin/pytest -q tests/test_research_phase2_to5_acceptance.py
```

Expected: FAIL until the full pipeline is integrated.

- [x] **Step 2: Implement/finalize fixtures and integration wiring**

Keep every fixture labelled as `LOCAL_FIXTURE`/`BOUNDARY_FIXTURE` or `AUTHORIZED_MOCK`; do not present it as current MLS market truth. Configure at least one authorized mock adapter to prove the authorization path without making a network request.

- [x] **Step 3: Run the complete test suite**

```bash
rtk .venv/bin/pytest -q
```

Expected: all existing and new tests pass.

## Task 10: Final verification, review, and completion audit

**Files:**

- Review all changed source/config/test/fixture/report files.
- Generate: `reports/weekly/YYYY-MM-DD.md`, `reports/latest.md`.
- Inspect: `outputs/research_watchdog.sqlite`.

- [x] **Step 1: Run compile, help, and command checks**

```bash
rtk .venv/bin/python -m compileall -q src tests
rtk .venv/bin/northshore-watchdog --help
rtk .venv/bin/northshore-watchdog research-acquire --config config/research.yaml --dry-run
rtk .venv/bin/northshore-watchdog research-run-weekly --config config/research.yaml --dry-run
rtk .venv/bin/northshore-watchdog research-conflicts --config config/research.yaml
```

Expected: exit code 0, all seven research commands visible, dry-run does not increase append-only row counts, and conflict queue is readable.

- [x] **Step 2: Run the real local weekly pipeline twice**

```bash
rtk .venv/bin/northshore-watchdog research-run-weekly --config config/research.yaml
rtk .venv/bin/northshore-watchdog research-run-weekly --config config/research.yaml
```

Read the complete JSON output and report. Record source-run statuses, properties scanned, Stage 1/2 counts, comparable confidence, product selections, conflict count, confidence bands, deltas, and row-count changes between runs.

- [x] **Step 3: Perform requirement-by-requirement audit**

Verify from current files/output—not intent—that:

- Phase 2 source policy and enrichment are operational;
- Phase 3 sold comparable windows/fallback/confidence/dislocation/land proxy are operational;
- Phase 4 legal gate/product economics/TOA/assembly/future opportunity cost are operational;
- Phase 5 scheduler/dry-run/lock/watchlist/delta/report/conflict queue are operational;
- all unknown/conflict/title/assessment/listing-price invariants hold;
- history is append-only;
- default execution makes no remote requests;
- legacy purchase/rental regressions pass.

- [x] **Step 4: Request a focused code review**

Use the requesting-code-review skill with the exact changed-file list, plan, fresh test output, and no-Git explanation. Resolve every Critical/Important finding, then rerun all verification commands.

- [x] **Step 5: Mark the active objective complete only after fresh evidence**

Use `update_goal(status="complete")` only after the full test suite, compile, CLI help, dry-run, two-run history, report inspection, and requirement audit all pass. If a real external source requires credentials, record that as a data-coverage limitation, not as a reason to fabricate data or claim external coverage.
