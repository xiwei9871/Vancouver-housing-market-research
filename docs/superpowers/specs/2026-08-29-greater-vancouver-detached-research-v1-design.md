# Greater Vancouver Detached House Research System V1 Design

**Date:** 2026-08-29

**Status:** Design approved in conversation; awaiting written-spec review.

**Scope:** Phase 1 read-only research baseline only.

## 1. Goal

Extend the existing northshore_property_watchdog repository into a repeatable, auditable research core for Greater Vancouver detached houses. The system must analyze each property as:

Current House Use Value
+
Land Value
+
Redevelopment Option Value
+
Resale Buyer Pool
+
Family Living Value
-
Permanent Defects
-
Carrying Cost
-
Execution Risk

Phase 1 must provide a typed schema, configuration, provenance, scoring baseline, confidence model, candidate state machine, append-only watchlist storage, a weekly report template, and end-to-end validation using a small number of traceable local cases. It must not start large-scale acquisition.

## 2. Confirmed constraints

- The canonical repository is /Users/xiwei/northshore_property_watchdog.
- Existing rental functionality remains separate and unchanged in behavior.
- Freehold detached houses are the primary target.
- Freehold strata, duplex, half duplex, and ground-oriented housing are supplemental cases.
- Leasehold, fractional ownership, partial interest, and similar title structures are not normal candidates.
- Region and neighbourhood are first-class market dimensions; there is no single Greater Vancouver price filter.
- Listing price is never treated as sale price.
- Assessment is a signal, not a valuation conclusion.
- Unknown values remain unknown and are not guessed.
- Official and source-traceable evidence is preferred.
- Historical observations, scores, and watchlist events are append-only.
- Phase 1 uses local fixtures, saved snapshots, manual records, and existing repository material only.
- Phase 2 acquisition, Phase 3 comparable automation, Phase 4 redevelopment automation, and Phase 5 scheduling are explicitly out of scope.

## 3. Existing repository fit

The repository already contains:

- Python packaging and pytest configuration;
- a Listing model and a legacy purchase scorer;
- collector interfaces for local feeds, saved emails, snapshots, manual links, and future authorized feeds;
- SQLite storage;
- Markdown reporting;
- risk and keyword enrichers;
- an independent rental subsystem.

The new implementation will add a bounded purchase research core under src/northshore_watchdog/research/. The legacy purchase modules remain usable for compatibility and are adapted into the new model rather than being given all V1 semantics. The rental modules are not imported by the new research core.

The target directory currently has no Git metadata. The design document can be written locally, but it cannot be committed unless version control is added by the user or an existing repository is restored. The implementation will not initialize Git implicitly.

## 4. Architecture

The system has five logical stages:

~~~text
raw local record or snapshot
        |
        v
normalization and field provenance
        |
        +-- title and approved-use gate
        +-- derived assessment and lot metrics
        +-- conflict detection
        +-- comparable and scenario inputs
        |
        v
deterministic V1 score engine
        |
        +-- hard veto and special situation
        +-- LIVE / LAND / INVESTMENT indexes
        +-- total score and explanation
        +-- confidence and candidate state
        |
        v
append-only SQLite observations
        |
        v
weekly Markdown report and watchlist delta
~~~

The core modules are:

| Module | Responsibility |
|---|---|
| research/models.py | Typed property, title, assessment, location, scenario, score, and watchlist models |
| research/provenance.py | Field-level source records, source registry, evidence status, and conflicts |
| research/normalization.py | Units, enums, address/PID identity, derived metrics, and duplicate detection |
| research/scoring.py | Eight score dimensions, three independent indexes, opportunity tags, and explanations |
| research/state.py | Hard veto, special situation, confidence gate, and A/B/C/D/NEED_VERIFICATION state |
| research/storage.py | SQLite schema, current projection, append-only observations, score snapshots, and watchlist events |
| research/report.py | Weekly report, candidate cards, source display, conflict display, and investment thesis |
| research/adapters.py | Conversion from legacy Listing and local case inputs |
| research/validation.py | Deterministic Phase 1 end-to-end validation runner |

Configuration is stored in config/research.yaml. Research inputs are stored under data/research/raw, data/research/normalized, and data/research/history. Reports are stored under reports/weekly, reports/phase1, and reports/latest.md.

## 5. Data flow and identity

Every input is treated as an observation rather than an immediate current truth:

1. A local record is assigned an observation_id, source identity, retrieval timestamp, and raw payload.
2. Normalization produces a PropertyRecord projection and field-level provenance.
3. Identity resolution uses PID and legal description first, then canonical civic address and municipality, then listing identifiers.
4. A duplicate resolver records all alternate source identifiers rather than silently creating a second property.
5. Conflicts are persisted before scoring.
6. Only non-conflicting normalized evidence contributes to derived metrics and scores.
7. The current property projection is updated, but the input observation and all score/watchlist events remain immutable.

The canonical property_id is stable across listing cycles. A listing_id or MLS number identifies a market listing, not a property. A property may therefore have multiple listing observations and multiple historical listing cycles.

## 6. Domain schema

### 6.1 PropertyRecord

PropertyRecord is the current normalized projection and supports both typed access and a flat export.

Identity and title:

- property_id
- address
- municipality
- neighbourhood
- property_type
- secondary_civic_addresses
- pid
- legal_description
- number_of_pids
- strata_lot_identifiers
- ownership_type
- approved_use
- single_title_multi_address
- independently_tradable
- title_status

Listing and transaction history:

- listing_id
- mls_number
- source_url
- listing_status
- asking
- original_asking
- listing_date
- dom
- cumulative_dom
- relist_count
- terminated_history
- previous_listing_prices
- previous_sale_dates
- previous_sale_prices
- price_reduction_amount
- price_reduction_pct
- transaction_history_conflict
- previous_listing_sources
- sold_history_sources

Assessment:

- assessment_year
- assessment_total
- assessment_land
- assessment_improvement
- previous_year_total
- previous_year_land
- land_ratio
- asking_vs_total_assessment
- asking_vs_land_assessment
- assessment_labels

Lot:

- lot_area_sqft
- lot_area_m2
- frontage_ft
- depth_ft
- shape
- corner_lot
- lane_access
- slope
- orientation
- rear_access
- lot_size_percentile_in_neighbourhood
- lot_geometry_score

House and current use:

- year_built
- effective_year
- substantial_rebuild
- finished_sqft
- unfinished_sqft
- bedrooms
- bathrooms
- basement
- basement_ceiling
- kitchens
- separate_entrance
- foundation
- roof
- heating
- electrical
- plumbing
- sewer
- drainage
- current_house_utility_score
- renovation_tier
- renovation_estimate
- permanent_defects
- fixable_defects

Zoning and buildability:

- zoning
- bylaw_version
- permitted_uses
- conditional_uses
- minimum_site_area
- minimum_frontage
- maximum_fsr
- maximum_units
- maximum_height
- setbacks
- lot_coverage
- parking
- tree_constraints
- character_rules
- theoretical_buildable_sqft
- realistic_buildable_sqft
- buildability_score
- zoning_threshold_advantages

Road and noise:

- nearest_major_road
- distance_to_major_road_m
- property_fronts_major_road
- road_class
- road_buffer_type
- road_buffer_stability
- buffer_at_risk
- indoor_noise_mitigation_potential
- outdoor_noise_exposure
- noise_risk

Planning, heritage, and assembly:

- toa_status
- toa_tier
- theoretical_height
- theoretical_fsr
- current_zoning
- rezoning_requirement
- rental_requirement
- below_market_requirement
- assembly_score
- neighbouring_lot_context
- recent_assembly_evidence
- rezoning_application_evidence
- heritage_register
- character_status
- retention_incentives
- demolition_rules
- infill_options

Schools and family location:

- official_elementary_catchment
- official_secondary_catchment
- elementary_distance
- secondary_distance
- kindergarten_capacity
- overflow
- school_plan
- enrolment
- literacy
- numeracy
- special_programs
- capital_projects
- seismic_upgrade
- nearby_private_schools
- current_school_score
- school_future_score
- family_location_score

Economics and decisions:

- duplex_area
- max_duplex_area
- estimated_front_unit_area
- estimated_rear_unit_area
- multiplex_area
- max_realistic_multiplex_units
- best_economic_product
- median_sold_price
- median_lot_size
- median_house_size
- median_price_per_lot_sqft
- median_assessment_ratio
- comp_window_days
- comp_count
- comp_confidence
- expected_market_value
- price_dislocation_pct
- relative_affordability_index
- land_value_proxy
- adjusted_land_price_per_sqft
- land_discount_or_premium_pct
- annual_municipal_carrying_cost
- tax
- utilities
- live_score
- land_score
- investment_score
- total_score
- data_confidence_score
- candidate_status
- opportunity_tags
- last_updated

### 6.2 Enumerations and title gate

ownership_type supports:

- FREEHOLD_NON_STRATA
- FREEHOLD_STRATA
- LEASEHOLD
- PREPAID_LEASE
- NON_PREPAID_LEASE
- BARE_LAND_STRATA
- FRACTIONAL_OWNERSHIP
- UNDIVIDED_INTEREST
- PARTIAL_INTEREST
- COURT_SALE
- FORECLOSURE_COURT_ORDERED_SALE
- UNKNOWN

approved_use supports:

- SINGLE_DETACHED
- SINGLE_DETACHED_SECONDARY_SUITE
- TWO_FAMILY_DWELLING
- MULTIPLE_DWELLING
- LANEWAY_HOUSE
- STRATA
- NON_CONFORMING_USE
- UNKNOWN

Two civic addresses do not imply two titles. If two addresses map to one PID, the normalized record sets single_title_multi_address=true and independently_tradable=false unless official evidence proves otherwise. The record must then preserve current approved use as UNKNOWN until official evidence establishes single detached with suite, two-family dwelling, or another use.

Different PIDs or independently identified strata lots are required before independent tradability is asserted. Missing title evidence is not treated as freehold non-strata.

### 6.3 Observation and provenance objects

PropertyObservation contains:

- observation_id
- property_id when identity is resolved
- observed_at
- source_registry_id
- raw_payload
- parser_version
- normalized_record
- normalization_warnings

FieldProvenance contains:

- property_id
- field_name
- source_url
- source_type
- retrieved_at
- raw_value
- normalized_value
- source_version
- evidence_status
- notes

source_type is restricted to OFFICIAL_GOV, BC_ASSESSMENT, MLS, VOW, AGGREGATOR, and MANUAL.

Source registry records domain or organization, access method, authorization status, reliability notes, and the date the source definition was last reviewed. A source URL is never used as evidence that a value is current unless its retrieval timestamp is stored.

### 6.4 Conflicts

DataConflict contains:

- conflict_id
- property_id
- field_name
- conflict_type
- source_values
- conflicting_sources
- detected_at
- resolution_status
- resolution_note

Conflicts include PID mismatch, legal description mismatch, duplicate history, lot-size conflict, year-built conflict, duplicate MLS history across addresses, and inconsistent listing cycles. Affected fields are excluded from scoring until resolved. A property-level data_conflict flag is true while any material conflict remains open.

## 7. Derived metrics

Assessment calculations:

- land_ratio = assessment_land / assessment_total
- asking_vs_total_assessment = asking / assessment_total - 1
- asking_vs_land_assessment = asking / assessment_land - 1

Labels:

- LAND_DOMINANT when land_ratio >= 0.85
- HIGHLY_LAND_DOMINANT when land_ratio >= 0.95
- ASK_BELOW_ASSESSMENT when asking_vs_total_assessment < 0
- ASK_10PCT_BELOW_ASSESSMENT when asking_vs_total_assessment <= -0.10
- ASK_BELOW_LAND_ASSESSMENT when asking_vs_land_assessment < 0

Assessment labels never produce a cheapness conclusion without comparable and defect analysis.

Lot geometry uses lot area, frontage, depth, shape, lane, corner, and slope. Vancouver proper reference bands are configuration values only. North Vancouver and West Vancouver use neighbourhood percentiles and municipality-specific geometry rubrics rather than Vancouver cutoffs.

Frontage threshold detection compares normalized frontage to official zoning minimum frontage using a configured tolerance. A frontage that is just above the official threshold receives ZONING_THRESHOLD_ADVANTAGE and JUST_ABOVE_ZONING_THRESHOLD evidence; the tag is not generated from listing copy alone.

Road exposure combines road class and distance:

- Level 5: directly fronts major road
- Level 4: less than 50m
- Level 3: 50–100m
- Level 2: 100–200m
- Level 1: over 200m

The buffer type and stability are independent fields. If a buffer may disappear through widening or redevelopment, BUFFER_AT_RISK is true. Indoor mitigation and outdoor exposure are scored separately, with outdoor exposure carrying the larger family and resale impact.

Land value proxy is transaction value less estimated current-use building value. It is only compared with properties of similar zoning, geometry, and road exposure. It is explicitly marked as a proxy and does not replace an appraisal.

## 8. Comparable baseline

The comparable engine is a Phase 1 deterministic baseline, not a large-scale automated acquisition system.

Selection order:

1. Same neighbourhood and municipality.
2. Detached freehold where possible.
3. Sold within 180 days.
4. Lot area preferably within plus or minus 20 percent.
5. Similar frontage, depth, zoning, road exposure, and house condition.
6. Expand to 365 days if fewer than five reliable records remain.

The normalized comparable set stores:

- comp_window_days
- comparable_property_id or source identifier
- sold_date
- sold_price
- asking_price when available
- lot area and house size
- zoning and road exposure
- similarity notes
- source records
- reliability and conflict flags

The baseline computes median_sold_price, median_lot_size, median_house_size, median_price_per_lot_sqft, median_assessment_ratio, and expected_market_value only when the set is sufficiently reliable. If fewer than five reliable comparable records remain, comp_confidence is LOW_COMP_CONFIDENCE and the report avoids false precision.

relative_affordability_index is asking divided by the neighbourhood comparable median. price_dislocation_pct is asking divided by expected_market_value minus 1. These measures are more important than absolute assessment discount for investment ranking.

## 9. Redevelopment scenarios

Each detached case has at least these scenario records:

- KEEP_EXISTING_HOUSE
- DUPLEX
- MAX_REALISTIC_MULTIPLEX

Where supported by official evidence, the runner also evaluates:

- CHARACTER_HOUSE_INFILL
- TOA_OR_ASSEMBLY

Each scenario contains:

- legal_status
- zoning_basis
- bylaw_version
- unit_count
- total_buildable_sqft
- realistic_buildable_sqft
- estimated_saleable_sqft
- average_unit_size
- likely_bedrooms
- likely_buyer
- estimated_resale_price
- hard_cost
- soft_cost
- developer_specific_cost
- total_project_cost
- execution_complexity
- product_market_fit
- evidence and assumptions

Max legal unit count is not automatically the selected product. The engine compares two-, three-, four-, five-, and six-unit products when legal and chooses Best Economic Product using saleable area, family-oriented unit size, buyer pool, cost, and execution risk.

Duplex baseline supports front/rear and side-by-side layout hypotheses. For road-exposed lots it can model a front building as a potential rear-unit noise buffer, but this remains an explicit scenario assumption rather than a guaranteed design outcome.

Hard cost, soft cost, developer-specific cost, and contingency are separate input fields. A dynamic hard-cost range is configuration data and is never mislabeled as an all-in developer feasibility number.

TOA is stored as long-term option value. Theoretical height or FSR is not automatic entitlement. The record must state current zoning, rezoning requirement, rental or below-market requirements, and assembly requirement.

## 10. Scoring model

All points are configuration-backed and the default total is 100.

| Dimension | Weight | Subcomponents |
|---|---:|---|
| Land / Lot / Title | 20 | ownership 3; lot area 4; frontage/depth 4; shape/lane 3; land scarcity 3; buildability 3 |
| Price / Safety Margin | 15 | asking vs comps 6; asking vs assessment 3; land value discount 3; DOM/seller pressure 1; capital efficiency 2 |
| Current House Utility | 15 | finished space 3; bedroom/bath usability 2; layout 2; basement 2; systems/structure 2; renovation efficiency 2; effective age 1; suite flexibility 1 |
| Family Location / Noise | 10 | quietness 2; outdoor environment 2; parks/daily life 2; transit/walkability 2; family street quality 2 |
| School | 10 | elementary current 2; secondary current 1.5; programs 1.5; capacity 1; improvement trend 1; community 1; five-year outlook 1; boundary risk 1 |
| Redevelopment / Option Value | 20 | current entitlement 4; duplex 3; multiplex 3; buildable area 3; product-market fit 3; TOA/assembly 2; execution difficulty 2 |
| Resale / Affordability | 5 | future buyer pool 2; affordability 2; liquidity 1 |
| Carrying Cost / Risk | 5 | tax/utilities 1; maintenance 1; title/legal 1; permanent defects 1; data quality 1 |

Each subcomponent returns points, maximum points, evidence status, and explanation. A missing value is represented as UNKNOWN with a configurable conservative provisional value; the default is 50 percent of the subcomponent weight. This prevents missing data from becoming an unearned positive while keeping the numeric score usable for triage. The missing-field list and confidence gate remain visible in every report.

Material conflicts cause the affected subcomponent to use its conservative unknown treatment rather than either conflicting value. Direct hard-veto properties are not ranked as normal candidates.

The three independent indexes are:

- live_score = 45 percent current house utility + 30 percent family location + 25 percent school;
- land_score = 50 percent Land / Lot / Title + 50 percent Redevelopment / Option Value;
- investment_score = 30 percent Price / Safety Margin + 35 percent land_score + 15 percent Resale / Affordability + 15 percent Carrying / Risk + 5 percent Current House Utility.

The indexes and total score retain one decimal internally and display rounded integers. The score snapshot stores scoring_version and config hash so a future rubric change does not rewrite historical results.

Opportunity tags include LAND_VALUE, LIVE_NOW_DEVELOP_LATER, DUPLEX_OPTION, MULTIPLEX_OPTION, TOA_OPTION, ASSEMBLY_OPTION, CHARACTER_OPTION, FIXER, PRICE_DISLOCATION, LARGE_LOT, STRONG_FRONTAGE, ZONING_THRESHOLD_ADVANTAGE, PERMANENT_DEFECT, NOISE_RISK, TITLE_RISK, DATA_CONFLICT, ASSESSMENT_DISCOUNT, and LAND_ASSESSMENT_DISCOUNT.

## 11. Data confidence

Data confidence is an evidence score independent from investment score:

| Evidence | Points |
|---|---:|
| Official PID confirmed | 15 |
| Ownership confirmed | 15 |
| Official assessment | 10 |
| Lot dimensions confirmed | 10 |
| Official zoning | 10 |
| Approved use confirmed | 10 |
| Listing history confirmed | 10 |
| Sold history cross-checked | 10 |
| Official school catchment | 5 |
| Road/location verified | 5 |

Unresolved conflicts contribute zero for the affected evidence item. Bands are High for 85–100, Medium for 70–84, and Needs Verification for below 70.

Confidence is never allowed to hide a data gap. The report displays the score, evidence items, missing fields, conflicts, and the next verification action.

## 12. Candidate state machine

The state resolver applies this order:

1. Direct hard veto becomes REJECT.
2. A deliberate special case becomes SPECIAL_SITUATION.
3. A provisional high-potential property (total score at least 60) with confidence below 70, or any property with a score of at least 80 and confidence below 80, becomes NEED_VERIFICATION.
4. Otherwise total score below 60 becomes D_REJECT.
5. Total score 60–69 becomes C_OPPORTUNISTIC.
6. Total score 70–79 becomes B_WATCH.
7. Total score at least 80 and confidence at least 80 becomes A_STRONG_CANDIDATE.

Direct hard-veto examples are non-prepaid leasehold, severe contamination, severe foundation problem, impossible vacant possession, legally blocked redevelopment, and a severe highway or industrial defect without sufficient price compensation.

Special-situation examples are partial interest, undivided interest, court sale, foreclosure, unclear title requiring legal review, serious litigation, serious unresolved strata issue, and material data conflict that may be resolvable. The exact result is persisted with reason codes.

The resolver never changes UNKNOWN to a favorable value. An A candidate must satisfy both score and confidence. A high score with confidence below the A threshold is visible as NEED_VERIFICATION rather than being promoted.

## 13. Storage

The research SQLite database has these tables:

- properties: current normalized projection;
- property_observations: immutable raw and normalized observations;
- field_provenance: field-level evidence;
- data_conflicts: unresolved and resolved conflicts;
- comparable_records: sold comparable observations and similarity notes;
- redevelopment_scenarios: scenario inputs and outputs;
- score_snapshots: versioned total and independent scores;
- watchlist_events: append-only membership and status changes;
- source_registry: source identity, type, authorization, and reliability;
- weekly_runs: run metadata, counts, and errors.

The current properties projection may be updated. All other observation, source, score, and event records are append-only. A weekly run stores its run_id, configuration hash, scoring version, input count, output count, and generated report path.

The watchlist event vocabulary includes ADDED, PRICE_CHANGED, PRICE_REDUCED, RELISTED, SOLD, REMOVED, STATUS_CHANGED, SCORE_CHANGED, CONFLICT_OPENED, CONFLICT_RESOLVED, and VERIFICATION_REQUESTED.

## 14. Configuration

config/research.yaml contains:

- municipality and neighbourhood market buckets;
- core and extended acquisition ranges for Vancouver West, Vancouver East, City of North Vancouver, District of North Vancouver, West Vancouver, and Burnaby North;
- property-type and ownership gates;
- Vancouver geometry reference bands;
- municipality-specific geometry and percentile settings;
- zoning threshold tolerances;
- road classes and distance levels;
- permanent and fixable defect rules;
- renovation tiers and ranges;
- comparable windows and minimum reliability;
- development cost inputs;
- TOA and assembly flags;
- school and family scoring rubrics;
- score weights and unknown treatment;
- confidence thresholds;
- candidate-state thresholds;
- SQLite path and report paths.

Search ranges are never used as value judgments. Any future municipality or neighbourhood must be added as a named market bucket before it can use an acquisition range.

## 15. Read-only Phase 1 validation

The validation runner reads a case manifest and produces a normalized property, provenance summary, score snapshot, state decision, and candidate card without external acquisition or remote writes. It may write local Phase 1 SQLite and Markdown artifacts so the append-only behavior can be tested.

The initial case manifest will prefer traceable research material already present in the repository:

- 1379 Haywood Avenue: West Vancouver sold and assessment-discount case;
- 1531 Inglewood Avenue: older Westside lot and current-use review case;
- 4670 Willow Creek Road: large lot with permanent environmental-risk review;
- 4110 Burkeridge Place: large-lot lifestyle and location-discount case;
- 532 W 23 Street: North Vancouver older detached case;
- 2553 Bendale Road: as-is condition-discount case;
- 1380 Coleman Street: older North Vancouver large-lot case;
- 1006 Hendecourt Road: target-shape sold comparable;
- 1494 Ross Road: bare-land strata boundary case;
- one traceable leasehold case if a non-synthetic source record is available.

The existing 1000 Sample Ambleside Avenue, 321 Queensbury Avenue, and 88 Leasehold Lane records remain test fixtures unless their source provenance is upgraded. They do not count as real cases solely because they have plausible fields.

The repository currently has no verified local case for each of Vancouver East assessment discount, Knight major-road exposure, TOA large lot, partial-interest or court sale, and small freehold-strata coach house. These are recorded as coverage gaps. Boundary fixtures may validate the rules, but the acceptance report must state that real-market coverage is not yet complete.

Validation assertions cover:

- multi-address versus multi-title;
- title and ownership classification;
- official versus unsupported evidence;
- assessment formulas and labels;
- lot geometry and threshold advantage;
- road exposure and outdoor/indoor noise separation;
- hard veto and special situation;
- data confidence gating;
- duplicate property identity;
- PID, listing-history, and sold-history conflicts;
- history preservation across two observations;
- weekly delta events;
- report source and explanation display.

## 16. Weekly report

The report writer produces:

- reports/weekly/YYYY-MM-DD.md;
- reports/latest.md;
- reports/phase1/validation-YYYY-MM-DD.md.

The weekly report contains:

1. Executive Summary;
2. Market Changes;
3. Top New Candidates;
4. Major Price Reductions;
5. Watchlist Changes;
6. Sold / Removed;
7. Data Conflicts;
8. Recommended Site Visits.

Executive Summary includes properties_scanned, stage1_candidates, deep_research_count, A_candidates, B_candidates, largest_price_reduction, largest_assessment_discount, largest_land_assessment_discount, and largest_lot_opportunity, followed by the three to five most important cases.

Each candidate card includes address, ask, neighbourhood, ownership, PID, legal/use status, lot, house, assessment, listing history, road/noise, school, zoning, approved use, duplex and multiplex scenario summaries, TOA, assembly, character, renovation estimate, current house utility, permanent and fixable defects, comparable value, price dislocation, LIVE SCORE, LAND SCORE, INVESTMENT SCORE, TOTAL SCORE, data confidence, recommendation, and a two-to-four-sentence investment thesis.

The thesis must directly explain why the property is cheap, whether the discount compensates for the permanent defect, what is valuable about the land, whether the house supports five to ten years of living, and what future product is most realistic. Generic statements such as “large lot and convenient” are not sufficient.

When no acquisition feed exists, market-change fields are NOT_AVAILABLE or UNKNOWN and the report is marked run_type=phase1_validation. Static case data is not presented as current weekly market activity.

## 17. Error handling and audit rules

- Malformed records are quarantined with parser and normalization errors.
- Missing fields remain UNKNOWN.
- A source failure does not create a favorable fallback value.
- A conflict is recorded before scoring and remains visible until resolved.
- A source with insufficient authorization or reliability can be stored as context but cannot satisfy an official evidence requirement.
- Duplicate listing records are merged by stable property identity and source identifiers; the merge decision is logged.
- A failed report section states the failure and affected records rather than omitting evidence silently.
- Any derived value stores its calculation name, input fields, calculation version, and timestamp.
- Scores are reproducible from the observation, configuration hash, and scoring version.

## 18. Testing strategy

Unit tests cover:

- enum and ownership classification;
- multi-address/title distinction;
- assessment calculation;
- lot geometry;
- zoning threshold detection;
- road exposure;
- score component and index calculations;
- hard veto;
- data confidence;
- duplicate resolution;
- PID and history conflict detection.

Storage tests cover:

- append-only observations;
- score snapshot versioning;
- watchlist event history;
- weekly delta generation.

Report tests cover:

- all required sections;
- candidate-card fields;
- UNKNOWN and conflict rendering;
- investment thesis presence;
- exclusion and special-situation presentation.

End-to-end validation runs the case manifest and asserts expected category behavior. It does not assert a specific investment recommendation for an unverified case; it asserts that the evidence, score, confidence, and state are consistent with the available data.

## 19. Phase 1 acceptance

Phase 1 is accepted only when:

- schema, config, provenance, score, state, watchlist, and report modules run together;
- all eight score dimensions and three independent indexes are represented;
- unknown and conflict handling is visible and deterministic;
- at least five traceable real research cases complete end to end;
- required boundary rules have automated tests;
- two observations of one property preserve both historical states;
- a generated report contains the eight required sections and candidate cards;
- current data-source limitations and category coverage gaps are listed;
- no large-scale crawler or scheduler is introduced;
- the repository remains compatible with the existing rental subsystem.

Phase 1 does not claim that zoning entitlement, title, assessment, school, sale history, or redevelopment economics are automatically reliable for all Greater Vancouver properties. Those claims require the official or authorized sources planned for later phases.

## 20. Explicit next phases, not included here

After written acceptance of Phase 1:

- Phase 2: authorized listing acquisition, assessment lookup, official zoning, school, road analysis, historical state, and weekly delta;
- Phase 3: dynamic neighbourhood comparable engine, relative affordability, price dislocation, and land-value proxy;
- Phase 4: zoning-rule redevelopment engine, duplex/multiplex models, TOA, product-market fit, and redevelopment economics;
- Phase 5: scheduler, watchlist operations, delta reports, top-candidate reports, and conflict queue.

No Phase 2 implementation begins as part of this design.
