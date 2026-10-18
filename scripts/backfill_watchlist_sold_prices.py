#!/usr/bin/env python3
"""Backfill sold prices into the watchlist from BCA's public sales layer.

BCA's ArcGIS recent-sales layer lags the market by roughly a quarter (and its
per-property history only shows completed calendar years), so sold prices are
a lagging learning signal — run this periodically to fill them in.

Matching rule (conservative): same normalized address key AND sale_date on or
after the entry's first_seen scan.  A matched row upgrades a SOLD entry's
missing price, and can upgrade a REMOVED/UNRESOLVED entry to SOLD — the sale
record is itself the proof a transaction happened.

Usage:
    .venv/bin/python scripts/backfill_watchlist_sold_prices.py \
        --sales data/research/raw/location/bca-arcgis-sales-YYYY-MM-DD.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.watchlist import (  # noqa: E402
    load_watchlist,
    normalize_address_key,
    record_outcome,
    save_watchlist,
)

WATCHLIST_PATH = ROOT / "data/research/watchlist/property-watchlist.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sales", required=True)
    args = ap.parse_args()

    sales_payload = json.load(open(args.sales))
    rows = sales_payload.get("sales") or sales_payload.get("rows") or sales_payload
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        key = normalize_address_key(r.get("address") or "")
        if key:
            by_key.setdefault(key, []).append(r)

    wl = load_watchlist(WATCHLIST_PATH)
    filled = upgraded = 0
    for e in wl["entries"]:
        if e["status"] not in ("SOLD", "REMOVED", "EXPIRED_OR_WITHDRAWN", "OUTCOME_UNRESOLVED"):
            continue
        candidates = [
            r for r in by_key.get(e["address_key"], [])
            if r.get("sale_date") and r["sale_date"] >= e["first_seen"]
        ]
        if not candidates:
            continue
        sale = max(candidates, key=lambda r: r["sale_date"])
        sold = e.get("sold") or {}
        if e["status"] != "SOLD":
            upgraded += 1
        if not sold.get("price"):
            filled += 1
        record_outcome(e, {
            "type": "SOLD",
            "price": sale.get("sale_price"),
            "date": sale["sale_date"],
            "source": "bca_arcgis_sales",
            "bca_total_at_sale": sale.get("bca_total"),
            "record_id": sale.get("record_id"),
            "previously": sold or None,
        })
    save_watchlist(wl, WATCHLIST_PATH)
    print(f"sales rows indexed: {sum(len(v) for v in by_key.values())}")
    print(f"prices filled: {filled} | entries upgraded to SOLD: {upgraded}")


if __name__ == "__main__":
    main()
