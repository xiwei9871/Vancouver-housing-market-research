from __future__ import annotations

import json

from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.research.location_valuation import run_location_research


def test_location_runner_writes_the_ten_requested_artifacts(tmp_path):
    input_path = tmp_path / "regional.json"
    input_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "mls_number": "R1",
                        "address": "1 Test Street",
                        "municipality": "Vancouver",
                        "asking": 1_500_000,
                        "neighbourhood": "Knight",
                        "property_type": "Single Family",
                        "ownership_type": "Freehold",
                        "lot_area_sqft": 4_000,
                        "finished_sqft": 1_800,
                        "raw": {
                            "official_assessment_improvement": 300_000,
                            "official_elementary_catchment": "Test Elementary",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_location_research(input_path=input_path, output_dir=tmp_path / "location")

    expected = {
        "location-methodology-v1.md",
        "location-feature-schema-v1.json",
        "school-premium-v1.csv",
        "area-baseline-premium-v1.csv",
        "micro-location-premium-v1.csv",
        "matched-pairs-v1.csv",
        "hedonic-model-v1.md",
        "benchmark-validation-v1.md",
        "location-model-config-v1.yaml",
        "RECOMMENDATION.md",
    }
    actual = {path.name for path in (tmp_path / "location").iterdir()}
    assert actual == expected
    assert result["summary"]["property_count"] == 1
    assert result["summary"]["sale_count"] == 0


def test_premium_csvs_preserve_evidence_columns(tmp_path):
    input_path = tmp_path / "regional.json"
    input_path.write_text(json.dumps({"candidates": []}), encoding="utf-8")

    run_location_research(input_path=input_path, output_dir=tmp_path / "location")

    required = {"value", "applied_value", "method", "sample_size", "confidence", "automatic_rankable"}
    for name in ("school-premium-v1.csv", "area-baseline-premium-v1.csv", "micro-location-premium-v1.csv"):
        header = (tmp_path / "location" / name).read_text(encoding="utf-8").splitlines()[0].split(",")
        assert required.issubset(header)


def test_feature_schema_documents_both_accounts(tmp_path):
    input_path = tmp_path / "regional.json"
    input_path.write_text(json.dumps({"candidates": []}), encoding="utf-8")

    run_location_research(input_path=input_path, output_dir=tmp_path / "location")

    schema = json.loads((tmp_path / "location" / "location-feature-schema-v1.json").read_text(encoding="utf-8"))
    assert schema["model_separation"]["model_a"] == "price_normalization"
    assert schema["model_separation"]["model_b"] == "investment_redevelopment"
    assert "normalized_physical_lot_basis" in schema["price_normalization"]
    assert "combined_value" in schema["location_value"]["school"]


def test_research_location_cli_passes_explicit_local_inputs(tmp_path, monkeypatch):
    captured = {}

    def fake_run_location_research(**kwargs):
        captured.update(kwargs)
        return {"summary": {"property_count": 166}, "output_paths": []}

    monkeypatch.setattr("northshore_watchdog.cli.run_location_research", fake_run_location_research)
    result = CliRunner().invoke(
        app,
        [
            "research-location",
            "--input-path", "regional.json",
            "--history-path", "history.json",
            "--comparables-path", "comps.json",
            "--school-path", "schools.json",
            "--output-dir", "location-output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "input_path": "regional.json",
        "history_path": "history.json",
        "comparables_path": "comps.json",
        "school_path": "schools.json",
        "output_dir": "location-output",
        "observed_at": None,
    }
