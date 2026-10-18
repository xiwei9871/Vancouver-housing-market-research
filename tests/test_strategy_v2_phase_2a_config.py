from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).parents[1] / "config" / "strategy-v2-phase-2a.yaml"


def test_phase_2a_config_is_frozen_for_task_1():
    config = yaml.safe_load(CONFIG_PATH.read_text())

    assert config["schema_version"] == "strategy-v2-phase-2a-v1"
    assert config["ranking"]["enabled"] is False

    assert config["photo"]["required_space_groups"] == [
        "exterior",
        "living",
        "basement",
        "site",
    ]
    assert config["photo"]["duplicate_policy"] == "exact_sha256_then_perceptual_hash"
    assert config["photo"]["missing_source_policy"] == "preserve_unknown_continue"
    assert config["photo"]["visual_adapter"] == "disabled"
    assert config["photo"]["acquisition"]["enabled"] is False
    assert config["photo"]["acquisition"]["perceptual_distance"] == 5
    assert config["photo"]["visual"]["enabled"] is False
    assert config["photo"]["visual"]["request_mode"] == "one_request_per_image"
    assert config["photo"]["visual"]["api_key_env"] == "VISION_API_KEY"
    assert config["photo"]["visual"]["wire_api"] == "responses"
    assert config["photo"]["visual"]["max_attempts"] == 3
    assert config["photo"]["visual"]["retry_backoff_seconds"] == 1.0

    assert config["shortlist"]["target_range"] == [25, 35]
    assert config["shortlist"]["track_targets"] == {
        "HOME_REBUILD": 20,
        "LAND_DEVELOPMENT": 5,
        "PREMIUM_SMALL_LOT": 5,
    }
    assert config["shortlist"]["special_tracks_excluded"] == ["SPECIAL_SITUATION"]
    assert config["shortlist"]["protect_pareto_frontier"] is True
    assert config["shortlist"]["protect_exceptional_signal"] is True

    assert config["analysis_routing"]["enabled"] is True
    assert config["analysis_routing"]["min_home_bedrooms"] == 3
    assert config["analysis_routing"]["min_home_bathrooms"] == 2
    assert config["analysis_routing"]["min_home_lot_area_sqft"] == 3000
    assert config["analysis_routing"]["min_visual_photo_count"] == 3
    assert config["analysis_routing"]["min_full_visual_photo_count"] == 6
    assert config["analysis_routing"]["representative_photo_budget"] == 3
    assert "land value only" in config["analysis_routing"]["land_dominant_phrases"]
    assert "rebuild" in config["analysis_routing"]["development_signal_phrases"]
    assert config["analysis_routing"]["special_tracks"] == ["SPECIAL_SITUATION"]

    assert config["calibration"]["random_seed"] == 20260909
    assert config["calibration"]["calibration_count"] == 120
    assert config["calibration"]["blind_test_count"] == 46
    assert config["calibration"]["must_keep_recall_target"] == 1.0
    assert config["calibration"]["keep_recall_target"] == 0.90

    assert config["scoring"]["unknown_value"] == "UNKNOWN"
    assert config["scoring"]["capex_bands"] == ["A", "B", "C", "D", "E"]
