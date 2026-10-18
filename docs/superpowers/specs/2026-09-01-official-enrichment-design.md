# Official Direct Enrichment for Greater Vancouver Detached Research

## Status

Approved design for the bounded Phase 2 official-enrichment increment. This
spec covers read-only queries for BC Assessment, municipal zoning, school
catchments, and registered sold history. It does not authorize a broad
listing crawler or start Phase 3 comparable/redevelopment automation.

## Goal

For each property that passes Stage 1 and enters the weekly Stage 2 queue,
query authoritative public sources directly, preserve the raw response and
field-level provenance, and feed only identity-matched, non-conflicting values
into the existing enrichment, confidence, scoring, and report pipeline.

The default run is bounded to 20–30 Stage 2 properties. The limit is
configurable, and a source must fail closed when it reaches a request, robots,
authentication, CAPTCHA, or rate-limit boundary. A failed query produces a
traceable verification item; it never silently loads a fixture or invents a
value.

## Existing context and non-goals

The repository already has:

- `PropertyRecord` with assessment, title, zoning, school, history, road, and
  scoring fields;
- `EnrichmentProvider`, `EnrichmentResult`, `SourceRecord`, and field-level
  trust gates;
- append-only `source_runs`, `source_snapshots`, provenance, conflict,
  enrichment-observation, and weekly-delta storage;
- an MLS-first acquisition order with an injected MLS slot and bounded
  REALTOR.ca/Zolo public cross-checks.

This increment does not replace that acquisition order, claim that public
REALTOR.ca or Zolo is a board MLS entitlement, or implement the later
Comparable Engine and Redevelopment Engine. It only supplies official
enrichment after acquisition.

## Approaches considered

### Configuration-only HTTP parser

One generic JSON/HTML client would be quick to write, but the four source
families have different search, identity, pagination, and JavaScript behavior.
A change to one municipal GIS page would also be difficult to isolate and test.

### Independent official adapters with a shared transport

Each source family gets a small adapter and parser while timeout, request
limits, raw snapshots, retries, and status handling remain shared. This gives
the strongest audit trail and independent failure boundaries, at the cost of
more adapter code.

### Recommended hybrid

Use independent adapters as the core, with ordinary HTTP/JSON/ArcGIS/open-data
queries where stable. Expose a browser/manual fallback for interactive
locators, but represent that result as an explicit verification method and
preserve the official page evidence. The fallback is never an implicit retry
that upgrades an unresolved value.

## Architecture

The enrichment stage becomes:

```text
Stage 1 candidates
  -> bounded official collectors
  -> PID/address identity matching
  -> source-run + raw-snapshot persistence
  -> existing EnrichmentProvider normalization
  -> field conflict detection
  -> confidence and score recalculation
  -> report/watchlist/verification queue
```

Collectors will implement a dedicated batch interface such as
`collect_for_records(records, observed_at)`. They return an enrichment batch
containing source-run metadata, one normalized row per matched property, and
source snapshots. The pipeline persists the source run first, then adapts its
rows through the existing `EnrichmentProvider` path. This reuses current trust
gates and avoids changing the acquisition `SourceAdapter.collect()` contract.

The pipeline will link each resulting `SourceRecord` to both the weekly run and
the persisted source-run ID. The source snapshot table remains the canonical
raw-response index; field provenance points back to it through the source-run
ID and snapshot metadata.

## Source adapters

### BC Assessment

`BCAssessmentAdapter` searches by PID when available and by normalized civic
address otherwise. It records the selected property identity, legal
description, current/previous assessment values, land and improvement values,
property facts, and available recent sales. Sales are represented separately
from listing history and carry an explicit coverage/scope value.

The public Assessment Search is the direct source for this use case. BC
Assessment documents current and previous year assessment fields, PID/legal
description, property facts, and recent sales history. Its FAQ states that
property sales are updated monthly, may be delayed until LTSA registration,
and normally cover the last three full calendar years. The adapter therefore
does not describe the result as a complete transaction history.

Primary references:

- [BC Assessment property lookup](https://www.bcassessment.ca/)
- [BC Assessment property information](https://info.bcassessment.ca/services-and-products/Pages/Understanding-property-assessment-look-ups.aspx)
- [BC Assessment FAQ and sales-history scope](https://info.bcassessment.ca/Faq)

### Municipal zoning

`MunicipalZoningAdapter` routes by municipality and keeps parcel lookup,
zoning-code parsing, and bylaw-rule parsing separate. The initial routing
catalogue is:

- Vancouver: Digital Zoning Map and current zoning/bylaw pages;
- Burnaby: BurnabyMap and official Zoning Bylaw map;
- City of North Vancouver: the City's official property/zoning map;
- District of North Vancouver: GEOweb property viewer, zoning data, and
  Bylaw 3210;
- West Vancouver: WESTMap and the District zoning map/bylaw.

The returned row includes the zoning code, bylaw/version, permitted and
conditional uses, site/frontage/FSR/unit/height/setback/coverage/parking
rules, tree and character constraints, and a query status. A map-derived code
is not expanded into numeric development rights unless the matching official
bylaw rule is also found. `TOA` remains an option flag and never becomes an
automatic entitlement.

Primary references:

- [Vancouver Digital Zoning Map](https://maps.vancouver.ca/portal/home/item.html?id=b44f215c5c3c44cbad0ba1b0c334daaf)
- [Burnaby Zoning Bylaw and official map](https://www.burnaby.ca/our-city/bylaws/zoning-bylaw)
- [District of North Vancouver GEOweb](https://geoweb.dnv.org/properties/)
- [District of North Vancouver zoning data](https://www.geoweb.dnv.org/data/)
- [West Vancouver zoning map](https://westvancouver.ca/business-development/building-development/zoning-map)

### School catchment

`SchoolCatchmentAdapter` routes by municipality/school district. The initial
district mapping is SD39 Vancouver, SD41 Burnaby, SD44 North Vancouver, and
SD45 West Vancouver. It obtains elementary and secondary catchments from the
official locator or official boundary GIS, then separately records distance,
capacity/overflow, programs, current score inputs, and five-year outlook
inputs. A school directory or ranking page alone cannot confirm a catchment.

Primary references:

- [Vancouver School Board school directory/locator](https://vsb.bc.ca/school-directory-and-map)
- [Vancouver School Board catchment boundaries](https://vsb.bc.ca/catchment-boundaries)
- [North Vancouver School District locator](https://www.sd44.ca/Schools/SchoolLocator/Pages/default.aspx)

### Sold history

`SoldHistoryAdapter` consumes the sales portion of the BC Assessment response
and writes `previous_sale_dates`, `previous_sale_prices`, and
`sold_history_sources` with the source scope. A later MLS/VOW/REALTOR.ca/Zolo
history row is retained as a separate observation. The conflict engine compares
date/price pairs and blocks disputed values from comparable and investment
scoring. `listing_date`, `DOM`, price reductions, terminated/relisted events,
and asking prices remain listing-history fields and cannot be treated as sale
prices.

## Identity and matching rules

1. A confirmed PID is the strongest match key.
2. A complete normalized civic address plus municipality is the fallback key.
3. Multiple official search results are `AMBIGUOUS`; no fuzzy choice is made.
4. A differing PID, legal description, or incompatible address is a material
   conflict.
5. Two civic addresses do not imply two titles. `SINGLE_TITLE_MULTI_ADDRESS`
   is set when the official result has one PID and multiple addresses.
6. A source row with no reliable identity is stored as an unmatched observation
   and is not applied to a property.

The provenance record will gain structured optional metadata for
`match_method`, `query_scope`, and `raw_snapshot_path`. This makes it possible
to audit not only what was returned, but how it was linked and what period the
source covers.

## Configuration

The research profile will add an `official_enrichment` section:

```yaml
official_enrichment:
  enabled: true
  stage2_only: true
  max_properties_per_run: 30
  fail_closed: true
  raw_snapshot_dir: data/research/raw/official-enrichment
  sources:
    - bca-assessment
    - municipal-zoning
    - school-catchment
    - sold-history
```

Each source definition supplies its registry ID, source type, official domain,
adapter name, timeout, delay, request cap, parser version, enabled flag, and
manual/browser fallback policy. Municipality and school-district routing are
configuration data, not hard-coded score logic. Source registry metadata is
the trust boundary; a payload cannot change source type, authority, or
evidence status.

The official-query queue is the in-range Stage 1 research/verification queue,
including title-unknown records, because official title/PID lookup is needed to
clear the first gate. The downstream Stage 2 set remains the Stage 1-included
subset; zoning entitlement, comparables, redevelopment, and scoring do not run
for a title-unknown or otherwise gated record.

## Status and error handling

At source level: `SUCCESS`, `NO_MATCH`, `AMBIGUOUS`, `BLOCKED`, `FAILED`, and
`SKIPPED_DISABLED`.

At property level:

- successful unique official matches apply normalized fields;
- no match leaves fields `UNKNOWN`;
- ambiguous identity creates `NEED_VERIFICATION`;
- blocked or failed queries create a persisted verification item and keep the
  last known value with its prior observation timestamp;
- a material source conflict sets `data_conflict` and excludes the disputed
  field from scoring;
- confidence below 70 prevents an A candidate;
- existing hard veto and special-situation rules remain authoritative.

Every attempt is append-only. New weekly observations are added; old source
runs, snapshots, and provenance are not overwritten.

## Test and acceptance criteria

Tests will add deterministic HTML/JSON/ArcGIS fixtures for each parser and
cover:

- assessment current/previous values and BCA sales scope;
- municipality and school-district routing;
- PID-first and address fallback matching;
- multi-address versus multi-title classification;
- sold/listing history separation;
- raw snapshot/source-run persistence and provenance linkage;
- per-run request cap, timeout, block, CAPTCHA/challenge, and partial failure;
- official source trust gates and no fixture fallback;
- PID/legal/address conflicts and score exclusion;
- confidence recalculation, `NEED_VERIFICATION`, and weekly delta history.

Before implementation is considered complete, run one bounded read-only smoke
query for each supported municipality and school district, plus BC Assessment,
using existing Stage 2 examples. Smoke results must report actual endpoint
status and match method; a blocked or interactive source is an explicit
partial result, not a fabricated success.

## Phase 2 acceptance boundary

Phase 2 direct enrichment is accepted when the pipeline can answer, with
provenance or an explicit verification state:

- why asking is below assessment or land assessment;
- whether zoning and approved use are officially confirmed;
- which elementary and secondary catchments apply;
- which sold records are official and what their coverage window is;
- whether the property identity is one title or multiple titles;
- which fields conflict or remain unknown.

This design does not claim that every official website has a stable unattended
API. The system is successful when it preserves that distinction and routes
interactive/blocked cases to verification without lowering data quality.

## Implementation sequence after spec review

1. Add model/provenance fields and adapter result types.
2. Add parser and identity-matching tests first; observe the expected RED
   failures.
3. Implement shared transport, raw snapshot handling, and four adapters.
4. Integrate bounded collectors into the enrichment stage and source registry.
5. Add config and deterministic fixtures.
6. Run focused tests, full tests, compile checks, and bounded smoke queries.
7. Update the Phase 2 report and stop for user review; do not begin Phase 3.
