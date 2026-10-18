from __future__ import annotations

import json
from datetime import date

from northshore_watchdog.research.location_sold_comps import (
    BENCHMARK_ADDRESSES,
    add_benchmark_records,
    build_physical_cohorts,
    extract_bca_sale_rows,
    load_sold_comp_sources,
    merge_sold_comps,
    normalize_sold_comp,
    summarize_sold_comp_quality,
    assign_local_area_neighbourhoods,
    assign_school_catchments,
    _premium_rows,
)


def _sale(**overrides):
    row = {
        "address": "123 Test Street, Vancouver, BC",
        "mls": "R1234567",
        "sale_price": 1_700_000,
        "sale_date": "2026-01-15",
        "municipality": "City of Vancouver",
        "property_type": "Detached House",
        "title": "Freehold",
        "neighbourhood": "Arbutus",
        "lot_area": 4_000,
        "lot_width": 33,
        "lot_depth": 121.2,
        "living_area": 1_900,
        "year_built": 1960,
        "BCA_land": 1_500_000,
        "BCA_improvement": 220_000,
        "elementary_catchment": "Test Elementary",
        "secondary_catchment": "Test Secondary",
        "road_class": "LOCAL_RESIDENTIAL",
        "zoning": "R1-1",
    }
    row.update(overrides)
    return row


def test_normalize_sold_comp_never_uses_asking_as_sale_price():
    result = normalize_sold_comp(
        {
            "address": "1 Test Street",
            "asking_price": 1_700_000,
            "sale_date": "2026-01-01",
            "municipality": "Vancouver",
            "property_type": "House",
            "ownership_type": "Freehold",
        },
        source_id="fixture",
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    assert result["sale_price"] is None
    assert result["last_asking"] == 1_700_000
    assert result["statistical_eligible"] is False
    assert "MISSING_SALE_PRICE" in result["exclusion_reasons"]


def test_normalize_sold_comp_maps_required_aliases_and_marks_eligible():
    result = normalize_sold_comp(
        _sale(),
        source_id="fixture",
        source_type="MLS",
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    assert result["mls"] == "R1234567"
    assert result["sale_price"] == 1_700_000
    assert result["sale_date"] == "2026-01-15"
    assert result["lot_area_sqft"] == 4_000
    assert result["frontage_ft"] == 33
    assert result["living_area_sqft"] == 1_900
    assert result["bca_improvement"] == 220_000
    assert result["statistical_eligible"] is True
    assert result["transaction_key"] == "mls:r1234567:2026-01-15"


def test_normalize_bca_detail_derives_physical_area_fields_from_official_parts():
    result = normalize_sold_comp(
        _sale(
            source_type="BC_ASSESSMENT",
            lot_area=14_000,
            lot_width=33,
            lot_depth=122,
            bca_first_floor_area_sqft=1_100,
            bca_second_floor_area_sqft=700,
            living_area_sqft=None,
            living_area=None,
        ),
        source_id="bca",
    )

    assert result["lot_area_sqft"] == 4026
    assert result["lot_area_source"] == "BCA_PROPERTY_INFORMATION_DIMENSIONS"
    assert result["living_area_sqft"] == 1800
    assert result["living_area_source"] == "BCA_PROPERTY_INFORMATION_FLOOR_AREAS"


def test_normalize_sold_comp_preserves_source_observation_metadata():
    result = normalize_sold_comp(
        _sale(
            observed_at="2026-09-05T10:00:00Z",
            retrieved_at="2026-09-05T10:01:00Z",
            source_version="bca-snapshot-v1",
        ),
        source_id="bca:one",
        source_type="BC_ASSESSMENT",
    )

    assert result["source_id"] == "bca:one"
    assert result["observed_at"] == "2026-09-05T10:00:00Z"
    assert result["retrieved_at"] == "2026-09-05T10:01:00Z"
    assert result["source_version"] == "bca-snapshot-v1"


def test_load_sources_accepts_json_jsonl_and_csv(tmp_path):
    json_path = tmp_path / "sales.json"
    json_path.write_text(json.dumps({"sales": [_sale(mls="R1")]}), encoding="utf-8")
    jsonl_path = tmp_path / "sales.jsonl"
    jsonl_path.write_text(json.dumps(_sale(mls="R2")) + "\n", encoding="utf-8")
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "address,mls,sale_price,sale_date,municipality,property_type,title\n"
        "3 Test Street,R3,1800000,2026-02-01,Vancouver,House,Freehold\n",
        encoding="utf-8",
    )

    rows = load_sold_comp_sources(
        [json_path, jsonl_path, csv_path],
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    assert {row["mls"] for row in rows} == {"R1", "R2", "R3"}
    assert all(row["source_id"] for row in rows)


def test_merge_sold_comps_combines_matching_sources():
    left = normalize_sold_comp(_sale(source_url="local://one"), source_id="one")
    right = normalize_sold_comp(_sale(source_url="local://two"), source_id="two")

    merged = merge_sold_comps([left, right])

    assert len(merged) == 1
    assert merged[0]["source_ids"] == ["one", "two"]
    assert merged[0]["data_conflict"] is False
    assert merged[0]["statistical_eligible"] is True


def test_merge_bca_geometry_and_property_detail_prefers_detail_without_false_conflict():
    geometry = normalize_sold_comp(
        _sale(
            source_type="BC_ASSESSMENT",
            bca_afp_oid="A-1",
            lot_area_sqft=14_000,
            lot_area_source="BC_ASSESSMENT_ARCGIS_SHAPE_AREA",
            lot_width=None,
            lot_depth=None,
            living_area_sqft=None,
            bca_detail_status="NOT_SELECTED_PRICE_BAND",
        ),
        source_id="bca-map",
    )
    detail = normalize_sold_comp(
        _sale(
            source_type="BC_ASSESSMENT",
            bca_afp_oid="A-1",
            lot_area=4_000,
            lot_area_source="BCA_PROPERTY_INFORMATION_DIMENSIONS",
            living_area_sqft=1_900,
            living_area_source="BCA_PROPERTY_INFORMATION_FLOOR_AREAS",
            bca_detail_status="CONFIRMED",
        ),
        source_id="bca-detail",
    )

    merged = merge_sold_comps([geometry, detail])

    assert len(merged) == 1
    assert merged[0]["lot_area_sqft"] == 3999.6
    assert merged[0]["living_area_sqft"] == 1_900
    assert merged[0]["bca_detail_status"] == "CONFIRMED"
    assert merged[0]["data_conflict"] is False


def test_merge_sold_comps_isolates_conflicting_sale_prices():
    left = normalize_sold_comp(_sale(sale_price=1_700_000), source_id="one")
    right = normalize_sold_comp(_sale(sale_price=1_790_000), source_id="two")

    merged = merge_sold_comps([left, right])

    assert len(merged) == 1
    assert merged[0]["data_conflict"] is True
    assert merged[0]["statistical_eligible"] is False
    assert "SALE_PRICE_CONFLICT" in merged[0]["conflict_fields"]
    assert merged[0]["conflicting_values"]["sale_price"] == [1_700_000, 1_790_000]


def test_merge_sold_comps_preserves_explicit_research_window():
    row = normalize_sold_comp(
        _sale(sale_date="2024-01-15"),
        source_id="old",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 9, 5),
    )

    merged = merge_sold_comps(
        [row],
        start_date=date(2025, 1, 1),
        end_date=date(2026, 9, 5),
    )

    assert merged[0]["statistical_eligible"] is False
    assert "OUTSIDE_DATE_WINDOW" in merged[0]["exclusion_reasons"]


def test_required_benchmark_registry_contains_all_ten_addresses():
    expected = {
        "2876 W 18TH AVENUE",
        "3974 W 10TH AVENUE",
        "333 E 13TH AVENUE",
        "486 E KING EDWARD AVENUE",
        "4561 NANAIMO STREET",
        "3555 TRAFALGAR STREET",
        "2887 W 24TH AVENUE",
        "440-442 E 10TH AVENUE",
        "21 E 41ST AVENUE",
        "41 E 41ST AVENUE",
    }
    assert set(BENCHMARK_ADDRESSES) == expected


def test_benchmark_public_transaction_mls_is_kept_separate_from_current_listing_mls():
    rows = add_benchmark_records([])

    row = next(row for row in rows if row["address"] == "2876 W 18TH AVENUE")
    assert row["mls"] == "R3117599"
    assert row["transaction_mls_status"] == "VERIFIED_PUBLIC_PAGE"
    assert row["current_listing_mls"] is None
    assert row["transaction_mls_source_url"].endswith("2876-w-18th-avenue-vancouver")
    assert row["statistical_eligible"] is False


def test_quality_summary_counts_scope_conflicts_and_field_coverage():
    eligible = normalize_sold_comp(_sale(), source_id="eligible")
    outside = normalize_sold_comp(
        _sale(mls="R2", municipality="City of North Vancouver"), source_id="outside"
    )
    conflict = normalize_sold_comp(_sale(mls="R3"), source_id="conflict")
    conflict["data_conflict"] = True
    conflict["statistical_eligible"] = False
    conflict["exclusion_reasons"] = ["DATA_CONFLICT"]

    summary = summarize_sold_comp_quality([eligible, outside, conflict])

    assert summary["total_observations"] == 3
    assert summary["eligible_sales"] == 1
    assert summary["conflict_count"] == 1
    assert summary["outside_target_scope_count"] == 1
    assert summary["samples_by_neighbourhood"] == {"Arbutus": 1}
    assert summary["samples_by_year"] == {"2026": 1}
    assert summary["coverage"]["bca"] == 1.0
    assert summary["coverage"]["school"] == 1.0
    assert summary["coverage"]["road"] == 1.0
    assert summary["coverage"]["zoning"] == 1.0


def test_physical_cohort_uses_only_eligible_standard_old_house_sales():
    standard = normalize_sold_comp(_sale(), source_id="standard")
    too_wide = normalize_sold_comp(_sale(mls="R2", lot_width=50), source_id="wide")
    missing_title = normalize_sold_comp(_sale(mls="R3", title=None), source_id="unknown-title")

    rows = build_physical_cohorts([standard, too_wide, missing_title])

    cohort = [row for row in rows if row["cohort"] == "OLD_HOUSE_STANDARD_LOT"]
    assert [row["mls"] for row in cohort] == ["R1234567"]


def test_extract_bca_sale_rows_preserves_each_official_sale_event(tmp_path):
    path = tmp_path / "regional.json"
    path.write_text(json.dumps({
        "candidates": [{
            "address": "3218 W 6TH AVENUE",
            "municipality": "Vancouver",
            "neighbourhood": "Kitsilano",
            "property_type": "Single Family",
            "ownership_type": "Freehold",
            "mls_number": "R3160005",
            "lot_area_sqft": 4_000,
            "finished_sqft": 1_900,
            "year_built": 1955,
            "raw": {
                "official_previous_sale_dates": ["2025-10-08T00:00:00Z", "2021-05-01T00:00:00Z"],
                "official_previous_sale_prices": [1_700_000, 1_200_000],
                "pid": "012-345-678",
            },
        }]
    }), encoding="utf-8")

    rows = extract_bca_sale_rows(
        path,
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    assert len(rows) == 1
    assert rows[0]["sale_price"] == 1_700_000
    assert rows[0]["sale_date"] == "2025-10-08"
    assert rows[0]["source_type"] == "BC_ASSESSMENT"
    assert rows[0]["title"] == "Freehold"
    assert rows[0]["pid"] == "012-345-678"
    assert rows[0]["mls"] is None
    assert rows[0]["current_listing_mls"] == "R3160005"
    assert rows[0]["transaction_mls_status"] == "UNVERIFIED_CURRENT_LISTING_ONLY"


def test_assign_local_area_neighbourhoods_uses_saved_official_polygon(tmp_path):
    boundary_path = tmp_path / "local-areas.geojson"
    boundary_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "Test Area"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [-123.2, 49.2], [-123.2, 49.3], [-123.1, 49.3],
                [-123.1, 49.2], [-123.2, 49.2]
            ]]}},
        ],
    }), encoding="utf-8")
    rows = [{"record_id": "one", "longitude": -123.15, "latitude": 49.25, "neighbourhood": None}]

    result = assign_local_area_neighbourhoods(rows, boundary_path=boundary_path)

    assert result[0]["neighbourhood"] == "Test Area"
    assert result[0]["neighbourhood_source"] == "CITY_OF_VANCOUVER_LOCAL_AREA_BOUNDARY"
    assert result[0]["neighbourhood_boundary_status"] == "CONFIRMED"


def test_assign_school_catchments_uses_saved_official_web_mercator_boundary(tmp_path):
    boundary_path = tmp_path / "school-boundaries.json"
    boundary_path.write_text(json.dumps({
        "boundaries": {
            "elementary": [{
                "school_name": "Test Elementary",
                "source_url": "https://school.example/elementary.zip",
                "rings": [[[-13720000, 6310000], [-13720000, 6325000], [-13690000, 6325000], [-13690000, 6310000], [-13720000, 6310000]]],
            }],
            "secondary": [],
        },
    }), encoding="utf-8")
    rows = [{"record_id": "one", "longitude": -123.15, "latitude": 49.25, "elementary_catchment": None}]

    result = assign_school_catchments(rows, boundary_path=boundary_path)

    assert result[0]["elementary_catchment"] == "Test Elementary"
    assert result[0]["school_boundary_status"] == "CONFIRMED"
    assert result[0]["school_source_url"] == "https://school.example/elementary.zip"


def test_school_pair_diagnostic_does_not_promote_unsigned_delta_to_premium():
    rows = []
    for index in range(7):
        rows.append(normalize_sold_comp(_sale(
            mls=f"R{index}",
            address=f"{index + 1} Test Street, Vancouver, BC",
            sale_price=1_700_000 + index * 10_000,
            sale_date="2026-01-15",
            elementary_catchment="School A",
        ), source_id=f"a-{index}"))
        rows.append(normalize_sold_comp(_sale(
            mls=f"S{index}",
            address=f"{index + 101} Test Street, Vancouver, BC",
            sale_price=1_750_000 + index * 10_000,
            sale_date="2026-01-15",
            elementary_catchment="School B",
        ), source_id=f"b-{index}"))

    result = _premium_rows(rows, "school", "school")

    assert result[0]["value"] == 0
    assert result[0]["applied_value"] == 0
    assert result[0]["automatic_rankable"] is False
    assert result[0]["method"] == "DIRECTION_NOT_IDENTIFIED"
    assert "diagnostic median absolute pair delta" in result[0]["notes"]
