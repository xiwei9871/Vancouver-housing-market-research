# Strategy V2 Phase 2A Photo Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a photo-manifest-driven, high-recall online screening pass for all 166 saved Vancouver listings without modifying V1, V2 Phase 1, RALV, or final ranking.

**Architecture:** Add a focused photo-intelligence module for manifests, content labels, condition ledgers, coverage, and online screening signals. Add a separate selector/runner that reads the saved 166 cards and V2 Phase 1 output, applies track-aware Pareto protection, and writes new date-stamped artifacts. A local manifest is the source of truth; unavailable photos remain explicit unknowns.

**Tech Stack:** Python 3.11+, Pydantic v2, YAML/JSON/CSV/Markdown, SHA-256, optional image metadata and perceptual-hash libraries only when already installed, pytest.

---

### Task 1: Freeze the Phase 2A contract and config

**Files:**
- Create: `config/strategy-v2-phase-2a.yaml`
- Create: `tests/test_strategy_v2_phase_2a_config.py`
- Test: `docs/superpowers/specs/2026-09-10-strategy-v2-phase-2a-photo-screening-design.md`

- [x] **Step 1: Write the failing test**

```python
from pathlib import Path
import yaml


def test_phase_2a_config_freezes_high_recall_scope():
    config = yaml.safe_load(Path("config/strategy-v2-phase-2a.yaml").read_text())
    assert config["ranking"]["enabled"] is False
    assert config["photo"]["required_space_groups"] == ["exterior", "living", "basement", "site"]
    assert config["shortlist"]["target_range"] == [25, 35]
    assert config["calibration"]["random_seed"] == 20260909
    assert config["calibration"]["blind_test_count"] == 46
```

- [x] **Step 2: Run the focused test and verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_config.py`

Expected: FAIL because the Phase 2A config does not exist.

- [x] **Step 3: Add minimal configuration**

```yaml
schema_version: strategy-v2-phase-2a-v1
ranking:
  enabled: false
photo:
  required_space_groups: [exterior, living, basement, site]
  duplicate_policy: exact_sha256_then_perceptual_hash
  missing_source_policy: preserve_unknown_continue
  visual_adapter: disabled
shortlist:
  target_range: [25, 35]
  track_targets:
    HOME_REBUILD: 20
    LAND_DEVELOPMENT: 5
    PREMIUM_SMALL_LOT: 5
  special_tracks_excluded: [SPECIAL_SITUATION]
  protect_pareto_frontier: true
  protect_exceptional_signal: true
calibration:
  random_seed: 20260909
  calibration_count: 120
  blind_test_count: 46
  must_keep_recall_target: 1.0
  keep_recall_target: 0.90
scoring:
  unknown_value: UNKNOWN
  capex_bands: [A, B, C, D, E]
```

- [x] **Step 4: Run the focused test and verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_config.py`

Expected: PASS.

---

### Task 2: Add photo manifest and content-evidence models

**Files:**
- Create: `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py`
- Create: `tests/test_strategy_v2_photo_intelligence.py`

- [x] **Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from northshore_watchdog.research.strategy_v2_photo_intelligence import (
    PhotoObservation,
    build_photo_manifest,
)


def test_manifest_preserves_order_and_required_audit_fields():
    manifest = build_photo_manifest(
        property_key="mls:R1",
        photos=[
            {"source_url": "https://img.example/front.jpg", "original_order": 0, "content_labels": ["EXTERIOR_FRONT"]},
            {"source_url": "https://img.example/front.jpg", "original_order": 1, "content_labels": ["EXTERIOR_FRONT"]},
        ],
        retrieved_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    assert manifest.property_key == "mls:R1"
    assert manifest.images[0].original_order == 0
    assert manifest.images[0].sha256
    assert manifest.images[0].source_status == "AVAILABLE"
    assert manifest.images[1].is_duplicate is True


def test_missing_photo_source_is_unknown_not_error():
    manifest = build_photo_manifest(property_key="mls:R2", photos=[], retrieved_at=None)
    assert manifest.source_status == "PHOTO_SOURCE_UNAVAILABLE"
    assert manifest.images == []
```

- [x] **Step 2: Run tests and verify the expected failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_photo_intelligence.py`

Expected: FAIL because the manifest model and builder do not exist.

- [x] **Step 3: Implement the manifest contract**

Implement Pydantic models with explicit fields:

```python
class PhotoObservation(BaseModel):
    property_key: str
    source_url: str | None
    original_order: int
    retrieved_at: datetime | None
    sha256: str | None
    perceptual_hash: str | None
    width: int | None
    height: int | None
    local_path: str | None
    source_status: str
    content_labels: list[str]
    is_duplicate: bool = False

class PhotoManifest(BaseModel):
    property_key: str
    source_status: str
    retrieved_at: datetime | None
    images: list[PhotoObservation]
    duplicate_count: int = 0
```

`build_photo_manifest` must preserve original order, compute SHA-256 for local
bytes when available, mark exact duplicate bytes/URLs, and represent empty or
failed input as `PHOTO_SOURCE_UNAVAILABLE` without raising.

- [x] **Step 4: Run focused tests and verify they pass**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_photo_intelligence.py`

Expected: PASS.

---

### Task 3: Classify photo content before aggregating conditions

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py`
- Modify: `tests/test_strategy_v2_photo_intelligence.py`

- [x] **Step 1: Add failing tests**

```python
def test_content_labels_drive_space_coverage_not_raw_photo_count():
    manifest = build_photo_manifest(
        property_key="mls:R3",
        photos=[
            {"source_url": f"https://img.example/living-{i}.jpg", "original_order": i, "content_labels": ["LIVING"]}
            for i in range(12)
        ]
        + [{"source_url": "https://img.example/kitchen.jpg", "original_order": 12, "content_labels": ["KITCHEN"]}],
        retrieved_at=None,
    )
    summary = summarize_photo_coverage(manifest)
    assert summary["photo_count"] == 13
    assert summary["covered_space_groups"] == ["living"]
    assert summary["photo_evidence_coverage"] < 50
    assert summary["photo_confidence"] == "LOW"


def test_unconfigured_visual_adapter_does_not_guess_labels():
    manifest = build_photo_manifest(
        property_key="mls:R4",
        photos=[{"source_url": "https://img.example/one.jpg", "original_order": 0}],
        retrieved_at=None,
    )
    assert manifest.images[0].content_labels == []
```

- [x] **Step 2: Run and verify failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_photo_intelligence.py`

Expected: FAIL because coverage summarization is not implemented.

- [x] **Step 3: Implement controlled labels and coverage**

Support the frozen content-label vocabulary. Implement `summarize_photo_coverage`
using non-duplicate classified labels and the four configured required groups.
Return `photo_count`, `classified_photo_count`, `covered_space_groups`,
`photo_evidence_coverage`, `photo_confidence`, and `uncovered_space_groups`.
No raw count may substitute for space coverage.

- [x] **Step 4: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_photo_intelligence.py`

Expected: PASS.

---

### Task 4: Build separate maintenance, modernization, and usability ledgers

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_photo_intelligence.py`
- Create: `tests/test_strategy_v2_phase_2a_ledgers.py`

- [x] **Step 1: Write failing tests**

```python
from northshore_watchdog.research.strategy_v2_photo_intelligence import build_online_holding_assessment


def test_clean_dated_house_is_not_misclassified_as_poor():
    result = build_online_holding_assessment(
        listing_description="Original kitchen, clean and well maintained; updated roof in 2021.",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={"visible_defects": [], "maintenance_signals": ["clean", "well maintained"]},
        coverage={"photo_evidence_coverage": 75, "photo_confidence": "HIGH"},
    )
    assert result.visible_maintenance_condition == "GOOD"
    assert result.cosmetic_modernization == "DATED"
    assert result.holding_usability == "GOOD"
    assert result.online_capex_band == "B"


def test_visible_water_damage_downgrades_maintenance_but_not_by_missing_photos():
    result = build_online_holding_assessment(
        listing_description="",
        card={"bedrooms": 3, "bathrooms": 2, "finished_sqft": 1800},
        photo_evidence={"visible_defects": ["WATER_STAINING"], "maintenance_signals": []},
        coverage={"photo_evidence_coverage": 25, "photo_confidence": "LOW"},
    )
    assert result.visible_maintenance_condition == "ELEVATED_RISK"
    assert result.photo_confidence == "LOW"
    assert result.online_capex_band in {"C", "D"}


def test_multiple_kitchens_are_layout_signal_not_family_room_bonus():
    result = build_online_holding_assessment(
        listing_description="Two kitchens and five bedrooms.",
        card={"bedrooms": 5, "bathrooms": 2, "finished_sqft": 2000},
        photo_evidence={"visible_defects": [], "maintenance_signals": []},
        coverage={"photo_evidence_coverage": 80, "photo_confidence": "HIGH"},
    )
    assert result.holding_usability != "VERY_GOOD"
    assert "MULTI_KITCHEN_LAYOUT_REVIEW" in result.flags
```

- [x] **Step 2: Run and verify failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_ledgers.py`

Expected: FAIL because the online holding assessment does not exist.

- [x] **Step 3: Implement evidence-first aggregation**

Implement explicit evidence precedence:

1. visible major defects downgrade maintenance and CAPEX band;
2. positive maintenance wording or explicit completed system updates support
   maintenance, but do not clear unobserved systems;
3. dated/old cosmetic language affects modernization only;
4. bedrooms/baths/area and kitchen count affect holding usability/layout;
5. coverage modifies confidence only.

Return a typed `OnlineHoldingAssessment` with bands, 0–100 explanatory
subscores, `online_capex_band`, `photo_confidence`, flags, and evidence.
Use `UNKNOWN` when there is no evidence; never use zero for missing data.

- [x] **Step 4: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_ledgers.py`

Expected: PASS.

---

### Task 5: Add transparent screening signals and Pareto selection

**Files:**
- Create: `src/northshore_watchdog/research/strategy_v2_online_screening.py`
- Create: `tests/test_strategy_v2_online_screening.py`

- [x] **Step 1: Write failing tests**

```python
from northshore_watchdog.research.strategy_v2_online_screening import select_shortlist


def _candidate(key, track, location, holding, land, price, risk):
    return {"property_key": key, "primary_track": track, "online_location_score": location,
            "online_holding_score": holding, "online_land_score": land,
            "price_attractiveness": price, "permanent_risk": risk}


def test_pareto_candidate_is_not_removed_by_track_target():
    candidates = [
        _candidate("a", "HOME_REBUILD", 90, 90, 60, 60, 30),
        _candidate("b", "HOME_REBUILD", 70, 70, 80, 80, 40),
        _candidate("c", "HOME_REBUILD", 75, 75, 75, 75, 50),
    ]
    selected = select_shortlist(candidates, track_targets={"HOME_REBUILD": 1}, target_range=(1, 1))
    assert {item["property_key"] for item in selected} >= {"a", "b"}


def test_special_situation_does_not_consume_normal_slot():
    candidates = [
        _candidate("special", "SPECIAL_SITUATION", 100, 100, 100, 100, 100),
        _candidate("home", "HOME_REBUILD", 60, 60, 60, 60, 20),
    ]
    selected = select_shortlist(candidates, track_targets={"HOME_REBUILD": 1}, target_range=(1, 1))
    assert [item["property_key"] for item in selected] == ["home"]
```

- [x] **Step 2: Run and verify failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_online_screening.py`

Expected: FAIL because the selector does not exist.

- [x] **Step 3: Implement selection**

Implement normalized comparison directions (`risk` is lower-is-better) and
Pareto dominance within primary track. Retain non-dominated candidates and
exceptional one-dimension cases before filling track targets by a transparent
screening score. Deduplicate by property key. Keep special situations in an
excluded observation list and do not count them against the normal target.

- [x] **Step 4: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_online_screening.py`

Expected: PASS.

---

### Task 6: Add calibration recall evaluation

**Files:**
- Modify: `src/northshore_watchdog/research/strategy_v2_online_screening.py`
- Create: `tests/test_strategy_v2_phase_2a_calibration.py`

- [x] **Step 1: Write failing tests**

```python
from northshore_watchdog.research.strategy_v2_online_screening import evaluate_recall


def test_recall_reports_not_available_without_human_labels():
    result = evaluate_recall([{"property_key": "a"}], retained_keys={"a"})
    assert result["status"] == "NOT_AVAILABLE"


def test_recall_counts_must_keep_and_keep_separately():
    labels = {"a": "MUST_KEEP", "b": "KEEP", "c": "DROP"}
    result = evaluate_recall(list({"property_key": key} for key in labels), retained_keys={"a", "c"}, labels=labels)
    assert result["must_keep_recall"] == 1.0
    assert result["keep_recall"] == 0.5
```

- [x] **Step 2: Run and verify failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_calibration.py`

Expected: FAIL because recall evaluation does not exist.

- [x] **Step 3: Implement fixed-label evaluation**

Return `NOT_AVAILABLE` when no complete labels are supplied. When labels are
available, calculate MUST_KEEP recall and combined MUST_KEEP/KEEP recall using
only the supplied calibration universe, with explicit denominators and
retained keys.

- [x] **Step 4: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_calibration.py`

Expected: PASS.

---

### Task 7: Add the 166-property Phase 2A runner and reports

**Files:**
- Create: `scripts/run_strategy_v2_phase_2a.py`
- Create: `tests/test_strategy_v2_phase_2a_runner.py`

- [x] **Step 1: Write failing test**

```python
def test_phase_2a_runner_writes_all_rows_without_ranking(tmp_path):
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-10",
    )
    assert result["property_count"] == 166
    assert result["ranking_status"] == "NOT_RUN"
    assert result["shortlist_status"] in {"GENERATED", "GENERATED_WITH_PARETO_OVERRIDES"}
    assert Path(result["artifacts"]["json"]).is_file()
```

- [x] **Step 2: Run and verify failure**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py`

Expected: FAIL because the runner does not exist.

- [x] **Step 3: Implement the runner**

Load exactly 166 official-enrichment cards and the V2 Phase 1 JSON. Load local
photo manifests by stable property key, creating an unavailable manifest when
one is missing. Build the online assessment and screening fields for every
row, select the track-aware shortlist, calculate calibration status, and write:

```text
data/research/normalized/strategy-v2-online-photo-evidence-<date>.json
data/research/normalized/strategy-v2-online-screening-<date>.csv
data/research/history/strategy-v2-online-screening-snapshots.jsonl
reports/strategy-v2-online-screening/<date>/online-shortlist.md
reports/strategy-v2-online-screening/<date>/audit-report.md
reports/strategy-v2-online-screening/<date>/ACCEPTANCE-REPORT.md
```

The runner must write no `rank`, `current_use_score`, `dual_score`,
`redevelopment_score`, or RALV output. Record before/after hashes for the V1
analysis, V1 report index, and V2 Phase 1 JSON.

- [x] **Step 4: Run focused runner tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py`

Expected: PASS.

---

### Task 8: Replay the 166-property universe and verify acceptance

**Files:**
- Modify: `reports/strategy-v2-online-screening/2026-09-10/ACCEPTANCE-REPORT.md`
- Create: `data/research/normalized/strategy-v2-online-photo-evidence-2026-09-10.json`
- Create: `data/research/normalized/strategy-v2-online-screening-2026-09-10.csv`
- Create: `data/research/history/strategy-v2-online-screening-snapshots.jsonl`
- Create: `reports/strategy-v2-online-screening/2026-09-10/online-shortlist.md`
- Create: `reports/strategy-v2-online-screening/2026-09-10/audit-report.md`

- [x] **Step 1: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_*.py tests/test_strategy_v2_online_screening.py`

Expected: PASS.

- [x] **Step 2: Run the Phase 2A replay**

Run:

```bash
rtk .venv/bin/python scripts/run_strategy_v2_phase_2a.py \
  --input data/research/normalized/portfolio-official-enrichment-2026-09-07.json \
  --shadow data/research/normalized/strategy-v2-shadow-2026-09-09.json \
  --output-root . \
  --output-date 2026-09-10
```

Expected: `property_count=166`, `ranking_status=NOT_RUN`, and an explicit
photo-source availability count.

- [x] **Step 3: Verify isolation and artifacts**

Run:

```bash
rtk .venv/bin/python -c "import json, pathlib; p=json.load(open('data/research/normalized/strategy-v2-online-photo-evidence-2026-09-10.json')); assert p['property_count']==166; assert p['ranking_status']=='NOT_RUN'; assert len(p['shortlist']['property_keys']) >= 25; assert len(list(pathlib.Path('reports/strategy-v2-online-screening/2026-09-10').glob('*'))) >= 3; print('phase2a artifact checks passed')"
```

Then compare the V1 and Phase 1 hashes recorded in the acceptance report.

- [x] **Step 4: Run the full test suite**

Run: `rtk .venv/bin/pytest -q`

Expected: all tests pass; any pre-existing skip is documented.

### Implementation note

The production replay found that an unbounded Pareto frontier can become the
whole market when Walk Score is unavailable.  The selector therefore bounds
frontier protection by the configured track target (while retaining explicit
exceptional signals), and records `GENERATED_WITH_PARETO_OVERRIDES` when that
protection is used.  This preserves high recall without allowing the normal
shortlist to expand from 30 to 161 properties.
