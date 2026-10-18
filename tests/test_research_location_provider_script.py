import json

from scripts.run_location_provider_enrichment import _latest_input_artifact, main


def test_location_provider_cli_writes_snapshot_and_artifacts_without_credentials(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "property_id": "mls:RTEST",
                        "mls_number": "RTEST",
                        "address": "5454 MELBOURNE STREET",
                        "latitude": 49.235,
                        "longitude": -123.03,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "artifacts"
    snapshot_path = tmp_path / "snapshots.jsonl"
    output_json = tmp_path / "normalized.json"

    exit_code = main(
        [
            "--input", str(input_path),
            "--output-dir", str(output_dir),
            "--snapshot-path", str(snapshot_path),
            "--output-json", str(output_json),
            "--observed-at", "2026-09-08T00:00:00+00:00",
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["property_count"] == 1
    assert payload["complete_count"] == 0
    assert payload["snapshots"][0]["composite"]["status"] == "UNKNOWN"
    assert (output_dir / "provider-schema.json").is_file()
    assert (output_dir / "benchmark-results.csv").is_file()
    assert len(snapshot_path.read_text(encoding="utf-8").splitlines()) == 1


def test_default_input_is_a_saved_portfolio_artifact():
    assert _latest_input_artifact().name.startswith("portfolio-official-enrichment-")
