from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from northshore_watchdog.research.models import (
    ApprovedUse,
    CandidateStatus,
    OwnershipType,
    PropertyRecord,
    RedevelopmentScenario,
    ScenarioType,
    ScoreSnapshot,
)


def test_property_record_keeps_unknown_research_fields_as_none_and_exports_flat_fields():
    record = PropertyRecord(
        property_id="property:123",
        address="123 Example Street",
        municipality="City of North Vancouver",
        neighbourhood="Queensbury",
        property_type="detached house",
        pid="012-345-678",
        ownership_type=OwnershipType.UNKNOWN,
        approved_use=ApprovedUse.UNKNOWN,
        asking=1_500_000,
    )

    flat = record.to_flat_dict()

    assert record.frontage_ft is None
    assert record.approved_use is ApprovedUse.UNKNOWN
    assert flat["property_id"] == "property:123"
    assert flat["PID"] == "012-345-678"
    assert flat["frontage_ft"] is None


def test_ownership_enum_covers_title_gate_categories():
    required = {
        "FREEHOLD_NON_STRATA",
        "FREEHOLD_STRATA",
        "LEASEHOLD",
        "PREPAID_LEASE",
        "NON_PREPAID_LEASE",
        "BARE_LAND_STRATA",
        "FRACTIONAL_OWNERSHIP",
        "UNDIVIDED_INTEREST",
        "PARTIAL_INTEREST",
        "COURT_SALE",
        "FORECLOSURE_COURT_ORDERED_SALE",
        "UNKNOWN",
    }

    assert required.issubset({item.value for item in OwnershipType})


def test_candidate_status_and_approved_use_have_required_values():
    assert {"A_STRONG_CANDIDATE", "B_WATCH", "C_OPPORTUNISTIC", "D_REJECT", "NEED_VERIFICATION", "REJECT", "SPECIAL_SITUATION"} <= {
        item.value for item in CandidateStatus
    }
    assert {"SINGLE_DETACHED", "SINGLE_DETACHED_SECONDARY_SUITE", "TWO_FAMILY_DWELLING", "MULTIPLE_DWELLING", "LANEWAY_HOUSE", "STRATA", "NON_CONFORMING_USE", "UNKNOWN"} <= {
        item.value for item in ApprovedUse
    }


def test_strata_risk_fields_and_land_to_product_index_are_explicit():
    record = PropertyRecord(
        property_id="property:strata",
        address="1 Strata Street",
        municipality="City of North Vancouver",
        strata_fee=0,
        strata_form_b_status="UNKNOWN",
        strata_bylaws_status="UNKNOWN",
        strata_insurance_status="UNKNOWN",
        strata_crf=0,
        common_property="UNKNOWN",
        limited_common_property="UNKNOWN",
        unit_entitlement="UNKNOWN",
        repair_responsibility="UNKNOWN",
        land_to_product_score=55,
    )

    assert record.strata_fee == 0
    assert record.land_to_product_score == 55


def test_scenario_supports_keep_duplex_multiplex_character_and_toa():
    scenarios = [
        RedevelopmentScenario(property_id="property:123", scenario_type=scenario)
        for scenario in ScenarioType
    ]

    assert {scenario.scenario_type for scenario in scenarios} >= {
        ScenarioType.KEEP_EXISTING_HOUSE,
        ScenarioType.DUPLEX,
        ScenarioType.MAX_REALISTIC_MULTIPLEX,
        ScenarioType.CHARACTER_HOUSE_INFILL,
        ScenarioType.TOA_OR_ASSEMBLY,
    }


def test_score_snapshot_preserves_decimal_scores_and_reproducibility_metadata():
    snapshot = ScoreSnapshot(
        property_id="property:123",
        generated_at=datetime.now(timezone.utc),
        scoring_version="research-v1",
        config_hash="abc123",
        total_score=81.6,
        live_score=92.4,
        land_score=86.1,
        investment_score=75.8,
        land_to_product_score=86.1,
        data_confidence_score=84,
        data_confidence_band="Medium",
    )

    assert snapshot.total_score == pytest.approx(81.6)
    assert snapshot.config_hash == "abc123"
    assert snapshot.scoring_version == "research-v1"


def test_invalid_score_snapshot_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        ScoreSnapshot(
            property_id="property:123",
            scoring_version="research-v1",
            config_hash="abc123",
            total_score=101,
            live_score=50,
            land_score=50,
            investment_score=50,
            data_confidence_score=50,
            data_confidence_band="Needs Verification",
        )
