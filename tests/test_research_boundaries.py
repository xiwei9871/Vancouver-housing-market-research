from __future__ import annotations

from datetime import datetime, timezone
import json

from northshore_watchdog.research.boundaries import (
    geometry_to_multipolygon,
    multipolygon_to_wkt,
    point_in_multipolygon,
    validate_registry_payload,
)
from northshore_watchdog.research.discovery import (
    DiscoveryCapture,
    DiscoveryCard,
    DiscoveryFilterPlan,
    DiscoveryStatus,
    validate_capture,
)


def _registry() -> dict:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]],
    }
    return {
        "schema_version": "market-boundaries-v1",
        "source_local_areas": ["Test Area"],
        "regions": [{
            "boundary_id": "test",
            "source_local_areas": ["Test Area"],
            "geometry": geometry,
            "polygon_wkt": multipolygon_to_wkt(geometry_to_multipolygon(geometry)),
        }],
    }


def test_registry_rejects_duplicate_local_area_assignment():
    payload = _registry()
    payload["regions"].append(dict(payload["regions"][0], boundary_id="other"))

    errors = validate_registry_payload(payload)

    assert any("more than once" in error for error in errors)


def test_point_in_multipolygon_includes_edge_and_excludes_outside():
    polygons = geometry_to_multipolygon(_registry()["regions"][0]["geometry"])

    assert point_in_multipolygon((1.0, 1.0), polygons)
    assert point_in_multipolygon((1.0, 0.0), polygons)
    assert not point_in_multipolygon((2.1, 1.0), polygons)


def test_capture_boundary_validation_fails_closed_for_outside_card(tmp_path):
    path = tmp_path / "boundaries.json"
    path.write_text(json.dumps(_registry()), encoding="utf-8")
    plan = DiscoveryFilterPlan(
        preset_name="test",
        geography={
            "map_params": {
                "boundary_id": "test",
                "boundary_registry_path": str(path),
            }
        },
    )
    cards = [
        DiscoveryCard(address="1 Inside Street", latitude=1.0, longitude=1.0),
        DiscoveryCard(address="2 Outside Street", latitude=3.0, longitude=1.0),
    ]
    capture = DiscoveryCapture(
        source_name="test",
        source_url="https://example.invalid/map",
        preset_name="test",
        observed_at=datetime.now(timezone.utc),
        result_count=2,
        count_is_complete=True,
        cards=cards,
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=10)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert "BOUNDARY_POINT_OUTSIDE" in result.reason_codes
