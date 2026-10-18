# Regional Boundary Enrichment Implementation Plan

> **For agentic workers:** Execute this plan inline in the current session. The user has already authorized implementation and live read-only retrieval.

**Goal:** Reconcile the previous broad Vancouver scan with the new non-overlapping regional presets, cache official school-boundary data by stable region geometry, enrich the saved regional listings from REALTOR.ca detail pages, and produce an auditable report.

**Architecture:** Treat the saved listing scan and school-boundary cache as separate versioned inputs. Region polygons are stable research partitions; school boundaries are refreshed independently. Detail enrichment is bounded to the 166 validated listings already acquired, preserving raw snapshots and unknowns on failures.

**Tech Stack:** Python 3.11+, Pydantic, YAML/JSON, SQLite/JSON artifacts, Playwright through the existing Chrome CDP session, official City/VSB sources where available, Markdown reports.

---

### Task 1: Reconcile scan counts and price presets

**Files:**
- Read: `config/research-discovery.yaml`
- Read: `data/research/normalized/regional-boundary-scan-2026-09-05.json`
- Read: prior broad-scan artifacts under `data/research/normalized/` and `reports/`
- Test: `tests/test_research_regional_analysis.py`

- [ ] Compare the 236-result broad scan against the sum of the regional presets, explicitly identifying price-range and boundary differences.
- [ ] Record the result in the report without treating the two counts as duplicate scans of the same universe.

### Task 2: Add versioned official school-boundary cache

**Files:**
- Create: `src/northshore_watchdog/research/schools.py`
- Create: `scripts/build_school_boundaries.py`
- Create: `data/research/schools/` versioned cache artifact
- Modify: `config/research-discovery.yaml`
- Test: `tests/test_research_schools.py`

- [ ] Write tests for loading a versioned cache, mapping a coordinate to one school boundary, and returning `UNKNOWN` outside or when the source is unavailable.
- [ ] Fetch and store the official VSB catchment export if available, preserving source URL, retrieval time, source hash, and school-year metadata.
- [ ] Keep school-boundary cache versioning independent from listing and market-boundary versions.

### Task 3: Run bounded REALTOR.ca detail enrichment

**Files:**
- Modify: `config/research-discovery.yaml`
- Read/modify only through existing pipeline: `src/northshore_watchdog/research/discovery_browser.py`, `src/northshore_watchdog/research/discovery_pipeline.py`
- Create: `data/research/normalized/regional-detail-enrichment-2026-09-05.json`
- Create: `data/research/raw/discovery/realtor-ca-native-details/`

- [ ] Use the saved 166 validated cards as the input set; do not rescan unrelated listings.
- [ ] Open each listing detail URL through the existing read-only browser path with bounded retries/delays and continue on individual failure.
- [ ] Preserve MLS/address identity, lot dimensions, year built, taxes, description, DOM, and available listing facts; keep unavailable fields as `UNKNOWN`.

### Task 4: Generate and verify the analysis report

**Files:**
- Create: `scripts/analyze_regional_enrichment.py`
- Create: `reports/discovery/regional-detail-enrichment-2026-09-05.md`
- Create: `reports/discovery/regional-detail-enrichment-latest.md`
- Test: `tests/test_research_regional_analysis.py`

- [ ] Report count reconciliation, regional price distributions, enrichment success/failure, lot/house-field coverage, school-cache status, and boundary validation.
- [ ] Do not produce final current-use/redevelopment/investment rankings unless the required official and detail fields are actually present.
- [ ] Run the focused tests, compile check, and complete test suite before claiming completion.
