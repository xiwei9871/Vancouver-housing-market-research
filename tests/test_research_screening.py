from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.discovery_pipeline import DiscoveryRunResult
from northshore_watchdog.research.discovery_report import render_discovery_report
import northshore_watchdog.research.screening as screening_module
from northshore_watchdog.research.screening import screen_discovery_candidates


def _config() -> dict:
    return {
        "screening_v2": {
            "allowed_municipalities": ["Vancouver"],
            "stage_a": {
                "price_min": 1_000_000,
                "price_max": 1_800_000,
            },
            "stage_b": {
                "min_score": 30,
            },
            "final_review": {
                "min_score": 68,
            },
            "major_road_tokens": [
                "boundary road",
                "knight street",
                "kingsway",
                "nanaimo street",
                "marine drive",
                "victoria drive",
            ],
        }
    }


def _card(**values: object) -> DiscoveryCard:
    defaults = {
        "address": "100 Sample Street",
        "municipality": "Vancouver",
        "asking": 1_500_000,
        "bedrooms": 3,
        "bathrooms": 2,
        "finished_sqft": 1_700,
        "lot_area_sqft": 4_000,
        "lot_dimensions": "33 x 120 ft",
        "property_type": "Single Family",
        "ownership_type": "Freehold",
        "listing_status": "Active",
        "dom": "30 days",
        "mls_number": "R3000001",
    }
    defaults.update(values)
    return DiscoveryCard(**defaults)


def test_screening_stage_a_excludes_out_of_boundary_and_obvious_bad_rows():
    result = screen_discovery_candidates(
        [
            _card(address="1 Vancouver Street"),
            _card(address="2 Boundary Road", municipality="Burnaby"),
            _card(address="3 Lease Street", ownership_type="Leasehold"),
        ],
        _config(),
    )

    assert [candidate.card.address for candidate in result.stage_a_candidates] == [
        "1 Vancouver Street"
    ]
    assert result.stage_a_excluded_count == 2
    assert any(
        item.card.address == "2 Boundary Road" and "MUNICIPALITY_OUT_OF_SCOPE" in item.reason_codes
        for item in result.stage_a_excluded
    )


def test_v14_location_residual_is_applied_to_both_value_axes_without_school_double_counting():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_000_000,
        "score_anchor": 50,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 500_000,
                "standard_property_value": 2_500_000,
                "confidence": "LOW",
            },
            "Hastings-Sunrise": {
                "residual_cad": -300_000,
                "standard_property_value": 1_700_000,
                "confidence": "HIGH",
            },
        },
    }
    config["screening_v2"]["orthogonal_scoring"] = {
        "current_use_weights": {
            "location_residual": 35,
            "micro_location": 15,
            "family_layout": 25,
            "maintenance_systems": 25,
            "school_proxy": 0,
        },
        "redevelopment_weights": {
            "lot_geometry": 20,
            "frontage_depth_threshold": 15,
            "official_zoning_entitlement": 25,
            "product_market_fit": 10,
            "location_residual": 10,
            "lane_access_servicing": 10,
            "permanent_execution_risk": 10,
        },
    }
    west = _card(
        address="1 West Point Grey Street",
        neighbourhood="West Point Grey",
        asking=1_800_000,
        lot_dimensions="33 x 122 ft",
    )
    east = _card(
        address="2 Hastings-Sunrise Street",
        neighbourhood="Hastings-Sunrise",
        asking=1_400_000,
        lot_dimensions="33 x 122 ft",
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([west, east], config).stage_b_candidates
    }

    west_row = rows["1 West Point Grey Street"]
    east_row = rows["2 Hastings-Sunrise Street"]
    assert west_row.location_residual_cad == 500_000
    assert east_row.location_residual_cad == -300_000
    assert west_row.location_standard_property_value == 2_500_000
    assert west_row.location_residual_confidence == "LOW"
    assert west_row.current_use_score_components["location_residual"] > east_row.current_use_score_components["location_residual"]
    assert west_row.redevelopment_score_components["location_residual"] > east_row.redevelopment_score_components["location_residual"]
    assert west_row.current_use_score > east_row.current_use_score
    assert west_row.redevelopment_score > east_row.redevelopment_score
    assert west_row.area_relative_price_ratio == 0.72
    assert east_row.area_relative_price_ratio == 0.8235
    assert "LOCATION_RESIDUAL_APPLIED" in west_row.flags
    assert west_row.current_use_score_components["school_proxy"] == 0


def test_v14_location_residual_source_is_loaded_from_local_csv(tmp_path):
    source = tmp_path / "location-premium-stability-v1.4.csv"
    source.write_text(
        "row_type,analysis_variant,component,segment,value,standard_property_value,confidence\n"
        "SUMMARY,ROBUST_SUMMARY,area_location_residual,West Point Grey,500000,2500000,LOW\n",
        encoding="utf-8",
    )
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "source_path": str(source),
        "neutral_standard_value": 2_000_000,
        "score_anchor": 50,
        "segment_aliases": {"Point Grey": "West Point Grey"},
    }

    row = screen_discovery_candidates(
        [_card(neighbourhood="Point Grey")],
        config,
    ).stage_b_candidates[0]

    assert row.location_residual_cad == 500_000
    assert row.location_standard_property_value == 2_500_000
    assert row.current_use_score_components["location_residual"] > 0


def test_regional_relative_price_safety_is_based_on_each_area_standard_value():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_000_000,
        "score_anchor": 50,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 500_000,
                "standard_property_value": 2_500_000,
                "confidence": "MEDIUM",
            },
            "Hastings-Sunrise": {
                "residual_cad": -300_000,
                "standard_property_value": 1_700_000,
                "confidence": "HIGH",
            },
        },
    }
    config["screening_v2"]["orthogonal_scoring"] = {
        "current_use_weights": {
            "location_residual": 25,
            "regional_price_safety": 20,
            "micro_location": 15,
            "family_layout": 20,
            "maintenance_systems": 20,
            "school_proxy": 0,
        },
        "redevelopment_weights": {
            "lot_geometry": 20,
            "frontage_depth_threshold": 15,
            "official_zoning_entitlement": 25,
            "product_market_fit": 10,
            "regional_price_safety": 15,
            "lane_access_servicing": 10,
            "permanent_execution_risk": 5,
        },
    }
    west = _card(
        address="1 West Point Grey Street",
        neighbourhood="West Point Grey",
        asking=1_875_000,
        lot_dimensions="33 x 122 ft",
    )
    east = _card(
        address="2 Hastings-Sunrise Street",
        neighbourhood="Hastings-Sunrise",
        asking=1_400_000,
        lot_dimensions="33 x 122 ft",
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([west, east], config).stage_b_candidates
    }

    west_row = rows["1 West Point Grey Street"]
    east_row = rows["2 Hastings-Sunrise Street"]
    assert west_row.regional_standard_value == 2_500_000
    assert east_row.regional_standard_value == 1_700_000
    assert west_row.regional_price_dislocation_cad == 625_000
    assert east_row.regional_price_dislocation_cad == 300_000
    assert west_row.regional_price_dislocation_pct == 0.25
    assert east_row.regional_price_dislocation_pct == 0.1765
    assert west_row.regional_price_safety_score > east_row.regional_price_safety_score
    assert west_row.current_use_score_components["regional_price_safety"] > east_row.current_use_score_components["regional_price_safety"]
    assert west_row.redevelopment_score_components["regional_price_safety"] > east_row.redevelopment_score_components["regional_price_safety"]


def test_regional_standard_value_is_not_percentile_normalized_within_each_area():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_000_000,
        "score_anchor": 50,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 500_000,
                "standard_property_value": 2_500_000,
            },
            "Hastings-Sunrise": {
                "residual_cad": -300_000,
                "standard_property_value": 1_700_000,
            },
        },
    }
    high_west = _card(address="1 West Point Grey Street", neighbourhood="West Point Grey", asking=1_875_000)
    ordinary_east = _card(address="2 Hastings-Sunrise Street", neighbourhood="Hastings-Sunrise", asking=1_700_000)

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([high_west, ordinary_east], config).stage_b_candidates
    }

    assert rows[high_west.address].regional_price_dislocation_pct > 0
    assert rows[ordinary_east.address].regional_price_dislocation_pct == 0
    assert rows[high_west.address].regional_price_safety_score > rows[ordinary_east.address].regional_price_safety_score


def test_stage_b_keeps_small_lot_and_major_road_for_score_based_review():
    candidate = _card(
        address="486 E King Edward Avenue",
        lot_area_sqft=2_900,
        lot_dimensions="29 x 100 ft",
        bedrooms=2,
        bathrooms=1,
        finished_sqft=1_250,
        description="Direct arterial exposure, but priced for the location.",
    )

    result = screen_discovery_candidates([candidate], _config())

    assert result.stage_a_candidates
    assert result.stage_b_candidates
    screened = result.stage_b_candidates[0]
    assert "SMALL_LOT_SIGNAL" in screened.flags
    assert "MAJOR_ROAD_SIGNAL" in screened.flags
    assert screened.asset_class == "ORTHOGONAL_SCORED"
    assert screened.priority == "SCORED"
    assert screened.current_use_score is not None
    assert screened.redevelopment_score is not None


def test_class_specific_final_review_uses_threshold_without_an_arbitrary_cap():
    cards = [
        _card(
            address=f"{index} Strong Candidate Street",
            mls_number=f"R30000{index}",
            asking=1_200_000,
            bedrooms=4,
            bathrooms=3,
            finished_sqft=2_100,
            lot_area_sqft=5_000,
            lot_dimensions="50 x 120 ft",
            neighbourhood="Dunbar",
            year_built=2000,
            dom="200 days",
            description=(
                "Well-maintained updated home on a quiet residential street near a park. "
                "R1-1 zoning, lane access and duplex potential."
            ),
        )
        for index in range(1, 13)
    ]

    result = screen_discovery_candidates(cards, _config())

    assert result.final_review_candidates == []
    assert len(result.current_use_candidates) == len(cards)
    assert len(result.redevelopment_candidates) == len(cards)
    assert len(result.dual_value_candidates) == len(cards)
    assert len(result.current_use_top10) == 10
    assert len(result.redevelopment_top10) == 10
    assert len(result.dual_value_top10) == 10


def test_v23_classifies_three_asset_types_and_routes_special_transactions_first():
    result = screen_discovery_candidates(
        [
            _card(
                address="3390 Nanaimo Street",
                description=(
                    "TOA Tier 3. Up to 8 stories or 3.0 FSR. Court Order sale for "
                    "2/3 share of property. As is where is."
                ),
            ),
            _card(
                address="3466 E 2nd Avenue",
                description="Court-Ordered Sale - exceptional opportunity for builders.",
            ),
            _card(
                address="5454 Melbourne Street",
                bedrooms=3,
                bathrooms=1,
                finished_sqft=1_300,
                year_built=1948,
                lot_area_sqft=5_525,
                lot_dimensions="45 x 123 ft",
                description="As-is transit-oriented development site, up to 3.0 FAR and 8 storeys.",
            ),
            _card(
                address="100 Live Home Street",
                neighbourhood="Dunbar",
                year_built=2010,
                lot_area_sqft=3_100,
                lot_dimensions="31 x 100 ft",
                description=(
                    "Renovated move-in ready family home on a quiet residential street. "
                    "Heritage covenant prevents redevelopment and subdivision."
                ),
            ),
            _card(
                address="200 Hold Then Rebuild Street",
                neighbourhood="Arbutus",
                year_built=1955,
                lot_area_sqft=4_026,
                lot_dimensions="33 x 122 ft",
                description=(
                    "Solid well-maintained family home on a quiet residential street. "
                    "R1-1 zoning, lane access and duplex or multiplex potential."
                ),
            ),
        ],
        _config(),
    )

    decisions = {item.card.address: item for item in result.stage_a_candidates}
    assert decisions["3390 Nanaimo Street"].asset_class == "SPECIAL_TRANSACTION"
    assert decisions["3466 E 2nd Avenue"].asset_class == "SPECIAL_TRANSACTION"

    normal = {item.card.address: item for item in result.stage_b_candidates}
    assert normal["5454 Melbourne Street"].redevelopment_score >= normal["100 Live Home Street"].redevelopment_score
    assert normal["200 Hold Then Rebuild Street"].current_use_score is not None
    assert normal["200 Hold Then Rebuild Street"].redevelopment_score is not None
    assert {item.card.address for item in result.current_use_candidates} == set(normal)
    assert {item.card.address for item in result.redevelopment_candidates} == set(normal)
    assert result.special_track_count == 2

    assert all(item.classification_status == "PROVISIONAL" for item in result.stage_b_candidates)
    assert all(item.current_use_score is not None for item in result.stage_b_candidates)
    assert all(item.redevelopment_score is not None for item in result.stage_b_candidates)


def test_v23_classification_is_independent_of_asking_price():
    config = _config()
    config["screening_v2"]["neighbourhood_reference_prices"] = {"Arbutus": 1_600_000}
    base = {
        "neighbourhood": "Arbutus",
        "year_built": 1955,
        "lot_area_sqft": 4_026,
        "lot_dimensions": "33 x 122 ft",
        "description": (
            "Solid well-maintained family home on a quiet residential street. "
            "R1-1 zoning, lane access and duplex potential."
        ),
    }
    inexpensive = _card(address="1 Price Independent Street", asking=1_100_000, **base)
    expensive = _card(address="2 Price Independent Street", asking=1_790_000, **base)

    result = screen_discovery_candidates([inexpensive, expensive], config)
    decisions = {item.card.address: item for item in result.stage_b_candidates}

    left = decisions["1 Price Independent Street"]
    right = decisions["2 Price Independent Street"]
    assert left.asset_class == right.asset_class == "ORTHOGONAL_SCORED"
    assert left.current_use_index == right.current_use_index
    assert left.redevelopment_readiness_index == right.redevelopment_readiness_index
    assert left.class_score is None
    assert left.area_relative_price_signal != right.area_relative_price_signal


def test_v23_class_specific_scorecards_use_different_lot_weights():
    cards = [
        _card(
            address="1 Live Scorecard Street",
            neighbourhood="Dunbar",
            year_built=2015,
            lot_area_sqft=4_000,
            lot_dimensions="33 x 121 ft",
            description=(
                "Renovated move-in ready home on a quiet residential street. "
                "Heritage covenant prevents redevelopment and subdivision."
            ),
        ),
        _card(
            address="2 Hold Scorecard Street",
            neighbourhood="Arbutus",
            year_built=1955,
            lot_area_sqft=4_000,
            lot_dimensions="33 x 121 ft",
            description=(
                "Solid well-maintained family home on a quiet residential street. "
                "R1-1 zoning, lane access and duplex potential."
            ),
        ),
        _card(
            address="3 Development Scorecard Street",
            neighbourhood="Renfrew",
            year_built=1940,
            lot_area_sqft=4_000,
            lot_dimensions="33 x 121 ft",
            description="As-is development site in a TOA with up to 3.0 FSR and 8 storeys.",
        ),
    ]

    result = screen_discovery_candidates(cards, _config())

    rows = {item.card.address: item for item in result.stage_b_candidates}
    assert "lot_geometry" not in rows["1 Live Scorecard Street"].current_use_score_components
    assert rows["1 Live Scorecard Street"].redevelopment_score_components["lot_geometry"] > 0
    assert rows["2 Hold Scorecard Street"].current_use_score_components["area_neighbourhood_value"] > 0
    assert rows["3 Development Scorecard Street"].redevelopment_score_components["official_zoning_entitlement"] > 0


def test_v23_does_not_create_a_global_cross_class_ranking():
    result = screen_discovery_candidates(
        [
            _card(
                address="1 Live Only Street",
                year_built=2015,
                description="Fully renovated in 2024 with new roof, electrical, plumbing, kitchen and bathrooms. Quiet family home. Heritage covenant prevents redevelopment.",
            ),
            _card(
                address="2 Hold Street",
                year_built=1960,
                description="Fully renovated in 2022 with new roof, electrical, plumbing, kitchen and bathrooms. Solid quiet family home. R1-1 zoning, lane and duplex potential.",
            ),
            _card(
                address="3 Dev Street",
                year_built=1940,
                description="As-is TOA development site with 3.0 FSR and 8 storeys.",
            ),
        ],
        _config(),
    )

    assert result.final_review_candidates == []
    assert result.current_use_top10
    assert result.redevelopment_top10
    assert result.dual_value_top10


def test_v21_lot_sufficiency_caps_area_and_home_fit_prefers_quiet_family_house():
    quiet = _card(
        address="2876 Dunbar Street",
        neighbourhood="Dunbar",
        asking=1_700_000,
        lot_area_sqft=4_000,
        lot_dimensions="33 x 121 ft",
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_850,
        year_built=1955,
        description="Solid well-maintained family home on a quiet residential street near a park. R1-1 zoning, lane access and duplex potential.",
    )
    large_cheap_arterial = _card(
        address="942 Nanaimo Street",
        neighbourhood="Hastings-Sunrise",
        asking=1_200_000,
        lot_area_sqft=8_000,
        lot_dimensions="50 x 160 ft",
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_850,
        year_built=1955,
        description="Solid well-maintained family home with lane access and R1-1 duplex potential. Major arterial and truck route exposure.",
    )

    result = screen_discovery_candidates([quiet, large_cheap_arterial], _config())
    decisions = {item.card.address: item for item in result.stage_b_candidates}

    assert decisions["2876 Dunbar Street"].home_fit_score > decisions["942 Nanaimo Street"].home_fit_score
    assert decisions["2876 Dunbar Street"].land_adequacy_score == 70
    assert decisions["942 Nanaimo Street"].land_adequacy_score == 100
    assert decisions["2876 Dunbar Street"].area_stratum == "WESTSIDE"
    assert decisions["942 Nanaimo Street"].area_stratum == "EAST_VANCOUVER"
    assert decisions["942 Nanaimo Street"].current_use_components["micro_location_noise"] < decisions["2876 Dunbar Street"].current_use_components["micro_location_noise"]


def test_v22_uses_area_school_and_relative_price_instead_of_absolute_cheapness():
    config = _config()
    config["screening_v2"].update(
        {
            "area_tiers": {
                "WESTSIDE": {"tier": "A", "score": 20},
                "EAST_VANCOUVER": {"tier": "B-", "score": 12},
            },
            "neighbourhood_reference_prices": {
                "Dunbar": 2_000_000,
                "Hastings-Sunrise": 1_400_000,
            },
            "school_proxy": {
                "elementary": {"Dunbar Elementary": {"tier": "A+", "score": 7}},
                "secondary": {"Templeton Secondary": {"tier": "B", "score": 2}},
            },
        }
    )
    westside = _card(
        address="2876 Dunbar Street",
        neighbourhood="Dunbar",
        asking=1_700_000,
        lot_area_sqft=4_000,
        lot_dimensions="33 x 121 ft",
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_850,
        year_built=1955,
        description="Solid well-maintained family home on a quiet residential street near a park. R1-1 zoning, lane access and duplex potential.",
        raw={"elementary_school": "Dunbar Elementary"},
    )
    eastside = _card(
        address="942 Nanaimo Street",
        neighbourhood="Hastings-Sunrise",
        asking=1_270_000,
        lot_area_sqft=8_000,
        lot_dimensions="50 x 160 ft",
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_850,
        year_built=1955,
        description="Solid well-maintained family home with lane access and R1-1 duplex potential. Major arterial and truck route exposure.",
        raw={"elementary_school": "Unknown School"},
    )

    result = screen_discovery_candidates([westside, eastside], config)
    decisions = {item.card.address: item for item in result.stage_b_candidates}

    assert decisions["2876 Dunbar Street"].current_use_score > decisions["942 Nanaimo Street"].current_use_score
    assert decisions["2876 Dunbar Street"].asset_class == "ORTHOGONAL_SCORED"
    assert decisions["942 Nanaimo Street"].asset_class == "ORTHOGONAL_SCORED"
    assert decisions["2876 Dunbar Street"].current_use_score_components["area_neighbourhood_value"] == 20
    # Elementary is known at 7/7; unknown secondary receives its neutral
    # midpoint (1.5/3) instead of a missing-data deduction.
    assert decisions["2876 Dunbar Street"].current_use_score_components["school_proxy"] == 8.5
    assert decisions["2876 Dunbar Street"].area_relative_price_signal > decisions["942 Nanaimo Street"].area_relative_price_signal
    assert decisions["942 Nanaimo Street"].redevelopment_score_components["lot_geometry"] == 20
    assert decisions["2876 Dunbar Street"].home_fit_score <= 100
    assert "AREA_PRICE_REFERENCE_UNKNOWN" not in decisions["2876 Dunbar Street"].flags


def test_v22_unknown_area_price_and_school_are_explicit_not_guessed():
    result = screen_discovery_candidates(
        [
            _card(
                neighbourhood="Unmapped Area",
                year_built=1960,
                description="Well-maintained quiet family home. R1-1 zoning, lane access and duplex potential.",
                raw={},
            )
        ],
        _config(),
    )

    screened = result.stage_b_candidates[0]
    assert screened.score_components["area_relative_price_signal"] == 5
    assert screened.current_use_score_components["school_proxy"] == 5
    assert "AREA_PRICE_REFERENCE_UNKNOWN" in screened.flags
    assert "SCHOOL_PROXY_UNKNOWN" in screened.flags


def test_missing_data_uses_neutral_score_values_without_confidence_penalty():
    result = screen_discovery_candidates(
        [
            _card(
                address="1 Unknown Evidence Street",
                neighbourhood="Unmapped Area",
                raw={},
                description="Family home with no additional verified evidence.",
            )
        ],
        _config(),
    )

    screened = result.stage_b_candidates[0]

    # Missing area, school, road, and zoning evidence remains visible through
    # flags/confidence, but each score component receives a neutral midpoint.
    assert screened.current_use_score_components["area_neighbourhood_value"] == 10
    assert screened.current_use_score_components["school_proxy"] == 5
    assert screened.current_use_score_components["micro_location"] == 12.0
    assert screened.redevelopment_score_components["official_zoning_entitlement"] == 12.5
    assert "NEED_VERIFICATION" in screened.flags
    assert screened.dual_value_score is None
    assert screened not in result.dual_value_top10


def test_partial_school_evidence_is_not_treated_as_zero_for_missing_dimension():
    elementary_only = _card(
        address="1 Elementary Only Street",
        raw={"official_elementary_score": 7},
    )
    secondary_only = _card(
        address="2 Secondary Only Street",
        raw={"official_secondary_score": 3},
    )

    result = screen_discovery_candidates([elementary_only, secondary_only], _config())
    rows = {item.card.address: item for item in result.stage_b_candidates}

    assert rows["1 Elementary Only Street"].current_use_score_components["school_proxy"] == 8.5
    assert rows["2 Secondary Only Street"].current_use_score_components["school_proxy"] == 6.5


def test_official_geometry_is_used_when_listing_geometry_is_missing():
    candidate = _card(
        address="3910 W 10th Avenue",
        lot_dimensions=None,
        frontage_ft=None,
        depth_ft=None,
        lot_area_sqft=4_042.5,
        raw={"official_frontage_ft": 33, "official_depth_ft": 122.5},
        description="Point Grey family home with duplex potential.",
    )

    screened = screen_discovery_candidates([candidate], _config()).stage_b_candidates[0]

    assert screened.redevelopment_score_components["frontage_depth_threshold"] == 10.5
    assert screened.redevelopment_score_components["lot_geometry"] == 14


def test_v22_exact_neighbourhood_match_wins_over_substring_match():
    result = screen_discovery_candidates(
        [_card(neighbourhood="Victoria-Fraserview")],
        {
            **_config(),
            "screening_v2": {
                **_config()["screening_v2"],
                "area_strata": {
                    "CENTRAL_CORE": ["Fraser"],
                    "SOUTH_VANCOUVER": ["Victoria-Fraserview"],
                },
            },
        },
    )

    assert result.stage_b_candidates[0].area_stratum == "SOUTH_VANCOUVER"


def test_v22_configured_arterial_address_receives_direct_penalty():
    config = _config()
    config["screening_v2"]["road_tokens"] = {
        "arterial": ["e 41st avenue"],
    }

    result = screen_discovery_candidates(
        [_card(address="301 E 41st Avenue", description="Convenient family home.")],
        config,
    )

    screened = result.stage_b_candidates[0]
    assert screened.road_class == "arterial"
    assert screened.road_penalty == -8
    assert screened.current_use_components["micro_location_noise"] == 10.65
    assert screened.road_quality_micro_score == 4.65
    assert screened.micro_location_nonroad_score == 6.0


def test_road_frontage_and_distance_are_persisted_as_separate_scoring_facts():
    card = _card(
        address="4561 Nanaimo Street",
        raw={
            "official_road_class": "B",
            "official_distance_to_major_road_m": 4.2,
            "official_property_fronts_major_road": True,
            "official_nearest_major_road": "NANAIMO STREET",
            "official_bus_route": True,
            "official_truck_route": True,
        },
    )

    decision = screen_discovery_candidates([card], _config()).stage_b_candidates[0]

    assert decision.property_fronts_major_road is True
    assert decision.road_distance_m == 4.2
    assert decision.road_quality_direct_score == 2.5
    assert decision.road_quality_exposure_factor == 1.0
    assert decision.road_quality_effective_score == 2.5
    assert decision.road_quality_micro_score == 1.5
    assert decision.current_use_components["micro_location_road"] == 1.5


def test_road_frontage_repairs_cached_street_suffix_abbreviation():
    card = _card(
        address="3910 W 10th Avenue",
        raw={
            "official_road_class": "B",
            "official_nearest_major_road": "3900 W 10TH AV",
            "official_distance_to_major_road_m": 29.0,
            "official_property_fronts_major_road": False,
        },
    )

    decision = screen_discovery_candidates([card], _config()).stage_b_candidates[0]

    assert decision.property_fronts_major_road is True
    assert decision.road_distance_m == 29.0


def test_v22_nearby_arterial_mention_does_not_mean_direct_frontage():
    config = _config()
    config["screening_v2"]["road_tokens"] = {
        "arterial": ["broadway"],
    }

    result = screen_discovery_candidates(
        [
            _card(
                address="2413 Collingwood Street",
                description="Quiet residential street near Broadway shops and transit.",
            )
        ],
        config,
    )

    screened = result.stage_b_candidates[0]
    assert screened.road_class == "quiet"
    assert screened.road_penalty == 0


def test_v23_configured_class_weights_are_used_only_in_that_scorecard():
    config = _config()
    config["screening_v2"]["class_scoring"] = {
        "HOLD_5_10_REDEVELOP": {"area_neighbourhood_value": 5},
    }
    config["screening_v2"]["neighbourhood_tiers"] = {
        "Dunbar": {"tier": "A+", "score": 20},
    }

    screened = screen_discovery_candidates(
        [
            _card(
                neighbourhood="Dunbar",
                year_built=1960,
                description="Well-maintained quiet family home. R1-1 zoning, lane access and duplex potential.",
            )
        ],
        config,
    ).stage_b_candidates[0]

    assert screened.current_use_score_components["area_neighbourhood_value"] == 20
    assert screened.class_score is None


def test_v24_high_density_signal_keeps_both_independent_scores():
    card = _card(
        address="3312 Queens Avenue",
        neighbourhood="Collingwood",
        year_built=1965,
        bedrooms=5,
        bathrooms=3,
        finished_sqft=2_200,
        lot_area_sqft=4_570,
        lot_dimensions="33 x 138 ft",
            description=(
                "Fully renovated in 2024 with new roof, electrical, plumbing, kitchen and bathrooms. "
                "Well-maintained family home on a quiet residential street with lane access. "
            "Transit-oriented area with up to 3.0 FSR and 8 storeys."
        ),
    )

    decision = screen_discovery_candidates([card], _config()).stage_b_candidates[0]

    assert "HIGH_DENSITY_DEVELOPMENT_SIGNAL" not in decision.flags
    assert "TOA_OPTION_SIGNAL" in decision.flags
    assert decision.value_profile == "DUAL_VALUE"
    assert decision.current_use_score > 60
    assert decision.redevelopment_score < 80
    assert decision.confidence_score is not None
    assert decision.class_score is None
    assert decision.status == "SCORED"


def test_toa_is_an_option_and_rt11_is_duplex_not_multiplex():
    card = _card(
        address="4561 Nanaimo Street",
        neighbourhood="Victoria",
        bedrooms=5,
        bathrooms=2,
        finished_sqft=2_205,
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        year_built=1950,
        description="Updated family home with one legal garden suite and back lane access.",
        raw={
            "official_current_zoning": "RT-11",
            "official_toa": "Tier 3",
        },
    )

    decision = screen_discovery_candidates([card], _config()).stage_b_candidates[0]

    assert decision.redevelopment_components["zoning_legal_path"] == 16.0
    assert decision.redevelopment_components["product_market_fit"] == 13.0
    assert decision.redevelopment_components["toa_threshold_assembly"] < 10.0
    assert "TOA_OPTION_SIGNAL" in decision.flags


def test_toa_marketing_language_does_not_route_property_to_high_density_track():
    decision = screen_discovery_candidates(
        [_card(description="Transit-oriented area with up to 3.0 FSR and 8 storeys.")],
        _config(),
    ).stage_b_candidates[0]

    assert decision.asset_class == "ORTHOGONAL_SCORED"
    assert "HIGH_DENSITY_DEVELOPMENT_SIGNAL" not in decision.flags
    assert "TOA_OPTION_SIGNAL" in decision.flags


def test_tod_marketing_language_is_also_a_future_option_only():
    decision = screen_discovery_candidates(
        [_card(description="Currently zoned for duplex potential, inside TOD Tier 3 for future development upside.")],
        _config(),
    ).stage_b_candidates[0]

    assert "HIGH_DENSITY_DEVELOPMENT_SIGNAL" not in decision.flags
    assert "TOA_OPTION_SIGNAL" in decision.flags
    assert decision.redevelopment_components["toa_threshold_assembly"] == 6.0


def test_v24_dual_value_score_requires_both_current_use_and_redevelopment():
    result = screen_discovery_candidates(
        [
            _card(
                address="1 High Use Weak Land Street",
                neighbourhood="Dunbar",
                year_built=2015,
                description="Fully renovated in 2024 with new roof, electrical, plumbing, kitchen and bathrooms. Move-in ready family home on a quiet residential street. Heritage covenant prevents redevelopment.",
            ),
            _card(
                address="2 Weak Use Strong Land Street",
                neighbourhood="Renfrew",
                bedrooms=2,
                bathrooms=1,
                finished_sqft=1_250,
                year_built=1940,
                lot_area_sqft=5_000,
                lot_dimensions="50 x 100 ft",
                description="As-is development site in a TOA with up to 3.0 FSR and 8 storeys.",
            ),
            _card(
                address="3 Dual Value Street",
                neighbourhood="Arbutus",
                year_built=1960,
                lot_area_sqft=4_026,
                lot_dimensions="33 x 122 ft",
        description="Fully renovated in 2022 with new roof, electrical and plumbing. Solid well-maintained family home on a quiet residential street. R1-1 zoning, lane access and duplex potential.",
            ),
        ],
        _config(),
    )

    assert [item.card.address for item in result.current_use_top10] == [
        "3 Dual Value Street",
        "1 High Use Weak Land Street",
        "2 Weak Use Strong Land Street",
    ]
    redevelopment_rows = {item.card.address: item for item in result.redevelopment_top10}
    assert redevelopment_rows["2 Weak Use Strong Land Street"].redevelopment_score > redevelopment_rows["1 High Use Weak Land Street"].redevelopment_score
    assert redevelopment_rows["3 Dual Value Street"].redevelopment_score > redevelopment_rows["1 High Use Weak Land Street"].redevelopment_score
    assert [item.card.address for item in result.dual_value_top10] == ["3 Dual Value Street"]


def test_v24_asking_changes_do_not_change_current_use_or_redevelopment_rank_inputs():
    common = {
        "neighbourhood": "Arbutus",
        "year_built": 1960,
        "lot_area_sqft": 4_026,
        "lot_dimensions": "33 x 122 ft",
        "description": "Solid well-maintained family home on a quiet residential street. R1-1 zoning, lane access and duplex potential.",
    }
    result = screen_discovery_candidates(
        [
            _card(address="1 Cheap Same Asset Street", asking=1_100_000, **common),
            _card(address="2 Expensive Same Asset Street", asking=1_790_000, **common),
        ],
        {**_config(), "screening_v2": {**_config()["screening_v2"], "neighbourhood_reference_prices": {"Arbutus": 1_600_000}}},
    )

    rows = {item.card.address: item for item in result.stage_b_candidates}
    assert rows["1 Cheap Same Asset Street"].current_use_score == rows["2 Expensive Same Asset Street"].current_use_score
    assert rows["1 Cheap Same Asset Street"].redevelopment_score == rows["2 Expensive Same Asset Street"].redevelopment_score
    assert rows["1 Cheap Same Asset Street"].dual_value_score == rows["2 Expensive Same Asset Street"].dual_value_score
    assert rows["1 Cheap Same Asset Street"].area_relative_price_signal != rows["2 Expensive Same Asset Street"].area_relative_price_signal


def test_v24_special_transaction_is_not_in_any_value_leaderboard():
    result = screen_discovery_candidates(
        [_card(description="Court-Ordered Sale for 2/3 share of property. TOA up to 8 storeys.")],
        _config(),
    )

    assert result.special_track_count == 1
    assert result.current_use_top10 == []
    assert result.redevelopment_top10 == []
    assert result.dual_value_top10 == []


def test_current_use_does_not_treat_seven_bed_suite_layout_as_family_advantage():
    family = _card(
        address="1 Family Layout Street",
        neighbourhood="Dunbar",
        bedrooms=4,
        bathrooms=3,
        finished_sqft=2_100,
        year_built=1995,
        description="Well-maintained family home on a quiet residential street.",
    )
    suite_heavy = _card(
        address="2 Suite Layout Street",
        neighbourhood="Dunbar",
        bedrooms=7,
        bathrooms=5,
        finished_sqft=2_100,
        year_built=1995,
        description=(
            "Well-maintained investment property with multiple self-contained suites "
            "and separate rental entrances on a quiet residential street."
        ),
    )

    result = screen_discovery_candidates([family, suite_heavy], _config())
    rows = {item.card.address: item for item in result.stage_b_candidates}

    assert rows["1 Family Layout Street"].current_use_score > rows["2 Suite Layout Street"].current_use_score
    assert "NON_STANDARD_HOLDING_LAYOUT" in rows["2 Suite Layout Street"].flags
    assert rows["2 Suite Layout Street"].score_components["current_use.functional_area_per_bedroom"] < 400


def test_current_use_penalizes_over_divided_bedroom_layout_even_without_listing_suite_words():
    family = _card(
        address="3 Normal Four Bedroom Street",
        neighbourhood="Dunbar",
        bedrooms=4,
        bathrooms=3,
        finished_sqft=2_000,
        year_built=1995,
        description="Well-maintained family home on a quiet residential street.",
    )
    over_divided = _card(
        address="4 Over Divided Seven Bedroom Street",
        neighbourhood="Dunbar",
        bedrooms=7,
        bathrooms=5,
        finished_sqft=2_000,
        year_built=1995,
        description="Well-maintained home on a quiet residential street.",
    )

    result = screen_discovery_candidates([family, over_divided], _config())
    rows = {item.card.address: item for item in result.stage_b_candidates}

    assert rows["3 Normal Four Bedroom Street"].score_components["current_use.functional_area_per_bedroom"] == 500
    assert rows["4 Over Divided Seven Bedroom Street"].score_components["current_use.functional_area_per_bedroom"] < 400
    assert rows["3 Normal Four Bedroom Street"].current_use_score > rows["4 Over Divided Seven Bedroom Street"].current_use_score


def test_stage_a_does_not_apply_global_price_gate_to_regional_candidate():
    candidate = _card(
        address="3910 W 10th Avenue",
        asking=1_875_000,
        bedrooms=3,
        neighbourhood="West Point Grey",
        description="Point Grey family home on a standard lot.",
    )

    config = _config()
    config["screening_v2"]["stage_a"]["price_gate_enabled"] = False
    result = screen_discovery_candidates([candidate], config)

    assert [item.card.address for item in result.stage_a_candidates] == ["3910 W 10th Avenue"]
    assert result.stage_a_excluded == []


def test_four_or_five_bed_main_house_with_one_garden_suite_is_family_hold_candidate():
    candidate = _card(
        address="4561 Nanaimo Street",
        bedrooms=5,
        bathrooms=2,
        finished_sqft=2_205,
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        neighbourhood="Victoria",
        year_built=1950,
        description=(
            "Updated 5-bedroom bungalow with one legal 1-bedroom garden suite. "
            "Recent updates include double glazed windows, roof, hot water tank and "
            "fire sprinkler system. Back lane access, RT-11 zoning, duplex potential."
        ),
    )

    decision = screen_discovery_candidates([candidate], _config()).stage_b_candidates[0]

    assert "NON_STANDARD_HOLDING_LAYOUT" not in decision.flags
    assert "GARDEN_SUITE_COMPATIBLE" in decision.flags
    # Recent system updates keep this in the current-use universe, but the
    # direct major-road exposure is still a material current-use penalty.
    assert decision.current_use_score >= 50
    assert decision.redevelopment_score >= 70
    assert decision.dual_value_score is not None
    assert decision.road_quality_micro_score is not None


def test_multiple_kitchens_inside_main_house_are_penalized_but_separate_garden_suite_is_not():
    normal = _card(
        address="1 Separate Garden Suite Street",
        bedrooms=5,
        bathrooms=2,
        finished_sqft=2_200,
        description=(
            "Updated family home with one legal garden suite and separate entrance "
            "on a quiet residential street."
        ),
    )
    divided = _card(
        address="2 Multiple Main Kitchens Street",
        bedrooms=5,
        bathrooms=2,
        finished_sqft=2_200,
        description=(
            "Updated home with two kitchens in the main house, multiple suites and "
            "separate rental entrances on a quiet residential street."
        ),
    )

    result = screen_discovery_candidates([normal, divided], _config())
    rows = {item.card.address: item for item in result.stage_b_candidates}

    assert "GARDEN_SUITE_COMPATIBLE" in rows["1 Separate Garden Suite Street"].flags
    assert "MULTIPLE_MAIN_KITCHENS" in rows["2 Multiple Main Kitchens Street"].flags
    assert rows["1 Separate Garden Suite Street"].current_use_score > rows["2 Multiple Main Kitchens Street"].current_use_score


def test_current_use_consumes_official_school_and_road_evidence():
    config = _config()
    config["screening_v2"]["school_proxy"] = {
        "elementary": {"Official Elementary": {"score": 10}},
        "secondary": {"Official Secondary": {"score": 3}},
    }
    quiet = _card(
        address="1 Official Location Street",
        neighbourhood="Dunbar",
        description="Well-maintained family home.",
        raw={
            "official_elementary_catchment": "Official Elementary",
            "official_secondary_catchment": "Official Secondary",
            "road_class": "E",
            "distance_to_major_road_m": 300,
        },
    )
    arterial = _card(
        address="2 Official Location Street",
        neighbourhood="Dunbar",
        description="Well-maintained family home.",
        raw={
            "official_elementary_catchment": "Official Elementary",
            "official_secondary_catchment": "Official Secondary",
            "road_class": "B",
            "distance_to_major_road_m": 0,
        },
    )

    result = screen_discovery_candidates([quiet, arterial], config)
    rows = {item.card.address: item for item in result.stage_b_candidates}

    assert rows["1 Official Location Street"].school_proxy_status == "KNOWN"
    assert rows["1 Official Location Street"].current_use_score > rows["2 Official Location Street"].current_use_score
    assert rows["2 Official Location Street"].road_class == "major"


def test_bca_assessment_discount_is_calculated_and_enters_price_safety_score():
    assessed = _card(
        address="1 BCA Discount Street",
        asking=1_600_000,
        neighbourhood="Dunbar",
        raw={
            "official_assessment_total": 2_000_000,
            "official_assessment_land": 1_700_000,
            "official_assessment_improvement": 300_000,
            "official_assessment_year": 2026,
        },
    )
    no_assessment = _card(
        address="2 No BCA Street",
        asking=1_600_000,
        neighbourhood="Dunbar",
    )

    result = screen_discovery_candidates([assessed, no_assessment], _config())
    rows = {item.card.address: item for item in result.stage_b_candidates}
    discount = rows["1 BCA Discount Street"]
    missing = rows["2 No BCA Street"]

    assert discount.ask_to_assessment_ratio == 0.8
    assert discount.assessment_discount_pct == 0.2
    assert discount.ask_to_land_assessment_ratio == round(1_600_000 / 1_700_000, 4)
    assert discount.land_assessment_discount_pct == round(1 - 1_600_000 / 1_700_000, 4)
    assert discount.assessment_discount_score == 90
    assert discount.assessment_discount_status == "KNOWN_OFFICIAL"
    assert "ASSESSMENT_DISCOUNT_20PCT_PLUS" in discount.flags
    assert "ASK_BELOW_LAND_ASSESSMENT" in discount.flags
    assert discount.investment_score > missing.investment_score
    assert discount.current_use_score == missing.current_use_score
    assert discount.redevelopment_score == missing.redevelopment_score


def test_bca_discount_does_not_guess_when_official_assessment_is_missing():
    result = screen_discovery_candidates([_card()], _config())
    screened = result.stage_b_candidates[0]

    assert screened.assessment_discount_status == "UNKNOWN"
    assert screened.ask_to_assessment_ratio is None
    assert screened.assessment_discount_pct is None
    assert screened.assessment_discount_score is None
    assert "ASSESSMENT_UNKNOWN" in screened.flags


def test_cosmetic_updates_do_not_make_an_old_house_holding_good():
    candidate = _card(
        address="3186 E 28th Avenue",
        year_built=1918,
        description=(
            "Updated 2-bedroom suite with renovated bathrooms, laminate flooring "
            "and newer windows."
        ),
    )

    decision = screen_discovery_candidates([candidate], _config()).stage_b_candidates[0]

    assert decision.holding_status == "HOLDING_ACCEPTABLE"
    assert decision.current_use_score_components["maintenance_systems"] < 18.4


def test_panoramic_north_shore_and_city_views_are_captured_as_outlook_signal():
    candidate = _card(
        address="3910 W 10th Avenue",
        year_built=1947,
        description="High-side Point Grey home with panoramic North Shore mountain and city views.",
    )

    decision = screen_discovery_candidates([candidate], _config()).stage_b_candidates[0]

    assert "STRONG_OUTLOOK_SIGNAL" in decision.flags


def test_report_labels_regional_dislocation_separately_from_bca_discount():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_000_000,
        "score_anchor": 50,
        "residuals": {
            "Fraser": {
                "residual_cad": 152_000,
                "standard_property_value": 2_216_000,
                "confidence": "MEDIUM",
            },
        },
    }
    candidate = _card(
        address="1016 E 18th Avenue",
        neighbourhood="Fraser",
        asking=1_100_000,
        raw={
            "official_assessment_total": 1_334_700,
            "official_assessment_land": 1_309_000,
            "official_assessment_improvement": 25_700,
            "official_assessment_year": 2026,
        },
    )
    screening = screen_discovery_candidates([candidate], config)
    result = DiscoveryRunResult(
        status="COMPLETED",
        candidate_count=1,
        candidates=[candidate],
        screening=screening,
    )

    report = render_discovery_report(result)

    assert "regional standard dislocation" in report
    assert "BCA total discount" in report
    assert "BCA discount" not in report


def test_physical_normalization_scales_land_component_without_erasing_house_value():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_000_000,
        "standard_market_land_share": 0.75,
        "reference_lot_area_sqft": 4_026,
        "reference_frontage_ft": 33,
        "reference_depth_ft": 122,
        "reference_house_sqft": 1_700,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 500_000,
                "standard_property_value": 2_500_000,
                "confidence": "HIGH",
            },
        },
    }
    config["screening_v2"]["physical_normalization"] = {"enabled": True}
    standard = _card(
        address="1 Standard Lot Street",
        neighbourhood="West Point Grey",
        asking=2_500_000,
        finished_sqft=1_700,
        bedrooms=3,
        bathrooms=2,
        lot_area_sqft=4_026,
        lot_dimensions="33 x 122 ft",
        year_built=1960,
    )
    small = _card(
        address="2 Small Lot Street",
        neighbourhood="West Point Grey",
        asking=2_300_000,
        finished_sqft=1_700,
        bedrooms=3,
        bathrooms=2,
        lot_area_sqft=3_000,
        lot_dimensions="25 x 120 ft",
        year_built=1960,
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([standard, small], config).stage_b_candidates
    }

    assert rows["2 Small Lot Street"].physical_lot_factor < rows["1 Standard Lot Street"].physical_lot_factor
    assert rows["2 Small Lot Street"].normalized_existing_house_value == rows["1 Standard Lot Street"].normalized_existing_house_value
    assert rows["2 Small Lot Street"].normalized_lot_basis_per_sqft > rows["1 Standard Lot Street"].normalized_lot_basis_per_sqft
    assert rows["2 Small Lot Street"].physical_adjusted_expected_value < rows["1 Standard Lot Street"].physical_adjusted_expected_value


def test_similar_asking_3910_beats_2012_on_physical_land_and_dual_signals():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_064_000,
        "standard_market_land_share": 0.75,
        "reference_lot_area_sqft": 4_026,
        "reference_frontage_ft": 33,
        "reference_depth_ft": 122,
        "reference_house_sqft": 1_700,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 563_000,
                "standard_property_value": 2_627_000,
                "confidence": "MEDIUM",
            },
            "Kitsilano": {
                "residual_cad": 498_000,
                "standard_property_value": 2_562_000,
                "confidence": "MEDIUM",
            },
        },
    }
    config["screening_v2"]["physical_normalization"] = {"enabled": True}
    w10 = _card(
        address="3910 W 10th Avenue",
        neighbourhood="West Point Grey",
        asking=1_875_000,
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_985,
        lot_area_sqft=4_042.5,
        lot_dimensions="33 x 122.5 ft",
        year_built=1947,
        description="High-side home with panoramic North Shore mountain and city views; renovation potential.",
        raw={
            "official_assessment_total": 2_242_200,
            "official_assessment_land": 2_212_000,
            "official_assessment_improvement": 30_200,
        },
    )
    york = _card(
        address="2012 York Avenue",
        neighbourhood="Kitsilano",
        asking=1_899_000,
        bedrooms=4,
        bathrooms=3,
        finished_sqft=1_827,
        lot_area_sqft=3_000,
        lot_dimensions="25 x 120 ft",
        year_built=1906,
        description="Character renovator three blocks to Kits Beach with excellent walkability.",
        raw={
            "official_assessment_total": 2_129_000,
            "official_assessment_land": 2_068_000,
            "official_assessment_improvement": 61_000,
        },
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([w10, york], config).stage_b_candidates
    }

    assert rows["3910 W 10th Avenue"].normalized_lot_basis_per_sqft < rows["2012 York Avenue"].normalized_lot_basis_per_sqft
    assert rows["3910 W 10th Avenue"].redevelopment_score > rows["2012 York Avenue"].redevelopment_score
    assert rows["3910 W 10th Avenue"].dual_value_score is not None
    assert rows["2012 York Avenue"].dual_value_score is None or rows["3910 W 10th Avenue"].dual_value_score > rows["2012 York Avenue"].dual_value_score


def test_reno_score_uses_the_approved_cost_bands():
    assert screening_module._reno_score_from_estimate(0) == (100.0, "MOVE_IN_READY")
    assert screening_module._reno_score_from_estimate(30_000) == (80.0, "LIGHT_RENO")
    assert screening_module._reno_score_from_estimate(60_000) == (60.0, "MODERATE_RENO")
    assert screening_module._reno_score_from_estimate(100_000) == (40.0, "SIGNIFICANT_RENO")
    assert screening_module._reno_score_from_estimate(150_000) == (20.0, "HEAVY_RENO")
    assert screening_module._reno_score_from_estimate(150_001) == (0.0, "CURRENT_USE_PASS")


def test_recent_substantial_listing_renovation_is_move_in_ready():
    profile = screening_module._reno_profile(
        _card(
            year_built=1948,
            description="Fully renovated in 2023 with new roof, electrical, plumbing, kitchen and bathrooms.",
        )
    )

    assert profile["estimate"] == 0.0
    assert profile["score"] == 100.0
    assert profile["band"] == "MOVE_IN_READY"
    assert profile["reno_year"] == 2023


def test_recent_multi_system_updates_are_not_overstated_as_old_house_reno():
    profile = screening_module._reno_profile(
        _card(
            year_built=1950,
            description=(
                "Recent updates include double glazed windows, roof, hot water tank "
                "and fire sprinkler system."
            ),
        )
    )

    assert profile["estimate"] <= 30_000
    assert profile["score"] == 80.0
    assert "recent_multi_system_updates" in profile["evidence"]


def test_reno_estimate_uses_age_fallback_and_listing_scope():
    untouched = screening_module._reno_profile(_card(year_built=1948, description=None))
    partially_updated = screening_module._reno_profile(
        _card(
            year_built=1948,
            description="Updated kitchen and bathroom in 2022; otherwise original systems.",
        )
    )

    assert untouched["estimate"] == 100_000.0
    assert untouched["score"] == 40.0
    assert partially_updated["estimate"] < untouched["estimate"]
    # Kitchen and bathroom updates reduce the age fallback, but old core
    # systems remain unresolved; the remaining estimate stays in the
    # significant-reno band.
    assert partially_updated["score"] == 40.0
    assert "kitchen" in partially_updated["scope"]
    assert "bathroom" in partially_updated["scope"]


def test_bca_normalized_land_basis_uses_bca_improvement_directly():
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_064_000,
        "reference_lot_area_sqft": 4_026,
        "reference_frontage_ft": 33,
        "reference_depth_ft": 122,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 563_000,
                "standard_property_value": 2_627_000,
                "confidence": "MEDIUM",
            },
        },
    }
    config["screening_v2"]["physical_normalization"] = {"enabled": True}
    row = screen_discovery_candidates(
        [
            _card(
                neighbourhood="West Point Grey",
                asking=1_875_000,
                lot_area_sqft=4_042.5,
                lot_dimensions="33 x 122.5 ft",
                year_built=1947,
                raw={
                    "official_assessment_total": 2_242_200,
                    "official_assessment_land": 2_212_000,
                    "official_assessment_improvement": 30_200,
                },
            )
        ],
        config,
    ).stage_b_candidates[0]

    assert row.bca_house_value_proxy == 30_200.0
    assert row.normalized_existing_house_value == 30_200.0
    assert row.bca_normalized_land_basis == 1_281_800.0
    assert row.bca_normalized_land_basis_per_sqft == 317.08
    assert row.current_use_all_in_basis >= row.card.asking


def test_standard_33_by_122_is_a_threshold_not_a_redevelopment_geometry_maximum():
    standard = screen_discovery_candidates(
        [_card(lot_area_sqft=4_026, lot_dimensions="33 x 122 ft")],
        _config(),
    ).stage_b_candidates[0]
    wide = screen_discovery_candidates(
        [_card(lot_area_sqft=5_100, lot_dimensions="50 x 102 ft")],
        _config(),
    ).stage_b_candidates[0]

    assert standard.redevelopment_score_components["lot_geometry"] < 20.0
    assert standard.redevelopment_score_components["frontage_depth_threshold"] < 15.0
    assert wide.redevelopment_score_components["lot_geometry"] > standard.redevelopment_score_components["lot_geometry"]
    assert wide.redevelopment_score_components["frontage_depth_threshold"] > standard.redevelopment_score_components["frontage_depth_threshold"]


def test_permanent_road_discount_reduces_redevelopment_score_not_only_current_use():
    quiet = _card(
        address="1 Quiet Interior Street",
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        raw={"official_road_class": "E", "official_distance_to_major_road_m": 250},
    )
    arterial = _card(
        address="2 Nanaimo Street",
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        raw={
            "official_road_class": "B",
            "official_distance_to_major_road_m": 0,
            "official_property_fronts_major_road": True,
        },
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([quiet, arterial], _config()).stage_b_candidates
    }

    assert rows["2 Nanaimo Street"].road_discount_pct >= 0.08
    assert rows["2 Nanaimo Street"].redevelopment_score < rows["1 Quiet Interior Street"].redevelopment_score
    assert rows["2 Nanaimo Street"].redevelopment_score_components["permanent_execution_risk"] < rows["1 Quiet Interior Street"].redevelopment_score_components["permanent_execution_risk"]
    assert rows["2 Nanaimo Street"].redevelopment_components["road_exit_discount_pct"] >= 0.08


def test_reno_above_150k_excludes_current_use_and_dual_but_keeps_redevelopment():
    card = _card(
        lot_area_sqft=5_100,
        lot_dimensions="50 x 102 ft",
        raw={"official_reno_estimate_cad": 175_000},
    )

    row = screen_discovery_candidates([card], _config()).stage_b_candidates[0]

    assert row.reno_estimate == 175_000.0
    assert row.reno_score == 0.0
    assert row.current_use_score == 0.0
    assert row.dual_value_score is None
    assert "CURRENT_USE_RENO_PASS" in row.flags
    assert row.redevelopment_score is not None


def test_current_use_price_value_includes_required_reno_in_all_in_basis():
    config = _config()
    config["screening_v2"]["neighbourhood_reference_prices"] = {"Dunbar": 1_600_000}
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "residuals": {
            "Dunbar": {
                "residual_cad": 0,
                "standard_property_value": 1_600_000,
            },
        },
    }
    ready = _card(
        address="1 Ready Basis Street",
        neighbourhood="Dunbar",
        asking=1_500_000,
        raw={"official_reno_estimate_cad": 0},
    )
    work = _card(
        address="2 Work Basis Street",
        neighbourhood="Dunbar",
        asking=1_500_000,
        raw={"official_reno_estimate_cad": 100_000},
    )

    rows = {
        item.card.address: item
        for item in screen_discovery_candidates([ready, work], config).stage_b_candidates
    }

    assert rows["1 Ready Basis Street"].current_use_all_in_basis == 1_500_000.0
    assert rows["2 Work Basis Street"].current_use_all_in_basis == 1_600_000.0
    assert rows["1 Ready Basis Street"].current_use_score_components["regional_price_safety"] > rows["2 Work Basis Street"].current_use_score_components["regional_price_safety"]


def _unified_value_config() -> dict:
    config = _config()
    config["screening_v2"]["location_residual_model"] = {
        "enabled": True,
        "neutral_standard_value": 2_064_000,
        "residuals": {
            "West Point Grey": {
                "residual_cad": 563_000,
                "standard_property_value": 2_627_000,
                "confidence": "MEDIUM",
            },
            "Kitsilano": {
                "residual_cad": 498_000,
                "standard_property_value": 2_562_000,
                "confidence": "MEDIUM",
            },
        },
    }
    config["screening_v2"]["physical_normalization"] = {
        "enabled": True,
        "reference_lot_area_sqft": 4_026,
        "reference_frontage_ft": 33,
        "reference_depth_ft": 122,
        "reference_house_sqft": 1_700,
        "standard_market_land_share": 0.75,
    }
    config["screening_v2"]["orthogonal_scoring"] = {
        "value_for_money_components": {
            "current_use": {
                "house_value_for_money": 35,
                "location_value_for_money": 20,
                "lot_value_for_money": 20,
                "regional_price_safety": 10,
                "bca_price_safety": 10,
                "permanent_risk": 5,
            },
            "redevelopment": {
                "house_value_for_money": 5,
                "location_value_for_money": 15,
                "lot_value_for_money": 30,
                "development_capacity_value_for_money": 30,
                "regional_price_safety": 0,
                "bca_price_safety": 10,
                "permanent_risk": 10,
            },
        },
    }
    return config


def test_current_redevelopment_and_dual_share_one_value_ledger():
    result = screen_discovery_candidates(
        [
            _card(
                address="3910 W 10th Avenue",
                neighbourhood="West Point Grey",
                asking=1_875_000,
                lot_area_sqft=4_042.5,
                lot_dimensions="33 x 122.5 ft",
                raw={
                    "official_assessment_total": 2_242_200,
                    "official_assessment_land": 2_212_000,
                    "official_assessment_improvement": 30_200,
                },
            )
        ],
        _unified_value_config(),
    )
    row = result.stage_b_candidates[0]

    assert row.area_normalized_lot_basis_per_sqft == row.score_components["area_normalized_lot_basis_per_sqft"]
    assert row.risk_adjusted_lot_basis_per_sqft == row.score_components["risk_adjusted_lot_basis_per_sqft"]
    assert row.current_use_score is not None
    assert row.redevelopment_score is not None
    assert row.dual_value_score is not None


def test_current_use_value_for_money_penalizes_material_lot_basis_difference():
    result = screen_discovery_candidates(
        [
            _card(
                address="3910 W 10th Avenue",
                neighbourhood="West Point Grey",
                asking=1_875_000,
                bedrooms=3,
                bathrooms=2,
                finished_sqft=1_985,
                lot_area_sqft=4_042.5,
                lot_dimensions="33 x 122.5 ft",
                year_built=1947,
                raw={
                    "official_assessment_total": 2_242_200,
                    "official_assessment_land": 2_212_000,
                    "official_assessment_improvement": 30_200,
                },
            ),
            _card(
                address="2012 York Avenue",
                neighbourhood="Kitsilano",
                asking=1_899_000,
                bedrooms=4,
                bathrooms=3,
                finished_sqft=1_827,
                lot_area_sqft=3_000,
                lot_dimensions="25 x 120 ft",
                year_built=1906,
                raw={
                    "official_assessment_total": 2_129_000,
                    "official_assessment_land": 2_068_000,
                    "official_assessment_improvement": 61_000,
                },
            ),
        ],
        _unified_value_config(),
    )
    rows = {item.card.address: item for item in result.stage_b_candidates}
    york = rows["2012 York Avenue"]
    west10 = rows["3910 W 10th Avenue"]

    assert york.area_normalized_lot_basis_per_sqft > west10.area_normalized_lot_basis_per_sqft
    assert york.lot_value_for_money_score < west10.lot_value_for_money_score
    assert west10.current_use_value_for_money_score >= york.current_use_value_for_money_score


def test_regional_price_safety_does_not_add_location_residual_twice():
    result = screen_discovery_candidates(
        [_card(address="3910 W 10th Avenue", neighbourhood="West Point Grey")],
        _unified_value_config(),
    )
    row = result.stage_b_candidates[0]

    assert row.current_use_score_components["location_residual"] == 0.0
    assert row.regional_standard_value == 2_627_000


def test_reno_is_current_use_cost_not_an_oversized_maintenance_bonus():
    result = screen_discovery_candidates(
        [_card(address="3910 W 10th Avenue", raw={"official_reno_estimate_cad": 100_000})],
        _unified_value_config(),
    )
    row = result.stage_b_candidates[0]

    assert row.current_use_effective_basis == 1_600_000
    assert row.current_use_score_components["house_value_for_money"] <= 25.0


def test_unified_current_package_uses_bca_total_and_dual_is_a_view_for_every_row():
    result = screen_discovery_candidates(
        [_card(
            address="4561 Nanaimo Street",
            asking=1_499_000,
            bedrooms=5,
            bathrooms=2,
            finished_sqft=2_205,
            lot_area_sqft=5_100,
            lot_dimensions="50 x 102 ft",
            year_built=1950,
            description="Recent updates include double glazed windows, roof, hot water tank and fire sprinkler system. Back lane access.",
            raw={
                "official_assessment_total": 1_701_500,
                "official_assessment_land": 1_673_000,
                "official_assessment_improvement": 28_500,
                "official_current_zoning": "RT-11",
            },
        )],
        _unified_value_config(),
    )
    row = result.stage_b_candidates[0]

    assert row.current_package_value_proxy == 1_701_500.0
    assert row.current_package_value_proxy_source == "BCA_TOTAL"
    assert row.current_package_value_for_money_score is not None
    assert row.redevelopment_capacity_value_for_money_score is not None
    assert row.dual_value_score == round((row.current_use_score * row.redevelopment_score) ** 0.5, 2)


def test_authoritative_score_components_sum_after_layout_or_redevelopment_caps():
    rows = screen_discovery_candidates(
        [
            _card(
                address="75 N Nanaimo Street",
                bedrooms=9,
                bathrooms=6,
                description="Nine bedrooms and multiple kitchens in the main house.",
            ),
            _card(
                address="1 Fire Damage Street",
                description="Fire damaged; sold as-is, where-is.",
            ),
        ],
        _unified_value_config(),
    ).stage_b_candidates

    for row in rows:
        assert sum(row.current_use_score_components.values()) == row.current_use_score
        assert sum(row.redevelopment_score_components.values()) == row.redevelopment_score


def test_original_condition_cannot_win_current_use_by_land_discount_alone():
    rows = {
        row.card.address: row
        for row in screen_discovery_candidates(
            [
                _card(
                    address="1 Original Condition Street",
                    asking=1_499_000,
                    year_built=1965,
                    bedrooms=5,
                    bathrooms=2,
                    finished_sqft=2_231,
                    lot_area_sqft=5_926,
                    description="Original condition home of 2,231 sf with 5-bedroom, 2-bathroom.",
                    raw={
                        "official_assessment_total": 1_859_200,
                        "official_assessment_land": 1_789_000,
                        "official_assessment_improvement": 70_200,
                    },
                ),
                _card(
                    address="2 Move In Ready Street",
                    asking=1_499_000,
                    year_built=1985,
                    bedrooms=4,
                    bathrooms=3,
                    finished_sqft=1_900,
                    lot_area_sqft=4_026,
                    lot_dimensions="33 x 122 ft",
                    description="Fully renovated in 2024 with new roof, furnace, electrical, plumbing and kitchen.",
                    raw={
                        "official_assessment_total": 1_859_200,
                        "official_assessment_land": 1_789_000,
                        "official_assessment_improvement": 70_200,
                    },
                ),
            ],
            _unified_value_config(),
        ).stage_b_candidates
    }

    original = rows["1 Original Condition Street"]
    ready = rows["2 Move In Ready Street"]

    assert original.reno_estimate == 100_000
    assert original.current_use_score <= 70.0
    assert original.current_use_score < ready.current_use_score


def test_redevelopment_geometry_sets_standard_lot_at_seventy_and_small_lot_low():
    rows = {
        row.card.address: row
        for row in screen_discovery_candidates(
            [
                _card(
                    address="1 Standard Rebuild Lot",
                    lot_area_sqft=4_026,
                    lot_dimensions="33 x 122 ft",
                    raw={"official_current_zoning": "R1-1"},
                ),
                _card(
                    address="2 Small Rebuild Lot",
                    asking=1_100_000,
                    lot_area_sqft=2_772,
                    lot_dimensions=None,
                    raw={"official_current_zoning": "RM-1"},
                ),
            ],
            _unified_value_config(),
        ).stage_b_candidates
    }

    standard = rows["1 Standard Rebuild Lot"]
    small = rows["2 Small Rebuild Lot"]

    assert standard.redevelopment_geometry_adequacy_score == 70.0
    assert small.redevelopment_geometry_adequacy_score < 40.0
    assert small.redevelopment_lot_value_for_money_score < standard.redevelopment_lot_value_for_money_score
    assert small.redevelopment_score < 70.0
    assert small.redevelopment_score < standard.redevelopment_score


def test_land_adequacy_uses_redevelopment_geometry_not_stage_b_sufficiency():
    standard = screen_discovery_candidates(
        [_card(lot_area_sqft=4_026, lot_dimensions="33 x 122 ft")],
        _config(),
    ).stage_b_candidates[0]
    larger = screen_discovery_candidates(
        [_card(lot_area_sqft=5_100, lot_dimensions="50 x 102 ft")],
        _config(),
    ).stage_b_candidates[0]

    assert standard.land_adequacy_score == 70.0
    assert larger.land_adequacy_score > standard.land_adequacy_score


def _west10_card(**overrides: object) -> DiscoveryCard:
    values = dict(
        address="3910 W 10th Avenue",
        neighbourhood="West Point Grey",
        asking=1_875_000,
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_985,
        lot_area_sqft=4_042.5,
        lot_dimensions="33 x 122.5 ft",
        year_built=1947,
        raw={
            "official_reno_estimate_cad": 100_000,
            "official_assessment_total": 2_242_200,
            "official_assessment_land": 2_212_000,
            "official_assessment_improvement": 30_200,
        },
    )
    values.update(overrides)
    return _card(**values)


def test_current_use_is_not_pinned_by_the_flat_reno_cap():
    row = screen_discovery_candidates(
        [_west10_card()],
        _unified_value_config(),
    ).stage_b_candidates[0]

    assert row.reno_estimate == 100_000.0
    assert row.reno_band == "SIGNIFICANT_RENO"
    # The historical cap pinned every 60-100k reno home to exactly 40.0.  The
    # cap now sits one band step higher (60) so a strong Point Grey package
    # still outranks worn stock without beating move-in-ready homes.
    assert 40.0 < row.current_use_score <= 60.0
    assert "CURRENT_USE_RENO_CAP_APPLIED" in row.flags


def test_current_use_caps_homes_fronting_ordinary_arterials():
    row = screen_discovery_candidates(
        [_west10_card(raw={
            "official_reno_estimate_cad": 100_000,
            "official_assessment_total": 2_242_200,
            "official_property_fronts_major_road": True,
            "official_road_class": "C",
            "official_distance_to_major_road_m": 25,
        })],
        _unified_value_config(),
    ).stage_b_candidates[0]

    assert row.property_fronts_major_road is True
    assert row.current_use_score == 45.0
    assert "ROAD_FRONTAGE_CURRENT_USE_CAPPED" in row.flags


def test_current_use_caps_homes_fronting_bus_truck_corridors_harder():
    row = screen_discovery_candidates(
        [_west10_card(
            address="4561 Nanaimo Street",
            raw={
                "official_reno_estimate_cad": 30_000,
                "official_assessment_total": 1_701_500,
                "official_property_fronts_major_road": True,
                "official_road_class": "B",
                "official_distance_to_major_road_m": 30,
                "official_bus_route": True,
                "official_truck_route": True,
            },
        )],
        _unified_value_config(),
    ).stage_b_candidates[0]

    assert row.current_use_score == 35.0
    assert "ROAD_FRONTAGE_CURRENT_USE_CAPPED" in row.flags


def test_current_use_not_capped_for_homes_near_but_not_fronting_major_roads():
    row = screen_discovery_candidates(
        [_west10_card(raw={
            "official_reno_estimate_cad": 100_000,
            "official_assessment_total": 2_242_200,
            "official_property_fronts_major_road": False,
            "official_road_class": "C",
            "official_distance_to_major_road_m": 120,
        })],
        _unified_value_config(),
    ).stage_b_candidates[0]

    assert row.property_fronts_major_road is False
    assert row.current_use_score > 45.0
    assert "ROAD_FRONTAGE_CURRENT_USE_CAPPED" not in row.flags


def test_current_use_livability_gate_blocks_unlivable_condition():
    rows = {
        row.card.address: row
        for row in screen_discovery_candidates(
            [
                _west10_card(
                    address="3 Tiny Wreck Street",
                    asking=1_300_000,
                    bedrooms=1,
                    bathrooms=1,
                    finished_sqft=650,
                    raw={
                        "official_reno_estimate_cad": 130_000,
                        "official_assessment_total": 2_000_000,
                    },
                ),
                _west10_card(
                    address="4 Family Wreck Street",
                    asking=1_300_000,
                    raw={
                        "official_reno_estimate_cad": 130_000,
                        "official_assessment_total": 2_000_000,
                    },
                ),
            ],
            _unified_value_config(),
        ).stage_b_candidates
    }
    tiny = rows["3 Tiny Wreck Street"]
    family = rows["4 Family Wreck Street"]

    # condition <20 caps Current Use at the bottom band; condition <40 caps
    # it at 40.  A cheap teardown cannot win the livability view on price.
    assert tiny.house_condition_score < 20.0
    assert tiny.current_use_score <= 20.0
    assert "LOW_CONDITION_CURRENT_USE_CAPPED" in tiny.flags
    assert family.current_use_score <= 40.0
