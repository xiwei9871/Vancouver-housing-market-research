# Strategy V2 Online CAPEX and Cosmetic Reno Estimation Design

**Date:** 2026-09-12  
**Status:** Design approved in conversation; implementation pending written-spec review  
**Scope:** Strategy V2 Phase 2A online evidence aggregation for the frozen 166-listing universe

## 1. Goal

Improve the online assessment of existing-house condition so the system can
separately estimate:

```text
Survival CAPEX
Cosmetic Reno
Holding Usability
Photo Evidence Confidence
```

The system is an online screening tool. Listing photos and descriptions are
not an inspection, a contractor quote, or proof that hidden systems are sound.

## 2. Non-goals and compatibility

This change does not:

- modify V1 scoring or legacy artifacts;
- modify V2 asset classification, location, BCA, zoning, development, RALV,
  or final ranking rules;
- infer a defect from a missing photograph;
- turn cosmetic age into a maintenance failure;
- replace an inspection, seller disclosure, permit review, or contractor quote;
- call the visual API again during the deterministic re-aggregation run.

Existing fields remain available for compatibility:

```text
visible_maintenance_condition
visible_maintenance_score
cosmetic_modernization
cosmetic_modernization_score
holding_usability
holding_usability_score
online_capex_band
photo_confidence
```

The new fields are additive and versioned.

## 3. Evidence taxonomy

Every extracted observation is assigned to one of these categories before it
affects a cost ledger:

| Category | Examples | Survival CAPEX | Cosmetic Reno |
|---|---|---:|---:|
| `CRITICAL` | fire, collapse, uninhabitable, severe mold, obvious structural failure | yes; E | no |
| `MAJOR` | clear roof failure, sustained water damage, major unfinished demolition, serious floor deformation | yes; D | no |
| `MODERATE` | bounded water/moisture issue, localized envelope damage, clear drainage/threshold issue | possibly; C | possibly |
| `MINOR_MAINTENANCE` | light rust, weathered fence, dry lawn, surface paint peeling, ordinary aging | no, except one merged minor allowance | small optional allowance |
| `COSMETIC` | dated kitchen, old cabinets, dated bathroom, old flooring, paint/style, clutter | no | yes |
| `NOT_OBSERVED` | no visible issue in an inspected frame | no inference | no inference |
| `UNKNOWN` | roof, furnace, electrical, plumbing, foundation, or key room not covered | no invented cost | no invented cost |

Free-form model text is normalized into controlled evidence categories using
case-insensitive aliases and conservative keyword rules. A phrase such as
“dry lawn” or “weathered fence” must not become a generic `visible_defects`
item that automatically raises the CAPEX band.

## 4. Separate ledgers

### 4.1 Survival CAPEX

Survival CAPEX covers only work required to keep the home safe, dry, heated,
functional, and reasonably insurable for a 5–10 year holding period.

Project groups:

```text
roof
water / drainage
foundation / structure
heating
electrical
plumbing
envelope / windows / siding
basement / major unfinished work
```

Minor exterior wear is tracked separately and is not Survival CAPEX by
default. It enters Survival CAPEX only when the evidence shows a safety,
drainage, envelope, or insurability consequence.

Each unique project group is recorded once even when multiple photographs
describe it.

### 4.2 Cosmetic Reno

Cosmetic Reno is optional improvement for appearance or convenience:

```text
paint / lighting / closet refresh
flooring
kitchen cosmetic update
bathroom cosmetic update
```

Cosmetic Reno is displayed separately and does not enter the default CHB
calculation.

### 4.3 Functional upgrade

Adding a bathroom, finishing a basement for more living area, changing a
layout, or converting an income layout is not silently classified as Survival
CAPEX or Cosmetic Reno. It remains a separate `FUNCTIONAL_UPGRADE` flag and
is excluded from the online cost totals unless explicit evidence shows that
the existing condition is unfinished or unusable.

## 5. Item schema

The assessment adds these item collections:

```json
{
  "observed_survival_items": [
    {
      "category": "ROOF",
      "scope": "LOCAL",
      "cost_type": "SURVIVAL",
      "cost_low": 5000,
      "cost_base": 10000,
      "cost_high": 15000,
      "source": "PHOTO",
      "evidence": "localized roof-edge deterioration",
      "confidence": "MEDIUM"
    }
  ],
  "observed_cosmetic_items": [],
  "minor_maintenance_items": [],
  "unseen_systems": ["FURNACE", "ELECTRICAL"],
  "unobserved_critical_areas": ["BASEMENT", "MECHANICAL"],
  "estimate_confidence": "MEDIUM"
}
```

The `OnlineHoldingAssessment` model adds:

```text
survival_capex_low
survival_capex_base
survival_capex_high
cosmetic_reno_low
cosmetic_reno_base
cosmetic_reno_high
observed_survival_items
observed_cosmetic_items
minor_maintenance_items
unseen_systems
unobserved_critical_areas
estimate_confidence
estimate_basis
```

The property-level row adds:

```text
online_cost_ledger
online_chb:
    low
    base
    high
```

`online_chb = asking + survival_capex`; cosmetic costs are excluded.

## 6. Default online cost priors

These ranges are screening priors, not quotations:

| Project group | Local / bounded issue | Systemic / major issue |
|---|---:|---:|
| Roof | $5K–15K | $15K–35K |
| Water / drainage | $5K–20K | $20K–60K |
| Foundation / structure | $10K–30K | $30K–100K+ |
| Heating | $5K–12K | $12K–25K |
| Electrical | $3K–15K | $15K–40K+ |
| Plumbing | $3K–15K | $15K–40K+ |
| Envelope / windows / siding | $3K–15K | $15K–50K |
| Basement / major unfinished work | $10K–30K | $30K–80K+ |
| Merged minor exterior maintenance allowance | $0–10K | not applicable |

Cosmetic priors:

| Project group | Online range |
|---|---:|
| Paint / lighting / closet refresh | $5K–20K |
| Flooring | $5K–20K |
| Kitchen cosmetic update | $15K–40K |
| Bathroom cosmetic update | $8K–20K |
| Multiple ordinary cosmetic projects | capped at $60K unless explicitly major |

Calculation rules:

```text
low  = sum of unique observed item low values
base = sum of unique observed item base values
high = sum of unique observed item high values
```

Additional protections:

- repeated evidence for one system is counted once;
- minor exterior evidence is merged and capped at $10K in the separate
  `minor_maintenance_items` allowance; it is not added to Survival CAPEX by
  default;
- unknown systems add no cost to the observed totals;
- cosmetic items never enter Survival CAPEX;
- D/E is triggered by severity, not by unlimited accumulation of minor text;
- if no Survival item is observed, `survival_capex_base` remains $0 even if
  the band is B because hidden systems remain unconfirmed.

## 7. CAPEX band assignment

`online_capex_band` is a risk/evidence band, not a required spend amount:

```text
E: fire, collapse, uninhabitable, severe mold, or equivalent severe evidence
D: explicit major repair burden or systemic major issue
C: one clear moderate necessary project, or multiple distinct moderate
   necessary projects
B: no clear necessary repair, but dated/older house, limited coverage, or
   minor maintenance evidence leaves ordinary uncertainty
A: broad coverage, consistent good maintenance evidence, and explicit recent
   key updates with no visible Survival issue
UNKNOWN: insufficient visual/text evidence to assess
```

A/B/C/D/E retain these display ranges:

```text
A: $0–40K
B: $30–70K
C: $60–120K
D: $100–200K+
E: $150K+ / severe
```

The display range must not be silently substituted for the observed
low/base/high ledger. A B-band property can therefore have:

```text
survival_capex_base = $0
online_capex_band = B
unseen_systems = [roof, furnace, electrical, plumbing]
```

## 8. Age, update, and description rules

- Year built is contextual evidence only; age alone never triggers C/D.
- `original kitchen`, `dated bathroom`, and `old flooring` are Cosmetic Reno
  evidence.
- `fully renovated` or `recently updated` improves modernization evidence;
  it proves a Survival update only when the description names the relevant
  system or a photo provides direct evidence.
- A recent kitchen renovation does not imply a new roof or electrical system.
- Explicit `new roof`, `new furnace`, `updated electrical`, or similar terms
  are recorded as named system evidence.
- Photo/text conflict creates `EVIDENCE_CONFLICT` and uses the more
  conservative band; it does not average away the conflict.

## 9. Confidence and unseen systems

`estimate_confidence` is independent of condition and cost:

```text
HIGH: coverage >=75%, required key spaces covered, and evidence is consistent
MEDIUM: coverage 50–75% or evidence relies partly on limited text/representative photos
LOW: coverage <50% or key spaces are not covered
UNKNOWN: no usable photo/text evidence
```

The system records unobserved areas and systems explicitly. Low confidence
does not downgrade a property to poor condition; it limits the strength of
the conclusion and signals human review.

## 10. Pipeline integration

The existing Phase 2A runner remains the aggregation entry point:

```text
local visual manifests / cached REALTOR.ca HTML
    ↓
photo coverage summary
    ↓
controlled evidence normalization
    ↓
maintenance / cosmetic / usability assessment
    ↓
survival and cosmetic project ledgers
    ↓
online CHB range
    ↓
166-property JSON/CSV and audit report
```

The implementation will reuse the current 30-property visual snapshot and
will not make another visual API request. The remaining properties retain
HTML/text-only evidence and corresponding confidence flags.

The shortlist remains high-recall and `ranking_status=NOT_RUN`; no final
Current Use, Dual, or Redevelopment ranking is generated by this change.

## 11. Test requirements

Unit and runner tests must cover:

1. A clean but dated house has Cosmetic Reno evidence but zero observed
   Survival base cost and is not automatically C.
2. Dry lawn, weathered fence, light rust, and surface aging are merged as
   minor maintenance and do not independently trigger C.
3. Visible water damage, mold, foundation cracking, severe floor deformation,
   or major unfinished demolition produces the appropriate C/D/E behavior.
4. Explicit recent named system updates can produce A when coverage and
   evidence are sufficient.
5. Multiple photos describing one roof or water issue produce one cost item.
6. Missing mechanical/basement photos produce `unseen_systems` and no
   invented cost.
7. No-photo properties remain UNKNOWN rather than becoming poor condition.
8. Property-level online CHB equals asking plus Survival CAPEX only; Cosmetic
   Reno is excluded.
9. Existing V1/V2 input artifacts remain byte-for-byte unchanged.

## 12. Acceptance criteria

The implementation is accepted when:

- all 166 properties produce the additive schema;
- the existing 30 visual manifests are aggregated without another API call;
- ordinary surface aging no longer makes every visually classified property
  appear to need a C-band repair burden;
- each amount is traceable to a unique evidence-backed project or explicitly
  marked unknown;
- Survival and Cosmetic ledgers are visibly separate in JSON, CSV, and the
  report;
- old V1 and V2 artifacts are unchanged;
- the full automated test suite passes;
- a date-stamped report states that online evidence is not inspection-grade.
