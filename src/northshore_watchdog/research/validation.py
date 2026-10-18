from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import yaml

from ..config import project_root, resolve_path
from .models import CaseQuality, EvidenceStatus, PropertyObservation, PropertyRecord, ScoreSnapshot, SourceType
from .normalization import normalize_property
from .provenance import (
    DataConflict,
    EvidenceItem,
    SourceRecord,
    SourceRegistryEntry,
    calculate_data_confidence,
    default_evidence_items,
    evidence_status_for_source,
    registry_allows_evidence,
)
from .report import build_weekly_report, summarize_research_run, write_latest_report, write_weekly_report
from .scoring import build_baseline_scenarios, score_property
from .state import StateResolution, resolve_candidate_state
from .storage import ResearchStorage


REQUIRED_CASE_FIELDS = {
    "case_id",
    "quality",
    "payload_path",
    "source_references",
    "expected_behavior",
    "verification_notes",
}


def _manifest_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate
    return project_root() / candidate


def classify_case_quality(case: dict[str, Any]) -> CaseQuality:
    try:
        return CaseQuality(case["quality"])
    except KeyError as exc:
        raise ValueError("case quality is required") from exc
    except ValueError as exc:
        raise ValueError(f"unknown case quality: {case.get('quality')!r}") from exc


def _validate_source_reference(case_id: str, reference: dict[str, Any]) -> None:
    for field in ("source_url", "source_type", "observed_at"):
        if reference.get(field) in (None, ""):
            raise ValueError(f"case {case_id} source reference missing {field}")
    try:
        SourceType(reference["source_type"])
    except ValueError as exc:
        raise ValueError(
            f"case {case_id} has unsupported source type {reference.get('source_type')!r}"
        ) from exc
    if "T" not in str(reference["observed_at"]):
        raise ValueError(f"case {case_id} observed_at must be an ISO timestamp")


def load_case_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = _manifest_path(path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle) or {}
    if manifest.get("schema_version") != "research-v1":
        raise ValueError("case manifest schema_version must be research-v1")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("case manifest cases must be a list")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each case manifest entry must be a mapping")
        missing = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing:
            raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} missing {', '.join(missing)}")
        case_id = str(case["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        classify_case_quality(case)
        if not isinstance(case["source_references"], list) or not case["source_references"]:
            raise ValueError(f"case {case_id} source_references must be non-empty")
        for reference in case["source_references"]:
            if not isinstance(reference, dict):
                raise ValueError(f"case {case_id} source reference must be a mapping")
            _validate_source_reference(case_id, reference)
        if not isinstance(case["expected_behavior"], list) or not case["expected_behavior"]:
            raise ValueError(f"case {case_id} expected_behavior must be non-empty")
        if not str(case["verification_notes"]).strip():
            raise ValueError(f"case {case_id} verification_notes must be non-empty")
        if not case.get("payload_path") and "payload" not in case:
            raise ValueError(f"case {case_id} needs payload_path or inline payload")
    manifest["_manifest_path"] = str(manifest_path)
    manifest["coverage_gaps"] = manifest.get("coverage_gaps", []) or []
    return manifest


def load_case_payload(
    case: dict[str, Any],
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    if "payload" in case:
        payload = case["payload"]
        if not isinstance(payload, dict):
            raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} inline payload must be a mapping")
        return dict(payload)
    if not case.get("payload_path"):
        raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} has no payload_path")
    if manifest_path is None:
        base = project_root() / "data/research/cases"
    else:
        base = _manifest_path(manifest_path).parent
    payload_path = Path(case["payload_path"])
    if not payload_path.is_absolute():
        payload_path = base / payload_path
    if not payload_path.exists():
        raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} payload does not exist: {payload_path}")
    import json

    with payload_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} payload must be a mapping")
    return payload


def build_validation_input(case: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Combine a validated manifest entry and payload without inventing fields."""

    quality = classify_case_quality(case)
    if not case.get("source_references"):
        raise ValueError(f"case {case.get('case_id', 'UNKNOWN')} source_references are required")
    return {
        "case_id": case["case_id"],
        "quality": quality,
        "payload": dict(payload),
        "source_references": [dict(reference) for reference in case["source_references"]],
        "expected_behavior": list(case["expected_behavior"]),
        "verification_notes": case["verification_notes"],
        "category": case.get("category"),
    }


def _timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _source_records(
    validation_input: dict[str, Any],
    property_id: str,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> list[SourceRecord]:
    payload = validation_input["payload"]
    records: list[SourceRecord] = []
    for reference in validation_input["source_references"]:
        field_name = str(reference.get("field_name") or "case_reference")
        value = reference.get("normalized_value", payload.get(field_name))
        raw_value = reference.get("raw_value", value)
        records.append(
            SourceRecord(
                property_id=property_id,
                field_name=field_name,
                source_url=str(reference["source_url"]),
                source_type=SourceType(reference["source_type"]),
                source_registry_id=_source_registry_id(reference),
                retrieved_at=_timestamp(reference["observed_at"]),
                raw_value=raw_value,
                normalized_value=value,
                source_version=reference.get("source_version"),
                evidence_status=evidence_status_for_source(
                    dict(source_registry or {}),
                    _source_registry_id(reference),
                    SourceType(reference["source_type"]),
                ),
                notes=reference.get("notes"),
            )
        )
    return records


def _ref_fields(source_records: list[SourceRecord], *names: str) -> list[SourceRecord]:
    wanted = set(names)
    return [item for item in source_records if item.field_name in wanted]


def _has_source_type(source_records: list[SourceRecord], source_type: SourceType, *names: str) -> bool:
    return any(item.source_type is source_type for item in _ref_fields(source_records, *names))


def _build_evidence(
    payload: dict[str, Any],
    source_records: list[SourceRecord],
    *,
    source_registry: Mapping[str, SourceRegistryEntry] | None = None,
) -> list[EvidenceItem]:
    def trusted(source: SourceRecord, expected_type: SourceType) -> bool:
        if source.source_type is not expected_type:
            return False
        if source.evidence_status is not EvidenceStatus.CONFIRMED:
            return False
        registry = source_registry.get(source.source_registry_id) if source_registry and source.source_registry_id else None
        return registry_allows_evidence(registry, expected_source_type=expected_type)

    fields = {item.field_name for item in source_records}
    confirmed: set[str] = set()
    if payload.get("pid") or payload.get("PID"):
        if any(
            trusted(item, expected_type)
            for expected_type in (SourceType.OFFICIAL_GOV, SourceType.BC_ASSESSMENT)
            for item in _ref_fields(source_records, "pid", "PID", "pids")
        ):
            confirmed.add("official_pid_confirmed")
    if payload.get("ownership_type") or payload.get("tenure"):
        if any(trusted(item, SourceType.OFFICIAL_GOV) for item in _ref_fields(source_records, "ownership", "ownership_type", "tenure")):
            confirmed.add("ownership_confirmed")
    if payload.get("assessment_total") is not None and _has_source_type(
        source_records, SourceType.BC_ASSESSMENT, "assessment", "assessment_total", "assessment_land", "assessment_improvement"
    ) and any(trusted(item, SourceType.BC_ASSESSMENT) for item in _ref_fields(source_records, "assessment", "assessment_total", "assessment_land", "assessment_improvement")):
        confirmed.add("official_assessment")
    if payload.get("lot_area_sqft") is not None and (
        "lot" in fields or "listing" in fields or "listing_snapshot" in fields
    ) and any(
        item.source_type in {SourceType.MLS, SourceType.VOW, SourceType.AGGREGATOR, SourceType.OFFICIAL_GOV}
        and (source_registry is not None and source_registry.get(item.source_registry_id or "") is not None)
        for item in source_records
    ):
        confirmed.add("lot_dimensions_confirmed")
    if payload.get("zoning") and any(trusted(item, SourceType.OFFICIAL_GOV) for item in _ref_fields(source_records, "zoning", "current_zoning")):
        confirmed.add("official_zoning")
    if payload.get("approved_use") and _has_source_type(
        source_records, SourceType.OFFICIAL_GOV, "approved_use", "current_approved_use"
    ) and any(trusted(item, SourceType.OFFICIAL_GOV) for item in _ref_fields(source_records, "approved_use", "current_approved_use")):
        confirmed.add("approved_use_confirmed")
    listing_refs = _ref_fields(source_records, "asking", "listing", "listing_snapshot", "listing_history")
    if payload.get("asking") is not None and any(
        item.source_type in {SourceType.MLS, SourceType.VOW, SourceType.AGGREGATOR}
        and source_registry is not None
        and source_registry.get(item.source_registry_id or "") is not None
        for item in listing_refs
    ):
        confirmed.add("listing_history_confirmed")
    sold_refs = _ref_fields(source_records, "sold_history")
    sold_sources = {
        item.source_registry_id or item.source_url
        for item in sold_refs
        if source_registry is not None and source_registry.get(item.source_registry_id or "") is not None
    }
    if len(sold_sources) >= 2:
        confirmed.add("sold_history_cross_checked")
    if payload.get("official_elementary_catchment") and any(trusted(item, SourceType.OFFICIAL_GOV) for item in _ref_fields(source_records, "official_elementary_catchment", "school_catchment")):
        confirmed.add("official_school_catchment")
    if payload.get("road_class") and any(trusted(item, SourceType.OFFICIAL_GOV) for item in _ref_fields(source_records, "road_class", "road_location")):
        confirmed.add("road_location_verified")
    return default_evidence_items(confirmed=confirmed)


def _source_registry_id(reference: dict[str, Any]) -> str:
    raw = f"{reference['source_type']}|{reference['source_url']}"
    return f"source:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _register_case_sources(storage: ResearchStorage, validation_input: dict[str, Any]) -> str:
    references = validation_input["source_references"]
    first_id = ""
    for reference in references:
        source_id = _source_registry_id(reference)
        if not first_id:
            first_id = source_id
        source_type = SourceType(reference["source_type"])
        storage.register_source(
            SourceRegistryEntry(
                source_registry_id=source_id,
                name=f"Phase 1 local reference: {reference['source_url']}",
                source_type=source_type,
                access_method="local_manifest_reference",
                authorization_status="NOT_APPLICABLE",
                evidence_status="CONFIRMED",
                reliability_notes="Read-only local Phase 1 evidence; no remote fetch performed.",
            )
        )
    return first_id


def _expected_behavior_failures(
    validation_input: dict[str, Any],
    record: PropertyRecord,
    resolution: StateResolution,
) -> list[str]:
    failures: list[str] = []
    expected = " ".join(str(item).lower() for item in validation_input["expected_behavior"])
    if "not an active site visit" in expected or "not site visit" in expected or "sold status" in expected:
        if resolution.site_visit_eligible:
            failures.append("sold case remained site-visit eligible")
    if "non-prepaid leasehold" in expected and resolution.state.value not in {"REJECT", "D_REJECT"}:
        failures.append("non-prepaid leasehold did not reject")
    if "bare land strata" in expected and resolution.state.value not in {"SPECIAL_SITUATION", "REJECT", "D_REJECT"}:
        failures.append("bare land strata entered normal ranking")
    if "unknown" in expected and record.data_confidence_score is not None and record.data_confidence_score >= 85:
        failures.append("case expected unknown verification but confidence was high")
    return failures


@dataclass
class Phase1ValidationResult:
    summary: dict[str, Any]
    records: list[PropertyRecord] = field(default_factory=list)
    snapshots: dict[str, ScoreSnapshot] = field(default_factory=dict)
    resolutions: dict[str, StateResolution] = field(default_factory=dict)
    provenance: dict[str, list[SourceRecord]] = field(default_factory=dict)
    scenarios: dict[str, list[Any]] = field(default_factory=dict)
    conflicts: list[DataConflict] = field(default_factory=list)
    expected_behavior_failures: list[str] = field(default_factory=list)
    report_path: Path | None = None
    weekly_path: Path | None = None
    latest_path: Path | None = None
    case_quality_counts: dict[str, int] = field(default_factory=dict)
    coverage_gaps: list[dict[str, Any]] = field(default_factory=list)


def run_phase1_validation(config: dict[str, Any]) -> Phase1ValidationResult:
    """Run only the local, read-only Phase 1 validation pipeline."""

    manifest_path = resolve_path(
        config.get("cases", {}).get("manifest_path", "data/research/cases/phase1_cases.yaml"),
        config,
    )
    manifest = load_case_manifest(manifest_path)
    db_path = resolve_path(
        config.get("storage", {}).get("sqlite_path", "outputs/research_watchdog.sqlite"),
        config,
    )
    records: list[PropertyRecord] = []
    snapshots: dict[str, ScoreSnapshot] = {}
    resolutions: dict[str, StateResolution] = {}
    provenance: dict[str, list[SourceRecord]] = {}
    scenarios: dict[str, list[Any]] = {}
    conflicts: list[DataConflict] = []
    failures: list[str] = []
    quality_counts: dict[str, int] = {quality.value: 0 for quality in CaseQuality}
    run_started = datetime.now(timezone.utc)

    with ResearchStorage(db_path) as storage:
        run_id = storage.start_weekly_run("phase1_validation", started_at=run_started)
        try:
            for case in manifest["cases"]:
                validation_input = build_validation_input(
                    case,
                    load_case_payload(case, manifest_path=manifest_path),
                )
                quality = validation_input["quality"]
                quality_counts[quality.value] = quality_counts.get(quality.value, 0) + 1
                source_registry = {
                    _source_registry_id(reference): SourceRegistryEntry(
                        source_registry_id=_source_registry_id(reference),
                        name=f"Phase 1 local reference: {reference['source_url']}",
                        source_type=SourceType(reference["source_type"]),
                        access_method="local_manifest_reference",
                        authorization_status="NOT_APPLICABLE",
                        evidence_status="CONFIRMED",
                        reliability_notes="Read-only local Phase 1 evidence; no remote fetch performed.",
                    )
                    for reference in validation_input["source_references"]
                }
                provisional = normalize_property(validation_input["payload"], config=config)
                source_records = _source_records(
                    validation_input,
                    provisional.property.property_id,
                    source_registry,
                )
                normalized = normalize_property(
                    validation_input["payload"],
                    source_records,
                    config=config,
                    source_registry=source_registry,
                )
                record = normalized.property
                case_scenarios = build_baseline_scenarios(record, config)
                evidence = _build_evidence(
                    validation_input["payload"],
                    source_records,
                    source_registry=source_registry,
                )
                snapshot = score_property(
                    record,
                    config,
                    evidence=evidence,
                    conflicts={conflict.field_name for conflict in normalized.conflicts},
                    scenarios=case_scenarios,
                    as_of=run_started,
                    generated_at=run_started,
                )
                resolution = resolve_candidate_state(
                    record,
                    total_score=snapshot.total_score,
                    data_confidence_score=snapshot.data_confidence_score,
                    thresholds={
                        **config.get("scoring", {}).get("candidate_thresholds", {}),
                        "major_road_discount_threshold": config.get("research_policy", {}).get(
                            "major_road_discount_threshold", 0.15
                        ),
                        "major_road_distance_threshold_m": config.get("research_policy", {}).get(
                            "major_road_distance_threshold_m", 50
                        ),
                    },
                )
                snapshot = snapshot.model_copy(
                    update={
                        "candidate_status": resolution.state,
                        "state_reasons": resolution.reason_codes,
                    }
                )
                record = record.model_copy(
                    update={
                        "live_score": snapshot.live_score,
                        "land_score": snapshot.land_score,
                        "investment_score": snapshot.investment_score,
                        "land_to_product_score": snapshot.land_to_product_score,
                        "total_score": snapshot.total_score,
                        "data_confidence_score": snapshot.data_confidence_score,
                        "data_confidence_band": snapshot.data_confidence_band,
                        "candidate_status": resolution.state,
                        "opportunity_tags": snapshot.opportunity_tags,
                        "data_conflict": bool(normalized.conflicts),
                        "last_updated": run_started,
                    }
                )
                old = storage.get_current_property(record.property_id)
                source_registry_id = _register_case_sources(storage, validation_input)
                observation = PropertyObservation(
                    observation_id=f"observation:{validation_input['case_id']}:{uuid4().hex[:12]}",
                    property_id=record.property_id,
                    observed_at=run_started,
                    source_registry_id=source_registry_id,
                    raw_payload=validation_input["payload"],
                    parser_version=config.get("schema_version", "research-v1"),
                    normalized_record=record,
                    normalization_warnings=normalized.warnings,
                )
                storage.save_observation(observation)
                storage.save_provenance(source_records)
                for conflict in normalized.conflicts:
                    storage.save_conflict(conflict)
                for scenario in case_scenarios:
                    storage.save_scenario(scenario)
                snapshot = snapshot.model_copy(
                    update={
                        "weekly_run_id": run_id,
                        "source_registry_id": source_registry_id,
                    }
                )
                snapshots[record.property_id] = snapshot
                storage.save_score_snapshot(snapshot)
                if old is not None and old.candidate_status != record.candidate_status:
                    storage.record_watchlist_event(
                        record.property_id,
                        "state_change",
                        {
                            "old_state": old.candidate_status.value,
                            "new_state": record.candidate_status.value,
                        },
                        created_at=run_started,
                    )
                failures.extend(
                    f"{validation_input['case_id']}: {failure}"
                    for failure in _expected_behavior_failures(validation_input, record, resolution)
                )
                records.append(record)
                snapshots[record.property_id] = snapshot
                resolutions[record.property_id] = resolution
                provenance[record.property_id] = source_records
                scenarios[record.property_id] = case_scenarios
                conflicts.extend(normalized.conflicts)

            summary = summarize_research_run(
                records,
                snapshots=snapshots,
                resolutions=resolutions,
                conflicts=conflicts,
                case_quality_counts=quality_counts,
                coverage_gaps=[item.get("category", "UNKNOWN") for item in manifest["coverage_gaps"]],
                stage1_candidates=len(manifest["cases"]),
                deep_research_count=len(records),
            )
            summary["expected_behavior_failures"] = failures
            storage.finish_weekly_run(run_id, summary=summary)
            events = storage.list_watchlist_events()
        except Exception as exc:
            storage.finish_weekly_run(run_id, status="FAILED", summary={"error": str(exc)})
            raise

    report = build_weekly_report(
        records,
        snapshots=snapshots,
        resolutions=resolutions,
        scenarios=scenarios,
        provenance=provenance,
        events=events,
        conflicts=conflicts,
        case_quality_counts=quality_counts,
        coverage_gaps=[item.get("category", "UNKNOWN") for item in manifest["coverage_gaps"]],
        stage1_candidates=len(manifest["cases"]),
        deep_research_count=len(records),
        generated_at=run_started,
        run_type="phase1_validation",
    )
    phase1_dir = resolve_path(config.get("reports", {}).get("phase1_dir", "reports/phase1"), config)
    report_path = write_weekly_report(
        phase1_dir / f"validation-{run_started.date().isoformat()}.md",
        report,
    )
    weekly_path = write_weekly_report(
        resolve_path(config.get("reports", {}).get("weekly_dir", "reports/weekly"), config)
        / f"{run_started.date().isoformat()}.md",
        report,
    )
    latest_path = write_latest_report(
        resolve_path(config.get("reports", {}).get("latest_path", "reports/latest.md"), config),
        report,
    )
    return Phase1ValidationResult(
        summary=summary,
        records=records,
        snapshots=snapshots,
        resolutions=resolutions,
        provenance=provenance,
        scenarios=scenarios,
        conflicts=conflicts,
        expected_behavior_failures=failures,
        report_path=report_path,
        weekly_path=weekly_path,
        latest_path=latest_path,
        case_quality_counts=quality_counts,
        coverage_gaps=manifest["coverage_gaps"],
    )
