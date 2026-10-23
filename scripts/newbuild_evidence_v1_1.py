#!/usr/bin/env python3
"""New-Build Evidence Recovery V1.1 — 分类修正 + 证据恢复 + 独立基准重建。
修复 V1 已确认缺陷;不改 V3s 任何估值/ROI/纠偏补丁。
四层实体:permit_record_id / project_id / unit_id / sale_id。
产出 data/research/normalized/audit_v1_1/* + docs/audits 更新。
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from harvest_vancouver_sales_2025 import _num  # noqa: E402
from valuation_v2_sites import dev_cost       # noqa: E402

RAW = ROOT / "data/research/raw/official"
LOC = ROOT / "data/research/raw/location"
NORM = ROOT / "data/research/normalized"
OUTAUD = ROOT / "docs/audits/newbuild_coverage_v1"
OUTD = NORM / "audit_v1_1"
OUTD.mkdir(parents=True, exist_ok=True); OUTAUD.mkdir(parents=True, exist_ok=True)

V3S = json.loads((NORM / "valuation-v3s-shadow-2026-10-22.json").read_text())
HPATCH = json.loads((NORM / "house_anchor_patch_v3s.json").read_text()) \
    if (NORM / "house_anchor_patch_v3s.json").exists() else {}

# ---------- 常量 ----------
NEWYB = 2018
EXCL = {"BUILT_AFTER_SALE", "LAND_SALE_SIGNATURE", "POSSIBLE_PRESALE"}
# 已裁定不合格的单点支配锚点(规范化地址) — 基准池剔除,记录保留
ANCHOR_EXCLUDE = {"1185 17TH AVE E", "2549 KENT AVENUE NORTH E", "78 MALTA PL"}
RC_LAT_SPLIT = 49.243   # Renfrew(北)/Collingwood(南) 经验证分界≈E29th/Grandview Hwy

# 显式产品映射 — 禁止"非空即MPX";未列值→OTHER 不进池
CLASS_MAP = {
    "HOUSE_DETACHED": ("HOUSE", "WHOLE"),
    "HOUSE_SUSPECT_STRATA": ("HOUSE", "WHOLE_SUSPECT"),
    "DUPLEX_HALF_STRATA": ("DUPLEX", "HALF"),
    "DUPLEX_WHOLE_BLDG": ("DUPLEX", "WHOLE"),
    "MULTIPLEX_3_4_CANDIDATE": ("MPX", "UNIT"),
    "MULTIPLEX_WHOLE_BLDG": ("MPX_WHOLE", "WHOLE"),
    "STRATA_UNIT_UNKNOWN": ("STRATA_UNKNOWN", "UNIT"),
    "OTHER": ("OTHER", "?"),
}


def norm_addr(a):
    a = (a or "").upper().split(",")[0].replace(" VANCOUVER", "").strip()
    a = a.replace(" AVENUE", " AVE").replace(" STREET", " ST") \
         .replace(" BOULEVARD", " BLVD").replace(" DRIVE", " DR") \
         .replace(" ROAD", " RD").replace(" PLACE", " PL")
    a = re.sub(r"\s+", " ", a).strip()
    # "1185 E 17TH AVE" → "1185 17TH AVE E"(对齐 comps 门牌-序数-朝向序)
    m = re.match(r"^(\d+-?\d*)\s+([EWNS])\s+(\d+(?:ST|ND|RD|TH))\s+"
                 r"(AVE|ST|BLVD|DR|RD|PL)$", a)
    if m:
        a = f"{m.group(1)} {m.group(3)} {m.group(4)} {m.group(2)}"
    return a


def base_addr(a):
    """去单元前缀 '204-3189 CAMOSUN' → '3189 CAMOSUN'"""
    return re.sub(r"^\d+-", "", norm_addr(a))


def rc_split(rec):
    """Renfrew-Collingwood 官方合并区按纬度细分;无纬度保持未分。"""
    if (rec.get("official_area") or "") != "Renfrew-Collingwood":
        return rec.get("official_area") or "?"
    try:
        lat = float(rec.get("lat") or 0)
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


# BCA 描述 → 判定(arcgis official_bca_description / 许可 desc 复用)
def desc_verdict(desc):
    d = (desc or "").lower()
    if not d:
        return "UNRESOLVED", "NO_DESC"
    # 非住宅优先:warehouse 含 "house" 子串,必须先排除
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


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ============ 输入指纹(全部输入 sha256) ============
INPUTS = {
    "permits": RAW / "issued-permits-ldh-2017.csv",
    "tax2026": RAW / "property-tax-2026-yb2018plus.csv",
    "tax2023": RAW / "property-tax-2023-yb2018plus.csv",
    "comps": NORM / "standard-residential-comps-v2.csv",
    "v3s": NORM / "valuation-v3s-shadow-2026-10-22.json",
    "patch": NORM / "house_anchor_patch_v3s.json",
    "arcgis_all": LOC / "bca-arcgis-sales-2025-present-all.json",
    "enr_high": LOC / "bca-arcgis-sales-2023-01-01-2026-09-06-enriched-high.json",
}
HASHES = {k: sha(p) for k, p in INPUTS.items() if p.exists()}

# ============ 1. 许可册:permit_record ↔ project ============
permits = list(csv.DictReader(
    open(INPUTS["permits"], encoding="utf-8-sig"), delimiter=";"))
precs, proj_by_addr = [], defaultdict(list)
for r in permits:
    prod, tags = classify_permit(r["specificusecategory"],
                                 r["projectdescription"])
    addr = norm_addr(r["address"])
    geo = r["geolocalarea"] or "UNKNOWN"
    lat = None
    if r.get("geo_point_2d"):
        try:
            lat = float(r["geo_point_2d"].split(",")[0])
        except Exception:
            lat = None
    comm = geo
    if geo == "Renfrew-Collingwood":
        comm = ("Renfrew(北)" if lat and lat >= RC_LAT_SPLIT
                else "Collingwood(南)" if lat
                else "Renfrew-Collingwood(未分)")
    precs.append({"permit_id": r["permitnumber"], "addr": addr,
                  "product": prod, "tags": sorted(tags),
                  "units": parse_units(r["projectdescription"]),
                  "issue_date": r["issuedate"], "issue_year": r["issueyear"],
                  "comm": comm, "value": r["projectvalue"],
                  "desc": (r["projectdescription"] or "")[:160]})
    proj_by_addr[addr].append(precs[-1])

# 项目 = 同规范化地址的全部许可集合(关联证据保留)
projects = []
for addr, ps in proj_by_addr.items():
    ps.sort(key=lambda x: x["issue_date"])
    # 住宅单元合计:每 permit 独立贡献(HOUSE 1 / DUPLEX 2 / LANEWAY 1 /
    # MPX 解析;主户与附属 suite 分列)
    units_total, acc = 0, 0
    for p in ps:
        u = p["units"]
        if u is None:
            u = {"HOUSE": 1, "DUPLEX": 2, "LANEWAY": 1,
                 "MPX": None}.get(p["product"])
        if u:
            units_total += u
        if "SEC_SUITE" in p["tags"] or "LANEWAY" in p["tags"]:
            acc += 1
    projects.append({
        "project_id": "P" + hashlib.md5(addr.encode()).hexdigest()[:10],
        "address": addr, "comm": ps[0]["comm"],
        "n_permits": len(ps),
        "permit_ids": "|".join(p["permit_id"] for p in ps),
        "products": "|".join(sorted({p["product"] for p in ps})),
        "first_issue": ps[0]["issue_date"], "last_issue": ps[-1]["issue_date"],
        "units_est": units_total, "accessory_flags": acc,
        "status": "ISSUED"})
pcnt = Counter(p["product"] for p in precs)
recon = [{"project_id": p["project_id"], "address": p["address"],
          "comm": p["comm"], "n_permits": p["n_permits"],
          "permit_ids": p["permit_ids"], "products": p["products"],
          "units_est": p["units_est"], "first_issue": p["first_issue"],
          "last_issue": p["last_issue"], "status": p["status"]}
         for p in projects]

# ============ 2. 单元册 + folio 年度核查 ============
tax26 = list(csv.DictReader(open(INPUTS["tax2026"], encoding="utf-8-sig"),
                            delimiter=";"))
tax23 = list(csv.DictReader(open(INPUTS["tax2023"], encoding="utf-8-sig"),
                            delimiter=";"))
folio_audit = []
for yr, rows in (("2023", tax23), ("2026", tax26)):
    fids = {r["folio"] for r in rows}
    pids = {r["pid"] for r in rows if r["pid"]}
    bad = [r for r in rows if r["year_built"]
           and r["year_built"].isdigit() and int(r["year_built"]) > int(yr)]
    folio_audit.append({"report_year": yr, "rows": len(rows),
                        "uniq_folio": len(fids), "uniq_pid": len(pids),
                        "yb_gt_report_year": len(bad)})
tax26_folio = {r["folio"]: r for r in tax26}
tax23_folio = {r["folio"]: r for r in tax23}
tax_by_pid = defaultdict(list)
tax_by_addr = defaultdict(list)
for r in tax26:
    if r["pid"]:
        tax_by_pid[r["pid"]].append(r)
    a = f"{r['from_civic_number'] or r['to_civic_number']} " \
        f"{r['street_name']}".strip()
    tax_by_addr[norm_addr(a)].append(r)


def zone_product(z):
    z = (z or "").upper()
    if z.startswith(("R1-1", "RS", "RA", "RE", "RF")):
        return "HOUSE"
    if z.startswith(("RT", "RM-", "FM", "RR")):
        return "DUPLEX_OR_MPX"
    if z.startswith(("CD", "C")):
        return "CD"
    return "OTHER"


units = [{"unit_id": r["folio"], "pid": r["pid"],
          "legal_type": r["legal_type"],
          "addr": f"{r['from_civic_number'] or r['to_civic_number']} "
                  f"{r['street_name']}".strip(),
          "zone": r["zoning_district"], "yb": r["year_built"],
          "first_seen": "2026+" if r["folio"] not in tax23_folio else "≤2023",
          "zone_product": zone_product(r["zoning_district"])}
         for r in tax26]
ucnt = Counter(u["zone_product"] for u in units)

# ============ 3. 成交层 + 显式产品映射 ============
COMPS = list(csv.DictReader(open(INPUTS["comps"], encoding="utf-8-sig")))
for r in COMPS:
    r["_p"], r["_fa"], r["_lot"], r["_yb"] = (
        _num(r["sale_price"]), _num(r["floor_above_sf"]),
        _num(r["lot_sqft"]), _num(r["year_built"]))
    r["_prod"], r["_scope"] = CLASS_MAP.get(
        r["product_class"], ("OTHER", "?"))
    r["_comm"] = rc_split(r)
    r["_base"] = base_addr(r["address"])

SNAPSHOT = max(r["sale_date"] for r in COMPS if r["sale_date"])  # 数据截点
snap = datetime.date.fromisoformat(SNAPSHOT)
WIN = {"2023+": "2023-01-01",
       "recent24": (snap - datetime.timedelta(days=730)).isoformat(),
       "recent12": (snap - datetime.timedelta(days=365)).isoformat()}

# arcgis 全量(描述字段是 strata/产品判定的关键证据)
arcgis = json.loads(INPUTS["arcgis_all"].read_text())
arcgis = arcgis if isinstance(arcgis, list) else \
    next(v for v in arcgis.values() if isinstance(v, list))
arc_by_key = {(r["address"], r["sale_date"]): r for r in arcgis}

# ============ P1a. HOUSE yb 多源恢复 ============
# 源1: 全部 enriched/原始 json 中存在的 year_built
enrich_pool = defaultdict(dict)
for f in LOC.glob("*.json"):
    try:
        j = json.loads(f.read_text())
        rows = j if isinstance(j, list) else \
            next((v for v in j.values() if isinstance(v, list)), [])
        for e in rows:
            if e.get("year_built"):
                enrich_pool[(e.get("address"), e.get("sale_date"))][
                    f.name] = e["year_built"]
    except Exception:
        continue

yb_log = []
yb_rec = {"RECOVERED_ENRICHED": 0, "RECOVERED_TAX": 0,
          "AMBIGUOUS": 0, "NO_SOURCE": 0, "PRE2018": 0}
for r in COMPS:
    if r["_yb"] or r["_prod"] != "HOUSE":
        continue
    key = (r["address"], r["sale_date"])
    # 源1 enriched(多文件冲突→AMBIGUOUS)
    if key in enrich_pool:
        ys = set(enrich_pool[key].values())
        if len(ys) == 1:
            y = int(next(iter(ys)))
            r["_yb"], r["_yb_src"] = y, "enriched"
            yb_rec["RECOVERED_ENRICHED" if y >= NEWYB else "PRE2018"] += 1
            yb_log.append({"address": r["address"], "date": r["sale_date"],
                           "yb": y, "src": "enriched"})
            continue
        else:
            yb_rec["AMBIGUOUS"] += 1
            yb_log.append({"address": r["address"], "date": r["sale_date"],
                           "yb": "", "src": "ambiguous_multi_src"})
            continue
    # 源2 tax folio:pid 唯一优先;地址多 folio→歧义
    cands = tax_by_pid.get(r["pid"], []) if r["pid"] else \
        tax_by_addr.get(norm_addr(r["address"]), [])
    cands = [t for t in cands if t.get("year_built")]
    if len(cands) == 1:
        y = int(cands[0]["year_built"])
        r["_yb"], r["_yb_src"] = y, "tax_folio"
        yb_rec["RECOVERED_TAX" if y >= NEWYB else "PRE2018"] += 1
        yb_log.append({"address": r["address"], "date": r["sale_date"],
                       "yb": y, "src": f"tax:{cands[0]['folio']}"})
    elif len(cands) > 1:
        yb_rec["AMBIGUOUS"] += 1
        yb_log.append({"address": r["address"], "date": r["sale_date"],
                       "yb": "", "src": f"ambiguous_folio×{len(cands)}"})
    else:
        yb_rec["NO_SOURCE"] += 1
        yb_log.append({"address": r["address"], "date": r["sale_date"],
                       "yb": "", "src": "none"})

# ============ P1b. DUPLEX 半户/整栋核销 ============
dup_groups = defaultdict(list)
for r in COMPS:
    if r["_prod"] == "DUPLEX":
        dup_groups[r["_base"]].append(r)
duplex_projects = {}
for b, rs in dup_groups.items():
    halves = [x for x in rs if x["_scope"] == "HALF"]
    wholes = [x for x in rs if x["_scope"] == "WHOLE"]
    for x in rs:
        x["_pair"] = "PAIRED_HALVES" if len(halves) >= 2 else \
                     "WHOLE_SALE" if x["_scope"] == "WHOLE" else "LONE_HALF"
    duplex_projects[b] = {"halves": len(halves), "wholes": len(wholes),
                          "records": len(rs)}

# ============ P1c. STRATA 分类(635 + 5613 arcgis 重审计) ============
permit_prod_by_base = defaultdict(set)
for p in precs:
    permit_prod_by_base[base_addr(p["addr"])].add(p["product"])


def classify_strata(r):
    """多证据判定;zoning 不单独定案。返回(verdict, evidence)"""
    key = (r["address"], r["sale_date"])
    arc = arc_by_key.get(key, {})
    desc = arc.get("official_bca_description") or ""
    v0, why = desc_verdict(desc)
    ev = [why]
    # 许可旁证(同基址)
    pp = permit_prod_by_base.get(r["_base"], set())
    # tax 旁证(pid→zoning)
    zps = {zone_product(t["zoning_district"])
           for t in tax_by_pid.get(r.get("pid") or "", [])}
    if "MPX" in pp:
        ev.append("PERMIT_MPX")
        if v0 == "UNRESOLVED":
            return "CONFIRMED_MULTIPLEX", ev
    if "DUPLEX" in pp:
        ev.append("PERMIT_DUPLEX")
        if v0 == "UNRESOLVED":
            return "CONFIRMED_DUPLEX", ev
    if "TOWNHOUSE" in pp:
        ev.append("PERMIT_TOWNHOUSE")
    if zps:
        ev.append("ZONE_" + "|".join(sorted(zps)))
    return v0, ev


strata_audit = []
for r in COMPS:
    if r["product_class"] == "STRATA_UNIT_UNKNOWN":
        v, ev = classify_strata(r)
        r["_strata_verdict"] = v
        # 恢复入池:CONFIRMED_* / TOWNHOUSE(辅助) 修正 _prod
        if v == "CONFIRMED_DUPLEX":
            r["_prod"], r["_scope"] = "DUPLEX", "HALF"
        elif v == "CONFIRMED_MULTIPLEX":
            r["_prod"], r["_scope"] = "MPX", "UNIT"
        elif v == "TOWNHOUSE":
            r["_prod"], r["_scope"] = "TOWNHOUSE", "UNIT"
        elif v == "APARTMENT":
            r["_prod"], r["_scope"] = "APARTMENT", "UNIT"
        strata_audit.append({"address": r["address"], "date": r["sale_date"],
                             "price": r["_p"], "pid": r["pid"],
                             "yb": r["year_built"], "verdict": v,
                             "evidence": "|".join(ev),
                             "desc": (arc_by_key.get(
                                 (r["address"], r["sale_date"]), {})
                                 .get("official_bca_description") or "")})

# 已分类 MPX/HOUSE_SUSPECT 记录再核对(描述交叉证)
recheck = []
for r in COMPS:
    if r["product_class"] in ("MULTIPLEX_3_4_CANDIDATE",
                              "MULTIPLEX_WHOLE_BLDG", "DUPLEX_HALF_STRATA",
                              "HOUSE_SUSPECT_STRATA", "OTHER"):
        arc = arc_by_key.get((r["address"], r["sale_date"]), {})
        v, why = desc_verdict(arc.get("official_bca_description"))
        recheck.append({"address": r["address"], "date": r["sale_date"],
                        "old_class": r["product_class"], "desc_verdict": v,
                        "reason": why,
                        "desc": arc.get("official_bca_description") or ""})
        # WHOLE_BLDG 实为公寓 → 标 APARTMENT_WHOLE 出池
        if r["product_class"] == "MULTIPLEX_WHOLE_BLDG" and v == "APARTMENT":
            r["_prod"], r["_scope"] = "APARTMENT_WHOLE", "WHOLE"
        # 3_4_CANDIDATE 实为城市屋 → TOWNHOUSE(辅助,不进 MPX 主池)
        if r["product_class"] == "MULTIPLEX_3_4_CANDIDATE" \
                and v == "TOWNHOUSE":
            r["_prod"], r["_scope"] = "TOWNHOUSE", "UNIT"
        if r["product_class"] == "OTHER" and v in (
                "CONFIRMED_HOUSE", "CONFIRMED_DUPLEX"):
            r["_prod"] = {"CONFIRMED_HOUSE": "HOUSE",
                          "CONFIRMED_DUPLEX": "DUPLEX"}[v]

# 5,613 条排除 arcgis 记录重审计
arc_excl = [r for r in arcgis
            if (r["address"], r["sale_date"]) not in
            {(c["address"], c["sale_date"]) for c in COMPS}]
arc_excl_audit = [{"address": r["address"], "date": r["sale_date"],
                   "price": r.get("sale_price"),
                   "desc": r.get("official_bca_description") or "",
                   "verdict": desc_verdict(
                       r.get("official_bca_description"))[0],
                   "reason": desc_verdict(
                       r.get("official_bca_description"))[1]}
                  for r in arc_excl]
excl_v = Counter(a["verdict"] for a in arc_excl_audit)

# ============ P1d. fa 恢复(enriched first+second floor) ============
fa_rec = 0
for r in COMPS:
    if r["_fa"]:
        continue
    for f in LOC.glob("*.json"):
        pass  # enriched_pool 只存 yb;fa 走 arc_by_key 无此字段
# enriched 文件重扫(一次)取 fa
fa_pool = {}
for f in LOC.glob("*enriched*.json"):
    try:
        j = json.loads(f.read_text())
        rows = j if isinstance(j, list) else \
            next((v for v in j.values() if isinstance(v, list)), [])
        for e in rows:
            a = _num(e.get("bca_first_floor_area_sqft"))
            b = _num(e.get("bca_second_floor_area_sqft"))
            if a:
                fa_pool[(e.get("address"), e.get("sale_date"))] = a + (b or 0)
    except Exception:
        continue
for r in COMPS:
    if not r["_fa"]:
        v = fa_pool.get((r["address"], r["sale_date"]))
        if v:
            r["_fa"], r["_fa_src"] = v, "enriched"
            fa_rec += 1

# ============ P2. 证据分级 ============
def tier(r):
    prod_ok = r["_prod"] in ("HOUSE", "DUPLEX", "MPX", "MPX_WHOLE")
    sale_ok = bool(r["_p"] and r["sale_date"])
    id_ok = bool(r["pid"]) or r.get("asof_status") == "CONSISTENT"
    if not prod_ok:
        return "UNVERIFIED"
    if not sale_ok:
        return "UNVERIFIED"
    if not id_ok:
        return "PARTIAL_EVIDENCE"
    if r["_yb"] and r["_yb"] >= NEWYB and r["_fa"] \
            and r.get("asof_status") not in EXCL:
        return "VERIFIED_COMPARABLE" if not r.get("_yb_src") else \
               "PARTIAL_EVIDENCE"
    return "PARTIAL_EVIDENCE"


for r in COMPS:
    r["_tier"] = tier(r)

# ============ 瀑布(修正口径;与 sales_evidence 同一函数) ============
def usable(r):
    return (r["_prod"] in ("HOUSE", "DUPLEX", "MPX") and
            (r["_yb"] or 0) >= NEWYB and r["_fa"] and r["_p"] and
            r.get("asof_status") not in EXCL)


water = defaultdict(lambda: defaultdict(int))
for p in projects:
    for prod in p["products"].split("|"):
        if prod in ("HOUSE", "DUPLEX", "MPX"):
            water[(p["comm"], prod)]["projects"] += 1
for r in COMPS:
    w = water[(r["_comm"], r["_prod"])]
    w["comp_records"] += 1
    if r.get("asof_status") in EXCL:
        w["x_asof"] += 1; continue
    w["asof_ok"] += 1
    if r["gst_basis"] == "new_first_sale":
        w["new_first_sale"] += 1
    if (r["_yb"] or 0) >= NEWYB:
        w["yb2018+"] += 1
    if not r["_yb"]:
        w["yb_missing"] += 1
    if r["_fa"]:
        w["has_fa"] += 1
    if r["_lot"]:
        w["has_lot"] += 1
    if r["_p"]:
        w["has_price"] += 1
    if usable(r):
        w["usable_newbuild"] += 1

# ============ 销售证据(与瀑布同一口径→逐行核销) ============
sales_ev = []
for r in COMPS:
    if usable(r):
        sales_ev.append({
            "product": r["_prod"], "address": r["address"],
            "comm": r["_comm"], "sale_date": r["sale_date"],
            "sale_price": r["_p"], "floor_above_sf": r["_fa"],
            "lot_sqft": r["_lot"], "year_built": r["_yb"],
            "scope": r["_scope"], "tier": r["_tier"],
            "yb_src": r.get("_yb_src", ""), "fa_src": r.get("_fa_src", ""),
            "gst_basis": r["gst_basis"], "asof_status": r["asof_status"],
            "pair": r.get("_pair", ""), "pid": r["pid"],
            "strata_verdict": r.get("_strata_verdict", ""),
            "src": r["supplemental_source"] or "comps-v2"})

# ============ P3. 基准重建(锚点剔除+项目数+口径分列) ============
BANDS = [(0, 1600), (1600, 2000), (2000, 2400), (2400, 2800),
         (2800, 3400), (3400, 99999)]


def band_lo(fa):
    for lo, hi in BANDS:
        if lo <= fa < hi:
            return lo
    return None


def bench_cells(recs, label_comm):
    out = {}
    good = [r for r in recs if usable(r)
            and norm_addr(r["address"]) not in ANCHOR_EXCLUDE]
    for lo, hi in BANDS:
        inb = [r for r in good if lo <= r["_fa"] < hi]
        for wname, wstart in WIN.items():
            sel = [r for r in inb if r["sale_date"] >= wstart]
            if len(sel) < 3:
                continue
            psf = sorted(x["_p"] / x["_fa"] for x in sel)
            n = len(psf)
            fs = sum(1 for x in sel if x["gst_basis"] == "new_first_sale")
            out[f"{label_comm}|{lo}-{hi if hi < 99999 else 'up'}|{wname}"] = {
                "n_sales": n,
                "n_projects": len({x["_base"] for x in sel}),
                "n_first_sale": fs, "n_resale": n - fs,
                "p25": round(psf[n // 4]), "p50": round(psf[n // 2]),
                "p75": round(psf[3 * n // 4]),
                "fresh": max(x["sale_date"] for x in sel),
                "grade": "A" if n >= 8 else "B" if n >= 5 else "C"}
    return out


benchmarks = {}
by_cp = defaultdict(list)
for r in COMPS:
    if r["_prod"] in ("HOUSE", "DUPLEX", "MPX"):
        by_cp[(r["_comm"], r["_prod"])].append(r)
        by_cp[("_CITY", r["_prod"])].append(r)
for (comm, prod), recs in by_cp.items():
    for k, v in bench_cells(recs, comm).items():
        benchmarks[f"{k.split('|', 1)[0]}|{prod}|" +
                   k.split("|", 1)[1]] = {**v,
                   "borrowed": "BORROWED_CITY" if comm == "_CITY" else "OWN"}

# ============ 影子交叉(成本档:HOUSE/DUPLEX t350,MPX t400) ============
shadow = []
for s in V3S["sites"]:
    if s["status"] != "ACTIVE":
        continue
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
        ptype = "MPX" if p["product"] in ("TRIPLEX", "FOURPLEX",
                                          "MULTIPLEX") else p["product"]
        cost_tier = 350 if ptype in ("HOUSE", "DUPLEX") else 400
        fa = p["build_sf"] / (p["units"] or 1)
        blo = band_lo(fa)
        bmk, key = None, None
        for ck in (s["comm"], "_CITY"):
            for (lo, hi) in BANDS:
                if lo == blo:
                    hh = f"{lo}-{hi if hi < 99999 else 'up'}"
            k = f"{ck}|{ptype}|{hh}|2023+"
            if benchmarks.get(k):
                bmk, key = benchmarks[k], k
                break
        ec = dev_cost(c, s["ask"], p["build_sf"], p["units"], cost_tier)
        ind_p = bmk["p50"] * fa * (p["units"] or 1) if bmk else None
        shadow.append({
            "listing": s["address"], "plan": p["product"],
            "v3s_price": round(c), "bench_key": key,
            "borrowed": bmk["borrowed"] if bmk else "NONE",
            "bench_n": bmk["n_sales"] if bmk else 0,
            "bench_projects": bmk["n_projects"] if bmk else 0,
            "bench_p50_psf": bmk["p50"] if bmk else None,
            "indep_price": round(ind_p) if bmk else None,
            "v3s_roi": round(ec["roi"], 4),
            "indep_roi": round(dev_cost(ind_p, s["ask"], p["build_sf"],
                                        p["units"], cost_tier)["roi"], 4)
            if bmk else None,
            "diff": round(c / ind_p - 1, 3) if bmk else None,
            "grade": bmk["grade"] if bmk else "NOT_ESTIMABLE",
            "fresh": bmk["fresh"] if bmk else None})

# ============ coverage_v1_vs_v1_1 ============
# V1 口径:无恢复、旧 catchall 产品归类、无锚点剔除、yb 原始
cov = []
cells = sorted({(r["_comm"], r["_prod"]) for r in COMPS
                if r["_prod"] in ("HOUSE", "DUPLEX", "MPX")})
for comm, prod in cells:
    rs = [r for r in COMPS if r["_comm"] == comm and r["_prod"] == prod]
    v1_usable = sum(1 for r in rs
                    if (_num(r["year_built"]) or 0) >= NEWYB
                    and _num(r["floor_above_sf"]) and _num(r["sale_price"])
                    and r.get("asof_status") not in EXCL)
    v11_usable = sum(1 for r in rs if usable(r))
    recovered = sum(1 for r in rs if usable(r)
                    and (r.get("_yb_src") or r.get("_fa_src")
                         or r.get("_strata_verdict", "").startswith(
                             "CONFIRMED")))
    gap = ("yb" if sum(1 for r in rs if not r["_yb"]) >
           sum(1 for r in rs if not r["_fa"]) else "fa")
    cov.append({"comm": comm, "product": prod,
                "v1_usable": v1_usable, "v1_1_usable": v11_usable,
                "added": v11_usable - v1_usable,
                "recovered_records": recovered,
                "still_missing": sum(1 for r in rs
                                     if r.get("asof_status") not in EXCL
                                     and not usable(r)),
                "main_gap": f"missing_{gap} / 成交未捕获(MLS)"})

# ============ 写出 ============
def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


wcsv(OUTD / "project_registry.csv",
     [{k: v for k, v in p.items()} for p in projects])
wcsv(OUTD / "permit_project_reconciliation.csv", recon)
wcsv(OUTD / "unit_registry.csv", units)
wcsv(OUTD / "folio_audit.csv", folio_audit)
wcsv(OUTD / "yb_recovery_log.csv", yb_log)
wcsv(OUTD / "strata_classification_audit.csv",
     strata_audit + [{"address": f"[EXCLUDED-POOL n={len(arc_excl)}]",
                      "date": "", "price": "", "pid": "", "yb": "",
                      "verdict": k, "evidence": "", "desc": str(v)}
                     for k, v in excl_v.items()])
wcsv(OUTD / "arcgis_excluded_audit.csv", arc_excl_audit)
wcsv(OUTD / "product_recheck.csv", recheck)
wcsv(OUTD / "coverage_waterfall.csv",
     [{"comm": c, "product": p, **v} for (c, p), v in sorted(water.items())])
wcsv(OUTD / "sales_evidence.csv", sales_ev)
wcsv(OUTD / "regional_benchmark_v1_1.csv",
     [{"key": k, **v} for k, v in sorted(benchmarks.items())])
wcsv(OUTD / "shadow_impact.csv", shadow)
wcsv(OUTD / "coverage_v1_vs_v1_1.csv", cov)

summary = {
    "snapshot_date": SNAPSHOT, "windows": WIN,
    "permits": {"records": len(precs), "uniq_projects": len(projects),
                "uniq_addrs": len(proj_by_addr),
                "products": dict(pcnt),
                "units_est": sum(p["units_est"] for p in projects)},
    "folio_audit": folio_audit,
    "yb_recovery": yb_rec,
    "fa_recovery_enriched": fa_rec,
    "duplex": {"projects": len(duplex_projects),
               "paired": sum(1 for v in duplex_projects.values()
                             if v["halves"] >= 2),
               "whole_bldg": sum(1 for v in duplex_projects.values()
                                 if v["wholes"])},
    "strata_635": dict(Counter(a["verdict"] for a in strata_audit)),
    "arcgis_excluded_5613": dict(excl_v),
    "sales_evidence": dict(Counter(s["product"] for s in sales_ev)),
    "usable_by_product": dict(Counter(
        s["product"] for s in sales_ev)),
    "benchmark_cells": len(benchmarks),
    "shadow_rows": len(shadow),
    "shadow_estimable": sum(1 for s in shadow if s["bench_n"]),
}
(OUTD / "evidence_recovery_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1))

manifest = {
    "version": "v1.1", "run": datetime.datetime.now().isoformat(),
    "snapshot_date": SNAPSHOT, "windows": WIN,
    "input_sha256": HASHES,
    "inputs": {k: str(v.relative_to(ROOT)) for k, v in INPUTS.items()},
    "counts": summary,
}
(OUTD / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
print("wrote audit_v1_1/*")
