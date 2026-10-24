#!/usr/bin/env python3
"""price_events 纯函数回归 — 4 个真实案例镜像 + 边界;无私有数据可跑。
若本地 watchlist 存在,额外做真实记录回归。
"""
import json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import price_events as PE                         # noqa: E402

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)

S = lambda scan, ask, mls: {"scan": scan, "asking": ask, "mls": mls}

# F1 4940 KILLARNEY:换 MLS 降价重挂 — 不得显示为涨价
c1 = PE.build_event_chain([
    S("2026-09-05", 1399000, "R3045627"),
    S("2026-10-14", 1099000, "R3173056"),
    S("2026-10-20", 1099000, "R3173056")])
chk("F1 唯一事件=RELISTED",
    [e["type"] for e in c1["price_events"]] == ["RELISTED"])
chk("F1 重挂降价", c1["price_events"][0]["sub"] == "RELIST_LOWER"
    and c1["price_events"][0]["delta"] == -300000)
chk("F1 累计 -300k", c1["cumulative"] == -300000)

# F2 3326 SLOCAN:同 MLS 真涨价
c2 = PE.build_event_chain([
    S("2026-09-20", 1498800, "R3166617"),
    S("2026-10-20", 1648800, "R3166617")])
chk("F2 PRICE_INCREASE",
    c2["price_events"][0]["type"] == "PRICE_INCREASE"
    and c2["price_events"][0]["delta"] == 150000)

# F3 3184 E 8TH:换 MLS 涨价重挂
c3 = PE.build_event_chain([
    S("2026-09-20", 1199900, "R3167057"),
    S("2026-10-05", 1359900, "R3171659"),
    S("2026-10-14", 1359900, "R3171659"),
    S("2026-10-20", 1359900, "R3171659")])
chk("F3 RELIST_HIGHER", c3["price_events"][0]["sub"] == "RELIST_HIGHER")

# F4 614 E 19TH:同 MLS 两次连降 — 中间事件必须保留
c4 = PE.build_event_chain([
    S("2026-09-20", 1498000, "R3166059"),
    S("2026-10-14", 1398000, "R3166059"),
    S("2026-10-20", 1298000, "R3166059")])
chk("F4 两次 PRICE_DROP 都保留",
    [e["type"] for e in c4["price_events"]] ==
    ["PRICE_DROP", "PRICE_DROP"])
chk("F4 累计 -200k", c4["cumulative"] == -200000)
chk("F4 最近事件 = 第二次降", c4["latest_event"]["delta"] == -100000)

# 边界:多次撤牌重挂 + 回到原价
c5 = PE.build_event_chain([
    S("2026-01-01", 2000000, "R1"),
    S("2026-02-01", 1900000, "R1"),
    S("2026-03-01", 2000000, "R2"),
    S("2026-04-01", 2000000, "R3"),
    S("2026-05-01", 1800000, "R3")])
chk("F5 全链保留 5 事件",
    [e["type"] for e in c5["price_events"]] ==
    ["PRICE_DROP", "RELISTED", "RELISTED", "PRICE_DROP"])
chk("F5 回原价重挂 RELIST_HIGHER",
    c5["price_events"][1]["sub"] == "RELIST_HIGHER")
chk("F5 原价重挂 RELIST_SAME",
    c5["price_events"][2]["sub"] == "RELIST_SAME")
chk("F5 累计 -200k(非首尾误判0)",
    c5["cumulative"] == -200000 and c5["latest_event"]["delta"] == -200000)

# 边界:价格先降后升回原值 — 首尾价相同但中间有事件
c6 = PE.build_event_chain([
    S("2026-01-01", 1500000, "R1"),
    S("2026-02-01", 1400000, "R1"),
    S("2026-03-01", 1500000, "R1")])
chk("F6 首尾同价仍有 2 事件",
    len(c6["price_events"]) == 2 and c6["cumulative"] == 0)

# 边界:缺 MLS → UNKNOWN_CHANGE
c7 = PE.build_event_chain([
    S("2026-01-01", 1000000, ""),
    S("2026-02-01", 900000, "")])
chk("F7 无 MLS → UNKNOWN_CHANGE",
    c7["price_events"][0]["type"] == "UNKNOWN_CHANGE")
chk("F7 n_unknown=1", c7["n_unknown"] == 1)

# 边界:乱序 sightings 按 scan 纠正
c8 = PE.build_event_chain([
    S("2026-10-14", 1099000, "R3173056"),
    S("2026-09-05", 1399000, "R3045627")])
chk("F8 乱序纠正为 RELIST_LOWER",
    c8["price_events"][0]["sub"] == "RELIST_LOWER"
    and c8["first_ask"] == 1399000)

# 真实 watchlist 回归(存在则跑,否则 SKIPPED)
wf = ROOT / "data/research/watchlist/property-watchlist.json"
if wf.exists():
    w = json.loads(wf.read_text())
    truth = {
        "4940 KILLARNEY STREET": ("RELISTED", "RELIST_LOWER"),
        "3326 SLOCAN STREET": ("PRICE_INCREASE", None),
        "3184 E 8TH AVENUE": ("RELISTED", "RELIST_HIGHER"),
        "614 E 19TH AVENUE": ("PRICE_DROP", None)}
    for e in w["entries"]:
        if e["address"] in truth:
            ch = PE.build_event_chain(e["sightings"])
            et, sub = truth[e["address"]]
            chk(f"真案 {e['address']}={et}",
                ch["price_events"][0]["type"] == et and
                (sub is None or ch["price_events"][0]["sub"] == sub))
    # 全量审计 csv(公开要价数据)
    out = ROOT / "data/research/normalized/audit_v1_2_2"
    out.mkdir(parents=True, exist_ok=True)
    import csv
    rows = []
    for e in w["entries"]:
        ch = PE.build_event_chain(e.get("sightings", []))
        if not ch["price_events"]:
            continue
        rows.append({
            "address": e["address"],
            "n_events": len(ch["price_events"]),
            "event_types": "|".join(x["type"]
                                    for x in ch["price_events"]),
            "first_ask": ch["first_ask"], "last_ask": ch["last_ask"],
            "cumulative": ch["cumulative"],
            "n_drops": ch["n_drops"], "n_increases": ch["n_increases"],
            "n_relists": ch["n_relists"], "n_unknown": ch["n_unknown"],
            "latest_type": ch["latest_event"]["type"],
            "latest_delta": ch["latest_event"]["delta"],
            "latest_date": ch["latest_event"]["date"],
            "invalid_dates": ch["invalid_dates"]})
    with open(out / "price_event_chain_audit.csv", "w",
              newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=list(rows[0]))
        wcsv.writeheader(); wcsv.writerows(rows)
    chk(f"审计 csv 生成 {len(rows)} 行", len(rows) > 0)
else:
    print("SKIP real-watchlist regression(无私有数据)")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
