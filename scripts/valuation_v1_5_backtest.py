#!/usr/bin/env python3
"""V1.5 sale-price backtest — quantify how wrong each estimator is.

Leave-one-parcel-out (LOPO) over every new-build sale in the HOUSE and
DUPLEX evidence pools: each sale is priced by the remaining sales
(excluding every record on the same parcel_cluster, so duplex sister
halves / repeat sales cannot leak), then actual/estimate is recorded.
A temporal holdout (train on earlier sales, test on later) checks the
chosen method is not tuned to the LOPO sample.

Estimators
  V14      — V1.4.4 match_newbuild verbatim (500M→COMM→SIDE first
             level with n>=3, V3s size curve k=-0.55)
  COMM     — community median $/sf (n>=3) else side median, no size adj
  ANCHOR   — community base (shrunk toward side median, weight K) ×
             nearby correction (comps within R km, relative to their own
             community base, shrunk with K2 and capped ±CAP) × fitted
             size elasticity
Land (separate): V14 match_land vs BCA-calibrated land
  (bca_land_2026 × community median price/bca_land) vs geometric blend.

All inputs are the frozen V1.4 inputs; V3s/V1.4 code is imported, not
modified. Output: docs/audits/listing_valuation_v1_5/backtest_*.csv,
backtest_summary.json (consumed by listing_valuation_v1_5.py).
Run: python3 scripts/valuation_v1_5_backtest.py
"""
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import listing_level_valuation_v1_4 as v  # noqa: E402

OUT = ROOT / "docs/audits/listing_valuation_v1_5"
OUT.mkdir(parents=True, exist_ok=True)
AGE = ROOT / ("data/research/normalized/housing_age_recovery_v2_4/"
              "age_recovery_per_row.csv")

ANCHOR_DEFAULT = {"K": 5, "K2": 6, "R": 0.6, "CAP": 0.15, "k": "fit"}
GRID = [dict(ANCHOR_DEFAULT, **g) for g in (
    {}, {"K": 2}, {"K": 10}, {"K2": 2}, {"K2": 15}, {"R": 0.4},
    {"R": 1.0}, {"CAP": 0.30}, {"CAP": 0.0}, {"k": 0.0}, {"k": -0.55})]
HOLDOUT_SPLIT = {"HOUSE": "2025-07-01", "DUPLEX": "2025-10-01"}


# ============================================================ pools
def load_pools():
    hr = list(csv.DictReader(open(v.V12 / "house_price_normalized_per_row.csv")))
    pr = list(csv.DictReader(open(v.V12 / "attached_proxy_scenarios_per_row.csv")))
    arch = v.load_archive()
    age = {r["canonical_tx"]: r for r in csv.DictReader(open(AGE))}
    pools = v.build_newbuild_pools(hr, pr, arch)
    for name in ("HOUSE", "DUPLEX", "MULTIPLEX"):
        for c in pools[name]:
            a = arch.get(c["tx"], {})
            c["cluster"] = a.get("parcel_cluster") or c["tx"]
            c["date"] = a.get("sale_date", "")[:10]
            c["conflict"] = age.get(c["tx"], {}).get("conflict", "")
    return pools, hr, arch


# ======================================================= estimators
def wmed(pairs, p=50):
    return v.wmedian(pairs, p) if pairs else None


def fit_k(train):
    """pooled within-community log($/sf) ~ log(fa) slope, clipped."""
    by = defaultdict(list)
    for c in train:
        if c["psf"] and c["fa"]:
            by[c["community"]].append((math.log(c["fa"]), math.log(c["psf"])))
    sxy = sxx = 0.0
    for pts in by.values():
        if len(pts) < 3:
            continue
        mx = statistics.mean(p[0] for p in pts)
        my = statistics.mean(p[1] for p in pts)
        sxy += sum((x - mx) * (y - my) for x, y in pts)
        sxx += sum((x - mx) ** 2 for x, _ in pts)
    return min(max(sxy / sxx, -0.6), 0.0) if sxx else 0.0


def est_v14(t, train, name):
    nb = v.match_newbuild(
        {"lat": t["lat"], "lng": t["lng"], "hood_comm": t["community"],
         "side_key": t["side"]},
        {"name": name, "net_saleable": t["area"], "units": 1},
        {name: train})
    return nb["p50"], nb["level"], nb["n"]


def est_comm(t, train):
    c = [x["psf"] for x in train if x["community"] == t["community"]]
    if len(c) >= 3:
        return statistics.median(c), "COMM", len(c)
    s = [x["psf"] for x in train if x["side"] == t["side"]]
    return (statistics.median(s), "SIDE", len(s)) if s else (None, "BLOCKED", 0)


def est_anchor(t, train, k, P=ANCHOR_DEFAULT):
    """Returns (psf, detail dict)."""
    A = t["area"]

    def adj(x):
        return x["psf"] * ((A / x["fa"]) ** k if x["fa"] and A else 1.0)

    by_c, by_s = defaultdict(list), defaultdict(list)
    for x in train:
        a = adj(x)
        by_c[x["community"]].append(a)
        by_s[x["side"]].append(a)
    side_med = {s: statistics.median(l) for s, l in by_s.items()}

    def base_of(comm, side):
        sm = side_med.get(side)
        l = by_c.get(comm, [])
        if sm is None:
            return statistics.median(l) if l else None, len(l)
        if not l:
            return sm, 0
        return (len(l) * statistics.median(l) + P["K"] * sm) / (len(l) + P["K"]), len(l)

    base, n_c = base_of(t["community"], t["side"])
    if base is None:
        return None, {"level": "BLOCKED", "n_comm": 0, "n_near": 0}
    comm_base = {}
    pairs = []
    for x in train:
        d = v.hav_km(t["lat"], t["lng"], x["lat"], x["lng"])
        if d is None or d > P["R"]:
            continue
        key = (x["community"], x["side"])
        if key not in comm_base:
            comm_base[key] = base_of(*key)[0]
        if comm_base[key]:
            pairs.append((adj(x) / comm_base[key], 1 / (1 + d / 0.25)))
    n_near = len(pairs)
    rel = wmed(pairs) if pairs else 1.0
    w = n_near / (n_near + P["K2"]) if P["K2"] else (1.0 if n_near else 0.0)
    f = 1 + w * (rel - 1)
    f = min(max(f, 1 - P["CAP"]), 1 + P["CAP"])
    return base * f, {"level": "ANCHOR_COMM" if n_c >= 3 else "ANCHOR_SIDE",
                      "n_comm": n_c, "n_near": n_near,
                      "base": base, "near_rel": rel, "near_factor": f}


def anchor_k(train, P):
    return fit_k(train) if P["k"] == "fit" else P["k"]


# ========================================================== metrics
def metrics(ratios):
    r = [x for x in ratios if x]
    if not r:
        return {"n": 0}
    # ratio = actual/estimate; APE uses the conventional denominator:
    # |estimate - actual| / actual = |1 - 1/ratio|  (V1.5.1 — V1.5
    # reported |ratio - 1|, i.e. estimate as denominator)
    ape = [abs(1 - 1 / x) for x in r]
    return {"n": len(r),
            "mdape": round(statistics.median(ape), 4),
            "mape": round(statistics.mean(ape), 4),
            "bias_median_ratio": round(statistics.median(r), 4),
            "p10_ratio": round(v.pct(r, 10), 4),
            "p90_ratio": round(v.pct(r, 90), 4),
            "within_10pct": round(sum(a <= 0.10 for a in ape) / len(r), 3),
            "within_20pct": round(sum(a <= 0.20 for a in ape) / len(r), 3),
            "worst_30pct_share": round(sum(a > 0.30 for a in ape) / len(r), 3)}


def target_of(c):
    return {"lat": c["lat"], "lng": c["lng"], "community": c["community"],
            "side": c["side"], "area": c["fa"]}


def clean(pool):
    """drop rows whose new-build age rests on a contested year source"""
    return [c for c in pool if c["conflict"] in ("", "NO_CONFLICT")]


def run_sale_backtest(pools):
    rows, summary = [], {}
    for name in ("HOUSE", "DUPLEX"):
        full = [c for c in pools[name] if c["psf"] and 200 < c["psf"] < 6000
                and c["lat"]]
        for variant, pool in (("ALL", full), ("CLEAN", clean(full))):
            k_fit = fit_k(pool)
            res = defaultdict(list)
            grid_res = defaultdict(list)
            for c in pool:
                train = [x for x in pool if x["cluster"] != c["cluster"]]
                t = target_of(c)
                actual = c["psf"]
                p14, lvl14, n14 = est_v14(t, train, name)
                pc, lvlc, _ = est_comm(t, train)
                kk = fit_k(train)
                pa, det = est_anchor(t, train, kk)
                for m, p in (("V14", p14), ("COMM", pc), ("ANCHOR", pa)):
                    res[m].append(actual / p if p else None)
                    res[f"{m}|{name}"].append(actual / p if p else None)
                res[f"V14@{lvl14.split(':')[0]}"].append(
                    actual / p14 if p14 else None)
                if variant == "ALL":
                    for gi, P in enumerate(GRID):
                        pg, _ = est_anchor(t, train, anchor_k(train, P), P)
                        grid_res[gi].append(actual / pg if pg else None)
                rows.append({
                    "product": name, "variant": variant, "tx": c["tx"],
                    "community": c["community"], "date": c["date"],
                    "fa": round(c["fa"]), "actual_psf": round(actual, 1),
                    "v14_psf": round(p14, 1) if p14 else "",
                    "v14_level": lvl14, "v14_n": n14,
                    "comm_psf": round(pc, 1) if pc else "",
                    "anchor_psf": round(pa, 1) if pa else "",
                    "anchor_level": det["level"],
                    "anchor_n_near": det["n_near"],
                    "conflict": c["conflict"]})
            summary[f"{name}|{variant}"] = {
                "k_fit_full": round(k_fit, 4),
                "methods": {m: metrics(r) for m, r in res.items()
                            if "|" not in m},
                "grid": [{"params": GRID[i], **metrics(r)}
                         for i, r in grid_res.items()]}
        # temporal holdout on CLEAN pool
        pool = clean(full)
        cut = HOLDOUT_SPLIT[name]
        train = [c for c in pool if c["date"] < cut]
        test = [c for c in pool if c["date"] >= cut]
        kk = fit_k(train)
        th = defaultdict(list)
        for c in test:
            t = target_of(c)
            p14 = est_v14(t, train, name)[0]
            pa = est_anchor(t, train, kk)[0]
            pc = est_comm(t, train)[0]
            th["V14"].append(c["psf"] / p14 if p14 else None)
            th["ANCHOR"].append(c["psf"] / pa if pa else None)
            th["COMM"].append(c["psf"] / pc if pc else None)
        summary[f"{name}|TEMPORAL"] = {
            "split": cut, "n_train": len(train), "n_test": len(test),
            "methods": {m: metrics(r) for m, r in th.items()}}
    return rows, summary


# ============================================================ land
def run_land_backtest(hr, arch):
    tax = v.load_tax()
    pool, _, _ = v.build_land_pool(hr, arch, v.load_resolved_yb(), tax,
                                   v.load_lot_dims())
    for r in pool:
        r["bca_land"] = (tax.get(r["pid"], {}).get(2026) or (None,))[0]
        r["cluster"] = arch.get(r["tx"], {}).get("parcel_cluster") or r["tx"]
    pool = [r for r in pool if r["land_psf"] and r["lat"]]
    rows, res = [], defaultdict(list)
    for c in pool:
        train = [x for x in pool if x["cluster"] != c["cluster"]]
        comm_psf = {}
        for x in train:
            comm_psf.setdefault(x["community"], []).append(x["land_psf"])
        comm_psf = {k: statistics.median(l) for k, l in comm_psf.items()}
        t = {"lot_sqft": c["lot"], "hood_comm": c["community"],
             "lat": c["lat"], "lng": c["lng"], "side_key": c["side"]}
        m = v.match_land(t, train, comm_psf)
        p14 = m["p50"] * c["lot"] if m["p50"] else None
        ratios = [x["price_2026_09"] / x["bca_land"] for x in train
                  if x["community"] == c["community"] and x["bca_land"]]
        if len(ratios) < 5:
            ratios = [x["price_2026_09"] / x["bca_land"] for x in train
                      if x["side"] == c["side"] and x["bca_land"]]
        pb = c["bca_land"] * statistics.median(ratios) \
            if c["bca_land"] and ratios else None
        pbl = math.sqrt(p14 * pb) if p14 and pb else (p14 or pb)
        act = c["price_2026_09"]
        for mname, p in (("V14_LAND", p14), ("BCA_CAL", pb), ("BLEND", pbl)):
            res[mname].append(act / p if p else None)
        if p14 and pb and c["date"] >= "2025-07-01":
            # BCA 2026 roll is valued at 2025-07-01: only sales after
            # that date are out-of-sample for the BCA-calibrated method
            for mname, p in (("V14_LAND", p14), ("BCA_CAL", pb),
                             ("BLEND", pbl)):
                res[f"{mname}|post_bca_valdate"].append(act / p)
        if p14 and pb:
            res["V14_LAND|both"].append(act / p14)
            res["BCA_CAL|both"].append(act / pb)
            res["BLEND|both"].append(act / pbl)
        rows.append({"tx": c["tx"], "community": c["community"],
                     "lot": round(c["lot"]), "actual": round(act),
                     "v14_land": round(p14) if p14 else "",
                     "v14_level": m["level"],
                     "bca_cal": round(pb) if pb else "",
                     "blend": round(pbl) if pbl else ""})
    return rows, {m: metrics(r) for m, r in res.items()}


def main():
    pools, hr, arch = load_pools()
    sale_rows, sale_sum = run_sale_backtest(pools)
    v.wcsv(OUT / "backtest_sale_lopo.csv", sale_rows)
    land_rows, land_sum = run_land_backtest(hr, arch)
    v.wcsv(OUT / "backtest_land_lopo.csv", land_rows)
    summary = {"sale": sale_sum, "land": land_sum,
               "protocol": "leave-one-parcel-cluster-out; ratio=actual/"
               "estimate on 2026-09 HPI-normalized $/sf; temporal "
               "holdout on CLEAN pool",
               "sales_cutoff": "~2026-07-08", "hpi_asof": v.HPI_ASOF}
    (OUT / "backtest_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False))
    for key, s in sale_sum.items():
        print(key, {m: (x.get("n"), x.get("mdape"), x.get("p10_ratio"),
                        x.get("p90_ratio"))
                    for m, x in s["methods"].items()})
        for g in s.get("grid", []):
            print("   grid", {k: g["params"][k] for k in "K K2 R CAP k".split()},
                  g["n"], g["mdape"], g.get("within_20pct"))
    print("LAND", {m: (x.get("n"), x.get("mdape"), x.get("bias_median_ratio"))
                   for m, x in land_sum.items()})




# ================================================ V1.5.1 strict tests
def forward_test(pool, name, min_train=40):
    """Expanding window: each sale priced ONLY from sales strictly
    earlier (and not on its parcel). No future information."""
    pool = sorted(pool, key=lambda c: c["date"])
    res = defaultdict(list)
    for c in pool:
        train = [x for x in pool if x["date"] < c["date"]
                 and x["cluster"] != c["cluster"]]
        if len(train) < min_train:
            continue
        t = target_of(c)
        p14 = est_v14(t, train, name)[0]
        pa = est_anchor(t, train, fit_k(train))[0]
        res["V14"].append(c["psf"] / p14 if p14 else None)
        res["ANCHOR"].append(c["psf"] / pa if pa else None)
    return {m: metrics(r) for m, r in res.items()}


def nested_select(pool, name, cut):
    """Pick ANCHOR params by LOPO restricted to the pre-cutoff sample,
    then score the chosen params (and V1.4) once on post-cutoff sales.
    Removes the selection bias of choosing params on the reported set."""
    train = [c for c in pool if c["date"] < cut]
    test = [c for c in pool if c["date"] >= cut]
    inner = []
    for gi, P in enumerate(GRID):
        r = []
        for c in train:
            tr = [x for x in train if x["cluster"] != c["cluster"]]
            p = est_anchor(target_of(c), tr, anchor_k(tr, P), P)[0]
            r.append(c["psf"] / p if p else None)
        inner.append((metrics(r)["mdape"], gi))
    best = min(inner)[1]
    P = GRID[best]
    out = {"V14": [], "ANCHOR_SELECTED": [], "ANCHOR_DEFAULT": []}
    kk = anchor_k(train, P)
    kd = fit_k(train)
    for c in test:
        t = target_of(c)
        p14 = est_v14(t, train, name)[0]
        ps = est_anchor(t, train, kk, P)[0]
        pd_ = est_anchor(t, train, kd)[0]
        out["V14"].append(c["psf"] / p14 if p14 else None)
        out["ANCHOR_SELECTED"].append(c["psf"] / ps if ps else None)
        out["ANCHOR_DEFAULT"].append(c["psf"] / pd_ if pd_ else None)
    return {"cut": cut, "n_train": len(train), "n_test": len(test),
            "selected_params": P, "inner_mdape": dict(
                (str(gi), md) for md, gi in inner),
            "methods": {m: metrics(r) for m, r in out.items()}}


def by_area(rows, product):
    """residual bias/error by unit-area bucket (support diagnostics)"""
    rs = [r for r in rows if r["product"] == product
          and r["variant"] == "ALL" and r["anchor_psf"]]
    fa = sorted(r["fa"] for r in rs)
    q = [v.pct(fa, p) for p in (25, 50, 75, 90, 95)]
    edges = [0] + q + [1e9]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        b = [r["actual_psf"] / r["anchor_psf"] for r in rs
             if lo <= r["fa"] < hi]
        out.append({"lo": round(lo), "hi": round(hi) if hi < 1e9 else None,
                    **metrics(b)})
    return {"fa_p95": round(q[4]), "fa_max": round(fa[-1]), "buckets": out}


def land_asof_test(hr, arch):
    """BCA-calibrated land with publication-date discipline: a sale in
    year Y may only use roll Y (published Jan Y, valued Jul Y-1), and
    the calibration ratio uses only earlier sales on their own roll."""
    tax = v.load_tax()
    pool, _, _ = v.build_land_pool(hr, arch, v.load_resolved_yb(), tax,
                                   v.load_lot_dims())
    pool = [r for r in pool if r["land_psf"] and r["lat"]]
    for r in pool:
        r["cluster"] = arch.get(r["tx"], {}).get("parcel_cluster") or r["tx"]
        yr = int(r["date"][:4])
        r["bl_asof"] = (tax.get(r["pid"], {}).get(yr) or (None,))[0]
    pool.sort(key=lambda r: r["date"])
    res = defaultdict(list)
    for c in pool:
        if not c["bl_asof"]:
            continue
        train = [x for x in pool if x["date"] < c["date"]
                 and x["cluster"] != c["cluster"]]
        if len(train) < 200:
            continue
        rat = [x["price_2026_09"] / x["bl_asof"] for x in train
               if x["community"] == c["community"] and x["bl_asof"]]
        if len(rat) < 5:
            rat = [x["price_2026_09"] / x["bl_asof"] for x in train
                   if x["side"] == c["side"] and x["bl_asof"]]
        pb = c["bl_asof"] * statistics.median(rat) if rat else None
        cp = defaultdict(list)
        for x in train:
            cp[x["community"]].append(x["land_psf"])
        m = v.match_land({"lot_sqft": c["lot"], "hood_comm": c["community"],
                          "lat": c["lat"], "lng": c["lng"],
                          "side_key": c["side"]}, train,
                         {k: statistics.median(l) for k, l in cp.items()})
        p14 = m["p50"] * c["lot"] if m["p50"] else None
        if pb and p14:
            res["BCA_CAL_ASOF"].append(c["price_2026_09"] / pb)
            res["V14_LAND_FWD"].append(c["price_2026_09"] / p14)
    return {m: metrics(r) for m, r in res.items()}


def strict_main():
    pools, hr, arch = load_pools()
    S = json.loads((OUT / "backtest_summary.json").read_text())
    rows = list(csv.DictReader(open(OUT / "backtest_sale_lopo.csv")))
    for r in rows:
        for k in ("fa", "actual_psf", "anchor_psf"):
            r[k] = float(r[k]) if r[k] else None
    strict = {}
    for name in ("HOUSE", "DUPLEX"):
        full = [c for c in pools[name] if c["psf"] and 200 < c["psf"] < 6000
                and c["lat"]]
        strict[name] = {
            "forward_expanding": forward_test(full, name),
            # Devin V1.4.5 protocol: ALL pool, cutoff 2025-07-01
            "devin_protocol_2025_07_01": nested_select(full, name,
                                                       "2025-07-01"),
            "nested_split": nested_select(full, name, HOLDOUT_SPLIT[name]),
            "area_support": by_area(rows, name)}
    strict["LAND"] = land_asof_test(hr, arch)
    S["strict_v1_5_1"] = strict
    S["ape_definition"] = "|estimate-actual|/actual"
    (OUT / "backtest_summary.json").write_text(
        json.dumps(S, indent=1, ensure_ascii=False))
    for k, x in strict.items():
        print(k, json.dumps(x, ensure_ascii=False)[:900])


if __name__ == "__main__":
    main()
    strict_main()
