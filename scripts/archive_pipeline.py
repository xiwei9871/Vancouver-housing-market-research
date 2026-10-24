#!/usr/bin/env python3
"""Sales Archive V1.0.2 — 可测试管线核心(纯函数,无 IO)。

层次:
  Raw Records → Parcel Identity → Unit Identity →
  Transaction Candidates → Canonical Sales

与 V1.0.1 的差异(修复点):
- add_raw 直写 SI.SCHEMA 规范字段(unit_number/base_address/…),
  不再有 addr_* / base_addr 错位。
- folio 只放 CoV 税务 folio;BCA bca_oid_evbc 是 EVBC 对象号,
  单独存 bca_evbc_oid,不作 folio 证据。
- legal 描述区分 strata lot 与细分 plan+lot;
  细分 plan 不再触发整街区 EXACT_PARCEL 假合并。
- EXACT_PARCEL 只进 parcel 簇;unit 簇需 unit_mergeable。
- union_checked 在传递合并时拦截 pid/plan+lot 矛盾簇。
- MPX 候选与验证彻底分离;vacant/空地永远不成 MPX。
- 房龄证据七级分级。
"""
import re, hashlib
from collections import defaultdict

import address_norm as AN
import spatial_identity as SI


# ============ Raw schema ============
RAW_KEYS = ("raw_id", "source", "source_record_id", "address_raw",
            "bca_raw_address", "mls_raw_address", "unit_number",
            "street_number", "street_name", "street_type",
            "street_direction", "qualifier", "normalized_address",
            "base_address", "municipality", "pid", "folio",
            "bca_evbc_oid", "land_coordinate", "strata_plan",
            "strata_lot", "legal_plan", "legal_lot", "lat", "lng",
            "lot_sqft", "sale_date", "sale_price", "product_class",
            "product_raw", "is_strata", "year_built", "fa", "gst",
            "asof", "comm", "bca_improvement", "files")


def normalize_record(raw_id, src, src_rid, **kw):
    """建一条符合 schema 的 raw 记录;缺失键显式补 None/''。
    返回 (record, schema_issues)。"""
    p = AN.parse(kw.get("address_raw"))
    ids = AN.legal_ids(kw.get("legal_description"))
    rec = {
        "raw_id": raw_id, "source": src,
        "source_record_id": src_rid or "",
        "address_raw": kw.get("address_raw") or "",
        "bca_raw_address": kw.get("bca_raw_address") or "",
        "mls_raw_address": kw.get("mls_raw_address") or "",
        "unit_number": p["unit_number"] or "",
        "street_number": p["street_number"] or "",
        "street_name": p["street_name"] or "",
        "street_type": p["street_type"] or "",
        "street_direction": p["street_direction"] or "",
        "qualifier": p["qualifier"] or "",
        "normalized_address": p["normalized_address"] or "",
        "base_address": p["base_address"] or "",
        "municipality": kw.get("municipality") or "",
        "pid": (kw.get("pid") or "").strip(),
        "folio": (kw.get("folio") or "").strip(),
        "bca_evbc_oid": (kw.get("bca_evbc_oid") or "").strip(),
        "land_coordinate": (kw.get("land_coordinate") or "").strip(),
        "strata_plan": ids["strata_plan"] or "",
        "strata_lot": ids["strata_lot"] or "",
        "legal_plan": ids["legal_plan"] or "",
        "legal_lot": ids["legal_lot"] or "",
        "lat": kw.get("lat"), "lng": kw.get("lng"),
        "lot_sqft": kw.get("lot_sqft"),
        "sale_date": kw.get("sale_date") or "",
        "sale_price": kw.get("sale_price"),
        "product_class": kw.get("product_class") or "",
        "product_raw": kw.get("product_raw") or "",
        # 三态:True/False/None(未知绝不推定非 strata)
        "is_strata": kw.get("is_strata"),
        "year_built": kw.get("year_built"),
        "fa": kw.get("fa"), "gst": kw.get("gst") or "",
        "asof": kw.get("asof") or "", "comm": kw.get("comm") or "",
        "bca_improvement": kw.get("bca_improvement"),
        "files": kw.get("files") or "",
    }
    return rec, SI.validate_record(rec, ctx=raw_id + ":")


# ============ 候选对 + 分层判定 ============
def build_pairs(raw):
    """空间网格 + 同基地址候选 → { (i,j): match }。"""
    grid, addr_map = defaultdict(list), defaultdict(list)
    for i, r in enumerate(raw):
        k = SI.grid_key(r.get("lat"), r.get("lng"))
        if k:
            grid[k].append(i)
        if r["base_address"]:
            addr_map[r["base_address"]].append(i)
    pairs = {}
    for i, r in enumerate(raw):
        cand = []
        if r.get("lat") is not None:
            for c in SI.neighbor_cells(r["lat"], r["lng"]):
                cand += grid.get(c, [])
        cand += addr_map.get(r["base_address"], [])
        for j in set(cand):
            if j <= i:
                continue
            m = SI.match(raw[i], raw[j])
            if m["tier"] != "UNMATCHED":
                pairs[(i, j)] = m
    return pairs


# ============ 双层 Union-Find ============
class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb
            return True
        return False


def _unit_keyset(r):
    """记录自带的单元级身份键(用于 union 冲突拦截)。"""
    ks = set()
    if r["pid"]:
        ks.add(("pid", r["pid"]))
    if r["strata_plan"] and r["strata_lot"]:
        ks.add(("planlot", f"{r['strata_plan']}/{r['strata_lot']}"))
    return ks


def build_clusters(raw, pairs):
    """parcel_uf(可同址/同地块聚合)与 unit_uf(仅单元确认)。
    返回 dict(parcel_of, unit_of, blocked, conflicts)。"""
    n = len(raw)
    puf, uuf = UF(n), UF(n)
    # parcel 层:任何非冲突非纯候选证据可聚合同一地块/建筑
    PARCEL_TIERS = ("EXACT_PID_UNIT", "EXACT_PARCEL",
                    "SPATIAL_ADDRESS_CORROBORATED", "ADDRESS_ONLY")
    blocked = []
    for (i, j), m in pairs.items():
        if m["tier"] in PARCEL_TIERS:
            puf.union(i, j)
    # unit 层:仅 unit_mergeable,且合簇不得混入互相矛盾的
    # pid / plan+lot(传递拦截:根键集增量维护,不是逐对全扫)
    root_keys = {i: _unit_keyset(raw[i]) for i in range(n)}
    for (i, j), m in pairs.items():
        if m["tier"] == "IDENTITY_CONFLICT":
            continue
        if not SI.unit_mergeable(raw[i], raw[j], m):
            continue
        ra, rb = uuf.find(i), uuf.find(j)
        if ra == rb:
            continue
        merged = root_keys.get(ra, set()) | root_keys.get(rb, set())
        pids = {v for t, v in merged if t == "pid"}
        lots = {v for t, v in merged if t == "planlot"}
        if len(pids) > 1 or len(lots) > 1:
            blocked.append({"i": i, "j": j,
                            "reason": "conflicting_pid_or_planlot",
                            "pids": sorted(pids)[:4],
                            "lots": sorted(lots)[:4]})
            continue
        uuf.union(ra, rb)
        root_keys[uuf.find(ra)] = merged
    parcel_of = {i: puf.find(i) for i in range(n)}
    unit_of = {i: uuf.find(i) for i in range(n)}
    # 簇级冲突复核(兜底:即使 pair 层漏掉,簇内矛盾也要暴露)
    conflicts = []
    ucl = defaultdict(list)
    for i in range(n):
        ucl[uuf.find(i)].append(i)
    for root, ms in ucl.items():
        ks = [k for i in ms for k in _unit_keyset(raw[i])]
        pids = {v for t, v in ks if t == "pid"}
        lots = {v for t, v in ks if t == "planlot"}
        units = {raw[i]["unit_number"] for i in ms
                 if raw[i]["unit_number"]}
        if len(pids) > 1 or len(lots) > 1 or len(units) > 1:
            conflicts.append({"cluster_root": root,
                              "members": ms,
                              "pids": sorted(pids),
                              "planlots": sorted(lots),
                              "units": sorted(units)})
    return {"parcel_of": parcel_of, "unit_of": unit_of,
            "blocked": blocked, "conflicts": conflicts,
            "n_parcel": len({v for v in parcel_of.values()}),
            "n_unit": len({v for v in unit_of.values()})}


# ============ 成交归并 ============
def dedup_sales(raw, unit_of, parcel_of, pairs):
    """同单元簇 + 成交事件窗 → canonical 组。
    返回 (canon_groups, merge_log, candidate_dups)。
    candidate_dups:同 parcel 簇不同单元、事件窗命中的疑似重复,
    不合并,进人工核对。"""
    by_unit = defaultdict(list)
    for i, r in enumerate(raw):
        if r.get("sale_date") and r.get("sale_price"):
            by_unit[unit_of[i]].append(i)
    canon, merge_log = [], []
    for uid, idxs in by_unit.items():
        idxs.sort(key=lambda i: (raw[i]["sale_date"],
                                 raw[i]["sale_price"]))
        groups = []
        for i in idxs:
            r = raw[i]
            placed = False
            for g in groups:
                rep = raw[g[0]]
                t = pairs.get((min(i, g[0]), max(i, g[0])),
                              {}).get("tier", "UNIT_CLUSTER")
                if SI.same_sale_event(rep, r, t, unit_ok=True):
                    g.append(i)
                    placed = True
                    merge_log.append({
                        "kept_raw_id": rep["raw_id"],
                        "merged_raw_id": r["raw_id"],
                        "unit_cluster": uid,
                        "reason": "same_unit_sale_event",
                        "ident_tier": t,
                        "date1": rep["sale_date"],
                        "date2": r["sale_date"],
                        "price1": rep["sale_price"],
                        "price2": r["sale_price"]})
                    break
            if not placed:
                groups.append([i])
        for g in groups:
            canon.append(g)
    # 候选重复:parcel 同簇、unit 异簇、事件窗命中 → 人工核对
    cand = []
    by_parcel = defaultdict(list)
    for i, r in enumerate(raw):
        if r.get("sale_date") and r.get("sale_price"):
            by_parcel[parcel_of[i]].append(i)
    for pid_root, idxs in by_parcel.items():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, j = idxs[x], idxs[y]
                if unit_of[i] == unit_of[j]:
                    continue
                a, b = raw[i], raw[j]
                if SI.same_sale_event(a, b, "EXACT_PID_UNIT",
                                      unit_ok=True):
                    cand.append({
                        "parcel_cluster": pid_root,
                        "raw_a": a["raw_id"], "raw_b": b["raw_id"],
                        "date_a": a["sale_date"],
                        "date_b": b["sale_date"],
                        "price_a": a["sale_price"],
                        "price_b": b["sale_price"],
                        "base_a": a["base_address"],
                        "base_b": b["base_address"],
                        "unit_a": a["unit_number"],
                        "unit_b": b["unit_number"]})
    return canon, merge_log, cand


# ============ 产品分类 ============
MULTI_DESC = ("multiple residence", "multi-family", "multi family",
              "apartment block", "4-plex", "fourplex", "triplex",
              "three-plex", "6 units", "row housing")


def classify_product(comp_pc, arc_desc, is_strata, unit_number,
                     pid, strata_lot, bca_improvement, sale_price):
    """返回 (product, verification, reasons)。
    verification ∈ VERIFIED / CANDIDATE / UNKNOWN。
    MPX 候选绝不自动升级;vacant/空地永不进 MPX。"""
    desc = (arc_desc or "").lower()
    reasons = []
    vacant = "vacant" in desc or \
        (bca_improvement in (0, 0.0) and "multi" in desc)
    unit_ev = bool(unit_number) or bool(strata_lot)
    pc = comp_pc or ""

    if vacant:
        return "OTHER_RESIDENTIAL", "VERIFIED", \
            "vacant_or_zero_improvement"

    def _ver_mpx_unit():
        # V1.0.3 收紧:单元身份(strata+unit+pid)只证单元,
        # 项目类型必须独立证据 —— 通用 "Strata Apartment"/
        # "Townhouse" 描述不构成 multiplex 项目证据
        ev = []
        if is_strata is True:
            ev.append("strata")
        if unit_ev:
            ev.append("unit_id")
        if pid:
            ev.append("pid")
        if any(k in desc for k in MULTI_DESC):
            ev.append("desc_multi")
        ok = is_strata is True and unit_ev and bool(pid) and \
            "desc_multi" in ev
        return ok, "|".join(ev)

    def _ver_mpx_whole():
        ev = []
        if is_strata is False:
            ev.append("non_strata")
        if pid:
            ev.append("pid")
        if any(k in desc for k in MULTI_DESC):
            ev.append("desc_multi")
        ok = is_strata is False and bool(pid) and \
            "desc_multi" in ev
        return ok, "|".join(ev)

    if pc in ("MULTIPLEX_3_4_CANDIDATE", "MPX",
              "MULTIPLEX_WHOLE_BLDG", "MULTIPLEX_WHOLE"):
        vu, eu = _ver_mpx_unit()
        vw, ew = _ver_mpx_whole()
        if pc in ("MULTIPLEX_WHOLE_BLDG", "MULTIPLEX_WHOLE"):
            if vw:
                return "MULTIPLEX_WHOLE", "VERIFIED", ew
            if vu:
                return "MULTIPLEX_UNIT", "VERIFIED", eu
            base = "MULTIPLEX_UNIT" if is_strata is True \
                else "MULTIPLEX_WHOLE"
            return base, "CANDIDATE", ew or eu or "insufficient_evidence"
        if vu:
            return "MULTIPLEX_UNIT", "VERIFIED", eu
        if vw:
            return "MULTIPLEX_WHOLE", "VERIFIED", ew
        base = "MULTIPLEX_UNIT" if is_strata is True \
            else "MULTIPLEX_WHOLE"
        return base, "CANDIDATE", eu or ew or "insufficient_evidence"

    if "duplex" in desc or pc in ("DUPLEX", "DUPLEX_HALF_STRATA",
                                  "DUPLEX_WHOLE_BLDG"):
        if pc == "DUPLEX_HALF_STRATA" or \
                (is_strata is True and unit_ev):
            return "DUPLEX_HALF", "VERIFIED", "strata_half"
        if pc == "DUPLEX_WHOLE_BLDG" or \
                (is_strata is False and "duplex" in desc):
            return "DUPLEX_WHOLE", "VERIFIED", "non_strata_whole"
        return "DUPLEX_SCOPE_UNKNOWN", "CANDIDATE", "scope_unresolved"

    table = {"HOUSE_DETACHED": "HOUSE", "HOUSE": "HOUSE",
             "HOUSE_SUSPECT_STRATA": "HOUSE",
             "TOWNHOUSE": "TOWNHOUSE", "CONDO": "CONDO",
             "APARTMENT": "CONDO", "STRATA_UNIT_UNKNOWN":
             "UNKNOWN_STRATA", "OTHER": "OTHER_RESIDENTIAL",
             "OTHER_RESIDENTIAL": "OTHER_RESIDENTIAL",
             "UNKNOWN": "UNKNOWN", "": "UNKNOWN"}
    prod = table.get(pc)
    if prod is None or prod in ("UNKNOWN", "UNKNOWN_STRATA"):
        arc = _arc_fallback(desc)
        if arc:
            prod, reasons = arc, "arc_desc_fallback"
        else:
            prod = prod or "UNKNOWN"
    return prod, "VERIFIED" if prod not in ("UNKNOWN",
            "UNKNOWN_STRATA") else "UNKNOWN", "|".join(reasons)


def _arc_fallback(desc):
    d = desc
    if "townhouse" in d or "row house" in d:
        return "TOWNHOUSE"
    if "apartment" in d or "condominium" in d:
        return "CONDO"
    if "duplex" in d:
        return "DUPLEX_SCOPE_UNKNOWN"
    if "house" in d or "dwelling" in d or "sty" in d:
        return "HOUSE"
    return None


PRODUCTS = ("HOUSE", "DUPLEX_HALF", "DUPLEX_WHOLE",
            "DUPLEX_SCOPE_UNKNOWN", "TOWNHOUSE", "CONDO",
            "MULTIPLEX_UNIT", "MULTIPLEX_WHOLE",
            "UNKNOWN_STRATA", "OTHER_RESIDENTIAL", "UNKNOWN")


def duplex_scope(is_strata, desc, unit_number, strata_lot, pid,
                 has_strata_neighbor=False, strata_plan=""):
    """duplex 交易范围证据分级(独立判断,不改成交记录)。
    strata lot/单元号是强于 is_strata 标志的法律证据:
    comps 的 is_strata=N 在 duplex 半户上不可靠(EPS plan +
    半户 pid 才是事实)。返回 (scope, verification, reasons)。
    """
    ev = []
    unit_ev = bool(unit_number) or bool(strata_lot) \
        or bool(strata_plan)
    strata_ev = (is_strata is True) or \
        bool(strata_lot) or bool(strata_plan)
    desc_duplex = "duplex" in (desc or "").lower()
    if is_strata is False and (strata_lot or strata_plan):
        ev.append("flag_conflict:is_strataN_but_strata_lot")
    if strata_ev and unit_ev:
        ev += ["strata_or_lot", "unit_id"]
        if pid:
            ev.append("pid")
        if has_strata_neighbor:
            return "DUPLEX_SPLIT_CANDIDATE", "CANDIDATE", \
                "|".join(ev) + "|same_parcel_neighbor_sale"
        return ("DUPLEX_HALF_VERIFIED" if pid
                else "DUPLEX_HALF"), \
            "VERIFIED" if pid else "CANDIDATE", "|".join(ev)
    if is_strata is False and desc_duplex and not strata_ev:
        ev += ["non_strata", "desc_duplex"]
        if pid:
            ev.append("pid")
            return "DUPLEX_WHOLE_VERIFIED", "VERIFIED", \
                "|".join(ev)
        return "DUPLEX_WHOLE", "CANDIDATE", "|".join(ev)
    return "DUPLEX_SCOPE_UNKNOWN", "CANDIDATE", \
        "|".join(ev) or "strata_state_unknown_or_no_unit_ev"


def mpx_flags(unit_ev, project_ev, sale_ev):
    """MPX 三独立判断:任一不满足即 CANDIDATE。
    unit_ev:    strata + (unit_number|strata_lot) + pid
    project_ev: desc 命中 MULTI_DESC(3-6户 multiplex 项目类型)
    sale_ev:    多源核实 或 pid+税表回连确认
    返回 dict 便于核销表逐列审计。"""
    return {"UNIT_IDENTITY_VERIFIED": bool(unit_ev),
            "PROJECT_TYPE_VERIFIED": bool(project_ev),
            "SALE_EVENT_VERIFIED": bool(sale_ev),
            "MPX_VERIFIED": bool(unit_ev and project_ev and sale_ev)}


# ============ 房龄证据分级 ============
AGE_TIERS = ("AGE_EXACT_PID", "AGE_FOLIO_CONFIRMED",
             "AGE_UNIT_CONFIRMED", "AGE_ADDRESS_INFERRED",
             "AGE_CONSENSUS_ONLY", "AGE_UNKNOWN",
             "AGE_TEMPORAL_CONFLICT")
STRICT_TIERS = ("AGE_EXACT_PID", "AGE_FOLIO_CONFIRMED",
                "AGE_UNIT_CONFIRMED")


def resolve_age(sale_year, record_pid, tax_by_pid, tax_link,
                native_yb, has_unit_ev=False, has_roll_ev=False):
    """返回 (yb, age_tier)。
    tax_by_pid: pid -> {year: taxrow};tax_link 由管线侧产出
    {status, n, yb, age_tier}。"""
    sy = int(sale_year)

    def pick(hist):
        """hist=[(yb,tax_year)] → (yb, conflict)。房龄取成交当时
        状态:优先 ≤ 成交年的最大 yb;全部晚于成交年 = 重建污染。"""
        ok = [h[0] for h in hist if h[0] <= sy]
        if ok:
            return max(ok), False
        if hist:
            return min(h[0] for h in hist), True
        return None, False

    # 1) 记录原生 pid 直查税表 → EXACT_PID
    if record_pid and record_pid in tax_by_pid:
        hist = [(int(t["year_built"]), int(y))
                for y, t in tax_by_pid[record_pid].items()
                if str(t.get("year_built") or "").isdigit()]
        yb, conf = pick(hist)
        if yb:
            return yb, "AGE_TEMPORAL_CONFLICT" if conf \
                else "AGE_EXACT_PID"
    # 2) 记录自带 yb(BCA enriched/comps 评估口径)
    #    FOLIO_CONFIRMED 只给有真实 roll/folio 关联的记录
    #    (bca_evbc_oid / tax folio);无关联仅地址推断,不抬级
    if native_yb and str(native_yb).isdigit():
        yb = int(native_yb)
        if yb > sy:
            return yb, "AGE_TEMPORAL_CONFLICT"
        if record_pid:
            return yb, "AGE_EXACT_PID"
        if has_roll_ev:
            return yb, "AGE_FOLIO_CONFIRMED"
        if has_unit_ev:
            return yb, "AGE_UNIT_CONFIRMED"
        return yb, "AGE_ADDRESS_INFERRED"
    # 3) tax_link 回连的 pid:跨年度快照取 yb(命中行当列
    #    可能为空,不能只看单行),证据等级沿用 link 的 tier
    if tax_link and tax_link.get("pid"):
        lp = tax_link["pid"]
        hist = [(int(t["year_built"]), int(y))
                for y, t in tax_by_pid.get(lp, {}).items()
                if str(t.get("year_built") or "").isdigit()]
        yb, conf = pick(hist)
        if yb:
            return yb, "AGE_TEMPORAL_CONFLICT" if conf else \
                tax_link.get("age_tier", "AGE_ADDRESS_INFERRED")
    # 4) 共识 yb(无 pid 可用)
    if tax_link and str(tax_link.get("yb") or "").isdigit():
        yb = int(tax_link["yb"])
        if yb > sy:
            return yb, "AGE_TEMPORAL_CONFLICT"
        return yb, tax_link.get("age_tier", "AGE_ADDRESS_INFERRED")
    return None, "AGE_UNKNOWN"
