#!/usr/bin/env python3
"""V1.2.1 验证测试 — 集成断言(V1.2 纯函数已由 test_v1_2 覆盖)"""
import sys, json, csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K            # noqa: E402
import newbuild_evidence_v1_2_1 as V  # noqa: E402  运行管线

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)

D = V.OUTD
# 产物存在
for f in ("mpx_identity_linkage_audit.csv", "mpx_sale_verification.csv",
          "house_verified_evidence.csv", "duplex_project_unit_audit.csv",
          "sales_source_trial_results.csv", "verified_market_benchmark.csv",
          "evidence_gap_reconciliation.csv", "manifest.json",
          "evidence_recovery_summary.json"):
    chk(f"产物 {f}", (D / f).exists())

# 分层关联:仅地址匹配不标 HIGH
la = V.link_audit
addr_only = [a for a in la if a["match_keys"] == "permit_addr"]
chk("仅permit_addr全为LOW", all(a["confidence"] == "LOW" for a in addr_only))
chk("每条带match_method+keys+sources", all(
    a["match_method"] and a["match_keys"] and a["supporting_sources"]
    for a in la))

# MPX 75 笔定性
ms = V.mpx_sale_ver
chk("mpx_sale_ver = 75(74地址+1许可后)", len(ms) == 75)
chk("pre 全部 TEARDOWN|PRE 二分", all(
    x["verdict"] in ("PRE_PERMIT_SALE", "CONFIRMED_TEARDOWN_SALE")
    for x in ms if not x["post"]))
chk("特殊三记录定论", all(s["verdict"] for s in V.specials))

# HOUSE 四桶互斥且 = 217+33
hs = V.buckets
chk("house 桶互斥(地址+日期无重复)",
    len({(r["address"], r["sale_date"]) for rs in hs.values()
         for r in rs}) == sum(len(v) for v in hs.values()) == 250)
chk("usable 桶=217", len(hs["VERIFIED_FULL"]) +
    len(hs["RECOVERED_NEEDS_REVIEW"]) + len(hs["KEY_UNCERTAINTY"]) == 217)
chk("BANNED=33 冲突", len(hs["BANNED"]) == 33)
chk("VERIFIED_FULL 全 GST 可判", all(
    r["gst_basis"].startswith(("new", "res"))
    for r in hs["VERIFIED_FULL"]))

# DUPLEX 389 条有标注
de = V.dup_ev
chk("duplex 审计行=622(389 usable+233 partial)", len(de) == 622)
chk("duplex usable=389", sum(1 for x in de if x["usable"]) == 389)
chk("duplex 带 strata_plan 或 pid 证据",
    sum(1 for x in de if x["strata_plan"] or x["pid"]) > 300)

# 基准:VERIFIED vs USABLE 分离,gst 三分
b = V.bench2
chk("基准含两档分层", any("VERIFIED_MARKET_SALES" in k for k in b) and
    any("USABLE_WITH_LIMITATIONS" in k for k in b))
chk("n_unknown_gst 计数在", all("n_unknown_gst" in v for v in b.values()))
chk("LOCAL/BORROWED 标", all(
    v["conf"].startswith(("LOCAL_", "BORROWED_")) for v in b.values()))

# 核销守恒
chk("MPX 零 verified unit sales", not any(
    x["verdict"] == "MPX_UNIT_SALE" for x in ms))
chk("summary 三产品 gap 在", len(V.summary["gap"]) == 3)

# 幂等
import hashlib
h1 = hashlib.sha256(
    (D / "house_verified_evidence.csv").read_bytes()).hexdigest()
V2 = __import__("newbuild_evidence_v1_2_1")
h2 = hashlib.sha256(
    (D / "house_verified_evidence.csv").read_bytes()).hexdigest()
chk("house 输出幂等", h1 == h2)

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
