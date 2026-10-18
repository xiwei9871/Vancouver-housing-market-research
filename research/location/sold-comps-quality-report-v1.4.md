# Sold Comps Quality Report V1.4

Research window: `2023-01-01` to `2026-09-06`.

## Scope

Target population is City of Vancouver detached freehold completed sales. Asking-only rows, unknown title, out-of-scope municipalities, and conflicts are retained for audit but excluded from statistics.
Current official zoning, road, and school joins describe the current spatial context of an address; they are not historical as-of-sale attributes unless the source explicitly provides that time version.

## Summary

- `total_observations`: 5158
- `eligible_sales`: 5072
- `conflict_count`: 0
- `outside_target_scope_count`: 76
- `benchmark_count`: 10
- `benchmark_observed_count`: 0

## Sources

- `data/research/raw/location/bca-arcgis-sales-2023-01-01-2026-09-06.json`
- `data/research/raw/location/bca-arcgis-sales-2023-01-01-2026-09-06-enriched-high.json`
- `data/research/raw/location/bca-arcgis-sales-over-6m-enriched.json`
- `data/research/raw/location/bca-arcgis-sales-2026-09-06-enriched-spatial.json`
- `data/research/normalized/regional-official-enrichment-2026-09-05.json`
- `data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson`
- `data/research/normalized/school-boundaries/school-catchment-53a59061080d5749.json`
- `data/research/raw/location/vancouver-public-streets-2026-09-06.json`
- `data/research/raw/location/vancouver-zoning-2026-09-06.json`
- `data/research/raw/location/vancouver-parks-2026-09-06.json`

## Coverage among eligible sales

| Field | Coverage |
|---|---:|
| bca | 100.0% |
| bca_property_detail | 82.0% |
| neighbourhood | 99.5% |
| coordinates | 99.7% |
| school | 100.0% |
| road | 99.4% |
| official_park_distance | 99.7% |
| zoning | 99.3% |
| current_official_zoning | 99.3% |
| lot | 73.9% |
| house | 81.6% |
| mls | 0.0% |
| verified_transaction_mls | 0.0% |
| current_listing_mls | 0.2% |
| sale_date | 100.0% |
| sale_price | 100.0% |

## Samples by neighbourhood

| Neighbourhood | Eligible sales |
|---|---:|
| Arbutus Ridge | 231 |
| Collingwood | 3 |
| Dunbar-Southlands | 511 |
| Fairview | 6 |
| Grandview-Woodland | 232 |
| Hastings-Sunrise | 482 |
| Kensington-Cedar Cottage | 558 |
| Kerrisdale | 292 |
| Killarney | 167 |
| Kitsilano | 249 |
| Knight | 1 |
| Main | 1 |
| Marpole | 199 |
| Mount Pleasant | 86 |
| Oakridge | 119 |
| Renfrew | 1 |
| Renfrew-Collingwood | 418 |
| Riley Park | 296 |
| Shaughnessy | 167 |
| South Cambie | 73 |
| Strathcona | 46 |
| Sunset | 301 |
| Victoria-Fraserview | 300 |
| West Point Grey | 309 |

## Gates

- Sold-database size gate (`>=300` eligible sales): **MET**.
- Factor-evidence gate: **not met for automatic production pricing**. Current official zoning, road, and school spatial joins are available where coordinates can be matched, but factor-specific pair output is still diagnostic and does not yet establish directional premiums.
- Transaction MLS gate: **not met for verified transaction MLS coverage**. BCA historical sale rows do not contain transaction MLS identifiers; any current-listing MLS is retained separately and is not counted as a sold-transaction MLS.
- All location premiums remain research-only unless their factor-specific estimate has direction, controls, dispersion, and sufficient evidence.
