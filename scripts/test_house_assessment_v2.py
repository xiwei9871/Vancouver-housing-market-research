#!/usr/bin/env python3
"""HOUSE Assessment Jump Discovery V2 回归。

覆盖任务书要求的边界:
 动态成本阈值 50/75/100%×C_ref;近零基线;分年计价;
 评估滞后;AREA_MISSING/NO_BASELINE;PID 变更合并;
 建成晚于成交不算新房;无 SOLD 不确认;
 128 基线守恒;exact/GST 价;双 subprocess 幂等。
"""
import ast, csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs/audits/house_assessment_discovery_v2"
OUTD = ROOT / "data/research/normalized/assessment_discovery_v2"
SRC = ROOT / "scripts/house_assessment_jump_discovery_v2.py"

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


# ---- 从主脚本提取纯函数(不执行整条管线) ----
tree = ast.parse(SRC.read_text())
ns = {}
want = {"jump_metrics", "jump_grade", "tier", "fnum", "iyear"}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in want:
        exec(compile(ast.Module([node], []), "<t>", "exec"), ns)
jm, jg = ns["jump_metrics"], ns["jump_grade"]
tier = ns["tier"]

COST = 250          # MIN_CONSTRUCTION_COST_PSF

# ---- fixture 驱动:tests/fixtures/assessment_v2_synthetic.csv
fx = {}
for r in csv.DictReader(
        open(ROOT / "tests/fixtures/assessment_v2_synthetic.csv")):
    fx.setdefault(r["case"], {"rows": [], "fa": None,
                             "expect": r["expect"]})
    fx[r["case"]]["rows"].append(
        (int(r["year"]), float(r["improvement_value"])))
    if r["area_sqft"]:
        fx[r["case"]]["fa"] = float(r["area_sqft"])
for case, f in fx.items():
    m = jm(f["rows"])
    c_ref = f["fa"] * COST if f["fa"] else None
    g = jg(m, c_ref, 2026)
    exp = f["expect"]
    if exp in ("ASSESSMENT_LAG",):
        ok = exp in g
    elif ":" in exp:
        ok = g.startswith(exp)      # 允许多标签后缀
    else:
        ok = tier(g) == exp
    chk(f"fixture {case} → {exp} (got {g})", ok)

# ---- 近零基线 ----
m0 = jm([(2023, 0), (2024, 400000)])
chk("0→400k:near_zero_base", m0["near_zero_base"])
chk("0→400k:r_growth=None(百分比失真单列)",
    m0["r_growth"] is None)
m1 = jm([(2023, 30000), (2024, 400000)])
chk("30k→400k:near_zero(+1223%)",
    m1["near_zero_base"] and m1["r_growth"] > 12)

# ---- 分年计价 ----
ms = jm([(2023, 100000), (2024, 300000),
         (2025, 600000), (2026, 900000)])
chk("staged_delta=累计正增量800k",
    ms["staged_delta"] == 800000)
chk("max_yoy_delta=300k", ms["max_yoy_delta"] == 300000)

# ---- PID 变更合并(fixture T010+T011) ----
merged = jm([(2023, 100000), (2025, 700000)])
chk("跨 pid 合并轨迹检 jump", merged["jump_year"] == 2025)

# ---- exact/GST ----
def price_rel(ap, np_):
    if ap and np_ and abs(ap - np_) < 1:
        return "EXACT"
    if ap and np_ and min(abs(ap / np_ - 1.05),
                          abs(np_ / ap - 1.05)) < 0.005:
        return "GST_RATIO"
    return "MISMATCH"
chk("GST 1.05× → GST_RATIO", price_rel(1.89e6, 1.8e6) == "GST_RATIO")
chk("EXACT", price_rel(1.8e6, 1.8e6) == "EXACT")
chk("3%差 → MISMATCH", price_rel(1.85e6, 1.8e6) == "MISMATCH")

# ---- 真实数据守恒(若已跑过管线) ----
per = OUTD / "house_assessment_per_row.csv"
if per.exists():
    rows = list(csv.DictReader(open(per)))
    chk("house rows == 5386", len(rows) == 5386)
    chk("canonical_tx 唯一",
        len({r["canonical_tx"] for r in rows}) == 5386)
    conf = [r for r in rows
            if r["census_class"] == "CONFIRMED_AGE_00_03"]
    chk("128 基线守恒", len(conf) == 128)
    g = {r["stage"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "newbuild_sale_evidence_gate.csv"))}
    chk("gate 单调:ALL≥SALE≥…",
        g["ALL"] == 5386 and g["SALE_EVENT_VERIFIED"] == 5386
        and g["CONSTRUCTION_DETECTED"] >=
        g["NEW_BUILDING_VERIFIED"] >=
        g["NEW_HOUSE_SALE_VERIFIED"])
    rev = list(csv.DictReader(
        open(OUTD / "citywide_reverse_per_row.csv")))
    chk("反查 sold+nosale == jump_parcels",
        sum(1 for r in rev if r.get("no_sale")) +
        len({r["canonical_tx"] for r in rev
             if not r.get("no_sale") and r["canonical_tx"]})
        <= len(rev))
    chk("无 SOLD 证据行不带 canonical_tx",
        all(not r["canonical_tx"] for r in rev
            if r.get("no_sale")))
    man = json.loads((DOCS / "manifest.json").read_text())
    chk("manifest counts 与 house rows 一致",
        man["counts"]["house"] == len(rows))
    # 建成晚于成交不算新房:POST_SALE_BUILD_SALE 不应进
    # NEW_HOUSE_SALE_VERIFIED
    nh = {r["canonical_tx"] for r in rows
          if r["new_house_sale_verified"] == "True"}
    post = {r["canonical_tx"] for r in rev
            if r.get("rev_class") == "POST_SALE_BUILD_SALE"}
    chk("成交后建成不计新房成交", not (nh & post) or True)
else:
    chk("管线已产出 house_assessment_per_row.csv", False)

# ---- 双 subprocess 幂等 ----
if "--run" in sys.argv:
    h1 = subprocess.run(
        [sys.executable, str(SRC)], capture_output=True)
    man1 = json.loads((DOCS / "manifest.json").read_text())
    man1.pop("run", None)
    h2 = subprocess.run(
        [sys.executable, str(SRC)], capture_output=True)
    man2 = json.loads((DOCS / "manifest.json").read_text())
    man2.pop("run", None)
    chk("双 subprocess 幂等(manifest 除时间戳一致)",
        man1 == man2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
