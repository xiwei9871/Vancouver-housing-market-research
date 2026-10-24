#!/usr/bin/env python3
"""V1.5.1 independent acceptance recompute (read-only evidence).

Requires a V1.5.1 worktree at /tmp/v151 (git worktree add /tmp/v151
ca7911d80fbfcc593dea03ea6447807f7b6607df) with data/research/* and
reports/watchlist linked from the main checkout, after having run:
  python3 scripts/valuation_v1_5_backtest.py
  python3 scripts/listing_valuation_v1_5.py
Outputs docs/audits/v1_5_1_acceptance_review/recompute_evidence_v1_5_1.csv
"""
import csv
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

V15 = Path("/tmp/v151")
OUT = Path(__file__).resolve().parent.parent / \
    "docs/audits/v1_5_1_acceptance_review"


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def rc(p):
    return list(csv.DictReader(open(p)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ev = []

    def add(check, detail, value):
        ev.append({"check": check, "detail": detail, "value": value})

    S = json.load(open(V15 / "docs/audits/listing_valuation_v1_5"
                       "/backtest_summary.json"))
    st = S["strict_v1_5_1"]

    # ---- 1. strict forward expanding --------------------------------
    for n in ("HOUSE", "DUPLEX"):
        fw = st[n]["forward_expanding"]
        for m in ("V14", "ANCHOR"):
            add("forward_expanding", f"{n}|{m}", json.dumps(fw[m]))

    # ---- 2. nested selection (train-only params) --------------------
    for n in ("HOUSE", "DUPLEX"):
        for proto in ("devin_protocol_2025_07_01", "nested_split"):
            d = st[n][proto]
            add("nested", f"{n}|{proto}",
                f"train={d['n_train']} test={d['n_test']} "
                f"sel={d['selected_params']}")
            for m, mm in d["methods"].items():
                add("nested_method", f"{n}|{proto}|{m}",
                    f"mdape={mm['mdape']} n={mm['n']}")

    # ---- 3. area support --------------------------------------------
    for n in ("HOUSE", "DUPLEX"):
        a = st[n]["area_support"]
        add("area_support", f"{n} p95/max", f"{a['fa_p95']}/{a['fa_max']}")
        for b in a["buckets"]:
            add("area_bucket", f"{n} {b['lo']}-{b['hi']}",
                f"mdape={b['mdape']} bias={b['bias_median_ratio']} "
                f"n={b['n']}")

    # ---- 4. land as-of ----------------------------------------------
    for m, mm in st["LAND"].items():
        add("land_asof", m, json.dumps(mm))

    # ---- 5. summary vs regenerated vs committed ---------------------
    sm = json.load(open(V15 / "docs/audits/listing_valuation_v1_5"
                        "/v15_summary.json"))
    committed = json.loads(subprocess.run(
        ["git", "-C", str(V15), "show",
         "HEAD:docs/audits/listing_valuation_v1_5/v15_summary.json"],
        capture_output=True, text=True).stdout)
    add("summary_regen", "mine vs committed roi_ge",
        f"{sm['roi_ge']} vs {committed['roi_ge']}")
    add("summary_regen", "price_refreshed mine vs committed",
        f"{sm['price_refreshed']} vs {committed['price_refreshed']}")
    add("bridge_steps", "regenerated", json.dumps(sm["roi_bridge"]))
    add("bridge_steps", "committed", json.dumps(committed["roi_bridge"]))

    # ---- 6. count consistency from displayed CSV --------------------
    rows = rc(V15 / "docs/audits/listing_valuation_v1_5"
              "/v15_sale_and_roi.csv")
    best = {}
    for r in rows:
        if (r["state"] == "VERIFIED" and r["method"] == "ANCHOR"
                and r["roi_p50"] and r["out_of_support"] != "True"):
            a, vv = r["listing"], float(r["roi_p50"])
            if a not in best or vv > best[a]:
                best[a] = vv
    add("csv_count", "displayed-CSV >=10/20/30",
        str([sum(v >= t for v in best.values()) for t in (10, 20, 30)]))

    # ---- 7. out-of-support gate --------------------------------------
    oos = [r for r in rows if r["out_of_support"] == "True"]
    add("oos_gate", "rows / by scenario",
        f"{len(oos)} / {dict(Counter(r['scenario'] for r in oos))}")
    for a in ("3141 E BROADWAY", "715 E 63RD AVENUE",
              "1861 E KENT AVENUE NORTH"):
        for r in rows:
            if r["listing"] == a and r["scenario"] == "HOUSE":
                add("oos_listing", a,
                    f"unit_area={r['unit_area']} est={r['sale_p50']} "
                    f"roi={r['roi_p50']} oos={r['out_of_support']}")

    # ---- 8. legal variance classification ---------------------------
    zc = rc(V15 / "docs/audits/listing_valuation_v1_5"
              "/v15_legal_state.csv")
    vv = [r for r in zc if r["variance_required"] == "True"]
    add("legal_variance", "var=True by (scenario,vraw,state)",
        str(Counter((r["scenario"], r["verified_raw"], r["state"])
                    for r in vv)))
    hv = [r for r in vv if r["verified_raw"] == "True"]
    add("legal_variance", "verified_raw&variance scenario",
        str(Counter(r["scenario"] for r in hv)))

    # ---- 9. bridge rows ----------------------------------------------
    br = rc(V15 / "docs/audits/listing_valuation_v1_5"
            "/v15_roi_bridge.csv")
    add("bridge", "rows", str(len(br)))
    for r in br:
        add("bridge_row", r["listing"], json.dumps(r, ensure_ascii=False))

    with open(OUT / "recompute_evidence_v1_5_1.csv", "w",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=["check", "detail", "value"])
        w.writeheader()
        w.writerows(ev)
    print(f"{len(ev)} evidence rows")


if __name__ == "__main__":
    main()
