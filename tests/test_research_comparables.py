from datetime import datetime, timedelta, timezone

from northshore_watchdog.research.comparables import ComparableRecord, evaluate_comparables
from northshore_watchdog.research.models import EvidenceStatus, PropertyRecord, SourceType
from northshore_watchdog.research.provenance import SourceRecord


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _subject(**updates):
    values = {
        "property_id": "pid:COMP-1",
        "address": "1 Comparable Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "asking": 1_500_000,
        "lot_area_sqft": 5000,
        "frontage_ft": 40,
        "depth_ft": 125,
        "zoning": "R1-1",
        "road_exposure_level": 2,
        "current_use_building_value": 450_000,
    }
    values.update(updates)
    return PropertyRecord(**values)


def _comp(index, *, days=30, price=1_600_000, **updates):
    values = {
        "comparable_id": f"comp-{index}",
        "subject_property_id": "pid:COMP-1",
        "property_id": f"pid:SOLD-{index}",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "sold_date": OBSERVED_AT - timedelta(days=days),
        "sold_price": price,
        "lot_area_sqft": 5000,
        "frontage_ft": 40,
        "depth_ft": 125,
        "zoning": "R1-1",
        "road_exposure_level": 2,
        "current_use_building_value": 450_000,
    }
    values.update(updates)
    return ComparableRecord(**values)


def _config(*, cross_check=False):
    comparables = {"min_reliable_comps": 5, "lot_tolerance_pct": 0.20}
    if cross_check:
        comparables["cross_check_source_registry_ids"] = [
            "source:sold-feed-a",
            "source:sold-feed-b",
        ]
    return {
        "comparables": comparables,
        "source_registry": [
            {
                "source_registry_id": "source:sold-feed-a",
                "name": "sold feed a",
                "source_type": "MLS",
                "authorization_status": "NOT_APPLICABLE",
                "evidence_status": "CONFIRMED",
            },
            {
                "source_registry_id": "source:sold-feed-b",
                "name": "sold feed b",
                "source_type": "MLS",
                "authorization_status": "NOT_APPLICABLE",
                "evidence_status": "CONFIRMED",
            },
        ],
    }


def _single_source_provisional_config():
    config = _config()
    config["comparables"]["allow_single_source_provisional"] = True
    return config


def _cross_checked_comp(index, *, days=30, price=1_600_000):
    comparable = _comp(index, days=days, price=price)
    sources = [
        SourceRecord(
            property_id=comparable.property_id or comparable.comparable_id,
            field_name="sold_history",
            source_url=f"local://sold/a/{comparable.comparable_id}",
            source_type=SourceType.MLS,
            source_registry_id="source:sold-feed-a",
            retrieved_at=OBSERVED_AT,
            raw_value=price,
            normalized_value=price,
            evidence_status=EvidenceStatus.CONFIRMED,
        ),
        SourceRecord(
            property_id=comparable.property_id or comparable.comparable_id,
            field_name="sold_history",
            source_url=f"local://sold/b/{comparable.comparable_id}",
            source_type=SourceType.MLS,
            source_registry_id="source:sold-feed-b",
            retrieved_at=OBSERVED_AT,
            raw_value=price,
            normalized_value=price,
            evidence_status=EvidenceStatus.CONFIRMED,
        ),
    ]
    return comparable.model_copy(update={"source_records": sources})


def test_comparable_engine_uses_180_day_sold_median_and_dislocation():
    result = evaluate_comparables(
        _subject(transaction_price=1_500_000),
        [_cross_checked_comp(index, price=1_600_000 + index * 10_000) for index in range(6)],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_window_days == 180
    assert result.comp_count == 6
    assert result.expected_market_value == result.median_sold_price
    assert result.relative_affordability_index == result.property.asking / result.median_sold_price
    assert result.price_dislocation_pct == result.property.asking / result.expected_market_value - 1
    assert result.property.land_value_proxy == 1_050_000


def test_configured_cross_check_ids_cannot_fabricate_independent_sold_sources():
    result = evaluate_comparables(
        _subject(),
        [_comp(index) for index in range(5)],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_count == 5
    assert result.comp_confidence == "LOW_COMP_CONFIDENCE"
    assert result.expected_market_value is None


def test_five_sold_rows_from_one_registry_are_not_high_confidence():
    result = evaluate_comparables(
        _subject(),
        [_comp(index) for index in range(5)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_count == 5
    assert result.comp_confidence == "LOW_COMP_CONFIDENCE"
    assert result.expected_market_value is None


def test_single_source_mode_exposes_provisional_valuation_for_manual_followup():
    result = evaluate_comparables(
        _subject(),
        [_comp(index, price=1_600_000 + index * 10_000) for index in range(5)],
        _single_source_provisional_config(),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_confidence == "PROVISIONAL_SINGLE_SOURCE"
    assert result.neighbourhood_benchmark_confidence == "PROVISIONAL_SINGLE_SOURCE"
    assert result.valuation_status == "PROVISIONAL"
    assert result.manual_verification_required is True
    assert result.expected_market_value == result.median_sold_price
    assert result.relative_affordability_index is not None
    assert result.price_dislocation_pct is not None
    assert result.property.comparable_valuation_status == "PROVISIONAL"
    assert result.property.manual_verification_required is True


def test_comparable_stage_preserves_an_existing_listing_manual_gate():
    subject = _subject().model_copy(update={
        "manual_verification_required": True,
        "manual_verification_reasons": ["LISTING_SOURCE_CROSS_CHECK_DEFERRED"],
        "data_verification_status": "PROVISIONAL_SINGLE_SOURCE",
    })
    result = evaluate_comparables(
        subject,
        [_cross_checked_comp(index) for index in range(5)],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_confidence == "HIGH_COMP_CONFIDENCE"
    assert result.valuation_status == "VERIFIED"
    assert result.manual_verification_required is True
    assert result.manual_verification_reasons == ["LISTING_SOURCE_CROSS_CHECK_DEFERRED"]
    assert result.property.manual_verification_required is True


def test_price_safety_margin_is_positive_only_when_asking_is_below_expected_value():
    result = evaluate_comparables(
        _subject(asking=1_500_000),
        [_cross_checked_comp(index) for index in range(5)],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.price_dislocation_pct == result.property.price_dislocation_pct
    assert result.property.price_safety_margin_pct == -result.price_dislocation_pct


def test_comparable_row_source_metadata_cannot_add_an_untrusted_cross_check_source():
    rows = []
    for index in range(5):
        row = _comp(index).model_dump(mode="python")
        row["source_registry_id"] = "source:spoofed-feed"
        row["source_type"] = "OFFICIAL_GOV"
        rows.append(row)

    result = evaluate_comparables(
        _subject(),
        rows,
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    source_ids = {
        source.source_registry_id
        for comp in result.selected_comparables
        for source in comp.source_records
        if source.source_registry_id
    }
    assert "source:spoofed-feed" not in source_ids
    assert source_ids == set()


def test_comparable_engine_falls_back_to_365_days_and_marks_low_sample():
    result = evaluate_comparables(
        _subject(transaction_price=1_500_000),
        [_comp(index, days=200 + index * 5) for index in range(4)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_window_days == 365
    assert result.comp_count == 4
    assert result.comp_confidence == "LOW_COMP_CONFIDENCE"
    assert result.expected_market_value is None
    assert result.property.land_value_proxy == 1_050_000


def test_listing_asking_is_not_used_as_subject_transaction_value():
    result = evaluate_comparables(
        _subject(),
        [_comp(index) for index in range(5)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.property.land_value_proxy is None
    assert result.property.adjusted_land_price_per_sqft is None


def test_comparable_windows_are_read_from_config():
    config = {
        "comparables": {
            "min_reliable_comps": 5,
            "lot_tolerance_pct": 0.20,
            "primary_window_days": 90,
            "fallback_window_days": 240,
        }
    }
    result = evaluate_comparables(
        _subject(),
        [_comp(index, days=120 + index) for index in range(4)],
        config,
        observed_at=OBSERVED_AT,
    )

    assert result.comp_window_days == 240


def test_comparable_neighbourhood_lot_distribution_sets_subject_percentile():
    comps = [
        _comp(index, price=1_600_000 + index * 10_000, lot_area_sqft=4000 + index * 500)
        for index in range(5)
    ]

    result = evaluate_comparables(
        _subject(lot_area_sqft=5000),
        comps,
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.property.lot_size_percentile_in_neighbourhood == 60.0


def test_dict_comparable_retains_sold_history_provenance():
    row = _comp(1).model_dump(mode="python")
    row["source_url"] = "local://sold/comp-1"
    row["source_type"] = "MLS"
    config = _config()
    config["comparables"] = {**config["comparables"], "source_registry_id": "source:test-comps"}
    config["source_registry"].append(
        {
            "source_registry_id": "source:test-comps",
            "name": "test sold comps",
            "source_type": "MLS",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        }
    )
    result = evaluate_comparables(_subject(), [row], config, observed_at=OBSERVED_AT)

    assert result.selected_comparables[0].source_records
    assert result.selected_comparables[0].source_records[0].field_name == "sold_history"
    assert result.selected_comparables[0].source_records[0].source_registry_id == "source:test-comps"


def test_typed_comparable_without_provenance_gets_a_visible_sold_history_record():
    result = evaluate_comparables(_subject(), [_comp(1)], _config(), observed_at=OBSERVED_AT)

    source = result.selected_comparables[0].source_records[0]
    assert source.field_name == "sold_history"
    assert source.source_url == "UNKNOWN"


def test_typed_comparable_without_observed_at_uses_run_timestamp():
    result = evaluate_comparables(_subject(), [_comp(1)], _config(), observed_at=OBSERVED_AT)

    comparable = result.selected_comparables[0]
    assert comparable.observed_at == OBSERVED_AT
    assert comparable.source_records[0].retrieved_at == OBSERVED_AT


def test_missing_subject_context_is_not_treated_as_a_reliable_comparable():
    row = _comp(1).model_dump(mode="python")
    row.pop("neighbourhood")
    row.pop("zoning")
    row.pop("road_exposure_level")

    result = evaluate_comparables(_subject(), [row], _config(), observed_at=OBSERVED_AT)

    assert result.comp_count == 0
    reasons = {reason for comp in result.excluded_comparables for reason in comp.exclusion_reasons}
    assert {"NEIGHBOURHOOD_UNKNOWN", "ZONING_UNKNOWN", "ROAD_EXPOSURE_UNKNOWN"} <= reasons


def test_comparable_context_rejects_material_frontage_and_depth_mismatch():
    result = evaluate_comparables(
        _subject(),
        [_comp(1, frontage_ft=25, depth_ft=70)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.selected_comparables == []
    reasons = set(result.excluded_comparables[0].exclusion_reasons)
    assert {"FRONTAGE_MISMATCH", "DEPTH_MISMATCH"} <= reasons


def test_comparable_engine_excludes_listing_only_conflicts_and_mismatched_context():
    comps = [
        _comp(1),
        _comp(2, sold_price=None),
        _comp(3, conflict=True),
        _comp(4, reliable=False),
        _comp(5, zoning="R1-2"),
        _comp(6, road_exposure_level=5),
        _comp(7),
    ]

    result = evaluate_comparables(_subject(), comps, _config(), observed_at=OBSERVED_AT)

    assert result.comp_count == 2
    assert result.expected_market_value is None
    reasons = {reason for comp in result.excluded_comparables for reason in comp.exclusion_reasons}
    assert {"NOT_SOLD", "CONFLICT", "UNRELIABLE", "ZONING_MISMATCH", "ROAD_EXPOSURE_MISMATCH"} <= reasons


def test_land_proxy_stays_unknown_without_explicit_building_value():
    result = evaluate_comparables(
        _subject(current_use_building_value=None),
        [_comp(index) for index in range(5)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.property.land_value_proxy is None
    assert result.property.adjusted_land_price_per_sqft is None


def test_neighbourhood_benchmark_is_separate_from_like_for_like_selection():
    close = [
        _cross_checked_comp(index, price=1_600_000 + index * 10_000)
        for index in range(5)
    ]
    broader_neighbourhood = [
        _cross_checked_comp(
            10 + index,
            price=2_100_000 + index * 10_000,
        ).model_copy(update={
            "lot_area_sqft": 7000,
            "frontage_ft": 44,
            "depth_ft": 140,
        })
        for index in range(3)
    ]

    result = evaluate_comparables(
        _subject(),
        [*close, *broader_neighbourhood],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_count == 5
    assert result.neighbourhood_benchmark_count == 8
    assert result.neighbourhood_comparable_median != result.median_sold_price
    assert result.relative_affordability_index == (
        result.property.asking / result.neighbourhood_comparable_median
    )


def test_configured_assessment_improvement_derives_current_use_building_value():
    config = _config(cross_check=True)
    config["comparables"]["building_value_proxy"] = {
        "method": "assessment_improvement",
    }

    result = evaluate_comparables(
        _subject(
            current_use_building_value=None,
            estimated_current_use_building_value=None,
            assessment_improvement=350_000,
            transaction_price=1_500_000,
        ),
        [_cross_checked_comp(index) for index in range(5)],
        config,
        observed_at=OBSERVED_AT,
    )

    assert result.current_use_building_value_method == "assessment_improvement"
    assert result.property.current_use_building_value == 350_000
    assert result.property.land_value_proxy == 1_150_000
    assert result.property.adjusted_land_price_per_sqft == 230


def test_conflicting_independent_sold_prices_are_excluded_before_medians():
    conflicting = _cross_checked_comp(0)
    conflicting_sources = list(conflicting.source_records)
    conflicting_sources[1] = conflicting_sources[1].model_copy(update={
        "raw_value": 1_700_000,
        "normalized_value": 1_700_000,
    })
    conflicting = conflicting.model_copy(update={"source_records": conflicting_sources})

    result = evaluate_comparables(
        _subject(),
        [conflicting, *[_cross_checked_comp(index) for index in range(1, 5)]],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.comp_count == 4
    assert result.comparable_conflict_count == 1
    assert result.comparable_conflict_ids == ["comp-0"]
    assert all(item.comparable_id != "comp-0" for item in result.selected_comparables)
    reasons = {
        reason
        for item in result.excluded_comparables
        for reason in item.exclusion_reasons
    }
    assert "SOLD_PRICE_CONFLICT" in reasons


def test_subject_transaction_history_conflict_blocks_land_proxy():
    result = evaluate_comparables(
        _subject(
            transaction_price=1_500_000,
            transaction_history_conflict=True,
        ),
        [_cross_checked_comp(index) for index in range(5)],
        _config(cross_check=True),
        observed_at=OBSERVED_AT,
    )

    assert result.property.land_value_proxy is None
    assert result.property.adjusted_land_price_per_sqft is None
