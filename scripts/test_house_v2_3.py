#!/usr/bin/env python3
"""HOUSE V2.3 年龄分布/市场规模回归——直接调生产函数。

覆盖:互斥年龄档边界;merge_band;BROAD 取最新 ≤sy 证据;
census corrected 进候选;yb>sy 不采用;CONFLICT 跨档;
守恒 5386;合并档=精确档之和;LD 零填充;
BROAD⊇STRICT 口径方向;or True 自检;V3s 零修改;
双 subprocess 幂等。
"""
import ast, csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_age_market_scale_v2_3 as M        # noqa: E402

DOCS = ROOT / "docs/audits/house_newbuild_market_scale"
OUTD = ROOT / "data/research/normalized/house_market_scale_v2_3"
R = Counter()


def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


# ---------- 纯函数:band ----------
chk("age None → UNKNOWN", M.band(None) == "UNKNOWN")
chk("age -1 → CONFLICT(负房龄非法)",
    M.band(-1) == "CONFLICT")
chk("age 0/1 → 0/1", M.band(0) == "0" and M.band(1) == "1")
chk("age 2/3 → 2-3", M.band(2) == "2-3" and M.band(3) == "2-3")
chk("age 4/5 → 4-5", M.band(4) == "4-5" and M.band(5) == "4-5")
chk("age 6/10 → 6-10", M.band(6) == "6-10" and
    M.band(10) == "6-10")
chk("age 11/20 → 11-20", M.band(11) == "11-20" and
    M.band(20) == "11-20")
chk("age 21/30 → 21-30", M.band(21) == "21-30" and
    M.band(30) == "21-30")
chk("age 31/50 → 31-50", M.band(31) == "31-50" and
    M.band(50) == "31-50")
chk("age 51+ → 51+", M.band(51) == "51+" and
    M.band(200) == "51+")
chk("band 互斥:0-199 覆盖 9 个精确档无空洞",
    len({M.band(a) for a in range(0, 200)}) == 9)

# ---------- merge_band ----------
chk("merge:0/1→0-1,其他原样",
    M.merge_band("0") == "0-1" and
    M.merge_band("1") == "0-1" and
    M.merge_band("4-5") == "4-5" and
    M.merge_band("CONFLICT") == "CONFLICT")

# ---------- broad_age ----------
S = lambda sd, yb="", pid="p": {"sale_date": sd, "yb": yb,
                                "pid": pid}
age, yb = M.broad_age(S("2025-03-01", "2024"), {}, {})
chk("archive yb → age 1", age == 1 and yb == 2024)
age, _ = M.broad_age(S("2025-03-01", ""), {}, {},
                     {"year_built_corrected": "2023"})
chk("census corrected 进 broad 候选", age == 2)
age, _ = M.broad_age(S("2025-03-01", ""), {}, {},
                     {"cur_max_yb": "2024"})
chk("census cur_max_yb 进 broad 候选", age == 1)
age, _ = M.broad_age(S("2025-03-01", "1980"),
                     {"p": {2026: {"yb": 2026}}},
                     {}, {"year_built_corrected": "1980"})
chk("yb>sy 的卷面不采用(滞后不倒灌)",
    age == 45)
age, _ = M.broad_age(S("2025-03-01", "1980"),
                     {"p": {2024: {"yb": 2024},
                            2025: {"yb": 2023}}},
                     {}, {})
chk("多 ≤sy 证据取最新(最小房龄)", age == 1)
age, _ = M.broad_age(S("2025-03-01", ""), {}, {}, {})
chk("无任何证据 → no_evidence",
    age is None)
age, yb = M.broad_age(S("2025-03-01"), {}, {},
                      {"year_built_corrected": "garbage"})
chk("非数字 corrected → no_evidence",
    age is None)

# ---------- conflict_flag ----------
chk("两源跨 0-10 边界 → CONFLICT", M.conflict_flag(
    S("2025-01-01", "2000"),
    {"p": {2024: {"yb": 2015}}}, {}) is True)
chk("两源都老 → 非 CONFLICT", M.conflict_flag(
    S("2025-01-01", "2000"),
    {"p": {2024: {"yb": 2002}}}, {}) is False)
chk("单源 → 非 CONFLICT", M.conflict_flag(
    S("2025-01-01", "2000"), {}, {}) is False)

# ---------- 真实数据守恒 ----------
per = OUTD / "house_age_per_row.csv"
chk("per_row 存在", per.exists())
rows = list(csv.DictReader(open(per)))
chk("house=5386 唯一", len(rows) == 5386 and
    len({r["canonical_tx"] for r in rows}) == 5386)
chk("每行恰 1 个 strict band + 1 个 broad band",
    all(r["strict_band"] in M.BANDS + ["CONFLICT",
                                       "UNKNOWN"] and
        r["broad_band"] for r in rows))
chk("strict 0-3 复现用户复核 41/48/26/13",
    [sum(1 for r in rows if r["sale_year"] == str(y) and
         r["strict_band"] in ("0", "1", "2-3"))
     for y in (2023, 2024, 2025, 2026)] ==
    [41, 48, 26, 13])
chk("2025 0-1 严格=7", sum(
    1 for r in rows if r["sale_year"] == "2025" and
    r["strict_band"] in ("0", "1")) == 7)
chk("2026 成交全部 ≤2026-07-08",
    all(s["sale_date"] <= M.CUTOFF_2026
        for s in csv.DictReader(open(M.V.IN["archive"]))
        if s["product"] == "HOUSE" and
        s["sale_date"].startswith("2026")))

r1 = list(csv.DictReader(open(
    DOCS / "house_sales_by_exact_age_year.csv")))
r2 = list(csv.DictReader(open(
    DOCS / "house_sales_0_5_6_10_by_year.csv")))
chk("exact CSV 守恒 5386×2 层",
    sum(int(r["n"]) for r in r1) == 10772)

# 合并档 = 精确档之和
chk("0-5 合并档=精确档之和(全年全kind)",
    all(sum(int(r["n"]) for r in r1
            if r["tier"] == t and r["year"] == y and
            r["sale_kind"] == k and
            r["band"] in ("0", "1", "0-1", "2-3", "4-5"))
        == sum(int(r["n"]) for r in r2
               if r["tier"] == t and r["year"] == y and
               r["sale_kind"] == k and
               r["merged_band"] == "0-5")
        for t in ("STRICT", "BROAD")
        for y in ("2023", "2024", "2025", "2026")
        for k in ("FIRST_SALE", "RESALE", "UNKNOWN")))
chk("0-10 合并档=精确档之和",
    all(sum(int(r["n"]) for r in r1
            if r["tier"] == t and r["year"] == y and
            r["sale_kind"] == k and
            r["band"] in ("0", "1", "0-1", "2-3", "4-5",
                          "6-10"))
        == sum(int(r["n"]) for r in r2
               if r["tier"] == t and r["year"] == y and
               r["sale_kind"] == k and
               r["merged_band"] == "0-10")
        for t in ("STRICT", "BROAD")
        for y in ("2023", "2024", "2025", "2026")
        for k in ("FIRST_SALE", "RESALE", "UNKNOWN")))

# broad/strict 表内部一致
r4 = list(csv.DictReader(open(
    DOCS / "house_broad_vs_strict_age_counts.csv")))
chk("r4 每行 gap=broad-strict",
    all(int(r["gap"]) == int(r["broad"]) - int(r["strict"])
        for r in r4))
chk("r4 strict 合计=5386",
    sum(int(r["strict"]) for r in r4) == 5386)
chk("r4 broad 合计=5386",
    sum(int(r["broad"]) for r in r4) == 5386)
chk("r4 无重复 (year,band)",
    len({(r["year"], r["band"]) for r in r4}) == len(r4))

# LD 表:全组合存在且产品不混入 HOUSE
r3 = list(csv.DictReader(open(
    DOCS / "low_density_newbuild_sales_by_product_age.csv")))
chk("LD 6产品×4年×4档×3kind=288 行",
    len(r3) == 6 * 4 * 4 * 3)
chk("LD 不含 HOUSE", not any(
    r["product"] == "HOUSE" for r in r3))
chk("LD 0 填充行存在(2025 DUPLEX_HALF 0-1)",
    any(r["product"] == "DUPLEX_HALF" and r["year"] == "2025"
        and r["band"] == "0-1" and r["n"] == "0"
        for r in r3))

# 许可偏移矩阵
om = list(csv.DictReader(open(
    OUTD / "permit_sales_offset_matrix.csv")))
chk("偏移矩阵:offset∈{0,1,2,3} 且 sale_year≤2026",
    all(int(r["offset_years"]) in (0, 1, 2, 3) and
        int(r["sale_year"]) <= 2026 for r in om))
chk("偏移矩阵 2023 许可 offset1=2024 成交",
    any(r["permit_year"] == "2023" and
        r["offset_years"] == "1" and
        r["sale_year"] == "2024" for r in om))
chk("官方声明仅 2024/2025 列值",
    all(r["sdh_permits_official"] in ("", "246", "187")
        for r in om))

man = json.loads((DOCS / "manifest.json").read_text())
chk("manifest house=5386",
    man["counts"]["house"] == 5386)
chk("manifest strict_0_10 各年 >0",
    all(v > 0 for v in
        man["counts"]["strict_0_10_by_year"].values()))

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
     "research/house-age-market-scale-v2-3*"],
    capture_output=True, text=True).stdout.split()
if br:
    v3 = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         "503114fbf302814dfb813ec3d60c88659b3c7ec1", br[0],
         "--", "scripts/v3s_consistency_gate.py",
         "scripts/render_v3s_shadow.py",
         "src/northshore_watchdog/research/"
         "strategy_v2_shadow.py",
         "data/research/normalized/sales_archive_v1_0_3"],
        capture_output=True, text=True).stdout.strip()
    chk("V3s/冻结档案零修改", v3 == "")
else:
    chk("V2.3 分支存在", False)

# ---------- 幂等 ----------
if "--run" in sys.argv:
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "house_age_market_scale_v2_3.py")],
        capture_output=True)
    m1 = json.loads((DOCS / "manifest.json").read_text())
    m1.pop("run", None)
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "house_age_market_scale_v2_3.py")],
        capture_output=True)
    m2 = json.loads((DOCS / "manifest.json").read_text())
    m2.pop("run", None)
    chk("双 subprocess 幂等", m1 == m2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
