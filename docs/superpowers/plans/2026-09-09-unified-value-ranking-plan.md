# Unified Property Value and Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calculate one complete house-plus-land value ledger for every normal listing, then generate Current Use, Redevelopment, and Dual leaderboards as transparent views of the same ledger.

**Architecture:** Keep discovery and enrichment unchanged. Extend the existing screening decision with explicit area-only and risk-adjusted land-basis metrics, BCA-based house value, renovation-adjusted current-use basis, and separate price-safety signals. Generate all three rankings from the same scored rows.

**Tech Stack:** Python 3, Pydantic, YAML, pytest, JSON/Markdown reports.

---

## File map

- Modify: `src/northshore_watchdog/research/screening.py` — canonical ledger and shared score calculations.
- Modify: `src/northshore_watchdog/research/portfolio.py` and `src/northshore_watchdog/research/discovery_report.py` — expose ledger and score components.
- Modify: `config/research-discovery.yaml` — versioned shared-component weights and thresholds.
- Modify: `tests/test_research_screening.py` and `tests/test_research_portfolio.py` — behavior and report regression tests.
- Generate: `data/research/normalized/regional-official-enrichment-2026-09-09.json` and `reports/properties/2026-09-09/*` with the existing runner.

### Task 1: Add failing tests for the unified ledger

- [ ] Add tests that assert every scored row exposes `current_use_effective_basis`, `area_normalized_lot_basis_per_sqft`, `risk_adjusted_lot_basis_per_sqft`, `current_use_value_for_money_score`, and `redevelopment_value_for_money_score`.
- [ ] Add a regression test that 2012 York's materially higher normalized lot basis produces a lower lot-value-for-money component than 3910 W10 and cannot be ignored by Current Use ranking.
- [ ] Add a test that the current-use score does not contain a standalone `location_residual` component when regional price safety already uses the residual in its denominator.
- [ ] Add a test that renovation is added to the current-use basis and is not converted into a maintenance bonus greater than its configured component cap.
- [ ] Run the focused tests with `rtk pytest -q tests/test_research_screening.py -k 'unified or lot_basis or residual or renovation'` and confirm they fail for the missing/obsolete behavior.

### Task 2: Implement explicit canonical value fields

- [ ] Add typed decision fields for the canonical ledger while retaining old `normalized_lot_basis*` names as compatibility aliases.
- [ ] Implement:

```python
market_land_basis = asking - bca_improvement
area_normalized_lot_basis = market_land_basis - area_location_residual
risk_adjusted_lot_basis = market_land_basis - total_location_value
area_normalized_lot_basis_per_sqft = area_normalized_lot_basis / lot_area_sqft
risk_adjusted_lot_basis_per_sqft = risk_adjusted_lot_basis / lot_area_sqft
```

- [ ] Store both standard-lot equivalents using the configured 4,026 sqft reference. Return `None`/`UNKNOWN` where required inputs are missing.
- [ ] Set `market_house_value_proxy` to official BCA improvement value without an inferred condition multiplier.
- [ ] Set `current_use_effective_basis = asking + reno_estimate` and expose `regional_price_ratio`, `regional_price_discount_pct`, and `bca_discount_pct` separately.
- [ ] Run the focused tests and confirm the ledger tests pass.

### Task 3: Implement shared value-for-money scoring

- [ ] Add failing tests asserting the score component names `house_value_for_money`, `location_value_for_money`, `lot_value_for_money`, `regional_price_safety`, `bca_price_safety`, and `permanent_risk` exist and sum to the authoritative score.
- [ ] Add the following versioned initial weights to `config/research-discovery.yaml`:

```yaml
orthogonal_scoring:
  value_for_money_components:
    current_use:
      house_value_for_money: 25
      location_value_for_money: 20
      lot_value_for_money: 25
      regional_price_safety: 15
      bca_price_safety: 10
      permanent_risk: 5
    redevelopment:
      house_value_for_money: 5
      location_value_for_money: 15
      lot_value_for_money: 40
      regional_price_safety: 15
      bca_price_safety: 10
      permanent_risk: 15
```

- [ ] Implement shared bounded ratio scores; lower normalized lot basis per sqft is better, and missing reference values are neutral/unknown rather than zero.
- [ ] Remove the additive current-use residual component when the regional standard value already includes that residual. Preserve raw residual and regional price discount as report fields.
- [ ] Compute Current Use and Redevelopment from their configured views over the same ledger. Compute Dual as `sqrt(current_use_score * redevelopment_score)` with existing minimum floors.
- [ ] Run `rtk pytest -q tests/test_research_screening.py` and update only assertions that intentionally describe obsolete score behavior.

### Task 4: Update explainable reports

- [ ] Add a failing report test requiring Asking, Reno/effective basis, BCA improvement/discount, location residual/regional discount, both normalized lot-basis variants, road adjustment, Current Use, Redevelopment, and Dual values.
- [ ] Update property and leaderboard report rendering to show raw values, score components, formulas/variants, and `UNKNOWN` explicitly.
- [ ] Run `rtk pytest -q tests/test_research_portfolio.py tests/test_research_report.py`.

### Task 5: Recalculate and verify all 166 listings

- [ ] Run the existing full-property-report command against the saved official enrichment snapshot; do not perform new acquisition.
- [ ] Verify the normal scored rows remain 158 plus the same special transaction rows, and every normal row has the unified ledger fields or explicit missing values.
- [ ] Verify all three leaderboards are created from the same rows and authoritative score fields.
- [ ] Inspect `3910 W10` vs `2012 York`, `4561 Nanaimo` vs `5454 Melbourne`, and the benchmark set. Confirm the reports make the ranking reasons visible.
- [ ] Run `rtk pytest -q` and report the exact result before claiming completion.

## Final handoff

Report files changed/generated, formulas, weights, score components, representative comparisons, remaining unknowns, and full verification output.
