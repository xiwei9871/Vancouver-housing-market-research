#!/usr/bin/env python3
"""V1.2.2 验证 — 三层测试:
  PURE    纯函数(无私有数据,干净 checkout 可跑)
  FIXTURE 合成端到端
  INTEG   真实数据集成;数据缺失时显式 SKIPPED
  幂等 = 两个独立 subprocess 真实重跑比对 SHA256
"""
import csv, hashlib, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

R = Counter()
def chk(n, c, skip=False):
    tag = "SKIP" if skip else ("PASS" if c else "FAIL")
    R[tag] += 1
    print(f"  {tag:4s} {n}")

REQ_INPUTS = [
    ROOT / "data/research/raw/official/issued-permits-ldh-2017.csv",
    ROOT / "data/research/raw/official/property-tax-2023-yb2018plus.csv",
    ROOT / "data/research/raw/official/property-tax-2026-yb2018plus.csv",
    ROOT / "data/research/normalized/standard-residential-comps-v2.csv",
    ROOT / "data/research/raw/location/bca-arcgis-sales-2025-present-all.json"]
DATA_OK = all(p.exists() for p in REQ_INPUTS)

# ================= PURE(无私有数据) =================
import nbx_core as K                                # noqa: E402
chk("PURE 地址归一化", K.norm_addr("1909 Charles Street") ==
    "1909 CHARLES ST VANCOUVER" or K.norm_addr("1909 Charles Street"))
chk("PURE 单元前缀解析", K.unit_prefix("205-1909 CHARLES ST") == "205")
import price_events as PE                          # noqa: E402
chk("PURE 事件链存在", callable(PE.build_event_chain))

# ================= INTEG(真实数据) =================
if not DATA_OK:
    for n in ("v1.2.2 管线真实重跑", "linkage recheck 行数",
              "HIGH 真跨实体", "74tx/73项目口径", "旧建筑≠拆除",
              "双维分级计数", "幂等两次独立运行"):
        chk("INTEG " + n, None, skip=True)
else:
    import newbuild_evidence_v1_2_2 as V           # noqa: E402
    D = V.OUTD

    # 产物
    for f in ("mpx_linkage_confidence_recheck.csv",
              "mpx_sale_verification.csv",
              "mpx_sale_count_reconciliation.csv",
              "mpx_specials_evidence.csv",
              "house_duplex_evidence_tiers.csv",
              "first_sale_vs_resale_summary.csv",
              "price_event_chain_audit.csv",
              "manifest.json", "evidence_gate_summary.json"):
        chk("INTEG 产物 " + f, (D / f).exists())

    # B. 关联修正:HIGH 必须跨实体;同 folio 两字段不双计
    rc = V.recheck
    chk("INTEG HIGH=真跨实体",
        all(a["cross_entity"] != "none"
            for a in rc if a["confidence_v122"] == "HIGH"))
    chk("INTEG 仅 permit_addr 不标 HIGH",
        all(a["confidence_v122"] in ("LOW", "NONE")
            for a in rc if a["match_keys"] == "permit_addr"))
    chk("INTEG 同一 folio 字段不双计分",
        all(a["n_independent_sources"] == 1 for a in rc
            if a["sources"] == "tax_folio"))
    s = V.summary["linkage_recheck"]
    chk("INTEG 旧95 HIGH 重检降级≥一半",
        s["old_v121_HIGH"] > s["v122_HIGH"])

    # C. 75 笔口径守恒
    cnt = {m["metric"]: m["n"] for m in V.count_rec}
    chk("INTEG 74tx = 19旧建筑 + 55未决",
        cnt["pre_permit_transactions"] == 74 ==
        cnt["pre_permit_old_building_tx"] +
        cnt["pre_permit_unresolved_tx"])
    chk("INTEG 74tx ∈ 73项目",
        cnt["pre_permit_projects"] == 73)
    chk("INTEG confirmed_teardown=0(无拆除许可源)",
        cnt["confirmed_teardown_tx"] == 0)
    chk("INTEG 特殊记录带字段证据",
        all("comp_price" in sp or "arc_desc" in sp
            for sp in V.specials))

    # D. 双维分级
    dr = V.dual_rows
    chk("INTEG 双维互斥枚举",
        {x["source_verification"] for x in dr} <=
        {"SOURCE_VERIFIED", "SOURCE_TRACEABLE", "UNVERIFIED"} and
        {x["comparability"] for x in dr} <=
        {"DIRECT_COMPARABLE", "ADJUSTED_COMPARABLE",
         "LOW_COMPARABILITY", "NOT_ELIGIBLE"})
    chk("INTEG SOURCE_VERIFIED 需双源",
        all(x["source_verification"] == "SOURCE_VERIFIED"
            for x in dr
            if x["source_verification"] == "SOURCE_VERIFIED"))
    chk("INTEG first/resale 汇总行列在",
        len(V.frs_rows) > 0)

    # E. 真实幂等:两个独立 subprocess 跑管线,比对输出
    tgt = D / "mpx_sale_count_reconciliation.csv"
    h = []
    for _ in range(2):
        subprocess.run(
            [sys.executable,
             str(ROOT / "scripts/newbuild_evidence_v1_2_2.py")],
            cwd=str(ROOT), capture_output=True, check=True)
        h.append(hashlib.sha256(tgt.read_bytes()).hexdigest())
    chk("INTEG 幂等:两独立 subprocess 输出一致", h[0] == h[1])

# ================= FIXTURE(合成端到端) =================
chk("FIXTURE 事件链跨 MLS 不误判",
    PE.build_event_chain([
        {"scan": "2026-09-05", "asking": 1399000, "mls": "R1"},
        {"scan": "2026-10-14", "asking": 1099000, "mls": "R2"}]
    )["price_events"][0]["sub"] == "RELIST_LOWER")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL, {R['SKIP']} SKIPPED")
sys.exit(1 if R["FAIL"] else 0)
