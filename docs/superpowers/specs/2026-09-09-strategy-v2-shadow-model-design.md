# Strategy V2 Shadow Model Design

**Date:** 2026-09-09  
**Status:** Approved for implementation  
**Scope:** First architecture rebuild for the saved 166-property Vancouver portfolio

## Goal

Build a parallel, ledger-first research model for all 166 saved listings. V1 remains an immutable legacy reference. V2 first classifies assets, applies non-compensatory gates, and produces holding, location, development-scenario, and acquisition ledgers. V2 does not publish a final leaderboard in this phase.

## Non-goals

- No changes to V1 configuration, formulas, reports, or historical outputs.
- No new GIS composite model.
- No black-box machine-learning model.
- No claim that preliminary RALV values are market-validated when required inputs are unknown.
- No final Current Use, Dual, or Redevelopment ranking until the ledgers are reviewed and the human-label protocol is complete.

## Frozen invariants

1. The input is the existing local official-enrichment artifact containing the 166 saved listings.
2. Every V2 row keeps a pointer and SHA-256 hash for the V1 analysis artifact used as `legacy_reference`.
3. Missing data remains `UNKNOWN`; it is not converted to zero and does not silently create a negative score.
4. Gates are evaluated before track comparison. A gate result is `PASS`, `REVIEW`, or `FAIL` and is not a weighted score.
5. A property can have one primary track and zero or more secondary tracks.
6. Development potential is separate from development economics. `development_potential` may be numeric while `base_ralv`, `conditional_ralv`, and `upside_ralv` remain `UNKNOWN`.
7. Current-use maintenance is a dollar ledger: `CHB = asking + survival_capex`. It is not a hard ceiling on a composite score.
8. Location is represented by external provider fields, school fields, area-baseline prior, and parcel-specific facts. V2 does not invent another walkability, noise, or amenity score.
9. Road exposure is applied once in the relevant value ledger and is also retained as an explanatory fact. It cannot be both a full value adjustment and a second full penalty for the same outcome.
10. TOA, assembly, rezoning, and unresolved access are never included in Base RALV unless the official rule and site condition are confirmed.

## Architecture

```text
saved official-enrichment artifact
        |
        +--> V1 legacy reference (read-only hash/pointer)
        |
        +--> asset classification + hard gates
                |
                +--> holding ledger
                +--> location ledger
                +--> development scenarios
                +--> acquisition ledger
                                |
                                +--> V2 audit table (no final ranking)
                                +--> calibration/blind-set manifests
```

The implementation is a focused module under `src/northshore_watchdog/research/strategy_v2_shadow.py`, reusing the existing `DiscoveryCard`, official enrichment records, zoning rule registry, road profile, location-provider snapshots, and report/path conventions. V2 output is written under a new date-stamped `strategy-v2-shadow` directory and never overwrites `portfolio-analysis-*` or the existing property reports.

## Asset classification

Classification is inferred from property structure and evidence, not municipality or neighbourhood alone.

Supported primary/secondary tracks:

- `HOME_REBUILD`: house can plausibly support the intended 5–10 year holding period and has a credible future rebuild path.
- `LAND_DEVELOPMENT`: current house utility is weak or secondary, but entitlement, geometry, access, and a plausible product path are strong enough for development research.
- `PREMIUM_SMALL_LOT`: below the standard lot but in a location/product market where a smaller site may work; must be evaluated with its local product path.
- `INCOME_HOLD`: existing suite/duplex/rental utility is materially relevant and is not automatically treated as family-house utility.
- `SPECIAL_SITUATION`: court sale, partial interest, title defect, severe condition, or another non-standard transaction requiring a separate review.
- `UNCLASSIFIED_REVIEW`: evidence is insufficient to assign a normal track.

The classifier records `classification_basis`, `classification_flags`, and `classification_confidence`. A large lot alone cannot create `LAND_DEVELOPMENT`; a Westside address alone cannot create `HOME_REBUILD`.

## Gates

The gate layer evaluates:

- title/ownership and partial-interest signals;
- court/foreclosure and litigation signals;
- detached/freehold scope;
- severe condition or vacant-possession blockers;
- official zoning or geometry blockers;
- redevelopment access and entitlement uncertainty;
- data conflicts requiring human review.

Gate output is recorded as structured findings. Failed normal-track rows remain in the artifact under their special track and are not discarded.

## Holding ledger

Each row contains:

```text
house_utility_band
holding_condition_band
survival_capex_low
survival_capex_base
survival_capex_high
cash_holding_basis_low/base/high
house_value_reference
holding_risks
```

The existing listing-based renovation evidence is preserved, but an old house with unknown electrical, plumbing, heating, drainage, or basement condition retains an uncertainty reserve. Listing updates reduce only the matching cost items; they do not imply that all other systems were updated.

## Location ledger

The ledger stores, without a new custom composite:

```text
external_provider_scores
school_quality_and_catchment
area_baseline_prior
road_class_and_exposure
road_discount_low/base/high
park_facing
view
waterfront/beach facts
corner/lane/slope facts
parcel_positive_facts
parcel_permanent_defects
```

Walk Score remains the only active external provider. Missing API data remains `UNKNOWN`. The area residual is labelled `LOCATION_BASELINE_PRIOR` until supported by a larger sold database.

## Development scenarios

Every eligible property receives explicit scenario records where the available rule data permits:

```text
BASE
CONDITIONAL
UPSIDE
```

Each scenario stores:

```text
product_type
legal_status
unit_count
theoretical_area
realistic_area
access_status
exit_value
hard_cost
soft_cost
selling_cost
developer_margin
ralv
mos
confidence
blocking_conditions
```

`development_potential` is an entitlement/geometry/product-potential diagnostic and is never used as a synonym for RALV. If exit or cost inputs are unavailable, the corresponding RALV and MOS are explicitly `UNKNOWN`.

## Acquisition ledger

The acquisition ledger stores:

```text
asking
assessment_total/land/improvement
assessment_discount_signals
area_relative_price_signal
normalized_physical_lot_basis (diagnostic)
CHB
BASE_CASH_MOS
CONDITIONAL_CASH_MOS
UPSIDE_CASH_MOS
price_risk_flags
```

The three MOS values are separate. Base MOS is the default acquisition metric; Conditional MOS is only activated after the named condition is confirmed; Upside MOS is option value and cannot justify the current asking price.

## Human calibration protocol

The V2 artifact includes a fixed-seed split manifest. The split is generated from stable property keys with `random_seed = 20260909`, 120 calibration rows, and 46 blind-test rows. The manifest stores the sorted keys and SHA-256 hash.

The later annotation file uses bands, not false-precision point estimates:

```text
human_current_use_band: A / B / C / D / FAIL
human_location_band: A+ / A / B / C / D
human_development_band: STRONG / GOOD / MARGINAL / WEAK / NOT_SUITABLE
human_decision: KEEP / WATCH / PASS / SPECIAL
human_reason: free text
```

Current-use and development labels are independent. Dual is derived only after calibration, with an optional human override field.

## Outputs

```text
data/research/normalized/strategy-v2-shadow-2026-09-09.json
data/research/normalized/strategy-v2-shadow-2026-09-09.csv
data/research/history/strategy-v2-shadow-splits.json
reports/strategy-v2-shadow/2026-09-09/audit-table.md
reports/strategy-v2-shadow/2026-09-09/ACCEPTANCE-REPORT.md
reports/strategy-v2-shadow/2026-09-09/property/<property-key>.md
```

The JSON is the authoritative machine artifact. CSV and Markdown are projections for inspection. The report explicitly states that ranking is not run.

## Acceptance criteria

- Exactly 166 input properties are represented, with stable property keys.
- V1 artifacts remain byte-for-byte unchanged by the V2 run.
- Every property has a classification result, gate findings, holding ledger, location ledger, and acquisition ledger.
- Every normal redevelopment candidate has Base/Conditional/Upside status even when the numeric RALV is `UNKNOWN`.
- `4561 Nanaimo` records direct arterial/bus/truck exposure as a permanent holding and exit risk; it cannot receive a high Current Use conclusion solely from price and lot signals.
- `5454 Melbourne` keeps strong development potential while separating rear-access-dependent capacity into Conditional.
- `3910 W10` records survival CAPEX in CHB without a Current Use hard cap.
- `1016 E18` records its sub-standard lot and does not receive standard-lot development treatment.
- `2413 Collingwood` is eligible for the premium-small-lot track rather than being compared as an ordinary large-lot redevelopment property.
- Special situations remain isolated from normal track results.
- The run reports counts, unknowns, conflicts, and scenario statuses without producing a final ranking.
