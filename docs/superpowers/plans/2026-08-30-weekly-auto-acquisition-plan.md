# Weekly Auto-Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Saturday radar search Realtor.ca and Zolo.ca automatically, persist fresh source snapshots, then run the existing scoring/history/Feishu pipeline only after deterministic acquisition validation succeeds.

**Architecture:** The shell wrapper invokes an isolated OpenClaw Jarvis agent turn before scoring. The agent performs public `web_search`/`web_fetch` research and writes four timestamped HTML snapshots plus `outputs/acquisition/latest.json`. A new Python validator checks the manifest, paths, freshness, metadata, and source-domain URLs; the wrapper aborts normal delivery on acquisition failure while preserving the existing stale-data gate.

**Tech Stack:** Bash cron wrapper, OpenClaw CLI, Python 3.11+, Pydantic/config YAML, BeautifulSoup, pytest, jq.

---

### Task 1: Add deterministic acquisition validation contract

**Files:**
- Create: `src/northshore_watchdog/acquisition.py`
- Modify: `src/northshore_watchdog/cli.py`
- Test: `tests/test_acquisition.py`

- [ ] **Step 1: Write failing tests**

Add tests for a successful four-entry manifest, a valid empty search result, an old manifest, a partial source status, a snapshot outside the configured directory, and a card URL from a disallowed domain. Use temporary files with the exact metadata markers and parser-compatible cards described in the design.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_acquisition.py -q`

Expected: collection/import failure because the acquisition validator does not exist yet.

- [ ] **Step 3: Implement the validator and CLI command**

Implement `validate_acquisition_manifest(manifest_path, purchase_config, rental_config, now=None)`, returning a serializable result with `status`, `reasons`, `manifest_path`, and validated entries. Require a recent successful manifest, four successful source entries (`purchase.realtor.ca`, `purchase.zolo.ca`, `rental.realtor.ca`, `rental.zolo.ca`), existing HTML files under the configured snapshot directories, matching `source-domain` and `acquisition-status=success` metadata, and allowed card URLs. Permit `result_count=0` when the source marker and manifest status are valid. Add `validate-acquisition` to Typer; it writes an optional status JSON and exits non-zero for invalid acquisition.

- [ ] **Step 4: Run focused tests**

Run: `./.venv/bin/pytest tests/test_acquisition.py -q`

Expected: all acquisition validator tests pass.

### Task 2: Add the OpenClaw search prompt and wrapper integration

**Files:**
- Create: `config/weekly_auto_acquisition_prompt.md`
- Modify: `scripts/run_weekly_watchdog.sh`
- Test: `tests/test_weekly_watchdog.py`

- [ ] **Step 1: Write failing wrapper tests**

Add a shell-level test fixture that replaces the watchdog binary and OpenClaw binary with test doubles. Assert the acquisition command is called before `run-weekly` and `rentals-run-weekly`, and assert a failed acquisition prevents the normal fresh delivery path. Keep Feishu disabled in the test.

- [ ] **Step 2: Run the wrapper tests to verify failure**

Run: `./.venv/bin/pytest tests/test_weekly_watchdog.py -q`

Expected: the new ordering/failure assertions fail because the wrapper currently starts scoring immediately.

- [ ] **Step 3: Add the acquisition prompt**

Write a precise agent prompt that requires the current Asia/Shanghai date, public searches on both allowed domains for purchase and rental targets, source-faithful fields only, four timestamped files, `outputs/acquisition/latest.json`, and no Feishu/report execution. Require explicit per-source statuses and allow a source to report `success` with zero candidates.

- [ ] **Step 4: Integrate the acquisition turn and validator**

Before the two existing watchdog commands, invoke `openclaw agent --agent agent_jarvis --session-key property-watchdog-acquisition-$STAMP --message-file config/weekly_auto_acquisition_prompt.md --timeout ... --json --no-deliver`, log its result, then invoke `northshore-watchdog validate-acquisition`. Add an acquisition status file and include its details in the wrapper's error/stale message. Set delivery mode to `error` when acquisition or validation fails; only a validated acquisition plus fresh purchase/rental statuses may send a normal report.

- [ ] **Step 5: Run focused tests and shell syntax checks**

Run: `./.venv/bin/pytest tests/test_weekly_watchdog.py -q` and `bash -n scripts/run_weekly_watchdog.sh`.

Expected: wrapper tests pass and shell syntax is valid.

### Task 3: Verify end-to-end acquisition and fresh report delivery

**Files:**
- Modify only generated runtime artifacts under `outputs/` as the commands produce them.

- [ ] **Step 1: Run the full test suite**

Run: `./.venv/bin/pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run a live acquisition without Feishu delivery**

Run the wrapper with `SEND_FEISHU=0`, inspect the acquisition log, manifest, and four new snapshots, and verify the validator reports `fresh`.

- [ ] **Step 3: Run the fresh pipeline with Feishu delivery**

Run the wrapper with `SEND_FEISHU=1`, verify that the purchase and rental reports contain the new snapshot observation time and weekly delta sections, and confirm the Feishu send succeeds.

- [ ] **Step 4: Run final verification**

Run `python3 -m compileall -q src`, `bash -n scripts/run_weekly_watchdog.sh`, and inspect the final status JSON/log for acquisition, purchase, and rental success.

