from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..config import resolve_path
from .acquisition import (
    AcquisitionResult,
    Stage1ScanResult,
    acquire_from_sources,
    order_source_adapters,
    scan_stage1,
)
from .comparables import ComparableResult, evaluate_comparables
from .delta import source_run_status_event
from .enrichment import EnrichedPropertyResult, JsonMappingProvider, enrich_property
from .models import CandidateStatus, PropertyObservation, PropertyRecord, RedevelopmentScenario, ScoreSnapshot, SourceType, VerificationItem, WatchlistEvent
from .provenance import (
    DataConflict,
    SourceRecord,
    SourceRegistryEntry,
    load_source_registry_entries,
)
from .redevelopment import RedevelopmentAnalysis, analyze_redevelopment
from .report import build_weekly_report, summarize_research_run, write_latest_report, write_weekly_report
from .scoring import score_property
from .state import resolve_candidate_state
from .storage import ResearchStorage
from .sources import (
    AuthorizedJsonSource,
    LegacyCollectorSource,
    LocalCsvSource,
    LocalJsonSource,
    SourceAdapter,
    SourceRunResult,
    UnsupportedSource,
)
from .crawl_source import CrawlManifestSource
from .public_feeds import PublicFeedSource
from .browser_feeds import PlaywrightPublicSource
from .official_enrichment import (
    BCAssessmentAdapter,
    MunicipalRoadAdapter,
    MunicipalZoningAdapter,
    SchoolCatchmentAdapter,
    SoldHistoryAdapter,
)
from .validation import _build_evidence
from .normalization import normalize_address


ALL_STAGES = ("acquire", "enrich", "comps", "redevelop", "score", "report")


class ResearchRunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: int | str | None = None
    run_type: str
    status: str
    observed_at: datetime
    dry_run: bool = False
    stage_status: dict[str, str] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    records: list[PropertyRecord] = Field(default_factory=list)
    stage1: Stage1ScanResult | None = None
    source_runs: list[SourceRunResult] = Field(default_factory=list)
    enrichments: dict[str, EnrichedPropertyResult] = Field(default_factory=dict)
    comparable_results: dict[str, ComparableResult] = Field(default_factory=dict)
    redevelopment_results: dict[str, RedevelopmentAnalysis] = Field(default_factory=dict)
    snapshots: dict[str, ScoreSnapshot] = Field(default_factory=dict)
    resolutions: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, list[SourceRecord]] = Field(default_factory=dict)
    conflicts: list[DataConflict] = Field(default_factory=list)
    events: list[WatchlistEvent] = Field(default_factory=list)
    verification_items: list[VerificationItem] = Field(default_factory=list)
    report_path: Path | None = None
    latest_path: Path | None = None
    parse_errors: list[str] = Field(default_factory=list)
    primary_source_status: str = "NOT_RUN"
    primary_source_names: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    source_priority: list[str] = Field(default_factory=list)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _source_type(value: Any) -> SourceType:
    try:
        return value if isinstance(value, SourceType) else SourceType(str(value))
    except ValueError:
        return SourceType.MANUAL


def _enabled(config: dict[str, Any], section: dict[str, Any]) -> bool:
    return bool(section.get("enabled", True)) and bool(config.get("source_policy", {}).get("remote_default_enabled", False) or section.get("type", "").upper() not in {"AUTHORIZED_JSON", "REMOTE_JSON"})


def _register_source(storage: ResearchStorage, definition: dict[str, Any]) -> None:
    source_type = _source_type(definition.get("source_type"))
    registry_id = str(definition.get("source_registry_id") or f"source:{definition.get('name', 'unnamed')}")
    storage.register_source(
        SourceRegistryEntry(
            source_registry_id=registry_id,
            name=str(definition.get("name") or registry_id),
            source_type=source_type,
            source_role=definition.get("source_role", "UNSPECIFIED"),
            priority=definition.get("priority"),
            domain=definition.get("domain"),
            access_method=definition.get("access_method") or str(definition.get("type", "unknown")),
            authorization_status=str(definition.get("authorization_status", "NOT_APPLICABLE")),
            evidence_status=definition.get("evidence_status", "UNCONFIRMED"),
            reliability_notes=definition.get("reliability_notes"),
            enabled=bool(definition.get("enabled", True)),
        )
    )


def _load_source_registry(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry.model_dump(mode="python")
        for entry in load_source_registry_entries(config).values()
    ]


def _trusted_definition(config: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    """Apply registry metadata as the authoritative source envelope.

    Paths, adapter kinds, and other execution details live in the runtime
    config.  Source identity, type, authorization, and enabled status live in
    the registry and must not be downgraded or changed by a row payload (or by
    a conflicting runtime copy of the same metadata).
    """

    trusted = dict(definition)
    registry_id = str(
        definition.get("source_registry_id")
        or f"source:{definition.get('name', 'unnamed-source')}"
    )
    registry = next(
        (
            item
            for item in _load_source_registry(config)
            if str(item.get("source_registry_id") or "") == registry_id
        ),
        None,
    )
    if registry is None:
        return trusted
    for field_name in (
        "source_registry_id",
        "source_type",
        "authorization_status",
        "evidence_status",
        "enabled",
        "source_url",
        "domain",
            "access_method",
            "reliability_notes",
            "source_role",
            "priority",
        ):
        if field_name in registry:
            trusted[field_name] = registry[field_name]
    return trusted


def _source_registry_entries(config: dict[str, Any]) -> dict[str, SourceRegistryEntry]:
    return load_source_registry_entries(config)


def build_source_adapters(
    config: dict[str, Any],
    *,
    source_names: set[str] | None = None,
) -> list[SourceAdapter]:
    definitions = [
        _trusted_definition(config, dict(item))
        for item in config.get("acquisition", {}).get("sources", [])
        if isinstance(item, dict)
    ]
    adapters: list[SourceAdapter] = []
    for definition in definitions:
        name = str(definition.get("name") or "unnamed-source")
        if source_names and name not in source_names:
            continue
        kind = str(definition.get("type", "LOCAL_JSON")).upper()
        source_type = _source_type(definition.get("source_type"))
        common = {
            "name": name,
            "source_type": source_type,
            "source_registry_id": definition.get("source_registry_id"),
            "source_url": definition.get("source_url"),
            "config": definition,
        }
        if kind == "LOCAL_JSON":
            adapters.append(LocalJsonSource(path=resolve_path(definition["path"], config), **common))
        elif kind == "LOCAL_CSV":
            adapters.append(LocalCsvSource(path=resolve_path(definition["path"], config), **common))
        elif kind in {"AUTHORIZED_JSON", "REMOTE_JSON"}:
            transport = config.get("_authorized_transports", {}).get(name)
            adapters.append(AuthorizedJsonSource(transport=transport, **common))
        elif kind == "PUBLIC_HTTP":
            transport = config.get("_public_transports", {}).get(name)
            robots_transport = config.get("_public_robots_transports", {}).get(name)
            raw_snapshot_dir = definition.get("raw_snapshot_dir", "data/research/raw/public-feeds")
            adapters.append(
                PublicFeedSource(
                    feed_requests=definition.get("feed_requests", []),
                    raw_snapshot_dir=resolve_path(raw_snapshot_dir, config),
                    transport=transport,
                    robots_transport=robots_transport,
                    **common,
                )
            )
        elif kind == "PLAYWRIGHT_PUBLIC":
            browser_transports = config.get("_playwright_transports", {})
            browser_robots_transports = config.get("_playwright_robots_transports", {})
            if not isinstance(browser_transports, dict):
                browser_transports = {}
            if not isinstance(browser_robots_transports, dict):
                browser_robots_transports = {}
            raw_snapshot_dir = definition.get("raw_snapshot_dir", "data/research/raw/browser-feeds")
            adapters.append(
                PlaywrightPublicSource(
                    feed_requests=definition.get("feed_requests", []),
                    raw_snapshot_dir=resolve_path(raw_snapshot_dir, config),
                    browser_transport=browser_transports.get(name),
                    robots_transport=browser_robots_transports.get(name),
                    **common,
                )
            )
        elif kind == "LEGACY_COLLECTOR":
            collector = definition.get("collector")
            if collector is not None:
                adapters.append(LegacyCollectorSource(collector=collector, **common))
            else:
                adapters.append(UnsupportedSource(kind=kind, **common))
        else:
            adapters.append(UnsupportedSource(kind=kind, **common))
    return order_source_adapters(adapters, config)


def _crawl_manifest_source_definition(
    config: dict[str, Any], manifest_path: str | Path
) -> dict[str, Any]:
    """Map a crawl manifest to its configured crawl-source definition."""

    source_name: str | None = None
    source_registry_id: str | None = None
    try:
        for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                source_name = payload.get("source_name")
                source_registry_id = payload.get("source_registry_id")
            break
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        pass

    crawl_sources = config.get("acquisition", {}).get("crawl_sources", [])
    for item in crawl_sources:
        if not isinstance(item, dict):
            continue
        if item.get("name") == source_name or item.get("source_registry_id") == source_registry_id:
            return _trusted_definition(config, dict(item))
    raise ValueError(
        f"crawl manifest is not mapped to config acquisition.crawl_sources: {manifest_path}"
    )


def _build_crawl_manifest_adapters(
    config: dict[str, Any],
    manifest_paths: Iterable[str | Path],
    *,
    source_names: set[str] | None = None,
) -> tuple[list[SourceAdapter], list[dict[str, Any]]]:
    adapters: list[SourceAdapter] = []
    definitions: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        definition = _crawl_manifest_source_definition(config, manifest_path)
        name = str(definition.get("name"))
        if source_names and name not in source_names:
            continue
        definitions.append(definition)
        adapters.append(
            CrawlManifestSource(
                manifest_path=manifest_path,
                name=name,
                source_type=_source_type(definition.get("source_type")),
                source_registry_id=definition.get("source_registry_id"),
                source_url=definition.get("source_url"),
                config=definition,
            )
        )
    return order_source_adapters(adapters, config), definitions


def build_enrichment_providers(
    config: dict[str, Any],
    *,
    provider_names: set[str] | None = None,
) -> list[JsonMappingProvider]:
    providers: list[JsonMappingProvider] = []
    definitions = [
        _trusted_definition(config, dict(item))
        for item in config.get("enrichment", {}).get("providers", [])
        if isinstance(item, dict)
    ]
    for definition in definitions:
        name = str(definition.get("name") or "unnamed-provider")
        if provider_names and name not in provider_names:
            continue
        path = definition.get("path")
        providers.append(
            JsonMappingProvider(
                name=name,
                path=resolve_path(path, config) if path else None,
                data=definition.get("data"),
                source_type=_source_type(definition.get("source_type")),
                enabled=bool(definition.get("enabled", True)),
                source_url=definition.get("source_url"),
                source_registry_id=definition.get("source_registry_id"),
                config=definition,
            )
        )
    return providers


def build_official_enrichment_adapters(
    config: dict[str, Any],
    *,
    source_names: set[str] | None = None,
) -> list[Any]:
    """Build bounded official adapters from the Phase 2 source catalogue."""

    section = config.get("official_enrichment", {})
    if not isinstance(section, dict) or not bool(section.get("enabled", False)):
        return []
    transports = config.get("_official_transports", {})
    if not isinstance(transports, dict):
        transports = {}
    adapters: list[Any] = []
    for raw_definition in section.get("sources", []):
        if not isinstance(raw_definition, dict):
            continue
        definition = _trusted_definition(config, dict(raw_definition))
        name = str(definition.get("name") or "unnamed-official-source")
        if source_names and name not in source_names:
            continue
        if not bool(definition.get("enabled", True)):
            continue
        adapter_kind = str(definition.get("adapter") or "").lower()
        source_url = str(definition.get("source_url") or "")
        raw_snapshot_dir = definition.get(
            "raw_snapshot_dir",
            section.get("raw_snapshot_dir", "data/research/raw/official-enrichment"),
        )
        common = {
            "name": name,
            "source_registry_id": str(definition.get("source_registry_id") or f"source:{name}"),
            "source_url": source_url,
            "raw_snapshot_dir": resolve_path(raw_snapshot_dir, config),
            "config": definition,
            "transport": transports.get(name) or transports.get(str(definition.get("source_registry_id") or "")),
        }
        if adapter_kind in {"bca", "bca_assessment", "assessment"}:
            adapters.append(BCAssessmentAdapter(**common))
        elif adapter_kind in {"municipal_road", "road", "roads"}:
            adapters.append(MunicipalRoadAdapter(**common))
        elif adapter_kind in {"municipal_zoning", "zoning"}:
            adapters.append(MunicipalZoningAdapter(**common))
        elif adapter_kind in {"school_catchment", "school", "catchment"}:
            adapters.append(SchoolCatchmentAdapter(**common))
        elif adapter_kind in {"school_performance", "fsa", "school_fsa"}:
            from .official_enrichment import SchoolPerformanceAdapter

            adapters.append(SchoolPerformanceAdapter(**common))
        elif adapter_kind in {"sold_history", "sold", "sales"}:
            adapters.append(SoldHistoryAdapter(**common))
    return adapters


def _load_json_rows(path: str | Path, config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        source_path = resolve_path(path, config)
        with source_path.open("r", encoding="utf-8") as handle:
            if source_path.suffix.lower() in {".jsonl", ".ndjson"}:
                payload = [json.loads(line) for line in handle if line.strip()]
            else:
                payload = json.load(handle)
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)], []
        if isinstance(payload, dict):
            value = payload.get("records") or payload.get("items") or payload.get("data") or payload
            return (
                [dict(item) for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else [dict(value)]
            ), []
    except (OSError, ValueError, TypeError) as exc:
        return [], [f"comparable source {path}: {type(exc).__name__}: {exc}"]
    return [], [f"comparable source {path}: unsupported payload"]


def _load_comparable_rows(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Load sold rows while attaching the source that actually supplied each row."""

    comparable_config = config.get("comparables", {})
    source_definitions = comparable_config.get("sources") or []
    if source_definitions:
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for definition in source_definitions:
            if not isinstance(definition, dict) or not definition.get("path"):
                errors.append("comparable source definition missing path")
                continue
            source_rows, source_errors = _load_json_rows(definition["path"], config)
            errors.extend(source_errors)
            source_registry_id = definition.get("source_registry_id")
            source_type = definition.get("source_type", SourceType.MLS.value)
            source_version = definition.get("source_version")
            source_url = definition.get("source_url")
            for row in source_rows:
                item = dict(row)
                # Registry/config metadata is the source envelope.  A row can
                # carry a property-specific URL, but cannot relabel its feed.
                if source_registry_id:
                    item["source_registry_id"] = str(source_registry_id)
                item["source_type"] = str(source_type)
                if source_version:
                    item["source_version"] = str(source_version)
                if source_url and not item.get("source_url"):
                    item["source_url"] = str(source_url)
                rows.append(item)
        return rows, errors

    path = comparable_config.get("source_path")
    if not path:
        return [], []
    return _load_json_rows(path, config)


def _normalise_stages(stages: set[str] | None) -> set[str]:
    if stages is None:
        return set(ALL_STAGES)
    aliases = {
        "acquisition": "acquire",
        "enrichment": "enrich",
        "comparables": "comps",
        "comparable": "comps",
        "redevelopment": "redevelop",
        "scoring": "score",
    }
    return {aliases.get(str(item).lower(), str(item).lower()) for item in stages}


def _record_with_snapshot(record: PropertyRecord, snapshot: ScoreSnapshot, resolution: Any) -> PropertyRecord:
    return record.model_copy(update={
        "total_score": snapshot.total_score,
        "total_score_label": snapshot.total_score_label,
        "live_score": snapshot.live_score,
        "land_score": snapshot.land_score,
        "investment_score": snapshot.investment_score,
        "land_to_product_score": snapshot.land_to_product_score,
        "data_confidence_score": snapshot.data_confidence_score,
        "data_confidence_band": snapshot.data_confidence_band,
        "candidate_status": resolution.state,
        "opportunity_tags": list(dict.fromkeys([*record.opportunity_tags, *snapshot.opportunity_tags])),
        "last_updated": snapshot.generated_at,
    })


def _evidence_for(
    record: PropertyRecord,
    provenance: Iterable[SourceRecord],
    *,
    source_registry: dict[str, SourceRegistryEntry] | None = None,
) -> list[Any]:
    payload = record.model_dump(mode="python", by_alias=True)
    return _build_evidence(payload, list(provenance), source_registry=source_registry)


def _dedupe_conflicts(conflicts: Iterable[DataConflict]) -> list[DataConflict]:
    return list({item.conflict_id: item for item in conflicts}.values())


def _conflict_value_key(value: Any) -> Any:
    if hasattr(value, "value"):
        return _conflict_value_key(value.value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), _conflict_value_key(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_conflict_value_key(item) for item in value)
    return value


def _resolve_equivalent_conflicts(
    storage: ResearchStorage,
    conflicts: Iterable[DataConflict],
    *,
    weekly_run_id: int | None,
) -> list[DataConflict]:
    """Close stale conflicts whose latest source values normalize identically."""

    unresolved: list[DataConflict] = []
    for conflict in conflicts:
        values = [value for value in conflict.source_values.values() if value not in (None, "")]
        if len(values) > 1 and len({_conflict_value_key(value) for value in values}) == 1:
            storage.save_conflict(
                conflict.model_copy(
                    update={
                        "resolution_status": "RESOLVED",
                        "resolution_note": "Latest normalized source values are equivalent.",
                    }
                ),
                weekly_run_id=weekly_run_id,
            )
        else:
            unresolved.append(conflict)
    return unresolved


def _link_provenance(
    records: Iterable[SourceRecord],
    *,
    weekly_run_id: int | None,
    source_run_ids: dict[str, int] | None = None,
) -> list[SourceRecord]:
    return [
        item.model_copy(update={
            "weekly_run_id": weekly_run_id,
            "source_run_id": (source_run_ids or {}).get(item.source_registry_id or ""),
        })
        for item in records
    ]


def _manual_provisional_verification_items(
    records: Iterable[PropertyRecord],
    provenance: Iterable[SourceRecord],
    *,
    observed_at: datetime,
    weekly_run_id: int | None,
) -> list[VerificationItem]:
    """Create an append-only queue for single-source listing observations."""

    listing_sources: dict[str, list[SourceRecord]] = {}
    for item in provenance:
        if item.field_name == "listing_snapshot":
            listing_sources.setdefault(item.property_id, []).append(item)

    items: list[VerificationItem] = []
    for record in records:
        if not record.manual_verification_required:
            continue
        if str(getattr(record, "range_classification", "UNKNOWN") or "UNKNOWN").upper() == "OUT_OF_RANGE":
            continue
        if str(getattr(record, "research_scope", "UNKNOWN") or "UNKNOWN").upper() in {
            "EXCLUDED_NON_TARGET",
            "EXCLUDED_STEEP_LOT",
        }:
            continue
        sources = listing_sources.get(record.property_id, [])
        is_single_source = record.data_verification_status == "PROVISIONAL_SINGLE_SOURCE"
        reason = (
            "Single-source listing accepted for provisional research; cross-check deferred."
            if is_single_source
            else "Multiple listing sources observed; cross-check pending."
        )
        for source in sources:
            items.append(
                VerificationItem(
                    verification_id=f"verification:{uuid4().hex}",
                    property_id=record.property_id,
                    source_registry_id=source.source_registry_id or "UNKNOWN",
                    source_url=source.source_url,
                    status="PENDING_MANUAL",
                    reason=reason,
                    observed_at=source.retrieved_at or _aware(observed_at),
                    weekly_run_id=weekly_run_id,
                    source_run_id=source.source_run_id,
                    details={
                        "verification_type": "MANUAL_PROVISIONAL_LISTING",
                        "data_verification_status": record.data_verification_status,
                        "reasons": record.manual_verification_reasons,
                        "source_count": len(sources),
                        "source_registry_ids": record.provisional_source_registry_ids,
                        "fields": [
                            "ownership_type",
                            "pid",
                            "legal_description",
                            "approved_use",
                            "zoning",
                            "assessment",
                            "listing_history",
                        ],
                    },
                )
            )
    return items


def _is_manual_provisional_verification(item: VerificationItem) -> bool:
    return str(item.details.get("verification_type", "")).startswith("MANUAL_PROVISIONAL_")


def _apply_open_manual_verification_gate(
    records: Iterable[PropertyRecord],
    items: Iterable[VerificationItem],
) -> list[PropertyRecord]:
    """Re-apply unresolved manual gates to refreshed current projections."""

    pending: dict[str, list[VerificationItem]] = {}
    for item in items:
        if _is_manual_provisional_verification(item) and item.status not in {"RESOLVED", "CLOSED"}:
            pending.setdefault(item.property_id, []).append(item)
    updated: list[PropertyRecord] = []
    for record in records:
        queue_items = pending.get(record.property_id)
        if not queue_items:
            updated.append(record)
            continue
        reasons = list(dict.fromkeys([
            *(record.manual_verification_reasons or []),
            "MANUAL_VERIFICATION_QUEUE_OPEN",
        ]))
        updates: dict[str, Any] = {
            "manual_verification_required": True,
            "manual_verification_reasons": reasons,
        }
        queue_statuses = {
            str(item.details.get("data_verification_status", "UNKNOWN"))
            for item in queue_items
        }
        if str(record.data_verification_status).upper() == "UNKNOWN":
            known_status = next(
                (status for status in queue_statuses if status != "UNKNOWN"),
                None,
            )
            if known_status:
                updates["data_verification_status"] = known_status
        updated.append(record.model_copy(update=updates))
    return updated


def _link_enrichment_result(
    result: EnrichedPropertyResult,
    *,
    weekly_run_id: int | None,
    source_run_ids: dict[str, int] | None = None,
) -> EnrichedPropertyResult:
    linked_provenance = _link_provenance(
        result.provenance,
        weekly_run_id=weekly_run_id,
        source_run_ids=source_run_ids,
    )
    linked_provider_results = [
        item.model_copy(update={
            "source_records": _link_provenance(
                item.source_records,
                weekly_run_id=weekly_run_id,
                source_run_ids=source_run_ids,
            )
        })
        for item in result.provider_results
    ]
    return result.model_copy(
        update={
            "provenance": linked_provenance,
            "provider_results": linked_provider_results,
        }
    )


def _mark_conflicted_records(
    records: Iterable[PropertyRecord],
    conflicts: Iterable[DataConflict],
) -> list[PropertyRecord]:
    conflicted_ids = {item.property_id for item in conflicts}
    return [
        record.model_copy(update={"data_conflict": True, "major_data_conflict": True})
        if record.property_id in conflicted_ids
        else record
        for record in records
    ]


def _stage2_property_ids(stage1: Stage1ScanResult | None, records: Iterable[PropertyRecord]) -> set[str]:
    """Return only Stage 1-passed records for deep research.

    Verification and special-situation queues remain in the result/history,
    but they cannot silently enter zoning, comparables, redevelopment, or
    ranking stages before their gate is cleared.
    """

    if stage1 is None:
        return {record.property_id for record in records}
    return {
        item.property_id
        for item in stage1.candidates
        if item.included
    }


def _official_query_property_ids(
    stage1: Stage1ScanResult | None,
    records: Iterable[PropertyRecord],
) -> set[str]:
    """Select in-scope records for official verification before downstream gates.

    A title-unknown record cannot pass the Stage 1 title gate yet, but official
    sources are precisely what can resolve that uncertainty.  This queue is
    therefore broader than the downstream Stage 2 set.  It still excludes
    out-of-range and hard-veto records; comparables, redevelopment, and scoring
    continue to use ``_stage2_property_ids`` only.
    """

    materialized = list(records)
    if stage1 is None:
        return {record.property_id for record in materialized}
    return {
        item.property_id
        for item in stage1.candidates
        if item.range_classification in {"CORE", "EXTENDED", "SPECIAL"}
        and item.gate_state in {"PASS", "NEED_VERIFICATION", "SPECIAL_SITUATION"}
    }


def _source_registry_id(definition: dict[str, Any]) -> str:
    return str(definition.get("source_registry_id") or f"source:{definition.get('name', 'unnamed-source')}")


def _coverage_gaps_for_run(
    config: Mapping[str, Any],
    *,
    crawl_manifest_supplied: bool,
) -> list[str]:
    """Return caveats that describe this run's actual input boundary."""

    gaps = [str(value) for value in config.get("coverage_gaps", [])]
    if crawl_manifest_supplied:
        gaps = [
            value
            for value in gaps
            if not value.startswith("Default Phase 2–5 inputs are local validation fixtures")
        ]
    return list(dict.fromkeys(gaps))


def _reconcile_absent_records(
    storage: ResearchStorage,
    current_records: Iterable[PropertyRecord],
    source_runs: Iterable[SourceRunResult],
    source_definitions: Iterable[dict[str, Any]],
    *,
    observed_at: datetime,
) -> list[PropertyRecord]:
    """Create conservative removed/unknown observations only for complete feeds."""

    current_materialized = list(current_records)
    current_ids = {record.property_id for record in current_materialized}
    current_by_address: dict[str, list[PropertyRecord]] = {}
    for record in current_materialized:
        current_by_address.setdefault(
            normalize_address(record.address, record.municipality),
            [],
        ).append(record)
    definitions = {
        _source_registry_id(definition): definition
        for definition in source_definitions
    }
    reconciled: dict[str, PropertyRecord] = {}
    for source_run in source_runs:
        if (
            source_run.status.value != "SUCCESS"
            or source_run.coverage_complete is not True
            or source_run.source_registry_id not in definitions
        ):
            continue
        for previous in storage.list_current_properties_for_source(source_run.source_registry_id):
            if previous.property_id in current_ids:
                continue
            same_address_records = current_by_address.get(
                normalize_address(previous.address, previous.municipality),
                [],
            )
            if same_address_records:
                previous_pids = set(previous.pids or ([previous.pid] if previous.pid else []))
                current_pids = [
                    set(record.pids or ([record.pid] if record.pid else []))
                    for record in same_address_records
                ]
                # Same-address identity migration is safe to suppress only when
                # the old record is pidless, the new observation is pidless, or
                # at least one PID overlaps.  Disjoint known PIDs must remain
                # visible so a title/address collision is not hidden as a
                # routine relist.
                if (
                    not previous_pids
                    or any(not pids for pids in current_pids)
                    or any(previous_pids.intersection(pids) for pids in current_pids)
                ):
                    continue
            reconciled[previous.property_id] = previous.model_copy(
                update={
                    "listing_status": "REMOVED_OR_UNKNOWN",
                    "last_updated": _aware(observed_at),
                }
            )
    return list(reconciled.values())


def _write_operational_report(
    config: dict[str, Any],
    observed_at: datetime,
    result: ResearchRunResult,
) -> tuple[Path, Path]:
    report = build_weekly_report(
        result.records,
        snapshots=result.snapshots,
        resolutions=result.resolutions,
        scenarios={key: value.scenarios for key, value in result.redevelopment_results.items()},
        provenance=result.provenance,
        events=result.events,
        conflicts=result.conflicts,
        coverage_gaps=result.summary.get(
            "coverage_gaps",
            config.get("coverage_gaps", []),
        ),
        stage1_candidates=result.stage1.included_count if result.stage1 else len(result.records),
        stage1_research_queue=result.stage1.research_candidate_count if result.stage1 else len(result.records),
        verification_queue_count=result.stage1.verification_queue_count if result.stage1 else None,
        special_situation_count=result.stage1.special_situation_count if result.stage1 else None,
        deep_research_count=sum(
            1
            for record in result.records
            if result.stage1 is None or record.property_id in _stage2_property_ids(result.stage1, result.records)
        ),
        generated_at=observed_at,
        run_type=result.run_type,
        source_runs=result.source_runs,
        enrichment_results=result.enrichments,
        verification_items=result.verification_items,
        stage1_included_property_ids=(
            {item.property_id for item in result.stage1.candidates if item.included}
            if result.stage1 else None
        ),
        stage_status=result.stage_status,
        comparable_results=result.comparable_results,
        redevelopment_results=result.redevelopment_results,
        delta_events=result.events,
        primary_source_status=result.primary_source_status,
        primary_source_names=result.primary_source_names,
        fallback_used=result.fallback_used,
        source_priority=result.source_priority,
        stage1_counts=result.summary.get("stage1_counts"),
    )
    weekly_dir = resolve_path(config.get("reports", {}).get("weekly_dir", "reports/weekly"), config)
    report_path = write_weekly_report(weekly_dir / f"{observed_at.date().isoformat()}.md", report)
    latest_path = write_latest_report(
        resolve_path(config.get("reports", {}).get("latest_path", "reports/latest.md"), config),
        report,
    )
    return report_path, latest_path


def run_research_pipeline(
    config: dict[str, Any],
    *,
    run_type: str = "weekly",
    observed_at: datetime | None = None,
    stages: set[str] | None = None,
    source_names: set[str] | None = None,
    provider_names: set[str] | None = None,
    dry_run: bool = False,
    crawl_manifest_paths: Iterable[str | Path] | None = None,
) -> ResearchRunResult:
    """Run bounded Phase 2–5 research with append-only persistence."""

    observed_at = _aware(observed_at or datetime.now(timezone.utc))
    requested = _normalise_stages(stages)
    stage_status = {stage: ("PENDING" if stage in requested else "SKIPPED") for stage in ALL_STAGES}
    db_path = resolve_path(config.get("storage", {}).get("sqlite_path", "outputs/research_watchdog.sqlite"), config)
    storage_path: str | Path = ":memory:" if dry_run else db_path
    source_runs: list[SourceRunResult] = []
    parse_errors: list[str] = []
    records: list[PropertyRecord] = []
    stage1: Stage1ScanResult | None = None
    provenance_by_property: dict[str, list[SourceRecord]] = {}
    enrichments: dict[str, EnrichedPropertyResult] = {}
    comparable_results: dict[str, ComparableResult] = {}
    redevelopment_results: dict[str, RedevelopmentAnalysis] = {}
    snapshots: dict[str, ScoreSnapshot] = {}
    resolutions: dict[str, Any] = {}
    conflicts: list[DataConflict] = []
    events: list[WatchlistEvent] = []
    verification_items: list[VerificationItem] = []
    source_run_ids: dict[str, int] = {}
    run_id: int | str | None = None
    report_path: Path | None = None
    latest_path: Path | None = None
    primary_source_status = "NOT_RUN"
    primary_source_names: list[str] = []
    fallback_used = False
    source_priority: list[str] = []
    status = "SUCCESS"
    open_manual_verification_items: list[VerificationItem] = []
    pending_manual_verification_ids: set[str] = set()
    manifest_paths = list(crawl_manifest_paths or [])
    reconciliation_source_definitions: list[dict[str, Any]] = [
        item
        for item in config.get("acquisition", {}).get("sources", [])
        if isinstance(item, dict)
    ]
    run_coverage_gaps = _coverage_gaps_for_run(
        config,
        crawl_manifest_supplied=bool(manifest_paths),
    )

    with ResearchStorage(storage_path) as storage:
        event_start_count = len(storage.list_watchlist_events()) if not dry_run else 0
        source_registry = _source_registry_entries(config)
        if not dry_run:
            run_id = storage.start_weekly_run(run_type, started_at=observed_at)
            conflicts.extend(storage.list_open_conflicts())
            open_manual_verification_items = [
                item
                for item in storage.list_open_verification_items()
                if _is_manual_provisional_verification(item)
            ]
            pending_manual_verification_ids = {
                item.property_id for item in open_manual_verification_items
            }
        try:
            for definition in _load_source_registry(config):
                _register_source(storage, definition)
            if "acquire" in requested:
                if manifest_paths:
                    adapters, definitions = _build_crawl_manifest_adapters(
                        config, manifest_paths, source_names=source_names
                    )
                    reconciliation_source_definitions = definitions
                    for definition in definitions:
                        _register_source(storage, definition)
                else:
                    adapters = build_source_adapters(config, source_names=source_names)
                    for definition in config.get("acquisition", {}).get("sources", []):
                        if source_names and definition.get("name") not in source_names:
                            continue
                        _register_source(storage, _trusted_definition(config, dict(definition)))
                acquisition: AcquisitionResult = acquire_from_sources(
                    adapters,
                    observed_at=observed_at,
                    config=config,
                    identity_aliases=storage.list_identity_aliases() if not dry_run else None,
                    identity_alias_pids=storage.list_identity_alias_pids() if not dry_run else None,
                    source_registry=source_registry,
                )
                source_runs = acquisition.source_runs
                primary_source_status = acquisition.primary_source_status
                primary_source_names = acquisition.primary_source_names
                fallback_used = acquisition.fallback_used
                source_priority = acquisition.source_priority
                if not dry_run and run_id is not None:
                    persisted_runs: list[SourceRunResult] = []
                    for source_run in source_runs:
                        linked = source_run.model_copy(update={"weekly_run_id": run_id})
                        source_run_id = storage.save_source_run(linked)
                        source_run_ids[source_run.source_registry_id] = source_run_id
                        persisted_runs.append(linked.model_copy(update={"source_run_id": source_run_id}))
                    source_runs = persisted_runs
                parse_errors.extend(acquisition.parse_errors)
                conflicts.extend(acquisition.conflicts)
                stage1 = scan_stage1(acquisition.records, config, observed_at=observed_at)
                conflicts.extend(stage1.conflicts)
                conflicts = _dedupe_conflicts(conflicts)
                records = list(stage1.records)
                if not dry_run:
                    for acquired_record in acquisition.records:
                        storage.register_identity_aliases(acquired_record, observed_at=observed_at)
                    records.extend(
                        _reconcile_absent_records(
                            storage,
                            records,
                            source_runs,
                            reconciliation_source_definitions,
                            observed_at=observed_at,
                        )
                    )
                acquisition_provenance = _link_provenance(
                    acquisition.provenance,
                    weekly_run_id=run_id,
                    source_run_ids=source_run_ids,
                )
                for source in acquisition_provenance:
                    provenance_by_property.setdefault(source.property_id, []).append(source)
                manual_items = _manual_provisional_verification_items(
                    records,
                    acquisition_provenance,
                    observed_at=observed_at,
                    weekly_run_id=run_id,
                )
                verification_items.extend(manual_items)
                if not dry_run:
                    for item in manual_items:
                        storage.save_verification_item(item)
                if not dry_run:
                    for source in acquisition_provenance:
                        if source.field_name != "listing_snapshot" or not isinstance(source.raw_value, dict):
                            continue
                        observed_mls = source.raw_value.get("mls_number")
                        if observed_mls in (None, ""):
                            continue
                        storage.record_identity_alias_observation(
                            alias_type="mls",
                            alias_value=str(observed_mls),
                            property_id=source.property_id,
                            observed_at=source.retrieved_at,
                            source_registry_id=source.source_registry_id,
                            source_run_id=source.source_run_id,
                            weekly_run_id=source.weekly_run_id,
                            source_url=source.source_url,
                        )
                    storage.save_provenance(acquisition_provenance)
                acquisition_partial = any(
                    item.status.value != "SUCCESS"
                    for item in source_runs
                ) or bool(acquisition.parse_errors)
                stage_status["acquire"] = "PARTIAL" if acquisition_partial else "COMPLETED"
                if acquisition_partial:
                    status = "PARTIAL"
            else:
                records = storage.list_current_properties()
                if records:
                    stage1 = scan_stage1(records, config, observed_at=observed_at)
                    conflicts.extend(stage1.conflicts)
                    conflicts = _dedupe_conflicts(conflicts)

            pending_manual_verification_ids.update(
                item.property_id
                for item in verification_items
                if _is_manual_provisional_verification(item)
            )
            records = _apply_open_manual_verification_gate(
                records,
                [*open_manual_verification_items, *verification_items],
            )
            if not dry_run:
                conflicts = _resolve_equivalent_conflicts(
                    storage,
                    conflicts,
                    weekly_run_id=run_id,
                )
            records = _mark_conflicted_records(records, conflicts)

            if "enrich" in requested:
                for definition in config.get("enrichment", {}).get("providers", []):
                    if provider_names and definition.get("name") not in provider_names:
                        continue
                    _register_source(storage, _trusted_definition(config, dict(definition)))
                official_definitions = config.get("official_enrichment", {}).get("sources", [])
                for definition in official_definitions:
                    if isinstance(definition, dict):
                        if provider_names and definition.get("name") not in provider_names:
                            continue
                        _register_source(storage, _trusted_definition(config, dict(definition)))
                official_section = config.get("official_enrichment", {})
                official_query_ids = _official_query_property_ids(stage1, records)
                stage2_records = [item for item in records if item.property_id in official_query_ids]
                if isinstance(official_section, dict) and bool(official_section.get("stage2_only", True)):
                    max_properties = int(official_section.get("max_properties_per_run", 30))
                    if max_properties > 0:
                        stage2_records = stage2_records[:max_properties]
                official_query_ids = {item.property_id for item in stage2_records}
                official_providers: list[JsonMappingProvider] = []
                official_status_by_property: dict[str, dict[str, str]] = {}
                official_conflicted_ids: set[str] = set()
                stage2_records_by_id = {item.property_id: item for item in stage2_records}
                official_adapters = (
                    build_official_enrichment_adapters(config, source_names=provider_names)
                    if stage2_records
                    else []
                )
                for adapter in official_adapters:
                    try:
                        batch = adapter.collect_for_records(stage2_records, observed_at)
                    except Exception as exc:
                        for item in stage2_records:
                            official_status_by_property.setdefault(item.property_id, {})[adapter.name] = "FAILED"
                            verification = VerificationItem(
                                verification_id=f"verification:{uuid4().hex}",
                                property_id=item.property_id,
                                source_registry_id=adapter.source_registry_id,
                                source_url=adapter.source_url,
                                status="FAILED",
                                reason=f"{type(exc).__name__}: {exc}",
                                observed_at=observed_at,
                                weekly_run_id=run_id,
                                details={"adapter": adapter.name},
                            )
                            verification_items.append(verification)
                            if not dry_run:
                                storage.save_verification_item(verification)
                        status = "PARTIAL"
                        parse_errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
                        continue
                    source_run = batch.source_run
                    official_source_run_id: int | None = None
                    if not dry_run and run_id is not None:
                        linked_source_run = source_run.model_copy(update={"weekly_run_id": run_id})
                        official_source_run_id = storage.save_source_run(linked_source_run)
                        source_run_ids[source_run.source_registry_id] = official_source_run_id
                        source_runs.append(
                            linked_source_run.model_copy(update={"source_run_id": official_source_run_id})
                        )
                    else:
                        source_runs.append(source_run)
                    if source_run.status.value != "SUCCESS":
                        status = "PARTIAL"
                    for row in batch.rows:
                        official_status_by_property.setdefault(row.property_id, {})[adapter.name] = row.status.value
                        if "PID_MISMATCH" in row.warnings:
                            subject = stage2_records_by_id.get(row.property_id)
                            conflict = DataConflict(
                                conflict_id=(
                                    f"conflict:{row.property_id}:pid:official:"
                                    f"{adapter.source_registry_id}"
                                ),
                                property_id=row.property_id,
                                field_name="pid",
                                conflict_type="OFFICIAL_IDENTITY_MISMATCH",
                                source_values={
                                    "property_record": subject.pid if subject else None,
                                    "official_pids": next(
                                        (
                                            warning.split("=", 1)[1]
                                            for warning in row.warnings
                                            if warning.startswith("OFFICIAL_PIDS=")
                                        ),
                                        "UNKNOWN",
                                    ),
                                },
                                conflicting_sources=[
                                    "property_record",
                                    adapter.source_registry_id,
                                    row.source_url,
                                ],
                                detected_at=row.retrieved_at or observed_at,
                            )
                            conflicts.append(conflict)
                            official_conflicted_ids.add(row.property_id)
                            if not dry_run:
                                storage.save_conflict(conflict, weekly_run_id=run_id)
                        if row.status.value != "SUCCESS":
                            verification = VerificationItem(
                                verification_id=f"verification:{uuid4().hex}",
                                property_id=row.property_id,
                                source_registry_id=adapter.source_registry_id,
                                source_url=row.source_url,
                                status=row.status.value,
                                reason="; ".join(row.warnings) or f"official lookup status: {row.status.value}",
                                match_method=row.match_method,
                                raw_snapshot_path=row.raw_snapshot_path,
                                observed_at=row.retrieved_at or observed_at,
                                weekly_run_id=run_id,
                                source_run_id=official_source_run_id,
                                details={"adapter": adapter.name, "query_scope": row.query_scope},
                            )
                            verification_items.append(verification)
                            if not dry_run:
                                storage.save_verification_item(verification)
                    official_providers.append(
                        JsonMappingProvider(
                            name=adapter.name,
                            data=[
                                {
                                    "property_id": row.property_id,
                                    "fields": row.fields,
                                    "status": row.status.value,
                                    "source_url": row.source_url,
                                    "source_type": adapter.source_type.value,
                                    "source_registry_id": adapter.source_registry_id,
                                    "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else observed_at.isoformat(),
                                    "source_version": row.source_version,
                                    "match_method": row.match_method,
                                    "query_scope": row.query_scope,
                                    "raw_snapshot_path": row.raw_snapshot_path,
                                    "warnings": row.warnings,
                                }
                                for row in batch.rows
                            ],
                            source_type=adapter.source_type,
                            source_url=adapter.source_url,
                            source_registry_id=adapter.source_registry_id,
                            config={"enabled": True},
                        )
                    )
                providers = [
                    *build_enrichment_providers(config, provider_names=provider_names),
                    *official_providers,
                ]
                updated_records: list[PropertyRecord] = []
                for record in records:
                    if record.property_id not in official_query_ids:
                        updated_records.append(record)
                        continue
                    official_status = official_status_by_property.get(record.property_id, {})
                    record_for_enrichment = record.model_copy(update={
                        "official_enrichment_status": {
                            **record.official_enrichment_status,
                            **official_status,
                        },
                        "data_conflict": record.data_conflict or record.property_id in official_conflicted_ids,
                        "major_data_conflict": record.major_data_conflict or record.property_id in official_conflicted_ids,
                    })
                    result = _link_enrichment_result(
                        enrich_property(
                            record_for_enrichment,
                            providers=providers,
                            observed_at=observed_at,
                            config=config,
                            source_registry=source_registry,
                        ),
                        weekly_run_id=run_id,
                        source_run_ids=source_run_ids,
                    )
                    resolved_conflict_ids = {
                        item.conflict_id for item in result.resolved_conflicts
                    }
                    if resolved_conflict_ids:
                        conflicts = [
                            item
                            for item in conflicts
                            if item.conflict_id not in resolved_conflict_ids
                        ]
                        if not dry_run:
                            for resolved_conflict in result.resolved_conflicts:
                                storage.save_conflict(
                                    resolved_conflict,
                                    weekly_run_id=run_id,
                                )
                        remaining_property_conflicts = [
                            item
                            for item in conflicts
                            if item.property_id == record.property_id
                        ]
                        if not remaining_property_conflicts:
                            result = result.model_copy(update={
                                "property": result.property.model_copy(update={
                                    "data_conflict": False,
                                    "major_data_conflict": False,
                                })
                            })
                    enrichments[record.property_id] = result
                    updated_records.append(result.property)
                    provenance_by_property.setdefault(record.property_id, []).extend(result.provenance)
                    conflicts.extend(result.conflicts)
                    if any(item.status != "SUCCESS" for item in result.provider_results):
                        status = "PARTIAL"
                    if not dry_run:
                        provider_registry_ids = [
                            item.source_registry_id
                            for item in result.provenance
                            if item.source_registry_id
                        ]
                        storage.save_enrichment_observation(
                            result,
                            observed_at=observed_at,
                            weekly_run_id=run_id,
                            source_registry_id=(
                                ",".join(dict.fromkeys(provider_registry_ids))
                                if provider_registry_ids
                                else None
                            ),
                            source_version=next(
                                (item.source_version for item in result.provenance if item.source_version),
                                None,
                            ),
                        )
                        storage.save_provenance(result.provenance)
                        for conflict in result.conflicts:
                            storage.save_conflict(conflict, weekly_run_id=run_id)
                records = updated_records
                conflicts = _dedupe_conflicts(conflicts)
                enrichment_partial = any(
                    any(item.status != "SUCCESS" for item in result.provider_results)
                    for result in enrichments.values()
                )
                stage_status["enrich"] = "PARTIAL" if enrichment_partial else "COMPLETED"

            if "comps" in requested:
                comp_rows, comparable_errors = _load_comparable_rows(config)
                parse_errors.extend(comparable_errors)
                if comparable_errors:
                    status = "PARTIAL"
                stage2_ids = _stage2_property_ids(stage1, records)
                updated_records = []
                for record in records:
                    if record.property_id not in stage2_ids:
                        updated_records.append(record)
                        continue
                    result = evaluate_comparables(
                        record,
                        comp_rows,
                        config,
                        observed_at=observed_at,
                        source_registry=source_registry,
                    )
                    comparable_results[record.property_id] = result
                    updated_records.append(result.property)
                    if result.manual_verification_required:
                        comparable_source = next(
                            (
                                source
                                for comparable in result.selected_comparables
                                for source in comparable.source_records
                                if source.field_name == "sold_history"
                            ),
                            None,
                        )
                        comparable_verification = VerificationItem(
                            verification_id=f"verification:{uuid4().hex}",
                            property_id=record.property_id,
                            source_registry_id=(
                                comparable_source.source_registry_id
                                if comparable_source and comparable_source.source_registry_id
                                else str(config.get("comparables", {}).get("source_registry_id") or "UNKNOWN")
                            ),
                            source_url=(
                                comparable_source.source_url
                                if comparable_source
                                else "UNKNOWN"
                            ),
                            status="PENDING_MANUAL",
                            reason="Single-source sold comparable valuation is provisional; cross-check deferred.",
                            observed_at=(
                                comparable_source.retrieved_at
                                if comparable_source
                                else observed_at
                            ),
                            weekly_run_id=run_id,
                            details={
                                "verification_type": "MANUAL_PROVISIONAL_COMPARABLE",
                                "valuation_status": result.valuation_status,
                                "reasons": result.manual_verification_reasons,
                            },
                        )
                        verification_items.append(comparable_verification)
                        if not dry_run:
                            storage.save_verification_item(comparable_verification)
                    if not dry_run:
                        linked_comparables = []
                        linked_benchmark_comparables = []
                        persisted_comp_keys: set[tuple[Any, ...]] = set()
                        comparable_rows = [
                            ("selected", comp)
                            for comp in result.selected_comparables
                        ] + [
                            ("benchmark", comp)
                            for comp in result.neighbourhood_benchmark_comparables
                        ]
                        for population, comp in comparable_rows:
                            linked_comp = comp.model_copy(update={
                                "source_records": _link_provenance(
                                    comp.source_records,
                                    weekly_run_id=run_id,
                                )
                            })
                            if population == "selected":
                                linked_comparables.append(linked_comp)
                            else:
                                linked_benchmark_comparables.append(linked_comp)
                            persist_key = (
                                linked_comp.comparable_id,
                                linked_comp.sold_date,
                                linked_comp.sold_price,
                            )
                            if persist_key in persisted_comp_keys:
                                continue
                            persisted_comp_keys.add(persist_key)
                            comp_payload = linked_comp.model_dump(mode="json")
                            comp_source_registry_id = next(
                                (
                                    item.source_registry_id
                                    for item in linked_comp.source_records
                                    if item.source_registry_id
                                ),
                                None,
                            )
                            comp_source_version = next(
                                (
                                    item.source_version
                                    for item in linked_comp.source_records
                                    if item.source_version
                                ),
                                None,
                            )
                            storage.save_comparable(
                                record.property_id,
                                comp_payload,
                                weekly_run_id=run_id,
                                source_registry_id=comp_source_registry_id,
                                source_version=comp_source_version,
                            )
                            # Comparable evidence belongs to the comparable's
                            # own identity; persist it separately so the sold
                            # input remains auditable without inflating the
                            # subject property's confidence automatically.
                            storage.save_provenance(linked_comp.source_records)
                        result = result.model_copy(update={
                            "selected_comparables": linked_comparables,
                            "neighbourhood_benchmark_comparables": linked_benchmark_comparables,
                        })
                        comparable_results[record.property_id] = result
                records = updated_records
                stage_status["comps"] = "PARTIAL" if comparable_errors else "COMPLETED"

            if "redevelop" in requested:
                stage2_ids = _stage2_property_ids(stage1, records)
                updated_records = []
                for record in records:
                    if record.property_id not in stage2_ids:
                        updated_records.append(record)
                        continue
                    result = analyze_redevelopment(
                        record,
                        config,
                        comparable_result=comparable_results.get(record.property_id),
                        observed_at=observed_at,
                        source_registry=source_registry,
                    )
                    redevelopment_results[record.property_id] = result
                    updated_records.append(result.property)
                    if not dry_run:
                        for scenario in result.scenarios:
                            scenario_source_registry_id = next(
                                (
                                    item.source_registry_id
                                    for item in provenance_by_property.get(record.property_id, [])
                                    if item.field_name in {"zoning", "current_zoning", "approved_use"}
                                    and item.source_type is SourceType.OFFICIAL_GOV
                                    and item.source_registry_id
                                ),
                                None,
                            )
                            storage.save_scenario(
                                scenario,
                                recorded_at=observed_at,
                                weekly_run_id=run_id,
                                source_registry_id=scenario_source_registry_id,
                                source_version=scenario.bylaw_version,
                            )
                records = updated_records
                stage_status["redevelop"] = "COMPLETED"

            if "score" in requested:
                updated_records = []
                thresholds = dict(config.get("scoring", {}).get("candidate_thresholds", {}))
                research_policy = config.get("research_policy", {})
                thresholds.setdefault(
                    "major_road_discount_threshold",
                    research_policy.get("major_road_discount_threshold", 0.15),
                )
                thresholds.setdefault(
                    "major_road_distance_threshold_m",
                    research_policy.get("major_road_distance_threshold_m", 50),
                )
                for record in records:
                    property_conflicts = [item.field_name for item in conflicts if item.property_id == record.property_id]
                    evidence = _evidence_for(
                        record,
                        provenance_by_property.get(record.property_id, []),
                        source_registry=source_registry,
                    )
                    scenarios = redevelopment_results.get(record.property_id)
                    snapshot = score_property(
                        record,
                        config,
                        evidence=evidence,
                        conflicts=property_conflicts,
                        scenarios=scenarios.scenarios if scenarios else None,
                        as_of=observed_at,
                        generated_at=observed_at,
                    )
                    resolution = resolve_candidate_state(
                        record,
                        total_score=snapshot.total_score,
                        data_confidence_score=snapshot.data_confidence_score,
                        manual_verification_pending=(
                            record.property_id in pending_manual_verification_ids
                        ),
                        thresholds=thresholds,
                    )
                    snapshot_source_registry_id = next(
                        (
                            item.source_registry_id
                            for item in provenance_by_property.get(record.property_id, [])
                            if item.source_registry_id
                        ),
                        None,
                    )
                    snapshot = snapshot.model_copy(update={
                        "candidate_status": resolution.state,
                        "state_reasons": resolution.reason_codes,
                        "weekly_run_id": run_id,
                        "source_registry_id": snapshot_source_registry_id,
                        "source_run_id": source_run_ids.get(snapshot_source_registry_id or ""),
                    })
                    record = _record_with_snapshot(record, snapshot, resolution)
                    snapshots[record.property_id] = snapshot
                    resolutions[record.property_id] = resolution
                    updated_records.append(record)
                    if not dry_run:
                        storage.save_score_snapshot(snapshot)
                records = updated_records
                stage_status["score"] = "COMPLETED"

            if not dry_run:
                # Acquisition and Stage 1 can discover title/PID conflicts
                # before enrichment runs; persist the complete queue here so
                # `research-conflicts` has the same view as the report.
                for conflict in conflicts:
                    storage.save_conflict(conflict, weekly_run_id=run_id)
                conflicts = _dedupe_conflicts([
                    *conflicts,
                    *storage.list_open_conflicts(),
                ])
                if run_id is not None:
                    source_runs = [
                        item.model_copy(update={"weekly_run_id": run_id})
                        for item in source_runs
                    ]
                pipeline_source = SourceRegistryEntry(
                    source_registry_id="source:research-pipeline",
                    name="Research pipeline normalized observation",
                    source_type=SourceType.MANUAL,
                    access_method="pipeline",
                    authorization_status="NOT_APPLICABLE",
                    reliability_notes="Current projection generated from explicitly supplied source runs.",
                )
                storage.register_source(pipeline_source)
                for result in source_runs:
                    event = source_run_status_event(
                        result,
                        observed_at=observed_at,
                        weekly_run_id=run_id,
                        source_run_id=source_run_ids.get(result.source_registry_id),
                    )
                    storage.record_watchlist_event(event)
                    events.append(event)
                observation_source_registry_id = (
                    source_runs[0].source_registry_id
                    if len(source_runs) == 1
                    else "source:research-pipeline"
                )
                for record in records:
                    observation = PropertyObservation(
                        observation_id=f"observation:{uuid4().hex}",
                        property_id=record.property_id,
                        observed_at=observed_at,
                        source_registry_id=observation_source_registry_id,
                        parser_version="research-v1-phase2-5",
                        raw_payload={
                            "record": record.model_dump(mode="json", by_alias=True),
                            "source_runs": [item.model_dump(mode="json") for item in source_runs],
                        },
                        normalized_record=record,
                    )
                    storage.save_observation(observation, weekly_run_id=run_id)
                # Report only events created by this run; prior history remains in SQLite.
                events = storage.list_watchlist_events()[event_start_count:]

            summary = summarize_research_run(
                records,
                snapshots=snapshots,
                resolutions=resolutions,
                conflicts=conflicts,
                coverage_gaps=run_coverage_gaps,
                stage1_candidates=stage1.included_count if stage1 else len(records),
                stage1_research_queue=stage1.research_candidate_count if stage1 else len(records),
                verification_queue_count=stage1.verification_queue_count if stage1 else None,
                special_situation_count=stage1.special_situation_count if stage1 else None,
                deep_research_count=sum(
                    1
                    for record in records
                    if stage1 is None or record.property_id in _stage2_property_ids(stage1, records)
                ),
                primary_source_status=primary_source_status,
                primary_source_names=primary_source_names,
                fallback_used=fallback_used,
                source_priority=source_priority,
                source_runs=source_runs,
                stage1_counts={
                    "normalized_records": stage1.normalized_count if stage1 else len(records),
                    "in_range": sum(
                        1
                        for item in (stage1.candidates if stage1 else ())
                        if item.range_classification in {"CORE", "EXTENDED", "SPECIAL"}
                    ) if stage1 else len(records),
                    "excluded": stage1.excluded_count if stage1 else 0,
                    "NEED_VERIFICATION": stage1.verification_queue_count if stage1 else 0,
                },
            )
            summary.update({
                "source_runs": len(source_runs),
                "source_errors": sum(len(item.errors) for item in source_runs),
                "official_verification_queue_count": sum(
                    1
                    for item in verification_items
                    if not _is_manual_provisional_verification(item)
                ),
                "manual_verification_queue_count": sum(
                    1
                    for item in verification_items
                    if _is_manual_provisional_verification(item)
                ),
                "stage_status": stage_status,
                "delta_events": len(events),
                "parse_errors": len(parse_errors),
            })
            run_result = ResearchRunResult(
                run_id=run_id,
                run_type=run_type,
                status=status,
                observed_at=observed_at,
                dry_run=dry_run,
                stage_status=stage_status,
                summary=summary,
                records=records,
                stage1=stage1,
                source_runs=source_runs,
                enrichments=enrichments,
                comparable_results=comparable_results,
                redevelopment_results=redevelopment_results,
                snapshots=snapshots,
                resolutions=resolutions,
                provenance=provenance_by_property,
                conflicts=conflicts,
                events=events,
                verification_items=verification_items,
                parse_errors=parse_errors,
                primary_source_status=primary_source_status,
                primary_source_names=primary_source_names,
                fallback_used=fallback_used,
                source_priority=source_priority,
            )
            if "report" in requested:
                if dry_run:
                    stage_status["report"] = "COMPLETED"
                else:
                    # Make the persisted report reflect the state at the point
                    # it is generated, rather than the pre-report PENDING value.
                    stage_status["report"] = "COMPLETED"
                    summary["stage_status"] = dict(stage_status)
                    run_result.summary = summary
                    run_result.stage_status = stage_status
                    report_path, latest_path = _write_operational_report(config, observed_at, run_result)
            else:
                stage_status["report"] = "SKIPPED"
                summary["stage_status"] = dict(stage_status)
                run_result.summary = summary
            summary["stage_status"] = dict(stage_status)
            run_result.summary = summary
            run_result.stage_status = stage_status
            run_result.report_path = report_path
            run_result.latest_path = latest_path
            if not dry_run:
                storage.finish_weekly_run(run_id, status=status, summary=summary, finished_at=observed_at)
            return run_result
        except Exception as exc:
            status = "FAILED"
            if not dry_run and run_id is not None:
                storage.finish_weekly_run(run_id, status=status, summary={"error": str(exc)}, finished_at=observed_at)
            raise
