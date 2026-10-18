import base64
import json
from pathlib import Path

import pytest

from scripts.acquire_strategy_v2_photo_assets import run_photo_acquisition
from northshore_watchdog.research.strategy_v2_photo_acquisition import FetchedPhoto
from northshore_watchdog.research.strategy_v2_photo_intelligence import build_photo_manifest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_photo_asset_runner_writes_snapshot_and_explicit_visual_status(tmp_path):
    input_root = tmp_path / "input"
    input_root.joinpath("mls-r1").mkdir(parents=True)
    manifest = build_photo_manifest(
        "mls:R1",
        [{"source_url": "https://cdn.example/one.jpg", "original_order": 0}],
        retrieved_at=None,
    )
    input_root.joinpath("mls-r1", "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")),
        encoding="utf-8",
    )

    payload = run_photo_acquisition(
        manifest_root=input_root,
        output_root=tmp_path / "assets",
        report_root=tmp_path / "reports",
        output_date="2026-09-11",
        fetcher=lambda _url: FetchedPhoto(PNG_1X1, "image/png"),
    )

    assert payload["counts"]["properties_processed"] == 1
    assert payload["counts"]["downloaded_images"] == 1
    output_manifest = Path(payload["properties"][0]["output_manifest"])
    saved = json.loads(output_manifest.read_text(encoding="utf-8"))
    assert saved["acquisition_status"] == "COMPLETE"
    assert saved["visual_status"] == "UNAVAILABLE"
    assert saved["images"][0]["sha256"]
    assert Path(saved["images"][0]["local_path"]).is_file()
    assert Path(payload["artifacts"]["markdown"]).is_file()


def test_classifier_options_support_current_responses_provider(monkeypatch):
    from scripts.acquire_strategy_v2_photo_assets import _classifier_from_options

    monkeypatch.setenv("VISION_API_KEY", "test-key")
    classifier = _classifier_from_options(
        endpoint="https://example.test/v1",
        api_key_env="VISION_API_KEY",
        model="gpt-test-vision",
        timeout=12.0,
        wire_api="responses",
        max_attempts=5,
        retry_backoff_seconds=0.25,
    )

    assert classifier is not None
    assert classifier.wire_api == "responses"
    assert classifier.endpoint == "https://example.test/v1/responses"
    assert classifier.timeout == pytest.approx(12.0)
    assert classifier.max_attempts == 5
    assert classifier.retry_backoff_seconds == pytest.approx(0.25)
