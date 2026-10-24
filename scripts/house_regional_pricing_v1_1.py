#!/usr/bin/env python3
"""HOUSE Regional Pricing V1.1 — 分龄区域成交价格研究。

输入:冻结 sales_archive_v1_0_3 + V2.4 房龄恢复层
(resolve_year_built_at_sale 同一套生产函数)。

口径:
- 价格池 = HOUSE,age_tier ∈ STRICT_AGE/SUPPORTED_AGE
  (INFERRED_AGE 单列占比不入中位);UNKNOWN band 排除。
- 互斥年龄档 + 0-3/0-5/0-10 合并;period=2023-26 全部 /
  2025-26 最近(2026 截 2026-07-08)。
- sale_kind UNKNOWN 进总价研究但不入 FIRST_SALE 中位;
  FIRST_SALE 的 GST 未明 → 原价 + 5% 净价情景并列。
- 隔离集:strata HOUSE、desc 含 Laneway/Coach、fa<800
  或 >5000、lot>10000 —— 单列不混入中位。
- 薄样本输出参考价,质量列标 THIN。
V3s 零修改。
"""
import csv, json, sys, datetime
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_newbuild_timeline_gate_v2_1 as V     # noqa: E402
import housing_age_recovery_v2_4 as R            # noqa: E402
import house_newbuild_evidence_pricing_v2_2 as P2  # noqa: E402

DOCS = ROOT / "docs/audits/house_regional_pricing_v1_1"
OUTD = ROOT / "data/research/normalized/house_regional_pricing_v1_1"
CUTOFF_2026 = "2026-07-08"
BANDS_OUT = ["0-1", "2-3", "4-5", "6-10", "11-20", "21-30",
             "31-50", "51+", "UNKNOWN", "CONFLICT"]
PRIORITY1 = ["Hastings-Sunrise", "Grandview-Woodland",
             "Renfrew(北)", "Collingwood(南)",
             "Kensington-Cedar Cottage", "Killarney",
             "Sunset", "Riley Park"]
PRIORITY2 = ["Dunbar-Southlands", "Kitsilano",
             "West Point Grey", "Marpole", "Arbutus Ridge"]
MAIN_TIERS = ("STRICT_AGE", "SUPPORTED_AGE")


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def pct(vals, p):
    """线性插值百分位;n<3 仍返回(调用方标 THIN)。"""
    v = sorted(vals)
    if not v:
        return ""
    k = (len(v) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo))


def stat_block(rows):
    """rows: pool dicts → n/P25/50/75、$/sf、中位 fa/lot、
    STRICT 占比、时间范围、质量提示。"""
    prices = [r["price"] for r in rows]
    psf = [r["psf"] for r in rows if r["psf"]]
    fas = [r["fa"] for r in rows if r["fa"]]
    lots = [r["lot"] for r in rows if r["lot"]]
    strict = sum(1 for r in rows
                 if r["tier"] == "STRICT_AGE")
    n = len(rows)
    qual = []
    if n < 5:
        qual.append("THIN")
    if not psf:
        qual.append("NO_PSF")
    if strict and strict / n < 0.5:
        qual.append("LOW_STRICT_SHARE")
    elif not strict and n:
        qual.append("ALL_SUPPORTED")
    return {"n": n,
            "p25": pct(prices, 25), "p50": pct(prices, 50),
            "p75": pct(prices, 75),
            "psf25": pct(psf, 25), "psf50": pct(psf, 50),
            "psf75": pct(psf, 75),
            "fa_med": pct(fas, 50), "lot_med": pct(lots, 50),
            "strict_share": round(strict / n, 2) if n else 0,
            "date_min": min(r["date"] for r in rows)
                        if rows else "",
            "date_max": max(r["date"] for r in rows)
                        if rows else "",
            "quality": "|".join(qual) or "OK"}


def is_excluded(s):
    """隔离集判定:strata / LWH / fa 异常 / 超大地块。"""
    d = (s.get("bca_desc") or "").lower()
    fa = num(s.get("fa"))
    lot = num(s.get("lot_sqft"))
    if str(s.get("is_strata")) == "True":
        return "STRATA"
    if "laneway" in d or "coach" in d:
        return "LWH"
    if fa is not None and (fa < 800 or fa > 5000):
        return "FA_OUTLIER"
    if lot is not None and lot > 10000:
        return "BIG_LOT"
    return ""


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    OUTD.mkdir(parents=True, exist_ok=True)

    sales = [s for s in csv.DictReader(open(V.IN["archive"]))
             if s["product"] == "HOUSE"]
    census = {r["canonical_tx"]: r for r in
              csv.DictReader(open(V.IN["census"]))}
    traj, plan_fs, pid_fs, jumpy, permit_addr = \
        R.load_evidence()

    # ---------- P0 证据:税卷 year_built 冻结复算 ----------
    yb_audit = Counter()
    for yr in R.TAX_YEARS:
        for t in csv.DictReader(open(
                V.IN[f"tax{yr}"], encoding="utf-8-sig"),
                delimiter=";"):
            yb = V.iyear(t.get("year_built"))
            if yb:
                yb_audit[(yr, "max")] = max(
                    yb_audit.get((yr, "max"), 0), yb)
                if yb >= 2020:
                    yb_audit[(yr, "n_ge2020")] += 1
    V.wcsv(DOCS / "tax_yb_field_audit.csv", [{
        "roll_year": y,
        "max_year_built": yb_audit.get((y, "max"), ""),
        "n_yb_ge2020": yb_audit.get((y, "n_ge2020"), 0)}
        for y in R.TAX_YEARS])

    # ---------- 价格池 ----------
    pool = []
    for s in sales:
        sy = int(s["sale_date"][:4])
        ev = R.build_evidence(s, traj, plan_fs, pid_fs,
                              jumpy, permit_addr, census)
        r = R.resolve_year_built_at_sale(s, ev)
        band = r["band"]
        mb = "0-1" if band in ("0", "1") else band
        kind = s["sale_kind"] if s["sale_kind"] in (
            "FIRST_SALE", "RESALE") else "UNKNOWN"
        fa = num(s["fa"])
        price = num(s["sale_price"])
        lot = num(s["lot_sqft"])
        gst = P2.gst_basis(kind, s.get("sources", ""))
        psf = round(price / fa, 1) if price and fa else None
        pool.append({
            "tx": s["canonical_tx"], "community":
            s["community"] or "?", "date": s["sale_date"],
            "year": sy, "price": price, "kind": kind,
            "tier": r["age_tier"], "band": mb,
            "conflict": r["conflict"], "fa": fa,
            "lot": lot, "strata": s["is_strata"] == "True",
            "zoning": s["zoning"], "excl": is_excluded(s),
            "psf": psf,
            "gst_basis": gst["gst_basis"],
            "price_net_gst5": round(price / 1.05)
            if price and kind == "FIRST_SALE" else ""})
    V.wcsv(OUTD / "house_price_pool_per_row.csv", pool)

    # ---------- P0 抽样审计(分层) ----------
    samples = defaultdict(list)
    for r in pool:
        if r["tier"] != "STRICT_AGE":
            samples[(r["band"], r["tier"],
                     r["year"])].append(r)
    V.wcsv(OUTD / "recovered_sample_audit.csv", [
        {"band": b, "tier": t, "year": y, "n": len(rs),
         "sample_tx": [x["tx"] for x in rs[:3]]}
        for (b, t, y), rs in sorted(samples.items())
        if rs])

    # ---------- 主研究池:排除隔离项+推断层 ----------
    main_pool = [r for r in pool if not r["excl"] and
                 r["tier"] in MAIN_TIERS and
                 r["band"] not in ("UNKNOWN", "CONFLICT")]

    def period(r):
        if r["year"] in (2025, 2026):
            return "2025_26"
        return "2023_24"

    # ---------- D1 主表 community×band×period×kind ----------
    def emit(rows, keys, extra=None):
        out = []
        for kv, grp in sorted(rows.items()):
            st = stat_block(grp)
            row = dict(zip(keys, kv))
            if extra:
                row.update(extra)
            row.update(st)
            out.append(row)
        return out

    g = defaultdict(list)
    for r in main_pool:
        for per in ("ALL", period(r)):
            g[(r["community"], r["band"], per,
               r["kind"])].append(r)
    V.wcsv(DOCS / "house_price_by_community_age.csv",
           emit(g, ("community", "age_band", "period",
                    "sale_kind")))

    g2 = defaultdict(list)
    for r in main_pool:
        g2[(r["community"], r["year"], r["kind"])
           ].append(r)
    V.wcsv(DOCS / "house_price_by_community_year.csv",
           emit(g2, ("community", "year", "sale_kind")))

    # ---------- D2-D4 基准池 ----------
    def bench(bands, name):
        rows = [r for r in main_pool if r["band"] in bands]
        g3 = defaultdict(list)
        for r in rows:
            g3[(r["community"], period(r), r["kind"])
               ].append(r)
        V.wcsv(DOCS / name, emit(
            g3, ("community", "period", "sale_kind")))
        return rows

    nb03 = bench(("0-1", "2-3"),
                 "house_newbuild_0_3_price_benchmark.csv")
    nn410 = bench(("4-5", "6-10"),
                  "house_nearnew_4_10_price_benchmark.csv")
    bench(("11-20", "21-30", "31-50", "51+"),
          "house_old_property_price_benchmark.csv")

    # ---------- D5 面积/地块/时序调整 ----------
    adj = []

    def add_adj(scope, rows, note=""):
        st = stat_block(rows)
        adj.append({"scope": scope, "note": note, **st})

    fa1525 = [r for r in nb03 if r["fa"] and
              1500 <= r["fa"] <= 2500]
    add_adj("newbuild_fa1500_2500", fa1525)
    lot35 = [r for r in nb03 if r["lot"] and
             3000 <= r["lot"] <= 5000]
    add_adj("newbuild_lot3000_5000", lot35)
    add_adj("newbuild_0_3_all", nb03)
    add_adj("nearnew_4_10_all", nn410)
    for per in ("2023_24", "2025_26"):
        add_adj(f"newbuild_0_3_{per}",
                [r for r in nb03 if period(r) == per])
        add_adj(f"nearnew_4_10_{per}",
                [r for r in nn410 if period(r) == per])
    for k in ("FIRST_SALE", "RESALE", "UNKNOWN"):
        add_adj(f"newbuild_0_3_{k}",
                [r for r in nb03 if r["kind"] == k])
    # FIRST_SALE GST 情景:独立行
    fs = [r for r in nb03 if r["kind"] == "FIRST_SALE"]
    prices_adj = [r["price_net_gst5"] for r in fs
                  if r["price_net_gst5"] != ""]
    add_adj("newbuild_0_3_FIRST_SALE_gst5_net",
            [{**r, "price": r["price_net_gst5"],
              "psf": (r["price_net_gst5"] / r["fa"]
                      if r["fa"] else None)}
             for r in fs if r["price_net_gst5"] != ""],
            note="GST5%净价情景,非确认口径")
    # Q2:1800-2200sqft + 地块3000-5000 freehold
    rebuild = [r for r in main_pool
               if r["band"] in ("0-1", "2-3", "4-5")
               and r["fa"] and 1800 <= r["fa"] <= 2200
               and r["lot"] and 3000 <= r["lot"] <= 5000]
    add_adj("rebuild_fa1800_2200_lot3_5k_age0_5", rebuild,
            "Q2 参考:freehold 新房近新")
    for c in PRIORITY1 + PRIORITY2:
        rs = [r for r in rebuild if r["community"] == c]
        if rs:
            add_adj(f"rebuild_1800_2200:{c}", rs)
    V.wcsv(DOCS / "house_price_size_lot_adjustment.csv",
           adj)

    # ---------- 报告 ----------
    def med(rows, k="p50"):
        return stat_block(rows)[k] if rows else ""

    def rowline(label, rows):
        st = stat_block(rows)
        return (f"| {label} | {st['n']} | {st['p25']} | "
                f"{st['p50']} | {st['p75']} | "
                f"{st['psf50']} | {st['fa_med']} | "
                f"{st['quality']} |\n")

    rep = f"""# HOUSE Regional Pricing V1.1

基线 a5b9767;成交截点 {CUTOFF_2026}。池=
HOUSE & STRICT/SUPPORTED & 非隔离项
(strata/LWH/fa异常/超大地块单列)。

## P0 修正
- 税卷 year_built 复算:四年上限均 2022
  (tax_yb_field_audit.csv)——字段冻结属实。
- plan_fs/pid_fs 已独立保留;
  impv 跳涨晚于成交仅标 TEMPORAL_CONFLICT。
- 恢复样本分层抽样:recovered_sample_audit.csv。

## Q1 各社区 0-5 年新 HOUSE(2023-26 合并)
| 社区 | n | P25 | P50 | P75 | $/sf50 | fa中位 | 质量 |
|---|---|---|---|---|---|---|---|
"""
    for c in PRIORITY1 + PRIORITY2:
        rs = [r for r in main_pool if r["community"] == c
              and r["band"] in ("0-1", "2-3", "4-5")]
        if rs:
            rep += rowline(c, rs)
    rep += "\n其他社区:\n\n"
    for c in sorted({r["community"] for r in main_pool}
                    - set(PRIORITY1) - set(PRIORITY2)):
        rs = [r for r in main_pool if r["community"] == c
              and r["band"] in ("0-1", "2-3", "4-5")]
        if rs:
            rep += rowline(c, rs)
    rep += """
## Q1b 最近期 2025-26 vs 2023-24(0-5 年)
| 期 | n | P50 | $/sf50 |
|---|---|---|---|
"""
    st_by_per = {}
    for per in ("2023_24", "2025_26"):
        rs = [r for r in main_pool
              if r["band"] in ("0-1", "2-3", "4-5")
              and period(r) == per]
        st_by_per[per] = stat_block(rs)
        rep += (f"| {per} | {st_by_per[per]['n']} | "
                f"{st_by_per[per]['p50']} | "
                f"{st_by_per[per]['psf50']} |\n")
    a, b = st_by_per["2025_26"], st_by_per["2023_24"]
    if a["p50"] and b["p50"]:
        ratio = a["p50"] / b["p50"]
        rep += f"""
时间调整情景:2025-26 P50 / 2023-24 P50 =
{ratio:.3f}(约 {round((ratio - 1) * 100)}%)。
该差含构成效应(社区/面积组合变化),不仅市场
时点;用于情景时标注 COMPOSITION_MIXED。
"""
    rep += """
## Q2 重建 1800-2200sqft + 地块3000-5000(0-5年)
| 范围 | n | P25 | P50 | P75 | $/sf50 | fa中位 | 质量 |
|---|---|---|---|---|---|---|---|
"""
    rs = [r for r in main_pool
          if r["band"] in ("0-1", "2-3", "4-5")
          and r["fa"] and 1800 <= r["fa"] <= 2200
          and r["lot"] and 3000 <= r["lot"] <= 5000]
    rep += rowline("全市", rs)
    for c in PRIORITY1 + PRIORITY2:
        sub = [r for r in rs if r["community"] == c]
        if sub:
            rep += rowline(c, sub)
    rep += f"""
## 结构性回答
- 0-3 vs 4-10:见两个 benchmark CSV 与
  size_lot_adjustment(newbuild_0_3_all vs
  nearnew_4_10_all)。
- 首销 vs 转售:newbuild_0_3_FIRST_SALE /
  RESALE / UNKNOWN 分列;FIRST_SALE 另有
  gst5_net 情景行(净价=价/1.05,非确认口径)。
- 模型外推警示:fa 过滤后的重建区间是
  样本内插值;小样本社区(THIN)只作参考。
"""
    (DOCS / "house_regional_pricing_v1_1_report.md"
     ).write_text(rep)

    man = {"version": "v1_1",
           "run": datetime.datetime.now().isoformat(),
           "baseline": "a5b9767", "cutoff": CUTOFF_2026,
           "counts": {
               "house_rows": len(pool),
               "main_pool": len(main_pool),
               "excluded": dict(Counter(
                   r["excl"] for r in pool if r["excl"])),
               "nb03": len(nb03), "nn410": len(nn410),
               "rebuild_1800_2200": len(rebuild),
               "tier_dist": dict(Counter(
                   r["tier"] for r in pool))}}
    (DOCS / "manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("done", len(pool), "house rows;",
          len(main_pool), "in main pool")


if __name__ == "__main__":
    main()
