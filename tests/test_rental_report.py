from northshore_watchdog.rentals.models import RentalListing
from northshore_watchdog.rentals.report import build_rental_markdown_report
from northshore_watchdog.rentals.scoring import score_rental_listing


CONFIG = {
    "allowed_domains": ["realtor.ca", "zolo.ca"],
    "budget": {"target_min": 2500, "target_max": 3000, "stretch_max": 3300},
    "regions": {"preferred": ["Lansdowne"], "acceptable": ["Marine Gateway"], "opportunity": ["Yaletown"]},
}


def test_rental_report_is_chinese_and_contains_core_sections():
    listing = RentalListing(
        source="snapshot",
        url="https://www.realtor.ca/real-estate/123/richmond-rental",
        address="8080 Lansdowne Road",
        city="Richmond",
        neighbourhood="Lansdowne",
        monthly_rent=2900,
        bedrooms=2,
        bathrooms=2,
        property_type="Condo",
        amenities=["gym", "pool", "elevator"],
        transit_minutes=6,
        description="Near SkyTrain and T&T.",
    )
    scored = score_rental_listing(listing, CONFIG)

    markdown = build_rental_markdown_report([scored], CONFIG)

    assert markdown.startswith("# 大温租房雷达 - 周报")
    assert "## 总结" in markdown
    assert "## A类候选" in markdown
    assert "8080 Lansdowne Road" in markdown
    assert "月租: $2,900" in markdown
    assert "pool/gym等楼宇配套" in markdown
    assert "下一步" in markdown


def test_rental_report_does_not_repeat_a_candidate_for_minor_risks():
    listing = RentalListing(
        source="snapshot",
        url="https://www.realtor.ca/real-estate/123/richmond-rental",
        address="8080 Lansdowne Road",
        city="Richmond",
        neighbourhood="Lansdowne",
        monthly_rent=2900,
        bedrooms=2,
        bathrooms=2,
        property_type="Condo",
        description="Near T&T.",
    )
    scored = score_rental_listing(listing, CONFIG)

    markdown = build_rental_markdown_report([scored], CONFIG)
    risk_section = markdown.split("## 风险/排除", 1)[1]

    assert "8080 Lansdowne Road" not in risk_section
