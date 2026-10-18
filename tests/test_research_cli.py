from pathlib import Path
import json
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.config import load_config


def test_research_validate_phase1_creates_local_artifacts_without_collectors(tmp_path, monkeypatch):
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    config["reports"]["phase1_dir"] = str(tmp_path / "phase1")
    config["reports"]["weekly_dir"] = str(tmp_path / "weekly")
    config["reports"]["latest_path"] = str(tmp_path / "latest.md")
    config["cases"]["manifest_path"] = str(
        Path(config["_project_root"]) / "data/research/cases/phase1_cases.yaml"
    )
    config_path = tmp_path / "research.yaml"
    config_to_write = {key: value for key, value in config.items() if not key.startswith("_")}
    config_path.write_text(yaml.safe_dump(config_to_write, sort_keys=False), encoding="utf-8")

    def fail_if_collector_called(*_args, **_kwargs):
        raise AssertionError("Phase 1 validation must not call remote collectors")

    monkeypatch.setattr("northshore_watchdog.cli._collect_listings", fail_if_collector_called)
    result = CliRunner().invoke(
        app,
        ["research-validate-phase1", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.output
    assert "run_type=phase1_validation" in result.output
    assert "traceable" in result.output.lower()
    assert "coverage_gaps" in result.output
    assert (tmp_path / "research.sqlite").exists()
    assert list((tmp_path / "phase1").glob("validation-*.md"))
    assert list((tmp_path / "weekly").glob("*.md"))
    assert (tmp_path / "latest.md").exists()


def test_research_command_does_not_change_legacy_command_registration():
    command_names = {
        command.name or getattr(command.callback, "__name__", "")
        for command in app.registered_commands
    }

    assert "collect" in command_names
    assert "run-weekly" in command_names
    assert "research-validate-phase1" in command_names


def test_research_acquire_from_crawl_passes_repeatable_manifest_paths(tmp_path, monkeypatch):
    captured = {}

    def fake_pipeline(config, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            summary={},
            run_type="research",
            status="SUCCESS",
            dry_run=True,
            run_id=None,
            report_path=None,
            latest_path=None,
        )

    monkeypatch.setattr("northshore_watchdog.cli.run_research_pipeline", fake_pipeline)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "research-acquire",
            "--from-crawl", str(first),
            "--from-crawl", str(second),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["crawl_manifest_paths"] == [str(first), str(second)]
    assert captured["stages"] == {"acquire"}
    assert captured["dry_run"] is True


def test_research_crawl_calls_lazy_runner_and_emits_json_summary(monkeypatch):
    captured = {}

    def fake_run_crawl(config, source_name, run_id, project_root, **kwargs):
        captured.update({
            "config": config,
            "source_name": source_name,
            "run_id": run_id,
            "project_root": project_root,
            **kwargs,
        })
        return SimpleNamespace(
            source_name=source_name,
            status="PARTIAL",
            coverage_complete=False,
            pages_fetched=2,
            cards_found=5,
            details_fetched=3,
            coverage_gaps=["PAGINATION_NOT_ADVANCED"],
            run_dir="run-dir",
            job_dir="job-dir",
            artifact_dir="artifact-dir",
            manifest_path="manifest.jsonl",
            summary_path="summary.json",
        )

    monkeypatch.setattr(
        "northshore_watchdog.research.crawling.runner.run_crawl",
        fake_run_crawl,
    )
    result = CliRunner().invoke(
        app,
        [
            "research-crawl",
            "--config", "config/research-full-market.yaml",
            "--source", "zolo-full-market",
            "--run-id", "crawl-run-1",
            "--max-search-pages", "3",
            "--max-detail-requests", "4",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source_name"] == "zolo-full-market"
    assert payload["status"] == "PARTIAL"
    assert payload["coverage_complete"] is False
    assert payload["pages_fetched"] == 2
    assert payload["cards_found"] == 5
    assert payload["details_fetched"] == 3
    assert payload["coverage_gaps"] == ["PAGINATION_NOT_ADVANCED"]
    assert payload["paths"]["manifest_path"] == "manifest.jsonl"
    assert captured["source_name"] == "zolo-full-market"
    assert captured["run_id"] == "crawl-run-1"
    assert captured["overrides"] == {
        "max_search_pages": 3,
        "max_detail_requests": 4,
    }


def test_research_crawl_accepts_multiple_sources(monkeypatch):
    calls = []

    def fake_run_crawl(config, source_name, run_id, project_root, **kwargs):
        calls.append(source_name)
        return SimpleNamespace(
            source_name=source_name,
            status="PARTIAL",
            coverage_complete=False,
            pages_fetched=1,
            cards_found=2,
            details_fetched=1,
            coverage_gaps=[],
            run_dir=f"{source_name}-run",
            job_dir=f"{source_name}-job",
            artifact_dir=f"{source_name}-artifacts",
            manifest_path=f"{source_name}.jsonl",
            summary_path=f"{source_name}.json",
        )

    monkeypatch.setattr(
        "northshore_watchdog.research.crawling.runner.run_crawl",
        fake_run_crawl,
    )
    result = CliRunner().invoke(
        app,
        [
            "research-crawl",
            "--config", "config/research-full-market.yaml",
            "--sources", "zolo-full-market,realtor-full-market",
            "--run-id", "crawl-run-multi",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert calls == ["zolo-full-market", "realtor-full-market"]
    assert [item["source_name"] for item in payload["runs"]] == calls
