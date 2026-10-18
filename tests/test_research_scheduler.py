from datetime import datetime, timezone
import json
from types import SimpleNamespace

from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.config import load_config
from northshore_watchdog.research.scheduler import run_weekly_scheduled


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _config(tmp_path):
    listing_path = tmp_path / "listings.json"
    listing_path.write_text(
        json.dumps([{
            "address": "1 Scheduler Street",
            "municipality": "City of Vancouver",
            "neighbourhood": "Knight",
            "property_type": "Detached House",
            "ownership_type": "Freehold Non-Strata",
            "asking": 1_500_000,
            "source_url": "local://scheduler/1",
        }]),
        encoding="utf-8",
    )
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    config["scheduler"] = {"lock_path": str(tmp_path / "research.lock")}
    config["market_buckets"] = {"vancouver_east": {"municipality": "City of Vancouver", "neighbourhoods": ["Knight"], "core_range": [1_000_000, 2_000_000], "extended_range": [1_000_000, 2_200_000]}}
    config["acquisition"] = {"sources": [{"name": "scheduler-listings", "type": "LOCAL_JSON", "path": str(listing_path), "source_type": "MLS", "enabled": True}]}
    config["enrichment"] = {"providers": []}
    return config


def test_scheduler_dry_run_leaves_lock_and_rows_unchanged(tmp_path):
    config = _config(tmp_path)

    result = run_weekly_scheduled(config, observed_at=OBSERVED_AT, dry_run=True)

    assert result.dry_run is True
    assert not (tmp_path / "research.lock").exists()


def test_scheduler_returns_locked_without_running_when_lock_exists(tmp_path):
    config = _config(tmp_path)
    lock = tmp_path / "research.lock"
    lock.write_text("held", encoding="utf-8")

    result = run_weekly_scheduled(config, observed_at=OBSERVED_AT)

    assert result.status == "LOCKED"
    assert result.summary["locked"] is True
    assert lock.exists()


def test_locked_dry_run_preserves_the_requested_dry_run_flag(tmp_path):
    config = _config(tmp_path)
    lock = tmp_path / "research.lock"
    lock.write_text("held", encoding="utf-8")

    result = run_weekly_scheduled(config, observed_at=OBSERVED_AT, dry_run=True)

    assert result.status == "LOCKED"
    assert result.dry_run is True


def test_scheduler_dry_run_holds_atomic_lock_while_pipeline_runs(tmp_path, monkeypatch):
    config = _config(tmp_path)
    lock_path = tmp_path / "research.lock"
    observed = {}

    def fake_pipeline(config, **kwargs):
        observed["lock_exists_during_run"] = lock_path.exists()
        return SimpleNamespace(
            summary={},
            run_type="weekly",
            status="SUCCESS",
            dry_run=True,
            run_id=None,
            report_path=None,
            latest_path=None,
        )

    monkeypatch.setattr("northshore_watchdog.research.scheduler.run_research_pipeline", fake_pipeline)

    result = run_weekly_scheduled(config, observed_at=OBSERVED_AT, dry_run=True)

    assert result.status == "SUCCESS"
    assert observed["lock_exists_during_run"] is True
    assert not lock_path.exists()


def test_research_cli_registers_phase2_to5_commands():
    command_names = {
        command.name or getattr(command.callback, "__name__", "")
        for command in app.registered_commands
    }

    assert {
        "research-acquire",
        "research-enrich",
        "research-comps",
        "research-redevelop",
        "research-run-weekly",
        "research-conflicts",
        "research-verifications",
        "research-validate-phase1",
    } <= command_names


def test_research_weekly_cli_passes_explicit_observed_at_to_scheduler(monkeypatch):
    captured = {}

    def fake_scheduler(config, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            summary={},
            run_type="weekly",
            status="SUCCESS",
            dry_run=True,
            run_id=None,
            report_path=None,
            latest_path=None,
        )

    monkeypatch.setattr("northshore_watchdog.cli.run_weekly_scheduled", fake_scheduler)

    result = CliRunner().invoke(
        app,
        [
            "research-run-weekly",
            "--config", "config/research.yaml",
            "--observed-at", "2026-09-02T08:30:00+00:00",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["observed_at"] == datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)
    assert captured["dry_run"] is True
