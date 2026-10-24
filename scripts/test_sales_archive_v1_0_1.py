#!/usr/bin/env python3
"""V1.0.1 合成回归:地址解析 / 空间身份分层 / 防误合并 / 去重口径。
纯 fixture,无私有数据;真 watchlist 不在此测。
"""
import sys, datetime
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                              # noqa: E402
import spatial_identity as SI                          # noqa: E402
import archive_core as AC                              # noqa: E402

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


def rec(**kw):
    d = {"pid": "", "folio": "", "land_coordinate": "",
         "strata_plan": "", "strata_lot": "", "unit_number": "",
         "base_address": "", "normalized_address": "",
         "lat": None, "lng": None, "lot_sqft": None}
    d.update(kw)
    return d


# ============ A. 地址解析 ============
p = AN.parse("614 E 19TH AVENUE")
chk("前置方向 E→后置", p["base_address"] == "614 19TH AVE E"
    and p["street_direction"] == "E")
p = AN.parse("614 19TH AVE E")
chk("后置方向一致", p["base_address"] == "614 19TH AVE E")
p = AN.parse("204-3189 CAMOSUN ST")
chk("单元前缀", p["unit_number"] == "204"
    and p["base_address"] == "3189 CAMOSUN ST")
p = AN.parse("311 KENT AVENUE SOUTH E")
chk("SOUTH 为街名成分不被吞", "KENT" in p["base_address"]
    and p["street_type"] == "AVE")
p = AN.parse("105 W 1ST ST")
chk("序数 ST 不拆:1ST 是街名", p["street_name"] == "1ST"
    and p["street_type"] == "ST")
p = AN.parse("8888 OAK STREET")
chk("STREET→ST", p["base_address"] == "8888 OAK ST")
p = AN.parse("#204 3189 CAMOSUN STREET")
chk("#204 单元", p["unit_number"] == "204")
plan, lot = AN.strata_plan_from_legal(
    "STRATA LOT 5 DISTRICT LOT 330 PLAN EPS9731")
chk("legal→plan/lot", plan == "EPS9731" and lot == "5")

# ============ B. 身份分层 ============
# 同 condo 楼两 unit 不同 pid:同 folio/parcel → EXACT_PARCEL,非同户
a = rec(pid="001", unit_number="204", base_address="3189 CAMOSUN ST",
        lat=49.2, lng=-123.1)
b = rec(pid="002", unit_number="305", base_address="3189 CAMOSUN ST",
        lat=49.2, lng=-123.1)
m = SI.match(a, b)
chk("同楼不同pid不同unit → 非单元合并", m["tier"] != "EXACT_PID_UNIT")
# 同 pid 同地址
a2 = rec(pid="001", base_address="3189 CAMOSUN ST")
b2 = rec(pid="001", base_address="3189 CAMOSUN ST", lat=49.2,
         lng=-123.1)
chk("同pid → EXACT_PID_UNIT", SI.match(a2, b2)["tier"] ==
    "EXACT_PID_UNIT")
# 同 pid 但单元号声称不同 → 冲突
c = rec(pid="001", unit_number="204", base_address="3189 CAMOSUN ST")
d = rec(pid="001", unit_number="205", base_address="3189 CAMOSUN ST")
chk("同pid不同unit声称 → CONFLICT",
    SI.match(c, d)["tier"] == "IDENTITY_CONFLICT")
# 同址同 unit 但 pid 不同 → 数据冲突
c2 = rec(pid="001", unit_number="204", base_address="3189 CAMOSUN ST")
d2 = rec(pid="009", unit_number="204", base_address="3189 CAMOSUN ST")
chk("同址同unit不同pid → CONFLICT",
    SI.match(c2, d2)["tier"] == "IDENTITY_CONFLICT")
# 仅坐标邻近(相邻小地块)→ 候选不合并
a3 = rec(base_address="100 X ST", lat=49.2600, lng=-123.1000,
         lot_sqft=3300)
b3 = rec(base_address="104 X ST", lat=49.2601, lng=-123.1001,
         lot_sqft=3300)
m = SI.match(a3, b3)
chk("邻地仅坐标近 → SPATIAL_CANDIDATE_ONLY",
    m["tier"] == "SPATIAL_CANDIDATE_ONLY")
chk("CANDIDATE 不算身份", not SI.same_sale_event(
    {"sale_date": "2025-01-01", "sale_price": 1e6},
    {"sale_date": "2025-01-05", "sale_price": 1e6},
    "SPATIAL_CANDIDATE_ONLY"))
# 空间+地址互证
a4 = rec(base_address="100 X ST", lat=49.2600, lng=-123.1000)
b4 = rec(base_address="100 X ST", lat=49.26005, lng=-123.10005)
chk("近+同址 → CORROBORATED",
    SI.match(a4, b4)["tier"] == "SPATIAL_ADDRESS_CORROBORATED")
# 仅同址 → ADDRESS_ONLY
m = SI.match(rec(base_address="100 X ST"),
             rec(base_address="100 X ST"))
chk("仅同址 → ADDRESS_ONLY", m["tier"] == "ADDRESS_ONLY")
chk("ADDRESS_ONLY 不自动认定同交易", not SI.same_sale_event(
    {"sale_date": "2025-01-01", "sale_price": 1e6},
    {"sale_date": "2025-01-03", "sale_price": 1e6},
    "ADDRESS_ONLY"))
# 无证据 → UNMATCHED
chk("无关 → UNMATCHED", SI.match(
    rec(base_address="100 X ST"),
    rec(base_address="999 Y AVE")).get("tier") == "UNMATCHED")

# ============ C. 成交事件去重 ============
# 同一房产两次真交易(2023/2025)不合并
a5 = {"sale_date": "2023-03-01", "sale_price": 1.5e6}
b5 = {"sale_date": "2025-06-01", "sale_price": 1.5e6}
chk("同年房产两次真交易不合", not SI.same_sale_event(
    a5, b5, "EXACT_PID_UNIT"))
# MLS 合同日 vs BCA 登记日(45d 内)→ 同一笔
chk("合同/登记时间差合并", SI.same_sale_event(
    {"sale_date": "2025-03-01", "sale_price": 1.5e6},
    {"sale_date": "2025-04-10", "sale_price": 1.5e6},
    "EXACT_PID_UNIT"))
# 日期差 90d → 不合并
chk("超窗不合并", not SI.same_sale_event(
    {"sale_date": "2025-01-01", "sale_price": 1.5e6},
    {"sale_date": "2025-04-15", "sale_price": 1.5e6},
    "EXACT_PID_UNIT"))
# 同日期差价差大 → 不合并
chk("价差>2%不合", not SI.same_sale_event(
    {"sale_date": "2025-03-01", "sale_price": 1.5e6},
    {"sale_date": "2025-03-01", "sale_price": 1.9e6},
    "EXACT_PID_UNIT"))
# strata 同 plan+lot → 单元级
a6 = rec(strata_plan="EPS9731", strata_lot="5")
b6 = rec(strata_plan="EPS9731", strata_lot="5")
chk("plan+lot → EXACT_PID_UNIT",
    SI.match(a6, b6)["tier"] == "EXACT_PID_UNIT")
# duplex 两半户同 parcel 不同 pid:EXACT_PARCEL,非单元合并
a7 = rec(pid="P1", folio="F777", base_address="500 SLOCAN ST",
         unit_number="")
b7 = rec(pid="P2", folio="F777", base_address="500 SLOCAN ST",
         unit_number="")
m = SI.match(a7, b7)
chk("同folio不同pid → EXACT_PARCEL(非同户)",
    m["tier"] == "EXACT_PARCEL")

# ============ D. 房龄 ============
band, _ = AC.age_band_at_sale(2022, 2025)
chk("2022建2025售→AGE_00_03", band == "AGE_00_03")
band, _ = AC.age_band_at_sale(1980, 2025)
chk("1980建2025售→AGE_31_50", band == "AGE_31_50")
band, _ = AC.age_band_at_sale(None, 2025)
chk("无yb→AGE_UNKNOWN", band == "AGE_UNKNOWN")
band, _ = AC.age_band_at_sale(2027, 2024)
chk("yb>成交年→CONFLICT", band == "AGE_TEMPORAL_CONFLICT")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
