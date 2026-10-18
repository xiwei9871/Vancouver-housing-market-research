# Greater Vancouver Detached House Research System V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add a typed, auditable, read-only Phase 1 purchase-research core to northshore_property_watchdog without changing the existing rental subsystem or starting large-scale acquisition.

**Architecture:** Add a bounded research package under src/northshore_watchdog/research/. Normalize local case inputs into a PropertyRecord with field-level provenance, conflicts, derived metrics, scenario records, versioned scores, and candidate state. Persist local observations and watchlist history in a separate SQLite database, then generate Phase 1 validation and weekly Markdown reports through a dedicated CLI command.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, SQLite, existing pytest setup, existing Typer CLI, Markdown. No new network or crawler dependency.

---

## Working rules

- Work in /Users/xiwei/northshore_property_watchdog.
- Prefix every shell command with rtk, including test commands.
- Use apply_patch for source and fixture edits.
- Preserve the existing purchase MVP and rental package behavior unless a new compatibility test proves an explicit adapter is needed.
- Do not initialize Git. The target directory currently has no Git metadata; commit steps are listed for environments where version control is available, but the implementation must remain usable without it.
- Do not fetch from external websites in Phase 1.
- Do not treat synthetic sample records as real case validation.
- Do not use an unknown field as a favorable default.
- Do not begin Phase 2 acquisition, scheduler, or remote source integration.

## File map

Create:

- src/northshore_watchdog/research/__init__.py — public research-core exports and version.
- src/northshore_watchdog/research/models.py — typed enums and domain models.
- src/northshore_watchdog/research/provenance.py — source records, evidence scoring, and conflict models.
- src/northshore_watchdog/research/normalization.py — identity resolution, normalization, derived metrics, and conflict detection.
- src/northshore_watchdog/research/scoring.py — eight dimension scores, three independent indexes, tags, and score snapshots.
- src/northshore_watchdog/research/state.py — hard veto and A/B/C/D/NEED_VERIFICATION/SPECIAL_SITUATION resolver.
- src/northshore_watchdog/research/storage.py — separate research SQLite schema and append-only persistence.
- src/northshore_watchdog/research/report.py — weekly report sections and candidate cards.
- src/northshore_watchdog/research/adapters.py — legacy Listing and case-manifest adapters.
- src/northshore_watchdog/research/validation.py — local Phase 1 validation runner and acceptance summary.
- config/research.yaml — market buckets, rubrics, thresholds, source rules, storage, and report paths.
- data/research/README.md — local input conventions and provenance requirements.
- data/research/cases/phase1_cases.yaml — manifest for traceable cases and boundary fixtures.
- data/research/cases/*.json — normalized local case inputs only where an existing repository source is traceable.
- tests/test_research_models.py — model and enum behavior.
- tests/test_research_provenance.py — source evidence and conflict behavior.
- tests/test_research_normalization.py — identity, unit, and metric behavior.
- tests/test_research_scoring.py — eight dimensions, indexes, tags, and versioning.
- tests/test_research_state.py — veto and candidate-state behavior.
- tests/test_research_storage.py — append-only observation and watchlist behavior.
- tests/test_research_report.py — required sections, candidate card, UNKNOWN, and thesis rendering.
- tests/test_research_validation.py — end-to-end case manifest behavior.
- tests/test_research_cli.py — dedicated CLI command behavior.
- docs/superpowers/specs/2026-08-29-greater-vancouver-detached-research-v1-design.md — approved design, already written.
- reports/phase1/.gitkeep or generated validation output directory — local Phase 1 artifacts.

Modify:

- src/northshore_watchdog/cli.py — register research validation/report command without changing rental commands.
- pyproject.toml — only if the new package needs a packaging inclusion or test marker; do not add unnecessary dependencies.
- .gitignore — ignore generated research SQLite and transient reports only if current ignore rules do not already cover them.

## Task 1: Establish the research package and typed schema

**Files:**
- Create: src/northshore_watchdog/research/__init__.py
- Create: src/northshore_watchdog/research/models.py
- Create: config/research.yaml
- Create: data/research/README.md
- Test: tests/test_research_models.py

- [ ] **Step 1: Write failing model tests**

Test that:

- PropertyRecord accepts the required flat fields and keeps absent values as None;
- ownership_type includes all title categories required by the design;
- approved_use includes UNKNOWN and the current-use categories;
- a scenario can represent Keep, Duplex, Multiplex, Character + Infill, and TOA/Assembly;
- ScoreSnapshot can retain decimal scores, config hash, and scoring version;
- candidate states include A, B, C, D, NEED_VERIFICATION, REJECT, and SPECIAL_SITUATION.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_models.py
~~~

Expected: FAIL because the research package and models do not exist.

- [ ] **Step 2: Implement the minimum typed schema**

Use Pydantic models with:

- nullable numeric/string fields for unknown research data;
- timezone-aware timestamps;
- explicit Literal or Enum values rather than free-form title/state values;
- list fields for defects, tags, sources, prior prices, and civic addresses;
- a flat export method that emits the field names in the approved design.

Include explicit duplex fields: duplex_area, max_duplex_area, estimated_front_unit_area, estimated_rear_unit_area; multiplex_area, max_realistic_multiplex_units, and best_economic_product.

- [ ] **Step 3: Add the initial research configuration**

Define market buckets for Vancouver West, Vancouver East, City of North Vancouver, District of North Vancouver, West Vancouver, and Burnaby North. Include neighbourhood maps, core/extended ranges, geometry bands, road classes, score weights, confidence thresholds, unknown treatment, scenario cost ranges, report paths, and research SQLite path.

Do not copy the old North Shore budget values as a universal range. Put each range under its own municipality/market bucket.

- [ ] **Step 4: Run the focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_models.py
~~~

Expected: PASS.

- [ ] **Step 5: Run the existing model/scoring tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_scoring.py tests/test_risk_rules.py
~~~

Expected: PASS with no behavior change in the legacy scorer.

## Task 2: Add provenance, evidence scoring, and conflict records

**Files:**
- Create: src/northshore_watchdog/research/provenance.py
- Test: tests/test_research_provenance.py

- [ ] **Step 1: Write failing provenance tests**

Test that:

- each field provenance record preserves source_url, source_type, retrieved_at, raw_value, normalized_value, source_version, and notes;
- source types outside OFFICIAL_GOV, BC_ASSESSMENT, MLS, VOW, AGGREGATOR, and MANUAL are rejected;
- the ten evidence categories total 100 points;
- unresolved conflicts give zero evidence for the affected item;
- confidence bands are High at 85–100, Medium at 70–84, and Needs Verification below 70;
- a PID conflict and duplicate history conflict are represented with conflicting_sources.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_provenance.py
~~~

Expected: FAIL because provenance behavior does not exist.

- [ ] **Step 2: Implement the minimum provenance API**

Implement:

- SourceType;
- SourceRecord;
- SourceRegistryEntry;
- EvidenceItem;
- DataConflict;
- calculate_data_confidence;
- confidence_band.

Keep field provenance separate from the current PropertyRecord so a value can be updated without losing its prior evidence.

- [ ] **Step 3: Run the focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_provenance.py
~~~

Expected: PASS.

## Task 3: Normalize identity, title gates, units, and derived metrics

**Files:**
- Create: src/northshore_watchdog/research/normalization.py
- Create: src/northshore_watchdog/research/adapters.py
- Test: tests/test_research_normalization.py

- [ ] **Step 1: Write failing normalization tests**

Test that:

- legacy Listing fields map to the new names without changing the legacy object;
- square feet and square metres are normalized consistently;
- address normalization is municipality-aware;
- two civic addresses with one PID set single_title_multi_address=true and independently_tradable=false;
- two addresses without title evidence do not become two tradable properties;
- different PIDs can support independent identity records;
- missing approved use remains UNKNOWN;
- leasehold, strata, partial interest, undivided interest, court sale, and foreclosure are classified explicitly;
- assessment formulas and labels are correct;
- lot geometry uses area, frontage, depth, shape, lane, corner, and slope;
- road class plus distance creates the configured exposure level;
- official frontage just above the official zoning minimum creates JUST_ABOVE_ZONING_THRESHOLD and ZONING_THRESHOLD_ADVANTAGE;
- a material PID, legal-description, lot-size, year-built, or history mismatch creates a material DataConflict.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_normalization.py
~~~

Expected: FAIL because the normalizer and adapter do not exist.

- [ ] **Step 2: Implement identity and adapter functions**

Implement small pure functions:

- adapt_legacy_listing;
- normalize_address;
- resolve_property_identity;
- classify_ownership;
- classify_approved_use;
- detect_single_title_multi_address;
- detect_duplicates_and_conflicts.

Use PID/legal description first, then canonical address and municipality, then listing/source identifiers. Do not use listing price as identity.

- [ ] **Step 3: Implement derived metrics**

Implement:

- derive_assessment_metrics;
- classify_lot_geometry;
- calculate_lot_percentile when a neighbourhood benchmark is available;
- calculate_lot_geometry_score;
- calculate_road_exposure;
- detect_zoning_threshold_advantage;
- classify_permanent_and_fixable_defects.

If the required inputs are absent or conflicting, return UNKNOWN/None plus an explanation and a missing/conflict reference.

- [ ] **Step 4: Run the focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_normalization.py
~~~

Expected: PASS.

- [ ] **Step 5: Verify legacy adapter compatibility**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_collectors.py tests/test_scoring.py tests/test_risk_rules.py
~~~

Expected: PASS.

## Task 4: Implement the eight score dimensions and three independent indexes

**Files:**
- Create: src/northshore_watchdog/research/scoring.py
- Test: tests/test_research_scoring.py

- [ ] **Step 1: Write failing scoring tests**

Test that:

- the eight dimension maxima are exactly 20, 15, 15, 10, 10, 20, 5, and 5;
- subcomponent maxima match the approved design;
- unknown values use the configured conservative provisional rule and expose unknown_fields;
- material conflicts do not score either conflicting value;
- outdoor noise has a larger family-location impact than indoor mitigation;
- lot area alone cannot produce the maximum land score;
- max legal units alone cannot produce the maximum redevelopment score;
- live_score, land_score, and investment_score follow their approved formulas;
- score snapshots preserve one decimal internally, rounded display values, scoring_version, and config hash;
- tags include assessment discount, land value, noise, title risk, zoning threshold, duplex/multiplex option, TOA, assembly, and character where evidence supports them.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_scoring.py
~~~

Expected: FAIL because the V1 scorer does not exist.

- [ ] **Step 2: Implement score component primitives**

Implement a component result object containing:

- points;
- maximum_points;
- evidence_status;
- explanation;
- unknown_fields.

Implement configuration-backed rubrics for each of the eight dimensions. Missing analytical values default to the configured 50 percent conservative provisional value, never to a favorable value, and remain visible in the score explanation.

- [ ] **Step 3: Implement the three indexes and total**

Implement:

- live_score = 45% current house utility + 30% family location + 25% school;
- land_score = 50% Land / Lot / Title + 50% Redevelopment / Option Value;
- investment_score = 30% Price / Safety Margin + 35% land_score + 15% Resale / Affordability + 15% Carrying / Risk + 5% Current House Utility.

Return a ScoreSnapshot that includes dimension breakdowns, index breakdowns, tags, reasons, risks, and reproducibility metadata.

- [ ] **Step 4: Run the focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_scoring.py
~~~

Expected: PASS.

## Task 5: Implement hard veto and candidate state resolution

**Files:**
- Create: src/northshore_watchdog/research/state.py
- Test: tests/test_research_state.py

- [ ] **Step 1: Write failing state tests**

Test that:

- non-prepaid leasehold, severe contamination, severe foundation problem, impossible vacant possession, legally blocked redevelopment, and severe uncompensated highway/industrial defect resolve to REJECT;
- partial interest, undivided interest, court sale, foreclosure, unclear title, serious litigation, serious unresolved strata issue, and resolvable material conflict resolve to SPECIAL_SITUATION;
- total score at least 60 with confidence below 70 resolves to NEED_VERIFICATION;
- total score at least 80 with confidence 70–79 resolves to NEED_VERIFICATION rather than A;
- total score at least 80 with confidence at least 80 resolves to A_STRONG_CANDIDATE;
- 70–79 with confidence at least 70 resolves to B_WATCH;
- 60–69 with confidence at least 70 resolves to C_OPPORTUNISTIC;
- below 60 resolves to D_REJECT;
- a sold/removed listing is not returned as an active site-visit candidate even if its historical score is high.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_state.py
~~~

Expected: FAIL because the state resolver does not exist.

- [ ] **Step 2: Implement the state resolver**

Apply the approved priority:

1. direct hard veto;
2. special situation;
3. high-potential or high-score confidence gate;
4. D/C/B/A score bands.

Return state, reason codes, recommendation, and site-visit eligibility separately so a sold comparable can remain useful as evidence without being recommended as an active property.

- [ ] **Step 3: Run the focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_state.py
~~~

Expected: PASS.

## Task 6: Add redevelopment scenario records and conservative baseline economics

**Files:**
- Modify: src/northshore_watchdog/research/models.py
- Modify: src/northshore_watchdog/research/scoring.py
- Modify: config/research.yaml
- Test: tests/test_research_scoring.py
- Test: tests/test_research_normalization.py

- [ ] **Step 1: Write failing scenario tests**

Test that every detached case creates Keep, Duplex, and Max Realistic Multiplex scenario records. Test that official evidence is required before a scenario is marked legally permitted. Test that a product with fewer units can outrank a maximum-unit product when unit size, buyer pool, cost, or execution risk is better.

Test duplex-specific fields for front/rear and side-by-side layouts, including the optional front-building noise-buffer hypothesis.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_scoring.py tests/test_research_normalization.py
~~~

Expected: FAIL because scenario construction is incomplete.

- [ ] **Step 2: Implement the Phase 1 scenario baseline**

Implement deterministic scenario construction from supplied normalized fields and configuration:

- preserve unknown legal values;
- separate hard, soft, developer-specific, and contingency costs;
- calculate theoretical and realistic buildable area only when required zoning inputs exist;
- compare 2-, 3-, 4-, 5-, and 6-unit products only when legally supported;
- select best_economic_product using product-market fit and execution complexity, not unit count alone;
- model future opportunity cost only when existing value and project cost inputs are available.

Do not claim automatic TOA entitlement. Store TOA as option value with current zoning and rezoning requirements.

- [ ] **Step 3: Run focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_scoring.py tests/test_research_normalization.py
~~~

Expected: PASS.

## Task 7: Implement separate append-only research storage

**Files:**
- Create: src/northshore_watchdog/research/storage.py
- Test: tests/test_research_storage.py

- [ ] **Step 1: Write failing storage tests**

Test that:

- the research database creates properties, property_observations, field_provenance, data_conflicts, comparable_records, redevelopment_scenarios, score_snapshots, watchlist_events, source_registry, and weekly_runs;
- inserting a second observation preserves the first raw/normalized payload;
- the current property projection may update without deleting history;
- score snapshots are versioned and not overwritten;
- price changes and status changes create watchlist events;
- conflict-opened and conflict-resolved events are append-only;
- a stable property identity prevents duplicate properties across listing cycles.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_storage.py
~~~

Expected: FAIL because the research storage layer does not exist.

- [ ] **Step 2: Implement transactional SQLite persistence**

Use a separate path from outputs/watchdog.sqlite, configured as outputs/research_watchdog.sqlite. Use explicit schema creation and parameterized SQL. Store typed payloads as JSON alongside queryable identifiers and timestamps.

Implement:

- init;
- save_observation;
- save_provenance;
- save_conflict;
- save_comparable;
- save_scenario;
- save_score_snapshot;
- record_watchlist_event;
- get_current_property;
- list_score_snapshots;
- list_watchlist_events;
- start_weekly_run and finish_weekly_run.

- [ ] **Step 3: Run focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_storage.py
~~~

Expected: PASS.

## Task 8: Implement candidate cards and weekly/Phase 1 Markdown reports

**Files:**
- Create: src/northshore_watchdog/research/report.py
- Test: tests/test_research_report.py

- [ ] **Step 1: Write failing report tests**

Test that:

- the report contains Executive Summary, Market Changes, Top New Candidates, Major Price Reductions, Watchlist Changes, Sold / Removed, Data Conflicts, and Recommended Site Visits;
- Executive Summary includes the required scan, candidate, discount, and lot-opportunity counters;
- candidate cards contain title, lot, house, assessment, listing history, road/noise, school, zoning/use, scenario, renovation, defects, comparable, three independent scores, total, confidence, status, recommendation, and thesis;
- UNKNOWN and NOT_AVAILABLE render explicitly;
- source URLs and source types appear near key facts;
- an investment thesis contains two to four non-generic sentences;
- sold and hard-veto cases appear in their appropriate sections rather than as site-visit recommendations.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_report.py
~~~

Expected: FAIL because the V1 report writer does not exist.

- [ ] **Step 2: Implement the report writer**

Implement:

- summarize_research_run;
- build_candidate_card;
- build_weekly_report;
- write_weekly_report;
- write_latest_report;

Use a deterministic ordering: active A/B/NEED_VERIFICATION candidates first by investment score and then address, followed by watchlist deltas, conflicts, and historical references.

Make the Phase 1 output explicitly identify run_type=phase1_validation and avoid presenting static local records as current weekly market changes.

- [ ] **Step 3: Run focused tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_report.py
~~~

Expected: PASS.

## Task 9: Create the local case manifest and adapters

**Files:**
- Create: data/research/cases/phase1_cases.yaml
- Create: data/research/cases/1379-haywood.json
- Create: data/research/cases/1531-inglewood.json
- Create: data/research/cases/4670-willow-creek.json
- Create: data/research/cases/4110-burkeridge.json
- Create: data/research/cases/532-w-23.json
- Create: data/research/cases/2553-bendale.json
- Create: data/research/cases/1380-coleman.json
- Create: data/research/cases/1006-hendecourt.json
- Create: data/research/cases/1494-ross.json
- Modify: src/northshore_watchdog/research/adapters.py
- Test: tests/test_research_validation.py

- [ ] **Step 1: Write failing manifest tests**

Test that the manifest distinguishes:

- traceable research records;
- synthetic boundary fixtures;
- coverage gaps.

Test that the manifest includes at least five traceable real research cases when the local source references are present, and never counts 1000 Sample Ambleside Avenue, 321 Queensbury Avenue, or 88 Leasehold Lane merely because their fields look plausible.

Test that each case has a source reference, source type, retrieval/observation timestamp, expected behavior, and verification notes.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_validation.py
~~~

Expected: FAIL because the case manifest and validation runner do not exist.

- [ ] **Step 2: Populate cases from existing local evidence**

Use existing outputs, saved records, and local research notes as MANUAL, AGGREGATOR, MLS, VOW, or other approved source types only when the source is actually present. Preserve unknown PID, zoning, school, or sale values rather than copying unsupported values.

The initial case set should cover:

- West Vancouver standard/old lot and assessment discount;
- West Vancouver large-lot lifestyle/permanent-risk discount;
- North Vancouver normal detached;
- North Vancouver as-is/condition discount;
- target-shape sold comparable;
- bare-land strata boundary;
- at least one leasehold boundary if a traceable non-synthetic record exists.

Record Vancouver East assessment discount, Knight major-road, TOA large lot, partial-interest/court sale, and small freehold-strata coach house as explicit coverage gaps if no traceable local case exists. Add boundary fixture records for rule tests only; label them synthetic.

- [ ] **Step 3: Implement case loading**

Implement:

- load_case_manifest;
- load_case_payload;
- classify_case_quality;
- build_validation_input;
- expected-behavior checks.

The loader must reject a case with missing provenance metadata instead of silently assigning MANUAL.

- [ ] **Step 4: Run focused validation tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_validation.py
~~~

Expected: PASS, with any missing category reported as coverage_gaps rather than a false success.

## Task 10: Build the Phase 1 runner and CLI integration

**Files:**
- Create: src/northshore_watchdog/research/validation.py
- Modify: src/northshore_watchdog/cli.py
- Test: tests/test_research_validation.py
- Test: tests/test_research_cli.py

- [ ] **Step 1: Write failing runner and CLI tests**

Test that:

- the Phase 1 command reads only the local manifest and case files;
- it creates local SQLite and Markdown artifacts;
- it does not invoke existing collectors or remote HTTP;
- it reports case counts, confidence bands, state counts, coverage gaps, conflicts, and output paths;
- it writes reports/phase1/validation-YYYY-MM-DD.md and reports/latest.md;
- it does not alter rental storage or rental commands.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_validation.py tests/test_research_cli.py
~~~

Expected: FAIL because the command is not registered.

- [ ] **Step 2: Implement the read-only local runner**

Implement a pipeline that:

1. loads config and manifest;
2. loads and adapts cases;
3. normalizes identity, title, fields, and metrics;
4. persists local observation/provenance/conflicts;
5. builds baseline scenarios;
6. computes scores and state;
7. persists score snapshots and watchlist events;
8. writes the validation report and latest report;
9. returns a structured acceptance summary.

Register a command such as research-validate-phase1 with the existing Typer app. Keep legacy commands and rental commands intact.

- [ ] **Step 3: Run focused CLI tests**

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_validation.py tests/test_research_cli.py
~~~

Expected: PASS.

## Task 11: Add compatibility, end-to-end, and acceptance assertions

**Files:**
- Create: tests/test_research_end_to_end.py
- Modify: tests/test_research_validation.py
- Modify: README.md only if the new command and paths need user-facing documentation

- [ ] **Step 1: Write failing end-to-end assertions**

Assert:

- one standard freehold case can flow from manifest to report;
- one assessment-discount case exposes assessment labels but does not call it cheap without comparable/defect evidence;
- one permanent-noise/road or environmental-risk case separates indoor mitigation from outdoor exposure;
- one bare-land strata or leasehold case is excluded from normal ranking;
- one material conflict enters the conflict queue and cannot use conflicting values for scoring;
- one high-score low-confidence case is NEED_VERIFICATION rather than A;
- a second observation preserves the first state and emits a delta event;
- the generated report answers why cheap, opportunity versus defect, land quality, five-to-ten-year use, future product, and safety margin.

Run:

~~~bash
rtk .venv/bin/pytest -q tests/test_research_end_to_end.py
~~~

Expected: FAIL until all modules are wired together.

- [ ] **Step 2: Implement only the missing integration behavior**

Do not broaden the model or add a crawler. Fix adapters, persistence, ordering, or report assembly until the assertions pass.

- [ ] **Step 3: Run the complete test suite**

Run:

~~~bash
rtk .venv/bin/pytest -q
~~~

Expected: PASS with existing and research tests green.

- [ ] **Step 4: Run the Phase 1 command against local cases**

Run:

~~~bash
rtk .venv/bin/northshore-watchdog research-validate-phase1 --config config/research.yaml
~~~

Expected: local SQLite and Phase 1 Markdown artifacts are generated, no remote acquisition occurs, and the output lists real case count, boundary fixture count, conflicts, confidence distribution, and coverage gaps.

## Task 12: Review, verify, and hand off Phase 1

**Files:**
- Review: all changed files
- Output: reports/phase1/validation-YYYY-MM-DD.md
- Output: reports/latest.md

- [ ] **Step 1: Inspect the final working tree and generated artifacts**

Run:

~~~bash
rtk git status --short --branch
rtk rg --files src/northshore_watchdog/research tests data/research reports/phase1 config | sort
rtk rg -n 'UNKNOWN|NEED_VERIFICATION|DATA_CONFLICT|coverage_gaps|run_type=phase1_validation' reports/phase1 reports/latest.md
~~~

If Git is still unavailable, record that fact in the implementation summary instead of initializing it.

- [ ] **Step 2: Perform requirements verification**

Check the approved design line by line:

- schema and flat export;
- title gate and multi-address behavior;
- provenance and conflict queue;
- assessment and lot metrics;
- eight score dimensions;
- three independent scores;
- state machine and hard veto;
- append-only history;
- report sections and thesis;
- at least five traceable cases or an explicit documented blocker;
- no large-scale acquisition.

- [ ] **Step 3: Request a focused code review**

Use the requesting-code-review skill after implementation. Provide the reviewer the final diff or, if Git remains unavailable, the exact changed-file list and fresh test output. Resolve all critical and important findings before handoff.

- [ ] **Step 4: Run final verification**

Run:

~~~bash
rtk .venv/bin/pytest -q
rtk .venv/bin/northshore-watchdog research-validate-phase1 --config config/research.yaml
~~~

Read the complete outputs and record exit codes, test count, case count, conflict count, confidence distribution, and coverage gaps in the acceptance report.

- [ ] **Step 5: Stop at Phase 1**

Do not implement listing acquisition, official source connectors, dynamic comparable automation, redevelopment automation beyond the baseline scenario records, scheduler, or remote notifications. Report those as the next-step plan only.

