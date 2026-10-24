#!/usr/bin/env python3
"""Free Evidence Integration V1 — 829 回填质量复核 + 三产品证据整合。
全离线:不新增任何网站请求。输出到 audit_v1_2_2/。
"""
import csv, json, re, sys, datetime, hashlib
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                              # noqa: E402
import newbuild_evidence_v1_2 as B                # noqa: E402

OUTD = ROOT / "data/research/normalized/audit_v1_2_2"
BF = OUTD / "bca_yb_backfill_results.csv"
UNIT_RE = re.compile(r"^\d+-\d+\s")          # 1-5438 MAIN 式单元前缀

bf = list(csv.DictReader(open(BF)))
comps = {r["address"]: r for r in B.COMPS}


# ============ P0a. 身份核销(离线) ============
def identity_checks(brow):
    """用已存字段判身份强度,不重新请求。"""
    addr, c = brow["address"], comps.get(brow["address"], {})
    checks, fails = {}, []
    checks["unique_candidate"] = brow["n_candidates"] == "1"
    checks["bca_pid_present"] = bool(brow["bca_pid"])
    checks["comp_pid_present"] = bool(c.get("pid"))
    checks["pid_consistent"] = (
        not checks["bca_pid_present"] or not checks["comp_pid_present"]
        or c["pid"].replace("-", "") == brow["bca_pid"].replace("-", ""))
    checks["muni_vancouver"] = "VANCOUVER" in addr.upper()
    checks["unit_prefix"] = UNIT_RE.match(addr) is not None
    checks["comp_strata_flag"] = c.get("is_strata") == "Y"
    checks["sale_history_captured"] = bool(brow["sales_history"])
    fails = [k for k, v in checks.items()
             if v is False and k in ("unique_candidate", "pid_consistent")]
    if not checks["muni_vancouver"]:
        ident = "OUT_OF_SCOPE_MUNICIPALITY"   # Burnaby 等,身份无碍仅出范围
    elif fails:
        ident = "IDENTITY_UNRESOLVED"
    elif checks["bca_pid_present"] and checks["comp_pid_present"]:
        ident = "IDENTITY_PID_CONFIRMED"
    elif checks["unique_candidate"]:
        ident = "IDENTITY_SINGLE_CANDIDATE"
    else:
        ident = "IDENTITY_UNRESOLVED"
    return ident, checks


recon = []
for r in bf:
    ident, ck = identity_checks(r)
    yb = r["bca_year_built"]
    cat = ("CONFIRMED_OLD" if yb.isdigit() and int(yb) < 2018 else
           "NEW_CANDIDATE" if yb.isdigit() and int(yb) >= 2018
           and r["date"][:4] >= yb else
           "TEMPORAL_CONFLICT" if yb.isdigit() else "BCA_BLANK")
    recon.append({"address": r["address"], "date": r["date"],
                  "price": r["price"], "bca_yb": yb,
                  "bca_f1": r["bca_first_floor"],
                  "bca_f2": r["bca_second_floor"],
                  "bca_basement": r["bca_basement"],
                  "bca_desc": r["bca_description"],
                  "bca_pid": r["bca_pid"], "comp_pid":
                  comps.get(r["address"], {}).get("pid", ""),
                  "identity": ident, "cat": cat,
                  "unique_candidate": ck["unique_candidate"],
                  "unit_prefix": ck["unit_prefix"],
                  "muni": ("BURNABY" if "BURNABY" in
                           r["address"].upper() else "VANCOUVER"),
                  "sale_cross_check": ("not_possible_no_sales_history"
                                       if not ck["sale_history_captured"]
                                       else "checkable")})


# ============ P1. 12 条候选漏斗 ============
new12 = [r for r in recon if r["cat"] == "NEW_CANDIDATE"]
cand_rows = []
for r in new12:
    c = comps.get(r["address"], {})
    fa = int((r["bca_f1"] or "0").replace(",", "") or 0) + \
        int((r["bca_f2"] or "0").replace(",", "") or 0)
    stage, notes = [], []
    if r["muni"] != "VANCOUVER":
        stage.append("EXCLUDE_MUNICIPALITY")
    if r["unit_prefix"] or c.get("product_class") == "HOUSE_SUSPECT_STRATA":
        stage.append("EXCLUDE_STRATA_UNIT")
    if fa and fa < 800:
        notes.append(f"tiny_area_{fa}sf_laneway_suspect")
    eligible = not stage
    cand_rows.append({
        "address": r["address"], "date": r["date"], "price": r["price"],
        "yb": r["bca_yb"], "fa_above_grade": fa or "",
        "muni": r["muni"], "unit_prefix": r["unit_prefix"],
        "identity": r["identity"],
        "pid_evidence": ("bca_pid_only" if r["bca_pid"]
                         and not r["comp_pid"] else
                         "pid_consistent" if r["comp_pid"] else "none"),
        "sale_verified": "UNKNOWN_no_sales_history_captured",
        "exclusions": "+".join(stage) or "none",
        "notes": "+".join(notes),
        "eligible_comparable": eligible,
        # sales_history 未捕获 → 交易无法离线核对,全部待人工
        "decision": ("ELIGIBLE_PENDING_MANUAL_SALE_CHECK" if eligible
                     else "EXCLUDED")})

# ============ P2a. 24 冲突审计 ============
conf_rows = [{"address": r["address"], "date": r["date"],
              "price": r["price"], "bca_yb": r["bca_yb"],
              "identity": r["identity"], "muni": r["muni"],
              "status": "QUARANTINED_NOT_TEARDOWN"}
             for r in recon if r["cat"] == "TEMPORAL_CONFLICT"]

# ============ P2b. 面积质量 ============
fa_rows = [r for r in recon if (r["bca_f1"] or "").strip()
           or (r["bca_f2"] or "").strip()]
fa_sum = [{"product": "HOUSE_829scope",
           "with_floor_area": len(fa_rows),
           "with_second_floor": sum(1 for r in fa_rows
                                    if (r["bca_f2"] or "").strip()),
           "with_basement": sum(1 for r in fa_rows
                                if (r["bca_basement"] or "").strip()),
           "unit_prefixed": sum(1 for r in fa_rows if r["unit_prefix"]),
           "note": "bca first+second floor=地上层面积;basement单列;均待"
                   "身份验证后才入估值"}]

# ============ P3. MPX 离线再核 ============
mpx_rows = list(csv.DictReader(open(
    B.ROOT / "data/research/normalized/audit_v1_2/"
    "mpx_project_unit_linkage_summary.csv")))
arc_by_base = defaultdict(list)
_arc = json.loads((B.ROOT / "data/research/raw/location/"
                   "bca-arcgis-sales-2025-present-all.json").read_text())
_arc = _arc if isinstance(_arc, list) else \
    next(v for v in _arc.values() if isinstance(v, list))
for r in _arc:
    arc_by_base[K.base_addr(r.get("address") or "")].append(r)
comp_by_base = defaultdict(list)
for r in B.COMPS:
    comp_by_base[r["_base"]].append(r)

mpx_off = []
for p in mpx_rows:
    b, addr = K.base_addr(p["address"]), p["address"]
    folios = B.tax_by_addr.get(addr, []) + B.tax_by_addr.get(b, [])
    plans = {f.get("plan") for f in folios if f.get("plan")}
    same_street_strata = []
    if addr:
        st = " ".join(addr.split()[1:])
        for k2, f2 in B.tax_by_addr.items():
            if k2 != addr and k2.endswith(" " + st):
                for f in f2:
                    if f.get("legal_type") == "STRATA":
                        same_street_strata.append(
                            f"{k2}:{f.get('plan','')}")
    unit_sales = [s for s in comp_by_base.get(b, [])
                  if K.unit_prefix(s["address"])]
    arc_units = [a for a in arc_by_base.get(b, [])
                 if a.get("official_bca_is_strata") == "Y"]
    if unit_sales or arc_units:
        cat = "CONFIRMED_SALE" if any(
            s["sale_date"] >= p["first_issue"] for s in unit_sales) \
            else "MATCHED_NO_SALE"
    elif plans or same_street_strata:
        cat = "MATCHED_NO_SALE"
    elif folios:
        cat = "UNIT_IDENTITY_UNKNOWN"
    else:
        cat = "NO_MATCH_IN_CURRENT_DATA"
    mpx_off.append({"project_id": p["project_id"], "address": addr,
                    "folios": len(folios), "plans": "|".join(sorted(plans)),
                    "same_street_strata_n": len(same_street_strata),
                    "unit_sales": len(unit_sales),
                    "arcgis_strata_units": len(arc_units),
                    "category": cat})


# ============ 覆盖前后 ============
cov = [{"scope": "HOUSE usable", "before": 217,
        "after_conditional": 217 + sum(1 for c in cand_rows
                                       if c["eligible_comparable"]),
        "note": "新增均需人工复核;sales_history 未捕获故"
                "sale_verified=UNKNOWN"},
       {"scope": "HOUSE yb confirmed", "before": 50,
        "after_conditional": 50 + 786 + 12 + 24,
        "note": "829 闭合:旧786+候选12+冲突24+空7"},
       {"scope": "MPX unit sales", "before": 0,
        "after_conditional": 0,
        "note": "离线复核后仍无 CONFIRMED_SALE"}]


def wcsv(path, rows):
    if not rows:
        path.write_text(""); return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


wcsv(OUTD / "bca_829_identity_reconciliation.csv", recon)
wcsv(OUTD / "newbuild_12_candidate_verification.csv", cand_rows)
wcsv(OUTD / "temporal_conflict_24_audit.csv", conf_rows)
wcsv(OUTD / "bca_floor_area_quality_summary.csv", fa_sum)
wcsv(OUTD / "mpx_offline_match_reconciliation.csv", mpx_off)
wcsv(OUTD / "coverage_before_after_free_recovery.csv", cov)

summary = {
    "identity": dict(Counter(r["identity"] for r in recon)),
    "cat": dict(Counter(r["cat"] for r in recon)),
    "candidates": {
        "total": len(cand_rows),
        "eligible_reviewed": sum(1 for c in cand_rows
                                 if c["eligible_comparable"]),
        "excluded": dict(Counter(c["exclusions"] for c in cand_rows
                                 if c["exclusions"] != "none"))},
    "mpx_offline": dict(Counter(r["category"] for r in mpx_off)),
    "fa_quality": fa_sum[0] if fa_sum else {},
}
(OUTD / "manifest.json").write_text(json.dumps({
    "version": "v1.2.2+free-integration", "run":
    datetime.datetime.now().isoformat(),
    "input_sha256": {**B.HASHES,
                     "bca_yb_backfill": hashlib.sha256(
                         BF.read_bytes()).hexdigest()},
    "counts": summary}, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("wrote integration outputs")
