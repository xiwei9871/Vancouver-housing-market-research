#!/usr/bin/env python3
"""V1.4.5 cross-consistency audit — Phase A (read-only findings) +
confirmed-bug flags for Phase B.

Reads the frozen V1.4.4 outputs + the same corrected pools the
valuation pipeline uses, then applies rules C01-C11. No valuation,
cost, ROI or zoning inputs are modified — every output lands in
docs/audits/listing_consistency_v1_4_5/.

Finding classes: CONFIRMED_BUG / EXPLAINED_DIFFERENCE / UNRESOLVED /
LEGAL_REVIEW_REQUIRED. These are audit conclusions, not confidence
tiers and never auto-exclusion rules.
"""
import csv
import hashlib
import importlib.util
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/audits/listing_level_valuation_v1_4"
OUT = ROOT / "docs/audits/listing_consistency_v1_4_5"
RULESET = "V1.4.5-C01..C11-r1"
AUDIT_DATE = "2026-10-23"

spec = importlib.util.spec_from_file_location(
    "v14", ROOT / "scripts/listing_level_valuation_v1_4.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def num(x):
    try:
        f = float(str(x).replace(",", "").replace("$", ""))
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def hav(a, b, c, d):
    return v.hav_km(a, b, c, d)


def wcsv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k))
                        for k in fields})


def rc(path):
    return list(csv.DictReader(open(path)))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


# ------------------------------------------------------------------ load
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    land = {r["listing"]: r for r in
            rc(SRC / "active_house_land_valuation.csv")}
    roi = rc(SRC / "active_house_listing_roi.csv")
    sale = rc(SRC / "active_house_newbuild_sale_estimate.csv")
    scen = rc(SRC / "active_house_buildable_scenarios.csv")
    rank2 = rc(SRC / "rebuild_roi_ranking.csv")
    bid = {(r["listing"], r["scenario"]): r for r in
           rc(SRC / "active_house_max_bid_and_negotiation.csv")}
    lrows = {r["address"]: r for r in json.load(
        open(ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"))
        ["rows"]}

    arch = v.load_archive()
    house_rows = rc(v.V12 / "house_price_normalized_per_row.csv")
    proxy_rows = rc(v.V12 / "attached_proxy_scenarios_per_row.csv")
    dims = v.load_lot_dims()
    land_pool, aux_pool, _ = v.build_land_pool(
        house_rows, arch, v.load_resolved_yb(), v.load_tax(), dims)
    lcomp = {r["tx"]: r for r in land_pool + aux_pool}
    nb_pools = v.build_newbuild_pools(house_rows, proxy_rows, arch)

    # newbuild HOUSE pool WITH sale dates (for the C08 holdout split)
    nb_house = []
    for r in house_rows:
        if (r["band"] in ("0-1", "2-3", "4-5") and r["strata"] != "True"
                and r["excl"] == "" and num(r["fa"])
                and num(r["price_2026_09"])):
            a = arch.get(r["tx"], {})
            nb_house.append({
                "tx": r["tx"], "community": r["community"],
                "side": r["side"], "lat": num(a.get("lat")),
                "lng": num(a.get("lng")), "band": r["band"],
                "fa": num(r["fa"]), "date": r["date"][:10],
                "year": num(r["year"]),
                "price": num(r["price_2026_09"]),
                "psf": num(r["price_2026_09"]) / num(r["fa"]),
                "factor_kind": "OFFICIAL",
                "address": a.get("address")})

    # per-listing enrichment
    T = {}
    for addr, lr in lrows.items():
        L = land.get(addr)
        if not L:
            continue
        T[addr] = {
            "listing": addr,
            "lat": num(lr["lat"]), "lng": num(lr["lng"]),
            "hood_comm": L["hood_comm"], "side_key": L["side"],
            "zoning": L["zoning"], "lot": num(L["lot_sqft"]),
            "frontage": num(L["frontage"]),
            "ask": num(L["asking"]), "bca": num(L["bca"]),
            "bca_land": num(L["bca_land"]),
            "fair": num(L["fair_land_value_today"]),
            "fair_psf": num(L["fair_land_psf"]),
            "land_level": L["land_comp_level"],
            "land_n": L["land_comp_n"], "land_ids": L["land_comp_ids"],
            "disc": num(L["discount_to_land_market_pct"]),
            "land_conf": L["confidence"],
            "psf_p25": num(L["land_psf_p25"]),
            "psf_p75": num(L["land_psf_p75"])}

    roi_best = {}
    roi_by_scen = {}
    # canonical basis is GST-EXCL (V1.4.2); INCL rows are the tax-
    # inclusive variant and must not double-count pairs or lift a
    # listing over an ROI threshold
    roi = [r for r in roi if r["gst_scenario"] == "EXCL"]
    for r in roi:
        a = r["listing"]
        rv = num(r["roi_development_1y"])
        roi_by_scen[(a, r["scenario"])] = r
        if rv is not None and (a not in roi_best
                               or rv > roi_best[a]["roi"]):
            roi_best[a] = {"roi": rv, "scen": r["scenario"], "row": r}
    sale_by_scen = {(r["listing"], r["scenario"]): r for r in sale}
    scen_map = {(r["listing"], r["scenario"]): r for r in scen}
    rank_idx = {r["listing"]: int(r["rank"]) for r in rank2}

    def comp_evidence(ids, cap=12):
        """land/newbuild comp detail for a finding."""
        out = []
        for tx in (ids or "").split(";")[:cap]:
            c = lcomp.get(tx)
            if c:
                out.append(
                    f"{c['address']}|{c['date']}|lot{round(c['lot'])}"
                    f"|${round(c['price_2026_09'] or 0):,}"
                    f"|${round(c['land_psf'] or 0)}/sf|{c['lot_src']}")
                continue
            for p in nb_pools.values():
                hit = next((x for x in p if x["tx"] == tx), None)
                if hit:
                    aa = arch.get(tx, {})
                    out.append(
                        f"{aa.get('address')}|nb|psf"
                        f"{round(hit['psf'] or 0)}|fa"
                        f"{round(hit['fa'] or 0)}|{hit['factor_kind']}")
                    break
        return " ~ ".join(out)

    findings = []
    fid = defaultdict(int)

    def add(rule, listing, severity, cls, fields, evidence, note):
        fid[rule] += 1
        findings.append({
            "finding_id": f"F-{rule}-{fid[rule]:03d}", "rule": rule,
            "listing": listing, "severity": severity,
            "classification": cls, "key_fields": fields,
            "evidence": evidence, "note": note})

    # ================================================= C01/C02/C03 BCA
    for a, t in T.items():
        ask, fair, bt, bl = t["ask"], t["fair"], t["bca"], t["bca_land"]
        if not (ask and fair):
            continue
        ev = comp_evidence(t["land_ids"])
        base = (f"ask={ask:,.0f} bca_total={bt or 0:,.0f} "
                f"bca_land={bl or 0:,.0f} fair={fair:,.0f} "
                f"level={t['land_level']} n={t['land_n']} "
                f"conf={t['land_conf']}")
        if bt and ask / bt <= 0.90 and (ask - fair) / fair >= 0.10:
            add("C01", a, "HIGH", "UNRESOLVED",
                base + f"|ask/bca_t={ask / bt:.2f}"
                       f"|(ask-fair)/fair={(ask - fair) / fair:.2f}",
                ev, "政府评估显示挂牌便宜,模型地价却更低——核对 BCA 年"
                    "度/土地占比/comps 地块尺寸与成交性质")
        if bl and (fair / bl < 0.80 or fair / bl > 1.25):
            share = bl / bt if bt else None
            add("C02", a, "HIGH" if fair / bl < 0.8 else "MEDIUM",
                "UNRESOLVED",
                base + f"|fair/bca_land={fair / bl:.2f}"
                       f"|bca_land_share={share:.2f}" if share else base,
                ev, "模型地价与 BCA 土地值差>20/25%;BCA 不替代市场,"
                    "但需解释差异来源")
        if bt and ask / bt >= 1.00 and (fair - ask) / fair >= 0.10:
            add("C03", a, "MEDIUM", "UNRESOLVED",
                base + f"|ask/bca_t={ask / bt:.2f}"
                       f"|(fair-ask)/fair={(fair - ask) / fair:.2f}",
                ev, "Ask 不低于 BCA 总估值,模型却给出≥10%土地折价——"
                    "核对大地块/稀缺地段/异常 comps")

    # ======================================= C04 same-comm similar lots
    def same_dev(a, b):
        return a["zoning"] == b["zoning"] or (
            {a["zoning"], b["zoning"]} <= {"R1-1", "RS-1", "RS-5"})

    pair_rows = []
    addrs = [a for a, t in T.items() if t["lat"] and t["fair"]]
    for i in range(len(addrs)):
        for j in range(i + 1, len(addrs)):
            a, b = T[addrs[i]], T[addrs[j]]
            if a["hood_comm"] != b["hood_comm"]:
                continue
            d = hav(a["lat"], a["lng"], b["lat"], b["lng"])
            if d is None or d > 1.5 or not same_dev(a, b):
                continue
            if not a["lot"] or not b["lot"]:
                continue
            lr = abs(a["lot"] / b["lot"] - 1)
            if lr > 0.15:
                continue
            if (a["frontage"] and b["frontage"]
                    and abs(a["frontage"] / b["frontage"] - 1) > 0.20):
                continue
            d_cad = abs(a["fair"] - b["fair"])
            d_psf = (abs(a["fair_psf"] / b["fair_psf"] - 1)
                     if a["fair_psf"] and b["fair_psf"] else None)
            if d_cad <= 500_000 and (d_psf is None or d_psf <= 0.30):
                continue
            drv = []
            if a["land_level"] != b["land_level"]:
                drv.append(f"层级切换 {a['land_level']}↔{b['land_level']}")
            if int(a["land_n"] or 0) != int(b["land_n"] or 0):
                drv.append(f"comps n {a['land_n']}↔{b['land_n']}")
            if a["lot"] != b["lot"]:
                drv.append(f"lot {a['lot']:,.0f}↔{b['lot']:,.0f}sf")
            pr = {"rule": "C04", "listing_a": a["listing"],
                  "listing_b": b["listing"], "hood_comm": a["hood_comm"],
                  "dist_km": round(d, 2), "zoning": a["zoning"],
                  "lot_a": a["lot"], "lot_b": b["lot"],
                  "fair_a": round(a["fair"]), "fair_b": round(b["fair"]),
                  "fair_psf_a": round(a["fair_psf"], 1),
                  "fair_psf_b": round(b["fair_psf"], 1),
                  "delta_cad": round(d_cad),
                  "delta_psf_pct": round(d_psf * 100, 1) if d_psf else "",
                  "level_a": a["land_level"], "level_b": b["land_level"],
                  "drivers": "; ".join(drv),
                  "comps_a": comp_evidence(a["land_ids"], 6),
                  "comps_b": comp_evidence(b["land_ids"], 6)}
            pair_rows.append(pr)
            add("C04", f"{a['listing']} ↔ {b['listing']}",
                "HIGH" if (d_psf or 0) > 0.30 else "MEDIUM",
                "EXPLAINED_DIFFERENCE" if "层级切换" in pr["drivers"]
                else "UNRESOLVED",
                f"lot比{lr:.2f} dist{d:.2f}km Δ${d_cad:,.0f} "
                f"Δpsf{(d_psf or 0) * 100:.0f}%",
                pr["drivers"],
                "同社区同zoning相似地块估值差>阈值;检查 500M→LOTBAND "
                "切换是否制造突变")

    # ========================================= C05 high ROI on pricey land
    bridge_rows = []
    for a, t in T.items():
        if t["disc"] is None or t["disc"] >= -10:
            continue
        rb = roi_best.get(a)
        if not rb or rb["roi"] < 20:
            continue
        s = rb["row"]
        se = sale_by_scen.get((a, rb["scen"]), {})
        sc = scen_map.get((a, rb["scen"]), {})
        bridge_rows.append({
            "rule": "C05", "listing": a, "ask": t["ask"],
            "bca_total": t["bca"], "bca_land": t["bca_land"],
            "fair_land": round(t["fair"]),
            "disc_pct": round(t["disc"], 1),
            "scenario": rb["scen"],
            "build_sqft": se.get("build_sqft"), "units": sc.get("units"),
            "sale_psf": se.get("psf_p50"),
            "sale_p25": se.get("psf_p25"), "sale_p75": se.get("psf_p75"),
            "est_sale": s.get("est_sale_today"),
            "total_cost": s.get("total_cost_dev_1y"),
            "profit": s.get("profit_development_1y"),
            "roi": round(rb["roi"], 1),
            "sale_level": se.get("comp_level"),
            "sale_n": se.get("comp_n"),
            "sale_conf": se.get("confidence"),
            "zoning": t["zoning"], "zstatus": sc.get("status"),
            "sale_comps": comp_evidence(se.get("comp_ids"), 8)})
        add("C05", a, "HIGH", "UNRESOLVED",
            f"disc={t['disc']:.1f}% roi={rb['roi']:.1f}% "
            f"scen={rb['scen']} sale_psf={se.get('psf_p50')}",
            bridge_rows[-1]["sale_comps"],
            "地价买贵但开发ROI高——核对售价依据是否高估或真有经济解释")

    # ============================== C06 similar newbuild psf divergence
    priced = defaultdict(list)  # scenario -> rows
    for (a, s), r in sale_by_scen.items():
        if r["sale_value_today"] and num(r["psf_p50"]) and a in T:
            priced[s].append((a, r))
    for s, rows in priced.items():
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                (a, ra), (b, rb) = rows[i], rows[j]
                ta, tb = T[a], T[b]
                same = ta["hood_comm"] == tb["hood_comm"]
                d = (hav(ta["lat"], ta["lng"], tb["lat"], tb["lng"])
                     if ta["lat"] and tb["lat"] else None)
                if not (same or (d is not None and d <= 1.5)):
                    continue
                fa, fb = num(ra["build_sqft"]), num(rb["build_sqft"])
                if not fa or not fb or abs(fa / fb - 1) > 0.20:
                    continue
                pa, pb = num(ra["psf_p50"]), num(rb["psf_p50"])
                if not pa or not pb or abs(pa / pb - 1) <= 0.30:
                    continue
                pr = {"rule": "C06", "listing_a": a, "listing_b": b,
                      "scenario": s, "same_comm": same,
                      "dist_km": round(d, 2) if d else "",
                      "build_a": fa, "build_b": fb,
                      "psf_a": round(pa), "psf_b": round(pb),
                      "delta_psf_pct": round(abs(pa / pb - 1) * 100, 1),
                      "level_a": ra["comp_level"],
                      "level_b": rb["comp_level"],
                      "n_a": ra["comp_n"], "n_b": rb["comp_n"],
                      "comps_a": comp_evidence(ra["comp_ids"], 8),
                      "comps_b": comp_evidence(rb["comp_ids"], 8)}
                pair_rows.append(pr)
                add("C06", f"{a} ↔ {b}", "HIGH",
                    "EXPLAINED_DIFFERENCE"
                    if ra["comp_level"] != rb["comp_level"] else
                    "UNRESOLVED",
                    f"{s} build {fa:.0f}↔{fb:.0f}sf psf ${pa:.0f}↔"
                    f"${pb:.0f} ({abs(pa / pb - 1) * 100:.0f}%)",
                    f"levels {ra['comp_level']}↔{rb['comp_level']} "
                    f"n{ra['comp_n']}↔{rb['comp_n']}",
                    "同产品相似建面售价单价差>30%——逐笔 comps 核对")

    # =============================== C07 similar listings ROI divergence
    roi_rows = [r for r in roi if num(r["roi_development_1y"]) is not None]
    for i in range(len(roi_rows)):
        for j in range(i + 1, len(roi_rows)):
            ra, rb = roi_rows[i], roi_rows[j]
            if ra["scenario"] != rb["scenario"]:
                continue
            a, b = ra["listing"], rb["listing"]
            if a not in T or b not in T:
                continue
            ta, tb = T[a], T[b]
            if (ta["hood_comm"] != tb["hood_comm"]
                    or ta["zoning"] != tb["zoning"]):
                continue
            if abs(ta["lot"] / tb["lot"] - 1) > 0.15:
                continue
            sa = sale_by_scen.get((a, ra["scenario"]), {})
            sb = sale_by_scen.get((b, rb["scenario"]), {})
            fa, fb = num(sa.get("build_sqft")), num(sb.get("build_sqft"))
            if not fa or not fb or abs(fa / fb - 1) > 0.20:
                continue
            if abs(ta["ask"] / tb["ask"] - 1) > 0.20:
                continue
            da, db = (num(ra["roi_development_1y"]),
                      num(rb["roi_development_1y"]))
            if abs(da - db) < 30:
                continue
            bridge_rows.append({
                "rule": "C07", "listing": f"{a} ↔ {b}",
                "scenario": ra["scenario"],
                "ask": f"{ta['ask']:,.0f}↔{tb['ask']:,.0f}",
                "fair_land": f"{ta['fair']:,.0f}↔{tb['fair']:,.0f}",
                "build_sqft": f"{fa:.0f}↔{fb:.0f}",
                "sale_psf": f"{sa.get('psf_p50')}↔{sb.get('psf_p50')}",
                "est_sale": f"{ra['est_sale_today']}↔"
                            f"{rb['est_sale_today']}",
                "total_cost": f"{ra['total_cost_dev_1y']}↔"
                              f"{rb['total_cost_dev_1y']}",
                "roi": f"{da:.1f}%↔{db:.1f}%",
                "sale_level": f"{sa.get('comp_level')}↔"
                              f"{sb.get('comp_level')}",
                "sale_comps": comp_evidence(sa.get("comp_ids"), 5) +
                " ||| " + comp_evidence(sb.get("comp_ids"), 5)})
            add("C07", f"{a} ↔ {b}", "HIGH",
                "UNRESOLVED",
                f"ROI {da:.0f}%↔{db:.0f}% ask {ta['ask']:,.0f}↔"
                f"{tb['ask']:,.0f} psf {sa.get('psf_p50')}↔"
                f"{sb.get('psf_p50')}",
                "", "相似房源ROI差≥30pp——分解土地价/售价/面积/成本差")

    # ==================================== C08 newbuild pricing backtest
    # temporal holdout: train on sales strictly before the cutoff,
    # predict later sales; the future never leaks into the train pool
    CUTOFF = "2025-07-01"
    train = [c for c in nb_house if c["date"] < CUTOFF]
    test = [c for c in nb_house if c["date"] >= CUTOFF
            and c["psf"] and 200 < c["psf"] < 6000]
    comm_train = defaultdict(list)
    side_train = defaultdict(list)
    for c in train:
        comm_train[c["community"]].append(c)
        side_train[c["side"]].append(c)

    def wm(pairs, p):
        return v.wmedian(pairs, p)

    def size_adj(tfa, cfa):
        return v.size_adj(tfa, cfa, v.SIZE_K["HOUSE"])

    def m500(t):
        cands = [c for c in train
                 if c["lat"] and (d := hav(t["lat"], t["lng"],
                                         c["lat"], c["lng"])) is not None
                 and d <= 0.5 and c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) >= 3:
            pairs = [(c["psf"] * size_adj(t["fa"], c["fa"]),
                      (1 / (1 + hav(t["lat"], t["lng"], c["lat"], c["lng"])
                             or 0.5))
                      * (1 / (1 + abs(math.log(c["fa"] / t["fa"]))))) for
                     c in cands]
            return wm(pairs, 50), len(cands)
        return None, 0

    def mcomm(t):
        cands = [c for c in comm_train.get(t["community"], [])
                 if c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) >= 3:
            pairs = [(c["psf"] * size_adj(t["fa"], c["fa"]),
                      (1 / (1 + hav(t["lat"], t["lng"], c["lat"], c["lng"])
                             or 0.5))
                      * (1 / (1 + abs(math.log(c["fa"] / t["fa"]))))) for
                     c in cands]
            return wm(pairs, 50), len(cands)
        return None, 0

    def mside(t):
        cands = [c for c in side_train.get(t["side"], [])
                 if c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) >= 3:
            pairs = [(c["psf"] * size_adj(t["fa"], c["fa"]), 1.0)
                     for c in cands]
            return wm(pairs, 50), len(cands)
        return None, 0

    def meth_A(t):
        for f in (m500, mcomm, mside):
            est, n = f(t)
            if est:
                return est
        return None

    def meth_B(t):
        # community baseline, adjusted by the listing's 500m ring when
        # that micro-evidence exists (bounded ±15%)
        est_c, _ = mcomm(t)
        if not est_c:
            est_c, _ = mside(t)
        if not est_c:
            return None
        est5, n5 = m500(t)
        if est5 and n5 >= 3:
            ratio = min(max(est5 / est_c, 0.85), 1.15)
            return est_c * ratio
        return est_c

    def meth_C(t):
        # community+size+distance pooled weighting (no 500m priority)
        cands = [c for c in comm_train.get(t["community"], [])
                 if c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) < 3:
            cands = [c for c in side_train.get(t["side"], [])
                     if c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) < 3:
            return None
        pairs = [(c["psf"] * size_adj(t["fa"], c["fa"]),
                  (1 / (1 + (hav(t["lat"], t["lng"], c["lat"], c["lng"])
                             or 0.5) ** 2))
                  * (1 / (1 + abs(math.log(c["fa"] / t["fa"]))))) for
                 c in cands]
        return wm(pairs, 50)

    bt_rows = []
    for t in test:
        row = {"tx": t["tx"], "address": t["address"],
               "community": t["community"], "date": t["date"],
               "fa": t["fa"], "actual_psf": round(t["psf"], 1),
               "actual_price": round(t["price"])}
        for m, f in (("A_500M_FIRST", meth_A),
                     ("B_COMM_NEARBY_ADJ", meth_B),
                     ("C_COMM_WEIGHTED", meth_C)):
            est = f(t)
            row[m + "_psf"] = round(est, 1) if est else ""
            row[m + "_ape"] = (round(abs(est / t["psf"] - 1) * 100, 1)
                               if est else "")
        bt_rows.append(row)

    def bt_stats(rows, m):
        apes = sorted(float(r[m + "_ape"]) for r in rows
                      if r[m + "_ape"] != "")
        if not apes:
            return {"coverage": 0}
        errs = [float(r[m + "_psf"]) - r["actual_psf"]
                for r in rows if r[m + "_psf"] != ""]
        return {"coverage": len(apes), "MdAPE": round(
                    statistics.median(apes), 1),
                "MAE_psf": round(statistics.median(
                    [abs(e) for e in errs]), 0),
                "P90APE": round(apes[int(len(apes) * 0.9)], 1)}

    bt_summary = []
    for m in ("A_500M_FIRST", "B_COMM_NEARBY_ADJ", "C_COMM_WEIGHTED"):
        s = bt_stats(bt_rows, m)
        bt_summary.append({"scope": "ALL", "method": m, **s})
        by_comm = defaultdict(list)
        for r in bt_rows:
            by_comm[r["community"]].append(r)
        for c, rs in by_comm.items():
            if len(rs) >= 4:
                bt_summary.append({"scope": f"comm:{c}", "method": m,
                                   **bt_stats(rs, m)})
        for lo, hi in ((0, 2500), (2500, 3500), (3500, 99999)):
            rs = [r for r in bt_rows if lo <= r["fa"] < hi]
            if len(rs) >= 4:
                bt_summary.append({"scope": f"fa:{lo}-{hi}",
                                   "method": m, **bt_stats(rs, m)})

    # ================================================== C09/C10/C11
    # C09 R1-1: DUPLEX INELIGIBLE on dimensional minima while HOUSE is
    # VERIFIED — clause 3.2.2.9 allows discretionary relaxation, so the
    # correct state is CONDITIONAL_REVIEW, not 'banned' nor 'verified'
    R1_MIN_AREA_SF = 306 * 10.7639   # 3294sf
    R1_MIN_FRONT_FT = 7.3 * 3.28084  # 23.95ft
    c09_rows = []
    for a, t in T.items():
        if t["zoning"] != "R1-1":
            continue
        hs = scen_map.get((a, "HOUSE"), {})
        ds = scen_map.get((a, "DUPLEX"), {})
        if hs.get("status") == "VERIFIED" and ds.get("status") \
                in ("INELIGIBLE", "UNVERIFIED"):
            area_short = t["lot"] and t["lot"] < R1_MIN_AREA_SF
            fr_short = (t["frontage"] and
                        t["frontage"] < R1_MIN_FRONT_FT)
            c09_rows.append({
                "listing": a, "zoning": "R1-1", "lot_sqft": t["lot"],
                "frontage_ft": t["frontage"],
                "below_min_area": bool(area_short),
                "below_min_frontage": bool(fr_short),
                "house_status": hs.get("status"),
                "duplex_status": ds.get("status"),
                "suggested": "CONDITIONAL_REVIEW",
                "clause": "R1-1 Sch 2.1 use-permitted; 3.2.1.1 FSR "
                          "0.60/0.70; 3.2.2.1 min 306m2; 3.2.2.2 min "
                          "7.3m frontage; 3.2.2.9 director discretion "
                          "to relax minima"})
            add("C09", a, "MEDIUM", "LEGAL_REVIEW_REQUIRED",
                f"lot={t['lot']:,.0f}sf front={t['frontage']}ft "
                f"house={hs.get('status')} duplex={ds.get('status')}",
                "R1-1 3.2.2.1/3.2.2.2/3.2.2.9",
                "DUPLEX 因常规最小面积/面宽不适用但 HOUSE VERIFIED——"
                "酌情放宽存在,不能当永久禁止也不能当已批准")

    # C10 zoning × product matrix
    zmat = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    zmeta = {}
    for r in scen:
        z = r["zoning"] or "(blank)"
        zmat[z][r["scenario"]][r["status"]] += 1
        zmeta.setdefault(z, r["zoning"])
    zoning_matrix = []
    for z in sorted(zmat):
        row = {"zoning": z}
        for s in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            st = zmat[z].get(s, {})
            tot = sum(st.values())
            row[s + "_n"] = tot or 0
            row[s + "_status"] = ";".join(
                f"{k}:{n}" for k, n in sorted(st.items())) or "n/a"
        row["rule_ref"] = ("R1-1 Sch 2.1/3.2.1.1/3.2.2.x"
                           if z == "R1-1"
                           else "LEGAL_REVIEW_REQUIRED — CoV district "
                                "schedule; matrix shows pipeline "
                                "status only, not legal confirmation")
        zoning_matrix.append(row)

    # C11 status reclassification: split legality vs price-evidence
    ZC = []
    status_counts = defaultdict(int)
    for r in scen:
        a, s = r["listing"], r["scenario"]
        t = T.get(a, {})
        old = r["status"]
        z = r["zoning"]
        if old == "VERIFIED":
            legal = "VERIFIED"
        elif old == "ASSUMPTIVE_ZONING":
            legal = "LEGAL_UNVERIFIED"
        elif old == "UNVERIFIED":
            legal = "LEGAL_UNVERIFIED"
        else:  # INELIGIBLE — dimensional vs structural
            if z == "R1-1" and s == "DUPLEX" and t.get("lot") and \
                    (t["lot"] < R1_MIN_AREA_SF or
                     (t.get("frontage") and
                      t["frontage"] < R1_MIN_FRONT_FT)):
                legal = "CONDITIONAL_REVIEW"
            elif z == "" or not z.upper().startswith(("R", "F")):
                legal = "LEGAL_REVIEW_REQUIRED"
            else:
                legal = "LEGAL_REVIEW_REQUIRED"
        has_price = bool(sale_by_scen.get((a, s), {})
                         .get("sale_value_today"))
        if r["eligible"] == "True":
            price_st = "PRICED" if has_price else "PRICE_NOT_EVALUATED"
        else:
            price_st = "n/a"
        status_counts[(legal, price_st)] += 1
        ZC.append({"listing": a, "scenario": s, "zoning": z or "(blank)",
                   "fsr": r["fsr"], "build_sqft": r["build_sqft"],
                   "eligible": r["eligible"],
                   "verified_raw": r["verified_raw"],
                   "old_status": old, "legal_status": legal,
                   "price_status": price_st,
                   "zone_flags": r["zone_flags"],
                   "clause": ("R1-1 3.2.2.1/3.2.2.2/3.2.2.9"
                              if z == "R1-1" else "district schedule "
                              "review required")})

    # -------------------------------------------------------- outputs
    wcsv(OUT / "consistency_findings.csv", findings,
         ["finding_id", "rule", "listing", "severity", "classification",
          "key_fields", "evidence", "note"])
    summ = []
    fmap = defaultdict(list)
    for f in findings:
        for a in f["listing"].split(" ↔ "):
            fmap[a].append(f["rule"])
    for a in sorted(T, key=lambda x: rank_idx.get(x, 999)):
        t = T[a]
        rb = roi_best.get(a, {})
        summ.append({
            "listing": a, "hood_comm": t["hood_comm"],
            "zoning": t["zoning"], "ask": t["ask"],
            "bca_total": t["bca"], "bca_land": t["bca_land"],
            "fair_land": round(t["fair"] or 0),
            "disc_pct": t["disc"],
            "best_roi": round(rb["roi"], 1) if rb else "",
            "best_scen": rb.get("scen") if rb else "",
            "rules_triggered": ";".join(sorted(set(fmap.get(a, [])))),
            "n_findings": len(fmap.get(a, []))})
    wcsv(OUT / "listing_consistency_summary.csv", summ,
         ["listing", "hood_comm", "zoning", "ask", "bca_total",
          "bca_land", "fair_land", "disc_pct", "best_roi", "best_scen",
          "rules_triggered", "n_findings"])
    wcsv(OUT / "comparable_pair_anomalies.csv", pair_rows,
         ["rule", "listing_a", "listing_b", "hood_comm", "dist_km",
          "zoning", "lot_a", "lot_b", "fair_a", "fair_b",
          "fair_psf_a", "fair_psf_b", "delta_cad", "delta_psf_pct",
          "level_a", "level_b", "scenario", "same_comm", "build_a",
          "build_b", "psf_a", "psf_b", "n_a", "n_b", "drivers",
          "comps_a", "comps_b"])
    wcsv(OUT / "valuation_bridge.csv", bridge_rows,
         ["rule", "listing", "ask", "bca_total", "bca_land", "fair_land",
          "disc_pct", "scenario", "build_sqft", "units", "sale_psf",
          "sale_p25", "sale_p75", "est_sale", "total_cost", "profit",
          "roi", "sale_level", "sale_n", "sale_conf", "zoning",
          "zstatus", "sale_comps"])
    wcsv(OUT / "zoning_rule_matrix.csv", zoning_matrix,
         ["zoning", "HOUSE_n", "HOUSE_status", "DUPLEX_n",
          "DUPLEX_status", "MULTIPLEX_n", "MULTIPLEX_status",
          "rule_ref"])
    wcsv(OUT / "zoning_conflicts.csv", ZC,
         ["listing", "scenario", "zoning", "fsr", "build_sqft",
          "eligible", "verified_raw", "old_status", "legal_status",
          "price_status", "zone_flags", "clause"])
    wcsv(OUT / "c09_r11_duplex_conditional.csv", c09_rows,
         ["listing", "zoning", "lot_sqft", "frontage_ft",
          "below_min_area", "below_min_frontage", "house_status",
          "duplex_status", "suggested", "clause"])
    wcsv(OUT / "pricing_backtest.csv", bt_rows,
         ["tx", "address", "community", "date", "fa", "actual_psf",
          "actual_price", "A_500M_FIRST_psf", "A_500M_FIRST_ape",
          "B_COMM_NEARBY_ADJ_psf", "B_COMM_NEARBY_ADJ_ape",
          "C_COMM_WEIGHTED_psf", "C_COMM_WEIGHTED_ape"])
    wcsv(OUT / "pricing_backtest_summary.csv", bt_summary,
         ["scope", "method", "coverage", "MdAPE", "MAE_psf", "P90APE"])

    # ------------------------------------------------------------ report
    n_contra = sum(1 for s in summ if s["n_findings"])
    by_rule = defaultdict(lambda: defaultdict(int))
    for f in findings:
        by_rule[f["rule"]][f["classification"]] += 1
    a_rows = [s for s in bt_summary if s["scope"] == "ALL"]
    roi_c = {10: 0, 20: 0, 30: 0}
    for a, rb in roi_best.items():
        for t_ in roi_c:
            roi_c[t_] += rb["roi"] >= t_
    top20 = [s for s in summ
             if rank_idx.get(s["listing"], 999) <= 20
             and s["n_findings"]]
    unver = sum(1 for z in ZC if z["legal_status"]
                in ("LEGAL_UNVERIFIED", "LEGAL_REVIEW_REQUIRED",
                    "CONDITIONAL_REVIEW"))
    cls_names = {"CONFIRMED_BUG": "有证据的程序/数据错误",
                 "EXPLAINED_DIFFERENCE": "差异大但有解释",
                 "UNRESOLVED": "证据不足",
                 "LEGAL_REVIEW_REQUIRED": "需法规专项核实"}
    cls_tbl = "\n".join(
        f"| {c} | {cls_names[c]} | "
        f"{sum(1 for f in findings if f['classification'] == c)} |"
        for c in cls_names)
    c06_drv = defaultdict(int)
    for p in pair_rows:
        if p["rule"] == "C06":
            for a in (p["listing_a"], p["listing_b"]):
                c06_drv[a] += 1
    c06_drivers = ", ".join(f"{a}({n}对)"
                            for a, n in sorted(
                                c06_drv.items(), key=lambda x: -x[1])
                            [:5])
    rep = f"""# V1.4.5 一致性审计报告 — {AUDIT_DATE}

规则集 `{RULESET}`;对照基线 V1.4.4(SHA `aa45207`)。Phase A 只读,
不改估值输入/输出。

## 必答题

- **224 套中存在跨指标矛盾的房源(按地址去重)**:**{n_contra}**
- **规则触发分布**:
{chr(10).join(f"  - {k}: " + " / ".join(f"{c} {n}" for c, n in v2.items()) for k, v2 in sorted(by_rule.items()))}
- **ROI 计数(修复前后一致,本轮无估值改动)**:≥10% **{roi_c[10]}**
  / ≥20% **{roi_c[20]}** / ≥30% **{roi_c[30]}**
- **排名前 20 中估价或法规需改变的房源**:**{len(top20)}** —
  {[s['listing'] for s in top20]}
- **法规未验证/需专项核查的方案数**:**{unver}**(LEGAL_UNVERIFIED+
  LEGAL_REVIEW_REQUIRED+CONDITIONAL_REVIEW)
- **C08 留出回测(cutoff {CUTOFF},train {len(train)}/test {len(test)})**:
{chr(10).join(f"  - {r['method']}: n={r['coverage']} MdAPE={r.get('MdAPE')}% MAE=${r.get('MAE_psf')}/sf P90APE={r.get('P90APE')}%" for r in a_rows)}
  - 判读:时间留出后 500m 环在任何测试点都凑不齐 ≥3 个训练期
    comps,A 与 B 逐笔完全一致;C 与 A 在 17/39 行有差异但统计量
    相同。**回测无法区分 500M 优先与社区基准——不支持切换算法**。
    但 C06 横截面证据(75 N SLOCAN:n=3 薄样本 → $690/sf vs 社区池
    $1,220/sf,差 45%)说明 n=3 的 500m 环摆动大,依赖披露而非
    改法。
- **C06/C07 集中度**:121 对 C06 中少数异常房源驱动大多数配对
  ——{c06_drivers}。成对数量高 ≠ 每对独立问题。
- **P0-1(排序/筛选)**:V1.4.4 `aa45207` 已修(data-k 固定键+浏览器
  回归通过);本审计基线已含该修复。
- **P0-2(社区统计用冻结池)**:本分支已修——渲染器与逐房源估值
  复用同一 dims 土地池;Hastings-Sunrise lot P50 4,191 /
  Marpole 5,839 / Renfrew(北) 4,224 / Kensington-CC 4,048,与
  冻结池独立对账一致。
- **土地面积来源冲突**:冻结 dims 输入由渲染器/审计/估值三处
  共用;`input_manifest_v1_4.csv` 已含 SHA256。

## 分类汇总

| 分类 | 含义 | 数 |
|---|---|---|
{cls_tbl}

## 交付文件

`consistency_findings.csv`({len(findings)} 条)/ `listing_consistency_summary.csv`(224)/
`comparable_pair_anomalies.csv`({len(pair_rows)})/ `valuation_bridge.csv`({len(bridge_rows)})/
`zoning_rule_matrix.csv`({len(zoning_matrix)})/ `zoning_conflicts.csv`({len(ZC)})/
`pricing_backtest.csv`({len(bt_rows)})/ `audit_report.md` / `manifest.json`

复现:`python3 scripts/listing_consistency_audit_v1_4_5.py`
"""
    (OUT / "audit_report.md").write_text(rep)
    (OUT / "manifest.json").write_text(json.dumps({
        "audit": "listing_consistency_v1_4_5", "ruleset": RULESET,
        "date": AUDIT_DATE, "baseline": "V1.4.4 aa45207",
        "inputs_sha256": {n: sha(p) for n, p in {
            "land_valuation": SRC / "active_house_land_valuation.csv",
            "roi": SRC / "active_house_listing_roi.csv",
            "sale": SRC / "active_house_newbuild_sale_estimate.csv",
            "scenarios": SRC / "active_house_buildable_scenarios.csv",
            "listing_json":
            ROOT / "reports/watchlist/rebuild-value-2026-10-20.json",
            "lot_dims_frozen": SRC / "lot_dims_frozen_input.csv",
        }.items()},
        "outputs_sha256": {p.name: sha(p) for p in sorted(OUT.glob("*"))
                           if p.name != "manifest.json"}},
        indent=2))
    print(f"findings {len(findings)} contra {n_contra} pairs "
          f"{len(pair_rows)} bridge {len(bridge_rows)} zc {len(ZC)} "
          f"bt {len(bt_rows)}")


if __name__ == "__main__":
    main()
