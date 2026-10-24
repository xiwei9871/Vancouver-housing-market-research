#!/usr/bin/env python3
"""HOUSE Newbuild V2.2 回归——直接调生产函数。

覆盖:许可≠竣工;跳涨≠竣工;买家重建≠新房成交;
评估滞后不倒灌;同年未知→UNRESOLVED;一对多不合并;
多命中去重;prev 基线;GST/价/面积独立门;
守恒;or True 自检;V3s 零修改;双 subprocess 幂等。
"""
import ast, csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import house_newbuild_evidence_pricing_v2_2 as M    # noqa: E402
import house_newbuild_timeline_gate_v2_1 as V      # noqa: E402

DOCS = ROOT / "docs/audits/house_newbuild_v2_2"
OUTD = ROOT / "data/research/normalized/newbuild_v2_2"
R = Counter()


def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


ED = M.evidence_dims_v2
PG = M.pricing_gates
GB = M.gst_basis
P = lambda iy, d="2024-01-01": {"iy": iy, "date": d,
                                "cat": "Single Detached House",
                                "desc": ""}

# ---- 许可签发 ≠ 竣工 ----
d = ED(None, False, P(2024), None, None, 2025)
chk("仅许可 → 竣工 NONE",
    d["COMPLETION_OR_OCCUPANCY_EVIDENCE"] == "NONE")
# ---- 跳涨 ≠ 竣工 ----
d = ED(None, False, None, {"year": 2025, "delta": 600000},
       None, 2025)
chk("仅跳涨 → 竣工 NONE(施工≠竣工)",
    d["COMPLETION_OR_OCCUPANCY_EVIDENCE"] == "NONE" and
    d["CONSTRUCTION_STATUS_EVIDENCE"] ==
    "ASSESSED_IMPROVEMENT_JUMP")
# ---- 新 yb 但无跳涨 → 仅房龄支持 ----
d = ED(2024, True, None, None, 2024, 2025)
chk("新yb无跳涨 → NEW_BUILDING_AGE_SUPPORTED",
    d["COMPLETION_OR_OCCUPANCY_EVIDENCE"] ==
    "NEW_BUILDING_AGE_SUPPORTED")
# ---- 新yb+大跳涨 → ASSESSMENT_COMPLETION(非 OCCUPANCY) ----
d = ED(2024, True, P(2023), {"year": 2024, "delta": 600000},
       2024, 2025)
chk("yb+跳涨 → ASSESSMENT_COMPLETION",
    d["COMPLETION_OR_OCCUPANCY_EVIDENCE"] ==
    "ASSESSMENT_COMPLETION" and
    d["SALE_AFTER_NEW_BUILDING_EVIDENCE"] is True)
chk("任何输入不得产 OCCUPANCY_VERIFIED",
    all(ED(a, b, c, e, f, g)
        ["COMPLETION_OR_OCCUPANCY_EVIDENCE"] !=
        "OCCUPANCY_VERIFIED" for a, b, c, e, f, g in
        [(2024, True, P(2023),
          {"year": 2024, "delta": 9e5}, 2024, 2025),
         (None, False, None, None, None, 2025)]))
# ---- 评估滞后:yb≤sy 即 SALE_AFTER_NEW ----
d = ED(2025, True, P(2024), {"year": 2026, "delta": 800000},
       2025, 2025)
chk("成交后评估才显化仍认新建筑(yb≤sy)",
    d["SALE_AFTER_NEW_BUILDING_EVIDENCE"] is True)
# ---- 买家重建:无 ≤sy 新yb → 非新房成交 ----
d = ED(None, True, None, {"year": 2025, "delta": 800000},
       2025, 2024)
chk("jy>sy 且无 ≤sy 新yb → SALE_AFTER_NEW=False",
    d["SALE_AFTER_NEW_BUILDING_EVIDENCE"] is False)
# ---- 时序五类(V2.1 生产函数复用) ----
chk("permit_post → OLD", V.classify_timeline(
    None, 2025, 1920, None, 2025, 2026, 900000, 2024) ==
    "OLD_HOUSE_OR_LAND_SALE")
chk("同年未知 → UNRESOLVED", V.classify_timeline(
    None, None, None, None, None, 2025, 900000, 2025) ==
    "UNRESOLVED_TEMPORAL_ORDER")

# ---- 血统:多后继不再自动 CONFIRMED ----
chk("单一强候选 → CONFIRMED",
    M.lineage_successors_v2(
        {"p": [{"succ": "s", "key_ev": "PLAN_LOT",
                "status": "CONFIRMED"}]}, "p") == ["s"])
G = {"p": [{"succ": "a", "key_ev": "PLAN_LOT",
            "status": "CONFIRMED"},
           {"succ": "b", "key_ev": "PLAN_LOT",
            "status": "SPLIT_CANDIDATE"}]}
chk("多后继(混 CONFIRMED+SPLIT) → 不合并",
    M.lineage_successors_v2(G, "p") == [])
G2 = {"p": [{"succ": "a", "key_ev": "FOLIO",
             "status": "SPLIT_CANDIDATE"},
            {"succ": "b", "key_ev": "FOLIO",
             "status": "SPLIT_CANDIDATE"}]}
chk("duplex 双 strata 后继 → 不合并",
    M.lineage_successors_v2(G2, "p") == [])

# ---- GST/价/面积门 ----
g = GB("FIRST_SALE", "bca_arcgis+comps_v2")
chk("FIRST_SALE → UNKNOWN_LIKELY_INCLUDED+5%情景",
    g["gst_basis"] == "UNKNOWN_LIKELY_INCLUDED" and
    g["price_incl_gst_est"] is True)
g = GB("RESALE", "x")
chk("RESALE → UNKNOWN_LIKELY_EXCLUDED",
    g["gst_basis"] == "UNKNOWN_LIKELY_EXCLUDED")
gg = PG("SALE_SOURCE_CONFIRMED", True, 2000, True, True,
        {"gst_basis": "UNKNOWN"}, True)
chk("全门 → READY", gg["NEWBUILD_PRICING_READY"] is True)
gg = PG("SALE_SINGLE_SOURCE", True, 2000, True, True,
        {"gst_basis": "UNKNOWN"}, True)
chk("单源 → SALE_VERIFIED 不过 → 不 READY",
    gg["SALE_VERIFIED"] is False and
    gg["NEWBUILD_PRICING_READY"] is False)
gg = PG("SALE_SOURCE_CONFIRMED", True, 662, True, True,
        {"gst_basis": "UNKNOWN"}, False)
chk("fa<1200/不可比 → PSF 不过",
    gg["PSF_COMPARABLE"] is False)
gg = PG("SALE_SOURCE_CONFIRMED", False, 2000, True, True,
        {"gst_basis": "UNKNOWN"}, True)
chk("无新房证据 → NH 门不过 → 不 READY",
    gg["NEWBUILD_PRICING_READY"] is False)
gg = PG("SALE_SOURCE_CONFIRMED", True, 2000, True, False,
        {"gst_basis": "UNKNOWN"}, True)
chk("非 freehold → TOTAL_PRICE 不过",
    gg["TOTAL_PRICE_COMPARABLE"] is False)

# ---- 面积口径:fa vs pool first+second floor 25% 容差 ----
# (在 analyze 内,这里验函数层 fa_ok 逻辑等价实现见主脚本)
chk("prev 基线仍接", V.jump_metrics(
    [(2022, 100000), (2023, 700000)])["jump_year"] == 2023)

# ---- 真实数据守恒 ----
per = OUTD / "house_v2_2_per_row.csv"
if per.exists():
    rows = list(csv.DictReader(open(per)))
    chk("house==5386 唯一", len(rows) == 5386 and
        len({r["canonical_tx"] for r in rows}) == 5386)
    wf = {r["layer"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "candidate_589_identity_waterfall.csv"))}
    chk("瀑布守恒:UNIQUE=IN128+NEW",
        wf["UNIQUE_TX"] == wf["ALREADY_IN_128"] +
        wf["NEW_CANDIDATES"])
    chk("瀑布:concl 分项=UNIQUE",
        wf["CONCL_CONFIRMED"] + wf["CONCL_PROBABLE"] +
        wf["CONCL_STAGE"] + wf["CONCL_UNRESOLVED"] ==
        wf["UNIQUE_TX"])
    chk("OCCUPANCY_VERIFIED 全局为 0(无源)",
        not any(r["dim_COMPLETION_OR_OCCUPANCY_EVIDENCE"] ==
                "OCCUPANCY_VERIFIED" for r in rows))
    rec = list(csv.DictReader(open(
        DOCS / "newbuild_111_gst_scope_reconciliation.csv")))
    chk("READY ⊆ PSF ⊆ TOTAL ⊆ SALE ⊆ NH",
        all(not (r["gate_NEWBUILD_PRICING_READY"] == "True"
                 and (r["gate_PSF_COMPARABLE"] != "True" or
                      r["gate_TOTAL_PRICE_COMPARABLE"] != "True"
                      or r["gate_SALE_VERIFIED"] != "True" or
                      r["gate_NEW_HOUSE_STATUS_VERIFIED"]
                      != "True"))
            for r in rec))
    man = json.loads((DOCS / "manifest.json").read_text())
    chk("manifest pricing_ready==ready 行数",
        man["counts"]["pricing_ready"] ==
        sum(1 for r in rows
            if r["gate_NEWBUILD_PRICING_READY"] == "True"))
else:
    chk("管线产出存在", False)

# ---- or True 自检 ----
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

# ---- V3s 零修改 ----
br = subprocess.run(
    ["git", "-C", str(ROOT), "branch", "--list",
     "research/house-newbuild-v2-2*"],
    capture_output=True, text=True).stdout.split()
if br:
    v3 = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--name-only",
         "d2b26bc4c486025bab2206409a1678f2b4d0ee2c", br[0],
         "--", "scripts/v3s_consistency_gate.py",
         "scripts/render_v3s_shadow.py",
         "src/northshore_watchdog/research/"
         "strategy_v2_shadow.py",
         "data/research/normalized/sales_archive_v1_0_3"],
        capture_output=True, text=True).stdout.strip()
    chk("V3s/冻结档案零修改", v3 == "")
else:
    chk("V2.2 分支存在", False)

# ---- 幂等 ----
if "--run" in sys.argv:
    subprocess.run([sys.executable, str(
        ROOT / "scripts" /
        "house_newbuild_evidence_pricing_v2_2.py")],
        capture_output=True)
    m1 = json.loads((DOCS / "manifest.json").read_text())
    m1.pop("run", None)
    subprocess.run([sys.executable, str(
        ROOT / "scripts" /
        "house_newbuild_evidence_pricing_v2_2.py")],
        capture_output=True)
    m2 = json.loads((DOCS / "manifest.json").read_text())
    m2.pop("run", None)
    chk("双 subprocess 幂等", m1 == m2)

print(f"\n{R['PASS']} PASS / {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
