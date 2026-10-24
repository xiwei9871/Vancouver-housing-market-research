#!/usr/bin/env python3
"""V1.5 independent acceptance recompute — evidence for the review.

Run from the V1.4.5 checkout with the V1.5 worktree mounted at
/tmp/v15 (git worktree add /tmp/v15 f5de744b39701d6beb243035c72b16e4a5170719;
data/research/* + reports/watchlist symlinked from the main checkout).
Outputs docs/audits/v1_5_acceptance_review/recompute_evidence.csv.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

V15 = Path("/tmp/v15")
OUT = Path(__file__).resolve().parent.parent / \
    "docs/audits/v1_5_acceptance_review"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def rc(p):
    return list(csv.DictReader(open(p)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    def add(check, detail, value):
        rows.append({"check": check, "detail": detail, "value": value})

    # ---- extrapolation support -------------------------------------
    bt = rc(V15 / "docs/audits/listing_valuation_v1_5/backtest_sale_lopo.csv")
    h = [r for r in bt if r["product"] == "HOUSE" and r["variant"] == "ALL"]
    fa = [float(r["fa"]) for r in h]
    add("extrapolation", "HOUSE backtest fa max / n>3000 / n>3500",
        f"{max(fa)} / {sum(f > 3000 for f in fa)} / "
        f"{sum(f > 3500 for f in fa)}")
    for r in h:
        if float(r["fa"]) > 3000:
            add("extrapolation_row", f"fa={r['fa']} {r['community']}",
                f"actual {r['actual_psf']} anchor {r['anchor_psf']} "
                f"v14 {r['v14_psf']}")

    # ---- large-build listing estimates ------------------------------
    v15 = rc(V15 / "docs/audits/listing_valuation_v1_5/v15_sale_and_roi.csv")
    for a in ("3141 E BROADWAY", "715 E 63RD AVENUE",
              "1861 E KENT AVENUE NORTH"):
        for r in v15:
            if r["listing"] == a and r["scenario"] == "HOUSE":
                add("large_build_est", a,
                    f"build {r['net_saleable']}sf psf {r['psf_p50']} "
                    f"est {r['sale_p50']} roi {r['roi_p50']}% "
                    f"n_comm {r['n_comm']} n_near {r['n_near']}")

    # ---- ROI attribution 74 -> 122 ---------------------------------
    roi14 = rc(OUT.parent / "listing_level_valuation_v1_4"
               / "active_house_listing_roi.csv")
    s14 = {(r["listing"], r["scenario"]): r for r in
           rc(OUT.parent / "listing_level_valuation_v1_4"
              / "active_house_buildable_scenarios.csv")}
    sal14 = {(r["listing"], r["scenario"]): r for r in
             rc(OUT.parent / "listing_level_valuation_v1_4"
                / "active_house_newbuild_sale_estimate.csv")}
    b14 = {}
    for r in roi14:
        if r["gst_scenario"] != "EXCL":
            continue
        rv = num(r["roi_development_1y"])
        if rv is None or not r["est_sale_today"]:
            continue
        if s14.get((r["listing"], r["scenario"]), {})["eligible"] != "True":
            continue
        a = r["listing"]
        if a not in b14 or rv > b14[a]:
            b14[a] = rv
    b15 = {}
    for r in v15:
        if (r["state"] == "VERIFIED" and r["method"] == "ANCHOR"
                and r["roi_p50"]):
            v = float(r["roi_p50"])
            a = r["listing"]
            if a not in b15 or v > b15[a][0]:
                b15[a] = (v, r["scenario"], r)
    gain, loss = [], []
    for a in set(b14) | set(b15):
        v14x, v15x = b14.get(a, -99), b15.get(a, (None,))[0] or -99
        if v15x >= 10 > v14x:
            gain.append(a)
        if v14x >= 10 > v15x:
            loss.append(a)
    add("roi_attribution", "cross>=10 up/down/net",
        f"{len(gain)} / {len(loss)} / {len(gain) - len(loss)}")
    for a in sorted(gain):
        v, s, r = b15[a]
        s14e = sal14.get((a, s), {})
        add("roi_gain_row", f"{a}|{s}",
            f"roi {b14.get(a)} -> {v}; v14psf {s14e.get('psf_p50')} -> "
            f"v15psf {r['psf_p50']}; est {s14e.get('sale_value_today')}"
            f" -> {r['sale_p50']}")
    for a in sorted(loss):
        cands = [(num(r["roi_p50"]), r["scenario"], r["state"])
                 for r in v15 if r["listing"] == a and r["roi_p50"]]
        cands.sort(key=lambda x: -(x[0] or 0))
        add("roi_loss_row", a,
            f"roi14 {b14[a]:.1f} -> v15 best {cands[0] if cands else None}")

    # ---- P10-P90 grouped coverage ----------------------------------
    band = {"HOUSE": (0.7021, 1.1491), "DUPLEX": (0.8317, 1.218)}
    cov = defaultdict(lambda: [0, 0])
    for r in bt:
        if r["variant"] != "ALL" or not r["anchor_psf"]:
            continue
        ratio = float(r["actual_psf"]) / float(r["anchor_psf"])
        lo, hi = band[r["product"]]
        ok = lo <= ratio <= hi
        for grp in (r["product"], f"comm:{r['community']}",
                    "fa>3000" if float(r["fa"]) > 3000 else
                    "fa2000-3000" if float(r["fa"]) > 2000 else "fa<2000"):
            cov[grp][0] += ok
            cov[grp][1] += 1
    for g, (k, n) in sorted(cov.items()):
        add("p10p90_coverage", g, f"{k}/{n} = {k / n:.0%} (nominal 80%)")

    # ---- rounding boundary ------------------------------------------
    best = {}
    for r in v15:
        if (r["state"] == "VERIFIED" and r["method"] == "ANCHOR"
                and r["roi_p50"]):
            a, v = r["listing"], float(r["roi_p50"])
            if a not in best or v > best[a]:
                best[a] = v
    add("rounding_boundary",
        "listing best-verified roi displayed-1dp >=10 / >=20",
        f"{sum(v >= 10 for v in best.values())} / "
        f"{sum(v >= 20 for v in best.values())} "
        f"(summary json raw: 122 / 59)")
    add("rounding_edge", "best-roi rows displayed exactly 10.0 / 20.0",
        str([(a, v) for a, v in best.items() if v in (10.0, 20.0)]))

    # ---- legal conditional ------------------------------------------
    zc = rc(V15 / "docs/audits/listing_valuation_v1_5/v15_legal_state.csv")
    cond = [r for r in zc if r["state"] == "CONDITIONAL"]
    var = [r for r in cond if r["variance_required"] == "True"]
    add("legal_conditional", "CONDITIONAL total / variance_required",
        f"{len(cond)} / {len(var)}")
    for r in cond:
        add("legal_conditional_row",
            f"{r['listing']}|{r['scenario']}",
            f"zoning={r['zoning'] or 'blank'} var={r['variance_required']}"
            f" vraw={r['verified_raw']} reason={r['reason']}")

    # ---- summary vs committed ----------------------------------------
    summ = json.load(open(V15 / "docs/audits/listing_valuation_v1_5"
                          "/v15_summary.json"))
    add("committed_summary", "roi_ge / price_refreshed",
        f"{summ['roi_ge']} / {summ['price_refreshed']}")

    with open(OUT / "recompute_evidence.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["check", "detail", "value"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} evidence rows -> {OUT}/recompute_evidence.csv")


if __name__ == "__main__":
    main()
