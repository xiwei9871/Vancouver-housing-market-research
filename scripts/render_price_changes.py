#!/usr/bin/env python3
"""ACTIVE 房源要价变动专审表 — 价格历史 + V3s 现状 + 门检 + 卡片链接。

用途: 要价变动后重新人工判断 tier(PASS/WATCH/KEEP)。
只读 watchlist/V3s/异常表,不改任何估值。
"""
import csv, glob, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports/watchlist/price-changes-active-2026-10-23.html"
OUTJ = OUT.with_suffix(".json")
V3S = json.loads((ROOT / "data/research/normalized/"
                  "valuation-v3s-shadow-2026-10-22.json").read_text())
WATCH = json.loads((ROOT / "data/research/watchlist/"
                    "property-watchlist.json").read_text())
SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "REVIEW": 2, "INFO": 1, "PASS": 0}

anom = {}
for r in csv.DictReader(open(
        ROOT / "reports/price_consistency_audit/"
             "valuation_integrity_anomalies.csv")):
    k = r["listing_id"]
    cur = anom.setdefault(k, {"sev": "PASS", "kinds": set()})
    cur["kinds"].add(r["check_type"])
    if SEV_RANK.get(r["severity"], 0) > SEV_RANK.get(cur["sev"], 0):
        cur["sev"] = r["severity"]

CARD_DIRS = sorted(glob.glob(str(ROOT / "reports/calibration/*/cards")),
                   reverse=True)

def card_link(mls):
    if not mls:
        return None
    for d in CARD_DIRS:
        if (ROOT / d / f"mls-{mls}.html").exists():
            return f"../{d.split('reports/')[1]}/mls-{mls}.html"
    return None

def fm(n):
    return f"${n/1e6:.2f}M" if n and abs(n) >= 1e6 else (f"${n/1e3:.0f}K" if n else "—")

def rec(tag, **kw):
    return tag if kw.get("on") else None

rows = []
v3s = {s["address"]: s for s in V3S["sites"]}
for e in WATCH["entries"]:
    addr = e["address"]
    site = v3s.get(addr)
    if not site or site.get("status") != "ACTIVE":
        continue
    ps = [(s.get("scan"), s.get("asking")) for s in e.get("sightings", [])
          if s.get("asking")]
    if len({p for _, p in ps}) < 2:
        continue
    first, latest = ps[0], ps[-1]
    delta = latest[1] - first[1]
    pct = delta / first[1]
    g = anom.get(addr, {"sev": "PASS", "kinds": set()})
    br = site.get("best_ref") or {}
    drops = [ev for ev in e.get("events", []) if ev["type"] == "PRICE_DROP"]
    ups = [ev for ev in e.get("events", []) if ev["type"] == "PRICE_UP"]
    sugg = ("降价>10%,建议 tier 复核→WATCH/KEEP" if pct <= -0.10 else
            "降价5-10%,复核" if pct <= -0.05 else
            "涨价,复核" if pct > 0 else "小幅变动,复核")
    rows.append({
        "addr": addr, "comm": site.get("comm"), "zone": site.get("zone"),
        "lot": site.get("lot"), "tier": site.get("tier"),
        "ask": site["ask"], "first_p": first[1], "first_d": first[0],
        "latest_p": latest[1], "latest_d": latest[0],
        "delta": delta, "pct": pct, "drops": len(drops), "ups": len(ups),
        "sev": g["sev"], "kinds": "·".join(sorted(g["kinds"]))[:60],
        "prod": br.get("product"), "roi": br.get("roi"),
        "roi_m5": br.get("roi_m5"), "roi_c10": br.get("roi_c10"),
        "card": card_link(e.get("latest_mls")),
        "realtor": e.get("latest_source_url"), "sugg": sugg,
        "hist": " → ".join(fm(p) for _, p in ps),
        "v3s_eq": site["ask"] == latest[1],
    })
rows.sort(key=lambda r: r["pct"])

TD = "padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top"
def tr(r):
    sev_c = {"CRITICAL": "#c00", "HIGH": "#d60", "REVIEW": "#90a",
             "INFO": "#36c", "PASS": "#2a2"}[r["sev"]]
    dcls = "#c00" if r["delta"] < 0 else "#080"
    mismatch = (' <b style="color:#c00">V3s≠最新!</b>' if not r["v3s_eq"] else "")
    return f"""<tr>
<td style="{TD}"><a href="{r['realtor']}" target="_blank">{r['addr']}</a>
  {f'<br><a href="{r["card"]}">校准卡</a>' if r['card'] else ''}
  <br><span style="color:#666;font-size:12px">{r['comm']}·{r['zone']}·{r['lot']:,.0f}sf</span></td>
<td style="{TD}"><b style="color:{dcls}">{fm(r['delta'])} ({r['pct']:+.1%})</b>
  <br>{r['first_d']}:{fm(r['first_p'])}→{r['latest_d']}:{fm(r['latest_p'])}
  <br><span style="color:#666;font-size:12px">{r['hist']} · 降{r['drops']}次/升{r['ups']}次{mismatch}</span></td>
<td style="{TD}">{fm(r['ask'])}<br><span style="color:#666;font-size:12px">{r['tier']}</span></td>
<td style="{TD}">{r['prod'] or '—'} ROI <b>{r['roi'] and f"{r['roi']:+.0%}" or '—'}</b>
  <br><span style="color:#666;font-size:12px">压测 售-10%:{r['roi_m5'] and f"{r['roi_m5']:+.0%}" or '—'} 建安+10%:{r['roi_c10'] and f"{r['roi_c10']:+.0%}" or '—'}</span></td>
<td style="{TD}"><b style="color:{sev_c}">{r['sev']}</b>
  <br><span style="color:#666;font-size:12px">{r['kinds']}</span></td>
<td style="{TD};font-size:13px">{r['sugg']}</td></tr>"""

html = f"""<!doctype html><meta charset="utf-8"><title>ACTIVE 要价变动专审 2026-10-23</title>
<style>body{{font:14px/1.5 -apple-system,"PingFang SC",sans-serif;margin:20px}}
table{{border-collapse:collapse;width:100%}}th{{text-align:left;padding:6px 8px;border-bottom:2px solid #333;background:#f6f6f6;position:sticky;top:0}}</style>
<h2>ACTIVE 房源要价变动专审 — {len(rows)} 套</h2>
<p><a href="price-changes-active-2026-10-23.json" download
   style="background:#0b57d0;color:#fff;padding:4px 12px;border-radius:4px;text-decoration:none">导出全部 JSON</a></p>
<p>口径:watchlist 多期 sightings 中 ≥2 个不同要价 ∩ V3s ACTIVE。
V3s/ROI 已用 <b>latest_asking</b>(见V3s≠最新标记,无标记即一致)。
<b>降价后是复核机会,不是自动改 tier</b>——本表供人工重判 PASS/WATCH/KEEP。</p>
<table><tr><th>房源/链接</th><th>要价变动</th><th>V3s ASK·tier</th>
<th>最优方案 ROI</th><th>门检</th><th>建议</th></tr>
{''.join(tr(r) for r in rows)}</table>
<p style="color:#888;font-size:12px">生成: render_price_changes.py ·
输入: watchlist(最近扫描 2026-10-20) + V3s-2026-10-22 + gate anomalies</p>"""
OUT.write_text(html)
OUTJ.write_text(json.dumps(
    {"generated": "2026-10-23", "snapshot": "V3s-2026-10-22",
     "criteria": "watchlist sightings≥2 distinct askings ∩ V3s ACTIVE",
     "rows": rows}, ensure_ascii=False, indent=1))
print(f"{len(rows)} rows → {OUT} + .json")
for r in rows[:10]:
    print(f"  {r['pct']:+.1%} {r['addr']:28} {r['hist']} | ROI {r['roi'] and format(r['roi'],'+.0%')} | {r['sev']}")
