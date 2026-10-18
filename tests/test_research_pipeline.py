from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import CandidateStatus, PropertyObservation, PropertyRecord
from northshore_watchdog.research.provenance import DataConflict
import northshore_watchdog.research.pipeline as research_pipeline
from northshore_watchdog.research.pipeline import run_research_pipeline
from northshore_watchdog.research.storage import ResearchStorage
from northshore_watchdog.research.crawling.models import CrawlArtifact


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _write_inputs(tmp_path, asking=1_500_000):
    listing_path = tmp_path / "listings.json"
    listing_path.write_text(
        json.dumps(
            [
                {
                    "property_id": "listing:1",
                    "pid": "PID-PIPE-1",
                    "address": "1 Pipeline Street",
                    "municipality": "City of Vancouver",
                    "neighbourhood": "Knight",
                    "property_type": "Detached House",
                    "ownership_type": "Freehold Non-Strata",
                    "asking": asking,
                    "original_asking": 1_600_000,
                    "listing_status": "Active",
                    "dom": 61,
                    "lot_area_sqft": 5000,
                    "frontage_ft": 40,
                    "depth_ft": 125,
                    "finished_sqft": 2200,
                    "year_built": 1960,
                    "bedrooms": 4,
                    "bathrooms": 2.5,
                    "zoning": "R1-1",
                    "source_url": "local://pipeline/listing/1",
                }
            ]
        ),
        encoding="utf-8",
    )
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps([{"property_id": "pid:PID-PIPE-1", "assessment_total": 1_550_000, "assessment_land": 1_300_000, "source_url": "local://pipeline/assessment/1"}]),
        encoding="utf-8",
    )
    zoning_path = tmp_path / "zoning.json"
    zoning_path.write_text(
        json.dumps([{"property_id": "pid:PID-PIPE-1", "zoning": "R1-1", "maximum_fsr": 1.0, "maximum_units": 6, "permitted_uses": ["single detached", "duplex", "multiple dwelling"], "approved_use": "Single Detached", "source_url": "local://pipeline/zoning/1"}]),
        encoding="utf-8",
    )
    comp_path = tmp_path / "comps.json"
    comp_path.write_text(
        json.dumps([
            {
                "comparable_id": f"pipe-comp-{index}",
                "subject_property_id": "pid:PID-PIPE-1",
                "property_id": f"pid:PIPE-SOLD-{index}",
                "municipality": "City of Vancouver",
                "neighbourhood": "Knight",
                "property_type": "Detached House",
                "sold_date": (OBSERVED_AT - timedelta(days=20 + index)).isoformat(),
                "sold_price": 1_600_000 + index * 10_000,
                "lot_area_sqft": 5000,
                "frontage_ft": 40,
                "depth_ft": 125,
                "zoning": "R1-1",
                "road_exposure_level": 2,
                "current_use_building_value": 450_000,
                "source_records": [
                    {
                        "property_id": f"pid:PIPE-SOLD-{index}",
                        "field_name": "sold_history",
                        "source_url": f"local://pipeline/comps/a/{index}",
                        "source_type": "MLS",
                        "source_registry_id": "source:comps-a",
                        "retrieved_at": OBSERVED_AT.isoformat(),
                        "raw_value": 1_600_000 + index * 10_000,
                        "normalized_value": 1_600_000 + index * 10_000,
                        "evidence_status": "CONFIRMED",
                    },
                    {
                        "property_id": f"pid:PIPE-SOLD-{index}",
                        "field_name": "sold_history",
                        "source_url": f"local://pipeline/comps/b/{index}",
                        "source_type": "MLS",
                        "source_registry_id": "source:comps-b",
                        "retrieved_at": OBSERVED_AT.isoformat(),
                        "raw_value": 1_600_000 + index * 10_000,
                        "normalized_value": 1_600_000 + index * 10_000,
                        "evidence_status": "CONFIRMED",
                    },
                ],
            }
            for index in range(5)
        ]),
        encoding="utf-8",
    )
    return listing_path, assessment_path, zoning_path, comp_path


def _config(tmp_path, asking=1_500_000):
    listing_path, assessment_path, zoning_path, comp_path = _write_inputs(tmp_path, asking)
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    config["market_buckets"] = {
        "vancouver_east": {
            "municipality": "City of Vancouver",
            "neighbourhoods": ["Knight"],
            "core_range": [1_000_000, 2_000_000],
            "extended_range": [1_000_000, 2_200_000],
        }
    }
    config["acquisition"] = {"sources": [{"name": "pipeline-listings", "type": "LOCAL_JSON", "path": str(listing_path), "source_type": "MLS", "enabled": True}]}
    config["enrichment"] = {"providers": [
        {"name": "pipeline-assessment", "type": "JSON_MAPPING", "path": str(assessment_path), "source_type": "BC_ASSESSMENT", "enabled": True},
        {"name": "pipeline-zoning", "type": "JSON_MAPPING", "path": str(zoning_path), "source_type": "OFFICIAL_GOV", "enabled": True},
    ]}
    config["comparables"] = {
        "source_path": str(comp_path),
        "min_reliable_comps": 5,
        "lot_tolerance_pct": 0.2,
        "cross_check_source_registry_ids": ["source:comps-a", "source:comps-b"],
    }
    config["source_registry"] = [
        {
            "source_registry_id": "source:pipeline-listings",
            "name": "pipeline listings",
            "source_type": "MLS",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
        {
            "source_registry_id": "source:pipeline-assessment",
            "name": "pipeline assessment",
            "source_type": "BC_ASSESSMENT",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
        {
            "source_registry_id": "source:pipeline-zoning",
            "name": "pipeline zoning",
            "source_type": "OFFICIAL_GOV",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
        {
            "source_registry_id": "source:comps-a",
            "name": "pipeline comps a",
            "source_type": "MLS",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
        {
            "source_registry_id": "source:comps-b",
            "name": "pipeline comps b",
            "source_type": "MLS",
            "authorization_status": "NOT_APPLICABLE",
            "evidence_status": "CONFIRMED",
        },
    ]
    config["redevelopment"]["rules"] = {"City of Vancouver:R1-1": {"bylaw_version": "test-bylaw-v1", "maximum_fsr": 1.0, "maximum_units": 6, "duplex_permitted": True, "multiplex_permitted": True}}
    return config


def test_full_research_pipeline_persists_all_stages_and_report(tmp_path):
    result = run_research_pipeline(_config(tmp_path), observed_at=OBSERVED_AT)

    assert result.status in {"SUCCESS", "PARTIAL"}
    assert result.summary["properties_scanned"] == 1
    assert result.summary["deep_research_count"] == 1
    assert result.records[0].expected_market_value is not None
    assert result.records[0].neighbourhood_comparable_median is not None
    assert result.records[0].neighbourhood_benchmark_count == 5
    assert result.records[0].best_economic_product is None
    assert result.summary["coverage_gaps"]
    assert result.report_path is not None and result.report_path.exists()
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "- report: COMPLETED" in report_text
    assert "## Data Source Limitations" in report_text
    assert "PID/title and municipal approved-use records" in report_text
    assert "Neighbourhood Benchmark" in report_text
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM property_observations").fetchone()[0] == 1
        assert storage.conn.execute("SELECT COUNT(*) FROM field_provenance").fetchone()[0] >= 1
        assert storage.conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 1
        assert storage.conn.execute("SELECT weekly_run_id FROM source_runs").fetchone()[0] == result.run_id
        assert storage.conn.execute("SELECT json_extract(payload, '$.source_run_id') FROM source_runs").fetchone()[0] == 1
        assert storage.conn.execute("SELECT COUNT(*) FROM enrichment_observations").fetchone()[0] == 1
        assert storage.conn.execute("SELECT weekly_run_id FROM enrichment_observations").fetchone()[0] == result.run_id
        assert storage.conn.execute("SELECT COUNT(*) FROM comparable_records").fetchone()[0] == 5
        assert storage.conn.execute("SELECT weekly_run_id FROM comparable_records").fetchone()[0] == result.run_id
        assert storage.conn.execute("SELECT COUNT(*) FROM field_provenance WHERE field_name = 'sold_history'").fetchone()[0] == 10
        assert storage.conn.execute("SELECT observed_at FROM comparable_records LIMIT 1").fetchone()[0] == OBSERVED_AT.isoformat()
        assert storage.conn.execute("SELECT COUNT(*) FROM redevelopment_scenarios").fetchone()[0] >= 3
        assert storage.conn.execute(
            "SELECT source_registry_id FROM redevelopment_scenarios WHERE scenario_type = 'DUPLEX'"
        ).fetchone()[0] == "source:pipeline-zoning"
        assert storage.conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 1
        score_lineage = storage.conn.execute(
            "SELECT weekly_run_id, source_run_id, source_registry_id FROM score_snapshots"
        ).fetchone()
        assert score_lineage[0] == result.run_id
        assert score_lineage[1] == 1
        assert score_lineage[2] == "source:pipeline-listings"
        source_event = next(
            event for event in storage.list_watchlist_events()
            if event.event_type == "source_run_status"
        )
        assert source_event.weekly_run_id == result.run_id
        assert source_event.source_run_id == 1


def test_reconciled_bca_bedroom_difference_closes_prior_open_conflict(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    listing_payload = json.loads(listing_path.read_text(encoding="utf-8"))
    listing_payload[0]["bedrooms"] = 3
    listing_path.write_text(json.dumps(listing_payload), encoding="utf-8")
    assessment_path = Path(config["enrichment"]["providers"][0]["path"])
    assessment_payload = json.loads(assessment_path.read_text(encoding="utf-8"))
    assessment_payload[0]["bedrooms"] = 4
    assessment_path.write_text(json.dumps(assessment_payload), encoding="utf-8")

    first = run_research_pipeline(config, observed_at=OBSERVED_AT)
    property_id = first.records[0].property_id
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        storage.save_observation(
            PropertyObservation(
                observation_id="observation:bedroom-conflict-state",
                property_id=property_id,
                observed_at=OBSERVED_AT + timedelta(days=1),
                source_registry_id="source:pipeline-listings",
                parser_version="test",
                raw_payload={},
                normalized_record=first.records[0].model_copy(update={
                    "candidate_status": CandidateStatus.REJECT,
                    "data_conflict": True,
                    "major_data_conflict": True,
                }),
            )
        )
        storage.save_conflict(
            DataConflict(
                conflict_id=f"conflict:{property_id}:bedrooms",
                property_id=property_id,
                field_name="bedrooms",
                conflict_type="BEDROOMS_MISMATCH",
                source_values={"listing": 3, "bca": 4},
                conflicting_sources=["listing", "bca"],
            )
        )

    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    assert second.records[0].bedrooms == 4
    assert second.records[0].data_conflict is False
    assert second.records[0].major_data_conflict is not True
    assert not any(item.field_name == "bedrooms" for item in second.conflicts)
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert not any(item.field_name == "bedrooms" for item in storage.list_open_conflicts())
        current = storage.get_current_property(property_id)
        assert current is not None
        assert current.candidate_status is CandidateStatus.NEED_VERIFICATION


def test_single_source_mode_persists_provisional_status_and_manual_queue(tmp_path):
    config = _config(tmp_path)
    config["acquisition"]["allow_single_source_provisional"] = True
    config["acquisition"]["sources"][0]["source_role"] = "REALTOR_CA_PUBLIC"
    config["source_registry"][0]["source_role"] = "REALTOR_CA_PUBLIC"

    result = run_research_pipeline(
        config,
        stages={"acquire", "score", "report"},
        observed_at=OBSERVED_AT,
    )

    record = result.records[0]
    assert record.data_verification_status == "PROVISIONAL_SINGLE_SOURCE"
    assert record.manual_verification_required is True
    assert record.candidate_status is CandidateStatus.NEED_VERIFICATION
    assert result.summary["manual_verification_queue_count"] == 1
    assert len(result.verification_items) == 1
    assert result.verification_items[0].status == "PENDING_MANUAL"
    assert "## Provisional / Unverified Source Observations" in result.report_path.read_text(encoding="utf-8")
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "Acquisition Channel: PUBLIC_REALTOR_CA" in report_text
    assert "source=PUBLIC_REALTOR_CA via source:pipeline-listings" in report_text
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert len(storage.list_open_verification_items()) == 1


def test_open_manual_queue_survives_a_strict_listing_refresh(tmp_path):
    config = _config(tmp_path)
    config["acquisition"]["allow_single_source_provisional"] = True
    config["acquisition"]["sources"][0]["source_role"] = "REALTOR_CA_PUBLIC"
    config["source_registry"][0]["source_role"] = "REALTOR_CA_PUBLIC"

    first = run_research_pipeline(
        config,
        stages={"acquire", "score"},
        observed_at=OBSERVED_AT,
    )
    assert first.records[0].manual_verification_required is True

    config["acquisition"]["allow_single_source_provisional"] = False
    second = run_research_pipeline(
        config,
        stages={"acquire", "score"},
        observed_at=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
    )

    record = second.records[0]
    assert record.manual_verification_required is True
    assert record.data_verification_status == "PROVISIONAL_SINGLE_SOURCE"
    assert record.candidate_status is CandidateStatus.NEED_VERIFICATION


def test_pipeline_persists_neighbourhood_benchmark_rows_as_well_as_selected_comps(tmp_path):
    config = _config(tmp_path)
    comp_path = Path(config["comparables"]["source_path"])
    rows = json.loads(comp_path.read_text(encoding="utf-8"))
    outlier = json.loads(json.dumps(rows[0]))
    outlier["comparable_id"] = "pipe-benchmark-outlier"
    outlier["property_id"] = "pid:PIPE-BENCHMARK-OUTLIER"
    outlier["sold_price"] = 2_100_000
    outlier["lot_area_sqft"] = 7_000
    outlier["frontage_ft"] = 44
    outlier["depth_ft"] = 140
    for source in outlier["source_records"]:
        source["property_id"] = outlier["property_id"]
        source["raw_value"] = outlier["sold_price"]
        source["normalized_value"] = outlier["sold_price"]
    rows.append(outlier)
    comp_path.write_text(json.dumps(rows), encoding="utf-8")

    result = run_research_pipeline(config, observed_at=OBSERVED_AT)

    assert result.records[0].comp_count == 5
    assert result.records[0].neighbourhood_benchmark_count == 6
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM comparable_records").fetchone()[0] == 6


def test_second_pipeline_run_preserves_observation_history_and_emits_delta(tmp_path):
    config = _config(tmp_path, asking=1_500_000)
    first = run_research_pipeline(config, observed_at=OBSERVED_AT)
    config = _config(tmp_path, asking=1_425_000)
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    assert first.records[0].asking == 1_500_000
    assert second.records[0].asking == 1_425_000
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM property_observations").fetchone()[0] == 2
        assert any(event.event_type == "price_reduction" for event in storage.list_watchlist_events())


def test_pid_appearing_after_address_first_observation_keeps_one_identity(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    payload = json.loads(listing_path.read_text(encoding="utf-8"))
    payload[0].pop("pid")
    listing_path.write_text(json.dumps(payload), encoding="utf-8")

    first = run_research_pipeline(config, observed_at=OBSERVED_AT)
    first_id = first.records[0].property_id

    payload[0]["pid"] = "PID-PIPE-1"
    listing_path.write_text(json.dumps(payload), encoding="utf-8")
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    assert second.records[0].property_id == first_id
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
        aliases = storage.list_identity_aliases()
        assert aliases["address:1 pipeline st city of vancouver"] == first_id
        assert aliases["pid:PID-PIPE-1"] == first_id


def test_dry_run_and_stage_filter_do_not_persist_observations(tmp_path):
    config = _config(tmp_path)
    dry = run_research_pipeline(config, observed_at=OBSERVED_AT, dry_run=True)
    assert dry.dry_run is True
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.conn.execute("SELECT COUNT(*) FROM property_observations").fetchone()[0] == 0
        assert storage.conn.execute("SELECT COUNT(*) FROM score_snapshots").fetchone()[0] == 0
    acquire_only = run_research_pipeline(config, observed_at=OBSERVED_AT, stages={"acquire"})
    assert acquire_only.stage_status["acquire"] == "COMPLETED"
    assert acquire_only.stage_status["enrich"] == "SKIPPED"


def test_stage_only_redevelopment_reuses_official_evidence_from_current_projection(tmp_path):
    config = _config(tmp_path)
    run_research_pipeline(config, observed_at=OBSERVED_AT)

    result = run_research_pipeline(
        config,
        observed_at=OBSERVED_AT + timedelta(days=7),
        stages={"redevelop"},
    )

    scenario_result = result.redevelopment_results["pid:PID-PIPE-1"]
    assert scenario_result.official_zoning_evidence is True
    assert any(
        scenario.legal_status == "PERMITTED_BY_OFFICIAL_BYLAW"
        for scenario in scenario_result.scenarios
    )


def test_complete_source_run_reconciles_absent_listing_as_removed_or_unknown(tmp_path):
    listing_path, *_ = _write_inputs(tmp_path)
    first_payload = json.loads(listing_path.read_text(encoding="utf-8"))
    second_payload = dict(first_payload[0])
    second_payload.update({
        "property_id": "listing:2",
        "pid": "PID-PIPE-2",
        "address": "2 Pipeline Street",
    })
    listing_path.write_text(json.dumps([first_payload[0], second_payload]), encoding="utf-8")
    config = _config(tmp_path)
    config["acquisition"]["sources"][0]["coverage_complete"] = True
    run_research_pipeline(config, observed_at=OBSERVED_AT)

    listing_path.write_text(json.dumps([second_payload]), encoding="utf-8")
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        removed = storage.get_current_property("pid:PID-PIPE-1")
        assert removed is not None
        assert removed.listing_status == "REMOVED_OR_UNKNOWN"
        assert any(
            event.event_type == "status_change"
            and event.property_id == "pid:PID-PIPE-1"
            for event in storage.list_watchlist_events()
        )
    assert any(record.listing_status == "REMOVED_OR_UNKNOWN" for record in second.records)


def test_missing_comparable_source_is_reported_as_partial(tmp_path):
    config = _config(tmp_path)
    config["comparables"]["source_path"] = str(tmp_path / "missing-comps.json")

    result = run_research_pipeline(config, observed_at=OBSERVED_AT, dry_run=True)

    assert result.status == "PARTIAL"
    assert result.stage_status["comps"] == "PARTIAL"
    assert result.summary["parse_errors"] >= 1


def test_acquisition_conflicts_are_persisted_in_open_conflict_queue(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    row = json.loads(listing_path.read_text(encoding="utf-8"))[0]
    collision = dict(row)
    collision["pid"] = "PID-PIPE-CONFLICT"
    collision["address"] = row["address"].upper()
    listing_path.write_text(json.dumps([row, collision]), encoding="utf-8")

    result = run_research_pipeline(config, observed_at=OBSERVED_AT)

    assert result.conflicts
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        open_conflicts = storage.list_open_conflicts()
    assert any(item.conflict_type == "PID_ADDRESS_COLLISION" for item in open_conflicts)


def test_weekly_report_includes_preexisting_open_conflict_queue(tmp_path):
    config = _config(tmp_path)
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    row = json.loads(listing_path.read_text(encoding="utf-8"))[0]
    collision = dict(row)
    collision["pid"] = "PID-PIPE-CONFLICT"
    collision["address"] = row["address"].upper()
    listing_path.write_text(json.dumps([row, collision]), encoding="utf-8")
    run_research_pipeline(config, observed_at=OBSERVED_AT)

    listing_path.write_text(json.dumps([row]), encoding="utf-8")
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    report = second.report_path.read_text(encoding="utf-8")
    assert second.records[0].data_conflict is True
    assert second.records[0].major_data_conflict is True
    assert second.resolutions[second.records[0].property_id].state is CandidateStatus.REJECT
    assert "## Open Conflict Queue" in report
    assert "PID_ADDRESS_COLLISION" in report


def test_reconciliation_does_not_resurrect_legacy_identity_for_same_current_address(tmp_path):
    config = _config(tmp_path)
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        legacy = PropertyRecord(
            property_id="property:legacy-pipeline",
            address="1 Pipeline Street",
            municipality="City of Vancouver",
            asking=1_500_000,
            listing_status="Active",
        )
        storage.save_observation(
            PropertyObservation(
                observation_id="observation:legacy-pipeline",
                property_id=legacy.property_id,
                observed_at=OBSERVED_AT,
                source_registry_id="source:phase2-local-listings",
                parser_version="legacy",
                raw_payload={"address": legacy.address},
                normalized_record=legacy,
            )
        )

    result = run_research_pipeline(config, observed_at=OBSERVED_AT)

    assert [record.address for record in result.records].count("1 Pipeline Street") == 1
    assert {record.property_id for record in result.records} == {"property:legacy-pipeline"}


def test_reconciliation_keeps_disjoint_pid_collision_visible_at_same_address(tmp_path):
    config = _config(tmp_path)
    config["acquisition"]["sources"][0]["coverage_complete"] = True
    listing_path = Path(config["acquisition"]["sources"][0]["path"])
    payload = json.loads(listing_path.read_text(encoding="utf-8"))
    payload[0]["pid"] = "PID-COLLISION-A"
    listing_path.write_text(json.dumps(payload), encoding="utf-8")
    run_research_pipeline(config, observed_at=OBSERVED_AT)

    payload[0]["pid"] = "PID-COLLISION-B"
    listing_path.write_text(json.dumps(payload), encoding="utf-8")
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    current = {record.property_id: record for record in second.records}
    assert "pid:PID-COLLISION-B" in current
    assert current["pid:PID-COLLISION-A"].listing_status == "REMOVED_OR_UNKNOWN"


def test_equivalent_legacy_conflict_is_resolved_on_next_reconciliation(tmp_path):
    config = _config(tmp_path)
    first = run_research_pipeline(config, observed_at=OBSERVED_AT)
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        storage.save_conflict(
            DataConflict(
                conflict_id=f"conflict:{first.records[0].property_id}:ownership_type",
                property_id=first.records[0].property_id,
                field_name="ownership_type",
                conflict_type="OWNERSHIP_TYPE_MISMATCH",
                source_values={"old": "FREEHOLD_NON_STRATA", "new": "FREEHOLD_NON_STRATA"},
                conflicting_sources=["old", "new"],
            )
        )

    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))

    assert second.records[0].data_conflict is False
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        assert storage.list_open_conflicts() == []


def _write_crawl_manifest(tmp_path: Path) -> Path:
    entrypoint = "https://www.zolo.ca/vancouver-real-estate/houses"
    artifact = CrawlArtifact(
        run_id="crawl-run-1",
        source_name="zolo-full-market",
        source_registry_id="source:zolo-full-market",
        entrypoint_url=entrypoint,
        request_kind="SEARCH",
        requested_url=entrypoint,
        final_url=entrypoint,
        status_code=200,
        content_type="text/html",
        raw_path=str(tmp_path / "search.html"),
        sha256="a" * 64,
        size_bytes=1,
        retrieved_at=OBSERVED_AT,
        rows=[{
            "mls_number": "R4000001",
            "address": "1 Pipeline Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_500_000,
            "lot_area_sqft": 5_000,
            "frontage_ft": 40,
            "depth_ft": 125,
            "bedrooms": 4,
            "bathrooms": 3,
            "finished_sqft": 2_200,
        }],
        coverage_state="COVERAGE_COMPLETE",
        card_count=1,
        extracted_row_count=1,
    )
    path = tmp_path / "manifest.jsonl"
    path.write_text(artifact.model_dump_json() + "\n", encoding="utf-8")
    return path


def _write_empty_complete_crawl_manifest(tmp_path: Path) -> Path:
    entrypoint = "https://www.zolo.ca/vancouver-real-estate/houses"
    artifact = CrawlArtifact(
        run_id="crawl-run-2",
        source_name="zolo-full-market",
        source_registry_id="source:zolo-full-market",
        entrypoint_url=entrypoint,
        request_kind="SEARCH",
        requested_url=entrypoint,
        final_url=entrypoint,
        status_code=200,
        content_type="text/html",
        raw_path=str(tmp_path / "search-empty.html"),
        sha256="b" * 64,
        size_bytes=1,
        retrieved_at=OBSERVED_AT + timedelta(days=7),
        rows=[],
        coverage_state="COVERAGE_COMPLETE",
        card_count=0,
        extracted_row_count=0,
    )
    path = tmp_path / "manifest-empty.jsonl"
    path.write_text(artifact.model_dump_json() + "\n", encoding="utf-8")
    return path


def test_pipeline_uses_manifest_adapters_only_and_preserves_source_filter(tmp_path, monkeypatch):
    config = _config(tmp_path)
    manifest = _write_crawl_manifest(tmp_path)
    config["acquisition"]["crawl_sources"] = [{
        "name": "zolo-full-market",
        "source_type": "AGGREGATOR",
        "source_role": "ZOLO_PUBLIC",
        "source_registry_id": "source:zolo-full-market",
        "entrypoints": [{"url": "https://www.zolo.ca/vancouver-real-estate/houses"}],
    }]
    config["acquisition"]["sources"] = [{
        "name": "ordinary-source-must-not-run",
        "type": "LOCAL_JSON",
        "path": str(tmp_path / "missing.json"),
        "source_type": "MLS",
        "enabled": True,
    }]

    def fail_if_ordinary_adapters_are_built(*_args, **_kwargs):
        raise AssertionError("ordinary acquisition sources must not be built")

    monkeypatch.setattr(
        research_pipeline,
        "build_source_adapters",
        fail_if_ordinary_adapters_are_built,
    )

    result = run_research_pipeline(
        config,
        stages={"acquire"},
        source_names={"zolo-full-market"},
        crawl_manifest_paths=[str(manifest)],
        dry_run=True,
        observed_at=OBSERVED_AT,
    )

    assert [item.source_name for item in result.source_runs] == ["zolo-full-market"]
    assert result.source_runs[0].source_registry_id == "source:zolo-full-market"
    assert result.records[0].mls_number == "R4000001"


def test_complete_crawl_manifest_reconciles_missing_listing_as_removed(tmp_path):
    config = _config(tmp_path)
    config["acquisition"]["crawl_sources"] = [{
        "name": "zolo-full-market",
        "source_type": "AGGREGATOR",
        "source_role": "ZOLO_PUBLIC",
        "source_registry_id": "source:zolo-full-market",
        "coverage_complete": True,
        "entrypoints": [{"url": "https://www.zolo.ca/vancouver-real-estate/houses"}],
    }]
    config["acquisition"]["sources"] = []
    first_manifest = _write_crawl_manifest(tmp_path)
    second_manifest = _write_empty_complete_crawl_manifest(tmp_path)

    first = run_research_pipeline(
        config,
        stages={"acquire"},
        crawl_manifest_paths=[str(first_manifest)],
        observed_at=OBSERVED_AT,
    )
    second = run_research_pipeline(
        config,
        stages={"acquire"},
        crawl_manifest_paths=[str(second_manifest)],
        observed_at=OBSERVED_AT + timedelta(days=7),
    )

    assert first.records[0].listing_status != "REMOVED_OR_UNKNOWN"
    assert [record.listing_status for record in second.records] == ["REMOVED_OR_UNKNOWN"]
    assert any(event.event_type == "status_change" for event in second.events)
