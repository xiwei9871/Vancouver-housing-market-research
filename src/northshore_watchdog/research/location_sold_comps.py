"""Versioned sold-comps research data layer for Vancouver location valuation.

This module is intentionally separate from the production screening and
valuation models.  It builds a research database from supplied snapshots,
keeps asking observations separate from completed sales, and fails closed
when sale identity or required scope fields are incomplete.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .boundaries import geometry_to_multipolygon, point_in_multipolygon
from .location_valuation import find_matched_pairs, fit_hedonic_model
from .vancouver_official_location import assign_official_road_classes
from .vancouver_official_amenities import assign_official_park_distances
from .vancouver_official_zoning import assign_official_vancouver_zoning
from .location_premium import estimate_initial_location_premiums, render_initial_location_premium_markdown
from .location_premium_stability import (
    estimate_location_premium_stability,
    render_location_premium_stability_markdown,
)


BENCHMARK_ADDRESSES = (
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
)

_BENCHMARK_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "benchmark_name": "2876 W 18th", "address": "2876 W 18TH AVENUE", "neighbourhood": "Arbutus", "reference_price": 1_700_000,
        "sale_price": 1_700_000, "sale_date": "2026-07-30", "transaction_mls": "R3117599",
        "transaction_mls_status": "VERIFIED_PUBLIC_PAGE",
        "transaction_mls_source_url": "https://www.realtor.ca/real-estate/29675581/2876-w-18th-avenue-vancouver",
        "transaction_mls_source_type": "MLS_PUBLIC_PAGE", "transaction_mls_retrieved_at": "2026-09-06",
    },
    {
        "benchmark_name": "3974 W 10th", "address": "3974 W 10TH AVENUE", "neighbourhood": "Point Grey", "reference_price": 1_900_000,
        "sale_price": 1_900_000, "sale_date": "2026-08-10", "transaction_mls": "R3150355",
        "transaction_mls_status": "VERIFIED_PUBLIC_PAGE",
        "transaction_mls_source_url": "https://www.realtor.ca/real-estate/30085119/3974-w-10th-avenue-vancouver",
        "transaction_mls_source_type": "MLS_PUBLIC_PAGE", "transaction_mls_retrieved_at": "2026-09-06",
    },
    {"benchmark_name": "333 E 13th", "address": "333 E 13TH AVENUE", "neighbourhood": "Mount Pleasant", "reference_price": 1_468_000},
    {"benchmark_name": "486 E King Edward", "address": "486 E KING EDWARD AVENUE", "neighbourhood": "Fraser/Main", "reference_price": 1_250_000},
    {"benchmark_name": "4561 Nanaimo", "address": "4561 NANAIMO STREET", "neighbourhood": "East Vancouver", "reference_price": None},
    {
        "benchmark_name": "3555 Trafalgar", "address": "3555 TRAFALGAR STREET", "neighbourhood": "Arbutus", "reference_price": 1_800_000,
        "sale_price": 1_800_000, "sale_date": "2026-06-09", "transaction_mls": "R3089214",
        "transaction_mls_status": "VERIFIED_PUBLIC_PAGE",
        "transaction_mls_source_url": "https://www.realtor.ca/real-estate/29352278/3555-trafalgar-street-vancouver",
        "transaction_mls_source_type": "MLS_PUBLIC_PAGE", "transaction_mls_retrieved_at": "2026-09-06",
    },
    {"benchmark_name": "2887 W 24th", "address": "2887 W 24TH AVENUE", "neighbourhood": "Arbutus", "reference_price": 1_899_000},
    {
        "benchmark_name": "440-442 E 10th", "address": "440-442 E 10TH AVENUE", "neighbourhood": "Mount Pleasant", "reference_price": 1_599_000,
        "sale_price": 1_599_000, "sale_date": "2026-08-14", "transaction_mls": "R3143462",
        "transaction_mls_status": "VERIFIED_PUBLIC_PAGE",
        "transaction_mls_source_url": "https://www.realtor.ca/real-estate/30000456/440-442-e-10th-avenue-vancouver",
        "transaction_mls_source_type": "MLS_PUBLIC_PAGE", "transaction_mls_retrieved_at": "2026-09-06",
    },
    {
        "benchmark_name": "21 E 41st", "address": "21 E 41ST AVENUE", "neighbourhood": "Main/Oakridge", "reference_price": 1_680_000,
        "sale_price": 1_680_000, "sale_date": "2026-02-12", "transaction_mls": "R3083974",
        "transaction_mls_status": "VERIFIED_PUBLIC_PAGE",
        "transaction_mls_source_url": "https://www.zealty.ca/property/bc/vancouver/21-e-41st-avenue-mls_r3083974",
        "transaction_mls_source_type": "AGGREGATOR_PUBLIC_PAGE", "transaction_mls_retrieved_at": "2026-09-06",
    },
    {"benchmark_name": "41 E 41st", "address": "41 E 41ST AVENUE", "neighbourhood": "Main/Oakridge", "reference_price": 1_638_000},
)

_OUTPUT_FILES = (
    "sold-comps-v1.1.csv",
    "sold-comps-quality-report-v1.1.md",
    "neighbourhood-statistics-v1.1.csv",
    "physical-cohorts-v1.1.csv",
    "school-boundary-pairs-v1.1.csv",
    "road-matched-pairs-v1.1.csv",
    "park-matched-pairs-v1.1.csv",
    "view-matched-pairs-v1.1.csv",
    "school-premium-v1.1.csv",
    "area-baseline-premium-v1.1.csv",
    "micro-location-premium-v1.1.csv",
    "location-premium-initial-v1.1.csv",
    "location-premium-initial-v1.1.md",
    "matched-pairs-v1.1.csv",
    "hedonic-model-v1.1.md",
    "benchmark-validation-v1.1.md",
    "RECOMMENDATION-v1.1.md",
)

_STABILITY_OUTPUT_FILES = (
    "location-premium-stability-v1.3.csv",
    "location-premium-stability-v1.3.md",
)

_STANDARD_PROFILE_OUTPUT_FILES = (
    "location-standard-profile-v1.4.yaml",
)


def _artifact_files(version: str) -> tuple[str, ...]:
    """Return the versioned research artifact names for one run."""

    if not version or any(character in version for character in ("/", "\\", " ")):
        raise ValueError("artifact_version must be a non-empty path-safe label")
    names = [name.replace("v1.1", version) for name in _OUTPUT_FILES]
    # Keep the historical V1.1/V1.2 artifact contracts stable.  The new
    # sensitivity layer is introduced as a V1.3 research artifact set.
    if version not in {"v1.1", "v1.2"}:
        names.extend(name.replace("v1.3", version) for name in _STABILITY_OUTPUT_FILES)
    if version == "v1.4":
        names.extend(_STANDARD_PROFILE_OUTPUT_FILES)
    return tuple(names)

_SOLD_COLUMNS = (
    "record_id", "transaction_key", "address", "pid", "legal_description", "mls", "transaction_mls_status",
    "current_listing_mls", "transaction_mls_source_url", "transaction_mls_source_type", "transaction_mls_retrieved_at", "sale_price", "sale_date",
    "original_asking", "last_asking", "dom", "municipality", "neighbourhood",
    "neighbourhood_source", "neighbourhood_boundary_status", "neighbourhood_source_url", "latitude", "longitude",
    "road_status", "nearest_major_road", "road_distance_m", "road_source_url",
    "property_type", "title", "lot_area_sqft", "lot_area_source", "frontage_ft", "depth_ft",
    "living_area_sqft", "living_area_source", "beds", "baths", "year_built", "effective_year",
    "bca_land", "bca_improvement", "zoning", "current_zoning", "zoning_source_url",
    "bca_detail_status", "bca_detail_fields", "bca_detail_error", "bca_extract_date", "bca_sales_refresh_date",
    "zoning_observed_at", "zoning_match_method", "zoning_status", "zoning_temporal_status", "elementary_catchment",
    "secondary_catchment", "road_class", "lane", "corner", "park_facing",
    "nearest_park_name", "nearest_park_id", "park_distance_m", "park_source_url", "park_status",
    "beach_distance_m", "rapid_transit_distance_m", "view_type",
    "source_type", "source_id", "source_ids", "source_urls", "observed_at", "retrieved_at", "source_version",
    "statistical_eligible", "data_conflict",
    "conflict_fields", "conflicting_values", "exclusion_reasons", "is_benchmark",
    "benchmark_name", "benchmark_status", "reference_price", "raw_observations",
)

_PAIR_COLUMNS = (
    "pair_id", "factor", "left_id", "right_id", "left_factor_value",
    "right_factor_value", "price_difference", "price_difference_pct", "matched", "diagnostics",
)

_PREMIUM_COLUMNS = (
    "component", "segment", "value", "applied_value", "method", "sample_size",
    "confidence", "automatic_rankable", "source_ids", "notes",
)

_INITIAL_LOCATION_PREMIUM_COLUMNS = (
    "component", "segment", "value", "applied_value", "reference_segment", "method",
    "sample_size", "confidence", "automatic_rankable", "source_ids", "notes",
    "standard_property_value", "neutral_standard_value",
    "standard_property_value_age_1940", "standard_property_value_age_1960",
    "standard_property_value_age_1980",
)

_LOCATION_PREMIUM_STABILITY_COLUMNS = (
    "row_type", "analysis_variant", "cohort", "component", "segment", "value",
    "applied_value", "reference_segment", "method", "sample_size", "confidence",
    "automatic_rankable", "school_split", "full_36m_value", "recent_24m_value",
    "recent_12m_value", "old_house_cohort_value", "sample_size_full_36m",
    "sample_size_recent_24m", "sample_size_recent_12m", "sample_size_old_house_cohort",
    "sensitivity_low", "sensitivity_high", "sensitivity_range", "variant_count",
    "stability_status", "neutral_standard_value", "standard_property_value",
    "standard_property_value_low", "standard_property_value_high",
    "standard_property_value_age_1940", "standard_property_value_age_1960",
    "standard_property_value_age_1980", "notes",
)


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def assign_local_area_neighbourhoods(
    rows: Sequence[Mapping[str, Any]],
    *,
    boundary_path: str | Path,
) -> list[dict[str, Any]]:
    """Assign saved City of Vancouver local-area polygons to point rows.

    This is a deterministic spatial join over a versioned local snapshot. It
    fills only missing neighbourhoods; an existing source value is retained
    and labelled as such. No neighbourhood value is inferred from an address
    string or from a listing description.
    """

    path = Path(boundary_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features", []) if isinstance(payload, Mapping) else []
    boundaries: list[tuple[str, Any]] = []
    source_url = (
        payload.get("source_export_url") or payload.get("source_url") or str(path)
        if isinstance(payload, Mapping)
        else str(path)
    )
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, Mapping):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
        name = properties.get("name") or properties.get("NAME")
        geometry = feature.get("geometry")
        if not name or not isinstance(geometry, Mapping):
            continue
        try:
            boundaries.append((str(name), geometry_to_multipolygon(geometry)))
        except (TypeError, ValueError, IndexError):
            continue

    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if _has(row.get("neighbourhood")):
            row.setdefault("neighbourhood_source", "EXISTING_SOURCE")
            row.setdefault("neighbourhood_boundary_status", "NOT_NEEDED")
            output.append(row)
            continue
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            row["neighbourhood_boundary_status"] = "NO_POINT"
            output.append(row)
            continue
        matches = [
            name for name, polygons in boundaries
            if point_in_multipolygon((longitude, latitude), polygons)
        ]
        if len(matches) == 1:
            row["neighbourhood"] = matches[0]
            row["neighbourhood_source"] = "CITY_OF_VANCOUVER_LOCAL_AREA_BOUNDARY"
            row["neighbourhood_boundary_status"] = "CONFIRMED"
            row["neighbourhood_source_url"] = source_url
        elif not matches:
            row["neighbourhood_boundary_status"] = "NO_MATCH"
        else:
            row["neighbourhood_boundary_status"] = "AMBIGUOUS"
        output.append(row)
    return output


def _wgs84_to_web_mercator(latitude: float, longitude: float) -> tuple[float, float]:
    origin = 20037508.34
    x = longitude * origin / 180.0
    y = latitude * 3.141592653589793 / 180.0
    y = origin * math.log(math.tan(math.pi / 4.0 + y / 2.0)) / math.pi
    return x, y


def assign_school_catchments(
    rows: Sequence[Mapping[str, Any]],
    *,
    boundary_path: str | Path,
) -> list[dict[str, Any]]:
    """Assign cached official VSB elementary/secondary polygons to sales."""

    path = Path(boundary_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    boundary_groups = payload.get("boundaries", {}) if isinstance(payload, Mapping) else {}
    prepared: dict[str, list[tuple[str, Any, str | None]]] = {"elementary": [], "secondary": []}
    for level in prepared:
        entries = boundary_groups.get(level, []) if isinstance(boundary_groups, Mapping) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, Mapping) or not entry.get("school_name"):
                continue
            try:
                # The cached VSB polygons are exported in Web Mercator, unlike
                # the BCA parcel centers which are requested in WGS84.
                rings = entry.get("rings")
                polygons = (tuple(tuple((float(point[0]), float(point[1])) for point in ring) for ring in rings),)
                prepared[level].append((str(entry["school_name"]), polygons, entry.get("source_url")))
            except (TypeError, ValueError, IndexError):
                continue

    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        existing = _has(row.get("elementary_catchment")) or _has(row.get("secondary_catchment"))
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        if latitude is None or longitude is None:
            if existing:
                row.setdefault("school_boundary_status", "EXISTING_SOURCE")
            else:
                row["school_boundary_status"] = "NO_POINT"
            output.append(row)
            continue
        point = _wgs84_to_web_mercator(latitude, longitude)
        matched_sources: list[str] = []
        matched_levels = 0
        for level, field in (("elementary", "elementary_catchment"), ("secondary", "secondary_catchment")):
            if _has(row.get(field)):
                matched_levels += 1
                continue
            matches = [
                (name, source_url)
                for name, polygons, source_url in prepared[level]
                if point_in_multipolygon(point, polygons)
            ]
            if len(matches) == 1:
                row[field] = matches[0][0]
                if matches[0][1]:
                    matched_sources.append(str(matches[0][1]))
                matched_levels += 1
            elif len(matches) > 1:
                row["school_boundary_status"] = "AMBIGUOUS"
        if matched_levels:
            row["school_boundary_status"] = "CONFIRMED"
            if matched_sources:
                row["school_source_url"] = matched_sources[0]
        elif row.get("school_boundary_status") != "AMBIGUOUS":
            row["school_boundary_status"] = "NO_MATCH"
        output.append(row)
    return output


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    text = re.sub(r"\s*(sq\.?\s*ft|ft|feet)\s*$", "", text, flags=re.IGNORECASE)
    multiplier = 1.0
    if text.lower().endswith("m"):
        text = text[:-1]
        multiplier = 1_000_000
    elif text.lower().endswith("k"):
        text = text[:-1]
        multiplier = 1_000
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _date_text(value: Any) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _address_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value))


def _has(value: Any) -> bool:
    return value not in (None, "", [], {})


def _same(left: Any, right: Any) -> bool:
    if not _has(left) or not _has(right):
        return True
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        left_number = _number(left)
        right_number = _number(right)
        return left_number is not None and right_number is not None and abs(left_number - right_number) < 0.01
    return _text(left) == _text(right)


def _is_vancouver(value: Any) -> bool:
    municipality = _text(value)
    return municipality in {"vancouver", "city of vancouver"} or municipality.startswith("city of vancouver ")


def _is_detached(value: Any) -> bool:
    property_type = _text(value)
    if not property_type or any(item in property_type for item in ("duplex", "triplex", "townhouse", "row house", "land")):
        return False
    return "detached" in property_type or property_type in {"house", "single family", "single family home", "single family dwelling"}


def _is_freehold(value: Any) -> bool:
    ownership = _text(value)
    return bool(ownership) and "freehold" in ownership and not any(item in ownership for item in ("strata", "leasehold", "fractional", "partial", "undivided"))


def _default_window(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * 3))
    return start, end


def _eligibility(
    row: Mapping[str, Any],
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[bool, list[str]]:
    start, end = _default_window(start_date, end_date)
    reasons: list[str] = []
    sale_date = _date(row.get("sale_date"))
    if not _has(row.get("address")):
        reasons.append("MISSING_ADDRESS")
    if row.get("sale_price") is None or row.get("sale_price") <= 0:
        reasons.append("MISSING_SALE_PRICE")
    if sale_date is None:
        reasons.append("MISSING_SALE_DATE")
    elif not start <= sale_date <= end:
        reasons.append("OUTSIDE_DATE_WINDOW")
    if not _is_vancouver(row.get("municipality")):
        reasons.append("OUTSIDE_TARGET_MUNICIPALITY")
    if not _is_detached(row.get("property_type")):
        reasons.append("NOT_DETACHED")
    if not _has(row.get("title")):
        reasons.append("OWNERSHIP_UNKNOWN")
    elif not _is_freehold(row.get("title")):
        reasons.append("NOT_FREEHOLD")
    if row.get("data_conflict"):
        reasons.append("DATA_CONFLICT")
    return not reasons, list(dict.fromkeys(reasons))


def normalize_sold_comp(
    raw: Mapping[str, Any],
    *,
    source_id: str,
    source_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Normalize one observation while keeping asking and sale values separate."""

    address = _value(raw, "address", "civic_address", "street_address")
    transaction_mls = _value(raw, "transaction_mls", "sold_mls", "mls", "MLS", "listing_id")
    current_listing_mls = _value(raw, "current_listing_mls", "current_mls")
    sale_price = _number(_value(raw, "sale_price", "sold_price", "sold", "transaction_price"))
    sale_date = _date_text(_value(raw, "sale_date", "sold_date", "transaction_date", "sold_on"))
    record_id = str(_value(raw, "record_id", "id", "comparable_id", "property_id", "listing_id", "mls") or source_id)
    frontage = _number(_value(raw, "frontage_ft", "lot_width", "width"))
    depth = _number(_value(raw, "depth_ft", "lot_depth", "depth"))
    lot_area = _number(_value(raw, "lot_area_sqft", "lot_area", "lot_size_sqft", "lot_size"))
    if str(_value(raw, "source_type", "source") or "").upper() == "BC_ASSESSMENT" and frontage and depth:
        lot_area = round(frontage * depth, 2)
        lot_area_source = "BCA_PROPERTY_INFORMATION_DIMENSIONS"
    else:
        lot_area_source = _value(raw, "lot_area_source")
    living_area = _number(_value(raw, "living_area_sqft", "living_area", "house_size_sqft", "house_sqft", "finished_sqft"))
    if living_area is None:
        floor_parts = [
            _number(_value(raw, "bca_first_floor_area_sqft", "first_floor_area")),
            _number(_value(raw, "bca_second_floor_area_sqft", "second_floor_area")),
        ]
        above_grade = [value for value in floor_parts if value is not None]
        living_area = round(sum(above_grade), 2) if above_grade else None
        living_area_source = "BCA_PROPERTY_INFORMATION_FLOOR_AREAS" if above_grade else None
    else:
        living_area_source = _value(raw, "living_area_source")
    row: dict[str, Any] = {
        "record_id": record_id,
        "transaction_key": "",
        "address": str(address).strip() if _has(address) else None,
        "pid": _value(raw, "pid", "PID"),
        "legal_description": _value(raw, "legal_description", "legalDescription"),
        "mls": str(transaction_mls).strip() if _has(transaction_mls) else None,
        "transaction_mls_status": _value(raw, "transaction_mls_status") or (
            "PROVIDED_UNVERIFIED" if _has(transaction_mls) else "NOT_AVAILABLE"
        ),
        "current_listing_mls": str(current_listing_mls).strip() if _has(current_listing_mls) else None,
        "transaction_mls_source_url": _value(raw, "transaction_mls_source_url"),
        "transaction_mls_source_type": _value(raw, "transaction_mls_source_type"),
        "transaction_mls_retrieved_at": _value(raw, "transaction_mls_retrieved_at"),
        "sale_price": sale_price,
        "sale_date": sale_date,
        "original_asking": _number(_value(raw, "original_asking", "original_ask", "original_price")),
        "last_asking": _number(_value(raw, "last_asking", "asking_price", "asking", "current_asking", "list_price")),
        "dom": _number(_value(raw, "dom", "days_on_market", "DOM")),
        "municipality": _value(raw, "municipality", "city", "city_name"),
        "neighbourhood": _value(raw, "neighbourhood", "neighborhood", "area"),
        "neighbourhood_source": _value(raw, "neighbourhood_source"),
        "neighbourhood_boundary_status": _value(raw, "neighbourhood_boundary_status"),
        "neighbourhood_source_url": _value(raw, "neighbourhood_source_url"),
        "latitude": _number(_value(raw, "latitude", "lat")),
        "longitude": _number(_value(raw, "longitude", "lon")),
        "school_boundary_status": _value(raw, "school_boundary_status"),
        "school_source_url": _value(raw, "school_source_url"),
        "road_status": _value(raw, "road_status"),
        "nearest_major_road": _value(raw, "nearest_major_road"),
        "road_distance_m": _number(_value(raw, "road_distance_m")),
        "road_source_url": _value(raw, "road_source_url"),
        "property_type": _value(raw, "property_type", "building_type", "property_type_group"),
        "title": _value(raw, "title", "ownership_type", "ownership", "ownership_type_group"),
        "lot_area_sqft": lot_area,
        "lot_area_source": lot_area_source,
        "frontage_ft": frontage,
        "depth_ft": depth,
        "living_area_sqft": living_area,
        "living_area_source": living_area_source,
        "beds": _number(_value(raw, "beds", "bedrooms", "bedroom_count")),
        "baths": _number(_value(raw, "baths", "bathrooms", "bathroom_count")),
        "year_built": _number(_value(raw, "year_built", "built_year")),
        "effective_year": _number(_value(raw, "effective_year")),
        "bca_land": _number(_value(raw, "bca_land", "BCA_land", "assessment_land", "land_assessment")),
        "bca_improvement": _number(_value(raw, "bca_improvement", "BCA_improvement", "assessment_improvement", "improvement_assessment")),
        "bca_detail_status": _value(raw, "bca_detail_status"),
        "bca_detail_fields": _value(raw, "bca_detail_fields"),
        "bca_detail_error": _value(raw, "bca_detail_error"),
        "bca_extract_date": _value(raw, "bca_extract_date"),
        "bca_sales_refresh_date": _value(raw, "bca_sales_refresh_date"),
        "zoning": _value(raw, "zoning", "official_zoning"),
        "current_zoning": _value(raw, "current_zoning", "official_zoning"),
        "zoning_source_url": _value(raw, "zoning_source_url"),
        "zoning_observed_at": _value(raw, "zoning_observed_at"),
        "zoning_match_method": _value(raw, "zoning_match_method"),
        "zoning_status": _value(raw, "zoning_status"),
        "zoning_temporal_status": _value(raw, "zoning_temporal_status") or (
            "CURRENT_ONLY" if _has(_value(raw, "current_zoning", "official_zoning")) else None
        ),
        "elementary_catchment": _value(raw, "elementary_catchment", "official_elementary_catchment", "elementary_school"),
        "secondary_catchment": _value(raw, "secondary_catchment", "official_secondary_catchment", "secondary_school"),
        "road_class": _value(raw, "road_class", "official_road_class", "road_exposure"),
        "lane": _value(raw, "lane", "lane_access"),
        "corner": _value(raw, "corner", "corner_lot"),
        "park_facing": _value(raw, "park_facing", "park"),
        "nearest_park_name": _value(raw, "nearest_park_name"),
        "nearest_park_id": _value(raw, "nearest_park_id"),
        "park_distance_m": _number(_value(raw, "park_distance_m", "distance_to_park_m")),
        "park_source_url": _value(raw, "park_source_url"),
        "park_status": _value(raw, "park_status"),
        "beach_distance_m": _number(_value(raw, "beach_distance_m", "distance_to_beach_m")),
        "rapid_transit_distance_m": _number(_value(raw, "rapid_transit_distance_m", "distance_to_skytrain_m")),
        "view_type": _value(raw, "view_type", "view"),
        "source_type": source_type or str(_value(raw, "source_type", "source") or "UNKNOWN"),
        "source_id": source_id,
        "source_ids": [source_id],
        "source_urls": list(dict.fromkeys([
            str(value) for value in (
                _value(raw, "source_url", "url", "listing_url"),
                _value(raw, "transaction_mls_source_url"),
            ) if _has(value)
        ])),
        "observed_at": _value(raw, "observed_at", "snapshot_observed_at"),
        "retrieved_at": _value(raw, "retrieved_at"),
        "source_version": _value(raw, "source_version"),
        "statistical_eligible": False,
        "data_conflict": False,
        "conflict_fields": [],
        "conflicting_values": {},
        "exclusion_reasons": [],
        "is_benchmark": bool(raw.get("is_benchmark", False)),
        "benchmark_name": _value(raw, "benchmark_name"),
        "benchmark_status": _value(raw, "benchmark_status"),
        "reference_price": _number(_value(raw, "reference_price")),
        "raw_observations": [dict(raw)],
    }
    row["transaction_key"] = _transaction_key(row)
    eligible, reasons = _eligibility(row, start_date=start_date, end_date=end_date)
    row["statistical_eligible"] = eligible
    row["exclusion_reasons"] = reasons
    # Compatibility aliases used by the existing evidence-aware pair/model code.
    row["id"] = row["record_id"]
    row["sold_price"] = row["sale_price"]
    row["sold_date"] = row["sale_date"]
    row["house_size_sqft"] = row["living_area_sqft"]
    row["school"] = row["elementary_catchment"]
    return row


def _transaction_key(row: Mapping[str, Any]) -> str:
    sale_date = row.get("sale_date") or "UNKNOWN_DATE"
    if _has(row.get("mls")):
        return f"mls:{_address_key(row['mls'])}:{sale_date}"
    address = _address_key(row.get("address"))
    if address:
        return f"address:{address}:{sale_date}"
    return f"record:{_address_key(row.get('record_id'))}:{sale_date}"


def _read_source(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, Mapping)]
        if isinstance(payload, Mapping):
            for key in ("sales", "sold_comps", "comparables", "rows", "data"):
                if isinstance(payload.get(key), list):
                    return [dict(row) for row in payload[key] if isinstance(row, Mapping)]
            if "address" in payload or "sale_price" in payload or "sold_price" in payload:
                return [dict(payload)]
        raise ValueError(f"unsupported sold-comp JSON payload: {path}")
    raise ValueError(f"unsupported sold-comp source extension: {path.suffix}")


def extract_bca_sale_rows(
    path: str | Path,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Extract official BCA previous-sale events from a regional snapshot."""

    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", []) if isinstance(payload, Mapping) else []
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        raw = candidate.get("raw") if isinstance(candidate.get("raw"), Mapping) else {}
        dates = raw.get("official_previous_sale_dates") or []
        prices = raw.get("official_previous_sale_prices") or []
        for index, (sale_date, sale_price) in enumerate(zip(dates, prices), start=1):
            parsed_sale_date = _date(sale_date)
            window_start, window_end = _default_window(start_date, end_date)
            if parsed_sale_date is None or not window_start <= parsed_sale_date <= window_end:
                continue
            output.append(normalize_sold_comp(
                {
                    "address": candidate.get("address"),
                    "pid": raw.get("pid"),
                    "legal_description": raw.get("legal_description"),
                    # The candidate MLS identifies the current/related listing,
                    # not necessarily this historical BCA sale transaction.
                    "current_listing_mls": candidate.get("mls_number"),
                    "transaction_mls_status": "UNVERIFIED_CURRENT_LISTING_ONLY",
                    "sale_date": sale_date,
                    "sale_price": sale_price,
                    "municipality": candidate.get("municipality"),
                    "neighbourhood": candidate.get("neighbourhood"),
                    "property_type": candidate.get("property_type"),
                    "title": candidate.get("ownership_type"),
                    "lot_area_sqft": candidate.get("lot_area_sqft"),
                    "lot_width": candidate.get("frontage_ft"),
                    "lot_depth": candidate.get("depth_ft"),
                    "living_area_sqft": candidate.get("finished_sqft"),
                    "beds": candidate.get("bedrooms"),
                    "baths": candidate.get("bathrooms"),
                    "year_built": candidate.get("year_built"),
                    "bca_land": raw.get("official_assessment_land"),
                    "bca_improvement": raw.get("official_assessment_improvement"),
                    # Current active enrichment must not be presented as
                    # sale-date zoning. The bulk current zoning polygon join
                    # supplies this later, with explicit temporal metadata.
                    "current_zoning": None,
                    "elementary_catchment": raw.get("official_elementary_catchment") or candidate.get("elementary_school"),
                    "secondary_catchment": raw.get("official_secondary_catchment") or candidate.get("secondary_school"),
                    "source_url": "https://www.bcassessment.ca/",
                    "source_type": "BC_ASSESSMENT",
                    "observed_at": payload.get("observed_at") if isinstance(payload, Mapping) else None,
                    "retrieved_at": payload.get("retrieved_at") if isinstance(payload, Mapping) else None,
                    "source_version": payload.get("source_version") if isinstance(payload, Mapping) else None,
                },
                source_id=f"{source_path.name}:bca:{_address_key(candidate.get('address'))}:{index}:{_date_text(sale_date) or 'unknown'}",
                source_type="BC_ASSESSMENT",
                start_date=start_date,
                end_date=end_date,
            ))
    return output


def load_sold_comp_sources(
    paths: Sequence[str | Path],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Read supplied snapshots; no network access or anti-bot bypass is attempted."""

    rows: list[dict[str, Any]] = []
    for source_path in paths:
        path = Path(source_path)
        raw_rows = _read_source(path)
        for index, raw in enumerate(raw_rows, start=1):
            source_id = f"{path.name}:{index}"
            rows.append(normalize_sold_comp(raw, source_id=source_id, start_date=start_date, end_date=end_date))
    return rows


def _unique_values(values: Iterable[Any]) -> list[Any]:
    output: list[Any] = []
    for value in values:
        if not _has(value):
            continue
        if not any(_same(value, existing) for existing in output):
            output.append(value)
    return output


def _bca_asset_keys(row: Mapping[str, Any]) -> set[str]:
    """Return stable BCA identifiers carried by a normalized observation."""

    keys: set[str] = set()
    if _has(row.get("pid")):
        keys.add(f"pid:{_text(row['pid'])}")
    observations = row.get("raw_observations")
    for observation in observations if isinstance(observations, list) else []:
        if not isinstance(observation, Mapping):
            continue
        for field in ("bca_afp_oid", "bca_oid_evbc"):
            if _has(observation.get(field)):
                keys.add(f"{field}:{_text(observation[field])}")
        fields = observation.get("fields")
        if isinstance(fields, Mapping):
            for field in ("bca_afp_oid", "bca_oid_evbc", "pid"):
                if _has(fields.get(field)):
                    keys.add(f"{field}:{_text(fields[field])}")
    return keys


def _same_bca_asset(rows: Sequence[Mapping[str, Any]]) -> bool:
    identifiers = [_bca_asset_keys(row) for row in rows]
    non_empty = [values for values in identifiers if values]
    if not non_empty:
        return False
    common = set.intersection(*(values for values in non_empty))
    return bool(common)


def _is_detail_measurement(row: Mapping[str, Any], field: str) -> bool:
    source_field = {
        "lot_area_sqft": "lot_area_source",
        "living_area_sqft": "living_area_source",
    }.get(field)
    source = str(row.get(source_field) or "") if source_field else ""
    return source in {
        "BCA_PROPERTY_INFORMATION_DIMENSIONS",
        "BCA_PROPERTY_INFORMATION_FLOOR_AREAS",
    }


def _merge_identity(row: Mapping[str, Any]) -> str:
    address = _address_key(row.get("address"))
    if address and row.get("sale_date"):
        return f"address:{address}:{row['sale_date']}"
    return str(row.get("transaction_key"))


def merge_sold_comps(
    rows: Sequence[Mapping[str, Any]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Merge exact observations and isolate material cross-source conflicts."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_merge_identity(row)].append(dict(row))
    merged_rows: list[dict[str, Any]] = []
    conflict_fields = ("sale_price", "sale_date", "mls", "address", "lot_area_sqft", "living_area_sqft")
    for group_rows in groups.values():
        first = dict(group_rows[0])
        first["source_ids"] = []
        first["source_urls"] = []
        first["raw_observations"] = []
        conflicts: dict[str, list[Any]] = {}
        for field in conflict_fields:
            values = _unique_values(row.get(field) for row in group_rows)
            if len(values) > 1:
                # The BCA sales map carries parcel Shape_Area while the
                # property-information tab carries the official frontage x
                # depth / floor-area measurement. They refer to the same BCA
                # asset and are not a transaction conflict; prefer the
                # property-information measurement below.
                if field in {"lot_area_sqft", "living_area_sqft"} and _same_bca_asset(group_rows):
                    continue
                conflicts[field] = values
        for item in group_rows:
            first["source_ids"].extend(item.get("source_ids", []))
            first["source_urls"].extend(item.get("source_urls", []))
            first["raw_observations"].extend(item.get("raw_observations", []))
            for field, value in item.items():
                if field in {"source_ids", "source_urls", "raw_observations", "conflicting_values", "conflict_fields"}:
                    continue
                if field == "bca_detail_status" and value == "CONFIRMED":
                    # A price-band enrichment snapshot deliberately marks
                    # unselected rows. A completed detail observation must
                    # win when the same BCA transaction is merged from both
                    # snapshots.
                    first[field] = value
                    continue
                if (
                    field in {"lot_area_sqft", "living_area_sqft"}
                    and _has(value)
                    and _is_detail_measurement(item, field)
                    and not _is_detail_measurement(first, field)
                ):
                    first[field] = value
                    source_field = {
                        "lot_area_sqft": "lot_area_source",
                        "living_area_sqft": "living_area_source",
                    }[field]
                    first[source_field] = item.get(source_field)
                elif not _has(first.get(field)) and _has(value):
                    first[field] = value
        first["source_ids"] = list(dict.fromkeys(first["source_ids"]))
        first["source_urls"] = list(dict.fromkeys(first["source_urls"]))
        first["conflicting_values"] = conflicts
        first["conflict_fields"] = [f"{field.upper()}_CONFLICT" for field in conflicts]
        first["data_conflict"] = bool(conflicts)
        eligible, reasons = _eligibility(first, start_date=start_date, end_date=end_date)
        first["statistical_eligible"] = eligible and not conflicts
        conflict_reason = ["DATA_CONFLICT"] if conflicts else []
        first["exclusion_reasons"] = list(dict.fromkeys([*reasons, *conflict_reason]))
        first["id"] = first.get("record_id")
        first["sold_price"] = first.get("sale_price")
        first["sold_date"] = first.get("sale_date")
        first["house_size_sqft"] = first.get("living_area_sqft")
        first["school"] = first.get("elementary_catchment")
        merged_rows.append(first)
    return sorted(merged_rows, key=lambda row: (str(row.get("sale_date") or "9999-99-99"), str(row.get("record_id") or "")))


def add_benchmark_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Retain benchmark cases without promoting incomplete facts to sales."""

    output = [dict(row) for row in rows]
    by_address = {_address_key(row.get("address")): row for row in output if _has(row.get("address"))}
    for seed in _BENCHMARK_SEEDS:
        key = _address_key(seed["address"])
        observed = by_address.get(key)
        if observed is not None:
            observed["is_benchmark"] = True
            observed["benchmark_name"] = seed["benchmark_name"]
            observed["benchmark_status"] = "OBSERVED_IN_SOURCE"
            observed["reference_price"] = seed.get("reference_price")
            for field in (
                "sale_price", "sale_date", "transaction_mls", "transaction_mls_status",
                "transaction_mls_source_url", "transaction_mls_source_type", "transaction_mls_retrieved_at",
            ):
                if _has(seed.get(field)) and not _has(observed.get(field)):
                    if field == "transaction_mls":
                        observed["mls"] = str(seed[field])
                    else:
                        observed[field] = seed[field]
            if _has(observed.get("transaction_mls_source_url")):
                observed["source_urls"] = list(dict.fromkeys([
                    *observed.get("source_urls", []), observed["transaction_mls_source_url"]
                ]))
            continue
        seed_row = dict(seed)
        seed_row.update({
            "municipality": "City of Vancouver",
            "property_type": "Detached House",
            "title": None,
            "is_benchmark": True,
            "benchmark_status": "SEED_INCOMPLETE",
            "source_type": "MANUAL_BENCHMARK",
        })
        output.append(normalize_sold_comp(
            seed_row,
            source_id=f"benchmark:{key}",
            source_type="MANUAL_BENCHMARK",
            start_date=start_date,
            end_date=end_date,
        ))
    return output


def _eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("statistical_eligible") is True]


def summarize_sold_comp_quality(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = _eligible(rows)
    neighbourhoods = Counter(str(row["neighbourhood"]) for row in eligible if _has(row.get("neighbourhood")))
    years = Counter(str(_date(row.get("sale_date")).year) for row in eligible if _date(row.get("sale_date")))
    field_groups = {
        "bca": lambda row: _has(row.get("bca_land")) and _has(row.get("bca_improvement")),
        "bca_property_detail": lambda row: row.get("bca_detail_status") == "CONFIRMED",
        "neighbourhood": lambda row: _has(row.get("neighbourhood")),
        "coordinates": lambda row: _has(row.get("latitude")) and _has(row.get("longitude")),
        "school": lambda row: _has(row.get("elementary_catchment")) or _has(row.get("secondary_catchment")),
        "road": lambda row: _has(row.get("road_class")),
        "official_park_distance": lambda row: row.get("park_status") == "CONFIRMED_POINT_DISTANCE" and _has(row.get("park_distance_m")),
        "zoning": lambda row: _has(row.get("zoning")),
        "current_official_zoning": lambda row: row.get("zoning_status") == "CONFIRMED" and _has(row.get("current_zoning")),
        "lot": lambda row: _has(row.get("lot_area_sqft")) and _has(row.get("frontage_ft")) and _has(row.get("depth_ft")),
        "house": lambda row: _has(row.get("living_area_sqft")) and _has(row.get("year_built")),
        "mls": lambda row: _has(row.get("mls")),
        "verified_transaction_mls": lambda row: _has(row.get("mls")) and row.get("transaction_mls_status") == "VERIFIED",
        "current_listing_mls": lambda row: _has(row.get("current_listing_mls")),
        "sale_date": lambda row: _has(row.get("sale_date")),
        "sale_price": lambda row: _has(row.get("sale_price")),
    }
    denominator = len(eligible)
    coverage = {
        name: round(sum(1 for row in eligible if check(row)) / denominator, 4) if denominator else 0.0
        for name, check in field_groups.items()
    }
    return {
        "total_observations": len(rows),
        "eligible_sales": len(eligible),
        "conflict_count": sum(1 for row in rows if row.get("data_conflict")),
        "outside_target_scope_count": sum("OUTSIDE_TARGET_MUNICIPALITY" in row.get("exclusion_reasons", []) for row in rows),
        "benchmark_count": sum(bool(row.get("is_benchmark")) for row in rows),
        "benchmark_observed_count": sum(row.get("benchmark_status") == "OBSERVED_IN_SOURCE" for row in rows),
        "samples_by_neighbourhood": dict(sorted(neighbourhoods.items())),
        "samples_by_year": dict(sorted(years.items())),
        "coverage": coverage,
        "source_counts": dict(sorted(Counter(str(row.get("source_type") or "UNKNOWN") for row in rows).items())),
    }


def _median(rows: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if _has(row.get(field))]
    return round(median(values), 2) if values else None


def build_neighbourhood_statistics(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _eligible(rows):
        if _has(row.get("neighbourhood")):
            groups[str(row["neighbourhood"])].append(row)
    output: list[dict[str, Any]] = []
    for neighbourhood in sorted(groups):
        group = groups[neighbourhood]
        lot_psf = [float(row["sale_price"]) / float(row["lot_area_sqft"]) for row in group if _has(row.get("sale_price")) and _has(row.get("lot_area_sqft")) and float(row["lot_area_sqft"]) > 0]
        output.append({
            "neighbourhood": neighbourhood,
            "sold_count": len(group),
            "median_sale_price": _median(group, "sale_price"),
            "median_lot_area_sqft": _median(group, "lot_area_sqft"),
            "median_lot_price_per_sqft": round(median(lot_psf), 2) if lot_psf else None,
            "median_house_size_sqft": _median(group, "living_area_sqft"),
            "median_house_age": round(median(2026 - float(row["year_built"]) for row in group if _has(row.get("year_built"))), 2) if any(_has(row.get("year_built")) for row in group) else None,
            "median_bca_land": _median(group, "bca_land"),
            "median_bca_improvement": _median(group, "bca_improvement"),
        })
    return output


def build_physical_cohorts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _eligible(rows):
        if not all(_has(row.get(field)) for field in ("lot_area_sqft", "frontage_ft", "living_area_sqft", "year_built")):
            continue
        if not (3_700 <= row["lot_area_sqft"] <= 4_300 and 30 <= row["frontage_ft"] <= 36 and 1_500 <= row["living_area_sqft"] <= 2_500 and row["year_built"] < 1985):
            continue
        output.append({
            "cohort": "OLD_HOUSE_STANDARD_LOT",
            "record_id": row.get("record_id"),
            "mls": row.get("mls"),
            "address": row.get("address"),
            "neighbourhood": row.get("neighbourhood"),
            "sale_price": row.get("sale_price"),
            "sale_date": row.get("sale_date"),
            "lot_area_sqft": row.get("lot_area_sqft"),
            "frontage_ft": row.get("frontage_ft"),
            "depth_ft": row.get("depth_ft"),
            "living_area_sqft": row.get("living_area_sqft"),
            "year_built": row.get("year_built"),
        })
    return output


def _json_value(value: Any) -> str:
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else str(value)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _json_value(row.get(column)) for column in columns})


def _pair_rows(rows: Sequence[Mapping[str, Any]], factor: str) -> list[dict[str, Any]]:
    pairs = find_matched_pairs(_eligible(rows), factor=factor, date_window_days=183, lot_tolerance=0.10, frontage_tolerance=0.15, house_tolerance=0.20, year_tolerance=25)
    output = [asdict(pair) for pair in pairs]
    if not output:
        output.append({column: ("NO_MATCHED_PAIRS" if column == "diagnostics" else "") for column in _PAIR_COLUMNS} | {"factor": factor, "matched": False})
    return output


def _premium_rows(rows: Sequence[Mapping[str, Any]], factor: str, component: str) -> list[dict[str, Any]]:
    pair_rows = _pair_rows(rows, factor)
    credible = [row for row in pair_rows if row.get("matched") is True]
    sample_size = len(credible)
    if sample_size >= 20:
        values = [abs(float(row["price_difference"])) for row in credible]
        diagnostic_delta = round(median(values), 2)
        value = 0
        method, applied, confidence, rankable = (
            "DIRECTION_NOT_IDENTIFIED",
            0,
            "LOW",
            False,
        )
        notes = (
            f"Unsigned pair deltas describe dispersion only; diagnostic median absolute pair delta is ${diagnostic_delta:,.0f}. "
            "It is not a directional premium. Use the separate initial residual model or factor-specific transaction evidence."
        )
    else:
        value, method, applied, confidence, rankable = 0, "INSUFFICIENT_EVIDENCE", 0, "UNKNOWN", False
        notes = "Research-only output; no production scoring or RALV/MOS input is modified."
    source_ids: list[str] = []
    for pair in credible:
        for source_id in (pair.get("left_id"), pair.get("right_id")):
            if source_id and source_id not in source_ids:
                source_ids.append(str(source_id))
            if len(source_ids) >= 100:
                break
        if len(source_ids) >= 100:
            break
    return [{
        "component": component,
        "segment": factor,
        "value": value,
        "applied_value": applied,
        "method": method,
        "sample_size": sample_size,
        "confidence": confidence,
        "automatic_rankable": rankable,
        "source_ids": source_ids,
        "notes": f"{notes} Full pair evidence is in the factor-specific matched-pairs CSV; summary provenance is capped at 100 unique IDs.",
    }]


def _quality_markdown(
    summary: Mapping[str, Any],
    sources: Sequence[str | Path],
    start: date,
    end: date,
    *,
    version: str = "V1.1",
) -> str:
    lines = [
        f"# Sold Comps Quality Report {version}", "",
        f"Research window: `{start.isoformat()}` to `{end.isoformat()}`.",
        "", "## Scope", "",
        "Target population is City of Vancouver detached freehold completed sales. Asking-only rows, unknown title, out-of-scope municipalities, and conflicts are retained for audit but excluded from statistics.",
        "Current official zoning, road, and school joins describe the current spatial context of an address; they are not historical as-of-sale attributes unless the source explicitly provides that time version.",
        "", "## Summary", "",
    ]
    for key in ("total_observations", "eligible_sales", "conflict_count", "outside_target_scope_count", "benchmark_count", "benchmark_observed_count"):
        lines.append(f"- `{key}`: {summary.get(key)}")
    lines += ["", "## Sources", ""]
    lines.extend(f"- `{source}`" for source in sources)
    lines += ["", "## Coverage among eligible sales", "", "| Field | Coverage |", "|---|---:|"]
    lines.extend(f"| {field} | {value:.1%} |" for field, value in summary.get("coverage", {}).items())
    lines += ["", "## Samples by neighbourhood", "", "| Neighbourhood | Eligible sales |", "|---|---:|"]
    lines.extend(f"| {name} | {count} |" for name, count in summary.get("samples_by_neighbourhood", {}).items())
    sample_gate = "MET" if summary.get("eligible_sales", 0) >= 300 else "NOT_MET"
    lines += [
        "", "## Gates", "",
        f"- Sold-database size gate (`>=300` eligible sales): **{sample_gate}**.",
        "- Factor-evidence gate: **not met for automatic production pricing**. Current official zoning, road, and school spatial joins are available where coordinates can be matched, but factor-specific pair output is still diagnostic and does not yet establish directional premiums.",
        "- Transaction MLS gate: **not met for verified transaction MLS coverage**. BCA historical sale rows do not contain transaction MLS identifiers; any current-listing MLS is retained separately and is not counted as a sold-transaction MLS.",
        "- All location premiums remain research-only unless their factor-specific estimate has direction, controls, dispersion, and sufficient evidence.",
        "",
    ]
    return "\n".join(lines)


def _hedonic_markdown(rows: Sequence[Mapping[str, Any]], *, version: str = "V1.1") -> str:
    eligible = _eligible(rows)
    result = fit_hedonic_model(eligible, minimum_rows=200)
    if len(eligible) < 200:
        status = "DO_NOT_FIT"
    elif len(eligible) < 500:
        status = "EXPERIMENTAL_ONLY"
    else:
        status = result.status
    return "\n".join([
        f"# Hedonic Model {version}", "",
        f"- Eligible sales: `{len(eligible)}`",
        f"- Gate status: `{status}`",
        f"- Diagnostic model status: `{result.status}`",
        f"- Diagnostic row count: `{result.row_count}`",
        "- Production use: `DISABLED`",
        "",
        "No coefficient from this research artifact is written into production scoring. A model is not fit below 200 eligible sales.",
        "",
        f"Diagnostics: `{json.dumps(result.diagnostics, ensure_ascii=False, sort_keys=True)}`",
        "",
    ])


def _benchmark_markdown(rows: Sequence[Mapping[str, Any]], *, version: str = "V1.1") -> str:
    lines = [
        f"# Benchmark Validation {version}", "",
        "Benchmarks are retained for backtesting and are never address bonuses. Incomplete user-provided facts are not statistical sales.",
        "", "| Benchmark | Address | Reference price | Transaction MLS | Sale date | Observed | Statistical eligible | Status |", "|---|---|---:|---|---|---|---|---|",
    ]
    for row in rows:
        price = "UNKNOWN" if row.get("reference_price") in (None, "") else f"${float(row['reference_price']):,.0f}"
        transaction_mls = row.get("mls") or "UNKNOWN"
        sale_date = row.get("sale_date") or "UNKNOWN"
        lines.append(f"| {row.get('benchmark_name') or 'UNKNOWN'} | {row.get('address')} | {price} | {transaction_mls} | {sale_date} | {'YES' if row.get('benchmark_status') == 'OBSERVED_IN_SOURCE' else 'NO'} | {'YES' if row.get('statistical_eligible') else 'NO'} | {row.get('benchmark_status') or 'UNKNOWN'} |")
    return "\n".join(lines) + "\n"


def _recommendation(summary: Mapping[str, Any], *, version: str = "V1.1") -> str:
    gate = "met" if summary.get("eligible_sales", 0) >= 300 else "not met"
    return "\n".join([
        f"# Recommendation {version}", "",
        f"The sold-comps expansion gate is **{gate}**: `{summary.get('eligible_sales', 0)}` eligible City of Vancouver detached/freehold sales were available in the supplied snapshots; target is `>=300`.",
        "",
        "No production scoring, Current Use, Dual Value, Redevelopment, RALV, or MOS calculation was changed. Location premiums remain research-only until credible transaction evidence is sufficient.",
        "",
        "Next required research work: use the now-thick BCA transaction base with the current official zoning, road, and school joins, then supplement exact transaction MLS links for matched pairs and benchmarks before applying factor-specific premiums. The current source is sufficient for the size gate but not yet sufficient to promote a location premium into production.",
        "",
    ])


def run_sold_comps_research(
    *,
    source_paths: Sequence[str | Path],
    output_dir: str | Path = "research/location",
    start_date: date | None = None,
    end_date: date | None = None,
    property_history_paths: Sequence[str | Path] = (),
    boundary_path: str | Path | None = "data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson",
    school_boundary_path: str | Path | None = "data/research/normalized/school-boundaries/school-catchment-53a59061080d5749.json",
    road_path: str | Path | None = "data/research/raw/location/vancouver-public-streets-2026-09-06.json",
    zoning_path: str | Path | None = "data/research/raw/location/vancouver-zoning-2026-09-06.json",
    park_path: str | Path | None = "data/research/raw/location/vancouver-parks-2026-09-06.geojson",
    artifact_version: str = "v1.1",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Run V1.1 sold-comps research and write only versioned research artifacts."""

    start, end = _default_window(start_date, end_date)
    loaded = load_sold_comp_sources(source_paths, start_date=start, end_date=end)
    for history_path in property_history_paths:
        loaded.extend(extract_bca_sale_rows(history_path, start_date=start, end_date=end))
    if boundary_path:
        boundary_file = Path(boundary_path)
        if boundary_file.exists():
            loaded = assign_local_area_neighbourhoods(loaded, boundary_path=boundary_file)
    if school_boundary_path:
        school_boundary_file = Path(school_boundary_path)
        if school_boundary_file.exists():
            loaded = assign_school_catchments(loaded, boundary_path=school_boundary_file)
    if zoning_path:
        zoning_file = Path(zoning_path)
        if zoning_file.exists():
            loaded = assign_official_vancouver_zoning(loaded, zoning_path=zoning_file)
    if road_path:
        road_file = Path(road_path)
        if road_file.exists():
            loaded = assign_official_road_classes(loaded, streets_path=road_file)
    if park_path:
        park_file = Path(park_path)
        if park_file.exists():
            loaded = assign_official_park_distances(loaded, parks_path=park_file)
    merged = merge_sold_comps(loaded, start_date=start, end_date=end)
    rows = add_benchmark_records(merged, start_date=start, end_date=end)
    summary = summarize_sold_comp_quality(rows)
    artifact_files = _artifact_files(artifact_version)
    summary = {
        "schema_version": f"location-sold-comps-{artifact_version}",
        "observed_at": (observed_at.isoformat() if observed_at else datetime.now().astimezone().isoformat()),
        "research_start_date": start.isoformat(),
        "research_end_date": end.isoformat(),
        "input_observations": len(loaded),
        **summary,
        "stage_gate": "TARGET_MET" if summary["eligible_sales"] >= 300 else "INSUFFICIENT_SAMPLE",
        "hedonic_gate": "DO_NOT_FIT" if summary["eligible_sales"] < 200 else ("EXPERIMENTAL_ONLY" if summary["eligible_sales"] < 500 else "RESEARCH_ONLY"),
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    initial_location_premium = estimate_initial_location_premiums(
        rows,
        include_road=artifact_version not in {"v1.1", "v1.2"},
    )
    summary["initial_location_premium"] = initial_location_premium["diagnostics"]
    location_premium_stability = estimate_location_premium_stability(rows, end_date=end)
    summary["location_premium_stability"] = location_premium_stability["diagnostics"]
    stats = build_neighbourhood_statistics(rows)
    cohorts = build_physical_cohorts(rows)
    pair_by_factor = {factor: _pair_rows(rows, factor) for factor in ("school", "road", "park", "view")}
    all_pairs = [row for values in pair_by_factor.values() for row in values if row.get("matched") is True]
    pair_output = all_pairs or [{column: ("NO_MATCHED_PAIRS" if column == "diagnostics" else "") for column in _PAIR_COLUMNS} | {"matched": False}]
    premium_school = _premium_rows(rows, "school", "school")
    premium_area = [
        {**row, "component": "area_baseline"}
        for row in initial_location_premium["estimates"]
        if row.get("component") == "area_location_residual"
    ] or [{
        "component": "area_baseline", "segment": "neighbourhood residual", "value": 0, "applied_value": 0,
        "method": "INSUFFICIENT_EVIDENCE", "sample_size": len(_eligible(rows)), "confidence": "UNKNOWN",
        "automatic_rankable": False, "source_ids": [], "notes": "Area residual requires at least 20 eligible sales and controlled physical/time/zoning inputs.",
    }]
    premium_micro = [
        {**row, "component": "micro_location"}
        for row in initial_location_premium["estimates"]
        if row.get("component") == "road_adjustment"
    ] or _premium_rows(rows, "road", "micro_location")
    benchmark_rows = [row for row in rows if row.get("is_benchmark")]
    names: dict[str, str] = {}
    for name in artifact_files:
        key, suffix = name.split(f"-{artifact_version}.", 1)
        if key == "location-premium-initial":
            key = f"{key}-{'csv' if suffix == 'csv' else 'md'}"
        elif key == "location-premium-stability":
            key = f"{key}-{'csv' if suffix == 'csv' else 'md'}"
        names[key] = name
    _write_csv(out / names["sold-comps"], rows, _SOLD_COLUMNS)
    _write_csv(out / names["neighbourhood-statistics"], stats, ("neighbourhood", "sold_count", "median_sale_price", "median_lot_area_sqft", "median_lot_price_per_sqft", "median_house_size_sqft", "median_house_age", "median_bca_land", "median_bca_improvement"))
    _write_csv(out / names["physical-cohorts"], cohorts, ("cohort", "record_id", "mls", "address", "neighbourhood", "sale_price", "sale_date", "lot_area_sqft", "frontage_ft", "depth_ft", "living_area_sqft", "year_built"))
    pair_filenames = {
        "school": names["school-boundary-pairs"],
        "road": names["road-matched-pairs"],
        "park": names["park-matched-pairs"],
        "view": names["view-matched-pairs"],
    }
    for factor, filename in pair_filenames.items():
        _write_csv(out / filename, pair_by_factor[factor], _PAIR_COLUMNS)
    _write_csv(out / names["school-premium"], premium_school, _PREMIUM_COLUMNS)
    _write_csv(out / names["area-baseline-premium"], premium_area, _PREMIUM_COLUMNS)
    _write_csv(out / names["micro-location-premium"], premium_micro, _PREMIUM_COLUMNS)
    _write_csv(out / names["location-premium-initial-csv"], initial_location_premium["estimates"], _INITIAL_LOCATION_PREMIUM_COLUMNS)
    if "location-premium-stability-csv" in names:
        _write_csv(
            out / names["location-premium-stability-csv"],
            location_premium_stability["rows"],
            _LOCATION_PREMIUM_STABILITY_COLUMNS,
        )
    _write_csv(out / names["matched-pairs"], pair_output, _PAIR_COLUMNS)
    report_sources = [
        *source_paths,
        *property_history_paths,
        *[path for path in (boundary_path, school_boundary_path, road_path, zoning_path, park_path) if path],
    ]
    (out / names["sold-comps-quality-report"]).write_text(
        _quality_markdown(summary, report_sources, start, end, version=artifact_version.upper()),
        encoding="utf-8",
    )
    (out / names["hedonic-model"]).write_text(_hedonic_markdown(rows, version=artifact_version.upper()), encoding="utf-8")
    (out / names["benchmark-validation"]).write_text(_benchmark_markdown(benchmark_rows, version=artifact_version.upper()), encoding="utf-8")
    (out / names["location-premium-initial-md"]).write_text(
        render_initial_location_premium_markdown(initial_location_premium, version=artifact_version.upper()),
        encoding="utf-8",
    )
    if "location-premium-stability-md" in names:
        (out / names["location-premium-stability-md"]).write_text(
            render_location_premium_stability_markdown(location_premium_stability, version=artifact_version.upper()),
            encoding="utf-8",
        )
    if "location-standard-profile" in names:
        profile_diagnostics = location_premium_stability.get("diagnostics", {})
        profile_payload = {
            "schema_version": f"location-standard-profile-{artifact_version}",
            "profile": profile_diagnostics.get("standard_property_profile", {}),
            "neutral_standard_value": profile_diagnostics.get("neutral_standard_value"),
            "neutral_standard_value_age_sensitivity": profile_diagnostics.get(
                "neutral_standard_value_age_sensitivity", {}
            ),
            "automatic_application": False,
            "bca_improvement_rule": "Resolved profile uses the eligible sample median as a control variable because the standard-property contract does not specify a BCA improvement amount.",
            "interpretation": "Year built 1960 is the primary midpoint; 1940 and 1980 are sensitivity cases. The profile is a counterfactual valuation anchor, not a claim that every address has these attributes.",
        }
        (out / names["location-standard-profile"]).write_text(
            yaml.safe_dump(profile_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    (out / names["RECOMMENDATION"]).write_text(_recommendation(summary, version=artifact_version.upper()), encoding="utf-8")
    return {"summary": summary, "output_paths": sorted(str(out / name) for name in artifact_files)}


__all__ = [
    "BENCHMARK_ADDRESSES",
    "add_benchmark_records",
    "assign_local_area_neighbourhoods",
    "assign_school_catchments",
    "build_neighbourhood_statistics",
    "build_physical_cohorts",
    "extract_bca_sale_rows",
    "load_sold_comp_sources",
    "merge_sold_comps",
    "normalize_sold_comp",
    "run_sold_comps_research",
    "summarize_sold_comp_quality",
]
