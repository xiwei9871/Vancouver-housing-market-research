#!/usr/bin/env python3
"""Free Evidence Integration V1 测试 — PURE/FIXTURE/INTEG 三层"""
import csv, subprocess, sys, hashlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

R = Counter()
def chk(n, c, skip=False):
    tag = "SKIP" if skip else ("PASS" if c else "FAIL")
    R[tag] += 1
    print(f"  {tag:4s} {n}")

BF = ROOT / "data/research/normalized/audit_v1_2_2/bca_yb_backfill_results.csv"
DATA_OK = BF.exists() and (ROOT / "data/research/raw/official/"
                           "issued-permits-ldh-2017.csv").exists()

# ---------- PURE ----------
import nbx_core as K  # noqa: E402
chk("PURE 单元前缀识别", K.unit_prefix("1-5438 MAIN ST") == "1")
chk("PURE 无前缀返回空", not K.unit_prefix("5438 MAIN ST"))

# ---------- FIXTURE ----------
sys.path.insert(0, str(ROOT / "scripts"))
from free_evidence_integration_v1 import UNIT_RE  # noqa: E402
chk("FIXTURE 单元正则", bool(UNIT_RE.match("1-5438 MAIN ST"))
    and not UNIT_RE.match("20 26TH AVE E"))

# ---------- INTEG ----------
if not DATA_OK:
    for n in ("829身份核销", "12候选漏斗", "24冲突隔离",
              "MPX四态", "幂等subprocess"):
        chk("INTEG " + n, None, skip=True)
else:
    import free_evidence_integration_v1 as V
    D = V.OUTD
    for f in ("bca_829_identity_reconciliation.csv",
              "newbuild_12_candidate_verification.csv",
              "temporal_conflict_24_audit.csv",
              "bca_floor_area_quality_summary.csv",
              "mpx_offline_match_reconciliation.csv",
              "coverage_before_after_free_recovery.csv",
              "manifest.json"):
        chk("INTEG 产物 " + f, (D / f).exists())

    recon = V.recon
    chk("INTEG 829 行", len(recon) == 829)
    chk("INTEG 恒等式 786+12+24+7",
        sum(V.summary["cat"].values()) == 829)
    chk("INTEG 身份四态枚举",
        {r["identity"] for r in recon} <=
        {"IDENTITY_PID_CONFIRMED", "IDENTITY_SINGLE_CANDIDATE",
         "IDENTITY_UNRESOLVED", "OUT_OF_SCOPE_MUNICIPALITY"})
    chk("INTEG pid 冲突为 0",
        all(r["identity"] != "IDENTITY_UNRESOLVED"
            or r["unique_candidate"] for r in recon))

    cand = V.cand_rows
    chk("INTEG 12 候选", len(cand) == 12)
    chk("INTEG Burnaby×2 排除",
        sum(1 for c in cand if "EXCLUDE_MUNICIPALITY"
            in c["exclusions"]) == 2)
    chk("INTEG strata×3 排除",
        sum(1 for c in cand if "EXCLUDE_STRATA_UNIT"
            in c["exclusions"]) == 3)
    chk("INTEG 全部 sale_verified=UNKNOWN",
        all("UNKNOWN" in c["sale_verified"] for c in cand))
    chk("INTEG 合格均标记待人工",
        all(c["decision"] == "ELIGIBLE_PENDING_MANUAL_SALE_CHECK"
            for c in cand if c["eligible_comparable"]))

    chk("INTEG 24 冲突全隔离",
        all(r["status"] == "QUARANTINED_NOT_TEARDOWN"
            for r in V.conf_rows) and len(V.conf_rows) == 24)
    mpx = {r["category"] for r in V.mpx_off}
    chk("INTEG MPX 四态合法", mpx <= {"NO_MATCH_IN_CURRENT_DATA",
        "MATCHED_NO_SALE", "UNIT_IDENTITY_UNKNOWN", "CONFIRMED_SALE"})
    chk("INTEG MPX 零 CONFIRMED_SALE",
        sum(1 for r in V.mpx_off
            if r["category"] == "CONFIRMED_SALE") == 0)

    # 真幂等:两次独立 subprocess
    tgt = D / "bca_829_identity_reconciliation.csv"
    hs = []
    for _ in range(2):
        subprocess.run(
            [sys.executable,
             str(ROOT / "scripts/free_evidence_integration_v1.py")],
            cwd=str(ROOT), capture_output=True, check=True)
        hs.append(hashlib.sha256(tgt.read_bytes()).hexdigest())
    chk("INTEG 幂等:双 subprocess SHA256 一致", hs[0] == hs[1])

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL, {R['SKIP']} SKIPPED")
sys.exit(1 if R["FAIL"] else 0)
