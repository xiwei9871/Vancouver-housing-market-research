#!/usr/bin/env python3
"""Evidence Verification V1.2.1 — 分层实体关联 + 证据核销 + 来源可行性。
import V1.2 管线复用其对象(幂等),新增审计输出到 audit_v1_2_1/。
实体链:Permit → Parcel → Building → StrataPlan → StrataLot/PID → Sale
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                              # noqa: E402
import newbuild_evidence_v1_2 as B                # noqa: E402  运行V1.2得对象
from valuation_v2_sites import dev_cost           # noqa: E402

OUTD = ROOT / "data/research/normalized/audit_v1_2_1"
OUTAUD = ROOT / "docs/audits/newbuild_coverage_v1"
OUTD.mkdir(parents=True, exist_ok=True)

# ============ P0a. 分层关联函数 ============
def layered_match(r):
    """返回 match 证据:permit→parcel→strata_plan→unit_pid→sale 分层。"""
    b = r["_base"]
    keys, sources, meth = [], [], []
    # L1 单元 PID → folio(最强)
    unit_tax = None
    if r.get("pid") and r["pid"] in B.tax_by_pid:
        cands = B.tax_by_pid[r["pid"]]
        unit_tax = cands[0]
        keys.append("unit_pid"); sources.append("tax_folio")
        if len(cands) > 1:
            keys.append("pid_multi_folio")
    # L2 基址 → LAND folio(母地块)
    land = B.land_by_addr.get(b)
    if land:
        keys.append("parcel_addr"); sources.append("land_folio")
    # L3 基址 → 许可项目(仅地址 → 不升级)
    proj = B.proj_by_base.get(b)
    if proj:
        keys.append("permit_addr"); sources.append("permit")
    # L4 strata plan 交叉:单元 folio 的 plan ↔ 同址其它 folio
    plan = (unit_tax or {}).get("plan", "")
    if plan:
        keys.append("strata_plan"); sources.append("tax_folio_plan")
    ambiguity = len([k for k in keys if "multi" in k])
    if len(set(keys) - {"permit_addr"}) >= 2:
        conf = "HIGH"          # pid+folio 或 pid+plan 双证
    elif keys:
        conf = "MED"           # 单证
    else:
        conf = "LOW"
    if keys == ["permit_addr"]:
        conf = "LOW"           # 仅地址=弱证,不标 HIGH
    return {"match_method": "+".join(sources) or "none",
            "match_keys": "+".join(keys) or "none",
            "supporting_sources": "|".join(sorted(set(sources))) or "none",
            "ambiguity": ambiguity, "confidence": conf,
            "unit_tax": unit_tax, "land": land, "proj": proj}


# ============ P0b. 全量审计目标的分层关联 ============
link_audit = []
for r in B.COMPS:
    if r["_prod"] in ("STRATA_UNKNOWN", "MPX_CAND", "MPX_WHOLE",
                      "TOWNHOUSE", "APARTMENT", "MPX", "DUPLEX"):
        m = layered_match(r)
        v = r.get("_verdict", r["_prod"])
        link_audit.append({
            "address": r["address"], "date": r["sale_date"],
            "price": r["_p"], "class": r["product_class"], "verdict": v,
            "match_method": m["match_method"], "match_keys": m["match_keys"],
            "supporting_sources": m["supporting_sources"],
            "ambiguity": m["ambiguity"], "confidence": m["confidence"],
            "permit_project": (m["proj"] or {}).get("project_id", ""),
            "approved_type": (m["proj"] or {}).get("products", ""),
            "strata_plan": (m["unit_tax"] or {}).get("plan", ""),
            "parent_parcel_pid": (m["land"] or {}).get("pid", ""),
            "unit_pid": r["pid"], "tax_zoning":
            (m["unit_tax"] or {}).get("zoning_district", ""),
            "tax_yb": (m["unit_tax"] or {}).get("year_built", "")})

# ============ P0c. 73 条许可前成交逐条定性 ============
mpx_sale_ver = []
for p in [x for x in B.mpx_linkage if int(x["sales_pre_permit"]) or
          int(x["sales_post_permit"])]:
    for s in B.sales_by_base.get(p["address"], []):
        arc = B.arc_by_key.get((s["address"], s["sale_date"]), {})
        desc = arc.get("official_bca_description") or ""
        yb_old = s.get("year_built") or ""
        pre = s["sale_date"] < p["first_issue"]
        # 定性:旧建筑描述/旧年份 → CONFIRMED_TEARDOWN;否则 PRE_PERMIT_SALE
        if pre:
            if yb_old and int(yb_old) < 2000:
                verdict, why = "CONFIRMED_TEARDOWN_SALE", \
                    f"yb{yb_old}<2000,旧房于许可前售出"
            elif "house" in desc.lower() or "1 sty" in desc.lower():
                verdict, why = "CONFIRMED_TEARDOWN_SALE", \
                    f"desc={desc[:30]},旧形态房"
            else:
                verdict, why = "PRE_PERMIT_SALE", "历史属性不足以确认拆地"
        else:
            verdict, why = "POST_PERMIT_SALE_REVIEW", "许可后成交,需人工定性"
        mpx_sale_ver.append({
            "project": p["project_id"], "address": p["address"],
            "permit_issue": p["first_issue"], "sale_date": s["sale_date"],
            "sale_price": s["_p"], "sale_class": s["product_class"],
            "sale_yb": yb_old, "bca_desc": desc[:60],
            "verdict": verdict, "basis": why,
            "post": not pre})

# ============ P0d. 三个特殊记录定论 ============
specials = [
    {"address": "2609 DUNDAS ST", "finding":
     "HOUSE_DETACHED yb2025 fa1204sf — 新建小房(非multiplex单元);"
     "MPX许可为同址独立阶段,成交不构成MPX证据", "verdict": "NOT_MPX_SALE"},
    {"address": "1909 CHARLES ST", "finding":
     "year_built='1909'(疑为门牌号误入字段或真1909老楼)+ 'Multiple Residence'"
     " + bca_improvement=$1.67M — 旧多户楼整体出售,非新建multiplex",
     "verdict": "OLD_MULTI_RES_NOT_NEWBUILD"},
    {"address": "3122 MARINE DR SE", "finding":
     "'Multi-Family (Vacant)' + improvement=0 + 30,765sf — "
     "多户地块空地出售,无建筑", "verdict": "VACANT_LAND_NOT_BUILDING"}]

# ============ P1a. HOUSE 217 四桶 ============
house_rows = [r for r in B.COMPS if r["_prod"] == "HOUSE"]
buckets = {"VERIFIED_FULL": [], "RECOVERED_NEEDS_REVIEW": [],
           "KEY_UNCERTAINTY": [], "BANNED": []}
for r in house_rows:
    if not B.usable(r):
        if r.get("_yb_conflict"):
            # 冲突记录单独核实:售价 vs 当年房值
            buckets["BANNED"].append(r)
        continue
    if r.get("_yb_src") or r.get("_fa_src"):
        buckets["RECOVERED_NEEDS_REVIEW"].append(r)
    elif (not r["pid"] and r.get("asof_status") != "CONSISTENT") or \
            not r["gst_basis"].startswith(("new", "res")) or \
            r["_scope"] == "WHOLE_SUSPECT":
        buckets["KEY_UNCERTAINTY"].append(r)
    else:
        buckets["VERIFIED_FULL"].append(r)
house_ev = [{"address": r["address"], "comm": r["_comm"],
             "sale_date": r["sale_date"], "price": r["_p"],
             "fa": r["_fa"], "yb": r["_yb"], "pid": r["pid"],
             "bucket": bk, "basis":
             ("pid+CONSISTENT+原始字段" if bk == "VERIFIED_FULL" else
              "yb/fa恢复,需二次验证" if bk == "RECOVERED_NEEDS_REVIEW" else
              "无pid且非CONSISTENT" if bk == "KEY_UNCERTAINTY" else
              "时点冲突,疑拆地销售"),
             "price_source": r["supplemental_source"] or "comps-v2"}
            for bk in buckets for r in buckets[bk]]

# ============ P1b. DUPLEX 389 逐条标注 ============
dup_ev = []
for r in B.COMPS:
    if r["_prod"] == "DUPLEX":
        m = layered_match(r)
        dup_ev.append({
            "address": r["address"], "date": r["sale_date"],
            "price": r["_p"], "fa": r["_fa"], "scope": r["_scope"],
            "pair": r.get("_pair", ""), "pid": r["pid"],
            "unit_no": r["_unit_no"] or "",
            "strata_plan": (m["unit_tax"] or {}).get("plan", ""),
            "permit_project": (m["proj"] or {}).get("project_id", ""),
            "gst_basis": r["gst_basis"], "tier": r["_tier"],
            "usable": B.usable(r), "match_conf": m["confidence"],
            "match_keys": m["match_keys"]})

# ============ P3. 分层基准 ============
def bench3(recs, label):
    out = {}
    good = [r for r in recs if B.usable(r)
            and K.norm_addr(r["address"]) not in K.ANCHOR_EXCLUDE]
    ver = [r for r in good if r["_tier"] == "VERIFIED_COMPARABLE"]
    lim = [r for r in good if r["_tier"] != "VERIFIED_COMPARABLE"]
    for lo, hi in K.BANDS:
        for wname, wstart in B.WIN.items():
            for grp, gl in ((ver, "VERIFIED_MARKET_SALES"),
                            (lim, "USABLE_WITH_LIMITATIONS")):
                sel = [r for r in grp if lo <= r["_fa"] < hi
                       and r["sale_date"] >= wstart]
                if len(sel) < 3:
                    continue
                psf = sorted(x["_p"] / x["_fa"] for x in sel)
                n = len(psf)
                out[f"{label}|{lo}-{hi if hi<99999 else 'up'}|{wname}|{gl}"] = {
                    "n_sales": n,
                    "n_projects": len({x["_base"] for x in sel}),
                    "n_permit_linked": len({x["_base"] for x in sel
                                            if x["_base"] in B.proj_by_base}),
                    "n_first_sale": sum(1 for x in sel
                                        if x["gst_basis"] == "new_first_sale"),
                    "n_resale": sum(1 for x in sel
                                    if x["gst_basis"].startswith("res")),
                    "n_unknown_gst": sum(1 for x in sel
                                         if not x["gst_basis"].startswith(
                                             ("new", "res"))),
                    "p25": round(psf[n // 4]), "p50": round(psf[n // 2]),
                    "p75": round(psf[3 * n // 4]),
                    "fresh": max(x["sale_date"] for x in sel),
                    "conf": ("BORROWED_" if label == "_CITY" else "LOCAL_") +
                            ("A" if n >= 8 else "B" if n >= 5 else "C")}
    return out


bench2 = {}
for (comm, prod), recs in B.by_cp.items():
    for k, v in bench3(recs, comm).items():
        bench2[f"{k.split('|',1)[0]}|{prod}|" + k.split("|", 1)[1]] = v

# ============ 核销汇总 ============
gap = [{"product": p,
        "sales_confirmed": sum(1 for s in B.sales_ev if s["product"] == p),
        "projects_confirmed": len({s["address"] for s in B.sales_ev
                                   if s["product"] == p}),
        "verified_comparable": sum(1 for s in B.sales_ev
                                   if s["product"] == p and
                                   s["tier"] == "VERIFIED_COMPARABLE"),
        "partial": sum(1 for r in B.COMPS if r["_prod"] == p and
                       r["_tier"] == "PARTIAL_EVIDENCE"),
        "unverified": sum(1 for r in B.COMPS if r["_prod"] == p and
                          r["_tier"] == "UNVERIFIED"),
        "fresh": max((s["sale_date"] for s in B.sales_ev
                      if s["product"] == p), default=""),
        "gap": {"HOUSE": "yb_no_source×829+MLS首销渠道",
                "DUPLEX": "半户fa交叉验证+MLS渠道",
                "MPX": "单元成交源缺失(MLS/GVR)"}[p]}
       for p in ("HOUSE", "DUPLEX", "MPX")]


def wcsv(path, rows):
    if not rows:
        path.write_text(""); return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


wcsv(OUTD / "mpx_identity_linkage_audit.csv", link_audit)
wcsv(OUTD / "mpx_sale_verification.csv", mpx_sale_ver)
wcsv(OUTD / "house_verified_evidence.csv", house_ev)
wcsv(OUTD / "duplex_project_unit_audit.csv", dup_ev)
wcsv(OUTD / "verified_market_benchmark.csv",
     [{"key": k, **v} for k, v in sorted(bench2.items())])
wcsv(OUTD / "evidence_gap_reconciliation.csv", gap)

summary = {
    "linkage": {"rows": len(link_audit),
                "conf": dict(Counter(a["confidence"] for a in link_audit)),
                "methods": dict(Counter(a["match_method"]
                                        for a in link_audit))},
    "mpx_sales": dict(Counter(x["verdict"] for x in mpx_sale_ver)),
    "specials": {s["address"]: s["verdict"] for s in specials},
    "house_buckets": {k: len(v) for k, v in buckets.items()},
    "bench_cells": len(bench2),
    "gap": gap,
}
(OUTD / "manifest.json").write_text(json.dumps({
    "version": "v1.2.1", "run": datetime.datetime.now().isoformat(),
    "parent_commit": "b890f39497330f0861d723cbdf8cf4c0cb4c7149",
    "input_sha256": B.HASHES,
    "inputs": {k: str(v.relative_to(ROOT)) for k, v in B.INPUTS.items()},
    "counts": summary}, ensure_ascii=False, indent=1))
(OUTD / "evidence_recovery_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("wrote audit_v1_2_1/*")
