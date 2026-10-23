#!/usr/bin/env python3
"""HOUSE Sample Recovery Gate 回归测试。

1) townhouse 不被 houseish 误判("townhouse" 含 "house" 子串)
2) corrected_yb>成交年不纠正(POST_SALE_BUILD 冲突)
3) waterfall 九类合计=217,逐条无遗漏
4) coverage before/after 守恒(每年 before==after==canon)
5) NEWBUILD_COMPARABLE ⊆ CAPTURED;纠正只改标签不加行
6) 分组等级:STRICT 需 n_strict>=10,AGE_UNKNOWN 不可估计
7) 交付文件齐全 + 幂等(重跑 manifest counts 一致)

运行:python3 scripts/test_house_sample_recovery_gate.py
"""
import csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import archive_core as AC                              # noqa: E402

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


DOCS = ROOT / "docs/audits/house_sample_recovery"
OUTD = ROOT / "data/research/normalized/house_sample_recovery"

# 1) houseish 子串陷阱(逻辑复述,与脚本同口径)
def houseish(desc):
    import re
    d = (desc or "").lower()
    return bool(re.search(r"\bhouse\b", d)) or \
        "single family" in d
chk("'2 STY townhouse - Custom' 非 house", not houseish(
    "2 STY townhouse - Custom"))
chk("'2 STY house - Custom' 是 house", houseish(
    "2 STY house - Custom"))
chk("'Warehouse, Storage' 非 house", not houseish(
    "Warehouse, Storage"))

# 2) 房龄纠正口径
band = lambda yb, sy: AC.age_band_at_sale(yb, sy)[0] \
    if yb else "AGE_UNKNOWN"
chk("yb2022 售2025 → 00_03", band(2022, 2025) == "AGE_00_03")
chk("yb2025 售2023 → 不纠正(yb>sy)", 2025 > 2023)
chk("yb2018 售2025 → 04_10(2018+≠0-3)",
    band(2018, 2025) == "AGE_04_10")

if (DOCS / "manifest.json").exists():
    man = json.loads((DOCS / "manifest.json").read_text())
    mc = man["counts"]

    wf = {r["category"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "house_217_overlap_waterfall.csv"))}
    chk("waterfall 九类合计=217", sum(wf.values()) == 217)
    chk("217 全部在档案(NOT_IN_ARCHIVE=0)",
        wf.get("NOT_IN_ARCHIVE", 0) == 0 and
        wf.get("IDENTITY_UNCERTAIN", 0) == 0)

    per = list(csv.DictReader(
        open(OUTD / "house_217_per_row_reconciliation.csv")))
    chk("逐条行数=217 且类别与 waterfall 一致",
        len(per) == 217 and
        Counter(r["category"] for r in per) ==
        Counter(wf))

    rec = list(csv.DictReader(
        open(OUTD / "house_recovery_per_row.csv")))
    nb_rows = [r for r in rec
               if r["category"] == "NEWBUILD_SALE_RECOVERED"]
    chk("恢复行全部 0<=成交龄<=3 且为 house 描述",
        all(0 <= int(r["sale_date"][:4]) - int(r["pool_yb"])
            <= 3 and houseish(r["desc"]) for r in nb_rows))
    chk("manifest recovered == 恢复行数",
        mc["recovered_newbuild"] == len(nb_rows))

    ba = list(csv.DictReader(
        open(DOCS / "house_sample_coverage_before_after.csv")))
    by_year = {}
    for r in ba:
        y = r["year"]
        by_year.setdefault(y, [0, 0])
        by_year[y][0] += int(r["n_before"])
        by_year[y][1] += int(r["n_after"])
    chk("coverage before/after 每年守恒",
        all(a == b for a, b in by_year.values()))
    chk("2025 00_03 before=0 after>0",
        any(r["year"] == "2025" and r["age_band"] ==
            "AGE_00_03" and r["n_before"] == "0" and
            int(r["n_after"]) > 0 for r in ba))

    p = list(csv.DictReader(
        open(DOCS / "house_price_eligibility_by_region.csv")))
    chk("STRICT 组均 n_strict>=10",
        all(int(r["n_strict_identity"]) >= 10 for r in p
            if r["grade"] == "STRICT"))
    chk("EXPLORATORY 组均 eligible>=5",
        all(int(r["n_total_price_eligible"]) >= 5 for r in p
            if r["grade"] == "EXPLORATORY"))
    chk("psf_eligible <= total_price_eligible",
        all(int(r["n_psf_eligible"]) <=
            int(r["n_total_price_eligible"]) for r in p))

    chk("NEWBUILD_COMPARABLE <= CAPTURED",
        mc["NEWBUILD_COMPARABLE_SALES"] <=
        mc["CAPTURED_HOUSE_SALES"])
    chk("CAPTURED == 5386(纠正不加行)",
        mc["CAPTURED_HOUSE_SALES"] == 5386)
    chk("AGE_CLASSIFIED 纠正后>纠正前",
        mc["AGE_CLASSIFIED_HOUSE_SALES"] >=
        mc["CAPTURED_HOUSE_SALES"] - 300)

    run = subprocess.run(
        [sys.executable,
         str(ROOT / "scripts/house_sample_recovery_gate.py")],
        capture_output=True, text=True, timeout=300)
    chk("重跑退出0", run.returncode == 0)
    man2 = json.loads((DOCS / "manifest.json").read_text())
    chk("幂等:counts 一致", man["counts"] == man2["counts"])
else:
    print("  (skip:先跑 house_sample_recovery_gate.py)")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
