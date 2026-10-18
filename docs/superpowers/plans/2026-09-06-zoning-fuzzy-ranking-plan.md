# Zoning Fuzzy Matching and Ranking Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve official Vancouver zoning records through auditable address variants and rescore the 166 saved listings using physical-lot and existing-house adjustments.

**Architecture:** Keep official identity strict after retrieval: generate canonical address variants and a constrained prefix query, require one parcel with geometry, then query the official zoning polygon. For scoring, preserve BCA discount as an independent signal, adjust the regional standard value for lot and house differences, and expose current-use, dual-value, and redevelopment rankings separately.

**Tech Stack:** Python, Pydantic, pytest, ArcGIS REST JSON, YAML configuration, cached local enrichment artifacts.

---

### Task 1: Add zoning address-variant regression tests

**Files:**
- Modify: `tests/test_research_official_enrichment.py`

- [ ] Write tests proving `AVENUE` becomes the City's `AV` form, leading/trailing direction variants are generated, and the zoning adapter accepts a unique canonical/prefix result while preserving ambiguity.
- [ ] Run the focused tests and confirm they fail because the current zoning request uses one strict equality predicate.

### Task 2: Implement constrained official zoning lookup

**Files:**
- Modify: `src/northshore_watchdog/research/official_enrichment.py`

- [ ] Generate deterministic suffix and direction variants.
- [ ] Query the official address layer with exact variants plus a constrained `LIKE` prefix.
- [ ] Require one unique parcel with geometry; return `AMBIGUOUS` for multiple candidates.
- [ ] Record the query/match method in provenance and continue to the official zoning polygon.

### Task 3: Add physical-normalization scoring tests

**Files:**
- Modify: `tests/test_research_screening.py`

- [ ] Add tests proving lot area adjusts only the land component, house features adjust only the house component, and BCA discount remains separately visible.
- [ ] Add a 3910-vs-2012 regression asserting the standard-lot/view candidate has stronger normalized land and dual signals than the 3,000 sqft candidate at similar asking prices.
- [ ] Run focused tests and confirm the new assertions fail before scoring implementation.

### Task 4: Implement physical lot and existing-house normalization

**Files:**
- Modify: `src/northshore_watchdog/research/screening.py`
- Modify: `config/research-discovery.yaml`
- Modify: `src/northshore_watchdog/research/discovery_report.py` if new fields need report output

- [ ] Add standard-lot and house reference configuration.
- [ ] Calculate market-land-share prior from regional configuration/BCA signals without treating BCA improvement as market value.
- [ ] Calculate physical-adjusted regional expected value, normalized lot basis, and house-value/holding-use components.
- [ ] Use the new price signal in Current Use, Dual Value, and Redevelopment rankings without double counting location residual or BCA discount.

### Task 5: Re-enrich and rescore the saved 166-listing artifact

**Files:**
- Create: versioned normalized enrichment artifact and discovery report under existing data/report directories

- [ ] Run official enrichment with the repaired zoning join against the cached listing detail data.
- [ ] Generate Current Use Top 10, Dual Value Top 10, and Redevelopment Top 10.
- [ ] Inspect 3910 W 10th, 2012 York, and zoning coverage totals.

### Task 6: Full verification

- [ ] Run the focused zoning and screening tests.
- [ ] Run the complete test suite.
- [ ] Report actual zoning coverage, ranking changes, remaining unmatched/ambiguous records, and any limitations.
