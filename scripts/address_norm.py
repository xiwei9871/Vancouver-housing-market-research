#!/usr/bin/env python3
"""地址组件解析 — 纯函数,无私有数据依赖。

保留:unit_number / street_number(含 1/2 分数)/ street_name /
street_type / street_direction / municipality /
normalized_address / base_address。

规则:
- 方向词 E/W/N/S 既可前置("614 E 19TH AVENUE")也可后置
  ("614 19TH AVE E"),规范化统一为后置。
- "KENT AVENUE SOUTH E": SOUTH 是街名成分,E 是方向后缀。
- ST 只在独立成词时是 STREET 缩写;"1ST/2ND/3RD/4TH" 序数粘数字,
  不参与缩写替换(不拆 "41ST")。
- 单元号:前缀 "204-3189" / "#204" / "UNIT 204" / "STE 204" /
  "PH1-" 均抽出;laneway/front/rear 修饰保留为 qualifier。
"""
import re

TYPES = {"AVENUE": "AVE", "AVE": "AVE", "STREET": "ST", "ST": "ST",
         "BOULEVARD": "BLVD", "BLVD": "BLVD", "DRIVE": "DR", "DR": "DR",
         "ROAD": "RD", "RD": "RD", "PLACE": "PL", "PL": "PL",
         "CRESCENT": "CRES", "CRES": "CRES", "COURT": "CT", "CT": "CT",
         "LANE": "LANE", "WAY": "WAY", "TERRACE": "TER", "TER": "TER",
         "CIRCLE": "CIR", "CIR": "CIR", "BAY": "BAY", "MEWS": "MEWS",
         "WALK": "WALK", "PARKWAY": "PKWY", "PKWY": "PKWY",
         "HIGHWAY": "HWY", "HWY": "HWY", "SQUARE": "SQ", "SQ": "SQ",
         "GATE": "GATE", "GARDENS": "GDNS", "GDNS": "GDNS",
         "TRAIL": "TRL", "TRL": "TRL", "PLAZA": "PLZ", "PLZ": "PLZ",
         "HEIGHTS": "HTS", "HTS": "HTS", "VILLA": "VILLA",
         "GREEN": "GRN", "GRN": "GRN", "WYND": "WYND",
         "AV": "AVE"}
DIRS = {"E": "E", "W": "W", "N": "N", "S": "S",
        "EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S"}
QUALIFIERS = ("REAR", "FRONT", "LANEWAY", "LWH", "UPPER", "LOWER",
              "BASEMENT", "BSMT", "MAIN", "BACK")


def _clean(a):
    a = (a or "").upper().strip()
    a = a.split(",")[0]
    a = re.sub(r"\bVANCOUVER\b|\bBC\b|\bBRITISH COLUMBIA\b", "", a)
    a = re.sub(r"[#\.]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def parse(addr):
    """返回组件 dict;无法解析处返回 None 不猜。"""
    raw_a = (addr or "").upper()
    unit = qual = None
    # "#204 3189 X":# 先抽单元再清洗
    m = re.match(r"^#\s*([A-Z]*\d+[A-Z]*)\s+(.+)$", raw_a)
    if m:
        unit, raw_a = m.group(1), m.group(2)
    a = _clean(raw_a)
    # 单元前缀:"204-3189" / "204/3189";也支持 PH1-/TH2- 前缀
    m = re.match(r"^([A-Z]*\d+[A-Z]*)\s*[-/]\s*(\d.*)$", a)
    if m:
        unit, a = m.group(1), m.group(2)
    else:
        # "UNIT 204 3189 X" / "STE 204 3189 X" / "APT 204 ..."
        m = re.match(r"^(?:UNIT|STE|SUITE|APT|NO)\s+([A-Z]*\d+[A-Z]*)"
                     r"\s+(\d.*)$", a)
        if m:
            unit, a = m.group(1), m.group(2)
    toks = a.split()
    # 前后屋修饰:尾置 "REAR"/"FRONT"/"A"/"B" 单独标记
    if toks and toks[-1] in QUALIFIERS:
        qual = toks.pop()
    # 门牌号:首 token 数字(可含 1/2 分数或字母后缀如 1234A)
    number = None
    if toks and re.match(r"^\d+(?:\s?1/2)?[A-Z]?$", toks[0]):
        number = toks.pop(0)
        if toks and toks[0] == "1/2":
            number += " 1/2"; toks.pop(0)
    # 前置方向:"614 E 19TH AVENUE"
    dirn = None
    if toks and toks[0] in DIRS:
        dirn = DIRS[toks.pop(0)]
    # 街型:最后一个独立 TYPES 词(防 "MAIN ST" 中 ST 是类型)
    stype = None
    ti = None
    for i in range(len(toks) - 1, -1, -1):
        if toks[i] in TYPES:
            stype, ti = TYPES[toks[i]], i
            break
    if ti is not None:
        tail = toks[ti + 1:]
        name = " ".join(toks[:ti])
        toks = tail
    else:
        name = " ".join(toks)
        toks = []
    # 后置方向:"614 19TH AVE E"(取出后必须从残留移除)
    if toks and toks[0] in DIRS:
        dirn = dirn or DIRS[toks[0]]
        toks.pop(0)
    # 残留 token 归并回街名(如 "KENT AVENUE SOUTH E" 的 SOUTH)
    if toks:
        name = (name + " " + " ".join(toks)).strip()
    # canonical:number + name + type + dir
    parts = [p for p in (number, name, stype, dirn) if p]
    norm = " ".join(parts)
    return {"unit_number": unit, "street_number": number,
            "street_name": name or None, "street_type": stype,
            "street_direction": dirn, "qualifier": qual,
            "normalized_address": (f"{unit}-{norm}" if unit else norm),
            "base_address": norm}


def norm_addr(addr):
    return parse(addr)["normalized_address"] or _clean(addr)


def base_addr(addr):
    return parse(addr)["base_address"]


def unit_no(addr):
    return parse(addr)["unit_number"]


def civic_key(addr):
    """跨源对齐键:number+name+type+dir(忽略单元)。"""
    p = parse(addr)
    return p["base_address"]


def strata_plan_from_legal(legal):
    """'LOT 1 PLAN EPS1234' / 'STRATA LOT 5 DISTRICT LOT 330 PLAN VAS123'
    → ('EPS1234','1') 等;无→(None,None)"""
    t = (legal or "").upper()
    pm = re.search(r"PLAN\s+([A-Z]{0,4}\d+[A-Z]?)", t)
    plan = pm.group(1) if pm else None
    lm = re.search(r"(?:STRATA\s+LOT|LOT)\s+([0-9]+[A-Z]?)", t)
    return plan, (lm.group(1) if lm else None)
