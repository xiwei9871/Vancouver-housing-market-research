# Unified Property Value and Ranking Design

## Objective

Every normal detached/freehold listing must receive one complete, reproducible valuation record. The record must cover the existing house, the land, location, price safety, renovation cost, and redevelopment option. `Current Use`, `Redevelopment`, and `Dual Value` are three ranking views over that same record; they are not separate acquisition or enrichment pipelines.

The design must answer two different questions without losing their relationship:

1. Is the entire purchase package (house plus land, including required near-term work) good value for current use?
2. Is the physical land being acquired at a good value for a future redevelopment outcome?

## Non-negotiable invariants

- All normal listings use the same field definitions, formulas, score scales, and evidence rules.
- All value fields are calculated before any leaderboard is created.
- No listing receives a positive score merely because it is absolutely cheaper than another municipality or neighbourhood.
- Location residual is used as a regional valuation reference once. It must not be added as an independent bonus while also being embedded in the regional price denominator.
- BCA assessment discount is a price-safety signal, not the market value itself.
- BCA improvement value is the default existing-house value proxy, with no unverified condition multiplier.
- Renovation is a cost added to the current-use basis. Recent renovation reduces required cost; it is not converted into an oversized maintenance bonus.
- Lot size, frontage, depth, and geometry are valued through normalized land-basis measures and legal/buildability evidence, not through a capped "larger is always better" bonus.
- Missing data remains `UNKNOWN`; it does not become zero and does not reduce a score merely because it is missing. Known negative evidence does reduce the relevant value or risk score.
- Special transactions remain outside normal home-track leaderboards.

## Canonical value ledger

Every property must expose these fields, whether or not it eventually ranks highly:

```text
asking_price
reno_cost_estimate
current_use_effective_basis = asking_price + reno_cost_estimate

bca_total
bca_improvement
bca_land
bca_discount_pct = 1 - asking_price / bca_total

location_residual_cad
parcel_location_adjustment_cad
total_location_value_cad
regional_standard_property_value

market_land_basis = asking_price - bca_improvement
area_normalized_lot_basis
area_normalized_lot_basis_per_sqft
risk_adjusted_lot_basis
risk_adjusted_lot_basis_per_sqft
area_standard_lot_equivalent_basis
risk_adjusted_standard_lot_equivalent_basis

regional_price_ratio
regional_price_discount_pct
current_package_value_proxy
current_package_value_proxy_source
current_package_value_ratio
current_value_gap_cad
current_lot_basis_per_sqft
current_lot_basis_ratio

house_value_for_money_score
location_value_for_money_score
lot_value_for_money_score
regional_price_safety_score
bca_price_safety_score
permanent_risk_score
redevelopment_capacity_value_for_money_score
```

### Location and land normalization

The area residual establishes the local standard-property value:

```text
regional_standard_property_value
= neutral_standard_property_value + location_residual_cad
```

For cross-area physical-land comparison:

```text
area_normalized_lot_basis
= asking_price
 - bca_improvement
 - area_location_residual_cad
```

Parcel-specific permanent effects are shown separately and then included in the risk-adjusted view:

```text
total_location_value_cad
= area_location_residual_cad
 + parcel_positive_adjustments
 - road_noise_discount
 - other_nuisance_discounts

risk_adjusted_lot_basis
= asking_price
 - bca_improvement
 - total_location_value_cad
```

Both versions must be retained. The first isolates neighbourhood/area pricing; the second reflects the actual parcel's permanent location quality.

`area_normalized_lot_basis_per_sqft` and `risk_adjusted_lot_basis_per_sqft` are cost metrics: lower is better. They are not positive lot-size scores. Standard-lot equivalents are retained for easier comparison with the 33 x 122 ft reference:

```text
area_standard_lot_equivalent_basis
= area_normalized_lot_basis_per_sqft * 4,026

risk_adjusted_standard_lot_equivalent_basis
= risk_adjusted_lot_basis_per_sqft * 4,026
```

The system must state which variant is used in every report. The area-only variant is used to compare neighbourhood pricing; the risk-adjusted variant is used for actual investment downside and future-exit analysis.

## House and holding value

The default house value proxy is:

```text
market_house_value_proxy = official BCA improvement value
```

The current-use cost is:

```text
current_use_effective_basis = asking_price + reno_cost_estimate
```

The current-use package value proxy uses the latest official BCA total when
available. This is the direct assessed house-plus-land anchor for current use;
it is intentionally not confused with BCA improvement, which is used for the
redevelopment land-basis split. If BCA total is unavailable, the package proxy
falls back to the physical model estimate and is labelled as such:

```text
reference_house_value_proxy
= median official BCA improvement for the standard-property cohort

reference_land_value
= regional_standard_property_value - reference_house_value_proxy

current_package_value_proxy = official BCA total
current_package_value_proxy_source = BCA_TOTAL

fallback:
current_package_value_proxy
= reference_land_value * physical_lot_factor
 + official/imputed house proxy
 + parcel location adjustments
```

If the standard cohort house-value proxy is unavailable, the package value ratio is `UNKNOWN`; the property is not assigned a guessed value.

Renovation bands remain:

```text
0                 move-in ready / no identified work
0–30K             light work
30–60K            moderate work
60–100K           substantial work
100–150K          major work; current-use downgrade
>150K             current-use pass; redevelopment track may remain
```

For the unified production score, the renovation estimate also imposes a
Current Use ceiling. This prevents land cheapness from disguising a house
that is not realistically ready for the intended holding period:

```text
0                  100 (move-in ready / no identified work)
0–30K              80
30–60K             60
60–100K            40
100–150K           20
>150K              0
```

The ceiling is applied after the common Current Use components are calculated
and the components are proportionally rescaled so their authoritative sum
still equals the published Current Use score. The estimate remains a
planning signal, not an inspection or contractor quote.

The report must list the evidence behind the renovation estimate. Age-only fallback is allowed as an uncertainty marker, but it cannot create a high maintenance score. Recent listing-described work or official permit evidence reduces the estimated required work; it does not produce an independent windfall bonus.

## Price safety signals

The following remain separate, auditable fields:

```text
regional_price_discount_pct
= 1 - current_use_effective_basis / regional_standard_property_value

bca_discount_pct
= 1 - asking_price / bca_total
```

The regional discount answers whether the package is inexpensive relative to its own neighbourhood. The BCA discount answers whether the asking price is below the latest official assessment. Neither is allowed to be treated as guaranteed profit.

These signals may contribute capped points to a score, but their source percentages must always be displayed beside the score.

## Three ranking views

All three views use the canonical ledger. The difference is the question being answered, not the underlying data universe.

### Current Use Value-for-Money

This is not a pure house-quality score. It evaluates the complete purchase package:

```text
house value and required work
location value
lot value and land exposure
regional price safety
BCA safety signal
permanent defects
```

The lot component must be meaningful but smaller than in the redevelopment view. A high-quality house cannot completely hide an overpriced or undersized lot, and a cheap lot cannot completely hide an unacceptable holding house.

### Redevelopment Value-for-Money

This evaluates the land acquisition and legal/product conversion:

```text
normalized land basis
frontage/depth/geometry
official zoning and FSR
realistic buildable area and units
local exit value
road-adjusted exit discount
construction economics
required profit
```

Existing-house condition is still calculated because BCA improvement affects the land-basis split, but condition is not rewarded as a redevelopment benefit.

The redevelopment lot value-for-money component is deliberately split between
price efficiency and physical rebuild adequacy. The initial production rule
uses 30% normalized land-price efficiency and 70% geometry adequacy. The
configured standard 33 x 122 ft / approximately 4,026 sqft lot is 70/100 on
geometry, not 100/100; a sub-3,000 sqft lot therefore cannot win the
redevelopment ranking on asking-price cheapness alone.

### Dual Value

Dual is derived from the two value-for-money scores using a geometric mean.
In the current production configuration it is calculated for every scored
normal row; it is a ranking view, not a gate:

```text
dual_score = sqrt(current_use_score * redevelopment_score)
```

Older replay configurations may retain minimum thresholds for compatibility.
The report still exposes both component scores so a weak axis is visible.

## Ranking behavior

After every property has all canonical fields and three scores:

```text
Current Use Top 10     sort current_use_score descending
Redevelopment Top 10   sort redevelopment_score descending
Dual Value Top 10      sort dual_score descending
```

No property is excluded from a normal leaderboard because it is weak on another dimension. Special transaction and legal hard-veto cases remain separately labeled.

The report must show both the score and the value evidence that drove it. At minimum, every ranked row includes:

```text
asking
reno cost / effective basis
BCA improvement / BCA discount
area residual / regional discount
normalized lot basis per sqft
lot dimensions
location and road adjustment
current-use score
redevelopment score
dual score
```

## Implementation and migration

1. Preserve the existing enrichment and history data.
2. Replace the current score-only price path with the canonical value ledger.
3. Remove the standalone current-use location-residual bonus when the same residual is already embedded in the regional price denominator.
4. Add area-only and risk-adjusted land-basis fields so road treatment is explicit rather than hidden in one ambiguous number.
5. Reduce maintenance from a large positive score to an evidence-based cost/cap; use BCA improvement as the house-value proxy.
6. Recalculate all 166 listings from the same enriched snapshot.
7. Validate 2012 York versus 3910 W10, 4561 Nanaimo versus 5454 Melbourne, and the benchmark set before accepting new rankings.

## Acceptance criteria

- Every normal listing has the canonical ledger fields or explicit `UNKNOWN`.
- The same property has one set of value calculations regardless of which leaderboard displays it.
- Current Use includes land cost/value and renovation cost; it is not merely a house-condition ranking.
- Redevelopment emphasizes normalized land basis and legal/product potential; it does not award points for cosmetic condition.
- Location residual is not double-counted.
- 2012 York cannot outrank 3910 W10 solely because the former has more bedrooms/bathrooms while ignoring its materially higher normalized lot basis.
- 4561 Nanaimo receives a strong current-use result when its documented updates support that conclusion, while its arterial-road penalty is visible in both current-use and redevelopment analyses.
- All three leaderboards are generated from the same 166-property result set and the same evidence snapshot.
