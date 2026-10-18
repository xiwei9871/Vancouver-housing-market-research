import json
import os
import sqlite3
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from typer.testing import CliRunner

from northshore_watchdog.cli import app
from northshore_watchdog.config import load_config


RUNNER = CliRunner()
WRAPPER = Path(__file__).parents[1] / "scripts" / "run_weekly_watchdog.sh"


def _set_recent_mtime(path: Path) -> None:
    observed = datetime.now().astimezone() - timedelta(days=1)
    timestamp = observed.timestamp()
    os.utime(path, (timestamp, timestamp))


def _write_purchase_snapshot(path: Path, *, price: int, mls: str = "R3000001") -> None:
    path.write_text(
        f"""
        <article class="listing-card"
          data-address="1 Main Street" data-city="North Vancouver"
          data-url="https://www.realtor.ca/real-estate/1/1-main-street-north-vancouver"
          data-mls-number="{mls}" data-price="${price:,}"
          data-property-type="detached house" data-tenure="freehold" data-status="active"
          data-bedrooms="3" data-bathrooms="2" data-house-size="1800"
          data-lot-size="6000" data-frontage="45" data-year-built="1980">
        </article>
        """,
        encoding="utf-8",
    )
    _set_recent_mtime(path)


def _write_rental_snapshot(path: Path, *, rent: int) -> None:
    path.write_text(
        f"""
        <article class="rental-card"
          data-address="101 - 1 Main Street" data-city="Richmond"
          data-neighbourhood="Lansdowne"
          data-url="https://www.zolo.ca/richmond-real-estate/1-main-street/101"
          data-rent="${rent:,}" data-bedrooms="2" data-bathrooms="2"
          data-property-type="Condo" data-transit-minutes="6"
          data-amenities="elevator,gym,pool,in-suite laundry">
          Near SkyTrain and T&T.
        </article>
        """,
        encoding="utf-8",
    )
    _set_recent_mtime(path)


def _write_config(tmp_path: Path, *, kind: str, input_dir: Path, database: Path, report: Path) -> Path:
    source = load_config("config/real.yaml" if kind == "purchase" else "config/rentals.yaml")
    source["monitoring"] = {
        "max_age_days": 7,
        "required_collectors": ["search_page_snapshot" if kind == "purchase" else "snapshot"],
        "require_each_input_dir": True,
        "alert_dir": str(tmp_path / "alerts"),
    }
    if kind == "purchase":
        source["collectors"]["search_page_snapshot"]["input_dirs"] = [str(input_dir)]
        source["collectors"]["email_alert"]["enabled"] = False
        source["assessment"]["input_path"] = str(tmp_path / "missing-assessment.csv")
    else:
        source["collectors"]["snapshot"]["input_dirs"] = [str(input_dir)]
    source["storage"]["sqlite_path"] = str(database)
    source["report"]["weekly_output"] = str(report)
    source["run"] = {"send_notifications": False}
    config_path = tmp_path / f"{kind}-{input_dir.name}.yaml"
    serializable = {key: value for key, value in source.items() if not key.startswith("_")}
    config_path.write_text(yaml.safe_dump(serializable, sort_keys=False), encoding="utf-8")
    return config_path


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_openclaw_double(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "agent" ]]; then
  printf '%s\\n' agent >> "$CALL_LOG"
  if [[ "${AGENT_FAIL:-0}" == "1" ]]; then
    exit 1
  fi
elif [[ "${1:-}" == "message" ]]; then
  printf '%s\\n' message >> "$CALL_LOG"
fi
""",
        encoding="utf-8",
    )
    _make_executable(path)


def _write_watchdog_double(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
command_name="${1:-}"
printf '%s\\n' "$command_name" >> "$CALL_LOG"
status_path=""
previous=""
for argument in "$@"; do
  if [[ "$previous" == "--status-path" ]]; then
    status_path="$argument"
    break
  fi
  previous="$argument"
done
if [[ "$command_name" == "validate-acquisition" ]]; then
  mkdir -p "$(dirname "$status_path")"
  if [[ "${VALIDATE_FAIL:-0}" == "1" ]]; then
    printf '%s\\n' '{"status":"error","reasons":["search failed"]}' > "$status_path"
    exit 1
  fi
  printf '%s\\n' '{"status":"fresh","entries_validated":4,"reasons":[]}' > "$status_path"
  exit 0
fi
mkdir -p "$(dirname "$status_path")"
printf '%s\\n' '{"status":"fresh","reasons":[]}' > "$status_path"
""",
        encoding="utf-8",
    )
    _make_executable(path)


def _run_wrapper_test(tmp_path: Path, *, acquisition_fails: bool = False) -> tuple[list[str], str]:
    project = tmp_path / "project"
    project.mkdir()
    openclaw = tmp_path / "openclaw"
    watchdog = tmp_path / "watchdog"
    _write_openclaw_double(openclaw)
    _write_watchdog_double(watchdog)
    call_log = tmp_path / "calls.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PROJECT_DIR": str(project),
            "OPENCLAW_BIN": str(openclaw),
            "WATCHDOG_BIN": str(watchdog),
            "CALL_LOG": str(call_log),
            "SEND_FEISHU": "0",
            "VALIDATE_FAIL": "1" if acquisition_fails else "0",
        }
    )
    result = subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    return calls, result.stdout


def test_wrapper_searches_and_validates_before_scoring(tmp_path):
    calls, output = _run_wrapper_test(tmp_path)

    assert calls == ["agent", "validate-acquisition", "run-weekly", "rentals-run-weekly"]
    assert "delivery_mode=fresh" in output


def test_wrapper_does_not_enter_fresh_delivery_when_search_validation_fails(tmp_path):
    calls, output = _run_wrapper_test(tmp_path, acquisition_fails=True)

    assert calls == ["agent", "validate-acquisition"]
    assert "delivery_mode=error" in output


def test_purchase_weekly_stale_run_preserves_previous_report_and_db(tmp_path):
    report = tmp_path / "purchase.md"
    report.write_text("previous normal report", encoding="utf-8")
    database = tmp_path / "purchase.sqlite"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    snapshot = source_dir / "old.html"
    _write_purchase_snapshot(snapshot, price=1_500_000)
    old = (datetime.now().astimezone() - timedelta(days=8)).timestamp()
    os.utime(snapshot, (old, old))
    config_path = _write_config(
        tmp_path,
        kind="purchase",
        input_dir=source_dir,
        database=database,
        report=report,
    )
    status_path = tmp_path / "purchase-status.json"

    result = RUNNER.invoke(
        app,
        ["run-weekly", "--config", str(config_path), "--status-path", str(status_path)],
    )

    assert result.exit_code == 0, result.output
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "stale"
    assert report.read_text(encoding="utf-8") == "previous normal report"
    assert not database.exists()
    assert list((tmp_path / "alerts").glob("purchase_stale_*.md"))


def test_fresh_purchase_runs_compare_stable_identity_and_price_change(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    _write_purchase_snapshot(first_dir / "week-one.html", price=1_500_000)
    _write_purchase_snapshot(second_dir / "week-two.html", price=1_400_000)
    database = tmp_path / "purchase.sqlite"
    report = tmp_path / "purchase.md"
    first_config = _write_config(tmp_path, kind="purchase", input_dir=first_dir, database=database, report=report)
    second_config = _write_config(tmp_path, kind="purchase", input_dir=second_dir, database=database, report=report)

    first = RUNNER.invoke(
        app,
        ["run-weekly", "--config", str(first_config), "--status-path", str(tmp_path / "first.json")],
    )
    second = RUNNER.invoke(
        app,
        ["run-weekly", "--config", str(second_config), "--status-path", str(tmp_path / "second.json")],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    status = json.loads((tmp_path / "second.json").read_text(encoding="utf-8"))
    assert status["status"] == "fresh"
    assert status["delta"]["baseline"] is False
    assert status["delta"]["price_reduced_count"] == 1
    assert status["delta"]["new_count"] == 0
    assert "Data status: fresh" in report.read_text(encoding="utf-8")
    assert "Weekly Changes" in report.read_text(encoding="utf-8")
    assert "Weekly New Listings" in report.read_text(encoding="utf-8")

    with sqlite3.connect(database) as connection:
        assert connection.execute("select count(*) from radar_runs").fetchone()[0] == 2
        assert connection.execute("select count(*) from radar_run_listings").fetchone()[0] == 2


def test_first_fresh_purchase_run_establishes_baseline_without_false_new_count(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    _write_purchase_snapshot(source_dir / "week-one.html", price=1_500_000)
    database = tmp_path / "purchase.sqlite"
    report = tmp_path / "purchase.md"
    config_path = _write_config(tmp_path, kind="purchase", input_dir=source_dir, database=database, report=report)

    result = RUNNER.invoke(
        app,
        ["run-weekly", "--config", str(config_path), "--status-path", str(tmp_path / "status.json")],
    )

    assert result.exit_code == 0, result.output
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["delta"]["baseline"] is True
    assert status["delta"]["new_count"] == 0


def test_rental_weekly_stale_run_preserves_previous_report(tmp_path):
    report = tmp_path / "rental.md"
    report.write_text("previous rental report", encoding="utf-8")
    database = tmp_path / "rental.sqlite"
    source_dir = tmp_path / "rental-sources"
    source_dir.mkdir()
    snapshot = source_dir / "old.html"
    _write_rental_snapshot(snapshot, rent=2_900)
    old = (datetime.now().astimezone() - timedelta(days=8)).timestamp()
    os.utime(snapshot, (old, old))
    config_path = _write_config(
        tmp_path,
        kind="rental",
        input_dir=source_dir,
        database=database,
        report=report,
    )

    result = RUNNER.invoke(
        app,
        ["rentals-run-weekly", "--config", str(config_path), "--status-path", str(tmp_path / "status.json")],
    )

    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["status"] == "stale"
    assert report.read_text(encoding="utf-8") == "previous rental report"
    assert not database.exists()


def test_fresh_rental_runs_report_rent_reduction(tmp_path):
    first_dir = tmp_path / "rental-first"
    second_dir = tmp_path / "rental-second"
    first_dir.mkdir()
    second_dir.mkdir()
    _write_rental_snapshot(first_dir / "week-one.html", rent=3_000)
    _write_rental_snapshot(second_dir / "week-two.html", rent=2_800)
    database = tmp_path / "rental.sqlite"
    report = tmp_path / "rental.md"
    first_config = _write_config(tmp_path, kind="rental", input_dir=first_dir, database=database, report=report)
    second_config = _write_config(tmp_path, kind="rental", input_dir=second_dir, database=database, report=report)

    first = RUNNER.invoke(
        app,
        ["rentals-run-weekly", "--config", str(first_config), "--status-path", str(tmp_path / "first.json")],
    )
    second = RUNNER.invoke(
        app,
        ["rentals-run-weekly", "--config", str(second_config), "--status-path", str(tmp_path / "second.json")],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    status = json.loads((tmp_path / "second.json").read_text(encoding="utf-8"))
    assert status["delta"]["price_reduced_count"] == 1
    text = report.read_text(encoding="utf-8")
    assert "数据状态: fresh" in text
    assert "每周变化" in text


def test_fresh_rental_deduplicates_same_listing_across_recent_snapshots(tmp_path):
    source_dir = tmp_path / "rental-sources"
    source_dir.mkdir()
    _write_rental_snapshot(source_dir / "week-one.html", rent=3_000)
    _write_rental_snapshot(source_dir / "week-two.html", rent=2_800)
    database = tmp_path / "rental.sqlite"
    report = tmp_path / "rental.md"
    config_path = _write_config(tmp_path, kind="rental", input_dir=source_dir, database=database, report=report)

    result = RUNNER.invoke(
        app,
        ["rentals-run-weekly", "--config", str(config_path), "--status-path", str(tmp_path / "status.json")],
    )

    assert result.exit_code == 0, result.output
    assert "- 总房源数: 1" in report.read_text(encoding="utf-8")
    with sqlite3.connect(database) as connection:
        assert connection.execute("select count(*) from radar_run_listings").fetchone()[0] == 1
