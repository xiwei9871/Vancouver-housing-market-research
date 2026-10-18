from __future__ import annotations

import json

from northshore_watchdog.research.vancouver_official_amenities import (
    VANCOUVER_PARKS_EXPORT_URL,
    assign_official_park_distances,
    fetch_vancouver_parks,
)


def test_fetch_parks_writes_official_snapshot(tmp_path):
    def transport(url, params):
        assert url == VANCOUVER_PARKS_EXPORT_URL
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "Test Park", "parkid": 1, "hectare": 2.0},
                "geometry": {"type": "Point", "coordinates": [-123.15, 49.25]},
            }],
        }

    path = tmp_path / "parks.json"
    result = fetch_vancouver_parks(output_path=path, transport=transport)

    assert result["records"] == 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source_type"] == "OFFICIAL_GOV"
    assert payload["parks"][0]["name"] == "Test Park"


def test_assign_official_park_distances_uses_nearest_official_park_point(tmp_path):
    path = tmp_path / "parks.json"
    path.write_text(json.dumps({
        "source_url": VANCOUVER_PARKS_EXPORT_URL,
        "parks": [{"name": "Test Park", "parkid": 1, "latitude": 49.25, "longitude": -123.15}],
    }), encoding="utf-8")

    result = assign_official_park_distances(
        [{"record_id": "one", "latitude": 49.25, "longitude": -123.15}],
        parks_path=path,
    )

    assert result[0]["nearest_park_name"] == "Test Park"
    assert result[0]["park_distance_m"] == 0.0
    assert result[0]["park_status"] == "CONFIRMED_POINT_DISTANCE"
