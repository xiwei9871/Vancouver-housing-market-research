from collections import Counter

import pytest

from datetime import datetime, timezone

from northshore_watchdog.research.models import CaseQuality, SourceType
from northshore_watchdog.research.provenance import SourceRecord
from northshore_watchdog.research.validation import (
    _build_evidence,
    build_validation_input,
    classify_case_quality,
    load_case_manifest,
    load_case_payload,
)


def test_phase1_manifest_separates_traceable_cases_fixtures_and_coverage_gaps():
    manifest_path = "data/research/cases/phase1_cases.yaml"
    manifest = load_case_manifest(manifest_path)
    cases = manifest["cases"]
    counts = Counter(classify_case_quality(case) for case in cases)

    assert counts[CaseQuality.TRACEABLE_RESEARCH] >= 5
    assert counts[CaseQuality.BOUNDARY_FIXTURE] >= 1
    assert manifest["coverage_gaps"]
    assert any("TOA" in gap["category"] for gap in manifest["coverage_gaps"])

    excluded_synthetic_addresses = {
        "1000 Sample Ambleside Avenue",
        "321 Queensbury Avenue",
        "88 Leasehold Lane",
    }
    traceable_addresses = {
        load_case_payload(case, manifest_path=manifest_path).get("address")
        for case in cases
        if classify_case_quality(case) is CaseQuality.TRACEABLE_RESEARCH
    }
    assert not excluded_synthetic_addresses & traceable_addresses


def test_each_case_has_provenance_expected_behavior_and_verification_notes():
    manifest_path = "data/research/cases/phase1_cases.yaml"
    manifest = load_case_manifest(manifest_path)

    for case in manifest["cases"]:
        if classify_case_quality(case) is CaseQuality.COVERAGE_GAP:
            continue
        assert case["source_references"]
        assert case["expected_behavior"]
        assert case["verification_notes"]
        payload = load_case_payload(case, manifest_path=manifest_path)
        validation_input = build_validation_input(case, payload)
        assert validation_input["case_id"] == case["case_id"]
        assert validation_input["source_references"]


def test_manifest_rejects_cases_without_source_metadata(tmp_path):
    manifest_path = tmp_path / "bad.yaml"
    manifest_path.write_text(
        """
schema_version: research-v1
cases:
  - case_id: bad-case
    quality: TRACEABLE_RESEARCH
    payload_path: bad.json
    expected_behavior: [must fail]
    verification_notes: missing source refs
coverage_gaps: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_references"):
        load_case_manifest(manifest_path)


def test_boundary_fixture_quality_is_not_promoted_by_plausible_fields():
    fixture = {
        "case_id": "sample-leasehold",
        "quality": "BOUNDARY_FIXTURE",
        "payload_path": "fixture.json",
        "source_references": [
            {
                "source_url": "local://data/samples/listings_sample.csv",
                "source_type": "MANUAL",
                "observed_at": "2026-08-29T00:00:00+00:00",
            }
        ],
        "expected_behavior": ["SPECIAL_SITUATION"],
        "verification_notes": "Synthetic boundary fixture.",
        "payload": {"address": "88 Leasehold Lane", "asking": 1000000},
    }

    assert classify_case_quality(fixture) is CaseQuality.BOUNDARY_FIXTURE
    validation_input = build_validation_input(fixture, fixture["payload"])
    assert validation_input["quality"] is CaseQuality.BOUNDARY_FIXTURE


def test_confidence_requires_field_specific_official_zoning_and_use_sources():
    observed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    sources = [
        SourceRecord(
            property_id="property:1",
            field_name="zoning",
            source_url="local://listing-copy",
            source_type=SourceType.MLS,
            retrieved_at=observed_at,
            normalized_value="R1-1",
        ),
        SourceRecord(
            property_id="property:1",
            field_name="road_class",
            source_url="https://city.example/road",
            source_type=SourceType.OFFICIAL_GOV,
            retrieved_at=observed_at,
            normalized_value="B",
        ),
    ]

    evidence = {item.name: item for item in _build_evidence(
        {"zoning": "R1-1", "approved_use": "SINGLE_DETACHED"},
        sources,
    )}

    assert evidence["official_zoning"].confirmed is False
    assert evidence["approved_use_confirmed"].confirmed is False


def test_listing_pid_and_ownership_are_not_officially_confirmed_by_unrelated_gov_source():
    observed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    sources = [
        SourceRecord(
            property_id="property:1",
            field_name="pid",
            source_url="local://listing",
            source_type=SourceType.MLS,
            retrieved_at=observed_at,
            normalized_value="PID-LISTING",
        ),
        SourceRecord(
            property_id="property:1",
            field_name="ownership_type",
            source_url="local://listing",
            source_type=SourceType.MLS,
            retrieved_at=observed_at,
            normalized_value="FREEHOLD_NON_STRATA",
        ),
        SourceRecord(
            property_id="property:1",
            field_name="zoning",
            source_url="local://official-zoning",
            source_type=SourceType.OFFICIAL_GOV,
            retrieved_at=observed_at,
            normalized_value="R1-1",
        ),
    ]

    evidence = {item.name: item for item in _build_evidence(
        {"pid": "PID-LISTING", "ownership_type": "FREEHOLD_NON_STRATA"},
        sources,
    )}

    assert evidence["official_pid_confirmed"].confirmed is False
    assert evidence["ownership_confirmed"].confirmed is False


def test_listing_history_confidence_requires_a_listing_field_source():
    observed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    sources = [
        SourceRecord(
            property_id="property:1",
            field_name="zoning",
            source_url="local://official-zoning",
            source_type=SourceType.OFFICIAL_GOV,
            retrieved_at=observed_at,
            normalized_value="R1-1",
        ),
        SourceRecord(
            property_id="property:1",
            field_name="sold_history",
            source_url="local://sold-history",
            source_type=SourceType.MLS,
            retrieved_at=observed_at,
            normalized_value=1_400_000,
        ),
    ]

    evidence = {item.name: item for item in _build_evidence(
        {"asking": 1_500_000},
        sources,
    )}

    assert evidence["listing_history_confirmed"].confirmed is False


def test_sold_history_confidence_requires_independent_source_registries():
    observed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    sources = [
        SourceRecord(
            property_id="property:1",
            field_name="sold_history",
            source_url="local://sold/one",
            source_type=SourceType.MLS,
            source_registry_id="source:one-feed",
            retrieved_at=observed_at,
            normalized_value=1_400_000,
        ),
        SourceRecord(
            property_id="property:1",
            field_name="sold_history",
            source_url="local://sold/two",
            source_type=SourceType.MLS,
            source_registry_id="source:one-feed",
            retrieved_at=observed_at,
            normalized_value=1_450_000,
        ),
    ]

    evidence = {item.name: item for item in _build_evidence(
        {"previous_sale_prices": [1_400_000, 1_450_000]},
        sources,
    )}

    assert evidence["sold_history_cross_checked"].confirmed is False
