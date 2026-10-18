# Vancouver Location Sold-Comps V1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a versioned, read-only Vancouver detached/freehold sold-comps data layer that can support location-premium research without changing production property scoring.

**Architecture:** Add a focused `location_sold_comps` module beside the existing V1 location research module. It ingests local CSV/JSON/JSONL snapshots, normalizes transaction identity and provenance, excludes incomplete or conflicting observations from statistics, retains benchmark records for blind-test review, and writes V1.1 data-quality/statistical/matched-pair artifacts. A separate CLI command runs this research layer; V1 files and production Current Use, Dual Value, Redevelopment, RALV, and MOS code remain untouched.

**Tech Stack:** Python 3.11, standard-library CSV/JSON/statistics/date handling, pandas through the existing location research module, Typer, pytest, Markdown/CSV artifacts.

---

### Task 1: Define the sold-comp normalization contract

**Files:**
- Create: `tests/test_research_location_sold_comps.py`
- Create: `src/northshore_watchdog/research/location_sold_comps.py`

- [ ] **Step 1: Write a failing test for alias normalization without asking-to-sale substitution.**

```python
def test_normalize_sold_comp_never_uses_asking_as_sale_price():
    result = normalize_sold_comp({
        "address": "1 Test Street",
        "asking_price": 1_700_000,
        "sale_date": "2026-01-01",
    }, source_id="fixture")
    assert result["sale_price"] is None
    assert result["last_asking"] == 1_700_000
    assert result["statistical_eligible"] is False
```

- [ ] **Step 2: Run the focused test and verify RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py`

Expected: collection fails because `northshore_watchdog.research.location_sold_comps` does not exist.

- [ ] **Step 3: Implement value/date/address aliases and a deterministic transaction key.**

The normalized record must preserve sale price separately from original/last asking, parse lot/house/BCA fields, normalize municipality/property/title fields, retain source URL/type/ID, and create a transaction key from MLS when available or normalized address plus sale date otherwise.

- [ ] **Step 4: Make eligibility fail closed.**

An observation is statistically eligible only when it is in City of Vancouver, detached, freehold, has a genuine sale price and sale date in the 36-month research window, and has no material transaction conflict. Missing title remains `UNKNOWN` and cannot silently become freehold.

- [ ] **Step 5: Run the focused test and verify GREEN.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py`

Expected: normalization tests pass.

### Task 2: Add multi-source ingestion, deduplication, and conflict isolation

**Files:**
- Modify: `tests/test_research_location_sold_comps.py`
- Modify: `src/northshore_watchdog/research/location_sold_comps.py`

- [ ] **Step 1: Write failing tests for JSON/JSONL/CSV loading.**

Each loader must return rows plus source metadata and reject an unsupported top-level payload with a clear error.

- [ ] **Step 2: Write a failing test for duplicate transaction merge.**

Two rows with the same transaction key and matching sale price/date must become one record with both source IDs retained.

- [ ] **Step 3: Write a failing test for conflicting sale prices.**

Two rows with the same address/date but materially different sale prices must produce one `data_conflict=true` record that is excluded from statistical work and retains both raw values/sources.

- [ ] **Step 4: Run focused tests and verify RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py`

Expected: loader/merge assertions fail because behavior is not implemented.

- [ ] **Step 5: Implement loaders and deterministic merge.**

Support `.json`, `.jsonl`, and `.csv`; preserve the complete raw observation in an audit field; merge only exact transaction identities; never infer that two different sale dates represent the same transaction.

- [ ] **Step 6: Run focused tests and verify GREEN.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py`

Expected: ingestion and merge tests pass.

### Task 3: Add benchmark registry, quality metrics, and physical cohorts

**Files:**
- Modify: `tests/test_research_location_sold_comps.py`
- Modify: `src/northshore_watchdog/research/location_sold_comps.py`

- [ ] **Step 1: Write failing tests for the ten required benchmark addresses.**

The registry must contain 2876 W 18th, 3974 W 10th, 333 E 13th, 486 E King Edward, 4561 Nanaimo, 3555 Trafalgar, 2887 W 24th, 440-442 E 10th, 21 E 41st, and 41 E 41st. A benchmark seed lacking a confirmed sale date/price remains visible but is never eligible for statistics.

- [ ] **Step 2: Write failing tests for quality coverage.**

The summary must report total observations, eligible sales, conflicts, out-of-scope rows, samples by neighbourhood/year, and non-null coverage for BCA, school, road, zoning, lot, house, MLS, sale date, and sale price.

- [ ] **Step 3: Write failing tests for the old-house physical cohort.**

The cohort requires 3,700–4,300 sqft lot, 30–36 ft frontage, 1,500–2,500 sqft living area, pre-1985, detached/freehold, and statistical eligibility.

- [ ] **Step 4: Run tests and verify RED, implement, then verify GREEN.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py`

Expected after implementation: all focused unit tests pass.

### Task 4: Generate V1.1 artifacts without overwriting V1

**Files:**
- Create: `tests/test_research_location_sold_comps_artifacts.py`
- Modify: `src/northshore_watchdog/research/location_sold_comps.py`

- [ ] **Step 1: Write a failing artifact test.**

Assert creation of:

```text
sold-comps-v1.1.csv
sold-comps-quality-report-v1.1.md
neighbourhood-statistics-v1.1.csv
physical-cohorts-v1.1.csv
school-boundary-pairs-v1.1.csv
road-matched-pairs-v1.1.csv
park-matched-pairs-v1.1.csv
view-matched-pairs-v1.1.csv
school-premium-v1.1.csv
area-baseline-premium-v1.1.csv
micro-location-premium-v1.1.csv
matched-pairs-v1.1.csv
hedonic-model-v1.1.md
benchmark-validation-v1.1.md
RECOMMENDATION-v1.1.md
```

- [ ] **Step 2: Verify RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps_artifacts.py`

Expected: runner/artifacts are absent.

- [ ] **Step 3: Implement deterministic CSV/Markdown generation.**

Neighbourhood statistics and premium/pair research may only consume statistically eligible rows. If there are fewer than 20 credible pairs, premium rows retain zero applied value and explicit `INSUFFICIENT_EVIDENCE`. Hedonic status is `DO_NOT_FIT` below 200 eligible sales, `EXPERIMENTAL_ONLY` for 200–499, and research-only at 500+; no coefficient is written into production config.

- [ ] **Step 4: Verify GREEN.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps.py tests/test_research_location_sold_comps_artifacts.py`

Expected: all V1.1 focused tests pass and no V1 artifact is changed.

### Task 5: Add the research CLI command

**Files:**
- Modify: `src/northshore_watchdog/cli.py`
- Modify: `tests/test_research_location_sold_comps_artifacts.py`

- [ ] **Step 1: Write a failing CLI test.**

Invoke `research-location-sold-comps` with repeated `--source-path`, explicit `--output-dir`, `--start-date`, and `--end-date`; assert the runner receives those exact values.

- [ ] **Step 2: Verify RED.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps_artifacts.py`

Expected: command is not registered.

- [ ] **Step 3: Register the read-only command.**

The command delegates to `run_sold_comps_research`, prints JSON, defaults to the local comparable snapshot, and does not call production scoring or write any production score/watchlist file.

- [ ] **Step 4: Verify GREEN.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps_artifacts.py`

Expected: CLI test passes.

### Task 6: Run local V1.1 and report the acquisition gate

**Files:**
- Create: the fifteen V1.1 artifacts under `research/location/`

- [ ] **Step 1: Run the local research command.**

Run:

```bash
rtk .venv/bin/python -m northshore_watchdog.cli research-location-sold-comps \
  --source-path data/research/sources/phase2_comparables.json \
  --output-dir research/location \
  --start-date 2023-09-06 \
  --end-date 2026-09-05
```

- [ ] **Step 2: Inspect quality and scope.**

Confirm City of North Vancouver rows are outside target scope, unknown-title fixtures are not silently promoted to freehold, incomplete benchmark seeds remain non-statistical, and asking prices are never used as sale prices.

- [ ] **Step 3: Enforce stage gate.**

If eligible samples are below 300, keep matched-pair premiums non-production and stop before claiming a calibrated location model. Record exact acquisition limitations and the minimum external data needed to resume.

- [ ] **Step 4: Run full verification.**

Run: `rtk .venv/bin/pytest -q`

Expected: zero failures.

Run: `rtk .venv/bin/python -m northshore_watchdog.cli research-location-sold-comps --help`

Expected: command help exits zero.

Run: `rtk rg -n "Current Use|Dual Value|RALV|MOS" src/northshore_watchdog/research/location_sold_comps.py`

Expected: documentation may mention protected models, but the new module must not import or mutate production scoring modules.

