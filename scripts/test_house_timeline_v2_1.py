#!/usr/bin/env python3
"""HOUSE Newbuild Timeline Gate V2.1 回归。

测试直接 import 生产函数(classify_timeline / gate_level /
sale_evidence_grade / jump_metrics / jump_grade),
不写近似逻辑。含 or True 恒真断言自检、V3s 零修改、
双 subprocess 幂等。
"""
import csv, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_newbuild_timeline_gate_v2_1 as M        # noqa: E402

DOCS = ROOT / "docs/audits/house_newbuild_timeline_v2_1"
OUTD = ROOT / "data/research/normalized/newbuild_timeline_v2_1"
R = Counter()


def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


CT = M.classify_timeline
GL = M.gate_level
SE = M.sale_evidence_grade

# ---- P0-A-3 反例(任务书五个场景) ----
# 1) 2024 卖地、2025 建、2026 评估上涨 → OLD_HOUSE_OR_LAND_SALE
chk("卖地后买家重建 → OLD(不是新房成交)",
    CT(None, 2025, 1920, None, 2025, 2026, 900000, 2024) ==
    "OLD_HOUSE_OR_LAND_SALE")
# 2) 2025 签发许可、2025 售未完工旧房 → CONSTRUCTION_STAGE
chk("成交前许可+突变在后 → CONSTRUCTION_STAGE_SALE",
    CT(None, None, 1920, 2025, None, 2026, 800000, 2025) ==
    "CONSTRUCTION_STAGE_SALE")
# 3) 2024 竣工、2025 售出 → CONFIRMED
chk("yb=2024 ≤sy 且 0-3 → CONFIRMED",
    CT(2024, 2024, 1920, 2023, None, 2024, 900000, 2025) ==
    "CONFIRMED_NEW_HOUSE_SALE")
# 4) 2025 竣工+2025 售,价值 2026 才显 → 仍 CONFIRMED(yb 证据)
chk("评估滞后不倒灌:yb≤sy 即 CONFIRMED",
    CT(2025, 2025, 1920, 2024, None, 2026, 900000, 2025) ==
    "CONFIRMED_NEW_HOUSE_SALE")
# 5) 同 pid 2023 旧房成交 + 2026 新房成交:独立保留
chk("2023 旧房成交 → OLD;2026 新房成交 → CONFIRMED",
    CT(None, 2025, 1920, None, 2024, 2025, 800000, 2023) ==
    "OLD_HOUSE_OR_LAND_SALE" and
    CT(2025, 2025, 1920, 2024, None, 2025, 800000, 2026) ==
    "CONFIRMED_NEW_HOUSE_SALE")
# 6) 无建设信号的旧房成交 → NOT_NEWBUILD(非 UNRESOLVED)
chk("无建设信号 → NOT_NEWBUILD_SALE",
    CT(None, None, 1920, None, None, None, 20000, 2024) ==
    "NOT_NEWBUILD_SALE")
# 7) 许可签发 ≠ 竣工:permit_pre 但无任何建成/突变证据
#    jy=None → CONSTRUCTION_STAGE(许可在前)
chk("permit 签发≠竣工(无突变) → STAGE",
    CT(None, None, 1920, 2024, None, None, None, 2025) ==
    "CONSTRUCTION_STAGE_SALE")
# 8) 估值跳涨 ≠ 竣工:staged 大但 jy>sy + old yb → OLD
chk("跳涨≠竣工:jy>sy+旧yb → OLD",
    CT(None, 2026, 1920, None, None, 2026, 900000, 2024) ==
    "OLD_HOUSE_OR_LAND_SALE")
# 9) 建设证据强但无 ≤sy 房龄 → PROBABLE(停留候选)
chk("permit+突变≤sy 缺竣工证据 → PROBABLE",
    CT(None, 2025, 1920, 2024, None, 2024, 900000, 2025) ==
    "PROBABLE_NEW_HOUSE_SALE")
# 10) 同年顺序未知 → UNRESOLVED(不臆断)
chk("评估年与成交同年+无其他证据 → UNRESOLVED",
    CT(None, None, None, None, None, 2025, 900000, 2025) ==
    "UNRESOLVED_TEMPORAL_ORDER")

# ---- SALE 证据分级 ----
chk("无日期/价格 → SALE_UNVERIFIED",
    SE({"sale_date": "", "sale_price": ""}) ==
    "SALE_UNVERIFIED")
chk("AMBIGUOUS tax_link → SALE_CONFLICT",
    SE({"sale_date": "2024-01-01", "sale_price": "1e6",
        "tax_link": "AMBIGUOUS", "n_sources": "2"}) ==
    "SALE_CONFLICT")
chk("n_sources=1 → SINGLE_SOURCE",
    SE({"sale_date": "2024-01-01", "sale_price": "1e6",
        "tax_link": "SINGLE", "n_sources": "1"}) ==
    "SALE_SINGLE_SOURCE")
chk("n_sources=2 → CONFIRMED",
    SE({"sale_date": "2024-01-01", "sale_price": "1e6",
        "tax_link": "SINGLE", "n_sources": "2"}) ==
    "SALE_SOURCE_CONFIRMED")

# ---- 四级门 ----
chk("CONFIRMED+双源 → VERIFIED",
    GL("CONFIRMED_NEW_HOUSE_SALE", "SALE_SOURCE_CONFIRMED",
       2000, "FIRST_SALE", "VERIFIED", True) ==
    "PRICING_COMPARABLE")
chk("CONFIRMED+单源 → 停在 LIKELY",
    GL("CONFIRMED_NEW_HOUSE_SALE", "SALE_SINGLE_SOURCE",
       2000, "FIRST_SALE", "VERIFIED", True) ==
    "LIKELY_NEW_AT_SALE")
chk("PROBABLE → LIKELY",
    GL("PROBABLE_NEW_HOUSE_SALE", "SALE_SOURCE_CONFIRMED",
       2000, "RESALE", "VERIFIED", True) ==
    "LIKELY_NEW_AT_SALE")
chk("无 fa → VERIFIED 不升 PRICING",
    GL("CONFIRMED_NEW_HOUSE_SALE", "SALE_SOURCE_CONFIRMED",
       None, "FIRST_SALE", "VERIFIED", True) ==
    "VERIFIED_NEW_AT_SALE")
chk("OLD 成交 → DETECTED 不超 LIKELY",
    GL("OLD_HOUSE_OR_LAND_SALE", "SALE_SOURCE_CONFIRMED",
       2000, "RESALE", "VERIFIED", True) ==
    "DETECTED")
chk("sale_kind UNKNOWN → 不升 PRICING",
    GL("CONFIRMED_NEW_HOUSE_SALE", "SALE_SOURCE_CONFIRMED",
       2000, "UNKNOWN", "VERIFIED", True) ==
    "VERIFIED_NEW_AT_SALE")
chk("duplex_half 单元 → 不升 PRICING(scope)",
    GL("CONFIRMED_NEW_HOUSE_SALE", "SALE_SOURCE_CONFIRMED",
       2000, "FIRST_SALE", "VERIFIED", True, False) ==
    "VERIFIED_NEW_AT_SALE")

# ---- 2023 prev 基线语义 ----
m22 = M.jump_metrics([(2022, 100000), (2023, 700000)])
chk("2022 基线接入 → jump 检出", m22["jump_year"] == 2023)
chk("无基线单点 → None → NO_BASELINE",
    M.jump_grade(M.jump_metrics([(2023, 700000)]), None)
    == "NO_BASELINE_ASSESSMENT")

# ---- 血统一对多不自动合并 ----
G = {"p1": [{"succ": "a", "key_ev": "PLAN_LOT",
             "status": "CONFIRMED"},
            {"succ": "b", "key_ev": "PLAN_LOT",
             "status": "SPLIT_CANDIDATE"}]}
chk("一对多图:CONFIRMED+SPLIT 不并单元",
    M.lineage_successors(G, "p1") == ["a"] or
    M.lineage_successors(G, "p1") == [])
chk("单 CONFIRMED 边 → 后继并入",
    M.lineage_successors(
        {"p": [{"succ": "s", "key_ev": "PLAN_LOT",
                "status": "CONFIRMED"}]}, "p") == ["s"])

# ---- or True 恒真断言自检(ast,防自匹配) ----
import ast
tree = ast.parse(Path(__file__).read_text())
bad = [n for n in ast.walk(tree)
       if isinstance(n, ast.Call) and
       getattr(n.func, "id", "") == "chk" and
       any(isinstance(a, ast.BoolOp) and
           isinstance(a.op, ast.Or) and
           any(isinstance(v, ast.Constant) and
               v.value is True for v in a.values)
           for a in n.args)]
chk("测试文件不含 or True 恒真断言", not bad)

# ---- 真实数据守恒 ----
per = OUTD / "house_timeline_per_row.csv"
if per.exists():
    rows = list(csv.DictReader(open(per)))
    chk("house rows == 5386", len(rows) == 5386)
    chk("canonical_tx 唯一",
        len({r["canonical_tx"] for r in rows}) == 5386)
    c128 = [r for r in rows
            if r["census_class"] == "CONFIRMED_AGE_00_03"]
    chk("128 基线守恒且全部 CONFIRMED",
        len(c128) == 128 and all(
            r["concl"] == "CONFIRMED_NEW_HOUSE_SALE"
            for r in c128))
    g = {r["dim"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "newbuild_sale_four_level_gate.csv"))}
    chk("四级门单调(累计)",
        g["DETECTED_CUM"] >= g["LIKELY_NEW_AT_SALE_CUM"]
        >= g["VERIFIED_NEW_AT_SALE_CUM"]
        >= g["PRICING_COMPARABLE_CUM"])
    chk("OLD_HOUSE 成交不得进 VERIFIED",
        not any(r["concl"] == "OLD_HOUSE_OR_LAND_SALE" and
                r["level"] in ("VERIFIED_NEW_AT_SALE",
                               "PRICING_COMPARABLE")
                for r in rows))
    chk("成交后建成不计新房:POST-sale permit + jump>sy"
        " 不在 CONFIRMED",
        not any(r["concl"] == "CONFIRMED_NEW_HOUSE_SALE" and
                r["permit_pre"] == "" and
                r["yb_le_sy"] == "" for r in rows))
    rev = list(csv.DictReader(
        open(OUTD / "citywide_reverse_per_row.csv")))
    dedup = {r["layer"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "reverse_504_dedup_waterfall.csv"))}
    chk("dedup 层单调:rows≥tx≥pid≥proj",
        dedup["MATCH_ROWS"] >= dedup["UNIQUE_CANONICAL_TX"]
        >= dedup["UNIQUE_SALE_PID"] >=
        dedup["UNIQUE_PROJECTS"])
    chk("无成交行不带 canonical_tx",
        all(not r["canonical_tx"] for r in rev
            if r.get("no_sale")))
    man = json.loads((DOCS / "manifest.json").read_text())
    chk("manifest house==5386", man["counts"]["house"] == 5386)
else:
    chk("管线已产出 house_timeline_per_row.csv", False)

# ---- V3s 零修改(生产估值/ROI/排名脚本) ----
V3 = ["scripts/v3s_consistency_gate.py",
      "scripts/render_v3s_shadow.py",
      "src/northshore_watchdog/research/strategy_v2_shadow.py",
      "data/research/normalized/sales_archive_v1_0_3"]
br = subprocess.run(
    ["git", "-C", str(ROOT), "branch", "--list",
     "research/house-newbuild-discovery-v2-1*"],
    capture_output=True, text=True).stdout.split()
if br:
    v3 = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         "29653ced1200c4d45c490bff3a2c04d1b58269a3", br[0],
         "--"] + V3, capture_output=True,
        text=True).stdout.strip()
    chk("V3s 生产文件与冻结档案零修改(基线↔分支)", v3 == "")
else:
    chk("V3s 分支存在可检", False)

# ---- 双 subprocess 幂等 ----
if "--run" in sys.argv:
    subprocess.run([sys.executable,
                    str(ROOT / "scripts" /
                        "house_newbuild_timeline_gate_v2_1.py")],
                   capture_output=True)
    m1 = json.loads((DOCS / "manifest.json").read_text())
    m1.pop("run", None)
    subprocess.run([sys.executable,
                    str(ROOT / "scripts" /
                        "house_newbuild_timeline_gate_v2_1.py")],
                   capture_output=True)
    m2 = json.loads((DOCS / "manifest.json").read_text())
    m2.pop("run", None)
    chk("双 subprocess 幂等", m1 == m2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
