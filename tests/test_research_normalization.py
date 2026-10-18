from datetime import datetime, timezone

from northshore_watchdog.config import load_config
from northshore_watchdog.models import Listing
from northshore_watchdog.research.models import ApprovedUse, EvidenceStatus, OwnershipType, SourceType
from northshore_watchdog.research.normalization import (
    NormalizationResult,
    calculate_lot_geometry_score,
    calculate_lot_size_percentile,
    calculate_road_exposure,
    classify_approved_use,
    classify_defects,
    classify_ownership,
    detect_material_conflicts,
    detect_material_conflicts_from_provenance,
    detect_zoning_threshold_advantage,
    normalize_property,
    normalize_address,
    resolve_property_identity,
)
from northshore_watchdog.research.provenance import SourceRecord, SourceRegistryEntry


def test_legacy_listing_adapts_without_treating_listing_id_as_property_identity():
    listing = Listing(
        source="realtor",
        source_id="listing-1",
        address="123 Example Street",
        city="North Vancouver",
        neighbourhood="Queensbury",
        property_type="detached house",
        tenure="freehold",
        listing_status="active",
        list_price=1_500_000,
        house_size_sqft=1800,
        lot_size_sqft=6000,
        year_built=1975,
    )

    result = normalize_property(listing)

    assert isinstance(result, NormalizationResult)
    assert result.property.asking == 1_500_000
    assert result.property.finished_sqft == 1800
    assert result.property.property_id != "listing-1"


def test_normalization_derives_square_metres_and_assessment_labels():
    result = normalize_property({
        "address": "123 Example Street",
        "municipality": "City of North Vancouver",
        "asking": 900_000,
        "lot_area_sqft": 7500,
        "assessment_total": 1_000_000,
        "assessment_land": 960_000,
        "assessment_improvement": 40_000,
    })

    assert result.property.lot_area_m2 == 696.77
    assert result.property.land_ratio == 0.96
    assert "LAND_DOMINANT" in result.property.assessment_labels
    assert "HIGHLY_LAND_DOMINANT" in result.property.assessment_labels
    assert "ASK_10PCT_BELOW_ASSESSMENT" in result.property.assessment_labels


def test_material_conflicts_ignore_address_suffix_and_numeric_formatting_but_keep_real_mismatch():
    assert not detect_material_conflicts(
        "property:geometry",
        {
            "address": {
                "listing": "5322 Culloden Street",
                "assessment": "5322 CULLODEN ST VANCOUVER V5W 3R6",
            },
            "bathrooms": {"listing": 2.0, "assessment": "2"},
        },
    )
    assert detect_material_conflicts(
        "property:geometry",
        {"bedrooms": {"listing": 3, "assessment": 4}},
    )[0].field_name == "bedrooms"


def test_two_addresses_with_one_pid_are_not_two_tradable_properties():
    result = normalize_property({
        "address": "3034 Example Street",
        "municipality": "City of Vancouver",
        "secondary_civic_addresses": ["3036 Example Street"],
        "pid": "012-345-678",
        "number_of_pids": 1,
        "property_type": "detached house",
    })

    assert result.property.single_title_multi_address is True
    assert result.property.independently_tradable is False
    assert result.property.approved_use is ApprovedUse.UNKNOWN


def test_identity_prefers_pid_and_supports_multiple_pids():
    assert resolve_property_identity(
        address="123 Example Street",
        municipality="City of Vancouver",
        pids=["P2", "P1"],
    ) == "pid:P1+P2"
    assert resolve_property_identity(
        address="123 Example Street",
        municipality="City of Vancouver",
        pids=[],
    ).startswith("property:")


def test_address_suffix_aliases_keep_pidless_property_identity_stable():
    left = normalize_address("123 Stable Street, City of Vancouver")
    right = normalize_address("123 STABLE ST, CITY OF VANCOUVER")

    assert left == right
    first = normalize_property({
        "address": "123 Stable Street",
        "municipality": "City of Vancouver",
    }).property
    second = normalize_property({
        "address": "123 STABLE ST",
        "municipality": "City of Vancouver",
    }).property
    assert first.property_id == second.property_id


def test_listing_reduction_is_derived_without_confusing_ask_and_sale():
    record = normalize_property({
        "address": "123 Reduction Street",
        "municipality": "City of Vancouver",
        "asking": 1_500_000,
        "original_asking": 2_000_000,
        "previous_sale_prices": [1_200_000],
    }).property

    assert record.price_reduction_amount == 500_000
    assert record.price_reduction_pct == -0.25
    assert record.previous_sale_prices == [1_200_000]
    no_reduction = normalize_property({
        "address": "124 Reduction Street",
        "municipality": "City of Vancouver",
        "asking": 1_500_000,
        "original_asking": 1_500_000,
    }).property
    assert no_reduction.price_reduction_amount is None
    assert no_reduction.price_reduction_pct is None


def test_common_schema_aliases_normalize_toa_skytrain_and_pid_count():
    result = normalize_property({
        "address": "1 Alias Street",
        "municipality": "City of Vancouver",
        "TOA": True,
        "TOA_tier": "Tier 2",
        "distance_to_SkyTrain": 420,
        "number_of_PIDs": 1,
    }).property

    assert result.toa is True
    assert result.toa_tier == "Tier 2"
    assert result.distance_to_skytrain_m == 420
    assert result.number_of_pids == 1


def test_local_lot_percentile_is_computed_from_neighbourhood_distribution():
    assert calculate_lot_size_percentile(5000, [3000, 4000, 5000, 6000]) == 75.0
    assert calculate_lot_size_percentile(None, [3000, 4000]) is None


def test_road_class_changes_exposure_at_the_same_distance():
    major = calculate_road_exposure("A", 150, False)
    quiet = calculate_road_exposure("E", 150, False)

    assert major > quiet


def test_title_and_approved_use_classification_is_explicit():
    assert classify_ownership("freehold non-strata") is OwnershipType.FREEHOLD_NON_STRATA
    assert classify_ownership("freehold") is OwnershipType.UNKNOWN
    assert classify_ownership("bare land strata") is OwnershipType.BARE_LAND_STRATA
    assert classify_ownership("partial interest court sale") is OwnershipType.PARTIAL_INTEREST
    assert classify_ownership(None) is OwnershipType.UNKNOWN
    assert classify_approved_use("two-family dwelling") is ApprovedUse.TWO_FAMILY_DWELLING
    assert classify_approved_use("not confirmed") is ApprovedUse.UNKNOWN


def test_multi_address_does_not_accept_unverified_approved_use():
    result = normalize_property({
        "address": "3034 Example Street",
        "municipality": "City of Vancouver",
        "secondary_civic_addresses": ["3036 Example Street"],
        "pid": "012-345-678",
        "number_of_pids": 1,
        "approved_use": "two-family dwelling",
    })

    assert result.property.approved_use is ApprovedUse.UNKNOWN


def test_lot_geometry_scores_regular_deep_lane_lot_above_shallow_irregular_lot():
    config = load_config("config/research.yaml")
    strong = normalize_property({
        "address": "1 Strong Lot",
        "municipality": "City of Vancouver",
        "lot_area_sqft": 4026,
        "frontage_ft": 33,
        "depth_ft": 122,
        "lot_shape": "regular_rectangular",
        "lane_access": True,
        "corner_lot": False,
        "slope": "flat",
    }).property
    weak = normalize_property({
        "address": "2 Weak Lot",
        "municipality": "City of Vancouver",
        "lot_area_sqft": 2970,
        "frontage_ft": 45,
        "depth_ft": 66,
        "lot_shape": "irregular",
        "lane_access": False,
        "corner_lot": True,
        "slope": "steep",
    }).property

    assert calculate_lot_geometry_score(strong, config) > calculate_lot_geometry_score(weak, config)


def test_road_exposure_combines_fronting_and_distance():
    assert calculate_road_exposure("B", 0, True) == 5
    assert calculate_road_exposure("B", 40, False) == 4
    assert calculate_road_exposure("B", 75, False) == 3
    assert calculate_road_exposure("E", 250, False) == 1


def test_just_above_official_frontage_threshold_gets_advantage_tag():
    tags = detect_zoning_threshold_advantage(13.41, 13.40, tolerance_m=0.05)

    assert tags == ["JUST_ABOVE_ZONING_THRESHOLD", "ZONING_THRESHOLD_ADVANTAGE"]
    assert detect_zoning_threshold_advantage(13.20, 13.40, tolerance_m=0.05) == []


def test_normalization_derives_threshold_tag_when_configured_frontage_is_just_above_minimum():
    result = normalize_property({
        "address": "1 Threshold Street",
        "municipality": "City of Vancouver",
        "frontage_ft": 44.0,
        "minimum_frontage": 13.4,
    }, config=load_config("config/research.yaml"))

    assert "JUST_ABOVE_ZONING_THRESHOLD" in result.property.zoning_threshold_advantages


def test_assessment_vs_total_is_available_even_when_land_assessment_is_unknown():
    result = normalize_property({
        "address": "1 Assessment Street",
        "municipality": "City of Vancouver",
        "asking": 900_000,
        "assessment_total": 1_000_000,
    })

    assert result.property.asking_vs_total_assessment == -0.1
    assert "ASK_10PCT_BELOW_ASSESSMENT" in result.property.assessment_labels


def test_material_conflicts_are_persisted_for_mismatched_source_values():
    conflicts = detect_material_conflicts("property:123", {
        "pid": {"official": "P1", "aggregator": "P2"},
        "year_built": {"mls": 1950, "aggregator": 1975},
    })

    assert {item.field_name for item in conflicts} == {"pid", "year_built"}
    assert all(item.resolution_status == "OPEN" for item in conflicts)
    assert all(len(item.conflicting_sources) == 2 for item in conflicts)


def test_material_conflict_detection_treats_enum_and_serialized_value_as_equal():
    conflicts = detect_material_conflicts("property:123", {
        "ownership_type": {
            "projection": OwnershipType.FREEHOLD_NON_STRATA,
            "official": "FREEHOLD_NON_STRATA",
        }
    })

    assert conflicts == []


def test_listing_history_conflict_preserves_both_sources_for_review():
    conflicts = detect_material_conflicts_from_provenance(
        "property:history-conflict",
        [
            SourceRecord(
                property_id="property:history-conflict",
                field_name="listing_history",
                source_url="https://mls.example/one",
                source_type="MLS",
                retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                normalized_value="MLS-1 / 1,500,000",
            ),
            SourceRecord(
                property_id="property:history-conflict",
                field_name="listing_history",
                source_url="https://mls.example/two",
                source_type="AGGREGATOR",
                retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
                normalized_value="MLS-1 / 1,250,000",
            ),
        ],
    )

    assert len(conflicts) == 1
    assert conflicts[0].field_name == "listing_history"
    assert conflicts[0].conflicting_sources == [
        "https://mls.example/one",
        "https://mls.example/two",
    ]


def test_defects_are_separated_into_permanent_and_fixable():
    permanent, fixable = classify_defects(
        "Major arterial frontage, knob and tube, old kitchen, unfinished basement",
        load_config("config/research.yaml"),
    )

    assert "major arterial frontage" in permanent
    assert "knob and tube" not in fixable
    assert "old kitchen" in fixable
    assert "unfinished basement" in fixable


def test_normalizing_property_record_does_not_drop_explicit_pid_when_pid_list_is_empty():
    from northshore_watchdog.research.models import PropertyRecord

    source = PropertyRecord(
        property_id="pid:explicit",
        address="1 Explicit PID Street",
        municipality="City of Vancouver",
        pid="012-345-678",
    )

    normalized = normalize_property(source).property

    assert normalized.pid == "012-345-678"
    assert normalized.pids == ["012-345-678"]


def test_normalization_does_not_promote_unproven_official_zoning_flags():
    from northshore_watchdog.research.models import PropertyRecord

    source = PropertyRecord(
        property_id="property:untrusted-zoning",
        address="1 Untrusted Zoning Street",
        municipality="City of Vancouver",
        zoning="R1-1",
        maximum_units=6,
        official_zoning_confirmed=True,
        official_zoning_source_url="https://listing.example/zoning",
    )

    normalized = normalize_property(source).property

    assert normalized.zoning == "R1-1"
    assert normalized.maximum_units == 6
    assert normalized.official_zoning_confirmed is not True
    assert normalized.official_zoning_source_url is None


def test_official_claim_requires_registered_confirmed_source_evidence():
    observed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    source = SourceRecord(
        property_id="property:registered-official",
        field_name="zoning",
        source_url="https://city.example/zoning/1",
        source_type=SourceType.OFFICIAL_GOV,
        source_registry_id="source:city-zoning",
        retrieved_at=observed_at,
        raw_value="R1-1",
        normalized_value="R1-1",
        evidence_status=EvidenceStatus.CONFIRMED,
    )
    payload = {
        "property_id": "property:registered-official",
        "address": "1 Registered Official Street",
        "municipality": "City of Vancouver",
        "zoning": "R1-1",
        "maximum_units": 6,
    }

    unregistered = normalize_property(payload, [source]).property
    registered = normalize_property(
        payload,
        [source],
        source_registry={
            "source:city-zoning": SourceRegistryEntry(
                source_registry_id="source:city-zoning",
                name="city zoning",
                source_type=SourceType.OFFICIAL_GOV,
                authorization_status="AUTHORIZED",
                evidence_status=EvidenceStatus.CONFIRMED,
            )
        },
    ).property

    assert unregistered.official_zoning_confirmed is not True
    assert registered.official_zoning_confirmed is True
    assert registered.official_zoning_source_registry_id == "source:city-zoning"

    unconfirmed_registry = normalize_property(
        payload,
        [source.model_copy(update={"evidence_status": EvidenceStatus.UNCONFIRMED})],
        source_registry={
            "source:city-zoning": SourceRegistryEntry(
                source_registry_id="source:city-zoning",
                name="city zoning",
                source_type=SourceType.OFFICIAL_GOV,
                authorization_status="AUTHORIZED",
                evidence_status=EvidenceStatus.UNCONFIRMED,
            )
        },
    ).property
    assert unconfirmed_registry.official_zoning_confirmed is not True
