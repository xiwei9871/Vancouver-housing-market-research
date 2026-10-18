from __future__ import annotations

from datetime import date

from northshore_watchdog.research.location_valuation import (
    estimate_premium,
    find_matched_pairs,
    fit_hedonic_model,
    normalize_price,
    sum_location_value,
)


def test_unproven_prior_is_not_applied_to_automatic_value():
    estimate = estimate_premium(
        component="school",
        value=200_000,
        method="CONFIG_PRIOR",
        sample_size=0,
        confidence="PRIOR_ONLY",
    )

    assert estimate.applied_value == 0
    assert estimate.value == 200_000
    assert estimate.automatic_rankable is False


def test_normalized_lot_basis_separates_location_from_local_ralv():
    result = normalize_price(
        asking=1_800_000,
        existing_house_value=400_000,
        school_value=150_000,
        area_value=250_000,
        micro_adjustments={"park": 50_000, "road": -25_000},
        lot_area_sqft=4_000,
    )

    assert result.market_land_basis == 1_400_000
    assert result.total_location_value == 425_000
    assert result.normalized_physical_lot_basis == 975_000
    assert result.normalized_lot_price_per_sqft == 243.75
    assert result.ralv is None


def test_location_value_adds_school_and_non_school_components_once():
    value = sum_location_value(
        school_value=189_500,
        area_value=220_000,
        micro_values={"view": 40_000, "road": -30_000},
    )

    assert value == 419_500


def test_matched_pairs_require_same_physical_context_and_differing_factor():
    rows = [
        {
            "id": "a",
            "sold_price": 1_800_000,
            "sold_date": date(2026, 1, 10),
            "neighbourhood": "Arbutus",
            "lot_area_sqft": 4_000,
            "frontage_ft": 33,
            "house_size_sqft": 2_000,
            "year_built": 1955,
            "zoning": "R1-1",
            "school": "A",
        },
        {
            "id": "b",
            "sold_price": 1_650_000,
            "sold_date": date(2026, 2, 1),
            "neighbourhood": "Arbutus",
            "lot_area_sqft": 4_050,
            "frontage_ft": 34,
            "house_size_sqft": 1_950,
            "year_built": 1960,
            "zoning": "R1-1",
            "school": "B",
        },
        {
            "id": "c",
            "sold_price": 2_500_000,
            "sold_date": date(2026, 2, 1),
            "neighbourhood": "Renfrew",
            "lot_area_sqft": 4_000,
            "frontage_ft": 33,
            "house_size_sqft": 2_000,
            "year_built": 1955,
            "zoning": "R1-1",
            "school": "B",
        },
    ]

    pairs = find_matched_pairs(rows, factor="school", date_window_days=183)

    assert len(pairs) == 1
    assert pairs[0].factor == "school"
    assert pairs[0].matched is True
    assert {pairs[0].left_id, pairs[0].right_id} == {"a", "b"}


def test_hedonic_model_reports_insufficient_sample_without_fabricating_coefficients():
    rows = [
        {
            "id": str(index),
            "sold_price": 1_000_000 + index * 10_000,
            "sold_date": date(2026, 1, 1),
            "lot_area_sqft": 4_000,
            "house_size_sqft": 1_800,
            "neighbourhood": "Arbutus",
        }
        for index in range(3)
    ]

    result = fit_hedonic_model(rows, minimum_rows=20)

    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.coefficients == {}
