from northshore_watchdog.config import load_config
from northshore_watchdog.enrichers.region_classifier import classify_region
from northshore_watchdog.models import Listing


def test_preferred_region_detected_from_neighbourhood():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="1",
        url="https://example.com/1",
        address="1379 Haywood Avenue",
        city="West Vancouver",
        neighbourhood="Ambleside",
        list_price=1_700_000,
    )

    assert classify_region(listing, config).category == "preferred_region"


def test_slope_lifestyle_region_detected_from_address_keyword():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="2",
        url="https://example.com/2",
        address="4670 Willow Creek Road, Westmount",
        city="West Vancouver",
        list_price=1_700_000,
    )

    result = classify_region(listing, config)

    assert result.category in {"downgraded_region", "lifestyle_or_slope_region"}
    assert result.matched_keyword


def test_nearby_mosquito_creek_trails_does_not_trigger_slope_region():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="realtor",
        source_id="532-w-23",
        address="532 W 23 Street",
        city="North Vancouver",
        neighbourhood="Central Lonsdale",
        list_price=1_598_000,
        description="Close to Mosquito Creek trails and Central Lonsdale amenities.",
    )

    result = classify_region(listing, config)

    assert result.category == "preferred_region"
    assert result.matched_keyword == "Central Lonsdale"
