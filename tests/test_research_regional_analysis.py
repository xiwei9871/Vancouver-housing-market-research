from northshore_watchdog.research.regional_analysis import analyze_regional_boundary_scan


def _boundary_builder_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "scripts" / "build_market_boundaries.py"
    spec = importlib.util.spec_from_file_location("build_market_boundaries", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _registry():
    return {
        "schema_version": "market-boundaries-v1",
        "regions": [
            {
                "boundary_id": "region-a",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-123.2, 49.2],
                        [-123.1, 49.2],
                        [-123.1, 49.3],
                        [-123.2, 49.3],
                        [-123.2, 49.2],
                    ]],
                },
                "source_local_areas": ["Area A"],
            }
        ],
    }


def test_regional_analysis_counts_prices_and_boundary_validation():
    result = analyze_regional_boundary_scan(
        {
            "observed_at": "2026-09-05T05:30:00Z",
            "presets": [{
                "preset": "region-a",
                "result_count": 3,
                "parsed_cards": 2,
                "validated_cards": 2,
                "parse_warnings": ["row 3: listing card has no explicit address"],
                "cards": [
                    {"address": "1 Main Street", "asking": 1_100_000, "beds": 3, "baths": 2, "sqft": 1_500, "lat": 49.25, "lon": -123.15},
                    {"address": "2 Main Street", "asking": 1_600_000, "beds": 2, "baths": 1, "sqft": 1_250, "lat": 49.25, "lon": -123.25},
                ],
            }],
        },
        _registry(),
    )

    summary = result["regions"][0]
    assert summary["reported_results"] == 3
    assert summary["parsed_cards"] == 2
    assert summary["missing_address_observations"] == 1
    assert summary["boundary_point_counts"] == {"inside": 1, "outside": 1, "unknown": 0}
    assert summary["price"]["median"] == 1_350_000
    assert summary["family_proxy_count"] == 1
    assert summary["field_missing_counts"]["lot_dimensions"] == 2


def test_regional_analysis_does_not_create_shortlist_from_discovery_cards():
    result = analyze_regional_boundary_scan(
        {"presets": [{"preset": "region-a", "cards": []}]},
        _registry(),
    )

    assert result["total_validated_cards"] == 0
    assert result["investment_ranking_status"] == "NOT_RUN_DISCOVERY_ONLY"


def test_boundary_builder_can_rebuild_from_saved_raw_export(tmp_path):
    path = tmp_path / "local-area-boundary.geojson"
    path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")

    assert _boundary_builder_module()._load_source(path)["type"] == "FeatureCollection"
