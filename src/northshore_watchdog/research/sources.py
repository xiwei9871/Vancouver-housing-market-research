from __future__ import annotations

import csv
import inspect
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..models import Listing
from .models import SourceRole, SourceType


class SourceRunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    SKIPPED_UNAUTHORIZED = "SKIPPED_UNAUTHORIZED"
    BLOCKED_POLICY = "BLOCKED_POLICY"


class SourceSnapshot(BaseModel):
    """Immutable metadata for one raw source response."""

    model_config = ConfigDict(extra="forbid")

    source_registry_id: str
    url: str
    final_url: str | None = None
    method: str = "GET"
    request_kind: str = "target"
    status_code: int
    content_type: str | None = None
    raw_path: str
    screenshot_path: str | None = None
    sha256: str
    size_bytes: int
    observed_at: datetime
    retrieved_at: datetime
    source_version: str | None = None


class SourceRunResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_registry_id: str
    source_name: str
    status: SourceRunStatus
    source_role: str = SourceRole.UNSPECIFIED.value
    priority: int | None = None
    coverage_complete: bool | None = None
    source_run_id: int | None = None
    weekly_run_id: int | None = None
    started_at: datetime
    finished_at: datetime
    records: list[dict[str, Any]] = Field(default_factory=list)
    snapshots: list[SourceSnapshot] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    crawl_metrics: dict[str, Any] = Field(default_factory=dict)
    coverage_gaps: list[str] = Field(default_factory=list)


class SourceAdapter(Protocol):
    source_name: str
    source_type: SourceType
    source_role: str
    priority: int | None
    coverage_complete: bool | None

    def collect(self, observed_at: datetime) -> SourceRunResult:
        ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _source_type(value: SourceType | str | None) -> SourceType:
    if isinstance(value, SourceType):
        return value
    if value in (None, ""):
        return SourceType.MANUAL
    return SourceType(str(value))


def _source_role(value: SourceRole | str | None) -> str:
    if isinstance(value, SourceRole):
        return value.value
    if value in (None, ""):
        return SourceRole.UNSPECIFIED.value
    text = str(value)
    if text.startswith("SourceRole."):
        text = text.split(".", 1)[1]
    return text.upper()


def _priority(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rows_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "items", "listings", "data", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    raise ValueError("source payload must be a mapping or list of mappings")


def _record_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Listing):
        return value.model_dump(mode="python")
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"source row must be a mapping, got {type(value).__name__}")


class _BaseSource:
    def __init__(
        self,
        *,
        name: str,
        source_type: SourceType | str = SourceType.MANUAL,
        source_registry_id: str | None = None,
        source_url: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.source_name = name
        self.source_type = _source_type(source_type)
        self.source_registry_id = source_registry_id or f"source:{name}"
        self.source_url = source_url or f"local://{name}"
        self.config = dict(config or {})
        self.source_role = _source_role(self.config.get("source_role"))
        self.priority = _priority(self.config.get("priority"))
        self.coverage_complete = self.config.get("coverage_complete")

    def _result(
        self,
        *,
        started_at: datetime,
        records: list[dict[str, Any]] | None = None,
        snapshots: list[SourceSnapshot] | None = None,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        status: SourceRunStatus,
        finished_at: datetime | None = None,
    ) -> SourceRunResult:
        return SourceRunResult(
            source_registry_id=self.source_registry_id,
            source_name=self.source_name,
            status=status,
            source_role=self.source_role,
            priority=self.priority,
            coverage_complete=self.coverage_complete,
            started_at=_timestamp(started_at),
            finished_at=_timestamp(finished_at or _now()),
            records=records or [],
            snapshots=snapshots or [],
            errors=errors or [],
            warnings=warnings or [],
        )

    def _normalize_rows(
        self,
        payload: Any,
        observed_at: datetime,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        rows = _rows_from_payload(payload)
        snapshot_observed_at = self.config.get("snapshot_observed_at")
        snapshot_source_version = self.config.get("source_version")
        normalized: list[dict[str, Any]] = []
        errors: list[str] = []
        for index, row in enumerate(rows):
            try:
                item = _record_dict(row)
            except ValueError as exc:
                errors.append(f"row {index}: {exc}")
                continue
            if not item.get("source_url"):
                item["source_url"] = self.source_url
            # The adapter is the trust boundary.  A payload row may carry a
            # property-specific URL, but it cannot relabel the feed as an
            # official or otherwise different source.
            item["source_type"] = self.source_type.value
            if not item.get("observed_at"):
                item["observed_at"] = str(snapshot_observed_at or _timestamp(observed_at).isoformat())
            if not item.get("source_version") and snapshot_source_version:
                item["source_version"] = str(snapshot_source_version)
            item["source_registry_id"] = self.source_registry_id
            item["source_role"] = self.source_role
            normalized.append(item)
        return normalized, errors

    def _collect_payload(self, observed_at: datetime) -> Any:
        raise NotImplementedError

    def collect(self, observed_at: datetime) -> SourceRunResult:
        started_at = _now()
        if self.config.get("enabled") is False:
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.SKIPPED_DISABLED,
                finished_at=_now(),
            )
        try:
            payload = self._collect_payload(_timestamp(observed_at))
            records, errors = self._normalize_rows(payload, observed_at)
            if errors and records:
                status = SourceRunStatus.PARTIAL
            elif errors:
                status = SourceRunStatus.FAILED
            else:
                status = SourceRunStatus.SUCCESS
            return self._result(
                started_at=started_at,
                finished_at=_now(),
                records=records,
                errors=errors,
                status=status,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._result(
                started_at=started_at,
                finished_at=_now(),
                errors=[str(exc)],
                status=SourceRunStatus.FAILED,
            )


class UnsupportedSource(_BaseSource):
    """Represent an unimplemented configured source so it cannot disappear silently."""

    def __init__(self, *, name: str, kind: str, **kwargs: Any) -> None:
        super().__init__(name=name, **kwargs)
        self.kind = kind

    def _collect_payload(self, observed_at: datetime) -> Any:
        del observed_at
        raise ValueError(f"unsupported source type: {self.kind}")


class LocalJsonSource(_BaseSource):
    """Read a local JSON fixture or export without performing network access."""

    def __init__(
        self,
        *,
        name: str,
        path: str | Path,
        source_type: SourceType | str = SourceType.MANUAL,
        source_registry_id: str | None = None,
        source_url: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            source_registry_id=source_registry_id,
            source_url=source_url or f"local://{Path(path)}",
            config=config,
        )
        self.path = Path(path)

    def _collect_payload(self, observed_at: datetime) -> Any:
        del observed_at
        with self.path.open("r", encoding="utf-8") as handle:
            if self.path.suffix.lower() in {".jsonl", ".ndjson"}:
                return [json.loads(line) for line in handle if line.strip()]
            return json.load(handle)


class LocalCsvSource(_BaseSource):
    """Read a local CSV fixture/export using its header row as field names."""

    def __init__(
        self,
        *,
        name: str,
        path: str | Path,
        source_type: SourceType | str = SourceType.MANUAL,
        source_registry_id: str | None = None,
        source_url: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            source_registry_id=source_registry_id,
            source_url=source_url or f"local://{Path(path)}",
            config=config,
        )
        self.path = Path(path)

    def _collect_payload(self, observed_at: datetime) -> Any:
        del observed_at
        with self.path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


class LegacyCollectorSource(_BaseSource):
    """Explicit adapter for a pre-existing collector; never used implicitly."""

    def __init__(
        self,
        *,
        name: str,
        collector: Any,
        source_type: SourceType | str = SourceType.MLS,
        source_registry_id: str | None = None,
        source_url: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            source_registry_id=source_registry_id,
            source_url=source_url,
            config=config,
        )
        self.collector = collector

    def _collect_payload(self, observed_at: datetime) -> Any:
        target = self.collector.collect if hasattr(self.collector, "collect") else self.collector
        try:
            signature = inspect.signature(target)
            if signature.parameters:
                return target(observed_at)
        except (TypeError, ValueError):
            pass
        return target()


class AuthorizedJsonSource(_BaseSource):
    """Run a remote/authorized JSON transport only when policy explicitly permits it."""

    def __init__(
        self,
        *,
        name: str,
        source_type: SourceType | str,
        config: dict[str, Any] | None = None,
        transport: Callable[..., Any] | None = None,
        source_registry_id: str | None = None,
        source_url: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            source_type=source_type,
            source_registry_id=source_registry_id,
            source_url=source_url or f"authorized://{name}",
            config=config,
        )
        self.transport = transport

    def collect(self, observed_at: datetime) -> SourceRunResult:
        started_at = _now()
        if not bool(self.config.get("enabled", False)):
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.SKIPPED_DISABLED,
                finished_at=_now(),
            )
        if str(self.config.get("authorization_status", "UNKNOWN")).upper() != "AUTHORIZED":
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.SKIPPED_UNAUTHORIZED,
                finished_at=_now(),
            )
        if self.transport is None:
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.BLOCKED_POLICY,
                finished_at=_now(),
                errors=["authorized source has no injected transport"],
            )
        try:
            signature = inspect.signature(self.transport)
            payload = self.transport(_timestamp(observed_at)) if signature.parameters else self.transport()
            records, errors = self._normalize_rows(payload, observed_at)
            status = SourceRunStatus.PARTIAL if errors and records else SourceRunStatus.FAILED if errors else SourceRunStatus.SUCCESS
            return self._result(
                started_at=started_at,
                finished_at=_now(),
                records=records,
                errors=errors,
                status=status,
            )
        except Exception as exc:  # transport failures must be visible, never silently bypassed
            return self._result(
                started_at=started_at,
                status=SourceRunStatus.FAILED,
                finished_at=_now(),
                errors=[f"{type(exc).__name__}: {exc}"],
            )


def source_record_metadata(
    row: dict[str, Any],
    *,
    property_id: str,
    field_name: str = "source_row",
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """Return the minimal provenance envelope used by acquisition/enrichment."""

    return {
        "property_id": property_id,
        "field_name": field_name,
        "source_url": row.get("source_url", "UNKNOWN"),
        "source_type": row.get("source_type", SourceType.MANUAL.value),
        "retrieved_at": (retrieved_at or _now()).isoformat(),
        "raw_value": row,
        "normalized_value": row,
    }
