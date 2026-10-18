from pathlib import Path

from northshore_watchdog.collectors.email_alert_collector import EmailAlertCollector
from northshore_watchdog.collectors.official_feed_stub_collector import OfficialFeedStubCollector
from northshore_watchdog.collectors.search_page_snapshot_collector import SearchPageSnapshotCollector


def test_official_feed_stub_reads_sample_json():
    listings = OfficialFeedStubCollector({"input_path": "data/samples/official_feed_sample.json"}).collect()

    assert any(item.address == "1000 Sample Ambleside Avenue" for item in listings)
    assert next(item for item in listings if item.address == "1379 Haywood Avenue").listing_status == "sold"
    assert all(item.source == "official_feed_stub" for item in listings)


def test_email_alert_collector_extracts_listing_basics():
    listings = EmailAlertCollector({"input_dir": "data/samples"}).collect()

    haywood = next(item for item in listings if "Haywood" in item.address)
    assert haywood.city == "West Vancouver"
    assert haywood.list_price == 1_700_000
    assert haywood.url.startswith("https://example.com/")


def test_search_snapshot_collector_extracts_cards():
    listings = SearchPageSnapshotCollector({"input_dir": "data/samples"}).collect()

    assert {listing.address for listing in listings} >= {
        "1379 Haywood Avenue",
        "321 Queensbury Avenue",
    }


def test_search_snapshot_collector_extracts_mls_number_from_url():
    listings = SearchPageSnapshotCollector({"input_dir": "data/samples"}).collect()

    assert listings


def test_search_snapshot_collector_preserves_snapshot_provenance(tmp_path):
    snapshot = tmp_path / "fresh.html"
    snapshot.write_text(
        """
        <article class="listing-card" data-address="10 Trusted Road" data-city="North Vancouver"
          data-url="https://www.realtor.ca/real-estate/123/10-trusted-road"
          data-price="$1,399,000"></article>
        """,
        encoding="utf-8",
    )

    listings = SearchPageSnapshotCollector({
        "selected_files": [str(snapshot)],
        "allowed_domains": ["realtor.ca"],
    }).collect()

    assert listings[0].last_seen_date is not None
    assert listings[0].raw_data["snapshot_path"] == str(snapshot)
    assert listings[0].raw_data["snapshot_observed_at"] == listings[0].last_seen_date.isoformat()
    assert listings[0].raw_data["snapshot_sha256"]


def test_search_snapshot_collector_rejects_untrusted_domains_when_configured(tmp_path):
    snapshot = tmp_path / "idx.html"
    snapshot.write_text(
        """
        <article class="listing-card" data-address="4370 Mountain Highway" data-city="North Vancouver" data-url="https://petero.ca/listing.r3052423-test" data-price="$1,399,000"></article>
        <article class="listing-card" data-address="10 Trusted Road" data-city="North Vancouver" data-url="https://www.realtor.ca/real-estate/123456/10-trusted-road" data-price="$1,399,000"></article>
        """,
        encoding="utf-8",
    )

    listings = SearchPageSnapshotCollector({
        "input_dir": str(tmp_path),
        "allowed_domains": ["realtor.ca", "zolo.ca"],
    }).collect()

    assert [listing.address for listing in listings] == ["10 Trusted Road"]
