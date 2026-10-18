"""Small official City of Vancouver amenity joins for location research."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests


VANCOUVER_PARKS_EXPORT_URL = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/parks/exports/geojson"
SOURCE_TYPE = "OFFICIAL_GOV"
SOURCE_VERSION = "parks-2026-09-06"
Transport = Callable[[str, Mapping[str, Any]], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _real_transport(timeout_seconds: float) -> Transport:
    def request(url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=dict(params), timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()
    return request


def fetch_vancouver_parks(
    *,
    output_path: str | Path,
    transport: Transport | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Cache the official City parks point dataset."""

    payload = (transport or _real_transport(timeout_seconds))(VANCOUVER_PARKS_EXPORT_URL, {})
    if not isinstance(payload, Mapping):
        raise RuntimeError("City parks export endpoint returned a non-object payload")
    parks: list[dict[str, Any]] = []
    for feature in payload.get("features", []) if isinstance(payload.get("features"), list) else []:
        if not isinstance(feature, Mapping):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), Mapping) else {}
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        longitude = _number(coordinates[0])
        latitude = _number(coordinates[1])
        if latitude is None or longitude is None:
            continue
        parks.append({
            "parkid": properties.get("parkid"),
            "name": properties.get("name"),
            "hectare": properties.get("hectare"),
            "latitude": latitude,
            "longitude": longitude,
        })
    snapshot = {
        "schema_version": "vancouver-parks-v1",
        "source_type": SOURCE_TYPE,
        "source_url": VANCOUVER_PARKS_EXPORT_URL,
        "source_version": SOURCE_VERSION,
        "observed_at": _now(),
        "parks": parks,
        "geometry_note": "Official dataset point locations are used for distance; park-boundary edge/facing is not inferred.",
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"output_path": str(output), "records": len(parks), "source_url": VANCOUVER_PARKS_EXPORT_URL}


def _distance_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    latitude, longitude = left
    other_latitude, other_longitude = right
    x_scale = 111320.0 * math.cos(math.radians(latitude))
    y_scale = 110540.0
    return math.hypot((longitude - other_longitude) * x_scale, (latitude - other_latitude) * y_scale)


def assign_official_park_distances(
    rows: Sequence[Mapping[str, Any]],
    *,
    parks_path: str | Path,
    max_distance_m: float = 5_000,
) -> list[dict[str, Any]]:
    """Add nearest official park point distance without inferring park-facing."""

    payload = json.loads(Path(parks_path).read_text(encoding="utf-8"))
    parks = payload.get("parks", []) if isinstance(payload, Mapping) else []
    usable = [
        park for park in parks if isinstance(park, Mapping)
        and _number(park.get("latitude")) is not None
        and _number(park.get("longitude")) is not None
    ]
    source_url = str(payload.get("source_url") or VANCOUVER_PARKS_EXPORT_URL) if isinstance(payload, Mapping) else VANCOUVER_PARKS_EXPORT_URL
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            row["park_status"] = "NO_POINT"
            output.append(row)
            continue
        nearest = min(
            usable,
            key=lambda park: _distance_m(
                (latitude, longitude),
                (_number(park["latitude"]) or 0.0, _number(park["longitude"]) or 0.0),
            ),
            default=None,
        )
        if nearest is None:
            row["park_status"] = "NO_PARK_DATA"
            output.append(row)
            continue
        distance = _distance_m(
            (latitude, longitude),
            (_number(nearest["latitude"]) or 0.0, _number(nearest["longitude"]) or 0.0),
        )
        if distance > max_distance_m:
            row["park_status"] = "OUTSIDE_DISTANCE_LIMIT"
            output.append(row)
            continue
        row.update({
            "nearest_park_name": nearest.get("name"),
            "nearest_park_id": nearest.get("parkid"),
            "park_distance_m": round(distance, 1),
            "park_source_url": source_url,
            "park_status": "CONFIRMED_POINT_DISTANCE",
        })
        output.append(row)
    return output


__all__ = [
    "VANCOUVER_PARKS_EXPORT_URL",
    "assign_official_park_distances",
    "fetch_vancouver_parks",
]
