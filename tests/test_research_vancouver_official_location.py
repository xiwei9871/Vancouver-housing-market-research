from __future__ import annotations

import json

from northshore_watchdog.research.vancouver_official_location import (
    VANCOUVER_STREETS_URL,
    VANCOUVER_STREETS_EXPORT_URL,
    assign_official_road_classes,
    fetch_vancouver_public_streets,
)
from northshore_watchdog.research.vancouver_official_zoning import (
    VANCOUVER_ZONING_QUERY_URL,
    assign_official_vancouver_zoning,
    fetch_vancouver_zoning_polygons,
)


def test_fetch_public_streets_paginates_and_writes_snapshot(tmp_path):
    calls = []
    pages = [{"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"hblock": "100 TEST ST", "streetuse": "Arterial", "geo_point_2d": {"lat": 49.25, "lon": -123.15}}, "geometry": {"type": "LineString", "coordinates": [[-123.15, 49.249], [-123.15, 49.251]]}},
        {"type": "Feature", "properties": {"hblock": "200 TEST ST", "streetuse": "Residential", "geo_point_2d": {"lat": 49.26, "lon": -123.15}}, "geometry": {"type": "LineString", "coordinates": [[-123.15, 49.259], [-123.15, 49.261]]}},
    ]}]

    def transport(url, params):
        calls.append((url, dict(params)))
        return pages.pop(0)

    path = tmp_path / "streets.json"
    result = fetch_vancouver_public_streets(output_path=path, page_size=1, transport=transport)

    assert result["records"] == 2
    assert len(calls) == 1
    assert calls[0] == (VANCOUVER_STREETS_EXPORT_URL, {"lang": "en", "timezone": "America/Vancouver"})
    assert json.loads(path.read_text(encoding="utf-8"))["source_type"] == "OFFICIAL_GOV"


def test_assign_official_road_classes_uses_nearest_saved_street(tmp_path):
    path = tmp_path / "streets.json"
    path.write_text(json.dumps({
        "source_url": VANCOUVER_STREETS_URL,
        "streets": [{
            "hblock": "100 TEST ST",
            "streetuse": "Arterial",
            "geo_point_2d": {"lat": 49.25, "lon": -123.15},
            "geom": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-123.15, 49.249], [-123.15, 49.251]]}},
        }],
    }), encoding="utf-8")

    result = assign_official_road_classes(
        [{"record_id": "one", "latitude": 49.25, "longitude": -123.15}],
        streets_path=path,
    )

    assert result[0]["road_class"] == "B"
    assert result[0]["nearest_major_road"] == "100 TEST ST"
    assert result[0]["road_status"] == "CONFIRMED"


def test_fetch_vancouver_zoning_polygons_writes_official_geojson_snapshot(tmp_path):
    calls = []

    def transport(url, params):
        calls.append((url, dict(params)))
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"zoning": "RS-1", "zonename": "RS-1"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-123.16, 49.24], [-123.14, 49.24],
                        [-123.14, 49.26], [-123.16, 49.26],
                        [-123.16, 49.24],
                    ]],
                },
            }],
        }

    path = tmp_path / "zoning.json"
    result = fetch_vancouver_zoning_polygons(output_path=path, transport=transport)

    assert result["records"] == 1
    assert calls == [(VANCOUVER_ZONING_QUERY_URL, {
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    })]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_type"] == "OFFICIAL_GOV"
    assert payload["polygons"][0]["properties"]["zoning"] == "RS-1"


def test_assign_official_vancouver_zoning_uses_polygon_and_marks_current_only(tmp_path):
    path = tmp_path / "zoning.json"
    path.write_text(json.dumps({
        "source_url": VANCOUVER_ZONING_QUERY_URL,
        "observed_at": "2026-09-06T00:00:00Z",
        "polygons": [{
            "type": "Feature",
            "properties": {"zoning": "RS-1"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-123.16, 49.24], [-123.14, 49.24],
                    [-123.14, 49.26], [-123.16, 49.26],
                    [-123.16, 49.24],
                ]],
            },
        }],
    }), encoding="utf-8")

    result = assign_official_vancouver_zoning(
        [{"record_id": "one", "latitude": 49.25, "longitude": -123.15}],
        zoning_path=path,
    )

    assert result[0]["zoning"] == "RS-1"
    assert result[0]["current_zoning"] == "RS-1"
    assert result[0]["zoning_status"] == "CONFIRMED"
    assert result[0]["zoning_match_method"] == "POINT_IN_OFFICIAL_ZONING_POLYGON"
    assert result[0]["zoning_temporal_status"] == "CURRENT_ONLY"
