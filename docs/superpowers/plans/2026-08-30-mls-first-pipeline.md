# Implementation Plan: MLS-first listing acquisition pipeline

> **For the implementing agent:** follow the repository instructions and use
> test-driven development. Complete each task in order and verify before
> claiming acceptance.

**Goal:** Change the bounded research acquisition pipeline so an explicit MLS
primary source is attempted first, public REALTOR.ca and Zolo snapshots run as
ordered cross-checks/fallbacks, and the source decision is auditable.

**Architecture:** Keep the existing typed `PropertyRecord`, provenance,
conflict, SQLite, scoring, watchlist, and Markdown contracts. Add source roles
and ordered orchestration in the existing research modules. Add a small
MLS-first config profile and timestamped real web snapshots; do not introduce a
new crawler or a parallel storage system.

## Task 1: Lock the source-priority contract with failing tests

- [ ] Add tests for source roles, configured priority ordering, explicit MLS
  unavailability, public fallback, and material listing-field conflicts.
- [ ] Run the focused tests and confirm they fail for the missing behavior.

## Task 2: Implement source-role metadata and orchestration

- [ ] Add source-role/priority fields to source registry and adapter run
  metadata.
- [ ] Sort acquisition adapters by configured priority without allowing a
  public source to masquerade as direct MLS.
- [ ] Return and report `primary_source_status`, `fallback_used`, and the
  effective priority order.
- [ ] Keep property identity PID/address based while treating MLS number as
  the listing cross-check key.

## Task 3: Add the approved read-only web profile

- [ ] Add a config inheritance mechanism for a small profile override.
- [ ] Add `config/research-mls-first.yaml` and its source registry.
- [ ] Add bounded REALTOR.ca and Zolo JSON snapshots for the real 2026-08-30
  validation set, preserving unknown official fields.
- [ ] Add report output for source policy and primary-source availability.

## Task 4: Validate end to end

- [ ] Add tests for the profile, real snapshot row counts, MLS-number merge,
  conflict queue, and report language.
- [ ] Run the MLS-first dry run and persisted read-only run; inspect report and
  SQLite source/provenance/conflict records.
- [ ] Run the complete pytest suite, compile checks, and CLI smoke checks.
- [ ] Stop after this phase; do not implement Phase 2 acquisition automation.

## Verification commands

```bash
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m compileall -q src
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-mls-first.yaml --dry-run
```

No Git commit is planned because this checkout does not contain Git metadata.
