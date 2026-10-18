# Sold Comps Quality Report V1.2

Research window: `2023-09-07` to `2026-09-06`.

## Scope

Target population is City of Vancouver detached freehold completed sales. Asking-only rows, unknown title, out-of-scope municipalities, and conflicts are retained for audit but excluded from statistics.
Current official zoning, road, and school joins describe the current spatial context of an address; they are not historical as-of-sale attributes unless the source explicitly provides that time version.

## Summary

- `total_observations`: 3106
- `eligible_sales`: 3033
- `conflict_count`: 0
- `outside_target_scope_count`: 58
- `benchmark_count`: 10
- `benchmark_observed_count`: 0

## Sources

- `data/research/raw/location/bca-arcgis-sales-2026-09-06-enriched-spatial.json`
- `data/research/sources/phase2_comparables.json`
- `data/research/normalized/regional-official-enrichment-2026-09-05.json`
- `data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson`
- `data/research/normalized/school-boundaries/school-catchment-53a59061080d5749.json`
- `data/research/raw/location/vancouver-public-streets-2026-09-06.json`
- `data/research/raw/location/vancouver-zoning-2026-09-06.json`

## Coverage among eligible sales

| Field | Coverage |
|---|---:|
| bca | 100.0% |
| neighbourhood | 96.7% |
| coordinates | 96.4% |
| school | 96.8% |
| road | 96.4% |
| zoning | 96.3% |
| current_official_zoning | 96.3% |
| lot | 90.9% |
| house | 99.5% |
| mls | 0.0% |
| verified_transaction_mls | 0.0% |
| current_listing_mls | 0.4% |
| sale_date | 100.0% |
| sale_price | 100.0% |

## Samples by neighbourhood

| Neighbourhood | Eligible sales |
|---|---:|
| Arbutus Ridge | 80 |
| Collingwood | 3 |
| Dunbar-Southlands | 221 |
| Fairview | 5 |
| Grandview-Woodland | 160 |
| Hastings-Sunrise | 364 |
| Kensington-Cedar Cottage | 417 |
| Kerrisdale | 101 |
| Killarney | 127 |
| Kitsilano | 148 |
| Knight | 1 |
| Main | 1 |
| Marpole | 114 |
| Mount Pleasant | 23 |
| Oakridge | 48 |
| Renfrew | 1 |
| Renfrew-Collingwood | 310 |
| Riley Park | 194 |
| Shaughnessy | 11 |
| South Cambie | 36 |
| Strathcona | 20 |
| Sunset | 216 |
| Victoria-Fraserview | 216 |
| West Point Grey | 115 |

## Gates

- Sold-database size gate (`>=300` eligible sales): **MET**.
- Factor-evidence gate: **not met for automatic production pricing**. Current official zoning, road, and school spatial joins are available where coordinates can be matched, but factor-specific pair output is still diagnostic and does not yet establish directional premiums.
- Transaction MLS gate: **not met for verified transaction MLS coverage**. BCA historical sale rows do not contain transaction MLS identifiers; any current-listing MLS is retained separately and is not counted as a sold-transaction MLS.
- All location premiums remain research-only unless their factor-specific estimate has direction, controls, dispersion, and sufficient evidence.
