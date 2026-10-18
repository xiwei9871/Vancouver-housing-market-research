from northshore_watchdog.research.screening import (
    calculate_road_discount,
    calculate_road_quality,
    screen_discovery_candidates,
)
from northshore_watchdog.research.discovery import DiscoveryCard


def test_direct_arterial_with_bus_and_truck_uses_8_to_12_percent_prior():
    profile = calculate_road_discount(
        road_class="B",
        distance_m=34,
        property_fronts_major_road=True,
        road_name="Nanaimo Street",
        description="arterial bus and truck route",
    )

    assert profile["category"] == "ARTERIAL_BUS_TRUCK"
    assert profile["low_pct"] == 0.08
    assert profile["base_pct"] == 0.10
    assert profile["high_pct"] == 0.12
    assert profile["exposure_factor"] == 1.0


def test_direct_truck_route_at_noisy_intersection_uses_10_to_15_percent_prior():
    profile = calculate_road_discount(
        road_class="B",
        distance_m=0,
        property_fronts_major_road=True,
        description="truck route, high noise, major intersection",
    )

    assert profile["category"] == "ARTERIAL_TRUCK_INTERSECTION"
    assert profile["low_pct"] == 0.10
    assert profile["base_pct"] == 0.125
    assert profile["high_pct"] == 0.15


def test_collector_uses_2_to_4_percent_prior():
    profile = calculate_road_discount(
        road_class="D",
        distance_m=20,
        property_fronts_major_road=True,
    )

    assert profile["category"] == "COLLECTOR"
    assert profile["low_pct"] == 0.02
    assert profile["base_pct"] == 0.03
    assert profile["high_pct"] == 0.04


def test_far_arterial_does_not_create_a_direct_frontage_discount():
    profile = calculate_road_discount(
        road_class="B",
        distance_m=500,
        property_fronts_major_road=False,
    )

    assert profile["category"] == "BACKGROUND_ROAD"
    assert profile["base_pct"] == 0.0
    assert profile["exposure_factor"] == 0.0


def test_quiet_residential_street_has_no_discount():
    profile = calculate_road_discount(
        road_class="E",
        distance_m=12,
        property_fronts_major_road=False,
    )

    assert profile["category"] == "QUIET_RESIDENTIAL"
    assert profile["low_pct"] == 0.0
    assert profile["base_pct"] == 0.0
    assert profile["high_pct"] == 0.0


def test_road_quality_is_a_six_point_micro_location_subscore():
    profile = calculate_road_quality(
        road_class="B",
        road_category="HIGH_FLOW_ARTERIAL",
        distance_m=29,
        property_fronts_major_road=True,
    )

    assert profile["direct_road_quality_score"] == 4.0
    assert profile["exposure_factor"] == 1.0
    assert profile["effective_road_quality_score"] == 4.0
    assert profile["micro_location_road_score"] == 2.4
    assert profile["micro_location_road_penalty"] == -3.6


def test_non_fronting_arterial_distance_uses_category_specific_decay():
    profile = calculate_road_quality(
        road_class="C",
        road_category="ORDINARY_ARTERIAL",
        distance_m=125,
        property_fronts_major_road=False,
    )

    assert profile["exposure_factor"] == 0.25
    assert profile["effective_road_quality_score"] == 8.875
    assert profile["micro_location_road_score"] == 5.32


def test_road_quality_reads_versioned_policy_without_changing_default_scale():
    profile = calculate_road_quality(
        road_class="C",
        road_category="ORDINARY_ARTERIAL",
        distance_m=125,
        property_fronts_major_road=False,
        policy={
            "road_points": 6,
            "direct_quality_by_category": {"ORDINARY_ARTERIAL": 6.0},
            "exposure_bands_m": {"ordinary_arterial": {50: 0.5, 125: 0.2}},
        },
    )

    assert profile["direct_road_quality_score"] == 6.0
    assert profile["exposure_factor"] == 0.2
    assert profile["effective_road_quality_score"] == 9.2
    assert profile["micro_location_road_score"] == 5.52


def test_major_road_class_without_high_flow_marker_defaults_to_ordinary_arterial():
    profile = calculate_road_discount(
        road_class="B",
        distance_m=29,
        property_fronts_major_road=True,
        road_name="3900 W 10TH AV",
    )

    assert profile["category"] == "ORDINARY_ARTERIAL"
    assert profile["base_pct"] == 0.065


def test_screening_uses_road_adjusted_local_standard_value_for_price_signal():
    card = DiscoveryCard(
        address="4561 Nanaimo Street",
        municipality="Vancouver",
        neighbourhood="Victoria",
        asking=1_500_000,
        bedrooms=5,
        bathrooms=2,
        finished_sqft=2_205,
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        property_type="Single Family",
        ownership_type="Freehold",
        listing_status="Active",
        mls_number="R3158658",
        raw={
            "official_road_class": "B",
            "official_distance_to_major_road_m": 34,
            "official_property_fronts_major_road": True,
            "official_assessment_total": 1_701_500,
            "official_assessment_land": 1_673_000,
            "official_assessment_improvement": 28_500,
        },
    )
    config = {
        "screening_v2": {
            "allowed_municipalities": ["Vancouver"],
            "stage_a": {"price_gate_enabled": False, "hard_lot_min_sqft": 2800},
            "location_residual_model": {
                "enabled": True,
                "neutral_standard_value": 2_064_000,
                "score_anchor": 50,
                "residuals": {
                    "Victoria-Fraserview": {
                        "residual_cad": -300_000,
                        "standard_property_value": 1_764_000,
                        "confidence": "MEDIUM",
                    }
                },
                "segment_aliases": {"Victoria": "Victoria-Fraserview"},
            },
            "neighbourhood_reference_prices": {"Victoria": 1_764_000},
            "road_discount_prior": {"known_bus_truck_roads": ["nanaimo street"]},
        }
    }

    row = screen_discovery_candidates([card], config).stage_b_candidates[0]

    assert row.road_discount_category == "ARTERIAL_BUS_TRUCK"
    assert row.road_discount_pct == 0.10
    assert row.road_discount_basis_value == 1_764_000
    assert row.road_discount_cad == 176_400
    assert row.road_adjusted_standard_property_value == 1_587_600
    assert row.area_relative_price_ratio == round(1_500_000 / 1_587_600, 4)
