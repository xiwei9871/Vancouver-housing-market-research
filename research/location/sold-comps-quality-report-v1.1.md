# Sold Comps Quality Report V1.1

Research window: `2023-09-07` to `2026-09-06`.

## Scope

Target population is City of Vancouver detached freehold completed sales. Asking-only rows, unknown title, out-of-scope municipalities, and conflicts are retained for audit but excluded from statistics.

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

## Coverage among eligible sales

| Field | Coverage |
|---|---:|
| bca | 100.0% |
| neighbourhood | 96.7% |
| coordinates | 96.4% |
| school | 96.8% |
| road | 96.4% |
| zoning | 0.2% |
| lot | 90.9% |
| house | 99.5% |
| mls | 0.4% |
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
- Factor-evidence gate: **not met for automatic production pricing**. School boundary joins are now available, but the current pair output is an unsigned diagnostic and road/zoning coverage remains insufficient for a complete location model.
- All location premiums remain research-only unless their factor-specific estimate has direction, controls, dispersion, and sufficient evidence.
