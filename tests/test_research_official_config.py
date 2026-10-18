from northshore_watchdog.config import load_config


def test_mls_first_config_declares_bounded_official_stage2_enrichment():
    config = load_config("config/research-mls-first.yaml")
    section = config["official_enrichment"]

    assert section["enabled"] is True
    assert section["stage2_only"] is True
    assert section["max_properties_per_run"] == 30
    assert {item["name"] for item in section["sources"]} == {
        "bca-assessment",
        "municipal-zoning",
        "municipal-road",
        "school-catchment",
        "school-performance",
        "sold-history",
    }
    assert all(item["source_type"] in {"BC_ASSESSMENT", "OFFICIAL_GOV"} for item in section["sources"])
    assert config["enrichment"]["providers"] == []


def test_official_config_keeps_municipality_and_school_routes_explicit():
    config = load_config("config/research-mls-first.yaml")
    sources = {item["name"]: item for item in config["official_enrichment"]["sources"]}

    zoning_routes = sources["municipal-zoning"]["routes"]
    assert {value["municipality"] for value in zoning_routes.values()} == {
        "City of Vancouver",
        "City of Burnaby",
        "City of North Vancouver",
        "District of North Vancouver",
        "District of West Vancouver",
    }
    school_routes = sources["school-catchment"]["routes"]
    assert {value["district"] for value in school_routes.values()} == {"SD39", "SD41", "SD44", "SD45"}
