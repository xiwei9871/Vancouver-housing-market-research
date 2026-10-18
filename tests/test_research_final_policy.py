from datetime import datetime, timezone

from northshore_watchdog.config import load_config
from northshore_watchdog.research.acquisition import scan_stage1
from northshore_watchdog.research.models import CandidateStatus, PropertyRecord
from northshore_watchdog.research.report import build_candidate_card
from northshore_watchdog.research.scoring import score_property


OBSERVED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _real_config() -> dict:
    return load_config("config/research-real-single-source.yaml")


def _listing(**updates) -> dict:
    row = {
        "address": "100 Policy Test Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "ownership_type": "Freehold Non-Strata",
        "asking": 1_450_000,
    }
    row.update(updates)
    return row


def test_real_profile_uses_final_hard_search_ranges_and_asset_scope():
    config = _real_config()

    assert config["market_buckets"]["vancouver_west"]["core_range"] == [1_500_000, 2_000_000]
    assert config["market_buckets"]["west_vancouver"]["extended_range"] == [1_500_000, 2_000_000]
    assert config["market_buckets"]["city_north_vancouver"]["core_range"] == [1_000_000, 1_600_000]
    assert config["market_buckets"]["district_north_vancouver"]["extended_range"] == [1_000_000, 1_600_000]
    assert config["market_buckets"]["vancouver_east"]["core_range"] == [1_000_000, 1_600_000]
    assert config["research_policy"]["hard_price_range"] is True
    assert config["research_policy"]["primary_asset_classes"] == ["DETACHED_HOUSE"]
    assert config["research_policy"]["supplementary_asset_classes"] == ["DUPLEX"]


def test_real_profile_enables_bounded_official_enrichment():
    config = _real_config()

    assert config["official_enrichment"]["enabled"] is True
    assert {
        source["name"]
        for source in config["official_enrichment"]["sources"]
    } >= {"bca-assessment", "municipal-zoning", "municipal-road", "school-catchment", "sold-history"}


def test_discovery_profile_removes_deep_research_cap_for_filtered_cards():
    config = load_config("config/research-discovery.yaml")

    assert config["official_enrichment"]["enabled"] is True
    assert config["official_enrichment"]["max_properties_per_run"] == 0
    assert config["official_enrichment"]["discovery_batch_size"] == 10
    assert config["discovery_official_enrichment"]["source_names"] == [
        "bca-assessment",
        "municipal-zoning",
        "municipal-road",
        "school-catchment",
        "school-performance",
    ]


def test_mls_first_profile_inherits_final_non_west_price_ranges():
    config = load_config("config/research-mls-first.yaml")

    assert config["market_buckets"]["vancouver_east"]["extended_range"] == [1_000_000, 1_600_000]
    assert config["market_buckets"]["vancouver_value"]["core_range"] == [1_000_000, 1_600_000]
    assert config["market_buckets"]["burnaby_south"]["extended_range"] == [1_000_000, 1_600_000]


def test_final_profile_does_not_rescue_out_of_range_listings_as_special_assets():
    result = scan_stage1(
        [_listing(asking=1_700_000)],
        _real_config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.range_classification == "OUT_OF_RANGE"
    assert candidate.included is False
    assert "OUTSIDE_EXTENDED_SEARCH_RANGE" in candidate.reason_codes
    assert "SPECIAL_ASSET_TRIGGER_OUTSIDE_SEARCH_RANGE" not in candidate.reason_codes
    assert result.records[0].range_classification == "OUT_OF_RANGE"
    assert result.records[0].research_scope == "OUT_OF_RANGE"


def test_final_profile_separates_strata_duplex_from_primary_detached_house_scope():
    result = scan_stage1(
        [_listing(
            address="1 1450 E 30TH AVENUE",
            asking=1_500_000,
            source_capture_note="Public page showed a strata duplex-style detached unit; verify title.",
        )],
        _real_config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.record.research_asset_class == "STRATA_DUPLEX"
    assert candidate.included is False
    assert "STRATA_DUPLEX_OUT_OF_PRIMARY_SCOPE" in candidate.reason_codes
    assert candidate.candidate_status is CandidateStatus.SPECIAL_SITUATION
    assert result.special_situation_count == 1


def test_westside_small_lot_keeps_single_house_rebuild_option_without_multifamily_gate():
    result = scan_stage1(
        [_listing(
            address="2950 Westside Rebuild Street",
            neighbourhood="Point Grey",
            asking=1_800_000,
            lot_area_sqft=2_950,
            frontage_ft=25,
            depth_ft=89,
        )],
        _real_config(),
        observed_at=OBSERVED_AT,
    )

    candidate = result.candidates[0]
    assert candidate.record.research_scope == "HOUSE_REBUILD_ONLY"
    assert "HOUSE_REBUILD_ONLY" in candidate.record.opportunity_tags


def test_score_and_report_show_dimension_labels_and_assessment_anchor_status():
    config = load_config("config/research.yaml")
    from northshore_watchdog.research.normalization import normalize_property

    record = normalize_property({
        "property_id": "property:score-policy",
        "address": "100 Score Policy Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "ownership_type": "FREEHOLD_NON_STRATA",
        "asking": 1_200_000,
        "assessment_year": 2026,
        "assessment_total": 1_500_000,
        "assessment_land": 1_100_000,
        "assessment_improvement": 400_000,
        "lot_area_sqft": 4_000,
        "frontage_ft": 33,
        "depth_ft": 100,
        "finished_sqft": 1_800,
        "bedrooms": 3,
        "bathrooms": 2.5,
    }, config=config).property
    snapshot = score_property(record, config, as_of=OBSERVED_AT, generated_at=OBSERVED_AT)
    card = build_candidate_card(record, snapshot)

    assert record.assessment_valuation_status == "ASSESSMENT_ANCHOR_ONLY"
    assert snapshot.dimensions["price_safety_margin"].label
    assert "Price / Safety Margin:" in card
    assert "Assessment Anchor Status: ASSESSMENT_ANCHOR_ONLY" in card
    assert "Total Score Label:" in card


def test_final_lot_geometry_thresholds_are_explicit_and_scored():
    config = load_config("config/research.yaml")
    from northshore_watchdog.research.normalization import (
        calculate_depth_score,
        calculate_frontage_score,
        calculate_lot_area_score,
        normalize_property,
    )

    def normalized(area, frontage, depth):
        return normalize_property(
            _listing(
                lot_area_sqft=area,
                frontage_ft=frontage,
                depth_ft=depth,
            ),
            config=config,
        ).property

    at_standard = normalized(4_000, 33, 100)
    below_area = normalized(2_999, 33, 100)
    full_area = normalized(6_000, 50, 125)

    assert calculate_lot_area_score(at_standard, config) == 80
    assert calculate_lot_area_score(below_area, config) == 0
    assert calculate_lot_area_score(full_area, config) == 100
    assert calculate_frontage_score(at_standard) == 60
    assert calculate_frontage_score(full_area) == 100
    assert calculate_depth_score(at_standard) == 60
    assert calculate_depth_score(full_area) == 100


def test_frontage_depth_dimension_uses_final_gate_scores():
    config = load_config("config/research.yaml")
    from northshore_watchdog.research.normalization import normalize_property

    record = normalize_property(
        _listing(
            lot_area_sqft=4_000,
            frontage_ft=33,
            depth_ft=100,
        ),
        config=config,
    ).property
    snapshot = score_property(record, config, as_of=OBSERVED_AT, generated_at=OBSERVED_AT)

    component = snapshot.dimensions["land_lot_title"].components["frontage_depth"]
    assert component.points == 2.4
