#!/usr/bin/env python3
"""空间+产权身份匹配 — 纯函数。

分层结果(七级):
  EXACT_PID_UNIT               pid 相同且单元一致
  EXACT_PARCEL                 同 folio/land_coordinate/母 parcel,
                               单元身份未确认
  SPATIAL_ADDRESS_CORROBORATED 坐标邻近 + 规范化地址一致
  SPATIAL_CANDIDATE_ONLY       仅坐标邻近 → 候选,禁止自动认定
  ADDRESS_ONLY                 仅规范化地址一致
  IDENTITY_CONFLICT            产权/单元/空间证据互相矛盾
  UNMATCHED

坐标只用于发现候选;身份确认要 pid/folio/plan/单元等产权证据。
半径随地块尺寸缩放,不设统一距离。
"""
import math

R_EARTH = 6_371_000.0


def haversine_m(a_lat, a_lng, b_lat, b_lng):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) \
        * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def radius_m(lot_a_sqft=None, lot_b_sqft=None):
    """候选半径:25m 基底,按大地块放宽(上限 80m)。
    lot_sqft→m² 后取平方根×0.8 近似地块边长。"""
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
    """~100m 网格桶用于邻近搜索。"""
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
    ua, ub = a.get("unit_number"), b.get("unit_number")
    if ua and ub:
        return ua == ub
    return None                      # 一方/双方无单元号:未定


def match(a, b):
    """a/b: dict(pid, folio, land_coordinate, strata_plan, strata_lot,
    unit_number, base_address, normalized_address, lat, lng, lot_sqft)。
    返回 {tier, reasons, dist_m}。"""
    reasons = []
    pid_a, pid_b = (a.get("pid") or "").strip(), (b.get("pid") or "").strip()
    fol_a, fol_b = (a.get("folio") or "").strip(), \
        (b.get("folio") or "").strip()
    lc_a, lc_b = (a.get("land_coordinate") or "").strip(), \
        (b.get("land_coordinate") or "").strip()
    pl_a, pl_b = a.get("strata_plan"), b.get("strata_plan")
    lt_a, lt_b = a.get("strata_lot"), b.get("strata_lot")

    dist = None
    if None not in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        dist = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
    rad = radius_m(a.get("lot_sqft"), b.get("lot_sqft"))
    near = dist is not None and dist <= rad

    # --- 冲突优先判定 ---
    if pid_a and pid_b and pid_a == pid_b:
        su = _same_unit(a, b)
        if su is False:
            return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                    "reasons": ["same_pid_diff_unit_claim"]}
        if dist is not None and dist > max(200, rad * 4):
            return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                    "reasons": ["same_pid_far_coords"]}
        reasons.append("pid_match")
        return {"tier": "EXACT_PID_UNIT", "dist_m": dist,
                "reasons": reasons}
    if pl_a and pl_b and pl_a == pl_b and lt_a and lt_a == lt_b:
        return {"tier": "EXACT_PID_UNIT", "dist_m": dist,
                "reasons": ["strata_plan_lot_match"]}
    # 同址+同单元号但 pid 冲突 = 数据矛盾
    if pid_a and pid_b and pid_a != pid_b \
            and a.get("base_address") == b.get("base_address") \
            and _same_unit(a, b) is True:
        return {"tier": "IDENTITY_CONFLICT", "dist_m": dist,
                "reasons": ["same_unit_diff_pid"]}

    # --- parcel 级 ---
    if fol_a and fol_b and fol_a == fol_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "reasons": ["folio_match"],
                "unit_same": _same_unit(a, b)}
    if lc_a and lc_b and lc_a == lc_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "reasons": ["land_coordinate_match"],
                "unit_same": _same_unit(a, b)}
    if pl_a and pl_b and pl_a == pl_b:
        return {"tier": "EXACT_PARCEL", "dist_m": dist,
                "reasons": ["strata_plan_match"],
                "unit_same": _same_unit(a, b)}

    # --- 空间+地址 ---
    same_base = a.get("base_address") and \
        a["base_address"] == b.get("base_address")
    if near and same_base:
        return {"tier": "SPATIAL_ADDRESS_CORROBORATED",
                "dist_m": dist,
                "reasons": ["near_coords", "base_addr_match"],
                "unit_same": _same_unit(a, b)}
    if near:
        return {"tier": "SPATIAL_CANDIDATE_ONLY", "dist_m": dist,
                "reasons": ["near_coords_only"],
                "unit_same": _same_unit(a, b)}
    if same_base:
        return {"tier": "ADDRESS_ONLY", "dist_m": dist,
                "reasons": ["base_addr_match"],
                "unit_same": _same_unit(a, b)}
    return {"tier": "UNMATCHED", "dist_m": dist, "reasons": []}


# ---------- 事务去重(成交事件层) ----------
def same_sale_event(a, b, ident_tier, day_tol=60, price_tol=0.02):
    """同一身份 + 合同日 vs 登记日合理差 → 同一笔成交。
    身份需 ≥SPATIAL_ADDRESS_CORROBORATED 且单元一致/无冲突;
    价差 ≤2%、日期差 ≤60d(BCA 登记滞后于合同)。"""
    if ident_tier in ("SPATIAL_CANDIDATE_ONLY", "ADDRESS_ONLY",
                      "UNMATCHED"):
        return False
    if ident_tier == "IDENTITY_CONFLICT":
        return False
    if a.get("sale_date") and b.get("sale_date"):
        d1, d2 = a["sale_date"], b["sale_date"]
        dd = abs((d1.toordinal() if hasattr(d1, "toordinal")
                  else _ord(d1)) -
                 (d2.toordinal() if hasattr(d2, "toordinal")
                  else _ord(d2)))
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
    return datetime.date.fromisoformat(str(iso)[:10]).toordinal()
