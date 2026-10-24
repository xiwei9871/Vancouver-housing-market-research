#!/usr/bin/env python3
"""V1.2 强制回归测试。`python3 scripts/test_house_v1_2.py --run`
覆盖:factor=1 同月/方向/东西区异指数/产品不混指数/
Duplex 不冒充官方/三价独立/不可二次归一化/百分位用调整后价/
缺失 HPI 排除/来源可追溯/双目标可复现/V3s 零修改/双跑幂等。
"""
import csv, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_hpi_normalization_v1_2 as N  # noqa: E402

DOCS = N.DOCS
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")


def main():
    run = "--run" in sys.argv
    idx, hpi_qual = N.load_hpi()

    # T1 同年同月 factor=1
    f = N.factor(idx, "Single Family Detached", "Vancouver East",
                 "2026-09", "2026-09")
    check("same_month_factor_1", f == 1.0)
    f7 = N.factor(idx, "Townhouse", "Vancouver West",
                  "2026-07", "2026-07")
    check("same_month_factor_1_t07", f7 == 1.0)

    # T2 2023 历史价方向正确(下行市场 factor<1)
    f23 = N.factor(idx, "Single Family Detached", "Vancouver East",
                   "2023-09", "2026-09")
    check("declining_market_factor_lt1",
          f23 is not None and f23 < 1.0, str(f23))
    check("detached_east_2023_09_factor_official",
          abs(f23 - 0.861) < 0.01, str(f23))

    # T3 东西区用不同指数
    fe = N.factor(idx, "Single Family Detached", "Vancouver East",
                  "2024-03", "2026-09")
    fw = N.factor(idx, "Single Family Detached", "Vancouver West",
                  "2024-03", "2026-09")
    check("east_west_different_index",
          fe is not None and fw is not None and fe != fw)

    # T4 HOUSE 与 CONDO 不混指数
    mp = {r["product"]: r for r in csv.DictReader(
        open(DOCS / "hpi_product_geography_mapping.csv"))}
    check("house_maps_detached",
          mp["HOUSE"]["hpi_property_type"] == "Single Family Detached")
    check("condo_maps_apartment",
          mp["CONDO"]["hpi_property_type"] == "Apartment")
    fh = N.factor(idx, "Single Family Detached", "Vancouver East",
                  "2023-06", "2026-09")
    fc = N.factor(idx, "Apartment", "Vancouver East",
                  "2023-06", "2026-09")
    check("house_condo_index_not_shared", fh != fc)

    # T5 Duplex 不冒充官方 Townhouse HPI
    check("duplex_half_is_proxy",
          mp["DUPLEX_HALF"]["factor_kind"] == "PROXY" and
          "Townhouse" in mp["DUPLEX_HALF"]["hpi_property_type"])
    check("duplex_proxy_scenarios_present",
          "weak" in mp["DUPLEX_HALF"]["proxy_scenarios"] and
          "strong" in mp["DUPLEX_HALF"]["proxy_scenarios"])
    check("duplex_whole_not_townhouse_primary",
          mp["DUPLEX_WHOLE"]["factor_kind"] == "PROXY" and
          "Detached" in mp["DUPLEX_WHOLE"]["hpi_property_type"])

    # T6 原价/调整后/GST 三列独立
    per = list(csv.DictReader(
        open(N.OUTD / "house_price_normalized_per_row.csv")))
    cols = set(per[0])
    check("three_price_columns",
          {"price", "price_2026_09", "price_2026_07",
           "price_net_gst5", "price_net_gst5_2026_09"} <= cols)
    same = sum(1 for r in per if r["price_2026_09"]
               and float(r["price_2026_09"]) == float(r["price"]))
    check("normalized_differs_from_nominal",
          same <= len(per) * 0.2, f"{same} identical rows")
    gst = [r for r in per if r["price_net_gst5_2026_09"]]
    check("gst_scenario_preserved_independent", len(gst) > 0)
    if gst:
        ok = all(
            abs(float(r["price_net_gst5_2026_09"]) -
                float(r["price_net_gst5"]) *
                float(r["factor_2026_09"]))
            < max(2.0, float(r["price_net_gst5_2026_09"]) * 0.001)
            for r in gst[:500])
        check("gst_scenario_relationship", ok)

    # T7 归一化不可重复执行
    try:
        N.normalize_pool([{"tx": "X", "date": "2024-01-01",
                           "price": 1.0, "price_2026_09": 1.0}],
                         {"X": {"lng": "-123.05"}}, idx, {})
        check("double_normalization_rejected", False)
    except RuntimeError:
        check("double_normalization_rejected", True)

    # T8 百分位基于调整后逐笔价(构造反序样本)
    fake = [
        {"price": 100.0, "fa": 100.0, "psf": 1.0, "tier": "STRICT_AGE",
         "band": "0-1", "kind": "RESALE", "excl": "", "date": "2023-01-15",
         "month": "2023-01", "side": "Vancouver East", "border_tx": "",
         "price_2026_09": 50.0, "psf_2026_09": 0.5, "price_2026_07": 55.0,
         "revision_flag": "", "price_net_gst5": "", "year": 2023,
         "community": "c", "tx": "a", "lot": 1, "strata": "False",
         "zoning": "", "hpi_missing_reason": "", "conflict": "",
         "gst_basis": "", "price_net_gst5_2026_09": "",
         "factor_2026_07": "", "factor_2026_09": "", "factor_kind": "",
         "hpi_product": "", "idx_sale": "", "idx_t07": "", "idx_t09": ""},
        {"price": 200.0, "fa": 100.0, "psf": 2.0, "tier": "STRICT_AGE",
         "band": "0-1", "kind": "RESALE", "excl": "", "date": "2026-09-15",
         "month": "2026-09", "side": "Vancouver East", "border_tx": "",
         "price_2026_09": 400.0, "psf_2026_09": 4.0, "price_2026_07": 390.0,
         "revision_flag": "", "price_net_gst5": "", "year": 2026,
         "community": "c", "tx": "b", "lot": 1, "strata": "False",
         "zoning": "", "hpi_missing_reason": "", "conflict": "",
         "gst_basis": "", "price_net_gst5_2026_09": "",
         "factor_2026_07": "", "factor_2026_09": "", "factor_kind": "",
         "hpi_product": "", "idx_sale": "", "idx_t07": "", "idx_t09": ""},
    ]
    st = N.stat(fake)
    check("percentiles_on_adjusted_prices",
          st["p50"] == 225 and st["p50_nom"] == 150,
          f"p50={st['p50']} p50_nom={st['p50_nom']}")

    # T9 缺失 HPI 不计入归一化中位且计数
    fake2 = fake + [dict(fake[0], tx="c", price_2026_09="",
                         hpi_missing_reason="INDEX_MISSING_SALE_MONTH")]
    st2 = N.stat(fake2)
    check("missing_hpi_excluded_and_counted",
          st2["n_missing_hpi"] == 1 and st2["p50"] == 225 and
          "HPI_MISSING" in st2["quality"])

    # T10 来源/版本可追溯
    hpi_rows = list(csv.DictReader(open(DOCS / "gvr_monthly_hpi_2023_2026.csv")))
    check("hpi_source_url_traced",
          all(r["source_url"] and r["publication_version"]
              for r in hpi_rows if r["quality"] == "DIRECT"))
    check("derived_months_labeled",
          all(r["derived_from"] for r in hpi_rows
              if r["quality"] == "DERIVED_FROM_CHANGE"))
    check("series_complete_45x6",
          len(hpi_rows) == 45 * 3 * 3 and
          not any(r["quality"] == "INDEX_MISSING" for r in hpi_rows))

    # T11 2026-07 与 2026-09 双目标可复现
    fac = list(csv.DictReader(
        open(DOCS / "historical_price_normalization_factors.csv")))
    tg = {r["target_month"] for r in fac if r["factor"]}
    check("both_targets_present", {"2026-07", "2026-09"} <= tg)
    a = [r for r in fac if r["sale_month"] == "2024-06"
         and r["geography"] == "Vancouver East"
         and r["hpi_property_type"] == "Single Family Detached"
         and r["target_month"] == "2026-07"][0]
    i_sale = idx[("2024-06", "Single Family Detached", "Vancouver East")]
    i_t = idx[("2026-07", "Single Family Detached", "Vancouver East")]
    check("factor_2026_07_reproducible",
          abs(float(a["factor"]) - i_t / i_sale) < 1e-4)

    # T12 用户三年系数与官方 3Y change 一致(2023-09→2026-09)
    fe3 = N.factor(idx, "Single Family Detached", "Vancouver East",
                   "2023-09", "2026-09")
    fw3 = N.factor(idx, "Single Family Detached", "Vancouver West",
                   "2023-09", "2026-09")
    check("official_3yr_change_verified",
          abs(fe3 - 0.861) < 0.005 and abs(fw3 - 0.836) < 0.005,
          f"{fe3},{fw3}")

    # T13 覆盖表三年齐 & 官方值锚定
    cov = {r["year"]: r for r in csv.DictReader(
        open(DOCS / "official_sales_coverage_comparison.csv"))}
    check("coverage_2023_1719", cov["2023"]["gvr_east_west_detached_total"]
          == "1719")
    check("coverage_2024_1675", cov["2024"]["gvr_east_west_detached_total"]
          == "1675")
    check("coverage_2025_1410", cov["2025"]["gvr_east_west_detached_total"]
          == "1410")

    # T14 东/西 0-5 归一化中位数存在且东<西
    nb = {r["scope"]: r for r in csv.DictReader(
        open(DOCS / "house_newbuild_normalized_by_community.csv"))
        if r["age_band"] == "0-5" and r["period"] == "ALL"}
    check("east_west_05_medians_present",
          nb["Vancouver East"]["p50"] and nb["Vancouver West"]["p50"])
    check("east_cheaper_than_west",
          float(nb["Vancouver East"]["p50"]) <
          float(nb["Vancouver West"]["p50"]))

    # T14b CONDO 官方归一化完成(同一引擎,Apartment 指数)
    condo = list(csv.DictReader(
        open(N.OUTD / "condo_price_normalized_per_row.csv")))
    check("condo_rows_normalized",
          len(condo) > 10000 and
          all(r["factor_kind"] == "OFFICIAL" for r in condo))
    check("condo_uses_apartment_index",
          all(r["hpi_product"] == "Apartment" for r in condo))
    csum = list(csv.DictReader(
        open(DOCS / "condo_normalized_summary.csv")))
    check("condo_summary_official",
          all(r["factor_kind"] == "OFFICIAL" for r in csum))

    # T14c DUPLEX 仅代理情景,不入官方因子表
    px = list(csv.DictReader(
        open(N.OUTD / "attached_proxy_scenarios_per_row.csv")))
    check("duplex_all_proxy_labeled",
          len(px) > 0 and all(r["factor_kind"] == "PROXY" for r in px))
    check("duplex_three_scenarios",
          all(r["scenario_neutral_index"] and r["scenario_weak_index"]
              and r["scenario_strong_index"] for r in px))
    check("no_duplex_in_official_factors",
          not any("DUPLEX" in r["hpi_property_type"] or
                  "MULTIPLEX" in r["hpi_property_type"] for r in fac))
    check("duplex_scenarios_not_official",
          all(r["factor_kind"] == "PROXY" for r in csv.DictReader(
              open(DOCS / "duplex_proxy_scenarios.csv"))))

    # T14d 产品状态区分
    stt = {r["product"]: r["normalization_status"] for r in csv.DictReader(
        open(DOCS / "normalization_status_by_product.csv"))}
    check("status_house_condo_complete",
          stt["HOUSE"] == "COMPLETE_OFFICIAL" and
          stt["CONDO"] == "COMPLETE_OFFICIAL")
    check("status_duplex_proxy_only",
          stt["DUPLEX_HALF"] == "PROXY_SCENARIOS_ONLY" and
          stt["DUPLEX_WHOLE"] == "PROXY_SCENARIOS_ONLY")
    check("status_townhouse_mapped_only",
          stt["TOWNHOUSE"] == "INDEX_MAPPED_NOT_NORMALIZED")

    # T14e 新旧配对价差(同社区×地块档,不含 ROI)
    prs = list(csv.DictReader(
        open(DOCS / "house_rebuild_spread_pairs.csv")))
    check("spread_pairs_present", len(prs) >= 10)
    check("spread_pairs_positive_direction",
          all(float(r["spread_2026_09"]) > 0 for r in prs))
    check("spread_pairs_no_roi_language",
          all("ROI" not in r["note"].upper() or "非毛利" in r["note"]
              for r in prs))

    # T14f 修订敏感性文件存在并声明非统一版本
    sens_md = (DOCS / "hpi_revision_sensitivity.md").read_text()
    check("revision_sensitivity_doc",
          "不是同一修订版本" in sens_md and "REVISION_SENSITIVE" in sens_md)

    # T15 V3s 零修改(baseline vs HEAD)
    if run:
        base = "a5b9767c0ff0c5fa4ee148787e1c0eb8c94081a9"
        diff = subprocess.run(
            ["git", "diff", "--name-only", base, "HEAD", "--",
             "data/production", "v3s", "scripts/v3s", "reports/v3s",
             "config/v3s", "data/research/v3s"],
            capture_output=True, text=True, cwd=ROOT)
        check("v3s_zero_modification", diff.stdout.strip() == "",
              diff.stdout[:200])

    # T16 双 subprocess 幂等
    if run:
        outs = ["house_price_nominal_vs_2026.csv",
                "house_newbuild_normalized_by_community.csv",
                "historical_price_normalization_factors.csv",
                "normalization_convergence_check.csv",
                "house_rebuild_2000sqft_2026_benchmark.csv"]
        snap = {o: (DOCS / o).read_bytes() for o in outs}
        subprocess.run([sys.executable,
                        str(ROOT / "scripts/house_hpi_normalization_v1_2.py")],
                       check=True, capture_output=True, cwd=ROOT)
        same = all((DOCS / o).read_bytes() == v for o, v in snap.items())
        check("double_run_idempotent", same)

    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
