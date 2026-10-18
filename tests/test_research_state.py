from northshore_watchdog.research.models import (
    CandidateStatus,
    OwnershipType,
    PropertyRecord,
)
from northshore_watchdog.research.state import StateResolution, resolve_candidate_state


def _record(**overrides) -> PropertyRecord:
    values = {
        "property_id": "state-case",
        "address": "123 Research Street",
        "municipality": "Vancouver",
        "neighbourhood": "Kensington",
        "ownership_type": OwnershipType.FREEHOLD_NON_STRATA,
        "listing_status": "Active",
    }
    values.update(overrides)
    return PropertyRecord(**values)


def test_hard_vetoes_resolve_to_reject():
    veto_cases = [
        {"ownership_type": OwnershipType.NON_PREPAID_LEASE},
        {"major_contamination": True},
        {"severe_foundation_problem": True},
        {"vacant_possession_possible": False},
        {"redevelopment_legal_blocked": True},
        {"severe_uncompensated_permanent_defect": True},
    ]

    for overrides in veto_cases:
        result = resolve_candidate_state(_record(**overrides), total_score=90, data_confidence_score=95)
        assert result.state is CandidateStatus.REJECT
        assert result.site_visit_eligible is False
        assert result.reason_codes


def test_special_situations_are_separated_from_normal_ranking():
    special_cases = [
        {"ownership_type": OwnershipType.PARTIAL_INTEREST},
        {"ownership_type": OwnershipType.UNDIVIDED_INTEREST},
        {"ownership_type": OwnershipType.COURT_SALE},
        {"ownership_type": OwnershipType.FORECLOSURE_COURT_ORDERED_SALE},
        {"title_status": "UNCLEAR"},
        {"serious_litigation": True},
        {"serious_unresolved_strata_issue": True},
        {"data_conflict": True},
    ]

    for overrides in special_cases:
        result = resolve_candidate_state(_record(**overrides), total_score=90, data_confidence_score=95)
        assert result.state is CandidateStatus.SPECIAL_SITUATION
        assert result.site_visit_eligible is False
        assert result.recommendation


def test_confidence_gate_precedes_score_band():
    low_confidence = resolve_candidate_state(_record(), total_score=75, data_confidence_score=69)
    high_score_medium_confidence = resolve_candidate_state(_record(), total_score=90, data_confidence_score=75)

    assert low_confidence.state is CandidateStatus.NEED_VERIFICATION
    assert high_score_medium_confidence.state is CandidateStatus.NEED_VERIFICATION
    assert high_score_medium_confidence.site_visit_eligible is False


def test_provisional_single_source_record_cannot_become_a_candidate_before_manual_verification():
    result = resolve_candidate_state(
        _record(
            manual_verification_required=True,
            data_verification_status="PROVISIONAL_SINGLE_SOURCE",
        ),
        total_score=95,
        data_confidence_score=95,
    )

    assert result.state is CandidateStatus.NEED_VERIFICATION
    assert "MANUAL_VERIFICATION_REQUIRED" in result.reason_codes
    assert result.site_visit_eligible is False


def test_open_manual_queue_cannot_be_cleared_by_a_fresh_record():
    result = resolve_candidate_state(
        _record(),
        total_score=95,
        data_confidence_score=95,
        manual_verification_pending=True,
    )

    assert result.state is CandidateStatus.NEED_VERIFICATION
    assert "MANUAL_VERIFICATION_REQUIRED" in result.reason_codes
    assert result.site_visit_eligible is False


def test_unknown_ownership_is_always_need_verification_even_for_high_scores():
    result = resolve_candidate_state(
        _record(ownership_type=OwnershipType.UNKNOWN),
        total_score=95,
        data_confidence_score=95,
    )

    assert result.state is CandidateStatus.NEED_VERIFICATION
    assert "OWNERSHIP_UNKNOWN" in result.reason_codes
    assert result.site_visit_eligible is False


def test_major_road_with_insufficient_market_discount_is_rejected_by_final_policy():
    result = resolve_candidate_state(
        _record(
            road_class="B",
            distance_to_major_road_m=40,
            expected_market_value=1_500_000,
            asking=1_400_000,
        ),
        total_score=85,
        data_confidence_score=85,
        thresholds={
            "major_road_discount_threshold": 0.15,
            "major_road_distance_threshold_m": 50,
        },
    )

    assert result.state is CandidateStatus.D_REJECT
    assert "MAJOR_ROAD_DISCOUNT_INSUFFICIENT" in result.reason_codes


def test_major_road_is_not_rejected_when_discount_exceeds_final_policy():
    result = resolve_candidate_state(
        _record(
            road_class="B",
            distance_to_major_road_m=40,
            expected_market_value=1_700_000,
            asking=1_400_000,
        ),
        total_score=75,
        data_confidence_score=85,
        thresholds={
            "major_road_discount_threshold": 0.15,
            "major_road_distance_threshold_m": 50,
        },
    )

    assert result.state is CandidateStatus.B_WATCH


def test_major_road_without_comparable_value_waits_for_manual_discount_check():
    result = resolve_candidate_state(
        _record(
            road_class="B",
            distance_to_major_road_m=40,
        ),
        total_score=85,
        data_confidence_score=85,
        thresholds={
            "major_road_discount_threshold": 0.15,
            "major_road_distance_threshold_m": 50,
        },
    )

    assert result.state is CandidateStatus.NEED_VERIFICATION
    assert "MAJOR_ROAD_DISCOUNT_REQUIRES_MARKET_VALUE" in result.reason_codes


def test_score_bands_resolve_to_expected_states():
    cases = [
        (90, 80, CandidateStatus.A_STRONG_CANDIDATE),
        (79, 80, CandidateStatus.B_WATCH),
        (70, 70, CandidateStatus.B_WATCH),
        (69, 70, CandidateStatus.C_OPPORTUNISTIC),
        (60, 70, CandidateStatus.C_OPPORTUNISTIC),
        (59, 95, CandidateStatus.D_REJECT),
    ]

    for total_score, confidence, expected in cases:
        result = resolve_candidate_state(_record(), total_score=total_score, data_confidence_score=confidence)
        assert result.state is expected
        assert result.site_visit_eligible is (expected in {CandidateStatus.A_STRONG_CANDIDATE, CandidateStatus.B_WATCH})


def test_sold_or_removed_property_keeps_historical_state_but_is_not_site_visit_candidate():
    result = resolve_candidate_state(
        _record(listing_status="Sold"),
        total_score=90,
        data_confidence_score=95,
    )

    assert result.state is CandidateStatus.A_STRONG_CANDIDATE
    assert result.site_visit_eligible is False
    assert "SOLD_OR_REMOVED" in result.reason_codes


def test_explicit_major_data_conflict_is_a_hard_reject():
    result = resolve_candidate_state(
        _record(data_conflict=True, major_data_conflict=True),
        total_score=90,
        data_confidence_score=95,
    )

    assert result.state is CandidateStatus.REJECT
    assert "MAJOR_DATA_CONFLICT" in result.reason_codes


def test_candidate_thresholds_are_configurable():
    result = resolve_candidate_state(
        _record(),
        total_score=75,
        data_confidence_score=75,
        thresholds={"a_min": 75, "b_min": 65, "c_min": 50, "confidence_gate": 70, "needs_verification": 60},
    )

    assert result.state is CandidateStatus.A_STRONG_CANDIDATE
