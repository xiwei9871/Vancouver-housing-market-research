"""Cross-scan property watchlist: track sightings, relists, price changes, disappearances, sold outcomes.

The watchlist is keyed by normalized address (not MLS), because the same
property cycles through new MLS numbers when relisted.  Each entry keeps the
full sighting history across weekly scans plus derived events:

- APPEARED / REAPPEARED
- RELISTED (new MLS for a known address)
- PRICE_DROP / PRICE_UP (relist or in-listing asking change)
- DISAPPEARED (seen before, absent in the latest covered scan)
- SOLD / EXPIRED_OR_WITHDRAWN / UNRESOLVED (outcome classification)

Tiers come from human annotations (KEEP > WATCH > PASS > SPECIAL) and control
highlight priority only — every property ever scanned is tracked, because a
PASS'd property that sells is still a calibration sample.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


TIER_ORDER = {"KEEP": 4, "WATCH": 3, "SPECIAL": 2, "PASS": 1}

_STREET_TYPES = (
    "AVENUE", "AVE", "STREET", "ST", "DRIVE", "DR", "ROAD", "RD",
    "BOULEVARD", "BLVD", "CRESCENT", "CRES", "PLACE", "PL", "COURT",
    "CT", "TERRACE", "WAY", "LANE", "DRIVEWAY", "PARKWAY",
)


def normalize_address_key(address: str) -> str:
    """Stable key for the same civic address across listing formats.

    Strips street-type suffixes and everything after them so that
    '4343 11TH AVENUE' and '4343 11TH AVE W' collide only on the part that
    actually identifies the parcel.  Directionals in the street-name segment
    are kept ('W 18TH' stays distinct from '18TH' — callers should normalize
    upstream formats to the detail-page canonical form when available).
    """
    text = re.sub(r"\s+", " ", (address or "").upper().strip())
    for st in _STREET_TYPES:
        text = re.sub(rf"\b{st}\b.*$", "", text).strip()
    return text


def sighting(
    *,
    scan: str,
    mls: str | None,
    asking: int | float | None,
    status: str = "active",
    source_url: str | None = None,
) -> dict[str, Any]:
    return {
        "scan": scan,
        "mls": mls,
        "asking": int(asking) if asking else None,
        "status": status,
        "source_url": source_url,
    }


def reconcile_entry(
    address_key: str,
    sightings: list[dict[str, Any]],
    *,
    latest_scan: str,
    all_scans: Iterable[str] | None = None,
    covered: bool = True,
) -> dict[str, Any]:
    """Build one watchlist entry from its ordered sightings.

    ``all_scans`` is every scan date run so far — a sighting that follows a
    missed scan emits REAPPEARED rather than a plain observation.  ``covered``
    marks whether the latest scan's successful presets would have captured
    this address (municipality + price band).  A disappearance under
    coverage=True is a real market event candidate; under coverage=False it
    is just "not observable this week".
    """
    deduped: dict[tuple[str, str | None], dict[str, Any]] = {}
    for s in sightings:
        deduped[(s["scan"], s.get("mls"))] = s
    sightings = sorted(deduped.values(), key=lambda s: s["scan"])

    scan_order = sorted(set(all_scans or []))
    events: list[dict[str, Any]] = []
    prev = None
    for s in sightings:
        if prev is None:
            events.append({"date": s["scan"], "type": "APPEARED",
                           "mls": s["mls"], "asking": s["asking"]})
        else:
            if scan_order and prev["scan"] in scan_order:
                idx = scan_order.index(prev["scan"])
                if s["scan"] in scan_order and scan_order.index(s["scan"]) > idx + 1:
                    events.append({"date": s["scan"], "type": "REAPPEARED",
                                   "mls": s["mls"], "asking": s["asking"],
                                   "missed_scans": scan_order[idx + 1:scan_order.index(s["scan"])]})
            if s["mls"] and prev["mls"] and s["mls"] != prev["mls"]:
                events.append({"date": s["scan"], "type": "RELISTED",
                               "old_mls": prev["mls"], "mls": s["mls"],
                               "old_asking": prev["asking"], "asking": s["asking"]})
            if s["asking"] and prev["asking"] and s["asking"] != prev["asking"]:
                delta = s["asking"] - prev["asking"]
                events.append({
                    "date": s["scan"],
                    "type": "PRICE_DROP" if delta < 0 else "PRICE_UP",
                    "mls": s["mls"],
                    "old_asking": prev["asking"], "asking": s["asking"],
                    "delta": delta,
                })
        if s["mls"] or s["asking"]:
            prev = s
    last = sightings[-1]
    disappeared = last["scan"] < latest_scan
    status = "ACTIVE"
    if disappeared:
        events.append({"date": latest_scan, "type": "DISAPPEARED",
                       "last_seen": last["scan"], "last_mls": last["mls"],
                       "last_asking": last["asking"]})
        status = "OFF_MARKET_CHECK" if covered else "OUT_OF_COVERAGE"
    return {
        "address_key": address_key,
        "sightings": sightings,
        "events": events,
        "status": status,
        "latest_mls": last["mls"],
        "latest_asking": last["asking"],
        "latest_source_url": last.get("source_url"),
        "first_seen": sightings[0]["scan"],
        "last_seen": last["scan"],
        "sold": None,
    }


def apply_tier(entry: dict[str, Any], decision: str | None) -> None:
    entry["tier"] = decision if decision in TIER_ORDER else None


def record_outcome(entry: dict[str, Any], outcome: dict[str, Any]) -> None:
    """Attach a resolved outcome to a DISAPPEARED entry."""
    entry["sold"] = outcome
    otype = outcome.get("type")
    if otype == "SOLD":
        entry["status"] = "SOLD"
    elif otype == "UNRESOLVED":
        entry["status"] = "OUTCOME_UNRESOLVED"
    elif otype == "STILL_LISTED":
        # Scan coverage gap — the listing is live but outside the successful
        # presets' reach.  Keep it ACTIVE so it stays on the radar.
        entry["status"] = "STILL_LISTED"
    else:
        entry["status"] = "EXPIRED_OR_WITHDRAWN"


def reconcile_watchlist(
    scans: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    latest_scan: str,
    tiers: Mapping[str, str] | None = None,
    coverage: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Merge all scans into the watchlist.

    ``scans``: scan_date -> iterable of card-like dicts with at least
    ``address``; ``mls_number``/``mls``, ``asking``, ``listing_status`` and
    ``source_url`` are used when present.
    """
    by_key: dict[str, list[dict[str, Any]]] = {}
    address_display: dict[str, str] = {}
    for scan_date in sorted(scans):
        for card in scans[scan_date]:
            addr = card.get("address") or ""
            key = normalize_address_key(addr)
            if not key:
                continue
            by_key.setdefault(key, []).append(sighting(
                scan=scan_date,
                mls=card.get("mls_number") or card.get("mls"),
                asking=card.get("asking") or card.get("asking_price"),
                status=card.get("listing_status") or "active",
                source_url=card.get("source_url"),
            ))
            address_display.setdefault(key, addr)
    entries = []
    for key, seen in by_key.items():
        entry = reconcile_entry(
            key, seen, latest_scan=latest_scan, all_scans=sorted(scans),
            covered=(coverage or {}).get(key, True),
        )
        entry["address"] = address_display[key]
        apply_tier(entry, (tiers or {}).get(key) or (tiers or {}).get(address_display[key]))
        entries.append(entry)
    entries.sort(key=lambda e: (-(TIER_ORDER.get(e.get("tier")) or 0), e["address_key"]))
    return {
        "latest_scan": latest_scan,
        "property_count": len(entries),
        "entries": entries,
    }


def weekly_changes(watchlist: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Bucket events emitted on the latest scan date for reporting."""
    latest = watchlist["latest_scan"]
    buckets: dict[str, list[dict[str, Any]]] = {
        "sold": [], "removed": [], "still_listed_missed": [], "disappeared": [],
        "price_drop": [], "price_up": [], "relisted_same_price": [], "new": [],
    }
    for e in watchlist["entries"]:
        recent = [ev for ev in e["events"] if ev["date"] == latest]
        ev_types = {ev["type"] for ev in recent}
        item = {"address": e.get("address"), "tier": e.get("tier"),
                "status": e["status"], "events": recent,
                "asking": e.get("latest_asking"), "mls": e.get("latest_mls"),
                "sold": e.get("sold")}
        if e["status"] == "SOLD":
            buckets["sold"].append(item)
        elif e["status"] == "STILL_LISTED":
            buckets["still_listed_missed"].append(item)
        elif e["status"] == "EXPIRED_OR_WITHDRAWN":
            buckets["removed"].append(item)
        elif "DISAPPEARED" in ev_types or e["status"] in (
            "OFF_MARKET_CHECK", "OUTCOME_UNRESOLVED",
        ):
            buckets["disappeared"].append(item)
        if "PRICE_DROP" in ev_types:
            buckets["price_drop"].append(item)
        elif "PRICE_UP" in ev_types:
            buckets["price_up"].append(item)
        elif "RELISTED" in ev_types:
            buckets["relisted_same_price"].append(item)
        if "APPEARED" in ev_types:
            buckets["new"].append(item)
    return buckets


def render_watchlist_markdown(watchlist: Mapping[str, Any]) -> str:
    changes = weekly_changes(watchlist)
    lines = [f"# Watchlist — {watchlist['latest_scan']}", "",
             f"Tracked properties: {watchlist['property_count']}", ""]

    def _rows(items: list[dict[str, Any]], *, price_col: str = "asking") -> None:
        lines.append("| 地址 | Tier | 状态 | 价格/事件 |")
        lines.append("|---|---|---|---|")
        for it in items:
            ev = it["events"][-1] if it["events"] else {}
            detail = ""
            if ev.get("type") in ("PRICE_DROP", "PRICE_UP"):
                detail = f"${ev.get('old_asking'):,} → ${ev.get('asking'):,} ({ev.get('delta'):+,})"
            elif it.get("sold") and it["sold"].get("price"):
                detail = f"SOLD ${it['sold']['price']:,}"
            elif it.get("asking"):
                detail = f"${it['asking']:,}"
            lines.append(f"| {it['address']} | {it.get('tier') or '—'} | {it['status']} | {detail} |")
        lines.append("")

    for bucket, title in (("sold", "成交 (SOLD)"), ("price_drop", "降价重挂 / 降价"),
                          ("disappeared", "消失待查"), ("removed", "下架/撤回"),
                          ("still_listed_missed", "扫描漏抓（仍在售）"),
                          ("relisted_same_price", "平价重挂"),
                          ("price_up", "涨价重挂"), ("new", "新出现")):
        items = changes[bucket]
        if not items:
            continue
        lines.append(f"## {title} — {len(items)}")
        lines.append("")
        _rows(items)
    return "\n".join(lines)


def load_watchlist(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_watchlist(watchlist: Mapping[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(watchlist, ensure_ascii=False, indent=2))
    return p


def merge_watchlist(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve resolved outcomes and tier history when a fresh reconcile
    runs: a SOLD/resolved entry stays resolved even though it keeps
    'disappearing' from subsequent scans."""
    if not old:
        return new
    old_by_key = {e["address_key"]: e for e in old.get("entries", [])}
    for entry in new["entries"]:
        prev = old_by_key.get(entry["address_key"])
        if not prev:
            continue
        if prev.get("sold") and prev["sold"].get("type") in ("SOLD", "EXPIRED", "WITHDRAWN"):
            if not any(ev["type"] in ("APPEARED", "REAPPEARED") for ev in entry["events"]
                       if ev["date"] == new["latest_scan"]):
                entry["sold"] = prev["sold"]
                entry["status"] = prev["status"]
                # dedupe repeated DISAPPEARED events on resolved entries
                entry["events"] = [ev for ev in entry["events"]
                                   if ev["type"] != "DISAPPEARED" or ev["date"] != new["latest_scan"]]
        if prev.get("tier") and not entry.get("tier"):
            entry["tier"] = prev["tier"]
    return new
