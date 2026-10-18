from datetime import datetime, timezone

from northshore_watchdog.research.acquisition import (
    _current_house_gate,
    acquire_from_sources,
    scan_stage1,
)
from northshore_watchdog.research.models import CandidateStatus, EvidenceStatus, PropertyRecord, SourceType
from northshore_watchdog.research.provenance import SourceRegistryEntry
from northshore_watchdog.research.sources import LocalJsonSource, SourceRunStatus


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _config():
    return {
        "market_buckets": {
            "vancouver_east": {
                "municipality": "City of Vancouver",
                "neighbourhoods": ["Knight"],
                "core_range": [1_000_000, 2_000_000],
                "extended_range": [1_000_000, 2_200_000],
            },
            "west_vancouver": {
                "municipality": "District of West Vancouver",
                "neighbourhoods": ["Ambleside"],
                "core_range": [1_500_000, 2_400_000],
                "extended_range": [1_500_000, 2_600_000],
            },
        },
        "geometry": {"vancouver_proper": {"large_lot_min_sqft": 6000}},
        "zoning": {"threshold_tolerance_m": 0.05},
    }


def test_stage1_deduplicates_by_pid_or_address_not_listing_id():
    rows = [
        {
            "property_id": "listing:one",
            "listing_id": "MLS-1",
            "pid": "PID-1",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_450_000,
        },
        {
            "property_id": "listing:two",
            "listing_id": "MLS-2",
            "pid": "PID-1",
            "address": "100 Example Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_425_000,
        },
    ]

    result = scan_stage1(rows, _config(), observed_at=OBSERVED_AT)

    assert result.normalized_count == 1
    assert result.candidates[0].property_id == "pid:PID-1"


def test_stage1_uses_named_market_bucket_and_extended_range():
    result = scan_stage1(
        [
            {
                "address": "200 Example Street",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "ownership_type": "Freehold Non-Strata",
                "asking": 2_100_000,
                "bedrooms": 3,
                "bathrooms": 2.5,
                "finished_sqft": 1500,
            }
        ],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.market_bucket == "vancouver_east"
    assert candidate.range_classification == "EXTENDED"
    assert candidate.included is True
    assert result.included_count == 1


def test_stage1_keeps_below_range_price_dislocation_in_special_research_pool():
    result = scan_stage1(
        [{
            "address": "201 Below Range Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 900_000,
            "bedrooms": 3,
            "bathrooms": 2.5,
            "finished_sqft": 1500,
            "assessment_total": 1_200_000,
            "assessment_land": 1_000_000,
            "original_asking": 1_100_000,
        }],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.range_classification == "SPECIAL"
    assert candidate.included is True
    assert {"PRICE_DISLOCATION", "ASSESSMENT_DISCOUNT", "LAND_ASSESSMENT_DISCOUNT"} <= set(candidate.trigger_tags)


def test_stage1_records_multiple_opportunity_triggers():
    result = scan_stage1(
        [
            {
                "address": "300 Example Street",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "ownership_type": "Freehold Non-Strata",
                "asking": 1_200_000,
                "assessment_total": 1_500_000,
                "assessment_land": 1_250_000,
                "lot_area_sqft": 7000,
                "frontage_ft": 44,
                "depth_ft": 115,
                "dom": 121,
                "original_asking": 1_400_000,
                "zoning_threshold_advantages": ["JUST_ABOVE_ZONING_THRESHOLD"],
                "toa": True,
            }
        ],
        _config(),
        observed_at=OBSERVED_AT,
    )

    tags = set(result.candidates[0].trigger_tags)
    assert {"PRICE_DISLOCATION", "ASSESSMENT_DISCOUNT", "LARGE_LOT", "LONG_DOM", "ZONING_THRESHOLD_ADVANTAGE", "TOA_OPTION"} <= tags


def test_title_gate_excludes_leasehold_and_keeps_unknown_as_verification():
    result = scan_stage1(
        [
            {
                "address": "400 Leasehold Street",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "ownership_type": "Non-Prepaid Lease",
                "asking": 1_100_000,
            },
            {
                "address": "401 Unknown Street",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "asking": 1_100_000,
            },
        ],
        _config(),
        observed_at=OBSERVED_AT,
    )

    by_address = {candidate.address: candidate for candidate in result.candidates}
    leasehold = by_address["400 Leasehold Street"]
    unknown = by_address["401 Unknown Street"]
    assert leasehold.included is False
    assert leasehold.candidate_status is CandidateStatus.REJECT
    assert unknown.included is False
    assert unknown.candidate_status is CandidateStatus.NEED_VERIFICATION


def test_generic_leasehold_is_special_situation_not_normal_candidate():
    result = scan_stage1(
        [{
            "address": "402 Generic Leasehold Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Leasehold",
            "asking": 1_100_000,
        }],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.candidate_status is CandidateStatus.SPECIAL_SITUATION
    assert candidate.included is False
    assert result.research_candidate_count == 1


def test_source_adapter_exception_becomes_visible_partial_input():
    class BrokenSource:
        source_name = "broken-source"
        source_type = SourceType.MLS

        def collect(self, observed_at):
            raise RuntimeError("transport failed")

    result = acquire_from_sources([BrokenSource()], observed_at=OBSERVED_AT)

    assert result.source_runs[0].status is SourceRunStatus.FAILED
    assert "transport failed" in result.source_runs[0].errors[0]
    assert result.records == []


def test_acquisition_keeps_field_level_listing_provenance(tmp_path):
    path = tmp_path / "listing.json"
    path.write_text(
        '[{"address":"700 Provenance Street","municipality":"City of Vancouver",'
        '"asking":1400000,"pid":"PID-PROVENANCE","source_url":"local://listing/700",'
        '"source_type":"MLS"}]',
        encoding="utf-8",
    )

    result = acquire_from_sources(
        [LocalJsonSource(name="listing", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert {item.field_name for item in result.provenance} >= {"listing_snapshot", "asking", "pid"}


def test_official_acquisition_rows_can_establish_official_zoning_evidence(tmp_path):
    path = tmp_path / "official.json"
    path.write_text(
        '[{"address":"701 Official Street","municipality":"City of Vancouver",'
        '"pid":"PID-OFFICIAL","zoning":"R1-1","approved_use":"Single Detached",'
        '"bylaw_version":"official-v1","source_url":"official://zoning/701",'
        '"source_type":"OFFICIAL_GOV"}]',
        encoding="utf-8",
    )

    result = acquire_from_sources(
        [LocalJsonSource(name="official", path=path, source_type=SourceType.OFFICIAL_GOV)],
        observed_at=OBSERVED_AT,
        source_registry={
            "source:official": SourceRegistryEntry(
                source_registry_id="source:official",
                name="official fixture",
                source_type=SourceType.OFFICIAL_GOV,
                authorization_status="NOT_APPLICABLE",
                evidence_status=EvidenceStatus.CONFIRMED,
            )
        },
    )

    assert result.records[0].official_zoning_confirmed is True
    assert result.records[0].official_approved_use_confirmed is True
    assert result.records[0].approved_use.value == "SINGLE_DETACHED"


def test_listing_source_does_not_turn_mls_annual_tax_into_municipal_cost_evidence(tmp_path):
    path = tmp_path / "listing-costs.json"
    path.write_text(
        '[{"address":"702 Listing Street","municipality":"City of Vancouver",'
        '"asking":1400000,"tax":15000,"utilities":1000,"pure_property_tax":14000, '
        '"assessment_total":1600000,"assessment_land":1450000,"assessment_year":2026, '
        '"toa":true,"assembly_score":90, '
        '"road_class":"E","outdoor_noise_exposure":0,"current_school_score":100, '
        '"source_url":"local://listing/702","source_type":"MLS"}]',
        encoding="utf-8",
    )

    result = acquire_from_sources(
        [LocalJsonSource(name="listing-costs", path=path, source_type=SourceType.MLS)],
        observed_at=OBSERVED_AT,
    )

    assert result.records[0].tax is None
    assert result.records[0].utilities is None
    assert result.records[0].pure_property_tax is None
    assert result.records[0].annual_municipal_carrying_cost is None
    assert result.records[0].assessment_total is None
    assert result.records[0].assessment_land is None
    assert result.records[0].assessment_year is None
    assert result.records[0].toa is None
    assert result.records[0].assembly_score is None
    assert result.records[0].road_class is None
    assert result.records[0].outdoor_noise_exposure is None
    assert result.records[0].current_school_score is None


def test_stage1_does_not_fabricate_unseen_universe_records():
    result = scan_stage1([], _config(), observed_at=OBSERVED_AT)

    assert result.normalized_count == 0
    assert result.candidates == []


def _current_house_record(**updates) -> PropertyRecord:
    payload = {
        "property_id": "property:current-house-gate",
        "address": "800 Current House Street",
        "municipality": "City of Vancouver",
        "bedrooms": 3,
        "bathrooms": 2.5,
        "finished_sqft": 1500,
    }
    payload.update(updates)
    return PropertyRecord.model_validate(payload)


def test_current_house_gate_passes_at_default_minimums():
    gate, reasons = _current_house_gate(_current_house_record(), {})

    assert gate == "PASS"
    assert reasons == ["CURRENT_USE_MINIMUMS_MET"]


def test_current_house_gate_rejects_known_value_below_minimum():
    gate, reasons = _current_house_gate(_current_house_record(bedrooms=2), {})

    assert gate == "OUT_OF_SCOPE"
    assert reasons == ["BEDROOMS_BELOW_MINIMUM"]


def test_current_house_gate_routes_any_unknown_value_to_verification():
    gate, reasons = _current_house_gate(_current_house_record(bathrooms=None), {})

    assert gate == "NEED_VERIFICATION"
    assert reasons == ["CURRENT_USE_BATHROOMS_UNKNOWN"]


def _current_house_listing(**updates) -> dict:
    row = {
        "address": "801 Current House Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "ownership_type": "Freehold Non-Strata",
        "asking": 1_450_000,
        "lot_area_sqft": 4_000,
        "frontage_ft": 40,
        "depth_ft": 120,
        "bedrooms": 3,
        "bathrooms": 2.5,
        "finished_sqft": 1500,
    }
    row.update(updates)
    return row


def test_stage1_current_house_gate_passes_and_keeps_detached_candidate_in_scope():
    result = scan_stage1(
        [_current_house_listing()],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.gate_state == "PASS"
    assert candidate.included is True
    assert candidate.candidate_status is CandidateStatus.B_WATCH
    assert "CURRENT_USE_MINIMUMS_MET" in candidate.reason_codes


def test_stage1_current_house_gate_rejects_below_minimum_detached_candidate():
    result = scan_stage1(
        [_current_house_listing(bedrooms=2)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.gate_state == "OUT_OF_SCOPE"
    assert candidate.included is False
    assert candidate.candidate_status is CandidateStatus.D_REJECT
    assert "BEDROOMS_BELOW_MINIMUM" in candidate.reason_codes


def test_stage1_current_house_gate_routes_unknown_detached_candidate_to_verification():
    result = scan_stage1(
        [_current_house_listing(finished_sqft=None)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.gate_state == "NEED_VERIFICATION"
    assert candidate.included is False
    assert candidate.candidate_status is CandidateStatus.NEED_VERIFICATION
    assert "CURRENT_USE_FINISHED_SQFT_UNKNOWN" in candidate.reason_codes


def test_known_current_house_failure_overrides_unconfirmed_title():
    result = scan_stage1(
        [_current_house_listing(ownership_type=None, bedrooms=2)],
        _config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.gate_state == "OUT_OF_SCOPE"
    assert candidate.candidate_status is CandidateStatus.D_REJECT
    assert "BEDROOMS_BELOW_MINIMUM" in candidate.reason_codes


def test_non_vancouver_bucket_does_not_use_vancouver_area_cutoff_without_percentile():
    config = _config()
    config["market_buckets"]["north_vancouver"] = {
        "municipality": "District of North Vancouver",
        "neighbourhoods": ["Lynn Valley"],
        "core_range": [1_100_000, 2_200_000],
        "extended_range": [1_100_000, 2_400_000],
    }
    result = scan_stage1(
        [{
            "address": "500 Local Street",
            "municipality": "District of North Vancouver",
            "neighbourhood": "Lynn Valley",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_500_000,
            "lot_area_sqft": 9000,
        }],
        config,
        observed_at=OBSERVED_AT,
    )

    assert "LARGE_LOT" not in result.candidates[0].trigger_tags


def test_same_address_with_different_pids_is_a_conflict_not_two_clean_candidates():
    rows = [
        {
            "pid": "PID-A",
            "address": "600 Collision Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
            "lot_area_sqft": 5000,
        },
        {
            "pid": "PID-B",
            "address": "600 COLLISION ST",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
            "lot_area_sqft": 5000,
        },
    ]

    result = scan_stage1(rows, _config(), observed_at=OBSERVED_AT)

    assert len(result.records) == 2
    assert result.conflicts
    assert {item.conflict_type for item in result.conflicts} == {"PID_ADDRESS_COLLISION"}
    assert all(record.data_conflict for record in result.records)


def test_address_collision_conflict_id_is_stable_when_source_order_changes():
    rows = [
        {
            "pid": "PID-ORDER-A",
            "address": "603 Order Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
        },
        {
            "pid": "PID-ORDER-B",
            "address": "603 Order Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
        },
    ]

    forward = scan_stage1(rows, _config(), observed_at=OBSERVED_AT)
    reverse = scan_stage1(list(reversed(rows)), _config(), observed_at=OBSERVED_AT)

    assert {item.conflict_id for item in forward.conflicts} == {item.conflict_id for item in reverse.conflicts}


def test_same_pid_with_different_addresses_is_an_address_conflict():
    rows = [
        {
            "pid": "PID-SAME",
            "address": "601 First Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
        },
        {
            "pid": "PID-SAME",
            "address": "602 Second Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_400_000,
        },
    ]

    result = scan_stage1(rows, _config(), observed_at=OBSERVED_AT)

    assert len(result.records) == 1
    assert any(item.field_name == "address" for item in result.conflicts)
    assert result.records[0].data_conflict is True
