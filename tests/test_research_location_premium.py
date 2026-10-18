from __future__ import annotations

from datetime import date, timedelta

from northshore_watchdog.research.location_premium import estimate_initial_location_premiums


def _sale(index: int, *, neighbourhood: str, price: float, road_class: str = "E") -> dict:
    return {
        "record_id": f"sale-{index}",
        "statistical_eligible": True,
        "address": f"{index} Test Street, Vancouver",
        "sale_price": price,
        "sale_date": date(2026, 1, 1) + timedelta(days=index),
        "neighbourhood": neighbourhood,
        "road_class": road_class,
        "zoning": "R1-1",
        "lot_area_sqft": 4_000,
        "frontage_ft": 33,
        "depth_ft": 122,
        "living_area_sqft": 1_800,
        "year_built": 1960,
        "bca_improvement": 150_000,
        "beds": 3,
        "baths": 2,
    }


def test_initial_model_estimates_directional_neighbourhood_and_road_values():
    rows = []
    for index in range(12):
        rows.append(_sale(index, neighbourhood="Reference", price=1_800_000, road_class="E"))
        rows.append(_sale(index + 100, neighbourhood="Premium", price=2_100_000, road_class="E"))
    for index in range(6):
        rows.append(_sale(index + 200, neighbourhood="Reference", price=1_650_000, road_class="B"))

    result = estimate_initial_location_premiums(rows)
    area = {row["segment"]: row for row in result["estimates"] if row["component"] == "area_location_residual"}
    road = {row["segment"]: row for row in result["estimates"] if row["component"] == "road_adjustment"}

    assert area["Premium"]["value"] > area["Reference"]["value"]
    assert road["B"]["value"] < road["E"]["value"]
    assert area["Premium"]["automatic_rankable"] is False
    assert result["diagnostics"]["method"] == "INITIAL_RIDGE_CONTROLLED_RESIDUAL"


def test_initial_model_does_not_invent_unidentified_micro_premiums():
    result = estimate_initial_location_premiums([_sale(1, neighbourhood="Only", price=1_800_000)])

    unknown = [
        row for row in result["estimates"]
        if row["component"] in {"school", "park", "view", "beach", "transit"}
    ]
    assert len(unknown) == 5
    assert all(row["value"] == 0 for row in unknown)
    assert all(row["automatic_rankable"] is False for row in unknown)


def test_initial_model_uses_vancouver_standard_property_v2_counterfactual():
    rows = []
    for index in range(12):
        rows.append(_sale(index, neighbourhood="Reference", price=1_800_000))
        rows.append(_sale(index + 100, neighbourhood="Premium", price=2_100_000))

    result = estimate_initial_location_premiums(rows, include_road=True)
    diagnostics = result["diagnostics"]
    profile = diagnostics["standard_profile"]

    assert profile["profile_name"] == "VANCOUVER_STANDARD_PROPERTY_V2"
    assert profile["property_type"] == "DETACHED_FREEHOLD"
    assert profile["lot_area_sqft"] == 4026
    assert profile["frontage_ft"] == 33
    assert profile["depth_ft"] == 122
    assert profile["living_area_sqft"] == 1700
    assert profile["beds"] == 3
    assert profile["baths"] == 2
    assert profile["year_built"] == 1960
    assert profile["year_built_sensitivity"] == [1940, 1960, 1980]
    assert profile["zoning_group"] == "R"
    assert profile["road_class"] == "E"
    assert profile["view"] == "NONE"
    assert profile["park_facing"] is False
    assert profile["waterfront"] is False


def test_initial_model_reports_neutral_and_area_standard_values_with_age_sensitivity():
    rows = []
    for index in range(12):
        rows.append(_sale(index, neighbourhood="Reference", price=1_800_000))
        rows.append(_sale(index + 100, neighbourhood="Premium", price=2_100_000))

    result = estimate_initial_location_premiums(rows, include_road=True)
    diagnostics = result["diagnostics"]
    area = {
        row["segment"]: row
        for row in result["estimates"]
        if row["component"] == "area_location_residual"
    }

    assert diagnostics["neutral_standard_value"] > 0
    assert set(diagnostics["neutral_standard_value_age_sensitivity"]) == {"1940", "1960", "1980"}
    assert all(value > 0 for value in diagnostics["neutral_standard_value_age_sensitivity"].values())
    premium = area["Premium"]
    assert premium["standard_property_value"] > diagnostics["neutral_standard_value"]
    assert abs(
        premium["standard_property_value"]
        - diagnostics["neutral_standard_value"]
        - premium["value"]
    ) <= 2_000
