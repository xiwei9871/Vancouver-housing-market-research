#!/usr/bin/env python3
"""Unified contradiction index: Devin V1.4.5 (199) + Claude V1.5 (56).

Never adds counts across the two sources. Each source finding keeps its
original ID, rule and classification; findings are grouped by
(topic, listing set) into one unified row U-xxxx that lists every
source ID it absorbs. For V1.4.5 findings whose trigger depends on
numbers V1.5 replaced (land value, sale $/sf, ROI), the trigger is
re-evaluated on V1.5 outputs: STILL_TRIGGERS / CLEARED_BY_V15 / N/A.
A cleared trigger is not RESOLVED — it only means the V1.5 numbers no
longer cross the threshold; the human review status stays OPEN.

Out: docs/audits/listing_valuation_v1_5/unified_contradictions.csv,
     unified_contradictions_summary.json
Run: python3 scripts/unify_contradictions_v1_5.py   (after v1_5 pipeline)
"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D15 = ROOT / "docs/audits/listing_valuation_v1_5"
D145 = ROOT / "docs/audits/listing_consistency_v1_4_5"

# source rule -> shared topic. Same topic + same listing set = duplicate.
TOPIC = {
    ("V1.4.5", "C01"): "LAND_BCA_LOW_MODEL_PREMIUM",
    ("V1.5", "C01"): "LAND_BCA_LOW_MODEL_PREMIUM",
    ("V1.4.5", "C02"): "LAND_MODEL_VS_BCA_GAP",
    ("V1.5", "C02"): "LAND_MODEL_VS_BCA_GAP",
    ("V1.4.5", "C03"): "LAND_BCA_HIGH_MODEL_DISCOUNT",
    ("V1.4.5", "C04"): "PAIR_LAND_GAP",
    ("V1.5", "C04"): "PAIR_LAND_GAP",
    ("V1.4.5", "C05"): "LAND_PREMIUM_WITH_HIGH_ROI",
    ("V1.5", "C03"): "LAND_PREMIUM_WITH_HIGH_ROI",
    ("V1.4.5", "C06"): "PAIR_SALE_PSF_GAP",
    ("V1.5", "C06"): "SALE_PSF_V14_TO_V15_JUMP",
    ("V1.4.5", "C07"): "PAIR_ROI_GAP",
    ("V1.5", "C05"): "PAIR_ROI_GAP",
    ("V1.4.5", "C09"): "LEGAL_SMALL_LOT_DUPLEX",
    ("V1.5", "C07"): "LEGAL_VERIFIED_WITH_VARIANCE",
    ("V1.5", "C08"): "STATE_VS_ESTIMATE",
}
# topics about the same underlying legal fact (small R1-1 lot) are
# linked, not merged: one concerns DUPLEX, the other HOUSE
LINKED = {"LEGAL_SMALL_LOT_DUPLEX": "LEGAL_VERIFIED_WITH_VARIANCE",
          "LEGAL_VERIFIED_WITH_VARIANCE": "LEGAL_SMALL_LOT_DUPLEX"}


def split_pair(s):
    return tuple(sorted(x.strip() for x in s.split("↔") if x.strip()))


def load_v15():
    land = {r["listing"]: r for r in csv.DictReader(open(D15 / "v15_land.csv"))}
    sale = {(r["listing"], r["scenario"]): r for r in csv.DictReader(
        open(D15 / "v15_sale_and_roi.csv"))}
    br = {r["listing"]: r for r in csv.DictReader(open(D15 / "v15_roi_bridge.csv"))}
    lst = {r["address"]: r for r in json.load(open(
        ROOT / "reports/watchlist/rebuild-value-2026-10-20.json"))["rows"]}
    return land, sale, br, lst


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def recheck(rule, listings, key_fields, land, sale, br, lst):
    """Re-evaluate a V1.4.5 trigger on V1.5 numbers (same threshold)."""
    a = listings[0]
    L, R = land.get(a, {}), lst.get(a, {})
    ask, bt, bl = f(L.get("asking")), f(R.get("bca")), f(L.get("bca_land"))
    fair = f(L.get("fair_land"))
    if rule == "C01" and ask and bt and fair:
        return ask / bt <= 0.90 and (ask - fair) / fair >= 0.10
    if rule == "C02" and bl and fair:
        return fair / bl < 0.80 or fair / bl > 1.25
    if rule == "C03" and ask and bt and fair:
        return ask / bt >= 1.00 and (fair - ask) / fair >= 0.10
    if rule == "C05":
        roi, disc = f(br.get(a, {}).get("roi_full")), f(L.get("disc_pct"))
        return None if roi is None or disc is None else \
            disc <= -10 and roi >= 20
    if rule == "C04" and len(listings) == 2:
        fa, fb = (f(land.get(x, {}).get("fair_land")) for x in listings)
        return None if not fa or not fb else abs(fa - fb) > 500_000
    if rule == "C06" and len(listings) == 2:
        prod = "DUPLEX" if "DUPLEX" in key_fields else "HOUSE"
        pa, pb = (f(sale.get((x, prod), {}).get("psf_p50")) for x in listings)
        return None if not pa or not pb else max(pa, pb) / min(pa, pb) - 1 > 0.30
    if rule == "C07" and len(listings) == 2:
        ra, rb = (f(br.get(x, {}).get("roi_full")) for x in listings)
        return None if ra is None or rb is None else abs(ra - rb) >= 30
    return None


def main():
    land, sale, br, lst = load_v15()
    src = []
    for r in csv.DictReader(open(D145 / "consistency_findings.csv")):
        ls = split_pair(r["listing"]) if "↔" in r["listing"] else (r["listing"],)
        rc = recheck(r["rule"], ls, r["key_fields"], land, sale, br, lst)
        src.append({"source": "V1.4.5", "source_id": r["finding_id"],
                    "rule": r["rule"], "listings": ls,
                    "classification": r["classification"],
                    "severity": r["severity"], "detail": r["key_fields"],
                    "v15_recheck": {True: "STILL_TRIGGERS",
                                    False: "CLEARED_BY_V15"}.get(rc, "N/A")})
    for r in csv.DictReader(open(D15 / "v15_contradictions.csv")):
        ls = tuple(sorted(x for x in (r["listing"], r["other"]) if x))
        src.append({"source": "V1.5", "source_id": r["id"], "rule": r["code"],
                    "listings": ls, "classification": r["status"],
                    "severity": r["severity"], "detail": r["detail"],
                    "v15_recheck": "NATIVE"})
    groups = defaultdict(list)
    for s in src:
        s["topic"] = TOPIC.get((s["source"], s["rule"]),
                               f"{s['source']}_{s['rule']}")
        groups[(s["topic"], s["listings"])].append(s)
    by_listing_topic = defaultdict(set)
    rows = []
    for i, ((topic, ls), items) in enumerate(sorted(groups.items())):
        uid = f"U-{i + 1:04d}"
        for x in ls:
            by_listing_topic[(x, topic)].add(uid)
        srcs = {s["source"] for s in items}
        rc = sorted({s["v15_recheck"] for s in items if s["source"] == "V1.4.5"})
        rows.append({
            "unified_id": uid, "topic": topic, "listings": " ↔ ".join(ls),
            "sources": "+".join(sorted(srcs)),
            "source_ids": ";".join(s["source_id"] for s in items),
            "source_rules": ";".join(f"{s['source']}:{s['rule']}" for s in items),
            "source_classifications": ";".join(
                sorted({s["classification"] for s in items})),
            "max_severity": max((s["severity"] for s in items),
                                key=lambda x: ["LOW", "MEDIUM", "REVIEW",
                                               "HIGH", "DEFECT"].index(x)
                                if x in ("LOW", "MEDIUM", "REVIEW", "HIGH",
                                         "DEFECT") else 0),
            "v145_recheck_on_v15": ";".join(rc),
            "linked_ids": "", "review_status": "OPEN",
            "detail": " || ".join(s["detail"] for s in items)[:600]})
    for r in rows:
        lt = LINKED.get(r["topic"])
        if lt:
            ids = set()
            for x in r["listings"].split(" ↔ "):
                ids |= by_listing_topic.get((x, lt), set())
            r["linked_ids"] = ";".join(sorted(ids))
    fields = list(rows[0])
    with open(D15 / "unified_contradictions.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    n_src = Counter(s["source"] for s in src)
    summ = {
        "source_findings": dict(n_src),
        "unified_rows": len(rows),
        "absorbed_duplicates": len(src) - len(rows),
        "rows_by_sources": dict(Counter(r["sources"] for r in rows)),
        "rows_by_topic": dict(Counter(r["topic"] for r in rows)),
        "v145_recheck": dict(Counter(s["v15_recheck"] for s in src
                                     if s["source"] == "V1.4.5")),
        "v145_recheck_by_rule": {
            k: dict(Counter(s["v15_recheck"] for s in src
                            if s["source"] == "V1.4.5" and s["rule"] == k))
            for k in sorted({s["rule"] for s in src if s["source"] == "V1.4.5"})},
        "listings_with_any": len({x for r in rows
                                  for x in r["listings"].split(" ↔ ")}),
        "note": "counts are never summed across sources; CLEARED_BY_V15 "
                "!= RESOLVED (review_status stays OPEN)"}
    (D15 / "unified_contradictions_summary.json").write_text(
        json.dumps(summ, indent=1, ensure_ascii=False))
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
