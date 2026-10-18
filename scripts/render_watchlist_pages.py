#!/usr/bin/env python3
"""Render watchlist study pages (SOLD outcomes + price drops) as static HTML.

Data comes entirely from already-persisted artifacts — no fetching:

- data/research/watchlist/property-watchlist.json
- data/research/normalized/regional-official-enrichment-*.json
- data/research/annotations/strategy-v2-human-annotations-*.json
- existing calibration card HTML for deep links

Usage:
    .venv/bin/python scripts/render_watchlist_pages.py
"""

from __future__ import annotations

import glob
import html
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = ROOT / "data/research/watchlist/property-watchlist.json"
ANNOTATIONS_PATH = ROOT / "data/research/annotations/strategy-v2-human-annotations-2026-10-05.json"
OUT_DIR = ROOT / "reports/watchlist"

CARD_DIRS = sorted(
    (
        str(Path(d).relative_to(ROOT))
        for d in glob.glob(str(ROOT / "reports/calibration/*/cards"))
    ),
    reverse=True,  # newest batch first so relisted MLS resolve to the latest card
)

DIM_LABELS = [
    ("human_redevelopment_band", "重建"),
    ("human_lot_band", "地块"),
    ("human_location_band", "区位"),
    ("human_price_value_band", "价格"),
    ("human_current_use_band", "自住"),
    ("human_house_reno_band", "状况"),
]

CSS = """
body{font-family:-apple-system,'SF Pro',Helvetica,Arial,sans-serif;margin:24px;color:#222;max-width:1500px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#666;font-size:13px;margin-bottom:16px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0 18px}
.stat{background:#f5f5f3;border:1px solid #e3e1dc;border-radius:8px;padding:10px 14px;font-size:13px}
.stat b{display:block;font-size:18px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid #e8e6e1;text-align:left;vertical-align:top}
th{background:#f7f6f3;position:sticky;top:0;font-size:12px;color:#555;white-space:nowrap}
tr:hover td{background:#fafaf8}
.tier-KEEP{color:#0a7a3f;font-weight:700}.tier-WATCH{color:#b46a00;font-weight:700}
.tier-PASS{color:#777}.tier-SPECIAL{color:#6b4fbb;font-weight:700}
.band{display:inline-block;padding:1px 6px;border-radius:4px;background:#eee;font-size:11px;margin:1px}
.band-b5{background:#d6ecd9}.band-b4{background:#e7f2e0}.band-b3{background:#f5efdc}.band-b2{background:#f7e2d3}.band-b1{background:#f5d5d5}
.drop{color:#b3261e;font-weight:600}
a{color:#1a56db;text-decoration:none}a:hover{text-decoration:underline}
.ev{font-size:11px;color:#666}
.flag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px}
.flag-sold{background:#d6ecd9;color:#0a7a3f;font-weight:700}
.flag-active{background:#e3ecfa;color:#1a56db}
.flag-removed{background:#eee;color:#777}
.muted{color:#999}
"""


def band_chip(v: str | None) -> str:
    if not v:
        return '<span class="muted">—</span>'
    idx = {"0-20": 1, "21-40": 2, "41-60": 3, "61-80": 4, "81-100": 5}.get(v, 3)
    return f'<span class="band band-b{idx}">{v}</span>'


def money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def load_cards() -> dict[str, dict]:
    cards = {}
    for p in sorted(glob.glob(str(ROOT / "data/research/normalized/regional-official-enrichment*.json"))):
        if ".bak." in p:
            continue
        for c in json.load(open(p)).get("candidates", []):
            if c.get("mls_number"):
                cards[f"mls:{c['mls_number']}"] = c
    return cards


def card_link(mls: str | None) -> str | None:
    if not mls:
        return None
    for d in CARD_DIRS:
        if (ROOT / d / f"mls-{mls}.html").exists():
            return f"../{d.split('reports/')[1]}/mls-{mls}.html"
    return None


def row_common(entry, card, ann) -> dict:
    raw = (card or {}).get("raw") or {}
    bca = raw.get("official_assessment_total")
    ask = entry.get("latest_asking")
    ratio = ask / bca if ask and bca else None
    lot = (card or {}).get("lot_dimensions") or (
        f"{raw.get('official_frontage_ft')}x{raw.get('official_depth_ft')} ft"
        if raw.get("official_frontage_ft") else None
    )
    days = None
    try:
        from datetime import date
        # sale window = last confirmed sighting -> the scan where it was gone
        days = (date.fromisoformat(entry["_latest_scan"]) - date.fromisoformat(entry["last_seen"])).days
    except Exception:
        pass
    return {
        "bca": bca, "ratio": ratio, "lot": lot,
        "zoning": raw.get("official_current_zoning"),
        "dom": (card or {}).get("dom"), "days": days,
        "neighbourhood": (card or {}).get("neighbourhood"),
    }


def bands_cell(ann) -> str:
    if not ann:
        return '<span class="muted">未标注</span>'
    return " ".join(
        f'<span class="ev">{lab}</span>{band_chip(ann.get(k))}' for k, lab in DIM_LABELS
    )


def page(title: str, subtitle: str, stats: list[tuple[str, str]], headers: list[str], rows: list[str]) -> str:
    stats_html = "".join(f'<div class="stat"><b>{v}</b>{k}</div>' for k, v in stats)
    head = "".join(f"<th>{h}</th>" for h in headers)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{title}</title><style>{CSS}</style></head><body>
<h1>{title}</h1><div class="sub">{subtitle}</div>
<div class="stats">{stats_html}</div>
<table><thead><tr>{head}</tr></thead><tbody>
{''.join(rows)}
</tbody></table>
<p class="sub" style="margin-top:18px">数据源：property-watchlist.json + 官方富化工件 + 人工标注库。成交价为 realtor.ca 页面状态确认；公开成交价待 BCA 销售层季度回填。</p>
</body></html>"""


def render_sold(entries, cards, anns) -> str:
    rows = []
    ratios = []
    for e in entries:
        card = cards.get("mls:" + (e.get("latest_mls") or ""), {})
        ann = anns.get("mls:" + (e.get("latest_mls") or ""), {})
        c = row_common(e, card, ann)
        if c["ratio"]:
            ratios.append(c["ratio"])
        link = card_link(e.get("latest_mls"))
        addr = html.escape(e["address"])
        addr_cell = f'<a href="{link}">{addr}</a>' if link else addr
        sightings = " → ".join(
            f"{s['scan'][5:]} {money(s['asking'])}" for s in e["sightings"] if s.get("asking")
        )
        rows.append(
            "<tr>"
            f"<td>{addr_cell}<div class='ev'>{e.get('latest_mls') or ''} · {c['neighbourhood'] or ''}</div></td>"
            f"<td class='tier-{e.get('tier') or ''}'>{e.get('tier') or '—'}</td>"
            f"<td>{money(e.get('latest_asking'))}</td>"
            f"<td>{money(c['bca'])}</td>"
            f"<td>{f'{c[chr(114)+chr(97)+chr(116)+chr(105)+chr(111)]:.2f}' if c['ratio'] else '—'}</td>"
            f"<td>{html.escape(str(c['lot'] or '—'))}<div class='ev'>{c['zoning'] or ''}</div></td>"
            f"<td>{e['first_seen'][5:]} 首见<br>{e['last_seen'][5:]} 末次在售<div class='ev'>{'≤'+str(c['days'])+'d 内成交' if c['days'] is not None else ''}</div></td>"
            f"<td>{bands_cell(ann)}</td>"
            f"<td class='ev'>{sightings}</td>"
            "</tr>"
        )
    stats = [
        ("成交数", str(len(entries))),
        ("中位 ask/BCA", f"{median(ratios):.2f}" if ratios else "—"),
        ("其中 KEEP", str(sum(1 for e in entries if e.get("tier") == "KEEP"))),
        ("其中 WATCH", str(sum(1 for e in entries if e.get("tier") == "WATCH"))),
    ]
    return page(
        "成交样本 — 2026-10-05 扫描",
        "20 套已从市场消失的房源被 realtor.ca 页面确认为 SOLD（status=sold）。按最后挂牌价排序研究：我们判的 KEEP/WATCH 是否真被买走？",
        stats,
        ["地址", "Tier", "最后挂牌", "BCA 估价", "ask/BCA", "地块·Zoning", "卖出窗口", "人工维度档", "挂牌轨迹"],
        rows,
    )


def render_drops(entries, cards, anns) -> str:
    rows = []
    drops = []
    for e in entries:
        ev = next((x for x in e["events"] if x["type"] == "PRICE_DROP"), None)
        if not ev:
            continue
        pct = abs(ev["delta"]) / ev["old_asking"] if ev.get("old_asking") else None
        drops.append(pct or 0)
        card = cards.get("mls:" + (e.get("latest_mls") or ""), {})
        ann = anns.get("mls:" + (e.get("latest_mls") or ""), {})
        c = row_common(e, card, ann)
        link = card_link(e.get("latest_mls"))
        addr = html.escape(e["address"])
        addr_cell = f'<a href="{link}">{addr}</a>' if link else addr
        relisted = any(x["type"] == "RELISTED" for x in e["events"])
        flag = '<span class="flag flag-active">重挂</span>' if relisted else '<span class="flag">挂牌内降</span>'
        drop_cell = f"{money(ev['delta'])} ({pct*100:.1f}%)" if pct else money(ev.get('delta'))
        rows.append(
            "<tr>"
            f"<td>{addr_cell}<div class='ev'>{e.get('latest_mls') or ''} · {c['neighbourhood'] or ''}</div></td>"
            f"<td class='tier-{e.get('tier') or ''}'>{e.get('tier') or '—'}</td>"
            f"<td>{money(ev['old_asking'])}</td>"
            f"<td>{money(ev['asking'])}</td>"
            f"<td class='drop'>{drop_cell}</td>"
            f"<td>{money(c['bca'])}</td>"
            f"<td>{f'{c[chr(114)+chr(97)+chr(116)+chr(105)+chr(111)]:.2f}' if c['ratio'] else '—'}</td>"
            f"<td>{html.escape(str(c['lot'] or '—'))}<div class='ev'>{c['zoning'] or ''}</div></td>"
            f"<td>{flag}</td>"
            f"<td>{bands_cell(ann)}</td>"
            "</tr>"
        )
    rows.sort(key=lambda r: r)  # stable
    stats = [
        ("降价数", str(len(rows))),
        ("平均降幅", f"{sum(drops)/len(drops)*100:.1f}%" if drops else "—"),
        ("最大降幅", f"{max(drops)*100:.1f}%" if drops else "—"),
        ("KEEP 降价", str(sum(1 for e in entries if e.get("tier") == "KEEP"))),
    ]
    return page(
        "降价房源 — 2026-10-05 扫描",
        "本周相对上次观测降价的房源（新 MLS 重挂 + 挂牌内降价），按表内排序。重点关注 tier=KEEP/WATCH 的降价——那是卖家在向你的区间靠拢。",
        stats,
        ["地址", "Tier", "原价", "现价", "降幅", "BCA 估价", "ask/BCA", "地块·Zoning", "类型", "人工维度档"],
        rows,
    )


def render_cohort(entries, latest) -> str:
    """Tier × market-outcome validation matrix: does the market agree with us?"""
    from collections import defaultdict
    from northshore_watchdog.research.watchlist import TIER_ORDER

    tiers = ["KEEP", "WATCH", "PASS", "SPECIAL", None]
    by_tier: dict[str, list] = defaultdict(list)
    for e in entries:
        by_tier[e.get("tier")].append(e)

    head_rows, active_rows, sold_rows = [], [], []
    for t in tiers:
        es = by_tier.get(t, [])
        if not es:
            continue
        on_mkt = [e for e in es if e["status"] in ("ACTIVE", "STILL_LISTED")]
        sold = [e for e in es if e["status"] == "SOLD"]
        removed = [e for e in es if e["status"] == "EXPIRED_OR_WITHDRAWN"]
        dropped = [e for e in on_mkt if any(ev["type"] == "PRICE_DROP" for ev in e["events"])]
        sold_after_drop = [e for e in sold if any(ev["type"] == "PRICE_DROP" for ev in e["events"])]
        deltas = sorted(
            -ev["delta"] for e in dropped for ev in e["events"] if ev["type"] == "PRICE_DROP"
        )
        med_drop = f"-${deltas[len(deltas)//2]:,}" if deltas else "—"
        label = t or "未标注"
        head_rows.append(
            f"<tr><td class='tier-{t or ''}'>{label}</td><td>{len(es)}</td>"
            f"<td>{len(sold)} <b>({len(sold)*100//len(es)}%)</b></td>"
            f"<td>{len(removed)} ({len(removed)*100//len(es)}%)</td>"
            f"<td>{len(on_mkt)}</td></tr>"
        )
        active_rows.append(
            f"<tr><td class='tier-{t or ''}'>{label}</td><td>{len(on_mkt)}</td>"
            f"<td>{len(dropped)} <b>({len(dropped)*100//max(len(on_mkt),1)}%)</b></td>"
            f"<td>{med_drop}</td>"
            f"<td>{len(sold_after_drop)} / {len(sold)}</td></tr>"
        )
        for e in sorted(sold, key=lambda x: x.get("latest_asking") or 0):
            sold_rows.append(
                f"<tr><td class='tier-{t or ''}'>{label}</td><td>{html.escape(e['address'])}</td>"
                f"<td>{money(e.get('latest_asking'))}</td>"
                f"<td>{e['first_seen'][5:]} → 末见 {e['last_seen'][5:]}</td></tr>"
            )

    section = f"""
<h1>判断 × 市场结果对照 — {latest}</h1>
<div class="sub">每周 reconcile 后自动更新。核心问题：KEEP 是否快卖？PASS 是否降价/卖不动？WATCH 降价幅度？</div>

<h2 style="font-size:16px;margin-top:24px">结局矩阵</h2>
<table><thead><tr><th>Tier</th><th>追踪数</th><th>已成交 SOLD</th><th>下架/撤回</th><th>仍在售</th></tr></thead>
<tbody>{''.join(head_rows)}</tbody></table>

<h2 style="font-size:16px;margin-top:24px">价格行为（仍在售的）</h2>
<table><thead><tr><th>Tier</th><th>在售数</th><th>降价数(率)</th><th>中位降幅</th><th>降价后成交 / 总成交</th></tr></thead>
<tbody>{''.join(active_rows)}</tbody></table>

<h2 style="font-size:16px;margin-top:24px">成交明细（按最后挂牌价排序）</h2>
<table><thead><tr><th>Tier</th><th>地址</th><th>最后挂牌</th><th>窗口</th></tr></thead>
<tbody>{''.join(sold_rows)}</tbody></table>
<p class="sub" style="margin-top:18px">口径：SOLD=realtor.ca 页面确认成交；下架/撤回=详情页 404（可能含已售但页面被撤的）；成交价待 BCA 季度回填。降价率的分母是当前仍在售房源。</p>
"""
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>判断×市场对照</title><style>{CSS}</style></head><body>{section}</body></html>"""


def main() -> None:
    wl = json.load(open(WATCHLIST_PATH))
    cards = load_cards()
    _ann_list = json.load(open(ANNOTATIONS_PATH))["annotations"]
    anns = {a["property_key"]: a for a in _ann_list}
    # relisted entries carry the NEW mls; annotations were written under the
    # old property_key — fall back to address-key lookup for those.
    from northshore_watchdog.research.watchlist import normalize_address_key
    anns_by_addr = {}
    for a in _ann_list:
        k = normalize_address_key(a.get("address") or "")
        if k and (k not in anns_by_addr or (a.get("annotated_at") or "") > (anns_by_addr[k].get("annotated_at") or "")):
            anns_by_addr[k] = a
    for e in wl["entries"]:
        key = "mls:" + (e.get("latest_mls") or "")
        if key not in anns and e["address_key"] in anns_by_addr:
            anns[key] = anns_by_addr[e["address_key"]]
    entries = wl["entries"]
    latest = wl["latest_scan"]
    for e in entries:
        e["_latest_scan"] = latest

    sold = sorted(
        [e for e in entries if e["status"] == "SOLD"],
        key=lambda e: (-(__import__("northshore_watchdog.research.watchlist", fromlist=["TIER_ORDER"]).TIER_ORDER.get(e.get("tier")) or 0), e.get("latest_asking") or 0),
    )
    drops = [
        e for e in entries
        if any(ev["type"] == "PRICE_DROP" and ev["date"] == latest for ev in e["events"])
    ]
    drops.sort(key=lambda e: -(__import__("northshore_watchdog.research.watchlist", fromlist=["TIER_ORDER"]).TIER_ORDER.get(e.get("tier")) or 0))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p1 = OUT_DIR / f"sold-{latest}.html"
    p1.write_text(render_sold(sold, cards, anns))
    p2 = OUT_DIR / f"price-drops-{latest}.html"
    p2.write_text(render_drops(drops, cards, anns))
    p3 = OUT_DIR / "cohort-validation.html"
    p3.write_text(render_cohort(entries, latest))
    print(p1, len(sold))
    print(p2, len(drops))
    print(p3)


if __name__ == "__main__":
    main()
