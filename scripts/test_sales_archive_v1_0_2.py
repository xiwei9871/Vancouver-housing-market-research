#!/usr/bin/env python3
"""Sales Archive V1.0.2 集成回归 — 管线级(非纯 SI.match 单测)。

覆盖验收要求:
1) add_raw→normalize_record 产出的字段真正喂给 SI.match
   (schema 校验零缺陷 + 地址证据进入判定)
2) parcel 合并不导致不同 unit 合并(strata 楼两单元同址同事件
   → 候选重复,不自动合并)
3) union-find 传递后 pid/planlot 冲突仍拦截
4) MPX 候选不自动升级;vacant 永远不成 MPX;
   1909 CHARLES / 3122 MARINE 回归
5) 房龄证据分级;2023 成交不被 2026 重建 yb 污染
6) 守恒断言:产品/房龄/tax_link 与 canonical 一致
7) 真实数据双 subprocess 幂等(有输入文件时)

运行:python3 scripts/test_sales_archive_v1_0_2.py
"""
import sys, subprocess, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                              # noqa: E402
import spatial_identity as SI                          # noqa: E402
import archive_pipeline as AP                          # noqa: E402
import archive_core as AC                              # noqa: E402

R = Counter()
def chk(n, c):
    R["PASS" if c else "FAIL"] += 1
    print(("  ok " if c else "FAIL ") + n)


def mk(raw_id, src, **kw):
    rec, iss = AP.normalize_record(raw_id, src, raw_id, **kw)
    assert not iss, f"schema issues: {iss}"
    return rec


# ============ 1. schema 与字段喂给 ============
rec, iss = AP.normalize_record("T1", "comps_v2", "c1",
    address_raw="204-3189 CAMOSUN ST VANCOUVER",
    mls_raw_address="204-3189 CAMOSUN ST VANCOUVER",
    sale_date="2025-01-15", sale_price=1.2e6, pid="001-002-003",
    is_strata=True, lat=49.2, lng=-123.1)
chk("normalize_record schema 零缺陷", not iss)
chk("unit/base 字段存在且喂给 match",
    rec["unit_number"] == "204" and rec["base_address"] ==
    "3189 CAMOSUN ST" and "unit_number" in SI.SCHEMA and
    "base_address" in SI.SCHEMA)
chk("schema 校验能抓缺键", SI.validate_record({"pid": ""}) !=
    [])
chk("街名/类型/方向组件保留",
    rec["street_name"] == "CAMOSUN" and rec["street_type"] == "ST")

# ============ 2. parcel 合并不合并不同 unit ============
# 同楼两单元不同 pid:同址同坐标同事件 —— 只能同 parcel,
# 不得同 unit,也不得 canonical 合并
b1 = mk("B1", "a", address_raw="204-3189 CAMOSUN ST",
        sale_date="2025-03-01", sale_price=1.2e6, pid="P-204",
        is_strata=True, lat=49.2, lng=-123.1)
b2 = mk("B2", "b", address_raw="305-3189 CAMOSUN ST",
        sale_date="2025-03-01", sale_price=1.2e6, pid="P-305",
        is_strata=True, lat=49.2, lng=-123.1)
raw2 = [b1, b2]
pairs2 = AP.build_pairs(raw2)
cl2 = AP.build_clusters(raw2, pairs2)
canon2, ml2, cd2 = AP.dedup_sales(raw2, cl2["unit_of"],
                                  cl2["parcel_of"], pairs2)
chk("同楼异pid异unit → 同parcel异unit",
    cl2["parcel_of"][0] == cl2["parcel_of"][1] and
    cl2["unit_of"][0] != cl2["unit_of"][1])
chk("异unit不合并 → 2 canon + 候选重复",
    len(canon2) == 2 and len(ml2) == 0 and len(cd2) == 1)

# duplex 两半户同 parcel 同址不同单元
d1 = mk("D1", "a", address_raw="1-4422 6TH AVE W",
        sale_date="2026-05-28", sale_price=1.8e6, pid="D-1",
        is_strata=True, lat=49.26, lng=-123.19)
d2 = mk("D2", "b", address_raw="2-4422 6TH AVE W",
        sale_date="2026-05-28", sale_price=1.8e6, pid="D-2",
        is_strata=True, lat=49.26, lng=-123.19)
raw3 = [d1, d2]
pairs3 = AP.build_pairs(raw3)
cl3 = AP.build_clusters(raw3, pairs3)
canon3, ml3, cd3 = AP.dedup_sales(raw3, cl3["unit_of"],
                                  cl3["parcel_of"], pairs3)
chk("duplex 两半户不合并 → 2 canon", len(canon3) == 2
    and len(ml3) == 0)

# ============ 3. union 传递冲突拦截 ============
# A-C pid 冲突(同址同 unit 不同 pid);B 与 A、C 分别可合并
# (通过 corroborated)→ 传递合并必须被拦,C 不进 A 的簇
a = mk("A", "x", address_raw="204-3189 CAMOSUN ST",
       sale_date="2025-03-01", sale_price=1.2e6, pid="P-X",
       is_strata=True, lat=49.2, lng=-123.1)
b = mk("B", "y", address_raw="204-3189 CAMOSUN STREET",
       sale_date="2025-03-02", sale_price=1.2e6, pid="",
       is_strata=True, lat=49.20001, lng=-123.10001)
c = mk("C", "z", address_raw="204-3189 CAMOSUN ST",
       sale_date="2025-03-03", sale_price=1.2e6, pid="P-Z",
       is_strata=True, lat=49.20002, lng=-123.10002)
raw4 = [a, b, c]
pairs4 = AP.build_pairs(raw4)
cl4 = AP.build_clusters(raw4, pairs4)
chk("A/B 无pid冲突 → 同unit簇", cl4["unit_of"][0] ==
    cl4["unit_of"][1])
chk("C pid 与 A 矛盾被拦 → 异unit簇", cl4["unit_of"][2] !=
    cl4["unit_of"][0])
chk("冲突对记入 blocked 或 pair CONFLICT",
    len(cl4["blocked"]) >= 1 or any(
        m["tier"] == "IDENTITY_CONFLICT" for m in pairs4.values()))

# ============ 4. MPX 分类 ============
p, v, e = AP.classify_product("MULTIPLEX_3_4_CANDIDATE",
                              "", True, "204", "001-1", "", None,
                              1.9e6)
chk("MPX_3_4 strata+unit+pid 无 desc → 候选不升级",
    p == "MULTIPLEX_UNIT" and v == "CANDIDATE")
p2, v2, e2 = AP.classify_product("MULTIPLEX_WHOLE_BLDG",
                                 "Multiple Residence", False,
                                 "", "015-217-736", "", 1.67e6,
                                 3.8e6)
chk("1909 CHARLES 形态 → MPX_WHOLE VERIFIED",
    p2 == "MULTIPLEX_WHOLE" and v2 == "VERIFIED")
p3, v3, e3 = AP.classify_product("MULTIPLEX_WHOLE_BLDG",
                                 "Multi-Family (Vacant)", False,
                                 "", "x", "", 0, 1.93e6)
chk("3122 MARINE vacant → 非 MPX", p3 == "OTHER_RESIDENTIAL")
p4, v4, _ = AP.classify_product("MULTIPLEX_3_4_CANDIDATE", "",
                                None, "", "", "", None, 1.5e6)
chk("无证据 MPX → CANDIDATE", v4 == "CANDIDATE")
p5, v5, _ = AP.classify_product("DUPLEX", "", None, "", "",
                                "", None, 2e6)
chk("duplex 范围不明 → SCOPE_UNKNOWN",
    p5 == "DUPLEX_SCOPE_UNKNOWN" and v5 == "CANDIDATE")

# ============ 5. 房龄证据分级 + 重建污染 ============
tax = {"P-1": {"2023": {"year_built": "1980"},
               "2026": {"year_built": "2025"}}}
yb, t = AP.resolve_age(2023, "P-1", tax, None, None)
chk("2023成交取当时状态yb=1980非2025", yb == 1980 and
    t == "AGE_EXACT_PID")
yb, t = AP.resolve_age(2023, "P-9", {"P-9": {"2026":
    {"year_built": "2025"}}}, None, None)
chk("成交前只有重建后yb → TEMPORAL_CONFLICT",
    t == "AGE_TEMPORAL_CONFLICT")
yb, t = AP.resolve_age(2024, "", {}, {"yb": "1990", "pid":
    "P-7", "age_tier": "AGE_ADDRESS_INFERRED"},
    None)
chk("回连pid跨年取yb → ADDRESS_INFERRED",
    yb == 1990 and t == "AGE_ADDRESS_INFERRED")
yb, t = AP.resolve_age(2024, "", {}, {"yb": "", "pid": "P-8",
    "age_tier": "AGE_ADDRESS_INFERRED"},
    None) if "P-8" not in {} else (None, "AGE_UNKNOWN")
chk("无yb无pid → UNKNOWN", t == "AGE_UNKNOWN")
yb, t = AP.resolve_age(2024, "", {}, None, 2001, True)
chk("原生yb+单元证据无pid → UNIT_CONFIRMED",
    yb == 2001 and t == "AGE_UNIT_CONFIRMED")
yb, t = AP.resolve_age(2024, "", {}, None, 2001, False)
chk("原生yb无pid无unit → FOLIO_CONFIRMED",
    t == "AGE_FOLIO_CONFIRMED")

# ============ 6. 守恒:synthetic ETL 端到端 ============
fixture = [
    # 同一房产 arcgis+comps 两源同事件 → 1 canon 多源
    mk("F1", "bca_arcgis", address_raw="614 E 19TH AVENUE",
       bca_raw_address="614 E 19TH AVE",
       sale_date="2025-04-10", sale_price=1.298e6,
       pid="PP-614", is_strata=False, lat=49.25, lng=-123.09),
    mk("F2", "comps_v2", address_raw="614 E 19TH AVE VANCOUVER",
       mls_raw_address="614 E 19TH AVENUE VANCOUVER",
       sale_date="2025-03-01", sale_price=1.298e6,
       pid="PP-614", is_strata=False, lat=49.2501, lng=-123.0901),
    # 同房产 2023 前次成交 → 独立 canon
    mk("F3", "bca_arcgis", address_raw="614 E 19TH AVENUE",
       sale_date="2023-01-15", sale_price=1.1e6,
       pid="PP-614", is_strata=False, lat=49.25, lng=-123.09),
    # 邻地同价不同址 → 不得合并
    mk("F4", "comps_v2", address_raw="620 E 19TH AVE",
       sale_date="2025-04-10", sale_price=1.298e6,
       pid="PP-620", is_strata=False, lat=49.2502, lng=-123.0898),
]
pf = AP.build_pairs(fixture)
cf = AP.build_clusters(fixture, pf)
cf_, mlf, cdf = AP.dedup_sales(fixture, cf["unit_of"],
                             cf["parcel_of"], pf)
chk("同pid同事件两源 → 1 canon",
    sum(1 for g in cf_ if "F1" in [fixture[i]["raw_id"] for i in g]
        and "F2" in [fixture[i]["raw_id"] for i in g]) == 1)
chk("跨年两真交易保留", len(cf_) == 3)
chk("邻地不合并在 canon 数守恒", sum(len(g) for g in cf_) == 4)
prods = [AP.classify_product(fixture[i]["product_class"],
         fixture[i]["product_raw"], fixture[i]["is_strata"],
         fixture[i]["unit_number"], fixture[i]["pid"],
         fixture[i]["strata_lot"], None,
         fixture[i]["sale_price"])[0] for g in cf_ for i in g]
chk("产品数守恒", len(prods) == 4)

# ============ 7. 真实数据双 subprocess 幂等 ============
inp = ROOT / "data/research/normalized/standard-residential-comps-v2.csv"
if inp.exists():
    script = ROOT / "scripts/sales_archive_v1_0_2.py"
    outd = ROOT / "data/research/normalized/sales_archive_v1_0_2"
    run1 = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=900)
    chk("ETL run1 退出0", run1.returncode == 0)
    m1 = json.loads((outd / "manifest.json").read_text())["counts"]
    run2 = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=900)
    chk("ETL run2 退出0", run2.returncode == 0)
    m2 = json.loads((outd / "manifest.json").read_text())["counts"]
    chk("双跑幂等:counts 完全一致",
        {k: v for k, v in m1.items() if k != "tax_years"} ==
        {k: v for k, v in m2.items() if k != "tax_years"})
    # 报告↔CSV 一致性:覆盖表 canon 合计 == manifest canonical
    import csv as _csv
    cov = sum(int(r["canonical_sales"]) for r in _csv.DictReader(
        open(outd / "source_coverage_2023_2026.csv")))
    chk("coverage canon 合计 == manifest", cov ==
        m2["canonical_sales"])
    mat = sum(int(r["n_tx"]) for r in _csv.DictReader(
        open(outd / "transactions_by_year_product_age.csv")))
    chk("age 矩阵 n_tx 合计 == manifest", mat ==
        m2["canonical_sales"])
else:
    print("  (skip 真实数据 ETL:comps 输入缺失)")

print(f"\n{R['PASS']} PASS, {R['FAIL']} FAIL")
sys.exit(1 if R["FAIL"] else 0)
