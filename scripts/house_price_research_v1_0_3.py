#!/usr/bin/env python3
"""HOUSE 六档房龄社区影子价格研究(V1.0.3)。

输入:已归并冻结档案 sales_archive_v1_0_3/sales_archive.csv
输出(GitHub 可推):
- house_prices_by_community_age_year.csv
- house_prices_strict_vs_expanded.csv
- house_price_sensitivity_report.md

三套口径:
  STRICT   pid 产权 + 严格房龄证据(EXACT_PID/FOLIO/UNIT)+
           地上面积已知 + 产品 VERIFIED
  EXPANDED 房龄已知即可(含地址推断/共识)
  SENSITIVITY 对比两组中位价,衡量推断证据对结论的影响
样本 < N_MIN 标 REFERENCE_ONLY 不出价格锚。
研究输出,不写 V3s。
"""
import csv, json, sys, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import archive_pipeline as AP                        # noqa: E402

OUTD = ROOT / "data/research/normalized/sales_archive_v1_0_3"
DOCS = ROOT / "docs/audits/sales_archive_v1_0_3"
DOCS.mkdir(parents=True, exist_ok=True)
N_MIN = 10
STRICT_AGE = set(AP.STRICT_TIERS)

sales = [r for r in csv.DictReader(
    open(OUTD / "sales_archive.csv"))
    if r["product"] == "HOUSE"]

def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pctile(ps, q):
    if not ps:
        return ""
    ps = sorted(ps)
    return round(ps[min(len(ps) - 1, int(q * len(ps)))])


def is_strict(s):
    return bool(s["pid"]) and s["age_tier"] in STRICT_AGE \
        and fnum(s["fa"]) and s["product_verification"] == "VERIFIED"


def is_expanded(s):
    return s["age_band"] not in ("AGE_UNKNOWN",
                                 "AGE_TEMPORAL_CONFLICT")


YEAR_COVERAGE = {
    "2023": "PARTIAL_COVERAGE:last_sale_window_truncated",
    "2024": "PARTIAL_COVERAGE:last_sale_window_truncated",
    "2025": "PARTIAL_COVERAGE:last_sale_truncated_by_2026_resales",
    "2026": "PARTIAL_COVERAGE:cutoff=2026-07-08",
}

groups = defaultdict(list)
for s in sales:
    groups[(s["community"] or "UNMAPPED", s["age_band"],
            s["sale_date"][:4])].append(s)


def estimable_state(band, n_all, n_strict):
    """YES 需严格身份门检;AGE_UNKNOWN 永不可估计。"""
    if band in ("AGE_UNKNOWN", "AGE_TEMPORAL_CONFLICT"):
        return "REFERENCE_ONLY"
    if n_strict >= N_MIN:
        return "YES"
    if n_all >= N_MIN:
        return "EXPANDED_ONLY"
    return "REFERENCE_ONLY"


rows = []
for (comm, band, yr), es in sorted(groups.items()):
    prices = [fnum(e["sale_price"]) for e in es if fnum(e["sale_price"])]
    psf = [fnum(e["sale_price"]) / fnum(e["fa"]) for e in es
           if fnum(e["sale_price"]) and fnum(e["fa"])]
    strict = [e for e in es if is_strict(e)]
    st_prices = [fnum(e["sale_price"]) for e in strict
                 if fnum(e["sale_price"])]
    st_psf = [fnum(e["sale_price"]) / fnum(e["fa"]) for e in strict
              if fnum(e["sale_price"]) and fnum(e["fa"])]
    strata_ct = sum(1 for e in es
                    if str(e["is_strata"]) == "True")
    rows.append({
        "community": comm, "age_band": band, "year": yr,
        "n_tx": len(es),
        "n_properties": len({e["parcel_cluster"] for e in es}),
        "n_units": len({e["unit_cluster"] for e in es}),
        "captured_rows": sum(len(e["raw_ids"].split("|"))
                            for e in es),
        "p25": pctile(prices, .25), "p50": pctile(prices, .5),
        "p75": pctile(prices, .75),
        "psf_median": pctile(psf, .5),
        "fa_known": sum(1 for e in es if fnum(e["fa"])),
        "fa_missing": sum(1 for e in es if not fnum(e["fa"])),
        "first_sale": sum(1 for e in es
                          if e["sale_kind"] == "FIRST_SALE"),
        "resale": sum(1 for e in es if e["sale_kind"] == "RESALE"),
        "sale_kind_unknown": sum(
            1 for e in es if e["sale_kind"] not in
            ("FIRST_SALE", "RESALE")),
        "freehold": len(es) - strata_ct, "strata": strata_ct,
        "age_strict": sum(1 for e in es
                          if e["age_tier"] in STRICT_AGE),
        "age_inferred": sum(
            1 for e in es if e["age_tier"] in
            ("AGE_ADDRESS_INFERRED", "AGE_CONSENSUS_ONLY")),
        "multi_source": sum(1 for e in es if int(e["n_sources"]) > 1),
        "latest_sale": max(e["sale_date"] for e in es),
        "n_strict": len(strict),
        "strict_p50": pctile(st_prices, .5),
        "strict_psf_median": pctile(st_psf, .5),
        "coverage_state": YEAR_COVERAGE.get(yr, ""),
        "estimable": estimable_state(band, len(es),
                                     len(strict))})


def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


wcsv(OUTD / "house_prices_by_community_age_year.csv", rows)

# ---- STRICT vs EXPANDED(社区×房龄全期,年内样本过薄聚合) ----
se = defaultdict(lambda: {"strict_p": [], "exp_p": [],
                          "strict_psf": [], "exp_psf": [],
                          "n_strict": 0, "n_exp": 0})
for s in sales:
    if s["age_band"] in ("AGE_UNKNOWN", "AGE_TEMPORAL_CONFLICT"):
        continue
    p = fnum(s["sale_price"])
    fa = fnum(s["fa"])
    k = (s["community"] or "UNMAPPED", s["age_band"])
    se[k]["n_exp"] += 1
    if p:
        se[k]["exp_p"].append(p)
    if p and fa:
        se[k]["exp_psf"].append(p / fa)
    if is_strict(s):
        se[k]["n_strict"] += 1
        if p:
            se[k]["strict_p"].append(p)
        if p and fa:
            se[k]["strict_psf"].append(p / fa)

srows = []
for (comm, band), d in sorted(se.items()):
    sp50, ep50 = pctile(d["strict_p"], .5), pctile(d["exp_p"], .5)
    spsf, epsf = pctile(d["strict_psf"], .5), \
        pctile(d["exp_psf"], .5)
    delta = (round((ep50 - sp50) / sp50 * 100, 1)
             if sp50 and ep50 else "")
    srows.append({
        "community": comm, "age_band": band,
        "n_strict": d["n_strict"], "n_expanded": d["n_exp"],
        "strict_p50": sp50, "expanded_p50": ep50,
        "delta_pct": delta,
        "strict_psf": spsf, "expanded_psf": epsf,
        "psf_delta_pct": round((epsf - spsf) / spsf * 100, 1)
        if spsf and epsf else "",
        "estimable": "YES" if d["n_strict"] >= N_MIN and
        d["n_exp"] >= N_MIN else "REFERENCE_ONLY"})
wcsv(OUTD / "house_prices_strict_vs_expanded.csv", srows)

# ---- 敏感性报告 ----
estimable = [r for r in srows if r["estimable"] == "YES" and
             r["delta_pct"] != ""]
big = sorted(estimable, key=lambda r: -abs(float(r["delta_pct"])))[:15]
tot_s = sum(r["n_strict"] for r in rows)
tot_e = sum(r["n_tx"] for r in rows
            if r["age_band"] not in ("AGE_UNKNOWN",
                                     "AGE_TEMPORAL_CONFLICT"))
rep = f"""# HOUSE 区域价格敏感性报告(V1.0.3 影子)

生成:{datetime.datetime.now().isoformat(timespec="seconds")}
口径:STRICT = pid + 严格房龄 + 面积已知 + VERIFIED;
EXPANDED = 房龄已知(含推断)。N_MIN={N_MIN}。
本报告仅研究输出,V3s 零修改。

## 规模
- HOUSE canonical:{len(sales)};EXPANDED 可入价:{tot_e};
  STRICT 子集:{tot_s}
- 社区×房龄×年 分组:{len(rows)};STRICT/EXPANDED 对照组:
  {len(srows)}(可估计 {len(estimable)})

## 推断证据对价格的影响(Top |Δ|)
| 社区 | 房龄 | n_strict | n_exp | strict_p50 | expanded_p50 | Δ% |
|---|---|---|---|---|---|---|
""" + "\n".join(
    f"| {r['community']} | {r['age_band']} | {r['n_strict']} | "
    f"{r['n_expanded']} | {r['strict_p50']:,} | "
    f"{r['expanded_p50']:,} | {r['delta_pct']}% |"
    for r in big) + """

## 解读
- Δ%>0 表示含推断样本的中位价更高(推断样本可能含新房龄
  误判/高端物业);Δ%<0 则推断样本拉低锚点。
- |Δ| 大的分组:推断证据在改变价格结论,正式校准只用
  STRICT;|Δ| 小的分组推断影响有限。
- REFERENCE_ONLY 分组保留但不作价格锚。
"""
(DOCS / "house_price_sensitivity_report.md").write_text(rep)
for name in ("house_prices_by_community_age_year.csv",
             "house_prices_strict_vs_expanded.csv"):
    (DOCS / name).write_text(
        (OUTD / name).read_text())
print(f"house={len(sales)} groups={len(rows)} "
      f"se_groups={len(srows)} estimable={len(estimable)}")
