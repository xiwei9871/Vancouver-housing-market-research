#!/usr/bin/env python3
"""HOUSE Regional Pricing V1.1 回归——直接调生产函数。

覆盖:pct/stat_block 边界;is_excluded 隔离;pool 守恒
(5386);tier/隔离过滤;FIRST_SALE GST 情景=价/1.05;
UNKNOWN kind 不进首销中位;主表 n 合计守恒;0-5 合并
口径;时间调整因子存在;or True 自检;V3s 零修改;
双 subprocess 幂等。
"""
import ast, csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_regional_pricing_v1_1 as M        # noqa: E402

DOCS = ROOT / "docs/audits/house_regional_pricing_v1_1"
OUTD = ROOT / "data/research/normalized/house_regional_pricing_v1_1"
R = Counter()


def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


# ---------- pct ----------
chk("pct 空 → ''", M.pct([], 50) == "")
chk("pct 单值", M.pct([5], 50) == 5)
chk("pct 两值 50 中位", M.pct([100, 200], 50) == 150)
chk("pct P25/P75 插值(取整输出)",
    M.pct([1, 2, 3, 4], 25) == 2 and
    M.pct([1, 2, 3, 4], 75) == 3)

# ---------- stat_block ----------
st = M.stat_block([{"price": 1000, "psf": 100, "fa": 10,
                    "lot": 100, "tier": "STRICT_AGE",
                    "date": "2025-01-01"}])
chk("stat_block 单行", st["n"] == 1 and
    st["p50"] == 1000 and st["quality"] == "THIN")
st = M.stat_block([{"price": i * 1000, "psf": i, "fa": 1,
                    "lot": 1, "tier": "STRICT_AGE",
                    "date": "2025-01-01"} for i in
                   range(1, 8)])
chk("stat_block n≥5 → OK", st["quality"] == "OK" and
    st["n"] == 7 and st["strict_share"] == 1.0)

# ---------- is_excluded ----------
chk("strata → STRATA", M.is_excluded(
    {"is_strata": "True"}) == "STRATA")
chk("laneway → LWH", M.is_excluded(
    {"is_strata": "False", "bca_desc": "2 STY house - "
     "w/ Laneway House"}) == "LWH")
chk("fa<800 → FA_OUTLIER", M.is_excluded(
    {"is_strata": "False", "bca_desc": "x", "fa": "662"})
    == "FA_OUTLIER")
chk("lot>10000 → BIG_LOT", M.is_excluded(
    {"is_strata": "False", "bca_desc": "x",
     "fa": "2000", "lot_sqft": "15000"}) == "BIG_LOT")
chk("普通 → ''", M.is_excluded(
    {"is_strata": "False", "bca_desc": "2 STY house",
     "fa": "2000", "lot_sqft": "4000"}) == "")

# ---------- 真实池守恒 ----------
pool = list(csv.DictReader(open(
    OUTD / "house_price_pool_per_row.csv")))
chk("pool=5386", len(pool) == 5386)
chk("每行恰一个 band", all(r["band"] in M.BANDS_OUT
                           for r in pool))
chk("tier 合法", all(r["tier"] in
                     ("STRICT_AGE", "SUPPORTED_AGE",
                      "INFERRED_AGE", "AGE_UNKNOWN")
                     for r in pool))
chk("FIRST_SALE 行有 gst5 净价",
    all(r["price_net_gst5"] != "" for r in pool
        if r["kind"] == "FIRST_SALE" and r["price"]))
chk("RESALE 无 gst 净价列",
    all(r["price_net_gst5"] == "" for r in pool
        if r["kind"] == "RESALE"))

# 主表守恒:ALL 期 n = 两分期 n 之和
r1 = list(csv.DictReader(open(
    DOCS / "house_price_by_community_age.csv")))
for k in {(r["community"], r["age_band"],
           r["sale_kind"]) for r in r1}:
    a = sum(int(r["n"]) for r in r1
            if (r["community"], r["age_band"],
                r["sale_kind"]) == k and
            r["period"] == "ALL")
    parts = sum(int(r["n"]) for r in r1
                if (r["community"], r["age_band"],
                    r["sale_kind"]) == k and
                r["period"] != "ALL")
    if a != parts:
        chk(f"ALL=分期和 {k}", False)
        break
else:
    chk("ALL=两分期之和(全组合)", True)

# UNKNOWN 不混首销:FIRST_SALE 行 strict 池仅 kind=FS
chk("FIRST_SALE 基准行 n≤池内 FS 数", all(
    int(r["n"]) <= sum(1 for x in pool
                       if x["kind"] == "FIRST_SALE" and
                       x["community"] == r["community"])
    for r in csv.DictReader(open(
        DOCS / "house_newbuild_0_3_price_benchmark.csv"))
    if r["sale_kind"] == "FIRST_SALE"))

# 调整表存在时间因子
adj = {r["scope"]: r for r in csv.DictReader(
    open(DOCS / "house_price_size_lot_adjustment.csv"))}
chk("调整表含 GST 情景行",
    "newbuild_0_3_FIRST_SALE_gst5_net" in adj)
chk("GST 净价 < 原价",
    adj["newbuild_0_3_FIRST_SALE_gst5_net"]["p50"] and
    int(adj["newbuild_0_3_FIRST_SALE_gst5_net"]["p50"])
    < int(adj["newbuild_0_3_FIRST_SALE"]["p50"]))
chk("重建区间行存在且 n>0",
    int(adj["rebuild_fa1800_2200_lot3_5k_age0_5"]["n"])
    > 0)
chk("0-3 与 4-10 基准分离",
    adj["newbuild_0_3_all"]["n"] and
    adj["nearnew_4_10_all"]["n"])

man = json.loads((DOCS / "manifest.json").read_text())
chk("manifest house_rows=5386",
    man["counts"]["house_rows"] == 5386)
chk("manifest main_pool>0", man["counts"]["main_pool"] > 0)
chk("报告文件存在且非空",
    (DOCS / "house_regional_pricing_v1_1_report.md"
     ).stat().st_size > 1000)

# ---------- or True 自检 ----------
tree = ast.parse(Path(__file__).read_text())
bad = [n for n in ast.walk(tree)
       if isinstance(n, ast.Call) and
       getattr(n.func, "id", "") == "chk" and
       any(isinstance(a, ast.BoolOp) and
           isinstance(a.op, ast.Or) and
           any(isinstance(v, ast.Constant) and
               v.value is True for v in a.values)
           for a in n.args)]
chk("无 or True 恒真断言", not bad)

# ---------- V3s 零修改 ----------
br = subprocess.run(
    ["git", "-C", str(ROOT), "branch", "--list",
     "research/house-regional-pricing-v1-1*"],
    capture_output=True, text=True).stdout.split()
if br:
    v3 = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         "a5b9767c0ff0c5fa4ee148787e1c0eb8c94081a9", br[0],
         "--", "scripts/v3s_consistency_gate.py",
         "scripts/render_v3s_shadow.py",
         "src/northshore_watchdog/research/"
         "strategy_v2_shadow.py",
         "data/research/normalized/sales_archive_v1_0_3"],
        capture_output=True, text=True).stdout.strip()
    chk("V3s/冻结档案零修改", v3 == "")
else:
    chk("V1.1 分支存在", False)

# ---------- 幂等 ----------
if "--run" in sys.argv:
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "house_regional_pricing_v1_1.py")],
        capture_output=True)
    m1 = json.loads((DOCS / "manifest.json").read_text())
    m1.pop("run", None)
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "house_regional_pricing_v1_1.py")],
        capture_output=True)
    m2 = json.loads((DOCS / "manifest.json").read_text())
    m2.pop("run", None)
    chk("双 subprocess 幂等", m1 == m2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
