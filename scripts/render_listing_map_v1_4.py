#!/usr/bin/env python3
"""Render the V1.4 listing-level results as a List/Map HTML page.

Reads the committed audit CSVs + the listing snapshot for coordinates.
Output: docs/audits/listing_level_valuation_v1_4/listing_map.html
Style follows reports/watchlist/rebuild-value-*.html (Leaflet toggle).
"""
import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs/audits/listing_level_valuation_v1_4"
LISTINGS = ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"
OUT = D / "listing_map.html"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(v):
    return "—" if v in (None, "") else "$" + format(int(round(v)), ",")


def pct1(v):
    return "—" if v in (None, "") else f"{v:.1f}%"


def main():
    land = {r["listing"]: r for r in csv.DictReader(
        open(D / "active_house_land_valuation.csv"))}
    roi = [r for r in csv.DictReader(
        open(D / "active_house_listing_roi.csv"))
        if r["gst_scenario"] == "EXCL"]
    bid = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_max_bid_and_negotiation.csv"))}
    scen = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D / "active_house_buildable_scenarios.csv"))}
    geo = {r["address"]: r for r in json.load(open(LISTINGS))["rows"]}

    best = {}
    for r in roi:
        k = r["listing"]
        if k not in best or float(r["roi_development_1y"]) > \
                float(best[k]["roi_development_1y"]):
            best[k] = r
    # include listings with no priced/verified scenario as NEITHER
    for k in land:
        if k not in best:
            best[k] = {"listing": k, "scenario": "—",
                       "asking": land[k].get("asking", ""),
                       "est_sale_today": "", "roi_development_1y": "",
                       "roi_simple": "", "land_conf": land[k]["confidence"],
                       "sale_conf": "BLOCKED"}

    rows, points = [], []
    for k, r in best.items():
        l = land.get(k, {})
        g = geo.get(k, {})
        b = bid.get((k, r["scenario"]), {})
        s = scen.get((k, r["scenario"]), {})
        land_disc = num(l.get("discount_to_land_market_pct"))
        roi_v = num(r["roi_development_1y"])
        land_ok = land_disc is not None and land_disc > 0
        roi_ok = roi_v is not None and roi_v >= 10
        quad = ("BOTH" if land_ok and roi_ok else
                "LAND_VALUE" if land_ok else
                "ROI_ONLY" if roi_ok else "NEITHER")
        conf = f"{r['land_conf'][:4]}/{r['sale_conf'][:4]}"
        rows.append({
            "k": k, "addr": k, "hood": l.get("hood_comm", g.get("hood")),
            "zoning": l.get("zoning", g.get("zoning", "")),
            "lot": num(l.get("lot_sqft")), "asking": num(r["asking"]),
            "fair": num(l.get("fair_land_value_today")),
            "disc": land_disc, "scenario": r["scenario"],
            "build": num(s.get("build_sqft")),
            "sale": num(r["est_sale_today"]),
            "roi": roi_v, "simple": num(r["roi_simple"]),
            "mb20": b.get("max_bid_roi_20"),
            "mb30": b.get("max_bid_roi_30"),
            "quad": quad, "conf": conf, "n": l.get("land_comp_n"),
            "lat": num(g.get("lat")), "lng": num(g.get("lng")),
            "link": g.get("link", "")})
    rows.sort(key=lambda r: -(r["roi"] or -999))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        if r["lat"] and r["lng"]:
            points.append({
                "k": r["k"], "addr": r["addr"], "hood": r["hood"],
                "lat": r["lat"], "lng": r["lng"], "asking": r["asking"],
                "fair": r["fair"], "disc": r["disc"], "roi": r["roi"],
                "scen": r["scenario"], "quad": r["quad"],
                "zoning": r["zoning"], "mb20": r["mb20"],
                "sale": r["sale"], "rank": r["rank"]})

    QC = {"BOTH": "#2e8b57", "ROI_ONLY": "#2456a6",
          "LAND_VALUE": "#e6a23c", "NEITHER": "#8a94a3"}
    body = []
    for r in rows:
        rc = "roi-pos" if (r["roi"] or 0) >= 10 else \
            ("roi-neg" if (r["roi"] or 0) < 0 else "")
        dc = "roi-pos" if (r["disc"] or 0) > 0 else "roi-neg"
        mb = r["mb20"] if r["mb20"] not in ("", None) else "—"
        if mb not in ("—", "UNATTAINABLE"):
            mb = money(num(mb))
        body.append(
            f"<tr class='{r['quad'].lower()}'>"
            f"<td>{r['rank']}</td>"
            f"<td><a href='{html.escape(r['link'])}'>{html.escape(r['addr'])}</a>"
            f"<br><span class='sub'>{r['hood']} · {r['zoning']}</span></td>"
            f"<td>{money(r['asking'])}</td>"
            f"<td>{money(r['fair'])}</td>"
            f"<td class='{dc}'>{pct1(r['disc'])}</td>"
            f"<td>{r['scenario']}"
            f"<br><span class='sub'>"
            f"{format(r['build'], ',.0f') + 'sf' if r['build'] else '—'}"
            f"</span></td>"
            f"<td>{money(r['sale'])}</td>"
            f"<td class='{rc}'>{pct1(r['roi'])}</td>"
            f"<td class='{rc}'>{pct1(r['simple'])}</td>"
            f"<td>{mb}</td>"
            f"<td><i style='background:{QC[r['quad']]}' "
            f"class='dot'></i>{r['quad'].replace('_',' ')}</td>"
            f"<td class='sub'>{r['conf']}</td>"
            f"<td class='sub'>{r['n']}</td></tr>")

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>V1.4.3 Listing Map — 224 ACTIVE HOUSE</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body{{font:13px/1.5 -apple-system,"Helvetica Neue",Arial,"PingFang SC",sans-serif;margin:14px;color:#222}}
h1{{font-size:19px;margin:0 0 2px}}
.sub{{color:#777;font-size:11px}}
.roi-pos{{color:#0a7a3f;font-weight:700}}
.roi-neg{{color:#b3261e;font-weight:700}}
.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}}
table{{border-collapse:collapse;width:100%;margin-top:8px}}
th,td{{border-bottom:1px solid #e5e5e5;padding:5px 7px;text-align:right;white-space:nowrap}}
th{{background:#f6f7f9;cursor:pointer;user-select:none;position:sticky;top:0}}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){{text-align:left}}
tr.filters input{{width:100%;box-sizing:border-box;font-size:11px;padding:3px 5px;border:1px solid #ddd;border-radius:4px}}
tr.both td{{background:#f2f8f3}}
.viewbar{{display:flex;align-items:center;gap:8px;margin:10px 0;flex-wrap:wrap}}
.vbtn{{padding:6px 18px;font-size:14px;border:1px solid #2456a6;background:#fff;color:#2456a6;border-radius:4px;cursor:pointer}}
.vbtn.on{{background:#2456a6;color:#fff}}
.legend{{font-size:12px;color:#555}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 4px 0 12px}}
#mapview{{display:none}}
#map{{height:78vh;border:1px solid #ccc;border-radius:6px}}
.leaflet-popup-content{{font-size:13px;line-height:1.5}}
.leaflet-popup-content .pp-title{{font-weight:700;font-size:14px}}
.leaflet-popup-content .pp-roi{{color:#0a7a3f;font-weight:700}}
.badge{{background:#eef2f8;border-radius:10px;padding:2px 9px;margin-right:6px;font-size:12px}}
</style></head><body>
<h1>V1.4.3 逐房源估值 · 列表/地图</h1>
<div class="sub">224 套 ACTIVE HOUSE · 挂牌快照 2026-10-20 · HPI 2026-09 基准 ·
1 年开发融资窗口 · 0% 未来涨跌 · 数据源 sales_archive_v1_0_3 + hpi_v1_2 +
V3s 方案几何。绿色行 = 地价与 ROI 双优(BOTH)。置信度列 land/sale,
LOW 已展示不过滤。</div>
<div class="viewbar">
<button id="btn-list" class="vbtn on">列表</button>
<button id="btn-map" class="vbtn">地图</button>
<span class="legend"><i style="background:{QC['BOTH']}"></i>BOTH
<i style="background:{QC['ROI_ONLY']}"></i>ROI ONLY
<i style="background:{QC['LAND_VALUE']}"></i>LAND VALUE
<i style="background:{QC['NEITHER']}"></i>NEITHER</span>
<span class="badge">ROI≥10%: 74</span><span class="badge">≥20%: 15</span>
<span class="badge">≥30%: 2</span>
<span class="badge" id="mapcount"></span>
</div>
<div id="listview"><table id="proptable"><thead>
<tr class="filters"><td></td>"""
    for _ in range(11):
        doc += '<td><input placeholder="筛选…"></td>'
    doc += "<td></td></tr><tr>" + "".join(
        f"<th>{h}<span class='arrow'></span></th>" for h in
        ("#", "地址/社区", "Ask", "合理地价", "地价差", "方案",
         "估售(今)", "ROI 1y", "简单ROI", "20%上限", "判定", "置信", "comps")
    ) + "</tr></thead><tbody>" + "\n".join(body) + """</tbody></table></div>
<div id="mapview"><div id="map"></div></div>
<script>
const MAP_POINTS = """ + json.dumps(points, ensure_ascii=False) + """;
const QC = """ + json.dumps(QC) + """;
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
        m.bindPopup('<div class="pp-title">#' + p.rank + ' ' + p.addr + '</div>'
            + '<div>' + p.hood + ' · ' + p.zoning + ' · ' + p.quad + '</div>'
            + '<div>Ask ' + fmtM(p.asking) + ' · 地价 ' + fmtM(p.fair)
            + ' (' + fmtP(p.disc) + ')</div>'
            + '<div>' + p.scen + ' 估售 ' + fmtM(p.sale) + '</div>'
            + '<div class="pp-roi">ROI1y ' + fmtP(p.roi)
            + ' · 20%上限 ' + fmtM(p.mb20) + '</div>');
        bounds.push([p.lat, p.lng]);
    }
    document.getElementById('mapcount').textContent =
        '点位 ' + pts.length + ' / ' + MAP_POINTS.length;
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
// sorting + filtering (same UX as rebuild-value pages)
const table = document.getElementById('proptable');
const tbody = table.querySelector('tbody');
const dataRows = Array.from(tbody.querySelectorAll('tr'));
let sortCol = -1, sortDir = 1;
const cellVal = (row, i) => row.cells[i] ? row.cells[i].innerText.trim() : '';
function numOf(t) {
    const m = t.replace(/[$,%\\s]/g, '').match(/-?\\d+\\.?\\d*/);
    return m ? parseFloat(m[0]) : null;
}
function sortBy(i) {
    if (sortCol === i) sortDir *= -1; else { sortCol = i; sortDir = 1; }
    table.querySelectorAll('th .arrow').forEach(a => a.textContent = '');
    table.querySelectorAll('th')[i].querySelector('.arrow').textContent =
        sortDir === 1 ? '▲' : '▼';
    dataRows.sort((a, b) => {
        const ta = cellVal(a, i), tb = cellVal(b, i);
        const na = numOf(ta), nb = numOf(tb);
        if (na !== null && nb !== null) return (na - nb) * sortDir;
        return ta.localeCompare(tb, 'zh') * sortDir;
    });
    dataRows.forEach(r => tbody.appendChild(r));
}
table.querySelectorAll('th').forEach((th, i) => th.onclick = () => sortBy(i));
function applyFilters() {
    const inputs = table.querySelectorAll('tr.filters input');
    const vis = new Set();
    dataRows.forEach(r => {
        let ok = true;
        inputs.forEach((inp, j) => {
            const q = inp.value.trim();
            if (!q) return;
            const t = cellVal(r, j + 1);
            const cmp = q.match(/^(>=|<=|>|<)?\\s*(.+)$/);
            const val = cmp[2].toLowerCase(), op = cmp[1];
            const n = numOf(t);
            if (n !== null && /^-?\\d/.test(val)) {
                const v = parseFloat(val.replace(/,/g, ''));
                ok &= op === '>' ? n > v : op === '<' ? n < v :
                       op === '>=' ? n >= v : op === '<=' ? n <= v :
                       Math.abs(n - v) < Math.abs(v) * 0.05 + 0.5;
            } else {
                ok &= t.toLowerCase().includes(val);
            }
        });
        r.style.display = ok ? '' : 'none';
        if (ok) vis.add(r.cells[1].innerText.split('\\n')[0].trim());
    });
    // map points keyed by listing address shown in cell link text
    window.__visKeys = new Set([...vis].flatMap(() => []));
    window.__visKeys = null;
    const visAddrs = new Set();
    dataRows.forEach(r => {
        if (r.style.display !== 'none')
            visAddrs.add(r.querySelector('td a') ? r.querySelector('td a').textContent : '');
    });
    window.__visKeys = visAddrs;
    if (_map) drawPoints();
}
table.querySelectorAll('tr.filters input').forEach(i =>
    i.addEventListener('input', applyFilters));
</script></body></html>"""
    OUT.write_text(doc)
    print(f"{OUT} rows={len(rows)} points={len(points)}")


if __name__ == "__main__":
    main()
