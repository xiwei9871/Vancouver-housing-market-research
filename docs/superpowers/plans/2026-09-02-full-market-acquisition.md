# Greater Vancouver Full-Market Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking.

## Execution status — 2026-09-02

Tasks 0–8 were already implemented in the repository and were re-verified in
this execution. Task 9 was executed against the real public browser paths for
all nine configured entrypoints, followed by manifest ingestion. Task 10 was
completed with an explicit external-data blocker: both sources returned HTTP
403 challenge pages, so the result is `BLOCKED_POLICY`, not full-market
coverage. The authoritative handoff is
`reports/phase10/full-market-acquisition-2026-09-02.md`.

The original step checkboxes below describe the implementation sequence. The
execution result, real counts, raw-evidence paths, and acceptance boundary are
recorded in the handoff rather than inferred from checkbox state.

**Goal:** Build a resumable Scrapy + scrapy-playwright acquisition layer that discovers all reachable public active house listings in the configured Greater Vancouver routes, preserves raw evidence, and feeds the existing normalization, deduplication, storage, and Stage 1 screening pipeline.

**Architecture:** Keep the existing `PropertyRecord`, public HTML parser, acquisition merge logic, SQLite history, and Stage 1 rules. Add a separate `research-crawl` process that runs a Scrapy spider with browser rendering only where needed, writes append-only raw artifacts and a JSONL manifest, then let `research-acquire --from-crawl` consume that manifest through a normal `SourceAdapter`.

**Tech Stack:** Python 3.11, Scrapy, scrapy-playwright, Playwright Chromium, BeautifulSoup, Pydantic, existing SQLite/Markdown pipeline, pytest.

---

## File map

Create:

- `src/northshore_watchdog/research/crawling/__init__.py` — optional crawler package export; must remain importable when crawler extras are not installed.
- `src/northshore_watchdog/research/crawling/models.py` — manifest artifact, route coverage, and crawl summary models.
- `src/northshore_watchdog/research/crawling/policies.py` — URL allowlist, challenge detection, canonicalization, caps, and coverage helpers.
- `src/northshore_watchdog/research/crawling/artifacts.py` — raw response/screenshot persistence and append-only JSONL manifest writer.
- `src/northshore_watchdog/research/crawling/spider.py` — Scrapy request graph for Zolo and Realtor.ca search/detail pages.
- `src/northshore_watchdog/research/crawling/runner.py` — lazy Scrapy imports, Asyncio reactor settings, JOBDIR lifecycle, and run summary.
- `src/northshore_watchdog/research/crawl_source.py` — `SourceAdapter` that reads a completed crawl manifest into `SourceRunResult`.
- `config/research-full-market.yaml` — full-market routes and runtime limits, extending the current research policy.
- `data/research/sources/full_market_source_registry.yaml` — source identity and public-read evidence envelope.
- `tests/test_research_crawl_artifacts.py` — manifest/raw artifact tests.
- `tests/test_research_crawl_policies.py` — crawl policy tests.
- `tests/test_research_crawling.py` — pure spider/pagination/detail scheduling tests and optional Scrapy integration tests.
- `tests/test_research_crawl_source.py` — manifest-to-source adapter tests.
- `tests/test_research_full_market_config.py` — config and route contract tests.
- `tests/fixtures/crawl/zolo-search-page-1.html` — minimal deterministic Zolo search fixture.
- `tests/fixtures/crawl/zolo-detail.html` — minimal deterministic Zolo detail fixture.
- `tests/fixtures/crawl/realtor-search-page-1.html` — minimal Realtor SSR fixture.
- `tests/fixtures/crawl/realtor-repeated-page.html` — repeated Realtor page fixture.

Modify:

- `pyproject.toml` — add optional `crawler` dependencies without making the base CLI depend on Scrapy.
- `src/northshore_watchdog/research/public_feeds.py` — add Zolo card selectors/field extraction and Zolo detail parsing while retaining conservative no-guess behavior.
- `src/northshore_watchdog/research/sources.py` — add typed crawl metrics/coverage gaps to `SourceRunResult` with backward-compatible defaults.
- `src/northshore_watchdog/research/pipeline.py` — accept manifest adapters for acquire runs and register their source metadata using the existing storage path.
- `src/northshore_watchdog/cli.py` — add `research-crawl` and `--from-crawl` to `research-acquire`; emit crawl metrics in JSON.
- `src/northshore_watchdog/research/report.py` — include source coverage and crawl metrics when a crawl-backed run is reported.
- `tests/test_research_public_feeds.py` — cover Zolo search/detail extraction and ensure existing Realtor fixtures remain green.
- `tests/test_research_cli.py` — cover command registration and manifest argument behavior.
- `tests/test_research_pipeline.py` or `tests/test_research_end_to_end.py` — cover crawl manifest ingestion without starting a browser.

The repository has no Git metadata, so the implementation checkpoints below use test runs and file inspection rather than commit commands.

## Task 0: Establish the baseline before editing production code

**Files:**

- Test: no file changes.

- [ ] **Step 1: Run the existing research test suite.**

Run:

```bash
rtk .venv/bin/python -m pytest -q
```

Expected: the existing suite passes. Record the exact count in the implementation notes; any pre-existing failure must be isolated before proceeding.

- [ ] **Step 2: Confirm optional crawler dependencies are not silently required by the base import path.**

Run:

```bash
rtk .venv/bin/python -c "import northshore_watchdog.research.pipeline; print('base-import-ok')"
```

Expected output contains `base-import-ok`, whether or not Scrapy is installed.

## Task 1: Add the dependency and full-market configuration contract

**Files:**

- Modify: `pyproject.toml`
- Create: `config/research-full-market.yaml`
- Create: `data/research/sources/full_market_source_registry.yaml`
- Create: `tests/test_research_full_market_config.py`

- [ ] **Step 1: Write failing config tests.**

Add:

```python
from pathlib import Path

from northshore_watchdog.config import load_config


def test_full_market_config_has_user_approved_gates_and_routes():
    config = load_config("config/research-full-market.yaml")
    acquisition = config["acquisition"]
    crawl_sources = acquisition["crawl_sources"]

    assert config["renovation"]["user_strategy"]["baseline_hold_years"] == 5
    assert config["research_filters"]["minimum_bedrooms"] == 3
    assert config["research_filters"]["minimum_bathrooms"] == 2.5
    assert config["research_filters"]["minimum_finished_sqft"] == 1500
    assert config["research_filters"]["minimum_lot_area_sqft"] == 3000
    assert config["research_filters"]["minimum_frontage_ft"] == 33
    assert config["research_filters"]["minimum_depth_ft"] == 100
    assert config["research_filters"]["major_road_discount_threshold"] == 0.15
    assert config["research_filters"]["major_road_distance_threshold_m"] == 50

    names = {item["name"] for item in crawl_sources}
    assert names == {"zolo-full-market", "realtor-full-market"}
    zolo = next(item for item in crawl_sources if item["name"] == "zolo-full-market")
    realtor = next(item for item in crawl_sources if item["name"] == "realtor-full-market")
    assert len(zolo["entrypoints"]) == 4
    assert len(realtor["entrypoints"]) == 5
    assert all(item["allowed_domains"] for item in crawl_sources)


def test_full_market_source_registry_is_loaded_from_project_path():
    config = load_config("config/research-full-market.yaml")
    registry_path = Path(config["_project_root"]) / config["source_registry_path"]
    assert registry_path.exists()
    assert config["source_registry_path"].endswith("full_market_source_registry.yaml")


def test_public_crawl_limits_are_safety_caps_not_result_counts():
    config = load_config("config/research-full-market.yaml")
    crawl = config["acquisition"]["crawl"]
    assert crawl["max_search_pages"] > 50
    assert crawl["max_detail_requests"] > 100
    assert "expected_public_result_count" not in crawl

- [ ] **Step 2: Run the new tests and verify the contract fails before implementation.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_full_market_config.py
```

Expected: FAIL because `config/research-full-market.yaml` and its full-market keys do not yet exist.

- [ ] **Step 3: Add the crawler extra and concrete full-market config.**

Add this optional dependency group to `pyproject.toml`:

```toml
crawler = [
  "scrapy>=2.11,<3",
  "scrapy-playwright>=0.0.40,<1",
]
```

Create `config/research-full-market.yaml` with the existing research policy as its parent and these explicit acquisition settings:

```yaml
extends: research.yaml

schema_version: research-full-market-v1
source_registry_path: data/research/sources/full_market_source_registry.yaml

research_filters:
  minimum_bedrooms: 3
  minimum_bathrooms: 2.5
  minimum_finished_sqft: 1500
  minimum_lot_area_sqft: 3000
  minimum_frontage_ft: 33
  minimum_depth_ft: 100
  major_road_distance_threshold_m: 50
  major_road_discount_threshold: 0.15
  allow_small_lot_house_rebuild_exception: true
  current_target_property_types:
    - DETACHED_HOUSE
    - SINGLE_FAMILY_RESIDENCE
  excluded_current_property_types:
    - DUPLEX
    - TRIPLEX
    - TOWNHOUSE
    - COACH_HOUSE
    - STRATA
    - LEASEHOLD

acquisition:
  strategy: FULL_MARKET_SCRAPY_PLAYWRIGHT
  allow_single_source_provisional: true
  priority_order:
    - MLS_PRIMARY
    - REALTOR_CA_PUBLIC
    - ZOLO_PUBLIC
  primary_source_roles:
    - MLS_PRIMARY
  sources: []
  crawl:
    enabled: true
    respect_robots: true
    concurrency_per_domain: 1
    delay_seconds: 1.0
    max_search_pages: 200
    max_detail_requests: 2000
    max_requests: 3000
    timeout_seconds: 45
    max_retries: 2
    headless: true
    browser: chromium
    screenshot_on_error: true
    screenshot_entrypoints: true
  crawl_sources:
    - name: zolo-full-market
      source_type: AGGREGATOR
      source_role: ZOLO_PUBLIC
      source_registry_id: source:zolo-full-market
      allowed_domains: [www.zolo.ca, zolo.ca]
      parser: zolo_html
      detail_parser: zolo_detail
      entrypoints:
        - municipality: City of Vancouver
          url: https://www.zolo.ca/vancouver-real-estate/houses
        - municipality: North Vancouver
          url: https://www.zolo.ca/north-vancouver-real-estate/houses
        - municipality: West Vancouver
          url: https://www.zolo.ca/west-vancouver-real-estate/houses
        - municipality: Burnaby
          url: https://www.zolo.ca/burnaby-real-estate/houses
    - name: realtor-full-market
      source_type: MLS
      source_role: REALTOR_CA_PUBLIC
      source_registry_id: source:realtor-full-market
      allowed_domains: [www.realtor.ca, realtor.ca]
      parser: realtor_ca_html
      detail_parser: realtor_ca_html
      pagination_mode: browser_control
      entrypoints:
        - municipality: City of Vancouver
          url: https://www.realtor.ca/bc/vancouver/single-family-homes-for-sale
        - municipality: City of North Vancouver
          url: https://www.realtor.ca/bc/city-of-north-vancouver/single-family-homes-for-sale
        - municipality: District of North Vancouver
          url: https://www.realtor.ca/bc/district-of-north-vancouver/single-family-homes-for-sale
        - municipality: District of West Vancouver
          url: https://www.realtor.ca/bc/west-vancouver/single-family-homes-for-sale
        - municipality: City of Burnaby
          url: https://www.realtor.ca/bc/burnaby/single-family-homes-for-sale
```

`research_filters` is the cheap crawler/detail-fetch mirror. The existing inherited `research_policy` remains the authoritative Stage 1 gate, so implementation must keep the two values synchronized in this config and add a config-contract test if either changes.

The crawler must apply the existing `market_buckets` mapping after extraction. The broad Vancouver and North Vancouver routes are intentionally not split by asking price in the URL, so local Stage 1 can retain the user-approved ranges.

Create `data/research/sources/full_market_source_registry.yaml`:

```yaml
sources:
  - source_registry_id: source:zolo-full-market
    name: zolo-full-market
    source_type: AGGREGATOR
    source_role: ZOLO_PUBLIC
    domain: zolo.ca
    access_method: public_browser_read
    authorization_status: NOT_APPLICABLE
    evidence_status: UNCONFIRMED
    reliability_notes: Public aggregator listing pages; use for discovery and provenance, not official title or assessment claims.
    enabled: true
  - source_registry_id: source:realtor-full-market
    name: realtor-full-market
    source_type: MLS
    source_role: REALTOR_CA_PUBLIC
    domain: realtor.ca
    access_method: public_browser_read
    authorization_status: NOT_APPLICABLE
    evidence_status: UNCONFIRMED
    reliability_notes: Public Realtor.ca listing pages; coverage is complete only when verified page advancement reaches natural exhaustion.
    enabled: true
```

- [ ] **Step 4: Run the config tests and inspect dependency metadata.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_full_market_config.py
rtk .venv/bin/python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies']['crawler'])"
```

Expected: all config tests pass and the printed list contains both `scrapy` and `scrapy-playwright`.

## Task 2: Make the public parser understand Zolo search and detail pages

**Files:**

- Modify: `src/northshore_watchdog/research/public_feeds.py`
- Modify: `tests/test_research_public_feeds.py`
- Create: `tests/fixtures/crawl/zolo-search-page-1.html`
- Create: `tests/fixtures/crawl/zolo-detail.html`

- [ ] **Step 1: Add failing parser tests and small fixtures.**

Add a fixture containing the actual Zolo card shape:

```html
<html><head><link rel="canonical" href="https://www.zolo.ca/vancouver-real-estate/houses"></head>
<body>
  <article class="card-listing">
    <a class="tile-overlay-link" href="/vancouver-real-estate/401-test-street">Open</a>
    <div class="card-listing--location">
      <span class="street">401 Test Street</span>
      <span class="city">Vancouver</span>
      <span class="province">BC</span>
      <span class="neighbourhood">Killarney VE</span>
    </div>
    <ul class="card-listing--values">
      <li class="price"><span itemprop="price" value="1425000">$1,425,000</span></li>
      <li>4 bed</li><li>3 bath</li><li>2,100 sqft</li><li>Built in 1981</li>
    </ul>
  </article>
  <a rel="next" href="/vancouver-real-estate/houses/page-2">Next</a>
</body></html>
```

Add a detail fixture containing the visible detail labels:

```html
<html><head><title>401 Test Street, Vancouver — For Sale @ $1,425,000</title></head>
<body>
  <h1>401 Test Street</h1><div class="city">Vancouver</div>
  <div>4 bed</div><div>3 bath</div><div>2,100 sqft</div>
  <div>Single Family Residence</div><div>Lot Size 4,026 sqft</div>
  <div>Year Built 1981</div><div>Taxes $6,000/yr</div>
  <div>MLS®# R4000001</div><div>Property Area Minor Killarney VE</div>
  <div class="description">Detached family house on a regular lot.</div>
</body></html>
```

Add tests:

```python
from datetime import datetime, timezone
from pathlib import Path

from northshore_watchdog.research.public_feeds import parse_public_html, parse_zolo_detail


FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_zolo_search_card_extracts_address_price_and_house_metrics():
    rows = parse_public_html(
        (FIXTURES / "zolo-search-page-1.html").read_bytes(),
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo-full-market",
        OBSERVED_AT,
    )
    assert len(rows) == 1
    assert rows[0]["address"] == "401 Test Street"
    assert rows[0]["municipality"] == "Vancouver"
    assert rows[0]["neighbourhood"] == "Killarney VE"
    assert rows[0]["asking"] == 1_425_000
    assert rows[0]["bedrooms"] == 4
    assert rows[0]["bathrooms"] == 3
    assert rows[0]["finished_sqft"] == 2100
    assert rows[0]["source_url"] == "https://www.zolo.ca/vancouver-real-estate/401-test-street"


def test_zolo_detail_extracts_lot_type_tax_year_and_mls():
    row = parse_zolo_detail(
        (FIXTURES / "zolo-detail.html").read_bytes(),
        "https://www.zolo.ca/vancouver-real-estate/401-test-street",
        OBSERVED_AT,
    )
    assert row["property_type"] == "Single Family Residence"
    assert row["lot_area_sqft"] == 4026
    assert row["year_built"] == 1981
    assert row["mls_number"] == "R4000001"
    assert row["taxes"] == 6000
    assert row["source_url"] == "https://www.zolo.ca/vancouver-real-estate/401-test-street"


def test_zolo_information_article_is_not_a_listing_card():
    html = b"<article class='article-card'>Vancouver houses guide</article>"
    assert parse_public_html(html, "https://www.zolo.ca/vancouver-real-estate/houses", "zolo", OBSERVED_AT) == []
```

- [ ] **Step 2: Run only the new parser tests and confirm they fail.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_public_feeds.py -k 'zolo'
```

Expected: FAIL because the current card parser does not extract `.card-listing` values and `parse_zolo_detail` does not exist.

- [ ] **Step 3: Implement conservative Zolo search extraction.**

Update `_card_row()` and `_looks_like_listing_card()` in `public_feeds.py` using explicit Zolo selectors. The implementation must use the existing `_number`, `_price`, `_absolute_url`, `_html_address_parts`, and `_lot_measurements` helpers. The core branch must extract:

```python
is_zolo = bool(card.select_one(".card-listing--location, .card-listing--values, a.tile-overlay-link"))
if is_zolo:
    location = card.select_one(".card-listing--location")
    raw_address = _first(card, (".card-listing--location .street", ".card-listing--location"))
    address, address_municipality = _html_address_parts(raw_address)
    municipality = _first(card, (".card-listing--location .city", ".city")) or address_municipality
    neighbourhood = _first(card, (".card-listing--location .neighbourhood", ".neighbourhood"))
    anchor = card.select_one("a.tile-overlay-link[href]")
    values_node = card.select_one(".card-listing--values")
    values = " ".join(values_node.get_text(" ").split()) if values_node else ""
    price_node = card.select_one(".card-listing--values .price [itemprop='price'], .card-listing--values .price")
    price_text = price_node.get("value") if price_node and price_node.get("value") else values
    bed_match = re.search(r"(\d+(?:\.\d+)?)\s*bed", values, re.I)
    bath_match = re.search(r"(\d+(?:\.\d+)?)\s*bath", values, re.I)
    sqft_match = re.search(r"([\d,]+)\s*sqft", values, re.I)
    year_match = re.search(r"built\s+in\s+(\d{4})", values, re.I)
    result = {
        "address": address,
        "municipality": municipality,
        "neighbourhood": neighbourhood,
        "asking": _price(price_text),
        "bedrooms": _number(bed_match.group(1)) if bed_match else None,
        "bathrooms": _number(bath_match.group(1)) if bath_match else None,
        "finished_sqft": _number(sqft_match.group(1)) if sqft_match else None,
        "year_built": _number(year_match.group(1)) if year_match else None,
        "source_url": _absolute_url(anchor.get("href"), requested_url) if anchor else requested_url,
        "feed_url": requested_url,
    }
    return {key: value for key, value in result.items() if value not in (None, "")}
```

Remove the unused `location` local if linting flags it. Add `card-listing` to `_looks_like_listing_card()` and add `.card-listing` to the card selector without allowing generic informational `<article>` nodes to become listing rows.

- [ ] **Step 4: Implement `parse_zolo_detail()` with visible-label and JSON-LD fallbacks.**

Add this public contract:

```python
def parse_zolo_detail(content: bytes, requested_url: str, observed_at: datetime) -> dict[str, Any]:
    """Parse one Zolo detail page without inventing missing legal facts."""
```

Implementation rules:

1. Strip `script`, `style`, and `template` before visible-text regex parsing.
2. Extract address from `h1`, `.street`, or explicit JSON-LD address; extract municipality from `.city` or JSON-LD.
3. Extract price from JSON-LD offers or title/visible `$` value.
4. Extract bed/bath/sqft with label-bound regexes, and lot area only from `Lot Size`, `Lot Area`, or explicit JSON-LD `lotSize`.
5. Extract type, year, taxes, MLS number, neighbourhood, and description only from explicit labels/selectors.
6. If no address and no MLS number exists, raise `ValueError("Zolo detail page has no explicit property identity")`.
7. Always add `source_url`, `feed_url`, and an ISO `observed_at` value.

Keep `taxes` as a separate field; do not map it to assessment or official property tax until an official enrichment source confirms its semantics.

- [ ] **Step 5: Run parser tests and the existing public-feed suite.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_public_feeds.py tests/test_research_playwright_feeds.py
```

Expected: all new Zolo tests and all pre-existing public/browser parser tests pass.

## Task 3: Define crawl artifacts, coverage states, and safety policies

**Files:**

- Create: `src/northshore_watchdog/research/crawling/__init__.py`
- Create: `src/northshore_watchdog/research/crawling/models.py`
- Create: `src/northshore_watchdog/research/crawling/policies.py`
- Create: `src/northshore_watchdog/research/crawling/artifacts.py`
- Modify: `src/northshore_watchdog/research/sources.py`
- Create: `tests/test_research_crawl_policies.py`
- Create: `tests/test_research_crawl_artifacts.py`

- [ ] **Step 1: Write failing policy and artifact tests.**

Use these contracts:

```python
from pathlib import Path

from northshore_watchdog.research.crawling.artifacts import ArtifactWriter
from northshore_watchdog.research.crawling.policies import canonical_url, page_fingerprint, url_allowed


def test_url_policy_requires_allowed_http_domain():
    assert url_allowed("https://www.zolo.ca/vancouver-real-estate/houses", {"zolo.ca"})
    assert not url_allowed("https://example.com/out", {"zolo.ca"})
    assert not url_allowed("javascript:void(0)", {"zolo.ca"})


def test_canonical_url_removes_fragment_and_trailing_noise():
    assert canonical_url("https://www.zolo.ca/vancouver-real-estate/houses#page=2") == "https://www.zolo.ca/vancouver-real-estate/houses"


def test_fingerprint_is_stable_for_same_body():
    assert page_fingerprint(b"same") == page_fingerprint(b"same")
    assert page_fingerprint(b"same") != page_fingerprint(b"different")


def test_artifact_writer_saves_raw_body_and_manifest_line(tmp_path):
    writer = ArtifactWriter(tmp_path, source_name="zolo-full-market", run_id="run-1")
    artifact = writer.write(
        request_kind="SEARCH",
        requested_url="https://www.zolo.ca/vancouver-real-estate/houses",
        final_url="https://www.zolo.ca/vancouver-real-estate/houses",
        body=b"<html>listing</html>",
        status_code=200,
        headers={"content-type": "text/html"},
        rows=[{"address": "401 Test Street"}],
        page_number=1,
        coverage_state="IN_PROGRESS",
    )
    assert Path(artifact.raw_path).read_bytes() == b"<html>listing</html>"
    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"request_kind":"SEARCH"' in lines[0]
```

- [ ] **Step 2: Run the policy/artifact tests and verify they fail.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawl_policies.py tests/test_research_crawl_artifacts.py
```

Expected: FAIL because the crawling package and artifact writer do not exist.

- [ ] **Step 3: Add the typed manifest models.**

`crawling/models.py` must define these Pydantic models with defaults that allow old manifests to be read:

```python
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CrawlArtifact(BaseModel):
    run_id: str
    source_name: str
    source_registry_id: str
    entrypoint_url: str | None = None
    request_kind: Literal["ROBOTS", "SEARCH", "DETAIL"]
    requested_url: str
    final_url: str | None = None
    canonical_url: str | None = None
    page_number: int | None = None
    detail_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    raw_path: str | None = None
    screenshot_path: str | None = None
    sha256: str
    size_bytes: int
    retrieved_at: datetime
    parser: str | None = None
    parse_status: str = "NOT_RUN"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    next_url: str | None = None
    card_count: int = 0
    extracted_row_count: int = 0
    coverage_state: str = "IN_PROGRESS"
    skip_reason: str | None = None
    error_type: str | None = None
    retry_count: int = 0


class CrawlRunSummary(BaseModel):
    run_id: str
    source_name: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "RUNNING"
    coverage_complete: bool = False
    entrypoints: int = 0
    entrypoints_complete: int = 0
    pages_fetched: int = 0
    details_fetched: int = 0
    cards_found: int = 0
    rows_extracted: int = 0
    duplicate_pages: int = 0
    skipped_details: int = 0
    failed_requests: int = 0
    coverage_gaps: list[str] = Field(default_factory=list)
```

Also add `crawl_metrics: dict[str, Any] = Field(default_factory=dict)` and `coverage_gaps: list[str] = Field(default_factory=list)` to `SourceRunResult` in `sources.py`, after `warnings`, so current serialized payloads remain valid.

- [ ] **Step 4: Implement policy helpers and append-only artifact writing.**

Implement these public functions with concrete bodies:

```python
from hashlib import sha256
from urllib.parse import urlparse, urlunparse


def url_allowed(url: str, allowed_domains: set[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == domain.rstrip(".").lower() or host.endswith(f".{domain.rstrip('.').lower()}") for domain in allowed_domains)


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def page_fingerprint(body: bytes) -> str:
    return sha256(body).hexdigest()


def challenge_detected(body: bytes) -> bool:
    return _has_challenge(body)
```

`url_allowed()` accepts only `http`/`https`, requires a hostname, and accepts the exact configured domain or a subdomain. `canonical_url()` removes fragments, removes a trailing slash except for a bare host, and leaves query parameters intact because search pagination may use them. `page_fingerprint()` uses SHA-256. `challenge_detected()` delegates to the existing visible-text challenge logic or moves that helper into `policies.py` without changing its markers.

`ArtifactWriter.write()` must:

1. create `raw/search`, `raw/detail`, and `screenshots/entry-and-errors` directories;
2. write the exact response body using a timestamp, request index, and SHA prefix in the filename;
3. optionally write a PNG screenshot only when `capture_screenshot` is true;
4. create a `CrawlArtifact`, serialize it as one JSON line with `model_dump_json()`, and flush the manifest file;
5. return the validated `CrawlArtifact`.

The writer must never truncate an existing manifest. A new run must receive a new run directory.

Also implement `summarize_artifacts(artifacts, expected_entrypoints=())` and `status_from_metrics(metrics, normalize_errors)` in `policies.py` with these observable rules: count only `SEARCH` artifacts as pages, count only `DETAIL` artifacts with a successful status as details, count repeated hashes once in `duplicate_pages`, collect every non-empty `error_type` and coverage gap, and set `coverage_complete=True` only when every configured entrypoint has a natural-completion artifact and no gap/error/cap exists. Each artifact carries `entrypoint_url`; the runner passes the configured entrypoint URLs to the summary helper.

- [ ] **Step 5: Run the tests and inspect the manifest.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawl_policies.py tests/test_research_crawl_artifacts.py tests/test_research_sources.py
```

Expected: all new policy/artifact tests and source-model compatibility tests pass.

## Task 4: Implement the Scrapy spider request graph

**Files:**

- Create: `src/northshore_watchdog/research/crawling/spider.py`
- Create: `tests/test_research_crawling.py`
- Create: `tests/fixtures/crawl/realtor-search-page-1.html`
- Create: `tests/fixtures/crawl/realtor-repeated-page.html`

- [ ] **Step 1: Write failing pure helper tests for pagination and detail scheduling.**

Test the spider through source-independent helper methods so unit tests do not require a browser:

```python
from northshore_watchdog.research.crawling.spider import next_zolo_url, realtor_page_advanced, should_fetch_detail


def test_zolo_next_url_is_same_domain_and_comes_from_rel_next():
    html = b'<a rel="next" href="/vancouver-real-estate/houses/page-2">Next</a>'
    assert next_zolo_url(html, "https://www.zolo.ca/vancouver-real-estate/houses", {"zolo.ca"}) == "https://www.zolo.ca/vancouver-real-estate/houses/page-2"


def test_zolo_next_url_rejects_external_link():
    html = b'<a rel="next" href="https://example.com/page-2">Next</a>'
    assert next_zolo_url(html, "https://www.zolo.ca/vancouver-real-estate/houses", {"zolo.ca"}) is None


def test_realtor_page_advanced_requires_new_listing_identity_set():
    old = {"R1", "R2"}
    assert realtor_page_advanced(old, {"R2", "R3"})
    assert not realtor_page_advanced(old, {"R1", "R2"})


def test_detail_policy_fetches_unknown_or_plausible_cards_but_skips_clear_out_of_scope():
    assert should_fetch_detail({"asking": 1_425_000, "bedrooms": None}, {"max_asking": 1_600_000})
    assert should_fetch_detail({"asking": 1_425_000, "bedrooms": 4}, {"max_asking": 1_600_000})
    assert not should_fetch_detail({"asking": 2_500_000, "bedrooms": 2}, {"max_asking": 1_600_000})
```

- [ ] **Step 2: Run the new spider tests and verify they fail.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py
```

Expected: FAIL because `spider.py` and its pure helpers do not exist.

- [ ] **Step 3: Add lazy Scrapy imports and the spider settings contract.**

`spider.py` must import Scrapy inside the optional module boundary. If Scrapy is absent, importing `northshore_watchdog.research.crawling` remains valid, while constructing `FullMarketSpider` raises:

```python
RuntimeError("Scrapy crawler extra is not installed; install the crawler extra")
```

The spider class must expose `name = "northshore_full_market"`, accept one source definition and run context, and use these settings supplied by `runner.py`:

```python
{
    "ROBOTSTXT_OBEY": True,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    "DOWNLOAD_DELAY": 1.0,
    "RETRY_TIMES": 2,
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    "DOWNLOAD_HANDLERS": {
        "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    },
    "PLAYWRIGHT_BROWSER_TYPE": "chromium",
}
```

Every request must carry `source_name`, `source_registry_id`, `request_kind`, `page_number`, and `run_id` in `meta`. Every request must be restricted to the configured allowed domains.

Store seen page URLs, page fingerprints, route completion states, and detail-request keys in `self.state` so Scrapy JOBDIR resume retains them across process interruption; in-memory sets may be used as a fast projection but cannot be the only state.

- [ ] **Step 4: Implement Zolo search pagination and detail requests.**

Implement `next_zolo_url()` with BeautifulSoup, `rel="next"` first and an explicit next-page selector fallback. Canonicalize and validate the URL before returning it. The callback must:

1. save the rendered response body as a `SEARCH` artifact;
    2. call `parse_public_html(body, page_url, source_name, observed_at)`;
3. collect each row’s detail URL;
4. apply `should_fetch_detail()` using the configured maximum price and cheap card fields;
5. yield `scrapy.Request` detail requests with `playwright=True` and `request_kind="DETAIL"` for plausible/unknown rows;
6. yield exactly one next-page request when the page fingerprint and canonical URL have not been seen;
7. mark a repeated page, cap, challenge, or missing-next contradiction in the artifact and summary.

The detail callback calls `parse_zolo_detail()` and writes a `DETAIL` artifact. A detail failure produces an error artifact and does not remove its search-card row.

For Realtor detail URLs, use a separate callback that saves the rendered body as `DETAIL`, parses only explicit listing/detail fields with the existing conservative HTML parser (or a new `parse_realtor_detail()` helper if the fixture proves the generic parser insufficient), and preserves the original search row when detail parsing fails.

- [ ] **Step 5: Implement Realtor verification-based browser pagination.**

For Realtor search entrypoints, create the initial request with:

```python
meta={
    "playwright": True,
    "playwright_include_page": True,
    "request_kind": "SEARCH",
}
```

The async callback must keep one page at a time and execute this state machine:

```python
previous_ids = set()
for page_number in range(1, max_search_pages + 1):
    body = (await page.content()).encode("utf-8")
    rows = parse_public_html(body, page.url, source_name, observed_at)
    current_ids = {row.get("mls_number") or row.get("source_url") for row in rows}
    write_search_artifact(body, page.url, page_number, rows)
    if page_number > 1 and not realtor_page_advanced(previous_ids, current_ids):
        record_coverage_gap("PAGINATION_NOT_ADVANCED")
        break
    yield detail_requests(rows)
    if not await click_and_wait_for_next_listing_set(page, current_ids):
        mark_complete_if_natural_end_or_partial()
        break
    previous_ids = current_ids
await page.close()
```

`click_and_wait_for_next_listing_set()` must use visible controls configured by selector, wait for either a changed listing identity set or a changed page marker, and return false on timeout, `href="#"` with no state change, challenge, or repeated HTML fingerprint. It must never synthesize an undocumented API URL. All `page.close()` paths belong in `finally`.

- [ ] **Step 6: Run pure spider tests and optional Scrapy import tests.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py
```

Expected: pure pagination/detail-policy tests pass. If the crawler extra is not installed, Scrapy-specific tests are skipped with an explicit reason rather than failing the base suite.

## Task 5: Add the crawl runner, JOBDIR, and run summary

**Files:**

- Create: `src/northshore_watchdog/research/crawling/runner.py`
- Modify: `src/northshore_watchdog/research/crawling/__init__.py`
- Modify: `tests/test_research_crawling.py`

- [ ] **Step 1: Write failing runner tests.**

Add tests with a monkeypatched fake crawler process:

```python
from pathlib import Path

from northshore_watchdog.research.crawling.runner import build_crawl_job_dir, crawl_settings


def test_job_directory_is_unique_per_source_and_run(tmp_path):
    first = build_crawl_job_dir(tmp_path, "zolo-full-market", "run-1")
    second = build_crawl_job_dir(tmp_path, "zolo-full-market", "run-2")
    assert first != second
    assert first.name == "scrapy-jobdir"


def test_crawl_settings_enable_async_scrapy_playwright_and_safety_caps(tmp_path):
    settings = crawl_settings(
        {"headless": True, "concurrency_per_domain": 1, "delay_seconds": 1.0},
        tmp_path / "job",
    )
    assert settings["TWISTED_REACTOR"] == "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
    assert settings["CONCURRENT_REQUESTS_PER_DOMAIN"] == 1
    assert settings["DOWNLOAD_DELAY"] == 1.0
    assert settings["JOBDIR"] == str(tmp_path / "job")
```

For `run_crawl()`, provide a fake spider summary and assert that a `PAGINATION_NOT_ADVANCED` summary is `PARTIAL`, has `coverage_complete=False`, and writes `summary.json`. The fake process must be injected through a function parameter or monkeypatch, not by starting a real reactor in the unit test.

- [ ] **Step 2: Run runner tests and verify they fail.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py -k 'job_directory or crawl_settings or run_crawl'
```

Expected: FAIL because `runner.py` does not exist.

- [ ] **Step 3: Implement `crawl_settings()` and isolated job directories.**

Implement:

```python
def build_crawl_job_dir(base_dir: Path, source_name: str, run_id: str) -> Path:
    path = base_dir / source_name / run_id / "scrapy-jobdir"
    path.mkdir(parents=True, exist_ok=True)
    return path


def crawl_settings(crawl_config: Mapping[str, Any], job_dir: Path) -> dict[str, Any]:
    return {
        "ROBOTSTXT_OBEY": bool(crawl_config.get("respect_robots", True)),
        "CONCURRENT_REQUESTS_PER_DOMAIN": max(1, int(crawl_config.get("concurrency_per_domain", 1))),
        "DOWNLOAD_DELAY": max(0.0, float(crawl_config.get("delay_seconds", 1.0))),
        "RETRY_TIMES": max(0, int(crawl_config.get("max_retries", 2))),
        "JOBDIR": str(job_dir),
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": str(crawl_config.get("browser", "chromium")),
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": bool(crawl_config.get("headless", True))},
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": int(float(crawl_config.get("timeout_seconds", 45)) * 1000),
        "LOG_ENABLED": False,
    }
```

The settings function must receive `job_dir` rather than inventing it, so a resumed run uses the same scheduler state. Do not share a JOBDIR between sources or run IDs.

- [ ] **Step 4: Implement `run_crawl()` with lazy imports and summary persistence.**

`run_crawl(config, source_name, run_id, project_root, crawler_process_factory=None, overrides=None)` must:

1. load the matching `acquisition.crawl_sources` definition;
2. create `data/research/crawl_jobs/<source>/<run_id>/`;
    3. instantiate `CrawlerProcess(crawl_settings(crawl_config, job_dir))` only after importing Scrapy;
4. schedule `FullMarketSpider` with the route definition and artifact directory;
5. apply `overrides` only to explicit numeric safety limits such as `max_search_pages` and `max_detail_requests`, then call `process.start()` once in this process;
6. read the JSONL manifest and produce `summary.json` from `CrawlRunSummary`; the sidecar includes configured entrypoint URLs so a route that never returned a page cannot be mistaken for complete;
7. return a summary object with `SUCCESS`, `PARTIAL`, `FAILED`, or `BLOCKED_POLICY`;
8. classify complete only when every configured entrypoint is `COVERAGE_COMPLETE` and no cap/error/challenge occurred.

If Scrapy or scrapy-playwright is missing, raise a clear `RuntimeError` instructing the operator to install `.[crawler]`; do not silently fall back to bounded HTTP.

The CLI exposes the smoke-slice limits as `--max-search-pages` and `--max-detail-requests`; these become `overrides` and never modify the YAML file.

- [ ] **Step 5: Run runner tests and inspect the generated state paths.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py
```

Expected: all runner and pure spider tests pass; no production database is created by these tests.

## Task 6: Bridge completed crawl manifests into the existing acquisition pipeline

**Files:**

- Create: `src/northshore_watchdog/research/crawl_source.py`
- Modify: `src/northshore_watchdog/research/pipeline.py`
- Modify: `src/northshore_watchdog/cli.py`
- Modify: `src/northshore_watchdog/research/acquisition.py`
- Modify: `tests/test_research_crawl_source.py`
- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_research_cli.py`
- Modify: `tests/test_research_acquisition.py`

- [ ] **Step 1: Write failing manifest-source tests.**

Create a fixture manifest with one search artifact, one detail artifact for the same MLS, one snapshot path, and a partial coverage gap. Test:

```python
import json
from datetime import datetime, timezone

from northshore_watchdog.research.crawl_source import CrawlManifestSource
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.sources import SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_manifest_source_returns_rows_snapshots_and_coverage_metrics(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    (tmp_path / "search.html").write_bytes(b"search")
    (tmp_path / "detail.html").write_bytes(b"detail")
    manifest.write_text(
        "\n".join([
            json.dumps({
                "run_id": "run-1", "source_name": "zolo-full-market",
                "source_registry_id": "source:zolo-full-market",
                "request_kind": "SEARCH", "requested_url": "https://www.zolo.ca/houses",
                "final_url": "https://www.zolo.ca/houses", "status_code": 200,
                "content_type": "text/html", "raw_path": str(tmp_path / "search.html"),
                "sha256": "a" * 64, "size_bytes": 6,
                "retrieved_at": "2026-09-02T00:00:00Z", "rows": [{
                    "mls_number": "R4000001", "address": "401 Test Street",
                    "municipality": "Vancouver", "asking": 1425000,
                }], "coverage_state": "PARTIAL_CAP_REACHED",
                "card_count": 1, "extracted_row_count": 1,
            }),
            json.dumps({
                "run_id": "run-1", "source_name": "zolo-full-market",
                "source_registry_id": "source:zolo-full-market",
                "request_kind": "DETAIL", "requested_url": "https://www.zolo.ca/401-test-street",
                "final_url": "https://www.zolo.ca/401-test-street", "status_code": 200,
                "content_type": "text/html", "raw_path": str(tmp_path / "detail.html"),
                "sha256": "b" * 64, "size_bytes": 6,
                "retrieved_at": "2026-09-02T00:00:01Z", "rows": [{
                    "mls_number": "R4000001", "address": "401 Test Street",
                    "municipality": "Vancouver", "bedrooms": 4,
                    "bathrooms": 3, "lot_area_sqft": 4026,
                }], "coverage_state": "IN_PROGRESS",
                "card_count": 0, "extracted_row_count": 1,
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    source = CrawlManifestSource(
        manifest_path=manifest,
        name="zolo-full-market",
        source_type=SourceType.AGGREGATOR,
        source_registry_id="source:zolo-full-market",
        source_url="https://www.zolo.ca/vancouver-real-estate/houses",
        config={"source_role": "ZOLO_PUBLIC"},
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert result.coverage_complete is False
    assert result.records[0]["bedrooms"] == 4
    assert result.records[0]["lot_area_sqft"] == 4026
    assert len(result.snapshots) == 2
    assert result.crawl_metrics["details_fetched"] == 1
```

- [ ] **Step 2: Run manifest-source tests and verify they fail.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawl_source.py
```

Expected: FAIL because `CrawlManifestSource` and the typed source metrics do not exist.

- [ ] **Step 3: Implement `CrawlManifestSource` without creating a second property database.**

The adapter must subclass `_BaseSource` and implement `collect(observed_at)`:

```python
class CrawlManifestSource(_BaseSource):
    def __init__(self, *, manifest_path: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.manifest_path = Path(manifest_path)

    def collect(self, observed_at: datetime) -> SourceRunResult:
        artifacts = [
            CrawlArtifact.model_validate_json(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = [row for artifact in artifacts for row in artifact.rows]
        snapshots = [
            snapshot_from_artifact(artifact, self.source_registry_id, observed_at)
            for artifact in artifacts
            if artifact.raw_path and artifact.status_code is not None
        ]
        expected_entrypoints = self.config.get("expected_entrypoints", [])
        metrics = summarize_artifacts(artifacts, expected_entrypoints=expected_entrypoints)
        normalized, normalize_errors = self._normalize_rows(rows, observed_at)
        status = status_from_metrics(metrics, normalize_errors)
        return self._result(
            started_at=min((artifact.retrieved_at for artifact in artifacts), default=observed_at),
            finished_at=max((artifact.retrieved_at for artifact in artifacts), default=observed_at),
            records=normalized,
            snapshots=snapshots,
            errors=normalize_errors + metrics["errors"],
            warnings=metrics["warnings"],
            status=status,
        ).model_copy(update={
            "coverage_complete": metrics["coverage_complete"],
            "crawl_metrics": metrics,
            "coverage_gaps": metrics["coverage_gaps"],
        })
```

`source_snapshot` conversion must preserve `request_kind`, raw path, screenshot path, hash, byte size, status, final URL, observed/retrieved timestamps. Search and detail rows remain separate raw observations; the existing `acquire_from_sources()` merge/dedup logic combines sparse rows by MLS/URL/address and records conflicts.

Status mapping:

- all entrypoints naturally complete and no errors → `SUCCESS`, `coverage_complete=True`;
- records exist but any cap, repeated page, pagination gap, error, or challenge exists → `PARTIAL`, `coverage_complete=False`;
- no records and policy blocked → `BLOCKED_POLICY`;
- no records and parse/runtime failure → `FAILED`.

- [ ] **Step 4: Enforce the approved current-house minimums in Stage 1.**

Add a small helper in `acquisition.py` that reads the inherited `research_policy.current_house_minimums` values and returns a gate state plus reason codes:

```python
def _current_house_gate(record: PropertyRecord, config: dict[str, Any]) -> tuple[str, list[str]]:
    minimums = config.get("research_policy", {}).get("current_house_minimums", {})
    checks = {
        "bedrooms": (record.bedrooms, float(minimums.get("bedrooms", 3)), "BEDROOMS_BELOW_MINIMUM"),
        "bathrooms": (record.bathrooms, float(minimums.get("bathrooms", 2.5)), "BATHROOMS_BELOW_MINIMUM"),
        "finished_sqft": (record.finished_sqft, float(minimums.get("finished_sqft", 1500)), "FINISHED_SQFT_BELOW_MINIMUM"),
    }
    unknown = [field for field, (value, _minimum, _reason) in checks.items() if value is None]
    if unknown:
        return "NEED_VERIFICATION", [f"CURRENT_USE_{field.upper()}_UNKNOWN" for field in unknown]
    failed = [reason for value, minimum, reason in checks.values() if float(value) < minimum]
    if failed:
        return "OUT_OF_SCOPE", failed
    return "PASS", ["CURRENT_USE_MINIMUMS_MET"]
```

Call this helper only after a record has been classified as a detached-house primary candidate. A failed known minimum must set the candidate to `D_REJECT`/`OUT_OF_SCOPE`; an unknown minimum must set `NEED_VERIFICATION` and `included=False`. Add tests for 3/2.5/1500 passing, one known value failing, and one missing value routing to verification. Keep the 3,000 sqft/33 ft/100 ft lot gate separate so a property is not rejected for current-use metrics by a redevelopment-only rule.

In `scan_stage1()`, immediately after `_research_scope()` and before the `included` expression, apply the result as follows:

```python
current_gate, current_reasons = _current_house_gate(record, config)
reasons.extend(current_reasons)
if primary_candidate and current_gate == "OUT_OF_SCOPE" and gate_state == "PASS":
    gate_state = "OUT_OF_SCOPE"
    candidate_status = CandidateStatus.D_REJECT
elif primary_candidate and current_gate == "NEED_VERIFICATION":
    gate_state = "NEED_VERIFICATION"
    candidate_status = CandidateStatus.NEED_VERIFICATION
```

Set `primary_candidate` before this block and keep `included = gate_state == "PASS" and primary_candidate and range_class in {"CORE", "EXTENDED", "SPECIAL"}` unchanged.

- [ ] **Step 5: Add optional manifest input to pipeline and CLI.**

Extend `run_research_pipeline()` with a backward-compatible parameter:

```python
def run_research_pipeline(
    config: dict[str, Any],
    *,
    stages: Iterable[str] | None = None,
    source_names: set[str] | None = None,
    provider_names: set[str] | None = None,
    observed_at: datetime | None = None,
    dry_run: bool = False,
    crawl_manifest_paths: Iterable[str | Path] | None = None,
) -> ResearchRunResult:
```

When `crawl_manifest_paths` is non-empty, construct one `CrawlManifestSource` per manifest, register the corresponding trusted source definition, and do not run the ordinary fixture/public adapters. All downstream code remains the existing `acquire_from_sources()` → `scan_stage1()` path.

- [ ] **Step 6: Add CLI command tests for crawl and manifest acquire.**

Add:

```python
def fake_research_result():
    return SimpleNamespace(
        summary={}, run_type="research", status="SUCCESS", dry_run=True,
        run_id=None, report_path=None, latest_path=None,
    )


def test_research_acquire_from_crawl_uses_manifest_adapter(tmp_path, monkeypatch):
    captured = {}

    def fake_pipeline(config, **kwargs):
        captured.update(kwargs)
        return fake_research_result()

    monkeypatch.setattr("northshore_watchdog.cli.run_research_pipeline", fake_pipeline)
    result = CliRunner().invoke(
        app,
        ["research-acquire", "--from-crawl", str(tmp_path / "manifest.jsonl")],
    )
    assert result.exit_code == 0
    assert captured["crawl_manifest_paths"] == [str(tmp_path / "manifest.jsonl")]
    assert captured["stages"] == {"acquire"}
```

Import `SimpleNamespace` in the test module. The new `research-crawl` command must pass config, source names, and run ID to `run_configured_crawl()` and print JSON containing `source_name`, `status`, `coverage_complete`, `pages_fetched`, `cards_found`, `details_fetched`, and `coverage_gaps`.

- [ ] **Step 7: Run the bridge suite.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawl_source.py tests/test_research_pipeline.py tests/test_research_cli.py
```

Expected: all manifest, pipeline, and CLI tests pass without opening a browser.

## Task 7: Surface crawl coverage and Stage 1 counts in reports

**Files:**

- Modify: `src/northshore_watchdog/research/report.py`
- Modify: `src/northshore_watchdog/research/pipeline.py`
- Create: `tests/test_research_full_market_report.py`

- [ ] **Step 1: Write a failing report test.**

Use a `ResearchRunResult` with one partial crawl source and assert the rendered summary contains source-level metrics:

```python
def test_full_market_summary_exposes_coverage_and_stage1_counts():
    result = make_result_with_source_run(
        source_name="realtor-full-market",
        status="PARTIAL",
        coverage_complete=False,
        crawl_metrics={
            "entrypoints": 5,
            "pages_fetched": 1,
            "cards_found": 11,
            "details_fetched": 4,
            "coverage_gaps": ["PAGINATION_NOT_ADVANCED"],
        },
    )
    summary = summarize_research_run(result)
    assert summary["crawl"]["realtor-full-market"]["coverage"] == "PARTIAL"
    assert summary["crawl"]["realtor-full-market"]["pages_fetched"] == 1
    assert "PAGINATION_NOT_ADVANCED" in summary["crawl"]["realtor-full-market"]["coverage_gaps"]
```

The test module must define the helper with the existing models:

```python
from datetime import datetime, timezone

from northshore_watchdog.research.pipeline import ResearchRunResult
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus


def make_result_with_source_run(*, source_name, status, coverage_complete, crawl_metrics):
    source_run = SourceRunResult(
        source_registry_id=f"source:{source_name}",
        source_name=source_name,
        status=SourceRunStatus(status),
        coverage_complete=coverage_complete,
        started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        crawl_metrics=crawl_metrics,
        coverage_gaps=crawl_metrics.get("coverage_gaps", []),
    )
    return ResearchRunResult(
        run_type="test",
        status="SUCCESS",
        observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        source_runs=[source_run],
    )
```

It must not make a network request.

- [ ] **Step 2: Run the report test and verify it fails.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_full_market_report.py
```

Expected: FAIL because crawl metrics are not yet projected into the research summary.

- [ ] **Step 3: Implement a backward-compatible crawl summary projection.**

In `summarize_research_run()`, add a `crawl` mapping derived only from source runs that have `crawl_metrics`:

```python
summary["crawl"] = {
    source_run.source_name: {
        "status": source_run.status.value if hasattr(source_run.status, "value") else str(source_run.status),
        "coverage": "COMPLETE" if source_run.coverage_complete is True else "PARTIAL",
        "pages_fetched": source_run.crawl_metrics.get("pages_fetched", 0),
        "cards_found": source_run.crawl_metrics.get("cards_found", 0),
        "details_fetched": source_run.crawl_metrics.get("details_fetched", 0),
        "rows_extracted": source_run.crawl_metrics.get("rows_extracted", 0),
        "duplicates": source_run.crawl_metrics.get("duplicates", 0),
        "errors": source_run.crawl_metrics.get("errors", []),
        "coverage_gaps": source_run.coverage_gaps,
    }
    for source_run in result.source_runs
    if source_run.crawl_metrics
}
```

Add Stage 1 counts to the same summary without changing score semantics: raw cards, normalized records, in-range records, excluded records, and `NEED_VERIFICATION` records. Preserve the distinction between `assessment_discount` and `price_dislocation`; this task only reports counts.

- [ ] **Step 4: Run the full report and end-to-end unit suites.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_full_market_report.py tests/test_research_report.py tests/test_research_end_to_end.py
```

Expected: all report and end-to-end tests pass, including old reports that have no crawl metrics.

## Task 8: Add resume, deduplication, conflict, and coverage integration tests

**Files:**

- Modify: `tests/test_research_crawling.py`
- Modify: `tests/test_research_crawl_source.py`
- Modify: `tests/test_research_acquisition.py`
- Modify: `tests/test_research_storage.py`

- [ ] **Step 1: Add failing tests for resume and duplicate behavior.**

Cover these concrete cases:

```python
from datetime import datetime, timezone
import json

from northshore_watchdog.config import load_config
from northshore_watchdog.research.acquisition import acquire_from_sources
from northshore_watchdog.research.crawl_source import CrawlManifestSource
from northshore_watchdog.research.crawling.models import CrawlArtifact
from northshore_watchdog.research.models import SourceType


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_config():
    return load_config("config/research-full-market.yaml")


def make_artifact(*, page_number, sha256):
    return CrawlArtifact(
        run_id="run-1",
        source_name="zolo-full-market",
        source_registry_id="source:zolo-full-market",
        entrypoint_url="https://www.zolo.ca/vancouver-real-estate/houses",
        request_kind="SEARCH",
        requested_url=f"https://www.zolo.ca/vancouver-real-estate/houses/page-{page_number}",
        final_url=f"https://www.zolo.ca/vancouver-real-estate/houses/page-{page_number}",
        page_number=page_number,
        status_code=200,
        content_type="text/html",
        sha256=sha256,
        size_bytes=1,
        retrieved_at=OBSERVED_AT,
        coverage_state="IN_PROGRESS",
    )


def write_manifest_source(tmp_path, rows):
    artifact = CrawlArtifact(
        run_id="run-1",
        source_name="zolo-full-market",
        source_registry_id="source:zolo-full-market",
        entrypoint_url="https://www.zolo.ca/vancouver-real-estate/houses",
        request_kind="SEARCH",
        requested_url="https://www.zolo.ca/vancouver-real-estate/houses",
        final_url="https://www.zolo.ca/vancouver-real-estate/houses",
        status_code=200,
        content_type="text/html",
        sha256="c" * 64,
        size_bytes=1,
        retrieved_at=OBSERVED_AT,
        rows=rows,
        coverage_state="COVERAGE_COMPLETE",
    )
    path = tmp_path / "manifest.jsonl"
    path.write_text(artifact.model_dump_json() + "\n", encoding="utf-8")
    return CrawlManifestSource(
        manifest_path=path,
        name="zolo-full-market",
        source_type=SourceType.AGGREGATOR,
        source_registry_id="source:zolo-full-market",
        source_url="https://www.zolo.ca/vancouver-real-estate/houses",
        config={"source_role": "ZOLO_PUBLIC", "expected_entrypoints": ["https://www.zolo.ca/vancouver-real-estate/houses"]},
    )


def test_second_manifest_page_with_same_sha_is_not_counted_as_new_page():
    summary = summarize_artifacts([
        make_artifact(page_number=1, sha256="a" * 64),
        make_artifact(page_number=2, sha256="a" * 64),
    ])
    assert summary["duplicate_pages"] == 1
    assert summary["coverage_complete"] is False


def test_same_mls_from_search_and_detail_merges_sparse_fields_in_existing_acquisition(tmp_path):
    result = acquire_from_sources(
        [write_manifest_source(tmp_path, [
            {"mls_number": "R4000001", "address": "401 Test Street", "municipality": "Vancouver", "asking": 1425000},
            {"mls_number": "R4000001", "address": "401 Test Street", "municipality": "Vancouver", "bedrooms": 4, "lot_area_sqft": 4026},
        ])],
        observed_at=OBSERVED_AT,
        config=test_config(),
    )
    assert len(result.records) == 1
    assert result.records[0].bedrooms == 4
    assert result.records[0].lot_area_sqft == 4026
    assert result.duplicate_count == 1


def test_conflicting_mls_and_address_evidence_is_retained_as_open_conflict(tmp_path):
    result = acquire_from_sources(
        [write_manifest_source(tmp_path, [
            {"mls_number": "R4000002", "address": "402 Test Street", "municipality": "Vancouver"},
            {"mls_number": "R4000002", "address": "999 Other Street", "municipality": "Vancouver"},
        ])],
        observed_at=OBSERVED_AT,
        config=test_config(),
    )
    assert result.conflicts
    assert any(item.field_name in {"address", "municipality"} for item in result.conflicts)
```

The test helpers must construct actual `CrawlArtifact`/`CrawlManifestSource` objects in the test module; they must not reference undeclared external fixtures.

- [ ] **Step 2: Run the targeted tests and verify the new assertions expose missing behavior.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_crawling.py tests/test_research_crawl_source.py tests/test_research_acquisition.py tests/test_research_storage.py
```

Expected: the newly added cases fail only where manifest metrics, source bridging, or existing merge behavior needs the planned changes.

- [ ] **Step 3: Implement the smallest corrections and preserve existing identity logic.**

Do not add a second property deduplication algorithm in the crawler. Use the existing acquisition identity order and make the manifest source return each raw observation. Only add crawler-level page URL/hash suppression; property identity dedup remains in `acquire_from_sources()`.

Ensure `ResearchStorage.save_source_run()` serializes `crawl_metrics` and `coverage_gaps` through the existing `SourceRunResult` payload and that `list_source_runs()` reads both old and new JSON payloads.

- [ ] **Step 4: Run all research tests.**

Run:

```bash
rtk .venv/bin/python -m pytest -q tests/test_research_*.py
```

Expected: the complete research suite passes.

## Task 9: Install the crawler extra and execute real-data validation

**Files:**

- Modify: environment package installation only.
- Create at runtime: `data/research/crawl_jobs/` artifacts and `reports/full-market/` output.

- [ ] **Step 1: Install the optional crawler dependencies and browser.**

Run:

```bash
rtk .venv/bin/python -m pip install -e '.[crawler,browser]'
rtk .venv/bin/python -m playwright install chromium
```

Expected: imports for `scrapy`, `scrapy_playwright`, and `playwright` succeed. If dependency resolution fails, preserve the failure output and do not start the real crawl.

- [ ] **Step 2: Run the deterministic suite after installation.**

Run:

```bash
rtk .venv/bin/python -m pytest -q
```

Expected: all existing and new tests pass.

- [ ] **Step 3: Run the real-data smoke slice before the full crawl.**

Add a CLI/config override or runner argument that limits each Zolo entrypoint to two search pages and Realtor to one entrypoint. Run:

```bash
rtk .venv/bin/northshore-watchdog research-crawl --config config/research-full-market.yaml --sources zolo-full-market --run-id real-smoke-zolo
rtk .venv/bin/northshore-watchdog research-crawl --config config/research-full-market.yaml --sources realtor-full-market --run-id real-smoke-realtor
```

Expected:

- Zolo returns real raw pages, card rows, detail artifacts, and either `COMPLETE` or an explicit partial/error status per route;
- Realtor returns the real first page and explicitly reports whether pagination advanced;
- no source claims complete merely because the first page returned HTTP 200;
- manifest rows contain source URLs and raw paths that can be manually inspected.

- [ ] **Step 4: Ingest the smoke manifests into Stage 1 without enrichment.**

Run:

```bash
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-full-market.yaml --from-crawl data/research/crawl_jobs/zolo-full-market/real-smoke-zolo/artifacts/manifest.jsonl
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-full-market.yaml --from-crawl data/research/crawl_jobs/realtor-full-market/real-smoke-realtor/artifacts/manifest.jsonl
```

Expected: JSON output includes source status, crawl metrics, raw/normalized/Stage 1 counts, candidate states, and `NEED_VERIFICATION` reasons. No official assessment/zoning/school lookup is silently claimed by a public listing row.

- [ ] **Step 5: Manually inspect a sample before full-market execution.**

Inspect at least five Zolo listing/detail pairs and every Realtor page artifact returned by the smoke slice. Verify address, asking, MLS, bed/bath, house sqft, lot size, and detail URL correspondence. Record discrepancies in the data-conflict queue.

- [ ] **Step 6: Run the complete reachable Zolo crawl.**

Run:

```bash
rtk .venv/bin/northshore-watchdog research-crawl --config config/research-full-market.yaml --sources zolo-full-market --run-id real-full-zolo
```

Expected: all configured Zolo routes are traversed until natural exhaustion or an explicit cap/policy/error; summary reports pages, cards, details, duplicates, gaps, and route-level coverage.

- [ ] **Step 7: Run Realtor full entrypoints only with verified pagination.**

Run:

```bash
rtk .venv/bin/northshore-watchdog research-crawl --config config/research-full-market.yaml --sources realtor-full-market --run-id real-full-realtor
```

Expected: if UI pagination does not advance, the run remains auditable and `PARTIAL`; it is not described as a full Realtor/MLS scan. If it advances and naturally exhausts all five entrypoints, report `COMPLETE` with the evidence.

- [ ] **Step 8: Ingest the final manifests and save the Phase 2 acquisition summary.**

Run:

```bash
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-full-market.yaml --from-crawl data/research/crawl_jobs/zolo-full-market/real-full-zolo/artifacts/manifest.jsonl
rtk .venv/bin/northshore-watchdog research-acquire --config config/research-full-market.yaml --from-crawl data/research/crawl_jobs/realtor-full-market/real-full-realtor/artifacts/manifest.jsonl
```

The final handoff must state exact counts and coverage status per source, list the top Stage 1 candidates and `NEED_VERIFICATION` records, and distinguish source discovery from official title/assessment/zoning/school confirmation.

## Task 10: Final verification and Phase 2 handoff

**Files:**

- Inspect: all files changed above.
- Create: `reports/full-market/<date>.md` only after real acquisition succeeds.

- [ ] **Step 1: Run compile and complete test verification.**

Run:

```bash
rtk .venv/bin/python -m compileall -q src tests
rtk .venv/bin/python -m pytest -q
```

Expected: compile succeeds and the full suite passes.

- [ ] **Step 2: Verify the artifact and data contracts.**

Run:

```bash
rtk .venv/bin/python -c "from pathlib import Path; root=Path('data/research/crawl_jobs'); manifests=list(root.glob('*/real-*/artifacts/manifest.jsonl')); print({'manifests': len(manifests), 'nonempty': sum(bool(p.read_text(encoding='utf-8').strip()) for p in manifests)})"
```

Expected: every reported completed/partial real run has a non-empty manifest and raw files referenced by its rows exist.

- [ ] **Step 3: Report Phase 2 status without overstating coverage.**

The final response must include:

- modified files;
- installed/runtime dependencies;
- exact source/page/card/detail/normalized/Stage 1 counts;
- Zolo and Realtor route-level coverage states;
- examples of real records and their source URLs;
- skipped/unknown/conflict counts;
- whether Realtor pagination reached complete coverage or remained partial;
- current blockers and data-source limitations;
- explicit statement that Phase 3–5 automation was not entered.
