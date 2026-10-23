#!/usr/bin/env python3
"""HOUSE Newbuild Discovery V2 — 建筑评估价值突变 + 反向核销。

输入(全本地冻结):
- property-tax-2023..2026-full.csv(~897k 行;impv/land/yb/
  big_impr/folio/land_coordinate/plan+lot/门牌)
- sales_archive_v1_0_3/sales_archive.csv(全部 24,203 canonical)
- house_5386_census_per_row.csv(Issue#1 普查逐笔)
- audit_v1_2/sales_evidence.csv(217)
- enriched/candidates 池(地上面积口径)
- issued-permits-ldh-2017.csv

规则:P0-B 动态阈值 C_ref=above_grade_fa×250;
STRONG≥100%C_ref,MODERATE≥50%;另有 50/75/100% 敏感性;
近零基线/滞后/面积缺失单列;阈值只是筛查非确认。
P0-C 血统:folio/land_coordinate/plan+lot/地址 连接新旧 PID。
P0-D 双轨:5,386 挂载 + 全市突变反向查成交(全产品)。
P1 四阶段:CONSTRUCTION_DETECTED → NEW_BUILDING_VERIFIED →
SALE_EVENT_VERIFIED → NEW_HOUSE_SALE_VERIFIED。
"""
import csv, json, re, sys, hashlib, datetime
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402
import archive_core as AC                             # noqa: E402

OUTD = ROOT / "data/research/normalized/assessment_discovery_v2"
OUTD.mkdir(parents=True, exist_ok=True)
DOCS = ROOT / "docs/audits/house_assessment_discovery_v2"
DOCS.mkdir(parents=True, exist_ok=True)
CUTOFF_2026 = "2026-07-08"
COST_PSF = 250.0
YEARS = (2023, 2024, 2025, 2026)

IN = {"archive": ROOT / "data/research/normalized/"
      "sales_archive_v1_0_3/sales_archive.csv",
      "census": ROOT / "data/research/normalized/"
      "house_5386_census/house_5386_census_per_row.csv",
      "nb217": ROOT / "data/research/normalized/audit_v1_2/"
      "sales_evidence.csv",
      "permits": ROOT / "data/research/raw/official/"
      "issued-permits-ldh-2017.csv"}
for y in YEARS:
    IN[f"tax{y}"] = ROOT / "data/research/raw/official/" \
                          f"property-tax-{y}-full.csv"
POOL_FILES = [
    "bca-sales-24mo-newbuild-enriched.json",
    "bca-sales-2023band-newbuild-enriched.json",
    "bca-sales-lastyear-newbuild-enriched.json",
    "bca-sales-24mo-newbuild-candidates.json",
    "bca-sales-2023band-newbuild-candidates.json",
    "bca-sales-lastyear-newbuild-candidates.json"]
HASHES = {k: hashlib.sha256(p.read_bytes()).hexdigest()
          for k, p in IN.items() if p.exists()}
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if p.exists():
        HASHES[f] = hashlib.sha256(p.read_bytes()).hexdigest()


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iyear(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def tier(g):
    return (g or "").split(":")[0]


def wcsv(path, rows):
    if not rows:
        path.write_text("")
        return
    f0 = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=f0, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_tax(y):
    f = open(IN[f"tax{y}"], newline="", encoding="utf-8-sig")
    d = csv.Sniffer().sniff(f.read(4000), delimiters=",;\t")
    f.seek(0)
    return csv.DictReader(f, dialect=d)


# ============ P0-A:全市轨迹 + prev↔current 口径验证 ============
# pid -> {year: {...}};留全市(不仅 5,386)
traj = defaultdict(dict)
folio_pids = defaultdict(set)     # 血统:folio -> {pid}
loco_pids = defaultdict(set)
planlot_pids = defaultdict(set)
addr_pids = defaultdict(set)
pid_info = {}                     # pid -> {folio,loco,plan,lot,addr}
prev_check = {"match": 0, "diff": 0, "na": 0}
fields_seen = set()
fields_by_year = {}
audit_year = {}        # y -> {rows, nopid, miss_impv, miss_yb,
                       #       assess_years, report_years}
for y in YEARS:
    ay = {"rows": 0, "nopid": 0, "miss_impv": 0,
          "miss_yb": 0, "assess_years": set(),
          "report_years": set(), "fields": set()}
    audit_year[y] = ay
    fields_by_year[y] = ay["fields"]
    for t in read_tax(y):
        ay["rows"] += 1
        ay["fields"].update(t.keys())
        ay["assess_years"].add(t.get("tax_assessment_year") or "")
        ay["report_years"].add(t.get("report_year") or "")
        p = t["pid"]
        if not p:
            ay["nopid"] += 1
            continue
        fields_seen.update(t.keys())
        impv = fnum(t.get("current_improvement_value"))
        land = fnum(t.get("current_land_value"))
        prev = fnum(t.get("previous_improvement_value"))
        old = traj[p].get(y - 1)
        if prev is not None and old and \
                old.get("impv") is not None:
            if abs(prev - old["impv"]) < 1:
                prev_check["match"] += 1
            else:
                prev_check["diff"] += 1
        elif prev is not None:
            prev_check["na"] += 1
        yb = iyear(t.get("year_built"))
        if impv is None:
            ay["miss_impv"] += 1
        if not yb:
            ay["miss_yb"] += 1
        traj[p][y] = {
            "impv": impv, "land": land, "prev_impv": prev,
            "yb": yb, "big": iyear(t.get("big_improvement_year")),
            "folio": t.get("folio") or "",
            "loco": t.get("land_coordinate") or "",
            "plan": t.get("plan") or "",
            "lot": t.get("lot") or "",
            "legal": t.get("legal_type") or "",
            "zoning": t.get("zoning_district") or "",
            "civic_lo": t.get("from_civic_number") or "",
            "civic_hi": t.get("to_civic_number") or "",
            "street": t.get("street_name") or ""}
        info = traj[p][y]
        pid_info[p] = {k: info[k] for k in
                       ("folio", "loco", "plan", "lot",
                        "street", "civic_lo", "zoning", "legal")}
        if info["folio"]:
            folio_pids[info["folio"]].add(p)
        if info["loco"]:
            loco_pids[info["loco"]].add(p)
        if info["plan"] and info["lot"]:
            planlot_pids[(info["plan"], info["lot"])].add(p)
        if info["street"] and info["civic_lo"]:
            addr_pids[(info["street"],
                       info["civic_lo"])].add(p)

KEY_FIELDS = ["pid", "folio", "land_coordinate", "plan", "lot",
              "legal_type", "current_improvement_value",
              "previous_improvement_value", "current_land_value",
              "previous_land_value", "year_built",
              "big_improvement_year", "tax_assessment_year",
              "report_year", "zoning_district",
              "from_civic_number", "street_name"]
schema_lines = ["# Tax field schema audit", "",
                "## 逐年行数 / 口径字段 / 缺失", "",
                "| 快照年 | rows | 无pid | 缺impv | 缺yb |"
                " assessment_year 值 | report_year 值 |",
                "|---|---|---|---|---|---|---|"]
for y in YEARS:
    ay = audit_year[y]
    schema_lines.append(
        f"| {y} | {ay['rows']} | {ay['nopid']} |"
        f" {ay['miss_impv']} | {ay['miss_yb']} |"
        f" {sorted(x for x in ay['assess_years'] if x)} |"
        f" {sorted(x for x in ay['report_years'] if x)} |")
schema_lines += ["", "## 关键字段逐年存在性", "",
                 "| field | " + " | ".join(map(str, YEARS)) + " |",
                 "|---|" + "---|" * len(YEARS)]
for f in KEY_FIELDS:
    schema_lines.append(
        f"| {f} | " + " | ".join(
            "Y" if f in fields_by_year[y] else "-"
            for y in YEARS) + " |")
schema_lines += ["",
                 f"all_fields={sorted(fields_seen)}",
                 f"distinct_pids={len(traj)}",
                 "", "## prev↔prior-current 跨年口径验证",
                 f"{prev_check}",
                 "- match: 当年 previous_improvement_value"
                 " 与上年 current 完全一致 → 跨年可比;",
                 "- diff: 不一致(改判/补录),不计跨年差;",
                 "- na: 无上年快照可对比(2023 全年+新增pid)。",
                 "value 基准日=assessment_year;"
                 "快照时点=文件对应 report_year。"]
(DOCS / "assessment_field_schema_audit.md").write_text(
    "\n".join(schema_lines))

# ============ 输入:archive/census/nb217/pool/permits ============
sales = list(csv.DictReader(open(IN["archive"])))
sales_by_pid = defaultdict(list)
sales_by_addr = defaultdict(list)
for s in sales:
    if s["pid"]:
        sales_by_pid[s["pid"]].append(s)
    if s["norm_addr"]:
        sales_by_addr[s["norm_addr"]].append(s)
house = [s for s in sales if s["product"] == "HOUSE"]
census = {r["canonical_tx"]: r for r in
          csv.DictReader(open(IN["census"]))}
nb217 = {r["pid"]: r for r in
         csv.DictReader(open(IN["nb217"]))
         if r["product"] == "HOUSE" and r["pid"]}
fa_by_pid = {}
for s in house:
    if s["pid"] and fnum(s["fa"]):
        fa_by_pid[s["pid"]] = fnum(s["fa"])
for f in POOL_FILES:
    p = ROOT / "data/research/raw/location" / f
    if not p.exists():
        continue
    d = json.load(open(p))
    for r in (d.get("sales", []) if isinstance(d, dict) else d):
        pid = r.get("pid")
        if not pid:
            continue
        fa = fnum(r.get("bca_first_floor_area_sqft")) or 0
        fa += fnum(r.get("bca_second_floor_area_sqft")) or 0
        if fa:
            fa_by_pid.setdefault(pid, fa)
permits = defaultdict(list)
pf = open(IN["permits"], newline="", encoding="utf-8-sig")
pd_ = csv.Sniffer().sniff(pf.read(4000), delimiters=",;\t")
pf.seek(0)
for r in csv.DictReader(pf, dialect=pd_):
    a = (r.get("address") or "").split(",")[0]
    na = AN.parse(a)["normalized_address"] if a.strip() else ""
    iy = iyear(r.get("issueyear") or (r.get("issuedate") or "")[:4])
    if na and iy:
        permits[na].append((iy, r.get("specificusecategory") or ""))

# ============ P0-B:突变度量 ============
def jump_metrics(series):
    """series: [(year, impv)] 排序后;返回度量 dict。"""
    vals = [(y, v) for y, v in sorted(series) if v is not None]
    if len(vals) < 2:
        return None
    first, last = vals[0], vals[-1]
    yoy = [vals[i][1] - vals[i - 1][1]
           for i in range(1, len(vals))]
    jump_year = vals[1 + yoy.index(max(yoy))][0] \
        if max(yoy) > 0 else None
    tot = last[1] - first[1]
    staged = sum(d for d in yoy if d > 0)   # 分阶段累计
    base = first[1]
    return {"first_year": first[0], "first_impv": first[1],
            "last_year": last[0], "last_impv": last[1],
            "max_yoy_delta": max(yoy), "jump_year": jump_year,
            "total_delta": tot, "staged_delta": staged,
            "r_growth": (tot / base if base and base > 0
                         else None),
            "near_zero_base": bool(base is not None and
                                   base < 50000)}


def jump_grade(m, c_ref, last_year=None):
    """阈值分级;无 C_ref 用绝对档兜底但标 AREA_MISSING。
    last_year=全局最新快照年;轨迹止于更早→ASSESSMENT_LAG。"""
    if m is None:
        return "NO_BASELINE_ASSESSMENT"
    d = m["staged_delta"]
    if c_ref:
        r = d / c_ref
        g = ("STRONG_REBUILD_SIGNAL" if r >= 1.0
             else "MODERATE_REBUILD_SIGNAL" if r >= 0.5
             else "LOW_VALUE_CHANGE")
    elif d >= 500000:
        g = "STRONG_REBUILD_SIGNAL:AREA_MISSING"
    elif d >= 250000:
        g = "MODERATE_REBUILD_SIGNAL:AREA_MISSING"
    else:
        g = "LOW_VALUE_CHANGE"
    if last_year and m["last_year"] < last_year:
        g += ":ASSESSMENT_LAG"
    return g


# ============ P0-C:血统(PID 变更 successor) ============
# pid 出现/消失于同一 folio/loco/planlot → predecessor 边
lineage = {}          # pid -> {"succ": pid or "", "key": "", "kind": ""}
for p, years in traj.items():
    yrs = sorted(years)
    if len(yrs) < 1:
        continue
    last_y, first_y = yrs[-1], yrs[0]
    if first_y == YEARS[0] and last_y == YEARS[-1]:
        continue                       # 全程存在,无变更
    if last_y < YEARS[-1]:             # pid 消失→找 successor
        info = pid_info[p]
        for key_map, key in (
                (folio_pids, info["folio"]),
                (loco_pids, info["loco"]),
                (planlot_pids, (info["plan"], info["lot"]))):
            for q in key_map.get(key, ()):  # 同 key 其他 pid
                if q != p and q in traj and \
                        min(traj[q]) > last_y:
                    lineage[p] = {"succ": q, "key": str(key),
                                  "kind": "pid_vanished"}
                    break
            if p in lineage:
                break

# ============ P0-D-A:5,386 挂载 ============
# 反向血统 succ→pred,用于合并重建前后的跨 pid 轨迹
pred_map = {v["succ"]: k for k, v in lineage.items()}
house_rows = []
for s in house:
    pid = s["pid"]
    sy = int(s["sale_date"][:4])
    series = [(y, v["impv"]) for y, v in
              traj.get(pid, {}).items()]
    via_lineage = ""
    if pid in lineage:                          # pid 消失→并入后继
        via_lineage = lineage[pid]["succ"]
    elif pid in pred_map:                       # 新 pid→并入前身
        via_lineage = pred_map[pid]
    if via_lineage:
        series += [(y, v["impv"]) for y, v in
                   traj.get(via_lineage, {}).items()]
        series.sort()
    m = jump_metrics(series)
    fa = fa_by_pid.get(pid)
    c_ref = fa * COST_PSF if fa else None
    grade = jump_grade(m, c_ref, YEARS[-1])
    cens = census.get(s["canonical_tx"], {})
    ybs = [v["yb"] for v in traj.get(pid, {}).values()
           if v["yb"]]
    yb_changed = len(set(ybs)) > 1 if ybs else False
    big_yb = [v["big"] for v in traj.get(pid, {}).values()
              if v["big"]]
    na = s["norm_addr"]
    pm = permits.get(na, [])
    permit_pre = [iy for iy, c in pm if iy <= sy and
                  "Single Detached" in c]
    permit_any = [iy for iy, c in pm if iy <= sy]
    jy = m["jump_year"] if m else None
    strong_j = tier(grade) in (
        "STRONG_REBUILD_SIGNAL", "MODERATE_REBUILD_SIGNAL")
    # 四阶段(证据独立)
    g1 = bool(strong_j or permit_pre or yb_changed or
              (m and m["near_zero_base"] and
               m["staged_delta"] > 200000))       # CONSTRUCTION_DETECTED
    roll_new_yb = [v["yb"] for v in traj.get(pid, {}).values()
                   if v["yb"] and v["yb"] >= sy - 3 and
                   v["yb"] <= sy]
    new_bldg = bool(
        cens.get("classification") == "CONFIRMED_AGE_00_03"
        or (yb_changed and roll_new_yb)
        or (strong_j and permit_pre))            # NEW_BUILDING_VERIFIED
    g2 = new_bldg
    g3 = True                                    # canonical=成交已证
    # 建成先于成交:yb≤sy 证据 或 突变年度≤sy+1(评估可滞后)
    done_before_sale = bool(
        roll_new_yb or
        (m and jy and jy <= sy + 1 and
         cens.get("age_band_after") == "AGE_00_03") or
        (m and jy and jy <= sy and strong_j))
    g4 = bool(g2 and g3 and done_before_sale)    # NEW_HOUSE_SALE_VERIFIED
    house_rows.append({
        "canonical_tx": s["canonical_tx"], "pid": pid,
        "community": s["community"], "sale_year": sy,
        "sale_kind": s["sale_kind"],
        "census_class": cens.get("classification", "?"),
        "band_before": s["age_band"],
        "band_after": cens.get("age_band_after", ""),
        "first_impv": m["first_impv"] if m else "",
        "last_impv": m["last_impv"] if m else "",
        "staged_delta": m["staged_delta"] if m else "",
        "r_growth": round(m["r_growth"], 2)
        if m and m["r_growth"] else "",
        "jump_year": jy or "", "fa": fa or "",
        "c_ref": round(c_ref) if c_ref else "",
        "grade": grade, "yb_changed": yb_changed,
        "lineage_linked": bool(via_lineage),
        "permit_sdh_pre": max(permit_pre) if permit_pre else "",
        "construction_detected": g1,
        "new_building_verified": g2,
        "sale_event_verified": g3,
        "new_house_sale_verified": g4})

wcsv(OUTD / "house_assessment_per_row.csv", house_rows)

# ============ P0-D-B:全市反向发现 ============
# 对全 pid 算突变(为速度:只看有 jump 可能的)
jump_all = {}
for p, years in traj.items():
    m = jump_metrics([(y, v["impv"]) for y, v in years.items()])
    if not m or m["staged_delta"] <= 0:
        continue
    fa = fa_by_pid.get(p)
    c_ref = fa * COST_PSF if fa else None
    g = jump_grade(m, c_ref, YEARS[-1])
    if not g.startswith(("STRONG", "MODERATE")) and \
            not (m["near_zero_base"] and m["staged_delta"] > 200000):
        continue
    jump_all[p] = {**m, "grade": g,
                   "c_ref": c_ref, "fa": fa}
print(f"jump parcels={len(jump_all)}")

# 反向找成交:pid 直查 + 血统扩展 pid 集
# (dedup 按 canonical_tx — 一宗地可能链多 pid/多事件)
rev = []
for p, m in jump_all.items():
    info = pid_info.get(p, {})
    pid_set = {p}
    for mp, key in ((folio_pids, info.get("folio")),
                    (loco_pids, info.get("loco")),
                    (planlot_pids,
                     (info.get("plan"), info.get("lot"))),
                    (addr_pids,
                     (info.get("street"),
                      info.get("civic_lo")))):
        pid_set |= mp.get(key, set())
    if p in lineage:
        pid_set.add(lineage[p]["succ"])       # 消失 pid→后继
    if p in pred_map:
        pid_set.add(pred_map[p])              # 新 pid→前身
    seen = {}
    for q in pid_set:
        for s in sales_by_pid.get(q, []):
            seen[s["canonical_tx"]] = s
    hits = list(seen.values())
    if hits:
        for s in hits:
            sy = int(s["sale_date"][:4])
            cens = census.get(s["canonical_tx"], {})
            rev.append({
                "jump_pid": p, "sale_pid": s["pid"],
                "canonical_tx": s["canonical_tx"],
                "sale_year": sy, "product": s["product"],
                "census_class": cens.get("classification", ""),
                "band_after": cens.get("age_band_after", ""),
                "grade": m["grade"], "jump_year": m["jump_year"],
                "staged_delta": round(m["staged_delta"]),
                "r_growth": round(m["r_growth"], 2)
                if m["r_growth"] else "",
                "sale_kind": s["sale_kind"],
                "community": s["community"],
                "pid_changed": s["pid"] != p,
                "link": "pid" if s["pid"] == p else
                        "lineage"})
    else:
        rev.append({"jump_pid": p, "sale_pid": "",
                    "canonical_tx": "", "sale_year": "",
                    "product": "", "census_class": "",
                    "band_after": "", "grade": m["grade"],
                    "jump_year": m["jump_year"],
                    "staged_delta": round(m["staged_delta"]),
                    "r_growth": round(m["r_growth"], 2)
                    if m["r_growth"] else "",
                    "sale_kind": "", "community": "",
                    "pid_changed": "", "link": "none",
                    "no_sale": True})
# 时序分类:评估突变晚于成交 → 买家重建,成交的是旧房/土地;
# 早于/同年 → 建成可能先于成交,属疑似新房成交(仍待 yb)
for r in rev:
    if r.get("no_sale"):
        continue
    jy, sy = r["jump_year"], r["sale_year"]
    if jy and sy and int(jy) > int(sy):
        r["rev_class"] = "POST_SALE_BUILD_SALE"
    elif jy and sy:
        r["rev_class"] = "PRE_SALE_IMPROVEMENT_SUSPECT"
    else:
        r["rev_class"] = "TIMING_UNKNOWN"
wcsv(OUTD / "citywide_reverse_per_row.csv", rev)
# 反向命中的唯一成交
rev_sales = {r["canonical_tx"]: r for r in rev
             if not r.get("no_sale")}
# 新增 VERIFIED:有 ≤sy 新房龄证据但普查未 CONFIRMED
# (普查已含 tax/enriched/nb 证据,理论上≈0,如实输出)
new_verified = [r for r in rev_sales.values()
                if r["product"] == "HOUSE" and
                r["census_class"] not in
                ("CONFIRMED_AGE_00_03", "PROBABLE_AGE_00_03")
                and r["band_after"] == "AGE_00_03"]
wcsv(OUTD / "citywide_new_verified_candidates.csv",
     new_verified)

# ============ P1/P2:回测与汇总 ============
back = Counter()
for r in house_rows:
    cls = r["census_class"]
    g = tier(r["grade"])
    if cls == "CONFIRMED_AGE_00_03":
        back["confirmed_128:" + g] += 1
    elif cls == "REVIEW_REBUILD_STALE_YB":
        back["review_52:" + g] += 1
    elif cls == "AGE_UNKNOWN":
        back["unknown_259:" + g] += 1
    elif cls in ("AGE_31_50", "AGE_51_PLUS"):
        back["old_band:" + g] += 1
    elif cls in ("LAND_OLD_HOUSE_SALE", "POST_SALE_BUILD"):
        back["land_old:" + g] += 1
    else:
        back["other_band:" + g] += 1

# 敏感性:全 house 在 50/75/100% C_ref 的分布
sens = defaultdict(Counter)
for r in house_rows:
    fa = r["fa"]
    if not fa or not r["staged_delta"]:
        continue
    d = float(r["staged_delta"])
    for pct in (50, 75, 100):
        tag = f">={pct}%C_ref" if d >= fa * COST_PSF * pct / 100 \
            else None
        if tag:
            sens[pct][r["census_class"]] += 1
wcsv(DOCS / "assessment_jump_threshold_sensitivity.csv",
     [{"threshold": f"{p}%C_ref", "class": c, "n": n}
      for p in (50, 75, 100)
      for c, n in sorted(sens[p].items())])

# 轨迹文件(仅 jump 候选,匿名化推送版无地址)
traj_rows = []
for p, m in jump_all.items():
    info = pid_info.get(p, {})
    traj_rows.append({
        "parcel_ref": hashlib.sha1(p.encode()).hexdigest()[:12],
        "first_year": m["first_year"],
        "first_impv": m["first_impv"],
        "last_year": m["last_year"],
        "last_impv": m["last_impv"],
        "staged_delta": round(m["staged_delta"]),
        "max_yoy_delta": round(m["max_yoy_delta"]),
        "jump_year": m["jump_year"],
        "r_growth": round(m["r_growth"], 2)
        if m["r_growth"] else "",
        "grade": m["grade"],
        "yb_seen": "|".join(str(traj[p][y]["yb"]) for y in
                          sorted(traj[p])),
        "zoning": info.get("zoning", ""),
        "n_linked_pids": len(loco_pids.get(
            info.get("loco"), ())) if info.get("loco") else ""})
wcsv(DOCS / "tax_improvement_value_trajectory.csv", traj_rows)
wcsv(OUTD / "tax_improvement_value_trajectory_full.csv",
     [{"pid": p, **{f"{k}_{y}": traj[p].get(y, {}).get(k, "")
                    for y in YEARS for k in
                    ("impv", "land", "yb", "big")}}
      for p in jump_all])

# house 瀑布
hw = Counter()
for r in house_rows:
    hw[(r["census_class"], tier(r["grade"]))] += 1
wcsv(DOCS / "house_5386_assessment_match_waterfall.csv",
     [{"census_class": c, "jump_grade": g, "n": n}
      for (c, g), n in sorted(hw.items())])

# 52 REVIEW + 259 UNKNOWN 对账(匿名)
def _anon(rows, cls):
    out = []
    for i, r in enumerate(x for x in rows
                          if x["census_class"] == cls):
        out.append({"row": i + 1, "sale_year": r["sale_year"],
                    "community": r["community"],
                    "jump_grade": r["grade"],
                    "staged_delta": r["staged_delta"],
                    "r_growth": r["r_growth"],
                    "jump_year": r["jump_year"],
                    "yb_changed": r["yb_changed"],
                    "permit_sdh_pre": r["permit_sdh_pre"],
                    "construction_detected":
                        r["construction_detected"],
                    "new_building_verified":
                        r["new_building_verified"],
                    "sale_kind": r["sale_kind"]})
    return out


wcsv(DOCS / "house_52_review_reconciliation.csv",
     _anon(house_rows, "REVIEW_REBUILD_STALE_YB"))
wcsv(DOCS / "house_259_unknown_reconciliation.csv",
     _anon(house_rows, "AGE_UNKNOWN"))

# 反向发现聚合(分类×年×社区,匿名聚合)
rev_agg = Counter()
for r in rev:
    if r.get("no_sale"):
        rev_agg[("CONSTRUCTION_NO_SALE", "", "")] += 1
        continue
    newish = r["band_after"] == "AGE_00_03" or \
        r["census_class"] in ("CONFIRMED_AGE_00_03",
                              "PROBABLE_AGE_00_03")
    if r["product"] != "HOUSE":
        cat = "SOLD_MISCLASS_" + r["product"]
    elif newish:
        cat = "SOLD_HOUSE_00_03"
    elif r["rev_class"] == "POST_SALE_BUILD_SALE":
        cat = "SOLD_HOUSE_POST_SALE_BUILD"
    elif r["rev_class"] == "PRE_SALE_IMPROVEMENT_SUSPECT":
        cat = "SOLD_HOUSE_SUSPECT_NEW_PRESALE"
    else:
        cat = "SOLD_HOUSE_TIMING_UNKNOWN"
    rev_agg[(cat, str(r["sale_year"]),
             r["community"] or "?")] += 1
wcsv(DOCS / "citywide_assessment_reverse_discovery.csv",
     [{"category": c, "year": y, "community": cm, "n": n}
      for (c, y, cm), n in sorted(rev_agg.items())])

# 血统审计(匿名):predecessor→successor 边 +
# 两端是否 jump 地块 / 是否命中成交
def _h(x):
    return hashlib.sha1(x.encode()).hexdigest()[:12]


lin_rows = [{"from": _h(k), "to": _h(v["succ"]),
             "key": v["kind"],
             "succ_is_jump": int(v["succ"] in jump_all),
             "pred_is_jump": int(k in jump_all),
             "either_has_sale": int(
                 bool(sales_by_pid.get(k)) or
                 bool(sales_by_pid.get(v["succ"])))}
            for k, v in lineage.items()]
wcsv(DOCS / "parcel_pid_rebuild_lineage_audit.csv", lin_rows)

# 四阶段 gate
gate = Counter()
for r in house_rows:
    gate["ALL"] += 1
    if r["construction_detected"]:
        gate["CONSTRUCTION_DETECTED"] += 1
    if r["new_building_verified"]:
        gate["NEW_BUILDING_VERIFIED"] += 1
    if r["sale_event_verified"]:
        gate["SALE_EVENT_VERIFIED"] += 1
    if r["new_house_sale_verified"]:
        gate["NEW_HOUSE_SALE_VERIFIED"] += 1
wcsv(DOCS / "newbuild_sale_evidence_gate.csv",
     [{"stage": k, "n": gate[k]} for k in (
         "ALL", "CONSTRUCTION_DETECTED",
         "NEW_BUILDING_VERIFIED", "SALE_EVENT_VERIFIED",
         "NEW_HOUSE_SALE_VERIFIED")])

# 128 before/after:原档案房龄档 vs 普查纠偏档 vs 跳变等级
b128 = Counter()
for r in house_rows:
    if r["census_class"] == "CONFIRMED_AGE_00_03":
        b128[(r["band_before"] or "?", tier(r["grade"]))] += 1
wcsv(DOCS / "newbuild_128_before_after.csv",
     [{"band_before": b, "jump_grade": g, "n": n}
      for (b, g), n in sorted(b128.items())])

# 瀑布 P3 数字
n_conf_jump = sum(1 for r in house_rows
                  if r["census_class"] == "CONFIRMED_AGE_00_03"
                  and tier(r["grade"]).startswith(
                      ("STRONG", "MODERATE")))
n_rev_jump = sum(1 for r in house_rows
                 if r["census_class"] ==
                 "REVIEW_REBUILD_STALE_YB" and
                 tier(r["grade"]).startswith(("STRONG",
                                              "MODERATE")))
n_unk_jump = sum(1 for r in house_rows
                 if r["census_class"] == "AGE_UNKNOWN" and
                 tier(r["grade"]).startswith(("STRONG",
                                              "MODERATE")))
n_old_jump = sum(1 for r in house_rows
                 if r["census_class"] in
                 ("AGE_31_50", "AGE_51_PLUS") and
                 tier(r["grade"]).startswith(("STRONG",
                                              "MODERATE")))
n_city_nosale = sum(1 for r in rev if r.get("no_sale"))
n_city_sold = len(rev_sales)
n_city_new_confirmed = len(new_verified)

# 分年 × 类别的反查成交统计
rev_year = Counter()
for r in rev:
    if r.get("no_sale"):
        continue
    if r["product"] != "HOUSE":
        cat = "misclass_" + r["product"]
    elif r["census_class"] in ("CONFIRMED_AGE_00_03",
                              "PROBABLE_AGE_00_03"):
        cat = "house_00_03_confirmed"
    else:
        cat = "house_" + (r["rev_class"] or "?").lower()
    rev_year[(cat, r["sale_year"])] += 1

# 真新房首销:CONFIRMED_00_03 + FIRST_SALE
n_first_sale = sum(1 for r in house_rows
                   if r["census_class"] == "CONFIRMED_AGE_00_03"
                   and str(r["sale_kind"]).startswith("FIRST"))
n_resale_003 = sum(1 for r in house_rows
                   if r["census_class"] == "CONFIRMED_AGE_00_03"
                   and not str(r["sale_kind"]).startswith("FIRST"))
# 负例:land_old(土地/旧房成交)中 STRONG = 买家重建
n_land_strong = sum(1 for r in house_rows
                    if r["census_class"] == "LAND_OLD_HOUSE_SALE"
                    and tier(r["grade"]) == "STRONG_REBUILD_SIGNAL")
n_staged = sum(1 for r in house_rows
               if r["census_class"] == "CONFIRMED_AGE_00_03"
               and r["grade"].endswith("STAGED"))
year_tab = Counter()
for r in house_rows:
    if r["census_class"] in ("CONFIRMED_AGE_00_03",
                            "PROBABLE_AGE_00_03"):
        year_tab[(r["census_class"], r["sale_year"])] += 1

rep = f"""# Assessment Jump Discovery V2 报告

基线:816f6ae;档案/普查冻结,本轮=评估价值研究层。
筛查阈值非确认规则;无 SOLD 证据不计成交。
2026 cutoff={CUTOFF_2026};2023 前无更早快照 →
NO_BASELINE_ASSESSMENT 单列。

## Schema 审计(prev↔prior-current 跨年口径)
{prev_check}
一致率 ≥99% 的年份对可直接跨年比较;差异行计入注记。

## 全市突变规模
- jump 候选地块:{len(jump_all)}
  (STRONG/MODERATE 或近零基线→显著非零)
- 反向找到档案内成交(唯一 canonical_tx):{n_city_sold}
- 有建筑变化但无 SOLD 证据:{n_city_nosale}(不计入销量)
- PID 变更血统边(predecessor→successor):{len(lineage)}

## 四阶段证据门(全 5,386 挂载)
{dict(gate)}
NEW_HOUSE_SALE_VERIFIED={gate['NEW_HOUSE_SALE_VERIFIED']}
严格可比口径(普查已证 ≤ 成交年的新房龄证据)。

## P3 瀑布回答(实测)
| 问 | 数 |
|---|---|
| 原 128 条中估值明显跳涨(STRONG+MODERATE) | {n_conf_jump} |
| 128 条召回率 | {n_conf_jump}/128 = {n_conf_jump/128:.0%} |
| 52 REVIEW 获新增建设证据 | {n_rev_jump} |
| 259 UNKNOWN 获建设证据 | {n_unk_jump} |
| 旧房组(31_50/51+)疑似重建成交 | {n_old_jump} |
| 全市突变反查命中档案内成交(唯一) | {n_city_sold} |
| ├ 其中已证 0-3 新房成交(事件行) | {sum(n for (c,y),n in rev_year.items() if c=='house_00_03_confirmed')} |
| ├ 其中非 HOUSE 产品(误分排查,事件行) | {sum(n for (c,y),n in rev_year.items() if c.startswith('misclass'))} |
| └ 其中疑似新房成交(建成先于成交,事件行) | {sum(n for (c,y),n in rev_year.items() if c=='house_pre_sale_improvement_suspect')} |
| 有建筑变化但无 SOLD 证据 | {n_city_nosale} |
| 新增 VERIFIED 0-3 HOUSE 成交 | {n_city_new_confirmed} |
| 真新房首销(FIRST_SALE ∩ CONFIRMED_00_03) | {n_first_sale} |
| └ 0-3 中非首销(转售/未知) | {n_resale_003} |
| 负例:土地/旧房成交后买家重建(STRONG) | {n_land_strong} |

## 分年 0-3 成交
{chr(10).join(f"- {cls} {y}: {n}" for (cls, y), n in sorted(year_tab.items()))}

## 阈值敏感性
见 assessment_jump_threshold_sensitivity.csv
(50%/75%/100%×C_ref;128 条中多数未达 STRONG 阈,
按 P2 要求保留 MODERATE/LOW 候选层不牺牲召回)。

## 血统(P0-C)
{len(lineage)} 条 predecessor→successor 边
(folio/land_coordinate/plan+lot 同键、时间互补)。
见 parcel_pid_rebuild_lineage_audit.csv(匿名哈希)。

## 盲区(诚实声明)
- 2023 年无 pre-2023 基线:只能看 2024-26 三年窗口;
- 2026 成交{ CUTOFF_2026 }截断,且新建成价值可能
  需 2027+ 快照才显化 → 当年 jump 召回偏低;
- 评估滞后:突变年度≠竣工年度,jump_year 是卷面
  追认时间不是开工/完工时间;
- 无 pid 行(地址级)无法纳入血统;
- duplex/multiplex 单元跳变会命中多 pid,
  已按 canonical_tx 去重但单元口径需人工复核;
- 反查只覆盖已保存成交池(BCA/MLS/官方),
  未保存来源的成交不在反查域内。
"""
(DOCS / "assessment_discovery_v2_report.md").write_text(rep)

manifest = {
    "version": "assessment_discovery_v2",
    "run": datetime.datetime.now().isoformat(),
    "baseline": "816f6ae", "cost_psf": COST_PSF,
    "cutoff_2026": CUTOFF_2026,
    "input_sha256": HASHES,
    "counts": {"house": len(house_rows),
               "jump_parcels": len(jump_all),
               "city_sold": n_city_sold,
               "city_no_sale": n_city_nosale,
               "conf_jump": n_conf_jump,
               "review_jump": n_rev_jump,
               "unknown_jump": n_unk_jump,
               "old_jump": n_old_jump,
               "gate": dict(gate),
               "back": dict(back)}}
(DOCS / "manifest.json").write_text(
    json.dumps(manifest, indent=1, ensure_ascii=False))
print("waterfall:", dict(back))
print("gate:", dict(gate))
print("city: sold", n_city_sold, "nosale", n_city_nosale,
      "jump_parcels", len(jump_all))
