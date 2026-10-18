# Vancouver Location Valuation Research V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, evidence-aware location valuation research layer that produces the requested versioned artifacts without changing production property scoring.

**Architecture:** Add a focused `location_valuation` module with pure normalization, matching, estimation, and artifact-writing functions. Add a CLI command that consumes local enrichment/history/comparable inputs and writes all research outputs under `research/location/`; sparse data produces explicit low-confidence/unknown results.

**Tech Stack:** Python 3.11, pandas, numpy (via pandas), PyYAML, existing Pydantic/research models, pytest, Markdown/CSV/JSON/YAML artifacts.

---

### Task 1: Add the location research contract and failing tests

**Files:**
- Create: `tests/test_research_location_valuation.py`
- Modify: none

- [ ] **Step 1: Write failing tests for evidence-aware premium records.**

```python
def test_unproven_prior_is_not_applied_to_automatic_value():
    estimate = estimate_premium(
        component="school",
        value=200_000,
        method="CONFIG_PRIOR",
        sample_size=0,
        confidence="PRIOR_ONLY",
    )
    assert estimate.applied_value == 0
    assert estimate.value == 200_000
    assert estimate.automatic_rankable is False
```

- [ ] **Step 2: Write failing tests for the two-account normalization.**

```python
def test_normalized_lot_basis_separates_location_from_local_ralv():
    result = normalize_price(
        asking=1_800_000,
        existing_house_value=400_000,
        school_value=150_000,
        area_value=250_000,
        micro_adjustments={"park": 50_000, "road": -25_000},
        lot_area_sqft=4_000,
    )
    assert result.market_land_basis == 1_400_000
    assert result.total_location_value == 425_000
    assert result.normalized_physical_lot_basis == 975_000
    assert result.normalized_lot_price_per_sqft == 243.75
    assert result.ralv is None
```

- [ ] **Step 3: Write failing tests for school being included only once.**

```python
def test_location_value_adds_school_and_non_school_components_once():
    value = sum_location_value(
        school_value=189_500,
        area_value=220_000,
        micro_values={"view": 40_000, "road": -30_000},
    )
    assert value == 419_500
```

- [ ] **Step 4: Run the focused tests and confirm they fail because the new module is absent.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_valuation.py`

Expected: collection/import failure for `northshore_watchdog.research.location_valuation`.

### Task 2: Implement observations, evidence records, and Model A calculations

**Files:**
- Create: `src/northshore_watchdog/research/location_valuation.py`
- Test: `tests/test_research_location_valuation.py`

- [ ] **Step 1: Implement the minimal dataclasses and helpers required by Task 1.**

```python
@dataclass(frozen=True)
class PremiumEstimate:
    component: str
    value: float
    applied_value: float
    method: str
    sample_size: int
    confidence: str
    automatic_rankable: bool

@dataclass(frozen=True)
class NormalizedPrice:
    market_land_basis: float | None
    total_location_value: float
    normalized_physical_lot_basis: float | None
    normalized_lot_price_per_sqft: float | None
    normalized_price_per_buildable_sqft: float | None
    ralv: float | None = None
```

Use `applied_value=0` for `PRIOR_ONLY`, `LOW`, or `UNKNOWN` estimates in automatic calculations while retaining the unadjusted estimate for human review.

- [ ] **Step 2: Add normalized sale/property observation parsing.**

Normalize aliases (`sold`, `sale_price`, `sold_price`; `house_sqft`, `house_size_sqft`; assessment building aliases), parse dates/prices, preserve `source_url`, `source_type`, `source_registry_id`, and identify whether a row is an actual sale or only an asking/listing observation. Never use asking as a sold observation.

- [ ] **Step 3: Implement school/area/micro component aggregation and physical-lot normalization.**

Keep school inside `total_location_value`; keep RALV fields absent from Model A. Return `None` for basis/per-square-foot metrics when asking, house value, or lot area is missing.

- [ ] **Step 4: Run focused tests and the existing research tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_valuation.py`

Expected: focused tests pass.

Run: `rtk .venv/bin/pytest -q`

Expected: no regressions in the existing suite.

### Task 3: Implement matched pairs and hedonic diagnostics

**Files:**
- Modify: `src/northshore_watchdog/research/location_valuation.py`
- Test: `tests/test_research_location_valuation.py`

- [ ] **Step 1: Add tests for deterministic matched-pair selection.**

```python
def test_matched_pairs_require_same_physical_context_and_differing_factor():
    pairs = find_matched_pairs(rows, factor="school", date_window_days=183)
    assert len(pairs) == 1
    assert pairs[0].factor == "school"
    assert pairs[0].matched is True
```

- [ ] **Step 2: Add tests for sparse hedonic output.**

```python
def test_hedonic_model_reports_insufficient_sample_without_fabricating_coefficients():
    result = fit_hedonic_model(rows[:3], minimum_rows=20)
    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.coefficients == {}
```

- [ ] **Step 3: Implement pair matching with the approved tolerances.**

Use sale date ±183 days, lot ±10%, frontage ±15%, living area ±20%, similar year band/zoning/property type, and require the target factor to differ. Store both row IDs and the matching diagnostics; do not create pairs from duplicate observations of one transaction.

- [ ] **Step 4: Implement a small OLS diagnostic using available numeric/categorical features.**

Use log sale price as the dependent variable, time fixed effects by sale month, and one-hot encoded neighbourhood/school/road flags where present. Detect fewer-than-minimum rows, zero variance, and high pairwise correlation; return status and diagnostics instead of unstable coefficients. No coefficient becomes a production premium automatically.

- [ ] **Step 5: Run focused tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_valuation.py`

Expected: all location tests pass.

### Task 4: Implement artifact generation and CLI integration

**Files:**
- Modify: `src/northshore_watchdog/research/location_valuation.py`
- Modify: `src/northshore_watchdog/cli.py`
- Create: `tests/test_research_location_valuation_artifacts.py`

- [ ] **Step 1: Write failing artifact tests.**

Assert the runner writes exactly the ten requested files, every premium CSV has `value,method,sample_size,confidence`, and the feature schema includes school/area/micro/total_location_value and both normalized land metrics.

- [ ] **Step 2: Implement `run_location_research(...)`.**

Read the current regional enrichment plus local history/comparables/school inputs. Build school, area and micro estimates, matched-pair rows, hedonic diagnostics, and benchmark decompositions. Write deterministic JSON/CSV/YAML/Markdown with observation date and input manifests. Use local official enrichment as evidence and record missing official GIS inputs as explicit limitations.

- [ ] **Step 3: Add CLI command `research-location`.**

Support explicit input/output path options with defaults pointing to the current local files. Print a compact JSON summary containing input counts, output paths, evidence counts, and low-confidence/unknown counts.

- [ ] **Step 4: Run artifact tests and the full existing suite.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_valuation.py tests/test_research_location_valuation_artifacts.py`

Expected: all focused tests pass.

Run: `rtk .venv/bin/pytest -q`

Expected: all existing tests pass.

### Task 5: Run the real local research and review outputs

**Files:**
- Create: `research/location/location-methodology-v1.md`
- Create: `research/location/location-feature-schema-v1.json`
- Create: `research/location/school-premium-v1.csv`
- Create: `research/location/area-baseline-premium-v1.csv`
- Create: `research/location/micro-location-premium-v1.csv`
- Create: `research/location/matched-pairs-v1.csv`
- Create: `research/location/hedonic-model-v1.md`
- Create: `research/location/benchmark-validation-v1.md`
- Create: `research/location/location-model-config-v1.yaml`
- Create: `research/location/RECOMMENDATION.md`

- [ ] **Step 1: Run the CLI against the local 166-property enrichment and existing history/comparable/school files.**

Run: `rtk .venv/bin/python -m northshore_watchdog.cli research-location --input-path data/research/normalized/regional-official-enrichment-2026-09-05.json --history-path data/research/sources/phase2_history.json --comparables-path data/research/sources/phase2_comparables.json --school-path data/research/sources/phase2_schools.json --output-dir research/location`

Expected: JSON summary with 166 property inputs and ten output paths; no production score file is changed.

- [ ] **Step 2: Inspect outputs for fake precision and verify benchmark rows.**

Check that sparse school/area/micro estimates are labeled `PRIOR_ONLY`, `LOW`, or `UNKNOWN`; verify all five benchmarks are present or explicitly reported as missing; verify no RALV/MOS calculation uses normalized physical lot basis.

- [ ] **Step 3: Run final verification.**

Run: `rtk .venv/bin/pytest -q`

Expected: all tests pass with zero failures.

Run: `rtk git diff --check` only if the workspace is later initialized as a Git repository; otherwise use `rtk find research/location -maxdepth 1 -type f` and the artifact test output to verify the generated file set.
