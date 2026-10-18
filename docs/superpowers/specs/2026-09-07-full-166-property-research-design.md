# Full 166-Property Research Design

## Objective

Use the saved 166 Vancouver detached-house listing cards and their cached official enrichment to produce a complete, auditable report for every property. The report must distinguish observed facts, official facts, calculations derived from official rules, estimates, and unknowns. It must not treat missing data as a negative value or silently invent development rights.

## Data flow

```text
regional-official-enrichment JSON
        |
        +-- targeted official refresh for only missing deterministic fields
        |
        +-- normalized portfolio facts
        +-- official zoning-rule registry
        +-- existing unified screening scores
        +-- transparent BCA/location/road/reno calculations
        |
        +-- one Markdown report per listing
        +-- full ranked index and coverage manifest
```

The existing acquisition and screening pipeline remains the source of listing observations and score components. The new portfolio layer is a read-only reporting/replay layer and does not change the meaning of the existing Current Use, Redevelopment, Dual Value, Investment, or confidence scores.

## Zoning and development

The rule registry is versioned by retrieval date and stores the official City schedule URL and bylaw version for every zoning code observed in the portfolio. R1-1 uses the current City frontage/area tiers: 32.8 ft / 3,293.8 sqft permits 3–4 units, 44 ft / 4,994.5 sqft permits 4–5 units, and 49.5 ft / 5,995.5 sqft permits 4–6 units; 7–8 units are rental-only under the current City materials. Multiplex eligibility also records rear vehicle access, single-site, floodplain, tree/heritage, and bedroom requirements. Other codes are classified using their official schedule or explicitly marked `RULE_NOT_FOUND`; no R1-1 rule is copied to another zone.

For each property the report calculates current detached/duplex/multiplex possibilities only when the official rule is available. Theoretical and realistic areas are labelled `DERIVED_FROM_OFFICIAL_RULES` and `ESTIMATE` respectively. TOA is shown as a long-term rezoning option and never as current entitlement.

## Value and renovation

The primary physical-land calculation is:

```text
market_land_basis = asking - official BCA improvement
normalized_lot_basis = asking - official BCA improvement - location residual
normalized_lot_basis_per_sqft = normalized_lot_basis / lot area
```

The BCA improvement is reported as a transparent market-house proxy, not as a claim that BCA equals a market sale decomposition. Holding utility and current-use reno cost remain separate. Reno uses the approved bands: 0, <=30k, <=60k, <=100k, <=150k, and >150k. Listing update language, official permit evidence, and age fallback are shown separately; age alone never proves good maintenance.

Road discount uses the approved initial prior bands and reports the low/base/high percentage and CAD amount. It is a permanent location/exit adjustment, not a repair cost.

## Reports

Every property gets a report with:

1. identity, listing, BCA and price signals;
2. current-use/holding analysis;
3. redevelopment/zoning/scenario analysis;
4. location, school, road and land normalization;
5. estimated economics and assumptions;
6. all unified score components and ranking axes;
7. explicit gaps, provenance status, and an investment thesis.

The index contains all 166 reports, sorted independently by each ranking axis and separated into normal scored, special transaction, and Stage-A excluded groups. No top-10 cap is applied to report generation.

## Error handling

Missing official fields remain `UNKNOWN` and are listed in the completion manifest. A missing optional field does not reduce any score. If a zoning code has no rule entry, its legal scenario output is unknown rather than estimated from a similar code. A malformed individual report must be recorded in the manifest; it must not suppress the other reports.

## Verification

Tests cover exact property identity, coverage audit, zoning threshold calculations, unknown zoning behavior, approved reno bands, BCA/location arithmetic, report sections, and one-report-per-card output. The final acceptance check verifies exactly 166 report files, no duplicate keys, and required sections in every file.
