# School and Regional Discovery Enrichment Design

## Goal

Make official school catchment data available before scoring, then run listing discovery through independent regional price presets so Vancouver West is not lost inside a broad $1.0M–$1.8M scan.

## Scope

This change covers two upstream concerns only:

1. official school catchment and school-performance enrichment;
2. independent regional listing presets and merged, auditable results.

It does not change Current Use, Redevelopment, Dual Value, or Investment weights.

## Data flow

```text
regional native filters
  -> per-preset validated cards
  -> MLS/address deduplication with source_presets
  -> official school catchment
  -> official FSA performance join
  -> school coverage audit
  -> scoring
```

The catchment adapter owns its own public collection method. The performance adapter owns a separate public method that downloads the official DataBC FSA dataset and joins it to the catchment names. A failed or unmatched official lookup remains explicit evidence and never becomes a guessed school.

## Vancouver school lookup

For Vancouver, the adapter resolves an official City address polygon, downloads the VSB elementary and secondary boundary ZIP files, and matches the address geometry center to the published polygons. The result stores official catchment names, district, source URL, retrieval timestamp, source version, query scope, and raw snapshots.

## Regional presets

The existing independent presets remain the source of truth:

- Vancouver West: $1.5M–$2.0M
- Vancouver East: $1.0M–$1.6M
- Central Core: the configured central range
- South Vancouver: the configured south range

Each preset must be validated independently. The merged run retains every passing card, deduplicates by MLS first and normalized address second, and records which presets produced each card. A broad scan may remain for audit, but it is not the regional ranking input.

## Failure handling

Adapter failures, missing boundaries, ambiguous address matches, and absent official school names are recorded per property. The run continues for other properties. School coverage reports elementary and secondary success separately and lists failure reasons.

## Acceptance

- adapter binding tests pass;
- official Vancouver catchment data is non-zero for real input cards;
- school coverage is no longer uniformly `UNKNOWN`;
- each regional preset has an independent PASS/FAIL result;
- merged cards retain preset provenance and deduplicate deterministically;
- existing scoring tests pass unchanged because score weights are not part of this change.
