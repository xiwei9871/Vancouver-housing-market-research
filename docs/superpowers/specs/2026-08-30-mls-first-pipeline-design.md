# MLS-first listing acquisition pipeline

## Status

Approved by the project owner on 2026-08-30. This document changes the
listing-source order for the Greater Vancouver detached-house research system;
it does not authorize broad crawling or change the official-record trust gate.

## Decision

The acquisition stage uses the following ordered source roles:

1. `MLS_PRIMARY`: a directly supplied MLS export, feed, or injected adapter;
2. `REALTOR_CA_PUBLIC`: a public REALTOR.ca snapshot used to discover and
   cross-check the MLS number and current listing fields;
3. `ZOLO_PUBLIC`: a public Zolo snapshot used as a second independent
   cross-check and history signal.

The MLS primary adapter is explicit and disabled until an actual authorized
transport or local MLS export is supplied. Its unavailable state is recorded
in the run summary. Public snapshots may still run after that state, but the
run is marked as using a fallback and never presents public data as a direct
MLS feed.

Official municipal, title, assessment, zoning, permit, school, and road
records remain separate enrichment sources. A listing page cannot establish a
PID, legal description, approved use, entitlement, or municipal carrying cost
merely by containing that field in its payload.

## Identity and merge rules

An MLS number is the canonical cross-source listing key: REALTOR.ca and Zolo
rows for the same `mls_number` are expected to be checked against each other.
It is not the property identity. Property identity remains PID/PID-set first,
then normalized civic address plus municipality, so relists and historical
MLS numbers can map to one property while their listing history is preserved.

All source rows and field-level provenance are retained. The normalized
projection is sparse-merge based: a later non-empty value may fill an unknown
field, but a material disagreement creates an open `DataConflict` with both
source URLs and values. Material fields include title/address/lot/zoning facts
and listing facts such as MLS number, ask, original ask, listing date, DOM,
finished area, bedrooms, bathrooms, and year built. Derived valuation and
score fields are not calculated as reliable conclusions while a material
conflict remains unresolved.

For `MLS_PRIMARY`, `REALTOR_CA_PUBLIC`, and `ZOLO_PUBLIC` rows, legal-looking
PID, legal-description, ownership, approved-use, zoning, and entitlement fields
are retained as raw provenance claims but excluded from the normalized legal
projection until an official source confirms them. A provisional special-title
claim (for example cooperative share) remains visible in the special-situation
queue, while an ordinary freehold claim remains `NEED_VERIFICATION` and cannot
pass the title gate.

MLS aliases are also append-only: every observed MLS number is retained with
its source/run/timestamp, including a lower-priority or historical value that
does not win the current projection. A reused MLS number is not allowed to
merge different addresses or disjoint PIDs; it creates separate identities and
an open identity conflict.

## Source policy

The source registry records the role, priority, access method, authorization
state, evidence state, and retrieval metadata for every source. The run report
must show:

- the configured priority order;
- whether an MLS primary source was configured and available;
- whether public cross-check data was used as fallback;
- each source run status, row count, warning, and error;
- unresolved conflicts and the verification queue.

There is no silent fallback. A missing direct MLS feed is a coverage gap, not
an assertion that REALTOR.ca or Zolo is equivalent to private MLS access.

The pipeline may be run in an explicit provisional single-source mode for
bounded real-data testing. In that mode a traceable public listing observation
is retained and surfaced for research, but it is marked
`PROVISIONAL_SINGLE_SOURCE`, creates a manual verification item, and cannot
clear the title/verification gate or become an A/site-visit candidate. This is
an explicit policy switch, not an equivalence claim about public pages and a
direct MLS feed.

## Current read-only profile

`config/research-mls-first.yaml` extends the deterministic baseline config and
points acquisition at timestamped public snapshots under
`data/research/sources/`. It contains a disabled `MLS_PRIMARY` slot, then the
real 2026-08-30 REALTOR.ca and Zolo snapshots collected for a small validation
set. Snapshot capture time and version are stored in each source definition so
reruns do not replace the original observation time. It intentionally leaves official enrichment and sold comparables empty
for these web observations; no official fact is fabricated from listing copy.

The profile is a bounded validation input, not a production market crawler.
The next acquisition phase can replace the disabled MLS adapter with an
authorized adapter or a refreshed export without changing the merge, scoring,
storage, or report contracts.

## Acceptance criteria

- configured adapters execute in MLS, REALTOR.ca, Zolo priority order;
- a disabled/unavailable MLS primary source is visible in JSON, SQLite, and
  Markdown report output;
- public rows retain `mls_number` and source-specific provenance;
- same-address public rows merge to one property when title identity is not
  contradictory, while different PIDs or material field values enter the
  conflict queue;
- the real snapshot run is read-only, bounded, repeatable, and produces a
  report with explicit `NEED_VERIFICATION`/special-title outcomes;
- existing Phase 1 and deterministic fixture regression tests remain green.
