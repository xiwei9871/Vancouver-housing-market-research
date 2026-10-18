# Reno and Unified Land-Basis Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved Reno thresholds and separate current-use all-in cost from redevelopment BCA-normalized land basis across the 166-listing screening run.

**Architecture:** Keep the existing screening pipeline. Add a focused, evidence-first Reno estimator that produces a cost, band, score, and evidence fields. Make official BCA improvement the direct house proxy and make redevelopment price signals use `asking - BCA improvement - location residual`. Keep shared atomic scores, but use separate current-use and redevelopment aggregation lenses with a normalized 100-point redevelopment score.

**Tech Stack:** Python 3, Pydantic models, YAML configuration, pytest, JSON/Markdown report artifacts.

---

### Task 1: Lock the approved Reno behavior with tests

**Files:**
- Modify: `tests/test_research_screening.py`
- Test: `tests/test_research_screening.py`

- [ ] **Step 1: Write failing tests** for move-in-ready evidence, each cost threshold, age fallback, direct BCA house proxy, and the BCA-normalized land formula.
- [ ] **Step 2: Run the focused tests and verify they fail for the missing fields/old calculations.**

### Task 2: Implement the Reno estimator and output fields

**Files:**
- Modify: `src/northshore_watchdog/research/screening.py`
- Modify: `config/research-discovery.yaml`
- Modify: `tests/test_research_screening.py`

- [ ] **Step 1: Add configurable Reno bands and evidence markers.**
- [ ] **Step 2: Implement evidence-first project/year parsing with age fallback.**
- [ ] **Step 3: Return Reno cost, band, score, year, scope, evidence, and confidence.**
- [ ] **Step 4: Run focused tests and confirm all Reno tests pass.**

### Task 3: Correct price normalization and lot geometry

**Files:**
- Modify: `src/northshore_watchdog/research/screening.py`
- Modify: `src/northshore_watchdog/research/models.py` if model fields require extension
- Modify: `config/research-discovery.yaml`
- Modify: `tests/test_research_screening.py`

- [ ] **Step 1: Replace model-multiplied house value in redevelopment price calculations with official BCA improvement.**
- [ ] **Step 2: Add `bca_normalized_land_basis` and per-square-foot output while preserving legacy fields.**
- [ ] **Step 3: Make 33 x 122 approximately a 70/100 geometry threshold and distinguish 50ft frontage/5,000+ sqft.**
- [ ] **Step 4: Ensure unknown zoning/road never creates a positive redevelopment bonus.**
- [ ] **Step 5: Run focused regression tests.**

### Task 4: Apply current-use routing and unified ranking lenses

**Files:**
- Modify: `src/northshore_watchdog/research/screening.py`
- Modify: `config/research-discovery.yaml`
- Modify: `tests/test_research_screening.py`

- [ ] **Step 1: Add current-use all-in basis using asking plus required Reno.**
- [ ] **Step 2: Exclude properties with Reno above $150K from Current Use and Dual while retaining redevelopment evaluation.**
- [ ] **Step 3: Normalize redevelopment weights to 100 and keep the same underlying atomic measures for every property.**
- [ ] **Step 4: Add an explicit price-value score using local price and BCA evidence without double-counting seller fatigue as intrinsic value.**
- [ ] **Step 5: Run the full screening test module.**

### Task 5: Re-run the 166-listing artifact and verify outputs

**Files:**
- Create: dated JSON/Markdown scoring artifacts under `data/research/normalized/` and `reports/discovery/`
- Modify: latest report pointer if the existing pipeline provides one

- [ ] **Step 1: Run the existing official-enrichment screening command against the cached 166-listing input.**
- [ ] **Step 2: Verify the output includes Current Use, Dual Value, Redevelopment, and Price Value rankings.**
- [ ] **Step 3: Inspect 3910 W 10th, 2012 York, 4561 Nanaimo, and 956 E 21st for formulas and score components.**
- [ ] **Step 4: Run the complete test suite and report exact counts and any remaining limitations.**
