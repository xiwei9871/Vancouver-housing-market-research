#!/usr/bin/env python3
"""V1.4.3 listing-level results — V3s-style List/Map HTML report.

Reproduces the confirmed rebuild-value page layout (banner, xbar
selects, community stats table, 3-scenario grid, filter row, Leaflet
map toggle) with V1.4.3 data swapped in:

  - 社区均价 = MAIN land-dominant pool (age>=50, land_share>=0.95)
    median normalized price per community (and per community x lot
    band for the per-listing column) — rebuilt via the V1.4 pool
    builder so numbers are identical to the pipeline.
  - Scenario cells = V1.4.3 est sale + 1y dev ROI + comps level/n.
  - Map dots colored by quadrant (BOTH/ROI_ONLY/LAND_VALUE/NEITHER).

Output: docs/audits/listing_level_valuation_v1_4/listing_map.html
Run:    python3 scripts/render_listing_map_v1_4.py
"""
import csv
import html
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import listing_level_valuation_v1_4 as v  # noqa: E402
import render_market_analytics as base    # noqa: E402

D = ROOT / "docs/audits/listing_level_valuation_v1_4"
OUT = D / "listing_map.html"

CSS = base.CSS + """
.roi-pos{color:#0a7a3f;font-weight:700}
.roi-neg{color:#b3261e;font-weight:700}
tr.best td{background:#f2f8f3}
#proptable th{cursor:pointer;user-select:none}
th .arrow{color:#999;font-size:10px;margin-left:3px}
tr.filters input,tr.filters select{width:100%;box-sizing:border-box;
font-size:11px;padding:3px 5px;border:1px solid #ddd;border-radius:4px}
tr.filters td{padding:4px 6px;border-bottom:2px solid #ddd}
.viewbar{display:flex;align-items:center;gap:8px;margin:8px 0}
.vbtn{padding:6px 18px;font-size:14px;border:1px solid #2456a6;
background:#fff;color:#2456a6;border-radius:4px;cursor:pointer}
.vbtn.on{background:#2456a6;color:#fff}
.map-legend{margin-left:14px;font-size:12px;color:#555}
.map-legend i{display:inline-block;width:10px;height:10px;
border-radius:50%;margin:0 4px 0 10px}
#mapview{display:none}
#map{height:78vh;border:1px solid #ccc;border-radius:6px}
.leaflet-popup-content{font-size:13px;line-height:1.5}
.leaflet-popup-content .pp-title{font-weight:700;font-size:14px}
.leaflet-popup-content .pp-roi{color:#0a7a3f;font-weight:700}
.badge{background:#eef2f8;border-radius:10px;padding:2px 9px;
margin-right:6px;font-size:12px}
"""

MAP_JS = """
let mapInit = false, _map = null, _layer = null;
const fmtM = v => v == null ? '—' : '$' + Number(v).toLocaleString('en-CA');
const fmtP = v => v == null ? '—' : Number(v).toFixed(1) + '%';
function visiblePoints() {
    if (!window.__visKeys) return MAP_POINTS;
    return MAP_POINTS.filter(p => window.__visKeys.has(String(p.k)));
}
function drawPoints() {
    if (!_map) return;
    if (_layer) _layer.clearLayers();
    _layer = L.layerGroup().addTo(_map);
    const pts = visiblePoints(), bounds = [];
    for (const p of pts) {
        const m = L.circleMarker([p.lat, p.lng], {
            radius: 8, color: '#fff', weight: 1.5,
            fillColor: QC[p.quad], fillOpacity: 0.92
        }).addTo(_layer);
        m.bindPopup('<div class="pp-title">#' + p.rank + ' ' + p.addr
            + '</div><div>' + p.hood + ' · ' + p.zoning + ' · '
            + p.quad + '</div><div>Ask ' + fmtM(p.asking)
            + ' · 均价 ' + fmtM(p.comm_avg)
            + ' · 地价 ' + fmtM(p.fair) + ' (' + fmtP(p.disc)
            + ')</div><div>' + p.scen + ' 估售 ' + fmtM(p.sale)
            + '</div><div class="pp-roi">ROI1y ' + fmtP(p.roi)
            + ' · 20%上限 ' + fmtM(p.mb20) + '</div>');
        bounds.push([p.lat, p.lng]);
    }
    document.getElementById('mapcount').textContent =
        pts.length + ' / ' + MAP_POINTS.length;
    if (bounds.length) _map.fitBounds(bounds, {padding: [30, 30]});
}
function initMap() {
    _map = L.map('map').setView([49.24, -123.11], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(_map);
    drawPoints();
}
document.getElementById('btn-list').onclick = () => {
    document.getElementById('btn-list').classList.add('on');
    document.getElementById('btn-map').classList.remove('on');
    document.getElementById('listview').style.display = '';
    document.getElementById('mapview').style.display = 'none';
};
document.getElementById('btn-map').onclick = () => {
    document.getElementById('btn-map').classList.add('on');
    document.getElementById('btn-list').classList.remove('on');
    document.getElementById('listview').style.display = 'none';
    document.getElementById('mapview').style.display = 'block';
    if (!mapInit) { mapInit = true; initMap(); }
};
"""

TABLE_JS = """
const table = document.getElementById('proptable');
const tbody = table.querySelector('tbody');
const dataRows = Array.from(tbody.querySelectorAll('tr'));
let sortCol = -1, sortDir = 1;
const cellVal = (row, i) => {
    if (!row.cells[i]) return '';
    const dv = row.cells[i].getAttribute('data-v');
    return dv !== null && dv !== '' ? dv
                                   : row.cells[i].innerText.trim();
};
function numOf(t) {
    const m = t.replace(/[$,%\\s]/g, '').match(/-?\\d+\\.?\\d*/);
    return m ? parseFloat(m[0]) : null;
}
function sortBy(i) {
    if (sortCol === i) sortDir *= -1; else { sortCol = i; sortDir = 1; }
    table.querySelectorAll('th .arrow').forEach(a => a.textContent = '');
    table.querySelectorAll('th')[i].querySelector('.arrow')
        .textContent = sortDir === 1 ? '▲' : '▼';
    dataRows.sort((a, b) => {
        const ta = cellVal(a, i), tb = cellVal(b, i);
        const na = numOf(ta), nb = numOf(tb);
        if (na !== null && nb !== null) return (na - nb) * sortDir;
        return ta.localeCompare(tb, 'zh') * sortDir;
    });
    dataRows.forEach(r => tbody.appendChild(r));
}
table.querySelectorAll('th').forEach((th, i) =>
    th.onclick = () => sortBy(i));

function matchExpr(t, q) {
    q = q.trim(); if (!q) return true;
    const cmp = q.match(/^(>=|<=|>|<)?\\s*(.+)$/);
    const val = cmp[2].toLowerCase(), op = cmp[1];
    const rng = val.match(/^(\\d+\\.?\\d*)-(\\d+\\.?\\d*)$/);
    const n = numOf(t);
    if (rng && n !== null) {
        const v = t.includes('%') && Math.abs(n) < 2 ? n * 100 : n;
        return v >= parseFloat(rng[1]) && v <= parseFloat(rng[2]);
    }
    if (n !== null && /^-?\\d|^<\\d|^>\\d/.test(val)) {
        const v = parseFloat(val.replace(/,/g, ''));
        return op === '>' ? n > v : op === '<' ? n < v :
               op === '>=' ? n >= v : op === '<=' ? n <= v :
               Math.abs(n - v) < Math.abs(v) * 0.05 + 0.5;
    }
    return t.toLowerCase().includes(val);
}
function roiBandMatch(v, sel) {
    if (!sel) return true;
    if (sel === '—') return v == null;
    if (v == null) return false;
    if (sel === '>30') return v >= 30;
    if (sel === '<0') return v < 0;
    const [a, b] = sel.split('-').map(Number);
    return v >= a && v < b;
}
function xbarOk(meta) {
    const x = window.__xf || {};
    if (x.quad && meta.quad !== x.quad) return false;
    if (x.prod === 'HOUSE' && !meta.has_h) return false;
    if (x.prod === 'DUPLEX' && !meta.has_d) return false;
    if (x.prod === 'MPX' && !meta.has_m) return false;
    if (x.conf === 'low' && !meta.low) return false;
    if (x.conf === 'ok' && meta.low) return false;
    return true;
}
function applyFilters() {
    const cells = table.querySelectorAll('tr.filters > *');
    let vis = 0; const visKeys = new Set();
    dataRows.forEach((r, idx) => {
        let ok = true;
        cells.forEach((c, i) => {
            const el = c.querySelector('input,select');
            if (!el || !el.value) return;
            if (el.tagName === 'SELECT') {
                const sel = el.value, t = cellVal(r, i);
                if (c.closest('tr').children[i] &&
                    el.dataset.kind === 'roi')
                    ok = ok && roiBandMatch(
                        META[idx].roi, sel);
                else
                    ok = ok && matchExpr(t, sel);
            } else {
                ok = ok && matchExpr(cellVal(r, i), el.value);
            }
        });
        ok = ok && xbarOk(META[idx]);
        r.style.display = ok ? '' : 'none';
        if (ok) { vis++; visKeys.add(String(idx)); }
    });
    document.getElementById('viscount').textContent = vis;
    window.__visKeys = visKeys;
    if (_map) drawPoints();
}
table.querySelectorAll('tr.filters input,' +
    'tr.filters select').forEach(el => {
    el.addEventListener('input', applyFilters);
    el.addEventListener('change', applyFilters);
});
document.querySelectorAll('#xbar select').forEach(el =>
    el.addEventListener('change', () => {
        window.__xf = window.__xf || {};
        window.__xf[el.dataset.x] = el.value;
        applyFilters();
    }));
"""


def money_m(v):
    if v is None:
        return "—"
    s = "-" if v < 0 else ""
    v = abs(v)
    return f"{s}${v / 1e6:.2f}M" if v >= 1e6 else f"{s}${v / 1e3:.0f}k"


def main():
    land = {r["listing"]: r for r in csv.DictReader(
        open(D / "active_house_land_valuation.csv"))}
    roi = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_listing_roi.csv"))
        if r["gst_scenario"] == "EXCL"}
    bid = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_max_bid_and_negotiation.csv"))}
    scen = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_buildable_scenarios.csv"))}
    nb = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_newbuild_sale_estimate.csv"))}
    geo = {r["address"]: r for r in json.load(
        open(v.LISTING_JSON))["rows"]}

    # ---- BCA assessment year + official values ----------------------
    # property-tax roll: civic number + street name -> assessment year.
    # Street names use short forms ("19TH AVE E"); a leading direction
    # in the listing address ("614 E 19TH AVENUE") maps to a trailing
    # suffix, while directional street names ("WEST BOULEVARD") stay.
    DIR = {"E", "W", "N", "S", "EAST", "WEST", "NORTH", "SOUTH"}
    TYPE_ABBR = {"AVENUE": "AVE", "STREET": "ST", "ROAD": "RD",
                 "DRIVE": "DR", "CRESCENT": "CRES", "PLACE": "PL",
                 "BOULEVARD": "BOULEVARD", "AVENUE E": "AVE E"}

    def street_variants(street):
        w = street.split()
        out = {street, " ".join(TYPE_ABBR.get(t, t) for t in w)}
        if w and w[0] in DIR:
            d = w[0]
            short = {"EAST": "E", "WEST": "W", "NORTH": "N",
                     "SOUTH": "S"}.get(d, d)
            rest = " ".join(TYPE_ABBR.get(t, t) for t in w[1:])
            out.add(f"{rest} {short}")
            out.add(f"{' '.join(w[1:])} {short}")
        return out

    tax_idx = {}
    with open(ROOT / "data/research/raw/official/"
              "property-tax-2026-full.csv", encoding="utf-8-sig") as f:
        for t in csv.DictReader(f, delimiter=";"):
            civ = (t.get("from_civic_number") or
                   t.get("to_civic_number") or "").strip()
            st = (t.get("street_name") or "").strip().upper()
            if not civ or not st:
                continue
            key = f"{civ} {st}"
            if key not in tax_idx:
                tax_idx[key] = {
                    "year": (t.get("tax_assessment_year") or "").strip(),
                    "land": num_or_none(t.get("current_land_value")),
                    "impr": num_or_none(t.get("current_improvement_value"))}

    def tax_of(addr):
        parts = addr.upper().split(None, 1)
        if len(parts) != 2 or not parts[0][0].isdigit():
            return None
        for sv in street_variants(parts[1]):
            hit = tax_idx.get(f"{parts[0]} {sv}")
            if hit:
                return hit
        return None

    # ---- rebuild the exact V1.4.3 MAIN land pool for 社区均价 -------
    house_rows = list(csv.DictReader(
        open(v.V12 / "house_price_normalized_per_row.csv")))
    pool, _aux, _ = v.build_land_pool(
        house_rows, v.load_archive(), v.load_resolved_yb(), v.load_tax())
    comm_all = defaultdict(list)
    comm_band = defaultdict(list)
    for r in pool:
        comm_all[r["community"]].append(r)
        comm_band[(r["community"], v.band_label(
            v.lot_band(r["lot"])))].append(r)
    comm_price_p50 = {c: statistics.median(
        [r["price_2026_09"] for r in rs if r["price_2026_09"]])
        for c, rs in comm_all.items()}
    comm_psf_p50 = {c: statistics.median(
        [r["land_psf"] for r in rs if r["land_psf"]])
        for c, rs in comm_all.items()}
    comm_lot_p50 = {c: statistics.median(r["lot"] for r in rs)
                    for c, rs in comm_all.items()}
    comm_band_price = {(c, b): statistics.median(
        [r["price_2026_09"] for r in rs if r["price_2026_09"]])
        for (c, b), rs in comm_band.items()}

    # ---- per-listing rows -------------------------------------------
    rows, meta, points = [], [], []
    n10 = n20 = n30 = 0
    for k, l in land.items():
        g = geo.get(k, {})
        cells, rois, has = {}, [], {"HOUSE": False, "DUPLEX": False,
                                  "MULTIPLEX": False}
        for s in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            sc, rr, nr = scen.get((k, s)), roi.get((k, s)), nb.get((k, s))
            bd = bid.get((k, s), {})
            if sc and sc["eligible"] == "True" and rr \
                    and rr["est_sale_today"]:
                has[s] = True
                rois.append((float(rr["roi_development_1y"]), s))
                cells[s] = {
                    "assumptive": sc.get("status") == "ASSUMPTIVE_ZONING",
                    "build": float(sc["build_sqft"]),
                    "units": sc["units"],
                    "sale": float(rr["est_sale_today"]),
                    "roi": float(rr["roi_development_1y"]),
                    "simple": float(rr["roi_simple"]),
                    "psf": float(nr["psf_p50"]) if nr and nr["psf_p50"]
                    else None,
                    "level": nr["comp_level"] if nr else "",
                    "n": nr["comp_n"] if nr else "",
                    "mb20": bd.get("max_bid_roi_20", ""),
                }
            elif sc and sc["eligible"] == "True":
                cells[s] = {"blocked": "缺comps"}
            else:
                cells[s] = None
        best = max(rois) if rois else (None, None)
        roi_v, best_s = best
        asking = float(l["asking"]) if l["asking"] else None
        fair = float(l["fair_land_value_today"]) \
            if l["fair_land_value_today"] else None
        disc = float(l["discount_to_land_market_pct"]) \
            if l["discount_to_land_market_pct"] else None
        comm = l["hood_comm"]
        band = v.band_label(v.lot_band(float(l["lot_sqft"]))) \
            if l["lot_sqft"] else None
        comm_avg = comm_band_price.get((comm, band)) or \
            comm_price_p50.get(comm)
        land_ok = disc is not None and disc > 0
        roi_ok = roi_v is not None and roi_v >= 10
        quad = ("BOTH" if land_ok and roi_ok else
                "LAND_VALUE" if land_ok else
                "ROI_ONLY" if roi_ok else "NEITHER")
        if roi_v is not None:
            n10 += roi_v >= 10
            n20 += roi_v >= 20
            n30 += roi_v >= 30
        confs = [roi[k2]["land_conf"] for k2 in
                 ((k, s) for s in ("HOUSE", "DUPLEX", "MULTIPLEX"))
                 if k2 in roi] + [l["confidence"]]
        low = any("LOW" in c or "UNKNOWN" in c for c in confs)
        tx = tax_of(k)
        rows.append({"k": k, "addr": k, "comm": comm, "band": band,
                     "zoning": l["zoning"], "asking": asking,
                     "bca_t": num_or_none(g.get("bca")),
                     "bca_l": num_or_none(g.get("bca_land")),
                     "bca_yr": (tx or {}).get("year"),
                     "bca_tax": tx,
                     "fair": fair, "disc": disc, "comm_avg": comm_avg,
                     "lot": float(l["lot_sqft"]) if l["lot_sqft"]
                     else None,
                     "frontage": float(l["frontage"])
                     if l["frontage"] else None,
                     "cells": cells, "roi": roi_v, "best": best_s,
                     "quad": quad, "conf": confs[0][:4] if confs else "",
                     "low": low, "link": (g.get("link") or "").replace("../calibration/", "/reports/calibration/"),
                     "lat": g.get("lat"), "lng": g.get("lng"),
                     "mb20": cells.get(best_s, {}).get("mb20")
                     if best_s else None,
                     "sale": cells.get(best_s, {}).get("sale")
                     if best_s else None})
    rows.sort(key=lambda r: (r["roi"] is None, -(r["roi"] or -999)))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        meta.append({"quad": r["quad"], "roi": r["roi"],
                     "has_h": r["cells"]["HOUSE"] is not None
                     and "sale" in r["cells"]["HOUSE"],
                     "has_d": r["cells"]["DUPLEX"] is not None
                     and "sale" in r["cells"]["DUPLEX"],
                     "has_m": r["cells"]["MULTIPLEX"] is not None
                     and "sale" in r["cells"]["MULTIPLEX"],
                     "low": r["low"]})
        if r["lat"] and r["lng"]:
            points.append({"k": i, "addr": r["addr"], "hood": r["comm"],
                           "lat": float(r["lat"]), "lng": float(r["lng"]),
                           "asking": r["asking"], "fair": r["fair"],
                           "comm_avg": r["comm_avg"], "disc": r["disc"],
                           "roi": r["roi"], "scen": r["best"] or "—",
                           "quad": r["quad"], "zoning": r["zoning"],
                           "sale": r["sale"], "rank": r["rank"],
                           "mb20": num_or_none(r["mb20"])})

    # ---- community stats table (社区均价) ----------------------------
    listed_by_comm = defaultdict(list)
    best_by_comm = defaultdict(Counter)
    for r in rows:
        listed_by_comm[r["comm"]].append(r)
        if r["best"]:
            best_by_comm[r["comm"]][r["best"]] += 1
    hood_rows = []
    for c in sorted(comm_price_p50, key=lambda c: -comm_price_p50[c]):
        n_list = len(listed_by_comm.get(c, []))
        asks = [r["asking"] for r in listed_by_comm.get(c, [])
                if r["asking"]]
        ask_med = statistics.median(asks) if asks else None
        ratio = f"{ask_med / comm_price_p50[c]:.2f}" \
            if ask_med and comm_price_p50[c] else "—"
        cnt = best_by_comm.get(c)
        tend = (f"{cnt.most_common(1)[0][0]}"
                f"<span class='muted'>("
                f"{'/'.join(f'{k2[0]}{v2}' for k2, v2 in cnt.most_common())}"
                f")</span>") if cnt else "<span class='muted'>—</span>"
        hood_rows.append(
            f"<tr><td>{c}</td><td>{len(comm_all[c])}</td>"
            f"<td><b>{base.money(comm_price_p50[c])}</b></td>"
            f"<td>${comm_psf_p50[c]:,.0f}</td>"
            f"<td>{comm_lot_p50[c]:,.0f}</td>"
            f"<td>{n_list}</td><td>{base.money(ask_med)}</td>"
            f"<td>{ratio}</td><td>{tend}</td></tr>")
    hood_table = (
        "<h2>社区均价(土地主导旧房·age≥50·BCA土地占比≥95%·归一化2026-09)</h2>"
        "<div class='stat-note'>均价=该社区土地型成交池的归一化成交中位价"
        "(非当前挂牌价);$/sf 为土地单价中位;挂牌列为当前 ACTIVE 房源的"
        " Ask 中位与均价之比。</div>"
        "<table><tr><th>社区</th><th>土地型成交n</th><th>社区均价P50</th>"
        "<th>土地$/sf P50</th><th>lot P50</th><th>挂牌n</th>"
        "<th>挂牌Ask中位</th><th>Ask/均价</th><th>最优形态</th></tr>"
        + "".join(hood_rows) + "</table>")

    # ---- main table ---------------------------------------------------
    QC = {"BOTH": "#2e8b57", "ROI_ONLY": "#2456a6",
          "LAND_VALUE": "#e6a23c", "NEITHER": "#8a94a3"}
    QUAD_CN = {"BOTH": "双优", "ROI_ONLY": "仅ROI",
               "LAND_VALUE": "仅地价", "NEITHER": "—"}

    def scen_cell(c):
        if c is None:
            return "<td class='muted'>不适用</td>"
        if "blocked" in c:
            return "<td class='muted'>缺comps</td>"
        mb = c["mb20"]
        mb_txt = money_m(num_or_none(mb)) if mb not in ("", None) \
            else ("UNATTAINABLE" if mb == "UNATTAINABLE" else "—")
        return (
            f"<td>建面{c['build']:,.0f}sf"
            f"{'·' + c['units'] + '户' if c['units'] != '1' else ''}"
            f"<span style='color:#b45309'>⚠法规假设</span>"
            if c.get("assumptive") else ""
        ) + (
            f"<br>估售{base.money(c['sale'])}"
            f"<br><span class='stat-note'>@"
            f"{f'${c[chr(112)+chr(115)+chr(102)]:,.0f}/sf' if c['psf'] else '—'}"
            f"·{c['level']}·n{c['n']}</span>"
            f"<br><span class='{'roi-pos' if c['roi'] >= 10 else 'roi-neg'}'>"
            f"ROI1y {c['roi']:.1f}%</span>"
            f"<span class='stat-note'> 20%上限 {mb_txt}</span></td>")

    body = []
    for r in rows:
        cls = " class='best'" if r["roi"] is not None and r["roi"] >= 10 \
            else ""
        dc = "roi-pos" if (r["disc"] or 0) > 0 else "roi-neg"
        disc_txt = "—" if r["disc"] is None else (
            f"低于估值 {r['disc']:.1f}%" if r["disc"] > 0
            else f"高于估值 {-r['disc']:.1f}%")
        best_txt = (f"<span class='roi-pos'>{r['roi']:.0f}% "
                    f"{r['best']}</span>") if r["roi"] is not None else "—"
        addr = (f'<a href="{html.escape(r["link"])}">'
                f"{html.escape(r['addr'])}</a>"
                if r["link"] else html.escape(r["addr"]))
        # BCA 对照: two lines (total ratio / land ratio), hover detail
        if r["bca_t"] or r["bca_l"]:
            rt = f"{r['asking'] / r['bca_t']:.2f}" \
                if r["asking"] and r["bca_t"] else "—"
            rl = f"{r['asking'] / r['bca_l']:.2f}" \
                if r["asking"] and r["bca_l"] else "—"
            tip = (f"BCA总 {base.money(r['bca_t'])} · "
                   f"BCA地 {base.money(r['bca_l'])}"
                   + (f" · 评估年 {r['bca_yr']}" if r["bca_yr"]
                      else " · 评估年未核"))
            bca_td = (f"<td data-v='{rt if rt != '—' else ''}' "
                      f"title='{tip}'>总 {rt}<br>"
                      f"<span class='stat-note'>地 {rl}</span></td>")
        else:
            bca_td = "<td class='muted'>—</td>"
        body.append(
            f"<tr{cls}><td>{addr}<br><span class='stat-note'>"
            f"{r['band'] or ''}</span></td>"
            f"<td>{r['comm']}</td>"
            f"<td>{base.money(r['asking'])}</td>"
            + bca_td +
            f"<td>{base.money(r['fair'])}</td>"
            f"<td data-v='{r['disc'] if r['disc'] is not None else ''}' "
            f"class='{dc}'>{disc_txt}</td>"
            f"<td>{f'{r[chr(108)+chr(111)+chr(116)]:,.0f}' if r['lot'] else '—'}</td>"
            f"<td>{f'{r[chr(102)+chr(114)+chr(111)+chr(110)+chr(116)+chr(97)+chr(103)+chr(101)]:,.0f}' if r['frontage'] else '—'}</td>"
            f"<td>{r['zoning'] or '—'}</td>"
            + scen_cell(r["cells"]["HOUSE"])
            + scen_cell(r["cells"]["DUPLEX"])
            + scen_cell(r["cells"]["MULTIPLEX"])
            + f"<td>{best_txt}<br>"
            f"<i style='display:inline-block;width:8px;height:8px;"
            f"border-radius:50%;background:{QC[r['quad']]};margin-right:3px'>"
            f"</i><span class='stat-note'>{QUAD_CN[r['quad']]}</span></td></tr>")

    comms = sorted({r["comm"] for r in rows})
    comm_sel = ('<select><option value="">全部社区</option>' +
                "".join(f'<option value="{c}">{c}</option>'
                        for c in comms) + "</select>")
    fams = sorted({(r["zoning"] or "—").split("-")[0][:2]
                   for r in rows})
    zsel = ('<select><option value="">全部zoning</option>' +
            "".join(f'<option value="{f}">{f}系</option>'
                    for f in sorted(fams)) + "</select>")
    roi_sel = ('<select data-kind="roi"><option value="">全部ROI</option>'
               '<option value=">30">≥30%</option>'
               '<option value="20-30">20–30%</option>'
               '<option value="10-20">10–20%</option>'
               '<option value="0-10">0–10%</option>'
               '<option value="<0">负值</option>'
               '<option value="—">缺数据</option></select>')
    quad_sel = ('<select><option value="">全部判定</option>'
                '<option>双优</option><option>仅ROI</option>'
                '<option>仅地价</option><option>—</option></select>')
    fcells = [
        '<td><input placeholder="筛选"></td>',
        f"<td>{comm_sel}</td>",
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        f"<td>{zsel}</td>",
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        '<td><input placeholder="筛选"></td>',
        f"<td>{roi_sel}</td>",
    ]
    head = ("<tr><th>地址<span class='arrow'></span></th>"
            "<th>社区<span class='arrow'></span></th>"
            "<th>挂牌<span class='arrow'></span></th>"
            "<th>BCA对照<br><span class='stat-note'>Ask/总·地</span>"
            "<span class='arrow'></span></th>"
            "<th>合理地价<span class='arrow'></span></th>"
            "<th>市场折价<span class='arrow'></span></th>"
            "<th>地块<span class='arrow'></span></th>"
            "<th>面宽<span class='arrow'></span></th>"
            "<th>zoning<span class='arrow'></span></th>"
            "<th>HOUSE 0.6×<br><span class='stat-note'>整栋新房comps"
            "</span><span class='arrow'></span></th>"
            "<th>DUPLEX 0.7×<br><span class='stat-note'>strata半户"
            "</span><span class='arrow'></span></th>"
            "<th>MULTIPLEX 1.0×<br><span class='stat-note'>strata多户"
            "</span><span class='arrow'></span></th>"
            "<th>最优ROI<span class='arrow'></span></th></tr>"
            f"<tr class='filters'>{''.join(fcells)}</tr>")

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store,
must-revalidate">
<link rel="stylesheet"
href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>{CSS}</style>
<title>V1.4.3 逐房源估值 — 224 ACTIVE HOUSE · 2026-10-20</title>
</head><body>
<div style="background:#e8f0fe;border:1px solid #4285f4;padding:8px 12px;
margin:6px 0;font-size:13px;border-radius:4px">
<b>V1.4.3 逐房源估值(研究层 · V3s 生产零修改):</b>
每套房源独立回答——土地合理市价(同社区/同面积段土地型成交)、
Ask 折价、合法可建方案(V3s verified 几何)、个性化新房估售、
统一 <b>1 年开发融资窗口</b> ROI、10/20/30% 目标反推最高买入价。
新房匹配已修社区别名(500m→COMM→SIDE);未来涨跌基准 0%,
±10/20% 仅压力情景。置信度仅展示不过滤。
<span class="badge">ROI≥10%: {n10}</span>
<span class="badge">≥20%: {n20}</span>
<span class="badge">≥30%: {n30}</span>
快照 2026-10-20 · HPI 2026-09 · 成交截至 2026-07-08
</div>
<div id="xbar" style="margin:6px 0;font-size:12px">扩展筛选:
<select data-x="prod"><option value="">全部产品</option>
<option value="HOUSE">有HOUSE价</option>
<option value="DUPLEX">有DUPLEX价</option>
<option value="MPX">有MPX价</option></select>
<select data-x="quad"><option value="">全部判定</option>
<option value="BOTH">双优</option><option value="ROI_ONLY">仅ROI</option>
<option value="LAND_VALUE">仅地价</option>
<option value="NEITHER">双缺</option></select>
<select data-x="conf"><option value="">全部置信</option>
<option value="low">含低置信</option>
<option value="ok">无低置信</option></select>
</div>
<h1>逐房源估值与重建 ROI — V1.4.3 · 224 套 ACTIVE HOUSE</h1>
<div class="viewbar"><button id="btn-list" class="vbtn on">列表</button>
<button id="btn-map" class="vbtn">地图</button>
<span class="map-legend"><i style="background:{QC['BOTH']}"></i>双优
<i style="background:{QC['ROI_ONLY']}"></i>仅ROI
<i style="background:{QC['LAND_VALUE']}"></i>仅地价
<i style="background:{QC['NEITHER']}"></i>双缺</span>
<span class="map-legend">· 地图与列表筛选联动,显示
<b id="mapcount"></b> 点</span></div>
<div id="mapview"><div id="map"></div></div>
<div id="listview">
<div class="sub">对象:224 套 ACTIVE HOUSE(真实 Ask,2026-10-20)。
「BCA对照」=Ask/BCA总估值(上)·Ask/BCA土地估值(下),悬停见
BCA 总值/土地值/评估年——政府估值口径,与「合理地价」独立。
「合理地价」=逐房源土地型旧房可比匹配(MICRO_500M→COMM_LOTBAND
→…→SIDE)市场估值。「市场折价」=(合理地价−Ask)/合理地价:
低于估值=正,高于估值=负,为买入性价比主指标。方案格:建面/
估售/匹配单价层级/1y ROI/20%目标上限;不适用=法规不合,
缺comps=合法但无可售证据。绿底行=最优 ROI≥10%。<b>表头点击
排序;第二行筛选</b>(数值表达式 &gt;30 / &lt;0.8 / 20-30 可用)。
当前显示 <b id="viscount">{len(rows)}</b> 行。</div>
{hood_table}
<table id="proptable"><thead>{head}</thead>
<tbody>{''.join(body)}</tbody></table></div>
<script>
const MAP_POINTS = {json.dumps(points, ensure_ascii=False)};
const META = {json.dumps(meta, ensure_ascii=False)};
const QC = {json.dumps(QC)};
{MAP_JS}
{TABLE_JS}
</script></body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"{OUT} rows={len(rows)} points={len(points)} "
          f"roi>10/20/30={n10}/{n20}/{n30}")


def num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
