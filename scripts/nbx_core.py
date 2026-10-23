#!/usr/bin/env python3
"""newbuild 证据管线的纯函数核心 — 不读本地文件,可独立被测试。
V1.1/V1.2 共享;测试在无私有数据的干净 checkout 上即可运行。
"""
import re

NEWYB = 2018
EXCL = {"BUILT_AFTER_SALE", "LAND_SALE_SIGNATURE", "POSSIBLE_PRESALE"}
ANCHOR_EXCLUDE = {"1185 17TH AVE E", "2549 KENT AVENUE NORTH E",
                  "78 MALTA PL"}
RC_LAT_SPLIT = 49.243
BANDS = [(0, 1600), (1600, 2000), (2000, 2400), (2400, 2800),
         (2800, 3400), (3400, 99999)]

# 显式产品映射 — 未知值一律 OTHER,禁止兜底进 MPX
CLASS_MAP = {
    "HOUSE_DETACHED": ("HOUSE", "WHOLE"),
    "HOUSE_SUSPECT_STRATA": ("HOUSE", "WHOLE_SUSPECT"),
    "DUPLEX_HALF_STRATA": ("DUPLEX", "HALF"),
    "DUPLEX_WHOLE_BLDG": ("DUPLEX", "WHOLE"),
    "MULTIPLEX_3_4_CANDIDATE": ("MPX_CAND", "UNIT"),
    "MULTIPLEX_WHOLE_BLDG": ("MPX_WHOLE", "WHOLE"),
    "STRATA_UNIT_UNKNOWN": ("STRATA_UNKNOWN", "UNIT"),
    "OTHER": ("OTHER", "?"),
}


def num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "")) \
            if str(x or "").strip() else None
    except (TypeError, ValueError):
        return None


def norm_addr(a):
    a = (a or "").upper().split(",")[0].replace(" VANCOUVER", "").strip()
    a = a.replace(" AVENUE", " AVE").replace(" STREET", " ST") \
         .replace(" BOULEVARD", " BLVD").replace(" DRIVE", " DR") \
         .replace(" ROAD", " RD").replace(" PLACE", " PL")
    a = re.sub(r"\s+", " ", a).strip()
    m = re.match(r"^(\d+-?\d*)\s+([EWNS])\s+(\d+(?:ST|ND|RD|TH))\s+"
                 r"(AVE|ST|BLVD|DR|RD|PL)$", a)
    if m:
        a = f"{m.group(1)} {m.group(3)} {m.group(4)} {m.group(2)}"
    return a


def base_addr(a):
    """'204-3189 CAMOSUN ST' → '3189 CAMOSUN ST'(去单元前缀)"""
    return re.sub(r"^\d+-", "", norm_addr(a))


def unit_prefix(a):
    """提取单元号:'204-3189 CAMOSUN'→'204';无→None"""
    m = re.match(r"^(\d+)-\d+\s", (a or "").strip().upper())
    return m.group(1) if m else None


def rc_split(official_area, lat):
    if official_area != "Renfrew-Collingwood":
        return official_area or "?"
    try:
        lat = float(lat or 0)
    except (TypeError, ValueError):
        lat = 0.0
    if not lat:
        return "Renfrew-Collingwood(未分)"
    return "Renfrew(北)" if lat >= RC_LAT_SPLIT else "Collingwood(南)"


def classify_permit(specuse, desc):
    s = (specuse or "").lower(); d = (desc or "").lower()
    tags = set()
    if "secondary suite" in s or "sec suite" in s or "secondary suite" in d:
        tags.add("SEC_SUITE")
    if "laneway" in s or "laneway" in d:
        tags.add("LANEWAY")
    if "custom" in d:
        tags.add("CUSTOM")
    if "infill" in s:
        tags.add("INFILL")
    if "lock" in s and "off" in s:
        tags.add("LOCKOFF")
    if "townhouse" in s or "townhouse" in d or "row house" in d:
        return "TOWNHOUSE", tags
    if "multiple dwelling" in s or "multiplex" in d or "multiple dwelling" in d:
        return "MPX", tags
    if "duplex" in s or "two-family" in d or "2-family" in d:
        return "DUPLEX", tags
    if "laneway" in s:
        return "LANEWAY", tags
    if "single detached" in s or "one-family" in d or "1-family" in d \
            or "sfd" in d or "1fd" in s:
        return "HOUSE", tags
    return "OTHER", tags


def parse_units(desc):
    W2N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "single": 1}
    d = (desc or "").lower()
    units = 0
    for m in re.finditer(r"(\w+)[-\s]unit", d):
        g = m.group(1)
        units += int(g) if g.isdigit() else W2N.get(g, 0)
    if units:
        return units
    for m in re.finditer(r"(\w+)-family", d):
        g = m.group(1)
        return int(g) if g.isdigit() else W2N.get(g, 0)
    m = re.search(r"(\d+)\s*sfd", d)
    return int(m.group(1)) if m else None


def desc_verdict(desc):
    """BCA 建筑形态 — 仅 bca_building_form,不等于开发项目类型"""
    d = (desc or "").lower()
    if not d:
        return "UNRESOLVED", "NO_DESC"
    if any(k in d for k in ("warehouse", "office", "commercial", "comm & res",
                            "retail", "hotel", "studio", "marine", "parking",
                            "rooming", "gen comm", "storage", "artist")):
        return "OTHER", "BCA_DESC_NONRES"
    if "hi-rise" in d or "high rise" in d or "apartment" in d \
            or "walk up" in d or "condo" in d or d.startswith("apt"):
        return "APARTMENT", "BCA_DESC_APARTMENT"
    if "townhouse" in d or "row house" in d:
        return "TOWNHOUSE", "BCA_DESC_TOWNHOUSE"
    if "duplex" in d or "semi-detached" in d or "two family" in d:
        return "CONFIRMED_DUPLEX", "BCA_DESC_DUPLEX"
    if "triplex" in d or "fourplex" in d or "multiple res" in d \
            or "multiplex" in d or "multi-family" in d:
        return "CONFIRMED_MULTIPLEX", "BCA_DESC_MPX"
    if re.search(r"\bhouse\b", d) or "detached" in d or "sfd" in d:
        return "CONFIRMED_HOUSE", "BCA_DESC_HOUSE"
    return "UNRESOLVED", "DESC_AMBIGUOUS"


def zone_product(z):
    z = (z or "").upper()
    if z.startswith(("R1-1", "RS", "RA", "RE", "RF")):
        return "HOUSE_OR_MPX_R1"   # R1-1 2023-10 起允许多户
    if z.startswith(("RT", "RM-", "FM", "RR")):
        return "DUPLEX_OR_MPX"
    if z.startswith(("CD", "C")):
        return "CD"
    return "OTHER"


def band_lo(fa):
    for lo, hi in BANDS:
        if fa and lo <= fa < hi:
            return lo
    return None


def sale_tier(prod, price, sale_date, pid, asof, yb, fa, yb_src=None):
    """P2 四级:不可仅 pid+CONSISTENT 即 VERIFIED。"""
    prod_ok = prod in ("HOUSE", "DUPLEX", "MPX", "MPX_WHOLE")
    if not prod_ok or not (price and sale_date):
        return "UNVERIFIED"
    if not (pid or asof == "CONSISTENT"):
        return "UNVERIFIED"
    if (yb or 0) >= NEWYB and fa and asof not in EXCL:
        return "PARTIAL_EVIDENCE" if yb_src else "VERIFIED_COMPARABLE"
    return "PARTIAL_EVIDENCE"


def usable(rec):
    """统一可用口径:产品明确 + yb2018+ + fa + price + asof_ok"""
    return (rec["_prod"] in ("HOUSE", "DUPLEX", "MPX") and
            (rec["_yb"] or 0) >= NEWYB and rec["_fa"] and rec["_p"] and
            rec.get("asof_status") not in EXCL)
