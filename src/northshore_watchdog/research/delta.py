from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import PropertyRecord, WatchlistEvent
from .sources import SourceRunResult


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo and value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)


def _json_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _event(
    property_id: str,
    event_type: str,
    observed_at: datetime,
    *,
    weekly_run_id: int | None = None,
    source_run_id: int | None = None,
    source_registry_id: str | None = None,
    **payload: Any,
) -> WatchlistEvent:
    return WatchlistEvent(
        property_id=property_id,
        event_type=event_type,
        created_at=_aware(observed_at),
        weekly_run_id=weekly_run_id,
        source_run_id=source_run_id,
        source_registry_id=source_registry_id,
        payload={key: _json_value(value) for key, value in payload.items()},
    )


def _status_classification(status: Any) -> str:
    value = str(_json_value(status) or "").strip().lower().replace("-", "_").replace(" ", "_")
    if "sold" in value:
        return "SOLD"
    if "removed_or_unknown" in value:
        return "REMOVED_OR_UNKNOWN"
    if any(marker in value for marker in ("terminated", "expired", "withdrawn", "cancelled", "canceled", "off_market")):
        return "TERMINATED_OR_REMOVED"
    return "LISTING_STATUS_CHANGE"


def _changed(previous: PropertyRecord, current: PropertyRecord, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in fields:
        old = _json_value(getattr(previous, field, None))
        new = _json_value(getattr(current, field, None))
        if old != new:
            changes[field] = {"old_value": old, "new_value": new}
    return changes


def diff_property_records(
    previous: PropertyRecord | None,
    current: PropertyRecord,
    *,
    observed_at: datetime,
) -> list[WatchlistEvent]:
    """Return append-only events for a current projection transition."""

    if previous is None:
        return [_event(current.property_id, "first_seen", observed_at, asking=current.asking)]
    events: list[WatchlistEvent] = []
    if previous.asking != current.asking and previous.asking is not None and current.asking is not None:
        events.append(
            _event(
                current.property_id,
                "price_change",
                observed_at,
                old_price=previous.asking,
                new_price=current.asking,
            )
        )
        if current.asking < previous.asking:
            old_price = previous.asking
            amount = old_price - current.asking
            events.append(
                _event(
                    current.property_id,
                    "price_reduction",
                    observed_at,
                    old_price=old_price,
                    new_price=current.asking,
                    amount=amount,
                    percentage=round(current.asking / old_price - 1, 4),
                )
            )
    status_changes = _changed(previous, current, ("listing_status",))
    if status_changes:
        change = next(iter(status_changes.values()))
        events.append(
            _event(
                current.property_id,
                "status_change",
                observed_at,
                fields=status_changes,
                status_classification=_status_classification(change.get("new_value")),
                **change,
            )
        )
    dom_changes = _changed(previous, current, ("dom", "cumulative_dom"))
    if dom_changes:
        events.append(_event(current.property_id, "dom_change", observed_at, fields=dom_changes))
    relist_changes = _changed(previous, current, ("relist_count",))
    if relist_changes:
        events.append(_event(current.property_id, "relist_change", observed_at, fields=relist_changes))
    assessment_changes = _changed(
        previous,
        current,
        ("assessment_year", "assessment_total", "assessment_land", "assessment_improvement"),
    )
    if assessment_changes:
        events.append(_event(current.property_id, "assessment_change", observed_at, fields=assessment_changes))
    zoning_changes = _changed(
        previous,
        current,
        ("zoning", "bylaw_version", "maximum_fsr", "maximum_units", "official_zoning_confirmed"),
    )
    if zoning_changes:
        change = next(iter(zoning_changes.values()))
        events.append(_event(current.property_id, "zoning_change", observed_at, fields=zoning_changes, **change))
    approved_use_changes = _changed(previous, current, ("approved_use",))
    if approved_use_changes:
        change = next(iter(approved_use_changes.values()))
        events.append(
            _event(
                current.property_id,
                "approved_use_change",
                observed_at,
                fields=approved_use_changes,
                **change,
            )
        )
    score_changes = _changed(
        previous,
        current,
        ("total_score", "live_score", "land_score", "investment_score", "land_to_product_score"),
    )
    if score_changes:
        events.append(_event(current.property_id, "score_change", observed_at, fields=score_changes))
    confidence_changes = _changed(previous, current, ("data_confidence_score", "data_confidence_band"))
    if confidence_changes:
        events.append(_event(current.property_id, "confidence_change", observed_at, fields=confidence_changes))
    state_changes = _changed(previous, current, ("candidate_status",))
    if state_changes:
        events.append(_event(current.property_id, "state_change", observed_at, fields=state_changes))
    if previous.data_conflict != current.data_conflict:
        events.append(
            _event(
                current.property_id,
                "conflict_opened" if current.data_conflict else "conflict_resolved",
                observed_at,
                old_value=previous.data_conflict,
                new_value=current.data_conflict,
            )
        )
    return events


def source_run_status_event(
    result: SourceRunResult,
    *,
    observed_at: datetime,
    property_id: str | None = None,
    weekly_run_id: int | None = None,
    source_run_id: int | None = None,
) -> WatchlistEvent:
    event = _event(
        property_id or result.source_registry_id,
        "source_run_status",
        observed_at,
        source_name=result.source_name,
        source_role=result.source_role,
        priority=result.priority,
        status=result.status.value,
        record_count=len(result.records),
        errors=result.errors,
        warnings=result.warnings,
        coverage_classification=(
            "COMPLETE"
            if result.status.value == "SUCCESS" and result.coverage_complete is True
            else "SNAPSHOT"
            if result.status.value == "SUCCESS" and result.coverage_complete is False
            else "COVERAGE_UNKNOWN"
        ),
        weekly_run_id=weekly_run_id,
        source_run_id=source_run_id,
        source_registry_id=result.source_registry_id,
    )
    event.payload["source_registry_id"] = result.source_registry_id
    return event
