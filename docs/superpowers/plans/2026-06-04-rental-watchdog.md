# Rental Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Realtor.ca/Zolo-only rental radar for 2 bed condo/apartment rentals in Richmond, Vancouver, and optional Burnaby, optimized for Chinese-speaking parents and a CAD 2,500-3,000 target rent.

**Architecture:** Add a separate `northshore_watchdog.rentals` package with rental-specific Pydantic models, local snapshot/email/manual-link collectors, scoring, reporting, storage, and CLI commands. Reuse the existing project dependencies, Feishu/OpenClaw delivery path, and local-only data-source posture without changing detached-home scoring.

**Tech Stack:** Python 3.11+, Pydantic, BeautifulSoup4, SQLite, Typer, pytest, existing OpenClaw CLI script integration.

---

### Task 1: Rental Models And Scoring

**Files:**
- Create: `src/northshore_watchdog/rentals/models.py`
- Create: `src/northshore_watchdog/rentals/scoring.py`
- Test: `tests/test_rental_scoring.py`

- [ ] **Step 1: Write failing tests** for A/B/C/Excluded behavior:
  - Richmond 2 bed 2 bath at CAD 2,900 near SkyTrain with gym/pool scores A.
  - Vancouver downtown CAD 3,250 scores B unless other risk exists.
  - Basement/shared/short-term rentals are excluded.
  - Missing source domain or non-Realtor/Zolo domain is excluded.

- [ ] **Step 2: Run targeted tests** with `.venv/bin/pytest tests/test_rental_scoring.py -q` and confirm failure from missing package.

- [ ] **Step 3: Implement models and scoring** with fields for rent, beds/baths, city, neighbourhood, property type, amenities, transit minutes, Chinese-community score, risks, reasons, grade, and confidence.

- [ ] **Step 4: Re-run targeted tests** and confirm pass.

### Task 2: Rental Collectors And Storage

**Files:**
- Create: `src/northshore_watchdog/rentals/collectors.py`
- Create: `src/northshore_watchdog/rentals/storage.py`
- Test: `tests/test_rental_collectors.py`

- [ ] **Step 1: Write failing tests** for parsing local HTML and manual links from Realtor.ca/Zolo only.
- [ ] **Step 2: Run targeted tests** and confirm failure.
- [ ] **Step 3: Implement collectors** that parse local `.html/.txt/.eml` content for URLs, rent, address, bed/bath, city, and source domain without performing network requests.
- [ ] **Step 4: Implement SQLite storage** keyed by normalized address/city/rent/url hash.
- [ ] **Step 5: Re-run targeted tests** and confirm pass.

### Task 3: Rental Report And CLI

**Files:**
- Create: `src/northshore_watchdog/rentals/report.py`
- Modify: `src/northshore_watchdog/cli.py`
- Test: `tests/test_rental_report.py`

- [ ] **Step 1: Write failing report tests** for Chinese markdown summary, A/B/C counts, top rental candidates, risks, and next actions.
- [ ] **Step 2: Run targeted tests** and confirm failure.
- [ ] **Step 3: Implement report builder** with Chinese output.
- [ ] **Step 4: Add CLI commands** `rentals-collect`, `rentals-score`, `rentals-report`, and `rentals-run-weekly`.
- [ ] **Step 5: Re-run targeted tests** and confirm pass.

### Task 4: Config, Samples, Docs, And Schedule

**Files:**
- Create: `config/rentals.yaml`
- Create: `data/rentals/samples/realtor_zolo_rental_snapshot_sample.html`
- Modify: `README.md`
- Modify: `scripts/run_weekly_watchdog.sh`

- [ ] **Step 1: Add sample data** covering Richmond A, Vancouver B, basement excluded, and non-allowed domain excluded.
- [ ] **Step 2: Add config** with budget bands, preferred regions, allowed domains, amenities, and report path.
- [ ] **Step 3: Update README** with rental radar commands and data-source rules.
- [ ] **Step 4: Extend weekly script** to run rental weekly report and include rental summary in Feishu message.
- [ ] **Step 5: Run full test suite** with `.venv/bin/pytest -q`.
- [ ] **Step 6: Generate sample report** with `.venv/bin/northshore-watchdog rentals-run-weekly --config config/rentals.yaml`.

### Self-Review

- Spec coverage: covers Realtor.ca/Zolo-only data sources, 2 bed rental focus, CAD 2,500-3,000 budget, Chinese-community weighting, pool/gym amenities, Chinese report, and weekly Feishu integration.
- Placeholder scan: no open-ended implementation placeholders remain.
- Type consistency: rental types are separate from purchase `Listing` and `ScoredListing` to avoid changing existing buy-side behavior.
