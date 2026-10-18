from northshore_watchdog.config import load_config
from northshore_watchdog.models import Listing
from northshore_watchdog.scoring import score_listing


def test_haywood_template_scores_as_a_candidate():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="ambleside-template",
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
        zoning="RS",
        description="Original condition regular flat lot with suite potential and separate entrance.",
        assessment_total=2_615_000,
        assessment_land=2_100_000,
        assessment_building=515_000,
    )

    scored = score_listing(listing, config)

    assert scored.grade == "A"
    assert scored.score_total >= 78
    assert scored.alert_level == "urgent"
    assert not scored.excluded


def test_strata_and_leasehold_are_excluded():
    config = load_config("config/default.yaml")
    strata = Listing(
        source="sample",
        source_id="strata",
        url="https://example.com/strata",
        address="101-123 Marine Drive",
        city="West Vancouver",
        property_type="townhouse",
        tenure="strata",
        list_price=1_100_000,
    )
    leasehold = Listing(
        source="sample",
        source_id="leasehold",
        url="https://example.com/leasehold",
        address="55 Leasehold Lane",
        city="North Vancouver",
        property_type="detached house",
        tenure="leasehold",
        list_price=1_250_000,
    )

    assert score_listing(strata, config).excluded
    assert score_listing(leasehold, config).excluded


def test_sold_listing_is_excluded_even_if_other_fields_are_strong():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="haywood-sold",
        url="https://example.com/haywood",
        address="1379 Haywood Avenue",
        city="West Vancouver",
        neighbourhood="Ambleside",
        property_type="detached house",
        tenure="freehold",
        listing_status="sold",
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
    )

    scored = score_listing(listing, config)

    assert scored.excluded
    assert "listing_status_excluded:sold" in scored.exclusion_reasons


def test_missing_assessment_is_neutral_but_not_high_confidence():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="nv-good",
        url="https://example.com/nv-good",
        address="321 Queensbury Avenue",
        city="North Vancouver",
        neighbourhood="Queensbury",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_450_000,
        bedrooms=3,
        bathrooms=2,
        house_size_sqft=1700,
        lot_size_sqft=5600,
        frontage_ft=45,
        year_built=1984,
        description="Regular flat lot, original condition, vacant.",
    )

    scored = score_listing(listing, config)

    assert scored.score_breakdown["assessment_discount"] == 10
    assert "assessment_missing" in scored.risks
    assert scored.confidence in {"medium", "low"}


def test_pre_1965_manual_review_listing_can_remain_b_candidate():
    config = load_config("config/default.yaml")
    listing = Listing(
        source="sample",
        source_id="inglewood",
        url="https://example.com/inglewood",
        address="1531 Inglewood Avenue",
        city="West Vancouver",
        neighbourhood="Ambleside",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_689_000,
        bedrooms=3,
        bathrooms=2,
        house_size_sqft=1850,
        lot_size_sqft=6100,
        frontage_ft=50,
        year_built=1945,
        zoning="RS",
        description="Older freehold house with renovate or rebuild potential. Estate sale. Manual review.",
        assessment_total=2_250_000,
        assessment_land=2_050_000,
        assessment_building=200_000,
    )

    scored = score_listing(listing, config)

    assert not scored.excluded
    assert scored.grade == "Review"


def test_real_config_missing_assessment_can_enter_b():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="zolo",
        source_id="bendale",
        mls_number="R3126455",
        url="https://www.zolo.ca/north-vancouver-real-estate/2553-bendale-road",
        address="2553 Bendale Road",
        city="North Vancouver",
        neighbourhood="Blueridge NV",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_499_900,
        bedrooms=3,
        bathrooms=2,
        house_size_sqft=2040,
        lot_size_sqft=7200,
        year_built=1972,
        description="Single Family Residence in Blueridge NV. Lot size 7200 sqft. Built 1972.",
    )

    scored = score_listing(listing, config)

    assert scored.grade == "B"
    assert "assessment_missing" in scored.risks


def test_real_config_pre_1965_listing_becomes_review_not_excluded():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="zolo",
        source_id="coleman",
        mls_number="R3131292",
        url="https://www.zolo.ca/north-vancouver-real-estate/1380-coleman-street",
        address="1380 Coleman Street",
        city="North Vancouver",
        neighbourhood="Lynn Valley",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_475_000,
        bedrooms=4,
        bathrooms=2,
        house_size_sqft=1816,
        lot_size_sqft=8728,
        year_built=1956,
        description="Possible suite potential on lower level with private side entry. Livable condition.",
    )

    scored = score_listing(listing, config)

    assert not scored.excluded
    assert scored.grade == "Review"
    assert "review_older_home" in scored.risks


def test_nearby_mosquito_creek_trails_does_not_create_land_creek_risk():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="realtor",
        source_id="532-w-23",
        mls_number="R3114561",
        url="https://www.realtor.ca/real-estate/29641477/532-w-23-street-north-vancouver",
        address="532 W 23 Street",
        city="North Vancouver",
        neighbourhood="Central Lonsdale",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_598_000,
        bedrooms=3,
        bathrooms=3,
        house_size_sqft=2133,
        lot_size_sqft=6750,
        frontage_ft=50,
        year_built=1962,
        description="Close to Mosquito Creek trails and Central Lonsdale amenities.",
    )

    scored = score_listing(listing, config)

    assert "creek" not in scored.risks


def test_real_config_north_van_near_miss_price_enters_review_not_excluded():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="realtor",
        source_id="hendecourt-like",
        mls_number="R3066104",
        url="https://www.realtor.ca/example/hendecourt-like",
        address="1006 Sample Hendecourt Road",
        city="North Vancouver",
        neighbourhood="Lynn Valley",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_788_000,
        bedrooms=5,
        bathrooms=3,
        house_size_sqft=2646,
        lot_size_sqft=7980,
        frontage_ft=60,
        year_built=1976,
        zoning="RS",
        description="Regular flat lot with basement suite potential and separate entrance.",
        assessment_total=2_350_000,
        assessment_land=1_950_000,
        assessment_building=400_000,
    )

    scored = score_listing(listing, config)

    assert not scored.excluded
    assert scored.grade in {"B", "Review"}
    assert "near_miss_price_review" in scored.risks


def test_real_config_north_van_above_near_miss_price_is_excluded():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="realtor",
        source_id="too-expensive",
        url="https://www.realtor.ca/example/too-expensive",
        address="999 Sample Road",
        city="North Vancouver",
        neighbourhood="Lynn Valley",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_825_000,
        bedrooms=5,
        bathrooms=3,
        house_size_sqft=2600,
        lot_size_sqft=7200,
        frontage_ft=55,
        year_built=1976,
        description="Regular flat lot.",
        assessment_total=2_400_000,
    )

    scored = score_listing(listing, config)

    assert scored.excluded
    assert "price_above_near_miss_review_max:1825000" in scored.exclusion_reasons


def test_real_config_west_van_lifestyle_region_is_capped_at_review():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="realtor",
        source_id="skilift-like",
        mls_number="R3050166",
        url="https://www.realtor.ca/example/skilift-like",
        address="2735 Sample Skilift Place",
        city="West Vancouver",
        neighbourhood="Chelsea Park",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_800_000,
        bedrooms=4,
        bathrooms=3,
        house_size_sqft=2821,
        lot_size_sqft=12888,
        frontage_ft=80,
        year_built=1981,
        zoning="RS",
        description="Regular flat lot with suite potential and separate entrance.",
        assessment_total=2_700_000,
        assessment_land=2_300_000,
        assessment_building=400_000,
    )

    scored = score_listing(listing, config)

    assert scored.grade == "Review"
    assert any(risk.startswith("downgraded_region") for risk in scored.risks)


def test_real_config_excludes_untrusted_listing_domain_from_report_scoring():
    config = load_config("config/real.yaml")
    listing = Listing(
        source="petero",
        source_id="petero-stale",
        mls_number="R3052423",
        url="https://petero.ca/north-vancouver-detached-homes/listing.r3052423",
        address="4370 Mountain Highway",
        city="North Vancouver",
        neighbourhood="Lynn Valley",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_399_000,
        bedrooms=4,
        bathrooms=2,
        house_size_sqft=1281,
        lot_size_sqft=5700,
        frontage_ft=50,
        description="Awaiting your ideas.",
    )

    scored = score_listing(listing, config)

    assert scored.excluded
    assert "source_domain_not_allowed:petero.ca" in scored.exclusion_reasons
