# Strategy V2 Online CAPEX and Cosmetic Reno Estimation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 2A photo-to-CAPEX overclassification with an auditable, evidence-backed Survival CAPEX and Cosmetic Reno ledger for all 166 properties.

**Architecture:** Keep the existing photo manifest and `OnlineHoldingAssessment` boundary. Add controlled evidence severity, unique project-group cost items, separate Survival/Cosmetic/Minor Maintenance totals, and property-level online CHB ranges. Preserve all existing V1/V2 fields and rerun only the deterministic aggregation against the cached 30-property visual manifests plus local REALTOR.ca HTML.

**Tech Stack:** Python 3.14, Pydantic models, pytest, JSON/CSV/Markdown artifacts, existing Phase 2A runner.

---

## Implementation map

- Modify `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py` to add cost-item models, controlled evidence classification, project-group deduplication, cost priors, CAPEX band rules, and the additive assessment fields.
- Modify `scripts/run_strategy_v2_phase_2a.py` to expose the cost ledger and online CHB ranges in each property row, CSV, shortlist, and audit report.
- Modify `tests/test_strategy_v2_phase_2a_ledgers.py` and add focused cases in `tests/test_strategy_v2_phase_2a_runner.py`.
- Create the date-stamped deterministic output under `data/research/normalized/` and `reports/strategy-v2-online-screening/2026-09-12/` without replacing `data/research/raw/photos`.

## Task 1: Add the cost-ledger schema and failing behavior tests

**Files:**
- Modify: `tests/test_strategy_v2_phase_2a_ledgers.py`
- Modify: `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py`

- [ ] **Step 1: Write failing tests for the additive schema and cost separation**

Append these tests to `tests/test_strategy_v2_phase_2a_ledgers.py`:

```python
def test_clean_dated_home_has_cosmetic_cost_but_no_observed_survival_cost():
    result = build_online_holding_assessment(
        listing_description="Original kitchen, dated bathroom, clean and well maintained.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["clean", "well maintained"],
            "cosmetic_signals": ["original kitchen", "dated bathroom"],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "B"
    assert result.survival_capex_base == 0
    assert result.cosmetic_reno_base > 0
    assert result.observed_survival_items == []
    assert result.observed_cosmetic_items


def test_surface_weathering_is_minor_maintenance_not_a_survival_repair():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [
                "dry and patchy grass",
                "weathered fence and peeling exterior paint",
                "light rust on exterior railing",
            ],
            "maintenance_signals": [],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "B"
    assert result.survival_capex_base == 0
    assert result.minor_maintenance_items
    assert result.observed_survival_items == []


def test_visible_water_damage_creates_one_deduplicated_survival_item():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [
                "water staining beneath sink",
                "localized water damage near sink",
            ],
            "maintenance_signals": [],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "C"
    assert result.survival_capex_base > 0
    assert len(result.observed_survival_items) == 1
    assert result.observed_survival_items[0]["project_group"] == "WATER_DRAINAGE"


def test_missing_systems_are_unknown_without_invented_cost():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={},
        coverage={"photo_evidence_coverage": 0, "photo_confidence": "LOW"},
    )

    assert result.online_capex_band == "UNKNOWN"
    assert result.survival_capex_low == 0
    assert result.survival_capex_base == 0
    assert result.survival_capex_high == 0
    assert "ROOF" in result.unseen_systems
    assert "FURNACE" in result.unseen_systems
    assert "ELECTRICAL" in result.unseen_systems
    assert "PLUMBING" in result.unseen_systems


def test_recent_named_system_updates_can_produce_a_without_cosmetic_capex():
    result = build_online_holding_assessment(
        listing_description="Updated roof 2022, new furnace 2023, updated electrical and plumbing; clean kitchen.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={
            "visible_defects": [],
            "maintenance_signals": ["updated roof", "new furnace", "updated electrical", "updated plumbing"],
            "cosmetic_signals": [],
        },
        coverage={"photo_evidence_coverage": 100, "photo_confidence": "HIGH"},
    )

    assert result.online_capex_band == "A"
    assert result.survival_capex_base == 0
    assert result.estimate_confidence == "HIGH"


def test_online_cost_fields_are_serializable_and_versioned():
    result = build_online_holding_assessment(
        listing_description="Original kitchen.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={"cosmetic_signals": ["original kitchen"]},
        coverage={"photo_evidence_coverage": 75, "photo_confidence": "HIGH"},
    )

    payload = result.model_dump(mode="json")
    assert payload["schema_version"] == "strategy-v2-phase-2a-online-holding-v2"
    assert payload["estimate_basis"] == "online_cost_prior_v1"
    assert all(key in payload for key in (
        "survival_capex_low",
        "survival_capex_base",
        "survival_capex_high",
        "cosmetic_reno_low",
        "cosmetic_reno_base",
        "cosmetic_reno_high",
        "observed_survival_items",
        "observed_cosmetic_items",
        "minor_maintenance_items",
        "unseen_systems",
        "unobserved_critical_areas",
        "estimate_confidence",
    ))
```

- [ ] **Step 2: Run the focused tests and verify they fail for the intended reason**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_ledgers.py
```

Expected: the new tests fail because the assessment has no cost-ledger fields and the old free-text defect rule still sends surface observations to C.

- [ ] **Step 3: Add the minimal Pydantic cost-item schema**

In `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py`, add these type aliases and model before `OnlineHoldingAssessment`:

```python
EvidenceCategory = Literal[
    "CRITICAL", "MAJOR", "MODERATE", "MINOR_MAINTENANCE",
    "COSMETIC", "NOT_OBSERVED", "UNKNOWN",
]
EvidenceScope = Literal["LOCAL", "SYSTEMIC", "NONE", "UNKNOWN"]
CostType = Literal["SURVIVAL", "COSMETIC", "MINOR_MAINTENANCE", "FUNCTIONAL"]


class OnlineCostItem(_PhotoModel):
    project_group: str
    category: EvidenceCategory
    scope: EvidenceScope
    cost_type: CostType
    cost_low: float = Field(default=0, ge=0)
    cost_base: float = Field(default=0, ge=0)
    cost_high: float = Field(default=0, ge=0)
    source: str
    evidence: list[str] = Field(default_factory=list)
    confidence: PhotoConfidence = "UNKNOWN"
```

Extend `OnlineHoldingAssessment` with:

```python
    schema_version: str = "strategy-v2-phase-2a-online-holding-v2"
    survival_capex_low: float = Field(default=0, ge=0)
    survival_capex_base: float = Field(default=0, ge=0)
    survival_capex_high: float = Field(default=0, ge=0)
    cosmetic_reno_low: float = Field(default=0, ge=0)
    cosmetic_reno_base: float = Field(default=0, ge=0)
    cosmetic_reno_high: float = Field(default=0, ge=0)
    observed_survival_items: list[dict[str, Any]] = Field(default_factory=list)
    observed_cosmetic_items: list[dict[str, Any]] = Field(default_factory=list)
    minor_maintenance_items: list[dict[str, Any]] = Field(default_factory=list)
    unseen_systems: list[str] = Field(default_factory=list)
    unobserved_critical_areas: list[str] = Field(default_factory=list)
    estimate_confidence: PhotoConfidence = "UNKNOWN"
    estimate_basis: str = "online_cost_prior_v1"

The implementation must map each recognized observation to one project group,
preserve the original evidence string, and deduplicate project groups before
calculating totals. It must map `WATER_STAINING` and `BASEMENT_MOISTURE` to
`WATER_DRAINAGE`, `FOUNDATION_CRACKING` and `FLOOR_DEFORMATION` to
`FOUNDATION_STRUCTURE`, `SIDING_DAMAGE` to `ENVELOPE`, `ROOF_DAMAGE` to
`ROOF`, and `UNFINISHED_MAJOR_DEMOLITION` to `BASEMENT_UNFINISHED`.

- [ ] **Step 5: Implement the assessment aggregation and run the focused tests**

Refactor only the CAPEX portion of `build_online_holding_assessment` so it:

1. combines description and visual evidence;
2. calls `_observation_severity` for every free-form visible defect;
3. creates at most one `OnlineCostItem` per Survival project group;
4. creates at most one cosmetic item per cosmetic project group;
5. puts minor exterior items in `minor_maintenance_items` with a `$0/$0/$10K`
   allowance, never in Survival totals by default;
6. computes Survival and Cosmetic low/base/high sums from unique items;
7. sets `survival_capex_base=0` when no Survival item is observed;
8. records `unseen_systems` as `ROOF`, `FURNACE`, `ELECTRICAL`, `PLUMBING`,
   and `FOUNDATION` when the relevant evidence is absent;
9. records missing required photo groups in `unobserved_critical_areas`;
10. assigns A/B/C/D/E/UNKNOWN using the approved spec;
11. sets `estimate_confidence` from coverage and evidence completeness without
    changing the condition band merely because coverage is low.

The return statement must pass JSON-serializable dictionaries from
`OnlineCostItem.model_dump(mode="json")` into the new assessment fields. The
old maintenance, modernization, usability, flags, and evidence fields remain
populated, except ordinary surface observations no longer trigger C.

Run:

```bash
rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_ledgers.py
```

Expected: all ledger tests pass, including cost separation and deduplication.

## Task 2: Expose the ledger and online CHB at property level

**Files:**
- Modify: `scripts/run_strategy_v2_phase_2a.py`
- Modify: `tests/test_strategy_v2_phase_2a_runner.py`

- [ ] **Step 1: Write failing runner tests for property-level amounts**

Add this test to `tests/test_strategy_v2_phase_2a_runner.py`:

```python
def test_phase_2a_row_exposes_separate_cost_ledgers_and_online_chb(tmp_path):
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-12",
        photo_manifest_root=tmp_path / "input-manifests",
    )

    saved = json.loads(Path(result["artifacts"]["json"]).read_text(encoding="utf-8"))
    row = next(item for item in saved["properties"] if item["address"] == "5454 MELBOURNE STREET")
    ledger = row["online_cost_ledger"]
    assert set(ledger) >= {"survival_capex", "cosmetic_reno", "minor_maintenance"}
    assert set(ledger["survival_capex"]) == {"low", "base", "high", "items"}
    assert set(row["online_chb"]) == {"low", "base", "high"}
    assert row["online_chb"]["base"] == row["asking"] + ledger["survival_capex"]["base"]
    assert ledger["cosmetic_reno"]["base"] >= 0
```

- [ ] **Step 2: Run the runner test and verify it fails**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py::test_phase_2a_row_exposes_separate_cost_ledgers_and_online_chb
```

Expected: FAIL because the property row does not yet contain
`online_cost_ledger` or `online_chb`.

- [ ] **Step 3: Add property-level ledger construction**

In `_build_row`, after `assessment_dict = assessment.model_dump(mode="json")`,
add a small `_amounts` helper and construct:

```python
def _amounts(assessment: Mapping[str, Any], prefix: str) -> dict[str, float]:
    return {
        "low": float(assessment.get(f"{prefix}_low") or 0),
        "base": float(assessment.get(f"{prefix}_base") or 0),
        "high": float(assessment.get(f"{prefix}_high") or 0),
    }


survival_amounts = _amounts(assessment_dict, "survival_capex")
cosmetic_amounts = _amounts(assessment_dict, "cosmetic_reno")
minor_items = assessment_dict.get("minor_maintenance_items") or []
minor_amounts = {
    "low": sum(float(item.get("cost_low") or 0) for item in minor_items if isinstance(item, Mapping)),
    "base": sum(float(item.get("cost_base") or 0) for item in minor_items if isinstance(item, Mapping)),
    "high": sum(float(item.get("cost_high") or 0) for item in minor_items if isinstance(item, Mapping)),
}
asking_value = _number(candidate.get("asking"))
online_chb = {
    key: asking_value + value if asking_value is not None else None
    for key, value in survival_amounts.items()
}
online_cost_ledger = {
    "version": assessment_dict.get("estimate_basis"),
    "survival_capex": {**survival_amounts, "items": assessment_dict.get("observed_survival_items") or []},
    "cosmetic_reno": {**cosmetic_amounts, "items": assessment_dict.get("observed_cosmetic_items") or []},
    "minor_maintenance": {**minor_amounts, "items": minor_items},
    "unseen_systems": assessment_dict.get("unseen_systems") or [],
    "unobserved_critical_areas": assessment_dict.get("unobserved_critical_areas") or [],
    "estimate_confidence": assessment_dict.get("estimate_confidence"),
}
```

Add these two mappings to the returned row:

```python
"online_cost_ledger": online_cost_ledger,
"online_chb": online_chb,
```

- [ ] **Step 4: Add flattened CSV fields and report columns**

Extend `_write_csv` with:

```python
"survival_capex_low", "survival_capex_base", "survival_capex_high",
"cosmetic_reno_low", "cosmetic_reno_base", "cosmetic_reno_high",
"minor_maintenance_high", "estimate_confidence",
"online_chb_low", "online_chb_base", "online_chb_high",
"unseen_systems", "unobserved_critical_areas",
```

Flatten these values from `photo_assessment`, `online_cost_ledger`, and
`online_chb`. Add these columns to the shortlist table after `Road risk`:

```text
Survival base | Cosmetic base | CHB base | Cost confidence
```

Update the audit report with distributions for `estimate_confidence` and
`online_capex_band`, plus counts of rows with non-empty Survival and Cosmetic
items. Keep the explicit statement that this is online evidence, not an
inspection.

- [ ] **Step 5: Run focused runner tests**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py tests/test_strategy_v2_phase_2a_ledgers.py
```

Expected: all tests pass and `ranking_status=NOT_RUN` remains unchanged.

## Task 3: Re-aggregate the 166-property snapshot without API calls

**Files:**
- Create/update by runner: `data/research/raw/photos-visual-aggregated/2026-09-12/*/manifest.json`
- Create/update by runner: `data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json`
- Create/update by runner: `data/research/normalized/strategy-v2-online-screening-2026-09-12.csv`
- Create/update by runner: `reports/strategy-v2-online-screening/2026-09-12/*`

- [ ] **Step 1: Record baseline manifest count and hash**

Run:

```bash
rtk .venv/bin/python - <<'PY'
from pathlib import Path
import hashlib
root = Path("data/research/raw/photos")
files = sorted(root.glob("*/manifest.json"))
digest = hashlib.sha256()
for path in files:
    digest.update(str(path.relative_to(root)).encode())
    digest.update(path.read_bytes())
print({"count": len(files), "sha256": digest.hexdigest()})
PY
```

Expected: 166 manifests. Save the printed digest for final verification.

- [ ] **Step 2: Run deterministic aggregation using cached evidence**

Run:

```bash
rtk .venv/bin/python scripts/run_strategy_v2_phase_2a.py \
  --photo-manifest-root data/research/raw/photo-assets-visual/2026-09-12 \
  --manifest-output-root data/research/raw/photos-visual-aggregated/2026-09-12 \
  --output-date 2026-09-12
```

The command must not use `--classify-photos`; it must reuse the 30 local
visual manifests, produce 166 rows, and leave the old
`data/research/raw/photos` tree untouched.

- [ ] **Step 3: Verify property-level arithmetic and distributions**

Run:

```bash
rtk .venv/bin/python - <<'PY'
from pathlib import Path
import json
from collections import Counter
payload = json.loads(Path("data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json").read_text())
rows = payload["properties"]
assert len(rows) == 166
assert payload["ranking_status"] == "NOT_RUN"
for row in rows:
    ledger = row["online_cost_ledger"]
    chb = row["online_chb"]
    if chb["base"] is not None:
        assert chb["base"] == row["asking"] + ledger["survival_capex"]["base"]
print("properties", len(rows))
print("visual_status", Counter(row.get("visual_status") for row in rows))
print("capex_band", Counter((row.get("photo_assessment") or {}).get("online_capex_band") for row in rows))
print("estimate_confidence", Counter((row.get("photo_assessment") or {}).get("estimate_confidence") for row in rows))
print("survival_items", sum(bool(row["online_cost_ledger"]["survival_capex"].get("items")) for row in rows))
print("cosmetic_items", sum(bool(row["online_cost_ledger"]["cosmetic_reno"].get("items")) for row in rows))
PY
```

Expected: ordinary surface weathering produces no Survival item and does not
automatically create a C band; cosmetic items are separate; every row has the
new additive schema.

## Task 4: Regression and baseline verification

**Files:**
- Modify if needed: `tests/test_strategy_v2_phase_2a_ledgers.py`
- Modify if needed: `tests/test_strategy_v2_phase_2a_runner.py`

- [ ] **Step 1: Run the focused test suite**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_ledgers.py tests/test_strategy_v2_phase_2a_runner.py tests/test_strategy_v2_photo_intelligence.py
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete test suite and compile check**

Run:

```bash
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m compileall -q src scripts tests
```

Expected: no test failures and no compilation output.

- [ ] **Step 3: Confirm baseline preservation**

Run:

```bash
rtk .venv/bin/python - <<'PY'
from pathlib import Path
import hashlib
for name in (
    "data/research/normalized/portfolio-analysis-2026-09-09.json",
    "data/research/normalized/strategy-v2-shadow-2026-09-09.json",
):
    path = Path(name)
    assert path.is_file(), path
root = Path("data/research/raw/photos")
files = sorted(root.glob("*/manifest.json"))
digest = hashlib.sha256()
for path in files:
    digest.update(str(path.relative_to(root)).encode())
    digest.update(path.read_bytes())
print({"legacy_manifest_count": len(files), "legacy_manifest_sha256": digest.hexdigest()})
PY
```

Expected: the count and SHA-256 match Task 3 Step 1; the V1 and V2 Phase 1
input files remain present and unchanged.

- [ ] **Step 4: Update the approved design status and report the result**

Mark implementation steps complete only after the commands above pass. The
final handoff must link the new JSON, CSV, shortlist, audit report, and
acceptance report, and state explicitly that the new amounts are online
screening estimates rather than inspection-grade costs.
