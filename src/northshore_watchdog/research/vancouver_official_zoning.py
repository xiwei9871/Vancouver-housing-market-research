"""Bulk official zoning polygon acquisition and spatial enrichment."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests

from .boundaries import geometry_to_multipolygon, point_in_multipolygon


VANCOUVER_ZONING_QUERY_URL = (
    "https://maps.vancouver.ca/server/rest/services/Hosted/"
    "ZoningDistrict/FeatureServer/0/query"
)
SOURCE_TYPE = "OFFICIAL_GOV"
SOURCE_VERSION = "city-of-vancouver-zoning-district-current"

Transport = Callable[[str, Mapping[str, Any]], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _real_transport(timeout_seconds: float) -> Transport:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "northshore-property-watchdog/0.1 (+read-only-research)",
        "Accept": "application/geo+json, application/json",
    })

    def request(url: str, params: Mapping[str, Any]) -> Any:
        response = session.get(url, params=dict(params), timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()

    return request


def fetch_vancouver_zoning_polygons(
    *,
    output_path: str | Path,
    transport: Transport | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Fetch the complete current City of Vancouver zoning polygon layer once."""

    request = transport or _real_transport(timeout_seconds)
    params = {
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    payload = request(VANCOUVER_ZONING_QUERY_URL, params)
    if not isinstance(payload, Mapping):
        raise RuntimeError("Vancouver zoning endpoint returned a non-object payload")
    features = payload.get("features", [])
    if not isinstance(features, list):
        raise RuntimeError("Vancouver zoning endpoint returned invalid features")
    polygons = [feature for feature in features if isinstance(feature, Mapping) and feature.get("geometry")]
    if not polygons:
        raise RuntimeError("Vancouver zoning endpoint returned no usable polygons")
    snapshot = {
        "schema_version": "vancouver-zoning-polygons-v1",
        "source_type": SOURCE_TYPE,
        "source_url": VANCOUVER_ZONING_QUERY_URL,
        "source_version": SOURCE_VERSION,
        "observed_at": _now(),
        "polygon_count": len(polygons),
        "polygons": polygons,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "output_path": str(output),
        "records": len(polygons),
        "source_url": VANCOUVER_ZONING_QUERY_URL,
    }


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _geometry_bounds(polygons: Any) -> tuple[float, float, float, float]:
    points = [
        point
        for polygon in polygons
        for ring in polygon
        for point in ring
    ]
    if not points:
        raise ValueError("zoning polygon has no coordinates")
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _zoning_grid(
    polygons: Sequence[tuple[str, Any, tuple[float, float, float, float]]],
    *,
    cell_size: float = 0.01,
) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = {}
    for index, (_, _, bounds) in enumerate(polygons):
        min_longitude, min_latitude, max_longitude, max_latitude = bounds
        for lat_index in range(math.floor(min_latitude / cell_size), math.floor(max_latitude / cell_size) + 1):
            for lon_index in range(math.floor(min_longitude / cell_size), math.floor(max_longitude / cell_size) + 1):
                grid.setdefault((lat_index, lon_index), []).append(index)
    return grid


def assign_official_vancouver_zoning(
    rows: Sequence[Mapping[str, Any]],
    *,
    zoning_path: str | Path,
) -> list[dict[str, Any]]:
    """Assign current official zoning locally using saved polygon geometry.

    The result deliberately labels the temporal scope as ``CURRENT_ONLY``.
    It must not be interpreted as zoning effective on the historical sale date.
    """

    payload = json.loads(Path(zoning_path).read_text(encoding="utf-8"))
    features = payload.get("polygons", []) if isinstance(payload, Mapping) else []
    source_url = str(payload.get("source_url") or VANCOUVER_ZONING_QUERY_URL) if isinstance(payload, Mapping) else VANCOUVER_ZONING_QUERY_URL
    observed_at = payload.get("observed_at") if isinstance(payload, Mapping) else None
    polygons: list[tuple[str, Any, tuple[float, float, float, float]]] = []
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, Mapping):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
        zoning = properties.get("zoning") or properties.get("zonename")
        geometry = feature.get("geometry")
        if not zoning or not isinstance(geometry, Mapping):
            continue
        try:
            normalized = geometry_to_multipolygon(geometry)
            polygons.append((str(zoning), normalized, _geometry_bounds(normalized)))
        except (TypeError, ValueError, IndexError):
            continue
    grid = _zoning_grid(polygons)

    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            row["zoning_status"] = "NO_POINT"
            row["zoning_temporal_status"] = "CURRENT_ONLY"
            output.append(row)
            continue
        candidate_indexes = grid.get((math.floor(latitude / 0.01), math.floor(longitude / 0.01)), [])
        matches = []
        for index in candidate_indexes:
            zoning, geometry, bounds = polygons[index]
            min_longitude, min_latitude, max_longitude, max_latitude = bounds
            if not (min_longitude <= longitude <= max_longitude and min_latitude <= latitude <= max_latitude):
                continue
            if point_in_multipolygon((longitude, latitude), geometry):
                matches.append(zoning)
        unique_matches = list(dict.fromkeys(matches))
        if len(unique_matches) == 1:
            zoning = unique_matches[0]
            row.update({
                "zoning": zoning,
                "current_zoning": zoning,
                "zoning_source_url": source_url,
                "zoning_observed_at": observed_at,
                "zoning_match_method": "POINT_IN_OFFICIAL_ZONING_POLYGON",
                "zoning_status": "CONFIRMED",
                "zoning_temporal_status": "CURRENT_ONLY",
            })
        elif not unique_matches:
            row["zoning_status"] = "NO_MATCH"
            row["zoning_temporal_status"] = "CURRENT_ONLY"
        else:
            row["zoning_status"] = "AMBIGUOUS"
            row["zoning_temporal_status"] = "CURRENT_ONLY"
        output.append(row)
    return output


__all__ = [
    "VANCOUVER_ZONING_QUERY_URL",
    "assign_official_vancouver_zoning",
    "fetch_vancouver_zoning_polygons",
]
