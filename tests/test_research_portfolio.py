from __future__ import annotations

from datetime import datetime, timezone

from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.portfolio import (
    audit_portfolio,
    normalized_physical_basis,
    property_key,
    reno_score,
    render_property_report,
    validate_property_reports,
    write_portfolio_reports,
    zoning_scenarios,
)
from northshore_watchdog.research.screening import ScreeningDecision


def _card(**values: object) -> DiscoveryCard:
    defaults = {
        "source_name": "realtor-ca-native",
        "mls_number": "R123",
        "municipality": "Vancouver",
        "asking": 1_500_000,
        "bedrooms": 3,
        "bathrooms": 2,
        "finished_sqft": 1700,
        "lot_area_sqft": 4026,
        "lot_dimensions": "33 x 122 ft",
        "property_type": "Single Family",
        "ownership_type": "Freehold",
        "listing_status": "active",
        "address": "100 MAIN STREET",
        "source_url": "https://www.realtor.ca/real-estate/123/100-main-street-vancouver",
        "year_built": 1960,
        "neighbourhood": "Mount Pleasant",
        "description": "",
        "observed_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
        "raw": {
            "official_assessment_total": 1_700_000,
            "official_assessment_land": 1_200_000,
            "official_assessment_improvement": 500_000,
            "official_assessment_year": 2026,
            "official_current_zoning": "R1-1",
            "official_zoning": "R1-1",
            "official_frontage_ft": 33,
            "official_depth_ft": 122,
            "official_elementary_catchment": "Example Elementary",
            "official_secondary_catchment": "Example Secondary",
            "official_road_class": "E",
            "official_distance_to_major_road_m": 300,
            "official_property_fronts_major_road": False,
            "_official_evidence": [
                {
                    "adapter": "bca-assessment",
                    "status": "SUCCESS",
                    "source_url": "https://official.example/bca",
                    "retrieved_at": "2026-09-07T00:00:00Z",
                    "query_scope": "last_three_full_calendar_years",
                    "fields": {
                        "assessment_total": 1_700_000,
                        "assessment_land": 1_200_000,
                        "assessment_improvement": 500_000,
                        "frontage_ft": 33,
                        "depth_ft": 122,
                    },
                },
                {
                    "adapter": "municipal-zoning",
                    "status": "SUCCESS",
                    "source_url": "https://official.example/zoning",
                    "retrieved_at": "2026-09-07T00:00:00Z",
                    "fields": {"zoning": "R1-1", "current_zoning": "R1-1"},
                },
                {
                    "adapter": "municipal-road",
                    "status": "SUCCESS",
                    "source_url": "https://official.example/road",
                    "retrieved_at": "2026-09-07T00:00:00Z",
                    "fields": {"road_class": "E", "distance_to_major_road_m": 300},
                },
                {
                    "adapter": "school-catchment",
                    "status": "SUCCESS",
                    "source_url": "https://official.example/school",
                    "retrieved_at": "2026-09-07T00:00:00Z",
                    "fields": {
                        "official_elementary_catchment": "Example Elementary",
                        "official_secondary_catchment": "Example Secondary",
                    },
                },
            ],
        },
    }
    defaults.update(values)
    return DiscoveryCard(**defaults)


def test_audit_reports_all_cards_and_explicit_missing_fields():
    missing = _card(
        mls_number="R124",
        address="101 MAIN STREET",
        raw={
            "official_assessment_total": 1_700_000,
            "official_assessment_land": 1_200_000,
            "official_assessment_improvement": 500_000,
            "_official_evidence": [{
                "adapter": "bca-assessment",
                "status": "SUCCESS",
                "source_url": "https://official.example/bca",
                "retrieved_at": "2026-09-07T00:00:00Z",
            }],
        },
    )
    result = audit_portfolio([missing, _card()])
    assert result["property_count"] == 2
    assert result["missing_official"]["zoning"] == 1
    assert result["missing_official"]["road"] == 1
    assert result["optional_missing"]["sold_history"] == 1
    assert result["sold_history_status"]["CHECKED_NO_RECORDED_SALE_IN_WINDOW"] == 1


def test_property_key_prefers_mls_then_address():
    assert property_key(_card()) == "mls:R123"
    assert property_key(_card(mls_number=None, address=" 100 main street ")) == "address:100 MAIN STREET"


def test_zoning_rule_calculates_legal_area_and_unit_options():
    result = zoning_scenarios(
        zoning="R1-1",
        municipality="City of Vancouver",
        lot_area_sqft=5525,
        frontage_ft=50,
        depth_ft=110,
    )
    assert result["rule_status"] == "OFFICIAL_RULE_APPLIED"
    assert result["maximum_fsr"] == 1.0
    assert result["theoretical_buildable_sqft"] == 5525
    assert result["multiplex_max_units"] >= 5


def test_unknown_zoning_does_not_invent_development_rights():
    result = zoning_scenarios(
        zoning=None,
        municipality="City of Vancouver",
        lot_area_sqft=4000,
        frontage_ft=33,
        depth_ft=122,
    )
    assert result["rule_status"] == "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    assert result["theoretical_buildable_sqft"] is None
    assert result["best_product"] == "UNKNOWN"


def test_official_coverage_requires_successful_provenance():
    card = _card()
    card.raw["_official_evidence"][0]["status"] = "FAILED"
    result = audit_portfolio([card])
    assert result["missing_official"]["bca"] == 1


def test_report_does_not_label_unproven_bca_value_as_official():
    card = _card()
    card.raw["_official_evidence"][0]["status"] = "FAILED"
    report = render_property_report(card, ScreeningDecision(card=card, stage="B_SCORED", status="SCORED"))
    assert "| BCA total | 1700000 | UNVERIFIED |" in report


def test_rt11_uses_official_duplex_base_and_keeps_conditional_path_visible():
    result = zoning_scenarios(
        zoning="RT-11",
        municipality="City of Vancouver",
        lot_area_sqft=5100,
        frontage_ft=50,
        depth_ft=102,
    )
    assert result["rule_status"] == "OFFICIAL_RULE_APPLIED"
    assert result["maximum_fsr"] == 0.75
    assert result["best_product"] == "DUPLEX"
    assert result["maximum_units"] == 3
    assert result["conditional_additional_units"] == 1


def test_rt2_uses_duplex_base_and_does_not_promote_conditional_multiple_path():
    result = zoning_scenarios(
        zoning="RT-2",
        municipality="City of Vancouver",
        lot_area_sqft=5100,
        frontage_ft=33,
        depth_ft=122,
    )
    assert result["maximum_fsr"] == 0.60
    assert result["best_product"] == "DUPLEX"
    assert result["multiplex_max_units"] is None
    assert result["alternative_paths"][0]["product"] == "MULTIPLE_DWELLING"


def test_rt7_has_an_explicit_registered_multiple_product_path():
    result = zoning_scenarios(
        zoning="RT-7",
        municipality="City of Vancouver",
        lot_area_sqft=5000,
        frontage_ft=33,
        depth_ft=122,
    )
    assert result["best_product"] == "MULTIPLEX_6_UNIT_OPTION"
    assert result["multiplex_max_units"] == 6


def test_r3_small_scale_path_delegates_to_r11_thresholds():
    result = zoning_scenarios(
        zoning="R3-1",
        municipality="City of Vancouver",
        lot_area_sqft=4026,
        frontage_ft=33,
        depth_ft=122,
    )
    assert result["rule_status"] == "OFFICIAL_RULE_APPLIED"
    assert result["multiplex_max_units"] == 4
    assert result["theoretical_buildable_sqft"] == 4026


def test_reno_bands_are_exact_and_not_age_only_zero():
    assert reno_score(0) == (100, "MOVE_IN_READY")
    assert reno_score(30000) == (80, "LIGHT_RENO")
    assert reno_score(60000) == (60, "MODERATE_RENO")
    assert reno_score(100000) == (40, "SIGNIFICANT_RENO")
    assert reno_score(150000) == (20, "HEAVY_RENO")
    assert reno_score(150001) == (0, "CURRENT_USE_PASS")


def test_physical_basis_uses_bca_improvement_and_location_residual():
    value = normalized_physical_basis(
        asking=1_875_000,
        bca_improvement=30_200,
        location_residual=563_000,
        lot_area_sqft=4_042.5,
    )
    assert value["market_land_basis"] == 1_844_800
    assert value["normalized_lot_basis"] == 1_281_800
    assert value["normalized_lot_basis_per_sqft"] == 317.0


def test_render_property_report_contains_three_layers_and_evidence_labels():
    card = _card()
    decision = ScreeningDecision(card=card, stage="B_SCORED", status="SCORED")
    report = render_property_report(card, decision)
    assert "## 1. Current Use" in report
    assert "## 2. Redevelopment" in report
    assert "## 3. Confidence and Data Gaps" in report
    assert "OFFICIAL_CONFIRMED" in report
    assert "ESTIMATE" in report
    assert "### Automated property thesis" in report
    assert "Holding view" in report
    assert "Land / redevelopment view" in report
    assert "Price / risk view" in report
    assert "CHECKED_NO_RECORDED_SALE_IN_WINDOW" in report


def test_render_property_report_exposes_frontage_distance_and_six_point_road_split():
    card = _card()
    decision = ScreeningDecision(
        card=card,
        stage="B_SCORED",
        status="SCORED",
        road_class="B",
        property_fronts_major_road=True,
        road_distance_m=4.2,
        road_quality_direct_score=2.5,
        road_quality_exposure_factor=1.0,
        road_quality_effective_score=2.5,
        road_quality_micro_score=1.5,
        micro_location_nonroad_score=7.5,
        current_use_components={
            "micro_location_road": 1.5,
            "micro_location_nonroad": 7.5,
        },
    )

    report = render_property_report(card, decision)

    assert "Direct frontage confirmed" in report
    assert "Road distance used for scoring (m)" in report
    assert "Road quality (direct /10)" in report
    assert "Effective road quality (/10)" in report
    assert "Road quality exposure factor" in report
    assert "Road micro-location score (/6)" in report
    assert "Non-road micro-location score (/9)" in report


def test_portfolio_generation_writes_exactly_one_report_per_card(tmp_path):
    cards = [_card(), _card(mls_number="R124", address="101 MAIN STREET")]
    decisions = [ScreeningDecision(card=card, stage="B_SCORED", status="SCORED") for card in cards]
    paths = write_portfolio_reports(cards, decisions, tmp_path)
    assert len(paths) == len(cards)
    assert len(list(tmp_path.glob("*.md"))) == len(cards)


def test_report_validation_matches_property_keys_not_only_file_count(tmp_path):
    cards = [_card(), _card(mls_number="R124", address="101 MAIN STREET")]
    decisions = [ScreeningDecision(card=card, stage="B_SCORED", status="SCORED") for card in cards]
    paths = write_portfolio_reports(cards, decisions, tmp_path)
    assert validate_property_reports(cards, tmp_path)["valid"] is True

    paths[0].write_text(paths[0].read_text().replace("mls:R123", "mls:STALE"), encoding="utf-8")
    result = validate_property_reports(cards, tmp_path)
    assert result["valid"] is False
    assert result["missing_report_keys"] == ["mls:R123"]
    assert result["unexpected_report_keys"] == ["mls:STALE"]
