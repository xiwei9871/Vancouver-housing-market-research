from datetime import datetime, timezone

from northshore_watchdog.config import load_config
from northshore_watchdog.research.crawling.models import CrawlArtifact
from northshore_watchdog.research.models import PropertyRecord
from northshore_watchdog.research.pipeline import ResearchRunResult, run_research_pipeline
from northshore_watchdog.research.report import build_weekly_report, summarize_research_run
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)


def make_result_with_source_run(*, source_name, status, coverage_complete, crawl_metrics):
    source_run = SourceRunResult(
        source_registry_id=f"source:{source_name}",
        source_name=source_name,
        status=SourceRunStatus(status),
        coverage_complete=coverage_complete,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        crawl_metrics=crawl_metrics,
        coverage_gaps=crawl_metrics.get("coverage_gaps", []),
    )
    return ResearchRunResult(
        run_type="test",
        status="SUCCESS",
        observed_at=OBSERVED_AT,
        source_runs=[source_run],
    )


def test_full_market_summary_exposes_coverage_and_stage1_counts():
    result = make_result_with_source_run(
        source_name="realtor-full-market",
        status="PARTIAL",
        coverage_complete=False,
        crawl_metrics={
            "entrypoints": 5,
            "pages_fetched": 1,
            "cards_found": 11,
            "details_fetched": 4,
            "rows_extracted": 4,
            "duplicates": 1,
            "errors": ["one detail failed"],
            "coverage_gaps": ["PAGINATION_NOT_ADVANCED"],
        },
    )
    summary = summarize_research_run(
        result,
        stage1_counts={
            "normalized_records": 4,
            "in_range": 2,
            "excluded": 2,
            "NEED_VERIFICATION": 1,
        },
    )

    crawl = summary["crawl"]["realtor-full-market"]
    assert crawl == {
        "status": "PARTIAL",
        "coverage": "PARTIAL",
        "pages_fetched": 1,
        "cards_found": 11,
        "details_fetched": 4,
        "rows_extracted": 4,
        "duplicates": 1,
        "errors": ["one detail failed"],
        "coverage_gaps": ["PAGINATION_NOT_ADVANCED"],
    }
    assert summary["stage1_counts"] == {
        "raw_cards": 11,
        "normalized_records": 4,
        "in_range": 2,
        "excluded": 2,
        "NEED_VERIFICATION": 1,
    }


def test_blocked_crawl_summary_preserves_blocked_policy_coverage():
    result = make_result_with_source_run(
        source_name="zolo-full-market",
        status="BLOCKED_POLICY",
        coverage_complete=False,
        crawl_metrics={
            "pages_fetched": 4,
            "cards_found": 0,
            "details_fetched": 0,
            "rows_extracted": 0,
            "errors": ["CHALLENGE_DETECTED"],
            "coverage_gaps": ["BLOCKED_POLICY"],
        },
    )

    summary = summarize_research_run(result)

    assert summary["crawl"]["zolo-full-market"]["status"] == "BLOCKED_POLICY"
    assert summary["crawl"]["zolo-full-market"]["coverage"] == "BLOCKED_POLICY"

    report = build_weekly_report(
        result.records,
        source_runs=result.source_runs,
        generated_at=OBSERVED_AT,
    )

    assert "status=BLOCKED_POLICY; coverage=BLOCKED_POLICY" in report


def test_old_report_without_crawl_source_remains_renderable():
    record = PropertyRecord(
        property_id="report:legacy",
        address="1 Legacy Street",
        municipality="City of Vancouver",
    )
    report = build_weekly_report([record], generated_at=OBSERVED_AT)

    assert "## Executive Summary" in report
    assert "## Acquisition Source Policy" in report
    assert "crawl source metrics" not in report.lower()


def test_weekly_report_displays_crawl_metrics_and_stage1_counts():
    result = make_result_with_source_run(
        source_name="zolo-full-market",
        status="PARTIAL",
        coverage_complete=False,
        crawl_metrics={
            "pages_fetched": 3,
            "cards_found": 27,
            "details_fetched": 9,
            "rows_extracted": 9,
            "duplicates": 2,
            "errors": [],
            "coverage_gaps": ["DETAILS_SKIPPED"],
        },
    )
    report = build_weekly_report(
        result.records,
        source_runs=result.source_runs,
        stage1_counts={
            "normalized_records": 9,
            "in_range": 5,
            "excluded": 4,
            "NEED_VERIFICATION": 2,
        },
        generated_at=OBSERVED_AT,
    )

    assert "zolo-full-market" in report
    assert "pages_fetched=3" in report
    assert "cards_found=27" in report
    assert "stage1_raw_cards: 27" in report
    assert "stage1_normalized_records: 9" in report
    assert "stage1_in_range: 5" in report
    assert "stage1_excluded: 4" in report
    assert "stage1_NEED_VERIFICATION: 2" in report
    assert "DETAILS_SKIPPED" in report


def test_pipeline_summary_receives_crawl_source_metrics(tmp_path):
    config = load_config("config/research.yaml")
    config["storage"]["sqlite_path"] = str(tmp_path / "research.sqlite")
    entrypoint = "https://www.zolo.ca/vancouver-real-estate/houses"
    artifact = CrawlArtifact(
        run_id="crawl-report-1",
        source_name="zolo-full-market",
        source_registry_id="source:zolo-full-market",
        entrypoint_url=entrypoint,
        request_kind="SEARCH",
        requested_url=entrypoint,
        final_url=entrypoint,
        status_code=200,
        content_type="text/html",
        sha256="a" * 64,
        size_bytes=1,
        retrieved_at=OBSERVED_AT,
        rows=[],
        card_count=7,
        extracted_row_count=0,
        coverage_state="PARTIAL_CAP_REACHED",
    )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(artifact.model_dump_json() + "\n", encoding="utf-8")
    config["acquisition"]["crawl_sources"] = [{
        "name": "zolo-full-market",
        "source_type": "AGGREGATOR",
        "source_role": "ZOLO_PUBLIC",
        "source_registry_id": "source:zolo-full-market",
        "entrypoints": [{"url": entrypoint}],
    }]

    result = run_research_pipeline(
        config,
        stages={"acquire"},
        crawl_manifest_paths=[manifest],
        dry_run=True,
        observed_at=OBSERVED_AT,
    )

    assert result.summary["crawl"]["zolo-full-market"]["coverage"] == "PARTIAL"
    assert result.summary["crawl"]["zolo-full-market"]["cards_found"] == 7
    assert result.summary["stage1_counts"]["raw_cards"] == 7
    assert not any(
        gap.startswith("Default Phase 2–5 inputs are local validation fixtures")
        for gap in result.summary["coverage_gaps"]
    )
