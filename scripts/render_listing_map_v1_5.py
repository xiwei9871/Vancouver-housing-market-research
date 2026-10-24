#!/usr/bin/env python3
"""V1.5 listing map — same layout/JS as the confirmed V1.4.3 page, with
V1.5 numbers: ANCHOR sale (P10/P50/P90), BCA-calibrated land with
comps cross-check, five-state legality, C01-C10 audit column and an
audit table with JSON export/import of review status.

Community table uses the dims-corrected land pool (the same pool as
per-listing land) — fixes the V1.4.3 C09 lineage defect.
Run: python3 scripts/render_listing_map_v1_5.py   (after v1_5 pipeline)
"""
import html
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import listing_level_valuation_v1_4 as v   # noqa: E402
import listing_valuation_v1_5 as v15       # noqa: E402
import render_listing_map_v1_4 as r14      # noqa: E402
import render_market_analytics as base     # noqa: E402

OUT = v15.OUT / "listing_map.html"
QC, QUAD_CN = {"BOTH": "#2e8b57", "ROI_ONLY": "#2456a6",
               "LAND_VALUE": "#e6a23c", "NEITHER": "#8a94a3"}, {
    "BOTH": "双优", "ROI_ONLY": "仅ROI", "LAND_VALUE": "仅地价",
    "NEITHER": "—"}
STATE_CN = {"VERIFIED": "已核实", "CONDITIONAL": "需审批/放宽",
            "ELIGIBLE": "条件满足·未核", "NOT_ELIGIBLE": "常规不满足",
            "NO_RULE": "无规则"}
CSS = r14.CSS + """
.st{display:inline-block;padding:0 5px;border-radius:3px;font-size:10px}
.st-CONDITIONAL{background:#fdf3e0;color:#b46a00}
.st-ELIGIBLE{background:#eef2f8;color:#2456a6}
.cx{display:inline-block;background:#fde8e6;color:#b3261e;border-radius:3px;
padding:0 4px;margin:1px;font-size:10px;cursor:help}
.cx-DEFECT{background:#b3261e;color:#fff}
.band{color:#666;font-size:11px}
.method{background:#f7f6f3;border:1px solid #e8e6e1;border-radius:6px;
padding:10px 14px;font-size:12.5px;margin:10px 0;max-width:1300px}
.method table{width:auto;font-size:12px}
.tools button{margin-right:6px;padding:4px 10px;font-size:12px}
"""


def m(x):
    return base.money(x) if x else "—"


def pct(x):
    return "—" if x is None else f"{x:.1f}%"


def scen_cell(sc):
    if sc is None:
        return "<td class='muted' data-v=''>—</td>"
    st = sc["state"]
    tag = (f"<span class='st st-{st}' title='{html.escape(sc['why'])}'>"
           f"{STATE_CN[st]}</span>")
    if st in ("NOT_ELIGIBLE", "NO_RULE"):
        return f"<td class='muted' data-v=''>不适用<br>{tag}</td>"
    if not sc.get("est"):
        return f"<td class='muted' data-v=''>无售价证据<br>{tag}</td>"
    units = int(sc["units"] or 1)
    roi, roi10 = sc.get("roi"), sc.get("roi_p10")
    if sc["method"] != "ANCHOR":
        return (f"<td data-v='{roi:.1f}'>建面{sc['build_sqft']:,.0f}sf·"
                f"{units}户<br>估售{m(sc['est'])}<br>"
                f"<span class='stat-note'>单户价代理·{sc['level']}·n{sc['n']}"
                f"·未回测,不参与排序</span><br><span class='muted'>"
                f"ROI1y {roi:.1f}%</span><br>{tag}</td>")
    cls = "roi-pos" if roi >= 10 else "roi-neg"
    cls10 = "roi-pos" if (roi10 or -1) >= 0 else "roi-neg"
    near = (f"·近{sc['n_near']}笔×{sc['near_factor']:.2f}"
            if sc.get("n_near") else "")
    v14 = (f"<br><span class='stat-note'>V1.4 ${sc['v14_psf']:,.0f}/sf"
           f"·{sc['v14_level']}</span>" if sc.get("v14_psf") else "")
    return (
        f"<td data-v='{roi:.1f}'>建面{sc['build_sqft']:,.0f}sf"
        f"{'·' + str(units) + '户' if units != 1 else ''}"
        f"<br>估售{m(sc['est'])}"
        f"<br><span class='band'>P10 {m(sc['p10'])} · P90 {m(sc['p90'])}"
        f"</span><br><span class='stat-note'>@${sc['psf']:,.0f}/sf·"
        f"社区n{sc['n']}{near}</span>{v14}"
        f"<br><span class='{cls}'>ROI1y {roi:.1f}%</span> "
        f"<span class='{cls10}' title='历史误差P10压力情景(非概率保证)'>压力 {pct(roi10)}</span>"
        f"<br><span class='band'>售价−10% {pct(sc.get('roi_m10'))} · −20% "
        f"{pct(sc.get('roi_m20'))}</span>"
        + ("" if st == "VERIFIED" else f"<br>{tag}")
        + (f"<br><span class='st st-CONDITIONAL' title='单户面积 "
           f"{sc['unit_area']:,.0f}sf 超出回测样本 P95,误差未经验证'>"
           "⚠超历史样本面积</span>" if sc.get("out_of_support") else "")
        + "</td>")


def community_table(rows):
    hr = list(v.csv.DictReader(open(v.V12 / "house_price_normalized_per_row.csv")))
    tax = v.load_tax()
    pool, _, _ = v.build_land_pool(hr, v.load_archive(), v.load_resolved_yb(),
                                   tax, v.load_lot_dims())
    r_comm, _ = v15.bca_ratio_tables(pool, tax)
    by = defaultdict(list)
    for r in pool:
        by[r["community"]].append(r)
    listed = defaultdict(list)
    for r in rows:
        listed[r["comm"]].append(r)
    med = statistics.median
    out = []
    for c in sorted(by, key=lambda c: -med([r["price_2026_09"] for r in by[c]])):
        rs = by[c]
        p50 = med([r["price_2026_09"] for r in rs])
        asks = [r["asking"] for r in listed.get(c, [])]
        best = Counter(r["best"] for r in listed.get(c, []) if r["best"])
        ratio = r_comm.get(c)
        out.append(
            f"<tr><td>{c}</td><td>{len(rs)}</td><td><b>{m(p50)}</b></td>"
            f"<td>${med([r['land_psf'] for r in rs]):,.0f}</td>"
            f"<td>{med([r['lot'] for r in rs]):,.0f}</td>"
            f"<td>{f'{ratio[0]:.3f}' if ratio else '—'}</td>"
            f"<td>{len(asks)}</td><td>{m(med(asks)) if asks else '—'}</td>"
            f"<td>{f'{med(asks) / p50:.2f}' if asks else '—'}</td>"
            f"<td>{'/'.join(f'{k[0]}{n}' for k, n in best.most_common()) or '—'}"
            f"</td></tr>")
    return (
        "<h2>社区均价(土地主导旧房·age≥50·BCA土地占比≥95%·<b>BCA尺寸纠偏后"
        "</b>·归一化2026-09)</h2><div class='stat-note'>与逐房源地价同一"
        "池(V1.4.3 社区表误用未纠偏旧池,已修)。成交/BCA地 = 该社区土地型"
        "成交价÷2026 BCA 土地值中位,用于 BCA 校准地价。</div>"
        "<table><tr><th>社区</th><th>土地型成交n</th><th>社区均价P50</th>"
        "<th>土地$/sf P50</th><th>lot P50</th><th>成交/BCA地</th>"
        "<th>挂牌n</th><th>挂牌Ask中位</th><th>Ask/均价</th>"
        "<th>最优形态</th></tr>" + "".join(out) + "</table>")


def method_box(summary):
    bs = json.load(open(v15.OUT / "backtest_summary.json"))

    def line(key, mth):
        x = bs["sale"][key]["methods"].get(mth, {})
        return (f"<td>{x.get('n', '—')}</td><td>{x.get('mdape', 0):.1%}</td>"
                f"<td>{x.get('p10_ratio', 0):.2f}</td>"
                f"<td>{x.get('p90_ratio', 0):.2f}</td>")
    st = bs["strict_v1_5_1"]

    def cell(x):
        return (f"<td>{x['n']}</td><td>{x['mdape']:.1%}</td>"
                f"<td>{x['p10_ratio']:.2f}</td>")
    trs = ""
    for p in ("HOUSE", "DUPLEX"):
        lo = bs["sale"][p + "|ALL"]["methods"]
        fw = st[p]["forward_expanding"]
        dv = st[p]["devin_protocol_2025_07_01"]["methods"]
        for mm, f2, d2 in (("V14", "V14", "V14"),
                           ("ANCHOR", "ANCHOR", "ANCHOR_SELECTED")):
            trs += (f"<tr><td>{p}</td><td>{mm}</td>{cell(lo[mm])}"
                    f"{cell(fw[f2])}{cell(dv[d2])}</tr>")
    ld = bs["land"]
    _lt = "".join(
        f"<tr><td>LAND</td><td>{k}</td><td>{ld[k]['n']}</td>"
        f"<td>{ld[k]['mdape']:.1%}</td><td>{ld[k]['p10_ratio']:.2f}</td>"
        f"<td>{ld[k]['p90_ratio']:.2f}</td>"
        f"<td colspan=4>post-BCA估值日 MdAPE "
        f"{ld[k + '|post_bca_valdate']['mdape']:.1%} "
        f"(n{ld[k + '|post_bca_valdate']['n']})</td></tr>"
        for k in ("V14_LAND", "BCA_CAL", "BLEND"))
    return (
        "<div class='method'><b>估值方法与回测(留一地块交叉验证 · "
        "ratio=实际/估价 · 2026-09 归一化)</b><br>"
        "售价 = <b>社区基准 $/sf</b>(向东/西区中位收缩,K=5)× <b>附近修正</b>"
        "(0.6km 内成交相对其社区基准,按样本量收缩 K2=6,上限 ±15%)× "
        f"<b>拟合面积弹性</b>(HOUSE k={summary['k_fit']['HOUSE']:.2f},"
        f"DUPLEX k={summary['k_fit']['DUPLEX']:.2f};V1.4 HOUSE 用 −0.55)。"
        "区间 P10/P90 = 回测实际/估价分位;<b>P10 ROI = 下行情景</b>。"
        "地价 = BCA 2026 土地值 × 社区成交/BCA地中位;V1.4 comps 地价作交叉校验。"
        "<table><tr><th>产品</th><th>方法</th><th>留一 n</th><th>MdAPE</th>"
        "<th>P10比</th><th>前向滚动 n</th><th>MdAPE</th><th>P10比</th>"
        "<th>嵌套选参@2025-07-01 n</th><th>MdAPE</th><th>P10比</th></tr>"
        + trs + "</table>"
        f"<b>土地(BCA 按成交年份取当年 roll,比率只用更早成交)</b>:BCA校准 "
        f"MdAPE {st['LAND']['BCA_CAL_ASOF']['mdape']:.1%} vs comps "
        f"{st['LAND']['V14_LAND_FWD']['mdape']:.1%}(n{st['LAND']['BCA_CAL_ASOF']['n']})。"
        "MdAPE=|估价−实际|/实际。<b>压力情景</b>=估售×前向滚动回测的实际/估价"
        "P10 比值;是历史误差分位,不是单套房概率保证。单户面积超回测样本 P95"
        f"(HOUSE {summary['area_support_p95']['HOUSE']:,}sf / DUPLEX "
        f"{summary['area_support_p95']['DUPLEX']:,}sf)标注 ⚠ 但<b>仍参与完整研究排名</b>;"
        "「样本覆盖内」视图可在扩展筛选切换。<br>"
        "<b>ROI≥10% 变化拆解</b>:" + " → ".join(
            f"{lab} {summary['roi_bridge'][k][10]}" for lab, k in (
                ("V1.4.4", "roi_s0_v144"), ("仅VERIFIED", "roi_s1_legal"),
                ("+V1.5售价 = 完整研究排名", "roi_s2_sale")))
        + f";样本覆盖内视图 {summary['roi_bridge']['roi_s3_support'][10]}"
        "(逐房源见 v15_roi_bridge.csv)<br>"
        "<span class='stat-note'>限定:ANCHOR 默认参数曾在全样本留一上比较选出,"
        "前向滚动成绩非完全独立;嵌套选参列为更强证据。土地前向检验按成交年取"
        "BCA roll,但成交价已经 HPI 归一到 2026-09,并非当时可得信息的真实模拟。"
        "</span><br>"
        "<span class='stat-note'>未解决:回测样本即 comp 池本身(同源);"
        "MULTIPLEX 无建面无法回测,仅展示不排序;成交截 ~2026-07-08,"
        "非当前现价;建造成本/产品配置未回测。</span></div>")


def main():
    rows, contra, summary = v15.main()
    cx_by = defaultdict(list)
    for c in contra:
        cx_by[c["listing"]].append(c)
        if c["other"]:
            cx_by[c["other"]].append(c)
    for r in rows:
        land_ok = r["disc"] is not None and r["disc"] > 0
        roi_ok = r["roi"] is not None and r["roi"] >= 10
        r["quad"] = ("BOTH" if land_ok and roi_ok else "LAND_VALUE"
                     if land_ok else "ROI_ONLY" if roi_ok else "NEITHER")
    rows.sort(key=lambda r: (r["roi"] is None, -(r["roi"] or -999)))
    body, meta, points = [], [], []
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        cxs = cx_by.get(r["listing"], [])
        meta.append({"quad": r["quad"], "roi": rd(r["roi"], 1),
                     "roi10": rd(r["roi_p10"], 1),
                     "oos": r["best_oos"], "roi_in": r["roi_in"],
                     "has_h": bool((r["scen"].get("HOUSE") or {}).get("roi")),
                     "has_d": bool((r["scen"].get("DUPLEX") or {}).get("roi")),
                     "has_m": bool((r["scen"].get("MULTIPLEX") or {}).get("roi")),
                     "cx": len(cxs), "addr": r["listing"]})
        best = r["scen"].get(r["best"]) if r["best"] else None
        if r["lat"] and r["lng"]:
            points.append({"k": i, "addr": r["listing"], "hood": r["comm"],
                           "lat": r["lat"], "lng": r["lng"],
                           "asking": r["asking"], "fair": rd(r["fair_land"]),
                           "comm_avg": None, "disc": rd(r["disc"], 1),
                           "roi": rd(r["roi"], 1), "scen": r["best"] or "—",
                           "quad": r["quad"], "zoning": r["zoning"],
                           "sale": rd(best["est"]) if best else None,
                           "rank": r["rank"]})
        link = (r["link"] or "").replace("../calibration/",
                                         "/reports/calibration/")
        addr = (f'<a href="{html.escape(link)}">{html.escape(r["listing"])}</a>'
                if link else html.escape(r["listing"]))
        rt = r["asking"] / r["bca"] if r["bca"] else None
        rl = r["asking"] / r["bca_land"] if r["bca_land"] else None
        disc = r["disc"]
        dtxt = "—" if disc is None else (f"低于估值 {disc:.1f}%" if disc > 0
                                         else f"高于估值 {-disc:.1f}%")
        cdisc = r["comps_disc"]
        cross = "" if cdisc is None else (
            f"<br><span class='stat-note'>comps {m(r['comps_fair'])}"
            f" ({cdisc:+.1f}%)</span>")
        if r["roi"] is not None:
            bt = (f"<span class='roi-pos'>{r['roi']:.0f}% {r['best']}</span>"
                  f"<br><span class='{'roi-pos' if (r['roi_p10'] or -1) >= 0 else 'roi-neg'}'"
                  f" title='历史误差P10压力情景'>压力 {pct(r['roi_p10'])}</span>"
                  f"<br><span class='band'>−10% {pct(r['roi_m10'])} · −20% "
                  f"{pct(r['roi_m20'])}</span>"
                  + ("<br><span class='st st-CONDITIONAL'>⚠超样本面积</span>"
                     + (f"<br><span class='stat-note'>覆盖内最优 {r['best_in']} "
                        f"{r['roi_in']:.0f}%</span>" if r["best_in"] else "")
                     if r["best_oos"] else ""))
        else:
            bt = "—"
        cb = r["cond_best"]
        if cb and (r["roi"] is None or cb["roi"] > r["roi"]):
            bt += (f"<br><span class='st st-CONDITIONAL'>待审 {cb['name']} "
                   f"{cb['roi']:.0f}%</span>")
        cx_html = "".join(
            f"<span class='cx cx-{c['severity']}' title='"
            f"{html.escape(c['detail'], quote=True)}'>{c['code']}</span>"
            for c in cxs) or "<span class='muted'>—</span>"
        body.append(
            f"<tr{' class=best' if r['roi'] is not None and r['roi'] >= 10 else ''}"
            f" data-k='{i}'><td>{addr}</td><td>{r['comm']}</td>"
            f"<td>{m(r['asking'])}</td>"
            f"<td data-v='{rt or ''}' title='BCA总 {m(r['bca'])} · BCA地 "
            f"{m(r['bca_land'])}'>总 {f'{rt:.2f}' if rt else '—'}<br>"
            f"<span class='stat-note'>地 {f'{rl:.2f}' if rl else '—'}</span></td>"
            f"<td data-v='{rd(r['fair_land'])}'>{m(r['fair_land'])}<br>"
            f"<span class='band'>{m(r['fair_land_p10'])}–{m(r['fair_land_p90'])}"
            f"</span><br><span class='stat-note'>{r['land_src']}·"
            f"{r['bca_ratio_lvl']}n{r['bca_ratio_n']}</span></td>"
            f"<td data-v='{rd(disc, 1)}' class='{'roi-pos' if (disc or 0) > 0 else 'roi-neg'}'>"
            f"{dtxt}{cross}</td>"
            f"<td>{f'{r[chr(108)+chr(111)+chr(116)]:,.0f}' if r['lot'] else '—'}</td>"
            f"<td>{f'{r[chr(102)+chr(114)+chr(111)+chr(110)+chr(116)+chr(97)+chr(103)+chr(101)]:,.0f}' if r['frontage'] else '—'}</td>"
            f"<td>{r['zoning'] or '—'}</td>"
            + scen_cell(r["scen"].get("HOUSE"))
            + scen_cell(r["scen"].get("DUPLEX"))
            + scen_cell(r["scen"].get("MULTIPLEX"))
            + f"<td data-v='{rd(r['roi'], 1)}'>{bt}<br><i style='display:inline-block;"
            f"width:8px;height:8px;border-radius:50%;background:{QC[r['quad']]}'>"
            f"</i><span class='stat-note'>{QUAD_CN[r['quad']]}</span></td>"
            f"<td>{cx_html}</td></tr>")
    return render(rows, body, meta, points, contra, summary)


def rd(x, n=0):
    return None if x is None else round(x, n) if n else round(x)


AUDIT_JS = """
// latest definition wins: V1.5 xbar adds P10 / contradiction filters
function xbarOk(meta) {
    const x = window.__xf || {};
    if (x.quad && meta.quad !== x.quad) return false;
    if (x.prod === 'HOUSE' && !meta.has_h) return false;
    if (x.prod === 'DUPLEX' && !meta.has_d) return false;
    if (x.prod === 'MPX' && !meta.has_m) return false;
    if (x.p10 === 'pos' && !(meta.roi10 != null && meta.roi10 >= 0)) return false;
    if (x.sup === 'in' && meta.oos) return false;
    if (x.sup === 'oos' && !meta.oos) return false;
    if (x.cx === 'any' && !meta.cx) return false;
    if (x.cx === 'none' && meta.cx) return false;
    return true;
}
const AKEY = 'v15_audit_status';
function loadStatus() {
    try { return JSON.parse(localStorage.getItem(AKEY) || '{}'); }
    catch (e) { return {}; }
}
function saveStatus(s) { localStorage.setItem(AKEY, JSON.stringify(s)); }
function paintAudit() {
    const s = loadStatus();
    document.querySelectorAll('#audittable tbody tr').forEach(tr => {
        const id = tr.dataset.id, st = s[id] || {};
        tr.querySelector('select').value = st.status || 'OPEN';
        tr.querySelector('input').value = st.note || '';
    });
}
document.querySelectorAll('#audittable tbody tr').forEach(tr => {
    const upd = () => {
        const s = loadStatus();
        s[tr.dataset.id] = {status: tr.querySelector('select').value,
                            note: tr.querySelector('input').value};
        saveStatus(s);
    };
    tr.querySelector('select').addEventListener('change', upd);
    tr.querySelector('input').addEventListener('input', upd);
});
function download(name, obj) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 1)],
        {type: 'application/json'}));
    a.download = name; a.click();
}
document.getElementById('btn-export-all').onclick = () => {
    const s = loadStatus();
    download('v15_export_all.json', {model: META_INFO, listings: META,
        contradictions: CONTRA.map(c => Object.assign({}, c, s[c.id] || {}))});
};
document.getElementById('btn-export-audit').onclick = () => {
    const s = loadStatus();
    download('v15_audit_review.json',
        CONTRA.map(c => Object.assign({}, c, s[c.id] || {})));
};
document.getElementById('btn-import').onclick = () =>
    document.getElementById('file-import').click();
document.getElementById('file-import').onchange = ev => {
    const f = ev.target.files[0]; if (!f) return;
    f.text().then(t => {
        const d = JSON.parse(t);
        const arr = Array.isArray(d) ? d : (d.contradictions || []);
        const s = loadStatus(); let n = 0;
        arr.forEach(c => { if (c.id && c.status) {
            s[c.id] = {status: c.status, note: c.note || ''}; n++; } });
        saveStatus(s); paintAudit();
        alert('导入 ' + n + ' 条审查状态');
    });
};
paintAudit();
"""


def render(rows, body, meta, points, contra, summary):
    CN = {"C01": "BCA低但模型判溢价", "C02": "comps地价≠BCA校准>20%",
          "C03": "土地溢价+高ROI", "C04": "相似地块地价差>$500k",
          "C05": "相似方案ROI差≥30pp", "C06": "V1.4→V1.5 单价跳变>30%",
          "C07": "法规输出自相矛盾", "C08": "状态与估价矛盾",
          "C09": "汇总/逐房源口径", "C10": "排序筛选一致性"}
    cnt = Counter(c["code"] for c in contra)
    audit_rows = "".join(
        f"<tr data-id='{c['id']}'><td>{c['id']}</td>"
        f"<td><span class='cx cx-{c['severity']}'>{c['code']}</span> "
        f"{CN[c['code']]}</td><td>{html.escape(c['listing'])}</td>"
        f"<td>{html.escape(c['other'])}</td><td>{c['severity']}</td>"
        f"<td>{html.escape(c['detail'])}</td>"
        "<td><select><option>OPEN</option><option>EXPLAINED</option>"
        "<option>MODEL_ERROR</option><option>DATA_ERROR</option>"
        "<option>OUT_OF_SCOPE</option></select></td>"
        "<td><input placeholder='说明' style='width:180px'></td></tr>"
        for c in contra)
    audit = (
        "<h2>矛盾检测 C01–C10(审计触发器,不淘汰房源)</h2>"
        "<div class='stat-note'>" + " · ".join(
            f"{k} {CN[k]}: <b>{cnt.get(k, 0)}</b>" for k in sorted(CN)) +
        "。C09/C10 由自动化测试守护(tests 见报告),页面不出现即为通过。"
        "状态存浏览器本地,可导出/导入 JSON。</div>"
        "<table id='audittable'><thead><tr><th>ID</th><th>类型</th>"
        "<th>房源</th><th>对照房源</th><th>级别</th><th>证据</th>"
        "<th>处理状态</th><th>说明</th></tr></thead><tbody>"
        + audit_rows + "</tbody></table>")
    ls = summary["legal_states"]
    legal = " · ".join(f"{k} {n}" for k, n in sorted(ls.items()))
    fc = ['<td><input placeholder="筛选"></td>'] * 14
    comms = sorted({r["comm"] for r in rows})
    fc[1] = ("<td><select><option value=''>全部社区</option>" + "".join(
        f"<option value='{c}'>{c}</option>" for c in comms) + "</select></td>")
    fc[8] = ("<td><select><option value=''>全部zoning</option>" + "".join(
        f"<option value='{z}'>{z}系</option>" for z in sorted(
            {(r['zoning'] or '—').split('-')[0][:2] for r in rows}))
        + "</select></td>")
    fc[12] = ('<td><select data-kind="roi"><option value="">全部ROI</option>'
              '<option value=">30">≥30%</option><option value="20-30">20–30%'
              '</option><option value="10-20">10–20%</option><option '
              'value="0-10">0–10%</option><option value="<0">负值</option>'
              '<option value="—">缺数据</option></select></td>')
    heads = ["地址", "社区", "挂牌(latest)", "BCA对照<br><span class='stat-note'>"
             "Ask/总·地</span>", "合理地价<br><span class='stat-note'>P10–P90"
             "</span>", "市场折价<br><span class='stat-note'>vs comps</span>",
             "地块", "面宽", "zoning", "HOUSE 0.6×", "DUPLEX 0.7×",
             "MULTIPLEX 1.0×<br><span class='stat-note'>代理·不排序</span>",
             "最优ROI<br><span class='stat-note'>仅VERIFIED</span>", "矛盾"]
    head = ("<tr>" + "".join(f"<th>{h}<span class='arrow'></span></th>"
                             for h in heads) + "</tr>"
            f"<tr class='filters'>{''.join(fc)}</tr>")
    rg, rp = summary["roi_ge"], summary["roi_p10_ge"]
    info = {"model": summary["model_version"], "k_fit": summary["k_fit"],
            "snapshot": v.LISTING_SNAPSHOT, "hpi": v.HPI_ASOF,
            "sales_cutoff": "~2026-07-08", "watchlist": os.path.basename(
                summary["watchlist"])}
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>{CSS}</style>
<title>V1.5.2 回测校准估值 — {len(rows)} ACTIVE HOUSE · 2026-10-20</title>
</head><body>
<div style="background:#e8f0fe;border:1px solid #4285f4;padding:8px 12px;
margin:6px 0;font-size:13px;border-radius:4px">
<b>V1.5.2 回测校准估值(研究层 · V3s 生产零修改):</b>
售价改为「社区基准 + 附近修正(收缩、±15%封顶)」,面积弹性由数据拟合;
每个售价带回测误差区间,<b>压力 ROI = 估售×历史前向回测误差 P10 比值时的 ROI(非概率保证)</b>;超回测面积样本的方案标注⚠、不剔除。
地价改为 BCA 校准(回测误差更低),comps 地价作交叉校验。
法规拆五态,<b>仅 VERIFIED 进最优 ROI 排名</b>;需审批/未核方案单列「待审」。
<span class="badge">P50 ROI≥10%: {rg[10]}</span>
<span class="badge">≥20%: {rg[20]}</span><span class="badge">≥30%: {rg[30]}</span>
<span class="badge">覆盖内≥10%: {summary['roi_in_support_ge'][10]}</span>
<span class="badge">售价−10%仍≥0: {summary['roi_m10_ge0']}</span>
<span class="badge">−20%仍≥0: {summary['roi_m20_ge0']}</span>
<span class="badge">压力ROI≥0%: {rp[0]}</span>
<span class="badge">矛盾触发: {len(contra)}</span>
快照 2026-10-20 · Ask=watchlist latest_asking · HPI 2026-09 · 成交截至 2026-07-08
</div>
<div id="xbar" style="margin:6px 0;font-size:12px">扩展筛选:
<select data-x="prod"><option value="">全部产品</option>
<option value="HOUSE">有HOUSE价</option><option value="DUPLEX">有DUPLEX价</option>
<option value="MPX">有MPX价</option></select>
<select data-x="quad"><option value="">全部判定</option>
<option value="BOTH">双优</option><option value="ROI_ONLY">仅ROI</option>
<option value="LAND_VALUE">仅地价</option><option value="NEITHER">双缺</option></select>
<select data-x="p10"><option value="">全部下行</option>
<option value="pos">压力ROI≥0</option></select>
<select data-x="sup"><option value="">完整研究排名</option>
<option value="in">样本覆盖内最优</option><option value="oos">仅⚠超样本面积</option></select>
<select data-x="cx"><option value="">全部矛盾</option>
<option value="any">有矛盾触发</option><option value="none">无矛盾</option></select>
<span class="tools"> · <button id="btn-export-all">导出全部</button>
<button id="btn-export-audit">导出审查数据 JSON</button>
<button id="btn-import">导入</button>
<input type="file" id="file-import" accept=".json" style="display:none"></span>
</div>
<h1>逐房源估值与重建 ROI — V1.5.2 回测校准 · {len(rows)} 套 ACTIVE HOUSE</h1>
<div class="viewbar"><button id="btn-list" class="vbtn on">列表</button>
<button id="btn-map" class="vbtn">地图</button>
<span class="map-legend"><i style="background:{QC['BOTH']}"></i>双优
<i style="background:{QC['ROI_ONLY']}"></i>仅ROI
<i style="background:{QC['LAND_VALUE']}"></i>仅地价
<i style="background:{QC['NEITHER']}"></i>双缺</span>
<span class="map-legend">· 地图与列表筛选联动,显示 <b id="mapcount"></b> 点</span></div>
<div id="mapview"><div id="map"></div></div>
<div id="listview">
{method_box(summary)}
<div class="sub">法规五态:{legal}。「需审批/放宽」= 生产规则 verified 但同时
variance_required,或非住宅 zoning 假设;「条件满足·未核」= 常规面积/面宽满足、
规则适用未核;二者不进排名。方案格:估售 P50 + P10/P90 区间、单价、社区样本、
附近修正;灰字为 V1.4 单价对照。<b>表头点击排序;第二行筛选</b>。
当前显示 <b id="viscount">{len(rows)}</b> 行。</div>
{community_table(rows)}
<h2>逐房源</h2>
<table id="proptable"><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>
{audit}
</div>
<script>
const MAP_POINTS = {json.dumps(points, ensure_ascii=False)};
const META = {json.dumps(meta, ensure_ascii=False)};
const META_INFO = {json.dumps(info, ensure_ascii=False)};
const CONTRA = {json.dumps(contra, ensure_ascii=False)};
const QC = {json.dumps(QC)};
{r14.MAP_JS}
{r14.TABLE_JS}
{AUDIT_JS}
</script></body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"{OUT} rows={len(rows)} points={len(points)} contra={len(contra)}")


if __name__ == "__main__":
    main()
