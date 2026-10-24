#!/usr/bin/env python3
"""Regression tests for the V1.4.5 consistency audit deliverables."""
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/audits/listing_consistency_v1_4_5"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail if not ok else "")


def rc(p):
    return list(csv.DictReader(open(p)))


def main():
    if "--run" in sys.argv:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts"
                                   "/listing_consistency_audit_v1_4_5.py")],
            check=True)

    findings = rc(OUT / "consistency_findings.csv")
    summ = rc(OUT / "listing_consistency_summary.csv")
    pairs = rc(OUT / "comparable_pair_anomalies.csv")
    bridge = rc(OUT / "valuation_bridge.csv")
    zmat = rc(OUT / "zoning_rule_matrix.csv")
    zc = rc(OUT / "zoning_conflicts.csv")
    c09 = rc(OUT / "c09_r11_duplex_conditional.csv")
    bt = rc(OUT / "pricing_backtest.csv")
    bts = rc(OUT / "pricing_backtest_summary.csv")

    rules = Counter(f["rule"] for f in findings)
    check("c01_trigger_7", rules["C01"] == 7, rules["C01"])
    check("c03_trigger_4", rules["C03"] == 4, rules["C03"])
    check("c09_r11_conditional_17", len(c09) == 17, len(c09))
    c02_low = [f for f in findings if f["rule"] == "C02"
               and "fair/bca_land=0." in f["key_fields"]
               and float(re.search(r"fair/bca_land=(\d+\.\d+)",
                                   f["key_fields"]).group(1)) < 0.8]
    check("c02_low_3", len(c02_low) == 3, len(c02_low))
    for a in ("1194 E 18TH AVENUE", "48 E 22ND AVENUE",
              "3707 INVERNESS STREET", "4766 GOTHARD STREET"):
        check(f"c01_focus_{a[:10]}",
              any(a in f["listing"] and f["rule"] == "C01"
                  for f in findings))
    check("c05_48e22nd",
          any("48 E 22ND" in b["listing"] for b in bridge))
    check("c06_slocan_dundas_pair",
          any({"75 N SLOCAN STREET", "2461 DUNDAS STREET"} <=
              {p["listing_a"], p["listing_b"]} for p in pairs))
    check("findings_unique_ids",
          len({f["finding_id"] for f in findings}) == len(findings))
    check("summary_224", len(summ) == 224)
    check("summary_roi_basis",
          sum(1 for s in summ if s["best_roi"]
              and float(s["best_roi"]) >= 10) == 74 and
          sum(1 for s in summ if s["best_roi"]
              and float(s["best_roi"]) >= 30) == 2)
    check("zc_672", len(zc) == 672)
    check("zc_status_split",
          {"VERIFIED", "LEGAL_UNVERIFIED", "CONDITIONAL_REVIEW",
           "LEGAL_REVIEW_REQUIRED"}
          >= {r["legal_status"] for r in zc} and
          "PRICE_NOT_EVALUATED" in {r["price_status"] for r in zc})
    check("zmat_covers_zones", len(zmat) >= 20 and
          any(r["zoning"] == "R1-1" for r in zmat))
    check("backtest_rows", len(bt) >= 30 and all(
        r["A_500M_FIRST_psf"] for r in bt))
    check("backtest_methods",
          {r["method"] for r in bts} ==
          {"A_500M_FIRST", "B_COMM_NEARBY_ADJ", "C_COMM_WEIGHTED"})
    check("report", (OUT / "audit_report.md").exists())
    man = json.load(open(OUT / "manifest.json"))
    check("manifest_inputs_hashed",
          len(man["inputs_sha256"]) == 6 and
          all(len(v) == 64 for v in man["inputs_sha256"].values()))
    check("every_finding_has_evidence_or_note",
          all(f["note"] for f in findings))

    fails = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(fails)} PASS / {len(fails)} FAIL")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
