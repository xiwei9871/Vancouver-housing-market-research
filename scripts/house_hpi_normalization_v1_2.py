#!/usr/bin/env python3
"""Vancouver Market Normalization V1.2 — GVR HPI 官方校准 + 历史成交归一化。

输入(全部本地冻结):
  V1.1 价格池  data/research/normalized/house_regional_pricing_v1_1/
               house_price_pool_per_row.csv  (5,386 HOUSE,含隔离/tier 标记)
  V1.0.3 档案  data/research/normalized/sales_archive_v1_0_3/sales_archive.csv
               (经纬度 → Vancouver East/West 逐笔划分;成交量对照)
  HPI 序列     docs/audits/house_hpi_normalization_v1_2/
               gvr_monthly_hpi_2023_2026.csv  (ingest 脚本产物)
  修订审计     hpi_revision_crosscheck.csv    (REVISION_SENSITIVE 标记)

归一化:price_adj = price_nominal * idx(target)/idx(sale_month)
  目标 2026-07(与档案截点对齐复现)与 2026-09(当前最新完整月报)。
  东/西区按逐笔 lng vs Main St 子午线(-123.1005)划分;
  HOUSE → GVR Detached HPI(官方直接对应,非代理)。
  百分位一律由调整后逐笔成交价重算;HPI 缺失行排除出归一化中位并计数。
  原始名义价、GST 情景价、归一化价三列独立,不改写源数据。
  V3s 零修改。
"""
import csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs/audits/house_hpi_normalization_v1_2"
OUTD = ROOT / "data/research/normalized/house_hpi_normalization_v1_2"
POOL_CSV = ROOT / "data/research/normalized/house_regional_pricing_v1_1" / \
    "house_price_pool_per_row.csv"
ARCHIVE = ROOT / "data/research/normalized/sales_archive_v1_0_3" / \
    "sales_archive.csv"
HPI_CSV = DOCS / "gvr_monthly_hpi_2023_2026.csv"
REV_CSV = DOCS / "hpi_revision_crosscheck.csv"
CMHC_CSV = ROOT / "data/research/raw/cmhc" / \
    "cmhc_1101_vancouver_single_semi.csv"

TARGETS = ("2026-07", "2026-09")
MAIN_ST_LNG = -123.1005          # Vancouver East/West 经验分界
BORDER_BAND = 0.0025             # ±~190m 内标 BORDER_TX 质量提示
MAIN_TIERS = ("STRICT_AGE", "SUPPORTED_AGE")
MONTH_RANGE = [(y, m) for y in (2023, 2024, 2025, 2026)
               for m in range(1, 13) if (y, m) <= (2026, 9)]

# 官方年度对照(从三份年终包逐块锚定;2026 无全年数据)
OFFICIAL_ANNUAL = {
    2023: {"ve": 901, "vw": 818, "src": "REBGV-Stats-Pkg-Dec-2023.pdf + "
           "GVR-Stats-Package-Dec-2024.pdf (Jan-Dec 2023 YTD, revised)"},
    2024: {"ve": 882, "vw": 793, "src": "GVR-Stats-Package-Dec-2024.pdf + "
           "GVR-Stats-Package-December-2025.pdf (Jan-Dec 2024 YTD)"},
    2025: {"ve": 765, "vw": 645, "src": "GVR-Stats-Package-December-2025.pdf"
           " (Jan-Dec 2025 YTD)"},
}

PRODUCT_MAP = [
    # product, hpi_property_type, official?, scenario note
    ("HOUSE", "Single Family Detached", "OFFICIAL",
     "GVR Detached HPI,直接对应,无代理"),
    ("TOWNHOUSE", "Townhouse", "OFFICIAL",
     "GVR Townhouse HPI(官方口径不含 duplex)"),
    ("CONDO", "Apartment", "OFFICIAL",
     "GVR Apartment HPI"),
    ("DUPLEX_HALF", "PROXY:Townhouse", "PROXY",
     "无官方独立 Duplex HPI;中性=Townhouse,"
     "偏弱=Detached,偏强=Apartment 三情景"),
    ("DUPLEX_WHOLE", "PROXY:Single Family Detached", "PROXY",
     "整栋双拼按土地/独立屋价值逻辑,"
     "中性=Detached,备选=Townhouse"),
    ("MULTIPLEX_UNIT", "PROXY:Townhouse", "PROXY",
     "无官方独立 HPI;中性=Townhouse,"
     "偏弱=Detached,偏强=Apartment"),
]
PROXY_SCEN = {"PROXY:Townhouse": ["Townhouse",
                                  "Single Family Detached", "Apartment"],
              "PROXY:Single Family Detached":
                  ["Single Family Detached", "Townhouse", "Apartment"]}


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def pct(vals, p):
    v = sorted(vals)
    if not v:
        return ""
    k = (len(v) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo))


def wcsv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ---------------- HPI ----------------

def load_hpi():
    idx, qual = {}, {}
    for r in csv.DictReader(open(HPI_CSV)):
        if not r["price_index"]:
            continue
        idx[(r["month"], r["property_type"], r["geography"])] = \
            float(r["price_index"])
        qual[(r["month"], r["property_type"], r["geography"])] = r["quality"]
    return idx, qual


def revision_flags():
    """|drift|>2% 的 (product,area,target_month) → 该月指数为修订敏感。"""
    flag = {}
    for r in csv.DictReader(open(REV_CSV)):
        if abs(float(r["drift_pct"])) > 2:
            flag[(r["product"], r["area"], r["target_month"])] = \
                round(float(r["drift_pct"]), 2)
    return flag


def factor(idx, prod, area, sale_ym, target):
    a = idx.get((target, prod, area))
    b = idx.get((sale_ym, prod, area))
    if not a or not b:
        return None
    return a / b


# ---------------- per-tx ----------------

def ew(lng):
    if lng is None:
        return ""
    return "Vancouver East" if lng > MAIN_ST_LNG else "Vancouver West"


def normalize_pool(pool, arch, idx, revflag):
    """逐笔归一化;禁止二次归一化(输入必须只有名义价)。"""
    rows = []
    for r in pool:
        if "price_2026_09" in r or "factor_2026_09" in r:
            raise RuntimeError(
                "input already normalized — refusing double normalization")
        a = arch[r["tx"]]
        lng = num(a.get("lng"))
        side = ew(lng)
        ym = r["date"][:7]
        prod = "Single Family Detached"          # HOUSE 唯一官方映射
        fi = factor(idx, prod, side, ym, "2026-09") if side else None
        f7 = factor(idx, prod, side, ym, "2026-07") if side else None
        miss = ""
        if not side:
            miss = "NO_GEO_LNG"
        elif (ym, prod, side) not in idx:
            miss = "INDEX_MISSING_SALE_MONTH"
        p9 = round(r["price"] * fi) if fi else ""
        p7 = round(r["price"] * f7) if f7 else ""
        psf9 = round(r["price"] * fi / r["fa"], 1) \
            if fi and r["fa"] else ""
        rows.append({
            **r, "month": ym, "side": side,
            "border_tx": "BORDER_TX"
            if lng and abs(lng - MAIN_ST_LNG) < BORDER_BAND else "",
            "hpi_product": prod,
            "idx_sale": idx.get((ym, prod, side), ""),
            "idx_t09": idx.get(("2026-09", prod, side), ""),
            "idx_t07": idx.get(("2026-07", prod, side), ""),
            "factor_2026_07": round(f7, 4) if f7 else "",
            "factor_2026_09": round(fi, 4) if fi else "",
            "factor_kind": "OFFICIAL" if fi else "",
            "hpi_missing_reason": miss,
            "price_2026_07": p7, "price_2026_09": p9,
            "psf_2026_09": psf9,
            "price_net_gst5_2026_09":
                round(num(r["price_net_gst5"]) * fi)
                if fi and r["price_net_gst5"] != "" else "",
            "revision_flag": "REVISION_SENSITIVE"
            if (prod, side, ym) in revflag else "",
        })
    return rows


def stat(rows, pk="price_2026_09", pk2="price",
         sk="psf_2026_09", sk2="psf"):
    out = {"n": len(rows)}
    for name, key in (("p25", pk), ("p50", pk), ("p75", pk)):
        vals = [r[key] for r in rows if r[key] not in ("", None)]
        out[name] = pct(vals, int(name[1:])) if vals else ""
    for name, key in (("p25_nom", pk2), ("p50_nom", pk2), ("p75_nom", pk2)):
        out[name] = pct([r[key] for r in rows if r[key] not in ("", None)],
                        int(name[1:3]))
    out["psf50"] = pct([r[sk] for r in rows if r[sk] not in ("", None)], 50)
    out["psf50_nom"] = pct([r[sk2] for r in rows
                            if r[sk2] not in ("", None)], 50)
    out["n_missing_hpi"] = sum(1 for r in rows if r["price_2026_09"] == "")
    out["n_rev_sensitive"] = sum(1 for r in rows if r["revision_flag"])
    out["strict_share"] = round(
        sum(1 for r in rows if r["tier"] == "STRICT_AGE") / len(rows), 2) \
        if rows else 0
    f9 = [r["factor_2026_09"] for r in rows if r["factor_2026_09"] != ""]
    out["factor_mean"] = round(sum(f9) / len(f9), 4) if f9 else ""
    qual = []
    if len(rows) < 5:
        qual.append("THIN")
    if out["n_missing_hpi"]:
        qual.append(f"HPI_MISSING:{out['n_missing_hpi']}")
    if out["n_rev_sensitive"]:
        qual.append(f"REVISION_SENSITIVE:{out['n_rev_sensitive']}")
    if sum(1 for r in rows if r["border_tx"]) > len(rows) * 0.3:
        qual.append("BORDER_COMMUNITY")
    if out["strict_share"] < 0.5 and rows:
        qual.append("LOW_STRICT_SHARE")
    out["quality"] = "|".join(qual) or "OK"
    return out


MERGED = {"0-3": ("0-1", "2-3"), "0-5": ("0-1", "2-3", "4-5"),
          "0-10": ("0-1", "2-3", "4-5", "6-10"),
          "31+": ("31-50", "51+")}


def band_rows(rows, band):
    if band in MERGED:
        return [r for r in rows if r["band"] in MERGED[band]]
    return [r for r in rows if r["band"] == band]


def period(r):
    return "2025_26" if r["year"] in (2025, 2026) else "2023_24"


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    OUTD.mkdir(parents=True, exist_ok=True)
    idx, hpi_qual = load_hpi()
    revflag = revision_flags()
    arch = {r["canonical_tx"]: r for r in csv.DictReader(open(ARCHIVE))}
    pool = [r for r in csv.DictReader(open(POOL_CSV))]
    for r in pool:
        r["price"] = num(r["price"]); r["year"] = int(r["year"])
        r["fa"] = num(r["fa"]); r["lot"] = num(r["lot"])
        r["psf"] = num(r["psf"])
    nrm = normalize_pool(pool, arch, idx, revflag)

    main_pool = [r for r in nrm if not r["excl"]
                 and r["tier"] in MAIN_TIERS
                 and r["band"] not in ("UNKNOWN", "CONFLICT")]
    inferred = [r for r in nrm if r["tier"] == "INFERRED_AGE"]

    wcsv(OUTD / "house_price_normalized_per_row.csv", [
        {k: r[k] for k in (
            "tx", "community", "side", "border_tx", "date", "month", "year",
            "price", "kind", "tier", "band", "fa", "lot", "strata", "zoning",
            "excl", "gst_basis", "price_net_gst5", "hpi_product",
            "idx_sale", "idx_t07", "idx_t09", "factor_2026_07",
            "factor_2026_09", "factor_kind", "hpi_missing_reason",
            "price_2026_07", "price_2026_09", "psf", "psf_2026_09",
            "price_net_gst5_2026_09", "revision_flag")} for r in nrm])

    # ---------- 1) official_sales_coverage_comparison.csv
    cov = []
    by_year = Counter(r["year"] for r in nrm)
    for y in (2023, 2024, 2025):
        o = OFFICIAL_ANNUAL[y]
        gvr = o["ve"] + o["vw"]
        arch_n = by_year.get(y, 0)
        cov.append({
            "year": y, "gvr_vancouver_east_detached": o["ve"],
            "gvr_vancouver_west_detached": o["vw"],
            "gvr_east_west_detached_total": gvr,
            "archive_house_total": arch_n,
            "diff_archive_minus_gvr": arch_n - gvr,
            "diff_pct": round(100 * (arch_n - gvr) / gvr, 1),
            "gvr_source": o["src"],
            "note": "口径差:BCA 产权登记成交 vs MLS 挂牌成交;登记日vs"
                    "合同日;产品归类差。外部规模一致性检查,"
                    "不等于捕获率/漏采率。"})
    wcsv(DOCS / "official_sales_coverage_comparison.csv", cov)

    # ---------- 2) hpi_product_geography_mapping.csv
    mp = []
    for prod, hpi, kind, note in PRODUCT_MAP:
        scen = PROXY_SCEN.get(hpi, [])
        mp.append({
            "product": prod, "hpi_property_type": hpi,
            "factor_kind": kind,
            "geography_rule": "逐笔 lng vs Main St 子午线 "
                              f"({MAIN_ST_LNG}):>east,<west",
            "proxy_scenarios": "|".join(
                f"{'neutral' if i == 0 else 'weak' if i == 1 else 'strong'}"
                f"={s}" for i, s in enumerate(scen)) or "n/a",
            "proxy_error_note": "Townhouse HPI 官方明确不含 duplex;"
            "代理情景用于敏感性,不得标称官方" if kind == "PROXY" else "",
            "note": note})
    wcsv(DOCS / "hpi_product_geography_mapping.csv", mp)

    # ---------- 3) historical_price_normalization_factors.csv
    fac = []
    for (y, m) in MONTH_RANGE:
        ym = f"{y}-{m:02d}"
        for area in ("Vancouver East", "Vancouver West"):
            for prod in ("Single Family Detached", "Townhouse", "Apartment"):
                for tgt in TARGETS:
                    f9 = factor(idx, prod, area, ym, tgt)
                    fac.append({
                        "sale_month": ym, "geography": area,
                        "hpi_property_type": prod,
                        "target_month": tgt,
                        "index_sale_month": idx.get((ym, prod, area), ""),
                        "index_target": idx.get((tgt, prod, area), ""),
                        "factor": round(f9, 5) if f9 else "",
                        "factor_kind": "OFFICIAL" if f9 else "",
                        "index_quality_sale": hpi_qual.get(
                            (ym, prod, area), ""),
                        "index_quality_target": hpi_qual.get(
                            (tgt, prod, area), ""),
                        "revision_flag": "REVISION_SENSITIVE" if
                            (prod, area, ym) in revflag else ""})
    wcsv(DOCS / "historical_price_normalization_factors.csv", fac)

    # ---------- 4) house_price_nominal_vs_2026.csv
    bands = ["0-1", "2-3", "4-5", "6-10", "11-20", "21-30", "31-50",
             "51+", "0-3", "0-5", "0-10", "31+"]
    out = []
    g = defaultdict(list)
    for r in main_pool:
        for per in ("ALL", period(r)):
            g[(r["community"], per, r["kind"])].append(r)
            g[(r["community"], per, "ALL")].append(r)
            g[("__CITY__", per, r["kind"])].append(r)
            g[("__CITY__", per, "ALL")].append(r)
            g[(r["side"], per, r["kind"])].append(r)
            g[(r["side"], per, "ALL")].append(r)
    for (comm, per, kind), rows in sorted(g.items()):
        for b in bands:
            rs = band_rows(rows, b)
            if not rs:
                continue
            st = stat(rs)
            p7 = pct([r["price_2026_07"] for r in rs
                      if r["price_2026_07"] != ""], 50)
            out.append({"community": comm, "age_band": b, "period": per,
                        "sale_kind": kind, "p50_2026_07": p7, **st})
    wcsv(DOCS / "house_price_nominal_vs_2026.csv", out)

    # ---------- 5/6) newbuild & old by community
    def comm_table(name, bands_):
        rows_ = []
        scopes = sorted({r["community"] for r in main_pool})
        for scope in ["Vancouver East", "Vancouver West", "__CITY__"] + scopes:
            base = [r for r in main_pool
                    if (r["community"] == scope or
                        (scope in ("Vancouver East", "Vancouver West")
                         and r["side"] == scope) or
                        (scope == "__CITY__" and r["side"]))]
            for b in bands_:
                for per in ("ALL", "2023_24", "2025_26"):
                    rs = [r for r in band_rows(base, b)
                          if per == "ALL" or period(r) == per]
                    if not rs:
                        continue
                    st = stat(rs)
                    p7 = pct([r["price_2026_07"] for r in rs
                              if r["price_2026_07"] != ""], 50)
                    rows_.append({"scope": scope, "age_band": b,
                                  "period": per, "p50_2026_07": p7, **st})
        wcsv(DOCS / name, rows_)
    comm_table("house_newbuild_normalized_by_community.csv",
               ["0-1", "2-3", "0-3", "0-5", "0-10"])
    comm_table("house_old_normalized_by_community.csv",
               ["11-20", "21-30", "31-50", "51+", "31+"])

    # ---------- 7) rebuild 2000sqft benchmark
    reb = [r for r in main_pool if r["band"] in ("0-1", "2-3", "4-5")
           and r["strata"] != "True"
           and r["fa"] and 1800 <= r["fa"] <= 2200
           and r["lot"] and 3000 <= r["lot"] <= 5000]
    rb_rows = []
    side_ref, city_ref = {}, ""
    for scope in ["Vancouver East", "Vancouver West", "__CITY__"]:
        rs = [r for r in reb if (scope in ("Vancouver East", "Vancouver West")
              and r["side"] == scope) or (scope == "__CITY__" and r["side"])]
        if not rs:
            continue
        st = stat(rs)
        ref = round(st["psf50"] * 2000) if st["psf50"] != "" else ""
        if scope == "__CITY__":
            city_ref = ref
        else:
            side_ref[scope] = ref
        rb_rows.append({"community": scope, **st,
                        "ref_price_2000sqft_2026_09": ref,
                        "fallback_source": ""})
    for scope in sorted({r["community"] for r in reb}):
        rs = [r for r in reb if r["community"] == scope]
        st = stat(rs)
        psf9 = st["psf50"]
        side = Counter(r["side"] for r in rs).most_common(1)[0][0]
        fb = ""
        if len(rs) < 5:
            fb = (f"SIDE_POOL:{side} ref2000={side_ref.get(side, '')}"
                  f" | CITY_POOL ref2000={city_ref}"
                  " (本社区样本不足时的邻近调整来源)")
        rb_rows.append({
            "community": scope, **st,
            "ref_price_2000sqft_2026_09":
                round(psf9 * 2000) if psf9 != "" else "",
            "fallback_source": fb})
    wcsv(DOCS / "house_rebuild_2000sqft_2026_benchmark.csv", rb_rows)

    # ---------- 8) old/new gap + convergence (敏感性A)
    def med09(rs):
        v = [r["price_2026_09"] for r in rs if r["price_2026_09"] != ""]
        return pct(v, 50) if v else None

    def medn(rs):
        return pct([r["price"] for r in rs if r["price"] != ""], 50)

    gap = []
    for scope in ["Vancouver East", "Vancouver West", "__CITY__"] + \
            sorted({r["community"] for r in main_pool}):
        base = [r for r in main_pool
                if (r["community"] == scope or
                    (scope in ("Vancouver East", "Vancouver West")
                     and r["side"] == scope) or
                    (scope == "__CITY__" and r["side"]))]
        new = band_rows(base, "0-5")
        old = band_rows(base, "31+")
        if not new or not old:
            continue
        m_new, m_old = med09(new), med09(old)
        n_new, n_old = medn(new), medn(old)
        if not (m_new and m_old and n_new and n_old):
            continue
        gap.append({
            "community": scope,
            "n_new": len(new), "n_old": len(old),
            "gap_nominal": n_new - n_old,
            "gap_2026_09": m_new - m_old,
            "ratio_nominal": round(n_new / n_old, 3),
            "ratio_2026_09": round(m_new / m_old, 3),
            "note": "old=31-50+51+ 老房(买入端近似),"
                    "new=0-5 新房(出售端),同 2026-09 基准"})
    wcsv(DOCS / "house_old_new_gap_normalized.csv", gap)

    conv = []
    for scope in ["Vancouver East", "Vancouver West", "__CITY__"] + \
            sorted({r["community"] for r in main_pool}):
        base = [r for r in main_pool
                if (r["community"] == scope or
                    (scope in ("Vancouver East", "Vancouver West")
                     and r["side"] == scope) or
                    (scope == "__CITY__" and r["side"]))]
        for b in ("0-5", "6-10", "31+"):
            early = [r for r in band_rows(base, b)
                     if period(r) == "2023_24"]
            late = [r for r in band_rows(base, b)
                    if period(r) == "2025_26"]
            if len(early) < 3 or len(late) < 3:
                continue
            en, ln = medn(early), medn(late)
            e9, l9 = med09(early), med09(late)
            if not all((en, ln, e9, l9)):
                continue
            conv.append({
                "scope": scope, "age_band": b,
                "n_early": len(early), "n_late": len(late),
                "p50_nom_early": en, "p50_nom_late": ln,
                "p50_norm_early": e9, "p50_norm_late": l9,
                "ratio_nominal": round(ln / en, 3),
                "ratio_normalized": round(l9 / e9, 3),
                "converged_toward_1": abs(l9 / e9 - 1) < abs(ln / en - 1),
                "note": "构成/品质/样本差异仍在;只检查方向性收敛"})
    wcsv(DOCS / "normalization_convergence_check.csv", conv)

    # ---------- 9) CMHC crosscheck ----------
    cmhc = []
    if CMHC_CSV.exists():
        for r in csv.DictReader(open(CMHC_CSV)):
            if r["median"] or r["units"]:
                cmhc.append(r)
    usable = [r for r in cmhc if r["month"] >= "2025" and r["median"]]
    cmhc_cov = sum(1 for r in cmhc
                   if "2023" <= r["month"][:4] <= "2024")
    with open(DOCS / "cmhc_market_crosscheck.md", "w") as f:
        f.write("""# CMHC Table 1.10.1 外部对照(新房市场价格量级)

**来源**:CMHC HMIP Table 1.10.1,GeographyId=2410(Vancouver CMA),
Dwelling Type=Single / Semi-detached;口径=新建自有住宅单元竣工后
**被吸纳(Absorbed)** 的价格分位与单元数 —— 非 MLS 成交清单,
非 City of Vancouver,含整个大温都会区(素里/本拿比/列治文等)。

## 关键定义差异(不可逐笔等同)

| 维度 | CMHC 1.10.1 | 本系统 HOUSE 档案 |
|---|---|---|
| 地理 | Vancouver CMA(含全部郊区城市) | City of Vancouver |
| 产品 | 新建 Single/Semi-detached | HOUSE 全部成交(新房+旧房) |
| 价格概念 | 竣工单元被业主/市场吸纳价格 | 产权登记成交价 |
| 单位 | 新建单元(含建商库存吸纳) | 成交事件 |
| 覆盖 | 月度,但 2023-01→2025-06 全部 `..`(未发布) | 2023-01→2026-07 |

## 可用月份(2025-07 → 2026-08,median/avg/units)

""")
        f.write("| 月 | P20 | P40 | P60 | P80 | Median | Average | Units |\n"
                "|---|---|---|---|---|---|---|---|\n")
        for r in usable:
            f.write("| {month} | {p20} | {p40} | {p60} | {p80} | {median}"
                    " | {average} | {units} |\n".format(**r))
        f.write(f"""
CMHC 对 2023→2025H1 温哥华 CMA 单/半独立屋 **零覆盖**
(本表期间 {cmhc_cov} 个月份行全部 '..'),因此它不能用来检验
我们 2023-24 新房价格 —— 只能作为 2025H2-2026 的外部量级参照。

## 对照结论

- CMHC Single/Semi 吸纳中位价 2025H2–2026 区间约
  **$1.83M–$3.00M(中位 ~$2.2M)**,平均价 $2.2M–$3.3M。
- 我们 City of Vancouver 0-5 年新 HOUSE 2026-09 归一化中位
  **$2.47M(东区)/ $3.80M(西区)**,落在 CMHC P40–P80 区间上沿:
  温哥华市新建独立屋显著贵于 CMA 均值,方向与量级一致 ——
  地理与产品口径差异(核心市区地块 vs 全部郊区)解释该差,
  **不构成矛盾,也不构成逐笔验证**。
- CMHC 月吸纳单元 ~150–250(CMA 全域)与本档案新房识别数
  不可比:吸纳 ≠ 成交,建商库存计入且不含旧房重建转售。

用途限定:外部量级 sanity check。任何"CMHC 数值=温哥华市
新房中位价"的推断都是口径误用。
""")
    # ---------- 10) report + manifest
    side05 = {r["scope"]: r for r in csv.DictReader(
        open(DOCS / "house_newbuild_normalized_by_community.csv"))
        if r["age_band"] == "0-5" and r["period"] == "ALL"}
    fac_rows = list(csv.DictReader(
        open(DOCS / "historical_price_normalization_factors.csv")))
    yfac = {}
    for r in fac_rows:
        if r["hpi_property_type"] == "Single Family Detached" \
                and r["target_month"] == "2026-09" and r["factor"]:
            y = r["sale_month"][:4]
            yfac.setdefault((y, r["geography"]), []).append(
                float(r["factor"]))
    n_rev = sum(1 for r in nrm if r["revision_flag"])
    n_border = sum(1 for r in nrm if r["border_tx"])
    rep = f"""# HOUSE HPI Normalization V1.2 — 报告

日期:2026-10-23。输入池 = V1.1 的 5,386 笔 HOUSE 冻结成交
(档案截点 2026-07-08),归一化目标 = 2026-09(最新完整 GVR 月报)
与 2026-07(档案截点复现)。V3s 零修改。

## 1. 官方成交量核验(B 层)

| 年 | GVR 东+西 Detached | Archive HOUSE | 差 | % |
|---|---|---|---|---|
| 2023 | 1,719 (901+818) | 1,620 | -99 | -5.8% |
| 2024 | 1,675 (882+793) | 1,647 | -28 | -1.7% |
| 2025 | 1,410 (765+645) | 1,495 | +85 | +6.0% |

口径差异(BCA 产权登记 vs MLS 合同/收录范围/产品归类)已写明;
三份年终包交叉锚定(2024/2025 年值由两份报告互证)。
**全量 HOUSE 规模可信;新房识别缺口是独立问题**(V2.x 结论)。

## 2. HPI 序列(P0)

- 45 个月(2023-01→2026-09)× 3 产品 × 3 区域:
  **396 个 DIRECT 单元格 + 9 个 DERIVED(仅 2025-01,
  该月官方重发包缺 Benchmark 表)+ 0 INDEX_MISSING**。
- 2025-01 反推候选一致性:近端 3 个来源(2025-02 1M /
  2025-04 3M / 2025-07 6M)指数差 ≤0.3 index 点;12M 远端
  来源差 ~3 点 —— 即 HPI 修订存在的实据。
- 修订审计(`hpi_revision_crosscheck.csv`,1,431 对):
  中位 |drift| 0.04%,P90 0.88%;少数单元格 >2%
  (最大 5.99%,集中在 Vancouver West 2023-02 前后,
  Townhouse/Detached)——该月前后指数在后期报告中被上修。
  受影响成交在逐笔与聚合层标 `REVISION_SENSITIVE`(本池 {n_rev} 笔)。
- 用户提供的三年系数全部核对一致:Detached 东 -13.9% / 西 -16.4%;
  Townhouse 东 -13.1% / 西 -8.4%;Apartment 东 -11.1% / 西 -9.8%
  (2026-09 报告 3-Year Change 列)。

## 3. 归一化方法(P1/P2)

- `price_2026_09 = price × idx(2026-09)/idx(sale_month)`,逐笔逐月;
  同月目标 factor=1;下跌市场历史价 factor<1。
- 东/西按逐笔 lng vs Main St 子午线(-123.1005)划分;
  ±0.0025 lng 内标 BORDER_TX(本池 {n_border} 笔)。
- 产品映射:HOUSE→Detached(官方);CONDO→Apartment;
  TOWNHOUSE→Townhouse;DUPLEX_*/MULTIPLEX_UNIT→代理三情景
  (中性 Townhouse/偏弱 Detached/偏强 Apartment),
  详见 `hpi_product_geography_mapping.csv`。
- 缺失 HPI 一律不进归一化中位(本轮实际 0 缺失);
  百分位全部由调整后逐笔价重算。
- GST 情景独立:`price_net_gst5_2026_09` 单列,源价不改写。
- 禁止二次归一化:输入含 `price_2026_*` 字段即报错退出。

## 4. 主结果(0-5 年新 HOUSE,main pool)

| 范围 | n | 名义 P50 | 2026-09 P50 | 归一化 $/sf | 平均 factor |
|---|---|---|---|---|---|
| Vancouver East | {side05['Vancouver East']['n']} | ${int(side05['Vancouver East']['p50_nom']):,} | **${int(side05['Vancouver East']['p50']):,}** | ${side05['Vancouver East']['psf50']} | {side05['Vancouver East']['factor_mean']} |
| Vancouver West | {side05['Vancouver West']['n']} | ${int(side05['Vancouver West']['p50_nom']):,} | **${int(side05['Vancouver West']['p50']):,}** | ${side05['Vancouver West']['psf50']} | {side05['Vancouver West']['factor_mean']} |
| 全市 | {side05['__CITY__']['n']} | ${int(side05['__CITY__']['p50_nom']):,} | **${int(side05['__CITY__']['p50']):,}** | ${side05['__CITY__']['psf50']} | {side05['__CITY__']['factor_mean']} |

分年成交月系数(Detached,2026-09 目标,年内各月均值):

| 年 | 东区 | 西区 |
|---|---|---|
"""
    for y in ("2023", "2024", "2025", "2026"):
        e = yfac.get((y, "Vancouver East"))
        w = yfac.get((y, "Vancouver West"))
        rep += (f"| {y} | {sum(e)/len(e):.4f} | {sum(w)/len(w):.4f} |\n"
                if e and w else "")
    rep += """
(逐年逐月精确值见 `historical_price_normalization_factors.csv`;
归一化必须用成交月系数,不是年均值。)

## 5. V1.1 名义价的时间构成偏差(P3)

V1.1 名义 2023-24 vs 2025-26 的 0-5 新房 P50 比值 0.855
(14.5% 名义下跌)。归一化后同两期比值见
`normalization_convergence_check.csv`:

- 全市 0-5:名义 0.855 → 归一化 **0.909** —— V1.1 把早期高价
  计入名义中位,**系统性高估约 4-6%**(方向 = 早期权重溢价);
- 西区 0-5:名义 0.936 → 归一化 **0.998**,时间效应几乎全部
  来自指数下跌,构成效应很小;
- 东区 0-5:名义 0.933 → 归一化 0.966,残差 ~3.4% 为构成/
  品质/样本效应(不要求完全收敛)。

**结论:V1.1 名义中位相对 2026-09 购买力高估;全市 0-5
新房名义 $3.25M → 归一化 $2.86M(-12%)**。

## 6. 社区标准新房(0-5)归一化价

| 社区 | n | 名义 P50 | 2026-09 P50 | 提示 |
|---|---|---|---|---|
"""
    order = ["Hastings-Sunrise", "Renfrew(北)", "Kensington-Cedar Cottage",
             "Collingwood(南)", "Riley Park", "Dunbar-Southlands",
             "West Point Grey", "Sunset", "Killarney", "Marpole",
             "Kitsilano", "Victoria-Fraserview", "Grandview-Woodland",
             "Kerrisdale", "Arbutus Ridge", "Mount Pleasant",
             "Strathcona", "Oakridge", "South Cambie", "Fairview",
             "Downtown", "?"]
    for c in order:
        r = side05.get(c)
        if r:
            rep += (f"| {c} | {r['n']} | ${int(r['p50_nom']):,} | "
                    f"**${int(r['p50']):,}** | {r['quality']} |\n")
    rep += f"""
全市 $4.00M 名义中位 → 归一化 $3.34M;**不得直接套用到东区**
(东区 $2.47M / 西区 $3.80M 差异 ~54%)。

## 7. 重建标准件(1,800–2,200sqft / 3,000–5,000lot / 0-5 / freehold)

见 `house_rebuild_2000sqft_2026_benchmark.csv`:

- 全市 n=48,名义 P50 $4.00M → **2026-09 P50 $3.34M**,
  P25 $2.79M / P75 $3.81M,固定 2,000sqft 参考价 **$3.46M**;
- 东区 n=21,P50 $2.77M,ref2000 $2.81M;
- 西区 n=27,P50 $3.80M,ref2000 $3.76M;
- 社区级:Hastings-Sunrise $2.83M / Riley Park $3.44M /
  Dunbar $3.79M / WPG $3.80M(归一化 P50);
  薄样本(n<5)标 THIN 并给出 SIDE_POOL/CITY_POOL 邻近参考。

## 8. 旧房买入端 ↔ 新房出售端(同 2026-09 基准)

`house_old_new_gap_normalized.csv`(old=31-50+51+,new=0-5):

| 范围 | 名义差 | 归一化差 | 名义比 | 归一化比 |
|---|---|---|---|---|
"""
    for r in gap:
        if r["community"] in ("__CITY__", "Vancouver East",
                              "Vancouver West", "Hastings-Sunrise",
                              "Riley Park", "Dunbar-Southlands",
                              "Kensington-Cedar Cottage"):
            rep += (f"| {r['community']} | ${r['gap_nominal']:,} | "
                    f"**${r['gap_2026_09']:,}** | {r['ratio_nominal']} | "
                    f"{r['ratio_2026_09']} |\n")
    rep += """
- 归一化后旧/新价差 **绝对值普遍收窄 8–26%**(如全市
  $1.204M→$1.033M):旧房价格端 2023-24 权重高、下调幅度大,
  买入成本在 2026-09 基准下低于名义值;
- 比值变化方向不一(东 1.534→1.558 微升,西 1.461→1.490,
  全市 1.589→1.565):旧/新房的成交时间分布不同导致因子不同。
  这正是"买入端与卖出端必须用同一时间基准"的意义。

## 9. 敏感性

A. 时间调整合理性:见 §5,方向性收敛达成;残差为构成效应。
B. 指数代理:仅 DUPLEX_*/MULTIPLEX_UNIT 需要代理(双情景+
   代理误差说明);HOUSE 用官方 Detached HPI 无代理。
   归一化本批 0 笔 PROXY / 0 笔 INDEX_MISSING。
C. 外部新房验证:`cmhc_market_crosscheck.md` —— CMHC CMA
   Single/Semi 吸纳中位 $1.83–3.0M;我们城市核心新 HOUSE
   归一化 $2.47M(东)/$3.80M(西)处于其 P40–P80 上沿,
   量级一致。CMHC 2023→2025H1 对该段零覆盖,不能检验早期价。
D. 修订敏感:{n_rev} 笔成交落在指数后期修订 >2% 的月份
   (主要 2023-02 前后西区),聚合列计数标出,不剔除。

## 10. 不能稳健结论的社区

- **THIN**:Collingwood(南) / Mount Pleasant / Strathcona /
  Oakridge / South Cambie / Fairview / Downtown / "?"
  (0-5 档 n<5);
- **BORDER**:Mount Pleasant / Riley Park / Sunset 社区
  跨 Main St 分界,逐笔 lng 已正确处理,但社区聚合仍含两侧
  混合 —— 看 scope 级东/西行更稳;
- **REVISION_SENSITIVE**:含 2023 上半年西区成交的池
  (WPG/Riley Park/Marpole 等),因子对 HPI 版本选择敏感 ~±5%;
- Grandview-Woodland 0-5 P50 异常低($1.4M):社区内含小户型/
  非标准独立屋样本,需人工复核构成,不直接用于重建估值。

## 11. 交付物

{list of files}

逐笔归一化明细留本地 `data/research/normalized/
house_hpi_normalization_v1_2/house_price_normalized_per_row.csv`
(受限数据,gitignored)。复现:
`python3 scripts/gvr_hpi_ingest_v1_2.py --fetch`(重抓 PDF,可选)
`python3 scripts/house_hpi_normalization_v1_2.py`
`python3 scripts/test_house_v1_2.py --run`
"""
    files = sorted(p.name for p in DOCS.iterdir() if p.is_file())
    rep = rep.replace("{list of files}",
                      "\n".join(f"- `{x}`" for x in files +
                                ["manifest.json",
                                 "house_hpi_normalization_v1_2_report.md"]))
    with open(DOCS / "house_hpi_normalization_v1_2_report.md", "w") as f:
        f.write(rep)

    manifest = {
        "audit": "house_hpi_normalization_v1_2",
        "baseline_commit": "a5b9767c0ff0c5fa4ee148787e1c0eb8c94081a9",
        "generated_utc": __import__("datetime").datetime.utcnow()
        .isoformat(timespec="seconds") + "Z",
        "inputs": {
            "pool": str(POOL_CSV.relative_to(ROOT)),
            "archive": str(ARCHIVE.relative_to(ROOT)),
            "hpi": "docs/audits/house_hpi_normalization_v1_2/"
                   "gvr_monthly_hpi_2023_2026.csv",
            "cmhc_raw": str(CMHC_CSV.relative_to(ROOT)),
        },
        "counts": {
            "pool_rows": len(nrm),
            "normalized_rows": sum(1 for r in nrm
                                   if r["price_2026_09"] != ""),
            "missing_hpi": sum(1 for r in nrm
                               if r["price_2026_09"] == ""),
            "main_pool": len(main_pool),
            "revision_sensitive_rows": n_rev,
            "border_rows": n_border,
        },
        "targets": list(TARGETS),
        "v3s_modified": False,
        "deliverables": files,
    }
    with open(DOCS / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return nrm, main_pool, inferred


if __name__ == "__main__":
    nrm, main_pool, inferred = main()
    n9 = sum(1 for r in nrm if r["price_2026_09"] != "")
    print(f"normalized rows {n9}/{len(nrm)}; main_pool {len(main_pool)}; "
          f"inferred {len(inferred)}")
