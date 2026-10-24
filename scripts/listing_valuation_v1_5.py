#!/usr/bin/env python3
"""V1.5 listing valuation — backtest-selected estimators + intervals.

Design decisions, each backed by scripts/valuation_v1_5_backtest.py
(backtest_summary.json must exist; run that first):

  Sale (HOUSE/DUPLEX): ANCHOR = community base $/sf (shrunk toward the
    side median) x nearby correction (<=0.6km, shrunk, capped ±15%) x
    fitted size elasticity. Replaces V1.4 "500m first" which had higher
    LOPO error and a k=-0.55 HOUSE size curve the data does not support.
  Sale interval: P10/P90 of the product's LOPO actual/estimate ratio —
    ROI is reported at P50 and P10 (downside) sale.
  MULTIPLEX: unit-price proxy kept verbatim from V1.4 (no floor area in
    feed => cannot be backtested); shown, never used for ranking.
  Land: BCA-calibrated (listing BCA land x community median of
    sale/BCA-land in the land-dominant pool); V1.4 comps value kept as
    an independent cross-check (C02 fires when they disagree >20%).
  Legality: five-state split instead of eligible/verified booleans.

V3s production valuation, cost tiers, rules: untouched (cost_model and
geometry are imported from V1.4 as-is). Research layer only.
Run: python3 scripts/valuation_v1_5_backtest.py
     WATCHLIST=<path> python3 scripts/listing_valuation_v1_5.py
"""
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import listing_level_valuation_v1_4 as v  # noqa: E402
import valuation_v1_5_backtest as bt      # noqa: E402

OUT = bt.OUT
V14 = v.DOCS
WATCHLIST = Path(os.environ.get(
    "WATCHLIST", ROOT / "data/research/watchlist/property-watchlist.json"))
MODEL_VERSION = "listing_valuation_v1_5_2"
SIDE = {"东区": "Vancouver East", "西区": "Vancouver West"}


def rd(x, n=0):
    return "" if x is None else round(x, n) if n else round(x)


# ================================================================ legality
def legal_state(s, zoning):
    """-> (state, reason). States:
    VERIFIED     rule + geometry verified, residential zone, no variance
    CONDITIONAL  buildable only via variance / relaxation, or verified
                 on an assumed-residential zone (C-*, CD-1, blank)
    ELIGIBLE     standard lot criteria met, rule application unverified
    NOT_ELIGIBLE standard criteria fail, no variance path modelled
    NO_RULE      no geometry/rule for this zone (CD-1, C-*, blank)"""
    resi = v.zoning_residential(zoning)
    if not s.get("build_sqft"):
        return "NO_RULE", "no geometry for zone " + (zoning or "blank")
    if s.get("variance_required"):
        return "CONDITIONAL", "variance_required (lot below standard)"
    if s.get("verified") and not resi:
        return "CONDITIONAL", "verified on non-residential/unknown zone"
    if s.get("verified"):
        return "VERIFIED", ""
    if s.get("eligible"):
        return "ELIGIBLE", "standard criteria met; rule unverified"
    return "NOT_ELIGIBLE", "standard criteria not met"


# ============================================================ estimators
def bca_ratio_tables(pool, tax):
    by_c, by_s = defaultdict(list), defaultdict(list)
    for r in pool:
        bl = (tax.get(r["pid"], {}).get(2026) or (None,))[0]
        if bl and r["price_2026_09"]:
            by_c[r["community"]].append(r["price_2026_09"] / bl)
            by_s[r["side"]].append(r["price_2026_09"] / bl)
    med = statistics.median
    return ({c: (med(l), len(l)) for c, l in by_c.items() if len(l) >= 5},
            {s: (med(l), len(l)) for s, l in by_s.items()})


def load_inputs():
    listings = [r for r in json.load(open(v.LISTING_JSON))["rows"]
                if r["status"] == "ACTIVE"]
    wl = {e["address"].upper(): e
          for e in json.load(open(WATCHLIST))["entries"]}
    land14 = {r["listing"]: r for r in csv.DictReader(
        open(V14 / "active_house_land_valuation.csv"))}
    sale14 = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(V14 / "active_house_newbuild_sale_estimate.csv"))}
    summ = json.load(open(OUT / "backtest_summary.json"))
    return listings, wl, land14, sale14, summ


def main():
    listings, wl, land14, sale14, summ = load_inputs()
    pools, hr, arch = bt.load_pools()
    sale_pool = {n: [c for c in pools[n] if c["psf"] and 200 < c["psf"] < 6000
                     and c["lat"]] for n in ("HOUSE", "DUPLEX")}
    k_fit = {n: bt.fit_k(p) for n, p in sale_pool.items()}
    # V1.5.1: stress band from the strict forward (expanding-window,
    # no future info) test, not the LOPO set the method was chosen on
    st = summ["strict_v1_5_1"]
    band = {n: st[n]["forward_expanding"]["ANCHOR"]
            for n in ("HOUSE", "DUPLEX")}
    support = {n: st[n]["area_support"]["fa_p95"]
               for n in ("HOUSE", "DUPLEX")}
    tax = v.load_tax()
    land_pool, _, _ = v.build_land_pool(hr, arch, v.load_resolved_yb(),
                                        tax, v.load_lot_dims())
    r_comm, r_side = bca_ratio_tables(land_pool, tax)
    lband = st["LAND"]["BCA_CAL_ASOF"]

    rows, scen_out, price_fix = [], [], []
    for r in listings:
        addr = r["address"]
        w = wl.get(addr.upper())
        asking = w["latest_asking"] if w and w.get("latest_asking") \
            else r["asking"]
        if asking != r["asking"]:
            price_fix.append({"listing": addr, "snapshot_ask": r["asking"],
                              "latest_asking": asking})
        l14 = land14[addr]
        comm = v.HOOD_COMM_ALIAS.get(r["hood"], r["hood"])
        side = SIDE.get(r["side"], "")
        lot = v.num(r["lot_sqft"])
        # ---- land
        ratio, rn, rlvl = None, 0, ""
        if comm in r_comm:
            (ratio, rn), rlvl = r_comm[comm], "COMM"
        elif side in r_side:
            (ratio, rn), rlvl = r_side[side], "SIDE"
        bl = v.num(r.get("bca_land"))
        fair = bl * ratio if bl and ratio else None
        comps_fair = v.num(l14["fair_land_value_today"])
        land_src = "BCA_CAL" if fair else "V14_COMPS"
        if not fair:
            fair = comps_fair
        row = {"listing": addr, "hood": r["hood"], "comm": comm,
               "side": side, "zoning": r.get("zoning") or "",
               "asking": asking, "bca": v.num(r.get("bca")), "bca_land": bl,
               "lot": lot, "frontage": v.num(r.get("frontage_ft")),
               "lat": v.num(r.get("lat")), "lng": v.num(r.get("lng")),
               "link": r.get("link", ""),
               "fair_land": fair, "land_src": land_src,
               "fair_land_p10": fair * lband["p10_ratio"] if fair else None,
               "fair_land_p90": fair * lband["p90_ratio"] if fair else None,
               "bca_ratio": ratio, "bca_ratio_n": rn, "bca_ratio_lvl": rlvl,
               "comps_fair": comps_fair,
               "comps_level": l14["land_comp_level"],
               "disc": 100 * (fair - asking) / fair if fair else None,
               "comps_disc": 100 * (comps_fair - asking) / comps_fair
               if comps_fair else None,
               "scen": {}}
        # ---- scenarios
        for name in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            s = r["analysis"]["scenarios"].get(name)
            if not s:
                continue
            state, why = legal_state(s, row["zoning"])
            sc = {"name": name, "state": state, "why": why,
                  "build_sqft": v.num(s.get("build_sqft")),
                  "net_saleable": v.num(s.get("net_saleable_area"))
                  or v.num(s.get("build_sqft")),
                  "units": s.get("units") or 1,
                  "variance": bool(s.get("variance_required")),
                  "verified_raw": bool(s.get("verified")),
                  "eligible_raw": bool(s.get("eligible"))}
            row["scen"][name] = sc
            scen_out.append({"listing": addr, "scenario": name,
                             "zoning": row["zoning"], "state": state,
                             "reason": why, "eligible_raw": sc["eligible_raw"],
                             "verified_raw": sc["verified_raw"],
                             "variance_required": sc["variance"],
                             "lot": lot, "frontage": row["frontage"]})
            if state == "NO_RULE":
                continue
            s14 = sale14.get((addr, name), {})
            if name == "MULTIPLEX":
                est = v.num(s14.get("sale_value_today"))
                sc.update(method="V14_UNITPX_UNTESTED", est=est,
                          psf=None, level=s14.get("comp_level", ""),
                          n=s14.get("comp_n", ""), p10=None, p90=None)
                if not est:
                    sc.update(method="NO_EVIDENCE")
            else:
                ua = sc["net_saleable"] / sc["units"]
                # beyond P95 of backtested unit areas the error band is
                # not evidenced — keep estimate, exclude from ranking
                sc["out_of_support"] = ua > support[name]
                sc["unit_area"] = ua
                t = {"lat": row["lat"], "lng": row["lng"],
                     "community": comm, "side": side, "area": ua}
                psf, det = bt.est_anchor(t, sale_pool[name], k_fit[name])
                est = psf * sc["net_saleable"] if psf else None
                sc.update(method="ANCHOR", est=est, psf=psf,
                          level=det["level"], n=det.get("n_comm", 0),
                          n_near=det.get("n_near", 0),
                          near_factor=det.get("near_factor"),
                          p10=est * band[name]["p10_ratio"] if est else None,
                          p90=est * band[name]["p90_ratio"] if est else None,
                          v14_psf=v.num(s14.get("psf_p50")),
                          v14_level=s14.get("comp_level", ""))
            if sc.get("est"):
                cm = v.cost_model(asking, sc, sc["est"], v.BASE_YEARS, "EXCL")
                # round once at source: summary counts == displayed CSV
                sc["roi"] = round(100 * cm["roi"], 1)
                sc["cost"] = cm["total"]
                if sc.get("p10"):
                    sc["roi_p10"] = round(100 * v.cost_model(
                        asking, sc, sc["p10"], v.BASE_YEARS, "EXCL")["roi"], 1)
                    mb = v.max_bid(sc, sc["p10"], 0.0, v.BASE_YEARS)
                    sc["breakeven_bid_p10"] = mb if isinstance(mb, float) \
                        else None
                mb = v.max_bid(sc, sc["est"], 0.20, v.BASE_YEARS)
                sc["mb20"] = mb if isinstance(mb, float) else None
                # fixed sale haircuts alongside the error-quantile stress
                for cut in (10, 20):
                    sc[f"roi_m{cut}"] = round(100 * v.cost_model(
                        asking, sc, sc["est"], v.BASE_YEARS, "EXCL",
                        -cut / 100)["roi"], 1)
        # V1.5.2: legality is the only ranking gate. Out-of-support area
        # is disclosed (flag) — it does not remove a scenario from the
        # full research ranking; an in-support view is kept separately.
        ranked = [sc for sc in row["scen"].values()
                  if sc["state"] == "VERIFIED" and sc.get("method") == "ANCHOR"
                  and sc.get("roi") is not None]
        best = max(ranked, key=lambda x: x["roi"]) if ranked else None
        ins = [sc for sc in ranked if not sc.get("out_of_support")]
        best_in = max(ins, key=lambda x: x["roi"]) if ins else None
        cond = [sc for sc in row["scen"].values()
                if sc["state"] in ("CONDITIONAL", "ELIGIBLE")
                and sc.get("method") == "ANCHOR" and sc.get("roi") is not None]
        row["best"] = best["name"] if best else None
        row["roi"] = best["roi"] if best else None
        row["roi_p10"] = best.get("roi_p10") if best else None
        row["roi_m10"] = best.get("roi_m10") if best else None
        row["roi_m20"] = best.get("roi_m20") if best else None
        row["best_oos"] = bool(best and best.get("out_of_support"))
        row["best_in"] = best_in["name"] if best_in else None
        row["roi_in"] = best_in["roi"] if best_in else None
        row["cond_best"] = max(cond, key=lambda x: x["roi"]) if cond else None
        rows.append(row)
    return finish(rows, scen_out, price_fix, k_fit, band, lband, support)


# ======================================================== contradictions
def similar(a, b, lot_tol=0.15):
    return (a["comm"] == b["comm"] and a["zoning"] == b["zoning"]
            and a["lot"] and b["lot"]
            and abs(a["lot"] / b["lot"] - 1) <= lot_tol)


def detect(rows):
    """C01-C10 audit triggers — never filters, only flags for review."""
    out = []

    def add(code, listing, detail, other="", sev="REVIEW"):
        out.append({"code": code, "listing": listing, "other": other,
                    "severity": sev, "detail": detail, "status": "OPEN"})

    for r in rows:
        a, bt_, bl, f = r["asking"], r["bca"], r["bca_land"], r["comps_fair"]
        # C01: Ask well below BCA but comps model calls it overpriced
        if bt_ and a / bt_ <= 0.90 and r["comps_disc"] is not None \
                and r["comps_disc"] <= -10:
            add("C01", r["listing"], f"Ask/BCA={a / bt_:.2f}; V1.4 comps "
                f"premium {-r['comps_disc']:.1f}%; V1.5 BCA-cal disc "
                f"{r['disc']:.1f}%" if r["disc"] is not None else "")
        # C02: comps land vs BCA-calibrated land
        if f and r["land_src"] == "BCA_CAL" and \
                abs(f / r["fair_land"] - 1) > 0.20:
            add("C02", r["listing"], f"comps {f:,.0f} vs BCA-cal "
                f"{r['fair_land']:,.0f} ({f / r['fair_land'] - 1:+.0%}); "
                f"comps level {r['comps_level']}")
        # C03: land overpriced but high ROI
        if r["disc"] is not None and r["disc"] <= -10 and \
                (r["roi"] or -99) >= 20:
            add("C03", r["listing"], f"land premium {-r['disc']:.1f}% "
                f"with ROI {r['roi']:.1f}% ({r['best']})")
        # C08: legality state vs sale evidence
        for sc in r["scen"].values():
            if sc["state"] in ("NOT_ELIGIBLE", "NO_RULE") and sc.get("est"):
                add("C08", r["listing"], f"{sc['name']} {sc['state']} "
                    "but carries a sale estimate", sev="DEFECT")
            if sc["state"] == "VERIFIED" and sc["name"] != "MULTIPLEX" \
                    and not sc.get("est"):
                add("C08", r["listing"], f"{sc['name']} VERIFIED but no "
                    "sale estimate", sev="DEFECT")
        # C07: production rule output contradicts itself — verified=True
        # while variance_required=True on the same scenario (V1.4 read
        # only `verified`, so these ranked as fully legal)
        for sc in r["scen"].values():
            if sc["verified_raw"] and sc["variance"]:
                add("C07", r["listing"], f"{sc['name']} verified=True and "
                    f"variance_required=True (lot {r['lot'] or 0:,.0f}sf); "
                    "V1.5 state CONDITIONAL, excluded from ranking",
                    sev="DEFECT")
        # C06: V1.4 vs V1.5 sale $/sf jump on same scenario
        for sc in r["scen"].values():
            if sc.get("v14_psf") and sc.get("psf") and \
                    abs(sc["v14_psf"] / sc["psf"] - 1) > 0.30:
                add("C06", r["listing"], f"{sc['name']} V1.4 "
                    f"${sc['v14_psf']:,.0f}/sf@{sc['v14_level']} vs V1.5 "
                    f"${sc['psf']:,.0f}/sf ({sc['v14_psf'] / sc['psf'] - 1:+.0%})")
    # pairwise C04/C05
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if not similar(a, b):
                continue
            if a["fair_land"] and b["fair_land"] and \
                    abs(a["fair_land"] - b["fair_land"]) > 500_000:
                add("C04", a["listing"], f"land {a['fair_land']:,.0f} vs "
                    f"{b['fair_land']:,.0f}; lots {a['lot']:,.0f}/"
                    f"{b['lot']:,.0f}", other=b["listing"])
            for n in ("HOUSE", "DUPLEX"):
                sa, sb = a["scen"].get(n), b["scen"].get(n)
                if not (sa and sb and sa.get("roi") is not None
                        and sb.get("roi") is not None):
                    continue
                if (abs(a["asking"] / b["asking"] - 1) <= 0.20
                        and abs(sa["net_saleable"] / sb["net_saleable"] - 1)
                        <= 0.20 and abs(sa["roi"] - sb["roi"]) >= 30):
                    add("C05", a["listing"], f"{n} ROI {sa['roi']:.1f}% vs "
                        f"{sb['roi']:.1f}%", other=b["listing"])
    return out


def bridge(rows):
    """74 -> N decomposition. Steps applied cumulatively per listing:
    S0 V1.4.4 page (eligible scenarios, V1.4 sale)
    S1 + legal scope (only V1.5 VERIFIED), V1.4 sale
    S2 + V1.5 sale (ANCHOR), all VERIFIED
    S3 + area-support gate (= V1.5.1 final)"""
    r14 = {(r["listing"], r["scenario"]): float(r["roi_development_1y"])
           for r in csv.DictReader(open(V14 / "active_house_listing_roi.csv"))
           if r["gst_scenario"] == "EXCL"}
    sc14 = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(V14 / "active_house_buildable_scenarios.csv"))}
    out = []
    for r in rows:
        L = r["listing"]

        def best(cands):
            c = [(x, n) for x, n in cands if x is not None]
            return max(c) if c else (None, None)
        s0 = best((r14.get((L, n)), n) for n in ("HOUSE", "DUPLEX", "MULTIPLEX")
                  if sc14.get((L, n), {}).get("eligible") == "True")
        ver = [n for n, sc in r["scen"].items() if sc["state"] == "VERIFIED"
               and sc.get("method") == "ANCHOR"]
        s1 = best((r14.get((L, n)), n) for n in ver)
        s2 = best((r["scen"][n].get("roi"), n) for n in ver)
        s3 = (r["roi_in"], r["best_in"])
        why = []
        if s0[1] and s0[1] not in ver:
            why.append(f"{s0[1]}:{r['scen'].get(s0[1], {}).get('state', '?')}")
        if s0[1] == "MULTIPLEX":
            why.append("MPX_UNITPX_NOT_RANKED")
        out.append({"listing": L, "roi_s0_v144": s0[0], "best_s0": s0[1],
                    "roi_s1_legal": s1[0], "roi_s2_sale": s2[0],
                    "best_s2": s2[1], "roi_s3_support": s3[0],
                    "best_s3": s3[1], "roi_full": r["roi"],
                    "best_full": r["best"], "best_full_oos": r["best_oos"],
                    "scope_change": ";".join(why),
                    "d_sale": round(s2[0] - s1[0], 1)
                    if s1[0] is not None and s2[0] is not None else ""})
    steps = {k: {t: sum(1 for x in out if x[k] is not None and x[k] >= t)
                 for t in (10, 20, 30)}
             for k in ("roi_s0_v144", "roi_s1_legal", "roi_s2_sale",
                       "roi_s3_support", "roi_full")}
    v.wcsv(OUT / "v15_roi_bridge.csv", out)
    return steps


def finish(rows, scen_out, price_fix, k_fit, band, lband, support=None):
    contra = detect(rows)
    seq = Counter()
    for c in contra:
        seq[c["code"]] += 1
        c["id"] = f"V15-{c['code']}-{seq[c['code']]:03d}"
    steps = bridge(rows)
    # C09: lineage — page community table must come from the same
    # dims-corrected pool as the per-listing land values (asserted in
    # the renderer + tests); recorded here as the declared source.
    flat = []
    for r in rows:
        for n in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            sc = r["scen"].get(n)
            if not sc:
                continue
            flat.append({
                "listing": r["listing"], "scenario": n, "state": sc["state"],
                "method": sc.get("method", ""), "asking": r["asking"],
                "net_saleable": rd(sc["net_saleable"]), "units": sc["units"],
                "psf_p50": rd(sc.get("psf")), "level": sc.get("level", ""),
                "n_comm": sc.get("n", ""), "n_near": sc.get("n_near", ""),
                "near_factor": rd(sc.get("near_factor"), 3),
                "sale_p10": rd(sc.get("p10")), "sale_p50": rd(sc.get("est")),
                "sale_p90": rd(sc.get("p90")),
                "roi_p50": rd(sc.get("roi"), 1),
                "roi_sale_m10": rd(sc.get("roi_m10"), 1),
                "roi_sale_m20": rd(sc.get("roi_m20"), 1),
                "unit_area": rd(sc.get("unit_area")),
                "out_of_support": bool(sc.get("out_of_support")),
                "roi_p10": rd(sc.get("roi_p10"), 1),
                "breakeven_bid_p10": rd(sc.get("breakeven_bid_p10")),
                "max_bid_roi20": rd(sc.get("mb20")),
                "v14_psf": rd(sc.get("v14_psf")),
                "v14_level": sc.get("v14_level", "")})
    v.wcsv(OUT / "v15_sale_and_roi.csv", flat)
    v.wcsv(OUT / "v15_legal_state.csv", scen_out)
    v.wcsv(OUT / "v15_land.csv", [{
        "listing": r["listing"], "comm": r["comm"], "asking": r["asking"],
        "bca_land": r["bca_land"], "bca_ratio": rd(r["bca_ratio"], 4),
        "bca_ratio_n": r["bca_ratio_n"], "bca_ratio_lvl": r["bca_ratio_lvl"],
        "fair_land": rd(r["fair_land"]), "land_src": r["land_src"],
        "fair_land_p10": rd(r["fair_land_p10"]),
        "fair_land_p90": rd(r["fair_land_p90"]),
        "disc_pct": rd(r["disc"], 1), "v14_comps_fair": rd(r["comps_fair"]),
        "v14_comps_disc_pct": rd(r["comps_disc"], 1),
        "v14_comps_level": r["comps_level"]} for r in rows])
    v.wcsv(OUT / "v15_contradictions.csv", contra,
           fields=["id", "code", "listing", "other", "severity", "detail", "status"])
    v.wcsv(OUT / "v15_price_refresh.csv", price_fix,
           fields=["listing", "snapshot_ask", "latest_asking"])
    roi = [r["roi"] for r in rows if r["roi"] is not None]
    roi10 = [r["roi_p10"] for r in rows if r["roi_p10"] is not None]
    summary = {
        "model_version": MODEL_VERSION, "n_listings": len(rows),
        "k_fit": {k: round(x, 4) for k, x in k_fit.items()},
        "sale_band": band, "land_band": lband,
        "legal_states": Counter(f"{s['scenario']}:{s['state']}"
                                for s in scen_out),
        "contradictions": Counter(c["code"] for c in contra),
        "roi_ge": {t: sum(x >= t for x in roi) for t in (10, 20, 30)},
        "roi_p10_ge": {t: sum(x >= t for x in roi10) for t in (0, 10, 20)},
        "roi_m10_ge0": sum(1 for r in rows if (r["roi_m10"] or -1) >= 0),
        "roi_m20_ge0": sum(1 for r in rows if (r["roi_m20"] or -1) >= 0),
        "roi_in_support_ge": {t: sum(1 for r in rows if r["roi_in"] is not None
                                     and r["roi_in"] >= t) for t in (10, 20, 30)},
        "best_is_out_of_support": sum(r["best_oos"] for r in rows),
        "roi_bridge": steps, "area_support_p95": support,
        "out_of_support": Counter(n for r in rows for n, sc in r["scen"].items()
                                  if sc.get("out_of_support")
                                  and sc["state"] == "VERIFIED"),
        "price_refreshed": len(price_fix),
        "watchlist": str(WATCHLIST)}
    (OUT / "v15_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False, default=dict))
    print(json.dumps(summary, ensure_ascii=False, default=dict)[:1500])
    return rows, contra, summary


if __name__ == "__main__":
    main()
