#!/usr/bin/env python3
"""Build the versioned Vancouver research-boundary registry.

The source is the City of Vancouver's official local-area boundary export.
Each local-area feature is assigned to exactly one research bucket.  Keeping
the source feature polygons intact avoids hand-drawn overlaps and makes a
later refresh auditable when the City's boundary dataset changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import argparse
from urllib.request import Request, urlopen

from shapely.geometry import mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "local-area-boundary/exports/geojson?lang=en&timezone=America%2FLos_Angeles"
)
SOURCE_PAGE_URL = "https://opendata.vancouver.ca/explore/dataset/local-area-boundary/"
CITY_BOUNDARY_URL = "https://opendata.vancouver.ca/explore/dataset/city-boundary/export/"

GROUPS = {
    "vancouver-westside": {
        "label": "Vancouver Westside",
        "market_bucket": "vancouver_west",
        "local_areas": [
            "Arbutus Ridge", "Dunbar-Southlands", "Kerrisdale", "Kitsilano",
            "Shaughnessy", "West Point Grey",
        ],
    },
    "vancouver-central": {
        "label": "Vancouver Central Core",
        "market_bucket": "central_core",
        "local_areas": [
            "Downtown", "Fairview", "Mount Pleasant", "Riley Park", "South Cambie",
            "West End",
        ],
    },
    "vancouver-oakridge": {
        "label": "Vancouver Oakridge",
        "market_bucket": "central_core",
        "local_areas": ["Oakridge"],
    },
    "vancouver-east": {
        "label": "Vancouver East",
        "market_bucket": "vancouver_east",
        "local_areas": [
            "Grandview-Woodland", "Hastings-Sunrise", "Kensington-Cedar Cottage",
            "Renfrew-Collingwood", "Strathcona",
        ],
    },
    "vancouver-south": {
        "label": "Vancouver South",
        "market_bucket": "south_vancouver",
        "local_areas": ["Killarney", "Marpole", "Sunset", "Victoria-Fraserview"],
    },
}


def _wkt(geometry: dict) -> str:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
    parts = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            points = ", ".join(f"{float(point[0]):.12f} {float(point[1]):.12f}" for point in ring)
            rings.append(f"({points})")
        parts.append(f"({', '.join(rings)})")
    return f"MULTIPOLYGON ({', '.join(parts)})"


def _load_source(input_path: str | Path | None = None) -> dict:
    """Load the official export, using a saved raw copy when supplied."""

    if input_path is not None:
        path = Path(input_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("saved boundary export must contain a JSON object")
        return payload
    request = Request(SOURCE_URL, headers={"User-Agent": "northshore-property-watchdog/0.1"})
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("official local-area export must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_version = datetime.now(timezone.utc).date().isoformat()
    parser.add_argument("--boundary-version", default=default_version)
    parser.add_argument("--input", help="Use a saved raw GeoJSON export instead of downloading it")
    parser.add_argument("--output")
    parser.add_argument("--raw-output")
    args = parser.parse_args()
    output = ROOT / (args.output or f"data/research/boundaries/vancouver-market-boundaries-{args.boundary_version}.json")
    raw_output = ROOT / (args.raw_output or f"data/research/raw/boundaries/local-area-boundary-{args.boundary_version}.geojson")
    source = _load_source(args.input)
    features = source.get("features")
    if not isinstance(features, list):
        raise RuntimeError("official local-area export did not contain features")
    by_name = {
        str(feature.get("properties", {}).get("name")): feature
        for feature in features
        if isinstance(feature, dict) and feature.get("properties", {}).get("name")
    }
    expected = {name for group in GROUPS.values() for name in group["local_areas"]}
    if set(by_name) != expected:
        raise RuntimeError(f"source local-area names changed; expected={sorted(expected)} actual={sorted(by_name)}")

    retrieved = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    regions = []
    for boundary_id, group in GROUPS.items():
        selected = [by_name[name] for name in group["local_areas"]]
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [feature["geometry"]["coordinates"] for feature in selected],
        }
        union = unary_union([shape(feature["geometry"]) for feature in selected])
        official_polygon_wkt = union.wkt
        search_geometries = list(union.geoms) if union.geom_type == "MultiPolygon" else [union]
        # The official union can contain interior water/park rings.  The
        # REALTOR.ca polygon endpoint accepts the exterior ring reliably but
        # rejects holes, so the search geometry is a documented, conservative
        # exterior-only approximation.  Official geometry remains the
        # authority used for local result-point validation.
        exteriors = []
        for item in search_geometries:
            points = ", ".join(f"{x:.8f} {y:.8f}" for x, y in item.exterior.coords)
            exteriors.append(f"POLYGON (({points}))")
        if len(exteriors) != 1:
            raise RuntimeError(f"group {boundary_id} must be a single search polygon")
        polygon_wkt = exteriors[0]
        regions.append({
            "boundary_id": boundary_id,
            "label": group["label"],
            "market_bucket": group["market_bucket"],
            "boundary_type": "OFFICIAL_LOCAL_AREA_UNION",
            "source_local_areas": group["local_areas"],
            "geometry": geometry,
            "official_polygon_wkt": official_polygon_wkt,
            "polygon_wkt": polygon_wkt,
            "search_polygons": [item.wkt for item in search_geometries],
            "geometry_sha256": hashlib.sha256(polygon_wkt.encode("utf-8")).hexdigest(),
            "source_url": SOURCE_PAGE_URL,
            "retrieved_at": retrieved,
            "_official_union": union,
        })

    # The City source is authoritative, but adjacent source features contain
    # a few tiny polygon slivers where vertices do not line up exactly.  Make
    # a deterministic, non-overlapping research partition in GROUPS order.
    # The unmodified official geometry remains stored beside it for audit.
    assigned = None
    partition_geometries = []
    for region in regions:
        official_union = region.pop("_official_union")
        partition = official_union if assigned is None else official_union.difference(assigned)
        if partition.is_empty or partition.geom_type not in {"Polygon", "MultiPolygon"}:
            raise RuntimeError(f"group {region['boundary_id']} produced an invalid partition geometry")
        region["official_geometry"] = region["geometry"]
        region["partition_geometry"] = mapping(partition)
        region["partition_polygon_wkt"] = partition.wkt
        region["geometry_sha256"] = hashlib.sha256(partition.wkt.encode("utf-8")).hexdigest()
        partition_geometries.append(partition)
        assigned = official_union if assigned is None else assigned.union(official_union)

    positive_area_overlaps = []
    for index, left in enumerate(partition_geometries):
        for right_index in range(index + 1, len(partition_geometries)):
            right = partition_geometries[right_index]
            overlap = left.intersection(right).area
            if overlap > 1e-15:
                positive_area_overlaps.append({
                    "left": regions[index]["boundary_id"],
                    "right": regions[right_index]["boundary_id"],
                    "area": overlap,
                })

    payload = {
        "schema_version": "market-boundaries-v1",
        "boundary_version": args.boundary_version,
        "retrieved_at": retrieved,
        "source_local_areas": sorted(expected),
        "partition_policy": "Each official local area is assigned to one bucket exactly once; shared edges use half-open point assignment.",
        "partition_overlap_check": {
            "status": "PASSED" if not positive_area_overlaps else "FAILED",
            "positive_area_overlaps": positive_area_overlaps,
        },
        "source_url": SOURCE_PAGE_URL,
        "source_export_url": SOURCE_URL,
        "city_boundary_reference_url": CITY_BOUNDARY_URL,
        "regions": regions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "raw": str(raw_output), "regions": len(regions), "local_areas": len(expected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
