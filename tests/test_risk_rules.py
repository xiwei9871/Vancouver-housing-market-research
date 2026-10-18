from northshore_watchdog.models import Listing
from northshore_watchdog.risk_rules import find_exclusion_reasons
from northshore_watchdog.enrichers.keyword_extractor import extract_keywords


def test_risk_and_opportunity_keywords_are_extracted_case_insensitively():
    listing = Listing(
        source="sample",
        source_id="risk",
        url="https://example.com/risk",
        address="4670 Willow Creek Road",
        city="West Vancouver",
        description="Creek side property. Needs TLC. As is where is.",
    )

    risks, opportunities = extract_keywords(listing, {
        "risk_keywords": ["creek", "as is where is"],
        "opportunity_keywords": ["needs TLC"],
    })

    assert risks == ["creek", "as is where is"]
    assert opportunities == ["needs TLC"]


def test_hard_exclusion_catches_non_target_tenure_and_property_type():
    listing = Listing(
        source="sample",
        source_id="duplex",
        url="https://example.com/duplex",
        address="12 Example Street",
        city="North Vancouver",
        property_type="duplex",
        tenure="freehold",
        list_price=1_000_000,
    )

    reasons = find_exclusion_reasons(listing)

    assert any("property_type" in reason for reason in reasons)


def test_duplex_potential_in_description_does_not_exclude_detached_house():
    listing = Listing(
        source="real_snapshot",
        source_id="central-lonsdale",
        url="https://example.com/central-lonsdale",
        address="234 E 24th Street",
        city="North Vancouver",
        property_type="detached house",
        tenure="freehold",
        list_price=1_398_000,
        description="Allows duplexes with secondary suites, triplexes, and row homes.",
    )

    assert find_exclusion_reasons(listing) == []
