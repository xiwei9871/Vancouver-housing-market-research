from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import (
    EvidenceStatus,
    PropertyRecord,
    RedevelopmentScenario,
    RoadClass,
    ScenarioType,
)
from northshore_watchdog.research.provenance import default_evidence_items
from datetime import datetime, timezone

from northshore_watchdog.research.scoring import (
    build_baseline_scenarios,
    calculate_independent_scores,
    calculate_future_opportunity_cost,
    select_best_economic_product,
    score_property,
)


def _evidence():
    return default_evidence_items(confirmed={
        "official_pid_confirmed",
        "ownership_confirmed",
        "official_assessment",
        "lot_dimensions_confirmed",
        "official_zoning",
        "approved_use_confirmed",
        "listing_history_confirmed",
        "sold_history_cross_checked",
        "official_school_catchment",
        "road_location_verified",
    })


def _strong_record(**updates):
    values = {
        "property_id": "property:strong",
        "address": "123 Strong Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "detached house",
        "pid": "012-345-678",
        "ownership_type": "FREEHOLD_NON_STRATA",
        "asking": 1_450_000,
        "original_asking": 1_650_000,
        "dom": 75,
        "lot_area_sqft": 5000,
        "lot_size_percentile_in_neighbourhood": 80,
        "frontage_ft": 40,
        "depth_ft": 125,
        "lot_shape": "regular_rectangular",
        "lane_access": True,
        "slope": "flat",
        "finished_sqft": 2100,
        "bedrooms": 4,
        "bathrooms": 3,
        "year_built": 1980,
        "effective_year": 2000,
        "basement": "finished",
        "separate_entrance": True,
        "current_house_utility_score": 85,
        "zoning": "R1-1",
        "bylaw_version": "test-bylaw-v1",
        "permitted_uses": ["detached", "duplex"],
        "minimum_frontage": 13.4,
        "maximum_fsr": 1.0,
        "maximum_units": 4,
        "maximum_height": 12,
        "buildability_score": 80,
        "realistic_buildable_sqft": 4000,
        "road_class": RoadClass.QUIET_RESIDENTIAL,
        "distance_to_major_road_m": 250,
        "property_fronts_major_road": False,
        "outdoor_noise_exposure": 10,
        "indoor_noise_mitigation_potential": 80,
        "current_school_score": 85,
        "school_future_score": 80,
        "family_location_score": 90,
        "buyer_pool_score": 85,
        "affordability_score": 80,
        "liquidity_score": 85,
        "expected_market_value": 1_650_000,
        "price_dislocation_pct": -0.12,
        "relative_affordability_index": 0.88,
        "land_value_proxy": 1_300_000,
        "land_discount_or_premium_pct": -0.10,
        "annual_municipal_carrying_cost": 9000,
        "tax": 7000,
        "utilities": 2000,
        "approved_use": "SINGLE_DETACHED",
        "official_zoning_confirmed": True,
        "official_zoning_source_url": "local://official/r1-1",
        "official_zoning_source_registry_id": "source:test-official-zoning",
        "official_zoning_evidence_status": "CONFIRMED",
        "official_zoning_retrieved_at": "2026-08-30T00:00:00+00:00",
    }
    values.update(updates)
    return PropertyRecord.model_validate(values)


def test_score_snapshot_has_the_eight_approved_dimension_maxima():
    snapshot = score_property(_strong_record(), load_config("config/research.yaml"), evidence=_evidence())

    assert [snapshot.dimensions[name].maximum_points for name in (
        "land_lot_title",
        "price_safety_margin",
        "current_house_utility",
        "family_location_noise",
        "school",
        "redevelopment_option_value",
        "resale_affordability",
        "carrying_cost_risk",
    )] == [20, 15, 15, 10, 10, 20, 5, 5]


def test_school_score_requires_an_official_catchment():
    snapshot = score_property(
        _strong_record(official_elementary_catchment=None, official_secondary_catchment=None),
        load_config("config/research.yaml"),
        evidence=_evidence(),
    )

    school = snapshot.dimensions["school"].components
    assert school["elementary_current"].evidence_status is EvidenceStatus.UNKNOWN
    assert school["secondary_current"].evidence_status is EvidenceStatus.UNKNOWN


def test_missing_fields_use_conservative_provisional_points_and_are_visible():
    snapshot = score_property(
        PropertyRecord(
            property_id="property:unknown",
            address="1 Unknown Street",
            municipality="City of Vancouver",
        ),
        load_config("config/research.yaml"),
        evidence=default_evidence_items(),
    )

    assert snapshot.total_score > 0
    assert snapshot.unknown_fields
    assert any(
        component.evidence_status is EvidenceStatus.UNKNOWN
        for dimension in snapshot.dimensions.values()
        for component in dimension.components.values()
    )


def test_conflicting_field_is_not_scored_as_either_conflicting_value():
    record = _strong_record()
    record = record.model_copy(update={"data_conflict": True})
    snapshot = score_property(
        record,
        load_config("config/research.yaml"),
        evidence=_evidence(),
        conflicts={"frontage_ft"},
    )

    component = snapshot.dimensions["land_lot_title"].components["frontage_depth"]

    assert component.evidence_status is EvidenceStatus.CONFLICT
    assert "frontage_ft" in component.unknown_fields
    assert snapshot.data_confidence_score < 100


def test_outdoor_noise_has_more_family_location_impact_than_indoor_mitigation():
    quiet = score_property(_strong_record(outdoor_noise_exposure=10), load_config("config/research.yaml"), evidence=_evidence())
    noisy = score_property(_strong_record(outdoor_noise_exposure=90), load_config("config/research.yaml"), evidence=_evidence())

    assert quiet.dimensions["family_location_noise"].points > noisy.dimensions["family_location_noise"].points
    assert quiet.live_score > noisy.live_score


def test_lot_area_alone_cannot_reach_full_land_score():
    large_only = _strong_record(
        lot_area_sqft=10000,
        lot_size_percentile_in_neighbourhood=95,
        frontage_ft=None,
        depth_ft=None,
        lot_shape=None,
        lane_access=None,
        buildability_score=None,
    )
    full = _strong_record()

    only_score = score_property(large_only, load_config("config/research.yaml"), evidence=_evidence())
    full_score = score_property(full, load_config("config/research.yaml"), evidence=_evidence())

    assert only_score.land_score < full_score.land_score


def test_north_vancouver_lot_area_without_local_percentile_is_not_scored_as_vancouver_size():
    record = _strong_record(
        municipality="District of North Vancouver",
        lot_size_percentile_in_neighbourhood=None,
        frontage_ft=None,
        depth_ft=None,
        lot_shape=None,
        lane_access=None,
        buildability_score=None,
    )
    snapshot = score_property(record, load_config("config/research.yaml"), evidence=_evidence())

    component = snapshot.dimensions["land_lot_title"].components["lot_area"]
    scarcity = snapshot.dimensions["land_lot_title"].components["land_scarcity"]
    assert component.evidence_status is EvidenceStatus.UNKNOWN
    assert scarcity.evidence_status is EvidenceStatus.UNKNOWN


def test_max_units_alone_cannot_reach_full_redevelopment_score():
    units_only = _strong_record(
        maximum_units=6,
        maximum_fsr=None,
        realistic_buildable_sqft=None,
        buildability_score=None,
        frontage_ft=None,
        depth_ft=None,
        lot_shape=None,
    )
    full = _strong_record()

    units_score = score_property(units_only, load_config("config/research.yaml"), evidence=_evidence())
    full_score = score_property(full, load_config("config/research.yaml"), evidence=_evidence())

    assert units_score.land_score < full_score.land_score


def test_independent_scores_follow_approved_composition():
    scores = calculate_independent_scores({
        "land_lot_title": 20,
        "price_safety_margin": 10,
        "current_house_utility": 15,
        "family_location_noise": 8,
        "school": 6,
        "redevelopment_option_value": 18,
        "resale_affordability": 4,
        "carrying_cost_risk": 4,
    })

    assert scores["live_score"] == 84.0
    assert scores["land_score"] == 95.0
    assert scores["investment_score"] == 82.25


def test_score_snapshot_has_reproducibility_metadata_and_opportunity_tags():
    snapshot = score_property(_strong_record(), load_config("config/research.yaml"), evidence=_evidence())

    assert snapshot.scoring_version == "research-v1"
    assert snapshot.config_hash
    assert "PRICE_DISLOCATION" in snapshot.opportunity_tags
    assert "LAND_VALUE" in snapshot.opportunity_tags
    assert 0 <= snapshot.land_to_product_score <= 100


def test_detached_baseline_always_includes_keep_duplex_and_multiplex():
    scenarios = build_baseline_scenarios(
        _strong_record(road_class=RoadClass.MAJOR_ARTERIAL_TRUCK, property_fronts_major_road=True)
    )
    scenario_types = {scenario.scenario_type for scenario in scenarios}

    assert scenario_types == {
        ScenarioType.KEEP_EXISTING_HOUSE,
        ScenarioType.DUPLEX,
        ScenarioType.MAX_REALISTIC_MULTIPLEX,
    }
    duplex = next(item for item in scenarios if item.scenario_type is ScenarioType.DUPLEX)
    assert "front/rear" in duplex.layout_options
    assert "side-by-side" in duplex.layout_options
    assert duplex.front_building_noise_buffer_hypothesis is True


def test_listing_fields_do_not_make_redevelopment_legally_permitted():
    unverified = build_baseline_scenarios(
        _strong_record(official_zoning_confirmed=False, maximum_units=6)
    )
    verified = build_baseline_scenarios(
        _strong_record(official_zoning_confirmed=True, maximum_units=6)
    )

    unverified_multiplex = next(
        item for item in unverified if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX
    )
    verified_multiplex = next(
        item for item in verified if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX
    )

    assert unverified_multiplex.legal_status != "PERMITTED_BY_OFFICIAL_BYLAW"
    assert unverified_multiplex.unit_count is None
    assert verified_multiplex.legal_status == "PERMITTED_BY_OFFICIAL_BYLAW"
    assert verified_multiplex.unit_count == 6


def test_unverified_unit_claims_do_not_contribute_entitlement_or_option_tags():
    record = _strong_record(
        official_zoning_confirmed=False,
        official_zoning_source_url=None,
        maximum_units=6,
        permitted_uses=["detached", "duplex", "multiple dwelling"],
    )
    snapshot = score_property(record, load_config("config/research.yaml"), evidence=_evidence())

    entitlement = snapshot.dimensions["redevelopment_option_value"].components["current_entitlement"]
    assert entitlement.evidence_status is EvidenceStatus.UNKNOWN
    assert "DUPLEX_OPTION" not in snapshot.opportunity_tags
    assert "MULTIPLEX_OPTION" not in snapshot.opportunity_tags


def test_explicit_zoning_override_cannot_bypass_official_evidence_boundary():
    scenarios = build_baseline_scenarios(
        _strong_record(
            official_zoning_confirmed=False,
            official_zoning_source_url=None,
            official_zoning_retrieved_at=None,
            maximum_units=6,
        ),
        official_zoning_confirmed=True,
    )

    multiplex = next(
        item for item in scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX
    )
    assert multiplex.legal_status != "PERMITTED_BY_OFFICIAL_BYLAW"
    assert multiplex.unit_count is None


def test_zoning_scoring_evidence_requires_a_bylaw_version():
    snapshot = score_property(
        _strong_record(bylaw_version=None),
        load_config("config/research.yaml"),
        evidence=_evidence(),
    )

    entitlement = snapshot.dimensions["redevelopment_option_value"].components["current_entitlement"]
    assert entitlement.evidence_status is EvidenceStatus.UNKNOWN


def test_official_multiplex_baseline_retains_two_to_six_unit_product_options():
    scenarios = build_baseline_scenarios(
        _strong_record(official_zoning_confirmed=True, maximum_units=6)
    )
    multiplex = next(
        item for item in scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX
    )

    assert [item["unit_count"] for item in multiplex.product_options] == [2, 3, 4, 5, 6]
    assert all(item["average_unit_size"] for item in multiplex.product_options)


def test_best_economic_product_can_prefer_fewer_larger_units():
    duplex = RedevelopmentScenario(
        property_id="product-case",
        scenario_type=ScenarioType.DUPLEX,
        legal_status="PERMITTED_BY_OFFICIAL_BYLAW",
        unit_count=2,
        average_unit_size=1700,
        estimated_resale_price=4_500_000,
        total_project_cost=1_700_000,
        product_market_fit=95,
        execution_complexity=20,
    )
    multiplex = RedevelopmentScenario(
        property_id="product-case",
        scenario_type=ScenarioType.MAX_REALISTIC_MULTIPLEX,
        legal_status="PERMITTED_BY_OFFICIAL_BYLAW",
        unit_count=6,
        average_unit_size=850,
        estimated_resale_price=4_800_000,
        total_project_cost=2_700_000,
        product_market_fit=55,
        execution_complexity=80,
    )

    assert select_best_economic_product([duplex, multiplex]) is ScenarioType.DUPLEX


def test_future_opportunity_cost_requires_complete_inputs():
    assert calculate_future_opportunity_cost(2_500_000, 4_600_000, 1_700_000) == 400_000
    assert calculate_future_opportunity_cost(2_500_000, None, 1_700_000) is None


def test_age_score_uses_explicit_as_of_date_for_replayable_runs():
    record = _strong_record(year_built=2020, effective_year=2020)
    config = load_config("config/research.yaml")
    early = score_property(
        record,
        config,
        evidence=_evidence(),
        as_of=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    late = score_property(
        record,
        config,
        evidence=_evidence(),
        as_of=datetime(2036, 8, 30, tzinfo=timezone.utc),
    )

    assert (
        early.dimensions["current_house_utility"].components["effective_age"].points
        > late.dimensions["current_house_utility"].components["effective_age"].points
    )
