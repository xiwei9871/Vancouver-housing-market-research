# Greater Vancouver Detached House Research System V1 — Phases 2–5 Design

**Date:** 2026-08-30

**Status:** Approved in conversation; implementation starts after this design is recorded.

**Scope:** Complete the operational Phase 2–5 research pipeline on top of the accepted Phase 1 baseline. The system remains read-only with respect to external sites: local files and explicitly authorized feeds may be read, but the default configuration performs no remote requests and never bypasses login, CAPTCHA, robots, paywall, rate, or API controls.

## 1. Objective and completion boundary

The system must progress from a static, auditable baseline to a repeatable weekly pipeline that can answer, for each detached-house candidate:

1. what was acquired and from which source;
2. what assessment, title, zoning, school, road, and history evidence is available;
3. whether the asking price is dislocated from reliable local sold comparables;
4. what current-use, duplex, multiplex, character/infill, TOA, or assembly options are legally supported or remain unknown;
5. which product is economically more sensible than merely maximizing units;
6. what changed since the last run; and
7. which five to ten properties should receive deeper human review.

Phase 2–5 completion means the software, local fixtures, CLI commands, storage, reports, and tests for these behaviors exist and run end to end. It does not mean that every Greater Vancouver official record is automatically available without credentials, that an unknown entitlement becomes legal, or that a market valuation is produced when comparable evidence is insufficient.

Phase 1 remains intact and is not migrated into the legacy purchase or rental databases.

## 2. Existing repository constraints

The canonical repository is `/Users/xiwei/northshore_property_watchdog`.

Existing components include:

- legacy `Listing` collectors for local official-feed fixtures, saved-search email, saved HTML, manual links, and future authorized APIs;
- legacy purchase scoring/storage/reporting;
- a separate rental subsystem;
- the Phase 1 `research/` package with typed records, provenance, scoring, state, SQLite storage, and Markdown reports.

The Phase 2–5 implementation will add adapters and engines under `src/northshore_watchdog/research/`. Legacy commands remain registered and their behavior remains covered by the existing tests. No Git repository is present, so documentation and code are saved in place without initializing Git.

## 3. Chosen architecture

The recommended bounded-pipeline architecture is adopted:

```text
source adapters
    |
    v
acquisition / universe scan
    |
    v
normalization + title gate + provenance + conflict queue
    |
    +--> assessment / zoning / school / road / history enrichment
    |
    v
dynamic comparable engine
    |
    v
zoning-driven redevelopment engine
    |
    v
score + candidate state
    |
    v
append-only research SQLite
    |
    v
delta / watchlist / weekly report / conflict queue
```

The components are deliberately separated:

| Component | Responsibility | Allowed side effects |
|---|---|---|
| `research/sources.py` | source policy, source registry, local/authorized source descriptors | local reads; an injected client may make an authorized read only |
| `research/acquisition.py` | convert source records to listings, merge by property identity, apply Stage 1 filters and opportunity triggers | local observations and source-run metadata |
| `research/enrichment.py` | load local/authorized assessment, zoning, school, road, and history records and merge field evidence | append-only provenance and observations |
| `research/comparables.py` | comparable selection, reliability, medians, affordability, dislocation, land-value proxy | comparable observations and score inputs |
| `research/redevelopment.py` | zoning rule resolution, scenarios, products, costs, future opportunity cost | scenario records and evidence; no entitlement inference |
| `research/delta.py` | compare current and prior observations and classify changes | append-only watchlist events |
| `research/pipeline.py` | orchestrate one run and expose structured results | transactionally writes research storage |
| `research/scheduler.py` | CLI/cron-compatible weekly execution, dry-run, lock and failure summary | local run records and reports only |

The existing `normalization.py`, `scoring.py`, `state.py`, `storage.py`, and `report.py` remain the shared domain foundation. New engines may wrap or extend them, but they must not duplicate title, provenance, or score semantics.

## 4. Source policy and provenance

Every input source has a registry record:

- stable `source_registry_id`;
- source name and allowed `SourceType`;
- access method (`LOCAL_FILE`, `SAVED_SEARCH`, `MANUAL`, `AUTHORIZED_API`, or `HTTP_READ_ONLY`);
- authorization status;
- domain and reliability notes;
- last attempted and last successful run;
- error or skip reason.

Supported Phase 2 input forms:

1. existing local HTML search snapshots;
2. existing saved-search `.eml`, `.html`, and `.txt` files;
3. manual listing dictionaries/URLs;
4. JSON/JSONL/CSV local enrichment files;
5. existing official-feed stub shape;
6. injected authorized feed/API adapters.

Remote adapters are disabled unless all of the following are true:

- the source is enabled in configuration;
- `authorization_status` is `AUTHORIZED`;
- the adapter has a configured endpoint/client;
- the request policy allows it;
- the caller has not exceeded configured request limits.

No adapter may scrape around a restriction. A disabled, unauthorized, unavailable, or rate-limited source yields a `SourceRunResult` with a skip/error status and leaves affected fields `UNKNOWN`. It does not cause a favorable score or a false successful acquisition.

Field-level `SourceRecord` remains the audit unit. When two sources disagree on PID, legal description, lot, year, listing history, sold history, or other material values, `DataConflict` stores both values and source identifiers. Conflicting values are excluded from score-derived calculations until resolved.

## 5. Phase 2 — acquisition and enrichment

### 5.1 Acquisition contract

`acquire_universe(config, source_adapters, observed_at)` returns a structured result containing:

- all parsed `Listing`/raw property inputs;
- normalized `PropertyRecord` projections;
- source records and source-run results;
- parse failures/quarantine entries;
- Stage 1 candidates;
- acquisition counters.

Property identity is resolved through the Phase 1 rules: PID set first, then canonical address plus municipality. A listing ID or MLS number is not a property identity. Duplicate listing observations merge into one property projection while each raw input remains persisted.

### 5.2 Stage 1 scan

Stage 1 applies configuration, not a single absolute Greater Vancouver budget:

- named market bucket and neighbourhood;
- detached-first property-type filter;
- title gate before ranking;
- core and extended asking ranges from that market bucket;
- lot/frontage/depth/zoning/road filters;
- assessment, price-history, DOM, relist, and discount triggers;
- special-interest tags such as large lot, threshold advantage, TOA, assembly, character, fixer, and permanent defect.

The scan records why a record was included or excluded. A property outside a core range may remain in the extended/special-opportunity pool; the range never becomes a value judgment.

Stage 1 target counts of 100–300 are operational targets, not fabricated padding. With fewer local inputs, the report shows the actual count and source limitations.

### 5.3 Enrichment contract

Each enrichment provider accepts a stable property identity and returns:

```text
EnrichmentResult(
    property_id,
    field_updates,
    source_records,
    conflicts,
    warnings,
    provider_status,
)
```

Providers are field-specific:

- assessment: year, land, improvement, total, prior year;
- zoning/use: zoning, bylaw version, permitted/conditional uses, FSR, units, height, setbacks, coverage, parking, tree/character rules, approved use and permit status;
- school: official elementary/secondary catchments, programs, capacity, boundary risk, current score, five-year outlook;
- road/location: nearest major road, class, distance, fronting, buffer, stability, outdoor noise and indoor mitigation;
- history: listing cycles, price reductions, sold dates/prices, DOM and relist history;
- lot/geometry: dimensions, shape, lane, corner, slope, orientation, rear access and neighbourhood percentile.

The merge policy is conservative: only non-empty provider fields are eligible for update; provenance is appended; conflicts are recorded before scoring; and unsupported claims are left unknown.

## 6. Phase 3 — comparable engine

`ComparableRecord` is a typed record with:

- subject property ID and comparable identity/source ID;
- municipality/neighbourhood/property type;
- sold date and sold price;
- asking price when present;
- lot area, frontage, depth, house size, year/condition;
- zoning and road exposure;
- similarity score and exclusion reasons;
- source records, reliability status, and conflict flag.

Selection order:

1. same municipality and neighbourhood;
2. detached and like-for-like title where possible;
3. sold within 180 days;
4. lot area preferably within ±20%;
5. similar frontage/depth/shape/zoning/road exposure/condition;
6. expand to 365 days only when fewer than five reliable comparables remain.

The engine computes only from reliable, non-conflicting sold prices:

- `median_sold_price`;
- `median_lot_size`;
- `median_house_size`;
- `median_price_per_lot_sqft`;
- `median_assessment_ratio`;
- `expected_market_value`;
- `relative_affordability_index`;
- `price_dislocation_pct`.

Fewer than five reliable records produces `LOW_COMP_CONFIDENCE`; the report does not state a precise fair value. If no sold evidence exists, expected value remains `UNKNOWN`.

The engine keeps two auditable populations. `selected_comparables` are the
closest like-for-like rows used for detailed medians and expected value;
`neighbourhood_benchmark_comparables` are valid same-municipality/
neighbourhood context rows, including lot-size outliers, used for the local
benchmark and relative affordability index. The benchmark has its own count,
window, median, and confidence fields. Independent sold-price disagreements
are marked `SOLD_PRICE_CONFLICT` and excluded before either population is
summarized.

The land-value proxy is available only when current-use building value is supplied or derived from an explicit configured method:

```text
land_value_proxy = transaction_value - current_use_building_value
```

Land proxy comparisons require similar zoning, geometry, road exposure, and transaction window. It is labelled as a proxy, not an appraisal.
The configured building-value method and the resulting value are retained in
the subject record; asking price is never used as the subject transaction
value.

## 7. Phase 4 — redevelopment engine

The engine has a municipality/zoning rule registry. A rule can be sourced from official municipal evidence or remain an unverified configuration hypothesis. Only official evidence may set `legal_status=PERMITTED_BY_OFFICIAL_BYLAW`.

Every detached property receives:

- Keep Existing House;
- Duplex;
- Max Realistic Multiplex.

When evidence supports them, it also receives:

- Character House + Infill;
- TOA / Assembly.

For each scenario the engine stores:

- total and realistic buildable area;
- saleable area after configured efficiency;
- units, average size, likely bedrooms and buyer;
- front/rear and side-by-side duplex layouts;
- front-building noise-buffer hypothesis where relevant;
- product-market fit, execution complexity and assumptions;
- hard cost, soft cost, developer-specific cost and contingency;
- total project cost and estimated resale value when supplied;
- future opportunity cost and redevelopment uplift where complete.

Multiplex product options test 2, 3, 4, 5, and 6 units, bounded by official maximum units. The selected product maximizes a transparent product score using legality, family-oriented unit size, market fit, execution, and economics; it does not maximize unit count.

TOA is stored as long-term option value with tier, distance, theoretical height/FSR, current zoning, rezoning requirement, rental/below-market requirements, and assembly requirement. It never becomes automatic entitlement.

Future uplift uses redevelopment-date value:

```text
redevelopment_uplift = new_finished_market_value
                       - existing_property_market_value_at_redevelopment_date
                       - total_redevelopment_cost
```

The original purchase price is not substituted for future property value.

## 8. Phase 5 — weekly execution and state

The operational interface is CLI/cron compatible, not a resident daemon. Commands are:

- `research-acquire`: run source adapters and Stage 1 scan;
- `research-enrich`: enrich selected/current properties;
- `research-comps`: refresh comparable calculations;
- `research-redevelop`: refresh scenario/economic outputs;
- `research-run-weekly`: run the complete local/authorized pipeline;
- `research-conflicts`: list open conflict queue;
- existing `research-validate-phase1`: remain as the local baseline validator.

`research-run-weekly` supports:

- `--dry-run` to parse and score without mutating the research database;
- source filters;
- explicit observed-at time for deterministic replay;
- partial-source operation;
- structured run status (`SUCCESS`, `PARTIAL`, `FAILED`, `SKIPPED`);
- a local lock preventing overlapping runs;
- error summaries in `weekly_runs` and the report.

Each run compares the new projection with the prior projection and emits append-only events for:

- first seen;
- price change/reduction;
- listing status change;
- relist or DOM change;
- sold/removed;
- assessment change;
- zoning/approved-use change;
- score/confidence/state change;
- conflict opened/resolved;
- source failure or skip.

The report keeps sold/removed properties as historical evidence but excludes them from site-visit recommendations. `NEED_VERIFICATION` and all special/hard-veto states remain explicit.

## 9. Error and unknown handling

- malformed source row: quarantine row, record parse warning, continue other rows;
- unavailable local file: source run is `SKIPPED`, fields remain unknown;
- unauthorized/disabled remote source: no request, source run is `SKIPPED_DISABLED`;
- HTTP 403/429/CAPTCHA/challenge/robots denial: stop that source and record the reason;
- enrichment mismatch: persist a conflict and exclude conflicting fields from scoring;
- insufficient comps: `LOW_COMP_CONFIDENCE`, no false precision;
- missing official zoning: scenario legal status remains unknown;
- scheduler overlap: skip with `LOCKED` status rather than duplicate a run;
- partial pipeline failure: preserve successful observations, mark the run `PARTIAL`, expose the failed stage in the report.

## 10. Storage evolution

The existing research SQLite is extended, not replaced. Current projection tables remain queryable; append-only tables receive:

- `source_runs`;
- `enrichment_observations` or equivalent provider payload records;
- typed comparable rows;
- redevelopment assumptions/economic snapshots;
- weekly run metadata and stage status;
- watchlist/delta events;
- conflict queue state.

Every append-only row has a stable identity where deduplication is semantically safe, an observation timestamp, source registry ID, parser/engine version, and JSON payload. Re-running a source does not delete earlier evidence or overwrite score/scenario snapshots. Current projections may update.

## 11. Report contract

The Phase 5 report continues to write:

- `reports/weekly/YYYY-MM-DD.md`;
- `reports/latest.md`;
- a run-specific Phase 2–5 report when requested.

Required sections remain:

1. Executive Summary;
2. Market Changes;
3. Top New Candidates;
4. Major Price Reductions;
5. Watchlist Changes;
6. Sold / Removed;
7. Data Conflicts;
8. Recommended Site Visits.

The report additionally exposes source-run status, Stage 1/Stage 2 counts, comparable confidence, redevelopment product comparison, source limitations, and the open conflict queue. Candidate cards retain the Phase 1 audit format and add comparable rows, product economics, and delta reasons.

## 12. Test strategy

Unit tests cover:

- source authorization and disabled-remote policy;
- local source parsing and malformed-row quarantine;
- Stage 1 market bucket and core/extended range behavior;
- title gate and property identity across listing cycles;
- enrichment merge, unknowns, provenance, and conflicts;
- comparable 180/365-day selection, ±20% lot preference, minimum reliability, medians, affordability, dislocation, and land proxy;
- official zoning entitlement gate and 2–6 product options;
- future opportunity cost and best economic product;
- delta classification and no false zero reduction;
- scheduler dry-run, lock, partial failure, and source filtering;
- report sections and conflict queue;
- preservation of all existing purchase/rental tests.

Integration fixtures cover one local full pipeline run, one disabled source, one authorized mock adapter, one assessment/zoning/school/road enrichment bundle, one five-comp subject, one insufficient-comp subject, one zoning-permitted multiplex, one unknown-entitlement property, one conflict, one price/status change, and two repeated weekly runs.

## 13. Acceptance criteria

Phases 2–5 are accepted only when:

- all seven new operational commands execute against local fixtures;
- the complete pipeline runs without external requests under default config;
- an authorized mock source can be enabled through configuration and produces source-run/provenance records;
- Stage 1 records market bucket, range, title gate, triggers, and candidate reason;
- enrichment retains unknowns and field-level sources;
- comparable calculations use sold prices, window fallback, reliability gates, and conflict exclusion;
- redevelopment scenarios distinguish legal evidence from hypotheses and choose products by fit/economics rather than units alone;
- future opportunity cost uses redevelopment-date value;
- repeated runs preserve observations, scenarios, snapshots, provenance, and events;
- weekly delta and conflict queue are visible in Markdown and SQLite;
- no source bypasses restrictions or runs when disabled;
- legacy purchase/rental tests remain green;
- full test, compile, CLI, and local fixture verification outputs are recorded;
- the final audit explicitly lists any real-world source coverage still requiring credentials or human verification.

## 14. Explicit non-goals

This implementation does not:

- bypass realtor/MLS/VOW authentication, CAPTCHA, robots, paywalls, or rate limits;
- claim official zoning, title, assessment, school, or sale facts without evidence;
- turn TOA into automatic entitlement;
- create a web dashboard;
- send notifications automatically;
- replace appraisal, legal review, survey, geotechnical review, tax advice, or municipal confirmation.
