# Property Radar Freshness and Weekly Diff

**Date:** 2026-08-30

## Problem

The Saturday job runs successfully, but the purchase and rental radars currently reparse the same local snapshots from June. The existing report generation has no freshness gate, no observation metadata, and no persisted run-to-run comparison. It also treats the snapshot filename as part of the listing identity, so a newly named snapshot would make the same listing look new.

The current cron remains the system crontab entry at 21:00 on Saturdays and continues to run the existing purchase and rental commands. This change improves the behavior around local inputs; it does not add a remote crawler or change the schedule.

## Goals

1. Prevent an old snapshot from producing a normal-looking weekly report.
2. Preserve the last normal report when inputs are stale.
3. Send a clear stale-data alert instead of normal candidate results when either radar is stale.
4. Record enough snapshot metadata to explain which files and observation times produced a report.
5. Compare successive fresh runs for new listings, price reductions, price increases, and listings not seen in the current run.
6. Keep listing identity stable when the snapshot filename changes.
7. Keep the existing CLI, SQLite databases, report paths, and Saturday schedule compatible.

## Non-goals

- No automatic scraping of Realtor.ca, Zolo, or other public pages.
- No new API, IMAP, Gmail, MLS, or authorized-feed integration.
- No change to the existing scoring rubric.
- No deletion of old SQLite rows or old reports.
- No assertion that an absent listing is definitely delisted; the report will say “not seen in this run”.

## Design

### 1. Snapshot inspection and freshness

Add a small shared monitoring module under `src/northshore_watchdog/monitoring.py`.

It will expose pure, testable operations for:

- discovering supported files in configured input directories;
- obtaining each file's local modification time and SHA-256 content hash;
- calculating age relative to an injectable `now` timestamp;
- selecting files whose age is at most the configured limit;
- returning a structured freshness result containing status, newest file, age, required directories, stale directories, and file metadata.

Both radar configurations will add:

```yaml
monitoring:
  max_age_days: 7
  required_collectors:
    - search_page_snapshot  # purchase
  require_each_input_dir: true
```

The rental configuration will use `snapshot` as its required collector. Optional collectors such as saved-search email remain usable when they have fresh files, but an empty optional collector does not block the radar. The assessment CSV remains an enrichment-quality warning rather than a listing-input freshness requirement.

For required collectors, every configured input directory must contain at least one supported file within the age limit. Old files are excluded from a fresh collection. The current June files therefore produce `stale` for both radars until newer snapshots are supplied.

The freshness check runs before any listing is upserted or normal report is overwritten. A stale run:

- writes a per-run status JSON file;
- writes a timestamped stale-alert Markdown artifact under `outputs/alerts/`;
- leaves the prior normal report and SQLite listing timestamps unchanged;
- exits successfully from the radar command so the wrapper can inspect the status and send the alert.

An unexpected internal error remains a command failure and is not converted into a stale status.

### 2. Snapshot metadata on parsed listings

The purchase and rental file collectors will use the shared snapshot metadata. Each parsed listing will retain:

- `last_seen_date` set to the snapshot file modification time;
- `raw_data.snapshot_path`;
- `raw_data.snapshot_observed_at`;
- `raw_data.snapshot_sha256`.

This preserves compatibility with the existing Pydantic models while making the provenance visible in stored payloads and reports. The collection timestamp remains separate from the source snapshot observation time.

Only files selected by the freshness inspector are parsed when a `max_age_days` value is present. Existing collector tests without that option continue to parse all files in their temporary directory.

### 3. Stable listing identity

Add stable identity helpers and use them for both current storage and run history:

- Purchase: `mls_number`, then canonical URL, then normalized address plus city.
- Rental: canonical URL, then normalized address plus city.

The identity must not include the snapshot filename or current price. Existing rows using the old filename-based key are retained for compatibility; new writes stop creating additional filename-based duplicates. No destructive migration is performed.

### 4. Append-only run history and weekly diff

Add `src/northshore_watchdog/history.py` with a small SQLite-backed run ledger. The existing purchase and rental databases each receive these tables:

```sql
CREATE TABLE IF NOT EXISTS radar_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  radar TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  status TEXT NOT NULL,
  data_status TEXT NOT NULL,
  source_files TEXT NOT NULL,
  input_fingerprint TEXT NOT NULL,
  delta TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radar_run_listings (
  run_id INTEGER NOT NULL,
  identity_key TEXT NOT NULL,
  address TEXT NOT NULL,
  city TEXT NOT NULL,
  price INTEGER,
  grade TEXT NOT NULL,
  score_total INTEGER NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY (run_id, identity_key),
  FOREIGN KEY (run_id) REFERENCES radar_runs(id)
);
```

The history API will:

- find the latest successful fresh run;
- append the current run and its scored listing payloads;
- compare current identities and prices with the previous run;
- return a `RunDelta` containing identity-keyed new, price-reduced, price-increased, and not-seen lists plus a `baseline` flag.

The first successful fresh run establishes a baseline and reports zero changes. Later fresh runs compare against the latest successful fresh run, even if one or more stale Saturdays occurred in between.

### 5. CLI and wrapper behavior

`run-weekly` and `rentals-run-weekly` will accept an optional per-run status output path. The Saturday wrapper passes separate paths containing the run timestamp. Each command follows this order:

1. Load configuration and inspect required snapshot inputs.
2. If stale, write stale status/artifact and return without collection, scoring, upserts, or normal report writes.
3. If fresh, collect only fresh inputs, enrich, score, compute the delta against history, write the normal report, persist the run, and write a fresh status JSON.

The wrapper will run both commands, then inspect both status files:

- If either status is stale, send one stale-data alert with each radar's status, newest source file, age, and stale directories. Do not send the normal candidate summary.
- If both are fresh, send the existing candidate summary with data age and delta counts added.
- If a status file is missing or says `error`, do not present the run as fresh; retain the normal reports and surface an execution-error alert.

The cron schedule and Feishu target remain unchanged.

### 6. Report changes

Purchase and rental reports will add a data-status block containing:

- fresh/stale status;
- newest snapshot and age;
- source file list or a concise source summary;
- baseline/new/reduced/increased/not-seen counts.

Fresh reports will add dedicated weekly-change sections. Candidate sections retain their existing scoring order and wording. Stale runs do not overwrite these reports.

The current purchase report's hardcoded `new_count = 0` will be replaced by the run delta. The existing “New Listings” section will be renamed or narrowed so it is not presented as a true weekly-new section; the true weekly-new section will use the persisted delta.

## Error handling

- Missing required directory or no supported file: stale status with a precise directory reason.
- Required files older than seven days: stale status with newest file and age.
- Optional input missing: warning metadata, but no stale gate by itself.
- Missing assessment CSV: preserve current neutral/unknown scoring behavior and expose the missing enrichment in the report.
- SQLite/history failure or malformed input: command error; wrapper sends an execution-error alert rather than a normal report.
- Repeated stale runs: each run gets its own status/log evidence; no database timestamp is refreshed by stale input.

## Testing strategy

Add focused tests before implementation for:

1. Freshness selection with fresh, stale, missing, and multiple required directories.
2. Snapshot metadata and age propagation in purchase and rental collectors.
3. Stable identities across two filenames and changed prices.
4. Run-history baseline behavior and subsequent new/reduced/increased/not-seen diffs.
5. Stale CLI behavior: status/artifact is written and normal report/database state is unchanged.
6. Fresh CLI behavior: report includes source metadata and delta counts.
7. Wrapper status branching: stale status sends only the stale alert path; two fresh statuses send the normal summary path.

Existing scoring, collector, report, and rental tests must continue to pass.

## Acceptance criteria

- With the current June inputs, the next manual or scheduled run sends a stale-data alert and does not overwrite either normal report.
- With fresh snapshots whose filenames differ from the previous run, unchanged listings are not counted as new solely because their filenames changed.
- A lower current price is reported as a price reduction; a listing absent from the current snapshot is reported as not seen, not definitively delisted.
- A first fresh run creates a baseline without false “all listings are new” output.
- Normal reports identify their source files and observation ages.
- No current cron schedule, score threshold, or external data access behavior is changed.
