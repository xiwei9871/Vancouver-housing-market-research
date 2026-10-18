# Location Enrichment V2 Design

## Goal

Build a versioned, Walk Score-backed urban-access score for every property without reimplementing walkability, transit, cycling, quietness, greenery, park access, car friendliness, or daycare accessibility from GIS data.

## Provider ownership

- Walk Score is the only active provider and is canonical for `walk`, `transit`, and `bike` on a 0–100 scale.
- Quiet, greenery, parks, car-friendliness, and daycare are not available from the active provider and remain outside this composite.
- School catchments and school quality, area-baseline residuals, and parcel-specific facts remain separate and never enter the external composite twice.

## Canonical scores

`FAMILY_DETACHED_WALK_SCORE_V1` uses frozen weights:

```text
external = walk*.50 + transit*.375 + bike*.125

urban_access = walk*.50 + transit*.375 + bike*.125
```

Urban-access outputs are 0–100. A complete composite requires all three Walk Score inputs. Missing input produces `UNKNOWN`, never zero. Residential Environment is `NOT_AVAILABLE`, not zero and not a ranking penalty.

## Data and history

Each snapshot stores property/address identity, query coordinates, raw provider values, normalized values, provider query timestamps, provider response versions, model name/version, composite outputs, completeness, and confidence. Snapshots are append-only JSON Lines keyed by property, provider dates, and model version. Cached provider responses use a 180-day TTL and are refreshed only when missing, stale, provider changes, or explicitly requested.

## Error handling

- Missing credentials: `NOT_CONFIGURED`.
- Provider has no score: `NO_DATA`.
- Throttling or transient network failure: `FAILED` with error metadata.
- Missing coordinates: `INVALID_REQUEST`.
- No provider failure excludes a property or becomes a zero score.

## Production isolation

This phase builds enrichment, snapshots, benchmark output, provider comparison, and double-counting audit. It does not modify Current Use, Redevelopment, Dual Value, RALV, MOS, school scoring, area residuals, or road-discount scoring. Any legacy custom composite location scorer is documented as deprecated and remains disabled from production ranking.

## Benchmark and acceptance

The six fixed benchmarks are 2876 W 18th, 3974 W 10th, 333 E 13th, 5454 Melbourne, 4561 Nanaimo, and 486 E King Edward. The run must output each raw input, Walk/Transit/Bike, the urban-access composite, residential-environment availability, completeness, and historical delta. Drift is `STABLE` at <=2 points, `REVIEW` above 2 and <=5, and `WARNING` above 5. No weights may be tuned to achieve an expected benchmark order.

## External access constraints

Walk Score requires an API key. The implementation must be fully testable with captured provider payloads and must produce explicit `NOT_CONFIGURED` benchmark results when the credential is unavailable.
