"""Versioned market-boundary geometry helpers.

The discovery site can change its viewport or forget a custom drawing between
sessions.  This module keeps the project's geographic authority independent of
that UI: a registry stores the official Vancouver local-area polygons grouped
into mutually exclusive research buckets, and the same geometry is used for
native search application and result-point validation.

Coordinates use GeoJSON/WKT order (longitude, latitude).  The registry is
deliberately JSON rather than a database table so it is easy to review in a
code/data diff and to reuse from a CLI or a future scheduler.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from ..config import resolve_path


Coordinate = tuple[float, float]
Ring = tuple[Coordinate, ...]
Polygon = tuple[Ring, ...]
MultiPolygon = tuple[Polygon, ...]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boundary coordinate must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"boundary coordinate must be numeric: {value!r}") from exc


def _ring(value: Any) -> Ring:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("boundary ring must be a coordinate sequence")
    points = tuple((_number(point[0]), _number(point[1])) for point in value if isinstance(point, Sequence) and len(point) >= 2)
    if len(points) < 4:
        raise ValueError("boundary ring must contain at least four points")
    if points[0] != points[-1]:
        points = (*points, points[0])
    return points


def _polygon(value: Any) -> Polygon:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("boundary polygon must contain rings")
    return tuple(_ring(ring) for ring in value)


def geometry_to_multipolygon(geometry: Mapping[str, Any]) -> MultiPolygon:
    """Normalize a GeoJSON Polygon or MultiPolygon into immutable rings."""

    geometry_type = str(geometry.get("type") or "").strip()
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        return (_polygon(coordinates),)
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, Sequence):
            raise ValueError("MultiPolygon coordinates must be a sequence")
        return tuple(_polygon(item) for item in coordinates)
    raise ValueError(f"unsupported boundary geometry type: {geometry_type!r}")


def multipolygon_to_wkt(polygons: MultiPolygon) -> str:
    """Serialize normalized rings as a deterministic WKT MultiPolygon."""

    parts: list[str] = []
    for polygon in polygons:
        rings = ", ".join(
            "(" + ", ".join(f"{lon:.12f} {lat:.12f}" for lon, lat in ring) + ")"
            for ring in polygon
        )
        parts.append(f"({rings})")
    if not parts:
        raise ValueError("cannot serialize an empty boundary")
    return f"MULTIPOLYGON ({', '.join(parts)})"


def _signed_area(ring: Ring) -> float:
    return 0.5 * sum(
        (ring[index][0] * ring[index + 1][1])
        - (ring[index + 1][0] * ring[index][1])
        for index in range(len(ring) - 1)
    )


def _point_on_segment(point: Coordinate, start: Coordinate, end: Coordinate) -> bool:
    px, py = point
    ax, ay = start
    bx, by = end
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > 1e-10:
        return False
    return min(ax, bx) - 1e-10 <= px <= max(ax, bx) + 1e-10 and min(ay, by) - 1e-10 <= py <= max(ay, by) + 1e-10


def point_in_ring(point: Coordinate, ring: Ring) -> bool:
    """Return true for a point inside or on the boundary of a ring."""

    x, y = point
    inside = False
    for index in range(len(ring) - 1):
        start = ring[index]
        end = ring[index + 1]
        if _point_on_segment(point, start, end):
            return True
        x1, y1 = start
        x2, y2 = end
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            at_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x <= at_x:
                inside = not inside
    return inside


def point_in_multipolygon(point: Coordinate, polygons: MultiPolygon) -> bool:
    for polygon in polygons:
        if not polygon or not point_in_ring(point, polygon[0]):
            continue
        if all(not point_in_ring(point, hole) for hole in polygon[1:]):
            return True
    return False


def validate_registry_payload(payload: Mapping[str, Any]) -> list[str]:
    """Validate registry structure without a third-party GIS dependency.

    The registry generator records each official local-area unit exactly once.
    That unique assignment is the partition invariant; shared administrative
    edges are intentionally represented by the source dataset rather than
    approximated with hand-drawn cut lines.
    """

    errors: list[str] = []
    if payload.get("schema_version") != "market-boundaries-v1":
        errors.append("schema_version must be market-boundaries-v1")
    regions = payload.get("regions")
    if not isinstance(regions, list) or not regions:
        return [*errors, "regions must be a non-empty list"]
    assigned: list[str] = []
    for index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            errors.append(f"regions[{index}] must be an object")
            continue
        if not str(region.get("boundary_id") or "").strip():
            errors.append(f"regions[{index}] missing boundary_id")
        source_units = region.get("source_local_areas")
        if not isinstance(source_units, list) or not source_units:
            errors.append(f"regions[{index}] missing source_local_areas")
        else:
            assigned.extend(str(item) for item in source_units)
        geometry = region.get("geometry")
        try:
            polygons = geometry_to_multipolygon(geometry) if isinstance(geometry, Mapping) else ()
            if not polygons:
                errors.append(f"regions[{index}] has empty geometry")
            for polygon in polygons:
                if abs(_signed_area(polygon[0])) < 1e-12:
                    errors.append(f"regions[{index}] has zero-area exterior ring")
        except (TypeError, ValueError, IndexError) as exc:
            errors.append(f"regions[{index}] invalid geometry: {exc}")
    duplicates = sorted({item for item in assigned if assigned.count(item) > 1})
    if duplicates:
        errors.append(f"source local areas assigned more than once: {duplicates}")
    expected = payload.get("source_local_areas")
    if isinstance(expected, list):
        missing = sorted(set(str(item) for item in expected) - set(assigned))
        extra = sorted(set(assigned) - set(str(item) for item in expected))
        if missing:
            errors.append(f"source local areas missing from partition: {missing}")
        if extra:
            errors.append(f"unknown source local areas in partition: {extra}")
    return errors


def load_boundary_registry(path: str | Path, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved = resolve_path(path, dict(config or {}))
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("boundary registry root must be an object")
    errors = validate_registry_payload(payload)
    if errors:
        raise ValueError("invalid boundary registry: " + "; ".join(errors))
    return dict(payload)


def region_by_id(registry: Mapping[str, Any], boundary_id: str) -> dict[str, Any]:
    for region in registry.get("regions", []):
        if isinstance(region, Mapping) and region.get("boundary_id") == boundary_id:
            return dict(region)
    raise KeyError(f"unknown boundary_id: {boundary_id}")


__all__ = [
    "Coordinate",
    "MultiPolygon",
    "geometry_to_multipolygon",
    "load_boundary_registry",
    "multipolygon_to_wkt",
    "point_in_multipolygon",
    "region_by_id",
    "validate_registry_payload",
]
