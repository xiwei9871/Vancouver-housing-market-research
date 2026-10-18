# Strategy V2 Property-Level Visual Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggregate the completed 30-property visual evidence snapshot into property-level Online Holding assessments while preserving all existing V1/V2 baseline artifacts.

**Architecture:** Reuse the existing deterministic `summarize_photo_coverage` and `build_online_holding_assessment` functions. Extend the Phase 2A runner with an optional manifest output root so the 166-property aggregation writes a new snapshot; selected properties consume the visual manifests and all others replay cached REALTOR HTML without classification.

**Tech Stack:** Python, Pydantic, pytest, JSON/CSV/Markdown artifacts.

---

### Task 1: Preserve aggregation outputs in a separate manifest snapshot

**Files:**
- Modify: `scripts/run_strategy_v2_phase_2a.py`
- Test: `tests/test_strategy_v2_phase_2a_runner.py`

- [x] **Step 1: Write a failing test**

Add a runner test that passes `manifest_output_root=tmp_path / "custom-manifests"` and asserts every returned manifest path is below that directory.

- [x] **Step 2: Run the focused test and verify it fails**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py::test_phase_2a_can_write_manifests_to_a_separate_snapshot`

Expected: FAIL because `run_phase_2a` does not accept `manifest_output_root`.

- [x] **Step 3: Implement the minimal option**

Add an optional `manifest_output_root` parameter, resolve it relative to the project root, use it instead of the hard-coded `root / "data/research/raw/photos"`, and expose the same option as `--manifest-output-root` in the CLI.

- [x] **Step 4: Run the focused test and verify it passes**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py::test_phase_2a_can_write_manifests_to_a_separate_snapshot`

Expected: PASS.

### Task 2: Run the 166-property aggregation with the 30-property visual snapshot

**Files:**
- Create by runner: `data/research/raw/photos-visual-aggregated/2026-09-12/*/manifest.json`
- Create by runner: `data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json`
- Create by runner: `data/research/normalized/strategy-v2-online-screening-2026-09-12.csv`
- Create by runner: `reports/strategy-v2-online-screening/2026-09-12/*`

- [x] **Step 1: Run the aggregation without API classification**

Run: `rtk .venv/bin/python scripts/run_strategy_v2_phase_2a.py --photo-manifest-root data/research/raw/photo-assets-visual/2026-09-12 --manifest-output-root data/research/raw/photos-visual-aggregated/2026-09-12 --output-date 2026-09-12`

Expected: `property_count=166`, `ranking_status=NOT_RUN`; 30 selected properties retain classified photo evidence and non-selected properties remain structured/HTML-only.

- [x] **Step 2: Verify the generated artifacts**

Run: `rtk jq '{property_count,ranking_status,counts,artifacts:{json,online_shortlist,audit_report,acceptance_report}}' data/research/normalized/strategy-v2-online-photo-evidence-2026-09-12.json`

Expected: 166 rows, ranking not run, and visual status counts matching the visual snapshot where supplied.

### Task 3: Regression verification

**Files:**
- No production changes beyond Tasks 1–2.

- [x] **Step 1: Run focused tests**

Run: `rtk .venv/bin/pytest -q tests/test_strategy_v2_phase_2a_runner.py tests/test_strategy_v2_photo_intelligence.py tests/test_strategy_v2_routed_visual_analysis.py`

- [x] **Step 2: Run the full test suite and compile check**

Run: `rtk .venv/bin/pytest -q && rtk .venv/bin/python -m compileall -q src scripts tests`

- [x] **Step 3: Confirm baseline preservation**

Verify that the existing V1 legacy file, Strategy V2 Shadow input, and previous `data/research/raw/photos` manifest count are unchanged by the new run.
