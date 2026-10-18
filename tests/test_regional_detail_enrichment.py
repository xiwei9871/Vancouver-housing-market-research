from northshore_watchdog.config import load_config
from northshore_watchdog.research.regional_detail import (
    REGIONAL_PRESETS,
    regional_detail_config,
    regional_official_config,
)


def test_regional_detail_config_limits_run_to_vancouver_boundary_presets():
    config = load_config("config/research-discovery.yaml")

    regional = regional_detail_config(config)

    assert [item["name"] for item in regional["discovery"]["presets"]] == list(REGIONAL_PRESETS)
    assert regional["discovery_official_enrichment"]["enabled"] is False
    assert regional["screening_v2"]["enabled"] is False


def test_regional_official_config_removes_inherited_property_cap():
    config = load_config("config/research-discovery.yaml")

    enriched = regional_official_config(config)

    assert enriched["official_enrichment"]["max_properties_per_run"] == 0
    assert enriched["official_enrichment"]["discovery_batch_size"] == 20
    assert enriched["discovery_official_enrichment"]["enabled"] is True
