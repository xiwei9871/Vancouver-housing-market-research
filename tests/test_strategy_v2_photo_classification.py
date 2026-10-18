import base64
import json
from urllib.error import HTTPError

import northshore_watchdog.research.strategy_v2_photo_classification as classification_module
from northshore_watchdog.research.strategy_v2_photo_classification import (
    JsonHttpVisionClassifier,
    VisualClassificationError,
    classify_photo_manifest,
)
from northshore_watchdog.research.strategy_v2_photo_intelligence import build_photo_manifest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeVisionClassifier:
    name = "fake-vision"
    version = "test-1"

    def __init__(self):
        self.calls = []

    def classify(self, *, phase, image_path, property_key, original_order, space_labels=()):
        self.calls.append((phase, original_order, tuple(space_labels)))
        if phase == "space":
            return {
                "labels": ["LIVING", "KITCHEN"],
                "confidence": "HIGH",
                "evidence": ["open-plan living area and kitchen visible"],
            }
        return {
            "visible_defects": ["WATER_STAINING"],
            "maintenance_signals": [],
            "cosmetic_signals": ["DATED_KITCHEN"],
            "confidence": "MEDIUM",
            "evidence": ["staining visible near ceiling"],
        }


class CombinedFakeVisionClassifier:
    name = "combined-fake-vision"
    version = "test-1"

    def __init__(self):
        self.calls = []

    def classify_image(self, *, image_path, property_key, original_order, space_labels=()):
        self.calls.append((original_order, tuple(space_labels)))
        return {
            "labels": ["LIVING", "KITCHEN"],
            "visible_defects": ["WATER_STAINING"],
            "maintenance_signals": [],
            "cosmetic_signals": ["DATED_KITCHEN"],
            "confidence": "MEDIUM",
            "evidence": ["open-plan living area and kitchen; staining near ceiling"],
        }


def test_visual_pipeline_classifies_space_before_condition(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    manifest = build_photo_manifest(
        "mls:R3",
        [
            {
                "source_url": "https://cdn.example/photo.jpg",
                "original_order": 0,
                "local_path": str(image_path),
                "source_status": "LOCAL_CACHE",
            }
        ],
        retrieved_at=None,
    )
    classifier = FakeVisionClassifier()

    enriched, summary = classify_photo_manifest(manifest, classifier=classifier)

    assert [call[0] for call in classifier.calls] == ["space", "condition"]
    assert classifier.calls[1][2] == ("LIVING", "KITCHEN")
    image = enriched.images[0]
    assert image.content_labels == ["LIVING", "KITCHEN"]
    assert image.visible_defects == ["WATER_STAINING"]
    assert image.cosmetic_signals == ["DATED_KITCHEN"]
    assert image.visual_status == "CLASSIFIED"
    assert summary["classified_count"] == 1
    assert summary["classification_mode"] == "LEGACY_TWO_PHASE"
    assert summary["classification_request_count"] == 2
    assert enriched.visual_status == "CLASSIFIED"


def test_visual_pipeline_uses_one_combined_request_per_image(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    manifest = build_photo_manifest(
        "mls:R3-COMBINED",
        [
            {
                "source_url": "https://cdn.example/photo.jpg",
                "original_order": 0,
                "local_path": str(image_path),
                "source_status": "LOCAL_CACHE",
            }
        ],
        retrieved_at=None,
    )
    classifier = CombinedFakeVisionClassifier()

    enriched, summary = classify_photo_manifest(manifest, classifier=classifier)

    assert classifier.calls == [(0, ())]
    image = enriched.images[0]
    assert image.content_labels == ["LIVING", "KITCHEN"]
    assert image.visible_defects == ["WATER_STAINING"]
    assert image.cosmetic_signals == ["DATED_KITCHEN"]
    assert summary["classification_mode"] == "ONE_REQUEST_PER_IMAGE"
    assert summary["classification_request_count"] == 1
    assert summary["classified_count"] == 1


def test_missing_classifier_is_explicit_and_does_not_guess(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    manifest = build_photo_manifest(
        "mls:R4",
        [
            {
                "source_url": "https://cdn.example/photo.jpg",
                "original_order": 0,
                "local_path": str(image_path),
                "source_status": "LOCAL_CACHE",
            }
        ],
        retrieved_at=None,
    )

    enriched, summary = classify_photo_manifest(manifest, classifier=None)

    assert summary["status"] == "UNAVAILABLE"
    assert enriched.visual_status == "UNAVAILABLE"
    assert enriched.images[0].content_labels == []
    assert enriched.images[0].visible_defects == []


def test_json_http_classifier_supports_responses_payload_and_nested_output(tmp_path, monkeypatch):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"labels":["BEDROOM"],"visible_defects":[],"maintenance_signals":["clean"],"cosmetic_signals":[],"confidence":0.99,"evidence":["bed visible"]}',
                                }
                            ],
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(classification_module, "urlopen", fake_urlopen)
    classifier = JsonHttpVisionClassifier(
        endpoint="https://example.test/v1",
        api_key="test-key",
        model="gpt-test-vision",
        wire_api="responses",
    )

    result = classifier.classify_image(
        image_path=str(image_path),
        property_key="mls:R5",
        original_order=0,
    )

    assert captured["timeout"] == 60.0
    assert captured["url"] == "https://example.test/v1/responses"
    assert captured["payload"]["model"] == "gpt-test-vision"
    assert "input" in captured["payload"]
    assert "messages" not in captured["payload"]
    content = captured["payload"]["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert result["labels"] == ["BEDROOM"]
    assert result["confidence"] == 0.99
    assert result["evidence"] == ["bed visible"]
    assert result["maintenance_signals"] == ["clean"]


def test_json_http_classifier_legacy_phase_wrapper_remains_compatible(tmp_path, monkeypatch):
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(PNG_1X1)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "output_text": '{"labels":["LIVING"],"visible_defects":["WATER_STAINING"]}'
                }
            ).encode("utf-8")

    monkeypatch.setattr(classification_module, "urlopen", lambda request, timeout: FakeResponse())
    classifier = JsonHttpVisionClassifier(
        endpoint="https://example.test/v1",
        api_key="test-key",
        model="gpt-test-vision",
        wire_api="responses",
    )

    result = classifier.classify(
        phase="space",
        image_path=str(image_path),
        property_key="mls:R5-COMPAT",
        original_order=0,
    )

    assert result["labels"] == ["LIVING"]
    assert "visible_defects" not in result


def test_json_http_classifier_retries_transient_rate_limit(tmp_path, monkeypatch):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    attempts = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"labels":["LIVING"],"visible_defects":[],"maintenance_signals":[],"cosmetic_signals":[],"confidence":"MEDIUM","evidence":[]}'}],
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            raise HTTPError(request.full_url, 429, "rate limited", {}, None)
        return FakeResponse()

    monkeypatch.setattr(classification_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(classification_module, "sleep", lambda _seconds: None)
    classifier = JsonHttpVisionClassifier(
        endpoint="https://example.test/v1",
        api_key="test-key",
        model="gpt-test-vision",
        wire_api="responses",
        max_attempts=3,
        retry_backoff_seconds=0.01,
    )

    result = classifier.classify(
        phase="space",
        image_path=str(image_path),
        property_key="mls:R6",
        original_order=0,
    )

    assert len(attempts) == 3
    assert result["labels"] == ["LIVING"]
    assert classifier.http_request_attempts == 3
    assert classifier.retry_attempts == 2


def test_visual_failure_keeps_safe_error_reason_in_audit_evidence(tmp_path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(PNG_1X1)
    manifest = build_photo_manifest(
        "mls:R7",
        [
            {
                "source_url": "https://cdn.example/photo.jpg",
                "original_order": 0,
                "local_path": str(image_path),
                "source_status": "LOCAL_CACHE",
            }
        ],
        retrieved_at=None,
    )

    class FailingClassifier:
        name = "failing-vision"
        version = "test-1"

        def classify(self, **_kwargs):
            raise VisualClassificationError("vision_request_failed:http_429")

    enriched, summary = classify_photo_manifest(manifest, classifier=FailingClassifier())

    assert summary["failed_count"] == 1
    assert enriched.images[0].visual_evidence == [
        "classification_failed:VisualClassificationError:vision_request_failed:http_429"
    ]
