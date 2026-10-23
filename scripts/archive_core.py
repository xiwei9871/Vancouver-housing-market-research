#!/usr/bin/env python3
"""Sales Archive 纯函数核心 — 无文件/网络依赖,干净 checkout 可测。"""

AGE_BANDS = [
    (0, 3, "AGE_00_03"), (4, 10, "AGE_04_10"), (11, 20, "AGE_11_20"),
    (21, 30, "AGE_21_30"), (31, 50, "AGE_31_50"), (51, 10_000,
                                                    "AGE_51_PLUS")]


def age_band_at_sale(year_built: int | None, sale_year: int):
    """返回 (band, age)。yb 缺失→AGE_UNKNOWN;yb>成交年→AGE_TEMPORAL_CONFLICT。
    房龄为年度估算(只有建造年份无竣工日期)。"""
    if not year_built:
        return "AGE_UNKNOWN", None
    if year_built > sale_year:
        return "AGE_TEMPORAL_CONFLICT", None
    age = sale_year - year_built
    for lo, hi, name in AGE_BANDS:
        if lo <= age <= hi:
            return name, age
    return "AGE_UNKNOWN", age


def pick_yb_at_sale(candidates, sale_year):
    """candidates: [(yb, source)]。规则:历史快照优先(tax2023);
    取 ≤ 成交年的最近快照;全部 > 成交年 → 取最小并标冲突。"""
    valid = [(int(y), s) for y, s in candidates
             if y and str(y).isdigit()]
    if not valid:
        return None, "", False
    hist = [(y, s) for y, s in valid if y <= sale_year]
    if hist:
        pref = sorted(hist, key=lambda x:
                      (x[1] != "tax2023", -x[0]))
        return pref[0][0], pref[0][1], False
    y, s = min(valid)
    return y, s, True


def tx_key(pid: str, norm_addr_unit: str, sale_date: str,
           sale_price: float | int | None = None):
    """交易实体键:pid+date 优先;无 pid 用含单元号的地址+date。
    同 key 价差>1.5% 视为不同单元交易,由调用方加后缀。"""
    pid = (pid or "").strip()
    base = ("PID|" + pid) if pid else ("ADDR|" + norm_addr_unit)
    return base + "|" + (sale_date or "")


def same_tx_price(p1, p2, tol=0.015):
    """同一键下价格差≤1.5% 视为同一笔交易的多源记录。"""
    if not p1 or not p2:
        return True
    return abs(float(p1) - float(p2)) / max(float(p1), float(p2)) <= tol


def arc_desc_product(desc: str):
    d = (desc or "").lower()
    if "apartment" in d or "condominium" in d:
        return "CONDO"
    if "townhouse" in d or "row house" in d:
        return "TOWNHOUSE"
    if "duplex" in d:
        return "DUPLEX"
    if "multiple residence" in d or "multi-family" in d \
            or "multi family" in d:
        return "MULTIPLEX_WHOLE"
    if "warehouse" in d or "commercial" in d or "store" in d:
        return "OTHER"
    if "house" in d or "dwelling" in d or "sty" in d:
        return "HOUSE"
    if "vacant" in d and "land" in d:
        return "OTHER"
    return "UNKNOWN"
