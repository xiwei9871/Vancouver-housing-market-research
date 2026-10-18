# Strategy V2 Analysis Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Route the frozen 166-property universe into full-vision, representative-vision, structured-only, and special-review paths so expensive photo analysis is reserved for approximately 25–35 high-potential properties without deleting structured evidence.

**Architecture:** Add a deterministic, configuration-driven router that evaluates only cheap listing fields, photo counts, track classification, and auditable listing-language signals. Reuse the existing track-aware/Pareto shortlist selector only to allocate the visual-analysis budget; it is not a new investment score and does not modify V1 or Phase 2A scoring. A standalone runner writes a date-stamped JSON/CSV decision table and Markdown audit report.

**Tech Stack:** Python 3, Pydantic models, YAML configuration, CSV/JSON local artifacts, pytest.

---

### Task 1: Define routing rules and regression tests

**Files:**
- Create: `tests/test_strategy_v2_analysis_router.py`
- Modify: `config/strategy-v2-phase-2a.yaml`
- Modify: `tests/test_strategy_v2_phase_2a_config.py`

- [ ] **Step 1: Write failing tests for the cheap routing contract**

  Cover these behaviors with small mapping fixtures:

  ```python
  def test_core_home_candidate_is_full_visual_eligible():
      decision = classify_visual_eligibility({
          "property_key": "mls:R1",
          "primary_track": "HOME_REBUILD",
          "lot_area_sqft": 4026,
          "bedrooms": 3,
          "bathrooms": 2,
          "photo_count": 12,
          "description": "quiet residential home",
      })
      assert decision.visual_eligibility == "FULL_POOL"
  ```

  Also test: fewer than three photos yields `STRUCTURED_ONLY`; fewer than
  three bedrooms or two bathrooms yields no normal Home visual eligibility;
  a small premium-lot/development exception yields `REPRESENTATIVE_POOL`;
  strong land-only language yields `STRUCTURED_ONLY`; ordinary “rebuild
  opportunity” language does not remove a qualifying 3/2 home; and selected
  full/representative routes set bounded photo budgets.

- [ ] **Step 2: Run the focused tests and confirm the expected missing-module failure**

  Run:

  ```bash
  rtk .venv/bin/pytest tests/test_strategy_v2_analysis_router.py -q
  ```

  Expected: collection or import failure because the router module does not
  yet exist.

- [ ] **Step 3: Add the routing configuration assertions**

  Add an `analysis_routing` section to the Phase 2A YAML with the frozen
  thresholds: minimum 3 bedrooms, minimum 2 bathrooms, minimum 3,000 sqft
  for normal Home eligibility, minimum 3 photos for any visual route,
  minimum 6 photos for full visual eligibility, representative budget of 3
  photos, and the existing 25–35/20–5–5 track allocation.

- [ ] **Step 4: Run the config test and confirm it fails until implementation/config is complete**

  Run:

  ```bash
  rtk .venv/bin/pytest tests/test_strategy_v2_phase_2a_config.py -q
  ```

  Expected: failure naming the not-yet-present `analysis_routing` keys.

### Task 2: Implement the deterministic router

**Files:**
- Create: `src/northshore_watchdog/research/strategy_v2_analysis_router.py`
- Modify: `tests/test_strategy_v2_analysis_router.py`

- [ ] **Step 1: Implement the minimal typed decision model and parser helpers**

  Define `AnalysisRoutingDecision` with these fields:

  ```python
  property_key
  primary_track
  visual_eligibility  # FULL_POOL / REPRESENTATIVE_POOL / STRUCTURED_ONLY / SPECIAL_REVIEW
  analysis_route      # FULL_VISION / REPRESENTATIVE_VISION / STRUCTURED_ONLY / SPECIAL_REVIEW
  photo_count
  unique_photo_count
  lot_area_sqft
  bedrooms
  bathrooms
  home_fit
  development_text_signal  # NONE / GENERAL / LAND_DOMINANT
  selected_for_visual
  visual_budget_images
  flags
  reasons
  selection_reasons
  ```

  Numeric parsing must treat blank, `UNKNOWN`, invalid, and NaN values as
  unknown rather than zero.

- [ ] **Step 2: Implement the hard routing rules**

  Apply rules in this order:

  1. `SPECIAL_SITUATION` becomes `SPECIAL_REVIEW` and never consumes a
     normal visual slot.
  2. Fewer than three usable photos becomes `STRUCTURED_ONLY`.
  3. Strong land-dominant wording becomes `STRUCTURED_ONLY` when the house
     is not a normal Home fit; a qualifying home remains at most a
     representative exception so land language cannot silently erase holding
     evidence.
  4. A normal Home fit requires `bedrooms >= 3`, `bathrooms >= 2`, and
     `lot_area_sqft >= 3000`.
  5. A qualifying home with at least six photos enters `FULL_POOL`; a
     qualifying home with three to five photos enters `REPRESENTATIVE_POOL`.
  6. A failing Home fit enters `REPRESENTATIVE_POOL` only for a premium-small-
     lot/development exception with at least three photos; otherwise it is
     `STRUCTURED_ONLY`.

  The listing-language classifier must distinguish strong land-dominant
  phrases (`land value only`, `building lot`, `tear down`, `no value in the
  house`) from general development mentions (`rebuild`, `redevelopment`,
  `developer`, `future development`). The latter is a routing signal, not a
  rejection.

- [ ] **Step 3: Implement selected-route application**

  Add a function that applies the shortlist selection to an eligibility
  decision without promoting an ineligible property:

  ```python
  def apply_visual_selection(
      decision: AnalysisRoutingDecision,
      *,
      selected: bool,
      unique_photo_count: int | None = None,
      selection_reasons: Sequence[str] = (),
  ) -> AnalysisRoutingDecision:
      ...
  ```

  Full routes use all unique local photos; representative routes use
  `min(3, unique_photo_count)`; structured and special routes use zero.

- [ ] **Step 4: Run the focused tests and then the full existing test suite**

  Run:

  ```bash
  rtk .venv/bin/pytest tests/test_strategy_v2_analysis_router.py -q
  rtk .venv/bin/pytest -q
  ```

  Expected: focused router tests pass and the pre-existing suite remains
  green.

### Task 3: Add the 166-property routing runner and audit artifacts

**Files:**
- Create: `scripts/run_strategy_v2_analysis_router.py`
- Create: `tests/test_strategy_v2_analysis_router_runner.py`

- [ ] **Step 1: Write the runner contract test**

  Invoke `run_analysis_router` against the saved 166-row CSV and local photo
  manifests in a temporary output directory. Assert that it writes one row
  per property, does not mutate the existing Phase 2A CSV, records a route
  for every property, and keeps the selected visual pool inside the configured
  target range unless protected exceptions explain an overflow.

- [ ] **Step 2: Run the runner test and confirm the expected missing-script failure**

  Run:

  ```bash
  rtk .venv/bin/pytest tests/test_strategy_v2_analysis_router_runner.py -q
  ```

  Expected: import failure because the runner does not yet exist.

- [ ] **Step 3: Implement local input joins**

  Read the existing Phase 2A CSV as the 166-row base, join listing
  descriptions from the frozen official-enrichment candidate JSON by MLS or
  address, and read `unique_photo_count` from the local photo manifest with a
  fallback to the CSV photo count. No network call and no visual API call are
  allowed.

- [ ] **Step 4: Select the visual budget within track**

  Pass only `FULL_POOL` and `REPRESENTATIVE_POOL` rows to the existing
  `select_shortlist` helper with the configured 20/5/5 targets and 25–35
  range. This reuses the already-tested Pareto/exception protection and keeps
  selection separate from the router’s hard rules. Copy its selection reasons
  into each final routing decision.

- [ ] **Step 5: Write date-stamped artifacts**

  Write:

  ```text
  data/research/normalized/strategy-v2-analysis-routing-YYYY-MM-DD.json
  data/research/normalized/strategy-v2-analysis-routing-YYYY-MM-DD.csv
  reports/strategy-v2-analysis-routing/YYYY-MM-DD/routing-report.md
  ```

  The report must include total counts, counts by route/track/reason, photo
  budgets, the selected property list, and explicit notes that `STRUCTURED_ONLY`
  means “no visual call”, not “property rejected”.

- [ ] **Step 6: Run the runner test and inspect the generated counts**

  Run:

  ```bash
  rtk .venv/bin/pytest tests/test_strategy_v2_analysis_router_runner.py -q
  rtk .venv/bin/python scripts/run_strategy_v2_analysis_router.py --output-date 2026-09-12
  ```

  Confirm the report has 166 rows, lists the 18 properties with fewer than
  three photos as structured-only or special, and does not start the visual
  classifier.

### Task 4: Final verification and handoff

**Files:**
- Modify: `reports/strategy-v2-analysis-routing/2026-09-12/routing-report.md` only if the generated report needs a deterministic wording fix.

- [ ] **Step 1: Run the full test suite and compile check**

  Run:

  ```bash
  rtk .venv/bin/pytest -q
  rtk .venv/bin/python -m compileall -q src scripts tests
  ```

- [ ] **Step 2: Verify source isolation and output invariants**

  Check that the existing Phase 2A/V1 files have unchanged SHA-256 values,
  the new JSON/CSV both contain 166 rows, route counts sum to 166, and the
  visual budget is zero for every non-selected property.

- [ ] **Step 3: Report the actual counts and remaining limitations**

  Summarize the selected pool size, full versus representative visual budget,
  structured-only count, special-review count, estimated image-call reduction,
  and the fact that this run intentionally does not invoke the vision API.
