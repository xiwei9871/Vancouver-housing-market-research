#!/usr/bin/env python3
"""Reconcile the property watchlist across weekly scans.

Usage:
    .venv/bin/python scripts/reconcile_watchlist.py

Reads every regional-official-enrichment snapshot (deduped by observed_at
date), merges with human annotation tiers, writes:

- data/research/watchlist/property-watchlist.json
- reports/watchlist/<latest_scan>.md

Properties outside the latest scan's successful coverage (non-Vancouver or
asking outside the covered price union) are marked OUT_OF_COVERAGE rather
than treated as market events.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from northshore_watchdog.research.watchlist import (
    load_watchlist,
    merge_watchlist,
    normalize_address_key,
    reconcile_watchlist,
    render_watchlist_markdown,
    save_watchlist,
)

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = ROOT / "data/research/watchlist/property-watchlist.json"
ANNOTATIONS_PATH = ROOT / "data/research/annotations/strategy-v2-human-annotations-2026-10-05.json"

# Union of preset price bands that succeeded on the latest scan (Vancouver).
COVERED_PRICE_MIN = 1_000_000
COVERED_PRICE_MAX = 2_000_000


def scan_artifacts() -> dict[str, list[dict]]:
    """observed date -> candidates, one snapshot per date."""
    by_date: dict[str, tuple[str, list[dict]]] = {}
    for path in sorted(glob.glob(str(ROOT / "data/research/normalized/regional-official-enrichment*.json"))):
        if ".bak." in path:
            continue
        data = json.load(open(path))
        candidates = data.get("candidates") or []
        dates = sorted({(c.get("observed_at") or "")[:10] for c in candidates if c.get("observed_at")})
        if not dates:
            continue
        date = dates[0]
        # Prefer the artifact with more candidates for the same date (the
        # *-166 replay and the original 2026-09-05 file share observed_at).
        if date not in by_date or len(candidates) > len(by_date[date][1]):
            by_date[date] = (path, candidates)
    # Merge the 2026-09-05 (166) and 2026-09-20 (43) snapshots: distinct
    # observed dates so both survive.  Also merge same-date newbatch files.
    scans: dict[str, list[dict]] = {}
    for date, (path, candidates) in sorted(by_date.items()):
        scans.setdefault(date, []).extend(candidates)
    # newbatch files share observed_at with their parent date — union them in.
    for path in sorted(glob.glob(str(ROOT / "data/research/normalized/regional-official-enrichment-newbatch-*.json"))):
        data = json.load(open(path))
        candidates = data.get("candidates") or []
        dates = sorted({(c.get("observed_at") or "")[:10] for c in candidates if c.get("observed_at")})
        if dates:
            scans.setdefault(dates[0], []).extend(candidates)
    return scans


def main() -> None:
    scans = scan_artifacts()
    latest = max(scans)

    annotations = json.load(open(ANNOTATIONS_PATH))["annotations"]
    tiers = {normalize_address_key(a["address"]): a.get("human_decision")
             for a in annotations if a.get("address")}

    def covered(key: str, cards: list[dict]) -> bool:
        c = cards[0]
        asking = c.get("asking") or 0
        return (c.get("municipality") == "Vancouver"
                and COVERED_PRICE_MIN <= asking <= COVERED_PRICE_MAX)

    # coverage lookup needs representative card per key — rebuild quickly
    rep: dict[str, dict] = {}
    for date, cards in scans.items():
        for c in cards:
            rep.setdefault(normalize_address_key(c.get("address") or ""), c)
    coverage = {k: covered(k, [c]) for k, c in rep.items()}

    wl = reconcile_watchlist(scans, latest_scan=latest, tiers=tiers, coverage=coverage)
    old = load_watchlist(WATCHLIST_PATH)
    wl = merge_watchlist(old, wl)

    save_watchlist(wl, WATCHLIST_PATH)
    report_dir = ROOT / "reports/watchlist"
    report_dir.mkdir(parents=True, exist_ok=True)
    md = render_watchlist_markdown(wl)
    (report_dir / f"{latest}.md").write_text(md)

    from collections import Counter
    print(f"scans: {sorted(scans)}")
    print(f"properties: {wl['property_count']}")
    print("status:", Counter(e['status'] for e in wl['entries']))
    print(f"watchlist -> {WATCHLIST_PATH}")
    print(f"report -> {report_dir / (latest + '.md')}")


if __name__ == "__main__":
    main()
