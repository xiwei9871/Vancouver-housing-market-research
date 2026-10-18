from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import typer

from .collectors import COLLECTOR_REGISTRY
from .acquisition import validate_acquisition_manifest
from .config import load_config, resolve_path
from .enrichers.assessment_enricher import enrich_assessment, load_assessment_rows
from .enrichers.detail_enricher import enrich_details
from .history import HistoryListing, RadarHistory
from .models import Listing, ScoredListing
from .monitoring import (
    FreshnessResult,
    flatten_selected_files,
    input_fingerprint,
    inspect_input_freshness,
    selected_paths,
    write_stale_alert,
    write_status,
)
from .normalize import listing_dedupe_key
from .notifiers.feishu_webhook_notifier import build_feishu_card_payload, build_feishu_text_payload, FeishuWebhookNotifier
from .notifiers.notification_router import send_with_fallback
from .notifiers.openclaw_notifier import OpenClawNotifier, build_openclaw_payload
from .report import build_markdown_report, summarize
from .rentals.collectors import RentalSnapshotCollector
from .rentals.models import RentalListing
from .rentals.report import build_rental_markdown_report, summarize_rentals
from .rentals.scoring import score_rental_listing
from .rentals.storage import RentalStorage, rental_dedupe_key
from .research.validation import run_phase1_validation
from .research.pipeline import run_research_pipeline
from .research.discovery_pipeline import run_discovery
from .research.discovery_report import persist_discovery_result, write_discovery_report
from .research.location_valuation import run_location_research
from .research.bca_arcgis_sales import (
    enrich_bca_sales_details,
    fetch_bca_arcgis_sales,
    merge_bca_sales_spatial_snapshot,
)
from .research.location_sold_comps import run_sold_comps_research
from .research.vancouver_official_location import fetch_vancouver_public_streets
from .research.vancouver_official_amenities import fetch_vancouver_parks
from .research.vancouver_official_zoning import fetch_vancouver_zoning_polygons
from .research.scheduler import run_weekly_scheduled
from .research.storage import ResearchStorage
from .scoring import score_listing
from .storage import Storage
from .utils.dates import now_iso

app = typer.Typer(help="North Shore detached freehold property watchdog.")

DEFAULT_RESEARCH_OPERATIONAL_CONFIG = "config/research-mls-first.yaml"


@app.command("validate-acquisition")
def validate_acquisition(
    manifest: str = "outputs/acquisition/latest.json",
    purchase_config: str = "config/real.yaml",
    rental_config: str = "config/rentals.yaml",
    status_path: str | None = None,
    min_observed_at: str | None = None,
) -> None:
    """Validate the weekly web-search hand-off before report generation."""

    purchase_cfg = load_config(purchase_config)
    rental_cfg = load_config(rental_config)
    minimum_observed_at: datetime | None = None
    if min_observed_at:
        try:
            minimum_observed_at = datetime.fromisoformat(min_observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter("min_observed_at must be ISO-8601") from exc
        if minimum_observed_at.tzinfo is None or minimum_observed_at.utcoffset() is None:
            minimum_observed_at = minimum_observed_at.astimezone()
    result = validate_acquisition_manifest(
        resolve_path(manifest, purchase_cfg),
        purchase_cfg,
        rental_cfg,
        min_observed_at=minimum_observed_at,
    )
    payload = result.to_dict()
    if status_path:
        target = resolve_path(status_path, purchase_cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if result.status != "fresh":
        raise typer.Exit(code=1)


@app.command("research-validate-phase1")
def research_validate_phase1(config: str = "config/research.yaml") -> None:
    """Validate the local Phase 1 purchase-research cases only."""

    cfg = load_config(config)
    result = run_phase1_validation(cfg)
    output = dict(result.summary)
    output.update({
        "run_type": "phase1_validation",
        "report_path": str(result.report_path) if result.report_path else None,
        "weekly_path": str(result.weekly_path) if result.weekly_path else None,
        "latest_path": str(result.latest_path) if result.latest_path else None,
    })
    typer.echo("run_type=phase1_validation")
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


@app.command("research-discover")
def research_discover(
    config: str = "config/research-discovery.yaml",
    preset: str | None = typer.Option(None, "--preset"),
    source: str | None = typer.Option(None, "--source"),
    capture: str | None = typer.Option(None, "--capture"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    official_enrich: bool | None = typer.Option(
        None,
        "--official-enrich/--no-official-enrich",
        help="Run official read-only enrichment before screening; live native discovery defaults to enabled.",
    ),
    observed_at: str | None = typer.Option(
        None,
        "--observed-at",
        help="ISO-8601 observation time; naive values use UTC.",
    ),
) -> None:
    """Discover only cards returned by a configured filtered search."""

    cfg = load_config(config)
    result = run_discovery(
        cfg,
        preset_name=preset,
        source_name=source,
        capture_path=capture,
        dry_run=dry_run,
        observed_at=_research_observed_at(observed_at),
        official_enrich=official_enrich,
    )
    output = result.model_dump(mode="json")
    if not dry_run:
        output["source_run_ids"] = persist_discovery_result(result, cfg)
        report_path, latest_path = write_discovery_report(result, cfg)
        output["report_path"] = str(report_path)
        output["latest_path"] = str(latest_path)
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _research_sources_arg(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _research_observed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter("observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _echo_research_result(result) -> None:
    output = dict(result.summary)
    output.update({
        "run_type": result.run_type,
        "status": result.status,
        "dry_run": result.dry_run,
        "run_id": result.run_id,
        "report_path": str(result.report_path) if result.report_path else None,
        "latest_path": str(result.latest_path) if result.latest_path else None,
    })
    typer.echo(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("research-acquire")
def research_acquire(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    dry_run: bool = False,
    sources: str | None = None,
    from_crawl: list[str] | None = typer.Option(None, "--from-crawl"),
) -> None:
    """Run read-only Phase 2 acquisition and Stage 1 scan."""

    result = run_research_pipeline(
        load_config(config),
        stages={"acquire"},
        source_names=_research_sources_arg(sources),
        dry_run=dry_run,
        crawl_manifest_paths=from_crawl,
    )
    _echo_research_result(result)


@app.command("research-crawl")
def research_crawl(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    source: str | None = typer.Option(None, "--source"),
    sources: list[str] | None = typer.Option(None, "--sources"),
    run_id: str | None = typer.Option(None, "--run-id"),
    max_search_pages: int | None = typer.Option(None, "--max-search-pages"),
    max_detail_requests: int | None = typer.Option(None, "--max-detail-requests"),
    dry_run: bool = False,
) -> None:
    """Run one or more bounded crawls and print JSON summaries."""

    from .research.crawling.runner import run_crawl

    cfg = load_config(config)
    effective_run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%SZ")
    selected_sources: list[str] = []
    for value in [*(sources or []), source or ""]:
        selected_sources.extend(
            item.strip()
            for item in value.split(",")
            if item.strip()
        )
    selected_sources = list(dict.fromkeys(selected_sources))
    if not selected_sources:
        raise typer.BadParameter("provide --sources or --source")
    overrides = {
        key: value
        for key, value in {
            "max_search_pages": max_search_pages,
            "max_detail_requests": max_detail_requests,
        }.items()
        if value is not None
    }
    if dry_run:
        typer.echo(json.dumps({
            "dry_run": True,
            "run_id": effective_run_id,
            "sources": selected_sources,
            "overrides": overrides,
        }, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return

    payloads: list[dict[str, Any]] = []
    project_root = Path(cfg.get("_project_root", Path.cwd()))
    for selected_source in selected_sources:
        summary = run_crawl(
            cfg,
            selected_source,
            effective_run_id,
            project_root,
            overrides=overrides,
        )
        if hasattr(summary, "model_dump"):
            item = summary.model_dump(mode="json")
        else:
            item = {key: value for key, value in vars(summary).items()}
        item["paths"] = {
            key: item.pop(key)
            for key in ("run_dir", "job_dir", "artifact_dir", "manifest_path", "summary_path")
            if key in item
        }
        payloads.append(item)
    payload: dict[str, Any]
    if len(payloads) == 1:
        payload = payloads[0]
    else:
        payload = {
            "run_id": effective_run_id,
            "status": "SUCCESS" if all(item.get("status") == "SUCCESS" for item in payloads) else "PARTIAL",
            "coverage_complete": all(item.get("coverage_complete") is True for item in payloads),
            "runs": payloads,
        }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("research-enrich")
def research_enrich(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    dry_run: bool = False,
    sources: str | None = None,
    providers: str | None = None,
) -> None:
    """Run Phase 2 official/local enrichment against current properties."""

    result = run_research_pipeline(
        load_config(config),
        stages={"enrich"},
        provider_names=_research_sources_arg(providers or sources),
        dry_run=dry_run,
    )
    _echo_research_result(result)


@app.command("research-comps")
def research_comps(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    dry_run: bool = False,
) -> None:
    """Run the dynamic sold-comparable engine."""

    result = run_research_pipeline(load_config(config), stages={"comps"}, dry_run=dry_run)
    _echo_research_result(result)


@app.command("research-location")
def research_location(
    input_path: str = typer.Option(
        "data/research/normalized/regional-official-enrichment-2026-09-05.json",
        "--input-path",
        help="Local regional enrichment snapshot.",
    ),
    history_path: str | None = typer.Option(
        "data/research/sources/phase2_history.json",
        "--history-path",
        help="Local listing-history snapshot.",
    ),
    comparables_path: str | None = typer.Option(
        "data/research/sources/phase2_comparables.json",
        "--comparables-path",
        help="Local sold-comparable snapshot.",
    ),
    school_path: str | None = typer.Option(
        "data/research/sources/phase2_schools.json",
        "--school-path",
        help="Local school snapshot.",
    ),
    output_dir: str = typer.Option("research/location", "--output-dir"),
    observed_at: str | None = typer.Option(None, "--observed-at"),
) -> None:
    """Build the read-only location valuation research artifacts."""

    result = run_location_research(
        input_path=input_path,
        history_path=history_path,
        comparables_path=comparables_path,
        school_path=school_path,
        output_dir=output_dir,
        observed_at=_research_observed_at(observed_at),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("research-location-sold-comps")
def research_location_sold_comps(
    source_path: list[str] | None = typer.Option(
        None,
        "--source-path",
        help="Local sold-comps CSV, JSON, or JSONL snapshot; repeat for multiple sources.",
    ),
    output_dir: str = typer.Option("research/location", "--output-dir"),
    property_history_path: list[str] | None = typer.Option(
        None,
        "--property-history-path",
        help="Local regional enrichment JSON containing official BCA previous-sale arrays; repeatable.",
    ),
    start_date: str | None = typer.Option(None, "--start-date", help="Inclusive ISO date."),
    end_date: str | None = typer.Option(None, "--end-date", help="Inclusive ISO date."),
    boundary_path: str | None = typer.Option(
        "data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson",
        "--boundary-path",
        help="Saved official City of Vancouver local-area boundary snapshot; use empty/None to disable.",
    ),
    school_boundary_path: str | None = typer.Option(
        "data/research/normalized/school-boundaries/school-catchment-53a59061080d5749.json",
        "--school-boundary-path",
        help="Saved official VSB catchment polygon snapshot.",
    ),
    road_path: str | None = typer.Option(
        "data/research/raw/location/vancouver-public-streets-2026-09-06.json",
        "--road-path",
        help="Cached official City of Vancouver public-streets snapshot.",
    ),
    park_path: str | None = typer.Option(
        "data/research/raw/location/vancouver-parks-2026-09-06.geojson",
        "--park-path",
        help="Cached official City of Vancouver parks point snapshot.",
    ),
    zoning_path: str | None = typer.Option(
        "data/research/raw/location/vancouver-zoning-2026-09-06.json",
        "--zoning-path",
        help="Cached official City of Vancouver zoning polygon snapshot.",
    ),
    artifact_version: str = typer.Option(
        "v1.2",
        "--artifact-version",
        help="Version label for this research artifact set.",
    ),
) -> None:
    """Build versioned sold-comps/location research artifacts only."""

    def parse_date(value: str | None, option: str):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise typer.BadParameter(f"{option} must be YYYY-MM-DD") from exc

    result = run_sold_comps_research(
        source_paths=source_path or ["data/research/sources/phase2_comparables.json"],
        output_dir=output_dir,
        start_date=parse_date(start_date, "start_date"),
        end_date=parse_date(end_date, "end_date"),
        property_history_paths=property_history_path or [
            "data/research/normalized/regional-official-enrichment-2026-09-05.json"
        ],
        boundary_path=boundary_path,
        school_boundary_path=school_boundary_path,
        road_path=road_path,
        zoning_path=zoning_path,
        park_path=park_path,
        artifact_version=artifact_version,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("acquire-vancouver-zoning")
def acquire_vancouver_zoning(
    output_path: str = typer.Option(
        "data/research/raw/location/vancouver-zoning-2026-09-06.json",
        "--output-path",
        help="Versioned local official zoning polygon snapshot.",
    ),
) -> None:
    """Fetch the current City of Vancouver zoning polygons once for local joins."""

    result = fetch_vancouver_zoning_polygons(output_path=output_path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("acquire-bca-location-sales")
def acquire_bca_location_sales(
    output_path: str | None = typer.Option(None, "--output-path", help="Raw versioned JSON snapshot path."),
    start_date: str | None = typer.Option(None, "--start-date", help="Inclusive ISO date; defaults to three years before end date."),
    end_date: str | None = typer.Option(None, "--end-date", help="Inclusive ISO date; defaults to today."),
    min_sale_price: int = typer.Option(1_000_000, "--min-sale-price"),
    max_sale_price: int = typer.Option(3_500_000, "--max-sale-price"),
    min_longitude: float = typer.Option(-123.27, "--min-longitude"),
    min_latitude: float = typer.Option(49.20, "--min-latitude"),
    max_longitude: float = typer.Option(-123.02, "--max-longitude"),
    max_latitude: float = typer.Option(49.30, "--max-latitude"),
) -> None:
    """Acquire a bounded, read-only BC Assessment recent-sales snapshot."""

    def parse_date(value: str | None, option: str) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise typer.BadParameter(f"{option} must be YYYY-MM-DD") from exc

    end = parse_date(end_date, "end_date") or date.today()
    start = parse_date(start_date, "start_date")
    if start is None:
        try:
            start = end.replace(year=end.year - 3)
        except ValueError:
            start = end.replace(year=end.year - 3, day=28)
    target = output_path or f"data/research/raw/location/bca-arcgis-sales-{end.isoformat()}.json"
    result = fetch_bca_arcgis_sales(
        output_path=target,
        start_date=start,
        end_date=end,
        min_sale_price=min_sale_price,
        max_sale_price=max_sale_price,
        bbox=(min_longitude, min_latitude, max_longitude, max_latitude),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("enrich-bca-location-sales")
def enrich_bca_location_sales(
    source_path: str = typer.Option(..., "--source-path", help="Raw BCA ArcGIS sales snapshot."),
    output_path: str = typer.Option(..., "--output-path", help="Versioned detail-enriched snapshot path."),
    max_records: int | None = typer.Option(None, "--max-records", help="Optional bounded detail request count."),
    min_sale_price: int | None = typer.Option(None, "--min-sale-price", help="Only request details for sales at or above this price."),
    max_sale_price: int | None = typer.Option(None, "--max-sale-price", help="Only request details for sales at or below this price."),
    delay_seconds: float = typer.Option(0.05, "--delay-seconds", min=0),
) -> None:
    """Enrich BCA sales rows through the public property-information endpoint."""

    result = enrich_bca_sales_details(
        source_path=source_path,
        output_path=output_path,
        max_records=max_records,
        min_sale_price=min_sale_price,
        max_sale_price=max_sale_price,
        delay_seconds=delay_seconds,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("merge-bca-location-sales-spatial")
def merge_bca_location_sales_spatial(
    detail_path: str = typer.Option(..., "--detail-path", help="Completed BCA detail-enriched snapshot."),
    spatial_path: str = typer.Option(..., "--spatial-path", help="New BCA ArcGIS snapshot with parcel geometry."),
    output_path: str = typer.Option(..., "--output-path", help="Merged versioned snapshot path."),
) -> None:
    """Merge a local spatial refresh without repeating detail requests."""

    result = merge_bca_sales_spatial_snapshot(
        detail_path=detail_path,
        spatial_path=spatial_path,
        output_path=output_path,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("acquire-vancouver-public-streets")
def acquire_vancouver_public_streets(
    output_path: str = typer.Option(..., "--output-path", help="Local official street snapshot path."),
) -> None:
    """Cache the official City of Vancouver public-streets dataset."""

    result = fetch_vancouver_public_streets(output_path=output_path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("acquire-vancouver-parks")
def acquire_vancouver_parks(
    output_path: str = typer.Option(..., "--output-path", help="Local official parks snapshot path."),
) -> None:
    """Cache the official City of Vancouver parks point dataset."""

    result = fetch_vancouver_parks(output_path=output_path)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


@app.command("research-redevelop")
def research_redevelop(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    dry_run: bool = False,
) -> None:
    """Run official-gated redevelopment scenarios and product economics."""

    result = run_research_pipeline(load_config(config), stages={"redevelop"}, dry_run=dry_run)
    _echo_research_result(result)


@app.command("research-run-weekly")
def research_run_weekly(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
    dry_run: bool = False,
    sources: str | None = None,
    observed_at: str | None = typer.Option(
        None,
        "--observed-at",
        help="ISO-8601 observation time for deterministic replay; naive values use UTC.",
    ),
) -> None:
    """Run the bounded Phase 2–5 weekly job under a local lock."""

    result = run_weekly_scheduled(
        load_config(config),
        observed_at=_research_observed_at(observed_at),
        dry_run=dry_run,
        source_names=_research_sources_arg(sources),
    )
    _echo_research_result(result)


@app.command("research-conflicts")
def research_conflicts(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
) -> None:
    """Print the current unresolved data-conflict queue as JSON."""

    cfg = load_config(config)
    storage_path = resolve_path(cfg.get("storage", {}).get("sqlite_path", "outputs/research_watchdog.sqlite"), cfg)
    with ResearchStorage(storage_path) as storage:
        conflicts = storage.list_open_conflicts()
    typer.echo(json.dumps([item.model_dump(mode="json") for item in conflicts], ensure_ascii=False, indent=2, sort_keys=True))


@app.command("research-verifications")
def research_verifications(
    config: str = DEFAULT_RESEARCH_OPERATIONAL_CONFIG,
) -> None:
    """Print the append-only official and manual verification queue as JSON."""

    cfg = load_config(config)
    storage_path = resolve_path(cfg.get("storage", {}).get("sqlite_path", "outputs/research_watchdog.sqlite"), cfg)
    with ResearchStorage(storage_path) as storage:
        items = storage.list_actionable_verification_items()
    typer.echo(json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False, indent=2, sort_keys=True))


def _storage(config: dict) -> Storage:
    return Storage(resolve_path(config.get("storage", {}).get("sqlite_path", "outputs/watchdog.sqlite"), config))


def _rental_storage(config: dict) -> RentalStorage:
    return RentalStorage(resolve_path(config.get("storage", {}).get("sqlite_path", "outputs/rental_watchdog.sqlite"), config))


def _collect_rentals(config: dict):
    rentals_cfg = config.get("collectors", {}).get("snapshot", {})
    if not rentals_cfg.get("enabled", True):
        return []
    collector_config = dict(rentals_cfg)
    collector_config.setdefault("allowed_domains", config.get("allowed_domains", ["realtor.ca", "zolo.ca"]))
    return _merge_rental_listings(RentalSnapshotCollector(collector_config).collect())


def _merge_rental_listings(listings: list[RentalListing]) -> list[RentalListing]:
    """Collapse the same rental found in overlapping recent snapshots.

    Freshness intentionally keeps every recent input file so a weekly run can
    compare sources and retain provenance. That means the same URL can appear
    in several snapshots; history rows still need one record per identity.
    """

    grouped: dict[str, RentalListing] = {}
    for listing in listings:
        key = rental_dedupe_key(listing)
        current = grouped.get(key)
        if current is None:
            grouped[key] = listing
            continue
        grouped[key] = _merge_two_rentals(current, listing)
    return list(grouped.values())


def _merge_two_rentals(left: RentalListing, right: RentalListing) -> RentalListing:
    left_score = _rental_completeness(left)
    right_score = _rental_completeness(right)
    # Collector paths are ordered oldest-to-newest, so the right-hand record
    # wins ties and supplies the current price/status when both are complete.
    base, other = (left, right) if left_score > right_score else (right, left)
    update: dict[str, Any] = {}
    for field in RentalListing.model_fields:
        if field == "raw_data":
            continue
        if getattr(base, field) in (None, "", False, []) and getattr(other, field) not in (None, "", False, []):
            update[field] = getattr(other, field)
    raw_data = dict(other.raw_data)
    raw_data.update(base.raw_data)
    snapshot_paths = {
        str(item.raw_data.get("snapshot_path"))
        for item in (left, right)
        if item.raw_data.get("snapshot_path")
    }
    if snapshot_paths:
        raw_data["merged_snapshot_paths"] = sorted(snapshot_paths)
    update["raw_data"] = raw_data
    return base.model_copy(update=update)


def _rental_completeness(listing: RentalListing) -> int:
    fields = [
        "neighbourhood",
        "property_type",
        "monthly_rent",
        "bedrooms",
        "bathrooms",
        "size_sqft",
        "furnished",
        "parking",
        "pets_allowed",
        "available_date",
        "transit_minutes",
        "amenities",
        "description",
    ]
    return sum(1 for field in fields if getattr(listing, field) not in (None, "", False, []))


def _collect_listings(config: dict) -> list[Listing]:
    listings: list[Listing] = []
    for name, collector_config in config.get("collectors", {}).items():
        if not collector_config.get("enabled", False):
            continue
        collector_cls = COLLECTOR_REGISTRY.get(name)
        if not collector_cls:
            continue
        listings.extend(collector_cls(collector_config).collect())
    return _merge_listings(listings)


def _merge_listings(listings: list[Listing]) -> list[Listing]:
    grouped: dict[str, Listing] = {}
    for listing in listings:
        key = listing_dedupe_key(listing)
        current = grouped.get(key)
        if current is None:
            grouped[key] = listing
            continue
        grouped[key] = _merge_two(current, listing)
    return list(grouped.values())


def _merge_two(left: Listing, right: Listing) -> Listing:
    base, other = (left, right) if _completeness(left) >= _completeness(right) else (right, left)
    update = {}
    for field in Listing.model_fields:
        if field == "raw_data":
            continue
        if getattr(base, field) in (None, "", False) and getattr(other, field) not in (None, "", False):
            update[field] = getattr(other, field)
    raw_data = dict(other.raw_data)
    raw_data.update(base.raw_data)
    raw_data.setdefault("merged_sources", sorted({left.source, right.source}))
    update["raw_data"] = raw_data
    return base.model_copy(update=update)


def _completeness(listing: Listing) -> int:
    fields = [
        "property_type",
        "tenure",
        "listing_status",
        "list_price",
        "bedrooms",
        "bathrooms",
        "house_size_sqft",
        "lot_size_sqft",
        "frontage_ft",
        "year_built",
        "assessment_total",
    ]
    return sum(1 for field in fields if getattr(listing, field) not in (None, ""))


def _enrich_listings(listings: list[Listing], config: dict) -> list[Listing]:
    rows = load_assessment_rows(config.get("assessment", {}).get("input_path", "data/samples/assessment_sample.csv"))
    enriched: list[Listing] = []
    for listing in listings:
        item = enrich_details(listing)
        if rows:
            item = enrich_assessment(item, rows)
        enriched.append(item)
    return enriched


def _score_listings(listings: list[Listing], config: dict) -> list[ScoredListing]:
    return [score_listing(listing, config) for listing in listings]


def _local_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now().astimezone()
    return current.astimezone() if current.tzinfo else current.astimezone()


def _status_output_path(config: dict, radar: str, status_path: str | None) -> Path:
    return resolve_path(status_path or f"outputs/status/{radar}_last_status.json", config)


def _config_with_selected_files(config: dict, freshness: FreshnessResult | None) -> dict:
    if freshness is None:
        return config
    prepared = dict(config)
    prepared_collectors: dict[str, dict[str, Any]] = {}
    for name, collector_config in config.get("collectors", {}).items():
        current = dict(collector_config)
        if name in freshness.selected_files:
            current["selected_files"] = [item.path for item in freshness.selected_files[name]]
        prepared_collectors[name] = current
    prepared["collectors"] = prepared_collectors
    return prepared


def _freshness_payload(freshness: FreshnessResult | None) -> dict | None:
    if freshness is None:
        return None
    payload = freshness.to_dict()
    payload["source_files"] = [item.to_dict() for item in flatten_selected_files(freshness)]
    payload["input_fingerprint"] = input_fingerprint(freshness)
    payload["selected_paths"] = selected_paths(freshness)
    return payload


def _status_payload(
    *,
    radar: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    freshness: FreshnessResult | None = None,
    delta: Any | None = None,
    report_path: Path | None = None,
    alert_path: Path | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    freshness_data = _freshness_payload(freshness)
    delta_data = delta.to_dict() if hasattr(delta, "to_dict") else delta
    return {
        "radar": radar,
        "status": status,
        "data_status": status,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "freshness": freshness_data,
        "newest": freshness_data.get("newest") if freshness_data else None,
        "source_files": freshness_data.get("source_files", []) if freshness_data else [],
        "selected_paths": freshness_data.get("selected_paths", {}) if freshness_data else {},
        "input_fingerprint": freshness_data.get("input_fingerprint", "") if freshness_data else "",
        "stale_directories": freshness_data.get("stale_directories", []) if freshness_data else [],
        "warnings": freshness_data.get("warnings", []) if freshness_data else [],
        "reasons": freshness_data.get("reasons", []) if freshness_data else [],
        "delta": delta_data,
        "report_path": str(report_path) if report_path else None,
        "alert_path": str(alert_path) if alert_path else None,
        "error": error,
    }


def _purchase_history_entries(scored: list[ScoredListing]) -> list[HistoryListing]:
    return [
        HistoryListing(
            identity_key=listing_dedupe_key(item.listing),
            address=item.listing.address,
            city=item.listing.city,
            price=item.listing.list_price,
            grade=item.grade,
            score_total=item.score_total,
            payload=item.model_dump(mode="json"),
        )
        for item in scored
    ]


def _rental_history_entries(scored) -> list[HistoryListing]:
    return [
        HistoryListing(
            identity_key=rental_dedupe_key(item.listing),
            address=item.listing.address,
            city=item.listing.city,
            price=item.listing.monthly_rent,
            grade=item.grade,
            score_total=item.score_total,
            payload=item.model_dump(mode="json"),
        )
        for item in scored
    ]


@app.command()
def collect(config: str = "config/default.yaml") -> None:
    cfg = load_config(config)
    storage = _storage(cfg)
    listings = _collect_listings(cfg)
    for listing in listings:
        storage.upsert_listing(listing)
    typer.echo(f"Collected {len(listings)} listings")


@app.command()
def enrich(config: str = "config/default.yaml") -> None:
    cfg = load_config(config)
    storage = _storage(cfg)
    listings = _enrich_listings(storage.list_listings(), cfg)
    for listing in listings:
        storage.upsert_listing(listing)
    typer.echo(f"Enriched {len(listings)} listings")


@app.command()
def score(config: str = "config/default.yaml") -> None:
    cfg = load_config(config)
    storage = _storage(cfg)
    scored = _score_listings(storage.list_listings(), cfg)
    for item in scored:
        storage.upsert_scored(item)
    typer.echo(f"Scored {len(scored)} listings")


@app.command()
def report(
    config: str = "config/default.yaml",
    format: Literal["markdown"] = "markdown",
    output: str = "outputs/weekly_report.md",
) -> None:
    cfg = load_config(config)
    storage = _storage(cfg)
    scored = storage.list_scored()
    if not scored:
        listings = _enrich_listings(storage.list_listings() or _collect_listings(cfg), cfg)
        scored = _score_listings(listings, cfg)
    markdown = build_markdown_report(scored, cfg)
    output_path = resolve_path(output, cfg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    typer.echo(str(output_path))


@app.command()
def notify(
    config: str = "config/default.yaml",
    channel: Literal["feishu", "openclaw"] = "feishu",
    report: str = "outputs/weekly_report.md",
) -> None:
    cfg = load_config(config)
    report_path = resolve_path(report, cfg)
    markdown = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    storage = _storage(cfg)
    scored = storage.list_scored()
    summary = summarize(scored)
    top = _top_candidates(scored)
    if channel == "feishu":
        payload = build_feishu_card_payload("North Shore Property Watchdog - Weekly Report", top, str(report_path))
        ok = FeishuWebhookNotifier(cfg.get("notifiers", {}).get("feishu", {})).send(payload)
        _write_payload_sample("outputs/feishu_payload_sample.json", payload, cfg)
        typer.echo("sent" if ok else "failed")
        return
    openclaw_cfg = cfg.get("notifiers", {}).get("openclaw", {})
    payload = build_openclaw_payload(
        route=openclaw_cfg.get("route", "property_watchdog"),
        action=openclaw_cfg.get("action", "send_weekly_report"),
        summary=summary,
        top_candidates=[_candidate_dict(item) for item in top],
        review_candidates=[_candidate_dict(item) for item in _review_candidates(scored)],
        markdown_report=markdown,
        html_report_path=None,
        generated_at=summary["generated_at"],
    )
    ok = OpenClawNotifier(openclaw_cfg).send(payload)
    _write_payload_sample("outputs/openclaw_payload_sample.json", payload, cfg)
    typer.echo("sent" if ok else "failed")


@app.command("run")
def run(config: str = "config/default.yaml") -> None:
    run_weekly(config)


@app.command("run-weekly")
def run_weekly(config: str = "config/default.yaml", status_path: str | None = None) -> None:
    _run_weekly(config, status_path=status_path)


def _run_weekly(
    config: str = "config/default.yaml",
    status_path: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    started_at = _local_now(now)
    cfg = load_config(config)
    report_path = resolve_path(cfg.get("report", {}).get("weekly_output", "outputs/weekly_report.md"), cfg)
    status_output = _status_output_path(cfg, "purchase", status_path)
    freshness: FreshnessResult | None = None
    storage: Storage | None = None
    history: RadarHistory | None = None
    try:
        if "monitoring" in cfg:
            freshness = inspect_input_freshness(cfg, now=started_at)
            if freshness.status != "fresh":
                alert_dir = resolve_path(
                    cfg.get("monitoring", {}).get("alert_dir", "outputs/alerts"),
                    cfg,
                )
                alert_path = write_stale_alert(alert_dir, "purchase", freshness)
                finished_at = _local_now(now)
                status = _status_payload(
                    radar="purchase",
                    status="stale",
                    started_at=started_at,
                    finished_at=finished_at,
                    freshness=freshness,
                    alert_path=alert_path,
                    report_path=report_path,
                )
                write_status(status_output, status)
                typer.echo(f"Stale purchase inputs; alert={alert_path}")
                return status

        prepared_cfg = _config_with_selected_files(cfg, freshness)
        storage = _storage(cfg)
        listings = _enrich_listings(_collect_listings(prepared_cfg), prepared_cfg)
        for listing in listings:
            storage.upsert_listing(listing)
        scored = _score_listings(listings, prepared_cfg)
        for item in scored:
            storage.upsert_scored(item)

        delta = None
        entries: list[HistoryListing] = []
        freshness_data = _freshness_payload(freshness)
        if freshness is not None:
            history = RadarHistory(storage.path, radar="purchase")
            entries = _purchase_history_entries(scored)
            delta = history.compare(entries)
        markdown = build_markdown_report(
            scored,
            prepared_cfg,
            data_status=freshness_data,
            delta=delta,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown, encoding="utf-8")

        finished_at = _local_now(now)
        if history is not None:
            history.record_success(
                started_at=started_at,
                finished_at=finished_at,
                source_files=freshness_data["source_files"] if freshness_data else [],
                input_fingerprint=freshness_data["input_fingerprint"] if freshness_data else "",
                entries=entries,
                delta=delta,
            )

        top = _top_candidates(scored)
        feishu_payload = build_feishu_card_payload("North Shore Property Watchdog - Weekly Report", top, str(report_path))
        openclaw_cfg = cfg.get("notifiers", {}).get("openclaw", {})
        openclaw_payload = build_openclaw_payload(
            route=openclaw_cfg.get("route", "property_watchdog"),
            action=openclaw_cfg.get("action", "send_weekly_report"),
            summary=summarize(scored, delta=delta),
            top_candidates=[_candidate_dict(item) for item in top],
            review_candidates=[_candidate_dict(item) for item in _review_candidates(scored)],
            markdown_report=markdown,
            html_report_path=None,
            generated_at=finished_at.isoformat(timespec="seconds"),
        )
        _write_payload_sample("outputs/feishu_payload_sample.json", feishu_payload, cfg)
        _write_payload_sample("outputs/openclaw_payload_sample.json", openclaw_payload, cfg)
        status = _status_payload(
            radar="purchase",
            status="fresh" if freshness is not None else "fresh",
            started_at=started_at,
            finished_at=finished_at,
            freshness=freshness,
            delta=delta,
            report_path=report_path,
        )
        write_status(status_output, status)
        if cfg.get("run", {}).get("send_notifications", False):
            notification_status = send_with_fallback(cfg, openclaw_payload, feishu_payload)
            typer.echo(f"Generated {report_path}; notification={notification_status}")
        else:
            typer.echo(f"Generated {report_path}; notifications disabled")
        return status
    except Exception as exc:
        finished_at = _local_now(now)
        error_status = _status_payload(
            radar="purchase",
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            freshness=freshness,
            report_path=report_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        write_status(status_output, error_status)
        raise
    finally:
        if history is not None:
            history.close()
        if storage is not None:
            storage.close()


@app.command("run-daily")
def run_daily(config: str = "config/default.yaml") -> None:
    cfg = load_config(config)
    storage = _storage(cfg)
    listings = _enrich_listings(_collect_listings(cfg), cfg)
    scored = _score_listings(listings, cfg)
    urgent_limit = int(cfg.get("alert_thresholds", {}).get("daily_urgent_limit", 5))
    urgent = [item for item in scored if item.alert_level == "urgent"][:urgent_limit]
    for listing in listings:
        storage.upsert_listing(listing)
    for item in scored:
        storage.upsert_scored(item)
    digest = build_markdown_report(urgent or scored, cfg)
    path = resolve_path(cfg.get("report", {}).get("daily_output", "outputs/daily_digest.md"), cfg)
    path.write_text(digest, encoding="utf-8")
    typer.echo(str(path))


@app.command("test-notify")
def test_notify(channel: Literal["feishu", "openclaw"] = "feishu", config: str = "config/default.yaml") -> None:
    cfg = load_config(config)
    if channel == "feishu":
        payload = build_feishu_text_payload("North Shore Property Watchdog test message")
        ok = FeishuWebhookNotifier(cfg.get("notifiers", {}).get("feishu", {})).send(payload)
    else:
        payload = build_openclaw_payload("property_watchdog", "test", {"ok": True}, [], "test", None, now_iso())
        ok = OpenClawNotifier(cfg.get("notifiers", {}).get("openclaw", {})).send(payload)
    typer.echo("sent" if ok else "failed")


@app.command("rentals-collect")
def rentals_collect(config: str = "config/rentals.yaml") -> None:
    cfg = load_config(config)
    storage = _rental_storage(cfg)
    listings = _collect_rentals(cfg)
    for listing in listings:
        storage.upsert_listing(listing)
    typer.echo(f"Collected {len(listings)} rental listings")


@app.command("rentals-score")
def rentals_score(config: str = "config/rentals.yaml") -> None:
    cfg = load_config(config)
    storage = _rental_storage(cfg)
    scored = [score_rental_listing(listing, cfg) for listing in storage.list_listings()]
    for item in scored:
        storage.upsert_scored(item)
    typer.echo(f"Scored {len(scored)} rental listings")


@app.command("rentals-report")
def rentals_report(config: str = "config/rentals.yaml", output: str | None = None) -> None:
    cfg = load_config(config)
    storage = _rental_storage(cfg)
    scored = storage.list_scored()
    if not scored:
        scored = [score_rental_listing(listing, cfg) for listing in storage.list_listings() or _collect_rentals(cfg)]
    markdown = build_rental_markdown_report(scored, cfg)
    output_path = resolve_path(output or cfg.get("report", {}).get("weekly_output", "outputs/rental_weekly_report.zh.md"), cfg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    typer.echo(str(output_path))


@app.command("rentals-run-weekly")
def rentals_run_weekly(config: str = "config/rentals.yaml", status_path: str | None = None) -> None:
    _run_rentals_weekly(config, status_path=status_path)


def _run_rentals_weekly(
    config: str = "config/rentals.yaml",
    status_path: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    started_at = _local_now(now)
    cfg = load_config(config)
    report_path = resolve_path(cfg.get("report", {}).get("weekly_output", "outputs/rental_weekly_report.zh.md"), cfg)
    status_output = _status_output_path(cfg, "rental", status_path)
    freshness: FreshnessResult | None = None
    storage: RentalStorage | None = None
    history: RadarHistory | None = None
    try:
        if "monitoring" in cfg:
            freshness = inspect_input_freshness(cfg, now=started_at)
            if freshness.status != "fresh":
                alert_dir = resolve_path(
                    cfg.get("monitoring", {}).get("alert_dir", "outputs/alerts"),
                    cfg,
                )
                alert_path = write_stale_alert(alert_dir, "rental", freshness)
                finished_at = _local_now(now)
                status = _status_payload(
                    radar="rental",
                    status="stale",
                    started_at=started_at,
                    finished_at=finished_at,
                    freshness=freshness,
                    alert_path=alert_path,
                    report_path=report_path,
                )
                write_status(status_output, status)
                typer.echo(f"Stale rental inputs; alert={alert_path}")
                return status

        prepared_cfg = _config_with_selected_files(cfg, freshness)
        storage = _rental_storage(cfg)
        listings = _collect_rentals(prepared_cfg)
        for listing in listings:
            storage.upsert_listing(listing)
        scored = [score_rental_listing(listing, prepared_cfg) for listing in listings]
        for item in scored:
            storage.upsert_scored(item)

        delta = None
        entries: list[HistoryListing] = []
        freshness_data = _freshness_payload(freshness)
        if freshness is not None:
            history = RadarHistory(storage.path, radar="rental")
            entries = _rental_history_entries(scored)
            delta = history.compare(entries)
        markdown = build_rental_markdown_report(
            scored,
            prepared_cfg,
            data_status=freshness_data,
            delta=delta,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown, encoding="utf-8")

        finished_at = _local_now(now)
        if history is not None:
            history.record_success(
                started_at=started_at,
                finished_at=finished_at,
                source_files=freshness_data["source_files"] if freshness_data else [],
                input_fingerprint=freshness_data["input_fingerprint"] if freshness_data else "",
                entries=entries,
                delta=delta,
            )

        _write_payload_sample(
            "outputs/rental_openclaw_payload_sample.json",
            {
                "route": cfg.get("notifiers", {}).get("openclaw", {}).get("route", "rental_watchdog"),
                "action": cfg.get("notifiers", {}).get("openclaw", {}).get("action", "send_weekly_report"),
                "payload": {
                    "summary": summarize_rentals(scored, delta=delta),
                    "top_candidates": [_rental_candidate_dict(item) for item in scored if item.grade == "A"],
                    "markdown_report": markdown,
                    "report_path": str(report_path),
                    "generated_at": finished_at.isoformat(timespec="seconds"),
                },
            },
            cfg,
        )
        status = _status_payload(
            radar="rental",
            status="fresh",
            started_at=started_at,
            finished_at=finished_at,
            freshness=freshness,
            delta=delta,
            report_path=report_path,
        )
        write_status(status_output, status)
        typer.echo(f"Generated {report_path}")
        return status
    except Exception as exc:
        finished_at = _local_now(now)
        error_status = _status_payload(
            radar="rental",
            status="error",
            started_at=started_at,
            finished_at=finished_at,
            freshness=freshness,
            report_path=report_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        write_status(status_output, error_status)
        raise
    finally:
        if history is not None:
            history.close()
        if storage is not None:
            storage.close()


def _rental_candidate_dict(item) -> dict:
    listing = item.listing
    return {
        "address": listing.address,
        "city": listing.city,
        "neighbourhood": listing.neighbourhood,
        "monthly_rent": listing.monthly_rent,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "score": item.score_total,
        "grade": item.grade,
        "url": listing.url,
        "reasons": item.reasons[:3],
        "risks": (item.risks + item.exclusion_reasons)[:3],
    }


def _candidate_dict(item: ScoredListing) -> dict:
    return {
        "id": listing_dedupe_key(item.listing),
        "mls_number": item.listing.mls_number,
        "address": item.listing.address,
        "city": item.listing.city,
        "neighbourhood": item.listing.neighbourhood,
        "price": item.listing.list_price,
        "score": item.score_total,
        "grade": item.grade,
        "url": item.listing.url,
        "reasons": item.reasons[:3],
        "risks": (item.risks + item.exclusion_reasons)[:3],
    }


def _top_candidates(scored: list[ScoredListing], limit: int = 5) -> list[ScoredListing]:
    return sorted(
        [item for item in scored if item.grade == "A" and not item.excluded],
        key=lambda item: (-item.score_total, item.listing.address),
    )[:limit]


def _review_candidates(scored: list[ScoredListing], limit: int = 10) -> list[ScoredListing]:
    return sorted(
        [item for item in scored if item.grade == "Review" and not item.excluded],
        key=lambda item: (-item.score_total, item.listing.address),
    )[:limit]


def _write_payload_sample(path: str, payload: dict, config: dict) -> None:
    output = resolve_path(path, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    app()
