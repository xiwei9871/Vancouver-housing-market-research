from northshore_watchdog.config import load_config
from northshore_watchdog.models import Listing
from northshore_watchdog.report import build_markdown_report
from northshore_watchdog.scoring import score_listing


def test_markdown_report_contains_required_sections_and_listing_details():
    config = load_config("config/default.yaml")
    scored = score_listing(Listing(
        source="sample",
        source_id="ambleside-template",
        mls_number="R3000001",
        url="https://example.com/ambleside-template",
        address="1000 Sample Ambleside Avenue",
        city="West Vancouver",
        neighbourhood="Ambleside",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_700_000,
        bedrooms=4,
        bathrooms=3,
        house_size_sqft=2300,
        lot_size_sqft=7500,
        frontage_ft=60,
        year_built=1981,
        description="Original condition regular flat lot.",
        assessment_total=2_615_000,
        assessment_land=2_100_000,
        assessment_building=515_000,
    ), config)

    markdown = build_markdown_report([scored], config)

    assert "# North Shore Property Watchdog - Weekly Report" in markdown
    assert "## Summary" in markdown
    assert "## Top A Candidates" in markdown
    assert "1000 Sample Ambleside Avenue" in markdown
    assert "MLS® Num: R3000001" in markdown


def test_price_level_uses_config_watch_budget():
    config = load_config("config/real.yaml")
    scored = score_listing(Listing(
        source="realtor",
        source_id="burkeridge",
        mls_number="R3115967",
        url="https://www.realtor.ca/real-estate/29661583/4110-burkeridge-place-west-vancouver",
        address="4110 Burkeridge Place",
        city="West Vancouver",
        neighbourhood="Bayridge",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_799_000,
        bedrooms=3,
        bathrooms=2,
        house_size_sqft=2095,
        lot_size_sqft=16705,
        frontage_ft=115,
        year_built=1962,
        description="Oversized lot with water views if rebuilt.",
    ), config)

    markdown = build_markdown_report([scored], config)

    assert "Price: $1,799,000 (观察候选)" in markdown
    assert "Manual Review Checklist" in markdown
