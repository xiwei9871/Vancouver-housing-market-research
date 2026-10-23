#!/usr/bin/env python3
"""Listing-Level Valuation V1.4 — per-listing land value, rebuild sale
estimate, and buy-side ROI decisions for every ACTIVE HOUSE.

Layers
  A  land-dominant old-house comp pool  (age>=50 & BCA land share>=0.95)
  B  per-listing buildable scenarios    (V3s verified geometry reused)
  C  per-listing new-build sale estimate (matched 2026-09-normalized comps)
  D  ROI at actual Ask, unified 1-yr development financing window
     (0% future market) + market stress + max-bid solve + negotiation
  E  two independent rankings (LAND BUY VALUE / REBUILD ROI)

Data: all local frozen inputs — see input_inventory.md.
V3s cost constants are replicated verbatim (asserted by tests) from
scripts/rebuild_value_analysis.py; production V3s is untouched.

Outputs -> docs/audits/listing_level_valuation_v1_4/
Run:  python3 scripts/listing_level_valuation_v1_4.py
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs/audits/listing_level_valuation_v1_4"
DOCS.mkdir(parents=True, exist_ok=True)
NORM = ROOT / "data/research/normalized"
V12 = NORM / "house_hpi_normalization_v1_2"
LISTING_JSON = ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"

HPI_ASOF = "2026-09"
LISTING_SNAPSHOT = "2026-10-20"
MODEL_VERSION = "listing_level_valuation_v1_4"
COST_VERSION = "v3s-2026-10(ptt+fin-1yr-dev-window)"

# ------------------------------------------------ V3s cost model (frozen)
BUILD_COST = {"HOUSE": 350, "DUPLEX": 400, "MULTIPLEX": 450}
PERMIT_PSQFT = 40
SELLING_PCT = 0.04
GST_PCT = 0.05
FIN_LAND_PCT_YR = 0.05            # land mortgage ~5%/yr
FIN_BUILD_PCT_YR = 0.35 * 0.035 + 0.65 * 0.10   # 0.07725 blended/yr
BASE_YEARS = 1                    # unified 1-yr development financing
                                  # window (V1.4.1 correction: sale
                                  # timing is separated from the dev
                                  # ROI — no 3y/5y ROI variants)
MPX_PRODUCT_FACTOR = 0.85         # V3s stacked-product discount

LOT_BANDS = ((2500, 3000), (3000, 4000), (4000, 5000), (5000, 6000),
             (6000, math.inf))
MARKET_SCEN = (-0.20, -0.10, 0.0, 0.10, 0.20)
ROI_TARGETS = (0.10, 0.20, 0.30)
OFFER_CUTS = (0.95, 0.90, 0.85)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def wmedian(pairs, p):
    """Weighted percentile: pairs = [(value, weight)]."""
    pts = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pts:
        return None
    tot = sum(w for _, w in pts)
    acc = 0.0
    for v, w in pts:
        acc += w
        if acc / tot >= p / 100:
            return v
    return pts[-1][0]


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100
    f = int(k)
    return vals[f] if f + 1 >= len(vals) \
        else vals[f] + (vals[f + 1] - vals[f]) * (k - f)


def hav_km(a_lat, a_lng, b_lat, b_lng):
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def lot_band(lot):
    for i, (lo, hi) in enumerate(LOT_BANDS):
        if lo <= lot < hi:
            return i
    return None


def band_label(i):
    if i is None:
        return "?"
    lo, hi = LOT_BANDS[i]
    return f"{int(lo)}-{'6000+' if math.isinf(hi) else int(hi)}"


def wcsv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ================================================================ loaders
def load_archive():
    return {r["canonical_tx"]: r for r in csv.DictReader(
        open(NORM / "sales_archive_v1_0_3/sales_archive.csv"))}


def load_resolved_yb():
    return {r["canonical_tx"]: r.get("resolved_yb") for r in
            csv.DictReader(open(
                NORM / "housing_age_recovery_v2_4"
                / "age_recovery_per_row.csv"))}


def load_tax():
    """pid -> {roll_year: (land, impr)}"""
    tax = defaultdict(dict)
    for yr in (2023, 2024, 2025, 2026):
        for t in csv.DictReader(
                open(ROOT / f"data/research/raw/official/"
                          f"property-tax-{yr}-full.csv",
                     encoding="utf-8-sig"), delimiter=";"):
            pid = (t.get("pid") or "").strip()
            if pid:
                tax[pid][yr] = (num(t.get("current_land_value")),
                                num(t.get("current_improvement_value")))
    return tax


def tax_share(tax_pid, sale_year):
    """land_share + roll year actually used. Try sale_year first, then
    adjacent rolls; None when unverifiable."""
    for yr in (sale_year, sale_year + 1, sale_year - 1):
        rec = tax_pid.get(yr)
        if rec and rec[0] and rec[1] is not None and rec[0] + rec[1] > 0:
            return rec[0] / (rec[0] + rec[1]), yr
    return None, None


# ========================================================= Module A pools
def build_land_pool(house_rows, arch, yb_map, tax):
    pool, aux, drops = [], [], {"no_yb": 0, "no_tax": 0, "strata": 0,
                              "no_lot": 0, "age_lt50": 0}
    for r in house_rows:
        yb = num(yb_map.get(r["tx"]))
        if not yb:
            drops["no_yb"] += 1
            continue
        age = int(r["year"]) - int(yb)
        if age < 50:
            drops["age_lt50"] += 1
            continue
        if r["strata"] == "True":
            drops["strata"] += 1
            continue
        lot = num(r["lot"])
        if not lot or lot < 1500:
            drops["no_lot"] += 1
            continue
        a = arch.get(r["tx"], {})
        share, asmt = tax_share(tax.get((a.get("pid") or "").strip(), {}),
                                int(r["year"]))
        if share is None:
            drops["no_tax"] += 1
            continue
        rec = {"tx": r["tx"], "community": r["community"], "side": r["side"],
               "date": r["date"], "year": int(r["year"]),
               "yb_tier": r["tier"],
               "lot": lot, "lat": num(a.get("lat")), "lng": num(a.get("lng")),
               "zoning": r["zoning"], "age_at_sale": age,
               "land_share": round(share, 4), "asmt_year_used": asmt,
               "price_2026_09": num(r["price_2026_09"]),
               "land_psf": num(r["price_2026_09"]) / lot
               if num(r["price_2026_09"]) else None}
        (pool if share >= 0.95 else aux if share >= 0.90 else []).append(rec)
    return pool, aux, drops


def build_newbuild_pools(house_rows, proxy_rows, arch):
    def geo(r):
        a = arch.get(r["tx"], {})
        return num(a.get("lat")), num(a.get("lng"))

    hb = []
    for r in house_rows:
        if (r["band"] in ("0-1", "2-3", "4-5") and r["strata"] != "True"
                and r["excl"] == "" and num(r["fa"]) and num(r["price_2026_09"])):
            la, ln = geo(r)
            hb.append({"tx": r["tx"], "community": r["community"],
                       "side": r["side"], "lat": la, "lng": ln,
                       "band": r["band"], "fa": num(r["fa"]),
                       "psf": num(r["price_2026_09"]) / num(r["fa"]),
                       "factor_kind": "OFFICIAL"})
    dup, mpx = [], []
    for r in proxy_rows:
        fa = num(r["fa"])
        px = num(r["price_2026_09_neutral"])
        if not fa or not px:
            continue
        la, ln = geo(r)
        rec = {"tx": r["tx"], "community": r["community"],
               "side": r["side"], "lat": la, "lng": ln, "fa": fa,
               "psf": px / fa, "factor_kind": "PROXY",
               "proxy_note": r.get("proxy_note", "")}
        (dup if r["product"] == "DUPLEX_HALF" else mpx
         if r["product"] == "MULTIPLEX_UNIT" else []).append(rec)
    for r in mpx:
        r["psf"] *= MPX_PRODUCT_FACTOR   # V3s stacked-product discount
    return {"HOUSE": hb, "DUPLEX": dup, "MULTIPLEX": mpx}


# ====================================================== matching engines
def match_land(t, pool, comm_psf):
    """Waterfall; returns dict with level, comps, weighted psf stats."""
    tband = lot_band(t["lot_sqft"])
    cand_levels = []

    def same_comm(r):
        return r["community"] == t["hood"]

    L = [r for r in pool if same_comm(r) and lot_band(r["lot"]) == tband
         and (d := hav_km(t["lat"], t["lng"], r["lat"], r["lng"])) is not None
         and d <= 0.5]
    cand_levels.append(("MICRO_500M", L, 1.0))
    L = [r for r in pool if same_comm(r) and lot_band(r["lot"]) == tband]
    cand_levels.append(("COMM_LOTBAND", L, 1.0))
    adj = {tband - 1, tband + 1} if tband is not None else set()
    L = [r for r in pool if same_comm(r) and lot_band(r["lot"]) in adj]
    cand_levels.append(("COMM_ADJ_BAND", L, 1.0))
    L = [r for r in pool if same_comm(r)]
    cand_levels.append(("COMM_ANY", L, 1.0))
    # nearby communities within 1.5km, adjusted by community-p50 ratio
    near = []
    for r in pool:
        d = hav_km(t["lat"], t["lng"], r["lat"], r["lng"])
        if d is not None and d <= 1.5 and not same_comm(r):
            cf = None
            tc, cc = comm_psf.get(r["community"]), comm_psf.get(t["hood"])
            if tc and cc:
                cf = tc / cc
            near.append((r, cf))
    if near:
        L = [dict(r, psf_adj=r["land_psf"] * cf) if cf else r
             for r, cf in near if r["land_psf"]]
        for r in L:
            r.setdefault("psf_adj", r["land_psf"])
        cand_levels.append(("NEARBY_COMM_1.5KM_ADJ", L, 1.0))
    L = [r for r in pool if r["side"] == t["side_key"]]
    cand_levels.append(("SIDE_FALLBACK", L, 0.0))

    for level, cands, _ in cand_levels:
        cands = [c for c in cands if c.get("psf_adj", c["land_psf"])]
        if len(cands) >= 3:
            pairs = []
            for c in cands:
                d = hav_km(t["lat"], t["lng"], c["lat"], c["lng"])
                dw = 1 / (1 + (d or 0.5))
                lw = 1 / (1 + abs(math.log(c["lot"] / t["lot_sqft"])))
                pairs.append((c.get("psf_adj", c["land_psf"]), dw * lw))
            return {"level": level, "n": len(cands),
                    "ids": ";".join(c["tx"] for c in cands[:12]),
                    "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                    "p75": wmedian(pairs, 75),
                    "factors": ("comm_adj" if level.startswith("NEARBY")
                                else "dist+lot_weight")}
    # best available even if thin
    for level, cands, _ in cand_levels:
        cands = [c for c in cands if c["land_psf"]]
        if cands:
            pairs = [(c["land_psf"], 1.0) for c in cands]
            return {"level": level + ":THIN", "n": len(cands),
                    "ids": ";".join(c["tx"] for c in cands[:12]),
                    "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                    "p75": wmedian(pairs, 75), "factors": "thin_unweighted"}
    return {"level": "BLOCKED", "n": 0, "ids": "", "p25": None,
            "p50": None, "p75": None, "factors": "no_comps"}


SIZE_K = {"HOUSE": -0.55, "DUPLEX": -0.15, "MULTIPLEX": -0.15}
SIZE_CLIP = (0.65, 1.45)


def size_adj(target_sqft, comp_sqft, k):
    """V3s size-decay curve: reprice a comp's $/sf onto target unit size."""
    if not target_sqft or not comp_sqft:
        return 1.0
    f = (target_sqft / comp_sqft) ** k
    return min(max(f, SIZE_CLIP[0]), SIZE_CLIP[1])


def match_newbuild(t, scen, pools):
    name = scen["name"]
    unit_area = scen["net_saleable"] / scen["units"]
    pool = pools[name]
    k = SIZE_K[name]
    for level, cands in (
            ("500M", [c for c in pool
                      if (d := hav_km(t["lat"], t["lng"], c["lat"], c["lng"]))
                      is not None and d <= 0.5]),
            ("COMM", [c for c in pool if c["community"] == t["hood"]]),
            ("SIDE", [c for c in pool if c["side"] == t["side_key"]])):
        cands = [c for c in cands if c["psf"] and 200 < c["psf"] < 6000]
        if len(cands) < 3:
            continue
        pairs = []
        for c in cands:
            d = hav_km(t["lat"], t["lng"], c["lat"], c["lng"])
            dw = 1 / (1 + (d or 0.5))
            sw = 1 / (1 + abs(math.log(c["fa"] / unit_area))) \
                if c["fa"] and unit_area else 1.0
            bw = 1.2 if c.get("band") in ("0-1", "2-3") else 1.0
            adj = c["psf"] * size_adj(unit_area, c["fa"], k)
            pairs.append((adj, dw * sw * bw))
        return {"level": level, "n": len(cands),
                "ids": ";".join(c["tx"] for c in cands[:12]),
                "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                "p75": wmedian(pairs, 75),
                "factor_kind": cands[0]["factor_kind"]}
    thin = [c for c in pool if c["community"] == t["hood"] and c["psf"]]
    if thin:
        pairs = [(c["psf"] * size_adj(unit_area, c["fa"], k), 1.0)
                 for c in thin]
        return {"level": "COMM:THIN", "n": len(thin),
                "ids": ";".join(c["tx"] for c in thin[:12]),
                "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                "p75": wmedian(pairs, 75),
                "factor_kind": thin[0]["factor_kind"]}
    return {"level": "BLOCKED", "n": 0, "ids": "", "p25": None,
            "p50": None, "p75": None, "factor_kind": "?"}


# ============================================================ economics
def bc_ptt(price):
    if not price or price <= 0:
        return 0.0
    tax = min(price, 200_000) * 0.01
    if price > 200_000:
        tax += (min(price, 2_000_000) - 200_000) * 0.02
    if price > 2_000_000:
        tax += (price - 2_000_000) * 0.03
    return tax


def cost_model(offer, scen, sale, years=BASE_YEARS, gst_mode="EXCL",
               market=0.0):
    """V3s rebuild economics. Returns dict or None if no sale."""
    if not sale:
        return None
    sale_f = sale * (1 + market)
    hard = scen["build_sqft"] * (BUILD_COST[scen["name"]] + PERMIT_PSQFT)
    fin = offer * FIN_LAND_PCT_YR * years + hard * FIN_BUILD_PCT_YR * years
    selling = sale_f * SELLING_PCT
    if gst_mode == "EXCL":
        revenue = sale_f
        gst = sale_f * GST_PCT
    else:
        revenue = sale_f / (1 + GST_PCT)
        gst = sale_f - revenue
    ptt = bc_ptt(offer)
    total = offer + ptt + hard + fin + selling + gst
    return {"offer": offer, "ptt": ptt, "hard": hard, "financing": fin,
            "selling": selling, "gst": gst, "revenue": revenue,
            "total": total, "profit": revenue - total,
            "roi": (revenue - total) / total}


def max_bid(scen, sale, target, years=3, gst_mode="EXCL"):
    if not sale:
        return None
    def roi(o):
        return cost_model(o, scen, sale, years, gst_mode)["roi"]
    if roi(0) < target:
        return "UNATTAINABLE"
    lo, hi = 0.0, 40_000_000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if roi(mid) < target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def confidence(level, n, extra=""):
    if level == "BLOCKED" or n == 0:
        return "BLOCKED"
    if "THIN" in level or n < 3 or "SIDE" in level or extra == "DATA_GAP":
        return "LOW_CONFIDENCE"
    if "MICRO" in level or "COMM" in level:
        return "MEDIUM_CONFIDENCE" if n < 8 else "HIGH_CONFIDENCE"
    return "LOW_CONFIDENCE"


# ================================================================== main
def main():
    listings = json.load(open(LISTING_JSON))["rows"]
    active = [r for r in listings if r["status"] == "ACTIVE"]

    house_rows = list(csv.DictReader(
        open(V12 / "house_price_normalized_per_row.csv")))
    proxy_rows = list(csv.DictReader(
        open(V12 / "attached_proxy_scenarios_per_row.csv")))
    arch = load_archive()
    yb_map = load_resolved_yb()
    tax = load_tax()

    land_pool, aux_pool, drops = build_land_pool(house_rows, arch,
                                               yb_map, tax)
    nb_pools = build_newbuild_pools(house_rows, proxy_rows, arch)

    # ---- A deliverables -------------------------------------------------
    cov = defaultdict(lambda: {"main": 0, "aux": 0})
    for r in land_pool:
        cov[r["community"]]["main"] += 1
    for r in aux_pool:
        cov[r["community"]]["aux"] += 1
    wcsv(DOCS / "land_dominant_comp_coverage.csv", [
        {"community": c, "n_main": v["main"], "n_aux": v["aux"]}
        for c, v in sorted(cov.items())])

    stats = []
    for pool_rows, scope in ((land_pool, "MAIN"), (aux_pool, "AUX_90_95")):
        by = defaultdict(list)
        for r in pool_rows:
            by[(r["community"], band_label(lot_band(r["lot"])))].append(r)
        for (c, b), rs in sorted(by.items()):
            ps = [r["land_psf"] for r in rs if r["land_psf"]]
            if not ps:
                continue
            stats.append({
                "pool": scope, "community": c, "lot_band": b, "n": len(rs),
                "psf_p10": pct(ps, 10), "psf_p25": pct(ps, 25),
                "psf_p50": pct(ps, 50), "psf_p75": pct(ps, 75),
                "psf_p90": pct(ps, 90),
                "price_p50": pct([r["price_2026_09"] for r in rs], 50),
                "lot_p50": pct([r["lot"] for r in rs], 50),
                "conf": "OK" if len(rs) >= 5 else "THIN"})
    wcsv(DOCS / "land_psf_by_community_lot.csv", stats)

    comm_psf = {c: pct([r["land_psf"] for r in land_pool
                        if r["community"] == c and r["land_psf"]], 50)
                for c in {r["community"] for r in land_pool}}

    # ---- per-listing ----------------------------------------------------
    tlist = []
    for r in active:
        t = {"listing": r["address"], "asking": num(r["asking"]),
             "bca": num(r["bca"]), "bca_land": num(r["bca_land"]),
             "lot_sqft": num(r["lot_sqft"]), "frontage": num(r["frontage_ft"]),
             "depth": num(r["depth_ft"]), "zoning": r.get("zoning") or "",
             "hood": r["hood"], "lat": num(r["lat"]), "lng": num(r["lng"]),
             "side_key": {"东区": "Vancouver East",
                          "西区": "Vancouver West"}.get(r["side"], ""),
             "tier": r.get("tier"), "link": r.get("link", "")}
        scen = r.get("analysis", {}).get("scenarios", {})
        t["scenarios"] = []
        for name in ("HOUSE", "DUPLEX", "MULTIPLEX"):
            s = scen.get(name)
            if not s:
                continue
            t["scenarios"].append({
                "name": name, "fsr": s.get("fsr"),
                "build_sqft": num(s.get("build_sqft")),
                "net_saleable": num(s.get("net_saleable_area"))
                or num(s.get("build_sqft")),
                "units": s.get("units"), "eligible": s.get("eligible"),
                "verified": s.get("verified"),
                "zone_flags": s.get("zone_flags")})
        tlist.append(t)

    land_rows, scen_rows, sale_rows, roi_rows, bid_rows = [], [], [], [], []
    market_rows = []
    for t in tlist:
        # ---- land valuation
        m = match_land(t, land_pool, comm_psf)
        extra = ("DATA_GAP" if not t["bca_land"] or not t["zoning"]
                 else "")
        conf = confidence(m["level"], m["n"], extra)
        fair = (m["p50"] or 0) * t["lot_sqft"] if m["p50"] else None
        bca_impr_share = (1 - t["bca_land"] / t["bca"]) \
            if t["bca"] and t["bca_land"] else None
        utility_flag = ("EXISTING_HOUSE_UTILITY"
                        if bca_impr_share is not None
                        and bca_impr_share > 0.10 else "LAND_DOMINANT")
        land_rows.append({
            "listing": t["listing"], "asking": t["asking"],
            "hood": t["hood"], "side": t["side_key"],
            "lot_sqft": t["lot_sqft"], "frontage": t["frontage"],
            "zoning": t["zoning"], "bca": t["bca"], "bca_land": t["bca_land"],
            "ask_bca_ratio": round(t["asking"] / t["bca"], 3)
            if t["bca"] else "",
            "ask_per_lot_sqft": round(t["asking"] / t["lot_sqft"], 1)
            if t["lot_sqft"] else "",
            "land_comp_level": m["level"], "land_comp_n": m["n"],
            "land_comp_ids": m["ids"], "land_weighted_psf":
            round(m["p50"], 1) if m["p50"] else "",
            "land_psf_p25": round(m["p25"], 1) if m["p25"] else "",
            "land_psf_p50": round(m["p50"], 1) if m["p50"] else "",
            "land_psf_p75": round(m["p75"], 1) if m["p75"] else "",
            "land_adjustment_factors": m["factors"],
            "fair_land_value_today": round(fair) if fair else "",
            "fair_land_value_low": round((m["p25"] or 0) * t["lot_sqft"])
            if m["p25"] else "",
            "fair_land_value_high": round((m["p75"] or 0) * t["lot_sqft"])
            if m["p75"] else "",
            "fair_land_psf": round(m["p50"], 1) if m["p50"] else "",
            "discount_to_land_market_pct":
            round(100 * (fair - t["asking"]) / fair, 1) if fair else "",
            "discount_to_land_market_cad": round(fair - t["asking"])
            if fair else "",
            "utility_flag": utility_flag,
            "confidence": conf,
            "valuation_asof": HPI_ASOF, "hpi_asof": HPI_ASOF,
            "listing_snapshot_date": LISTING_SNAPSHOT,
            "comps_snapshot_id": "sales_archive_v1_0_3+hpi_v1_2",
            "model_version": MODEL_VERSION, "cost_version": COST_VERSION})

        # ---- scenarios + sale estimates + ROI
        for s in t["scenarios"]:
            status = ("VERIFIED" if s["verified"] else
                      "UNVERIFIED" if s["eligible"] else "INELIGIBLE")
            scen_rows.append({
                "listing": t["listing"], "scenario": s["name"],
                "zoning": t["zoning"], "fsr": s["fsr"],
                "build_sqft": s["build_sqft"],
                "net_saleable": s["net_saleable"], "units": s["units"],
                "eligible": s["eligible"], "verified": s["verified"],
                "status": status,
                "zone_flags": ";".join(s["zone_flags"] or [])})
            if not s["verified"]:
                continue
            nb = match_newbuild(t, s, nb_pools)
            est = nb["p50"] * s["net_saleable"] if nb["p50"] else None
            sale_conf = confidence(nb["level"], nb["n"], extra)
            sale_rows.append({
                "listing": t["listing"], "scenario": s["name"],
                "build_sqft": s["build_sqft"],
                "net_saleable": s["net_saleable"], "units": s["units"],
                "comp_level": nb["level"], "comp_n": nb["n"],
                "comp_ids": nb["ids"], "psf_p25": nb["p25"],
                "psf_p50": nb["p50"], "psf_p75": nb["p75"],
                "psf_factor_kind": nb["factor_kind"],
                "sale_value_today": round(est) if est else "",
                "sale_low": round(nb["p25"] * s["net_saleable"])
                if nb["p25"] else "",
                "sale_high": round(nb["p75"] * s["net_saleable"])
                if nb["p75"] else "",
                "confidence": sale_conf,
                "basis": "2026-09 normalized comps" +
                (";PROXY_INDEX" if nb["factor_kind"] == "PROXY" else "")})

            if not est:
                continue
            # ---- ROI layers (EXCL GST base; INCL in file too)
            for gm in ("EXCL", "INCL"):
                simple_inv = t["asking"] + (
                    s["build_sqft"] * (BUILD_COST[s["name"]] + PERMIT_PSQFT))
                c1 = cost_model(t["asking"], s, est, BASE_YEARS, gm)
                roi_rows.append({
                    "listing": t["listing"], "scenario": s["name"],
                    "gst_scenario": gm, "asking": t["asking"],
                    "est_sale_today": round(est),
                    "investment_simple": round(simple_inv),
                    "roi_simple":
                    round(100 * (est - simple_inv) / simple_inv, 1),
                    "total_cost_dev_1y": round(c1["total"]),
                    "profit_development_1y": round(c1["profit"]),
                    "roi_development_1y": round(100 * c1["roi"], 1),
                    "land_conf": conf, "sale_conf": sale_conf,
                    "market_value_today": round(est),
                    "future_market_factor_base": 0.0})
                for mk in MARKET_SCEN:
                    cm = cost_model(t["asking"], s, est, BASE_YEARS, gm, mk)
                    market_rows.append({
                        "listing": t["listing"], "scenario": s["name"],
                        "gst_scenario": gm,
                        "future_market_factor": mk,
                        "future_sale_estimate": round(est * (1 + mk)),
                        "roi_pct": round(100 * cm["roi"], 1)})

            # ---- max bid + negotiation (1y dev base, EXCL)
            row = {"listing": t["listing"], "scenario": s["name"],
                   "asking": t["asking"], "est_sale_today": round(est),
                   "fair_land_value_today": round(fair) if fair else "",
                   "ask_minus_fair_land":
                   round(t["asking"] - fair) if fair else ""}
            for tg in ROI_TARGETS:
                mb = max_bid(s, est, tg, BASE_YEARS)
                key = int(tg * 100)
                row[f"max_bid_roi_{key}"] = (round(mb) if isinstance(
                    mb, float) else mb or "")
                row[f"ask_minus_max_bid_{key}"] = (
                    round(t["asking"] - mb) if isinstance(mb, float) else "")
                row[f"discount_required_from_ask_{key}_pct"] = (
                    round(100 * (t["asking"] - mb) / t["asking"], 1)
                    if isinstance(mb, float) and t["asking"] else "")
                # 建议出价上限 = min(目标ROI最高出价, 土地市场估值)
                caps = [v for v in (mb if isinstance(mb, float) else None,
                                    fair) if v is not None]
                row[f"suggested_cap_roi_{key}"] = (round(min(caps))
                                                  if caps else "")
            for cut in OFFER_CUTS:
                offer = t["asking"] * cut
                rc = cost_model(offer, s, est, BASE_YEARS, "EXCL")["roi"]
                row[f"roi_at_ask_x{int(cut*100)}"] = round(100 * rc, 1)
            bid_rows.append(row)

    wcsv(DOCS / "active_house_land_valuation.csv", land_rows)
    wcsv(DOCS / "active_house_buildable_scenarios.csv", scen_rows)
    wcsv(DOCS / "active_house_newbuild_sale_estimate.csv", sale_rows)
    wcsv(DOCS / "active_house_listing_roi.csv", roi_rows)
    wcsv(DOCS / "active_house_max_bid_and_negotiation.csv", bid_rows)
    wcsv(DOCS / "active_house_market_scenarios.csv", market_rows)

    # ---- rankings --------------------------------------------------------
    land_rank = sorted(
        [r for r in land_rows if r["discount_to_land_market_pct"] != ""],
        key=lambda r: -r["discount_to_land_market_pct"])
    for i, r in enumerate(land_rank, 1):
        r["rank_land"] = i
    wcsv(DOCS / "land_buy_value_ranking.csv", [
        {"rank": r["rank_land"], "listing": r["listing"],
         "asking": r["asking"], "fair_land_value": r["fair_land_value_today"],
         "discount_pct": r["discount_to_land_market_pct"],
         "discount_cad": r["discount_to_land_market_cad"],
         "ask_per_lot_sqft": r["ask_per_lot_sqft"],
         "comp_level": r["land_comp_level"], "comp_n": r["land_comp_n"],
         "confidence": r["confidence"], "utility_flag": r["utility_flag"]}
        for r in land_rank])

    # rebuild ranking: one row per (listing, scenario) — no confidence
    # hard filter; users judge evidence themselves. Keys always carry
    # the scenario so max_bid is never cross-matched (V1.4.1 fix).
    bmap = {(r["listing"], r["scenario"]): r for r in bid_rows}
    lmap = {r["listing"]: r for r in land_rows}
    rank2 = []
    for r in roi_rows:
        if r["gst_scenario"] != "EXCL":
            continue
        k = r["listing"]
        b = bmap.get((k, r["scenario"]), {})
        lm = lmap.get(k, {})
        d20 = b.get("discount_required_from_ask_20_pct")
        land_ok = (lm.get("discount_to_land_market_pct") != "" and
                   lm["discount_to_land_market_pct"] > 0)
        roi_ok = r["roi_development_1y"] >= 10
        quad = ("BOTH" if land_ok and roi_ok else
                "LAND_VALUE_ONLY" if land_ok else
                "ROI_ONLY" if roi_ok else "NEITHER")
        rank2.append({
            "listing": k, "scenario": r["scenario"],
            "asking": r["asking"], "est_sale_today": r["est_sale_today"],
            "roi_development_1y": r["roi_development_1y"],
            "profit_development_1y": r["profit_development_1y"],
            "roi_simple": r["roi_simple"],
            "max_bid_roi_10": b.get("max_bid_roi_10"),
            "max_bid_roi_20": b.get("max_bid_roi_20"),
            "max_bid_roi_30": b.get("max_bid_roi_30"),
            "discount_required_20_pct": d20,
            "suggested_cap_roi_20": b.get("suggested_cap_roi_20"),
            "land_discount_pct": lm.get("discount_to_land_market_pct"),
            "quadrant": quad,
            "all_gates": "PASS" if (land_ok and roi_ok) else "FAIL",
            "conf": f"{r['land_conf']}/{r['sale_conf']}"})
    rank2.sort(key=lambda r: -r["roi_development_1y"])
    for i, r in enumerate(rank2, 1):
        r["rank"] = i
    wcsv(DOCS / "rebuild_roi_ranking.csv", rank2)

    # ---- method audit ----------------------------------------------------
    audit = [{"metric": "land_pool_main", "value": len(land_pool)},
             {"metric": "land_pool_aux_90_95", "value": len(aux_pool)},
             {"metric": "land_pool_dropped", "value": json.dumps(drops)},
             {"metric": "active_listings", "value": len(tlist)},
             {"metric": "land_valued", "value": sum(
                 1 for r in land_rows if r["fair_land_value_today"] != "")},
             {"metric": "sale_estimated_scenarios", "value": len(sale_rows)},
             {"metric": "unattainable_20pct", "value": sum(
                 1 for r in bid_rows
                 if r.get("max_bid_roi_20") == "UNATTAINABLE")}]
    wcsv(DOCS / "land_comparable_method_audit.csv", audit)

    # ---- report ----------------------------------------------------------
    q1 = [r for r in land_rows if r["discount_to_land_market_pct"] != ""
          and r["discount_to_land_market_pct"] > 0]
    buck = {x: sum(1 for r in land_rows
                   if r["discount_to_land_market_pct"] != ""
                   and r["discount_to_land_market_pct"] >= x)
            for x in (5, 10, 15, 20)}
    base = [r for r in roi_rows if r["gst_scenario"] == "EXCL"]
    q3 = {}
    for t_ in ROI_TARGETS:
        key = int(t_ * 100)
        q3[key] = len({r["listing"] for r in base
                       if r["roi_development_1y"] >= t_ * 100 - 0.05})
    q4 = {int(c * 100): len({r["listing"] for r in bid_rows
                             if isinstance(r["roi_at_ask_x"
                                           + str(int(c * 100))],
                                           float) and
                             r["roi_at_ask_x" + str(int(c * 100))] >= 10})
          for c in OFFER_CUTS}
    q5 = [r for r in rank2 if r["all_gates"] == "PASS"]
    q6 = [r for r in rank2 if r["roi_development_1y"] >= 10
          and "LOW" in r["conf"]]

    rep = f"""# Listing-Level Valuation V1.4 — 报告

基准:HPI {HPI_ASOF};挂牌快照 {LISTING_SNAPSHOT};成本 {COST_VERSION}。
224 套 ACTIVE 中 {len(tlist)} 套进入逐房源估值。

## 土地型旧房池(模块 A)

主池 age≥50 & BCA land_share≥95%: **{len(land_pool)}** 笔;
辅助池 90-95%: **{len(aux_pool)}** 笔;剔除 {json.dumps(drops)}。

## Q1 Ask 低于独立土地估值

{len(q1)} / {len(tlist)} 套 Ask < fair_land_value;≥5%: {buck[5]},≥10%: {buck[10]},≥15%: {buck[15]},≥20%: {buck[20]}。

## Q3 基准 ROI 达标房源数(1年开发期,0% 未来涨跌,EXCL)

| 目标 | 达标房源数 |
|---|---|
| ≥10% | {q3[10]} |
| ≥20% | {q3[20]} |
| ≥30% | {q3[30]} |

## Q4 砍价后达标(≥10% 开发 ROI)

砍5%: {q4[95]} 套;砍10%: {q4[90]} 套;砍15%: {q4[85]} 套。

## Q5 全门通过(土地合理 + verified 方案 + ROI≥10%;置信度展示不过滤)

{len(q5)} 套: {", ".join(r["listing"] for r in q5[:15])}

## Q6 疑似假机会(ROI≥10% 但 LOW_CONFIDENCE)

{len(q6)} 套: {", ".join(r["listing"] for r in q6[:15])}

## 口径

- fair_land_value = 匹配土地 comps 加权 P50 $/sf × lot_sqft;
  水位 MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→NEARBY_ADJ→SIDE。
- est_sale = 匹配新房 comps 加权 P50 $/sf × net_saleable;DUPLEX/MPX
  comps 为代理指数归一化(PROXY 标),MPX ×{MPX_PRODUCT_FACTOR} 产品折价。
- ROI 层级:roi_simple=ASK+建安;roi_development_1y=V3s 全成本
  (含 PTT/办证/1年融资/销售/GST),统一 1 年开发融资窗口;
  未来出售时间与 ROI 分离——市场 ±10/20% 仅为独立价格压力情景,
  不再有 3y/5y ROI;开发前长期持有成本独立记录,不叠加。
- max_bid 以 1y 开发期/EXCL 为基准求满足目标 ROI 的最高出价;
  土地白送仍不达标 = UNATTAINABLE。
- utility_flag=EXISTING_HOUSE_UTILITY(改善占比>10%)提示房源含
  居住价值,土地估值与房价需分开看。
"""
    (DOCS / "listing_level_valuation_v1_4_report.md").write_text(rep)

    meth = """# Valuation Methodology V1.4

## 分离原则
- 土地估值 = 今日市场价值(土地型旧房 comps,land_share≥0.95 主池 /
  90-95% 辅助池,独立输出)。
- 新房估值 = "今天建成"的价值(matched $/sf × 方案 net_saleable)。
- 未来情景 = future_market_factor 独立乘数;基准 0% 不假设涨跌;
  已对 2026-09 归一化的价格不再二次扣历史跌幅。
- GST 单扣一次:EXCL(售价为未税,5% 记成本)或 INCL(含在售价,
  净收入=售/1.05);土地、地下室、LWH 不重复计价(方案
  net_saleable 单一计价面积)。

## 匹配与回退
土地:MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→
NEARBY_COMM_1.5KM_ADJ(社区 P50 比值调整)→SIDE_FALLBACK(低置信)。
新房:500M→COMM→SIDE;THIN 明确标注。
comp_ids 保留前 12 个可比 tx,可追溯。

## 成本(V3s frozen)
hard=建面×(350/400/450+40permit);fin=offer×5%×1年+
hard×7.725%×1年(统一1年开发融资窗口;出售时间与ROI分离,
开发前长期持有成本独立记录不叠加);selling=售价×4%;GST 5%;PTT=BC阶梯;
total=offer+ptt+hard+fin+selling+gst;roi=(revenue-total)/total。

## 置信
HIGH(n≥8 micro/comm)→MEDIUM(3≤n<8)→LOW(THIN/SIDE/缺字段)→BLOCKED。
"""
    (DOCS / "valuation_methodology.md").write_text(meth)

    files = sorted(p.name for p in DOCS.iterdir() if p.is_file())
    (DOCS / "manifest.json").write_text(json.dumps({
        "version": MODEL_VERSION, "hpi_asof": HPI_ASOF,
        "listing_snapshot": LISTING_SNAPSHOT,
        "cost_version": COST_VERSION,
        "active_listings": len(tlist), "deliverables": files,
        "land_pool": {"main": len(land_pool), "aux": len(aux_pool),
                      "dropped": drops}}, ensure_ascii=False, indent=2))
    print(f"active {len(tlist)}; land pool {len(land_pool)}+{len(aux_pool)}; "
          f"valued {sum(1 for r in land_rows if r['fair_land_value_today'] != '')};"
          f" roi rows {len(roi_rows)}; q5 {len(q5)} q6 {len(q6)}")


if __name__ == "__main__":
    main()
