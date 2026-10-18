from datetime import datetime, timezone

from northshore_watchdog.research.delta import diff_property_records, source_run_status_event
from northshore_watchdog.research.models import PropertyRecord
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _record(**updates):
    values = {
        "property_id": "pid:DELTA-1",
        "address": "1 Delta Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "asking": 1_500_000,
        "original_asking": 1_600_000,
        "dom": 61,
        "relist_count": 0,
        "listing_status": "Active",
        "assessment_total": 1_550_000,
        "zoning": "R1-1",
        "total_score": 71,
        "data_confidence_score": 72,
    }
    values.update(updates)
    return PropertyRecord(**values)


def test_diff_emits_structured_events_for_meaningful_weekly_changes():
    previous = _record()
    current = _record(
        asking=1_425_000,
        dom=75,
        relist_count=1,
        listing_status="Sold",
        assessment_total=1_600_000,
        zoning="R1-2",
        total_score=82,
        data_confidence_score=85,
    )

    events = diff_property_records(previous, current, observed_at=OBSERVED_AT)
    by_type = {event.event_type: event for event in events}

    assert {
        "price_change",
        "price_reduction",
        "dom_change",
        "relist_change",
        "status_change",
        "assessment_change",
        "zoning_change",
        "score_change",
        "confidence_change",
    } <= set(by_type)
    assert by_type["price_reduction"].payload["amount"] == 75_000
    assert by_type["zoning_change"].payload["new_value"] == "R1-2"


def test_identical_records_do_not_emit_duplicate_delta_events():
    record = _record()

    assert diff_property_records(record, record.model_copy(), observed_at=OBSERVED_AT) == []


def test_first_seen_and_source_run_status_are_explicit_events():
    record = _record()
    first = diff_property_records(None, record, observed_at=OBSERVED_AT)
    source_event = source_run_status_event(
        SourceRunResult(
            source_registry_id="source:fixture",
            source_name="fixture",
            status=SourceRunStatus.PARTIAL,
            started_at=OBSERVED_AT,
            finished_at=OBSERVED_AT,
            errors=["one row failed"],
        ),
        observed_at=OBSERVED_AT,
    )

    assert [event.event_type for event in first] == ["first_seen"]
    assert source_event.event_type == "source_run_status"
    assert source_event.payload["status"] == "PARTIAL"
    assert source_event.property_id == "source:fixture"


def test_approved_use_change_is_a_distinct_delta():
    previous = _record(approved_use="SINGLE_DETACHED")
    current = _record(approved_use="TWO_FAMILY_DWELLING")

    events = diff_property_records(previous, current, observed_at=OBSERVED_AT)

    approved = next(event for event in events if event.event_type == "approved_use_change")
    assert approved.payload["new_value"] == "TWO_FAMILY_DWELLING"


def test_listing_status_events_classify_sold_and_removed_transitions():
    previous = _record(listing_status="Active")
    sold = _record(listing_status="Sold")
    removed = _record(listing_status="REMOVED_OR_UNKNOWN")

    sold_event = next(event for event in diff_property_records(previous, sold, observed_at=OBSERVED_AT) if event.event_type == "status_change")
    removed_event = next(event for event in diff_property_records(previous, removed, observed_at=OBSERVED_AT) if event.event_type == "status_change")

    assert sold_event.payload["status_classification"] == "SOLD"
    assert removed_event.payload["status_classification"] == "REMOVED_OR_UNKNOWN"


def test_incomplete_source_coverage_emits_coverage_unknown_event():
    result = SourceRunResult(
        source_registry_id="source:partial",
        source_name="partial feed",
        status=SourceRunStatus.PARTIAL,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        warnings=["feed omitted inactive records"],
    )

    event = source_run_status_event(result, observed_at=OBSERVED_AT)

    assert event.payload["coverage_classification"] == "COVERAGE_UNKNOWN"


def test_snapshot_source_coverage_is_not_reported_as_a_complete_feed():
    result = SourceRunResult(
        source_registry_id="source:snapshot",
        source_name="public snapshot",
        status=SourceRunStatus.SUCCESS,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        coverage_complete=False,
    )

    event = source_run_status_event(result, observed_at=OBSERVED_AT)

    assert event.payload["coverage_classification"] == "SNAPSHOT"
