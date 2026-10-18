#!/usr/bin/env python3
"""Render the weekly market-analytics page for the property watchlist.

Section A — neighbourhood statistics:
  - tracked counts, outcome mix (SOLD / withdrawn / in-scan active /
    coverage-gap still-listed / unresolved)
  - resolved sold rate uses only confirmed outcomes (sold + withdrawn)
    as denominator; coverage-gap listings are reported, never counted
  - median last asking, median ask/BCA ratio, median lot area
  - price-drop events (relist drops + same-MLS drops) and drop-then-sold
  - neighbourhoods with n<10 tracked are visually de-emphasised;
    a coarse East/West side rollup (longitude divider -123.1066) keeps
    every listing inside a statistically meaningful bucket

Writes reports/watchlist/market-analytics-<latest_scan>.html plus a JSON
sidecar so later sections (ratio curve, drop funnel, lot segments) can
reuse the joined dataset without re-deriving it.
"""

from __future__ import annotations

import glob
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "data/research/watchlist/property-watchlist.json"
OUT_DIR = ROOT / "reports/watchlist"
EW_DIVIDER_LNG = -123.1066
EW_DEADZONE = 0.001

CSS = """
body{font-family:-apple-system,'SF Pro',Helvetica,Arial,sans-serif;margin:24px;color:#222;max-width:1560px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px}
.sub{color:#666;font-size:13px;margin-bottom:14px;max-width:1100px}
.stat-note{color:#888;font-size:11px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 9px;border-bottom:1px solid #e8e6e1;text-align:left;vertical-align:top}
th{background:#f7f6f3;position:sticky;top:0;font-size:12px;color:#555;white-space:nowrap}
tr:hover td{background:#fafaf8}
tr.thin td{color:#999}
.sold{color:#0a7a3f;font-weight:700}
.drop{color:#b3261e;font-weight:600}
.muted{color:#999}
.flag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px}
.flag-sold{background:#d6ecd9;color:#0a7a3f;font-weight:700}
.flag-gap{background:#fdf3e0;color:#b46a00}
"""


def money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def pct(n, d) -> str:
    if not d:
        return "—"
    return f"{100 * n / d:.0f}%"


def med(values) -> float | None:
    vals = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(vals) if vals else None


def load_card_index() -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for p in sorted(glob.glob(str(ROOT / "data/research/normalized/regional-official-enrichment*.json"))):
        if ".bak." in p:
            continue
        for c in json.load(open(p)).get("candidates", []):
            if c.get("mls_number"):
                cards[c["mls_number"]] = c
    return cards


HOOD_GEOJSON = ROOT / "data/research/raw/boundaries/local-area-boundary-2026-09-05.geojson"

_hood_polys = None


def hood_from_lnglat(lng, lat) -> str | None:
    """Point-in-polygon against Vancouver's official local-area boundaries."""
    global _hood_polys
    if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
        return None
    if _hood_polys is None:
        _hood_polys = []
        try:
            from shapely.geometry import Point, shape
        except ImportError:
            _hood_polys = False
            return None
        for feat in json.load(open(HOOD_GEOJSON)).get("features", []):
            name = (feat.get("properties") or {}).get("name")
            geom = feat.get("geometry")
            if name and geom:
                _hood_polys.append((name, shape(geom)))
    if not _hood_polys:
        return None
    from shapely.geometry import Point

    pt = Point(lng, lat)
    for name, poly in _hood_polys:
        if poly.contains(pt):
            return name
    return None


def side_of(lng) -> str:
    if not isinstance(lng, (int, float)):
        return "未知"
    if abs(lng - EW_DIVIDER_LNG) <= EW_DEADZONE:
        return "中线"
    return "西区" if lng < EW_DIVIDER_LNG else "东区"


def _parse_date(value) -> Any:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def _days_to_sale(entry) -> int | None:
    """Approximate days from first sighting to sale.

    Sold date is not published; the disappearance scan date is the closest
    observation we have of the transaction completing.
    """
    if entry.get("status") != "SOLD":
        return None
    first = _parse_date(entry.get("first_seen"))
    if first is None:
        return None
    gone = next(
        (_parse_date(ev.get("date")) for ev in entry.get("events") or [] if ev["type"] == "DISAPPEARED"),
        None,
    )
    if gone is None:
        gone = _parse_date(entry.get("last_seen"))
    if gone is None:
        return None
    return max(0, (gone - first).days)


def join_rows(entries, cards, scan_date):
    today = _parse_date(scan_date)
    rows = []
    for e in entries:
        card = cards.get(e.get("latest_mls") or "") or {}
        raw = card.get("raw") or {}
        bca = raw.get("official_assessment_total")
        ask = e.get("latest_asking")
        drops = sum(1 for ev in e.get("events") or [] if ev["type"] == "PRICE_DROP")
        relists = [
            ev for ev in e.get("events") or []
            if ev["type"] == "RELISTED"
            and isinstance(ev.get("old_asking"), (int, float))
            and isinstance(ev.get("asking"), (int, float))
        ]
        relist_drops = sum(1 for ev in relists if ev["asking"] < ev["old_asking"])
        drop_pcts = [
            (ev["asking"] - ev["old_asking"]) / ev["old_asking"]
            for ev in e.get("events") or []
            if ev["type"] in ("PRICE_DROP", "RELISTED")
            and isinstance(ev.get("old_asking"), (int, float))
            and isinstance(ev.get("asking"), (int, float))
            and ev["asking"] < ev["old_asking"]
        ]
        last_drop_date = next(
            (
                _parse_date(ev.get("date"))
                for ev in reversed(e.get("events") or [])
                if ev["type"] in ("PRICE_DROP", "RELISTED")
                and isinstance(ev.get("old_asking"), (int, float))
                and isinstance(ev.get("asking"), (int, float))
                and ev["asking"] < ev["old_asking"]
            ),
            None,
        )
        gone_date = next(
            (_parse_date(ev.get("date")) for ev in e.get("events") or [] if ev["type"] == "DISAPPEARED"),
            _parse_date(e.get("last_seen")),
        )
        drop_to_sale = (
            (gone_date - last_drop_date).days
            if e["status"] == "SOLD" and last_drop_date and gone_date
            else None
        )
        first = _parse_date(e.get("first_seen"))
        obs_days = (today - first).days if first and today else None
        days_to_sale = _days_to_sale(e)
        hood = (card.get("neighbourhood") or "").strip()
        hood_src = "listing"
        if not hood:
            hood = hood_from_lnglat(card.get("longitude"), card.get("latitude")) or "未知"
            hood_src = "official_boundary"
        rows.append({
            "address": e.get("address") or e["address_key"],
            "hood": hood,
            "hood_src": hood_src,
            "side": side_of(card.get("longitude")),
            "tier": e.get("tier") or "—",
            "status": e["status"],
            "asking": ask,
            "bca": bca,
            "ratio": ask / bca if ask and bca else None,
            "lot_sqft": card.get("lot_area_sqft"),
            "frontage_ft": card.get("frontage_ft") or raw.get("official_frontage_ft"),
            "depth_ft": card.get("depth_ft") or raw.get("official_depth_ft"),
            "zoning": raw.get("official_zoning"),
            "drop_events": drops,
            "relist_drop_events": relist_drops,
            "first_seen": e.get("first_seen"),
            "last_seen": e.get("last_seen"),
            "obs_days": obs_days,
            "days_to_sale": days_to_sale,
            "drop_pcts": drop_pcts,
            "drop_to_sale_days": drop_to_sale,
        })
    return rows


def hood_stats(rows):
    """Aggregate rows per neighbourhood; returns (per_hood, per_side)."""
    def agg(bucket_rows):
        n = len(bucket_rows)
        sold = sum(1 for r in bucket_rows if r["status"] == "SOLD")
        withdrawn = sum(1 for r in bucket_rows if r["status"] == "EXPIRED_OR_WITHDRAWN")
        active = sum(1 for r in bucket_rows if r["status"] == "ACTIVE")
        gap = sum(1 for r in bucket_rows if r["status"] == "STILL_LISTED")
        unres = sum(1 for r in bucket_rows if r["status"] == "OUTCOME_UNRESOLVED")
        resolved = sold + withdrawn
        # 30-day sell-through: numerator = sold within 30d of first sighting,
        # denominator = listings with at least 30 days of observation.
        # Long-listed unsold stock therefore counts as "not sold" evidence,
        # while fresh listings are simply not yet eligible.
        eligible = [r for r in bucket_rows if (r["obs_days"] or 0) >= 30]
        sold_30d = sum(
            1 for r in eligible
            if r["days_to_sale"] is not None and r["days_to_sale"] <= 30
        )
        stale = sum(
            1 for r in eligible
            if r["status"] in ("ACTIVE", "STILL_LISTED")
        )
        drops = sum(r["drop_events"] + r["relist_drop_events"] for r in bucket_rows)
        drop_sold = sum(
            1 for r in bucket_rows
            if r["status"] == "SOLD" and (r["drop_events"] or r["relist_drop_events"])
        )
        return {
            "n": n,
            "sold": sold,
            "withdrawn": withdrawn,
            "active": active,
            "gap": gap,
            "unresolved": unres,
            "resolved": resolved,
            "sold_rate": sold / resolved if resolved else None,
            "win30_n": len(eligible),
            "win30_sold": sold_30d,
            "win30_rate": sold_30d / len(eligible) if eligible else None,
            "stale_30d": stale,
            "med_ask": med([r["asking"] for r in bucket_rows]),
            "med_ratio": med([r["ratio"] for r in bucket_rows]),
            "med_lot": med([r["lot_sqft"] for r in bucket_rows]),
            "drops": drops,
            "drop_sold": drop_sold,
            "keeps": sum(1 for r in bucket_rows if r["tier"] == "KEEP"),
            "thin": n < 10,
        }

    hoods: dict[str, list] = {}
    sides: dict[str, list] = {}
    for r in rows:
        hoods.setdefault(r["hood"], []).append(r)
        sides.setdefault(r["side"], []).append(r)
    return (
        sorted(((h, agg(v)) for h, v in hoods.items()), key=lambda kv: -kv[1]["n"]),
        sorted(((s, agg(v)) for s, v in sides.items()), key=lambda kv: -kv[1]["n"]),
    )


RATIO_BUCKETS = [
    ("≤0.75", None, 0.75),
    ("0.75–0.85", 0.75, 0.85),
    ("0.85–0.95", 0.85, 0.95),
    ("0.95–1.05", 0.95, 1.05),
    (">1.05", 1.05, None),
    ("无 BCA", None, None),
]


def ratio_bucket_stats(rows):
    out = []
    for label, lo, hi in RATIO_BUCKETS:
        if lo is None and hi is None:
            bucket = [r for r in rows if r["ratio"] is None]
        elif lo is None:
            bucket = [r for r in rows if r["ratio"] is not None and r["ratio"] <= hi]
        elif hi is None:
            bucket = [r for r in rows if r["ratio"] is not None and r["ratio"] > lo]
        else:
            bucket = [r for r in rows if r["ratio"] is not None and lo < r["ratio"] <= hi]
        eligible = [r for r in bucket if (r["obs_days"] or 0) >= 30]
        sold_30d = sum(1 for r in eligible if r["days_to_sale"] is not None and r["days_to_sale"] <= 30)
        out.append({
            "name": label,
            "n": len(bucket),
            "sold": sum(1 for r in bucket if r["status"] == "SOLD"),
            "withdrawn": sum(1 for r in bucket if r["status"] == "EXPIRED_OR_WITHDRAWN"),
            "active": sum(1 for r in bucket if r["status"] in ("ACTIVE", "STILL_LISTED")),
            "win30_n": len(eligible),
            "win30_sold": sold_30d,
            "win30_rate": sold_30d / len(eligible) if eligible else None,
            "stale_30d": sum(1 for r in eligible if r["status"] in ("ACTIVE", "STILL_LISTED")),
            "med_ask": med([r["asking"] for r in bucket]),
            "keeps": sum(1 for r in bucket if r["tier"] == "KEEP"),
        })
    return out


def drop_funnel(rows):
    def group_stats(bucket):
        return {
            "n": len(bucket),
            "sold": sum(1 for r in bucket if r["status"] == "SOLD"),
            "sold_30d_post_drop": sum(
                1 for r in bucket
                if r["drop_to_sale_days"] is not None and r["drop_to_sale_days"] <= 30
            ),
            "withdrawn": sum(1 for r in bucket if r["status"] == "EXPIRED_OR_WITHDRAWN"),
            "still": sum(1 for r in bucket if r["status"] in ("ACTIVE", "STILL_LISTED")),
            "med_drop_pct": med([p for r in bucket for p in r["drop_pcts"]]),
            "med_ask": med([r["asking"] for r in bucket]),
        }

    dropped = [r for r in rows if r["drop_events"] or r["relist_drop_events"]]
    groups = [
        ("所有降过价的房", dropped),
        ("· 纯同 MLS 调价(未重挂)", [r for r in dropped if not r["relist_drop_events"]]),
        ("· 低价重挂(换 MLS)", [r for r in dropped if r["relist_drop_events"]]),
        ("对照:从未降价", [r for r in rows if not (r["drop_events"] or r["relist_drop_events"])]),
    ]
    return [{"name": n, **group_stats(g)} for n, g in groups]


MULTI_ZONE_PREFIX = ("RT", "RM", "FM", "CD", "C-", "I-")
STD_ZONE_PREFIX = ("R1", "RS", "RA", "RR")


def lot_shape_stats(rows):
    """Section D — lot-shape segmentation.

    Frontage penalties are only meaningful inside standard single-family
    zoning (RT/RM multiplex zones allow narrow lots by design), so the
    <33 / 33-50 / >50 ft split applies to standard zones only; multi
    zoning is its own group.
    """
    def zone_class(z):
        z = (z or "").strip().upper()
        if not z:
            return "未知"
        if z.startswith(MULTI_ZONE_PREFIX):
            return "多户型"
        if z.startswith(STD_ZONE_PREFIX):
            return "标准"
        return "其他"

    def agg(label, bucket):
        eligible = [r for r in bucket if (r["obs_days"] or 0) >= 30]
        sold_30d = sum(1 for r in eligible if r["days_to_sale"] is not None and r["days_to_sale"] <= 30)
        return {
            "name": label,
            "n": len(bucket),
            "sold": sum(1 for r in bucket if r["status"] == "SOLD"),
            "withdrawn": sum(1 for r in bucket if r["status"] == "EXPIRED_OR_WITHDRAWN"),
            "win30_n": len(eligible),
            "win30_sold": sold_30d,
            "win30_rate": sold_30d / len(eligible) if eligible else None,
            "stale_30d": sum(1 for r in eligible if r["status"] in ("ACTIVE", "STILL_LISTED")),
            "med_ask": med([r["asking"] for r in bucket]),
            "med_ratio": med([r["ratio"] for r in bucket]),
            "med_lot": med([r["lot_sqft"] for r in bucket]),
            "keeps": sum(1 for r in bucket if r["tier"] == "KEEP"),
        }

    std = [r for r in rows if zone_class(r["zoning"]) == "标准"]
    multi = [r for r in rows if zone_class(r["zoning"]) == "多户型"]
    other = [r for r in rows if zone_class(r["zoning"]) in ("其他", "未知")]
    no_front = [r for r in std if not r["frontage_ft"]]

    groups = [
        ("标准zoning · 面宽&lt;33ft", [r for r in std if r["frontage_ft"] and r["frontage_ft"] < 33]),
        ("标准zoning · 面宽33–50ft", [r for r in std if r["frontage_ft"] and 33 <= r["frontage_ft"] <= 50]),
        ("标准zoning · 面宽&gt;50ft", [r for r in std if r["frontage_ft"] and r["frontage_ft"] > 50]),
        ("标准zoning · 面宽未知", no_front),
        ("多户型zoning(RT/RM等)", multi),
        ("其他/未知zoning", other),
    ]
    return [agg(n, g) for n, g in groups]


def render(hood_rows, side_rows, ratio_rows, funnel_rows, lot_rows, scan_date: str, total: int) -> str:
    def tr(label, s):
        win30 = (
            f'<span class="sold">{s["win30_rate"]:.0%}</span>'
            if s["win30_rate"] is not None
            else '<span class="muted">—</span>'
        )
        return (
            f"<tr><td>{label}</td>"
            f"<td>{s['n']}</td>"
            f"<td>{s['sold']}</td><td>{s['withdrawn']}</td><td>{s['active']}</td>"
            f"<td>{s['gap']}</td><td>{s['unresolved']}</td>"
            f"<td>{win30} <span class='stat-note'>({s['win30_sold']}/{s['win30_n']}满窗)</span></td>"
            f"<td>{s['stale_30d']}</td>"
            f"<td>{money(s['med_ask'])}</td>"
            f"<td>{f'{s['med_ratio']:.2f}' if s['med_ratio'] else '—'}</td>"
            f"<td>{f'{s['med_lot']:,.0f}' if s['med_lot'] else '—'}</td>"
            f"<td>{s['drops']}</td><td>{s['drop_sold']}</td><td>{s['keeps']}</td></tr>"
        )

    head = (
        "<tr><th>社区</th><th>追踪</th><th>SOLD</th><th>撤回</th><th>在售</th>"
        "<th>漏抓在售</th><th>未决</th><th>≤30天成交率</th><th>在售&gt;30天</th><th>中位挂牌</th>"
        "<th>中位ask/BCA</th><th>中位地块sqft</th><th>降价次</th><th>降价后成交</th><th>KEEP</th></tr>"
    )
    body = "".join(tr(h, s) for h, s in hood_rows)
    side_body = "".join(tr(s, v) for s, v in side_rows)

    b_head = (
        "<tr><th>ask/BCA 桶</th><th>追踪</th><th>SOLD</th><th>撤回</th><th>在售</th>"
        "<th>≤30天成交率</th><th>在售&gt;30天</th><th>中位挂牌</th><th>KEEP</th></tr>"
    )
    def b_tr(s):
        rate = f'<span class="sold">{s["win30_rate"]:.0%}</span>' if s["win30_rate"] is not None else "—"
        return (
            f"<tr><td>{s['name']}</td><td>{s['n']}</td><td>{s['sold']}</td>"
            f"<td>{s['withdrawn']}</td><td>{s['active']}</td>"
            f"<td>{rate} <span class='stat-note'>({s['win30_sold']}/{s['win30_n']}满窗)</span></td>"
            f"<td>{s['stale_30d']}</td><td>{money(s['med_ask'])}</td><td>{s['keeps']}</td></tr>"
        )

    b_body = "".join(b_tr(s) for s in ratio_rows)

    c_head = (
        "<tr><th>分组</th><th>套数</th><th>SOLD</th><th>降价后≤30天成交</th>"
        "<th>撤回</th><th>仍在挂</th><th>中位单次降幅</th><th>中位挂牌</th></tr>"
    )
    def c_tr(s):
        drop_pct = f"{s['med_drop_pct']:.1%}" if s["med_drop_pct"] is not None else "—"
        return (
            f"<tr><td>{s['name']}</td><td>{s['n']}</td><td>{s['sold']}</td>"
            f"<td>{s['sold_30d_post_drop']}</td><td>{s['withdrawn']}</td><td>{s['still']}</td>"
            f"<td class='drop'>{drop_pct}</td>"
            f"<td>{money(s['med_ask'])}</td></tr>"
        )

    c_body = "".join(c_tr(s) for s in funnel_rows)

    d_head = (
        "<tr><th>分组</th><th>追踪</th><th>SOLD</th><th>撤回</th>"
        "<th>≤30天成交率</th><th>在售&gt;30天</th><th>中位挂牌</th>"
        "<th>中位ask/BCA</th><th>中位地块sqft</th><th>KEEP</th></tr>"
    )

    def d_tr(s):
        rate = f'<span class="sold">{s["win30_rate"]:.0%}</span>' if s["win30_rate"] is not None else "—"
        ratio = f"{s['med_ratio']:.2f}" if s["med_ratio"] else "—"
        lot = f"{s['med_lot']:,.0f}" if s["med_lot"] else "—"
        return (
            f"<tr><td>{s['name']}</td><td>{s['n']}</td><td>{s['sold']}</td>"
            f"<td>{s['withdrawn']}</td>"
            f"<td>{rate} <span class='stat-note'>({s['win30_sold']}/{s['win30_n']}满窗)</span></td>"
            f"<td>{s['stale_30d']}</td><td>{money(s['med_ask'])}</td>"
            f"<td>{ratio}</td><td>{lot}</td><td>{s['keeps']}</td></tr>"
        )

    d_body = "".join(d_tr(s) for s in lot_rows)

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style>
<title>市场分析 — {scan_date}</title></head><body>
<h1>市场分析 — {scan_date}</h1>
<div class="sub">追踪 {total} 套。口径：<b>≤30天成交率</b> = 首见后 30 天内成交 ÷ 观察已满 30 天的房（分子/分母括号内标注）——挂超 30 天没卖即计为未成交；挂牌未满窗的房不进分母。<b>在售&gt;30天</b> = 满窗且至今仍在挂的淤积库存。"漏抓在售"为扫描覆盖缺口，"未决"为结局未定。</div>
<h2>A · 片区汇总（东/西区，经线 -123.1066 分界）</h2>
<table>{head}{side_body}</table>
<h2>A · 社区明细</h2>
<table>{head}{body}</table>
<h2>B · ask/BCA 比率 × 30天成交率</h2>
<div class="sub">检验价格锚：低比率房是否明显卖得更快。注意<b>幸存者偏差</b>——便宜房卖得快更快离场，曲线天然被压平；压平了仍有差异才是硬信号。无 BCA 的房单列。</div>
<table>{b_head}{b_body}</table>
<h2>C · 降价转化漏斗</h2>
<div class="sub">降价到底有没有用：降过价的房 vs 从未降价房的结局对比；降后≤30天成交 = 降价事件后 30 天内消失（成交近似）。降幅为单次降价幅度的中位数。</div>
<table>{c_head}{c_body}</table>
<h2>D · 地块形态 × 市场表现</h2>
<div class="sub">验证窄面宽惩罚：面宽分段<b>只在标准 zoning(R1-1/RS 系）内比较</b>——RT/RM 多户型 zoning 的窄地合规，不算缺陷，单列一组。若窄面宽组成交率/比率显著更低，`NARROW_FRONTAGE_SHAPE` 规则获市场证实。</div>
<table>{d_head}{d_body}</table>
<p class="stat-note">成交日近似为消失扫描日（realtor.ca 不公布成交日）。挂牌中位数取最后观察价。降价 = 同 MLS 降价 + 低价重挂事件合计。</p>
</body></html>"""


def main() -> int:
    w = json.load(open(WATCHLIST))
    scan = w.get("latest_scan") or "unknown"
    cards = load_card_index()
    rows = join_rows(w["entries"], cards, scan)
    hood_rows, side_rows = hood_stats(rows)
    ratio_rows = ratio_bucket_stats(rows)
    funnel_rows = drop_funnel(rows)
    lot_rows = lot_shape_stats(rows)

    html = render(hood_rows, side_rows, ratio_rows, funnel_rows, lot_rows, scan, len(rows))
    out_html = OUT_DIR / f"market-analytics-{scan}.html"
    out_html.write_text(html, encoding="utf-8")

    sidecar = OUT_DIR / f"market-analytics-{scan}.json"
    sidecar.write_text(
        json.dumps(
            {
                "scan": scan,
                "property_count": len(rows),
                "neighbourhoods": [{"name": h, **s} for h, s in hood_rows],
                "sides": [{"name": s, **v} for s, v in side_rows],
                "ratio_buckets": ratio_rows,
                "drop_funnel": funnel_rows,
                "lot_shapes": lot_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(out_html)
    print(sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
