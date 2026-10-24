#!/usr/bin/env python3
"""V1.5.3 Economic Integrity Gate — Phase A (audit only, read-only).

Baseline: research/listing-valuation-v1-5-backtest @ 4600853 (V1.5.2).
Imports V1.4/V1.5 code; never writes to their outputs or parameters.

Outputs (repo-relative):
  data/audit/code_provenance.csv      parameter/function -> file:line
  data/audit/comps_trace.csv          every comp touching 880 / 5109 sale
  data/audit/cost_coverage.csv        cost line coverage, current vs audit
  data/audit/hbu_matrix.csv           legality states, 5 cases x 4 products
  data/audit/residual_land_value.csv  economic loop + sensitivities
  data/audit/cashflow_<case>_<scen>.csv monthly cash flow (audit scope)
  data/audit/economic_gate_results.json
Run: python3 scripts/economic_integrity_v1_5_3.py
"""
import csv
import glob
import inspect
import json
import math
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import listing_level_valuation_v1_4 as v   # noqa: E402
import valuation_v1_5_backtest as bt       # noqa: E402

OUT = ROOT / "data/audit"
OUT.mkdir(parents=True, exist_ok=True)
D15 = ROOT / "docs/audits/listing_valuation_v1_5"
RAW = ROOT / "data/research/raw/location"
BOUNDARY = Path("/Users/xiwei/northshore_property_watchdog/data/research/"
                "boundaries/vancouver-local-area-boundary-official.geojson")
BASELINE_SHA = "460085354039890c123ac9de728c497dd948e97d"
CORE = ("880 E 33RD AVENUE", "5109 SLOCAN STREET")
AUX = ("7455 WEST BOULEVARD", "3141 E BROADWAY", "3055 ROSEMONT DRIVE")
CASES = CORE + AUX
SIDE = {"东区": "Vancouver East", "西区": "Vancouver West"}


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def wcsv(path, rows, fields=None):
    fields = fields or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def rel(p):
    return str(Path(p).resolve().relative_to(ROOT))


# ================================================== 1. code provenance
def provenance():
    """Locate every parameter / function actually executed by V1.5.2.
    Line numbers are computed from the live source, not typed by hand."""
    rows = []
    files = {"v14": Path(v.__file__), "bt": Path(bt.__file__),
             "v15": ROOT / "scripts/listing_valuation_v1_5.py"}

    def const(key, name, note):
        src = files[key].read_text().splitlines()
        for i, line in enumerate(src, 1):
            if re.match(rf"^{name}\s*=", line):
                rows.append({"item": name, "kind": "CONST",
                             "file": rel(files[key]), "line": i,
                             "value": line.split("=", 1)[1].split("#")[0].strip(),
                             "source_note": note})
                return
        rows.append({"item": name, "kind": "CONST", "file": rel(files[key]),
                     "line": "NOT_FOUND", "value": "", "source_note": note})

    def func(obj, note):
        lines, start = inspect.getsourcelines(obj)
        rows.append({"item": obj.__name__, "kind": "FUNC",
                     "file": rel(inspect.getsourcefile(obj)),
                     "line": f"{start}-{start + len(lines) - 1}", "value": "",
                     "source_note": note})

    for name, note in (
            ("BUILD_COST", "V3s cost tier $/sf above-grade; no GC quote "
             "(docs/research/cost-audit-r7: UNVERIFIED)"),
            ("PERMIT_PSQFT", "'办证' $/sf on build_sqft; content undocumented"),
            ("SELLING_PCT", "commission+marketing, % of sale"),
            ("GST_PCT", "5% federal GST"),
            ("FIN_LAND_PCT_YR", "applied to 100% of offer, no LTV"),
            ("FIN_BUILD_PCT_YR", "0.35*3.5%+0.65*10% blended, on 100% hard"),
            ("BASE_YEARS", "unified 1-yr development window (V1.4.1)"),
            ("MPX_PRODUCT_FACTOR", "V3s stacked-product discount"),
            ("SIZE_K", "V1.4 size curve; V1.5 replaces with fit_k()"),
            ("HOOD_COMM_ALIAS", "listing hood -> comp community mapping"),
            ("LISTING_JSON", "frozen 2026-10-20 scenario geometry input"),
            ("HPI_ASOF", "price normalization month")):
        const("v14", name, note)
    const("bt", "ANCHOR_DEFAULT", "shrinkage K/K2, radius R km, cap CAP")
    const("v15", "MODEL_VERSION", "V1.5.2 pipeline")
    for f, note in ((v.cost_model, "ROI engine used by V1.5.2 (imported)"),
                    (v.bc_ptt, "BC PTT 1/2/3%; no +2% >$3M residential"),
                    (v.max_bid, "bisection on cost_model ROI"),
                    (v.build_newbuild_pools, "HOUSE pool filter: band 0-5, "
                     "non-strata, excl=='', fa & price present"),
                    (v.wmedian, "weighted percentile used by nearby factor"),
                    (bt.est_anchor, "V1.5 sale $/sf estimator"),
                    (bt.fit_k, "pooled within-community size elasticity"),
                    (bt.load_pools, "adds cluster/date/conflict to pools")):
        func(f, note)
    import listing_valuation_v1_5 as v15
    for f, note in ((v15.legal_state, "five-state legality"),
                    (v15.main, "ranking: VERIFIED + ANCHOR only; "
                     "out_of_support flagged, not excluded")):
        func(f, note)
    gen = Path("/Users/xiwei/northshore_property_watchdog/scripts/"
               "rebuild_value_analysis.py")
    if gen.exists():
        src = gen.read_text().splitlines()
        for pat, note in ((r"^def zoning_rules", "scenario FSR source "
                           "(UNCOMMITTED main-repo file)"),
                          (r"^MPX_MIN_AREA_SQFT", "R1-1 multiplex 306 m2"),
                          (r"^MPX_MIN_DEPTH_FT", "R1-1 multiplex depth 30.4 m"),
                          (r"^DUPLEX_MIN_AREA_SQFT", "duplex min site area")):
            for i, line in enumerate(src, 1):
                if re.match(pat, line):
                    rows.append({"item": pat.strip("^"), "kind": "GENERATOR",
                                 "file": str(gen), "line": i,
                                 "value": line.strip()[:80],
                                 "source_note": note + "; sha256 " +
                                 sha256(gen)[:16]})
    return rows


def sha256(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ================================================= 2. boundary check
def load_boundary():
    G = json.load(open(BOUNDARY))

    def inring(x, y, ring):
        c = False
        for i in range(len(ring)):
            x1, y1 = ring[i][:2]
            x2, y2 = ring[i - 1][:2]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                c = not c
        return c

    def area(lng, lat):
        for f in G["features"]:
            if inring(lng, lat, f["geometry"]["coordinates"][0]):
                return f["properties"]["name"]
        return None
    return area


# =================================================== 3. comps trace
LANEWAY = Path("/Users/xiwei/northshore_property_watchdog/data/research/"
               "normalized/laneway-flags-v2.json")


def load_context():
    pools, hr, arch = bt.load_pools()
    H = {r["tx"]: r for r in hr}
    PR = {r["tx"]: r for r in csv.DictReader(
        open(v.V12 / "attached_proxy_scenarios_per_row.csv"))}
    age = {r["canonical_tx"]: r for r in csv.DictReader(open(bt.AGE))}
    raw = {}
    for f in sorted(glob.glob(str(RAW / "*enriched*.json"))):
        for r in json.load(open(f))["sales"]:
            k = ((r.get("pid") or "").strip(), (r.get("sale_date") or "")[:10])
            raw.setdefault(k, dict(r, _file=Path(f).name))
    lw = json.load(open(LANEWAY)) if LANEWAY.exists() else {}
    listings = {r["address"]: r for r in json.load(open(v.LISTING_JSON))["rows"]}
    sale_pool = {n: [c for c in pools[n] if c["psf"] and 200 < c["psf"] < 6000
                     and c["lat"]] for n in ("HOUSE", "DUPLEX")}
    return {"pools": pools, "H": H, "PR": PR, "arch": arch, "age": age,
            "raw": raw, "lw": lw, "listings": listings,
            "sale_pool": sale_pool, "dims": v.load_lot_dims(),
            "k": {n: bt.fit_k(p) for n, p in sale_pool.items()},
            "area": load_boundary()}


def target_for(ctx, addr, name, community=None):
    r = ctx["listings"][addr]
    s = r["analysis"]["scenarios"][name]
    ns = num(s.get("net_saleable_area")) or num(s.get("build_sqft"))
    return {"lat": r["lat"], "lng": r["lng"],
            "community": community or v.HOOD_COMM_ALIAS.get(r["hood"], r["hood"]),
            "side": SIDE.get(r["side"], ""), "area": ns / (s.get("units") or 1),
            "net_saleable": ns, "units": s.get("units") or 1,
            "build_sqft": num(s.get("build_sqft"))}


def bsmt_state(raw):
    """UNKNOWN != NO: missing field is UNKNOWN; explicit 0 is NO."""
    if raw is None:
        return "NO_RAW_RECORD", ""
    b = num(raw.get("bca_basement_finish_area_sqft"))
    if b is None:
        return "UNKNOWN", ""
    return ("NO" if b == 0 else "YES"), b


def laneway_state(lw, raw):
    if raw is None or not lw:
        return "NOT_CHECKED", ""
    x = lw.get(raw.get("record_id"))
    if x is None:
        return "NOT_CHECKED", ""
    if isinstance(x, str):
        # "NONE" in the flag file = no permit matched within 40 m before
        # the sale; it is NOT evidence that no laneway house exists
        return "NO_PERMIT_MATCH" if x == "NONE" else x, ""
    return x.get("status", "?"), f"{x.get('permit', '')}@{x.get('d', '')}m"


def comps_trace(ctx):
    out = []
    P = bt.ANCHOR_DEFAULT
    for addr in CORE:
        for name in ("HOUSE", "DUPLEX"):
            pool = ctx["sale_pool"][name]
            t = target_for(ctx, addr, name)
            k = ctx["k"][name]
            psf0, det = bt.est_anchor(t, pool, k)
            # comm bases used by nearby comps (their own community level)
            near = {c["tx"]: v.hav_km(t["lat"], t["lng"], c["lat"], c["lng"])
                    for c in pool}
            near = {tx: d for tx, d in near.items() if d is not None
                    and d <= P["R"]}
            near_comms = {c["community"] for c in pool if c["tx"] in near}
            wsum = sum(1 / (1 + d / 0.25) for d in near.values()) or 1
            n_c = det.get("n_comm", 0)
            for c in pool:
                roles = []
                if c["community"] == t["community"]:
                    roles.append("COMM_BASE")
                if c["tx"] in near:
                    roles.append("NEARBY")
                if c["community"] in near_comms and c["community"] != t["community"]:
                    roles.append("NEARBY_REF_BASE")
                if c["side"] == t["side"]:
                    roles.append("SIDE_PRIOR")
                roles.append("K_FIT")
                # exact leave-one-out contribution (k refit too)
                tr = [x for x in pool if x["tx"] != c["tx"]]
                p_lo, _ = bt.est_anchor(t, tr, bt.fit_k(tr))
                out.append(trace_row(ctx, addr, name, t, c, roles, k, psf0,
                                     p_lo, near.get(c["tx"]), wsum, n_c, P))
            out.extend(excluded_rows(ctx, addr, name, t))
    return out


def trace_row(ctx, addr, name, t, c, roles, k, psf0, p_lo, d, wsum, n_c, P):
    a = ctx["arch"].get(c["tx"], {})
    key = ((a.get("pid") or "").strip(), (a.get("sale_date") or "")[:10])
    raw = ctx["raw"].get(key)
    src = ctx["H"].get(c["tx"]) if name == "HOUSE" else ctx["PR"].get(c["tx"])
    ag = ctx["age"].get(c["tx"], {})
    bs, bsf = bsmt_state(raw)
    lw, lwd = laneway_state(ctx["lw"], raw)
    dims = ctx["dims"].get(key)
    size_f = (t["area"] / c["fa"]) ** k if c["fa"] else 1.0
    in_target = c["community"] == t["community"]
    comp_off = ctx["area"](c["lng"], c["lat"]) if c["lng"] else None
    dup = sum(1 for x in ctx["sale_pool"][name] if x["cluster"] == c["cluster"])
    price_nom = num(src.get("price")) if src else None
    price_26 = num(src.get("price_2026_09") if name == "HOUSE"
                   else src.get("price_2026_09_neutral")) if src else None
    factor = num(src.get("factor_2026_09") if name == "HOUSE"
                 else src.get("factor_neutral")) if src else None
    return {
        "case": addr, "scenario": name, "tx": c["tx"],
        "address": a.get("address", ""), "unit": a.get("unit", ""),
        "sale_date": a.get("sale_date", ""), "sale_price_nominal": price_nom,
        "source_archive": a.get("sources", ""),
        "source_raw_file": raw.get("_file", "") if raw else "",
        "source_record_id": raw.get("record_id", "") if raw else "",
        "source_url": raw.get("source_url", "") if raw else "",
        "community_model": c["community"], "community_official": comp_off or "",
        "sale_kind": a.get("sale_kind", ""), "product": a.get("product", ""),
        "product_raw": a.get("product_raw", ""), "bca_desc": a.get("bca_desc", ""),
        "yb_archive": a.get("yb", ""), "yb_resolved": ag.get("resolved_yb", ""),
        "yb_conflict": ag.get("conflict", ""),
        "is_strata": a.get("is_strata", ""), "zoning": a.get("zoning", ""),
        "lot_archive_sqft": a.get("lot_sqft", ""),
        "lot_bca_dims_sqft": dims["lot"] if dims else "UNKNOWN",
        "above_grade_fa_sqft": round(c["fa"]) if c["fa"] else "UNKNOWN",
        "basement_finish_state": bs, "basement_finish_sqft": bsf,
        "beds": raw.get("beds", "") if raw else "",
        "baths": raw.get("baths", "") if raw else "",
        "laneway_state": lw, "laneway_evidence": lwd,
        "garage": "UNKNOWN", "renovation_finish": "UNKNOWN",
        "gst_basis": src.get("gst_basis", "UNKNOWN") if name == "HOUSE"
        else "UNKNOWN",
        "hpi_factor_2026_09": factor, "price_2026_09": price_26,
        "hpi_note": (src.get("factor_kind", "") + ";" +
                     (src.get("proxy_note", "") if name == "DUPLEX" else ""))
        if src else "",
        "psf_2026_09_above_grade": round(c["psf"], 1),
        "target_unit_area": round(t["area"]),
        "area_ratio_target_over_comp": round(t["area"] / c["fa"], 3)
        if c["fa"] else "",
        "size_adj_factor": round(size_f, 4),
        "psf_size_adjusted": round(c["psf"] * size_f, 1),
        "dist_km": round(d, 3) if d is not None else
        round(v.hav_km(t["lat"], t["lng"], c["lat"], c["lng"]), 3),
        "same_target_community": in_target,
        "parcel_cluster_records": dup,
        "roles": "|".join(roles),
        "status": "INCLUDED",
        "comm_base_share": round(n_c / (n_c + P["K"]), 3)
        if "COMM_BASE" in roles else "",
        "nearby_weight_norm": round((1 / (1 + d / 0.25)) / wsum, 4)
        if d is not None else "",
        "case_psf_estimate": round(psf0, 2),
        "loo_psf_without_comp": round(p_lo, 2) if p_lo else "",
        "loo_contribution_psf": round(psf0 - p_lo, 2) if p_lo else ""}


def excluded_rows(ctx, addr, name, t):
    """Same-community records dropped before pricing, with reason."""
    out = []
    kept = {c["tx"] for c in ctx["sale_pool"][name]}
    if name == "HOUSE":
        for r in ctx["H"].values():
            if r["community"] != t["community"] or r["tx"] in kept:
                continue
            if r["band"] not in ("0-1", "2-3", "4-5"):
                continue
            why = []
            if r["strata"] == "True":
                why.append("STRATA")
            if r["excl"]:
                why.append("EXCL:" + r["excl"])
            if not num(r["fa"]):
                why.append("FA_MISSING")
            if not num(r["price_2026_09"]):
                why.append("PRICE_2026_MISSING")
            if not why:
                psf = num(r["price_2026_09"]) / num(r["fa"])
                why.append("PSF_OUT_OF_RANGE" if not 200 < psf < 6000
                           else "NO_GEO")
            a = ctx["arch"].get(r["tx"], {})
            out.append({"case": addr, "scenario": name, "tx": r["tx"],
                        "address": a.get("address", ""),
                        "sale_date": r["date"], "community_model": r["community"],
                        "sale_price_nominal": num(r["price"]),
                        "status": "EXCLUDED", "roles": "",
                        "exclusion_reason": ";".join(why)})
    else:
        for r in ctx["PR"].values():
            if r["community"] != t["community"] or r["tx"] in kept:
                continue
            if r["product"] != "DUPLEX_HALF":
                continue
            why = []
            if not num(r["fa"]):
                why.append("FA_MISSING")
            if not num(r["price_2026_09_neutral"]):
                why.append("PRICE_2026_MISSING")
            if not why:
                why.append("PSF_OUT_OF_RANGE_OR_NO_GEO")
            out.append({"case": addr, "scenario": name, "tx": r["tx"],
                        "sale_date": r["date"], "community_model": r["community"],
                        "sale_price_nominal": num(r["price"]),
                        "status": "EXCLUDED", "roles": "",
                        "exclusion_reason": ";".join(why)})
    return out


# ============================================ 4. audit cost engine
# Every constant below is an AUDIT assumption with a named source; none
# is written back to V1.4/V1.5. Round-6 constants are read from the
# main-repo shadow script (sha recorded in provenance) and repeated here
# so this audit is self-contained.
R6 = Path("/Users/xiwei/northshore_property_watchdog/scripts/"
          "cost_audit_round6_20261020.py")
A = {
    "hard_psf": dict(v.BUILD_COST),           # V3s tiers, UNVERIFIED (no GC quote)
    "soft_psf": v.PERMIT_PSQFT,               # V1.4 '办证' $40/sf, reused as the
                                              # single soft line (no double count)
    "dcl_psf": 7.53, "acc_psf": 2.32,         # round-6 shadow script
    "metro_2027": {"lotdev": 28322, "town": 25625},
    "translink": {"sfd": 3416, "town": 2837},
    "demolition": 35000,
    "utility": {1: 40000, 2: 50000, 3: 60000, 4: 70000},
    "contingency_pct": 0.05,
    "land_ltv": 0.60, "land_rate": 0.06,
    "con_ltv": 0.65, "con_rate": 0.09, "loan_fee": 0.015,
    "prop_tax_pct_yr": 0.0045, "insurance_yr": 12000,
    "selling_pct": v.SELLING_PCT,
    "bsmt_psf": 200,                           # v2-4-1 doc range 150-250, no quote
    "months": 18, "permit_months": 6, "build_months": 10,
    "target_roi": 0.15,
}
# existing floor area for ACC s7.1(d) comparison: MLS interior area from
# the frozen listing card (NOT city-recognized GFA -> status UNKNOWN)
EXISTING_FA_CARD = {"880 E 33RD AVENUE": 1657, "5109 SLOCAN STREET": 1975,
                    "7455 WEST BOULEVARD": 2237, "3141 E BROADWAY": 1773,
                    "3055 ROSEMONT DRIVE": 2594}


def gov_fees(addr, gfa, units, existing_units=1):
    ex = EXISTING_FA_CARD.get(addr)
    acc = 0.0 if ex and gfa <= ex else gfa * A["acc_psf"]
    acc_st = ("EXEMPT_PENDING_CITY" if ex and gfa <= ex else
              "CHARGED_new>existing(card area,UNVERIFIED_GFA)")
    cat = "lotdev" if units <= 3 else "town"
    credit = min(existing_units, units)
    metro = (units - credit) * A["metro_2027"][cat]
    tl = units * A["translink"]["sfd" if units == 1 else "town"] \
        - credit * A["translink"]["sfd"]
    return {"dcl": gfa * A["dcl_psf"], "acc": acc, "acc_status": acc_st,
            "metro_dcc_net": metro, "translink_net": max(tl, 0.0)}


def audit_cost(addr, land, name, gfa, units, sale, bsmt_sf=0.0,
               months=None, hard_psf=None, rate_add=0.0, gst="INCL_NO_ITC",
               bsmt_psf=None):
    """Full development cost + monthly cash flow. ROI = profit / total
    cash cost (same definition as V1.4 cost_model). Returns dict."""
    months = months or A["months"]
    hp = hard_psf if hard_psf is not None else A["hard_psf"][name]
    bp = bsmt_psf if bsmt_psf is not None else A["bsmt_psf"]
    # fixed permit + build durations; extra months = post-completion
    # carry (sales period) on fully drawn loans
    pm = min(A["permit_months"], max(months - 2, 1))
    bm = max(min(A["build_months"], months - pm - 1), 1)
    hard = gfa * hp
    bsmt = bsmt_sf * bp
    cont = (hard + bsmt) * A["contingency_pct"]
    soft = gfa * A["soft_psf"]
    fees = gov_fees(addr, gfa, units)
    util = A["utility"].get(units, 70000)
    ptt = v.bc_ptt(land)
    selling = sale * A["selling_pct"]
    if gst == "CURRENT_5PCT":
        gst_net = sale * v.GST_PCT
    elif gst == "INCL_NO_ITC":
        gst_net = sale * v.GST_PCT / (1 + v.GST_PCT)
    elif gst == "INCL_WITH_ITC":
        gst_net = sale * v.GST_PCT / (1 + v.GST_PCT) - \
            (hard + bsmt + cont + soft + util) * v.GST_PCT / (1 + v.GST_PCT)
    else:   # EXCL: price GST-exclusive, buyer pays on top -> net 0
        gst_net = 0.0
    tax = land * A["prop_tax_pct_yr"] * months / 12
    ins = A["insurance_yr"] * months / 12
    build_total = hard + bsmt + cont
    # ---- monthly schedule (month 0 = land close)
    flows = [0.0] * (months + 1)
    flows[0] -= land + ptt
    for m in range(1, pm + 1):
        flows[m] -= soft / pm
    flows[pm] -= A["demolition"] + fees["dcl"] + fees["acc"] + \
        fees["metro_dcc_net"] + fees["translink_net"] + util
    for m in range(pm + 1, pm + bm + 1):
        flows[m] -= build_total / bm
    for m in range(1, months + 1):
        flows[m] -= (tax + ins) / months
    lr = (A["land_rate"] + rate_add) / 12
    cr = (A["con_rate"] + rate_add) / 12
    land_loan = land * A["land_ltv"]
    con_cap = build_total * A["con_ltv"]
    drawn, li, ci = 0.0, 0.0, 0.0
    flows[0] -= land_loan * A["loan_fee"]
    flows[pm] -= con_cap * A["loan_fee"]
    for m in range(1, months + 1):
        if pm < m <= pm + bm:
            drawn += con_cap / bm
        mi = land_loan * lr + drawn * cr
        li += land_loan * lr
        ci += drawn * cr
        flows[m] -= mi          # interest expensed monthly (not capitalised)
    interest = {"land_interest": li, "construction_interest": ci,
                "loan_fees": (land_loan + con_cap) * A["loan_fee"]}
    fin = sum(interest.values())
    items = {"land": land, "ptt": ptt, "demolition": A["demolition"],
             "hard_above_grade": hard, "basement_construction": bsmt,
             "contingency": cont, "soft_design_permit": soft,
             "dcl": fees["dcl"], "acc": fees["acc"],
             "metro_dcc_net": fees["metro_dcc_net"],
             "translink_net": fees["translink_net"], "utility_conn": util,
             "prop_tax_hold": tax, "insurance": ins, **interest,
             "selling": selling, "gst_net": gst_net}
    total = sum(items.values())
    profit = sale - total
    flows[months] += sale - selling - gst_net   # closing; project-level
                                               # (unlevered) cash, loans
                                               # principal not shown
    return {"items": items, "total": total, "profit": profit,
            "roi": profit / total, "fin": fin, "flows": flows,
            "acc_status": fees["acc_status"], "months": months}


def residual_land(fn, target, lo=0.0, hi=4e7):
    """Land price >= 0 at which fn(land)['roi'] == target (bisection).
    ROI is monotonic decreasing in land only for land >= 0 (total cost
    > 0); None = target unattainable even with free land."""
    if fn(lo)["roi"] < target:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if fn(mid)["roi"] < target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ===================================================== 5. HBU matrix
# Rule source available to this audit: the scenario generator in the
# main repo (UNCOMMITTED, sha in provenance) and docs/research/
# v2-4-3-product-decision-basis (cites R1-1 3.2.1.1 / 4.1.1 / 4.1.2).
# The official bylaw PDFs (bylaws.vancouver.ca) returned a Cloudflare
# challenge on 2026-10-24 and were NOT bypassed, so no state below can
# be LEGALLY_VERIFIED in this audit.
BYLAW_FETCH = ("bylaws.vancouver.ca R1-1/RM-7 PDF: Cloudflare challenge "
               "page (HTTP body 'Attention Required'), not bypassed")
MPX = {"area": 3294.0, "front": 32.8, "depth": 99.7,
       "min4_area": 4984.0, "min4_front": 43.6}


def hbu(ctx, addr):
    r = ctx["listings"][addr]
    z, lot = r.get("zoning") or "", num(r["lot_sqft"])
    fr, dp = num(r.get("frontage_ft")), num(r.get("depth_ft"))
    sc = r["analysis"]["scenarios"]
    out = []

    def row(prod, state, basis, fsr="", gfa="", units="", note=""):
        out.append({"case": addr, "zoning": z, "lot_sqft": lot,
                    "frontage_ft": fr, "depth_ft": dp, "product": prod,
                    "hbu_state": state, "fsr_used_by_model": fsr,
                    "gfa_used_by_model": gfa, "units": units,
                    "basis": basis, "fsr_achievability": note,
                    "v152_state": "", "bylaw_text_retrieved": "NO"})
    gate_note = ("max FSR used as saleable area; setbacks/height/"
                 "coverage/depth massing NOT checked -> UNKNOWN")
    for prod in ("HOUSE", "DUPLEX"):
        s = sc.get(prod, {})
        if not s.get("build_sqft"):
            row(prod, "UNKNOWN", "no generator rule for zone")
            continue
        var = bool(s.get("variance_required"))
        st = "CONDITIONAL_UNVERIFIED"
        basis = (f"generator {'RM-7 s3.11 -> R1-1 rules' if z == 'RM-7' else 'R1-1 3.2.x'}"
                 f"; verified={s.get('verified')} variance={var}; {BYLAW_FETCH}")
        if prod == "DUPLEX" and lot and lot < 3294 and not var:
            st, basis = "NOT_PERMITTED", "lot < 306 m2 duplex min (generator)"
        row(prod, st, basis, s.get("fsr"), num(s.get("build_sqft")),
            s.get("units"), gate_note + (
                f"; shallow lot depth {dp}ft" if dp and dp < 99.7 else ""))
    # TRIPLEX / MULTIPLEX
    if z.startswith("R1"):
        std = bool(lot and fr and dp and lot >= MPX["area"] and
                   fr >= MPX["front"] and dp >= MPX["depth"])
        min4 = bool(lot and fr and (lot > MPX["min4_area"] or
                                    fr > MPX["min4_front"]))
        if not std:
            why = [n for n, ok in (("area", lot and lot >= MPX["area"]),
                                   ("frontage", fr and fr >= MPX["front"]),
                                   ("depth", dp and dp >= MPX["depth"]))
                   if not ok]
            for p in ("TRIPLEX", "MULTIPLEX"):
                row(p, "NOT_PERMITTED", "R1-1 multiplex AND-gate fails: "
                    + ",".join(why) + " (generator thresholds); relaxation "
                    "path not modelled -> outright route only", "", "", "")
        else:
            row("TRIPLEX", "NOT_PERMITTED" if min4 else
                "CONDITIONAL_UNVERIFIED",
                "R1-1 min-4-unit clause (>4,984sf or >43.6ft)" if min4
                else "R1-1 3-unit tier; " + BYLAW_FETCH, 1.0, lot, 3,
                "" if min4 else gate_note)
            s = sc.get("MULTIPLEX", {})
            row("MULTIPLEX", "CONDITIONAL_UNVERIFIED",
                f"R1-1 multiplex tier, model units={s.get('units')}; "
                + BYLAW_FETCH, s.get("fsr"), num(s.get("build_sqft")),
                s.get("units"), gate_note + "; price evidence = untested "
                "unit-price proxy")
    elif z == "RM-7":
        ok = bool(lot and lot >= 3258.5)
        row("TRIPLEX", "CONDITIONAL_UNVERIFIED" if ok else "NOT_PERMITTED",
            "RM-7 s3.4 conditional triplex 0.60 base FSR, discretionary to "
            "0.90 (generator comment); conditional use = not outright; "
            + BYLAW_FETCH, 0.60, num(sc.get("MULTIPLEX", {}).get("build_sqft")),
            3, gate_note)
        row("MULTIPLEX", "UNKNOWN", "RM-7 4+ unit form not in generator",
            "", "", "")
    else:
        for p in ("TRIPLEX", "MULTIPLEX"):
            row(p, "UNKNOWN", "zone not modelled")
    v15s = {(x["listing"], x["scenario"]): x["state"] for x in csv.DictReader(
        open(D15 / "v15_legal_state.csv"))}
    for o in out:
        o["v152_state"] = v15s.get((addr, "MULTIPLEX" if o["product"] ==
                                    "TRIPLEX" else o["product"]), "")
    return out


# ================================================= 6. cost coverage
def cost_coverage():
    """Line-by-line: what V1.4/V1.5.2 cost_model includes vs this audit."""
    L = []

    def add(item, cur, cur_basis, aud, aud_basis, scope, note=""):
        L.append({"cost_item": item, "v152_status": cur,
                  "v152_formula": cur_basis, "audit_status": aud,
                  "audit_formula": aud_basis, "physical_scope": scope,
                  "note": note})
    E, I, U, N = ("EXPLICIT", "INCLUDED_DOCUMENTED", "UNKNOWN",
                  "NOT_APPLICABLE")
    add("land", E, "offer = Ask", E, "Ask (latest_asking)", "site")
    add("PTT", E, "bc_ptt 1/2/3% tiers", E, "same; +2% >$3M residential "
        "tier absent in bc_ptt -> N/A for these cases (land <$3M)", "site")
    add("demolition/abatement", U, "not in cost_model", E,
        "$35,000 (round-6 shadow; RFQ asks to include abatement)",
        "existing house", "abatement for 1940s-60s stock UNKNOWN")
    add("hard above-grade", E, "build_sqft x $350/400/450", E,
        "same tiers on above-grade GFA", "above-grade FSR area",
        "tiers UNVERIFIED: no GC quote (cost-audit-r7)")
    add("basement construction", U, "not costed; comps priced incl. "
        "basement", E, "basement sf x $200 (doc range 150-250, no quote)",
        "below-grade finished", "only in scope-aligned variants")
    add("contingency", U, "none", E, "5% of hard+basement", "construction")
    add("soft (design/engineering/permit)", E, "build_sqft x $40 ('办证')",
        E, "same $40/sf, counted once", "project", "content of $40 "
        "undocumented; audit does not add a second soft line")
    add("City DCL", U, "not separate; may or may not sit inside $40",
        E, "$7.53/sf x GFA, full (DCL bulletin 6.2 per cost-audit-r7)",
        "GFA", "if $40 already contains DCL, audit double counts "
        "<= $7.53/sf (disclosed)")
    add("ACC", U, "none", E, "$2.32/sf x GFA unless new GFA <= existing "
        "(s7.1(d)); existing from MLS card area -> city GFA UNKNOWN", "GFA")
    add("Metro Van DCC", U, "none", E, "(units-1) x 2027 rate "
        "(net-new-unit credit)", "units")
    add("TransLink DCC", U, "none", E, "units x rate - 1 SFD credit; "
        "credit rule UNKNOWN (cost-audit-r7)", "units")
    add("utility connections", U, "none", E, "$40k/50k/60k/70k by units",
        "site")
    add("property tax during hold", U, "none", E, "0.45%/yr x land x months",
        "site")
    add("builder's risk insurance", U, "none", E, "$12k/yr x months",
        "project")
    add("land interest", E, "offer x 5% x 1y (100% debt)", E,
        "60% LTV x 6%/yr monthly over full period", "site")
    add("construction interest", E, "hard x 7.725% x 1y on full hard",
        E, "65% LTV, linear draws x 9%/yr", "construction")
    add("loan fees", U, "none", E, "1.5% of committed loans", "finance")
    add("equity opportunity cost", N, "excluded (project cash ROI)", N,
        "excluded (cost-audit-r7 D)", "finance")
    add("selling", E, "4% of sale", E, "4% of sale", "sale")
    add("GST on sale", E, "5% x sale as a cost (labelled 'EXCL' but "
        "economically = price includes GST, overstated 0.24% of sale)", E,
        "INCL_NO_ITC: sale x 5/105 (comps mostly GST-free resales)",
        "sale", "GST basis of comps UNKNOWN_LIKELY_* only")
    add("GST input tax credits", U, "none", U, "whether $350 tiers are "
        "GST-inclusive is undocumented -> ITC not credited (conservative)",
        "construction")
    add("new housing rebate / FTHB GST relief", N, "none", U,
        "buyer-side; effect on achievable price not modelled", "sale")
    add("BC home flipping tax", U, "none", U, "sale <730 days; builder "
        "exemption exists (gov.bc.ca page) but conditions not retrieved",
        "profit", "exposure if NOT exempt shown as sensitivity only")
    add("speculation & vacancy / empty homes tax", U, "none", U,
        "redevelopment exemptions exist; applicability not verified",
        "site")
    add("BC Housing new home registration + 2-5-10 warranty", U, "none",
        U, "not quantified; plausibly inside $40 soft", "project")
    add("landscaping/site works", U, "unclear if in $350", U,
        "assumed inside hard tier (RFQ item 7 open)", "site")
    add("laneway house", N, "not built", N, "not in any scenario", "-")
    add("income tax on profit", N, "pre-tax ROI", N, "pre-tax ROI", "-")
    add("post-completion carry / sales period", U, "sold at completion",
        U, "months sensitivity 18/24/30", "time")
    return L


# ================================================= 7. economic loop
def comps_geometry(ctx):
    """Physical product actually sold in the HOUSE comp pool: above-grade
    / lot (BCA dims lot only) and finished basement / above-grade (only
    where the BCA basement field is present — UNKNOWN rows excluded)."""
    ag, br, n_unk = [], [], 0
    for c in ctx["sale_pool"]["HOUSE"]:
        a = ctx["arch"][c["tx"]]
        key = (a["pid"].strip(), a["sale_date"][:10])
        raw, dims = ctx["raw"].get(key), ctx["dims"].get(key)
        st, b = bsmt_state(raw)
        if st == "YES":
            br.append(b / c["fa"])
        elif st != "NO":
            n_unk += 1
        if dims and dims["lot"]:
            ag.append(c["fa"] / dims["lot"])
    med = statistics.median
    return {"above_over_lot_p50": med(ag), "above_over_lot_max": max(ag),
            "n_lot_dims": len(ag), "bsmt_over_above_p50": med(br),
            "n_bsmt_yes": len(br), "n_bsmt_unknown_or_noraw": n_unk,
            "n_bsmt_no": 0}


def variants(ctx, addr, name, geo, land15):
    """Scenario variants for one case x product. Sale is always the
    V1.5.2 ANCHOR estimator (no price tuning); only the physical scope
    of what is sold/costed changes."""
    t = target_for(ctx, addr, name)
    lot = num(ctx["listings"][addr]["lot_sqft"])
    pool, k = ctx["sale_pool"][name], ctx["k"][name]

    def price(area_unit):
        p, det = bt.est_anchor(dict(t, area=area_unit), pool, k)
        return p, det
    out = []
    p, det = price(t["area"])
    gfa = t["build_sqft"]
    out.append({"variant": "V152_MODEL_GEOMETRY", "above": gfa, "bsmt": 0.0,
                "sale": p * t["net_saleable"], "psf": p, "det": det,
                "scope": "sale: comp $/sf (comps INCLUDE basement) x model "
                "above-grade GFA; cost: above-grade only -> COST_AREA_MISMATCH",
                "legal": "model FSR as saleable; massing UNKNOWN"})
    if name == "HOUSE" and lot:
        r = geo["bsmt_over_above_p50"]
        a1 = lot * geo["above_over_lot_p50"]
        p1, d1 = price(a1)
        out.append({"variant": "ALIGNED_COMPS_GEOMETRY", "above": a1,
                    "bsmt": a1 * r, "sale": p1 * a1, "psf": p1, "det": d1,
                    "scope": f"build what comps sold: above={geo['above_over_lot_p50']:.3f}"
                    f"xlot + finished bsmt={r:.3f}xabove; both costed",
                    "legal": "total ~%.2f FSR > 0.60 -> requires basement/"
                    "cellar exclusion: CONDITIONAL_UNVERIFIED"
                    % (geo["above_over_lot_p50"] * (1 + r))})
        a2 = gfa / (1 + r)
        p2, d2 = price(a2)
        out.append({"variant": "ALIGNED_FSR060_INCL_BSMT", "above": a2,
                    "bsmt": gfa - a2, "sale": p2 * a2, "psf": p2, "det": d2,
                    "scope": "0.60 FSR total split above/bsmt at comps ratio "
                    "(basement counted in FSR per v2-4-3 doc citing R1-1 "
                    "4.1.1/4.1.2); sale on above-grade like comps",
                    "legal": "within 0.60 if basement counts: "
                    "CONDITIONAL_UNVERIFIED (bylaw text not retrieved)"})
    return out


def economics(ctx, geo):
    v15 = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D15 / "v15_sale_and_roi.csv"))}
    land = {r["listing"]: r for r in csv.DictReader(open(D15 / "v15_land.csv"))}
    rows, flows, repro = [], {}, []
    T = A["target_roi"]
    for addr in CASES:
        r = ctx["listings"][addr]
        ask = float(land[addr]["asking"])
        mkt = float(land[addr]["fair_land"])
        mkt_comps = num(land[addr]["v14_comps_fair"])
        for name in ("HOUSE", "DUPLEX"):
            s15 = v15.get((addr, name))
            if not s15 or s15["method"] != "ANCHOR" or not s15["sale_p50"]:
                continue
            t = target_for(ctx, addr, name)
            units = t["units"]
            # ---- reproduce V1.5.2 exactly with its own engine
            sc = {"name": name, "build_sqft": t["build_sqft"]}
            est = float(s15["sale_p50"])
            cur = v.cost_model(ask, sc, est, v.BASE_YEARS, "EXCL")
            repro.append({"case": addr, "scenario": name,
                          "roi_v152_csv": float(s15["roi_p50"]),
                          "roi_recomputed": round(100 * cur["roi"], 1)})
            mb_cur = v.max_bid(sc, est, T, v.BASE_YEARS)
            for var in variants(ctx, addr, name, geo, None):
                def eng(L, **kw):
                    return audit_cost(addr, L, name, var["above"], units,
                                      var["sale"], var["bsmt"], **kw)
                base = eng(ask)
                rl = residual_land(eng, T)
                rl0 = residual_land(eng, 0.0)
                rl10 = residual_land(eng, 0.10)
                sens = {}
                for lab, kw, sale_f in (
                        ("sale_m10", {}, 0.90), ("sale_m20", {}, 0.80),
                        ("sale_p10", {}, 1.10),
                        ("hard_p50", {"hard_psf": A["hard_psf"][name] + 50}, 1),
                        ("hard_p100", {"hard_psf": A["hard_psf"][name] + 100}, 1),
                        ("months_24", {"months": 24}, 1),
                        ("months_30", {"months": 30}, 1),
                        ("rate_p200bp", {"rate_add": 0.02}, 1),
                        ("gst_excl", {"gst": "EXCL"}, 1),
                        ("gst_incl_itc", {"gst": "INCL_WITH_ITC"}, 1),
                        ("bsmt_250", {"bsmt_psf": 250}, 1),
                        ("combo_m10_hard100_m24", {"hard_psf": A["hard_psf"][name] + 100,
                                                   "months": 24}, 0.90)):
                    x = audit_cost(addr, ask, name, var["above"], units,
                                   var["sale"] * sale_f, var["bsmt"], **kw)
                    sens[lab] = round(100 * x["roi"], 1)
                gap = mkt - rl if rl is not None else None
                row = {
                    "case": addr, "scenario": name, "variant": var["variant"],
                    "asking": ask, "market_land_bca_cal": round(mkt),
                    "market_land_comps_v14": round(mkt_comps) if mkt_comps else "",
                    "market_land_valuation_basis": "BCA 2026 roll (val. date "
                    "2025-07-01) x community sale/BCA-land median, sales "
                    "HPI-normalized to 2026-09",
                    "above_grade_sf": round(var["above"]),
                    "basement_sf": round(var["bsmt"]), "units": units,
                    "sale_psf_above": round(var["psf"], 1),
                    "sale_estimate": round(var["sale"]),
                    "sale_basis": f"{var['det']['level']} n_comm="
                    f"{var['det'].get('n_comm')} n_near={var['det'].get('n_near')}"
                    f" near_f={var['det'].get('near_factor', 1):.3f}",
                    "scope_note": var["scope"], "legal_note": var["legal"],
                    "roi_v152_model": float(s15["roi_p50"]),
                    "residual_land_v152_model_at_15": round(mb_cur)
                    if isinstance(mb_cur, float) else str(mb_cur),
                    "total_cost_audit": round(base["total"]),
                    "financing_audit": round(base["fin"]),
                    "roi_audit": round(100 * base["roi"], 1),
                    "residual_land_audit_at_15": round(rl) if rl is not None
                    else "UNATTAINABLE_AT_ZERO_LAND",
                    "residual_land_audit_at_10": round(rl10) if rl10 is not None
                    else "UNATTAINABLE_AT_ZERO_LAND",
                    "residual_land_audit_at_0": round(rl0) if rl0 is not None
                    else "UNATTAINABLE_AT_ZERO_LAND",
                    "market_minus_residual": round(gap) if gap is not None else "",
                    "ask_minus_residual": round(ask - rl) if rl is not None else "",
                    "acc_status": base["acc_status"],
                    **{f"roi_{k2}": v2 for k2, v2 in sens.items()}}
                for k2, v2 in base["items"].items():
                    row[f"cost_{k2}"] = round(v2)
                rows.append(row)
                if addr in CORE:
                    flows[f"{addr}|{name}|{var['variant']}"] = base
    return rows, flows, repro


def attribution(rows):
    """Sequential decomposition of (Ask - residual land @15%) per case x
    product: model residual -> audit cost coverage (same geometry) ->
    scope alignment. Each step's delta + evidence strength."""
    by = defaultdict(dict)
    for r in rows:
        by[(r["case"], r["scenario"])][r["variant"]] = r
    out = []
    for (case, scen), vs in by.items():
        m = vs["V152_MODEL_GEOMETRY"]
        r0 = num(m["residual_land_v152_model_at_15"])
        r1 = num(m["residual_land_audit_at_15"])
        steps = [("S0_V152_MODEL", r0, "", "MODEL_ONLY")]
        steps.append(("S1_FULL_COST_SAME_GEOMETRY", r1,
                      "adds demolition, contingency, DCL/ACC/DCC, utilities,"
                      " tax, insurance, loan fees; LTV financing over 18 mo",
                      "MEDIUM: rates from round-6 shadow script; hard tiers "
                      "UNVERIFIED"))
        for k in ("ALIGNED_COMPS_GEOMETRY", "ALIGNED_FSR060_INCL_BSMT"):
            if k in vs:
                steps.append((f"S2_{k}", num(vs[k]["residual_land_audit_at_15"]),
                              vs[k]["scope_note"],
                              "MEDIUM: comp geometry from BCA fields; "
                              "basement $/sf no quote; legality UNVERIFIED"))
        prev = None
        for lab, val, why, ev in steps:
            out.append({"case": case, "scenario": scen, "step": lab,
                        "residual_land_at_15": round(val) if val is not None else "",
                        "delta_vs_prev": round(val - prev)
                        if val is not None and prev is not None else "",
                        "asking": m["asking"],
                        "market_land": m["market_land_bca_cal"],
                        "driver": why, "evidence_strength": ev})
            if val is not None and lab in ("S0_V152_MODEL",
                                           "S1_FULL_COST_SAME_GEOMETRY"):
                prev = val
    return out


def gate(rows, hbu_rows):
    """Economic Gate per case x product (audit classification only)."""
    leg = {(h["case"], h["product"]): h["hbu_state"] for h in hbu_rows}
    by = defaultdict(list)
    for r in rows:
        by[(r["case"], r["scenario"])].append(r)
    res = {}
    for (case, scen), vs in by.items():
        aligned = [r for r in vs if r["variant"].startswith("ALIGNED")]
        ref = aligned or vs
        vals = [num(r["residual_land_audit_at_15"]) for r in ref]
        best = max((x for x in vals if x is not None), default=0.0)
        ask = vs[0]["asking"]
        lg = leg.get((case, scen), "UNKNOWN")
        if lg == "NOT_PERMITTED":
            st = "FAIL_NOT_PERMITTED"
        elif best < ask:
            st = "FAIL_RESIDUAL_BELOW_ASK"
        elif lg != "LEGALLY_VERIFIED":
            st = "CONDITIONAL_LEGAL_UNVERIFIED"
        else:
            st = "PASS"
        res[f"{case}|{scen}"] = {
            "status": st, "legal_state": lg, "asking": ask,
            "best_residual_land_at_15": round(best) if any(
                x is not None for x in vals) else "UNATTAINABLE_AT_ZERO_LAND",
            "residual_minus_ask": round(best - ask),
            "variants_considered": [r["variant"] for r in ref],
            "scope_aligned_available": bool(aligned),
            "roi_v152_model": vs[0]["roi_v152_model"],
            "roi_audit_by_variant": {r["variant"]: r["roi_audit"] for r in vs}}
    return res


# ============================================================ main
def main():
    ctx = load_context()
    prov = provenance()
    wcsv(OUT / "code_provenance.csv", prov)
    tr = comps_trace(ctx)
    fields = list(dict.fromkeys(k for r in tr for k in r))
    wcsv(OUT / "comps_trace.csv", tr, fields)
    wcsv(OUT / "cost_coverage.csv", cost_coverage())
    hb = [h for a in CASES for h in hbu(ctx, a)]
    wcsv(OUT / "hbu_matrix.csv", hb)
    geo = comps_geometry(ctx)
    rows, flows, repro = economics(ctx, geo)
    wcsv(OUT / "residual_land_value.csv", rows,
         list(dict.fromkeys(k for r in rows for k in r)))
    att = attribution(rows)
    wcsv(OUT / "residual_gap_attribution.csv", att)
    for key, f in flows.items():
        case, scen, var = key.split("|")
        name = re.sub(r"[^A-Za-z0-9]+", "_", f"{case}_{scen}_{var}").strip("_")
        cum = 0.0
        wcsv(OUT / f"cashflow_{name}.csv",
             [{"month": m, "cash_out": round(-x), "cumulative": round(
                 (cum := cum - x))} for m, x in enumerate(f["flows"])])
    g = gate(rows, hb)
    inc = [r for r in tr if r["status"] == "INCLUDED"]
    alias = {a: {"model_community": v.HOOD_COMM_ALIAS.get(
        ctx["listings"][a]["hood"], ctx["listings"][a]["hood"]),
        "official_local_area": ctx["area"](ctx["listings"][a]["lng"],
                                           ctx["listings"][a]["lat"])}
        for a in CASES}
    res = {
        "phase": "V1.5.3 Economic Integrity Gate / Phase A (audit only)",
        "baseline_commit": BASELINE_SHA, "target_roi_for_residual": A["target_roi"],
        "audit_assumptions": A, "existing_fa_card": EXISTING_FA_CARD,
        "v152_reproduction": repro,
        "v152_reproduced": all(abs(r["roi_v152_csv"] - r["roi_recomputed"])
                               < 0.11 for r in repro),
        "comp_geometry_house_pool": geo,
        "community_alias_check": alias,
        "comps_trace_counts": {
            f"{a}|{n}": {"included": sum(1 for r in inc if r["case"] == a
                                         and r["scenario"] == n),
                         "excluded": sum(1 for r in tr if r["case"] == a and
                                         r["scenario"] == n and
                                         r["status"] == "EXCLUDED")}
            for a in CORE for n in ("HOUSE", "DUPLEX")},
        "gate": g,
        "bylaw_fetch": BYLAW_FETCH}
    (OUT / "economic_gate_results.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False, default=str))
    for k, x in g.items():
        print(k, x["status"], x["roi_v152_model"], x["roi_audit_by_variant"],
              x["best_residual_land_at_15"], x["asking"])
    print("repro", res["v152_reproduced"], "geo", geo)
    print("alias", alias)


if __name__ == "__main__":
    main()
