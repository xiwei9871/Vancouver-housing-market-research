from datetime import datetime, timezone

import pytest

from northshore_watchdog.research.discovery import (
    DiscoveryCard,
    DiscoveryCapture,
    DiscoveryFilterPlan,
    DiscoveryPreset,
    DiscoveryStatus,
    DiscoveryValidation,
    SiteCapabilities,
    compile_filter_plan,
    deduplicate_cards,
    rank_shortlist,
    validate_capture,
)


OBSERVED_AT = datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc)
_RESULT_COUNT_UNSET = object()


def _preset(
    desired_filters: dict | None = None,
    *,
    max_result_cards: int = 100,
    sample_size: int = 5,
    shortlist_limit: int | None = None,
) -> DiscoveryPreset:
    return DiscoveryPreset(
        name="detached-core",
        market_bucket="vancouver_east",
        desired_filters=desired_filters or {},
        max_result_cards=max_result_cards,
        sample_size=sample_size,
        shortlist_limit=shortlist_limit,
    )


def _card(**values: object) -> DiscoveryCard:
    values.setdefault("address", "123 Main Street")
    return DiscoveryCard(**values)


def _capture(
    cards: list[DiscoveryCard | dict],
    *,
    preset_name: str | None = "detached-core",
    result_count: int | None | object = _RESULT_COUNT_UNSET,
    count_is_complete: bool = True,
    **values: object,
) -> DiscoveryCapture:
    return DiscoveryCapture(
        source_name="fixture-source",
        source_url="https://fixture.example/search",
        preset_name=preset_name,
        observed_at=OBSERVED_AT,
        cards=cards,
        result_count=(len(cards) if result_count is _RESULT_COUNT_UNSET else result_count),
        count_is_complete=count_is_complete,
        **values,
    )


def test_discovery_models_have_safe_defaults_and_validate_raw_dict_cards():
    preset = DiscoveryPreset(name="p", market_bucket="bucket")
    capabilities = SiteCapabilities()
    capture = DiscoveryCapture(
        source_name="fixture-source",
        source_url="https://fixture.example/search",
        cards=[
            {
                "address": "1 Main Street",
                "asking": "$1,200,000",
                "raw": {"source_id": "one"},
            }
        ],
    )

    assert preset.desired_filters == {}
    assert preset.max_result_cards == 100
    assert preset.sample_size == 5
    assert preset.shortlist_limit is None
    assert capabilities.native_filter_fields == set()
    assert capabilities.card_fields == set()
    assert isinstance(capture.cards[0], DiscoveryCard)
    assert capture.cards[0].asking == 1_200_000
    assert capture.observed_at.tzinfo is not None
    assert capture.observed_at.utcoffset() == timezone.utc.utcoffset(capture.observed_at)


def test_discovery_model_bounds_reject_non_positive_limits():
    with pytest.raises(ValueError):
        DiscoveryPreset(name="p", market_bucket="bucket", sample_size=0)
    with pytest.raises(ValueError):
        DiscoveryPreset(name="p", market_bucket="bucket", max_result_cards=0)
    with pytest.raises(ValueError):
        DiscoveryPreset(name="p", market_bucket="bucket", shortlist_limit=0)


@pytest.mark.parametrize("field", ["sample_size", "max_result_cards", "shortlist_limit"])
@pytest.mark.parametrize(
    "invalid_value",
    [True, 2.5, float("nan"), float("inf"), "2.5", "not-a-limit"],
)
def test_discovery_preset_rejects_non_integral_or_invalid_public_limits(field, invalid_value):
    with pytest.raises(ValueError):
        DiscoveryPreset(
            name="p",
            market_bucket="bucket",
            **{field: invalid_value},
        )


def test_compile_filter_plan_separates_native_post_and_unsupported_filters_without_mutation():
    desired = {
        "price_min": 1_000_000,
        "minimum_bedrooms": 3,
        "minimum_finished_sqft": 1_500,
        "minimum_bathrooms": 2,
        "status": "active",
    }
    preset = _preset(desired)
    capabilities = SiteCapabilities(
        native_filter_fields={"price_min", "status"},
        card_fields={"asking", "bedrooms", "finished_sqft", "listing_status"},
        filter_value_mapping={"status": {"active": "for sale"}},
    )

    plan = compile_filter_plan(
        preset,
        capabilities,
        source_name="fixture-source",
        actions=["capture search page"],
    )

    assert isinstance(plan, DiscoveryFilterPlan)
    assert plan.preset_name == preset.name
    assert plan.source_name == "fixture-source"
    assert plan.desired_filters == desired
    assert plan.native_filters == {"price_min": 1_000_000, "status": "for sale"}
    assert plan.post_filters == {
        "minimum_bedrooms": 3,
        "minimum_finished_sqft": 1_500,
    }
    assert plan.unsupported_filters == {"minimum_bathrooms": 2}
    assert plan.actions == ["capture search page"]
    assert preset.desired_filters == desired


def test_native_filter_validation_fails_closed_on_a_known_violation():
    preset = _preset({"minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"minimum_bedrooms"}, card_fields={"bedrooms"}),
    )
    capture = _capture([_card(address="1 Main Street", bedrooms=2)])

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes
    assert result.violations[0]["field"] == "minimum_bedrooms"
    assert result.violations[0]["observed"] == 2


def test_post_filter_is_not_mistaken_for_native_filter_failure():
    preset = _preset({"minimum_finished_sqft": 1_500})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(card_fields={"finished_sqft"}),
    )
    capture = _capture([_card(finished_sqft=1_200)])

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" not in result.reason_codes
    assert result.violations[0]["reason_code"] == "POST_FILTER_EXCLUDED"
    assert capture.cards[0].reason_codes == []


def test_municipality_post_filter_excludes_cards_outside_the_search_boundary():
    preset = _preset({"municipality": "Vancouver"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(card_fields={"municipality"}),
    )
    capture = _capture(
        [
            _card(address="1 Vancouver Street", municipality="Vancouver"),
            _card(address="2 Boundary Road", municipality="Burnaby"),
        ]
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert [card.address for card in result.candidate_cards] == ["1 Vancouver Street"]
    assert "POST_FILTER_EXCLUDED" in result.reason_codes


def test_rank_shortlist_with_no_limit_retains_all_cards_in_ranked_order():
    cards = [
        _card(address="1 First Street", asking=1_100_000),
        _card(address="2 Second Street", asking=1_200_000),
        _card(address="3 Third Street", asking=1_300_000),
    ]

    ranked = rank_shortlist(cards, limit=None)

    assert len(ranked) == len(cards)
    assert {card.address for card in ranked} == {card.address for card in cards}


def test_post_filter_unknown_value_is_retained_with_an_explicit_reason():
    preset = _preset({"minimum_finished_sqft": 1_500})
    plan = compile_filter_plan(preset, SiteCapabilities(card_fields={"finished_sqft"}))
    capture = _capture([_card(finished_sqft="not disclosed")])

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1
    assert "POST_FILTER_UNKNOWN" in result.candidate_cards[0].reason_codes


def test_native_semantics_accept_common_strings_and_numeric_formats():
    preset = _preset(
        {
            "price_min": 1_000_000,
            "price_max": 1_600_000,
            "minimum_bedrooms": 3,
            "minimum_bathrooms": 2,
            "minimum_finished_sqft": 1_500,
            "ownership": "freehold",
            "property_type": "detached",
            "status": "active",
        }
    )
    fields = set(preset.desired_filters)
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields=fields),
    )
    capture = _capture(
        [
            _card(
                asking="$1,250,000",
                bedrooms="3 beds",
                bathrooms="2.5",
                finished_sqft="1,800 sqft",
                ownership_type="Freehold Non-Strata",
                property_type="Detached House",
                listing_status="Active Listing",
            )
        ]
    )

    result = validate_capture(capture, plan, sample_size=1, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


@pytest.mark.parametrize(
    ("field", "label", "expected"),
    [
        ("bedrooms", "3 beds", 3),
        ("bathrooms", "2 baths", 2),
        ("bedrooms", "4 bedrooms", 4),
    ],
)
def test_common_room_count_labels_are_not_treated_as_magnitude_suffixes(
    field,
    label,
    expected,
):
    card = _card(**{field: label})

    assert getattr(card, field) == expected


def test_three_bed_label_fails_a_four_bedroom_native_minimum():
    plan = compile_filter_plan(
        _preset({"minimum_bedrooms": 4}),
        SiteCapabilities(native_filter_fields={"minimum_bedrooms"}),
    )

    result = validate_capture(
        _capture([_card(bedrooms="3 beds")]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("$1.25m", 1_250_000),
        ("2 thousand CAD", 2_000),
        ("3 billion", 3_000_000_000),
    ],
)
def test_exact_magnitude_suffix_tokens_continue_to_scale(label, expected):
    assert _card(asking=label).asking == expected


def test_missing_native_value_is_inconclusive_without_filter_evidence():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture([_card(asking=None)])

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in result.reason_codes


def test_filter_evidence_allows_a_missing_native_card_value():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filter_evidence={"price_min": {"present": True, "label": "Min $1M"}},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_list_filter_evidence_marker_allows_a_missing_native_card_value():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filter_evidence=["price_min control was visible"],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert capture.applied_filter_evidence == ["price_min control was visible"]
    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


@pytest.mark.parametrize(
    "evidence",
    [
        {"price_min": {"present": False}},
        {"price_min": {"applied": False}},
        ["price_min filter was not applied"],
        ["price_min control is unavailable"],
    ],
)
def test_negative_filter_evidence_does_not_authorize_a_missing_native_value(evidence):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture([_card(asking=None)], applied_filter_evidence=evidence)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


@pytest.mark.parametrize(
    "evidence",
    [
        ["price_min applied: false"],
        ["price_min present: no"],
        ["price_min not applied"],
        ["price_min not selected"],
        ["price_min control was visible, but failed to apply"],
        {"price_min": "price_min applied: false"},
        {"price_min": {"present": "no"}},
    ],
)
def test_embedded_negative_evidence_markers_override_generic_prose(evidence):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture([_card(asking=None)], applied_filter_evidence=evidence)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


@pytest.mark.parametrize(
    "nested_evidence",
    [
        {"present": True, "error": "filter failed"},
        {"applied": True, "failed": True},
        {"selected": True, "status": "not selected"},
    ],
)
def test_nested_negative_evidence_overrides_positive_sibling_keys(nested_evidence):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filter_evidence={"price_min": nested_evidence},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


def test_explicit_positive_evidence_still_authorizes_a_missing_native_value():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filter_evidence=["price_min applied: true"],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_generic_positive_mapping_evidence_is_explicitly_global():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture(
        [_card(asking=None, bedrooms=None)],
        applied_filter_evidence={"present": True},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_matching_applied_filters_authorize_missing_native_card_values():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filters={"price_min": 1_000_000},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_matching_applied_filter_with_nested_negative_state_fails_closed():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filters={
            "price_min": {
                "value": 1_000_000,
                "error": "filter failed",
            }
        },
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


def test_matching_applied_filter_conflicts_with_negative_evidence():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filters={"price_min": 1_000_000},
        applied_filter_evidence={
            "price_min": {
                "present": True,
                "error": "filter failed",
            }
        },
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


@pytest.mark.parametrize(
    ("applied_filters", "applied_filter_evidence"),
    [
        (
            {
                "price_min": {
                    "value": 1_000_000,
                    "error": "filter failed",
                }
            },
            {},
        ),
        (
            {"price_min": 1_000_000},
            {
                "price_min": {
                    "present": True,
                    "error": "filter failed",
                }
            },
        ),
        (
            {"price_min": 1_000_000},
            {"global": {"error": "filter failed"}},
        ),
    ],
)
def test_negative_filter_state_rejects_even_when_sampled_cards_match(
    applied_filters,
    applied_filter_evidence,
):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=1_250_000)],
        applied_filters=applied_filters,
        applied_filter_evidence=applied_filter_evidence,
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


def test_mismatched_applied_filter_value_fails_closed():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=None)],
        applied_filters={"price_min": 900_000},
        applied_filter_evidence={"price_min": {"present": True}},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


def test_explicit_native_value_overrides_unrelated_no_bound_page_marker():
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=1_250_000)],
        applied_filters={"price_min": 1_000_000},
        applied_filter_evidence=["global: applied filter marker found: No min"],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_explicit_native_values_override_unrelated_page_control_labels():
    preset = _preset(
        {
            "status": "ACTIVE",
            "property_type": "DETACHED_HOUSE",
            "ownership": "FREEHOLD",
            "minimum_bedrooms": 2,
            "minimum_bathrooms": 1,
            "minimum_finished_sqft": 1250,
            "price_min": 1_000_000,
            "price_max": 1_800_000,
        }
    )
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields=set(preset.desired_filters),
            card_fields={
                "listing_status",
                "property_type",
                "ownership_type",
                "bedrooms",
                "bathrooms",
                "finished_sqft",
                "asking",
            },
        ),
    )
    capture = _capture(
        [_card(asking=1_500_000, bedrooms=3, bathrooms=2, finished_sqft=1500)],
        applied_filters={
            "status": "ACTIVE",
            "property_type": "DETACHED_HOUSE",
            "ownership": "FREEHOLD",
            "minimum_bedrooms": "2-0",
            "minimum_bathrooms": "1-0",
            "minimum_finished_sqft": 1250,
            "price_min": 1_000_000,
            "price_max": 1_800_000,
        },
        applied_filter_evidence=[
            "global: applied filter marker found: For sale",
            "global: applied filter marker found: 1,000,000",
            "global: applied filter marker found: 1,800,000",
            "global: applied filter marker found: 2+",
            "global: applied filter marker found: 1+",
            "global: applied filter marker found: Any",
            "global: applied filter marker found: 1,250",
            "global: applied filter marker found: No max",
            "global: applied filter marker found: No min",
            "global: applied filter marker found: House",
            "global: applied filter marker found: 0 selected",
            "global: applied filter marker found: Freehold",
        ],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


@pytest.mark.parametrize(
    "evidence",
    [
        {"price_min": False},
        {"global": False},
        {"present": False},
    ],
)
def test_direct_boolean_negative_evidence_rejects_a_known_matching_card(evidence):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    capture = _capture(
        [_card(asking=1_250_000)],
        applied_filter_evidence=evidence,
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "APPLIED_FILTER_MISMATCH" in result.reason_codes


@pytest.mark.parametrize(
    "evidence",
    [
        {"price_min": 10**1000},
        {"price_min": float("inf")},
        {"price_min": {"present": 10**1000}},
        {"price_min": {"present": float("inf")}},
    ],
)
def test_malformed_numeric_evidence_returns_bounded_validation_result(evidence):
    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))

    result = validate_capture(
        _capture([_card(asking=None)], applied_filter_evidence=evidence),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert isinstance(result, DiscoveryValidation)
    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in result.reason_codes


def test_zero_sampled_cards_require_evidence_for_every_native_filter():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture([], result_count=0, count_is_complete=True)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert {violation["field"] for violation in result.violations} == {
        "price_min",
        "minimum_bedrooms",
    }


def test_zero_sampled_cards_pass_with_positive_evidence_for_every_native_filter():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture(
        [],
        result_count=0,
        count_is_complete=True,
        applied_filter_evidence={
            "price_min": {"present": True},
            "minimum_bedrooms": {"applied": True},
        },
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert result.candidate_cards == []


def test_global_dict_filter_evidence_marker_authorizes_all_native_fields():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture(
        [_card(asking=None, bedrooms=None)],
        applied_filter_evidence={"global": ["native controls were visible"]},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_field_specific_dict_evidence_does_not_authorize_an_unrelated_native_field():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture(
        [_card(asking=None, bedrooms=None)],
        applied_filter_evidence={"minimum_bedrooms": ["bedroom control was visible"]},
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.violations[0]["field"] == "price_min"


def test_global_list_filter_evidence_requires_an_explicit_global_marker():
    preset = _preset({"price_min": 1_000_000, "minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"price_min", "minimum_bedrooms"}),
    )
    capture = _capture(
        [_card(asking=None, bedrooms=None)],
        applied_filter_evidence=["global: native controls were visible"],
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_result_cap_rejects_an_oversized_capture():
    preset = _preset({}, max_result_cards=10)
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], result_count=11)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=10)

    assert result.status is DiscoveryStatus.RESULT_SET_TOO_LARGE
    assert result.candidate_cards == []
    assert "RESULT_COUNT_EXCEEDS_CAP" in result.reason_codes


@pytest.mark.parametrize(
    ("result_count", "count_is_complete"),
    [(0, True), (None, False), (101, False)],
)
def test_card_list_over_cap_is_rejected_even_when_count_is_not_authoritative(
    result_count,
    count_is_complete,
):
    preset = _preset({}, max_result_cards=100)
    plan = compile_filter_plan(preset, SiteCapabilities())
    cards = [_card(address=f"{index} Main Street") for index in range(101)]
    capture = _capture(
        cards,
        result_count=result_count,
        count_is_complete=count_is_complete,
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.RESULT_SET_TOO_LARGE
    assert result.candidate_cards == []
    assert "CARD_COUNT_EXCEEDS_CAP" in result.reason_codes


def test_negative_result_count_is_rejected_as_a_malformed_envelope():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], result_count=-1, count_is_complete=True)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PARSER_ERROR
    assert result.candidate_cards == []
    assert "NEGATIVE_RESULT_COUNT" in result.reason_codes


def test_complete_result_count_cannot_underreport_the_card_list():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture(
        [_card(address="1 Main Street"), _card(address="2 Main Street")],
        result_count=1,
        count_is_complete=True,
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PARSER_ERROR
    assert result.candidate_cards == []
    assert "RESULT_COUNT_UNDERREPORTS_CARDS" in result.reason_codes


def test_incomplete_provisional_count_over_cap_is_inconclusive_not_too_large():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], result_count=101, count_is_complete=False)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert "RESULT_COUNT_UNKNOWN" in result.reason_codes
    assert "RESULT_COUNT_EXCEEDS_CAP" not in result.reason_codes


def test_missing_incomplete_count_is_inconclusive():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], result_count=None, count_is_complete=False)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert "RESULT_COUNT_UNKNOWN" in result.reason_codes


@pytest.mark.parametrize("parameter", ["sample_size", "max_result_cards"])
@pytest.mark.parametrize(
    "invalid_value",
    [
        True,
        2.5,
        float("nan"),
        float("inf"),
        10**1000,
        "2.5",
        "not-a-limit",
    ],
)
def test_validate_capture_returns_parser_error_for_invalid_public_limits(
    parameter,
    invalid_value,
):
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    limit_values = {"sample_size": 5, "max_result_cards": 100}
    limit_values[parameter] = invalid_value

    result = validate_capture(
        _capture([_card()]),
        plan,
        sample_size=limit_values["sample_size"],
        max_result_cards=limit_values["max_result_cards"],
    )

    assert result.status is DiscoveryStatus.PARSER_ERROR
    assert result.candidate_cards == []
    assert "INVALID_VALIDATION_LIMIT" in result.reason_codes


def test_public_limit_integer_strings_remain_supported():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())

    result = validate_capture(
        _capture([_card()]),
        plan,
        sample_size="1",
        max_result_cards="100",
    )

    assert result.status is DiscoveryStatus.PASS
    assert len(rank_shortlist([_card()], limit="1")) == 1


@pytest.mark.parametrize(
    "invalid_value",
    [True, 2.5, float("nan"), float("inf"), 10**1000, "2.5", "not-a-limit"],
)
def test_rank_shortlist_returns_empty_for_invalid_public_limit(invalid_value):
    assert rank_shortlist([_card()], limit=invalid_value) == []


def test_compile_requires_the_requested_field_itself_for_native_placement():
    preset = _preset({"minimum_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"bedrooms"}, card_fields={"bedrooms"}),
    )

    assert plan.native_filters == {}
    assert plan.post_filters == {"minimum_bedrooms": 3}
    assert plan.unsupported_filters == {}


def test_compile_alias_uses_canonical_card_field_for_post_filter_lookup():
    preset = _preset({"min_bedrooms": 3})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(card_fields={"bedrooms"}),
    )

    assert plan.native_filters == {}
    assert plan.post_filters == {"min_bedrooms": 3}
    assert plan.unsupported_filters == {}


def test_compile_keeps_a_single_mapping_action_as_one_typed_action():
    action = {"kind": "capture", "selector": ".listing-card"}
    plan = compile_filter_plan(
        _preset({}),
        SiteCapabilities(),
        actions=action,
    )

    assert plan.actions == [action]


def test_compile_deep_copies_iterable_structured_actions():
    actions = [
        {
            "kind": "select",
            "parameters": {
                "values": ["detached", {"label": "house"}],
            },
        }
    ]
    plan = compile_filter_plan(
        _preset({}),
        SiteCapabilities(),
        actions=actions,
    )

    actions[0]["kind"] = "mutated"
    actions[0]["parameters"]["values"][1]["label"] = "condo"
    actions[0]["parameters"]["values"].append("townhouse")

    assert plan.actions == [
        {
            "kind": "select",
            "parameters": {
                "values": ["detached", {"label": "house"}],
            },
        }
    ]


def test_scalar_filter_mapping_spec_does_not_replace_the_native_value():
    preset = _preset({"status": "active"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"status"},
            filter_value_mapping={"status": "for sale"},
        ),
    )

    assert plan.native_filters == {"status": "active"}
    assert plan.desired_filters == {"status": "active"}


def test_weaker_native_numeric_mapping_keeps_the_desired_constraint_as_post_filter():
    preset = _preset({"minimum_bathrooms": 2.5})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"minimum_bathrooms"},
            card_fields={"bathrooms"},
            filter_value_mapping={"minimum_bathrooms": {"2.5": 2}},
        ),
    )

    assert plan.native_filters == {"minimum_bathrooms": 2}
    assert plan.post_filters == {"minimum_bathrooms": 2.5}

    result = validate_capture(
        _capture([_card(bathrooms=2)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.PASS
    assert result.candidate_cards == []
    assert "POST_FILTER_EXCLUDED" in result.reason_codes


def test_weaker_native_numeric_mapping_without_card_field_is_unsupported():
    preset = _preset({"minimum_bathrooms": 2.5})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"minimum_bathrooms"},
            filter_value_mapping={"minimum_bathrooms": {"2.5": 2}},
        ),
    )

    assert plan.unsupported_filters == {"minimum_bathrooms": 2.5}
    result = validate_capture(
        _capture([_card(bathrooms=2)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.UNSUPPORTED_FILTER
    assert result.candidate_cards == []


@pytest.mark.parametrize("property_type", ["Townhouse", "Detached Townhome"])
def test_broader_native_property_type_mapping_retains_desired_post_filter(property_type):
    preset = _preset({"property_type": "DETACHED_HOUSE"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"property_type"},
            card_fields={"property_type"},
            filter_value_mapping={"property_type": {"DETACHED_HOUSE": "house"}},
        ),
    )

    assert plan.native_filters == {"property_type": "house"}
    assert plan.post_filters == {"property_type": "DETACHED_HOUSE"}
    assert plan.unsupported_filters == {}

    result = validate_capture(
        _capture([_card(property_type=property_type)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.PASS
    assert result.candidate_cards == []
    assert "POST_FILTER_EXCLUDED" in result.reason_codes


def test_broader_native_categorical_mapping_without_card_field_is_unsupported():
    preset = _preset({"property_type": "DETACHED_HOUSE"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"property_type"},
            filter_value_mapping={"property_type": {"DETACHED_HOUSE": "house"}},
        ),
    )

    assert plan.native_filters == {"property_type": "house"}
    assert plan.post_filters == {}
    assert plan.unsupported_filters == {"property_type": "DETACHED_HOUSE"}


def test_narrower_native_property_mapping_remains_native_without_card_field():
    preset = _preset({"property_type": "house"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"property_type"},
            filter_value_mapping={"property_type": {"house": "detached"}},
        ),
    )

    assert plan.native_filters == {"property_type": "detached"}
    assert plan.post_filters == {}
    assert plan.unsupported_filters == {}

    result = validate_capture(
        _capture([_card(property_type="Detached House")]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1


def test_broader_native_house_mapping_treats_generic_house_as_post_unknown():
    preset = _preset({"property_type": "DETACHED_HOUSE"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"property_type"},
            card_fields={"property_type"},
            filter_value_mapping={"property_type": {"DETACHED_HOUSE": "house"}},
        ),
    )

    result = validate_capture(
        _capture([_card(property_type="House")]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.PASS
    assert len(result.candidate_cards) == 1
    assert "POST_FILTER_UNKNOWN" in result.candidate_cards[0].reason_codes
    assert "POST_FILTER_EXCLUDED" not in result.candidate_cards[0].reason_codes


@pytest.mark.parametrize(
    ("field", "desired", "native_value", "card_field", "observed"),
    [
        ("status", "active", "for sale", "listing_status", "Active Listing"),
        ("ownership", "freehold", "fee simple", "ownership_type", "Freehold Non-Strata"),
    ],
)
def test_equivalent_native_categorical_mappings_do_not_add_post_filters(
    field,
    desired,
    native_value,
    card_field,
    observed,
):
    preset = _preset({field: desired})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={field},
            card_fields={card_field},
            filter_value_mapping={field: {desired: native_value}},
        ),
    )

    assert plan.native_filters == {field: native_value}
    assert plan.post_filters == {}
    assert plan.unsupported_filters == {}

    result = validate_capture(
        _capture([_card(**{card_field: observed})]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )
    assert result.status is DiscoveryStatus.PASS


@pytest.mark.parametrize(
    ("desired", "native_value", "observed", "expected_status"),
    [
        ("strata", "freehold strata", "Freehold Strata", DiscoveryStatus.PASS),
        ("strata", "freehold strata", "Freehold Non-Strata", DiscoveryStatus.FILTER_NOT_APPLIED),
        ("freehold", "freehold", "Freehold Non-Strata", DiscoveryStatus.PASS),
        ("freehold", "freehold", "Freehold Strata", DiscoveryStatus.FILTER_NOT_APPLIED),
    ],
)
def test_ownership_categories_distinguish_freehold_strata_from_pure_freehold(
    desired,
    native_value,
    observed,
    expected_status,
):
    preset = _preset({"ownership": desired})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={"ownership"},
            card_fields={"ownership_type"},
            filter_value_mapping={"ownership": {desired: native_value}},
        ),
    )

    assert plan.native_filters == {"ownership": native_value}
    assert plan.post_filters == {}
    assert plan.unsupported_filters == {}

    result = validate_capture(
        _capture([_card(ownership_type=observed)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is expected_status
    if expected_status is DiscoveryStatus.FILTER_NOT_APPLIED:
        assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


@pytest.mark.parametrize("property_type", ["Townhouse", "Attached House", "Semi-Detached", "Condo"])
def test_detached_house_filter_rejects_attached_property_types(property_type):
    preset = _preset({"property_type": "DETACHED_HOUSE"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"property_type"}),
    )

    result = validate_capture(
        _capture([_card(property_type=property_type)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


@pytest.mark.parametrize("property_type", ["Detached Townhome", "Detached Townhouse", "Detached Town Home"])
def test_detached_house_filter_rejects_detached_townhome_labels(property_type):
    preset = _preset({"property_type": "DETACHED_HOUSE"})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(native_filter_fields={"property_type"}),
    )

    result = validate_capture(
        _capture([_card(property_type=property_type)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


@pytest.mark.parametrize(
    ("capture_values", "expected_status", "reason"),
    [
        ({"blocked": True}, DiscoveryStatus.SOURCE_BLOCKED, "CAPTURE_BLOCKED"),
        ({"status_code": 403}, DiscoveryStatus.SOURCE_BLOCKED, "NON_2XX_STATUS"),
    ],
)
def test_capture_rejection_conditions_fail_closed(capture_values, expected_status, reason):
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], **capture_values)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is expected_status
    assert result.candidate_cards == []
    assert reason in result.reason_codes


def test_capture_with_detail_requests_remains_validatable():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], detail_requests=1)

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.PASS
    assert result.candidate_cards


def test_capture_with_nonmatching_preset_is_rejected():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card()], preset_name="another-preset")

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert "PRESET_MISMATCH" in result.reason_codes


def test_unsupported_filter_rejects_capture_before_candidates_are_returned():
    preset = _preset({"minimum_bathrooms": 2})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture([_card(bathrooms=3)])

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)

    assert result.status is DiscoveryStatus.UNSUPPORTED_FILTER
    assert result.candidate_cards == []
    assert "UNSUPPORTED_FILTERS_PRESENT" in result.reason_codes


def test_capture_metadata_is_inherited_by_cards_before_validation():
    preset = _preset({})
    plan = compile_filter_plan(preset, SiteCapabilities())
    capture = _capture(
        [
            {
                "address": "1 Main Street",
                "observed_at": None,
            }
        ]
    )

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)
    card = result.candidate_cards[0]

    assert card.source_name == capture.source_name
    assert card.source_url == capture.source_url
    assert card.observed_at == capture.observed_at


def test_deduplicate_cards_prefers_exact_normalized_mls_key():
    result = deduplicate_cards(
        [
            _card(mls_number="C1234567", asking=1_000_000),
            _card(address="999 Other Street", mls_number=" c1234567 ", asking=2_000_000),
        ]
    )

    assert len(result.cards) == 1
    assert result.cards[0].asking == 1_000_000
    assert "EXACT_MLS_DUPLICATE" in result.duplicate_flags


def test_deduplicate_cards_retains_discarded_duplicate_observation_without_merging():
    result = deduplicate_cards(
        [
            _card(
                mls_number="C1234567",
                asking=1_000_000,
                raw={"source_observation": "first"},
            ),
            _card(
                address="999 Other Street",
                mls_number=" c1234567 ",
                asking=2_000_000,
                raw={"source_observation": "second"},
            ),
        ]
    )

    assert len(result.cards) == 1
    assert result.cards[0].asking == 1_000_000
    assert len(result.duplicate_observations) == 1
    duplicate = result.duplicate_observations[0]
    assert duplicate.mls_number == " c1234567 "
    assert duplicate.asking == 2_000_000
    assert duplicate.raw == {"source_observation": "second"}


def test_deduplicate_cards_uses_address_fallback_when_mls_is_absent():
    result = deduplicate_cards(
        [
            _card(address="123 Main Street", municipality="Vancouver"),
            _card(address="123 MAIN ST", municipality="Vancouver"),
        ]
    )

    assert len(result.cards) == 1
    assert "ADDRESS_FALLBACK_DUPLICATE" in result.duplicate_flags


def test_invalid_mls_values_are_treated_as_absent_for_address_fallback():
    result = deduplicate_cards(
        [
            _card(address="123 Main Street", mls_number="N/A", asking=1_000_000),
            _card(address="123 Main Street", mls_number="0", asking=1_100_000),
        ]
    )

    assert len(result.cards) == 1
    assert "ADDRESS_FALLBACK_DUPLICATE" in result.duplicate_flags


def test_placeholder_addresses_do_not_collapse_no_mls_observations():
    result = deduplicate_cards(
        [
            _card(address="N/A"),
            _card(address="N/A"),
        ]
    )

    assert len(result.cards) == 2
    assert "ADDRESS_FALLBACK_DUPLICATE" not in result.duplicate_flags


def test_duplicate_observation_records_are_derived_not_stored_twice():
    result = deduplicate_cards(
        [
            _card(mls_number="A1", asking=1_000_000),
            _card(address="999 Other Street", mls_number="a1", asking=2_000_000),
        ]
    )

    assert "duplicate_observation_records" not in result.model_dump()
    assert len(result.duplicate_observation_records) == 1
    assert result.duplicate_observation_records[0].card.asking == 2_000_000


def test_same_address_with_different_mls_is_retained_and_flagged():
    result = deduplicate_cards(
        [
            _card(address="123 Main Street", municipality="Vancouver", mls_number="A1", asking=1_000_000),
            _card(address="123 MAIN ST", municipality="Vancouver", mls_number="B2", asking=1_100_000),
        ]
    )

    assert len(result.cards) == 2
    assert "POSSIBLE_DUPLICATE" in result.duplicate_flags
    assert all("POSSIBLE_DUPLICATE" in card.reason_codes for card in result.cards)
    assert {card.asking for card in result.cards} == {1_000_000, 1_100_000}


def test_rank_shortlist_respects_limit_and_retains_small_lots():
    small = _card(
        address="1 Small Lot Street",
        asking=1_200_000,
        lot_area_sqft=2_950,
        bedrooms=3,
        bathrooms=2,
        finished_sqft=1_700,
        observed_at=OBSERVED_AT,
    )
    complete = _card(
        address="2 Complete Street",
        asking=1_300_000,
        lot_area_sqft=5_000,
        frontage_ft=40,
        depth_ft=120,
        bedrooms=4,
        bathrooms=3,
        finished_sqft=2_200,
        observed_at=OBSERVED_AT,
    )

    ranked = rank_shortlist([small, complete], limit=2)

    assert len(ranked) == 2
    assert {card.address for card in ranked} == {small.address, complete.address}
    retained_small = next(card for card in ranked if card.address == small.address)
    assert "SMALL_LOT" in retained_small.reason_codes


def test_rank_shortlist_uses_core_range_signal_without_becoming_an_investment_score():
    outside = _card(
        address="1 Outside Street",
        asking=1_800_000,
        raw={"core_range": [1_000_000, 1_600_000]},
    )
    inside = _card(
        address="2 Inside Street",
        asking=1_400_000,
        raw={"core_range": [1_000_000, 1_600_000]},
    )

    ranked = rank_shortlist([outside, inside], limit=1)

    assert len(ranked) == 1
    assert ranked[0].address == inside.address


def test_rank_shortlist_checks_negative_core_classification_before_core_text():
    outside = _card(
        address="1 Outside Street",
        raw={"range_classification": "out of core range"},
    )
    inside = _card(
        address="2 Inside Street",
        raw={"range_classification": "core"},
    )

    ranked = rank_shortlist([outside, inside], limit=1)

    assert ranked[0].address == inside.address


def test_rank_shortlist_ties_are_independent_of_input_order():
    first = _card(address="123 Same Street", source_url="https://source.example/b")
    second = _card(address="123 Same Street", source_url="https://source.example/a")

    forward = [card.source_url for card in rank_shortlist([first, second], limit=2)]
    reverse = [card.source_url for card in rank_shortlist([second, first], limit=2)]

    assert forward == reverse


def test_validation_uses_a_deep_snapshot_without_mutating_the_capture():
    capture = _capture([_card(source_name=None, source_url=None, observed_at=None)])
    plan = compile_filter_plan(_preset({}), SiteCapabilities())

    result = validate_capture(capture, plan, sample_size=5, max_result_cards=100)
    returned_card = result.candidate_cards[0]

    assert returned_card.source_name == capture.source_name
    assert returned_card.source_url == capture.source_url
    assert returned_card.observed_at == capture.observed_at
    assert capture.cards[0].source_name is None
    assert capture.cards[0].source_url is None
    assert capture.cards[0].observed_at is None
    returned_card.reason_codes.append("RETURN_ONLY")
    assert "RETURN_ONLY" not in capture.cards[0].reason_codes


def test_card_alias_populates_a_missing_canonical_value():
    card = DiscoveryCard(
        address="1 Main Street",
        asking=None,
        list_price="$1,200,000",
    )

    assert card.asking == 1_200_000


def test_card_alias_conflict_keeps_canonical_value_and_records_reason():
    card = DiscoveryCard(
        address="1 Main Street",
        asking=1_000_000,
        list_price=1_200_000,
    )

    assert card.asking == 1_000_000
    assert "ALIAS_CONFLICT" in card.reason_codes


@pytest.mark.parametrize(
    ("field", "desired", "card_field", "observed"),
    [
        ("ownership", "freehold", "ownership_type", "Not Freehold"),
        ("ownership", "freehold", "ownership_type", "Not Fee Simple"),
        ("status", "active", "listing_status", "Not For Sale"),
        ("status", "active", "listing_status", "Not Active"),
        ("property_type", "DETACHED_HOUSE", "property_type", "Not Detached"),
        ("property_type", "DETACHED_HOUSE", "property_type", "Not Detached House"),
    ],
)
def test_negated_observed_categorical_labels_are_native_violations(
    field,
    desired,
    card_field,
    observed,
):
    preset = _preset({field: desired})
    plan = compile_filter_plan(
        preset,
        SiteCapabilities(
            native_filter_fields={field},
            card_fields={card_field},
        ),
    )

    result = validate_capture(
        _capture([_card(**{card_field: observed})]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes
    assert "NATIVE_FILTER_VALUE_UNKNOWN" not in result.reason_codes


@pytest.mark.parametrize(
    "placeholder",
    ["N/A", "TBD", "not disclosed", "unavailable", "not provided", "no data"],
)
def test_expanded_invalid_mls_placeholders_use_address_fallback(placeholder):
    result = deduplicate_cards(
        [
            _card(address="123 Main Street", mls_number=placeholder, asking=1_000_000),
            _card(address="123 Main Street", mls_number="0", asking=1_100_000),
        ]
    )

    assert len(result.cards) == 1
    assert "ADDRESS_FALLBACK_DUPLICATE" in result.duplicate_flags
    assert "EXACT_MLS_DUPLICATE" not in result.duplicate_flags


@pytest.mark.parametrize(
    "placeholder",
    ["N/A", "TBD", "not disclosed", "unavailable", "not provided", "no data"],
)
def test_expanded_placeholder_addresses_do_not_collapse_no_mls_observations(placeholder):
    result = deduplicate_cards(
        [
            _card(address=placeholder),
            _card(address=placeholder),
        ]
    )

    assert len(result.cards) == 2
    assert "ADDRESS_FALLBACK_DUPLICATE" not in result.duplicate_flags


@pytest.mark.parametrize(
    "bad_numeric_value",
    [[1_000_000], {"value": 1_000_000}, True, float("nan"), float("inf")],
)
def test_malformed_numeric_card_values_remain_unknown(bad_numeric_value):
    try:
        card = _card(asking=bad_numeric_value)
    except Exception as exc:  # pragma: no cover - documents the desired boundary
        pytest.fail(f"malformed numeric card value should be retained: {exc}")

    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    result = validate_capture(
        _capture([card]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in result.reason_codes


@pytest.mark.parametrize(
    "value",
    ["2e6", "1e3 sqft", "about 2e6", "Unit 2 | $3,500,000"],
)
def test_unsupported_numeric_notation_remains_unknown(value):
    card = _card(asking=value)
    assert card.asking == value

    preset = _preset({"price_min": 1_000_000})
    plan = compile_filter_plan(preset, SiteCapabilities(native_filter_fields={"price_min"}))
    result = validate_capture(
        _capture([card]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in result.reason_codes


@pytest.mark.parametrize(
    ("field", "desired", "card_field", "observed"),
    [
        ("property_type", "house", "property_type", "bungalow"),
        ("status", "active", "listing_status", "unknown status"),
        ("ownership", "freehold", "ownership_type", "unknown tenure"),
        ("property_type", "mystery type alpha", "property_type", "mystery type beta"),
        ("status", "future status alpha", "listing_status", "future status beta"),
    ],
)
def test_unknown_categorical_labels_do_not_match_by_fallback_substring_or_other(
    field,
    desired,
    card_field,
    observed,
):
    plan = compile_filter_plan(
        _preset({field: desired}),
        SiteCapabilities(native_filter_fields={field}),
    )
    result = validate_capture(
        _capture([_card(**{card_field: observed})]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_VALIDATION_INCONCLUSIVE
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VALUE_UNKNOWN" in result.reason_codes


@pytest.mark.parametrize("observed", ["inactive", "not active"])
def test_known_status_negation_or_inactive_label_does_not_match_active(observed):
    plan = compile_filter_plan(
        _preset({"status": "active"}),
        SiteCapabilities(native_filter_fields={"status"}),
    )
    result = validate_capture(
        _capture([_card(listing_status=observed)]),
        plan,
        sample_size=5,
        max_result_cards=100,
    )

    assert result.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_cards == []
    assert "NATIVE_FILTER_VIOLATION" in result.reason_codes


def test_alias_placeholder_is_replaced_by_populated_canonical_alias():
    card = DiscoveryCard(
        address="1 Main Street",
        asking="N/A",
        list_price="$1,200,000",
    )

    assert card.asking == 1_200_000
    assert "ALIAS_CONFLICT" not in card.reason_codes


def test_ranking_uses_serialized_card_as_final_deterministic_tiebreaker():
    first = _card(
        address="1 Same Street",
        source_url="https://source.example/item",
        raw={"observation": "a"},
    )
    second = _card(
        address="1 Same Street",
        source_url="https://source.example/item",
        raw={"observation": "b"},
    )

    forward = [card.raw["observation"] for card in rank_shortlist([first, second], limit=2)]
    reverse = [card.raw["observation"] for card in rank_shortlist([second, first], limit=2)]

    assert forward == reverse
