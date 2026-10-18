"""Read-only acquisition from BC Assessment's public sales map layer.

The public Assessment Search map exposes a tokenized ArcGIS feature layer for
recent sales.  This adapter uses the same public map session as the website,
keeps the raw query scope in the snapshot, and does not attempt to bypass
access controls or anti-bot challenges.
"""

from __future__ import annotations

import json
import re
import base64
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests


BCA_MAP_SEARCH_URL = "https://www.bcassessment.ca/Property/Mapsearch?sp=1&act="
BCA_DEFAULT_MAPSERVER_URL = "https://arcgis.bcassessment.ca/ext_wa/rest/services/DCX/AFP_20260414/MapServer"
BCA_DEFAULT_BBOX = (-123.27, 49.20, -123.02, 49.30)
BCA_PROPERTY_INFORMATION_URL = "https://www.bcassessment.ca/Property/PropertyInformation"
SQM_TO_SQFT = 10.763910416709722

Transport = Callable[[str, Mapping[str, Any]], Any]


def _json_default(value: Any) -> str:
    """Serialize date-like parser values without losing audit readability."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _geometry_center_wgs84(geometry: Any) -> tuple[float, float] | None:
    """Return a simple parcel center from an ArcGIS WGS84 geometry."""

    if not isinstance(geometry, Mapping):
        return None
    rings = geometry.get("rings")
    if isinstance(rings, list) and rings and isinstance(rings[0], list):
        points = [
            point for point in rings[0]
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        if points:
            longitude = sum(float(point[0]) for point in points) / len(points)
            latitude = sum(float(point[1]) for point in points) / len(points)
            return latitude, longitude
    if geometry.get("y") is not None and geometry.get("x") is not None:
        return float(geometry["y"]), float(geometry["x"])
    return None


def _city_from_bca_address(value: Any) -> str | None:
    text = " ".join(str(value or "").upper().split())
    text = re.sub(r"\s+(?:BC|B\.C\.)$", "", text)
    if text.endswith(" VANCOUVER") and not re.search(r"(?:NORTH|WEST|EAST) VANCOUVER$", text):
        return "City of Vancouver"
    return None


def _layer_version(mapserver_url: str) -> str:
    match = re.search(r"/DCX/([^/]+)/MapServer", mapserver_url, re.IGNORECASE)
    return match.group(1) if match else mapserver_url.rstrip("/").rsplit("/", 1)[-1]


def build_bca_sales_where(
    *,
    start_date: str | date,
    end_date: str | date,
    min_sale_price: int = 1_000_000,
    max_sale_price: int = 3_500_000,
) -> str:
    """Build the server-side research filter for non-strata house sales."""

    def iso(value: str | date) -> str:
        return value.isoformat() if isinstance(value, date) else str(value)

    if min_sale_price < 0 or max_sale_price < min_sale_price:
        raise ValueError("sale price range is invalid")
    return (
        "IS_STRATA = 'N' AND DESCRIPTION LIKE '%house%' "
        f"AND SALE_PRICE >= {int(min_sale_price)} "
        f"AND SALE_PRICE <= {int(max_sale_price)} "
        f"AND LAST_SALE_DATE >= DATE '{iso(start_date)}' "
        f"AND LAST_SALE_DATE <= DATE '{iso(end_date)}'"
    )


def parse_bca_sales_features(
    payload: Mapping[str, Any],
    *,
    source_url: str,
    source_version: str,
    observed_at: str,
    retrieved_at: str | None = None,
) -> list[dict[str, Any]]:
    """Convert ArcGIS features to the sold-comp source schema.

    Rows outside City of Vancouver are retained with a null municipality so
    the downstream sold-comps layer can audit and exclude them conservatively.
    """

    features = payload.get("features", [])
    if not isinstance(features, list):
        return []
    retrieved = retrieved_at or _iso_now()
    rows: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, Mapping) or not isinstance(feature.get("attributes"), Mapping):
            continue
        attributes = dict(feature["attributes"])
        afp_oid = attributes.get("AFP_OID")
        address = attributes.get("ADDRESS")
        sale_date = _date_text(attributes.get("LAST_SALE_DATE"))
        if not afp_oid or not address or not sale_date:
            continue
        is_strata = str(attributes.get("IS_STRATA") or "").upper()
        description = str(attributes.get("DESCRIPTION") or "")
        shape_area_sqm = _number(attributes.get("Shape_Area"))
        center = _geometry_center_wgs84(feature.get("geometry"))
        rows.append({
            "record_id": f"bca-arcgis:{afp_oid}:{sale_date}",
            "address": address,
            "sale_price": attributes.get("SALE_PRICE"),
            "sale_date": sale_date,
            "municipality": _city_from_bca_address(address),
            "property_type": "Detached House" if "HOUSE" in description.upper() else None,
            "title": "Freehold" if is_strata == "N" else None,
            "bca_total": attributes.get("TOTAL_ASSESSED"),
            "bca_land": attributes.get("TOTAL_LAND"),
            "bca_improvement": attributes.get("TOTAL_BUILDING"),
            "lot_area_sqft": shape_area_sqm * SQM_TO_SQFT if shape_area_sqm is not None else None,
            "latitude": center[0] if center else None,
            "longitude": center[1] if center else None,
            "geometry_source": "BC_ASSESSMENT_ARCGIS_PARCEL" if center else None,
            "bca_oid_evbc": attributes.get("OID_EVBC"),
            "official_bca_is_strata": attributes.get("IS_STRATA"),
            "official_bca_description": description or None,
            "bca_afp_oid": afp_oid,
            "bca_extract_date": attributes.get("EXTRACT_DATE"),
            "bca_sales_refresh_date": attributes.get("SALES_REFRESH_DATE"),
            "source_url": source_url,
            "source_type": "BC_ASSESSMENT",
            "source_version": source_version,
            "observed_at": observed_at,
            "retrieved_at": retrieved,
        })
    return rows


def _real_detail_transport(timeout_seconds: float) -> Transport:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "northshore-property-watchdog/0.1 (+read-only-research)",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.bcassessment.ca/Property/AssessmentSearch",
    })

    def request(url: str, params: Mapping[str, Any]) -> Any:
        response = session.post(url, data=dict(params), timeout=timeout_seconds)
        response.raise_for_status()
        return response.content

    return request


def enrich_bca_sales_details(
    *,
    source_path: str | Path,
    output_path: str | Path,
    max_records: int | None = None,
    min_sale_price: int | None = None,
    max_sale_price: int | None = None,
    delay_seconds: float = 0.05,
    transport: Transport | None = None,
    observed_at: str | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Enrich official sales rows through BCA's public property-information tab."""

    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    sales = source.get("sales", []) if isinstance(source, Mapping) else []
    if not isinstance(sales, list):
        raise ValueError("BCA sales snapshot must contain a sales list")
    if max_records is not None and max_records < 0:
        raise ValueError("max_records must be non-negative")
    if min_sale_price is not None and min_sale_price < 0:
        raise ValueError("min_sale_price must be non-negative")
    if max_sale_price is not None and min_sale_price is not None and max_sale_price < min_sale_price:
        raise ValueError("sale price range is invalid")
    selected_indices = [
        index for index, row in enumerate(sales)
        if (min_sale_price is None or _number(row.get("sale_price")) is not None and _number(row.get("sale_price")) >= min_sale_price)
        and (max_sale_price is None or _number(row.get("sale_price")) is not None and _number(row.get("sale_price")) <= max_sale_price)
    ]
    if max_records is not None:
        selected_indices = selected_indices[:max_records]
    selected_index_set = set(selected_indices)
    selected_count = len(selected_indices)
    observed = observed_at or str(source.get("observed_at") or _iso_now())
    request = transport or _real_detail_transport(timeout_seconds)
    detail_rows = [dict(row) for row in sales]
    detail_status_counts: dict[str, int] = {}
    field_map = {
        "pid": "pid",
        "legal_description": "legal_description",
        "frontage_ft": "frontage_ft",
        "depth_ft": "depth_ft",
        "year_built": "year_built",
        "beds": "bedrooms",
        "baths": "bathrooms",
        "bca_first_floor_area_sqft": "first_floor_area",
        "bca_second_floor_area_sqft": "second_floor_area",
        "bca_basement_finish_area_sqft": "basement_finish_area",
    }
    for index, row in enumerate(detail_rows):
        if index not in selected_index_set:
            row["bca_detail_status"] = "NOT_SELECTED_PRICE_BAND" if min_sale_price is not None or max_sale_price is not None else "NOT_REQUESTED"
            status = str(row["bca_detail_status"])
            detail_status_counts[status] = detail_status_counts.get(status, 0) + 1
            continue
        oid = row.get("bca_oid_evbc")
        detail_source_id = f"bca-property-information:{oid}" if oid else None
        if not oid:
            row["bca_detail_status"] = "NO_OID"
            detail_status_counts["NO_OID"] = detail_status_counts.get("NO_OID", 0) + 1
            continue
        try:
            encoded_oid = base64.b64encode(str(oid).encode("utf-8")).decode("ascii")
            response = request(BCA_PROPERTY_INFORMATION_URL, {"oid": encoded_oid})
            content = response.encode("utf-8") if isinstance(response, str) else bytes(response)
            from .official_enrichment import parse_bc_assessment_payload

            parsed = parse_bc_assessment_payload(
                content,
                BCA_PROPERTY_INFORMATION_URL,
                datetime.fromisoformat(observed.replace("Z", "+00:00")),
            )
            fields = parsed[0] if parsed else {}
            for output_name, input_name in field_map.items():
                if row.get(output_name) in (None, "") and fields.get(input_name) not in (None, ""):
                    row[output_name] = fields[input_name]
            if row.get("frontage_ft") not in (None, "") and row.get("depth_ft") not in (None, ""):
                row["lot_area_sqft"] = round(
                    float(row["frontage_ft"]) * float(row["depth_ft"]),
                    2,
                )
                row["lot_area_source"] = "BCA_PROPERTY_INFORMATION_DIMENSIONS"
            row["bca_detail_status"] = "CONFIRMED" if fields else "EMPTY"
            row["bca_detail_fields"] = sorted(fields)
            source_ids = list(row.get("source_ids") or [])
            if detail_source_id and detail_source_id not in source_ids:
                source_ids.append(detail_source_id)
            row["source_ids"] = source_ids
            source_urls = list(row.get("source_urls") or [])
            if BCA_PROPERTY_INFORMATION_URL not in source_urls:
                source_urls.append(BCA_PROPERTY_INFORMATION_URL)
            row["source_urls"] = source_urls
            raw_observations = list(row.get("raw_observations") or [])
            raw_observations.append({
                "source_id": detail_source_id,
                "source_type": "BC_ASSESSMENT",
                "source_url": BCA_PROPERTY_INFORMATION_URL,
                "observed_at": observed,
                "fields": fields,
            })
            row["raw_observations"] = raw_observations
        except Exception as exc:  # continue a bounded run and audit each failed row
            row["bca_detail_status"] = "FAILED"
            row["bca_detail_error"] = type(exc).__name__
        status = str(row["bca_detail_status"])
        detail_status_counts[status] = detail_status_counts.get(status, 0) + 1
        if delay_seconds > 0 and index != selected_indices[-1]:
            time.sleep(delay_seconds)

    enriched = dict(source)
    enriched.update({
        "schema_version": "bca-arcgis-sales-v1.1-enriched",
        "detail_source_url": BCA_PROPERTY_INFORMATION_URL,
        "detail_observed_at": observed,
        "detail_records_requested": selected_count,
        "detail_selection": {
            "min_sale_price": min_sale_price,
            "max_sale_price": max_sale_price,
            "max_records": max_records,
        },
        "detail_status_counts": detail_status_counts,
        "sales": detail_rows,
    })
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            enriched,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    return {
        "output_path": str(output),
        "records_total": len(detail_rows),
        "records_requested": selected_count,
        "records_enriched": detail_status_counts.get("CONFIRMED", 0),
        "detail_status_counts": detail_status_counts,
    }


def merge_bca_sales_spatial_snapshot(
    *,
    detail_path: str | Path,
    spatial_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Merge a geometry-only refresh into an already enriched local snapshot."""

    detail = json.loads(Path(detail_path).read_text(encoding="utf-8"))
    spatial = json.loads(Path(spatial_path).read_text(encoding="utf-8"))
    detail_rows = detail.get("sales", []) if isinstance(detail, Mapping) else []
    spatial_rows = spatial.get("sales", []) if isinstance(spatial, Mapping) else []
    spatial_by_key = {
        str(row.get("bca_afp_oid") or row.get("record_id")): row
        for row in spatial_rows
        if isinstance(row, Mapping) and (row.get("bca_afp_oid") or row.get("record_id"))
    }
    output_rows: list[dict[str, Any]] = []
    matched = 0
    for raw in detail_rows if isinstance(detail_rows, list) else []:
        row = dict(raw)
        key = str(row.get("bca_afp_oid") or row.get("record_id") or "")
        spatial_row = spatial_by_key.get(key)
        if spatial_row is None:
            row["spatial_status"] = "NO_MATCH"
        else:
            for field in ("latitude", "longitude", "geometry_source"):
                if spatial_row.get(field) not in (None, ""):
                    row[field] = spatial_row[field]
            row["spatial_status"] = "CONFIRMED"
            matched += 1
        if row.get("frontage_ft") not in (None, "") and row.get("depth_ft") not in (None, ""):
            row["lot_area_sqft"] = round(
                float(row["frontage_ft"]) * float(row["depth_ft"]),
                2,
            )
            row["lot_area_source"] = "BCA_PROPERTY_INFORMATION_DIMENSIONS"
        output_rows.append(row)
    merged = dict(detail)
    merged.update({
        "schema_version": "bca-arcgis-sales-v1.1-enriched-spatial",
        "spatial_source_url": spatial.get("source_url") if isinstance(spatial, Mapping) else None,
        "spatial_source_version": spatial.get("source_version") if isinstance(spatial, Mapping) else None,
        "spatial_observed_at": spatial.get("observed_at") if isinstance(spatial, Mapping) else None,
        "spatial_records_matched": matched,
        "sales": output_rows,
    })
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    return {
        "output_path": str(output),
        "records_total": len(output_rows),
        "records_matched": matched,
        "records_unmatched": len(output_rows) - matched,
    }


def _real_transport(timeout_seconds: float) -> Transport:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "northshore-property-watchdog/0.1 (+read-only-research)",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Referer": BCA_MAP_SEARCH_URL,
    })

    def request(url: str, params: Mapping[str, Any]) -> Any:
        response = session.get(url, params=dict(params), timeout=timeout_seconds)
        response.raise_for_status()
        if url == BCA_MAP_SEARCH_URL:
            return response.text
        return response.json()

    return request


def fetch_bca_arcgis_sales(
    *,
    output_path: str | Path,
    start_date: str | date,
    end_date: str | date,
    bbox: tuple[float, float, float, float] = BCA_DEFAULT_BBOX,
    min_sale_price: int = 1_000_000,
    max_sale_price: int = 3_500_000,
    page_size: int = 2_000,
    max_pages: int = 100,
    transport: Transport | None = None,
    observed_at: str | None = None,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Fetch the bounded public BCA sales layer and write a versioned JSON snapshot."""

    if len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("bbox must be (min_longitude, min_latitude, max_longitude, max_latitude)")
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")

    observed = observed_at or _iso_now()
    request = transport or _real_transport(timeout_seconds)
    map_page = request(BCA_MAP_SEARCH_URL, {})
    map_text = map_page.decode("utf-8", errors="replace") if isinstance(map_page, bytes) else str(map_page)
    token_match = re.search(r"\bgistoken\s*=\s*['\"]([^'\"]+)['\"]", map_text)
    token = token_match.group(1) if token_match else None
    mapserver_match = re.search(r"\bmapserverUrl\s*=\s*['\"]([^'\"]+)['\"]", map_text)
    mapserver_url = mapserver_match.group(1) if mapserver_match else BCA_DEFAULT_MAPSERVER_URL
    if not token:
        raise RuntimeError("BC Assessment map page did not expose a guest GIS token")

    query_url = f"{mapserver_url.rstrip('/')}/0/query"
    source_version = _layer_version(mapserver_url)
    where = build_bca_sales_where(
        start_date=start_date,
        end_date=end_date,
        min_sale_price=min_sale_price,
        max_sale_price=max_sale_price,
    )
    rows: list[dict[str, Any]] = []
    pages = 0
    # This layer advertises a 2,000-record service limit but currently caps
    # query responses at 1,000.  Advance by the actual returned feature count
    # so a server-side cap cannot silently create a gap between pages.
    effective_page_size = min(page_size, 1_000)
    offset = 0
    for page in range(max_pages):
        payload = request(query_url, {
            "where": where,
            "geometry": ",".join(str(value) for value in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "returnGeometry": "true",
            "outSR": "4326",
            "outFields": "*",
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": effective_page_size,
            "f": "json",
            "token": token,
        })
        if not isinstance(payload, Mapping):
            raise RuntimeError("BC Assessment ArcGIS query returned a non-object payload")
        if payload.get("error"):
            raise RuntimeError(f"BC Assessment ArcGIS query failed: {payload['error']}")
        page_rows = parse_bca_sales_features(
            payload,
            source_url=query_url,
            source_version=source_version,
            observed_at=observed,
        )
        rows.extend(page_rows)
        pages += 1
        features = payload.get("features")
        feature_count = len(features) if isinstance(features, list) else 0
        if not payload.get("exceededTransferLimit") and feature_count < effective_page_size:
            break
        if feature_count == 0:
            break
        offset += feature_count
    else:
        raise RuntimeError(f"BC Assessment ArcGIS query exceeded max_pages={max_pages}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "schema_version": "bca-arcgis-sales-v1",
        "source_type": "BC_ASSESSMENT",
        "source_url": query_url,
        "source_version": source_version,
        "observed_at": observed,
        "retrieved_at": _iso_now(),
        "query": {
            "where": where,
            "bbox": list(bbox),
            "start_date": start_date.isoformat() if isinstance(start_date, date) else str(start_date),
            "end_date": end_date.isoformat() if isinstance(end_date, date) else str(end_date),
            "min_sale_price": min_sale_price,
            "max_sale_price": max_sale_price,
        },
        "pages": pages,
        "sales": rows,
    }
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "output_path": str(output),
        "records": len(rows),
        "pages": pages,
        "source_url": query_url,
        "source_version": source_version,
    }


__all__ = [
    "BCA_DEFAULT_BBOX",
    "BCA_DEFAULT_MAPSERVER_URL",
    "BCA_MAP_SEARCH_URL",
    "BCA_PROPERTY_INFORMATION_URL",
    "build_bca_sales_where",
    "enrich_bca_sales_details",
    "fetch_bca_arcgis_sales",
    "merge_bca_sales_spatial_snapshot",
    "parse_bca_sales_features",
]
