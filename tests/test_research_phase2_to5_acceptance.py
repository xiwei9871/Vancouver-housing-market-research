from datetime import datetime, timedelta, timezone
import sqlite3

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import CandidateStatus
from northshore_watchdog.research.pipeline import run_research_pipeline
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _config(tmp_path):
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    return config


def test_default_phase2_to5_pipeline_is_read_only_to_sources_and_auditable(tmp_path, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("default research pipeline must not make network calls")

    monkeypatch.setattr("httpx.Client.request", fail_network)
    result = run_research_pipeline(_config(tmp_path), observed_at=OBSERVED_AT, dry_run=True)

    assert result.status == "SUCCESS"
    assert result.summary["properties_scanned"] == 9
    assert result.summary["stage1_candidates"] == 1
    assert result.summary["stage1_research_queue"] == 6
    assert result.summary["verification_queue_count"] == 5
    assert result.summary["deep_research_count"] == 1
    assert result.summary["stage_status"]["acquire"] == "COMPLETED"
    assert result.summary["stage_status"]["report"] == "COMPLETED"
    assert not (tmp_path / "research.sqlite").exists()


def test_fixture_results_preserve_title_gate_and_best_product_logic(tmp_path):
    result = run_research_pipeline(_config(tmp_path), observed_at=OBSERVED_AT)
    by_address = {record.address: record for record in result.records}

    assert by_address["88 Leasehold Lane"].candidate_status is CandidateStatus.REJECT
    assert by_address["1494 Ross Road"].candidate_status is CandidateStatus.SPECIAL_SITUATION
    assert by_address["1234 Knight Street"].best_economic_product is None
    assert by_address["1234 Knight Street"].outdoor_noise_exposure == 88
    assert by_address["1234 Knight Street"].price_reduction_pct < -0.10
    assert result.report_path and "1234 Knight Street" in result.report_path.read_text(encoding="utf-8")


def test_repeated_runs_append_observations_scores_scenarios_and_real_deltas(tmp_path):
    config = _config(tmp_path)
    first = run_research_pipeline(config, observed_at=OBSERVED_AT)
    with sqlite3.connect(tmp_path / "research.sqlite") as conn:
        first_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("property_observations", "score_snapshots", "redevelopment_scenarios", "source_runs")
        }
    second = run_research_pipeline(config, observed_at=OBSERVED_AT + timedelta(days=7))
    with sqlite3.connect(tmp_path / "research.sqlite") as conn:
        second_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in first_counts
        }
        observations = conn.execute("SELECT COUNT(*) FROM property_observations").fetchone()[0]

    assert second.status == "SUCCESS"
    assert second_counts["property_observations"] == first_counts["property_observations"] * 2
    assert second_counts["score_snapshots"] == first_counts["score_snapshots"] * 2
    assert second_counts["redevelopment_scenarios"] == first_counts["redevelopment_scenarios"] * 2
    assert second_counts["source_runs"] == first_counts["source_runs"] * 2
    assert observations == 18
    # A same-input run adds source status events but no property-field delta.
    assert not any(event.event_type == "price_reduction" for event in second.events)
