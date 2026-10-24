#!/usr/bin/env python3
"""Housing Age Recovery V2.4 回归——直接调生产函数。

覆盖:resolve_year_built_at_sale 各源优先级;
fs=2023 卷面下界不构成证据;TEMPORAL_CONFLICT 不倒灌;
BUILDING_IDENTITY_CONFLICT 否决旧 yb;多弱源一致升级;
jump_year 双阈值;sale_kind≠age;守恒;or True 自检;
V3s 零修改;双 subprocess 幂等。
"""
import ast, csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import housing_age_recovery_v2_4 as M          # noqa: E402

DOCS = ROOT / "docs/audits/housing_age_recovery_v2_4"
OUTD = ROOT / "data/research/normalized/housing_age_recovery_v2_4"
R = Counter()


def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


S = lambda sd, **kw: {"sale_date": sd, "yb": kw.get("yb", ""),
                      "pid": kw.get("pid", "p"),
                      "strata_plan": kw.get("sp", ""),
                      "fa": kw.get("fa", "")}

# ---------- 证据优先级 ----------
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"census_yb": 2024, "roll_yb": 1980})
chk("census > roll_yb(STRICT+冲突标记)",
    r["yb"] == 2024 and r["age_tier"] == "STRICT_AGE"
    and r["conflict"] == "SOURCE_YEAR_CONFLICT")
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"roll_yb": 1990})
chk("roll_yb 一致 → STRICT 旧房",
    r["yb"] == 1990 and r["age_tier"] == "STRICT_AGE" and
    r["conflict"] == "NO_CONFLICT" and
    r["band"] == "31-50")

# ---------- plan/pid 首现年推断 ----------
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"plan_fs": 2025})
chk("plan_fs=2025 → yb 2024 SUPPORTED age1",
    r["yb"] == 2024 and r["age_tier"] == "SUPPORTED_AGE"
    and r["band"] == "1")
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"plan_fs": 2023})
chk("plan_fs=2023 卷面下界 → 无证据",
    r["yb"] is None and r["age_tier"] == "AGE_UNKNOWN")
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"pid_fs": 2023})
chk("pid_fs=2023 同样下界 → 无证据",
    r["yb"] is None)
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"plan_fs": 2026})
chk("plan_fs=2026 竣工晚于成交 → TEMPORAL 不计",
    r["yb"] is None and
    r["conflict"] == "TEMPORAL_CONFLICT")
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"jump_yr": 2026})
chk("jump 晚于成交 → TEMPORAL 不倒灌",
    r["yb"] is None and
    r["conflict"] == "TEMPORAL_CONFLICT")

# ---------- 身份冲突:旧 roll yb 被 plan_fs 否决 ----------
r = M.resolve_year_built_at_sale(
    S("2025-01-01", yb="1980"),
    {"roll_yb": 1980, "plan_fs": 2025})
chk("roll1980+plan2025 → IDENTITY 冲突+推断",
    r["conflict"] == "BUILDING_IDENTITY_CONFLICT" and
    r["yb"] == 2024 and r["age_tier"] == "SUPPORTED_AGE"
    and r["rebuilt"] is True)
r = M.resolve_year_built_at_sale(
    S("2025-01-01", yb="1980"),
    {"roll_yb": 1980, "plan_fs": 1985})
chk("roll1980+plan1985 同年代 → STRICT 保留",
    r["yb"] == 1980 and
    r["conflict"] == "NO_CONFLICT")

# ---------- 多弱源一致升级 ----------
r = M.resolve_year_built_at_sale(
    S("2025-01-01"),
    {"plan_fs": 2025, "jump_yr": 2025})
chk("plan_fs+jump 同年 → SUPPORTED",
    r["yb"] == 2024 and r["age_tier"] == "SUPPORTED_AGE")
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"jump_yr": 2025})
chk("单 jump → INFERRED",
    r["age_tier"] == "INFERRED_AGE" and r["yb"] == 2024)
r = M.resolve_year_built_at_sale(
    S("2025-01-01"), {"permit_yr": 2023})
chk("单 permit → INFERRED yb=2024",
    r["age_tier"] == "INFERRED_AGE" and r["yb"] == 2024)
r = M.resolve_year_built_at_sale(
    S("2025-01-01"),
    {"jump_yr": 2024, "permit_yr": 2023})
chk("jump2024+permit2023 → 同指 2023-24 → 一致升级",
    r["age_tier"] == "SUPPORTED_AGE")

# ---------- jump_year 双阈值 ----------
chk("升值 30% 不算 jump",
    M.jump_year({2023: 1_000_000, 2024: 1_300_000})
    is None)
chk("翻倍+大额 → jump",
    M.jump_year({2023: 300_000, 2024: 1_200_000}) == 2024)
chk("小额翻倍不算",
    M.jump_year({2023: 200_000, 2024: 450_000}) is None)
chk("单年无 jump", M.jump_year({2024: 900_000}) is None)
chk("非连续年也可比",
    M.jump_year({2023: 300_000, 2025: 1_200_000}) == 2025)

# ---------- 真实数据守恒 ----------
rows = list(csv.DictReader(open(
    OUTD / "age_recovery_per_row.csv")))
chk("per_row 非空且 canonical_tx 唯一",
    rows and len({r["canonical_tx"] for r in rows}) ==
    len(rows))
chk("tier 枚举合法", all(
    r["age_tier"] in M.TIERS for r in rows))
chk("conflict 枚举合法", all(
    r["conflict"] in M.CONFLICTS for r in rows))
chk("全库无 resolved_yb>成交年(不倒灌)",
    all(not r["resolved_yb"] or
        int(r["resolved_yb"]) <= int(r["sale_year"])
        for r in rows))
chk("HOUSE 2025 0-1 STRICT=7",
    sum(1 for r in rows if r["product"] == "HOUSE" and
        r["sale_year"] == "2025" and
        r["age_tier"] == "STRICT_AGE" and
        r["after_band"] in ("0", "1")) == 7)
chk("HOUSE 2023 0-3 STRICT=41(census 复现)",
    sum(1 for r in rows if r["product"] == "HOUSE" and
        r["sale_year"] == "2023" and
        r["age_tier"] == "STRICT_AGE" and
        r["after_band"] in ("0", "1", "2-3")) == 41)
chk("DUPLEX_HALF 2025 恢复显著(>200)",
    sum(1 for r in rows if r["product"] == "DUPLEX_HALF"
        and r["sale_year"] == "2025" and
        r["after_band"] in ("0", "1", "2-3")) > 200)
chk("CONDO 2025 恢复非零",
    sum(1 for r in rows if r["product"] == "CONDO" and
        r["sale_year"] == "2025" and
        r["after_band"] in ("0", "1", "2-3")) > 0)

# 交付物守恒
r4 = list(csv.DictReader(open(
    DOCS / "age_recovery_before_after.csv")))
chk("before_after:after=strict+supp+inf",
    all(int(r["after_total"]) == int(r["strict"]) +
        int(r["supported"]) + int(r["inferred"])
        for r in r4))
chk("before_after:delta=after-before",
    all(int(r["delta"]) == int(r["after_total"]) -
        int(r["before"]) for r in r4))
r5 = list(csv.DictReader(open(
    DOCS / "housing_sales_by_product_exact_age.csv")))
chk("exact_age 行数=per_row",
    sum(int(r["n"]) for r in r5) == len(rows))
r1 = list(csv.DictReader(open(
    DOCS / "age_source_coverage_by_product_year.csv")))
tot = sum(int(r["n"]) for r in r1
          if r["metric"] == "total")
chk("coverage total=per_row", tot == len(rows))
wf = list(csv.DictReader(open(
    DOCS / "zero_newbuild_root_cause_waterfall.csv")))
chk("瀑布 after_0_3_total=三层和",
    all(int(r["after_0_3_total"]) ==
        int(r["after_0_3_strict"]) +
        int(r["after_0_3_supported"]) +
        int(r["after_0_3_inferred"]) for r in wf))
man = json.loads((DOCS / "manifest.json").read_text())
chk("manifest house_2025_0_1_strict=7",
    man["counts"]["house_2025_0_1_strict"] == 7)

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
     "research/housing-age-recovery-v2-4*"],
    capture_output=True, text=True).stdout.split()
if br:
    v3 = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         "a52f911a9e91d949387c377eb15d6a0be22aa32b", br[0],
         "--", "scripts/v3s_consistency_gate.py",
         "scripts/render_v3s_shadow.py",
         "src/northshore_watchdog/research/"
         "strategy_v2_shadow.py",
         "data/research/normalized/sales_archive_v1_0_3"],
        capture_output=True, text=True).stdout.strip()
    chk("V3s/冻结档案零修改", v3 == "")
else:
    chk("V2.4 分支存在", False)

# ---------- 幂等 ----------
if "--run" in sys.argv:
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "housing_age_recovery_v2_4.py")],
        capture_output=True)
    m1 = json.loads((DOCS / "manifest.json").read_text())
    m1.pop("run", None)
    subprocess.run([sys.executable, str(
        ROOT / "scripts" / "housing_age_recovery_v2_4.py")],
        capture_output=True)
    m2 = json.loads((DOCS / "manifest.json").read_text())
    m2.pop("run", None)
    chk("双 subprocess 幂等", m1 == m2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
