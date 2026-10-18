# Full 166-Property Research Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the existing 166-property official-enrichment cache, fill deterministic official fields where possible, codify the applicable zoning and valuation rules, and generate one auditable three-layer research report for every property.

**Architecture:** Keep acquisition and the existing screening engine unchanged. Add a bounded replay/enrichment layer for only missing fields, a versioned Vancouver rule registry for official zoning interpretation and transparent estimates, and a portfolio report generator that renders all 166 cards plus the existing score breakdowns and scenario calculations. Unknown or estimated values remain explicitly labelled and never become fabricated facts.

**Tech Stack:** Python 3.11+, Pydantic models already used by the research package, YAML configuration, JSON/CSV local artifacts, Markdown reports, pytest.

---

### Task 1: Audit and persist the local completion manifest

**Files:**
- Create: `src/northshore_watchdog/research/portfolio.py`
- Create: `tests/test_research_portfolio.py`
- Create: `scripts/run_full_property_reports.py`
- Create: `data/research/normalized/portfolio-completion-2026-09-07.json`

- [ ] **Step 1: Write the failing tests**

Test that the portfolio audit reports all input cards, separates missing official fields from optional fields, and preserves the exact property identity:

```python
def test_audit_reports_all_cards_and_explicit_missing_fields():
    result = audit_portfolio([card_with_missing_zoning(), card_with_complete_fields()])
    assert result["property_count"] == 2
    assert result["missing_official"]["zoning"] == 1
    assert result["missing_official"]["road"] == 0
    assert result["optional_missing"]["sold_history"] == 2

def test_property_key_prefers_mls_then_address():
    assert property_key(card_with_mls()) == "mls:R123"
    assert property_key(card_without_mls()) == "address:100 MAIN STREET"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `rtk pytest tests/test_research_portfolio.py::test_audit_reports_all_cards_and_explicit_missing_fields tests/test_research_portfolio.py::test_property_key_prefers_mls_then_address -q`

Expected: FAIL because `portfolio.py` and its audit functions do not exist.

- [ ] **Step 3: Implement the audit functions**

Implement `property_key(card)` and `audit_portfolio(cards)` using the existing `DiscoveryCard` shape. Required official coverage keys are `bca`, `zoning`, `road`, `school_catchment`, `lot_geometry`; optional keys are `school_performance`, `sold_history`, `listing_history`, and `approved_use`. Count a field as present only when the normalized official/raw value is non-empty; do not count a listing description as official zoning.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `rtk pytest tests/test_research_portfolio.py::test_audit_reports_all_cards_and_explicit_missing_fields tests/test_research_portfolio.py::test_property_key_prefers_mls_then_address -q`

Expected: PASS.

- [ ] **Step 5: Run the audit against the cached 166-card artifact**

Run: `rtk python scripts/run_full_property_reports.py --audit-only --official-artifact data/research/normalized/regional-official-enrichment-2026-09-07.json`

Expected: JSON reports `property_count=166`, `bca=166`, `road=166`, `zoning=164`, `school_catchment=165`, and a separate optional-field gap report.

### Task 2: Add a versioned zoning-rule registry and scenario calculation

**Files:**
- Create: `config/vancouver-zoning-rules-2026-09-07.yaml`
- Modify: `src/northshore_watchdog/research/portfolio.py`
- Test: `tests/test_research_portfolio.py`

- [ ] **Step 1: Write the failing tests**

Test that a rule is selected only for an official zoning value, that lot thresholds produce transparent eligibility, and that unknown zoning remains unknown:

```python
def test_zoning_rule_calculates_legal_area_and_unit_options():
    result = zoning_scenarios(zoning="R1-1", municipality="City of Vancouver", lot_area_sqft=5525, frontage_ft=50, depth_ft=110)
    assert result["rule_status"] == "OFFICIAL_RULE_APPLIED"
    assert result["maximum_fsr"] == 1.0
    assert result["theoretical_buildable_sqft"] == 5525
    assert result["multiplex_max_units"] >= 5

def test_unknown_zoning_does_not_invent_development_rights():
    result = zoning_scenarios(zoning=None, municipality="City of Vancouver", lot_area_sqft=4000, frontage_ft=33, depth_ft=122)
    assert result["rule_status"] == "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    assert result["theoretical_buildable_sqft"] is None
    assert result["best_product"] == "UNKNOWN"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `rtk pytest tests/test_research_portfolio.py::test_zoning_rule_calculates_legal_area_and_unit_options tests/test_research_portfolio.py::test_unknown_zoning_does_not_invent_development_rights -q`

Expected: FAIL because the rule registry and `zoning_scenarios` do not exist.

- [ ] **Step 3: Add official source metadata and conservative rules**

Create the YAML registry with `source_url`, `retrieved_at`, `bylaw_version`, and explicit entries for every zoning code observed in the 166-card artifact. Rules must include `maximum_fsr`, `maximum_units`, `minimum_site_area_m2`, `minimum_frontage_m`, `duplex_permitted`, `multiplex_permitted`, `rear_access_required`, and the official schedule URL. Keep TOA as a separate `long_term_option` block requiring rezoning/rental-policy review; never treat TOA as current entitlement.

- [ ] **Step 4: Implement the scenario calculator**

Add `zoning_scenarios(...)` to load the registry, convert square feet to square metres, apply site/frontage/depth and unit thresholds, and return `OFFICIAL_RULE_APPLIED`, `RULE_NOT_FOUND`, or `UNKNOWN_REQUIRES_OFFICIAL_REVIEW`. Return theoretical area, realistic area using the configured efficiency, legal product candidates for 2–6 units, average unit size, likely family product, and an explicit `ESTIMATE` status for cost/revenue outputs.

- [ ] **Step 5: Run focused tests and inspect observed zoning codes**

Run: `rtk pytest tests/test_research_portfolio.py -q`

Then run: `rtk python scripts/run_full_property_reports.py --audit-only --official-artifact data/research/normalized/regional-official-enrichment-2026-09-07.json`

Expected: tests pass and the audit lists any zoning codes without a registry entry before report generation.

### Task 3: Add transparent maintenance/reno and value calculations

**Files:**
- Modify: `src/northshore_watchdog/research/portfolio.py`
- Modify: `config/research-discovery.yaml:360-490`
- Test: `tests/test_research_portfolio.py`

- [ ] **Step 1: Write the failing tests**

Test the user-approved reno bands and the BCA-based physical basis:

```python
def test_reno_bands_are_exact_and_not_age_only_zero():
    assert reno_score(0) == (100, "MOVE_IN_READY")
    assert reno_score(30000) == (80, "LIGHT_RENO")
    assert reno_score(60000) == (60, "MODERATE_RENO")
    assert reno_score(100000) == (40, "SIGNIFICANT_RENO")
    assert reno_score(150000) == (20, "HEAVY_RENO")
    assert reno_score(150001) == (0, "CURRENT_USE_PASS")

def test_physical_basis_uses_bca_improvement_and_location_residual():
    value = normalized_physical_basis(asking=1875000, bca_improvement=30200, location_residual=563000, lot_area_sqft=4042.5)
    assert value["market_land_basis"] == 1844800
    assert value["normalized_lot_basis"] == 1281800
    assert value["normalized_lot_basis_per_sqft"] == 317.0
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `rtk pytest tests/test_research_portfolio.py::test_reno_bands_are_exact_and_not_age_only_zero tests/test_research_portfolio.py::test_physical_basis_uses_bca_improvement_and_location_residual -q`

Expected: FAIL because the portfolio helpers are not implemented.

- [ ] **Step 3: Implement rules without replacing the existing score engine**

Use current official BCA improvement as `MARKET_HOUSE_VALUE_BCA_PROXY`, preserve it separately from `holding_utility_value`, and calculate:

```text
market_land_basis = asking - bca_improvement
normalized_lot_basis = asking - bca_improvement - location_residual
normalized_lot_basis_per_sqft = normalized_lot_basis / lot_area_sqft
```

Use the existing user-approved six reno bands. Reno evidence may come from explicit recent listing updates, permit evidence, and age fallback, but age fallback must be labelled `AGE_FALLBACK`; it must not claim the house was maintained well.

- [ ] **Step 4: Add report rule metadata**

Add a `portfolio_report` section to config with `standard_lot_width_ft=33`, `standard_lot_depth_ft=122`, `standard_lot_area_sqft=4026`, `standard_house_sqft=1700`, `road_discount_mode=USER_APPROVED_PRIOR`, `unknown_value_policy=SHOW_UNKNOWN`, `data_completeness_affects_score=false`, and `research_as_of=2026-09-07`.

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `rtk pytest tests/test_research_portfolio.py -q`

Expected: PASS.

### Task 4: Generate one full report per property and a ranked index

**Files:**
- Modify: `src/northshore_watchdog/research/portfolio.py`
- Modify: `scripts/run_full_property_reports.py`
- Create: `reports/properties/2026-09-07/index.md`
- Create: `reports/properties/2026-09-07/*.md` (one file per card)
- Create: `data/research/normalized/portfolio-analysis-2026-09-07.json`
- Test: `tests/test_research_portfolio.py`

- [ ] **Step 1: Write the failing tests**

Test that every card receives a report and that the report contains the required three-layer analysis and provenance labels:

```python
def test_render_property_report_contains_three_layers_and_evidence_labels():
    report = render_property_report(card, decision)
    assert "## 1. Current Use" in report
    assert "## 2. Redevelopment" in report
    assert "## 3. Confidence and Data Gaps" in report
    assert "OFFICIAL_CONFIRMED" in report
    assert "ESTIMATE" in report

def test_portfolio_generation_writes_exactly_one_report_per_card(tmp_path):
    paths = write_portfolio_reports(cards, decisions, tmp_path)
    assert len(paths) == len(cards)
    assert len(list(tmp_path.glob("*.md"))) == len(cards)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `rtk pytest tests/test_research_portfolio.py::test_render_property_report_contains_three_layers_and_evidence_labels tests/test_research_portfolio.py::test_portfolio_generation_writes_exactly_one_report_per_card -q`

Expected: FAIL because report rendering and portfolio writing do not exist.

- [ ] **Step 3: Implement the report renderer**

Render each property with: identity/listing, BCA assessment and discounts, lot geometry, road class and configured percentage range, school catchments/performance, zoning rule and legal scenarios, TOA option, current-house condition and reno evidence, BCA-based land/house calculations, redevelopment cost/revenue/profit assumptions, Current Use/Redevelopment/Dual/Investment scores with component points, recommendation, investment thesis, and a per-field status table (`OFFICIAL_CONFIRMED`, `DERIVED_FROM_OFFICIAL_RULES`, `ESTIMATE`, `LISTING_OBSERVED`, `UNKNOWN`).

- [ ] **Step 4: Implement portfolio output and ranking index**

Write deterministic slugs with MLS suffixes where available. Generate an index containing all 166 rows sorted by Current Use, Dual Value, Redevelopment, and Investment, while retaining special/excluded rows in their own sections. Do not cap the individual reports at top 10.

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `rtk pytest tests/test_research_portfolio.py -q`

Expected: PASS.

### Task 5: Run the local replay, targeted official refresh, and full artifact generation

**Files:**
- Modify: `scripts/run_full_property_reports.py`
- Create: `data/research/normalized/portfolio-completion-2026-09-07.json`
- Create: `data/research/normalized/portfolio-analysis-2026-09-07.json`
- Create: `reports/properties/2026-09-07/index.md`
- Create: `reports/properties/2026-09-07/*.md`

- [ ] **Step 1: Run targeted missing-field refreshes**

Use the existing local replay and official adapters for only missing zoning/school/lot fields. Preserve prior successful evidence and append a new versioned output; never overwrite the 2026-09-07 source artifact in place.

- [ ] **Step 2: Generate all reports from the refreshed artifact**

Run: `rtk python scripts/run_full_property_reports.py --official-artifact <refreshed-artifact> --output-date 2026-09-07`

Expected: exactly 166 property Markdown files plus `index.md` and JSON manifests.

- [ ] **Step 3: Audit report counts and rule statuses**

Run: `rtk python scripts/run_full_property_reports.py --audit-only --official-artifact <refreshed-artifact>`

Expected: all 166 cards accounted for; no report silently omitted; missing/unknown fields listed explicitly.

### Task 6: Full verification and acceptance report

**Files:**
- Create: `reports/properties/2026-09-07/ACCEPTANCE-REPORT.md`
- Modify: `docs/superpowers/plans/2026-09-07-full-166-property-research-plan.md`

- [ ] **Step 1: Run the portfolio tests**

Run: `rtk pytest tests/test_research_portfolio.py -q`

Expected: all new tests pass.

- [ ] **Step 2: Run the existing research regression suite**

Run: `rtk pytest tests/test_research_*.py -q`

Expected: existing research tests remain green.

- [ ] **Step 3: Validate generated artifacts**

Run: `rtk python scripts/run_full_property_reports.py --validate --official-artifact <refreshed-artifact> --output-date 2026-09-07`

Expected: `166 reports`, `0 duplicate report keys`, and no missing required section in any report.

- [ ] **Step 4: Write the acceptance report**

Document exact file paths, coverage before/after refresh, rules applied, fields remaining UNKNOWN, score methodology, 166-report count, and blockers. State clearly that missing data does not reduce scores and that estimates are not official facts.

