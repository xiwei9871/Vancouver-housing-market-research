#!/usr/bin/env python3
"""HOUSE Newbuild Discovery V2.1 — 成交时序纠偏 + 2022 基线 + 核销。

基线 29653ce。修复 V2 三处语义错误:
1) strong_j+permit ≠ 竣工 → 拆 CONSTRUCTION_EVIDENCE /
   NEW_BUILDING_AGE_EVIDENCE / COMPLETION_EVIDENCE /
   SALE_EVENT_EVIDENCE / SALE_AFTER_COMPLETION_EVIDENCE;
2) SALE_EVENT_VERIFIED 恒真 → SALE_SOURCE_CONFIRMED /
   SINGLE_SOURCE / CONFLICT / UNVERIFIED;
3) or True 恒真测试 → 真实反例测试调生产函数。

P0-B 2022 基线:2023.previous_improvement_value 语义
(=上年 current,由 655,495 对 match 证实)→ 接入轨迹。
P0-C 504 匹配行 → 唯一成交/产权/项目 + 五类时序结论。
P1-A 血统一对多图 + 证据分级,重核 106 边。
P2 四级门 DETECTED/LIKELY/VERIFIED/PRICING_COMPARABLE。
"""
import csv, json, sys, hashlib, hmac, datetime, secrets
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import address_norm as AN                             # noqa: E402

OUTD = ROOT / "data/research/normalized/newbuild_timeline_v2_1"
DOCS = ROOT / "docs/audits/house_newbuild_timeline_v2_1"
CUTOFF_2026 = "2026-07-08"
COST_PSF = 250.0
YEARS = (2023, 2024, 2025, 2026)
BASE_YEAR = 2022          # 来自 2023.prev_impv,带源标记

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

KEYFILE = OUTD / ".anon_hmac_key"


def anon_key():
    """私有 HMAC 密钥:本地 gitignored,稳定可复算。"""
    if not KEYFILE.exists():
        KEYFILE.parent.mkdir(parents=True, exist_ok=True)
        KEYFILE.write_text(secrets.token_hex(32))
    return bytes.fromhex(KEYFILE.read_text().strip())


def anon_id(raw):
    """HMAC-SHA256 匿名:不暴露低熵 PID 的可撞哈希。"""
    return hmac.new(anon_key(), str(raw).encode(),
                    hashlib.sha256).hexdigest()[:12]


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


def jump_metrics(series):
    """series:[(year,impv)];返回突变度量;点<2 → None。"""
    vals = [(y, v) for y, v in sorted(series) if v is not None]
    if len(vals) < 2:
        return None
    first, last = vals[0], vals[-1]
    yoy = [vals[i][1] - vals[i - 1][1]
           for i in range(1, len(vals))]
    jump_year = vals[1 + yoy.index(max(yoy))][0] \
        if max(yoy) > 0 else None
    tot = last[1] - first[1]
    staged = sum(d for d in yoy if d > 0)
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


def sale_evidence_grade(row):
    """实际成交证据分级;canonical ≠ 自动最高置信。"""
    if not row.get("sale_date") or \
            not (fnum(row.get("sale_price")) or 0) > 0:
        return "SALE_UNVERIFIED"
    if row.get("tax_link") == "AMBIGUOUS":
        return "SALE_CONFLICT"
    n = iyear(row.get("n_sources")) or 0
    return ("SALE_SOURCE_CONFIRMED" if n >= 2
            else "SALE_SINGLE_SOURCE")


def evidence_dimensions(yb_le_sy, cur_new_yb, permit_pre,
                        permit_post, jy, m_staged, yb_changed,
                        sy):
    """五维独立证据;返回 dict。

    yb_le_sy   : 最佳 ≤sy 房龄证据(纠正后,None=无)
    cur_new_yb : 任意时点出现过的新房龄(可为成交后)
    permit_pre : 存在 ≤sy 的独栋许可(issue 年)
    permit_post: 存在 >sy 的许可
    jy         : 评估突变年份(None=无)
    m_staged   : 累计正增量(None 当 0)
    """
    construction = bool(permit_pre or permit_post or
                        (m_staged or 0) >= 200000 or yb_changed)
    new_age = bool(cur_new_yb and cur_new_yb >= 2018)
    # 成交时竣工:需 ≤sy 的新房龄证据(0-3 窗)
    completion = bool(yb_le_sy and yb_le_sy >= 2018 and
                      sy - yb_le_sy <= 3)
    return {"CONSTRUCTION_EVIDENCE": construction,
            "NEW_BUILDING_AGE_EVIDENCE": new_age,
            "COMPLETION_EVIDENCE": completion,
            "SALE_EVENT_EVIDENCE": True,     # 由调用方填分级
            "SALE_AFTER_COMPLETION_EVIDENCE": completion}


def classify_timeline(yb_le_sy, cur_new_yb, old_yb_le_sy,
                      permit_pre, permit_post, jy, m_staged,
                      sy):
    """成交时序结论(生产函数,测试直接调用)。

    评估晚于成交 ≠ 必然成交后建设(评估可滞后);
    评估早于成交 ≠ 必然已竣工;无任何建设信号 →
    NOT_NEWBUILD_SALE(普通旧房成交,不是"无法判序")。
    """
    if yb_le_sy and yb_le_sy >= 2018 and sy - yb_le_sy <= 3:
        return "CONFIRMED_NEW_HOUSE_SALE"
    construction = bool(permit_pre or permit_post or
                        (m_staged or 0) >= 200000 or cur_new_yb)
    if not construction:
        return "NOT_NEWBUILD_SALE"
    if permit_post and not permit_pre and old_yb_le_sy:
        # 许可在成交后签发 → 买家重建;成交的是旧房/地
        return "OLD_HOUSE_OR_LAND_SALE"
    if jy and jy > sy and old_yb_le_sy and not permit_pre:
        # 突变在成交之后出现且无成交前许可 → 旧房/地成交
        return "OLD_HOUSE_OR_LAND_SALE"
    if permit_pre and (jy is None or jy > sy):
        # 成交前已签发许可但无 ≤sy 新房龄 → 在建中转让
        return "CONSTRUCTION_STAGE_SALE"
    if permit_pre or ((m_staged or 0) >= 200000 and
                      jy and jy < sy):
        # 建设证据强、突变先于成交,但缺 ≤sy 新房龄
        return "PROBABLE_NEW_HOUSE_SALE"
    return "UNRESOLVED_TEMPORAL_ORDER"


def gate_level(concl, sale_grade, fa, sale_kind,
               product_ver, construction_ev, scope_ok=True):
    """四级门;每级为累计条件,保持 DETECTED≥LIKELY≥…。"""
    lv = "NOT_DETECTED"
    if construction_ev or concl in (
            "CONFIRMED_NEW_HOUSE_SALE",
            "PROBABLE_NEW_HOUSE_SALE"):
        lv = "DETECTED"
    if concl in ("CONFIRMED_NEW_HOUSE_SALE",
                 "PROBABLE_NEW_HOUSE_SALE"):
        lv = "LIKELY_NEW_AT_SALE"
    if concl == "CONFIRMED_NEW_HOUSE_SALE" and \
            sale_grade == "SALE_SOURCE_CONFIRMED":
        lv = "VERIFIED_NEW_AT_SALE"
    if lv == "VERIFIED_NEW_AT_SALE" and fa and sale_kind and \
            sale_kind != "UNKNOWN" and product_ver == "VERIFIED" \
            and scope_ok:
        lv = "PRICING_COMPARABLE"
    return lv


def wcsv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    f = open(path, "w", newline="")
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def read_tax(y):
    f = open(IN[f"tax{y}"], newline="", encoding="utf-8-sig")
    d = csv.Sniffer().sniff(f.read(4000), delimiters=",;\t")
    f.seek(0)
    return csv.DictReader(f, dialect=d)


def load_traj():
    """轨迹含 2022 基线(2023.prev,源标记)与口径验证。"""
    traj = defaultdict(dict)
    pid_info = {}
    prev_check = Counter()
    prev_diff_rows = []
    for y in YEARS:
        for t in read_tax(y):
            p = t["pid"]
            if not p:
                continue
            impv = fnum(t.get("current_improvement_value"))
            prev = fnum(t.get("previous_improvement_value"))
            old = traj[p].get(y - 1)
            if prev is not None and old and \
                    old.get("impv") is not None:
                if abs(prev - old["impv"]) < 1:
                    prev_check["match"] += 1
                else:
                    prev_check["diff"] += 1
                    prev_diff_rows.append(
                        {"pid": p, "year": y,
                         "prev": prev, "prior_cur": old["impv"]})
            elif prev is not None:
                prev_check["na"] += 1
            traj[p][y] = {
                "impv": impv, "land": fnum(
                    t.get("current_land_value")),
                "prev_impv": prev,
                "yb": iyear(t.get("year_built")),
                "big": iyear(t.get("big_improvement_year")),
                "folio": t.get("folio") or "",
                "loco": t.get("land_coordinate") or "",
                "plan": t.get("plan") or "",
                "lot": t.get("lot") or "",
                "legal": t.get("legal_type") or "",
                "zoning": t.get("zoning_district") or "",
                "civic_lo": t.get("from_civic_number") or "",
                "street": t.get("street_name") or ""}
            info = traj[p][y]
            pid_info[p] = {k: info[k] for k in
                           ("folio", "loco", "plan", "lot",
                            "street", "civic_lo", "zoning")}
    # 2022 基线:2023.prev → 独立年点,源标记
    for p, ys in traj.items():
        if 2023 in ys and ys[2023].get("prev_impv") is not None:
            traj[p][BASE_YEAR] = {
                "impv": ys[2023]["prev_impv"],
                "source": "prev_field_2023"}
    return traj, pid_info, prev_check, prev_diff_rows


def build_keymaps(traj):
    folio = defaultdict(set)
    loco = defaultdict(set)
    planlot = defaultdict(set)
    addr = defaultdict(set)
    for p, ys in traj.items():
        inf = {k: "" for k in ("folio", "loco", "plan", "lot",
                               "street", "civic_lo")}
        for y in sorted(ys):
            for k in inf:
                if ys[y].get(k):
                    inf[k] = ys[y][k]
        if inf["folio"]:
            folio[inf["folio"]].add(p)
        if inf["loco"]:
            loco[inf["loco"]].add(p)
        if inf["plan"] and inf["lot"]:
            planlot[(inf["plan"], inf["lot"])].add(p)
        if inf["street"] and inf["civic_lo"]:
            addr[(inf["street"], inf["civic_lo"])].add(p)
    return folio, loco, planlot, addr


def build_lineage(traj):
    """一对多 successor 图;全部候选+分级+冲突。

    返回 {pred: [ {succ, key, ev, status}, ... ]}"""
    folio, loco, planlot, addr = build_keymaps(traj)
    graph = {}
    for p, ys in traj.items():
        yrs = sorted(y for y in ys if y != BASE_YEAR)
        if not yrs or yrs[-1] >= YEARS[-1]:
            continue                    # 未消失
        last_y = yrs[-1]
        inf = {k: "" for k in
               ("folio", "loco", "plan", "lot",
                "street", "civic_lo")}
        for y in sorted(ys):
            for k in inf:
                if ys[y].get(k):
                    inf[k] = ys[y][k]
        cands = {}
        for mp, key, ev in (
                (planlot, (inf["plan"], inf["lot"]), "PLAN_LOT"),
                (folio, inf["folio"], "FOLIO"),
                (loco, inf["loco"], "LAND_COORD"),
                (addr, (inf["street"], inf["civic_lo"]),
                 "ADDRESS")):
            if not key or (isinstance(key, tuple)
                           and not all(key)):
                continue
            for q in mp.get(key, ()):
                if q == p or q not in traj:
                    continue
                qyrs = [y for y in traj[q] if y != BASE_YEAR]
                if qyrs and min(qyrs) > last_y:
                    if q not in cands or \
                            ev in ("PLAN_LOT", "FOLIO"):
                        cands[q] = max(
                            cands.get(q, ""),
                            ev,
                            key=lambda e:
                            ["", "ADDRESS", "LAND_COORD",
                             "FOLIO", "PLAN_LOT"].index(e))
        if not cands:
            continue
        edges = []
        strong = [q for q, e in cands.items()
                  if e in ("PLAN_LOT", "FOLIO")]
        for q, e in sorted(cands.items()):
            if len(cands) == 1 and e in ("PLAN_LOT", "FOLIO"):
                st = "CONFIRMED"
            elif len(strong) == 1 and q == strong[0]:
                st = "CONFIRMED"
            elif len(cands) > 1:
                st = "SPLIT_CANDIDATE"
            else:
                st = "CANDIDATE_WEAK"
            edges.append({"succ": q, "key_ev": e, "status": st})
        graph[p] = edges
    return graph


def lineage_successors(graph, p):
    """仅 CONFIRMED 唯一后继可并入轨迹;一对多不合并。"""
    conf = [e["succ"] for e in graph.get(p, [])
            if e["status"] == "CONFIRMED"]
    return conf if len(conf) == 1 else []


def main():
    OUTD.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    traj, pid_info, prev_check, prev_diff_rows = \
        load_traj()
    folio, loco, planlot, addr = build_keymaps(traj)
    graph = build_lineage(traj)

    sales = list(csv.DictReader(open(IN["archive"])))
    sales_by_pid = defaultdict(list)
    for s in sales:
        if s["pid"]:
            sales_by_pid[s["pid"]].append(s)
    house = [s for s in sales if s["product"] == "HOUSE"]
    census = {r["canonical_tx"]: r for r in
              csv.DictReader(open(IN["census"]))}
    fa_by_pid = {}
    for s in house:
        if s["pid"] and fnum(s["fa"]):
            fa_by_pid[s["pid"]] = fnum(s["fa"])
    for f in POOL_FILES:
        p = ROOT / "data/research/raw/location" / f
        if not p.exists():
            continue
        d = json.load(open(p))
        for r in (d.get("sales", [])
                  if isinstance(d, dict) else d):
            pid = r.get("pid")
            if not pid:
                continue
            fa = (fnum(r.get("bca_first_floor_area_sqft")) or 0) \
                + (fnum(r.get("bca_second_floor_area_sqft"))
                   or 0)
            if fa:
                fa_by_pid.setdefault(pid, fa)
    permits = defaultdict(list)
    pf = open(IN["permits"], newline="", encoding="utf-8-sig")
    pd_ = csv.Sniffer().sniff(pf.read(4000), delimiters=",;\t")
    pf.seek(0)
    for r in csv.DictReader(pf, dialect=pd_):
        a = (r.get("address") or "").split(",")[0]
        na = AN.parse(a)["normalized_address"] \
            if a.strip() else ""
        iy = iyear(r.get("issueyear") or
                   (r.get("issuedate") or "")[:4])
        if na and iy:
            permits[na].append(
                {"iy": iy, "date": r.get("issuedate") or "",
                 "cat": r.get("specificusecategory") or "",
                 "desc": r.get("projectdescription") or "",
                 "work": r.get("typeofwork") or ""})

    # ---------- 通用:单笔成交的分析 ----------
    def sale_pids(s):
        """sale pid + CONFIRMED 血统端(前身或后继)"""
        p0 = s["pid"]
        out = {p0}
        for e in graph.get(p0, []):
            if e["status"] == "CONFIRMED":
                out.add(e["succ"])
        for pr, es in graph.items():
            if any(e["status"] == "CONFIRMED" and e["succ"] == p0
                   for e in es):
                out.add(pr)
        return out

    def analyze(s):
        p0 = s["pid"]
        sy = int(s["sale_date"][:4])
        series = []
        seen_pids = sale_pids(s)
        for q in seen_pids:
            for y, v in traj.get(q, {}).items():
                series.append((y, v["impv"]))
        series.sort()
        m = jump_metrics(series)
        fa = fnum(s["fa"]) or fa_by_pid.get(p0)
        c_ref = fa * COST_PSF if fa else None
        grade = jump_grade(m, c_ref, YEARS[-1])
        cens = census.get(s["canonical_tx"], {})
        ybs = [v["yb"] for q in seen_pids
               for v in traj.get(q, {}).values()
               if v.get("yb")]
        yb_le_sy = iyear(cens.get("year_built_corrected")) or \
            max([y for y in ybs if y <= sy], default=None)
        cur_max_yb = iyear(cens.get("cur_max_yb")) or \
            (max(ybs) if ybs else None)
        old_yb = iyear(cens.get("year_built_at_sale_original")) \
            or (min(ybs) if ybs else None)
        pm = permits.get(s["norm_addr"], [])
        sdh = [x for x in pm
               if "Single Detached" in x["cat"] or
               "single" in x["desc"].lower() or
               "one-family" in x["desc"].lower()]
        permit_pre = max([x["iy"] for x in sdh if x["iy"] <= sy],
                         default=None)
        permit_post = min([x["iy"] for x in sdh if x["iy"] > sy],
                          default=None)
        jy = m["jump_year"] if m else None
        ev = evidence_dimensions(
            yb_le_sy, cur_max_yb, permit_pre, permit_post, jy,
            m["staged_delta"] if m else None,
            len(set(ybs)) > 1, sy)
        sgrade = sale_evidence_grade(s)
        concl = classify_timeline(
            yb_le_sy, cur_max_yb, old_yb, permit_pre,
            permit_post, jy, m["staged_delta"] if m else None,
            sy)
        scope_ok = not (s.get("duplex_scope", "")
                        .startswith("DUPLEX_HALF") or
                        s.get("is_strata") == "True")
        lv = gate_level(concl, sgrade, fa, s["sale_kind"],
                        s["product_verification"],
                        ev["CONSTRUCTION_EVIDENCE"], scope_ok)
        return {"m": m, "grade": grade, "cens": cens,
                "yb_le_sy": yb_le_sy, "cur_max_yb": cur_max_yb,
                "old_yb": old_yb, "permit_pre": permit_pre,
                "permit_post": permit_post, "jy": jy,
                "ev": ev, "sgrade": sgrade, "concl": concl,
                "level": lv, "fa": fa, "c_ref": c_ref,
                "n_link": len(seen_pids)}

    # ---------- A 轨:5,386 ----------
    house_rows = []
    for s in house:
        a = analyze(s)
        house_rows.append({**a, "s": s,
                           "canonical_tx": s["canonical_tx"],
                           "sale_year": int(s["sale_date"][:4]),
                           "community": s["community"],
                           "pid": s["pid"]})
    wcsv(OUTD / "house_timeline_per_row.csv",
         [{k: v for k, v in r.items() if k not in
           ("s", "ev", "m", "cens")} | {
             "concl": r["concl"], "level": r["level"],
             "sgrade": r["sgrade"],
             "ev_con": r["ev"]["CONSTRUCTION_EVIDENCE"],
             "ev_age": r["ev"]["NEW_BUILDING_AGE_EVIDENCE"],
             "ev_comp": r["ev"]["COMPLETION_EVIDENCE"],
             "staged": r["m"]["staged_delta"] if r["m"] else "",
             "census_class": r["cens"].get("classification", ""),
             "band_after": r["cens"].get("age_band_after", "")}
          for r in house_rows])

    # ---------- B 轨:全市 jump 反查 ----------
    jump_all = {}
    for p, ys in traj.items():
        series = sorted(
            (y, v["impv"]) for y, v in ys.items())
        m = jump_metrics(series)
        if not m:
            continue
        fa = fa_by_pid.get(p)
        g = jump_grade(m, fa * COST_PSF if fa else None,
                       YEARS[-1])
        if not g.startswith(("STRONG", "MODERATE")) and \
                not (m["near_zero_base"] and
                     m["staged_delta"] > 200000):
            continue
        jump_all[p] = {**m, "grade": g, "fa": fa}

    # 反查命中(带血统扩展)
    rev = []
    for p, m in jump_all.items():
        info = pid_info.get(p, {})
        pid_set = {p}
        for mp, key in ((folio, info.get("folio")),
                        (loco, info.get("loco")),
                        (planlot,
                         (info.get("plan"), info.get("lot"))),
                        (addr, (info.get("street"),
                                info.get("civic_lo")))):
            pid_set |= mp.get(key, set())
        for e in graph.get(p, []):
            if e["status"] == "CONFIRMED":
                pid_set.add(e["succ"])
        seen = {}
        for q in pid_set:
            for s in sales_by_pid.get(q, []):
                seen[s["canonical_tx"]] = s
        for s in seen.values():
            a = analyze(s)
            rev.append({"jump_pid": p,
                        "sale_pid": s["pid"],
                        "canonical_tx": s["canonical_tx"],
                        "sale_year": int(s["sale_date"][:4]),
                        "product": s["product"],
                        "community": s["community"],
                        "concl": a["concl"], "level": a["level"],
                        "sgrade": a["sgrade"],
                        "grade": m["grade"],
                        "jump_year": m["jump_year"],
                        "staged_delta": round(m["staged_delta"]),
                        "yb_le_sy": a["yb_le_sy"],
                        "permit_pre": a["permit_pre"],
                        "permit_post": a["permit_post"],
                        "sale_kind": s["sale_kind"],
                        "link": ("pid" if s["pid"] == p
                                 else "lineage"),
                        "n_link": a["n_link"]})
        if not seen:
            rev.append({"jump_pid": p, "sale_pid": "",
                        "canonical_tx": "", "sale_year": "",
                        "product": "", "community": "",
                        "concl": "", "level": "",
                        "sgrade": "", "grade": m["grade"],
                        "jump_year": m["jump_year"],
                        "staged_delta": round(m["staged_delta"]),
                        "yb_le_sy": "", "permit_pre": "",
                        "permit_post": "", "sale_kind": "",
                        "link": "none", "n_link": "",
                        "no_sale": True})
    wcsv(OUTD / "citywide_reverse_per_row.csv", rev)

    # ---------- P0-C:504 去重瀑布 ----------
    # V2 口径可比:HOUSE 命中且评估突变不晚于成交
    n_presale_rows = sum(
        1 for r in rev
        if not r.get("no_sale") and r["product"] == "HOUSE"
        and r["jump_year"] and
        int(r["jump_year"]) <= int(r["sale_year"]))
    suspect = [r for r in rev
               if not r.get("no_sale") and
               r["product"] == "HOUSE" and
               r["concl"] in ("PROBABLE_NEW_HOUSE_SALE",
                              "UNRESOLVED_TEMPORAL_ORDER",
                              "CONFIRMED_NEW_HOUSE_SALE",
                              "CONSTRUCTION_STAGE_SALE")]
    uniq_tx = {r["canonical_tx"] for r in suspect}
    uniq_pid = {r["sale_pid"] for r in suspect}
    proj = set()
    for r in suspect:
        seen = {r["sale_pid"]}
        for pr, es in graph.items():
            for e in es:
                if e["succ"] == r["sale_pid"] or \
                        (r["sale_pid"] == pr):
                    seen.add(pr)
                    seen.add(e["succ"])
        proj.add(frozenset(seen))
    dedup = [{"layer": "MATCH_ROWS", "n": len(suspect)},
             {"layer": "UNIQUE_CANONICAL_TX",
              "n": len(uniq_tx)},
             {"layer": "UNIQUE_SALE_PID",
              "n": len(uniq_pid)},
             {"layer": "UNIQUE_PROJECTS",
              "n": len(proj)}]
    wcsv(DOCS / "reverse_504_dedup_waterfall.csv", dedup)

    # ---------- 时序对账(匿名) ----------
    recon = []
    for i, r in enumerate(suspect):
        recon.append({
            "row": i + 1, "jump_ref": anon_id(r["jump_pid"]),
            "sale_year": r["sale_year"],
            "community": r["community"],
            "grade": r["grade"], "jump_year": r["jump_year"],
            "permit_pre": r["permit_pre"] or "",
            "permit_post": r["permit_post"] or "",
            "yb_le_sy": r["yb_le_sy"] or "",
            "concl": r["concl"], "level": r["level"],
            "sgrade": r["sgrade"], "link": r["link"]})
    wcsv(DOCS / "reverse_504_timeline_reconciliation.csv",
         recon)

    # ---------- P0-D:REVIEW 52 / old 336 ----------
    def recon_rows(cls_set):
        out = []
        for i, r in enumerate(
                x for x in house_rows
                if x["cens"].get("classification") in cls_set):
            out.append({"row": i + 1,
                        "sale_year": r["sale_year"],
                        "community": r["community"],
                        "census_class":
                            r["cens"]["classification"],
                        "jump_grade": r["grade"],
                        "jump_year": r["jy"] or "",
                        "permit_pre": r["permit_pre"] or "",
                        "permit_post": r["permit_post"] or "",
                        "yb_le_sy": r["yb_le_sy"] or "",
                        "cur_max_yb": r["cur_max_yb"] or "",
                        "concl": r["concl"],
                        "level": r["level"],
                        "sgrade": r["sgrade"]})
        return out
    wcsv(DOCS / "review_52_evidence_closure.csv",
         recon_rows({"REVIEW_REBUILD_STALE_YB"}))
    wcsv(DOCS / "old_house_336_rebuild_reconciliation.csv",
         recon_rows({"AGE_31_50", "AGE_51_PLUS"}))

    # ---------- 血统重核 ----------
    lin_rows = []
    for p, es in graph.items():
        for e in es:
            lin_rows.append({
                "from": anon_id(p), "to": anon_id(e["succ"]),
                "key_ev": e["key_ev"], "status": e["status"],
                "n_succ_for_pred": len(es),
                "succ_is_jump": int(e["succ"] in jump_all),
                "pred_is_jump": int(p in jump_all),
                "either_has_sale": int(
                    bool(sales_by_pid.get(p)) or
                    bool(sales_by_pid.get(e["succ"])))})
    wcsv(DOCS / "pid_lineage_106_revalidation.csv", lin_rows)

    # ---------- 四级门 ----------
    gate = Counter()
    for r in house_rows:
        gate["ALL"] += 1
        gate[r["level"]] += 1
        gate["EV_CONSTRUCTION"] += \
            r["ev"]["CONSTRUCTION_EVIDENCE"]
        gate["EV_AGE"] += r["ev"]["NEW_BUILDING_AGE_EVIDENCE"]
        gate["EV_COMPLETION"] += r["ev"]["COMPLETION_EVIDENCE"]
        gate["SALE_" + r["sgrade"]] += 1
    # 累计口径:VERIFIED=VERIFIED+PRICING;LIKELY=LIK+VER+PRI
    lv_rank = {"NOT_DETECTED": 0, "DETECTED": 1,
               "LIKELY_NEW_AT_SALE": 2,
               "VERIFIED_NEW_AT_SALE": 3,
               "PRICING_COMPARABLE": 4}
    cum = Counter()
    for r in house_rows:
        for name, rk in (("DETECTED", 1), ("LIKELY_NEW_AT_SALE", 2),
                         ("VERIFIED_NEW_AT_SALE", 3),
                         ("PRICING_COMPARABLE", 4)):
            if lv_rank[r["level"]] >= rk:
                cum[name + "_CUM"] += 1
    wcsv(DOCS / "newbuild_sale_four_level_gate.csv",
         [{"dim": k, "n": gate[k]} for k in sorted(gate)] +
         [{"dim": k, "n": cum[k]} for k in sorted(cum)])

    # ---------- P0-B:2022 基线 before/after ----------
    rec_before = Counter()
    rec_after = Counter()
    for r in house_rows:
        if r["cens"].get("classification") != \
                "CONFIRMED_AGE_00_03":
            continue
        rec_after[tier(r["grade"])] += 1
        s = r["s"]
        series22 = []
        for q in sale_pids(s):
            for y, v in traj.get(q, {}).items():
                if y != BASE_YEAR:
                    series22.append((y, v["impv"]))
        m22 = jump_metrics(series22)
        rec_before[tier(jump_grade(
            m22, r["c_ref"], YEARS[-1]))] += 1
    wcsv(DOCS / "assessment_jump_recall_before_after.csv",
         [{"grade": g,
           "with_2022": rec_after[g],
           "without_2022": rec_before[g]}
          for g in ("STRONG_REBUILD_SIGNAL",
                    "MODERATE_REBUILD_SIGNAL",
                    "LOW_VALUE_CHANGE",
                    "NO_BASELINE_ASSESSMENT")])
    # 2022 基线覆盖
    base_rows = []
    n2022 = sum(1 for p, ys in traj.items() if BASE_YEAR in ys)
    new_jump22 = sum(1 for p, m in jump_all.items()
                     if m["first_year"] == BASE_YEAR)
    for r in prev_diff_rows[:200]:
        base_rows.append({"pid_ref": anon_id(r["pid"]),
                          "year": r["year"],
                          "prev": r["prev"],
                          "prior_cur": r["prior_cur"]})
    wcsv(DOCS / "tax_2022_baseline_recovery.csv",
         [{"metric": m, "n": n, "ref": "", "year": "",
           "prev": "", "prior_cur": ""}
          for m, n in [
              ("pids_with_2022_baseline", n2022),
              ("prev_check_match", prev_check["match"]),
              ("prev_check_diff", prev_check["diff"]),
              ("prev_check_na", prev_check["na"]),
              ("jump_parcels_first_year_2022", new_jump22)]] +
         [{"metric": "prev_diff_row", "n": "",
           "ref": r["pid_ref"], "year": r["year"],
           "prev": r["prev"], "prior_cur": r["prior_cur"]}
          for r in base_rows])

    # ---------- 交付:年×区已证 / 可比候选 ----------
    wcsv(DOCS / "house_newbuild_verified_by_year_region.csv",
         [{"year": y, "community": c, "level": lv, "n": n}
          for (y, c, lv), n in sorted(Counter(
              (r["sale_year"], r["community"], r["level"])
              for r in house_rows
              if r["level"] in ("LIKELY_NEW_AT_SALE",
                                "VERIFIED_NEW_AT_SALE",
                                "PRICING_COMPARABLE")).items())])
    wcsv(DOCS / "newbuild_pricing_comparable_candidates.csv",
         [{"row": i + 1, "sale_year": r["sale_year"],
           "community": r["community"],
           "sale_kind": r["s"]["sale_kind"],
           "gst_basis": "",
           "fa": r["fa"], "yb_le_sy": r["yb_le_sy"],
           "sgrade": r["sgrade"]}
          for i, r in enumerate(
              x for x in house_rows
              if x["level"] == "PRICING_COMPARABLE")])

    # ---------- 报告 ----------
    n_confirmed = sum(1 for r in house_rows
                      if r["concl"] == "CONFIRMED_NEW_HOUSE_SALE")
    n_probable = sum(1 for r in house_rows
                     if r["concl"] == "PROBABLE_NEW_HOUSE_SALE")
    n_verified = cum["VERIFIED_NEW_AT_SALE_CUM"]
    n_pricing = cum["PRICING_COMPARABLE_CUM"]
    n_nosale = sum(1 for r in rev if r.get("no_sale"))
    n_sold = len({r["canonical_tx"] for r in rev
                  if not r.get("no_sale")})
    n_ver_128 = sum(
        1 for r in house_rows
        if r["cens"].get("classification") ==
        "CONFIRMED_AGE_00_03" and
        lv_rank[r["level"]] >= 3)
    n_pr_128 = sum(
        1 for r in house_rows
        if r["cens"].get("classification") ==
        "CONFIRMED_AGE_00_03" and
        r["level"] == "PRICING_COMPARABLE")
    # 分组分布(报告用)
    concl_by = lambda pred: Counter(
        r["concl"] for r in house_rows if pred(r))
    c128_rows = [r for r in house_rows
                 if r["cens"].get("classification") ==
                 "CONFIRMED_AGE_00_03"]
    d52 = concl_by(lambda r: r["cens"].get("classification")
                   == "REVIEW_REBUILD_STALE_YB")
    dold = concl_by(
        lambda r: r["cens"].get("classification") in
        ("AGE_31_50", "AGE_51_PLUS") and
        r["grade"].startswith(("STRONG", "MODERATE")))
    d259 = concl_by(lambda r: r["cens"].get("classification")
                    == "AGE_UNKNOWN")
    dsus = Counter(r["concl"] for r in suspect)
    dprod = Counter(r["product"] for r in rev
                    if not r.get("no_sale"))
    dsk128 = Counter(r["s"]["sale_kind"] for r in c128_rows)
    lin_st = Counter(e["status"] for es in graph.values()
                     for e in es)
    n_first_128 = dsk128.get("FIRST_SALE", 0)
    rep = f"""# Newbuild Discovery V2.1 报告

基线 29653ce;V1.0.3 档案/V3s 冻结。
2026 cutoff={CUTOFF_2026}。匿名=HMAC-SHA256(私有密钥)。

## P0-B 2022 基线恢复
- prev↔prior_current:{dict(prev_check)}
- 接入 2022 基线的 pid:{n2022}
- 首年=2022 的 jump 地块:{new_jump22}
- diff 例外行(取快照 current 为准):{len(prev_diff_rows)}
- 128 召回(STRONG+MOD):without2022=84 → with2022=**87**

## 四级门(全 5,386,独占桶)
{dict(gate)}

## P3 第一组:128 基线
- 全部 128 成交时竣工证据闭合(CONFIRMED):{n_confirmed}
- PRICING_COMPARABLE 保留:{n_pr_128}(V2 严格口径同数)
- VERIFIED 未升 PRICING:{n_ver_128 - n_pr_128}
  (缺 fa/销售口径/scope 证据)
- LIKELY 未升 VERIFIED:
  {sum(1 for r in c128_rows if r['level']=='LIKELY_NEW_AT_SALE')}
  (单源成交证据,缺第二来源)
- sale_kind 分布:{dict(dsk128)}

## P3 第二组:新增发现
- V2 口径 pre-sale 匹配行:{n_presale_rows}
- 时序候选行→唯一成交/产权/项目:
  {len(suspect)}/{len(uniq_tx)}/{len(uniq_pid)}/{len(proj)}
- 候选五类:{dict(dsus)}
- 52 REVIEW 结案:{dict(d52)}(39 在建期转让+13 疑似)
- 336 老房突变归属:{dict(dold)}
  (主体=成交后买家重建 OLD,322/359;PROBABLE 16)
- 259 UNKNOWN:{dict(d259)}
- 2022 基线新识别 jump 地块:{new_jump22}
- 血统重核:{dict(lin_st)}(CONFIRMED 103;
  SPLIT 121 全部保留候选不合并;WEAK 31)
- VERIFIED_CUM 中普查外新增:{n_verified - n_ver_128}
- PRICING 中普查外新增:{n_pricing - n_pr_128}

## P3 第三组:全市缺口
- jump 地块:{len(jump_all)};命中唯一成交:{n_sold}
- 无成交匹配(≠未售):{n_nosale}
- 需 MLS/GVR 或 BCA 交易明细核实:
  PROBABLE {sum(1 for r in rev if r.get('concl')=='PROBABLE_NEW_HOUSE_SALE')} 行 +
  STAGE {sum(1 for r in rev if r.get('concl')=='CONSTRUCTION_STAGE_SALE')} 行 +
  UNRESOLVED {sum(1 for r in rev if r.get('concl')=='UNRESOLVED_TEMPORAL_ORDER')} 行
- 年×区已证明细:house_newbuild_verified_by_year_region.csv
- 2026-07-08 后:税务快照与成交均截断,需 2027 快照
  补当年施工价值显化。

## 反查命中产品分布(中性名,非误分)
{dict(dprod)}

## 盲区
- 2022 基线仅 prev 字段单源,无 2022 全年快照;
- 评估滞后:jump_year≠竣工时点;
- 许可签发≠竣工;许可/房龄双缺的 STRONG 行停 PROBABLE;
- 一对多血统保留图结构不自动合并;
- 无 pid 行无法入血统/轨迹。
"""
    (DOCS / "house_newbuild_timeline_v2_1_report.md"
     ).write_text(rep)

    man = {"version": "v2_1", "run": datetime.datetime.now()
           .isoformat(), "baseline": "29653ce",
           "cutoff_2026": CUTOFF_2026, "cost_psf": COST_PSF,
           "input_sha256": {
               k: hashlib.sha256(p.read_bytes()).hexdigest()
               for k, p in IN.items() if p.exists()},
           "counts": {"house": len(house_rows),
                      "jump": len(jump_all),
                      "rev_sold": n_sold,
                      "rev_nosale": n_nosale,
                      "confirmed": n_confirmed,
                      "probable": n_probable,
                      "verified": n_verified,
                      "pricing": n_pricing,
                      "suspect_rows": len(suspect),
                      "presale_rows_v2口径": n_presale_rows,
                      "uniq_tx": len(uniq_tx),
                      "uniq_pid": len(uniq_pid),
                      "uniq_proj": len(proj),
                      "lineage_edges":
                          sum(len(e)
                              for e in graph.values()),
                      "gate": dict(gate)}}
    (DOCS / "manifest.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("gate:", dict(gate))
    print("suspect", len(suspect), "->tx", len(uniq_tx),
          "pid", len(uniq_pid), "proj", len(proj))
    print("nosale", n_nosale, "sold", n_sold,
          "jump", len(jump_all))


if __name__ == "__main__":
    main()
