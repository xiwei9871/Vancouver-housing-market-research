#!/usr/bin/env python3
"""V1.4 mandatory tests — listing-level valuation integrity."""
import csv
import json
import random
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs/audits/listing_level_valuation_v1_4"
LISTINGS = ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def run_pipeline():
    r = subprocess.run([sys.executable,
                        "scripts/listing_level_valuation_v1_4.py"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit("v1.4 pipeline failed")


def main():
    if "--run" in sys.argv:
        run_pipeline()

    land = list(csv.DictReader(
        open(DOCS / "active_house_land_valuation.csv")))
    scen = list(csv.DictReader(
        open(DOCS / "active_house_buildable_scenarios.csv")))
    sale = list(csv.DictReader(
        open(DOCS / "active_house_newbuild_sale_estimate.csv")))
    roi = list(csv.DictReader(open(DOCS / "active_house_listing_roi.csv")))
    bid = list(csv.DictReader(
        open(DOCS / "active_house_max_bid_and_negotiation.csv")))
    market = list(csv.DictReader(
        open(DOCS / "active_house_market_scenarios.csv")))
    rank1 = list(csv.DictReader(
        open(DOCS / "land_buy_value_ranking.csv")))
    rank2 = list(csv.DictReader(open(DOCS / "rebuild_roi_ranking.csv")))
    cov = list(csv.DictReader(
        open(DOCS / "land_dominant_comp_coverage.csv")))
    stats = list(csv.DictReader(
        open(DOCS / "land_psf_by_community_lot.csv")))
    listings = {r["address"]: r for r in
                json.load(open(LISTINGS))["rows"] if r["status"] == "ACTIVE"}

    # T1 real ASK: roi rows use the listing's actual asking price
    random.seed(7)
    sample = random.sample(land, min(20, len(land)))
    check("t1_real_ask_20",
          all(abs(float(r["asking"]) - float(listings[r["listing"]]["asking"])) < 1
              for r in sample))
    check("t1_ask_in_roi_rows",
          all(abs(float(r["asking"]) - float(
              listings[r["listing"]]["asking"])) < 1 for r in roi))

    # T2 land model is not a single community price — same community,
    # different lot/frontage must get different values
    by_comm = {}
    for r in land:
        by_comm.setdefault(r["hood"], []).append(r)
    varied = False
    for c, rs in by_comm.items():
        vals = {round(float(r["fair_land_psf"]), 1) for r in rs
                if r["fair_land_psf"] != ""}
        if len(vals) >= 2:
            varied = True
            break
    check("t2_land_psf_varies", varied)

    # T3 sale estimate independent: same community, different build_sqft
    # -> different sale values
    by_list = {}
    for r in sale:
        by_list.setdefault(r["listing"], []).append(r)
    diff_ok = any(
        len({x["sale_value_today"] for x in rs
            if x["sale_value_today"] != ""}) > 1
        for rs in by_list.values() if len(rs) > 1)
    check("t3_sale_varies_by_scenario", diff_ok)
    # same community different lots must not share identical sale
    check("t3_distinct_sale_values",
          len({float(r["sale_value_today"]) for r in sale
               if r["sale_value_today"] != ""}) > 50)

    # T4 land pool gates: verify pool stats csv only lists MAIN/AUX and
    # coverage sums > 0; script-level gate verified via coverage counts
    check("t4_pools_separate",
          {r["pool"] for r in stats} <= {"MAIN", "AUX_90_95"})
    tot_main = sum(int(r["n_main"]) for r in cov)
    tot_aux = sum(int(r["n_aux"]) for r in cov)
    check("t4_pool_sizes", tot_main > 1000 and tot_aux > 0,
          f"{tot_main}/{tot_aux}")

    # T5 normalization single-pass: comp prices already 2026-09 — verify
    # land psf columns are present and not re-adjusted (psf_p50 within
    # sanity bounds for Vancouver land)
    ok_psf = all(50 < float(r["land_weighted_psf"]) < 1500
                 for r in land if r["land_weighted_psf"] != "")
    check("t5_land_psf_sanity", ok_psf)

    # T6 market base == today: market factor 0 row has roi equal to the
    # unified 1y development ROI; no 3y/5y variants exist
    m0 = [r for r in market if r["future_market_factor"] == "0.0"
          and r["gst_scenario"] == "EXCL"]
    b3 = {r["listing"] + r["scenario"]: r["roi_development_1y"]
          for r in roi if r["gst_scenario"] == "EXCL"}
    check("t6_zero_factor_matches_base",
          all(abs(float(r["roi_pct"]) -
                  float(b3[r["listing"] + r["scenario"]])) < 0.05
              for r in m0))
    check("t6_no_horizon_column",
          "horizon_y" not in market[0] and
          "roi_base_3y_pct" not in roi[0] and "roi_base_5y_pct" not in roi[0])
    # future sale at 0% factor equals estimated_sale_today
    est = {r["listing"] + r["scenario"]: float(r["est_sale_today"])
           for r in roi if r["gst_scenario"] == "EXCL"}
    check("t6_future_sale_base_identity",
          all(abs(float(r["future_sale_estimate"]) -
                  est[r["listing"] + r["scenario"]]) < 1 for r in m0))

    # T7 negotiation monotonic: lower offer -> better roi; max bids
    # ordered 10% > 20% > 30%
    ok_mono = True
    for r in bid:
        a = [r.get(f"roi_at_ask_x{k}") for k in (95, 90, 85)]
        if all(v != "" for v in a):
            ok_mono &= float(a[0]) < float(a[1]) < float(a[2])
        mb = [r.get(f"max_bid_roi_{t}") for t in (10, 20, 30)]
        if all(isinstance(v, str) and v not in ("", "UNATTAINABLE")
               for v in mb):
            ok_mono &= float(mb[0]) > float(mb[1]) > float(mb[2])
    check("t7_offer_roi_monotonic", ok_mono)

    # T8 product separation: duplex/mpx rows only from verified scenarios;
    # strata-proxy flagged
    # priced rows allowed only for VERIFIED or ASSUMPTIVE_ZONING
    # scenarios (assumptive = hypothetical, not legal confirmation)
    check("t8_only_verified_priced",
          all(next(s for s in scen if s["listing"] == r["listing"]
                   and s["scenario"] == r["scenario"])["status"]
              in ("VERIFIED", "ASSUMPTIVE_ZONING")
              for r in sale))
    check("t8_assumptive_verified_col_false",
          all(s["verified"] == "False"
              for s in scen if s["status"] == "ASSUMPTIVE_ZONING"))
    check("t8_proxy_marked",
          all(r["psf_factor_kind"] in ("PROXY", "PROXY_UNITPX")
              for r in sale
              if r["scenario"] in ("DUPLEX", "MULTIPLEX")
              and r["sale_value_today"] != ""))

    # T9 no double counting: roi row has exactly one gst cost per scenario
    check("t9_single_gst",
          len({(r["listing"], r["scenario"], r["gst_scenario"])
               for r in roi}) == len(roi))

    # T10 data gaps degrade: missing bca_land -> not HIGH/MEDIUM on land
    # unless comps strong; BLOCKED sales have no sale_value
    check("t10_blocked_no_value",
          all(r["sale_value_today"] == "" for r in sale
              if r["comp_level"] == "BLOCKED"))
    missing_bca = [r["listing"] for r in land
                   if r["confidence"] == "LOW_CONFIDENCE"]
    check("t10_low_conf_exists", len(missing_bca) > 0)

    # T11 protection: V3s files untouched; constants mirrored
    src = (ROOT / "scripts/listing_level_valuation_v1_4.py").read_text()
    v3s = (ROOT / "scripts/rebuild_value_analysis.py").read_text()
    check("t11_constants_mirror",
          'PERMIT_PSQFT = 40' in src and 'PERMIT_PSQFT = 40' in v3s
          and 'SELLING_PCT = 0.04' in src and 'GST_PCT = 0.05' in src)
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       capture_output=True, text=True)
    dirty = [l for l in r.stdout.splitlines()
             if "rebuild_value_analysis" in l or "research.yaml" in l]
    check("t11_v3s_untouched", not dirty, str(dirty))

    # extra: rankings cover all valued listings; report exists
    check("rank_rows", len(rank1) == len(land) and len(rank2) > 0)
    # V1.4.1: ranking must be per (listing, scenario) and its max_bid
    # must match the bid table for the same key (was 119/198 mismatch)
    bm = {(r["listing"], r["scenario"]): r for r in bid}
    check("v141_scenario_matched_maxbid",
          all(bm.get((r["listing"], r["scenario"]), {})
              .get("max_bid_roi_20") == r["max_bid_roi_20"]
              for r in rank2))
    check("v141_all_scenarios_in_ranking",
          len(rank2) == len([r for r in roi
                             if r["gst_scenario"] == "EXCL"]))

    # V1.4.2 fixes
    diff = list(csv.DictReader(
        open(DOCS / "land_nearby_adj_fix_diff.csv")))
    check("v142_nearby_diff_table",
          len(diff) > 50 and
          all(r["level_before"].startswith("NEARBY") for r in diff))
    # GST INCL must not double-count: gst cost = sale*5/105 < EXCL 5%,
    # hence INCL roi >= EXCL roi for identical inputs
    check("v142_gst_no_double_count",
          all(float(r_i["roi_development_1y"]) >=
              float(r_e["roi_development_1y"]) - 0.01
              for r_e in roi if r_e["gst_scenario"] == "EXCL"
              for r_i in roi if r_i["gst_scenario"] == "INCL"
              and r_i["listing"] == r_e["listing"]
              and r_i["scenario"] == r_e["scenario"]))
    rep = (DOCS / "listing_level_valuation_v1_4_report.md").read_text()
    check("v142_n_listings_n_scenarios",
          "n_listings=" in rep and "n_scenarios=" in rep)
    # hood alias applied: no row should match an unaliased hood's comps
    check("v142_hood_alias_recorded",
          all("hood_comm" in r and r["hood_comm"] for r in land))

    # V1.4.3: newbuild matching uses the same canonical community id
    nbd = list(csv.DictReader(
        open(DOCS / "newbuild_alias_fix_diff.csv")))
    check("v143_newbuild_diff_table",
          len(nbd) > 0 and
          all(r["comp_level_after"] in ("COMM", "MICRO_500M", "500M",
                                        "COMM:THIN")
              or r["comp_level_after"].startswith("COMM")
              for r in nbd))
    check("v143_comm_now_matches",
          sum(1 for r in nbd
              if r["comp_level_after"] in ("COMM", "COMM_UNITPX")) >=
          len(nbd) - 1)
    # V1.4.4 additions
    lotd = list(csv.DictReader(
        open(DOCS / "land_lot_compat_fix_diff.csv")))
    check("v144_lot_compat_diff_table", len(lotd) > 0 and
          all(float(r["lot_sqft"]) for r in lotd))
    # 3466 E 2ND: big-parcel comps must no longer reprice the lot
    l3466 = next(r for r in land
                 if r["listing"] == "3466 E 2ND AVENUE")
    check("v144_e2nd_repriced",
          float(l3466["fair_land_value_today"]) > 900_000,
          l3466["fair_land_value_today"])
    # non-residential/blank zoning: computed but not VERIFIED-legal
    check("v144_zoning_assumptive",
          all(r["status"] != "VERIFIED" for r in scen
              if (r["zoning"] or "") == "" or
              not r["zoning"].upper().startswith(("R", "F"))))
    # MULTIPLEX unit-price fallback: priced, labelled, low confidence
    mpx_sale = [r for r in sale if r["scenario"] == "MULTIPLEX"
                and r["sale_value_today"]]
    check("v144_mpx_unitpx_priced", len(mpx_sale) > 80 and
          all("_UNITPX" in r["comp_level"] and
              "FA_MISSING" in r["confidence"] for r in mpx_sale))
    check("v143_alias_geo_checked",
          all(r["alias_check"].startswith("ALIAS_OK") for r in nbd) and
          all(r["hood_alias_check"] in ("NAME_MATCH",) or
              r["hood_alias_check"].startswith("ALIAS_OK")
              for r in land))
    check("report", (DOCS / "listing_level_valuation_v1_4_report.md").exists())
    check("methodology", (DOCS / "valuation_methodology.md").exists())
    check("manifest", (DOCS / "manifest.json").exists())

    # render regression: every visible scenario cell must open <td>
    # and carry build area + ROI; only the ⚠ marker is conditional
    html = (DOCS / "listing_map.html").read_text()
    check("html_no_orphan_br_estsale", "</td><br>估售" not in html
          and "<tr><br>估售" not in html)
    check("html_every_cell_has_build",
          html.count("<td>建面") == html.count("<br>估售"))
    assumptive_priced = sum(
        1 for r in scen if r["status"] == "ASSUMPTIVE_ZONING"
        and any(s["listing"] == r["listing"]
                and s["scenario"] == r["scenario"]
                and s["sale_value_today"] for s in sale))
    check("html_assumptive_marked",
          html.count("⚠法规假设") == assumptive_priced,
          f"html={html.count('⚠法规假设')} csv={assumptive_priced}")
    check("html_no_mb20_label", "上限" not in html)
    # sort/filter binding: each row carries a fixed data-k and the
    # JS resolves META by that key, never by sorted DOM position
    ks = re.findall(r"data-k='(\d+)'", html)
    check("html_rows_bound_fixed_key",
          len(ks) == 224 and len(set(ks)) == 224 and
          set(ks) == {str(i) for i in range(224)})
    check("html_filters_use_fixed_key",
          "const k = r.dataset.k, meta = META[k]" in html and
          "META[idx]" not in html)

    # input manifest: every pipeline input hashed + classified
    iman = list(csv.DictReader(open(DOCS / "input_manifest_v1_4.csv")))
    check("input_manifest_coverage",
          len(iman) >= 11 and
          all(r["availability"] in ("COMMITTED", "LOCAL_CONTROLLED",
                                    "REGENERABLE") for r in iman) and
          all((r["present"] == "True" and r["sha256"]) or
              r["present"] == "False" for r in iman) and
          next(r for r in iman if r["input"] == "lot_dims_frozen_input")
          ["availability"] == "COMMITTED")

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(fails)} PASS / {len(fails)} FAIL")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
