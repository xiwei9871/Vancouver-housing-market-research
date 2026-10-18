from pathlib import Path

import yaml

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import CandidateStatus, EvidenceStatus
from northshore_watchdog.research.scoring import score_property
from northshore_watchdog.research.state import resolve_candidate_state
from northshore_watchdog.research.validation import run_phase1_validation


def _config(tmp_path: Path, manifest_path: str | Path = "data/research/cases/phase1_cases.yaml") -> dict:
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["phase1_dir"] = str(tmp_path / "phase1")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    config["cases"]["manifest_path"] = str(manifest_path)
    return config


def test_local_cases_flow_to_report_and_answer_the_core_decision_questions(tmp_path):
    result = run_phase1_validation(_config(tmp_path))
    by_address = {record.address: record for record in result.records}

    haywood = by_address["1379 Haywood Avenue"]
    ross = by_address["1494 Ross Road"]
    assert haywood.assessment_labels
    assert "ASK_BELOW_ASSESSMENT" in haywood.assessment_labels
    assert ross.ownership_type.value == "BARE_LAND_STRATA"
    assert result.resolutions[ross.property_id].state is CandidateStatus.SPECIAL_SITUATION
    assert result.report_path and result.report_path.exists()
    report = result.report_path.read_text(encoding="utf-8")

    for phrase in (
        "Investment Thesis",
        "apparent discount",
        "Permanent Defects",
        "buildability",
        "5–10 year use",
        "future product",
        "Price Dislocation",
        "coverage_gaps",
    ):
        assert phrase in report


def test_conflicting_provenance_enters_queue_and_conflicting_value_is_not_scored(tmp_path):
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    payload_path = case_dir / "conflict.json"
    payload_path.write_text(
        '{"property_id":"case:conflict","address":"500 Conflict Avenue","municipality":"City of Vancouver",'
        '"neighbourhood":"Knight","property_type":"detached house","listing_status":"Active",'
        '"asking":1500000,"lot_area_sqft":5000,"frontage_ft":33,"depth_ft":120}',
        encoding="utf-8",
    )
    manifest_path = case_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "research-v1",
                "cases": [
                    {
                        "case_id": "conflict",
                        "quality": "TRACEABLE_RESEARCH",
                        "payload_path": "conflict.json",
                        "source_references": [
                            {
                                "source_url": "local://official",
                                "source_type": "OFFICIAL_GOV",
                                "observed_at": "2026-08-29T00:00:00+00:00",
                                "field_name": "frontage_ft",
                                "normalized_value": 33,
                            },
                            {
                                "source_url": "local://aggregator",
                                "source_type": "AGGREGATOR",
                                "observed_at": "2026-08-29T00:00:00+00:00",
                                "field_name": "frontage_ft",
                                "normalized_value": 40,
                            },
                        ],
                        "expected_behavior": ["material conflict requires verification"],
                        "verification_notes": "Two local values disagree.",
                    }
                ],
                "coverage_gaps": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = run_phase1_validation(_config(tmp_path, manifest_path))
    assert result.conflicts
    assert result.conflicts[0].field_name == "frontage_ft"
    snapshot = next(iter(result.snapshots.values()))
    component = snapshot.dimensions["land_lot_title"].components["frontage_depth"]
    assert component.evidence_status is EvidenceStatus.CONFLICT
    assert snapshot.candidate_status is CandidateStatus.SPECIAL_SITUATION


def test_high_score_with_low_confidence_is_never_promoted_to_a():
    from northshore_watchdog.research.models import PropertyRecord

    record = PropertyRecord(
        property_id="case:low-confidence",
        address="501 Verification Avenue",
        municipality="City of Vancouver",
        listing_status="Active",
    )
    result = resolve_candidate_state(record, total_score=91, data_confidence_score=68)

    assert result.state is CandidateStatus.NEED_VERIFICATION
    assert result.site_visit_eligible is False
