from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from ..config import resolve_path
from .models import EvidenceStatus, SourceRole, SourceType


CONFIDENCE_WEIGHTS: dict[str, int] = {
    "official_pid_confirmed": 15,
    "ownership_confirmed": 15,
    "official_assessment": 10,
    "lot_dimensions_confirmed": 10,
    "official_zoning": 10,
    "approved_use_confirmed": 10,
    "listing_history_confirmed": 10,
    "sold_history_cross_checked": 10,
    "official_school_catchment": 5,
    "road_location_verified": 5,
}


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: str
    field_name: str
    source_url: str
    source_type: SourceType
    source_registry_id: str | None = None
    source_role: str | None = None
    source_run_id: int | None = None
    weekly_run_id: int | None = None
    retrieved_at: datetime
    raw_value: Any = None
    normalized_value: Any = None
    source_version: str | None = None
    evidence_status: EvidenceStatus = EvidenceStatus.UNCONFIRMED
    notes: str | None = None
    match_method: str | None = None
    query_scope: str | None = None
    raw_snapshot_path: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class SourceRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_registry_id: str
    name: str
    source_type: SourceType
    source_role: SourceRole = SourceRole.UNSPECIFIED
    priority: int | None = None
    domain: str | None = None
    access_method: str | None = None
    authorization_status: str = "UNKNOWN"
    evidence_status: EvidenceStatus = EvidenceStatus.UNCONFIRMED
    reliability_notes: str | None = None
    reviewed_at: datetime | None = None
    enabled: bool = True
    last_run_status: str | None = None
    last_run_at: datetime | None = None


def registry_allows_evidence(
    registry: SourceRegistryEntry | None,
    *,
    expected_source_type: SourceType,
) -> bool:
    """Return whether a registry entry may support a legal/data claim.

    Local fixtures are useful for deterministic validation but are not
    evidence for acquisition decisions unless explicitly registered as a
    confirmed source.  Missing, disabled, mismatched, or unauthorized
    entries fail closed.
    """

    if registry is None or not registry.enabled:
        return False
    if registry.source_type is not expected_source_type:
        return False
    status = str(registry.authorization_status or "").upper()
    return (
        status in {"AUTHORIZED", "NOT_APPLICABLE", "CONFIRMED"}
        and registry.evidence_status is EvidenceStatus.CONFIRMED
    )


def load_source_registry_entries(
    config: dict[str, Any] | None = None,
    *,
    path: str | Path | None = None,
) -> dict[str, SourceRegistryEntry]:
    """Load the configured source registry as the claim trust boundary."""

    cfg = config or {}
    raw: Any = cfg.get("source_registry")
    if raw is None:
        raw = cfg.get("_source_registry")
    if raw is None:
        registry_path = path or cfg.get("source_registry_path")
        if not registry_path:
            return {}
        try:
            raw = yaml.safe_load(resolve_path(registry_path, cfg).read_text(encoding="utf-8")) or {}
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            return {}
    if isinstance(raw, dict):
        entries = raw.get("sources", raw)
    else:
        entries = raw
    if isinstance(entries, dict):
        entries = [dict(value, source_registry_id=key) for key, value in entries.items() if isinstance(value, dict)]
    if not isinstance(entries, list):
        return {}
    result: dict[str, SourceRegistryEntry] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            entry = SourceRegistryEntry.model_validate(item)
        except (TypeError, ValueError):
            continue
        result[entry.source_registry_id] = entry
    return result


def evidence_status_for_source(
    source_registry: dict[str, SourceRegistryEntry] | None,
    source_registry_id: str | None,
    source_type: SourceType,
) -> EvidenceStatus:
    registry = source_registry.get(source_registry_id) if source_registry and source_registry_id else None
    return EvidenceStatus.CONFIRMED if registry_allows_evidence(registry, expected_source_type=source_type) else EvidenceStatus.UNCONFIRMED


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    maximum_points: int = Field(ge=0)
    confirmed: bool = False
    conflict: bool = False
    source_refs: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def points(self) -> int:
        return self.maximum_points if self.confirmed and not self.conflict else 0


class ConfidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    band: str
    items: list[EvidenceItem] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)


class DataConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    property_id: str
    field_name: str
    conflict_type: str
    source_values: dict[str, Any] = Field(default_factory=dict)
    conflicting_sources: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolution_status: str = "OPEN"
    resolution_note: str | None = None


def default_evidence_items(
    confirmed: Iterable[str] | None = None,
    conflicts: Iterable[str] | None = None,
) -> list[EvidenceItem]:
    confirmed_names = set(confirmed or ())
    conflict_names = set(conflicts or ())
    return [
        EvidenceItem(
            name=name,
            maximum_points=points,
            confirmed=name in confirmed_names,
            conflict=name in conflict_names,
        )
        for name, points in CONFIDENCE_WEIGHTS.items()
    ]


def confidence_band(
    score: float,
    *,
    high_threshold: float = 85,
    medium_threshold: float = 70,
) -> str:
    if score >= high_threshold:
        return "High"
    if score >= medium_threshold:
        return "Medium"
    return "Needs Verification"


def calculate_data_confidence(
    items: Iterable[EvidenceItem],
    *,
    high_threshold: float = 85,
    medium_threshold: float = 70,
) -> ConfidenceResult:
    materialized = list(items)
    score = sum(item.points for item in materialized)
    conflicts = sorted(item.name for item in materialized if item.conflict)
    return ConfidenceResult(
        score=score,
        band=confidence_band(
            score,
            high_threshold=high_threshold,
            medium_threshold=medium_threshold,
        ),
        items=materialized,
        unresolved_conflicts=conflicts,
    )
