# Phase 3 Comparable Engine Implementation Plan

> **Execution note:** Follow the TDD and verification instructions for this repository. Every shell command is prefixed with `rtk` per `/Users/xiwei/.codex/RTK.md`.

**Goal:** Turn the existing comparable skeleton into an auditable Phase 3 engine that separates local neighbourhood benchmarking from like-for-like valuation, protects calculations from conflicted sold history, and exposes a clearly configured current-use building-value proxy.

**Scope boundary:** This plan covers only sold-comparable selection and derived price/land metrics. It does not acquire listings, query official sources, infer zoning entitlement, calculate redevelopment scenarios, or change Phase 4 scoring formulas.

## Design

Use a deterministic, transparent rule engine rather than a statistical regression model. The subject and sold rows are normalized first; listing-only rows, unreliable rows, title exceptions, missing required context, and material conflicts are excluded with reason codes. A like-for-like set is selected in a primary 180-day window and expands to the configured fallback window (normally 365 days) only when the minimum reliable sample is not met.

Maintain two populations:

1. `selected_comparables`: closest valid geometry/zoning/road/condition matches used for `expected_market_value` and the detailed medians.
2. `neighbourhood_benchmark_comparables`: valid sold detached records in the same municipality/neighbourhood/context window, including lot-size outliers, used for the local benchmark and `relative_affordability_index`.

`expected_market_value` remains unknown unless the selected set reaches the configured minimum and has independently confirmed sold-history sources. A conflict between independent observations of the same transaction is excluded before any median is calculated.

The land proxy uses only an explicit subject transaction value (never asking price). The current-use building value is selected in this order: explicit subject value, explicit estimated value, then an enabled configured method such as assessment improvement or finished-house square footage times a configured proxy rate. The method and provenance are retained in the result so the value is visibly a proxy rather than an appraisal.

## Tasks

1. **Add failing tests.** Cover neighbourhood benchmark separation, configured building-value derivation, independent-source sold-price conflict exclusion, exact transaction-value handling, and report/fixture visibility. Run the focused test file and record the expected RED failures.

2. **Implement the model and engine changes.** Add comparable similarity/selection metadata and benchmark metrics, normalize assessment-improvement aliases, detect cross-source transaction conflicts before merging, compute the benchmark separately from the selected set, and apply the configured building-value proxy without ever treating asking price as a transaction.

3. **Update configuration and documentation.** Add explicit comparable benchmark and building-value-proxy settings to `config/research.yaml` and the MLS-first profile without enabling remote acquisition. Document unknown/low-confidence behavior and the provenance boundary.

4. **Validate the pipeline.** Run focused comparable, scoring, pipeline, end-to-end, official-enrichment, and validation tests, then the complete suite and compile check. Run the local case pipeline with comparable enrichment and inspect the generated output for the existing traceable cases plus explicit coverage gaps.

5. **Write the acceptance report.** Create `reports/phase3/comparable-engine-2026-09-01.md` with implementation files, metrics, example case results, source limitations, unresolved conflicts, and an explicit `ACCEPTED_WITH_COVERAGE_GAPS` or `BLOCKED` status. Stop before Phase 4.

## Verification commands

```bash
rtk .venv/bin/pytest -q tests/test_research_comparables.py
rtk .venv/bin/pytest -q tests/test_research_comparables.py tests/test_research_scoring.py tests/test_research_pipeline.py tests/test_research_end_to_end.py
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m compileall -q src
```

