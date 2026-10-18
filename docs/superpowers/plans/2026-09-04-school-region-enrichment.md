# School and Regional Enrichment Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first verification.

**Goal:** Fix official school enrichment and run independent Vancouver regional price presets before scoring.

**Architecture:** Keep school catchment and school performance as separate official adapters with explicit per-property evidence. Run configured discovery presets independently, merge validated cards by MLS/address, retain `source_presets`, then enrich and score the merged set.

**Tech Stack:** Python, Pydantic, YAML, existing official HTTP transport, Playwright native discovery, pytest, Markdown/JSON reports.

---

### Task 1: Lock school adapter ownership

**Files:**
- Modify: `tests/test_research_official_enrichment.py`
- Modify: `src/northshore_watchdog/research/official_enrichment.py`

- [x] Add a test asserting that each school adapter's public `collect_for_records` method is owned by the correct class.
- [x] Run the focused test and verify the current runtime monkey-patch fails the assertion.
- [ ] Move the catchment collection method into `SchoolCatchmentAdapter`.
- [ ] Give `SchoolPerformanceAdapter` a public method that calls only `_collect_school_performance`.
- [ ] Remove the runtime method-swapping assignment.
- [ ] Run all official-enrichment tests.

### Task 2: Real official school enrichment

**Files:**
- Create: `data/research/raw/official-enrichment/school-enrichment-20260904/`
- Create: `reports/discovery/school-enrichment-2026-09-04.md`
- Create: `reports/discovery/school-enrichment-latest.md`

- [ ] Reconstruct the 235 saved listing cards without re-requesting REALTOR.ca.
- [ ] Run Vancouver official catchment lookup in bounded batches.
- [ ] Run the official DataBC FSA join after catchment names are present.
- [ ] Persist raw responses and per-property status/evidence.
- [ ] Report elementary, secondary, FSA, failure, and unknown coverage separately.

### Task 3: Regional preset execution

**Files:**
- Modify: `src/northshore_watchdog/research/discovery.py`
- Modify: `src/northshore_watchdog/research/discovery_pipeline.py`
- Modify: `config/research-discovery.yaml`
- Modify: `tests/test_research_discovery.py`
- Modify: `tests/test_research_discovery_cli.py`

- [ ] Add auditable preset provenance to merged cards.
- [ ] Ensure all selected regional presets execute and validate independently.
- [ ] Keep broad scan separate from regional ranking input.
- [ ] Test deterministic MLS/address deduplication and retained `source_presets`.
- [ ] Test that one preset failure does not erase passing preset candidates.

### Task 4: Regional real-data run and rescoring

**Files:**
- Create: `reports/discovery/regional-run-2026-09-04.md`
- Create: `reports/discovery/regional-run-latest.md`
- Create: `reports/discovery/regional-score-2026-09-04.md`
- Create: `reports/discovery/regional-score-latest.md`
- Create: `reports/discovery/regional-score-2026-09-04.json`

- [ ] Execute Vancouver West, Central Core, East Vancouver, and South Vancouver presets with their configured price ranges.
- [ ] Verify the result set contains actual Westside cards before scoring.
- [ ] Apply official school enrichment to the merged regional cards.
- [ ] Run unchanged scoring code and produce separate Current Use, Redevelopment, Dual Value, and Investment rankings.
- [ ] Record coverage gaps and do not promote low-confidence rows to final recommendations.

### Task 5: Full verification

**Files:**
- No additional production files.

- [ ] Run focused adapter and discovery tests.
- [ ] Run the full pytest suite.
- [ ] Verify report/JSON consistency, regional counts, school coverage, and no score-weight changes.
