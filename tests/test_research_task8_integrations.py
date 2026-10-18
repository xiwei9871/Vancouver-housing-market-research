from __future__ import annotations

from datetime import datetime, timezone
import json

from northshore_watchdog.research.acquisition import acquire_from_sources
from northshore_watchdog.research.crawl_source import CrawlManifestSource
from northshore_watchdog.research.crawling.models import CrawlArtifact
from northshore_watchdog.research.crawling.policies import summarize_artifacts
from northshore_watchdog.research.crawling.spider import FullMarketSpider
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.sources import SourceRunResult, SourceRunStatus
from northshore_watchdog.research.storage import ResearchStorage


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
ENTRYPOINT = "https://www.realtor.ca/real-estate/vancouver"


def _artifact(*, request_kind: str = "SEARCH", sha256: str = "a" * 64, rows=None, coverage_state="COMPLETE") -> CrawlArtifact:
    return CrawlArtifact(
        run_id="task8-run",
        source_name="mls-browser",
        source_registry_id="source:mls-browser",
        entrypoint_url=ENTRYPOINT,
        request_kind=request_kind,
        requested_url=ENTRYPOINT if request_kind == "SEARCH" else "https://www.realtor.ca/detail/1",
        final_url=ENTRYPOINT,
        status_code=200,
        content_type="text/html",
        raw_path=f"/tmp/{request_kind.lower()}.html",
        sha256=sha256,
        size_bytes=10,
        retrieved_at=OBSERVED_AT,
        rows=rows or [],
        card_count=len(rows or []) if request_kind == "SEARCH" else 0,
        extracted_row_count=len(rows or []),
        coverage_state=coverage_state,
    )


def _manifest_source(path):
    return CrawlManifestSource(
        manifest_path=path,
        name="mls-browser",
        source_type=SourceType.MLS,
        source_registry_id="source:mls-browser",
        source_url=ENTRYPOINT,
        config={"expected_entrypoints": [ENTRYPOINT]},
    )


def test_summarize_artifacts_marks_same_hash_duplicate_and_incomplete():
    metrics = summarize_artifacts(
        [
            _artifact(sha256="b" * 64),
            _artifact(sha256="b" * 64),
        ],
        expected_entrypoints=[ENTRYPOINT],
    )

    assert metrics["duplicate_pages"] == 1
    assert metrics["coverage_complete"] is False


def test_crawl_manifest_search_and_detail_sparse_rows_merge_to_one_record(tmp_path):
    search_row = {
        "mls_number": "R8000001",
        "address": "100 Main Street",
        "municipality": "Vancouver",
        "asking": 1_450_000,
    }
    detail_row = {
        "mls_number": "R8000001",
        "address": "100 Main Street",
        "municipality": "Vancouver",
        "bedrooms": 4,
        "bathrooms": 3,
        "finished_sqft": 1_850,
        "lot_area_sqft": 4_026,
    }
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(item.model_dump(mode="json"))
            for item in (
                _artifact(rows=[search_row]),
                _artifact(request_kind="DETAIL", sha256="c" * 64, rows=[detail_row]),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = acquire_from_sources([_manifest_source(manifest)], observed_at=OBSERVED_AT)

    assert len(result.records) == 1
    record = result.records[0]
    assert record.mls_number == "R8000001"
    assert record.asking == 1_450_000
    assert record.bedrooms == 4
    assert record.bathrooms == 3
    assert record.finished_sqft == 1_850
    assert record.lot_area_sqft == 4_026


def test_same_mls_different_addresses_are_retained_as_conflict(tmp_path):
    rows = [
        {
            "mls_number": "R8000002",
            "address": "101 Main Street",
            "municipality": "Vancouver",
            "asking": 1_400_000,
        },
        {
            "mls_number": "R8000002",
            "address": "202 Oak Street",
            "municipality": "Vancouver",
            "asking": 1_425_000,
        },
    ]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(_artifact(rows=rows).model_dump(mode="json")) + "\n",
        encoding="utf-8",
    )

    result = acquire_from_sources([_manifest_source(manifest)], observed_at=OBSERVED_AT)

    assert len(result.records) == 2
    assert {record.address for record in result.records} == {"101 Main Street", "202 Oak Street"}
    assert any(item.conflict_type == "MLS_IDENTITY_COLLISION" for item in result.conflicts)
    assert all(record.data_conflict is True for record in result.records)


def test_storage_round_trip_preserves_crawl_metrics_and_coverage_gaps(tmp_path):
    storage = ResearchStorage(tmp_path / "research.sqlite")
    source_run = SourceRunResult(
        source_registry_id="source:mls-browser",
        source_name="mls-browser",
        status=SourceRunStatus.PARTIAL,
        started_at=OBSERVED_AT,
        finished_at=OBSERVED_AT,
        crawl_metrics={"pages_fetched": 3, "duplicate_pages": 1},
        coverage_gaps=["DETAILS_SKIPPED"],
    )

    storage.save_source_run(source_run)
    restored = storage.list_source_runs("source:mls-browser")
    storage.close()

    assert len(restored) == 1
    assert restored[0].crawl_metrics == {"pages_fetched": 3, "duplicate_pages": 1}
    assert restored[0].coverage_gaps == ["DETAILS_SKIPPED"]


def test_spider_state_snapshot_restore_preserves_page_and_detail_keys():
    spider = FullMarketSpider.__new__(FullMarketSpider)
    spider.state = {
        "seen_page_urls": [ENTRYPOINT],
        "page_fingerprints": ["d" * 64],
        "detail_request_keys": ["mls:R8000001"],
        "detail_urls": ["https://www.realtor.ca/detail/1"],
    }

    snapshot = spider.state_snapshot()
    spider.state = {}
    spider.restore_state(snapshot)

    assert spider.state["seen_page_urls"] == [ENTRYPOINT]
    assert spider.state["page_fingerprints"] == ["d" * 64]
    assert spider.state["detail_request_keys"] == ["mls:R8000001"]
    assert spider.state["detail_urls"] == ["https://www.realtor.ca/detail/1"]
