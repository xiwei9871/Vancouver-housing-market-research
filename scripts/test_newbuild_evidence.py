#!/usr/bin/env python3
"""newbuild_evidence_v1 验收断言 — 幂等/守恒/去重/不外泄。"""
import csv, hashlib, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTD = ROOT / "data/research/normalized/audit_v1"
V3S = ROOT / "data/research/normalized/valuation-v3s-shadow-2026-10-22.json"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def rows(name):
    return list(csv.DictReader(open(OUTD / name)))

fails = []

def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok:
        fails.append(name)

# V3s 输入指纹(运行前后不变)
h0 = sha(V3S)

# ---- 幂等:连跑两次,产物指纹一致 ----
subprocess.run([sys.executable, "scripts/newbuild_evidence_v1.py"],
               cwd=ROOT, check=True, capture_output=True)
snap1 = {f: sha(OUTD / f) for f in
         ["project_registry.csv", "unit_registry.csv", "sales_evidence.csv",
          "coverage_waterfall.csv", "regional_benchmark.csv",
          "shadow_impact.csv"]}
subprocess.run([sys.executable, "scripts/newbuild_evidence_v1.py"],
               cwd=ROOT, check=True, capture_output=True)
snap2 = {f: sha(OUTD / f) for f in snap1}
check("idempotent_outputs", snap1 == snap2,
      str({k: v == snap2[k] for k, v in snap1.items()}))

# ---- 守恒:comp_records = asof_ok + x_asof ----
bad = [w for w in rows("coverage_waterfall.csv")
       if w["comp_records"] and w["asof_ok"] and w["x_asof"]
       and int(w["comp_records"]) != int(w["asof_ok"]) + int(w["x_asof"])]
check("waterfall_conservation", not bad, str(bad[:2]))

# ---- 项目/单元/交易三层分离 ----
proj = rows("project_registry.csv"); units = rows("unit_registry.csv")
sales = rows("sales_evidence.csv")
check("three_layer_counts", len(proj) > 0 and len(units) > 0
      and len(sales) > 0 and len(units) != len(sales),
      f"proj={len(proj)} units={len(units)} sales={len(sales)}")

# ---- permit 去重稳定 ----
pn = [p["project_id"] for p in proj]
check("permit_dedup", len(pn) == len(set(pn)),
      f"dup={len(pn)-len(set(pn))}")

# ---- verified 层无恢复/缺 PID 记录 ----
bad_v = [s for s in sales if s["tier"] == "verified"
         and (s["yb_recovered"] == "True" or not s["pid"])]
check("verified_pool_pure", not bad_v, str(bad_v[:2]))

# ---- V3s 零修改 ----
check("v3s_unchanged", sha(V3S) == h0)

# ---- benchmark 不外泄:recent12 新鲜度不等于当前价 ----
# (fresh 字段存在即为逐格标注)
check("bench_freshness_labeled",
      all(r.get("fresh") for r in rows("regional_benchmark.csv")))

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
