from northshore_watchdog.models import Listing
from northshore_watchdog.notifiers.feishu_webhook_notifier import build_feishu_card_payload
from northshore_watchdog.scoring import score_listing
from northshore_watchdog.config import load_config


def test_feishu_card_payload_contains_top_candidate_fields():
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

    payload = build_feishu_card_payload("Weekly Report", [scored], "outputs/weekly_report.md")

    assert payload["msg_type"] == "interactive"
    assert "1000 Sample Ambleside Avenue" in str(payload)
    assert "R3000001" in str(payload)
    assert "outputs/weekly_report.md" in str(payload)
