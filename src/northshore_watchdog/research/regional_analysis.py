"""Small, auditable summaries for polygon-bounded listing discovery runs.

This module intentionally stops at discovery analysis.  A card returned by a
native REALTOR.ca filter is evidence that the search returned that card; it is
not evidence that BCA, title, zoning, school, lot geometry, or economics have
been verified.  Those fields therefore remain visible as missing data instead
of being converted into a score or recommendation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from .boundaries import geometry_to_multipolygon, point_in_multipolygon


_CARD_FIELDS = (
    "mls",
    "address",
    "asking",
    "beds",
    "baths",
    "sqft",
    "lot_dimensions",
    "lat",
    "lon",
    "url",
)
_PRICE_BANDS = (
    (0, 1_300_000, "< $1.30M"),
    (1_300_000, 1_500_000, "$1.30M–$1.50M"),
    (1_500_000, 1_600_000, "$1.50M–$1.60M"),
    (1_600_000, float("inf"), "$1.60M+"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _price_stats(prices: Sequence[float]) -> dict[str, float | None]:
    if not prices:
        return {"min": None, "median": None, "max": None}
    return {
        "min": min(prices),
        "median": median(prices),
        "max": max(prices),
    }


def _band_counts(prices: Sequence[float]) -> dict[str, int]:
    return {
        label: sum(lower <= price < upper for price in prices)
        for lower, upper, label in _PRICE_BANDS
    }


def _point_status(card: Mapping[str, Any], geometry: Mapping[str, Any] | None) -> str:
    latitude = _number(card.get("lat", card.get("latitude")))
    longitude = _number(card.get("lon", card.get("longitude")))
    if latitude is None or longitude is None or geometry is None:
        return "unknown"
    try:
        polygons = geometry_to_multipolygon(geometry)
        return "inside" if point_in_multipolygon((longitude, latitude), polygons) else "outside"
    except (TypeError, ValueError, IndexError):
        return "unknown"


def _lowest_cards(cards: Sequence[Mapping[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    usable = [card for card in cards if card.get("address")]
    ordered = sorted(usable, key=lambda card: (_number(card.get("asking")) is None, _number(card.get("asking")) or 0))
    return [
        {
            "address": card.get("address"),
            "mls": card.get("mls"),
            "asking": card.get("asking"),
            "beds": card.get("beds"),
            "baths": card.get("baths"),
            "sqft": card.get("sqft"),
            "url": card.get("url"),
        }
        for card in ordered[:limit]
    ]


def _region_summary(preset: Mapping[str, Any], region: Mapping[str, Any]) -> dict[str, Any]:
    raw_cards = preset.get("cards", [])
    cards = [card for card in raw_cards if isinstance(card, Mapping)] if isinstance(raw_cards, list) else []
    prices = [price for price in (_number(card.get("asking")) for card in cards) if price is not None]
    point_counts = Counter(_point_status(card, region.get("partition_geometry") or region.get("geometry")) for card in cards)
    missing = {
        field: sum(card.get(field) in (None, "") for card in cards)
        for field in _CARD_FIELDS
    }
    family_proxy_count = sum(
        (_int_or_none(card.get("beds")) or 0) >= 3
        and (_number(card.get("baths")) or 0) >= 2
        and (_number(card.get("sqft")) or 0) >= 1250
        for card in cards
    )
    reported = _int_or_none(preset.get("result_count"))
    parsed = _int_or_none(preset.get("parsed_cards"))
    return {
        "preset": preset.get("preset"),
        "boundary_id": region.get("boundary_id", preset.get("preset")),
        "observed_at": preset.get("observed_at"),
        "label": region.get("label", preset.get("preset")),
        "market_bucket": region.get("market_bucket"),
        "source_local_areas": list(region.get("source_local_areas", [])),
        "configured_price_min": preset.get("configured_price_min"),
        "configured_price_max": preset.get("configured_price_max"),
        "reported_results": reported if reported is not None else len(cards),
        "parsed_cards": parsed if parsed is not None else len(cards),
        "validated_cards": _int_or_none(preset.get("validated_cards")) or len(cards),
        "missing_address_observations": max((reported or len(cards)) - len(cards), 0),
        "parse_warnings": list(preset.get("parse_warnings", [])),
        "boundary_point_counts": {
            "inside": point_counts.get("inside", 0),
            "outside": point_counts.get("outside", 0),
            "unknown": point_counts.get("unknown", 0),
        },
        "price": _price_stats(prices),
        "price_bands": _band_counts(prices),
        "beds_distribution": dict(sorted(Counter(_int_or_none(card.get("beds")) for card in cards if _int_or_none(card.get("beds")) is not None).items())),
        "baths_distribution": dict(sorted(Counter(_number(card.get("baths")) for card in cards if _number(card.get("baths")) is not None).items())),
        "family_proxy_count": family_proxy_count,
        "field_missing_counts": missing,
        "lowest_price_cards": _lowest_cards(cards),
        "raw_snapshot_path": preset.get("raw_snapshot_path"),
        "validation_reasons": list(preset.get("validation_reasons", [])),
    }


def analyze_regional_boundary_scan(
    scan_payload: Mapping[str, Any],
    boundary_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Return discovery-only statistics with official-boundary checks."""

    regions = {
        str(region.get("boundary_id")): region
        for region in boundary_registry.get("regions", [])
        if isinstance(region, Mapping) and region.get("boundary_id")
    }
    summaries: list[dict[str, Any]] = []
    for preset in scan_payload.get("presets", []):
        if not isinstance(preset, Mapping):
            continue
        name = str(preset.get("preset") or "")
        boundary_id = str(preset.get("boundary_id") or name)
        region = regions.get(boundary_id, {"boundary_id": boundary_id, "label": name, "geometry": None})
        summaries.append(_region_summary(preset, region))

    return {
        "observed_at": scan_payload.get("observed_at"),
        "boundary_schema_version": boundary_registry.get("schema_version"),
        "boundary_version": boundary_registry.get("boundary_version"),
        "boundary_registry_source_url": boundary_registry.get("source_url"),
        "partition_overlap_check": boundary_registry.get("partition_overlap_check", {"status": "UNKNOWN"}),
        "region_count": len(summaries),
        "total_reported_results": sum(item["reported_results"] or 0 for item in summaries),
        "total_parsed_cards": sum(item["parsed_cards"] or 0 for item in summaries),
        "total_validated_cards": sum(item["validated_cards"] or 0 for item in summaries),
        "total_missing_address_observations": sum(item["missing_address_observations"] for item in summaries),
        "total_boundary_outside_points": sum(item["boundary_point_counts"]["outside"] for item in summaries),
        "investment_ranking_status": "NOT_RUN_DISCOVERY_ONLY",
        "regions": summaries,
    }


__all__ = ["analyze_regional_boundary_scan"]
