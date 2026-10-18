import hashlib
import json
from pathlib import Path

from scripts.run_strategy_v2_shadow import run_shadow_model


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
LEGACY = ROOT / "data/research/normalized/portfolio-analysis-2026-09-09.json"
LEGACY_INDEX = ROOT / "reports/properties/2026-09-09/index.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_mapping_keys(value):
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_all_mapping_keys(child))
        return keys
    if isinstance(value, list):
        keys = set()
        for child in value:
            keys.update(_all_mapping_keys(child))
        return keys
    return set()


def test_runner_writes_166_rows_without_ranking_and_preserves_legacy(tmp_path):
    before = _sha256(LEGACY)
    index_before = _sha256(LEGACY_INDEX)
    result = run_shadow_model(
        input_path=INPUT,
        legacy_reference_path=LEGACY,
        output_root=tmp_path,
        output_date="2026-09-09",
    )
    after = _sha256(LEGACY)
    index_after = _sha256(LEGACY_INDEX)
    assert result["property_count"] == 166
    assert result["ranking_status"] == "NOT_RUN"
    assert "rankings" not in result
    assert result["split"]["calibration_count"] == 120
    assert result["split"]["blind_test_count"] == 46
    assert before == after
    assert index_before == index_after
    assert result["legacy_reference"]["report_index_sha256_before"] == index_before
    assert result["legacy_reference"]["report_index_sha256_after"] == index_after
    assert Path(result["artifacts"]["json"]).is_file()
    assert Path(result["artifacts"]["csv"]).is_file()
    assert Path(result["artifacts"]["audit_report"]).is_file()
    assert Path(result["artifacts"]["acceptance_report"]).is_file()
    saved = json.loads(Path(result["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert len(saved["properties"]) == 166
    assert all(item["ranking_status"] == "NOT_RUN" for item in saved["properties"])
    forbidden = {"rank", "current_use_score", "dual_score", "redevelopment_score"}
    assert not forbidden.intersection(_all_mapping_keys(saved))
