# Vancouver Location Valuation Research V1

## Scope

This is a research-only run. It does not modify production Current Use,
Redevelopment, Dual Value, RALV, MOS, or ranking logic.

Observed at: `2026-09-05T20:00:00+08:00`  
Current property observations: **166**  
Local sold observations: **10**  
Local listing-history observations: **3**  
Local school fixture observations: **2**

## Two accounts

Model A estimates `market_land_basis = asking - existing_house_value` and then
subtracts only location components that have transaction evidence safe to
apply. It reports `normalized_physical_lot_basis` and $/sqft metrics.

Model B remains the redevelopment account. Its RALV must use local new-product
exit comparables and redevelopment costs. It is intentionally not calculated
from or mixed with Model A in this run.

## Evidence rule

Every premium carries value, applied value, method, sample size, confidence,
and source IDs. Configuration priors are retained for review but have applied
value zero. The current local transaction data has no controlled boundary
matched pairs for school, view, park, or road, so these components are not
automatically monetized.

## What the current data can and cannot identify

- BCA improvement value can be retained as an existing-house prior; it is not
  treated as the market house value without matched-sale calibration.
- School catchment names from official enrichment can identify school context,
  but the available sold rows do not provide school-separated matched pairs.
- Area residual requires cross-neighbourhood observations with lot, house,
  zoning, school, road, and time controls. The local ten-sale fixture has two
  neighbourhood groups and cannot identify that residual.
- Road observations exist for current properties, but no controlled sold
  outcome identifies a road discount.
- Parks, view, beach, amenity, and transit sale outcome data are absent from
  the current local input.
- A hedonic model is reported as insufficient when the configured minimum
  sample is not met; no coefficient is fabricated.

## Outputs

The CSVs are evidence ledgers, not a claim that every segment has a market
premium. `UNKNOWN`, `LOW`, and `PRIOR_ONLY` values must be resolved with a
larger lawful sold dataset before entering production price ranking.
