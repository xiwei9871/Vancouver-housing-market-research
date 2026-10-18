from __future__ import annotations

from datetime import datetime, timezone
import json

from northshore_watchdog.research.crawl_source import CrawlManifestSource
from northshore_watchdog.research.acquisition import acquire_from_sources
from northshore_watchdog.research.models import SourceType
from northshore_watchdog.research.sources import SourceRunStatus


OBSERVED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
ENTRYPOINT = "https://www.zolo.ca/vancouver-real-estate/houses"


def _artifact(*, request_kind: str, requested_url: str, retrieved_at: str, rows: list[dict], coverage_state: str) -> dict:
    return {
        "run_id": "run-1",
        "source_name": "zolo-full-market",
        "source_registry_id": "source:zolo-full-market",
        "entrypoint_url": ENTRYPOINT,
        "request_kind": request_kind,
        "requested_url": requested_url,
        "final_url": requested_url,
        "status_code": 200,
        "content_type": "text/html",
        "raw_path": f"/tmp/{request_kind.lower()}.html",
        "screenshot_path": f"/tmp/{request_kind.lower()}.png",
        "sha256": "a" * 64 if request_kind == "SEARCH" else "b" * 64,
        "size_bytes": 6,
        "observed_at": retrieved_at,
        "retrieved_at": retrieved_at,
        "rows": rows,
        "coverage_state": coverage_state,
        "card_count": len(rows) if request_kind == "SEARCH" else 0,
        "extracted_row_count": len(rows),
    }


def _source(manifest_path):
    return CrawlManifestSource(
        manifest_path=manifest_path,
        name="zolo-full-market",
        source_type=SourceType.AGGREGATOR,
        source_registry_id="source:zolo-full-market",
        source_url=ENTRYPOINT,
        config={
            "source_role": "ZOLO_PUBLIC",
            "expected_entrypoints": [ENTRYPOINT],
        },
    )


def test_manifest_source_bridges_search_and_detail_rows_snapshots_and_metrics(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                _artifact(
                    request_kind="SEARCH",
                    requested_url=ENTRYPOINT,
                    retrieved_at="2026-09-02T00:00:00Z",
                    rows=[
                        {
                            "mls_number": "R4000001",
                            "address": "401 Test Street",
                            "municipality": "Vancouver",
                            "asking": 1425000,
                        }
                    ],
                    coverage_state="PARTIAL_CAP_REACHED",
                ),
                _artifact(
                    request_kind="DETAIL",
                    requested_url="https://www.zolo.ca/401-test-street",
                    retrieved_at="2026-09-02T00:00:01Z",
                    rows=[
                        {
                            "mls_number": "R4000001",
                            "address": "401 Test Street",
                            "municipality": "Vancouver",
                            "bedrooms": 4,
                            "bathrooms": 3,
                            "lot_area_sqft": 4026,
                        }
                    ],
                    coverage_state="IN_PROGRESS",
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = _source(manifest).collect(OBSERVED_AT)

    assert result.status is SourceRunStatus.PARTIAL
    assert result.coverage_complete is False
    assert len(result.records) == 2
    assert result.records[0]["mls_number"] == "R4000001"
    assert result.records[1]["bedrooms"] == 4
    assert result.records[1]["lot_area_sqft"] == 4026
    assert [snapshot.request_kind for snapshot in result.snapshots] == ["SEARCH", "DETAIL"]
    assert result.snapshots[0].raw_path == "/tmp/search.html"
    assert result.snapshots[0].screenshot_path == "/tmp/search.png"
    assert result.snapshots[0].sha256 == "a" * 64
    assert result.snapshots[1].final_url == "https://www.zolo.ca/401-test-street"
    assert result.snapshots[1].observed_at == OBSERVED_AT
    assert result.snapshots[1].retrieved_at == datetime(2026, 9, 2, 0, 0, 1, tzinfo=timezone.utc)
    assert result.crawl_metrics["entrypoints"] == 1
    assert result.crawl_metrics["pages_fetched"] == 1
    assert result.crawl_metrics["details_fetched"] == 1
    assert result.crawl_metrics["coverage_complete"] is False
    assert "PARTIAL_CAP_REACHED" in result.coverage_gaps


def test_manifest_source_keeps_bad_lines_as_errors_without_discarding_good_rows(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    good = _artifact(
        request_kind="SEARCH",
        requested_url=ENTRYPOINT,
        retrieved_at="2026-09-02T00:00:00Z",
        rows=[
            {
                "mls_number": "R4000002",
                "address": "402 Test Street",
                "municipality": "Vancouver",
            }
        ],
        coverage_state="COVERAGE_COMPLETE",
    )
    manifest.write_text(json.dumps(good) + "\nnot-json\n", encoding="utf-8")

    result = _source(manifest).collect(OBSERVED_AT)

    assert len(result.records) == 1
    assert result.status is SourceRunStatus.PARTIAL
    assert result.errors
    assert "line 2" in result.errors[0]


def test_acquisition_preserves_manifest_coverage_from_metrics(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    good = _artifact(
        request_kind="SEARCH",
        requested_url=ENTRYPOINT,
        retrieved_at="2026-09-02T00:00:00Z",
        rows=[{"mls_number": "R4000003", "address": "403 Test Street", "municipality": "Vancouver"}],
        coverage_state="NATURAL_COMPLETE",
    )
    manifest.write_text(json.dumps(good) + "\n", encoding="utf-8")

    result = acquire_from_sources([_source(manifest)], observed_at=OBSERVED_AT)

    assert result.source_runs[0].status is SourceRunStatus.SUCCESS
    assert result.source_runs[0].coverage_complete is True
