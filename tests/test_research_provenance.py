from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from northshore_watchdog.research.models import EvidenceStatus, SourceType
from northshore_watchdog.research.provenance import (
    CONFIDENCE_WEIGHTS,
    DataConflict,
    EvidenceItem,
    SourceRecord,
    calculate_data_confidence,
    confidence_band,
    default_evidence_items,
)


def test_source_record_preserves_raw_normalized_values_and_retrieval_metadata():
    record = SourceRecord(
        property_id="property:123",
        field_name="pid",
        source_url="https://example.gov/property/123",
        source_type=SourceType.OFFICIAL_GOV,
        source_registry_id="source:official-title",
        retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        raw_value=" PID 012-345-678 ",
        normalized_value="012-345-678",
        source_version="2026-08",
        evidence_status=EvidenceStatus.CONFIRMED,
        notes="Land title lookup.",
    )

    assert record.raw_value == " PID 012-345-678 "
    assert record.normalized_value == "012-345-678"
    assert record.source_type is SourceType.OFFICIAL_GOV
    assert record.source_registry_id == "source:official-title"
    assert record.retrieved_at.tzinfo is not None


def test_source_record_rejects_unknown_source_type():
    with pytest.raises(ValidationError):
        SourceRecord(
            property_id="property:123",
            field_name="pid",
            source_url="https://example.com",
            source_type="UNKNOWN_PROVIDER",
            retrieved_at=datetime.now(timezone.utc),
            raw_value="x",
            normalized_value="x",
        )


def test_default_confidence_weights_total_one_hundred_points():
    assert sum(CONFIDENCE_WEIGHTS.values()) == 100
    items = default_evidence_items()

    assert len(items) == 10
    assert sum(item.maximum_points for item in items) == 100


def test_confidence_counts_confirmed_evidence_but_not_unresolved_conflicts():
    items = default_evidence_items(
        confirmed={"official_pid_confirmed", "ownership_confirmed", "official_assessment"},
        conflicts={"official_pid_confirmed"},
    )

    result = calculate_data_confidence(items)

    assert result.score == 25
    assert result.band == "Needs Verification"
    assert result.unresolved_conflicts == ["official_pid_confirmed"]


def test_confidence_bands_follow_configured_boundaries():
    assert confidence_band(100) == "High"
    assert confidence_band(85) == "High"
    assert confidence_band(84.9) == "Medium"
    assert confidence_band(70) == "Medium"
    assert confidence_band(69.9) == "Needs Verification"


def test_confidence_band_can_use_configured_thresholds():
    assert confidence_band(80, high_threshold=80, medium_threshold=60) == "High"
    assert confidence_band(65, high_threshold=80, medium_threshold=60) == "Medium"
    assert confidence_band(55, high_threshold=80, medium_threshold=60) == "Needs Verification"


def test_material_conflict_preserves_sources_and_resolution_state():
    conflict = DataConflict(
        conflict_id="conflict:1",
        property_id="property:123",
        field_name="pid",
        conflict_type="PID_MISMATCH",
        source_values={"official": "012", "aggregator": "999"},
        conflicting_sources=["official", "aggregator"],
    )

    assert conflict.resolution_status == "OPEN"
    assert conflict.conflicting_sources == ["official", "aggregator"]
