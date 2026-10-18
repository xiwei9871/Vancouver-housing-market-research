from datetime import datetime, timezone
from hashlib import sha256

from northshore_watchdog.research.strategy_v2_photo_intelligence import (
    PhotoManifest,
    PhotoObservation,
    build_photo_manifest,
    extract_photo_records_from_html,
    summarize_photo_coverage,
)


def test_manifest_preserves_order_and_required_audit_fields():
    manifest = build_photo_manifest(
        property_key="mls:R1",
        photos=[
            {
                "source_url": "https://img.example/front.jpg",
                "original_order": 0,
                "content_labels": ["EXTERIOR_FRONT"],
            },
            {
                "source_url": "https://img.example/front.jpg",
                "original_order": 1,
                "content_labels": ["EXTERIOR_FRONT"],
            },
        ],
        retrieved_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )

    assert isinstance(manifest, PhotoManifest)
    assert isinstance(manifest.images[0], PhotoObservation)
    assert manifest.property_key == "mls:R1"
    assert manifest.images[0].original_order == 0
    assert manifest.images[0].sha256 is None
    assert manifest.images[0].hash_basis == "NONE"
    assert manifest.images[0].source_status == "AVAILABLE"
    assert manifest.images[1].is_duplicate is True
    assert manifest.duplicate_count == 1


def test_manifest_uses_content_sha256_when_bytes_are_available():
    content = b"image fixture"
    manifest = build_photo_manifest(
        property_key="mls:R1",
        photos=[
            {
                "source_url": "https://img.example/front.jpg",
                "original_order": 0,
                "content": content,
            }
        ],
        retrieved_at=None,
    )

    assert manifest.images[0].sha256 == sha256(content).hexdigest()
    assert manifest.images[0].hash_basis == "CONTENT"


def test_missing_photo_source_is_unknown_not_error():
    manifest = build_photo_manifest(property_key="mls:R2", photos=[], retrieved_at=None)

    assert manifest.source_status == "PHOTO_SOURCE_UNAVAILABLE"
    assert manifest.images == []
    assert manifest.duplicate_count == 0


def test_content_labels_drive_space_coverage_not_raw_photo_count():
    manifest = build_photo_manifest(
        property_key="mls:R3",
        photos=[
            {
                "source_url": f"https://img.example/living-{i}.jpg",
                "original_order": i,
                "content_labels": ["LIVING"],
            }
            for i in range(12)
        ]
        + [
            {
                "source_url": "https://img.example/kitchen.jpg",
                "original_order": 12,
                "content_labels": ["KITCHEN"],
            }
        ],
        retrieved_at=None,
    )

    summary = summarize_photo_coverage(manifest)

    assert summary["photo_count"] == 13
    assert summary["classified_photo_count"] == 13
    assert summary["covered_space_groups"] == ["living"]
    assert summary["photo_evidence_coverage"] < 50
    assert summary["photo_confidence"] == "LOW"
    assert set(summary["uncovered_space_groups"]) == {"exterior", "basement", "site"}


def test_duplicate_photos_do_not_inflate_coverage():
    manifest = build_photo_manifest(
        property_key="mls:R3",
        photos=[
            {
                "source_url": "https://img.example/front-a.jpg",
                "original_order": 0,
                "content": b"same image",
                "content_labels": ["EXTERIOR_FRONT"],
            },
            {
                "source_url": "https://img.example/front-b.jpg",
                "original_order": 1,
                "content": b"same image",
                "content_labels": ["EXTERIOR_FRONT"],
            },
        ],
        retrieved_at=None,
    )

    summary = summarize_photo_coverage(manifest)

    assert manifest.images[1].is_duplicate is True
    assert summary["photo_count"] == 2
    assert summary["unique_photo_count"] == 1
    assert summary["covered_space_groups"] == ["exterior"]


def test_unconfigured_visual_adapter_does_not_guess_labels():
    manifest = build_photo_manifest(
        property_key="mls:R4",
        photos=[
            {
                "source_url": "https://img.example/one.jpg",
                "original_order": 0,
            }
        ],
        retrieved_at=None,
    )

    assert manifest.images[0].content_labels == []
    assert manifest.images[0].source_status == "AVAILABLE"


def test_html_parser_extracts_gallery_urls_in_photo_id_order_without_hashing_urls():
    html = """
    <img id="imageGallerySidebarPhoto2" src="https://cdn.example/two.jpg">
    <img id="imageGallerySidebarPhoto1" src="https://cdn.example/one.jpg">
    <img id="imageGallerySidebarPhoto3" data-src="https://cdn.example/three.jpg">
    <img id="not-a-gallery-photo" src="https://cdn.example/ignore.jpg">
    """

    records = extract_photo_records_from_html(html, property_key="mls:R6")

    assert [record["source_url"] for record in records] == [
        "https://cdn.example/one.jpg",
        "https://cdn.example/two.jpg",
        "https://cdn.example/three.jpg",
    ]
    assert [record["original_order"] for record in records] == [0, 1, 2]
    assert all(record["property_key"] == "mls:R6" for record in records)
    manifest = build_photo_manifest("mls:R6", records, retrieved_at=None)
    assert all(image.sha256 is None for image in manifest.images)


def test_coverage_without_a_photo_source_remains_unknown():
    manifest = build_photo_manifest(property_key="mls:R5", photos=[], retrieved_at=None)

    summary = summarize_photo_coverage(manifest)

    assert summary["photo_evidence_coverage"] is None
    assert summary["photo_confidence"] == "UNKNOWN"
    assert summary["classified_photo_count"] == 0
