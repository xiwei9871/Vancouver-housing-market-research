#!/usr/bin/env python3
"""Evidence Gate V1.2.2 — 修正 V1.2.1 四处缺陷:
  B. 分层关联:同源字段不重复计分;HIGH 必须跨实体(plan/parcel 交叉)
  C. 75 笔成交:交易数 vs 项目数分列;旧建筑≠已拆除;特殊记录带字段级证据
  D. 成交来源 vs 市场可比性 两维分级(字段完整≠独立验证)
依赖:import V1.2 管线对象(幂等)。
"""
import csv, json, sys, datetime, hashlib
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                              # noqa: E402
import newbuild_evidence_v1_2 as B                # noqa: E402

OUTD = ROOT / "data/research/normalized/audit_v1_2_2"
OUTD.mkdir(parents=True, exist_ok=True)

# 项目 → 其地址上的全部 folio plan(项目侧 strata 证据)
PROJ_PLANS = defaultdict(set)
for addr, folios in B.tax_by_addr.items():
    proj = B.proj_by_base.get(addr)
    if proj:
        for folio in folios:
            if folio.get("plan"):
                PROJ_PLANS[proj["project_id"]].add(folio["plan"])
for addr, lf in B.land_by_addr.items():
    proj = B.proj_by_base.get(addr)
    if proj and lf.get("plan"):
        PROJ_PLANS[proj["project_id"]].add(lf["plan"])


# ============ P0B. 修正分层关联 ============
def layered_match(r):
    """独立来源:permit / tax_folio / land_folio / arcgis(同一folio的
    pid+plan 只算一个源)。HIGH 需要跨实体证据:
      - unit folio 的 plan ∈ 项目地址侧 folio plan 集(strata 归属实证)
      - 或 sale pid == 母地块 folio pid 且该地块即许可地址(parcel 实证)
    """
    b = r["_base"]
    sources = set()
    keys = []
    # 单元层:pid → folio
    unit_tax = None
    if r.get("pid") and r["pid"] in B.tax_by_pid:
        cands = B.tax_by_pid[r["pid"]]
        unit_tax = cands[0]
        sources.add("tax_folio")
        keys.append("unit_pid")
        if len(cands) > 1:
            keys.append("pid_multi_folio")
    # 母地块层:基址 → LAND folio
    land = B.land_by_addr.get(b)
    if land:
        sources.add("land_folio")
        keys.append("parcel_addr")
    # 项目层:基址 → permit
    proj = B.proj_by_base.get(b)
    if proj:
        sources.add("permit")
        keys.append("permit_addr")
    # 跨实体证据
    cross = []
    plan = (unit_tax or {}).get("plan", "")
    if plan and proj and plan in PROJ_PLANS.get(proj["project_id"], set()):
        cross.append("strata_plan_in_project")
    if (r.get("pid") and land and r["pid"] == land.get("pid")
            and proj):
        cross.append("sale_pid_is_project_parcel")
    # 身份层级(与产品分类分离)
    if cross:
        identity = ("UNIT_STRATA" if "strata_plan_in_project" in cross
                    else "PARCEL")
    elif unit_tax:
        identity = "UNIT_PID_ONLY"
    elif land:
        identity = "PARCEL_ADDR"
    elif proj:
        identity = "PERMIT_ADDR_ONLY"
    else:
        identity = "NONE"
    ambiguity = ("multi_folio" if "pid_multi_folio" in keys else
                 "none")
    if cross:
        conf = "HIGH"                     # 真跨实体
    elif len(sources) >= 2:
        conf = "MED"                      # 双源但无跨实体证明
    elif sources:
        conf = "LOW"
    else:
        conf = "NONE"
    review_queue = (ambiguity != "none" or
                    (conf == "HIGH" and not cross))
    return {"sources": sources, "keys": keys, "cross": cross,
            "identity": identity, "confidence": conf,
            "ambiguity": ambiguity, "review_queue": review_queue,
            "unit_tax": unit_tax, "land": land, "proj": proj,
            "n_independent_sources": len(sources)}


# ============ 全量重检(与 V1.2.1 旧置信度对照) ============
AUDIT_PRODUCTS = ("STRATA_UNKNOWN", "MPX_CAND", "MPX_WHOLE", "TOWNHOUSE",
                  "APARTMENT", "MPX", "DUPLEX")
recheck = []
old_conf = {}
for r in B.COMPS:
    if r["_prod"] in AUDIT_PRODUCTS:
        m = layered_match(r)
        recheck.append({
            "address": r["address"], "date": r["sale_date"],
            "class": r["product_class"],
            "verdict": r.get("_verdict", r["_prod"]),
            "identity_link": m["identity"],
            "cross_entity": "+".join(m["cross"]) or "none",
            "n_independent_sources": m["n_independent_sources"],
            "sources": "+".join(sorted(m["sources"])) or "none",
            "match_keys": "+".join(m["keys"]) or "none",
            "confidence_v122": m["confidence"],
            "ambiguity": m["ambiguity"],
            "review_queue": m["review_queue"],
            "permit_project": (m["proj"] or {}).get("project_id", ""),
            "unit_plan": (m["unit_tax"] or {}).get("plan", ""),
            "project_plans":
                "|".join(sorted(PROJ_PLANS.get(
                    (m["proj"] or {}).get("project_id", ""), set())))})

# V1.2.1 旧 HIGH 重检数(读旧产物对照)
old_audit = list(csv.DictReader(open(
    B.ROOT / "data/research/normalized/audit_v1_2_1/"
    "mpx_identity_linkage_audit.csv")))
old_hi = {a["address"] for a in old_audit if a["confidence"] == "HIGH"}
new_hi = {a["address"] for a in recheck
          if a["confidence_v122"] == "HIGH"}
hi_kept = len(old_hi & new_hi)


# ============ P0C. MPX 75 笔:交易 vs 项目,旧建筑≠拆除 ============
mpx_ver = []
for p in [x for x in B.mpx_linkage
          if int(x["sales_pre_permit"]) or int(x["sales_post_permit"])]:
    for s in B.sales_by_base.get(p["address"], []):
        arc = B.arc_by_key.get((s["address"], s["sale_date"]), {})
        desc = arc.get("official_bca_description") or ""
        yb_old = s.get("year_built") or ""
        pre = s["sale_date"] < p["first_issue"]
        if pre:
            if yb_old and yb_old.isdigit() and int(yb_old) < 2000:
                verdict, basis = "PRE_PERMIT_OLD_BUILDING_SALE", \
                    f"yb{yb_old}:许可前旧建筑(拆除行为未证实)"
            elif any(t in desc.lower() for t in
                     ("house", "1 sty", "sfd")):
                verdict, basis = "PRE_PERMIT_OLD_BUILDING_SALE", \
                    f"desc='{desc[:30]}':旧房形态(拆除未证实)"
            else:
                verdict, basis = "PRE_PERMIT_SALE", \
                    "历史属性不足以判定新旧建筑"
        else:
            verdict, basis = "POST_PERMIT_SALE_REVIEW", \
                "许可后成交,人工定性中"
        mpx_ver.append({
            "project": p["project_id"], "address": p["address"],
            "permit_issue": p["first_issue"],
            "sale_date": s["sale_date"], "sale_price": s["_p"],
            "sale_class": s["product_class"], "sale_yb": yb_old,
            "bca_desc": desc[:60], "verdict": verdict,
            "basis": basis, "post": not pre,
            "demolition_permit_evidence":
                "unavailable:LDH许可集仅New Building"})

pre_tx = [x for x in mpx_ver if not x["post"]]
count_rec = [
    {"metric": "mpx_projects_total", "n": len(B.mpx_linkage)},
    {"metric": "projects_with_any_sale", "n":
        sum(1 for p in B.mpx_linkage
            if int(p["sales_pre_permit"]) + int(p["sales_post_permit"]))},
    {"metric": "pre_permit_transactions", "n": len(pre_tx)},
    {"metric": "pre_permit_projects",
     "n": len({x["project"] for x in pre_tx})},
    {"metric": "pre_permit_old_building_tx", "n":
        sum(1 for x in pre_tx
            if x["verdict"] == "PRE_PERMIT_OLD_BUILDING_SALE")},
    {"metric": "pre_permit_unresolved_tx", "n":
        sum(1 for x in pre_tx if x["verdict"] == "PRE_PERMIT_SALE")},
    {"metric": "post_permit_tx", "n":
        sum(1 for x in mpx_ver if x["post"])},
    {"metric": "confirmed_teardown_tx", "n": 0,
     "note": "拆除许可不在LDH数据集;旧建筑出售≠已拆除"}]

# 特殊三记录:字段级证据
def rec_evidence(addr_frag):
    out = {"address": addr_frag}
    for r in B.COMPS:
        if addr_frag in r["address"]:
            out.update({"comp_price": r["_p"], "comp_date": r["sale_date"],
                        "comp_yb": r.get("year_built", ""),
                        "comp_fa": r.get("floor_above_sf", ""),
                        "comp_lot": r.get("lot_sqft", ""),
                        "comp_class": r["product_class"],
                        "comp_pid": r["pid"],
                        "asof": r.get("asof_status", "")})
    for (a, d), arc in B.arc_by_key.items():
        if addr_frag in a:
            out.update({"arc_desc": arc.get("official_bca_description", ""),
                        "arc_impr": arc.get("bca_improvement", ""),
                        "arc_lot": arc.get("lot_area_sqft", "")})
    return out

specials = [
    {**rec_evidence("2609 DUNDAS"),
     "verdict": "NOT_MPX_SALE",
     "finding": "HOUSE_DETACHED yb2025 fa1204sf:新建小房而非MPX单元;"
               "MPX许可为同址独立阶段"},
    {**rec_evidence("1909 CHARLES"),
     "verdict": "OLD_MULTI_RES_NOT_NEWBUILD",
     "finding": "yb='1909'(疑门牌误入)+Multiple Residence+bca改善"
               "$1.67M:旧多户楼出售"},
    {**rec_evidence("3122 MARINE DR"),
     "verdict": "VACANT_LAND_NOT_BUILDING",
     "finding": "Multi-Family(Vacant)+improvement=0+30,765sf:空地"}]


# ============ P1. 双维证据等级(HOUSE+DUPLEX) ============
def source_verification(r):
    """独立于字段完整性:arcgis(独立源)交叉验证价/日 → VERIFIED。"""
    arc = B.arc_by_key.get((r["address"], r["sale_date"]))
    if arc and arc.get("sale_price") and \
            abs(arc["sale_price"] - r["_p"]) / max(r["_p"], 1) < 0.015:
        return "SOURCE_VERIFIED"
    if r.get("pid") and r.get("asof_status") == "CONSISTENT" and r["_p"]:
        return "SOURCE_TRACEABLE"
    return "UNVERIFIED"


def comparability(r):
    if not B.usable(r):
        return "NOT_ELIGIBLE"
    if r.get("_yb_src") or r.get("_fa_src"):
        return "ADJUSTED_COMPARABLE"     # 恢复字段需校正
    if (not r["gst_basis"].startswith(("new", "res"))
            or r["_scope"] == "WHOLE_SUSPECT"):
        return "ADJUSTED_COMPARABLE"
    return "DIRECT_COMPARABLE"


def first_resale(r):
    if r["gst_basis"] == "new_first_sale":
        return "first_sale"
    if r["gst_basis"].startswith("res"):
        return "resale"
    return "unknown"


def custom_flag(r):
    """定制豪宅启发式:高价 AND 大地块 才标(strata记录lot含整个
    parcel多边形,单条件误报高)。"""
    lot = float(r.get("lot_sqft") or 0)
    return ("custom_estate_suspect"
            if (lot > 6600 and r["_p"] > 4_500_000) else "standard")


dual_rows, frs = [], []
for r in B.COMPS:
    if r["_prod"] in ("HOUSE", "DUPLEX"):
        sv, cp = source_verification(r), comparability(r)
        fr = first_resale(r)
        dual_rows.append({
            "address": r["address"], "comm": r["_comm"],
            "prod": r["_prod"], "date": r["sale_date"],
            "price": r["_p"], "fa": r["_fa"], "scope": r["_scope"],
            "source_verification": sv, "comparability": cp,
            "first_or_resale": fr, "custom_flag": custom_flag(r),
            "old_tier": r["_tier"], "pid": r["pid"],
            "permit_linked": bool(B.proj_by_base.get(r["_base"]))})
        frs.append({"prod": r["_prod"], "comm": r["_comm"],
                    "first_or_resale": fr,
                    "comparability": cp, "usable": B.usable(r)})

frs_rows = [{"prod": p, "comm": c, "first_or_resale": f,
             "comparability": cp,
             "n_usable": sum(1 for x in frs
                             if x["prod"] == p and x["comm"] == c
                             and x["first_or_resale"] == f
                             and x["comparability"] == cp and x["usable"]),
             "n_total": sum(1 for x in frs
                            if x["prod"] == p and x["comm"] == c
                            and x["first_or_resale"] == f
                            and x["comparability"] == cp)}
            for p, c, f, cp in
            sorted({(x["prod"], x["comm"], x["first_or_resale"],
                     x["comparability"]) for x in frs})]


def wcsv(path, rows):
    if not rows:
        path.write_text(""); return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


wcsv(OUTD / "mpx_linkage_confidence_recheck.csv", recheck)
wcsv(OUTD / "mpx_sale_verification.csv", mpx_ver)
wcsv(OUTD / "mpx_sale_count_reconciliation.csv", count_rec)
wcsv(OUTD / "mpx_specials_evidence.csv", specials)
wcsv(OUTD / "house_duplex_evidence_tiers.csv", dual_rows)
wcsv(OUTD / "first_sale_vs_resale_summary.csv", frs_rows)

summary = {
    "linkage_recheck": {
        "rows": len(recheck),
        "conf_v122": dict(Counter(a["confidence_v122"]
                                  for a in recheck)),
        "identity": dict(Counter(a["identity_link"] for a in recheck)),
        "cross_entity": dict(Counter(a["cross_entity"]
                                     for a in recheck)),
        "old_v121_HIGH": len(old_hi),
        "v122_HIGH": len(new_hi),
        "HIGH_kept": hi_kept,
        "HIGH_downgraded": len(old_hi - new_hi),
        "review_queue": sum(1 for a in recheck if a["review_queue"])},
    "mpx_counts": {m["metric"]: m["n"] for m in count_rec},
    "specials": {s["address"]: s["verdict"] for s in specials},
    "dual": {
        "source_verification": dict(Counter(
            x["source_verification"] for x in dual_rows)),
        "comparability": dict(Counter(
            x["comparability"] for x in dual_rows)),
        "first_or_resale": dict(Counter(
            x["first_or_resale"] for x in dual_rows
            if x["comparability"] == "DIRECT_COMPARABLE")),
        "custom_estate_suspect": sum(
            1 for x in dual_rows
            if x["custom_flag"] == "custom_estate_suspect")},
}
(OUTD / "manifest.json").write_text(json.dumps({
    "version": "v1.2.2", "run": datetime.datetime.now().isoformat(),
    "parent_commit": "5a6ebbd3b00ae96872066b10261156b4ab790c87",
    "input_sha256": B.HASHES,
    "inputs": {k: str(v.relative_to(ROOT)) for k, v in B.INPUTS.items()},
    "counts": summary}, ensure_ascii=False, indent=1))
(OUTD / "evidence_gate_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("wrote audit_v1_2_2/*")
