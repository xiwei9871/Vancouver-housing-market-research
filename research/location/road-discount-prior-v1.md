# Road / Street-Exposure Discount Prior V1

## Purpose

This artifact applies the approved initial discount table for a property
exposed to a road, relative to a comparable quiet interior residential street.
It is a screening and underwriting prior, not a claim that every road has the
same empirically estimated local effect.

## Approved initial table

| Road condition | Low | Base used in ranking | High |
|---|---:|---:|---:|
| Quiet residential street | 0% | 0% | 0% |
| Ordinary collector | 2% | 3% | 4% |
| Ordinary arterial | 5% | 6.5% | 8% |
| High-flow arterial | 7% | 8.5% | 10% |
| Arterial + bus/truck route | 8% | 10% | 12% |
| Arterial + truck + intersection/high noise | 10% | 12.5% | 15% |

The model stores the discount as a positive magnitude, while reports display it
with a minus sign.

## Calculation

For each listing:

```text
road_discount_cad
  = quiet_inner_street_standard_property_value × base_discount_pct

road_adjusted_standard_property_value
  = quiet_inner_street_standard_property_value - road_discount_cad
```

The adjusted value is used by the area-relative price signal and price-safety
score. The same road adjustment is included as a negative location term when
the BCA-normalized physical-lot basis is calculated. This prevents a low-price
arterial lot from looking artificially cheap merely because the road defect has
already reduced its asking price.

Exposure handling:

| Evidence | Applied exposure |
|---|---:|
| Directly fronts identified road | 1.00 |
| Identified road within 50m but not direct frontage | 0.75 |
| Identified road within 50–100m | 0.75 |
| Identified road within 100–200m | 0.50 |
| Identified road beyond 200m | 0.00 |
| Road class known but distance/frontage unavailable | 0.50 |

The stored low/base/high percentages are the table interval multiplied by this
exposure factor. A road beyond 200m is retained as background context but does
not receive a direct frontage discount.

## Relation to score components

The existing qualitative `road_penalty` remains in the Current Use micro-location
component because it represents the living experience. The monetary road prior
is separate: it adjusts the local price reference and the normalized land
basis, representing whether the asking price compensates for a permanent
defect. Keeping both is intentional; one measures use impact and the other
measures price compensation.

## 2026-09-07 local rerun

The local 166-card artifact contained 157 normal Stage-B scored rows after
Stage-A transaction/scope handling. Road evidence and prior application were:

| Output | Count |
|---|---:|
| Normal scored rows | 157 |
| Known road class | 96 |
| Road discount applied with non-zero midpoint | 77 |
| Background road only (>200m) | 17 |
| Road class unknown | 61 |
| Quiet residential | 2 |

Examples:

- **4561 NANAIMO STREET**: official class B, direct frontage, 34m; classified
  `ARTERIAL_BUS_TRUCK`; base prior `-10%`; basis `$1,775,000`; base discount
  `$177,500`; range `$142,000–$213,000`.
- **5454 MELBOURNE STREET**: identified major road is background exposure over
  200m; classified `BACKGROUND_ROAD`; direct road discount `$0`.

## Limitations retained

The current municipal road enrichment does not consistently provide truck-route,
bus-route, or frontage fields for every card. The agreed table is therefore
applied from available official road class/distance/frontage data plus the
configured Nanaimo route prior. Unknown road evidence remains visible and is
not silently converted into a penalty.

The production artifact and per-listing fields are in:

```text
data/research/normalized/regional-official-enrichment-2026-09-07.json
reports/discovery/regional-official-enrichment-2026-09-07.md
```
