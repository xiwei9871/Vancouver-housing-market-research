import base64
from hashlib import sha256

from northshore_watchdog.research.strategy_v2_photo_acquisition import (
    FetchedPhoto,
    PhotoFetchError,
    acquire_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_intelligence import build_photo_manifest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_acquisition_writes_auditable_assets_and_deduplicates_content(tmp_path):
    calls = []
    visually_same_recompressed = PNG_1X1 + b"recompressed-payload"

    def fetch(url):
        calls.append(url)
        if url.endswith("/one.jpg"):
            return FetchedPhoto(PNG_1X1, "image/jpeg")
        return FetchedPhoto(visually_same_recompressed, "image/jpeg")

    manifest = build_photo_manifest(
        "mls:R1",
        [
            {"source_url": "https://cdn.example/one.jpg", "original_order": 0},
            {"source_url": "https://cdn.example/two.jpg", "original_order": 1},
            {"source_url": "https://cdn.example/one.jpg", "original_order": 2},
        ],
        retrieved_at=None,
    )

    enriched, summary = acquire_photo_manifest(
        manifest,
        tmp_path / "mls-r1",
        fetcher=fetch,
        retrieved_at="2026-09-11T00:00:00Z",
    )

    assert calls == [
        "https://cdn.example/one.jpg",
        "https://cdn.example/two.jpg",
    ]
    assert summary["downloaded_count"] == 2
    assert summary["duplicate_url_count"] == 1
    assert summary["duplicate_perceptual_count"] == 1
    assert summary["unique_content_count"] == 1

    first, second, third = enriched.images
    assert first.sha256 == sha256(PNG_1X1).hexdigest()
    assert first.hash_basis == "CONTENT"
    assert first.width == 1
    assert first.height == 1
    assert first.perceptual_hash
    assert first.local_path
    assert first.source_status == "DOWNLOADED"
    assert second.is_duplicate is True
    assert second.source_status == "DUPLICATE_PERCEPTUAL"
    assert second.local_path == first.local_path
    assert third.is_duplicate is True
    assert third.source_status == "DUPLICATE_URL"
    assert third.local_path == first.local_path
    assert enriched.acquisition_status == "COMPLETE"
    assert enriched.acquisition_summary["failed_count"] == 0


def test_acquisition_preserves_failures_and_continues(tmp_path):
    def fetch(url):
        if url.endswith("/forbidden.jpg"):
            raise PhotoFetchError("HTTP_403", status_code=403)
        return FetchedPhoto(PNG_1X1, "image/jpeg")

    manifest = build_photo_manifest(
        "mls:R2",
        [
            {"source_url": "https://cdn.example/forbidden.jpg", "original_order": 0},
            {"source_url": "https://cdn.example/ok.jpg", "original_order": 1},
        ],
        retrieved_at=None,
    )

    enriched, summary = acquire_photo_manifest(
        manifest,
        tmp_path / "mls-r2",
        fetcher=fetch,
    )

    assert summary["failed_count"] == 1
    assert summary["downloaded_count"] == 1
    assert enriched.images[0].source_status == "HTTP_403"
    assert enriched.images[1].source_status == "DOWNLOADED"
    assert enriched.source_status == "PHOTO_SOURCE_PARTIAL"
