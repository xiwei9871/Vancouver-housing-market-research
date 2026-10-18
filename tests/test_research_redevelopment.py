from datetime import datetime, timezone

from northshore_watchdog.research.models import PropertyRecord, ScenarioType
from northshore_watchdog.research.redevelopment import analyze_redevelopment, future_uplift


OBSERVED_AT = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _config():
    return {
        "source_registry": [
            {
                "source_registry_id": "source:test-official-zoning",
                "name": "test official zoning",
                "source_type": "OFFICIAL_GOV",
                "authorization_status": "NOT_APPLICABLE",
                "evidence_status": "CONFIRMED",
            }
        ],
        "redevelopment": {
            "hard_cost_per_sqft": [450, 550],
            "soft_cost_pct_of_hard": 0.20,
            "developer_specific_cost_pct_of_hard": 0.10,
            "contingency_pct_of_hard": 0.10,
            "saleable_efficiency": 0.90,
            "family_duplex_unit_size_sqft": [1250, 1800],
            "scenario_unit_range": [2, 3, 4, 5, 6],
            "resale_price_per_sqft": 1000,
            "rules": {
                "City of Vancouver:R1-1": {
                    "maximum_fsr": 1.0,
                    "maximum_units": 6,
                    "duplex_permitted": True,
                    "multiplex_permitted": True,
                }
            },
        }
    }


def _record(**updates):
    values = {
        "property_id": "pid:REDEV-1",
        "address": "1 Redevelopment Street",
        "municipality": "City of Vancouver",
        "neighbourhood": "Knight",
        "property_type": "Detached House",
        "ownership_type": "FREEHOLD_NON_STRATA",
        "lot_area_sqft": 6000,
        "frontage_ft": 50,
        "depth_ft": 120,
        "zoning": "R1-1",
        "bylaw_version": "test-bylaw-v1",
        "maximum_fsr": 1.0,
        "maximum_units": 6,
        "finished_sqft": 2200,
        "official_zoning_confirmed": True,
        "official_zoning_source_url": "local://official/r1-1",
        "official_zoning_source_registry_id": "source:test-official-zoning",
        "official_zoning_evidence_status": "CONFIRMED",
        "official_zoning_retrieved_at": OBSERVED_AT,
        "road_class": "B",
        "property_fronts_major_road": True,
        "toa": True,
        "assembly_score": 65,
    }
    values.update(updates)
    return PropertyRecord(**values)


def test_redevelopment_runs_keep_duplex_and_multiplex_and_splits_costs():
    result = analyze_redevelopment(_record(), _config(), observed_at=OBSERVED_AT)
    by_type = {item.scenario_type: item for item in result.scenarios}

    assert {ScenarioType.KEEP_EXISTING_HOUSE, ScenarioType.DUPLEX, ScenarioType.MAX_REALISTIC_MULTIPLEX} <= set(by_type)
    duplex = by_type[ScenarioType.DUPLEX]
    assert duplex.legal_status == "PERMITTED_BY_OFFICIAL_BYLAW"
    assert duplex.layout_options == ["front/rear", "side-by-side"]
    assert duplex.estimated_front_unit_area == duplex.estimated_rear_unit_area
    assert duplex.front_building_noise_buffer_hypothesis is True
    assert duplex.hard_cost is not None
    assert duplex.soft_cost is not None
    assert duplex.developer_specific_cost is not None
    assert duplex.contingency is not None
    assert duplex.total_project_cost == duplex.hard_cost + duplex.soft_cost + duplex.developer_specific_cost + duplex.contingency


def test_listing_phrase_cannot_create_legal_redevelopment_entitlement():
    record = _record(
        official_zoning_confirmed=False,
        permitted_uses=["duplex", "multiple dwelling"],
        description="Excellent duplex / multiplex opportunity",
    )

    result = analyze_redevelopment(record, _config(), observed_at=OBSERVED_AT)
    by_type = {item.scenario_type: item for item in result.scenarios}

    assert by_type[ScenarioType.DUPLEX].legal_status == "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    assert by_type[ScenarioType.DUPLEX].unit_count is None
    assert by_type[ScenarioType.MAX_REALISTIC_MULTIPLEX].unit_count is None


def test_zoning_flag_without_a_retrievable_official_source_is_not_entitlement():
    result = analyze_redevelopment(
        _record(official_zoning_confirmed=True, official_zoning_source_url=None),
        _config(),
        observed_at=OBSERVED_AT,
    )

    duplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.DUPLEX)
    assert duplex.legal_status == "UNKNOWN_REQUIRES_OFFICIAL_REVIEW"
    assert duplex.unit_count is None


def test_zoning_flag_without_registered_source_is_not_entitlement():
    config = _config()
    config.pop("source_registry")

    result = analyze_redevelopment(_record(), config, observed_at=OBSERVED_AT)

    assert result.official_zoning_evidence is False
    assert next(item for item in result.scenarios if item.scenario_type is ScenarioType.DUPLEX).unit_count is None


def test_official_restrictive_rule_cannot_be_overridden_by_listing_claims():
    config = _config()
    config["redevelopment"]["rules"]["City of Vancouver:R1-1"] = {
        "bylaw_version": "official-restrictive-v1",
        "maximum_fsr": 0.5,
        "maximum_units": 1,
        "duplex_permitted": False,
        "multiplex_permitted": False,
    }
    record = _record(
        maximum_fsr=1.0,
        maximum_units=6,
        permitted_uses=["duplex", "multiple dwelling"],
    )

    result = analyze_redevelopment(record, config, observed_at=OBSERVED_AT)
    by_type = {item.scenario_type: item for item in result.scenarios}

    assert by_type[ScenarioType.DUPLEX].unit_count is None
    assert by_type[ScenarioType.MAX_REALISTIC_MULTIPLEX].unit_count is None
    assert by_type[ScenarioType.DUPLEX].total_buildable_sqft == 3000


def test_explicit_official_false_entitlements_override_a_permissive_unit_cap():
    config = _config()
    config["redevelopment"]["rules"]["City of Vancouver:R1-1"] = {
        "bylaw_version": "official-use-restrictive-v1",
        "maximum_fsr": 1.0,
        "maximum_units": 6,
        "duplex_permitted": False,
        "multiplex_permitted": False,
    }

    result = analyze_redevelopment(_record(), config, observed_at=OBSERVED_AT)
    by_type = {item.scenario_type: item for item in result.scenarios}

    assert by_type[ScenarioType.DUPLEX].unit_count is None
    assert by_type[ScenarioType.DUPLEX].legal_status == "NOT_PERMITTED_BY_OFFICIAL_BYLAW"
    assert by_type[ScenarioType.MAX_REALISTIC_MULTIPLEX].unit_count is None
    assert by_type[ScenarioType.MAX_REALISTIC_MULTIPLEX].legal_status == "NOT_PERMITTED_BY_OFFICIAL_BYLAW"


def test_official_rule_without_optional_boolean_fields_remains_valid():
    config = _config()
    config["redevelopment"]["rules"]["City of Vancouver:R1-1"] = {
        "bylaw_version": "official-minimal-v1",
        "maximum_fsr": 1.0,
        "maximum_units": 4,
    }

    result = analyze_redevelopment(_record(), config, observed_at=OBSERVED_AT)

    assert result.official_zoning_evidence is True
    assert next(item for item in result.scenarios if item.scenario_type is ScenarioType.DUPLEX).unit_count == 2


def test_official_rules_compare_2_to_6_products_and_can_prefer_duplex_fit():
    config = _config()
    config["redevelopment"]["resale_price_per_sqft"] = 1500
    result = analyze_redevelopment(
        _record(expected_market_value=1_500_000),
        config,
        observed_at=OBSERVED_AT,
    )
    multiplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX)

    assert [item["unit_count"] for item in multiplex.product_options] == [2, 3, 4, 5, 6]
    assert result.best_economic_product is ScenarioType.DUPLEX
    assert result.property.best_economic_product == ScenarioType.DUPLEX.value


def test_toa_and_assembly_are_long_term_option_only_and_uplift_uses_future_value():
    result = analyze_redevelopment(_record(), _config(), observed_at=OBSERVED_AT)
    toa = next(item for item in result.scenarios if item.scenario_type is ScenarioType.TOA_OR_ASSEMBLY)

    assert toa.legal_status == "LONG_TERM_OPTION_ONLY"
    assert toa.unit_count is None
    assert future_uplift(2_500_000, 4_600_000, 1_700_000) == 400_000
    assert future_uplift(1_500_000, 4_600_000, 1_700_000) == 1_400_000


def test_realistic_buildable_area_cannot_exceed_official_theoretical_area():
    result = analyze_redevelopment(
        _record(realistic_buildable_sqft=7_000),
        _config(),
        observed_at=OBSERVED_AT,
    )

    assert result.property.theoretical_buildable_sqft == 6_000
    assert result.property.realistic_buildable_sqft == 6_000
    assert all(
        scenario.realistic_buildable_sqft is None
        or scenario.total_buildable_sqft is None
        or scenario.realistic_buildable_sqft <= scenario.total_buildable_sqft
        for scenario in result.scenarios
    )
    assert any("exceeded theoretical" in warning for warning in result.warnings)


def test_redevelopment_does_not_select_a_best_product_without_resale_economics():
    config = _config()
    config["redevelopment"]["resale_price_per_sqft"] = None
    record = _record(expected_market_value=None)

    result = analyze_redevelopment(record, config, observed_at=OBSERVED_AT)

    multiplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX)
    assert multiplex.product_options
    assert multiplex.best_product_option is None
    assert result.best_economic_product is None
    assert result.property.best_economic_product is None


def test_theoretical_area_applies_explicit_coverage_height_and_setback_constraints():
    config = _config()
    config["redevelopment"]["rules"]["City of Vancouver:R1-1"].update({
        "lot_coverage": 0.80,
        "maximum_height": 10,
        "setbacks": {"front": 10, "rear": 10, "side": 5},
    })

    result = analyze_redevelopment(_record(), config, observed_at=OBSERVED_AT)

    assert result.property.theoretical_buildable_sqft == 4_000
    assert result.property.realistic_buildable_sqft == 3_280
    assert any("maximum-height" in warning for warning in result.warnings)
    assert any("setback envelope" in warning for warning in result.warnings)


def test_future_uplift_below_owner_safety_margin_blocks_best_product():
    record = _record(expected_market_value=3_000_000)

    result = analyze_redevelopment(record, _config(), observed_at=OBSERVED_AT)

    assert result.best_economic_product is None
    assert result.property.best_economic_product is None


def test_future_uplift_only_gate_uses_the_500k_owner_profit_floor():
    config = _config()
    config["redevelopment"]["minimum_owner_uplift"] = 500_000
    record = _record(asking=None, expected_market_value=500_000)

    result = analyze_redevelopment(record, config, observed_at=OBSERVED_AT)

    duplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.DUPLEX)
    assert duplex.estimated_project_profit is None
    assert duplex.redevelopment_uplift == 220_000
    assert duplex.economic_gate_pass is False
    assert result.best_economic_product is None


def test_redevelopment_scenario_reports_project_profit_separately_from_future_uplift():
    record = _record(
        asking=1_500_000,
        renovation_estimate=50_000,
        expected_market_value=2_000_000,
    )
    result = analyze_redevelopment(record, _config(), observed_at=OBSERVED_AT)
    duplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.DUPLEX)

    assert duplex.estimated_project_profit is not None
    assert duplex.financing_not_modeled is True
    assert duplex.exit_mode == "ALL_SALE_BASELINE"
    assert duplex.estimated_project_profit != duplex.redevelopment_uplift


def test_multiplex_summary_preserves_best_product_project_profit_gate():
    config = _config()
    config["redevelopment"]["minimum_project_profit"] = 500_000
    config["redevelopment"]["project_costs"] = {
        "purchase_closing_pct": 0.0,
        "selling_cost_pct": 0.03,
        "gst_tax_pct": 0.0,
    }
    config["redevelopment"]["resale_price_per_sqft"] = 1_500
    result = analyze_redevelopment(
        _record(asking=1_000_000),
        config,
        observed_at=OBSERVED_AT,
    )
    multiplex = next(item for item in result.scenarios if item.scenario_type is ScenarioType.MAX_REALISTIC_MULTIPLEX)

    assert multiplex.best_product_option is not None
    assert multiplex.economic_gate_pass is multiplex.best_product_option["economic_gate_pass"]


def test_house_rebuild_only_scope_adds_single_detached_rebuild_scenario():
    result = analyze_redevelopment(
        _record(
            research_scope="HOUSE_REBUILD_ONLY",
            lot_area_sqft=2_950,
            frontage_ft=25,
            depth_ft=89,
            asking=1_500_000,
        ),
        _config(),
        observed_at=OBSERVED_AT,
    )

    rebuild = next(item for item in result.scenarios if item.scenario_type is ScenarioType.DETACHED_REBUILD)
    assert rebuild.unit_count == 1
    assert rebuild.likely_buyer == "Westside detached-house buyer"
    assert rebuild.estimated_project_profit is not None
