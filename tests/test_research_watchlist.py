"""Watchlist reconcile tests: relists, price moves, disappearance, reappear, outcomes."""

from __future__ import annotations

import json

from northshore_watchdog.research.watchlist import (
    merge_watchlist,
    normalize_address_key,
    reconcile_watchlist,
    record_outcome,
    weekly_changes,
)


def _card(mls, address, asking, status="active"):
    return {"mls_number": mls, "address": address, "asking": asking,
            "listing_status": status, "source_url": f"https://x/{mls}"}


def test_normalize_address_key_strips_street_type():
    assert normalize_address_key("4343 11TH AVENUE") == "4343 11TH"
    assert normalize_address_key("3055 ROSEMONT DRIVE") == "3055 ROSEMONT"
    assert normalize_address_key("  4225   JOHN  STREET ") == "4225 JOHN"


def test_new_property_appears():
    wl = reconcile_watchlist(
        {"2026-10-05": [_card("R1", "100 MAIN STREET", 1_500_000)]},
        latest_scan="2026-10-05",
    )
    e = wl["entries"][0]
    assert e["status"] == "ACTIVE"
    assert e["events"][0]["type"] == "APPEARED"


def test_relist_with_price_drop():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "10 ELM STREET", 1_500_000)],
            "2026-10-05": [_card("R2", "10 ELM STREET", 1_350_000)],
        },
        latest_scan="2026-10-05",
    )
    e = wl["entries"][0]
    types = [ev["type"] for ev in e["events"]]
    assert "RELISTED" in types and "PRICE_DROP" in types
    drop = [ev for ev in e["events"] if ev["type"] == "PRICE_DROP"][0]
    assert drop["delta"] == -150_000
    assert e["status"] == "ACTIVE"
    changes = weekly_changes(wl)
    assert changes["price_drop"][0]["address"] == "10 ELM STREET"


def test_in_listing_price_change_same_mls():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "5 OAK AVENUE", 1_700_000)],
            "2026-10-05": [_card("R1", "5 OAK AVENUE", 1_600_000)],
        },
        latest_scan="2026-10-05",
    )
    types = [ev["type"] for ev in wl["entries"][0]["events"]]
    assert "RELISTED" not in types and "PRICE_DROP" in types


def test_disappearance_marks_off_market_check():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "9 PINE STREET", 1_800_000)],
            "2026-10-05": [_card("R2", "1 MAPLE STREET", 1_800_000)],
        },
        latest_scan="2026-10-05",
    )
    gone = [e for e in wl["entries"] if e["address_key"] == "9 PINE"][0]
    assert gone["status"] == "OFF_MARKET_CHECK"
    assert any(ev["type"] == "DISAPPEARED" for ev in gone["events"])
    assert weekly_changes(wl)["disappeared"][0]["address"] == "9 PINE STREET"


def test_disappearance_out_of_coverage():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "9 PINE STREET", 3_900_000)],
            "2026-10-05": [],
        },
        latest_scan="2026-10-05",
        coverage={"9 PINE": False},
    )
    assert wl["entries"][0]["status"] == "OUT_OF_COVERAGE"


def test_reappear_after_gap():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "7 CEDAR STREET", 1_500_000)],
            "2026-09-20": [_card("R2", "OTHER STREET", 1_500_000)],
            "2026-10-05": [_card("R3", "7 CEDAR STREET", 1_400_000)],
        },
        latest_scan="2026-10-05",
    )
    e = [x for x in wl["entries"] if x["address_key"] == "7 CEDAR"][0]
    types = [ev["type"] for ev in e["events"]]
    assert "REAPPEARED" in types
    re = [ev for ev in e["events"] if ev["type"] == "REAPPEARED"][0]
    assert re["missed_scans"] == ["2026-09-20"]


def test_sold_outcome_preserved_on_merge():
    wl = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "3 BIRCH STREET", 1_600_000)],
            "2026-10-05": [],
        },
        latest_scan="2026-10-05",
    )
    e = wl["entries"][0]
    record_outcome(e, {"type": "SOLD", "price": 1_550_000, "date": "2026-10-01"})
    assert e["status"] == "SOLD"

    # Next week's reconcile still sees it absent — merge keeps the outcome.
    wl2 = reconcile_watchlist(
        {
            "2026-09-05": [_card("R1", "3 BIRCH STREET", 1_600_000)],
            "2026-10-12": [],
        },
        latest_scan="2026-10-12",
    )
    merged = merge_watchlist(wl, wl2)
    e2 = merged["entries"][0]
    assert e2["status"] == "SOLD" and e2["sold"]["price"] == 1_550_000
    assert not any(ev["type"] == "DISAPPEARED" and ev["date"] == "2026-10-12"
                   for ev in e2["events"])


def test_tier_priority_and_sorting():
    wl = reconcile_watchlist(
        {
            "2026-10-05": [
                _card("R1", "A STREET", 1_000_000),
                _card("R2", "B STREET", 1_000_000),
            ],
        },
        latest_scan="2026-10-05",
        tiers={"B STREET": "KEEP", "A STREET": "PASS"},
    )
    assert wl["entries"][0]["tier"] == "KEEP"  # KEEP sorts first


def test_duplicate_sighting_same_scan_deduped():
    wl = reconcile_watchlist(
        {"2026-10-05": [_card("R1", "4 ASH STREET", 1_200_000),
                        _card("R1", "4 ASH STREET", 1_200_000)]},
        latest_scan="2026-10-05",
    )
    e = wl["entries"][0]
    assert len(e["sightings"]) == 1
    assert len([ev for ev in e["events"] if ev["type"] == "APPEARED"]) == 1
