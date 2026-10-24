#!/usr/bin/env python3
"""挂牌生命周期事件链 — 纯函数(无私有数据依赖)。
sightings: [{scan, asking, mls}]
输出完整事件链:LISTED / PRICE_DROP / PRICE_INCREASE / RELISTED /
UNKNOWN_CHANGE;支持多次撤牌重挂、价格回弹、中间事件保留。
"""
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def build_event_chain(sightings):
    """返回事件链 dict。无效/缺失日期与缺失 MLS 均显式标注。"""
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
        elif ask < prev["ask"]:
            events.append({"type": "PRICE_DROP", "date": sc, "mls": mls,
                           "from": prev["ask"], "to": ask,
                           "delta": ask - prev["ask"]})
        elif ask > prev["ask"]:
            events.append({"type": "PRICE_INCREASE", "date": sc, "mls": mls,
                           "from": prev["ask"], "to": ask,
                           "delta": ask - prev["ask"]})
        prev = {"mls": mls, "ask": ask}
    priced = [e for e in events
              if e["type"] in ("PRICE_DROP", "PRICE_INCREASE",
                               "RELISTED", "UNKNOWN_CHANGE")]
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
        "n_unknown": sum(1 for e in priced
                         if e["type"] == "UNKNOWN_CHANGE"),
        "invalid_dates": invalid_dates,
        "latest_event": priced[-1] if priced else None,
        "current_mls": sgl[-1].get("mls") if sgl else "",
    }
