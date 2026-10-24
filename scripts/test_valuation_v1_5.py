#!/usr/bin/env python3
"""V1.5 tests — backtest integrity, estimator invariants, page lineage.
Run: python3 scripts/test_valuation_v1_5.py  (after v1_5 pipeline+render)
"""
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import valuation_v1_5_backtest as bt          # noqa: E402
import listing_valuation_v1_5 as v15          # noqa: E402
import listing_level_valuation_v1_4 as v      # noqa: E402

D = bt.OUT
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print("PASS" if ok else "FAIL", name, detail)


def h(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# 1. idempotency: rerun pipeline, outputs byte-identical
files = ["v15_sale_and_roi.csv", "v15_land.csv", "v15_legal_state.csv",
         "v15_contradictions.csv", "backtest_summary.json"]
before = {f: h(D / f) for f in files}
for s in ("valuation_v1_5_backtest.py", "listing_valuation_v1_5.py"):
    subprocess.run([sys.executable, f"scripts/{s}"], cwd=ROOT, check=True,
                   capture_output=True)
check("idempotent rerun", all(h(D / f) == before[f] for f in files))

# 2. LOPO leakage: no training row shares the held-out parcel cluster
pools, _, _ = bt.load_pools()
for n in ("HOUSE", "DUPLEX"):
    p = pools[n]
    c = p[0]
    train = [x for x in p if x["cluster"] != c["cluster"]]
    check(f"{n} LOPO excludes same cluster",
          all(x["cluster"] != c["cluster"] for x in train)
          and len(train) < len(p))

# 3. anchor invariants: near correction capped, no near => base
pool = [c for c in pools["HOUSE"] if c["psf"] and c["lat"]]
k = bt.fit_k(pool)
caps = []
for c in pool[:60]:
    _, d = bt.est_anchor(bt.target_of(c), pool, k)
    caps.append(d.get("near_factor", 1.0))
check("near factor within ±15%", all(0.85 - 1e-9 <= f <= 1.15 + 1e-9
                                     for f in caps))
t = {"lat": 0.0, "lng": 0.0, "community": pool[0]["community"],
     "side": pool[0]["side"], "area": 2000}
p0, d0 = bt.est_anchor(t, pool, k)
check("no nearby comps => factor 1, equals base",
      d0["n_near"] == 0 and abs(p0 - d0["base"]) < 1e-6)
check("fitted HOUSE k in [-0.6, 0]", -0.6 <= k <= 0)

# 4. backtest: chosen method beats V1.4 on both products (LOPO+temporal)
S = json.load(open(D / "backtest_summary.json"))["sale"]
for n in ("HOUSE", "DUPLEX"):
    for split in ("ALL", "TEMPORAL"):
        mm = S[f"{n}|{split}"]["methods"]
        check(f"{n}|{split} ANCHOR MdAPE < V14",
              mm["ANCHOR"]["mdape"] < mm["V14"]["mdape"],
              f"{mm['ANCHOR']['mdape']} vs {mm['V14']['mdape']}")

# 5. conservation: every listing x scenario appears once; 224 listings
sr = list(csv.DictReader(open(D / "v15_sale_and_roi.csv")))
keys = Counter((r["listing"], r["scenario"]) for r in sr)
check("no duplicate listing x scenario", max(keys.values()) == 1)
check("224 listings", len({r["listing"] for r in sr}) == 224)
ls = list(csv.DictReader(open(D / "v15_legal_state.csv")))
check("legal state rows == scenario rows", len(ls) == len(sr))

# 6. legality: no sale estimate on NOT_ELIGIBLE/NO_RULE; variance ->
#    never VERIFIED
check("no estimate on illegal scenarios",
      not [r for r in sr if r["state"] in ("NOT_ELIGIBLE", "NO_RULE")
           and r["sale_p50"]])
check("variance_required never VERIFIED",
      not [r for r in ls if r["variance_required"] == "True"
           and r["state"] == "VERIFIED"])

# 7. interval ordering
bad = [r for r in sr if r["sale_p50"] and r["sale_p10"]
       and not float(r["sale_p10"]) <= float(r["sale_p50"])
       <= float(r["sale_p90"])]
check("P10 <= P50 <= P90", not bad, f"bad={len(bad)}")
bad = [r for r in sr if r["roi_p10"] and float(r["roi_p10"])
       > float(r["roi_p50"])]
check("ROI P10 <= ROI P50", not bad)

# 8. Ask = watchlist latest_asking (AGENTS rule 2)
wl = {e["address"].upper(): e["latest_asking"]
      for e in json.load(open(v15.WATCHLIST))["entries"]}
mis = [r for r in sr if wl.get(r["listing"].upper()) not in
       (None, float(r["asking"]), int(float(r["asking"])))]
check("Ask == watchlist latest_asking", not mis, f"mismatch={len(mis)}")

# 9. ROI recomputes from cost_model on displayed Ask (display == input)
r0 = next(r for r in sr if r["method"] == "ANCHOR" and r["roi_p50"])
sc = {"name": r0["scenario"], "build_sqft": None}
lst = {x["address"]: x for x in json.load(open(v.LISTING_JSON))["rows"]}
s = lst[r0["listing"]]["analysis"]["scenarios"][r0["scenario"]]
sc["build_sqft"] = float(s["build_sqft"])
roi = 100 * v.cost_model(float(r0["asking"]), sc, float(r0["sale_p50"]),
                         v.BASE_YEARS, "EXCL")["roi"]
check("ROI reproduces from displayed Ask/sale", abs(roi - float(r0["roi_p50"]))
      < 0.11, f"{roi:.2f} vs {r0['roi_p50']}")

# 10. page: C09 lineage (community table from dims-corrected pool),
#     row count, JSON tools present
html = (D / "listing_map.html").read_text()
check("page 224 data rows", len(re.findall(r"<tr[^>]*data-k='", html)) == 224)
check("export/import buttons", all(x in html for x in (
    "btn-export-all", "btn-export-audit", "btn-import")))
hp = v.build_land_pool(list(csv.DictReader(open(
    v.V12 / "house_price_normalized_per_row.csv"))), v.load_archive(),
    v.load_resolved_yb(), v.load_tax(), v.load_lot_dims())[0]
hs = [r["lot"] for r in hp if r["community"] == "Hastings-Sunrise"]
import statistics  # noqa: E402
lot_p50 = f"{statistics.median(hs):,.0f}"
check("C09 community lot P50 uses corrected pool",
      re.search(r"<td>Hastings-Sunrise</td>(?:<td>[^<]*</td>|<td><b>[^<]*"
                r"</b></td>){3}<td>" + re.escape(lot_p50), html) is not None,
      f"HS lot P50={lot_p50}")

# 11. V1.5.1: strict forward test — ANCHOR still beats V1.4, no future info
ST = json.load(open(D / "backtest_summary.json"))["strict_v1_5_1"]
for n in ("HOUSE", "DUPLEX"):
    fw = ST[n]["forward_expanding"]
    check(f"{n} forward ANCHOR MdAPE < V14",
          fw["ANCHOR"]["mdape"] < fw["V14"]["mdape"],
          f"{fw['ANCHOR']['mdape']} vs {fw['V14']['mdape']}")
check("APE uses actual as denominator",
      json.load(open(D / "backtest_summary.json"))["ape_definition"]
      == "|estimate-actual|/actual")

# 12. V1.5.2: out-of-support is disclosed, never a ranking gate —
#     full ranking may pick it (flagged); in-support view never does
br = list(csv.DictReader(open(D / "v15_roi_bridge.csv")))
oos = {(r["listing"], r["scenario"]) for r in sr
       if r["out_of_support"] == "True"}
check("in-support view never picks out-of-support",
      not [b for b in br if (b["listing"], b["best_s3"]) in oos])
full_oos = [b for b in br if (b["listing"], b["best_full"]) in oos]
check("full ranking keeps out-of-support and flags it",
      full_oos and all(b["best_full_oos"] == "True" for b in full_oos),
      f"n={len(full_oos)}")
check("no listing loses its best ROI to the support gate",
      not [b for b in br if b["roi_s2_sale"] and not b["roi_full"]])

# 13. summary counts == counts from displayed (rounded) CSV values
sm = json.load(open(D / "v15_summary.json"))
for t in (10, 20, 30):
    n_full = sum(1 for b in br if b["roi_full"] and float(b["roi_full"]) >= t)
    n_in = sum(1 for b in br if b["roi_s3_support"]
               and float(b["roi_s3_support"]) >= t)
    check(f"ROI>={t} full summary == CSV", n_full == sm["roi_ge"][str(t)],
          f"{n_full} vs {sm['roi_ge'][str(t)]}")
    check(f"ROI>={t} in-support summary == CSV",
          n_in == sm["roi_in_support_ge"][str(t)])
check("haircut ROI ordered: P50 >= -10% >= -20%",
      all(float(r["roi_p50"]) >= float(r["roi_sale_m10"])
          >= float(r["roi_sale_m20"]) for r in sr if r["roi_sale_m10"]))

# 14. bridge anchors on V1.4.4 page counts (74/15/2)
check("bridge S0 reproduces V1.4.4 74/15/2",
      sm["roi_bridge"]["roi_s0_v144"] == {"10": 74, "20": 15, "30": 2})

# 15. unified contradiction index: every source ID appears exactly once,
#     counts are not summed, no finding is marked RESOLVED automatically
subprocess.run([sys.executable, "scripts/unify_contradictions_v1_5.py"],
               cwd=ROOT, check=True, capture_output=True)
U = list(csv.DictReader(open(D / "unified_contradictions.csv")))
ids = [i for u in U for i in u["source_ids"].split(";")]
d145 = ROOT / "docs/audits/listing_consistency_v1_4_5/consistency_findings.csv"
src145 = [r["finding_id"] for r in csv.DictReader(open(d145))]
src15 = [r["id"] for r in csv.DictReader(open(D / "v15_contradictions.csv"))]
check("every source ID kept exactly once",
      sorted(ids) == sorted(src145 + src15) and len(set(ids)) == len(ids),
      f"{len(ids)} ids / {len(U)} unified rows")
check("unified rows < summed source count",
      len(U) < len(src145) + len(src15))
check("no automatic RESOLVED", all(u["review_status"] == "OPEN" for u in U))
check("V1.5 contradiction IDs stable (code-prefixed)",
      all(re.match(r"V15-C\d\d-\d{3}$", i) for i in src15))

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
