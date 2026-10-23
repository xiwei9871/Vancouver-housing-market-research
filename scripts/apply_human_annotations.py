#!/usr/bin/env python3
"""人工策略标注 → watchlist tier — 幂等应用 + 核销表输出。

标注按 property_key(mls:Rxxxx)记录;重挂后同一地址可有多个
标注身份(新旧 MLS 各一条)。权威规则:
  1. 标注 key 的 MLS == 该房源当前 MLS → 该条为权威判断;
  2. 否则取标注文件日期最新的一条。
TIER_REVIEW 事件规范化:每条 entry 至多保留"首个自动化前 tier →
权威决定"的一条;pre==final 则全部移除(零变化不记噪音)。
重复运行结果稳定(幂等)。V3s 冻结估值文件不动。
"""
import csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANN = ROOT / "data/research/annotations"
WF = ROOT / "data/research/watchlist/property-watchlist.json"
OUT = ROOT / "docs/audits/strategy_annotations_2026_10_23"
sys.path.insert(0, str(ROOT / "scripts"))
import price_events  # noqa: E402


def all_annotations():
    """address -> [(date, dec, file, key, reason)], 全部批次。"""
    out = {}
    for f in sorted(ANN.glob("strategy-v2-human-annotations-*.json")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if not m:
            continue
        d = m.group(1)
        rows = json.loads(f.read_text())
        rows = rows["annotations"] if isinstance(rows, dict) else rows
        for r in rows:
            a = (r.get("address") or "").strip().upper()
            dec = r.get("human_decision")
            if not a or dec not in ("KEEP", "WATCH", "PASS"):
                continue
            out.setdefault(a, []).append(
                {"date": d, "dec": dec, "file": f.name,
                 "key": r.get("property_key", ""),
                 "reason": r.get("human_reason") or
                 r.get("human_notes") or ""})
    return out


def key_mls(key):
    m = re.search(r"(R\d+)", key or "")
    return m.group(1) if m else None


def pick_authoritative(recs, cur_mls):
    """mls 匹配优先,再按文件日期最新。"""
    matched = [r for r in recs if key_mls(r["key"]) == cur_mls]
    pool = matched if matched else recs
    return max(pool, key=lambda r: (r["date"], r["key"])), matched


def normalize_tier_events(e, pre_tier, final, auth):
    """重写该 entry 的 TIER_REVIEW 为至多一条规范事件。"""
    others = [x for x in e.get("events", [])
              if x.get("type") != "TIER_REVIEW"]
    e["events"] = others
    if pre_tier != final:
        e["events"].append({
            "date": auth["date"], "type": "TIER_REVIEW",
            "old_tier": pre_tier, "tier": final,
            "reason": auth["reason"], "annotated_at": auth["date"],
            "source": auth["file"]})


def apply(w, ann):
    changes = []
    for e in w["entries"]:
        a = e["address"].strip().upper()
        recs = ann.get(a)
        if not recs:
            continue
        ch = price_events.build_event_chain(e.get("sightings", []))
        cur_mls = ch["current_mls"]
        auth, matched = pick_authoritative(recs, cur_mls)
        # 自动化前 tier = 首个 TIER_REVIEW 的 old_tier,否则当前值
        trs = [x for x in e.get("events", [])
               if x.get("type") == "TIER_REVIEW"]
        pre = trs[0].get("old_tier") if trs else e.get("tier")
        final = auth["dec"]
        normalize_tier_events(e, pre, final, auth)
        if e.get("tier") != final:
            e["tier"] = final
        if pre != final or not matched:
            changes.append({"address": e["address"],
                            "pre_tier": pre, "final_tier": final,
                            "cur_mls": cur_mls,
                            "auth_key": auth["key"],
                            "auth_file": auth["file"],
                            "mls_matched": bool(matched),
                            "n_keys": len({r["key"] for r in recs})})
    return changes


def main():
    w = json.loads(WF.read_text())
    ann = all_annotations()
    changes = apply(w, ann)
    WF.write_text(json.dumps(w, indent=2, ensure_ascii=False))
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for e in w["entries"]:
        for x in e.get("events", []):
            if x.get("type") == "TIER_REVIEW":
                rows.append({"address": e["address"],
                             "old_tier": x.get("old_tier"),
                             "new_tier": x.get("tier"),
                             "reason": x.get("reason", ""),
                             "annotated_at": x.get("annotated_at"),
                             "source": x.get("source")})
    if rows:
        with open(OUT / "tier_change_reconciliation.csv", "w",
                  newline="") as f:
            c = csv.DictWriter(f, fieldnames=list(rows[0]))
            c.writeheader()
            c.writerows(rows)
    print(f"entries_annotated={sum(1 for e in w['entries'] if ann.get(e['address'].strip().upper()))} "
          f"tier对齐+冲突={len(changes)} TIER_REVIEW={len(rows)}")
    for c in changes:
        print("  ", c["address"], f"{c['pre_tier']}→{c['final_tier']}",
              f"key={c['auth_key']} mlsMatch={c['mls_matched']}",
              f"keys={c['n_keys']}")


if __name__ == "__main__":
    main()
