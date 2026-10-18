# Property Radar Freshness and Weekly Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Saturday purchase and rental radars reject stale local snapshots, preserve the last normal report, and show trustworthy week-over-week listing changes when fresh snapshots are supplied.

**Architecture:** Add a pure snapshot-monitoring module that inspects configured local files and supplies selected fresh paths plus provenance metadata to the existing collectors. Add an append-only run ledger to each existing radar SQLite database, use stable listing identities independent of snapshot filenames/prices, and pass the resulting freshness and delta context into the existing reports. The shell wrapper will run both commands independently, then send one stale/error alert or the existing normal summary based on the two per-run status files.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, hashlib, sqlite3, Pydantic 2 models, PyYAML, Typer, pytest, Bash, jq.

---

## Scope and file map

- Create `src/northshore_watchdog/monitoring.py` for snapshot discovery, age/hash metadata, freshness decisions, status JSON, and stale artifacts.
- Create `src/northshore_watchdog/history.py` for append-only radar runs, run listings, stable current-run deltas, and JSON-ready delta data.
- Modify `src/northshore_watchdog/normalize.py` so purchase identity prefers MLS/URL/address and never uses snapshot filename or current price.
- Modify `src/northshore_watchdog/rentals/storage.py` so rental identity prefers canonical URL/address and never uses snapshot filename or current rent.
- Modify `src/northshore_watchdog/collectors/search_page_snapshot_collector.py`, `src/northshore_watchdog/collectors/email_alert_collector.py`, and `src/northshore_watchdog/rentals/collectors.py` to honor selected fresh files and attach snapshot provenance.
- Modify `src/northshore_watchdog/report.py` and `src/northshore_watchdog/rentals/report.py` to render freshness and weekly-change sections while preserving existing default-call behavior.
- Modify `src/northshore_watchdog/cli.py` to gate weekly commands before storage writes, persist successful fresh runs, and write per-run statuses.
- Modify `config/real.yaml` and `config/rentals.yaml` with seven-day monitoring requirements.
- Modify `scripts/run_weekly_watchdog.sh` to pass unique status paths, run both radars despite one command failure, and choose stale/error/normal delivery.
- Create `tests/test_monitoring.py`, `tests/test_history.py`, and `tests/test_weekly_watchdog.py`; extend collector, normalize, report, and rental report tests where the new behavior is directly exercised.

The target directory has no Git metadata, so no commit commands will be run. Existing generated SQLite/report artifacts are test inputs and will not be deleted or reset.

---

### Task 1: Snapshot monitoring and stable identities

**Files:**
- Create: `src/northshore_watchdog/monitoring.py`
- Modify: `src/northshore_watchdog/normalize.py`
- Modify: `src/northshore_watchdog/collectors/search_page_snapshot_collector.py`
- Modify: `src/northshore_watchdog/collectors/email_alert_collector.py`
- Modify: `src/northshore_watchdog/rentals/collectors.py`
- Modify: `src/northshore_watchdog/rentals/storage.py`
- Create: `tests/test_monitoring.py`
- Modify: `tests/test_collectors.py`
- Modify: `tests/test_rental_collectors.py`

- [ ] **Step 1: Write the failing monitoring and identity tests.**

Add tests with an injectable `now` and temporary files whose mtimes are set with `os.utime`:

```python
def test_required_directories_are_stale_when_no_file_is_within_seven_days(tmp_path):
    stale = tmp_path / "stale.html"
    stale.write_text("<html />", encoding="utf-8")
    old = datetime(2026, 8, 30, 12, tzinfo=timezone.utc).timestamp() - 8 * 86400
    os.utime(stale, (old, old))

    result = inspect_input_freshness(
        {"monitoring": {"max_age_days": 7, "required_collectors": ["search_page_snapshot"]},
         "collectors": {"search_page_snapshot": {"enabled": True, "input_dirs": [str(tmp_path)]}}},
        now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )

    assert result.status == "stale"
    assert result.stale_directories == [str(tmp_path)]


def test_freshness_selects_only_recent_supported_files_and_hashes_content(tmp_path):
    fresh = tmp_path / "fresh.html"
    ignored = tmp_path / "notes.pdf"
    fresh.write_text("same content", encoding="utf-8")
    ignored.write_text("not a listing input", encoding="utf-8")
    observed = datetime(2026, 8, 29, 12, tzinfo=timezone.utc).timestamp()
    os.utime(fresh, (observed, observed))

    result = inspect_input_freshness(
        {"monitoring": {"max_age_days": 7, "required_collectors": ["search_page_snapshot"]},
         "collectors": {"search_page_snapshot": {"enabled": True, "input_dirs": [str(tmp_path)]}}},
        now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )

    assert result.status == "fresh"
    assert [item.path for item in result.selected_files["search_page_snapshot"]] == [str(fresh)]
    assert result.selected_files["search_page_snapshot"][0].sha256


def test_listing_identity_is_stable_across_snapshot_names_and_prices():
    first = Listing(source="search_page_snapshot", source_id="old.html:0", mls_number="R3000001", url="https://www.realtor.ca/real-estate/1/home", address="1 Main Street", city="North Vancouver", list_price=1_500_000)
    second = first.model_copy(update={"source_id": "new.html:0", "list_price": 1_425_000})

    assert listing_dedupe_key(first) == listing_dedupe_key(second)


def test_rental_identity_is_stable_across_snapshot_names_and_rents():
    first = RentalListing(source="rental_snapshot", source_id="old.html:0", url="https://www.zolo.ca/richmond-real-estate/1-main-street/101", address="101 - 1 Main Street", city="Richmond", monthly_rent=3000)
    second = first.model_copy(update={"source_id": "new.html:0", "monthly_rent": 2800})

    assert rental_dedupe_key(first) == rental_dedupe_key(second)
```

- [ ] **Step 2: Run the focused tests and confirm they fail for the missing behavior.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_monitoring.py tests/test_collectors.py tests/test_rental_collectors.py
```

Expected: the new monitoring import is unavailable and the identity assertions fail because current keys use `source_id`.

- [ ] **Step 3: Implement monitoring and stable identity minimally.**

`monitoring.py` will provide `SnapshotMetadata`, `FreshnessResult`, `inspect_input_freshness(config, now=None)`, `snapshot_metadata(path, collector, now=None)`, `flatten_selected_files(result)`, and `input_fingerprint(result)`. It will support `.html/.htm` for purchase search snapshots and `.html/.htm/.txt/.eml` for rental/email inputs. A required collector is stale if it is disabled, has no configured directory, has a missing directory, or has no fresh file in any required directory when `require_each_input_dir` is true. Optional collectors are represented as warnings only. A file is fresh when `0 <= age_days <= max_age_days`; all age calculations use timezone-aware datetimes.

Change purchase identity to:

```python
def listing_dedupe_key(listing: Listing) -> str:
    if listing.mls_number:
        return f"mls:{listing.mls_number.strip().upper()}"
    if listing.url:
        return f"url:{canonical_url(listing.url)}"
    return f"address:{normalized_address(listing.address)}|{slug_text(listing.city)}"
```

Add `canonical_url` using `urlsplit`, lower-case scheme/host, remove fragments, and strip trailing path slashes. Apply the analogous URL/address-only rule to `rental_dedupe_key`.

Each file collector will use `config["selected_files"]` when present; otherwise it retains its current directory scan. On every parsed file it will compute one `SnapshotMetadata`, set `last_seen_date` to `observed_at`, and merge these keys into `raw_data`: `snapshot_path`, `snapshot_observed_at`, and `snapshot_sha256`. Existing collector call sites without `selected_files` continue to work.

- [ ] **Step 4: Run the focused tests and existing collector tests.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_monitoring.py tests/test_collectors.py tests/test_rental_collectors.py tests/test_scoring.py tests/test_risk_rules.py
```

Expected: all focused and legacy tests pass, and no collector test needs a network call.

---

### Task 2: Append-only run history and weekly diff

**Files:**
- Create: `src/northshore_watchdog/history.py`
- Create: `tests/test_history.py`

- [ ] **Step 1: Write failing history tests.**

Test the baseline and later comparison contract with plain history entries. Define the fixtures used by the examples at the top of `tests/test_history.py`:

```python
START = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
END = datetime(2026, 8, 30, 12, 1, tzinfo=timezone.utc)

def history_listing(identity_key, address, price):
    return HistoryListing(
        identity_key=identity_key,
        address=address,
        city="North Vancouver",
        price=price,
        grade="Review",
        score_total=60,
        payload={"identity_key": identity_key, "address": address, "price": price},
    )

def test_first_successful_run_is_a_baseline(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="purchase")
    current = [history_listing("mls:R1", "1 Main Street", 1500000)]

    delta = history.compare(current)
    run_id = history.record_success(
        started_at=START,
        finished_at=END,
        source_files=[{"path": "fresh.html", "sha256": "abc"}],
        input_fingerprint="fingerprint-1",
        entries=current,
        delta=delta,
    )

    assert run_id == 1
    assert delta.baseline is True
    assert delta.new == []


def test_second_run_reports_new_price_changes_and_not_seen(tmp_path):
    history = RadarHistory(tmp_path / "radar.sqlite", radar="purchase")
    previous = [history_listing("mls:R1", "1 Main Street", 1500000), history_listing("mls:R2", "2 Main Street", 1600000)]
    baseline = history.compare(previous)
    history.record_success(START, END, [], "one", previous, baseline)

    current = [history_listing("mls:R1", "1 Main Street", 1400000), history_listing("mls:R3", "3 Main Street", 1700000)]
    delta = history.compare(current)

    assert [item.identity_key for item in delta.new] == ["mls:R3"]
    assert [(item.previous_price, item.current_price) for item in delta.price_reduced] == [(1500000, 1400000)]
    assert delta.price_increased == []
    assert [item.identity_key for item in delta.not_seen] == ["mls:R2"]
```

- [ ] **Step 2: Run the history tests and verify the expected missing-module failure.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_history.py
```

Expected: FAIL because `RadarHistory` and the delta models do not yet exist.

- [ ] **Step 3: Implement the history module.**

Define `HistoryListing`, `PriceChange`, and `RunDelta` dataclasses with `to_dict()` methods. Define `RadarHistory(path, radar)` with tables `radar_runs` and `radar_run_listings`, `compare(entries)`, `record_success(...)`, and `close()`. `compare` reads only the latest row with `status='success'` and `data_status='fresh'`; the first run is baseline with no new events. Price changes require both prices to be non-null. `not_seen` means absent from the current input only. `record_success` inserts the run and all listing rows in one transaction, storing sorted JSON for source files and the delta.

- [ ] **Step 4: Run the history tests and verify legacy databases remain readable.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_history.py tests/test_research_storage.py
```

Expected: all tests pass; the history schema is additive and does not remove existing tables.

---

### Task 3: Freshness-gated CLI and reports

**Files:**
- Modify: `config/real.yaml`
- Modify: `config/rentals.yaml`
- Modify: `src/northshore_watchdog/report.py`
- Modify: `src/northshore_watchdog/rentals/report.py`
- Modify: `src/northshore_watchdog/cli.py`
- Create: `tests/test_weekly_watchdog.py`
- Modify: `tests/test_report.py`
- Modify: `tests/test_rental_report.py`

- [ ] **Step 1: Write failing CLI/report tests.**

Create temporary configs with absolute input, SQLite, report, status, and alert paths. Use old file mtimes to prove stale runs do not rewrite the report/database; use two fresh runs with differently named files to prove history deltas:

```python
def write_snapshot(path, price, address, mls):
    path.write_text(
        f'''<article class="listing-card" data-address="{address}" data-city="North Vancouver"
          data-url="https://www.realtor.ca/real-estate/1/{address.lower().replace(" ", "-")}"
          data-mls-number="{mls}" data-price="${price:,}" data-property-type="detached house"
          data-tenure="freehold" data-status="active" data-bedrooms="3" data-bathrooms="2"
          data-house-size="1800" data-lot-size="6000" data-year-built="1980"></article>''',
        encoding="utf-8",
    )

def write_purchase_config(tmp_path, source_dir, db, report):
    config = {
        "monitoring": {"max_age_days": 7, "required_collectors": ["search_page_snapshot"], "require_each_input_dir": True, "alert_dir": str(tmp_path / "alerts")},
        "collectors": {"search_page_snapshot": {"enabled": True, "input_dirs": [str(source_dir)], "allowed_domains": ["realtor.ca"]}},
        "assessment": {"input_path": str(tmp_path / "missing-assessment.csv")},
        "storage": {"sqlite_path": str(db)},
        "report": {"weekly_output": str(report)},
        "budgets": {"west_vancouver": {"official_max": 1700000, "watch_max": 2000000}, "north_vancouver": {"official_max": 1500000, "watch_max": 1750000}},
        "year_built": {"core_min": 1970, "core_max": 2000, "backup_min": 1950, "backup_max": 2005},
        "lot_size": {"west_vancouver": {"target": [6000, 9000]}, "north_vancouver": {"target": [4000, 7500]}},
        "frontage": {"west_vancouver": 50, "north_vancouver": 40},
        "regions": {"west_vancouver": {}, "north_vancouver": {"preferred_region": ["North Vancouver"]}},
        "grade_thresholds": {"b_without_assessment_min": 58, "review_min": 45},
        "grade_caps": {"missing_assessment": "B", "near_miss_price": "B", "older_home_pre_1965": "Review", "older_home_1950s_1960s": "B", "lifestyle_or_slope_region": "Review", "west_vancouver_downgraded_region": "Review"},
        "scoring_weights": {"assessment_discount": 25, "location_liquidity": 20, "livability": 20, "land_regularness": 15, "age_fit": 10, "optionality": 5, "seller_motivation": 5},
        "risk_keywords": [], "opportunity_keywords": [], "alert_thresholds": {"normal_score": 70}, "run": {"send_notifications": False},
    }
    path = tmp_path / f"{source_dir.name}.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path

def test_purchase_weekly_stale_run_preserves_previous_report_and_db(tmp_path):
    report = tmp_path / "purchase.md"
    report.write_text("previous normal report", encoding="utf-8")
    db = tmp_path / "purchase.sqlite"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    snapshot = source_dir / "old.html"
    snapshot.write_text(PURCHASE_HTML, encoding="utf-8")
    set_mtime_days_ago(snapshot, 8)
    config_path = write_purchase_config(tmp_path, source_dir, db, report)
    status_path = tmp_path / "purchase-status.json"

    result = CliRunner().invoke(app, ["run-weekly", "--config", str(config_path), "--status-path", str(status_path)])

    assert result.exit_code == 0, result.output
    assert status_path.read_text(encoding="utf-8") and json.loads(status_path.read_text())['status'] == "stale"
    assert report.read_text(encoding="utf-8") == "previous normal report"
    assert not db.exists()
    assert list((tmp_path / "alerts").glob("*.md"))


def test_fresh_purchase_runs_compare_stable_listing_identity_and_price(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir(); second_dir.mkdir()
    write_snapshot(first_dir / "week-one.html", price=1500000, address="1 Main Street", mls="R3000001")
    write_snapshot(second_dir / "week-two.html", price=1400000, address="1 Main Street", mls="R3000001")
    first_config = write_purchase_config(tmp_path, first_dir, tmp_path / "history.sqlite", tmp_path / "purchase.md")
    second_config = write_purchase_config(tmp_path, second_dir, tmp_path / "history.sqlite", tmp_path / "purchase.md")
    # Both runs use the same SQLite path and report path; mtimes are within seven days of the real clock.
    # Build `first_config` and `second_config` with those directories and invoke the internal `_run_weekly` helper.

    _run_weekly(str(first_config), status_path=str(tmp_path / "first-status.json"))
    _run_weekly(str(second_config), status_path=str(tmp_path / "second-status.json"))

    second_status = json.loads((tmp_path / "second-status.json").read_text(encoding="utf-8"))
    assert second_status['delta']['price_reduced_count'] == 1
    assert second_status['delta']['new_count'] == 0
```

Add report assertions for `Data status`, source observation metadata, `Weekly Changes`, and the true `Weekly New Listings` section. Add rental equivalents for stale status and rent reduction. The internal helpers `_run_weekly(config, status_path=None, now=None)` and `_run_rentals_weekly(config, status_path=None, now=None)` accept an injected clock for deterministic tests; the Typer commands call them without exposing a clock option.

- [ ] **Step 2: Run the new tests to verify they fail before CLI/report implementation.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_weekly_watchdog.py tests/test_report.py tests/test_rental_report.py
```

Expected: the CLI does not accept `--status-path`, reports have no data-status/delta sections, and stale runs currently write output or create no status.

- [ ] **Step 3: Add monitoring configuration and implement status-aware weekly commands.**

Add this to `config/real.yaml`:

```yaml
monitoring:
  max_age_days: 7
  required_collectors:
    - search_page_snapshot
  require_each_input_dir: true
  alert_dir: outputs/alerts
```

Add the same block to `config/rentals.yaml` with `required_collectors: [snapshot]`. Add `status_path: str | None = None` to both weekly Typer commands. The commands call internal helpers `_run_weekly(config, status_path=None, now=None)` and `_run_rentals_weekly(config, status_path=None, now=None)`. Before creating `Storage`/`RentalStorage`, call `inspect_input_freshness` with the helper's optional `now`. On stale, write status JSON plus a Markdown artifact under `alert_dir`, print a concise stale line, and return without collecting, upserting, scoring, or writing the configured normal report. On exceptions, write `status='error'` and re-raise. On fresh runs, inject selected paths into collector configs, score the current listings, compare and record `RadarHistory`, render the normal report with freshness/delta context, then write `status='fresh'` containing `delta`, `source_files`, `input_fingerprint`, and report path.

The default absence of a `monitoring` block keeps the existing generic `run-weekly` behavior compatible for `config/default.yaml` and legacy unit tests; real and rental configs opt into the gate explicitly.

- [ ] **Step 4: Implement report rendering.**

Extend both report builders with optional `data_status=None, delta=None` parameters. When supplied, render source status, newest observed file/age, selected source files, baseline state, and counts for new/reduced/increased/not-seen. Add detail sections that show addresses and price transitions. Rename purchase’s current all-listing section to `Current Snapshot Listings`; reserve `Weekly New Listings` for `delta.new`. Keep the existing candidate/risk sections and default output assertions intact.

- [ ] **Step 5: Run focused CLI/report tests and the full Python suite.**

Run:

```bash
rtk .venv/bin/pytest -q tests/test_weekly_watchdog.py tests/test_report.py tests/test_rental_report.py
rtk .venv/bin/pytest -q
```

Expected: both focused and full suites pass with no network access. The stale integration test must show an unchanged report and no created SQLite database.

---

### Task 4: Cron wrapper status branching

**Files:**
- Modify: `scripts/run_weekly_watchdog.sh`

- [ ] **Step 1: Add a deterministic shell verification command before editing.**

Run:

```bash
rtk bash -n scripts/run_weekly_watchdog.sh
```

Record the current syntax result, then use the Python CLI status tests from Task 3 as the behavioral red test for the command side.

- [ ] **Step 2: Update the wrapper with unique status paths and independent command execution.**

Create `outputs/status/purchase_<STAMP>.json` and `outputs/status/rental_<STAMP>.json`. Run each weekly command under `set +e`, record its exit code in the log, and continue to the second command. After both finish, classify each status file with `jq` as `fresh`, `stale`, or `error` (missing/malformed is `error`). With either stale and no errors, send one stale-data alert containing each radar’s newest source, age, and stale directories, and omit candidate sections. With both fresh, send the existing summary plus delta counts. With any error, send one execution-error alert and omit normal candidate results. Keep the existing Feishu target/account and `SEND_FEISHU` switch. The wrapper must never treat a missing status file as fresh.

- [ ] **Step 3: Verify wrapper syntax and a no-send stale dry run.**

Run:

```bash
rtk bash -n scripts/run_weekly_watchdog.sh
rtk env SEND_FEISHU=0 /Users/xiwei/northshore_property_watchdog/scripts/run_weekly_watchdog.sh
```

Expected: both commands return stale with the current June inputs, the log contains `purchase_status=stale` and `rental_status=stale`, no normal-report content is sent, and the existing normal report mtimes/content remain unchanged.

---

### Task 5: Final verification and handoff

**Files:**
- No additional source changes unless a verification failure identifies a specific defect.

- [ ] **Step 1: Verify configuration and source freshness behavior against current production inputs.**

Run:

```bash
rtk .venv/bin/northshore-watchdog run-weekly --config config/real.yaml --status-path outputs/status/manual-purchase-check.json
rtk .venv/bin/northshore-watchdog rentals-run-weekly --config config/rentals.yaml --status-path outputs/status/manual-rental-check.json
```

Expected: both commands exit 0 with stale status, create stale artifacts, do not alter `outputs/real_weekly_report.md` or `outputs/rental_weekly_report.zh.md`, and do not refresh legacy listing timestamps.

- [ ] **Step 2: Inspect status JSON and generated stale artifacts.**

Run:

```bash
rtk jq '{status, data_status, newest, stale_directories, alert_path, delta}' outputs/status/manual-purchase-check.json
rtk jq '{status, data_status, newest, stale_directories, alert_path, delta}' outputs/status/manual-rental-check.json
rtk find outputs/alerts -type f -maxdepth 1
```

Expected: status is `stale`, each alert path exists, and no `delta` is recorded for a stale run.

- [ ] **Step 3: Run the complete test and syntax checks.**

Run:

```bash
rtk .venv/bin/pytest -q
rtk bash -n scripts/run_weekly_watchdog.sh
```

Expected: pytest exits 0 and Bash syntax validation exits 0.

- [ ] **Step 4: Review the final file diff and report actual status.**

Run:

```bash
rtk git status --short 2>&1 || true
rtk git diff --stat 2>&1 || true
```

Because the project is not a Git worktree, report changed paths and verification output directly instead of claiming a commit.
