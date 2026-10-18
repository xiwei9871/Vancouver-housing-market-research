from pathlib import Path

import yaml


CONFIG_PATH = Path("config/strategy-v2-shadow.yaml")


def _load_config() -> dict:
    assert CONFIG_PATH.is_file(), f"missing Strategy V2 shadow config: {CONFIG_PATH}"
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_strategy_v2_config_freezes_legacy_reference_and_disables_ranking():
    config = _load_config()

    assert config["schema_version"] == "strategy-v2-shadow-v1"
    assert config["ranking"]["enabled"] is False
    assert config["legacy_reference"] == {
        "analysis_artifact": "data/research/normalized/portfolio-analysis-2026-09-09.json",
        "report_index": "reports/properties/2026-09-09/index.md",
    }


def test_strategy_v2_config_freezes_calibration_tracks_without_v1_score_caps():
    config = _load_config()

    assert config["calibration_split"] == {
        "random_seed": 20260909,
        "calibration_count": 120,
        "blind_test_count": 46,
    }
    assert config["tracks"] == {
        "normal": [
            "HOME_REBUILD",
            "LAND_DEVELOPMENT",
            "PREMIUM_SMALL_LOT",
            "INCOME_HOLD",
        ],
        "special": ["SPECIAL_SITUATION", "UNCLASSIFIED_REVIEW"],
    }
    assert config["road_policy"] == {
        "role": "LOCATION_LEDGER_ONLY",
        "unknown_value": "UNKNOWN",
    }
    assert not any("cap" in key for key in config["road_policy"])


def test_strategy_v2_config_excludes_unconfirmed_development_inputs_from_base():
    config = _load_config()

    assert config["development"]["include_toa_in_base"] is False
    assert config["development"]["include_assembly_in_base"] is False
    assert config["development"]["require_confirmed_access_for_base"] is True
    assert config["development"]["economics"]["enabled"] is False
    assert config["holding"]["survival_capex_bands"]["pre_1960"]["base"] == 120000
    assert config["location"]["road_discount_priors_pct"]["ARTERIAL_BUS_TRUCK"] == [0.08, 0.10, 0.12]
