from northshore_watchdog.research.strategy_v2_shadow import (
    AcquisitionLedger,
    AssetClassification,
    GateFinding,
    HoldingLedger,
    LocationLedger,
    StrategyV2Property,
)


def test_v2_property_keeps_unknowns_and_separates_ledgers():
    row = StrategyV2Property(
        property_key="mls:R1",
        address="TEST STREET",
        asset_classification=AssetClassification(primary_track="HOME_REBUILD"),
        gates=[GateFinding(code="TITLE_CHECK", status="PASS")],
        holding=HoldingLedger(),
        location=LocationLedger(),
        acquisition=AcquisitionLedger(),
    )
    assert row.holding.cash_holding_basis_base is None
    assert row.development_scenarios == []
    assert row.acquisition.base_cash_mos is None
