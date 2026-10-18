#!/usr/bin/env python3
"""Run official enrichment and the orthogonal value leaderboards on saved cards."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil

from northshore_watchdog.config import load_config
from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.discovery_official import (
    DiscoveryOfficialEnrichmentResult,
    _property_id,
    enrich_discovery_cards,
    replay_cached_official_cards,
)
from northshore_watchdog.research.discovery_pipeline import DiscoveryRunResult
from northshore_watchdog.research.discovery_report import (
    persist_discovery_result,
    render_discovery_report,
)
from northshore_watchdog.research.regional_detail import regional_official_config
from northshore_watchdog.research.screening import screen_discovery_candidates
from northshore_watchdog.research.pipeline import build_official_enrichment_adapters
from northshore_watchdog.research.sources import SourceRunStatus


ROOT = Path(__file__).resolve().parents[1]
DETAIL_ARTIFACT = ROOT / "data/research/normalized/regional-detail-enrichment-2026-09-05.json"
OFFICIAL_ARTIFACT = ROOT / "data/research/normalized/regional-official-enrichment-2026-09-05.json"


def _latest_artifact(pattern: str, fallback: Path) -> Path:
    candidates = sorted((ROOT / "data/research/normalized").glob(pattern))
    return candidates[-1] if candidates else fallback


def _card_key(card: DiscoveryCard) -> str:
    return str(card.mls_number or card.source_url or card.address).strip().casefold()


def _cards_by_preset(
    result: DiscoveryRunResult,
    enriched: dict[str, DiscoveryCard],
) -> list:
    updated = []
    for item in result.preset_results:
        cards = [enriched.get(_card_key(card), card) for card in item.candidates]
        capture = item.capture
        if capture is not None:
            capture = capture.model_copy(
                update={
                    "cards": [enriched.get(_card_key(card), card) for card in capture.cards],
                },
                deep=True,
            )
        updated.append(item.model_copy(update={"candidates": cards, "capture": capture}, deep=True))
    return updated


def _aggregate_official_runs(source_runs: list) -> tuple[dict[str, int], dict[str, int], int]:
    """Rebuild coverage from the retained source-run records."""

    status_counts: dict[str, int] = {}
    field_coverage: dict[str, int] = {}
    successful_properties: set[str] = set()
    for source_run in source_runs:
        for record in source_run.records:
            status = str(record.get("status") or "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
            if status != "SUCCESS":
                continue
            property_id = record.get("property_id")
            if property_id:
                successful_properties.add(str(property_id))
            fields = record.get("fields") or {}
            if isinstance(fields, dict):
                for field in fields:
                    field_coverage[str(field)] = field_coverage.get(str(field), 0) + 1
    return status_counts, field_coverage, len(successful_properties)


def _refresh_zoning_from_cache(
    cards: list[DiscoveryCard],
    cached_result: DiscoveryRunResult,
    config: dict,
    observed_at: datetime,
) -> DiscoveryOfficialEnrichmentResult:
    """Refresh only official zoning while replaying all other local evidence."""

    if cached_result.official_enrichment is None:
        raise RuntimeError("Local official artifact has no official_enrichment payload")
    replayed_cards = replay_cached_official_cards(cards, cached_result.candidates)
    zoning_adapters = build_official_enrichment_adapters(
        config,
        source_names={"municipal-zoning"},
    )
    if not zoning_adapters:
        raise RuntimeError("municipal-zoning adapter is not configured")
    # ArcGIS is the only source queried in this mode.  Avoid carrying over
    # the general enrichment pacing (1s/request and 20-card batches), which
    # made a zoning-only refresh unnecessarily long.  The request cap and
    # geometry uniqueness checks remain unchanged.
    zoning_section = config.get("official_enrichment")
    if isinstance(zoning_section, dict):
        zoning_section["discovery_batch_size"] = len(replayed_cards)
    for adapter in zoning_adapters:
        for settings in (getattr(adapter, "config", None), getattr(getattr(adapter, "http", None), "config", None)):
            if isinstance(settings, dict):
                settings["max_requests_per_run"] = max(2 * len(replayed_cards), 1)
                settings["delay_seconds"] = 0
                settings["retry_attempts"] = 0
                settings["timeout_seconds"] = min(float(settings.get("timeout_seconds", 20)), 10.0)
    refreshed = enrich_discovery_cards(
        replayed_cards,
        config,
        observed_at=observed_at,
        adapters=zoning_adapters,
    )
    old_runs = [
        source_run
        for source_run in cached_result.official_enrichment.source_runs
        if source_run.source_name != "municipal-zoning"
    ]
    source_runs = old_runs + list(refreshed.source_runs)
    status_counts, field_coverage, successful_properties = _aggregate_official_runs(source_runs)
    evidence = [
        item
        for item in cached_result.official_enrichment.evidence
        if item.get("adapter") != "municipal-zoning"
    ] + list(refreshed.evidence)
    overall_status = (
        "COMPLETED"
        if source_runs and all(str(run.status.value) == "SUCCESS" for run in source_runs)
        else "PARTIAL"
    )
    return cached_result.official_enrichment.model_copy(
        update={
            "cards": refreshed.cards,
            "source_runs": source_runs,
            "evidence": evidence,
            "status": overall_status,
            "attempted_card_count": len(refreshed.cards),
            "enriched_card_count": successful_properties,
            "status_counts": status_counts,
            "field_coverage": field_coverage,
            "warnings": list(cached_result.official_enrichment.warnings) + list(refreshed.warnings),
        },
        deep=True,
    )


def _refresh_road_from_cache(
    cards: list[DiscoveryCard],
    cached_result: DiscoveryRunResult,
    config: dict,
    observed_at: datetime,
) -> DiscoveryOfficialEnrichmentResult:
    """Refresh all municipal-road observations while replaying other sources."""

    if cached_result.official_enrichment is None:
        raise RuntimeError("Local official artifact has no official_enrichment payload")
    replayed_cards = replay_cached_official_cards(cards, cached_result.official_enrichment.cards)
    target_cards = [_remove_cached_adapter_evidence(card, "municipal-road") for card in replayed_cards]
    road_adapters = build_official_enrichment_adapters(config, source_names={"municipal-road"})
    if not road_adapters:
        raise RuntimeError("municipal-road adapter is not configured")
    official_section = config.get("official_enrichment")
    if isinstance(official_section, dict):
        official_section["discovery_batch_size"] = len(target_cards)
    for adapter in road_adapters:
        for settings in (
            getattr(adapter, "config", None),
            getattr(getattr(adapter, "http", None), "config", None),
        ):
            if isinstance(settings, dict):
                settings["max_requests_per_run"] = max(2 * len(target_cards), 1)
                settings["delay_seconds"] = 0
                settings["retry_attempts"] = 0
                settings["timeout_seconds"] = min(float(settings.get("timeout_seconds", 20)), 12.0)
                settings["trust_env"] = False
    refreshed = enrich_discovery_cards(
        target_cards,
        config,
        observed_at=observed_at,
        adapters=road_adapters,
    )
    old_runs = [
        source_run
        for source_run in cached_result.official_enrichment.source_runs
        if source_run.source_name != "municipal-road"
    ]
    source_runs = old_runs + list(refreshed.source_runs)
    status_counts, field_coverage, successful_properties = _aggregate_official_runs(source_runs)
    evidence = [
        item
        for item in cached_result.official_enrichment.evidence
        if item.get("adapter") != "municipal-road"
    ] + list(refreshed.evidence)
    overall_status = (
        "COMPLETED"
        if source_runs and all(str(run.status.value) == "SUCCESS" for run in source_runs)
        else "PARTIAL"
    )
    return cached_result.official_enrichment.model_copy(
        update={
            "cards": refreshed.cards,
            "source_runs": source_runs,
            "evidence": evidence,
            "status": overall_status,
            "attempted_card_count": len(refreshed.cards),
            "enriched_card_count": successful_properties,
            "status_counts": status_counts,
            "field_coverage": field_coverage,
            "warnings": list(cached_result.official_enrichment.warnings) + list(refreshed.warnings),
        },
        deep=True,
    )


def _remove_cached_adapter_evidence(card, adapter_name: str):
    """Remove one adapter's old evidence before a targeted refresh."""

    raw = dict(card.raw or {})
    evidence = raw.get("_official_evidence")
    if isinstance(evidence, list):
        raw["_official_evidence"] = [
            item
            for item in evidence
            if not isinstance(item, dict) or item.get("adapter") != adapter_name
        ]
    return card.model_copy(update={"raw": raw}, deep=True)


def _refresh_bca_from_cache(
    cards: list,
    cached_result: DiscoveryRunResult,
    config: dict,
    observed_at: datetime,
) -> DiscoveryOfficialEnrichmentResult:
    """Refresh only cached non-successful BCA rows using fuzzy address lookup."""

    if cached_result.official_enrichment is None:
        raise RuntimeError("Local official artifact has no official_enrichment payload")
    cached_official = cached_result.official_enrichment
    bca_runs = [run for run in cached_official.source_runs if run.source_name == "bca-assessment"]
    if not bca_runs:
        raise RuntimeError("Local official artifact has no bca-assessment source run")
    bca_records = {
        str(record.get("property_id")): record
        for run in bca_runs
        for record in run.records
        if record.get("property_id")
    }
    failed_ids = {
        property_id
        for property_id, record in bca_records.items()
        if str(record.get("status") or "UNKNOWN") != "SUCCESS"
    }
    replayed_cards = replay_cached_official_cards(cards, cached_official.cards)
    target_cards = [
        _remove_cached_adapter_evidence(card, "bca-assessment")
        for card in replayed_cards
        if _property_id(card) in failed_ids
    ]
    if not target_cards:
        return cached_official.model_copy(update={"cards": replayed_cards}, deep=True)

    bca_adapters = build_official_enrichment_adapters(
        config,
        source_names={"bca-assessment"},
    )
    if not bca_adapters:
        raise RuntimeError("bca-assessment adapter is not configured")
    official_section = config.get("official_enrichment")
    if isinstance(official_section, dict):
        official_section["discovery_batch_size"] = len(target_cards)
    for adapter in bca_adapters:
        for settings in (
            getattr(adapter, "config", None),
            getattr(getattr(adapter, "http", None), "config", None),
        ):
            if isinstance(settings, dict):
                settings["max_requests_per_run"] = max(12 * len(target_cards), 1)
                settings["delay_seconds"] = 0.5
                settings["retry_attempts"] = 2
                settings["timeout_seconds"] = min(float(settings.get("timeout_seconds", 20)), 15.0)
                settings["trust_env"] = False

    refreshed = enrich_discovery_cards(
        target_cards,
        config,
        observed_at=observed_at,
        adapters=bca_adapters,
    )
    refreshed_by_id = {_property_id(card): card for card in refreshed.cards}
    final_cards = [refreshed_by_id.get(_property_id(card), card) for card in replayed_cards]

    refreshed_records = {
        str(record.get("property_id")): record
        for run in refreshed.source_runs
        for record in run.records
        if record.get("property_id")
    }
    bca_records.update(refreshed_records)
    base_run = bca_runs[0]
    all_bca_success = all(
        str(record.get("status") or "UNKNOWN") == "SUCCESS"
        for record in bca_records.values()
    )
    combined_bca_run = base_run.model_copy(
        update={
            "status": SourceRunStatus.SUCCESS if all_bca_success else SourceRunStatus.PARTIAL,
            "finished_at": datetime.now(timezone.utc),
            "records": list(bca_records.values()),
            "snapshots": [
                snapshot
                for run in bca_runs
                for snapshot in run.snapshots
            ] + [
                snapshot
                for run in refreshed.source_runs
                for snapshot in run.snapshots
            ],
            "errors": [
                error
                for run in refreshed.source_runs
                for error in run.errors
            ],
            "warnings": [
                warning
                for run in refreshed.source_runs
                for warning in run.warnings
            ],
        },
        deep=True,
    )
    source_runs = [
        run for run in cached_official.source_runs
        if run.source_name != "bca-assessment"
    ] + [combined_bca_run]
    status_counts, field_coverage, successful_properties = _aggregate_official_runs(source_runs)
    evidence = [
        item
        for item in cached_official.evidence
        if item.get("adapter") != "bca-assessment"
    ] + list(refreshed.evidence)
    overall_status = (
        "COMPLETED"
        if source_runs and all(run.status is SourceRunStatus.SUCCESS for run in source_runs)
        else "PARTIAL"
    )
    return cached_official.model_copy(
        update={
            "cards": final_cards,
            "source_runs": source_runs,
            "evidence": evidence,
            "status": overall_status,
            "attempted_card_count": len(final_cards),
            "enriched_card_count": successful_properties,
            "status_counts": status_counts,
            "field_coverage": field_coverage,
            "warnings": list(cached_official.warnings) + list(refreshed.warnings),
        },
        deep=True,
    )


def _render_report(result: DiscoveryRunResult, *, school_cache_path: str, official_mode: str) -> str:
    official = result.official_enrichment
    lines = [
        "# Vancouver Regional Official Enrichment and Ranking Report",
        "",
        f"- Observed at: `{result.observed_at}`",
        "- Input: saved REALTOR.ca detail artifact; no new listing-detail crawl",
        f"- Official enrichment mode: {official_mode}",
        "- Official sources: BCA, municipal zoning, municipal road, VSB catchment, BC FSA",
        f"- School boundary cache: `{school_cache_path}`",
        "- Ranking axes: Current Use, Dual Value (self-use + rebuild), Redevelopment, Price Value",
        "",
        "## Official enrichment result",
        "",
    ]
    if official is None:
        lines.append("- Status: `NOT_RUN`")
    else:
        lines.extend([
            f"- Status: `{official.status}`",
            f"- Cards attempted: `{official.attempted_card_count}`",
            f"- Cards with at least one successful adapter: `{official.enriched_card_count}`",
            f"- Adapter/status counts: `{official.status_counts}`",
            f"- Field coverage: `{official.field_coverage}`",
        ])
        if official.warnings:
            lines.append(f"- Warnings: `{official.warnings}`")
    lines.extend([
        "",
        "Official lookup failures remain explicit UNKNOWN/FAILED evidence. They lower only the review confidence label; missing data does not reduce value, investment, Dual Value, or ranking scores.",
        "",
        "## Requested leaderboards",
        "",
        "- `CURRENT_USE_TOP10`: 当前作为家庭住宅使用的相对排序",
        "- `DUAL_VALUE_TOP10`: Current Use 与 Redevelopment 两个轴同时达到门槛后的综合排序，代表自住5–10年后重建的综合型候选",
        "- `REDEVELOPMENT_TOP10`: 土地、合法开发能力和产品转换潜力的独立排序",
        "- `PRICE_VALUE_TOP10`: 以当前区域价格信号、BCA折价和自住必要Reno后的综合性价比排序",
        "",
        render_discovery_report(result),
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-official",
        action="store_true",
        help="Ignore the local official artifact and query configured official adapters again.",
    )
    parser.add_argument(
        "--refresh-zoning-only",
        action="store_true",
        help="Replay cached BCA/school/road evidence and refresh only municipal zoning.",
    )
    parser.add_argument(
        "--refresh-bca-only",
        action="store_true",
        help="Replay cached official evidence and refresh only non-successful BCA rows.",
    )
    parser.add_argument(
        "--refresh-road-only",
        action="store_true",
        help="Replay cached official evidence and refresh all municipal-road observations only.",
    )
    parser.add_argument(
        "--official-artifact",
        type=Path,
        help="Use a specific local official-enrichment artifact instead of the newest one.",
    )
    args = parser.parse_args()
    detail_artifact = _latest_artifact("regional-detail-enrichment-*.json", DETAIL_ARTIFACT)
    official_artifact = args.official_artifact or _latest_artifact(
        "regional-official-enrichment-*.json",
        OFFICIAL_ARTIFACT,
    )
    if not official_artifact.is_absolute():
        official_artifact = ROOT / official_artifact
    original = DiscoveryRunResult.model_validate(json.loads(detail_artifact.read_text(encoding="utf-8")))
    cards = [
        card
        for item in original.preset_results
        for card in item.candidates
    ]
    config = regional_official_config(load_config(ROOT / "config/research-discovery.yaml"))
    observed_at = datetime.now(timezone.utc)
    official_mode = "REMOTE_REFRESH"
    if args.refresh_zoning_only:
        if not official_artifact.exists():
            raise RuntimeError(f"cached official artifact not found: {official_artifact}")
        cached_result = DiscoveryRunResult.model_validate(json.loads(official_artifact.read_text(encoding="utf-8")))
        official = _refresh_zoning_from_cache(cards, cached_result, config, observed_at)
        official_mode = "LOCAL_CACHE_REPLAY_PLUS_ZONING_REFRESH"
        print(
            f"official_enrichment_zoning_refresh cards={len(official.cards)} "
            f"status={official.status}",
            flush=True,
        )
    elif args.refresh_bca_only:
        if not official_artifact.exists():
            raise RuntimeError(f"cached official artifact not found: {official_artifact}")
        cached_result = DiscoveryRunResult.model_validate(json.loads(official_artifact.read_text(encoding="utf-8")))
        official = _refresh_bca_from_cache(cards, cached_result, config, observed_at)
        official_mode = "LOCAL_CACHE_REPLAY_PLUS_BCA_FAILED_REFRESH"
        print(
            f"official_enrichment_bca_refresh cards={len(official.cards)} "
            f"status={official.status}",
            flush=True,
        )
    elif args.refresh_road_only:
        if not official_artifact.exists():
            raise RuntimeError(f"cached official artifact not found: {official_artifact}")
        cached_result = DiscoveryRunResult.model_validate(json.loads(official_artifact.read_text(encoding="utf-8")))
        official = _refresh_road_from_cache(cards, cached_result, config, observed_at)
        official_mode = "LOCAL_CACHE_REPLAY_PLUS_ROAD_REFRESH"
        print(
            f"official_enrichment_road_refresh cards={len(official.cards)} "
            f"status={official.status}",
            flush=True,
        )
    elif official_artifact.exists() and not args.refresh_official:
        cached_result = DiscoveryRunResult.model_validate(json.loads(official_artifact.read_text(encoding="utf-8")))
        replayed_cards = replay_cached_official_cards(cards, cached_result.candidates)
        if cached_result.official_enrichment is None:
            raise RuntimeError("Local official artifact has no official_enrichment payload")
        official = cached_result.official_enrichment.model_copy(
            update={"cards": replayed_cards},
            deep=True,
        )
        official_mode = "LOCAL_CACHE_REPLAY"
        print(
            f"official_enrichment_cache_replay cards={len(replayed_cards)} "
            f"cached_status={official.status}",
            flush=True,
        )
    else:
        print(f"official_enrichment_start cards={len(cards)} batch_size={config['official_enrichment']['discovery_batch_size']}", flush=True)
        official = enrich_discovery_cards(cards, config, observed_at=observed_at)
        print(
            f"official_enrichment_finished status={official.status} "
            f"attempted={official.attempted_card_count} enriched={official.enriched_card_count}",
            flush=True,
        )
    enriched = {_card_key(card): card for card in official.cards}
    updated_presets = _cards_by_preset(original, enriched)
    screening = screen_discovery_candidates(official.cards, config)
    result = original.model_copy(
        update={
            "status": "COMPLETED" if official.status == "COMPLETED" else "PARTIAL",
            "observed_at": observed_at,
            "candidates": official.cards,
            "candidate_count": len(official.cards),
            "preset_results": updated_presets,
            "official_enrichment": official,
            "screening": screening,
        },
        deep=True,
    )
    persist_discovery_result(result, config)

    date = observed_at.date().isoformat()
    normalized = ROOT / "data/research/normalized" / f"regional-official-enrichment-{date}.json"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    cache_files = sorted((ROOT / "data/research/normalized/school-boundaries").glob("school-catchment-*.json"))
    cache_path = str(cache_files[-1].resolve()) if cache_files else "UNKNOWN"
    report = ROOT / "reports/discovery" / f"regional-official-enrichment-{date}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_render_report(result, school_cache_path=cache_path, official_mode=official_mode), encoding="utf-8")
    latest = report.parent / "regional-official-enrichment-latest.md"
    shutil.copyfile(report, latest)
    summary = {
        "status": result.status,
        "normalized": str(normalized),
        "report": str(report),
        "latest": str(latest),
        "official_status": official.status,
        "official_mode": official_mode,
        "official_attempted": official.attempted_card_count,
        "official_enriched": official.enriched_card_count,
        "official_status_counts": official.status_counts,
        "official_field_coverage": official.field_coverage,
        "current_use_top10": len(screening.current_use_top10),
        "dual_value_top10": len(screening.dual_value_top10),
        "redevelopment_top10": len(screening.redevelopment_top10),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if result.status in {"COMPLETED", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
