from datetime import datetime, timezone
from pathlib import Path

import pytest

from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.strategy_v2_shadow import (
    build_acquisition_ledger,
    build_development_scenarios,
    development_potential_components,
    build_holding_ledger,
    build_location_ledger,
    classify_asset,
    evaluate_gates,
    make_calibration_split,
)


def _card(**values):
    defaults = {
        "source_name": "realtor-ca-native",
        "mls_number": "R123",
        "municipality": "Vancouver",
        "asking": 1_500_000,
        "bedrooms": 3,
        "bathrooms": 2,
        "finished_sqft": 1700,
        "lot_area_sqft": 4026,
        "lot_dimensions": "33 x 122 ft",
        "frontage_ft": 33,
        "depth_ft": 122,
        "property_type": "Single Family",
        "ownership_type": "Freehold",
        "listing_status": "active",
        "address": "100 MAIN STREET",
        "source_url": "https://www.realtor.ca/real-estate/123/100-main-street-vancouver",
        "year_built": 1960,
        "neighbourhood": "Mount Pleasant",
        "description": "",
        "observed_at": datetime(2026, 9, 9, tzinfo=timezone.utc),
        "raw": {
            "official_assessment_total": 1_700_000,
            "official_assessment_land": 1_200_000,
            "official_assessment_improvement": 500_000,
            "official_assessment_year": 2026,
            "official_current_zoning": "R1-1",
            "official_zoning": "R1-1",
            "official_frontage_ft": 33,
            "official_depth_ft": 122,
            "official_elementary_catchment": "Example Elementary",
            "official_secondary_catchment": "Example Secondary",
            "official_road_class": "E",
            "official_distance_to_major_road_m": 300,
            "official_property_fronts_major_road": False,
            "_official_evidence": [],
        },
    }
    defaults.update(values)
    return DiscoveryCard(**defaults)


def test_partial_interest_is_special_even_when_lot_is_large():
    card = _card(
        address="SPECIAL STREET",
        ownership_type="Partial Interest",
        lot_area_sqft=6000,
    )
    classification = classify_asset(card)
    gates = evaluate_gates(card)
    assert classification.primary_track == "SPECIAL_SITUATION"
    assert any(item.status == "FAIL" for item in gates)


def test_large_lot_does_not_create_land_development_without_entitlement():
    card = _card(
        address="LARGE STREET",
        lot_area_sqft=6000,
        raw={"official_current_zoning": "UNKNOWN"},
    )
    classification = classify_asset(card)
    assert classification.primary_track != "LAND_DEVELOPMENT"


def test_classification_uses_supplied_zoning_rules_path(tmp_path: Path):
    card = _card(
        address="CUSTOM RULE STREET",
        bedrooms=2,
        bathrooms=1,
        finished_sqft=1200,
        raw={"official_current_zoning": "CUSTOM-1"},
    )
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        """rules:
  CUSTOM-1:
    numeric_status: OFFICIAL_CONFIRMED
    maximum_fsr: 1.0
    maximum_units: 4
    duplex_permitted: true
    rear_access_required: false
""",
        encoding="utf-8",
    )
    classification = classify_asset(card, rules_path=rules)
    assert classification.primary_track == "LAND_DEVELOPMENT"


def test_holding_ledger_uses_survival_capex_without_a_score_cap():
    card = _card(
        asking=1_875_000,
        year_built=1947,
        finished_sqft=1985,
        description="Renovation potential. Buyer to verify systems.",
    )
    holding = build_holding_ledger(card)
    assert holding.survival_capex_base is not None
    assert holding.survival_capex_base >= 100_000
    assert holding.cash_holding_basis_base == 1_875_000 + holding.survival_capex_base
    assert not hasattr(holding, "current_use_score_cap")


def test_explicit_updates_reduce_only_matching_survival_reserves():
    card = _card(
        mls_number="R4561",
        description="Recent updates include double glazed windows, roof, hot water tank and fire sprinkler system.",
        year_built=1950,
    )
    holding = build_holding_ledger(card)
    assert holding.survival_capex_base is not None
    assert holding.survival_capex_base < 120_000
    assert holding.survival_capex_base > 0
    assert "ROOF" in holding.updated_components
    assert "WINDOWS" in holding.updated_components


def test_r1_1_unknown_rear_access_is_conditional_not_base():
    card = _card(
        mls_number="R5454",
        address="5454 MELBOURNE STREET",
        asking=1_475_000,
        bedrooms=3,
        bathrooms=1,
        finished_sqft=1657,
        lot_area_sqft=5525,
        lot_dimensions="50 x 110.5 ft",
        frontage_ft=50,
        depth_ft=110.5,
        year_built=1949,
        description="Prime 50X110.5 R1-1 zoned lot overlooking Foster Park.",
        raw={
            "official_current_zoning": "R1-1",
            "official_zoning": "R1-1",
            "official_frontage_ft": 50,
            "official_depth_ft": 110.5,
        },
    )
    scenarios = build_development_scenarios(card)
    base = next(item for item in scenarios if item.scenario_name == "BASE")
    conditional = next(item for item in scenarios if item.scenario_name == "CONDITIONAL")
    assert base.ralv is None
    assert base.access_status in {"UNKNOWN", "REVIEW_REQUIRED"}
    assert any("ACCESS" in item for item in base.blocking_conditions)
    assert conditional.unit_count is not None
    assert conditional.unit_count >= 4
    assert conditional.ralv is None


def test_below_standard_premium_lot_is_not_promoted_to_normal_home_rebuild():
    card = _card(
        address="2413 COLLINGWOOD STREET",
        mls_number="R2413",
        neighbourhood="Kitsilano",
        lot_area_sqft=2970,
        lot_dimensions="45 x 66 ft",
        frontage_ft=45,
        depth_ft=66,
        description="Land value opportunity in a premium West Side location; build a triplex.",
    )
    classification = classify_asset(card)
    assert classification.primary_track == "PREMIUM_SMALL_LOT"
    assert "SUBSTANDARD_LOT_WITH_LOCAL_PRODUCT_CONTEXT" in classification.basis


def test_standard_reference_lot_is_in_premium_lot_cohort():
    card = _card(
        address="3910 W 10TH STYLE STREET",
        neighbourhood="West Point Grey",
        lot_area_sqft=4026,
        lot_dimensions="33 x 122 ft",
        frontage_ft=33,
        depth_ft=122,
    )
    classification = classify_asset(card)

    assert classification.primary_track == "HOME_REBUILD"
    assert "PREMIUM_LOT" in classification.cohorts
    assert classification.lot_class == "STANDARD_REFERENCE"


def test_near_standard_premium_lot_is_in_premium_lot_cohort():
    card = _card(
        address="3910 W 10TH AVENUE",
        neighbourhood="West Point Grey",
        lot_area_sqft=4042.5,
        lot_dimensions="33 x 122.5 ft",
        frontage_ft=33,
        depth_ft=122.5,
    )
    classification = classify_asset(card)

    assert classification.primary_track == "HOME_REBUILD"
    assert "PREMIUM_LOT" in classification.cohorts
    assert classification.lot_class == "STANDARD_REFERENCE"


def test_development_potential_exposes_geometry_and_entitlement_components():
    smaller_rt7 = _card(
        address="3218 W 6TH AVENUE",
        neighbourhood="Kitsilano",
        lot_area_sqft=3629,
        lot_dimensions="33 x 110 ft",
        frontage_ft=33,
        depth_ft=110,
        raw={"official_current_zoning": "RT-7"},
    )
    standard_r1 = _card(
        address="3910 W 10TH AVENUE",
        neighbourhood="West Point Grey",
        lot_area_sqft=4042.5,
        lot_dimensions="33 x 122.5 ft",
        frontage_ft=33,
        depth_ft=122.5,
        raw={"official_current_zoning": "R1-1"},
    )

    rt7 = development_potential_components(smaller_rt7)
    r1 = development_potential_components(standard_r1)

    assert rt7["entitlement_score"] > r1["entitlement_score"]
    assert r1["geometry_score"] > rt7["geometry_score"]
    assert rt7["development_potential_proxy"] > r1["development_potential_proxy"]


def test_location_ledger_keeps_walk_score_unknown_and_road_as_fact():
    card = _card(
        raw={
            "official_current_zoning": "RT-11",
            "official_road_class": "B",
            "official_distance_to_major_road_m": 34,
            "official_property_fronts_major_road": True,
            "official_nearest_major_road": "NANAIMO ST",
            "official_elementary_catchment": "Norquay",
            "official_secondary_catchment": "Gladstone Secondary",
        },
    )
    location = build_location_ledger(card, legacy_row={"road_category": "ARTERIAL_BUS_TRUCK"})
    assert location.external_provider["walk_score_status"] == "UNKNOWN"
    assert location.parcel_facts["property_fronts_major_road"] is True
    assert location.road_adjustment["category"] == "ORDINARY_ARTERIAL"
    assert location.road_adjustment["category_source"] == "OFFICIAL_ROAD_CLASS"
    assert location.road_adjustment["discount_low_pct"] == pytest.approx(0.05)
    assert location.road_adjustment["exposure_factor"] == pytest.approx(1.0)


def test_unknown_road_distance_does_not_become_zero_exposure():
    card = _card(
        raw={
            "official_current_zoning": "RT-11",
            "official_road_class": "B",
            "official_property_fronts_major_road": False,
        },
    )
    location = build_location_ledger(card)
    assert location.road_adjustment["exposure_factor"] is None
    assert location.road_adjustment["effective_discount_base_pct"] is None


def test_acquisition_ledger_keeps_bca_and_location_normalization_separate():
    card = _card()
    holding = build_holding_ledger(card)
    location = build_location_ledger(card, legacy_row={"location_residual_cad": 200_000})
    acquisition = build_acquisition_ledger(
        card,
        holding=holding,
        scenarios=[],
        location=location,
        legacy_row={"location_residual_cad": 200_000},
    )
    assert acquisition.market_land_basis == 1_000_000
    assert acquisition.normalized_physical_lot_basis == 800_000
    assert acquisition.normalized_lot_basis_per_sqft == pytest.approx(198.71, abs=0.01)
    assert acquisition.base_cash_mos is None


def test_calibration_split_is_fixed_and_complete():
    keys = [f"mls:R{i}" for i in range(166)]
    split = make_calibration_split(keys)
    assert len(split["calibration_keys"]) == 120
    assert len(split["blind_test_keys"]) == 46
    assert set(split["calibration_keys"]).isdisjoint(split["blind_test_keys"])
    assert split == make_calibration_split(keys)
