"""Ledger-first Strategy V2 shadow model.

This module is deliberately independent of the V1 scoring engine.  It turns
the saved official-enrichment cards into separate, inspectable ledgers:

* classification and non-compensatory gates;
* a holding ledger whose renovation estimate is a survival-cost reserve;
* a location ledger containing facts and external-provider snapshots;
* BASE/CONDITIONAL/UPSIDE development scenarios; and
* an acquisition ledger containing transparent price signals.

The module does not publish a Current Use, Dual, or Redevelopment ranking.
Unknown facts remain unknown and are never converted to zero.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .discovery import DiscoveryCard
from .portfolio import property_key, zoning_scenarios


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "strategy-v2-shadow.yaml"
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "vancouver-zoning-rules-2026-09-07.yaml"
DEFAULT_LOCATION_SNAPSHOT_PATH = PROJECT_ROOT / "data/research/history/location-provider-snapshots.jsonl"


GateStatus = Literal["PASS", "REVIEW", "FAIL"]
ScenarioName = Literal["BASE", "CONDITIONAL", "UPSIDE"]


class _V2Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class GateFinding(_V2Model):
    code: str
    status: GateStatus
    evidence: list[str] = Field(default_factory=list)
    explanation: str = ""


class AssetClassification(_V2Model):
    primary_track: str = "UNCLASSIFIED_REVIEW"
    secondary_tracks: list[str] = Field(default_factory=list)
    cohorts: list[str] = Field(default_factory=list)
    lot_class: str = "UNKNOWN"
    basis: list[str] = Field(default_factory=list)
    confidence: str = "UNKNOWN"


class HoldingLedger(_V2Model):
    house_utility_band: str = "UNKNOWN"
    holding_condition_band: str = "UNKNOWN"
    survival_capex_low: float | None = None
    survival_capex_base: float | None = None
    survival_capex_high: float | None = None
    cash_holding_basis_low: float | None = None
    cash_holding_basis_base: float | None = None
    cash_holding_basis_high: float | None = None
    house_value_reference: float | None = None
    house_value_reference_type: str = "BCA_IMPROVEMENT_SANITY_ANCHOR"
    updated_components: list[str] = Field(default_factory=list)
    kitchen_count_signal: int | None = None
    layout_signal: str = "UNKNOWN"
    risks: list[str] = Field(default_factory=list)


class LocationLedger(_V2Model):
    external_provider: dict[str, Any] = Field(default_factory=dict)
    school: dict[str, Any] = Field(default_factory=dict)
    area_baseline_prior: dict[str, Any] = Field(default_factory=dict)
    parcel_facts: dict[str, Any] = Field(default_factory=dict)
    road_adjustment: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)


class DevelopmentScenario(_V2Model):
    scenario_name: ScenarioName
    product_type: str = "UNKNOWN"
    legal_status: str = "UNKNOWN"
    unit_count: float | None = None
    theoretical_area: float | None = None
    realistic_area: float | None = None
    access_status: str = "UNKNOWN"
    exit_value: float | None = None
    hard_cost: float | None = None
    soft_cost: float | None = None
    selling_cost: float | None = None
    developer_margin: float | None = None
    total_cost: float | None = None
    ralv: float | None = None
    cash_mos: float | None = None
    blocking_conditions: list[str] = Field(default_factory=list)
    confidence: str = "UNKNOWN"
    rule_basis: dict[str, Any] = Field(default_factory=dict)


class AcquisitionLedger(_V2Model):
    asking: float | None = None
    assessment_total: float | None = None
    assessment_land: float | None = None
    assessment_improvement: float | None = None
    assessment_signals: dict[str, Any] = Field(default_factory=dict)
    market_land_basis: float | None = None
    location_baseline_prior_cad: float | None = None
    normalized_physical_lot_basis: float | None = None
    normalized_lot_basis_per_sqft: float | None = None
    base_cash_mos: float | None = None
    conditional_cash_mos: float | None = None
    upside_cash_mos: float | None = None
    price_risk_flags: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class StrategyV2Property(_V2Model):
    property_key: str
    address: str
    mls: str | None = None
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    asset_classification: AssetClassification
    gates: list[GateFinding]
    holding: HoldingLedger
    location: LocationLedger
    development_potential: float | None = None
    development_geometry_score: float | None = None
    development_entitlement_score: float | None = None
    development_potential_proxy: float | None = None
    development_scenarios: list[DevelopmentScenario] = Field(default_factory=list)
    acquisition: AcquisitionLedger
    legacy_reference: dict[str, Any] = Field(default_factory=dict)
    ranking_status: str = "NOT_RUN"
    human_annotation_status: str = "NOT_ANNOTATED"


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _raw(card: Any) -> Mapping[str, Any]:
    value = _get(card, "raw", {})
    return value if isinstance(value, Mapping) else {}


def _present(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().upper() in {"UNKNOWN", "N/A", "NA", "NULL", "NONE", "UNAVAILABLE"}:
        return False
    return True


def _first(card: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = _get(card, name, None)
        if _present(value):
            return value
        value = _raw(card).get(name)
        if _present(value):
            return value
    return default


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("&amp;", "and").split())


def _description(card: Any) -> str:
    return _text(_first(card, "description", default=""))


def _config(config: Mapping[str, Any] | str | Path | None) -> Mapping[str, Any]:
    if isinstance(config, Mapping):
        return config
    path = Path(config) if config else DEFAULT_CONFIG_PATH
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _text_blob(card: Any) -> str:
    values = [
        _first(card, "ownership_type", default=""),
        _first(card, "property_type", default=""),
        _first(card, "listing_status", default=""),
        _description(card),
        str(_raw(card).get("approved_use") or ""),
        str(_raw(card).get("title_status") or ""),
    ]
    return " ".join(_text(item) for item in values)


def _has_any(blob: str, terms: Sequence[str]) -> bool:
    return any(term in blob for term in terms)


def _lot_dimensions(card: Any) -> tuple[float | None, float | None]:
    frontage = _number(_first(card, "official_frontage_ft", "frontage_ft"))
    depth = _number(_first(card, "official_depth_ft", "depth_ft"))
    if frontage is not None and depth is not None:
        return frontage, depth
    raw = _first(card, "lot_dimensions", default="")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|×|by)\s*(\d+(?:\.\d+)?)", str(raw), re.I)
    if match:
        return float(match.group(1)), float(match.group(2))
    return frontage, depth


def _lot_area(card: Any) -> float | None:
    return _number(_first(card, "lot_area_sqft"))


def _assessment(card: Any, name: str) -> float | None:
    return _number(_first(card, f"official_assessment_{name}", f"assessment_{name}"))


def _official_zoning(card: Any) -> str | None:
    value = _first(card, "official_current_zoning", "official_zoning", "current_zoning")
    return str(value).strip().upper() if _present(value) else None


def _zoning_result(card: Any, rules_path: str | Path | None = None) -> dict[str, Any]:
    frontage, depth = _lot_dimensions(card)
    return zoning_scenarios(
        zoning=_official_zoning(card),
        municipality=_first(card, "municipality"),
        lot_area_sqft=_lot_area(card),
        frontage_ft=frontage,
        depth_ft=depth,
        # Listing language such as "lane" is evidence, not a survey or
        # official rear-access confirmation.  Keep the legal gate open.
        lane_access=None,
        config_path=rules_path or DEFAULT_RULES_PATH,
    )


def _observed_lane(card: Any) -> bool | None:
    direct = _first(card, "lane_access", "rear_access")
    if isinstance(direct, bool):
        return direct
    blob = _description(card)
    if _has_any(blob, ("back lane", "rear lane", "lane access", "laneway")):
        return True
    if re.search(r"\bno lane\b|\bwithout lane\b", blob):
        return False
    return None


def _kitchen_signal(card: Any) -> int | None:
    raw = _raw(card).get("kitchen_count")
    value = _number(raw)
    if value is not None:
        return int(value)
    blob = _description(card)
    patterns = (
        r"\b(\d+)\s+(?:full\s+)?kitchens?\b",
        r"\b(\d+)\s+kitchen\b",
    )
    for pattern in patterns:
        match = re.search(pattern, blob)
        if match:
            return int(match.group(1))
    if _has_any(blob, ("second kitchen", "two kitchens", "2 kitchens", "multiple kitchens")):
        return 2
    if _has_any(blob, ("garden suite", "secondary suite", "legal suite", "basement suite")):
        # A suite is not assumed to be an extra main-house kitchen; retain a
        # signal only when the listing actually says kitchen.
        if "kitchen" in blob:
            return 2
    return None


def _has_partial_or_special_title(card: Any) -> tuple[bool, list[str]]:
    blob = _text_blob(card)
    flags: list[str] = []
    patterns = {
        "PARTIAL_INTEREST": ("partial interest", "partial ownership", "share of", "2/3 ownership", "one half interest"),
        "UNDIVIDED_INTEREST": ("undivided interest", "undivided ownership"),
        "FRACTIONAL_OWNERSHIP": ("fractional ownership", "fractional interest"),
        "LEASEHOLD": ("leasehold", "prepaid lease", "non prepaid lease"),
        "COURT_ORDERED": ("court ordered", "court-ordered", "court sale", "foreclosure", "foreclosed"),
        "LITIGATION": ("serious litigation", "subject to litigation"),
    }
    for flag, terms in patterns.items():
        if _has_any(blob, terms):
            flags.append(flag)
    ownership = _normalized(_first(card, "ownership_type", default=""))
    if "lease" in ownership and "LEASEHOLD" not in flags:
        flags.append("LEASEHOLD")
    if "partial" in ownership and "PARTIAL_INTEREST" not in flags:
        flags.append("PARTIAL_INTEREST")
    return bool(flags), flags


def _severe_condition_flags(card: Any) -> list[str]:
    blob = _description(card)
    flags: list[str] = []
    if _has_any(blob, ("fire damaged", "fire damage", "burned", "burnt", "collapsed", "collapse", "major foundation failure", "contaminated")):
        flags.append("SEVERE_CONDITION")
    if _has_any(blob, ("vacant possession impossible", "tenant will not vacate", "no vacant possession")):
        flags.append("VACANT_POSSESSION")
    return flags


def _has_development_entitlement(card: Any, rules_path: str | Path | None = None) -> bool:
    result = _zoning_result(card, rules_path)
    return result.get("rule_status") == "OFFICIAL_RULE_APPLIED" and (
        result.get("best_product") not in (None, "UNKNOWN")
        or result.get("duplex_permitted") is True
    )


def _is_family_house(card: Any) -> bool:
    ptype = _normalized(_first(card, "property_type", default=""))
    return any(token in ptype for token in ("single family", "single detached", "detached", "house")) and "townhouse" not in ptype and "condo" not in ptype


def _house_utility_plausible(card: Any) -> bool:
    beds = _number(_first(card, "bedrooms"))
    baths = _number(_first(card, "bathrooms"))
    area = _number(_first(card, "finished_sqft"))
    return beds is not None and beds >= 3 and baths is not None and baths >= 2 and area is not None and area >= 1500


def _small_lot_premium_context(card: Any) -> bool:
    area = _lot_area(card)
    if area is None or area > 4150:
        return False
    neighbourhood = _normalized(_first(card, "neighbourhood", default=""))
    premium_names = ("point grey", "kitsilano", "arbutus", "dunbar", "kerrisdale", "cambie", "shaughnessy", "mount pleasant", "main", "riley park")
    frontage, _ = _lot_dimensions(card)
    description = _description(card)
    return any(name in neighbourhood for name in premium_names) or (frontage is not None and frontage >= 40) or _has_any(description, ("triplex", "premium location", "land value"))


def _lot_class(card: Any) -> str:
    area = _lot_area(card)
    frontage, depth = _lot_dimensions(card)
    if area is None:
        return "UNKNOWN"
    if area < 2800:
        return "BELOW_HARD_MINIMUM"
    if area < 3900:
        return "SUBSTANDARD"
    if area <= 4150 and (
        (frontage is not None and 32.5 <= frontage <= 35.0)
        or (depth is not None and 118.0 <= depth <= 125.0)
        or 4026 <= area <= 4150
    ):
        return "STANDARD_REFERENCE"
    return "LARGE_OR_NONSTANDARD"


def _classification_cohorts(card: Any) -> tuple[list[str], str]:
    lot_class = _lot_class(card)
    cohorts: list[str] = []
    if lot_class in {"SUBSTANDARD", "STANDARD_REFERENCE"} and _small_lot_premium_context(card):
        cohorts.append("PREMIUM_LOT")
    return cohorts, lot_class


def classify_asset(
    card: Any,
    config: Mapping[str, Any] | str | Path | None = None,
    *,
    rules_path: str | Path | None = None,
) -> AssetClassification:
    """Classify from title, house utility, lot, and official entitlement facts."""

    flags, title_flags = _has_partial_or_special_title(card)
    severe = _severe_condition_flags(card)
    if flags or severe:
        basis = title_flags + severe
        return AssetClassification(
            primary_track="SPECIAL_SITUATION",
            cohorts=[],
            lot_class=_lot_class(card),
            basis=basis,
            confidence="HIGH" if title_flags else "MEDIUM",
        )

    zoning = _zoning_result(card, rules_path)
    has_entitlement = _has_development_entitlement(card, rules_path)
    area = _lot_area(card)
    utility = _house_utility_plausible(card)
    kitchens = _kitchen_signal(card)
    blob = _text_blob(card)
    basis: list[str] = []
    secondary: list[str] = []
    cohorts, lot_class = _classification_cohorts(card)

    if kitchens is not None and kitchens >= 2:
        secondary.append("INCOME_HOLD")
        basis.append("MULTI_KITCHEN_SIGNAL")
    elif _has_any(blob, ("legal suite", "secondary suite", "duplex", "rental income", "mortgage helper")):
        secondary.append("INCOME_HOLD")
        basis.append("SUITE_OR_INCOME_SIGNAL")

    if area is not None and area < 4026 and _small_lot_premium_context(card):
        # A sub-standard parcel in a premium local product market is a
        # distinct underwriting cohort even when its existing house happens
        # to have enough bedrooms.  Do not let bedroom count turn a
        # land-value/small-lot case into an ordinary HOME_REBUILD case.
        primary = "PREMIUM_SMALL_LOT"
        basis.append("SUBSTANDARD_LOT_WITH_LOCAL_PRODUCT_CONTEXT")
    elif area is not None and area < 2800:
        primary = "UNCLASSIFIED_REVIEW"
        basis.append("BELOW_HARD_LOT_REFERENCE")
    elif has_entitlement and not utility:
        primary = "LAND_DEVELOPMENT"
        basis.extend(("OFFICIAL_ENTITLEMENT", "WEAK_CURRENT_HOUSE_UTILITY"))
    elif utility and has_entitlement:
        primary = "HOME_REBUILD"
        basis.extend(("FAMILY_HOLDING_UTILITY", "OFFICIAL_REBUILD_PATH"))
    elif utility:
        primary = "HOME_REBUILD"
        basis.append("FAMILY_HOLDING_UTILITY")
    elif has_entitlement and area is not None and area >= 4026:
        primary = "LAND_DEVELOPMENT"
        basis.append("ENTITLEMENT_AND_STANDARD_OR_LARGE_LOT")
    else:
        primary = "UNCLASSIFIED_REVIEW"
        basis.append("INSUFFICIENT_TRACK_EVIDENCE")

    if primary == "HOME_REBUILD" and "INCOME_HOLD" in secondary and kitchens is not None and kitchens >= 2:
        basis.append("FAMILY_USE_WITH_INCOME_LAYOUT_REVIEW")
    if primary == "LAND_DEVELOPMENT" and "INCOME_HOLD" in secondary:
        secondary.remove("INCOME_HOLD")
        basis.append("DEVELOPMENT_PRIMARY_INCOME_SIGNAL_SECONDARY")

    # De-duplicate while retaining order.
    secondary = list(dict.fromkeys(item for item in secondary if item != primary))
    confidence = "HIGH" if has_entitlement and area is not None else "MEDIUM" if utility or area is not None else "LOW"
    if "PREMIUM_LOT" in cohorts:
        basis.append("PREMIUM_LOT_COHORT")
    return AssetClassification(
        primary_track=primary,
        secondary_tracks=secondary,
        cohorts=cohorts,
        lot_class=lot_class,
        basis=basis,
        confidence=confidence,
    )


def evaluate_gates(card: Any, config: Mapping[str, Any] | str | Path | None = None, *, rules_path: str | Path | None = None) -> list[GateFinding]:
    """Evaluate non-compensatory gates without turning them into points."""

    gates: list[GateFinding] = []
    special, title_flags = _has_partial_or_special_title(card)
    if special:
        gates.append(GateFinding(code="TITLE_OR_TRANSACTION", status="FAIL", evidence=title_flags, explanation="Non-standard title or transaction terms require the special-situation track."))
    else:
        ownership = _normalized(_first(card, "ownership_type", default=""))
        status: GateStatus = "PASS" if "freehold" in ownership and "strata" not in ownership else "REVIEW" if not ownership else "FAIL" if "lease" in ownership or "strata" in ownership else "REVIEW"
        gates.append(GateFinding(code="TITLE_OR_TRANSACTION", status=status, evidence=[str(_first(card, "ownership_type", default="UNKNOWN"))], explanation="Listing tenure is a scope signal; unresolved title requires review."))

    scope = _is_family_house(card)
    gates.append(GateFinding(code="DETACHED_FREEHOLD_SCOPE", status="PASS" if scope else "FAIL", evidence=[str(_first(card, "property_type", default="UNKNOWN"))], explanation="The normal V2 scope is detached/freehold house research."))

    severe = _severe_condition_flags(card)
    gates.append(GateFinding(code="SEVERE_CONDITION", status="FAIL" if severe else "PASS", evidence=severe or ["no explicit severe-condition signal"], explanation="Only explicit severe condition language fails this gate."))

    raw = _raw(card)
    conflicts = raw.get("_official_conflicts")
    conflict_items = list(conflicts) if isinstance(conflicts, Sequence) and not isinstance(conflicts, (str, bytes)) else []
    gates.append(GateFinding(code="DATA_CONFLICT", status="REVIEW" if conflict_items else "PASS", evidence=[str(item) for item in conflict_items[:5]] or ["none recorded"], explanation="Conflicts are reviewed; they do not become an arbitrary score deduction."))

    zoning = _zoning_result(card, rules_path)
    if zoning.get("rule_status") == "OFFICIAL_RULE_APPLIED":
        gates.append(GateFinding(code="OFFICIAL_ENTITLEMENT", status="PASS", evidence=[str(zoning.get("zoning"))], explanation="A registered municipal rule was found; site conditions remain separate."))
    else:
        gates.append(GateFinding(code="OFFICIAL_ENTITLEMENT", status="REVIEW", evidence=[str(_official_zoning(card) or "UNKNOWN")], explanation="No registered official rule path was established."))

    required_access = zoning.get("rear_access_required") is True
    observed_lane = _observed_lane(card)
    if required_access:
        status = "REVIEW"
        evidence = ["rear access required by official rule", f"listing/lane observation={observed_lane if observed_lane is not None else 'UNKNOWN'}"]
        gates.append(GateFinding(code="REAR_ACCESS", status=status, evidence=evidence, explanation="Listing lane language is not a survey or official rear-vehicular-access confirmation."))
    else:
        gates.append(GateFinding(code="REAR_ACCESS", status="PASS", evidence=["not required by registered rule"], explanation="No rear-access condition was found in the registered rule."))

    road_class = str(_first(card, "official_road_class", default="")).strip().upper()
    direct = _first(card, "official_property_fronts_major_road")
    if road_class in {"A", "B", "C"} or direct is True:
        gates.append(GateFinding(code="PERMANENT_ROAD_EXPOSURE", status="REVIEW", evidence=[f"road_class={road_class or 'UNKNOWN'}", f"direct={direct if direct is not None else 'UNKNOWN'}"], explanation="Road exposure is a permanent risk and is reviewed rather than automatically rejected."))
    else:
        gates.append(GateFinding(code="PERMANENT_ROAD_EXPOSURE", status="PASS", evidence=[f"road_class={road_class or 'UNKNOWN'}"], explanation="No official arterial exposure was recorded."))
    return gates


def _holding_band(year: float | None) -> str:
    if year is None:
        return "unknown"
    if year < 1960:
        return "pre_1960"
    if year < 1980:
        return "1960_1979"
    if year < 2000:
        return "1980_1999"
    if year <= 2015:
        return "2000_2015"
    return "post_2015"


_UPDATE_COMPONENTS: dict[str, tuple[str, ...]] = {
    "ROOF": ("roof", "roofing"),
    "WINDOWS": ("window", "double glazed", "double-glazed"),
    "HOT_WATER": ("hot water", "hot-water", "tankless water"),
    "ELECTRICAL": ("electrical", "rewired", "wiring", "200 amp"),
    "PLUMBING": ("plumbing", "re-piped", "repiped", "pipes replaced"),
    "HVAC": ("furnace", "heat pump", "boiler", "forced air"),
    "DRAINAGE": ("drainage", "perimeter drain", "drain tile"),
    "FOUNDATION": ("foundation repaired", "foundation upgrade", "concrete perimeter"),
    "SEWER": ("sewer replaced", "sewer line", "sewer upgrade"),
    "FIRE_SPRINKLER": ("fire sprinkler", "sprinkler system"),
}


def _updated_components(card: Any) -> list[str]:
    blob = _description(card)
    positive_context = _has_any(blob, ("updated", "update", "replaced", "new ", "newly", "recent", "renovated", "renovation", "upgrade", "upgraded", "installed"))
    result: list[str] = []
    for component, terms in _UPDATE_COMPONENTS.items():
        if _has_any(blob, terms) and positive_context:
            # Do not treat "roof needs replacement" as completed evidence.
            if _has_any(blob, ("needs new", "need new", "needs replacement", "replace roof", "as-is", "not updated")) and component in {"ROOF", "ELECTRICAL", "PLUMBING", "HVAC", "DRAINAGE", "FOUNDATION", "SEWER"}:
                continue
            result.append(component)
    return result


def _survival_reserve(card: Any, config: Mapping[str, Any]) -> tuple[float, float, float, list[str]]:
    holding = config.get("holding") if isinstance(config.get("holding"), Mapping) else {}
    bands = holding.get("survival_capex_bands") if isinstance(holding.get("survival_capex_bands"), Mapping) else {}
    band_name = _holding_band(_number(_first(card, "year_built", "official_year_built")))
    band = bands.get(band_name) if isinstance(bands.get(band_name), Mapping) else bands.get("unknown", {})
    if not isinstance(band, Mapping):
        band = {"low": 60000, "base": 120000, "high": 180000}
    values = {key: float(_number(band.get(key)) or 0.0) for key in ("low", "base", "high")}
    reserves = holding.get("component_reserves") if isinstance(holding.get("component_reserves"), Mapping) else {}
    updated = _updated_components(card)
    notes: list[str] = []
    for component in updated:
        reduction = float(_number(reserves.get(component)) or 0.0)
        if reduction:
            values = {key: max(0.0, value - reduction) for key, value in values.items()}
            notes.append(f"{component}_RESERVE_REDUCED_BY_LISTING_EVIDENCE")
    return values["low"], values["base"], values["high"], notes


def build_holding_ledger(card: Any, config: Mapping[str, Any] | str | Path | None = None) -> HoldingLedger:
    cfg = _config(config)
    low, base, high, notes = _survival_reserve(card, cfg)
    asking = _number(_first(card, "asking"))
    house_value = _assessment(card, "improvement")
    beds = _number(_first(card, "bedrooms"))
    baths = _number(_first(card, "bathrooms"))
    area = _number(_first(card, "finished_sqft"))
    kitchens = _kitchen_signal(card)
    updated = _updated_components(card)
    if beds is not None and beds >= 3 and baths is not None and baths >= 2 and area is not None and area >= 1800:
        utility = "GOOD"
    elif beds is not None and beds >= 3 and baths is not None and baths >= 2 and area is not None and area >= 1500:
        utility = "ACCEPTABLE"
    elif baths is not None and baths < 2 or area is not None and area < 1250:
        utility = "POOR"
    else:
        utility = "UNKNOWN"

    if base <= 60000:
        condition = "GOOD"
    elif base <= 100000:
        condition = "ACCEPTABLE"
    elif _number(_first(card, "year_built", "official_year_built")) is None:
        condition = "UNKNOWN"
    else:
        condition = "POOR"
    risks = list(notes)
    if not updated and _number(_first(card, "year_built", "official_year_built")) is not None and _number(_first(card, "year_built", "official_year_built")) < 1980:
        risks.append("UNMENTIONED_MAJOR_SYSTEMS_RETAIN_RESERVE")
    if kitchens is not None and kitchens >= 2:
        risks.append("MULTI_KITCHEN_LAYOUT_REVIEW")
        layout_signal = "INCOME_OR_MULTI_UNIT_LAYOUT"
    elif _has_any(_description(card), ("legal suite", "secondary suite", "garden suite", "mortgage helper")):
        layout_signal = "FAMILY_HOUSE_WITH_SUITE"
    else:
        layout_signal = "FAMILY_LAYOUT_OR_UNKNOWN"
    return HoldingLedger(
        house_utility_band=utility,
        holding_condition_band=condition,
        survival_capex_low=round(low, 2),
        survival_capex_base=round(base, 2),
        survival_capex_high=round(high, 2),
        cash_holding_basis_low=round(asking + low, 2) if asking is not None else None,
        cash_holding_basis_base=round(asking + base, 2) if asking is not None else None,
        cash_holding_basis_high=round(asking + high, 2) if asking is not None else None,
        house_value_reference=house_value,
        updated_components=updated,
        kitchen_count_signal=kitchens,
        layout_signal=layout_signal,
        risks=risks,
    )


def _load_provider_snapshot(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _exposure_factor(distance: float | None, direct: bool | None, cfg: Mapping[str, Any]) -> float | None:
    if direct is True or (distance is not None and distance <= 10):
        return float(_number(cfg.get("direct_or_0_10m")) or 1.0)
    if distance is None:
        return None
    if distance <= 25:
        return float(_number(cfg.get("over_10_25m")) or 0.75)
    if distance <= 50:
        return float(_number(cfg.get("over_25_50m")) or 0.50)
    if distance <= 75:
        return float(_number(cfg.get("over_50_75m")) or 0.25)
    return float(_number(cfg.get("over_75m")) or 0.0)


def _road_category(card: Any, legacy_row: Mapping[str, Any] | None) -> tuple[str, str]:
    explicit = _first(card, "official_road_category", "official_road_type")
    if _present(explicit):
        return str(explicit).upper(), "OFFICIAL_CARD_ROAD_CATEGORY"
    road_class = str(_first(card, "official_road_class", default="")).strip().upper()
    official_category = {"A": "ARTERIAL_TRUCK_INTERSECTION", "B": "ORDINARY_ARTERIAL", "C": "ORDINARY_ARTERIAL", "D": "COLLECTOR", "E": "LOCAL_RESIDENTIAL"}.get(road_class)
    if official_category is not None:
        return official_category, "OFFICIAL_ROAD_CLASS"
    if legacy_row and _present(legacy_row.get("road_category")):
        return str(legacy_row["road_category"]).upper(), "LEGACY_REFERENCE_FALLBACK"
    return "UNKNOWN", "UNKNOWN"


def _road_prior(category: str, cfg: Mapping[str, Any]) -> tuple[float | None, float | None, float | None]:
    values = cfg.get("road_discount_priors_pct") if isinstance(cfg.get("road_discount_priors_pct"), Mapping) else {}
    value = values.get(category) if isinstance(values, Mapping) else None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 3:
        return None, None, None
    return tuple(_number(item) for item in value[:3])  # type: ignore[return-value]


def _location_provider_dict(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {
            "provider": "walk_score",
            "status": "UNKNOWN",
            "walk_score_status": "UNKNOWN",
            "walk": None,
            "transit": None,
            "bike": None,
            "composite": None,
            "source": "WALK_SCORE_API_NOT_AVAILABLE",
        }
    walk = snapshot.get("walk_score") if isinstance(snapshot.get("walk_score"), Mapping) else {}
    scores = walk.get("scores") if isinstance(walk, Mapping) and isinstance(walk.get("scores"), Mapping) else {}
    composite = snapshot.get("composite") if isinstance(snapshot.get("composite"), Mapping) else {}
    status = str(walk.get("status") or "UNKNOWN") if isinstance(walk, Mapping) else "UNKNOWN"
    return {
        "provider": "walk_score",
        "status": status,
        "walk_score_status": status,
        "walk": _number(scores.get("walk")),
        "transit": _number(scores.get("transit")),
        "bike": _number(scores.get("bike")),
        "composite": _number(composite.get("external_location_score")) if isinstance(composite, Mapping) else None,
        "model_name": snapshot.get("model_name"),
        "model_version": snapshot.get("model_version"),
        "queried_at": snapshot.get("queried_at"),
    }


def build_location_ledger(
    card: Any,
    *,
    legacy_row: Mapping[str, Any] | None = None,
    provider_snapshot: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | str | Path | None = None,
) -> LocationLedger:
    cfg = _config(config)
    loc_cfg = cfg.get("location") if isinstance(cfg.get("location"), Mapping) else {}
    raw = _raw(card)
    road_class = str(_first(card, "official_road_class", default="")).strip().upper() or None
    distance = _number(_first(card, "official_distance_to_major_road_m", "road_distance_m"))
    direct = _first(card, "official_property_fronts_major_road")
    direct = direct if isinstance(direct, bool) else None
    category, category_source = _road_category(card, legacy_row)
    priors_cfg = loc_cfg.get("exposure_factor") if isinstance(loc_cfg.get("exposure_factor"), Mapping) else {}
    factor = _exposure_factor(distance, direct, priors_cfg)
    low, mid, high = _road_prior(category, loc_cfg)
    observed_lane = _observed_lane(card)
    description = _description(card)
    parcel = {
        "property_fronts_major_road": direct,
        "nearest_major_road": _first(card, "official_nearest_major_road", "nearest_major_road"),
        "road_class": road_class,
        "distance_to_major_road_m": distance,
        "park_facing": bool(_has_any(description, ("park-facing", "park facing", "overlooking foster park", "overlooking carn ar von", "overlooking carn ar von park", "facing the park", "facing park"))),
        "view_type": "WATER" if _has_any(description, ("water view", "ocean view", "beach view")) else "MOUNTAIN_CITY" if _has_any(description, ("mountain view", "city skyline", "north shore mountains")) else "NONE",
        "view_strength": "STRONG" if _has_any(description, ("panoramic", "stunning panoramic", "unobstructed")) else "GOOD" if _has_any(description, ("view", "outlook")) else "NONE",
        "waterfront": bool(_has_any(description, ("waterfront", "water front"))),
        "corner_lot": _raw(card).get("corner_lot") if isinstance(_raw(card).get("corner_lot"), bool) else None,
        "lane_observed": observed_lane,
        "slope": _raw(card).get("slope", "UNKNOWN"),
    }
    school = {
        "elementary_catchment": _first(card, "official_elementary_catchment", "elementary_school"),
        "secondary_catchment": _first(card, "official_secondary_catchment", "secondary_school"),
        "elementary_performance": raw.get("official_elementary_performance"),
        "secondary_performance": raw.get("official_secondary_performance"),
        "quality_tier": "UNKNOWN",
        "premium_cad": None,
        "status": "OFFICIAL_CATCHMENT_ONLY" if _present(raw.get("official_elementary_catchment")) or _present(raw.get("official_secondary_catchment")) else "UNKNOWN",
    }
    prior = _number((legacy_row or {}).get("location_residual_cad"))
    area_prior = {
        "neighbourhood": _first(card, "neighbourhood"),
        "value_cad": prior,
        "methodology": "LEGACY_LOCATION_BASELINE_PRIOR_NOT_MARKET_CONFIRMED" if prior is not None else "UNKNOWN",
        "confidence": "LOW" if prior is not None else "UNKNOWN",
    }
    road = {
        "category": category,
        "category_source": category_source,
        "discount_low_pct": low,
        "discount_base_pct": mid,
        "discount_high_pct": high,
        "exposure_factor": factor,
        "effective_discount_low_pct": round(low * factor, 6) if low is not None and factor is not None else None,
        "effective_discount_base_pct": round(mid * factor, 6) if mid is not None and factor is not None else None,
        "effective_discount_high_pct": round(high * factor, 6) if high is not None and factor is not None else None,
        "methodology": "ROAD_PRIOR_BY_CATEGORY_AND_DISTANCE; NOT_MARKET_CONFIRMED",
    }
    risks: list[str] = []
    if direct is True:
        risks.append("DIRECT_MAJOR_ROAD_EXPOSURE")
    if category in {"ARTERIAL_BUS_TRUCK", "ARTERIAL_TRUCK_INTERSECTION"}:
        risks.append("HIGH_PERMANENT_NUISANCE_PROFILE")
    if _number(road.get("effective_discount_base_pct")) is not None and _number(road.get("effective_discount_base_pct")) > 0:
        risks.append("ROAD_DISCOUNT_PRIOR_APPLIED_ONCE_IN_LOCATION_LEDGER")
    return LocationLedger(
        external_provider=_location_provider_dict(_load_provider_snapshot(provider_snapshot)),
        school=school,
        area_baseline_prior=area_prior,
        parcel_facts=parcel,
        road_adjustment=road,
        risks=risks,
    )


def _development_potential_components_from_zoning(card: Any, zoning: Mapping[str, Any]) -> dict[str, Any]:
    area = _lot_area(card)
    frontage, depth = _lot_dimensions(card)
    if area is None and frontage is None and depth is None:
        return {
            "area_score": None,
            "frontage_score": None,
            "depth_score": None,
            "geometry_score": None,
            "entitlement_score": None,
            "development_potential_proxy": None,
        }

    def area_score(value: float | None) -> float:
        if value is None:
            return 50.0
        for ceiling, score in ((2800, 10), (3300, 25), (3600, 40), (4000, 60), (5000, 70), (6000, 85), (float("inf"), 100)):
            if value < ceiling:
                return float(score)
        return 100.0

    def frontage_score(value: float | None) -> float:
        if value is None:
            return 50.0
        for ceiling, score in ((25, 15), (30, 35), (33, 55), (44, 70), (49.5, 85), (float("inf"), 100)):
            if value < ceiling:
                return float(score)
        return 100.0

    def depth_score(value: float | None) -> float:
        if value is None:
            return 50.0
        for ceiling, score in ((90, 25), (100, 40), (115, 65), (125, 75), (float("inf"), 85)):
            if value < ceiling:
                return float(score)
        return 85.0

    area_value = area_score(area)
    frontage_value = frontage_score(frontage)
    depth_value = depth_score(depth)
    geometry = area_value * 0.40 + frontage_value * 0.35 + depth_value * 0.25
    units = _number(zoning.get("multiplex_max_units")) or _number(zoning.get("maximum_units"))
    if units is None and zoning.get("duplex_permitted") is True:
        units = 2
    entitlement = 0.0 if zoning.get("rule_status") != "OFFICIAL_RULE_APPLIED" else min(90.0, 20.0 + (units or 0.0) * 12.5)
    if _number(zoning.get("maximum_fsr")) is not None and _number(zoning.get("maximum_fsr")) >= 1.0:
        entitlement = min(100.0, entitlement + 10.0)
    if zoning.get("rear_access_required") is True and _observed_lane(card) is None:
        entitlement *= 0.85
    proxy = round(max(0.0, min(100.0, geometry * 0.45 + entitlement * 0.55)), 2)
    return {
        "area_score": round(area_value, 2),
        "frontage_score": round(frontage_value, 2),
        "depth_score": round(depth_value, 2),
        "geometry_score": round(geometry, 2),
        "entitlement_score": round(entitlement, 2),
        "development_potential_proxy": proxy,
    }


def development_potential_components(
    card: Any,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return transparent geometry/entitlement components for comparison.

    The result is a planning/geometry proxy only.  It is intentionally not a
    development profit estimate and must not be used as RALV.
    """

    return _development_potential_components_from_zoning(card, _zoning_result(card, rules_path))


def _development_potential(card: Any, zoning: Mapping[str, Any]) -> float | None:
    return _development_potential_components_from_zoning(card, zoning)["development_potential_proxy"]


def _scenario(
    name: ScenarioName,
    *,
    zoning: Mapping[str, Any],
    card: Any,
    holding: HoldingLedger | None = None,
) -> DevelopmentScenario:
    best = str(zoning.get("best_product") or "UNKNOWN")
    access_required = zoning.get("rear_access_required") is True
    observed_lane = _observed_lane(card)
    if not best or best == "UNKNOWN":
        return DevelopmentScenario(scenario_name=name, product_type="UNKNOWN", legal_status="UNKNOWN", confidence="UNKNOWN", blocking_conditions=["OFFICIAL_PRODUCT_PATH_UNKNOWN"])
    area = _number(zoning.get("theoretical_buildable_sqft"))
    realistic = _number(zoning.get("realistic_buildable_sqft"))
    units = _number(zoning.get("multiplex_max_units")) or _number(zoning.get("maximum_units"))
    if best == "DUPLEX":
        units = 2
    if name == "BASE":
        if access_required and observed_lane is not True:
            return DevelopmentScenario(
                scenario_name=name,
                product_type="CONSERVATIVE_" + ("DUPLEX" if zoning.get("duplex_permitted") else best),
                legal_status="REVIEW_REQUIRED",
                access_status="REVIEW_REQUIRED",
                confidence="MEDIUM",
                blocking_conditions=["REAR_ACCESS_CONFIRMATION_REQUIRED"],
                rule_basis=dict(zoning),
            )
        return DevelopmentScenario(
            scenario_name=name,
            product_type=best,
            legal_status="CURRENT_OFFICIAL_RULE",
            unit_count=units,
            theoretical_area=area,
            realistic_area=realistic,
            access_status="OBSERVED_OR_NOT_REQUIRED",
            confidence="MEDIUM",
            rule_basis=dict(zoning),
        )
    if name == "CONDITIONAL":
        blockers = []
        status = "CONDITIONAL_SITE_REVIEW"
        if access_required and observed_lane is not True:
            blockers.append("REAR_ACCESS_CONFIRMATION_REQUIRED")
        return DevelopmentScenario(
            scenario_name=name,
            product_type=best,
            legal_status=status,
            unit_count=units,
            theoretical_area=area,
            realistic_area=realistic,
            access_status="REQUIRES_SURVEY_OR_OFFICIAL_CONFIRMATION" if access_required else "NOT_REQUIRED",
            confidence="LOW" if blockers else "MEDIUM",
            blocking_conditions=blockers,
            rule_basis=dict(zoning),
        )
    upside_signals = _has_any(_description(card), ("toa", "transit oriented", "assembly", "rezoning", "8 storey", "8-storey", "3.0 fsr"))
    return DevelopmentScenario(
        scenario_name="UPSIDE",
        product_type="TOA_OR_ASSEMBLY" if upside_signals else "POLICY_OR_ASSEMBLY_OPTION",
        legal_status="OPTION_ONLY_NOT_IN_BASE",
        access_status="UNKNOWN",
        confidence="LOW" if upside_signals else "UNKNOWN",
        blocking_conditions=["NOT_CURRENT_ENTITLEMENT", "NO_UPSIDE_ECONOMICS_INPUT"],
        rule_basis={"signal_observed": upside_signals},
    )


def build_development_scenarios(
    card: Any,
    *,
    config: Mapping[str, Any] | str | Path | None = None,
    rules_path: str | Path | None = None,
    holding: HoldingLedger | None = None,
) -> list[DevelopmentScenario]:
    zoning = _zoning_result(card, rules_path)
    return [_scenario(name, zoning=zoning, card=card, holding=holding) for name in ("BASE", "CONDITIONAL", "UPSIDE")]


def _discount(asking: float | None, value: float | None) -> float | None:
    if asking is None or value is None or value == 0:
        return None
    return round(1.0 - asking / value, 6)


def build_acquisition_ledger(
    card: Any,
    *,
    holding: HoldingLedger,
    scenarios: Sequence[DevelopmentScenario],
    location: LocationLedger,
    legacy_row: Mapping[str, Any] | None = None,
) -> AcquisitionLedger:
    asking = _number(_first(card, "asking"))
    total = _assessment(card, "total")
    land = _assessment(card, "land")
    improvement = _assessment(card, "improvement")
    total_discount = _discount(asking, total)
    land_discount = _discount(asking, land)
    market_land = asking - improvement if asking is not None and improvement is not None else None
    prior = _number(location.area_baseline_prior.get("value_cad"))
    normalized = market_land - prior if market_land is not None and prior is not None else None
    lot_area = _lot_area(card)
    price_flags: list[str] = []
    if total_discount is not None and total_discount > 0:
        price_flags.append("ASK_BELOW_ASSESSMENT")
    if total_discount is not None and total_discount >= 0.10:
        price_flags.append("ASK_10PCT_BELOW_ASSESSMENT")
    if land_discount is not None and land_discount > 0:
        price_flags.append("ASK_BELOW_LAND_ASSESSMENT")
    if total_discount is not None and total_discount >= 0.20:
        price_flags.append("ASSESSMENT_DISCOUNT_EXCEPTIONAL_SIGNAL")
    base_scenario = next((item for item in scenarios if item.scenario_name == "BASE"), None)
    conditional_scenario = next((item for item in scenarios if item.scenario_name == "CONDITIONAL"), None)
    upside_scenario = next((item for item in scenarios if item.scenario_name == "UPSIDE"), None)
    chb = holding.cash_holding_basis_base
    def mos(item: DevelopmentScenario | None) -> float | None:
        return round(item.ralv - chb, 2) if item is not None and item.ralv is not None and chb is not None else None
    return AcquisitionLedger(
        asking=asking,
        assessment_total=total,
        assessment_land=land,
        assessment_improvement=improvement,
        assessment_signals={
            "asking_vs_total_assessment_pct": total_discount,
            "asking_vs_land_assessment_pct": land_discount,
            "assessment_year": _first(card, "official_assessment_year", "assessment_year"),
        },
        market_land_basis=round(market_land, 2) if market_land is not None else None,
        location_baseline_prior_cad=prior,
        normalized_physical_lot_basis=round(normalized, 2) if normalized is not None else None,
        normalized_lot_basis_per_sqft=round(normalized / lot_area, 2) if normalized is not None and lot_area and lot_area > 0 else None,
        base_cash_mos=mos(base_scenario),
        conditional_cash_mos=mos(conditional_scenario),
        upside_cash_mos=mos(upside_scenario),
        price_risk_flags=price_flags,
        risks=["LOCATION_BASELINE_IS_PRIOR" if prior is not None else "LOCATION_BASELINE_UNKNOWN", "RALV_UNKNOWN_UNTIL_EXIT_AND_COST_INPUTS"],
    )


def _legacy_row_map(path: str | Path | None) -> dict[str, Mapping[str, Any]]:
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    rows = value.get("properties") if isinstance(value, Mapping) else None
    if not isinstance(rows, Sequence):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = row.get("property_key")
        address = row.get("address")
        mls = row.get("mls")
        if _present(key):
            result[str(key)] = row
        if _present(mls):
            result[f"mls:{mls}"] = row
        if _present(address):
            result[f"address:{' '.join(str(address).upper().split())}"] = row
    return result


def _provider_snapshot_map(path: str | Path | None = DEFAULT_LOCATION_SNAPSHOT_PATH) -> dict[str, Mapping[str, Any]]:
    if not path or not Path(path).is_file():
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, Mapping) and _present(row.get("property_id")):
            result[str(row["property_id"])] = row
    return result


def _v1_reference(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"status": "UNKNOWN"}
    target = Path(path)
    try:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return {"path": str(target), "status": "MISSING"}
    return {"path": str(target), "sha256": digest, "status": "FROZEN_READ_ONLY"}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    return value


def _source_provenance(card: Any) -> dict[str, Any]:
    """Keep the listing and enrichment evidence attached to each V2 row.

    The shadow model is intended to be auditable.  A source URL on the input
    card is not enough for official enrichment because each adapter may have
    used a different endpoint, timestamp, and match method.  Preserve those
    adapter records verbatim, grouped by adapter, without treating them as a
    confidence score.
    """

    raw = _raw(card)
    official: dict[str, list[dict[str, Any]]] = {}
    evidence = raw.get("_official_evidence")
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
        for item in evidence:
            if not isinstance(item, Mapping):
                continue
            adapter = str(item.get("adapter") or item.get("source_registry_id") or "UNKNOWN")
            official.setdefault(adapter, []).append(_json_safe(dict(item)))
    return _json_safe({
        "listing": {
            "url": _first(card, "source_url"),
            "source_type": _first(card, "source_name"),
            "retrieved_at": _first(card, "observed_at"),
            "mls": _first(card, "mls_number"),
        },
        "official": official,
        "official_evidence_count": sum(len(items) for items in official.values()),
    })


def build_shadow_property(
    card: Any,
    *,
    legacy_row: Mapping[str, Any] | None = None,
    legacy_reference: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | str | Path | None = None,
    rules_path: str | Path | None = None,
    provider_snapshot: Mapping[str, Any] | None = None,
) -> StrategyV2Property:
    classification = classify_asset(card, config, rules_path=rules_path)
    gates = evaluate_gates(card, config, rules_path=rules_path)
    holding = build_holding_ledger(card, config)
    location = build_location_ledger(card, legacy_row=legacy_row, provider_snapshot=provider_snapshot, config=config)
    scenarios = build_development_scenarios(card, config=config, rules_path=rules_path, holding=holding)
    acquisition = build_acquisition_ledger(card, holding=holding, scenarios=scenarios, location=location, legacy_row=legacy_row)
    zoning = _zoning_result(card, rules_path)
    development_components = _development_potential_components_from_zoning(card, zoning)
    return StrategyV2Property(
        property_key=property_key(card),
        address=str(_first(card, "address", default="UNKNOWN")),
        mls=str(_first(card, "mls_number", default="")) or None,
        source_provenance=_source_provenance(card),
        asset_classification=classification,
        gates=gates,
        holding=holding,
        location=location,
        development_potential=development_components["development_potential_proxy"],
        development_geometry_score=development_components["geometry_score"],
        development_entitlement_score=development_components["entitlement_score"],
        development_potential_proxy=development_components["development_potential_proxy"],
        development_scenarios=scenarios,
        acquisition=acquisition,
        legacy_reference=dict(legacy_reference or {}),
        ranking_status="NOT_RUN",
    )


def make_calibration_split(
    property_keys: Sequence[str],
    *,
    random_seed: int = 20260909,
    calibration_count: int = 120,
    blind_test_count: int = 46,
) -> dict[str, Any]:
    keys = sorted({str(item) for item in property_keys})
    if len(keys) != calibration_count + blind_test_count:
        raise ValueError(f"expected {calibration_count + blind_test_count} unique property keys, got {len(keys)}")
    shuffled = list(keys)
    random.Random(random_seed).shuffle(shuffled)
    calibration = sorted(shuffled[:calibration_count])
    blind = sorted(shuffled[calibration_count:])
    manifest = {
        "schema_version": "strategy-v2-shadow-split-v1",
        "random_seed": random_seed,
        "calibration_count": len(calibration),
        "blind_test_count": len(blind),
        "calibration_keys": calibration,
        "blind_test_keys": blind,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["split_sha256"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def build_shadow_portfolio(
    cards: Iterable[Any],
    *,
    legacy_reference_path: str | Path | None = None,
    config: Mapping[str, Any] | str | Path | None = None,
    rules_path: str | Path | None = None,
    provider_snapshots: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[StrategyV2Property], dict[str, Any]]:
    materialized = list(cards)
    legacy = _legacy_row_map(legacy_reference_path)
    reference = _v1_reference(legacy_reference_path)
    snapshots = provider_snapshots or {}
    rows: list[StrategyV2Property] = []
    for card in materialized:
        key = property_key(card)
        row = legacy.get(key)
        rows.append(build_shadow_property(card, legacy_row=row, legacy_reference=reference, config=config, rules_path=rules_path, provider_snapshot=snapshots.get(key)))
    split = make_calibration_split([row.property_key for row in rows]) if len(rows) == 166 else {"status": "NOT_GENERATED", "reason": "split requires the frozen 166-property universe"}
    return rows, split


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, list):
        return ", ".join(_fmt(item) for item in value) or "UNKNOWN"
    return str(value)


def _money(value: Any) -> str:
    value = _number(value)
    return "UNKNOWN" if value is None else f"${value:,.0f}"


def render_shadow_property(row: StrategyV2Property) -> str:
    """Render one complete ledger report; this is not a recommendation."""

    gates = "\n".join(f"| {item.code} | {item.status} | {_fmt(item.explanation)} |" for item in row.gates)
    scenarios = "\n".join(
        f"| {item.scenario_name} | {item.product_type} | {item.legal_status} | {_fmt(item.unit_count)} | {_fmt(item.theoretical_area)} | {_fmt(item.realistic_area)} | {_money(item.ralv)} | {_money(item.cash_mos)} | {_fmt(item.blocking_conditions)} |"
        for item in row.development_scenarios
    )
    road = row.location.road_adjustment
    provider = row.location.external_provider
    return f"""# {row.address}

**MLS:** {_fmt(row.mls)}  
**Property key:** `{row.property_key}`  
**Ranking:** `{row.ranking_status}` — V2 Phase 1 deliberately does not rank.

## Asset classification

| Field | Value |
|---|---|
| Primary track | {row.asset_classification.primary_track} |
| Secondary tracks | {_fmt(row.asset_classification.secondary_tracks)} |
| Basis | {_fmt(row.asset_classification.basis)} |
| Confidence | {row.asset_classification.confidence} |

## Source provenance

```json
{json.dumps(row.source_provenance, ensure_ascii=False, indent=2)}
```

## Gates

| Gate | Status | Explanation |
|---|---|---|
{gates}

## Holding ledger

| Field | Value |
|---|---:|
| House utility band | {row.holding.house_utility_band} |
| Holding condition band | {row.holding.holding_condition_band} |
| Survival CAPEX low / base / high | {_money(row.holding.survival_capex_low)} / {_money(row.holding.survival_capex_base)} / {_money(row.holding.survival_capex_high)} |
| Cash Holding Basis low / base / high | {_money(row.holding.cash_holding_basis_low)} / {_money(row.holding.cash_holding_basis_base)} / {_money(row.holding.cash_holding_basis_high)} |
| BCA improvement reference | {_money(row.holding.house_value_reference)} |
| Updated components | {_fmt(row.holding.updated_components)} |
| Kitchen/layout signal | {_fmt(row.holding.kitchen_count_signal)} / {row.holding.layout_signal} |
| Risks | {_fmt(row.holding.risks)} |

## Location ledger

### External provider (Walk Score only)

| Walk | Transit | Bike | Composite | Status |
|---:|---:|---:|---:|---|
| {_fmt(provider.get('walk'))} | {_fmt(provider.get('transit'))} | {_fmt(provider.get('bike'))} | {_fmt(provider.get('composite'))} | {_fmt(provider.get('status'))} |

### School / area / parcel

| Field | Value |
|---|---|
| Elementary / secondary | {_fmt(row.location.school.get('elementary_catchment'))} / {_fmt(row.location.school.get('secondary_catchment'))} |
| School premium | {_money(row.location.school.get('premium_cad'))} |
| Area baseline prior | {_money(row.location.area_baseline_prior.get('value_cad'))} ({_fmt(row.location.area_baseline_prior.get('confidence'))}) |
| Road category | {_fmt(road.get('category'))} |
| Road class / distance / direct | {_fmt(row.location.parcel_facts.get('road_class'))} / {_fmt(row.location.parcel_facts.get('distance_to_major_road_m'))}m / {_fmt(row.location.parcel_facts.get('property_fronts_major_road'))} |
| Road discount prior low / base / high | {_fmt(road.get('discount_low_pct'))} / {_fmt(road.get('discount_base_pct'))} / {_fmt(road.get('discount_high_pct'))} |
| Exposure factor | {_fmt(road.get('exposure_factor'))} |
| Park-facing / view | {_fmt(row.location.parcel_facts.get('park_facing'))} / {_fmt(row.location.parcel_facts.get('view_type'))} ({_fmt(row.location.parcel_facts.get('view_strength'))}) |
| Location risks | {_fmt(row.location.risks)} |

## Development scenario ledger

| Scenario | Product | Legal status | Units | Theoretical sqft | Realistic sqft | RALV | Cash MOS | Blocking conditions |
|---|---|---|---:|---:|---:|---:|---:|---|
{scenarios}

**Development potential diagnostic:** {_fmt(row.development_potential)} / 100  
This is entitlement/geometry potential, not development economics.

## Acquisition ledger

| Field | Value |
|---|---:|
| Asking | {_money(row.acquisition.asking)} |
| Assessment total / land / improvement | {_money(row.acquisition.assessment_total)} / {_money(row.acquisition.assessment_land)} / {_money(row.acquisition.assessment_improvement)} |
| Market land basis | {_money(row.acquisition.market_land_basis)} |
| Location baseline prior | {_money(row.acquisition.location_baseline_prior_cad)} |
| Normalized physical lot basis | {_money(row.acquisition.normalized_physical_lot_basis)} |
| Normalized lot basis / sqft | {_money(row.acquisition.normalized_lot_basis_per_sqft)} |
| Base / Conditional / Upside Cash MOS | {_money(row.acquisition.base_cash_mos)} / {_money(row.acquisition.conditional_cash_mos)} / {_money(row.acquisition.upside_cash_mos)} |
| Price flags | {_fmt(row.acquisition.price_risk_flags)} |

## Legacy reference

```json
{json.dumps(row.legacy_reference, ensure_ascii=False, indent=2)}
```

No final recommendation or ranking is emitted in Strategy V2 Phase 1.
"""


__all__ = [
    "AcquisitionLedger",
    "AssetClassification",
    "DevelopmentScenario",
    "GateFinding",
    "HoldingLedger",
    "LocationLedger",
    "StrategyV2Property",
    "build_acquisition_ledger",
    "build_development_scenarios",
    "build_holding_ledger",
    "build_location_ledger",
    "build_shadow_portfolio",
    "build_shadow_property",
    "classify_asset",
    "evaluate_gates",
    "make_calibration_split",
    "render_shadow_property",
]
