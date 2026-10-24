#!/usr/bin/env python3
"""Sales Archive V1.0.3 集成回归。

覆盖验收:
1) duplex_scope:strata lot 证据压过 is_strata=N 标志;
   IS_STRATA=N 单独不构成整栋证明;同 parcel 邻户成交→
   SPLIT_CANDIDATE;范围不明→SCOPE_UNKNOWN
2) MPX 三标志独立:unit/sale 有证据但无项目类型描述→
   仍 CANDIDATE;Strata Townhouse/Apartment 描述不升级;
   Multiple Residence 才是项目证据
3) 房龄 folio 语义:无 roll 关联不赋 FOLIO_CONFIRMED
4) 真实数据验收(有冻结档案时):
   - 2023/24 CONDO/TOWNHOUSE 保留
   - 1909 CHARLES=WHOLE VERIFIED+RESALE;3122 MARINE 非 MPX
   - MPX VERIFIED 必须三标志全真
   - 覆盖表/房龄矩阵守恒;覆盖率标签含 cutoff=2026-07-08
   - 双 subprocess 幂等

运行:python3 scripts/test_sales_archive_v1_0_3.py
"""
import csv, json, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import archive_pipeline as AP                          # noqa: E402

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


# ============ 1. duplex_scope ============
sc, v, ev = AP.duplex_scope(
    False, "2 STY Duplex-Semi-Custom", "1", "SL1", "032-510-535",
    strata_plan="EPS11431")
chk("is_strata=N 但有 strata plan+lot → HALF_VERIFIED+冲突标注",
    sc == "DUPLEX_HALF_VERIFIED" and v == "VERIFIED" and
    "flag_conflict" in ev)
sc, v, ev = AP.duplex_scope(
    False, "2 STY Duplex-Semi-Custom", "", "", "P-1")
chk("非strata+duplex描述+pid+无strata证据 → WHOLE_VERIFIED",
    sc == "DUPLEX_WHOLE_VERIFIED" and v == "VERIFIED")
sc, v, _ = AP.duplex_scope(False, "", "", "", "P-1")
chk("IS_STRATA=N 单独不构成整栋证明 → SCOPE_UNKNOWN",
    sc == "DUPLEX_SCOPE_UNKNOWN")
sc, v, _ = AP.duplex_scope(
    True, "duplex", "1", "SL1", "P-1", has_strata_neighbor=True)
chk("同parcel邻户成交 → SPLIT_CANDIDATE 不自动升级",
    sc == "DUPLEX_SPLIT_CANDIDATE" and v == "CANDIDATE")
sc, v, _ = AP.duplex_scope(None, "duplex", "", "", "P-1")
chk("strata 状态不明 → SCOPE_UNKNOWN",
    sc == "DUPLEX_SCOPE_UNKNOWN" and v == "CANDIDATE")
sc, v, _ = AP.duplex_scope(
    True, "duplex", "2", "SL2", "P-2")
chk("strata+unit+pid → HALF_VERIFIED",
    sc == "DUPLEX_HALF_VERIFIED" and v == "VERIFIED")
sc, v, _ = AP.duplex_scope(
    True, "duplex", "2", "SL2", "")
chk("strata+unit 无pid → HALF 候选级",
    sc == "DUPLEX_HALF" and v == "CANDIDATE")

# ============ 2. MPX 三标志独立 ============
fl = AP.mpx_flags(True, False, True)
chk("unit+sale 有证据但无项目证据 → 仍 CANDIDATE",
    fl["UNIT_IDENTITY_VERIFIED"] and
    fl["SALE_EVENT_VERIFIED"] and
    not fl["PROJECT_TYPE_VERIFIED"] and not fl["MPX_VERIFIED"])
chk("Strata Townhouse 描述不构成 multiplex 项目证据",
    not any(k in "strata townhouse" for k in AP.MULTI_DESC))
chk("Strata Apartment 描述不构成 multiplex 项目证据",
    not any(k in "strata apartment" for k in AP.MULTI_DESC))
chk("Multiple Residence 是项目证据",
    any(k in "multiple residence" for k in AP.MULTI_DESC))
fl = AP.mpx_flags(True, True, True)
chk("三标志全真 → MPX_VERIFIED", fl["MPX_VERIFIED"])
fl = AP.mpx_flags(True, True, False)
chk("成交证据缺失 → CANDIDATE", not fl["MPX_VERIFIED"])

# ============ 3. 房龄 folio 语义 ============
yb, t = AP.resolve_age(2024, "", {}, None, 2001)
chk("无roll关联原生yb → ADDRESS_INFERRED(不抬级)",
    t == "AGE_ADDRESS_INFERRED")
yb, t = AP.resolve_age(2024, "", {}, None, 2001, False, True)
chk("roll/folio 关联 → FOLIO_CONFIRMED",
    t == "AGE_FOLIO_CONFIRMED")

# ============ 4. 真实数据验收 ============
OUTD = ROOT / "data/research/normalized/sales_archive_v1_0_3"
if (OUTD / "sales_archive.csv").exists():
    sales = list(csv.DictReader(open(OUTD / "sales_archive.csv")))
    cy = Counter(s["sale_date"][:4] for s in sales)
    chk("2023/24 CONDO 保留(>3000/年)",
        sum(1 for s in sales if s["product"] == "CONDO" and
            s["sale_date"].startswith("2023")) > 3000 and
        sum(1 for s in sales if s["product"] == "CONDO" and
            s["sale_date"].startswith("2024")) > 3000)
    chk("2023/24 TOWNHOUSE 保留(>500/年)",
        sum(1 for s in sales if s["product"] == "TOWNHOUSE" and
            s["sale_date"].startswith("2023")) > 500 and
        sum(1 for s in sales if s["product"] == "TOWNHOUSE" and
            s["sale_date"].startswith("2024")) > 500)

    rec = list(csv.DictReader(
        open(OUTD / "multiplex_project_identity_recheck.csv")))
    ch = [r for r in rec if "CHARLES" in r["address"]]
    chk("1909 CHARLES=MPX_WHOLE VERIFIED+RESALE+老房龄",
        len(ch) == 1 and ch[0]["product"] == "MULTIPLEX_WHOLE"
        and ch[0]["verification"] == "VERIFIED" and
        ch[0]["sale_kind"] == "RESALE" and
        ch[0]["age_band"] == "AGE_51_PLUS")
    chk("3122 MARINE 不在 MPX 复核表(非 MPX)",
        not any("MARINE" in r["address"] for r in rec))
    chk("所有 VERIFIED 行三标志全真",
        all(r["unit_identity_verified"] == "True" and
            r["project_type_verified"] == "True" and
            r["sale_event_verified"] == "True"
            for r in rec if r["verification"] == "VERIFIED"))
    chk("Strata Townhouse/Apartment 描述行无一 VERIFIED",
        all(r["verification"] != "VERIFIED" for r in rec
            if r["desc"] in ("Strata Townhouse",
                             "Strata Apartment")))

    dx = list(csv.DictReader(
        open(OUTD / "duplex_scope_reconciliation.csv")))
    bysc = Counter(r["scope"] for r in dx)
    chk("duplex 范围分层非空:WHOLE/HALF/SPLIT/UNKNOWN",
        bysc["DUPLEX_WHOLE_VERIFIED"] > 0 and
        bysc["DUPLEX_HALF_VERIFIED"] > 0 and
        bysc["DUPLEX_SPLIT_CANDIDATE"] > 0)
    chk("duplex recon 行数 == canonical 中 duplex 行数",
        len(dx) == sum(1 for s in sales
                       if s["product"].startswith("DUPLEX")))

    cov = list(csv.DictReader(
        open(OUTD / "source_coverage_2023_2026.csv")))
    chk("覆盖表 canon 合计 == canonical",
        sum(int(r["canonical_sales"]) for r in cov) ==
        len(sales))
    chk("2025 不标 COVERED(全部 PARTIAL)",
        all("COVERED:" not in r["status"] for r in cov
            if r["year"] == "2025" and r["status"]))
    chk("2026 行均含 cutoff=2026-07-08",
        all("cutoff=2026-07-08" in r["status"] for r in cov
            if r["year"] == "2026" and
            r["status"].startswith("PARTIAL")))
    mat = list(csv.DictReader(
        open(OUTD / "transactions_by_year_product_age.csv")))
    chk("房龄矩阵合计 == canonical",
        sum(int(r["n_tx"]) for r in mat) == len(sales))
    age = list(csv.DictReader(
        open(OUTD / "age_evidence_semantics_audit.csv")))
    chk("age 语义审计合计 == canonical",
        sum(int(r["n"]) for r in age) == len(sales))

    hp = list(csv.DictReader(
        open(OUTD / "house_prices_by_community_age_year.csv")))
    chk("HOUSE 价格组 n_tx 合计 == canonical HOUSE 数",
        sum(int(r["n_tx"]) for r in hp) ==
        sum(1 for s in sales if s["product"] == "HOUSE"))
    chk("AGE_UNKNOWN 组全部 REFERENCE_ONLY",
        all(r["estimable"] == "REFERENCE_ONLY" for r in hp
            if r["age_band"] == "AGE_UNKNOWN"))
    chk("estimable=YES 组 n_strict 均 >=10",
        all(int(r["n_strict"]) >= 10 for r in hp
            if r["estimable"] == "YES"))
    chk("每行带 coverage_state",
        all(r["coverage_state"] for r in hp))

    # 双 subprocess 幂等(manifest counts)
    script = ROOT / "scripts/sales_archive_v1_0_3.py"
    m0 = json.loads((OUTD / "manifest.json").read_text())[
        "counts"]
    run = subprocess.run([sys.executable, str(script)],
                         capture_output=True, text=True,
                         timeout=900)
    chk("ETL 重跑退出0", run.returncode == 0)
    m1 = json.loads((OUTD / "manifest.json").read_text())[
        "counts"]
    chk("重跑幂等:counts 完全一致", m0 == m1)
else:
    print("  (skip 真实数据验收:冻结档案缺失)")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
