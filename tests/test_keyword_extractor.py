from northshore_watchdog.enrichers.keyword_extractor import extract_keywords
from northshore_watchdog.models import Listing


def test_risk_keyword_ignore_phrases_avoid_nearby_trail_false_positive():
    listing = Listing(
        source="realtor",
        source_id="532-w-23",
        address="532 W 23 Street",
        city="North Vancouver",
        description="Close to schools, shopping, Mosquito Creek trails, and recreation.",
    )

    risks, _ = extract_keywords(listing, {
        "risk_keywords": ["creek"],
        "opportunity_keywords": [],
        "risk_keyword_ignore_phrases": ["Mosquito Creek trails"],
    })

    assert risks == []
