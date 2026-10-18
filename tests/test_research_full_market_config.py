from pathlib import Path

from northshore_watchdog.config import load_config
from northshore_watchdog.research.models import EvidenceStatus, SourceRole, SourceType
from northshore_watchdog.research.provenance import load_source_registry_entries


def test_full_market_config_has_user_approved_gates_and_routes():
    config = load_config("config/research-full-market.yaml")
    acquisition = config["acquisition"]
    crawl_sources = acquisition["crawl_sources"]

    assert config["renovation"]["user_strategy"]["baseline_hold_years"] == 5
    assert config["research_filters"]["minimum_bedrooms"] == 2
    assert config["research_filters"]["minimum_bathrooms"] == 1
    assert config["research_filters"]["minimum_finished_sqft"] == 1250
    assert config["research_filters"]["minimum_lot_area_sqft"] == 3000
    assert config["research_filters"]["minimum_frontage_ft"] == 33
    assert config["research_filters"]["minimum_depth_ft"] == 100
    assert config["research_filters"]["major_road_discount_threshold"] == 0.15
    assert config["research_filters"]["major_road_distance_threshold_m"] == 50

    filters = config["research_filters"]
    policy = config["research_policy"]
    assert filters["minimum_bedrooms"] == policy["current_house_minimums"]["bedrooms"]
    assert filters["minimum_bathrooms"] == policy["current_house_minimums"]["bathrooms"]
    assert filters["minimum_finished_sqft"] == policy["current_house_minimums"]["finished_sqft"]
    assert filters["minimum_lot_area_sqft"] == policy["standard_redevelopment_min_lot_area_sqft"]
    assert filters["minimum_frontage_ft"] == policy["standard_redevelopment_min_frontage_ft"]
    assert filters["minimum_depth_ft"] == policy["standard_redevelopment_min_depth_ft"]
    assert filters["major_road_discount_threshold"] == policy["major_road_discount_threshold"]
    assert filters["major_road_distance_threshold_m"] == policy["major_road_distance_threshold_m"]

    names = {item["name"] for item in crawl_sources}
    assert names == {"zolo-full-market", "realtor-full-market"}
    zolo = next(item for item in crawl_sources if item["name"] == "zolo-full-market")
    realtor = next(item for item in crawl_sources if item["name"] == "realtor-full-market")
    assert len(zolo["entrypoints"]) == 4
    assert len(realtor["entrypoints"]) == 5
    assert all(item["allowed_domains"] for item in crawl_sources)


def test_full_market_source_registry_is_loaded_from_project_path():
    config = load_config("config/research-full-market.yaml")
    registry_path = Path(config["_project_root"]) / config["source_registry_path"]
    assert registry_path.exists()
    assert config["source_registry_path"].endswith("full_market_source_registry.yaml")

    registry = load_source_registry_entries(config)
    expected = {
        "source:zolo-full-market": (SourceRole.ZOLO_PUBLIC, SourceType.AGGREGATOR, "zolo.ca"),
        "source:realtor-full-market": (SourceRole.REALTOR_CA_PUBLIC, SourceType.MLS, "realtor.ca"),
    }
    assert set(registry) == set(expected)
    for source_registry_id, (source_role, source_type, domain) in expected.items():
        entry = registry[source_registry_id]
        assert entry.source_role is source_role
        assert entry.source_type is source_type
        assert entry.domain == domain
        assert entry.enabled is True
        assert entry.authorization_status == "NOT_APPLICABLE"
        assert entry.evidence_status is EvidenceStatus.UNCONFIRMED


def test_public_crawl_limits_are_safety_caps_not_result_counts():
    config = load_config("config/research-full-market.yaml")
    crawl = config["acquisition"]["crawl"]
    assert crawl["max_search_pages"] > 50
    assert crawl["max_detail_requests"] > 100
    assert "expected_public_result_count" not in crawl
