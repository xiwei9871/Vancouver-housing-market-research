#!/usr/bin/env python3
"""价格变化事实规范化 — 幂等。

watchlist `events` 是原始追加日志(同一变化可能同时有 RELISTED +
PRICE_UP 两条,重复计数风险)。本脚本为每个 entry 生成
`price_change_events` 规范层:一条经济价格变化只计一次,
RELISTED/REAPPEARED 作为 mechanism 属性保留;
REAPPEARED 是挂牌行为异常标注,不代表已证明撤牌或 DOM 重置。

- 原始 events 不删不改(血缘保留)
- 重复运行不产生差异(幂等)
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import price_events  # noqa: E402

WF = ROOT / "data/research/watchlist/property-watchlist.json"


def build(w):
    all_sc = sorted({s["scan"] for e in w["entries"]
                     for s in e.get("sightings", []) if s.get("scan")})
    n_with, n_ev, legacy_dups = 0, 0, 0
    for e in w["entries"]:
        ch = price_events.build_event_chain(e.get("sightings", []),
                                            all_scans=all_sc)
        canon = []
        for ev in ch["price_events"]:
            canon.append({
                "date": ev["date"], "label": ev["label"],
                "type": ev["type"], "sub": ev.get("sub"),
                "from": ev.get("from", ev.get("ask")),
                "to": ev.get("to"), "delta": ev.get("delta"),
                "mls": ev.get("mls") or ev.get("to_mls"),
                "from_mls": ev.get("from_mls"),
                "missed_scans": ev.get("missed_scans")})
        e["price_change_events"] = canon
        if canon:
            n_with += 1
            n_ev += len(canon)
        # 原始日志重复计数审计:同日 RELISTED+PRICE_UP / REAPPEARED+PRICE_*
        raw = [x for x in e.get("events", [])
               if x.get("type") in ("RELISTED", "REAPPEARED",
                                    "PRICE_UP", "PRICE_DROP",
                                    "PRICE_INCREASE")]
        by_day = {}
        for x in raw:
            by_day.setdefault((x.get("date") or "")[:10],
                              []).append(x["type"])
        for day, types in by_day.items():
            if (len(types) > len(set(types)) or
                    ({"RELISTED", "REAPPEARED"} & set(types) and
                     {"PRICE_UP", "PRICE_DROP",
                      "PRICE_INCREASE"} & set(types))):
                legacy_dups += 1
    return {"scan_dates": all_sc, "entries_with_events": n_with,
            "canonical_events": n_ev,
            "legacy_duplicate_pairs": legacy_dups}


def main():
    w = json.loads(WF.read_text())
    stats = build(w)
    canon_json = json.dumps(
        [e.get("price_change_events") for e in w["entries"]],
        sort_keys=True)
    if WF.exists():
        old = json.loads(WF.read_text())
        same = canon_json == json.dumps(
            [e.get("price_change_events") for e in old["entries"]],
            sort_keys=True)
    else:
        same = False
    if not same:
        WF.write_text(json.dumps(w, indent=2, ensure_ascii=False))
        print("watchlist 已更新 price_change_events")
    else:
        print("watchlist 无变化(幂等)")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
