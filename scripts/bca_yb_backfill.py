#!/usr/bin/env python3
"""BCA 免费逐户回填 829 条 NO_SOURCE 建成年份/面积 — 个人非商业用途。
公开端点(GetByAddress→PropertyInformation),礼貌限速,增量可续跑。
输出:data/research/normalized/audit_v1_2_2/bca_yb_backfill_results.csv
"""
import csv, json, sys, time, random, datetime
from pathlib import Path

import requests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from northshore_watchdog.research.official_enrichment import (  # noqa: E402
    _parse_bca_search_candidates, parse_bc_assessment_payload)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/research/normalized/audit_v1_2/house_yb_reconciliation.csv"
OUT = ROOT / "data/research/normalized/audit_v1_2_2/bca_yb_backfill_results.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)

FIELDS = ["address", "date", "price", "status", "bca_year_built",
          "bca_first_floor", "bca_second_floor", "bca_basement",
          "bca_description", "bca_pid", "bca_oid", "n_candidates",
          "sales_history", "error"]

S = requests.Session()
S.headers.update({"User-Agent":
                  "northshore-property-watchdog/0.1 (+personal-research)"})


def lookup(addr):
    r = S.get("https://www.bcassessment.ca/Property/Search/GetByAddress",
              params={"addr": addr}, timeout=30)
    cands = _parse_bca_search_candidates(r.content)
    if not cands:
        return {"status": "NO_MATCH", "n_candidates": 0}
    oid = cands[0].get("oid") or cands[0].get("value")
    time.sleep(0.8)
    d = S.post("https://www.bcassessment.ca/Property/PropertyInformation",
               data={"oid": oid}, timeout=30)
    rows = parse_bc_assessment_payload(d.content, "bca_detail",
                                       datetime.datetime.now())
    f = rows[0] if rows else {}
    sh = f.get("sales_history") or ""
    return {"status": "OK",
            "n_candidates": len(cands), "bca_oid": oid,
            "bca_year_built": f.get("year_built") or "",
            "bca_first_floor": f.get("first_floor_area") or "",
            "bca_second_floor": f.get("second_floor_area") or "",
            "bca_basement": f.get("basement_finish_area") or "",
            "bca_description": f.get("description") or "",
            "bca_pid": f.get("pid") or "",
            "sales_history": json.dumps(sh)[:600] if sh else ""}


def main():
    todo = [r for r in csv.DictReader(open(SRC)) if r["cat"] == "NO_SOURCE"]
    done = set()
    if OUT.exists():
        for r in csv.DictReader(open(OUT)):
            done.add((r["address"], r["date"]))
    mode = "a" if done else "w"
    n_ok = n_blank = n_nomatch = n_err = 0
    with open(OUT, mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if mode == "w":
            w.writeheader()
        for i, r in enumerate(todo, 1):
            if (r["address"], r["date"]) in done:
                continue
            try:
                got = lookup(r["address"])
                if got.get("status") == "OK":
                    if got.get("bca_year_built"):
                        n_ok += 1
                    else:
                        n_blank += 1
                elif got.get("status") == "NO_MATCH":
                    n_nomatch += 1
            except Exception as e:
                got = {"status": "ERR", "error": str(e)[:120]}
                n_err += 1
            w.writerow({"address": r["address"], "date": r["date"],
                        "price": r["price"], **got})
            f.flush()
            if i % 25 == 0:
                print(f"[{i}/{len(todo)}] ok={n_ok} blank={n_blank} "
                      f"nomatch={n_nomatch} err={n_err}", flush=True)
            time.sleep(1.6 + random.random())
    print(f"DONE ok={n_ok} blank={n_blank} nomatch={n_nomatch} "
          f"err={n_err}")


if __name__ == "__main__":
    main()
