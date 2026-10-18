# Filter-first Automated Listing Discovery Design

**Date:** 2026-09-03  
**Scope:** Phase 11A, Stage 0–4 only  
**Status:** Design approved by the user in the current project discussion

## Goal

Find a small, usable set of current Greater Vancouver detached-house candidates every day without importing or opening the full market. The discovery layer must use the selected source's native search filters first, verify that those filters actually took effect, extract only result-card fields, and hand at most a bounded shortlist to the existing property-intelligence pipeline.

The primary KPI is whether a configured market preset produces a reliable shortlist of approximately 5–10 listings. Data completeness, zoning, assessment, school, comparables, redevelopment scenarios, and final investment scoring are downstream concerns.

## Non-goals

This phase does not:

- crawl or open details for the unfiltered market;
- calculate assessment discounts, sold comparable value, or scores;
- infer title, zoning, approved use, or redevelopment entitlement from listing copy;
- reconcile every field across REALTOR.ca, Zolo, REW, and other sources;
- implement duplex/multiplex economics;
- bypass access controls, CAPTCHA, robots restrictions, or source terms;
- claim complete MLS-market recall.

## Stage definitions

The stages follow the user's actual operating sequence:

### Stage 0 — Open search surface

Select a configured source, municipality/market bucket, property-type search surface, and current-listing view. No listing records are acquired at this stage.

### Stage 1 — Build a search preset

Compile the desired project-level filters into a source-neutral `DiscoveryPreset`. Map those filters to the selected site's capabilities. This stage prepares a URL or browser action plan; it does not open listing detail pages or import the unfiltered result set.

### Stage 2 — Execute native source filters

Submit the mapped filters through the source's permitted UI, saved-search alert, or authorized export path. Capture the result count, the applied filter state when visible, and a sample of the first cards. The run must fail closed if the filter state cannot be verified.

### Stage 3 — Extract filtered result cards

Parse only the filtered search-result cards. The discovery schema is intentionally small:

```text
source
mls
address
price
beds
baths
living_sqft
lot_sqft / lot_dimensions (if visible)
property_type
title_type (if visible)
listing_status
dom (if visible)
listing_url
observed_at
```

No assessment, zoning, school, road, FSR, permit, sold-history, or redevelopment fields are required here.

### Stage 4 — Lightweight shortlist

Apply only defensive card-level filters that the source could not apply natively, normalize values, deduplicate, flag possible duplicates, and rank by simple configurable discovery signals. Produce a bounded shortlist for optional human review. The default output limit is 10 candidates across the selected run, with per-bucket limits configurable.

## Desired filters and source capability mapping

The project defines a desired preset once. Each source declares which fields it can apply natively and how the UI represents them.

Example desired preset:

```yaml
market_bucket: vancouver_west
status: ACTIVE
property_type: DETACHED_HOUSE
ownership: FREEHOLD
minimum_bedrooms: 2
minimum_bathrooms: 1
minimum_finished_sqft: 1250
price_range: [1000000, 1800000]
```

A source may map that to:

```yaml
native_filters:
  property_type: house
  minimum_bedrooms: 2
  minimum_bathrooms: 1
  price_range: [1500000, 2000000]
post_filters:
  minimum_bathrooms: 1
  minimum_finished_sqft: 1250
  ownership: FREEHOLD
```

The mapping must be recorded in the run result. A weaker native filter is acceptable only when the returned result set is still bounded and the missing constraint can be checked from card fields. Unsupported constraints must never silently disappear; they produce an explicit verification/error state rather than an apparently complete candidate set.

Lot width, lot depth, lot area, and redevelopment potential are not default discovery rejection gates in this phase. If a lot field is visible, it may be used as a soft shortlist signal or a configurable card-level filter. Detailed lot and development decisions belong to Property Intelligence. Small lots must be tagged rather than silently discarded unless a future preset explicitly opts into a hard lot-area gate.

## Filter validation and fail-closed rules

After native filter execution, the adapter performs this sequence:

```text
expected_filters
    ↓
execute native filters
    ↓
read result count and applied-state evidence
    ↓
extract first N cards (default N = 5)
    ↓
validate known card values against the native filter rules
    ↓
PASS → Stage 3
FAIL → stop the source run
```

The following conditions stop the run before bulk card ingestion:

- `FILTER_NOT_APPLIED`: a known sampled card violates a filter that the source claims to have applied natively; for example, a `$3.5M` card appears after a native `$2.0M` cap, or a two-bedroom card appears after a native `3+ bedrooms` filter;
- `FILTER_VALIDATION_INCONCLUSIVE`: the source gives neither reliable applied-state evidence nor enough card data to test the expected filters;
- `RESULT_SET_TOO_LARGE`: the reported result count exceeds `max_result_cards` (default 100), or the parser encounters that many cards when no count is exposed;
- `SOURCE_BLOCKED`: the source returns an access challenge, policy block, CAPTCHA, or equivalent non-result page;
- `UNSUPPORTED_FILTER`: a desired constraint is neither applied natively nor checkable from result-card fields;
- `PARSER_ERROR`: the page structure cannot produce a trustworthy card sample.

After native-filter validation passes, known violations of `post_filters` are removed from the candidate set with a reason code such as `POST_FILTER_EXCLUDED`. They do not invalidate the source run. Unknown post-filter values remain `UNKNOWN` and are routed for later verification. Unknown native-filter values in the sample make validation inconclusive when the source provides no independent applied-state evidence. A known native-filter violation is sufficient to fail closed; the adapter must not continue to pagination or detail requests after failure.

Pagination is allowed only over the already-filtered result set and only until the configured card cap. The current REALTOR.ca experiment uses the fixed Vancouver rectangle and a 500-card safety cap. There is no fallback to unfiltered pagination.

## Deduplication policy

Discovery uses conservative identity rules:

1. exact MLS number is the preferred listing identity;
2. normalized address is the fallback when MLS is absent;
3. the same MLS observed in several sources becomes one candidate with source observations retained;
4. the same normalized address with different MLS numbers remains as separate listing observations and receives `POSSIBLE_DUPLICATE`;
5. discovery does not decide which stale/current observation is correct;
6. no field-level reconciliation is required before Stage 4.

Existing property-level identity and historical storage remain downstream responsibilities. Discovery preserves each source URL, source label, raw snapshot reference, and observation time.

## Lightweight shortlist ranking

This is not the final property score. It is a transparent ordering aid applied only after filter validation:

- price within the configured core range;
- known bedrooms/bathrooms/finished area;
- visible lot area or dimensions;
- recent listing or price-change signal when available;
- data completeness for the discovery fields;
- source and duplicate penalties.

Every output must include the reason codes and the fields used for ordering. A human may replace the automatic shortlist before Stage 5 detail research.

## Source and access boundary

The implementation supports these discovery handoffs:

- user-visible saved-search or client-portal alerts;
- manually captured filtered search pages;
- user-supplied listing/search URLs;
- authorized CSV/JSON/MLS exports;
- a permitted browser adapter whose source-specific access policy allows the operation.

The existing broad Scrapy/Playwright crawl path is not the default discovery path. It remains a separately reported legacy/experimental capability and must not be used as a fallback when native filtering fails. A 403, challenge, or unverified filter result is a bounded failure, not permission to widen the crawl.

## Reuse and boundaries in the repository

Reuse:

- existing source registry and provenance envelope;
- existing search-page snapshot and email-alert parsers;
- existing address/MLS normalization helpers;
- existing append-only source-run and observation storage;
- existing weekly report plumbing where it can consume discovery summaries.

Add a focused discovery layer rather than changing the PropertyRecord intelligence schema:

- a source-neutral preset/result model;
- source capability mapping and filter validation;
- bounded card extraction and dedupe;
- a discovery CLI entry point and config profile;
- discovery-specific tests and a sample report section.

The discovery layer hands `PropertyRecord`-compatible listing observations to the existing acquisition boundary only after Stage 2 validation and Stage 4 limiting. It does not invoke enrichment, comps, redevelopment, or final scoring.

## Acceptance criteria

Phase 11A is accepted when:

1. Each configured market preset can be compiled into a source-specific filter plan without opening listing details.
2. The run records expected filters, native filters, post-filters, result count, sample-card validation, and source provenance.
3. A sampled card that violates a claimed native filter produces `FILTER_NOT_APPLIED` and zero candidate output for that source; a card that only violates a documented post-filter is excluded locally without invalidating the source.
4. A result count over 100 produces `RESULT_SET_TOO_LARGE` and no bulk ingestion.
5. Missing optional card fields remain `UNKNOWN`; they do not become fabricated values.
6. Cards are deduplicated by exact MLS first and normalized address second, while address/MLS disagreements are flagged rather than reconciled.
7. No detail URL is requested during Stage 0–4.
8. The final discovery output is bounded to 5–10 review candidates per configured run unless an explicit configuration overrides the limit.
9. Existing Phase 1–10 tests remain green.
10. The report clearly distinguishes discovery success from market completeness; no full-market recall claim is emitted.
