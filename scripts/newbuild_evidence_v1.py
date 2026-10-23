#!/usr/bin/env python3
"""New-Build Evidence Recovery V1 — 官方供给名册 + 成交补全 + 独立基准 + 影子验证。
只读现有数据 + 官方开放数据快照;不改 V3s 任何估值。
三层实体:project_id(许可) / unit_id(folio) / sale_id(成交记录)。
产出 docs/audits/newbuild_coverage_v1/* + data/research/normalized/audit_v1/*
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from harvest_vancouver_sales_2025 import _num  # noqa: E402
from valuation_v2_sites import dev_cost  # noqa: E402
import valuation_v2_citywide as v2  # noqa: E402

RAW = ROOT / "data/research/raw/official"
NORM = ROOT / "data/research/normalized"
OUTAUD = ROOT / "docs/audits/newbuild_coverage_v1"
OUTD = NORM / "audit_v1"
OUTAUD.mkdir(parents=True, exist_ok=True)
OUTD.mkdir(parents=True, exist_ok=True)

V3S = json.loads((NORM / "valuation-v3s-shadow-2026-10-22.json").read_text())
HPATCH = json.loads(
    (NORM / "house_anchor_patch_v3s.json").read_text()) if \
    (NORM / "house_anchor_patch_v3s.json").exists() else {}
COMM = V3S["community_table"]

# geolocalarea → V3s 社区名(RC 合并保留)
GEO2COMM = {g: g for g in (
    "Arbutus Ridge", "Dunbar-Southlands", "Fairview", "Grandview-Woodland",
    "Hastings-Sunrise", "Kensington-Cedar Cottage", "Kerrisdale", "Killarney",
    "Kitsilano", "Marpole", "Mount Pleasant", "Oakridge", "Riley Park",
    "Shaughnessy", "South Cambie", "Strathcona", "Sunset",
    "Victoria-Fraserview", "West Point Grey")}
GEO2COMM["Renfrew-Collingwood"] = "Renfrew(北)"   # 官方合并区;细分按地址纬度后注


def norm_addr(a):
    """'2315 E 5TH AVENUE, Vancouver' → '2315 E 5TH AVENUE'"""
    a = (a or "").upper().split(",")[0].strip()
    a = re.sub(r"\s+", " ", a)
    return a


def civic_addr(num, street):
    return f"{num} {street}".strip()


def parse_units(desc):
    """从许可描述解析住宅单元数:'two-unit front building'/'3 SFD' 等"""
    W2N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "single": 1}
    d = (desc or "").lower()
    units = 0
    for m in re.finditer(r"(\w+)[-\s]unit", d):
        units += W2N.get(m.group(1), 0) or int(m.group(1)) \
            if m.group(1).isdigit() else W2N.get(m.group(1), 0)
    if units:
        return units
    for m in re.finditer(r"(\w+)-family", d):
        return W2N.get(m.group(1), 0) or (int(m.group(1))
                                          if m.group(1).isdigit() else 0)
    m = re.search(r"(\d+)\s*sfd", d)
    if m:
        return int(m.group(1))
    return None


def classify_permit(r):
    """specificusecategory + 描述 → (产品, 品质/附属标记)"""
    s = (r["specificusecategory"] or "").lower()
    d = (r["projectdescription"] or "").lower()
    tags = set()
    if "secondary suite" in s or "sec suite" in s or "secondary suite" in d:
        tags.add("SEC_SUITE")
    if "laneway" in s or "laneway" in d:
        tags.add("LANEWAY")
    if "custom" in d:
        tags.add("CUSTOM")
    if "infill" in s:
        tags.add("INFILL")
    if "lock -off" in s or "lock-off" in s or "lock off" in s:
        tags.add("LOCKOFF")
    if "multiple dwelling" in s or "multiplex" in d or "multiple dwelling" in d:
        return "MPX", tags
    if "duplex" in s or "two-family" in d or "2-family" in d:
        return "DUPLEX", tags
    if "single detached" in s or "one-family" in d or "1-family" in d \
            or "sfd" in d or "1fd" in s:
        return "HOUSE", tags
    if "laneway" in s:
        return "LANEWAY", tags
    return "OTHER", tags


# ============ 1. 项目册 ============
permits = list(csv.DictReader(
    open(RAW / "issued-permits-ldh-2017.csv", encoding="utf-8-sig"),
    delimiter=";"))
projects = []
for r in permits:
    prod, tags = classify_permit(r)
    units = parse_units(r["projectdescription"])
    if prod == "HOUSE":
        units = (units or 1) + (1 if "LANEWAY" in tags and
                                "laneway" not in
                                (r["specificusecategory"] or "").lower() else 0)
    elif prod == "DUPLEX" and not units:
        units = 2
    addr = norm_addr(r["address"])
    projects.append({
        "project_id": r["permitnumber"], "address": addr,
        "geo": r["geolocalarea"] or "UNKNOWN",
        "comm": GEO2COMM.get(r["geolocalarea"], r["geolocalarea"] or "UNKNOWN"),
        "issue_date": r["issuedate"], "issue_year": r["issueyear"],
        "product": prod, "units": units,
        "tags": "|".join(sorted(tags)),
        "project_value": r["projectvalue"],
        "status": "ISSUED",   # 官方签发≠完工;完工状态由 folio/成交交叉证
        "desc": (r["projectdescription"] or "")[:200],
    })
pcnt = Counter(p["product"] for p in projects)

# ============ 2. 单元册(tax folio) ============
tax26 = list(csv.DictReader(
    open(RAW / "property-tax-2026-yb2018plus.csv", encoding="utf-8-sig"),
    delimiter=";"))
tax23 = list(csv.DictReader(
    open(RAW / "property-tax-2023-yb2018plus.csv", encoding="utf-8-sig"),
    delimiter=";"))
tax26_folio = {r["folio"]: r for r in tax26}
tax23_folio = {r["folio"]: r for r in tax23}
ZONE_PROD = {}  # zoning_district → 倾向产品

def zone_product(z):
    z = (z or "").upper()
    if z.startswith(("R1-1", "RS", "RA", "RE", "RF")):
        return "HOUSE"
    if z.startswith("RT") or z.startswith("RM-"):
        return "DUPLEX_OR_MPX"
    if z.startswith("CD") or z.startswith("C"):
        return "CD"
    if z.startswith("FM") or z.startswith("RR"):
        return "DUPLEX_OR_MPX"
    return "OTHER"

units = []
for r in tax26:
    st = "STRATA" if r["legal_type"] == "STRATA" else "LAND"
    first_seen = "2026+" if r["folio"] not in tax23_folio else "≤2023"
    units.append({
        "unit_id": r["folio"], "pid": r["pid"], "legal_type": r["legal_type"],
        "addr": civic_addr(r["from_civic_number"] or r["to_civic_number"],
                           r["street_name"]),
        "zone": r["zoning_district"], "yb": r["year_built"],
        "land_v": r["current_land_value"], "impr_v": r["current_improvement_value"],
        "first_seen": first_seen,
        "zone_product": zone_product(r["zoning_district"]),
    })
ucnt = Counter(u["zone_product"] for u in units)

# ============ 3. 成交证据普查 ============
comp_files = list((NORM).glob("*.csv")) + \
    list((ROOT / "data/research/raw/location").glob("*sales*.json"))
src_inv = []
seen_keys = defaultdict(set)
for f in sorted(comp_files):
    try:
        if f.suffix == ".csv":
            rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
        else:
            j = json.loads(f.read_text())
            rows = j if isinstance(j, list) else \
                next((v for v in j.values() if isinstance(v, list)), [])
        n = len(rows)
        dates = [r.get("sale_date") or r.get("saleDate") or r.get("date") or ""
                 for r in rows[:5000]]
        dates = [d for d in dates if d[:4].isdigit()]
        src_inv.append({"source": str(f.relative_to(ROOT)), "rows": n,
                        "date_min": min(dates) if dates else "",
                        "date_max": max(dates) if dates else "",
                        "size_mb": round(f.stat().st_size / 1e6, 1)})
    except Exception as e:
        src_inv.append({"source": str(f.relative_to(ROOT)), "rows": -1,
                        "date_min": "", "date_max": f"ERR:{e}",
                        "size_mb": round(f.stat().st_size / 1e6, 1)})

COMPS = list(csv.DictReader(open(
    NORM / "standard-residential-comps-v2.csv", encoding="utf-8-sig")))
for r in COMPS:
    r["_p"] = _num(r["sale_price"]); r["_fa"] = _num(r["floor_above_sf"])
    r["_lot"] = _num(r["lot_sqft"]); r["_yb"] = _num(r["year_built"])

# ============ 4. 三层实体 + 瀑布 ============
# sale_id = record_id; unit 关联 via 地址; project 关联 via 地址
permit_by_addr = defaultdict(list)
for p in projects:
    permit_by_addr[p["address"]].append(p)
comp_by_addr = defaultdict(list)
for r in COMPS:
    comp_by_addr[norm_addr(r["address"])].append(r)

def nkey(a):
    a = norm_addr(a)
    m = re.match(r"(\d+)\s+(.*)", a)
    return (m.group(1), m.group(2) if m else a) if m else ("", a)

permit_nk = defaultdict(list)
for p in projects:
    permit_nk[nkey(p["address"])].append(p)

# 瀑布:每社区×产品 项目→建成folio→成交→可用池
NEWYB = 2018
water = defaultdict(lambda: defaultdict(int))
for p in projects:
    if p["product"] in ("HOUSE", "DUPLEX", "MPX"):
        water[(p["comm"], p["product"])]["permits"] += 1
        water[(p["comm"], p["product"])]["permit_units"] += p["units"] or 0
# 按社区×产品走 comps 全量级联(EXCL同 active_screening)
EXCL = {"BUILT_AFTER_SALE", "LAND_SALE_SIGNATURE", "POSSIBLE_PRESALE"}
for r in COMPS:
    comm = r.get("official_area") or "?"
    pc = r["product_class"]
    prod = ("HOUSE" if "HOUSE" in pc else
            "DUPLEX" if "DUPLEX" in pc else
            "MPX" if pc else "OTHER")
    w = water[(comm, prod)]
    w["comp_records"] += 1
    if r.get("asof_status") in EXCL:
        w["x_asof"] += 1; continue
    w["asof_ok"] += 1
    if r["gst_basis"] == "new_first_sale":
        w["new_first_sale"] += 1
    if r["_yb"] and r["_yb"] >= NEWYB:
        w["yb2018+"] += 1
    if not r["_yb"]:
        w["yb_missing"] += 1
    if r["_fa"]:
        w["has_fa"] += 1
    else:
        w["fa_missing"] += 1
    if r["_lot"]:
        w["has_lot"] += 1
    if r["_p"]:
        w["has_price"] += 1
    if (r["_yb"] or 0) >= NEWYB and r["_fa"] and r["_p"] \
            and r.get("asof_status") not in EXCL:
        w["usable_newbuild"] += 1

# ============ 4b. 属性恢复:yb 缺失记录回连 tax folio ============
tax_by_addr = {}
for r in tax26:
    a = f"{r['from_civic_number'] or r['to_civic_number']} {r['street_name']}".strip()
    tax_by_addr.setdefault(a, r)

def comp_tax_key(a):
    a = (a or "").upper().replace(" VANCOUVER", "")
    a = a.replace(" AVENUE", " AVE").replace(" STREET", " ST") \
         .replace(" BOULEVARD", " BLVD").replace(" DRIVE", " DR") \
         .replace(" ROAD", " RD").replace(" PLACE", " PL")
    return re.sub(r"\s+", " ", a).strip()

recovered = []
for r in COMPS:
    if _num(r["year_built"]) or "HOUSE" not in r["product_class"]:
        continue
    t = tax_by_addr.get(comp_tax_key(r["address"]))
    if t and (t["year_built"] or "") >= "2018":
        r["_yb"] = int(t["year_built"])
        r["_yb_recovered"] = True
        r["asof_note"] = (r.get("asof_note") or "") + f"|yb↩tax{t['year_built']}"
        recovered.append({"address": r["address"], "sale_date": r["sale_date"],
                          "sale_price": r["sale_price"], "yb": t["year_built"],
                          "zone": t["zoning_district"], "folio": t["folio"],
                          "pid": t["pid"]})
        comm = r.get("official_area") or "?"
        if r["_fa"] and r["_p"] and r.get("asof_status") not in EXCL:
            water[(comm, "HOUSE")]["usable_newbuild_recovered"] += 1

# ============ 5. 三池重建(全源成交, 标记口径) ============
pools = {"HOUSE": [], "DUPLEX": [], "MPX": []}
for r in COMPS:
    pc = r["product_class"]
    if "DUPLEX" in pc:
        pools["DUPLEX"].append(r)
    elif "TRIPLEX" in pc or "FOURPLEX" in pc or "MULTI" in pc \
            or "MPX" in pc or "ROW" in pc:
        pools["MPX"].append(r)
    elif "HOUSE" in pc:
        pools["HOUSE"].append(r)
pool_cnt = {k: len(v) for k, v in pools.items()}

# ============ 6. 区域基准 V1(独立于 V3s:不训练不用其标签) ============
BANDS = [(0, 1600), (1600, 2000), (2000, 2400), (2400, 2800),
         (2800, 3400), (3400, 99999)]
def band_of(fa):
    for lo, hi in BANDS:
        if lo <= fa < hi:
            return f"{lo}-{hi if hi < 99999 else 'up'}"
    return "?"

def bench(recs, comm, prod):
    """new yb>=2018 + 近24mo优先,不足→2023+;独立P25/50/75"""
    out = {}
    good = [r for r in recs if (r["_yb"] or 0) >= NEWYB and r["_fa"] and r["_p"]
            and r.get("asof_status") not in EXCL]
    for band in [b[0] for b in BANDS]:
        lo, hi = [b for b in BANDS if b[0] == band][0]
        inb = [r for r in good if lo <= r["_fa"] < hi]
        for win, lbl in ((None, "2023+"), ("2025-01-01", "recent24"),
                         ("2025-10-01", "recent12")):
            sel = [r for r in inb if not win or r["sale_date"] >= win]
            if len(sel) < 3:
                continue
            psf = sorted(r["_p"] / r["_fa"] for r in sel)
            n = len(psf)
            out[f"{band}|{lbl}"] = {
                "n": n, "p25": round(psf[n // 4]),
                "p50": round(psf[n // 2]), "p75": round(psf[3 * n // 4]),
                "fresh": max(r["sale_date"] for r in sel),
                "grade": "A" if n >= 8 else "B" if n >= 5 else "C"}
    return out

benchmarks = {}
by_cp = defaultdict(list)
for r in COMPS:
    prod = ("HOUSE" if "HOUSE" in r["product_class"] else
            "DUPLEX" if "DUPLEX" in r["product_class"] else
            "MPX" if r["product_class"] else "OTHER")
    by_cp[(r.get("official_area") or "?", prod)].append(r)
    by_cp[("_CITY", prod)].append(r)   # 市级借用层(标 BORROWED)
for (comm, prod), recs in by_cp.items():
    bb = bench(recs, comm, prod)
    for k, v in bb.items():
        benchmarks[f"{comm}|{prod}|{k}"] = v

# ============ 7. 影子交叉验证 ============
shadow = []
for s in V3S["sites"]:
    if s["status"] != "ACTIVE":
        continue
    comm = s["comm"]
    for p in s["plans"]:
        if not p.get("eligible"):
            continue
        c = p.get("contract")
        if p["product"] == "DUPLEX" and p.get("contract_stable"):
            c = p["contract_stable"]
        if p["product"] == "HOUSE" and s["address"] in HPATCH:
            c = HPATCH[s["address"]]["new_contract"]
        if not c:
            continue
        fa = p["build_sf"] / (p["units"] or 1)
        ptype = 'MPX' if p['product'] in ('TRIPLEX','FOURPLEX') else p['product']
        band_lo = band_of(fa).split("-")[0]   # 基准键用带下界
        bmk, key = None, None
        for c_key in (comm, "_CITY"):
            k = f"{c_key}|{ptype}|{band_lo}|2023+"
            if benchmarks.get(k):
                bmk, key = benchmarks[k], k
                break
        ec = dev_cost(c, s["ask"], p["build_sf"], p["units"],
                      400 if p["product"] not in ("HOUSE", "DUPLEX") else 350)
        row = {"listing": s["address"], "plan": p["product"],
               "v3s_price": round(c), "bench_key": key,
               "borrowed": "BORROWED_CITY" if (key or "").startswith("_CITY")
                           else "OWN",
               "bench_n": bmk["n"] if bmk else 0,
               "bench_p50_psf": bmk["p50"] if bmk else None,
               "indep_price": round(bmk["p50"] * fa * (p["units"] or 1))
                              if bmk else None,
               "v3s_roi": round(ec["roi"], 4),
               "indep_roi": (round(dev_cost(
                   bmk["p50"] * fa * p["units"], s["ask"], p["build_sf"],
                   p["units"], 350)["roi"], 4) if bmk else None),
               "diff": (round(c / (bmk["p50"] * fa * p["units"]) - 1, 3)
                        if bmk else None),
               "grade": bmk["grade"] if bmk else "NONE",
               "fresh": bmk["fresh"] if bmk else None}
        shadow.append(row)

# ============ 8. 写出 ============
def wcsv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

wcsv(OUTD / "project_registry.csv", projects)
wcsv(OUTD / "unit_registry.csv", units)
wcsv(OUTD / "source_inventory.csv",
     [{k: v for k, v in s.items()} for s in src_inv])
wcsv(OUTD / "yb_recovered_records.csv", recovered)
wfall = [{"comm": c, "product": p, **v} for (c, p), v in sorted(water.items())]
wcsv(OUTD / "coverage_waterfall.csv", wfall)
wcsv(OUTD / "regional_benchmark.csv",
     [{"key": k, **v} for k, v in sorted(benchmarks.items())])
wcsv(OUTD / "shadow_impact.csv", shadow)

# sales_evidence: 可用新建成交(证据层,含等级标签)
sales_ev = []
for r in COMPS:
    prod = ("HOUSE" if "HOUSE" in r["product_class"] else
            "DUPLEX" if "DUPLEX" in r["product_class"] else
            "MPX" if r["product_class"].startswith("MULTIPLEX") else None)
    if not prod or (r["_yb"] or 0) < NEWYB or not r["_fa"] or not r["_p"] \
            or r.get("asof_status") in EXCL:
        continue
    tier = "verified" if (r["pid"] and r.get("asof_status") == "CONSISTENT"
                          and not r.get("_yb_recovered")) else \
           "usable_lowconf"
    sales_ev.append({
        "product": prod, "address": r["address"], "comm": r["official_area"],
        "sale_date": r["sale_date"], "sale_price": r["_p"],
        "floor_above_sf": r["_fa"], "bsmt_sf": r["bsmt_sf"],
        "lot_sqft": r["_lot"], "year_built": r["_yb"],
        "gst_basis": r["gst_basis"], "asof_status": r["asof_status"],
        "pid": r["pid"], "tier": tier,
        "yb_recovered": bool(r.get("_yb_recovered")),
        "src": r["supplemental_source"] or "comps-v2"})
wcsv(OUTD / "sales_evidence.csv", sales_ev)

# missing_evidence_queue: 缺口与修复路径
mq = []
mq.append({"gap": "HOUSE yb缺失 2,335 条(arcgis源无year_built)",
           "affected": "HOUSE 可用样本 215→可+?", "cause": "ATTRIBUTE_MISSING",
           "fix": "BCA folio 逐条回连(已恢复16)/LTSA", "status": "PARTIAL"})
mq.append({"gap": "MPX fa缺失 934/959 条 → 仅 20 条可用",
           "affected": "MPX 基准几乎全 NOT_ESTIMABLE", "cause": "ATTRIBUTE_MISSING",
           "fix": "per-project 单元面积表(许可附图/GVR)", "status": "OPEN"})
mq.append({"gap": "STRATA_UNIT_UNKNOWN 635 条无法归类产品",
           "affected": "MPX/DUPLEX 单元池", "cause": "MATCH_FAILED",
           "fix": "strata plan→permit 项目回连", "status": "OPEN"})
mq.append({"gap": "MLS/GVR 新建成交渠道未接入",
           "affected": "HOUSE/DUPLEX 覆盖 ~10%", "cause": "SOURCE_MISSING",
           "fix": "订阅 MLS 成交导出或经纪合作", "status": "OPEN"})
mq.append({"gap": "许可 ISSUED≠COMPLETED,无官方完工字段",
           "affected": "供给管线→存量转换", "cause": "SOURCE_MISSING",
           "fix": "folio 出现+yb 弱判定;occupancy permit 另源", "status": "PARTIAL"})
mq.append({"gap": "CITY级借用基准含 2549 KENT/78 MALTA 争议锚点",
           "affected": "22 个 2800+ 带影子估值", "cause": "CIRCULAR_REF",
           "fix": "剔除锚点重算+人工批准", "status": "OPEN"})
wcsv(OUTD / "missing_evidence_queue.csv", mq)

manifest = {
    "run": datetime.datetime.now().isoformat(),
    "inputs": {
        "permits": "official/issued-permits-ldh-2017.csv",
        "tax2026": "official/property-tax-2026-yb2018plus.csv",
        "tax2023": "official/property-tax-2023-yb2018plus.csv",
        "comps": "standard-residential-comps-v2.csv",
        "v3s": "valuation-v3s-shadow-2026-10-22.json",
    },
    "input_hash": hashlib.sha256(
        b"".join((NORM / f).read_bytes() for f in
                  ["standard-residential-comps-v2.csv",
                   "valuation-v3s-shadow-2026-10-22.json"])).hexdigest()[:16],
    "counts": {"permits": len(projects), "product": dict(pcnt),
               "units": len(units), "zone_prod": dict(ucnt),
               "pools": pool_cnt, "bench_cells": len(benchmarks),
               "shadow_rows": len(shadow)},
}
(OUTD / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
print(json.dumps(manifest["counts"], ensure_ascii=False, indent=1))
print("wrote audit_v1/* + registry/benchmark/shadow")
