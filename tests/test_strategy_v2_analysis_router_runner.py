import csv
import json
from hashlib import sha256
from pathlib import Path

from scripts.run_strategy_v2_analysis_router import run_analysis_router


ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = ROOT / "data/research/normalized/strategy-v2-online-screening-2026-09-11.csv"
LISTING_INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
PHOTO_ROOT = ROOT / "data/research/raw/photo-assets"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_runner_writes_one_auditable_route_per_property_without_visual_api(tmp_path):
    source_digest = _digest(INPUT_CSV)

    result = run_analysis_router(
        input_csv=INPUT_CSV,
        listing_input=LISTING_INPUT,
        photo_root=PHOTO_ROOT,
        output_root=tmp_path,
        output_date="2026-09-12",
    )

    assert result["property_count"] == 166
    assert result["visual_api_calls"] == 0
    assert 25 <= result["shortlist"]["count"] <= 35
    assert result["counts"]["analysis_routes"]
    assert sum(result["counts"]["analysis_routes"].values()) == 166
    assert sum(result["counts"]["visual_eligibility"].values()) == 166
    assert result["counts"]["estimated_http_requests"] == result["counts"]["visual_image_count"]
    assert result["counts"]["estimated_retry_budget"] == 2 * result["counts"]["visual_image_count"]
    assert result["counts"]["estimated_max_http_attempts"] == 3 * result["counts"]["visual_image_count"]

    json_path = Path(result["artifacts"]["json"])
    csv_path = Path(result["artifacts"]["csv"])
    report_path = Path(result["artifacts"]["report"])
    assert json_path.is_file()
    assert csv_path.is_file()
    assert report_path.is_file()

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(saved["properties"]) == 166
    assert all(item["property_key"] for item in saved["properties"])
    assert all(item["analysis_route"] in {
        "FULL_VISION",
        "REPRESENTATIVE_VISION",
        "STRUCTURED_ONLY",
        "SPECIAL_REVIEW",
    } for item in saved["properties"])
    assert all(
        item["visual_budget_images"] > 0
        for item in saved["properties"]
        if item["selected_for_visual"]
    )
    assert all(
        item["image_count"] == item["visual_budget_images"]
        and item["estimated_http_requests"] == item["image_count"]
        and item["estimated_retry_budget"] == 2 * item["image_count"]
        for item in saved["properties"]
    )
    assert all(
        item["unique_photo_count"] is not None
        and item["unique_photo_count"] >= item["visual_budget_images"]
        for item in saved["properties"]
        if item["selected_for_visual"]
    )
    assert all(
        item["visual_budget_images"] == 0
        for item in saved["properties"]
        if not item["selected_for_visual"]
    )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 166

    assert _digest(INPUT_CSV) == source_digest
    report = report_path.read_text(encoding="utf-8")
    assert "STRUCTURED_ONLY means no visual call" in report
    assert "Estimated HTTP requests (one request per image)" in report
    assert "No visual API was invoked" in report


def test_runner_routes_all_short_photo_properties_without_visual_budget(tmp_path):
    result = run_analysis_router(
        input_csv=INPUT_CSV,
        listing_input=LISTING_INPUT,
        photo_root=PHOTO_ROOT,
        output_root=tmp_path,
        output_date="2026-09-12",
    )
    short = [
        item
        for item in result["properties"]
        if item["photo_count"] is not None and item["photo_count"] < 3
    ]

    assert len(short) == 18
    assert all(not item["selected_for_visual"] for item in short)
    assert all(item["visual_budget_images"] == 0 for item in short)
    assert all("INSUFFICIENT_PHOTOS" in item["flags"] for item in short)
