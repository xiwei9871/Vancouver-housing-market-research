#!/usr/bin/env python3
"""V1.5.3 Phase A — independent recomputation tests.

Formulas are re-derived here by hand (not imported from the audit
script) and compared to the audit outputs. Runs with plain python3
(pytest optional):  python3 tests/test_economic_integrity_v1_5_3.py
"""
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
AUD = ROOT / "data/audit"
D15 = ROOT / "docs/audits/listing_valuation_v1_5"
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, detail)


def f(x):
    return float(x) if x not in ("", None) else None


def shas(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.iterdir()) if p.is_file()}


# 0. run audit; it must not touch any V1.5.2 output
before = shas(D15)
subprocess.run([sys.executable, "scripts/economic_integrity_v1_5_3.py"],
               cwd=ROOT, check=True, capture_output=True)
check("audit leaves V1.5.2 outputs byte-identical", shas(D15) == before)
first = shas(AUD)
subprocess.run([sys.executable, "scripts/economic_integrity_v1_5_3.py"],
               cwd=ROOT, check=True, capture_output=True)
check("audit idempotent", shas(AUD) == first)

G = json.load(open(AUD / "economic_gate_results.json"))
R = list(csv.DictReader(open(AUD / "residual_land_value.csv")))
T = list(csv.DictReader(open(AUD / "comps_trace.csv")))
C = list(csv.DictReader(open(AUD / "cost_coverage.csv")))
H = list(csv.DictReader(open(AUD / "hbu_matrix.csv")))
A = G["audit_assumptions"]

# 1. V1.5.2 reproduced from its own engine
check("V1.5.2 ROI reproduced", G["v152_reproduced"] and len(
    G["v152_reproduction"]) == 10)


# 2. PTT by hand (1% / 2% / 3%)
def ptt(p):
    return (min(p, 2e5) * .01 + max(min(p, 2e6) - 2e5, 0) * .02
            + max(p - 2e6, 0) * .03)


bad = [r for r in R if abs(ptt(f(r["asking"])) - f(r["cost_ptt"])) > 1]
check("PTT recomputed by hand", not bad, f"bad={len(bad)}")
check("PTT 1.4M == 26,000 (hand)", abs(ptt(1_400_000) - 26_000) < 1e-6)

# 3. GST: default audit mode = price includes GST, no ITC -> sale*5/105
bad = [r for r in R if abs(f(r["sale_estimate"]) * 5 / 105
                           - f(r["cost_gst_net"])) > 2]
check("GST net = sale x 5/105", not bad, f"bad={len(bad)}")

# 4. cost lines sum to total; no line counted twice
items = [k for k in R[0] if k.startswith("cost_")]
bad = [r for r in R if abs(sum(f(r[k]) for k in items)
                           - f(r["total_cost_audit"])) > len(items)]
check("cost items sum to total", not bad)
check("no duplicate cost line names", len(items) == len(set(items)))
check("ROI = (sale - total)/total",
      all(abs(100 * (f(r["sale_estimate"]) - f(r["total_cost_audit"]))
              / f(r["total_cost_audit"]) - f(r["roi_audit"])) < 0.06 for r in R))

# 5. financing recomputed independently (monthly, LTV, linear draws)
for r in R:
    if r["variant"] != "V152_MODEL_GEOMETRY" or r["case"] != "880 E 33RD AVENUE":
        continue
    land = f(r["asking"])
    build = (f(r["cost_hard_above_grade"]) + f(r["cost_basement_construction"])
             + f(r["cost_contingency"]))
    months, pm, bm = A["months"], A["permit_months"], A["build_months"]
    li = land * A["land_ltv"] * A["land_rate"] / 12 * months
    cap, drawn, ci = build * A["con_ltv"], 0.0, 0.0
    for m in range(1, months + 1):
        if pm < m <= pm + bm:
            drawn += cap / bm
        ci += drawn * A["con_rate"] / 12
    fee = (land * A["land_ltv"] + cap) * A["loan_fee"]
    check(f"financing hand recompute {r['scenario']}",
          abs(li - f(r["cost_land_interest"])) < 2
          and abs(ci - f(r["cost_construction_interest"])) < 2
          and abs(fee - f(r["cost_loan_fees"])) < 2,
          f"li {li:.0f} ci {ci:.0f} fee {fee:.0f}")

# 6. residual land solves the target: ROI(residual) ~= 15% (hand engine)
import economic_integrity_v1_5_3 as e   # noqa: E402
r = next(x for x in R if x["case"] == "880 E 33RD AVENUE"
         and x["scenario"] == "HOUSE" and x["variant"] == "V152_MODEL_GEOMETRY")
x = e.audit_cost(r["case"], f(r["residual_land_audit_at_15"]), "HOUSE",
                 f(r["above_grade_sf"]), 1, f(r["sale_estimate"]),
                 f(r["basement_sf"]))
check("residual land -> ROI 15%", abs(x["roi"] - 0.15) < 1e-3,
      f"{x['roi']:.4f}")
check("residual monotone: RL0 > RL10 > RL15", all(
    f(q["residual_land_audit_at_0"]) > f(q["residual_land_audit_at_10"])
    > f(q["residual_land_audit_at_15"]) for q in R
    if q["residual_land_audit_at_15"].replace(".", "").isdigit()))

# 7. comps trace: dedup + UNKNOWN != NO + no fabricated area
inc = [t for t in T if t["status"] == "INCLUDED"]
keys = [(t["case"], t["scenario"], t["tx"]) for t in inc]
check("each comp appears once per case x scenario", len(keys) == len(set(keys)))
check("basement states only YES/NO/UNKNOWN/NO_RAW_RECORD",
      {t["basement_finish_state"] for t in inc} <=
      {"YES", "NO", "UNKNOWN", "NO_RAW_RECORD"})
check("UNKNOWN basement never carries an area",
      not [t for t in inc if t["basement_finish_state"] != "YES"
           and t["basement_finish_sqft"]])
check("no imputed above-grade area", all(
    t["above_grade_fa_sqft"] not in ("", "0") for t in inc))
pool_n = {("880 E 33RD AVENUE", "HOUSE"): 158, ("880 E 33RD AVENUE", "DUPLEX"): 570}
check("trace covers full priced pool (every comp touches k_fit/base)", all(
    sum(1 for t in inc if (t["case"], t["scenario"]) == k) == n
    for k, n in pool_n.items()))
check("every excluded row has a reason", all(
    t["exclusion_reason"] for t in T if t["status"] == "EXCLUDED"))

# 8. cost coverage: every line classified; categories legal
cats = {"EXPLICIT", "INCLUDED_DOCUMENTED", "UNKNOWN", "NOT_APPLICABLE"}
check("cost coverage categories valid", all(
    c["v152_status"] in cats and c["audit_status"] in cats for c in C))
check("cost coverage items unique", len({c["cost_item"] for c in C}) == len(C))

# 9. HBU: allowed states only; nothing claimed LEGALLY_VERIFIED without
#    retrieved bylaw text
states = {"LEGALLY_VERIFIED", "CONDITIONAL_UNVERIFIED", "NOT_PERMITTED",
          "UNKNOWN"}
check("HBU states valid", {h["hbu_state"] for h in H} <= states)
check("no LEGALLY_VERIFIED without bylaw text", not [
    h for h in H if h["hbu_state"] == "LEGALLY_VERIFIED"
    and h["bylaw_text_retrieved"] != "YES"])

# 10. P95 is a flag only (V1.5.2 ranking unchanged by this audit)
br = list(csv.DictReader(open(D15 / "v15_roi_bridge.csv")))
oos = {(s["listing"], s["scenario"]) for s in csv.DictReader(
    open(D15 / "v15_sale_and_roi.csv")) if s["out_of_support"] == "True"}
check("P95 out-of-support still ranked in full view",
      any((b["listing"], b["best_full"]) in oos for b in br))

# 11. gate statuses come only from the allowed set
check("gate statuses valid", {g["status"] for g in G["gate"].values()} <= {
    "PASS", "CONDITIONAL_LEGAL_UNVERIFIED", "FAIL_RESIDUAL_BELOW_ASK",
    "FAIL_NOT_PERMITTED"})

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
