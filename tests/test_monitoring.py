import os
from datetime import datetime, timezone

from northshore_watchdog.models import Listing
from northshore_watchdog.normalize import listing_dedupe_key
from northshore_watchdog.monitoring import inspect_input_freshness
from northshore_watchdog.rentals.models import RentalListing
from northshore_watchdog.rentals.storage import rental_dedupe_key


CHECKED_AT = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _set_mtime(path, observed_at: datetime) -> None:
    timestamp = observed_at.timestamp()
    os.utime(path, (timestamp, timestamp))


def test_required_directories_are_stale_when_no_file_is_within_seven_days(tmp_path):
    stale = tmp_path / "stale.html"
    stale.write_text("<html />", encoding="utf-8")
    _set_mtime(stale, datetime(2026, 8, 22, 12, tzinfo=timezone.utc))

    result = inspect_input_freshness(
        {
            "monitoring": {
                "max_age_days": 7,
                "required_collectors": ["search_page_snapshot"],
            },
            "collectors": {
                "search_page_snapshot": {
                    "enabled": True,
                    "input_dirs": [str(tmp_path)],
                },
            },
        },
        now=CHECKED_AT,
    )

    assert result.status == "stale"
    assert result.stale_directories == [str(tmp_path)]
    assert result.selected_files["search_page_snapshot"] == []


def test_freshness_selects_only_recent_supported_files_and_hashes_content(tmp_path):
    fresh = tmp_path / "fresh.html"
    ignored = tmp_path / "notes.pdf"
    fresh.write_text("same content", encoding="utf-8")
    ignored.write_text("not a listing input", encoding="utf-8")
    _set_mtime(fresh, datetime(2026, 8, 29, 12, tzinfo=timezone.utc))

    result = inspect_input_freshness(
        {
            "monitoring": {
                "max_age_days": 7,
                "required_collectors": ["search_page_snapshot"],
            },
            "collectors": {
                "search_page_snapshot": {
                    "enabled": True,
                    "input_dirs": [str(tmp_path)],
                },
            },
        },
        now=CHECKED_AT,
    )

    assert result.status == "fresh"
    assert [item.path for item in result.selected_files["search_page_snapshot"]] == [str(fresh)]
    assert result.selected_files["search_page_snapshot"][0].sha256
    assert result.selected_files["search_page_snapshot"][0].age_days == 1.0


def test_each_required_directory_must_have_a_fresh_file(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    current = first / "current.html"
    old = second / "old.html"
    current.write_text("current", encoding="utf-8")
    old.write_text("old", encoding="utf-8")
    _set_mtime(current, datetime(2026, 8, 29, 12, tzinfo=timezone.utc))
    _set_mtime(old, datetime(2026, 8, 22, 12, tzinfo=timezone.utc))

    result = inspect_input_freshness(
        {
            "monitoring": {
                "max_age_days": 7,
                "required_collectors": ["search_page_snapshot"],
                "require_each_input_dir": True,
            },
            "collectors": {
                "search_page_snapshot": {
                    "enabled": True,
                    "input_dirs": [str(first), str(second)],
                },
            },
        },
        now=CHECKED_AT,
    )

    assert result.status == "stale"
    assert result.stale_directories == [str(second)]


def test_required_collector_can_accept_one_fresh_directory_when_configured(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    current = first / "current.html"
    old = second / "old.html"
    current.write_text("current", encoding="utf-8")
    old.write_text("old", encoding="utf-8")
    _set_mtime(current, datetime(2026, 8, 29, 12, tzinfo=timezone.utc))
    _set_mtime(old, datetime(2026, 8, 22, 12, tzinfo=timezone.utc))

    result = inspect_input_freshness(
        {
            "monitoring": {
                "max_age_days": 7,
                "required_collectors": ["search_page_snapshot"],
                "require_each_input_dir": False,
            },
            "collectors": {
                "search_page_snapshot": {
                    "enabled": True,
                    "input_dirs": [str(first), str(second)],
                },
            },
        },
        now=CHECKED_AT,
    )

    assert result.status == "fresh"
    assert [item.path for item in result.selected_files["search_page_snapshot"]] == [str(current)]


def test_listing_identity_is_stable_across_snapshot_names_and_prices():
    first = Listing(
        source="search_page_snapshot",
        source_id="old.html:0",
        mls_number="R3000001",
        url="https://www.realtor.ca/real-estate/1/home#details",
        address="1 Main Street",
        city="North Vancouver",
        list_price=1_500_000,
    )
    second = first.model_copy(update={"source_id": "new.html:0", "list_price": 1_425_000})

    assert listing_dedupe_key(first) == listing_dedupe_key(second)
    assert listing_dedupe_key(first) == "mls:R3000001"


def test_purchase_url_identity_ignores_fragment_and_trailing_slash():
    first = Listing(
        source="search_page_snapshot",
        source_id="old.html:0",
        url="https://WWW.Realtor.ca/real-estate/1/home/#details",
        address="1 Main Street",
        city="North Vancouver",
        list_price=1_500_000,
    )
    second = first.model_copy(
        update={"source_id": "new.html:0", "url": "https://www.realtor.ca/real-estate/1/home"}
    )

    assert listing_dedupe_key(first) == listing_dedupe_key(second)


def test_rental_identity_is_stable_across_snapshot_names_and_rents():
    first = RentalListing(
        source="rental_snapshot",
        source_id="old.html:0",
        url="https://www.zolo.ca/richmond-real-estate/1-main-street/101",
        address="101 - 1 Main Street",
        city="Richmond",
        monthly_rent=3000,
    )
    second = first.model_copy(update={"source_id": "new.html:0", "monthly_rent": 2800})

    assert rental_dedupe_key(first) == rental_dedupe_key(second)
