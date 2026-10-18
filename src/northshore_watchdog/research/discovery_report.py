"""Bounded Markdown reporting and append-only persistence for discovery runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..config import resolve_path
from .discovery import DiscoveryStatus
from .discovery_pipeline import DiscoveryRunResult, DiscoveryPresetResult
from .sources import SourceRunResult, SourceRunStatus, SourceSnapshot
from .storage import ResearchStorage


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _display(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, (dict, list)):
        return _json(value)
    return str(value)


def _ratio_percent(value: Any) -> str:
    """Render a 0–1 ratio as a human-readable percentage for the report."""

    if value is None:
        return "UNKNOWN"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return _display(value)


def _capture_payload(item: DiscoveryPresetResult) -> dict[str, Any]:
    capture = item.capture
    return {
        "source_name": capture.source_name if capture else None,
        "source_url": capture.source_url if capture else None,
        "status_code": capture.status_code if capture else None,
        "result_count": capture.result_count if capture else item.result_count,
        "count_is_complete": capture.count_is_complete if capture else None,
        "parse_warnings": capture.parse_warnings if capture else [],
        "raw_snapshot_path": capture.raw_snapshot_path if capture else None,
        "blocked": capture.blocked if capture else None,
        "detail_requests": capture.detail_requests if capture else None,
        "detail_successes": capture.detail_successes if capture else None,
        "detail_failures": capture.detail_failures if capture else None,
        "detail_snapshot_paths": capture.detail_snapshot_paths if capture else [],
    }


def _source_run_for(
    result: DiscoveryRunResult,
    item: DiscoveryPresetResult,
) -> SourceRunResult:
    observed = result.observed_at
    if observed is None:
        raise ValueError("discovery result is missing observed_at")
    status = (
        SourceRunStatus.SUCCESS
        if item.validation is not None and item.validation.status is DiscoveryStatus.PASS
        else SourceRunStatus.FAILED
    )
    plan = item.filter_plan.model_dump(mode="json") if item.filter_plan else None
    validation = item.validation.model_dump(mode="json") if item.validation else None
    capture = _capture_payload(item)
    discovery_payload = {
        "status": item.status,
        "preset_name": item.preset_name,
        "filter_plan": plan,
        "validation": validation,
        "candidate_count": len(item.candidates),
        "result_count": item.result_count,
        "capture": capture,
        "cards": [card.model_dump(mode="json") for card in item.candidates],
        "screening": result.screening.model_dump(mode="json") if result.screening else None,
        "official_enrichment": result.official_enrichment.model_dump(mode="json") if result.official_enrichment else None,
    }
    snapshots: list[SourceSnapshot] = []
    raw_path = capture.get("raw_snapshot_path")
    if raw_path:
        path = Path(str(raw_path))
        if path.is_file():
            payload = path.read_bytes()
            snapshots.append(
                SourceSnapshot(
                    source_registry_id=f"source:{capture.get('source_name') or item.preset_name}",
                    url=str(capture.get("source_url") or path),
                    final_url=str(capture.get("source_url") or path),
                    method="GET",
                    request_kind="filtered_discovery_capture",
                    status_code=int(capture.get("status_code") or 200),
                    raw_path=str(path),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    observed_at=observed,
                    retrieved_at=observed,
                )
            )
    for detail_path in capture.get("detail_snapshot_paths", []):
        path = Path(str(detail_path))
        if not path.is_file():
            continue
        payload = path.read_bytes()
        snapshots.append(
            SourceSnapshot(
                source_registry_id=f"source:{capture.get('source_name') or item.preset_name}",
                url=str(path),
                final_url=str(path),
                method="GET",
                request_kind="listing_detail",
                status_code=200,
                raw_path=str(path),
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                observed_at=observed,
                retrieved_at=observed,
            )
        )
    source_name = str(capture.get("source_name") or item.filter_plan.source_name or item.preset_name)
    registry_id = f"source:{source_name}"
    return SourceRunResult(
        source_registry_id=registry_id,
        source_name=source_name,
        status=status,
        source_role="LISTING_DISCOVERY",
        coverage_complete=False,
        started_at=observed,
        finished_at=observed,
        records=[],
        snapshots=snapshots,
        errors=[item.error] if item.error else [],
        crawl_metrics={"discovery": discovery_payload},
        coverage_gaps=["FILTERED_RESULT_NOT_COMPLETE_MARKET_SCAN"],
    )


def persist_discovery_result(
    result: DiscoveryRunResult,
    config: Mapping[str, Any],
) -> list[int]:
    """Save discovery evidence as source runs, never as verified properties."""

    if result.dry_run or not result.preset_results:
        return []
    storage_config = config.get("storage", {})
    storage_config = storage_config if isinstance(storage_config, Mapping) else {}
    database = resolve_path(
        str(storage_config.get("sqlite_path", "outputs/research_watchdog.sqlite")),
        dict(config),
    )
    source_run_ids: list[int] = []
    with ResearchStorage(database) as storage:
        for item in result.preset_results:
            source_run_ids.append(storage.save_source_run(_source_run_for(result, item)))
        if result.official_enrichment:
            for source_run in result.official_enrichment.source_runs:
                source_run_ids.append(storage.save_source_run(source_run))
    return source_run_ids


def _preset_markdown(item: DiscoveryPresetResult) -> str:
    plan = item.filter_plan
    lines = [
        f"### {item.preset_name}",
        f"- Status: `{item.status}`",
        f"- Result count: `{_display(item.result_count)}`",
    ]
    if plan:
        lines.extend(
            [
                f"- Desired filters: `{_display(plan.desired_filters)}`",
                f"- Native filters: `{_display(plan.native_filters)}`",
                f"- Post-filters: `{_display(plan.post_filters)}`",
                f"- Unsupported filters: `{_display(plan.unsupported_filters)}`",
            ]
        )
    if item.validation:
        lines.extend(
            [
                f"- Validation: `{item.validation.status.value}`",
                f"- Validation reasons: `{_display(item.validation.reason_codes)}`",
            ]
        )
    capture = _capture_payload(item)
    detail_cards = item.capture.cards if item.capture else []
    lot_data_count = sum(
        1
        for card in detail_cards
        if card.lot_area_sqft is not None
        or card.frontage_ft is not None
        or card.depth_ft is not None
        or card.lot_dimensions
    )
    lines.extend(
        [
            f"- Source URL: `{_display(capture['source_url'])}`",
            f"- Raw capture: `{_display(capture['raw_snapshot_path'])}`",
            f"- Parse warnings: `{_display(capture['parse_warnings'])}`",
            f"- Detail pages: attempted `{_display(capture['detail_requests'])}`; "
            f"successful `{_display(capture['detail_successes'])}`; "
            f"failed `{_display(capture['detail_failures'])}`",
            f"- Lot data coverage: `{lot_data_count}/{len(detail_cards)}` cards",
        ]
    )
    return "\n".join(lines)


def render_discovery_report(result: DiscoveryRunResult) -> str:
    screening = result.screening
    lines = [
        "# Vancouver House Screening V2.7 Report",
        "",
        f"Observed at: `{_display(result.observed_at)}`",
        "",
        "## Executive Summary",
        "",
        f"- Status: `{result.status}`",
        f"- Result count: `{_display(result.result_count)}`",
        f"- Discovery candidate pool: `{result.candidate_count}`",
        f"- Stage A passed: `{len(screening.stage_a_candidates) if screening else 'UNKNOWN'}`",
        f"- Stage A excluded: `{screening.stage_a_excluded_count if screening else 'UNKNOWN'}`",
        f"- Stage B normal rows scored on both axes: `{screening.stage_b_count if screening else 'UNKNOWN'}`",
        f"- Current Use Top 10: `{len(screening.current_use_top10) if screening else 'UNKNOWN'}`",
        f"- Redevelopment Top 10: `{len(screening.redevelopment_top10) if screening else 'UNKNOWN'}`",
        f"- Dual Value Top 10: `{len(screening.dual_value_top10) if screening else 'UNKNOWN'}`",
        f"- Investment Top 10: `{len(screening.investment_top10) if screening else 'UNKNOWN'}`",
        f"- Price Value Top 10: `{len(screening.price_value_top10) if screening else 'UNKNOWN'}`",
        f"- Verification queue: `{len(screening.verification_candidates) if screening else 'UNKNOWN'}`",
        f"- Special transaction rows: `{screening.special_track_count if screening else 'UNKNOWN'}`",
        f"- Presets: `{len(result.preset_results) or len(result.filter_plans)}`",
        "",
        "## Preset Results",
        "",
    ]
    if result.preset_results:
        for item in result.preset_results:
            lines.extend([_preset_markdown(item), ""])
    else:
        lines.append("No source was executed (dry run or no enabled filtered source).\n")

    lines.extend(["## Filter Validation", ""])
    for item in result.preset_results:
        if item.validation is None:
            continue
        lines.append(
            f"- `{item.preset_name}`: `{item.validation.status.value}`; "
            f"sampled `{item.validation.sampled_card_count}` card(s); "
            f"reasons `{_display(item.validation.reason_codes)}`"
        )
    if not result.preset_results:
        lines.append("- UNKNOWN")

    lines.extend(["", "## V2.7 Unified Value-for-Money Model", ""])
    if screening:
        lines.extend(
            [
                "Stage A removes only out-of-boundary, non-target, malformed, or clearly unusable rows.",
                "Stage B scores every normal row independently on Current Use and Redevelopment. A property may rank in both lists; no property is forced into a single use class.",
                "Dual Value is a derived two-axis ranking view for every normal scored row: the score is the geometric mean of Current Use and Redevelopment, not a separate enrichment path or candidate gate.",
                "V1.4 location residual is applied from the local fixed-standard-property artifact. It replaces the coarse area tier and school proxy in the score, while listing-specific road/micro-location evidence remains separate; the residual is not confidence-discounted.",
                "Road discount is applied as the configured initial prior relative to a quiet interior street: collector -2% to -4%, ordinary arterial -5% to -8%, high-flow arterial -7% to -10%, arterial plus bus/truck -8% to -12%, and truck plus intersection/high noise -10% to -15%. The midpoint adjusts the local reference value; the interval is retained for sensitivity review.",
                "All views use one value ledger. Current Use uses `asking + Reno` as effective holding basis and the BCA total as the current-package anchor when available; Redevelopment uses BCA-improvement land basis and the area-normalized physical-lot basis, with parcel road risk applied once. Assessment and regional price discounts are explicit price-safety fields and are not counted twice as location bonuses.",
                "Court sale, partial ownership, and similar transaction/title signals are gated into SPECIAL_TRANSACTION before normal scoring.",
                "The pipeline order is data-first: validated discovery cards are enriched from configured official adapters before either score is calculated. A source failure remains UNKNOWN/FAILED and lowers only the review confidence label; it does not reduce any value or ranking score and is never filled with a guess.",
                f"- Stage A excluded: `{screening.stage_a_excluded_count}`",
                f"- Stage B evaluated rows: `{screening.stage_b_count}`",
                f"- Current Use minimum for derived strong profile: `{_display(_orthogonal_policy_value(screening, 'current_use_strong_min', 70))}`",
                f"- Redevelopment minimum for derived strong profile: `{_display(_orthogonal_policy_value(screening, 'redevelopment_strong_min', 70))}`",
                "",
            ]
        )
    else:
        lines.append("V2 screening was not run.")

    def append_leaderboard(title: str, decisions: list[Any], primary: str) -> None:
        lines.extend([f"## {title}", ""])
        if decisions:
            for index, decision in enumerate(decisions, start=1):
                card = decision.card
                current_components = ", ".join(f"{name}={value}" for name, value in decision.current_use_score_components.items())
                redevelopment_components = ", ".join(f"{name}={value}" for name, value in decision.redevelopment_score_components.items())
                geometry_area = decision.redevelopment_components.get("lot_area_geometry")
                geometry_frontage = decision.redevelopment_components.get("frontage_depth_threshold")
                geometry_total = (
                    geometry_area + geometry_frontage
                    if geometry_area is not None and geometry_frontage is not None
                    else None
                )
                listing_link = (
                    f"[Realtor.ca]({card.source_url})"
                    if card.source_url and str(card.source_url).startswith(("http://", "https://"))
                    else "UNKNOWN"
                )
                primary_value = getattr(decision, primary)
                verification = " NEED_VERIFICATION" if "NEED_VERIFICATION" in decision.flags else ""
                lines.append(
                    f"{index}. **{card.address}** — {primary.replace('_', ' ').title()} `{_display(primary_value)}`; "
                    f"Current Use `{_display(decision.current_use_score)}`; Redevelopment `{_display(decision.redevelopment_score)}`; "
                    f"Dual Value `{_display(decision.dual_value_score)}`; Confidence `{_display(decision.confidence_score)}`;{verification} "
                    f"area `{decision.area_stratum}/{decision.area_tier}`; ask `{_display(card.asking)}`; "
                    f"{_display(card.bedrooms)} bd / `{_display(card.bathrooms)}` ba; `{_display(card.finished_sqft)}` sqft; "
                    f"lot `{_display(card.lot_dimensions)}` / `{_display(card.lot_area_sqft)}` sqft; "
                    f"redevelopment geometry `{_display(geometry_total)}`/35 (area `{_display(geometry_area)}`/20 + frontage/depth `{_display(geometry_frontage)}`/15); "
                    f"MLS `{_display(card.mls_number)}`; {listing_link}; holding `{decision.holding_status}`; "
                    f"road `{decision.road_class}` / `{decision.road_discount_category}`; "
                    f"road discount `-{_ratio_percent(decision.road_discount_pct)}` "
                    f"([{_ratio_percent(decision.road_discount_low_pct)}–{_ratio_percent(decision.road_discount_high_pct)}]) "
                    f"/ `{_display(decision.road_discount_cad)}` on `{_display(decision.road_discount_basis_value)}`; "
                    f"road-adjusted standard `{_display(decision.road_adjusted_standard_property_value)}`; "
                    f"area-price ratio `{_display(decision.area_relative_price_ratio)}`; "
                    f"location residual `{_display(decision.location_residual_cad)}`; standard location value `{_display(decision.location_standard_property_value)}`; "
                    f"location residual score `{_display(decision.location_residual_score)}` ({decision.location_residual_segment or 'UNKNOWN'} / {decision.location_residual_confidence}); "
                    f"regional standard `{_display(decision.regional_standard_value)}`; regional standard dislocation `{_display(decision.regional_price_dislocation_cad)}` / `{_ratio_percent(decision.regional_price_dislocation_pct)}`; "
                    f"regional price safety `{_display(decision.regional_price_safety_score)}`; "
                    f"physical expected `{_display(decision.physical_adjusted_expected_value)}`; physical dislocation `{_display(decision.physical_price_dislocation_cad)}` / `{_ratio_percent(decision.physical_price_dislocation_pct)}`; "
                    f"BCA house proxy `{_display(decision.bca_house_value_proxy)}`; BCA-normalized land basis `{_display(decision.bca_normalized_land_basis)}` / `{_display(decision.bca_normalized_land_basis_per_sqft)}` per sqft; "
                    f"legacy physical expected house `{_display(decision.normalized_existing_house_value)}`; legacy land `{_display(decision.normalized_land_value)}`; "
                    f"legacy normalized lot basis `{_display(decision.normalized_lot_basis)}` / `{_display(decision.normalized_lot_basis_per_sqft)}` per sqft; "
                    f"Current Use all-in basis `{_display(decision.current_use_all_in_basis)}` / local ratio `{_display(decision.current_use_basis_price_ratio)}`; Reno `{_display(decision.reno_estimate)}` / score `{_display(decision.reno_score)}` / band `{decision.reno_band}` / year `{_display(decision.reno_year)}`; "
                    f"reference land `{_display(decision.normalized_reference_land_value)}` / `{_display(decision.normalized_reference_land_price_per_sqft)}` per sqft; "
                    f"physical price safety `{_display(decision.redevelopment_price_safety_score)}`; "
                    f"BCA ask/total `{_display(decision.ask_to_assessment_ratio)}`; BCA total discount `{_ratio_percent(decision.assessment_discount_pct)}`; "
                    f"land ask/assessment `{_display(decision.ask_to_land_assessment_ratio)}`; land discount `{_ratio_percent(decision.land_assessment_discount_pct)}`; "
                    f"assessment signal `{_display(decision.assessment_discount_score)}`; price safety `{_display(decision.price_safety_score)}`; investment `{_display(decision.investment_score)}`; "
                    f"school `{decision.school_proxy_status}`; profile `{decision.value_profile}`; "
                    f"area/bedroom `{_display(decision.functional_area_per_bedroom)}` sqft; "
                    f"Current components `{current_components}`; Redevelopment components `{redevelopment_components}`; "
                    f"flags `{_display(decision.flags)}`."
                )
        else:
            lines.append("None.")
        lines.append("")

    if screening:
        append_leaderboard("CURRENT_USE_TOP10", screening.current_use_top10, "current_use_score")
        append_leaderboard("REDEVELOPMENT_TOP10", screening.redevelopment_top10, "redevelopment_score")
        append_leaderboard("DUAL_VALUE_TOP10", screening.dual_value_top10, "dual_value_score")
        append_leaderboard("INVESTMENT_TOP10", screening.investment_top10, "investment_score")
        append_leaderboard("PRICE_VALUE_TOP10", screening.price_value_top10, "price_value_score")

    lines.extend(["## OFFICIAL_ENRICHMENT", ""])
    official = result.official_enrichment
    if official:
        lines.extend(
            [
                f"- Status: `{official.status}`",
                f"- Cards attempted: `{official.attempted_card_count}`",
                f"- Cards with at least one successful official adapter: `{official.enriched_card_count}`",
                f"- Adapter/status counts: `{_display(official.status_counts)}`",
                f"- Field coverage: `{_display(official.field_coverage)}`",
            ]
        )
        for source_run in official.source_runs:
            lines.append(
                f"- `{source_run.source_name}`: `{source_run.status.value}`; "
                f"snapshots `{len(source_run.snapshots)}`; warnings `{_display(source_run.warnings)}`; "
                f"errors `{_display(source_run.errors)}`"
            )
        if official.warnings:
            lines.append(f"- Bridge warnings: `{_display(official.warnings)}`")
    else:
        lines.append("- Not run for this capture (use `--official-enrich` to enrich a replay; live native discovery defaults to automatic enrichment).")

    lines.extend(["## VERIFICATION_QUEUE", ""])
    if screening and screening.verification_candidates:
        reason_counts: dict[str, int] = {}
        for decision in screening.verification_candidates:
            for reason in decision.flags:
                if reason in {
                    "CLASSIFICATION_DATA_INCOMPLETE", "ZONING_PATH_UNKNOWN",
                    "HOLDING_CONDITION_UNKNOWN", "MAINTENANCE_DETAILS_UNKNOWN",
                    "SCHOOL_PROXY_UNKNOWN", "REAR_ACCESS_UNKNOWN",
                }:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
        lines.extend(
            [
                f"`{len(screening.verification_candidates)}` scored row(s) have confidence below the verification threshold.",
                f"- Missing-data signals: `{_display(reason_counts)}`",
                "- They retain their Current Use, Redevelopment, Dual Value, Investment, and ranking scores; the queue only identifies facts for later manual confirmation.",
            ]
        )
    else:
        lines.append("None.")

    lines.extend(["", "## SPECIAL_TRANSACTION", ""])
    if screening and screening.special_track_candidates:
        for index, decision in enumerate(screening.special_track_candidates, start=1):
            card = decision.card
            lines.append(
                f"{index}. **{card.address}** — ask `{_display(card.asking)}`; "
                f"MLS `{_display(card.mls_number)}`; flags `{_display(decision.flags)}`."
            )
    else:
        lines.append("None.")

    lines.extend(["", "## STAGE_A_EXCLUDED", ""])
    if screening and screening.rejected_candidates:
        for index, decision in enumerate(screening.rejected_candidates, start=1):
            lines.append(
                f"{index}. **{decision.card.address}** — Current Use `{_display(decision.current_use_index)}`; "
                f"Redevelopment Readiness `{_display(decision.redevelopment_readiness_index)}`; "
                f"confidence `{_display(decision.classification_confidence)}`; flags `{_display(decision.flags)}`."
            )
    else:
        if screening and screening.stage_a_excluded:
            for index, decision in enumerate(screening.stage_a_excluded, start=1):
                lines.append(
                    f"{index}. **{decision.card.address}** — status `{decision.status}`; "
                    f"reason codes `{_display(decision.reason_codes)}`."
                )
        else:
            lines.append("None.")

    lines.extend(["", "## Excluded / Duplicate Cards", ""])
    if result.reason_codes or result.duplicate_flags:
        lines.append(f"- Reason codes: `{_display(result.reason_codes)}`")
        lines.append(f"- Duplicate flags: `{_display(result.duplicate_flags)}`")
    else:
        lines.append("- None recorded at this stage.")

    lines.extend(
        [
            "",
            "## Source Limitations",
            "",
            "This report is a filtered and screened candidate report, not a complete MLS-market scan or final underwriting report.",
            "When detail enrichment is enabled, every bounded result card with a detail URL is opened after native-filter validation; lot and other detail fields are merged before shortlist ranking. The official enrichment section records the automated BCA, municipal zoning/road, school-catchment, and sold-history attempts.",
            "Area-price references remain preliminary configured estimates until the comparable engine is run. Official catchment names are automated, while school quality scoring remains UNKNOWN unless an official performance mapping is configured; no manual lookup is required by the pipeline.",
            "Individual malformed cards (for example, a card without an explicit address) are skipped and retained as parse warnings; they do not stop the remaining filtered scan.",
            "A filter failure or access challenge stops the source; it does not widen acquisition to an unfiltered crawl.",
            "",
        ]
    )
    return "\n".join(lines)


def _orthogonal_policy_value(screening: Any, key: str, default: float) -> float:
    """Read a display-only threshold from the attached result when available."""
    # ScreeningResult intentionally stores scores, not its source config.  The
    # default is therefore used in reports until a config snapshot is added to
    # the persisted run schema.
    del screening
    return default


def write_discovery_report(
    result: DiscoveryRunResult,
    config: Mapping[str, Any],
) -> tuple[Path, Path]:
    discovery = config.get("discovery", {})
    discovery = discovery if isinstance(discovery, Mapping) else {}
    reports_dir = resolve_path(
        str(discovery.get("reports_dir", "reports/discovery")),
        dict(config),
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    date = (result.observed_at or datetime.now(timezone.utc)).date().isoformat()
    report_path = reports_dir / f"{date}.md"
    latest_path = reports_dir / "latest.md"
    report = render_discovery_report(result)
    report_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    return report_path, latest_path


__all__ = [
    "persist_discovery_result",
    "render_discovery_report",
    "write_discovery_report",
]
