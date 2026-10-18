from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .models import (
    ApprovedUse,
    DimensionScore,
    EvidenceStatus,
    LotShape,
    OwnershipType,
    PropertyRecord,
    RedevelopmentScenario,
    RoadClass,
    ScoreComponent,
    ScoreSnapshot,
    ScenarioType,
)
from .provenance import EvidenceItem, calculate_data_confidence, default_evidence_items
from .normalization import calculate_lot_area_score


DIMENSION_MAXIMA: dict[str, float] = {
    "land_lot_title": 20,
    "price_safety_margin": 15,
    "current_house_utility": 15,
    "family_location_noise": 10,
    "school": 10,
    "redevelopment_option_value": 20,
    "resale_affordability": 5,
    "carrying_cost_risk": 5,
}

SUBCOMPONENT_MAXIMA: dict[str, dict[str, float]] = {
    "land_lot_title": {
        "ownership": 3,
        "lot_area": 4,
        "frontage_depth": 4,
        "shape_lane": 3,
        "land_scarcity": 3,
        "buildability": 3,
    },
    "price_safety_margin": {
        "comps": 6,
        "assessment": 3,
        "land_value": 3,
        "dom_pressure": 1,
        "capital_efficiency": 2,
    },
    "current_house_utility": {
        "finished_space": 3,
        "bed_bath": 2,
        "layout": 2,
        "basement": 2,
        "systems_structure": 2,
        "renovation_efficiency": 2,
        "effective_age": 1,
        "suite_flexibility": 1,
    },
    "family_location_noise": {
        "quietness": 2,
        "outdoor_environment": 2,
        "parks_daily_life": 2,
        "transit_walkability": 2,
        "family_street": 2,
    },
    "school": {
        "elementary_current": 2,
        "secondary_current": 1.5,
        "programs": 1.5,
        "capacity": 1,
        "improvement_trend": 1,
        "community": 1,
        "five_year_outlook": 1,
        "boundary_risk": 1,
    },
    "redevelopment_option_value": {
        "current_entitlement": 4,
        "duplex": 3,
        "multiplex": 3,
        "buildable_area": 3,
        "product_market_fit": 3,
        "toa_assembly": 2,
        "execution": 2,
    },
    "resale_affordability": {
        "buyer_pool": 2,
        "affordability": 2,
        "liquidity": 1,
    },
    "carrying_cost_risk": {
        "tax_utilities": 1,
        "maintenance": 1,
        "title_legal": 1,
        "permanent_defects": 1,
        "data_quality": 1,
    },
}


def _clamp(value: float, lower: float = 0, upper: float = 1) -> float:
    return max(lower, min(upper, value))


def _config_hash(config: dict[str, Any]) -> str:
    stable = {key: value for key, value in config.items() if not key.startswith("_")}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _unknown_ratio(config: dict[str, Any]) -> float:
    return float(config.get("scoring", {}).get("unknown_points_ratio", 0.5))


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    if isinstance(value, (OwnershipType, ApprovedUse)) and value.value == "UNKNOWN":
        return True
    return False


def _component(
    name: str,
    maximum_points: float,
    value: Any,
    fields: Iterable[str],
    score_fn: Callable[[Any], float],
    config: dict[str, Any],
    conflicts: set[str],
    explanation: str,
) -> ScoreComponent:
    field_names = list(fields)
    conflicted_fields = [field for field in field_names if field in conflicts]
    if conflicted_fields:
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.CONFLICT,
            explanation=f"{name}: material conflict; conservative provisional score.",
            unknown_fields=conflicted_fields,
        )
    missing_fields = [field for field in field_names if _is_missing(value) and len(field_names) == 1]
    if _is_missing(value):
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.UNKNOWN,
            explanation=f"{name}: unknown; conservative provisional score.",
            unknown_fields=field_names,
        )
    try:
        points = maximum_points * _clamp(float(score_fn(value)))
    except (TypeError, ValueError):
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.UNKNOWN,
            explanation=f"{name}: value could not be normalized.",
            unknown_fields=field_names,
        )
    return ScoreComponent(
        points=round(points, 2),
        maximum_points=maximum_points,
        evidence_status=EvidenceStatus.CONFIRMED,
        explanation=explanation,
        unknown_fields=missing_fields,
    )


def _compound_component(
    name: str,
    maximum_points: float,
    values: dict[str, Any],
    config: dict[str, Any],
    conflicts: set[str],
    score_fn: Callable[[dict[str, Any]], float],
    explanation: str,
) -> ScoreComponent:
    field_names = list(values)
    conflicted_fields = [field for field in field_names if field in conflicts]
    if conflicted_fields:
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.CONFLICT,
            explanation=f"{name}: material conflict; conservative provisional score.",
            unknown_fields=conflicted_fields,
        )
    available = {field: value for field, value in values.items() if not _is_missing(value)}
    if not available:
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.UNKNOWN,
            explanation=f"{name}: unknown; conservative provisional score.",
            unknown_fields=field_names,
        )
    missing = [field for field in field_names if field not in available]
    try:
        normalized_score = score_fn(available)
        if normalized_score is None:
            return ScoreComponent(
                points=round(maximum_points * _unknown_ratio(config), 2),
                maximum_points=maximum_points,
                evidence_status=EvidenceStatus.UNKNOWN,
                explanation=f"{name}: required local benchmark is unknown.",
                unknown_fields=field_names,
            )
        points = maximum_points * _clamp(float(normalized_score))
    except (TypeError, ValueError):
        return ScoreComponent(
            points=round(maximum_points * _unknown_ratio(config), 2),
            maximum_points=maximum_points,
            evidence_status=EvidenceStatus.UNKNOWN,
            explanation=f"{name}: value could not be normalized.",
            unknown_fields=field_names,
        )
    status = EvidenceStatus.UNKNOWN if missing else EvidenceStatus.CONFIRMED
    if missing:
        explanation = f"{explanation}; partial geometry evidence."
    return ScoreComponent(
        points=round(points, 2),
        maximum_points=maximum_points,
        evidence_status=status,
        explanation=explanation,
        unknown_fields=missing,
    )


def _score_label(
    points: float,
    maximum_points: float,
    statuses: Iterable[EvidenceStatus] = (),
) -> str:
    """Return the human-readable score band used in reports.

    The numeric score and evidence state are intentionally kept together in
    the label.  A 50% provisional score must never look like a confirmed
    "MIXED" result when the underlying fields are still unknown.
    """

    ratio = _clamp(float(points) / float(maximum_points)) if maximum_points else 0
    if ratio >= 0.80:
        base = "STRONG"
    elif ratio >= 0.65:
        base = "GOOD"
    elif ratio >= 0.50:
        base = "MIXED"
    else:
        base = "WEAK"
    evidence = set(statuses)
    if EvidenceStatus.CONFLICT in evidence:
        return f"{base} / CONFLICT"
    if EvidenceStatus.UNKNOWN in evidence or EvidenceStatus.UNCONFIRMED in evidence:
        return f"{base} / UNVERIFIED"
    return base


def _dimension(name: str, components: dict[str, ScoreComponent]) -> DimensionScore:
    points = round(sum(item.points for item in components.values()), 2)
    maximum_points = DIMENSION_MAXIMA[name]
    return DimensionScore(
        points=points,
        maximum_points=maximum_points,
        label=_score_label(
            points,
            maximum_points,
            (item.evidence_status for item in components.values()),
        ),
        components=components,
    )


def _enum_text(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "").lower()


def _score_from_percent(value: Any) -> float:
    return _clamp(float(value) / 100)


def _score_area(value: Any) -> float:
    return _clamp(float(value) / 8000)


def _score_lot_area(record: PropertyRecord, values: dict[str, Any], config: dict[str, Any]) -> float | None:
    required = set(config.get("geometry", {}).get("municipality_percentiles_required", []))
    if record.municipality in required and values.get("lot_size_percentile_in_neighbourhood") is None:
        return None
    if values.get("lot_size_percentile_in_neighbourhood") is not None:
        return _clamp(float(values["lot_size_percentile_in_neighbourhood"]) / 100)
    if values.get("lot_area_sqft") is not None:
        score = calculate_lot_area_score(record, config)
        return _clamp(float(score) / 100) if score is not None else None
    return None


def _score_frontage_depth(values: dict[str, Any]) -> float:
    def frontage_score(value: Any) -> float:
        frontage = float(value)
        if frontage < 33:
            return _clamp(frontage / 33 * 0.60)
        return _clamp(0.60 + (frontage - 33) / 17 * 0.40)

    def depth_score(value: Any) -> float:
        depth = float(value)
        if depth < 100:
            return _clamp(depth / 100 * 0.60)
        return _clamp(0.60 + (depth - 100) / 25 * 0.40)

    scores: list[float] = []
    if values.get("frontage_ft") is not None:
        scores.append(frontage_score(values["frontage_ft"]))
    if values.get("depth_ft") is not None:
        scores.append(depth_score(values["depth_ft"]))
    return sum(scores) / len(scores) if scores else 0


def _score_shape_lane(values: dict[str, Any]) -> float:
    shape = _enum_text(values.get("lot_shape"))
    shape_score = {
        "regular_rectangular": 1,
        "corner_regular": 0.8,
        "irregular": 0.5,
        "pie": 0.4,
        "flag": 0.3,
        "steep": 0.1,
    }.get(shape, 0.5)
    lane_score = 1 if values.get("lane_access") or values.get("lane") else 0
    return min(1, shape_score * 0.65 + lane_score * 0.35)


def _score_price_dislocation(value: Any) -> float:
    return _clamp(0.5 - float(value) * 2.5)


def _score_assessment(record: PropertyRecord) -> float | None:
    if record.asking is None or record.assessment_total is None:
        return None
    if record.assessment_land and record.asking <= record.assessment_land:
        return 1
    if record.asking <= record.assessment_total * 0.9:
        return 0.85
    if record.asking < record.assessment_total:
        return 0.65
    return 0.25


def _score_land_value(value: Any) -> float:
    return _clamp(0.5 - float(value) * 2.5)


def _score_dom(record: PropertyRecord) -> float | None:
    if record.dom is None and record.price_reduction_pct is None:
        return None
    if record.dom and record.dom > 120:
        return 1
    if record.dom and record.dom > 60:
        return 0.8
    if record.price_reduction_pct is not None and record.price_reduction_pct <= -0.10:
        return 0.9
    if record.price_reduction_pct is not None and record.price_reduction_pct < 0:
        return 0.7
    return 0.35


def _score_finished_space(value: Any) -> float:
    return _clamp(float(value) / 2500)


def _score_bed_bath(values: dict[str, Any]) -> float:
    bedrooms = float(values.get("bedrooms") or 0)
    bathrooms = float(values.get("bathrooms") or 0)
    return _clamp((min(bedrooms, 5) / 5 + min(bathrooms, 4) / 4) / 2)


def _score_basement(value: Any) -> float:
    text = _enum_text(value)
    if "finished" in text:
        return 1
    if text in {"yes", "full", "partial", "basement"}:
        return 0.65
    return 0.35


def _score_age(record: PropertyRecord, *, as_of: datetime | None = None) -> float | None:
    year = record.effective_year or record.year_built
    if year is None:
        return None
    reference_date = as_of or datetime.now(timezone.utc)
    age = max(0, reference_date.year - year)
    return _clamp(1 - max(0, age - 10) / 90)


def _score_noise(value: Any) -> float:
    return _clamp(1 - float(value) / 100)


def _score_slope(value: Any) -> float:
    text = _enum_text(value)
    if "steep" in text:
        return 0
    if text in {"flat", "level", "gentle"}:
        return 1
    return 0.5


def _score_school_with_catchment(values: dict[str, Any], catchment_field: str) -> float | None:
    """Score a school only when its official catchment is identified."""

    if _is_missing(values.get("current_school_score")) or _is_missing(values.get(catchment_field)):
        return None
    return _score_from_percent(values["current_school_score"])


def _score_permitted(record: PropertyRecord) -> float | None:
    if not _official_zoning_supported(record):
        return None
    if record.maximum_units is not None:
        return _clamp(float(record.maximum_units) / 6)
    if record.permitted_uses:
        return 0.5
    return None


def _score_duplex(record: PropertyRecord, scenarios: list[RedevelopmentScenario]) -> float | None:
    if not _official_zoning_supported(record):
        return None
    duplex = next((item for item in scenarios if item.scenario_type is ScenarioType.DUPLEX), None)
    area = record.max_duplex_area or record.duplex_area or (duplex.realistic_buildable_sqft if duplex else None)
    permitted = any("duplex" in str(value).lower() for value in record.permitted_uses)
    if area is None and not permitted:
        return None
    return _clamp((float(area or 0) / 3500) * 0.75 + (0.25 if permitted else 0))


def _score_multiplex(record: PropertyRecord, scenarios: list[RedevelopmentScenario]) -> float | None:
    if not _official_zoning_supported(record):
        return None
    multiplex = next((item for item in scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX), None)
    units = record.max_realistic_multiplex_units or record.maximum_units or (multiplex.unit_count if multiplex else None)
    if units is None:
        return None
    area = record.multiplex_area or (multiplex.realistic_buildable_sqft if multiplex else None)
    unit_score = _clamp(float(units) / 6)
    area_score = _clamp(float(area or 0) / 6000)
    return unit_score * 0.55 + area_score * 0.45


def _score_execution(record: PropertyRecord) -> float | None:
    if record.slope is None and record.heritage_status is None and not record.permanent_defects:
        return None
    penalty = 0
    if "steep" in _enum_text(record.slope):
        penalty += 0.45
    if record.heritage_status or record.heritage_register:
        penalty += 0.2
    if record.permanent_defects:
        penalty += min(0.5, len(record.permanent_defects) * 0.15)
    return _clamp(1 - penalty)


def _official_zoning_supported(record: PropertyRecord) -> bool:
    return bool(
        record.official_zoning_confirmed is True
        and record.official_zoning_source_url
        and record.official_zoning_source_registry_id
        and record.official_zoning_evidence_status.value == "CONFIRMED"
        and record.official_zoning_retrieved_at is not None
        and record.bylaw_version
    )


def _build_dimensions(
    record: PropertyRecord,
    config: dict[str, Any],
    conflicts: set[str],
    scenarios: list[RedevelopmentScenario],
    *,
    as_of: datetime | None = None,
) -> dict[str, DimensionScore]:
    maxima = SUBCOMPONENT_MAXIMA

    ownership = None if record.ownership_type is OwnershipType.UNKNOWN else record.ownership_type
    ownership_score = {
        OwnershipType.FREEHOLD_NON_STRATA: 1,
        OwnershipType.FREEHOLD_STRATA: 0.55,
        OwnershipType.PREPAID_LEASE: 0.35,
    }.get(record.ownership_type, 0)
    land_components = {
        "ownership": _component(
            "ownership",
            maxima["land_lot_title"]["ownership"],
            ownership,
            ["ownership_type"],
            lambda _: ownership_score,
            config,
            conflicts,
            "Ownership quality is explicit.",
        ),
        "lot_area": _compound_component(
            "lot_area",
            maxima["land_lot_title"]["lot_area"],
            {
                "lot_area_sqft": record.lot_area_sqft,
                "lot_size_percentile_in_neighbourhood": record.lot_size_percentile_in_neighbourhood,
            },
            config,
            conflicts,
            lambda values: _score_lot_area(record, values, config),
            "Lot area is evaluated with neighbourhood context when available.",
        ),
        "frontage_depth": _compound_component(
            "frontage_depth",
            maxima["land_lot_title"]["frontage_depth"],
            {"frontage_ft": record.frontage_ft, "depth_ft": record.depth_ft},
            config,
            conflicts,
            _score_frontage_depth,
            "Frontage and depth are scored separately from area.",
        ),
        "shape_lane": _compound_component(
            "shape_lane",
            maxima["land_lot_title"]["shape_lane"],
            {
                "lot_shape": record.lot_shape,
                "lane_access": record.lane_access if record.lane_access is not None else record.lane,
            },
            config,
            conflicts,
            _score_shape_lane,
            "Shape and lane access affect servicing and product flexibility.",
        ),
        "land_scarcity": _component(
            "land_scarcity",
            maxima["land_lot_title"]["land_scarcity"],
            record.lot_size_percentile_in_neighbourhood
            if record.lot_size_percentile_in_neighbourhood is not None
            or record.municipality not in set(config.get("geometry", {}).get("municipality_percentiles_required", []))
            else None,
            ["lot_size_percentile_in_neighbourhood"],
            lambda value: float(value) / 100,
            config,
            conflicts,
            "Local lot scarcity is a separate signal.",
        ),
        "buildability": _component(
            "buildability",
            maxima["land_lot_title"]["buildability"],
            record.buildability_score,
            ["buildability_score"],
            _score_from_percent,
            config,
            conflicts,
            "Buildability is not inferred from lot area alone.",
        ),
    }

    price_components = {
        "comps": _component(
            "comps",
            maxima["price_safety_margin"]["comps"],
            record.price_dislocation_pct,
            ["price_dislocation_pct"],
            _score_price_dislocation,
            config,
            conflicts,
            "Comparable-based price dislocation is preferred.",
        ),
        "assessment": _component(
            "assessment",
            maxima["price_safety_margin"]["assessment"],
            _score_assessment(record),
            ["asking", "assessment_total", "assessment_land"],
            lambda value: float(value),
            config,
            conflicts,
            "Assessment is a signal, not a valuation conclusion.",
        ),
        "land_value": _component(
            "land_value",
            maxima["price_safety_margin"]["land_value"],
            record.land_discount_or_premium_pct,
            ["land_discount_or_premium_pct"],
            _score_land_value,
            config,
            conflicts,
            "Land value proxy is compared with like-for-like land.",
        ),
        "dom_pressure": _component(
            "dom_pressure",
            maxima["price_safety_margin"]["dom_pressure"],
            _score_dom(record),
            ["dom", "price_reduction_pct"],
            lambda value: float(value),
            config,
            conflicts,
            "DOM and reduction history identify seller pressure.",
        ),
        "capital_efficiency": _compound_component(
            "capital_efficiency",
            maxima["price_safety_margin"]["capital_efficiency"],
            {"asking": record.asking, "lot_area_sqft": record.lot_area_sqft},
            config,
            conflicts,
            lambda values: (
                _clamp(1 - (float(values["asking"]) / max(float(values["lot_area_sqft"]), 1)) / 500)
                if values.get("asking") is not None and values.get("lot_area_sqft") is not None
                else None
            ),
            "Capital efficiency is contextualized by lot exposure.",
        ),
    }

    current_components = {
        "finished_space": _component(
            "finished_space",
            maxima["current_house_utility"]["finished_space"],
            record.finished_sqft,
            ["finished_sqft"],
            _score_finished_space,
            config,
            conflicts,
            "Finished space supports current family use.",
        ),
        "bed_bath": _compound_component(
            "bed_bath",
            maxima["current_house_utility"]["bed_bath"],
            {"bedrooms": record.bedrooms, "bathrooms": record.bathrooms},
            config,
            conflicts,
            _score_bed_bath,
            "Bedroom and bathroom usability is evaluated together.",
        ),
        "layout": _component(
            "layout",
            maxima["current_house_utility"]["layout"],
            record.current_house_utility_score,
            ["current_house_utility_score"],
            _score_from_percent,
            config,
            conflicts,
            "Current layout quality is evidence-backed when supplied.",
        ),
        "basement": _component(
            "basement",
            maxima["current_house_utility"]["basement"],
            record.basement,
            ["basement"],
            _score_basement,
            config,
            conflicts,
            "Basement condition and utility are separated from lot value.",
        ),
        "systems_structure": _component(
            "systems_structure",
            maxima["current_house_utility"]["systems_structure"],
            record.foundation or record.electrical or record.plumbing,
            ["foundation", "electrical", "plumbing"],
            lambda value: 0 if any(term in _enum_text(value) for term in ("issue", "old", "knob", "galvanized")) else 0.75,
            config,
            conflicts,
            "Structural and major systems risks are explicit.",
        ),
        "renovation_efficiency": _component(
            "renovation_efficiency",
            maxima["current_house_utility"]["renovation_efficiency"],
            record.renovation_estimate,
            ["renovation_estimate"],
            lambda value: _clamp(1 - float(value) / 300000),
            config,
            conflicts,
            "Renovation is evaluated as a five-to-ten-year use strategy.",
        ),
        "effective_age": _component(
            "effective_age",
            maxima["current_house_utility"]["effective_age"],
            _score_age(record, as_of=as_of),
            ["effective_year", "year_built"],
            lambda value: float(value),
            config,
            conflicts,
            "Effective age is distinct from original year built.",
        ),
        "suite_flexibility": _compound_component(
            "suite_flexibility",
            maxima["current_house_utility"]["suite_flexibility"],
            {"separate_entrance": record.separate_entrance, "kitchens": record.kitchens},
            config,
            conflicts,
            lambda values: (0.6 if values.get("separate_entrance") else 0) + (0.4 if values.get("kitchens", 0) >= 2 else 0),
            "Suite flexibility is not treated as approved use without official evidence.",
        ),
    }

    family_components = {
        "quietness": _component(
            "quietness",
            maxima["family_location_noise"]["quietness"],
            record.outdoor_noise_exposure,
            ["outdoor_noise_exposure"],
            _score_noise,
            config,
            conflicts,
            "Outdoor noise has a permanent family-living impact.",
        ),
        "outdoor_environment": _component(
            "outdoor_environment",
            maxima["family_location_noise"]["outdoor_environment"],
            record.slope,
            ["slope"],
            _score_slope,
            config,
            conflicts,
            "Outdoor environment includes terrain and permanent exposure.",
        ),
        "parks_daily_life": _component(
            "parks_daily_life",
            maxima["family_location_noise"]["parks_daily_life"],
            record.family_location_score,
            ["family_location_score"],
            _score_from_percent,
            config,
            conflicts,
            "Daily family location quality is explicit.",
        ),
        "transit_walkability": _component(
            "transit_walkability",
            maxima["family_location_noise"]["transit_walkability"],
            record.family_location_score,
            ["family_location_score"],
            _score_from_percent,
            config,
            conflicts,
            "Transit and walkability use the family location evidence.",
        ),
        "family_street": _component(
            "family_street",
            maxima["family_location_noise"]["family_street"],
            record.family_location_score,
            ["family_location_score"],
            _score_from_percent,
            config,
            conflicts,
            "Street quality is separate from school prestige.",
        ),
    }

    school_components = {
        "elementary_current": _component(
            "elementary_current",
            maxima["school"]["elementary_current"],
            {
                "current_school_score": record.current_school_score,
                "official_elementary_catchment": record.official_elementary_catchment,
            },
            ["current_school_score", "official_elementary_catchment"],
            lambda value: _score_school_with_catchment(value, "official_elementary_catchment"),
            config,
            conflicts,
            "Official elementary catchment and current score are explicit.",
        ),
        "secondary_current": _component(
            "secondary_current",
            maxima["school"]["secondary_current"],
            {
                "current_school_score": record.current_school_score,
                "official_secondary_catchment": record.official_secondary_catchment,
            },
            ["current_school_score", "official_secondary_catchment"],
            lambda value: _score_school_with_catchment(value, "official_secondary_catchment"),
            config,
            conflicts,
            "Secondary school evidence is separate from elementary.",
        ),
        "programs": _component(
            "programs",
            maxima["school"]["programs"],
            record.special_programs,
            ["special_programs"],
            lambda value: _clamp(len(value) / 4),
            config,
            conflicts,
            "Special programs are listed rather than inferred from rankings.",
        ),
        "capacity": _component(
            "capacity",
            maxima["school"]["capacity"],
            record.kindergarten_capacity or record.overflow,
            ["kindergarten_capacity", "overflow"],
            lambda value: 0.75 if "capacity" in _enum_text(value) or "available" in _enum_text(value) else 0.5,
            config,
            conflicts,
            "Capacity and overflow are explicit risk fields.",
        ),
        "improvement_trend": _component(
            "improvement_trend",
            maxima["school"]["improvement_trend"],
            record.capital_projects or record.seismic_upgrade,
            ["capital_projects", "seismic_upgrade"],
            lambda _: 0.75,
            config,
            conflicts,
            "School investment and capital projects affect outlook.",
        ),
        "community": _component(
            "community",
            maxima["school"]["community"],
            record.family_location_score,
            ["family_location_score"],
            _score_from_percent,
            config,
            conflicts,
            "Community is not reduced to Fraser ranking.",
        ),
        "five_year_outlook": _component(
            "five_year_outlook",
            maxima["school"]["five_year_outlook"],
            record.school_future_score,
            ["school_future_score"],
            _score_from_percent,
            config,
            conflicts,
            "Five-year school outlook is independent from current score.",
        ),
        "boundary_risk": _component(
            "boundary_risk",
            maxima["school"]["boundary_risk"],
            record.school_plan,
            ["school_plan"],
            lambda value: 0 if any(term in _enum_text(value) for term in ("risk", "change", "overflow")) else 0.75,
            config,
            conflicts,
            "Boundary risk is scored explicitly when a plan is known.",
        ),
    }

    redevelopment_components = {
        "current_entitlement": _component(
            "current_entitlement",
            maxima["redevelopment_option_value"]["current_entitlement"],
            _score_permitted(record),
            ["maximum_units", "permitted_uses"],
            lambda value: float(value),
            config,
            conflicts,
            "Current entitlement is not inferred from listing copy.",
        ),
        "duplex": _component(
            "duplex",
            maxima["redevelopment_option_value"]["duplex"],
            _score_duplex(record, scenarios),
            ["max_duplex_area", "duplex_area", "permitted_uses"],
            lambda value: float(value),
            config,
            conflicts,
            "Duplex potential includes area and legal-use evidence.",
        ),
        "multiplex": _component(
            "multiplex",
            maxima["redevelopment_option_value"]["multiplex"],
            _score_multiplex(record, scenarios),
            ["max_realistic_multiplex_units", "maximum_units", "multiplex_area"],
            lambda value: float(value),
            config,
            conflicts,
            "Multiplex potential is not maximum-unit maximization.",
        ),
        "buildable_area": _component(
            "buildable_area",
            maxima["redevelopment_option_value"]["buildable_area"],
            record.buildability_score or record.realistic_buildable_sqft,
            ["buildability_score", "realistic_buildable_sqft"],
            lambda value: _score_from_percent(value) if float(value) <= 100 else _clamp(float(value) / 6000),
            config,
            conflicts,
            "Realistic buildable area is separate from theoretical area.",
        ),
        "product_market_fit": _component(
            "product_market_fit",
            maxima["redevelopment_option_value"]["product_market_fit"],
            record.best_economic_product or record.buyer_pool_score,
            ["best_economic_product", "buyer_pool_score"],
            lambda value: _score_from_percent(value) if isinstance(value, (int, float)) else 0.75,
            config,
            conflicts,
            "Product-market fit considers family-oriented resale.",
        ),
        "toa_assembly": _compound_component(
            "toa_assembly",
            maxima["redevelopment_option_value"]["toa_assembly"],
            {"toa_status": record.toa_status or record.toa, "assembly_score": record.assembly_score},
            config,
            conflicts,
            lambda values: max(
                _score_from_percent(values["assembly_score"]) if values.get("assembly_score") is not None else 0,
                0.65 if values.get("toa_status") else 0,
            ),
            "TOA and assembly are long-term options, not automatic entitlement.",
        ),
        "execution": _component(
            "execution",
            maxima["redevelopment_option_value"]["execution"],
            _score_execution(record),
            ["slope", "heritage_status", "permanent_defects"],
            lambda value: float(value),
            config,
            conflicts,
            "Execution complexity reduces option value.",
        ),
    }

    resale_components = {
        "buyer_pool": _component(
            "buyer_pool",
            maxima["resale_affordability"]["buyer_pool"],
            record.buyer_pool_score,
            ["buyer_pool_score"],
            _score_from_percent,
            config,
            conflicts,
            "Future buyer pool is explicit.",
        ),
        "affordability": _component(
            "affordability",
            maxima["resale_affordability"]["affordability"],
            record.affordability_score or record.relative_affordability_index,
            ["affordability_score", "relative_affordability_index"],
            lambda value: _score_from_percent(value) if float(value) > 1 else _clamp(1.2 - float(value)),
            config,
            conflicts,
            "Affordability is relative to the local comparable market.",
        ),
        "liquidity": _component(
            "liquidity",
            maxima["resale_affordability"]["liquidity"],
            record.liquidity_score,
            ["liquidity_score"],
            _score_from_percent,
            config,
            conflicts,
            "Liquidity reflects the likely resale buyer pool.",
        ),
    }

    carrying_components = {
        "tax_utilities": _compound_component(
            "tax_utilities",
            maxima["carrying_cost_risk"]["tax_utilities"],
            {"tax": record.tax, "utilities": record.utilities},
            config,
            conflicts,
            lambda values: _clamp(1 - (float(values.get("tax", 0)) + float(values.get("utilities", 0))) / 20000),
            "Municipal carrying cost is separated by municipality.",
        ),
        "maintenance": _component(
            "maintenance",
            maxima["carrying_cost_risk"]["maintenance"],
            record.renovation_estimate,
            ["renovation_estimate"],
            lambda value: _clamp(1 - float(value) / 300000),
            config,
            conflicts,
            "Maintenance and renovation burden are visible.",
        ),
        "title_legal": _component(
            "title_legal",
            maxima["carrying_cost_risk"]["title_legal"],
            None if record.ownership_type in {
                OwnershipType.UNKNOWN,
                OwnershipType.PARTIAL_INTEREST,
                OwnershipType.UNDIVIDED_INTEREST,
                OwnershipType.FRACTIONAL_OWNERSHIP,
            } else record.ownership_type,
            ["ownership_type", "title_status"],
            lambda _: 1,
            config,
            conflicts,
            "Title and legal risk are not inferred.",
        ),
        "permanent_defects": _component(
            "permanent_defects",
            maxima["carrying_cost_risk"]["permanent_defects"],
            record.permanent_defects if record.permanent_defects else None,
            ["permanent_defects"],
            lambda value: _clamp(1 - len(value) * 0.25),
            config,
            conflicts,
            "Permanent defects are discounted separately from fixable defects.",
        ),
        "data_quality": _component(
            "data_quality",
            maxima["carrying_cost_risk"]["data_quality"],
            0 if record.data_conflict else (1 if record.data_confidence_score is not None else None),
            ["data_confidence_score", "data_conflict"],
            lambda value: _clamp(float(value)),
            config,
            conflicts,
            "Data quality is separate from investment confidence.",
        ),
    }

    return {
        "land_lot_title": _dimension("land_lot_title", land_components),
        "price_safety_margin": _dimension("price_safety_margin", price_components),
        "current_house_utility": _dimension("current_house_utility", current_components),
        "family_location_noise": _dimension("family_location_noise", family_components),
        "school": _dimension("school", school_components),
        "redevelopment_option_value": _dimension("redevelopment_option_value", redevelopment_components),
        "resale_affordability": _dimension("resale_affordability", resale_components),
        "carrying_cost_risk": _dimension("carrying_cost_risk", carrying_components),
    }


def _apply_conflicts_to_evidence(
    evidence_items: list[EvidenceItem],
    conflicts: set[str],
) -> list[EvidenceItem]:
    if not conflicts:
        return evidence_items
    evidence_fields = {
        "official_pid_confirmed": {"pid", "PID", "legal_description"},
        "ownership_confirmed": {"ownership_type", "tenure", "ownership"},
        "official_assessment": {
            "assessment_year",
            "assessment_total",
            "assessment_land",
            "assessment_improvement",
        },
        "lot_dimensions_confirmed": {
            "lot_area_sqft",
            "lot_area_m2",
            "frontage_ft",
            "depth_ft",
        },
        "official_zoning": {
            "zoning",
            "maximum_fsr",
            "maximum_units",
            "minimum_frontage",
        },
        "approved_use_confirmed": {"approved_use", "current_approved_use"},
        "listing_history_confirmed": {
            "asking",
            "original_asking",
            "listing_status",
            "listing_history",
        },
        "sold_history_cross_checked": {
            "previous_sale_prices",
            "previous_sale_dates",
            "sold_history",
        },
        "official_school_catchment": {
            "official_elementary_catchment",
            "official_secondary_catchment",
            "school_catchment",
        },
        "road_location_verified": {
            "road_class",
            "distance_to_major_road_m",
            "road_location",
        },
    }
    affected = {
        evidence_name
        for evidence_name, field_names in evidence_fields.items()
        if conflicts & field_names
    }
    return [
        item.model_copy(update={"conflict": True}) if item.name in affected else item
        for item in evidence_items
    ]


def calculate_independent_scores(dimension_points: dict[str, float]) -> dict[str, float]:
    normalized = {
        name: _clamp(float(dimension_points.get(name, 0)) / DIMENSION_MAXIMA[name]) * 100
        for name in DIMENSION_MAXIMA
    }
    land_score = normalized["land_lot_title"] * 0.5 + normalized["redevelopment_option_value"] * 0.5
    live_score = (
        normalized["current_house_utility"] * 0.45
        + normalized["family_location_noise"] * 0.30
        + normalized["school"] * 0.25
    )
    investment_score = (
        normalized["price_safety_margin"] * 0.30
        + land_score * 0.35
        + normalized["resale_affordability"] * 0.15
        + normalized["carrying_cost_risk"] * 0.15
        + normalized["current_house_utility"] * 0.05
    )
    return {
        "live_score": round(live_score, 2),
        "land_score": round(land_score, 2),
        "investment_score": round(investment_score, 2),
    }


def calculate_land_to_product_score(
    record: PropertyRecord,
    scenarios: Iterable[RedevelopmentScenario],
) -> float:
    """Estimate how efficiently the land can become a marketable product.

    This is an option-quality index, not a legal entitlement or appraisal. An
    unknown input contributes a neutral provisional value and remains visible
    through the surrounding score/confidence fields.
    """

    scenario_records = list(scenarios)
    buildability = record.buildability_score
    if buildability is None and record.realistic_buildable_sqft is not None:
        buildability = _clamp(record.realistic_buildable_sqft / 6000) * 100
    buildability = 50 if buildability is None else float(buildability)
    product_fits = [
        float(item.product_market_fit)
        for item in scenario_records
        if item.product_market_fit is not None and item.unit_count is not None
    ]
    product_fit = max(product_fits, default=record.buyer_pool_score or 50)
    buyer_pool = record.buyer_pool_score if record.buyer_pool_score is not None else 50
    legal_options = [
        item for item in scenario_records
        if item.unit_count is not None and item.scenario_type in {
            ScenarioType.DUPLEX,
            ScenarioType.MAX_REALISTIC_MULTIPLEX,
        }
    ]
    legal_support = 100 if any(item.legal_status == "PERMITTED_BY_OFFICIAL_BYLAW" for item in legal_options) else 50
    return round(
        _clamp(
            0.40 * float(buildability) / 100
            + 0.30 * float(product_fit) / 100
            + 0.20 * float(buyer_pool) / 100
            + 0.10 * legal_support / 100
        )
        * 100,
        2,
    )


def calculate_future_opportunity_cost(
    existing_property_market_value: int | float | None,
    new_finished_market_value: int | float | None,
    redevelopment_cost: int | float | None,
) -> int | None:
    """Return uplift over the property value that would otherwise be retained.

    This intentionally uses value at the redevelopment date, not the original
    purchase price.  Missing inputs remain unknown rather than becoming a
    fabricated feasibility result.
    """

    if (
        existing_property_market_value is None
        or new_finished_market_value is None
        or redevelopment_cost is None
    ):
        return None
    return round(float(new_finished_market_value) - float(existing_property_market_value) - float(redevelopment_cost))


def _scenario_product_score(scenario: RedevelopmentScenario) -> float:
    """Conservative product score used to compare products, not unit counts."""

    if scenario.unit_count is None or scenario.unit_count < 2:
        return -1
    legal = {
        "PERMITTED_BY_OFFICIAL_BYLAW": 1.0,
        "CURRENT_USE": 0.8,
        "UNKNOWN_REQUIRES_OFFICIAL_REVIEW": 0.35,
        "EVIDENCE_REQUIRES_OFFICIAL_REVIEW": 0.35,
    }.get(scenario.legal_status, 0.0)
    fit = _score_from_percent(scenario.product_market_fit) if scenario.product_market_fit is not None else 0.5
    execution = (
        _clamp(1 - float(scenario.execution_complexity) / 100)
        if scenario.execution_complexity is not None
        else 0.5
    )
    if scenario.average_unit_size is not None:
        size_fit = _clamp(1 - abs(float(scenario.average_unit_size) - 1450) / 1450)
    else:
        size_fit = 0.5
    if scenario.estimated_resale_price is not None and scenario.total_project_cost is not None:
        net_margin = float(scenario.estimated_resale_price - scenario.total_project_cost)
        economics = _clamp(net_margin / 3_000_000)
    else:
        economics = 0.5
    return 0.25 * legal + 0.35 * fit + 0.2 * execution + 0.1 * size_fit + 0.1 * economics


def select_best_economic_product(
    scenarios: Iterable[RedevelopmentScenario],
) -> ScenarioType | None:
    """Choose the best supported product by fit, economics, and execution risk."""

    candidates = [
        scenario
        for scenario in scenarios
        if scenario.scenario_type is not ScenarioType.KEEP_EXISTING_HOUSE
        and scenario.scenario_type is not ScenarioType.TOA_OR_ASSEMBLY
        and scenario.unit_count is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda scenario: (_scenario_product_score(scenario), -scenario.unit_count, scenario.scenario_type.value),
    ).scenario_type


def _scenario_costs(
    realistic_area: float | None,
    config: dict[str, Any],
) -> dict[str, int | None]:
    if realistic_area is None:
        return {
            "hard_cost": None,
            "soft_cost": None,
            "developer_specific_cost": None,
            "contingency": None,
            "total_project_cost": None,
        }
    redevelopment_cfg = config.get("redevelopment", {})
    hard_range = redevelopment_cfg.get("hard_cost_per_sqft")
    if not isinstance(hard_range, (list, tuple)) or len(hard_range) != 2:
        return {
            "hard_cost": None,
            "soft_cost": None,
            "developer_specific_cost": None,
            "contingency": None,
            "total_project_cost": None,
        }
    hard_cost = round(float(realistic_area) * ((float(hard_range[0]) + float(hard_range[1])) / 2))
    soft_cost = round(hard_cost * float(redevelopment_cfg.get("soft_cost_pct_of_hard", 0)))
    developer_cost = round(hard_cost * float(redevelopment_cfg.get("developer_specific_cost_pct_of_hard", 0)))
    contingency = round(hard_cost * float(redevelopment_cfg.get("contingency_pct_of_hard", 0)))
    return {
        "hard_cost": hard_cost,
        "soft_cost": soft_cost,
        "developer_specific_cost": developer_cost,
        "contingency": contingency,
        "total_project_cost": hard_cost + soft_cost + developer_cost + contingency,
    }


def _tags(record: PropertyRecord, dimensions: dict[str, DimensionScore]) -> list[str]:
    tags: list[str] = []
    if record.research_scope == "HOUSE_REBUILD_ONLY":
        tags.extend(["HOUSE_REBUILD_ONLY", "NO_STANDARD_DUPLEX_OPTION"])
    elif record.research_scope == "CURRENT_USE_ONLY":
        tags.append("CURRENT_USE_ONLY")
    elif record.research_scope == "STANDARD_REDEVELOPMENT":
        tags.append("STANDARD_REDEVELOPMENT_LOT")
    if record.lot_area_sqft is not None and record.lot_area_sqft < 3000:
        tags.append("SMALL_LOT")
    labels = set(record.assessment_labels)
    if "LAND_DOMINANT" in labels or record.land_value_proxy is not None:
        tags.append("LAND_VALUE")
    if "ASK_BELOW_ASSESSMENT" in labels or record.assessment_total and record.asking and record.asking < record.assessment_total:
        tags.append("ASSESSMENT_DISCOUNT")
    if "ASK_BELOW_LAND_ASSESSMENT" in labels or record.assessment_land and record.asking and record.asking < record.assessment_land:
        tags.append("LAND_ASSESSMENT_DISCOUNT")
    if record.price_dislocation_pct is not None and record.price_dislocation_pct <= -0.05:
        tags.append("PRICE_DISLOCATION")
    if record.lot_area_sqft is not None and record.lot_area_sqft >= 6000:
        tags.append("LARGE_LOT")
    if record.frontage_ft is not None and record.frontage_ft >= 44:
        tags.append("STRONG_FRONTAGE")
    if _official_zoning_supported(record):
        tags.extend(record.zoning_threshold_advantages)
    if _official_zoning_supported(record) and record.ownership_type is OwnershipType.FREEHOLD_NON_STRATA and (
        record.realistic_buildable_sqft or record.max_duplex_area or record.max_realistic_multiplex_units
    ):
        tags.append("LIVE_NOW_DEVELOP_LATER")
    if _official_zoning_supported(record) and (
        record.duplex_area
        or record.max_duplex_area
        or any("duplex" in str(item).lower() for item in record.permitted_uses)
    ):
        tags.append("DUPLEX_OPTION")
    if _official_zoning_supported(record) and (
        record.multiplex_area
        or record.max_realistic_multiplex_units
        or (record.maximum_units or 0) >= 3
    ):
        tags.append("MULTIPLEX_OPTION")
    if record.toa or record.toa_status:
        tags.append("TOA_OPTION")
    if record.assembly_score is not None:
        tags.append("ASSEMBLY_OPTION")
    if record.character_status or record.heritage_status or record.infill_options:
        tags.append("CHARACTER_OPTION")
    if record.fixable_defects:
        tags.append("FIXER")
    if record.permanent_defects:
        tags.append("PERMANENT_DEFECT")
    if record.outdoor_noise_exposure is not None and record.outdoor_noise_exposure >= 50:
        tags.append("NOISE_RISK")
    if record.ownership_type in {
        OwnershipType.PARTIAL_INTEREST,
        OwnershipType.UNDIVIDED_INTEREST,
        OwnershipType.FRACTIONAL_OWNERSHIP,
        OwnershipType.UNKNOWN,
    }:
        tags.append("TITLE_RISK")
    if record.data_conflict:
        tags.append("DATA_CONFLICT")
    return list(dict.fromkeys(tags))


def build_baseline_scenarios(
    record: PropertyRecord,
    config: dict[str, Any] | None = None,
    *,
    official_zoning_confirmed: bool | None = None,
) -> list[RedevelopmentScenario]:
    cfg = config or {}
    official_zoning = (
        bool(official_zoning_confirmed) and _official_zoning_supported(record)
        if official_zoning_confirmed is not None
        else _official_zoning_supported(record)
    )
    theoretical = (
        record.lot_area_sqft * record.maximum_fsr
        if record.lot_area_sqft is not None and record.maximum_fsr is not None
        else None
    )
    realistic = record.realistic_buildable_sqft
    if realistic is None and theoretical is not None:
        realistic = round(theoretical * 0.82, 2)
    uses = " ".join(str(item).lower() for item in record.permitted_uses)
    duplex_legally_supported = official_zoning and (
        (record.maximum_units or 0) >= 2
        or "duplex" in uses
        or record.approved_use.value == "TWO_FAMILY_DWELLING"
    )
    multiplex_legally_supported = official_zoning and (
        (record.maximum_units or 0) >= 3
        or (record.max_realistic_multiplex_units or 0) >= 3
        or "multiple" in uses
        or "multiplex" in uses
    )
    legal_status = "PERMITTED_BY_OFFICIAL_BYLAW" if official_zoning else "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    duplex_area = record.max_duplex_area or realistic
    if duplex_area is not None:
        duplex_area = round(min(duplex_area, 4500), 2)
    multiplex_units = record.max_realistic_multiplex_units if multiplex_legally_supported else None
    if multiplex_units is None and multiplex_legally_supported and record.maximum_units is not None:
        multiplex_units = min(record.maximum_units, 6)
    road_class = record.road_class.value if isinstance(record.road_class, RoadClass) else str(record.road_class or "")
    front_building_noise_buffer = road_class in {"A", "B", "C"} or record.property_fronts_major_road is True
    saleable_efficiency = float(cfg.get("redevelopment", {}).get("saleable_efficiency", 0.9))
    duplex_costs = _scenario_costs(duplex_area, cfg)
    multiplex_costs = _scenario_costs(realistic, cfg)
    duplex_average_unit_size = round(duplex_area / 2, 2) if duplex_area is not None else None
    multiplex_average_unit_size = (
        round(realistic * saleable_efficiency / multiplex_units, 2)
        if realistic is not None and multiplex_units
        else None
    )
    duplex_unit_size_range = cfg.get("redevelopment", {}).get("family_duplex_unit_size_sqft", [1250, 1800])
    duplex_fit = (
        95
        if duplex_average_unit_size is not None
        and duplex_unit_size_range[0] <= duplex_average_unit_size <= duplex_unit_size_range[1]
        else 70
        if duplex_average_unit_size is not None
        else None
    )
    multiplex_fit = (
        85
        if multiplex_average_unit_size is not None and multiplex_average_unit_size >= 1000
        else 65
        if multiplex_average_unit_size is not None
        else None
    )
    configured_units = cfg.get("redevelopment", {}).get("scenario_unit_range", [2, 3, 4, 5, 6])
    product_options: list[dict[str, Any]] = []
    if multiplex_units is not None and realistic is not None and multiplex_legally_supported:
        for unit_count in configured_units:
            unit_count = int(unit_count)
            if unit_count < 2 or unit_count > multiplex_units or unit_count > 6:
                continue
            average_unit_size = round(realistic * saleable_efficiency / unit_count, 2)
            size_fit = (
                85
                if 1000 <= average_unit_size <= 1800
                else 65
                if average_unit_size >= 850
                else 45
            )
            execution = min(100, 35 + (unit_count - 2) * 7)
            product_options.append({
                "unit_count": unit_count,
                "total_buildable_sqft": theoretical,
                "realistic_buildable_sqft": realistic,
                "estimated_saleable_sqft": round(realistic * saleable_efficiency, 2),
                "average_unit_size": average_unit_size,
                "product_market_fit": size_fit,
                "execution_complexity": execution,
                "hard_cost": multiplex_costs["hard_cost"],
                "soft_cost": multiplex_costs["soft_cost"],
                "developer_specific_cost": multiplex_costs["developer_specific_cost"],
                "contingency": multiplex_costs["contingency"],
                "total_project_cost": multiplex_costs["total_project_cost"],
            })
    best_product_option = max(
        product_options,
        key=lambda option: (
            option["product_market_fit"] / 100,
            1 - option["execution_complexity"] / 100,
            -abs(option["average_unit_size"] - 1450),
            -option["unit_count"],
        ),
        default=None,
    )
    scenarios = [
        RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.KEEP_EXISTING_HOUSE,
            legal_status="CURRENT_USE",
            zoning_basis=record.zoning,
            bylaw_version=record.bylaw_version,
            unit_count=1,
            realistic_buildable_sqft=record.finished_sqft,
            estimated_saleable_sqft=record.finished_sqft,
            likely_bedrooms=str(record.bedrooms) if record.bedrooms is not None else None,
            likely_buyer="family",
            assumptions=["Existing use must be confirmed by official records."],
        ),
        RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.DUPLEX,
            legal_status=legal_status,
            zoning_basis=record.zoning,
            bylaw_version=record.bylaw_version,
            unit_count=2 if official_zoning and duplex_area is not None else None,
            total_buildable_sqft=theoretical,
            realistic_buildable_sqft=duplex_area,
            estimated_saleable_sqft=round(duplex_area * saleable_efficiency, 2) if duplex_area is not None else None,
            average_unit_size=duplex_average_unit_size,
            likely_bedrooms="3–4",
            likely_buyer="family-oriented half duplex buyer",
            layout_options=["front/rear", "side-by-side"],
            front_building_noise_buffer_hypothesis=front_building_noise_buffer,
            estimated_front_unit_area=duplex_average_unit_size,
            estimated_rear_unit_area=duplex_average_unit_size,
            hard_cost=duplex_costs["hard_cost"],
            soft_cost=duplex_costs["soft_cost"],
            developer_specific_cost=duplex_costs["developer_specific_cost"],
            contingency=duplex_costs["contingency"],
            total_project_cost=duplex_costs["total_project_cost"],
            product_market_fit=duplex_fit,
            execution_complexity=35 if duplex_area is not None else None,
            assumptions=[
                "Front/rear and side-by-side layouts require design and bylaw review.",
                "A front building may buffer a rear unit only when road exposure and site design support it.",
            ],
        ),
        RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.MAX_REALISTIC_MULTIPLEX,
            legal_status=legal_status,
            zoning_basis=record.zoning,
            bylaw_version=record.bylaw_version,
            unit_count=multiplex_units,
            total_buildable_sqft=theoretical,
            realistic_buildable_sqft=realistic,
            estimated_saleable_sqft=round(realistic * saleable_efficiency, 2) if realistic is not None else None,
            average_unit_size=multiplex_average_unit_size,
            likely_bedrooms="2–3",
            likely_buyer="family or downsizer buyer pool",
            hard_cost=multiplex_costs["hard_cost"],
            soft_cost=multiplex_costs["soft_cost"],
            developer_specific_cost=multiplex_costs["developer_specific_cost"],
            contingency=multiplex_costs["contingency"],
            total_project_cost=multiplex_costs["total_project_cost"],
            product_market_fit=multiplex_fit,
            execution_complexity=(35 + max((multiplex_units or 2) - 2, 0) * 7) if multiplex_units is not None else None,
            product_options=product_options,
            best_product_option=best_product_option,
            assumptions=[
                "Maximum legal units are not automatically the best product.",
                "Compare two-, three-, four-, five-, and six-unit products only after official entitlement is confirmed.",
            ],
        ),
    ]
    if record.character_status or record.heritage_register or record.infill_options:
        scenarios.append(RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.CHARACTER_HOUSE_INFILL,
            legal_status="REVIEW_REQUIRED",
            zoning_basis=record.zoning,
            bylaw_version=record.bylaw_version,
            assumptions=["Retention and infill incentives require official confirmation."],
        ))
    if record.toa or record.toa_status or record.assembly_score is not None:
        scenarios.append(RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.TOA_OR_ASSEMBLY,
            legal_status="LONG_TERM_OPTION_ONLY",
            zoning_basis=record.current_zoning or record.zoning,
            bylaw_version=record.bylaw_version,
            assumptions=["TOA/assembly does not establish automatic entitlement."],
        ))
    return scenarios


def score_property(
    record: PropertyRecord,
    config: dict[str, Any],
    *,
    evidence: Iterable[EvidenceItem] | None = None,
    conflicts: Iterable[str] | None = None,
    scenarios: list[RedevelopmentScenario] | None = None,
    as_of: datetime | None = None,
    generated_at: datetime | None = None,
) -> ScoreSnapshot:
    evidence_items = list(evidence) if evidence is not None else default_evidence_items()
    conflict_fields = set(conflicts or ())
    evidence_items = _apply_conflicts_to_evidence(evidence_items, conflict_fields)
    confidence_thresholds = config.get("scoring", {}).get("confidence_thresholds", {})
    confidence = calculate_data_confidence(
        evidence_items,
        high_threshold=float(confidence_thresholds.get("high", 85)),
        medium_threshold=float(confidence_thresholds.get("medium", 70)),
    )
    scoring_record = record.model_copy(
        update={
            "data_confidence_score": confidence.score,
            "data_confidence_band": confidence.band,
        }
    )
    scenario_records = scenarios or build_baseline_scenarios(record, config)
    dimensions = _build_dimensions(
        scoring_record,
        config,
        conflict_fields,
        scenario_records,
        as_of=as_of,
    )
    independent = calculate_independent_scores({name: item.points for name, item in dimensions.items()})
    land_to_product = calculate_land_to_product_score(scoring_record, scenario_records)
    total = round(sum(item.points for item in dimensions.values()), 2)
    unknown_fields = sorted({
        field
        for dimension in dimensions.values()
        for component in dimension.components.values()
        for field in component.unknown_fields
    })
    reasons: list[str] = []
    risks: list[str] = []
    for dimension in dimensions.values():
        for component in dimension.components.values():
            if component.evidence_status is EvidenceStatus.CONFIRMED:
                reasons.append(component.explanation)
            elif component.evidence_status is EvidenceStatus.CONFLICT:
                risks.append(component.explanation)
    tags = _tags(scoring_record, dimensions)
    return ScoreSnapshot(
        property_id=record.property_id,
        generated_at=generated_at or datetime.now(timezone.utc),
        scoring_version=config.get("scoring", {}).get("version", "research-v1"),
        config_hash=_config_hash(config),
        total_score=total,
        total_score_label=_score_label(
            total,
            100,
            (
                component.evidence_status
                for dimension in dimensions.values()
                for component in dimension.components.values()
            ),
        ),
        live_score=independent["live_score"],
        land_score=independent["land_score"],
        investment_score=independent["investment_score"],
        land_to_product_score=land_to_product,
        data_confidence_score=confidence.score,
        data_confidence_band=confidence.band,
        dimensions=dimensions,
        reasons=list(dict.fromkeys(reasons)),
        risks=list(dict.fromkeys(risks)),
        unknown_fields=unknown_fields,
        opportunity_tags=tags,
    )
