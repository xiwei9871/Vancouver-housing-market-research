#!/usr/bin/env python3
"""Sales Archive V1.0.1 — 数据源补齐 + 空间身份去重。

Raw Records → Property Identity → Unit Identity →
Transaction Candidates → Canonical Sales

原则:
- 原始地址完整保留(bca_raw/mls_raw + 全部组件)
- 坐标只发现候选;pid/folio/plan/单元确认身份
- 一次经济成交只计一次;合并留可逆映射
- 缺源标 SOURCE_NOT_COVERED,不填零、不把税务记录当成交
- V3s 冻结产物不动
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

LOC = ROOT / "data/research/raw/location"
OFF = ROOT / "data/research/raw/official"
NORM = ROOT / "data/research/normalized"
OUTD = NORM / "sales_archive_v1_0_1"
DOCS = ROOT / "docs/audits/sales_archive_v1_0_1"
OUTD.mkdir(parents=True, exist_ok=True)
DOCS.mkdir(parents=True, exist_ok=True)

INPUTS = {
    "comps": NORM / "standard-residential-comps-v2.csv",
    "arc_2324_all":
        LOC / "bca-arcgis-sales-2023-01-01-2024-12-31-all.json",
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
    "tax2023sub": OFF / "property-tax-2023-yb2018plus.csv",
    "tax2026sub": OFF / "property-tax-2026-yb2018plus.csv",
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
raw = []
_rid = [0]


def add_raw(src, src_rid, **kw):
    _rid[0] += 1
    kw.update({"raw_id": f"R{_rid[0]:06d}", "source": src,
                   "source_record_id": src_rid or ""})
    p = AN.parse(kw.get("address_raw"))
    kw.update({f"addr_{k}": v for k, v in p.items()})
    kw["norm_addr"] = p["normalized_address"]
    kw["base_addr"] = p["base_address"]
    raw.append(kw)
    return kw[-1] if False else kw


for r in csv.DictReader(open(INPUTS["comps"], encoding="utf-8-sig")):
    add_raw("comps_v2", r.get("record_id"),
            address_raw=r["address"],
            mls_raw_address=r["address"],
            sale_date=r["sale_date"], sale_price=num(r["sale_price"]),
            pid=(r.get("pid") or "").strip(), folio="",
            land_coordinate="", strata_plan="", strata_lot="",
            lat=num(r.get("lat")), lng=num(r.get("lng")),
            lot_sqft=num(r.get("lot_sqft")),
            product_class=r["product_class"],
            is_strata=r.get("is_strata") == "Y",
            year_built=num(r.get("year_built")),
            fa=num(r.get("floor_above_sf")),
            gst=r.get("gst_basis") or "",
            asof=r.get("asof_status") or "",
            comm=r.get("rc_sub") or "")

# arcgis:record_id 归并多文件同记录,enriched 字段优先回填
arc_by_rid = {}
for tag in ("arc_2324_all", "arc_2326", "arc_2526",
            "arc_2326_enriched", "arc_2526_enriched", "arc_spatial"):
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
    plan, slot = AN.strata_plan_from_legal(r.get("legal_description"))
    add_raw("bca_arcgis", rid,
            address_raw=r.get("address"),
            bca_raw_address=r.get("address"),
            sale_date=r.get("sale_date"),
            sale_price=num(r.get("sale_price")),
            pid=(r.get("pid") or "").strip(),
            folio=(r.get("bca_oid_evbc") or "").strip(),
            land_coordinate="", strata_plan=plan or "",
            strata_lot=slot or "",
            lat=num(r.get("latitude")), lng=num(r.get("longitude")),
            lot_sqft=num(r.get("lot_area_sqft")),
            product_class=AC.arc_desc_product(
                r.get("official_bca_description")),
            product_raw=r.get("official_bca_description") or "",
            is_strata=r.get("official_bca_is_strata") == "Y",
            year_built=num(r.get("year_built")),
            fa=(num(r.get("bca_first_floor_area_sqft")) or 0) +
               (num(r.get("bca_second_floor_area_sqft")) or 0) or None,
            gst="", asof="", comm="",
            bca_improvement=num(r.get("bca_improvement")),
            files="|".join(sorted(set(r["_files"]))))

n_raw = len(raw)
arc_n = sum(1 for r in raw if r["source"] == "bca_arcgis")

# ============ 1. 税务全年度(若已下载) ============
tax = defaultdict(dict)      # pid -> {year: row}
tax_street = defaultdict(list)   # street_name(原始) -> [rows]
TAX_YEARS = {}
for yr in ("2023", "2024", "2025", "2026"):
    p = INPUTS[f"tax{yr}f"]
    rows = []
    if p.exists() and p.stat().st_size > 1000:
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig"),
                                   delimiter=";"))
    else:
        sub = INPUTS.get(f"tax{yr}sub")
        if yr in ("2023", "2026") and sub and sub.exists():
            rows = list(csv.DictReader(open(sub, encoding="utf-8-sig"),
                                       delimiter=";"))
    TAX_YEARS[yr] = {"rows": len(rows),
                     "full": bool(p.exists() and
                                  p.stat().st_size > 1000)}
    for t in rows:
        if t.get("pid"):
            tax[t["pid"]][yr] = t
        sn = (t.get("street_name") or "").strip().upper()
        if sn:
            sp = AN.parse("0 " + sn)
            skey = (sp["street_name"], sp["street_type"],
                    sp["street_direction"])
            tax_street[skey].append(t)


def tax_candidates(addr, unit=None, is_strata=None):
    """税表回连:规范化街名+类型+方向 精确键 + civic 区间包含,
    取最紧区间;legal_type 可作 strata/非 strata 过滤。
    一对多全部保留,由调用方判歧义。"""
    p = AN.parse(addr)
    if not p["street_name"] or not p["street_number"]:
        return []
    skey = (p["street_name"], p["street_type"], p["street_direction"])
    rows = tax_street.get(skey, [])
    if is_strata is True:
        rows = [t for t in rows if t.get("legal_type") == "STRATA"]
    elif is_strata is False:
        rows = [t for t in rows if t.get("legal_type") != "STRATA"]
    cands = []
    try:
        n = float(p["street_number"].replace(" 1/2", ".5"))
    except ValueError:
        return []
    for t in rows:
        try:
            lo = float(t.get("from_civic_number") or 0)
            hi = float(t.get("to_civic_number") or lo)
        except ValueError:
            continue
        if lo <= n <= hi:
            cands.append((hi - lo, t))
    if not cands:
        return []
    span_min = min(s for s, _ in cands)
    return [t for s, t in cands if s <= span_min + 0.51]

# ============ 2. 身份关联 ============
# 2a. 先按强证据键聚类
clusters = []           # list of member lists
pid_map = defaultdict(list)
folio_map = defaultdict(list)
planlot_map = defaultdict(list)
addr_map = defaultdict(list)
for i, r in enumerate(raw):
    if r["pid"]:
        pid_map[r["pid"]].append(i)
    if r["folio"]:
        folio_map[r["folio"]].append(i)
    if r["strata_plan"] and r["strata_lot"]:
        planlot_map[(r["strata_plan"], r["strata_lot"])].append(i)
    if r["base_addr"]:
        addr_map[r["base_addr"]].append(i)

# 2b. 空间网格候选 → match() 判定
grid = defaultdict(list)
for i, r in enumerate(raw):
    k = SI.grid_key(r.get("lat"), r.get("lng"))
    if k:
        grid[k].append(i)

pairs = {}
for i, r in enumerate(raw):
    if r.get("lat") is None:
        cand = list(addr_map.get(r["base_addr"], []))
    else:
        cand = []
        for c in SI.neighbor_cells(r["lat"], r["lng"]):
            cand += grid.get(c, [])
        cand += list(addr_map.get(r["base_addr"], []))
    for j in set(cand):
        if j <= i:
            continue
        m = SI.match(raw[i], raw[j])
        if m["tier"] != "UNMATCHED":
            pairs[(i, j)] = m

# 2c. union-find 建身份簇(EXACT_*/CORROBORATED/ADDRESS_ONLY 可聚合;
#     SPATIAL_CANDIDATE_ONLY 只作候选证据不自动合并)
parent = list(range(n_raw))


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


MERGE_TIERS = ("EXACT_PID_UNIT", "EXACT_PARCEL",
               "SPATIAL_ADDRESS_CORROBORATED", "ADDRESS_ONLY")
for (i, j), m in pairs.items():
    if m["tier"] in MERGE_TIERS:
        union(i, j)

clus = defaultdict(list)
for i in range(n_raw):
    clus[find(i)].append(i)
identity_of = {}       # raw index -> identity_id
ident_n = 0
for members in clus.values():
    ident_n += 1
    pids = {raw[i]["pid"] for i in members if raw[i]["pid"]}
    folios = {raw[i]["folio"] for i in members if raw[i]["folio"]}
    iid = ("PID:" + sorted(pids)[0]) if pids else \
        ("FOL:" + sorted(folios)[0]) if folios else \
        "ADDR:" + (raw[members[0]]["base_addr"] or f"#{ident_n}")
    for i in members:
        identity_of[i] = iid

# ============ 3. 单元身份 + 成交归并 ============
def unit_id(i):
    r = raw[i]
    u = r.get("addr_unit_number") or r.get("unit_number") or ""
    if r["pid"]:
        return f"{identity_of[i]}|U:pid:{r['pid']}"
    if r["strata_plan"] and r["strata_lot"]:
        return f"{identity_of[i]}|U:plan:{r['strata_plan']}/" \
               f"{r['strata_lot']}"
    return f"{identity_of[i]}|U:{u or '?'}"


# canonical tx:同一 unit_id + 日期窗 + 价差内 → 一笔
by_unit = defaultdict(list)
for i, r in enumerate(raw):
    if r.get("sale_date") and r.get("sale_price"):
        by_unit[unit_id(i)].append(i)

canon = []            # canonical sales
raw2canon = {}
merge_log = []
for uid, idxs in by_unit.items():
    idxs.sort(key=lambda i: (raw[i]["sale_date"],
                             raw[i]["sale_price"]))
    open_groups = []
    for i in idxs:
        r = raw[i]
        placed = False
        for g in open_groups:
            rep = raw[g[0]]
            tier = pairs.get((min(i, g[0]), max(i, g[0])),
                             {}).get("tier") or "EXACT_PARCEL"
            if SI.same_sale_event(rep, r, tier):
                g.append(i)
                placed = True
                p1, p2 = rep["sale_price"], r["sale_price"]
                merge_log.append({
                    "kept_raw_id": rep["raw_id"], "merged_raw_id":
                        r["raw_id"], "unit_id": uid,
                    "reason": "same_unit_near_date_price",
                    "date1": rep["sale_date"], "date2": r["sale_date"],
                    "price1": p1, "price2": p2,
                    "ident_tier": tier})
                break
        if not placed:
            open_groups.append([i])
    for g in open_groups:
        cid = "TX" + hashlib.sha1(
            f"{uid}|{g[0]}".encode()).hexdigest()[:12]
        canon.append(g)
        for i in g:
            raw2canon[i] = cid

print(f"raw={n_raw}(arcgis {arc_n}) identity={ident_n} "
      f"canon={len(canon)} merges={len(merge_log)} "
      f"pairs={len(pairs)}")

# ============ 4. 组装 canonical sale ============
PORD = {"HOUSE_DETACHED": ("HOUSE", ""), "DUPLEX_HALF_STRATA":
        ("DUPLEX_HALF", ""), "DUPLEX_WHOLE_BLDG": ("DUPLEX_WHOLE", ""),
        "MULTIPLEX_3_4_CANDIDATE": ("MULTIPLEX_UNIT", ""),
        "MULTIPLEX_WHOLE_BLDG": ("MULTIPLEX_WHOLE", ""),
        "TOWNHOUSE": ("TOWNHOUSE", ""),
        "CONDO": ("CONDO", ""), "APARTMENT": ("CONDO", ""),
        "STRATA_UNIT_UNKNOWN": ("UNKNOWN_STRATA", ""),
        "HOUSE_SUSPECT_STRATA": ("HOUSE", ""),
        "HOUSE": ("HOUSE", ""), "DUPLEX": ("DUPLEX_WHOLE", ""),
        "MULTIPLEX_WHOLE": ("MULTIPLEX_WHOLE", ""),
        "MPX": ("MULTIPLEX_UNIT", ""), "OTHER": ("OTHER_RESIDENTIAL", ""),
        "UNKNOWN": ("UNKNOWN", ""), "OTHER_RESIDENTIAL":
        ("OTHER_RESIDENTIAL", "")}
sales = []
for g in canon:
    ms = [raw[i] for i in g]
    comp = next((m for m in ms if m["source"] == "comps_v2"), ms[0])
    arc = next((m for m in ms if m["source"] == "bca_arcgis"), None)
    pc = comp.get("product_class") or ""
    WEAK = ("STRATA_UNIT_UNKNOWN", "UNKNOWN", "OTHER", "")
    if pc in WEAK and arc:
        pc = AC.arc_desc_product(arc.get("product_raw")) or pc
    prod = PORD.get(pc, ("UNKNOWN",))[0]
    pid = comp["pid"] or (arc or {}).get("pid") or ""
    # 税表回连:无 pid 时按街名+门牌区间取候选
    tax_link = {"status": "", "n": 0}
    if not pid:
        tcs = tax_candidates(comp["address_raw"],
                             comp.get("addr_unit_number"),
                             is_strata=(comp["is_strata"]
                                        or (arc or {}).get("is_strata")
                                        or None))
        pids = {t["pid"] for t in tcs if t.get("pid")}
        if len(pids) == 1:
            pid = pids.pop()
            tax_link = {"status": "SINGLE", "n": len(tcs)}
        elif pids:
            tax_link = {"status": "AMBIGUOUS", "n": len(tcs)}
            # 全体候选一致承认的 yb 是地址级事实,可采用
            ys = {t["year_built"] for t in tcs if t.get("year_built")}
            if len(ys) == 1:
                comp["_tax_consensus_yb"] = next(iter(ys))
    sy = int(comp["sale_date"][:4])
    # yb:优先 ≤ 成交年的税务快照,再 enriched/comps
    yb, yb_src, yb_conf = None, "", False
    if pid and pid in tax:
        hist = [(t["year_built"], y) for y, t in tax[pid].items()
                if t.get("year_built")]
        hist = [(int(y0), yr) for y0, yr in hist
                if str(y0).isdigit()]
        ok = [(y0, yr) for y0, yr in hist if y0 <= sy]
        if ok:
            yb, yb_src = sorted(ok, key=lambda x: -x[0])[0][0], \
                "tax" + sorted(ok, key=lambda x: -x[0])[0][1]
        elif hist:
            yb = min(hist)[0]
            yb_src, yb_conf = "tax_conflict", True
    if not yb:
        for m in (comp, arc):
            if m and m.get("year_built"):
                yb = int(m["year_built"])
                yb_src = m["source"]
                break
    if not yb and comp.get("_tax_consensus_yb"):
        yb, yb_src = int(comp["_tax_consensus_yb"]), "tax_addr_consensus"
    if yb and int(yb) > sy:
        yb_conf = True
    band, age = AC.age_band_at_sale(int(yb) if yb else None, sy)
    lat = comp.get("lat") or (arc or {}).get("lat")
    lng = comp.get("lng") or (arc or {}).get("lng")
    comm = comp.get("comm") or ""
    if not comm and lat and lng:
        comm = comm_of(float(lat), float(lng))
    desc = ((arc or {}).get("product_raw") or "").lower()
    gst = comp.get("gst") or ""
    if yb_conf:
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
        "identity_id": identity_of[g[0]],
        "pid": pid,
        "address": comp["address_raw"],
        "unit": comp.get("addr_unit_number") or "",
        "norm_addr": comp["norm_addr"],
        "community": comm, "sale_date": comp["sale_date"],
        "sale_price": comp["sale_price"], "product": prod,
        "product_raw": pc,
        "is_strata": comp["is_strata"] or
        (arc or {}).get("is_strata"),
        "yb": yb or "", "yb_src": yb_src, "age_band": band,
        "sale_kind": skind, "fa": comp.get("fa") or
        (arc or {}).get("fa") or "",
        "lot_sqft": comp.get("lot_sqft") or
        (arc or {}).get("lot_sqft") or "",
        "n_sources": len({m["source"] for m in ms}),
        "sources": "+".join(sorted({m["source"] for m in ms})),
        "lat": lat or "", "lng": lng or "",
        "tax_link": tax_link["status"],
        "tax_link_n": tax_link["n"],
        "asof": comp.get("asof") or ""})

# ============ 5. 核销与输出 ============
# 地址规范化冲突:同 identity 内原始写法差异/组件不一致
addr_conf = []
by_ident = defaultdict(list)
for i, r in enumerate(raw):
    by_ident[identity_of[i]].append(i)
for iid, idxs in by_ident.items():
    raws = {raw[i]["address_raw"] for i in idxs}
    if len(raws) <= 1:
        continue
    addr_conf.append({
        "identity_id": iid,
        "raw_variants": " || ".join(sorted(raws))[:300],
        "n_variants": len(raws),
        "norm_addr": raw[idxs[0]]["norm_addr"],
        "sources": "|".join(sorted({raw[i]["source"]
                                    for i in idxs}))})

# 瀑布:raw → identity → canon 分层
wf_rows = [
    {"stage": "raw_rows", "n": n_raw},
    {"stage": "identity_clusters", "n": ident_n},
    {"stage": "canonical_sales", "n": len(sales)},
    {"stage": "merged_rows", "n": n_raw - len(sales)},
    {"stage": "sales_price_known", "n": sum(1 for s in sales
                                            if s["sale_price"])},
    {"stage": "sales_age_known", "n": sum(1 for s in sales
                                          if s["yb"] != "")},
    {"stage": "sales_age_unknown", "n": sum(
        1 for s in sales if s["age_band"] == "AGE_UNKNOWN")},
    {"stage": "sales_pid_known", "n": sum(1 for s in sales
                                          if s["pid"])},
    {"stage": "sales_fa_known", "n": sum(1 for s in sales
                                         if s["fa"] != "")},
    {"stage": "tax_link_single", "n": sum(
        1 for s in sales if s["tax_link"] == "SINGLE")},
    {"stage": "tax_link_ambiguous", "n": sum(
        1 for s in sales if s["tax_link"] == "AMBIGUOUS")},
]

# 年度×产品源覆盖
cov = defaultdict(lambda: {"captured": 0, "canon": 0,
                           "verified": 0, "latest": "",
                           "srcs": set()})
for s in sales:
    c = cov[(s["sale_date"][:4], s["product"])]
    c["captured"] += len(s["raw_ids"].split("|"))
    c["canon"] += 1
    c["verified"] += s["n_sources"] > 1
    c["latest"] = max(c["latest"], s["sale_date"])
    for so in s["sources"].split("+"):
        c["srcs"].add(so)
PRODS = ["HOUSE", "DUPLEX_HALF", "DUPLEX_WHOLE", "TOWNHOUSE",
         "CONDO", "MULTIPLEX_UNIT", "MULTIPLEX_WHOLE",
         "UNKNOWN_STRATA", "OTHER_RESIDENTIAL", "UNKNOWN"]
cov_rows = []
for y in ("2023", "2024", "2025", "2026"):
    for p in PRODS:
        c = cov.get((y, p), {"captured": 0, "canon": 0,
                             "verified": 0, "latest": "",
                             "srcs": set()})
        status = "COVERED" if c["canon"] else \
            ("SOURCE_NOT_COVERED:BCA层仅存最近成交+早期"
             "非独立屋被后期转售覆盖,公开源无回补"
             if y in ("2023", "2024") else
             "SOURCE_NOT_COVERED:BCA层刷新截至2026-07-08"
             if y == "2026" else "SOURCE_NOT_COVERED")
        if c["canon"]:
            status = "COVERED:" + "+".join(sorted(c["srcs"]))
        cov_rows.append({"year": y, "product": p,
                         "captured_rows": c["captured"],
                         "canonical_sales": c["canon"],
                         "verified_multi_source": c["verified"],
                         "latest_sale": c["latest"],
                         "status": status})


def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        c = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        c.writeheader()
        c.writerows(rows)


wcsv(OUTD / "sales_archive.csv", sales)
wcsv(OUTD / "raw_to_canonical_mapping.csv", [
    {"raw_id": raw[i]["raw_id"], "canonical_tx": raw2canon[i],
     "source": raw[i]["source"], "address_raw": raw[i]["address_raw"],
     "sale_date": raw[i]["sale_date"], "sale_price":
         raw[i]["sale_price"], "identity_id": identity_of[i],
     "unit_id": unit_id(i)}
    for i in sorted(raw2canon)])
wcsv(OUTD / "cross_source_dedup_reconciliation.csv", [
    {"metric": "raw_rows", "n": n_raw},
    {"metric": "identity_clusters", "n": ident_n},
    {"metric": "canonical_sales", "n": len(sales)},
    {"metric": "merged_rows", "n": n_raw - len(sales)},
    {"metric": "multi_source_sales", "n": sum(
        1 for s in sales if s["n_sources"] > 1)},
    {"metric": "match_pairs", "n": len(pairs)},
    {"metric": "conflict_pairs", "n": sum(
        1 for m in pairs.values()
        if m["tier"] == "IDENTITY_CONFLICT")}])
wcsv(OUTD / "merge_evidence.csv", merge_log)
wcsv(OUTD / "spatial_identity_match_audit.csv", [
    {"tier": t, "pairs": n} for t, n in
    Counter(m["tier"] for m in pairs.values()).most_common()])
wcsv(OUTD / "canonical_transaction_waterfall.csv", wf_rows)
wcsv(OUTD / "source_coverage_2023_2026.csv", cov_rows)
wcsv(OUTD / "address_normalization_conflicts.csv", addr_conf)
wcsv(OUTD / "tax_attribute_recovery.csv", [
    {"metric": "sales_pid_known", "n": sum(
        1 for s in sales if s["pid"])},
    {"metric": "sales_yb_known", "n": sum(
        1 for s in sales if s["yb"] != "")},
    {"metric": "yb_src_tax", "n": sum(
        1 for s in sales if str(s["yb_src"]).startswith("tax"))},
    {"metric": "yb_src_enriched_or_comps", "n": sum(
        1 for s in sales if s["yb_src"] in
        ("comps_v2", "bca_arcgis"))},
    {"metric": "yb_src_addr_consensus", "n": sum(
        1 for s in sales if s["yb_src"] == "tax_addr_consensus")},
    {"metric": "age_unknown", "n": sum(
        1 for s in sales if s["age_band"] == "AGE_UNKNOWN")},
    {"metric": "age_conflict", "n": sum(
        1 for s in sales
        if s["age_band"] == "AGE_TEMPORAL_CONFLICT")},
    {"metric": "tax_link_single", "n": sum(
        1 for s in sales if s["tax_link"] == "SINGLE")},
    {"metric": "tax_link_ambiguous", "n": sum(
        1 for s in sales if s["tax_link"] == "AMBIGUOUS")}])

# 年度×产品×房龄矩阵
mat = defaultdict(list)
for s in sales:
    mat[(s["sale_date"][:4], s["product"], s["age_band"])].append(s)
mrows = []
for (y, p, ab), es in sorted(mat.items()):
    ps = sorted(e["sale_price"] for e in es)
    mrows.append({"year": y, "product": p, "age_band": ab,
                  "n_tx": len(es),
                  "n_properties": len({e["identity_id"] for e in es}),
                  "p50": round(ps[len(ps) // 2]),
                  "verified_sources": sum(
                      1 for e in es if e["n_sources"] > 1),
                  "latest": max(e["sale_date"] for e in es)})
wcsv(OUTD / "transactions_by_year_product_age.csv", mrows)

# ---- GitHub 交付副本(聚合/审计件;行级成交档案留本地) ----
for name in ("source_coverage_2023_2026.csv",
             "address_normalization_conflicts.csv",
             "spatial_identity_match_audit.csv",
             "cross_source_dedup_reconciliation.csv",
             "canonical_transaction_waterfall.csv",
             "tax_attribute_recovery.csv",
             "transactions_by_year_product_age.csv"):
    src_f = OUTD / name
    if src_f.exists():
        (DOCS / name).write_text(src_f.read_text())

summary = {"raw_rows": n_raw,
           "canonical_sales": len(sales),
           "identity_clusters": ident_n,
           "merge_log": len(merge_log),
           "pairs_by_tier": dict(Counter(m["tier"]
                                         for m in pairs.values())),
           "by_year": dict(Counter(s["sale_date"][:4]
                                   for s in sales)),
           "by_product": dict(Counter(s["product"] for s in sales)),
           "by_age": dict(Counter(s["age_band"] for s in sales)),
           "tax_years": TAX_YEARS,
           "age_unknown": sum(1 for s in sales
                              if s["age_band"] == "AGE_UNKNOWN")}
(OUTD / "manifest.json").write_text(json.dumps(
    {"version": "sales_archive_v1_0_1",
     "run": datetime.datetime.now().isoformat(),
     "input_sha256": HASHES, "counts": summary},
    ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
