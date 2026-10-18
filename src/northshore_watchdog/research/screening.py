"""Filter-first screening with independent value axes.

Stage A is deliberately cheap and conservative. Stage B scores every normal
listing independently for current-use value, redevelopment readiness, and
data confidence. It does not force a property into a single use track and it
does not claim official zoning, assessment, comps, or project economics.
"""

from __future__ import annotations

import csv
from html import unescape
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .discovery import DiscoveryCard, _evaluate_filter, _number_for_compare


_DIMENSIONS_RE = re.compile(
    r"(?<!\w)(?P<width>\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?\s*"
    r"(?:x|×)\s*"
    r"(?P<depth>\d[\d,]*(?:\.\d+)?)\s*(?:'|ft\.?|feet)?",
    re.IGNORECASE,
)
_DOM_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>hour|day|week|month|year)s?", re.I)
_PLACEHOLDER_RE = re.compile(r"^(?:n/?a|na|unknown|not available|not provided|-+)$", re.I)

_DEFAULT_STRATA: dict[str, tuple[str, ...]] = {
    "WESTSIDE": (
        "point grey", "west point grey", "dunbar", "arbutus", "kitsilano",
        "kerrisdale", "mackenzie heights", "shaughnessy", "southlands",
    ),
    "CENTRAL_CORE": (
        "mount pleasant", "main", "riley park", "grandview-woodland",
        "cambie", "fairview", "fraser",
    ),
    "SOUTH_VANCOUVER": (
        "sunset", "victoria-fraserview", "marpole", "south marine", "killarney",
    ),
    "EAST_VANCOUVER": (
        "renfrew", "hastings-sunrise", "collingwood", "kensington-cedar cottage",
        "knight", "victoria", "cascade-schou",
    ),
}
_DEFAULT_AREA_PROFILES = {
    "WESTSIDE": {"tier": "A", "score": 18.0},
    "CENTRAL_CORE": {"tier": "B+", "score": 15.0},
    "SOUTH_VANCOUVER": {"tier": "B-", "score": 11.0},
    "EAST_VANCOUVER": {"tier": "B-", "score": 10.0},
    # Unknown is not a weak neighbourhood; it is an unmapped one. Keep it at
    # the neutral midpoint and expose the missing mapping through metadata.
    "UNKNOWN": {"tier": "UNKNOWN", "score": 10.0},
}
_DEFAULT_ROAD_TOKENS = {
    "extreme": (
        "direct highway exposure", "highway adjacency", "highway noise",
        "railway adjacency", "extreme truck corridor",
    ),
    "major": (
        "boundary road", "knight street", "kingsway", "nanaimo street",
        "marine drive", "victoria drive", "major arterial", "truck route",
    ),
    "arterial": (
        "arterial road", "direct arterial exposure", "commercial frontage",
        "heavy bus route",
    ),
    "collector": ("collector road", "moderately busy local road"),
}
_DESCRIPTION_ROAD_TOKENS = {
    "extreme": ("direct highway exposure", "highway adjacency", "highway noise", "railway adjacency", "extreme truck corridor"),
    "major": ("major arterial", "truck route", "direct highway frontage"),
    "arterial": ("arterial road", "direct arterial exposure", "commercial frontage", "heavy bus route"),
    "collector": ("collector road", "moderately busy local road"),
}
_ROAD_PENALTIES = {"extreme": -15.0, "major": -12.0, "arterial": -8.0, "collector": -3.0, "quiet": 0.0, "unknown": 0.0}

_STREET_SUFFIX_ALIASES = {
    "ST": "STREET", "STREET": "STREET",
    "AVE": "AVENUE", "AV": "AVENUE", "AVENUE": "AVENUE",
    "RD": "ROAD", "ROAD": "ROAD",
    "DR": "DRIVE", "DRIVE": "DRIVE",
    "BLVD": "BOULEVARD", "BOULEVARD": "BOULEVARD",
    "HWY": "HIGHWAY", "HIGHWAY": "HIGHWAY",
    "PKWY": "PARKWAY", "PARKWAY": "PARKWAY",
    "CT": "COURT", "COURT": "COURT",
    "PL": "PLACE", "PLACE": "PLACE",
}

# The monetary road adjustment is intentionally separate from the qualitative
# ``road_penalty`` used by the current-use score.  These are the user-approved
# initial priors for a property exposed to the road versus a comparable quiet
# interior residential street.  The midpoint is used for ranking; the full
# interval is retained for review and sensitivity analysis.
_DEFAULT_ROAD_DISCOUNT_BANDS: dict[str, tuple[float, float, float]] = {
    "collector": (0.02, 0.03, 0.04),
    "arterial": (0.05, 0.065, 0.08),
    "high_flow_arterial": (0.07, 0.085, 0.10),
    "arterial_bus_truck": (0.08, 0.10, 0.12),
    "arterial_truck_intersection": (0.10, 0.125, 0.15),
    "extreme": (0.10, 0.125, 0.15),
    "quiet": (0.0, 0.0, 0.0),
}
_DEFAULT_ROAD_DISCOUNT_MARKERS = {
    "bus": ("bus route", "bus corridor", "frequent bus", "heavy bus", "high-frequency bus"),
    "truck": ("truck route", "truck corridor", "heavy truck", "truck traffic"),
    "high_flow": ("high traffic", "high-volume", "high volume", "busy arterial", "primary arterial"),
    "intersection": ("intersection", "major intersection", "busy intersection", "corner exposure"),
    "high_noise": ("high noise", "heavy noise", "traffic noise", "noise exposure"),
}
_DEFAULT_KNOWN_BUS_TRUCK_ROADS = (
    "nanaimo street",
)

_ROAD_QUALITY_BY_CATEGORY = {
    "EXTREME_HIGHWAY_RAIL": 0.0,
    "ARTERIAL_TRUCK_INTERSECTION": 2.5,
    "ARTERIAL_BUS_TRUCK": 2.5,
    "HIGH_FLOW_ARTERIAL": 4.0,
    "ORDINARY_ARTERIAL": 5.5,
    "COLLECTOR": 7.5,
    "QUIET_RESIDENTIAL": 10.0,
    "BACKGROUND_ROAD": 10.0,
    "UNKNOWN_ROAD": 5.0,
}
_ROAD_EXPOSURE_BANDS = {
    "impactful": ((50.0, 0.90), (100.0, 0.75), (200.0, 0.50), (300.0, 0.25), (400.0, 0.10)),
    "ordinary_arterial": ((50.0, 0.75), (100.0, 0.50), (150.0, 0.25), (250.0, 0.10)),
    "collector": ((50.0, 0.50), (100.0, 0.25)),
}


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def _street_component(value: Any) -> str:
    """Normalize a civic address street for cached road-frontage repair."""

    tokens = re.sub(r"[^A-Za-z0-9 ]+", " ", _text(value).upper()).split()
    if tokens and tokens[0].isdigit():
        tokens = tokens[1:]
    if tokens:
        tokens[-1] = _STREET_SUFFIX_ALIASES.get(tokens[-1], tokens[-1])
    return " ".join(tokens)


def _effective_property_frontage(card: DiscoveryCard) -> bool | None:
    """Use saved road frontage and repair an old suffix-only false negative."""

    raw_value = _card_value(
        card,
        "official_property_fronts_major_road",
        "property_fronts_major_road",
    )
    parsed = _boolish(raw_value)
    nearest = _card_value(card, "official_nearest_major_road", "nearest_major_road")
    if (
        nearest not in (None, "")
        and _street_component(card.address)
        and _street_component(card.address) == _street_component(nearest)
    ):
        return True
    return parsed


def _property_frontage_method(card: DiscoveryCard) -> str:
    raw_value = _card_value(
        card,
        "official_property_fronts_major_road",
        "property_fronts_major_road",
    )
    parsed = _boolish(raw_value)
    nearest = _card_value(card, "official_nearest_major_road", "nearest_major_road")
    if (
        nearest not in (None, "")
        and _street_component(card.address) == _street_component(nearest)
    ):
        return "OFFICIAL_ROAD_NAME_NORMALIZED"
    if parsed is not None:
        return "OFFICIAL_FRONTAGE_FLAG"
    return "UNKNOWN"


def _road_exposure_factor(
    *,
    road_key: str,
    category: str,
    distance_m: Any,
    property_fronts_major_road: Any,
    exposure_bands: Mapping[str, Any] | None = None,
) -> float:
    """Return exposure to an impactful road, after frontage confirmation.

    The distance is a fallback when direct frontage is not confirmed. Local
    streets have no negative-road exposure; higher-impact roads retain a
    longer, gradually decaying background effect.
    """

    if road_key in {"quiet", "unknown"}:
        return 0.0
    if _boolish(property_fronts_major_road) is True:
        return 1.0
    distance = _number(distance_m)
    if distance is None:
        return 0.5

    configured_bands = exposure_bands if isinstance(exposure_bands, Mapping) else {}

    def bands_for(name: str, fallback: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
        configured = configured_bands.get(name)
        if not isinstance(configured, Mapping):
            return fallback
        parsed: list[tuple[float, float]] = []
        for upper_bound, factor in configured.items():
            upper = _number(upper_bound)
            value = _number(factor)
            if upper is not None and value is not None and upper >= 0:
                parsed.append((upper, max(0.0, min(1.0, value))))
        return tuple(sorted(parsed)) or fallback

    if category in {
        "EXTREME_HIGHWAY_RAIL",
        "ARTERIAL_TRUCK_INTERSECTION",
        "ARTERIAL_BUS_TRUCK",
        "HIGH_FLOW_ARTERIAL",
    }:
        bands = bands_for("impactful", _ROAD_EXPOSURE_BANDS["impactful"])
    elif road_key == "collector":
        bands = bands_for("collector", _ROAD_EXPOSURE_BANDS["collector"])
    else:
        bands = bands_for("ordinary_arterial", _ROAD_EXPOSURE_BANDS["ordinary_arterial"])
    for upper_bound, factor in bands:
        if distance <= upper_bound:
            return factor
    return 0.0


def calculate_road_quality(
    *,
    road_class: Any = None,
    road_category: Any = None,
    distance_m: Any = None,
    property_fronts_major_road: Any = None,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate the road portion of the 15-point micro-location score.

    Road quality is capped at six points. The remaining nine points belong to
    other immediate micro-location facts, so a road defect cannot erase the
    whole micro-location score.
    """

    road_key = {
        "a": "extreme", "b": "major", "c": "arterial", "d": "collector", "e": "quiet",
        "extreme": "extreme", "major": "major", "arterial": "arterial",
        "collector": "collector", "quiet": "quiet",
    }.get(_text(road_class), "unknown")
    category = _text(road_category).upper().replace(" ", "_")
    if not category:
        category = {
            "extreme": "EXTREME_HIGHWAY_RAIL",
            "major": "HIGH_FLOW_ARTERIAL",
            "arterial": "ORDINARY_ARTERIAL",
            "collector": "COLLECTOR",
            "quiet": "QUIET_RESIDENTIAL",
            "unknown": "UNKNOWN_ROAD",
        }[road_key]
    policy = policy if isinstance(policy, Mapping) else {}
    configured_quality = policy.get("direct_quality_by_category")
    configured_quality = configured_quality if isinstance(configured_quality, Mapping) else {}
    direct_quality = _number(configured_quality.get(category))
    if direct_quality is None:
        direct_quality = float(_ROAD_QUALITY_BY_CATEGORY.get(category, 5.0))
    road_points = _number(policy.get("road_points")) or 6.0
    road_points = max(0.0, road_points)
    exposure_factor = _road_exposure_factor(
        road_key=road_key,
        category=category,
        distance_m=distance_m,
        property_fronts_major_road=property_fronts_major_road,
        exposure_bands=policy.get("exposure_bands_m"),
    )
    if road_key == "unknown":
        # Missing road evidence is a neutral midpoint, not a quiet-street
        # bonus and not a zero. A confirmed quiet local street is handled by
        # the full-score branch below.
        effective_quality = 5.0
    elif category == "BACKGROUND_ROAD" or exposure_factor == 0.0:
        effective_quality = 10.0
    else:
        effective_quality = 10.0 - (10.0 - direct_quality) * exposure_factor
    micro_score = round(effective_quality / 10.0 * road_points, 2)
    return {
        "road_class": road_key.upper(),
        "road_category": category,
        "direct_road_quality_score": round(direct_quality, 2),
        "exposure_factor": round(exposure_factor, 2),
        "effective_road_quality_score": round(effective_quality, 3),
        "micro_location_road_score": micro_score,
        "micro_location_road_penalty": round(micro_score - road_points, 2),
        "road_points": road_points,
        "property_fronts_major_road": _boolish(property_fronts_major_road),
        "distance_m": _number(distance_m),
    }


def calculate_road_discount(
    *,
    road_class: Any = None,
    distance_m: Any = None,
    property_fronts_major_road: Any = None,
    road_name: Any = None,
    description: Any = None,
    bus_route: Any = None,
    truck_route: Any = None,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the approved initial road-discount prior.

    Returned percentages are positive discount magnitudes relative to a quiet
    interior residential street. ``base_pct`` is the midpoint used by the
    ranking model; the low/high values stay visible for review and sensitivity
    analysis. Exposure is scaled only when the property is not directly
    exposed to the identified road.
    """

    policy = policy if isinstance(policy, Mapping) else {}
    if policy.get("enabled", True) is False:
        return {
            "status": "DISABLED",
            "method": "USER_APPROVED_INITIAL_ROAD_DISCOUNT_PRIOR",
            "road_class": _text(road_class).upper() or "UNKNOWN",
            "category": "DISABLED",
            "low_pct": 0.0,
            "base_pct": 0.0,
            "high_pct": 0.0,
            "unscaled_low_pct": 0.0,
            "unscaled_base_pct": 0.0,
            "unscaled_high_pct": 0.0,
            "exposure_factor": 0.0,
            "distance_m": _number(distance_m),
            "property_fronts_major_road": _boolish(property_fronts_major_road),
            "bus_route_signal": bool(_boolish(bus_route)),
            "truck_route_signal": bool(_boolish(truck_route)),
            "intersection_signal": False,
            "high_noise_signal": False,
        }
    configured_bands = policy.get("bands", {})
    configured_bands = configured_bands if isinstance(configured_bands, Mapping) else {}
    configured_markers = policy.get("markers", {})
    configured_markers = configured_markers if isinstance(configured_markers, Mapping) else {}
    configured_routes = policy.get("known_bus_truck_roads", _DEFAULT_KNOWN_BUS_TRUCK_ROADS)
    configured_routes = (
        tuple(_text(item) for item in configured_routes)
        if isinstance(configured_routes, (list, tuple, set))
        else _DEFAULT_KNOWN_BUS_TRUCK_ROADS
    )

    bands = dict(_DEFAULT_ROAD_DISCOUNT_BANDS)
    for key, default in tuple(bands.items()):
        configured = configured_bands.get(key)
        if not isinstance(configured, Mapping):
            continue
        values = (
            configured.get("low_pct", default[0]),
            configured.get("base_pct", default[1]),
            configured.get("high_pct", default[2]),
        )
        try:
            bands[key] = tuple(float(value) for value in values)  # type: ignore[assignment]
        except (TypeError, ValueError):
            continue

    def markers(name: str) -> tuple[str, ...]:
        configured = configured_markers.get(name)
        if isinstance(configured, (list, tuple, set)):
            return tuple(_text(item) for item in configured)
        return _DEFAULT_ROAD_DISCOUNT_MARKERS[name]

    road_key = {
        "a": "extreme", "b": "major", "c": "arterial", "d": "collector", "e": "quiet",
        "extreme": "extreme", "major": "major", "arterial": "arterial",
        "collector": "collector", "quiet": "quiet",
    }.get(_text(road_class), "unknown")
    road_text = f"{_text(road_name)} {_text(description)}"
    bus = _boolish(bus_route)
    truck = _boolish(truck_route)
    if bus is None:
        bus = any(marker in road_text for marker in markers("bus"))
    if truck is None:
        truck = any(marker in road_text for marker in markers("truck"))
    if any(route and route in road_text for route in configured_routes):
        bus = True
        truck = True
    high_flow = any(marker in road_text for marker in markers("high_flow"))
    intersection = any(marker in road_text for marker in markers("intersection"))
    high_noise = any(marker in road_text for marker in markers("high_noise"))

    if road_key == "quiet":
        category = "QUIET_RESIDENTIAL"
        band_key = "quiet"
    elif road_key == "collector":
        category = "COLLECTOR"
        band_key = "collector"
    elif road_key == "extreme":
        category = "EXTREME_HIGHWAY_RAIL"
        band_key = "extreme"
    elif road_key in {"major", "arterial"}:
        if truck and (intersection or high_noise):
            category = "ARTERIAL_TRUCK_INTERSECTION"
            band_key = "arterial_truck_intersection"
        elif truck or bus:
            category = "ARTERIAL_BUS_TRUCK"
            band_key = "arterial_bus_truck"
        elif high_flow:
            category = "HIGH_FLOW_ARTERIAL"
            band_key = "high_flow_arterial"
        else:
            category = "ORDINARY_ARTERIAL"
            band_key = "arterial"
    else:
        category = "UNKNOWN_ROAD"
        band_key = "quiet"

    fronts = _boolish(property_fronts_major_road)
    distance = _number(distance_m)
    direct_distance = _number(policy.get("direct_frontage_distance_m")) or 50.0
    near_distance = _number(policy.get("near_road_distance_m")) or 100.0
    extended_distance = _number(policy.get("extended_road_distance_m")) or 200.0
    exposure_factor = _road_exposure_factor(
        road_key=road_key,
        category=category,
        distance_m=distance,
        property_fronts_major_road=fronts,
    )

    low, base, high = bands[band_key]
    low = round(low * exposure_factor, 4)
    base = round(base * exposure_factor, 4)
    high = round(high * exposure_factor, 4)
    if exposure_factor == 0.0 and road_key not in {"quiet", "unknown"}:
        category = "BACKGROUND_ROAD"
        status = "BACKGROUND"
    elif road_key == "unknown":
        status = "UNKNOWN"
    else:
        status = "APPLIED"
    return {
        "status": status,
        "method": "USER_APPROVED_INITIAL_ROAD_DISCOUNT_PRIOR",
        "road_class": road_key.upper(),
        "category": category,
        "low_pct": low,
        "base_pct": base,
        "high_pct": high,
        "unscaled_low_pct": round(bands[band_key][0], 4),
        "unscaled_base_pct": round(bands[band_key][1], 4),
        "unscaled_high_pct": round(bands[band_key][2], 4),
        "exposure_factor": round(exposure_factor, 2),
        "distance_m": distance,
        "property_fronts_major_road": fronts,
        "bus_route_signal": bool(bus),
        "truck_route_signal": bool(truck),
        "intersection_signal": bool(intersection),
        "high_noise_signal": bool(high_noise),
    }


_ASSET_CLASSES = (
    "LIVE_HOME",
    "HOLD_5_10_REDEVELOP",
    "DEVELOPMENT_ONLY",
)
_DEFAULT_CLASS_WEIGHTS: dict[str, dict[str, float]] = {
    "LIVE_HOME": {
        "area_neighbourhood_value": 20.0,
        "micro_location": 15.0,
        "family_layout": 20.0,
        "maintenance_systems": 20.0,
        "school_proxy": 10.0,
        "area_relative_price_signal": 10.0,
        "lot_sufficiency": 5.0,
    },
    "HOLD_5_10_REDEVELOP": {
        "area_neighbourhood_value": 10.0,
        "micro_location": 10.0,
        "holding_house_fit": 25.0,
        "school_proxy": 10.0,
        "lot_sufficiency": 15.0,
        "redevelopment_path": 15.0,
        "area_relative_price_signal": 10.0,
        "seller_fatigue": 5.0,
    },
    "DEVELOPMENT_ONLY": {
        "lot_geometry": 20.0,
        "current_entitlement": 25.0,
        "toa_threshold_assembly": 10.0,
        "product_market_fit": 10.0,
        "area_adjusted_land_price_proxy": 20.0,
        "permanent_execution_risk": 10.0,
        "seller_fatigue": 5.0,
    },
}


class ScreeningDecision(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    card: DiscoveryCard
    stage: str
    status: str
    priority: str = "UNKNOWN"
    track: str = "NEED_CLASSIFICATION"
    asset_class: str = "NEED_CLASSIFICATION"
    classification_status: str = "NOT_CLASSIFIED"
    classification_confidence: float | None = None
    confidence_score: float | None = None
    current_use_index: float | None = None
    redevelopment_readiness_index: float | None = None
    current_use_components: dict[str, float] = Field(default_factory=dict)
    redevelopment_components: dict[str, float] = Field(default_factory=dict)
    current_use_score_components: dict[str, float] = Field(default_factory=dict)
    redevelopment_score_components: dict[str, float] = Field(default_factory=dict)
    current_use_score: float | None = None
    redevelopment_score: float | None = None
    dual_value_score: float | None = None
    area_relative_price_signal: float | None = None
    seller_fatigue_score: float | None = None
    value_profile: str = "UNCLASSIFIED"
    class_score: float | None = None
    area_stratum: str = "UNKNOWN"
    area_tier: str = "UNKNOWN"
    stage_b_score: float | None = None
    home_fit_score: float | None = None
    land_adequacy_score: float | None = None
    school_proxy_status: str = "UNKNOWN"
    location_residual_cad: float | None = None
    location_standard_property_value: float | None = None
    location_residual_score: float | None = None
    location_residual_segment: str | None = None
    location_residual_confidence: str = "UNKNOWN"
    location_residual_source: str | None = None
    regional_standard_value: float | None = None
    regional_price_dislocation_cad: float | None = None
    regional_price_dislocation_pct: float | None = None
    physical_adjusted_expected_value: float | None = None
    physical_price_dislocation_cad: float | None = None
    physical_price_dislocation_pct: float | None = None
    current_use_effective_basis: float | None = None
    market_house_value_proxy: float | None = None
    market_land_basis: float | None = None
    area_normalized_lot_basis: float | None = None
    area_normalized_lot_basis_per_sqft: float | None = None
    risk_adjusted_lot_basis: float | None = None
    risk_adjusted_lot_basis_per_sqft: float | None = None
    area_standard_lot_equivalent_basis: float | None = None
    risk_adjusted_standard_lot_equivalent_basis: float | None = None
    current_package_value_proxy: float | None = None
    current_package_value_proxy_source: str = "UNKNOWN"
    current_package_value_ratio: float | None = None
    current_lot_basis_per_sqft: float | None = None
    current_lot_basis_ratio: float | None = None
    current_value_gap_cad: float | None = None
    regional_price_ratio: float | None = None
    regional_price_discount_pct: float | None = None
    bca_discount_pct: float | None = None
    house_value_for_money_score: float | None = None
    house_condition_score: float | None = None
    current_package_value_for_money_score: float | None = None
    location_value_for_money_score: float | None = None
    location_quality_score: float | None = None
    lot_value_for_money_score: float | None = None
    current_lot_value_for_money_score: float | None = None
    redevelopment_lot_value_for_money_score: float | None = None
    bca_price_safety_score: float | None = None
    permanent_risk_score: float | None = None
    current_use_value_for_money_score: float | None = None
    redevelopment_value_for_money_score: float | None = None
    redevelopment_capacity_value_for_money_score: float | None = None
    redevelopment_geometry_adequacy_score: float | None = None
    current_use_reno_cap: float | None = None
    maintenance_systems_score: float | None = None
    market_land_share_prior: float | None = None
    bca_land_share: float | None = None
    physical_lot_factor: float | None = None
    normalized_land_value: float | None = None
    normalized_existing_house_value: float | None = None
    normalized_lot_basis: float | None = None
    normalized_lot_basis_per_sqft: float | None = None
    bca_house_value_proxy: float | None = None
    bca_normalized_land_basis: float | None = None
    bca_normalized_land_basis_per_sqft: float | None = None
    current_use_all_in_basis: float | None = None
    current_use_basis_price_ratio: float | None = None
    reno_estimate: float | None = None
    reno_score: float | None = None
    reno_band: str = "UNKNOWN"
    reno_year: int | None = None
    reno_scope: list[str] = Field(default_factory=list)
    reno_evidence: list[str] = Field(default_factory=list)
    reno_confidence: str = "UNKNOWN"
    normalized_reference_land_value: float | None = None
    normalized_reference_land_price_per_sqft: float | None = None
    physical_normalization_status: str = "NOT_APPLIED"
    regional_price_safety_score: float | None = None
    redevelopment_price_safety_score: float | None = None
    area_relative_price_ratio: float | None = None
    road_class: str = "UNKNOWN"
    property_fronts_major_road: bool | None = None
    road_distance_m: float | None = None
    road_frontage_method: str = "UNKNOWN"
    road_penalty: float = 0.0
    road_quality_direct_score: float | None = None
    road_quality_exposure_factor: float | None = None
    road_quality_effective_score: float | None = None
    road_quality_micro_score: float | None = None
    road_quality_micro_penalty: float | None = None
    micro_location_nonroad_score: float | None = None
    road_discount_status: str = "UNKNOWN"
    road_discount_method: str = ""
    road_discount_category: str = "UNKNOWN"
    road_discount_low_pct: float = 0.0
    road_discount_pct: float = 0.0
    road_discount_high_pct: float = 0.0
    road_discount_exposure_factor: float | None = None
    road_discount_basis_value: float | None = None
    road_discount_low_cad: float | None = None
    road_discount_cad: float | None = None
    road_discount_high_cad: float | None = None
    road_adjusted_standard_property_value: float | None = None
    functional_area_per_bedroom: float | None = None
    assessment_year: int | None = None
    assessment_total: float | None = None
    assessment_land: float | None = None
    assessment_improvement: float | None = None
    ask_to_assessment_ratio: float | None = None
    assessment_discount_pct: float | None = None
    ask_to_land_assessment_ratio: float | None = None
    land_assessment_discount_pct: float | None = None
    assessment_discount_score: float | None = None
    assessment_discount_status: str = "UNKNOWN"
    price_safety_score: float | None = None
    investment_score: float | None = None
    price_value_score: float | None = None
    # This is a diagnostic snapshot, not an arithmetic vector: it includes
    # numeric scores plus categorical evidence such as reno_band.
    score_components: dict[str, Any] = Field(default_factory=dict)
    holding_status: str = "HOLDING_UNKNOWN"
    flags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    reason: str = ""


class ScreeningResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_count: int = 0
    stage_a_candidates: list[ScreeningDecision] = Field(default_factory=list)
    stage_a_excluded: list[ScreeningDecision] = Field(default_factory=list)
    stage_b_candidates: list[ScreeningDecision] = Field(default_factory=list)
    final_review_candidates: list[ScreeningDecision] = Field(default_factory=list)
    special_track_candidates: list[ScreeningDecision] = Field(default_factory=list)
    live_home_candidates: list[ScreeningDecision] = Field(default_factory=list)
    hold_redevelop_candidates: list[ScreeningDecision] = Field(default_factory=list)
    development_only_candidates: list[ScreeningDecision] = Field(default_factory=list)
    need_classification_candidates: list[ScreeningDecision] = Field(default_factory=list)
    rejected_candidates: list[ScreeningDecision] = Field(default_factory=list)
    live_home_final_candidates: list[ScreeningDecision] = Field(default_factory=list)
    hold_redevelop_final_candidates: list[ScreeningDecision] = Field(default_factory=list)
    development_only_final_candidates: list[ScreeningDecision] = Field(default_factory=list)
    current_use_candidates: list[ScreeningDecision] = Field(default_factory=list)
    redevelopment_candidates: list[ScreeningDecision] = Field(default_factory=list)
    dual_value_candidates: list[ScreeningDecision] = Field(default_factory=list)
    current_use_top10: list[ScreeningDecision] = Field(default_factory=list)
    redevelopment_top10: list[ScreeningDecision] = Field(default_factory=list)
    dual_value_top10: list[ScreeningDecision] = Field(default_factory=list)
    investment_candidates: list[ScreeningDecision] = Field(default_factory=list)
    investment_top10: list[ScreeningDecision] = Field(default_factory=list)
    price_value_candidates: list[ScreeningDecision] = Field(default_factory=list)
    price_value_top10: list[ScreeningDecision] = Field(default_factory=list)
    verification_candidates: list[ScreeningDecision] = Field(default_factory=list)
    stage_a_excluded_count: int = 0
    stage_b_count: int = 0
    final_review_count: int = 0
    special_track_count: int = 0
    live_home_count: int = 0
    hold_redevelop_count: int = 0
    development_only_count: int = 0
    need_classification_count: int = 0
    rejected_count: int = 0


def _screening_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("screening_v2", {})
    return value if isinstance(value, Mapping) else {}


def _load_location_residual_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Load only the robust summary rows from a local V1.4 artifact."""

    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                str(row.get("row_type", "")).upper() != "SUMMARY"
                or str(row.get("analysis_variant", "")).upper() != "ROBUST_SUMMARY"
                or str(row.get("component", "")).lower() != "area_location_residual"
            ):
                continue
            segment = str(row.get("segment", "")).strip()
            value = _number_for_compare(row.get("value"))
            if not segment or value is None:
                continue
            standard = _number_for_compare(row.get("standard_property_value"))
            rows[_text(segment)] = {
                "segment": segment,
                "residual_cad": float(value),
                "standard_property_value": float(standard) if standard is not None else None,
                "confidence": str(row.get("confidence") or "UNKNOWN").upper(),
                "source": str(path),
            }
    return rows


def _prepare_screening_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the configured local location model once per screening run."""

    effective = dict(config)
    screening = dict(_screening_config(config))
    model = screening.get("location_residual_model", {})
    model = dict(model) if isinstance(model, Mapping) else {}
    if bool(model.get("enabled", False)) and not isinstance(model.get("residuals"), Mapping):
        source_path = model.get("source_path")
        if source_path:
            path = Path(str(source_path))
            if not path.is_absolute():
                path = Path(str(config.get("_project_root") or Path.cwd())) / path
            try:
                model["residuals"] = _load_location_residual_rows(path)
                model["resolved_source_path"] = str(path)
            except (OSError, UnicodeError, csv.Error):
                model["residuals"] = {}
                model["load_error"] = str(path)
        else:
            model["residuals"] = {}
    screening["location_residual_model"] = model
    effective["screening_v2"] = screening
    return effective


def _number(value: Any) -> float | None:
    return _number_for_compare(value)


def _text(value: Any) -> str:
    return unescape(str(value or "")).strip().lower()


def _has_identity(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and not _PLACEHOLDER_RE.fullmatch(text)


def _dom_days(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _DOM_RE.search(_text(value))
    if match is None:
        return None
    number = float(match.group("number"))
    unit = match.group("unit").lower()
    return {"hour": number / 24, "day": number, "week": number * 7, "month": number * 30, "year": number * 365}[unit]


def _description(card: DiscoveryCard) -> str:
    return _text(card.description)


def _card_value(card: DiscoveryCard, *names: str) -> Any:
    for name in names:
        value = getattr(card, name, None)
        if value is not None and _has_identity(value):
            return value
        raw = card.raw.get(name) if isinstance(card.raw, Mapping) else None
        if raw is not None and _has_identity(raw):
            return raw
        extra = card.model_extra.get(name) if card.model_extra else None
        if extra is not None and _has_identity(extra):
            return extra
    return None


def _dimensions(card: DiscoveryCard) -> tuple[float | None, float | None, bool]:
    value = card.lot_dimensions
    match = _DIMENSIONS_RE.search(str(value or "")) if value else None
    if match is None and (card.frontage_ft is not None or card.depth_ft is not None):
        width = _number(card.frontage_ft)
        depth = _number(card.depth_ft)
        if width is None:
            width = _number(_card_value(card, "official_frontage_ft"))
        if depth is None:
            depth = _number(_card_value(card, "official_depth_ft"))
    elif match is None and (
        _card_value(card, "official_frontage_ft") is not None
        or _card_value(card, "official_depth_ft") is not None
    ):
        width = _number(_card_value(card, "official_frontage_ft"))
        depth = _number(_card_value(card, "official_depth_ft"))
    elif match is not None:
        width = _number(match.group("width"))
        depth = _number(match.group("depth"))
    else:
        return None, None, False
    if width is None or depth is None or width <= 0 or depth <= 0:
        return None, None, True
    area = _number(card.lot_area_sqft)
    inconsistent = area is not None and not (area * 0.70 <= width * depth <= area * 1.30)
    return (None, None, True) if inconsistent else (width, depth, False)


def _exceptional_small_lot(card: DiscoveryCard, policy: Mapping[str, Any]) -> bool:
    asking = _number(card.asking)
    price_max = _number(policy.get("exceptional_small_lot_price_max", 1_250_000))
    if asking is not None and price_max is not None and asking <= price_max:
        return True
    text = f"{str(card.address or '')} {_description(card)}"
    markers = policy.get("exceptional_small_lot_markers", ["corner", "double frontage", "assembly", "special zoning"])
    return any(str(marker).lower() in text.lower() for marker in markers)


def _track_for(card: DiscoveryCard) -> tuple[str, list[str]]:
    """Apply transaction gates and retain explicit classification signals."""
    text = f"{_text(card.address)} {_description(card)}"
    partial = any(marker in text for marker in ("partial ownership", "fractional ownership", "undivided interest", "2/3 share", "1/3 share", "share of property"))
    court = any(marker in text for marker in ("court ordered", "court-ordered", "court order sale", "court sale", "foreclosure", "judicial sale"))
    # TOA / 3.0 FSR / 6-8 storey language is a future option or a listing
    # marketing claim. It is not, by itself, evidence that the property is
    # already a high-density development asset. Only explicit approved or
    # rezoned language may route a listing to the separate development track.
    development = any(marker in text for marker in (
        "approved high-density", "approved high density",
        "approved 6 storey", "approved 6-storey",
        "approved 8 storey", "approved 8-storey",
        "rezoned high-density", "rezoned high density",
        "rezoned to rm-", "approved multiplex development",
    ))
    income_layout = (
        ((_number(card.bedrooms) or 0) >= 8 and (_number(card.bathrooms) or 0) >= 4)
        or any(marker in text for marker in ("rooming house", "rooming-house", "boarding house", "five bedrooms below"))
        or any(marker in text for marker in (
            "multiple suites", "self-contained suites", "two kitchens in the main house",
            "2 kitchens in the main house", "multiple kitchens in the main house",
            "second kitchen in the main house", "multiple kitchens inside the main house",
        ))
    )
    if partial or court:
        flags = ["EXCLUDE_FROM_THREE_CLASS_MODEL", "COURT_ORDERED_SALE"] if court else ["EXCLUDE_FROM_THREE_CLASS_MODEL", "PARTIAL_OWNERSHIP"]
        if partial:
            flags.append("PARTIAL_OWNERSHIP")
        return "SPECIAL_TRANSACTION", list(dict.fromkeys(flags))
    if development:
        return "NEED_CLASSIFICATION", ["HIGH_DENSITY_DEVELOPMENT_SIGNAL"]
    if income_layout:
        return "NEED_CLASSIFICATION", ["NON_STANDARD_HOLDING_LAYOUT"]
    return "NEED_CLASSIFICATION", []


def _stage_a_decision(card: DiscoveryCard, config: Mapping[str, Any]) -> ScreeningDecision:
    screening = _screening_config(config)
    stage_a = screening.get("stage_a", {})
    stage_a = stage_a if isinstance(stage_a, Mapping) else {}
    reasons: list[str] = []
    hard_failures: list[str] = []
    track, track_flags = _track_for(card)
    allowed_municipalities = screening.get("allowed_municipalities", ["Vancouver"])
    if card.municipality is None:
        reasons.append("MUNICIPALITY_UNKNOWN")
    elif not any(_evaluate_filter("municipality", value, card.municipality) is True for value in allowed_municipalities):
        hard_failures.append("MUNICIPALITY_OUT_OF_SCOPE")
    checks = (("property_type", "DETACHED_HOUSE", card.property_type, "PROPERTY_TYPE"), ("ownership", "FREEHOLD", card.ownership_type, "OWNERSHIP"), ("status", "ACTIVE", card.listing_status, "LISTING_STATUS"))
    for field, expected, observed, label in checks:
        outcome = _evaluate_filter(field, expected, observed)
        if outcome is False:
            hard_failures.append(f"{label}_OUT_OF_SCOPE")
        elif outcome is None:
            hard_failures.append(f"{label}_UNKNOWN")
    asking = _number(card.asking)
    # Regional/native website filters own the price range. A global post-
    # acquisition price gate would incorrectly drop valid regional candidates
    # when a broad Vancouver preset has a different price ceiling.
    price_gate_enabled = bool(stage_a.get("price_gate_enabled", False))
    price_min = _number(stage_a.get("price_min", 1_000_000))
    price_max = _number(stage_a.get("price_max", 1_800_000))
    if asking is None:
        hard_failures.append("ASKING_UNKNOWN")
    elif price_gate_enabled and ((price_min is not None and asking < price_min) or (price_max is not None and asking > price_max)):
        hard_failures.append("ASKING_OUT_OF_RANGE")
    if not _has_identity(card.address):
        hard_failures.append("ADDRESS_UNKNOWN")
    if not _has_identity(card.mls_number):
        hard_failures.append("MLS_UNKNOWN")
    severe_markers = stage_a.get("severe_condition_markers", ["fire damaged", "fire-damaged", "collapsed", "condemned", "uninhabitable"])
    if any(str(marker).lower() in _description(card) for marker in severe_markers):
        hard_failures.append("SEVERE_CONDITION_SIGNAL")
    lot_area = _number(card.lot_area_sqft)
    hard_lot_min = _number(stage_a.get("hard_lot_min_sqft", 2_800))
    if lot_area is not None and hard_lot_min is not None and lot_area < hard_lot_min and not _exceptional_small_lot(card, stage_a):
        hard_failures.append("LOT_BELOW_HARD_MINIMUM")
    frontage = _number(card.frontage_ft) or _number(_card_value(card, "official_frontage_ft"))
    hard_frontage_min = _number(stage_a.get("hard_frontage_min_ft"))
    if frontage is not None and hard_frontage_min is not None and frontage < hard_frontage_min and not _exceptional_small_lot(card, stage_a):
        hard_failures.append("FRONTAGE_BELOW_HARD_MINIMUM")
    common = {
        "track": track,
        "asset_class": track,
        "classification_status": "GATED" if track == "SPECIAL_TRANSACTION" else "NOT_CLASSIFIED",
        "flags": list(dict.fromkeys(track_flags)),
    }
    if hard_failures:
        return ScreeningDecision(card=card, stage="A", status="REJECT", priority="REJECT", reason_codes=hard_failures + reasons, reason="; ".join(hard_failures + reasons), **common)
    return ScreeningDecision(card=card, stage="A", status="PASS", reason_codes=list(dict.fromkeys(reasons + track_flags)), reason="; ".join(reasons + track_flags) if reasons or track_flags else "Stage A basic identity and scope checks passed.", **common)


def _area_profile(card: DiscoveryCard, screening: Mapping[str, Any]) -> tuple[str, str, float]:
    neighbourhood = _text(card.neighbourhood)
    configured_strata = screening.get("area_strata", {})
    strata = configured_strata if isinstance(configured_strata, Mapping) and configured_strata else _DEFAULT_STRATA
    stratum = "UNKNOWN"
    exact_matches: list[str] = []
    for name, values in strata.items():
        values = values if isinstance(values, (list, tuple, set)) else []
        if any(_text(value) == neighbourhood for value in values):
            exact_matches.append(str(name).upper())
    if exact_matches:
        stratum = exact_matches[0]
    else:
        for name, values in strata.items():
            values = values if isinstance(values, (list, tuple, set)) else []
            if any(_text(value) in neighbourhood for value in values):
                stratum = str(name).upper()
                break
    profiles = screening.get("area_tiers", {})
    profiles = profiles if isinstance(profiles, Mapping) else {}
    configured = profiles.get(stratum, _DEFAULT_AREA_PROFILES.get(stratum, _DEFAULT_AREA_PROFILES["UNKNOWN"]))
    neighbourhood_profiles = screening.get("neighbourhood_tiers", {})
    neighbourhood_profiles = neighbourhood_profiles if isinstance(neighbourhood_profiles, Mapping) else {}
    neighbourhood_override = next((value for key, value in neighbourhood_profiles.items() if _text(key) == neighbourhood), None)
    if isinstance(neighbourhood_override, Mapping):
        configured = neighbourhood_override
    if isinstance(configured, Mapping):
        default = _DEFAULT_AREA_PROFILES.get(stratum, _DEFAULT_AREA_PROFILES["UNKNOWN"])
        tier = str(configured.get("tier", default["tier"]))
        score = float(configured.get("score", default["score"]))
    else:
        tier = str(configured)
        score = float(_DEFAULT_AREA_PROFILES.get(stratum, _DEFAULT_AREA_PROFILES["UNKNOWN"])["score"])
    return stratum, tier, max(0.0, min(20.0, score))


def _school_proxy(card: DiscoveryCard, screening: Mapping[str, Any]) -> tuple[float, str, list[str]]:
    policy = screening.get("school_proxy", {})
    policy = policy if isinstance(policy, Mapping) else {}
    total = 0.0
    flags: list[str] = []
    known = 0
    for name, maximum, label in (("elementary_school", 7.0, "ELEMENTARY"), ("secondary_school", 3.0, "SECONDARY")):
        if name.startswith("elementary"):
            aliases = ("official_elementary_catchment", "elementary_school", "elementary_catchment")
        else:
            aliases = ("official_secondary_catchment", "secondary_school", "secondary_catchment")
        school = _card_value(card, *aliases)
        official_score = _number(_card_value(
            card,
            "official_elementary_score" if name.startswith("elementary") else "official_secondary_score",
        ))
        mapping = policy.get("elementary" if name.startswith("elementary") else "secondary", {})
        mapping = mapping if isinstance(mapping, Mapping) else {}
        observed = next((value for key, value in mapping.items() if _text(key) == _text(school)), None) if school else None
        if official_score is not None:
            total += max(0.0, min(maximum, official_score))
            known += 1
            continue
        if not isinstance(observed, Mapping):
            # Missing school evidence is unknown, not a zero. Use the
            # dimension midpoint so incomplete enrichment does not silently
            # reduce the candidate's value score.
            total += maximum / 2.0
            flags.append("SCHOOL_PROXY_UNKNOWN")
            continue
        total += max(0.0, min(maximum, float(observed.get("score", maximum / 2))))
        known += 1
    return round(total if known else 5.0, 2), "KNOWN" if known else "UNKNOWN", list(dict.fromkeys(flags))


def _road_profile(card: DiscoveryCard, screening: Mapping[str, Any]) -> tuple[str, float, float, list[str]]:
    configured = screening.get("road_tokens", {})
    configured = configured if isinstance(configured, Mapping) else {}
    tokens = {key: tuple(str(x).lower() for x in configured.get(key, values)) for key, values in _DEFAULT_ROAD_TOKENS.items()}
    address_text = _text(card.address)
    description_text = _description(card)
    text = f"{address_text} {description_text}"
    road_class = "unknown"
    official_road = _card_value(card, "official_road_class", "road_class")
    official_distance = _number(_card_value(card, "official_distance_to_major_road_m", "distance_to_major_road_m"))
    if official_road is not None:
        official_key = _text(official_road)
        road_class = {
            "a": "extreme", "b": "major", "c": "arterial", "d": "collector", "e": "quiet",
            "extreme": "extreme", "major": "major", "arterial": "arterial", "collector": "collector", "quiet": "quiet",
        }.get(official_key, "unknown")
    else:
        for candidate in ("extreme", "major", "arterial", "collector"):
            if any(token in address_text for token in tokens[candidate]) or any(
                token in description_text for token in _DESCRIPTION_ROAD_TOKENS[candidate]
            ):
                road_class = candidate
                break
    penalty = _ROAD_PENALTIES[road_class]
    if official_road is not None and official_distance is not None:
        distance_factor = 1.0 if official_distance <= 50 else 0.75 if official_distance <= 100 else 0.5 if official_distance <= 200 else 0.25
        penalty = round(penalty * distance_factor, 2)
    positive = 0.0
    flags: list[str] = []
    if road_class not in {"quiet", "unknown"}:
        flags.append("MAJOR_ROAD_SIGNAL" if road_class in {"major", "extreme", "arterial"} else "ROAD_EXPOSURE_SIGNAL")
    if any(token in text for token in ("quiet residential", "quiet street", "peaceful", "family-friendly")):
        if road_class == "unknown":
            road_class = "quiet"
        positive += 3.0
        flags.append("QUIET_LOCATION_SIGNAL")
    if any(token in text for token in ("park-facing", "park facing", "steps to the park", "near the park")):
        positive += 2.0
        flags.append("PARK_LOCATION_SIGNAL")
    if any(token in text for token in ("walkable", "walkability", "shops, cafés", "shops and restaurants")):
        positive += 1.0
    strong_view_markers = (
        "panoramic view", "panoramic views", "panoramic north shore",
        "north shore mountain and city view", "north shore mountains and city view",
        "north shore mountain/city view", "ocean view", "waterfront view",
    )
    ordinary_view_markers = (
        "view lines", "water view", "mountain view", "city view", "city views",
    )
    if any(token in text for token in strong_view_markers):
        positive += 2.0
        flags.append("STRONG_OUTLOOK_SIGNAL")
    elif any(token in text for token in ordinary_view_markers):
        positive += 1.0
        flags.append("OUTLOOK_SIGNAL")
    nonroad_positive = 0.0
    if any(token in text for token in ("park-facing", "park facing", "steps to the park", "near the park")):
        nonroad_positive += 1.5
    if any(token in text for token in ("walkable", "walkability", "shops, cafés", "shops and restaurants")):
        nonroad_positive += 0.5
    if any(token in text for token in strong_view_markers):
        nonroad_positive += 1.5
    elif any(token in text for token in ordinary_view_markers):
        nonroad_positive += 0.75
    return road_class, min(9.0, 6.0 + nonroad_positive), penalty, flags


def _road_discount_for_card(
    card: DiscoveryCard,
    screening: Mapping[str, Any],
    road_class: str,
) -> dict[str, Any]:
    return calculate_road_discount(
        road_class=road_class,
        distance_m=_card_value(card, "official_distance_to_major_road_m", "distance_to_major_road_m"),
        property_fronts_major_road=_effective_property_frontage(card),
        road_name=card.address,
        description=_description(card),
        bus_route=_card_value(card, "official_bus_route", "bus_route"),
        truck_route=_card_value(card, "official_truck_route", "truck_route"),
        policy=screening.get("road_discount_prior"),
    )


def _road_reference_value(
    card: DiscoveryCard,
    screening: Mapping[str, Any],
    location: Mapping[str, Any],
) -> float | None:
    standard = _number(location.get("standard_property_value"))
    if standard is not None and standard > 0:
        return standard
    references = screening.get("neighbourhood_reference_prices", {})
    if not isinstance(references, Mapping):
        return None
    neighbourhood = _text(card.neighbourhood)
    value = next((value for key, value in references.items() if _text(key) == neighbourhood), None)
    return _number(value)


def _location_residual_profile(card: DiscoveryCard, screening: Mapping[str, Any]) -> dict[str, Any]:
    """Return the V1.4 combined location residual for a listing.

    The residual is a combined neighbourhood/location effect.  It therefore
    replaces the coarse area tier and school proxy when enabled; it must not
    be added on top of those two proxies.  A missing segment uses the neutral
    anchor and remains visible as UNKNOWN rather than reducing the score.
    """

    policy = screening.get("location_residual_model", {})
    policy = policy if isinstance(policy, Mapping) else {}
    enabled = bool(policy.get("enabled", False))
    anchor = float(policy.get("score_anchor", 50.0))
    if not enabled:
        return {
            "enabled": False,
            "score": None,
            "residual_cad": None,
            "standard_property_value": None,
            "segment": None,
            "confidence": "UNKNOWN",
            "source": None,
            "flags": [],
        }

    residuals = policy.get("residuals", {})
    residuals = residuals if isinstance(residuals, Mapping) else {}
    neighbourhood = _text(card.neighbourhood)
    aliases = policy.get("segment_aliases", {})
    aliases = aliases if isinstance(aliases, Mapping) else {}
    requested_segment = next(
        (value for key, value in aliases.items() if _text(key) == neighbourhood),
        neighbourhood,
    )
    record = None
    for key, value in residuals.items():
        if _text(key) == _text(requested_segment) and isinstance(value, Mapping):
            record = value
            break

    flags: list[str] = []
    neutral_value = _number(policy.get("neutral_standard_value"))
    if neutral_value is None or neutral_value <= 0:
        neutral_value = 2_064_000.0
    if record is None:
        flags.append("LOCATION_RESIDUAL_UNKNOWN")
        if model_error := policy.get("load_error"):
            flags.append("LOCATION_RESIDUAL_SOURCE_UNAVAILABLE")
        return {
            "enabled": True,
            "score": anchor,
            "residual_cad": None,
            "standard_property_value": neutral_value,
            "segment": None,
            "confidence": "UNKNOWN",
            "source": str(model_error) if model_error else policy.get("resolved_source_path"),
            "flags": flags,
        }

    residual = _number(record.get("residual_cad", record.get("value")))
    standard = _number(record.get("standard_property_value"))
    if standard is None and residual is not None:
        standard = neutral_value + residual
    if standard is None or standard <= 0:
        flags.append("LOCATION_RESIDUAL_UNKNOWN")
        return {
            "enabled": True,
            "score": anchor,
            "residual_cad": residual,
            "standard_property_value": None,
            "segment": record.get("segment", requested_segment),
            "confidence": str(record.get("confidence") or "UNKNOWN").upper(),
            "source": record.get("source") or policy.get("resolved_source_path"),
            "flags": flags,
        }

    # A 50-point neutral anchor scales with the value of the fixed standard
    # property.  This is a score bridge, not a second monetary valuation.
    score = anchor * standard / neutral_value
    score_min = float(policy.get("score_min", 0.0))
    score_max = float(policy.get("score_max", 100.0))
    score = max(score_min, min(score_max, score))
    confidence = str(record.get("confidence") or "UNKNOWN").upper()
    flags.append("LOCATION_RESIDUAL_APPLIED")
    if confidence in {"LOW", "UNKNOWN"}:
        flags.append("LOCATION_RESIDUAL_LOW_CONFIDENCE")
    return {
        "enabled": True,
        "score": round(score, 2),
        "residual_cad": residual,
        "standard_property_value": standard,
        "segment": record.get("segment", requested_segment),
        "confidence": confidence,
        "source": record.get("source") or policy.get("resolved_source_path") or policy.get("source_path"),
        "flags": flags,
    }


def _lot_component(card: DiscoveryCard) -> tuple[float, float, list[str]]:
    area = _number(card.lot_area_sqft)
    flags: list[str] = []
    if area is None:
        area_score = 5.0
        flags.append("LOT_AREA_UNKNOWN")
    elif area < 2_800:
        area_score = 2.0
        flags.extend(("SMALL_LOT_SIGNAL", "LOT_BELOW_HARD_MINIMUM_EXCEPTION"))
    elif area < 3_300:
        area_score = 4.0
        flags.append("SMALL_LOT_SIGNAL")
    elif area < 3_700:
        area_score = 6.0
        flags.append("SMALL_LOT_SIGNAL")
    elif area < 4_000:
        area_score = 8.0
        flags.append("BELOW_4000_SQFT_SIGNAL")
    else:
        area_score = 10.0
    width, depth, dimension_issue = _dimensions(card)
    if dimension_issue:
        flags.append("LOT_DIMENSIONS_CONFLICT")
        geometry_score = 3.5
    elif width is None or depth is None:
        flags.append("GEOMETRY_UNKNOWN")
        geometry_score = 3.5
    else:
        text = _description(card)
        if any(token in text for token in ("steep slope", "severe slope", "pie lot", "flag lot")):
            geometry_score = 2.0
            flags.append("LOT_GEOMETRY_RISK")
        elif "irregular" in text:
            geometry_score = 2.5
            flags.append("LOT_GEOMETRY_RISK")
        elif width < 33 and depth >= 115:
            geometry_score = 3.5
            flags.append("NARROW_DEEP_LOT")
        elif depth < 100:
            geometry_score = 3.0
            flags.append("SHALLOW_LOT")
        else:
            geometry_score = 5.0
    # Lot is a sufficiency gate in Stage B, not a land-size auction.  Scale
    # the area/geometry proxy into the single approved 10-point component.
    return round(min(10.0, (area_score + geometry_score) / 15.0 * 10.0), 2), round(geometry_score, 2), list(dict.fromkeys(flags))


def _redevelopment_geometry_scores(card: DiscoveryCard) -> tuple[float, float, list[str]]:
    """Return the 20-point area and 15-point geometry redevelopment inputs."""

    area = _number(card.lot_area_sqft)
    flags: list[str] = []
    if area is None:
        area_score = 10.0
        flags.append("LOT_AREA_UNKNOWN")
    elif area < 3_000:
        area_score = 4.0
        flags.append("SMALL_LOT_SIGNAL")
    elif area < 3_600:
        area_score = 8.0
        flags.append("SMALL_LOT_SIGNAL")
    elif area < 4_000:
        area_score = 11.0
        flags.append("BELOW_STANDARD_LOT_SIGNAL")
    elif area < 4_300:
        area_score = 14.0
    elif area < 5_000:
        area_score = 16.0
    elif area < 6_000:
        area_score = 18.0
    else:
        area_score = 20.0

    width, depth, dimension_issue = _dimensions(card)
    if dimension_issue or width is None or depth is None:
        frontage_score = 7.0
        flags.append("GEOMETRY_UNKNOWN" if not dimension_issue else "LOT_DIMENSIONS_CONFLICT")
    elif width >= 50 and depth >= 100:
        frontage_score = 15.0
    elif width >= 44 and depth >= 100:
        frontage_score = 13.5
    elif width >= 40 and depth >= 105:
        frontage_score = 12.0
    elif width >= 33 and depth >= 115:
        frontage_score = 10.5
    elif width >= 33 and depth >= 100:
        frontage_score = 9.0
    elif width >= 30 and depth >= 100:
        frontage_score = 7.0
    elif depth < 90:
        frontage_score = 3.0
        flags.append("SHALLOW_LOT")
    else:
        frontage_score = 5.0
    if width is not None and width < 33:
        flags.append("NARROW_FRONTAGE_SIGNAL")
    if depth is not None and depth < 100:
        flags.append("SHALLOW_DEPTH_SIGNAL")
    return round(area_score, 2), round(frontage_score, 2), list(dict.fromkeys(flags))


def _relative_price_component(
    card: DiscoveryCard,
    screening: Mapping[str, Any],
    *,
    location_standard_property_value: float | None = None,
    price_basis: float | None = None,
) -> tuple[float, float | None, list[str]]:
    asking = _number(card.asking)
    references = screening.get("neighbourhood_reference_prices", {})
    references = references if isinstance(references, Mapping) else {}
    neighbourhood = _text(card.neighbourhood)
    reference = location_standard_property_value
    if reference is None:
        reference = next((float(value) for key, value in references.items() if _text(key) == neighbourhood), None)
    basis = price_basis if price_basis is not None else asking
    if basis is None or reference is None or reference <= 0:
        return 5.0, None, ["AREA_PRICE_REFERENCE_UNKNOWN"]
    ratio = basis / reference
    if ratio > 1.05:
        score = 2.0
    elif ratio > 0.95:
        score = 5.0
    elif ratio > 0.85:
        score = 7.0
    elif ratio > 0.75:
        score = 8.5
    else:
        score = 10.0
    flags = ["LOCATION_STANDARD_REFERENCE_APPLIED"] if location_standard_property_value is not None else []
    return score, round(ratio, 4), flags


def _price_signal_from_ratio(ratio: float | None) -> float:
    if ratio is None or ratio <= 0:
        return 5.0
    if ratio > 1.05:
        return 2.0
    if ratio > 0.95:
        return 5.0
    if ratio > 0.85:
        return 7.0
    if ratio > 0.75:
        return 8.5
    return 10.0


def _reno_score_from_estimate(estimate: float) -> tuple[float, str]:
    """Map required current-use work to the approved six-band score."""

    value = max(0.0, float(estimate))
    if value == 0:
        return 100.0, "MOVE_IN_READY"
    if value <= 30_000:
        return 80.0, "LIGHT_RENO"
    if value <= 60_000:
        return 60.0, "MODERATE_RENO"
    if value <= 100_000:
        return 40.0, "SIGNIFICANT_RENO"
    if value <= 150_000:
        return 20.0, "HEAVY_RENO"
    return 0.0, "CURRENT_USE_PASS"


def _reno_year(text: str) -> int | None:
    """Find a year attached to renovation/update language, not the build year."""

    year_pattern = r"(?:19|20)\d{2}"
    renovation_pattern = (
        r"(?:renovat(?:ed|ion|ing)|updated|update|rebuilt|rebuild|"
        r"improvement|improved|remodel(?:ed|ing)?|to the studs)"
    )
    matches: list[int] = []
    for pattern in (
        rf"{renovation_pattern}[^0-9]{{0,35}}({year_pattern})",
        rf"({year_pattern})[^0-9]{{0,35}}{renovation_pattern}",
    ):
        matches.extend(int(value) for value in re.findall(pattern, text, re.IGNORECASE))
    valid = [year for year in matches if 1900 <= year <= 2100]
    return max(valid) if valid else None


def _reno_permit_evidence(card: DiscoveryCard) -> tuple[list[str], int | None, str]:
    """Extract permit text defensively from official enrichment namespaces."""

    values: list[Any] = []
    names = (
        "official_renovation_permits",
        "official_building_permits",
        "building_permits",
        "renovation_permits",
    )
    for name in names:
        value = _card_value(card, name)
        if value not in (None, "", [], {}):
            values.append(value)
    if not values and isinstance(card.raw, Mapping):
        for key, value in card.raw.items():
            if "permit" in str(key).lower() and value not in (None, "", [], {}):
                values.append(value)
    if not values:
        return [], None, "UNKNOWN"
    text = " ".join(str(value) for value in values).lower()
    return ["official_permit_record"], _reno_year(text), "OFFICIAL"


def _reno_profile(card: DiscoveryCard, screening: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Estimate necessary holding work from official/listing evidence, then age."""

    policy = screening.get("reno_estimation", {}) if isinstance(screening, Mapping) else {}
    policy = policy if isinstance(policy, Mapping) else {}
    text = _description(card)
    permit_evidence, permit_year, permit_confidence = _reno_permit_evidence(card)
    raw_estimate = _number(_card_value(card, "official_reno_estimate_cad", "reno_estimate", "renovation_estimate"))
    if raw_estimate is not None:
        score, band = _reno_score_from_estimate(raw_estimate)
        return {
            "estimate": raw_estimate,
            "score": score,
            "band": band,
            "reno_year": permit_year or _reno_year(text),
            "scope": ["official_estimate"],
            "evidence": permit_evidence + ["official_reno_estimate_cad"],
            "confidence": "OFFICIAL",
        }

    renovation_year = permit_year or _reno_year(text)
    current_year = int(policy.get("current_year", 2026))
    recent_years = int(policy.get("recent_years", 5))
    scope_costs = {
        "roof": 20_000.0,
        "hvac": 15_000.0,
        "electrical": 25_000.0,
        "plumbing": 20_000.0,
        "drainage": 20_000.0,
        "foundation": 30_000.0,
        "sewer": 20_000.0,
        "hot_water": 5_000.0,
        "fire_sprinkler": 5_000.0,
        "kitchen": 20_000.0,
        "bathroom": 15_000.0,
        "windows": 15_000.0,
        "flooring": 5_000.0,
        "paint": 5_000.0,
    }
    configured_scope_costs = policy.get("scope_costs", {})
    if isinstance(configured_scope_costs, Mapping):
        for key, value in configured_scope_costs.items():
            number = _number(value)
            if number is not None:
                scope_costs[str(key)] = number

    # The fallback is intentionally conservative.  Older homes with no
    # renovation evidence are not assumed to be move-in ready.
    age_fallback = {
        "pre_1960": 100_000.0,
        "1960_1979": 100_000.0,
        "1980_1999": 80_000.0,
        "2000_2015": 60_000.0,
        "post_2015": 30_000.0,
    }
    configured_age = policy.get("age_fallback", {})
    if isinstance(configured_age, Mapping):
        for key, value in configured_age.items():
            number = _number(value)
            if number is not None:
                age_fallback[str(key)] = number
    year = _number(card.year_built)
    if year is None:
        estimate = age_fallback["pre_1960"]
        age_key = "UNKNOWN_AGE"
    elif year < 1960:
        estimate, age_key = age_fallback["pre_1960"], "pre_1960"
    elif year < 1980:
        estimate, age_key = age_fallback["1960_1979"], "1960_1979"
    elif year < 2000:
        estimate, age_key = age_fallback["1980_1999"], "1980_1999"
    elif year <= 2015:
        estimate, age_key = age_fallback["2000_2015"], "2000_2015"
    else:
        estimate, age_key = age_fallback["post_2015"], "post_2015"

    evidence = list(permit_evidence)
    scope: list[str] = []
    if permit_evidence:
        evidence.append("official_permit_scope_present")
    marker_groups = {
        "roof": ("new roof", "newer roof", "roof replaced", "roof replacement", "roof 20"),
        "hvac": ("new furnace", "new hvac", "heat pump", "heating replaced", "furnace replaced"),
        "electrical": ("updated electrical", "rewired", "electrical upgraded", "200 amp electrical"),
        "plumbing": ("updated plumbing", "re-piped", "repiped", "piping updated", "plumbing upgraded"),
        "drainage": ("perimeter drain", "drain tile", "drainage upgraded", "drainage replaced"),
        "foundation": ("foundation repaired", "foundation upgrade", "foundation replaced"),
        "sewer": ("sewer line replaced", "new sewer", "sewer upgraded"),
        "hot_water": ("new hot water tank", "hot water tank replaced", "hot water replaced", "new hot water heater"),
        "fire_sprinkler": ("fire sprinkler", "sprinkler system"),
        "kitchen": ("new kitchen", "renovated kitchen", "updated kitchen", "kitchen renovation", "kitchen and bathroom"),
        "bathroom": ("new bathroom", "renovated bathroom", "renovated bathrooms", "updated bathroom", "updated bathrooms", "kitchen and bathroom"),
        "windows": ("new windows", "newer windows", "double glazed windows", "windows replaced"),
        "flooring": ("new flooring", "laminate flooring", "engineered flooring", "hardwood flooring"),
        "paint": ("freshly painted", "fresh paint", "new paint", "interior paint"),
    }
    for name, markers in marker_groups.items():
        if any(marker in text for marker in markers):
            scope.append(name)
            evidence.append(f"listing:{name}")
    recent_scope_match = re.search(
        r"recent(?:ly)?\s+updates?\s+(?:include|includes|included)\s+([^.;]+)",
        text,
    )
    if recent_scope_match:
        recent_scope = recent_scope_match.group(1)
        simple_scope_tokens = {
            "roof": ("roof",),
            "hvac": ("furnace", "hvac", "heat pump"),
            "electrical": ("electrical", "wiring"),
            "plumbing": ("plumbing", "pipes", "piping"),
            "drainage": ("drain", "drainage"),
            "foundation": ("foundation",),
            "sewer": ("sewer",),
            "hot_water": ("hot water", "water heater"),
            "fire_sprinkler": ("fire sprinkler", "sprinkler system"),
            "kitchen": ("kitchen",),
            "bathroom": ("bathroom", "bathrooms"),
            "windows": ("window", "windows"),
            "flooring": ("floor", "flooring"),
            "paint": ("paint", "painted"),
        }
        for name, tokens in simple_scope_tokens.items():
            if any(token in recent_scope for token in tokens):
                scope.append(name)
                evidence.append(f"listing:recent_update:{name}")

    full_markers = (
        "fully renovated", "completely renovated", "substantially rebuilt",
        "renovated to the studs", "major renovation", "extensive renovations",
    )
    substantial = any(marker in text for marker in full_markers)
    core_systems = {
        "roof", "hvac", "electrical", "plumbing", "drainage", "foundation",
        "sewer", "hot_water", "fire_sprinkler", "windows",
    }
    recent_update_language = bool(
        re.search(r"\brecent(?:ly)?\s+updates?\b", text)
    )
    recent_renovation_evidence = (
        renovation_year is not None
        and 0 <= current_year - renovation_year <= recent_years
    )
    recent_multi_system_updates = (
        len(set(scope).intersection(core_systems)) >= 3
        and (recent_update_language or recent_renovation_evidence)
        and not any(
            marker in text
            for marker in (
                "original condition",
                "needs updating",
                "needs renovation",
                "deferred maintenance",
            )
        )
    )
    if substantial and renovation_year is not None and current_year - renovation_year <= recent_years:
        estimate = 0.0
        scope = list(dict.fromkeys(scope + ["substantial_recent_renovation"]))
        evidence.append("recent_substantial_listing_renovation")
        confidence = "HIGH" if permit_evidence else "LISTING_STRONG"
    else:
        # Explicit scope is treated as work already completed and reduces the
        # age fallback.  The remaining estimate is still rounded into the
        # user's decision bands, rather than pretending to be a contractor bid.
        for name in scope:
            estimate -= scope_costs.get(name, 0.0)
        if substantial:
            estimate = min(estimate, 30_000.0)
        if any(marker in text for marker in ("original condition", "needs updating", "needs renovation", "renovation potential")):
            estimate = max(estimate, 100_000.0 if year is None or year < 1980 else 80_000.0)
            evidence.append("listing:deferred_maintenance_signal")
        if recent_multi_system_updates:
            # A recent package covering several core systems is meaningful
            # evidence against applying the full age fallback.  It is not
            # the same as a full interior renovation, so cap the remaining
            # work at the user's light-reno band instead of forcing zero.
            estimate = min(estimate, 30_000.0)
            evidence.append("recent_multi_system_updates")
        estimate = max(0.0, estimate)
        confidence = (
            "OFFICIAL"
            if permit_evidence
            else "LISTING_STRONG"
            if recent_multi_system_updates
            else "LISTING"
            if scope or renovation_year
            else "AGE_FALLBACK"
        )
    score, band = _reno_score_from_estimate(estimate)
    return {
        "estimate": round(estimate, 2),
        "score": score,
        "band": band,
        "reno_year": renovation_year,
        "scope": list(dict.fromkeys(scope)),
        "evidence": list(dict.fromkeys(evidence + ([f"age_fallback:{age_key}"] if confidence == "AGE_FALLBACK" else []))),
        "confidence": confidence if confidence != "UNKNOWN" else permit_confidence,
    }


def _physical_normalization_profile(
    card: DiscoveryCard,
    screening: Mapping[str, Any],
    *,
    location: Mapping[str, Any],
    maintenance: float,
    holding_status: str,
    reno_estimate: float | None = None,
    parcel_location_adjustment_cad: float | None = None,
) -> dict[str, Any]:
    """Split a regional standard property into physical land and house parts.

    This is deliberately a preliminary normalization model.  The regional
    standard value already contains the area's combined location residual, so
    physical adjustments are applied to the land and house portions only.
    BCA land share is retained as a prior/diagnostic and is shrunk toward the
    configured market land share instead of being treated as market truth.
    """

    policy = screening.get("physical_normalization", {})
    policy = policy if isinstance(policy, Mapping) else {}
    enabled = bool(policy.get("enabled", False)) and bool(location.get("enabled", False))
    standard = _number(location.get("standard_property_value"))
    asking = _number(card.asking)
    if not enabled or standard is None or standard <= 0:
        return {
            "enabled": False,
            "status": "NOT_APPLIED",
            "physical_adjusted_expected_value": None,
            "physical_price_dislocation_cad": None,
            "physical_price_dislocation_pct": None,
            "current_use_effective_basis": round(asking + (reno_estimate or 0.0), 2) if asking is not None else None,
            "market_house_value_proxy": None,
            "market_land_basis": None,
            "area_normalized_lot_basis": None,
            "area_normalized_lot_basis_per_sqft": None,
            "risk_adjusted_lot_basis": None,
            "risk_adjusted_lot_basis_per_sqft": None,
            "area_standard_lot_equivalent_basis": None,
            "risk_adjusted_standard_lot_equivalent_basis": None,
            "current_package_value_proxy": None,
            "current_package_value_proxy_source": "UNKNOWN",
            "current_package_value_ratio": None,
            "current_lot_basis_per_sqft": None,
            "current_lot_basis_ratio": None,
            "current_value_gap_cad": None,
            "regional_price_ratio": None,
            "regional_price_discount_pct": None,
            "bca_discount_pct": None,
            "total_location_value_cad": None,
            "parcel_location_adjustment_cad": None,
            "market_land_share_prior": None,
            "bca_land_share": None,
            "physical_lot_factor": None,
            "normalized_land_value": None,
            "normalized_existing_house_value": None,
            "normalized_lot_basis": None,
            "normalized_lot_basis_per_sqft": None,
            "bca_house_value_proxy": None,
            "bca_normalized_land_basis": None,
            "bca_normalized_land_basis_per_sqft": None,
            "current_use_all_in_basis": round(asking + (reno_estimate or 0.0), 2) if asking is not None else None,
            "normalized_reference_land_value": None,
            "normalized_reference_land_price_per_sqft": None,
            "current_price_signal": None,
            "current_price_ratio": None,
            "redevelopment_price_signal": None,
            "redevelopment_price_ratio": None,
            "flags": [],
        }

    reference_area = float(policy.get("reference_lot_area_sqft", 4_026.0))
    reference_frontage = float(policy.get("reference_frontage_ft", 33.0))
    reference_depth = float(policy.get("reference_depth_ft", 122.0))
    reference_house_sqft = float(policy.get("reference_house_sqft", 1_700.0))
    configured_land_share = float(policy.get("standard_market_land_share", 0.75))
    configured_land_share = max(0.50, min(0.90, configured_land_share))
    bca_total = _number(_card_value(card, "official_assessment_total"))
    bca_land = _number(_card_value(card, "official_assessment_land"))
    bca_improvement = _number(_card_value(card, "official_assessment_improvement"))
    bca_land_share = (
        round(bca_land / bca_total, 4)
        if bca_land is not None and bca_total is not None and bca_total > 0
        else None
    )
    bca_weight = float(policy.get("bca_land_share_weight", 0.25))
    bca_weight = max(0.0, min(0.50, bca_weight))
    market_land_share = configured_land_share
    if bca_land_share is not None:
        market_land_share = configured_land_share + bca_weight * (bca_land_share - configured_land_share)
    market_land_share = max(0.50, min(0.90, market_land_share))
    reference_land_value = standard * market_land_share
    reference_house_value = standard - reference_land_value

    area = _number(card.lot_area_sqft)
    width, depth, dimension_issue = _dimensions(card)
    flags: list[str] = []
    if area is None:
        area_factor = 1.0
        flags.append("PHYSICAL_LOT_AREA_UNKNOWN")
    else:
        # Land area is linear below the standard and capped above it here;
        # extra land is monetized later only when zoning/product evidence
        # demonstrates a real additional option.
        area_factor = max(0.65, min(1.0, area / reference_area))
    frontage_factor = 1.0
    if width is None or dimension_issue:
        flags.append("PHYSICAL_GEOMETRY_UNKNOWN")
    else:
        frontage_factor = max(0.85, min(1.05, 0.85 + 0.15 * width / reference_frontage))
        if width < reference_frontage:
            flags.append("PHYSICAL_FRONTAGE_BELOW_STANDARD")
    depth_factor = 1.0
    if depth is not None and not dimension_issue:
        depth_factor = max(0.95, min(1.05, 0.95 + 0.05 * depth / reference_depth))
        if depth < reference_depth:
            flags.append("PHYSICAL_DEPTH_BELOW_STANDARD")
    physical_lot_factor = round(max(0.0, min(1.10, area_factor * frontage_factor * depth_factor)), 4)

    sqft = _number(card.finished_sqft)
    house_factor = 1.0
    if sqft is not None and reference_house_sqft > 0:
        house_factor *= max(0.85, min(1.20, sqft / reference_house_sqft))
    else:
        flags.append("PHYSICAL_HOUSE_SIZE_UNKNOWN")
    year = _number(card.year_built)
    if year is not None:
        if year < 1940:
            house_factor *= 0.95
        elif year >= 1980:
            house_factor *= 1.04
    else:
        flags.append("PHYSICAL_HOUSE_AGE_UNKNOWN")
    condition_factor = {
        "HOLDING_GOOD": 1.10,
        "HOLDING_ACCEPTABLE": 1.03,
        "HOLDING_UNKNOWN": 0.97,
        "HOLDING_POOR": 0.82,
    }.get(holding_status, 0.97)
    house_factor *= condition_factor
    beds = _number(card.bedrooms)
    baths = _number(card.bathrooms)
    if beds is not None:
        if beds == 2:
            house_factor *= 0.96
        elif beds >= 4 and beds <= 5:
            house_factor *= 1.03
        elif beds >= 6:
            house_factor *= 0.96
    if baths is not None:
        if baths < 2:
            house_factor *= 0.92
        elif baths >= 3:
            house_factor *= 1.04
    if sqft is not None and beds is not None and beds > 0:
        area_per_bed = sqft / beds
        if area_per_bed < 350:
            house_factor *= 0.93
            flags.append("PHYSICAL_DENSE_LAYOUT")
        elif area_per_bed < 400:
            house_factor *= 0.97
            flags.append("PHYSICAL_TIGHT_LAYOUT")
    text = _description(card)
    if any(marker in text for marker in ("fully renovated", "completely renovated", "substantially renovated")):
        house_factor *= 1.08
    elif any(marker in text for marker in ("renovated", "updated kitchen", "updated bathrooms", "newer roof", "roof replaced")):
        house_factor *= 1.03
    if any(marker in text for marker in ("two kitchens in the main house", "2 kitchens in the main house", "multiple kitchens in the main house", "second kitchen in the main house")):
        house_factor *= 0.90
        flags.append("PHYSICAL_MULTIPLE_MAIN_KITCHENS")
    house_factor = max(0.70, min(1.30, house_factor))
    # The official BCA improvement value is the direct current-house proxy.
    # The old inferred market-house estimate remains only as a fallback for
    # records that have not yet been BCA-enriched.
    bca_house_value_proxy = round(bca_improvement, 2) if bca_improvement is not None else None
    normalized_house_value = bca_house_value_proxy if bca_house_value_proxy is not None else round(reference_house_value * house_factor, 2)
    normalized_land_value = round(reference_land_value * physical_lot_factor, 2)
    expected = round(normalized_land_value + normalized_house_value, 2)
    physical_dislocation = round(expected - asking, 2) if asking is not None else None
    physical_dislocation_pct = round(physical_dislocation / expected, 4) if physical_dislocation is not None and expected else None
    location_residual_raw = _number(location.get("residual_cad"))
    location_residual = location_residual_raw or 0.0
    parcel_adjustment = float(parcel_location_adjustment_cad or 0.0)
    total_location_value = location_residual + parcel_adjustment
    # This legacy-compatible field now follows the agreed BCA-improvement
    # basis whenever official BCA evidence exists.  Without BCA it falls back
    # to the older estimate but is explicitly marked through the new BCA field.
    market_land_basis = (
        round(asking - bca_house_value_proxy, 2)
        if asking is not None and bca_house_value_proxy is not None
        else None
    )
    normalized_lot_basis = round(asking - normalized_house_value - location_residual, 2) if asking is not None else None
    risk_adjusted_lot_basis = (
        round(asking - normalized_house_value - total_location_value, 2)
        if asking is not None
        else None
    )
    bca_normalized_land_basis = (
        round(asking - bca_house_value_proxy - location_residual, 2)
        if asking is not None and bca_house_value_proxy is not None
        else None
    )
    normalized_lot_basis_per_sqft = (
        round(normalized_lot_basis / area, 2)
        if normalized_lot_basis is not None and area is not None and area > 0
        else None
    )
    risk_adjusted_lot_basis_per_sqft = (
        round(risk_adjusted_lot_basis / area, 2)
        if risk_adjusted_lot_basis is not None and area is not None and area > 0
        else None
    )
    bca_normalized_land_basis_per_sqft = (
        round(bca_normalized_land_basis / area, 2)
        if bca_normalized_land_basis is not None and area is not None and area > 0
        else None
    )
    reference_land_psf = reference_land_value / reference_area if reference_area > 0 else None
    current_lot_basis_per_sqft = (
        round(market_land_basis / area, 2)
        if market_land_basis is not None and area is not None and area > 0
        else None
    )
    current_lot_basis_ratio = (
        current_lot_basis_per_sqft / reference_land_psf
        if current_lot_basis_per_sqft is not None and reference_land_psf
        else None
    )
    # Road exposure is a parcel-specific location adjustment and is scored
    # separately. The regional redevelopment price signal therefore uses the
    # area-normalized physical lot basis, not the same basis after road risk
    # has already been subtracted.
    redev_basis_psf = normalized_lot_basis_per_sqft or bca_normalized_land_basis_per_sqft
    redev_ratio = redev_basis_psf / reference_land_psf if redev_basis_psf is not None and reference_land_psf else None
    current_use_effective_basis = (
        round(asking + (reno_estimate or 0.0), 2)
        if asking is not None
        else None
    )
    if bca_total is not None and bca_total > 0:
        # BCA total is the current-use package anchor. It represents the
        # assessed house+land package in this location; the physical
        # decomposition remains the redevelopment/diagnostic ledger.
        current_package_value_proxy = round(bca_total, 2)
        current_package_value_proxy_source = "BCA_TOTAL"
    else:
        current_package_value_proxy = round(expected + parcel_adjustment, 2)
        current_package_value_proxy_source = "PHYSICAL_MODEL_ESTIMATE"
    current_ratio = (
        current_use_effective_basis / current_package_value_proxy
        if current_use_effective_basis is not None and current_package_value_proxy > 0
        else None
    )
    regional_ratio = (
        current_use_effective_basis / standard
        if current_use_effective_basis is not None and standard > 0
        else None
    )
    bca_discount = (
        1.0 - asking / bca_total
        if asking is not None and bca_total is not None and bca_total > 0
        else None
    )
    flags.append("PHYSICAL_NORMALIZATION_APPLIED")
    return {
        "enabled": True,
        "status": "APPLIED",
        "physical_adjusted_expected_value": expected,
        "physical_price_dislocation_cad": physical_dislocation,
        "physical_price_dislocation_pct": physical_dislocation_pct,
        "current_use_effective_basis": current_use_effective_basis,
        "market_house_value_proxy": bca_house_value_proxy,
        "market_land_basis": market_land_basis,
        "area_normalized_lot_basis": normalized_lot_basis,
        "area_normalized_lot_basis_per_sqft": normalized_lot_basis_per_sqft,
        "risk_adjusted_lot_basis": risk_adjusted_lot_basis,
        "risk_adjusted_lot_basis_per_sqft": risk_adjusted_lot_basis_per_sqft,
        "area_standard_lot_equivalent_basis": (
            round(normalized_lot_basis_per_sqft * reference_area, 2)
            if normalized_lot_basis_per_sqft is not None
            else None
        ),
        "risk_adjusted_standard_lot_equivalent_basis": (
            round(risk_adjusted_lot_basis_per_sqft * reference_area, 2)
            if risk_adjusted_lot_basis_per_sqft is not None
            else None
        ),
        "current_package_value_proxy": current_package_value_proxy,
        "current_package_value_proxy_source": current_package_value_proxy_source,
        "current_package_value_ratio": round(current_ratio, 4) if current_ratio is not None else None,
        "current_lot_basis_per_sqft": current_lot_basis_per_sqft,
        "current_lot_basis_ratio": round(current_lot_basis_ratio, 4) if current_lot_basis_ratio is not None else None,
        "current_value_gap_cad": (
            round(current_package_value_proxy - current_use_effective_basis, 2)
            if current_use_effective_basis is not None
            else None
        ),
        "regional_price_ratio": round(regional_ratio, 4) if regional_ratio is not None else None,
        "regional_price_discount_pct": round(1.0 - regional_ratio, 4) if regional_ratio is not None else None,
        "bca_discount_pct": round(bca_discount, 4) if bca_discount is not None else None,
        "total_location_value_cad": round(total_location_value, 2),
        "parcel_location_adjustment_cad": round(parcel_adjustment, 2),
        "market_land_share_prior": round(market_land_share, 4),
        "bca_land_share": bca_land_share,
        "physical_lot_factor": physical_lot_factor,
        "normalized_land_value": normalized_land_value,
        "normalized_existing_house_value": normalized_house_value,
        "normalized_lot_basis": normalized_lot_basis,
        "normalized_lot_basis_per_sqft": normalized_lot_basis_per_sqft,
        "bca_house_value_proxy": bca_house_value_proxy,
        "bca_normalized_land_basis": bca_normalized_land_basis,
        "bca_normalized_land_basis_per_sqft": bca_normalized_land_basis_per_sqft,
        "current_use_all_in_basis": round(asking + (reno_estimate or 0.0), 2) if asking is not None else None,
        "normalized_reference_land_value": round(reference_land_value, 2),
        "normalized_reference_land_price_per_sqft": round(reference_land_psf, 2) if reference_land_psf is not None else None,
        "current_price_signal": _price_signal_from_ratio(current_ratio) if current_ratio is not None else None,
        "current_price_ratio": round(current_ratio, 4) if current_ratio is not None else None,
        "redevelopment_price_signal": _price_signal_from_ratio(redev_ratio) if redev_ratio is not None else None,
        "redevelopment_price_ratio": round(redev_ratio, 4) if redev_ratio is not None else None,
        "flags": flags,
    }


def _assessment_discount_component(
    card: DiscoveryCard,
    screening: Mapping[str, Any] | None = None,
) -> tuple[
    float | None,
    str,
    int | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    float | None,
    list[str],
]:
    """Return the official-assessment price signal without calling it value.

    The total-assessment discount is the scored signal.  The land-assessment
    discount is retained separately because it is useful for review, but it is
    not added a second time.  Official bridge fields are preferred over any
    unverified listing-side assessment fields.
    """

    asking = _number(card.asking)
    official_total = _number(_card_value(card, "official_assessment_total"))
    official_land = _number(_card_value(card, "official_assessment_land"))
    official_improvement = _number(_card_value(card, "official_assessment_improvement"))
    # Only the official bridge namespace is scoreable here.  A listing-site
    # assessment claim may be retained for display, but cannot create a price
    # safety bonus before BCA evidence is present.
    total = official_total
    land = official_land
    improvement = official_improvement
    year_value = _card_value(card, "official_assessment_year")
    year = int(_number(year_value)) if _number(year_value) is not None else None
    flags: list[str] = []
    if asking is None or total is None or total <= 0:
        return None, "UNKNOWN", year, total, land, improvement, None, None, None, None, ["ASSESSMENT_UNKNOWN"]

    status = "KNOWN_OFFICIAL" if official_total is not None else "KNOWN_UNVERIFIED"
    if status != "KNOWN_OFFICIAL":
        flags.append("ASSESSMENT_NOT_OFFICIAL")
    ratio = round(asking / total, 4)
    discount = round(1.0 - ratio, 4)
    policy = screening.get("assessment_discount_signal", {}) if isinstance(screening, Mapping) else {}
    policy = policy if isinstance(policy, Mapping) else {}
    configured_bands = policy.get("bands")
    bands = (
        [(0.25, 100.0), (0.20, 90.0), (0.15, 80.0), (0.10, 70.0), (0.05, 55.0), (0.0, 40.0), (-0.05, 25.0), (-float("inf"), 10.0)]
        if not isinstance(configured_bands, list)
        else sorted(
            [
                (float(item.get("minimum_discount_pct")), float(item.get("score")))
                for item in configured_bands
                if isinstance(item, Mapping) and item.get("minimum_discount_pct") is not None and item.get("score") is not None
            ],
            reverse=True,
        )
    )
    score = next((candidate_score for minimum_discount, candidate_score in bands if discount >= minimum_discount), 50.0)
    if discount >= 0.20:
        flags.append("ASSESSMENT_DISCOUNT_20PCT_PLUS")
    elif discount >= 0.15:
        flags.append("ASSESSMENT_DISCOUNT_15PCT_PLUS")
    elif discount >= 0.10:
        flags.append("ASSESSMENT_DISCOUNT_10PCT_PLUS")
    elif discount > 0:
        flags.append("ASSESSMENT_DISCOUNT")
    if asking < total:
        flags.append("ASK_BELOW_ASSESSMENT")
    land_ratio = round(asking / land, 4) if land is not None and land > 0 else None
    land_discount = round(1.0 - land_ratio, 4) if land_ratio is not None else None
    if land_ratio is not None and asking <= land:
        flags.append("ASK_BELOW_LAND_ASSESSMENT")
    return score, status, year, total, land, improvement, ratio, discount, land_ratio, land_discount, flags


def _price_safety_score(
    screening: Mapping[str, Any],
    *,
    assessment_score: float | None,
    relative_price: float,
    fatigue: float,
) -> float:
    policy = screening.get("price_safety_weights", {})
    policy = policy if isinstance(policy, Mapping) else {}
    weights = {
        "assessment_discount": float(policy.get("assessment_discount", 0.50)),
        "area_relative_price": float(policy.get("area_relative_price", 0.35)),
        "seller_fatigue": float(policy.get("seller_fatigue", 0.15)),
    }
    terms = [
        (assessment_score, weights["assessment_discount"]),
        (relative_price / 10.0 * 100.0, weights["area_relative_price"]),
        (fatigue / 10.0 * 100.0, weights["seller_fatigue"]),
    ]
    known_terms = [(value, weight) for value, weight in terms if value is not None and weight > 0]
    denominator = sum(weight for _, weight in known_terms) or 1.0
    return round(sum(value * weight for value, weight in known_terms) / denominator, 2)


def _investment_score(
    screening: Mapping[str, Any],
    *,
    current_use: float,
    redevelopment: float,
    price_safety: float,
) -> float:
    policy = screening.get("investment_weights", {})
    policy = policy if isinstance(policy, Mapping) else {}
    weights = {
        "current_use": float(policy.get("current_use", 0.40)),
        "redevelopment": float(policy.get("redevelopment", 0.40)),
        "price_safety": float(policy.get("price_safety", 0.20)),
    }
    denominator = sum(max(0.0, value) for value in weights.values()) or 1.0
    return round(
        (
            current_use * max(0.0, weights["current_use"])
            + redevelopment * max(0.0, weights["redevelopment"])
            + price_safety * max(0.0, weights["price_safety"])
        )
        / denominator,
        2,
    )


def _fatigue_component(card: DiscoveryCard) -> float:
    days = _dom_days(card.dom)
    score = 0.0 if days is None else 10.0 if days > 300 else 7.0 if days > 180 else 4.0 if days > 90 else 2.0 if days > 45 else 0.0
    original = _number(_card_value(card, "original_asking"))
    asking = _number(card.asking)
    if original and asking and original > asking and (original - asking) / original >= 0.05:
        score += 1.0
    return min(10.0, score)


def _family_layout_component(
    card: DiscoveryCard,
    policy: Mapping[str, Any] | None = None,
) -> tuple[float, bool, float | None, list[str]]:
    policy = policy if isinstance(policy, Mapping) else {}
    neutral_ratio = float(policy.get("neutral_sqft_per_bedroom", 500.0))
    tight_ratio = float(policy.get("tight_sqft_per_bedroom", 450.0))
    low_ratio = float(policy.get("low_sqft_per_bedroom", 400.0))
    severe_ratio = float(policy.get("severe_sqft_per_bedroom", 350.0))
    five_plus_bed_penalty = float(policy.get("five_plus_bed_penalty", 0.0))
    beds = _number(card.bedrooms)
    baths = _number(card.bathrooms)
    sqft = _number(card.finished_sqft)
    # Four and five bedrooms are both ordinary family-home configurations.
    # Bedroom count alone is not an income-property signal; kitchen topology
    # and extreme bedroom density carry that signal instead.
    bed_points = 5.0 if beds is None else 1.0 if beds < 2 else 4.0 if beds < 3 else 8.0 if beds < 4 else 10.0 if beds <= 5 else 8.0
    area_per_bedroom = round(sqft / beds, 2) if sqft is not None and beds and beds > 0 else None
    density_penalty = 0.0
    flags: list[str] = []
    if beds is not None and beds >= 6:
        flags.append("HIGH_BEDROOM_COUNT_REVIEW")
        if area_per_bedroom is None:
            flags.append("AREA_PER_BEDROOM_UNKNOWN")
        elif area_per_bedroom < severe_ratio:
            density_penalty = -4.0
            flags.append("LOW_AREA_PER_BEDROOM")
        elif area_per_bedroom < low_ratio:
            density_penalty = -3.0
            flags.append("LOW_AREA_PER_BEDROOM")
        elif area_per_bedroom < tight_ratio:
            density_penalty = -2.0
            flags.append("LOW_AREA_PER_BEDROOM")
        elif area_per_bedroom < neutral_ratio:
            density_penalty = -1.0
            flags.append("TIGHT_AREA_PER_BEDROOM")
        density_penalty -= max(0.0, five_plus_bed_penalty)
    bed_points = max(0.0, bed_points + density_penalty)
    bath_points = 3.0 if baths is None else 0.0 if baths < 1 else 1.0 if baths < 2 else 5.0 if baths < 3 else 6.0
    size_points = 5.0 if sqft is None else 2.0 if sqft < 1_250 else 5.0 if sqft < 1_500 else 7.0 if sqft < 1_800 else 9.0
    kitchen_adjustment, kitchen_flags = _kitchen_layout_adjustment(card)
    flags.extend(kitchen_flags)
    return (
        round(max(0.0, min(25.0, bed_points + bath_points + size_points + kitchen_adjustment)), 2),
        all(value is not None for value in (beds, baths, sqft)),
        area_per_bedroom,
        list(dict.fromkeys(flags)),
    )


def _condition_components(card: DiscoveryCard) -> tuple[float, float, str, list[str], bool]:
    text = _description(card)
    year = _number(card.year_built)
    poor_markers = (
        "tear down", "teardown", "needs extensive", "uninhabitable",
        "as is where is", "as-is", "land value only",
    )
    core_system_markers = (
        "new roof", "newer roof", "roof replaced", "roof replacement",
        "new furnace", "new hvac", "heat pump", "updated electrical",
        "updated plumbing", "rewired", "re-piped", "perimeter drain",
        "drain tile", "foundation repaired", "sewer line replaced", "new sewer",
        "fully renovated", "completely renovated", "substantially renovated",
    )
    maintenance_markers = (
        "well-maintained", "well maintained", "solid home", "solid family home",
    )
    cosmetic_markers = (
        "renovated bathroom", "renovated bathrooms", "updated bathroom",
        "updated bathrooms", "updated kitchen", "updated suite", "recent updates",
        "newer windows", "double glazed windows", "laminate flooring", "new flooring",
        "freshly painted", "fresh paint", "new paint",
    )
    poor = any(marker in text for marker in poor_markers)
    core_count = sum(marker in text for marker in core_system_markers)
    if any(marker in text for marker in ("recent updates include", "recent update includes", "recently updated")):
        core_count += sum(
            marker in text
            for marker in ("roof", "furnace", "hvac", "electrical", "plumbing", "perimeter drain", "foundation", "sewer", "fire sprinkler")
        )
    maintenance_count = sum(marker in text for marker in maintenance_markers)
    cosmetic_count = sum(marker in text for marker in cosmetic_markers)
    needs_updates = any(
        marker in text
        for marker in (
            "needs updating", "needs renovation", "needs renovating", "original condition",
            "original family home", "renovation potential", "renovate or rebuild", "renovate/rebuild",
        )
    )
    condition_known = poor or core_count > 0 or maintenance_count > 0 or cosmetic_count > 0 or needs_updates
    if poor:
        return 4.0, 3.0, "HOLDING_POOR", ["HOLDING_CONDITION_RISK"], True
    if core_count >= 2 or (core_count >= 1 and maintenance_count >= 1):
        return 23.0, 18.0, "HOLDING_GOOD", [], True
    if core_count == 1 or maintenance_count >= 1:
        return 18.0, 15.0, "HOLDING_ACCEPTABLE", [], True
    if cosmetic_count > 0:
        return 15.0, 12.0, "HOLDING_ACCEPTABLE", ["COSMETIC_UPDATES_ONLY"], True
    if needs_updates:
        return 14.0, 12.0, "HOLDING_ACCEPTABLE", [], True
    if year is None:
        return 12.5, 10.0, "HOLDING_UNKNOWN", ["HOLDING_CONDITION_UNKNOWN"], False
    if year >= 2000:
        return 20.0, 16.0, "HOLDING_ACCEPTABLE", ["MAINTENANCE_DETAILS_UNKNOWN"], False
    if year >= 1980:
        return 17.0, 14.0, "HOLDING_ACCEPTABLE", ["MAINTENANCE_DETAILS_UNKNOWN"], False
    if year >= 1960:
        return 15.0, 12.0, "HOLDING_UNKNOWN", ["MAINTENANCE_DETAILS_UNKNOWN"], False
    return 12.0, 10.0, "HOLDING_UNKNOWN", ["MAINTENANCE_DETAILS_UNKNOWN"], False


def _holding_flexibility(card: DiscoveryCard) -> tuple[float, bool]:
    text = _description(card)
    markers = {
        "basement": 1.5,
        "separate entrance": 1.0,
        "garage": 1.0,
        "parking": 0.5,
        "suite": 1.0,
    }
    matched = [score for marker, score in markers.items() if marker in text]
    return (round(min(5.0, sum(matched)), 2), True) if matched else (2.5, False)


def _kitchen_layout_adjustment(card: DiscoveryCard) -> tuple[float, list[str]]:
    """Detect kitchen topology without treating a separate garden suite as a defect."""

    text = _description(card)
    main_kitchens = _number(_card_value(card, "main_house_kitchen_count", "main_house_kitchens"))
    internal_multiple = (
        main_kitchens is not None and main_kitchens > 1
    ) or any(
        marker in text
        for marker in (
            "two kitchens in the main house",
            "2 kitchens in the main house",
            "multiple kitchens in the main house",
            "second kitchen in the main house",
            "multiple kitchens inside the main house",
        )
    )
    if internal_multiple:
        return -6.0, ["MULTIPLE_MAIN_KITCHENS", "INCOME_PROPERTY_LAYOUT_RISK"]
    if any(marker in text for marker in ("garden suite", "laneway suite", "coach house", "detached suite")):
        return 0.0, ["GARDEN_SUITE_COMPATIBLE"]
    return 0.0, []


def _redevelopment_signals(card: DiscoveryCard, inherited_flags: Iterable[str]) -> dict[str, bool]:
    text = _description(card)
    flags = set(inherited_flags)
    official_zoning = _card_value(card, "official_current_zoning", "official_zoning", "zoning")
    official_fsr = _number(_card_value(card, "official_maximum_fsr", "maximum_fsr"))
    official_units = _number(_card_value(card, "official_maximum_units", "maximum_units"))
    official_toa = _card_value(card, "official_toa", "toa", "official_toa_status", "toa_status")
    official_uses = _card_value(card, "official_permitted_uses", "permitted_uses")
    official_height = _card_value(card, "official_maximum_height", "maximum_height")
    official_text = " ".join(
        _text(value)
        for value in (official_zoning, official_toa, official_uses, official_height)
        if value not in (None, "")
    )
    official_zoning_known = any(
        value not in (None, "")
        for value in (official_zoning, official_fsr, official_units, official_toa, official_uses, official_height)
    )
    blocked = any(
        marker in text
        for marker in (
            "redevelopment legally blocked", "prevents redevelopment",
            "cannot be redeveloped", "no redevelopment", "no subdivision",
        )
    )
    toa_option = (
        official_toa not in (None, "")
        or any(
            marker in text
            for marker in (
                "transit-oriented area", "transit oriented area", "toa tier",
                "transit-oriented development", "transit oriented development", "tod tier",
                " 3.0 fsr", " 3.0 far", "6 storeys", "6 stories",
                "8 storeys", "8 stories", "toa",
            )
        )
    )
    # TOA density is a future rezoning/eligibility option, not an existing
    # high-density entitlement. Do not promote a listing merely because it
    # advertises Tier 3, 3.0 FSR, or 6–8 storeys. An official high FSR/height
    # value is treated as current only when it is not paired with a TOA
    # option; otherwise it remains an option signal.
    high_density = (
        "HIGH_DENSITY_DEVELOPMENT_SIGNAL" in flags
        or (
            not toa_option
            and (
                (official_fsr is not None and official_fsr >= 2.5)
                or (
                    official_height is not None
                    and bool(re.search(r"(?:6|7|8|9|10)\s*(?:storeys|stories)", _text(official_height)))
                )
            )
        )
        or any(
            marker in text
            for marker in (
                "high-density development", "high density development",
                "approved high-density", "approved high density",
                "approved 6 storey", "approved 6-storey",
                "approved 8 storey", "approved 8-storey",
            )
        )
    )
    zoning_key = _text(official_zoning).casefold()
    rt_zone = zoning_key.startswith("rt-") or zoning_key.startswith("rt ")
    multiplex = (
        (official_units is not None and official_units >= 3)
        or any(marker in text for marker in ("multiplex", "triplex", "fourplex", "fiveplex", "sixplex", "r1-1", "rm-"))
        or any(marker in official_text for marker in ("multiplex", "triplex", "fourplex", "fiveplex", "sixplex", "r1-1", "rm-"))
    )
    duplex = (
        "duplex" in text
        or "duplex" in official_text
        or "two-family dwelling" in official_text
        or "two family dwelling" in official_text
        or rt_zone
    )
    assembly = "assembly" in text or "assembly" in official_text
    return {
        "blocked": blocked,
        "high_density": high_density,
        "toa_option": toa_option,
        "multiplex": multiplex,
        "duplex": duplex,
        "assembly": assembly,
        "zoning_known": official_zoning_known or blocked or high_density or multiplex or duplex or assembly or "zoning" in text,
        "official_zoning_known": official_zoning_known,
    }


def _redevelopment_components(
    card: DiscoveryCard,
    lot_score: float,
    road_class: str,
    inherited_flags: Iterable[str],
    road_discount: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, bool], list[str]]:
    area = _number(card.lot_area_sqft)
    width, depth, dimension_issue = _dimensions(card)
    lot_area_geometry, frontage_depth, geometry_flags = _redevelopment_geometry_scores(card)
    signals = _redevelopment_signals(card, inherited_flags)

    if signals["blocked"]:
        entitlement = 0.0
        product_fit = 0.0
    elif signals["high_density"]:
        entitlement = 25.0
        product_fit = 15.0
    elif signals["multiplex"]:
        entitlement = 20.0
        product_fit = 13.0 if area is not None and area >= 3_700 else 10.0
    elif signals["duplex"]:
        entitlement = 16.0
        product_fit = 13.0 if area is not None and area >= 3_700 else 9.0
    else:
        # Unknown zoning is held at a neutral midpoint. Known ordinary zoning
        # remains a smaller but positive standard-lot signal.
        entitlement = 12.5 if not signals["zoning_known"] else 8.0
        product_fit = 13.0 if area is not None and area >= 4_000 and (width or 0) >= 33 and (depth or 0) >= 100 else 7.5

    text = _description(card)
    access_known = any(marker in text for marker in ("lane", "rear street", "double frontage", "garage", "parking"))
    access = 5.0 if any(marker in text for marker in ("lane", "rear street", "double frontage")) else 3.0 if any(marker in text for marker in ("garage", "parking")) else 2.5
    # A road defect survives demolition and therefore has to affect the
    # redevelopment axis as well as current-use.  Use the approved monetary
    # road-discount prior to convert future-exit exposure into the existing
    # 10-point permanent-risk component.  A 10% exposed-site discount maps
    # to zero points; smaller discounts retain proportional value.  A road
    # classified in the background (outside the exposure distance) does not
    # receive a redevelopment penalty.
    road_discount = road_discount if isinstance(road_discount, Mapping) else {}
    road_discount_pct = max(0.0, float(road_discount.get("base_pct") or 0.0))
    road_discount_status = str(road_discount.get("status") or "").upper()
    if road_discount_status == "BACKGROUND":
        permanent = 10.0
    elif road_discount_status == "APPLIED" and road_discount_pct > 0.0:
        permanent = max(0.0, min(10.0, 10.0 * (1.0 - road_discount_pct / 0.10)))
    else:
        permanent = {"quiet": 10.0, "collector": 7.0, "unknown": 5.0, "arterial": 4.0, "major": 2.0, "extreme": 0.0}.get(road_class, 5.0)
    if any(marker in text for marker in ("steep slope", "severe slope", "irregular lot", "industrial adjacency")):
        permanent = max(0.0, permanent - 3.0)

    option = 10.0 if signals["high_density"] else 8.0 if signals["assembly"] else 6.0 if signals["toa_option"] else 7.0 if width is not None and width >= 44 else 5.0 if width is not None and width >= 40 else 3.0 if width is not None and width >= 33 else 0.0 if signals["blocked"] else 5.0 if width is None or depth is None else 1.5
    certainty = sum(
        (
            1.0 if area is not None else 0.0,
            1.0 if width is not None and depth is not None and not dimension_issue else 0.0,
            1.0 if signals["zoning_known"] else 0.0,
            1.0 if road_class != "unknown" else 0.0,
            1.0 if access_known else 0.0,
        )
    )
    components = {
        "lot_area_geometry": lot_area_geometry,
        "frontage_depth_threshold": round(frontage_depth, 2),
        "zoning_legal_path": round(entitlement, 2),
        "product_market_fit": round(product_fit, 2),
        "lane_access_servicing": round(access, 2),
        "permanent_defects": round(permanent, 2),
        "data_certainty": round(certainty, 2),
        "toa_threshold_assembly": round(option, 2),
        "road_exit_discount_pct": round(road_discount_pct, 4),
        "road_exit_value_factor": round(max(0.0, 1.0 - road_discount_pct), 4),
    }
    flags: list[str] = []
    if signals["blocked"]:
        flags.append("REDEVELOPMENT_BLOCK_SIGNAL")
    if signals["toa_option"] and not signals["high_density"]:
        flags.append("TOA_OPTION_SIGNAL")
    if not signals["zoning_known"]:
        flags.append("ZONING_PATH_UNKNOWN")
    if not access_known:
        flags.append("REAR_ACCESS_UNKNOWN")
    flags.extend(geometry_flags)
    return components, signals, flags


def _classification_confidence(
    card: DiscoveryCard,
    *,
    layout_known: bool,
    condition_known: bool,
    school_known: bool,
    road_class: str,
    signals: Mapping[str, bool],
) -> float:
    width, depth, dimension_issue = _dimensions(card)
    text = _description(card)
    access_known = any(marker in text for marker in ("lane", "rear street", "double frontage", "garage", "parking"))
    score = 0.0
    score += 15.0 if layout_known else 0.0
    score += 5.0 if _number(card.year_built) is not None else 0.0
    score += 10.0 if condition_known else 0.0
    score += 15.0 if _number(card.lot_area_sqft) is not None else 0.0
    score += 15.0 if width is not None and depth is not None and not dimension_issue else 0.0
    score += 20.0 if signals["zoning_known"] else 0.0
    score += 10.0 if road_class != "unknown" else 0.0
    score += 5.0 if school_known else 0.0
    score += 5.0 if access_known else 0.0
    return round(score, 2)


def _scale(value: float, maximum: float, weight: float) -> float:
    return round(max(0.0, min(maximum, value)) / maximum * weight, 2) if maximum else 0.0


def _ratio_value_score(ratio: float | None) -> float:
    """Convert a cost/value ratio into a bounded 0-100 value score."""

    if ratio is None or ratio <= 0:
        return 50.0
    return round(_price_signal_from_ratio(ratio) * 10.0, 2)


def _rescale_score_components(
    components: Mapping[str, float],
    target: float,
) -> dict[str, float]:
    """Keep authoritative component totals equal to a post-score cap."""

    values = {key: float(value) for key, value in components.items()}
    total = sum(values.values())
    target = round(max(0.0, float(target)), 2)
    if not values or total <= 0.0:
        return {key: 0.0 for key in values}
    if target >= round(total, 2):
        return {key: round(value, 2) for key, value in values.items()}
    factor = target / total
    scaled = {key: round(value * factor, 2) for key, value in values.items()}
    drift = round(target - sum(scaled.values()), 2)
    last_key = next(reversed(scaled))
    scaled[last_key] = round(scaled[last_key] + drift, 2)
    return scaled


def _parcel_location_value_score(
    *,
    road_discount_pct: float | None,
    road_discount_status: str,
) -> float:
    """Score parcel-specific location value without re-adding area residual.

    The neighbourhood residual is already represented by the regional
    standard-property value.  This component is reserved for address-level
    permanent effects such as an exposed arterial or truck route.
    """

    if str(road_discount_status or "").upper() in {"", "UNKNOWN"}:
        return 50.0
    discount = max(0.0, float(road_discount_pct or 0.0))
    return round(max(0.0, min(100.0, 100.0 * (1.0 - discount / 0.15))), 2)


def _current_location_quality_score(
    *,
    residual_score: float | None,
    road_discount_pct: float | None,
    road_discount_status: str,
) -> float:
    """Combine area quality and parcel road exposure for current living.

    The regional residual represents neighbourhood/location quality, while
    the parcel road score represents the address-specific permanent effect.
    Neither is a price bargain signal, so this component cannot be inflated by
    a low asking price.
    """

    area_score = 50.0 if residual_score is None else max(0.0, min(100.0, float(residual_score)))
    road_score = _parcel_location_value_score(
        road_discount_pct=road_discount_pct,
        road_discount_status=road_discount_status,
    )
    return round(area_score * 0.70 + road_score * 0.30, 2)


def _current_use_reno_cap(
    estimate: float | None,
    policy: Mapping[str, Any] | None,
) -> float | None:
    """Return the maximum Current Use score allowed by holding work.

    Caps sit one band step above the reno band value so the score keeps the
    package/location ordering information instead of pinning every home in a
    reno band to the same number.  A house needing significant work can still
    rank on its real value, but cannot outscore genuinely move-in-ready homes.
    """

    if estimate is None:
        return None
    configured = policy.get("current_use_caps", []) if isinstance(policy, Mapping) else []
    caps = configured if isinstance(configured, (list, tuple)) else []
    if not caps:
        caps = (
            {"max_cost": 0, "max_score": 100},
            {"max_cost": 30_000, "max_score": 100},
            {"max_cost": 60_000, "max_score": 80},
            {"max_cost": 100_000, "max_score": 60},
            {"max_cost": 150_000, "max_score": 40},
            {"max_cost": float("inf"), "max_score": 0},
        )
    value = max(0.0, float(estimate))
    for item in caps:
        if not isinstance(item, Mapping):
            continue
        maximum = _number(item.get("max_cost"))
        if maximum is None:
            maximum = float("inf")
        if value <= maximum:
            return round(max(0.0, min(100.0, float(item.get("max_score", 50.0)))), 2)
    return 0.0


def _road_frontage_current_use_cap(category: str, policy: Any) -> float:
    """Return the Current Use ceiling for a home fronting a major road.

    Bus/truck corridors and other severe exposure categories cap lower than
    ordinary arterials.  road_quality_model.frontage_current_use_caps may
    override per category plus the HEAVY_ARTERIAL and default tiers.
    """

    configured = policy.get("frontage_current_use_caps", {}) if isinstance(policy, Mapping) else {}
    configured = configured if isinstance(configured, Mapping) else {}
    cat = category.upper()
    value = _number(configured.get(cat))
    if value is not None:
        return round(max(0.0, min(100.0, value)), 2)
    heavy = (
        "BUS_TRUCK" in cat
        or "TRUCK" in cat
        or "HIGHWAY" in cat
        or "RAIL" in cat
        or "HIGH_FLOW" in cat
        or "IMPACTFUL" in cat
    )
    if heavy:
        return round(max(0.0, min(100.0, float(configured.get("HEAVY_ARTERIAL", 35.0)))), 2)
    return round(max(0.0, min(100.0, float(configured.get("default", 45.0)))), 2)


def _redevelopment_capacity_value_score(redevelopment: Mapping[str, Any]) -> float:
    """Score the legal/physical product path on a common 0-100 scale."""

    parts = (
        (_number(redevelopment.get("zoning_legal_path")), 25.0, 0.30),
        (_number(redevelopment.get("lot_area_geometry")), 20.0, 0.20),
        (_number(redevelopment.get("frontage_depth_threshold")), 15.0, 0.20),
        (_number(redevelopment.get("product_market_fit")), 15.0, 0.15),
        (_number(redevelopment.get("lane_access_servicing")), 5.0, 0.10),
        (_number(redevelopment.get("toa_threshold_assembly")), 10.0, 0.05),
    )
    score = 0.0
    for value, maximum, weight in parts:
        score += (value / maximum if value is not None and maximum else 0.5) * weight * 100.0
    return round(max(0.0, min(100.0, score)), 2)


def _shared_value_for_money_components(
    *,
    scoring: Mapping[str, Any],
    ledger: Mapping[str, Any],
    assessment_score: float | None,
    house_condition_score: float | None,
    location_quality_score: float | None,
    road_discount_pct: float | None,
    road_discount_status: str,
    redevelopment: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Build Current Use and Redevelopment views from one value ledger."""

    configured = scoring.get("value_for_money_components", {})
    configured = configured if isinstance(configured, Mapping) else {}
    current_weights = configured.get("current_use", {})
    current_weights = current_weights if isinstance(current_weights, Mapping) else {}
    redevelopment_weights = configured.get("redevelopment", {})
    redevelopment_weights = redevelopment_weights if isinstance(redevelopment_weights, Mapping) else {}

    house_condition_score = float(house_condition_score) if house_condition_score is not None else 50.0
    parcel_location_score = _parcel_location_value_score(
        road_discount_pct=road_discount_pct,
        road_discount_status=road_discount_status,
    )
    current_location_score = (
        float(location_quality_score)
        if location_quality_score is not None
        else parcel_location_score
    )
    current_lot_score = _ratio_value_score(_number(ledger.get("current_lot_basis_ratio")))
    redevelopment_price_score = _ratio_value_score(_number(ledger.get("area_lot_basis_ratio")))
    redevelopment_geometry_score = _number(ledger.get("redevelopment_geometry_adequacy_score")) or 50.0
    # Redevelopment lot value is price efficiency plus physical adequacy.  A
    # small lot cannot receive a maximum lot score merely because its price
    # per square foot is low.
    redevelopment_lot_score = round(
        redevelopment_price_score * 0.30 + redevelopment_geometry_score * 0.70,
        2,
    )
    current_package_score = _ratio_value_score(_number(ledger.get("current_package_value_ratio")))
    regional_current_score = _ratio_value_score(_number(ledger.get("regional_price_ratio")))
    # The area-normalized lot ratio is already the redevelopment price signal
    # above. Keep this second regional slot neutral unless a future independent
    # product-value model is added; otherwise one ratio is counted twice.
    regional_redevelopment_score = 50.0
    bca_score = float(assessment_score) if assessment_score is not None else 50.0
    capacity_score = _redevelopment_capacity_value_score(redevelopment)
    # Road exposure is already represented by parcel-specific location value;
    # do not subtract it again as permanent risk. A non-road defect can still
    # supply a real permanent-defect score when present.
    permanent_score = (
        50.0
        if str(road_discount_status).upper() in {"APPLIED", "BACKGROUND"}
        else _scale(float(redevelopment.get("permanent_defects", 5.0)), 10.0, 100.0)
    )

    # Current Use is first a livability decision: the house must be usable
    # before the all-in package price can add value.  Condition therefore
    # carries the larger share of this component; the package ratio remains
    # a secondary signal so a cheap but unlivable house cannot win this view.
    current_house_value_score = round(
        house_condition_score * 0.60 + current_package_score * 0.40,
        2,
    )
    current_raw = {
        "house_value_for_money": current_house_value_score,
        "location_value_for_money": current_location_score,
        "lot_value_for_money": current_lot_score,
        "regional_price_safety": regional_current_score,
        "bca_price_safety": bca_score,
        "permanent_risk": permanent_score,
        # Location residual is already embedded in regional_price_ratio and
        # must remain a zero-weight diagnostic in this unified ledger.
        "location_residual": 0.0,
        "development_capacity_value_for_money": capacity_score,
    }
    redevelopment_raw = {
        # House condition is retained in the common ledger but deliberately
        # has only a small redevelopment weight.
        "house_value_for_money": 50.0,
        "location_value_for_money": parcel_location_score,
        "lot_value_for_money": redevelopment_lot_score,
        "regional_price_safety": regional_redevelopment_score,
        "bca_price_safety": bca_score,
        "permanent_risk": permanent_score,
        "location_residual": 0.0,
        "development_capacity_value_for_money": capacity_score,
    }
    current_components = {
        key: round(value / 100.0 * float(current_weights.get(key, 0.0)), 2)
        for key, value in current_raw.items()
    }
    redevelopment_components = {
        key: round(value / 100.0 * float(redevelopment_weights.get(key, 0.0)), 2)
        for key, value in redevelopment_raw.items()
    }
    return current_components, redevelopment_components


def _class_weights(screening: Mapping[str, Any], asset_class: str) -> dict[str, float]:
    configured = screening.get("class_scoring", {})
    configured = configured if isinstance(configured, Mapping) else {}
    configured_class = configured.get(asset_class, {})
    configured_class = configured_class if isinstance(configured_class, Mapping) else {}
    return {
        key: float(configured_class.get(key, default))
        for key, default in _DEFAULT_CLASS_WEIGHTS[asset_class].items()
    }


def _score_for_class(
    asset_class: str,
    screening: Mapping[str, Any],
    *,
    area_score: float,
    micro_quality: float,
    family_layout: float,
    maintenance: float,
    school_score: float,
    lot_score: float,
    relative_price: float,
    fatigue: float,
    redevelopment: Mapping[str, float],
) -> tuple[float, dict[str, float]]:
    weights = _class_weights(screening, asset_class)
    holding_fit = _scale(family_layout, 25.0, 15.0) + _scale(maintenance, 25.0, 10.0)
    raw_values: dict[str, tuple[float, float]] = {
        "area_neighbourhood_value": (area_score, 20.0),
        "micro_location": (micro_quality, 15.0),
        "family_layout": (family_layout, 25.0),
        "maintenance_systems": (maintenance, 25.0),
        "holding_house_fit": (holding_fit, 25.0),
        "school_proxy": (school_score, 10.0),
        "lot_sufficiency": (lot_score, 10.0),
        "redevelopment_path": (redevelopment["zoning_legal_path"], 25.0),
        "area_relative_price_signal": (relative_price, 10.0),
        "seller_fatigue": (fatigue, 10.0),
        "lot_geometry": (redevelopment["lot_area_geometry"], 20.0),
        "current_entitlement": (redevelopment["zoning_legal_path"], 25.0),
        "toa_threshold_assembly": (redevelopment["toa_threshold_assembly"], 10.0),
        "product_market_fit": (redevelopment["product_market_fit"], 15.0),
        "area_adjusted_land_price_proxy": (relative_price, 10.0),
        "permanent_execution_risk": (redevelopment["permanent_defects"], 10.0),
    }
    components = {
        key: _scale(raw_values[key][0], raw_values[key][1], weight)
        for key, weight in weights.items()
    }
    return round(sum(components.values()), 2), components


def _classify_asset(
    *,
    current_use: float,
    redevelopment: float,
    confidence: float,
    signals: Mapping[str, bool],
    policy: Mapping[str, Any] | None = None,
) -> str:
    policy = policy if isinstance(policy, Mapping) else {}
    live_min = float(policy.get("live_current_min", 70.0))
    hold_current_min = float(policy.get("hold_current_min", 60.0))
    hold_redevelopment_min = float(policy.get("hold_redevelopment_min", 60.0))
    development_current_max = float(policy.get("development_current_max", 60.0))
    development_redevelopment_min = float(policy.get("development_redevelopment_min", 65.0))
    reject_current_max = float(policy.get("reject_current_max", 50.0))
    low_confidence = float(policy.get("low_confidence", 60.0))
    if signals["high_density"]:
        return "DEVELOPMENT_ONLY"
    if confidence < low_confidence:
        return "NEED_CLASSIFICATION"
    if current_use >= hold_current_min and redevelopment >= hold_redevelopment_min:
        return "HOLD_5_10_REDEVELOP"
    if current_use >= live_min and redevelopment < hold_redevelopment_min:
        return "LIVE_HOME"
    if current_use < development_current_max and redevelopment >= development_redevelopment_min:
        if not signals["zoning_known"]:
            return "NEED_CLASSIFICATION"
        return "DEVELOPMENT_ONLY"
    if current_use < reject_current_max and redevelopment < development_redevelopment_min:
        return "REJECT"
    return "NEED_CLASSIFICATION"


def _value_profile(current_use: float, redevelopment: float, policy: Mapping[str, Any]) -> str:
    current_strong = float(policy.get("current_use_strong_min", 70.0))
    redevelopment_strong = float(policy.get("redevelopment_strong_min", 70.0))
    if current_use >= current_strong and redevelopment >= redevelopment_strong:
        return "DUAL_VALUE"
    if current_use >= current_strong:
        return "CURRENT_USE_STRONG"
    if redevelopment >= redevelopment_strong:
        return "REDEVELOPMENT_STRONG"
    return "MIXED_VALUE"


def _orthogonal_score_components(
    *,
    screening: Mapping[str, Any],
    area_score: float,
    location_score: float | None,
    location_enabled: bool,
    regional_price_safety: float,
    redevelopment_price_safety: float,
    micro_quality: float,
    family_layout: float,
    maintenance: float,
    school_score: float,
    lot_score: float,
    redevelopment: Mapping[str, float],
    value_ledger: Mapping[str, Any] | None = None,
    assessment_score: float | None = None,
    house_condition_score: float | None = None,
    location_quality_score: float | None = None,
    road_discount_pct: float | None = None,
    road_discount_status: str = "UNKNOWN",
) -> tuple[dict[str, float], dict[str, float]]:
    scoring = screening.get("orthogonal_scoring", {})
    scoring = scoring if isinstance(scoring, Mapping) else {}
    value_scoring = scoring.get("value_for_money_components", {})
    if isinstance(value_scoring, Mapping) and value_scoring:
        return _shared_value_for_money_components(
            scoring=scoring,
            ledger=value_ledger if isinstance(value_ledger, Mapping) else {},
            assessment_score=assessment_score,
            house_condition_score=house_condition_score,
            location_quality_score=location_quality_score,
            road_discount_pct=road_discount_pct,
            road_discount_status=road_discount_status,
            redevelopment=redevelopment,
        )
    current_weights = scoring.get("current_use_weights", {})
    current_weights = current_weights if isinstance(current_weights, Mapping) else {}
    redevelopment_weights = scoring.get("redevelopment_weights", {})
    redevelopment_weights = redevelopment_weights if isinstance(redevelopment_weights, Mapping) else {}

    current_defaults = {
        # Once the combined residual is enabled, the old area tier and school
        # proxy are disabled by default because both are already represented
        # in the residual. Explicit config can still override this policy.
        "area_neighbourhood_value": 0.0 if location_enabled else 20.0,
        "location_residual": 25.0 if location_enabled else 0.0,
        "regional_price_safety": 20.0 if location_enabled else 0.0,
        "micro_location": 15.0 if location_enabled else 20.0,
        "family_layout": 20.0 if location_enabled else 25.0,
        "maintenance_systems": 20.0 if location_enabled else 25.0,
        "school_proxy": 0.0 if location_enabled else 10.0,
    }
    current_raw = {
        "area_neighbourhood_value": (area_score, 20.0),
        "location_residual": (location_score if location_score is not None else 50.0, 100.0),
        "regional_price_safety": (regional_price_safety, 100.0),
        "micro_location": (micro_quality, 15.0),
        "family_layout": (family_layout, 25.0),
        "maintenance_systems": (maintenance, 25.0),
        "school_proxy": (school_score, 10.0),
    }
    current_components = {
        key: _scale(value, maximum, float(current_weights.get(key, current_defaults[key])))
        for key, (value, maximum) in current_raw.items()
    }

    redevelopment_defaults = {
        "lot_geometry": 20.0,
        "frontage_depth_threshold": 15.0,
        "official_zoning_entitlement": 25.0,
        "product_market_fit": 10.0 if location_enabled else 15.0,
        # Redevelopment uses the local price-safety result, which already
        # references the regional standard value. Do not add the residual a
        # second time by default.
        "location_residual": 0.0,
        "regional_price_safety": 15.0 if location_enabled else 0.0,
        "lane_access_servicing": 10.0,
        "permanent_execution_risk": 10.0 if location_enabled else 15.0,
    }
    redevelopment_raw = {
        "lot_geometry": (redevelopment["lot_area_geometry"], 20.0),
        "frontage_depth_threshold": (redevelopment["frontage_depth_threshold"], 15.0),
        "official_zoning_entitlement": (redevelopment["zoning_legal_path"], 25.0),
        "product_market_fit": (redevelopment["product_market_fit"], 15.0),
        "location_residual": (location_score if location_score is not None else 50.0, 100.0),
        "regional_price_safety": (redevelopment_price_safety, 100.0),
        "lane_access_servicing": (redevelopment["lane_access_servicing"], 5.0),
        "permanent_execution_risk": (redevelopment["permanent_defects"], 10.0),
    }
    redevelopment_components = {
        key: _scale(value, maximum, float(redevelopment_weights.get(key, redevelopment_defaults[key])))
        for key, (value, maximum) in redevelopment_raw.items()
    }
    return current_components, redevelopment_components


def _route_decision(decision: ScreeningDecision) -> ScreeningDecision:
    flags = list(dict.fromkeys(decision.flags + ["EXCLUDE_FROM_THREE_CLASS_MODEL"]))
    return decision.model_copy(
        update={
            "stage": "B_GATED",
            "status": "SPECIAL_TRANSACTION",
            "priority": "SPECIAL_TRANSACTION",
            "track": "SPECIAL_TRANSACTION",
            "asset_class": "SPECIAL_TRANSACTION",
            "classification_status": "GATED",
            "flags": flags,
            "reason_codes": flags,
            "reason": "Transaction/title signal routed before property classification; no normal class score was calculated.",
        },
        deep=True,
    )


def _stage_b_decision(decision: ScreeningDecision, config: Mapping[str, Any]) -> ScreeningDecision:
    screening = _screening_config(config)
    stratum, tier, area_score = _area_profile(decision.card, screening)
    location = _location_residual_profile(decision.card, screening)
    school_score, school_status, school_flags = _school_proxy(decision.card, screening)
    road_class, micro_nonroad_score, road_penalty, location_flags = _road_profile(decision.card, screening)
    road_discount = _road_discount_for_card(decision.card, screening, road_class)
    property_fronts_major_road = _effective_property_frontage(decision.card)
    road_quality = calculate_road_quality(
        road_class=road_class,
        road_category=road_discount["category"],
        distance_m=_card_value(decision.card, "official_distance_to_major_road_m", "distance_to_major_road_m"),
        property_fronts_major_road=property_fronts_major_road,
        policy=screening.get("road_quality_model"),
    )
    road_reference_value = _road_reference_value(decision.card, screening, location)
    road_adjusted_standard_value = (
        round(road_reference_value * (1.0 - float(road_discount["base_pct"])), 2)
        if road_reference_value is not None
        else None
    )
    road_discount_low_cad = (
        round(road_reference_value * float(road_discount["low_pct"]), 2)
        if road_reference_value is not None
        else None
    )
    road_discount_cad = (
        round(road_reference_value * float(road_discount["base_pct"]), 2)
        if road_reference_value is not None
        else None
    )
    road_discount_high_cad = (
        round(road_reference_value * float(road_discount["high_pct"]), 2)
        if road_reference_value is not None
        else None
    )
    lot_score, _, lot_flags = _lot_component(decision.card)
    family_layout, layout_known, area_per_bedroom, layout_flags = _family_layout_component(
        decision.card,
        screening.get("current_use_layout", {}),
    )
    condition_proxy, survival, holding_status, holding_flags, condition_known = _condition_components(decision.card)
    reno = _reno_profile(decision.card, screening)
    asking = _number(decision.card.asking)
    current_use_all_in_basis = (
        round(asking + (reno["estimate"] or 0.0), 2)
        if asking is not None
        else None
    )
    # The current-use score now reflects the approved renovation bands.  Keep
    # the old condition proxy available for diagnostics and BCA fallback math,
    # but do not let it compete with the explicit Reno score.
    maintenance = float(reno["score"]) / 4.0
    flexibility, flexibility_known = _holding_flexibility(decision.card)
    raw_relative_price, ratio, price_flags = _relative_price_component(
        decision.card,
        screening,
        location_standard_property_value=road_adjusted_standard_value,
    )
    current_relative_price, current_basis_ratio, current_price_flags = _relative_price_component(
        decision.card,
        screening,
        location_standard_property_value=road_adjusted_standard_value,
        price_basis=current_use_all_in_basis,
    )
    physical = _physical_normalization_profile(
        decision.card,
        screening,
        location=location,
        maintenance=condition_proxy,
        holding_status=holding_status,
        reno_estimate=reno["estimate"],
        # Road discount is a parcel-specific negative location adjustment.
        # Keep it separate from the area residual so both area-only and
        # risk-adjusted land bases can be reported.
        parcel_location_adjustment_cad=-(road_discount_cad or 0.0),
    )
    # Current-use/price-value rankings use the local standard-property price
    # signal.  The redevelopment axis gets its separate BCA-normalized land
    # signal below; neither axis should silently replace the other.
    relative_price = raw_relative_price
    physical_price_flags = [
        "PHYSICAL_PRICE_SIGNAL_APPLIED"
    ] if physical["current_price_signal"] is not None else []
    (
        assessment_score,
        assessment_status,
        assessment_year,
        assessment_total,
        assessment_land,
        assessment_improvement,
        ask_to_assessment_ratio,
        assessment_discount_pct,
        ask_to_land_assessment_ratio,
        land_assessment_discount_pct,
        assessment_flags,
    ) = _assessment_discount_component(decision.card, screening)
    fatigue = _fatigue_component(decision.card)
    price_safety = _price_safety_score(
        screening,
        assessment_score=assessment_score if assessment_status != "UNKNOWN" else None,
        relative_price=current_relative_price,
        fatigue=fatigue,
    )
    redevelopment_relative_price = (
        physical["redevelopment_price_signal"]
        if physical["redevelopment_price_signal"] is not None
        else raw_relative_price
    )
    redevelopment_price_safety = _price_safety_score(
        screening,
        assessment_score=assessment_score if assessment_status != "UNKNOWN" else None,
        relative_price=redevelopment_relative_price,
        fatigue=fatigue,
    )
    area_lot_basis_ratio = (
        physical["area_normalized_lot_basis_per_sqft"]
        / physical["normalized_reference_land_price_per_sqft"]
        if physical["area_normalized_lot_basis_per_sqft"] is not None
        and physical["normalized_reference_land_price_per_sqft"]
        else None
    )
    value_ledger = {
        "current_package_value_ratio": physical["current_package_value_ratio"],
        "current_lot_basis_ratio": physical["current_lot_basis_ratio"],
        "regional_price_ratio": physical["regional_price_ratio"],
        "redevelopment_price_ratio": physical["redevelopment_price_ratio"],
        "area_lot_basis_ratio": round(area_lot_basis_ratio, 4) if area_lot_basis_ratio is not None else None,
    }
    family_layout_score = _scale(family_layout, 25.0, 100.0)
    # House utility is one shared fact in the ledger. Required holding work
    # remains the larger input, while layout captures size, usable bedrooms /
    # baths and multiple-main-kitchen penalties without rewarding 7+ rooms.
    house_condition_score = round(
        float(reno["score"]) * 0.65 + family_layout_score * 0.35,
        2,
    )
    current_package_value_for_money_score = _ratio_value_score(
        _number(physical["current_package_value_ratio"])
    )
    house_value_for_money_score = round(
        house_condition_score * 0.40
        + current_package_value_for_money_score * 0.60,
        2,
    )
    current_lot_value_for_money_score = _ratio_value_score(
        _number(physical["current_lot_basis_ratio"])
    )
    redevelopment_lot_value_for_money_score = _ratio_value_score(
        area_lot_basis_ratio
    )
    regional_standard_value = (
        _number(location["standard_property_value"])
        if location["enabled"]
        else None
    )
    regional_price_dislocation_cad = (
        round(regional_standard_value - asking, 2)
        if regional_standard_value is not None and asking is not None
        else None
    )
    regional_price_dislocation_pct = (
        round(regional_price_dislocation_cad / regional_standard_value, 4)
        if regional_price_dislocation_cad is not None and regional_standard_value
        else None
    )
    micro_quality = round(
        max(0.0, min(15.0, micro_nonroad_score + road_quality["micro_location_road_score"])),
        2,
    )
    school_family = round(min(10.0, school_score * 0.5 + area_score / 20.0 * 5.0), 2)
    current_components = {
        "family_layout": family_layout,
        "maintenance_systems": maintenance,
        "survival_capex_proxy": survival,
        "micro_location_noise": micro_quality,
        "micro_location_nonroad": micro_nonroad_score,
        "micro_location_road": road_quality["micro_location_road_score"],
        "school_family_environment": school_family,
        "parking_basement_suite_flexibility": flexibility,
        "functional_area_per_bedroom": area_per_bedroom if area_per_bedroom is not None else 0.0,
    }
    redevelopment_components, signals, redevelopment_flags = _redevelopment_components(
        decision.card, lot_score, road_class, decision.flags, road_discount
    )
    # 33 x 122 ft / approximately 4,026 sqft is the user's standard rebuild
    # lot and is intentionally a passing baseline, not a maximum.  The
    # redevelopment score must recognize that a 2,700 sqft lot is materially
    # below the target product geometry even when its asking price is low.
    geometry_total = (
        redevelopment_components["lot_area_geometry"]
        + redevelopment_components["frontage_depth_threshold"]
    )
    redevelopment_geometry_adequacy_score = round(
        max(0.0, min(100.0, geometry_total / 35.0 * 100.0)),
        2,
    )
    redevelopment_lot_value_for_money_score = round(
        redevelopment_lot_value_for_money_score * 0.30
        + redevelopment_geometry_adequacy_score * 0.70,
        2,
    )
    redevelopment_capacity_value_for_money_score = _redevelopment_capacity_value_score(
        redevelopment_components
    )
    current_location_quality_score = _current_location_quality_score(
        residual_score=location["score"],
        road_discount_pct=road_discount["base_pct"],
        road_discount_status=road_discount["status"],
    )
    current_use_reno_cap = _current_use_reno_cap(
        reno["estimate"],
        screening.get("reno_estimation", {}),
    )
    value_ledger["redevelopment_geometry_adequacy_score"] = redevelopment_geometry_adequacy_score
    current_score_components, redevelopment_score_components = _orthogonal_score_components(
        screening=screening,
        area_score=area_score,
        location_score=location["score"],
        location_enabled=location["enabled"],
        regional_price_safety=price_safety,
        redevelopment_price_safety=redevelopment_price_safety,
        micro_quality=micro_quality,
        family_layout=family_layout,
        maintenance=maintenance,
        school_score=school_score,
        lot_score=lot_score,
        redevelopment=redevelopment_components,
        value_ledger=value_ledger,
        assessment_score=assessment_score if assessment_status != "UNKNOWN" else None,
        house_condition_score=house_condition_score,
        location_quality_score=current_location_quality_score,
        road_discount_pct=road_discount["base_pct"],
        road_discount_status=road_discount["status"],
    )
    current_use = round(sum(current_score_components.values()), 2)
    redevelopment_readiness = round(sum(redevelopment_score_components.values()), 2)
    reno_cap_applied = False
    road_frontage_cap_applied = False
    low_condition_cap_applied = False
    value_for_money_model_enabled = bool(
        isinstance(screening.get("orthogonal_scoring"), Mapping)
        and isinstance(screening.get("orthogonal_scoring", {}).get("value_for_money_components"), Mapping)
        and screening.get("orthogonal_scoring", {}).get("value_for_money_components")
    )
    # The reno cap sits one band step above the reno band value instead of
    # pinning the score to the band itself, so package and location quality
    # still differentiate homes inside each holding-work tier.  Homes
    # fronting a major road are capped further, tiered by road class:
    # arterial bus/truck corridors cap lower than ordinary arterials.
    if (
        value_for_money_model_enabled
        and current_use_reno_cap is not None
        and current_use > current_use_reno_cap
    ):
        current_use = current_use_reno_cap
        current_score_components = _rescale_score_components(
            current_score_components,
            current_use,
        )
        reno_cap_applied = True
    # Livability gate: Current Use presumes the house can actually be lived
    # in.  A bottom-band condition cannot produce more than a bottom-band
    # Current Use score no matter how cheap the lot or the asking price is.
    if value_for_money_model_enabled and house_condition_score < 40.0:
        condition_cap = 20.0 if house_condition_score < 20.0 else 40.0
        if current_use > condition_cap:
            current_use = condition_cap
            current_score_components = _rescale_score_components(
                current_score_components,
                current_use,
            )
            low_condition_cap_applied = True
    if value_for_money_model_enabled and property_fronts_major_road:
        road_frontage_cap = _road_frontage_current_use_cap(
            str(road_discount.get("category") or ""),
            screening.get("road_quality_model"),
        )
        if current_use > road_frontage_cap:
            current_use = road_frontage_cap
            current_score_components = _rescale_score_components(
                current_score_components,
                current_use,
            )
            road_frontage_cap_applied = True
    if "NON_STANDARD_HOLDING_LAYOUT" in decision.flags:
        current_use = min(current_use, 55.0)
        current_score_components = _rescale_score_components(
            current_score_components,
            current_use,
        )
    if reno["band"] == "CURRENT_USE_PASS":
        current_use = 0.0
        current_score_components = {key: 0.0 for key in current_score_components}
        current_score_components["reno_current_use_pass"] = 0.0
    if "REDEVELOPMENT_BLOCK_SIGNAL" in redevelopment_flags:
        redevelopment_readiness = min(redevelopment_readiness, 20.0)
        redevelopment_score_components = _rescale_score_components(
            redevelopment_score_components,
            redevelopment_readiness,
        )
    confidence = _classification_confidence(
        decision.card,
        layout_known=layout_known,
        condition_known=condition_known,
        school_known=school_status == "KNOWN",
        road_class=road_class,
        signals=signals,
    )
    orthogonal_policy = screening.get("orthogonal_scoring", {})
    orthogonal_policy = orthogonal_policy if isinstance(orthogonal_policy, Mapping) else {}
    profile = _value_profile(current_use, redevelopment_readiness, orthogonal_policy)
    dual_policy = orthogonal_policy.get("dual_value", {})
    dual_policy = dual_policy if isinstance(dual_policy, Mapping) else {}
    dual_min_current = float(dual_policy.get("minimum_current_use", 60.0))
    dual_min_redevelopment = float(dual_policy.get("minimum_redevelopment", 60.0))
    unified_value_model = isinstance(orthogonal_policy.get("value_for_money_components"), Mapping) and bool(orthogonal_policy.get("value_for_money_components"))
    # The current production model treats Dual as a ranking view over every
    # scored row.  The legacy gate is retained only for old replay configs so
    # historical tests/artifacts keep their prior semantics.
    dual_value = (
        round((current_use * redevelopment_readiness) ** 0.5, 2)
        if unified_value_model
        else round((current_use * redevelopment_readiness) ** 0.5, 2)
        if current_use >= dual_min_current and redevelopment_readiness >= dual_min_redevelopment
        else None
    )
    investment = _investment_score(
        screening,
        current_use=current_use,
        redevelopment=redevelopment_readiness,
        price_safety=price_safety,
    )
    confidence_policy = orthogonal_policy.get("confidence", {})
    confidence_policy = confidence_policy if isinstance(confidence_policy, Mapping) else {}
    # Confidence is deliberately not a score multiplier or leaderboard gate.
    # It only controls the separate manual-review queue below.
    verification_threshold = float(confidence_policy.get("verification_threshold", 70.0))
    # Keep this diagnostic on the same scale as the redevelopment geometry
    # model.  The old implementation reused the Stage-B sufficiency gate,
    # which made every lot >=4,000 sqft look like 100/100 here.
    land_adequacy = redevelopment_geometry_adequacy_score
    flags = list(dict.fromkeys(
        decision.flags
        + lot_flags
        + location_flags
        + location["flags"]
        + holding_flags
        + school_flags
        + price_flags
        + physical_price_flags
        + physical["flags"]
        + assessment_flags
        + redevelopment_flags
        + layout_flags
        + current_price_flags
    ))
    if road_discount["status"] == "APPLIED" and road_discount["base_pct"] > 0:
        flags.append("ROAD_DISCOUNT_PRIOR_APPLIED")
    elif road_discount["status"] == "BACKGROUND":
        flags.append("ROAD_DISCOUNT_BACKGROUND_ONLY")
    flags.append(f"RENO_BAND_{reno['band']}")
    if reno["band"] == "CURRENT_USE_PASS":
        flags.append("CURRENT_USE_RENO_PASS")
    if reno_cap_applied:
        flags.append("CURRENT_USE_RENO_CAP_APPLIED")
    if low_condition_cap_applied:
        flags.append("LOW_CONDITION_CURRENT_USE_CAPPED")
    if road_frontage_cap_applied:
        flags.append("ROAD_FRONTAGE_CURRENT_USE_CAPPED")
    if confidence < 60.0:
        flags.append("CLASSIFICATION_DATA_INCOMPLETE")
    if confidence < verification_threshold:
        flags.append("NEED_VERIFICATION")
    priority = "PRIORITY_HIGH" if dual_value is not None and dual_value >= 75 else "PRIORITY_MEDIUM" if dual_value is not None else "SCORED"
    summary_components = {
        "current_use_score": current_use,
        "redevelopment_score": redevelopment_readiness,
        "confidence_score": confidence,
        "area_relative_price_signal": relative_price,
        "assessment_discount_score": assessment_score if assessment_score is not None else 0.0,
        "price_safety_score": price_safety,
        "investment_score": investment,
        "seller_fatigue": fatigue,
        "ask_to_assessment_ratio": ask_to_assessment_ratio if ask_to_assessment_ratio is not None else 0.0,
        "assessment_discount_pct": assessment_discount_pct if assessment_discount_pct is not None else 0.0,
        "ask_to_land_assessment_ratio": ask_to_land_assessment_ratio if ask_to_land_assessment_ratio is not None else 0.0,
        "land_assessment_discount_pct": land_assessment_discount_pct if land_assessment_discount_pct is not None else 0.0,
        "functional_area_per_bedroom": area_per_bedroom if area_per_bedroom is not None else 0.0,
        "location_residual_cad": location["residual_cad"] if location["residual_cad"] is not None else 0.0,
        "location_standard_property_value": location["standard_property_value"] if location["standard_property_value"] is not None else 0.0,
        "location_residual_score": location["score"] if location["score"] is not None else 0.0,
        "regional_standard_value": regional_standard_value if regional_standard_value is not None else 0.0,
        "regional_price_dislocation_cad": regional_price_dislocation_cad if regional_price_dislocation_cad is not None else 0.0,
        "regional_price_dislocation_pct": regional_price_dislocation_pct if regional_price_dislocation_pct is not None else 0.0,
        "regional_price_safety_score": price_safety,
        "redevelopment_price_safety_score": redevelopment_price_safety,
        "location_quality_score": current_location_quality_score,
        "redevelopment_geometry_adequacy_score": redevelopment_geometry_adequacy_score,
        "current_use_reno_cap": current_use_reno_cap if current_use_reno_cap is not None else 0.0,
        "redevelopment_road_exit_discount_pct": redevelopment_components["road_exit_discount_pct"],
        "redevelopment_road_exit_value_factor": redevelopment_components["road_exit_value_factor"],
        "road_discount_low_pct": road_discount["low_pct"],
        "road_discount_pct": road_discount["base_pct"],
        "road_discount_high_pct": road_discount["high_pct"],
        "road_discount_low_cad": road_discount_low_cad or 0.0,
        "road_discount_cad": road_discount_cad or 0.0,
        "road_discount_high_cad": road_discount_high_cad or 0.0,
        "road_adjusted_standard_property_value": road_adjusted_standard_value or 0.0,
        "physical_adjusted_expected_value": physical["physical_adjusted_expected_value"] or 0.0,
        "physical_price_dislocation_cad": physical["physical_price_dislocation_cad"] or 0.0,
        "physical_price_dislocation_pct": physical["physical_price_dislocation_pct"] or 0.0,
        "market_land_share_prior": physical["market_land_share_prior"] or 0.0,
        "bca_land_share": physical["bca_land_share"] or 0.0,
        "physical_lot_factor": physical["physical_lot_factor"] or 0.0,
        "normalized_land_value": physical["normalized_land_value"] or 0.0,
        "normalized_existing_house_value": physical["normalized_existing_house_value"] or 0.0,
        "normalized_lot_basis": physical["normalized_lot_basis"] or 0.0,
        "normalized_lot_basis_per_sqft": physical["normalized_lot_basis_per_sqft"] or 0.0,
        "normalized_reference_land_value": physical["normalized_reference_land_value"] or 0.0,
        "normalized_reference_land_price_per_sqft": physical["normalized_reference_land_price_per_sqft"] or 0.0,
        "physical_current_price_ratio": physical["current_price_ratio"] or 0.0,
        "physical_redevelopment_price_ratio": physical["redevelopment_price_ratio"] or 0.0,
        "current_use_effective_basis": physical["current_use_effective_basis"] or 0.0,
        "market_house_value_proxy": physical["market_house_value_proxy"] or 0.0,
        "market_land_basis": physical["market_land_basis"] or 0.0,
        "area_normalized_lot_basis": physical["area_normalized_lot_basis"] or 0.0,
        "area_normalized_lot_basis_per_sqft": physical["area_normalized_lot_basis_per_sqft"] or 0.0,
        "risk_adjusted_lot_basis": physical["risk_adjusted_lot_basis"] or 0.0,
        "risk_adjusted_lot_basis_per_sqft": physical["risk_adjusted_lot_basis_per_sqft"] or 0.0,
        "area_standard_lot_equivalent_basis": physical["area_standard_lot_equivalent_basis"] or 0.0,
        "risk_adjusted_standard_lot_equivalent_basis": physical["risk_adjusted_standard_lot_equivalent_basis"] or 0.0,
        "current_package_value_proxy": physical["current_package_value_proxy"] or 0.0,
        "current_package_value_proxy_source": physical["current_package_value_proxy_source"],
        "current_package_value_ratio": physical["current_package_value_ratio"] or 0.0,
        "current_lot_basis_per_sqft": physical["current_lot_basis_per_sqft"] or 0.0,
        "current_lot_basis_ratio": physical["current_lot_basis_ratio"] or 0.0,
        "current_value_gap_cad": physical["current_value_gap_cad"] or 0.0,
        "regional_price_ratio": physical["regional_price_ratio"] or 0.0,
        "regional_price_discount_pct": physical["regional_price_discount_pct"] or 0.0,
        "bca_discount_pct": physical["bca_discount_pct"] or 0.0,
        "house_value_for_money_score": house_value_for_money_score,
        "house_condition_score": house_condition_score,
        "current_package_value_for_money_score": current_package_value_for_money_score,
        "location_value_for_money_score": current_location_quality_score,
        "lot_value_for_money_score": current_lot_value_for_money_score,
        "current_lot_value_for_money_score": current_lot_value_for_money_score,
        "redevelopment_lot_value_for_money_score": redevelopment_lot_value_for_money_score,
        "redevelopment_capacity_value_for_money_score": redevelopment_capacity_value_for_money_score,
        "bca_price_safety_score": assessment_score if assessment_score is not None else 50.0,
        "permanent_risk_score": current_score_components.get("permanent_risk", 50.0),
        "bca_house_value_proxy": physical["bca_house_value_proxy"] or 0.0,
        "bca_normalized_land_basis": physical["bca_normalized_land_basis"] or 0.0,
        "bca_normalized_land_basis_per_sqft": physical["bca_normalized_land_basis_per_sqft"] or 0.0,
        "current_use_all_in_basis": current_use_all_in_basis or 0.0,
        "current_use_basis_price_ratio": current_basis_ratio or 0.0,
        "reno_estimate": reno["estimate"],
        "reno_score": reno["score"],
        "reno_band": reno["band"],
        "price_value_score": price_safety,
        "current_use.functional_area_per_bedroom": area_per_bedroom if area_per_bedroom is not None else 0.0,
        **{f"current_use.{key}": value for key, value in current_score_components.items()},
        **{f"redevelopment.{key}": value for key, value in redevelopment_score_components.items()},
    }
    return decision.model_copy(
        update={
            "stage": "B_SCORED", "status": "SCORED", "priority": "SCORED",
            # V2.5 deliberately does not classify the property into a use
            # category.  value_profile is a derived label for reporting only;
            # the three independent scores are authoritative.
            "track": "VALUE_PROFILE", "asset_class": "ORTHOGONAL_SCORED", "value_profile": profile,
            "classification_status": "PROVISIONAL",
            "classification_confidence": confidence, "confidence_score": confidence,
            "current_use_index": current_use,
            "redevelopment_readiness_index": redevelopment_readiness,
            "current_use_components": current_components,
            "redevelopment_components": redevelopment_components,
            "current_use_score_components": current_score_components,
            "redevelopment_score_components": redevelopment_score_components,
            "current_use_score": current_use,
            "redevelopment_score": redevelopment_readiness,
            "dual_value_score": dual_value,
            # Deprecated V2.3 fields must not carry a competing score.
            "class_score": None,
            "area_stratum": stratum, "area_tier": tier, "stage_b_score": None, "home_fit_score": current_use,
            "land_adequacy_score": land_adequacy, "school_proxy_status": school_status,
            "location_residual_cad": location["residual_cad"],
            "location_standard_property_value": location["standard_property_value"],
            "location_residual_score": location["score"],
            "location_residual_segment": location["segment"],
            "location_residual_confidence": location["confidence"],
            "location_residual_source": location["source"],
            "regional_standard_value": regional_standard_value,
            "regional_price_dislocation_cad": regional_price_dislocation_cad,
            "regional_price_dislocation_pct": regional_price_dislocation_pct,
            "regional_price_safety_score": price_safety,
            "redevelopment_price_safety_score": redevelopment_price_safety,
            "location_quality_score": current_location_quality_score,
            "redevelopment_geometry_adequacy_score": redevelopment_geometry_adequacy_score,
            "current_use_reno_cap": current_use_reno_cap,
            "physical_adjusted_expected_value": physical["physical_adjusted_expected_value"],
            "physical_price_dislocation_cad": physical["physical_price_dislocation_cad"],
            "physical_price_dislocation_pct": physical["physical_price_dislocation_pct"],
            "market_land_share_prior": physical["market_land_share_prior"],
            "bca_land_share": physical["bca_land_share"],
            "physical_lot_factor": physical["physical_lot_factor"],
            "normalized_land_value": physical["normalized_land_value"],
            "normalized_existing_house_value": physical["normalized_existing_house_value"],
            "normalized_lot_basis": physical["normalized_lot_basis"],
            "normalized_lot_basis_per_sqft": physical["normalized_lot_basis_per_sqft"],
            "bca_house_value_proxy": physical["bca_house_value_proxy"],
            "bca_normalized_land_basis": physical["bca_normalized_land_basis"],
            "bca_normalized_land_basis_per_sqft": physical["bca_normalized_land_basis_per_sqft"],
            "current_use_all_in_basis": current_use_all_in_basis,
            "current_use_basis_price_ratio": current_basis_ratio,
            "reno_estimate": reno["estimate"],
            "reno_score": reno["score"],
            "reno_band": reno["band"],
            "reno_year": reno["reno_year"],
            "reno_scope": reno["scope"],
            "reno_evidence": reno["evidence"],
            "reno_confidence": reno["confidence"],
            "normalized_reference_land_value": physical["normalized_reference_land_value"],
            "normalized_reference_land_price_per_sqft": physical["normalized_reference_land_price_per_sqft"],
            "physical_normalization_status": physical["status"],
            "current_use_effective_basis": physical["current_use_effective_basis"],
            "market_house_value_proxy": physical["market_house_value_proxy"],
            "market_land_basis": physical["market_land_basis"],
            "area_normalized_lot_basis": physical["area_normalized_lot_basis"],
            "area_normalized_lot_basis_per_sqft": physical["area_normalized_lot_basis_per_sqft"],
            "risk_adjusted_lot_basis": physical["risk_adjusted_lot_basis"],
            "risk_adjusted_lot_basis_per_sqft": physical["risk_adjusted_lot_basis_per_sqft"],
            "area_standard_lot_equivalent_basis": physical["area_standard_lot_equivalent_basis"],
            "risk_adjusted_standard_lot_equivalent_basis": physical["risk_adjusted_standard_lot_equivalent_basis"],
            "current_package_value_proxy": physical["current_package_value_proxy"],
            "current_package_value_proxy_source": physical["current_package_value_proxy_source"],
            "current_package_value_ratio": physical["current_package_value_ratio"],
            "current_lot_basis_per_sqft": physical["current_lot_basis_per_sqft"],
            "current_lot_basis_ratio": physical["current_lot_basis_ratio"],
            "current_value_gap_cad": physical["current_value_gap_cad"],
            "regional_price_ratio": physical["regional_price_ratio"],
            "regional_price_discount_pct": physical["regional_price_discount_pct"],
            "bca_discount_pct": physical["bca_discount_pct"],
            "house_value_for_money_score": house_value_for_money_score,
            "house_condition_score": house_condition_score,
            "current_package_value_for_money_score": current_package_value_for_money_score,
            "location_value_for_money_score": current_location_quality_score,
            "lot_value_for_money_score": current_lot_value_for_money_score,
            "current_lot_value_for_money_score": current_lot_value_for_money_score,
            "redevelopment_lot_value_for_money_score": redevelopment_lot_value_for_money_score,
            "redevelopment_capacity_value_for_money_score": redevelopment_capacity_value_for_money_score,
            "bca_price_safety_score": assessment_score if assessment_score is not None else 50.0,
            "permanent_risk_score": current_score_components.get("permanent_risk", 50.0),
            "area_relative_price_signal": relative_price, "seller_fatigue_score": fatigue,
            "area_relative_price_ratio": ratio, "road_class": road_class,
            "property_fronts_major_road": road_quality["property_fronts_major_road"],
            "road_distance_m": road_quality["distance_m"],
            "road_frontage_method": _property_frontage_method(decision.card),
            "road_penalty": road_penalty,
            "road_quality_direct_score": road_quality["direct_road_quality_score"],
            "road_quality_exposure_factor": road_quality["exposure_factor"],
            "road_quality_effective_score": road_quality["effective_road_quality_score"],
            "road_quality_micro_score": road_quality["micro_location_road_score"],
            "road_quality_micro_penalty": road_quality["micro_location_road_penalty"],
            "micro_location_nonroad_score": micro_nonroad_score,
            "road_discount_status": road_discount["status"],
            "road_discount_method": road_discount["method"],
            "road_discount_category": road_discount["category"],
            "road_discount_low_pct": road_discount["low_pct"],
            "road_discount_pct": road_discount["base_pct"],
            "road_discount_high_pct": road_discount["high_pct"],
            "road_discount_exposure_factor": road_discount["exposure_factor"],
            "road_discount_basis_value": road_reference_value,
            "road_discount_low_cad": road_discount_low_cad,
            "road_discount_cad": road_discount_cad,
            "road_discount_high_cad": road_discount_high_cad,
            "road_adjusted_standard_property_value": road_adjusted_standard_value,
            "functional_area_per_bedroom": area_per_bedroom,
            "assessment_year": assessment_year, "assessment_total": assessment_total,
            "assessment_land": assessment_land, "assessment_improvement": assessment_improvement,
            "ask_to_assessment_ratio": ask_to_assessment_ratio,
            "assessment_discount_pct": assessment_discount_pct,
            "ask_to_land_assessment_ratio": ask_to_land_assessment_ratio,
            "land_assessment_discount_pct": land_assessment_discount_pct,
            "assessment_discount_score": assessment_score,
            "assessment_discount_status": assessment_status,
            "price_safety_score": price_safety, "price_value_score": price_safety, "investment_score": investment,
            "current_use_value_for_money_score": current_use,
            "redevelopment_value_for_money_score": redevelopment_readiness,
            "maintenance_systems_score": current_score_components.get("house_value_for_money", 0.0),
            "score_components": summary_components, "holding_status": holding_status, "flags": flags,
            "reason_codes": flags,
            "reason": (
                f"V2.7 unified value-ledger scores; Current Use {current_use:.2f}; "
                f"Redevelopment {redevelopment_readiness:.2f}; Confidence {confidence:.2f}; "
                f"profile {profile}; Dual Value {dual_value if dual_value is not None else 'not eligible'}; "
                f"Investment {investment:.2f}; Assessment signal {assessment_score if assessment_score is not None else 'UNKNOWN'}; "
                f"Location residual {location['residual_cad'] if location['residual_cad'] is not None else 'UNKNOWN'}."
            ),
        },
        deep=True,
    )


def screen_discovery_candidates(cards: Iterable[DiscoveryCard | Mapping[str, Any]], config: Mapping[str, Any]) -> ScreeningResult:
    """Score every normal listing on independent current-use and redevelopment axes."""
    config = _prepare_screening_config(config)
    # Screening is also a supported direct entry point (not only the official
    # enrichment pipeline), so apply the same deterministic lot normalizer in
    # both paths.  This prevents stale cached artifacts from reintroducing a
    # parser value such as ``lot_area_sqft=1``.
    from .discovery_official import normalize_discovery_card

    materialized = [
        normalize_discovery_card(card if isinstance(card, DiscoveryCard) else DiscoveryCard.model_validate(card))
        for card in cards
    ]
    stage_a_candidates: list[ScreeningDecision] = []
    stage_a_excluded: list[ScreeningDecision] = []
    for card in materialized:
        decision = _stage_a_decision(card, config)
        (stage_a_candidates if decision.status == "PASS" else stage_a_excluded).append(decision)
    special: list[ScreeningDecision] = []
    screened: list[ScreeningDecision] = []
    for item in stage_a_candidates:
        if item.asset_class == "SPECIAL_TRANSACTION":
            special.append(_route_decision(item))
        else:
            screened.append(_stage_b_decision(item, config))

    screening = _screening_config(config)
    orthogonal_policy = screening.get("orthogonal_scoring", {})
    orthogonal_policy = orthogonal_policy if isinstance(orthogonal_policy, Mapping) else {}
    current_min = float(orthogonal_policy.get("current_use_strong_min", 70.0))
    redevelopment_min = float(orthogonal_policy.get("redevelopment_strong_min", 70.0))

    current_use = sorted(
        [item for item in screened if (item.current_use_score or 0.0) > 0 and "CURRENT_USE_RENO_PASS" not in item.flags],
        key=lambda item: (item.current_use_score or 0.0, item.card.address),
        reverse=True,
    )
    redevelopment = sorted(screened, key=lambda item: (item.redevelopment_score or 0.0, item.card.address), reverse=True)
    dual = sorted(
        [item for item in screened if item.dual_value_score is not None],
        key=lambda item: (item.dual_value_score or 0.0, item.card.address),
        reverse=True,
    )
    investment = sorted(
        screened,
        key=lambda item: (item.investment_score or 0.0, item.card.address),
        reverse=True,
    )
    price_value = sorted(
        screened,
        key=lambda item: (item.price_value_score or 0.0, item.card.address),
        reverse=True,
    )
    verification = sorted(
        [item for item in screened if (item.confidence_score or 0.0) < float(orthogonal_policy.get("confidence", {}).get("verification_threshold", 70.0))],
        key=lambda item: ((item.current_use_score or 0.0) + (item.redevelopment_score or 0.0), item.card.address),
        reverse=True,
    )
    # Legacy fields are retained as compatibility projections only. They are
    # not used by the V2.5 report or ranking and do not remove rows from any
    # authoritative leaderboard.
    live = [item for item in current_use if (item.current_use_score or 0.0) >= current_min]
    hold = dual
    development = [item for item in redevelopment if (item.redevelopment_score or 0.0) >= redevelopment_min]
    needs = verification
    rejected: list[ScreeningDecision] = []
    stage_b = screened
    top10_current = current_use[:10]
    top10_redevelopment = redevelopment[:10]
    top10_dual = dual[:10]
    top10_investment = investment[:10]
    leaderboard_keys = {
        item.card.mls_number or item.card.address
        for item in (*top10_current, *top10_redevelopment, *top10_dual, *top10_investment, *price_value[:10])
    }
    return ScreeningResult(
        source_count=len(materialized), stage_a_candidates=stage_a_candidates, stage_a_excluded=stage_a_excluded,
        stage_b_candidates=stage_b,
        final_review_candidates=[],
        special_track_candidates=special,
        live_home_candidates=live,
        hold_redevelop_candidates=hold,
        development_only_candidates=development,
        need_classification_candidates=needs,
        rejected_candidates=rejected,
        live_home_final_candidates=top10_current,
        hold_redevelop_final_candidates=top10_dual,
        development_only_final_candidates=top10_redevelopment,
        current_use_candidates=current_use,
        redevelopment_candidates=redevelopment,
        dual_value_candidates=dual,
        current_use_top10=top10_current,
        redevelopment_top10=top10_redevelopment,
        dual_value_top10=top10_dual,
        investment_candidates=investment,
        investment_top10=top10_investment,
        price_value_candidates=price_value,
        price_value_top10=price_value[:10],
        verification_candidates=verification,
        stage_a_excluded_count=len(stage_a_excluded),
        stage_b_count=len(stage_b),
        final_review_count=len(leaderboard_keys),
        special_track_count=len(special),
        live_home_count=len(live),
        hold_redevelop_count=len(hold),
        development_only_count=len(development),
        need_classification_count=len(verification),
        rejected_count=len(rejected),
    )


__all__ = [
    "ScreeningDecision",
    "ScreeningResult",
    "calculate_road_discount",
    "calculate_road_quality",
    "screen_discovery_candidates",
]
