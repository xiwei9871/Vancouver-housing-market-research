from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.public_feeds import (
    PublicFeedResponse,
    PublicFeedSource,
    parse_public_html,
    parse_realtor_ca_detail,
    parse_zolo_detail,
)
from northshore_watchdog.research.sources import SourceRunStatus
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "crawl"
ZOLO_OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _robots_response(url: str, body: str = "User-agent: *\nAllow: /\n") -> PublicFeedResponse:
    return PublicFeedResponse(
        url=url,
        status_code=200,
        headers={"content-type": "text/plain"},
        content=body.encode("utf-8"),
    )


def _source(
    tmp_path: Path,
    *,
    response: PublicFeedResponse,
    source_name: str = "realtor-public",
    source_url: str = "https://www.realtor.ca/bc/vancouver/real-estate",
    parser: str = "realtor_ca_json",
    feed_requests: list[dict] | None = None,
    transport=None,
    robots_transport=None,
    **config,
) -> PublicFeedSource:
    return PublicFeedSource(
        name=source_name,
        source_type=SourceType.MLS if source_name.startswith("realtor") else SourceType.AGGREGATOR,
        source_registry_id=f"source:{source_name}",
        source_url=source_url,
        config={
            "enabled": True,
            "source_role": "REALTOR_CA_PUBLIC" if source_name.startswith("realtor") else "ZOLO_PUBLIC",
            "priority": 1,
            "allowed_domains": ["www.realtor.ca"] if source_name.startswith("realtor") else ["www.zolo.ca"],
            "respect_robots": True,
            "user_agent": "northshore-property-watchdog-test/1.0",
            "source_version": "test-public-feed-v1",
            **config,
        },
        feed_requests=feed_requests or [{"url": source_url, "parser": parser}],
        transport=transport or (lambda request: response),
        robots_transport=robots_transport or (lambda url: _robots_response(url)),
        raw_snapshot_dir=tmp_path / "raw",
        sleep_fn=lambda seconds: None,
    )


def test_public_json_feed_maps_realtor_results_and_writes_snapshot(tmp_path):
    response = PublicFeedResponse(
        url="https://api2.realtor.ca/Listing.svc/PropertySearch_Post",
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps({
            "Paging": {"TotalRecords": 1},
            "Results": [{
                "Id": 3001,
                "MlsNumber": "R3000001",
                    "Property": {
                        "PriceUnformattedValue": 1_500_000,
                        "Address": {"AddressText": "100 TEST STREET|Vancouver, BC"},
                        "Type": "Single Family",
                        "OwnershipType": "Freehold",
                        "LandSize": "33 x 122 FT",
                        "OriginalPrice": 1_600_000,
                    },
                    "Building": {
                        "Bedrooms": "3",
                        "BathroomTotal": "2",
                        "SizeInterior": "1800 sqft",
                    },
                    "ListingDate": "2026-08-01T00:00:00-07:00",
                    "DaysOnMarket": 12,
                    "CumulativeDaysOnMarket": 19,
                    "RelistCount": 1,
                    "RelativeURLEn": "/real-estate/3001/100-test-street-vancouver",
            }],
        }).encode("utf-8"),
    )
    source = _source(
        tmp_path,
        response=response,
        source_url=response.url,
        feed_requests=[{
            "url": response.url,
            "method": "POST",
            "parser": "realtor_ca_json",
            "base_url": "https://www.realtor.ca",
        }],
        allowed_domains=["api2.realtor.ca"],
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.coverage_complete is False
    assert result.records[0]["mls_number"] == "R3000001"
    assert result.records[0]["asking"] == 1_500_000
    assert result.records[0]["address"] == "100 TEST STREET"
    assert result.records[0]["municipality"] == "Vancouver"
    assert result.records[0]["source_url"] == "https://www.realtor.ca/real-estate/3001/100-test-street-vancouver"
    assert "lot_area_sqft" not in result.records[0]
    assert result.records[0]["frontage_ft"] == 33
    assert result.records[0]["depth_ft"] == 122
    assert result.records[0]["original_asking"] == 1_600_000
    assert result.records[0]["listing_date"] == "2026-08-01T00:00:00-07:00"
    assert result.records[0]["dom"] == 12
    assert result.records[0]["cumulative_dom"] == 19
    assert result.records[0]["relist_count"] == 1
    assert result.snapshots[0].sha256
    assert Path(result.snapshots[0].raw_path).read_bytes() == response.content


def test_public_html_parser_maps_realtor_and_zolo_style_cards(tmp_path):
    html = b"""
    <html><body>
      <article class="listing-card"
        data-address="101 Public Street"
        data-city="Vancouver"
        data-neighbourhood="Knight"
        data-price="$1,425,000"
        data-mls-number="R3000002"
        data-property-type="Single Family"
        data-status="For Sale"
        data-url="https://www.realtor.ca/real-estate/3002/101-public-street-vancouver">
        <span class="beds">4</span><span class="baths">2</span>
        <span class="house-size">2,100 sqft</span>
        <span class="lot-size">4,026 sqft</span>
      </article>
    </body></html>
    """
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/single-family-homes-for-sale",
        status_code=200,
        headers={"content-type": "text/html"},
        content=html,
    )

    realtor = _source(
        tmp_path / "realtor",
        response=response,
        parser="public_html",
        feed_requests=[{"url": response.url, "parser": "public_html"}],
    )
    result = realtor.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["mls_number"] == "R3000002"
    assert result.records[0]["asking"] == 1_425_000
    assert result.records[0]["lot_area_sqft"] == 4_026
    assert result.records[0]["source_role"] == "REALTOR_CA_PUBLIC"


def test_public_html_parser_works_for_zolo_style_card(tmp_path):
    html = b"""
    <article data-listing-card data-address="202 Zolo Street" data-city="North Vancouver"
      data-neighbourhood="Lynn Valley" data-price="1699000" data-mls-number="R3000003"
      data-url="https://www.zolo.ca/north-vancouver-real-estate/202-zolo-street">
      <span class="beds">3 bed</span><span class="baths">2 bath</span>
    </article>
    """
    response = PublicFeedResponse(
        url="https://www.zolo.ca/north-vancouver-real-estate/north-vancouver/houses",
        status_code=200,
        headers={"content-type": "text/html"},
        content=html,
    )
    source = _source(
        tmp_path,
        response=response,
        source_name="zolo-public",
        source_url=response.url,
        parser="public_html",
        feed_requests=[{"url": response.url, "parser": "public_html"}],
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.records[0]["address"] == "202 Zolo Street"
    assert result.records[0]["asking"] == 1_699_000
    assert result.records[0]["source_type"] == "AGGREGATOR"
    assert result.records[0]["source_role"] == "ZOLO_PUBLIC"


def test_html_card_without_explicit_price_does_not_use_address_number(tmp_path):
    html = b'''<article class="listing-card" data-address="404 No Price Street"
        data-city="Vancouver" data-url="https://www.realtor.ca/real-estate/3005/404-no-price-street">
        <span class="beds">3</span>
    </article>'''
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/single-family-homes-for-sale",
        status_code=200,
        headers={"content-type": "text/html"},
        content=html,
    )
    source = _source(
        tmp_path,
        response=response,
        parser="public_html",
        feed_requests=[{"url": response.url, "parser": "public_html"}],
    )

    result = source.collect(OBSERVED_AT)

    assert result.records[0]["address"] == "404 No Price Street"
    assert "asking" not in result.records[0]


def test_malformed_public_json_rows_are_quarantined_as_partial(tmp_path):
    response = PublicFeedResponse(
        url="https://api2.realtor.ca/Listing.svc/PropertySearch_Post",
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps({
            "Results": [
                {"MlsNumber": "R3000004", "Property": {"PriceUnformattedValue": 1_000_000}},
                "not-a-listing",
            ]
        }).encode("utf-8"),
    )
    source = _source(
        tmp_path,
        response=response,
        source_url=response.url,
        feed_requests=[{"url": response.url, "parser": "realtor_ca_json"}],
        allowed_domains=["api2.realtor.ca"],
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert len(result.records) == 1
    assert any("row" in error for error in result.errors)


def test_robots_denial_prevents_target_request(tmp_path):
    calls: list[dict] = []
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    source = _source(
        tmp_path,
        response=response,
        transport=lambda request: calls.append(request) or response,
        robots_transport=lambda url: _robots_response(url, "User-agent: *\nDisallow: /\n"),
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert calls == []
    assert "robots" in " ".join(result.errors).lower()


def test_missing_robots_prevents_target_request(tmp_path):
    calls: list[dict] = []
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )

    def missing_robots(url: str):
        return PublicFeedResponse(
            url=url,
            status_code=503,
            headers={"content-type": "text/plain"},
            content=b"unavailable",
        )

    source = _source(
        tmp_path,
        response=response,
        transport=lambda request: calls.append(request) or response,
        robots_transport=missing_robots,
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert calls == []
    assert "robots" in " ".join(result.errors).lower()


def test_cloudflare_content_signal_comments_do_not_count_as_challenge(tmp_path):
    calls: list[dict] = []
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    robots = """# BEGIN Cloudflare Managed content
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
Allow: /
# END Cloudflare Managed content
"""
    source = _source(
        tmp_path,
        response=response,
        transport=lambda request: calls.append(request) or response,
        robots_transport=lambda url: _robots_response(url, robots),
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.SUCCESS
    assert len(calls) == 1


def test_robots_response_is_snapshotted_for_policy_audit(tmp_path):
    target_url = "https://www.realtor.ca/bc/vancouver/real-estate"
    response = PublicFeedResponse(
        url=target_url,
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    robots_url = "https://www.realtor.ca/robots.txt"
    robots = _robots_response(robots_url)
    source = _source(
        tmp_path,
        response=response,
        robots_transport=lambda url: robots,
    )

    result = source.collect(OBSERVED_AT)

    assert {snapshot.request_kind for snapshot in result.snapshots} == {"target", "robots"}
    assert any(snapshot.url == robots_url for snapshot in result.snapshots)


def test_403_and_challenge_response_stop_source(tmp_path):
    calls: list[dict] = []
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=403,
        headers={"content-type": "text/html"},
        content=b"Request blocked",
    )
    source = _source(
        tmp_path,
        response=response,
        transport=lambda request: calls.append(request) or response,
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert len(calls) == 1
    assert result.snapshots[0].status_code == 403


def test_blocked_json_host_does_not_prevent_allowed_html_fallback(tmp_path):
    api_url = "https://api2.realtor.ca/Listing.svc/PropertySearch_Post"
    html_url = "https://www.realtor.ca/bc/vancouver/single-family-homes-for-sale"
    blocked = PublicFeedResponse(
        url=api_url,
        status_code=403,
        headers={"content-type": "text/html"},
        content=b"Request blocked",
    )
    html = PublicFeedResponse(
        url=html_url,
        status_code=200,
        headers={"content-type": "text/html"},
        content=b'''<article class="listing-card" data-address="303 Fallback Street"
            data-city="Vancouver" data-price="$1,250,000"
            data-url="https://www.realtor.ca/real-estate/3004/303-fallback-street-vancouver">
        </article>''',
    )
    calls: list[dict] = []

    def transport(request):
        calls.append(dict(request))
        return blocked if request["url"] == api_url else html

    source = _source(
        tmp_path,
        response=blocked,
        source_url=api_url,
        allowed_domains=["api2.realtor.ca", "www.realtor.ca"],
        feed_requests=[
            {"url": api_url, "method": "POST", "parser": "realtor_ca_json"},
            {"url": html_url, "parser": "public_html"},
        ],
        transport=transport,
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert [request["url"] for request in calls] == [api_url, html_url]
    assert result.records[0]["address"] == "303 Fallback Street"
    target_snapshots = [snapshot for snapshot in result.snapshots if snapshot.request_kind == "target"]
    assert [snapshot.status_code for snapshot in target_snapshots] == [403, 200]


def test_challenge_html_is_not_parsed_as_listing_data(tmp_path):
    response = PublicFeedResponse(
        url="https://www.zolo.ca/north-vancouver-real-estate/north-vancouver/houses",
        status_code=200,
        headers={"content-type": "text/html"},
        content=b"<html><title>Just a moment...</title><body>Cloudflare challenge</body></html>",
    )
    source = _source(
        tmp_path,
        response=response,
        source_name="zolo-public",
        source_url=response.url,
        parser="public_html",
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert result.records == []
    assert result.snapshots[0].status_code == 200


def test_request_cap_prevents_second_target_request(tmp_path):
    calls: list[dict] = []
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    source = _source(
        tmp_path,
        response=response,
        feed_requests=[
            {"url": response.url, "parser": "realtor_ca_json"},
            {"url": "https://www.realtor.ca/bc/burnaby/real-estate", "parser": "realtor_ca_json"},
        ],
        transport=lambda request: calls.append(request) or response,
        max_requests_per_run=1,
    )

    result = source.collect(OBSERVED_AT)

    assert len(calls) == 1
    assert result.status is SourceRunStatus.PARTIAL
    assert "request cap" in " ".join(result.warnings).lower()


def test_off_domain_redirect_is_blocked_and_snapshotted(tmp_path):
    response = PublicFeedResponse(
        url="https://evil.example/listings",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    source = _source(
        tmp_path,
        response=response,
        transport=lambda request: response,
    )

    result = source.collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.BLOCKED_POLICY
    assert result.records == []
    assert "redirect" in " ".join(result.errors).lower() or "domain" in " ".join(result.errors).lower()


def test_source_run_snapshots_are_append_only(tmp_path):
    response = PublicFeedResponse(
        url="https://www.realtor.ca/bc/vancouver/real-estate",
        status_code=200,
        headers={"content-type": "application/json"},
        content=b'{"Results": []}',
    )
    source = _source(tmp_path, response=response)
    result = source.collect(OBSERVED_AT)

    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        first_run_id = storage.save_source_run(result)
        second_run_id = storage.save_source_run(result.model_copy(update={"weekly_run_id": 2}))
        rows = storage.conn.execute(
            "SELECT source_run_id, url, request_kind, status_code, sha256, raw_path, source_version "
            "FROM source_snapshots ORDER BY id"
        ).fetchall()

    assert first_run_id != second_run_id
    assert len(rows) == 4
    target_rows = [row for row in rows if row["request_kind"] == "target"]
    robots_rows = [row for row in rows if row["request_kind"] == "robots"]
    assert len(target_rows) == 2
    assert len(robots_rows) == 2
    assert target_rows[0]["sha256"] == target_rows[1]["sha256"]
    assert target_rows[0]["raw_path"] == target_rows[1]["raw_path"] or Path(target_rows[0]["raw_path"]).exists()
    assert all(row["source_version"] == "test-public-feed-v1" for row in rows)


def test_mls_first_profile_declares_bounded_public_http_sources():
    config = load_config("config/research-mls-first.yaml")
    sources = config["acquisition"]["sources"]

    assert [item["source_role"] for item in sources] == [
        "MLS_PRIMARY",
        "REALTOR_CA_PUBLIC",
        "ZOLO_PUBLIC",
    ]
    public_sources = sources[1:]
    assert all(item["type"] == "PUBLIC_HTTP" for item in public_sources)
    assert all(item["coverage_complete"] is False for item in public_sources)
    assert all(item["respect_robots"] is True for item in public_sources)
    assert all(item["max_requests_per_run"] > 0 for item in public_sources)


def test_zolo_search_card_extracts_address_price_and_house_metrics():
    rows = parse_public_html(
        (FIXTURES / "zolo-search-page-1.html").read_bytes(),
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo-full-market",
        ZOLO_OBSERVED_AT,
    )
    assert len(rows) == 1
    assert rows[0]["address"] == "401 Test Street"
    assert rows[0]["municipality"] == "Vancouver"
    assert rows[0]["neighbourhood"] == "Killarney VE"
    assert rows[0]["asking"] == 1_425_000
    assert rows[0]["bedrooms"] == 4
    assert rows[0]["bathrooms"] == 3
    assert rows[0]["finished_sqft"] == 2100
    assert rows[0]["year_built"] == 1981
    assert rows[0]["source_url"] == "https://www.zolo.ca/vancouver-real-estate/401-test-street"


def test_zolo_detail_extracts_lot_type_tax_year_and_mls():
    row = parse_zolo_detail(
        (FIXTURES / "zolo-detail.html").read_bytes(),
        "https://www.zolo.ca/vancouver-real-estate/401-test-street",
        ZOLO_OBSERVED_AT,
    )
    assert row["address"] == "401 Test Street"
    assert row["municipality"] == "Vancouver"
    assert row["asking"] == 1_425_000
    assert row["bedrooms"] == 4
    assert row["bathrooms"] == 3
    assert row["finished_sqft"] == 2100
    assert row["property_type"] == "Single Family Residence"
    assert row["lot_area_sqft"] == 4026
    assert row["year_built"] == 1981
    assert row["mls_number"] == "R4000001"
    assert row["neighbourhood"] == "Killarney VE"
    assert row["description"] == "Detached family house on a regular lot."
    assert row["taxes"] == 6000
    assert row["source_url"] == "https://www.zolo.ca/vancouver-real-estate/401-test-street"
    assert row["feed_url"] == "https://www.zolo.ca/vancouver-real-estate/401-test-street"
    assert row["observed_at"] == ZOLO_OBSERVED_AT.isoformat()


def test_realtor_detail_extracts_explicit_lot_area_and_dimensions():
    requested_url = "https://www.realtor.ca/real-estate/4000001/101-detail-street-vancouver"
    html = b"""
    <html><body>
      <h1>101 Detail Street</h1>
      <div class="city">Vancouver</div>
      <div class="price">$1,600,000</div>
      <div class="lot-area">4,026 sqft</div>
      <div class="lot-dimensions">33 x 122 ft</div>
      <div class="year-built">1950</div>
      <div class="property-type">Single Family</div>
      <div class="taxes">$6,000</div>
    </body></html>
    """

    row = parse_realtor_ca_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["address"] == "101 Detail Street"
    assert row["municipality"] == "Vancouver"
    assert row["asking"] == 1_600_000
    assert row["lot_area_sqft"] == 4_026
    assert row["lot_area_source"] == "EXPLICIT_DETAIL"
    assert row["lot_dimensions"] == "33 x 122 ft"
    assert row["frontage_ft"] == 33
    assert row["depth_ft"] == 122
    assert row["year_built"] == 1950
    assert row["taxes"] == 6_000


def test_realtor_detail_extracts_explicit_price_and_sale_history():
    requested_url = "https://www.realtor.ca/real-estate/4000003/103-history-street-vancouver"
    html = b"""
    <html><body>
      <h1 id="listingAddress">103 History Street<br>Vancouver, British Columbia</h1>
      <div id="propertyDetailsSectionContentSubCon_Price">
        <div class="propertyDetailsSectionContentValue">$1,600,000</div>
      </div>
      <div id="historyDetailSection">
        <table id="tableHistoryDetail"><tbody>
          <tr><td><span class="val">Apr 22, 2013</span></td>
              <td><span class="val">Sale Closed</span></td>
              <td><span class="val">$646,000</span></td></tr>
          <tr><td><span class="val">Jun 01, 2020</span></td>
              <td><span class="val">Listed</span></td>
              <td><span class="val">$1,300,000</span></td></tr>
        </tbody></table>
      </div>
    </body></html>
    """

    row = parse_realtor_ca_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["realtor_price_sale_history"] == [
        {"date": "2013-04-22T00:00:00+00:00", "status": "Sale Closed", "price": 646_000},
        {"date": "2020-06-01T00:00:00+00:00", "status": "Listed", "price": 1_300_000},
    ]
    assert row["realtor_history_status"] == "RECORDED"


def test_realtor_detail_repairs_implausible_lot_area_from_explicit_dimensions():
    requested_url = "https://www.realtor.ca/real-estate/4000002/102-detail-street-vancouver"
    html = b"""
    <html><body>
      <h1>102 Detail Street</h1>
      <div class="lot-area">1</div>
      <div class="lot-dimensions">33 x 122 ft</div>
    </body></html>
    """

    row = parse_realtor_ca_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["lot_area_sqft"] == 4_026
    assert row["lot_area_source"] == "CALCULATED_FROM_DIMENSIONS_CONFLICT_REPAIRED"
    assert row["lot_area_original_sqft"] == 1


def test_realtor_detail_parses_native_property_summary_markup():
    requested_url = "https://www.realtor.ca/real-estate/30231625/5755-dumfries-street-vancouver"
    html = b"""
    <html><head><title>For sale: 5755 DUMFRIES STREET, Vancouver - R3161299</title></head>
    <body>
      <script id="GTMDataLayerCode">
        dataLayer.push({ property: {
          listingID: 'R3161299', propertyType: 'Single Family',
          price: '1499000.00', neighbourhood: 'Victoria-Fraserview',
          bedrooms: '4', bathrooms: '2', landSize: '4067.25 sqft',
          interiorFloorSpace: '1813 sqft', city: 'Vancouver', status: 'active'
        }});
      </script>
      <h1 id="listingAddress">5755 DUMFRIES STREET<br>Vancouver, British Columbia V5P3A7</h1>
      <div id="listingMLSNum">MLS Number: <span id="MLNumberVal">R3161299</span></div>
      <div id="propertyDetailsSectionContentSubCon_PropertyType">
        <div class="propertyDetailsSectionContentLabel">Property Type</div>
        <div class="propertyDetailsSectionContentValue">Single Family</div>
      </div>
      <div id="propertyDetailsSectionContentSubCon_SquareFootage">
        <div class="propertyDetailsSectionContentLabel">Square Footage</div>
        <div class="propertyDetailsSectionContentValue">1813 sqft</div>
      </div>
      <div id="propertyDetailsSectionContentSubCon_Title">
        <div class="propertyDetailsSectionContentLabel">Title</div>
        <div class="propertyDetailsSectionContentValue">Freehold</div>
      </div>
      <div id="propertyDetailsSectionContentSubCon_LandSize">
        <div class="propertyDetailsSectionContentLabel">Land Size</div>
        <div class="propertyDetailsSectionContentValue">4067.25 sqft</div>
      </div>
      <div id="propertyDetailsSectionContentSubCon_BuiltIn">
        <div class="propertyDetailsSectionContentLabel">Built in</div>
        <div class="propertyDetailsSectionContentValue">1957</div>
      </div>
      <div id="propertyDetailsSectionContentSubCon_AnnualPropertyTaxes">
        <div class="propertyDetailsSectionContentLabel">Annual Property Taxes</div>
        <div class="propertyDetailsSectionContentValue">$8,017.57</div>
      </div>
      <div id="propertyDescriptionCon">40.8' x 102.4' lot with a solid house.</div>
    </body></html>
    """

    row = parse_realtor_ca_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["address"] == "5755 DUMFRIES STREET"
    assert row["municipality"] == "Vancouver"
    assert row["mls_number"] == "R3161299"
    assert row["asking"] == 1_499_000
    assert row["neighbourhood"] == "Victoria-Fraserview"
    assert row["finished_sqft"] == 1813
    assert row["lot_area_sqft"] == 4067.25
    assert row["lot_area_source"] == "EXPLICIT_DETAIL"
    assert row["frontage_ft"] == 40.8
    assert row["depth_ft"] == 102.4
    assert row["year_built"] == 1957
    assert row["taxes"] == 8017.57
    assert row["ownership_type"] == "Freehold"


def test_realtor_detail_does_not_treat_garage_dimensions_as_lot_dimensions():
    requested_url = "https://www.realtor.ca/real-estate/28746188/2975-e-41st-avenue-vancouver"
    html = b"""
    <html><body>
      <script id="GTMDataLayerCode">
        dataLayer.push({ property: {
          listingID: 'R3037905', price: '1799999.00',
          bedrooms: '6', bathrooms: '2', landSize: '5871 sqft',
          interiorFloorSpace: '2294 sqft', city: 'Vancouver', status: 'active'
        }});
      </script>
      <h1 id="listingAddress">2975 E 41ST AVENUE<br>Vancouver, British Columbia V5R2X7</h1>
      <div id="propertyDetailsSectionContentSubCon_LandSize">
        <div class="propertyDetailsSectionContentValue">5871 sqft</div>
      </div>
      <div id="propertyDescriptionCon">
        This rare 53 ft wide property offers redevelopment potential.
        The massive 24' x 32' triple-car garage has lane access.
      </div>
    </body></html>
    """

    row = parse_realtor_ca_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["lot_area_sqft"] == 5871
    assert "lot_dimensions" not in row
    assert "frontage_ft" not in row
    assert "depth_ft" not in row


@pytest.mark.parametrize("neighbourhood_key", ["neighborhood", "neighbourhood"])
def test_zolo_detail_uses_jsonld_neighbourhood_fallback(neighbourhood_key):
    requested_url = "https://www.zolo.ca/vancouver-real-estate/402-json-street"
    payload = {
        "@context": "https://schema.org",
        "@type": "SingleFamilyResidence",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "402 JSON Street",
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1500000"},
        "identifier": "R4000002",
        neighbourhood_key: "Renfrew VE",
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    row = parse_zolo_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["neighbourhood"] == "Renfrew VE"
    assert row["address"] == "402 JSON Street"
    assert row["municipality"] == "Vancouver"
    assert row["asking"] == 1_500_000


def test_zolo_detail_prefers_visible_price_over_jsonld_offer():
    requested_url = "https://www.zolo.ca/vancouver-real-estate/403-price-street"
    payload = {
        "@context": "https://schema.org",
        "@type": "SingleFamilyResidence",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "403 Price Street",
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1600000"},
    }
    html = (
        "<html><head><title>403 Price Street</title></head><body>"
        "<h1>403 Price Street</h1><div class='city'>Vancouver</div>"
        "<div class='price'>$1,425,000</div>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    row = parse_zolo_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["asking"] == 1_425_000


def test_zolo_detail_prefers_visible_price_over_title_price():
    requested_url = "https://www.zolo.ca/vancouver-real-estate/406-price-street"
    html = (
        "<html><head><title>406 Price Street — For Sale @ $1,600,000</title></head>"
        "<body><h1>406 Price Street</h1><div class='city'>Vancouver</div>"
        "<div class='price'>$1,425,000</div></body></html>"
    ).encode("utf-8")

    row = parse_zolo_detail(html, requested_url, ZOLO_OBSERVED_AT)

    assert row["asking"] == 1_425_000


def test_application_json_scripts_are_not_parsed_as_jsonld():
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "404 App State Street",
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1700000"},
        "description": "Application state for MLS# R4000008.",
    }
    html = (
        "<html><body>"
        f"<script type='application/json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []


def test_zolo_detail_ignores_application_json_scripts():
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "407 App State Street",
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1900000"},
    }
    html = (
        "<html><body>"
        f"<script type='application/json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="Zolo detail page has no explicit property identity",
    ):
        parse_zolo_detail(
            html,
            "https://www.zolo.ca/vancouver-real-estate/app-state",
            ZOLO_OBSERVED_AT,
        )


@pytest.mark.parametrize(
    "jsonld_type",
    ["Article", "NewsArticle", "BlogPosting", "Blog"],
)
def test_zolo_detail_rejects_editorial_jsonld_objects(jsonld_type):
    payload = {
        "@context": "https://schema.org",
        "@type": jsonld_type,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "405 Editorial Street",
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1800000"},
        "description": "Editorial property article.",
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="Zolo detail page has no explicit property identity",
    ):
        parse_zolo_detail(
            html,
            "https://www.zolo.ca/vancouver-real-estate/editorial",
            ZOLO_OBSERVED_AT,
        )


def test_zolo_article_with_tile_overlay_link_is_not_a_listing_card():
    html = b"""
    <article class="article-card">
      <a class="tile-overlay-link" href="/vancouver-real-estate/guide">Open guide</a>
      Vancouver houses guide
    </article>
    """

    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []


def test_zolo_detail_rejects_non_visible_identity_and_market_heading():
    html = b"""
    <html><head>
      <script>401 Hidden Street MLS# R4000003 4 bed $1,600,000</script>
      <style>.street { content: '401 Hidden Street'; }</style>
    </head><body>
      <h1>Vancouver Houses for Sale</h1>
      <template>
        <div class="street">401 Hidden Street</div>
        <div>MLS# R4000003</div><div>4 bed</div><div>$1,600,000</div>
      </template>
    </body></html>
    """

    with pytest.raises(
        ValueError,
        match="Zolo detail page has no explicit property identity",
    ):
        parse_zolo_detail(
            html,
            "https://www.zolo.ca/vancouver-real-estate/guide",
            ZOLO_OBSERVED_AT,
        )


def test_address_bearing_informational_article_is_not_a_listing_row():
    html = b"""
    <article class="article-card">
      <div class="address">401 Guide Street</div>
      <div class="price">$1,425,000</div>
      Vancouver houses guide
    </article>
    """

    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []


@pytest.mark.parametrize(
    "jsonld_type",
    ["Article", "NewsArticle", "BlogPosting", "Blog"],
)
def test_editorial_jsonld_objects_are_not_listing_rows(jsonld_type):
    payload = {
        "@context": "https://schema.org",
        "@type": jsonld_type,
        "headline": "Vancouver houses guide",
        "description": "Editorial discussion of MLS®# R4000010 listings.",
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []


@pytest.mark.parametrize(
    "address_markup",
    [
        "<h1>Vancouver</h1>",
        "<h1>123 456</h1>",
        "<div class='street'>Vancouver</div>",
        "<div class='street'>123 456</div>",
    ],
)
def test_zolo_detail_rejects_bogus_visible_address(address_markup):
    html = f"<html><body>{address_markup}</body></html>".encode("utf-8")

    with pytest.raises(
        ValueError,
        match="Zolo detail page has no explicit property identity",
    ):
        parse_zolo_detail(
            html,
            "https://www.zolo.ca/vancouver-real-estate/guide",
            ZOLO_OBSERVED_AT,
        )


@pytest.mark.parametrize("street_address", ["Vancouver", "123 456"])
def test_zolo_detail_rejects_bogus_jsonld_address(street_address):
    payload = {
        "@context": "https://schema.org",
        "@type": "SingleFamilyResidence",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": street_address,
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "1500000"},
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    with pytest.raises(
        ValueError,
        match="Zolo detail page has no explicit property identity",
    ):
        parse_zolo_detail(
            html,
            "https://www.zolo.ca/vancouver-real-estate/guide",
            ZOLO_OBSERVED_AT,
        )


def test_zolo_detail_preserves_explicit_mls_without_bogus_address():
    row = parse_zolo_detail(
        b"<html><body><div>MLS# R4000006</div></body></html>",
        "https://www.zolo.ca/vancouver-real-estate/without-address",
        ZOLO_OBSERVED_AT,
    )

    assert row["mls_number"] == "R4000006"
    assert "address" not in row


@pytest.mark.parametrize("street_address", ["Vancouver", "123 456"])
def test_zolo_search_card_rejects_bogus_address(street_address):
    html = f"""
    <article class="card-listing">
      <div class="card-listing--location">
        <span class="street">{street_address}</span>
        <span class="city">Vancouver</span>
      </div>
      <ul class="card-listing--values">
        <li class="price">$1,425,000</li><li>4 bed</li>
      </ul>
    </article>
    """.encode("utf-8")

    with pytest.raises(ValueError, match="listing card has no explicit address"):
        parse_public_html(
            html,
            "https://www.zolo.ca/vancouver-real-estate/houses",
            "zolo",
            ZOLO_OBSERVED_AT,
        )


def test_zolo_search_card_without_price_omits_asking():
    html = b"""
    <article class="card-listing">
      <a class="tile-overlay-link" href="/vancouver-real-estate/409-no-price-street">Open</a>
      <div class="card-listing--location">
        <span class="street">409 No Price Street</span>
        <span class="city">Vancouver</span>
      </div>
      <ul class="card-listing--values">
        <li>4 bed</li><li>3 bath</li><li>2,100 sqft</li>
      </ul>
    </article>
    """

    rows = parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    )

    assert len(rows) == 1
    assert "asking" not in rows[0]


def test_zolo_detail_excludes_lot_value_node_from_finished_sqft_fallback():
    html = b"""
    <html><head><title>410 Lot First Street</title></head><body>
      <h1>410 Lot First Street</h1><div class="city">Vancouver</div>
      <div>Lot Size</div><div>4,026 sqft</div>
      <div>Floor Area</div><div>2,100 sqft</div>
    </body></html>
    """

    row = parse_zolo_detail(
        html,
        "https://www.zolo.ca/vancouver-real-estate/410-lot-first-street",
        ZOLO_OBSERVED_AT,
    )

    assert row["lot_area_sqft"] == 4026
    assert row["finished_sqft"] == 2100


@pytest.mark.parametrize("street_address", ["Vancouver", "123 456"])
def test_jsonld_search_row_rejects_bogus_address(street_address):
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": street_address,
            "addressLocality": "Vancouver",
        },
        "offers": {"price": "2000000"},
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []


def test_jsonld_search_preserves_mls_only_identity_without_address():
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "identifier": "R4000013",
        "name": "Listing MLS R4000013",
    }
    html = (
        "<html><body>"
        f"<script type='application/ld+json'>{json.dumps(payload)}</script>"
        "</body></html>"
    ).encode("utf-8")

    rows = parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    )

    assert rows[0]["mls_number"] == "R4000013"
    assert "address" not in rows[0]


def test_zolo_information_article_is_not_a_listing_card():
    html = b"<article class='article-card'>Vancouver houses guide</article>"
    assert parse_public_html(
        html,
        "https://www.zolo.ca/vancouver-real-estate/houses",
        "zolo",
        ZOLO_OBSERVED_AT,
    ) == []
