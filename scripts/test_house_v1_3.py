#!/usr/bin/env python3
"""V1.3 mandatory tests — ROI shadow layer integrity."""
import csv
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs/audits/house_regional_roi_v1_3"
V12 = ROOT / "docs/audits/house_hpi_normalization_v1_2"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def run_pipeline():
    for s in ("housing_age_recovery_v2_4.py",
              "house_regional_pricing_v1_1.py",
              "house_hpi_normalization_v1_2.py",
              "house_regional_roi_shadow_v1_3.py"):
        r = subprocess.run([sys.executable, f"scripts/{s}"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:])
            raise SystemExit(f"pipeline failed at {s}")


def main():
    if "--run" in sys.argv:
        run_pipeline()

    roi = list(csv.DictReader(open(DOCS / "house_regional_roi_shadow.csv")))
    maxp = list(csv.DictReader(
        open(DOCS / "house_regional_max_acquisition_price.csv")))
    cost = list(csv.DictReader(
        open(DOCS / "house_regional_cost_breakdown.csv")))
    sens = list(csv.DictReader(
        open(DOCS / "house_regional_roi_sensitivity.csv")))
    pairs = list(csv.DictReader(
        open(V12 / "house_rebuild_spread_pairs.csv")))

    # T1 V3s constants in this script must mirror rebuild_value_analysis.py
    src = (ROOT / "scripts/house_regional_roi_shadow_v1_3.py").read_text()
    v3s = (ROOT / "scripts/rebuild_value_analysis.py").read_text()
    for pat in (r"HOUSE_BUILD_COST = 350", r"PERMIT_PSQFT = 40",
                r"SELLING_PCT = 0\.04", r"GST_PCT = 0\.05",
                r"FIN_LAND_PCT = 0\.05 \* 2",
                r"FIN_BUILD_PCT = \(0\.35 \* 0\.035 \+ 0\.65 \* 0\.10\) \* 2"):
        check(f"v3s_const:{pat}", re.search(pat, src) and re.search(pat, v3s))

    # T2 pool identity vs committed V1.2 pairs (4000-5000 cells)
    p45 = {r["community"]: r for r in pairs if r["lot_band"] == "4000-5000"}
    r45 = {r["community"]: r for r in roi
           if r["price_scenario"] == "neutral" and r["gst_scenario"] == "EXCL"}
    check("pool_cells_cover_pairs", set(p45) <= set(r45),
          f"{len(set(p45)&set(r45))}/{len(p45)}")
    check("pool_medians_match_v12",
          all(abs(float(r45[c]["old_p50_2026_09"]) - float(p45[c]["old_p50_2026_09"])) < 1
              and abs(float(r45[c]["new_p50_2026_09"]) - float(p45[c]["new_p50_2026_09"])) < 1
              for c in set(p45) & set(r45)))

    # T3 arithmetic: total = land+hard+fin+selling+gst (+ext)
    check("total_cost_arithmetic",
          all(abs(float(r["total_cost"]) - (float(r["land_price_used"]) +
              float(r["hard_cost"]) + float(r["financing"]) +
              float(r["selling"]) + float(r["gst_cost"]))) < 2 for r in roi))
    check("roi_formula",
          all(abs(float(r["roi_pct"]) -
              100 * float(r["profit"]) / float(r["total_cost"])) < 0.15
              for r in roi))
    # hard = 2000*(350+40); financing = land*0.10 + hard*0.1545
    check("v3s_cost_lines",
          all(float(r["hard_cost"]) == 780000 and
              abs(float(r["financing"]) - (float(r["land_price_used"]) * 0.10
                  + 780000 * 0.1545)) < 2 for r in roi))

    # T4 GST scenarios distinct & both present
    ex = [r for r in roi if r["gst_scenario"] == "EXCL"]
    inc = [r for r in roi if r["gst_scenario"] == "INCL"]
    check("gst_two_scenarios", len(ex) == len(inc) > 0)
    e0 = next(r for r in ex if r["price_scenario"] == "neutral")
    i0 = next(r for r in inc if r["price_scenario"] == "neutral"
            and r["community"] == e0["community"])
    check("gst_incl_differs",
          abs(float(i0["gst_cost"]) - float(e0["gst_cost"])) > 100)

    # T5 scenario ordering: conservative roi <= neutral <= optimistic
    for comm in {r["community"] for r in roi}:
        ro = {r["price_scenario"]: float(r["roi_pct"]) for r in roi
              if r["community"] == comm and r["gst_scenario"] == "EXCL"}
        check(f"scenario_order:{comm}",
              ro["conservative"] <= ro["neutral"] <= ro["optimistic"])

    # T6 max acquisition price monotonic in target ROI; solve identity
    for comm in {r["community"] for r in maxp}:
        ms = [r for r in maxp if r["community"] == comm
              and r["price_scenario"] == "neutral"
              and r["gst_scenario"] == "EXCL"]
        vals = [float(r["max_acquisition_price"]) for r in ms
                if r["max_acquisition_price"]
                not in ("", "IMPOSSIBLE_EVEN_FREE")]
        if len(vals) == 3:
            check(f"maxland_monotonic:{comm}",
                  vals[0] > vals[1] > vals[2])
    # closed-form check: for neutral EXCL, roi at max land == target
    for r in maxp[:6]:
        if r["max_acquisition_price"] in ("", "IMPOSSIBLE_EVEN_FREE"):
            continue
        L = float(r["max_acquisition_price"])
        S = float(r["sale_price_used"])
        hard = 780000
        if r["gst_scenario"] == "EXCL":
            rev, gst = S, S * 0.05
        else:
            rev, gst = S / 1.05, S - S / 1.05
        C = L + hard + L * 0.10 + hard * 0.1545 + S * 0.04 + gst
        check(f"maxland_solves:{r['community']}:{r['target_roi_pct']}:"
              f"{r['gst_scenario']}",
              abs((rev - C) / C - float(r["target_roi_pct"]) / 100) < 0.001)

    # T7 not-modeled items disclosed; extended rows labeled
    check("not_modeled_disclosure",
          all(r["demolition"] == "NOT_MODELED_IN_V3S"
              and r["dcl_acc_dcc"] == "NOT_MODELED_IN_V3S"
              and r["purchase_ptt_closing"] == "NOT_MODELED_IN_V3S"
              for r in cost))
    check("extended_labeled",
          any(r["variant"] == "EXTENDED_PTT+DEMOL_NOT_V3S" and
              "非 V3s" in r["note"] for r in sens))

    # T8 THIN -> LOW_CONFIDENCE; priority communities all present
    check("thin_is_low_conf",
          all(r["confidence"] == "LOW_CONFIDENCE" for r in roi
              if int(r["n_new"]) < 3 or int(r["n_old"]) < 3))
    for c in ("Hastings-Sunrise", "Renfrew(北)", "Collingwood(南)",
              "Kensington-Cedar Cottage", "Riley Park",
              "Dunbar-Southlands", "West Point Grey"):
        check(f"priority:{c}", any(r["community"] == c for r in roi))

    # T9 zoning feasibility flag exists; 2000sf build on 4-5k lot
    check("zoning_flagged",
          all(r["zoning_feasibility"] in
              ("HOUSE_ZONE_OK", "ZONING_CHECK") for r in roi))
    check("std_build_sqft",
          all(r["build_sqft"] == "2000" for r in roi))

    # T10 IRR column present and sanity (< roi_pct is wrong relation? just
    # check it's a number or blank)
    check("irr_present",
          all(r["irr_annual_pct"] == "" or
              -100 < float(r["irr_annual_pct"]) < 500 for r in roi))

    # T11 report + manifest exist; V3s files untouched by this run
    check("report_exists", (DOCS / "house_regional_roi_v1_3_report.md").exists())
    check("manifest_exists", (DOCS / "manifest.json").exists())
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True)
    dirty = [l for l in r.stdout.splitlines()
             if "rebuild_value_analysis.py" in l or "config/research.yaml" in l]
    check("v3s_files_untouched", not dirty, str(dirty))

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(fails)} PASS / {len(fails)} FAIL")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
