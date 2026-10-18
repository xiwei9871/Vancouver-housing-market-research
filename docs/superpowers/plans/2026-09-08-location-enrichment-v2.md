# Location Enrichment V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add versioned Walk Score-only enrichment that produces one reproducible family-detached urban-access score while keeping residential-environment dimensions explicitly unavailable and without changing production property rankings.

**Architecture:** A provider-neutral Pydantic schema receives raw results from one Walk Score HTTP adapter. A deterministic scoring module validates the three canonical inputs, while an append-only JSONL store handles cache/history. A runner enriches property artifacts and generates provider comparison, benchmark, recommendation, and double-counting audit artifacts.

**Tech Stack:** Python 3.11+, Pydantic 2, httpx, pytest, JSONL, Markdown, CSV.

---

### Task 1: Canonical schema and score calculation

**Files:**
- Create: `src/northshore_watchdog/research/location_providers/base.py`
- Create: `src/northshore_watchdog/research/location_providers/scoring.py`
- Create: `src/northshore_watchdog/research/location_providers/__init__.py`
- Test: `tests/test_research_location_providers.py`

- [x] Write failing tests for Walk Score 0–100 normalization, frozen composite formulas, complete/unknown handling, and invalid ranges.
- [x] Run `rtk .venv/bin/python -m pytest tests/test_research_location_providers.py -q` and confirm failures are caused by missing modules.
- [x] Implement minimal provider/result/snapshot models and deterministic scoring.
- [x] Re-run the focused tests and confirm they pass.

### Task 2: Walk Score adapter

**Files:**
- Create: `src/northshore_watchdog/research/location_providers/walk_score.py`
- Test: `tests/test_research_location_providers.py`

- [x] Write failing transport-injected tests for valid responses, missing credentials, no-data responses, and provider errors.
- [x] Confirm the tests fail for missing adapters.
- [x] Implement server-side Walk Score `/score` client with normalized provider results and no hidden GIS fallback.
- [x] Re-run focused tests.

### Task 3: Append-only snapshots and cache

**Files:**
- Create: `src/northshore_watchdog/research/location_providers/store.py`
- Test: `tests/test_research_location_providers.py`

- [x] Write failing tests for 180-day cache reuse, stale refresh decisions, append-only preservation, and model/provider drift classification.
- [x] Confirm expected failures.
- [x] Implement the JSONL snapshot store and cache lookup.
- [x] Re-run focused tests.

### Task 4: Portfolio runner and research artifacts

**Files:**
- Create: `src/northshore_watchdog/research/location_providers/runner.py`
- Create: `scripts/run_location_provider_enrichment.py`
- Modify: `config/research-discovery.yaml`
- Test: `tests/test_research_location_provider_runner.py`

- [x] Write failing end-to-end tests against captured provider payloads and an input artifact.
- [x] Confirm expected failures.
- [x] Implement config loading, property enrichment, benchmark delta calculation, and generation of `provider-comparison.md`, `provider-schema.json`, `benchmark-results.csv`, `double-counting-audit.md`, and `provider-recommendation.md`.
- [x] Ensure output retains provider errors/unknowns and does not mutate screening scores.
- [x] Re-run focused tests.

### Task 5: Documentation, benchmark execution, and verification

**Files:**
- Modify: `README.md`
- Generate: `research/location-provider/provider-comparison.md`
- Generate: `research/location-provider/provider-schema.json`
- Generate: `research/location-provider/benchmark-results.csv`
- Generate: `research/location-provider/double-counting-audit.md`
- Generate: `research/location-provider/provider-recommendation.md`

- [x] Document credentials, cache, CLI use, provider roles, and non-production status.
- [x] Run the local 166-property artifact without credentials and verify explicit `NOT_CONFIGURED`/`UNKNOWN` output rather than zero scores.
- [x] Run `rtk .venv/bin/python -m pytest tests/test_research_location_providers.py tests/test_research_location_provider_runner.py -q`.
- [x] Run `rtk .venv/bin/python -m pytest -q` and `rtk .venv/bin/python -m compileall -q src scripts`.
- [x] Review generated artifacts against every acceptance requirement.
