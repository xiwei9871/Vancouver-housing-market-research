"""Cached official City of Vancouver street data for sold-comps research."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests


VANCOUVER_STREETS_URL = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/public-streets/records"
VANCOUVER_STREETS_EXPORT_URL = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/public-streets/exports/geojson"
SOURCE_TYPE = "OFFICIAL_GOV"
SOURCE_VERSION = "public-streets-2026-09-01"

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
    session = requests.Session()
    session.headers.update({
        "User-Agent": "northshore-property-watchdog/0.1 (+read-only-research)",
        "Accept": "application/json",
    })

    def request(url: str, params: Mapping[str, Any]) -> Any:
        response = session.get(url, params=dict(params), timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()

    return request


def fetch_vancouver_public_streets(
    *,
    output_path: str | Path,
    page_size: int = 100,
    max_pages: int = 250,
    transport: Transport | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Fetch the complete bounded public-streets dataset into a local cache."""

    if page_size <= 0 or page_size > 100 or max_pages <= 0:
        raise ValueError("page_size must be in 1..100 and max_pages must be positive")
    request = transport or _real_transport(timeout_seconds)
    export_payload = request(VANCOUVER_STREETS_EXPORT_URL, {"lang": "en", "timezone": "America/Vancouver"})
    if not isinstance(export_payload, Mapping):
        raise RuntimeError("City streets export endpoint returned a non-object payload")
    features = export_payload.get("features", [])
    rows: list[dict[str, Any]] = []
    if isinstance(features, list):
        for feature in features:
            if not isinstance(feature, Mapping):
                continue
            properties = dict(feature.get("properties") or {})
            properties["geom"] = {
                "type": "Feature",
                "geometry": feature.get("geometry"),
                "properties": {},
            }
            rows.append(properties)
    if not rows:
        # Keep a bounded-record fallback for mirrors that do not expose the
        # export route. The official API limits page size to 100 and offset
        # can be capped by the service, so the caller receives an explicit
        # partial snapshot rather than a silently incomplete full claim.
        pages = 0
        offset = 0
        for _ in range(max_pages):
            payload = request(VANCOUVER_STREETS_URL, {"limit": page_size, "offset": offset})
            if not isinstance(payload, Mapping):
                raise RuntimeError("City streets endpoint returned a non-object payload")
            page_rows = payload.get("results", [])
            if not isinstance(page_rows, list):
                raise RuntimeError("City streets endpoint returned invalid results")
            rows.extend(dict(row) for row in page_rows if isinstance(row, Mapping))
            pages += 1
            if len(page_rows) < page_size:
                break
            offset += len(page_rows)
        else:
            raise RuntimeError(f"City streets endpoint exceeded max_pages={max_pages}")
    pages = 1 if features else pages

    snapshot = {
        "schema_version": "vancouver-public-streets-v1",
        "source_type": SOURCE_TYPE,
        "source_url": VANCOUVER_STREETS_EXPORT_URL if features else VANCOUVER_STREETS_URL,
        "source_version": SOURCE_VERSION,
        "observed_at": _now(),
        "pages": pages,
        "total_count_reported": len(features) if isinstance(features, list) else None,
        "streets": rows,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"output_path": str(output), "records": len(rows), "pages": pages, "source_url": snapshot["source_url"]}


def _street_class(streetuse: Any) -> str:
    value = " ".join(str(streetuse or "").lower().split())
    if any(token in value for token in ("highway", "freeway", "rail")):
        return "A"
    if any(token in value for token in ("arterial", "major", "truck")):
        return "B"
    if "collector" in value:
        return "D"
    return "E"


def _line_distance_m(point: tuple[float, float], geometry: Mapping[str, Any]) -> float | None:
    raw = geometry.get("geometry") if isinstance(geometry.get("geometry"), Mapping) else geometry
    coordinates = raw.get("coordinates") if isinstance(raw, Mapping) else None
    if not isinstance(coordinates, list):
        return None
    lines = coordinates if raw.get("type") == "MultiLineString" else [coordinates]
    latitude, longitude = point
    x_scale = 111320.0 * math.cos(math.radians(latitude))
    y_scale = 110540.0
    px, py = longitude * x_scale, latitude * y_scale
    distances: list[float] = []
    for line in lines:
        if not isinstance(line, list):
            continue
        for left, right in zip(line, line[1:]):
            if not all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in (left, right)):
                continue
            ax, ay = float(left[0]) * x_scale, float(left[1]) * y_scale
            bx, by = float(right[0]) * x_scale, float(right[1]) * y_scale
            dx, dy = bx - ax, by - ay
            denominator = dx * dx + dy * dy
            ratio = ((px - ax) * dx + (py - ay) * dy) / denominator if denominator else 0.0
            ratio = max(0.0, min(1.0, ratio))
            distances.append(math.hypot(px - (ax + ratio * dx), py - (ay + ratio * dy)))
    return min(distances) if distances else None


def assign_official_road_classes(
    rows: Sequence[Mapping[str, Any]],
    *,
    streets_path: str | Path,
    max_distance_m: float = 500.0,
) -> list[dict[str, Any]]:
    """Join sales to the nearest official street segment in a small grid."""

    payload = json.loads(Path(streets_path).read_text(encoding="utf-8"))
    streets = payload.get("streets", []) if isinstance(payload, Mapping) else []
    if not isinstance(streets, list):
        raise ValueError("street snapshot must contain a streets list")
    cell_size = 0.005
    grid: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    for street in streets:
        if not isinstance(street, Mapping):
            continue
        point = street.get("geo_point_2d")
        latitude = _number(point.get("lat")) if isinstance(point, Mapping) else None
        longitude = _number(point.get("lon")) if isinstance(point, Mapping) else None
        if latitude is None or longitude is None:
            continue
        key = (math.floor(latitude / cell_size), math.floor(longitude / cell_size))
        grid.setdefault(key, []).append(street)
    source_url = str(payload.get("source_url") or VANCOUVER_STREETS_URL) if isinstance(payload, Mapping) else VANCOUVER_STREETS_URL
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if row.get("road_class") not in (None, ""):
            output.append(row)
            continue
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            row["road_status"] = "NO_POINT"
            output.append(row)
            continue
        base = (math.floor(latitude / cell_size), math.floor(longitude / cell_size))
        candidates: list[Mapping[str, Any]] = []
        for lat_index in range(base[0] - 2, base[0] + 3):
            for lon_index in range(base[1] - 2, base[1] + 3):
                candidates.extend(grid.get((lat_index, lon_index), []))
        distances = [
            (_line_distance_m((latitude, longitude), street.get("geom") or {}), street)
            for street in candidates
        ]
        usable = [(distance, street) for distance, street in distances if distance is not None and distance <= max_distance_m]
        if not usable:
            row["road_status"] = "NO_MATCH"
            output.append(row)
            continue
        distance, street = min(usable, key=lambda item: item[0])
        row.update({
            "road_class": _street_class(street.get("streetuse")),
            "nearest_major_road": street.get("hblock"),
            "road_distance_m": round(distance, 1),
            "road_source_url": source_url,
            "road_status": "CONFIRMED",
        })
        output.append(row)
    return output


__all__ = [
    "VANCOUVER_STREETS_URL",
    "VANCOUVER_STREETS_EXPORT_URL",
    "assign_official_road_classes",
    "fetch_vancouver_public_streets",
]
