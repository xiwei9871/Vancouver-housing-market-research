from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .comparables import ComparableResult
from .models import EvidenceStatus, PropertyRecord, RedevelopmentScenario, ScenarioType, SourceType
from .normalization import calculate_lot_geometry_score
from .provenance import SourceRegistryEntry, load_source_registry_entries, registry_allows_evidence


class ZoningRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    municipality: str | None = None
    zoning: str | None = None
    permitted_uses: list[str] = Field(default_factory=list)
    conditional_uses: list[str] = Field(default_factory=list)
    minimum_site_area: float | None = None
    minimum_frontage: float | None = None
    maximum_fsr: float | None = None
    maximum_units: int | None = None
    maximum_height: float | None = None
    setbacks: dict[str, Any] = Field(default_factory=dict)
    lot_coverage: float | None = None
    parking: str | None = None
    tree_constraints: str | None = None
    servicing_constraints: str | None = None
    duplex_permitted: bool = False
    multiplex_permitted: bool = False
    bylaw_version: str | None = None
    resale_price_per_sqft: int | float | None = None


class RedevelopmentAnalysis(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    property: PropertyRecord
    scenarios: list[RedevelopmentScenario] = Field(default_factory=list)
    best_economic_product: ScenarioType | None = None
    official_zoning_evidence: bool = False
    zoning_rule: ZoningRule | None = None
    warnings: list[str] = Field(default_factory=list)
    observed_at: datetime


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _lookup_rule(record: PropertyRecord, config: dict[str, Any]) -> ZoningRule | None:
    rules = config.get("redevelopment", {}).get("rules", {})
    key = f"{record.municipality}:{record.zoning}" if record.zoning else ""
    raw: Any = None
    if isinstance(rules, dict):
        raw = rules.get(key)
        if raw is None:
            raw = rules.get(record.zoning or "")
        if raw is None and isinstance(rules.get(record.municipality), dict):
            raw = rules[record.municipality].get(record.zoning or "")
    elif isinstance(rules, list):
        for item in rules:
            if _text(item.get("municipality")) == _text(record.municipality) and _text(item.get("zoning")) == _text(record.zoning):
                raw = item
                break
    if raw is None:
        return None
    if isinstance(raw, ZoningRule):
        return raw
    return ZoningRule.model_validate({**raw, "municipality": record.municipality, "zoning": record.zoning})


def _official_zoning_evidence(
    record: PropertyRecord,
    rule: ZoningRule | None,
    source_registry: Mapping[str, SourceRegistryEntry] | None,
) -> bool:
    """Require a traceable official source before treating limits as legal."""

    if record.official_zoning_confirmed is not True:
        return False
    if not record.official_zoning_source_url or record.official_zoning_retrieved_at is None:
        return False
    if record.official_zoning_evidence_status is not EvidenceStatus.CONFIRMED:
        return False
    if not record.official_zoning_source_registry_id:
        return False
    registry = source_registry.get(record.official_zoning_source_registry_id) if source_registry else None
    if not registry_allows_evidence(registry, expected_source_type=SourceType.OFFICIAL_GOV):
        return False
    return bool(record.bylaw_version or (rule and rule.bylaw_version))


def _effective_rule(record: PropertyRecord, rule: ZoningRule | None) -> ZoningRule:
    """Fill an explicit official record from the official registry/record only."""

    values = rule.model_dump(mode="python") if rule else {}
    rule_fields = set(rule.model_fields_set) if rule else set()

    def entitlement_value(field: str, fallback: Any) -> Any:
        # An explicit false/zero/empty value in an official rule is meaningful;
        # listing claims must never replace it with a more permissive value.
        if rule is not None:
            if field in {"duplex_permitted", "multiplex_permitted"} and field not in rule_fields:
                return bool(getattr(rule, field, False))
            return getattr(rule, field) if field in rule_fields else None
        return fallback

    values.update({
        "municipality": record.municipality,
        "zoning": record.zoning,
        "maximum_fsr": entitlement_value("maximum_fsr", record.maximum_fsr),
        "maximum_units": entitlement_value("maximum_units", record.maximum_units),
        "maximum_height": entitlement_value("maximum_height", record.maximum_height),
        "minimum_frontage": entitlement_value("minimum_frontage", record.minimum_frontage),
        "minimum_site_area": entitlement_value("minimum_site_area", record.minimum_site_area),
        "duplex_permitted": entitlement_value(
            "duplex_permitted",
            any("duplex" in _text(item) for item in record.permitted_uses),
        ),
        "multiplex_permitted": entitlement_value(
            "multiplex_permitted",
            any("multiple" in _text(item) or "multiplex" in _text(item) for item in record.permitted_uses),
        ),
        "permitted_uses": entitlement_value("permitted_uses", record.permitted_uses),
        "conditional_uses": entitlement_value("conditional_uses", record.conditional_uses),
        "bylaw_version": entitlement_value("bylaw_version", record.bylaw_version),
    })
    values["permitted_uses"] = values.get("permitted_uses") or []
    values["conditional_uses"] = values.get("conditional_uses") or []
    return ZoningRule.model_validate(values)


def _explicitly_not_permitted(rule: ZoningRule | None, field: str) -> bool:
    """Return whether an official rule explicitly denies a use.

    ``maximum_units`` is a capacity ceiling, not a substitute for an explicit
    use permission.  Conversely, an explicit ``*_permitted: false`` must not
    be reopened merely because a source also carries a permissive-looking unit
    cap or listing claim.
    """

    return bool(
        rule is not None
        and field in rule.model_fields_set
        and getattr(rule, field, None) is False
    )


def _costs(area: float | None, config: dict[str, Any]) -> dict[str, int | None]:
    if area is None:
        return {"hard_cost": None, "soft_cost": None, "developer_specific_cost": None, "contingency": None, "total_project_cost": None}
    cfg = config.get("redevelopment", {})
    hard_range = cfg.get("hard_cost_per_sqft", [450, 550])
    if not isinstance(hard_range, (list, tuple)) or len(hard_range) < 2:
        return {"hard_cost": None, "soft_cost": None, "developer_specific_cost": None, "contingency": None, "total_project_cost": None}
    hard = round(float(area) * (float(hard_range[0]) + float(hard_range[1])) / 2)
    soft = round(hard * float(cfg.get("soft_cost_pct_of_hard", 0.20)))
    developer = round(hard * float(cfg.get("developer_specific_cost_pct_of_hard", 0.10)))
    contingency = round(hard * float(cfg.get("contingency_pct_of_hard", 0.10)))
    return {
        "hard_cost": hard,
        "soft_cost": soft,
        "developer_specific_cost": developer,
        "contingency": contingency,
        "total_project_cost": hard + soft + developer + contingency,
    }


def _resale_price_per_sqft(rule: ZoningRule, config: dict[str, Any], comparable_result: ComparableResult | None) -> float | None:
    value = rule.resale_price_per_sqft
    if value is None:
        value = config.get("redevelopment", {}).get("resale_price_per_sqft")
    if value is None and comparable_result and comparable_result.median_sold_price and comparable_result.median_house_size:
        value = comparable_result.median_sold_price / comparable_result.median_house_size
    return float(value) if value is not None else None


def _fit_score(unit_size: float | None, size_range: list[float]) -> float | None:
    if unit_size is None:
        return None
    if len(size_range) >= 2 and size_range[0] <= unit_size <= size_range[1]:
        return 95
    if unit_size >= 1000:
        return 75
    if unit_size >= 850:
        return 55
    return 35


def _execution(record: PropertyRecord, units: int | None) -> float | None:
    if units is None:
        return None
    value = 30 + max(units - 2, 0) * 8
    if "steep" in _text(record.slope):
        value += 20
    if record.heritage_status or record.heritage_register:
        value += 15
    value += min(25, len(record.permanent_defects) * 8)
    return min(100, value)


def _sale_value(area: float | None, price_per_sqft: float | None) -> int | None:
    if area is None or price_per_sqft is None:
        return None
    return round(area * price_per_sqft)


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    return None


def _coverage_ratio(value: Any) -> float | None:
    number = _numeric(value)
    if number is None:
        return None
    if number > 1:
        number /= 100
    return number if 0 < number <= 1 else None


def _theoretical_area(
    record: PropertyRecord,
    rule: ZoningRule,
    config: dict[str, Any],
    warnings: list[str],
) -> float | None:
    """Apply explicit FSR, coverage, setback, and height constraints."""

    if record.lot_area_sqft is None or rule.maximum_fsr is None:
        return None
    base = float(record.lot_area_sqft) * float(rule.maximum_fsr)
    candidates = [base]
    coverage = _coverage_ratio(rule.lot_coverage)
    floor_height = _numeric(config.get("redevelopment", {}).get("floor_to_floor_height"))
    if floor_height is None:
        floor_height = 10.0
    floors = 2.0
    if rule.maximum_height is not None:
        floors = max(1.0, float(rule.maximum_height) / floor_height)
        warnings.append("theoretical area includes the explicit maximum-height floor estimate")
    if coverage is not None:
        candidates.append(float(record.lot_area_sqft) * coverage * floors)

    frontage = _numeric(record.frontage_ft)
    depth = _numeric(record.depth_ft)
    setbacks = rule.setbacks or {}
    front = _numeric(setbacks.get("front"))
    rear = _numeric(setbacks.get("rear"))
    side = _numeric(setbacks.get("side"))
    if frontage is not None and depth is not None and (front is not None or rear is not None or side is not None):
        front = front or 0.0
        rear = rear or 0.0
        side = side or 0.0
        envelope_width = max(0.0, frontage - side * 2)
        envelope_depth = max(0.0, depth - front - rear)
        candidates.append(envelope_width * envelope_depth * floors)
        warnings.append("theoretical area includes the explicit setback envelope")

    result = round(min(candidates), 2)
    if getattr(rule, "tree_constraints", None) is not None or record.tree_constraints is not None:
        warnings.append("tree constraints are known but require an arborist/site plan for precise area")
    if record.slope is not None and "steep" in _text(record.slope):
        warnings.append("steep slope is treated as a realistic-area execution constraint")
    if record.servicing_constraints:
        warnings.append("servicing constraints require municipal/utility confirmation")
    if record.heritage_status or record.heritage_register:
        warnings.append("heritage/character constraints require retention and infill review")
    return result


def _realistic_area(
    record: PropertyRecord,
    theoretical: float | None,
    config: dict[str, Any],
) -> float | None:
    if theoretical is None:
        return None
    if record.realistic_buildable_sqft is not None:
        return min(float(record.realistic_buildable_sqft), theoretical)
    efficiency = float(config.get("redevelopment", {}).get("realistic_efficiency", 0.82))
    if record.slope is not None and "steep" in _text(record.slope):
        efficiency *= 0.85
    if record.tree_constraints and _text(record.tree_constraints) not in {"none", "no", "unknown"}:
        efficiency *= 0.90
    if record.servicing_constraints and _text(record.servicing_constraints) not in {"none", "no", "unknown"}:
        efficiency *= 0.90
    if record.heritage_status or record.heritage_register:
        efficiency *= 0.85
    return round(min(theoretical, theoretical * max(0.0, min(1.0, efficiency))), 2)


def _economic_gate_pass(option: dict[str, Any], config: dict[str, Any]) -> bool:
    """Apply owner safety margin when future opportunity cost is known."""

    resale = _numeric(option.get("estimated_resale_price"))
    cost = _numeric(option.get("total_project_cost"))
    if resale is None or cost is None or cost <= 0:
        return False
    developer_hurdle = float(config.get("developer_profit_hurdle_pct", 0.15))
    if (resale - cost) / cost < developer_hurdle:
        return False
    uplift = option.get("redevelopment_uplift")
    project_profit = option.get("estimated_project_profit")
    if project_profit is not None:
        minimum_profit = float(config.get("minimum_project_profit", 500_000))
        return float(project_profit) >= minimum_profit
    if uplift is None:
        return False
    minimum_uplift = float(config.get("minimum_owner_uplift", 500_000))
    return float(uplift) >= minimum_uplift


def _product_score(option: dict[str, Any]) -> float:
    if option.get("estimated_resale_price") is None or option.get("total_project_cost") is None:
        return -1
    if option.get("economic_gate_pass") is False:
        return -1
    fit = float(option.get("product_market_fit") or 0) / 100
    execution = 1 - float(option.get("execution_complexity") or 100) / 100
    economics = 0.5
    if option.get("estimated_resale_price") is not None and option.get("total_project_cost") is not None:
        margin = float(option["estimated_resale_price"]) - float(option["total_project_cost"])
        economics = max(0, min(1, margin / max(float(option["estimated_resale_price"]), 1)))
    size = float(option.get("average_unit_size") or 0)
    size_fit = max(0, min(1, 1 - abs(size - 1450) / 1450))
    unit_penalty = max(0, int(option.get("unit_count") or 2) - 2) * 0.04
    return max(0, 0.50 * fit + 0.20 * execution + 0.20 * size_fit + 0.10 * economics - unit_penalty)


def _scenario_from_option(
    record: PropertyRecord,
    scenario_type: ScenarioType,
    option: dict[str, Any],
    *,
    likely_buyer: str,
    legal_status: str,
    bylaw_version: str | None,
) -> RedevelopmentScenario:
    return RedevelopmentScenario(
        property_id=record.property_id,
        scenario_type=scenario_type,
        legal_status=legal_status,
        zoning_basis=record.zoning,
        bylaw_version=bylaw_version,
        unit_count=option.get("unit_count"),
        total_buildable_sqft=option.get("total_buildable_sqft"),
        realistic_buildable_sqft=option.get("realistic_buildable_sqft"),
        estimated_saleable_sqft=option.get("estimated_saleable_sqft"),
        average_unit_size=option.get("average_unit_size"),
        likely_bedrooms="3–4" if scenario_type in {ScenarioType.DUPLEX, ScenarioType.DETACHED_REBUILD} else "2–3",
        likely_buyer=likely_buyer,
        layout_options=option.get("layout_options", []),
        product_options=option.get("product_options", []),
        best_product_option=option.get("best_product_option"),
        front_building_noise_buffer_hypothesis=option.get("front_building_noise_buffer_hypothesis"),
        estimated_front_unit_area=option.get("estimated_front_unit_area"),
        estimated_rear_unit_area=option.get("estimated_rear_unit_area"),
        estimated_resale_price=option.get("estimated_resale_price"),
        hard_cost=option.get("hard_cost"),
        soft_cost=option.get("soft_cost"),
        developer_specific_cost=option.get("developer_specific_cost"),
        contingency=option.get("contingency"),
        total_project_cost=option.get("total_project_cost"),
        execution_complexity=option.get("execution_complexity"),
        product_market_fit=option.get("product_market_fit"),
        existing_property_market_value=option.get("existing_property_market_value"),
        new_finished_market_value=option.get("estimated_resale_price"),
        redevelopment_cost=option.get("total_project_cost"),
        future_opportunity_cost=option.get("existing_property_market_value"),
        redevelopment_uplift=future_uplift(
            option.get("existing_property_market_value"),
            option.get("estimated_resale_price"),
            option.get("total_project_cost"),
        ),
        estimated_project_profit=option.get("estimated_project_profit"),
        economic_gate_pass=option.get("economic_gate_pass"),
        exit_mode=option.get("exit_mode", "ALL_SALE_BASELINE"),
        owner_occupancy_benefit=option.get("owner_occupancy_benefit"),
        financing_not_modeled=option.get("financing_not_modeled", True),
        assumptions=option.get("assumptions", []),
    )


def _project_profit(
    record: PropertyRecord,
    option: dict[str, Any],
    config: dict[str, Any],
) -> int | None:
    """Estimate all-sale project profit using today's asking price.

    This is intentionally a coarse acquisition-plus-rebuild estimate.  It is
    separate from future opportunity-cost uplift and does not model financing.
    Optional transaction percentages are config inputs rather than hidden
    assumptions.
    """

    resale = _numeric(option.get("estimated_resale_price"))
    asking = _numeric(record.asking)
    build_cost = _numeric(option.get("total_project_cost"))
    if resale is None or asking is None or build_cost is None:
        return None
    renovation = _numeric(record.renovation_estimate) or 0
    costs = config.get("redevelopment", {}).get("project_costs", {})
    purchase_closing = round(asking * float(costs.get("purchase_closing_pct", 0)))
    selling = round(resale * float(costs.get("selling_cost_pct", 0)))
    gst_tax = round(resale * float(costs.get("gst_tax_pct", 0)))
    return round(resale - asking - purchase_closing - renovation - build_cost - selling - gst_tax)


def future_uplift(
    existing_property_market_value: int | None,
    new_finished_market_value: int | None,
    total_redevelopment_cost: int | None,
) -> int | None:
    if existing_property_market_value is None or new_finished_market_value is None or total_redevelopment_cost is None:
        return None
    return round(new_finished_market_value - existing_property_market_value - total_redevelopment_cost)


def analyze_redevelopment(
    record: PropertyRecord,
    config: dict[str, Any],
    *,
    comparable_result: ComparableResult | None = None,
    observed_at: datetime,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> RedevelopmentAnalysis:
    observed_at = _aware(observed_at)
    rule = _lookup_rule(record, config)
    registry = dict(source_registry or load_source_registry_entries(config))
    official = _official_zoning_evidence(record, rule, registry)
    effective = _effective_rule(record, rule)
    warnings: list[str] = []
    if not official:
        warnings.append("official zoning evidence is required before assigning redevelopment entitlement")
    if official and rule is None:
        warnings.append("official zoning flag exists but no matching local zoning rule was found")
    cfg = config.get("redevelopment", {})
    saleable_efficiency = float(cfg.get("saleable_efficiency", 0.90))
    theoretical = _theoretical_area(record, effective, config, warnings) if official else None
    realistic = _realistic_area(record, theoretical, config) if official else None
    if official and record.realistic_buildable_sqft is not None and record.realistic_buildable_sqft > (theoretical or record.realistic_buildable_sqft):
        warnings.append("record realistic buildable area exceeded theoretical area and was capped")
    price_per_sqft = _resale_price_per_sqft(effective, config, comparable_result)
    existing_value = comparable_result.expected_market_value if comparable_result else record.expected_market_value
    legal_status = "PERMITTED_BY_OFFICIAL_BYLAW" if official else "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    scenarios: list[RedevelopmentScenario] = [
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
            assumptions=["Existing current use and permits remain subject to official confirmation."],
        )
    ]

    duplex_max = cfg.get("family_duplex_unit_size_sqft", [1250, 1800])
    multi_family_research_scope = record.research_scope not in {
        "HOUSE_REBUILD_ONLY",
        "CURRENT_USE_ONLY",
    }
    if official and record.research_scope == "HOUSE_REBUILD_ONLY":
        detached_area = realistic
        detached_option = {
            "unit_count": 1,
            "total_buildable_sqft": theoretical,
            "realistic_buildable_sqft": detached_area,
            "estimated_saleable_sqft": detached_area * saleable_efficiency if detached_area is not None else None,
            "average_unit_size": detached_area * saleable_efficiency if detached_area is not None else None,
            "product_market_fit": 80,
            "execution_complexity": _execution(record, 1),
            "estimated_resale_price": _sale_value(
                detached_area * saleable_efficiency if detached_area is not None else None,
                price_per_sqft,
            ),
            "existing_property_market_value": existing_value,
            **_costs(detached_area, config),
            "exit_mode": "ALL_SALE_BASELINE",
            "owner_occupancy_benefit": None,
            "financing_not_modeled": True,
        }
        detached_option["estimated_project_profit"] = _project_profit(record, detached_option, config)
        detached_option["redevelopment_uplift"] = future_uplift(
            existing_value,
            detached_option["estimated_resale_price"],
            detached_option["total_project_cost"],
        )
        detached_option["economic_gate_pass"] = _economic_gate_pass(detached_option, cfg)
        scenarios.append(_scenario_from_option(
            record,
            ScenarioType.DETACHED_REBUILD,
            detached_option,
            likely_buyer="Westside detached-house buyer",
            legal_status=legal_status,
            bylaw_version=effective.bylaw_version or record.bylaw_version,
        ))

    duplex_capacity = effective.maximum_units is None or effective.maximum_units >= 2
    duplex_legal = official and multi_family_research_scope and duplex_capacity and not _explicitly_not_permitted(
        rule,
        "duplex_permitted",
    ) and (
        effective.duplex_permitted or (effective.maximum_units or 0) >= 2
    )
    duplex_legal_status = (
        "PERMITTED_BY_OFFICIAL_BYLAW"
        if duplex_legal
        else "NOT_PERMITTED_BY_OFFICIAL_BYLAW"
        if official and multi_family_research_scope
        else legal_status
    )
    duplex_area = None
    if duplex_legal:
        duplex_area = record.max_duplex_area or realistic
        if duplex_area is not None and len(duplex_max) >= 2:
            duplex_area = min(float(duplex_area), float(duplex_max[1]) * 2)
        if duplex_area is not None and realistic is not None:
            duplex_area = min(float(duplex_area), float(realistic))
    duplex_avg = duplex_area / 2 if duplex_area is not None else None
    duplex_cost = _costs(duplex_area, config)
    duplex_option = {
        "unit_count": 2 if duplex_legal else None,
        "total_buildable_sqft": theoretical,
        "realistic_buildable_sqft": duplex_area,
        "estimated_saleable_sqft": duplex_area * saleable_efficiency if duplex_area is not None else None,
        "average_unit_size": duplex_avg,
        "product_market_fit": _fit_score(duplex_avg, duplex_max),
        "execution_complexity": _execution(record, 2) if duplex_legal else None,
        "front_building_noise_buffer_hypothesis": record.road_class in {"A", "B", "C"} or record.property_fronts_major_road is True,
        "layout_options": ["front/rear", "side-by-side"],
        "estimated_front_unit_area": duplex_avg,
        "estimated_rear_unit_area": duplex_avg,
        **duplex_cost,
    }
    duplex_option["estimated_resale_price"] = _sale_value(duplex_option["estimated_saleable_sqft"], price_per_sqft)
    duplex_option["existing_property_market_value"] = existing_value
    duplex_option["estimated_project_profit"] = _project_profit(record, duplex_option, config)
    duplex_option["exit_mode"] = "ALL_SALE_BASELINE"
    duplex_option["owner_occupancy_benefit"] = None
    duplex_option["financing_not_modeled"] = True
    duplex_uplift = future_uplift(
        existing_value,
        duplex_option["estimated_resale_price"],
        duplex_option["total_project_cost"],
    )
    duplex_option["redevelopment_uplift"] = duplex_uplift
    duplex_option["economic_gate_pass"] = _economic_gate_pass(duplex_option, cfg)
    scenarios.append(_scenario_from_option(
        record,
        ScenarioType.DUPLEX,
        duplex_option,
        likely_buyer="family-oriented half duplex buyer",
        legal_status=duplex_legal_status,
        bylaw_version=effective.bylaw_version or record.bylaw_version,
    ))

    configured_multiplex_cap = int(effective.maximum_units or 0)
    multiplex_max = min(configured_multiplex_cap, 6) if official else 0
    multiplex_capacity = effective.maximum_units is None or effective.maximum_units >= 2
    multiplex_legal = official and multi_family_research_scope and multiplex_capacity and not _explicitly_not_permitted(
        rule,
        "multiplex_permitted",
    ) and (
        effective.multiplex_permitted or multiplex_max >= 3
    )
    multiplex_legal_status = (
        "PERMITTED_BY_OFFICIAL_BYLAW"
        if multiplex_legal
        else "NOT_PERMITTED_BY_OFFICIAL_BYLAW"
        if official and multi_family_research_scope
        else legal_status
    )
    options: list[dict[str, Any]] = []
    if multiplex_legal and realistic is not None:
        for units in cfg.get("scenario_unit_range", [2, 3, 4, 5, 6]):
            units = int(units)
            if units < 2 or units > multiplex_max or units > 6:
                continue
            saleable = realistic * saleable_efficiency
            avg = saleable / units
            costs = _costs(realistic, config)
            option = {
                "unit_count": units,
                "total_buildable_sqft": theoretical,
                "realistic_buildable_sqft": realistic,
                "estimated_saleable_sqft": saleable,
                "average_unit_size": avg,
                "product_market_fit": _fit_score(avg, [1250, 1800]) or 0,
                "execution_complexity": _execution(record, units),
                "estimated_resale_price": _sale_value(saleable, price_per_sqft),
                "existing_property_market_value": existing_value,
                **costs,
            }
            option["estimated_project_profit"] = _project_profit(record, option, config)
            option["exit_mode"] = "ALL_SALE_BASELINE"
            option["owner_occupancy_benefit"] = None
            option["financing_not_modeled"] = True
            option["redevelopment_uplift"] = future_uplift(existing_value, option["estimated_resale_price"], option["total_project_cost"])
            option["economic_gate_pass"] = _economic_gate_pass(option, cfg)
            options.append(option)
    economic_options = [option for option in options if _product_score(option) >= 0]
    best_option = max(economic_options, key=_product_score, default=None)
    multiplex_option = {
        "unit_count": multiplex_max if multiplex_legal else None,
        "total_buildable_sqft": theoretical,
        "realistic_buildable_sqft": realistic if multiplex_legal else None,
        "estimated_saleable_sqft": realistic * saleable_efficiency if realistic is not None and multiplex_legal else None,
        "average_unit_size": (realistic * saleable_efficiency / multiplex_max) if realistic is not None and multiplex_max else None,
        "product_market_fit": best_option.get("product_market_fit") if best_option else None,
        "execution_complexity": _execution(record, multiplex_max) if multiplex_legal else None,
        "product_options": options,
        "best_product_option": best_option,
        "hard_cost": _costs(realistic, config)["hard_cost"] if multiplex_legal else None,
        "soft_cost": _costs(realistic, config)["soft_cost"] if multiplex_legal else None,
        "developer_specific_cost": _costs(realistic, config)["developer_specific_cost"] if multiplex_legal else None,
        "contingency": _costs(realistic, config)["contingency"] if multiplex_legal else None,
        "total_project_cost": _costs(realistic, config)["total_project_cost"] if multiplex_legal else None,
        "estimated_resale_price": best_option.get("estimated_resale_price") if best_option else None,
        "existing_property_market_value": existing_value,
        "estimated_project_profit": best_option.get("estimated_project_profit") if best_option else None,
        "exit_mode": "ALL_SALE_BASELINE",
        "owner_occupancy_benefit": None,
        "financing_not_modeled": True,
        "economic_gate_pass": _economic_gate_pass(
            {
                "estimated_resale_price": best_option.get("estimated_resale_price") if best_option else None,
                "total_project_cost": _costs(realistic, config)["total_project_cost"] if multiplex_legal else None,
                "redevelopment_uplift": best_option.get("redevelopment_uplift") if best_option else None,
                "estimated_project_profit": best_option.get("estimated_project_profit") if best_option else None,
            },
            cfg,
        ) if best_option else False,
    }
    scenarios.append(_scenario_from_option(
        record,
        ScenarioType.MAX_REALISTIC_MULTIPLEX,
        multiplex_option,
        likely_buyer="family or downsizer buyer pool",
        legal_status=multiplex_legal_status,
        bylaw_version=effective.bylaw_version or record.bylaw_version,
    ))
    if record.character_status or record.heritage_register or record.infill_options:
        scenarios.append(RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.CHARACTER_HOUSE_INFILL,
            legal_status="REVIEW_REQUIRED",
            zoning_basis=record.zoning,
            bylaw_version=record.bylaw_version,
            assumptions=["Character retention and infill incentives require official confirmation."],
        ))
    if record.toa or record.toa_status or record.assembly_score is not None:
        scenarios.append(RedevelopmentScenario(
            property_id=record.property_id,
            scenario_type=ScenarioType.TOA_OR_ASSEMBLY,
            legal_status="LONG_TERM_OPTION_ONLY",
            zoning_basis=record.current_zoning or record.zoning,
            bylaw_version=record.bylaw_version,
            assumptions=["TOA/assembly is an option value and does not establish automatic entitlement."],
        ))

    duplex_scenario = next(item for item in scenarios if item.scenario_type is ScenarioType.DUPLEX)
    product_candidates: list[tuple[float, ScenarioType]] = []
    if duplex_scenario.unit_count is not None and _product_score({
        "product_market_fit": duplex_scenario.product_market_fit,
        "execution_complexity": duplex_scenario.execution_complexity,
            "average_unit_size": duplex_scenario.average_unit_size,
            "estimated_resale_price": duplex_scenario.estimated_resale_price,
            "total_project_cost": duplex_scenario.total_project_cost,
            "economic_gate_pass": duplex_option.get("economic_gate_pass"),
        }) >= 0:
        product_candidates.append((_product_score({
            "product_market_fit": duplex_scenario.product_market_fit,
            "execution_complexity": duplex_scenario.execution_complexity,
            "average_unit_size": duplex_scenario.average_unit_size,
            "estimated_resale_price": duplex_scenario.estimated_resale_price,
            "total_project_cost": duplex_scenario.total_project_cost,
            "economic_gate_pass": duplex_option.get("economic_gate_pass"),
        }), ScenarioType.DUPLEX))
    if best_option is not None:
        product_candidates.append((_product_score(best_option), ScenarioType.MAX_REALISTIC_MULTIPLEX))
    detached_scenario = next(
        (item for item in scenarios if item.scenario_type is ScenarioType.DETACHED_REBUILD),
        None,
    )
    if detached_scenario is not None:
        detached_score = _product_score({
            "product_market_fit": detached_scenario.product_market_fit,
            "execution_complexity": detached_scenario.execution_complexity,
            "average_unit_size": detached_scenario.average_unit_size,
            "estimated_resale_price": detached_scenario.estimated_resale_price,
            "total_project_cost": detached_scenario.total_project_cost,
            "economic_gate_pass": detached_scenario.economic_gate_pass,
        })
        if detached_score >= 0:
            product_candidates.append((detached_score, ScenarioType.DETACHED_REBUILD))
    best_product = max(product_candidates, default=(0, None), key=lambda item: (item[0], 1 if item[1] is ScenarioType.DUPLEX else 0))[1]
    updated = record.model_copy(update={
        "theoretical_buildable_sqft": theoretical,
        "realistic_buildable_sqft": realistic,
        "max_duplex_area": duplex_area,
        "estimated_front_unit_area": duplex_avg,
        "estimated_rear_unit_area": duplex_avg,
        "max_realistic_multiplex_units": multiplex_max if multiplex_legal else None,
        "multiplex_area": realistic if multiplex_legal else None,
        "best_economic_product": best_product.value if best_product else None,
        "buildability_score": min(100, max(0, (realistic / theoretical * 100) if theoretical and realistic else record.buildability_score or 0)) if official else record.buildability_score,
        "lot_geometry_score": calculate_lot_geometry_score(record, config) if record.lot_geometry_score is None else record.lot_geometry_score,
    })
    return RedevelopmentAnalysis(
        property=updated,
        scenarios=scenarios,
        best_economic_product=best_product,
        official_zoning_evidence=official,
        zoning_rule=effective if rule else None,
        warnings=warnings,
        observed_at=observed_at,
    )
