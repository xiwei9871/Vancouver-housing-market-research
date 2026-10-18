# Reno and Unified Land-Basis Scoring Design

## Scope

Update the Vancouver screening model so current-use, redevelopment, dual-value,
and price-value rankings share one transparent set of atomic measures while
using the correct price basis for each decision lens. Re-run the existing 166
enriched listings without replacing historical artifacts.

## Decisions

### Existing-house value

`bca_house_value_proxy` is the official BCA improvement assessment. It is not
multiplied by a model condition or size factor.

### Current-use basis

Current use uses the all-in holding cost:

```text
current_use_all_in_basis = asking + required_reno_estimate
```

Required Reno covers safety, dryness, insurability, heating, and functional
family use. Cosmetic luxury work is excluded. The cost enters the current-use
price/value calculation once; the Reno score represents readiness and execution
risk and must not duplicate the same dollar penalty.

Reno bands:

| Band | Required cost | Score | Current-use routing |
|---|---:|---:|---|
| MOVE_IN_READY | 0 | 100 | eligible |
| LIGHT_RENO | >0–30K | 80 | eligible |
| MODERATE_RENO | >30K–60K | 60 | eligible |
| SIGNIFICANT_RENO | >60K–100K | 40 | eligible with large penalty |
| HEAVY_RENO | >100K–150K | 20 | weak current-use candidate |
| CURRENT_USE_PASS | >150K | 0 | exclude current-use and dual tracks |

The estimator is evidence-first:

1. Official permit records are the strongest renovation evidence.
2. Explicit listing renovation year and scope are next.
3. Listing scope markers map to cost buckets; major systems are weighted above
   cosmetic work.
4. If no renovation evidence is present, age and explicit condition wording
   provide a conservative fallback. Missing evidence is not a data-completeness
   penalty, and the estimate carries an evidence/confidence label.
5. A recent substantial renovation within approximately 3–5 years, with no
   unresolved core-system warning, is `MOVE_IN_READY` and receives 100.

### Redevelopment land basis

Redevelopment uses the BCA-improvement-adjusted, location-normalized basis:

```text
bca_normalized_land_basis
  = asking - bca_house_value_proxy - location_residual
bca_normalized_land_basis_per_sqft
  = bca_normalized_land_basis / lot_area_sqft
```

This is the primary price/value input for redevelopment. The former
model-multiplied `normalized_existing_house_value` is retained only as a legacy
field for compatibility and is not used in the redevelopment price signal.

### Lot geometry

The redevelopment lot geometry subscore is 35 points, split into:

* lot area geometry: 20 points;
* frontage/depth usability: 15 points.

`33 x 122 ft` is the standard viable threshold and maps to approximately
70/100 on the combined geometry index, not a full score. A larger lot and/or
meaningfully wider frontage earns more only when the dimensions support a better
product option.

### Shared atomic scores and ranking lenses

Every property gets the same atomic measures:

* `price_value_score`;
* `location_score`;
* `house_holding_score`;
* `lot_geometry_score`;
* `official_entitlement_score`;
* `product_economics_score`;
* `risk_score`.

The three rankings are different aggregations of these common measures:

* Current Use emphasizes house/holding, location, school/micro-location, and
  all-in holding basis.
* Redevelopment emphasizes normalized land basis, geometry, official
  entitlement, product economics, and permanent exit risk. Current-house Reno
  does not contribute except for demolition, hazmat, or site execution risk.
* Dual Value combines the Current Use and Redevelopment scores after both are
  calculated on the shared scale.

Price Value is reported independently and is available as a tie-breaker and
primary value-for-money ranking. It combines the local price discount and BCA
discount without treating seller fatigue as intrinsic value.

### Unknowns and hard gates

Unknown data does not reduce data completeness, but it cannot create a positive
bonus. Hard vetoes and the existing `NEED_VERIFICATION` routing remain active.

## Output

Create a dated scoring artifact and update the latest report pointer. Include
all four rankings, per-property Reno evidence and bands, BCA fields, local price
discount, BCA-normalized land basis, and score components. Preserve previous
dated outputs.

## Testing

Add regression tests for each Reno band, recent renovation full score, age
fallback, BCA direct house proxy, the corrected land-basis formula, 33 x 122
threshold scoring, larger-frontage differentiation, and current-use exclusion
above 150K. Run the focused screening tests and the full test suite before
reporting completion.
