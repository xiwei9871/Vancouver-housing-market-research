from __future__ import annotations

from datetime import date, timedelta

from northshore_watchdog.research.location_premium_stability import (
    estimate_location_premium_stability,
    render_location_premium_stability_markdown,
)


def _sale(index: int, *, neighbourhood: str, price: float, days_ago: int = 30, lot: float = 4000) -> dict:
    return {
        "record_id": f"sale-{index}",
        "statistical_eligible": True,
        "address": f"{index} Test Street, Vancouver",
        "sale_price": price,
        "sale_date": date(2026, 9, 6) - timedelta(days=days_ago),
        "neighbourhood": neighbourhood,
        "road_class": "E",
        "zoning": "R1-1",
        "lot_area_sqft": lot,
        "frontage_ft": 33,
        "depth_ft": 122,
        "living_area_sqft": 1800,
        "year_built": 1960,
        "bca_improvement": 150000,
        "beds": 3,
        "baths": 2,
    }


def test_stability_compares_windows_and_does_not_apply_combined_residual():
    rows = []
    for index in range(30):
        rows.append(_sale(index, neighbourhood="Reference", price=1_800_000, days_ago=index * 20))
        rows.append(_sale(index + 100, neighbourhood="Premium", price=2_100_000, days_ago=index * 20))

    result = estimate_location_premium_stability(rows)
    assert result["method"] == "COMBINED_LOCATION_RESIDUAL_SENSITIVITY"
    assert result["automatic_application"] is False
    assert {row["analysis_variant"] for row in result["rows"]} >= {
        "FULL_36M",
        "RECENT_24M",
        "RECENT_12M",
    }
    summary = [
        row for row in result["rows"]
        if row["row_type"] == "SUMMARY" and row["segment"] == "Premium"
    ][0]
    assert summary["sample_size_full_36m"] >= 20
    assert summary["sensitivity_low"] <= summary["sensitivity_high"]
    assert summary["applied_value"] == 0
    assert summary["school_split"] is False


def test_old_house_cohort_is_reported_as_a_sensitivity_variant():
    rows = []
    for index in range(15):
        rows.extend([
            _sale(index, neighbourhood="Reference", price=1_800_000, days_ago=index * 40, lot=4000),
            _sale(index + 100, neighbourhood="Premium", price=2_100_000, days_ago=index * 40, lot=4000),
        ])
    rows.append(_sale(999, neighbourhood="Premium", price=2_100_000, days_ago=30, lot=2500))

    result = estimate_location_premium_stability(rows)
    cohort_rows = [row for row in result["rows"] if row["analysis_variant"] == "OLD_HOUSE_STANDARD_LOT"]
    assert cohort_rows
    assert all(row["cohort"] == "OLD_HOUSE_STANDARD_LOT" for row in cohort_rows)
    assert all(row["applied_value"] == 0 for row in cohort_rows)


def test_stability_markdown_identifies_combined_residual_and_unknown_evidence():
    result = estimate_location_premium_stability([])
    markdown = render_location_premium_stability_markdown(result, version="V1.3")
    assert "combined location residual" in markdown.lower()
    assert "School is not separately priced" in markdown
    assert "INSUFFICIENT_SAMPLE" in markdown


def test_stability_uses_one_fixed_standard_profile_and_reports_standard_values():
    rows = []
    for index in range(30):
        rows.append(_sale(index, neighbourhood="Reference", price=1_800_000, days_ago=index * 20))
        rows.append(_sale(index + 100, neighbourhood="Premium", price=2_100_000, days_ago=index * 20))

    result = estimate_location_premium_stability(rows)
    diagnostics = result["diagnostics"]
    summary = [
        row for row in result["rows"]
        if row["row_type"] == "SUMMARY" and row["segment"] == "Premium"
    ][0]

    assert diagnostics["standard_property_profile"]["profile_name"] == "VANCOUVER_STANDARD_PROPERTY_V2"
    assert diagnostics["standard_property_profile"]["lot_area_sqft"] == 4026
    assert diagnostics["neutral_standard_value"] > 0
    assert set(diagnostics["neutral_standard_value_age_sensitivity"]) == {"1940", "1960", "1980"}
    assert summary["standard_property_value"] == diagnostics["neutral_standard_value"] + summary["value"]
    assert summary["standard_property_value_low"] == diagnostics["neutral_standard_value"] + summary["sensitivity_low"]
    assert summary["standard_property_value_high"] == diagnostics["neutral_standard_value"] + summary["sensitivity_high"]

    markdown = render_location_premium_stability_markdown(result, version="V1.4")
    assert "VANCOUVER_STANDARD_PROPERTY_V2" in markdown
    assert "4,026 sqft" in markdown
    assert "1,700 sqft" in markdown
    assert "Neutral standard value" in markdown
