# Vancouver Screening V2.3 Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first verification.

**Goal:** Classify each normal house before scoring it as `LIVE_HOME`, `HOLD_5_10_REDEVELOP`, or `DEVELOPMENT_ONLY`, then apply a separate configuration-driven scorecard within each class. Transaction/title exceptions remain in `SPECIAL_TRANSACTION` and are never mixed into the three-class model.

**Architecture:** Keep Stage A as the inexpensive scope/identity gate. Stage B first calculates independent `CURRENT_USE_INDEX` and `REDEVELOPMENT_READINESS_INDEX` values without asking-price input. It then assigns a provisional asset class and scores only within that class. High-density signals route to the development class; partial-interest/court-sale signals are gated before classification. Stage C/D remain deferred.

**Tech Stack:** Python, Pydantic models, YAML configuration, pytest, existing discovery capture/report pipeline.

---

### Task 1: Define the V2.3 contract in tests

**Files:**
- Modify: `tests/test_research_screening.py`
- Test: `tests/test_research_screening.py`

- [x] Add assertions for three asset classes, price-independent classification, class-specific lot weights, special transaction gates, and no cross-class final list.
- [x] Run the focused tests and confirm they fail before implementation.

### Task 2: Implement the V2.3 screening model

**Files:**
- Modify: `src/northshore_watchdog/research/screening.py`

- [x] Add independent classification indexes, classification confidence, asset class, and per-class components to the decision/result models.
- [x] Detect special transaction/title signals before scoring; preserve high-density and non-standard-layout signals for classification.
- [x] Keep area tiers, school proxy, and area-relative reference prices explicit; missing data stays UNKNOWN/NEED_CLASSIFICATION.
- [x] Implement separate LIVE_HOME, HOLD_5_10_REDEVELOP, and DEVELOPMENT_ONLY scorecards.
- [x] Keep lot sufficiency capped in class scorecards while allowing geometry/threshold value only in the development scorecard.

### Task 3: Update configuration and reporting

**Files:**
- Modify: `config/research-discovery.yaml`
- Modify: `src/northshore_watchdog/research/discovery_report.py`

- [x] Store classification thresholds, class-specific weights, road classes, area tiers, neighbourhood reference prices, and school proxy mappings in YAML.
- [x] Show Current Use, Redevelopment Readiness, class score, confidence, area tier, school proxy, relative-price ratio, and class components.
- [x] Keep classes in separate report sections; special transactions and unknown classifications are separate and unscored.

### Task 4: Re-run the same real capture

**Files:**
- Regenerate: `reports/discovery/2026-09-04.md`
- Regenerate: `reports/discovery/latest.md`

- [x] Reconstruct the run from `/tmp/realtor-ca-detail-run-6.json` without network access.
- [x] Verify the 231 Vancouver cards remain the source pool and no global cross-class ranking is emitted.
- [x] Review the resulting classifications for the known seven properties and record data limitations in the report/hand-off.

### Task 5: Full verification

**Files:**
- No production files beyond the above.

- [x] Run the focused screening and discovery CLI tests.
- [x] Run the full pytest suite.
- [x] Inspect the generated report and verify class-specific lot/price semantics are active.
