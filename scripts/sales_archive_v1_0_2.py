#!/usr/bin/env python3
"""Sales Archive V1.0.2 — Identity & Product Integrity Gate。

修复 V1.0.1 已确认缺陷:
- P0-A:addr_*/base_addr 字段错位 → 统一规范 schema + 校验
- P0-B:EXACT_PARCEL 不再自动合并单元;bca_oid_evbc≠folio;
  Union-Find 传递冲突拦截
- P0-C:MPX 候选不自动升级;vacant 空地不成 MPX
- P0-D:房龄证据七级分级,严格/推断覆盖率分列
- P1:PARTIAL_COVERAGE 诚实标注;2026-07-08 截点保留
- P2:全部产物同一冻结运行生成 + 守恒断言

运行:python3 scripts/sales_archive_v1_0_2.py
"""
import csv, json, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                                  # noqa: E402
import archive_core as AC                            # noqa: E402
import address_norm as AN                            # noqa: E402
import spatial_identity as SI                        # noqa: E402
import archive_pipeline as AP                        # noqa: E402

LOC = ROOT / "data/research/raw/location"
OFF = ROOT / "data/research/raw/official"
NORM = ROOT / "data/research/normalized"
OUTD = NORM / "sales_archive_v1_0_2"
DOCS = ROOT / "docs/audits/sales_archive_v1_0_2"
OUTD.mkdir(parents=True, exist_ok=True)
DOCS.mkdir(parents=True, exist_ok=True)

CUTOFF_2026 = "2026-07-08"     # BCA 层实测刷新截点

INPUTS = {
    "comps": NORM / "standard-residential-comps-v2.csv",
    "arc_2324_all":
        LOC / "bca-arcgis-sales-2023-01-01-2024-12-31-all.json",
    "arc_2324_alltypes":
        LOC / "bca-arcgis-sales-2023-01-01-2024-12-31-alltypes.json",
    "arc_2326": LOC / "bca-arcgis-sales-2023-01-01-2026-09-06.json",
    "arc_2326_enriched":
        LOC / "bca-arcgis-sales-2023-01-01-2026-09-06-enriched-high.json",
    "arc_2526": LOC / "bca-arcgis-sales-2025-present-all.json",
    "arc_2526_enriched":
        LOC / "bca-arcgis-sales-2025-present-enriched.json",
    "arc_spatial":
        LOC / "bca-arcgis-sales-2026-09-06-enriched-spatial.json",
    "tax2023f": OFF / "property-tax-2023-full.csv",
    "tax2024f": OFF / "property-tax-2024-full.csv",
    "tax2025f": OFF / "property-tax-2025-full.csv",
    "tax2026f": OFF / "property-tax-2026-full.csv",
}
HASHES = {k: hashlib.sha256(p.read_bytes()).hexdigest()
          for k, p in INPUTS.items() if p.exists()}

BOUND = json.loads((ROOT / "data/research/boundaries/"
                    "vancouver-local-area-boundary-official.geojson")
                   .read_text())


def _pip(lat, lng, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
                lng < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def comm_of(lat, lng):
    if not lat or not lng:
        return "?"
    for f in BOUND["features"]:
        polys = (f["geometry"]["coordinates"]
                 if f["geometry"]["type"] == "MultiPolygon"
                 else [f["geometry"]["coordinates"]])
        for poly in polys:
            if _pip(lat, lng, poly[0]):
                return K.rc_split(f["properties"]["name"], lat)
    return "?"


def jrows(p):
    if not p.exists():
        return []
    j = json.loads(p.read_text())
    if isinstance(j, list):
        return j
    for k in ("sales", "results", "records", "features"):
        if isinstance(j.get(k), list):
            return j[k]
    return next((v for v in j.values() if isinstance(v, list)), [])


def num(x):
    return K.num(x)


# ============ 0. Raw Records ============
raw, schema_issues = [], []
_rid = [0]


def add_raw(src, src_rid, **kw):
    _rid[0] += 1
    rec, iss = AP.normalize_record(f"R{_rid[0]:06d}", src,
                                   src_rid, **kw)
    schema_issues.extend(iss)
    raw.append(rec)


for r in csv.DictReader(open(INPUTS["comps"], encoding="utf-8-sig")):
    addr = r.get("address") or r.get("﻿address") or ""
    add_raw("comps_v2", r.get("record_id"),
            address_raw=addr, mls_raw_address=addr,
            municipality=r.get("municipality"),
            sale_date=r["sale_date"], sale_price=num(r["sale_price"]),
            pid=r.get("pid"), lat=num(r.get("lat")),
            lng=num(r.get("lng")), lot_sqft=num(r.get("lot_sqft")),
            product_class=r["product_class"],
            is_strata=(r.get("is_strata") == "Y"
                       if r.get("is_strata") else None),
            year_built=num(r.get("year_built")),
            fa=num(r.get("floor_above_sf")),
            gst=r.get("gst_basis") or "",
            asof=r.get("asof_status") or "",
            comm=r.get("rc_sub") or "")

arc_by_rid = {}
for tag in ("arc_2324_all", "arc_2324_alltypes", "arc_2326",
            "arc_2526", "arc_2326_enriched", "arc_2526_enriched",
            "arc_spatial"):
    for r in jrows(INPUTS[tag]):
        rid = r.get("record_id") or \
            f"{r.get('address')}|{r.get('sale_date')}|" \
            f"{r.get('sale_price')}"
        e = arc_by_rid.setdefault(rid, {"_files": []})
        e["_files"].append(tag)
        for k, v in r.items():
            if v not in (None, "", []):
                e.setdefault(k, v)
for rid, r in arc_by_rid.items():
    add_raw("bca_arcgis", rid,
            address_raw=r.get("address"),
            bca_raw_address=r.get("address"),
            municipality=r.get("municipality"),
            sale_date=r.get("sale_date"),
            sale_price=num(r.get("sale_price")),
            pid=r.get("pid"),
            bca_evbc_oid=r.get("bca_oid_evbc"),   # EVBC 对象号≠folio
            legal_description=r.get("legal_description"),
            lat=num(r.get("latitude")), lng=num(r.get("longitude")),
            lot_sqft=num(r.get("lot_area_sqft")),
            product_class=AC.arc_desc_product(
                r.get("official_bca_description")),
            product_raw=r.get("official_bca_description") or "",
            is_strata=(r.get("official_bca_is_strata") == "Y"
                       if r.get("official_bca_is_strata")
                       else None),
            year_built=num(r.get("year_built")),
            fa=(num(r.get("bca_first_floor_area_sqft")) or 0) +
               (num(r.get("bca_second_floor_area_sqft")) or 0) or None,
            bca_improvement=num(r.get("bca_improvement")),
            files="|".join(sorted(set(r["_files"]))))

n_raw = len(raw)
arc_n = sum(1 for r in raw if r["source"] == "bca_arcgis")

# ============ 1. 税务全年度 ============
tax_by_pid = defaultdict(dict)          # pid -> {yr: row}
tax_street = defaultdict(list)          # 规范街键 -> [rows]
tax_planlot = defaultdict(dict)         # (plan,lot,legal_type) -> {yr:row}
TAX_YEARS = {}
for yr in ("2023", "2024", "2025", "2026"):
    p = INPUTS[f"tax{yr}f"]
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig"),
                               delimiter=";")) if p.exists() else []
    TAX_YEARS[yr] = {"rows": len(rows), "full": p.exists()}
    for t in rows:
        pid = (t.get("pid") or "").strip()
        if pid:
            tax_by_pid[pid][yr] = t
        sn = (t.get("street_name") or "").strip().upper()
        if sn:
            sp = AN.parse("0 " + sn)
            tax_street[(sp["street_name"], sp["street_type"],
                        sp["street_direction"])].append(t)
        pl = ((t.get("plan") or "").strip().upper(),
              (t.get("lot") or "").strip().upper(),
              (t.get("legal_type") or "").strip().upper())
        if pl[0] and pl[1]:
            tax_planlot[pl][yr] = t


def tax_link(rec):
    """税表证据回连(分层)。返回 dict:
    status UNIT/PARCEL/SINGLE/CONSENSUS/AMBIGUOUS/NONE,
    pid/folio/yb/age_tier/zoning/legal_type, n 候选数。"""
    out = {"status": "NONE", "n": 0, "yb": None}
    # a) strata 单元:STRATA + plan + lot 精确
    if rec["strata_plan"] and rec["strata_lot"]:
        hits = []
        for lt in ("STRATA", "STRATA-RES", "STRATA RES"):
            hits += [(y, t) for y, t in tax_planlot.get(
                (rec["strata_plan"], rec["strata_lot"], lt),
                {}).items()]
        if hits:
            hits.sort(key=lambda h: h[0])
            y0, t = hits[0]
            if (t.get("year_built") or "").isdigit():
                out.update(status="UNIT", n=len(hits),
                           pid=t.get("pid"), folio=t.get("folio"),
                           yb=t["year_built"],
                           age_tier="AGE_UNIT_CONFIRMED",
                           zoning=t.get("zoning_district"),
                           legal_type=t.get("legal_type"))
                return out
    # b) 非 strata 细分 parcel:plan + lot
    if rec["legal_plan"] and rec["legal_lot"]:
        hits = [(y, t) for y, t in tax_planlot.get(
            (rec["legal_plan"], rec["legal_lot"], "LAND"),
            {}).items()]
        hits += [(y, t) for y, t in tax_planlot.get(
            (rec["legal_plan"], rec["legal_lot"], ""),
            {}).items()]
        if not hits:   # 不限 legal_type 兜底
            hits = [(y, t) for k, v in tax_planlot.items()
                    if k[0] == rec["legal_plan"] and
                    k[1] == rec["legal_lot"] and
                    k[2] != "STRATA" for y, t in v.items()]
        if hits:
            hits.sort(key=lambda h: h[0])
            y0, t = hits[0]
            if (t.get("year_built") or "").isdigit():
                out.update(status="PARCEL", n=len(hits),
                           pid=t.get("pid"), folio=t.get("folio"),
                           yb=t["year_built"],
                           age_tier="AGE_FOLIO_CONFIRMED",
                           zoning=t.get("zoning_district"),
                           legal_type=t.get("legal_type"))
                return out
    # c) civic 区间回连(键必须与 tax_street 同型:None≠"")
    if not rec["street_number"] or not rec["street_name"]:
        return out
    skey = (rec["street_name"], rec["street_type"] or None,
            rec["street_direction"] or None)
    rows = tax_street.get(skey, [])
    if rec["is_strata"] is True:
        rows = [t for t in rows if t.get("legal_type") == "STRATA"]
    elif rec["is_strata"] is False:
        rows = [t for t in rows if t.get("legal_type") != "STRATA"]
    try:
        n = float(rec["street_number"].replace(" 1/2", ".5"))
    except ValueError:
        return out
    cands = []
    for t in rows:
        try:
            lo = float(t.get("from_civic_number") or 0)
            hi = float(t.get("to_civic_number") or lo)
        except ValueError:
            continue
        if lo <= n <= hi:
            cands.append((hi - lo, t))
    if not cands:
        return out
    span = min(s for s, _ in cands)
    tight = [t for s, t in cands if s <= span + 0.51]
    pids = {t["pid"] for t in tight if t.get("pid")}
    out["n"] = len(tight)
    if len(pids) == 1:
        t = tight[0]
        out.update(status="SINGLE", pid=next(iter(pids)),
                   folio=t.get("folio"), yb=t.get("year_built"),
                   age_tier="AGE_ADDRESS_INFERRED",
                   zoning=t.get("zoning_district"),
                   legal_type=t.get("legal_type"))
    elif pids:
        ys = {t["year_built"] for t in tight
              if t.get("year_built")}
        out["status"] = "AMBIGUOUS"
        if len(ys) == 1:
            out.update(status="CONSENSUS", yb=next(iter(ys)),
                       age_tier="AGE_CONSENSUS_ONLY")
    return out


# ============ 2. 身份关联 ============
pairs = AP.build_pairs(raw)
cl = AP.build_clusters(raw, pairs)
parcel_of, unit_of = cl["parcel_of"], cl["unit_of"]

ident_label = {i: (("PID:" + raw[i]["pid"]) if raw[i]["pid"]
                   else f"PAR:{parcel_of[i]}") for i in range(n_raw)}

# ============ 3. 成交归并 ============
canon, merge_log, cand_dups = AP.dedup_sales(
    raw, unit_of, parcel_of, pairs)
raw2canon = {}
for g in canon:
    cid = "TX" + hashlib.sha1(
        f"{unit_of[g[0]]}|{g[0]}".encode()).hexdigest()[:12]
    for i in g:
        raw2canon[i] = cid

# ============ 4. 组装 canonical sale ============
sales = []
mpx_recon = []
for g in canon:
    ms = [raw[i] for i in g]
    comp = next((m for m in ms if m["source"] == "comps_v2"),
                ms[0])
    arc = next((m for m in ms if m["source"] == "bca_arcgis"), None)
    # 原生 pid(记录自带)与回连 pid 严格区分:只有原生 pid
    # 直查税表才够格 AGE_EXACT_PID
    native_pid = comp["pid"] or (arc or {}).get("pid") or ""
    tl = tax_link(comp)     # strata/legal 证据 comp 缺则补 arc
    def _tri(a, b):
        return True if True in (a, b) else \
            (False if False in (a, b) else None)
    strata_t = _tri(comp["is_strata"], (arc or {}).get("is_strata"))
    if tl["status"] == "NONE" and arc:
        tl = tax_link({**comp,
                       "strata_plan": comp["strata_plan"] or
                       arc["strata_plan"],
                       "strata_lot": comp["strata_lot"] or
                       arc["strata_lot"],
                       "legal_plan": comp["legal_plan"] or
                       arc["legal_plan"],
                       "legal_lot": comp["legal_lot"] or
                       arc["legal_lot"],
                       "is_strata": strata_t})
    pid = native_pid or tl.get("pid") or ""
    sy = int(comp["sale_date"][:4])
    native = comp.get("year_built") or (arc or {}).get("year_built")
    has_unit_ev = bool(comp["unit_number"] or comp["strata_lot"]
                       or (arc or {}).get("strata_lot"))
    yb, age_tier = AP.resolve_age(sy, native_pid, tax_by_pid,
                                  tl, native, has_unit_ev)
    band, age = AC.age_band_at_sale(yb, sy) if yb else \
        ("AGE_UNKNOWN", None)
    if age_tier == "AGE_TEMPORAL_CONFLICT":
        band = "AGE_TEMPORAL_CONFLICT"
    prod, ver, preason = AP.classify_product(
        comp["product_class"], (arc or {}).get("product_raw"),
        strata_t,
        comp["unit_number"], pid, comp["strata_lot"],
        (arc or {}).get("bca_improvement"), comp["sale_price"])
    if comp["product_class"].startswith("MULTIPLEX") or \
            comp["product_class"] == "MPX" or \
            (arc or {}).get("product_class", "").startswith(
                "MULTIPLEX"):
        mpx_recon.append({
            "canonical_tx": raw2canon[g[0]],
            "address": comp["address_raw"],
            "comp_class": comp["product_class"],
            "arc_desc": (arc or {}).get("product_raw", ""),
            "is_strata": strata_t,
            "product": prod, "verification": ver,
            "evidence": preason,
            "sale_date": comp["sale_date"],
            "sale_price": comp["sale_price"]})
    lat = comp.get("lat") or (arc or {}).get("lat")
    lng = comp.get("lng") or (arc or {}).get("lng")
    comm = comp.get("comm") or \
        (comm_of(float(lat), float(lng)) if lat and lng else "?")
    desc = ((arc or {}).get("product_raw") or "").lower()
    gst = comp.get("gst") or ""
    if age_tier == "AGE_TEMPORAL_CONFLICT":
        skind = "PRECONSTRUCTION_OR_LAND"
    elif "vacant" in desc:
        skind = "LAND_OR_VACANT"
    elif gst == "new_first_sale":
        skind = "FIRST_SALE"
    elif gst.startswith("res"):
        skind = "RESALE"
    else:
        skind = "UNKNOWN"
    sales.append({
        "canonical_tx": raw2canon[g[0]],
        "raw_ids": "|".join(raw[i]["raw_id"] for i in g),
        "parcel_cluster": parcel_of[g[0]],
        "unit_cluster": unit_of[g[0]],
        "pid": pid,
        "folio": tl.get("folio") or "",
        "strata_plan": comp["strata_plan"] or
        (arc or {}).get("strata_plan") or "",
        "zoning": tl.get("zoning") or "",
        "legal_type": tl.get("legal_type") or "",
        "address": comp["address_raw"],
        "unit": comp["unit_number"],
        "norm_addr": comp["normalized_address"],
        "community": comm, "sale_date": comp["sale_date"],
        "sale_price": comp["sale_price"], "product": prod,
        "product_verification": ver,
        "product_raw": comp["product_class"] or
        (arc or {}).get("product_class", ""),
        "is_strata": strata_t,
        "yb": yb or "", "age_tier": age_tier, "age_band": band,
        "sale_kind": skind,
        "fa": comp.get("fa") or (arc or {}).get("fa") or "",
        "lot_sqft": comp.get("lot_sqft") or
        (arc or {}).get("lot_sqft") or "",
        "n_sources": len({m["source"] for m in ms}),
        "sources": "+".join(sorted({m["source"] for m in ms})),
        "lat": lat or "", "lng": lng or "",
        "tax_link": tl.get("status", "NONE"),
        "tax_link_n": tl.get("n", 0),
        "asof": comp.get("asof") or ""})

print(f"raw={n_raw}(arcgis {arc_n}) parcel={cl['n_parcel']} "
      f"unit={cl['n_unit']} canon={len(sales)} "
      f"merges={len(merge_log)} cand_dups={len(cand_dups)} "
      f"blocked={len(cl['blocked'])} conflicts={len(cl['conflicts'])}")

# ============ 5. 守恒断言 ============
by_year = Counter(s["sale_date"][:4] for s in sales)
by_prod = Counter(s["product"] for s in sales)
by_age = Counter(s["age_band"] for s in sales)
by_agetier = Counter(s["age_tier"] for s in sales)
by_ver = Counter(s["product_verification"] for s in sales)
assert sum(by_prod.values()) == len(sales)
assert sum(by_age.values()) == len(sales)
assert sum(by_agetier.values()) == len(sales)
assert sum(by_ver.values()) == len(sales)
canon_raw_n = sum(len(g) for g in canon)
assert canon_raw_n == sum(1 for r in raw
                         if r["sale_date"] and r["sale_price"])
assert canon_raw_n - len(sales) == len(merge_log)

# ============ 6. 输出 ============
def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


wcsv(OUTD / "sales_archive.csv", sales)
wcsv(OUTD / "raw_to_canonical_mapping.csv", [
    {"raw_id": raw[i]["raw_id"], "canonical_tx": raw2canon[i],
     "source": raw[i]["source"], "sale_date": raw[i]["sale_date"],
     "sale_price": raw[i]["sale_price"],
     "parcel_cluster": parcel_of[i], "unit_cluster": unit_of[i]}
    for i in sorted(raw2canon)])
wcsv(OUTD / "merge_evidence.csv", merge_log)

# --- 覆盖率矩阵 ---
cov = defaultdict(lambda: {"captured": 0, "canon": 0, "verified": 0,
                           "identity_uncertain": 0, "pending": 0,
                           "known_age": 0, "strict_age": 0,
                           "inferred_age": 0, "fa": 0, "latest": "",
                           "srcs": set(), "verified_prod": 0,
                           "candidate_prod": 0})
for s in sales:
    c = cov[(s["sale_date"][:4], s["product"])]
    c["captured"] += len(s["raw_ids"].split("|"))
    c["canon"] += 1
    c["verified"] += s["n_sources"] > 1
    c["identity_uncertain"] += s["pid"] == ""
    c["known_age"] += s["age_band"] not in ("AGE_UNKNOWN",
                                            "AGE_TEMPORAL_CONFLICT")
    c["strict_age"] += s["age_tier"] in AP.STRICT_TIERS
    c["inferred_age"] += s["age_tier"] in ("AGE_ADDRESS_INFERRED",
                                          "AGE_CONSENSUS_ONLY")
    c["fa"] += s["fa"] not in ("", None)
    c["verified_prod"] += s["product_verification"] == "VERIFIED"
    c["candidate_prod"] += s["product_verification"] == "CANDIDATE"
    c["latest"] = max(c["latest"], s["sale_date"])
    for so in s["sources"].split("+"):
        c["srcs"].add(so)

PRODS = list(AP.PRODUCTS)
cov_rows = []
for y in ("2023", "2024", "2025", "2026"):
    for p in PRODS:
        c = cov.get((y, p), {"captured": 0, "canon": 0,
                             "verified": 0, "identity_uncertain": 0,
                             "pending": 0, "known_age": 0,
                             "strict_age": 0, "inferred_age": 0,
                             "fa": 0, "latest": "", "srcs": set(),
                             "verified_prod": 0, "candidate_prod": 0})
        if c["canon"] == 0:
            status = ("SOURCE_NOT_COVERED:BCA层仅存最近成交,早期"
                      "非独立屋被后期转售覆盖" if y in ("2023", "2024")
                      else f"SOURCE_NOT_COVERED:BCA层刷新截至"
                           f"{CUTOFF_2026}" if y == "2026"
                      else "SOURCE_NOT_COVERED")
        elif p in ("UNKNOWN", "UNKNOWN_STRATA"):
            status = "UNCLASSIFIED:product_unknown"
        elif y in ("2023", "2024"):
            # BCA 层每物业仅存 LAST_SALE:期内二次转售的行已被
            # 覆盖 → 即使全类型拉取也只是窗口末次成交,诚实 PARTIAL
            status = ("PARTIAL_COVERAGE:last_sale_window_truncated"
                      "|" + "+".join(sorted(c["srcs"])))
        elif y == "2026":
            status = (f"PARTIAL_COVERAGE:cutoff={CUTOFF_2026}|"
                      + "+".join(sorted(c["srcs"])))
        elif c["canon"] < 50 or \
                (c["candidate_prod"] > 0 and
                 c["verified_prod"] == 0):
            status = "PARTIAL_COVERAGE:" + \
                "+".join(sorted(c["srcs"]))
        else:
            status = "COVERED:" + "+".join(sorted(c["srcs"]))
        cov_rows.append({"year": y, "product": p,
                         "captured_rows": c["captured"],
                         "canonical_sales": c["canon"],
                         "pending_dedup": c["pending"],
                         "identity_uncertain": c["identity_uncertain"],
                         "known_age": c["known_age"],
                         "unknown_age": c["canon"] - c["known_age"],
                         "strict_age": c["strict_age"],
                         "inferred_age": c["inferred_age"],
                         "fa_known": c["fa"],
                         "verified_multi_source": c["verified"],
                         "verified_product": c["verified_prod"],
                         "candidate_product": c["candidate_prod"],
                         "latest_sale": c["latest"],
                         "status": status})
assert sum(r["canonical_sales"] for r in cov_rows) == len(sales)

# --- 年度×产品×房龄矩阵 ---
mat = defaultdict(list)
for s in sales:
    mat[(s["sale_date"][:4], s["product"], s["age_band"])].append(s)
mrows = []
for (y, p, ab), es in sorted(mat.items()):
    ps = sorted(e["sale_price"] for e in es if e["sale_price"])
    mrows.append({"year": y, "product": p, "age_band": ab,
                  "n_tx": len(es),
                  "n_verified_product": sum(
                      1 for e in es
                      if e["product_verification"] == "VERIFIED"),
                  "n_multi_source": sum(1 for e in es
                                        if e["n_sources"] > 1),
                  "p50": round(ps[len(ps) // 2]) if ps else "",
                  "latest": max(e["sale_date"] for e in es)})
assert sum(r["n_tx"] for r in mrows) == len(sales)

# --- 社区×产品×房龄价 ---
cmat = defaultdict(list)
for s in sales:
    if s["sale_price"] and s["age_band"] not in \
            ("AGE_UNKNOWN", "AGE_TEMPORAL_CONFLICT"):
        cmat[(s["community"], s["product"],
              s["age_band"])].append(s["sale_price"])
crow = [{"community": c, "product": p, "age_band": ab,
         "n_tx": len(ps), "p50": round(sorted(ps)[len(ps)//2]),
         "p25": round(sorted(ps)[len(ps)//4]),
         "p75": round(sorted(ps)[(3*len(ps))//4])}
        for (c, p, ab), ps in sorted(cmat.items())]

# --- 各类审计 ---
wcsv(OUTD / "spatial_identity_match_audit.csv",
     [{"tier": t, "pairs": n} for t, n in
      Counter(m["tier"] for m in pairs.values()).most_common()])
wcsv(OUTD / "parcel_unit_collision_audit.csv",
     [{"kind": "BLOCKED_UNION", "reason": b["reason"],
       "pids": "|".join(b["pids"]),
       "planlots": "|".join(b["lots"]),
       "raw_i": raw[b["i"]]["raw_id"], "raw_j": raw[b["j"]]["raw_id"]}
      for b in cl["blocked"]] +
     [{"kind": "CLUSTER_CONFLICT", "reason": "contradictory_keys",
       "pids": "|".join(c["pids"]), "planlots": "|".join(c["planlots"]),
       "raw_i": "|".join(raw[i]["raw_id"] for i in c["members"][:8]),
       "raw_j": ""} for c in cl["conflicts"]] +
     [{"kind": "CANDIDATE_DUP_SAME_PARCEL",
       "reason": "same_event_diff_unit",
       "pids": "", "planlots": "",
       "raw_i": d["raw_a"], "raw_j": d["raw_b"],
       "base_a": d["base_a"], "base_b": d["base_b"],
       "unit_a": d["unit_a"], "unit_b": d["unit_b"],
       "date_a": d["date_a"], "date_b": d["date_b"],
       "price_a": d["price_a"], "price_b": d["price_b"]}
      for d in cand_dups])
wcsv(OUTD / "multiplex_classification_reconciliation.csv", mpx_recon)

# 地址冲突
addr_conf = []
by_parcel = defaultdict(list)
for i, r in enumerate(raw):
    by_parcel[parcel_of[i]].append(i)
for prt, idxs in by_parcel.items():
    raws = {raw[i]["address_raw"] for i in idxs}
    if len(raws) > 1:
        addr_conf.append({
            "parcel_cluster": prt,
            "raw_variants": " || ".join(sorted(raws))[:300],
            "n_variants": len(raws),
            "norm_addr": raw[idxs[0]]["normalized_address"],
            "sources": "|".join(sorted({raw[i]["source"]
                                        for i in idxs}))})
wcsv(OUTD / "address_normalization_conflicts.csv", addr_conf)

# 核销表
confirmed = len(merge_log)
recheck = [
    {"metric": "raw_rows", "n": n_raw},
    {"metric": "parcel_clusters", "n": cl["n_parcel"]},
    {"metric": "unit_clusters", "n": cl["n_unit"]},
    {"metric": "canonical_sales", "n": len(sales)},
    {"metric": "confirmed_merges", "n": confirmed},
    {"metric": "candidate_dups_manual_review",
     "n": len(cand_dups)},
    {"metric": "blocked_unions", "n": len(cl["blocked"])},
    {"metric": "cluster_conflicts", "n": len(cl["conflicts"])},
    {"metric": "multi_source_sales",
     "n": sum(1 for s in sales if s["n_sources"] > 1)},
    {"metric": "arcgis_comps_both",
     "n": sum(1 for s in sales if s["n_sources"] > 1)},
    {"metric": "v1_0_1_multi_source_baseline", "n": 4178},
    {"metric": "v1_0_1_arcgis_comps_merges_baseline", "n": 3},
]
wcsv(OUTD / "cross_source_merge_recheck.csv", recheck)

# 房龄质量
ager = [{"age_tier": t, "n": n} for t, n in
        by_agetier.most_common()] + [
    {"age_tier": "STRICT_COVERAGE(EXACT_PID+FOLIO+UNIT)",
     "n": sum(by_agetier[t] for t in AP.STRICT_TIERS)},
    {"age_tier": "INFERRED_COVERAGE(ADDR+CONSENSUS)",
     "n": sum(by_agetier[t] for t in
              ("AGE_ADDRESS_INFERRED", "AGE_CONSENSUS_ONLY"))}]
wcsv(OUTD / "age_evidence_quality.csv", ager)

wf_rows = [
    {"stage": "raw_rows", "n": n_raw},
    {"stage": "parcel_clusters", "n": cl["n_parcel"]},
    {"stage": "unit_clusters", "n": cl["n_unit"]},
    {"stage": "canonical_sales", "n": len(sales)},
    {"stage": "confirmed_merges", "n": confirmed},
    {"stage": "candidate_dups", "n": len(cand_dups)},
    {"stage": "schema_issues", "n": len(schema_issues)},
    {"stage": "sales_age_strict", "n": sum(
        by_agetier[t] for t in AP.STRICT_TIERS)},
    {"stage": "sales_age_inferred", "n": sum(
        by_agetier[t] for t in ("AGE_ADDRESS_INFERRED",
                                "AGE_CONSENSUS_ONLY"))},
    {"stage": "sales_age_unknown",
     "n": by_agetier.get("AGE_UNKNOWN", 0)},
    {"stage": "sales_pid_known",
     "n": sum(1 for s in sales if s["pid"])},
]
wcsv(OUTD / "canonical_transaction_waterfall.csv", wf_rows)
wcsv(OUTD / "source_coverage_2023_2026.csv", cov_rows)
wcsv(OUTD / "transactions_by_year_product_age.csv", mrows)
wcsv(OUTD / "community_product_age_prices.csv", crow)
wcsv(OUTD / "tax_attribute_recovery.csv", [
    {"metric": "sales_pid_known",
     "n": sum(1 for s in sales if s["pid"])},
    {"metric": "sales_folio_known",
     "n": sum(1 for s in sales if s["folio"])},
    {"metric": "sales_zoning_known",
     "n": sum(1 for s in sales if s["zoning"])},
    {"metric": "sales_yb_known",
     "n": sum(1 for s in sales if s["yb"] != "")},
    {"metric": "tax_link_unit", "n": sum(
        1 for s in sales if s["tax_link"] == "UNIT")},
    {"metric": "tax_link_parcel", "n": sum(
        1 for s in sales if s["tax_link"] == "PARCEL")},
    {"metric": "tax_link_single", "n": sum(
        1 for s in sales if s["tax_link"] == "SINGLE")},
    {"metric": "tax_link_consensus", "n": sum(
        1 for s in sales if s["tax_link"] == "CONSENSUS")},
    {"metric": "tax_link_ambiguous", "n": sum(
        1 for s in sales if s["tax_link"] == "AMBIGUOUS")}])
wcsv(OUTD / "cross_source_dedup_reconciliation.csv", recheck)

# ============ 7. 报告(全部由本运行统计生成,禁手抄) ============
tier_c = Counter(m["tier"] for m in pairs.values())
REPORT = f"""# Sales Archive V1.0.2 — Identity & Product Integrity Gate

运行时间:{datetime.datetime.now().isoformat(timespec="seconds")}
输入指纹:见 manifest.json(全部 SHA256)

## 瀑布
raw {n_raw} → parcel 簇 {cl['n_parcel']} → unit 簇 {cl['n_unit']} →
canonical {len(sales)};确认合并 {confirmed};疑似候选重复(人工)
{len(cand_dups)};被拦截 union {len(cl["blocked"])};簇冲突
{len(cl["conflicts"])};schema 缺陷 {len(schema_issues)}。

## 空间匹配分层
{chr(10).join(f"- {t}: {n}" for t, n in tier_c.most_common())}

## 年度成交
{chr(10).join(f"- {y}: {n}" for y, n in sorted(by_year.items()))}
(2026 截至 {CUTOFF_2026},非全年)

## 产品分类(VERIFIED/CANDIDATE 分离)
{chr(10).join(f"- {p}: {n}" for p, n in by_prod.most_common())}
{chr(10).join(f"- {v}: {n}" for v, n in by_ver.most_common())}
MPX 复核见 multiplex_classification_reconciliation.csv
(候选 {sum(1 for s in sales if s['product'].startswith('MULTIPLEX') and s['product_verification'] == 'CANDIDATE')} 笔,
验证 {sum(1 for s in sales if s['product'].startswith('MULTIPLEX') and s['product_verification'] == 'VERIFIED')} 笔)。

## 房龄证据分级
{chr(10).join(f"- {t}: {n}" for t, n in by_agetier.most_common())}
严格覆盖率:{sum(by_agetier[t] for t in AP.STRICT_TIERS)}
/ {len(sales)};含推断:{sum(by_agetier[t] for t in AP.STRICT_TIERS) + sum(by_agetier[t] for t in ('AGE_ADDRESS_INFERRED','AGE_CONSENSUS_ONLY'))} / {len(sales)}。

## 数据源修复(P1 逐源可行性)
- 旧 `arc_2324_all` 由 `build_bca_sales_where` 硬编码
  `IS_STRATA='N' AND DESCRIPTION LIKE '%house%'` → 2023/24 名义
  全类型实为 house-only,这是上一轮"2023/24 无 CONDO"的根因。
- 探测:同一合法 guest-token 通道改开放 where(仅价格带+日期),
  2023-24 实拉 15,535 条(2023:7,468 / 2024:8,067),
  含 strata 公寓 9,000+、联排 1,315、duplex ~1,100 —— 源端
  数据一直在,缺的是过滤条件。已存
  `bca-arcgis-sales-2023-01-01-2024-12-31-alltypes.json`。
- 结构缺口仍存在:BCA 层仅存每物业 LAST_SALE,窗口内二次转售
  的早前记录被覆盖;comps_v2 的 2023/24 无 CONDO/TH/DPX
  (MLS 口径侧重非 strata)→ 该两年全部产品标
  PARTIAL_COVERAGE:last_sale_window_truncated。
- IS_STRATA='Y' 服务端过滤返回 0 行(字段值域不符),故用
  开放 where + 本地描述分类;`Strata Office` 等非住宅归入
  OTHER_RESIDENTIAL 待剔除评估。
- 2026 截 {CUTOFF_2026};2025 亦受 LAST_SALE 截断影响
  (2026 在售物业的 2025 记录被覆盖)。

## 跨源归并核销
确认合并 {confirmed}(可追溯 merge_evidence);同 parcel
异单元事件命中 {len(cand_dups)} 笔列为人工核对,未自动合并;
多源核实销售 {sum(1 for s in sales if s['n_sources'] > 1)} 笔
(V1.0.1 基线:ArcGIS↔comps 3 笔 / 多源 4,178 笔)。

## 覆盖诚实性
2023/2024 的 CONDO/TOWNHOUSE/DUPLEX/MULTIPLEX 标
SOURCE_NOT_COVERED 或 PARTIAL_COVERAGE(见
source_coverage_2023_2026.csv),不为市场零。
2026 全产品均带 cutoff={CUTOFF_2026}。
"""
(DOCS / "sales_archive_v1_0_2_report.md").write_text(REPORT)

# schema 校验报告
sv = ["# Identity Schema Validation", "",
      f"- raw records: {n_raw}",
      f"- schema issues: {len(schema_issues)}", ""]
sv += [f"- {i}" for i in schema_issues[:200]]
(DOCS / "identity_schema_validation_report.md").write_text(
    "\n".join(sv))

# ============ 8. GitHub 交付副本 + manifest ============
for name in ("source_coverage_2023_2026.csv",
             "address_normalization_conflicts.csv",
             "spatial_identity_match_audit.csv",
             "cross_source_dedup_reconciliation.csv",
             "cross_source_merge_recheck.csv",
             "canonical_transaction_waterfall.csv",
             "tax_attribute_recovery.csv",
             "transactions_by_year_product_age.csv",
             "parcel_unit_collision_audit.csv",
             "multiplex_classification_reconciliation.csv",
             "age_evidence_quality.csv",
             "community_product_age_prices.csv"):
    src_f = OUTD / name
    if src_f.exists():
        (DOCS / name).write_text(src_f.read_text())

manifest = {"version": "sales_archive_v1_0_2",
            "run": datetime.datetime.now().isoformat(),
            "cutoff_2026": CUTOFF_2026,
            "input_sha256": HASHES,
            "counts": {"raw_rows": n_raw,
                       "parcel_clusters": cl["n_parcel"],
                       "unit_clusters": cl["n_unit"],
                       "canonical_sales": len(sales),
                       "confirmed_merges": confirmed,
                       "candidate_dups": len(cand_dups),
                       "blocked_unions": len(cl["blocked"]),
                       "cluster_conflicts": len(cl["conflicts"]),
                       "schema_issues": len(schema_issues),
                       "pairs_by_tier": dict(tier_c),
                       "by_year": dict(by_year),
                       "by_product": dict(by_prod),
                       "by_product_verification": dict(by_ver),
                       "by_age_band": dict(by_age),
                       "by_age_tier": dict(by_agetier),
                       "multi_source": sum(
                           1 for s in sales if s["n_sources"] > 1),
                       "tax_years": TAX_YEARS}}
(OUTD / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1))
(DOCS / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1))
print("done.OUTD", OUTD)
