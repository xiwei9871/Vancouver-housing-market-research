from northshore_watchdog.research.strategy_v2_analysis_router import (
    apply_visual_selection,
    classify_visual_eligibility,
)


def _candidate(**overrides):
    value = {
        "property_key": "mls:R1",
        "primary_track": "HOME_REBUILD",
        "lot_area_sqft": 4026,
        "bedrooms": 3,
        "bathrooms": 2,
        "photo_count": 12,
        "unique_photo_count": 10,
        "description": "quiet residential home",
    }
    value.update(overrides)
    return value


def test_core_home_candidate_is_full_visual_eligible():
    decision = classify_visual_eligibility(_candidate())

    assert decision.visual_eligibility == "FULL_POOL"
    assert decision.home_fit == "PASS"
    assert decision.analysis_route == "STRUCTURED_ONLY"
    assert "HOME_BASELINE_PASS" in decision.flags


def test_three_to_five_photos_are_representative_visual_eligible():
    decision = classify_visual_eligibility(
        _candidate(photo_count=5, unique_photo_count=5)
    )

    assert decision.visual_eligibility == "REPRESENTATIVE_POOL"
    assert decision.analysis_route == "STRUCTURED_ONLY"
    assert "PHOTO_COUNT_BELOW_FULL_THRESHOLD" in decision.flags


def test_fewer_than_three_photos_are_structured_only():
    decision = classify_visual_eligibility(
        _candidate(photo_count=2, unique_photo_count=2)
    )

    assert decision.visual_eligibility == "STRUCTURED_ONLY"
    assert decision.analysis_route == "STRUCTURED_ONLY"
    assert decision.visual_budget_images == 0
    assert "INSUFFICIENT_PHOTOS" in decision.flags


def test_home_gate_failure_without_exception_is_structured_only():
    decision = classify_visual_eligibility(
        _candidate(bedrooms=2, bathrooms=1, photo_count=12)
    )

    assert decision.home_fit == "FAIL"
    assert decision.visual_eligibility == "STRUCTURED_ONLY"
    assert "HOME_BASELINE_FAIL" in decision.flags


def test_small_premium_lot_or_development_exception_can_enter_representative_pool():
    decision = classify_visual_eligibility(
        _candidate(
            primary_track="PREMIUM_SMALL_LOT",
            lot_area_sqft=2970,
            description="corner lot with future redevelopment opportunity",
            photo_count=8,
            unique_photo_count=8,
        )
    )

    assert decision.home_fit == "FAIL"
    assert decision.visual_eligibility == "REPRESENTATIVE_POOL"
    assert "EXCEPTION_ROUTE" in decision.flags


def test_land_dominant_language_does_not_spend_interior_visual_budget_for_weak_house():
    decision = classify_visual_eligibility(
        _candidate(
            primary_track="LAND_DEVELOPMENT",
            bedrooms=2,
            bathrooms=1,
            description="building lot, land value only, developer alert",
        )
    )

    assert decision.development_text_signal == "LAND_DOMINANT"
    assert decision.visual_eligibility == "STRUCTURED_ONLY"
    assert "LAND_DOMINANT_TEXT" in decision.flags


def test_general_rebuild_language_does_not_remove_qualifying_home():
    decision = classify_visual_eligibility(
        _candidate(
            description="well kept family home with rebuild opportunity",
        )
    )

    assert decision.development_text_signal == "GENERAL"
    assert decision.visual_eligibility == "FULL_POOL"
    assert "GENERAL_DEVELOPMENT_TEXT" in decision.flags


def test_land_dominant_language_on_qualifying_home_is_at_most_representative():
    decision = classify_visual_eligibility(
        _candidate(
            description="building lot with land value only; current home has 3 beds and 2 baths",
        )
    )

    assert decision.home_fit == "PASS"
    assert decision.visual_eligibility == "REPRESENTATIVE_POOL"
    assert "LAND_DOMINANT_TEXT" in decision.flags


def test_unknown_numeric_values_do_not_pass_home_gate():
    decision = classify_visual_eligibility(
        _candidate(lot_area_sqft="UNKNOWN", bedrooms="", bathrooms=None)
    )

    assert decision.home_fit == "UNKNOWN"
    assert decision.visual_eligibility == "STRUCTURED_ONLY"
    assert "HOME_BASELINE_UNKNOWN" in decision.flags


def test_special_track_is_special_review_and_never_visual():
    decision = classify_visual_eligibility(
        _candidate(primary_track="SPECIAL_SITUATION", photo_count=20)
    )

    assert decision.visual_eligibility == "SPECIAL_REVIEW"
    assert decision.analysis_route == "SPECIAL_REVIEW"
    assert decision.visual_budget_images == 0


def test_apply_visual_selection_uses_all_unique_photos_for_full_route():
    decision = classify_visual_eligibility(_candidate())
    selected = apply_visual_selection(
        decision,
        selected=True,
        unique_photo_count=10,
        selection_reasons=["TRACK_TARGET_FILL"],
    )

    assert selected.analysis_route == "FULL_VISION"
    assert selected.selected_for_visual is True
    assert selected.visual_budget_images == 10
    assert selected.selection_reasons == ["TRACK_TARGET_FILL"]


def test_apply_visual_selection_caps_representative_route_at_three_photos():
    decision = classify_visual_eligibility(
        _candidate(photo_count=5, unique_photo_count=5)
    )
    selected = apply_visual_selection(
        decision,
        selected=True,
        unique_photo_count=5,
        selection_reasons=["EXCEPTIONAL_SIGNAL"],
    )

    assert selected.analysis_route == "REPRESENTATIVE_VISION"
    assert selected.visual_budget_images == 3


def test_unselected_candidate_remains_structured_only():
    decision = classify_visual_eligibility(_candidate())
    result = apply_visual_selection(decision, selected=False)

    assert result.analysis_route == "STRUCTURED_ONLY"
    assert result.selected_for_visual is False
    assert result.visual_budget_images == 0
