from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from .models import CandidateStatus, OwnershipType, PropertyRecord


class StateResolution(BaseModel):
    """The candidate state plus the separate operational recommendation."""

    model_config = ConfigDict(use_enum_values=False)

    state: CandidateStatus
    reason_codes: list[str] = Field(default_factory=list)
    recommendation: str
    site_visit_eligible: bool
    listing_active: bool = True
    hard_veto: bool = False
    special_situation: bool = False


def _normalise(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_true(value: object) -> bool:
    if value is True:
        return True
    return _normalise(value) in {"true", "yes", "y", "1", "confirmed"}


def _contains_any(values: Iterable[object], phrases: Iterable[str]) -> bool:
    haystack = " ".join(_normalise(value) for value in values)
    return any(phrase in haystack for phrase in phrases)


def _major_road_discount_reason_codes(
    record: PropertyRecord,
    thresholds: dict[str, float],
) -> list[str]:
    """Apply the final conditional major-road discount gate.

    Major-road exposure remains a scored permanent defect.  It becomes a
    candidate filter only when the exposure is within the configured distance
    and the observed market-value discount is known to be insufficient.
    """

    road_class = _normalise(getattr(record, "road_class", None)).upper()
    fronts_major_road = getattr(record, "property_fronts_major_road", None) is True
    major_road = fronts_major_road or road_class in {"A", "B", "C"}
    if not major_road:
        return []

    distance_threshold = float(thresholds.get("major_road_distance_threshold_m", 50))
    if fronts_major_road:
        exposed_within_threshold = True
    else:
        distance = getattr(record, "distance_to_major_road_m", None)
        if distance is None:
            return ["MAJOR_ROAD_DISTANCE_REQUIRES_VERIFICATION"]
        exposed_within_threshold = float(distance) <= distance_threshold
    if not exposed_within_threshold:
        return []

    discount_threshold = float(thresholds.get("major_road_discount_threshold", 0.15))
    dislocation = record.price_dislocation_pct
    if dislocation is None and record.asking is not None and record.expected_market_value:
        dislocation = float(record.asking) / float(record.expected_market_value) - 1
    if dislocation is None:
        return ["MAJOR_ROAD_DISCOUNT_REQUIRES_MARKET_VALUE"]
    if -float(dislocation) <= discount_threshold:
        return ["MAJOR_ROAD_DISCOUNT_INSUFFICIENT"]
    return []


def _is_non_active_status(status: object) -> bool:
    value = _normalise(status)
    if not value:
        return False
    return any(
        marker in value
        for marker in (
            "sold",
            "removed",
            "terminated",
            "expired",
            "withdrawn",
            "cancelled",
            "canceled",
            "off_market",
            "not_active",
        )
    )


def _hard_veto_reason_codes(record: PropertyRecord, supplied: Iterable[str]) -> list[str]:
    reasons = [str(reason).upper() for reason in supplied if str(reason).strip()]

    ownership = record.ownership_type
    if ownership is OwnershipType.NON_PREPAID_LEASE:
        reasons.append("NON_PREPAID_LEASE")
    elif ownership is OwnershipType.LEASEHOLD and "non_prepaid" in _normalise(record.title_status):
        reasons.append("NON_PREPAID_LEASE")

    if _is_true(getattr(record, "major_contamination", None)):
        reasons.append("MAJOR_CONTAMINATION")
    if _is_true(getattr(record, "severe_foundation_problem", None)):
        reasons.append("SEVERE_FOUNDATION_PROBLEM")
    if getattr(record, "vacant_possession_possible", None) is False:
        reasons.append("VACANT_POSSESSION_IMPOSSIBLE")
    if _is_true(getattr(record, "redevelopment_legal_blocked", None)):
        reasons.append("REDEVELOPMENT_LEGAL_BLOCKED")
    if _is_true(getattr(record, "severe_uncompensated_permanent_defect", None)):
        reasons.append("SEVERE_UNCOMPENSATED_PERMANENT_DEFECT")
    if _is_true(getattr(record, "major_data_conflict", None)):
        reasons.append("MAJOR_DATA_CONFLICT")

    defects = getattr(record, "permanent_defects", [])
    if _contains_any(
        defects,
        (
            "severe_contamination",
            "major_contamination",
            "severe_foundation",
            "major_foundation",
            "vacant_possession_impossible",
            "redevelopment_legally_blocked",
            "severe_uncompensated",
        ),
    ):
        reasons.append("PERMANENT_DEFECT_HARD_VETO")

    return list(dict.fromkeys(reasons))


def _special_reason_codes(record: PropertyRecord, supplied: Iterable[str]) -> list[str]:
    reasons = [str(reason).upper() for reason in supplied if str(reason).strip()]
    ownership = record.ownership_type
    unverified_claim = _normalise(getattr(record, "unverified_ownership_claim", None))
    if any(
        marker in unverified_claim
        for marker in (
            "leasehold",
            "prepaid_lease",
            "non_prepaid_lease",
            "bare_land_strata",
            "fractional",
            "undivided_interest",
            "partial_interest",
            "court_sale",
            "foreclosure",
            "cooperative",
            "co_operative",
            "corporate_share",
        )
    ):
        reasons.append("UNVERIFIED_OWNERSHIP_CLAIM")

    if ownership is OwnershipType.PARTIAL_INTEREST:
        reasons.append("PARTIAL_INTEREST")
    if ownership is OwnershipType.UNDIVIDED_INTEREST:
        reasons.append("UNDIVIDED_INTEREST")
    if ownership is OwnershipType.FRACTIONAL_OWNERSHIP:
        reasons.append("FRACTIONAL_OWNERSHIP")
    if ownership is OwnershipType.COURT_SALE:
        reasons.append("COURT_SALE")
    if ownership is OwnershipType.FORECLOSURE_COURT_ORDERED_SALE:
        reasons.append("FORECLOSURE_COURT_ORDERED_SALE")
    if ownership is OwnershipType.LEASEHOLD and "non_prepaid" not in _normalise(record.title_status):
        reasons.append("LEASEHOLD_REVIEW")
    if ownership in {OwnershipType.PREPAID_LEASE, OwnershipType.BARE_LAND_STRATA}:
        reasons.append(ownership.value)
    if ownership is OwnershipType.COOPERATIVE_SHARE:
        reasons.append("COOPERATIVE_SHARE")

    title_status = _normalise(record.title_status)
    if title_status in {"unclear", "title_unclear", "unknown", "pending", "title_defect"}:
        reasons.append("TITLE_UNCLEAR")
    if _is_true(getattr(record, "serious_litigation", None)):
        reasons.append("SERIOUS_LITIGATION")
    if _is_true(getattr(record, "serious_unresolved_strata_issue", None)):
        reasons.append("SERIOUS_UNRESOLVED_STRATA_ISSUE")
    if _is_true(getattr(record, "data_conflict", None)):
        reasons.append("DATA_CONFLICT")
    if record.transaction_history_conflict:
        reasons.append("TRANSACTION_HISTORY_CONFLICT")

    asset_class = str(getattr(record, "research_asset_class", "UNKNOWN") or "UNKNOWN").upper()
    if asset_class == "DUPLEX":
        reasons.append("SUPPLEMENTARY_DUPLEX_NOT_PRIMARY_HOUSE")
    elif asset_class not in {"UNKNOWN", "DETACHED_HOUSE"}:
        reasons.append("ASSET_CLASS_OUT_OF_PRIMARY_SCOPE")
    if str(getattr(record, "research_scope", "UNKNOWN") or "UNKNOWN").upper() == "EXCLUDED_STEEP_LOT":
        reasons.append("STEEP_LOT_OUT_OF_CURRENT_SCOPE")

    return list(dict.fromkeys(reasons))


def _recommendation_for_state(state: CandidateStatus) -> str:
    return {
        CandidateStatus.A_STRONG_CANDIDATE: "SITE_VISIT",
        CandidateStatus.B_WATCH: "WATCHLIST_AND_VERIFY",
        CandidateStatus.C_OPPORTUNISTIC: "ONLY_IF_PRICE_IMPROVES",
        CandidateStatus.D_REJECT: "REJECT",
        CandidateStatus.REJECT: "REJECT",
        CandidateStatus.NEED_VERIFICATION: "VERIFY_BEFORE_RANKING",
        CandidateStatus.SPECIAL_SITUATION: "SPECIAL_SITUATION_REVIEW",
    }[state]


def resolve_candidate_state(
    record: PropertyRecord,
    *,
    total_score: float | None = None,
    data_confidence_score: float | None = None,
    manual_verification_pending: bool = False,
    hard_vetoes: Iterable[str] = (),
    special_situations: Iterable[str] = (),
    thresholds: dict[str, float] | None = None,
) -> StateResolution:
    """Resolve operational candidate state with vetoes before score bands.

    A sold or removed record keeps its historical score state, but cannot be
    returned as an active site-visit recommendation.
    """

    hard_reasons = _hard_veto_reason_codes(record, hard_vetoes)
    if hard_reasons:
        return StateResolution(
            state=CandidateStatus.REJECT,
            reason_codes=hard_reasons,
            recommendation="REJECT",
            site_visit_eligible=False,
            listing_active=False if _is_non_active_status(record.listing_status) else True,
            hard_veto=True,
        )

    special_reasons = _special_reason_codes(record, special_situations)
    if special_reasons:
        return StateResolution(
            state=CandidateStatus.SPECIAL_SITUATION,
            reason_codes=special_reasons,
            recommendation="SPECIAL_SITUATION_REVIEW",
            site_visit_eligible=False,
            listing_active=not _is_non_active_status(record.listing_status),
            special_situation=True,
        )

    thresholds = thresholds or {}
    a_min = float(thresholds.get("a_min", 80))
    b_min = float(thresholds.get("b_min", 70))
    c_min = float(thresholds.get("c_min", 60))
    confidence_gate = float(thresholds.get("confidence_gate", 80))
    needs_verification = float(thresholds.get("needs_verification", 70))
    score = total_score if total_score is not None else record.total_score
    confidence = (
        data_confidence_score
        if data_confidence_score is not None
        else record.data_confidence_score
    )
    listing_active = not _is_non_active_status(record.listing_status)
    reason_codes: list[str] = []

    # Ownership is the first acquisition gate.  Direct callers must receive
    # the same safe state as the full weekly pipeline, even when a synthetic
    # score/confidence pair looks strong.
    if record.ownership_type is OwnershipType.UNKNOWN:
        return StateResolution(
            state=CandidateStatus.NEED_VERIFICATION,
            reason_codes=["OWNERSHIP_UNKNOWN"],
            recommendation="VERIFY_BEFORE_RANKING",
            site_visit_eligible=False,
            listing_active=listing_active,
        )

    if getattr(record, "manual_verification_required", False) or manual_verification_pending:
        return StateResolution(
            state=CandidateStatus.NEED_VERIFICATION,
            reason_codes=["MANUAL_VERIFICATION_REQUIRED"],
            recommendation="VERIFY_BEFORE_RANKING",
            site_visit_eligible=False,
            listing_active=listing_active,
        )

    road_reasons = _major_road_discount_reason_codes(record, thresholds)
    if "MAJOR_ROAD_DISCOUNT_INSUFFICIENT" in road_reasons:
        return StateResolution(
            state=CandidateStatus.D_REJECT,
            reason_codes=road_reasons,
            recommendation="REJECT",
            site_visit_eligible=False,
            listing_active=listing_active,
        )
    if road_reasons:
        return StateResolution(
            state=CandidateStatus.NEED_VERIFICATION,
            reason_codes=road_reasons,
            recommendation="VERIFY_BEFORE_RANKING",
            site_visit_eligible=False,
            listing_active=listing_active,
        )

    if score is None:
        state = CandidateStatus.NEED_VERIFICATION
        reason_codes.append("TOTAL_SCORE_UNKNOWN")
    elif score < c_min:
        state = CandidateStatus.D_REJECT
        reason_codes.append(f"TOTAL_SCORE_BELOW_{int(c_min)}")
    elif confidence is None:
        state = CandidateStatus.NEED_VERIFICATION
        reason_codes.append("DATA_CONFIDENCE_UNKNOWN")
    elif score >= c_min and confidence < needs_verification:
        state = CandidateStatus.NEED_VERIFICATION
        reason_codes.append(f"CONFIDENCE_BELOW_{int(needs_verification)}")
    elif score >= a_min and confidence < confidence_gate:
        state = CandidateStatus.NEED_VERIFICATION
        reason_codes.append(f"HIGH_SCORE_CONFIDENCE_BELOW_{int(confidence_gate)}")
    elif score >= a_min:
        state = CandidateStatus.A_STRONG_CANDIDATE
    elif score >= b_min:
        state = CandidateStatus.B_WATCH
    else:
        state = CandidateStatus.C_OPPORTUNISTIC

    if not listing_active:
        reason_codes.append("SOLD_OR_REMOVED")

    site_visit_eligible = listing_active and state in {
        CandidateStatus.A_STRONG_CANDIDATE,
        CandidateStatus.B_WATCH,
    }
    return StateResolution(
        state=state,
        reason_codes=list(dict.fromkeys(reason_codes)),
        recommendation=_recommendation_for_state(state),
        site_visit_eligible=site_visit_eligible,
        listing_active=listing_active,
    )
