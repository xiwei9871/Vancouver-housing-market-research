#!/usr/bin/env python3
"""Resolve outcomes for watchlist entries that disappeared from the latest scan.

For each OFF_MARKET_CHECK entry we refetch its last REALTOR.ca detail URL via
the CDP browser, parse it with the normal detail parser, and classify:

- SOLD            — a new "Sale Closed" history row dated on/after the listing
                    was last seen, or an explicit sold status
- STILL_LISTED    — page loads and shows an active listing (scan coverage gap)
- REMOVED         — page is gone / challenge / parse failure (expired or
                    withdrawn; sold price not publicly shown)

Usage:
    .venv/bin/python scripts/check_watchlist_outcomes.py [--tiers KEEP,WATCH] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from northshore_watchdog.research.public_feeds import (  # noqa: E402
    has_visible_challenge,
    parse_realtor_ca_detail,
)
from northshore_watchdog.research.watchlist import (  # noqa: E402
    TIER_ORDER,
    load_watchlist,
    record_outcome,
    save_watchlist,
)

WATCHLIST_PATH = ROOT / "data/research/watchlist/property-watchlist.json"
OUTCOME_LOG = ROOT / "data/research/watchlist/outcome-checks.jsonl"
CDP_ENDPOINT = "http://127.0.0.1:9222"
DELAY_S = 2.5


def classify(payload: bytes | None, url: str, last_seen: str, http_error: str | None) -> dict:
    if payload is None:
        return {"type": "REMOVED", "http_error": http_error}
    if has_visible_challenge(payload):
        return {"type": "UNRESOLVED", "reason": "CHALLENGE"}
    detail = parse_realtor_ca_detail(payload, url, datetime.now(timezone.utc))
    status = str(detail.get("listing_status") or "").lower()
    history = detail.get("realtor_price_sale_history") or []
    new_sales = [
        ev for ev in history
        if "closed" in str(ev.get("status") or "").lower()
        and ev.get("price") and str(ev.get("date") or "")[:10] >= last_seen
    ]
    if new_sales:
        latest = max(new_sales, key=lambda ev: str(ev.get("date") or ""))
        return {"type": "SOLD", "price": latest["price"],
                "date": str(latest.get("date"))[:10], "source": "history"}
    if "sold" in status:
        return {"type": "SOLD", "price": None, "date": None, "source": "status"}
    if "active" in status or detail.get("asking"):
        return {"type": "STILL_LISTED", "asking": detail.get("asking"), "status": status}
    if history:
        return {"type": "REMOVED", "reason": "page live, no new sale rows"}
    return {"type": "UNRESOLVED", "reason": f"status={status or 'unknown'}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="KEEP,SPECIAL,WATCH,PASS,")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_S)
    args = ap.parse_args()
    tier_filter = {t.strip() or None for t in args.tiers.split(",")}

    wl = load_watchlist(WATCHLIST_PATH)
    pending = [
        e for e in wl["entries"]
        if e["status"] in ("OFF_MARKET_CHECK", "OUTCOME_UNRESOLVED")
        and e.get("tier") in tier_filter
        and e.get("latest_source_url")
    ]
    pending.sort(key=lambda e: -(TIER_ORDER.get(e.get("tier")) or 0))
    if args.limit:
        pending = pending[: args.limit]
    print(f"pending outcome checks: {len(pending)}")

    from playwright.sync_api import sync_playwright

    log = OUTCOME_LOG.open("a")
    resolved = 0
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        for i, entry in enumerate(pending):
            url = entry["latest_source_url"]
            outcome = None
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                code = resp.status if resp else 0
                page.wait_for_timeout(1500)
                if not 200 <= code < 300:
                    outcome = classify(None, url, entry["last_seen"], f"HTTP {code}")
                else:
                    outcome = classify(page.content().encode(), url, entry["last_seen"], None)
            except Exception as exc:  # page crashes / timeouts / nav failures
                outcome = {"type": "UNRESOLVED", "reason": f"{type(exc).__name__}: {exc}"}
            record_outcome(entry, outcome)
            resolved += 1
            rec = {"address": entry["address"], "tier": entry.get("tier"),
                   "mls": entry.get("latest_mls"), "outcome": outcome}
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()
            print(f"[{i+1}/{len(pending)}] {entry['address']:34s} {entry.get('tier') or '-':7s} -> {outcome['type']}"
                  + (f" ${outcome['price']:,}" if outcome.get('price') else ""))
            save_watchlist(wl, WATCHLIST_PATH)  # checkpoint every entry
            time.sleep(args.delay)
        page.close()
    log.close()
    print(f"resolved {resolved}")


if __name__ == "__main__":
    main()
