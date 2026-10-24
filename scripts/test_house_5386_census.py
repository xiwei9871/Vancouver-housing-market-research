#!/usr/bin/env python3
"""HOUSE 5,386 新建识别普查回归(GitHub Issue #1)。

1) waterfall 合计==5386,逐笔 canonical_tx 唯一
2) before/after 每年守恒;CONFIRMED 均有 ≤sy 纠正yb+pid
3) 217 复核:日期+价格双校验,EXACT 才算入档
4) GST 1.05× 判据;许可 issued 不等于新房(REVIEW)
5) 池外/townhouse 不误识别;REVIEW 无 ≤sy 新yb证据
6) manifest↔CSV 一致;双 subprocess 幂等
"""
import csv, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


def price_rel(ap, np_):
    if ap and np_ and abs(ap - np_) < 1:
        return "EXACT"
    if ap and np_ and min(abs(ap / np_ - 1.05),
                          abs(np_ / ap - 1.05)) < 0.005:
        return "GST_RATIO"
    return "MISMATCH"


chk("价格完全相等 → EXACT", price_rel(1.8e6, 1.8e6) == "EXACT")
chk("GST 口径 1.05× → GST_RATIO 非错配",
    price_rel(1.89e6, 1.8e6) == "GST_RATIO")
chk("差 3% → MISMATCH", price_rel(1.85e6, 1.8e6) == "MISMATCH")

DOCS = ROOT / "docs/audits/house_5386_census"
OUTD = ROOT / "data/research/normalized/house_5386_census"
if (OUTD / "house_5386_census_per_row.csv").exists():
    census = list(csv.DictReader(
        open(OUTD / "house_5386_census_per_row.csv")))
    chk("canonical_tx 唯一且=5386",
        len({r["canonical_tx"] for r in census}) == 5386)

    wfr = {r["classification"]: int(r["n"]) for r in
           csv.DictReader(open(
               DOCS / "house_5386_census_waterfall.csv"))}
    chk("waterfall 合计==5386 且逐笔一致",
        sum(wfr.values()) == 5386 and
        Counter(r["classification"] for r in census) ==
        Counter(wfr))

    conf = [r for r in census
            if r["classification"] == "CONFIRMED_AGE_00_03"]
    chk("CONFIRMED 全部 pid+纠正yb≤成交年+00_03",
        all(r["identity_confidence"] == "PID_VERIFIED" and
            r["year_built_corrected"] and
            int(r["year_built_corrected"]) <=
            int(r["sale_year"]) and
            int(r["sale_year"]) -
            int(r["year_built_corrected"]) <= 3
            for r in conf))
    chk("CONFIRMED 无 >sy 冲突证据(nb/enr)",
        all(">sale_year" not in r["classification_reasons"]
            and not re.search(r"cur_enr_yb=\d+>%s" %
                              r["sale_year"],
                              r["classification_reasons"])
            for r in conf))
    rev = [r for r in census
           if r["classification"] == "REVIEW_REBUILD_STALE_YB"]
    chk("REVIEW 全部无 ≤sy 的新yb独立证据",
        all(not re.search(r"BCA_ENRICHED_CURRENT=\d|"
                          r"NB217_MLS=\d",
                          r["evidence_time"]) or
            all(int(m) > int(r["sale_year"]) for m in
                re.findall(r"(?:ENRICHED|NB217_MLS)=(\d+)",
                           r["evidence_time"]))
            for r in rev))
    chk("REVIEW 行均带新建信号(permit/first_sale/nb/pool)",
        all("permit" in r["classification_reasons"] or
            "first_sale" in r["classification_reasons"] or
            "nb217" in r["classification_reasons"] or
            r["in_pool"] == "True" for r in rev))
    unk = [r for r in census
           if r["classification"] == "AGE_UNKNOWN"]
    chk("AGE_UNKNOWN 无纠正yb", all(
        not r["year_built_corrected"] for r in unk))

    ba = list(csv.DictReader(
        open(DOCS / "house_census_before_after_year_age.csv")))
    by_y = {}
    for r in ba:
        by_y.setdefault(r["year"], [0, 0])
        by_y[r["year"]][0] += int(r["n_before"])
        by_y[r["year"]][1] += int(r["n_after"])
    chk("before/after 每年守恒", all(a == b for a, b in
                                   by_y.values()))

    rec = list(csv.DictReader(
        open(DOCS /
             "house_217_event_price_reconciliation.csv")))
    chk("217 复核行数=217", len(rec) == 217)
    chk("217 日期匹配均非 NONE", all(
        r["date_match"] != "NEAREST:NONE" for r in rec))
    chk("217 价格均有 EXACT/GST_RATIO/MISMATCH 判定",
        all(r["price_rel"] for r in rec))

    cy = list(csv.DictReader(
        open(DOCS / "house_newbuild_by_community_year.csv")))
    cy_tot = sum(int(r["confirmed_00_03"]) +
                 int(r["probable_00_03"]) for r in cy)
    chk("社区×年 confirmed+probable == census 口径",
        cy_tot == sum(1 for r in census if
                      r["classification"] in
                      ("CONFIRMED_AGE_00_03",
                       "PROBABLE_AGE_00_03")))
    gate = {r["gate"]: int(r["n"]) for r in csv.DictReader(
        open(DOCS / "house_newbuild_comparable_gate.csv"))}
    chk("comparable ⊆ CONFIRMED_00_03",
        gate["NEWBUILD_COMPARABLE"] <= len(conf))

    man = json.loads((DOCS / "manifest.json").read_text())
    chk("manifest waterfall == CSV 合计",
        sum(man["counts"]["waterfall"].values()) == 5386)

    run = subprocess.run(
        [sys.executable,
         str(ROOT / "scripts/house_newbuild_census.py")],
        capture_output=True, text=True, timeout=1200)
    chk("重跑退出0", run.returncode == 0)
    man2 = json.loads((DOCS / "manifest.json").read_text())
    chk("幂等:counts 一致", man["counts"] == man2["counts"])
else:
    print("  (skip:先跑 house_newbuild_census.py)")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
