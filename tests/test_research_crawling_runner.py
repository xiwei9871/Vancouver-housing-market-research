from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import northshore_watchdog.research.crawling.runner as runner
from northshore_watchdog.research.crawling.artifacts import ArtifactWriter
from northshore_watchdog.research.crawling.models import CrawlRunSummary


ENTRYPOINT = "https://www.zolo.ca/vancouver-real-estate/houses"


def _config() -> dict:
    return {
        "acquisition": {
            "crawl": {
                "respect_robots": True,
                "concurrency_per_domain": 1,
                "delay_seconds": 1.0,
                "max_retries": 2,
                "max_search_pages": 20,
                "max_detail_requests": 30,
                "timeout_seconds": 45,
                "headless": True,
                "browser": "chromium",
            },
            "crawl_sources": [
                {
                    "name": "zolo-full-market",
                    "source_registry_id": "source:zolo-full-market",
                    "allowed_domains": ["www.zolo.ca", "zolo.ca"],
                    "entrypoints": [{"municipality": "Vancouver", "url": ENTRYPOINT}],
                }
            ],
        }
    }


def _write_search_artifact(
    writer: ArtifactWriter,
    *,
    coverage_state: str,
    error_type: str | None = None,
    card_count: int = 1,
    body: bytes = b"<html>listing</html>",
) -> None:
    writer.write(
        request_kind="SEARCH",
        requested_url=ENTRYPOINT,
        final_url=ENTRYPOINT,
        entrypoint_url=ENTRYPOINT,
        body=body,
        status_code=200,
        headers={"content-type": "text/html"},
        card_count=card_count,
        extracted_row_count=card_count,
        coverage_state=coverage_state,
        error_type=error_type,
    )


class FakeSpider:
    pass


class RecordingProcess:
    def __init__(self, settings: dict, *, write_manifest: str | None = None, fail_on_start: bool = False):
        self.settings = settings
        self.write_manifest = write_manifest
        self.fail_on_start = fail_on_start
        self.crawl_calls: list[tuple[object, object, object]] = []
        self.start_calls = 0

    def crawl(self, spider_cls, source, context):
        self.crawl_calls.append((spider_cls, source, context))
        if self.write_manifest == "partial":
            _write_search_artifact(
                context["artifact_writer"],
                coverage_state="PAGINATION_NOT_ADVANCED",
            )
        elif self.write_manifest == "success":
            _write_search_artifact(
                context["artifact_writer"],
                coverage_state="COVERAGE_COMPLETE",
            )
        elif self.write_manifest == "blocked":
            _write_search_artifact(
                context["artifact_writer"],
                coverage_state="BLOCKED_POLICY",
                error_type="CHALLENGE_DETECTED",
                card_count=0,
                body=b"<html>challenge</html>",
            )

    def start(self):
        self.start_calls += 1
        if self.fail_on_start:
            raise RuntimeError("fake crawler failed")


def _inject_dependencies(monkeypatch, process: RecordingProcess) -> None:
    monkeypatch.setattr(
        runner,
        "_load_crawler_dependencies",
        lambda: (lambda settings: process, FakeSpider),
    )


def test_job_directory_is_unique_per_source_and_run(tmp_path):
    first = runner.build_crawl_job_dir(tmp_path, "zolo-full-market", "run-1")
    second = runner.build_crawl_job_dir(tmp_path, "zolo-full-market", "run-2")

    assert first != second
    assert first == tmp_path / "zolo-full-market" / "run-1" / "scrapy-jobdir"
    assert second == tmp_path / "zolo-full-market" / "run-2" / "scrapy-jobdir"
    assert first.is_dir()
    assert second.is_dir()


def test_crawl_settings_enable_async_scrapy_playwright_and_safety_defaults(tmp_path):
    settings = runner.crawl_settings({}, tmp_path / "job")

    assert settings == {
        "ROBOTSTXT_OBEY": True,
        "USER_AGENT": None,
        "ROBOTSTXT_USER_AGENT": "*",
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "RETRY_TIMES": 2,
        "JOBDIR": str(tmp_path / "job"),
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 45_000,
        "LOG_ENABLED": False,
    }


def test_crawl_settings_clamp_numeric_boundaries_without_importing_scrapy(tmp_path):
    settings = runner.crawl_settings(
        {
            "respect_robots": 0,
            "concurrency_per_domain": 0,
            "delay_seconds": -1,
            "max_retries": -3,
            "headless": False,
            "browser": "webkit",
            "timeout_seconds": 0,
        },
        tmp_path / "job",
    )

    assert settings["ROBOTSTXT_OBEY"] is False
    assert settings["CONCURRENT_REQUESTS_PER_DOMAIN"] == 1
    assert settings["DOWNLOAD_DELAY"] == 0.0
    assert settings["RETRY_TIMES"] == 0
    assert settings["PLAYWRIGHT_BROWSER_TYPE"] == "webkit"
    assert settings["PLAYWRIGHT_LAUNCH_OPTIONS"] == {"headless": False}
    assert settings["PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT"] == 0


def test_run_crawl_schedules_one_spider_starts_once_and_summarizes_partial(tmp_path, monkeypatch):
    process = RecordingProcess({}, write_manifest="partial")
    _inject_dependencies(monkeypatch, process)
    config = _config()
    original_crawl = dict(config["acquisition"]["crawl"])

    summary = runner.run_crawl(
        config,
        "zolo-full-market",
        "run-partial",
        tmp_path,
        crawler_process_factory=lambda settings: process,
        overrides={"max_search_pages": 3, "max_detail_requests": 4},
    )

    assert isinstance(summary, CrawlRunSummary)
    assert summary.status == "PARTIAL"
    assert summary.coverage_complete is False
    assert summary.coverage_gaps == ["PAGINATION_NOT_ADVANCED"]
    assert process.start_calls == 1
    assert len(process.crawl_calls) == 1
    spider_cls, source, context = process.crawl_calls[0]
    assert spider_cls is FakeSpider
    assert source["name"] == "zolo-full-market"
    assert context["run_id"] == "run-partial"
    assert isinstance(context["observed_at"], datetime)
    assert context["crawl"]["max_search_pages"] == 3
    assert context["crawl"]["max_detail_requests"] == 4
    assert config["acquisition"]["crawl"] == original_crawl
    assert Path(summary.job_dir).name == "scrapy-jobdir"
    assert Path(summary.job_dir).is_dir()
    assert Path(summary.artifact_dir).is_dir()
    assert Path(summary.job_dir).parent == Path(summary.artifact_dir).parent
    assert Path(summary.job_dir) != Path(summary.artifact_dir)
    assert Path(summary.summary_path).exists()
    assert CrawlRunSummary.model_validate_json(Path(summary.summary_path).read_text()).status == "PARTIAL"


def test_run_crawl_marks_natural_completion_success(tmp_path, monkeypatch):
    process = RecordingProcess({}, write_manifest="success")
    _inject_dependencies(monkeypatch, process)

    summary = runner.run_crawl(
        _config(),
        "zolo-full-market",
        "run-success",
        tmp_path,
        crawler_process_factory=lambda settings: process,
    )

    assert summary.status == "SUCCESS"
    assert summary.coverage_complete is True
    assert summary.entrypoints == 1
    assert summary.entrypoints_complete == 1
    assert process.start_calls == 1


def test_run_crawl_marks_zero_data_challenge_blocked_policy(tmp_path, monkeypatch):
    process = RecordingProcess({}, write_manifest="blocked")
    _inject_dependencies(monkeypatch, process)

    summary = runner.run_crawl(
        _config(),
        "zolo-full-market",
        "run-blocked",
        tmp_path,
        crawler_process_factory=lambda settings: process,
    )

    assert summary.status == "BLOCKED_POLICY"
    assert summary.coverage_complete is False
    assert summary.failed_requests == 0
    assert summary.coverage_gaps == ["BLOCKED_POLICY"]


def test_run_crawl_converts_process_failure_to_failed_summary(tmp_path, monkeypatch):
    process = RecordingProcess({}, fail_on_start=True)
    _inject_dependencies(monkeypatch, process)

    summary = runner.run_crawl(
        _config(),
        "zolo-full-market",
        "run-failed",
        tmp_path,
        crawler_process_factory=lambda settings: process,
    )

    assert summary.status == "FAILED"
    assert summary.coverage_complete is False
    assert process.start_calls == 1
    assert Path(summary.summary_path).exists()
    assert "RUNNER_ERROR" in summary.errors


def test_run_crawl_requires_the_crawler_extra_even_with_no_factory(tmp_path, monkeypatch):
    def missing_dependencies():
        raise ImportError("scrapy is unavailable")

    monkeypatch.setattr(runner, "_load_crawler_dependencies", missing_dependencies)

    with pytest.raises(RuntimeError, match=r"install \.\[crawler\]"):
        runner.run_crawl(_config(), "zolo-full-market", "run-missing", tmp_path)
