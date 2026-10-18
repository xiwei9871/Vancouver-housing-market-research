from datetime import datetime, timezone

from northshore_watchdog.research.models import (
    CandidateStatus,
    EvidenceStatus,
    PropertyRecord,
    RedevelopmentScenario,
    ScenarioType,
    ScoreSnapshot,
)
from northshore_watchdog.research.enrichment import EnrichedPropertyResult, EnrichmentResult
from northshore_watchdog.research.provenance import SourceRecord
from northshore_watchdog.research.redevelopment import RedevelopmentAnalysis
from northshore_watchdog.research.report import (
    build_candidate_card,
    build_weekly_report,
    summarize_research_run,
)
from northshore_watchdog.research.state import StateResolution


def _record(**updates) -> PropertyRecord:
    values = {
        "property_id": "report:1",
        "address": "123 Report Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "ownership_type": "FREEHOLD_NON_STRATA",
        "pid": "012-345-678",
        "asking": 1_500_000,
        "original_asking": 1_650_000,
        "dom": 75,
        "price_reduction_pct": -0.09,
        "assessment_total": 1_700_000,
        "assessment_land": 1_400_000,
        "assessment_improvement": 300_000,
        "asking_vs_total_assessment": -0.1176,
        "asking_vs_land_assessment": 0.0714,
        "lot_area_sqft": 5000,
        "frontage_ft": 40,
        "depth_ft": 125,
        "lot_shape": "regular_rectangular",
        "lane_access": True,
        "year_built": 1980,
        "finished_sqft": 2100,
        "bedrooms": 4,
        "bathrooms": 3,
        "basement": "finished",
        "zoning": "R1-1",
        "approved_use": "SINGLE_DETACHED",
        "road_class": "B",
        "nearest_major_road": "Knight Street",
        "distance_to_major_road_m": 0,
        "property_fronts_major_road": True,
        "outdoor_noise_exposure": 75,
        "indoor_noise_mitigation_potential": 80,
        "official_elementary_catchment": "Example Elementary",
        "official_secondary_catchment": "Example Secondary",
        "current_school_score": 75,
        "school_future_score": 80,
        "family_location_score": 60,
        "max_duplex_area": 4000,
        "max_realistic_multiplex_units": 4,
        "realistic_buildable_sqft": 4000,
        "renovation_estimate": 150_000,
        "permanent_defects": ["major arterial frontage"],
        "fixable_defects": ["kitchen", "paint"],
        "expected_market_value": 1_700_000,
        "price_dislocation_pct": -0.1176,
        "land_value_proxy": 1_300_000,
        "live_score": 63,
        "land_score": 82,
        "investment_score": 78,
        "total_score": 75,
        "data_confidence_score": 82,
        "data_confidence_band": "Medium",
        "candidate_status": CandidateStatus.B_WATCH,
        "opportunity_tags": ["PRICE_DISLOCATION", "DUPLEX_OPTION", "NOISE_RISK"],
    }
    values.update(updates)
    return PropertyRecord(**values)


def _snapshot(record: PropertyRecord, **updates) -> ScoreSnapshot:
    values = {
        "property_id": record.property_id,
        "generated_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
        "scoring_version": "research-v1",
        "config_hash": "report-config",
        "total_score": record.total_score or 0,
        "live_score": record.live_score or 0,
        "land_score": record.land_score or 0,
        "investment_score": record.investment_score or 0,
        "data_confidence_score": record.data_confidence_score or 0,
        "data_confidence_band": record.data_confidence_band or "Needs Verification",
        "candidate_status": record.candidate_status,
    }
    values.update(updates)
    return ScoreSnapshot(**values)


def _resolution(record: PropertyRecord, **updates) -> StateResolution:
    values = {
        "state": record.candidate_status,
        "recommendation": "WATCHLIST_AND_VERIFY",
        "site_visit_eligible": record.candidate_status is CandidateStatus.B_WATCH,
    }
    values.update(updates)
    return StateResolution(**values)


def test_weekly_report_contains_required_sections_and_summary_counters():
    record = _record()
    report = build_weekly_report(
        [record],
        snapshots={record.property_id: _snapshot(record)},
        resolutions={record.property_id: _resolution(record)},
        run_type="phase1_validation",
        coverage_gaps=["Knight major-road traceable case needs official road verification"],
        generated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    for heading in (
        "## Executive Summary",
        "## Market Changes",
        "## Top New Candidates",
        "## Major Price Reductions",
        "## Watchlist Changes",
        "## Sold / Removed",
        "## Data Conflicts",
        "## Recommended Site Visits",
    ):
        assert heading in report
    for counter in (
        "properties_scanned",
        "stage1_candidates",
        "deep_research_count",
        "A_candidates",
        "B_candidates",
        "C_candidates",
        "D_rejects",
        "NEED_VERIFICATION",
        "SPECIAL_SITUATION",
        "largest_price_reduction",
        "largest_assessment_discount",
        "largest_land_assessment_discount",
        "largest_lot_opportunity",
        "confidence_band_counts",
        "coverage_gaps",
        "priority_case_reviews",
        "run_type=phase1_validation",
    ):
        assert counter in report
    assert "generated_at=2026-08-30T00:00:00+00:00" in report


def test_weekly_report_redevelopment_summary_exposes_all_scenario_assumptions():
    record = _record()
    scenario = RedevelopmentScenario(
        property_id=record.property_id,
        scenario_type=ScenarioType.DUPLEX,
        legal_status="UNKNOWN_REQUIRES_OFFICIAL_REVIEW",
        unit_count=2,
        layout_options=["front/rear", "side-by-side"],
        front_building_noise_buffer_hypothesis=True,
    )
    analysis = RedevelopmentAnalysis(
        property=record,
        scenarios=[scenario],
        observed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    report = build_weekly_report(
        [record],
        snapshots={record.property_id: _snapshot(record)},
        resolutions={record.property_id: _resolution(record)},
        redevelopment_results={record.property_id: analysis},
    )

    assert "## Redevelopment Economics" in report
    assert "legal=UNKNOWN_REQUIRES_OFFICIAL_REVIEW" in report
    assert "Layouts=front/rear, side-by-side" in report
    assert "Noise Buffer Hypothesis=YES" in report


def test_candidate_card_contains_auditable_facts_unknowns_sources_and_thesis():
    record = _record(
        pid=None,
        official_secondary_catchment=None,
        current_school_score=None,
        toa=None,
        toa_status=None,
        building_permits=[{"permit": "BP-1"}],
        occupancy_permits=[{"permit": "OP-1"}],
        secondary_suite_permits=[{"permit": "SP-1"}],
        two_family_dwelling_status="unknown",
        outstanding_permits=[{"permit": "BP-2"}],
        bylaw_violations=[],
    )
    snapshot = _snapshot(record)
    scenario = RedevelopmentScenario(
        property_id=record.property_id,
        scenario_type=ScenarioType.DUPLEX,
        legal_status="UNKNOWN_REQUIRES_OFFICIAL_REVIEW",
        realistic_buildable_sqft=4000,
        average_unit_size=2000,
        likely_buyer="family-oriented half duplex buyer",
    )
    provenance = [
        SourceRecord(
            property_id=record.property_id,
            field_name="asking",
            source_url="https://example.com/listing/123",
            source_type="MLS",
            source_registry_id="source:public-listing",
            retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            raw_value="$1,500,000",
            normalized_value=1_500_000,
        )
    ]
    card = build_candidate_card(
        record,
        snapshot,
        scenarios=[scenario],
        resolution=_resolution(record),
        provenance=provenance,
    )

    for label in (
        "Current Ask",
        "Ownership",
        "PID",
        "Lot",
        "House",
        "Assessment",
        "Listing History",
        "Road / Noise",
        "School",
        "Zoning / Approved Use",
        "Redevelopment Scenarios",
        "Estimated Renovation",
        "Permanent Defects",
        "Fixable Defects",
        "Comparable Value",
        "Neighbourhood Benchmark",
        "Comparable Conflicts Excluded",
        "LIVE SCORE",
        "LAND SCORE",
        "INVESTMENT SCORE",
        "TOTAL SCORE",
        "DATA CONFIDENCE",
        "Recommendation",
        "Investment Thesis",
    ):
        assert label in card
    assert "UNKNOWN" in card
    assert "NOT_AVAILABLE" in card
    assert "Permits / Violations" in card
    assert "BP-1" in card
    assert "https://example.com/listing/123" in card
    assert "MLS" in card
    assert "source=MLS via source:public-listing" in card
    sentence_count = sum(1 for sentence in card.split("Investment Thesis", 1)[1].split("\n\n", 1)[0].split(". ") if sentence.strip())
    assert 2 <= sentence_count <= 4


def test_candidate_card_exposes_title_history_comparable_and_project_economics_fields():
    record = _record(
        secondary_civic_addresses=["125 Report Street"],
        pids=["012-345-678"],
        number_of_pids=1,
        single_title_multi_address=True,
        previous_year_total=1_600_000,
        previous_year_land=1_300_000,
        transaction_price=1_250_000,
        previous_sale_dates=[datetime(2020, 5, 1, tzinfo=timezone.utc)],
        previous_sale_prices=[1_250_000],
        previous_listing_sources=["https://example.com/history"],
        sold_history_sources=["https://example.com/sold"],
        median_sold_price=1_700_000,
        median_lot_size=4800,
        median_house_size=2050,
        median_price_per_lot_sqft=354.17,
        median_assessment_ratio=0.82,
        relative_affordability_index=0.88,
        land_discount_or_premium_pct=-0.12,
    )
    scenario = RedevelopmentScenario(
        property_id=record.property_id,
        scenario_type=ScenarioType.DUPLEX,
        legal_status="PERMITTED_BY_OFFICIAL_BYLAW",
        unit_count=2,
        total_buildable_sqft=4400,
        realistic_buildable_sqft=4000,
        estimated_saleable_sqft=3600,
        average_unit_size=1800,
        likely_bedrooms="3–4",
        likely_buyer="family-oriented half duplex buyer",
        layout_options=["front/rear", "side-by-side"],
        front_building_noise_buffer_hypothesis=True,
        estimated_resale_price=4_600_000,
        hard_cost=2_000_000,
        soft_cost=400_000,
        developer_specific_cost=200_000,
        contingency=200_000,
        total_project_cost=2_800_000,
        existing_property_market_value=2_500_000,
        new_finished_market_value=4_600_000,
        redevelopment_uplift=-700_000,
    )

    card = build_candidate_card(record, _snapshot(record), scenarios=[scenario])

    for label in (
        "Secondary Civic Addresses",
        "Number of PIDs",
        "SINGLE_TITLE_MULTI_ADDRESS",
        "Previous Year",
        "Transaction Price",
        "Previous Sale History",
        "Median Sold",
        "Relative Affordability",
        "Land Discount/Premium",
        "Estimated Resale",
        "Hard / Soft / Developer / Contingency",
        "Total Project Cost",
        "Existing Value / New Finished",
        "Future Opportunity Cost",
        "Redevelopment Uplift",
        "Layouts",
        "Noise Buffer Hypothesis",
    ):
        assert label in card


def test_sold_and_hard_veto_cases_are_not_recommended_for_site_visit():
    sold = _record(property_id="report:sold", address="Sold Report Street", listing_status="Sold")
    rejected = _record(
        property_id="report:reject",
        address="Rejected Report Street",
        severe_foundation_problem=True,
        candidate_status=CandidateStatus.REJECT,
    )
    report = build_weekly_report(
        [sold, rejected],
        snapshots={sold.property_id: _snapshot(sold), rejected.property_id: _snapshot(rejected)},
        resolutions={
            sold.property_id: _resolution(sold, site_visit_eligible=False, reason_codes=["SOLD_OR_REMOVED"]),
            rejected.property_id: _resolution(
                rejected,
                state=CandidateStatus.REJECT,
                recommendation="REJECT",
                site_visit_eligible=False,
                hard_veto=True,
                reason_codes=["SEVERE_FOUNDATION_PROBLEM"],
            ),
        },
    )

    recommended = report.split("## Recommended Site Visits", 1)[1]
    assert "Sold Report Street" not in recommended
    assert "Rejected Report Street" not in recommended
    assert "Sold Report Street" in report
    assert "Rejected Report Street" in report


def test_lower_priority_active_cases_are_not_silently_dropped_when_a_top_candidate_exists():
    top = _record(property_id="report:top", address="Top Report Street", total_score=82, investment_score=88, candidate_status=CandidateStatus.B_WATCH)
    lower = _record(property_id="report:lower", address="Lower Report Street", total_score=55, investment_score=45, candidate_status=CandidateStatus.D_REJECT)
    report = build_weekly_report(
        [top, lower],
        snapshots={top.property_id: _snapshot(top), lower.property_id: _snapshot(lower)},
        resolutions={
            top.property_id: _resolution(top, state=CandidateStatus.B_WATCH, site_visit_eligible=True),
            lower.property_id: _resolution(lower, state=CandidateStatus.D_REJECT, recommendation="REJECT", site_visit_eligible=False),
        },
    )

    assert "Lower Report Street" in report
    assert "## Lower Priority / Rejected" in report
    assert "Lower Report Street" not in report.split("## Recommended Site Visits", 1)[1]


def test_summary_exposes_case_and_conflict_counts():
    record = _record(data_conflict=True)
    summary = summarize_research_run(
        [record],
        snapshots={record.property_id: _snapshot(record)},
        resolutions={record.property_id: _resolution(record, state=CandidateStatus.SPECIAL_SITUATION)},
        conflicts=[{"property_id": record.property_id, "field_name": "pid"}],
        case_quality_counts={"TRACEABLE_RESEARCH": 1, "BOUNDARY_FIXTURE": 2, "COVERAGE_GAP": 3},
    )

    assert summary["properties_scanned"] == 1
    assert summary["conflicts"] == 1
    assert summary["case_quality_counts"]["BOUNDARY_FIXTURE"] == 2
    assert summary["confidence_band_counts"] == {"Medium": 1}
    assert "priority_case_reviews" in summary


def test_summary_and_lower_priority_keep_out_of_range_observations_out_of_candidate_bands():
    in_scope = _record(
        property_id="report:in-scope",
        address="In Scope Report Street",
        candidate_status=CandidateStatus.NEED_VERIFICATION,
    )
    out_of_range = _record(
        property_id="report:out-of-range",
        address="Out of Range Report Street",
        range_classification="OUT_OF_RANGE",
        candidate_status=CandidateStatus.B_WATCH,
    )
    resolutions = {
        in_scope.property_id: _resolution(
            in_scope,
            state=CandidateStatus.NEED_VERIFICATION,
            site_visit_eligible=False,
        ),
        out_of_range.property_id: _resolution(
            out_of_range,
            state=CandidateStatus.B_WATCH,
            site_visit_eligible=True,
        ),
    }

    summary = summarize_research_run(
        [in_scope, out_of_range],
        snapshots={
            in_scope.property_id: _snapshot(in_scope),
            out_of_range.property_id: _snapshot(out_of_range),
        },
        resolutions=resolutions,
    )
    assert summary["in_scope_primary_count"] == 1
    assert summary["out_of_range_count"] == 1
    assert summary["A_candidates"] == 0
    assert summary["B_candidates"] == 0
    assert summary["NEED_VERIFICATION"] == 1

    report = build_weekly_report(
        [in_scope, out_of_range],
        snapshots={
            in_scope.property_id: _snapshot(in_scope),
            out_of_range.property_id: _snapshot(out_of_range),
        },
        resolutions=resolutions,
    )
    lower_priority = report.split("## Lower Priority / Rejected", 1)[1]
    assert "Out of Range Report Street" not in lower_priority
    assert "Out of Range Report Street" in report.split("## Out-of-Range Price Observations", 1)[1]


def test_major_price_reduction_section_does_not_call_zero_change_a_reduction():
    record = _record(price_reduction_pct=0, price_reduction_amount=0)
    report = build_weekly_report(
        [record],
        snapshots={record.property_id: _snapshot(record)},
        resolutions={record.property_id: _resolution(record)},
    )

    section = report.split("## Major Price Reductions", 1)[1].split("## Watchlist Changes", 1)[0]
    assert "NONE" in section


def test_weekly_report_separates_verification_queue_and_exposes_full_provenance():
    included = _record(property_id="report:included", address="Included Report Street")
    verification = _record(
        property_id="report:verify",
        address="Verify Report Street",
        candidate_status=CandidateStatus.NEED_VERIFICATION,
    )
    provenance = SourceRecord(
        property_id=included.property_id,
        field_name="zoning",
        source_url="https://example.com/bylaw/r1-1",
        source_type="OFFICIAL_GOV",
        source_registry_id="source:city-zoning",
        retrieved_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        raw_value="R1-1",
        normalized_value="R1-1",
        source_version="bylaw-2026-08",
        evidence_status=EvidenceStatus.CONFIRMED,
        notes="Official bylaw record",
    )
    enrichment = EnrichedPropertyResult(
        property=included,
        provider_results=[
            EnrichmentResult(
                property_id=included.property_id,
                provider_name="official-zoning",
                status="SUCCESS",
            )
        ],
    )

    report = build_weekly_report(
        [included, verification],
        snapshots={
            included.property_id: _snapshot(included),
            verification.property_id: _snapshot(verification),
        },
        resolutions={
            included.property_id: _resolution(included),
            verification.property_id: _resolution(
                verification,
                state=CandidateStatus.NEED_VERIFICATION,
                recommendation="VERIFY_BEFORE_REVIEW",
                site_visit_eligible=False,
            ),
        },
        provenance={included.property_id: [provenance]},
        enrichment_results={included.property_id: enrichment},
        stage1_included_property_ids={included.property_id},
    )

    top_section = report.split("## Top New Candidates", 1)[1].split("## Verification Queue", 1)[0]
    assert "Included Report Street" in top_section
    assert "Verify Report Street" not in top_section
    assert "## Verification Queue" in report
    assert "Verify Report Street" in report.split("## Verification Queue", 1)[1]
    assert "source_version=bylaw-2026-08" in report
    assert "evidence=CONFIRMED" in report
    assert "notes=Official bylaw record" in report
    assert "Opportunity Tags" in report
    assert "Enrichment Provider Status" in report
    assert "official-zoning: status=SUCCESS" in report


def test_candidate_card_exposes_official_status_scope_and_snapshot_provenance():
    record = _record(
        school_district="SD39",
        sold_history_scope="last_three_full_calendar_years",
        official_enrichment_status={
            "bca-assessment": "SUCCESS",
            "school-catchment": "BLOCKED",
        },
    )
    provenance = [
        SourceRecord(
            property_id=record.property_id,
            field_name="assessment_total",
            source_url="https://www.bcassessment.ca/Property/PropertyInformation",
            source_type="BC_ASSESSMENT",
            source_registry_id="source:bca-assessment",
            retrieved_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            raw_value="$1,700,000",
            normalized_value=1_700_000,
            match_method="PID",
            query_scope="last_three_full_calendar_years",
            raw_snapshot_path="data/research/raw/official-enrichment/bca-1.html",
        )
    ]

    card = build_candidate_card(record, _snapshot(record), provenance=provenance)

    assert "Official Enrichment" in card
    assert "bca-assessment=SUCCESS" in card
    assert "school-catchment=BLOCKED" in card
    assert "School District: SD39" in card
    assert "Sold History Scope: last_three_full_calendar_years" in card
    assert "match=PID" in card
    assert "query_scope=last_three_full_calendar_years" in card
    assert "raw_snapshot_path=data/research/raw/official-enrichment/bca-1.html" in card


def test_source_run_status_includes_registry_and_lineage():
    from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus

    source_run = SourceRunResult(
        source_registry_id="source:listings",
        source_name="listings",
        status=SourceRunStatus.SUCCESS,
        source_run_id=11,
        weekly_run_id=7,
        started_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    report = build_weekly_report([_record()], source_runs=[source_run])

    assert "registry=source:listings" in report
    assert "source_run_id=11" in report
    assert "weekly_run_id=7" in report
