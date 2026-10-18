import json
from pathlib import Path

from scripts.run_strategy_v2_phase_2a import run_phase_2a


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
SHADOW = ROOT / "data/research/normalized/strategy-v2-shadow-2026-09-09.json"


def _mapping_keys(value):
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_mapping_keys(child))
        return keys
    if isinstance(value, list):
        keys = set()
        for child in value:
            keys.update(_mapping_keys(child))
        return keys
    return set()


def test_phase_2a_runner_writes_all_rows_without_ranking(tmp_path):
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-10",
    )

    assert result["property_count"] == 166
    assert result["ranking_status"] == "NOT_RUN"
    assert result["shortlist"]["status"] in {
        "GENERATED",
        "GENERATED_WITH_PARETO_OVERRIDES",
    }
    assert Path(result["artifacts"]["json"]).is_file()
    assert Path(result["artifacts"]["csv"]).is_file()
    assert Path(result["artifacts"]["history"]).is_file()
    assert Path(result["artifacts"]["online_shortlist"]).is_file()
    assert Path(result["artifacts"]["audit_report"]).is_file()
    assert Path(result["artifacts"]["acceptance_report"]).is_file()
    assert Path(result["artifacts"]["premium_lot_comparison"]).is_file()
    acceptance = Path(result["artifacts"]["acceptance_report"]).read_text(encoding="utf-8")
    assert "precomputed local manifests" in acceptance
    assert len(result["artifacts"]["photo_manifests"]) == 166
    assert all(Path(path).is_file() for path in result["artifacts"]["photo_manifests"])

    saved = json.loads(Path(result["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert len(saved["properties"]) == 166
    assert all(item["ranking_status"] == "NOT_RUN" for item in saved["properties"])
    assert all("photo_assessment" in item for item in saved["properties"])
    forbidden = {
        "rank",
        "current_use_score",
        "dual_score",
        "redevelopment_score",
        "ralv",
        "RALV",
    }
    assert not forbidden.intersection(_mapping_keys(saved))


def test_phase_2a_can_write_manifests_to_a_separate_snapshot(tmp_path):
    manifest_root = tmp_path / "custom-manifests"
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-10",
        manifest_output_root=manifest_root,
    )

    assert result["property_count"] == 166
    manifest_paths = [Path(path) for path in result["artifacts"]["photo_manifests"]]
    assert all(manifest_root in path.parents for path in manifest_paths)
    assert len(list(manifest_root.glob("*/manifest.json"))) == 166


def test_phase_2a_emits_same_cohort_comparison_for_3218_and_3910(tmp_path):
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-10",
    )

    saved = json.loads(Path(result["artifacts"]["json"]).read_text(encoding="utf-8"))
    rows = {item["address"]: item for item in saved["properties"]}
    w6 = rows["3218 W 6TH AVENUE"]
    w10 = rows["3910 W 10TH AVENUE"]

    assert "PREMIUM_LOT" in w6["cohorts"]
    assert "PREMIUM_LOT" in w10["cohorts"]
    assert w10["normalized_metrics"]["normalized_lot_basis_per_sqft"] < w6["normalized_metrics"]["normalized_lot_basis_per_sqft"]
    assert w10["cohort_comparison"]["overall_read"] == "3910_W10_STRONGER_PACKAGE"

    report = Path(result["artifacts"]["premium_lot_comparison"]).read_text(encoding="utf-8")
    assert "3218 W 6TH AVENUE" in report
    assert "3910 W 10TH AVENUE" in report
    assert "3910 W10" in report


def test_phase_2a_runner_replays_local_html_and_keeps_path_safe(tmp_path):
    result = run_phase_2a(
        input_path=INPUT,
        shadow_path=SHADOW,
        output_root=tmp_path,
        output_date="2026-09-10",
        photo_manifest_root=tmp_path / "input-manifests",
    )

    first_manifest = json.loads(
        Path(result["artifacts"]["photo_manifests"][0]).read_text(encoding="utf-8")
    )
    assert first_manifest["property_key"].startswith("mls:")
    assert ":" not in str(Path(result["artifacts"]["photo_manifests"][0]).parent.name)
    assert first_manifest["source_status"] in {
        "AVAILABLE",
        "PHOTO_SOURCE_UNAVAILABLE",
    }
    if first_manifest["source_status"] == "AVAILABLE":
        assert first_manifest["images"]
        assert all(item["content_labels"] == [] for item in first_manifest["images"])
