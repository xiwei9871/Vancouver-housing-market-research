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
             (6000, 8000), (8000, 12000), (12000, math.inf))
# Land $/sf falls steeply with lot size — a comp lot is only usable as
# like-for-like evidence inside a size window around the target lot.
# Prevents 10k-35k sf assembly parcels from repricing a standard lot.
LOT_RATIO_OK = (0.6, 1.67)
# pre-fix banding kept only to reproduce V1.4.3 values for the diff
LOT_BANDS_LEGACY = ((2500, 3000), (3000, 4000), (4000, 5000),
                    (5000, 6000), (6000, math.inf))
MARKET_SCEN = (-0.20, -0.10, 0.0, 0.10, 0.20)

# Listing feed hood labels -> archive community names. Assigned by
# centroid distance of ACTIVE listings vs pool comps (V1.4.2);
# documented in the report and auditable via hood_alias column.
HOOD_COMM_ALIAS = {
    "Collingwood": "Collingwood(南)",
    "Dunbar": "Dunbar-Southlands",
    "Renfrew": "Renfrew(北)",
    "Victoria": "Kensington-Cedar Cottage",
    "Fraser": "Riley Park",
    "Knight": "Kensington-Cedar Cottage",
    "Main": "Riley Park",
    "Guides": "Renfrew(北)",
    "South Marine": "Victoria-Fraserview",
    "Downtown Eastside": "Strathcona",
}
ROI_TARGETS = (0.10, 0.20, 0.30)
OFFER_CUTS = (0.95, 0.90, 0.85)


def zoning_residential(z):
    """V1.4.4 audit gate: only R*/F* districts are residential.
    C-*, CD, DEOD and blank zoning cannot support a verified-legal
    residential scenario — the estimate may stay as an assumption,
    but the row must not read VERIFIED."""
    return bool(z) and z.strip().upper().startswith(("R", "F"))


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


def lot_band(lot, bands=LOT_BANDS):
    for i, (lo, hi) in enumerate(bands):
        if lo <= lot < hi:
            return i
    return None


def band_label(i):
    if i is None:
        return "?"
    lo, hi = LOT_BANDS[i]
    return (f"{int(lo)}+" if math.isinf(hi)
            else f"{int(lo)}-{int(hi)}")


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
LOT_DIMS_PATH = ROOT / ("data/research/raw/location/"
                        "bca-arcgis-sales-2026-09-06-enriched-spatial.json")
LOT_DIMS_FROZEN = DOCS / "lot_dims_frozen_input.csv"


def load_lot_dims():
    """pid+sale_date -> BCA lot area from property-information
    dimensions (frontage x depth). This is the *legal* lot evidence;
    the raw ArcGIS parcel polygon (SHAPE_Area) is a different field
    that can cover shared/ravine area and inflate lot ~2.3x.

    Resolution order (V1.4.4 closure): committed frozen input first;
    otherwise regenerate it from the raw enriched-spatial json when
    that local file exists. If neither is present this is a hard
    error — silently falling back to unverified GIS parcel areas
    would revert the entire land-valuation fix."""
    if LOT_DIMS_FROZEN.exists():
        return {(r["pid"].strip(), r["sale_date"][:10]): {
                "lot": num(r["lot_area_sqft"]),
                "frontage": num(r["frontage_ft"]),
                "depth": num(r["depth_ft"])}
                for r in csv.DictReader(open(LOT_DIMS_FROZEN))}
    if LOT_DIMS_PATH.exists():
        idx = {}
        d = json.load(open(LOT_DIMS_PATH))
        rows = d if isinstance(d, list) else next(
            (d[k] for k in ("sales", "results", "records", "features")
             if k in d), [])
        for r in rows:
            if (r.get("lot_area_source")
                    != "BCA_PROPERTY_INFORMATION_DIMENSIONS"):
                continue
            pid = (r.get("pid") or "").strip()
            dt = (r.get("sale_date") or "")[:10]
            if pid and dt and num(r.get("lot_area_sqft")):
                idx[(pid, dt)] = {
                    "lot": num(r["lot_area_sqft"]),
                    "frontage": num(r.get("frontage_ft")),
                    "depth": num(r.get("depth_ft"))}
        # persist the desensitized frozen input (keys + dims only,
        # no addresses) so future runs never need the raw json
        wcsv(LOT_DIMS_FROZEN, [
            {"pid": pid, "sale_date": dt, "lot_area_sqft": v["lot"],
             "frontage_ft": v["frontage"] or "",
             "depth_ft": v["depth"] or ""}
            for (pid, dt), v in sorted(idx.items())],
            fields=["pid", "sale_date", "lot_area_sqft",
                    "frontage_ft", "depth_ft"])
        return idx
    raise RuntimeError(
        "lot dims input missing: expected committed frozen input at "
        f"{LOT_DIMS_FROZEN} or raw source at {LOT_DIMS_PATH}; "
        "refusing to fall back to unverified GIS parcel areas")


def build_land_pool(house_rows, arch, yb_map, tax, dims_idx=None):
    """dims_idx (V1.4.4): pid+date -> BCA lot dimensions. When present,
    the legal/dimensions lot overrides the raw ArcGIS parcel polygon —
    big GIS parcels (shared/ravine area) otherwise poison the $/sf
    evidence for standard lots."""
    dims_idx = dims_idx or {}
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
        a = arch.get(r["tx"], {})
        lot_gis = num(r["lot"])
        lot, lot_src, lot_flag = lot_gis, "ARCHIVE_GIS_PARCEL", ""
        dims = dims_idx.get(((a.get("pid") or "").strip(),
                             (r["date"] or "")[:10]))
        if dims:
            lot_src = "BCA_DIMS"
            if lot_gis and dims["lot"] / lot_gis < 0.67:
                lot_flag = "LOT_CORRECTED_2X"
            elif lot_gis and abs(dims["lot"] / lot_gis - 1) > 0.05:
                lot_flag = "LOT_CORRECTED"
            lot = dims["lot"]
        elif lot_gis and lot_gis >= 6000:
            lot_flag = "LOT_GIS_UNVERIFIED_BIG"
        if not lot or lot < 1500:
            drops["no_lot"] += 1
            continue
        share, asmt = tax_share(tax.get((a.get("pid") or "").strip(), {}),
                                int(r["year"]))
        if share is None:
            drops["no_tax"] += 1
            continue
        rec = {"tx": r["tx"], "community": r["community"], "side": r["side"],
               "address": a.get("address"), "date": r["date"],
               "year": int(r["year"]),
               "yb_tier": r["tier"], "pid": (a.get("pid") or "").strip(),
               "lot": lot, "lot_gis": lot_gis, "lot_src": lot_src,
               "lot_flag": lot_flag,
               "lat": num(a.get("lat")), "lng": num(a.get("lng")),
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
        # MULTIPLEX_UNIT strata rows carry a unit price but no floor
        # area in the feed — keep them for a per-unit-price fallback,
        # which is labelled separately from $/sf evidence
        if not px or (not fa and r["product"] != "MULTIPLEX_UNIT"):
            continue
        la, ln = geo(r)
        rec = {"tx": r["tx"], "community": r["community"],
               "side": r["side"], "lat": la, "lng": ln, "fa": fa,
               "psf": px / fa if fa else None,
               "unit_price": px, "factor_kind": "PROXY",
               "proxy_note": r.get("proxy_note", "")}
        (dup if r["product"] == "DUPLEX_HALF" else mpx
         if r["product"] == "MULTIPLEX_UNIT" else []).append(rec)
    for r in mpx:
        # V3s stacked-product discount, on whichever basis exists
        if r["psf"]:
            r["psf"] *= MPX_PRODUCT_FACTOR
        r["unit_price"] *= MPX_PRODUCT_FACTOR
    return {"HOUSE": hb, "DUPLEX": dup, "MULTIPLEX": mpx}


# ====================================================== matching engines
def match_land(t, pool, comm_psf, nearby_fix=True, lot_compat=True,
               bands=LOT_BANDS):
    """Waterfall; returns dict with level, comps, weighted psf stats.
    NEARBY_COMM adjustment converts a comp's land $/sf from its source
    community level to the target community level by multiplying with
    target_p50/source_p50 (V1.4.2 fix: previously inverted).
    V1.4.4: lot_compat gates comps to 0.6-1.67x target lot —
    set False only to reproduce the pre-fix behaviour for diff audits."""
    tband = lot_band(t["lot_sqft"], bands)
    cand_levels = []

    def same_comm(r):
        return r["community"] == t["hood_comm"]

    def lot_ok(r):
        # like-for-like lot size — without this guard, 10k+ sf parcels
        # reprice a standard lot at their (much lower) marginal $/sf
        if not lot_compat:
            return True
        if not t["lot_sqft"] or not r["lot"]:
            return False
        return LOT_RATIO_OK[0] <= r["lot"] / t["lot_sqft"] <= LOT_RATIO_OK[1]

    L = [r for r in pool if same_comm(r) and lot_band(r["lot"], bands) == tband
         and lot_ok(r)
         and (d := hav_km(t["lat"], t["lng"], r["lat"], r["lng"])) is not None
         and d <= 0.5]
    cand_levels.append(("MICRO_500M", L, 1.0))
    L = [r for r in pool if same_comm(r) and lot_band(r["lot"], bands) == tband
         and lot_ok(r)]
    cand_levels.append(("COMM_LOTBAND", L, 1.0))
    adj = {tband - 1, tband + 1} if tband is not None else set()
    L = [r for r in pool if same_comm(r) and lot_band(r["lot"], bands) in adj
         and lot_ok(r)]
    cand_levels.append(("COMM_ADJ_BAND", L, 1.0))
    L = [r for r in pool if same_comm(r) and lot_ok(r)]
    cand_levels.append(("COMM_ANY", L, 1.0))
    # nearby communities within 1.5km, adjusted by community-p50 ratio
    near = []
    for r in pool:
        d = hav_km(t["lat"], t["lng"], r["lat"], r["lng"])
        if d is not None and d <= 1.5 and not same_comm(r) and lot_ok(r):
            cf = None
            tc, cc = comm_psf.get(r["community"]), comm_psf.get(t["hood_comm"])
            if tc and cc:
                # target/source: convert source-community evidence onto
                # the target community's price level
                cf = cc / tc if nearby_fix else tc / cc
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
                    "n_unverified_big": sum(
                        1 for c in cands
                        if c.get("lot_flag") == "LOT_GIS_UNVERIFIED_BIG"),
                    "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                    "p75": wmedian(pairs, 75),
                    "factors": ("comm_adj+lot_compat"
                                if level.startswith("NEARBY")
                                else "dist+lot_weight+lot_compat")}
    # best available even if thin
    for level, cands, _ in cand_levels:
        cands = [c for c in cands if c["land_psf"]]
        if cands:
            pairs = [(c["land_psf"], 1.0) for c in cands]
            return {"level": level + ":THIN", "n": len(cands),
                    "ids": ";".join(c["tx"] for c in cands[:12]),
                    "n_unverified_big": sum(
                        1 for c in cands
                        if c.get("lot_flag") == "LOT_GIS_UNVERIFIED_BIG"),
                    "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                    "p75": wmedian(pairs, 75), "factors": "thin_unweighted"}
    return {"level": "BLOCKED", "n": 0, "ids": "", "n_unverified_big": 0,
            "p50": None, "p75": None, "p25": None, "factors": "no_comps"}


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
            ("COMM", [c for c in pool if c["community"] == t["hood_comm"]]),
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
    thin = [c for c in pool if c["community"] == t["hood_comm"] and c["psf"]]
    if thin:
        pairs = [(c["psf"] * size_adj(unit_area, c["fa"], k), 1.0)
                 for c in thin]
        return {"level": "COMM:THIN", "n": len(thin),
                "ids": ";".join(c["tx"] for c in thin[:12]),
                "p25": wmedian(pairs, 25), "p50": wmedian(pairs, 50),
                "p75": wmedian(pairs, 75),
                "factor_kind": thin[0]["factor_kind"]}
    # MULTIPLEX fallback: strata unit sales have no floor area in the
    # feed, so no $/sf is possible. Price per unit is the only honest
    # evidence — estimate = units x adjusted unit comp price. Always
    # flagged PROXY_UNITPX + FA_MISSING so it cannot be mistaken for
    # measured new-build $/sf evidence.
    if name == "MULTIPLEX":
        ups = [c for c in pool if c.get("unit_price")]
        for ulvl, uc in (
                ("500M_UNITPX", [c for c in ups
                                 if (d := hav_km(t["lat"], t["lng"],
                                                 c["lat"], c["lng"]))
                                 is not None and d <= 0.5]),
                ("COMM_UNITPX", [c for c in ups
                                 if c["community"] == t["hood_comm"]]),
                ("SIDE_UNITPX", [c for c in ups
                                 if c["side"] == t["side_key"]])):
            if len(uc) < 3:
                continue
            pairs = [(c["unit_price"], 1.0) for c in uc]
            return {"level": ulvl, "n": len(uc),
                    "ids": ";".join(c["tx"] for c in uc[:12]),
                    "p25": wmedian(pairs, 25),
                    "p50": wmedian(pairs, 50),
                    "p75": wmedian(pairs, 75),
                    "factor_kind": "PROXY_UNITPX",
                    "unit_price_mode": True}
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
        # 售价为不含 GST 净价:卖方收全价,另向 CRA 汇缴 5%
        revenue = sale_f
        gst = sale_f * GST_PCT
    else:
        # INCL:售价为含税总价,卖方净得 sale/1.05,汇缴 sale×5/105。
        # 单一计价:revenue 取含税收款,gst 成本行记实际汇缴额 ——
        # 不得再对 revenue 做 /1.05(那会与 gst 成本行重复扣税)。
        revenue = sale_f
        gst = sale_f * GST_PCT / (1 + GST_PCT)
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
    if "THIN" in level or n < 3 or "SIDE" in level or extra:
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

    dims_idx = load_lot_dims()
    land_pool, aux_pool, drops = build_land_pool(house_rows, arch,
                                               yb_map, tax, dims_idx)
    # V1.4.3 reproduction for the full before/after table: same pool
    # WITHOUT the dims correction, matched with the legacy bands and
    # no lot-compat guard (V1.4.2 alias fix and NEARBY direction were
    # already in place at V1.4.3, so they stay on)
    pre_pool, _, _ = build_land_pool(house_rows, arch, yb_map, tax,
                                     dims_idx={})
    comm_psf_pre = {c: pct([r["land_psf"] for r in pre_pool
                            if r["community"] == c and r["land_psf"]], 50)
                    for c in {r["community"] for r in pre_pool}}
    wcsv(DOCS / "land_pool_lot_audit.csv", [
        {"tx": r["tx"], "address": r.get("address"),
         "community": r["community"], "date": r["date"],
         "lot_used": round(r["lot"]), "lot_gis": round(r["lot_gis"])
         if r["lot_gis"] else "", "lot_src": r["lot_src"],
         "lot_flag": r["lot_flag"],
         "land_psf_used": round(r["land_psf"], 1)
         if r["land_psf"] else "",
         "price_2026_09": round(r["price_2026_09"])
         if r["price_2026_09"] else ""}
        for r in land_pool],
        fields=["tx", "address", "community", "date", "lot_used",
                "lot_gis", "lot_src", "lot_flag", "land_psf_used",
                "price_2026_09"])
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
    # community comp centroids — per-listing alias position check
    comm_cent = {}
    for c in {r["community"] for r in land_pool}:
        pts = [(r["lat"], r["lng"]) for r in land_pool
               if r["community"] == c and r["lat"] and r["lng"]]
        if pts:
            comm_cent[c] = (statistics.mean(p[0] for p in pts),
                            statistics.mean(p[1] for p in pts))

    # ---- per-listing ----------------------------------------------------
    tlist = []
    for r in active:
        hood = r["hood"]
        t = {"listing": r["address"], "asking": num(r["asking"]),
             "bca": num(r["bca"]), "bca_land": num(r["bca_land"]),
             "lot_sqft": num(r["lot_sqft"]), "frontage": num(r["frontage_ft"]),
             "depth": num(r["depth_ft"]), "zoning": r.get("zoning") or "",
             "hood": hood,
             "hood_comm": HOOD_COMM_ALIAS.get(hood, hood),
             "lat": num(r["lat"]), "lng": num(r["lng"]),
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
    nearby_diff = []
    newbuild_diff = []
    lot_fix_diff = []
    prob_comp = []
    v143_diff = []
    for t in tlist:
        # ---- land valuation
        m = match_land(t, land_pool, comm_psf)
        # V1.4.4 diff audits: (a) full V1.4.3 -> V1.4.4 effect on the
        # corrected+dims pool vs legacy pool/bands/no guard;
        # (b) isolated effect of the lot-compat guard on today's pool
        m_pre43 = match_land(t, pre_pool, comm_psf_pre,
                             lot_compat=False, bands=LOT_BANDS_LEGACY)
        fair43 = (m_pre43["p50"] or 0) * t["lot_sqft"] \
            if m_pre43["p50"] else None
        fair44 = (m["p50"] or 0) * t["lot_sqft"] if m["p50"] else None
        v143_diff.append({
            "listing": t["listing"], "hood_comm": t["hood_comm"],
            "lot_sqft": t["lot_sqft"],
            "level_v143": m_pre43["level"], "level_v144": m["level"],
            "psf_v143": round(m_pre43["p50"], 1)
            if m_pre43["p50"] else "",
            "psf_v144": round(m["p50"], 1) if m["p50"] else "",
            "fair_v143": round(fair43) if fair43 else "",
            "fair_v144": round(fair44) if fair44 else "",
            "delta_cad": round(fair44 - fair43)
            if fair43 and fair44 else "",
            "changed": "" if (fair43 and fair44 and
                              abs(fair44 / fair43 - 1) <= 0.05
                              and m_pre43["level"] == m["level"])
            else "CHANGED"})
        m_pre = match_land(t, land_pool, comm_psf, lot_compat=False)
        if m_pre["p50"] and m["p50"] and (
                m_pre["level"] != m["level"]
                or abs(m["p50"] / m_pre["p50"] - 1) > 0.05):
            lot_fix_diff.append({
                "listing": t["listing"], "hood_comm": t["hood_comm"],
                "lot_sqft": t["lot_sqft"],
                "level_before": m_pre["level"], "level_after": m["level"],
                "psf_before": round(m_pre["p50"], 1),
                "psf_after": round(m["p50"], 1),
                "fair_before": round(m_pre["p50"] * t["lot_sqft"]),
                "fair_after": round(m["p50"] * t["lot_sqft"]),
                "delta_cad": round((m["p50"] - m_pre["p50"])
                                 * t["lot_sqft"]),
                "ids_before": m_pre["ids"], "ids_after": m["ids"]})
        # per-comp audit for the flagged large-lot mismatches
        if m_pre["p50"] and m["p50"] and \
                m_pre["p50"] * t["lot_sqft"] < 0.65 * (
                        m["p50"] * t["lot_sqft"]):
            ids_pre = set(m_pre["ids"].split(";")) - {""}
            for c in land_pool:
                if c["tx"] in ids_pre or c["tx"] in set(
                        m["ids"].split(";")):
                    prob_comp.append({
                        "listing": t["listing"],
                        "lot_sqft": t["lot_sqft"],
                        "tx": c["tx"], "community": c["community"],
                        "comp_lot": round(c["lot"]),
                        "lot_ratio": round(c["lot"] / t["lot_sqft"], 2),
                        "sale_2026_09": round(c["price_2026_09"] or 0),
                        "land_psf": round(c["land_psf"], 1),
                        "kept": c["tx"] in set(m["ids"].split(";"))})
        # V1.4.2 diff audit: listings that USED the NEARBY fallback under
        # the pre-fix pipeline (no hood alias + inverted ratio)
        t_old = dict(t, hood_comm=t["hood"])
        m_old = match_land(t_old, land_pool, comm_psf,
                           nearby_fix=False)
        if m_old["level"].startswith("NEARBY") and m_old["p50"] \
                and m["p50"]:
            nearby_diff.append({
                "listing": t["listing"], "hood": t["hood"],
                "level_before": m_old["level"], "level_after": m["level"],
                "comp_n_before": m_old["n"], "comp_n_after": m["n"],
                "fair_land_psf_before": round(m_old["p50"], 1),
                "fair_land_psf_after": round(m["p50"], 1),
                "fair_land_before": round(m_old["p50"] * t["lot_sqft"]),
                "fair_land_after": round(m["p50"] * t["lot_sqft"]),
                "delta_cad": round((m["p50"] - m_old["p50"])
                                   * t["lot_sqft"]),
                "fix": "hood alias + target/source ratio corrected"})
        # non-residential or unknown zoning: the comp pool is
        # residential-zoned sales only — evidence class mismatch,
        # keep the estimate but degrade confidence and flag it
        extra = ";".join(x for x in (
            "DATA_GAP" if not t["bca_land"] or not t["zoning"] else "",
            "" if zoning_residential(t["zoning"]) else "NONRESI_ZONING",
            "UNVERIFIED_BIG_COMPS"
            if m["n"] and m.get("n_unverified_big", 0) / m["n"] >= 0.5
            else "")
            if x)
        conf = confidence(m["level"], m["n"], extra)
        # bimodal evidence (e.g. escarpment parcels mixed with normal
        # lots) must not present as high confidence
        if (m["p25"] and m["p75"] and m["p75"] / m["p25"] > 1.4
                and conf == "HIGH_CONFIDENCE"):
            conf = "MEDIUM_CONFIDENCE(HIGH_DISPERSION)"
        # alias position check: aliased community centroid vs listing
        if t["hood_comm"] != t["hood"] and t["hood_comm"] in comm_cent:
            dkm = hav_km(t["lat"], t["lng"], *comm_cent[t["hood_comm"]])
            t["alias_check"] = ("ALIAS_OK" if dkm is not None
                                and dkm <= 2.0
                                else f"ALIAS_REVIEW_{dkm:.1f}km")
        elif t["hood_comm"] != t["hood"]:
            t["alias_check"] = "ALIAS_NO_COMM_CENTROID"
        else:
            t["alias_check"] = "NAME_MATCH"
        fair = (m["p50"] or 0) * t["lot_sqft"] if m["p50"] else None
        bca_impr_share = (1 - t["bca_land"] / t["bca"]) \
            if t["bca"] and t["bca_land"] else None
        utility_flag = ("EXISTING_HOUSE_UTILITY"
                        if bca_impr_share is not None
                        and bca_impr_share > 0.10 else "LAND_DOMINANT")
        land_rows.append({
            "listing": t["listing"], "asking": t["asking"],
            "hood": t["hood"], "hood_comm": t["hood_comm"],
            "side": t["side_key"],
            "lot_sqft": t["lot_sqft"], "frontage": t["frontage"],
            "zoning": t["zoning"], "bca": t["bca"], "bca_land": t["bca_land"],
            "ask_bca_ratio": round(t["asking"] / t["bca"], 3)
            if t["bca"] else "",
            "ask_per_lot_sqft": round(t["asking"] / t["lot_sqft"], 1)
            if t["lot_sqft"] else "",
            "land_comp_level": m["level"], "land_comp_n": m["n"],
            "hood_alias_check": t["alias_check"],
            "land_comp_ids": m["ids"],
            "n_unverified_big": m.get("n_unverified_big", 0),
            "land_weighted_psf":
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
        resi = zoning_residential(t["zoning"])
        for s in t["scenarios"]:
            zflags = list(s["zone_flags"] or [])
            if s["verified"] and not resi:
                # production verified flag assumed a residential zone;
                # blank/C-*/CD/DEOD zoning cannot substantiate that —
                # keep the hypothetical estimate, mark it assumptive
                zflags.append("ZONING_UNVERIFIED_ASSUMED_RESI")
            status = ("VERIFIED" if s["verified"] and resi else
                      "ASSUMPTIVE_ZONING" if s["verified"] else
                      "UNVERIFIED" if s["eligible"] else "INELIGIBLE")
            scen_rows.append({
                "listing": t["listing"], "scenario": s["name"],
                "zoning": t["zoning"], "fsr": s["fsr"],
                "build_sqft": s["build_sqft"],
                "net_saleable": s["net_saleable"], "units": s["units"],
                "eligible": s["eligible"],
                # effective legality: verified AND residential zoning.
                # verified_raw keeps the production flag verbatim —
                # downstream must not read it as legal confirmation
                "verified": "True" if (s["verified"] and resi)
                else "False",
                "verified_raw": "True" if s["verified"] else "False",
                "status": status,
                "zone_flags": ";".join(zflags)})
            if not s["verified"]:
                continue
            nb = match_newbuild(t, s, nb_pools)
            # V1.4.3 diff: what the un-aliased match would have produced
            nb_old = match_newbuild(
                dict(t, hood_comm=t["hood"]), s, nb_pools)
            if (nb_old["level"] != nb["level"]
                    or (nb_old["p50"] or 0) != (nb["p50"] or 0)):
                est_o = (nb_old["p50"] or 0) * s["net_saleable"] \
                    if nb_old["p50"] else None
                est_n = (nb["p50"] or 0) * s["net_saleable"] \
                    if nb["p50"] else None
                newbuild_diff.append({
                    "listing": t["listing"], "scenario": s["name"],
                    "hood": t["hood"], "hood_comm": t["hood_comm"],
                    "alias_check": t["alias_check"],
                    "comp_level_before": nb_old["level"],
                    "comp_level_after": nb["level"],
                    "comp_n_before": nb_old["n"],
                    "comp_n_after": nb["n"],
                    "psf_before": round(nb_old["p50"], 1)
                    if nb_old["p50"] else "",
                    "psf_after": round(nb["p50"], 1)
                    if nb["p50"] else "",
                    "est_sale_before": round(est_o) if est_o else "",
                    "est_sale_after": round(est_n) if est_n else "",
                    "roi_1y_before":
                    round(100 * cost_model(t["asking"], s, est_o,
                                           BASE_YEARS, "EXCL")["roi"], 1)
                    if est_o else "",
                    "roi_1y_after":
                    round(100 * cost_model(t["asking"], s, est_n,
                                           BASE_YEARS, "EXCL")["roi"], 1)
                    if est_n else ""})
            upx = bool(nb.get("unit_price_mode"))
            mult = s["units"] if upx else s["net_saleable"]
            est = nb["p50"] * mult if nb["p50"] else None
            sale_conf = ("LOW_CONFIDENCE(PROXY_UNIT_PRICE,FA_MISSING)"
                         if upx
                         else confidence(nb["level"], nb["n"], extra))
            sale_rows.append({
                "listing": t["listing"], "scenario": s["name"],
                "build_sqft": s["build_sqft"],
                "net_saleable": s["net_saleable"], "units": s["units"],
                "comp_level": nb["level"], "comp_n": nb["n"],
                "comp_ids": nb["ids"], "psf_p25": "" if upx else nb["p25"],
                "psf_p50": "" if upx else nb["p50"],
                "psf_p75": "" if upx else nb["p75"],
                "unit_price_p50": nb["p50"] if upx else "",
                "psf_factor_kind": nb["factor_kind"],
                "sale_value_today": round(est) if est else "",
                "sale_low": round(nb["p25"] * mult) if nb["p25"] else "",
                "sale_high": round(nb["p75"] * mult) if nb["p75"] else "",
                "confidence": sale_conf,
                "basis": "2026-09 normalized comps" +
                (";UNIT_PRICE_X_UNITS,no FA,proxy" if upx else
                 ";PROXY_INDEX" if nb["factor_kind"] == "PROXY" else "")})

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
                    "zoning_caveat": "ASSUMPTIVE_ZONING"
                    if (s["verified"] and not resi) else "",
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
    wcsv(DOCS / "newbuild_alias_fix_diff.csv", newbuild_diff,
         fields=["listing", "scenario", "hood", "hood_comm",
                 "alias_check", "comp_level_before", "comp_level_after",
                 "comp_n_before", "comp_n_after",
                 "psf_before", "psf_after",
                 "est_sale_before", "est_sale_after",
                 "roi_1y_before", "roi_1y_after"])
    wcsv(DOCS / "land_v143_to_v144_diff.csv",
         sorted(v143_diff, key=lambda r: -(abs(r["delta_cad"])
                if isinstance(r["delta_cad"], (int, float)) else 0)),
         fields=["listing", "hood_comm", "lot_sqft",
                 "level_v143", "level_v144",
                 "psf_v143", "psf_v144",
                 "fair_v143", "fair_v144", "delta_cad", "changed"])
    # ---- input manifest (V1.4.4 closure): every pipeline input with
    # sha256 + reproducibility status. A clean clone cannot rerun the
    # full pipeline — only the committed frozen dims and audit CSVs
    # travel with git; normalized inputs regenerate from local raw
    # data; raw/official data stays under local controlled storage.
    import hashlib

    def sha256(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    INPUTS = [
        ("lot_dims_frozen_input", LOT_DIMS_FROZEN, "COMMITTED",
         "BCA dims evidence keys; regenerable by this script when the "
         "raw enriched-spatial json is present locally",
         "pid+sale_date keys can be re-identified to a property via "
         "public records — desensitized but not anonymous"),
        ("listing_snapshot", LISTING_JSON, "LOCAL_CONTROLLED",
         "V3s production output (reports/watchlist, gitignored); "
         "regenerate via production V3s buildable-scenarios run",
         "contains active asking prices and addresses"),
        ("hpi_normalized_per_row",
         V12 / "house_price_normalized_per_row.csv",
         "REGENERABLE", "scripts/house_hpi_normalization_v1_2.py "
         "(needs local raw archive + GVR HPI series)", ""),
        ("attached_proxy_per_row",
         V12 / "attached_proxy_scenarios_per_row.csv",
         "REGENERABLE", "scripts/house_hpi_normalization_v1_2.py", ""),
        ("sales_archive",
         NORM / "sales_archive_v1_0_3/sales_archive.csv",
         "REGENERABLE", "sales archive build (v1.0.3) from local raw "
         "ArcGIS/MLS extracts", ""),
        ("age_recovery_per_row",
         NORM / "housing_age_recovery_v2_4/age_recovery_per_row.csv",
         "REGENERABLE", "housing_age_recovery_v2_4 build from local "
         "BCA/tax sources", ""),
        ("enriched_spatial_raw", LOT_DIMS_PATH, "LOCAL_CONTROLLED",
         "raw enriched ArcGIS+BCA extract (gitignored); optional once "
         "the frozen dims file is committed",
         "full raw extract — do not commit"),
    ] + [("property_tax", ROOT / f"data/research/raw/official/"
                             f"property-tax-{y}-full.csv",
          "LOCAL_CONTROLLED", "City of Vancouver property-tax open "
          "data export", "") for y in range(2023, 2027)]
    wcsv(DOCS / "input_manifest_v1_4.csv",
         [{"input": name, "path": str(p.relative_to(ROOT)),
           "availability": av,
           "present": "True" if p.exists() else "False",
           "sha256": sha256(p) if p.exists() else "",
           "regen_or_source": src, "sensitivity_note": note}
          for name, p, av, src, note in INPUTS],
         fields=["input", "path", "availability", "present", "sha256",
                 "regen_or_source", "sensitivity_note"])

    wcsv(DOCS / "land_lot_compat_fix_diff.csv", lot_fix_diff,
         fields=["listing", "hood_comm", "lot_sqft",
                 "level_before", "level_after",
                 "psf_before", "psf_after",
                 "fair_before", "fair_after", "delta_cad",
                 "ids_before", "ids_after"])
    wcsv(DOCS / "problem_listing_comp_audit.csv", prob_comp,
         fields=["listing", "lot_sqft", "tx", "community",
                 "comp_lot", "lot_ratio", "sale_2026_09",
                 "land_psf", "kept"])
    wcsv(DOCS / "land_nearby_adj_fix_diff.csv", nearby_diff,
         fields=["listing", "hood", "level_before", "level_after",
                 "comp_n_before", "comp_n_after",
                 "fair_land_psf_before", "fair_land_psf_after",
                 "fair_land_before", "fair_land_after", "delta_cad",
                 "fix"])

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
    smap = {(r["listing"], r["scenario"]): r for r in scen_rows}
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
        # V1.4.4: Q5's "verified 方案" gate must exclude assumptive
        # zoning — hypothetical legality cannot claim VERIFIED
        stat = smap.get((k, r["scenario"]), {}).get("status")
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
            "all_gates": "PASS" if (land_ok and roi_ok
                                    and stat == "VERIFIED") else "FAIL",
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
    q5_list = sorted({r["listing"] for r in q5})
    q6 = [r for r in rank2 if r["roi_development_1y"] >= 10
          and "LOW" in r["conf"]]
    q6_list = sorted({r["listing"] for r in q6})

    za_list = sorted({r["listing"] for r in scen_rows
                      if r["status"] == "ASSUMPTIVE_ZONING"})
    mpx_priced = sum(1 for r in sale_rows
                     if r["scenario"] == "MULTIPLEX"
                     and r["sale_value_today"])
    n_changed143 = sum(1 for r in v143_diff if r["changed"] == "CHANGED")
    prob_rows = []
    for addr in ("3466 E 2ND AVENUE", "3281 E 2ND AVENUE",
                 "3939 BOUNDARY ROAD", "817 W 64TH AVENUE",
                 "1534 W 71ST AVENUE", "3151 HEATHER STREET",
                 "1861 E KENT AVENUE NORTH"):
        d = next((r for r in v143_diff if r["listing"] == addr), None)
        if d:
            p43 = f"${d['psf_v143']}/sf" if d["psf_v143"] != "" else "—"
            p44 = f"${d['psf_v144']}/sf" if d["psf_v144"] != "" else "—"
            f43 = f"${d['fair_v143']:,}" if d["fair_v143"] != "" else "—"
            f44 = f"${d['fair_v144']:,}" if d["fair_v144"] != "" else "—"
            prob_rows.append(
                f"| {addr} | {d['level_v143']} | {p43} | {f43} | "
                f"{d['level_v144']} | {p44} | {f44} |")
    prob_tbl = ("\n".join(prob_rows) if prob_rows else "| — | | | | | | |")

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

n_listings={len(q5_list)} / n_scenarios={len(q5)}:
{", ".join(q5_list[:15])}

## Q6 疑似假机会(ROI≥10% 但 LOW_CONFIDENCE)

n_listings={len(q6_list)} / n_scenarios={len(q6)}:
{", ".join(q6_list[:15])}

## V1.4.4 数据核查与修正(2026-10 复审)

**P0 lot 字段血缘修正(根因)**:主池 lot 原取 ArcGIS parcel
SHAPE_Area——该字段可含共有通道/坡地/附属地块,实测
`bca-arcgis-sales-…-enriched-spatial.json` 的
BCA_PROPERTY_INFORMATION_DIMENSIONS(法定面宽×进深)显示同一
parcel 面积普遍虚高 ~2.3×(例:1785 CASSIAR GIS 10,000sf vs
BCA dims 4,254sf)。修正:主池 lot 优先采用 BCA dims(按
pid+sale_date 连接,覆盖 1269/1979 笔,其中 707 笔纠偏>1.5×
记 `LOT_CORRECTED_2X`);无 dims 覆盖且 lot≥6000 的记
`LOT_GIS_UNVERIFIED_BIG`(649 笔),占比过半的匹配结果降级为
LOW 并标 `UNVERIFIED_BIG_COMPS`。逐笔血缘见
`land_pool_lot_audit.csv`。修正后主池 ≥6000sf 成交由 1448 笔
(73.2%)降为 852 笔(43.0%)——原"大地块"多数是面积虚增。

**匹配约束**:LOT_BANDS 细分(6000-8000/8000-12000/12000+),
MICRO_500M/COMM_LOTBAND/COMM_ADJ_BAND/COMM_ANY/NEARBY 各层统一加
lot 比 0.6–1.67× 实际面积约束;p75/p25>1.4 的双峰证据不得给
HIGH_CONFIDENCE。guard 修正前后差异见
`land_lot_compat_fix_diff.csv`({len(lot_fix_diff)} 套),逐笔 comp
见 `problem_listing_comp_audit.csv`。

| 房源 | 前层级 | 前$/sf | 前估值 | 后层级 | 后$/sf | 后估值 |
|---|---|---|---|---|---|---|
{prob_tbl}

**V1.4.3→V1.4.4 全量对照**(dims 纠正+细分 band+面积约束的合并
效应):224 套中 {n_changed143} 套合理地价变化>5%或匹配层级变动;
逐套 before/after 见 `land_v143_to_v144_diff.csv`(旧值用
无 dims 池+V1.4.3 原始 band+无面积约束复现)。

**P1 zoning 核验**:production verified 标志对空 zoning、C-*/CD/
DEOD 也给出 VERIFIED——共 {len(za_list)} 套 {za_list}。
修正:residential zoning 门(R*/F*),不通过者状态
`ASSUMPTIVE_ZONING` + flag `ZONING_UNVERIFIED_ASSUMED_RESI`;
假设测算保留,但 `verified` 输出列写 False(`verified_raw`
保留原始标志仅供溯源),不再显示已核实合法,ROI 行带
zoning_caveat。V3s 生产代码零修改。

**冻结输入与报错**:BCA 尺寸证据以脱敏冻结文件
`lot_dims_frozen_input.csv`(pid+sale_date+lot+frontage+depth,
无地址)随仓库提交;加载顺序 冻结文件→raw enriched-spatial
json(存在则重建冻结文件)→ 两者都缺时 RuntimeError 报错,
不再静默回退未核实 GIS 面积。**复现边界**:干净 clone 只能
读到冻结尺寸与审计产物,无法重跑完整流水线——HPI 归一化、
销售档案、税表、挂牌快照为本地受控输入,逐项状态与 SHA256
见 `input_manifest_v1_4.csv`(COMMITTED/REGENERABLE/
LOCAL_CONTROLLED 三类)。pid+sale_date 可经公开记录反查物业,
属脱敏而非匿名。

**P2 MULTIPLEX 售价**:228 条 MULTIPLEX_UNIT 代理成交单位价有、
floor area 全缺(fa=''),原被整体过滤导致 105 条 MPX 全 BLOCKED。
修正:MPX 池保留 unit_price;$/sf 匹配失败时回退
`500M/COMM/SIDE_UNITPX` 单位价×户数,confidence 一律
`LOW_CONFIDENCE(PROXY_UNIT_PRICE,FA_MISSING)`,与 $/sf 证据分列。
现 {mpx_priced}/105 条 MPX 有代理估值,无依据的售价仍不制造。

## 口径

- fair_land_value = 匹配土地 comps 加权 P50 $/sf × lot_sqft;
  水位 MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→NEARBY_ADJ→SIDE。
  V1.4.2:挂牌 hood→档案社区别名表(HOOD_COMM_ALIAS,按质心距离
  核定,见 land_valuation.hood_comm 列);NEARBY 回退的社区间换算
  已修正为 目标/来源 比值;`land_nearby_adj_fix_diff.csv` 留存
  修正前后差异(106 套原回退房源,现已全部命中社区级或更优匹配)。
- GST 口径:EXCL=售价为未税净价、5% 记成本行;INCL=售价为含税
  总价、成本行记实际汇缴 sale×5/105——两者只扣一次。
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
