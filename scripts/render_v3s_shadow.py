#!/usr/bin/env python3
"""V3s 影子版 Map+List — 冻结口径统一渲染(只读,不进生产)。
数据源(全部已冻结):
  valuation-v3s-shadow-2026-10-22.json  V3s 方案售价(DUPLEX=stable)
  rebuild-value-2026-10-20.json       挂牌坐标/面宽/link(生产只读引用,不回写)
  baseline_compare.csv                社区 NEW 产品基准对照
  v3s_consistency_anomalies.csv       一致性 HIGH/CRITICAL 标记
  active_screening_v2.old_market      旧房土地主导成交参照(P25/50/75)
两维独立展示:旧房市价判定(算法初判) × 开发 ROI(售-10%/成本+10% 压测)。
风险标记完整继承方案级 risk_flags + 一致性异常 + HOUSE_THIN_POOL_UNRESOLVED。
输出 reports/watchlist/rebuild-value-v3s-2026-10-22.html
"""
import csv, json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rebuild_value_analysis as prod  # noqa: E402
from valuation_v2_sites import dev_cost  # noqa: E402
from active_screening_v2 import old_market, verdict  # noqa: E402

V3S = ROOT / "data/research/normalized/valuation-v3s-shadow-2026-10-22.json"
PROD = ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"
BCMP = ROOT / "reports/price_consistency_audit/baseline_compare.csv"
ANOM = ROOT / "reports/price_consistency_audit/v3s_consistency_anomalies.csv"
OUT = ROOT / "reports/watchlist/rebuild-value-v3s-2026-10-22.html"

PCOL = {"HOUSE": 0, "DUPLEX": 1, "TRIPLEX": 2, "FOURPLEX": 2}
COL = {"HOUSE": 0, "DUPLEX": 1, "MULTIPLEX": 2}
SRC_CN = {"local_2km": "本地2km", "borrowed_3km": "借用3km",
          "thin_n@2.0km": "薄样本2km", "thin_n@3.0km": "薄样本3km",
          "older_stock@2.0km": "旧库存2km", "older_stock@3.0km": "旧库存3km",
          "low": "低置信", "ok": "匹配", "measured": "带内"}
UNRESOLVED = {"Sunset", "Kensington-Cedar Cottage"}
SEV_RANK = {"HIGH": 1, "CRITICAL": 2}


# ---- 同址成交/地块冲突索引(展示级,不改估值) ----
def _addr_key(a):
    """'1534 W 71ST AVENUE' / '1534 71ST AVE W VANCOUVER' → ('1534','71ST','W')"""
    a = a.upper().replace(" VANCOUVER", "").strip()
    num = re.match(r"\d+", a)
    dire = re.search(r"\b([EWNS])\b", a)
    st = re.search(r"\b(\d+(?:ST|ND|RD|TH)|[A-Z]+(?=\s+(?:AVE|ST|DR|BLVD|RD|PL|CRES|WAY|CT|TER)\b))", a)
    return ((num.group(0) if num else ""), (st.group(1) if st else ""),
            (dire.group(1) if dire else ""))


def load_site_sales():
    from harvest_vancouver_sales_2025 import _num  # noqa: E402
    idx = {}
    for r in csv.DictReader(open(
            ROOT / "data/research/normalized/standard-residential-comps-v2.csv",
            encoding="utf-8-sig")):
        if r["product_class"] != "HOUSE_DETACHED":
            continue
        k = _addr_key(r["address"])
        if k[0]:
            idx.setdefault(k, []).append(
                {"date": r["sale_date"], "price": _num(r["sale_price"]),
                 "lot": _num(r["lot_sqft"]), "asof": r.get("asof_status", "")})
    return idx


def large_house_unsupported(plan):
    """HOUSE 目标建面超 comp 池最大 fa×1.15 且池薄 → 大面积外推无证据"""
    ev = (plan.get("evidence") or {}).get("evidence") or []
    if not ev:
        return plan.get("build_sf", 0) >= 2800
    mx = max((e[3] for e in ev), default=0)
    return plan["build_sf"] > mx * 1.15 and len(ev) < 3 or \
        plan["build_sf"] > mx * 1.15 and plan["build_sf"] >= 3400


def fm(v):
    return f"${v/1e6:.2f}M" if v else "—"


def pct(v):
    return f"{v:+.0%}"


def main():
    d = json.loads(V3S.read_text())
    v1 = {r["address"]: r for r in json.loads(PROD.read_text())["rows"]}
    bcmp = {}
    for r in csv.DictReader(open(BCMP)):
        bcmp[(r["address"], r["product"])] = r
    anom = {}   # (addr,product) -> {sev,kinds,cls}
    for r in csv.DictReader(open(ANOM)):
        k = (r["address"], r["product"])
        cur = anom.setdefault(k, {"sev": r["sev"], "kinds": set(), "cls": set()})
        cur["kinds"].add(r["kind"]); cur["cls"].add(r["cls"])
        if SEV_RANK[r["sev"]] > SEV_RANK[cur["sev"]]:
            cur["sev"] = r["sev"]

    # 坐标重复/疑似同址检测(不删除,仅标记)
    coord_groups = defaultdict(list)
    for s in d["sites"]:
        v1r = v1.get(s["address"], {})
        if v1r.get("lat") and v1r.get("lng"):
            coord_groups[(round(v1r["lat"], 4), round(v1r["lng"], 4))].append(s["address"])
    dup_addr = {a for g in coord_groups.values() if len(g) > 1 for a in g}
    # Integrity Gate:先跑检测保证版本一致,再读异常(自动运行集成)
    import valuation_integrity_gate
    valuation_integrity_gate.main()
    # 冻结校准卡要价漂移横幅(防旧卡误导)
    import refresh_stale_cards
    refresh_stale_cards.main()
    # 要价事件链(列表徽标:最近事件+累计变化;完整链留详情)
    wl_hist = {}
    try:
        import price_events
        w = json.loads((ROOT / "data/research/watchlist/"
                        "property-watchlist.json").read_text())
        for e in w["entries"]:
            ch = price_events.build_event_chain(e.get("sightings", []))
            if ch["price_events"]:
                wl_hist[e["address"]] = ch
    except Exception:
        pass
    gsum = json.loads((ROOT / "reports/price_consistency_audit/"
                      "valuation_integrity_summary.json").read_text())
    gate_stamp = (f"Gate v{gsum['gate_version']}·快照{gsum['input_hash'][:10]}"
                  f"·run {gsum['run_id']}")
    gate = {}
    gf = ROOT / "reports/price_consistency_audit/valuation_integrity_anomalies.csv"
    if gf.exists():
        GRANK = {"INFO": -1, "REVIEW": 0, "HIGH": 1, "CRITICAL": 2}
        for r in csv.DictReader(open(gf)):
            cur = gate.setdefault(r["listing_id"],
                                  {"sev": "INFO", "n": 0, "n_rv": 0,
                                   "checks": set()})
            cur["n"] += 1
            if r["severity"] != "INFO":
                cur["n_rv"] += 1
                cur["checks"].add(r["check_type"])
            if GRANK[r["severity"]] > GRANK[cur["sev"]]:
                cur["sev"] = r["severity"]
    site_sales = load_site_sales()
    hpatch = {}
    pf = ROOT / "data/research/normalized/house_anchor_patch_v3s.json"
    if pf.exists():
        hpatch = json.loads(pf.read_text())

    rows_out, cell_note, addr_note = [], {}, {}
    map_extra = {}   # addr -> {sev, omv, risk}
    n_nocoord = 0
    for s in d["sites"]:
        addr, ask, lot = s["address"], s["ask"], s["lot"]
        v1r = v1.get(addr, {})
        site_sev, all_flags = "", set()
        scenarios = {}
        econ = {}
        for col in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            plan = next((p for p in s["plans"] if PCOL[p["product"]] == COL[col]), None)
            if not plan or not plan.get("eligible"):
                scenarios[col] = {"eligible": False}
                continue
            is_dx = plan["product"] == "DUPLEX"
            c = plan.get("contract_stable") if is_dx else plan.get("contract")
            c = c or plan.get("contract")
            hp = hpatch.get(addr) if col == "HOUSE" else None
            if c and hp:
                c = hp["new_contract"]
            if not c:
                # 合法但缺价格证据:显示 LEGAL+缺comps,不伪装不适用
                scenarios[col] = {"eligible": True, "est_sale": None,
                                  "build_sqft": plan["build_sf"],
                                  "units": plan["units"], "unit_range": None,
                                  "psqft_used": 0, "price_src": "",
                                  "price_confidence": "low",
                                  "roi": {}, "cost_total": {}, "profit": {}}
                legal = "LEGAL✓" if plan.get("verified") else "LEGAL待核"
                cell_note.setdefault(addr, {})[COL[col]] = (
                    f"FSR{plan['build_sf']/lot:.2f}·{legal}·缺价格证据")
                continue
            bt = 400 if col == "MULTIPLEX" else 350
            ec = dev_cost(c, ask, plan["build_sf"], plan["units"], bt)
            dn = dev_cost(c * .9, ask, plan["build_sf"], plan["units"], bt)
            cu = dev_cost(c, ask, plan["build_sf"], plan["units"], bt * 1.1)
            econ[col] = {"roi": ec["roi"], "dn": dn["roi"], "cu": cu["roi"],
                         "profit": ec["profit"], "c": c}
            ev = plan.get("evidence") or {}
            conf = (plan.get("price_conf_stable") or plan.get("price_conf") or "")
            src = (f"纠偏·{hp['src']}" if hp else
                   (plan.get("stable_note") if is_dx and plan.get("contract_stable")
                    else SRC_CN.get(conf, conf)))
            ev_n = "" if (is_dx and plan.get("contract_stable")) \
                else f"·n{ev.get('n') or '—'}"
            fl = set(plan.get("risk_flags") or [])
            if col == "HOUSE" and s["comm"] in UNRESOLVED:
                fl.add("HOUSE_THIN_POOL_UNRESOLVED")
            if col == "HOUSE" and large_house_unsupported(plan):
                fl.add("HOUSE_LARGE_PRODUCT_UNSUPPORTED")
            if col == "DUPLEX" and plan["build_sf"] / plan["units"] > 1900:
                fl.add("LARGE_UNIT_EVIDENCE_THIN")
            an = anom.get((addr, plan["product"])) or anom.get((addr, col))
            if an:
                fl.add(f"CONSISTENCY_{an['sev']}")
                if SEV_RANK.get(an["sev"], 0) > SEV_RANK.get(site_sev, 0):
                    site_sev = an["sev"]
            all_flags |= fl
            legal = "LEGAL✓" if plan.get("verified") else "LEGAL待核"
            scenarios[col] = {
                "eligible": True, "est_sale": c,
                "build_sqft": plan["build_sf"], "units": plan["units"],
                "unit_range": f"{plan['units']}户" if plan["units"] > 1 else None,
                "psqft_used": c / plan["build_sf"] if plan["build_sf"] else 0,
                "price_src": f"<b>{legal}</b>·{src}{ev_n}"
                             f"·{plan.get('model_valid', '')}",
                "price_confidence": ("local" if conf in
                                     ("local_2km", "ok", "measured", "band_match")
                                     else "low"),
                "roi": {str(bt): ec["roi"]},
                "cost_total": {str(bt): ec["tdc"]},
                "profit": {str(bt): ec["profit"]},
                "roi_sale_m5": dn["roi"],
                "roi_sale_m5_cost_p10": cu["roi"],
            }
            # ---- 方案格注记:FSR/户数 + 社区基准 + 风险 ----
            bits = [f"FSR{plan['build_sf']/lot:.2f}" if lot else "FSR—"]
            br = bcmp.get((addr, plan["product"]))
            if br:
                if br["baseline_price"]:
                    band = "带" if str(br["band_src"]).startswith("band") else "全量"
                    bits.append(f"社区{band}参考 {fm(float(br['baseline_price']))}"
                                f"·n{br['baseline_n']}")
                    dp = (c / float(br["baseline_price"]) - 1) * 100
                    mark = " ⚠DIVERGENCE" if abs(dp) >= 20 else ""
                    bits.append(f"V3s-基准{dp:+.0f}%{mark}")
                if br.get("target_band"):
                    tbn = br.get("target_band_n") or "0"
                    bits.append(f"目标带({br['target_band']}sf) n{tbn}"
                                + ("·样本不足" if tbn in ("0", "1", "2", "3") else ""))
            if fl:
                bits.append("⛔" + "|".join(sorted(fl)))
            if is_dx and plan.get("contract") and plan.get("contract_stable") \
                    and plan["contract_stable"] != plan["contract"]:
                chg = plan["contract_stable"] / plan["contract"] - 1
                bits.append(f"V3旧值{fm(plan['contract'])}→V3s{chg:+.0%}")
            if hp:
                chg = hp["new_contract"] / hp["old_contract"] - 1
                bits.append(f"原锚定价{fm(hp['old_contract'])}→纠偏{chg:+.0%}"
                            f"·研究价非已验证")
            cell_note.setdefault(addr, {})[COL[col]] = "<br>".join(bits)
        # 最优=保守口径(售-10%)最高 ROI
        best = max(econ.items(), key=lambda kv: kv[1]["dn"], default=None)
        rows_out.append({
            "address": addr, "hood": s.get("comm") or "—",
            "side": v1r.get("side"), "tier": s.get("tier"),
            "status": s.get("status"), "asking": ask,
            "ratio": v1r.get("ratio"), "ratio_land": v1r.get("ratio_land"),
            "lot_sqft": lot, "frontage_ft": v1r.get("frontage_ft"),
            "zoning": s.get("zone"), "link": v1r.get("link"),
            "lat": v1r.get("lat"), "lng": v1r.get("lng"),
            "analysis": {"scenarios": scenarios,
                         "best_scenario": best[0] if best else None,
                         "best_roi": best[1]["roi"] if best else None,
                         "cost_tiers": ["350", "400"]},
        })
        # ---- 地址下:旧房市价判断(算法初判) ----
        om = old_market(s["comm"], lot)
        if om.get("p50"):
            diff = ask - om["p50"]
            vd = verdict(ask, om)
            om_txt = (f"旧房参照(算法初判): P25 {fm(om['p25'])} P50 {fm(om['p50'])} "
                      f"P75 {fm(om['p75'])} · ASK−P50 {diff/1e4:+.0f}万 "
                      f"({ask/om['p50']*100-100:+.1f}%) → <b>{vd}</b> "
                      f"· n{om['n']}·{om['src']}·{om['date_min'][:7]}~{om['date_max'][:7]}")
        else:
            vd = "证据不足"
            om_txt = "旧房参照: 证据不足,无法可靠判断"
        extra = []
        if s["status"] not in ("ACTIVE", "STILL_LISTED"):
            extra.append("SOLD/历史价")
        if not (v1r.get("lat") and v1r.get("lng")):
            extra.append("⚠无坐标")
            n_nocoord += 1
        if addr in dup_addr:
            extra.append("疑似同址/相邻重复")
        # 同址成交记录 + 地块冲突(展示级核证,不自动改判定)
        sales = sorted(site_sales.get(_addr_key(addr), []),
                       key=lambda x: x["date"], reverse=True)
        if sales:
            latest = sales[0]
            extra.append(f"该址{latest['date'][:7]}曾成交"
                         f"${(latest['price'] or 0)/1e6:.2f}M")
            if latest["lot"] and lot and \
                    not (0.75 <= latest["lot"] / lot <= 1.3):
                extra.append(f"⛔LOT_CONFLICT 挂牌{lot:,.0f}sf"
                             f"vs记录{latest['lot']:,.0f}sf")
                site_sev = "CRITICAL" if not site_sev else site_sev
                all_flags.add("LOT_DATA_CONFLICT")
        # Integrity Gate 站点状态徽标:REVIEW+才计级,INFO=低证据
        gt = gate.get(addr)
        if gt and gt["sev"] != "INFO":
            gnames = "|".join(sorted(gt["checks"]))
            extra.append(f"门检:{gt['sev']}·{gt['n_rv']}项 [{gnames[:80]}]")
            all_flags |= gt["checks"]
            if SEV_RANK.get(gt["sev"], 0) > SEV_RANK.get(site_sev, 0):
                site_sev = gt["sev"]
        elif gt:
            extra.append(f"门检:低证据·{gt['n']}项")
        else:
            extra.append("门检:PASS")
        if addr in wl_hist:
            ch = wl_hist[addr]
            ev = ch["latest_event"]
            cum = ch["cumulative"]
            d = ev["delta"]
            tag = {"RELISTED": "重挂", "PRICE_DROP": "要价",
                   "PRICE_INCREASE": "要价",
                   "UNKNOWN_CHANGE": "变动?"}[ev["type"]]
            arrow = "↓" if d < 0 else "↑" if d > 0 else "="
            seg = f"{tag}{arrow}{fm(abs(d))}"
            if cum != d:
                seg += (f"·累计{'↓' if cum < 0 else '↑'}"
                        f"{fm(abs(cum))}")
            seg += (f"({fm(ch['first_ask'])}→{fm(ch['last_ask'])}"
                    f"·{ev['date'][:10]})")
            extra.append(seg)
        addr_note[addr] = om_txt + (" · " + "·".join(extra) if extra else "")
        map_extra[addr] = {
            "sev": site_sev, "flags": "·".join(sorted(all_flags)),
            "omv": f"旧房 {vd}{'' if vd == '证据不足' else ' ' + format(ask/om['p50']*100-100, '+.0f') + '%' if om.get('p50') else ''}"}

    html = prod.render(rows_out, "V3s冻结版-2026-10-22",
                       "V3s·DUPLEX稳定级联·HOUSE主体冻结·MPX研究口径·2026-10-22",
                       {}, {})

    # ---- 社区表:替换为审计口径真值统计(community_stats_audit.py 产出) ----
    st = json.loads(
        (ROOT / "data/research/normalized/community_stats_v3s.json").read_text())
    import collections as _c
    best_by_hood = _c.defaultdict(_c.Counter)
    for r in rows_out:
        bs = (r.get("analysis") or {}).get("best_scenario")
        if bs and r.get("hood"):
            best_by_hood[r["hood"]][bs] += 1
    hood_rows = []
    for a, e in sorted(st.items(), key=lambda kv:
                       -(kv[1]["norm"]["median"] if kv[1]["norm"] else 0)):
        m = e["norm"]
        if not m:
            hood_rows.append(f"<tr><td>{a}</td><td colspan='9' class='muted'>"
                             f"无样本</td></tr>")
            continue
        psf = lambda v: f"${v/2200:,.0f}"  # noqa: E731
        if e["used_window"] == "borrowed_side":
            typ = f"<span style='color:#b46a00'>REGIONAL_BORROWED·{e['pool_id']}</span>"
            own_n, bor_n = e["own_n_all"], e["borrow_n"]
        else:
            typ = f"自有·{e['used_window']}"
            own_n, bor_n = e["own_n_all"] if e["used_window"] == "all2018+" \
                else e["own_n_recent"], 0
        cnt = best_by_hood.get(a)
        tend = (f"{cnt.most_common(1)[0][0]}"
                f"<span class='muted'>({cnt.most_common(1)[0][1]})</span>"
                if cnt else "—")
        hood_rows.append(
            f"<tr><td>{a}</td><td>{typ}</td><td>{own_n}</td><td>{bor_n or '—'}</td>"
            f"<td>{psf(m['mean'])}</td><td>{psf(m['median'])}</td>"
            f"<td>{psf(m['p25'])}–{psf(m['p75'])}</td>"
            f"<td>{psf(m['min'])}–{psf(m['max'])}</td>"
            f"<td><b>{psf(m['median'])}</b></td>"
            f"<td class='stat-note'>{e['conf']}</td><td>{tend}</td></tr>")
    hood_table = (
        "<h2>社区新房单价(HOUSE·归一2200sf·审计口径)</h2>"
        "<div class='stat-note'>统计对象=同 community_prices 口径的 norm_house 归一价÷2200sf;"
        "mean/median/min/max 均从同一样本池真实计算(此前均值/中位误以P50填充,区间实为P25-75,已修正)。"
        "REGIONAL_BORROWED=自有成交<3,借用同侧市场池(借数=池规模,非本社区成交);"
        "参考价=中位,为 price_contract 回退输入。DUPLEX/MPX 社区价见方案格内基准对照。</div>"
        "<table><tr><th>社区</th><th>证据类型</th><th>自有n</th><th>借用n</th>"
        "<th>均值$/sf</th><th>中位$/sf</th><th>P25–P75</th><th>min–max</th>"
        "<th>参考价</th><th>置信</th><th>最优形态</th></tr>"
        + "".join(hood_rows) + "</table>")
    html = re.sub(r"<h2>社区单价分类.*?</table>", lambda _: hood_table,
                  html, count=1, flags=re.S)
    html = html.replace("售价−5% / 再+建安10%", "售价−10% / 建安+10%")
    html = html.replace("建造（$350/400/450)", "建造($350/400)")

    # ---- 地图:富化 MAP_POINTS + 风险圈 + 定位行 ----
    pts = []
    for i, r in enumerate(rows_out):
        if not (r.get("lat") and r.get("lng")):
            continue
        a = r["analysis"]; mx = map_extra.get(r["address"], {})
        pts.append({"k": i, "addr": r["address"], "hood": r["hood"],
                    "status": r["status"], "tier": r.get("tier") or "",
                    "asking": r["asking"], "ratio_land": r.get("ratio_land"),
                    "zoning": r.get("zoning") or "", "roi": a.get("best_roi"),
                    "scen": a.get("best_scenario"), "lat": r["lat"],
                    "lng": r["lng"], "link": r.get("link") or "",
                    "sev": mx.get("sev", ""), "omv": mx.get("omv", ""),
                    "risk": mx.get("flags", "")})
    html = re.sub(r"const MAP_POINTS = \[.*?\];",
                  "const MAP_POINTS = " + json.dumps(pts, ensure_ascii=False) + ";",
                  html, count=1, flags=re.S)
    html = html.replace(
        "radius: 7, color: '#fff', weight: 1.5,",
        "radius: 7, color: (p.sev==='CRITICAL'?'#c2255c':p.sev==='HIGH'?'#e8590c':'#fff'),"
        " weight: (p.sev?3.5:1.5),")
    html = html.replace(
        "+ '<div class=\"pp-roi\">最优 ' + fmtRoi(p.roi) + ' ' + (p.scen || '') + '</div>'",
        "+ '<div class=\"pp-roi\">最优 ' + fmtRoi(p.roi) + ' ' + (p.scen || '') + '</div>'\n"
        "            + '<div style=\"font-size:12px;color:#555\">' + (p.omv || '') + '</div>'\n"
        "            + '<div style=\"font-size:12px;color:#c2255c\">' + (p.risk || '') + '</div>'\n"
        "            + '<div><a href=\"javascript:void(0)\" onclick=\"__locate(' + p.k + ')\">"
        "定位列表行</a></div>'")
    html = html.replace("撤回</span>",
                        "撤回</span><span class=\"map-legend\">· "
                        "<i style=\"background:#fff;border:2px solid #c2255c\"></i>CRITICAL "
                        "<i style=\"background:#fff;border:2px solid #e8590c\"></i>HIGH</span>")

    # ---- 地址注记 + 方案格注记注入 ----
    for addr, note in addr_note.items():
        i = html.find(f">{addr}</a>")
        if i == -1:
            continue
        pos = html.find("</a>", i) + 4
        html = (html[:pos] + f"<br><span class='stat-note' style='color:#0b57d0'>"
                f"{note}</span>" + html[pos:])
    for addr, notes in cell_note.items():
        i = html.find(f">{addr}</a>")
        if i == -1:
            continue
        end = html.find("</tr>", i)
        tds = [i + m.start() for m in re.finditer(r"<td", html[i:end])]
        scen_pos = tds[-4:-1]
        for ci in sorted(notes, reverse=True):
            p = scen_pos[ci]
            tdend = html.find("</td>", p)
            if tdend == -1:
                continue
            html = (html[:tdend] + f"<br><span class='stat-note' style='color:#0b57d0'>"
                    f"{notes[ci]}</span>" + html[tdend:])

    # ---- 压测标签更正(售-10%/成本+10%) ----
    html = html.replace("压测−5%价", "压测·售−10%")
    html = html.replace("·+建安10%", "·建安+10%")

    # ---- 顶部横幅 + 扩展筛选条 ----
    html = html.replace("<body>", """<body>
<div style="background:#e8f0fe;border:1px solid #4285f4;padding:8px 12px;margin:6px 0;
font-size:13px;border-radius:4px">
<b>V3s 冻结版(研究·不进生产):</b>
两维独立判断——蓝字=旧房市价参照(土地主导同型成交 P25/50/75,算法初判待人工);
方案格=开发 ROI + 售−10%/建安+10% 压测。DUPLEX 用稳定级联价(格内注 V3旧值→V3s 差),
⛔ 行为方案级风险标记(继承 risk_flags + 一致性异常 + HOUSE_THIN_POOL_UNRESOLVED)。
地图圈色:红=CRITICAL·橙=HIGH;弹窗内"定位列表行"联动。默认筛 ACTIVE,状态筛选可查全部 292。
不设 MaxBid、不建议还价。BCA 成交截至 2026-07-08。__GATESTAMP__
</div>
<div id="xbar" style="margin:6px 0;font-size:12px">扩展筛选:
<select data-x="prod"><option value="">全部产品</option><option value="HOUSE">有HOUSE价</option><option value="DUPLEX">有DUPLEX价</option><option value="MPX">有MPX价</option></select>
<select data-x="risk"><option value="">全部风险</option><option value="sev">严重标记</option><option value="any">任一标记</option><option value="none">无严重标记</option></select>
<select data-x="old"><option value="">旧房判定</option><option>相对偏便宜</option><option>接近市场水平</option><option>相对偏贵</option><option>证据不足</option></select>
<select data-x="conf"><option value="">全部置信</option><option value="local">本地/带内证据</option><option value="low">低置信</option></select>
</div>""", 1)

    # ---- 默认筛 ACTIVE + 扩展筛选 JS + 定位函数 ----
    html = html.replace("</body></html>", """<script>
(function(){const sel=document.querySelectorAll('tr.filters select')[1];
if(sel){sel.value='ACTIVE';sel.dispatchEvent(new Event('change'));}})();
const XB={prod:'',risk:'',old:'',conf:''};
function extraOk(r){
 const c=r.cells,t=r.innerText;
 if(XB.prod==='HOUSE'&&!c[10].innerText.includes('估售'))return false;
 if(XB.prod==='DUPLEX'&&!c[11].innerText.includes('估售'))return false;
 if(XB.prod==='MPX'&&!c[12].innerText.includes('估售'))return false;
 if(XB.risk==='sev'&&!/门检:(CRITICAL|HIGH)|CONSISTENCY_(CRITICAL|HIGH)|LOT_CONFLICT|UNRESOLVED/.test(t))return false;
 if(XB.risk==='any'&&!/门检:(?!PASS)|HIGH|CRITICAL|UNRESOLVED|EXTRAPOLATION|THIN|CONFLICT|UNSUPPORTED/.test(t))return false;
 if(XB.risk==='none'&&/门检:(CRITICAL|HIGH)|CONSISTENCY_(CRITICAL|HIGH)|LOT_CONFLICT|UNRESOLVED/.test(t))return false;
 if(XB.old&&!c[0].innerText.includes(XB.old))return false;
 if(XB.conf==='local'&&!/本地2km|带内|匹配/.test(t))return false;
 if(XB.conf==='low'&&!/低置信|薄样本|借用|low/.test(t))return false;
 return true;
}
function extraPass(){
 dataRows.forEach(r=>{if(r.style.display!=='none'&&!extraOk(r))r.style.display='none'});
 const vis=dataRows.filter(r=>r.style.display!=='none');
 document.getElementById('viscount').textContent=vis.length+' / '+dataRows.length;
 window.__visKeys=new Set(vis.map(r=>r.dataset.k));
 if(window.__refreshMap)window.__refreshMap();
}
document.querySelectorAll('#xbar select').forEach(s=>s.addEventListener('change',()=>{
 XB[s.dataset.x]=s.value;
 const inp=document.querySelector('tr.filters input');
 inp.dispatchEvent(new Event('input'));extraPass();
}));
function __locate(k){
 document.getElementById('btn-list').click();
 const r=document.querySelector('tr[data-k="'+k+'"]');
 if(r){r.scrollIntoView({block:'center'});r.style.background='#fff3bf';
  setTimeout(()=>r.style.background='',1800);}
}
</script></body></html>""")

    html = html.replace("__GATESTAMP__",
                        f" {gate_stamp}")
    html += (f"\n<!-- integrity-gate:{gsum['gate_version']} "
             f"input_hash:{gsum['input_hash']} run:{gsum['run_id']} -->\n")
    OUT.write_text(html)

    # ---- 验收输出 ----
    n_act = sum(1 for s in d["sites"] if s["status"] == "ACTIVE")
    chk = {}
    for a in ("3910 W 10TH AVENUE", "4343 W 11TH AVENUE", "4712 VICTORIA DRIVE",
              "2635 W 7TH AVENUE", "1342 GRANT STREET", "1649 GRANT STREET"):
        s = next((x for x in d["sites"] if x["address"] == a), None)
        if s:
            chk[a] = {p["product"]: (p.get("contract_stable")
                                     if p["product"] == "DUPLEX" else p.get("contract"))
                      for p in s["plans"] if p.get("eligible")}
    print(f"sites={len(d['sites'])} ACTIVE={n_act} map_pts={len(pts)} "
          f"no_coord={n_nocoord} dup_addr={len(dup_addr)}")
    print(f"notes: addr={len(addr_note)} cell={sum(len(v) for v in cell_note.values())}")
    for a, v in chk.items():
        print(" ", a, v)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
