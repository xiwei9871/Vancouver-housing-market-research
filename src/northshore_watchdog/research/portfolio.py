"""Portfolio-level, auditable research reports for the saved listing universe.

This module is intentionally a reporting and rule-projection layer.  It does
not replace the screening engine.  A report keeps three things separate:

* facts observed from the listing or official enrichment;
* calculations derived from official rules or local configuration; and
* estimates which still require inspection, a survey, an architect, or a
  human transaction review.

The functions are deliberately usable with both Pydantic models and plain
mappings so that old JSON artifacts can be replayed without a migration.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
import json
import math
import re
from typing import Any

import yaml

from .discovery import DiscoveryCard
from .screening import ScreeningDecision


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "vancouver-zoning-rules-2026-09-07.yaml"
DEFAULT_RESEARCH_CONFIG_PATH = PROJECT_ROOT / "config" / "research.yaml"

OFFICIAL_MISSING_FIELDS = ("bca", "zoning", "road", "school_catchment", "lot_geometry")
OPTIONAL_MISSING_FIELDS = ("school_performance", "sold_history", "listing_history", "approved_use")


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _raw(card: Any) -> Mapping[str, Any]:
    value = _get(card, "raw", {})
    return value if isinstance(value, Mapping) else {}


def _first(card: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = _get(card, name, None)
        if value not in (None, "", [], {}):
            return value
        value = _raw(card).get(name)
        if value not in (None, "", [], {}):
            return value
    return default


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _dimensions(value: Any) -> tuple[float | None, float | None]:
    """Parse a visible listing dimension without treating it as official."""

    if not _present(value):
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|×|by)\s*(\d+(?:\.\d+)?)", str(value), re.IGNORECASE)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def _present(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().upper() in {"UNKNOWN", "N/A", "NA", "NULL", "NONE"}:
        return False
    return True


def _official(card: Any, *names: str) -> Any:
    raw = _raw(card)
    for name in names:
        value = raw.get(name)
        if _present(value):
            return value
    return None


def _has_successful_official_evidence(
    card: Any,
    *adapters: str,
    field_names: Sequence[str] = (),
) -> bool:
    """Require a successful, traceable official observation for a field group."""

    evidence = _raw(card).get("_official_evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return False
    wanted = {str(adapter).strip().lower() for adapter in adapters}
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        adapter = str(item.get("adapter") or item.get("source_registry_id") or "").strip().lower()
        status = str(item.get("status") or "").strip().upper()
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
        if (
            adapter in wanted
            and status in {"SUCCESS", "CONFIRMED", "COMPLETED"}
            and _present(item.get("source_url"))
            and _present(item.get("retrieved_at"))
            and all(_present(fields.get(name)) for name in field_names)
        ):
            return True
    return False


def property_key(card: Any) -> str:
    """Return the stable listing identity, preferring MLS over address."""

    mls = _first(card, "mls_number", "mls", "listing_id")
    if _present(mls):
        return f"mls:{str(mls).strip()}"
    address = str(_first(card, "address", default="") or "").strip().upper()
    address = " ".join(address.split())
    return f"address:{address}"


def _official_complete(card: Any, field: str) -> bool:
    if field == "bca":
        return _has_successful_official_evidence(
            card,
            "bca-assessment",
            field_names=("assessment_total", "assessment_land", "assessment_improvement"),
        ) and all(_present(_official(card, name)) for name in (
            "official_assessment_total",
            "official_assessment_land",
            "official_assessment_improvement",
        ))
    if field == "zoning":
        return _has_successful_official_evidence(card, "municipal-zoning", field_names=("zoning",)) and _present(_official(card, "official_current_zoning", "official_zoning"))
    if field == "road":
        return _has_successful_official_evidence(card, "municipal-road", field_names=("road_class", "distance_to_major_road_m")) and (
            _present(_official(card, "official_road_class"))
            and _number(_official(card, "official_distance_to_major_road_m")) is not None
        )
    if field == "school_catchment":
        return _has_successful_official_evidence(card, "school-catchment", field_names=("official_elementary_catchment", "official_secondary_catchment")) and all(_present(_official(card, name)) for name in (
            "official_elementary_catchment",
            "official_secondary_catchment",
        ))
    if field == "lot_geometry":
        return _has_successful_official_evidence(card, "bca-assessment", "property-geometry", field_names=("frontage_ft", "depth_ft")) and all(_number(_official(card, name)) is not None for name in (
            "official_frontage_ft",
            "official_depth_ft",
        ))
    raise KeyError(field)


def _official_sale_history(card: Any) -> dict[str, Any] | None:
    """Return the saved BCA transaction observation, including a no-sale result."""

    evidence = _raw(card).get("_official_evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return None
    for item in reversed(evidence):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("adapter") or "").strip().lower() != "bca-assessment":
            continue
        if str(item.get("status") or "").strip().upper() not in {"SUCCESS", "CONFIRMED", "COMPLETED"}:
            continue
        if not (_present(item.get("source_url")) and _present(item.get("retrieved_at"))):
            continue
        query_scope = str(item.get("query_scope") or "").strip()
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
        prices = fields.get("previous_sale_prices")
        dates = fields.get("previous_sale_dates")
        price_values = list(prices) if isinstance(prices, Sequence) and not isinstance(prices, (str, bytes)) else []
        date_values = list(dates) if isinstance(dates, Sequence) and not isinstance(dates, (str, bytes)) else []
        if price_values or date_values:
            count = max(len(price_values), len(date_values))
            sales = [
                {
                    "sale_date": date_values[index] if index < len(date_values) else None,
                    "sale_price": price_values[index] if index < len(price_values) else None,
                }
                for index in range(count)
            ]
            return {
                "status": "OFFICIAL_RECORDED_SALE",
                "query_scope": query_scope or "UNKNOWN",
                "sales": sales,
                "source_url": item.get("source_url"),
                "retrieved_at": item.get("retrieved_at"),
            }
        if query_scope:
            return {
                "status": "CHECKED_NO_RECORDED_SALE_IN_WINDOW",
                "query_scope": query_scope,
                "sales": [],
                "source_url": item.get("source_url"),
                "retrieved_at": item.get("retrieved_at"),
            }
    return None


def _realtor_price_sale_history(card: Any) -> dict[str, Any] | None:
    """Return explicit price/history rows rendered by a REALTOR.ca detail page."""

    events = _first(card, "realtor_price_sale_history", default=[])
    status = _first(card, "realtor_history_status")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        events = []
    if not events and not _present(status):
        return None
    return {
        "status": str(status or "RECORDED").strip(),
        "events": list(events),
    }


def _optional_present(card: Any, field: str) -> bool:
    raw = _raw(card)
    if field == "school_performance":
        return any(_present(raw.get(name)) for name in (
            "official_elementary_performance",
            "official_secondary_performance",
            "official_school_performance",
        ))
    if field == "sold_history":
        return (
            _official_sale_history(card) is not None
            or _realtor_price_sale_history(card) is not None
            or bool(_first(card, "previous_sale_prices", default=[]))
            or bool(raw.get("official_sale_history"))
        )
    if field == "listing_history":
        return any(_present(_first(card, name)) for name in (
            "original_asking", "listing_date", "dom", "cumulative_dom", "previous_listing_prices",
        ))
    if field == "approved_use":
        return _present(_official(card, "official_approved_use", "approved_use"))
    raise KeyError(field)


def audit_portfolio(cards: Iterable[Any]) -> dict[str, Any]:
    """Audit official and optional coverage without treating guesses as facts."""

    materialized = list(cards)
    missing_official = {
        field: sum(not _official_complete(card, field) for card in materialized)
        for field in OFFICIAL_MISSING_FIELDS
    }
    optional_missing = {
        field: sum(not _optional_present(card, field) for card in materialized)
        for field in OPTIONAL_MISSING_FIELDS
    }
    zoning_codes = Counter(
        str(_official(card, "official_current_zoning", "official_zoning") or "UNKNOWN").strip().upper()
        for card in materialized
    )
    special_or_excluded = Counter()
    sold_history_status = Counter()
    realtor_history_status = Counter()
    geometry_source_counts = Counter()
    for card in materialized:
        status = str(_first(card, "listing_status", default="ACTIVE") or "ACTIVE").upper()
        ownership = str(_first(card, "ownership_type", default="") or "").upper()
        if any(token in ownership for token in ("LEASE", "PARTIAL", "FRACTIONAL", "COURT")):
            special_or_excluded["special_transaction_or_title"] += 1
        if status not in {"ACTIVE", "FOR SALE", "AVAILABLE", "UNKNOWN"}:
            special_or_excluded["non_active_listing"] += 1
        sale_history = _official_sale_history(card)
        sold_history_status[
            str((sale_history or {}).get("status") or "NOT_CHECKED")
        ] += 1
        realtor_history = _realtor_price_sale_history(card)
        realtor_history_status[
            str((realtor_history or {}).get("status") or "NOT_PRESENT")
        ] += 1
        if _official_complete(card, "lot_geometry"):
            geometry_source_counts["OFFICIAL_BCA"] += 1
        elif _number(_first(card, "frontage_ft")) is not None and _number(_first(card, "depth_ft")) is not None:
            geometry_source_counts["LISTING_OBSERVED"] += 1
        else:
            geometry_source_counts["UNKNOWN"] += 1
    return {
        "property_count": len(materialized),
        "missing_official": missing_official,
        "official_coverage": {field: len(materialized) - count for field, count in missing_official.items()},
        "optional_missing": optional_missing,
        "optional_coverage": {field: len(materialized) - count for field, count in optional_missing.items()},
        "zoning_codes": dict(sorted(zoning_codes.items())),
        "special_or_excluded": dict(sorted(special_or_excluded.items())),
        "sold_history_status": dict(sorted(sold_history_status.items())),
        "realtor_history_status": dict(sorted(realtor_history_status.items())),
        "geometry_source_counts": dict(sorted(geometry_source_counts.items())),
        "usable_geometry_coverage": sum(
            count for source, count in geometry_source_counts.items() if source != "UNKNOWN"
        ),
    }


def reno_score(required_work_cad: float | int | None) -> tuple[int, str]:
    """Map estimated necessary holding work to the user-approved bands."""

    value = max(0.0, _number(required_work_cad) or 0.0)
    if value == 0:
        return 100, "MOVE_IN_READY"
    if value <= 30_000:
        return 80, "LIGHT_RENO"
    if value <= 60_000:
        return 60, "MODERATE_RENO"
    if value <= 100_000:
        return 40, "SIGNIFICANT_RENO"
    if value <= 150_000:
        return 20, "HEAVY_RENO"
    return 0, "CURRENT_USE_PASS"


def normalized_physical_basis(
    *,
    asking: float | int | None,
    bca_improvement: float | int | None,
    location_residual: float | int | None,
    lot_area_sqft: float | int | None,
) -> dict[str, float | None]:
    """Calculate the transparent preliminary physical-lot normalization."""

    ask = _number(asking)
    improvement = _number(bca_improvement)
    residual = _number(location_residual) or 0.0
    area = _number(lot_area_sqft)
    market_land = ask - improvement if ask is not None and improvement is not None else None
    normalized = market_land - residual if market_land is not None else None
    return {
        "market_land_basis": round(market_land, 2) if market_land is not None else None,
        "normalized_lot_basis": round(normalized, 2) if normalized is not None else None,
        # This is a screening proxy, not a valuation appraisal.  Keep the
        # displayed unit basis at whole dollars so small BCA/residual rounding
        # does not imply false precision.
        "normalized_lot_basis_per_sqft": round(normalized / area, 0) if normalized is not None and area and area > 0 else None,
    }


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _norm_zoning(value: Any) -> str | None:
    if not _present(value):
        return None
    return re.sub(r"\s+", "", str(value).upper())


def _unit_options(max_units: int | None) -> list[int]:
    return list(range(2, max_units + 1)) if max_units and max_units >= 2 else []


def zoning_scenarios(
    *,
    zoning: str | None,
    municipality: str | None,
    lot_area_sqft: float | int | None,
    frontage_ft: float | int | None,
    depth_ft: float | int | None,
    lane_access: bool | None = None,
    floodplain: bool | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply only registered official rules and return explicit scenarios.

    A missing rule, missing lot measurement, or unresolved access condition is
    returned as a review state.  The function never converts a TOA label or a
    listing description into current entitlement.
    """

    code = _norm_zoning(zoning)
    base = {
        "zoning": code,
        "municipality": municipality,
        "rule_status": "UNKNOWN_REQUIRES_OFFICIAL_REVIEW" if not code else "RULE_NOT_FOUND",
        "numeric_status": "UNKNOWN",
        "source_url": None,
        "bylaw_version": None,
        "maximum_fsr": None,
        "discretionary_max_fsr": None,
        "maximum_units": None,
        "multiplex_max_units": None,
        "conditional_additional_units": None,
        "duplex_permitted": None,
        "multiplex_permitted": None,
        "theoretical_buildable_sqft": None,
        "realistic_buildable_sqft": None,
        "unit_options": [],
        "best_product": "UNKNOWN",
        "alternative_paths": [],
        "rear_access_required": None,
        "eligibility_notes": [],
        "calculation_status": "ESTIMATE",
        "floodplain": floodplain,
    }
    if not code:
        return base
    registry_path = Path(config_path) if config_path else DEFAULT_RULES_PATH
    registry = _load_yaml(registry_path)
    rule = (registry.get("rules") or {}).get(code)
    if not isinstance(rule, Mapping):
        return base
    result = dict(base)
    result.update({
        "rule_status": "OFFICIAL_RULE_APPLIED" if str(rule.get("numeric_status", "")).upper() == "OFFICIAL_CONFIRMED" else str(rule.get("numeric_status") or "RULE_NOT_FOUND"),
        "numeric_status": rule.get("numeric_status", "UNKNOWN"),
        "source_url": rule.get("schedule_url"),
        "bylaw_version": rule.get("bylaw_version"),
        "maximum_fsr": _number(rule.get("maximum_fsr")),
        "discretionary_max_fsr": _number(rule.get("discretionary_max_fsr")),
        "maximum_units": int(rule["maximum_units"]) if _number(rule.get("maximum_units")) is not None else None,
        "duplex_permitted": rule.get("duplex_permitted"),
        "multiplex_permitted": rule.get("multiplex_permitted"),
        "rear_access_required": rule.get("rear_access_required"),
        "conditional_additional_units": int(_number(rule.get("conditional_additional_units"))) if _number(rule.get("conditional_additional_units")) is not None else None,
    })
    notes: list[str] = []
    if rule.get("notes"):
        notes.append(str(rule["notes"]))
    if floodplain is True and rule.get("floodplain_exclusion"):
        notes.append("FLOODPLAIN_EXCLUSION: current rule path is not eligible until site condition is resolved.")
    if rule.get("rear_access_required") and lane_access is False:
        notes.append("REAR_ACCESS_NOT_CONFIRMED: required rear vehicular access is absent or not evidenced.")
    elif rule.get("rear_access_required") and lane_access is None:
        notes.append("REAR_ACCESS_REVIEW_REQUIRED: rule requires rear vehicular access; listing/lane evidence is not enough for final confirmation.")

    area = _number(lot_area_sqft)
    frontage = _number(frontage_ft)
    depth = _number(depth_ft)
    if rule.get("duplex_max_fsr") is not None:
        result["maximum_fsr"] = _number(rule.get("duplex_max_fsr"))
    if result["maximum_fsr"] is not None and area is not None and area > 0:
        result["theoretical_buildable_sqft"] = round(area * float(result["maximum_fsr"]), 2)
        result["realistic_buildable_sqft"] = round(area * float(result["maximum_fsr"]) * 0.82, 2)
    elif result["numeric_status"] != "OFFICIAL_CONFIRMED":
        notes.append("NUMERIC_RULE_REVIEW_REQUIRED: no buildable-area estimate is emitted.")
    if code == "R1-1" or rule.get("tiers"):
        tiers = rule.get("tiers") or []
        qualified: list[Mapping[str, Any]] = []
        if frontage is None:
            notes.append("FRONTAGE_REQUIRED: R1-1 unit tier cannot be selected without a frontage measurement.")
        for tier in tiers:
            if not isinstance(tier, Mapping):
                continue
            min_area = _number(tier.get("minimum_site_area_sqft"))
            min_frontage = _number(tier.get("minimum_frontage_ft"))
            max_area = _number(tier.get("maximum_site_area_sqft"))
            if area is not None and min_area is not None and area < min_area:
                continue
            if area is not None and max_area is not None and area > max_area:
                continue
            if min_frontage is not None and (frontage is None or frontage < min_frontage):
                continue
            qualified.append(tier)
        if qualified:
            selected = qualified[-1]
            result["multiplex_max_units"] = int(_number(selected.get("maximum_units")) or 0) or None
            result["maximum_units"] = result["multiplex_max_units"]
            if result["multiplex_max_units"]:
                result["unit_options"] = _unit_options(result["multiplex_max_units"])
                result["best_product"] = f"MULTIPLEX_{result['multiplex_max_units']}_UNIT_OPTION"
            rental_only_units = _number(selected.get("rental_only_max_units"))
            if rental_only_units is not None and rental_only_units > result["multiplex_max_units"]:
                result["alternative_paths"].append({
                    "product": "SECURED_RENTAL_MULTIPLEX",
                    "maximum_units": int(rental_only_units),
                    "status": "OFFICIAL_RULE_PATH_REQUIRES_RENTAL_TENURE",
                })
        else:
            notes.append("LOT_THRESHOLD_NOT_MET: no registered frontage/site-area unit tier was satisfied.")
            result["multiplex_max_units"] = None
        if depth is not None and depth < 99.7:
            notes.append("DEPTH_BELOW_GUIDE_REFERENCE: R1-1 multiplex guide reference is approximately 99.7 ft.")
    elif rule.get("delegated_rule") == "R1-1_SMALL_SCALE_MULTIPLE":
        # R3/R5 explicitly delegate the <=8-unit small-scale multiple path to
        # R1-1.  Apply the same frontage/site-area thresholds instead of
        # blindly reporting the district's high-rise ceiling.
        r1_rule = (registry.get("rules") or {}).get("R1-1", {})
        tiers = r1_rule.get("tiers") if isinstance(r1_rule, Mapping) else []
        qualified: list[Mapping[str, Any]] = []
        for tier in tiers or []:
            if not isinstance(tier, Mapping):
                continue
            min_area = _number(tier.get("minimum_site_area_sqft"))
            min_frontage = _number(tier.get("minimum_frontage_ft"))
            if area is not None and min_area is not None and area < min_area:
                continue
            if frontage is not None and min_frontage is not None and frontage < min_frontage:
                continue
            if area is None or frontage is None:
                continue
            qualified.append(tier)
        if qualified:
            selected = qualified[-1]
            result["multiplex_max_units"] = int(_number(selected.get("maximum_units")) or 0) or None
            result["maximum_units"] = result["multiplex_max_units"]
            result["unit_options"] = _unit_options(result["multiplex_max_units"])
            result["best_product"] = f"MULTIPLEX_{result['multiplex_max_units']}_UNIT_OPTION"
        else:
            result["multiplex_max_units"] = None
            notes.append("R1-1_DELEGATED_THRESHOLD_REVIEW_REQUIRED: frontage and site area must be available to select the small-scale unit tier.")
    elif rule.get("multiple_min_site_area_sqft") is not None:
        multiple_min_area = _number(rule.get("multiple_min_site_area_sqft"))
        result["alternative_paths"] = [{
            "product": "MULTIPLE_DWELLING",
            "maximum_fsr": _number(rule.get("multiple_max_fsr")),
            "discretionary_max_fsr": _number(rule.get("discretionary_multiple_max_fsr")),
            "minimum_site_area_sqft": multiple_min_area,
            "maximum_units": int(_number(rule.get("multiple_max_units")) or 0) or None,
            "status": "OFFICIAL_RULE_PATH_REQUIRES_SITE_AND_USE_REVIEW",
        }]
        if area is not None and multiple_min_area is not None and area >= multiple_min_area:
            multiple_units = int(_number(rule.get("multiple_max_units")) or 0) or None
            if multiple_units is not None:
                result["multiplex_max_units"] = multiple_units
                result["unit_options"] = _unit_options(multiple_units)
                result["best_product"] = f"MULTIPLEX_{multiple_units}_UNIT_OPTION"
        if result["best_product"] == "UNKNOWN" and result["duplex_permitted"]:
            result["unit_options"] = [2]
            result["best_product"] = "DUPLEX"
    elif code == "RM-3A" and result["multiplex_permitted"] and area is not None and area >= (_number(rule.get("multiple_min_site_area_sqft")) or float("inf")):
        # Preserve the ordinary small-site duplex path above, while exposing
        # the distinct conditional multiple-dwelling gate when it is met.
        result["alternative_paths"] = [{
            "product": "MULTIPLE_DWELLING",
            "maximum_fsr": _number(rule.get("multiple_max_fsr")),
            "discretionary_max_fsr": _number(rule.get("discretionary_multiple_max_fsr")),
            "minimum_site_area_sqft": _number(rule.get("multiple_min_site_area_sqft")),
            "status": "OFFICIAL_RULE_PATH_REQUIRES_SITE_AND_USE_REVIEW",
        }]
    elif result["multiplex_permitted"] and result["maximum_units"]:
        result["multiplex_max_units"] = result["maximum_units"]
        result["unit_options"] = _unit_options(result["multiplex_max_units"])
        result["best_product"] = f"MULTIPLEX_{result['multiplex_max_units']}_UNIT_OPTION"
    elif result["duplex_permitted"]:
        result["multiplex_max_units"] = 2
        result["unit_options"] = [2]
        result["best_product"] = "DUPLEX"
    if result["multiplex_max_units"] and result["rear_access_required"] and lane_access is False:
        result["eligibility_notes"] = notes + ["MULTIPLEX_CURRENT_ELIGIBILITY_BLOCKED_BY_ACCESS"]
    else:
        result["eligibility_notes"] = notes
    return result


def _score_value(decision: Any, name: str) -> Any:
    value = _get(decision, name)
    return value


def _fmt(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_fmt(item) for item in value) if value else "UNKNOWN"
    return str(getattr(value, "value", value))


def _money(value: Any) -> str:
    number = _number(value)
    return "UNKNOWN" if number is None else f"${number:,.0f}"


def _pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "UNKNOWN"
    # score artifacts store ratios as decimals; a value over one is already a
    # percentage number and is displayed conservatively as-is.
    return f"{number * 100:.1f}%" if abs(number) <= 1 else f"{number:.1f}%"


def _table(rows: Sequence[tuple[str, Any, str]]) -> str:
    lines = ["| Field | Value | Status |", "|---|---:|---|"]
    lines.extend(f"| {name} | {_fmt(value)} | {status} |" for name, value, status in rows)
    return "\n".join(lines)


def _decision_mapping(decisions: Any) -> dict[str, Any]:
    if isinstance(decisions, Mapping):
        return {str(key): value for key, value in decisions.items()}
    result: dict[str, Any] = {}
    for decision in decisions or []:
        card = _get(decision, "card", decision)
        result[property_key(card)] = decision
    return result


def _zoning_for(card: Any, decision: Any, config_path: str | Path | None) -> dict[str, Any]:
    zoning = _official(card, "official_current_zoning", "official_zoning") or _first(card, "zoning")
    frontage = _official(card, "official_frontage_ft") or _first(card, "frontage_ft")
    depth = _official(card, "official_depth_ft") or _first(card, "depth_ft")
    parsed_frontage, parsed_depth = _dimensions(_first(card, "lot_dimensions"))
    frontage = frontage if _number(frontage) is not None else parsed_frontage
    depth = depth if _number(depth) is not None else parsed_depth
    area = _first(card, "lot_area_sqft", default=None)
    lane = _first(card, "lane_access", "lane", default=None)
    return zoning_scenarios(
        zoning=zoning,
        municipality=_first(card, "municipality"),
        lot_area_sqft=area,
        frontage_ft=frontage,
        depth_ft=depth,
        lane_access=lane,
        config_path=config_path,
    )


def _score_component_lines(decision: Any, field: str) -> list[str]:
    components = _score_value(decision, field) or {}
    if not isinstance(components, Mapping) or not components:
        return ["- UNKNOWN"]
    return [f"- `{name}`: {_fmt(value)}" for name, value in components.items()]


def _property_thesis(card: Any, decision: Any | None, zoning: Mapping[str, Any], normalized: Mapping[str, Any]) -> str:
    """Create a property-specific, evidence-labelled decision summary."""

    address = _first(card, "address", default="This property")
    beds = _number(_first(card, "bedrooms"))
    baths = _number(_first(card, "bathrooms"))
    sqft = _number(_first(card, "finished_sqft"))
    lot_area = _number(_first(card, "lot_area_sqft"))
    reno = _number(_score_value(decision, "reno_estimate"))
    reno_band = _score_value(decision, "reno_band") or "UNKNOWN"
    holding_status = _score_value(decision, "holding_status") or "HOLDING_UNKNOWN"
    residual = _number(_score_value(decision, "location_residual_cad"))
    road_discount = _number(_score_value(decision, "road_discount_pct")) or 0.0
    assessment_discount = _number(_score_value(decision, "assessment_discount_pct"))
    product = zoning.get("best_product")
    max_fsr = _number(zoning.get("maximum_fsr"))
    theoretical_area = _number(zoning.get("theoretical_buildable_sqft"))
    normalized_per_sqft = _number(normalized.get("normalized_lot_basis_per_sqft"))

    if reno is None:
        holding_sentence = (
            f"{address} has a {holding_status} screening status, but the necessary 5–10 year holding work is UNKNOWN; "
            "inspection evidence is required before treating the existing house as usable."
        )
    elif reno == 0:
        holding_sentence = (
            f"The saved listing evidence supports a move-in-ready holding profile for {address}; no necessary holding work "
            "was identified by the current rule set."
        )
    else:
        holding_sentence = (
            f"The current screening estimate places necessary 5–10 year holding work at {_money(reno)} ({reno_band}); "
            "this is a planning estimate, not an inspection or contractor quote."
        )
    if beds is not None and baths is not None and sqft is not None:
        holding_sentence += f" The listing presents {int(beds)} beds / {int(baths)} baths and {_fmt(sqft)} finished sqft."

    land_parts: list[str] = []
    if lot_area is not None:
        land_parts.append(f"{_fmt(lot_area)} sqft lot")
    if product and product != "UNKNOWN":
        land_parts.append(f"registered path {product}")
    if max_fsr is not None and theoretical_area is not None:
        land_parts.append(f"base FSR {_fmt(max_fsr)} and approximately {_fmt(theoretical_area)} sqft theoretical area")
    land_sentence = (
        f"The redevelopment screen for {address} is based on " + ", ".join(land_parts) + "."
        if land_parts
        else f"The redevelopment screen for {address} remains UNKNOWN because no complete registered product path was established."
    )
    if normalized_per_sqft is not None:
        land_sentence += f" The preliminary normalized physical-lot basis is {_money(normalized.get('normalized_lot_basis'))} ({_money(normalized_per_sqft)}/lot sqft)."
    elif _present(normalized.get("normalized_lot_basis")):
        land_sentence += f" The preliminary normalized physical-lot basis is {_money(normalized.get('normalized_lot_basis'))}; lot-area normalization is unavailable."

    value_parts: list[str] = []
    if residual is not None:
        direction = "positive" if residual >= 0 else "negative"
        value_parts.append(f"the local location residual is {_money(residual)} ({direction})")
    if assessment_discount is not None:
        value_parts.append(f"asking is {_pct(assessment_discount)} below total BCA assessment")
    if value_parts:
        value_sentence = "Value signals: " + "; ".join(value_parts) + "."
    else:
        value_sentence = "Value signals are incomplete; the report does not infer a market discount from missing fields."
    if road_discount > 0:
        value_sentence += f" The road model applies a {_pct(road_discount)} prior discount for permanent location exposure."
    else:
        value_sentence += " No configured road-discount prior was applied."

    return "\n".join((
        f"- **Holding view:** {holding_sentence}",
        f"- **Land / redevelopment view:** {land_sentence}",
        f"- **Price / risk view:** {value_sentence}",
    ))


def render_property_report(
    card: Any,
    decision: Any | None = None,
    *,
    config_path: str | Path | None = None,
    rules_path: str | Path | None = None,
) -> str:
    """Render a complete three-layer report for one listing."""

    # The card passed by the report pipeline is the current listing/detail
    # observation.  A persisted decision may contain an older card snapshot;
    # use it only for scores, never to overwrite refreshed facts.
    source_card = card
    address = _first(source_card, "address", default="UNKNOWN")
    mls = _first(source_card, "mls_number", "mls", default=None)
    url = _first(source_card, "source_url", "url", default=None)
    asking = _first(source_card, "asking", "current_asking")
    bca_total = _official(source_card, "official_assessment_total") or _score_value(decision, "assessment_total")
    bca_land = _official(source_card, "official_assessment_land") or _score_value(decision, "assessment_land")
    bca_improvement = _official(source_card, "official_assessment_improvement") or _score_value(decision, "assessment_improvement")
    ask_discount = (1 - float(asking) / float(bca_total)) if _number(asking) and _number(bca_total) else None
    land_discount = (1 - float(asking) / float(bca_land)) if _number(asking) and _number(bca_land) else None
    location_residual = _score_value(decision, "location_residual_cad")
    sale_history = _official_sale_history(source_card)
    realtor_history = _realtor_price_sale_history(source_card)
    normalized = normalized_physical_basis(
        asking=asking,
        bca_improvement=bca_improvement,
        location_residual=location_residual,
        lot_area_sqft=_first(source_card, "lot_area_sqft"),
    )
    # Prefer the current screening ledger when this report is rendered from a
    # scored portfolio.  The small helper above remains as a compatibility
    # fallback for older reports that have no ScreeningDecision.
    if decision is not None:
        for name in (
            "market_land_basis",
            "market_house_value_proxy",
            "area_normalized_lot_basis",
            "area_normalized_lot_basis_per_sqft",
            "risk_adjusted_lot_basis",
            "risk_adjusted_lot_basis_per_sqft",
            "area_standard_lot_equivalent_basis",
            "risk_adjusted_standard_lot_equivalent_basis",
        ):
            value = _score_value(decision, name)
            if value is not None:
                normalized[name] = value
        if normalized.get("area_normalized_lot_basis") is not None:
            normalized["normalized_lot_basis"] = normalized["area_normalized_lot_basis"]
        if normalized.get("area_normalized_lot_basis_per_sqft") is not None:
            normalized["normalized_lot_basis_per_sqft"] = normalized["area_normalized_lot_basis_per_sqft"]
    reno_estimate = _score_value(decision, "reno_estimate")
    reno_band = _score_value(decision, "reno_band")
    reno_points = _score_value(decision, "reno_score")
    if reno_estimate is not None and reno_points is None:
        reno_points, reno_band = reno_score(reno_estimate)
    zoning = _zoning_for(source_card, decision, rules_path or config_path)
    bca_confirmed = _official_complete(source_card, "bca")
    zoning_confirmed = _official_complete(source_card, "zoning")
    road_confirmed = _official_complete(source_card, "road")
    school_confirmed = _official_complete(source_card, "school_catchment")
    geometry_confirmed = _official_complete(source_card, "lot_geometry")
    bca_status = "OFFICIAL_CONFIRMED" if bca_confirmed else "UNVERIFIED" if _present(bca_total) else "UNKNOWN"
    zoning_status = "OFFICIAL_CONFIRMED" if zoning_confirmed else "UNVERIFIED" if _present(zoning.get("zoning")) else "UNKNOWN"
    road_status = "OFFICIAL_CONFIRMED" if road_confirmed else "UNVERIFIED" if any(
        _present(_official(source_card, field))
        for field in ("official_road_class", "official_distance_to_major_road_m", "official_nearest_major_road")
    ) else "UNKNOWN"
    school_status = "OFFICIAL_CONFIRMED" if school_confirmed else "UNVERIFIED" if any(
        _present(_official(source_card, field))
        for field in ("official_elementary_catchment", "official_secondary_catchment")
    ) else "UNKNOWN"
    parsed_frontage, parsed_depth = _dimensions(_first(source_card, "lot_dimensions"))
    displayed_frontage = _first(source_card, "frontage_ft", default=_official(source_card, "official_frontage_ft"))
    displayed_depth = _first(source_card, "depth_ft", default=_official(source_card, "official_depth_ft"))
    if _number(displayed_frontage) is None:
        displayed_frontage = parsed_frontage
    if _number(displayed_depth) is None:
        displayed_depth = parsed_depth
    score_fields = (
        ("Current Use Score", "current_use_score"),
        ("Redevelopment Score", "redevelopment_score"),
        ("Dual Value Score", "dual_value_score"),
        ("Investment Score", "investment_score"),
        ("Confidence Score", "confidence_score"),
        ("Total / Legacy Score", "stage_b_score"),
    )
    scores = "\n".join(f"- {label}: **{_fmt(_score_value(decision, field))}**" for label, field in score_fields)
    road_discount = _score_value(decision, "road_discount_pct")
    raw = _raw(source_card)
    description = str(_first(source_card, "description", default="") or "").strip()
    description_excerpt = " ".join(description.split())
    if len(description_excerpt) > 800:
        description_excerpt = description_excerpt[:797] + "..."
    data_rows = [
        ("asking", asking, "LISTING_OBSERVED"),
        ("BCA total", bca_total, bca_status),
        ("BCA land", bca_land, bca_status),
        ("BCA improvement / house proxy", bca_improvement, bca_status),
        ("asking vs BCA total", _pct(ask_discount), "DERIVED_FROM_OFFICIAL" if ask_discount is not None and bca_confirmed else "UNVERIFIED" if ask_discount is not None else "UNKNOWN"),
        ("asking vs BCA land", _pct(land_discount), "DERIVED_FROM_OFFICIAL" if land_discount is not None and bca_confirmed else "UNVERIFIED" if land_discount is not None else "UNKNOWN"),
        ("location residual", location_residual, "DERIVED_FROM_LOCAL_MODEL" if location_residual is not None else "UNKNOWN"),
        ("market house value proxy (BCA improvement)", _score_value(decision, "market_house_value_proxy") if decision is not None else bca_improvement, "DERIVED_FROM_OFFICIAL" if bca_improvement is not None else "UNKNOWN"),
        ("market land basis (asking - house proxy)", _score_value(decision, "market_land_basis") if decision is not None else normalized["market_land_basis"], "DERIVED_FROM_OFFICIAL" if (normalized.get("market_land_basis") is not None) else "UNKNOWN"),
        ("area-normalized lot basis (before parcel road adjustment)", _score_value(decision, "area_normalized_lot_basis") if decision is not None else normalized["normalized_lot_basis"], "DERIVED_FROM_VALUE_LEDGER" if (normalized.get("area_normalized_lot_basis", normalized.get("normalized_lot_basis")) is not None) else "UNKNOWN"),
        ("area-normalized lot basis / sqft", _score_value(decision, "area_normalized_lot_basis_per_sqft") if decision is not None else normalized["normalized_lot_basis_per_sqft"], "DERIVED_FROM_VALUE_LEDGER" if (normalized.get("area_normalized_lot_basis_per_sqft", normalized.get("normalized_lot_basis_per_sqft")) is not None) else "UNKNOWN"),
        ("risk-adjusted lot basis (includes parcel road adjustment)", _score_value(decision, "risk_adjusted_lot_basis") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "risk_adjusted_lot_basis") is not None else "UNKNOWN"),
        ("risk-adjusted lot basis / sqft", _score_value(decision, "risk_adjusted_lot_basis_per_sqft") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "risk_adjusted_lot_basis_per_sqft") is not None else "UNKNOWN"),
        ("current-use effective basis (asking + reno)", _score_value(decision, "current_use_effective_basis") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_use_effective_basis") is not None else "UNKNOWN"),
        ("current package value proxy", _score_value(decision, "current_package_value_proxy") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_package_value_proxy") is not None else "UNKNOWN"),
        ("current package proxy source", _score_value(decision, "current_package_value_proxy_source") if decision is not None else None, "MODEL_INPUT" if decision is not None and _score_value(decision, "current_package_value_proxy_source") is not None else "UNKNOWN"),
        ("current package value ratio", _score_value(decision, "current_package_value_ratio") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_package_value_ratio") is not None else "UNKNOWN"),
        ("current lot basis / sqft", _score_value(decision, "current_lot_basis_per_sqft") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_lot_basis_per_sqft") is not None else "UNKNOWN"),
        ("current lot basis ratio", _score_value(decision, "current_lot_basis_ratio") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_lot_basis_ratio") is not None else "UNKNOWN"),
        ("regional price ratio / discount", f"{_fmt(_score_value(decision, 'regional_price_ratio'))} / {_pct(_score_value(decision, 'regional_price_discount_pct'))}" if decision is not None and _score_value(decision, "regional_price_ratio") is not None else "UNKNOWN", "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "regional_price_ratio") is not None else "UNKNOWN"),
        ("BCA total discount", _pct(_score_value(decision, "bca_discount_pct")) if decision is not None and _score_value(decision, "bca_discount_pct") is not None else _pct(ask_discount), "DERIVED_FROM_OFFICIAL" if ask_discount is not None else "UNKNOWN"),
        ("Current Use house/condition VFM", _score_value(decision, "house_value_for_money_score") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "house_value_for_money_score") is not None else "UNKNOWN"),
        ("Current Use location quality", _score_value(decision, "location_quality_score") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "location_quality_score") is not None else "UNKNOWN"),
        ("Current Use Reno cap", _score_value(decision, "current_use_reno_cap") if decision is not None else None, "POLICY_CAP" if decision is not None and _score_value(decision, "current_use_reno_cap") is not None else "UNKNOWN"),
        ("Current Use package VFM", _score_value(decision, "current_package_value_for_money_score") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "current_package_value_for_money_score") is not None else "UNKNOWN"),
        ("Redevelopment capacity VFM", _score_value(decision, "redevelopment_capacity_value_for_money_score") if decision is not None else None, "DERIVED_FROM_VALUE_LEDGER" if decision is not None and _score_value(decision, "redevelopment_capacity_value_for_money_score") is not None else "UNKNOWN"),
        ("Redevelopment geometry adequacy", _score_value(decision, "redevelopment_geometry_adequacy_score") if decision is not None else None, "DERIVED_FROM_OFFICIAL_RULES_AND_GEOMETRY" if decision is not None and _score_value(decision, "redevelopment_geometry_adequacy_score") is not None else "UNKNOWN"),
    ]
    current_components = "\n".join(_score_component_lines(decision, "current_use_score_components"))
    redevelopment_components = "\n".join(_score_component_lines(decision, "redevelopment_score_components"))
    return f"""# {address}

**MLS:** {_fmt(mls)}  
**Listing URL:** {_fmt(url)}  
**Municipality / neighbourhood:** {_fmt(_first(source_card, 'municipality'))} / {_fmt(_first(source_card, 'neighbourhood'))}  
**Property key:** `{property_key(source_card)}`  
**Report status:** {_fmt(_get(decision, 'status', 'NO_DECISION'))} / {_fmt(_get(decision, 'track', 'UNKNOWN'))}

### Identity and listing record

{_table([
    ('PID', _official(source_card, 'pid', 'official_pid') or raw.get('PID'), 'OFFICIAL_CONFIRMED' if bca_confirmed and (_official(source_card, 'pid', 'official_pid') or raw.get('PID')) else 'UNVERIFIED' if _official(source_card, 'pid', 'official_pid') or raw.get('PID') else 'UNKNOWN'),
    ('Legal description', _official(source_card, 'legal_description', 'official_legal_description') or raw.get('legal_description'), 'OFFICIAL_CONFIRMED' if bca_confirmed and (_official(source_card, 'legal_description', 'official_legal_description') or raw.get('legal_description')) else 'UNVERIFIED' if _official(source_card, 'legal_description', 'official_legal_description') or raw.get('legal_description') else 'UNKNOWN'),
    ('Ownership / title', _first(source_card, 'ownership_type'), 'LISTING_OBSERVED' if _present(_first(source_card, 'ownership_type')) else 'UNKNOWN'),
    ('Listing status', _first(source_card, 'listing_status'), 'LISTING_OBSERVED'),
    ('Original asking', _first(source_card, 'original_asking'), 'LISTING_OBSERVED' if _present(_first(source_card, 'original_asking')) else 'UNKNOWN'),
    ('DOM / cumulative DOM', f"{_fmt(_first(source_card, 'dom'))} / {_fmt(_get(decision, 'cumulative_dom'))}", 'LISTING_OBSERVED'),
    ('Price reduction', f"{_money(_get(decision, 'price_reduction_amount'))} / {_pct(_get(decision, 'price_reduction_pct'))}", 'DERIVED_FROM_LISTING_HISTORY'),
])}

## Evidence status legend

`OFFICIAL_CONFIRMED` = saved official record with successful provenance; `DERIVED_FROM_OFFICIAL_RULES` = calculation from the versioned municipal rule registry; `DERIVED_FROM_OFFICIAL` = arithmetic from official values; `LISTING_OBSERVED` = listing/card observation; `ESTIMATE` = configured assumption or preliminary model; `UNVERIFIED` = a value exists but its required provenance is incomplete; `UNKNOWN` = not established and not guessed.

## 1. Current Use

### Family holding and house utility

{_table([
    ('Beds', _first(source_card, 'bedrooms'), 'LISTING_OBSERVED'),
    ('Baths', _first(source_card, 'bathrooms'), 'LISTING_OBSERVED'),
    ('Finished sqft', _first(source_card, 'finished_sqft'), 'LISTING_OBSERVED'),
    ('Year built', _first(source_card, 'year_built'), 'LISTING_OBSERVED'),
    ('Basement', _first(source_card, 'basement'), 'LISTING_OBSERVED'),
    ('Kitchens / layout signal', _first(source_card, 'kitchens', default=raw.get('kitchen_count')), 'LISTING_OBSERVED' if _present(_first(source_card, 'kitchens', default=raw.get('kitchen_count'))) else 'UNKNOWN'),
    ('Holding status', _score_value(decision, 'holding_status'), 'DERIVED_FROM_SCREENING'),
    ('Reno estimate', _money(reno_estimate), 'ESTIMATE' if reno_estimate is not None else 'UNKNOWN'),
    ('Reno score / band', f'{_fmt(reno_points)} / {_fmt(reno_band)}', 'DERIVED_FROM_SCREENING' if reno_points is not None else 'UNKNOWN'),
    ('Reno scope', _score_value(decision, 'reno_scope') or raw.get('reno_scope'), 'LISTING_OBSERVED' if _present(_score_value(decision, 'reno_scope') or raw.get('reno_scope')) else 'UNKNOWN'),
])}

### BCA and price basis

{_table(data_rows)}

The BCA improvement value is retained as the `MARKET_HOUSE_VALUE_BCA_PROXY`; it is not silently replaced with a guessed renovation value. Renovation is a separate holding-cost estimate.

**Assessment history:** {_fmt(_official(source_card, 'official_assessment_history') or raw.get('official_assessment_history'))}  
**Previous listing prices:** {_fmt(_first(source_card, 'previous_listing_prices', default=raw.get('previous_listing_prices')))}  
**Official sold history:** {_fmt(sale_history or _first(source_card, 'previous_sale_prices', default=raw.get('previous_sale_prices')))} — a successful query with no recorded sale is explicitly distinguished from an unqueried history.  
**REALTOR.ca displayed price/sale history:** {_fmt(realtor_history or 'NOT_PRESENT')} — only rows explicitly rendered by the detail page are retained; no sale is inferred when the page has no history table.

### Location, road and school

{_table([
    ('Road class', _score_value(decision, 'road_class') or _official(source_card, 'official_road_class'), road_status if _present(_score_value(decision, 'road_class') or _official(source_card, 'official_road_class')) else 'UNKNOWN'),
    ('Nearest major road', _official(source_card, 'official_nearest_major_road'), road_status if _present(_official(source_card, 'official_nearest_major_road')) else 'UNKNOWN'),
    ('Distance to major road (m)', _official(source_card, 'official_distance_to_major_road_m'), road_status if _number(_official(source_card, 'official_distance_to_major_road_m')) is not None else 'UNKNOWN'),
    ('Direct frontage confirmed', _score_value(decision, 'property_fronts_major_road'), 'OFFICIAL_CONFIRMED' if _score_value(decision, 'property_fronts_major_road') is not None and road_confirmed else 'UNVERIFIED' if _score_value(decision, 'property_fronts_major_road') is not None else 'UNKNOWN'),
    ('Frontage determination method', _score_value(decision, 'road_frontage_method'), 'DERIVED_FROM_OFFICIAL_ROAD' if _present(_score_value(decision, 'road_frontage_method')) and _score_value(decision, 'road_frontage_method') != 'UNKNOWN' else 'UNKNOWN'),
    ('Road distance used for scoring (m)', _score_value(decision, 'road_distance_m'), 'OFFICIAL_CONFIRMED' if _score_value(decision, 'road_distance_m') is not None and road_confirmed else 'UNKNOWN'),
    ('Road category', _score_value(decision, 'road_discount_category'), 'DERIVED_FROM_ROAD_PROFILE' if _present(_score_value(decision, 'road_discount_category')) else 'UNKNOWN'),
    ('Road quality (direct /10)', _score_value(decision, 'road_quality_direct_score'), 'DERIVED_FROM_ROAD_PROFILE' if _score_value(decision, 'road_quality_direct_score') is not None else 'UNKNOWN'),
    ('Effective road quality (/10)', _score_value(decision, 'road_quality_effective_score'), 'DERIVED_FROM_ROAD_PROFILE' if _score_value(decision, 'road_quality_effective_score') is not None else 'UNKNOWN'),
    ('Road quality exposure factor', _score_value(decision, 'road_quality_exposure_factor'), 'DERIVED_FROM_ROAD_PROFILE' if _score_value(decision, 'road_quality_exposure_factor') is not None else 'UNKNOWN'),
    ('Road micro-location score (/6)', _score_value(decision, 'road_quality_micro_score'), 'DERIVED_FROM_ROAD_PROFILE' if _score_value(decision, 'road_quality_micro_score') is not None else 'UNKNOWN'),
    ('Non-road micro-location score (/9)', _score_value(decision, 'micro_location_nonroad_score'), 'DERIVED_FROM_SCREENING' if _score_value(decision, 'micro_location_nonroad_score') is not None else 'UNKNOWN'),
    ('Micro-location total (/15)', _score_value(decision, 'current_use_components').get('micro_location_noise') if isinstance(_score_value(decision, 'current_use_components'), Mapping) else None, 'DERIVED_FROM_SCREENING' if isinstance(_score_value(decision, 'current_use_components'), Mapping) and _score_value(decision, 'current_use_components').get('micro_location_noise') is not None else 'UNKNOWN'),
    ('Road discount prior', _pct(road_discount), 'ESTIMATE' if road_discount is not None else 'UNKNOWN'),
    ('Road discount exposure factor', _score_value(decision, 'road_discount_exposure_factor'), 'DERIVED_FROM_ROAD_DISCOUNT' if _score_value(decision, 'road_discount_exposure_factor') is not None else 'UNKNOWN'),
    ('Elementary catchment', _official(source_card, 'official_elementary_catchment'), school_status if _present(_official(source_card, 'official_elementary_catchment')) else 'UNKNOWN'),
    ('Secondary catchment', _official(source_card, 'official_secondary_catchment'), school_status if _present(_official(source_card, 'official_secondary_catchment')) else 'UNKNOWN'),
    ('School performance', _official(source_card, 'official_elementary_performance', 'official_secondary_performance'), 'OFFICIAL_CONFIRMED' if _official(source_card, 'official_elementary_performance', 'official_secondary_performance') else 'UNKNOWN'),
    ('Location residual', location_residual, 'DERIVED_FROM_LOCAL_MODEL' if location_residual is not None else 'UNKNOWN'),
])}

## 2. Redevelopment

### Lot and official zoning rule

{_table([
    ('Lot area sqft', _first(source_card, 'lot_area_sqft'), 'LISTING_OBSERVED'),
    ('Frontage x depth', f"{_fmt(displayed_frontage)} x {_fmt(displayed_depth)} ft", 'OFFICIAL_CONFIRMED' if geometry_confirmed else 'LISTING_OBSERVED' if displayed_frontage is not None and displayed_depth is not None else 'UNKNOWN'),
    ('Geometry source', _first(source_card, 'geometry_source', default=raw.get('geometry_source')), 'DERIVED_FROM_OFFICIAL' if _first(source_card, 'geometry_source', default=raw.get('geometry_source')) == 'OFFICIAL_BCA' else 'LISTING_OBSERVED' if _first(source_card, 'geometry_source', default=raw.get('geometry_source')) == 'LISTING_OBSERVED' else 'UNKNOWN'),
    ('Lane access', _first(source_card, 'lane_access', 'lane'), 'LISTING_OBSERVED' if _present(_first(source_card, 'lane_access', 'lane')) else 'UNKNOWN'),
    ('Official zoning', zoning.get('zoning'), zoning_status),
    ('Rule status', zoning.get('rule_status'), 'OFFICIAL_CONFIRMED' if zoning_confirmed and zoning.get('rule_status') == 'OFFICIAL_RULE_APPLIED' else 'UNKNOWN'),
    ('Maximum FSR', zoning.get('maximum_fsr'), 'DERIVED_FROM_OFFICIAL_RULES' if zoning.get('maximum_fsr') is not None else 'UNKNOWN'),
    ('Theoretical buildable sqft', zoning.get('theoretical_buildable_sqft'), 'DERIVED_FROM_OFFICIAL_RULES' if zoning.get('theoretical_buildable_sqft') is not None else 'UNKNOWN'),
    ('Realistic buildable sqft', zoning.get('realistic_buildable_sqft'), 'ESTIMATE' if zoning.get('realistic_buildable_sqft') is not None else 'UNKNOWN'),
    ('Multiplex max units', zoning.get('multiplex_max_units'), 'DERIVED_FROM_OFFICIAL_RULES' if zoning.get('multiplex_max_units') is not None else 'UNKNOWN'),
    ('Unit options', zoning.get('unit_options'), 'DERIVED_FROM_OFFICIAL_RULES' if zoning.get('unit_options') else 'UNKNOWN'),
    ('Approx. realistic area / unit option', f"{_fmt(round(zoning['realistic_buildable_sqft'] / zoning['multiplex_max_units'], 1))} sqft at max-unit option" if zoning.get('realistic_buildable_sqft') and zoning.get('multiplex_max_units') else 'UNKNOWN', 'ESTIMATE' if zoning.get('realistic_buildable_sqft') and zoning.get('multiplex_max_units') else 'UNKNOWN'),
    ('Best registered product', zoning.get('best_product'), 'DERIVED_FROM_OFFICIAL_RULES' if zoning.get('best_product') != 'UNKNOWN' else 'UNKNOWN'),
])}

**Zoning source:** {_fmt(zoning.get('source_url'))}  
**Bylaw/version:** {_fmt(zoning.get('bylaw_version'))}  
**Eligibility notes:** {_fmt(zoning.get('eligibility_notes'))}
**Alternative official paths:** {_fmt(zoning.get('alternative_paths'))}

### Scenario interpretation

- **Keep existing house:** current-use value is shown above; do not infer tear-down solely from age.
- **Detached rebuild:** `UNKNOWN` unless the official rule and lot geometry support a defensible calculation.
- **Duplex:** use the official `duplex_permitted` flag and the registered FSR/site conditions.
- **Multiplex:** use the registered unit threshold and FSR only; access, floodplain, servicing, design and permit conditions remain due diligence items.
- **TOA / assembly:** long-term option only; it is not included as current entitlement unless an official site-specific rule confirms it.

### Preliminary economics

| Item | Value | Status |
|---|---:|---|
| Redevelopment cost | UNKNOWN | ESTIMATE |
| Exit value | UNKNOWN | ESTIMATE |
| Selling cost / developer margin | UNKNOWN | ESTIMATE |
| RALV / redevelopment profit | {_fmt(_score_value(decision, 'redevelopment_profit_cad'))} | ESTIMATE |
| Road-adjusted exit consideration | {_fmt(_score_value(decision, 'road_discount_cad'))} | ESTIMATE |

The scenario area is a legal-rule and geometry screen, not a building permit, massing plan, construction quote, or guaranteed saleable area.

### Listing evidence (not an official condition report)

{description_excerpt or 'UNKNOWN'}

## 3. Confidence and Data Gaps

**Data confidence score:** {_fmt(_score_value(decision, 'confidence_score'))}  
**Confidence affects score:** NO — user-approved rule; missing data is surfaced for later manual confirmation.  
**Decision flags:** {_fmt(_score_value(decision, 'flags'))}  
**Reason codes:** {_fmt(_score_value(decision, 'reason_codes'))}

### Official coverage for this property

{_table([
    ('BCA assessment', 'YES' if _official_complete(source_card, 'bca') else 'NO', 'OFFICIAL_CONFIRMED' if _official_complete(source_card, 'bca') else 'UNKNOWN'),
    ('Zoning', 'YES' if _official_complete(source_card, 'zoning') else 'NO', 'OFFICIAL_CONFIRMED' if _official_complete(source_card, 'zoning') else 'UNKNOWN'),
    ('Road', 'YES' if _official_complete(source_card, 'road') else 'NO', 'OFFICIAL_CONFIRMED' if _official_complete(source_card, 'road') else 'UNKNOWN'),
    ('School catchments', 'YES' if _official_complete(source_card, 'school_catchment') else 'NO', 'OFFICIAL_CONFIRMED' if _official_complete(source_card, 'school_catchment') else 'UNKNOWN'),
    ('Lot geometry', 'YES' if _official_complete(source_card, 'lot_geometry') else 'NO', 'OFFICIAL_CONFIRMED' if _official_complete(source_card, 'lot_geometry') else 'UNKNOWN'),
])}

### Scores

{scores}

### Unified value-for-money ledger components

#### Current Use

{current_components}

#### Redevelopment

{redevelopment_components}

### Required human follow-up

- Confirm title/ownership, legal description, PID and approved use before an offer.
- Confirm survey, lane/rear access, trees, slope, servicing, heritage/character and floodplain conditions before relying on a redevelopment scenario.
- Confirm house systems and inspection-based survival CAPEX; online listing evidence is not a substitute for an inspection.
- Confirm current school catchments and any boundary/capacity issue before treating school value as a purchase decision.

## Investment thesis

### Automated property thesis

{_property_thesis(source_card, decision, zoning, normalized)}

This report is a structured research record, not an automatic offer recommendation. The decision should be read through the three independent lenses above: current use, redevelopment, and confidence/data gaps. Unknowns remain unknown and are not converted into score bonuses.
"""


def _slug(value: Any) -> str:
    text = str(value or "unknown").upper()
    text = re.sub(r"[^A-Z0-9]+", "-", text).strip("-").lower()
    return text or "unknown"


def write_portfolio_reports(
    cards: Iterable[Any],
    decisions: Iterable[Any] | Mapping[str, Any] | None,
    output_dir: str | Path,
    *,
    rules_path: str | Path | None = None,
) -> list[Path]:
    """Write exactly one deterministic Markdown report per input card."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    by_key = _decision_mapping(decisions)
    paths: list[Path] = []
    used: set[str] = set()
    for card in cards:
        mls = _first(card, "mls_number", "mls", default=None)
        stem = _slug(_first(card, "address", default="unknown"))
        if _present(mls):
            stem = f"{stem}--{_slug(mls)}"
        candidate = stem
        suffix = 2
        # Re-running the same dated artifact should refresh the same report
        # paths rather than create ``--2`` copies.  Collisions within this
        # input batch still receive a deterministic suffix.
        while candidate in used:
            candidate = f"{stem}--{suffix}"
            suffix += 1
        used.add(candidate)
        path = destination / f"{candidate}.md"
        path.write_text(render_property_report(card, by_key.get(property_key(card)), rules_path=rules_path), encoding="utf-8")
        paths.append(path)
    return paths


_REPORT_KEY_PATTERN = re.compile(r"^\*\*Property key:\*\* `([^`]+)`", re.MULTILINE)


def validate_property_reports(cards: Iterable[Any], output_dir: str | Path) -> dict[str, Any]:
    """Validate report identity, coverage and required sections.

    File counts alone cannot detect a stale report copied over the wrong
    property.  Each report carries its stable property key; this validator
    compares that set with the current input set and also catches malformed or
    duplicate report identities.
    """

    materialized = list(cards)
    destination = Path(output_dir)
    reports = sorted(destination.glob("*.md")) if destination.exists() else []
    property_reports = [
        path for path in reports
        if path.name not in {"index.md", "ACCEPTANCE-REPORT.md"}
    ]
    required_sections = ("## 1. Current Use", "## 2. Redevelopment", "## 3. Confidence and Data Gaps")
    missing_sections: list[str] = []
    malformed_reports: list[str] = []
    report_keys: list[str] = []
    for path in property_reports:
        content = path.read_text(encoding="utf-8")
        match = _REPORT_KEY_PATTERN.search(content)
        if match is None:
            malformed_reports.append(path.name)
        else:
            report_keys.append(match.group(1))
        for section in required_sections:
            if section not in content:
                missing_sections.append(f"{path.name}:{section}")
    expected_keys = [property_key(card) for card in materialized]
    expected_set = set(expected_keys)
    report_set = set(report_keys)
    duplicate_report_keys = sorted(
        key for key, count in Counter(report_keys).items() if count > 1
    )
    return {
        "input_cards": len(materialized),
        "property_reports": len(property_reports),
        "expected_property_reports": len(materialized),
        "duplicate_input_keys": len(expected_keys) - len(expected_set),
        "duplicate_report_keys": duplicate_report_keys,
        "missing_report_keys": sorted(expected_set - report_set),
        "unexpected_report_keys": sorted(report_set - expected_set),
        "malformed_reports": malformed_reports,
        "missing_required_sections": missing_sections,
        "valid": (
            len(property_reports) == len(materialized)
            and len(expected_keys) == len(expected_set)
            and not duplicate_report_keys
            and not (expected_set - report_set)
            and not (report_set - expected_set)
            and not malformed_reports
            and not missing_sections
        ),
    }


def _summary_row(card: Any, decision: Any | None, report_path: str | None = None) -> dict[str, Any]:
    return {
        "property_key": property_key(card),
        "address": _first(card, "address"),
        "mls": _first(card, "mls_number", "mls"),
        "neighbourhood": _first(card, "neighbourhood"),
        "asking": _first(card, "asking"),
        "lot_area_sqft": _first(card, "lot_area_sqft"),
        "frontage_ft": _first(card, "frontage_ft", default=_official(card, "official_frontage_ft")),
        "depth_ft": _first(card, "depth_ft", default=_official(card, "official_depth_ft")),
        "geometry_source": _first(card, "geometry_source", default=_raw(card).get("geometry_source")),
        "realtor_history_status": _first(card, "realtor_history_status", default=_raw(card).get("realtor_history_status")),
        "realtor_history_event_count": len(_realtor_price_sale_history(card).get("events", [])) if _realtor_price_sale_history(card) else 0,
        "current_use_score": _score_value(decision, "current_use_score"),
        "dual_value_score": _score_value(decision, "dual_value_score"),
        "redevelopment_score": _score_value(decision, "redevelopment_score"),
        "investment_score": _score_value(decision, "investment_score"),
        "confidence_score": _score_value(decision, "confidence_score"),
        "status": _get(decision, "status", "NO_DECISION"),
        "track": _get(decision, "track", "UNKNOWN"),
        "asset_class": _get(decision, "asset_class", "UNKNOWN"),
        "location_residual_cad": _score_value(decision, "location_residual_cad"),
        "market_house_value_proxy": _score_value(decision, "market_house_value_proxy"),
        "market_land_basis": _score_value(decision, "market_land_basis"),
        "area_normalized_lot_basis": _score_value(decision, "area_normalized_lot_basis"),
        "area_normalized_lot_basis_per_sqft": _score_value(decision, "area_normalized_lot_basis_per_sqft"),
        "risk_adjusted_lot_basis": _score_value(decision, "risk_adjusted_lot_basis"),
        "risk_adjusted_lot_basis_per_sqft": _score_value(decision, "risk_adjusted_lot_basis_per_sqft"),
        "current_use_effective_basis": _score_value(decision, "current_use_effective_basis"),
        "current_package_value_proxy": _score_value(decision, "current_package_value_proxy"),
        "current_package_value_proxy_source": _score_value(decision, "current_package_value_proxy_source"),
        "current_package_value_ratio": _score_value(decision, "current_package_value_ratio"),
        "current_lot_basis_per_sqft": _score_value(decision, "current_lot_basis_per_sqft"),
        "current_lot_basis_ratio": _score_value(decision, "current_lot_basis_ratio"),
        "current_value_gap_cad": _score_value(decision, "current_value_gap_cad"),
        "regional_price_ratio": _score_value(decision, "regional_price_ratio"),
        "regional_price_discount_pct": _score_value(decision, "regional_price_discount_pct"),
        "assessment_discount_pct": _score_value(decision, "assessment_discount_pct"),
        "bca_discount_pct": _score_value(decision, "bca_discount_pct"),
        "reno_estimate": _score_value(decision, "reno_estimate"),
        "reno_score": _score_value(decision, "reno_score"),
        "reno_band": _score_value(decision, "reno_band"),
        "house_value_for_money_score": _score_value(decision, "house_value_for_money_score"),
        "current_package_value_for_money_score": _score_value(decision, "current_package_value_for_money_score"),
        "lot_value_for_money_score": _score_value(decision, "lot_value_for_money_score"),
        "current_lot_value_for_money_score": _score_value(decision, "current_lot_value_for_money_score"),
        "redevelopment_lot_value_for_money_score": _score_value(decision, "redevelopment_lot_value_for_money_score"),
        "redevelopment_capacity_value_for_money_score": _score_value(decision, "redevelopment_capacity_value_for_money_score"),
        "location_quality_score": _score_value(decision, "location_quality_score"),
        "redevelopment_geometry_adequacy_score": _score_value(decision, "redevelopment_geometry_adequacy_score"),
        "current_use_reno_cap": _score_value(decision, "current_use_reno_cap"),
        "current_use_score_components": _score_value(decision, "current_use_score_components") or {},
        "redevelopment_score_components": _score_value(decision, "redevelopment_score_components") or {},
        "score_components": _score_value(decision, "score_components") or {},
        "location_value_for_money_score": _score_value(decision, "location_value_for_money_score"),
        "bca_price_safety_score": _score_value(decision, "bca_price_safety_score"),
        "road_discount_pct": _score_value(decision, "road_discount_pct"),
        "road_class": _score_value(decision, "road_class"),
        "road_category": _score_value(decision, "road_discount_category"),
        "property_fronts_major_road": _score_value(decision, "property_fronts_major_road"),
        "road_distance_m": _score_value(decision, "road_distance_m"),
        "road_frontage_method": _score_value(decision, "road_frontage_method"),
        "road_quality_direct_score": _score_value(decision, "road_quality_direct_score"),
        "road_quality_exposure_factor": _score_value(decision, "road_quality_exposure_factor"),
        "road_quality_effective_score": _score_value(decision, "road_quality_effective_score"),
        "road_quality_micro_score": _score_value(decision, "road_quality_micro_score"),
        "micro_location_nonroad_score": _score_value(decision, "micro_location_nonroad_score"),
        "micro_location_score": (
            _score_value(decision, "current_use_components").get("micro_location_noise")
            if isinstance(_score_value(decision, "current_use_components"), Mapping)
            else None
        ),
        "report": report_path,
    }


def build_portfolio_analysis(
    cards: Iterable[Any],
    decisions: Iterable[Any] | Mapping[str, Any] | None,
    *,
    report_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build JSON-serializable audit, rows, and independent leaderboards."""

    materialized = list(cards)
    by_key = _decision_mapping(decisions)
    rows = [
        _summary_row(card, by_key.get(property_key(card)), (report_paths or {}).get(property_key(card)))
        for card in materialized
    ]

    def ranking(field: str) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: (_number(row.get(field)) if _number(row.get(field)) is not None else -1, str(row.get("address") or "")),
            reverse=True,
        )

    return {
        "schema_version": "portfolio-analysis-v1",
        "property_count": len(rows),
        "audit": audit_portfolio(materialized),
        "properties": rows,
        "rankings": {
            "current_use": ranking("current_use_score"),
            "dual_value": ranking("dual_value_score"),
            "redevelopment": ranking("redevelopment_score"),
            "investment": ranking("investment_score"),
        },
    }


def render_portfolio_index(analysis: Mapping[str, Any]) -> str:
    """Render all properties, not just a capped top-N list."""

    rows = list(analysis.get("properties") or [])
    audit = analysis.get("audit") or {}
    lines = [
        "# Vancouver Detached Portfolio — Full Three-Layer Research",
        "",
        f"**Properties:** {len(rows)}  ",
        "**Policy:** completeness gaps are reported but do not deduct from scores. Unknown is not guessed.",
        "",
        "## Coverage audit",
        "",
        f"- Official missing: `{json.dumps(audit.get('missing_official', {}), ensure_ascii=False)}`",
        f"- Optional missing: `{json.dumps(audit.get('optional_missing', {}), ensure_ascii=False)}`",
        f"- Zoning codes: `{json.dumps(audit.get('zoning_codes', {}), ensure_ascii=False)}`",
        "",
        "## Independent rankings",
        "",
        "The same underlying property analysis is used in every ranking. Only the sort key changes.",
    ]
    labels = (
        ("current_use", "Current Use"),
        ("dual_value", "Dual Value"),
        ("redevelopment", "Redevelopment"),
        ("investment", "Investment"),
    )
    for key, label in labels:
        lines.extend(["", f"### {label} ranking", "", "| Rank | Address | Ask | Score | Area | Report |", "|---:|---|---:|---:|---|---|"])
        for index, row in enumerate((analysis.get("rankings") or {}).get(key, []), 1):
            lines.append(
                f"| {index} | {row.get('address') or 'UNKNOWN'} | {_money(row.get('asking'))} | {_fmt(row.get(f'{key}_score'))} | {_fmt(row.get('neighbourhood'))} | [{row.get('mls') or 'report'}]({row.get('report') or '#'}) |"
            )
    lines.extend(["", f"## All {len(rows)} property records", "", "| # | Address | MLS | Ask | Current | Dual | Redevelopment | Investment | Confidence | Status | Report |", "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|"])
    for index, row in enumerate(sorted(rows, key=lambda item: str(item.get("address") or "")), 1):
        lines.append(
            f"| {index} | {row.get('address') or 'UNKNOWN'} | {row.get('mls') or 'UNKNOWN'} | {_money(row.get('asking'))} | {_fmt(row.get('current_use_score'))} | {_fmt(row.get('dual_value_score'))} | {_fmt(row.get('redevelopment_score'))} | {_fmt(row.get('investment_score'))} | {_fmt(row.get('confidence_score'))} | {_fmt(row.get('status'))} | [{row.get('mls') or 'open'}]({row.get('report') or '#'}) |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "audit_portfolio",
    "build_portfolio_analysis",
    "normalized_physical_basis",
    "property_key",
    "render_portfolio_index",
    "render_property_report",
    "reno_score",
    "validate_property_reports",
    "write_portfolio_reports",
    "zoning_scenarios",
]
