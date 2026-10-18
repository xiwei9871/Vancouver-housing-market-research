# Strategy V2 Phase 2A — Realtor Photo Intelligence & High-Recall Online Screening

**Date:** 2026-09-10  
**Status:** Approved for implementation  
**Scope:** The frozen 166-property Vancouver listing universe

## Goal

Use the existing REALTOR.ca listing text and structured enrichment, plus every
available listing photo, to reduce the 166-property universe to approximately
25–35 online-screened candidates without attempting online inspection,
final underwriting, or RALV calculation.

The priority is high recall. A false positive may proceed to human review; a
false negative that removes a genuinely attractive property is the principal
failure mode.

## Relationship to earlier models

The V1 scorer and artifacts remain frozen and read-only. Strategy V2 Phase 1
ledgers remain inputs/reference and are not overwritten. Phase 2A writes a new
date-stamped artifact and a new report tree. It may reuse the saved official
enrichment card, V2 Phase 1 ledger, and listing-detail snapshots, but it does
not call or mutate the V1 scoring engine.

## Non-goals

- No Current Use, Dual, or Redevelopment final ranking.
- No precise inspection-grade renovation or maintenance estimate.
- No inference that an unphotographed system is defective or newly replaced.
- No use of cosmetic age as a maintenance failure.
- No RALV, local new-build comp, developer margin, or offer recommendation.
- No automatic rejection solely because photos are missing or coverage is low.

## Source and cache contract

The source of truth for each image is a versioned photo manifest. Every image
observation records:

```text
property_key
source_url
original_order
retrieved_at
sha256
perceptual_hash
width
height
local_path
source_status
```

The manifest may be built from a REALTOR.ca detail-page capture or supplied
from a local replay. A live fetch failure is recorded per property/image and
does not stop the batch. Existing HTML snapshots are preferred for repeatable
runs; no image is silently replaced by a newer URL without a new observation.

## Photo content pipeline

The pipeline has two separate steps:

1. classify each photo into one or more controlled content labels;
2. aggregate only the evidence-supported labels into condition ledgers.

Supported content labels:

```text
EXTERIOR_FRONT
EXTERIOR_REAR
ROOF_VISIBLE
LIVING
KITCHEN
BATH
BEDROOM
BASEMENT
MECHANICAL
GARAGE
YARD
FLOOR_PLAN
MAP
OTHER
```

The implementation must support deterministic metadata/description evidence
and an optional visual-classification adapter. If a visual adapter is not
configured, labels remain `UNKNOWN` rather than being fabricated from photo
order. Duplicate images are identified using exact SHA-256 and perceptual
hash where available; duplicates remain in the manifest but count once in
coverage.

## Three distinct online holding dimensions

The output must keep these dimensions independent:

```text
VISIBLE_MAINTENANCE_CONDITION
COSMETIC_MODERNIZATION
HOLDING_USABILITY
```

Examples:

- clean but dated kitchen: maintenance `GOOD`, modernization `DATED`,
  usability `GOOD`;
- visible water staining or major unfinished demolition: maintenance is
  downgraded and a visible-defect flag is emitted;
- five bedrooms alone do not increase family usability above a normal
  four-bedroom layout; multiple kitchens are a separate layout/income signal.

Each dimension is a band plus an explanation/evidence list, not an opaque
single score. The machine-readable normalized values are:

```text
visible_maintenance_score: 0–100 or UNKNOWN
cosmetic_modernization_score: 0–100 or UNKNOWN
holding_usability_score: 0–100 or UNKNOWN
```

The scores are explanatory screening measures and are not V1 scores.

## Photo coverage and confidence

Coverage is based on required-space groups rather than photo count. The
default groups are:

```text
exterior: front/rear/roof
living: living/bedroom/kitchen/bath
basement: basement/mechanical
site: yard/garage
```

`photo_evidence_coverage` is the percentage of groups with at least one
non-duplicate classified photo, with a separate `photo_count` and
`classified_photo_count`. `photo_confidence` is independent from condition:

```text
HIGH       coverage >= 75 and no material classification ambiguity
MEDIUM     coverage >= 50
LOW        coverage < 50 or visual evidence is mostly unclassified
UNKNOWN    no usable photo evidence
```

Low coverage does not turn a good-looking room into `POOR`; it lowers evidence
confidence and adds a review flag.

## Online CAPEX band

The online band is a visible-risk band, not an inspection quote:

```text
A  visibly very well maintained / recent updates
B  maintained but dated
C  visible deferred maintenance / several moderate items
D  obvious major repair burden
E  severe / teardown-like
```

No reserve is added merely because a roof, foundation, electrical system, or
plumbing is not pictured. Explicit text/photo evidence can support a risk
flag. Cosmetic modernization affects the modernization field and only a
limited usability explanation; it does not automatically create CAPEX.

## Lightweight online screening fields

For every property, Phase 2A produces:

```text
online_holding_score
online_location_score
online_land_score
price_attractiveness
permanent_risk
visible_maintenance_condition
cosmetic_modernization
holding_usability
photo_evidence_coverage
photo_confidence
online_capex_band
```

Existing structured fields continue to supply location, school, lot, zoning,
BCA, road, price, DOM, and listing-history evidence. Phase 2A may compute
transparent screening subscores, but no score may hide a missing field or
convert unknown into zero.

## Track-aware shortlist and Pareto protection

Normal candidates compete within their primary track. Initial screening
budgets are:

```text
HOME_REBUILD          approximately 20
LAND_DEVELOPMENT      approximately 5
PREMIUM_SMALL_LOT     approximately 5
```

These are resource-allocation targets, not forced quotas. `SPECIAL_SITUATION`
does not consume a normal-track slot. A property appears at most once in the
shortlist, using its primary track.

Before applying the target sizes, retain any candidate on a track Pareto
frontier when no other same-track candidate is at least as good on all of:

```text
location
holding
land
price attractiveness
permanent risk
```

Also retain an `EXCEPTIONAL_SIGNAL` observation when one dimension is unusually
strong, even if the candidate is outside the provisional target. If the
frontier plus exceptional set exceeds the target, the output may be larger
than 35 and must explain why; it must never silently cut a non-dominated case.

## Calibration and recall validation

Use the frozen V2 split:

```text
random_seed = 20260909
calibration = 120
blind_test = 46
```

Human annotations use bands and retention labels:

```text
human_keep_label: MUST_KEEP / KEEP / MAYBE / DROP
human_current_use_band: A / B / C / D / FAIL
human_location_band: A+ / A / B / C / D
human_development_band: STRONG / GOOD / MARGINAL / WEAK / NOT_SUITABLE
```

The first run may have no labels and must report `NOT_AVAILABLE`. Once labels
exist, the report calculates:

```text
MUST_KEEP_RECALL = retained MUST_KEEP / all MUST_KEEP
KEEP_RECALL = retained (MUST_KEEP or KEEP) / all (MUST_KEEP or KEEP)
```

The target is near 100% MUST_KEEP recall and at least 90% KEEP recall on the
calibration set. Blind results remain hidden until the screening configuration
is frozen.

## Outputs

```text
data/research/raw/photos/<property-key>/manifest.json
data/research/normalized/strategy-v2-online-photo-evidence-2026-09-10.json
data/research/normalized/strategy-v2-online-screening-2026-09-10.csv
data/research/history/strategy-v2-online-screening-snapshots.jsonl
reports/strategy-v2-online-screening/2026-09-10/online-shortlist.md
reports/strategy-v2-online-screening/2026-09-10/audit-report.md
reports/strategy-v2-online-screening/2026-09-10/ACCEPTANCE-REPORT.md
```

Property-level evidence is stored in the JSON authority and the shortlist
report links each retained property to its photo manifest and existing V2
ledger. No V1 file is overwritten.

## Acceptance criteria

- All 166 input properties receive a Phase 2A row, even if no photos are
  available.
- Every photo observation has the required manifest fields.
- Duplicate images do not inflate coverage.
- Content classification precedes condition aggregation.
- Maintenance, cosmetic modernization, and holding usability remain separate.
- Low photo coverage lowers confidence only; it does not become a false poor
  condition.
- Missing photo/source data does not stop the batch or become zero.
- Track-aware selection produces an explainable approximately 25–35 candidate
  pool, with Pareto and exceptional cases protected.
- Each retained candidate has reason codes and structured evidence.
- Calibration recall is reported when labels exist and explicitly unavailable
  otherwise.
- Phase 2A does not emit a final V2 ranking or RALV.
- V1 and V2 Phase 1 artifacts remain byte-for-byte unchanged.
