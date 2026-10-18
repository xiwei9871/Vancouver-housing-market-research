from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import CandidateStatus, EvidenceStatus, PropertyRecord, RedevelopmentScenario, ScoreSnapshot
from .provenance import DataConflict, SourceRecord
from .state import StateResolution, resolve_candidate_state


def _display(value: Any, *, not_available: bool = False) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "NOT_AVAILABLE" if not_available else "UNKNOWN"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_display(item) for item in value) if value else "UNKNOWN"
    return str(getattr(value, "value", value))


def _mapping_inline(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "UNKNOWN"
    return "; ".join(
        f"{key}={_display(item)}"
        for key, item in sorted(value.items(), key=lambda item: str(item[0]))
    )


def _currency(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    return f"${float(value):,.0f}"


def _percent(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{float(value) * 100:.1f}%"


def _score_band(points: Any, maximum: Any, statuses: Iterable[Any] = ()) -> str:
    try:
        ratio = float(points) / float(maximum) if float(maximum) else 0
    except (TypeError, ValueError):
        return "UNKNOWN"
    if ratio >= 0.80:
        label = "STRONG"
    elif ratio >= 0.65:
        label = "GOOD"
    elif ratio >= 0.50:
        label = "MIXED"
    else:
        label = "WEAK"
    values = {getattr(status, "value", status) for status in statuses}
    if EvidenceStatus.CONFLICT.value in values:
        return f"{label} / CONFLICT"
    if EvidenceStatus.UNKNOWN.value in values or EvidenceStatus.UNCONFIRMED.value in values:
        return f"{label} / UNVERIFIED"
    return label


def _dimension_label(dimension: Any) -> str:
    label = getattr(dimension, "label", None)
    if label and label != "UNKNOWN":
        return str(label)
    components = getattr(dimension, "components", {}) or {}
    return _score_band(
        getattr(dimension, "points", None),
        getattr(dimension, "maximum_points", None),
        [getattr(item, "evidence_status", None) for item in components.values()],
    )


def _total_label(snapshot: ScoreSnapshot | None, total_score: Any) -> str:
    if snapshot and snapshot.total_score_label and snapshot.total_score_label != "UNKNOWN":
        return snapshot.total_score_label
    return _score_band(total_score, 100)


def _independent_score_label(snapshot: ScoreSnapshot | None, value: Any) -> str:
    statuses = []
    if snapshot and snapshot.data_confidence_score < 70:
        statuses.append(EvidenceStatus.UNKNOWN)
    return _score_band(value, 100, statuses)


def _assessment_status(record: PropertyRecord) -> str:
    if record.assessment_valuation_status and record.assessment_valuation_status != "UNKNOWN":
        return record.assessment_valuation_status
    if record.asking is not None and record.assessment_total is not None:
        return "ASSESSMENT_ANCHOR_ONLY"
    return "UNKNOWN"


def _primary_detached(record: PropertyRecord) -> bool:
    if getattr(record, "primary_detached_eligible", None) is False:
        return False
    if str(getattr(record, "research_scope", "UNKNOWN") or "UNKNOWN").upper() in {
        "EXCLUDED_NON_TARGET",
        "EXCLUDED_STEEP_LOT",
        "SUPPLEMENTARY_DUPLEX",
    }:
        return False
    asset_class = str(getattr(record, "research_asset_class", "UNKNOWN") or "UNKNOWN").upper()
    if asset_class == "DETACHED_HOUSE":
        return True
    if asset_class != "UNKNOWN":
        return False
    property_type = str(getattr(record, "property_type", "") or "").lower()
    return not property_type or any(token in property_type for token in ("detached", "single family", "house"))


def _in_price_scope(record: PropertyRecord) -> bool:
    return str(getattr(record, "range_classification", "UNKNOWN") or "UNKNOWN").upper() != "OUT_OF_RANGE"


def _supplementary_duplex(record: PropertyRecord) -> bool:
    return (
        str(getattr(record, "research_asset_class", "UNKNOWN") or "UNKNOWN").upper() == "DUPLEX"
        and _in_price_scope(record)
    )


def _in_report_scope(record: PropertyRecord) -> bool:
    return _in_price_scope(record)


def _score_breakdown_lines(snapshot: ScoreSnapshot | None) -> list[str]:
    if snapshot is None or not snapshot.dimensions:
        return ["- UNKNOWN (score snapshot not available)"]
    names = (
        "land_lot_title",
        "price_safety_margin",
        "current_house_utility",
        "family_location_noise",
        "school",
        "redevelopment_option_value",
        "resale_affordability",
        "carrying_cost_risk",
    )
    labels = {
        "land_lot_title": "Land / Lot / Title",
        "price_safety_margin": "Price / Safety Margin",
        "current_house_utility": "Current House Utility",
        "family_location_noise": "Family Location / Noise",
        "school": "School",
        "redevelopment_option_value": "Redevelopment / Option Value",
        "resale_affordability": "Resale / Affordability",
        "carrying_cost_risk": "Carrying Cost / Risk",
    }
    lines: list[str] = []
    for name in names:
        dimension = snapshot.dimensions.get(name)
        if dimension is None:
            lines.append(f"- {labels[name]}: UNKNOWN")
            continue
        lines.append(
            f"- {labels[name]}: {_display(dimension.points)}/{_display(dimension.maximum_points)} — {_dimension_label(dimension)}"
        )
        for component_name, component in dimension.components.items():
            component_label = _score_band(
                component.points,
                component.maximum_points,
                [component.evidence_status],
            )
            unknown = f"; unknown={_display(component.unknown_fields)}" if component.unknown_fields else ""
            lines.append(
                f"  - {component_name}: {_display(component.points)}/{_display(component.maximum_points)} — {component_label}{unknown}"
            )
    return lines


def _source_for(field_name: str, provenance: Iterable[SourceRecord]) -> str:
    matches = [item for item in provenance if item.field_name == field_name]
    if not matches:
        return ""
    source = matches[-1]
    registry = (
        f" via {source.source_registry_id}"
        if source.source_registry_id
        else ""
    )
    source_label = {
        "source:mls-primary": "DIRECT_MLS",
        "source:realtor-ca-public": "PUBLIC_REALTOR_CA",
        "source:zolo-public": "PUBLIC_ZOLO",
    }.get(
        source.source_registry_id,
        {
            "MLS_PRIMARY": "DIRECT_MLS",
            "REALTOR_CA_PUBLIC": "PUBLIC_REALTOR_CA",
            "ZOLO_PUBLIC": "PUBLIC_ZOLO",
        }.get(source.source_role, source.source_type.value),
    )
    return f"; source={source_label}{registry} (source_type={source.source_type.value}) {source.source_url}"


def _snapshot_for(
    record: PropertyRecord,
    snapshots: Mapping[str, ScoreSnapshot] | Iterable[ScoreSnapshot] | None,
) -> ScoreSnapshot | None:
    if snapshots is None:
        return None
    if isinstance(snapshots, Mapping):
        return snapshots.get(record.property_id)
    for snapshot in snapshots:
        if snapshot.property_id == record.property_id:
            return snapshot
    return None


def _resolution_for(
    record: PropertyRecord,
    resolutions: Mapping[str, StateResolution] | None,
) -> StateResolution:
    if resolutions and record.property_id in resolutions:
        return resolutions[record.property_id]
    return resolve_candidate_state(
        record,
        total_score=record.total_score,
        data_confidence_score=record.data_confidence_score,
    )


def _source_value(source_run: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(source_run, Mapping):
        return source_run.get(field_name, default)
    return getattr(source_run, field_name, default)


def _crawl_summary(source_runs: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Project crawler-only metrics into the normal weekly summary."""

    projected: dict[str, dict[str, Any]] = {}
    for source_run in source_runs:
        metrics = _source_value(source_run, "crawl_metrics", {}) or {}
        if not isinstance(metrics, Mapping) or not metrics:
            continue
        status = _source_value(source_run, "status", "UNKNOWN")
        status = getattr(status, "value", status)
        errors = metrics.get("errors")
        if errors is None:
            errors = _source_value(source_run, "errors", []) or []
        gaps = _source_value(source_run, "coverage_gaps", None)
        if gaps is None:
            gaps = metrics.get("coverage_gaps", [])
        source_name = str(
            _source_value(source_run, "source_name", None)
            or _source_value(source_run, "source_registry_id", "UNKNOWN")
        )
        coverage_complete = _source_value(source_run, "coverage_complete", False) is True
        status_text = str(status)
        if not coverage_complete and status_text == "BLOCKED_POLICY":
            coverage = "BLOCKED_POLICY"
        else:
            coverage = "COMPLETE" if coverage_complete else "PARTIAL"
        projected[source_name] = {
            "status": str(status),
            "coverage": coverage,
            "pages_fetched": int(metrics.get("pages_fetched", 0) or 0),
            "cards_found": int(metrics.get("cards_found", 0) or 0),
            "details_fetched": int(metrics.get("details_fetched", 0) or 0),
            "rows_extracted": int(metrics.get("rows_extracted", 0) or 0),
            "duplicates": int(metrics.get("duplicates", metrics.get("duplicate_pages", 0)) or 0),
            "errors": list(errors or ()) if not isinstance(errors, str) else [errors],
            "coverage_gaps": list(gaps or ()) if not isinstance(gaps, str) else [gaps],
        }
    return projected


def _stage1_counts(
    records: list[PropertyRecord],
    *,
    source_runs: Iterable[Any],
    overrides: Mapping[str, int] | None,
    verification_count: int,
) -> dict[str, int]:
    supplied = dict(overrides or {})
    crawl = _crawl_summary(source_runs)
    raw_cards = sum(int(item.get("cards_found", 0) or 0) for item in crawl.values())
    in_range = sum(
        1
        for record in records
        if str(getattr(record, "range_classification", "UNKNOWN") or "UNKNOWN").upper()
        in {"CORE", "EXTENDED", "SPECIAL"}
    )
    excluded = sum(
        1
        for record in records
        if str(getattr(record, "candidate_status", "UNKNOWN") or "UNKNOWN").upper()
        in {"D_REJECT", "REJECT", "SPECIAL_SITUATION"}
    )
    return {
        "raw_cards": int(supplied.get("raw_cards", raw_cards) or 0),
        "normalized_records": int(supplied.get("normalized_records", len(records)) or 0),
        "in_range": int(supplied.get("in_range", in_range) or 0),
        "excluded": int(supplied.get("excluded", excluded) or 0),
        "NEED_VERIFICATION": int(supplied.get("NEED_VERIFICATION", verification_count) or 0),
    }


def _scenario_line(scenario: RedevelopmentScenario) -> str:
    options = ""
    if scenario.product_options:
        options = "; options=" + ", ".join(
            f"{item.get('unit_count', 'UNKNOWN')}u/{_display(item.get('average_unit_size'))}sqft"
            for item in scenario.product_options
        )
    return (
        f"- {scenario.scenario_type.value}: legal={_display(scenario.legal_status)}, "
        f"units={_display(scenario.unit_count)}, realistic_area={_display(scenario.realistic_buildable_sqft)}, "
        f"saleable_area={_display(scenario.estimated_saleable_sqft)}, "
        f"buyer={_display(scenario.likely_buyer)}, fit={_display(scenario.product_market_fit)}, "
        f"execution={_display(scenario.execution_complexity)}, Estimated Resale={_currency(scenario.estimated_resale_price)}, "
        f"Hard / Soft / Developer / Contingency={_currency(scenario.hard_cost)}/{_currency(scenario.soft_cost)}/{_currency(scenario.developer_specific_cost)}/{_currency(scenario.contingency)}, "
        f"Total Project Cost={_currency(scenario.total_project_cost)}, Existing Value / New Finished={_currency(scenario.existing_property_market_value)}/{_currency(scenario.new_finished_market_value)}, "
        f"Future Opportunity Cost={_currency(scenario.future_opportunity_cost)}, Redevelopment Uplift={_currency(scenario.redevelopment_uplift)}{options}"
        f", Project Profit={_currency(scenario.estimated_project_profit)}, "
        f"Layouts={_display(scenario.layout_options)}, "
        f"Noise Buffer Hypothesis={_display(scenario.front_building_noise_buffer_hypothesis)}, "
        f"exit={_display(scenario.exit_mode)}, financing={_display('FINANCING_NOT_MODELED' if scenario.financing_not_modeled else 'MODELED')}"
    )


def build_investment_thesis(
    record: PropertyRecord,
    snapshot: ScoreSnapshot | None = None,
    resolution: StateResolution | None = None,
) -> str:
    """Build four concrete sentences for the review hand-off."""

    state = resolution or _resolution_for(record, None)
    expected_value = record.expected_market_value
    if expected_value is not None and record.asking is not None:
        sentence_one = (
            f"The asking price {_currency(record.asking)} is {_percent(record.price_dislocation_pct)} "
            f"versus the estimated comparable value {_currency(expected_value)}, so the apparent discount "
            "must be tested against the location and condition evidence."
        )
    else:
        sentence_one = "Comparable market value is UNKNOWN, so the apparent discount is not yet a verified bargain."

    if record.permanent_defects or record.outdoor_noise_exposure is not None:
        defects = ", ".join(record.permanent_defects) if record.permanent_defects else "outdoor exposure"
        sentence_two = (
            f"The permanent risk is {defects}; outdoor noise exposure is "
            f"{_display(record.outdoor_noise_exposure)}, while indoor mitigation potential is "
            f"{_display(record.indoor_noise_mitigation_potential)}."
        )
    else:
        sentence_two = "Permanent defects and outdoor exposure are UNKNOWN, so the discount cannot yet be assigned to an acceptable risk."

    lot = f"{_display(record.frontage_ft)} x {_display(record.depth_ft)} ft on {_display(record.lot_area_sqft)} sqft"
    product = record.best_economic_product or "no product selected until future-value and cost inputs are confirmed"
    sentence_three = (
        f"The land case is {lot} with {product} as the future product hypothesis; "
        f"buildability is {_display(record.buildability_score)} and the land score is "
        f"{_display((snapshot.land_score if snapshot else record.land_score))}."
    )

    sentence_four = (
        f"For a five-to-ten-year family hold, the live score is "
        f"{_display((snapshot.live_score if snapshot else record.live_score))}; "
        f"the current decision is {state.state.value} with recommendation {state.recommendation}."
    )
    return " ".join((sentence_one, sentence_two, sentence_three, sentence_four))


def build_candidate_card(
    record: PropertyRecord,
    snapshot: ScoreSnapshot | None = None,
    *,
    scenarios: Iterable[RedevelopmentScenario] | None = None,
    resolution: StateResolution | None = None,
    provenance: Iterable[SourceRecord] | None = None,
) -> str:
    sources = list(provenance or ())
    state = resolution or _resolution_for(record, None)
    scenario_records = list(scenarios or ())
    total_score = snapshot.total_score if snapshot else record.total_score
    live_score = snapshot.live_score if snapshot else record.live_score
    land_score = snapshot.land_score if snapshot else record.land_score
    investment_score = snapshot.investment_score if snapshot else record.investment_score
    confidence = snapshot.data_confidence_score if snapshot else record.data_confidence_score
    confidence_band = snapshot.data_confidence_band if snapshot else record.data_confidence_band

    lines = [
        f"### {record.address}",
        "",
        f"- Current Ask: {_currency(record.asking)}{_source_for('asking', sources)}",
        f"- Acquisition Channel: {_display(record.acquisition_channel)}; Feed Class: {_display(record.feed_class)}",
        f"- Research Asset Class: {_display(record.research_asset_class)}; Primary Detached House: {_display(record.primary_detached_eligible)}",
        f"- Research Scope: {_display(record.research_scope)}; scope reasons={_display(record.research_scope_reasons)}",
        f"- Market Bucket / Price Range: {_display(record.market_bucket)} / {_display(record.range_classification)}",
        f"- Classification Basis: {_display(record.research_asset_classification_basis)}; notes={_display(record.research_asset_classification_notes)}",
        f"- Neighbourhood: {_display(record.neighbourhood)}",
        f"- Ownership: {_display(record.ownership_type)}; listing claim={_display(record.unverified_ownership_claim, not_available=True)}; official ownership confirmed={_display(record.official_ownership_confirmed)}",
        f"- PID: {_display(record.pid)}{_source_for('pid', sources)}",
        f"- Secondary Civic Addresses: {_display(record.secondary_civic_addresses, not_available=True)}",
        f"- Number of PIDs: {_display(record.number_of_pids)}; independently tradable={_display(record.independently_tradable)}; SINGLE_TITLE_MULTI_ADDRESS={_display(record.single_title_multi_address)}",
        f"- Legal Description: {_display(record.legal_description)}",
        f"- Strata Review: fee={_display(record.strata_fee, not_available=True)}; Form B={_display(record.strata_form_b_status, not_available=True)}; bylaws={_display(record.strata_bylaws_status, not_available=True)}; insurance={_display(record.strata_insurance_status, not_available=True)}; CRF={_display(record.strata_crf, not_available=True)}",
        f"- Common/Limited Common Property: {_display(record.common_property, not_available=True)} / {_display(record.limited_common_property, not_available=True)}; unit entitlement={_display(record.unit_entitlement, not_available=True)}; repair responsibility={_display(record.repair_responsibility, not_available=True)}",
        "",
        "**Lot**",
        f"- Frontage x Depth: {_display(record.frontage_ft)} x {_display(record.depth_ft)} ft",
        f"- Area: {_display(record.lot_area_sqft)} sqft / {_display(record.lot_area_m2)} m²",
        f"- Shape / Lane / Corner / Slope: {_display(record.lot_shape)} / {_display(record.lane_access)} / {_display(record.corner_lot)} / {_display(record.slope)}",
        f"- Local Lot Percentile: {_display(record.lot_size_percentile_in_neighbourhood)}; Geometry Score: {_display(record.lot_geometry_score)}",
        f"- Lot Area Score: {_display(record.lot_area_score)}/100; Frontage Score: {_display(record.frontage_score)}/100; Depth Score: {_display(record.depth_score)}/100",
        f"- Standard Redevelopment Gate: {_display(record.research_scope)}; minimum=33 ft frontage × 100 ft depth × 3,000 sqft",
        "",
        "**House**",
        f"- Year / Effective Year: {_display(record.year_built)} / {_display(record.effective_year)}",
        f"- Finished sqft: {_display(record.finished_sqft)}",
        f"- Bed/Bath: {_display(record.bedrooms)} / {_display(record.bathrooms)}",
        f"- Basement: {_display(record.basement)}; utility score={_display(record.current_house_utility_score)}",
        f"- Systems: roof age={_display(record.roof_age)}; electrical panel={_display(record.electrical_panel)}; knob-and-tube={_display(record.knob_and_tube)}; galvanized pipe={_display(record.galvanized_pipe)}; cast iron={_display(record.cast_iron)}; oil tank={_display(record.oil_tank)}; asbestos={_display(record.asbestos)}; moisture={_display(record.moisture)}; perimeter drain={_display(record.perimeter_drain)}",
        "",
        "**Assessment**",
        f"- Year: {_display(record.assessment_year)}; Total: {_currency(record.assessment_total)}; Land: {_currency(record.assessment_land)}; Improvement: {_currency(record.assessment_improvement)}{_source_for('assessment_total', sources)}",
        f"- Previous Year: total={_currency(record.previous_year_total)}; land={_currency(record.previous_year_land)}; improvement={_currency(record.previous_year_improvement)}",
        f"- Assessment History: {_display(record.assessment_history)}",
        f"- Ask vs Assessment: {_percent(record.asking_vs_total_assessment)}; Ask vs Land: {_percent(record.asking_vs_land_assessment)}",
        f"- Assessment Anchor Status: {_assessment_status(record)}",
        f"- Assessment Labels: {_display(record.assessment_labels)}",
        f"- Official Enrichment: {_mapping_inline(record.official_enrichment_status)}; School District: {_display(record.school_district)}; Sold History Scope: {_display(record.sold_history_scope)}",
        f"- Data Reconciliation: {_display(record.data_reconciliation_notes)}",
        f"- Municipal Carrying Cost: total={_currency(record.annual_municipal_carrying_cost)}; pure tax={_currency(record.pure_property_tax)}; water={_currency(record.water)}; sewer={_currency(record.sewer)}; garbage={_currency(record.garbage)}; drainage={_currency(record.drainage_charge)}; other levies={_currency(record.other_levies)}",
        "",
        "**Listing History**",
        f"- Status: {_display(record.listing_status)}; DOM: {_display(record.dom)}; Cumulative DOM: {_display(record.cumulative_dom)}",
        f"- Original Ask: {_currency(record.original_asking)}; Reduction: {_currency(record.price_reduction_amount)} ({_percent(record.price_reduction_pct)})",
        f"- Relists: {_display(record.relist_count)}; previous prices={_display(record.previous_listing_prices)}; terminated={_display(record.terminated_history)}",
        f"- Previous Listing History: sources={_display(record.previous_listing_sources)}",
        f"- Previous Sale History: dates={_display(record.previous_sale_dates) if record.previous_sale_dates else ('NONE_IN_SCOPE' if record.sold_history_scope else 'UNKNOWN')}; prices={_display(record.previous_sale_prices) if record.previous_sale_prices else ('NONE_IN_SCOPE' if record.sold_history_scope else 'UNKNOWN')}; sources={_display(record.sold_history_sources)}; checked_scope={_display(record.sold_history_scope)}",
        f"- Transaction Date: {_display(record.transaction_date)}; Transaction Price: {_currency(record.transaction_price)}; history conflict={_display(record.transaction_history_conflict)}",
        "",
        "**Road / Noise**",
        f"- Road Class / Distance: {_display(record.road_class)} / {_display(record.distance_to_major_road_m)} m",
        f"- Nearest Major Road: {_display(record.nearest_major_road)}; fronts major road={_display(record.property_fronts_major_road)}",
        f"- Buffer: {_display(record.road_buffer_type)} / stability={_display(record.road_buffer_stability)} / at risk={_display(record.buffer_at_risk)}",
        f"- Noise Risk: {_display(record.noise_risk)}; Outdoor Noise: {_display(record.outdoor_noise_exposure)}; Indoor Mitigation: {_display(record.indoor_noise_mitigation_potential)}",
        "",
        "**School**",
        f"- Elementary: {_display(record.official_elementary_catchment)}; Secondary: {_display(record.official_secondary_catchment)}",
        f"- Current School Score: {_display(record.current_school_score)}; 5-Year Outlook: {_display(record.school_future_score)}",
        f"- Programs / Capacity / Boundary: {_display(record.special_programs)} / {_display(record.kindergarten_capacity or record.overflow)} / {_display(record.school_plan)}",
        "",
        "**Zoning / Approved Use**",
        f"- Zoning: {_display(record.zoning)}; Approved Use: {_display(record.approved_use)}",
        f"- Official Evidence: zoning={_display(record.official_zoning_confirmed)} ({_display(record.official_zoning_source_url, not_available=True)}); approved use={_display(record.official_approved_use_confirmed)} ({_display(record.official_approved_use_source_url, not_available=True)})",
        f"- Permits / Violations: building={_display(record.building_permits)}; occupancy={_display(record.occupancy_permits)}; secondary_suite={_display(record.secondary_suite_permits)}; two_family={_display(record.two_family_dwelling_status)}; outstanding={_display(record.outstanding_permits)}; bylaw={_display(record.bylaw_violations)}",
        f"- FSR / Units / Height: {_display(record.maximum_fsr)} / {_display(record.maximum_units)} / {_display(record.maximum_height)}",
        f"- Buildability: theoretical={_display(record.theoretical_buildable_sqft)}, realistic={_display(record.realistic_buildable_sqft)}, score={_display(record.buildability_score)}",
        f"- TOA: distance to SkyTrain={_display(record.distance_to_skytrain_m)} m; tier={_display(record.toa_tier)}; theoretical height/FSR={_display(record.theoretical_height)}/{_display(record.theoretical_fsr)}; rezoning={_display(record.rezoning_requirement)}; rental={_display(record.rental_requirement)}; below-market={_display(record.below_market_requirement)}; assembly={_display(record.assembly_requirement)}",
        "",
        "**Redevelopment Scenarios**",
    ]
    lines.extend([_scenario_line(scenario) for scenario in scenario_records] or ["- UNKNOWN"])
    lines.extend([
        f"- Future Product / Best Economic Product: {_display(record.best_economic_product)}",
        f"- Duplex area: {_display(record.max_duplex_area or record.duplex_area)}; front/rear unit={_display(record.estimated_front_unit_area)}; rear unit={_display(record.estimated_rear_unit_area)}",
        f"- Multiplex: max realistic units={_display(record.max_realistic_multiplex_units)}; area={_display(record.multiplex_area)}",
        f"- Land-to-Product Score: {_display(record.land_to_product_score)}",
        f"- TOA: {_display(record.toa or record.toa_status, not_available=True)}; Assembly: {_display(record.assembly_score, not_available=True)}; Character: {_display(record.character_status or record.heritage_status, not_available=True)}",
        "",
        "**Estimated Renovation**",
        f"- Tier / Estimate: {_display(record.renovation_tier)} / {_currency(record.renovation_estimate)}",
        "- Final Strategy: heating, paint, flooring, cabinets, closets, and lighting fixtures only; major wall/electrical/plumbing/foundation/structural work is disqualifying.",
        f"- Current House Utility: {_display(record.current_house_utility_score)}; 5–10 year use: {_display(record.current_house_use_status)}; reasons={_display(record.current_house_use_reasons)}",
        "",
        "**Permanent Defects**",
        f"- {_display(record.permanent_defects)}",
        "",
        "**Fixable Defects**",
        f"- {_display(record.fixable_defects)}",
        "",
        "**Comparable Value**",
        f"- Expected Market Value: {_currency(record.expected_market_value)}; Price Dislocation: {_percent(record.price_dislocation_pct)}; Price Safety Margin: {_percent(record.price_safety_margin_pct)}",
        f"- Window / Count / Confidence: {_display(record.comp_window_days)} days / {_display(record.comp_count)} / {_display(record.comp_confidence)}",
        f"- Medians: Median Sold={_currency(record.median_sold_price)}; median lot={_display(record.median_lot_size)} sqft; median house={_display(record.median_house_size)} sqft; sold $/lot sqft={_currency(record.median_price_per_lot_sqft)}; assessment ratio={_percent(record.median_assessment_ratio)}",
        f"- Neighbourhood Benchmark: median={_currency(record.neighbourhood_comparable_median)}; count={_display(record.neighbourhood_benchmark_count)}; window={_display(record.neighbourhood_benchmark_window_days)} days; confidence={_display(record.neighbourhood_benchmark_confidence)}",
        f"- Relative Affordability (asking / neighbourhood benchmark): {_display(record.relative_affordability_index)}",
        f"- Land Value Proxy: {_currency(record.land_value_proxy)}; building-value method={_display(record.current_use_building_value_method)}; Adjusted Land $/sqft: {_currency(record.adjusted_land_price_per_sqft)}; Land Discount/Premium: {_percent(record.land_discount_or_premium_pct)}",
        f"- Comparable Conflicts Excluded: {_display(record.comparable_conflict_count)}; IDs={_display(record.comparable_conflict_ids)}",
        "",
        "**Scores**",
        f"- LIVE SCORE: {_display(live_score)} — {_independent_score_label(snapshot, live_score)}",
        f"- LAND SCORE: {_display(land_score)} — {_independent_score_label(snapshot, land_score)}",
        f"- INVESTMENT SCORE: {_display(investment_score)} — {_independent_score_label(snapshot, investment_score)}",
        f"- TOTAL SCORE: {_display(total_score)} — {_total_label(snapshot, total_score)}",
        f"- Total Score Label: {_total_label(snapshot, total_score)}",
        "- Score Breakdown (points / maximum — label):",
        *_score_breakdown_lines(snapshot),
        f"- DATA CONFIDENCE: {_display(confidence)} ({_display(confidence_band)})",
        f"- Data Verification: {_display(record.data_verification_status)}; manual verification required={_display(record.manual_verification_required)}; sources={_display(record.provisional_source_registry_ids)}",
        f"- Candidate Status: {state.state.value}; Recommendation: {state.recommendation}",
        f"- State Reasons: {_display(state.reason_codes)}",
        f"- Opportunity Tags: {_display(record.opportunity_tags)}",
        "",
        "**Investment Thesis**",
        build_investment_thesis(record, snapshot, state),
        "",
        "**Source Provenance**",
    ])
    if sources:
        lines.extend(
            f"- {source.field_name}: type={source.source_type.value}; registry={_display(source.source_registry_id)}; source_run_id={_display(source.source_run_id)}; weekly_run_id={_display(source.weekly_run_id)}; url={source.source_url}; retrieved_at={source.retrieved_at.isoformat()}; raw={_display(source.raw_value)}; normalized={_display(source.normalized_value)}; source_version={_display(source.source_version)}; evidence={source.evidence_status.value}; match={_display(source.match_method)}; query_scope={_display(source.query_scope)}; raw_snapshot_path={_display(source.raw_snapshot_path)}; notes={_display(source.notes)}"
            for source in sources
        )
    else:
        lines.append("- UNKNOWN")
    return "\n".join(lines)


def summarize_research_run(
    records: Iterable[PropertyRecord] | Any,
    *,
    snapshots: Mapping[str, ScoreSnapshot] | Iterable[ScoreSnapshot] | None = None,
    resolutions: Mapping[str, StateResolution] | None = None,
    conflicts: Iterable[DataConflict | Mapping[str, Any]] | None = None,
    case_quality_counts: Mapping[str, int] | None = None,
    coverage_gaps: Iterable[str] | None = None,
    stage1_candidates: int | None = None,
    stage1_research_queue: int | None = None,
    verification_queue_count: int | None = None,
    manual_verification_queue_count: int | None = None,
    verification_items: Iterable[Any] | None = None,
    special_situation_count: int | None = None,
    deep_research_count: int | None = None,
    generated_at: datetime | None = None,
    primary_source_status: str = "NOT_RUN",
    primary_source_names: Iterable[str] | None = None,
    fallback_used: bool = False,
    source_priority: Iterable[str] | None = None,
    source_runs: Iterable[Any] | None = None,
    stage1_counts: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    if hasattr(records, "records") and not isinstance(records, (list, tuple, set, Mapping)):
        result = records
        if source_runs is None:
            source_runs = getattr(result, "source_runs", None)
        if snapshots is None:
            snapshots = getattr(result, "snapshots", None)
        if resolutions is None:
            resolutions = getattr(result, "resolutions", None)
        if conflicts is None:
            conflicts = getattr(result, "conflicts", None)
        if generated_at is None:
            generated_at = getattr(result, "observed_at", None)
        records = getattr(result, "records", ())
    materialized = list(records)
    source_runs_materialized = list(source_runs or ())
    resolved = [_resolution_for(record, resolutions) for record in materialized]
    resolved_by_id = {
        record.property_id: resolution
        for record, resolution in zip(materialized, resolved)
    }
    # Price scope is a hard research gate in the final policy. Out-of-range
    # observations remain visible in their own report section, but must not
    # inflate the candidate bands or the main verification queue.
    primary_candidate_records = [
        record
        for record in materialized
        if _primary_detached(record) and _in_price_scope(record)
    ]
    supplementary_candidate_records = [
        record
        for record in materialized
        if _supplementary_duplex(record)
    ]
    primary_status_counts = Counter(
        resolved_by_id[record.property_id].state.value
        for record in primary_candidate_records
    )
    supplementary_status_counts = Counter(
        resolved_by_id[record.property_id].state.value
        for record in supplementary_candidate_records
    )
    all_status_counts = Counter(item.state.value for item in resolved)
    reductions = [
        abs(record.price_reduction_pct)
        for record in materialized
        if record.price_reduction_pct is not None and record.price_reduction_pct < 0
    ]
    assessment_discounts = [record.asking_vs_total_assessment for record in materialized if record.asking_vs_total_assessment is not None]
    land_discounts = [record.asking_vs_land_assessment for record in materialized if record.asking_vs_land_assessment is not None]
    confidence_band_counts = Counter()
    for record in materialized:
        snapshot = _snapshot_for(record, snapshots)
        band = snapshot.data_confidence_band if snapshot else record.data_confidence_band
        if band:
            confidence_band_counts[band] += 1
    lot_pool = [
        record
        for record in materialized
        if _primary_detached(record) and _in_price_scope(record)
    ]
    largest_lot = max(lot_pool, key=lambda item: item.lot_area_sqft or -1, default=None)
    conflict_list = list(conflicts or ())
    priority_pool = [
        record
        for record in materialized
        if _in_price_scope(record)
        and (_primary_detached(record) or _supplementary_duplex(record))
        if resolved_by_id[record.property_id].listing_active
        and not resolved_by_id[record.property_id].hard_veto
        and not resolved_by_id[record.property_id].special_situation
    ]
    if not priority_pool:
        priority_pool = [
            record
            for record in materialized
            if _in_price_scope(record)
            and (_primary_detached(record) or _supplementary_duplex(record))
            if not resolved_by_id[record.property_id].hard_veto
            and not resolved_by_id[record.property_id].special_situation
        ]
    priority_records = sorted(
        priority_pool,
        key=lambda item: (-(item.investment_score if item.investment_score is not None else 0), item.address),
    )[:5]
    primary_detached_count = sum(1 for record in materialized if _primary_detached(record))
    supplementary_duplex_count = sum(1 for record in materialized if _supplementary_duplex(record))
    excluded_asset_count = sum(
        1
        for record in materialized
        if not _primary_detached(record) and not _supplementary_duplex(record)
    )
    summary = {
        "properties_scanned": len(materialized),
        "primary_detached_count": primary_detached_count,
        "supplementary_duplex_count": supplementary_duplex_count,
        "excluded_asset_count": excluded_asset_count,
        "in_scope_primary_count": len(primary_candidate_records),
        "in_scope_supplementary_duplex_count": len(supplementary_candidate_records),
        "out_of_range_count": sum(
            1
            for record in materialized
            if str(getattr(record, "range_classification", "UNKNOWN") or "UNKNOWN").upper()
            == "OUT_OF_RANGE"
        ),
        "stage1_candidates": stage1_candidates if stage1_candidates is not None else len(materialized),
        "stage1_research_queue": stage1_research_queue if stage1_research_queue is not None else len(materialized),
        "verification_queue_count": verification_queue_count if verification_queue_count is not None else primary_status_counts.get(CandidateStatus.NEED_VERIFICATION.value, 0),
        "manual_verification_queue_count": manual_verification_queue_count if manual_verification_queue_count is not None else sum(
            1
            for item in (verification_items or ())
            if str(getattr(item, "details", {}).get("verification_type", "")).startswith("MANUAL_PROVISIONAL_")
        ),
        "special_situation_count": special_situation_count if special_situation_count is not None else all_status_counts.get(CandidateStatus.SPECIAL_SITUATION.value, 0),
        "deep_research_count": deep_research_count if deep_research_count is not None else len(materialized),
        "A_candidates": primary_status_counts.get(CandidateStatus.A_STRONG_CANDIDATE.value, 0),
        "B_candidates": primary_status_counts.get(CandidateStatus.B_WATCH.value, 0),
        "C_candidates": primary_status_counts.get(CandidateStatus.C_OPPORTUNISTIC.value, 0),
        "D_rejects": primary_status_counts.get(CandidateStatus.D_REJECT.value, 0) + primary_status_counts.get(CandidateStatus.REJECT.value, 0),
        "NEED_VERIFICATION": primary_status_counts.get(CandidateStatus.NEED_VERIFICATION.value, 0),
        "SPECIAL_SITUATION": all_status_counts.get(CandidateStatus.SPECIAL_SITUATION.value, 0),
        "supplementary_A_candidates": supplementary_status_counts.get(CandidateStatus.A_STRONG_CANDIDATE.value, 0),
        "supplementary_B_candidates": supplementary_status_counts.get(CandidateStatus.B_WATCH.value, 0),
        "supplementary_C_candidates": supplementary_status_counts.get(CandidateStatus.C_OPPORTUNISTIC.value, 0),
        "supplementary_NEED_VERIFICATION": supplementary_status_counts.get(CandidateStatus.NEED_VERIFICATION.value, 0),
        "largest_price_reduction": max(reductions, default=None),
        "largest_assessment_discount": min(assessment_discounts, default=None),
        "largest_land_assessment_discount": min(land_discounts, default=None),
        "largest_lot_opportunity": {
            "address": largest_lot.address,
            "lot_area_sqft": largest_lot.lot_area_sqft,
        } if largest_lot else None,
        "confidence_band_counts": dict(confidence_band_counts),
        "conflicts": len(conflict_list),
        "coverage_gaps": list(dict.fromkeys(coverage_gaps or ())),
        "primary_source_status": primary_source_status,
        "primary_source_names": list(primary_source_names or ()),
        "fallback_used": fallback_used,
        "source_priority": list(source_priority or ()),
        "case_quality_counts": dict(case_quality_counts or {}),
        "priority_case_reviews": [
            {
                "address": record.address,
                "investment_score": record.investment_score,
                "status": resolved_item.state.value,
            }
            for record in priority_records
            for resolved_item in [resolved_by_id[record.property_id]]
        ],
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
    }
    summary["crawl"] = _crawl_summary(source_runs_materialized)
    summary["stage1_counts"] = _stage1_counts(
        materialized,
        source_runs=source_runs_materialized,
        overrides=stage1_counts,
        verification_count=summary["NEED_VERIFICATION"],
    )
    return summary


def _summary_lines(summary: Mapping[str, Any]) -> list[str]:
    lines = [
        f"- properties_scanned: {summary['properties_scanned']}",
        f"- primary_detached_count: {summary.get('primary_detached_count', 0)}; supplementary_duplex_count: {summary.get('supplementary_duplex_count', 0)}; excluded_asset_count: {summary.get('excluded_asset_count', 0)}",
        f"- in_scope_primary_count: {summary.get('in_scope_primary_count', 0)}; in_scope_supplementary_duplex_count: {summary.get('in_scope_supplementary_duplex_count', 0)}; out_of_range_count: {summary.get('out_of_range_count', 0)}",
        f"- stage1_candidates: {summary['stage1_candidates']}",
        f"- stage1_research_queue: {summary['stage1_research_queue']}; verification_queue: {summary['verification_queue_count']}; special_situation_queue: {summary['special_situation_count']}",
        f"- manual_provisional_verification_queue: {summary.get('manual_verification_queue_count', 0)}",
        f"- deep_research_count: {summary['deep_research_count']}",
        f"- A_candidates: {summary['A_candidates']}; B_candidates: {summary['B_candidates']}; C_candidates: {summary['C_candidates']}; D_rejects: {summary['D_rejects']}; NEED_VERIFICATION: {summary['NEED_VERIFICATION']}; SPECIAL_SITUATION: {summary['SPECIAL_SITUATION']}",
        f"- supplementary_statuses: A={summary.get('supplementary_A_candidates', 0)}; B={summary.get('supplementary_B_candidates', 0)}; C={summary.get('supplementary_C_candidates', 0)}; NEED_VERIFICATION={summary.get('supplementary_NEED_VERIFICATION', 0)}",
        f"- largest_price_reduction: {_percent(summary['largest_price_reduction'])}",
        f"- largest_assessment_discount: {_percent(summary['largest_assessment_discount'])}",
        f"- largest_land_assessment_discount: {_percent(summary['largest_land_assessment_discount'])}",
        f"- largest_lot_opportunity: {_display(summary['largest_lot_opportunity'])}",
        f"- confidence_band_counts: {_display(summary['confidence_band_counts'])}",
        f"- conflicts: {summary['conflicts']}",
        f"- case_quality_counts: {_display(summary['case_quality_counts'])}",
        f"- priority_case_reviews: {_display(summary['priority_case_reviews'])}",
        f"- coverage_gaps: {_display(summary['coverage_gaps'])}",
        f"- primary_source_status: {summary['primary_source_status']}; fallback_used: {summary['fallback_used']}; priority: {_display(summary['source_priority'])}",
    ]
    stage1 = summary.get("stage1_counts", {})
    lines.extend([
        f"- stage1_raw_cards: {stage1.get('raw_cards', 0)}",
        f"- stage1_normalized_records: {stage1.get('normalized_records', 0)}",
        f"- stage1_in_range: {stage1.get('in_range', 0)}",
        f"- stage1_excluded: {stage1.get('excluded', 0)}",
        f"- stage1_NEED_VERIFICATION: {stage1.get('NEED_VERIFICATION', 0)}",
    ])
    for source_name, crawl in (summary.get("crawl", {}) or {}).items():
        lines.append(
            f"- crawl[{source_name}]: status={crawl.get('status', 'UNKNOWN')}; coverage={crawl.get('coverage', 'PARTIAL')}; "
            f"pages_fetched={crawl.get('pages_fetched', 0)}; cards_found={crawl.get('cards_found', 0)}; "
            f"details_fetched={crawl.get('details_fetched', 0)}; rows_extracted={crawl.get('rows_extracted', 0)}; "
            f"duplicates={crawl.get('duplicates', 0)}; errors={_display(crawl.get('errors', []))}; "
            f"coverage_gaps={_display(crawl.get('coverage_gaps', []))}"
        )
    return lines


def build_weekly_report(
    records: Iterable[PropertyRecord],
    *,
    snapshots: Mapping[str, ScoreSnapshot] | Iterable[ScoreSnapshot] | None = None,
    resolutions: Mapping[str, StateResolution] | None = None,
    scenarios: Mapping[str, Iterable[RedevelopmentScenario]] | None = None,
    provenance: Mapping[str, Iterable[SourceRecord]] | None = None,
    events: Iterable[Any] | None = None,
    conflicts: Iterable[DataConflict | Mapping[str, Any]] | None = None,
    case_quality_counts: Mapping[str, int] | None = None,
    coverage_gaps: Iterable[str] | None = None,
    stage1_candidates: int | None = None,
    stage1_research_queue: int | None = None,
    verification_queue_count: int | None = None,
    manual_verification_queue_count: int | None = None,
    special_situation_count: int | None = None,
    deep_research_count: int | None = None,
    generated_at: datetime | None = None,
    run_type: str = "weekly",
    source_runs: Iterable[Any] | None = None,
    enrichment_results: Mapping[str, Any] | None = None,
    verification_items: Iterable[Any] | None = None,
    stage1_included_property_ids: set[str] | None = None,
    stage_status: Mapping[str, Any] | None = None,
    comparable_results: Mapping[str, Any] | None = None,
    redevelopment_results: Mapping[str, Any] | None = None,
    delta_events: Iterable[Any] | None = None,
    primary_source_status: str = "NOT_RUN",
    primary_source_names: Iterable[str] | None = None,
    fallback_used: bool = False,
    source_priority: Iterable[str] | None = None,
    stage1_counts: Mapping[str, int] | None = None,
) -> str:
    materialized = list(records)
    verification_items_materialized = list(verification_items or ())
    conflict_list = list(conflicts or ())
    summary = summarize_research_run(
        materialized,
        snapshots=snapshots,
        resolutions=resolutions,
        conflicts=conflict_list,
        case_quality_counts=case_quality_counts,
        coverage_gaps=coverage_gaps,
        stage1_candidates=stage1_candidates,
        stage1_research_queue=stage1_research_queue,
        verification_queue_count=verification_queue_count,
        manual_verification_queue_count=manual_verification_queue_count,
        verification_items=verification_items_materialized,
        special_situation_count=special_situation_count,
        deep_research_count=deep_research_count,
        generated_at=generated_at,
        primary_source_status=primary_source_status,
        primary_source_names=primary_source_names,
        fallback_used=fallback_used,
        source_priority=source_priority,
        source_runs=source_runs,
        stage1_counts=stage1_counts,
    )
    resolved = {record.property_id: _resolution_for(record, resolutions) for record in materialized}
    active = [
        record for record in materialized
        if _primary_detached(record)
        and _in_price_scope(record)
        if resolved[record.property_id].listing_active
        and resolved[record.property_id].state in {
            CandidateStatus.A_STRONG_CANDIDATE,
            CandidateStatus.B_WATCH,
            CandidateStatus.C_OPPORTUNISTIC,
        }
        and (
            stage1_included_property_ids is None
            or record.property_id in stage1_included_property_ids
        )
    ]
    active.sort(
        key=lambda record: (
            -(record.investment_score if record.investment_score is not None else 0),
            record.address,
        )
    )
    sold_or_removed = [record for record in materialized if not resolved[record.property_id].listing_active]
    excluded = [
        record for record in materialized
        if resolved[record.property_id].hard_veto or resolved[record.property_id].special_situation
    ]
    lower_priority = [
        record
        for record in materialized
        if _in_price_scope(record)
        and (_primary_detached(record) or _supplementary_duplex(record))
        if resolved[record.property_id].listing_active
        and record not in active
        and record not in excluded
        and resolved[record.property_id].state is not CandidateStatus.NEED_VERIFICATION
    ]
    verification_queue = [
        record
        for record in materialized
        if _primary_detached(record)
        and _in_price_scope(record)
        if resolved[record.property_id].listing_active
        and resolved[record.property_id].state is CandidateStatus.NEED_VERIFICATION
    ]
    reductions = sorted(
        [record for record in materialized if record.price_reduction_pct is not None and record.price_reduction_pct < 0],
        key=lambda record: record.price_reduction_pct or 0,
    )

    lines = [
        f"# Greater Vancouver Detached House Research — {run_type}",
        "",
        f"run_type={run_type}",
        f"generated_at={summary['generated_at']}",
        "",
        "## Executive Summary",
        "",
        *_summary_lines(summary),
        "",
        "## Market Changes",
        "",
        (
            "Phase 1 uses static local case evidence; this section is not a current market scan."
            if run_type == "phase1_validation"
            else "Current changes are limited to supplied observations and watchlist events."
        ),
        "",
        "## Top New Candidates",
        "",
    ]
    if active:
        for record in active:
            lines.extend([
                build_candidate_card(
                    record,
                    _snapshot_for(record, snapshots),
                    scenarios=(scenarios or {}).get(record.property_id, ()),
                    resolution=resolved[record.property_id],
                    provenance=(provenance or {}).get(record.property_id, ()),
                ),
                "",
            ])
    else:
        lines.append("- NONE")
        lines.append("")

    provisional_records = sorted(
        [
            record
            for record in materialized
            if record.manual_verification_required
            and _primary_detached(record)
            and _in_price_scope(record)
        ],
        key=lambda record: (
            -(record.investment_score if record.investment_score is not None else 0),
            record.address,
        ),
    )
    lines.extend(["## Provisional / Unverified Source Observations", ""])
    if provisional_records:
        for record in provisional_records:
            lines.extend([
                build_candidate_card(
                    record,
                    _snapshot_for(record, snapshots),
                    scenarios=(scenarios or {}).get(record.property_id, ()),
                    resolution=resolved[record.property_id],
                    provenance=(provenance or {}).get(record.property_id, ()),
                ),
                "",
            ])
    else:
        lines.append("- NONE")
        lines.append("")

    supplementary_duplexes = sorted(
        [record for record in materialized if _supplementary_duplex(record)],
        key=lambda record: (-(record.investment_score or 0), record.address),
    )
    lines.extend(["## Supplementary Duplex Observations", ""])
    if supplementary_duplexes:
        for record in supplementary_duplexes:
            lines.extend([
                build_candidate_card(
                    record,
                    _snapshot_for(record, snapshots),
                    scenarios=(scenarios or {}).get(record.property_id, ()),
                    resolution=resolved[record.property_id],
                    provenance=(provenance or {}).get(record.property_id, ()),
                ),
                "",
            ])
    else:
        lines.append("- NONE")

    excluded_assets = [
        record
        for record in materialized
        if not _primary_detached(record) and not _supplementary_duplex(record)
    ]
    lines.extend(["## Excluded Non-Target Asset Types", ""])
    if excluded_assets:
        lines.extend(
            f"- {record.address}: asset_class={_display(record.research_asset_class)}; ask={_currency(record.asking)}; reason={_display(record.research_asset_classification_notes)}"
            for record in sorted(excluded_assets, key=lambda item: item.address)
        )
    else:
        lines.append("- NONE")

    out_of_range = [
        record for record in materialized if record.range_classification == "OUT_OF_RANGE"
    ]
    lines.extend(["## Out-of-Range Price Observations", ""])
    if out_of_range:
        lines.extend(
            f"- {record.address}: ask={_currency(record.asking)}; bucket={_display(record.market_bucket)}; range={_display(record.range_classification)}"
            for record in sorted(out_of_range, key=lambda item: item.address)
        )
    else:
        lines.append("- NONE")

    lines.extend(["## Verification Queue", ""])
    if verification_queue:
        lines.extend(
            f"- {record.address}: state=NEED_VERIFICATION; reasons={_display(resolved[record.property_id].reason_codes)}"
            for record in verification_queue
        )
    else:
        lines.append("- NONE")

    lines.extend(["## Major Price Reductions", ""])
    if reductions:
        lines.extend(
            f"- {record.address}: {_percent(record.price_reduction_pct)}; current ask={_currency(record.asking)}; DOM={_display(record.dom)}"
            for record in reductions
        )
    else:
        lines.append("- NONE")
    lines.extend(["", "## Watchlist Changes", ""])
    event_list = list(events or ())
    if event_list:
        for event in event_list:
            if isinstance(event, Mapping):
                lines.append(f"- {event.get('event_type', 'UNKNOWN')}: {event.get('property_id', 'UNKNOWN')} — {_display(event.get('payload'))}")
            else:
                lines.append(f"- {event.event_type}: {event.property_id} — {_display(event.payload)}")
    else:
        lines.append("- NONE")

    lines.extend(["", "## Sold / Removed", ""])
    if sold_or_removed:
        for record in sold_or_removed:
            lines.extend([
                f"- {record.address}: status={_display(record.listing_status)}; historical state={resolved[record.property_id].state.value}",
            ])
    else:
        lines.append("- NONE")

    lines.extend(["", "## Data Conflicts", ""])
    if conflict_list:
        for conflict in conflict_list:
            if isinstance(conflict, Mapping):
                lines.append(f"- {conflict.get('property_id', 'UNKNOWN')} / {conflict.get('field_name', 'UNKNOWN')}: {_display(conflict)}")
            else:
                lines.append(f"- {conflict.property_id} / {conflict.field_name}: {conflict.conflict_type}; status={conflict.resolution_status}")
    else:
        lines.append("- NONE")

    operational_sources = list(source_runs or ())
    lines.extend(["", "## Acquisition Source Policy", ""])
    lines.extend([
        f"- primary_source_status: {summary['primary_source_status']}",
        f"- primary_source_names: {_display(summary['primary_source_names'])}",
        f"- fallback_used: {summary['fallback_used']}",
        f"- priority_order: {_display(summary['source_priority'])}",
        "- Public REALTOR.ca/Zolo rows may be accepted as PROVISIONAL_SINGLE_SOURCE observations; they are not relabeled as a direct MLS feed.",
    ])
    if operational_sources:
        lines.extend(["", "## Source Run Status", ""])
        for source in operational_sources:
            status = getattr(getattr(source, "status", None), "value", getattr(source, "status", "UNKNOWN"))
            errors = getattr(source, "errors", []) or []
            coverage_complete = getattr(source, "coverage_complete", None)
            coverage = (
                "COMPLETE"
                if status == "SUCCESS" and coverage_complete is True
                else "SNAPSHOT"
                if status == "SUCCESS" and coverage_complete is False
                else "BLOCKED_POLICY"
                if status == "BLOCKED_POLICY"
                else "UNKNOWN"
            )
            lines.append(
                f"- {getattr(source, 'source_name', 'UNKNOWN')}: role={_display(getattr(source, 'source_role', None))}; registry={_display(getattr(source, 'source_registry_id', None))}; status={status}; coverage={coverage}; priority={_display(getattr(source, 'priority', None))}; source_run_id={_display(getattr(source, 'source_run_id', None))}; weekly_run_id={_display(getattr(source, 'weekly_run_id', None))}; records={len(getattr(source, 'records', []) or [])}; snapshots={len(getattr(source, 'snapshots', []) or [])}; errors={_display(errors)}"
            )
    provider_statuses: dict[str, list[str]] = {}
    provider_errors: dict[str, list[Any]] = {}
    for result in (enrichment_results or {}).values():
        for provider in getattr(result, "provider_results", []) or []:
            provider_statuses.setdefault(provider.provider_name, []).append(provider.status)
            provider_errors.setdefault(provider.provider_name, []).extend(provider.warnings)
    if provider_statuses:
        lines.extend(["", "## Enrichment Provider Status", ""])
        for provider_name in sorted(provider_statuses):
            statuses = ",".join(provider_statuses[provider_name])
            lines.append(
                f"- {provider_name}: status={statuses}; warnings={_display(provider_errors.get(provider_name))}"
            )
    if verification_items is not None:
        lines.extend(["", "## Official Verification Queue", ""])
        verification_materialized = verification_items_materialized
        items = [
            item
            for item in verification_materialized
            if not str(getattr(item, "details", {}).get("verification_type", "")).startswith("MANUAL_PROVISIONAL_")
        ]
        if items:
            lines.extend(
                f"- {getattr(item, 'property_id', 'UNKNOWN')} / {getattr(item, 'source_registry_id', 'UNKNOWN')}: status={getattr(item, 'status', 'UNKNOWN')}; reason={_display(getattr(item, 'reason', None))}; match={_display(getattr(item, 'match_method', None))}; raw={_display(getattr(item, 'raw_snapshot_path', None))}"
                for item in items
            )
        else:
            lines.append("- NONE")
        lines.extend(["", "## Manual Provisional Verification Queue", ""])
        manual_items = [
            item
            for item in verification_materialized
            if str(getattr(item, "details", {}).get("verification_type", "")).startswith("MANUAL_PROVISIONAL_")
        ]
        if manual_items:
            lines.extend(
                f"- {getattr(item, 'property_id', 'UNKNOWN')} / {getattr(item, 'source_registry_id', 'UNKNOWN')}: status={getattr(item, 'status', 'UNKNOWN')}; reason={_display(getattr(item, 'reason', None))}; source={_display(getattr(item, 'source_url', None))}"
                for item in manual_items
            )
        else:
            lines.append("- NONE")
    if stage_status:
        lines.extend(["", "## Stage Status", ""])
        lines.extend(f"- {name}: {value}" for name, value in stage_status.items())
    if comparable_results:
        lines.extend(["", "## Comparable Summary", ""])
        for property_id, result in comparable_results.items():
            lines.append(
                f"- {property_id}: window={_display(getattr(result, 'comp_window_days', None))} days; count={_display(getattr(result, 'comp_count', None))}; confidence={_display(getattr(result, 'comp_confidence', None))}; valuation_status={_display(getattr(result, 'valuation_status', None))}; expected={_currency(getattr(result, 'expected_market_value', None))}; dislocation={_percent(getattr(result, 'price_dislocation_pct', None))}; Neighbourhood Benchmark={_currency(getattr(result, 'neighbourhood_comparable_median', None))} / {_display(getattr(result, 'neighbourhood_benchmark_count', None))} comps ({_display(getattr(result, 'neighbourhood_benchmark_confidence', None))}); conflicts excluded={_display(getattr(result, 'comparable_conflict_count', None))} IDs={_display(getattr(result, 'comparable_conflict_ids', None))}"
            )
    if redevelopment_results:
        lines.extend(["", "## Redevelopment Economics", ""])
        for property_id, result in redevelopment_results.items():
            best = getattr(result, "best_economic_product", None)
            best = getattr(best, "value", best)
            best_scenario = next(
                (
                    scenario
                    for scenario in (getattr(result, "scenarios", []) or [])
                    if getattr(getattr(scenario, "scenario_type", None), "value", getattr(scenario, "scenario_type", None)) == best
                ),
                None,
            )
            lines.append(
                f"- {property_id}: best_product={_display(best)}; official_zoning={_display(getattr(result, 'official_zoning_evidence', None))}; scenarios={len(getattr(result, 'scenarios', []) or [])}; resale={_currency(getattr(best_scenario, 'estimated_resale_price', None))}; total_cost={_currency(getattr(best_scenario, 'total_project_cost', None))}; uplift={_currency(getattr(best_scenario, 'redevelopment_uplift', None))}; assumptions={_display(getattr(best_scenario, 'assumptions', None))}"
            )
            for scenario in getattr(result, "scenarios", []) or []:
                lines.append(f"  {_scenario_line(scenario)}")
    operational_events = list(delta_events or ())
    if operational_events:
        lines.extend(["", "## Delta Events", ""])
        for event in operational_events:
            lines.append(
                f"- {getattr(event, 'event_type', 'UNKNOWN')}: {getattr(event, 'property_id', 'UNKNOWN')} — {_display(getattr(event, 'payload', {}))}"
            )
    if conflict_list:
        lines.extend(["", "## Open Conflict Queue", ""])
        for conflict in conflict_list:
            if isinstance(conflict, Mapping):
                lines.append(f"- {conflict.get('property_id', 'UNKNOWN')} / {conflict.get('field_name', 'UNKNOWN')}: sources={_display(conflict.get('conflicting_sources'))}; values={_display(conflict.get('source_values'))}")
            else:
                lines.append(f"- {conflict.property_id} / {conflict.field_name}: sources={_display(conflict.conflicting_sources)}; values={_display(conflict.source_values)}")

    if run_type == "phase1_validation" and not active:
        lines.extend(["", "## Phase 1 Case Review Cards", ""])
        for record in sorted(materialized, key=lambda item: item.address):
            lines.extend([
                build_candidate_card(
                    record,
                    _snapshot_for(record, snapshots),
                    scenarios=(scenarios or {}).get(record.property_id, ()),
                    resolution=resolved[record.property_id],
                    provenance=(provenance or {}).get(record.property_id, ()),
                ),
                "",
            ])

    lines.extend(["", "## Hard Veto / Special Situations", ""])
    if excluded:
        for record in excluded:
            lines.append(
                f"- {record.address}: {resolved[record.property_id].state.value}; reasons={_display(resolved[record.property_id].reason_codes)}"
            )
    else:
        lines.append("- NONE")

    lines.extend(["", "## Lower Priority / Rejected", ""])
    if lower_priority:
        lines.extend(
            f"- {record.address}: state={resolved[record.property_id].state.value}; reasons={_display(resolved[record.property_id].reason_codes)}"
            for record in lower_priority
        )
    else:
        lines.append("- NONE")

    lines.extend(["", "## Data Source Limitations", ""])
    if summary["coverage_gaps"]:
        lines.extend(f"- {gap}" for gap in summary["coverage_gaps"])
    else:
        lines.append("- NONE")

    lines.extend(["", "## Recommended Site Visits", ""])
    site_visits = [record for record in active if resolved[record.property_id].site_visit_eligible]
    if site_visits:
        lines.extend(
            f"- {record.address}: investment={_display((snapshots or {}).get(record.property_id).investment_score if isinstance(snapshots, Mapping) and (snapshots or {}).get(record.property_id) else record.investment_score)}; state={resolved[record.property_id].state.value}"
            for record in site_visits
        )
    else:
        lines.append("- NONE")
    return "\n".join(lines).rstrip() + "\n"


def write_weekly_report(path: str | Path, report: str) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


def write_latest_report(path: str | Path, report: str) -> Path:
    return write_weekly_report(path, report)
