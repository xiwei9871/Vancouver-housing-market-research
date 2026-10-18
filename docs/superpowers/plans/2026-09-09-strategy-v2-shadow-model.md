# Strategy V2 Shadow Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a parallel, ledger-first V2 research artifact for all 166 saved Vancouver listings while preserving V1 as a read-only legacy reference and postponing final rankings.

**Architecture:** Add one focused strategy-V2 module that reads the existing official-enrichment cards, performs asset classification and non-compensatory gates, and emits typed holding, location, development-scenario, and acquisition ledgers. Keep V1 screening and reports untouched; expose V2 through a dedicated script and date-stamped artifacts.

**Tech Stack:** Python 3.11, Pydantic v2, existing YAML configuration, JSON/CSV/Markdown artifacts, pytest.

---

### Task 1: Freeze the V1 reference and add V2 configuration

**Files:**
- Create: `config/strategy-v2-shadow.yaml`
- Create: `tests/test_strategy_v2_shadow_config.py`
- Test: `data/research/normalized/portfolio-analysis-2026-09-09.json`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

import yaml


def test_strategy_v2_config_freezes_legacy_reference_and_disables_ranking():
    path = Path("config/strategy-v2-shadow.yaml")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["schema_version"] == "strategy-v2-shadow-v1"
    assert config["ranking"]["enabled"] is False
    assert config["legacy_reference"]["analysis_artifact"].endswith(
        "portfolio-analysis-2026-09-09.json"
    )
    assert config["calibration_split"]["random_seed"] == 20260909
    assert config["calibration_split"]["calibration_count"] == 120
    assert config["calibration_split"]["blind_test_count"] == 46
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_config.py`  
Expected: FAIL because `config/strategy-v2-shadow.yaml` does not exist.

- [ ] **Step 3: Add the minimal configuration**

```yaml
schema_version: strategy-v2-shadow-v1
ranking:
  enabled: false
legacy_reference:
  analysis_artifact: data/research/normalized/portfolio-analysis-2026-09-09.json
  report_index: reports/properties/2026-09-09/index.md
calibration_split:
  random_seed: 20260909
  calibration_count: 120
  blind_test_count: 46
tracks:
  normal: [HOME_REBUILD, LAND_DEVELOPMENT, PREMIUM_SMALL_LOT, INCOME_HOLD]
  special: [SPECIAL_SITUATION, UNCLASSIFIED_REVIEW]
road_policy:
  direct_ordinary_arterial_current_use_cap: 65
  direct_arterial_bus_truck_current_use_cap: 55
  direct_arterial_truck_intersection_current_use_cap: 45
  unknown_value: UNKNOWN
development:
  include_toa_in_base: false
  include_assembly_in_base: false
  require_confirmed_access_for_base: true
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_config.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add config/strategy-v2-shadow.yaml tests/test_strategy_v2_shadow_config.py && rtk git commit -m "feat: freeze strategy v1 reference for shadow model"`  
If the workspace is not a Git repository, record the files in the acceptance report instead of fabricating a commit.

### Task 2: Define typed V2 ledger models

**Files:**
- Create: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_models.py`

- [ ] **Step 1: Write the failing test**

```python
from northshore_watchdog.research.strategy_v2_shadow import (
    AcquisitionLedger,
    AssetClassification,
    GateFinding,
    HoldingLedger,
    LocationLedger,
    StrategyV2Property,
)


def test_v2_property_keeps_unknowns_and_separates_ledgers():
    row = StrategyV2Property(
        property_key="mls:R1",
        address="TEST STREET",
        asset_classification=AssetClassification(primary_track="HOME_REBUILD"),
        gates=[GateFinding(code="TITLE_CHECK", status="PASS")],
        holding=HoldingLedger(),
        location=LocationLedger(),
        acquisition=AcquisitionLedger(),
    )
    assert row.holding.cash_holding_basis_base is None
    assert row.development_scenarios == []
    assert row.acquisition.base_cash_mos is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_models.py`  
Expected: FAIL because the V2 module and models do not exist.

- [ ] **Step 3: Implement the minimal typed models**

Add Pydantic models with `extra="allow"` only for source-compatible facts and explicit fields for:

```python
class GateFinding(BaseModel):
    code: str
    status: Literal["PASS", "REVIEW", "FAIL"]
    evidence: list[str] = Field(default_factory=list)
    explanation: str = ""

class AssetClassification(BaseModel):
    primary_track: str = "UNCLASSIFIED_REVIEW"
    secondary_tracks: list[str] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    confidence: str = "UNKNOWN"

class HoldingLedger(BaseModel):
    house_utility_band: str = "UNKNOWN"
    holding_condition_band: str = "UNKNOWN"
    survival_capex_low: float | None = None
    survival_capex_base: float | None = None
    survival_capex_high: float | None = None
    cash_holding_basis_low: float | None = None
    cash_holding_basis_base: float | None = None
    cash_holding_basis_high: float | None = None
    house_value_reference: float | None = None
    risks: list[str] = Field(default_factory=list)

class LocationLedger(BaseModel):
    external_provider: dict[str, Any] = Field(default_factory=dict)
    school: dict[str, Any] = Field(default_factory=dict)
    area_baseline_prior: dict[str, Any] = Field(default_factory=dict)
    parcel_facts: dict[str, Any] = Field(default_factory=dict)
    road_adjustment: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)

class DevelopmentScenario(BaseModel):
    scenario_name: Literal["BASE", "CONDITIONAL", "UPSIDE"]
    product_type: str = "UNKNOWN"
    legal_status: str = "UNKNOWN"
    unit_count: float | None = None
    theoretical_area: float | None = None
    realistic_area: float | None = None
    access_status: str = "UNKNOWN"
    exit_value: float | None = None
    total_cost: float | None = None
    ralv: float | None = None
    cash_mos: float | None = None
    blocking_conditions: list[str] = Field(default_factory=list)
    confidence: str = "UNKNOWN"

class AcquisitionLedger(BaseModel):
    asking: float | None = None
    assessment_signals: dict[str, Any] = Field(default_factory=dict)
    normalized_physical_lot_basis: float | None = None
    normalized_lot_basis_per_sqft: float | None = None
    base_cash_mos: float | None = None
    conditional_cash_mos: float | None = None
    upside_cash_mos: float | None = None
    risks: list[str] = Field(default_factory=list)

class StrategyV2Property(BaseModel):
    property_key: str
    address: str
    mls: str | None = None
    asset_classification: AssetClassification
    gates: list[GateFinding]
    holding: HoldingLedger
    location: LocationLedger
    development_scenarios: list[DevelopmentScenario] = Field(default_factory=list)
    acquisition: AcquisitionLedger
    legacy_reference: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_models.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_models.py && rtk git commit -m "feat: add strategy v2 ledger models"`.

### Task 3: Implement asset classification and non-compensatory gates

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_classification.py`

- [ ] **Step 1: Write the failing tests**

```python
from northshore_watchdog.research.strategy_v2_shadow import classify_asset, evaluate_gates


def test_partial_interest_is_special_even_when_lot_is_large(sample_card):
    card = sample_card(
        address="SPECIAL STREET",
        property_type="Single Family",
        ownership_type="Partial Interest",
        lot_area_sqft=6000,
    )
    classification = classify_asset(card)
    gates = evaluate_gates(card)
    assert classification.primary_track == "SPECIAL_SITUATION"
    assert any(item.status == "FAIL" for item in gates)


def test_large_lot_does_not_create_land_development_without_entitlement(sample_card):
    card = sample_card(
        address="LARGE STREET",
        property_type="Single Family",
        lot_area_sqft=6000,
        raw={"official_current_zoning": "UNKNOWN"},
    )
    classification = classify_asset(card)
    assert classification.primary_track != "LAND_DEVELOPMENT"


def test_direct_bus_truck_arterial_is_review_risk_not_deleted(sample_card):
    card = sample_card(
        address="NANAIMO STREET",
        raw={
            "official_road_class": "B",
            "official_distance_to_major_road_m": 34,
            "official_property_fronts_major_road": True,
            "official_bus_route": True,
            "official_truck_route": True,
        },
    )
    gates = evaluate_gates(card)
    assert any(item.code == "DIRECT_ARTERIAL_BUS_TRUCK" and item.status == "REVIEW" for item in gates)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_classification.py`  
Expected: FAIL because the classifier and gate functions do not exist.

- [ ] **Step 3: Implement classification and gates**

Implement deterministic functions that inspect title, property type, description/raw official fields, kitchens/suite signals, lot geometry, zoning, access, road, and condition. Special transaction/title findings must be evaluated before normal-track classification. A direct arterial is `REVIEW`, not an automatic rejection; partial interest and severe title blockers are `FAIL` for normal tracks and route to `SPECIAL_SITUATION`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_classification.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_classification.py && rtk git commit -m "feat: classify shadow model asset tracks and gates"`.

### Task 4: Implement the Holding Ledger without a Reno hard cap

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_holding.py`

- [ ] **Step 1: Write the failing tests**

```python
from northshore_watchdog.research.strategy_v2_shadow import build_holding_ledger


def test_holding_ledger_calculates_chb_from_survival_capex(sample_card):
    card = sample_card(address="HOLD STREET", asking=1_875_000)
    ledger = build_holding_ledger(card)
    assert ledger.survival_capex_base is not None
    assert ledger.cash_holding_basis_base == 1_875_000 + ledger.survival_capex_base


def test_listed_updates_do_not_clear_unknown_major_systems(sample_card):
    card = sample_card(
        address="UPDATED STREET",
        year_built=1950,
        description="New roof, double glazed windows and hot water tank.",
    )
    ledger = build_holding_ledger(card)
    assert ledger.survival_capex_base is not None
    assert any("electrical" in risk or "plumbing" in risk or "heating" in risk for risk in ledger.risks)


def test_holding_ledger_does_not_expose_current_use_score_cap(sample_card):
    ledger = build_holding_ledger(sample_card(address="NO CAP STREET", asking=1_875_000))
    assert not hasattr(ledger, "current_use_score_cap")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_holding.py`  
Expected: FAIL because `build_holding_ledger` does not exist.

- [ ] **Step 3: Implement the ledger**

Use existing listing evidence and current configuration cost items. Subtract only explicitly evidenced completed updates from the relevant survival-cost components. Add a bounded reserve for unresolved major systems on pre-1980 houses. Store low/base/high estimates, holding condition, house utility, and risks. Do not read or apply `current_use_caps`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_holding.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_holding.py && rtk git commit -m "feat: add holding ledger and cash holding basis"`.

### Task 5: Implement the Location Ledger with one road adjustment path

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_location.py`

- [ ] **Step 1: Write the failing tests**

```python
from northshore_watchdog.research.strategy_v2_shadow import build_location_ledger


def test_direct_nanaimo_exposure_is_preserved_as_parcel_risk(sample_card):
    card = sample_card(
        address="4561 NANAIMO STREET",
        raw={
            "official_road_class": "B",
            "official_distance_to_major_road_m": 34,
            "official_property_fronts_major_road": True,
        },
    )
    ledger = build_location_ledger(card)
    assert ledger.parcel_facts["direct_arterial"] is True
    assert ledger.road_adjustment["base_pct"] == 0.10
    assert ledger.road_adjustment["application_count"] == 1


def test_location_provider_missing_stays_unknown(sample_card):
    ledger = build_location_ledger(sample_card(address="NO API STREET"))
    assert ledger.external_provider.get("walk_score") == "UNKNOWN"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_location.py`  
Expected: FAIL because `build_location_ledger` does not exist.

- [ ] **Step 3: Implement the ledger**

Reuse the existing road discount calculator and saved Walk Score fields. Preserve school catchment/quality, location prior metadata, and parcel-specific facts. Store low/base/high road discount and an explicit `application_count = 1`. Do not create a new walkability, quietness, park-access, or amenity composite.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_location.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_location.py && rtk git commit -m "feat: add shadow location ledger"`.

### Task 6: Implement Base/Conditional/Upside development scenarios

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_development.py`

- [ ] **Step 1: Write the failing tests**

```python
from northshore_watchdog.research.strategy_v2_shadow import build_development_scenarios


def test_unknown_rear_access_moves_multiplex_to_conditional(sample_card):
    card = sample_card(
        address="PARK STREET",
        lot_area_sqft=5525,
        frontage_ft=50,
        depth_ft=110.5,
        raw={"official_current_zoning": "R1-1", "official_rear_access": "UNKNOWN"},
    )
    scenarios = build_development_scenarios(card)
    five_unit = [item for item in scenarios if item.unit_count == 5]
    assert five_unit
    assert five_unit[0].scenario_name == "CONDITIONAL"
    assert five_unit[0].ralv is None


def test_toa_is_upside_not_base(sample_card):
    card = sample_card(
        address="TOA STREET",
        raw={"official_current_zoning": "RT-11", "toa_tier": 3, "distance_to_skytrain_m": 520},
    )
    scenarios = build_development_scenarios(card)
    toa = [item for item in scenarios if item.product_type == "TOA_OPTION"]
    assert toa
    assert toa[0].scenario_name == "UPSIDE"


def test_potential_is_not_ralv(sample_card):
    scenarios = build_development_scenarios(sample_card(address="UNKNOWN ECONOMICS STREET"))
    assert all(item.ralv is None for item in scenarios if item.legal_status != "UNKNOWN")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_development.py`  
Expected: FAIL because `build_development_scenarios` does not exist.

- [ ] **Step 3: Implement scenario generation**

Reuse the official zoning rule registry and current redevelopment facts. Generate legal/physical potential independently from economic values. Create scenario rows for confirmed base products, unresolved-access or conditional-entitlement products, and TOA/assembly/rezoning upside. Calculate RALV only when exit and cost inputs are present; otherwise leave it `None` and record the missing inputs.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_development.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_development.py && rtk git commit -m "feat: separate development potential from scenario economics"`.

### Task 7: Implement acquisition ledger and fixed calibration/blind manifests

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_acquisition.py`
- Create: `tests/test_strategy_v2_shadow_split.py`

- [ ] **Step 1: Write the failing tests**

```python
from northshore_watchdog.research.strategy_v2_shadow import (
    build_acquisition_ledger,
    make_calibration_split,
)


def test_acquisition_ledger_keeps_three_mos_values_separate(sample_card):
    card = sample_card(address="ACQUISITION STREET", asking=1_500_000)
    ledger = build_acquisition_ledger(card, holding_basis=1_550_000, scenarios=[])
    assert ledger.base_cash_mos is None
    assert ledger.conditional_cash_mos is None
    assert ledger.upside_cash_mos is None


def test_fixed_split_has_120_calibration_and_46_blind_rows():
    keys = [f"mls:R{i}" for i in range(166)]
    split = make_calibration_split(keys, random_seed=20260909)
    assert len(split["calibration"]) == 120
    assert len(split["blind_test"]) == 46
    assert set(split["calibration"]).isdisjoint(split["blind_test"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_acquisition.py tests/test_strategy_v2_shadow_split.py`  
Expected: FAIL because the ledger and split functions do not exist.

- [ ] **Step 3: Implement acquisition and split functions**

The acquisition ledger must use CHB from the holding ledger, preserve BCA and normalized-lot diagnostics, and derive each MOS only from the matching scenario. The split function must sort stable property keys before applying `random.Random(random_seed).shuffle`, then return disjoint lists with counts and a SHA-256 hash over the canonical JSON representation.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_acquisition.py tests/test_strategy_v2_shadow_split.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_acquisition.py tests/test_strategy_v2_shadow_split.py && rtk git commit -m "feat: add shadow acquisition ledger and blind split"`.

### Task 8: Add the end-to-end V2 shadow runner and projections

**Files:**
- Create: `scripts/run_strategy_v2_shadow.py`
- Create: `tests/test_strategy_v2_shadow_runner.py`
- Modify: `src/northshore_watchdog/research/strategy_v2_shadow.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

from scripts.run_strategy_v2_shadow import run_shadow


def test_shadow_run_writes_166_rows_without_rankings(tmp_path, saved_artifact):
    result = run_shadow(
        input_path=Path(saved_artifact),
        legacy_analysis=Path("data/research/normalized/portfolio-analysis-2026-09-09.json"),
        output_dir=tmp_path,
        observed_at="2026-09-09T00:00:00+00:00",
    )
    payload = json.loads((tmp_path / "strategy-v2-shadow.json").read_text(encoding="utf-8"))
    assert result["property_count"] == 166
    assert len(payload["properties"]) == 166
    assert payload["ranking_status"] == "NOT_RUN"
    assert "rankings" not in payload
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_runner.py`  
Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement the runner**

Load `official_enrichment.cards` from the existing artifact, construct every `StrategyV2Property`, hash the V1 analysis artifact, write JSON/CSV, write split manifest, and render one compact Markdown audit table plus an acceptance report. The runner must assert that the input count is 166 for this run and must not call the V1 screening function or write V1 output paths.

- [ ] **Step 4: Run the test to verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_runner.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add scripts/run_strategy_v2_shadow.py src/northshore_watchdog/research/strategy_v2_shadow.py tests/test_strategy_v2_shadow_runner.py && rtk git commit -m "feat: run strategy v2 shadow model"`.

### Task 9: Add regression tests for the named benchmark cases

**Files:**
- Create: `tests/test_strategy_v2_shadow_benchmarks.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_benchmark_track_and_gate_expectations(shadow_rows):
    rows = {row.address: row for row in shadow_rows}
    nanaimo = rows["4561 NANAIMO STREET"]
    melbourne = rows["5454 MELBOURNE STREET"]
    w10 = rows["3910 W 10TH AVENUE"]
    e18 = rows["1016 E 18TH AVENUE"]

    assert "SPECIAL_SITUATION" not in nanaimo.asset_classification.secondary_tracks
    assert any("DIRECT_ARTERIAL" in gate.code for gate in nanaimo.gates)
    assert any(item.scenario_name == "CONDITIONAL" for item in melbourne.development_scenarios)
    assert w10.holding.cash_holding_basis_base is not None
    assert e18.location.parcel_facts.get("substandard_lot") is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_benchmarks.py`  
Expected: FAIL until the end-to-end artifact and named-case projections are wired.

- [ ] **Step 3: Add the smallest benchmark assertions**

Use stable property keys and official/listing facts from the saved artifact. Assert classifications and scenario statuses, not arbitrary ranks or scores.

- [ ] **Step 4: Run the test to verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_benchmarks.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

Run: `rtk git add tests/test_strategy_v2_shadow_benchmarks.py && rtk git commit -m "test: lock strategy v2 benchmark semantics"`.

### Task 10: Run the full 166-property shadow model and verify V1 isolation

**Files:**
- Create: `reports/strategy-v2-shadow/2026-09-09/audit-table.md`
- Create: `reports/strategy-v2-shadow/2026-09-09/ACCEPTANCE-REPORT.md`
- Create: `data/research/normalized/strategy-v2-shadow-2026-09-09.json`
- Create: `data/research/normalized/strategy-v2-shadow-2026-09-09.csv`
- Create: `data/research/history/strategy-v2-shadow-splits.json`

- [ ] **Step 1: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_shadow_*.py`  
Expected: all new tests pass.

- [ ] **Step 2: Run the shadow model**

Run: `rtk .venv/bin/python scripts/run_strategy_v2_shadow.py --input data/research/normalized/portfolio-official-enrichment-2026-09-07.json --legacy-analysis data/research/normalized/portfolio-analysis-2026-09-09.json --output-date 2026-09-09`  
Expected: 166 properties written, `ranking_status=NOT_RUN`.

- [ ] **Step 3: Verify V1 isolation**

Run: `rtk shasum -a 256 data/research/normalized/portfolio-analysis-2026-09-09.json reports/properties/2026-09-09/index.md` before and after the V2 run.  
Expected: hashes are identical.

- [ ] **Step 4: Run the complete test suite**

Run: `rtk .venv/bin/pytest -q`  
Expected: all existing and new tests pass, with no V1 regression.

- [ ] **Step 5: Review the acceptance report**

Check counts for tracks, gate statuses, unknown economics, scenario statuses, and benchmark semantics. The report must explicitly state that no final ranking was generated and that human calibration is the next phase.

- [ ] **Step 6: Commit generated V2 artifacts if the repository tracks artifacts**

Run: `rtk git add config/strategy-v2-shadow.yaml src/northshore_watchdog/research/strategy_v2_shadow.py scripts/run_strategy_v2_shadow.py tests/test_strategy_v2_shadow_*.py data/research/normalized/strategy-v2-shadow-2026-09-09.json data/research/normalized/strategy-v2-shadow-2026-09-09.csv data/research/history/strategy-v2-shadow-splits.json reports/strategy-v2-shadow/2026-09-09 && rtk git commit -m "feat: generate strategy v2 shadow portfolio"`  
If Git is unavailable, preserve the generated artifact paths and hashes in the final handoff.
