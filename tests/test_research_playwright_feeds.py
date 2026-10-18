from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from northshore_watchdog.config import load_config
from northshore_watchdog.research.browser_feeds import (
    BrowserPageResponse,
    PlaywrightPublicSource,
)
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.pipeline import build_source_adapters
from northshore_watchdog.research.public_feeds import PublicFeedResponse
from northshore_watchdog.research.sources import SourceRunStatus
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
SEARCH_URL = "https://www.example.test/search"


def _robots_response(
    url: str,
    *,
    status_code: int = 200,
    body: str = "User-agent: *\nAllow: /\n",
) -> PublicFeedResponse:
    return PublicFeedResponse(
        url=url,
        status_code=status_code,
        headers={"content-type": "text/plain"},
        content=body.encode("utf-8"),
    )


def _listing_html(
    *,
    mls: str = "R4000001",
    address: str = "401 Browser Street",
    next_url: str | None = None,
) -> bytes:
    next_link = f'<a class="next" href="{next_url}">Next</a>' if next_url else ""
    return f"""
    <html><body>
      <article class="listing-card"
        data-address="{address}"
        data-city="Vancouver"
        data-neighbourhood="Knight"
        data-price="$1,425,000"
        data-mls-number="{mls}"
        data-property-type="Single Family"
        data-status="For Sale"
        data-url="https://www.example.test/real-estate/{mls}">
        <span class="beds">4</span><span class="baths">2.5</span>
        <span class="house-size">2,100 sqft</span>
        <span class="lot-size">4,026 sqft</span>
      </article>
      {next_link}
    </body></html>
    """.encode("utf-8")


def _source(
    tmp_path: Path,
    *,
    responses: dict[str, BrowserPageResponse],
    robots_response: PublicFeedResponse | None = None,
    feed_requests: list[dict] | None = None,
    **config,
) -> PlaywrightPublicSource:
    def browser_transport(request: dict) -> BrowserPageResponse:
        return responses[str(request["url"])]

    def robots_transport(url: str) -> PublicFeedResponse:
        return robots_response or _robots_response(url)

    return PlaywrightPublicSource(
        name="browser-public",
        source_type=SourceType.MLS,
        source_registry_id="source:browser-public",
        source_url=SEARCH_URL,
        config={
            "enabled": True,
            "source_role": "REALTOR_CA_PUBLIC",
            "priority": 1,
            "allowed_domains": ["www.example.test"],
            "respect_robots": True,
            "user_agent": "northshore-property-watchdog-test/1.0",
            "source_version": "test-playwright-v1",
            "max_requests_per_run": 5,
            "capture_screenshot": True,
            **config,
        },
        feed_requests=feed_requests or [{"url": SEARCH_URL, "parser": "public_html"}],
        browser_transport=browser_transport,
        robots_transport=robots_transport,
        raw_snapshot_dir=tmp_path / "raw",
        sleep_fn=lambda seconds: None,
    )


def test_rendered_page_is_parsed_and_browser_evidence_is_saved(tmp_path):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=_listing_html(),
        screenshot=b"fake-png",
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.coverage_complete is False
    assert result.records[0]["mls_number"] == "R4000001"
    assert result.records[0]["asking"] == 1_425_000
    assert result.records[0]["source_registry_id"] == "source:browser-public"
    target_snapshots = [item for item in result.snapshots if item.request_kind == "target"]
    assert len(target_snapshots) == 1
    snapshot = target_snapshots[0]
    assert Path(snapshot.raw_path).read_bytes() == response.content
    assert snapshot.screenshot_path is not None
    assert Path(snapshot.screenshot_path).read_bytes() == b"fake-png"


def test_realtor_rendered_listing_card_markup_is_supported(tmp_path):
    html = b"""
    <div class="listingCard card">
      <a href="#">Favorite</a>
      <a class="listingDetailsLink" href="/real-estate/4001/968-e-31st-avenue-vancouver">
        <div class="listingCardMLS">MLS: <span>R4000004</span></div>
        <div class="listingCardPrice" data-value-cad="$2,298,000">$2,298,000</div>
        <div class="listingCardAddress">968 E 31ST AVENUE, Vancouver, British Columbia</div>
        <div class="listingCardIconCon">
          <div class="listingCardIconNum">8</div>
          <div class="listingCardIconText">Bedrooms</div>
        </div>
        <div class="listingCardIconCon">
          <div class="listingCardIconNum">6</div>
          <div class="listingCardIconText">Bathrooms</div>
        </div>
        <div class="listingCardIconCon">
          <div class="listingCardIconNum">2627</div>
          <div class="listingCardIconText">Square Feet</div>
        </div>
      </a>
    </div>
    """
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html"},
        content=html,
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["mls_number"] == "R4000004"
    assert result.records[0]["address"] == "968 E 31ST AVENUE"
    assert result.records[0]["municipality"] == "Vancouver"
    assert result.records[0]["source_url"] == (
        "https://www.example.test/real-estate/4001/968-e-31st-avenue-vancouver"
    )
    assert result.records[0]["asking"] == 2_298_000
    assert result.records[0]["bedrooms"] == 8
    assert result.records[0]["bathrooms"] == 6
    assert result.records[0]["finished_sqft"] == 2627


def test_browser_page_script_marker_does_not_mask_real_listing_cards(tmp_path):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html"},
        content=(
            b"<html><head><script>challenge-platform</script></head>"
            b"<body>"
            b"<article class='listing-card' data-address='405 Browser Street' "
            b"data-city='Vancouver' data-price='$1,425,000' data-mls-number='R4000005' "
            b"data-url='https://www.example.test/real-estate/R4000005'></article>"
            b"</body></html>"
        ),
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["mls_number"] == "R4000005"


def test_non_listing_article_sections_do_not_make_a_valid_page_partial(tmp_path):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html"},
        content=(
            b"<article class='listing-card' data-address='406 Browser Street' "
            b"data-city='Vancouver' data-price='$1,425,000' data-mls-number='R4000006' "
            b"data-url='https://www.example.test/real-estate/R4000006'></article>"
            b"<article class='acc-content'>Market statistics and popular searches</article>"
        ),
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert len(result.records) == 1


def test_browser_pagination_obeys_page_cap_and_is_partial(tmp_path):
    page_one = SEARCH_URL
    page_two = f"{SEARCH_URL}?page=2"
    page_three = f"{SEARCH_URL}?page=3"
    responses = {
        page_one: BrowserPageResponse(
            url=page_one,
            status_code=200,
            headers={"content-type": "text/html"},
            content=_listing_html(next_url=page_two),
        ),
        page_two: BrowserPageResponse(
            url=page_two,
            status_code=200,
            headers={"content-type": "text/html"},
            content=_listing_html(mls="R4000002", address="402 Browser Street", next_url=page_three),
        ),
    }

    source = _source(
        tmp_path,
        responses=responses,
        feed_requests=[{
            "url": page_one,
            "parser": "public_html",
            "next_page_selector": "a.next",
            "max_pages_per_request": 2,
        }],
    )
    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert [item["mls_number"] for item in result.records] == ["R4000001", "R4000002"]
    assert [
        item.request_kind
        for item in result.snapshots
        if item.request_kind == "target"
    ] == ["target", "target"]
    assert any("pagination page cap" in warning for warning in result.warnings)


def test_browser_pagination_stops_on_repeated_page_fingerprint(tmp_path):
    page_one = SEARCH_URL
    page_two = f"{SEARCH_URL}?page=2"
    responses = {
        page_one: BrowserPageResponse(
            url=page_one,
            status_code=200,
            headers={"content-type": "text/html"},
            content=_listing_html(next_url=page_two),
        ),
        page_two: BrowserPageResponse(
            url=page_two,
            status_code=200,
            headers={"content-type": "text/html"},
            content=_listing_html(next_url=page_two),
        ),
    }

    result = _source(
        tmp_path,
        responses=responses,
        feed_requests=[{
            "url": page_one,
            "parser": "public_html",
            "next_page_selector": "a.next",
            "max_pages_per_request": 5,
        }],
    ).collect(OBSERVED_AT)

    assert len([item for item in result.snapshots if item.request_kind == "target"]) == 2
    assert any("fingerprint" in warning for warning in result.warnings)


@pytest.mark.parametrize("status_code", [403, 429])
def test_browser_block_response_is_recorded_as_policy_block(tmp_path, status_code):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=status_code,
        headers={"content-type": "text/html"},
        content=b"<html><body>Access denied</body></html>",
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert result.records == []
    assert result.snapshots[0].status_code == status_code
    assert "public feed blocked" in result.errors[0]


def test_browser_challenge_is_recorded_as_policy_block(tmp_path):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><body>Verify you are human</body></html>",
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert any("challenge" in error for error in result.errors)
    assert Path(result.snapshots[0].raw_path).exists()


def test_browser_redirect_to_unlisted_domain_is_blocked(tmp_path):
    response = BrowserPageResponse(
        url="https://not-allowed.example/redirect",
        status_code=200,
        headers={"content-type": "text/html"},
        content=_listing_html(),
    )

    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert any("disallowed domain" in error for error in result.errors)
    assert result.snapshots[0].final_url == response.url


def test_browser_source_rejects_non_get_requests_without_http_fallback(tmp_path):
    source = _source(
        tmp_path,
        responses={},
        feed_requests=[{"url": SEARCH_URL, "method": "POST", "parser": "public_html"}],
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.FAILED
    assert any("GET" in error for error in result.errors)


def test_pipeline_builds_playwright_source_from_config(tmp_path):
    config = load_config("config/research.yaml")
    config["source_registry"] = [{
        "source_registry_id": "source:browser-public",
        "name": "Browser public",
        "source_type": "MLS",
        "source_role": "REALTOR_CA_PUBLIC",
        "priority": 1,
        "enabled": True,
        "authorization_status": "NOT_APPLICABLE",
        "evidence_status": "CONFIRMED",
    }]
    config["acquisition"] = {"sources": [{
        "name": "browser-public",
        "type": "PLAYWRIGHT_PUBLIC",
        "source_type": "MLS",
        "source_role": "REALTOR_CA_PUBLIC",
        "source_registry_id": "source:browser-public",
        "source_url": SEARCH_URL,
        "feed_requests": [{"url": SEARCH_URL, "parser": "public_html"}],
        "allowed_domains": ["www.example.test"],
        "raw_snapshot_dir": str(tmp_path / "raw"),
        "enabled": True,
    }]}

    adapters = build_source_adapters(config)

    assert len(adapters) == 1
    assert isinstance(adapters[0], PlaywrightPublicSource)


def test_playwright_profile_has_two_bounded_public_sources():
    config = load_config("config/research-playwright.yaml")
    sources = config["acquisition"]["sources"]
    public_sources = [item for item in sources if item["type"] == "PLAYWRIGHT_PUBLIC"]

    assert [item["source_role"] for item in public_sources] == [
        "REALTOR_CA_PUBLIC",
        "ZOLO_PUBLIC",
    ]
    assert all(item["coverage_complete"] is False for item in public_sources)
    assert all(item["respect_robots"] is True for item in public_sources)
    assert all(item["max_requests_per_run"] == 1 for item in public_sources)
    assert all(item["max_pages_per_request"] == 1 for item in public_sources)
    assert all(item["capture_screenshot"] is True for item in public_sources)
    assert config["official_enrichment"]["enabled"] is False
    zolo = next(item for item in public_sources if item["source_role"] == "ZOLO_PUBLIC")
    assert zolo["source_url"].endswith("/vancouver-real-estate/houses")


def test_browser_snapshot_screenshot_path_is_persisted_append_only(tmp_path):
    response = BrowserPageResponse(
        url=SEARCH_URL,
        status_code=200,
        headers={"content-type": "text/html"},
        content=_listing_html(),
        screenshot=b"fake-png",
    )
    result = _source(tmp_path, responses={SEARCH_URL: response}).collect(OBSERVED_AT)
    target_snapshot = next(item for item in result.snapshots if item.request_kind == "target")

    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        source_run_id = storage.save_source_run(result)
        row = storage.conn.execute(
            "SELECT source_run_id, raw_path, screenshot_path FROM source_snapshots"
        ).fetchone()

    assert row["source_run_id"] == source_run_id
    assert row["raw_path"] == target_snapshot.raw_path
    assert row["screenshot_path"] == target_snapshot.screenshot_path
