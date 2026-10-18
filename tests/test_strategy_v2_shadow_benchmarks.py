import json
from pathlib import Path

from northshore_watchdog.research.discovery import DiscoveryCard
from northshore_watchdog.research.strategy_v2_shadow import build_shadow_portfolio


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/research/normalized/portfolio-official-enrichment-2026-09-07.json"
LEGACY = ROOT / "data/research/normalized/portfolio-analysis-2026-09-09.json"


def _rows():
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    cards = [DiscoveryCard.model_validate(item) for item in payload["official_enrichment"]["cards"]]
    rows, _ = build_shadow_portfolio(cards, legacy_reference_path=LEGACY)
    return {row.address: row for row in rows}


def test_benchmark_tracks_and_ledgers_are_structurally_distinct():
    rows = _rows()
    assert rows["3910 W 10TH AVENUE"].source_provenance["listing"]["url"]
    assert "bca-assessment" in rows["3910 W 10TH AVENUE"].source_provenance["official"]
    assert rows["4561 NANAIMO STREET"].asset_classification.primary_track == "HOME_REBUILD"
    assert rows["4561 NANAIMO STREET"].location.parcel_facts["property_fronts_major_road"] is True
    assert "DIRECT_MAJOR_ROAD_EXPOSURE" in rows["4561 NANAIMO STREET"].location.risks

    melbourne = rows["5454 MELBOURNE STREET"]
    assert melbourne.asset_classification.primary_track == "LAND_DEVELOPMENT"
    scenarios = {item.scenario_name: item for item in melbourne.development_scenarios}
    assert scenarios["BASE"].ralv is None
    assert "REAR_ACCESS_CONFIRMATION_REQUIRED" in scenarios["BASE"].blocking_conditions
    assert scenarios["CONDITIONAL"].unit_count is not None

    assert rows["3910 W 10TH AVENUE"].holding.survival_capex_base is not None
    assert rows["3910 W 10TH AVENUE"].holding.cash_holding_basis_base is not None
    assert rows["3910 W 10TH AVENUE"].holding.holding_condition_band == "POOR"

    assert rows["1016 E 18TH AVENUE"].asset_classification.primary_track == "UNCLASSIFIED_REVIEW"
    assert rows["1016 E 18TH AVENUE"].development_potential < 50
    assert rows["2413 COLLINGWOOD STREET"].asset_classification.primary_track == "PREMIUM_SMALL_LOT"


def test_all_scenarios_are_economic_unknown_until_inputs_exist():
    rows = _rows()
    for row in rows.values():
        for scenario in row.development_scenarios:
            assert scenario.ralv is None
            assert scenario.cash_mos is None
