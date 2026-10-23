#!/usr/bin/env python3
"""HOUSE Regional ROI Shadow V1.3 — normalized prices × V3s cost model.

Connects V1.2 2026-09 normalized prices (old-house acquisition proxy vs
standard new-build sale proxy) to the *frozen* V3s rebuild cost model
from scripts/rebuild_value_analysis.py:

    hard      = build_sqft * HOUSE_BUILD_COST + build_sqft * PERMIT_PSQFT
    financing = land * FIN_LAND_PCT + hard * FIN_BUILD_PCT
    selling   = sale * SELLING_PCT
    gst       = sale * GST_PCT            (GST-exclusive sale scenario)
    total     = land + hard + financing + selling + gst
    roi       = (sale - total) / total

This script does NOT implement a second cost model. Items the V3s model
does not carry as separate lines — demolition, DCL/ACC/DCC, purchase
closing/PTT — are reported as NOT_MODELED_IN_V3S. One clearly-labeled
EXTENDED sensitivity row adds BC PTT + a demolition allowance as research
assumptions; it never replaces the V3s figures.

Pools replicate house_rebuild_spread_pairs.csv filters exactly and the
recomputed P50s are asserted against that committed file.

Outputs -> docs/audits/house_regional_roi_v1_3/ (aggregate only; per-row
normalized data stays local under data/research/normalized/).
Run:  python3 scripts/house_regional_roi_shadow_v1_3.py
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path

DOCS = Path("docs/audits/house_regional_roi_v1_3")
DOCS.mkdir(parents=True, exist_ok=True)
V12_NORM = Path("data/research/normalized/house_hpi_normalization_v1_2")
V12_DOCS = Path("docs/audits/house_hpi_normalization_v1_2")

# ---------------------------------------------------------------- V3s model
# Exact constants from scripts/rebuild_value_analysis.py (frozen V3s).
HOUSE_BUILD_COST = 350        # single-detached all-in $/sf — user-set
PERMIT_PSQFT = 40             # design/permits $/sf
SELLING_PCT = 0.04            # commissions/legal % of gross sale
GST_PCT = 0.05                # GST on new construction — cost-side wedge
FIN_LAND_PCT = 0.05 * 2       # land mortgage ~5%/yr × 2yr
FIN_BUILD_PCT = (0.35 * 0.035 + 0.65 * 0.10) * 2   # build capital blend × 2yr

# Standard scenario (task spec): lot 4,000-5,000 sf, above-grade build
# 2,000 sf -> implied FSR 0.40-0.50, inside the 0.70 single-detached
# ceiling V3s assigns to all residential zones.
STD_BUILD_SQFT = 2000
STD_LOT_BAND = "4000-5000"

LOT_BANDS = ((2500, 4000), (4000, 5000), (5000, 8000))

# Extended (non-V3s) research assumptions for ONE labeled sensitivity row.
EXT_DEMOLITION = 50_000       # $ allowance — RESEARCH ASSUMPTION
EXT_PTT_BANDS = ((200_000, 0.01), (2_000_000, 0.02), (math.inf, 0.03))

PRIORITY = ["Hastings-Sunrise", "Grandview-Woodland", "Renfrew(北)",
            "Collingwood(南)", "Kensington-Cedar Cottage", "Riley Park",
            "Dunbar-Southlands", "West Point Grey"]


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100
    f = int(k)
    return vals[f] if f + 1 >= len(vals) else vals[f] + (vals[f + 1] - vals[f]) * (k - f)


def wcsv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


def house_zone_ok(z):
    """Does this zoning permit a single-detached rebuild? V3s zoning_rules
    gives a house FSR for every R*/C* zone; realistically flag only clearly
    non-residential prefixes."""
    z = (z or "").strip().upper()
    if not z:
        return None
    return z.startswith(("R1", "R2", "R3", "R4", "R5", "R6", "R7",
                         "RS", "RT", "RM", "RA", "RR", "FSD", "CD-1"))


def load_pools():
    rows = list(csv.DictReader(
        open(V12_NORM / "house_price_normalized_per_row.csv")))
    pool = [r for r in rows if not r["excl"]
            and r["tier"] in ("STRICT_AGE", "SUPPORTED_AGE")
            and r["band"] not in ("UNKNOWN", "CONFLICT")]
    for r in pool:
        for k in ("fa", "lot", "price", "price_2026_09", "price_2026_07"):
            r[k] = float(r[k]) if r.get(k) not in (None, "") else None
    return pool


def lot_band(lot):
    for lo, hi in LOT_BANDS:
        if lo <= lot < hi:
            return f"{lo}-{hi}"
    return ""


def pools_by_cell(pool):
    """community × lot_band -> {new: rows, old: rows} — same filters as
    house_hpi_normalization_v1_2.spread_pairs."""
    new = [r for r in pool if r["band"] in ("0-1", "2-3", "4-5")
           and r["strata"] != "True"
           and r["fa"] and 1800 <= r["fa"] <= 2200
           and r["lot"] and 2500 <= r["lot"] <= 8000]
    old = [r for r in pool if r["band"] in ("31-50", "51+")
           and r["strata"] != "True"
           and r["lot"] and 2500 <= r["lot"] <= 8000]
    cells = {}
    for r in new:
        cells.setdefault((r["community"], lot_band(r["lot"])), {"new": [], "old": []})["new"].append(r)
    for r in old:
        cells.setdefault((r["community"], lot_band(r["lot"])), {"new": [], "old": []})["old"].append(r)
    return cells


# -------------------------------------------------------------- economics
def ptt(price):
    """BC Property Transfer Tax — EXTENDED assumption, not in V3s."""
    tax, prev = 0.0, 0.0
    for cap, rate in EXT_PTT_BANDS:
        if price > prev:
            tax += (min(price, cap) - prev) * rate
            prev = cap
        else:
            break
    return tax


def project(land, sale, gst_mode="EXCL", build_sqft=STD_BUILD_SQFT,
            ext=False):
    """V3s rebuild economics. gst_mode EXCL: sale is net-of-GST revenue
    and V3s charges gst = sale*5% as a cost line (identical to V3s code).
    INCL: sale price already contains GST -> net revenue = sale/1.05.
    ext adds EXTENDED non-V3s charges (BC PTT + demolition allowance)."""
    hard = build_sqft * HOUSE_BUILD_COST + build_sqft * PERMIT_PSQFT
    fin_land = land * FIN_LAND_PCT
    fin_build = hard * FIN_BUILD_PCT
    financing = fin_land + fin_build
    selling = sale * SELLING_PCT
    if gst_mode == "EXCL":
        revenue = sale
        gst_cost = sale * GST_PCT
    else:                                  # INCL — price contains GST
        revenue = sale / (1 + GST_PCT)
        gst_cost = sale - revenue
    ext_charges = ptt(land) + EXT_DEMOLITION if ext else 0.0
    total = land + hard + financing + selling + gst_cost + ext_charges
    profit = revenue - total
    return {"land": land, "hard": hard, "fin_land": fin_land,
            "fin_build": fin_build, "financing": financing,
            "selling": selling, "gst_cost": gst_cost,
            "ext_charges": ext_charges, "revenue": revenue,
            "total_cost": total, "profit": profit,
            "roi": profit / total if total else None}


def max_land_for_roi(sale, target, gst_mode="EXCL", ext=False):
    """Solve land L for (revenue - C(L))/C(L) = target.
    C(L) = L*(1+FIN_LAND_PCT) + fixed, where fixed covers hard+fin_build+
    selling+gst+ext_ptt-ish (PTT is linear in L but piecewise; solve by
    bisection to stay exact)."""
    def roi_of(L):
        return project(L, sale, gst_mode, ext=ext)["roi"]
    lo, hi = 0.0, 20_000_000.0
    if roi_of(hi) is None or roi_of(hi) > target:
        return None                       # even $20M land hits target — odd
    for _ in range(60):
        mid = (lo + hi) / 2
        if roi_of(mid) is None or roi_of(mid) < target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def irr_approx(land, sale, gst_mode="EXCL", months=24):
    """Unlevered timing approximation (NOT the V3s ROI definition):
    land out t=0, hard spread evenly over months 1..24, revenue less
    selling+gst at month 24. Returns annualized IRR or None."""
    hard = STD_BUILD_SQFT * (HOUSE_BUILD_COST + PERMIT_PSQFT)
    p = project(land, sale, gst_mode)
    flows = [-land] + [-hard / months] * months
    flows[-1] += p["revenue"] - p["selling"] - p["gst_cost"]
    def npv(rm):
        return sum(f / (1 + rm) ** i for i, f in enumerate(flows))
    lo, hi = -0.9, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (1 + (lo + hi) / 2) ** 12 - 1


# ------------------------------------------------------------------ main
def main():
    pool = load_pools()
    cells = pools_by_cell(pool)

    # ---- verify pool identity vs committed V1.2 pairs file
    pair_check = []
    for r in csv.DictReader(open(V12_DOCS / "house_rebuild_spread_pairs.csv")):
        cell = cells.get((r["community"], r["lot_band"]), {"new": [], "old": []})
        n9 = [x["price_2026_09"] for x in cell["new"] if x["price_2026_09"]]
        o9 = [x["price_2026_09"] for x in cell["old"] if x["price_2026_09"]]
        pair_check.append({
            "community": r["community"], "lot_band": r["lot_band"],
            "new_p50_match": abs((pct(n9, 50) or 0) - float(r["new_p50_2026_09"])) < 1,
            "old_p50_match": abs((pct(o9, 50) or 0) - float(r["old_p50_2026_09"])) < 1,
        })

    SCEN = {"conservative": ("25", "75"), "neutral": ("50", "50"),
            "optimistic": ("75", "25")}
    roi_rows, maxp_rows, cost_rows, sens_rows = [], [], [], []

    for (comm, band), cell in sorted(cells.items()):
        if band != STD_LOT_BAND:
            continue                       # standard scenario only
        rn, ro = cell["new"], cell["old"]
        if not rn or not ro:
            continue
        n9 = [x["price_2026_09"] for x in rn if x["price_2026_09"]]
        o9 = [x["price_2026_09"] for x in ro if x["price_2026_09"]]
        if not n9 or not o9:
            continue
        new_q = {p: pct(n9, int(p)) for p in ("25", "50", "75")}
        old_q = {p: pct(o9, int(p)) for p in ("25", "50", "75")}
        thin = "THIN" if len(n9) < 3 or len(o9) < 3 else "OK"
        zshare = [house_zone_ok(x["zoning"]) for x in ro]
        z_yes = sum(1 for v in zshare if v is True)
        z_unk = sum(1 for v in zshare if v is None)
        z_note = ("ZONING_CHECK" if (z_yes + z_unk) and
                  z_yes / (z_yes + z_unk) < 0.8 else "HOUSE_ZONE_OK")
        conf = "LOW_CONFIDENCE" if thin == "THIN" else "MEDIUM_CONFIDENCE"
        if z_note != "HOUSE_ZONE_OK":
            conf = "LOW_CONFIDENCE"

        for scen, (pn, po) in SCEN.items():
            sale, land = new_q[pn], old_q[po]
            for gm in ("EXCL", "INCL"):
                p = project(land, sale, gm)
                roi_rows.append({
                    "community": comm, "lot_band": band,
                    "price_scenario": scen, "gst_scenario": gm,
                    "n_new": len(n9), "n_old": len(o9),
                    "old_p25_2026_09": round(old_q["25"]),
                    "old_p50_2026_09": round(old_q["50"]),
                    "old_p75_2026_09": round(old_q["75"]),
                    "new_p25_2026_09": round(new_q["25"]),
                    "new_p50_2026_09": round(new_q["50"]),
                    "new_p75_2026_09": round(new_q["75"]),
                    "land_price_used": round(land),
                    "sale_price_used": round(sale),
                    "build_sqft": STD_BUILD_SQFT,
                    "hard_cost": round(p["hard"]),
                    "financing": round(p["financing"]),
                    "selling": round(p["selling"]),
                    "gst_cost": round(p["gst_cost"]),
                    "total_cost": round(p["total_cost"]),
                    "profit": round(p["profit"]),
                    "roi_pct": round(100 * p["roi"], 1),
                    "irr_annual_pct": (round(100 * irr_approx(land, sale, gm), 1)
                                       if irr_approx(land, sale, gm) is not None else ""),
                    "zoning_feasibility": z_note,
                    "confidence": conf,
                    "basis": "2026-09 HPI-normalized;V3s-cost-model;SHADOW"})

        # max acceptable acquisition price per target ROI
        for scen, (pn, po) in SCEN.items():
            sale = new_q[pn]
            for target in (0.10, 0.20, 0.30):
                for gm in ("EXCL", "INCL"):
                    ml = max_land_for_roi(sale, target, gm)
                    maxp_rows.append({
                        "community": comm, "lot_band": band,
                        "price_scenario": scen, "gst_scenario": gm,
                        "target_roi_pct": int(target * 100),
                        "sale_price_used": round(sale),
                        "max_acquisition_price": (round(ml) if ml is not None
                                                  and ml >= 0 else
                                                  ("IMPOSSIBLE_EVEN_FREE"
                                                   if ml is not None else "")),
                        "old_p50_2026_09": round(old_q["50"]),
                        "headroom_vs_old_p50":
                            (round(ml - old_q["50"]) if ml is not None else ""),
                        "confidence": conf})

        # cost breakdown @ neutral / both GST
        for gm in ("EXCL", "INCL"):
            p = project(old_q["50"], new_q["50"], gm)
            pext = project(old_q["50"], new_q["50"], gm, ext=True)
            cost_rows.append({
                "community": comm, "lot_band": band, "gst_scenario": gm,
                "land_price_p50": round(old_q["50"]),
                "sale_price_p50": round(new_q["50"]),
                "hard_build_350psf": STD_BUILD_SQFT * HOUSE_BUILD_COST,
                "permit_soft_40psf": STD_BUILD_SQFT * PERMIT_PSQFT,
                "demolition": "NOT_MODELED_IN_V3S",
                "dcl_acc_dcc": "NOT_MODELED_IN_V3S",
                "financing_land": round(p["fin_land"]),
                "financing_build": round(p["fin_build"]),
                "selling_4pct": round(p["selling"]),
                "gst_cost": round(p["gst_cost"]),
                "purchase_ptt_closing": "NOT_MODELED_IN_V3S",
                "total_cost_v3s": round(p["total_cost"]),
                "extended_ptt_assumption": round(ptt(old_q["50"])),
                "extended_demolition_assumption": EXT_DEMOLITION,
                "total_cost_extended": round(pext["total_cost"])})

        # sensitivity grid (neutral prices): hard-cost ±10% / sale ±5% /
        # extended charges — all labeled
        for label, hs, s_m, ext in (
                ("BASE_V3S", 350, 1.0, False),
                ("HARD_-10%_SHADOW", 315, 1.0, False),
                ("HARD_+10%_SHADOW", 385, 1.0, False),
                ("SALE_-5%_FUTURE", 350, 0.95, False),
                ("SALE_+5%_FUTURE", 350, 1.05, False),
                ("EXTENDED_PTT+DEMOL_NOT_V3S", 350, 1.0, True)):
            sale = new_q["50"] * s_m
            hard = STD_BUILD_SQFT * (hs + PERMIT_PSQFT)
            land = old_q["50"]
            gm = "EXCL"
            fin = land * FIN_LAND_PCT + hard * FIN_BUILD_PCT
            selling = sale * SELLING_PCT
            gst = sale * GST_PCT
            exc = ptt(land) + EXT_DEMOLITION if ext else 0
            total = land + hard + fin + selling + gst + exc
            profit = sale - total
            sens_rows.append({
                "community": comm, "lot_band": band, "variant": label,
                "hard_cost": round(hard), "sale_price": round(sale),
                "total_cost": round(total), "profit": round(profit),
                "roi_pct": round(100 * profit / total, 1),
                "note": ("V3s 基准" if label == "BASE_V3S" else
                         "SHADOW/EXTENDED — 非 V3s 口径")})

    order = {c: i for i, c in enumerate(PRIORITY)}
    roi_rows.sort(key=lambda r: (order.get(r["community"], 99),
                                 r["price_scenario"], r["gst_scenario"]))
    maxp_rows.sort(key=lambda r: (order.get(r["community"], 99),
                                  r["price_scenario"], r["target_roi_pct"]))
    wcsv(DOCS / "house_regional_roi_shadow.csv", roi_rows)
    wcsv(DOCS / "house_regional_max_acquisition_price.csv", maxp_rows)
    wcsv(DOCS / "house_regional_cost_breakdown.csv", cost_rows)
    wcsv(DOCS / "house_regional_roi_sensitivity.csv", sens_rows)

    # ------------------------------------------------------------- report
    neut = [r for r in roi_rows if r["price_scenario"] == "neutral"
            and r["gst_scenario"] == "EXCL"]
    rep = """# HOUSE Regional ROI Shadow V1.3 — 报告

归一化价格(2026-09 基准)× **冻结 V3s 成本模型**(`rebuild_value_analysis.py`):
hard = 建面×($350+$40);融资 = 地价×10% + 建安×15.45%;销售费 4%;GST 5%(EXCL
=售价为未税收入、5% 记成本,与 V3s 公式逐项一致;INCL = 售价含 GST,
净收入=售/1.05)。标准件:lot 4,000-5,000sf、地上建面 2,000sf(隐含 FSR
0.40-0.50 ≤ 0.70 独户上限)。**影子测算,不修改 V3s 正式 ROI。**

## V3s 未单列的成本项(诚实披露)

拆除费、DCL/ACC/DCC、购入交易费(PTT/律师)在 V3s 模型中**没有独立行**;
`cost_breakdown` 中记 NOT_MODELED_IN_V3S,仅在 sensitivity 的
EXTENDED_PTT+DEMOL_NOT_V3S 行中作为研究假设出现(PTT 按 BC 阶梯 +
拆除 $50k 假设)。这意味着表内 ROI **系统性偏高**,实际可行社区只会更少。

## 社区中性情景(EXCL,新 P50 卖 / 旧 P50 买)

| 社区 | 旧 P50 | 新 P50 | 总成本 | 净利 | ROI | IRR≈ | 置信 |
|---|---|---|---|---|---|---|---|
"""
    for r in neut:
        rep += (f"| {r['community']} | ${r['land_price_used']:,} | "
                f"${r['sale_price_used']:,} | ${r['total_cost']:,} | "
                f"${r['profit']:,} | {r['roi_pct']}% | "
                f"{r['irr_annual_pct']}% | {r['confidence']} |\n")

    rep += """
## 最高可接受买入价(目标 ROI,EXCL)

`house_regional_max_acquisition_price.csv`;下表为 neutral/EXCL:
"""
    for r in maxp_rows:
        if r["price_scenario"] == "neutral" and r["gst_scenario"] == "EXCL":
            rep += (f"\n- {r['community']} ROI≥{r['target_roi_pct']}%: "
                    f"土地上限 ≈ ${r['max_acquisition_price']:,}"
                    f"(旧 P50 ${r['old_p50_2026_09']:,}, "
                    f"余量 {r['headroom_vs_old_p50']:,})")
    rep += """

## 口径与边界

- 旧房池 = 31+ 年 freehold(土地购入代理);新房池 = 0-5 年 freehold
  fa 1,800-2,200sf(重建出售代理)—— 与 V1.2 spread_pairs 同一过滤器,
  P50 已逐格对账。
- THIN(n<3)= LOW_CONFIDENCE;zoning 非住宅占比高同样降级。
- IRR 为 24 个月等额建安的无杠杆时点近似,标注 APPROX,非 V3s 口径。
- 未来售价涨跌只出现在 sensitivity(SALE_±5%),不改历史归一化价。
- 地块溢价(LWH/面宽/地形)、地下室/车位未拆算 —— 标准件假设。
"""
    (DOCS / "house_regional_roi_v1_3_report.md").write_text(rep)

    files = [p.name for p in sorted(DOCS.iterdir()) if p.is_file()]
    (DOCS / "manifest.json").write_text(json.dumps({
        "version": "house_regional_roi_shadow_v1_3",
        "generated_utc": __import__("datetime").datetime.utcnow()
        .isoformat() + "Z",
        "basis": "V1.2 2026-09 normalized prices; frozen V3s cost model "
                 "(rebuild_value_analysis.py constants); shadow only",
        "deliverables": files,
        "n_roi_cells": len(roi_rows),
        "pair_pool_check": {"cells": len(pair_check),
                            "all_match": all(r["new_p50_match"] and
                                             r["old_p50_match"]
                                             for r in pair_check)},
    }, ensure_ascii=False, indent=2))
    print(f"roi cells {len(roi_rows)}; maxp {len(maxp_rows)}; "
          f"pair_check all_match="
          f"{all(r['new_p50_match'] and r['old_p50_match'] for r in pair_check)}")
    for r in neut:
        print(f"  {r['community']:28s} land ${r['land_price_used']:>9,} "
              f"-> sale ${r['sale_price_used']:>9,} roi {r['roi_pct']}%")


if __name__ == "__main__":
    main()
