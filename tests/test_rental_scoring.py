from northshore_watchdog.rentals.models import RentalListing
from northshore_watchdog.rentals.scoring import score_rental_listing


BASE_CONFIG = {
    "allowed_domains": ["realtor.ca", "zolo.ca"],
    "budget": {"target_min": 2500, "target_max": 3000, "stretch_max": 3300},
    "transit": {"strong_minutes": 10, "acceptable_minutes": 15},
    "regions": {
        "preferred": ["Richmond City Centre", "Brighouse", "Lansdowne", "Aberdeen", "West Cambie"],
        "acceptable": ["Marine Gateway", "Cambie", "Oakridge", "Metrotown", "Edmonds"],
        "opportunity": ["Downtown", "Yaletown", "Olympic Village"],
    },
}


def test_richmond_two_bed_two_bath_budget_near_skytrain_scores_a():
    listing = RentalListing(
        source="snapshot",
        source_id="r1",
        url="https://www.realtor.ca/real-estate/123/richmond-rental",
        address="8080 Lansdowne Road",
        city="Richmond",
        neighbourhood="Lansdowne",
        monthly_rent=2900,
        bedrooms=2,
        bathrooms=2,
        property_type="Condo",
        transit_minutes=6,
        amenities=["gym", "pool", "elevator", "in-suite laundry"],
        description="Bright 2 bed 2 bath condo near Lansdowne SkyTrain, T&T, restaurants and pharmacy.",
    )

    scored = score_rental_listing(listing, BASE_CONFIG)

    assert scored.grade == "A"
    assert scored.score_total >= 78
    assert scored.alert_level == "urgent"
    assert "中文生活便利强" in scored.reasons
    assert "pool/gym等楼宇配套" in scored.reasons


def test_vancouver_stretch_budget_scores_b_not_a():
    listing = RentalListing(
        source="snapshot",
        source_id="v1",
        url="https://www.zolo.ca/vancouver-real-estate/123-main-street",
        address="123 Main Street",
        city="Vancouver",
        neighbourhood="Yaletown",
        monthly_rent=3250,
        bedrooms=2,
        bathrooms=1,
        property_type="Apartment",
        transit_minutes=8,
        amenities=["elevator", "gym"],
        description="2 bedroom apartment near SkyTrain and grocery.",
    )

    scored = score_rental_listing(listing, BASE_CONFIG)

    assert scored.grade == "B"
    assert "stretch_budget" in scored.risks


def test_basement_shared_or_short_term_is_excluded():
    listing = RentalListing(
        source="snapshot",
        url="https://www.realtor.ca/real-estate/456/basement",
        address="1 Basement Lane",
        city="Richmond",
        monthly_rent=2600,
        bedrooms=2,
        bathrooms=1,
        property_type="Basement suite",
        description="Short term shared basement accommodation.",
    )

    scored = score_rental_listing(listing, BASE_CONFIG)

    assert scored.excluded is True
    assert scored.grade == "Excluded"
    assert {"basement", "shared_accommodation", "short_term"}.issubset(set(scored.exclusion_reasons))


def test_non_realtor_or_zolo_domain_is_excluded():
    listing = RentalListing(
        source="snapshot",
        url="https://example.com/rental/123",
        address="1 Unknown Road",
        city="Richmond",
        monthly_rent=2800,
        bedrooms=2,
        bathrooms=2,
        property_type="Condo",
    )

    scored = score_rental_listing(listing, BASE_CONFIG)

    assert scored.grade == "Excluded"
    assert scored.exclusion_reasons == ["source_domain_not_allowed:example.com"]
