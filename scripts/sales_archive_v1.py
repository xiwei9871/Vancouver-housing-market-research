#!/usr/bin/env python3
"""Sales Archive V1 — 统一成交事实档案(交易级,非地址级)。
五层:Source Registry → Transaction → Property → Attribute History →
统计层。同实体多源合并留痕;dup 标记不删除;事实档案≠可比池。
"""
import csv, json, sys, datetime, hashlib
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import nbx_core as K                              # noqa: E402
import archive_core as AC                         # noqa: E402
import newbuild_evidence_v1_2 as B                # noqa: E402

OUTD = ROOT / "data/research/normalized/sales_archive_v1"
OUTD.mkdir(parents=True, exist_ok=True)

BOUND = ROOT / "data/research/boundaries/" \
    "vancouver-local-area-boundary-official.geojson"
BFD = ROOT / "data/research/normalized/audit_v1_2_2"


# ---- 边界点入多边形(arc-only 记录取社区) ----
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


_bound = json.loads(BOUND.read_text())
def comm_of(lat, lng):
    if not lat or not lng:
        return "?"
    for f in _bound["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" \
            else [geom["coordinates"]]
        for poly in polys:
            if _pip(lat, lng, poly[0]):
                return K.rc_split(f["properties"]["name"], lat)
    return "?"


# ---- 输入 ----
backfill = {(r["address"], r["date"]): r for r in csv.DictReader(
    open(BFD / "bca_yb_backfill_results.csv"))}
tax23, tax26 = {}, {}
for yr, p in (("tax2023", "property-tax-2023-yb2018plus.csv"),
              ("tax2026", "property-tax-2026-yb2018plus.csv")):
    tgt = tax23 if yr == "tax2023" else tax26
    fp = B.ROOT / "data/research/raw/official" / p
    for t in csv.DictReader(open(fp, encoding="utf-8-sig"),
                            delimiter=";"):
        if t.get("year_built") and t.get("pid"):
            tgt[t["pid"]] = t


# ---- 交易实体构建 ----
ents = {}
dup_log = []
def upsert(src_rec, src):
    """写入或合并交易实体;同 key 价差超阈=不同交易。"""
    key = AC.tx_key(src_rec.get("pid"), src_rec["norm_addr"],
                    src_rec["sale_date"])
    e = ents.get(key)
    if e and not AC.same_tx_price(e["sale_price"], src_rec["sale_price"]):
        # 同地址同日不同价 → 不同单元/不同交易,后缀分化
        i = 2
        while f"{key}#{i}" in ents:
            i += 1
        key = f"{key}#{i}"
        dup_log.append({"key": key, "why": "same_key_price_diff",
                        "p1": e["sale_price"], "p2": src_rec["sale_price"]})
        e = None
    if not e:
        e = {"transaction_id": "TX" + hashlib.sha1(
            key.encode()).hexdigest()[:12],
             "property_id": src_rec.get("pid") or src_rec["norm_addr"],
             "pid": src_rec.get("pid") or "",
             "address": src_rec["address"],
             "norm_addr": src_rec["norm_addr"],
             "unit_no": K.unit_prefix(src_rec["address"]) or "",
             "sale_date": src_rec["sale_date"],
             "sale_price": src_rec["sale_price"],
             "sources": [], "_raw": {}}
        ents[key] = e
    e["sources"].append(src)
    e["_raw"][src] = src_rec
    return e


def archive_product(prod, scope):
    """规范化为任务书产品集。"""
    if prod == "HOUSE":
        return "HOUSE"
    if prod == "DUPLEX":
        return "DUPLEX_WHOLE" if scope == "WHOLE" else "DUPLEX_HALF"
    if prod in ("MPX", "MPX_WHOLE"):
        return "MULTIPLEX_WHOLE" if scope == "WHOLE" else "MULTIPLEX_UNIT"
    if prod == "TOWNHOUSE":
        return "TOWNHOUSE"
    if prod in ("APARTMENT", "CONDO"):
        return "CONDO"
    if prod in ("OTHER",):
        return "OTHER_RESIDENTIAL"
    return "UNKNOWN"


for r in B.COMPS:                       # comps-v2(MLS派生)
    upsert({"pid": r.get("pid"), "address": r["address"],
            "norm_addr": K.norm_addr(r["address"]),
            "sale_date": r["sale_date"], "sale_price": r["_p"],
            "product": archive_product(r["_prod"], r.get("_scope")),
            "product_raw": r["product_class"],
            "is_strata": r.get("is_strata") == "Y",
            "community": r["_comm"], "gst": r["gst_basis"],
            "fa": r["_fa"], "yb": r.get("year_built"),
            "lot": r.get("lot_sqft"), "asof": r.get("asof_status")},
           "comps_v2")

arc_dup_merged = arc_only = 0
for r in B.arcgis:                      # BCA arcgis
    lat, lng = r.get("latitude"), r.get("longitude")
    rec = {"pid": "", "address": r["address"],
           "norm_addr": K.norm_addr(r["address"]),
           "sale_date": r["sale_date"], "sale_price": r["sale_price"],
           "product": archive_product(AC.arc_desc_product(
               r.get("official_bca_description")), ""),
           "product_raw": r.get("official_bca_description") or "",
           "is_strata": r.get("official_bca_is_strata") == "Y",
           "community": comm_of(lat, lng), "gst": "",
           "fa": None, "yb": None, "lot": r.get("lot_area_sqft"),
           "asof": "", "roll": r.get("bca_oid_evbc") or "",
           "coords": (lat, lng)}
    upsert(rec, "bca_arcgis")

# ---- 属性历史层:yb 候选按时间轴选 ----
for e in ents.values():
    sy = int(e["sale_date"][:4]) if e["sale_date"] else 0
    cands = []
    pid = e["pid"]
    if pid and pid in tax23:
        cands.append((tax23[pid]["year_built"], "tax2023"))
    if pid and pid in tax26:
        cands.append((tax26[pid]["year_built"], "tax2026"))
    bf = backfill.get((e["address"], e["sale_date"])) or \
        backfill.get((e["norm_addr"], e["sale_date"]))
    if bf and (bf["bca_year_built"] or "").isdigit():
        cands.append((bf["bca_year_built"], "bca_detail"))
        e["bca_detail_pid"] = bf["bca_pid"]
        e["bca_fa"] = bf["bca_first_floor"]
        if not pid and bf["bca_pid"]:
            e["pid"] = bf["bca_pid"]
            e["property_id"] = bf["bca_pid"]
    comp_yb = (e["_raw"].get("comps_v2") or {}).get("yb")
    if comp_yb and str(comp_yb).isdigit():
        cands.append((comp_yb, "comps"))
    e["yb_at_sale"], e["yb_source"], e["yb_conflict"] = \
        AC.pick_yb_at_sale(cands, sy)
    e["age_band"], e["age_at_sale"] = AC.age_band_at_sale(
        e["yb_at_sale"], sy)
    e["yb_conflict"] = e["age_band"] == "AGE_TEMPORAL_CONFLICT"

    # 综合维度
    arc_r = e["_raw"].get("bca_arcgis") or {}
    comp_r = e["_raw"].get("comps_v2") or {}
    e["product"] = comp_r.get("product") or arc_r.get("product") \
        or "UNKNOWN"
    e["is_strata"] = comp_r.get("is_strata") or arc_r.get("is_strata")
    e["ownership"] = "STRATA" if e["is_strata"] else "FREEHOLD"
    e["community"] = comp_r.get("community") or arc_r.get("community")
    e["fa"] = comp_r.get("fa") or \
        (int((e.get("bca_fa") or "0").replace(",", "") or 0) or None)
    e["lot"] = comp_r.get("lot") or arc_r.get("lot")
    e["gst"] = comp_r.get("gst") or ""
    e["project_id"] = (B.proj_by_base.get(K.base_addr(e["address"]))
                       or {}).get("project_id", "")
    e["confidence"] = "SOURCE_VERIFIED" if len(e["sources"]) > 1 else \
        "SOURCE_TRACEABLE"
    # sale_kind
    desc = (arc_r.get("product_raw") or "").lower()
    if e["yb_conflict"]:
        e["sale_kind"] = "PRECONSTRUCTION_OR_LAND"
    elif "vacant" in desc or \
            (arc_r and not arc_r.get("_raw", {}).get("bca_improvement")
             and "vacant" in desc):
        e["sale_kind"] = "LAND_OR_VACANT"
    elif e["gst"] == "new_first_sale":
        e["sale_kind"] = "FIRST_SALE"
    elif e["gst"].startswith("res"):
        e["sale_kind"] = "RESALE"
    else:
        e["sale_kind"] = "UNKNOWN"

    del e["_raw"]

EL = list(ents.values())

# ---- 核销:同址同价近邻日期的重复登记(arcgis 内部) ----
intra_dup = 0
by_a = defaultdict(list)
for e in EL:
    by_a[e["norm_addr"]].append(e)
for a, es in by_a.items():
    for i in range(len(es)):
        for j in range(i + 1, len(es)):
            d = abs((datetime.date.fromisoformat(es[i]["sale_date"]) -
                     datetime.date.fromisoformat(es[j]["sale_date"])
                     ).days) if es[i]["sale_date"] and \
                es[j]["sale_date"] else 99
            if d <= 14 and es[i]["sale_price"] == es[j]["sale_price"]:
                es[j]["record_note"] = "POSSIBLE_DUPLICATE_RECORDING"
                intra_dup += 1

# ---- Source Registry ----
src_reg = [
    {"source": "comps_v2", "type": "MLS-derived comps",
     "coverage": "2023-2026", "rows": len(B.COMPS),
     "fields": "price/date/addr/product/yb/fa/pid/gst",
     "limits": "restricted;已授权持有", "last_sale_only": "no"},
    {"source": "bca_arcgis", "type": "BCA sales layer",
     "coverage": "2025-2026", "rows": len(B.arcgis),
     "fields": "price/date/desc/values/lot/coords/strata/roll",
     "limits": "公开地图层;无 yb/fa;sales_refresh 2026-07",
     "last_sale_only": "rows_per_sale_event(同日同价重复登记存在)"},
    {"source": "bca_detail", "type": "BCA PropertyInformation",
     "coverage": "current", "rows": len(backfill),
     "fields": "yb/floors/basement/pid",
     "limits": "ToS 限制自动采集;一次性回填已存档",
     "last_sale_only": "n/a"},
    {"source": "tax_2023_2026", "type": "CoV property tax",
     "coverage": "yb2018+ folios", "rows": len(tax23) + len(tax26),
     "fields": "pid/folio/plan/legal_type/zoning/yb/values",
     "limits": "官方开放集", "last_sale_only": "n/a"}]

# ---- 瀑布 ----
wf = Counter()
for e in EL:
    wf["captured"] += 1
    if e["sale_price"]:
        wf["price_known"] += 1
    if e["yb_at_sale"]:
        wf["age_known"] += 1
    if e["fa"]:
        wf["fa_known"] += 1
    if e["pid"]:
        wf["pid_known"] += 1
    if (e["sale_price"] and e["yb_at_sale"] and e["fa"]
            and not e["yb_conflict"]
            and e["product"] in ("HOUSE", "DUPLEX", "MPX")):
        wf["comparable_ready"] += 1
    if e.get("record_note"):
        wf["dup_flagged"] += 1

# ---- 统计 ----
def stats(gf):
    g = defaultdict(list)
    for e in EL:
        if e["sale_price"]:
            g[gf(e)].append(e)
    out = []
    for k, es in sorted(g.items()):
        ps = sorted(e["sale_price"] for e in es)
        n = len(ps)
        fa = [e["sale_price"] / e["fa"] for e in es if e["fa"]]
        out.append({
            "key": "|".join(map(str, k)), "n_tx": n,
            "n_properties": len({e["property_id"] for e in es}),
            "n_projects": len({e["project_id"] for e in es
                               if e["project_id"]}),
            "p25": round(ps[n // 4]), "p50": round(ps[n // 2]),
            "p75": round(ps[3 * n // 4]),
            "ppsf_p50": round(sorted(fa)[len(fa) // 2]) if fa else "",
            "first_sale": sum(1 for e in es
                              if e["sale_kind"] == "FIRST_SALE"),
            "resale": sum(1 for e in es if e["sale_kind"] == "RESALE"),
            "unknown_kind": sum(1 for e in es
                                if e["sale_kind"] == "UNKNOWN"),
            "strata": sum(1 for e in es if e["is_strata"]),
            "yb_missing": sum(1 for e in es if not e["yb_at_sale"]),
            "fa_missing": sum(1 for e in es if not e["fa"]),
            "verified": sum(1 for e in es
                            if e["confidence"] == "SOURCE_VERIFIED"),
            "latest": max(e["sale_date"] for e in es)})
    return out


by_yp = stats(lambda e: (e["sale_date"][:4], e["product"]))
by_ab = stats(lambda e: (e["sale_date"][:4], e["age_band"]))
by_cap = stats(lambda e: (e["sale_date"][:4], e["community"],
                          e["product"], e["age_band"]))


def wcsv(path, rows):
    if not rows:
        path.write_text(""); return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


FIELDS = ["transaction_id", "property_id", "pid", "address", "unit_no",
          "community", "sale_date", "sale_price", "product",
          "ownership", "is_strata", "yb_at_sale", "yb_source",
          "age_at_sale", "age_band", "sale_kind", "gst", "fa", "lot",
          "project_id", "confidence", "sources", "record_note"]
wcsv(OUTD / "sales_archive.csv",
     [{k: ("+".join(e["sources"]) if k == "sources"
           else e.get(k, "")) for k in FIELDS} for e in EL])
wcsv(OUTD / "sales_archive_source_inventory.csv", src_reg)
wcsv(OUTD / "sales_capture_waterfall.csv", [
    {"stage": k, "n": v} for k, v in wf.items()])
wcsv(OUTD / "transactions_by_year_product.csv", by_yp)
wcsv(OUTD / "transactions_by_age_band.csv", by_ab)
wcsv(OUTD / "transactions_by_community_age_product.csv", by_cap)
arc_merged = sum(1 for e in EL
                 if {"comps_v2", "bca_arcgis"} <= set(e["sources"]))
wcsv(OUTD / "transaction_dedup_reconciliation.csv", [
    {"metric": "input_rows", "n": len(B.COMPS) + len(B.arcgis)},
    {"metric": "entities", "n": len(EL)},
    {"metric": "arcgis_merged_to_comps", "n": arc_merged},
    {"metric": "arcgis_only", "n": sum(
        1 for e in EL if e["sources"] == ["bca_arcgis"])},
    {"metric": "intra_source_dup_flagged", "n": intra_dup},
    {"metric": "multi_source", "n": sum(
        1 for e in EL if len(e["sources"]) > 1)}])
wcsv(OUTD / "attribute_history_quality.csv", [
    {"dim": "yb_known", "n": sum(1 for e in EL if e["yb_at_sale"])},
    {"dim": "yb_tax2023", "n": sum(
        1 for e in EL if e["yb_source"] == "tax2023")},
    {"dim": "yb_tax2026", "n": sum(
        1 for e in EL if e["yb_source"] == "tax2026")},
    {"dim": "yb_bca_detail", "n": sum(
        1 for e in EL if e["yb_source"] == "bca_detail")},
    {"dim": "yb_comps", "n": sum(
        1 for e in EL if e["yb_source"] == "comps")},
    {"dim": "fa_known", "n": sum(1 for e in EL if e["fa"])},
    {"dim": "conflict", "n": sum(
        1 for e in EL if e["yb_conflict"])},
    {"dim": "unknown_age", "n": sum(
        1 for e in EL if e["age_band"] == "AGE_UNKNOWN")}])

schema = {
    "entities": {
        "Transaction": "每行一笔交易;多源合并;sources[] 留痕",
        "Property": "property_id(pid 或 norm_addr);一址可对应多 pid",
        "Project": "project_id(permit 组);可含多单元",
        "Unit": "unit_no + property_id;strata 单元",
        "SourceEvidence": "sources[] + 各源原始字段",
        "AttributeHistory": "yb_source/yb_at_sale 时点化"},
    "rules": ["多笔成交不删(同 pid 不同日期各留)",
              "拆地/重建/拆产权→状态标记不删除",
              "yb_at_sale≤成交年优先 tax2023 快照",
              "yb>成交年 → AGE_TEMPORAL_CONFLICT",
              "无全市分母 → CAPTURED_ONLY 非市场总量"]}
(OUTD / "sales_archive_schema.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=1))

summary = {
    "entities": len(EL),
    "by_year": dict(Counter(e["sale_date"][:4] for e in EL)),
    "by_product": dict(Counter(e["product"] for e in EL)),
    "by_age": dict(Counter(e["age_band"] for e in EL)),
    "by_kind": dict(Counter(e["sale_kind"] for e in EL)),
    "waterfall": dict(wf),
}
(OUTD / "manifest.json").write_text(json.dumps({
    "version": "sales_archive_v1",
    "run": datetime.datetime.now().isoformat(),
    "input_sha256": B.HASHES, "counts": summary},
    ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
