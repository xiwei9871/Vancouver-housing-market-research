#!/usr/bin/env python3
"""Evidence Recovery V1.2 — 项目身份与销售证据核销。
核心:permit→parcel→building→strata unit→sale 证据链;
MPX 不误判、HOUSE 核销恒等式、DUPLEX 单元身份、基准血缘。
纯函数在 nbx_core.py;本文件只做 IO+编排。不改 V3s。
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                       # noqa: E402
from valuation_v2_sites import dev_cost    # noqa: E402

RAW = ROOT / "data/research/raw/official"
LOC = ROOT / "data/research/raw/location"
NORM = ROOT / "data/research/normalized"
OUTAUD = ROOT / "docs/audits/newbuild_coverage_v1"
OUTD = NORM / "audit_v1_2"
OUTD.mkdir(parents=True, exist_ok=True); OUTAUD.mkdir(parents=True, exist_ok=True)

MPX_ERA = "2023-10-01"   # R1-1 multiplex 政策生效(2023年秋)

INPUTS = {
    "permits": RAW / "issued-permits-ldh-2017.csv",
    "tax2026": RAW / "property-tax-2026-yb2018plus.csv",
    "tax2023": RAW / "property-tax-2023-yb2018plus.csv",
    "comps": NORM / "standard-residential-comps-v2.csv",
    "v3s": NORM / "valuation-v3s-shadow-2026-10-22.json",
    "patch": NORM / "house_anchor_patch_v3s.json",
    "arcgis_all": LOC / "bca-arcgis-sales-2025-present-all.json",
}
HASHES = {k: hashlib.sha256(p.read_bytes()).hexdigest()
          for k, p in INPUTS.items() if p.exists()}

V3S = json.loads(INPUTS["v3s"].read_text())
HPATCH = json.loads(INPUTS["patch"].read_text()) \
    if INPUTS["patch"].exists() else {}

# ============ 1. 许可册 → permit_record + project ============
permits = list(csv.DictReader(open(INPUTS["permits"], encoding="utf-8-sig"),
                              delimiter=";"))
precs, proj_by_addr = [], defaultdict(list)
for r in permits:
    prod, tags = K.classify_permit(r["specificusecategory"],
                                   r["projectdescription"])
    addr = K.norm_addr(r["address"])
    lat = None
    if r.get("geo_point_2d"):
        try:
            lat = float(r["geo_point_2d"].split(",")[0])
        except Exception:
            pass
    geo = r["geolocalarea"] or "UNKNOWN"
    comm = K.rc_split(geo, lat) if geo == "Renfrew-Collingwood" else geo
    precs.append({"permit_id": r["permitnumber"], "addr": addr,
                  "product": prod, "tags": sorted(tags),
                  "units": K.parse_units(r["projectdescription"]),
                  "issue_date": r["issuedate"], "issue_year": r["issueyear"],
                  "comm": comm, "desc": (r["projectdescription"] or "")[:160]})
    proj_by_addr[addr].append(precs[-1])

projects = []
for addr, ps in proj_by_addr.items():
    ps.sort(key=lambda x: x["issue_date"])
    units_total = 0
    for p in ps:
        u = p["units"] if p["units"] is not None else {
            "HOUSE": 1, "DUPLEX": 2, "LANEWAY": 1}.get(p["product"])
        units_total += u or 0
    projects.append({
        "project_id": "P" + hashlib.md5(addr.encode()).hexdigest()[:10],
        "address": addr, "comm": ps[0]["comm"], "n_permits": len(ps),
        "permit_ids": "|".join(p["permit_id"] for p in ps),
        "products": "|".join(sorted({p["product"] for p in ps})),
        "first_issue": ps[0]["issue_date"], "last_issue": ps[-1]["issue_date"],
        "units_est": units_total,
        "mpx_era": ps[-1]["issue_date"] >= MPX_ERA,
        "status": "ISSUED"})
proj_by_id = {p["project_id"]: p for p in projects}
proj_by_base = {p["address"]: p for p in projects}   # 地址即基址(无单元前缀)

# ============ 2. 单元册 ============
tax26 = list(csv.DictReader(open(INPUTS["tax2026"], encoding="utf-8-sig"),
                            delimiter=";"))
tax23 = list(csv.DictReader(open(INPUTS["tax2023"], encoding="utf-8-sig"),
                            delimiter=";"))
tax_by_pid = defaultdict(list)
tax_by_addr = defaultdict(list)
land_by_addr = {}
for r in tax26:
    if r["pid"]:
        tax_by_pid[r["pid"]].append(r)
    a = f"{r['from_civic_number'] or r['to_civic_number']} " \
        f"{r['street_name']}".strip()
    na = K.norm_addr(a)
    tax_by_addr[na].append(r)
    if r["legal_type"] == "LAND":
        land_by_addr[na] = r   # 母地块 folio

# ============ 3. 成交层 ============
COMPS = list(csv.DictReader(open(INPUTS["comps"], encoding="utf-8-sig")))
for r in COMPS:
    r["_p"], r["_fa"], r["_lot"], r["_yb"] = (
        K.num(r["sale_price"]), K.num(r["floor_above_sf"]),
        K.num(r["lot_sqft"]), K.num(r["year_built"]))
    r["_prod"], r["_scope"] = K.CLASS_MAP.get(
        r["product_class"], ("OTHER", "?"))
    r["_comm"] = K.rc_split(r["official_area"], r.get("lat"))
    r["_base"] = K.base_addr(r["address"])
    r["_unit_no"] = K.unit_prefix(r["address"])

SNAPSHOT = max(r["sale_date"] for r in COMPS if r["sale_date"])
snap = datetime.date.fromisoformat(SNAPSHOT)
WIN = {"2023+": "2023-01-01",
       "recent24": (snap - datetime.timedelta(days=730)).isoformat(),
       "recent12": (snap - datetime.timedelta(days=365)).isoformat()}

arcgis = json.loads(INPUTS["arcgis_all"].read_text())
arcgis = arcgis if isinstance(arcgis, list) else \
    next(v for v in arcgis.values() if isinstance(v, list))
arc_by_key = {(r["address"], r["sale_date"]): r for r in arcgis}
arc_excl = [r for r in arcgis
            if (r["address"], r["sale_date"]) not in
            {(c["address"], c["sale_date"]) for c in COMPS}]

# ============ P0. MPX 证据链 ============
# 对每个待核记录构建:permit→parcel→building→strata→sale
def evidence_chain(r):
    """返回完整证据字典"""
    b = r["_base"]
    proj = proj_by_base.get(b)
    arc = arc_by_key.get((r["address"], r["sale_date"]), {})
    desc = arc.get("official_bca_description") or ""
    form, why = K.desc_verdict(desc)
    taxs = tax_by_addr.get(b, [])
    unit_tax = (tax_by_pid.get(r.get("pid") or "") or [None])[0]
    return {
        "permit_project_id": proj["project_id"] if proj else "",
        "approved_project_type": proj["products"] if proj else "",
        "project_unit_count": proj["units_est"] if proj else "",
        "mpx_era_permit": proj["mpx_era"] if proj else "",
        "bca_building_form": form if form != "UNRESOLVED" else "",
        "form_reason": why,
        "strata_unit_type": ("STRATA_UNIT" if r.get("is_strata") == "Y"
                             or r["_unit_no"] else "NON_STRATA"),
        "unit_no": r["_unit_no"] or "",
        "unit_pid": r.get("pid") or (unit_tax or {}).get("pid", ""),
        "strata_plan": (unit_tax or {}).get("plan", "") or
                       (taxs[0].get("plan", "") if taxs else ""),
        "parent_parcel_pid": (land_by_addr.get(b) or {}).get("pid", ""),
        "tax_zoning": (unit_tax or {}).get("zoning_district", "") or
                      (taxs[0].get("zoning_district", "") if taxs else ""),
        "tax_yb": (unit_tax or {}).get("year_built", ""),
        "n_tax_folios_at_addr": len(taxs),
        "desc": desc[:80],
    }


def mpx_verdict(e, r):
    """多证据定案;permit 类型优先于 BCA 形态。返回(verdict, confidence)"""
    apt = e["approved_project_type"]
    if "MPX" in apt.split("|"):
        return "CONFIRMED_MULTIPLEX", "HIGH"          # 许可 MPX:形态可为城市屋
    if "DUPLEX" in apt.split("|") and r["_prod"] in (
            "STRATA_UNKNOWN", "MPX_CAND"):
        return "CONFIRMED_DUPLEX", "HIGH"
    if "TOWNHOUSE" in apt.split("|"):
        return "TOWNHOUSE", "HIGH"
    f = e["bca_building_form"]
    if f == "APARTMENT":
        return "APARTMENT", "MED"
    if f == "TOWNHOUSE":
        return "TOWNHOUSE", "MED"                    # 辅助池,非 MPX
    if f == "CONFIRMED_MULTIPLEX":
        return "CONFIRMED_MULTIPLEX", "MED"
    if f == "CONFIRMED_DUPLEX":
        return "CONFIRMED_DUPLEX", "MED"
    if f == "CONFIRMED_HOUSE":
        return "HOUSE_STRATA", "LOW"
    if f == "OTHER":
        return "OTHER", "MED"
    return "UNRESOLVED", "LOW"


TARGET = ("STRATA_UNKNOWN", "MPX_CAND", "MPX_WHOLE", "OTHER",
          "DUPLEX", "HOUSE")
mpx_audit = []
for r in COMPS:
    if r["_prod"] in ("STRATA_UNKNOWN", "MPX_CAND", "MPX_WHOLE", "OTHER"):
        e = evidence_chain(r)
        v, conf = mpx_verdict(e, r)
        r["_verdict"], r["_conf"], r["_ev"] = v, conf, e
        # 恢复入池
        if v == "CONFIRMED_MULTIPLEX":
            r["_prod"], r["_scope"] = "MPX", "UNIT"
        elif v == "CONFIRMED_DUPLEX":
            r["_prod"], r["_scope"] = "DUPLEX", "HALF"
        elif v == "HOUSE_STRATA":
            r["_prod"], r["_scope"] = "HOUSE", "UNIT"
        elif v == "TOWNHOUSE":
            r["_prod"], r["_scope"] = "TOWNHOUSE", "UNIT"
        elif v == "APARTMENT":
            r["_prod"], r["_scope"] = "APARTMENT", "UNIT"
        mpx_audit.append({"address": r["address"], "date": r["sale_date"],
                          "price": r["_p"], "old_class": r["product_class"],
                          "verdict": v, "confidence": conf,
                          **{k: e[k] for k in
                             ("permit_project_id", "approved_project_type",
                              "project_unit_count", "bca_building_form",
                              "strata_unit_type", "unit_pid", "strata_plan",
                              "parent_parcel_pid", "tax_zoning", "tax_yb",
                              "n_tax_folios_at_addr", "desc")}})
    else:
        r["_verdict"], r["_conf"], r["_ev"] = r["_prod"], "HIGH", {}

# MPX 项目级核销:341 许可 ↔ folio ↔ 成交
mpx_projects = [p for p in projects if "MPX" in p["products"].split("|")]
sales_by_base = defaultdict(list)
for r in COMPS:
    sales_by_base[r["_base"]].append(r)
mpx_linkage = []
for p in mpx_projects:
    folios = tax_by_addr.get(p["address"], [])
    sales = sales_by_base.get(p["address"], [])
    strata_units = [f for f in folios if f["legal_type"] == "STRATA"]
    pre = [s for s in sales if s["sale_date"] < p["first_issue"]]
    post = [s for s in sales if s["sale_date"] >= p["first_issue"]]
    status = ("POSSIBLE_POST_PERMIT_SALE" if post else
              "TEARDOWN_SALE_ONLY" if pre else
              "HAS_STRATA_NO_SALE" if strata_units else "PERMIT_ONLY")
    mpx_linkage.append({
        "project_id": p["project_id"], "address": p["address"],
        "comm": p["comm"], "permits": p["n_permits"],
        "units_est": p["units_est"], "mpx_era": p["mpx_era"],
        "first_issue": p["first_issue"],
        "tax_folios": len(folios), "strata_units": len(strata_units),
        "sales_pre_permit": len(pre), "sales_post_permit": len(post),
        "sale_ids": "|".join(f"{s['sale_date']}" for s in sales),
        "evidence_status": status})

# arcgis 排除集重审计(V1.1 结论复核)
arc_excl_audit = [{"address": r["address"], "date": r["sale_date"],
                   "price": r.get("sale_price"),
                   "desc": r.get("official_bca_description") or "",
                   "verdict": K.desc_verdict(
                       r.get("official_bca_description"))[0],
                   "reason": K.desc_verdict(
                       r.get("official_bca_description"))[1],
                   "permit_at_addr":
                   "|".join(sorted({p["product"] for p in
                                    proj_by_addr.get(
                                        K.base_addr(r["address"]), [])}))}
                  for r in arc_excl]

# ============ P1a. HOUSE yb 核销恒等式 ============
enrich_pool, fa_pool = defaultdict(set), {}
for f in LOC.glob("*.json"):
    try:
        j = json.loads(f.read_text())
        rows = j if isinstance(j, list) else \
            next((v for v in j.values() if isinstance(v, list)), [])
        for e in rows:
            k = (e.get("address"), e.get("sale_date"))
            if e.get("year_built"):
                enrich_pool[k].add(str(e["year_built"]))
            a = K.num(e.get("bca_first_floor_area_sqft"))
            b = K.num(e.get("bca_second_floor_area_sqft"))
            if a:
                fa_pool[k] = a + (b or 0)
    except Exception:
        continue

yb_cat = Counter()
yb_rows = []
for r in COMPS:
    if r["_prod"] != "HOUSE" or r["_yb"]:
        continue
    key = (r["address"], r["sale_date"])
    cat, yb = None, None
    if key in enrich_pool:
        ys = enrich_pool[key]
        if len(ys) == 1:
            yb = int(next(iter(ys)))
            cat = "CONFIRMED_NEW_ENRICHED" if yb >= K.NEWYB else \
                  "CONFIRMED_OLD_ENRICHED"
            r["_yb"], r["_yb_src"] = yb, "enriched"
        else:
            cat = "AMBIGUOUS"
    else:
        cands = tax_by_pid.get(r["pid"], []) if r["pid"] else \
            tax_by_addr.get(K.norm_addr(r["address"]), [])
        cands = [t for t in cands if t.get("year_built")]
        if len(cands) == 1:
            yb = int(cands[0]["year_built"])
            cat = "CONFIRMED_NEW_TAX" if yb >= K.NEWYB else "CONFIRMED_OLD_TAX"
            r["_yb"], r["_yb_src"] = yb, "tax_folio"
        elif len(cands) > 1:
            cat = "AMBIGUOUS"
        else:
            cat = "NO_SOURCE"
    yb_cat[cat] += 1
    # 时点冲突:恢复 yb > 成交年 → 该成交发生时房还未建
    conflict = ""
    if yb and r["sale_date"] and int(r["sale_date"][:4]) < yb:
        conflict = "TEMPORAL_CONFLICT"
        r["_yb_conflict"] = True
    yb_rows.append({"address": r["address"], "date": r["sale_date"],
                    "cat": cat, "yb": yb or "", "conflict": conflict,
                    "price": r["_p"]})
# fa 恢复
for r in COMPS:
    if not r["_fa"]:
        v = fa_pool.get((r["address"], r["sale_date"]))
        if v:
            r["_fa"], r["_fa_src"] = v, "enriched"

# ============ P1b. DUPLEX 单元身份核销 ============
dup_groups = defaultdict(list)
for r in COMPS:
    if r["_prod"] == "DUPLEX":
        dup_groups[r["_base"]].append(r)
dup_audit = []
dup_stats = {"groups": 0, "paired_units": 0, "resale_same_unit": 0,
             "whole": 0, "lone": 0}
for b, rs in dup_groups.items():
    dup_stats["groups"] += 1
    halves = [x for x in rs if x["_scope"] == "HALF"]
    wholes = [x for x in rs if x["_scope"] == "WHOLE"]
    pid_groups = defaultdict(list)
    for x in halves:
        pid_groups[x["pid"] or x["_unit_no"] or "?"].append(x)
    distinct_units = len(pid_groups)
    resales = sum(len(v) - 1 for v in pid_groups.values())
    verdict = ("PAIRED_UNITS" if distinct_units >= 2 else
               "RESALE_SAME_UNIT" if resales else
               "WHOLE_SALE" if wholes and not halves else "LONE_HALF")
    if wholes and halves:
        verdict += "+WHOLE"
    for x in rs:
        x["_pair"] = verdict
    dup_stats["paired_units" if distinct_units >= 2 else
              "resale_same_unit" if resales else
              "whole" if wholes else "lone"] += 1
    dup_audit.append({"base": b, "records": len(rs), "halves": len(halves),
                      "wholes": len(wholes), "distinct_unit_keys": distinct_units,
                      "resales_same_unit": resales, "verdict": verdict,
                      "prices": "|".join(str(int(x["_p"])) for x in rs),
                      "dates": "|".join(x["sale_date"] for x in rs),
                      "pids": "|".join(x["pid"] or "?" for x in rs)})

# ============ P2. 四级证据 ============
def tier4(r):
    if r["_prod"] not in ("HOUSE", "DUPLEX", "MPX"):
        return "UNVERIFIED"
    if not (r["_p"] and r["sale_date"]):
        return "UNVERIFIED"
    if not (r["pid"] or r.get("asof_status") == "CONSISTENT"):
        return "UNVERIFIED"
    if (r["_yb"] or 0) >= K.NEWYB and r["_fa"] \
            and r.get("asof_status") not in K.EXCL:
        soft = (r.get("_yb_src") or r.get("_fa_src") or
                r["_scope"] == "WHOLE_SUSPECT" or
                not r["gst_basis"].startswith(("new", "res")))
        return "USABLE_WITH_LIMITATIONS" if soft else "VERIFIED_COMPARABLE"
    return "PARTIAL_EVIDENCE"


for r in COMPS:
    r["_tier"] = tier4(r)


def usable(r):
    return (r["_prod"] in ("HOUSE", "DUPLEX", "MPX") and
            (r["_yb"] or 0) >= K.NEWYB and r["_fa"] and r["_p"] and
            r.get("asof_status") not in K.EXCL and
            not r.get("_yb_conflict"))   # 恢复yb>成交年=重建前地售,剔除


# ============ 瀑布 ============
water = defaultdict(lambda: defaultdict(int))
for p in projects:
    for prod in p["products"].split("|"):
        if prod in ("HOUSE", "DUPLEX", "MPX"):
            water[(p["comm"], prod)]["projects"] += 1
for r in COMPS:
    w = water[(r["_comm"], r["_prod"])]
    w["comp_records"] += 1
    if r.get("asof_status") in K.EXCL:
        w["x_asof"] += 1; continue
    w["asof_ok"] += 1
    if r["gst_basis"] == "new_first_sale":
        w["new_first_sale"] += 1
    if (r["_yb"] or 0) >= K.NEWYB:
        w["yb2018+"] += 1
    if not r["_yb"]:
        w["yb_missing"] += 1
    if r["_fa"]:
        w["has_fa"] += 1
    if r["_p"]:
        w["has_price"] += 1
    if usable(r):
        w["usable_newbuild"] += 1

sales_ev = [{"product": r["_prod"], "address": r["address"],
             "comm": r["_comm"], "sale_date": r["sale_date"],
             "sale_price": r["_p"], "floor_above_sf": r["_fa"],
             "lot_sqft": r["_lot"], "year_built": r["_yb"],
             "scope": r["_scope"], "tier": r["_tier"],
             "yb_src": r.get("_yb_src", ""), "fa_src": r.get("_fa_src", ""),
             "gst_basis": r["gst_basis"] or "unknown_gst",
             "asof_status": r["asof_status"], "pair": r.get("_pair", ""),
             "verdict": r.get("_verdict", ""), "pid": r["pid"],
             "src": r["supplemental_source"] or "comps-v2"}
            for r in COMPS if usable(r)]

# ============ P3. 基准 V1.2 ============
def bench(recs, label):
    out = {}
    good = [r for r in recs if usable(r)
            and K.norm_addr(r["address"]) not in K.ANCHOR_EXCLUDE]
    for lo, hi in K.BANDS:
        inb = [r for r in good if lo <= r["_fa"] < hi]
        for wname, wstart in WIN.items():
            sel = [r for r in inb if r["sale_date"] >= wstart]
            if len(sel) < 3:
                continue
            psf = sorted(x["_p"] / x["_fa"] for x in sel)
            n = len(psf)
            fs = sum(1 for x in sel if x["gst_basis"] == "new_first_sale")
            un = sum(1 for x in sel if not x["gst_basis"].startswith(
                ("new", "res")))
            nlink = len({x["_base"] for x in sel
                         if x["_base"] in proj_by_base})
            out[f"{label}|{lo}-{hi if hi < 99999 else 'up'}|{wname}"] = {
                "n_sales": n, "n_projects": len({x["_base"] for x in sel}),
                "n_projects_permit_linked": nlink,
                "n_first_sale": fs, "n_resale": n - fs - un,
                "n_unknown_gst": un,
                "p25": round(psf[n // 4]), "p50": round(psf[n // 2]),
                "p75": round(psf[3 * n // 4]),
                "fresh": max(x["sale_date"] for x in sel),
                "fa_src_enriched": sum(1 for x in sel
                                       if x.get("_fa_src")),
                "yb_src_recovered": sum(1 for x in sel
                                        if x.get("_yb_src")),
                "grade": "A" if n >= 8 else "B" if n >= 5 else "C"}
    return out


benchmarks = {}
by_cp = defaultdict(list)
for r in COMPS:
    if r["_prod"] in ("HOUSE", "DUPLEX", "MPX"):
        by_cp[(r["_comm"], r["_prod"])].append(r)
        by_cp[("_CITY", r["_prod"])].append(r)
for (comm, prod), recs in by_cp.items():
    for k, v in bench(recs, comm).items():
        g = v.pop("grade")
        benchmarks[f"{k.split('|',1)[0]}|{prod}|" +
                   k.split("|", 1)[1]] = {
            **v,
            "confidence": ("BORROWED_" + g if comm == "_CITY"
                           else "LOCAL_" + g)}

# ============ 影子(冻结:不写新 ROI 结论,只留对照) ============
shadow = []
for s in V3S["sites"]:
    if s["status"] != "ACTIVE":
        continue
    for p in s["plans"]:
        if not p.get("eligible"):
            continue
        c = p.get("contract")
        if p["product"] == "DUPLEX" and p.get("contract_stable"):
            c = p["contract_stable"]
        if p["product"] == "HOUSE" and s["address"] in HPATCH:
            c = HPATCH[s["address"]]["new_contract"]
        if not c:
            continue
        ptype = "MPX" if p["product"] in ("TRIPLEX", "FOURPLEX",
                                          "MULTIPLEX") else p["product"]
        cost_tier = 350 if ptype in ("HOUSE", "DUPLEX") else 400
        fa = p["build_sf"] / (p["units"] or 1)
        blo = K.band_lo(fa)
        bmk, key = None, None
        for ck in (s["comm"], "_CITY"):
            hh = next(f"{lo}-{hi if hi<99999 else 'up'}"
                      for lo, hi in K.BANDS if lo == blo)
            k = f"{ck}|{ptype}|{hh}|2023+"
            if benchmarks.get(k):
                bmk, key = benchmarks[k], k
                break
        ind_p = bmk["p50"] * fa * (p["units"] or 1) if bmk else None
        shadow.append({
            "listing": s["address"], "plan": p["product"],
            "v3s_price": round(c), "bench_key": key,
            "borrowed": bmk["confidence"].startswith("BORROWED")
            if bmk else "NONE",
            "bench_n": bmk["n_sales"] if bmk else 0,
            "bench_projects": bmk["n_projects"] if bmk else 0,
            "bench_p50_psf": bmk["p50"] if bmk else None,
            "indep_price": round(ind_p) if bmk else None,
            "v3s_roi": round(
                dev_cost(c, s["ask"], p["build_sf"], p["units"],
                         cost_tier)["roi"], 4),
            "indep_roi": round(
                dev_cost(ind_p, s["ask"], p["build_sf"], p["units"],
                         cost_tier)["roi"], 4) if bmk else None,
            "diff": round(c / ind_p - 1, 3) if bmk else None,
            "confidence": bmk["confidence"] if bmk else "NOT_ESTIMABLE",
            "fresh": bmk["fresh"] if bmk else None})

# ============ coverage v1/v1.1/v1.2 ============
cov = []
cells = sorted({(r["_comm"], r["_prod"]) for r in COMPS
                if r["_prod"] in ("HOUSE", "DUPLEX", "MPX")})
for comm, prod in cells:
    rs = [r for r in COMPS if r["_comm"] == comm and r["_prod"] == prod]
    v1 = sum(1 for r in rs if (K.num(r["year_built"]) or 0) >= K.NEWYB
             and K.num(r["floor_above_sf"]) and K.num(r["sale_price"])
             and r.get("asof_status") not in K.EXCL)
    v12 = sum(1 for r in rs if usable(r))
    rec = sum(1 for r in rs if usable(r)
              and (r.get("_yb_src") or r.get("_fa_src")
                   or r.get("_verdict", "").startswith("CONFIRMED")))
    cov.append({"comm": comm, "product": prod, "v1_usable": v1,
                "v1_1_usable": v12 - rec + sum(
                    1 for r in rs if usable(r) and r.get("_yb_src")),
                "v1_2_usable": v12, "added_vs_v1": v12 - v1,
                "recovered_records": rec,
                "still_missing": sum(1 for r in rs
                                     if r.get("asof_status") not in K.EXCL
                                     and not usable(r)),
                "main_gap": "yb_no_source" if any(
                    y["cat"] == "NO_SOURCE" for y in yb_rows
                    if y["address"] in {x["address"] for x in rs})
                else "fa/capture"})

# ============ tier 核销表 ============
tier_rec = [{"product": p, "tier": t, "n": n}
            for (p, t), n in sorted(Counter(
                (r["_prod"], r["_tier"]) for r in COMPS).items())]

# ============ 写出 ============
def wcsv(path, rows):
    if not rows:
        path.write_text(""); return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


wcsv(OUTD / "permit_project_reconciliation.csv",
     [{"project_id": p["project_id"], "address": p["address"],
       "comm": p["comm"], "n_permits": p["n_permits"],
       "permit_ids": p["permit_ids"], "products": p["products"],
       "units_est": p["units_est"], "mpx_era": p["mpx_era"],
       "first_issue": p["first_issue"], "last_issue": p["last_issue"]}
      for p in projects])
wcsv(OUTD / "mpx_project_unit_linkage_summary.csv", mpx_linkage)
wcsv(OUTD / "mpx_misclassification_audit.csv", mpx_audit)
wcsv(OUTD / "house_yb_reconciliation.csv", yb_rows)
wcsv(OUTD / "duplex_unit_identity_audit.csv", dup_audit)
wcsv(OUTD / "evidence_tier_reconciliation.csv", tier_rec)
wcsv(OUTD / "arcgis_excluded_audit.csv", arc_excl_audit)
wcsv(OUTD / "coverage_waterfall.csv",
     [{"comm": c, "product": p, **v} for (c, p), v in sorted(water.items())])
wcsv(OUTD / "sales_evidence.csv", sales_ev)
wcsv(OUTD / "regional_benchmark_v1_2.csv",
     [{"key": k, **v} for k, v in sorted(benchmarks.items())])
wcsv(OUTD / "shadow_impact.csv", shadow)
wcsv(OUTD / "coverage_v1_v1_1_v1_2.csv", cov)

usable_tot = Counter(r["product"] for r in sales_ev)
summary = {
    "snapshot_date": SNAPSHOT, "windows": WIN,
    "permits": {"records": len(precs), "projects": len(projects),
                "products": dict(Counter(p["product"] for p in precs)),
                "units_est": sum(p["units_est"] for p in projects)},
    "mpx": {"permit_projects": len(mpx_projects),
            "with_any_sale": sum(1 for x in mpx_linkage
                                 if x["sales_pre_permit"] or
                                 x["sales_post_permit"]),
            "teardown_sales": sum(1 for x in mpx_linkage
                                  if x["evidence_status"] ==
                                  "TEARDOWN_SALE_ONLY"),
            "post_permit_sales": sum(1 for x in mpx_linkage
                                     if x["evidence_status"] ==
                                     "POSSIBLE_POST_PERMIT_SALE"),
            "with_strata": sum(1 for x in mpx_linkage
                               if x["strata_units"]),
            "permit_only": sum(1 for x in mpx_linkage
                               if x["evidence_status"] == "PERMIT_ONLY"),
            "audit_verdicts": dict(Counter(a["verdict"]
                                           for a in mpx_audit)),
            "mpx_whole_bldg_sales": sum(
                1 for a in mpx_audit
                if a["verdict"] == "CONFIRMED_MULTIPLEX"),
            "ZERO_VERIFIED_MPX_UNIT_SALES_IN_CAPTURED_DATA":
            not any(s["product"] == "MPX" for s in sales_ev)},
    "yb_recon": dict(yb_cat),
    "yb_temporal_conflicts": sum(1 for y in yb_rows if y["conflict"]),
    "fa_recovered": sum(1 for r in COMPS if r.get("_fa_src")),
    "duplex": dup_stats,
    "arcgis_excluded": dict(Counter(a["verdict"]
                                    for a in arc_excl_audit)),
    "tier_counts": {f"{p}|{t}": n for (p, t), n in
                    Counter((r["_prod"], r["_tier"])
                            for r in COMPS).items()},
    "usable": dict(usable_tot),
    "benchmark_cells": len(benchmarks),
    "shadow_rows": len(shadow),
    "shadow_estimable": sum(1 for s in shadow if s["bench_n"]),
}
(OUTD / "evidence_recovery_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1))
(OUTD / "manifest.json").write_text(json.dumps({
    "version": "v1.2", "run": datetime.datetime.now().isoformat(),
    "snapshot_date": SNAPSHOT, "windows": WIN,
    "input_sha256": HASHES,
    "inputs": {k: str(v.relative_to(ROOT)) for k, v in INPUTS.items()},
    "counts": summary}, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("wrote audit_v1_2/*")
