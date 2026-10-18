from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.research.bca_arcgis_sales import (
    build_bca_sales_where,
    enrich_bca_sales_details,
    fetch_bca_arcgis_sales,
    merge_bca_sales_spatial_snapshot,
    parse_bca_sales_features,
)


def test_build_bca_sales_where_contains_research_scope_without_listing_filter():
    where = build_bca_sales_where(
        start_date="2023-09-06",
        end_date="2026-09-05",
        min_sale_price=1_000_000,
        max_sale_price=3_500_000,
    )

    assert "IS_STRATA = 'N'" in where
    assert "DESCRIPTION LIKE '%house%'" in where
    assert "SALE_PRICE >= 1000000" in where
    assert "SALE_PRICE <= 3500000" in where
    assert "LAST_SALE_DATE >= DATE '2023-09-06'" in where
    assert "LAST_SALE_DATE <= DATE '2026-09-05'" in where


def test_parse_bca_sales_features_normalizes_city_vancouver_rows_and_excludes_other_city():
    rows = parse_bca_sales_features(
        {
            "features": [
                {
                    "attributes": {
                        "AFP_OID": "200001",
                        "OID_EVBC": "A00000003A",
                        "ADDRESS": "123 TEST ST VANCOUVER",
                        "DESCRIPTION": "2 STY house - Standard",
                        "TOTAL_ASSESSED": 2_000_000,
                        "TOTAL_LAND": 1_900_000,
                        "TOTAL_BUILDING": 100_000,
                        "LAST_SALE_DATE": 1736467200000,
                        "SALE_PRICE": 1_800_000,
                        "IS_STRATA": "N",
                        "Shape_Area": 400.0,
                        "EXTRACT_DATE": "20260323",
                        "SALES_REFRESH_DATE": "20260616",
                    },
                    "geometry": {"rings": [[[-123.1, 49.2], [-123.1, 49.21], [-123.09, 49.21], [-123.1, 49.2]]]},
                },
                {
                    "attributes": {
                        "AFP_OID": "200002",
                        "ADDRESS": "456 TEST ST NORTH VANCOUVER",
                        "DESCRIPTION": "2 STY house - Standard",
                        "TOTAL_ASSESSED": 2_000_000,
                        "TOTAL_LAND": 1_900_000,
                        "TOTAL_BUILDING": 100_000,
                        "LAST_SALE_DATE": 1736467200000,
                        "SALE_PRICE": 1_800_000,
                        "IS_STRATA": "N",
                    }
                },
            ]
        },
        source_url="https://arcgis.bcassessment.ca/AFP/0/query",
        source_version="AFP_20260414",
        observed_at="2026-09-05T10:00:00Z",
    )

    assert len(rows) == 2
    assert rows[0]["municipality"] == "City of Vancouver"
    assert rows[0]["property_type"] == "Detached House"
    assert rows[0]["title"] == "Freehold"
    assert rows[0]["sale_date"] == "2025-01-10"
    assert rows[0]["source_type"] == "BC_ASSESSMENT"
    assert rows[0]["source_version"] == "AFP_20260414"
    assert rows[0]["bca_oid_evbc"] == "A00000003A"
    assert abs(rows[0]["lot_area_sqft"] - 4305.56416668) < 1e-8
    assert rows[0]["longitude"] is not None
    assert rows[0]["latitude"] is not None
    assert rows[1]["municipality"] is None


def test_fetch_bca_arcgis_sales_uses_tokenized_public_map_session_paginates_and_writes_snapshot(tmp_path):
    calls = []
    page_payloads = [
        {
            "features": [
                {
                    "attributes": {
                        "AFP_OID": "200001",
                        "ADDRESS": "123 TEST ST VANCOUVER",
                        "DESCRIPTION": "2 STY house - Standard",
                        "TOTAL_ASSESSED": 2_000_000,
                        "TOTAL_LAND": 1_900_000,
                        "TOTAL_BUILDING": 100_000,
                        "LAST_SALE_DATE": 1736467200000,
                        "SALE_PRICE": 1_800_000,
                        "IS_STRATA": "N",
                        "EXTRACT_DATE": "20260323",
                        "SALES_REFRESH_DATE": "20260616",
                    }
                }
            ],
            "exceededTransferLimit": True,
        },
        {
            "features": [],
            "exceededTransferLimit": False,
        },
    ]

    def transport(url, params):
        calls.append((url, dict(params)))
        if url.endswith("/Property/Mapsearch?sp=1&act="):
            return "var gistoken = 'guest-token'; var mapserverUrl = 'https://arcgis.bcassessment.ca/ext_wa/rest/services/DCX/AFP_20260414/MapServer';"
        return page_payloads.pop(0)

    output_path = tmp_path / "bca-sales.json"
    result = fetch_bca_arcgis_sales(
        output_path=output_path,
        start_date="2023-09-06",
        end_date="2026-09-05",
        bbox=(-123.27, 49.20, -123.02, 49.30),
        page_size=2_000,
        transport=transport,
        observed_at="2026-09-05T10:00:00Z",
    )

    assert result["records"] == 1
    assert result["pages"] == 2
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["source_type"] == "BC_ASSESSMENT"
    assert payload["source_version"] == "AFP_20260414"
    assert len(payload["sales"]) == 1
    assert len(calls) == 3
    assert calls[1][1]["token"] == "guest-token"
    assert calls[1][1]["resultOffset"] == 0
    assert calls[1][1]["resultRecordCount"] == 1_000
    assert calls[1][1]["returnGeometry"] == "true"
    assert calls[1][1]["outSR"] == "4326"
    assert calls[2][1]["resultOffset"] == 1


def test_cli_exposes_bounded_official_bca_sales_acquisition(monkeypatch):
    captured = {}

    def fake_fetch(**kwargs):
        captured.update(kwargs)
        return {"records": 0, "pages": 0}

    monkeypatch.setattr("northshore_watchdog.cli.fetch_bca_arcgis_sales", fake_fetch)
    result = CliRunner().invoke(
        app,
        [
            "acquire-bca-location-sales",
            "--output-path", "sales.json",
            "--start-date", "2023-09-06",
            "--end-date", "2026-09-05",
            "--min-sale-price", "1000000",
            "--max-sale-price", "3500000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["output_path"] == "sales.json"
    assert captured["start_date"].isoformat() == "2023-09-06"
    assert captured["end_date"].isoformat() == "2026-09-05"
    assert captured["min_sale_price"] == 1_000_000
    assert captured["max_sale_price"] == 3_500_000


def test_cli_exposes_bounded_official_bca_sales_detail_enrichment(monkeypatch):
    captured = {}

    def fake_enrich(**kwargs):
        captured.update(kwargs)
        return {"records_enriched": 1}

    monkeypatch.setattr("northshore_watchdog.cli.enrich_bca_sales_details", fake_enrich)
    result = CliRunner().invoke(
        app,
        [
            "enrich-bca-location-sales",
            "--source-path", "raw-sales.json",
            "--output-path", "detail-sales.json",
            "--max-records", "300",
            "--delay-seconds", "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "source_path": "raw-sales.json",
        "output_path": "detail-sales.json",
        "max_records": 300,
        "min_sale_price": None,
        "max_sale_price": None,
        "delay_seconds": 0.0,
    }


def test_cli_exposes_local_spatial_snapshot_merge(monkeypatch):
    captured = {}

    def fake_merge(**kwargs):
        captured.update(kwargs)
        return {"records_matched": 1}

    monkeypatch.setattr("northshore_watchdog.cli.merge_bca_sales_spatial_snapshot", fake_merge)
    result = CliRunner().invoke(
        app,
        [
            "merge-bca-location-sales-spatial",
            "--detail-path", "detail.json",
            "--spatial-path", "spatial.json",
            "--output-path", "merged.json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "detail_path": "detail.json",
        "spatial_path": "spatial.json",
        "output_path": "merged.json",
    }


def test_enrich_bca_sales_details_uses_official_property_information_and_preserves_snapshot(tmp_path):
    source_path = tmp_path / "sales.json"
    source_path.write_text(json.dumps({
        "schema_version": "bca-arcgis-sales-v1",
        "sales": [{
            "record_id": "bca-arcgis:200001:2025-01-10",
            "address": "123 TEST ST VANCOUVER",
            "bca_oid_evbc": "A00000003A",
            "source_ids": ["sales:1"],
            "source_urls": ["https://arcgis.example/query"],
            "raw_observations": [],
        }],
    }), encoding="utf-8")

    detail_html = """
    <table>
      <tr><td>Year built</td><td id="lblYearBuilt">1950</td></tr>
      <tr><td>Bedrooms</td><td id="lblBedrooms">3</td></tr>
      <tr><td>Baths</td><td id="lblBathRooms">2</td></tr>
      <tr><td>Land size</td><td id="lblLandSize">33 x 122 Ft</td></tr>
      <tr><td>First floor area</td><td id="lblFirstFloorArea">1,000</td></tr>
      <tr><td>Second floor area</td><td id="lblSecondFloorArea">500</td></tr>
    </table>
    """
    calls = []

    def transport(url, params):
        calls.append((url, dict(params)))
        return detail_html

    output_path = tmp_path / "sales-enriched.json"
    result = enrich_bca_sales_details(
        source_path=source_path,
        output_path=output_path,
        transport=transport,
        observed_at="2026-09-05T10:00:00Z",
    )

    assert result["records_requested"] == 1
    assert result["records_enriched"] == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    row = payload["sales"][0]
    assert row["year_built"] == 1950
    assert row["beds"] == 3
    assert row["baths"] == 2
    assert row["frontage_ft"] == 33
    assert row["depth_ft"] == 122
    assert row["lot_area_sqft"] == 4026
    assert row["lot_area_source"] == "BCA_PROPERTY_INFORMATION_DIMENSIONS"
    assert row["bca_detail_status"] == "CONFIRMED"
    assert row["source_ids"] == ["sales:1", "bca-property-information:A00000003A"]
    assert calls[0][1]["oid"] == "QTAwMDAwMDAzQQ=="


def test_enrich_bca_sales_details_can_select_a_price_band_without_marking_other_rows_requested(tmp_path):
    source_path = tmp_path / "sales.json"
    source_path.write_text(json.dumps({"sales": [
        {"record_id": "low", "sale_price": 1_000_000, "bca_oid_evbc": "LOW"},
        {"record_id": "high", "sale_price": 4_000_000, "bca_oid_evbc": "HIGH"},
    ]}), encoding="utf-8")
    calls = []

    def transport(url, params):
        calls.append(params["oid"])
        return "<html></html>"

    output_path = tmp_path / "detail.json"
    result = enrich_bca_sales_details(
        source_path=source_path,
        output_path=output_path,
        min_sale_price=3_500_000,
        transport=transport,
    )

    assert result["records_requested"] == 1
    assert calls == ["SElHSA=="]
    rows = json.loads(output_path.read_text(encoding="utf-8"))["sales"]
    assert rows[0]["bca_detail_status"] == "NOT_SELECTED_PRICE_BAND"
    assert rows[1]["bca_detail_status"] == "EMPTY"
def test_merge_bca_sales_spatial_snapshot_reuses_completed_detail_snapshot(tmp_path):
    detail_path = tmp_path / "detail.json"
    spatial_path = tmp_path / "spatial.json"
    output_path = tmp_path / "merged.json"
    detail_path.write_text(json.dumps({
        "schema_version": "bca-arcgis-sales-v1.1-enriched",
        "sales": [{"record_id": "one", "bca_afp_oid": "AFP1", "pid": "012-345-678"}],
    }), encoding="utf-8")
    spatial_path.write_text(json.dumps({
        "schema_version": "bca-arcgis-sales-v1",
        "source_url": "https://arcgis.example/query",
        "source_version": "AFP_TEST",
        "observed_at": "2026-09-05T10:00:00Z",
        "sales": [{"record_id": "one", "bca_afp_oid": "AFP1", "latitude": 49.25, "longitude": -123.15}],
    }), encoding="utf-8")

    result = merge_bca_sales_spatial_snapshot(
        detail_path=detail_path,
        spatial_path=spatial_path,
        output_path=output_path,
    )

    assert result["records_matched"] == 1
    row = json.loads(output_path.read_text(encoding="utf-8"))["sales"][0]
    assert row["pid"] == "012-345-678"
    assert row["latitude"] == 49.25
    assert row["longitude"] == -123.15
    assert row["spatial_status"] == "CONFIRMED"
