import base64
import json
from pathlib import Path

from scripts.run_strategy_v2_routed_visual_analysis import run_routed_visual_analysis
from northshore_watchdog.research.strategy_v2_photo_intelligence import build_photo_manifest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeCombinedClassifier:
    name = "fake-combined"
    version = "test-1"

    def __init__(self):
        self.calls = []

    def classify_image(self, **kwargs):
        self.calls.append(kwargs["original_order"])
        return {
            "labels": ["LIVING"],
            "visible_defects": [],
            "maintenance_signals": ["clean"],
            "cosmetic_signals": [],
            "confidence": "MEDIUM",
            "evidence": ["test evidence"],
        }


def _manifest(path: Path, property_key: str, count: int):
    perceptual_hashes = [
        "0000000000000000",
        "ffffffffffffffff",
        "00000000ffffffff",
        "ffffffff00000000",
        "f0f0f0f0f0f0f0f0",
    ]
    images = []
    for index in range(count):
        image_path = path / f"{property_key.replace(':', '-')}-{index}.png"
        image_path.write_bytes(PNG_1X1 + bytes([index]))
        images.append(
            {
                "source_url": f"https://example.test/{property_key}/{index}.png",
                "original_order": index,
                "local_path": str(image_path),
                "source_status": "LOCAL_CACHE",
                "perceptual_hash": perceptual_hashes[index],
            }
        )
    return build_photo_manifest(property_key, images, retrieved_at=None)


def test_routed_visual_runner_processes_only_selected_image_budgets(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    full_key = "mls:R-FULL"
    representative_key = "mls:R-REP"
    full_manifest = _manifest(source_root, full_key, 2)
    representative_manifest = _manifest(source_root, representative_key, 5)
    manifest_paths = {}
    for key, manifest in ((full_key, full_manifest), (representative_key, representative_manifest)):
        path = tmp_path / f"{key.replace(':', '-')}.json"
        path.write_text(json.dumps(manifest.model_dump(mode="json")), encoding="utf-8")
        manifest_paths[key] = path

    routing_path = tmp_path / "routing.json"
    routing_path.write_text(
        json.dumps(
            {
                "properties": [
                    {
                        "property_key": full_key,
                        "address": "1 Full Street",
                        "mls": "R-FULL",
                        "analysis_route": "FULL_VISION",
                        "selected_for_visual": True,
                        "image_count": 2,
                        "photo_manifest_path": str(manifest_paths[full_key]),
                    },
                    {
                        "property_key": representative_key,
                        "address": "2 Representative Street",
                        "mls": "R-REP",
                        "analysis_route": "REPRESENTATIVE_VISION",
                        "selected_for_visual": True,
                        "image_count": 3,
                        "photo_manifest_path": str(manifest_paths[representative_key]),
                    },
                    {
                        "property_key": "mls:R-NOT-SELECTED",
                        "address": "3 Structured Street",
                        "analysis_route": "STRUCTURED_ONLY",
                        "selected_for_visual": False,
                        "image_count": 0,
                        "photo_manifest_path": str(manifest_paths[full_key]),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    classifier = FakeCombinedClassifier()

    result = run_routed_visual_analysis(
        routing_path=routing_path,
        output_root=tmp_path / "output",
        output_date="2026-09-12",
        classifier=classifier,
    )

    assert result["selected_property_count"] == 2
    assert result["image_count"] == 5, json.dumps(result, indent=2)
    assert result["classification_request_count"] == 5
    assert result["http_request_attempts"] is None
    assert len(classifier.calls) == 5
    assert sorted(classifier.calls) == [0, 0, 1, 1, 2]
    assert Path(result["artifacts"]["json"]).is_file()
    assert Path(result["artifacts"]["report"]).is_file()

    saved = json.loads(Path(result["artifacts"]["json"]).read_text(encoding="utf-8"))
    rows = {row["property_key"]: row for row in saved["properties"]}
    assert rows[full_key]["classified_count"] == 2
    assert rows[representative_key]["classified_count"] == 3
    assert "mls:R-NOT-SELECTED" not in rows


def test_routed_visual_runner_requires_a_classifier(tmp_path):
    routing_path = tmp_path / "routing.json"
    routing_path.write_text(json.dumps({"properties": []}), encoding="utf-8")

    try:
        run_routed_visual_analysis(
            routing_path=routing_path,
            output_root=tmp_path / "output",
            output_date="2026-09-12",
            classifier=None,
        )
    except ValueError as exc:
        assert "classifier is required" in str(exc)
    else:
        raise AssertionError("expected a missing-classifier error")
