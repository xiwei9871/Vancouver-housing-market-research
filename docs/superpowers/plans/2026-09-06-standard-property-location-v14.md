# Standard Property Location Residual V1.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recalculate Vancouver combined location residuals at one explicit standard detached-property profile and report the neutral standard value, area standard values, and age sensitivity without changing production scoring.

**Architecture:** Extend the research-only controlled residual model with a versioned standard-property profile. The model will use exact lot, frontage, depth, house size, beds, baths, zoning, and quiet-road assumptions; it will use 1960 as the age midpoint and report 1940/1980 sensitivity. Area residuals remain centred on the sample-weighted Vancouver prediction for that same counterfactual profile.

**Tech Stack:** Existing Python research modules, NumPy ridge model, PyYAML artifact writer, pytest, CSV/Markdown/YAML artifacts.

---

### Task 1: Lock the V1.4 standard-profile contract with failing tests

**Files:**
- Modify: `tests/test_research_location_premium.py`
- Modify: `tests/test_research_location_premium_stability.py`
- Modify: `tests/test_research_location_sold_comps_artifacts.py`

- [ ] **Step 1: Add a test for the exact standard-property profile.**

Assert the model diagnostics expose `lot_area_sqft=4026`, `frontage_ft=33`, `depth_ft=122`, `living_area_sqft=1700`, `beds=3`, `baths=2`, `year_built=1960`, zoning group `R`, and quiet road `E`, plus the declared age range `[1940, 1980]`.

- [ ] **Step 2: Add a test for neutral and area counterfactual values.**

Run the controlled model on at least 20 synthetic sales and assert diagnostics include a positive `neutral_standard_value`, each area row includes `standard_property_value`, and the area residual equals the area standard value minus the neutral standard value within rounding tolerance.

- [ ] **Step 3: Add a test for age sensitivity and the V1.4 profile artifact.**

Assert the stability result exposes `standard_property_profile`, age sensitivity for 1940/1960/1980, and the sold-comps runner writes `location-standard-profile-v1.4.yaml` when called with `artifact_version="v1.4"`.

- [ ] **Step 4: Run the focused tests and confirm the new assertions fail for the missing contract.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_premium.py tests/test_research_location_premium_stability.py tests/test_research_location_sold_comps_artifacts.py`

Expected: failures for missing standard profile, counterfactual values, age sensitivity, and V1.4 profile artifact.

### Task 2: Implement the fixed standard profile and controlled predictions

**Files:**
- Modify: `src/northshore_watchdog/research/location_premium.py`
- Modify: `src/northshore_watchdog/research/location_premium_stability.py`

- [ ] **Step 1: Add the explicit V1.4 profile constants and profile builder.**

Use the exact requested profile, use 1960 as the model age midpoint, retain 1940/1980 as sensitivity values, and use the eligible-sample median only for the control variable `bca_improvement` because the user did not specify a BCA improvement amount.

- [ ] **Step 2: Add the profile to `estimate_initial_location_premiums`.**

Fit the model as before, predict every neighbourhood using the same standard profile, calculate the sample-weighted Vancouver neutral standard value, and record each neighbourhood’s standard-property predicted value and residual. Keep road control and `automatic_application=False` unchanged.

- [ ] **Step 3: Add 1940/1960/1980 age sensitivity diagnostics.**

Predict the weighted city neutral value and each neighbourhood value at the three declared ages. Use the 1960 profile for the primary residual; expose the three neutral values for interpretation rather than averaging them.

- [ ] **Step 4: Pass the standard profile through all stability variants.**

Use the same fixed physical profile and age midpoint in FULL_36M, RECENT_24M, RECENT_12M, and OLD_HOUSE_STANDARD_LOT. Preserve the old cohort as a sensitivity dataset, not as the primary profile definition.

- [ ] **Step 5: Run the focused tests and confirm they pass.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_premium.py tests/test_research_location_premium_stability.py`

Expected: all focused tests pass.

### Task 3: Version the V1.4 artifacts and reports

**Files:**
- Modify: `src/northshore_watchdog/research/location_sold_comps.py`
- Modify: `src/northshore_watchdog/research/location_premium.py`
- Modify: `src/northshore_watchdog/research/location_premium_stability.py`
- Modify: `tests/test_research_location_sold_comps_artifacts.py`

- [ ] **Step 1: Add V1.4 CSV fields and the profile YAML artifact.**

Add standard-property value, neutral standard value, and age-sensitivity fields to the V1.4 premium/stability CSV contracts. Write `location-standard-profile-v1.4.yaml` with the exact profile, interpretation of `year_built_reference`, and the BCA-improvement control rule.

- [ ] **Step 2: Update Markdown rendering.**

Show the exact standard profile, neutral standard value, age sensitivity, and area standard value alongside the residual. State clearly that residual ranges are model sensitivity ranges, not formal confidence intervals, and that school remains combined.

- [ ] **Step 3: Preserve V1.1/V1.2/V1.3 artifact compatibility.**

Only add the new profile artifact and extra V1.4 fields to the new V1.4 run; do not alter old artifact sets or production files.

- [ ] **Step 4: Run artifact tests.**

Run: `rtk .venv/bin/pytest -q tests/test_research_location_sold_comps_artifacts.py`

Expected: all artifact tests pass.

### Task 4: Re-run the real local research and verify V1.4 outputs

**Files:**
- Create: `research/location/location-standard-profile-v1.4.yaml`
- Create: `research/location/location-premium-initial-v1.4.csv`
- Create: `research/location/location-premium-initial-v1.4.md`
- Create: `research/location/location-premium-stability-v1.4.csv`
- Create: `research/location/location-premium-stability-v1.4.md`
- Create: other versioned V1.4 sold-comps artifacts from the existing runner

- [ ] **Step 1: Run V1.4 against the local expanded BCA and official GIS snapshots.**

Use the existing local source paths and `--artifact-version v1.4`; do not perform new web acquisition.

- [ ] **Step 2: Inspect the V1.4 profile and residual outputs.**

Confirm the report states the exact 4,026 sqft/33×122/1,700 sqft/3-bed/2-bath profile, shows the neutral standard value, shows 1940/1960/1980 sensitivity, and exposes the WPG/Sunset residuals without claiming school or formal-causal estimates.

- [ ] **Step 3: Verify old artifacts are preserved.**

Confirm V1.3 files are unchanged and no production screening/RALV/MOS files are modified.

### Task 5: Full verification

- [ ] **Step 1: Run all tests.**

Run: `rtk .venv/bin/pytest -q`

Expected: zero failures.

- [ ] **Step 2: Compile and validate artifact integrity.**

Run: `rtk .venv/bin/python -m compileall -q src` and a local artifact check for V1.4 row counts, profile fields, and preserved V1.3 files.

- [ ] **Step 3: Report evidence and remaining interpretation limits.**

Report the actual neutral standard value, the V1.4 WPG/Sunset residuals and ranges, the fact that residuals are combined location effects, and that this remains research-only.
