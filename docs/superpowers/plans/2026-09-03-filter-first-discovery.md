# Filter-first Automated Listing Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 11A Stage 0–4 so configured source-native filters reduce each market preset before the repository parses cards, validates the filter result, deduplicates lightweight observations, and emits a bounded 5–10 listing shortlist.

**Architecture:** Keep the discovery engine pure and transport-independent. A `DiscoveryPreset` is compiled into a source-specific native/post-filter plan; a source adapter returns a bounded filtered capture; the engine validates native-filter behavior, applies documented post-filters, deduplicates by MLS/address, and ranks a shortlist. Existing Property Intelligence, enrichment, comparables, redevelopment, and final scoring remain outside this command. Existing source-run persistence is reused by storing the discovery payload in the existing append-only source-run envelope; no new database table is needed in this phase.

**Tech Stack:** Python 3.11, Pydantic v2, BeautifulSoup, optional Playwright, Typer, existing SQLite/source provenance/report utilities, pytest.

---

## File map

Create:

- `src/northshore_watchdog/research/discovery.py` — pure models, filter-plan compilation, validation, post-filtering, deduplication, and shortlist ranking.
- `src/northshore_watchdog/research/discovery_sources.py` — capture protocol, JSON/HTML replay adapters, source-status mapping, and bounded page extraction.
- `src/northshore_watchdog/research/discovery_browser.py` — optional standard Playwright native-filter executor with injected test seam; no stealth, proxy rotation, CAPTCHA handling, or detail-page navigation.
- `src/northshore_watchdog/research/discovery_pipeline.py` — configuration selection and orchestration of presets, sources, validation, dedupe, and shortlist results.
- `src/northshore_watchdog/research/discovery_report.py` — discovery-only Markdown rendering.
- `config/research-discovery.yaml` — four target regions with North Vancouver split into City and District presets, caps, and source capability mappings.
- `tests/test_research_discovery.py` — pure engine tests.
- `tests/test_research_discovery_sources.py` — capture/parser/browser-contract tests.
- `tests/test_research_discovery_cli.py` — CLI/config/persistence tests.
- `data/samples/discovery_filtered_capture.json` — deterministic filtered-result replay fixture.

Modify:

- `src/northshore_watchdog/cli.py` — add `research-discover` without changing existing command registration or `research-acquire` behavior.
- `src/northshore_watchdog/research/models.py` — only if a shared source-role enum value is required; otherwise keep the intelligence schema unchanged.
- `src/northshore_watchdog/research/storage.py` — add a narrow helper only if the existing `save_source_run` envelope cannot retain discovery metrics; do not add a parallel property table.
- `README.md` — document the new discovery command and explicitly distinguish it from the legacy broad crawler.

Test commands used throughout:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
.venv/bin/pytest tests/test_research_discovery_sources.py -q
.venv/bin/pytest tests/test_research_discovery_cli.py -q
.venv/bin/pytest -q
```

The repository currently has no `.git` metadata, so implementation checkpoints are verified with tests and file inspection rather than fabricated commits.

### Task 1: Add the pure discovery domain models and filter engine

**Files:**

- Create: `src/northshore_watchdog/research/discovery.py`
- Test: `tests/test_research_discovery.py`

- [ ] **Step 1: Write the failing model and plan-compilation tests.**

Add these tests to `tests/test_research_discovery.py`:

```python
from datetime import datetime, timezone

from northshore_watchdog.research.discovery import (
    DiscoveryPreset,
    SiteCapabilities,
    compile_filter_plan,
)


def test_compile_filter_plan_preserves_desired_filters_and_moves_unsupported_fields():
    preset = DiscoveryPreset(
        name="vancouver-west-house",
        market_bucket="vancouver_west",
        desired_filters={
            "property_type": "DETACHED_HOUSE",
            "minimum_bedrooms": 3,
            "minimum_bathrooms": 2.5,
            "minimum_finished_sqft": 1500,
            "ownership": "FREEHOLD",
            "price_max": 2_200_000,
        },
    )
    capabilities = SiteCapabilities(
        native_filter_fields={"property_type", "minimum_bedrooms", "minimum_bathrooms", "price_max"},
        card_fields={"property_type", "bedrooms", "bathrooms", "finished_sqft", "ownership_type"},
    )

    plan = compile_filter_plan(preset, capabilities)

    assert plan.native_filters == {
        "property_type": "DETACHED_HOUSE",
        "minimum_bedrooms": 3,
        "minimum_bathrooms": 2.5,
        "price_max": 2_200_000,
    }
    assert plan.post_filters == {
        "minimum_finished_sqft": 1500,
        "ownership": "FREEHOLD",
    }
    assert plan.unsupported_filters == {}


def test_compile_filter_plan_marks_uncheckable_constraint_unsupported():
    preset = DiscoveryPreset(
        name="east-house",
        market_bucket="vancouver_east",
        desired_filters={"ownership": "FREEHOLD"},
    )
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields=set(), card_fields=set()),
    )

    assert plan.unsupported_filters == {"ownership": "FREEHOLD"}
    assert plan.post_filters == {}
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-model failure.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
```

Expected: collection fails because `northshore_watchdog.research.discovery` does not exist yet. If collection fails for any unrelated import error, fix the test import before implementing the module.

- [ ] **Step 3: Implement the minimal preset, capability, and plan models.**

Define the following Pydantic models and function in `discovery.py`:

```python
class DiscoveryPreset(BaseModel):
    name: str
    market_bucket: str
    desired_filters: dict[str, Any] = Field(default_factory=dict)
    max_result_cards: int = Field(default=100, ge=1)
    sample_size: int = Field(default=5, ge=1)
    shortlist_limit: int = Field(default=10, ge=1)


class SiteCapabilities(BaseModel):
    native_filter_fields: set[str] = Field(default_factory=set)
    card_fields: set[str] = Field(default_factory=set)
    filter_value_mapping: dict[str, Any] = Field(default_factory=dict)


class DiscoveryFilterPlan(BaseModel):
    preset_name: str
    source_name: str | None = None
    desired_filters: dict[str, Any] = Field(default_factory=dict)
    native_filters: dict[str, Any] = Field(default_factory=dict)
    post_filters: dict[str, Any] = Field(default_factory=dict)
    unsupported_filters: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)


def compile_filter_plan(
    preset: DiscoveryPreset,
    capabilities: SiteCapabilities,
    *,
    source_name: str | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> DiscoveryFilterPlan:
    native: dict[str, Any] = {}
    post: dict[str, Any] = {}
    unsupported: dict[str, Any] = {}
    for field_name, value in preset.desired_filters.items():
        if field_name in capabilities.native_filter_fields:
            mapping = capabilities.filter_value_mapping.get(field_name, {})
            native[field_name] = mapping.get(str(value), value) if isinstance(mapping, dict) else value
        elif _card_field_for_filter(field_name) in capabilities.card_fields:
            post[field_name] = value
        else:
            unsupported[field_name] = value
    return DiscoveryFilterPlan(
        preset_name=preset.name,
        source_name=source_name,
        desired_filters=dict(preset.desired_filters),
        native_filters=native,
        post_filters=post,
        unsupported_filters=unsupported,
        actions=list(actions or []),
    )
```

The implementation must place a desired field in `native_filters` when its name is in `native_filter_fields`; otherwise place it in `post_filters` only when its semantic value can be checked using `card_fields`; otherwise place it in `unsupported_filters`. Apply `filter_value_mapping` to the native value without mutating `desired_filters`.

Use this fixed semantic mapping in `_card_field_for_filter` so source configuration stays readable:

```python
_FILTER_CARD_FIELDS = {
    "price_min": "asking",
    "price_max": "asking",
    "minimum_bedrooms": "bedrooms",
    "minimum_bathrooms": "bathrooms",
    "minimum_finished_sqft": "finished_sqft",
    "ownership": "ownership_type",
    "property_type": "property_type",
    "status": "listing_status",
}


def _card_field_for_filter(field_name: str) -> str:
    return _FILTER_CARD_FIELDS.get(field_name, field_name)
```

- [ ] **Step 4: Run the model tests and verify they pass.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Add failing tests for native-filter validation, post-filtering, and caps.**

Append tests using the following contract:

```python
from northshore_watchdog.research.discovery import (
    DiscoveryCard,
    DiscoveryCapture,
    DiscoveryStatus,
    validate_capture,
)


def _capture(**updates):
    payload = {
        "source_name": "test-source",
        "source_url": "https://example.test/search",
        "result_count": 2,
        "applied_filters": {"price_max": 2_200_000, "minimum_bedrooms": 3},
        "cards": [
            {
                "address": "1 Test Street",
                "mls_number": "R4000001",
                "asking": 2_000_000,
                "bedrooms": 3,
                "bathrooms": 2.5,
                "finished_sqft": 1600,
                "source_url": "https://example.test/1",
            }
        ],
    }
    payload.update(updates)
    return DiscoveryCapture.model_validate(payload)


def test_native_filter_violation_fails_closed():
    plan = DiscoveryFilterPlan(
        preset_name="test",
        native_filters={"price_max": 2_200_000, "minimum_bedrooms": 3},
    )
    capture = _capture(
        cards=[{
            "address": "2 Test Street",
            "mls_number": "R4000002",
            "asking": 3_500_000,
            "bedrooms": 2,
            "source_url": "https://example.test/2",
        }]
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


def test_post_filter_violation_is_excluded_without_invalidating_source():
    plan = DiscoveryFilterPlan(
        preset_name="test",
        native_filters={"minimum_bedrooms": 3},
        post_filters={"minimum_bathrooms": 2.5},
    )
    capture = _capture(
        applied_filters={"minimum_bedrooms": 3},
        cards=[
            {"address": "3 Test Street", "mls_number": "R4000003", "bedrooms": 3,
             "bathrooms": 2, "source_url": "https://example.test/3"},
            {"address": "4 Test Street", "mls_number": "R4000004", "bedrooms": 3,
             "bathrooms": 2.5, "source_url": "https://example.test/4"},
        ],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert [card.mls_number for card in result.candidate_cards] == ["R4000004"]
    assert "POST_FILTER_EXCLUDED" in result.reason_codes


def test_result_cap_stops_before_card_ingestion():
    plan = DiscoveryFilterPlan(preset_name="test", native_filters={})
    result = validate_capture(
        _capture(result_count=101), plan, sample_size=5, max_result_cards=100
    )

    assert result.status is DiscoveryStatus.RESULT_SET_TOO_LARGE
    assert result.candidate_cards == []
```

- [ ] **Step 6: Run the tests and verify they fail for the unimplemented validation behavior.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
```

Expected: failures identify the missing `DiscoveryCapture`, `DiscoveryStatus`, and `validate_capture` behavior, not malformed fixtures.

- [ ] **Step 7: Implement capture models and fail-closed validation.**

Add these contracts to `discovery.py`:

```python
class DiscoveryStatus(str, Enum):
    PASS = "PASS"
    FILTER_NOT_APPLIED = "FILTER_NOT_APPLIED"
    FILTER_VALIDATION_INCONCLUSIVE = "FILTER_VALIDATION_INCONCLUSIVE"
    RESULT_SET_TOO_LARGE = "RESULT_SET_TOO_LARGE"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    UNSUPPORTED_FILTER = "UNSUPPORTED_FILTER"
    PARSER_ERROR = "PARSER_ERROR"


class DiscoveryCard(BaseModel):
    source_name: str | None = None
    mls_number: str | None = None
    address: str
    municipality: str | None = None
    asking: int | float | None = None
    bedrooms: int | float | None = None
    bathrooms: int | float | None = None
    finished_sqft: int | float | None = None
    lot_area_sqft: int | float | None = None
    lot_dimensions: str | None = None
    frontage_ft: int | float | None = None
    depth_ft: int | float | None = None
    property_type: str | None = None
    ownership_type: str | None = None
    listing_status: str | None = None
    dom: int | None = None
    source_url: str | None = None
    feed_url: str | None = None
    observed_at: datetime | None = None
    reason_codes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class DiscoveryCapture(BaseModel):
    source_name: str
    source_url: str
    preset_name: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status_code: int = 200
    result_count: int | None = None
    count_is_complete: bool = False
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    applied_filter_evidence: list[str] = Field(default_factory=list)
    cards: list[DiscoveryCard | dict[str, Any]] = Field(default_factory=list)
    detail_requests: int = 0
    blocked: bool = False
    raw_snapshot_path: str | None = None


class DiscoveryValidation(BaseModel):
    status: DiscoveryStatus
    reason_codes: list[str] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    sampled_card_count: int = 0
    candidate_cards: list[DiscoveryCard] = Field(default_factory=list)
```

`validate_capture` must first reject `blocked`, non-2xx status, nonzero `detail_requests`, a nonmatching non-null `preset_name`, unsupported filters, and result counts over the cap. Before validation it must fill each card's missing `source_name`, `source_url`, and `observed_at` from the capture envelope. It must validate the first `sample_size` cards only against `native_filters`. A known native violation returns `FILTER_NOT_APPLIED` and an empty candidate list. A missing value is inconclusive only when there is no `applied_filter_evidence` for that native field. After native validation passes, apply `post_filters` locally; known post-filter violations receive `POST_FILTER_EXCLUDED`, while unknown values remain in the candidate list with `POST_FILTER_UNKNOWN`.

- [ ] **Step 8: Run the focused tests and verify validation passes.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
```

Expected: all current pure-engine tests pass.

- [ ] **Step 9: Add failing tests for MLS/address dedupe and bounded ranking.**

Add tests that assert exact MLS wins, address fallback is used only without MLS, different MLS at the same address is retained and flagged `POSSIBLE_DUPLICATE`, and `rank_shortlist(cards, limit=10)` never returns more than ten cards. Include a card with `lot_area_sqft=2950` and assert it is tagged `SMALL_LOT` rather than removed.

- [ ] **Step 10: Implement `deduplicate_cards`, `rank_shortlist`, and the public exports.**

Use a normalized MLS key first and the existing `normalize_address` helper as the fallback. Return a result object containing `cards`, `duplicate_flags`, and `reason_codes`; do not merge conflicting field values. Rank with transparent tuple components: core-range membership, known current-use fields, visible lot signal, freshness/DOM signal, and completeness. Keep ranking separate from the final investment score.

The orchestration models and `run_discovery` function belong in the separate `discovery_pipeline.py` module described in Task 4. Keeping them out of this file preserves the pure-engine boundary. Task 4 must implement the function so it compiles each selected preset, invokes exactly one configured filtered source, validates the capture, deduplicates only successful card sets, aggregates the run status, and returns `DRY_RUN` without invoking a transport when requested.

- [ ] **Step 11: Run the pure-engine test file again.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py -q
```

Expected: all tests pass with no network access.

### Task 2: Add bounded filtered-capture and HTML source adapters

**Files:**

- Create: `src/northshore_watchdog/research/discovery_sources.py`
- Modify: `src/northshore_watchdog/research/public_feeds.py` only if a public parser helper must be exported cleanly.
- Create: `data/samples/discovery_filtered_capture.json`
- Test: `tests/test_research_discovery_sources.py`

- [ ] **Step 1: Write failing tests for JSON replay and result-card extraction.**

Test a JSON capture with `result_count`, `applied_filters`, and two cards. Assert that the adapter returns a `DiscoveryCapture`, preserves `raw_snapshot_path`, and never follows a detail URL. Test an HTML capture containing a result-count marker, an applied-filter marker, and REALTOR/Zolo-style cards; assert that only card fields are extracted.

- [ ] **Step 2: Run the source tests and verify they fail because the adapter module is absent.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_sources.py -q
```

Expected: import failure for `discovery_sources`.

- [ ] **Step 3: Implement the source protocol and local replay adapter.**

Define:

```python
class DiscoverySource(Protocol):
    source_name: str

    def execute(
        self,
        plan: DiscoveryFilterPlan,
        *,
        observed_at: datetime,
    ) -> DiscoveryCapture:
        raise NotImplementedError


class JsonFilteredCaptureSource:
    def __init__(self, *, name: str, path: str | Path, config: Mapping[str, Any]):
        self.source_name = name
        self.path = Path(path)
        self.config = dict(config)

    def execute(self, plan, *, observed_at):
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("source_name") not in {None, self.source_name}:
            raise ValueError("capture source_name does not match configured source")
        if payload.get("preset_name") not in {None, plan.preset_name}:
            raise ValueError("capture preset_name does not match requested preset")
        payload["source_name"] = self.source_name
        payload["preset_name"] = plan.preset_name
        payload.setdefault("source_url", str(self.path))
        payload.setdefault("observed_at", observed_at.isoformat())
        payload["raw_snapshot_path"] = str(self.path)
        return DiscoveryCapture.model_validate(payload)


def parse_filtered_html(
    content: bytes,
    *,
    source_name: str,
    source_url: str,
    observed_at: datetime,
    selectors: Mapping[str, str],
) -> DiscoveryCapture:
    soup = BeautifulSoup(content, "html.parser")
    result_node = soup.select_one(selectors["result_count"])
    result_count = _parse_result_count(result_node.get_text(" ") if result_node else None)
    evidence = [
        "APPLIED_FILTER_MARKER_FOUND"
        for node in soup.select(selectors.get("applied_filter", ""))
        if node.get_text(" ").strip()
    ]
    cards = parse_public_html(content, source_url, observed_at)
    return DiscoveryCapture(
        source_name=source_name,
        source_url=source_url,
        observed_at=observed_at,
        result_count=result_count,
        applied_filter_evidence=evidence,
        cards=cards,
    )
```

The JSON adapter must reject a capture whose declared `preset_name` or `source_name` does not match the requested plan/source. If either field is absent, the adapter fills it from the requested plan and configured source. The HTML parser must use existing `parse_public_html` behavior for listing-card normalization where possible, but it must additionally parse a configurable result count and applied-filter evidence. It must not infer values from detail links or request those links.

- [ ] **Step 4: Implement source-status mapping and cap enforcement at the adapter boundary.**

Map HTTP 401/403/429, challenge markers, and CAPTCHA text to `SOURCE_BLOCKED`; preserve the response metadata and raw snapshot path. Do not retry with a different user agent or bypass mechanism. A missing result count is accepted only when the capture explicitly declares `count_is_complete: true`; otherwise the pure validator returns `FILTER_VALIDATION_INCONCLUSIVE`. When `count_is_complete` is true, the adapter may set `result_count` to the complete number of extracted filtered cards.

- [ ] **Step 5: Run source tests and inspect the fixture output.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_sources.py -q
rtk sed -n '1,260p' data/samples/discovery_filtered_capture.json
```

Expected: all source tests pass, and the fixture contains only lightweight card fields.

### Task 3: Add the optional standard Playwright native-filter executor

**Files:**

- Create: `src/northshore_watchdog/research/discovery_browser.py`
- Modify: `src/northshore_watchdog/research/browser_feeds.py` only to expose a reusable standard browser session if duplication is unavoidable.
- Test: `tests/test_research_discovery_sources.py`

- [ ] **Step 1: Write failing tests with a fake page object.**

The fake page must record `goto`, `fill`, `select_option`, `click`, `wait_for_selector`, and `content` calls. Assert that the executor applies the configured native actions, reads count/card selectors, and never clicks a listing detail link. Add a 403/challenge response test that returns `SOURCE_BLOCKED` and makes no pagination/detail call.

- [ ] **Step 2: Run the focused test and verify the executor is missing.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_sources.py -q
```

Expected: failures identify the missing browser executor only.

- [ ] **Step 3: Implement the bounded executor.**

Use the optional Playwright dependency only at execution time. Support only configured actions `goto`, `fill`, `select_option`, `check`, `click_filter_submit`, and `wait_for_selector`. After the filter-submit action, read the result count, applied-state selectors, and result-card HTML. Enforce `max_result_cards` before pagination. The executor must close the page/context in `finally` and must not include stealth flags, proxy rotation, CAPTCHA solving, or detail navigation.

Use this configuration contract:

```yaml
browser:
  entrypoint: https://example.test/search
  actions:
    - action: fill
      selector: "#min-price"
      value_from: price_min
    - action: fill
      selector: "#max-price"
      value_from: price_max
    - action: select_option
      selector: "#property-type"
      value_from: property_type
    - action: click_filter_submit
      selector: "button[type=submit]"
    - action: wait_for_selector
      selector: ".listing-card"
  selectors:
    result_count: ".result-count"
    applied_filter: ".applied-filter"
    card: ".listing-card"
```

- [ ] **Step 4: Run browser-contract tests without requiring Chromium.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_sources.py -q
```

Expected: fake-page tests pass in the base development environment; tests that require real Playwright remain skipped unless the browser extra is installed. A live 403 is reported as blocked, not as a successful discovery run.

### Task 4: Add discovery configuration and the `research-discover` CLI command

**Files:**

- Create: `config/research-discovery.yaml`
- Create: `src/northshore_watchdog/research/discovery_pipeline.py`
- Modify: `src/northshore_watchdog/cli.py`
- Test: `tests/test_research_discovery_cli.py`

- [ ] **Step 1: Write failing config and CLI tests.**

Test that the new profile loads five operational presets whose names are:

```text
vancouver-west
vancouver-east
north-vancouver-city
north-vancouver-district
west-vancouver
```

Test that `research-discover --dry-run --preset vancouver-west` prints the compiled desired/native/post-filter plan and does not call a source or detail transport. Test that an input capture with `result_count=101` returns a nonzero discovery status and zero candidates.

- [ ] **Step 2: Run the tests and verify the new command/profile are absent.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_cli.py -q
```

Expected: failures identify the missing config/command.

- [ ] **Step 3: Add the discovery profile.**

Create `config/research-discovery.yaml` extending `config/research.yaml`. Set:

```yaml
schema_version: research-discovery-v1

discovery:
  enabled: true
  max_result_cards: 100
  sample_size: 5
  shortlist_limit: 10
  presets:
    - name: vancouver-west
      market_bucket: vancouver_west
      desired_filters:
        status: ACTIVE
        property_type: DETACHED_HOUSE
        ownership: FREEHOLD
        minimum_bedrooms: 3
        minimum_bathrooms: 2.5
        minimum_finished_sqft: 1500
        price_min: 1500000
        price_max: 2000000
    - name: vancouver-east
      market_bucket: vancouver_east
      desired_filters:
        status: ACTIVE
        property_type: DETACHED_HOUSE
        ownership: FREEHOLD
        minimum_bedrooms: 3
        minimum_bathrooms: 2.5
        minimum_finished_sqft: 1500
        price_min: 1000000
        price_max: 1600000
    - name: north-vancouver-city
      market_bucket: city_north_vancouver
      desired_filters:
        status: ACTIVE
        property_type: DETACHED_HOUSE
        ownership: FREEHOLD
        minimum_bedrooms: 3
        minimum_bathrooms: 2.5
        minimum_finished_sqft: 1500
        price_min: 1000000
        price_max: 1600000
    - name: north-vancouver-district
      market_bucket: district_north_vancouver
      desired_filters:
        status: ACTIVE
        property_type: DETACHED_HOUSE
        ownership: FREEHOLD
        minimum_bedrooms: 3
        minimum_bathrooms: 2.5
        minimum_finished_sqft: 1500
        price_min: 1000000
        price_max: 1600000
    - name: west-vancouver
      market_bucket: west_vancouver
      desired_filters:
        status: ACTIVE
        property_type: DETACHED_HOUSE
        ownership: FREEHOLD
        minimum_bedrooms: 3
        minimum_bathrooms: 2.5
        minimum_finished_sqft: 1500
        price_min: 1500000
        price_max: 2000000

  sources:
    - name: filtered-capture-replay
      type: JSON_CAPTURE
      enabled: true
      path: data/samples/discovery_filtered_capture.json
      capabilities:
        native_filter_fields:
          - property_type
          - minimum_bedrooms
          - price_min
          - price_max
        card_fields:
          - property_type
          - bedrooms
          - bathrooms
          - finished_sqft
          - ownership_type
          - asking
        filter_value_mapping: {}
    - name: permitted-native-browser
      type: PLAYWRIGHT_NATIVE
      enabled: false
      capabilities:
        native_filter_fields: []
        card_fields: []
        filter_value_mapping: {}
```

Keep lot dimensions out of default hard filters. Add `max_result_cards`, `sample_size`, and `shortlist_limit` at the profile level, with per-preset overrides supported by the model.

The replay source is enabled so the command is deterministic without network access. The browser source is an explicit opt-in and remains disabled until source-specific selectors and permitted access are configured; it is never an automatic fallback.

- [ ] **Step 4: Implement CLI orchestration without invoking Property Intelligence.**

Implement `DiscoveryPresetResult`, `DiscoveryRunResult`, and `run_discovery` in `src/northshore_watchdog/research/discovery_pipeline.py`. The function signature is:

```python
def run_discovery(
    config: Mapping[str, Any],
    *,
    preset_name: str | None = None,
    source_name: str | None = None,
    capture_path: str | Path | None = None,
    dry_run: bool = False,
    observed_at: datetime | None = None,
) -> DiscoveryRunResult:
```

Use `_materialize_preset`, `_configured_presets`, `_configured_discovery_source`, and `_build_discovery_source` in this module. Merge profile defaults into each preset before Pydantic validation. If an explicit `preset_name` matches no configured preset, raise `ValueError`; if no source is enabled, return a `NO_DISCOVERY_SOURCE` result with no cards. Aggregate per-preset statuses as `COMPLETED` when all selected presets pass, `PARTIAL` when at least one passes and at least one fails, and `FAILED` when none passes.

Add:

```python
@app.command("research-discover")
def research_discover(
    config: str = "config/research-discovery.yaml",
    preset: str | None = None,
    source: str | None = None,
    capture: str | None = None,
    dry_run: bool = False,
    observed_at: str | None = None,
) -> None:
    cfg = load_config(config)
    result = run_discovery(
        cfg,
        preset_name=preset,
        source_name=source,
        capture_path=capture,
        dry_run=dry_run,
        observed_at=_research_observed_at(observed_at),
    )
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True, default=str))
```

The command must:

1. load configured presets and source capabilities;
2. compile the filter plan;
3. execute only the selected filtered source/capture;
4. validate native filters and apply post-filters;
5. deduplicate and rank at the configured limit;
6. print JSON containing `status`, `result_count`, `filter_plan`, `validation`, `candidate_count`, and candidate cards;
7. never call `run_research_pipeline`, enrichment, comps, redevelopment, or detail-page code.

When no source/capture is configured, print an explicit `NO_DISCOVERY_SOURCE` status rather than falling back to `research-crawl`.

Implement the configuration helpers used by `run_discovery`:

```python
def _materialize_preset(
    config: Mapping[str, Any],
    preset_config: Mapping[str, Any],
) -> dict[str, Any]:
    defaults = {
        key: config.get("discovery", {}).get(key)
        for key in ("max_result_cards", "sample_size", "shortlist_limit")
        if config.get("discovery", {}).get(key) is not None
    }
    materialized = {**defaults, **dict(preset_config)}
    return materialized


def _configured_presets(
    config: Mapping[str, Any],
    preset_name: str | None,
) -> list[Mapping[str, Any]]:
    configured = config.get("discovery", {}).get("presets", [])
    selected = [item for item in configured if isinstance(item, Mapping)]
    if preset_name is None:
        return selected
    return [item for item in selected if item.get("name") == preset_name]


def _configured_discovery_source(
    config: Mapping[str, Any],
    source_name: str | None,
    preset: DiscoveryPreset,
) -> Mapping[str, Any]:
    sources = [
        item
        for item in config.get("discovery", {}).get("sources", [])
        if isinstance(item, Mapping)
    ]
    enabled = [item for item in sources if item.get("enabled", True)]
    if source_name:
        enabled = [item for item in enabled if item.get("name") == source_name]
    if not enabled:
        raise ValueError(f"no enabled discovery source for preset {preset.name}")
    return enabled[0]


def _build_discovery_source(
    config: Mapping[str, Any],
    source_config: Mapping[str, Any],
    capture_path: str | Path | None,
) -> DiscoverySource:
    source_type = str(source_config.get("type", "")).upper()
    if capture_path is not None:
        return JsonFilteredCaptureSource(
            name=str(source_config["name"]),
            path=capture_path,
            config=source_config,
        )
    if source_type == "JSON_CAPTURE":
        return JsonFilteredCaptureSource(
            name=str(source_config["name"]),
            path=resolve_path(str(source_config["path"]), dict(config)),
            config=source_config,
        )
    if source_type == "PLAYWRIGHT_NATIVE":
        return PlaywrightNativeDiscoverySource.from_config(source_config)
    raise ValueError(f"unsupported discovery source type: {source_type}")
```

The implementation must pass `config` into `_build_discovery_source` so relative paths resolve through the existing project-root helper. A missing preset name produces a clear CLI error and never selects all sources accidentally.

- [ ] **Step 5: Run CLI tests and a dry run.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_cli.py -q
.venv/bin/northshore-watchdog research-discover --config config/research-discovery.yaml --preset vancouver-west --dry-run
```

Expected: tests pass; dry-run prints the filter plan and no listing/detail request count.

### Task 5: Persist bounded discovery evidence and render a discovery report

**Files:**

- Create: `src/northshore_watchdog/research/discovery_report.py`
- Modify: `src/northshore_watchdog/research/storage.py` only if needed for a helper around `save_source_run`.
- Modify: `src/northshore_watchdog/cli.py`
- Test: `tests/test_research_discovery_cli.py`

- [ ] **Step 1: Write failing report and append-only history tests.**

Assert that a successful run writes:

```text
reports/discovery/YYYY-MM-DD.md
reports/discovery/latest.md
```

and includes expected filters, native filters, post-filters, result count, sample validation, candidate cards, source URL, and a statement that the result is not a complete MLS-market scan. Run twice with different observed times and assert both source-run payloads remain queryable; neither run may overwrite the prior source-run record.

- [ ] **Step 2: Implement report rendering and source-run persistence.**

Render sections:

```text
Executive Summary
Preset Results
Filter Validation
Shortlist
Excluded / Duplicate Cards
Source Limitations
```

Persist a `SourceRunResult` through existing `ResearchStorage.save_source_run`, mapping discovery statuses to existing transport statuses while retaining the exact discovery status and filter plan inside `crawl_metrics["discovery"]`. Preserve raw capture paths and source provenance. Do not save discovery cards as fully verified `PropertyRecord` intelligence observations.

- [ ] **Step 3: Run report/history tests and inspect generated Markdown.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery_cli.py -q
rtk sed -n '1,280p' reports/discovery/latest.md
```

Expected: the report is bounded, transparent, and explicitly does not contain final scores or development conclusions.

### Task 6: Update documentation and complete regression verification

**Files:**

- Modify: `README.md`
- Test: existing test suite plus new discovery tests.

- [ ] **Step 1: Document the new command and stage boundary.**

Add a README section showing:

```bash
.venv/bin/northshore-watchdog research-discover \
  --config config/research-discovery.yaml \
  --preset vancouver-west
```

Explain that native filtering happens before card acquisition, `max_result_cards=100` is a safety cap, filter failures stop the source, and `research-discover` does not replace the later detail/enrichment pipeline. State that the old broad Scrapy/Playwright command is not a fallback.

- [ ] **Step 2: Run all new tests.**

Run:

```bash
.venv/bin/pytest tests/test_research_discovery.py tests/test_research_discovery_sources.py tests/test_research_discovery_cli.py -q
```

Expected: all new tests pass.

- [ ] **Step 3: Run the complete regression suite.**

Run:

```bash
.venv/bin/pytest -q
```

Expected: exit code 0 and zero failures. Existing Phase 1–10 behavior, including `research-acquire`, `research-crawl`, source provenance, and report tests, must remain green.

- [ ] **Step 4: Verify the acceptance checklist from the design spec.**

Run:

```bash
rtk rg -n "research-discover|FILTER_NOT_APPLIED|RESULT_SET_TOO_LARGE|FILTER_VALIDATION_INCONCLUSIVE|POSSIBLE_DUPLICATE|shortlist_limit" src config tests README.md
```

Confirm manually from test output and generated report that:

1. no Stage 0–4 path requests a detail URL;
2. native filter violations produce zero candidates;
3. documented post-filter violations are locally excluded rather than source-failing;
4. result sets over 100 stop before ingestion;
5. unknown optional fields remain unknown;
6. MLS/address dedupe behavior is conservative;
7. output is bounded to 5–10 candidates;
8. no full-market recall claim is emitted;
9. no enrichment, comps, redevelopment, or final score code runs.
