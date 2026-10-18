# Recommendation

## Current conclusion

This V1 run should **not** modify production price scoring. It has
`10` local sold observations and the hedonic status is
`INSUFFICIENT_SAMPLE`. The local data does not yet support a statistically
defensible split of Point Grey/Arbutus area premium, school premium, park/view
premium, or arterial discount.

## Answers to the research questions

1. **Statistically meaningful factors:** none can be confirmed by the current
   controlled transaction sample; official current road and catchment fields
   are available but lack sale-outcome controls.
2. **Approximate CAD values:** only the configuration priors are shown; they
   are `PRIOR_ONLY` and applied value is zero.
3. **Insufficient evidence:** school, area residual, view, park/beach,
   walkability, transit, and road/noise premiums/discounts.
4. **School prior ladder:** not supported or rejected by the current sample;
   preserve as an economic anchor only.
5. **Neighbourhood residuals:** UNKNOWN until cross-area controlled sales are
   added.
6. **Arterial discount:** UNKNOWN until same-context sold pairs are added.
7. **Park/view/beach:** UNKNOWN; the benchmark facts are retained but not
   monetized.
8. **Transit:** UNKNOWN; no structured sale-outcome feature is present.
9. **Correlated factors:** neighbourhood, school, view, amenity and buyer pool
   are likely correlated; do not split them without variation and controls.
10. **Next production change:** none yet. Acquire a larger, lawful detached
    freehold sold dataset across the $1.0M–$3.0M+ range, join official school
    and GIS features, then validate matched pairs and hedonic diagnostics.

## Model safety

`normalized_physical_lot_basis` is a cross-area comparison metric only. It
must not be compared directly with full local RALV to calculate MOS, because
local RALV already includes local product and buyer-pool premiums.
