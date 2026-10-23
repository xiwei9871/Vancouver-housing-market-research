#!/usr/bin/env python3
"""挂牌生命周期事件链 — 纯函数(无私有数据依赖)。
sightings: [{scan, asking, mls}]
输出完整事件链:LISTED / PRICE_DROP / PRICE_INCREASE / RELISTED /
UNKNOWN_CHANGE;支持多次撤牌重挂、价格回弹、中间事件保留。
"""
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def build_event_chain(sightings, all_scans=None):
    """返回事件链 dict。无效/缺失日期与缺失 MLS 均显式标注。

    all_scans: 全量扫描日历(sorted date list)。提供后,同一 MLS
    且中间存在缺失扫描 → REAPPEARED(挂牌行为异常:可能是暂时下架、
    采集可见性变化等;不等于已证明撤牌,不得据此断言 DOM 重置)。
    每个价格事件带统一 label:RELISTED_HIGHER/RELISTED_LOWER/
    RELISTED_SAME/REAPPEARED_WITH_HIGHER_ASK/..._LOWER_ASK/
    ..._SAME_ASK/PRICE_INCREASE/PRICE_DROP/UNKNOWN_CHANGE。
    """
    def skey(s):
        sc = s.get("scan") or ""
        return sc if DATE_RE.match(sc) else "9999-99-99"

    sgl = sorted((s for s in sightings if s.get("asking")), key=skey)
    events, prev = [], None
    invalid_dates = sum(1 for s in sgl
                        if not DATE_RE.match(s.get("scan") or ""))
    for s in sgl:
        ask, mls = s["asking"], (s.get("mls") or "").strip()
        sc = s.get("scan") or ""
        if prev is None:
            events.append({"type": "LISTED", "date": sc,
                           "ask": ask, "mls": mls})
        elif mls and prev["mls"] and mls != prev["mls"]:
            d = ask - prev["ask"]
            events.append({
                "type": "RELISTED", "date": sc,
                "sub": ("RELIST_LOWER" if d < 0 else
                        "RELIST_HIGHER" if d > 0 else "RELIST_SAME"),
                "from_mls": prev["mls"], "to_mls": mls,
                "from": prev["ask"], "to": ask, "delta": d})
        elif not mls or not prev["mls"]:
            if ask != prev["ask"]:
                events.append({"type": "UNKNOWN_CHANGE", "date": sc,
                               "from": prev["ask"], "to": ask,
                               "delta": ask - prev["ask"],
                               "prev_mls": prev["mls"], "mls": mls})
        else:
            missed = [x for x in (all_scans or [])
                      if prev["date"] < x < sc] or []
            if ask != prev["ask"] and missed:
                events.append({
                    "type": "REAPPEARED", "date": sc, "mls": mls,
                    "sub": ("REAPPEAR_LOWER" if ask < prev["ask"] else
                            "REAPPEAR_HIGHER"),
                    "missed_scans": missed,
                    "from": prev["ask"], "to": ask,
                    "delta": ask - prev["ask"]})
            elif missed:
                events.append({
                    "type": "REAPPEARED", "date": sc, "mls": mls,
                    "sub": "REAPPEAR_SAME", "missed_scans": missed,
                    "from": prev["ask"], "to": ask, "delta": 0})
            elif ask < prev["ask"]:
                events.append({"type": "PRICE_DROP", "date": sc,
                               "mls": mls, "from": prev["ask"],
                               "to": ask, "delta": ask - prev["ask"]})
            elif ask > prev["ask"]:
                events.append({"type": "PRICE_INCREASE", "date": sc,
                               "mls": mls, "from": prev["ask"],
                               "to": ask, "delta": ask - prev["ask"]})
        prev = {"mls": mls, "ask": ask, "date": sc}
    priced = [e for e in events
              if e["type"] in ("PRICE_DROP", "PRICE_INCREASE",
                               "RELISTED", "REAPPEARED",
                               "UNKNOWN_CHANGE")]
    LABEL = {"RELISTED": {"RELIST_HIGHER": "RELISTED_HIGHER",
                          "RELIST_LOWER": "RELISTED_LOWER",
                          "RELIST_SAME": "RELISTED_SAME"},
             "REAPPEARED": {"REAPPEAR_HIGHER":
                            "REAPPEARED_WITH_HIGHER_ASK",
                            "REAPPEAR_LOWER":
                            "REAPPEARED_WITH_LOWER_ASK",
                            "REAPPEAR_SAME":
                            "REAPPEARED_WITH_SAME_ASK"}}
    for e in events:
        e["label"] = LABEL.get(e["type"], {}).get(e.get("sub"),
                                                e["type"])
    first_ask = sgl[0]["asking"] if sgl else None
    last_ask = sgl[-1]["asking"] if sgl else None
    return {
        "events": events, "price_events": priced,
        "first_ask": first_ask, "last_ask": last_ask,
        "cumulative": (last_ask - first_ask)
        if first_ask is not None else 0,
        "n_drops": sum(1 for e in priced
                       if e["type"] == "PRICE_DROP"),
        "n_increases": sum(1 for e in priced
                           if e["type"] == "PRICE_INCREASE"),
        "n_relists": sum(1 for e in priced if e["type"] == "RELISTED"),
        "n_reappears": sum(1 for e in priced
                           if e["type"] == "REAPPEARED"),
        "n_unknown": sum(1 for e in priced
                         if e["type"] == "UNKNOWN_CHANGE"),
        "invalid_dates": invalid_dates,
        "latest_event": priced[-1] if priced else None,
        "current_mls": sgl[-1].get("mls") if sgl else "",
    }
