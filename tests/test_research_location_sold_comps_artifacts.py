from __future__ import annotations

import json
from datetime import date

import yaml

from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.research.location_sold_comps import _quality_markdown, run_sold_comps_research


EXPECTED_FILES = {
    "sold-comps-v1.1.csv",
    "sold-comps-quality-report-v1.1.md",
    "neighbourhood-statistics-v1.1.csv",
    "physical-cohorts-v1.1.csv",
    "school-boundary-pairs-v1.1.csv",
    "road-matched-pairs-v1.1.csv",
    "park-matched-pairs-v1.1.csv",
    "view-matched-pairs-v1.1.csv",
    "school-premium-v1.1.csv",
    "area-baseline-premium-v1.1.csv",
    "micro-location-premium-v1.1.csv",
    "location-premium-initial-v1.1.csv",
    "location-premium-initial-v1.1.md",
    "matched-pairs-v1.1.csv",
    "hedonic-model-v1.1.md",
    "benchmark-validation-v1.1.md",
    "RECOMMENDATION-v1.1.md",
}


def _source(path):
    path.write_text(
        json.dumps(
            [
                {
                    "address": "123 Test Street",
                    "mls": "R1234567",
                    "sale_price": 1_700_000,
                    "sale_date": "2026-01-15",
                    "municipality": "City of Vancouver",
                    "property_type": "Detached House",
                    "title": "Freehold",
                    "neighbourhood": "Arbutus",
                    "lot_area": 4_000,
                    "lot_width": 33,
                    "living_area": 1_900,
                    "year_built": 1960,
                }
            ]
        ),
        encoding="utf-8",
    )


def test_sold_comps_runner_writes_versioned_v11_artifacts(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)

    result = run_sold_comps_research(
        source_paths=[source_path],
        output_dir=tmp_path / "location",
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    actual = {path.name for path in (tmp_path / "location").iterdir()}
    assert actual == EXPECTED_FILES
    assert result["summary"]["input_observations"] == 1
    assert result["summary"]["eligible_sales"] == 1
    assert result["summary"]["stage_gate"] == "INSUFFICIENT_SAMPLE"


def test_v11_run_does_not_overwrite_v1_artifacts(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)
    output_dir = tmp_path / "location"
    output_dir.mkdir()
    v1 = output_dir / "RECOMMENDATION.md"
    v1.write_text("V1 sentinel\n", encoding="utf-8")

    run_sold_comps_research(source_paths=[source_path], output_dir=output_dir)

    assert v1.read_text(encoding="utf-8") == "V1 sentinel\n"


def test_runner_includes_official_bca_history_source_in_inputs_and_report(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)
    bca_path = tmp_path / "regional-bca.json"
    bca_path.write_text(json.dumps({
        "candidates": [{
            "address": "3218 W 6TH AVENUE",
            "municipality": "Vancouver",
            "neighbourhood": "Kitsilano",
            "property_type": "Single Family",
            "ownership_type": "Freehold",
            "lot_area_sqft": 4_000,
            "finished_sqft": 1_900,
            "year_built": 1955,
            "raw": {
                "official_previous_sale_dates": ["2025-10-08"],
                "official_previous_sale_prices": [1_700_000],
            },
        }]
    }), encoding="utf-8")

    result = run_sold_comps_research(
        source_paths=[source_path],
        property_history_paths=[bca_path],
        output_dir=tmp_path / "location",
        start_date=date(2023, 9, 6),
        end_date=date(2026, 9, 5),
    )

    assert result["summary"]["input_observations"] == 2
    assert result["summary"]["eligible_sales"] == 2
    report = (tmp_path / "location" / "sold-comps-quality-report-v1.1.md").read_text(encoding="utf-8")
    assert str(bca_path) in report


def test_runner_can_write_new_artifact_version_without_overwriting_previous_version(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)
    output_dir = tmp_path / "location"
    output_dir.mkdir()
    previous = output_dir / "sold-comps-v1.1.csv"
    previous.write_text("previous\n", encoding="utf-8")

    result = run_sold_comps_research(
        source_paths=[source_path],
        output_dir=output_dir,
        artifact_version="v1.2",
    )

    assert (output_dir / "sold-comps-v1.2.csv").exists()
    assert previous.read_text(encoding="utf-8") == "previous\n"
    assert any(path.endswith("RECOMMENDATION-v1.2.md") for path in result["output_paths"])


def test_v14_run_writes_standard_property_profile_without_overwriting_v13(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)
    output_dir = tmp_path / "location"
    output_dir.mkdir()
    previous = output_dir / "location-premium-stability-v1.3.md"
    previous.write_text("V1.3 sentinel\n", encoding="utf-8")

    result = run_sold_comps_research(
        source_paths=[source_path],
        output_dir=output_dir,
        artifact_version="v1.4",
    )

    profile_path = output_dir / "location-standard-profile-v1.4.yaml"
    assert profile_path.exists()
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "location-standard-profile-v1.4"
    assert payload["profile"]["profile_name"] == "VANCOUVER_STANDARD_PROPERTY_V2"
    assert payload["profile"]["lot_area_sqft"] == 4026
    assert payload["profile"]["living_area_sqft"] == 1700
    assert payload["profile"]["year_built_sensitivity"] == [1940, 1960, 1980]
    assert payload["automatic_application"] is False
    assert previous.read_text(encoding="utf-8") == "V1.3 sentinel\n"
    assert str(profile_path) in result["output_paths"]


def test_runner_writes_initial_location_premium_artifacts(tmp_path):
    source_path = tmp_path / "sales.json"
    _source(source_path)

    run_sold_comps_research(
        source_paths=[source_path],
        output_dir=tmp_path / "location",
        artifact_version="v1.2",
    )

    premium_csv = tmp_path / "location" / "location-premium-initial-v1.2.csv"
    premium_report = tmp_path / "location" / "location-premium-initial-v1.2.md"
    assert premium_csv.exists()
    assert premium_report.exists()
    assert "Initial Location Premium Research V1.2" in premium_report.read_text(encoding="utf-8")


def test_quality_report_separates_current_official_zoning_from_transaction_mls():
    report = _quality_markdown(
        {
            "total_observations": 10,
            "eligible_sales": 10,
            "conflict_count": 0,
            "outside_target_scope_count": 0,
            "benchmark_count": 0,
            "benchmark_observed_count": 0,
            "coverage": {
                "current_official_zoning": 0.963,
                "current_listing_mls": 0.004,
                "verified_transaction_mls": 0.0,
            },
        },
        ["bca.json"],
        date(2023, 9, 6),
        date(2026, 9, 5),
    )

    assert "| current_official_zoning | 96.3% |" in report
    assert "| current_listing_mls | 0.4% |" in report
    assert "| verified_transaction_mls | 0.0% |" in report
    assert "road/zoning coverage remains insufficient" not in report


def test_benchmark_report_exposes_target_transaction_mls_without_promoting_seed(tmp_path):
    result = run_sold_comps_research(
        source_paths=[],
        output_dir=tmp_path / "location",
        artifact_version="v1.2",
    )

    report = (tmp_path / "location" / "benchmark-validation-v1.2.md").read_text(encoding="utf-8")
    assert "Transaction MLS" in report
    assert "R3117599" in report
    assert "SEED_INCOMPLETE" in report


def test_research_location_sold_comps_cli_passes_explicit_inputs(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"summary": {"eligible_sales": 0}, "output_paths": []}

    monkeypatch.setattr("northshore_watchdog.cli.run_sold_comps_research", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "research-location-sold-comps",
            "--source-path", "one.json",
            "--source-path", "two.csv",
            "--property-history-path", "bca.json",
            "--output-dir", "research/location",
            "--start-date", "2023-09-06",
            "--end-date", "2026-09-05",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "source_paths": ["one.json", "two.csv"],
        "output_dir": "research/location",
        "property_history_paths": ["bca.json"],
        "start_date": date(2023, 9, 6),
        "end_date": date(2026, 9, 5),
        "boundary_path": "data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson",
        "school_boundary_path": "data/research/normalized/school-boundaries/school-catchment-53a59061080d5749.json",
        "road_path": "data/research/raw/location/vancouver-public-streets-2026-09-06.json",
        "park_path": "data/research/raw/location/vancouver-parks-2026-09-06.geojson",
        "zoning_path": "data/research/raw/location/vancouver-zoning-2026-09-06.json",
        "artifact_version": "v1.2",
    }


def test_research_location_sold_comps_cli_reuses_local_bca_history_by_default(monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"summary": {}, "output_paths": []}

    monkeypatch.setattr("northshore_watchdog.cli.run_sold_comps_research", fake_run)
    result = CliRunner().invoke(
        app,
        ["research-location-sold-comps", "--source-path", "sales.json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["property_history_paths"] == [
        "data/research/normalized/regional-official-enrichment-2026-09-05.json"
    ]
    assert captured["zoning_path"] == "data/research/raw/location/vancouver-zoning-2026-09-06.json"
