from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.config import load_config
from northshore_watchdog.research.discovery import DiscoveryStatus
from northshore_watchdog.research.discovery_pipeline import (
    DiscoveryRunResult,
    _build_discovery_source,
    run_discovery,
)
from northshore_watchdog.research.discovery_report import (
    persist_discovery_result,
    write_discovery_report,
)
from northshore_watchdog.research.discovery_sources import JsonFilteredCaptureSource
from northshore_watchdog.research.storage import ResearchStorage


ROOT = Path(__file__).parents[1]
RUNNER = CliRunner()


def test_discovery_config_exposes_operational_presets_and_relaxed_house_gates():
    config = load_config("config/research-discovery.yaml")
    presets = config["discovery"]["presets"]

    assert config["discovery"]["shortlist_limit"] is None
    assert [item["name"] for item in presets] == [
        "vancouver-west",
        "vancouver-east",
        "vancouver-central-core",
        "vancouver-oakridge",
        "vancouver-south",
        "north-vancouver-city",
        "north-vancouver-district",
        "west-vancouver",
        "vancouver-broad-scan",
    ]
    assert presets[0]["desired_filters"]["price_min"] == 1_500_000
    assert presets[0]["desired_filters"]["price_max"] == 2_000_000
    assert presets[1]["desired_filters"]["price_min"] == 1_000_000
    assert presets[1]["desired_filters"]["price_max"] == 1_600_000
    for item in presets[:-1]:
        desired = item["desired_filters"]
        assert desired["property_type"] == "DETACHED_HOUSE"
        assert desired["ownership"] == "FREEHOLD"
        assert desired["minimum_bedrooms"] == 2
        assert desired["minimum_bathrooms"] == 1
        assert desired["minimum_finished_sqft"] == 1250
        assert "lot_area_sqft" not in desired

    broad = presets[-1]["desired_filters"]
    assert presets[0]["desired_filters"]["municipality"] == "Vancouver"
    assert presets[1]["desired_filters"]["municipality"] == "Vancouver"
    assert broad["municipality"] == "Vancouver"
    assert broad["property_type"] == "DETACHED_HOUSE"
    assert broad["ownership"] == "FREEHOLD"
    assert broad["minimum_bedrooms"] == 2
    assert broad["minimum_bathrooms"] == 1
    assert "minimum_finished_sqft" not in broad
    assert broad["price_min"] == 1_000_000
    assert broad["price_max"] == 1_800_000


def test_discovery_dry_run_compiles_plan_without_invoking_source(monkeypatch):
    config = load_config("config/research-discovery.yaml")

    def forbidden_execute(*args, **kwargs):
        raise AssertionError("dry run must not execute a discovery source")

    monkeypatch.setattr(
        "northshore_watchdog.research.discovery_sources.JsonFilteredCaptureSource.execute",
        forbidden_execute,
    )

    result = run_discovery(
        config,
        preset_name="vancouver-west",
        dry_run=True,
    )

    assert isinstance(result, DiscoveryRunResult)
    assert result.status == "DRY_RUN"
    assert result.dry_run is True
    assert result.filter_plan is not None
    assert result.filter_plan.preset_name == "vancouver-west"
    assert result.filter_plan.native_filters["property_type"] == "DETACHED_HOUSE"
    assert result.filter_plan.post_filters["minimum_bathrooms"] == 1
    assert result.candidate_count == 0


def test_realtor_native_source_uses_real_map_route_and_applies_relaxed_bath_filter():
    config = load_config("config/research-discovery.yaml")
    source = next(
        item for item in config["discovery"]["sources"] if item["name"] == "realtor-ca-native"
    )

    assert source["enabled"] is True
    assert source["type"] == "PLAYWRIGHT_NATIVE"
    assert source["browser"]["url_builder"] == "REALTOR_CA_MAP"
    assert source["browser"]["browser_cdp_endpoint"] == "http://127.0.0.1:9222"
    assert source["browser"]["reuse_current_page"] is False
    assert source["browser"]["open_new_page"] is True
    assert source["browser"]["launch_options"] == {"args": ["--window-size=1438,967"]}
    assert source["browser"]["result_count_variance_tolerance"] == 2
    assert "www.realtor.ca/map#" in source["browser"]["entrypoint"]
    assert "LatitudeMax=49.27707" in source["browser"]["entrypoint"]
    assert "LongitudeMax=-123.02127" in source["browser"]["entrypoint"]
    assert "LatitudeMin=49.20792" in source["browser"]["entrypoint"]
    assert "LongitudeMin=-123.20787" in source["browser"]["entrypoint"]
    assert "CurrentPage=1" in source["browser"]["entrypoint"]
    assert "PriceMin=1000000" in source["browser"]["entrypoint"]
    assert "PriceMax=1800000" in source["browser"]["entrypoint"]
    assert source["browser"]["selectors"]["result_count"] == "#mapResultsNumVal, #listViewResultsNumVal"
    assert source["max_result_cards"] == 500
    assert source["browser"]["pagination"] == {
        "enabled": True,
        "next_selector": "#SideBarPagination a.lnkNextResultsPage.paginationLink.paginationLinkForward",
        "max_pages": 50,
        "wait_after_click_ms": 1000,
        "progress_poll_interval_ms": 250,
        "progress_timeout_ms": 15000,
        "progress_stable_polls": 3,
    }

    result = run_discovery(
        config,
        preset_name="vancouver-west",
        source_name="realtor-ca-native",
        dry_run=True,
    )

    assert result.filter_plan is not None
    assert result.filter_plan.native_filters["minimum_bathrooms"] == 1
    assert result.filter_plan.post_filters == {"municipality": "Vancouver"}


def test_discovery_replay_rejects_sample_card_outside_selected_preset():
    config = load_config("config/research-discovery.yaml")

    result = run_discovery(config, preset_name="vancouver-west")

    assert result.status == "FAILED"
    assert result.result_count == 2
    assert result.filter_plan is not None
    assert result.filter_plan.preset_name == "vancouver-west"
    assert result.validation is not None
    assert result.validation.status is DiscoveryStatus.FILTER_NOT_APPLIED
    assert result.candidate_count == len(result.candidates)
    assert result.candidates == []


def test_discovery_replay_valid_capture_runs_end_to_end(tmp_path):
    config = load_config("config/research-discovery.yaml")
    capture_path = tmp_path / "valid.json"
    capture_path.write_text(
        json.dumps(
            {
                "source_name": "filtered-capture-replay",
                "source_url": "https://example.test/filtered",
                "result_count": 1,
                "count_is_complete": True,
                "applied_filters": {"property_type": "DETACHED_HOUSE"},
                "cards": [
                    {
                        "address": "101 Valid Discovery Street",
                        "asking": 1_688_000,
                        "bedrooms": 4,
                        "bathrooms": 3,
                        "finished_sqft": 1_850,
                        "property_type": "Detached House",
                        "ownership_type": "Freehold",
                        "listing_status": "Active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_discovery(
        config,
        preset_name="vancouver-west",
        capture_path=capture_path,
    )

    assert result.status == "COMPLETED"
    assert result.validation is not None
    assert result.validation.status is DiscoveryStatus.PASS
    assert result.candidate_count == 1
    assert result.candidates[0].address == "101 Valid Discovery Street"


def test_discovery_run_attaches_v2_screening_without_changing_discovery_pool(tmp_path):
    config = load_config("config/research-discovery.yaml")
    capture_path = tmp_path / "screening.json"
    capture_path.write_text(
        json.dumps(
            {
                "source_name": "filtered-capture-replay",
                "source_url": "https://example.test/filtered",
                "result_count": 1,
                "count_is_complete": True,
                "applied_filters": {"property_type": "DETACHED_HOUSE"},
                "cards": [
                    {
                        "address": "101 Screening Street",
                        "mls_number": "R3000002",
                            "municipality": "Vancouver",
                            "neighbourhood": "Dunbar",
                        "asking": 1_200_000,
                        "bedrooms": 4,
                        "bathrooms": 3,
                        "finished_sqft": 2_100,
                        "lot_area_sqft": 5_000,
                        "lot_dimensions": "50 x 120 ft",
                        "year_built": 2000,
                        "property_type": "Detached House",
                        "ownership_type": "Freehold",
                            "listing_status": "Active",
                            "dom": "200 days",
                            "description": (
                                "Well-maintained updated home on a quiet residential street near a park. "
                                "R1-1 zoning, lane access and duplex potential."
                            ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_discovery(
        config,
        preset_name="vancouver-broad-scan",
        capture_path=capture_path,
    )

    assert result.candidate_count == 1
    assert result.screening is not None
    assert result.screening.source_count == 1
    assert result.screening.stage_b_count == 1
    assert result.screening.final_review_count == 1
    assert result.screening.final_review_candidates == []
    assert result.screening.hold_redevelop_final_candidates[0].card.address == "101 Screening Street"


def test_discovery_runs_official_enrichment_before_screening(tmp_path, monkeypatch):
    from northshore_watchdog.research import discovery_pipeline
    from northshore_watchdog.research.discovery_official import DiscoveryOfficialEnrichmentResult

    config = load_config("config/research-discovery.yaml")
    events = []
    capture_path = tmp_path / "official-before-score.json"
    capture_path.write_text(
        json.dumps({
            "source_name": "filtered-capture-replay",
            "source_url": "https://example.test/filtered",
            "result_count": 1,
            "count_is_complete": True,
            "applied_filters": {"property_type": "DETACHED_HOUSE"},
            "cards": [{
                "address": "101 Official Before Score Street",
                "mls_number": "R3000042",
                "municipality": "Vancouver",
                "neighbourhood": "Dunbar",
                "asking": 1_600_000,
                "bedrooms": 4,
                "bathrooms": 3,
                "finished_sqft": 2_000,
                "lot_dimensions": "33 x 122 ft",
                "property_type": "Detached House",
                "ownership_type": "Freehold",
                "listing_status": "Active",
            }],
        }),
        encoding="utf-8",
    )

    def fake_enrich(cards, config, *, observed_at):
        events.append("enrich")
        enriched_card = cards[0].model_copy(deep=True)
        enriched_card.raw["official_zoning"] = "R1-1"
        return DiscoveryOfficialEnrichmentResult(
            cards=[enriched_card],
            status="COMPLETED",
            attempted_card_count=1,
            enriched_card_count=1,
        )

    original_screen = discovery_pipeline.screen_discovery_candidates

    def screen_after_enrichment(cards, config):
        events.append("screen")
        assert cards[0].raw["official_zoning"] == "R1-1"
        return original_screen(cards, config)

    monkeypatch.setattr(discovery_pipeline, "enrich_discovery_cards", fake_enrich)
    monkeypatch.setattr(discovery_pipeline, "screen_discovery_candidates", screen_after_enrichment)
    result = run_discovery(
        config,
        preset_name="vancouver-broad-scan",
        capture_path=capture_path,
        official_enrich=True,
    )

    assert result.status == "COMPLETED"
    assert events == ["enrich", "screen"]
    assert result.official_enrichment is not None


def test_discovery_over_cap_capture_has_no_candidates(tmp_path):
    config = load_config("config/research-discovery.yaml")
    capture_path = tmp_path / "over-cap.json"
    payload = {
        "source_name": "filtered-capture-replay",
        "preset_name": "vancouver-west",
        "source_url": "https://example.test/filtered",
        "result_count": 501,
        "count_is_complete": True,
        "applied_filters": {"property_type": "DETACHED_HOUSE"},
        "cards": [],
    }
    capture_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_discovery(
        config,
        preset_name="vancouver-west",
        capture_path=capture_path,
    )

    assert result.status == "FAILED"
    assert result.candidate_count == 0
    assert result.candidates == []
    assert result.validation is not None
    assert result.validation.status is DiscoveryStatus.RESULT_SET_TOO_LARGE


def test_discovery_unknown_preset_is_explicit_error():
    config = load_config("config/research-discovery.yaml")

    with pytest.raises(ValueError, match="unknown discovery preset"):
        run_discovery(config, preset_name="not-configured")


def test_discovery_without_enabled_source_returns_explicit_status():
    config = load_config("config/research-discovery.yaml")
    config["discovery"]["sources"] = [
        {"name": "disabled", "type": "JSON_CAPTURE", "enabled": False}
    ]

    result = run_discovery(config, preset_name="vancouver-west")

    assert result.status == "NO_DISCOVERY_SOURCE"
    assert result.candidate_count == 0
    assert result.candidates == []


def test_source_factory_builds_json_capture_and_resolves_capture_override(tmp_path):
    config = load_config("config/research-discovery.yaml")
    source_config = config["discovery"]["sources"][0]
    capture = tmp_path / "capture.json"
    capture.write_text("{}", encoding="utf-8")

    source = _build_discovery_source(config, source_config, capture)

    assert isinstance(source, JsonFilteredCaptureSource)
    assert source.path == capture


def test_research_discover_cli_prints_json_and_does_not_call_legacy_pipeline(monkeypatch):
    def forbidden_pipeline(*args, **kwargs):
        raise AssertionError("research-discover must not call legacy pipeline")

    monkeypatch.setattr("northshore_watchdog.cli.run_research_pipeline", forbidden_pipeline)
    result = RUNNER.invoke(
        app,
        [
            "research-discover",
            "--config",
            str(ROOT / "config/research-discovery.yaml"),
            "--preset",
            "vancouver-west",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["filter_plan"]["preset_name"] == "vancouver-west"
    assert payload["candidate_count"] == 0


def test_discovery_report_and_source_run_history_are_append_only(tmp_path):
    config = load_config("config/research-discovery.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["discovery"]["reports_dir"] = str(tmp_path / "reports" / "discovery")
    capture_path = tmp_path / "valid.json"
    capture_path.write_text(
        json.dumps(
            {
                "source_name": "filtered-capture-replay",
                "source_url": "https://example.test/filtered",
                "result_count": 1,
                "count_is_complete": True,
                "applied_filters": {"property_type": "DETACHED_HOUSE"},
                "cards": [
                    {
                        "address": "101 Append Only Street",
                        "mls_number": "R3000003",
                            "municipality": "Vancouver",
                            "neighbourhood": "Dunbar",
                        "asking": 1_688_000,
                        "bedrooms": 4,
                        "bathrooms": 3,
                        "finished_sqft": 1_850,
                        "property_type": "Detached House",
                        "ownership_type": "Freehold",
                            "listing_status": "Active",
                            "lot_area_sqft": 5_000,
                                "lot_dimensions": "50 x 120 ft",
                                "year_built": 2000,
                                "dom": "301 days",
                                "description": (
                                    "Well-maintained updated home on a quiet residential street near a park. "
                                    "R1-1 zoning, lane access and duplex potential."
                                ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    first = run_discovery(
        config,
        preset_name="vancouver-west",
        capture_path=capture_path,
        observed_at="2026-09-03T01:00:00+00:00",
    )
    second = run_discovery(
        config,
        preset_name="vancouver-west",
        capture_path=capture_path,
        observed_at="2026-09-04T01:00:00+00:00",
    )

    first_ids = persist_discovery_result(first, config)
    second_ids = persist_discovery_result(second, config)
    report_path, latest_path = write_discovery_report(first, config)

    assert len(first_ids) == 1
    assert len(second_ids) == 1
    assert report_path == tmp_path / "reports" / "discovery" / "2026-09-03.md"
    assert latest_path == tmp_path / "reports" / "discovery" / "latest.md"
    report = report_path.read_text(encoding="utf-8")
    assert "Executive Summary" in report
    assert "Filter Validation" in report
    assert "101 Append Only Street" in report
    assert "not a complete MLS-market scan" in report
    assert "LIVE SCORE" not in report
    assert "official zoning:" not in report.lower()

    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        runs = storage.list_source_runs()
    assert len(runs) == 2
    assert runs[0].source_run_id != runs[1].source_run_id
    for run in runs:
        discovery_payload = run.crawl_metrics["discovery"]
        assert discovery_payload["filter_plan"]["preset_name"] == "vancouver-west"
        assert discovery_payload["candidate_count"] == 1
        assert run.records == []


def test_research_discover_cli_persists_report_for_non_dry_run(tmp_path):
    capture_path = tmp_path / "valid.json"
    capture_path.write_text(
        json.dumps(
            {
                "source_name": "filtered-capture-replay",
                "source_url": "https://example.test/filtered",
                "result_count": 1,
                "count_is_complete": True,
                "applied_filters": {"property_type": "DETACHED_HOUSE"},
                "cards": [
                    {
                        "address": "101 CLI Discovery Street",
                        "asking": 1_688_000,
                        "bedrooms": 4,
                        "bathrooms": 3,
                        "finished_sqft": 1_850,
                        "property_type": "Detached House",
                        "ownership_type": "Freehold",
                        "listing_status": "Active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "extends": str(ROOT / "config/research-discovery.yaml"),
                "storage": {"sqlite_path": str(tmp_path / "research.sqlite")},
                "discovery": {"reports_dir": str(tmp_path / "reports" / "discovery")},
            }
        ),
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        app,
        [
            "research-discover",
            "--config",
            str(config_path),
            "--preset",
            "vancouver-west",
            "--capture",
            str(capture_path),
            "--observed-at",
            "2026-09-03T01:00:00+00:00",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "COMPLETED"
    assert payload["candidate_count"] == 1
    assert len(payload["source_run_ids"]) == 1
    assert payload["report_path"].endswith("reports/discovery/2026-09-03.md")
    assert Path(payload["report_path"]).is_file()
    with ResearchStorage(tmp_path / "research.sqlite") as storage:
        runs = storage.list_source_runs()
    assert len(runs) == 1
    assert runs[0].records == []
