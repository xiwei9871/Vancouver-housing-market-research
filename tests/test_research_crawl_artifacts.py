import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from northshore_watchdog.research.crawling.artifacts import ArtifactWriter
from northshore_watchdog.research.crawling.models import CrawlArtifact, CrawlRunSummary
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
REQUESTED_URL = "https://www.example.com/listings"


def test_crawl_models_read_old_manifest_defaults():
    old_manifest = {
        "run_id": "run-old",
        "source_name": "example",
        "source_registry_id": "source:example",
        "request_kind": "SEARCH",
        "requested_url": REQUESTED_URL,
        "sha256": "a" * 64,
        "size_bytes": 0,
        "retrieved_at": OBSERVED_AT.isoformat(),
    }

    artifact = CrawlArtifact.model_validate(old_manifest)
    summary = CrawlRunSummary(
        run_id="run-old",
        source_name="example",
        started_at=OBSERVED_AT,
    )

    assert artifact.entrypoint_url is None
    assert artifact.rows == []
    assert artifact.parse_status == "NOT_RUN"
    assert artifact.coverage_state == "IN_PROGRESS"
    assert artifact.retry_count == 0
    assert summary.status == "RUNNING"
    assert summary.coverage_complete is False
    assert summary.coverage_gaps == []


def test_source_run_result_accepts_old_serialized_payload_and_new_defaults():
    old_payload = {
        "source_registry_id": "source:legacy",
        "source_name": "legacy",
        "status": "SUCCESS",
        "started_at": OBSERVED_AT.isoformat(),
        "finished_at": OBSERVED_AT.isoformat(),
        "records": [],
        "snapshots": [],
        "errors": [],
        "warnings": [],
    }

    result = SourceRunResult.model_validate(old_payload)

    assert result.status is SourceRunStatus.SUCCESS
    assert result.crawl_metrics == {}
    assert result.coverage_gaps == []
    assert "crawl_metrics" in result.model_dump()
    assert "coverage_gaps" in result.model_dump()


def test_writer_uses_root_dir_as_the_current_run_directory_and_writes_exact_raw_body(tmp_path):
    """The caller supplies a distinct root_dir for each source/run."""
    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")
    body = b"<html>exact bytes\x00\xff</html>"

    artifact = writer.write(
        request_kind="SEARCH",
        entrypoint_url=REQUESTED_URL,
        requested_url=REQUESTED_URL,
        final_url=REQUESTED_URL,
        body=body,
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        rows=[{"address": "1 Example Street"}],
        page_number=1,
        coverage_state="IN_PROGRESS",
    )

    assert Path(artifact.raw_path).read_bytes() == body
    assert artifact.sha256 == sha256(body).hexdigest()
    assert artifact.size_bytes == len(body)
    assert Path(artifact.raw_path).parent == tmp_path / "raw" / "search"
    assert (tmp_path / "raw" / "detail").is_dir()
    assert (tmp_path / "screenshots" / "entry-and-errors").is_dir()
    assert (tmp_path / "manifest.jsonl").is_file()
    assert not (tmp_path / "example" / "run-1").exists()

    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert '"request_kind":"SEARCH"' in lines[0]
    assert json.loads(lines[0])["sha256"] == artifact.sha256


def test_writer_normalizes_scrapy_bytes_headers_and_uses_html_suffix(tmp_path):
    pytest.importorskip("scrapy")
    from scrapy.http import Headers

    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")
    artifact = writer.write(
        request_kind="SEARCH",
        requested_url=REQUESTED_URL,
        body=b"<html>challenge</html>",
        status_code=403,
        headers=Headers({b"Content-Type": [b"text/html; charset=UTF-8"]}),
    )

    assert artifact.content_type == "text/html"
    assert Path(artifact.raw_path).suffix == ".html"


def test_writer_appends_a_second_compact_manifest_line_without_truncating_first(tmp_path):
    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")
    first = writer.write(
        request_kind="SEARCH",
        requested_url=REQUESTED_URL,
        body=b"first",
        status_code=200,
    )
    manifest = tmp_path / "manifest.jsonl"
    first_line = manifest.read_text(encoding="utf-8").splitlines()[0]

    second = writer.write(
        request_kind="DETAIL",
        requested_url="https://www.example.com/listings/one",
        body=b"second",
        status_code=200,
    )
    lines = manifest.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert lines[0] == first_line
    assert json.loads(lines[0])["sha256"] == first.sha256
    assert json.loads(lines[1])["sha256"] == second.sha256
    assert Path(first.raw_path) != Path(second.raw_path)
    assert "001" in Path(first.raw_path).name
    assert "002" in Path(second.raw_path).name


def test_writer_only_persists_png_when_capture_screenshot_is_enabled(tmp_path):
    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")

    without_capture = writer.write(
        request_kind="SEARCH",
        requested_url=REQUESTED_URL,
        body=b"html",
        status_code=200,
        capture_screenshot=False,
        screenshot_bytes=b"fake-png",
    )
    with_capture = writer.write(
        request_kind="DETAIL",
        requested_url="https://www.example.com/listings/one",
        body=b"detail",
        status_code=200,
        capture_screenshot=True,
        screenshot_bytes=b"fake-png",
    )

    assert without_capture.screenshot_path is None
    assert with_capture.screenshot_path is not None
    screenshot_path = Path(with_capture.screenshot_path)
    assert screenshot_path.parent == tmp_path / "screenshots" / "entry-and-errors"
    assert screenshot_path.suffix == ".png"
    assert screenshot_path.read_bytes() == b"fake-png"


def test_writer_validates_artifact_before_creating_raw_or_screenshot_files(tmp_path):
    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")

    with pytest.raises(ValidationError):
        writer.write(
            request_kind="SEARCH",
            requested_url=REQUESTED_URL,
            body=b"raw body",
            status_code=200,
            capture_screenshot=True,
            screenshot_bytes=b"fake-png",
            page_number="not-an-integer",
        )

    assert list((tmp_path / "raw" / "search").iterdir()) == []
    assert list((tmp_path / "screenshots" / "entry-and-errors").iterdir()) == []
    assert not (tmp_path / "manifest.jsonl").exists()


def test_writer_cleans_new_files_when_manifest_append_fails(tmp_path, monkeypatch):
    writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")

    def fail_append(_artifact):
        raise OSError("manifest is unavailable")

    monkeypatch.setattr(writer, "_append_manifest", fail_append)

    with pytest.raises(OSError, match="manifest is unavailable"):
        writer.write(
            request_kind="SEARCH",
            requested_url=REQUESTED_URL,
            body=b"raw body",
            status_code=200,
            capture_screenshot=True,
            screenshot_bytes=b"fake-png",
        )

    assert list((tmp_path / "raw" / "search").iterdir()) == []
    assert list((tmp_path / "screenshots" / "entry-and-errors").iterdir()) == []


def test_reinstantiated_writers_use_unique_paths_in_the_same_run_directory(tmp_path):
    first_writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")
    second_writer = ArtifactWriter(tmp_path, source_name="example", run_id="run-1")

    first = first_writer.write(
        request_kind="SEARCH",
        requested_url=REQUESTED_URL,
        body=b"same body",
        status_code=200,
        retrieved_at=OBSERVED_AT,
    )
    second = second_writer.write(
        request_kind="SEARCH",
        requested_url=REQUESTED_URL,
        body=b"same body",
        status_code=200,
        retrieved_at=OBSERVED_AT,
    )

    assert first.raw_path != second.raw_path
    assert Path(first.raw_path).read_bytes() == b"same body"
    assert Path(second.raw_path).read_bytes() == b"same body"
    assert len((tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()) == 2
