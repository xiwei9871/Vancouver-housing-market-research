#!/usr/bin/env python3
"""V1.2 测试 — 合成 fixture 在干净 checkout 可跑(只依赖 nbx_core);
真实数据在时做集成断言。"""
import csv, json, subprocess, sys, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K   # 纯函数,无私有数据依赖  # noqa: E402

OUTD = ROOT / "data/research/normalized/audit_v1_2"
fails = []


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok:
        fails.append(name)


# ---- A. fixture(无私有数据) ----
check("warehouse_not_house", K.desc_verdict("Strata Warehouse")[0] == "OTHER")
check("mpx_desc", K.desc_verdict("Multiple Residence")[0] ==
      "CONFIRMED_MULTIPLEX")
check("th_desc", K.desc_verdict("Strata Townhouse")[0] == "TOWNHOUSE")
check("dir_addr", K.norm_addr("1185 E 17TH AVENUE") == "1185 17TH AVE E")
check("unit_pref", K.unit_prefix("204-3189 CAMOSUN ST") == "204")
check("base_addr", K.base_addr("204-3189 CAMOSUN ST") == "3189 CAMOSUN ST")
check("mpx_units_parse", K.parse_units(
    "three-storey two-unit front building and one-unit rear") == 3)
check("r1_mpx_zone", K.zone_product("R1-1") == "HOUSE_OR_MPX_R1")
check("rc_split", K.rc_split("Renfrew-Collingwood", "49.246") == "Renfrew(北)")
check("class_no_catchall",
      K.CLASS_MAP["STRATA_UNIT_UNKNOWN"][0] == "STRATA_UNKNOWN")
# tier:recovered yb 不给 VERIFIED_COMPARABLE
t = K.sale_tier("HOUSE", 3e6, "2025-01-01", "001", "CONSISTENT",
                2021, 2500, yb_src="enriched")
check("tier_recovered_partial", t == "PARTIAL_EVIDENCE", t)
t = K.sale_tier("HOUSE", 3e6, "2025-01-01", "001", "CONSISTENT",
                2021, 2500)
check("tier_verified", t == "VERIFIED_COMPARABLE", t)
t = K.sale_tier("STRATA_UNKNOWN", 3e6, "2025-01-01", "001",
                "CONSISTENT", 2021, 2500)
check("tier_unknown_prod", t == "UNVERIFIED", t)

# ---- B. 集成(真实产物) ----
if (OUTD / "evidence_recovery_summary.json").exists():
    S = json.loads((OUTD / "evidence_recovery_summary.json").read_text())
    rows = lambda n: list(csv.DictReader(open(OUTD / n)))
    # B1 HOUSE yb 恒等式
    y = rows("house_yb_reconciliation.csv")
    miss = sum(int(v) for k, v in S["yb_recon"].items())
    check("yb_identity", miss == len(y) == 2351,
          f"cats={miss} rows={len(y)}")
    # B2 时点冲突不进 usable
    conf = {r["address"] for r in y if r["conflict"]}
    se = rows("sales_evidence.csv")
    check("conflict_excluded",
          not any(r["address"] in conf for r in se), f"{len(conf)} 冲突")
    # B3 MPX 链式核销:teardown+post+strata+only = 项目总数
    lk = rows("mpx_project_unit_linkage_summary.csv")
    check("mpx_linkage_total", len(lk) == S["mpx"]["permit_projects"] == 340)
    check("mpx_zero_unit", S["mpx"]["ZERO_VERIFIED_MPX_UNIT_SALES_IN_CAPTURED_DATA"])
    # B4 duplex 配对核销:同 pid 重售 ≠ 两户
    da = rows("duplex_unit_identity_audit.csv")
    check("dup_groups", len(da) == 553)
    check("dup_resale_distinct",
          any(r["verdict"] == "RESALE_SAME_UNIT" for r in da))
    # B5 瀑布=证据数
    wf = rows("coverage_waterfall.csv")
    tot = sum(int(r["usable_newbuild"] or 0) for r in wf
              if r["product"] in ("HOUSE", "DUPLEX", "MPX"))
    check("sales_eq_wf", len(se) == tot, f"{len(se)} vs {tot}")
    # B6 基准血缘字段齐
    bm = rows("regional_benchmark_v1_2.csv")
    need = {"n_sales", "n_projects", "n_first_sale", "n_resale",
            "n_unknown_gst", "confidence", "fresh"}
    check("bench_lineage", bm and need <= set(bm[0].keys()),
          str(need - set(bm[0].keys()) if bm else "empty"))
    check("bench_conf_split",
          any(r["confidence"].startswith("LOCAL_") for r in bm) and
          any(r["confidence"].startswith("BORROWED_") for r in bm))
    # B7 许可↔项目核销
    pr = rows("permit_project_reconciliation.csv")
    check("permit_proj", sum(int(r["n_permits"]) for r in pr) == 9944)
    # B8 幂等
    h1 = {f: hashlib.sha256((OUTD / f).read_bytes()).hexdigest()
          for f in ["sales_evidence.csv", "regional_benchmark_v1_2.csv",
                    "mpx_misclassification_audit.csv",
                    "duplex_unit_identity_audit.csv",
                    "house_yb_reconciliation.csv"]}
    subprocess.run([sys.executable, "scripts/newbuild_evidence_v1_2.py"],
                   cwd=ROOT, check=True, capture_output=True)
    h2 = {f: hashlib.sha256((OUTD / f).read_bytes()).hexdigest() for f in h1}
    check("idempotent", h1 == h2)
else:
    print("SKIP integration (no private data) — fixtures only")

print(f"\n{len(fails)} failed")
sys.exit(1 if fails else 0)
