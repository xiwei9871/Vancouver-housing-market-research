#!/usr/bin/env python3
"""空间+产权身份匹配 — 纯函数(V1.0.2)。

七级判定:
  EXACT_PID_UNIT               pid 相同且单元一致 / strata plan+lot 相同
  EXACT_PARCEL                 同 folio / land_coordinate / legal plan+lot /
                               strata plan(仅楼级) —— 同一地块/建筑,
                               不等于同一住宅单元
  SPATIAL_ADDRESS_CORROBORATED 坐标邻近 + 规范化基地址一致
  SPATIAL_CANDIDATE_ONLY       仅坐标邻近 → 候选,禁止自动认定
  ADDRESS_ONLY                 仅规范化基地址一致
  IDENTITY_CONFLICT            产权/单元/空间证据互相矛盾
  UNMATCHED

记录 schema(match 输入契约,缺键即缺陷不是空值):
  pid, folio, land_coordinate, strata_plan, strata_lot,
  legal_plan, legal_lot, unit_number, base_address,
  normalized_address, lat, lng, lot_sqft, is_strata
坐标只发现候选;pid/folio/plan/单元确认身份。
EXACT_PARCEL 永不直接合并为同一住宅单元 —— parcel 层只进
parcel 簇;unit 合并需 unit_mergeable() 通过。
"""
import math

R_EARTH = 6_371_000.0

SCHEMA = ("pid", "folio", "land_coordinate", "strata_plan",
          "strata_lot", "legal_plan", "legal_lot", "unit_number",
          "qualifier", "base_address", "normalized_address",
          "lat", "lng", "lot_sqft", "is_strata")


def validate_record(r, ctx=""):
    """schema 校验:返回缺陷列表;缺失字段是缺陷,不静默当空串。"""
    issues = []
    for k in SCHEMA:
        if k not in r:
            issues.append(f"{ctx}missing:{k}")
    for k in ("pid", "folio", "land_coordinate", "strata_plan",
              "strata_lot", "legal_plan", "legal_lot", "unit_number",
              "base_address"):
        if k in r and r[k] is not None and not isinstance(r[k], str):
            issues.append(f"{ctx}type:{k}")
    return issues


def haversine_m(a_lat, a_lng, b_lat, b_lng):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) \
        * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def radius_m(lot_a_sqft=None, lot_b_sqft=None):
    """候选半径:25m 基底,按大地块放宽(上限 80m)。"""
    r = 25.0
    for sqft in (lot_a_sqft, lot_b_sqft):
        if sqft:
            try:
                m2 = float(sqft) * 0.092903
                r = max(r, min(80.0, 0.8 * math.sqrt(m2)))
            except (TypeError, ValueError):
                pass
    return r


def grid_key(lat, lng, cell_m=100):
    if lat is None or lng is None:
        return None
    return (round(lat * 111_320 / cell_m),
            round(lng * 88_000 / cell_m))


def neighbor_cells(lat, lng, cell_m=100):
    k = grid_key(lat, lng, cell_m)
    if not k:
        return []
    return [(k[0] + dx, k[1] + dy)
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def _same_unit(a, b):
    """单元一致性:True/False/None(未定)。
     qualifier(REAR/FRONT/LANEWAY…)视作单元成分:双方都有且不同
    即不同单元;单方缺失不判。"""
    ua, ub = a.get("unit_number"), b.get("unit_number")
    if ua and ub:
        return ua == ub
    qa, qb = a.get("qualifier"), b.get("qualifier")
    if qa and qb and qa != qb:
        return False
    return None                      # 未定


def _strata(a):
    return a.get("is_strata") is True or bool(a.get("strata_plan")) \
        or bool(a.get("strata_lot"))


def _non_strata_known(a):
    """显式已知非 strata(未知不算非 strata)。"""
    return a.get("is_strata") is False and not _strata(a)


def match(a, b):
    """a/b 必须符合 SCHEMA;返回 {tier, reasons, dist_m, unit_same}。"""
    reasons = []
    pid_a, pid_b = (a.get("pid") or "").strip(), (b.get("pid") or "").strip()
    fol_a, fol_b = (a.get("folio") or "").strip(), \
        (b.get("folio") or "").strip()
    lc_a, lc_b = (a.get("land_coordinate") or "").strip(), \
        (b.get("land_coordinate") or "").strip()
    spl_a, spl_b = a.get("strata_plan"), b.get("strata_plan")
    slt_a, slt_b = a.get("strata_lot"), b.get("strata_lot")
    lpl_a, lpl_b = a.get("legal_plan"), b.get("legal_plan")
    llt_a, llt_b = a.get("legal_lot"), b.get("legal_lot")

    dist = None
    if None not in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        dist = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
    rad = radius_m(a.get("lot_sqft"), b.get("lot_sqft"))
    near = dist is not None and dist <= rad
    same_base = bool(a.get("base_address")) and \
        a["base_address"] == b.get("base_address")
    su = _same_unit(a, b)

    # --- 冲突优先 ---
    if pid_a and pid_b and pid_a == pid_b:
        if su is False:
            return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                    "unit_same": su,
                    "reasons": ["same_pid_diff_unit_claim"]}
        if dist is not None and dist > max(200, rad * 4):
            return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                    "unit_same": su,
                    "reasons": ["same_pid_far_coords"]}
        return {"tier": "EXACT_PID_UNIT", "dist_m": dist,
                "unit_same": su, "reasons": ["pid_match"]}
    if spl_a and spl_b and spl_a == spl_b and slt_a and slt_a == slt_b:
        return {"tier": "EXACT_PID_UNIT", "dist_m": dist,
                "unit_same": True,
                "reasons": ["strata_plan_lot_match"]}
    if pid_a and pid_b and pid_a != pid_b and same_base and su is True:
        return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                "unit_same": su,
                "reasons": ["same_unit_diff_pid"]}
    # strata lot 冲突:同 plan 不同 lot → 同楼不同单元,不得合并
    if spl_a and spl_b and spl_a == spl_b and slt_a and slt_b \
            and slt_a != slt_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "unit_same": False,
                "reasons": ["same_strata_plan_diff_lot"]}

    # --- parcel/建筑级 ---
    if fol_a and fol_b and fol_a == fol_b:
        # CoV folio = assessment roll:strata 下每单元独立 roll
        # → strata 同 folio 实为单元级;非 strata 为地块级
        tier = "EXACT_PID_UNIT" if _strata(a) or _strata(b) \
            else "EXACT_PARCEL"
        return {"tier": tier, "dist_m": dist, "unit_same": su,
                "reasons": ["folio_match" + ("_strata_roll"
                             if tier == "EXACT_PID_UNIT" else "")]}
    if lc_a and lc_b and lc_a == lc_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist, "unit_same": su,
                "reasons": ["land_coordinate_match"]}
    if lpl_a and lpl_b and lpl_a == lpl_b and llt_a and llt_a == llt_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "unit_same": su,
                "reasons": ["legal_plan_lot_match"]}
    if spl_a and spl_b and spl_a == spl_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "unit_same": su,
                "reasons": ["strata_plan_building_match"]}

    # --- 空间+地址 ---
    if near and same_base:
        return {"tier": "SPATIAL_ADDRESS_CORROBORATED",
                "dist_m": dist, "unit_same": su,
                "reasons": ["near_coords", "base_addr_match"]}
    if near:
        return {"tier": "SPATIAL_CANDIDATE_ONLY", "dist_m": dist,
                "unit_same": su, "reasons": ["near_coords_only"]}
    if same_base:
        return {"tier": "ADDRESS_ONLY", "dist_m": dist,
                "unit_same": su, "reasons": ["base_addr_match"]}
    return {"tier": "UNMATCHED", "dist_m": dist,
            "unit_same": su, "reasons": []}


def unit_mergeable(a, b, m):
    """该 pair 是否可合并为同一住宅单元。
    EXACT_PARCEL 本身不证单元;仅当:
      - 单元号一致(unit_same True),或
      - 双方均为非 strata 单户地块且单元号未冲突(strata 楼内
        同址不同 roll/lot 不得靠'都没有单元号'合并)。"""
    t = m["tier"]
    if t == "EXACT_PID_UNIT":
        return True
    if m.get("unit_same") is True:
        return t in ("EXACT_PARCEL", "SPATIAL_ADDRESS_CORROBORATED",
                     "ADDRESS_ONLY")
    if t in ("EXACT_PARCEL", "SPATIAL_ADDRESS_CORROBORATED",
             "ADDRESS_ONLY") and m.get("unit_same") is None:
        # 未知 strata 态不推定单户:防止 condo 楼内无单元号
        # 记录被同址同事件误并
        return _non_strata_known(a) and _non_strata_known(b)
    return False


# ---------- 事务去重 ----------
def same_sale_event(a, b, ident_tier, day_tol=60, price_tol=0.02,
                    unit_ok=True):
    """同一已确认单元 + 合同日 vs 登记日合理差 → 同一笔成交。
    unit_ok=False(parcel 级/候选)时一律不合并。"""
    if not unit_ok:
        return False
    if ident_tier in ("SPATIAL_CANDIDATE_ONLY", "ADDRESS_ONLY",
                      "UNMATCHED", "IDENTITY_CONFLICT"):
        return False
    if a.get("sale_date") and b.get("sale_date"):
        dd = abs(_ord(a["sale_date"]) - _ord(b["sale_date"]))
        if dd > day_tol:
            return False
    pa, pb = a.get("sale_price"), b.get("sale_price")
    if pa and pb:
        if abs(pa - pb) / max(pa, pb) > price_tol:
            return False
    elif bool(pa) != bool(pb):
        return False
    return True


def _ord(iso):
    import datetime
    d = iso if isinstance(iso, datetime.date) else \
        datetime.date.fromisoformat(str(iso)[:10])
    return d.toordinal()
